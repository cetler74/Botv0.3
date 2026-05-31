"""
RSI + StochRSI 5m — spot standalone long-only strategy.

Long (closed 5m bar): RSI(14) < 30, StochRSI %K and %D both < 30, and %K > %D.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.base_strategy import BaseStrategy
from strategy.playbooks.rsi_stoch_reversal_5m_engine import (
    evaluate_rsi_stoch_reversal_5m,
    params_from_config,
)


class RsiStochReversal5mStrategy(BaseStrategy):
    STRATEGY_NAME = "RSI StochRSI Reversal 5m"

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
        self._current_ohlcv = None

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
        data = self._resolve_market_data(
            market_data, self._engine_params.entry_timeframe
        )
        regime = str(getattr(self.state, "market_regime", "unknown") or "unknown")
        result = evaluate_rsi_stoch_reversal_5m(
            data,
            self._engine_params,
            market_regime=regime,
            allow_short=False,
            exchange_name=self.exchange_name,
        )
        self.state.indicators = dict(result.indicators)
        self.state.last_signal = result.signal if result.signal != "sell" else "hold"

        rsi_val = result.indicators.get("rsi")
        k_val = result.indicators.get("stoch_rsi_k")
        d_val = result.indicators.get("stoch_rsi_d")
        try:
            rsi_f = float(rsi_val) if rsi_val is not None else float("nan")
            k_f = float(k_val) if k_val is not None else float("nan")
            d_f = float(d_val) if d_val is not None else float("nan")
        except (TypeError, ValueError):
            rsi_f = k_f = d_f = float("nan")

        stoch_os = float(self._engine_params.stoch_oversold)
        rsi_os = float(self._engine_params.rsi_oversold)
        oversold_ok = rsi_f == rsi_f and rsi_f < rsi_os
        stoch_k_os_ok = k_f == k_f and k_f < stoch_os
        stoch_d_os_ok = d_f == d_f and d_f < stoch_os
        stoch_long_ok = (
            k_f == k_f and d_f == d_f and k_f > d_f and stoch_k_os_ok and stoch_d_os_ok
        )
        await self.log_condition_outcome(
            "rsi_oversold",
            oversold_ok,
            f"RSI={rsi_f:.2f} threshold<{rsi_os}",
        )
        await self.log_condition_outcome(
            "stoch_k_oversold",
            stoch_k_os_ok,
            f"K={k_f:.2f} threshold<{stoch_os}",
        )
        await self.log_condition_outcome(
            "stoch_d_oversold",
            stoch_d_os_ok,
            f"D={d_f:.2f} threshold<{stoch_os}",
        )
        await self.log_condition_outcome(
            "stoch_k_gt_d",
            k_f == k_f and d_f == d_f and k_f > d_f,
            f"K={k_f:.2f} D={d_f:.2f}",
        )

        if result.signal == "buy":
            return "buy", result.confidence, result.strength
        return "hold", 0.0, 0.0
