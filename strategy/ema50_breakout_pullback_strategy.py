"""
EMA50 Breakout Pullback — spot standalone long-only strategy.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.base_strategy import BaseStrategy
from strategy.playbooks.ema50_breakout_pullback_engine import (
    evaluate_ema50_breakout_pullback,
    params_from_config,
)


class Ema50BreakoutPullbackStrategy(BaseStrategy):
    STRATEGY_NAME = "EMA50 Breakout Pullback"

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
        self._engine_params = params_from_config(config)

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    async def calculate_position_size(self, signal_type: str) -> float:
        params = self.config.get("parameters") or {}
        return float(params.get("position_size", 0.0) or 0.0)

    async def should_exit(self) -> bool:
        return False

    @staticmethod
    def _resolve_market_data(market_data: Any, params) -> Dict[str, pd.DataFrame]:
        if isinstance(market_data, dict):
            return market_data
        if isinstance(market_data, pd.DataFrame):
            tf = str(params.primary_timeframe or "4h").lower()
            return {tf: market_data}
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
        result = evaluate_ema50_breakout_pullback(
            data,
            self._engine_params,
            market_regime=regime,
            allow_short=False,
            asset_class="crypto",
        )
        self.state.indicators = dict(result.indicators)
        if result.signal == "buy":
            self.state.stop_loss = result.indicators.get("stop_hint")
            self.state.take_profit = result.indicators.get("target_hint")
            return "buy", result.confidence, result.strength
        return "hold", 0.0, 0.0
