"""Timeframe helpers: map our string timeframes to Alpaca TimeFrame objects.

Each timeframe is fetched NATIVELY from Alpaca (never derived from 1m) so live
and backtest bars are session-aligned and identical. See docs/08-market-data.md.
"""

from __future__ import annotations


def alpaca_tf(timeframe: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    table = {
        "1m": (1, TimeFrameUnit.Minute),
        "5m": (5, TimeFrameUnit.Minute),
        "1h": (1, TimeFrameUnit.Hour),
        "1d": (1, TimeFrameUnit.Day),
    }
    if timeframe not in table:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    amount, unit = table[timeframe]
    return TimeFrame(amount, unit)


def tf_seconds(timeframe: str) -> int:
    return {"1m": 60, "5m": 300, "1h": 3600, "1d": 86400}[timeframe]
