"""drop the retired price_bar hypertable (TODO #4a)

Bars now live in the two-tier price store (Redis hot + Parquet archive) via
neuromancing_shared.price_store, so the `game.price_bar` Timescale hypertable is
retired. This drops ONLY that table — the timescaledb extension and every other
table (incl. trade-schema hypertables) are untouched.

⚠️ Deploy ordering: the new price store must be deployed and the Parquet/Redis
archive warmed (backfill) BEFORE this migration runs, so consumers never hit a
window with no bars anywhere. See docs/08-market-data.md.

Revision ID: 0002_drop_price_bar
Revises: 0001_initial
Create Date: 2026-08-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002_drop_price_bar"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CASCADE covers any Timescale-internal chunks/objects tied to the hypertable.
    op.execute("DROP TABLE IF EXISTS game.price_bar CASCADE")


def downgrade() -> None:
    # No rollback: bars are no longer stored in Postgres. Re-create the store by
    # re-running market-ingest backfill (Parquet/Redis), not a schema migration.
    pass
