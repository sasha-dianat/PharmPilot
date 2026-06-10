"""
Package Verification Engine — Phase 24
========================================
Verifies that the medication item being dispensed visually matches the
expected product for the prescription.

Call flow at dispensing:
  1. Camera captures the item's packaging.
  2. `VerificationEngine.verify_for_rx(frame, expected_ndc)` is called.
  3. Engine extracts embedding from the frame.
  4. Compares against the reference embedding for `expected_ndc`.
  5. Returns VerificationResult with match/fail + confidence score.

Additionally supports `identify_unknown(frame)` — finds the best-match
product across ALL enrolled NDCs (useful for misplaced stock identification).

Similarity metric: cosine similarity on L2-normalised vectors.
  1.0 = identical   0.0 = orthogonal   -1.0 = opposite
  Accept threshold (configurable, default 0.80):
    ≥ 0.80 → PASS    0.60–0.79 → WARN (borderline)    < 0.60 → FAIL
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

from .enrollment_engine import EnrolledProduct, PackageEnrollmentEngine
from .feature_extractor import PackageFeatureExtractor

logger = logging.getLogger(__name__)

ACCEPT_THRESHOLD  = 0.80    # ≥ this → PASS
WARN_THRESHOLD    = 0.60    # ≥ this (and < ACCEPT) → WARN
# Below WARN_THRESHOLD → FAIL


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class MatchCandidate:
    ndc11:       str
    drug_name:   str
    similarity:  float
    is_accepted: bool


@dataclass
class VerificationResult:
    expected_ndc:    str
    expected_name:   str
    status:          str         # "pass" | "warn" | "fail" | "unenrolled" | "error"
    similarity:      float       # 0.0 – 1.0
    confidence_pct:  float       # similarity × 100, capped at 100
    message:         str
    verified_at:     str         # ISO timestamp
    latency_ms:      float
    top_candidates:  list[MatchCandidate] = field(default_factory=list)

    @property
    def is_pass(self) -> bool:
        return self.status == "pass"

    @property
    def needs_alert(self) -> bool:
        return self.status in ("fail", "error")


# ---------------------------------------------------------------------------
# PackageVerificationEngine
# ---------------------------------------------------------------------------

class PackageVerificationEngine:
    """
    Verifies medication packaging appearance at the point of dispensing.

    Usage::

        engine = PackageVerificationEngine()

        # Verify a specific Rx item:
        result = engine.verify_for_rx(frame_bgr, expected_ndc="00093314905")

        # Identify unknown item:
        result = engine.identify_unknown(frame_bgr)
    """

    def __init__(
        self,
        enrollment_engine: Optional[PackageEnrollmentEngine] = None,
        accept_threshold:  float = ACCEPT_THRESHOLD,
        warn_threshold:    float = WARN_THRESHOLD,
    ) -> None:
        self._enrollment = enrollment_engine or PackageEnrollmentEngine()
        self._extractor  = self._enrollment.extractor
        self.accept_threshold = accept_threshold
        self.warn_threshold   = warn_threshold

        # In-memory embedding matrix cache (rebuilt when invalidated)
        self._ndc_list:   list[str] = []
        self._emb_matrix: np.ndarray = np.empty((0, 0), dtype=np.float32)
        self._cache_dirty = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_for_rx(
        self,
        frame_bgr:     np.ndarray,
        expected_ndc:  str,
        top_k:         int = 3,
    ) -> VerificationResult:
        """
        Verify that `frame_bgr` shows the product for `expected_ndc`.

        Returns PASS if the similarity to the enrolled reference ≥ accept_threshold.
        Returns UNENROLLED if the NDC has no enrolled reference.
        """
        t0 = time.monotonic()
        now = datetime.now(timezone.utc).isoformat()

        ndc_clean = expected_ndc.replace("-", "").replace(" ", "")[:11]
        product   = self._enrollment.get_product(ndc_clean)

        if product is None:
            return VerificationResult(
                expected_ndc    = ndc_clean,
                expected_name   = "",
                status          = "unenrolled",
                similarity      = 0.0,
                confidence_pct  = 0.0,
                message         = (
                    f"NDC {ndc_clean} has no visual reference enrolled. "
                    "Enroll this product at receiving to enable visual verification."
                ),
                verified_at     = now,
                latency_ms      = (time.monotonic() - t0) * 1000,
            )

        ref_emb = self._enrollment.load_embedding(ndc_clean)
        if ref_emb is None:
            return VerificationResult(
                expected_ndc    = ndc_clean,
                expected_name   = product.drug_name,
                status          = "error",
                similarity      = 0.0,
                confidence_pct  = 0.0,
                message         = "Reference embedding file missing — re-enroll this product.",
                verified_at     = now,
                latency_ms      = (time.monotonic() - t0) * 1000,
            )

        try:
            preprocessed = self._extractor.preprocess(frame_bgr, crop_center=True, clahe=True)
            live_emb     = self._extractor.extract(preprocessed)
        except Exception as e:
            return VerificationResult(
                expected_ndc    = ndc_clean,
                expected_name   = product.drug_name,
                status          = "error",
                similarity      = 0.0,
                confidence_pct  = 0.0,
                message         = f"Feature extraction failed: {e}",
                verified_at     = now,
                latency_ms      = (time.monotonic() - t0) * 1000,
            )

        similarity = float(np.dot(live_emb, ref_emb))   # cosine (both L2-normed)
        similarity = max(-1.0, min(1.0, similarity))

        status, message = self._classify(similarity, product.drug_name, ndc_clean)

        # Top-K candidates from the full index (for diagnostics)
        candidates = self._top_k_candidates(live_emb, top_k)

        latency = (time.monotonic() - t0) * 1000
        logger.info(
            "Verification: NDC=%s  similarity=%.3f  status=%s  latency=%.1fms",
            ndc_clean, similarity, status, latency,
        )

        return VerificationResult(
            expected_ndc    = ndc_clean,
            expected_name   = product.drug_name,
            status          = status,
            similarity      = round(similarity, 4),
            confidence_pct  = round(similarity * 100, 1),
            message         = message,
            verified_at     = now,
            latency_ms      = round(latency, 1),
            top_candidates  = candidates,
        )

    def identify_unknown(
        self,
        frame_bgr: np.ndarray,
        top_k:     int = 5,
    ) -> VerificationResult:
        """
        Identify an unknown package by finding its closest match across
        all enrolled products. Useful for misplaced or unlabelled stock.
        """
        t0 = time.monotonic()
        now = datetime.now(timezone.utc).isoformat()

        self._maybe_refresh_cache()
        if len(self._ndc_list) == 0:
            return VerificationResult(
                expected_ndc   = "",
                expected_name  = "",
                status         = "unenrolled",
                similarity     = 0.0,
                confidence_pct = 0.0,
                message        = "No products are enrolled. Cannot identify.",
                verified_at    = now,
                latency_ms     = 0.0,
            )

        try:
            preprocessed = self._extractor.preprocess(frame_bgr, crop_center=True, clahe=True)
            live_emb     = self._extractor.extract(preprocessed)
        except Exception as e:
            return VerificationResult(
                expected_ndc   = "",
                expected_name  = "",
                status         = "error",
                similarity     = 0.0,
                confidence_pct = 0.0,
                message        = f"Feature extraction failed: {e}",
                verified_at    = now,
                latency_ms     = (time.monotonic() - t0) * 1000,
            )

        candidates = self._top_k_candidates(live_emb, top_k)
        best       = candidates[0] if candidates else None

        if best is None:
            status, message = "error", "Identification failed — no candidates."
        else:
            status, message = self._classify(best.similarity, best.drug_name, best.ndc11)

        return VerificationResult(
            expected_ndc    = best.ndc11 if best else "",
            expected_name   = best.drug_name if best else "",
            status          = status,
            similarity      = round(best.similarity if best else 0.0, 4),
            confidence_pct  = round((best.similarity if best else 0.0) * 100, 1),
            message         = message,
            verified_at     = now,
            latency_ms      = round((time.monotonic() - t0) * 1000, 1),
            top_candidates  = candidates,
        )

    # ------------------------------------------------------------------
    # Invalidate the in-memory cache when enrollment changes
    # ------------------------------------------------------------------

    def invalidate_cache(self) -> None:
        self._cache_dirty = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify(self, similarity: float, drug_name: str, ndc: str) -> tuple[str, str]:
        if similarity >= self.accept_threshold:
            return "pass", f"✅ Visual match confirmed for {drug_name} ({ndc}) — similarity {similarity:.0%}"
        elif similarity >= self.warn_threshold:
            return "warn", (
                f"⚠️ Borderline match for {drug_name} ({ndc}) — similarity {similarity:.0%}. "
                "Different manufacturer brand? Pharmacist should visually confirm."
            )
        else:
            return "fail", (
                f"❌ Visual mismatch — item does not match {drug_name} ({ndc}). "
                f"Similarity only {similarity:.0%} (threshold {self.accept_threshold:.0%}). "
                "Verify the correct product was pulled."
            )

    def _top_k_candidates(self, live_emb: np.ndarray, k: int) -> list[MatchCandidate]:
        self._maybe_refresh_cache()
        if self._emb_matrix.shape[0] == 0:
            return []

        scores = self._emb_matrix @ live_emb          # (N,) cosine similarities
        top_idx = np.argsort(scores)[::-1][:k]

        products_by_ndc = {p.ndc11: p for p in self._enrollment.list_products()}
        candidates = []
        for i in top_idx:
            ndc  = self._ndc_list[i]
            prod = products_by_ndc.get(ndc)
            candidates.append(MatchCandidate(
                ndc11       = ndc,
                drug_name   = prod.drug_name if prod else ndc,
                similarity  = round(float(scores[i]), 4),
                is_accepted = float(scores[i]) >= self.accept_threshold,
            ))
        return candidates

    def _maybe_refresh_cache(self) -> None:
        if self._cache_dirty:
            self._ndc_list, self._emb_matrix = self._enrollment.load_all_embeddings()
            self._cache_dirty = False
            logger.debug(
                "Embedding cache refreshed: %d products, dim=%d",
                len(self._ndc_list),
                self._emb_matrix.shape[1] if self._emb_matrix.ndim == 2 and self._emb_matrix.shape[0] > 0 else 0,
            )
