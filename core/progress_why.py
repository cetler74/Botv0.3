"""Compose Progress & Why dashboard payloads for spot and perps.

No new tables — aggregates closed-trade evidence plus config/gate state.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from perp_paper_pnl_report import (
        is_accounting_excluded,
        parse_ts,
        profit_factor,
        trade_window_timestamp,
    )
except ImportError:  # pragma: no cover
    from core.perp_paper_pnl_report import (
        is_accounting_excluded,
        parse_ts,
        profit_factor,
        trade_window_timestamp,
    )


DEFAULT_DAILY_TARGET_USD = 20.0
DEFAULT_MAX_DRAWDOWN_PCT = 5.0
DEFAULT_ROLLING_DAYS = 30


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _strategy_key(trade: Dict[str, Any]) -> str:
    meta = trade.get("metadata") or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return str(
        meta.get("strategy_key")
        or trade.get("source_strategy")
        or trade.get("strategy")
        or "unknown"
    ).strip().lower()


def _strategy_version(trade: Dict[str, Any]) -> str:
    meta = trade.get("metadata") or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return str(meta.get("strategy_version") or "unversioned").strip()


def _closed_realized(trade: Dict[str, Any]) -> float:
    if trade.get("realized_pnl") is not None:
        return _f(trade.get("realized_pnl"))
    return _f(trade.get("pnl"))


def _filter_closed(
    trades: Sequence[Dict[str, Any]],
    *,
    days: int,
    market_type: str,
) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    out: List[Dict[str, Any]] = []
    for trade in trades:
        if str(trade.get("status") or "").upper() != "CLOSED":
            continue
        if market_type == "perp" and is_accounting_excluded(trade):
            continue
        ts = trade_window_timestamp(trade) or parse_ts(trade.get("exit_time"))
        if ts is None or ts < cutoff:
            continue
        out.append(trade)
    return out


def _daily_series(closed: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    by_day: Dict[str, float] = defaultdict(float)
    for trade in closed:
        ts = trade_window_timestamp(trade) or parse_ts(trade.get("exit_time"))
        if ts is None:
            continue
        day = ts.astimezone(timezone.utc).date().isoformat()
        by_day[day] += _closed_realized(trade)
    return dict(by_day)


def _rolling_drawdown_pct(
    closed: Sequence[Dict[str, Any]],
    *,
    starting_equity: float,
) -> Dict[str, float]:
    equity = max(_f(starting_equity, 10000.0), 1.0)
    peak = equity
    max_dd_pct = 0.0
    max_dd_usd = 0.0
    ordered = sorted(
        closed,
        key=lambda t: (
            trade_window_timestamp(t) or parse_ts(t.get("exit_time")) or datetime.min.replace(tzinfo=timezone.utc)
        ),
    )
    for trade in ordered:
        equity += _closed_realized(trade)
        if equity > peak:
            peak = equity
        dd_usd = peak - equity
        dd_pct = (dd_usd / peak * 100.0) if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_usd = dd_usd
    return {
        "maxDrawdownPct": round(max_dd_pct, 4),
        "maxDrawdownUsd": round(max_dd_usd, 4),
        "endingEquity": round(equity, 4),
        "peakEquity": round(peak, 4),
    }


def _lane_metrics(closed: Sequence[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in closed:
        by_key[_strategy_key(trade)].append(trade)

    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
    lanes: List[Dict[str, Any]] = []
    for strategy, rows in by_key.items():
        wins = [r for r in rows if _closed_realized(r) > 0]
        losses = [r for r in rows if _closed_realized(r) < 0]
        pnl = sum(_closed_realized(r) for r in rows)
        wins_sum = sum(_closed_realized(r) for r in wins)
        losses_sum = sum(_closed_realized(r) for r in losses)
        recent = [
            r
            for r in rows
            if (trade_window_timestamp(r) or parse_ts(r.get("exit_time")) or datetime.min.replace(tzinfo=timezone.utc))
            >= cutoff_7d
        ]
        versions = {_strategy_version(r) for r in rows}
        lanes.append(
            {
                "strategy": strategy,
                "version": sorted(versions)[-1] if versions else "unversioned",
                "tradeCount": len(rows),
                "pnl30d": round(pnl, 4),
                "pnl7d": round(sum(_closed_realized(r) for r in recent), 4),
                "winRate": round(100.0 * len(wins) / len(rows), 2) if rows else 0.0,
                "expectancy": round(pnl / len(rows), 4) if rows else 0.0,
                "profitFactor": profit_factor(wins_sum, losses_sum),
                "windowDays": days,
            }
        )
    lanes.sort(key=lambda x: x["pnl30d"])
    return lanes


def _status_for_strategy(
    strategy: str,
    *,
    market_type: str,
    enabled_map: Dict[str, bool],
    live_allowlist: Sequence[str],
    shadow_strategies: Sequence[str],
    require_promotion: Sequence[str],
    adaptive_actions: Dict[str, str],
    validation_manifest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    enabled = bool(enabled_map.get(strategy, False))
    in_live = strategy in set(live_allowlist or [])
    in_shadow = strategy in set(shadow_strategies or [])
    needs_promo = strategy in set(require_promotion or [])
    adaptive = str(adaptive_actions.get(strategy) or "").strip().lower()
    manifest_ok = None
    if isinstance(validation_manifest, dict):
        approved = {
            str(x).strip().lower()
            for x in (validation_manifest.get("approved_strategies") or [])
            if str(x).strip()
        }
        rejected = {
            str(x).strip().lower()
            for x in (validation_manifest.get("rejected_strategies") or [])
            if str(x).strip()
        }
        if strategy in approved:
            manifest_ok = True
        elif strategy in rejected:
            manifest_ok = False

    reason_codes: List[str] = []
    if not enabled:
        status = "retired" if strategy in (enabled_map or {}) else "blocked"
        reason_codes.append("config_disabled")
        next_action = "retire" if status == "retired" else "pause"
        why = f"{strategy} is disabled in config."
    elif adaptive in {"pause", "block", "retire"}:
        status = "blocked"
        reason_codes.append(f"adaptive_{adaptive}")
        next_action = "pause"
        why = f"Adaptive control set {strategy} to {adaptive}."
    elif market_type == "perp" and needs_promo and not in_live:
        status = "shadow_only" if in_shadow or enabled else "probation"
        reason_codes.append("promotion_required")
        next_action = "continue"
        why = f"{strategy} remains shadow/probation until promotion gates pass."
    elif market_type == "perp" and in_live:
        status = "promoted"
        reason_codes.append("live_allowlist")
        next_action = "continue"
        why = f"{strategy} is on the live/paper executable allowlist."
    elif manifest_ok is False:
        status = "blocked"
        reason_codes.append("validation_manifest_rejected")
        next_action = "retire"
        why = f"{strategy} failed offline validation gates."
    elif manifest_ok is True:
        status = "probation" if not in_live else "promoted"
        reason_codes.append("validation_manifest_approved")
        next_action = "promote" if status == "probation" else "continue"
        why = f"{strategy} passed offline validation; awaiting guarded promotion."
    elif enabled:
        status = "enabled"
        reason_codes.append("config_enabled")
        next_action = "continue"
        why = f"{strategy} is enabled and collecting closed-trade evidence."
    else:
        status = "blocked"
        reason_codes.append("unknown_gate")
        next_action = "pause"
        why = f"{strategy} has no clear enable path."

    return {
        "status": status,
        "why": why,
        "reasonCodes": reason_codes,
        "nextAction": next_action,
        "continueCriteria": _continue_criteria(status, next_action),
    }


def _continue_criteria(status: str, next_action: str) -> str:
    if next_action == "promote":
        return "Need positive OOS expectancy, PF≥1.25, and validation-manifest approval."
    if next_action == "pause":
        return "Resume only after drawdown recovers inside 5% and expectancy turns positive."
    if next_action == "retire":
        return "Keep retired until a new versioned config hash clears holdout gates."
    if status == "shadow_only":
        return "Promote when episode-aware shadow PF≥1.25 and promotion cohort clears."
    return "Continue while rolling 30d avg daily PnL trends toward $20 and DD≤5%."


def build_progress_why_payload(
    *,
    market_type: str,
    closed_trades: Sequence[Dict[str, Any]],
    open_trades: Sequence[Dict[str, Any]] = (),
    starting_equity: float = 10000.0,
    daily_target_usd: float = DEFAULT_DAILY_TARGET_USD,
    max_drawdown_pct: float = DEFAULT_MAX_DRAWDOWN_PCT,
    rolling_days: int = DEFAULT_ROLLING_DAYS,
    enabled_map: Optional[Dict[str, bool]] = None,
    live_allowlist: Optional[Sequence[str]] = None,
    shadow_strategies: Optional[Sequence[str]] = None,
    require_promotion: Optional[Sequence[str]] = None,
    adaptive_actions: Optional[Dict[str, str]] = None,
    validation_manifest: Optional[Dict[str, Any]] = None,
    size_multipliers: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Build the shared Progress & Why contract for dashboards."""
    enabled_map = dict(enabled_map or {})
    live_allowlist = list(live_allowlist or [])
    shadow_strategies = list(shadow_strategies or [])
    require_promotion = [
        str(row.get("strategy") if isinstance(row, dict) else row).strip().lower()
        for row in (require_promotion or [])
        if (row.get("strategy") if isinstance(row, dict) else row)
    ]
    adaptive_actions = {str(k).lower(): str(v) for k, v in (adaptive_actions or {}).items()}
    size_multipliers = {str(k).lower(): _f(v, 1.0) for k, v in (size_multipliers or {}).items()}

    closed_30d = _filter_closed(closed_trades, days=rolling_days, market_type=market_type)
    daily = _daily_series(closed_30d)
    day_count = max(len(daily), 1)
    total_pnl = sum(daily.values())
    avg_daily = total_pnl / day_count if daily else 0.0
    today = datetime.now(timezone.utc).date().isoformat()
    today_pnl = _f(daily.get(today))
    gap = daily_target_usd - avg_daily
    dd = _rolling_drawdown_pct(closed_30d, starting_equity=starting_equity)
    fees = sum(_f(t.get("fees")) for t in closed_30d)
    wins = [t for t in closed_30d if _closed_realized(t) > 0]
    win_rate = (100.0 * len(wins) / len(closed_30d)) if closed_30d else 0.0

    open_risk = 0.0
    for trade in open_trades or []:
        if market_type == "perp" and is_accounting_excluded(trade):
            continue
        open_risk += abs(_f(trade.get("margin") or trade.get("notional") or trade.get("position_value")))

    lane_rows = _lane_metrics(closed_30d, rolling_days)
    known_strategies = set(enabled_map) | {row["strategy"] for row in lane_rows}
    lanes: List[Dict[str, Any]] = []
    for strategy in sorted(known_strategies):
        metrics = next((row for row in lane_rows if row["strategy"] == strategy), None) or {
            "strategy": strategy,
            "version": "unversioned",
            "tradeCount": 0,
            "pnl30d": 0.0,
            "pnl7d": 0.0,
            "winRate": 0.0,
            "expectancy": 0.0,
            "profitFactor": None,
            "windowDays": rolling_days,
        }
        gate = _status_for_strategy(
            strategy,
            market_type=market_type,
            enabled_map=enabled_map,
            live_allowlist=live_allowlist,
            shadow_strategies=shadow_strategies,
            require_promotion=require_promotion,
            adaptive_actions=adaptive_actions,
            validation_manifest=validation_manifest,
        )
        # Evidence override: deep losers get pause recommendation even if enabled.
        if metrics["pnl30d"] < -abs(daily_target_usd) and gate["status"] in {"enabled", "promoted", "probation"}:
            gate = {
                **gate,
                "nextAction": "pause",
                "reasonCodes": list(gate["reasonCodes"]) + ["rolling_loss_budget"],
                "why": (
                    f"{strategy} is {gate['status']} but rolling 30d PnL is "
                    f"${metrics['pnl30d']:.2f}; pause until expectancy recovers."
                ),
                "continueCriteria": "Need 7d expectancy > 0 and drawdown inside 5% before continuing size.",
            }
        lanes.append(
            {
                **metrics,
                **gate,
                "market": market_type,
                "sizeMultiplier": size_multipliers.get(strategy, 1.0),
            }
        )
    lanes.sort(key=lambda x: (x.get("pnl30d") or 0.0, x.get("strategy") or ""))

    on_track = avg_daily >= daily_target_usd and dd["maxDrawdownPct"] <= max_drawdown_pct
    return {
        "marketType": market_type,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "targets": {
            "dailyProfitUsd": daily_target_usd,
            "maxDrawdownPct": max_drawdown_pct,
            "rollingDays": rolling_days,
        },
        "kpis": {
            "avgDailyPnl30d": round(avg_daily, 4),
            "gapToTargetUsd": round(gap, 4),
            "todayRealizedPnl": round(today_pnl, 4),
            "realizedPnl30d": round(total_pnl, 4),
            "fees30d": round(fees, 4),
            "winRate30d": round(win_rate, 2),
            "closedTrades30d": len(closed_30d),
            "activeDays30d": len(daily),
            "openRiskUsd": round(open_risk, 4),
            "maxDrawdownPct": dd["maxDrawdownPct"],
            "maxDrawdownUsd": dd["maxDrawdownUsd"],
            "drawdownBudgetRemainingPct": round(
                max(0.0, max_drawdown_pct - dd["maxDrawdownPct"]), 4
            ),
            "onTrack": on_track,
        },
        "lanes": lanes,
        "dailySeries": [
            {"date": day, "pnl": round(pnl, 4)}
            for day, pnl in sorted(daily.items())
        ],
        "validationManifest": validation_manifest or {},
        "chartsDeepLink": "/strategy-performance",
    }


