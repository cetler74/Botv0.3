"""
Hyperliquid perpetual strategy manager (isolated from spot StrategyManager).
"""

from __future__ import annotations

import importlib
import asyncio
import logging
import copy
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Tuple

import httpx
import numpy as np
import pandas as pd

from strategy.hyperliquid.consensus import (
    calculate_hyperliquid_consensus,
    normalize_perp_entry_signal,
    select_recommended_strategy,
)
from strategy.hyperliquid.indicators import closed_bar_snapshot, ohlcv_dict_to_df
from strategy.hyperliquid.mapping import HYPERLIQUID_STRATEGY_MAPPING
from strategy.market_regime_detector import MarketRegime, MarketRegimeDetector

logger = logging.getLogger(__name__)

HL_DEFAULT_TIMEFRAMES = ["1h", "15m"]
EXECUTION_TIMEFRAMES = frozenset([*HL_DEFAULT_TIMEFRAMES, "1m"])

DEPRECATED_STRATEGIES = {
    "heikin_ashi": (
        "heikin_ashi is deprecated (2026-05-26): 0% WR on 28 closed shorts "
        "(-$29.24 lifetime perp), 0/4 today (-$4.91). "
        "Disable in config or review design spec before re-enabling."
    ),
    "engulfing_multi_tf": (
        "engulfing_multi_tf is deprecated (2026-05-27): 0% WR on 4 closed "
        "paper trades (-$6.44 lifetime perp). Same evidence pattern as "
        "heikin_ashi. Disable in config or review profit-plan spec before "
        "re-enabling."
    ),
    "breakout_retest_long": (
        "breakout_retest_long is deprecated (2026-05-29): top lifetime loser "
        "-$52.79 on 37 closed paper trades (43.2% WR, PF 0.26). Higher "
        "confidence gating (0.80) did not fix the top-chasing entries in "
        "trending_up. Disable in config or review profit-plan spec before "
        "re-enabling."
    ),
}


