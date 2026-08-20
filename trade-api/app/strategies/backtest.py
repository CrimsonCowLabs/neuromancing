"""Backtest harness — replays a deterministic strategy over historical bars with a
simple **long/flat OR short/flat** model, one position at a time. Produces reproducible
track-record metrics.

A per-side transaction cost (`DEFAULT_COST_BPS`, fees + slippage) is applied on every
entry and exit so results are not costlessly optimistic — this materially affects which
evolved candidates clear the adoption gate (TODO #3)."""

from __future__ import annotations

from .base import Bar
from .composed import base_timeframe
from .engine import evaluate, evaluate_multi

DEFAULT_COST_BPS = 5.0  # per side (entry AND exit); ~10bps round trip
_WARMUP = 35  # enough history for the slowest default indicator


class _Book:
    """Signed one-position book: qty > 0 long, < 0 short, 0 flat. Equity = cash + qty·price
    (a short's proceeds credit cash on open; qty·price is the negative buyback liability)."""

    def __init__(self, cash: float, cost: float):
        self.cash = cash
        self.cost = cost
        self.qty = 0.0
        self.entry = 0.0
        self.trades = 0
        self.wins = 0

    def step(self, action: str, price: float) -> None:
        c = self.cost
        if price <= 0:
            return
        if action == "buy" and self.qty == 0.0:          # open long (all-in)
            self.qty, self.entry, self.cash = (self.cash * (1 - c)) / price, price, 0.0
            self.trades += 1
        elif action in ("sell", "exit") and self.qty > 0.0:  # close long
            self.cash = self.qty * price * (1 - c)
            self.wins += 1 if price > self.entry else 0
            self.qty = 0.0
        elif action == "short" and self.qty == 0.0:      # open short (1x notional)
            n = self.cash / price
            self.cash += n * price * (1 - c)             # receive proceeds (minus cost)
            self.qty, self.entry = -n, price
            self.trades += 1
        elif action == "cover" and self.qty < 0.0:       # close short
            self.cash += self.qty * price * (1 + c)      # buy back (qty<0 → cash falls)
            self.wins += 1 if price < self.entry else 0
            self.qty = 0.0

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price


def _metrics(book: _Book, curve: list[float], n_bars: int, starting_cash: float) -> dict:
    peak, max_dd = starting_cash, 0.0
    for e in curve:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)
    final = curve[-1] if curve else starting_cash
    return {
        "bars": n_bars, "trades": book.trades,
        "win_rate": (book.wins / book.trades) if book.trades else 0.0,
        "total_return": (final - starting_cash) / starting_cash,
        "max_drawdown": max_dd, "final_equity": final,
    }


def backtest(kind: str, spec: dict, bars: list[Bar], starting_cash: float = 10000.0,
             cost_bps: float = DEFAULT_COST_BPS) -> dict:
    book = _Book(starting_cash, cost_bps / 10000.0)
    curve: list[float] = []
    for i in range(1, len(bars) + 1):
        price = bars[i - 1].close
        if i >= _WARMUP:
            book.step(evaluate(kind, spec, bars[:i]).action, price)
        curve.append(book.equity(price))
    return _metrics(book, curve, len(bars), starting_cash)


def backtest_multi(
    kind: str, spec: dict, bars_by_tf: dict[str, list[Bar]], starting_cash: float = 10000.0,
    cost_bps: float = DEFAULT_COST_BPS,
) -> dict:
    """Multi-timeframe backtest for indicator_dsl. Steps 'now' through the BASE timeframe
    only; higher-timeframe series are passed in full and the composed evaluator's as-of
    filter drops any bar not yet closed — so there is NO lookahead. Same signed model."""
    base_tf = base_timeframe(spec)
    base = bars_by_tf.get(base_tf) or []
    higher = {tf: bb for tf, bb in bars_by_tf.items() if tf != base_tf}
    book = _Book(starting_cash, cost_bps / 10000.0)
    curve: list[float] = []
    for i in range(1, len(base) + 1):
        price = base[i - 1].close
        if i >= _WARMUP:
            window = {base_tf: base[:i], **higher}  # higher tfs filtered as-of by composed
            book.step(evaluate_multi(kind, spec, window).action, price)
        curve.append(book.equity(price))
    return _metrics(book, curve, len(base), starting_cash)
