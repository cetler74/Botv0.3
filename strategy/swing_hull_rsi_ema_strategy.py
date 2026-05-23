"""
Swing Hull/RSI/EMA long-only spot strategy (1h).

Port of TradingView "Swing Hull/rsi/EMA Strategy":
  - Hull direction (n1 > n2) + price cross-down through fast EMA for entry
  - RSI cross above exit level for structural long exit (orchestrator)
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import pandas_ta as ta

from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


def _wma(series: pd.Series, length: int) -> pd.Series:
    """Linearly-weighted moving average (Pine ta.wma)."""
    if series is None or len(series) == 0 or length <= 0:
        return pd.Series([], dtype=float)
    if not isinstance(series, pd.Series):
        series = pd.Series(series)
    weights = np.arange(1, length + 1, dtype=float)
    wsum = weights.sum()

    def _w(window):
        return float(np.dot(window, weights) / wsum)

    return series.rolling(window=length, min_periods=length).apply(_w, raw=True)


def compute_hull_n1_n2(close: pd.Series, period: int) -> Tuple[pd.Series, pd.Series]:
    """
    Pine Hull variant: n1 from current close; n2 from close shifted by 1 bar.
    longCondition = n1 > n2
    """
    n = max(1, int(period))
    half = max(1, int(round(n / 2)))
    sqn = max(1, int(round(math.sqrt(n))))
    close = close.astype(float)
    n2ma = 2.0 * _wma(close, half)
    nma = _wma(close, n)
    diff = n2ma - nma
    n1 = _wma(diff, sqn)
    close_lag = close.shift(1)
    n2ma1 = 2.0 * _wma(close_lag, half)
    nma1 = _wma(close_lag, n)
    diff1 = n2ma1 - nma1
    n2 = _wma(diff1, sqn)
    return n1, n2


class SwingHullRsiEmaStrategy(BaseStrategy):
    """Spot long-only swing entry on 1h; structural RSI exit via orchestrator."""

    STRATEGY_NAME = "Swing Hull RSI EMA"

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
        self._load_parameters(config.get("parameters", {}))

    def _load_parameters(self, params: Dict[str, Any]) -> None:
        self.execution_timeframe = str(params.get("execution_timeframe", "1h")).lower()
        self.hull_period = max(1, int(params.get("hull_period", 100)))
        self.rsi_period = max(2, int(params.get("rsi_period", 14)))
        self.rsi_exit_level = float(params.get("rsi_exit_level", 80.0))
        self.ema_fast_period = max(1, int(params.get("ema_fast_period", 50)))
        self.ema_slow_period = max(1, int(params.get("ema_slow_period", 100)))
        self.min_confidence = float(params.get("min_confidence", 0.62))
        self.buy_strength = float(params.get("buy_strength", 0.72))
        self.stop_loss_pct = float(params.get("stop_loss_pct", 0.03))
        br = params.get("blocked_regimes")
        if br is None:
            br = ["trending_down", "high_volatility"]
        self.blocked_regimes = {str(x).strip().lower() for x in br if str(x).strip()}
        ar = params.get("allowed_regimes")
        if ar is None:
            ar = []
        self.allowed_regimes = {str(x).strip().lower() for x in ar if str(x).strip()}
        ae = params.get("allowed_exchanges")
        if ae is None:
            ae = []
        self.allowed_exchanges = {str(x).strip().lower() for x in ae if str(x).strip()}
        self.orchestrator_structural_exit_enabled = bool(
            params.get("orchestrator_structural_exit_enabled", True)
        )
        self.structural_exit_timeframe = str(
            params.get("structural_exit_timeframe", "1h")
        ).lower()
        self.structural_exit_min_hold_minutes = float(
            params.get("structural_exit_min_hold_minutes", 60) or 0
        )

    def _regime_allowed(self, current_regime: str) -> bool:
        regime = str(current_regime or "unknown").strip().lower()
        if self.allowed_regimes:
            return regime in self.allowed_regimes
        return regime not in self.blocked_regimes

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}
        self.logger.info("Initialized SwingHullRsiEma for %s", pair)

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    @staticmethod
    def _df_or_none(obj: Any) -> Optional[pd.DataFrame]:
        if obj is None:
            return None
        if isinstance(obj, pd.DataFrame) and not obj.empty:
            return obj
        return None

    def _resolve_exec_df(self, market_data) -> Optional[pd.DataFrame]:
        if not isinstance(market_data, dict):
            return market_data if isinstance(market_data, pd.DataFrame) else None
        key = self.execution_timeframe
        df = self._df_or_none(market_data.get(key))
        if df is not None:
            return df
        for k in ("1h", "15m"):
            df = self._df_or_none(market_data.get(k))
            if df is not None:
                return df
        return None

    def _min_bars_required(self) -> int:
        return max(self.hull_period, self.ema_slow_period, self.rsi_period) + 30

    def _evaluate_entry_on_df(
        self, exec_df: pd.DataFrame, closed_idx: int = -2, prev_idx: int = -3
    ) -> Tuple[bool, Dict[str, Any]]:
        """Return (buy_ok, indicator snapshot) on closed bar indices."""
        close = exec_df["close"].astype(float)
        n1, n2 = compute_hull_n1_n2(close, self.hull_period)
        ema_fast = ta.ema(close, length=self.ema_fast_period)
        ema_slow = ta.ema(close, length=self.ema_slow_period)
        rsi = ta.rsi(close, length=self.rsi_period)

        if (
            pd.isna(n1.iloc[closed_idx])
            or pd.isna(n2.iloc[closed_idx])
            or pd.isna(ema_fast.iloc[closed_idx])
            or pd.isna(ema_fast.iloc[prev_idx])
        ):
            return False, {"reason": "insufficient_indicator_data"}

        hull_bullish = float(n1.iloc[closed_idx]) > float(n2.iloc[closed_idx])
        c_closed = float(close.iloc[closed_idx])
        c_prev = float(close.iloc[prev_idx])
        ef_closed = float(ema_fast.iloc[closed_idx])
        ef_prev = float(ema_fast.iloc[prev_idx])
        ema_cross_down = c_closed < ef_closed and c_prev > ef_prev

        rsi_closed = None
        if rsi is not None and len(rsi) > abs(closed_idx):
            rv = rsi.iloc[closed_idx]
            if not pd.isna(rv):
                rsi_closed = float(rv)

        es_closed = None
        if ema_slow is not None and not pd.isna(ema_slow.iloc[closed_idx]):
            es_closed = float(ema_slow.iloc[closed_idx])

        buy_ok = hull_bullish and ema_cross_down
        indicators = {
            "n1": float(n1.iloc[closed_idx]),
            "n2": float(n2.iloc[closed_idx]),
            "hull_bullish": hull_bullish,
            "ema_fast": ef_closed,
            "ema_slow": es_closed,
            "rsi": rsi_closed,
            "ema_cross_down": ema_cross_down,
            "close_closed": c_closed,
            "close_prev": c_prev,
            "exec_timeframe": self.execution_timeframe,
            "stop_loss_pct": self.stop_loss_pct,
        }
        return buy_ok, indicators

    def structural_long_exit_from_ohlcv(self, ohlcv: pd.DataFrame) -> Tuple[bool, Optional[str]]:
        """RSI crosses above rsi_exit_level on last closed bar (TV closeLong)."""
        try:
            if ohlcv is None or ohlcv.empty:
                return False, None
            min_len = max(self.rsi_period, 3) + 2
            if len(ohlcv) < min_len:
                return False, None
            close = ohlcv["close"].astype(float)
            rsi = ta.rsi(close, length=self.rsi_period)
            if rsi is None or len(rsi) < 3:
                return False, None
            closed_idx = -2
            prev_idx = -3
            r_prev = rsi.iloc[prev_idx]
            r_now = rsi.iloc[closed_idx]
            if pd.isna(r_prev) or pd.isna(r_now):
                return False, None
            level = self.rsi_exit_level
            if float(r_prev) <= level < float(r_now):
                return True, "rsi_exit_cross"
            return False, None
        except Exception as e:
            self.logger.debug(
                "[SwingHullRsiEma] structural_long_exit_from_ohlcv failed: %s", e
            )
            return False, None

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        try:
            self._apply_regime_overrides(getattr(self.state, "market_regime", None))
            current_regime = str(
                getattr(self.state, "market_regime", "unknown") or "unknown"
            ).lower()
            if self.allowed_exchanges and str(self.exchange_name or "").lower() not in self.allowed_exchanges:
                self.logger.info(
                    "[SwingHullRsiEma] %s HOLD: exchange=%s not in allowed_exchanges",
                    pair or self.state.pair,
                    self.exchange_name,
                )
                return "hold", 0.0, 0.0
            if not self._regime_allowed(current_regime):
                self.logger.info(
                    "[SwingHullRsiEma] %s HOLD: regime=%s blocked (allowed=%s blocked=%s)",
                    pair or self.state.pair,
                    current_regime,
                    sorted(self.allowed_regimes) if self.allowed_regimes else "any",
                    sorted(self.blocked_regimes),
                )
                return "hold", 0.0, 0.0

            exec_df = self._resolve_exec_df(market_data)
            min_len = self._min_bars_required()
            if exec_df is None or len(exec_df) < min_len:
                self.logger.info(
                    "[SwingHullRsiEma] %s HOLD: need >=%s bars, got %s",
                    pair or self.state.pair,
                    min_len,
                    len(exec_df) if exec_df is not None else 0,
                )
                return "hold", 0.0, 0.0

            if "close" not in exec_df.columns:
                return "hold", 0.0, 0.0

            buy_ok, indicators = self._evaluate_entry_on_df(exec_df)
            hull_bullish = indicators.get("hull_bullish", False)
            ema_cross_down = indicators.get("ema_cross_down", False)
            c_closed = indicators.get("close_closed", 0.0)
            ef = indicators.get("ema_fast", 0.0)

            entry_reason_detail = (
                f"Swing Hull/RSI/EMA ({self.execution_timeframe}, hull={self.hull_period}, "
                f"ema_fast={self.ema_fast_period}): on last closed bar "
                f"Hull bullish (n1={indicators.get('n1'):.8f} > n2={indicators.get('n2'):.8f})={hull_bullish}; "
                f"EMA cross-down (close {c_closed:.8f} < ema_fast {ef:.8f} after prior close above EMA)="
                f"{ema_cross_down}; RSI={indicators.get('rsi')}."
            )
            indicators["entry_reason_detail"] = entry_reason_detail
            self.state.indicators = indicators

            if buy_ok:
                self.state.last_signal = "buy"
                self.logger.info(
                    "[SwingHullRsiEma] %s BUY conf=%.2f str=%.2f hull_bullish=%s ema_cross_down=%s",
                    pair or self.state.pair,
                    self.min_confidence,
                    self.buy_strength,
                    hull_bullish,
                    ema_cross_down,
                )
                return "buy", float(self.min_confidence), float(self.buy_strength)

            hold_reason = []
            if not hull_bullish:
                hold_reason.append("hull_not_bullish")
            if not ema_cross_down:
                hold_reason.append("no_ema_cross_down")
            self.state.last_signal = "hold"
            self.logger.info(
                "[SwingHullRsiEma] %s HOLD: %s",
                pair or self.state.pair,
                ", ".join(hold_reason) or "conditions_not_met",
            )
            return "hold", 0.0, 0.0
        except Exception as e:
            self.logger.error("[SwingHullRsiEma] generate_signal error: %s", e)
            return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        if signal_type != "buy":
            return 0.0
        if not self.exchange:
            return 0.0
        try:
            balance = await self.exchange.get_balance()
            free_usd = float(balance.get("free", 0.0) or 0.0)
            ticker = await self.exchange.get_ticker(self.state.pair, self.exchange_name)
            last_price = float((ticker or {}).get("last", 0.0) or 0.0)
            if free_usd <= 0 or last_price <= 0:
                return 0.0
            risk_usd = free_usd * 0.01
            return max(0.0, risk_usd / last_price)
        except Exception as e:
            self.logger.debug("[SwingHullRsiEma] position size 0: %s", e)
            return 0.0

    async def should_exit(self) -> bool:
        return False