class HyperliquidExchangeAdapter:
    """Fetch HL OHLCV via exchange-service."""

    def __init__(self, exchange_service_url: str):
        self.exchange_service_url = exchange_service_url.rstrip("/")

    async def get_ohlcv(self, exchange_name: str, symbol: str, timeframe: str, limit: int = 100):
        raw = str(symbol or "").replace("/", "")
        if ":" in raw:
            dex, base = raw.split(":", 1)
            coin = f"{dex.lower()}:{base.upper()}"
        else:
            coin = raw.upper()
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
            # Forming-candle drop is handled in rsi_stoch engine via prepare_closed_ohlcv.
            return ohlcv_dict_to_df(data)


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
            deprecation_msg = DEPRECATED_STRATEGIES.get(strategy_name)
            if deprecation_msg:
                logger.warning("[HLStrategy] Skipping deprecated strategy %s: %s", strategy_name, deprecation_msg)
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
                    params["target_timeframes"] = ["1h", "15m"]
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

        def add(tf: Any, *, allow_1m: bool = False) -> None:
            value = str(tf or "").strip()
            if value == "1m" and not allow_1m:
                return
            if value in EXECUTION_TIMEFRAMES and value not in ordered:
                ordered.append(value)

        allowlist = set(strategy_allowlist) if strategy_allowlist else None
        explicit_1m_allowed = allowlist is not None and "heikin_ashi_1m_scalper" in allowlist
        for tf in (timeframes or HL_DEFAULT_TIMEFRAMES):
            add(tf, allow_1m=explicit_1m_allowed)

        for strategy_name, data in self.strategies.items():
            if allowlist is not None and strategy_name not in allowlist:
                continue
            params = ((data.get("config") or {}).get("parameters") or {})
            allow_1m = strategy_name == "heikin_ashi_1m_scalper"
            for tf in params.get("target_timeframes") or []:
                add(tf, allow_1m=allow_1m)
            add(params.get("entry_timeframe"), allow_1m=allow_1m)
            add(params.get("structure_timeframe"), allow_1m=allow_1m)
            add(params.get("bias_timeframe"), allow_1m=allow_1m)
            add(params.get("confirmation_timeframe"), allow_1m=allow_1m)
            add(params.get("precision_timeframe"), allow_1m=allow_1m)
            add(params.get("execution_timeframe"), allow_1m=allow_1m)
            for tf in params.get("context_timeframes") or []:
                add(tf, allow_1m=allow_1m)

        return ordered

    async def _get_market_data(self, coin: str, timeframes: List[str]) -> Dict[str, pd.DataFrame]:
        adapter = HyperliquidExchangeAdapter(self.exchange_service_url)
        market_data: Dict[str, pd.DataFrame] = {}
        tf_limits = {"1h": 240, "15m": 240, "1m": 180}
        for tf in timeframes:
            if tf not in EXECUTION_TIMEFRAMES:
                continue
            limit = tf_limits.get(tf, 120)
            try:
                df = await adapter.get_ohlcv("hyperliquid", coin, tf, limit=limit)
                if df is not None and len(df) >= 30:
                    market_data[tf] = df
            except Exception as exc:
                logger.debug("[HLStrategy] OHLCV %s %s failed: %s", coin, tf, exc)
        return market_data

    @staticmethod
    def _closed_market_data(
        market_data: Dict[str, pd.DataFrame],
    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Dict[str, Any]]]:
        """Normalize every execution frame to complete, finite candles."""
        closed: Dict[str, pd.DataFrame] = {}
        contract: Dict[str, Dict[str, Any]] = {}
        for timeframe, frame in market_data.items():
            if timeframe not in EXECUTION_TIMEFRAMES:
                continue
            try:
                snapshot, metadata = closed_bar_snapshot(frame, timeframe)
            except ValueError as exc:
                logger.debug("[HLStrategy] rejected %s OHLCV: %s", timeframe, exc)
                continue
            if len(snapshot) >= 30:
                closed[timeframe] = snapshot
                contract[timeframe] = metadata
        return closed, contract

    @staticmethod
    def _ta_evidence(
        indicators: Dict[str, Any],
        contract: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Expose bounded, finite TA values actually available to a signal."""
        inputs = {}
        for key, value in indicators.items():
            if len(inputs) >= 16 or isinstance(value, (dict, list, tuple)):
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(numeric):
                inputs[str(key)] = numeric
        return {
            "bar_closed": True,
            "timeframes": [
                timeframe
                for timeframe in [*HL_DEFAULT_TIMEFRAMES, "1m"]
                if timeframe in contract
            ],
            "bar_times": {
                timeframe: metadata["bar_time"]
                for timeframe, metadata in contract.items()
            },
            "inputs": inputs,
        }

    async def analyze_coin(
        self,
        coin: str,
        timeframes: Optional[List[str]] = None,
        *,
        strategy_allowlist: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        raw_symbol = str(coin or "")
        if ":" in raw_symbol:
            dex, base = raw_symbol.split(":", 1)
            symbol = f"{dex.lower()}:{base.upper()}"
        else:
            symbol = raw_symbol.upper()
        tfs = self._resolve_timeframes(timeframes, strategy_allowlist)
        market_data = await self._get_market_data(symbol, tfs)
        market_data, ta_contract = self._closed_market_data(market_data)
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
            "ta_contract": {
                "bar_closed": True,
                "timeframes": [
                    timeframe
                    for timeframe in [*HL_DEFAULT_TIMEFRAMES, "1m"]
                    if timeframe in ta_contract
                ],
                "frames": ta_contract,
            },
            "strategies": {},
            "consensus": {},
        }

        strategy_sem = asyncio.Semaphore(
            max(1, min(int(os.getenv("HL_STRATEGY_EVAL_CONCURRENCY", "8")), 12))
        )

        async def _evaluate_strategy(strategy_name: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            if allowlist is not None and strategy_name not in allowlist:
                return strategy_name, None
            if strategy_name not in self.strategies:
                return strategy_name, None
            async with strategy_sem:
                try:
                    instance = self._build_strategy_instance(strategy_name)
                    if instance is None:
                        return strategy_name, None
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
                    if signal == "short" and not bool(getattr(instance, "allow_short", True)):
                        signal, confidence, strength = "hold", 0.0, 0.0
                    elif signal == "long" and not bool(getattr(instance, "allow_long", True)):
                        signal, confidence, strength = "hold", 0.0, 0.0
                    if isinstance(confidence, float) and (np.isnan(confidence) or np.isinf(confidence)):
                        confidence = 0.0
                    if isinstance(strength, float) and (np.isnan(strength) or np.isinf(strength)):
                        strength = 0.0
                    indicators = dict(getattr(instance.state, "indicators", {}) or {})
                    row = {
                        "signal": signal,
                        "confidence": float(confidence),
                        "strength": float(strength),
                        "market_regime": market_regime.value,
                        "timestamp": datetime.utcnow().isoformat(),
                        "state": {
                            "market_regime": market_regime.value,
                            "indicators": indicators,
                            "entry_reason": getattr(instance.state, "entry_reason", ""),
                        },
                        "ta_evidence": self._ta_evidence(indicators, ta_contract),
                    }
                    if strategy_name == "supply_demand_3step":
                        from strategy.playbooks.supply_demand_audit import persist_supply_demand_audit

                        try:
                            db_url = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002")
                            await persist_supply_demand_audit(
                                db_url,
                                "hyperliquid",
                                symbol,
                                row,
                                source="strategy-service-hl",
                            )
                        except Exception as exc:
                            logger.debug("[HLStrategy] supply/demand audit persist failed: %s", exc)
                    if strategy_name == "dual_sma_daytrade":
                        from strategy.playbooks.dual_sma_audit import persist_dual_sma_audit

                        try:
                            db_url = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002")
                            await persist_dual_sma_audit(
                                db_url,
                                "hyperliquid",
                                symbol,
                                row,
                                source="strategy-service-hl",
                            )
                        except Exception as exc:
                            logger.debug("[HLStrategy] dual-SMA audit persist failed: %s", exc)
                    if strategy_name == "arc_daytrade":
                        from strategy.playbooks.arc_audit import persist_arc_audit

                        try:
                            db_url = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002")
                            await persist_arc_audit(
                                db_url,
                                "hyperliquid",
                                symbol,
                                row,
                                source="strategy-service-hl",
                            )
                        except Exception as exc:
                            logger.debug("[HLStrategy] ARC audit persist failed: %s", exc)
                    if strategy_name == "ema50_breakout_pullback":
                        from strategy.playbooks.ema50_breakout_pullback_audit import (
                            persist_ema50_breakout_pullback_audit,
                        )

                        try:
                            db_url = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002")
                            await persist_ema50_breakout_pullback_audit(
                                db_url,
                                "hyperliquid",
                                symbol,
                                row,
                                source="strategy-service-hl",
                            )
                        except Exception as exc:
                            logger.debug("[HLStrategy] EMA50 BP audit persist failed: %s", exc)
                    if strategy_name == "orb_5m_scalp":
                        from strategy.playbooks.orb_5m_scalp_audit import persist_orb_5m_scalp_audit

                        try:
                            db_url = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002")
                            await persist_orb_5m_scalp_audit(
                                db_url,
                                "hyperliquid",
                                symbol,
                                row,
                                source="strategy-service-hl",
                            )
                        except Exception as exc:
                            logger.debug("[HLStrategy] ORB 5m scalp audit persist failed: %s", exc)
                    return strategy_name, row
                except Exception as exc:
                    logger.error("[HLStrategy] %s on %s: %s", strategy_name, symbol, exc)
                    return strategy_name, {"error": str(exc)}

        evaluated = await asyncio.gather(
            *(_evaluate_strategy(strategy_name) for strategy_name in self.strategies)
        )
        for strategy_name, row in evaluated:
            if row is not None:
                results["strategies"][strategy_name] = row

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
