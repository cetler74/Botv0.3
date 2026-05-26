"""
Hyperliquid perp — SMA Reclaim Bull Flag (long-only).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.hyperliquid.base_perp_strategy import BasePerpStrategy
from strategy.indicators.session_filter import session_filter_from_params
from strategy.playbooks.sma_reclaim_bull_flag_engine import (
    evaluate_sma_reclaim_bull_flag,
    params_from_config,
)


class SmaReclaimBullFlagPerpStrategy(BasePerpStrategy):
    STRATEGY_NAME = "SMA Reclaim Bull Flag Perp"

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
        self._session_cfg = session_filter_from_params(dict(config.get("parameters") or {}))
        _params = config.get("parameters") or {}
        self.allow_long = bool(_params.get("allow_long", True))
        self.allow_short = bool(_params.get("allow_short", False))

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    @staticmethod
    def _resolve_market_data(market_data: Any, entry_tf: str) -> Dict[str, pd.DataFrame]:
        if isinstance(market_data, dict):
            return market_data
        if isinstance(market_data, pd.DataFrame):
            return {entry_tf: market_data}
        return {}

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        if not self.allow_long:
            self.state.indicators = {"invalidation_reason": "long_disabled"}
            return "hold", 0.0, 0.0
        data = self._resolve_market_data(market_data, self._engine_params.entry_timeframe)
        regime = str(getattr(self.state, "market_regime", "unknown") or "unknown")
        result = evaluate_sma_reclaim_bull_flag(
            data,
            self._engine_params,
            session_cfg=self._session_cfg,
            market_regime=regime,
        )
        self.state.indicators = dict(result.indicators)
        if result.signal == "buy":
            self.state.stop_loss = result.indicators.get("stop_hint")
            self.state.take_profit = result.indicators.get("target_hint")
            return "long", result.confidence, result.strength
        return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        if signal_type not in {"long", "buy"}:
            return 0.0
        hint = (self.state.indicators or {}).get("position_size_hint")
        if hint:
            return float(hint)
        return 0.0

    async def _should_exit_legacy(self) -> bool:
        return False
