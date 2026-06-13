"""
Package Enrollment Engine — Phase 24
======================================
Enrolls medication packaging appearances during the receiving (stock-in) process.

For each product (identified by NDC-11), the pharmacist/technician takes
5–10 photos from different angles:
  - Front of box / blister
  - Back of box / blister
  - Side panel (often has barcode)
  - Sachet / individual unit
  - Close-up of text/colour (brand differentiation)

The engine extracts embeddings from all photos, computes the mean reference
embedding, and stores it in `~/.pharmpilot/package_index/`.

The index uses:
  - SQLite WAL-mode   → product metadata (NDC, name, brand, enrolled_by, timestamp)
  - NumPy .npy files  → per-product embedding vectors

FAISS is used for fast approximate nearest-neighbour search when the enrolled
catalogue grows large (>50 products). Falls back to brute-force cosine
similarity for smaller catalogues.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

from .feature_extractor import PackageFeatureExtractor

logger = logging.getLogger(__name__)

INDEX_DIR = Path(os.environ.get(
    "PHARMPILOT_PACKAGE_INDEX_DIR",
    str(Path.home() / ".pharmpilot" / "package_index"),
))
DB_PATH   = INDEX_DIR / "enrollment.db"
EMB_DIR   = INDEX_DIR / "embeddings"

FAISS_THRESHOLD = 50   # switch to FAISS when > this many products enrolled


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EnrolledProduct:
    ndc11:         str
    drug_name:     str
    manufacturer:  str
    dosage_form:   str           # sachet | blister | bottle | box | vial | other
    photo_count:   int
    enrolled_by:   str           # staff_id
    enrolled_at:   str           # ISO datetime
    last_updated:  Optional[str]
    notes:         Optional[str]
    embedding_path: str          # relative path under EMB_DIR

    @property
    def short_id(self) -> str:
        return self.ndc11.replace("-", "")[:11]


# ---------------------------------------------------------------------------
# PackageEnrollmentEngine
# ---------------------------------------------------------------------------

class PackageEnrollmentEngine:
    """
    Manages the enrollment catalogue of medication package appearances.

    Thread-safe: uses a threading.Lock around all DB writes.
    """

    def __init__(self) -> None:
        self._extractor = PackageFeatureExtractor()
        self._lock      = threading.Lock()
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        EMB_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # DB setup
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS enrolled_products (
                    ndc11          TEXT PRIMARY KEY,
                    drug_name      TEXT NOT NULL,
                    manufacturer   TEXT DEFAULT '',
                    dosage_form    TEXT DEFAULT 'other',
                    photo_count    INTEGER DEFAULT 0,
                    enrolled_by    TEXT NOT NULL,
                    enrolled_at    TEXT NOT NULL,
                    last_updated   TEXT,
                    notes          TEXT,
                    embedding_path TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS enrollment_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ndc11        TEXT NOT NULL,
                    session_at   TEXT NOT NULL,
                    photo_count  INTEGER NOT NULL,
                    staff_id     TEXT NOT NULL,
                    FOREIGN KEY (ndc11) REFERENCES enrolled_products(ndc11)
                );
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # Enroll a product from a list of raw BGR images
    # ------------------------------------------------------------------

    def enroll(
        self,
        ndc11:        str,
        drug_name:    str,
        images_bgr:   list[np.ndarray],
        enrolled_by:  str,
        manufacturer: str = "",
        dosage_form:  str = "other",
        notes:        Optional[str] = None,
        replace:      bool = False,
    ) -> EnrolledProduct:
        """
        Enroll or re-enroll a product.

        Args:
            ndc11:       11-digit NDC (dashes allowed — stripped internally)
            drug_name:   Drug name + strength e.g. "Amoxicillin 500 mg Capsules"
            images_bgr:  List of BGR images from different angles (min 3, recommended 5–10)
            enrolled_by: Staff ID performing the enrollment
            manufacturer: Manufacturer / brand name
            dosage_form: One of sachet | blister | bottle | box | vial | other
            notes:       Free-text notes (e.g., "Red-and-white blister, Arabic text")
            replace:     If True, overwrite existing enrollment

        Returns:
            EnrolledProduct instance
        """
        if len(images_bgr) < 1:
            raise ValueError("At least 1 photo is required for enrollment")

        ndc11_clean = ndc11.replace("-", "").replace(" ", "")[:11]

        existing = self.get_product(ndc11_clean)
        if existing and not replace:
            raise ValueError(
                f"NDC {ndc11_clean} is already enrolled. "
                "Pass replace=True to update the reference embedding."
            )

        # Pre-process and extract mean embedding
        preprocessed = [
            self._extractor.preprocess(img, crop_center=True, clahe=True)
            for img in images_bgr
        ]
        mean_embedding = self._extractor.extract_mean(preprocessed)

        # Persist embedding to disk
        emb_filename = f"{ndc11_clean}.npy"
        emb_path     = EMB_DIR / emb_filename
        np.save(str(emb_path), mean_embedding)

        now = datetime.now(timezone.utc).isoformat()

        product = EnrolledProduct(
            ndc11          = ndc11_clean,
            drug_name      = drug_name,
            manufacturer   = manufacturer,
            dosage_form    = dosage_form,
            photo_count    = len(images_bgr),
            enrolled_by    = enrolled_by,
            enrolled_at    = now if not existing else (existing.enrolled_at),
            last_updated   = now if existing else None,
            notes          = notes,
            embedding_path = emb_filename,
        )

        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO enrolled_products
                    (ndc11, drug_name, manufacturer, dosage_form,
                     photo_count, enrolled_by, enrolled_at, last_updated,
                     notes, embedding_path)
                VALUES
                    (:ndc11, :drug_name, :manufacturer, :dosage_form,
                     :photo_count, :enrolled_by, :enrolled_at, :last_updated,
                     :notes, :embedding_path)
                ON CONFLICT(ndc11) DO UPDATE SET
                    drug_name      = excluded.drug_name,
                    manufacturer   = excluded.manufacturer,
                    dosage_form    = excluded.dosage_form,
                    photo_count    = excluded.photo_count,
                    last_updated   = excluded.last_updated,
                    notes          = excluded.notes,
                    embedding_path = excluded.embedding_path
                """,
                {
                    "ndc11":          product.ndc11,
                    "drug_name":      product.drug_name,
                    "manufacturer":   product.manufacturer,
                    "dosage_form":    product.dosage_form,
                    "photo_count":    product.photo_count,
                    "enrolled_by":    product.enrolled_by,
                    "enrolled_at":    product.enrolled_at,
                    "last_updated":   product.last_updated,
                    "notes":          product.notes,
                    "embedding_path": product.embedding_path,
                },
            )
            conn.execute(
                "INSERT INTO enrollment_sessions (ndc11, session_at, photo_count, staff_id) "
                "VALUES (?, ?, ?, ?)",
                (ndc11_clean, now, len(images_bgr), enrolled_by),
            )
            conn.commit()

        logger.info(
            "Enrolled %s (%s) with %d photos by %s",
            drug_name, ndc11_clean, len(images_bgr), enrolled_by,
        )
        return product

    # ------------------------------------------------------------------
    # Retrieve enrolled products
    # ------------------------------------------------------------------

    def get_product(self, ndc11: str) -> Optional[EnrolledProduct]:
        ndc11_clean = ndc11.replace("-", "").replace(" ", "")[:11]
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM enrolled_products WHERE ndc11 = ?", (ndc11_clean,)
            ).fetchone()
        return _row_to_product(row) if row else None

    def list_products(self) -> list[EnrolledProduct]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM enrolled_products ORDER BY drug_name"
            ).fetchall()
        return [_row_to_product(r) for r in rows]

    def delete_product(self, ndc11: str, staff_id: str) -> bool:
        ndc11_clean = ndc11.replace("-", "").replace(" ", "")[:11]
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM enrolled_products WHERE ndc11 = ?", (ndc11_clean,)
            )
            conn.commit()
        # Remove embedding file
        emb_path = EMB_DIR / f"{ndc11_clean}.npy"
        if emb_path.exists():
            emb_path.unlink()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("Enrollment for %s deleted by %s", ndc11_clean, staff_id)
        return deleted

    # ------------------------------------------------------------------
    # Load embedding for a specific NDC
    # ------------------------------------------------------------------

    def load_embedding(self, ndc11: str) -> Optional[np.ndarray]:
        ndc11_clean = ndc11.replace("-", "").replace(" ", "")[:11]
        emb_path = EMB_DIR / f"{ndc11_clean}.npy"
        if not emb_path.exists():
            return None
        return np.load(str(emb_path)).astype(np.float32)

    # ------------------------------------------------------------------
    # Load ALL embeddings into memory (for brute-force search)
    # ------------------------------------------------------------------

    def load_all_embeddings(self) -> tuple[list[str], np.ndarray]:
        """
        Returns:
            ndc11_list: list of NDC11 strings in order
            matrix:     (N, dim) float32 array of reference embeddings
        """
        products = self.list_products()
        if not products:
            return [], np.empty((0, self._extractor.dim), dtype=np.float32)

        vecs = []
        ndcs = []
        for p in products:
            emb = self.load_embedding(p.ndc11)
            if emb is not None:
                ndcs.append(p.ndc11)
                vecs.append(emb)

        if not vecs:
            return [], np.empty((0, self._extractor.dim), dtype=np.float32)

        return ndcs, np.stack(vecs, axis=0).astype(np.float32)

    @property
    def extractor(self) -> PackageFeatureExtractor:
        return self._extractor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_product(row: sqlite3.Row) -> EnrolledProduct:
    return EnrolledProduct(
        ndc11          = row["ndc11"],
        drug_name      = row["drug_name"],
        manufacturer   = row["manufacturer"] or "",
        dosage_form    = row["dosage_form"] or "other",
        photo_count    = row["photo_count"] or 0,
        enrolled_by    = row["enrolled_by"],
        enrolled_at    = row["enrolled_at"],
        last_updated   = row["last_updated"],
        notes          = row["notes"],
        embedding_path = row["embedding_path"],
    )
