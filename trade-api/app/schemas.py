from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator


class AccountCreate(BaseModel):
    external_ref: str
    starting_cash: Decimal
    base_currency: str = "USD"


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    external_ref: str
    base_currency: str
    backend_type: str
    cash_balance: Decimal
    buying_power: Decimal
    starting_equity: Decimal


class OrderCreate(BaseModel):
    symbol: str
    side: str  # buy | sell
    order_type: str = "market"  # market | limit | stop | stop_limit
    qty: Decimal | None = None
    notional: Decimal | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    tif: str = "day"
    asset_class: str = "equity"
    client_order_id: str
    source: str = "agent"
    source_ref: str | None = None
    # Anti-flip / stale-close safety: a close or cover sets this so the broker only
    # ever REDUCES the position (never opens or flips one). Opens leave it false.
    reduce_only: bool = False
    # Deterministic exit levels (fractions of avg entry), set on an open (buy or short).
    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None
    trailing_stop_pct: Decimal | None = None

    @model_validator(mode="after")
    def _one_of_qty_notional(self) -> "OrderCreate":
        if (self.qty is None) == (self.notional is None):
            raise ValueError("provide exactly one of qty or notional")
        if self.qty is not None and self.qty <= 0:
            raise ValueError("qty must be positive")
        if self.notional is not None and self.notional <= 0:
            raise ValueError("notional must be positive")
        if self.side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        return self


class FillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: datetime
    qty: Decimal
    price: Decimal
    fees: Decimal
    slippage_bps: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    qty: Decimal | None
    notional: Decimal | None
    status: str
    source: str
    reject_reason: str | None
    submitted_at: datetime
    filled_at: datetime | None


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    symbol: str
    asset_class: str
    qty: Decimal
    avg_entry_price: Decimal
    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    high_water_price: Decimal | None = None


class ExitOut(BaseModel):
    position_id: int
    account_ref: str
    symbol: str
    reason: str  # stop_loss | trailing_stop | take_profit
    qty: Decimal
    price: Decimal
    realized: Decimal


class EquityOut(BaseModel):
    account_id: int
    ts: datetime
    cash: Decimal
    positions_value: Decimal
    total_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal


# ---- strategies ----
class StrategyCreate(BaseModel):
    name: str
    kind: str  # rule_dsl | signal_fn | indicator_dsl
    spec: dict
    owner_type: str = "house"
    owner_ref: str | None = None
    version: int = 1
    status: str = "active"  # draft | active | retired


class StrategyStatusUpdate(BaseModel):
    status: str  # draft | active | retired


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    owner_type: str
    kind: str
    spec: dict
    version: int
    status: str


class EvaluateRequest(BaseModel):
    agent_ref: str
    symbols: list[str]
    timeframe: str = "1m"
    lookback: int = 200
    persist: bool = True


class SignalOut(BaseModel):
    symbol: str
    action: str
    strength: float
    features: dict


class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str = "1m"
    limit: int = 1000
    starting_cash: float = 10000.0


class BacktestWindow(BaseModel):
    start: datetime
    end: datetime


class AdhocBacktestRequest(BaseModel):
    """Backtest an ad-hoc spec WITHOUT persisting it (deep-agent iteration, TODO #3)."""
    kind: str = "indicator_dsl"
    spec: dict
    symbol: str
    starting_cash: float = 10000.0
    window: BacktestWindow | None = None  # calendar span for walk-forward


class BacktestResult(BaseModel):
    symbol: str
    bars: int
    trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    final_equity: float


class OptionsBacktestRequest(BaseModel):
    """Backtest a defined-risk option structure (v1) over one or more underlyings,
    WITHOUT persisting. `structure` is validated fail-closed by validate_structure."""
    structure: dict
    underlyings: list[str]
    starting_cash: float = 100_000.0
    window: BacktestWindow | None = None


class OptionsBacktestResult(BaseModel):
    archetype: str
    underlyings: list[str]
    trades: int
    win_rate: float
    total_return: float          # mean across underlyings
    max_drawdown: float          # worst across underlyings
    avg_credit: float
    avg_return_on_risk: float
    assignment_rate: float
    per_underlying: list[dict]
