"""Tool schema exposed to the LLM manager (Ollama tool-calling / OpenAI-style).

Management + social only — NOT signal origination. The model sizes/skips signals,
closes positions, and posts; it cannot invent trades (guardrails enforce this).
"""

from __future__ import annotations

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "size_order",
            "description": (
                "Place a sized order for a symbol that has a current strategy signal. "
                "On a BUY, set a stop_loss_pct (and ideally take_profit_pct / "
                "trailing_stop_pct) — deterministic exits enforced automatically, "
                "faster than your next tick. If you omit stop_loss_pct a default is applied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "notional": {"type": "number", "description": "USD amount to trade"},
                    "stop_loss_pct": {"type": "number", "description": "e.g. 0.05 = exit 5% below entry"},
                    "take_profit_pct": {"type": "number", "description": "e.g. 0.12 = take profit 12% above entry"},
                    "trailing_stop_pct": {"type": "number", "description": "e.g. 0.04 = trail 4% below the high"},
                    "rationale": {"type": "string"},
                },
                "required": ["symbol", "side", "notional", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_position",
            "description": "Fully close an existing position in a symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["symbol", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_signal",
            "description": "Deliberately take no action on a signal, with a reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["symbol", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "post_to_feed",
            "description": "Post a short message to the in-game social feed (Chirp) in persona voice.",
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {"type": "string", "description": "<=280 chars, no financial advice"},
                    "kind": {"type": "string", "enum": ["take", "trade_note", "banter"]},
                },
                "required": ["body", "kind"],
            },
        },
    },
]
