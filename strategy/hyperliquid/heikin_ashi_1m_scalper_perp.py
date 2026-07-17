"""Hyperliquid perp wrapper for the Heikin-Ashi 1m pullback/doji scalper."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.hyperliquid.base_perp_strategy import BasePerpStrategy
from strategy.playbooks.heikin_ashi_1m_scalper_engine import (
    evaluate_heikin_ashi_1m_scalper,
    params_from_config,
)


class HeikinAshi1mScalperPerpStrategy(BasePerpStrategy):
    STRATEGY_NAME = "Heikin Ashi 1m Scalper Perp"

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
        params = config.get("parameters") or {}
        self.allow_long = bool(params.get("allow_long", self._engine_params.allow_long))
        self.allow_short = bool(params.get("allow_short", self._engine_params.allow_short))
        self._position_size_hint = float(params.get("position_size", 0.0) or 0.0)

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}
        self.state.stop_loss = None
        self.state.take_profit = None

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    @staticmethod
    def _resolve_market_data(market_data: Any, params) -> Dict[str, pd.DataFrame]:
        if isinstance(market_data, dict):
            return market_data
        if isinstance(market_data, pd.DataFrame):
            return {str(params.entry_timeframe or "1m").lower(): market_data}
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
        result = evaluate_heikin_ashi_1m_scalper(
            data,
            self._engine_params,
            market_regime=str(getattr(self.state, "market_regime", "unknown") or "unknown"),
        )
        self.state.indicators = dict(result.indicators)
        if result.signal == "buy":
            if not self.allow_long:
                self.state.indicators["skip_reason"] = "long_disabled"
                return "hold", 0.0, 0.0
            self.state.stop_loss = result.indicators.get("stop_hint")
            self.state.take_profit = result.indicators.get("target_hint")
            return "long", result.confidence, result.strength
        if result.signal == "sell":
            if not self.allow_short:
                self.state.indicators["skip_reason"] = "short_disabled"
                return "hold", 0.0, 0.0
            self.state.stop_loss = result.indicators.get("stop_hint")
            self.state.take_profit = result.indicators.get("target_hint")
            return "short", result.confidence, result.strength
        return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        if signal_type in {"long", "buy"} and not self.allow_long:
            return 0.0
        if signal_type in {"short", "sell"} and not self.allow_short:
            return 0.0
        return self._position_size_hint

    async def _should_exit_legacy(self) -> bool:
        return False
