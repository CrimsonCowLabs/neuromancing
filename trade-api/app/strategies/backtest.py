"""Backtest **accounting machinery** — the P&L-spine adapter the equity `Strategy`
kinds replay over historical bars with a simple **long/flat OR short/flat** model, one
position at a time. Produces reproducible track-record metrics.

The harness books every fill through the **live P&L spine** (`apply_fill` in
`app.engine.portfolio`) — the same signed, Decimal, fee-aware, no-flip authority the
SimBroker uses for real fills — and replays the **live exit settlement**
(`evaluate_exit`) on each bar. So a backtest and a live position share ONE accounting:
there is no separate hand-rolled transition math to drift from the engine, and a
strategy that stops out live also stops out in the backtest.

The adapter (`_Book`) owns only what the spine does not: **sizing** (a fixed notional
fraction of starting cash), a **per-side cost** (`cost_bps`, applied on every entry and
exit so churn is penalized), the **equity curve**, and the **exit replay**. Scope is
gate-first / rank-faithful: mechanisms are modeled only when their absence changes which
strategy the adoption gate (TODO #3) would pick — hence exit settlement and churn cost,
but not live slippage tiers or quote-based fills.

This module is dispatch-free: it exposes `ExitConfig` + `replay` (the shared step loop);
the equity strategy kinds in `interface.py` drive it with their own per-bar signal. The
whole evaluation surface (which kind, which timeframes, evaluate vs backtest) lives behind
the `Strategy` interface, not here.

Accounting is Decimal end-to-end (matching the spine); indicator math stays float for
speed and the O(n²) indicator recompute dominates the hot loop, so Decimal is free here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from neuromancing_shared.money import ZERO, D, quantize_money, quantize_qty

from ..engine.matching import evaluate_exit
from ..engine.portfolio import PositionState, apply_fill

DEFAULT_COST_BPS = 5.0        # per side (entry AND exit); ~10bps round trip
DEFAULT_ALLOC_PCT = 0.20      # fixed notional per entry = 20% of STARTING cash
DEFAULT_STOP_LOSS_PCT = 0.08  # default stop applied when a caller passes no stop
WARMUP = 35                   # enough history for the slowest default indicator


@dataclass(frozen=True)
class ExitConfig:
    """Deterministic exit discipline replayed on each bar, mirroring what a live position
    carries. A stop is mandatory: an unset (or 0) `stop_loss_pct` defaults to
    `DEFAULT_STOP_LOSS_PCT` so a bare backtest always measures under live-like protection —
    a caller's own stop (tighter or looser) is honored as given. Shorts mirror automatically
    inside `evaluate_exit` via the position's qty sign."""

    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None

    def __post_init__(self) -> None:
        if not self.stop_loss_pct:
            object.__setattr__(self, "stop_loss_pct", DEFAULT_STOP_LOSS_PCT)


