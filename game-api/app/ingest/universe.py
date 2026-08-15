"""Tradable universe for the game.

The active equity universe is a named symbol list under `ingest/symbols/*.txt`,
selected by the `PRICE_UNIVERSE` env (default `diversified`, ~150 sector-balanced
names). `russell1000` is wired in for TODO #4b. Crypto is a small always-on set.

Files: one ticker per line; blank lines and `#` comments ignored; `include <name>`
pulls in another list (for composing/scaling). See docs/08-market-data.md.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

_SYMBOLS_DIR = Path(__file__).parent / "symbols"

DEFAULT_CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD"]


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def _load_list(name: str, _seen: frozenset[str] = frozenset()) -> list[str]:
    """Load a symbol list file, resolving `include` directives (cycle-safe)."""
    if name in _seen:
        return []
    path = _SYMBOLS_DIR / f"{name}.txt"
    if not path.exists():
        return []
    out: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("include "):
            out.extend(_load_list(line.split(None, 1)[1].strip(), _seen | {name}))
        else:
            out.append(line.upper())
    # de-dupe, preserve order
    return list(dict.fromkeys(out))


@lru_cache(maxsize=8)
def equity_universe(name: str | None = None) -> tuple[str, ...]:
    name = name or os.getenv("PRICE_UNIVERSE", "diversified")
    symbols = _load_list(name)
    if not symbols:
        # Never leave ingest with an empty equity universe.
        symbols = _load_list("diversified")
    return tuple(symbols)


def default_universe() -> list[str]:
    return list(equity_universe()) + DEFAULT_CRYPTO


# --- Synthetic mode: deterministic per-symbol starting price ---------------
# No hand-maintained seed table; derive a plausible, stable price from the
# symbol so the random walk starts somewhere sensible without a lookup.
def seed_price(symbol: str) -> float:
    if is_crypto(symbol):
        base = {"BTC/USD": 115_000.0, "ETH/USD": 4_200.0, "SOL/USD": 210.0}
        if symbol in base:
            return base[symbol]
        return 100.0
    h = int(hashlib.sha256(symbol.encode()).hexdigest(), 16)
    # Spread equities across ~$20–$520 deterministically.
    return 20.0 + (h % 50_000) / 100.0
