"""
RSI Oversold Override strategy (15m only).

Independent override strategy:
- Evaluates only 15m RSI.
- Emits BUY whenever RSI is below threshold (default 30).
- Designed to be displayed as a separate strategy signal, while consensus
  integration is handled by strategy-service override logic.
"""

from typing import Dict, Any, Optional, Tuple
import logging

import pandas as pd
import pandas_ta as ta

from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class RSIOversoldOverrideStrategy(BaseStrategy):
    """Independent RSI-oversold strategy for aggressive long entries."""

    STRATEGY_NAME = "RSI Oversold Override"

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: Any,
        redis_client=None,
        exchange_name=None,
    ):
        super().__init__(config, exchange, database, redis_client)
        self.exchange_name = exchange_name or "binance"
        self.logger = logging.getLogger(__name__)

        params = config.get("parameters", {})
        self.rsi_period = int(params.get("rsi_period", 14))
        self.rsi_buy_threshold = float(params.get("rsi_buy_threshold", 30.0))
        self.buy_confidence = float(params.get("buy_confidence", 0.95))
        self.buy_strength = float(params.get("buy_strength", 0.90))

        self._current_ohlcv = None

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}
        self.logger.info(f"Initialized RSI Oversold Override strategy for {pair}")

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        try:
            if isinstance(market_data, dict):
                exec_df = market_data.get("15m")
                if exec_df is None:
                    exec_df = market_data.get("1h")
            else:
                exec_df = market_data

            if exec_df is None or len(exec_df) < max(self.rsi_period + 2, 20):
                return "hold", 0.0, 0.0

            required_cols = {"open", "high", "low", "close", "volume"}
            if not required_cols.issubset(exec_df.columns):
                return "hold", 0.0, 0.0

            rsi_series = ta.rsi(exec_df["close"], length=self.rsi_period)
            if rsi_series is None or len(rsi_series) == 0 or pd.isna(rsi_series.iloc[-1]):
                return "hold", 0.0, 0.0
            rsi_now = float(rsi_series.iloc[-1])

            signal = "buy" if rsi_now < self.rsi_buy_threshold else "hold"
            confidence = self.buy_confidence if signal == "buy" else 0.0
            strength = self.buy_strength if signal == "buy" else 0.0

            self.state.indicators.update(
                {
                    "rsi_15m": rsi_now,
                    "rsi_buy_threshold": self.rsi_buy_threshold,
                    "rsi_oversold_buy": bool(signal == "buy"),
                }
            )
            self.state.last_signal = signal

            reason = "rsi_15m_oversold_buy" if signal == "buy" else "rsi_not_oversold"
            self.logger.info(
                "[RSIOversoldOverrideStrategy] %s %s conf=%.2f strength=%.2f reason=%s rsi_15m=%.2f",
                pair or self.state.pair,
                signal.upper(),
                confidence,
                strength,
                reason,
                rsi_now,
            )
            return signal, float(confidence), float(strength)
        except Exception as e:
            self.logger.error(f"[RSIOversoldOverrideStrategy] Error generating signal: {e}")
            return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        if signal_type != "buy":
            return 0.0
        return 0.0

    async def should_exit(self) -> bool:
        return False

