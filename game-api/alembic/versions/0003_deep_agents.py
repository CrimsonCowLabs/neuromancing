"""deep agents: trade_diary + strategy_experiment (TODO #3)

Adds the per-position trade diary (the deep agent's analysis substrate) and the
strategy-evolution experiment memory. Both are plain game-schema tables; soft refs
to trade.strategy are plain ints (no cross-schema FK). The LangGraph Postgres
checkpointer manages its OWN tables via `.setup()` — not created here.

Revision ID: 0003_deep_agents
Revises: 0002_drop_price_bar
Create Date: 2026-08-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_deep_agents"
down_revision: Union[str, None] = "0002_drop_price_bar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "game"


def upgrade() -> None:
    # Idempotent: 0001 builds the schema via metadata.create_all() from the CURRENT
    # models, so on a FRESH DB these tables already exist. Guard the creates (and use
    # CREATE INDEX IF NOT EXISTS) so `alembic upgrade head` is clean on both fresh installs
    # and incrementally-migrated DBs.
    insp = sa.inspect(op.get_bind())
    have = set(insp.get_table_names(schema=SCHEMA))
    if "trade_diary" not in have:
        op.create_table(
            "trade_diary",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("agent_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.agent.id"), index=True),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("status", sa.String(8), server_default="open", nullable=False),
            sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("open_tick_id", sa.String(96), nullable=True),
            sa.Column("strategy_id", sa.Integer, nullable=True),
            sa.Column("entry_price", sa.Numeric(20, 8), server_default="0"),
            sa.Column("qty", sa.Numeric(28, 10), server_default="0"),
            sa.Column("notional", sa.Numeric(20, 8), server_default="0"),
            sa.Column("signal", JSONB, server_default="{}"),
            sa.Column("rationale", sa.Text, server_default=""),
            sa.Column("entry_context", JSONB, server_default="{}"),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("exit_price", sa.Numeric(20, 8), nullable=True),
            sa.Column("exit_reason", sa.String(16), nullable=True),
            sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=True),
            sa.Column("return_pct", sa.Numeric(10, 6), nullable=True),
            sa.Column("holding_secs", sa.BigInteger, nullable=True),
            sa.Column("outcome", sa.String(8), nullable=True),
            schema=SCHEMA,
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_diary_agent_opened ON game.trade_diary (agent_id, opened_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_diary_open_slot ON game.trade_diary (agent_id, symbol, status)")

    if "strategy_experiment" not in have:
        op.create_table(
            "strategy_experiment",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("agent_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.agent.id"), index=True),
            sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("run_id", sa.String(96), nullable=False),
            sa.Column("hypothesis", sa.Text, server_default=""),
            sa.Column("candidate_specs", JSONB, server_default="{}"),
            sa.Column("backtests", JSONB, server_default="{}"),
            sa.Column("incumbent_metrics", JSONB, server_default="{}"),
            sa.Column("decision", sa.String(16), server_default="rejected"),
            sa.Column("reason", sa.Text, server_default=""),
            sa.Column("adopted_strategy_id", sa.Integer, nullable=True),
            schema=SCHEMA,
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_experiment_agent_ts ON game.strategy_experiment (agent_id, ts)")


def downgrade() -> None:
    op.drop_table("strategy_experiment", schema=SCHEMA)
    op.drop_table("trade_diary", schema=SCHEMA)
