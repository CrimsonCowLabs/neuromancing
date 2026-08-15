"""Backtest harness — replays a deterministic strategy over historical bars with
a simple long/flat model. Produces reproducible track-record metrics that back
the sellable strategies (PDFs / marketplace).

A per-side transaction cost (`DEFAULT_COST_BPS`, fees + slippage) is applied on
every entry and exit so results are not costlessly optimistic — this materially
affects which evolved candidates clear the adoption gate (TODO #3)."""

from __future__ import annotations

from .base import Bar
from .composed import base_timeframe
from .engine import evaluate, evaluate_multi

DEFAULT_COST_BPS = 5.0  # per side (entry AND exit); ~10bps round trip


def backtest(kind: str, spec: dict, bars: list[Bar], starting_cash: float = 10000.0,
             cost_bps: float = DEFAULT_COST_BPS) -> dict:
    cost = cost_bps / 10000.0
    cash = starting_cash
    qty = 0.0
    entry = 0.0
    trades = 0
    wins = 0
    equity_curve: list[float] = []
    peak = starting_cash
    max_dd = 0.0
    warmup = 35  # enough history for the slowest default indicator

    for i in range(1, len(bars) + 1):
        window = bars[:i]
        price = window[-1].close
        if i >= warmup:
            sig = evaluate(kind, spec, window)
            if sig.action == "buy" and qty == 0.0 and price > 0:
                qty = (cash * (1 - cost)) / price  # pay entry cost
                entry = price
                cash = 0.0
                trades += 1
            elif sig.action in ("sell", "exit") and qty > 0.0:
                cash = qty * price * (1 - cost)  # pay exit cost
                if price > entry:
                    wins += 1
                qty = 0.0
        equity = cash + qty * price
        equity_curve.append(equity)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    final_equity = equity_curve[-1] if equity_curve else starting_cash
    return {
        "bars": len(bars),
        "trades": trades,
        "win_rate": (wins / trades) if trades else 0.0,
        "total_return": (final_equity - starting_cash) / starting_cash,
        "max_drawdown": max_dd,
        "final_equity": final_equity,
    }


def backtest_multi(
    kind: str, spec: dict, bars_by_tf: dict[str, list[Bar]], starting_cash: float = 10000.0,
    cost_bps: float = DEFAULT_COST_BPS,
) -> dict:
    """Multi-timeframe backtest for indicator_dsl. Steps 'now' through the BASE
    timeframe only; higher-timeframe series are passed in full and the composed
    evaluator's as-of filter drops any bar not yet closed — so there is NO
    lookahead. Same long/flat model + metrics as `backtest`."""
    base_tf = base_timeframe(spec)
    base = bars_by_tf.get(base_tf) or []
    higher = {tf: bb for tf, bb in bars_by_tf.items() if tf != base_tf}
    cost = cost_bps / 10000.0

    cash, qty, entry = starting_cash, 0.0, 0.0
    trades = wins = 0
    equity_curve: list[float] = []
    peak, max_dd = starting_cash, 0.0
    warmup = 35

    for i in range(1, len(base) + 1):
        window = {base_tf: base[:i], **higher}  # higher tfs filtered as-of by composed
        price = base[i - 1].close
        if i >= warmup:
            sig = evaluate_multi(kind, spec, window)
            if sig.action == "buy" and qty == 0.0 and price > 0:
                qty, entry, cash, trades = (cash * (1 - cost)) / price, price, 0.0, trades + 1
            elif sig.action in ("sell", "exit") and qty > 0.0:
                cash = qty * price * (1 - cost)
                wins += 1 if price > entry else 0
                qty = 0.0
        equity = cash + qty * price
        equity_curve.append(equity)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    final_equity = equity_curve[-1] if equity_curve else starting_cash
    return {
        "bars": len(base),
        "trades": trades,
        "win_rate": (wins / trades) if trades else 0.0,
        "total_return": (final_equity - starting_cash) / starting_cash,
        "max_drawdown": max_dd,
        "final_equity": final_equity,
    }
