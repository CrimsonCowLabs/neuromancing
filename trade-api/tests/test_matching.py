from datetime import datetime, timezone
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.engine.matching import compute_fill, limit_stop_crosses, slippage_bps_for
from app.prices import Quote

D = Decimal


def _quote(bid="99", ask="101", last="100"):
    return Quote(
        symbol="TEST",
        bid=D(bid),
        ask=D(ask),
        last=D(last),
        ts=datetime.now(timezone.utc),
        stale=False,
    )


def test_buy_fills_at_ask_plus_slippage():
    fq = compute_fill(quote=_quote(), side="buy", asset_class="equity", qty=D("10"))
    # ask 101, small size -> 1bps slippage -> 101 * 1.0001
    assert fq.price >= D("101")
    assert fq.qty == D("10")


def test_sell_fills_at_bid_minus_slippage():
    fq = compute_fill(quote=_quote(), side="sell", asset_class="equity", qty=D("10"))
    assert fq.price <= D("99")


def test_notional_order_converts_to_fractional_qty():
    fq = compute_fill(quote=_quote(), side="buy", asset_class="equity", notional=D("500"))
    # qty * price should be ~ the notional (within a fill-price rounding).
    assert abs(fq.qty * fq.price - D("500")) < D("1")


def test_crypto_has_fees_equity_none():
    eq = compute_fill(quote=_quote(), side="buy", asset_class="equity", qty=D("10"))
    cr = compute_fill(quote=_quote(), side="buy", asset_class="crypto", qty=D("10"))
    assert eq.fees == D("0")
    assert cr.fees > D("0")


def test_slippage_tiers_monotonic():
    assert slippage_bps_for(D("500")) <= slippage_bps_for(D("5000"))
    assert slippage_bps_for(D("5000")) <= slippage_bps_for(D("50000"))
    assert slippage_bps_for(D("50000")) <= slippage_bps_for(D("5000000"))


@settings(max_examples=200)
@given(
    qty=st.decimals(min_value=D("0.0001"), max_value=D("100000"), places=4),
    last=st.decimals(min_value=D("0.01"), max_value=D("100000"), places=2),
    side=st.sampled_from(["buy", "sell"]),
)
def test_fill_price_direction_and_positivity(qty, last, side):
    q = Quote("X", None, None, last, datetime.now(timezone.utc), False)
    fq = compute_fill(quote=q, side=side, asset_class="equity", qty=qty)
    assert fq.price > 0
    assert fq.qty > 0
    if side == "buy":
        assert fq.price >= last  # adverse slippage never helps the buyer
    else:
        assert fq.price <= last


def test_limit_buy_crosses_when_price_at_or_below_limit():
    q = _quote(bid="95", ask="95", last="95")
    assert limit_stop_crosses(
        order_type="limit", side="buy", limit_price=D("96"), stop_price=None, quote=q
    )
    assert not limit_stop_crosses(
        order_type="limit", side="buy", limit_price=D("94"), stop_price=None, quote=q
    )


def test_stop_sell_triggers_when_price_falls_through_stop():
    q = _quote(bid="90", ask="90", last="90")
    assert limit_stop_crosses(
        order_type="stop", side="sell", limit_price=None, stop_price=D("92"), quote=q
    )
