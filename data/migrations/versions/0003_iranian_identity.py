"""iranian_identity — national code, Jalali dual-store, untyped person links

Adds the Iranian-default identity layer:
  - patients: national_id (کد ملی, primary identifier), Jalali DOB, father name,
    identity_system selector, archived-customer enrichment fields.
  - customer_identities: archived demographics extracted before a patient exists,
    plus loyalty counters for returning-customer recognition.
  - person_links: untyped many-to-many links between persons (customer/patient),
    so a recognised customer fans out to ALL related profiles as candidates.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-03
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── patients: Iranian identity fields (dual-store dates) ──────────────────
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS national_id VARCHAR(10)")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS date_of_birth_jalali VARCHAR(10)")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS father_name VARCHAR(100)")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS identity_system VARCHAR(10) NOT NULL DEFAULT 'iranian'")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS auto_created BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS identity_provenance JSONB")
    # National code unique per pharmacy (partial — only when present)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_pharmacy_national_id "
        "ON patients (pharmacy_id, national_id) WHERE national_id IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_patients_national_id ON patients (national_id)")

    # ── customer_identities: archived demographics + loyalty counters ─────────
    op.execute("ALTER TABLE customer_identities ADD COLUMN IF NOT EXISTS national_id VARCHAR(10)")
    op.execute("ALTER TABLE customer_identities ADD COLUMN IF NOT EXISTS display_name VARCHAR(200)")
    op.execute("ALTER TABLE customer_identities ADD COLUMN IF NOT EXISTS display_name_fa VARCHAR(200)")
    op.execute("ALTER TABLE customer_identities ADD COLUMN IF NOT EXISTS extracted_demographics JSONB")
    op.execute("ALTER TABLE customer_identities ADD COLUMN IF NOT EXISTS visit_count INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE customer_identities ADD COLUMN IF NOT EXISTS loyalty_points INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE customer_identities ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT true")
    op.execute("CREATE INDEX IF NOT EXISTS ix_customer_identities_national_id ON customer_identities (national_id)")

    # ── person_links: untyped link graph between any two persons ──────────────
    # person_ref = 'patient:<uuid>' or 'customer:<uuid>' — lets us link a
    # recognised customer to multiple patient profiles (and customers to
    # customers) WITHOUT needing to know the relationship semantics.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS person_links (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            pharmacy_id     UUID NOT NULL REFERENCES pharmacies(id),
            person_a_ref    VARCHAR(60) NOT NULL,
            person_b_ref    VARCHAR(60) NOT NULL,
            relationship    VARCHAR(50),
            confidence      NUMERIC(5,4) NOT NULL DEFAULT 1.0,
            source          VARCHAR(40) NOT NULL DEFAULT 'insurance',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_person_link UNIQUE (pharmacy_id, person_a_ref, person_b_ref)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_person_links_a ON person_links (person_a_ref)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_person_links_b ON person_links (person_b_ref)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_person_links_pharmacy ON person_links (pharmacy_id)")

    # ── prescription_fills: Jalali display column for purchase history ────────
    op.execute("ALTER TABLE prescription_fills ADD COLUMN IF NOT EXISTS fill_date_jalali VARCHAR(10)")

    # ── pharmacy: default identity system selector (iranian | american) ───────
    op.execute("ALTER TABLE pharmacies ADD COLUMN IF NOT EXISTS identity_system VARCHAR(10) NOT NULL DEFAULT 'iranian'")
    op.execute("ALTER TABLE pharmacies ADD COLUMN IF NOT EXISTS default_locale VARCHAR(10) NOT NULL DEFAULT 'fa-IR'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS person_links")
    for col in ("national_id", "date_of_birth_jalali", "father_name",
                "identity_system", "auto_created", "identity_provenance"):
        op.execute(f"ALTER TABLE patients DROP COLUMN IF EXISTS {col}")
    for col in ("national_id", "display_name", "display_name_fa",
                "extracted_demographics", "visit_count", "loyalty_points", "is_archived"):
        op.execute(f"ALTER TABLE customer_identities DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE prescription_fills DROP COLUMN IF EXISTS fill_date_jalali")
    op.execute("ALTER TABLE pharmacies DROP COLUMN IF EXISTS identity_system")
    op.execute("ALTER TABLE pharmacies DROP COLUMN IF EXISTS default_locale")
