"""Provider seam for the LLM management (agents) + evolution layers.

One config-driven resolver so any **OpenAI-compatible** endpoint can back the models:
Ollama Cloud (the default), OpenRouter, a local server (LM Studio / llama.cpp /
ollama-local), or a fully custom base_url. This is the LLM analogue of the trade-side
`Broker` interface (decision D3) — it lets the open-source build bring its own provider.

Policy (D4): **Ollama is the default and we keep NO native Anthropic client.** Anthropic
models are reachable *through OpenRouter* (which exposes them over the OpenAI-compatible
API), so "BYO Anthropic" works without a second SDK or the Anthropic key ever touching
this code. The seam abstracts *transport* (base_url / key / timeout / headers) and a
*default model* per role — it does not guarantee a given provider+model supports
tool-calls or structured output; the deterministic fallback covers those failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import get_settings

# base_url per built-in preset. "ollama" (and any unknown value) resolves to the
# configured OLLAMA_HOST so existing deployments are byte-for-byte unchanged; "custom"
# requires LLM_BASE_URL.
_PRESETS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "local": "http://localhost:11434/v1",  # ollama-local / LM Studio-style default
}


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float
    default_headers: dict = field(default_factory=dict)
    provider: str = "ollama"


def _base_url(s) -> str:
    if s.llm_base_url:
        return s.llm_base_url
    if s.llm_provider in _PRESETS:
        return _PRESETS[s.llm_provider]
    return s.ollama_host  # ollama default (honors an existing OLLAMA_HOST override)


def _api_key(s) -> str:
    """Generic LLM_API_KEY wins; else fall back to OLLAMA_API_KEY for the ollama
    provider so deployments that only set OLLAMA_API_KEY keep working untouched.

    LLM_ENABLED=false zeroes the key unconditionally — the single "no LLM in dev"
    seam. Every consumer already treats an empty key as "run the deterministic
    fallback" (agents/llm.py::manage, evolve has_key() guards), so no token is spent."""
    if not s.llm_enabled:
        return ""
    if s.llm_api_key:
        return s.llm_api_key
    if s.llm_provider == "ollama":
        return s.ollama_api_key
    return ""


def _headers(s) -> dict:
    # OpenRouter uses these for attribution/ranking; harmless (and only sent) there.
    if s.llm_provider == "openrouter":
        h: dict = {}
        if s.llm_referer:
            h["HTTP-Referer"] = s.llm_referer
        if s.llm_title:
            h["X-Title"] = s.llm_title
        return h
    return {}


def resolve_provider(role: str) -> ProviderConfig:
    """Resolve transport + a default model for a call role.

    role: "manage" (agent decision) / "flash" (evolve reflection) use the standard
    model + agent timeout; "reasoner" (evolve strategy design) uses the reasoning
    model + reasoning timeout. Model precedence: LLM_MODEL / LLM_REASONING_MODEL
    override the ollama_* defaults; call sites may still override per call (e.g. the
    per-persona model in agents/llm.py::manage).
    """
    s = get_settings()
    if role == "reasoner":
        model = s.llm_reasoning_model or s.reasoning_model
        timeout = s.ollama_reasoning_timeout_s
    else:  # manage, flash
        model = s.llm_model or s.ollama_model
        timeout = s.ollama_timeout_s
    return ProviderConfig(
        base_url=_base_url(s), api_key=_api_key(s), model=model, timeout=timeout,
        default_headers=_headers(s), provider=s.llm_provider,
    )


def has_key() -> bool:
    """True if the resolved provider has an API key (any role — they share a key)."""
    return bool(_api_key(get_settings()))
