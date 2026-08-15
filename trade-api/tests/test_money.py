"""Smoke + property tests for the shared money math. The full matching-engine
and money/position property suite is built under task #3."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from neuromancing_shared.money import D, quantize_money, quantize_qty


def test_no_binary_float_drift():
    # 0.1 + 0.2 must be exactly 0.3 in Decimal money space.
    assert quantize_money(D("0.1") + D("0.2")) == Decimal("0.30000000")


def test_float_coerced_via_string():
    # Floats are stringified so we don't inherit 0.1's binary error.
    assert D(0.1) == Decimal("0.1")


@given(st.decimals(min_value=0, max_value=10**9, allow_nan=False, allow_infinity=False))
def test_money_quantization_is_idempotent(value):
    once = quantize_money(value)
    assert quantize_money(once) == once


@given(st.decimals(min_value=0, max_value=10**6, allow_nan=False, allow_infinity=False))
def test_qty_quantization_is_idempotent(value):
    once = quantize_qty(value)
    assert quantize_qty(once) == once
