"""indicator_dsl grammar — re-exported from the shared package.

The models + validation moved to `neuromancing_shared.strategy_spec` so BOTH services
use one grammar (trade-api validates/evaluates; game-api's deep agent emits structured
candidates). This module keeps the old import path (`app.strategies.spec`) working.
"""

from __future__ import annotations

from neuromancing_shared.strategy_spec import (  # noqa: F401
    ALLOWED_TIMEFRAMES,
    ARCHETYPES,
    CROSS,
    DICT_FIELDS,
    KNOWN_FNS,
    OPS,
    SOURCES,
    IndicatorSpec,
    StrategySpec,
    validate_spec,
)
