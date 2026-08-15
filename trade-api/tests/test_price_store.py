"""Offline coverage for the two-tier price store (Redis hot + Parquet/DuckDB).

Uses fakeredis for the hot tier and a tmp dir for the Parquet archive, so it
runs with no infra. Exercises the merge contract, dedup-by-ts, trim, Redis-first
last-bar, Parquet fallback, and crypto slash-safe paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fakeredis.aioredis as fakeredis
import pytest

import neuromancing_shared.price_store as ps

BASE = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)


def _bar(i: int, close: float) -> dict:
    return {"ts": BASE + timedelta(minutes=i), "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": 100.0 + i}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "BARDATA_DIR", str(tmp_path))
    monkeypatch.setattr(ps, "HOT_LEN", 10)
    monkeypatch.setattr(ps, "_redis", fakeredis.FakeRedis(decode_responses=True))
    # DuckDB connection is a module singleton; a fresh in-memory one is fine.
    monkeypatch.setattr(ps, "_duck", None)
    return ps


async def test_get_bars_merges_hot_and_parquet(store):
    bars = [_bar(i, 100 + i) for i in range(30)]
    store.write_parquet("1m", "AAPL", bars[:25])       # 0..24 on disk
    await store.append_hot("1m", "AAPL", bars[20:])    # 20..29 hot (5 overlap)

    got = await store.get_bars("AAPL", "1m", 30)
    ts_list = [b["ts"] for b in got]
    assert len(got) == 30
    assert ts_list == sorted(ts_list)                  # ascending
    assert len(set(ts_list)) == 30                     # deduped across tiers
    assert got[0]["close"] == 100 and got[-1]["close"] == 129


async def test_hot_only_window_skips_disk(store):
    for i in range(30):
        await store.append_hot("1m", "AAPL", [_bar(i, 100 + i)])
    small = await store.get_bars("AAPL", "1m", 5)
    assert [b["close"] for b in small] == [125, 126, 127, 128, 129]


async def test_last_bar_redis_first(store):
    store.write_parquet("1m", "AAPL", [_bar(0, 100)])
    await store.append_hot("1m", "AAPL", [_bar(9, 129)])
    last = await store.get_last_bar("AAPL", "1m")
    assert last["close"] == 129


async def test_append_hot_dedup_and_trim(store):
    for i in range(30):
        await store.append_hot("1m", "AAPL", [_bar(i, 100 + i)])
    await store.append_hot("1m", "AAPL", [_bar(29, 999)])   # same ts, new value
    assert (await store.get_last_bar("AAPL", "1m"))["close"] == 999
    assert await store._redis.zcard(store._hot_key("1m", "AAPL")) == 10


async def test_last_bar_parquet_fallback(store):
    store.write_parquet("1m", "MSFT", [_bar(i, 400 + i) for i in range(3)])
    last = await store.get_last_bar("MSFT", "1m")
    assert last is not None and last["close"] == 402


async def test_crypto_slash_safe(store, tmp_path):
    store.write_parquet("1m", "BTC/USD", [_bar(0, 115000)])
    await store.append_hot("1m", "BTC/USD", [_bar(1, 115500)])
    got = await store.get_bars("BTC/USD", "1m", 5)
    assert [b["close"] for b in got] == [115000, 115500]
    assert (tmp_path / "1m" / "BTC_USD").is_dir()


async def test_empty_symbol_returns_empty(store):
    assert await store.get_bars("NOPE", "1m", 10) == []
    assert await store.get_last_bar("NOPE", "1m") is None


async def test_get_window_span(store):
    # 30 minutes of bars in Parquet; query a sub-window [t+10, t+20)
    bars = [_bar(i, 100 + i) for i in range(30)]
    store.write_parquet("1m", "AAPL", bars)
    start = BASE + timedelta(minutes=10)
    end = BASE + timedelta(minutes=20)
    got = await store.get_window("AAPL", "1m", start, end)
    assert [b["close"] for b in got] == [110, 111, 112, 113, 114, 115, 116, 117, 118, 119]
    assert all(start <= b["ts"] < end for b in got)
