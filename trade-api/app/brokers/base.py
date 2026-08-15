"""The pluggable Broker interface — the load-bearing seam of the whole system.

`SimBroker` implements it now; a future `AlpacaBroker` implements the SAME
interface against Alpaca's Broker API so the game layer never changes. Keep this
interface stable and backend-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from ..models import Account, EquitySnapshot, Order, Position
from ..schemas import OrderCreate


class Broker(ABC):
    @abstractmethod
    async def create_account(
        self, external_ref: str, starting_cash: Decimal, base_currency: str = "USD"
    ) -> Account: ...

    @abstractmethod
    async def get_account(self, account_id: int) -> Account | None: ...

    @abstractmethod
    async def get_account_by_ref(self, external_ref: str) -> Account | None: ...

    @abstractmethod
    async def place_order(self, account_id: int, req: OrderCreate) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: int) -> Order | None: ...

    @abstractmethod
    async def get_order(self, order_id: int) -> Order | None: ...

    @abstractmethod
    async def list_orders(
        self, account_id: int, limit: int = 100, offset: int = 0,
        exclude_rejected: bool = False,
    ) -> list[Order]: ...

    @abstractmethod
    async def list_positions(self, account_id: int) -> list[Position]: ...

    @abstractmethod
    async def mark_to_market(
        self, account_id: int, marks: dict[str, Decimal]
    ) -> EquitySnapshot: ...

    @abstractmethod
    async def settle_exits(self, tick_id: str = "monitor") -> list[dict]:
        """Enforce position-level stop-loss / take-profit / trailing exits."""
        ...
