"""LLM management layer via Ollama Cloud (never Anthropic).

Accessed through Ollama Cloud's OpenAI-COMPATIBLE endpoint (base_url …/v1) using
the `openai` SDK — the pattern proven in the hl-froggy trading system. A HARD
TIMEOUT is mandatory (hl-froggy incident 2026-04-08: a stuck call froze the loop
for 22h). When no OLLAMA_API_KEY is set or the call fails, a deterministic
fallback manager runs so the game still plays headlessly.

Given the tick's strategy signals + portfolio/risk state, decide sizing, which
signals to take/skip, closes, and a social post — in persona voice.
"""

from __future__ import annotations

import asyncio
import json
import logging

from ..config import get_settings
from ..llm import provider
from . import budget
from .tools import TOOLS

log = logging.getLogger("neuromancing.llm")

FALLBACK_MODEL = "deterministic-fallback"
DEFAULT_STOP_LOSS_PCT = 0.08  # mandatory floor the fallback attaches to a short open

# Reused across calls — one client + pool per provider base_url, not one per tick/agent.
_clients: dict[str, object] = {}
_sem: asyncio.Semaphore | None = None


def _get_client(pc: provider.ProviderConfig):
    """Cached AsyncOpenAI client for a resolved provider (keyed by base_url so a
    config change is honored). Works for any OpenAI-compatible endpoint."""
    cli = _clients.get(pc.base_url)
    if cli is None:
        from openai import AsyncOpenAI

        cli = AsyncOpenAI(
            base_url=pc.base_url,
            api_key=pc.api_key or "none",
            timeout=pc.timeout,
            max_retries=0,  # exactly one attempt — Temporal owns retries, no stacking
            default_headers=pc.default_headers or None,
        )
        _clients[pc.base_url] = cli
    return cli


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(get_settings().ollama_max_concurrency)
    return _sem


def _default_intro(persona: dict) -> str:
    """Generic persona header, used when a construct has no authored `system_prompt`."""
    return (
        f"You are {persona.get('display_name', 'a trader')}, an autonomous trading agent.\n"
        f"Thesis: {persona.get('thesis', '')}\n"
        f"Voice: {persona.get('voice_style', 'concise, confident')}\n"
        f"Risk temperament: {persona.get('risk_temperament', 'balanced')}"
    )


# The fixed MANAGEMENT-layer contract — safety/trading rules that apply to EVERY
# construct and are always appended after the persona voice. Non-overridable: a
# persona's authored `system_prompt` supplies voice/decision-style only; these
# rules (and the deterministic guardrails behind them) still bind regardless.
_MANAGEMENT_CONTRACT = (
    "You are the MANAGEMENT layer. Deterministic strategies produce the trade "
    "signals; you decide position sizing, which signals to act on or skip, when "
    "to close, and overall risk. You may ONLY act on symbols that have a matching "
    "current signal — a 'buy' signal to open a LONG (size_order side=buy), a 'short' "
    "signal to open a SHORT / sell-to-open (size_order side=short) — or an open "
    "position to close (close_position sells a long or covers a short). Never invent "
    "trades. A SHORT profits when price FALLS and its loss is otherwise unbounded, so "
    "always set a stop_loss_pct on it (a default floor is applied if you don't). "
    "Positions carry a SIGNED qty: negative means you are short that symbol. "
    "Each signal carries `sources` (the per-strategy signals behind it, each with "
    "indicator `features`) and a `conflict` flag when your own strategies disagree "
    "on that symbol — weigh both when sizing or skipping; be more cautious on "
    "conflict. "
    "Use size_order/close_position/skip_signal for decisions and post_to_feed for "
    "one short in-character message. Set a stop_loss_pct on every open (and, when it "
    "fits your style, a take_profit_pct or trailing_stop_pct) — these are enforced "
    "deterministically the instant price crosses them. Never give financial advice "
    "or promise returns."
)


def _system_prompt(persona: dict) -> str:
    """A construct's authored voice (`system_prompt`) — or the generic header when it
    has none — followed by the fixed, non-overridable MANAGEMENT contract."""
    intro = persona.get("system_prompt") or _default_intro(persona)
    return f"{intro}\n\n{_MANAGEMENT_CONTRACT}"


def _llm_view(context: dict) -> dict:
    """Trim the context to just what the model reasons over, so per-tick token cost
    stays bounded regardless of universe size. The model may only act on symbols with
    a current signal or an open position, so it needs marks for *those* only — not the
    whole (now up to ~1000-symbol) universe. The full `marks`/`universe` stay in
    `context` for the server-side guardrails/sizing path (see decision.apply_activity)."""
    signals = context.get("signals", {})
    positions = context.get("positions", {})
    marks = context.get("marks", {})
    keep = set(signals) | set(positions)
    return {
        "equity": context.get("equity"),
        "cash": context.get("cash"),
        "buying_power": context.get("buying_power"),
        "positions": positions,  # SIGNED qty (negative = short)
        "position_values": context.get("position_values", {}),
        "signals": signals,
        "marks": {s: marks[s] for s in keep if s in marks},
        "equity_open": context.get("equity_open", True),
    }


