"""Two-tier price store: Redis hot cache + DuckDB-over-Parquet historical archive.

- **Redis hot** (`bars:{tf}:{sym}` sorted set, score = epoch seconds, member = JSON
  bar): the most recent `price_hot_len` bars per (timeframe, symbol). Deduped by
  `ts`; the live read path never touches disk.
- **Parquet archive** (`{bardata_dir}/{tf}/{sym}/{yyyy-mm-dd}.parquet`): closed
  history, written **atomically** (temp + os.replace) by the SINGLE ingest writer.
- **DuckDB** (reused in-memory connection, read-only over the Parquet glob): deep
  history / backtest scans.

Bars are plain dicts: {ts: datetime(UTC), open, high, low, close, volume: float}.
Callers (e.g. trade-api's `Bar`) adapt as needed. See docs/08-market-data.md.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from .redisio import make_redis
from .settings import BaseServiceSettings

log = logging.getLogger("neuromancing.price_store")

_settings = BaseServiceSettings()
_redis = make_redis(_settings.redis_url)
BARDATA_DIR = _settings.bardata_dir
HOT_LEN = _settings.price_hot_len
QUOTE_TTL_S = _settings.quote_ttl_s

_BAR_COLS = ("open", "high", "low", "close", "volume")


# ------------------------------------------------------------------ helpers
def _hot_key(tf: str, sym: str) -> str:
    return f"bars:{tf}:{sym}"


def _bar_json(bar: dict) -> str:
    ts: datetime = bar["ts"]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return json.dumps({
        "ts": ts.astimezone(timezone.utc).isoformat(),
        **{c: float(bar[c]) for c in _BAR_COLS},
    })


def _parse_bar(raw: str) -> dict:
    d = json.loads(raw)
    ts = datetime.fromisoformat(d["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return {"ts": ts, **{c: float(d[c]) for c in _BAR_COLS}}


def _epoch(ts: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


def _naive_utc(ts: datetime) -> datetime:
    """Drop to naive-UTC (how bar ts is stored in Parquet)."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc)
    return ts.replace(tzinfo=None)


def _dedup_sort(bars: list[dict]) -> list[dict]:
    """Dedup by ts (later entries win) and return ascending."""
    by_ts: dict[float, dict] = {}
    for b in bars:
        by_ts[_epoch(b["ts"])] = b
    return [by_ts[k] for k in sorted(by_ts)]


# ------------------------------------------------------------------ Redis hot
async def append_hot(tf: str, sym: str, bars: list[dict]) -> None:
    """Upsert bars into the Redis hot cache (dedup by ts, trim to HOT_LEN)."""
    if not bars:
        return
    key = _hot_key(tf, sym)
    pipe = _redis.pipeline()
    for b in bars:
        score = _epoch(b["ts"])
        pipe.zremrangebyscore(key, score, score)  # dedup this ts
        pipe.zadd(key, {_bar_json(b): score})
    pipe.zremrangebyrank(key, 0, -(HOT_LEN + 1))  # keep newest HOT_LEN
    pipe.expire(key, 60 * 60 * 24 * 3)
    await pipe.execute()


async def _get_hot(tf: str, sym: str, limit: int) -> list[dict]:
    raws = await _redis.zrevrange(_hot_key(tf, sym), 0, max(0, limit - 1))
    return _dedup_sort([_parse_bar(r) for r in raws])


async def write_quote(
    symbol: str, *, bid: float | None, ask: float | None, last: float,
    ts: datetime | None = None,
) -> None:
    """Latest-quote hot cache (`quote:{symbol}`). Single source for the quote
    contract; the matcher's own >90s staleness check still governs fills."""
    ts = ts or datetime.now(timezone.utc)
    payload = {
        "bid": None if bid is None else str(bid),
        "ask": None if ask is None else str(ask),
        "last": str(last),
        "ts": ts.isoformat(),
    }
    await _redis.set(f"quote:{symbol}", json.dumps(payload), ex=QUOTE_TTL_S)


# ------------------------------------------------------------------ Parquet + DuckDB
_duck_lock = threading.Lock()
_duck = None


def _duckdb():
    global _duck
    if _duck is None:
        import duckdb

        _duck = duckdb.connect(database=":memory:")
    return _duck


def _glob(tf: str, sym: str) -> str:
    # Directory-per-symbol so a query only ever reads that symbol's files.
    return os.path.join(BARDATA_DIR, tf, _safe(sym), "*.parquet")


def _safe(sym: str) -> str:
    return sym.replace("/", "_")  # BTC/USD -> BTC_USD for the filesystem


