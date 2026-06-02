"""
AI Hub API — provider management, routing config, cost tracking, audit log.
Powers the Section 8: Multi-AI Intelligence Hub dashboard.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from services.ai.provider_registry.registry import (
    AIProviderRegistry, AITask, PHISensitivity,
    get_ai_registry, TASK_PHI_SENSITIVITY,
)
from shared.models.auth import Staff

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Provider status ───────────────────────────────────────────────────────

@router.get("/providers")
async def list_providers(staff: Staff = Depends(get_current_staff)):
    """Live status for all AI providers — feeds the provider grid dashboard."""
    registry = get_ai_registry()
    return {
        "providers": registry.get_provider_status(),
        "total_cost_today": round(sum(registry._cost_tracker.values()), 4),
    }


@router.post("/providers/{provider_id}/test")
async def test_provider(
    provider_id: str,
    staff: Staff = Depends(require_permission("clinical:read")),
):
    """Send a test ping to a provider. Returns latency and response preview."""
    registry = get_ai_registry()
    if provider_id not in registry.PROVIDERS:
        raise HTTPException(404, f"Provider '{provider_id}' not found")

    import time
    start = time.monotonic()
    try:
        response = await registry.call(
            task=AITask.GENERAL,
            prompt="In one sentence, confirm you are operational and state your model name.",
            max_tokens=50,
            force_provider=provider_id,
        )
        latency = (time.monotonic() - start) * 1000
        return {
            "provider": provider_id,
            "status": "ok",
            "latency_ms": round(latency, 0),
            "response_preview": response.content[:100],
            "model": response.model,
        }
    except Exception as exc:
        return {
            "provider": provider_id,
            "status": "error",
            "error": str(exc)[:200],
        }


# ── Routing configuration ─────────────────────────────────────────────────

class RoutingUpdate(BaseModel):
    task: str
    providers: list[str]   # Ordered: primary first, then fallbacks
    pharmacy_id: Optional[UUID] = None


@router.get("/routing")
async def get_routing_config(staff: Staff = Depends(get_current_staff)):
    """Get current task→provider routing table."""
    registry = get_ai_registry()
    routing = []
    for task in AITask:
        chain = registry.get_provider_chain(task, pharmacy_id=str(staff.pharmacy_id))
        phi = TASK_PHI_SENSITIVITY.get(task, PHISensitivity.MEDIUM)
        routing.append({
            "task": task.value,
            "phi_sensitivity": phi.value,
            "primary_provider": chain[0] if chain else None,
            "fallback_providers": chain[1:] if len(chain) > 1 else [],
            "full_chain": chain,
        })
    return {"routing": routing}


@router.put("/routing")
async def update_routing(
    body: RoutingUpdate,
    staff: Staff = Depends(require_permission("clinical:write")),
):
    """Update routing for a specific task (admin override)."""
    registry = get_ai_registry()
    try:
        task = AITask(body.task)
    except ValueError:
        raise HTTPException(400, f"Unknown task: {body.task}")

    # Validate providers exist
    unknown = [p for p in body.providers if p not in registry.PROVIDERS]
    if unknown:
        raise HTTPException(400, f"Unknown providers: {unknown}")

    pharmacy_id = str(body.pharmacy_id) if body.pharmacy_id else str(staff.pharmacy_id)
    registry.set_pharmacy_routing(pharmacy_id, task, body.providers)
    return {"status": "updated", "task": body.task, "providers": body.providers}


# ── Query / completion ────────────────────────────────────────────────────

class AIQueryRequest(BaseModel):
    prompt: str
    task: str = "general"
    system_prompt: Optional[str] = None
    max_tokens: int = 1024
    temperature: float = 0.1
    force_provider: Optional[str] = None
    force_local: bool = False


@router.post("/query")
async def ai_query(
    body: AIQueryRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
):
    """Direct AI query with task routing and cost tracking."""
    registry = get_ai_registry()
    try:
        task = AITask(body.task)
    except ValueError:
        raise HTTPException(400, f"Unknown task: {body.task}")

    try:
        response = await registry.call(
            task=task,
            prompt=body.prompt,
            system_prompt=body.system_prompt,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
            pharmacy_id=str(staff.pharmacy_id),
            staff_id=str(staff.id),
            force_provider=body.force_provider,
        )
        return {
            "content":       response.content,
            "provider":      response.provider,
            "model":         response.model,
            "latency_ms":    round(response.latency_ms, 0),
            "cost_usd":      round(response.cost_usd, 5),
            "tokens_input":  response.tokens_input,
            "tokens_output": response.tokens_output,
            "invocation_id": response.invocation_id,
            "from_fallback": response.from_fallback,
        }
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@router.post("/query/stream")
async def ai_query_stream(
    body: AIQueryRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
):
    """Streaming AI query — returns Server-Sent Events."""
    registry = get_ai_registry()
    try:
        task = AITask(body.task)
    except ValueError:
        raise HTTPException(400, f"Unknown task: {body.task}")

    import json

    async def event_generator():
        async for chunk in registry.stream(
            task=task,
            prompt=body.prompt,
            system_prompt=body.system_prompt,
            max_tokens=body.max_tokens,
            pharmacy_id=str(staff.pharmacy_id),
        ):
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache"})


# ── A/B Testing ───────────────────────────────────────────────────────────

class ABTestRequest(BaseModel):
    prompt: str
    provider_a: str
    provider_b: str
    task: str = "general"
    system_prompt: Optional[str] = None


@router.post("/ab-test")
async def ab_test_providers(
    body: ABTestRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
):
    """Run the same prompt against two providers simultaneously."""
    registry = get_ai_registry()
    task = AITask(body.task) if body.task in [t.value for t in AITask] else AITask.GENERAL

    async def call_provider(provider: str):
        return await registry.call(
            task=task, prompt=body.prompt,
            system_prompt=body.system_prompt, max_tokens=512,
            force_provider=provider,
            pharmacy_id=str(staff.pharmacy_id),
        )

    import asyncio
    results = await asyncio.gather(
        call_provider(body.provider_a),
        call_provider(body.provider_b),
        return_exceptions=True,
    )

    def fmt(r):
        if isinstance(r, Exception):
            return {"error": str(r)}
        return {
            "content":    r.content,
            "provider":   r.provider,
            "model":      r.model,
            "latency_ms": round(r.latency_ms, 0),
            "cost_usd":   round(r.cost_usd, 5),
        }

    return {"provider_a": fmt(results[0]), "provider_b": fmt(results[1])}


@router.post("/ab-test/{invocation_id}/rate")
async def rate_ab_result(
    invocation_id: str,
    rating: int,
    staff: Staff = Depends(get_current_staff),
):
    """Record pharmacist preference rating for an A/B test result."""
    registry = get_ai_registry()
    for record in registry._invocation_log:
        if record.invocation_id == invocation_id:
            record.pharmacist_rating = max(1, min(5, rating))
            return {"status": "rated", "rating": record.pharmacist_rating}
    raise HTTPException(404, "Invocation not found")


# ── Cost & audit ──────────────────────────────────────────────────────────

@router.get("/costs")
async def get_cost_breakdown(staff: Staff = Depends(get_current_staff)):
    """Daily cost breakdown by provider and task."""
    return get_ai_registry().get_daily_cost_breakdown()


@router.get("/audit-log")
async def get_audit_log(
    limit: int = 100,
    staff: Staff = Depends(require_permission("reports:read")),
):
    """Full AI invocation audit log — exportable for compliance."""
    return {"records": get_ai_registry().get_audit_log(limit)}
