"""
Unified LLM + Embedding access — PART 2 of the Offline-First Doctrine
=====================================================================
One API that every intelligent service uses for text generation and embeddings.
Services NEVER branch on connectivity themselves — they call generate()/embed()
and this module routes to the right brain:

  • OFFLINE / max-privacy  → Ollama local model (via provider_registry)
  • ONLINE                 → cloud LLM (Claude/GPT) when PHI rules + tier allow

Embeddings are ALWAYS local (sentence-transformers / PubMedBERT on torch CPU) so
RAG, similarity and clustering keep working with the cable unplugged.

Public surface:
  generate(prompt, system, max_tokens, prefer_local, phi)  → GenevResult
  embed(text)                                              → list[float]
  embed_batch(texts)                                       → list[list[float]]
  embedding_dim()                                          → int
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .tier_resolver import Tier, network_up

logger = logging.getLogger(__name__)


# ─── Generation result ────────────────────────────────────────────────────────

@dataclass
class GenResult:
    text:      str
    tier:      Tier        # which brain actually produced this
    provider:  str
    degraded:  bool        # True if we wanted cloud but fell back to local
    model:     str = ""


# ─── Text generation ──────────────────────────────────────────────────────────

async def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    max_tokens: int = 512,
    temperature: float = 0.1,
    prefer_local: bool = False,
    phi: bool = False,
    task: str = "general",
    pharmacy_id: Optional[str] = None,
    staff_id: Optional[str] = None,
) -> GenResult:
    """
    Generate text via the best available brain.

    prefer_local=True  → force Ollama even when online (privacy / determinism).
    phi=True           → contains patient data; cloud path restricted to BAA providers
                         by the provider_registry. Offline always uses local Ollama
                         which never leaves the building.

    Always returns a GenResult — on total failure, text is "" and degraded=True.
    """
    want_cloud = network_up() and not prefer_local

    # Resolve the AITask + PHI sensitivity from the registry's enums lazily so this
    # module imports cleanly even if the registry is mid-refactor.
    try:
        from services.ai.provider_registry.registry import (
            get_ai_registry, AITask, PHISensitivity,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("[local_llm] provider_registry unavailable (%s) → raw ollama", exc)
        return await _raw_ollama(prompt, system, max_tokens, temperature)

    registry = get_ai_registry()

    # Map our coarse task string → registry AITask (fallback GENERAL).
    ai_task = _map_task(AITask, task, phi)
    phi_sens = PHISensitivity.HIGH if phi else PHISensitivity.NONE

    # OFFLINE or prefer_local → force the local provider.
    if not want_cloud:
        try:
            resp = await registry.call(
                task=ai_task, prompt=prompt, system_prompt=system,
                max_tokens=max_tokens, temperature=temperature,
                pharmacy_id=pharmacy_id, staff_id=staff_id,
                force_provider="ollama_local",
            )
            return GenResult(text=resp.content, tier=Tier.LOCAL,
                             provider=resp.provider, degraded=not prefer_local,
                             model=resp.model)
        except Exception as exc:  # pragma: no cover
            logger.warning("[local_llm] registry ollama failed (%s) → raw http", exc)
            return await _raw_ollama(prompt, system, max_tokens, temperature)

    # ONLINE → let the registry pick the best (PHI-safe) provider; it already
    # falls back down its chain on error. We add a final local safety net.
    try:
        resp = await registry.call(
            task=ai_task, prompt=prompt, system_prompt=system,
            max_tokens=max_tokens, temperature=temperature,
            phi_sensitivity=phi_sens, pharmacy_id=pharmacy_id, staff_id=staff_id,
        )
        used_local = resp.provider == "ollama_local"
        return GenResult(
            text=resp.content,
            tier=Tier.LOCAL if used_local else Tier.CLOUD,
            provider=resp.provider,
            degraded=used_local,   # registry fell all the way to local
            model=resp.model,
        )
    except Exception as exc:
        logger.warning("[local_llm] cloud chain failed (%s) → local fallback", exc)
        try:
            resp = await registry.call(
                task=ai_task, prompt=prompt, system_prompt=system,
                max_tokens=max_tokens, temperature=temperature,
                force_provider="ollama_local",
            )
            return GenResult(text=resp.content, tier=Tier.LOCAL,
                             provider="ollama_local", degraded=True, model=resp.model)
        except Exception:
            return await _raw_ollama(prompt, system, max_tokens, temperature)


def _map_task(AITask, task: str, phi: bool):
    """Best-effort map of a coarse task string to a registry AITask enum member."""
    table = {
        "clinical":     "CLINICAL_CONSULTATION",
        "ddi":          "DRUG_INTERACTION_CHECK",
        "dosing":       "DOSING_GUIDANCE",
        "ocr":          "PRESCRIPTION_OCR",
        "refill":       "REFILL_MESSAGE",
        "rag":          "RAG_SYNTHESIS",
        "summarize":    "SUMMARIZATION",
        "counseling":   "ADHERENCE_COUNSELING",
        "inventory":    "INVENTORY_NARRATIVE",
        "fraud":        "FRAUD_ANALYSIS",
        "local":        "PHI_SAFE_LOCAL",
        "general":      "GENERAL",
    }
    name = table.get(task, "GENERAL")
    return getattr(AITask, name, getattr(AITask, "GENERAL"))


async def _raw_ollama(
    prompt: str, system: Optional[str], max_tokens: int, temperature: float,
) -> GenResult:
    """Last-resort direct Ollama HTTP call (no registry). Returns empty text on failure."""
    try:
        import os
        import httpx
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        model    = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
        payload  = {
            "model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{base_url}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return GenResult(text=data.get("response", ""), tier=Tier.LOCAL,
                             provider="ollama_local", degraded=True, model=model)
    except Exception as exc:  # pragma: no cover
        logger.error("[local_llm] raw ollama failed (%s) — returning empty", exc)
        return GenResult(text="", tier=Tier.LOCAL, provider="none",
                         degraded=True, model="unavailable")


# ─── Embeddings (always local) ────────────────────────────────────────────────
# Reuse the PubMedBERT embedder already used by the knowledge engine so we share
# one loaded model in memory. Falls back to a hashing embedder if torch/model
# are missing, so similarity never hard-fails.

_embedder = None
_embed_dim = 768   # PubMedBERT default; corrected on first real load


def _get_embedder():
    global _embedder, _embed_dim
    if _embedder is not None:
        return _embedder
    try:
        from sentence_transformers import SentenceTransformer
        import os
        model_name = os.environ.get(
            "PHARMPILOT_EMBED_MODEL",
            "pritamdeka/S-PubMedBert-MS-MARCO",
        )
        _embedder = SentenceTransformer(model_name)
        try:
            _embed_dim = _embedder.get_sentence_embedding_dimension()
        except Exception:
            pass
        logger.info("[local_llm] embedder loaded: %s (dim=%d)", model_name, _embed_dim)
    except Exception as exc:  # pragma: no cover
        logger.warning("[local_llm] sentence-transformers unavailable (%s) → hash embedder", exc)
        _embedder = "hash"
        _embed_dim = 256
    return _embedder


def embedding_dim() -> int:
    _get_embedder()
    return _embed_dim


def embed(text: str) -> list[float]:
    """Embed a single string. Always local, never fails."""
    return embed_batch([text])[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Batch-embed. Uses PubMedBERT if available, else a deterministic hash embedder."""
    emb = _get_embedder()
    if emb == "hash":
        return [_hash_embed(t) for t in texts]
    try:
        vecs = emb.encode(texts, normalize_embeddings=True,
                          show_progress_bar=len(texts) > 200)
        return [v.tolist() for v in vecs]
    except Exception as exc:  # pragma: no cover
        logger.warning("[local_llm] encode failed (%s) → hash embedder", exc)
        return [_hash_embed(t) for t in texts]


def _hash_embed(text: str, dim: int = 256) -> list[float]:
    """
    Deterministic bag-of-hashed-tokens embedding. Crude but always available and
    good enough for coarse similarity when no neural model is present.
    """
    import hashlib
    import math
    vec = [0.0] * dim
    for tok in text.lower().split():
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for two equal-length vectors."""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