def enabled_strategy_map(strategies_cfg: Any) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    if not isinstance(strategies_cfg, dict):
        return out
    for name, cfg in strategies_cfg.items():
        if name == "regime_stability" or not isinstance(cfg, dict):
            continue
        out[str(name).strip().lower()] = bool(cfg.get("enabled", False))
    return out


def normalize_validation_manifest(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize walk-forward or curated manifests into dashboard/live-readiness shape."""
    if not isinstance(raw, dict):
        return {
            "approved_strategies": [],
            "rejected_strategies": [
                "donchian_atr_pullback",
                "vwap_rsi_mean_reversion",
            ],
            "gates_all_passed": False,
            "promotion_allowed": False,
        }
    out = dict(raw)
    gates = out.get("target_gates") if isinstance(out.get("target_gates"), dict) else {}
    all_passed = bool(gates.get("all_passed")) if gates else bool(out.get("gates_all_passed"))
    approved = [
        str(x).strip().lower()
        for x in (out.get("approved_strategies") or [])
        if str(x).strip()
    ]
    rejected = [
        str(x).strip().lower()
        for x in (out.get("rejected_strategies") or [])
        if str(x).strip()
    ]
    # Default research candidates stay rejected until a curated approval list exists.
    if not approved and not all_passed:
        for name in ("donchian_atr_pullback", "vwap_rsi_mean_reversion"):
            if name not in rejected:
                rejected.append(name)
    out["approved_strategies"] = approved
    out["rejected_strategies"] = rejected
    out["gates_all_passed"] = all_passed
    out["promotion_allowed"] = bool(all_passed and approved)
    return out


def load_validation_manifest(path: Optional[str] = None) -> Dict[str, Any]:
    """Best-effort read of offline selection artifact (never auto-enables)."""
    import json
    from pathlib import Path

    candidates = []
    if path:
        candidates.append(Path(path))
    root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            root / "analysis_outputs" / "strategy_portfolio_validation_manifest.json",
            root / "analysis_outputs" / "validation_manifest.json",
        ]
    )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return normalize_validation_manifest(json.loads(candidate.read_text()))
        except (OSError, TypeError, ValueError):
            continue
    return normalize_validation_manifest({})
