"""Reset the game to a clean slate — wipes all trading + game state and re-seeds.

Use when the price basis is contaminated (e.g. positions opened on the synthetic
feed then revalued on real Alpaca prices), OR to reseed the roster under new
identities. Truncates both schemas' stateful tables + relevant Redis keys, then
re-seeds the roster.

Pass `--keep-prices` to PRESERVE the price store (Redis `bars:*`/`quote:*` + the
Parquet archive) — market data is keyed by symbol, independent of the truncated
agent/account state, so a rename/reseed needn't throw away the deep #4a/#4b
history and force a full re-backfill. Without it, prices are wiped too (basis reset).

After running: re-register schedules (and, WITHOUT --keep-prices, restart
market-ingest to re-backfill real bars).

Run: `uv run python -m app.reset [--keep-prices]`  (DESTRUCTIVE)
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from neuromancing_shared.redisio import make_redis

from .config import get_settings
from .db import SessionLocal
from .seed import main as seed_main

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuromancing.reset")

# Order doesn't matter with CASCADE, but list every stateful table explicitly.
TRUNCATE_SQL = """
TRUNCATE
  game.agent_decision, game.agent_post, game.post_reaction, game.feed_event,
  game.leaderboard_snapshot, game.donation_tx, game.donation_balance,
  game.donation_address, game.agent, game.persona,
  trade.fill, trade."order", trade.strategy_signal, trade.equity_snapshot,
  trade.position, trade.account, trade.strategy
RESTART IDENTITY CASCADE;
"""


async def main(keep_prices: bool = False) -> None:
    async with SessionLocal() as session:
        await session.execute(text(TRUNCATE_SQL))
        await session.commit()
    log.info("truncated all game + trade state")

    redis = make_redis(get_settings().redis_url)
    # Game/LLM state is always cleared; price data (bars/quotes) only when NOT keeping prices.
    patterns = ["leaderboard:*", "llm:tok:*", "llm:fallback:*"]
    if not keep_prices:
        patterns = ["quote:*", "bars:*", *patterns]
    for pattern in patterns:
        keys = [k async for k in redis.scan_iter(match=pattern)]
        if keys:
            await redis.delete(*keys)
    await redis.delete("leaderboard:z", "leaderboard:current")
    log.info("cleared Redis leaderboard/llm keys%s", "" if keep_prices else " + quote/bars")

    if not keep_prices:
        # Wipe the Parquet archive so a reset truly re-backfills from scratch.
        import shutil
        from pathlib import Path

        bardata = Path(get_settings().bardata_dir)
        if bardata.exists():
            for child in bardata.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
            log.info("cleared Parquet archive under %s", bardata)
    else:
        log.info("kept price store (Parquet archive + Redis bars) — no re-backfill needed")

    await seed_main()
    log.info("reseeded roster. Now: %sre-run schedules.",
             "" if keep_prices else "restart market-ingest, then ")


if __name__ == "__main__":
    import sys

    asyncio.run(main(keep_prices="--keep-prices" in sys.argv))