def _fallback(context: dict, persona: dict) -> dict:
    """Conservative deterministic manager: size buys at a fixed fraction of equity,
    close on exit signals. Runs when the LLM is unavailable."""
    equity = float(context.get("equity", 0.0))
    signals = context.get("signals", {})  # symbol -> {action, strength}
    positions = context.get("positions", {})
    actions: list[dict] = []
    notes: list[str] = []
    for symbol, sig in signals.items():
        action = sig.get("action")
        strength = float(sig.get("strength", 0.0))
        qty = positions.get(symbol, 0)
        if action == "buy":
            notional = round(max(50.0, equity * 0.08 * (0.5 + strength)), 2)
            actions.append({"type": "order", "symbol": symbol, "side": "buy",
                            "notional": notional, "rationale": f"signal buy (strength {strength:.2f})"})
            notes.append(f"opening {symbol}")
        elif action == "short":
            notional = round(max(50.0, equity * 0.08 * (0.5 + strength)), 2)
            # Fallback shorts always carry a stop (loss is otherwise unbounded).
            actions.append({"type": "order", "symbol": symbol, "side": "short",
                            "notional": notional, "stop_loss_pct": DEFAULT_STOP_LOSS_PCT,
                            "rationale": f"signal short (strength {strength:.2f})"})
            notes.append(f"shorting {symbol}")
        elif action in ("exit", "sell") and qty > 0:
            actions.append({"type": "close", "symbol": symbol, "rationale": "signal exit"})
            notes.append(f"closing {symbol}")
        elif action == "cover" and qty < 0:
            actions.append({"type": "close", "symbol": symbol, "rationale": "signal cover"})
            notes.append(f"covering {symbol}")

    narration = "; ".join(notes) if notes else "holding — no compelling signals this tick"
    post = None
    if notes:
        post = {"body": f"{narration.capitalize()}. Managing risk, staying disciplined.",
                "kind": "trade_note"}
    return {
        "actions": actions,
        "narration": narration,
        "posts": [post] if post else [],
        "model": FALLBACK_MODEL,
        "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
    }


def _parse_tool_calls(message) -> dict:
    """Parse OpenAI-style tool_calls (function.name + JSON-string arguments)."""
    actions: list[dict] = []
    posts: list[dict] = []
    notes: list[str] = []
    for call in getattr(message, "tool_calls", None) or []:
        fn = call.function
        try:
            args = fn.arguments if isinstance(fn.arguments, dict) else json.loads(fn.arguments)
        except (json.JSONDecodeError, TypeError):
            continue
        if fn.name == "size_order":
            act = {"type": "order", "symbol": args["symbol"], "side": args["side"],
                   "notional": float(args["notional"]), "rationale": args.get("rationale", "")}
            for k in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct"):
                if args.get(k) is not None:
                    act[k] = float(args[k])
            actions.append(act)
            notes.append(args.get("rationale", ""))
        elif fn.name == "close_position":
            actions.append({"type": "close", "symbol": args["symbol"],
                            "rationale": args.get("rationale", "")})
            notes.append(args.get("rationale", ""))
        elif fn.name == "post_to_feed":
            posts.append({"body": str(args.get("body", ""))[:280], "kind": args.get("kind", "take")})
        # skip_signal -> intentionally no action
    return {"actions": actions, "posts": posts, "narration": "; ".join(n for n in notes if n)}


async def _fallback_with_reason(context: dict, persona: dict, model: str, reason: str) -> dict:
    await budget.record_fallback(model, reason)
    return _fallback(context, persona)


async def manage(context: dict, persona: dict, handle: str = "unknown") -> dict:
    pc = provider.resolve_provider("manage")
    # Per-persona model override wins over the provider's default model.
    model = persona.get("model_config", {}).get("model") or pc.model

    if not pc.api_key:
        # Empty key = run headless. Distinguish the deliberate dev kill-switch from a
        # merely-unconfigured deployment so the digest/telemetry reads honestly.
        reason = "llm_disabled" if not get_settings().llm_enabled else "no_api_key"
        return await _fallback_with_reason(context, persona, model, reason)

    # Circuit-breaker: if the daily token budget is spent, don't call the LLM.
    tripped, why = await budget.over_budget(handle)
    if tripped:
        return await _fallback_with_reason(context, persona, model, why)

    try:
        from openai import APIError, APITimeoutError

        async with _semaphore():
            resp = await _get_client(pc).chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _system_prompt(persona)},
                    {"role": "user", "content": json.dumps(_llm_view(context), default=str)},
                ],
                tools=TOOLS,
                tool_choice="auto",
                temperature=persona.get("model_config", {}).get("temperature", 0.4),
                max_tokens=1536,
            )
    except (APITimeoutError, APIError) as e:  # expected infra failure -> fall back
        log.warning("Ollama Cloud call failed (%s); using fallback manager", e)
        return await _fallback_with_reason(context, persona, model, "api_error")
    except Exception as e:  # noqa: BLE001 — a code bug: fall back but LOUD (stack trace)
        log.error("LLM manage() unexpected error; using fallback manager", exc_info=True)
        return await _fallback_with_reason(context, persona, model, "unexpected_error")

    choice = resp.choices[0]
    message = choice.message
    if getattr(choice, "finish_reason", None) == "length":
        # Truncated: a mid-JSON tool call would be silently dropped. Flag it.
        log.warning("LLM response truncated (finish_reason=length) for %s/%s; "
                    "some tool calls may be lost — consider raising max_tokens", handle, model)
    parsed = _parse_tool_calls(message)
    usage = getattr(resp, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
    tokens_out = getattr(usage, "completion_tokens", 0) or 0
    await budget.record_usage(handle, tokens_in, tokens_out)
    return {
        "actions": parsed["actions"],
        "narration": parsed["narration"] or (message.content or "")[:500],
        "posts": parsed["posts"],
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": budget.estimate_cost_usd(tokens_in, tokens_out),
    }
