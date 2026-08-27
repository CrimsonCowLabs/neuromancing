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
    # Idempotent: 0001 builds the schema via metadata.create_all() from the CURRENT
    # models, so on a FRESH DB these columns already exist. Guard each add so
    # `alembic upgrade head` is clean on both fresh installs and incrementally-migrated
    # DBs. (The partial index below is migration-only — not in the model metadata — so
    # create_all does NOT make it; its own IF NOT EXISTS keeps it idempotent.)
    insp = sa.inspect(op.get_bind())
    pos_cols = {c["name"] for c in insp.get_columns("position", schema="trade")}
    ord_cols = {c["name"] for c in insp.get_columns("order", schema="trade")}
    for col in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct"):
        if col not in pos_cols:
            op.add_column("position", sa.Column(col, _PCT, nullable=True), schema="trade")
        if col not in ord_cols:
            op.add_column("order", sa.Column(col, _PCT, nullable=True), schema="trade")
    if "high_water_price" not in pos_cols:
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
