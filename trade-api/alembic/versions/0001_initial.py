"""initial trade schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from app.models import Base

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    # Create all trade-schema tables + enum types from the ORM metadata.
    Base.metadata.create_all(bind=bind, checkfirst=True)
    # Equity curve → Timescale hypertable (partition on ts).
    op.execute(
        "SELECT create_hypertable('trade.equity_snapshot', 'ts', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS trade CASCADE")
