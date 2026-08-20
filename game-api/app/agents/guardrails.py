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
DEFAULT_SHORT_COLLATERAL_MULT = 1.5  # reg-T-style; mirror trade-api short_collateral_mult


@dataclass
class GuardrailContext:
    equity: float
    cash: float
    positions: dict[str, float]  # symbol -> SIGNED qty (>0 long, <0 short)
    position_values: dict[str, float]  # symbol -> signed market value
    signals: dict[str, str]  # symbol -> latest signal action
    universe: list[str]
    equity_open: bool = True  # when False, equity-symbol orders are rejected
    buying_power: float = 0.0  # cash − reserved short collateral
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT
    per_tick_notional_pct: float = DEFAULT_PER_TICK_NOTIONAL_PCT
    max_positions: int = DEFAULT_MAX_POSITIONS
    short_collateral_mult: float = DEFAULT_SHORT_COLLATERAL_MULT


@dataclass
class GuardrailResult:
    valid: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def reject(self, action: dict, reason: str) -> None:
        self.rejected.append({"action": action, "reason": reason})


def validate(actions: list[dict], ctx: GuardrailContext) -> GuardrailResult:
    result = GuardrailResult()
    longs = {s for s, q in ctx.positions.items() if q > 0}
    shorts = {s for s, q in ctx.positions.items() if q < 0}
    held = longs | shorts
    per_tick_cap = ctx.per_tick_notional_pct * ctx.equity
    max_pos_val = ctx.max_position_pct * ctx.equity

    def _pass_open(action: dict, side: str, notional: float) -> dict:
        out = {"type": "order", "symbol": action["symbol"], "side": side,
               "notional": round(notional, 2), "rationale": action.get("rationale", "")}
        for k in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct"):
            if action.get(k) is not None:
                out[k] = action[k]
        return out

    for action in actions:
        symbol = action.get("symbol", "")
        atype = action.get("type")

        if symbol not in ctx.universe:
            result.reject(action, f"{symbol} not in tradable universe")
            continue

        # Equity can't trade on stale prices while the market is closed; block it at the
        # source (opens, closes, covers) so no rejected order is created.
        if not ctx.equity_open and not is_crypto(symbol):
            result.reject(action, "equity market closed")
            continue

        # A generic close works for a held position of EITHER sign (direction is
        # resolved from the position sign in decision.py: long→sell, short→buy).
        if atype == "close":
            if symbol not in held:
                result.reject(action, "close with no open position")
                continue
            result.valid.append({"type": "close", "symbol": symbol,
                                 "rationale": action.get("rationale", "")})
            continue

        if atype != "order":
            result.reject(action, f"unknown action type: {atype}")
            continue

        side = action.get("side")

        if side == "sell":  # close a long
            if symbol not in longs:
                result.reject(action, "sell with no open long")
                continue
            result.valid.append(action)
            continue

        if side == "cover":  # close a short
            if symbol not in shorts:
                result.reject(action, "cover with no open short")
                continue
            result.valid.append(action)
            continue

        if side == "buy":  # open/add a long — must be signal-backed
            if symbol in shorts:
                result.reject(action, "cover the short before going long")
                continue
            if ctx.signals.get(symbol) != "buy":
                result.reject(action, "buy not backed by a current signal")
                continue
            if symbol not in held and len(held) >= ctx.max_positions:
                result.reject(action, "max open positions reached")
                continue
            notional = float(action.get("notional") or 0.0)
            if notional <= 0:
                result.reject(action, "buy notional must be positive")
                continue
            room = max(0.0, max_pos_val - ctx.position_values.get(symbol, 0.0))
            capped = min(notional, per_tick_cap, room, ctx.cash)
            if capped <= 0:
                result.reject(action, "no room under concentration/cash caps")
                continue
            result.valid.append(_pass_open(action, "buy", capped))
            continue

        if side == "short":  # open/add a short — signal-backed, collateralized, mandatory stop
            if symbol in longs:
                result.reject(action, "close the long before going short")
                continue
            if ctx.signals.get(symbol) != "short":
                result.reject(action, "short not backed by a current signal")
                continue
            if symbol not in held and len(held) >= ctx.max_positions:
                result.reject(action, "max open positions reached")
                continue
            if action.get("stop_loss_pct") is None:
                result.reject(action, "short requires a stop-loss (unbounded risk)")
                continue
            notional = float(action.get("notional") or 0.0)
            if notional <= 0:
                result.reject(action, "short notional must be positive")
                continue
            # Short-side concentration: |existing short value| + new <= max position value.
            existing_short = abs(min(0.0, ctx.position_values.get(symbol, 0.0)))
            room = max(0.0, max_pos_val - existing_short)
            # Collateral affordability: reserved (notional * mult) must fit buying power.
            bp_room = ctx.buying_power / ctx.short_collateral_mult if ctx.short_collateral_mult else 0.0
            capped = min(notional, per_tick_cap, room, bp_room)
            if capped <= 0:
                result.reject(action, "no room under short concentration/collateral caps")
                continue
            result.valid.append(_pass_open(action, "short", capped))
            continue

        result.reject(action, f"unknown order side: {side}")

    return result
