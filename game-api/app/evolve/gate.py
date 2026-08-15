"""The deterministic adoption gate — the last, unit-tested safety check.

A candidate is adopted only if it beats the incumbent out-of-sample on BOTH
walk-forward windows by a margin, has enough trades, and stays under the drawdown
cap. This is the guardrail on the LLM's creativity (same philosophy as the trading
guardrails): the model proposes, deterministic code decides.
"""

from __future__ import annotations

from dataclasses import dataclass

# Per-window aggregate metric shape: {"total_return", "trades", "max_drawdown"}
WINDOWS = ("w1", "w2")


@dataclass(frozen=True)
class GateConfig:
    return_margin: float
    min_trades: int
    max_dd: float


def should_adopt(incumbent: dict, candidate: dict, cfg: GateConfig) -> tuple[bool, str]:
    """incumbent/candidate: {"w1": {total_return, trades, max_drawdown}, "w2": {...}}.
    Returns (adopt, reason)."""
    for w in WINDOWS:
        c = candidate.get(w) or {}
        i = incumbent.get(w) or {}
        trades = int(c.get("trades", 0))
        if trades < cfg.min_trades:
            return False, f"too few trades in {w} ({trades} < {cfg.min_trades})"
        dd = float(c.get("max_drawdown", 1.0))
        if dd > cfg.max_dd:
            return False, f"drawdown too high in {w} ({dd:.2%} > {cfg.max_dd:.2%})"
        edge = float(c.get("total_return", -9.0)) - float(i.get("total_return", 0.0))
        if edge < cfg.return_margin:
            return False, f"no edge in {w} (Δreturn {edge:+.2%} < {cfg.return_margin:+.2%})"
    return True, "beats incumbent on both windows by margin"
