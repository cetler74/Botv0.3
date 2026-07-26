"""Hyperliquid perp — daily box break + retest (long + short, shadow-first)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.hyperliquid.base_perp_strategy import BasePerpStrategy
from strategy.playbooks.daily_box_break_retest_engine import (
    evaluate_daily_box_break_retest,
    params_from_config,
)


class DailyBoxBreakRetestPerpStrategy(BasePerpStrategy):
    STRATEGY_NAME = "Daily Box Break Retest Perp"
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
            tf = str(params.entry_timeframe or "1h").lower()
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
        result = evaluate_daily_box_break_retest(
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
