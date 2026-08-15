"""Trade-schema ORM models (accounts, execution, strategies).

Money & quantity are NUMERIC (never float). Cross-service links to game entities
are soft (string external_ref), not DB foreign keys, to keep the services
independent and let the execution backend swap (Sim -> Alpaca Broker) freely.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

# NUMERIC precisions: money to 8dp, quantity to 10dp (fractional shares + crypto).
Money = Numeric(20, 8)
Qty = Numeric(28, 10)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetClass(str, enum.Enum):
    equity = "equity"
    crypto = "crypto"


class BackendType(str, enum.Enum):
    sim = "sim"
    alpaca = "alpaca"


class OrderSide(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class OrderType(str, enum.Enum):
    market = "market"
    limit = "limit"
    stop = "stop"
    stop_limit = "stop_limit"


class TIF(str, enum.Enum):
    day = "day"
    gtc = "gtc"
    ioc = "ioc"


class OrderStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    partially_filled = "partially_filled"
    filled = "filled"
    canceled = "canceled"
    rejected = "rejected"
    expired = "expired"


class OrderSource(str, enum.Enum):
    agent = "agent"
    copy = "copy"
    manual = "manual"


class StrategyOwner(str, enum.Enum):
    house = "house"
    user = "user"


class StrategyKind(str, enum.Enum):
    # Deterministic only — the LLM never originates signals.
    rule_dsl = "rule_dsl"
    signal_fn = "signal_fn"
    indicator_dsl = "indicator_dsl"  # YAML-authored, validated, multi-timeframe


class StrategyStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    retired = "retired"


class SignalAction(str, enum.Enum):
    buy = "buy"
    sell = "sell"
    hold = "hold"
    exit = "exit"


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Soft link to a game.agent (no cross-schema FK).
    external_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    base_currency: Mapped[str] = mapped_column(String(8), default="USD")
    backend_type: Mapped[BackendType] = mapped_column(
        Enum(BackendType, name="backend_type"), default=BackendType.sim
    )
    cash_balance: Mapped[Decimal] = mapped_column(Money, default=0)
    buying_power: Mapped[Decimal] = mapped_column(Money, default=0)
    starting_equity: Mapped[Decimal] = mapped_column(Money, default=0)
    created_at: Mapped[datetime] = _ts()

    positions: Mapped[list["Position"]] = relationship(back_populates="account")
    orders: Mapped[list["Order"]] = relationship(back_populates="account")


class Position(Base):
    __tablename__ = "position"
    __table_args__ = (UniqueConstraint("account_id", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(AssetClass, name="asset_class"), default=AssetClass.equity
    )
    qty: Mapped[Decimal] = mapped_column(Qty, default=0)
    avg_entry_price: Mapped[Decimal] = mapped_column(Money, default=0)
    # Deterministic exit config (fractions of avg entry). Enforced by the
    # position-monitor; reset when the position is (re)opened from qty 0.
    stop_loss_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    take_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    trailing_stop_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    high_water_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account: Mapped[Account] = relationship(back_populates="positions")


class Order(Base):
    __tablename__ = "order"
    __table_args__ = (
        UniqueConstraint("account_id", "client_order_id"),
        Index("ix_order_account_created", "account_id", "submitted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    # Deterministic idempotency key from (agent_id, tick_id, n).
    client_order_id: Mapped[str] = mapped_column(String(96))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(AssetClass, name="asset_class"), default=AssetClass.equity
    )
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide, name="order_side"))
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType, name="order_type"))
    qty: Mapped[Decimal | None] = mapped_column(Qty, nullable=True)
    notional: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    limit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    tif: Mapped[TIF] = mapped_column(Enum(TIF, name="tif"), default=TIF.day)
    # Requested deterministic exit levels (fractions); transferred to the position on fill.
    stop_loss_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    take_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    trailing_stop_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.pending, index=True
    )
    source: Mapped[OrderSource] = mapped_column(
        Enum(OrderSource, name="order_source"), default=OrderSource.agent
    )
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    submitted_at: Mapped[datetime] = _ts()
    filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    account: Mapped[Account] = relationship(back_populates="orders")
    fills: Mapped[list["Fill"]] = relationship(back_populates="order")

    __table_args__ += (
        CheckConstraint(
            "(qty IS NOT NULL) OR (notional IS NOT NULL)",
            name="ck_order_qty_or_notional",
        ),
    )


class Fill(Base):
    __tablename__ = "fill"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order.id"), index=True)
    ts: Mapped[datetime] = _ts()
    qty: Mapped[Decimal] = mapped_column(Qty)
    price: Mapped[Decimal] = mapped_column(Money)
    fees: Mapped[Decimal] = mapped_column(Money, default=0)
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=0)
    liquidity_flag: Mapped[str | None] = mapped_column(String(16), nullable=True)

    order: Mapped[Order] = relationship(back_populates="fills")


class EquitySnapshot(Base):
    """Timescale hypertable — the equity curve. PK includes ts (partition key)."""

    __tablename__ = "equity_snapshot"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), primary_key=True, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    cash: Mapped[Decimal] = mapped_column(Money)
    positions_value: Mapped[Decimal] = mapped_column(Money)
    total_equity: Mapped[Decimal] = mapped_column(Money)
    realized_pnl: Mapped[Decimal] = mapped_column(Money, default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Money, default=0)
    is_stale: Mapped[bool] = mapped_column(default=False)


class Strategy(Base):
    __tablename__ = "strategy"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    owner_type: Mapped[StrategyOwner] = mapped_column(
        Enum(StrategyOwner, name="strategy_owner"), default=StrategyOwner.house
    )
    owner_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[StrategyKind] = mapped_column(Enum(StrategyKind, name="strategy_kind"))
    spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[StrategyStatus] = mapped_column(
        Enum(StrategyStatus, name="strategy_status"), default=StrategyStatus.active
    )
    sandbox_policy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts()


class StrategySignal(Base):
    """Deterministic output the LLM management layer acts on. Also the audit
    trail behind marketplace/[redacted]."""

    __tablename__ = "strategy_signal"
    __table_args__ = (
        Index("ix_signal_agent_ts", "agent_ref", "ts"),
        Index("ix_signal_strategy_ts", "strategy_id", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategy.id"), index=True)
    # Soft link to game.agent.
    agent_ref: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = _ts()
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[SignalAction] = mapped_column(Enum(SignalAction, name="signal_action"))
    strength: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=0)
    features: Mapped[dict] = mapped_column(JSONB, default=dict)
