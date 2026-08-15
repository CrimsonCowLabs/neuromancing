"""Core types for the deterministic strategy engine.

Signals are NOT money — indicator math runs in float for speed. Everything here
is pure (inputs -> Signal), so strategies are fully backtestable and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Signal:
    action: str  # buy | sell | hold | exit
    strength: float = 0.0  # 0..1 conviction
    features: dict = field(default_factory=dict)


HOLD = Signal(action="hold", strength=0.0)
