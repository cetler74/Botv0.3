#!/usr/bin/env python3
"""Audit Hyperliquid paper-perp entry guardrails from live local services.

This script answers the goal-specific question: are profitable shadow cohorts
being scanned, are they currently live-matching, and are known losing lanes
blocked by config before execution?
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "services" / "orchestrator-service"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from hyperliquid_perps import (  # noqa: E402
    hyperliquid_min_edge_gate,
    hyperliquid_regime_direction_gate,
    hyperliquid_shadow_promotion_requirement,
    pair_to_hyperliquid_coin,
    position_sides_from_signal,
    select_mirrored_signal,
)


def _get_json(url: str, timeout: float = 20.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _parse_dt(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fetch_trades(
    database_url: str,
    *,
    status: str,
    limit: int,
    include_excluded: bool = True,
    shadow_only: bool = False,
) -> List[Dict[str, Any]]:
    params = {
        "status": status,
        "limit": max(1, min(limit, 1000)),
        "include_accounting_excluded": str(include_excluded).lower(),
    }
    if shadow_only:
        params["shadow_only"] = "true"
    url = database_url.rstrip() + "/api/v1/perps/paper-trades?" + urllib.parse.urlencode(params)
    payload = _get_json(url)
    return list(payload.get("trades") or [])


def _load_hl_config(config_url: str) -> Dict[str, Any]:
    payload = _get_json(config_url.rstrip("/") + "/api/v1/config/trading")
    trading = payload.get("trading") or payload
    return dict((trading.get("hyperliquid_perps") or {}))


def _cohort_key(row: Mapping[str, Any]) -> Tuple[str, str, str, str]:
    metadata = row.get("metadata") or {}
    return (
        pair_to_hyperliquid_coin(str(row.get("coin") or "")),
        str(row.get("source_strategy") or "").strip().lower(),
        str(row.get("position_side") or "").strip().lower(),
        str(metadata.get("market_regime") or "").strip().lower(),
    )


def _promoted_cohorts(
    shadow_rows: Iterable[Dict[str, Any]],
    hl_cfg: Mapping[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    promotion = hl_cfg.get("shadow_cohort_promotion") or {}
    lookback_hours = float(promotion.get("lookback_hours", 72) or 72)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    min_closed = int(promotion.get("min_closed", 5) or 5)
    min_win_rate = float(promotion.get("min_win_rate", 0.70) or 0.70)
    min_pnl = float(promotion.get("min_realized_pnl_usd", 5.0) or 5.0)
    max_candidates = int(promotion.get("max_candidates", 8) or 8)
    allowed_strategies = {str(x).strip().lower() for x in promotion.get("strategies", [])}
    allowed_sides = {str(x).strip().lower() for x in promotion.get("sides", [])}
    allowed_regimes = {str(x).strip().lower() for x in promotion.get("regimes", [])}

    cohorts: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for row in shadow_rows:
        exit_time = _parse_dt(row.get("exit_time") or row.get("updated_at"))
        if not exit_time or exit_time < cutoff:
            continue
        coin, strategy, side, regime = _cohort_key(row)
        if not coin or not strategy or side not in {"long", "short"}:
            continue
        if allowed_strategies and strategy not in allowed_strategies:
            continue
        if allowed_sides and side not in allowed_sides:
            continue
        if allowed_regimes and regime not in allowed_regimes:
            continue
        key = (coin, strategy, side, regime)
        cohort = cohorts.setdefault(
            key,
            {
                "coin": coin,
                "strategy": strategy,
                "side": side,
                "regime": regime,
                "closed": 0,
                "wins": 0,
                "realized": 0.0,
            },
        )
        pnl = float(row.get("realized_pnl") or 0.0)
        cohort["closed"] += 1
        cohort["wins"] += int(pnl > 0)
        cohort["realized"] += pnl

    eligible = []
    for cohort in cohorts.values():
        closed = int(cohort["closed"])
        win_rate = float(cohort["wins"]) / closed if closed else 0.0
        if closed >= min_closed and win_rate >= min_win_rate and cohort["realized"] >= min_pnl:
            eligible.append({**cohort, "win_rate": win_rate})
    eligible.sort(key=lambda c: (c["realized"], c["win_rate"], c["closed"]), reverse=True)

    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for cohort in eligible[:max_candidates]:
        out[pair_to_hyperliquid_coin(cohort["coin"])].append(cohort)
    return dict(out)


def _shadow_cohort_stats(
    shadow_rows: Iterable[Dict[str, Any]],
    hl_cfg: Mapping[str, Any],
) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    promotion = hl_cfg.get("shadow_cohort_promotion") or {}
    lookback_hours = float(promotion.get("lookback_hours", 72) or 72)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cohorts: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for row in shadow_rows:
        exit_time = _parse_dt(row.get("exit_time") or row.get("updated_at"))
        if not exit_time or exit_time < cutoff:
            continue
        coin, strategy, side, regime = _cohort_key(row)
        if not coin or not strategy or side not in {"long", "short"}:
            continue
        key = (coin, strategy, side, regime)
        cohort = cohorts.setdefault(
            key,
            {
                "coin": coin,
                "strategy": strategy,
                "side": side,
                "regime": regime,
                "closed": 0,
                "wins": 0,
                "realized": 0.0,
            },
        )
        pnl = float(row.get("realized_pnl") or 0.0)
        cohort["closed"] += 1
        cohort["wins"] += int(pnl > 0)
        cohort["realized"] += pnl

    for cohort in cohorts.values():
        closed = int(cohort["closed"])
        cohort["win_rate"] = float(cohort["wins"]) / closed if closed else 0.0
    return cohorts


def _scan_current_signals(
    strategy_url: str,
    hl_cfg: Mapping[str, Any],
    promoted_by_coin: Mapping[str, List[Mapping[str, Any]]],
) -> List[Dict[str, Any]]:
    coins = sorted(promoted_by_coin)
    rows: List[Dict[str, Any]] = []
    for coin in coins:
        payload = _get_json(
            strategy_url.rstrip("/") + f"/api/v1/signals/hyperliquid/{urllib.parse.quote(coin, safe='')}",
            timeout=12.0,
        )
        selected = select_mirrored_signal(payload, dict(hl_cfg))
        regime = str(payload.get("market_regime") or "").strip().lower()
        promoted = promoted_by_coin.get(coin) or []
        for cohort in promoted:
            strategy_payload = (payload.get("strategies") or {}).get(cohort["strategy"]) or {}
            current_side = position_sides_from_signal(strategy_payload.get("signal"))
            matches = (
                current_side == cohort["side"]
                and regime == cohort["regime"]
            )
            probe_signal = {
                "strategy": cohort["strategy"],
                "signal": cohort["side"],
                "confidence": float(strategy_payload.get("confidence") or 0.0),
                "strength": float(strategy_payload.get("strength") or 0.0),
                "details": strategy_payload,
            }
            rows.append(
                {
                    "coin": coin,
                    "strategy": cohort["strategy"],
                    "side": cohort["side"],
                    "regime": cohort["regime"],
                    "closed": cohort["closed"],
                    "win_rate": cohort["win_rate"],
                    "realized": cohort["realized"],
                    "current_regime": regime,
                    "current_side": current_side or "hold",
                    "matches": matches,
                    "selected_strategy": (selected or {}).get("strategy"),
                    "selected_side": position_sides_from_signal((selected or {}).get("signal")) or "none",
                    "promotion_gate": hyperliquid_shadow_promotion_requirement(
                        coin,
                        selected or probe_signal,
                        regime,
                        dict(hl_cfg),
                        promoted_by_coin,
                    ),
                    "edge_gate": hyperliquid_min_edge_gate(probe_signal, dict(hl_cfg)),
                    "regime_gate": hyperliquid_regime_direction_gate(
                        cohort["side"],
                        regime,
                        probe_signal["confidence"],
                        probe_signal["strength"],
                        dict(hl_cfg),
                        strategy=cohort["strategy"],
                    ),
                }
            )
    return rows


def _configured_coins(hl_cfg: Mapping[str, Any]) -> List[str]:
    raw = hl_cfg.get("selected_symbols") or hl_cfg.get("coins") or []
    coins = []
    for item in raw:
        coin = pair_to_hyperliquid_coin(str(item or ""))
        if coin:
            coins.append(coin)
    return sorted(set(coins))


def _selected_pair_coins(database_url: str) -> List[str]:
    try:
        payload = _get_json(database_url.rstrip("/") + "/api/v1/pairs/hyperliquid", timeout=10.0)
    except Exception:
        return []
    coins = []
    for pair in payload.get("pairs") or []:
        coin = pair_to_hyperliquid_coin(str(pair or ""))
        if coin:
            coins.append(coin)
    return sorted(set(coins))


def _scan_directional_candidates(
    database_url: str,
    strategy_url: str,
    hl_cfg: Mapping[str, Any],
    promoted_by_coin: Mapping[str, List[Mapping[str, Any]]],
    shadow_stats: Mapping[Tuple[str, str, str, str], Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    coins = sorted(
        set(_configured_coins(hl_cfg))
        | set(_selected_pair_coins(database_url))
        | set(promoted_by_coin)
    )
    promoted_keys = {
        (
            pair_to_hyperliquid_coin(cohort.get("coin")),
            str(cohort.get("strategy") or "").strip().lower(),
            str(cohort.get("side") or "").strip().lower(),
            str(cohort.get("regime") or "").strip().lower(),
        )
        for cohorts in promoted_by_coin.values()
        for cohort in cohorts
    }
    rows: List[Dict[str, Any]] = []
    for coin in coins:
        try:
            payload = _get_json(
                strategy_url.rstrip("/") + f"/api/v1/signals/hyperliquid/{urllib.parse.quote(coin, safe='')}",
                timeout=12.0,
            )
        except Exception as exc:
            rows.append({"coin": coin, "error": str(exc)})
            continue
        selected = select_mirrored_signal(payload, dict(hl_cfg))
        regime = str(payload.get("market_regime") or "").strip().lower()
        strategies = payload.get("strategies") or {}
        if not isinstance(strategies, dict):
            continue
        for strategy, strategy_payload in sorted(strategies.items()):
            if not isinstance(strategy_payload, dict):
                continue
            side = position_sides_from_signal(strategy_payload.get("signal"))
            if side not in {"long", "short"}:
                continue
            strategy_key = str(strategy or "").strip().lower()
            key = (coin, strategy_key, side, regime)
            stats = dict(shadow_stats.get(key) or {})
            signal = {
                "strategy": strategy_key,
                "signal": side,
                "confidence": float(strategy_payload.get("confidence") or 0.0),
                "strength": float(strategy_payload.get("strength") or 0.0),
                "details": strategy_payload,
            }
            rows.append(
                {
                    "coin": coin,
                    "strategy": strategy_key,
                    "side": side,
                    "regime": regime,
                    "confidence": signal["confidence"],
                    "strength": signal["strength"],
                    "closed": int(stats.get("closed") or 0),
                    "win_rate": float(stats.get("win_rate") or 0.0),
                    "realized": float(stats.get("realized") or 0.0),
                    "promoted": key in promoted_keys,
                    "selected": (
                        str((selected or {}).get("strategy") or "").strip().lower()
                        == strategy_key
                        and position_sides_from_signal((selected or {}).get("signal")) == side
                    ),
                    "promotion_gate": hyperliquid_shadow_promotion_requirement(
                        coin,
                        signal,
                        regime,
                        dict(hl_cfg),
                        promoted_by_coin,
                    ),
                    "edge_gate": hyperliquid_min_edge_gate(signal, dict(hl_cfg)),
                    "regime_gate": hyperliquid_regime_direction_gate(
                        side,
                        regime,
                        signal["confidence"],
                        signal["strength"],
                        dict(hl_cfg),
                        strategy=strategy_key,
                    ),
                }
            )
    rows.sort(
        key=lambda row: (
            bool(row.get("selected")),
            bool(row.get("promoted")),
            float(row.get("realized") or 0.0),
            int(row.get("closed") or 0),
        ),
        reverse=True,
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="http://127.0.0.1:8002")
    parser.add_argument("--config-url", default="http://127.0.0.1:8001")
    parser.add_argument("--strategy-url", default="http://127.0.0.1:8004")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    hl_cfg = _load_hl_config(args.config_url)
    shadow_rows = _fetch_trades(
        args.database_url,
        status="CLOSED",
        limit=args.limit,
        include_excluded=True,
        shadow_only=True,
    )
    promoted = _promoted_cohorts(shadow_rows, hl_cfg)
    shadow_stats = _shadow_cohort_stats(shadow_rows, hl_cfg)
    scan_rows = _scan_current_signals(args.strategy_url, hl_cfg, promoted)
    candidates = _scan_directional_candidates(
        args.database_url,
        args.strategy_url,
        hl_cfg,
        promoted,
        shadow_stats,
    )

    print("# Hyperliquid Perp Guardrail Audit")
    print(f"promoted_coins={len(promoted)} promoted_cohorts={sum(len(v) for v in promoted.values())}")
    if not promoted:
        print("No promoted shadow cohorts currently meet thresholds.")
        return 0

    print("\n## Promoted Cohorts And Current Signals")
    for row in scan_rows:
        print(
            f"{row['coin']:10} {row['strategy']:28} {row['side']:5} {row['regime']:13} "
            f"n={row['closed']:3} wr={row['win_rate']*100:5.1f}% pnl={row['realized']:7.2f} "
            f"now={row['current_side']:5}/{row['current_regime']:13} "
            f"match={str(row['matches']).lower():5} "
            f"selected={row['selected_strategy'] or 'none'}:{row['selected_side']}"
        )
        if row["matches"]:
            print(f"  promotion_gate={row['promotion_gate'].get('reason')}")
            print(f"  edge_gate={row['edge_gate'].get('reason')}")
            print(f"  regime_gate={row['regime_gate'].get('reason')}")

    print("\n## Current Directional Candidates")
    if not candidates:
        print("No current long/short strategy payloads in the configured/promoted universe.")
    for row in candidates[:20]:
        if row.get("error"):
            print(f"{row['coin']:10} fetch_error={row['error']}")
            continue
        promo_gate = row["promotion_gate"]
        edge_gate = row["edge_gate"]
        regime_gate = row["regime_gate"]
        gate_bits = []
        if promo_gate.get("blocked"):
            gate_bits.append(f"promotion={promo_gate.get('reason')}")
        if edge_gate.get("blocked"):
            gate_bits.append(f"edge={edge_gate.get('reason')}")
        if regime_gate.get("blocked"):
            gate_bits.append(f"regime={regime_gate.get('reason')}")
        gates = "; ".join(gate_bits) if gate_bits else "clear"
        print(
            f"{row['coin']:10} {row['strategy']:28} {row['side']:5} {row['regime']:13} "
            f"conf={row['confidence']:.2f} str={row['strength']:.2f} "
            f"shadow_n={row['closed']:3} wr={row['win_rate']*100:5.1f}% "
            f"pnl={row['realized']:7.2f} promoted={str(row['promoted']).lower():5} "
            f"selected={str(row['selected']).lower():5} gates={gates}"
        )

    print("\n## Configured Losing-Lane Blocks")
    blocks = hl_cfg.get("strategy_regime_side_blocks") or {}
    for strategy, regimes in sorted(blocks.items()):
        if not isinstance(regimes, dict):
            continue
        for regime, sides in sorted(regimes.items()):
            print(f"{strategy:28} {regime:15} {','.join(str(x) for x in sides)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
