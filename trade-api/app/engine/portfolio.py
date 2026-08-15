"""Position & cash math for applying fills. Long-only at MVP (no shorting):
sells may only reduce/close an existing long; the broker rejects oversized sells.

Pure Decimal math, property-tested — this is the P&L spine, so no float ever.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from neuromancing_shared.money import D, ZERO, quantize_money, quantize_qty


@dataclass(frozen=True)
class PositionState:
    qty: Decimal
    avg_entry: Decimal


@dataclass(frozen=True)
class FillOutcome:
    cash: Decimal
    position: PositionState
    realized_pnl: Decimal


def apply_fill(
    *,
    cash: Decimal,
    position: PositionState,
    side: str,
    qty: Decimal,
    price: Decimal,
    fees: Decimal,
) -> FillOutcome:
    cash = D(cash)
    pos_qty = D(position.qty)
    pos_avg = D(position.avg_entry)
    qty = D(qty)
    price = D(price)
    fees = D(fees)

    if side == "buy":
        cost = quantize_money(qty * price + fees)
        new_cash = quantize_money(cash - cost)
        new_qty = quantize_qty(pos_qty + qty)
        # Weighted average entry over the combined long.
        if new_qty > 0:
            new_avg = quantize_money((pos_qty * pos_avg + qty * price) / new_qty)
        else:
            new_avg = ZERO
        return FillOutcome(new_cash, PositionState(new_qty, new_avg), ZERO)

    if side == "sell":
        if qty > pos_qty:
            raise ValueError("oversized sell (long-only): qty exceeds position")
        proceeds = quantize_money(qty * price - fees)
        new_cash = quantize_money(cash + proceeds)
        realized = quantize_money(qty * (price - pos_avg) - fees)
        new_qty = quantize_qty(pos_qty - qty)
        new_avg = pos_avg if new_qty > 0 else ZERO
        return FillOutcome(new_cash, PositionState(new_qty, new_avg), realized)

    raise ValueError(f"bad side: {side}")


def positions_value(marks: dict[str, Decimal], positions: dict[str, Decimal]) -> Decimal:
    """Mark-to-market value of open positions given latest prices."""
    total = ZERO
    for symbol, qty in positions.items():
        mark = marks.get(symbol)
        if mark is not None:
            total += D(qty) * D(mark)
    return quantize_money(total)
