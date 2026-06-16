"""Owner/admin AI provider configuration.

Lets the pharmacy owner choose the AI service provider (e.g. Gemini) used by
every AI surface (Clinical Council, RAG, etc.) and set/rotate provider API keys.
Keys are write-only over the API — GET never returns a raw key, only masked
presence. Gated on `staff:write` (manager / super-admin / owner).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from services.ai.provider_registry.registry import get_ai_registry
from services.platform.auth import require_permission
from shared.models.auth import Staff

router = APIRouter()


class PreferredProviderRequest(BaseModel):
    provider: str | None  # provider_id, e.g. "google" (Gemini); None clears the preference


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str

    @field_validator("api_key")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("api_key must not be empty")
        return v.strip()


@router.get("/providers")
async def get_providers(staff: Staff = Depends(require_permission("staff:write"))):
    """Provider list + which is preferred/active + masked key presence."""
    return get_ai_registry().get_public_config()


@router.put("/preferred")
async def set_preferred(
    body: PreferredProviderRequest,
    staff: Staff = Depends(require_permission("staff:write")),
):
    """Set the global preferred AI provider (used first for every task)."""
    try:
        get_ai_registry().set_preferred_provider(body.provider)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return get_ai_registry().get_public_config()


@router.put("/key")
async def set_api_key(
    body: ApiKeyRequest,
    staff: Staff = Depends(require_permission("staff:write")),
):
    """Set/rotate a provider's API key (stored server-side; never echoed back)."""
    try:
        get_ai_registry().set_api_key(body.provider, body.api_key)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "provider": body.provider, **get_ai_registry().get_public_config()}
