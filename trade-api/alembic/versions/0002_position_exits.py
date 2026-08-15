"""position + order exit levels (stop-loss / take-profit / trailing)

Revision ID: 0002_position_exits
Revises: 0001_initial
Create Date: 2026-08-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_position_exits"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PCT = sa.Numeric(6, 4)
_MONEY = sa.Numeric(20, 8)


def upgrade() -> None:
    for col in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct"):
        op.add_column("position", sa.Column(col, _PCT, nullable=True), schema="trade")
        op.add_column("order", sa.Column(col, _PCT, nullable=True), schema="trade")
    op.add_column(
        "position", sa.Column("high_water_price", _MONEY, nullable=True), schema="trade"
    )
    # Partial index so the position-monitor's scan stays cheap as the universe grows.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_position_exit_watch ON trade.position (symbol) "
        "WHERE qty > 0 AND (stop_loss_pct IS NOT NULL OR take_profit_pct IS NOT NULL "
        "OR trailing_stop_pct IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS trade.ix_position_exit_watch")
    op.drop_column("position", "high_water_price", schema="trade")
    for col in ("trailing_stop_pct", "take_profit_pct", "stop_loss_pct"):
        op.drop_column("order", col, schema="trade")
        op.drop_column("position", col, schema="trade")
