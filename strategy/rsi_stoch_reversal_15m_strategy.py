"""Versioned 15m RSI/Stoch reversal with 1h directional confirmation."""

from __future__ import annotations

from typing import Any, Dict

from strategy.rsi_stoch_reversal_5m_strategy import RsiStochReversal5mStrategy


class RsiStochReversal15mStrategy(RsiStochReversal5mStrategy):
    STRATEGY_NAME = "RSI StochRSI Reversal 15m"

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
        params.setdefault("entry_timeframe", "15m")
        params.setdefault("confirmation_timeframe", "1h")
        params.setdefault("require_confirmation", True)
        params.setdefault("target_timeframes", ["1h", "15m"])
        cfg["parameters"] = params
        super().__init__(
            cfg,
            exchange,
            database,
            redis_client=redis_client,
            exchange_name=exchange_name,
        )
