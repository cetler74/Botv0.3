"""
SMA Reclaim Bull Flag — spot standalone long strategy.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.base_strategy import BaseStrategy
from strategy.indicators.session_filter import session_filter_from_params
from strategy.playbooks.sma_reclaim_bull_flag_engine import (
    EngineParams,
    evaluate_sma_reclaim_bull_flag,
    params_from_config,
)


class SmaReclaimBullFlagStrategy(BaseStrategy):
    STRATEGY_NAME = "SMA Reclaim Bull Flag"

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: Any,
        redis_client=None,
        exchange_name=None,
    ):
        super().__init__(config, exchange, database, redis_client)
        self.exchange_name = exchange_name
        self._engine_params: EngineParams = params_from_config(config)
        self._session_cfg = session_filter_from_params(
            dict(config.get("parameters") or {}) if isinstance(config, dict) else {}
        )
        cfg_params = config.get("parameters") or {}
        self.entry_timeframe = str(cfg_params.get("entry_timeframe", "5m")).lower()
        self.execution_timeframe = str(cfg_params.get("execution_timeframe", "1m")).lower()
        self._current_ohlcv = None

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    async def calculate_position_size(self, signal_type: str) -> float:
        hint = (self.state.indicators or {}).get("position_size_hint")
        if hint:
            return float(hint)
        params = self.config.get("parameters") or {}
        return float(params.get("position_size", 0.0) or 0.0)

    async def should_exit(self) -> bool:
        return False

    @staticmethod
    def _resolve_market_data(market_data: Any, params: EngineParams) -> Dict[str, pd.DataFrame]:
        if isinstance(market_data, dict):
            return market_data
        if isinstance(market_data, pd.DataFrame):
            return {params.entry_timeframe: market_data}
        return {}

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        data = self._resolve_market_data(market_data, self._engine_params)
        regime = str(getattr(self.state, "market_regime", "unknown") or "unknown")
        result = evaluate_sma_reclaim_bull_flag(
            data,
            self._engine_params,
            session_cfg=self._session_cfg,
            market_regime=regime,
        )
        self.state.indicators = dict(result.indicators)
        self.state.last_signal = result.signal
        if result.signal == "buy":
            self.state.stop_loss = result.indicators.get("stop_hint")
            self.state.take_profit = result.indicators.get("target_hint")
            return "buy", result.confidence, result.strength
        return "hold", 0.0, 0.0
