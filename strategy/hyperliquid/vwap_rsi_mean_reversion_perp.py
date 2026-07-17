"""Hyperliquid perp — VWAP/RSI mean-reversion (long + short)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.hyperliquid.base_perp_strategy import BasePerpStrategy
from strategy.playbooks.vwap_rsi_mean_reversion_engine import (
    evaluate_vwap_rsi_mean_reversion,
    params_from_config,
)


class VwapRsiMeanReversionPerpStrategy(BasePerpStrategy):
    STRATEGY_NAME = "VWAP RSI Mean Reversion Perp"
    SUPPORTED_SIDES = ("long", "short")

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
        _params = config.get("parameters") or {}
        self.allow_long = bool(_params.get("allow_long", True))
        self.allow_short = bool(_params.get("allow_short", True))

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    @staticmethod
    def _resolve_market_data(market_data: Any, params) -> Dict[str, pd.DataFrame]:
        if isinstance(market_data, dict):
            return market_data
        if isinstance(market_data, pd.DataFrame):
            tf = str(params.entry_timeframe or "15m").lower()
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
        result = evaluate_vwap_rsi_mean_reversion(
            data,
            self._engine_params,
            market_regime=regime,
            allow_short=self.allow_short,
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
        params = self.config.get("parameters") or {}
        return float(params.get("position_size", 0.0) or 0.0)

    async def _should_exit_legacy(self) -> bool:
        return False
