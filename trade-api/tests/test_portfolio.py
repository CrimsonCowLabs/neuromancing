from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.engine.portfolio import PositionState, apply_fill, positions_value

D = Decimal
ZERO = D("0")


def test_buy_sets_avg_and_reduces_cash():
    out = apply_fill(
        cash=D("1000"), position=PositionState(ZERO, ZERO),
        side="buy", qty=D("10"), price=D("50"), fees=D("0"),
    )
    assert out.position.qty == D("10")
    assert out.position.avg_entry == D("50")
    assert out.cash == D("500")
    assert out.realized_pnl == ZERO


def test_second_buy_weighted_average():
    out1 = apply_fill(cash=D("1000"), position=PositionState(ZERO, ZERO),
                      side="buy", qty=D("10"), price=D("50"), fees=D("0"))
    out2 = apply_fill(cash=out1.cash, position=out1.position,
                      side="buy", qty=D("10"), price=D("60"), fees=D("0"))
    assert out2.position.qty == D("20")
    assert out2.position.avg_entry == D("55")  # (500+600)/20


def test_sell_realizes_pnl_and_adds_cash():
    pos = PositionState(D("10"), D("50"))
    out = apply_fill(cash=D("500"), position=pos,
                     side="sell", qty=D("10"), price=D("60"), fees=D("0"))
    assert out.position.qty == ZERO
    assert out.position.avg_entry == ZERO
    assert out.cash == D("1100")
    assert out.realized_pnl == D("100")  # 10 * (60-50)


def test_oversized_sell_rejected():
    with pytest.raises(ValueError):
        apply_fill(cash=ZERO, position=PositionState(D("5"), D("50")),
                   side="sell", qty=D("10"), price=D("60"), fees=D("0"))


def test_positions_value():
    pv = positions_value({"A": D("10"), "B": D("20")}, {"A": D("2"), "B": D("3")})
    assert pv == D("80")  # 2*10 + 3*20


@settings(max_examples=200)
@given(
    q1=st.decimals(min_value=D("0.001"), max_value=D("1000"), places=3),
    p1=st.decimals(min_value=D("0.01"), max_value=D("10000"), places=2),
    p2=st.decimals(min_value=D("0.01"), max_value=D("10000"), places=2),
)
def test_buy_then_full_sell_cash_conservation(q1, p1, p2):
    # Buy q1 @ p1 (no fees), then sell all @ p2. Net cash change == realized pnl.
    start = D("10000000")
    buy = apply_fill(cash=start, position=PositionState(ZERO, ZERO),
                     side="buy", qty=q1, price=p1, fees=ZERO)
    sell = apply_fill(cash=buy.cash, position=buy.position,
                      side="sell", qty=buy.position.qty, price=p2, fees=ZERO)
    assert sell.position.qty == ZERO
    # Final cash - start cash should equal realized pnl (within qty rounding).
    assert abs((sell.cash - start) - sell.realized_pnl) < D("0.01")
