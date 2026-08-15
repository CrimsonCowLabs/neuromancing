"""Money & quantity math — Decimal only, never float.

All monetary and quantity values in Neuromancing are Decimals persisted as
Postgres NUMERIC. This module centralizes construction and rounding so P&L is
never subject to binary float drift. See the property-based tests in trade-api.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, getcontext

# Generous precision headroom for crypto (many decimals) and interim products.
getcontext().prec = 38

ZERO = Decimal("0")

# Money is tracked to 8 dp (covers fiat cents and small crypto notional cleanly).
_MONEY_Q = Decimal("0.00000001")
# Quantity to 10 dp (fractional shares + fine-grained crypto units).
_QTY_Q = Decimal("0.0000000001")


def D(value: object) -> Decimal:
    """Coerce to Decimal safely. Floats are stringified first to avoid
    inheriting binary representation error."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)  # type: ignore[arg-type]


def quantize_money(value: object) -> Decimal:
    return D(value).quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def quantize_qty(value: object) -> Decimal:
    return D(value).quantize(_QTY_Q, rounding=ROUND_HALF_UP)
