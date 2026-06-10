"""
Intelligence Tier Resolver — PART 2 of the Offline-First Doctrine
==================================================================
The single decision point that every intelligent service uses to choose
between the LOCAL brain (always available) and the CLOUD brain (online only).

Core guarantees enforced here:
  • A service ALWAYS gets a usable answer — we fail *closed to local*, never to error.
  • Cloud calls are timeout-guarded (default 2.5 s); on timeout we silently fall back.
  • Connectivity is probed cheaply and cached (10 s) so we never block a request on it.

Public surface:
  Tier                       — enum: LOCAL | CLOUD | HYBRID
  network_up()               — cached reachability probe (non-blocking semantics)
  IntelligenceTier.resolve() — pick the tier for a given service
  cloud_call(fn, fallback)   — run a cloud coroutine with timeout → fallback on failure
  @dual_brain(service=...)   — wrap a service fn so it auto-emits the §1.2 envelope
  build_envelope(...)        — manual envelope builder
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# ─── Tier enum ────────────────────────────────────────────────────────────────

class Tier(str, Enum):
    LOCAL  = "local"     # works with the network cable unplugged
    CLOUD  = "cloud"     # full external intelligence
    HYBRID = "hybrid"    # local result enriched by a successful cloud call


# ─── Connectivity probe (cheap, cached) ───────────────────────────────────────

_PROBE_HOSTS = [
    ("1.1.1.1", 53),       # Cloudflare DNS
    ("8.8.8.8", 53),       # Google DNS
]
_PROBE_TTL_SECONDS = 10.0
_probe_cache: dict[str, Any] = {"up": None, "checked_at": 0.0}

# Hard override for tests / air-gapped deployments:
#   PHARMPILOT_FORCE_OFFLINE=1  → always LOCAL
_FORCE_OFFLINE_ENV = "PHARMPILOT_FORCE_OFFLINE"


def _raw_socket_probe(timeout: float = 0.8) -> bool:
    """One quick TCP connect attempt. Returns True if any probe host answers."""
    for host, port in _PROBE_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def network_up(force_refresh: bool = False) -> bool:
    """
    Cheap, cached connectivity check. Cached for _PROBE_TTL_SECONDS so repeated
    calls within a request burst do not each open a socket.
    """
    if os.environ.get(_FORCE_OFFLINE_ENV) == "1":
        return False

    now = time.monotonic()
    cached = _probe_cache["up"]
    if (not force_refresh
            and cached is not None
            and (now - _probe_cache["checked_at"]) < _PROBE_TTL_SECONDS):
        return cached

    up = _raw_socket_probe()
    _probe_cache["up"] = up
    _probe_cache["checked_at"] = now
    return up


# ─── Per-service cloud dependency health ──────────────────────────────────────
# A service may be "online" at the network level but its specific cloud
# dependency (an external registry, a price feed) may be down. Services register
# a lightweight health predicate; default is "healthy when network is up".

_DEPENDENCY_HEALTH: dict[str, Callable[[], bool]] = {}


def register_dependency_health(service: str, predicate: Callable[[], bool]) -> None:
    _DEPENDENCY_HEALTH[service] = predicate


def _dependency_healthy(service: str) -> bool:
    pred = _DEPENDENCY_HEALTH.get(service)
    if pred is None:
        return True
    try:
        return bool(pred())
    except Exception:
        return False


# ─── The resolver ─────────────────────────────────────────────────────────────

class IntelligenceTier:
    """Decide which brain a service should use for this request."""

    @staticmethod
    def resolve(service: str, force: Optional[Tier] = None) -> Tier:
        if force is not None:
            return force
        if not network_up():
            return Tier.LOCAL
        if _dependency_healthy(service):
            return Tier.CLOUD
        return Tier.LOCAL   # fail closed to local — never to error


# ─── Timeout-guarded cloud call ───────────────────────────────────────────────

DEFAULT_CLOUD_TIMEOUT = float(os.environ.get("PHARMPILOT_CLOUD_TIMEOUT", "2.5"))


async def cloud_call(
    fn: Callable[[], Awaitable[Any]],
    *,
    fallback: Any = None,
    timeout: float = DEFAULT_CLOUD_TIMEOUT,
    label: str = "cloud_call",
) -> tuple[Any, bool]:
    """
    Run an async cloud operation with a hard timeout.

    Returns (value, ok):
      ok=True  → cloud succeeded, value is its result
      ok=False → timed out or raised; value is `fallback`  (caller marks degraded)
    The user never waits longer than `timeout` on a dead socket.
    """
    try:
        value = await asyncio.wait_for(fn(), timeout=timeout)
        return value, True
    except asyncio.TimeoutError:
        logger.warning("[%s] cloud timed out after %.1fs → local fallback", label, timeout)
        return fallback, False
    except Exception as exc:  # noqa: BLE001 — any cloud failure degrades, never crashes
        logger.warning("[%s] cloud failed (%s) → local fallback", label, exc)
        return fallback, False


# ─── The universal response envelope (§1.2) ───────────────────────────────────

@dataclass
class IntelligenceEnvelope:
    result:          Any
    tier_used:       Tier
    confidence:      float
    degraded:        bool
    options_active:  list[str] = field(default_factory=list)
    options_offline: list[str] = field(default_factory=list)
    model_version:   str = ""
    generated_at:    str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "result":          self.result,
            "tier_used":       self.tier_used.value if isinstance(self.tier_used, Tier) else self.tier_used,
            "confidence":      round(float(self.confidence), 3),
            "degraded":        self.degraded,
            "options_active":  self.options_active,
            "options_offline": self.options_offline,
            "model_version":   self.model_version,
            "generated_at":    self.generated_at,
        }


def build_envelope(
    result: Any,
    *,
    tier_used: Tier,
    confidence: float,
    degraded: bool,
    options_active: Optional[list[str]] = None,
    options_offline: Optional[list[str]] = None,
    model_version: str = "",
) -> dict:
    """Manual envelope builder for services that don't use the decorator."""
    return IntelligenceEnvelope(
        result=result,
        tier_used=tier_used,
        confidence=confidence,
        degraded=degraded,
        options_active=options_active or [],
        options_offline=options_offline or [],
        model_version=model_version,
    ).to_dict()


# ─── @dual_brain decorator ────────────────────────────────────────────────────
#
# Wrap a service function that returns an IntelligenceEnvelope (or a plain dict
# it built via build_envelope). The decorator injects the resolved tier so the
# function can branch, and guarantees that *any* unhandled exception still yields
# a valid envelope (degraded local) rather than a 500.

def dual_brain(service: str):
    """
    Usage:
        @dual_brain(service="queue_ranking")
        async def rank(tier: Tier, ...):  # tier injected as first kwarg
            if tier is Tier.LOCAL: ...
            return build_envelope(...)
    """
    def decorator(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(*args, force_tier: Optional[Tier] = None, **kwargs):
            tier = IntelligenceTier.resolve(service, force=force_tier)
            try:
                return await fn(*args, tier=tier, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.error("[%s] service raised (%s) → emergency local envelope",
                             service, exc, exc_info=True)
                # Last-resort envelope so the UI never breaks.
                return build_envelope(
                    result={"error": "intelligence_unavailable", "detail": str(exc)},
                    tier_used=Tier.LOCAL,
                    confidence=0.0,
                    degraded=True,
                    options_active=[],
                    options_offline=["all"],
                    model_version=f"{service}_safe_fallback",
                )
        return wrapper
    return decorator
