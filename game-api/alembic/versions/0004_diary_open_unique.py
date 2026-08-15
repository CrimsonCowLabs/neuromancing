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
    op.drop_index("ix_diary_open_slot", table_name="trade_diary", schema=SCHEMA)
    op.create_index(
        "ux_diary_open_slot", "trade_diary", ["agent_id", "symbol"], unique=True,
        schema=SCHEMA, postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ux_diary_open_slot", table_name="trade_diary", schema=SCHEMA)
    op.create_index("ix_diary_open_slot", "trade_diary",
                    ["agent_id", "symbol", "status"], schema=SCHEMA)
