"""add the indicator_dsl value to the strategy_kind enum (TODO #2)

The YAML-authored, validated, multi-timeframe strategy model adds a third
`StrategyKind`. Postgres enums are extended with ALTER TYPE ... ADD VALUE.

Note: on PG 12+ (we run PG 18) ADD VALUE is allowed inside a transaction as long
as the new value isn't *used* in the same transaction (it isn't here).
IF NOT EXISTS makes the migration idempotent.

Revision ID: 0003_indicator_dsl_kind
Revises: 0002_position_exits
Create Date: 2026-08-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003_indicator_dsl_kind"
down_revision: Union[str, None] = "0002_position_exits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE strategy_kind ADD VALUE IF NOT EXISTS 'indicator_dsl'")


def downgrade() -> None:
    # Postgres cannot DROP a single enum value; leaving it is harmless (no rows use
    # it after a downstream cleanup). No-op.
    pass
