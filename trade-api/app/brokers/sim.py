"""SimBroker — fills simulated orders against real Alpaca prices (via Redis
quotes written by market-ingest). Long-only, cash account (buying_power == cash).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from neuromancing_shared.money import D, ZERO, quantize_money

from ..config import get_settings
from ..engine.matching import compute_fill, evaluate_exit, limit_stop_crosses
from ..engine.portfolio import (
    PositionState,
    apply_fill,
    marks_are_stale,
    positions_value,
    short_collateral,
)
from ..models import (
    Account,
    AssetClass,
    BackendType,
    EquitySnapshot,
    Fill,
    Order,
    OrderSide,
    OrderSource,
    OrderStatus,
    OrderType,
    Position,
    TIF,
)
from ..prices import Quote, get_quote
from ..schemas import OrderCreate

_RESTING_TYPES = {"limit", "stop", "stop_limit"}
# Clamp ranges for LLM-supplied exit levels (fractions of avg entry).
_PCT_MAX = {"stop_loss_pct": D("0.9"), "take_profit_pct": D("5"), "trailing_stop_pct": D("0.9")}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_pct(value, hi: Decimal) -> Decimal | None:
    """Sanitize an exit percent: None/≤0 → None; else min(value, hi)."""
    if value is None:
        return None
    v = D(value)
    if v <= 0:
        return None
    return min(v, hi)


class SimBroker:
    def __init__(self, session: AsyncSession, redis) -> None:
        self.session = session
        self.redis = redis

    # ---- accounts ----
    async def create_account(
        self, external_ref: str, starting_cash: Decimal, base_currency: str = "USD"
    ) -> Account:
        cash = quantize_money(starting_cash)
        acct = Account(
            external_ref=external_ref,
            base_currency=base_currency,
            backend_type=BackendType.sim,
            cash_balance=cash,
            buying_power=cash,
            starting_equity=cash,
        )
        self.session.add(acct)
        await self.session.flush()
        return acct

    async def get_account(self, account_id: int) -> Account | None:
        return await self.session.get(Account, account_id)

    async def get_account_by_ref(self, external_ref: str) -> Account | None:
        res = await self.session.execute(
            select(Account).where(Account.external_ref == external_ref)
        )
        return res.scalar_one_or_none()

    # ---- positions ----
    async def _get_position(self, account_id: int, symbol: str) -> Position | None:
        res = await self.session.execute(
            select(Position).where(
                Position.account_id == account_id, Position.symbol == symbol
            )
        )
        return res.scalar_one_or_none()

    async def list_positions(self, account_id: int) -> list[Position]:
        res = await self.session.execute(
            select(Position).where(Position.account_id == account_id)
        )
        return list(res.scalars().all())

    # ---- orders ----
    async def get_order(self, order_id: int) -> Order | None:
        return await self.session.get(Order, order_id)

    async def list_orders(
        self, account_id: int, limit: int = 100, offset: int = 0,
        exclude_rejected: bool = False,
    ) -> list[Order]:
        stmt = select(Order).where(Order.account_id == account_id)
        if exclude_rejected:
            stmt = stmt.where(Order.status != OrderStatus.rejected)
        stmt = stmt.order_by(Order.submitted_at.desc()).offset(offset).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def _existing(self, account_id: int, client_order_id: str) -> Order | None:
        res = await self.session.execute(
            select(Order).where(
                Order.account_id == account_id,
                Order.client_order_id == client_order_id,
            )
        )
        return res.scalar_one_or_none()

    async def _lock_account(self, account_id: int) -> Account | None:
        """Row-lock the account for the duration of a mutating transaction, so the
        decision tick and the position-monitor never race on cash/positions."""
        res = await self.session.execute(
            select(Account).where(Account.id == account_id).with_for_update()
        )
        return res.scalar_one_or_none()

    async def place_order(self, account_id: int, req: OrderCreate) -> Order:
        # Idempotency: a repeated client_order_id returns the original order.
        existing = await self._existing(account_id, req.client_order_id)
        if existing is not None:
            return existing

        # Lock the account row for the duration of the fill so a concurrent
        # position-monitor exit (or another order) can't lose an update / overspend.
        account = await self._lock_account(account_id)
        if account is None:
            raise ValueError("account not found")

        order = Order(
            account_id=account_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            asset_class=AssetClass(req.asset_class),
            side=OrderSide(req.side),
            order_type=OrderType(req.order_type),
            qty=req.qty,
            notional=req.notional,
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            tif=TIF(req.tif),
            source=req.source,  # type: ignore[arg-type]
            source_ref=req.source_ref,
            reduce_only=req.reduce_only,
            stop_loss_pct=_clamp_pct(req.stop_loss_pct, _PCT_MAX["stop_loss_pct"]),
            take_profit_pct=_clamp_pct(req.take_profit_pct, _PCT_MAX["take_profit_pct"]),
            trailing_stop_pct=_clamp_pct(req.trailing_stop_pct, _PCT_MAX["trailing_stop_pct"]),
            status=OrderStatus.pending,
        )
        self.session.add(order)
        await self.session.flush()

        quote = await get_quote(self.redis, req.symbol)
        if quote is None or quote.stale:
            return await self._reject(order, "no fresh price for symbol")

        if req.order_type in _RESTING_TYPES:
            # Resting orders wait for the poller to detect a price cross.
            order.status = OrderStatus.accepted
            await self.session.flush()
            return order

        return await self._fill_market(account, order, quote)

    async def _reject(self, order: Order, reason: str) -> Order:
        order.status = OrderStatus.rejected
        order.reject_reason = reason[:256]
        await self.session.flush()
        return order

    async def _fill_market(self, account: Account, order: Order, quote: Quote) -> Order:
        fq = compute_fill(
            quote=quote,
            side=order.side.value,
            asset_class=order.asset_class.value,
            qty=order.qty,
            notional=order.notional,
        )

        position = await self._get_position(order.account_id, order.symbol)
        prev_qty = D(position.qty) if position else ZERO  # signed qty before this fill
        pos_state = PositionState(
            qty=prev_qty,
            avg_entry=D(position.avg_entry_price) if position else ZERO,
        )

        buy = order.side == OrderSide.buy
        # A fill OPENS/increases when buy&flat/long or sell&flat/short; else it reduces.
        is_open = (buy and prev_qty >= 0) or (not buy and prev_qty <= 0)
        # reduce_only is the anti-flip / stale-close safety catch: it must only reduce.
        if getattr(order, "reduce_only", False) and (is_open or prev_qty == 0):
            return await self._reject(order, "reduce_only: nothing to reduce")

        # Direction-aware affordability (defense in depth beyond game-api guardrails).
        if is_open:
            if buy:  # buy-to-open long spends cash
                cost = quantize_money(fq.qty * fq.price + fq.fees)
                if cost > D(account.buying_power):
                    return await self._reject(order, "insufficient buying power")
            else:    # sell-to-open short reserves reg-T collateral
                mult = D(get_settings().short_collateral_mult)
                reserve = quantize_money(fq.qty * fq.price * mult)
                if reserve > D(account.buying_power):
                    return await self._reject(order, "insufficient buying power (short collateral)")

        outcome = apply_fill(
            cash=account.cash_balance,
            position=pos_state,
            side=order.side.value,
            qty=fq.qty,
            price=fq.price,
            fees=fq.fees,
        )
        filled = outcome.filled_qty  # a closing fill is capped at the held qty (no flip)

        self.session.add(
            Fill(order_id=order.id, ts=_now(), qty=filled, price=fq.price,
                 fees=outcome.fee, slippage_bps=fq.slippage_bps)  # prorated if the close was capped
        )
        # Upsert position.
        if position is None:
            position = Position(
                account_id=order.account_id, symbol=order.symbol,
                asset_class=order.asset_class, qty=outcome.position.qty,
                avg_entry_price=outcome.position.avg_entry,
            )
            self.session.add(position)
        else:
            position.qty = outcome.position.qty
            position.avg_entry_price = outcome.position.avg_entry

        # Transfer deterministic exit config on OPENS (long via buy, short via sell).
        if is_open:
            if prev_qty == 0:
                # Fresh open from flat — reset; never inherit a closed position's levels.
                position.stop_loss_pct = order.stop_loss_pct
                position.take_profit_pct = order.take_profit_pct
                position.trailing_stop_pct = order.trailing_stop_pct
                position.high_water_price = fq.price
            else:
                # Increasing an existing position — override only what's specified, ratchet
                # the favorable extreme (max for a long, min for a short).
                if order.stop_loss_pct is not None:
                    position.stop_loss_pct = order.stop_loss_pct
                if order.take_profit_pct is not None:
                    position.take_profit_pct = order.take_profit_pct
                if order.trailing_stop_pct is not None:
                    position.trailing_stop_pct = order.trailing_stop_pct
                wm = D(position.high_water_price) if position.high_water_price is not None else fq.price
                position.high_water_price = min(wm, fq.price) if outcome.position.qty < 0 else max(wm, fq.price)

        # A position closed back to flat leaves no meaningful row — delete it so it doesn't
        # linger as qty=0 clutter. A re-open later starts clean (_get_position → None).
        # went_flat implies prev_qty != 0, so the row genuinely existed.
        if outcome.position.qty == 0:
            await self.session.delete(position)

        # Update cash, then recompute buying power = cash − reserved short collateral.
        account.cash_balance = outcome.cash
        await self.session.flush()  # so the position delete/upsert is visible to the sum
        positions = await self.list_positions(order.account_id)
        reserved = short_collateral(
            [(p.qty, p.avg_entry_price) for p in positions], get_settings().short_collateral_mult)
        account.buying_power = quantize_money(D(outcome.cash) - reserved)

        order.status = OrderStatus.filled
        order.filled_at = _now()
        await self.session.flush()
        return order

    async def cancel_order(self, order_id: int) -> Order | None:
        order = await self.get_order(order_id)
        if order is None:
            return None
        if order.status in (OrderStatus.accepted, OrderStatus.pending):
            order.status = OrderStatus.canceled
            await self.session.flush()
        return order

    async def settle_exits(self, tick_id: str = "monitor") -> list[dict]:
        """Deterministically exit long positions whose stop / trailing-stop /
        take-profit level has crossed, using FRESH quotes only (stale/missing →
        skip, so after-hours equities aren't touched). Locks per account (account
        row first) for a consistent order with place_order. Returns the exits."""
        res = await self.session.execute(
            select(Position).where(
                Position.qty != 0,
                or_(
                    Position.stop_loss_pct.isnot(None),
                    Position.take_profit_pct.isnot(None),
                    Position.trailing_stop_pct.isnot(None),
                ),
            )
        )
        by_account: dict[int, list[Position]] = {}
        for p in res.scalars().all():
            by_account.setdefault(p.account_id, []).append(p)

        exits: list[dict] = []
        for account_id in sorted(by_account):
            account = await self._lock_account(account_id)
            if account is None:
                continue
            for p in sorted(by_account[account_id], key=lambda x: x.id):
                await self.session.refresh(p)  # fresh state under the lock
                pos_qty = D(p.qty)
                if pos_qty == 0:
                    continue
                quote = await get_quote(self.redis, p.symbol)
                if quote is None or quote.stale:
                    continue
                decision = evaluate_exit(
                    avg_entry=p.avg_entry_price,
                    last=quote.last,
                    qty=pos_qty,
                    high_water=p.high_water_price,
                    stop_loss_pct=p.stop_loss_pct,
                    take_profit_pct=p.take_profit_pct,
                    trailing_stop_pct=p.trailing_stop_pct,
                )
                p.high_water_price = decision.high_water  # persist ratchet either way
                if not decision.should_exit:
                    continue

                pid, psym = p.id, p.symbol  # capture — the fill deletes the flat row
                coid = f"exit:{pid}:{tick_id}"
                if await self._existing(account_id, coid) is not None:
                    continue  # idempotent — already exited this tick
                avg_entry_before = D(p.avg_entry_price)
                is_short = pos_qty < 0
                exit_qty = -pos_qty if is_short else pos_qty  # positive fill qty
                exit_side = OrderSide.buy if is_short else OrderSide.sell  # cover vs sell
                order = Order(
                    account_id=account_id, client_order_id=coid, symbol=psym,
                    asset_class=p.asset_class, side=exit_side,
                    order_type=OrderType.market, qty=exit_qty, tif=TIF.day,
                    source=OrderSource.agent, source_ref=f"exit:{decision.reason}",
                    reduce_only=True, status=OrderStatus.pending,
                )
                self.session.add(order)
                await self.session.flush()
                await self._fill_market(account, order, quote)
                # (the now-flat position row was deleted by the fill — no cleanup needed)

                fill = (await self.session.execute(
                    select(Fill).where(Fill.order_id == order.id)
                )).scalar_one_or_none()
                exit_price = D(fill.price) if fill else quote.last
                fees = D(fill.fees) if fill else ZERO
                # Signed realized: long = qty·(exit−entry), short = qty·(entry−exit).
                realized = (exit_qty * (avg_entry_before - exit_price) if is_short
                            else exit_qty * (exit_price - avg_entry_before)) - fees
                exits.append({
                    "position_id": pid, "account_ref": account.external_ref,
                    "symbol": psym, "reason": decision.reason, "side": exit_side.value,
                    "qty": str(exit_qty), "price": str(exit_price),
                    "realized": str(quantize_money(realized)),
                })
            await self.session.flush()
        return exits

    async def settle_resting(self, symbol: str) -> int:
        """Fill any resting orders on `symbol` whose price has crossed. Returns
        the number filled. Called by the resting-order poller on new quotes."""
        quote = await get_quote(self.redis, symbol)
        if quote is None or quote.stale:
            return 0
        res = await self.session.execute(
            select(Order).where(
                Order.symbol == symbol, Order.status == OrderStatus.accepted
            )
        )
        filled = 0
        for order in res.scalars().all():
            if limit_stop_crosses(
                order_type=order.order_type.value,
                side=order.side.value,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                quote=quote,
            ):
                account = await self.get_account(order.account_id)
                await self._fill_market(account, order, quote)
                filled += 1
        return filled

    async def mark_to_market(
        self, account_id: int, marks: dict[str, Decimal], feed_age_s: float | None = None
    ) -> EquitySnapshot:
        account = await self.get_account(account_id)
        positions = await self.list_positions(account_id)
        pos_map = {p.symbol: D(p.qty) for p in positions}
        pv = positions_value(marks, pos_map)
        cost_basis = quantize_money(sum((D(p.qty) * D(p.avg_entry_price) for p in positions), ZERO))
        cash = D(account.cash_balance)
        total = quantize_money(cash + pv)
        unrealized = quantize_money(pv - cost_basis)
        realized = quantize_money(total - D(account.starting_equity) - unrealized)
        snap = EquitySnapshot(
            account_id=account_id,
            ts=_now(),
            cash=cash,
            positions_value=pv,
            total_equity=total,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            is_stale=marks_are_stale(
                marks, pos_map, feed_age_s, get_settings().ingest_health_stale_s
            ),
        )
        self.session.add(snap)
        await self.session.flush()
        return snap
