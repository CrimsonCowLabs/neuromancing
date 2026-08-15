"""Deterministic technical indicators (pure functions).

Two layers:
- **close-list fns** (`sma/ema/rsi/roc`, `INDICATORS`) — the original registry the
  legacy `rule_dsl` + `signal_fn` kinds use; unchanged.
- **bar-aware layer** (`compute_indicator`) — used by the `indicator_dsl` composed
  engine. Derives a series from `Bar`s by `source` (close/open/high/low/hlc3/volume)
  and adds `macd`, `bollinger`/`bbpercent` (scalar %B), `atr` (OHLC), `vwap` (OHLCV).
  Multi-valued indicators (`macd`, `bollinger`) return a dict; a condition selects a
  `field` (see `scalar_value`).
"""

from __future__ import annotations


def sma(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def ema(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) < n + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-n, 0):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / n
    avg_loss = losses / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def roc(values: list[float], n: int) -> float | None:
    """Rate of change over n bars, as a fraction (0.05 == +5%)."""
    if len(values) < n + 1 or values[-n - 1] == 0:
        return None
    return (values[-1] - values[-n - 1]) / values[-n - 1]


INDICATORS = {"sma": sma, "ema": ema, "rsi": rsi, "roc": roc}


# --------------------------------------------------------------------------
# Bar-aware layer for the indicator_dsl composed engine.
# The grammar vocabulary (SOURCES/KNOWN_FNS/DICT_FIELDS) is owned by
# neuromancing_shared.strategy_spec; imported here so implementations can't drift.
# --------------------------------------------------------------------------

from neuromancing_shared.strategy_spec import (  # noqa: E402,F401
    DICT_FIELDS,
    KNOWN_FNS,
    SOURCES,
)

# Internal implementation groupings (which fns need full bars vs a source series).
_SERIES_FNS = ("sma", "ema", "rsi", "roc", "macd", "bollinger", "bbpercent")
_BAR_FNS = ("atr", "vwap")
assert set(_SERIES_FNS) | set(_BAR_FNS) == set(KNOWN_FNS)  # keep in sync with the grammar


def series_from_bars(bars: list, source: str = "close") -> list[float]:
    if source == "hlc3":
        return [(b.high + b.low + b.close) / 3 for b in bars]
    return [float(getattr(b, source)) for b in bars]


def _ema_series(values: list[float], n: int) -> list[float | None]:
    """EMA at each index (SMA-seeded); first n-1 entries are None."""
    if n <= 0 or len(values) < n:
        return [None] * len(values)
    k = 2.0 / (n + 1)
    out: list[float | None] = [None] * (n - 1)
    e = sum(values[:n]) / n
    out.append(e)
    for v in values[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if fast >= slow or len(values) < slow:
        return None
    ef, es = _ema_series(values, fast), _ema_series(values, slow)
    line = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)]
    valid = [x for x in line if x is not None]
    if len(valid) < signal:
        return None
    sig = _ema_series(valid, signal)[-1]
    if sig is None:
        return None
    last = valid[-1]
    return {"line": last, "signal": sig, "hist": last - sig}


def bollinger(values: list[float], period: int = 20, mult: float = 2.0) -> dict | None:
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    mid = sum(window) / period
    sd = (sum((x - mid) ** 2 for x in window) / period) ** 0.5
    upper, lower = mid + mult * sd, mid - mult * sd
    last = values[-1]
    pct = (last - lower) / (upper - lower) if upper != lower else 0.5
    return {"upper": upper, "middle": mid, "lower": lower, "percent": pct}


def bbpercent(values: list[float], period: int = 20, mult: float = 2.0) -> float | None:
    b = bollinger(values, period, mult)
    return b["percent"] if b else None


def atr(bars: list, period: int = 14) -> float | None:
    """Average True Range over `period` bars (SMA of true range; deterministic)."""
    if period <= 0 or len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        h, low, pc = float(bars[i].high), float(bars[i].low), float(bars[i - 1].close)
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def vwap(bars: list, period: int = 20) -> float | None:
    """Rolling VWAP over `period` bars using typical price (deterministic)."""
    if period <= 0 or len(bars) < period:
        return None
    window = bars[-period:]
    num = sum(((float(b.high) + float(b.low) + float(b.close)) / 3) * float(b.volume) for b in window)
    den = sum(float(b.volume) for b in window)
    return num / den if den else None


def compute_indicator(ind: dict, bars: list) -> float | dict | None:
    """Evaluate one indicator spec over bars → scalar or dict (or None if short)."""
    fn = ind["fn"]
    if fn in _BAR_FNS:
        p = int(ind.get("period", 14 if fn == "atr" else 20))
        return atr(bars, p) if fn == "atr" else vwap(bars, p)
    series = series_from_bars(bars, ind.get("source", "close"))
    if fn == "sma" or fn == "ema":
        return (sma if fn == "sma" else ema)(series, int(ind["period"]))
    if fn == "rsi":
        return rsi(series, int(ind.get("period", 14)))
    if fn == "roc":
        return roc(series, int(ind["period"]))
    if fn == "macd":
        return macd(series, int(ind.get("fast", 12)), int(ind.get("slow", 26)),
                    int(ind.get("signal", 9)))
    if fn == "bollinger":
        return bollinger(series, int(ind.get("period", 20)), float(ind.get("mult", 2.0)))
    if fn == "bbpercent":
        return bbpercent(series, int(ind.get("period", 20)), float(ind.get("mult", 2.0)))
    raise ValueError(f"unknown indicator fn: {fn}")


def scalar_value(value: float | dict | None, field: str | None) -> float | None:
    """Resolve a scalar from an indicator result: dict-valued indicators need a field."""
    if value is None:
        return None
    if isinstance(value, dict):
        return None if field is None else value.get(field)
    return float(value)
