"""Phase 1 — options pricing/vol/risk core. Pure math; no I/O."""

import math

import pytest

from neuromancing_shared.options import black_scholes as bs
from neuromancing_shared.options import risk
from neuromancing_shared.options.risk import Leg
from neuromancing_shared.options.vol import iv_model, realized_vol

S, K, T, R, SIG = 100.0, 100.0, 0.5, 0.04, 0.25


# ---- Black-Scholes ----
def test_put_call_parity():
    c = bs.price(S, K, T, R, SIG, "call")
    p = bs.price(S, K, T, R, SIG, "put")
    # C - P = S e^{-qT} - K e^{-rT}
    assert c - p == pytest.approx(S - K * math.exp(-R * T), abs=1e-6)


def test_price_bounds_and_intrinsic():
    # Deep ITM call ≈ intrinsic (discounted); expired option = intrinsic.
    assert bs.price(200, 100, T, R, SIG, "call") > 100 - 100 * math.exp(-R * T) - 1
    assert bs.price(120, 100, 0.0, R, SIG, "call") == pytest.approx(20.0)
    assert bs.price(80, 100, 0.0, R, SIG, "put") == pytest.approx(20.0)


def test_greeks_delta_finite_difference():
    g = bs.greeks(S, K, T, R, SIG, "call")
    h = 1e-4
    fd = (bs.price(S + h, K, T, R, SIG, "call") - bs.price(S - h, K, T, R, SIG, "call")) / (2 * h)
    assert g["delta"] == pytest.approx(fd, abs=1e-4)
    # ATM call delta ~0.5+, put delta negative, gamma positive, vega positive.
    assert 0.4 < g["delta"] < 0.7
    assert bs.greeks(S, K, T, R, SIG, "put")["delta"] < 0
    assert g["gamma"] > 0 and g["vega"] > 0


def test_vega_finite_difference():
    g = bs.greeks(S, K, T, R, SIG, "call")
    h = 1e-4
    fd = (bs.price(S, K, T, R, SIG + h, "call") - bs.price(S, K, T, R, SIG - h, "call")) / (2 * h)
    assert g["vega"] == pytest.approx(fd, abs=1e-2)


def test_implied_vol_roundtrip():
    px = bs.price(S, K, T, R, 0.32, "put")
    iv = bs.implied_vol(px, S, K, T, R, "put")
    assert iv == pytest.approx(0.32, abs=1e-4)


# ---- vol model ----
def test_realized_vol_and_iv_model():
    closes = [100 * (1.001 ** i) for i in range(40)]  # smooth uptrend -> low vol
    rv = realized_vol(closes, window=20, timeframe="1d")
    assert rv is not None and rv >= 0
    assert realized_vol([100, 101], window=20) is None  # not enough history
    # VRP lifts IV above RV; put skew makes a lower strike richer than a higher one.
    assert iv_model(100, 100, 0.1, 0.20, vrp_mult=1.2, skew=0.0) == pytest.approx(0.24, abs=1e-9)
    assert iv_model(100, 90, 0.1, 0.20) > iv_model(100, 110, 0.1, 0.20)


# ---- defined-risk ----
def test_cash_secured_put_max_loss():
    legs = [Leg("put", "sell", 95.0)]
    ml = risk.max_loss(legs, net_credit_ps=2.0)  # credit $2/sh
    assert ml == pytest.approx((95.0 - 2.0) * 100)  # assigned at 95, keep $2
    assert risk.is_defined_risk(legs, 2.0)


def test_vertical_and_condor_bounded_by_width():
    # bull put spread: short 95 / long 90, $1 credit -> max loss (5-1)*100
    vert = [Leg("put", "sell", 95.0), Leg("put", "buy", 90.0)]
    assert risk.max_loss(vert, 1.0) == pytest.approx((5.0 - 1.0) * 100)
    # iron condor: put spread 95/90 + call spread 105/110, $2 net credit
    condor = vert + [Leg("call", "sell", 105.0), Leg("call", "buy", 110.0)]
    ml = risk.max_loss(condor, 2.0)
    assert ml == pytest.approx((5.0 - 2.0) * 100)  # worst wing width minus credit
    assert risk.is_defined_risk(condor, 2.0)


