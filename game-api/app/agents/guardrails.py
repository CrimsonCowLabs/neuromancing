"""Deterministic guardrail layer between the LLM manager and trade-api.

Pure and unit-tested. The LLM can only MANAGE — it cannot originate trades: a buy
must be backed by a current signal, and a close/sell must be backed by an existing
position. Also enforces concentration + per-tick notional caps. This is the hard
cap on LLM blast radius (and on prompt-injection).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ingest.universe import is_crypto

DEFAULT_MAX_POSITION_PCT = 0.20  # no single position > 20% of equity
DEFAULT_PER_TICK_NOTIONAL_PCT = 0.15  # no single order > 15% of equity
DEFAULT_MAX_POSITIONS = 8


@dataclass
class GuardrailContext:
    equity: float
    cash: float
    positions: dict[str, float]  # symbol -> qty
    position_values: dict[str, float]  # symbol -> current market value
    signals: dict[str, str]  # symbol -> latest signal action
    universe: list[str]
    equity_open: bool = True  # when False, equity-symbol orders are rejected
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT
    per_tick_notional_pct: float = DEFAULT_PER_TICK_NOTIONAL_PCT
    max_positions: int = DEFAULT_MAX_POSITIONS


@dataclass
class GuardrailResult:
    valid: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def reject(self, action: dict, reason: str) -> None:
        self.rejected.append({"action": action, "reason": reason})


def validate(actions: list[dict], ctx: GuardrailContext) -> GuardrailResult:
    result = GuardrailResult()
    open_positions = {s for s, q in ctx.positions.items() if q > 0}
    per_tick_cap = ctx.per_tick_notional_pct * ctx.equity
    max_pos_val = ctx.max_position_pct * ctx.equity

    for action in actions:
        symbol = action.get("symbol", "")
        atype = action.get("type")

        if symbol not in ctx.universe:
            result.reject(action, f"{symbol} not in tradable universe")
            continue

        # Equity can't trade on stale prices while the market is closed; block it
        # at the source (buys, sells, and closes) so no rejected order is created.
        if not ctx.equity_open and not is_crypto(symbol):
            result.reject(action, "equity market closed")
            continue

        if atype == "close":
            if symbol not in open_positions:
                result.reject(action, "close with no open position")
                continue
            result.valid.append({"type": "close", "symbol": symbol,
                                 "rationale": action.get("rationale", "")})
            continue

        if atype == "order":
            side = action.get("side")
            if side == "sell":
                if symbol not in open_positions:
                    result.reject(action, "sell with no open position")
                    continue
                result.valid.append(action)
                continue

            if side == "buy":
                if ctx.signals.get(symbol) != "buy":
                    result.reject(action, "buy not backed by a current signal")
                    continue
                if symbol not in open_positions and len(open_positions) >= ctx.max_positions:
                    result.reject(action, "max open positions reached")
                    continue
                notional = float(action.get("notional") or 0.0)
                if notional <= 0:
                    result.reject(action, "buy notional must be positive")
                    continue
                # Concentration: cap so existing + new value <= max position value.
                room = max(0.0, max_pos_val - ctx.position_values.get(symbol, 0.0))
                capped = min(notional, per_tick_cap, room, ctx.cash)
                if capped <= 0:
                    result.reject(action, "no room under concentration/cash caps")
                    continue
                valid_buy = {"type": "order", "symbol": symbol, "side": "buy",
                             "notional": round(capped, 2),
                             "rationale": action.get("rationale", "")}
                for k in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct"):
                    if action.get(k) is not None:
                        valid_buy[k] = action[k]
                result.valid.append(valid_buy)
                continue

        result.reject(action, f"unknown action type: {atype}")

    return result
