"""
Strategy Service for the Multi-Exchange Trading Bot
Strategy analysis and signal generation
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import importlib
import sys
import os
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import json
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry

# Import market regime detector
sys.path.append('/app')
from strategy.market_regime_detector import MarketRegimeDetector, MarketRegime

# Custom JSON encoder for numpy types
class NumpyJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            # CRITICAL: Check for NaN and infinity in numpy floats
            float_val = float(obj)
            if np.isnan(float_val) or np.isinf(float_val):
                return 0.0  # Replace NaN/inf with 0
            return float_val
        elif isinstance(obj, np.ndarray):
            # CRITICAL: Sanitize numpy arrays
            cleaned_list = []
            for val in obj.tolist():
                if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                    cleaned_list.append(0.0)
                else:
                    cleaned_list.append(val)
            return cleaned_list
        elif isinstance(obj, np.bool_):
            return bool(obj)
        elif isinstance(obj, pd.Series):
            # CRITICAL: Sanitize pandas series
            cleaned_list = []
            for val in obj.tolist():
                if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                    cleaned_list.append(0.0)
                else:
                    cleaned_list.append(val)
            return cleaned_list
        elif isinstance(obj, pd.DataFrame):
            # CRITICAL: Sanitize pandas dataframes
            df_dict = obj.to_dict('records')
            cleaned_records = []
            for record in df_dict:
                cleaned_record = {}
                for key, value in record.items():
                    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
                        cleaned_record[key] = 0.0
                    else:
                        cleaned_record[key] = value
                cleaned_records.append(cleaned_record)
            return cleaned_records
        elif isinstance(obj, datetime):
            return obj.isoformat()
        # CRITICAL: Handle regular Python floats that might be NaN/inf
        elif isinstance(obj, float):
            if np.isnan(obj) or np.isinf(obj):
                return 0.0
            return obj
        return super().default(obj)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create custom registry to avoid conflicts
strategy_registry = CollectorRegistry()

# Prometheus Metrics
strategy_analyses = Counter('strategy_analyses_total', 'Total strategy analyses performed', ['strategy', 'exchange', 'pair', 'result'], registry=strategy_registry)
analysis_duration = Histogram('strategy_analysis_duration_seconds', 'Strategy analysis duration', ['strategy'], registry=strategy_registry)
signals_generated = Counter('strategy_signals_total', 'Total signals generated', ['strategy', 'signal_type'], registry=strategy_registry)
active_strategies = Gauge('strategy_active_strategies', 'Number of active strategies', registry=strategy_registry)

# Initialize FastAPI app
app = FastAPI(
    title="Strategy Service",
    description="Strategy analysis and signal generation",
    version="1.0.0"
)

# Configure custom JSON encoder
app.json_encoder = NumpyJSONEncoder

# Override FastAPI's jsonable_encoder to handle numpy types
from fastapi.encoders import jsonable_encoder as original_jsonable_encoder

def custom_jsonable_encoder(obj, **kwargs):
    """Custom JSON encoder that handles numpy types and sanitizes NaN/infinity values"""
    if isinstance(obj, dict):
        return {k: custom_jsonable_encoder(v, **kwargs) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [custom_jsonable_encoder(item, **kwargs) for item in obj]
    elif isinstance(obj, (np.integer, np.floating, np.bool_)):
        if isinstance(obj, np.bool_):
            return bool(obj)
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            # CRITICAL: Sanitize numpy floating values
            float_val = float(obj)
            if np.isnan(float_val) or np.isinf(float_val):
                return 0.0
            return float_val
    elif isinstance(obj, np.ndarray):
        # CRITICAL: Sanitize numpy arrays
        cleaned_list = []
        for val in obj.tolist():
            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                cleaned_list.append(0.0)
            else:
                cleaned_list.append(custom_jsonable_encoder(val, **kwargs))
        return cleaned_list
    elif isinstance(obj, pd.Series):
        # CRITICAL: Sanitize pandas series
        cleaned_list = []
        for val in obj.tolist():
            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                cleaned_list.append(0.0)
            else:
                cleaned_list.append(custom_jsonable_encoder(val, **kwargs))
        return cleaned_list
    elif isinstance(obj, pd.DataFrame):
        # CRITICAL: Sanitize pandas dataframes
        df_dict = obj.to_dict('records')
        cleaned_records = []
        for record in df_dict:
            cleaned_record = {}
            for key, value in record.items():
                if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
                    cleaned_record[key] = 0.0
                else:
                    cleaned_record[key] = custom_jsonable_encoder(value, **kwargs)
            cleaned_records.append(cleaned_record)
        return cleaned_records
    elif isinstance(obj, float):
        # CRITICAL: Handle regular Python floats
        if np.isnan(obj) or np.isinf(obj):
            return 0.0
        return obj
    elif hasattr(obj, '__dict__'):
        return custom_jsonable_encoder(obj.__dict__, **kwargs)
    else:
        return original_jsonable_encoder(obj, **kwargs)

# Monkey patch FastAPI's jsonable_encoder
import fastapi.encoders
fastapi.encoders.jsonable_encoder = custom_jsonable_encoder

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data Models
class StrategySignal(BaseModel):
    strategy_name: str
    pair: str
    exchange: str
    signal: str  # 'buy', 'sell', 'hold'
    confidence: float
    strength: float
    timestamp: datetime
    market_regime: str = "unknown"

class StrategyConsensus(BaseModel):
    pair: str
    exchange: str
    consensus_signal: str
    agreement_percentage: float
    participating_strategies: int
    signals: List[StrategySignal]
    timestamp: datetime

class StrategyPerformance(BaseModel):
    strategy_name: str
    total_trades: int
    winning_trades: int
    win_rate: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    period: str

class AnalysisRequest(BaseModel):
    # PnL-FIX v4: Default timeframes updated to the union of all strategies'
    # declared target_timeframes in config.yaml:
    #   - heikin_ashi:          [4h, 1h, 15m]
    #   - engulfing_multi_tf:   [4h, 1h, 15m]
    #   - vwma_hull:            [4h, 1h, 15m]
    #   - multi_timeframe_confluence: [1h, 15m]
    # The previous default ['1h', '15m', '5m'] never fetched 4h, which meant
    # vwma_hull silently skipped its 4h macro-bullish HARD VETO (PnL-FIX v3)
    # and engulfing_multi_tf fell back to using 1h as a pseudo-macro. ``5m``
    # wasn't declared by any strategy — dropping it removes one wasted call.
    # Order matters: ``timeframes[0]`` is the "primary" used for market-regime
    # detection, so keep 1h first (4h regimes would react too slowly).
    timeframes: List[str] = ['1h', '4h', '15m']
    strategies: Optional[List[str]] = None
    include_indicators: bool = True

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    strategies_loaded: int
    total_strategies: int

# Global variables
strategies = {}
strategy_instances = {}
signal_cache = {}
# Last strategy evaluation per exchange → pair (for dashboard Exchange Status)
pair_last_snapshots: Dict[str, Dict[str, Any]] = {}
# Successful generate_signal() calls per strategy since process start (for dashboard)
strategy_cumulative_runs: Dict[str, int] = {}
# Align with orchestrator default entry bar (see trading.min_confidence_threshold in config)
SNAPSHOT_ENTRY_CONFIDENCE = 0.3
strategy_manager = None
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database_service:8002")

class ExchangeAdapter:
    """Adapter to provide exchange functionality to strategies via HTTP calls"""
    
    def __init__(self, exchange_service_url: str, exchange_name: str = None):
        self.exchange_service_url = exchange_service_url
        self.exchange_name = exchange_name
    
    async def get_ohlcv(self, exchange_name: str, symbol: str, timeframe: str, limit: int = 100):
        """Get OHLCV data from exchange service"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Convert symbol format if needed (remove slash for URL)
                url_symbol = symbol.replace('/', '')
                response = await client.get(
                    f"{self.exchange_service_url}/api/v1/market/ohlcv/{exchange_name}/{url_symbol}",
                    params={'timeframe': timeframe, 'limit': limit}
                )
                response.raise_for_status()
                
                # Convert response to DataFrame
                response_data = response.json()
                if response_data and 'data' in response_data:
                    # Extract the nested data structure
                    data = response_data['data']
                    
                    # Convert to DataFrame format expected by strategies
                    df = pd.DataFrame({
                        'timestamp': data['timestamp'],
                        'open': data['open'],
                        'high': data['high'],
                        'low': data['low'],
                        'close': data['close'],
                        'volume': data['volume']
                    })
                    
                    # Convert timestamp and set as index
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('timestamp', inplace=True)
                    
                    # Ensure numeric columns are properly typed
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    logger.info(f"✅ [DATA FETCH] Successfully converted {len(df)} {timeframe} candles for {symbol} on {exchange_name}")
                    return df
                    
                return None
                
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} on {exchange_name}: {e}")
            return None
    
    async def get_ticker(self, symbol: str, exchange_name: str = None):
        """Get ticker data from exchange service - compatible with base strategy interface"""
        try:
            # Use provided exchange_name or fall back to adapter's exchange_name
            if exchange_name is None:
                exchange_name = self.exchange_name or 'binance'  # Fallback
            
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Convert symbol format if needed (remove slash for URL)
                url_symbol = symbol.replace('/', '')
                response = await client.get(
                    f"{self.exchange_service_url}/api/v1/market/ticker/{exchange_name}/{url_symbol}"
                )
                response.raise_for_status()
                return response.json()
                
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol} on {exchange_name}: {e}")
            return None

# Global exchange adapter
exchange_adapter = ExchangeAdapter(exchange_service_url)

# Global market regime detector
market_regime_detector = MarketRegimeDetector()


def _bump_strategy_cumulative_run(strategy_name: str) -> None:
    global strategy_cumulative_runs
    strategy_cumulative_runs[strategy_name] = strategy_cumulative_runs.get(strategy_name, 0) + 1


def _pair_snapshot_aliases(pair: str) -> List[str]:
    """Dashboard / DB may use BASE/QUOTE or BASEQUOTE; store snapshots under all sensible keys."""
    if not pair:
        return []
    p = str(pair).strip()
    seen = set()
    out: List[str] = []
    for cand in (p, p.replace("/", "")):
        if cand and cand not in seen:
            seen.add(cand)
            out.append(cand)
    if "/" not in p:
        if len(p) >= 7 and p.endswith("USDC"):
            slash = f"{p[:-4]}/USDC"
            if slash not in seen:
                seen.add(slash)
                out.append(slash)
        elif len(p) >= 6 and p.endswith("USD") and not p.endswith("USDC"):
            slash = f"{p[:-3]}/USD"
            if slash not in seen:
                seen.add(slash)
                out.append(slash)
    return out


