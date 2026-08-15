"""Simulated matching engine — pure pricing/slippage/crossing logic.

Deterministic and side-effect free so it is exhaustively unit/property testable.
The SimBroker calls these to price fills; a resting-order poller calls
`limit_stop_crosses` to decide when a resting order fills.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from neuromancing_shared.money import D, quantize_money, quantize_qty

from ..prices import Quote

# Slippage tiers by order notional (USD) -> basis points, applied adverse to the
# taker. Bigger orders move the (simulated) market more.
_SLIPPAGE_TIERS: list[tuple[Decimal, Decimal]] = [
    (D("1000"), D("1")),
    (D("10000"), D("3")),
    (D("100000"), D("8")),
]
_SLIPPAGE_TOP_BPS = D("15")

# Simulated fees in bps per side, by asset class.
_FEE_BPS = {"equity": D("0"), "crypto": D("10")}

_BPS = D("10000")


def slippage_bps_for(notional: Decimal) -> Decimal:
    for threshold, bps in _SLIPPAGE_TIERS:
        if notional < threshold:
            return bps
    return _SLIPPAGE_TOP_BPS


def fee_bps_for(asset_class: str) -> Decimal:
    return _FEE_BPS.get(asset_class, D("0"))


@dataclass(frozen=True)
class FillQuote:
    qty: Decimal
    price: Decimal
    slippage_bps: Decimal
    fees: Decimal
    notional: Decimal  # signed-neutral gross notional at fill price (qty*price)


def compute_fill(
    *,
    quote: Quote,
    side: str,
    asset_class: str,
    qty: Decimal | None = None,
    notional: Decimal | None = None,
) -> FillQuote:
    """Price a market fill against a quote.

    Exactly one of qty / notional must be given. Notional orders convert to a
    fractional qty at the slipped fill price. Slippage is adverse: buys fill
    higher, sells fill lower.
    """
    if (qty is None) == (notional is None):
        raise ValueError("provide exactly one of qty or notional")
    if side not in ("buy", "sell"):
        raise ValueError(f"bad side: {side}")

    ref = quote.side_price(side)
    if ref <= 0:
        raise ValueError("non-positive reference price")

    # Size drives the slippage tier — use the requested notional (or qty*ref).
    est_notional = notional if notional is not None else D(qty) * ref
    slip_bps = slippage_bps_for(est_notional)
    slip = slip_bps / _BPS
    fill_price = ref * (D(1) + slip) if side == "buy" else ref * (D(1) - slip)
    fill_price = quantize_money(fill_price)

    if qty is None:
        fill_qty = quantize_qty(D(notional) / fill_price)
    else:
        fill_qty = quantize_qty(qty)

    gross = quantize_money(fill_qty * fill_price)
    fee = quantize_money(gross * (fee_bps_for(asset_class) / _BPS))
    return FillQuote(
        qty=fill_qty,
        price=fill_price,
        slippage_bps=slip_bps,
        fees=fee,
        notional=gross,
    )


def limit_stop_crosses(
    *, order_type: str, side: str, limit_price, stop_price, quote: Quote
) -> bool:
    """Should a resting limit/stop order fill given the current quote?"""
    ref = quote.side_price(side)
    lp = D(limit_price) if limit_price is not None else None
    sp = D(stop_price) if stop_price is not None else None
    if order_type == "limit":
        return (side == "buy" and ref <= lp) or (side == "sell" and ref >= lp)
    if order_type == "stop":
        # Stop triggers when price moves through the stop, then fills at market.
        return (side == "buy" and ref >= sp) or (side == "sell" and ref <= sp)
    if order_type == "stop_limit":
        triggered = (side == "buy" and ref >= sp) or (side == "sell" and ref <= sp)
        if not triggered:
            return False
        return (side == "buy" and ref <= lp) or (side == "sell" and ref >= lp)
    return False


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str | None
    high_water: Decimal  # ratcheted (monotonic non-decreasing) high-water price


def evaluate_exit(
    *,
    avg_entry,
    last,
    high_water=None,
    stop_loss_pct=None,
    take_profit_pct=None,
    trailing_stop_pct=None,
) -> ExitDecision:
    """Pure deterministic exit decision for a long position given the latest price.

    Ratchets the trailing high-water monotonically, then checks the *tighter* of
    the fixed stop and the trailing stop, then the take-profit. Returns whether to
    exit, why, and the updated high-water (which the caller persists).
    """
    avg_entry = D(avg_entry)
    last = D(last)
    hw = D(high_water) if high_water is not None else avg_entry
    if last > hw:  # ratchet — never decreases
        hw = last

    stops: list[tuple[Decimal, str]] = []
    if stop_loss_pct:
        stops.append((avg_entry * (D(1) - D(stop_loss_pct)), "stop_loss"))
    if trailing_stop_pct:
        stops.append((hw * (D(1) - D(trailing_stop_pct)), "trailing_stop"))
    if stops:
        eff_price, reason = max(stops, key=lambda s: s[0])  # tighter (higher) stop wins
        if last <= eff_price:
            return ExitDecision(True, reason, hw)

    if take_profit_pct:
        if last >= avg_entry * (D(1) + D(take_profit_pct)):
            return ExitDecision(True, "take_profit", hw)

    return ExitDecision(False, None, hw)
