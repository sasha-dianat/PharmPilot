"""
Intelligence Core — shared substrate for all offline-first intelligent services.

Build order (PART 2 of the Master Prompt):
  tier_resolver   — LOCAL/CLOUD/HYBRID resolution, timeout-guarded cloud calls, envelope
  local_llm       — unified generate()/embed() switching Ollama↔cloud transparently
  model_store     — versioned joblib persistence at ~/.pharmpilot/models/
  feature_store   — shared SQL→DataFrame feature builders (offline reads)
  reference_corpus— trainable references with Qdrant-online / SQLite-offline retrieval
  outbox          — deferred cloud writes reconciled on reconnect
"""
from .tier_resolver import (
    Tier,
    IntelligenceTier,
    IntelligenceEnvelope,
    network_up,
    cloud_call,
    dual_brain,
    build_envelope,
    register_dependency_health,
)
from .local_llm import (
    GenResult,
    generate,
    embed,
    embed_batch,
    embedding_dim,
    cosine,
)
from .model_store import ModelStore
from .reference_corpus import ReferenceCorpus, RetrievedRef
from . import feature_store
from . import outbox

__all__ = [
    "Tier", "IntelligenceTier", "IntelligenceEnvelope", "network_up",
    "cloud_call", "dual_brain", "build_envelope", "register_dependency_health",
    "GenResult", "generate", "embed", "embed_batch", "embedding_dim", "cosine",
    "ModelStore", "ReferenceCorpus", "RetrievedRef",
    "feature_store", "outbox",
]
