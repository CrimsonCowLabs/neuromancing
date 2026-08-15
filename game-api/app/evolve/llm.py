"""LangChain ChatOpenAI clients for the evolution layer — a strong reasoner for
design/critique + flash for reflection/compaction. Transport (base_url/key/timeout/
headers) + default models come from the shared provider seam (app/llm/provider.py), so
evolution honors the same LLM_PROVIDER config as the agent decision path. Ollama is the
default (D4); models read from config, never hardcoded."""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from ..llm import provider


@lru_cache(maxsize=8)
def _mk(base_url: str, api_key: str, model: str, timeout: float,
        temperature: float, headers_items: tuple) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key or "none",
        model=model,
        timeout=timeout,
        max_retries=0,                    # Temporal owns retries
        temperature=temperature,
        default_headers=dict(headers_items) or None,
    )


def _client(role: str, temperature: float) -> ChatOpenAI:
    pc = provider.resolve_provider(role)
    return _mk(pc.base_url, pc.api_key, pc.model, pc.timeout, temperature,
               tuple(sorted(pc.default_headers.items())))


def reasoner() -> ChatOpenAI:
    """Strong model for strategy design/critique."""
    return _client("reasoner", 0.5)


def flash() -> ChatOpenAI:
    """Cheap model for reflection / summarization / compaction."""
    return _client("flash", 0.3)


def has_key() -> bool:
    return provider.has_key()
