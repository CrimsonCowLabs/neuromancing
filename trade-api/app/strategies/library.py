"""House signal-function strategies (deterministic). Each takes the close-price
series + a params dict and returns a Signal.

All three are EVENT-DRIVEN: they fire only on the *transition* into their
condition (comparing the current bar's indicator against the previous bar's),
not on every tick the condition holds. This stops a persistent state (e.g. RSI
staying oversold) from re-emitting the same signal every tick — which otherwise
invokes the LLM manager needlessly. See docs/05-trading-system.md and the
actionability filter in game-api's context builder (docs/04-decision-tick.md)."""

from __future__ import annotations

from .base import HOLD, Signal
from .indicators import roc, rsi, sma


def sma_crossover(closes: list[float], spec: dict) -> Signal:
    fast_n = int(spec.get("fast", 10))
    slow_n = int(spec.get("slow", 30))
    if len(closes) < slow_n + 1:
        return HOLD
    fast_now, slow_now = sma(closes, fast_n), sma(closes, slow_n)
    fast_prev, slow_prev = sma(closes[:-1], fast_n), sma(closes[:-1], slow_n)
    if None in (fast_now, slow_now, fast_prev, slow_prev):
        return HOLD
    feats = {"fast": fast_now, "slow": slow_now}
    spread = abs(fast_now - slow_now) / slow_now if slow_now else 0.0
    strength = max(0.0, min(1.0, spread * 20))
    if fast_prev <= slow_prev and fast_now > slow_now:
        return Signal("buy", strength, feats)
    if fast_prev >= slow_prev and fast_now < slow_now:
        return Signal("exit", strength, feats)
    return HOLD


def rsi_reversion(closes: list[float], spec: dict) -> Signal:
    period = int(spec.get("period", 14))
    low = float(spec.get("low", 30))
    high = float(spec.get("high", 70))
    r = rsi(closes, period)
    r_prev = rsi(closes[:-1], period)
    if r is None or r_prev is None:
        return HOLD
    feats = {"rsi": r}
    # Fire only on the crossing INTO the zone (not every tick it stays there).
    if r_prev >= low and r < low:
        return Signal("buy", min(1.0, (low - r) / low), feats)
    if r_prev <= high and r > high:
        return Signal("exit", min(1.0, (r - high) / (100 - high)), feats)
    return HOLD


def momentum(closes: list[float], spec: dict) -> Signal:
    lookback = int(spec.get("lookback", 20))
    threshold = float(spec.get("threshold", 0.03))
    r = roc(closes, lookback)
    r_prev = roc(closes[:-1], lookback)
    if r is None or r_prev is None:
        return HOLD
    feats = {"roc": r}
    # Fire only when momentum crosses through the threshold, not while it persists.
    if r_prev <= threshold and r > threshold:
        return Signal("buy", min(1.0, r / (threshold * 5)), feats)
    if r_prev >= -threshold and r < -threshold:
        return Signal("exit", min(1.0, -r / (threshold * 5)), feats)
    return HOLD


SIGNAL_FNS = {
    "sma_crossover": sma_crossover,
    "rsi_reversion": rsi_reversion,
    "momentum": momentum,
}
