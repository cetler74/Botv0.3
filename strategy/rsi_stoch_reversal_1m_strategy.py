"""
RSI + StochRSI 1m — spot standalone long-only strategy.

Uses the same shared RSI/Stoch reversal engine as the 5m spot and perp
strategies, but defaults the entry timeframe to 1m. Spot remains long-only.
"""

from __future__ import annotations

from typing import Any, Dict

from strategy.rsi_stoch_reversal_5m_strategy import RsiStochReversal5mStrategy


class RsiStochReversal1mStrategy(RsiStochReversal5mStrategy):
    STRATEGY_NAME = "RSI StochRSI Reversal 1m"

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
        cfg["parameters"] = params
        super().__init__(
            cfg,
            exchange,
            database,
            redis_client=redis_client,
            exchange_name=exchange_name,
        )
