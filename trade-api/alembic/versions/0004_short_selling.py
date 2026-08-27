"""short selling: SignalAction short/cover + order.reduce_only

Adds the `short`/`cover` values to the signal_action enum (so short-capable
strategies can emit + persist short/cover signals) and a `reduce_only` boolean on
`order` (the anti-flip / stale-close safety catch). No `Position.qty` change is
needed — the NUMERIC column is already signed; a short is just a negative qty.

PG18 allows ADD VALUE inside a transaction as long as the value isn't *used* in the
same transaction (it isn't). IF NOT EXISTS makes the enum add idempotent.

Revision ID: 0004_short_selling
Revises: 0003_indicator_dsl_kind
Create Date: 2026-08-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_short_selling"
down_revision: Union[str, None] = "0003_indicator_dsl_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE signal_action ADD VALUE IF NOT EXISTS 'short'")
    op.execute("ALTER TYPE signal_action ADD VALUE IF NOT EXISTS 'cover'")
    # Idempotent: on a fresh DB 0001's create_all already added reduce_only (it's in the
    # current model), so guard the add.
    insp = sa.inspect(op.get_bind())
    if "reduce_only" not in {c["name"] for c in insp.get_columns("order", schema="trade")}:
        op.add_column(
            "order",
            sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            schema="trade",
        )


def downgrade() -> None:
    op.drop_column("order", "reduce_only", schema="trade")
    # Postgres cannot DROP a single enum value; leaving short/cover is harmless.
