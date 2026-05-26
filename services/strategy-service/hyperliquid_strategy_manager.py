"""
Hyperliquid perpetual strategy manager (isolated from spot StrategyManager).
"""

from __future__ import annotations

import importlib
import logging
import copy
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import numpy as np
import pandas as pd

from strategy.hyperliquid.consensus import (
    calculate_hyperliquid_consensus,
    normalize_perp_entry_signal,
    select_recommended_strategy,
)
from strategy.hyperliquid.indicators import ohlcv_dict_to_df
from strategy.hyperliquid.mapping import HYPERLIQUID_STRATEGY_MAPPING
from strategy.market_regime_detector import MarketRegime, MarketRegimeDetector

logger = logging.getLogger(__name__)

HL_DEFAULT_TIMEFRAMES = ["1h", "4h", "15m", "5m"]


class HyperliquidExchangeAdapter:
    """Fetch HL OHLCV via exchange-service."""

    def __init__(self, exchange_service_url: str):
        self.exchange_service_url = exchange_service_url.rstrip("/")

    async def get_ohlcv(self, exchange_name: str, symbol: str, timeframe: str, limit: int = 100):
        coin = str(symbol).upper().replace("/", "")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.exchange_service_url}/api/v1/market/ohlcv/hyperliquid/{coin}",
                params={"timeframe": timeframe, "limit": limit},
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not data:
                return None
            df = ohlcv_dict_to_df(data)
            if df is not None and len(df) > 1:
                df = df.iloc[:-1]
            return df


