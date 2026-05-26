"""
MACD + 200 EMA + session VWAP perp scalper.

Spec (long):
- Execution chart: 15m (preferred; 5m if present in ``market_data`` and configured).
- Price structure: last fully closed candle entirely above 200 EMA (low above EMA200).
- VWAP: session VWAP (typical price HLC/3, cumulative per session day in ``session_tz``);
  price pulls toward VWAP then holds above (low near VWAP from above, close above VWAP).
- MACD (12, 26, 9): MACD line crosses above signal on the signal candle.

Emits long/short/hold; exits are orchestrator-managed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import pandas_ta as ta

from strategy.hyperliquid.base_perp_strategy import BasePerpStrategy
from strategy.vwap_utils import session_vwap_hlc3

logger = logging.getLogger(__name__)


class MacdEmaVwapScalperPerpStrategy(BasePerpStrategy):
    """Triple-confirmation perp scalper: trend (200 EMA), mean anchor (VWAP), momentum (MACD cross)."""

    STRATEGY_NAME = "MACD EMA VWAP Scalper"

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: Any,
        redis_client=None,
        exchange_name=None,
    ):
        super().__init__(config, exchange, database, redis_client)

        # Perp venue flags (from parameters)
        _params = config.get("parameters", {}) if isinstance(config, dict) else {}
        self.allow_long = bool(_params.get("allow_long", True))
        self.allow_short = bool(_params.get("allow_short", True))
        self.venue = "hyperliquid"
        self.exchange_name = exchange_name or "binance"
        self.logger = logging.getLogger(__name__)

        params = config.get("parameters", {})
        self.execution_timeframe = str(params.get("execution_timeframe", "15m")).lower()
        self.ema_trend_period = int(params.get("ema_trend_period", 200))
        self.macd_fast = int(params.get("macd_fast_period", 12))
        self.macd_slow = int(params.get("macd_slow_period", 26))
        self.macd_signal = int(params.get("macd_signal_period", 9))
        self.session_tz = str(params.get("session_tz", "UTC"))
        # Max (low - vwap) / vwap when low is still at or above VWAP — "pulled to" VWAP.
        self.vwap_pull_proximity_pct = float(params.get("vwap_pull_proximity_pct", 0.002))
        # Recent bars (including current) where a pullback-toward-VWAP may occur; MACD cross is on the latest bar only.
        self.vwap_pull_lookback_bars = max(1, int(params.get("vwap_pull_lookback_bars", 3)))
        self.min_confidence = float(params.get("min_confidence", 0.62))
        self.buy_strength = float(params.get("buy_strength", 0.72))
        self.skip_sideways_regime = bool(params.get("skip_sideways_regime", True))
        self.require_trend_above_ema200 = bool(params.get("require_trend_above_ema200", True))
        self.require_macd_line_positive = bool(params.get("require_macd_line_positive", True))
        self.min_trend_ema_distance_pct = float(
            params.get("min_trend_ema_distance_pct", 0.002)
        )
        self.require_bullish_entry_candle = bool(
            params.get("require_bullish_entry_candle", True)
        )
        self.min_atr_pct_of_price = float(params.get("min_atr_pct_of_price", 0.002))
        self.atr_period = max(2, int(params.get("atr_period", 14)))
        ae = params.get("allowed_exchanges")
        if ae is None:
            ae = ["binance"]
        self.allowed_exchanges = {str(x).strip().lower() for x in ae if str(x).strip()}
        self.trend_timeframe_key = str(params.get("trend_timeframe", "1h")).lower()
        # Long-side regime veto. Shorts use the mirrored trending-up veto below.
        br = params.get("blocked_regimes")
        if br is None:
            br = ["trending_down"]
        self.blocked_regimes = {str(x).strip().lower() for x in br if str(x).strip()}
        short_block = params.get("short_blocked_regimes")
        if short_block is None:
            short_block = ["trending_up"]
        self.short_blocked_regimes = {
            str(x).strip().lower() for x in short_block if str(x).strip()
        }

        self._current_ohlcv = None

    


    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}
        self.logger.info("Initialized MACD EMA VWAP Scalper for %s", pair)

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    @staticmethod
    def _macd_cols(macd_df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
        if macd_df is None or macd_df.empty:
            return None, None
        macd_col = next((c for c in macd_df.columns if str(c).startswith("MACD_")), None)
        signal_col = next((c for c in macd_df.columns if str(c).startswith("MACDs_")), None)
        return macd_col, signal_col

    @staticmethod
    def _df_or_none(obj: Any) -> Optional[pd.DataFrame]:
        """Avoid ``a or b`` with DataFrames — bool(df) raises in pandas."""
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
        if key == "5m":
            df = self._df_or_none(market_data.get("15m"))
            if df is not None:
                return df
        for k in ("15m", "1h"):
            df = self._df_or_none(market_data.get(k))
            if df is not None:
                return df
        return None

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
            if self.skip_sideways_regime and current_regime == "sideways":
                self.logger.info(
                    "[MACDEMAVWAPScalper] %s HOLD: regime=sideways skip",
                    pair or self.state.pair,
                )
                return "hold", 0.0, 0.0
            if self.allowed_exchanges:
                ex = str(self.exchange_name or "").strip().lower()
                if ex not in self.allowed_exchanges:
                    self.logger.info(
                        "[MACDEMAVWAPScalper] %s HOLD: exchange=%s not in allowed_exchanges",
                        pair or self.state.pair,
                        self.exchange_name,
                    )
                    return "hold", 0.0, 0.0

            exec_df = self._resolve_exec_df(market_data)
            trend_df = None
            if isinstance(market_data, dict):
                trend_df = self._df_or_none(market_data.get(self.trend_timeframe_key))
                if trend_df is None:
                    trend_df = self._df_or_none(market_data.get("1h"))

            min_len = max(
                self.ema_trend_period + 5,
                self.macd_slow + self.macd_signal + 10,
                50,
            )
            if exec_df is None or len(exec_df) < min_len:
                return "hold", 0.0, 0.0

            required = {"open", "high", "low", "close", "volume"}
            if not required.issubset(exec_df.columns):
                return "hold", 0.0, 0.0

            if self.require_trend_above_ema200:
                if trend_df is None or len(trend_df) < self.ema_trend_period + 5:
                    return "hold", 0.0, 0.0
                if not required.issubset(trend_df.columns):
                    return "hold", 0.0, 0.0

            close = exec_df["close"].astype(float)
            low = exec_df["low"].astype(float)
            high = exec_df["high"].astype(float)
            open_ = exec_df["open"].astype(float)

            ema200 = ta.ema(close, length=self.ema_trend_period)
            if ema200 is None or len(ema200) < 2 or pd.isna(ema200.iloc[-1]):
                return "hold", 0.0, 0.0

            vwap = session_vwap_hlc3(exec_df, session_tz=self.session_tz)
            if vwap is None or len(vwap) < 2 or pd.isna(vwap.iloc[-1]):
                return "hold", 0.0, 0.0

            macd_df = ta.macd(close, fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal)
            macd_col, sig_col = self._macd_cols(macd_df)
            if not macd_col or not sig_col:
                return "hold", 0.0, 0.0
            mline = macd_df[macd_col].astype(float)
            sline = macd_df[sig_col].astype(float)
            if len(mline) < 3 or pd.isna(mline.iloc[-1]) or pd.isna(sline.iloc[-1]):
                return "hold", 0.0, 0.0

            ema_now = float(ema200.iloc[-1])
            vwap_now = float(vwap.iloc[-1])
            o_now = float(open_.iloc[-1])
            h_now = float(high.iloc[-1])
            l_now = float(low.iloc[-1])
            c_now = float(close.iloc[-1])

            m_now = float(mline.iloc[-1])
            m_prev = float(mline.iloc[-2])
            s_now = float(sline.iloc[-1])
            s_prev = float(sline.iloc[-2])

            bullish_cross = m_prev <= s_prev and m_now > s_now
            bearish_cross = m_prev >= s_prev and m_now < s_now

            # Entire candle above 200 EMA (no intrabar print through the average).
            fully_above_ema200 = bool(
                min(o_now, h_now, l_now, c_now) > ema_now
            )
            fully_below_ema200 = bool(
                max(o_now, h_now, l_now, c_now) < ema_now
            )

            eps = max(self.vwap_pull_proximity_pct, 1e-8)
            hold_above_vwap = c_now > vwap_now and l_now >= vwap_now
            hold_below_vwap = c_now < vwap_now and h_now <= vwap_now
            lb = min(self.vwap_pull_lookback_bars, len(exec_df))
            recent_pull = False
            recent_pull_short = False
            for i in range(-lb, 0):
                try:
                    li = float(low.iloc[i])
                    hi = float(high.iloc[i])
                    ci = float(close.iloc[i])
                    vi = float(vwap.iloc[i])
                except (IndexError, TypeError, ValueError):
                    continue
                if ci > vi and li >= vi and (li - vi) / max(vi, 1e-12) <= eps:
                    recent_pull = True
                if ci < vi and hi <= vi and (vi - hi) / max(vi, 1e-12) <= eps:
                    recent_pull_short = True
            pulled_to_vwap = hold_above_vwap and recent_pull
            pulled_to_vwap_short = hold_below_vwap and recent_pull_short

            trend_ok = True
            trend_ok_short = True
            trend_ema_val = None
            trend_close_val = None
            if self.require_trend_above_ema200 and trend_df is not None:
                t_close = trend_df["close"].astype(float)
                t_ema = ta.ema(t_close, length=self.ema_trend_period)
                if t_ema is not None and not pd.isna(t_ema.iloc[-1]):
                    trend_ema_val = float(t_ema.iloc[-1])
                    trend_close_val = float(t_close.iloc[-1])
                    trend_ok = trend_close_val > trend_ema_val
                    trend_ok_short = trend_close_val < trend_ema_val
                    if trend_ok and self.min_trend_ema_distance_pct > 0 and trend_ema_val > 0:
                        dist_pct = (trend_close_val - trend_ema_val) / trend_ema_val
                        trend_ok = dist_pct >= self.min_trend_ema_distance_pct
                    if trend_ok_short and self.min_trend_ema_distance_pct > 0 and trend_ema_val > 0:
                        dist_pct_short = (trend_ema_val - trend_close_val) / trend_ema_val
                        trend_ok_short = dist_pct_short >= self.min_trend_ema_distance_pct

            macd_positive = (not self.require_macd_line_positive) or (m_now > 0)
            macd_negative = (not self.require_macd_line_positive) or (m_now < 0)
            bullish_candle = (not self.require_bullish_entry_candle) or (c_now > o_now)
            bearish_candle = (not self.require_bullish_entry_candle) or (c_now < o_now)

            atr_pct = None
            atr_ok = True
            if self.min_atr_pct_of_price > 0:
                atr_series = ta.atr(high, low, close, length=self.atr_period)
                if (
                    atr_series is not None
                    and len(atr_series) >= 1
                    and not pd.isna(atr_series.iloc[-1])
                    and c_now > 0
                ):
                    atr_pct = float(atr_series.iloc[-1]) / c_now
                    atr_ok = atr_pct >= self.min_atr_pct_of_price

            buy_ok = bool(
                self.allow_long
                and current_regime not in self.blocked_regimes
                and
                fully_above_ema200
                and pulled_to_vwap
                and bullish_cross
                and trend_ok
                and macd_positive
                and bullish_candle
                and atr_ok
            )
            sell_ok = bool(
                self.allow_short
                and current_regime not in self.short_blocked_regimes
                and fully_below_ema200
                and pulled_to_vwap_short
                and bearish_cross
                and trend_ok_short
                and macd_negative
                and bearish_candle
                and atr_ok
            )

            detail_parts = [
                f"Closed candle fully above {self.ema_trend_period} EMA "
                f"(O={o_now:.8f} H={h_now:.8f} L={l_now:.8f} C={c_now:.8f} vs EMA200={ema_now:.8f}).",
                f"Session VWAP ({self.session_tz}) now {vwap_now:.8f}: within the last {lb} closed bars, "
                f"price dipped toward VWAP while staying at or above it "
                f"(max relative distance low−VWAP ≤ {eps:.5f}), and the latest close remains above VWAP.",
                f"MACD ({self.macd_fast},{self.macd_slow},{self.macd_signal}) bullish cross on this bar "
                f"(MACD prev={m_prev:.8f} ≤ signal prev={s_prev:.8f}; MACD now={m_now:.8f} > signal now={s_now:.8f}).",
            ]
            if self.require_trend_above_ema200:
                detail_parts.append(
                    f"1h trend filter: close {trend_close_val} vs 1h EMA200 {trend_ema_val} "
                    f"(require price above 200 EMA on {self.trend_timeframe_key})."
                    if trend_close_val is not None and trend_ema_val is not None
                    else "1h trend filter: insufficient higher-TF data."
                )

            entry_reason_detail = " ".join(detail_parts)

            self.state.indicators = {
                "scalper_buy_ok": buy_ok,
                "scalper_sell_ok": sell_ok,
                "fully_above_ema200": fully_above_ema200,
                "fully_below_ema200": fully_below_ema200,
                "pulled_to_vwap": pulled_to_vwap,
                "pulled_to_vwap_short": pulled_to_vwap_short,
                "vwap_pull_recent": recent_pull,
                "vwap_pull_recent_short": recent_pull_short,
                "hold_above_vwap": hold_above_vwap,
                "hold_below_vwap": hold_below_vwap,
                "macd_bullish_cross": bullish_cross,
                "macd_bearish_cross": bearish_cross,
                "macd_line_positive": macd_positive,
                "macd_line_negative": macd_negative,
                "bullish_entry_candle": bullish_candle,
                "bearish_entry_candle": bearish_candle,
                "atr_pct_of_price": atr_pct,
                "atr_ok": atr_ok,
                "trend_above_ema200_ok": trend_ok,
                "trend_below_ema200_ok": trend_ok_short,
                "ema200": ema_now,
                "vwap": vwap_now,
                "macd_line": m_now,
                "macd_signal_line": s_now,
                "exec_timeframe": self.execution_timeframe,
                "entry_reason_detail": entry_reason_detail,
            }

            if buy_ok:
                self.state.last_signal = "long"
                reasons = "long_scalp_macd_cross+body_above_ema200+vwap_pull_hold"
                self.logger.info(
                    "[MACDEMAVWAPScalper] %s LONG conf=%.2f str=%.2f %s",
                    pair or self.state.pair,
                    self.min_confidence,
                    self.buy_strength,
                    reasons,
                )
                return self._perp_gate_signal("long", float(self.min_confidence), float(self.buy_strength))
            if sell_ok:
                self.state.last_signal = "short"
                reasons = "short_scalp_macd_cross+body_below_ema200+vwap_pull_hold"
                self.logger.info(
                    "[MACDEMAVWAPScalper] %s SHORT conf=%.2f str=%.2f %s",
                    pair or self.state.pair,
                    self.min_confidence,
                    self.buy_strength,
                    reasons,
                )
                return self._perp_gate_signal("short", float(self.min_confidence), float(self.buy_strength))
            self.state.last_signal = "hold"
            failed = []
            if not fully_above_ema200:
                failed.append("not_fully_above_ema200")
            if not pulled_to_vwap:
                failed.append("no_vwap_pull_or_hold")
            if not bullish_cross:
                failed.append("no_macd_bull_cross")
            if not trend_ok:
                failed.append("trend_not_above_ema200")
            if not macd_positive:
                failed.append("macd_line_not_positive")
            if not bullish_candle:
                failed.append("entry_candle_not_bullish")
            if not atr_ok:
                failed.append("atr_below_min_pct")
            if self.allow_short and current_regime in self.short_blocked_regimes:
                failed.append("short_regime_blocked")
            self.logger.info(
                "[MACDEMAVWAPScalper] %s HOLD failed=%s",
                pair or self.state.pair,
                ",".join(failed) if failed else "n/a",
            )
            return "hold", 0.0, 0.0
        except Exception as e:
            self.logger.error("[MACDEMAVWAPScalper] generate_signal error: %s", e)
            return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        if signal_type not in {"long", "short", "buy", "sell"}:
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
            self.logger.debug("[MACDEMAVWAPScalper] position size 0: %s", e)
            return 0.0

    async def _should_exit_legacy(self) -> bool:
        return False
