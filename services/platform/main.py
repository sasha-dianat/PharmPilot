"""
PharmPilot AI — Main FastAPI Application
"""
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import make_asgi_app

from services.platform.config import settings
from services.platform.database import engine
from shared.models.base import Base

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PharmPilot starting up", environment=settings.ENVIRONMENT)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified")

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
        vault, phase32,
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
    app.include_router(vault.router,         prefix="/api/v1/vault",         tags=["evidence vault"])
    app.include_router(phase32.router,       prefix="/api/v1/pharmacy",      tags=["pharmacy journey"])

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "service": "pharmpilot-api"}

    return app


app = create_app()