def test_covered_call_bounded():
    legs = [Leg("call", "sell", 105.0)]
    assert risk.is_defined_risk(legs, 2.0, has_stock=True, stock_entry=100.0)
    # loss if stock -> 0: keep $2 credit, lose $100 of stock
    assert risk.max_loss(legs, 2.0, has_stock=True, stock_entry=100.0) == pytest.approx((100 - 2) * 100)


def test_naked_short_call_rejected():
    legs = [Leg("call", "sell", 105.0)]  # no covering long, no stock
    assert risk.max_loss(legs, 2.0) == float("inf")
    assert not risk.is_defined_risk(legs, 2.0)  # categorically undefined risk


def test_sizing_by_defined_risk():
    # $100k equity, risk 2% => $2000; condor max loss $300 -> 6 contracts
    assert risk.sized_contracts(100_000, 0.02, 300.0) == 6
    assert risk.sized_contracts(100_000, 0.02, float("inf")) == 0


# ---- chain generator ----
def test_build_chain_shape_and_monotone_deltas():
    from datetime import date

    from neuromancing_shared.options.chain import build_chain

    rows = build_chain("AAPL", 200.0, date(2026, 1, 1), 0.25, expiries_dte=[30], n_strikes=3, step_pct=0.02)
    assert len(rows) == (2 * 3 + 1) * 2  # 7 strikes × {call,put}
    calls = sorted([r for r in rows if r["right"] == "call"], key=lambda r: r["strike"])
    deltas = [c["delta"] for c in calls]
    assert all(deltas[i] >= deltas[i + 1] for i in range(len(deltas) - 1))  # falls with strike
    assert all(0 < c["delta"] < 1 for c in calls)
    assert all(-1 < r["delta"] < 0 for r in rows if r["right"] == "put")


def test_strike_for_delta_inverts():
    from neuromancing_shared.options.chain import strike_for_delta
    from neuromancing_shared.options.vol import iv_model

    t = 30 / 365
    kc = strike_for_delta(200.0, t, 0.25, "call", 0.30)
    assert kc is not None and kc > 200  # a 0.30-delta call is OTM (above spot)
    ivc = iv_model(200.0, kc, t, 0.25)
    assert abs(bs.greeks(200.0, kc, t, 0.04, ivc, "call")["delta"] - 0.30) < 0.02
    # a 0.30-delta PUT must be OTM too (BELOW spot) — the monotonicity bug put it above.
    kp = strike_for_delta(200.0, t, 0.25, "put", 0.30)
    assert kp is not None and kp < 200
    ivp = iv_model(200.0, kp, t, 0.25)
    assert abs(bs.greeks(200.0, kp, t, 0.04, ivp, "put")["delta"] + 0.30) < 0.02


# ── short/flat backtest replay (Phase 2) ──
def test_short_strategy_backtests_short_flat():
    """A short that opens on an overbought crossing and covers lower banks a win — asserted
    through the backtest seam (the signed open/cover accounting itself is property-tested in
    test_portfolio, and the exit replay in test_exits). The short covers at its take-profit
    (stop loosened so the post-entry rise doesn't stop it out first)."""
    from datetime import datetime, timedelta, timezone

    from app.strategies import BacktestConfig, build_strategy
    from app.strategies.backtest import ExitConfig
    from app.strategies.base import Bar
    from app.strategies.spec import validate_spec

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    spec = validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "short_when": {"all": [{"indicator": "r", "cross": "above", "value": 65}]},
    })
    # decline (RSI low) → sharp rally that crosses RSI up through 65 (opens a short) → a
    # deep decline that carries price below the short's take-profit target.
    closes = [120 - i for i in range(45)] + [76 + i * 3 for i in range(8)] + [100 - i * 2 for i in range(40)]
    bars = {"5m": [Bar(ts=t0 + timedelta(minutes=5 * i), open=c, high=c, low=c, close=c, volume=1)
                   for i, c in enumerate(closes)]}
    cfg = BacktestConfig(cost_bps=0.0, exit_config=ExitConfig(stop_loss_pct=0.90, take_profit_pct=0.08))
    r = build_strategy("indicator_dsl", spec).backtest(bars, cfg).to_dict()
    assert r["trades"] == 1 and r["win_rate"] == 1.0        # short opened and covered lower
    assert r["final_equity"] > 10000.0                      # profited on the decline
