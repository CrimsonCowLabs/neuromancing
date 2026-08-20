"""Position & cash math for applying fills. Signed net-position model: qty > 0 is a
long, qty < 0 a short, 0 is flat. One order never FLIPS direction — a fill that would
close past flat is capped at the held qty (the caller records the executed qty). Four
transitions: open/increase long, close long, open/increase short, cover short.

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
    filled_qty: Decimal  # executed qty (a closing fill is capped at the held qty — no flip)
    fee: Decimal         # fee actually charged (prorated when a close is capped)


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
    if side not in ("buy", "sell"):
        raise ValueError(f"bad side: {side}")

    # Fees scale with the executed (possibly capped) qty, so cap first.
    if side == "buy" and pos_qty < 0:
        # ── cover (buy-to-close short); never flip to long → cap at the short size ──
        filled = qty if qty <= -pos_qty else -pos_qty
        f = quantize_money(fees * filled / qty) if qty > 0 else fees
        cost = quantize_money(filled * price + f)
        new_cash = quantize_money(cash - cost)
        realized = quantize_money(filled * (pos_avg - price) - f)  # short profit if bought back lower
        new_qty = quantize_qty(pos_qty + filled)
        new_avg = pos_avg if new_qty < 0 else ZERO
        return FillOutcome(new_cash, PositionState(new_qty, new_avg), realized, filled, f)

    if side == "sell" and pos_qty > 0:
        # ── close (sell-to-close long); never flip to short → cap at the long size ──
        filled = qty if qty <= pos_qty else pos_qty
        f = quantize_money(fees * filled / qty) if qty > 0 else fees
        proceeds = quantize_money(filled * price - f)
        new_cash = quantize_money(cash + proceeds)
        realized = quantize_money(filled * (price - pos_avg) - f)
        new_qty = quantize_qty(pos_qty - filled)
        new_avg = pos_avg if new_qty > 0 else ZERO
        return FillOutcome(new_cash, PositionState(new_qty, new_avg), realized, filled, f)

    if side == "buy":
        # ── open/increase long (pos_qty >= 0) ──
        cost = quantize_money(qty * price + fees)
        new_cash = quantize_money(cash - cost)
        new_qty = quantize_qty(pos_qty + qty)
        new_avg = quantize_money((pos_qty * pos_avg + qty * price) / new_qty) if new_qty > 0 else ZERO
        return FillOutcome(new_cash, PositionState(new_qty, new_avg), ZERO, qty, fees)

    # ── open/increase short (side == "sell", pos_qty <= 0): receive proceeds ──
    proceeds = quantize_money(qty * price - fees)
    new_cash = quantize_money(cash + proceeds)
    new_qty = quantize_qty(pos_qty - qty)  # more negative
    short_size = -new_qty
    new_avg = quantize_money((-pos_qty * pos_avg + qty * price) / short_size) if short_size > 0 else ZERO
    return FillOutcome(new_cash, PositionState(new_qty, new_avg), ZERO, qty, fees)


def positions_value(marks: dict[str, Decimal], positions: dict[str, Decimal]) -> Decimal:
    """Signed mark-to-market value of open positions (a short's value is negative)."""
    total = ZERO
    for symbol, qty in positions.items():
        mark = marks.get(symbol)
        if mark is not None:
            total += D(qty) * D(mark)
    return quantize_money(total)


def short_collateral(positions: list[tuple[Decimal, Decimal]], mult: Decimal) -> Decimal:
    """Reg-T-style entry-based collateral reserved against buying power for short
    positions. `positions` = [(qty, avg_entry), ...]; only qty < 0 reserves."""
    total = ZERO
    for qty, avg in positions:
        q = D(qty)
        if q < 0:
            total += -q * D(avg) * D(mult)
    return quantize_money(total)
