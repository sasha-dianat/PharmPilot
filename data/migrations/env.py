import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.models.base import Base  # noqa: E402

# Import every model so Alembic autogenerate sees all tables
from shared.models.pharmacy import Pharmacy  # noqa
from shared.models.prescriber import Prescriber  # noqa
from shared.models.auth import Staff, StaffSession  # noqa
from shared.models.patient import Patient, PatientAllergy, LabResult, ClinicalNote  # noqa
from shared.models.insurance import PatientInsurance, InsurancePlan  # noqa
from shared.models.prescription import Prescription, PrescriptionFill, DURAlert, RxStateEvent  # noqa
from shared.models.claims import ClaimTransaction, ERA835Record, DIRFeeAdjustment  # noqa
from shared.models.inventory import DrugProduct, InventoryLot, StockLevel, PurchaseOrder, PurchaseOrderLine, ReceivingRecord  # noqa
from shared.models.biometric import BiometricIdentity, PharmacyVisit, SecurityEvent  # noqa
from shared.models.audio import AudioTranscript, ProfileEnrichmentAction  # noqa

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://pharmpilot:pharmpilot_dev@localhost:5432/pharmpilot",
    )


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
