"""enforce one open trade-diary episode per (agent, symbol)

Replaces the non-unique open-slot index with a PARTIAL UNIQUE index over the open
rows, so the "one open episode per symbol" invariant is enforced by the DB, not just
app logic (TODO #3 review fix).

Revision ID: 0004_diary_open_unique
Revises: 0003_deep_agents
Create Date: 2026-08-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_diary_open_unique"
down_revision: Union[str, None] = "0003_deep_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "game"


def upgrade() -> None:
    # Idempotent: raw IF (NOT) EXISTS so `upgrade head` is clean on both fresh installs
    # (where 0001's create_all may already carry the current model's indexes) and
    # incrementally-migrated DBs.
    op.execute("DROP INDEX IF EXISTS game.ix_diary_open_slot")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_diary_open_slot ON game.trade_diary "
        "(agent_id, symbol) WHERE status = 'open'"
    )


def downgrade() -> None:
    op.drop_index("ux_diary_open_slot", table_name="trade_diary", schema=SCHEMA)
    op.create_index("ix_diary_open_slot", "trade_diary",
                    ["agent_id", "symbol", "status"], schema=SCHEMA)
