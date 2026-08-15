from types import SimpleNamespace

import app.llm.provider as provider


def _settings(**over):
    base = dict(
        llm_provider="ollama", llm_base_url="", llm_api_key="",
        llm_model="", llm_reasoning_model="", llm_referer="", llm_title="",
        ollama_host="https://ollama.com/v1", ollama_api_key="",
        ollama_model="flash-x", ollama_reasoning_model="",
        ollama_timeout_s=45.0, ollama_reasoning_timeout_s=120.0,
    )
    base.update(over)
    ns = SimpleNamespace(**base)
    # mirror config.Settings.reasoning_model property
    ns.reasoning_model = base["ollama_reasoning_model"] or base["ollama_model"]
    return ns


def _patch(monkeypatch, s):
    monkeypatch.setattr(provider, "get_settings", lambda: s)


def test_ollama_default_unchanged(monkeypatch):
    _patch(monkeypatch, _settings(ollama_api_key="k"))
    pc = provider.resolve_provider("manage")
    assert pc.base_url == "https://ollama.com/v1"
    assert pc.api_key == "k"  # falls back to OLLAMA_API_KEY for the ollama provider
    assert pc.model == "flash-x"
    assert pc.default_headers == {}


def test_openrouter_preset_and_headers(monkeypatch):
    _patch(monkeypatch, _settings(
        llm_provider="openrouter", llm_api_key="sk", llm_model="anthropic/claude-sonnet-5",
        llm_referer="https://x", llm_title="Neuro"))
    pc = provider.resolve_provider("manage")
    assert pc.base_url == "https://openrouter.ai/api/v1"
    assert pc.api_key == "sk"
    assert pc.model == "anthropic/claude-sonnet-5"
    assert pc.default_headers == {"HTTP-Referer": "https://x", "X-Title": "Neuro"}


def test_generic_key_wins_over_ollama_key(monkeypatch):
    _patch(monkeypatch, _settings(llm_api_key="generic", ollama_api_key="ollama"))
    assert provider.resolve_provider("manage").api_key == "generic"


def test_non_ollama_provider_ignores_ollama_key(monkeypatch):
    # An OLLAMA_API_KEY must NOT leak to a non-ollama provider that has no LLM_API_KEY.
    _patch(monkeypatch, _settings(llm_provider="openrouter", ollama_api_key="ollama"))
    assert provider.resolve_provider("manage").api_key == ""
    assert provider.has_key() is False


def test_custom_base_url_override(monkeypatch):
    _patch(monkeypatch, _settings(llm_provider="custom", llm_base_url="http://box:1234/v1"))
    assert provider.resolve_provider("manage").base_url == "http://box:1234/v1"


def test_reasoner_role_uses_reasoning_model_and_timeout(monkeypatch):
    _patch(monkeypatch, _settings(llm_reasoning_model="big-reasoner"))
    pc = provider.resolve_provider("reasoner")
    assert pc.model == "big-reasoner"
    assert pc.timeout == 120.0