def _record_pair_strategy_snapshot(
    exchange_name: str,
    pair: str,
    results: Optional[Dict[str, Any]] = None,
    *,
    analysis_status: str = "success",
    detail: Optional[str] = None,
) -> None:
    """Persist last strategy run summary for dashboard (per exchange / pair)."""
    global pair_last_snapshots
    ex = (exchange_name or "").lower()
    if ex not in pair_last_snapshots:
        pair_last_snapshots[ex] = {}
    now = datetime.utcnow().isoformat() + "Z"
    if analysis_status != "success" or not results:
        snap = {
            "timestamp": now,
            "analysis_status": analysis_status,
            "summary": detail or "No analysis data",
            "market_regime": None,
            "consensus": {},
            "strategies": [],
        }
        for pk in _pair_snapshot_aliases(pair):
            pair_last_snapshots[ex][pk] = snap
        return

    strategies_out: List[Dict[str, Any]] = []
    for name, sr in results.get("strategies", {}).items():
        if "error" in sr:
            strategies_out.append(
                {
                    "strategy": name,
                    "signal": None,
                    "confidence": 0.0,
                    "strength": 0.0,
                    "outcome": "failed",
                    "reason": f"Strategy error: {sr['error']}",
                }
            )
            continue
        sig = (sr.get("signal") or "hold").lower()
        try:
            conf = float(sr.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        try:
            strength = float(sr.get("strength", 0) or 0)
        except (TypeError, ValueError):
            strength = 0.0
        if sig == "buy" and conf >= SNAPSHOT_ENTRY_CONFIDENCE:
            outcome = "buy_signal"
            reason = (
                f"BUY at or above entry-confidence bar ({conf:.2f} ≥ {SNAPSHOT_ENTRY_CONFIDENCE}); "
                "counts toward long bias."
            )
        elif sig == "sell" and conf >= SNAPSHOT_ENTRY_CONFIDENCE:
            outcome = "sell_signal"
            reason = (
                f"SELL at or above confidence bar ({conf:.2f} ≥ {SNAPSHOT_ENTRY_CONFIDENCE})."
            )
        elif sig == "hold":
            outcome = "hold"
            reason = "HOLD — no directional entry from this strategy."
            if name == "macd_momentum":
                indicators = ((sr.get("state") or {}).get("indicators") or {})
                if isinstance(indicators, dict):
                    hist_now = indicators.get("exec_macd_hist")
                    hist_prev = indicators.get("macd_hist_prev")
                    exec_macd = indicators.get("exec_macd")
                    exec_macd_signal = indicators.get("exec_macd_signal")
                    exec_rsi = indicators.get("exec_rsi")
                    trend_rsi = indicators.get("trend_rsi")
                    lookback = indicators.get("macd_green_rsi_lookback")
                    threshold = indicators.get("macd_green_rsi_threshold")
                    green2 = indicators.get("macd_green_consecutive_increasing")
                    rsi_ok = indicators.get("macd_green_rsi_buy_ok")
                    hist_ts = indicators.get("macd_hist_ts")
                    reason = (
                        "HOLD — MACD rule not satisfied"
                        + (
                            f": hist={float(hist_now):.4f}, prev={float(hist_prev):.4f}, "
                            f"macd={float(exec_macd):.4f}, signal={float(exec_macd_signal):.4f}, "
                            f"green_2bar_up={bool(green2)}, rsi_window_ok={bool(rsi_ok)}, "
                            f"rsi15={float(exec_rsi):.2f}, rsi1h={float(trend_rsi):.2f}, "
                            f"lookback={int(lookback) if lookback is not None else '—'}, "
                            f"threshold={float(threshold):.2f}, ts={hist_ts or '—'}"
                            if all(v is not None for v in (hist_now, hist_prev, exec_macd, exec_macd_signal, exec_rsi, trend_rsi, threshold))
                            else ""
                        )
                    )
            if name == "rsi_oversold_checklist":
                indicators = ((sr.get("state") or {}).get("indicators") or {})
                checklist_buy = bool(indicators.get("checklist_buy", False))
                if not checklist_buy and isinstance(indicators, dict):
                    failed_checks = []
                    if indicators.get("rsi_reclaimed_30") is False:
                        failed_checks.append("rsi_reclaim_30")
                    if indicators.get("price_above_ema20") is False:
                        failed_checks.append("price_above_ema20")
                    if indicators.get("macro_trend_ok") is False:
                        failed_checks.append("ema50_above_ema200")
                    if indicators.get("volume_ok") is False:
                        failed_checks.append("volume_spike")
                    if indicators.get("support_bounce") is False:
                        failed_checks.append("support_bounce")
                    if indicators.get("bullish_divergence") is False:
                        failed_checks.append("bullish_divergence")
                    if failed_checks:
                        exec_rsi = indicators.get("exec_rsi_15m")
                        trend_rsi = indicators.get("trend_rsi_1h")
                        reason = (
                            "HOLD — checklist not satisfied: "
                            + ", ".join(failed_checks)
                            + (
                                f" (rsi15={float(exec_rsi):.2f}, rsi1h={float(trend_rsi):.2f})"
                                if exec_rsi is not None and trend_rsi is not None
                                else ""
                            )
                        )
        elif sig == "buy":
            outcome = "weak_buy"
            reason = (
                f"BUY below entry-confidence bar ({conf:.2f} < {SNAPSHOT_ENTRY_CONFIDENCE}); "
                "treated as weak / no standalone entry."
            )
        elif sig == "sell":
            outcome = "weak_sell"
            reason = (
                f"SELL below confidence bar ({conf:.2f} < {SNAPSHOT_ENTRY_CONFIDENCE})."
            )
        else:
            outcome = "neutral"
            reason = f"{sig.upper()} confidence {conf:.2f}."
        strategies_out.append(
            {
                "strategy": name,
                "signal": sig,
                "confidence": round(conf, 4),
                "strength": round(strength, 4),
                "outcome": outcome,
                "reason": reason,
                "validation": (
                    {
                        "hist_now": ((sr.get("state") or {}).get("indicators") or {}).get("exec_macd_hist"),
                        "hist_prev": ((sr.get("state") or {}).get("indicators") or {}).get("macd_hist_prev"),
                        "exec_macd": ((sr.get("state") or {}).get("indicators") or {}).get("exec_macd"),
                        "exec_macd_signal": ((sr.get("state") or {}).get("indicators") or {}).get("exec_macd_signal"),
                        "trend_macd": ((sr.get("state") or {}).get("indicators") or {}).get("trend_macd"),
                        "trend_macd_signal": ((sr.get("state") or {}).get("indicators") or {}).get("trend_macd_signal"),
                        "exec_rsi": ((sr.get("state") or {}).get("indicators") or {}).get("exec_rsi"),
                        "trend_rsi": ((sr.get("state") or {}).get("indicators") or {}).get("trend_rsi"),
                        "lookback": ((sr.get("state") or {}).get("indicators") or {}).get("macd_green_rsi_lookback"),
                        "threshold": ((sr.get("state") or {}).get("indicators") or {}).get("macd_green_rsi_threshold"),
                        "green_consecutive_increasing": ((sr.get("state") or {}).get("indicators") or {}).get("macd_green_consecutive_increasing"),
                        "rsi_window_ok": ((sr.get("state") or {}).get("indicators") or {}).get("macd_green_rsi_buy_ok"),
                        "hist_ts": ((sr.get("state") or {}).get("indicators") or {}).get("macd_hist_ts"),
                        "rsi_window": ((sr.get("state") or {}).get("indicators") or {}).get("macd_rsi_window_values"),
                        "rsi_window_ts": ((sr.get("state") or {}).get("indicators") or {}).get("macd_rsi_window_timestamps"),
                    }
                    if name == "macd_momentum"
                    else None
                ),
            }
        )

    cons = results.get("consensus") or {}
    cs = (cons.get("signal") or "hold").lower()
    try:
        cc = float(cons.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        cc = 0.0
    try:
        agr = float(cons.get("agreement", 0) or 0)
    except (TypeError, ValueError):
        agr = 0.0
    parts = [
        f"Market regime: {results.get('market_regime', 'unknown')}.",
        (
            f"Consensus {cs.upper()} @ {cc:.2f}, agreement {agr:.1f}%, "
            f"participating: {cons.get('participating_strategies', 0)}."
        ),
    ]
    for s in strategies_out:
        parts.append(
            f"{s['strategy']}: {s['signal']} ({s['confidence']:.2f}) — {s['reason']}"
        )

    snap = {
        "timestamp": now,
        "analysis_status": "success",
        "summary": " ".join(parts),
        "market_regime": results.get("market_regime"),
        "applicable_strategies": list(results.get("applicable_strategies") or []),
        "consensus": {
            "signal": cs,
            "confidence": round(cc, 4),
            "agreement": round(agr, 2),
            "participating_strategies": cons.get("participating_strategies", 0),
        },
        "strategies": strategies_out,
    }
    for pk in _pair_snapshot_aliases(pair):
        pair_last_snapshots[ex][pk] = snap


class StrategyManager:
    """Manages all trading strategies and coordinates analysis"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._regime_state: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._regime_stability_cfg: Dict[str, Any] = {
            "enabled": True,
            "min_dwell_seconds": 180,
            "hysteresis_delta": 0.08,
            "emergency_volatility_score": 0.92,
        }
        self._initialize_strategies()
        self._load_regime_stability_config()

    async def _persist_macd_analysis_log(
        self,
        exchange_name: str,
        pair: str,
        strategy_result: Dict[str, Any],
    ) -> None:
        """Persist MACD validation values for each fresh macd_momentum analysis."""
        try:
            indicators = ((strategy_result or {}).get("state") or {}).get("indicators") or {}
            # Skip empty rows (e.g., early-regime short-circuit) to avoid misleading null-only records.
            if indicators.get("exec_macd_hist") is None or indicators.get("macd_hist_prev") is None:
                return
            payload = {
                "log_ts": (strategy_result or {}).get("timestamp") or datetime.utcnow().isoformat(),
                "exchange": exchange_name,
                "pair": pair,
                "hist_now": indicators.get("exec_macd_hist"),
                "hist_prev": indicators.get("macd_hist_prev"),
                "green_consecutive_increasing": indicators.get("macd_green_consecutive_increasing"),
                "hist_candle_ts": indicators.get("macd_hist_ts"),
                "lookback": indicators.get("macd_green_rsi_lookback"),
                "threshold": indicators.get("macd_green_rsi_threshold"),
                "rsi_window_ts": indicators.get("macd_rsi_window_timestamps") or [],
                "rsi_window": indicators.get("macd_rsi_window_values") or [],
                "trigger": indicators.get("macd_green_rsi_buy_ok"),
                "source": "strategy-service-live",
                "raw_line": (
                    f"[MACDMomentumStrategy] {pair} macd_rsi_rule "
                    f"hist_now={indicators.get('exec_macd_hist')} "
                    f"hist_prev={indicators.get('macd_hist_prev')} "
                    f"green_consecutive_increasing={indicators.get('macd_green_consecutive_increasing')} "
                    f"hist_ts={indicators.get('macd_hist_ts')} "
                    f"lookback={indicators.get('macd_green_rsi_lookback')} "
                    f"threshold={indicators.get('macd_green_rsi_threshold')} "
                    f"rsi_window_ts={indicators.get('macd_rsi_window_timestamps') or []} "
                    f"rsi_window={indicators.get('macd_rsi_window_values') or []} "
                    f"trigger={indicators.get('macd_green_rsi_buy_ok')}"
                ),
            }
            async with httpx.AsyncClient(timeout=6.0) as client:
                await client.post(
                    f"{database_service_url}/api/v1/analytics/macd-analysis-logs",
                    json=payload,
                )
        except Exception as e:
            logger.warning(
                "Failed to persist macd analysis log for %s on %s: %s",
                pair,
                exchange_name,
                e,
            )

    def _load_regime_stability_config(self) -> None:
        """Load optional regime stability tuning from strategy config."""
        try:
            cfg = self.config.get("regime_stability")
            if isinstance(cfg, dict):
                self._regime_stability_cfg.update(cfg)
                market_regime_detector.configure(cfg)
                logger.info(
                    "Regime detector trend persistence configured: lookback=%s aligned=%s latest=%s consecutive=%s",
                    market_regime_detector.trend_lookback_bars,
                    market_regime_detector.trend_min_aligned_bars,
                    market_regime_detector.require_latest_trend_alignment,
                    market_regime_detector.require_consecutive_trend_bars,
                )
        except Exception as e:
            logger.warning(f"Failed to load regime stability config; using defaults: {e}")

    def _resolve_stable_regime(
        self,
        exchange_name: str,
        pair: str,
        detected_regime: str,
        regime_score: float,
        runner_up_score: float,
    ) -> Dict[str, Any]:
        """Apply hysteresis+dwell to avoid noisy regime flip-flops."""
        key = (exchange_name, pair)
        now = datetime.utcnow()
        prev_state = self._regime_state.get(key, {})
        previous_regime = str(prev_state.get("stable_regime") or detected_regime)
        changed_at = prev_state.get("changed_at", now)
        if not isinstance(changed_at, datetime):
            changed_at = now
        regime_age_seconds = max(0.0, (now - changed_at).total_seconds())

        if not bool(self._regime_stability_cfg.get("enabled", True)):
            next_regime = detected_regime
        else:
            min_dwell_seconds = float(self._regime_stability_cfg.get("min_dwell_seconds", 180) or 180)
            hysteresis_delta = float(self._regime_stability_cfg.get("hysteresis_delta", 0.08) or 0.08)
            emergency_score = float(self._regime_stability_cfg.get("emergency_volatility_score", 0.92) or 0.92)
            emergency_override = regime_score >= emergency_score
            score_delta = regime_score - float(runner_up_score or 0.0)
            if detected_regime == previous_regime:
                next_regime = previous_regime
            elif emergency_override or (regime_age_seconds >= min_dwell_seconds and score_delta >= hysteresis_delta):
                next_regime = detected_regime
            else:
                next_regime = previous_regime

        if next_regime != previous_regime:
            changed_at = now
            regime_age_seconds = 0.0

        self._regime_state[key] = {
            "stable_regime": next_regime,
            "changed_at": changed_at,
            "latest_regime_score": regime_score,
            "latest_detected_regime": detected_regime,
        }
        return {
            "stable_regime": next_regime,
            "previous_regime": previous_regime,
            "regime_age_seconds": float(regime_age_seconds),
        }
        
    def _initialize_strategies(self) -> None:
        """Initialize all enabled strategies"""
        self.strategies = {}
        # Config service returns strategies directly, not wrapped in 'strategies' key
        strategies_config = self.config
        
        # Strategy class mapping with actual module names
        strategy_mapping = {
            'vwma_hull': {
                'module': 'vwma_hull_strategy',
                'class': 'VWMAHullStrategy'
            },
            'rsi_oversold_checklist': {
                'module': 'rsi_oversold_checklist_strategy',
                'class': 'RSIOversoldChecklistStrategy'
            },
            'rsi_oversold_override': {
                'module': 'rsi_oversold_override_strategy',
                'class': 'RSIOversoldOverrideStrategy'
            },
            'macd_momentum': {
                'module': 'macd_momentum_strategy',
                'class': 'MACDMomentumStrategy'
            },
            'heikin_ashi': {
                'module': 'heikin_ashi_strategy', 
                'class': 'HeikinAshiStrategy'
            },
            'multi_timeframe_confluence': {
                'module': 'multi_timeframe_confluence_strategy',
                'class': 'MultiTimeframeConfluenceStrategy'
            },
            'engulfing_multi_tf': {
                'module': 'engulfing_multi_tf',
                'class': 'EngulfingMultiTimeframeStrategy'
            }
            # Note: strategy_pnl_enhanced contains utility functions, not a strategy class
        }
        
        for strategy_name, strategy_config in strategies_config.items():
            if not strategy_config.get('enabled', False):
                continue
                
            try:
                # Import strategy module
                if strategy_name in strategy_mapping:
                    mapping = strategy_mapping[strategy_name]
                    module_name = mapping['module']
                    class_name = mapping['class']
                    
                    # Import the strategy module with correct path
                    module = importlib.import_module(f"strategy.{module_name}")
                    strategy_class = getattr(module, class_name)
                    
                    # Create strategy instance with a generic exchange adapter (will be replaced per analysis)
                    strategy_instance = strategy_class(
                        config=strategy_config,
                        exchange=None,  # Will be set per analysis with exchange-specific adapter
                        database=None,   # Can be None for now
                        redis_client=None  # Can be None for now
                    )
                    
                    # Enable detailed logging mode for condition logger even without Redis
                    if hasattr(strategy_instance, '_condition_logger'):
                        strategy_instance._condition_logger.detailed_mode = True
                        logger.info(f"Enabled detailed condition logging for {strategy_name}")
                    
                    # Set exchange_name attribute for proper logging
                    strategy_instance.exchange_name = None  # Will be set per analysis
                    
                    self.strategies[strategy_name] = {
                        'instance': strategy_instance,
                        'config': strategy_config,
                        'enabled': True,
                        'last_analysis': None
                    }
                    
                    # Also add to global strategies for backward compatibility
                    strategies[strategy_name] = self.strategies[strategy_name]
                    
                    logger.info(f"Initialized strategy: {strategy_name}")
                    
            except Exception as e:
                logger.error(f"Failed to initialize strategy {strategy_name}: {e}")
                continue
                
    async def analyze_pair(self, exchange_name: str, pair: str,
                          timeframes: List[str] = ['1h', '4h', '15m'],
                          strategy_allowlist: Optional[List[str]] = None) -> Dict[str, Any]:  # PnL-FIX v4 / v6
        """Analyze a trading pair using market regime-based strategy selection.

        Args:
            strategy_allowlist: optional per-call allowlist. When provided,
                only strategies in this list are run for THIS analysis (in
                addition to passing the regime/enabled checks). When None,
                all enabled & regime-applicable strategies run. PnL-FIX v6
                replaces the previous pattern of mutating
                ``strategies[name]['enabled']`` globally, which was racy
                across concurrent analyses.
        """
        try:
            # Get market data for all timeframes
            market_data = await self._get_market_data_for_strategy(
                exchange_name, pair, timeframes
            )
            
            if not market_data:
                logger.warning(f"No market data available for {pair} on {exchange_name}")
                _record_pair_strategy_snapshot(
                    exchange_name,
                    pair,
                    None,
                    analysis_status="no_market_data",
                    detail=f"No OHLCV / market data for {pair} on {exchange_name}",
                )
                return {}
            
            # STEP 1: Detect Market Regime using primary timeframe
            primary_timeframe = timeframes[0]
            primary_ohlcv = market_data.get(primary_timeframe)
            
            if primary_ohlcv is None or len(primary_ohlcv) < 50:
                logger.warning(f"Insufficient data for regime detection: {pair} on {exchange_name}")
                # Fallback to all strategies if we can't detect regime
                market_regime = MarketRegime.LOW_VOLATILITY
                applicable_strategies = list(strategies.keys())
                regime_analysis = {"reason": "insufficient_data"}
            else:
                # Detect market regime
                market_regime, regime_analysis = market_regime_detector.detect_regime(primary_ohlcv, pair)
                detected_regime = market_regime.value
                regime_score = float(regime_analysis.get("regime_score", 0.0) or 0.0)
                runner_up_score = float(regime_analysis.get("runner_up_score", 0.0) or 0.0)
                stable_meta = self._resolve_stable_regime(
                    exchange_name=exchange_name,
                    pair=pair,
                    detected_regime=detected_regime,
                    regime_score=regime_score,
                    runner_up_score=runner_up_score,
                )
                stable_regime_value = stable_meta["stable_regime"]
                market_regime = MarketRegime(stable_regime_value)
                applicable_strategies = market_regime_detector.get_applicable_strategies(market_regime, pair, exchange_name)
                # Independent strategy: always evaluate when enabled, outside regime routing.
                if (
                    "rsi_oversold_override" in strategies
                    and strategies["rsi_oversold_override"].get("enabled", False)
                    and "rsi_oversold_override" not in applicable_strategies
                ):
                    applicable_strategies.append("rsi_oversold_override")
                if (
                    "rsi_oversold_checklist" in strategies
                    and strategies["rsi_oversold_checklist"].get("enabled", False)
                    and "rsi_oversold_checklist" not in applicable_strategies
                ):
                    applicable_strategies.append("rsi_oversold_checklist")
                
                # Log regime detection
                logger.info(
                    f"📊 [MARKET REGIME] {pair} on {exchange_name}: "
                    f"detected={detected_regime} stable={stable_regime_value} "
                    f"score={regime_score:.3f} age={stable_meta['regime_age_seconds']:.0f}s"
                )
                logger.info(f"🎯 [STRATEGY SELECTION] Applicable strategies: {applicable_strategies}")
                regime_analysis["stable_regime"] = stable_regime_value
                regime_analysis["previous_regime"] = stable_meta["previous_regime"]
                regime_analysis["regime_age_seconds"] = stable_meta["regime_age_seconds"]
                regime_analysis["detected_regime"] = detected_regime
            
            analysis_results = {
                'pair': pair,
                'exchange': exchange_name,
                'timestamp': datetime.utcnow().isoformat(),
                'market_regime': market_regime.value,
                'stable_regime': regime_analysis.get("stable_regime", market_regime.value),
                'detected_regime': regime_analysis.get("detected_regime", market_regime.value),
                'previous_regime': regime_analysis.get("previous_regime", market_regime.value),
                'regime_score': float(regime_analysis.get("regime_score", 0.0) or 0.0),
                'regime_age_seconds': float(regime_analysis.get("regime_age_seconds", 0.0) or 0.0),
                'regime_analysis': regime_analysis,
                'applicable_strategies': applicable_strategies,
                'strategies': {},
                'consensus': {}
            }
            
            # STEP 2: Run analysis only for applicable strategies
            pair_specific_entry = await self._load_pair_specific_config_entry(pair, exchange_name)
            allowlist_set = set(strategy_allowlist) if strategy_allowlist else None
            strategies_analyzed = 0
            for strategy_name in applicable_strategies:
                if strategy_name not in strategies:
                    logger.warning(f"Strategy {strategy_name} not available, skipping")
                    continue

                strategy_data = strategies[strategy_name]
                if not strategy_data['enabled']:
                    logger.info(f"Strategy {strategy_name} disabled, skipping")
                    continue

                # PnL-FIX v6: per-call allowlist replaces the old global toggle
                # in analyze_pair_internal which mutated strategies[name]['enabled'].
                if allowlist_set is not None and strategy_name not in allowlist_set:
                    logger.debug(
                        f"Strategy {strategy_name} not in per-call allowlist, skipping"
                    )
                    continue
                    
                try:
                    logger.info(f"🔍 [STRATEGY ANALYSIS] {strategy_name} analyzing {pair} on {exchange_name} (regime: {market_regime.value})")
                    strategy_instance = strategy_data['instance']
                    
                    # Create exchange-specific adapter for this analysis
                    exchange_specific_adapter = ExchangeAdapter(exchange_service_url, exchange_name)
                    
                    # Set exchange and exchange name for proper logging and functionality
                    strategy_instance.exchange = exchange_specific_adapter
                    strategy_instance.exchange_name = exchange_name
                    
                    # Initialize strategy for this pair with detailed logging
                    logger.info(f"🔧 [STRATEGY INIT] Initializing {strategy_name} for {pair}")
                    await strategy_instance.initialize(pair)
                    
                    # Apply pair-specific configuration overrides (entry loaded once per pair analysis)
                    self._apply_pair_strategy_overrides(
                        strategy_instance, strategy_name, pair, pair_specific_entry
                    )
                    
                    # Update strategy with market data (use primary timeframe)
                    if primary_timeframe in market_data:
                        logger.info(f"📊 [STRATEGY DATA] Updating {strategy_name} with {primary_timeframe} data ({len(market_data[primary_timeframe])} candles)")
                        await strategy_instance.update(market_data[primary_timeframe])
                    
                    # CRITICAL FIX: Set market regime in strategy state
                    if hasattr(strategy_instance, 'state') and strategy_instance.state:
                        strategy_instance.state.market_regime = market_regime.value
                    elif hasattr(strategy_instance, 'state'):
                        # Initialize state with market regime if state exists but is empty
                        from types import SimpleNamespace
                        strategy_instance.state = SimpleNamespace()
                        strategy_instance.state.market_regime = market_regime.value
                    
                    # Generate signal - pass full market_data AND the exchange adapter for strategies that need multiple timeframes
                    logger.info(f"🎯 [SIGNAL GENERATION] {strategy_name} generating signal for {pair} on {exchange_name}")
                    signal, confidence, strength = await strategy_instance.generate_signal(
                        market_data, pair=pair, timeframe=primary_timeframe, exchange_adapter=exchange_specific_adapter
                    )
                    
                    # CRITICAL: Sanitize confidence and strength values to prevent JSON serialization errors
                    try:
                        # Check for NaN or infinity in confidence
                        if isinstance(confidence, (int, float)):
                            if np.isnan(confidence) or np.isinf(confidence):
                                logger.warning(f"🔧 [SANITIZE] Invalid confidence value {confidence} from {strategy_name}, setting to 0.0")
                                confidence = 0.0
                        else:
                            confidence = 0.0
                            
                        # Check for NaN or infinity in strength  
                        if isinstance(strength, (int, float)):
                            if np.isnan(strength) or np.isinf(strength):
                                logger.warning(f"🔧 [SANITIZE] Invalid strength value {strength} from {strategy_name}, setting to 0.0")
                                strength = 0.0
                        else:
                            strength = 0.0
                            
                    except Exception as sanitize_error:
                        logger.error(f"❌ [SANITIZE] Error sanitizing values from {strategy_name}: {sanitize_error}")
                        confidence = 0.0
                        strength = 0.0
                    
                    # Store results with detailed logging
                    # Clean strategy state to ensure JSON serialization
                    clean_state = self._clean_strategy_state_for_serialization(strategy_instance)
                    
                    analysis_results['strategies'][strategy_name] = {
                        'signal': signal,
                        'confidence': float(confidence),  # Ensure it's a regular Python float
                        'strength': float(strength),      # Ensure it's a regular Python float
                        'market_regime': clean_state['market_regime'],
                        'timestamp': datetime.utcnow().isoformat(),
                        'selected_for_regime': market_regime.value,
                        'state': clean_state
                    }
                    if strategy_name == "macd_momentum":
                        await self._persist_macd_analysis_log(
                            exchange_name, pair, analysis_results['strategies'][strategy_name]
                        )
                    
                    # Log the final result
                    signal_emoji = "🟢" if signal == "buy" else "🔴" if signal == "sell" else "⚪"
                    logger.info(f"📈 [STRATEGY RESULT] {strategy_name}: {signal_emoji} {signal.upper()} | "
                              f"Confidence: {confidence:.2f} | Strength: {strength:.2f} | "
                              f"Regime: {getattr(strategy_instance.state, 'market_regime', 'unknown')}")
                    
                    # Update last analysis time
                    strategy_data['last_analysis'] = datetime.utcnow()
                    strategies_analyzed += 1
                    _bump_strategy_cumulative_run(strategy_name)
                    
                except Exception as e:
                    logger.error(f"Error analyzing {pair} with {strategy_name}: {e}")
                    analysis_results['strategies'][strategy_name] = {
                        'error': str(e),
                        'timestamp': datetime.utcnow().isoformat(),
                        'selected_for_regime': market_regime.value
                    }
                    
            # Calculate consensus with detailed logging
            logger.info(f"🤝 [CONSENSUS] Calculating consensus from {strategies_analyzed} regime-selected strategies "
                       f"(regime: {market_regime.value})")
            analysis_results['consensus'] = self._calculate_consensus(
                analysis_results['strategies'],
                regime_context={
                    "market_regime": analysis_results.get("market_regime"),
                    "stable_regime": analysis_results.get("stable_regime"),
                    "regime_score": analysis_results.get("regime_score"),
                    "rsi_15m_value": float(
                        (
                            (
                                analysis_results.get("strategies", {})
                                .get("rsi_oversold_override", {})
                                .get("state", {})
                                .get("indicators", {})
                            ).get("rsi_15m", 50.0)
                        ) or 50.0
                    ),
                    "rsi_15m_oversold_buy_override": bool(
                        str(
                            (
                                analysis_results.get("strategies", {})
                                .get("rsi_oversold_override", {})
                                .get("signal", "hold")
                            )
                        ).lower() == "buy"
                    ),
                    "rsi_checklist_buy_override": bool(
                        str(
                            (
                                analysis_results.get("strategies", {})
                                .get("rsi_oversold_checklist", {})
                                .get("signal", "hold")
                            )
                        ).lower() == "buy"
                    ),
                },
            )
            
            consensus = analysis_results['consensus']
            consensus_emoji = "🟢" if consensus['signal'] == "buy" else "🔴" if consensus['signal'] == "sell" else "⚪"
            logger.info(f"🎯 [CONSENSUS RESULT] {pair} on {exchange_name}: {consensus_emoji} {consensus['signal'].upper()} | "
                       f"Confidence: {consensus['confidence']:.2f} | Agreement: {consensus['agreement']:.2f} | "
                       f"Participating: {consensus['participating_strategies']} | Regime: {market_regime.value}")
            
            # Cache results
            cache_key = f"{exchange_name}_{pair}_{int(datetime.utcnow().timestamp() / 300)}"  # 5-minute cache
            signal_cache[cache_key] = analysis_results

            _record_pair_strategy_snapshot(exchange_name, pair, analysis_results)

            return analysis_results

        except Exception as e:
            logger.error(f"Error analyzing pair {pair} on {exchange_name}: {e}")
            _record_pair_strategy_snapshot(
                exchange_name,
                pair,
                None,
                analysis_status="error",
                detail=str(e),
            )
            return {}
            
    # SPOT day-trading consensus thresholds (PnL-FIX v7).
    # These live as class-level constants so they're easy to discover/tune.
    PRIMARY_OVERRIDE_THRESHOLD = 0.58   # Calibrated: primary strategy can lead in its regime
    SELL_VETO_THRESHOLD = 0.60          # Calibrated: only strong SELL blocks new BUY consensus

    def _calculate_consensus(
        self,
        strategy_results: Dict[str, Any],
        regime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Calculate regime-aware consensus with strategy weighting.

        PnL-FIX v7 — SPOT day-trading hardening:
          A) HOLDs no longer dilute the directional confidence/strength
             average. Previously a PRIMARY BUY @ 0.60 averaged with a
             SECONDARY HOLD @ 0.0 produced 0.35 → below
             min_confidence_threshold → no trade. HOLDs still count toward
             the agreement % and the unweighted breakdown for visibility.
          B) Primary-override: if the regime PRIMARY (weight 1.0) emits a
             BUY with confidence >= PRIMARY_OVERRIDE_THRESHOLD AND no other
             strategy is voting SELL above the veto threshold, we promote
             the primary's BUY to consensus even when secondaries HOLD.
             Spot-only design choice: secondaries can confirm but should
             not be able to silently veto the regime specialist.
          D) SELL-veto: any strategy emitting SELL with confidence
             >= SELL_VETO_THRESHOLD blocks a BUY consensus and forces HOLD.
             SPOT semantics: we never open shorts, so "veto" only means
             "do not enter a new long" — exit-side logic is handled by the
             orchestrator/strategy SL/TP/trailing rules.
        """
        try:
            ctx = regime_context or {}
            stable_regime = str(ctx.get("stable_regime") or ctx.get("market_regime") or "unknown")
            regime_score = float(ctx.get("regime_score", 0.0) or 0.0)
            valid_signals = []
            weighted_confidence_directional = 0.0   # Fix A: excludes HOLDs
            weighted_strength_directional = 0.0
            directional_weight = 0.0
            weighted_signal_counts = {'buy': 0.0, 'sell': 0.0, 'hold': 0.0}
            signal_counts = {'buy': 0, 'sell': 0, 'hold': 0}
            total_weight = 0.0

            primary_buy_signal = None               # Fix B
            max_sell_confidence = 0.0               # Fix D
            sell_veto_strategy = None               # for logging

            # Define strategy weights based on regime appropriateness
            regime_weights = {
                'primary': 1.0,    # First strategy for this regime
                'secondary': 0.7,  # Supporting strategies
                'fallback': 0.5    # Available but not optimal
            }

            for strategy_name, result in strategy_results.items():
                if 'error' in result:
                    continue

                signal = result.get('signal', 'hold')
                if signal not in ('buy', 'sell', 'hold'):
                    continue

                confidence = float(result.get('confidence', 0) or 0)
                strength = float(result.get('strength', 0) or 0)
                selected_for_regime = result.get('selected_for_regime', 'unknown')

                # Independent/non-consensus strategies do not contribute to
                # weighted consensus voting.
                # - RSI overrides are handled by explicit RSI override logic.
                # - MACD momentum runs for telemetry/standalone visibility but
                #   must not influence consensus voting.
                if strategy_name == "rsi_oversold_override":
                    valid_signals.append({
                        'strategy': strategy_name,
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'weight': 0.0,
                        'regime': selected_for_regime,
                        'is_primary': False,
                    })
                    continue
                if strategy_name == "rsi_oversold_checklist":
                    valid_signals.append({
                        'strategy': strategy_name,
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'weight': 0.0,
                        'regime': selected_for_regime,
                        'is_primary': False,
                    })
                    continue
                if strategy_name == "macd_momentum":
                    valid_signals.append({
                        'strategy': strategy_name,
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'weight': 0.0,
                        'regime': selected_for_regime,
                        'is_primary': False,
                    })
                    continue

                # Determine strategy weight + primary flag based on regime
                strategy_weight = regime_weights['secondary']
                is_primary = False
                if strategy_name == 'heikin_ashi' and selected_for_regime in (
                    'trending_up', 'trending_down', 'high_volatility'
                ):
                    strategy_weight = regime_weights['primary']
                    is_primary = True
                elif strategy_name == 'vwma_hull' and selected_for_regime in ('breakout',):
                    strategy_weight = regime_weights['primary']
                    is_primary = True
                elif strategy_name == 'macd_momentum' and selected_for_regime in (
                    'trending_up', 'breakout', 'high_volatility'
                ):
                    strategy_weight = regime_weights['primary']
                    is_primary = True
                elif strategy_name == 'multi_timeframe_confluence' and selected_for_regime in (
                    'sideways', 'low_volatility'
                ):
                    strategy_weight = regime_weights['primary']
                    is_primary = True
                elif strategy_name == 'engulfing_multi_tf' and selected_for_regime in (
                    'reversal_zone',
                ):
                    strategy_weight = regime_weights['primary']
                    is_primary = True

                valid_signals.append({
                    'strategy': strategy_name,
                    'signal': signal,
                    'confidence': confidence,
                    'strength': strength,
                    'weight': strategy_weight,
                    'regime': selected_for_regime,
                    'is_primary': is_primary,
                })

                weighted_signal_counts[signal] += strategy_weight
                signal_counts[signal] += 1
                total_weight += strategy_weight

                # Fix A: only BUY/SELL contribute to the confidence average
                if signal in ('buy', 'sell'):
                    weighted_confidence_directional += confidence * strategy_weight
                    weighted_strength_directional += strength * strategy_weight
                    directional_weight += strategy_weight

                # Fix D: track strongest SELL across all strategies (veto)
                if signal == 'sell' and confidence > max_sell_confidence:
                    max_sell_confidence = confidence
                    sell_veto_strategy = strategy_name

                # Fix B: track strongest PRIMARY BUY (override candidate)
                if (
                    is_primary
                    and signal == 'buy'
                    and confidence >= self.PRIMARY_OVERRIDE_THRESHOLD
                ):
                    if (primary_buy_signal is None
                            or confidence > primary_buy_signal['confidence']):
                        primary_buy_signal = {
                            'strategy': strategy_name,
                            'confidence': confidence,
                            'strength': strength,
                        }

            if not valid_signals:
                return {
                    'signal': 'hold',
                    'confidence': 0,
                    'strength': 0,
                    'agreement': 0,
                    'participating_strategies': 0,
                    'signal_breakdown': signal_counts,
                }

            # ---------- Step 1: weighted-majority consensus (existing behavior) ----------
            max_weighted_count = max(weighted_signal_counts.values())
            consensus_signal = 'hold'
            if weighted_signal_counts['buy'] == max_weighted_count and weighted_signal_counts['buy'] > 0:
                consensus_signal = 'buy'
            elif weighted_signal_counts['sell'] == max_weighted_count and weighted_signal_counts['sell'] > 0:
                consensus_signal = 'sell'

            # ---------- Step 2 (Fix B): primary-override ---------------------------------
            primary_overrode = False
            if (
                primary_buy_signal is not None
                and consensus_signal != 'buy'
                and max_sell_confidence < self.SELL_VETO_THRESHOLD
            ):
                consensus_signal = 'buy'
                primary_overrode = True
                logger.info(
                    f"🎯 [PRIMARY OVERRIDE] {primary_buy_signal['strategy']} "
                    f"BUY @ {primary_buy_signal['confidence']:.2f} promoted to consensus "
                    f"(no SELL veto — max_sell={max_sell_confidence:.2f})"
                )

            # ---------- Step 3 (Fix D): SELL-veto on BUY consensus -----------------------
            if consensus_signal == 'buy' and max_sell_confidence >= self.SELL_VETO_THRESHOLD:
                logger.info(
                    f"🛑 [SELL VETO] BUY consensus blocked: {sell_veto_strategy} "
                    f"SELL @ {max_sell_confidence:.2f} >= {self.SELL_VETO_THRESHOLD}"
                )
                consensus_signal = 'hold'
                primary_overrode = False

            # ---------- Step 4: RSI 15m oversold override --------------------------------
            # Operator note: overrides run after SELL veto — see operator/README.md
            rsi_oversold_override = bool(ctx.get("rsi_15m_oversold_buy_override", False))
            rsi_checklist_override = bool(ctx.get("rsi_checklist_buy_override", False))
            rsi_15m_value = float(ctx.get("rsi_15m_value", 50.0) or 50.0)
            if rsi_oversold_override:
                consensus_signal = 'buy'
                logger.info(
                    f"🟢 [RSI OVERRIDE] Forcing BUY consensus: 15m RSI={rsi_15m_value:.2f} < 30.00"
                )
            if rsi_checklist_override:
                consensus_signal = 'buy'
                logger.info("🟢 [RSI CHECKLIST OVERRIDE] Forcing BUY consensus from checklist strategy")

            # ---------- Confidence / agreement / sanitize -------------------------------
            total_strategies = len(valid_signals)
            agreement = (max_weighted_count / total_weight) * 100 if total_weight > 0 else 0.0

            # Fix A: average over directional weight only
            if directional_weight > 0:
                avg_confidence = weighted_confidence_directional / directional_weight
                avg_strength = weighted_strength_directional / directional_weight
            else:
                avg_confidence = 0.0
                avg_strength = 0.0

            # If consensus is HOLD (e.g. all HOLDs or vetoed), confidence is 0
            if consensus_signal == 'hold':
                avg_confidence = 0.0
                avg_strength = 0.0

            # If primary-override fired, the consensus confidence is at least
            # the primary's own confidence (don't average it down with other
            # non-directional voters that didn't see the setup).
            if primary_overrode and primary_buy_signal is not None:
                avg_confidence = max(avg_confidence, primary_buy_signal['confidence'])
                avg_strength = max(avg_strength, primary_buy_signal['strength'])

            # Independent RSI override sets a high-confidence BUY floor.
            if rsi_oversold_override:
                avg_confidence = max(avg_confidence, 0.95)
                avg_strength = max(avg_strength, 0.90)
            if rsi_checklist_override:
                avg_confidence = max(avg_confidence, 0.97)
                avg_strength = max(avg_strength, 0.92)

            try:
                if np.isnan(avg_confidence) or np.isinf(avg_confidence):
                    logger.warning(f"🔧 [CONSENSUS SANITIZE] Invalid avg_confidence {avg_confidence}, setting to 0.0")
                    avg_confidence = 0.0
                if np.isnan(avg_strength) or np.isinf(avg_strength):
                    logger.warning(f"🔧 [CONSENSUS SANITIZE] Invalid avg_strength {avg_strength}, setting to 0.0")
                    avg_strength = 0.0
                if np.isnan(agreement) or np.isinf(agreement):
                    logger.warning(f"🔧 [CONSENSUS SANITIZE] Invalid agreement {agreement}, setting to 0.0")
                    agreement = 0.0
            except Exception as consensus_sanitize_error:
                logger.error(f"❌ [CONSENSUS SANITIZE] Error sanitizing consensus values: {consensus_sanitize_error}")
                avg_confidence = 0.0
                avg_strength = 0.0
                agreement = 0.0

            return {
                'signal': consensus_signal,
                'confidence': float(avg_confidence),
                'strength': float(avg_strength),
                'agreement': float(agreement),
                'participating_strategies': total_strategies,
                'signal_breakdown': signal_counts,
                'weighted_breakdown': weighted_signal_counts,
                'primary_override': primary_overrode,
                'sell_veto_max': float(max_sell_confidence),
                'rsi_15m_value': rsi_15m_value,
                'rsi_15m_oversold_buy_override': bool(rsi_oversold_override),
                'rsi_checklist_buy_override': bool(rsi_checklist_override),
                'market_regime': str(ctx.get("market_regime") or stable_regime),
                'stable_regime': stable_regime,
                'regime_score': regime_score,
            }

        except Exception as e:
            logger.error(f"Error calculating consensus: {e}")
            return {
                'signal': 'hold',
                'confidence': 0,
                'strength': 0,
                'agreement': 0,
                'participating_strategies': 0
            }
            
    async def _load_pair_specific_config_entry(
        self, pair: str, exchange_name: str
    ) -> Optional[Dict[str, Any]]:
        """Load one entry from pair_specific_configs if the pair exists and exchange matches."""
        config_url = f"{config_service_url}/api/v1/config/all"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(config_url)
        except Exception as e:
            logger.error(f"Error fetching pair-specific config for {pair}: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"Failed to get config data: {response.status_code}")
            return None
        pair_configs = response.json().get("pair_specific_configs") or {}
        pair_config = pair_configs.get(pair)
        if not pair_config:
            logger.debug(f"No pair-specific configuration for {pair}")
            return None
        if exchange_name and pair_config.get("exchange") != exchange_name:
            logger.debug(
                f"Exchange mismatch for pair {pair}: expected {exchange_name}, "
                f"found {pair_config.get('exchange')}"
            )
            return None
        return pair_config

    def _apply_pair_strategy_overrides(
        self,
        strategy_instance,
        strategy_name: str,
        pair: str,
        pair_config: Optional[Dict[str, Any]],
    ) -> None:
        """Apply strategy_overrides from a pre-resolved pair_specific block to the instance."""
        if not pair_config:
            return
        overrides = (
            pair_config.get("market_conditions", {})
            .get("strategy_overrides", {})
            .get(strategy_name, {})
        )
        if not overrides:
            logger.debug(f"No specific overrides for {strategy_name} on {pair}")
            return
        logger.info(
            f"🎛️ [PAIR CONFIG] Applying {len(overrides)} overrides for {strategy_name} on {pair}"
        )
        for param_name, param_value in overrides.items():
            if hasattr(strategy_instance, param_name):
                old_value = getattr(strategy_instance, param_name)
                setattr(strategy_instance, param_name, param_value)
                logger.info(f"  📝 {param_name}: {old_value} → {param_value}")
            else:
                logger.warning(f"  ❌ Parameter {param_name} not found in {strategy_name}")
            
    async def _get_market_data_for_strategy(self, exchange_name: str, symbol: str,
                                           timeframes: List[str] = ['1h', '4h', '15m']) -> Dict[str, pd.DataFrame]:  # PnL-FIX v4
        """Get market data for strategy analysis"""
        try:
            market_data = {}
            # Hard freshness gate: do not analyze on stale OHLCV snapshots.
            # If exchange data lags too much, we skip that timeframe and fail-safe to no entry.
            tf_seconds_map = {
                '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
                '1h': 3600, '2h': 7200, '4h': 14400,
                '6h': 21600, '12h': 43200, '1d': 86400,
            }
            
            for timeframe in timeframes:
                # Retry logic for handling temporary timeout errors
                max_retries = 3
                response: Optional[httpx.Response] = None

                # Per-timeframe candle limits.
                # We drop the in-progress (unclosed) last candle below, so the
                # effective usable count is (limit - 1). RSI checklist uses
                # EMA200 and requires >205 candles, therefore 1h/15m must fetch
                # materially more than 110.
                if timeframe in ("4h", "1d"):
                    ohlcv_limit = 150
                elif timeframe in ("1h", "15m"):
                    ohlcv_limit = 240
                else:
                    ohlcv_limit = 110

                for retry in range(max_retries):
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            response = await client.get(
                                f"{exchange_service_url}/api/v1/market/ohlcv/{exchange_name}/{symbol}",
                                params={'timeframe': timeframe, 'limit': ohlcv_limit}
                            )
                            
                            # If successful, break out of retry loop
                            break
                    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as timeout_error:
                        if retry < max_retries - 1:
                            wait_time = 2 ** retry  # Exponential backoff: 1s, 2s, 4s
                            logger.warning(f"Timeout getting {timeframe} data for {symbol} on {exchange_name}, retry {retry + 1}/{max_retries} in {wait_time}s")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            logger.error(f"Failed to get {timeframe} data for {symbol} on {exchange_name} after {max_retries} retries: {type(timeout_error).__name__}")
                            response = None
                            break
                    except Exception as e:
                        logger.error(f"Failed to get {timeframe} data for {symbol} on {exchange_name}: {type(e).__name__}: {str(e)}")
                        response = None
                        break

                if response is None:
                    continue
                
                try:
                    if response.status_code == 200:
                        ohlcv_data = response.json()
                        
                        # Handle nested data format from exchange service
                        if 'data' in ohlcv_data and isinstance(ohlcv_data['data'], dict):
                            # New format: {data: {timestamp: [], open: [], ...}}
                            data = ohlcv_data['data']
                            df = pd.DataFrame({
                                'timestamp': data['timestamp'],
                                'open': data['open'],
                                'high': data['high'],
                                'low': data['low'],
                                'close': data['close'],
                                'volume': data['volume']
                            })
                        elif 'data' in ohlcv_data:
                            # Fallback: data is already a list/array format
                            df = pd.DataFrame(ohlcv_data['data'])
                        else:
                            # Legacy format: direct data
                            df = pd.DataFrame(ohlcv_data)
                        
                        # Validate data structure
                        if df.empty:
                            logger.warning(f"Empty OHLCV data for {symbol} on {exchange_name} {timeframe}")
                            continue
                        
                        # Validate we have the required columns
                        required_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                        if not all(col in df.columns for col in required_columns):
                            logger.warning(f"Missing required columns for {symbol} on {exchange_name} {timeframe}: {df.columns.tolist()}")
                            continue
                        
                        # Handle timestamp conversion - check if it's already a datetime string
                        try:
                            if isinstance(df['timestamp'].iloc[0], str):
                                df['timestamp'] = pd.to_datetime(df['timestamp'])
                            else:
                                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                            df.set_index('timestamp', inplace=True)

                            # Ensure numeric columns are properly typed
                            for col in ['open', 'high', 'low', 'close', 'volume']:
                                df[col] = pd.to_numeric(df[col], errors='coerce')

                            # PnL-FIX v4: Drop the in-progress (unclosed) last candle.
                            # Evidence from live logs: on 15m/1h pairs the last row
                            # was consistently showing the *same* tiny volume across
                            # both TFs (e.g. BTC 1h=15m=0.009) — proof the exchange
                            # returns the currently-building bar. That partial bar
                            # poisons `df['volume'].iloc[-1]` and makes every
                            # volume veto fail, forcing HOLD across the bot.
                            tf_seconds = tf_seconds_map.get(timeframe, 0)
                            if tf_seconds and len(df) >= 2:
                                last_ts = df.index[-1]
                                now_utc = pd.Timestamp.utcnow().tz_localize(None) if last_ts.tzinfo is None else pd.Timestamp.utcnow()
                                # If the bar's close time (open + tf) is in the future,
                                # the last row is still being built — drop it.
                                if (last_ts + pd.Timedelta(seconds=tf_seconds)) > now_utc:
                                    df = df.iloc[:-1]

                            # After dropping potentially in-progress candle, reject stale snapshots.
                            if tf_seconds and len(df) >= 1:
                                last_closed_open_ts = df.index[-1]
                                if getattr(last_closed_open_ts, "tzinfo", None) is not None:
                                    now_naive = pd.Timestamp.utcnow()
                                    try:
                                        last_closed_open_ts = last_closed_open_ts.tz_convert("UTC").tz_localize(None)
                                    except Exception:
                                        last_closed_open_ts = last_closed_open_ts.tz_localize(None)
                                else:
                                    now_naive = pd.Timestamp.utcnow().tz_localize(None)

                                # Latest closed bar close time = open + timeframe seconds.
                                last_closed_close_ts = last_closed_open_ts + pd.Timedelta(seconds=tf_seconds)
                                age_seconds = max(
                                    0.0, float((now_naive - last_closed_close_ts).total_seconds())
                                )
                                # Allow up to 2 bars of lag + 60s jitter.
                                max_age_seconds = float((2 * tf_seconds) + 60)
                                if age_seconds > max_age_seconds:
                                    logger.warning(
                                        "🚫 [DATA FRESHNESS] Stale %s candles for %s on %s: "
                                        "last_closed_open=%s last_closed_close=%s age=%.0fs max=%.0fs. "
                                        "Skipping timeframe to prevent stale-trade decisions.",
                                        timeframe,
                                        symbol,
                                        exchange_name,
                                        last_closed_open_ts.isoformat() if hasattr(last_closed_open_ts, "isoformat") else str(last_closed_open_ts),
                                        last_closed_close_ts.isoformat() if hasattr(last_closed_close_ts, "isoformat") else str(last_closed_close_ts),
                                        age_seconds,
                                        max_age_seconds,
                                    )
                                    continue

                            market_data[timeframe] = df
                            logger.info(f"✅ [DATA FETCH] Got {len(df)} {timeframe} candles for {symbol} on {exchange_name}")
                            
                        except Exception as timestamp_error:
                            logger.error(f"❌ [DATA ERROR] Timestamp processing error for {symbol} on {exchange_name} {timeframe}: {timestamp_error}")
                            continue
                    else:
                        logger.warning(f"Failed to get {timeframe} data for {symbol} on {exchange_name}: {response.status_code}")
                        
                except Exception as e:
                    logger.error(f"Failed to get {timeframe} data for {symbol} on {exchange_name}: {type(e).__name__}: {str(e)}")
                    continue
                    
            return market_data
            
        except Exception as e:
            logger.error(f"Error getting market data for {symbol} on {exchange_name}: {e}")
            return {}

    def _clean_strategy_state_for_serialization(self, strategy_instance) -> Dict[str, Any]:
        """Clean strategy state to ensure JSON serialization compatibility"""
        try:
            # Get the strategy state
            state = strategy_instance.state
            
            # Create a clean copy of indicators, converting numpy/pandas objects
            clean_indicators = {}
            if hasattr(state, 'indicators') and state.indicators:
                for key, value in state.indicators.items():
                    if isinstance(value, (pd.Series, pd.DataFrame)):
                        # Convert to list of values, handling NaN and infinity
                        if not value.empty:
                            cleaned_list = []
                            for val in value.tolist():
                                if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                                    cleaned_list.append(0.0)  # Replace NaN/inf with 0
                                else:
                                    cleaned_list.append(val)
                            clean_indicators[key] = cleaned_list
                        else:
                            clean_indicators[key] = []
                    elif isinstance(value, np.ndarray):
                        # Handle numpy arrays with NaN/inf values
                        cleaned_list = []
                        for val in value.tolist():
                            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                                cleaned_list.append(0.0)  # Replace NaN/inf with 0
                            else:
                                cleaned_list.append(val)
                        clean_indicators[key] = cleaned_list
                    elif isinstance(value, np.bool_):
                        clean_indicators[key] = bool(value)
                    elif isinstance(value, (np.integer, np.floating)):
                        if isinstance(value, np.floating):
                            # Check for NaN or infinity
                            float_val = float(value)
                            if np.isnan(float_val) or np.isinf(float_val):
                                clean_indicators[key] = 0.0  # Replace NaN/inf with 0
                            else:
                                clean_indicators[key] = float_val
                        else:
                            clean_indicators[key] = int(value)
                    else:
                        clean_indicators[key] = value
            
            # Create a clean copy of patterns
            clean_patterns = {}
            if hasattr(state, 'patterns') and state.patterns:
                for key, value in state.patterns.items():
                    if isinstance(value, dict):
                        clean_patterns[key] = {}
                        for k, v in value.items():
                            if isinstance(v, np.bool_):
                                clean_patterns[key][k] = bool(v)
                            elif isinstance(v, (np.integer, np.floating)):
                                if isinstance(v, np.floating):
                                    float_val = float(v)
                                    if np.isnan(float_val) or np.isinf(float_val):
                                        clean_patterns[key][k] = 0.0  # Replace NaN/inf with 0
                                    else:
                                        clean_patterns[key][k] = float_val
                                else:
                                    clean_patterns[key][k] = int(v)
                            elif isinstance(v, datetime):
                                clean_patterns[key][k] = v.isoformat()
                            else:
                                clean_patterns[key][k] = v
                    else:
                        clean_patterns[key] = value
            
            # Create a clean copy of performance
            clean_performance = {}
            if hasattr(state, 'performance') and state.performance:
                for key, value in state.performance.items():
                    if isinstance(value, np.bool_):
                        clean_performance[key] = bool(value)
                    elif isinstance(value, (np.integer, np.floating)):
                        if isinstance(value, np.floating):
                            float_val = float(value)
                            if np.isnan(float_val) or np.isinf(float_val):
                                clean_performance[key] = 0.0  # Replace NaN/inf with 0
                            else:
                                clean_performance[key] = float_val
                        else:
                            clean_performance[key] = int(value)
                    else:
                        clean_performance[key] = value
            
            return {
                'indicators': clean_indicators,
                'patterns': clean_patterns,
                'performance': clean_performance,
                'market_regime': getattr(state, 'market_regime', 'unknown')
            }
            
        except Exception as e:
            logger.error(f"Error cleaning strategy state: {e}")
            return {
                'indicators': {},
                'patterns': {},
                'performance': {},
                'market_regime': 'unknown'
            }

# Global strategy manager
strategy_manager = None

async def get_config_from_service() -> Dict[str, Any]:
    """Get configuration from config service"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{config_service_url}/api/v1/config/strategies")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to get config from service: {e}")
        # Fallback to default strategies
        return {
            'vwma_hull': {
                'enabled': True,
                'parameters': {}
            },
            'heikin_ashi': {
                'enabled': True,
                'parameters': {}
            }
        }

async def initialize_strategies():
    """Initialize strategy manager"""
    global strategy_manager
    try:
        config = await get_config_from_service()
        # FIXED: Pass config directly instead of wrapping in 'strategies' key
        strategy_manager = StrategyManager(config)
        logger.info("Strategy service initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize strategy service: {e}")
        raise

async def continuous_strategy_analysis():
    """Continuous strategy analysis loop"""
    logger.info("🔄 Starting continuous strategy analysis loop")
    
    # Define exchanges to analyze
    exchanges = ['binance', 'bybit', 'cryptocom']
    
    while True:
        try:
            all_trading_pairs = []
            
            # Get trading pairs from database for each exchange
            async with httpx.AsyncClient(timeout=60.0) as client:
                for exchange in exchanges:
                    try:
                        response = await client.get(f"{database_service_url}/api/v1/pairs/{exchange}")
                        if response.status_code == 200:
                            pairs_data = response.json()
                            pairs = pairs_data.get('pairs', [])
                            
                            # Convert to the expected format
                            for pair in pairs:
                                all_trading_pairs.append({
                                    'exchange': exchange,
                                    'pair': pair
                                })
                            
                            logger.info(f"📊 Retrieved {len(pairs)} pairs for {exchange}")
                        else:
                            logger.warning(f"⚠️ Failed to get pairs for {exchange}: {response.status_code}")
                    except Exception as e:
                        logger.warning(f"⚠️ Error getting pairs for {exchange}: {e}")
                        continue
            
            if not all_trading_pairs:
                logger.warning("⚠️ No trading pairs configured for analysis")
                await asyncio.sleep(60)  # Wait 1 minute before retrying
                continue
            
            logger.info(f"📊 Analyzing {len(all_trading_pairs)} trading pairs across {len(exchanges)} exchanges...")
            
            for pair_data in all_trading_pairs:
                try:
                    exchange = pair_data.get('exchange', 'binance')
                    pair = pair_data.get('pair', 'BTC/USDC')
                    
                    # Create analysis request.
                    # PnL-FIX v4: include 4h so vwma_hull's macro HARD VETO and
                    # heikin_ashi's 4h hierarchy level actually receive data.
                    # PnL-FIX v6: do NOT pass an explicit `strategies` whitelist
                    # here. Previously this hard-coded ['vwma_hull','heikin_ashi']
                    # silently flipped `enabled=False` on every other strategy
                    # via analyze_pair_internal's per-call toggle, so
                    # multi_timeframe_confluence (PRIMARY for sideways /
                    # low_volatility) and engulfing_multi_tf (PRIMARY for
                    # reversal_zone) never participated in consensus —
                    # explaining the dashboard's "participating: 2" everywhere.
                    # The market regime detector already filters to the
                    # applicable strategies per pair; let it decide.
                    analysis_request = AnalysisRequest(
                        strategies=None,
                        timeframes=['1h', '4h', '15m'],
                        include_indicators=True
                    )
                    
                    # Run analysis
                    result = await analyze_pair_internal(exchange, pair, analysis_request)
                    
                    if result and result.get('consensus'):
                        consensus = result['consensus']
                        signal = consensus.get('signal', 'HOLD')
                        confidence = consensus.get('confidence', 0.0)
                        
                        logger.info(f"🎯 [{exchange.upper()}] {pair}: {signal} | Confidence: {confidence:.2f} | Regime: {consensus.get('regime', 'unknown')}")
                    
                except Exception as e:
                    logger.error(f"❌ Error analyzing {pair} on {exchange}: {e}")
                    _record_pair_strategy_snapshot(
                        exchange,
                        pair,
                        None,
                        analysis_status="error",
                        detail=str(e)[:2000],
                    )
                    continue
            
            # Wait before next analysis cycle
            await asyncio.sleep(30)  # Analyze every 30 seconds
            
        except Exception as e:
            logger.error(f"❌ Error in continuous strategy analysis: {e}")
            await asyncio.sleep(60)  # Wait longer on error

# API Endpoints
@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(strategy_registry), media_type=CONTENT_TYPE_LATEST)

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    enabled_count = sum(1 for s in strategies.values() if s['enabled'])
    return HealthResponse(
        status="healthy" if enabled_count > 0 else "degraded",
        timestamp=datetime.utcnow(),
        version="1.0.0",
        strategies_loaded=enabled_count,
        total_strategies=len(strategies)
    )

@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    return {"status": "ready"}

@app.get("/live")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "alive"}

# Strategy Management Endpoints
@app.get("/api/v1/strategies")
async def get_strategies():
    """Get all strategies"""
    strategy_list = []
    for name, data in strategies.items():
        strategy_list.append({
            'name': name,
            'enabled': data['enabled'],
            'last_analysis': data['last_analysis'],
            'config': data['config']
        })
    
    return {
        "strategies": strategy_list,
        "total": len(strategy_list),
        "enabled": sum(1 for s in strategy_list if s['enabled'])
    }

@app.get("/api/v1/strategies/{strategy_name}")
async def get_strategy(strategy_name: str):
    """Get specific strategy details"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    strategy_data = strategies[strategy_name]
    return {
        'name': strategy_name,
        'enabled': strategy_data['enabled'],
        'last_analysis': strategy_data['last_analysis'],
        'config': strategy_data['config']
    }

@app.post("/api/v1/strategies/{strategy_name}/enable")
async def enable_strategy(strategy_name: str):
    """Enable a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    strategies[strategy_name]['enabled'] = True
    logger.info(f"Enabled strategy: {strategy_name}")
    return {"message": f"Strategy {strategy_name} enabled"}

@app.post("/api/v1/strategies/{strategy_name}/disable")
async def disable_strategy(strategy_name: str):
    """Disable a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    strategies[strategy_name]['enabled'] = False
    logger.info(f"Disabled strategy: {strategy_name}")
    return {"message": f"Strategy {strategy_name} disabled"}

# Signal Generation Endpoints
async def analyze_pair_internal(exchange: str, pair: str, request: AnalysisRequest):
    """Internal function to analyze a trading pair"""
    try:
        # Convert pair format: Add slash if missing (ACXUSD -> ACX/USD)
        formatted_pair = pair
        if '/' not in pair:
            # Handle common patterns like ACXUSD -> ACX/USD, BTCUSD -> BTC/USD
            if len(pair) >= 6 and pair.endswith('USD'):
                base = pair[:-3]
                formatted_pair = f"{base}/USD"
            elif len(pair) >= 7 and pair.endswith('USDC'):
                base = pair[:-4] 
                formatted_pair = f"{base}/USDC"
            else:
                raise ValueError(f"Unsupported pair format: {pair}")

        # PnL-FIX v6: per-call allowlist instead of mutating
        # strategies[name]['enabled']. The previous pattern was racy under
        # concurrent calls and was the root cause of multi_timeframe_confluence
        # being silently disabled across the bot.
        analysis_results = await strategy_manager.analyze_pair(
            exchange,
            formatted_pair,
            request.timeframes,
            strategy_allowlist=request.strategies,
        )

        if not analysis_results:
            raise ValueError("Analysis failed - no results")

        return analysis_results

    except Exception as e:
        logger.error(f"Error in analyze_pair_internal for {exchange}/{pair}: {e}")
        raise

@app.post("/api/v1/analysis/{exchange}/{pair}")
async def analyze_pair(exchange: str, pair: str, request: AnalysisRequest):
    """Analyze a trading pair"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    
    try:
        # Convert pair format: Add slash if missing (ACXUSD -> ACX/USD)
        formatted_pair = pair
        if '/' not in pair:
            # Handle common patterns like ACXUSD -> ACX/USD, BTCUSD -> BTC/USD
            if len(pair) >= 6 and pair.endswith('USD'):
                base = pair[:-3]
                formatted_pair = f"{base}/USD"
            elif len(pair) >= 7 and pair.endswith('USDC'):
                base = pair[:-4] 
                formatted_pair = f"{base}/USDC"
            else:
                # For other formats, return 404
                raise HTTPException(status_code=404, detail=f"Unsupported pair format: {pair}")
        
        # PnL-FIX v6: per-call allowlist (no global mutation).
        analysis_results = await strategy_manager.analyze_pair(
            exchange,
            formatted_pair,
            request.timeframes,
            strategy_allowlist=request.strategies,
        )

        if not analysis_results:
            raise HTTPException(status_code=404, detail="Analysis failed - no results")

        return analysis_results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing {pair} on {exchange}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/signals/{exchange}/{pair}")
async def get_signals(exchange: str, pair: str):
    """Get cached signals for a pair"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    
    # Convert pair format: Add slash if missing (ACXUSD -> ACX/USD)
    formatted_pair = pair
    if '/' not in pair:
        # Handle common patterns like ACXUSD -> ACX/USD, BTCUSD -> BTC/USD
        if len(pair) >= 6 and pair.endswith('USD'):
            base = pair[:-3]
            formatted_pair = f"{base}/USD"
        elif len(pair) >= 7 and pair.endswith('USDC'):
            base = pair[:-4] 
            formatted_pair = f"{base}/USDC"
        else:
            # For other formats, return 404
            raise HTTPException(status_code=404, detail=f"Unsupported pair format: {pair}")
    
    # Check cache with formatted pair
    cache_key = f"{exchange}_{formatted_pair}_{int(datetime.utcnow().timestamp() / 300)}"
    if cache_key in signal_cache:
        return signal_cache[cache_key]
    
    # If not in cache, perform analysis with formatted pair
    analysis_results = await strategy_manager.analyze_pair(exchange, formatted_pair)
    
    if not analysis_results:
        raise HTTPException(status_code=404, detail="No signals available")
    
    return analysis_results

@app.get("/api/v1/signals/consensus/{exchange}/{pair}")
async def get_consensus_signals(exchange: str, pair: str):
    """Get consensus signals for a pair"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    
    # Convert pair format: Add slash if missing (ACXUSD -> ACX/USD)
    formatted_pair = pair
    if '/' not in pair:
        # Handle common patterns like ACXUSD -> ACX/USD, BTCUSD -> BTC/USD
        if len(pair) >= 6 and pair.endswith('USD'):
            base = pair[:-3]
            formatted_pair = f"{base}/USD"
        elif len(pair) >= 7 and pair.endswith('USDC'):
            base = pair[:-4] 
            formatted_pair = f"{base}/USDC"
        else:
            # For other formats, return 404
            raise HTTPException(status_code=404, detail=f"Unsupported pair format: {pair}")
    
    # Get full analysis with formatted pair
    analysis_results = await strategy_manager.analyze_pair(exchange, formatted_pair)
    
    if not analysis_results or 'consensus' not in analysis_results:
        raise HTTPException(status_code=404, detail="No consensus available")
    
    # Convert to StrategyConsensus model
    consensus_data = analysis_results['consensus']
    signals = []
    
    for strategy_name, strategy_result in analysis_results['strategies'].items():
        if 'error' not in strategy_result:
            signals.append(StrategySignal(
                strategy_name=strategy_name,
                pair=pair,
                exchange=exchange,
                signal=strategy_result['signal'],
                confidence=strategy_result['confidence'],
                strength=strategy_result['strength'],
                timestamp=datetime.fromisoformat(strategy_result['timestamp']),
                market_regime=strategy_result.get('market_regime', 'unknown')
            ))
    
    consensus = StrategyConsensus(
        pair=pair,
        exchange=exchange,
        consensus_signal=consensus_data['signal'],
        agreement_percentage=consensus_data['agreement'],
        participating_strategies=consensus_data['participating_strategies'],
        signals=signals,
        timestamp=datetime.utcnow()
    )
    
    return consensus

# Strategy Performance Endpoints
@app.get("/api/v1/performance/{strategy_name}")
async def get_strategy_performance(strategy_name: str, period: str = "30d"):
    """Get performance metrics for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    try:
        # Get performance data from database
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{database_service_url}/api/v1/performance/strategy/{strategy_name}",
                params={'period': period}
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                # Return mock data if not available
                return StrategyPerformance(
                    strategy_name=strategy_name,
                    total_trades=0,
                    winning_trades=0,
                    win_rate=0.0,
                    total_pnl=0.0,
                    sharpe_ratio=0.0,
                    max_drawdown=0.0,
                    period=period
                )
                
    except Exception as e:
        logger.error(f"Error getting performance for {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/performance/{strategy_name}/update")
async def update_strategy_performance(strategy_name: str, performance_data: Dict[str, Any]):
    """Update performance metrics for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{database_service_url}/api/v1/performance/strategy/{strategy_name}/update",
                json=performance_data
            )
            response.raise_for_status()
            
        return {"message": f"Performance updated for {strategy_name}"}
        
    except Exception as e:
        logger.error(f"Error updating performance for {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/performance/compare")
async def compare_strategies(period: str = "30d"):
    """Compare performance across all strategies"""
    try:
        comparison = {}
        
        for strategy_name in strategies:
            try:
                performance = await get_strategy_performance(strategy_name, period)
                comparison[strategy_name] = performance
            except Exception as e:
                logger.error(f"Error getting performance for {strategy_name}: {e}")
                comparison[strategy_name] = {"error": str(e)}
        
        return {
            "period": period,
            "comparison": comparison,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error comparing strategies: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Strategy Configuration Endpoints
@app.get("/api/v1/strategies/{strategy_name}/config")
async def get_strategy_config(strategy_name: str):
    """Get configuration for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    return strategies[strategy_name]['config']

@app.put("/api/v1/strategies/{strategy_name}/config")
async def update_strategy_config(strategy_name: str, config: Dict[str, Any]):
    """Update configuration for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    try:
        # Update local config
        strategies[strategy_name]['config'].update(config)
        
        # Update strategy instance if possible
        strategy_instance = strategies[strategy_name]['instance']
        if hasattr(strategy_instance, 'update_config'):
            await strategy_instance.update_config(config)
        
        logger.info(f"Updated config for strategy: {strategy_name}")
        return {"message": f"Configuration updated for {strategy_name}"}
        
    except Exception as e:
        logger.error(f"Error updating config for {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/strategies/{strategy_name}/backtest")
async def backtest_strategy(strategy_name: str, backtest_params: Dict[str, Any]):
    """Run backtest for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    try:
        # This would implement backtesting logic
        # For now, return a mock result
        backtest_result = {
            "strategy": strategy_name,
            "period": backtest_params.get("period", "30d"),
            "total_trades": 100,
            "winning_trades": 65,
            "win_rate": 0.65,
            "total_pnl": 1250.0,
            "sharpe_ratio": 1.2,
            "max_drawdown": -150.0,
            "backtest_date": datetime.utcnow().isoformat()
        }
        
        return backtest_result
        
    except Exception as e:
        logger.error(f"Error backtesting {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Cache Management
@app.delete("/api/v1/cache/clear")
async def clear_cache():
    """Clear signal cache"""
    global signal_cache, pair_last_snapshots
    cache_size = len(signal_cache)
    signal_cache.clear()
    pair_last_snapshots.clear()
    logger.info(f"Cleared signal cache ({cache_size} entries) and pair snapshots")
    return {"message": f"Cache cleared ({cache_size} entries)"}

@app.get("/api/v1/cache/stats")
async def get_cache_stats():
    """Get cache statistics"""
    return {
        "cache_size": len(signal_cache),
        "cache_keys": list(signal_cache.keys()),
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/v1/pair-snapshots")
async def get_pair_snapshots():
    """Latest per-pair snapshot + cumulative per-strategy run counts (process lifetime)."""
    return {
        "snapshots": pair_last_snapshots,
        "strategy_cumulative_runs": dict(
            sorted(strategy_cumulative_runs.items(), key=lambda x: x[0])
        ),
    }


# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize strategies on startup"""
    logger.info("Starting Strategy Service...")
    await initialize_strategies()
    
    # Start continuous strategy analysis in background
    asyncio.create_task(continuous_strategy_analysis())
    
    logger.info("Strategy Service started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Strategy Service...")
    # Clear any cached data
    signal_cache.clear()
    pair_last_snapshots.clear()
    strategy_cumulative_runs.clear()
    strategies.clear()
    strategy_instances.clear()
    logger.info("Strategy Service shutdown complete")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8004,
        reload=False,
        log_level="info"
    ) 