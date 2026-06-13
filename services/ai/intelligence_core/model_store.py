"""
Local Model Store — PART 2 of the Offline-First Doctrine
=========================================================
Versioned, on-box persistence for every LOCAL-brain model.

Every intelligent service trains its classical ML models on the pharmacy's own
PostgreSQL data (nightly Celery beat) and serializes them HERE, so the box keeps
working with no internet and no model download.

Layout:
  ~/.pharmpilot/models/{service}/{version}.joblib
  ~/.pharmpilot/models/{service}/manifest.json   # points to the active version

manifest.json:
  {
    "service": "queue_ranking",
    "active_version": "queue_ranking_20260606_142233",
    "versions": {
       "queue_ranking_20260606_142233": {
          "created_at": "...", "row_count": 5123,
          "features": [...], "metrics": {"auc": 0.88}, "notes": "..."
       }, ...
    }
  }

Public surface:
  ModelStore(service).save(model, features, metrics, notes) -> version
  ModelStore(service).load()           -> (model, manifest_entry) | (None, None)
  ModelStore(service).load_version(v)  -> model | None
  ModelStore(service).has_model()      -> bool
  ModelStore(service).list_versions()  -> list[dict]
  ModelStore(service).prune(keep=5)    -> int
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MODELS_ROOT = Path(os.environ.get(
    "PHARMPILOT_MODEL_DIR",
    str(Path.home() / ".pharmpilot" / "models"),
))


class ModelStore:
    def __init__(self, service: str):
        self.service = service
        self.dir = MODELS_ROOT / service
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.dir / "manifest.json"

    # ── manifest ──────────────────────────────────────────────────────────────

    def _read_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {"service": self.service, "active_version": None, "versions": {}}
        try:
            return json.loads(self.manifest_path.read_text())
        except Exception as exc:  # pragma: no cover
            logger.warning("[model_store:%s] manifest unreadable (%s) → fresh", self.service, exc)
            return {"service": self.service, "active_version": None, "versions": {}}

    def _write_manifest(self, manifest: dict) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2))
        tmp.replace(self.manifest_path)   # atomic

    # ── save ──────────────────────────────────────────────────────────────────

    def save(
        self,
        model: Any,
        *,
        features: Optional[list[str]] = None,
        metrics: Optional[dict] = None,
        row_count: int = 0,
        notes: str = "",
        make_active: bool = True,
    ) -> str:
        """Serialize a model as a new version. Returns the version id."""
        import joblib

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        version = f"{self.service}_{ts}"
        path = self.dir / f"{version}.joblib"
        joblib.dump(model, path)

        manifest = self._read_manifest()
        manifest["versions"][version] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "row_count":  row_count,
            "features":   features or [],
            "metrics":    metrics or {},
            "notes":      notes,
            "path":       str(path),
        }
        if make_active or manifest.get("active_version") is None:
            manifest["active_version"] = version
        self._write_manifest(manifest)

        logger.info("[model_store:%s] saved %s (rows=%d, active=%s)",
                    self.service, version, row_count, make_active)
        return version

    # ── load ──────────────────────────────────────────────────────────────────

    def load(self) -> tuple[Optional[Any], Optional[dict]]:
        """Load the active model. Returns (model, manifest_entry) or (None, None)."""
        manifest = self._read_manifest()
        version = manifest.get("active_version")
        if not version:
            return None, None
        model = self.load_version(version)
        if model is None:
            return None, None
        return model, manifest["versions"].get(version)

    def load_version(self, version: str) -> Optional[Any]:
        import joblib
        path = self.dir / f"{version}.joblib"
        if not path.exists():
            logger.warning("[model_store:%s] version %s missing on disk", self.service, version)
            return None
        try:
            return joblib.load(path)
        except Exception as exc:  # pragma: no cover
            logger.error("[model_store:%s] load failed for %s (%s)", self.service, version, exc)
            return None

    def has_model(self) -> bool:
        """True if an active model exists and is loadable — drives cold-start logic."""
        manifest = self._read_manifest()
        version = manifest.get("active_version")
        if not version:
            return False
        return (self.dir / f"{version}.joblib").exists()

    def active_version(self) -> Optional[str]:
        return self._read_manifest().get("active_version")

    def active_metrics(self) -> dict:
        _, entry = self.load() if self.has_model() else (None, None)
        return (entry or {}).get("metrics", {})

    # ── management ──────────────────────────────────────────────────────────────

    def list_versions(self) -> list[dict]:
        manifest = self._read_manifest()
        out = []
        for v, meta in sorted(manifest["versions"].items(), reverse=True):
            out.append({"version": v, "active": v == manifest.get("active_version"), **meta})
        return out

    def set_active(self, version: str) -> bool:
        manifest = self._read_manifest()
        if version not in manifest["versions"]:
            return False
        manifest["active_version"] = version
        self._write_manifest(manifest)
        return True

    def prune(self, keep: int = 5) -> int:
        """Delete all but the most recent `keep` versions (active is always kept)."""
        manifest = self._read_manifest()
        versions = sorted(manifest["versions"].keys(), reverse=True)
        active = manifest.get("active_version")
        removed = 0
        for v in versions[keep:]:
            if v == active:
                continue
            meta = manifest["versions"].pop(v, None)
            if meta:
                try:
                    Path(meta["path"]).unlink(missing_ok=True)
                    removed += 1
                except Exception:  # pragma: no cover
                    pass
        self._write_manifest(manifest)
        return removed
