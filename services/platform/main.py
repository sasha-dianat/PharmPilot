"""
PharmPilot AI — Main FastAPI Application
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import make_asgi_app

from services.platform.config import settings
from services.platform.database import engine
from services.platform.migrations import run_migrations_on_boot

# Import ALL models here so SQLAlchemy's mapper registry resolves every
# relationship string reference before the first request is handled.
from shared.models.pharmacy import Pharmacy  # noqa: F401
from shared.models.prescriber import Prescriber  # noqa: F401
from shared.models.auth import Staff, StaffSession  # noqa: F401
from shared.models.patient import Patient, PatientAllergy, LabResult, ClinicalNote  # noqa: F401
from shared.models.clinical import Medication, GenotypeResult, ClinicalAlert, ClinicalAuditLog  # noqa: F401
from shared.models.insurance import PatientInsurance, InsurancePlan  # noqa: F401
from shared.models.prescription import Prescription, PrescriptionFill, DURAlert, RxStateEvent  # noqa: F401
from shared.models.claims import ClaimTransaction, ERA835Record, DIRFeeAdjustment  # noqa: F401
from shared.models.inventory import (  # noqa: F401
    DrugProduct, InventoryLot, StockLevel, PurchaseOrder, PurchaseOrderLine, ReceivingRecord
)
from shared.models.biometric import BiometricIdentity, PharmacyVisit, SecurityEvent  # noqa: F401
from shared.models.audio import AudioTranscript, ProfileEnrichmentAction  # noqa: F401

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PharmPilot starting up", environment=settings.ENVIRONMENT)

    if settings.RUN_MIGRATIONS_ON_BOOT:
        try:
            await asyncio.to_thread(run_migrations_on_boot)
        except Exception:
            logger.exception("Database migration failed during startup")
            raise
        logger.info("Database migrated to alembic head")

    yield

    logger.info("PharmPilot shutting down")
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="PharmPilot AI",
        description="Intelligent Pharmacy Operations Platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Prometheus metrics endpoint
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # Register routers
    from services.platform.routers import (
        auth, patients, prescriptions, adjudication,
        inventory, biometric, audio, clinical_brain, analytics, knowledge,
        vault, phase32, clinical_services, security_events, ai_hub, identity,
        drug_database, label_engine, package_verification,
        pos, dur_overrides, rx_documents, rx_transcription,
        intelligence, intel_finance, intel_inventory,
        intel_dur, intel_prescriber,
        intel_analytics, intel_docs, intel_label,
        intel_clinical, intel_workflow, cds, adr, polypharmacy, pgx,
    )

    app.include_router(auth.router,          prefix="/api/v1/auth",          tags=["auth"])
    app.include_router(patients.router,      prefix="/api/v1/patients",      tags=["patients"])
    app.include_router(prescriptions.router, prefix="/api/v1/prescriptions", tags=["prescriptions"])
    app.include_router(adjudication.router,  prefix="/api/v1/claims",        tags=["claims"])
    app.include_router(inventory.router,     prefix="/api/v1/inventory",     tags=["inventory"])
    app.include_router(biometric.router,     prefix="/api/v1/biometric",     tags=["biometric"])
    app.include_router(audio.router,         prefix="/api/v1/audio",         tags=["audio"])
    app.include_router(clinical_brain.router,prefix="/api/v1/clinical",      tags=["clinical"])
    app.include_router(knowledge.router,     prefix="/api/v1/knowledge",     tags=["knowledge"])
    app.include_router(analytics.router,     prefix="/api/v1/analytics",     tags=["analytics"])
    app.include_router(vault.router,            prefix="/api/v1/vault",            tags=["evidence vault"])
    app.include_router(clinical_services.router,prefix="/api/v1/clinical-services",tags=["clinical services"])
    app.include_router(phase32.router,         prefix="/api/v1/pharmacy",        tags=["pharmacy journey"])
    app.include_router(security_events.router, prefix="/api/v1/security",   tags=["security"])
    app.include_router(ai_hub.router,          prefix="/api/v1/ai",         tags=["AI hub"])
    app.include_router(identity.router,        prefix="/api/v1/identity",   tags=["identity"])
    app.include_router(drug_database.router,   prefix="/api/v1/drug-database",       tags=["drug database & pricing"])
    app.include_router(label_engine.router,    prefix="/api/v1/labels",              tags=["label engine"])           # Phase 23
    app.include_router(package_verification.router, prefix="/api/v1/package-verification", tags=["package verification"])  # Phase 24
    app.include_router(pos.router,            prefix="/api/v1/pos",                 tags=["pos"])                    # Phase 29
    app.include_router(dur_overrides.router,  prefix="/api/v1/dur",                 tags=["dur overrides"])          # Phase 31
    app.include_router(rx_documents.router,     prefix="/api/v1/rx-documents",        tags=["rx documents"])           # Phase 32
    app.include_router(rx_transcription.router,prefix="/api/v1/rx-transcription",    tags=["rx transcription"])       # Phase 32+
    app.include_router(intelligence.router,    prefix="/api/v1/intelligence",        tags=["intelligence"])           # Offline-first AI substrate
    app.include_router(intel_finance.router,   prefix="/api/v1/intelligence/finance",   tags=["intelligence: finance"])   # #19 Margin
    app.include_router(intel_inventory.router, prefix="/api/v1/intelligence/inventory", tags=["intelligence: inventory"]) # #12 Expiry + #17 Supply
    app.include_router(intel_dur.router,       prefix="/api/v1/intelligence/dur",        tags=["intelligence: dur"])       # #5 DUR patterns
    app.include_router(intel_prescriber.router,prefix="/api/v1/intelligence/prescriber", tags=["intelligence: prescriber"])# #13 Prescriber
    app.include_router(intel_analytics.router, prefix="/api/v1/intelligence/analytics",  tags=["intelligence: analytics"])# #3 Ask Your Data
    app.include_router(intel_docs.router,      prefix="/api/v1/intelligence/docs",        tags=["intelligence: docs"])     # #18 Clinical docs
    app.include_router(intel_label.router,     prefix="/api/v1/intelligence/label",       tags=["intelligence: label"])    # #8 Label simplify
    app.include_router(intel_clinical.router,  prefix="/api/v1/intelligence/clinical",    tags=["intelligence: clinical"]) # #11 Counseling + #6 Integrity + #10 Compounding
    app.include_router(intel_workflow.router,  prefix="/api/v1/intelligence/workflow",    tags=["intelligence: workflow"]) # #2 Queue + #16 Trajectory + #15 Copilot
    app.include_router(cds.router,             prefix="/api/v1/cds",                      tags=["clinical decision support"])
    app.include_router(adr.router,             prefix="/api/v1/adr",                      tags=["adr detective"])
    app.include_router(polypharmacy.router,    prefix="/api/v1/polypharmacy",             tags=["polypharmacy"])
    app.include_router(pgx.router,             prefix="/api/v1/pgx",                      tags=["pharmacogenomics"])

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "service": "pharmpilot-api"}

    return app


app = create_app()