def _read_parquet(
    tf: str, sym: str, before: datetime | None, limit: int,
    since: datetime | None = None,
) -> list[dict]:
    import glob as _g

    if not _g.glob(_glob(tf, sym)):
        return []
    # Parquet ts is stored naive-UTC; bind naive to avoid DuckDB's pytz path.
    clauses: list[str] = []
    params: list = [_glob(tf, sym)]
    if before is not None:
        clauses.append("ts < ?")
        params.append(_naive_utc(before))
    if since is not None:
        clauses.append("ts >= ?")
        params.append(_naive_utc(since))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT ts, open, high, low, close, volume "
        f"FROM read_parquet(?) {where} ORDER BY ts DESC LIMIT {int(limit)}"
    )
    with _duck_lock:
        rows = _duckdb().execute(sql, params).fetchall()
    out = [
        {"ts": (r[0].replace(tzinfo=timezone.utc) if r[0].tzinfo is None
                else r[0].astimezone(timezone.utc)),
         "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
         "close": float(r[4]), "volume": float(r[5])}
        for r in rows
    ]
    out.reverse()  # ascending
    return out


def write_parquet(tf: str, sym: str, bars: list[dict]) -> None:
    """Merge bars into per-day Parquet files (dedup by ts), atomically. Only the
    single ingest writer calls this."""
    if not bars:
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    by_day: dict[str, list[dict]] = {}
    for b in bars:
        ts = b["ts"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        by_day.setdefault(ts.astimezone(timezone.utc).strftime("%Y-%m-%d"), []).append(b)

    dir_ = os.path.join(BARDATA_DIR, tf, _safe(sym))
    os.makedirs(dir_, exist_ok=True)
    for day, day_bars in by_day.items():
        path = os.path.join(dir_, f"{day}.parquet")
        existing: list[dict] = []
        if os.path.exists(path):
            t = pq.read_table(path)
            existing = t.to_pylist()
        merged = _dedup_sort(existing + day_bars)
        table = pa.Table.from_pylist([
            {"ts": _naive_utc(b["ts"]), **{c: float(b[c]) for c in _BAR_COLS}}
            for b in merged
        ])
        tmp = f"{path}.tmp.{os.getpid()}"
        pq.write_table(table, tmp)
        os.replace(tmp, path)  # atomic


# ------------------------------------------------------------------ public read API
async def get_bars(symbol: str, timeframe: str = "1m", limit: int = 200) -> list[dict]:
    """Recent bars from Redis hot; deeper windows fall back to Parquet and merge
    (deduped by ts). Returns ascending."""
    hot = await _get_hot(timeframe, symbol, limit)
    if len(hot) >= limit:
        return hot
    earliest = hot[0]["ts"] if hot else None
    older = _read_parquet(timeframe, symbol, before=earliest, limit=limit - len(hot) + 50)
    return _dedup_sort(older + hot)[-limit:]


async def get_last_bar(symbol: str, timeframe: str = "1m") -> dict | None:
    """Latest bar — Redis-first (hot path), Parquet only on miss."""
    hot = await _get_hot(timeframe, symbol, 1)
    if hot:
        return hot[-1]
    older = _read_parquet(timeframe, symbol, before=None, limit=1)
    return older[-1] if older else None


async def get_window(
    symbol: str, timeframe: str, start: datetime, end: datetime, limit: int = 20000
) -> list[dict]:
    """Bars in the calendar span [start, end), ascending — for backtest walk-forward
    windows. Reads Parquet (history) and merges any overlapping hot bars, deduped by
    ts. Used by the ad-hoc backtest path (TODO #3)."""
    older = _read_parquet(timeframe, symbol, before=end, limit=limit, since=start)
    hot = [b for b in await _get_hot(timeframe, symbol, HOT_LEN)
           if start <= b["ts"] < end]
    return _dedup_sort(older + hot)


async def ingest_bars(
    symbol: str, timeframe: str, bars: list[dict], *, archive: bool = False
) -> None:
    """Single ingest write path: always update the Redis hot cache; when
    ``archive`` (backfill), also persist straight to Parquet. Live polling passes
    ``archive=False`` and relies on the periodic flush job for durability, so the
    live path never touches disk."""
    await append_hot(timeframe, symbol, bars)
    if archive:
        write_parquet(timeframe, symbol, bars)


async def flush_to_parquet(symbol: str, timeframe: str) -> int:
    """Persist the current hot cache for (symbol, timeframe) into the Parquet
    archive. Called periodically by the ingest flush job. Returns bars written."""
    bars = await _get_hot(timeframe, symbol, HOT_LEN)
    write_parquet(timeframe, symbol, bars)
    return len(bars)


async def hot_series() -> list[tuple[str, str]]:
    """Enumerate (timeframe, symbol) pairs currently in the Redis hot cache."""
    out: list[tuple[str, str]] = []
    async for key in _redis.scan_iter(match="bars:*"):
        parts = key.split(":", 2)  # bars:{tf}:{sym} (sym may contain '/')
        if len(parts) == 3:
            out.append((parts[1], parts[2]))
    return out


async def flush_all() -> int:
    """Flush every hot series to Parquet. The periodic durability job in
    market-ingest (the single writer) calls this."""
    n = 0
    for tf, sym in await hot_series():
        n += await flush_to_parquet(sym, tf)
    return n
