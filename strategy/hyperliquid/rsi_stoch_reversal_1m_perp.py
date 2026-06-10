"""
Hyperliquid perp — RSI + StochRSI 1m replica.
"""

from __future__ import annotations

from typing import Any, Dict

from strategy.hyperliquid.rsi_stoch_reversal_5m_perp import (
    RsiStochReversal5mPerpStrategy,
)


class RsiStochReversal1mPerpStrategy(RsiStochReversal5mPerpStrategy):
    STRATEGY_NAME = "RSI StochRSI Reversal 1m Perp"
    SUPPORTED_SIDES = ("long", "short")

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: Any,
        redis_client=None,
        exchange_name=None,
    ):
        cfg = dict(config or {})
        params = dict(cfg.get("parameters") or {})
        params.setdefault("entry_timeframe", "1m")
        params.setdefault("target_timeframes", ["1m"])
        cfg["parameters"] = params
        super().__init__(
            cfg,
            exchange,
            database,
            redis_client=redis_client,
            exchange_name=exchange_name,
        )
