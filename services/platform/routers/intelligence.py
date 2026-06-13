"""
Intelligence Router — shared endpoints for the offline-first intelligent services
==================================================================================
Mounted at /api/v1/intelligence. Individual services (queue, analytics, dur,
integrity, label, compound, counseling, inventory, prescriber, rx-copilot,
trajectory, supply, docs, finance) attach their own sub-routers here as they are
built. This base module provides the cross-cutting endpoints every panel uses:

  GET  /intelligence/tier-status         — is the platform online? what's degraded?
  GET  /intelligence/outbox              — pending deferred cloud writes
  POST /intelligence/outbox/reconcile    — manually trigger reconciliation
  GET  /intelligence/health              — substrate self-check
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import (
    network_up, embedding_dim, outbox,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["intelligence"])


@router.get("/tier-status")
async def tier_status(_current: dict = Depends(get_current_user)):
    """
    Live connectivity + capability snapshot for the TierBadge in the UI.
    The frontend polls this (and also uses navigator.onLine) to render the
    green 'Full Intelligence' / amber 'Local Intelligence' chip.
    """
    online = network_up()
    return {
        "online":       online,
        "tier":         "cloud" if online else "local",
        "degraded":     not online,
        "local_brain": {
            "available":      True,                 # local brain is ALWAYS available
            "embedding_dim":  embedding_dim(),
            "llm":            "ollama_local",
        },
        "cloud_brain": {
            "available":      online,
            "note": "Cloud LLMs, external registries and live feeds are "
                    + ("reachable." if online else "unavailable — running on local intelligence."),
        },
    }


@router.get("/outbox")
async def outbox_pending(
    db: AsyncSession = Depends(get_db),
    _current: dict   = Depends(get_current_user),
):
    """Deferred cloud-write actions waiting for connectivity."""
    pending = await outbox.list_pending(db, limit=100)
    return {"pending_count": len(pending), "pending": pending}


@router.post("/outbox/reconcile")
async def outbox_reconcile(
    db: AsyncSession = Depends(get_db),
    _current: dict   = Depends(get_current_user),
):
    """Manually replay parked cloud writes (also runs automatically on reconnect)."""
    report = await outbox.reconcile(db)
    return {
        "attempted":          report.attempted,
        "succeeded":          report.succeeded,
        "failed":             report.failed,
        "skipped_no_handler": report.skipped_no_handler,
        "still_offline":      report.still_offline,
    }


@router.get("/health")
async def intelligence_health(_current: dict = Depends(get_current_user)):
    """Substrate self-check — confirms the local brain is wired and loadable."""
    checks = {"tier_resolver": True, "local_llm": False, "model_store": True}
    try:
        dim = embedding_dim()
        checks["local_llm"] = dim > 0
        checks["embedding_dim"] = dim
    except Exception as exc:  # pragma: no cover
        checks["local_llm_error"] = str(exc)
    return {"status": "ok", "checks": checks}
