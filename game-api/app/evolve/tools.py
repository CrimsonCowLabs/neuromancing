"""Deep-agent tool functions (typed I/O) the graph nodes call: gather live
performance, describe the indicator vocabulary, and backtest a candidate spec over
walk-forward windows via trade-api's ad-hoc backtest. Deterministic; no LLM here."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from neuromancing_shared.strategy_spec import (
    ALLOWED_TIMEFRAMES,
    ARCHETYPES,
    CROSS,
    DICT_FIELDS,
    KNOWN_FNS,
    OPS,
    SOURCES,
)
from sqlalchemy.ext.asyncio import AsyncSession

from . import diary, memory

# Archive depth per timeframe (mirrors PRICE_BACKFILL_DAYS) — bounds walk-forward spans.
_TF_DEPTH_DAYS = {"1m": 5, "5m": 20, "1h": 120, "1d": 365}
_TF_SECONDS = {"1m": 60, "5m": 300, "1h": 3600, "1d": 86400}


def indicator_vocabulary() -> dict:
    """The grammar the reasoner may compose from."""
    return {
        "fns": list(KNOWN_FNS),
        "sources": list(SOURCES),
        "timeframes": list(ALLOWED_TIMEFRAMES),
        "ops": list(OPS),
        "cross": list(CROSS),
        "dict_fields": {k: list(v) for k, v in DICT_FIELDS.items()},
        "archetypes": list(ARCHETYPES),
    }


async def gather_performance(session: AsyncSession, agent_id: int, since_days: int = 30) -> dict:
    """Deterministic performance digest from the trade diary (+ recent experiments +
    a predicted-vs-realized calibration for the last adoption)."""
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    rows = await diary.read_closed(session, agent_id, since)
    agg = diary.aggregates(rows)
    exps = await memory.recent_experiments(session, agent_id, limit=5)
    agg["recent_experiments"] = [
        {"decision": e.decision, "reason": e.reason, "hypothesis": e.hypothesis} for e in exps
    ]
    # Calibration: what the last adopted strategy was PREDICTED to do vs what its
    # trades since adoption actually REALIZED — so the agent can learn if backtests
    # overpromise and demand a bigger edge next time.
    adopted = await memory.last_adopted(session, agent_id)
    if adopted is not None:
        pred = (adopted.backtests or {}).get("adopted", {})
        pred_ret = None
        if pred:
            ws = [pred.get("w1", {}).get("total_return"), pred.get("w2", {}).get("total_return")]
            ws = [w for w in ws if w is not None]
            pred_ret = round(sum(ws) / len(ws), 5) if ws else None
        realized = await diary.read_closed(session, agent_id, adopted.ts)
        realized_agg = diary.aggregates(realized)
        agg["last_adoption_calibration"] = {
            "adopted_at": adopted.ts.isoformat(),
            "predicted_avg_return": pred_ret,
            "realized_avg_return": realized_agg.get("avg_return"),
            "realized_episodes": realized_agg.get("episodes", 0),
        }
    return agg


def _base_tf(spec: dict) -> str:
    tfs = [i.get("timeframe") for i in spec.get("indicators", []) if i.get("timeframe")]
    return spec.get("base_timeframe") or (min(tfs, key=lambda t: _TF_SECONDS.get(t, 60)) if tfs else "1m")


def walk_forward_windows(spec: dict, now: datetime) -> list[tuple[datetime, datetime]]:
    """Two non-overlapping calendar spans sized to the spec's shallowest timeframe's
    archive depth (so a 5m strategy is tested on real 5m history)."""
    tfs = {i.get("timeframe") for i in spec.get("indicators", []) if i.get("timeframe")}
    tfs.add(_base_tf(spec))
    depth = min(_TF_DEPTH_DAYS.get(tf, 20) for tf in tfs)
    span = max(2, depth // 3)  # leave a gap; two spans within the archive
    w1 = (now - timedelta(days=span), now)
    w2 = (now - timedelta(days=2 * span + 1), now - timedelta(days=span + 1))
    return [w1, w2]


def _risk_knobs(risk_profile: dict | None) -> dict:
    """Map a construct's free-form risk profile to the backtest's sizing/exit knobs so a
    candidate is measured under the exact discipline it would trade under. Absent keys are
    dropped → the harness applies its live-like defaults (0.20 alloc, mandatory 0.08 stop)."""
    rp = risk_profile or {}
    knobs = {
        "alloc_pct": rp.get("max_position_pct"),
        "stop_loss_pct": rp.get("stop_loss_pct"),
        "take_profit_pct": rp.get("take_profit_pct"),
        "trailing_stop_pct": rp.get("trailing_stop_pct"),
    }
    return {k: v for k, v in knobs.items() if v is not None}


async def backtest_candidate(trade, spec: dict, symbols: list[str], now: datetime,
                             risk_profile: dict | None = None) -> dict:
    """Backtest a candidate over the two walk-forward windows × sampled symbols under the
    construct's own risk profile; return aggregate metrics per window: {"w1": {...}, "w2": {...}}."""
    windows = walk_forward_windows(spec, now)
    knobs = _risk_knobs(risk_profile)
    out: dict = {}
    for name, win in zip(("w1", "w2"), windows):
        rets, trades, dds = [], 0, []
        for sym in symbols:
            try:
                m = await trade.backtest_spec(spec, sym, kind="indicator_dsl", window=win, **knobs)
            except Exception:  # noqa: BLE001 — a thin symbol / no bars just contributes nothing
                continue
            rets.append(float(m.get("total_return", 0.0)))
            trades += int(m.get("trades", 0))
            dds.append(float(m.get("max_drawdown", 0.0)))
        out[name] = {
            "total_return": round(sum(rets) / len(rets), 5) if rets else 0.0,
            "trades": trades,
            "max_drawdown": round(max(dds), 5) if dds else 0.0,
            "symbols": len(rets),
        }
    return out
