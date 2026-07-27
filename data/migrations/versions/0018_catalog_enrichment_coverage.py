"""catalog enrichment columns + coverage sources/runs (+ seeded insurer sources)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drug_catalog", sa.Column("country", sa.String(80), nullable=True))
    op.add_column("drug_catalog", sa.Column("license_owner", sa.String(200), nullable=True))
    op.add_column("drug_catalog", sa.Column("brand_owner", sa.String(200), nullable=True))
    op.add_column("drug_catalog", sa.Column("license_valid_until", sa.String(20), nullable=True))
    op.add_column("drug_catalog", sa.Column("monograph", JSONB, nullable=True))
    op.create_index("ix_drug_catalog_country", "drug_catalog", ["country"])

    op.create_table(
        "coverage_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("insurer", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("settings", JSONB, nullable=True),
        sa.Column("check_interval_days", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_coverage_sources_insurer", "coverage_sources", ["insurer"])

    op.create_table(
        "coverage_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("coverage_sources.id"), nullable=False),
        sa.Column("insurer", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", JSONB, nullable=True),
        sa.Column("diff", JSONB, nullable=True),
        sa.Column("staged", JSONB, nullable=True),
        sa.Column("review", JSONB, nullable=True),
        sa.Column("unmatched", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("applied_by", UUID(as_uuid=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_coverage_runs_source_id", "coverage_runs", ["source_id"])

    # Seed one source per major insurer. URLs are best-known ROOTS, unverifiable
    # without the Iran proxy — the admin probes and corrects them in the GUI.
    op.execute(sa.text("""
        INSERT INTO coverage_sources (id, insurer, name, url, strategy, settings,
                                      check_interval_days, enabled, created_at, updated_at)
        VALUES
        (uuid_generate_v4(), 'tamin', 'دارونامه تأمین اجتماعی',
         'https://darman.tamin.ir', 'auto', '{}', 7, true, now(), now()),
        (uuid_generate_v4(), 'salamat', 'دارونامه بیمه سلامت',
         'https://ihio.gov.ir', 'auto', '{}', 7, true, now(), now()),
        (uuid_generate_v4(), 'armed_forces', 'دارونامه نیروهای مسلح',
         'https://esata.ir', 'auto', '{}', 7, true, now(), now())
    """))


def downgrade() -> None:
    op.drop_index("ix_coverage_runs_source_id", table_name="coverage_runs")
    op.drop_table("coverage_runs")
    op.drop_index("ix_coverage_sources_insurer", table_name="coverage_sources")
    op.drop_table("coverage_sources")
    op.drop_index("ix_drug_catalog_country", table_name="drug_catalog")
    for col in ("monograph", "license_valid_until", "brand_owner", "license_owner", "country"):
        op.drop_column("drug_catalog", col)