class HyperliquidStrategyManager:
    def __init__(
        self,
        strategies_config: Dict[str, Any],
        exchange_service_url: str,
        consensus_cfg: Optional[Dict[str, Any]] = None,
    ):
        self.config = strategies_config or {}
        self.exchange_service_url = exchange_service_url
        self.consensus_cfg = consensus_cfg or {}
        self.strategies: Dict[str, Dict[str, Any]] = {}
        self._regime_state: Dict[str, Dict[str, Any]] = {}
        self.regime_detector = MarketRegimeDetector()
        regime_cfg = self.config.get("regime_stability") or {}
        if isinstance(regime_cfg, dict) and regime_cfg.get("enabled", True):
            self.regime_detector.configure(regime_cfg)
        self._initialize_strategies()

    def _initialize_strategies(self) -> None:
        self.strategies = {}
        for strategy_name, strategy_config in self.config.items():
            if strategy_name == "regime_stability":
                continue
            if not isinstance(strategy_config, dict):
                continue
            if not strategy_config.get("enabled", False):
                continue
            mapping = HYPERLIQUID_STRATEGY_MAPPING.get(strategy_name)
            if not mapping:
                logger.warning("[HLStrategy] No mapping for %s", strategy_name)
                continue
            module_path, class_name = mapping
            try:
                module = importlib.import_module(module_path)
                strategy_class = getattr(module, class_name)
                cfg_block = dict(strategy_config)
                params = dict(cfg_block.get("parameters") or {})
                for key in ("target_timeframes", "timeframe_weights"):
                    if key in cfg_block and key not in params:
                        params[key] = cfg_block[key]
                if strategy_name == "engulfing_multi_tf" and "target_timeframes" not in params:
                    params["target_timeframes"] = ["4h", "1h", "15m"]
                cfg_block["parameters"] = params
                self.strategies[strategy_name] = {
                    "class": strategy_class,
                    "config": cfg_block,
                    "enabled": True,
                }
                logger.info("[HLStrategy] Loaded %s -> %s", strategy_name, class_name)
            except Exception as exc:
                logger.error("[HLStrategy] Failed to load %s: %s", strategy_name, exc)

    def _build_strategy_instance(self, strategy_name: str):
        """Create a fresh strategy object so per-coin analysis cannot share mutable state."""
        data = self.strategies.get(strategy_name)
        if not data:
            return None
        strategy_class = data["class"]
        cfg_block = copy.deepcopy(data["config"])
        return strategy_class(
            config=cfg_block,
            exchange=None,
            database=None,
            redis_client=None,
        )

    def _resolve_timeframes(
        self,
        timeframes: Optional[List[str]],
        strategy_allowlist: Optional[List[str]],
    ) -> List[str]:
        """Fetch requested/default frames plus each enabled strategy's required frames."""
        ordered: List[str] = []

        def add(tf: Any) -> None:
            value = str(tf or "").strip()
            if value and value not in ordered:
                ordered.append(value)

        for tf in (timeframes or HL_DEFAULT_TIMEFRAMES):
            add(tf)

        allowlist = set(strategy_allowlist) if strategy_allowlist else None
        for strategy_name, data in self.strategies.items():
            if allowlist is not None and strategy_name not in allowlist:
                continue
            params = ((data.get("config") or {}).get("parameters") or {})
            for tf in params.get("target_timeframes") or []:
                add(tf)
            add(params.get("entry_timeframe"))
            add(params.get("execution_timeframe"))
            for tf in params.get("context_timeframes") or []:
                add(tf)

        return ordered

    async def _get_market_data(self, coin: str, timeframes: List[str]) -> Dict[str, pd.DataFrame]:
        adapter = HyperliquidExchangeAdapter(self.exchange_service_url)
        market_data: Dict[str, pd.DataFrame] = {}
        tf_limits = {"1d": 260, "4h": 150, "1h": 240, "15m": 240, "5m": 260, "1m": 240}
        for tf in timeframes:
            limit = tf_limits.get(tf, 120)
            try:
                df = await adapter.get_ohlcv("hyperliquid", coin, tf, limit=limit)
                if df is not None and len(df) >= 30:
                    market_data[tf] = df
            except Exception as exc:
                logger.debug("[HLStrategy] OHLCV %s %s failed: %s", coin, tf, exc)
        return market_data

    async def analyze_coin(
        self,
        coin: str,
        timeframes: Optional[List[str]] = None,
        *,
        strategy_allowlist: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        symbol = str(coin or "").upper()
        tfs = self._resolve_timeframes(timeframes, strategy_allowlist)
        market_data = await self._get_market_data(symbol, tfs)
        if not market_data:
            return {}

        primary_tf = "1h" if "1h" in market_data else tfs[0]
        primary_ohlcv = market_data.get(primary_tf)
        if primary_ohlcv is None or len(primary_ohlcv) < 50:
            market_regime = MarketRegime.LOW_VOLATILITY
            regime_analysis: Dict[str, Any] = {"reason": "insufficient_data"}
        else:
            market_regime, regime_analysis = self.regime_detector.detect_regime(
                primary_ohlcv, symbol
            )

        allowlist = set(strategy_allowlist) if strategy_allowlist else None
        results: Dict[str, Any] = {
            "coin": symbol,
            "venue": "hyperliquid",
            "timestamp": datetime.utcnow().isoformat(),
            "market_regime": market_regime.value,
            "regime_analysis": regime_analysis,
            "strategies": {},
            "consensus": {},
        }

        for strategy_name, data in self.strategies.items():
            if allowlist is not None and strategy_name not in allowlist:
                continue
            try:
                instance = self._build_strategy_instance(strategy_name)
                if instance is None:
                    continue
                await instance.initialize(symbol)
                if primary_tf in market_data:
                    await instance.update(market_data[primary_tf])
                instance.state.market_regime = market_regime.value
                adapter = HyperliquidExchangeAdapter(self.exchange_service_url)
                signal, confidence, strength = await instance.generate_signal(
                    market_data,
                    pair=symbol,
                    exchange_adapter=adapter,
                )
                signal = normalize_perp_entry_signal(signal)
                if isinstance(confidence, float) and (np.isnan(confidence) or np.isinf(confidence)):
                    confidence = 0.0
                if isinstance(strength, float) and (np.isnan(strength) or np.isinf(strength)):
                    strength = 0.0
                results["strategies"][strategy_name] = {
                    "signal": signal,
                    "confidence": float(confidence),
                    "strength": float(strength),
                    "market_regime": market_regime.value,
                    "timestamp": datetime.utcnow().isoformat(),
                    "state": {
                        "market_regime": market_regime.value,
                        "indicators": dict(getattr(instance.state, "indicators", {}) or {}),
                        "entry_reason": getattr(instance.state, "entry_reason", ""),
                    },
                }
            except Exception as exc:
                logger.error("[HLStrategy] %s on %s: %s", strategy_name, symbol, exc)
                results["strategies"][strategy_name] = {"error": str(exc)}

        min_agreement = float(self.consensus_cfg.get("min_agreement", 50) or 50)
        results["consensus"] = calculate_hyperliquid_consensus(
            results["strategies"],
            min_agreement=min_agreement,
        )
        results["recommended"] = select_recommended_strategy(
            results["strategies"],
            results["consensus"].get("signal", "hold"),
        )
        return results

    async def exit_advice_for_trade(
        self,
        trade: Dict[str, Any],
        market_data: Dict[str, pd.DataFrame],
        current_price: float,
    ) -> Dict[str, Any]:
        """Per-strategy should_exit for an open paper perp."""
        source = str(trade.get("source_strategy") or "")
        side = str(trade.get("position_side") or "long").lower()
        entry = float(trade.get("entry_price") or 0.0)
        advice: Dict[str, Any] = {"should_exit": False, "reason": None, "strategy": source}
        if source not in self.strategies:
            return advice
        try:
            instance = self._build_strategy_instance(source)
            if instance is None:
                return advice
            await instance.initialize(str(trade.get("coin") or ""))
            should, reason = await instance.should_exit(side, entry, current_price, market_data=market_data)
            advice["should_exit"] = bool(should)
            advice["reason"] = reason or None
        except Exception as exc:
            advice["error"] = str(exc)
        return advice
