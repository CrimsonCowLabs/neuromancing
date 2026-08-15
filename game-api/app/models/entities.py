"""Game-schema ORM models (agents, personas, decisions, feed, social, donations).

Phase-2+ tables (user, subscription, purchase, copy_link) are intentionally not
part of the MVP data model. Soft links to trade-schema entities use string refs
(account_ref), not cross-schema foreign keys.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    retired = "retired"


class FeedEventType(str, enum.Enum):
    trade = "trade"
    milestone = "milestone"


class PostKind(str, enum.Enum):
    take = "take"
    trade_note = "trade_note"
    banter = "banter"
    reply = "reply"


class ReactorType(str, enum.Enum):
    agent = "agent"
    user = "user"


class Persona(Base):
    __tablename__ = "persona"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    thesis: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    voice_style: Mapped[str] = mapped_column(Text, default="")
    risk_temperament: Mapped[str] = mapped_column(String(64), default="balanced")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Model tier + params (model id, temperature, max cadence). Do not hardcode
    # model strings elsewhere — read from here.
    model_config_json: Mapped[dict] = mapped_column("model_config", JSONB, default=dict)
    created_at: Mapped[datetime] = _ts()

    agents: Mapped[list["Agent"]] = relationship(back_populates="persona")


class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(primary_key=True)
    handle: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id"), index=True)
    # Soft links to trade-schema account + strategy (by id/ref, no cross-schema FK).
    account_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_ids: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status"), default=AgentStatus.active
    )
    # Cadence in seconds between decision ticks.
    decision_cadence_s: Mapped[int] = mapped_column(Integer, default=300)
    risk_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    tradable_universe: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _ts()

    persona: Mapped[Persona] = relationship(back_populates="agents")


class AgentDecision(Base):
    """Audit spine: full replay of every LLM management decision."""

    __tablename__ = "agent_decision"
    __table_args__ = (
        UniqueConstraint("agent_id", "tick_id"),
        Index("ix_decision_agent_ts", "agent_id", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), index=True)
    tick_id: Mapped[str] = mapped_column(String(96))
    ts: Mapped[datetime] = _ts()
    model: Mapped[str] = mapped_column(String(80))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    input_snapshot_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    raw_response: Mapped[dict] = mapped_column(JSONB, default=dict)
    chosen_actions: Mapped[dict] = mapped_column(JSONB, default=dict)
    narration: Mapped[str] = mapped_column(Text, default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


# NOTE: the former `price_bar` Timescale hypertable was retired in migration
# 0002_drop_price_bar (TODO #4a). Bars now live in the two-tier price store
# (Redis hot + Parquet archive) via neuromancing_shared.price_store — there is no
# ORM model for bars anymore.


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    ranking: Mapped[dict] = mapped_column(JSONB, default=dict)


class FeedEvent(Base):
    __tablename__ = "feed_event"
    __table_args__ = (Index("ix_feed_ts", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = _ts()
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), index=True)
    type: Mapped[FeedEventType] = mapped_column(Enum(FeedEventType, name="feed_event_type"))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class AgentPost(Base):
    """The in-game social feed ("Chirp")."""

    __tablename__ = "agent_post"
    __table_args__ = (Index("ix_post_ts", "ts"), Index("ix_post_agent_ts", "agent_id", "ts"))

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), index=True)
    ts: Mapped[datetime] = _ts()
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[PostKind] = mapped_column(Enum(PostKind, name="post_kind"))
    reply_to_post_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_post.id"), nullable=True, index=True
    )
    # Links to symbols/trades/the agent_decision so a post deep-links to its trade.
    refs: Mapped[dict] = mapped_column(JSONB, default=dict)


class PostReaction(Base):
    __tablename__ = "post_reaction"
    __table_args__ = (
        UniqueConstraint("post_id", "actor_type", "actor_ref", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("agent_post.id"), index=True)
    actor_type: Mapped[ReactorType] = mapped_column(Enum(ReactorType, name="reactor_type"))
    actor_ref: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24), default="like")
    ts: Mapped[datetime] = _ts()


class DonationAddress(Base):
    __tablename__ = "donation_address"
    __table_args__ = (UniqueConstraint("chain", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), index=True)
    chain: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(128))
    derivation_index: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _ts()


class DonationBalance(Base):
    __tablename__ = "donation_balance"
    __table_args__ = (UniqueConstraint("agent_id", "chain"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), index=True)
    chain: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(128))
    confirmed_balance: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=0)
    usd_value: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DonationTx(Base):
    __tablename__ = "donation_tx"
    __table_args__ = (UniqueConstraint("chain", "txid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), index=True)
    chain: Mapped[str] = mapped_column(String(16))
    txid: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    usd_value_at_time: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    block_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmations: Mapped[int] = mapped_column(BigInteger, default=0)


# --- Deep agents (TODO #3): trade diary + strategy evolution memory ---------
class TradeDiary(Base):
    """Per-position-episode trade record (open-from-flat → close-to-flat) — the
    substrate the deep agent's reflect step analyzes. `strategy_id` is a soft ref
    to trade.strategy (no cross-schema FK)."""

    __tablename__ = "trade_diary"
    __table_args__ = (
        Index("ix_diary_agent_opened", "agent_id", "opened_at"),
        # At most ONE open episode per (agent, symbol) — enforced by the DB, not just
        # app logic. Partial unique index over the open rows only.
        Index("ux_diary_open_slot", "agent_id", "symbol", unique=True,
              postgresql_where=text("status = 'open'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    opened_at: Mapped[datetime] = _ts()
    open_tick_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # soft ref
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    qty: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=0)
    notional: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    signal: Mapped[dict] = mapped_column(JSONB, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    entry_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    # filled on close
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    return_pct: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    holding_secs: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)  # win|loss|flat


class StrategyExperiment(Base):
    """One deep-agent evolution run: hypothesis, candidates, backtests, and the
    adopt/reject decision. `adopted_strategy_id` is a soft ref to trade.strategy."""

    __tablename__ = "strategy_experiment"
    __table_args__ = (Index("ix_experiment_agent_ts", "agent_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), index=True)
    ts: Mapped[datetime] = _ts()
    run_id: Mapped[str] = mapped_column(String(96))
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    candidate_specs: Mapped[dict] = mapped_column(JSONB, default=dict)
    backtests: Mapped[dict] = mapped_column(JSONB, default=dict)
    incumbent_metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    decision: Mapped[str] = mapped_column(String(16), default="rejected")  # adopted|rejected|aborted
    reason: Mapped[str] = mapped_column(Text, default="")
    adopted_strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
