"""initial game schema

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
    Base.metadata.create_all(bind=bind, checkfirst=True)
    # NOTE: the `price_bar` Timescale hypertable that used to be created here was
    # retired in 0002_drop_price_bar (TODO #4a) — bars moved to the two-tier price
    # store (Redis hot + Parquet). The timescaledb extension stays for
    # trade-schema hypertables (equity_snapshot).


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS game CASCADE")
