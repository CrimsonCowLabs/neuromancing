"""Async client for the internal game-api -> trade-api seam.

Carries the privileged service token for order-mutating calls. This token lives
only in server-side game-api / temporal-worker processes — never the browser/BFF.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_settings


class TradeClient:
    def __init__(self) -> None:
        s = get_settings()
        self._base = s.trade_api_url.rstrip("/")
        self._headers = {"X-Service-Token": s.trade_api_service_token}

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base, headers=self._headers, timeout=15.0)

    # ---- accounts ----
    async def create_account(self, external_ref: str, starting_cash: float) -> dict:
        async with await self._client() as c:
            r = await c.post("/accounts", json={"external_ref": external_ref,
                                                "starting_cash": str(starting_cash)})
            r.raise_for_status()
            return r.json()

    async def get_account_by_ref(self, external_ref: str) -> dict | None:
        async with await self._client() as c:
            r = await c.get(f"/accounts/by-ref/{external_ref}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    async def list_positions(self, account_id: int) -> list[dict]:
        async with await self._client() as c:
            r = await c.get(f"/accounts/{account_id}/positions")
            r.raise_for_status()
            return r.json()

    async def list_orders(
        self, account_id: int, limit: int = 50, offset: int = 0,
        exclude_rejected: bool = False,
    ) -> list[dict]:
        async with await self._client() as c:
            r = await c.get(
                f"/accounts/{account_id}/orders",
                params={"limit": limit, "offset": offset,
                        "exclude_rejected": str(exclude_rejected).lower()},
            )
            r.raise_for_status()
            return r.json()

    async def order_fills(self, account_id: int, order_id: int) -> list[dict]:
        async with await self._client() as c:
            r = await c.get(f"/accounts/{account_id}/orders/{order_id}/fills")
            r.raise_for_status()
            return r.json()

    async def place_order(self, account_id: int, order: dict[str, Any]) -> dict:
        async with await self._client() as c:
            r = await c.post(f"/accounts/{account_id}/orders", json=order)
            r.raise_for_status()
            return r.json()

    async def mark_to_market(self, account_id: int, marks: dict[str, str]) -> dict:
        async with await self._client() as c:
            r = await c.post(f"/accounts/{account_id}/mark-to-market", json={"marks": marks})
            r.raise_for_status()
            return r.json()

    async def settle_exits(self, tick_id: str = "monitor") -> list[dict]:
        async with await self._client() as c:
            r = await c.post("/positions/settle-exits", params={"tick_id": tick_id})
            r.raise_for_status()
            return r.json()

    # ---- strategies ----
    async def create_strategy(
        self, name: str, kind: str, spec: dict, *,
        owner_type: str = "house", owner_ref: str | None = None,
        version: int = 1, status: str = "active",
    ) -> dict:
        async with await self._client() as c:
            r = await c.post("/strategies", json={
                "name": name, "kind": kind, "spec": spec, "owner_type": owner_type,
                "owner_ref": owner_ref, "version": version, "status": status,
            })
            r.raise_for_status()
            return r.json()

    async def set_strategy_status(self, strategy_id: int, status: str) -> dict:
        async with await self._client() as c:
            r = await c.post(f"/strategies/{strategy_id}/status", json={"status": status})
            r.raise_for_status()
            return r.json()

    async def backtest_spec(
        self, spec: dict, symbol: str, *, kind: str = "indicator_dsl",
        starting_cash: float = 10000.0, window: tuple | None = None,
    ) -> dict:
        """Ad-hoc backtest of an unpersisted spec (deep-agent iteration). `window` is
        an optional (start, end) of ISO strings/datetimes for walk-forward."""
        body: dict = {"kind": kind, "spec": spec, "symbol": symbol,
                      "starting_cash": starting_cash}
        if window is not None:
            s, e = window
            body["window"] = {"start": s.isoformat() if hasattr(s, "isoformat") else s,
                              "end": e.isoformat() if hasattr(e, "isoformat") else e}
        async with await self._client() as c:
            r = await c.post("/strategies/backtest", json=body)
            r.raise_for_status()
            return r.json()

    async def options_backtest_spec(
        self, structure: dict, underlyings: list[str], *,
        starting_cash: float = 100000.0, window: tuple | None = None,
    ) -> dict:
        """Backtest a defined-risk option structure (v1) over one or more underlyings via
        the synthetic Black-Scholes chain (backtest-only; nothing persisted or traded)."""
        body: dict = {"structure": structure, "underlyings": underlyings,
                      "starting_cash": starting_cash}
        if window is not None:
            s, e = window
            body["window"] = {"start": s.isoformat() if hasattr(s, "isoformat") else s,
                              "end": e.isoformat() if hasattr(e, "isoformat") else e}
        async with await self._client() as c:
            r = await c.post("/strategies/options-backtest", json=body)
            r.raise_for_status()
            return r.json()

    async def seed_strategies(self) -> list[dict]:
        async with await self._client() as c:
            r = await c.post("/strategies/seed")
            r.raise_for_status()
            return r.json()

    async def list_strategies(self) -> list[dict]:
        async with await self._client() as c:
            r = await c.get("/strategies")
            r.raise_for_status()
            return r.json()

    async def evaluate_strategy(
        self, strategy_id: int, agent_ref: str, symbols: list[str],
        persist: bool = True, timeframe: str = "1m",
    ) -> list[dict]:
        async with await self._client() as c:
            r = await c.post(
                f"/strategies/{strategy_id}/evaluate",
                json={"agent_ref": agent_ref, "symbols": symbols,
                      "timeframe": timeframe, "persist": persist},
            )
            r.raise_for_status()
            return r.json()
