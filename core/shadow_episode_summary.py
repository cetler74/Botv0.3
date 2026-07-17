"""
Independent-episode aggregation for Hyperliquid shadow (counterfactual) trades.

Repeated orchestrator scans can open multiple shadow rows for the same live setup.
Episode grouping treats overlapping runs on the same strategy/version/coin/side
as one opportunity and uses the first closed row's PnL for performance totals.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from perp_paper_pnl_report import exit_bucket
    from strategy_trade_evidence import normalized_closed_trade_evidence
except ImportError:  # pragma: no cover - package imports
    from core.perp_paper_pnl_report import exit_bucket
    from core.strategy_trade_evidence import normalized_closed_trade_evidence


CohortKey = Tuple[str, str, str, str, str, str, str, str, str]


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _metadata(trade: Mapping[str, Any]) -> Dict[str, Any]:
    meta = trade.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _edge_gate_value(raw: Mapping[str, Any], *, summary: bool = False) -> str:
    key = "edge_gate_passed" if summary else "shadow_edge_passed"
    val = raw.get(key)
    if val is None:
        return "unknown"
    if isinstance(val, bool):
        return "true" if val else "false"
    text = str(val).strip().lower()
    return text or "unknown"


def shadow_cohort_key(trade: Mapping[str, Any]) -> CohortKey:
    meta = _metadata(trade)
    return (
        str(trade.get("source_strategy") or "unknown"),
        str(meta.get("strategy_version") or "unversioned"),
        str(meta.get("strategy_config_hash") or "unknown"),
        str(trade.get("position_side") or "unknown"),
        str(meta.get("market_regime") or "unknown"),
        _edge_gate_value(meta, summary=False),
        str(meta.get("real_execution_status") or "legacy_unclassified"),
        str(meta.get("downstream_block_reason") or "none"),
        str(meta.get("shadow_exit_policy_version") or "legacy"),
    )


def shadow_cohort_key_from_summary(row: Mapping[str, Any]) -> CohortKey:
    return (
        str(row.get("source_strategy") or "unknown"),
        str(row.get("strategy_version") or "unversioned"),
        str(row.get("strategy_config_hash") or "unknown"),
        str(row.get("position_side") or "unknown"),
        str(row.get("market_regime") or "unknown"),
        _edge_gate_value(row, summary=True),
        str(row.get("real_execution_status") or "legacy_unclassified"),
        str(row.get("downstream_block_reason") or "none"),
        str(row.get("shadow_exit_policy_version") or "legacy"),
    )


def _instrument_side_key(
    trade: Mapping[str, Any],
) -> Tuple[str, str, str, str, str]:
    metadata = _metadata(trade)
    return (
        str(trade.get("source_strategy") or "unknown"),
        str(metadata.get("strategy_version") or "unversioned"),
        str(metadata.get("strategy_config_hash") or "unknown"),
        str(trade.get("coin") or "unknown"),
        str(trade.get("position_side") or "unknown"),
    )


def independent_closed_episode_rows(
    trades: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Return one representative closed row per independent episode.

    A new episode starts when entry_time is strictly after the prior episode's
    latest exit_time on the same strategy/version/coin/side. Overlapping rows
    share an episode; only the earliest row in that episode is kept for PnL.
    """
    closed = [
        dict(trade)
        for trade in trades
        if str(trade.get("status") or "").upper() == "CLOSED"
    ]
    if not closed:
        return []

    grouped: Dict[Tuple[str, str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for trade in closed:
        grouped[_instrument_side_key(trade)].append(trade)

    episodes: List[Dict[str, Any]] = []
    for _, items in grouped.items():
        items.sort(
            key=lambda row: _parse_dt(row.get("entry_time"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        running_exit: Optional[datetime] = None
        episode_first: Optional[Dict[str, Any]] = None
        for row in items:
            entry = _parse_dt(row.get("entry_time"))
            exit_time = _parse_dt(row.get("exit_time"))
            if running_exit is None or (entry is not None and entry > running_exit):
                if episode_first is not None:
                    episodes.append(episode_first)
                episode_first = row
                running_exit = exit_time
            elif exit_time is not None:
                running_exit = max(running_exit, exit_time) if running_exit else exit_time
        if episode_first is not None:
            episodes.append(episode_first)
    return episodes


def _aggregate_episode_stats(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    closed_rows = sorted(
        list(rows),
        key=lambda row: _parse_dt(row.get("exit_time"))
        or _parse_dt(row.get("entry_time"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    closed = len(closed_rows)
    wins = sum(1 for row in closed_rows if float(row.get("realized_pnl") or 0.0) > 0.0)
    losses = sum(1 for row in closed_rows if float(row.get("realized_pnl") or 0.0) < 0.0)
    realized = sum(float(row.get("realized_pnl") or 0.0) for row in closed_rows)
    fees = sum(float(row.get("fees") or 0.0) for row in closed_rows)
    gross_wins = sum(
        float(row.get("realized_pnl") or 0.0)
        for row in closed_rows
        if float(row.get("realized_pnl") or 0.0) > 0.0
    )
    gross_losses = abs(
        sum(
            float(row.get("realized_pnl") or 0.0)
            for row in closed_rows
            if float(row.get("realized_pnl") or 0.0) < 0.0
        )
    )
    win_values = [
        float(row.get("realized_pnl") or 0.0)
        for row in closed_rows
        if float(row.get("realized_pnl") or 0.0) > 0.0
    ]
    loss_values = [
        float(row.get("realized_pnl") or 0.0)
        for row in closed_rows
        if float(row.get("realized_pnl") or 0.0) < 0.0
    ]
    cumulative = peak = max_drawdown = 0.0
    for row in closed_rows:
        cumulative += float(row.get("realized_pnl") or 0.0)
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    hold_values = []
    for row in closed_rows:
        entry = _parse_dt(row.get("entry_time"))
        exit_time = _parse_dt(row.get("exit_time"))
        if entry is not None and exit_time is not None:
            hold_values.append((exit_time - entry).total_seconds() / 60.0)
    return {
        "episode_count": closed,
        "episode_closed_count": closed,
        "episode_wins": wins,
        "episode_losses": losses,
        "episode_realized_pnl": realized,
        "episode_fees": fees,
        "episode_win_rate": (wins / closed) if closed else None,
        "episode_profit_factor": (gross_wins / gross_losses) if gross_losses > 0 else None,
        "episode_expectancy": (realized / closed) if closed else None,
        "episode_average_win": (sum(win_values) / len(win_values)) if win_values else None,
        "episode_average_loss": (sum(loss_values) / len(loss_values)) if loss_values else None,
        "episode_max_drawdown": max_drawdown,
        "episode_max_winner_contribution": (
            max(win_values) / gross_wins if gross_wins > 0 else None
        ),
        "episode_average_hold_minutes": (
            sum(hold_values) / len(hold_values) if hold_values else None
        ),
    }


def episode_stats_by_cohort(
    trades: Sequence[Mapping[str, Any]],
) -> Dict[CohortKey, Dict[str, Any]]:
    episode_rows = independent_closed_episode_rows(trades)
    grouped: Dict[CohortKey, List[Dict[str, Any]]] = defaultdict(list)
    for row in episode_rows:
        grouped[shadow_cohort_key(row)].append(row)
    return {key: _aggregate_episode_stats(rows) for key, rows in grouped.items()}


def enrich_shadow_summary_cohorts(
    cohorts: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    stats_by_key = episode_stats_by_cohort(trades)
    enriched: List[Dict[str, Any]] = []
    for row in cohorts:
        item = dict(row)
        stats = stats_by_key.get(shadow_cohort_key_from_summary(row)) or {}
        item.update(
            {
                "episode_count": int(stats.get("episode_count") or 0),
                "episode_closed_count": int(stats.get("episode_closed_count") or 0),
                "episode_wins": int(stats.get("episode_wins") or 0),
                "episode_losses": int(stats.get("episode_losses") or 0),
                "episode_realized_pnl": float(stats.get("episode_realized_pnl") or 0.0),
                "episode_fees": float(stats.get("episode_fees") or 0.0),
                "episode_win_rate": stats.get("episode_win_rate"),
                "episode_profit_factor": stats.get("episode_profit_factor"),
                "episode_expectancy": stats.get("episode_expectancy"),
                "episode_average_win": stats.get("episode_average_win"),
                "episode_average_loss": stats.get("episode_average_loss"),
                "episode_max_drawdown": stats.get("episode_max_drawdown"),
                "episode_max_winner_contribution": stats.get(
                    "episode_max_winner_contribution"
                ),
                "episode_average_hold_minutes": stats.get("episode_average_hold_minutes"),
            }
        )
        enriched.append(item)
    return enriched


def shadow_promotion_cohort_identity(
    row: Mapping[str, Any],
) -> Tuple[Tuple[str, str, str, str], Dict[str, str]]:
    """Return a four-part consumer key plus explicit strategy evidence labels."""
    metadata = _metadata(row)
    coin = str(row.get("coin") or "")
    strategy = str(row.get("source_strategy") or "").strip().lower()
    side = str(row.get("position_side") or "").strip().lower()
    regime = str(metadata.get("market_regime") or "").strip().lower()
    strategy_version = str(
        metadata.get("strategy_version") or "unversioned"
    ).strip()
    config_hash = str(
        metadata.get("strategy_config_hash") or "unknown"
    ).strip()
    strategy_identity = strategy
    if strategy_version != "unversioned" or config_hash != "unknown":
        strategy_identity = f"{strategy}@{strategy_version}#{config_hash}"
    return (
        (coin, strategy_identity, side, regime),
        {
            "coin": coin,
            "strategy": strategy,
            "side": side,
            "regime": regime,
            "strategy_version": strategy_version,
            "strategy_config_hash": config_hash,
        },
    )


def shadow_promotion_cohorts_from_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    cutoff: Optional[datetime] = None,
) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    """Episode stats keyed by a backward-compatible four-part version identity."""
    filtered: List[Dict[str, Any]] = []
    cutoff_utc = _parse_dt(cutoff)
    for trade in trades:
        if str(trade.get("status") or "").upper() != "CLOSED":
            continue
        exit_dt = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if cutoff_utc is not None and exit_dt is not None:
            if exit_dt < cutoff_utc:
                continue
        filtered.append(dict(trade))

    episode_rows = independent_closed_episode_rows(filtered)
    identities_by_base: Dict[
        Tuple[str, str, str, str], set[Tuple[str, str]]
    ] = defaultdict(set)
    prepared_rows = []
    for row in episode_rows:
        versioned_key, identity = shadow_promotion_cohort_identity(row)
        base_key = (
            identity["coin"],
            identity["strategy"],
            identity["side"],
            identity["regime"],
        )
        identities_by_base[base_key].add(
            (identity["strategy_version"], identity["strategy_config_hash"])
        )
        prepared_rows.append((row, base_key, versioned_key, identity))
    prepared_rows.sort(
        key=lambda item: (
            _parse_dt(item[0].get("exit_time") or item[0].get("updated_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            item[2],
        )
    )

    cohorts: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for row, base_key, versioned_key, identity in prepared_rows:
        coin = identity["coin"]
        strategy = identity["strategy"]
        side = identity["side"]
        regime = identity["regime"]
        if not coin or not strategy or side not in {"long", "short"}:
            continue
        key = (
            base_key
            if len(identities_by_base[base_key]) == 1
            else versioned_key
        )
        cohort = cohorts.setdefault(
            key,
            {
                **identity,
                "episodes": 0,
                "wins": 0,
                "realized": 0.0,
                "gross_wins": 0.0,
                "gross_losses": 0.0,
                "episode_pnls": [],
                "last_exit": None,
            },
        )
        pnl = float(row.get("realized_pnl") or 0.0)
        cohort["episodes"] += 1
        if pnl > 0:
            cohort["wins"] += 1
            cohort["gross_wins"] += pnl
        elif pnl < 0:
            cohort["gross_losses"] += abs(pnl)
        cohort["realized"] += pnl
        cohort["episode_pnls"].append(pnl)
        exit_dt = _parse_dt(row.get("exit_time") or row.get("updated_at"))
        if exit_dt is not None and (
            cohort["last_exit"] is None or exit_dt > cohort["last_exit"]
        ):
            cohort["last_exit"] = exit_dt
    for cohort in cohorts.values():
        pnls = list(cohort.pop("episode_pnls", []))
        loss = float(cohort.get("gross_losses") or 0.0)
        cohort["profit_factor"] = (
            float(cohort.get("gross_wins") or 0.0) / loss if loss > 0 else None
        )
        split = max(1, int(len(pnls) * 0.70)) if pnls else 0
        cohort["training_realized"] = sum(pnls[:split])
        cohort["holdout_realized"] = sum(pnls[split:])
        cohort["holdout_episodes"] = len(pnls[split:])
        total_abs_profit = sum(value for value in pnls if value > 0)
        cohort["max_winner_contribution"] = (
            max((value for value in pnls if value > 0), default=0.0)
            / total_abs_profit
            if total_abs_profit > 0
            else None
        )
    return cohorts


def shadow_summary_totals(
    cohorts: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    raw_closed = sum(int(row.get("closed_count") or 0) for row in cohorts)
    raw_pnl = sum(float(row.get("realized_pnl") or 0.0) for row in cohorts)
    episode_rows = independent_closed_episode_rows(trades)
    episode_stats = _aggregate_episode_stats(episode_rows)
    normalized_evidence = [
        normalized_closed_trade_evidence(
            row,
            market_type="perp",
            exit_bucket_value=exit_bucket(str(row.get("exit_reason") or "")),
        )
        for row in episode_rows
    ]
    return {
        "raw": {
            "closed_count": raw_closed,
            "realized_pnl": raw_pnl,
        },
        "episode": {
            "closed_count": int(episode_stats.get("episode_closed_count") or 0),
            "realized_pnl": float(episode_stats.get("episode_realized_pnl") or 0.0),
            "win_rate": episode_stats.get("episode_win_rate"),
            "profit_factor": episode_stats.get("episode_profit_factor"),
            "expectancy": episode_stats.get("episode_expectancy"),
            "average_win": episode_stats.get("episode_average_win"),
            "average_loss": episode_stats.get("episode_average_loss"),
            "max_drawdown": episode_stats.get("episode_max_drawdown"),
            "max_winner_contribution": episode_stats.get(
                "episode_max_winner_contribution"
            ),
        },
        "episode_inflation_ratio": (
            (raw_closed / episode_stats["episode_closed_count"])
            if episode_stats.get("episode_closed_count")
            else None
        ),
        "normalizedEvidence": normalized_evidence,
    }
