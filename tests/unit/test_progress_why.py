"""Progress & Why dashboard payload contracts."""

from core.progress_why import build_progress_why_payload, enabled_strategy_map


def test_progress_kpis_compute_gap_and_drawdown():
    closed = [
        {
            "status": "CLOSED",
            "source_strategy": "rsi_stoch_reversal_15m",
            "realized_pnl": 25.0,
            "fees": 1.0,
            "exit_time": "2026-07-01T12:00:00+00:00",
            "metadata": {"strategy_version": "v1"},
        },
        {
            "status": "CLOSED",
            "source_strategy": "rsi_stoch_reversal_15m",
            "realized_pnl": -10.0,
            "fees": 1.0,
            "exit_time": "2026-07-02T12:00:00+00:00",
            "metadata": {"strategy_version": "v1"},
        },
        {
            "status": "CLOSED",
            "source_strategy": "supply_demand_3step",
            "realized_pnl": -30.0,
            "fees": 1.0,
            "exit_time": "2026-07-03T12:00:00+00:00",
            "metadata": {"strategy_version": "v2"},
        },
    ]
    payload = build_progress_why_payload(
        market_type="perp",
        closed_trades=closed,
        starting_equity=10000.0,
        daily_target_usd=20.0,
        max_drawdown_pct=5.0,
        rolling_days=30,
        enabled_map={
            "rsi_stoch_reversal_15m": True,
            "supply_demand_3step": True,
            "donchian_atr_pullback": False,
        },
        live_allowlist=["rsi_stoch_reversal_15m"],
        require_promotion=[{"strategy": "supply_demand_3step"}],
        shadow_strategies=["supply_demand_3step"],
    )

    # total pnl = 25-10-30 = -15 over 3 active days => -5 avg; gap to $20 = 25
    assert payload["kpis"]["avgDailyPnl30d"] == -5.0
    assert payload["kpis"]["gapToTargetUsd"] == 25.0
    assert payload["kpis"]["maxDrawdownPct"] >= 0.0
    assert payload["targets"]["dailyProfitUsd"] == 20.0
    assert payload["chartsDeepLink"] == "/strategy-performance"

    by_name = {lane["strategy"]: lane for lane in payload["lanes"]}
    assert by_name["rsi_stoch_reversal_15m"]["status"] == "promoted"
    assert by_name["rsi_stoch_reversal_15m"]["nextAction"] in {"continue", "pause"}
    assert by_name["supply_demand_3step"]["status"] == "shadow_only"
    assert "promotion_required" in by_name["supply_demand_3step"]["reasonCodes"]
    assert by_name["donchian_atr_pullback"]["status"] in {"retired", "blocked"}
    assert by_name["donchian_atr_pullback"]["why"]


def test_enabled_strategy_map_skips_non_strategy_blocks():
    assert enabled_strategy_map(
        {
            "rsi_stoch_reversal_15m": {"enabled": True},
            "regime_stability": {"enabled": True},
            "noise": "skip",
        }
    ) == {"rsi_stoch_reversal_15m": True}


def test_normalize_validation_manifest_rejects_candidates_when_gates_fail():
    from core.progress_why import normalize_validation_manifest

    normalized = normalize_validation_manifest(
        {
            "target_gates": {"all_passed": False},
            "promotion_performed": False,
        }
    )
    assert normalized["gates_all_passed"] is False
    assert normalized["promotion_allowed"] is False
    assert "donchian_atr_pullback" in normalized["rejected_strategies"]
    assert "vwap_rsi_mean_reversion" in normalized["rejected_strategies"]
