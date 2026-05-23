"""
Kivanc SuperTrend long-only spot strategy (configurable TF; default 1h execution).

Bullish SuperTrend flip (trend -1 -> 1) on execution_timeframe is the entry trigger.
Optional trend_timeframe filter when require_1h_bullish_trend is true and TFs differ.
Bearish flips are not used for exit (orchestrator-managed exits only).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import pandas_ta as ta

from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


def compute_supertrend_kivanc(
    df: pd.DataFrame,
    atr_period: int = 10,
    atr_multiplier: float = 3.0,
    use_standard_atr: bool = True,
    source: str = "hl2",
) -> pd.DataFrame:
    """
    Port of KivancOzbilgic SuperTrend (Pine v4) — ratcheting bands and trend state.

    Returns DataFrame with columns: src, atr, up, dn, trend, bullish_flip, bearish_flip.
    """
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    n = len(close)
    if n < 2:
        empty = pd.DataFrame(index=df.index)
        for col in ("src", "atr", "up", "dn", "trend", "bullish_flip", "bearish_flip"):
            empty[col] = np.nan
        return empty

    if source.lower() == "hl2":
        src = (high + low) / 2.0
    elif source.lower() == "hlc3":
        src = (high + low + close) / 3.0
    elif source.lower() == "close":
        src = close.copy()
    else:
        src = (high + low) / 2.0

    if use_standard_atr:
        atr_series = ta.atr(
            pd.Series(high, index=df.index),
            pd.Series(low, index=df.index),
            pd.Series(close, index=df.index),
            length=atr_period,
        )
        atr_vals = atr_series.values if atr_series is not None else np.full(n, np.nan)
    else:
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum(
            high - low,
            np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
        )
        atr_vals = pd.Series(tr).rolling(atr_period, min_periods=atr_period).mean().values

    up = np.zeros(n, dtype=float)
    dn = np.zeros(n, dtype=float)
    trend = np.ones(n, dtype=int)

    for i in range(n):
        atr_i = atr_vals[i]
        if np.isnan(atr_i) or atr_i <= 0:
            up[i] = src[i] - atr_multiplier * (atr_vals[max(0, i - 1)] if i > 0 else atr_i or 0)
            dn[i] = src[i] + atr_multiplier * (atr_vals[max(0, i - 1)] if i > 0 else atr_i or 0)
            trend[i] = trend[i - 1] if i > 0 else 1
            continue

        up_raw = src[i] - atr_multiplier * atr_i
        dn_raw = src[i] + atr_multiplier * atr_i

        if i == 0:
            up[i] = up_raw
            dn[i] = dn_raw
            trend[i] = 1
            continue

        up1 = up[i - 1]
        dn1 = dn[i - 1]
        up[i] = max(up_raw, up1) if close[i - 1] > up1 else up_raw
        dn[i] = min(dn_raw, dn1) if close[i - 1] < dn1 else dn_raw

        prev_trend = trend[i - 1]
        if prev_trend == -1 and close[i] > dn1:
            trend[i] = 1
        elif prev_trend == 1 and close[i] < up1:
            trend[i] = -1
        else:
            trend[i] = prev_trend

    bullish_flip = np.zeros(n, dtype=bool)
    bearish_flip = np.zeros(n, dtype=bool)
    for i in range(1, n):
        bullish_flip[i] = trend[i] == 1 and trend[i - 1] == -1
        bearish_flip[i] = trend[i] == -1 and trend[i - 1] == 1

    return pd.DataFrame(
        {
            "src": src,
            "atr": atr_vals,
            "up": up,
            "dn": dn,
            "trend": trend,
            "bullish_flip": bullish_flip,
            "bearish_flip": bearish_flip,
        },
        index=df.index,
    )


class SuperTrendStrategy(BaseStrategy):
    """Spot long-only SuperTrend flip entry; exits are orchestrator-managed."""

    STRATEGY_NAME = "SuperTrend"

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
        self.execution_timeframe = str(params.get("execution_timeframe", "15m")).lower()
        self.trend_timeframe = str(params.get("trend_timeframe", "1h")).lower()
        self.require_1h_bullish_trend = bool(params.get("require_1h_bullish_trend", True))
        self.atr_period = max(1, int(params.get("atr_period", 10)))
        self.atr_multiplier = float(params.get("atr_multiplier", 3.0))
        self.source = str(params.get("source", "hl2"))
        self.use_standard_atr = bool(params.get("use_standard_atr", True))
        self.min_confidence = float(params.get("min_confidence", 0.60))
        self.buy_strength = float(params.get("buy_strength", 0.70))
        ar = params.get("allowed_regimes")
        if ar is None:
            ar = []
        self.allowed_regimes = {str(x).strip().lower() for x in ar if str(x).strip()}
        br = params.get("blocked_regimes")
        if br is None:
            br = []
        self.blocked_regimes = {str(x).strip().lower() for x in br if str(x).strip()}
        ae = params.get("allowed_exchanges")
        if ae is None:
            ae = []
        self.allowed_exchanges = {str(x).strip().lower() for x in ae if str(x).strip()}
        self.require_close_above_dn = bool(params.get("require_close_above_dn", True))
        self.confirm_bullish_bars = max(1, int(params.get("confirm_bullish_bars", 1)))
        self.min_adx = float(params.get("min_adx", 22.0))
        self.adx_period = max(1, int(params.get("adx_period", 14)))
        self.require_volume_confirmation = bool(
            params.get("require_volume_confirmation", True)
        )
        self.volume_window = max(2, int(params.get("volume_window", 20)))
        self.volume_multiplier = float(params.get("volume_multiplier", 1.05))

        self._current_ohlcv = None

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}
        self.logger.info("Initialized SuperTrend for %s", pair)

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

    def _resolve_tf_df(self, market_data, timeframe: str) -> Optional[pd.DataFrame]:
        if not isinstance(market_data, dict):
            if timeframe == self.execution_timeframe:
                return market_data if isinstance(market_data, pd.DataFrame) else None
            return None
        tf = str(timeframe or "").lower()
        df = self._df_or_none(market_data.get(tf))
        if df is not None:
            return df
        fallbacks = ("15m", "1h") if tf in ("15m", "1h") else (tf,)
        for k in fallbacks:
            df = self._df_or_none(market_data.get(k))
            if df is not None:
                return df
        return None

    def _resolve_exec_df(self, market_data) -> Optional[pd.DataFrame]:
        return self._resolve_tf_df(market_data, self.execution_timeframe)

    def _resolve_trend_df(self, market_data) -> Optional[pd.DataFrame]:
        return self._resolve_tf_df(market_data, self.trend_timeframe)

    def _supertrend_at_closed(
        self, df: pd.DataFrame
    ) -> Optional[Tuple[int, int, bool, pd.DataFrame]]:
        """Return (trend_now, trend_prev, bullish_flip, st_df) on last closed bar."""
        min_len = max(
            self.atr_period + 5,
            self.adx_period + 5,
            self.volume_window + 3,
        )
        if df is None or len(df) < min_len:
            return None
        required = {"high", "low", "close"}
        if not required.issubset(df.columns):
            return None
        st = compute_supertrend_kivanc(
            df,
            atr_period=self.atr_period,
            atr_multiplier=self.atr_multiplier,
            use_standard_atr=self.use_standard_atr,
            source=self.source,
        )
        if len(st) < 3:
            return None
        closed_idx = -2
        prev_idx = -3
        if pd.isna(st["trend"].iloc[closed_idx]) or pd.isna(st["trend"].iloc[prev_idx]):
            return None
        trend_now = int(st["trend"].iloc[closed_idx])
        trend_prev = int(st["trend"].iloc[prev_idx])
        bullish_flip = bool(trend_now == 1 and trend_prev == -1)
        return trend_now, trend_prev, bullish_flip, st

    def _adx_at_index(self, exec_df: pd.DataFrame, idx: int) -> Optional[float]:
        try:
            adx_df = ta.adx(
                high=exec_df["high"],
                low=exec_df["low"],
                close=exec_df["close"],
                length=self.adx_period,
            )
            if adx_df is None or adx_df.empty:
                return None
            adx_col = next((c for c in adx_df.columns if str(c).startswith("ADX")), None)
            if not adx_col:
                return None
            val = float(adx_df[adx_col].iloc[idx])
            return val if not pd.isna(val) else None
        except Exception as e:
            self.logger.debug("[SuperTrend] ADX calc failed: %s", e)
            return None

    def _volume_ok_at_index(self, exec_df: pd.DataFrame, idx: int) -> bool:
        if not self.require_volume_confirmation:
            return True
        if "volume" not in exec_df.columns:
            return False
        vol = exec_df["volume"].astype(float)
        if len(vol) < self.volume_window:
            return False
        closed_vol = float(vol.iloc[idx])
        if pd.isna(closed_vol) or closed_vol <= 0:
            return False
        start = max(0, idx - self.volume_window + 1)
        avg_vol = float(vol.iloc[start : idx + 1].mean())
        if avg_vol <= 0:
            return False
        return closed_vol >= self.volume_multiplier * avg_vol

    def _regime_allowed(self, current_regime: str) -> bool:
        regime = str(current_regime or "unknown").strip().lower()
        if self.allowed_regimes:
            return regime in self.allowed_regimes
        return regime not in self.blocked_regimes

    def _exchange_allowed(self) -> bool:
        if not self.allowed_exchanges:
            return True
        ex = str(self.exchange_name or "").strip().lower()
        return ex in self.allowed_exchanges

    def _bullish_trend_confirmed(self, st: pd.DataFrame, closed_idx: int) -> bool:
        """Require N consecutive closed bars with trend==1 (includes flip bar)."""
        n = self.confirm_bullish_bars
        if n <= 1:
            return int(st["trend"].iloc[closed_idx]) == 1
        start = closed_idx - n + 1
        if start < -len(st):
            return False
        for idx in range(start, closed_idx + 1):
            if int(st["trend"].iloc[idx]) != 1:
                return False
        return True

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
            current_regime = str(getattr(self.state, "market_regime", "unknown") or "unknown").lower()
            if not self._exchange_allowed():
                self.logger.info(
                    "[SuperTrend] %s HOLD: exchange=%s not in allowed_exchanges",
                    pair or self.state.pair,
                    self.exchange_name,
                )
                return "hold", 0.0, 0.0
            if not self._regime_allowed(current_regime):
                self.logger.info(
                    "[SuperTrend] %s HOLD: regime=%s blocked (allowed=%s blocked=%s)",
                    pair or self.state.pair,
                    current_regime,
                    sorted(self.allowed_regimes) if self.allowed_regimes else "any",
                    sorted(self.blocked_regimes),
                )
                return "hold", 0.0, 0.0

            exec_df = self._resolve_exec_df(market_data)
            exec_st = self._supertrend_at_closed(exec_df)
            if exec_st is None:
                return "hold", 0.0, 0.0
            trend_now, trend_prev, bullish_flip, st = exec_st
            closed_idx = -2

            trend_1h: Optional[int] = None
            trend_1h_prev: Optional[int] = None
            trend_1h_ok = True
            if self.require_1h_bullish_trend:
                trend_df = self._resolve_trend_df(market_data)
                trend_st = self._supertrend_at_closed(trend_df)
                if trend_st is None:
                    self.logger.info(
                        "[SuperTrend] %s HOLD: missing or insufficient %s OHLCV for trend filter",
                        pair or self.state.pair,
                        self.trend_timeframe,
                    )
                    return "hold", 0.0, 0.0
                trend_1h, trend_1h_prev, _, _ = trend_st
                trend_1h_ok = trend_1h == 1

            c_closed = float(exec_df["close"].iloc[closed_idx])
            up_band = float(st["up"].iloc[closed_idx])
            dn_band = float(st["dn"].iloc[closed_idx])
            atr_val = float(st["atr"].iloc[closed_idx]) if not pd.isna(st["atr"].iloc[closed_idx]) else None

            entry_reason_detail = (
                f"SuperTrend {self.execution_timeframe} flip (prev={trend_prev}, now={trend_now})"
                f" + {self.trend_timeframe} trend={trend_1h if trend_1h is not None else 'n/a'} "
                f"(require_bullish={self.require_1h_bullish_trend}); "
                f"close={c_closed:.8f}, up={up_band:.8f}, dn={dn_band:.8f}"
                + (f", atr={atr_val:.8f}." if atr_val is not None else ".")
            )

            adx_val = self._adx_at_index(exec_df, closed_idx)
            adx_ok = adx_val is not None and adx_val >= self.min_adx
            vol_ok = self._volume_ok_at_index(exec_df, closed_idx)

            self.state.indicators = {
                "bullish_flip": bullish_flip,
                "trend": trend_now,
                "trend_prev": trend_prev,
                "trend_1h": trend_1h,
                "trend_1h_prev": trend_1h_prev,
                "trend_1h_ok": trend_1h_ok,
                "up": up_band,
                "dn": dn_band,
                "atr": atr_val,
                "adx": adx_val,
                "adx_ok": adx_ok,
                "vol_ok": vol_ok,
                "close_closed": c_closed,
                "exec_timeframe": self.execution_timeframe,
                "trend_timeframe": self.trend_timeframe,
                "entry_reason_detail": entry_reason_detail,
            }

            if bullish_flip:
                if not trend_1h_ok:
                    self.logger.info(
                        "[SuperTrend] %s HOLD: 15m bullish_flip but %s trend=%s (need bullish)",
                        pair or self.state.pair,
                        self.trend_timeframe,
                        trend_1h,
                    )
                    return "hold", 0.0, 0.0
                if not self._bullish_trend_confirmed(st, closed_idx):
                    self.logger.info(
                        "[SuperTrend] %s HOLD: bullish_flip but trend not confirmed for %s bars",
                        pair or self.state.pair,
                        self.confirm_bullish_bars,
                    )
                    return "hold", 0.0, 0.0
                if self.require_close_above_dn and c_closed <= dn_band:
                    self.logger.info(
                        "[SuperTrend] %s HOLD: bullish_flip but close %.8f <= dn %.8f",
                        pair or self.state.pair,
                        c_closed,
                        dn_band,
                    )
                    return "hold", 0.0, 0.0
                if not adx_ok:
                    self.logger.info(
                        "[SuperTrend] %s HOLD: bullish_flip but ADX %.1f < min %.1f",
                        pair or self.state.pair,
                        adx_val if adx_val is not None else float("nan"),
                        self.min_adx,
                    )
                    return "hold", 0.0, 0.0
                if not vol_ok:
                    self.logger.info(
                        "[SuperTrend] %s HOLD: bullish_flip but volume below %.2fx avg",
                        pair or self.state.pair,
                        self.volume_multiplier,
                    )
                    return "hold", 0.0, 0.0
                self.state.last_signal = "buy"
                self.logger.info(
                    "[SuperTrend] %s BUY conf=%.2f str=%.2f 15m_flip %s_trend=%s adx=%.1f vol_ok=%s",
                    pair or self.state.pair,
                    self.min_confidence,
                    self.buy_strength,
                    self.trend_timeframe,
                    trend_1h,
                    adx_val if adx_val is not None else 0.0,
                    vol_ok,
                )
                return "buy", float(self.min_confidence), float(self.buy_strength)

            self.state.last_signal = "hold"
            self.logger.info(
                "[SuperTrend] %s HOLD: no bullish_flip (trend_prev=%s trend_now=%s)",
                pair or self.state.pair,
                trend_prev,
                trend_now,
            )
            return "hold", 0.0, 0.0
        except Exception as e:
            self.logger.error("[SuperTrend] generate_signal error: %s", e)
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
            self.logger.debug("[SuperTrend] position size 0: %s", e)
            return 0.0

    async def should_exit(self) -> bool:
        return False
