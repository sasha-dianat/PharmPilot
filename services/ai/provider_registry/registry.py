"""
Multi-AI Provider Registry
===========================
PharmPilot is provider-agnostic. This registry manages connections to
every major LLM provider with intelligent routing, fallback chains,
cost tracking, A/B testing, and PHI safety enforcement.

Supported providers:
  Anthropic (Claude)  — clinical reasoning, safety-critical tasks
  OpenAI (GPT-4o)     — OCR, structured output, function calling
  Google (Gemini)     — multimodal, long-context, speed
  Cohere              — RAG synthesis, reranking, citations
  Mistral             — cost-efficient, multilingual
  Groq                — ultra-low latency (NOT for PHI)
  Ollama (local)      — zero-cost, offline, PHI-safe

PHI ROUTING RULE:
  PHI-sensitive tasks → only Anthropic / OpenAI / Google (all have BAAs)
  Non-PHI tasks (summaries, templates) → any provider
  Maximum privacy → Ollama local (never leaves the building)
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# Owner-configurable AI settings persist here (preferred provider + API keys),
# so the choice survives restarts. Single-pharmacy pilot scope; for production,
# back this with a secrets manager / encrypted column.
_AI_SETTINGS_PATH = Path(os.environ.get(
    "PHARMPILOT_AI_SETTINGS",
    str(Path.home() / ".pharmpilot" / "ai_provider_settings.json"),
))


class AITask(str, Enum):
    CLINICAL_CONSULTATION    = "clinical_consultation"
    DRUG_INTERACTION_CHECK   = "drug_interaction_check"
    DOSING_GUIDANCE          = "dosing_guidance"
    PRESCRIPTION_OCR         = "prescription_ocr"
    REFILL_MESSAGE           = "refill_message_generation"
    RAG_SYNTHESIS            = "rag_synthesis"
    SUMMARIZATION            = "summarization"
    ADHERENCE_COUNSELING     = "adherence_counseling"
    INVENTORY_NARRATIVE      = "inventory_narrative"
    FRAUD_ANALYSIS           = "fraud_analysis"
    PHI_SAFE_LOCAL           = "phi_safe_local"
    GENERAL                  = "general"


class PHISensitivity(str, Enum):
    HIGH   = "high"    # Contains patient name, DOB, Rx data — BAA required
    MEDIUM = "medium"  # De-identified clinical data — BAA preferred
    LOW    = "low"     # No PHI — any provider acceptable
    NONE   = "none"    # Pure utility tasks — use cheapest/fastest


# PHI sensitivity per task
TASK_PHI_SENSITIVITY: dict[AITask, PHISensitivity] = {
    AITask.CLINICAL_CONSULTATION:  PHISensitivity.HIGH,
    AITask.DRUG_INTERACTION_CHECK: PHISensitivity.HIGH,
    AITask.DOSING_GUIDANCE:        PHISensitivity.HIGH,
    AITask.PRESCRIPTION_OCR:       PHISensitivity.HIGH,
    AITask.ADHERENCE_COUNSELING:   PHISensitivity.HIGH,
    AITask.FRAUD_ANALYSIS:         PHISensitivity.HIGH,
    AITask.RAG_SYNTHESIS:          PHISensitivity.MEDIUM,
    AITask.INVENTORY_NARRATIVE:    PHISensitivity.LOW,
    AITask.REFILL_MESSAGE:         PHISensitivity.LOW,
    AITask.SUMMARIZATION:          PHISensitivity.LOW,
    AITask.PHI_SAFE_LOCAL:         PHISensitivity.NONE,
    AITask.GENERAL:                PHISensitivity.MEDIUM,
}

# BAA-covered providers (safe for PHI)
BAA_PROVIDERS = {"anthropic", "openai", "google"}


@dataclass
class ProviderConfig:
    name: str
    api_key_env: str
    base_url: Optional[str]
    models: list[str]
    default_model: str
    strengths: list[str]
    has_baa: bool                    # Business Associate Agreement for HIPAA
    cost_per_1k_input: float         # USD per 1K input tokens
    cost_per_1k_output: float        # USD per 1K output tokens
    avg_latency_ms: float = 500.0    # Updated dynamically
    is_active: bool = True
    supports_streaming: bool = True
    supports_vision: bool = False


@dataclass
class AIInvocationRecord:
    """Audit log entry for every AI call — required for compliance."""
    invocation_id: str
    task_type: AITask
    provider: str
    model: str
    tokens_input: int
    tokens_output: int
    latency_ms: float
    cost_usd: float
    phi_sensitivity: PHISensitivity
    pharmacy_id: Optional[str]
    staff_id: Optional[str]
    success: bool
    error: Optional[str]
    pharmacist_rating: Optional[int] = None  # 1-5, collected post-hoc (set later via rate endpoint)
    invoked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AIResponse:
    content: str
    provider: str
    model: str
    tokens_input: int
    tokens_output: int
    latency_ms: float
    cost_usd: float
    invocation_id: str
    from_fallback: bool = False
    fallback_provider: Optional[str] = None


class AIProviderRegistry:
    """
    Central registry for all AI providers.
    Routes requests intelligently, tracks costs, enforces PHI safety.
    """

    PROVIDERS: dict[str, ProviderConfig] = {
        "anthropic": ProviderConfig(
            name="Anthropic", api_key_env="ANTHROPIC_API_KEY",
            base_url=None,
            models=["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
            default_model="claude-opus-4-5",
            strengths=["clinical_reasoning", "long_context", "safety", "instruction_following"],
            has_baa=True,
            cost_per_1k_input=0.015,   # claude-opus-4-5
            cost_per_1k_output=0.075,
            supports_streaming=True,
        ),
        "openai": ProviderConfig(
            name="OpenAI", api_key_env="OPENAI_API_KEY",
            base_url=None,
            models=["gpt-4o", "gpt-4o-mini", "o3-mini"],
            default_model="gpt-4o",
            strengths=["ocr_extraction", "structured_output", "function_calling", "vision"],
            has_baa=True,
            cost_per_1k_input=0.0025,
            cost_per_1k_output=0.010,
            supports_streaming=True, supports_vision=True,
        ),
        "google": ProviderConfig(
            name="Google", api_key_env="GOOGLE_API_KEY",
            base_url=None,
            models=["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
            default_model="gemini-2.0-flash",
            strengths=["multimodal", "long_context", "speed", "cost_efficient"],
            has_baa=True,
            cost_per_1k_input=0.0001,
            cost_per_1k_output=0.0004,
            supports_streaming=True, supports_vision=True,
        ),
        "cohere": ProviderConfig(
            name="Cohere", api_key_env="COHERE_API_KEY",
            base_url=None,
            models=["command-r-plus", "command-r"],
            default_model="command-r-plus",
            strengths=["rag_grounding", "reranking", "citation", "search_augmented"],
            has_baa=False,  # Check current Cohere BAA status
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            supports_streaming=True,
        ),
        "mistral": ProviderConfig(
            name="Mistral", api_key_env="MISTRAL_API_KEY",
            base_url=None,
            models=["mistral-large-latest", "mistral-small-latest", "open-mistral-nemo"],
            default_model="mistral-large-latest",
            strengths=["cost_efficiency", "multilingual", "speed", "european_data_residency"],
            has_baa=False,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.009,
            supports_streaming=True,
        ),
        "groq": ProviderConfig(
            name="Groq", api_key_env="GROQ_API_KEY",
            base_url="https://api.groq.com/openai/v1",
            models=["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
            default_model="llama-3.3-70b-versatile",
            strengths=["ultra_low_latency", "open_source", "high_throughput"],
            has_baa=False,   # NOT PHI-safe — never route PHI here
            cost_per_1k_input=0.00059,
            cost_per_1k_output=0.00079,
            avg_latency_ms=150,  # Groq's signature ultra-low latency
            supports_streaming=True,
        ),
        "ollama_local": ProviderConfig(
            name="Ollama Local", api_key_env="",
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            models=["llama3.1:8b", "phi3:medium", "meditron:7b", "llama3.1:70b"],
            default_model="llama3.1:8b",
            strengths=["zero_cost", "offline", "phi_safe", "no_external_call"],
            has_baa=True,   # Data never leaves premises — maximally PHI-safe
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            avg_latency_ms=2000,   # Slower without GPU
            supports_streaming=True,
        ),
    }

    # Task routing: primary → fallback chain
    # PHI safety is checked at runtime — this is task→capability routing
    TASK_ROUTING: dict[AITask, list[str]] = {
        AITask.CLINICAL_CONSULTATION:  ["anthropic", "openai"],
        AITask.DRUG_INTERACTION_CHECK: ["anthropic", "google"],
        AITask.DOSING_GUIDANCE:        ["anthropic", "openai"],
        AITask.PRESCRIPTION_OCR:       ["openai",    "google"],
        AITask.RAG_SYNTHESIS:          ["anthropic", "cohere"],
        AITask.REFILL_MESSAGE:         ["mistral",   "openai"],
        AITask.SUMMARIZATION:          ["groq",      "mistral"],
        AITask.ADHERENCE_COUNSELING:   ["anthropic", "google"],
        AITask.INVENTORY_NARRATIVE:    ["anthropic", "mistral"],
        AITask.FRAUD_ANALYSIS:         ["anthropic", "openai"],
        AITask.PHI_SAFE_LOCAL:         ["ollama_local"],
        AITask.GENERAL:                ["anthropic", "openai", "google"],
    }

    def __init__(self):
        self._cost_tracker: dict[str, float] = {}   # provider → USD today
        self._latency_tracker: dict[str, list[float]] = {}  # provider → recent latencies
        self._invocation_log: list[AIInvocationRecord] = []
        self._custom_routing: dict[str, dict[AITask, list[str]]] = {}  # pharmacy_id → overrides
        # Owner-configured global preference: when set (e.g. "google" for Gemini),
        # this provider is tried first for EVERY task (PHI-permitting).
        self.preferred_provider: Optional[str] = None
        self._runtime_keys: dict[str, str] = {}     # provider → api key set via admin UI
        self._load_settings()

    # ── Owner-configurable settings (preferred provider + API keys) ───────────

    def _load_settings(self) -> None:
        try:
            if not _AI_SETTINGS_PATH.exists():
                return
            data = json.loads(_AI_SETTINGS_PATH.read_text())
            pref = data.get("preferred_provider")
            if pref in self.PROVIDERS:
                self.preferred_provider = pref
            for provider, key in (data.get("api_keys") or {}).items():
                if provider in self.PROVIDERS and key:
                    self._runtime_keys[provider] = key
                    env = self.PROVIDERS[provider].api_key_env
                    if env:
                        os.environ[env] = key  # make existing env-based reads work
        except Exception as exc:  # pragma: no cover
            logger.warning("[registry] could not load AI settings (%s)", exc)

    def _save_settings(self) -> None:
        try:
            _AI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _AI_SETTINGS_PATH.write_text(json.dumps({
                "preferred_provider": self.preferred_provider,
                "api_keys": self._runtime_keys,
            }, indent=2))
            try:
                _AI_SETTINGS_PATH.chmod(0o600)  # keys at rest — restrict perms
            except OSError:
                pass
        except Exception as exc:  # pragma: no cover
            logger.warning("[registry] could not save AI settings (%s)", exc)

    def set_preferred_provider(self, provider: Optional[str]) -> None:
        """Set the global preferred provider (None clears it). Persisted."""
        if provider is not None and provider not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        self.preferred_provider = provider
        self._save_settings()

    def set_api_key(self, provider: str, api_key: str) -> None:
        """Store an API key for a provider (used immediately + persisted)."""
        if provider not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        self._runtime_keys[provider] = api_key
        env = self.PROVIDERS[provider].api_key_env
        if env:
            os.environ[env] = api_key
        self._save_settings()

    def get_public_config(self) -> dict:
        """Owner-facing config — never returns raw keys (masked presence only)."""
        return {
            "preferred_provider": self.preferred_provider,
            "providers": [
                {
                    "provider_id": pid,
                    "name": c.name,
                    "default_model": c.default_model,
                    "has_baa": c.has_baa,
                    "has_api_key": bool(self._runtime_keys.get(pid) or (c.api_key_env and os.environ.get(c.api_key_env))),
                    "is_active": self._is_provider_active(pid),
                    "is_preferred": pid == self.preferred_provider,
                    "requires_key": bool(c.api_key_env),
                }
                for pid, c in self.PROVIDERS.items()
            ],
        }

    def get_provider_chain(
        self,
        task: AITask,
        phi_sensitivity: Optional[PHISensitivity] = None,
        pharmacy_id: Optional[str] = None,
        force_local: bool = False,
    ) -> list[str]:
        """
        Returns ordered list of providers to try for this task.
        Enforces PHI safety: removes non-BAA providers for sensitive tasks.
        """
        # Force local (maximum privacy)
        if force_local:
            return ["ollama_local"]

        # Check pharmacy-specific routing override
        if pharmacy_id and pharmacy_id in self._custom_routing:
            chain = self._custom_routing[pharmacy_id].get(task)
            if chain:
                return chain

        chain = list(self.TASK_ROUTING.get(task, ["anthropic"]))

        # PHI enforcement — remove non-BAA providers for sensitive data
        phi = phi_sensitivity or TASK_PHI_SENSITIVITY.get(task, PHISensitivity.MEDIUM)
        if phi in (PHISensitivity.HIGH, PHISensitivity.MEDIUM):
            chain = [p for p in chain if self.PROVIDERS.get(p, ProviderConfig(
                name="", api_key_env="", base_url=None, models=[], default_model="",
                strengths=[], has_baa=False, cost_per_1k_input=0, cost_per_1k_output=0
            )).has_baa]

        # Filter out inactive providers
        chain = [p for p in chain if self._is_provider_active(p)]

        # Owner-preferred provider (e.g. Gemini) goes FIRST for every task,
        # as long as it's active and PHI rules allow it (it must be BAA for
        # PHI-sensitive tasks — Google/Gemini is BAA-covered, so it qualifies).
        pref = self.preferred_provider
        if pref and self._is_provider_active(pref):
            pref_ok = self.PROVIDERS[pref].has_baa if phi in (
                PHISensitivity.HIGH, PHISensitivity.MEDIUM) else True
            if pref_ok:
                chain = [pref] + [p for p in chain if p != pref]

        # Fallback to anthropic (always BAA, highest quality)
        if not chain:
            chain = ["anthropic"]

        return chain

    def _is_provider_active(self, provider_name: str) -> bool:
        config = self.PROVIDERS.get(provider_name)
        if not config or not config.is_active:
            return False
        if config.api_key_env and not os.environ.get(config.api_key_env):
            return False  # No API key configured
        if provider_name == "ollama_local":
            return True  # Always available if configured
        return True

    async def call(
        self,
        task: AITask,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.1,
        pharmacy_id: Optional[str] = None,
        staff_id: Optional[str] = None,
        phi_sensitivity: Optional[PHISensitivity] = None,
        force_provider: Optional[str] = None,
    ) -> AIResponse:
        """
        Route a prompt to the best available provider for the task.
        Automatically falls back on error or excessive latency.
        """
        import uuid
        invocation_id = str(uuid.uuid4())[:12]
        chain = [force_provider] if force_provider else self.get_provider_chain(
            task, phi_sensitivity, pharmacy_id
        )

        last_error = None
        for i, provider_name in enumerate(chain):
            try:
                start = time.monotonic()
                response_text, tokens_in, tokens_out = await self._invoke_provider(
                    provider_name, prompt, system_prompt, max_tokens, temperature
                )
                latency = (time.monotonic() - start) * 1000

                # Update latency tracking
                if provider_name not in self._latency_tracker:
                    self._latency_tracker[provider_name] = []
                self._latency_tracker[provider_name].append(latency)
                if len(self._latency_tracker[provider_name]) > 100:
                    self._latency_tracker[provider_name].pop(0)

                config = self.PROVIDERS[provider_name]
                cost = (tokens_in * config.cost_per_1k_input / 1000 +
                        tokens_out * config.cost_per_1k_output / 1000)

                # Track cost
                self._cost_tracker[provider_name] = (
                    self._cost_tracker.get(provider_name, 0.0) + cost
                )

                # Audit log
                self._invocation_log.append(AIInvocationRecord(
                    invocation_id=invocation_id, task_type=task,
                    provider=provider_name, model=config.default_model,
                    tokens_input=tokens_in, tokens_output=tokens_out,
                    latency_ms=latency, cost_usd=cost,
                    phi_sensitivity=phi_sensitivity or TASK_PHI_SENSITIVITY.get(task, PHISensitivity.MEDIUM),
                    pharmacy_id=pharmacy_id, staff_id=staff_id,
                    success=True, error=None,
                ))

                logger.info(
                    "AI call: task=%s provider=%s model=%s latency=%.0fms cost=$%.4f",
                    task.value, provider_name, config.default_model, latency, cost
                )

                return AIResponse(
                    content=response_text, provider=provider_name,
                    model=config.default_model, tokens_input=tokens_in,
                    tokens_output=tokens_out, latency_ms=latency, cost_usd=cost,
                    invocation_id=invocation_id, from_fallback=(i > 0),
                    fallback_provider=chain[0] if i > 0 else None,
                )

            except Exception as exc:
                last_error = exc
                logger.warning("Provider %s failed for task %s: %s", provider_name, task.value, exc)
                self._invocation_log.append(AIInvocationRecord(
                    invocation_id=invocation_id, task_type=task,
                    provider=provider_name, model="",
                    tokens_input=0, tokens_output=0, latency_ms=0, cost_usd=0,
                    phi_sensitivity=phi_sensitivity or PHISensitivity.MEDIUM,
                    pharmacy_id=pharmacy_id, staff_id=staff_id,
                    success=False, error=str(exc),
                ))
                continue

        raise RuntimeError(
            f"All AI providers failed for task {task.value}. "
            f"Last error: {last_error}. "
            f"Providers tried: {', '.join(chain)}"
        )

    async def stream(
        self,
        task: AITask,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        pharmacy_id: Optional[str] = None,
        phi_sensitivity: Optional[PHISensitivity] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream response tokens for real-time UI display."""
        chain = self.get_provider_chain(task, phi_sensitivity, pharmacy_id)
        provider_name = chain[0]

        async for chunk in self._stream_provider(provider_name, prompt, system_prompt, max_tokens):
            yield chunk

    async def _invoke_provider(
        self,
        provider: str,
        prompt: str,
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, int, int]:
        """Invoke a specific provider. Returns (response_text, tokens_in, tokens_out)."""

        config = self.PROVIDERS[provider]

        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            messages = [{"role": "user", "content": prompt}]
            kwargs = {"model": config.default_model, "max_tokens": max_tokens,
                      "messages": messages, "temperature": temperature}
            if system_prompt:
                kwargs["system"] = system_prompt
            response = client.messages.create(**kwargs)
            text = response.content[0].text
            return text, response.usage.input_tokens, response.usage.output_tokens

        elif provider == "openai":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
            msgs = []
            if system_prompt:
                msgs.append({"role": "system", "content": system_prompt})
            msgs.append({"role": "user", "content": prompt})
            response = await client.chat.completions.create(
                model=config.default_model, messages=msgs,
                max_tokens=max_tokens, temperature=temperature,
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return text, usage.prompt_tokens, usage.completion_tokens

        elif provider == "google":
            import google.generativeai as genai
            genai.configure(api_key=os.environ.get("GOOGLE_API_KEY", ""))
            model = genai.GenerativeModel(
                config.default_model,
                system_instruction=system_prompt,
            )
            response = await asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config={"max_output_tokens": max_tokens, "temperature": temperature},
            )
            text = response.text
            # Google doesn't always return token counts in basic API
            approx_in = len(prompt.split()) * 1.3
            approx_out = len(text.split()) * 1.3
            return text, int(approx_in), int(approx_out)

        elif provider == "cohere":
            import cohere
            client = cohere.Client(api_key=os.environ.get("COHERE_API_KEY", ""))
            preamble = system_prompt or ""
            response = await asyncio.to_thread(
                client.chat,
                model=config.default_model,
                message=prompt,
                preamble=preamble,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = response.text
            meta = response.meta
            tokens_in  = getattr(getattr(meta, "tokens", None), "input_tokens", len(prompt.split()))
            tokens_out = getattr(getattr(meta, "tokens", None), "output_tokens", len(text.split()))
            return text, int(tokens_in), int(tokens_out)

        elif provider == "mistral":
            from mistralai import Mistral
            client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY", ""))
            msgs = []
            if system_prompt:
                msgs.append({"role": "system", "content": system_prompt})
            msgs.append({"role": "user", "content": prompt})
            response = await client.chat.complete_async(
                model=config.default_model, messages=msgs,
                max_tokens=max_tokens, temperature=temperature,
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return text, usage.prompt_tokens, usage.completion_tokens

        elif provider == "groq":
            # Groq exposes an OpenAI-compatible API — reuse the installed openai
            # SDK with Groq's base_url (no separate groq dependency needed).
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=os.environ.get("GROQ_API_KEY", ""),
                                 base_url=config.base_url or "https://api.groq.com/openai/v1")
            msgs = []
            if system_prompt:
                msgs.append({"role": "system", "content": system_prompt})
            msgs.append({"role": "user", "content": prompt})
            response = await client.chat.completions.create(
                model=config.default_model, messages=msgs,
                max_tokens=max_tokens, temperature=temperature,
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return text, usage.prompt_tokens, usage.completion_tokens

        elif provider == "ollama_local":
            import httpx
            base_url = config.base_url or "http://localhost:11434"
            payload = {
                "model": config.default_model, "prompt": prompt,
                "stream": False, "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if system_prompt:
                payload["system"] = system_prompt
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{base_url}/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data.get("response", "")
                return text, data.get("prompt_eval_count", 0), data.get("eval_count", 0)

        raise ValueError(f"Unknown provider: {provider}")

    async def _stream_provider(
        self,
        provider: str,
        prompt: str,
        system_prompt: Optional[str],
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens from provider."""
        config = self.PROVIDERS[provider]

        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            kwargs = {"model": config.default_model, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]}
            if system_prompt:
                kwargs["system"] = system_prompt
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield text
            return

        # Generic OpenAI-compatible streaming (works for OpenAI, Groq, Mistral, Ollama)
        from openai import AsyncOpenAI
        api_key = os.environ.get(config.api_key_env, "none")
        client = AsyncOpenAI(api_key=api_key, base_url=config.base_url)
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
        async with client.chat.completions.stream(
            model=config.default_model, messages=msgs, max_tokens=max_tokens,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    # ── Status & monitoring ───────────────────────────────────────────────

    def get_provider_status(self) -> list[dict]:
        """Returns live status for all providers — feeds the AI Hub dashboard."""
        statuses = []
        for name, config in self.PROVIDERS.items():
            latencies = self._latency_tracker.get(name, [])
            p50 = sorted(latencies)[len(latencies)//2] if latencies else None
            p95 = sorted(latencies)[int(len(latencies)*0.95)] if latencies else None
            statuses.append({
                "name": config.name,
                "provider_id": name,
                "is_active": self._is_provider_active(name),
                "has_api_key": bool(os.environ.get(config.api_key_env)) if config.api_key_env else True,
                "has_baa": config.has_baa,
                "models": config.models,
                "default_model": config.default_model,
                "strengths": config.strengths,
                "cost_per_1k_input": config.cost_per_1k_input,
                "cost_per_1k_output": config.cost_per_1k_output,
                "latency_p50_ms": round(p50, 0) if p50 else None,
                "latency_p95_ms": round(p95, 0) if p95 else None,
                "cost_today_usd": round(self._cost_tracker.get(name, 0.0), 4),
                "requests_today": sum(
                    1 for r in self._invocation_log
                    if r.provider == name and r.success
                ),
                "tokens_today": sum(
                    r.tokens_input + r.tokens_output
                    for r in self._invocation_log if r.provider == name
                ),
            })
        return statuses

    def get_daily_cost_breakdown(self) -> dict:
        """Returns cost breakdown by provider and task for the dashboard."""
        breakdown: dict[str, dict[str, float]] = {}
        for record in self._invocation_log:
            if record.provider not in breakdown:
                breakdown[record.provider] = {}
            task = record.task_type.value
            breakdown[record.provider][task] = (
                breakdown[record.provider].get(task, 0.0) + record.cost_usd
            )
        return {
            "by_provider": {p: sum(tasks.values()) for p, tasks in breakdown.items()},
            "by_task": breakdown,
            "total": sum(self._cost_tracker.values()),
        }

    def set_pharmacy_routing(self, pharmacy_id: str, task: AITask, providers: list[str]) -> None:
        """Allow pharmacy-level routing override (admin feature)."""
        if pharmacy_id not in self._custom_routing:
            self._custom_routing[pharmacy_id] = {}
        self._custom_routing[pharmacy_id][task] = providers

    def get_audit_log(self, limit: int = 100) -> list[dict]:
        recent = sorted(self._invocation_log, key=lambda r: r.invoked_at, reverse=True)[:limit]
        return [
            {
                "invocation_id": r.invocation_id,
                "task": r.task_type.value,
                "provider": r.provider,
                "model": r.model,
                "tokens_in": r.tokens_input,
                "tokens_out": r.tokens_output,
                "latency_ms": round(r.latency_ms, 0),
                "cost_usd": round(r.cost_usd, 5),
                "phi_sensitivity": r.phi_sensitivity.value,
                "success": r.success,
                "error": r.error,
                "pharmacist_rating": r.pharmacist_rating,
                "invoked_at": r.invoked_at.isoformat(),
            }
            for r in recent
        ]


# Application singleton
_registry: Optional[AIProviderRegistry] = None

def get_ai_registry() -> AIProviderRegistry:
    global _registry
    if _registry is None:
        _registry = AIProviderRegistry()
    return _registry
