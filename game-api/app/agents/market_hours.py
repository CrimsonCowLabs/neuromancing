"""US equity market-hours awareness (NYSE), via Alpaca's calendar — which already
encodes holidays, half-days, and DST. Used to let equity-only agents sleep outside
[open - buffer, close + buffer]. Crypto trades 24/7 and is never gated.

Cached in-process (the worker is long-lived) and fail-OPEN: if the calendar can't
be fetched, agents stay active rather than silently sleeping forever.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import get_settings

log = logging.getLogger("neuromancing.market_hours")

_ET = ZoneInfo("America/New_York")
_CACHE_TTL_S = 1800  # refresh the session windows every 30 min
_BUFFER_MIN = 30

_sessions: list[tuple[datetime, datetime]] = []  # (open_utc, close_utc) per trading day
_fetched_at: datetime | None = None
_lock = asyncio.Lock()


def _client():
    from alpaca.trading.client import TradingClient

    s = get_settings()
    return TradingClient(s.alpaca_api_key, s.alpaca_api_secret, paper=True)


def _to_utc(value, day) -> datetime:
    """Alpaca returns open/close as naive ET datetimes; tolerate time or aware too."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=_ET)
    elif isinstance(value, time):
        dt = datetime.combine(day, value, tzinfo=_ET)
    else:
        raise TypeError(f"unexpected calendar time type: {type(value)}")
    return dt.astimezone(timezone.utc)


def _fetch_sessions(now: datetime) -> list[tuple[datetime, datetime]]:
    from alpaca.trading.requests import GetCalendarRequest

    today = now.astimezone(_ET).date()
    req = GetCalendarRequest(start=today - timedelta(days=2), end=today + timedelta(days=2))
    cal = _client().get_calendar(req)
    return [(_to_utc(c.open, c.date), _to_utc(c.close, c.date)) for c in cal]


async def _ensure_fresh() -> None:
    global _sessions, _fetched_at
    now = datetime.now(timezone.utc)
    if _fetched_at is not None and (now - _fetched_at).total_seconds() < _CACHE_TTL_S:
        return
    async with _lock:
        now = datetime.now(timezone.utc)
        if _fetched_at is not None and (now - _fetched_at).total_seconds() < _CACHE_TTL_S:
            return
        _sessions = await asyncio.to_thread(_fetch_sessions, now)
        _fetched_at = now
        log.info("refreshed %d market sessions from Alpaca calendar", len(_sessions))


async def recent_sessions() -> list[tuple[datetime, datetime]]:
    """The cached (open_utc, close_utc) trading sessions — used by the deep-agent
    trigger to detect 'today's session has closed'. Empty when no Alpaca key."""
    if not get_settings().alpaca_api_key:
        return []
    try:
        await _ensure_fresh()
    except Exception:  # noqa: BLE001 — fail closed to 'no sessions' (caller handles)
        return []
    return list(_sessions)


async def session_closed_today(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    for _open, _close in await recent_sessions():
        if _close.date() == now.date() and _close <= now:
            return True
    return False


async def equity_active(buffer_min: int = _BUFFER_MIN) -> bool:
    """True if now is within [open - buffer, close + buffer] of a trading session."""
    s = get_settings()
    if not s.alpaca_api_key:
        return True  # no calendar available -> fail open (don't gate)
    try:
        await _ensure_fresh()
    except Exception as e:  # noqa: BLE001 — fail open on any calendar error
        log.warning("market calendar fetch failed (%s); treating market as active", e)
        return True
    now = datetime.now(timezone.utc)
    buf = timedelta(minutes=buffer_min)
    return any(o - buf <= now <= c + buf for o, c in _sessions)