class _Book:
    """Thin adapter over the P&L spine. Holds one position at a time (long/flat OR
    short/flat) and delegates every cash/position/realized-pnl transition to
    `apply_fill`. Owns only sizing, per-side cost, the exit replay, and win/trade counts."""

    def __init__(self, starting_cash: float, alloc_pct: float, cost_bps: float,
                 exit_config: ExitConfig):
        self.cash: Decimal = D(starting_cash)
        self.pos = PositionState(ZERO, ZERO)
        self.alloc_notional: Decimal = quantize_money(D(starting_cash) * D(alloc_pct))
        self.cost: Decimal = D(cost_bps) / D(10000)  # per-side fraction
        self.exit = exit_config
        self.high_water: Decimal | None = None  # favorable extreme since entry (max long / min short)
        self.trades = 0
        self.wins = 0

    def equity(self, price: float) -> Decimal:
        """Signed mark-to-market: cash + qty·price (a short's qty<0 is the buyback liability)."""
        return self.cash + self.pos.qty * D(price)

    def _fees(self, qty: Decimal, price: Decimal) -> Decimal:
        return quantize_money(abs(qty) * price * self.cost)

    def _open(self, side: str, price: float) -> None:
        p = D(price)
        if p <= 0:
            return
        notional = self.alloc_notional
        if side == "buy":  # a long spends cash — never book what a drawdown can't afford
            notional = min(notional, self.cash)
        if notional <= 0:
            return
        qty = quantize_qty(notional / p)
        if qty <= 0:
            return
        out = apply_fill(cash=self.cash, position=self.pos, side=side, qty=qty,
                         price=p, fees=self._fees(qty, p))
        self.cash, self.pos = out.cash, out.position
        self.high_water = p
        self.trades += 1

    def _close(self, price: float) -> None:
        p = D(price)
        qty = self.pos.qty
        if qty == 0 or p <= 0:
            return
        side = "sell" if qty > 0 else "buy"  # close long / cover short
        out = apply_fill(cash=self.cash, position=self.pos, side=side, qty=abs(qty),
                         price=p, fees=self._fees(qty, p))
        self.cash, self.pos = out.cash, out.position
        self.wins += 1 if out.realized_pnl > 0 else 0  # spine's realized sign — fee/short aware
        self.high_water = None

    def settle_exit(self, price: float) -> None:
        """Exit-first: replay the live stop/take-profit/trailing decision against the open
        position BEFORE the strategy signal, so a position a live stop would already have
        closed cannot be rescued by a later signal on the same bar. Threads the ratcheted
        high-water across bars."""
        if self.pos.qty == 0:
            return
        d = evaluate_exit(
            avg_entry=self.pos.avg_entry, last=price, qty=self.pos.qty,
            high_water=self.high_water,
            stop_loss_pct=self.exit.stop_loss_pct,
            take_profit_pct=self.exit.take_profit_pct,
            trailing_stop_pct=self.exit.trailing_stop_pct,
        )
        self.high_water = d.high_water
        if d.should_exit:
            self._close(price)

    def step(self, action: str, price: float) -> None:
        """Apply a strategy signal under the one-position model. Only `buy` opens a long,
        only `short` opens a short; a bare `sell` while flat is a no-op (never a flip)."""
        qty = self.pos.qty
        if action == "buy" and qty == 0:
            self._open("buy", price)
        elif action in ("sell", "exit") and qty > 0:
            self._close(price)
        elif action == "short" and qty == 0:
            self._open("sell", price)
        elif action == "cover" and qty < 0:
            self._close(price)


def _metrics(book: _Book, curve: list[Decimal], n_bars: int, starting_cash: Decimal) -> dict:
    peak, max_dd = starting_cash, ZERO
    for e in curve:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd
    final = curve[-1] if curve else starting_cash
    return {
        "bars": n_bars,
        "trades": book.trades,
        "win_rate": (book.wins / book.trades) if book.trades else 0.0,
        "total_return": float((final - starting_cash) / starting_cash),
        "max_drawdown": float(max_dd),
        "final_equity": float(final),
    }


def replay(n_bars: int, price_at, action_at, *, starting_cash: float, alloc_pct: float,
           cost_bps: float, exit_config: ExitConfig) -> dict:
    """Shared accounting + exit-replay loop for the single- and multi-timeframe paths, so a
    multi-tf strategy and a single-tf strategy are booked identically. `price_at(i)` gives
    the bar-i close; `action_at(i)` gives the strategy action for bar i (None during warmup)."""
    book = _Book(starting_cash, alloc_pct, cost_bps, exit_config)
    curve: list[Decimal] = []
    for i in range(n_bars):
        price = price_at(i)
        book.settle_exit(price)          # exit-first, before the signal
        action = action_at(i)
        if action is not None:
            book.step(action, price)
        curve.append(book.equity(price))
    return _metrics(book, curve, n_bars, D(starting_cash))
