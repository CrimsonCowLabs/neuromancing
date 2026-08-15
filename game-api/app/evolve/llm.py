"""LangChain ChatOpenAI clients pointed at Ollama Cloud (never Anthropic) — a strong
reasoner for design/critique + flash for reflection/compaction. Same endpoint pattern
as agents/llm.py; models read from config, never hardcoded."""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from ..config import get_settings


@lru_cache(maxsize=8)
def _mk(model: str, timeout: float, temperature: float) -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(
        base_url=s.ollama_host,           # https://ollama.com/v1
        api_key=s.ollama_api_key or "none",
        model=model,
        timeout=timeout,
        max_retries=0,                    # Temporal owns retries
        temperature=temperature,
    )


def reasoner() -> ChatOpenAI:
    """Strong model for strategy design/critique."""
    s = get_settings()
    return _mk(s.reasoning_model, s.ollama_reasoning_timeout_s, 0.5)


def flash() -> ChatOpenAI:
    """Cheap model for reflection / summarization / compaction."""
    s = get_settings()
    return _mk(s.ollama_model, s.ollama_timeout_s, 0.3)


def has_key() -> bool:
    return bool(get_settings().ollama_api_key)
