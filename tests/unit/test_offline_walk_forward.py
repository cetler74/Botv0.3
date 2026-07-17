"""Focused contracts for the offline 1h/15m portfolio evaluator."""

from __future__ import annotations

import json

import pandas as pd
import pytest

import core.offline_walk_forward as offline_walk_forward
from scripts import evaluate_offline_walk_forward as evaluator_cli
from core.offline_walk_forward import (
    EvaluationConfig,
    TradeIntent,
    closed_bar_ma_intents,
    compute_metrics,
    deterministic_json_bytes,
    evaluate_walk_forward,
    load_exchange_ohlcv,
    prepare_closed_bars,
    rolling_splits,
    simulate_portfolio,
)


def _bars(
    closes,
    *,
    start="2026-01-01",
    freq="15min",
    highs=None,
    lows=None,
):
    closes = [float(value) for value in closes]
    index = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs if highs is not None else closes,
            "low": lows if lows is not None else closes,
            "close": closes,
            "volume": [1000.0] * len(closes),
        },
        index=index,
    )


def _config(**overrides):
    values = {
        "initial_equity": 1000.0,
        "fee_bps": 0.0,
        "spread_bps": 0.0,
        "slippage_bps": 0.0,
        "max_positions": 2,
        "daily_loss_limit": 0.0,
    }
    values.update(overrides)
    return EvaluationConfig(**values)


def _as_of(frames, timeframe="15m"):
    interval = pd.Timedelta(minutes=15) if timeframe == "15m" else pd.Timedelta(hours=1)
    return max(frame.index[-1] for frame in frames.values()) + interval


def _simulate(bars, intents, config):
    return simulate_portfolio(
        bars,
        intents,
        config,
        evaluation_as_of=_as_of(bars),
    )


def _three_day_equity_index():
    return pd.DatetimeIndex(
        [
            pd.Timestamp("2026-01-01", tz="UTC") - pd.Timedelta(nanoseconds=1),
            pd.Timestamp("2026-01-01 23:59", tz="UTC"),
            pd.Timestamp("2026-01-02 23:59", tz="UTC"),
            pd.Timestamp("2026-01-03 23:59", tz="UTC"),
        ]
    )


def test_closed_bars_drop_forming_bar_and_reject_non_hourly_inputs():
    bars = _bars([100, 101, 102], freq="1h")
    closed = prepare_closed_bars(
        bars,
        "1h",
        as_of=pd.Timestamp("2026-01-01 02:30", tz="UTC"),
    )
    assert list(closed.index) == list(bars.index[:2])

    irregular = bars.copy()
    irregular.index = irregular.index[:2].append(
        pd.DatetimeIndex([pd.Timestamp("2026-01-01 02:30", tz="UTC")])
    )
    with pytest.raises(ValueError, match="aligned"):
        prepare_closed_bars(irregular, "1h", as_of=pd.Timestamp("2026-01-02", tz="UTC"))


def test_fee_spread_and_slippage_are_charged_on_both_sides():
    bars = {"BTC": _bars([100, 110])}
    intent = TradeIntent("BTC", bars["BTC"].index[0], "spot", "long", quantity=1)
    result = _simulate(
        bars,
        [intent],
        _config(fee_bps=10, spread_bps=20, slippage_bps=30),
    )
    trade = result["trades"][0]
    assert trade["entry_price"] == pytest.approx(100.5)
    assert trade["exit_price"] == pytest.approx(109.45)
    assert trade["fees"] == pytest.approx((100.5 + 109.45) * 0.001)
    assert trade["net_pnl"] == pytest.approx(8.95 - trade["fees"])


def test_ambiguous_stop_and_target_touch_exits_at_stop_first():
    bars = {
        "BTC": _bars(
            [100, 100],
            highs=[100, 110],
            lows=[100, 90],
        )
    }
    intent = TradeIntent(
        "BTC",
        bars["BTC"].index[0],
        "spot",
        "long",
        quantity=1,
        stop_price=95,
        target_price=105,
    )
    trade = _simulate(bars, [intent], _config())["trades"][0]
    assert trade["exit_reason"] == "stop"
    assert trade["exit_price"] == 95


def test_spot_is_long_only_and_perp_supports_short_directionality():
    bars = {"BTC": _bars([100, 90])}
    with pytest.raises(ValueError, match="spot.*long"):
        _simulate(
            bars,
            [TradeIntent("BTC", bars["BTC"].index[0], "spot", "short")],
            _config(),
        )
    short = TradeIntent("BTC", bars["BTC"].index[0], "perp", "short", quantity=2)
    trade = _simulate(bars, [short], _config())["trades"][0]
    assert trade["net_pnl"] == pytest.approx(20)


def test_concurrent_position_limit_blocks_excess_entries():
    bars = {"BTC": _bars([100, 101]), "ETH": _bars([50, 51])}
    timestamp = bars["BTC"].index[0]
    intents = [
        TradeIntent("BTC", timestamp, "spot", "long"),
        TradeIntent("ETH", timestamp, "spot", "long"),
    ]
    result = _simulate(bars, intents, _config(max_positions=1))
    assert len(result["trades"]) == 1
    assert result["rejections"] == [
        {"symbol": "ETH", "time": timestamp.isoformat(), "reason": "max_positions"}
    ]


def test_intrabar_exit_cannot_free_capacity_for_same_bar_open_entry():
    btc = _bars([100, 110], highs=[100, 110], lows=[100, 100])
    eth = _bars([50, 51])
    intents = [
        TradeIntent(
            "BTC",
            btc.index[0],
            "spot",
            "long",
            target_price=105,
        ),
        TradeIntent("ETH", eth.index[1], "spot", "long"),
    ]
    result = _simulate(
        {"BTC": btc, "ETH": eth},
        intents,
        _config(max_positions=1),
    )
    assert result["trades"][0]["exit_reason"] == "target"
    assert result["rejections"] == [
        {
            "symbol": "ETH",
            "time": eth.index[1].isoformat(),
            "reason": "max_positions",
        }
    ]


def test_daily_loss_halt_blocks_later_same_day_but_resets_next_day():
    btc = _bars([100, 90, 100, 101], start="2026-01-01 00:00")
    eth = _bars([50] * 97 + [51], start="2026-01-01 00:00")
    intents = [
        TradeIntent(
            "BTC", btc.index[0], "spot", "long", stop_price=95, max_hold_bars=1
        ),
        TradeIntent("ETH", eth.index[2], "spot", "long", max_hold_bars=1),
        TradeIntent("ETH", eth.index[96], "spot", "long", max_hold_bars=1),
    ]
    result = _simulate(
        {"BTC": btc, "ETH": eth},
        intents,
        _config(daily_loss_limit=4),
    )
    assert [row["reason"] for row in result["rejections"]] == ["daily_loss_halt"]
    assert len(result["trades"]) == 2


def test_daily_loss_halt_stays_latched_after_same_day_equity_recovery():
    btc = _bars([100, 90, 110, 110])
    eth = _bars([50, 50, 50, 51])
    result = _simulate(
        {"BTC": btc, "ETH": eth},
        [
            TradeIntent("BTC", btc.index[0], "spot", "long"),
            TradeIntent("ETH", eth.index[3], "spot", "long"),
        ],
        _config(daily_loss_limit=5),
    )
    assert result["rejections"] == [
        {
            "symbol": "ETH",
            "time": eth.index[3].isoformat(),
            "reason": "daily_loss_halt",
        }
    ]


def test_latched_daily_loss_halt_resets_on_next_utc_day():
    btc = _bars([100, 90] + [100] * 96)
    eth = _bars([50] * 97 + [51])
    result = _simulate(
        {"BTC": btc, "ETH": eth},
        [
            TradeIntent("BTC", btc.index[0], "spot", "long"),
            TradeIntent("ETH", eth.index[96], "spot", "long"),
        ],
        _config(daily_loss_limit=5),
    )
    assert result["rejections"] == []
    assert {trade["symbol"] for trade in result["trades"]} == {"BTC", "ETH"}


def test_structural_trailing_and_max_hold_exits_are_supported():
    trailing_bars = {"BTC": _bars([100, 110, 104])}
    trailing = TradeIntent(
        "BTC",
        trailing_bars["BTC"].index[0],
        "perp",
        "long",
        trailing_pct=0.05,
    )
    trade = _simulate(trailing_bars, [trailing], _config())["trades"][0]
    assert trade["exit_reason"] == "trailing_stop"
    assert trade["exit_price"] == pytest.approx(104.0)

    hold_bars = {"BTC": _bars([100, 101, 102, 103])}
    held = TradeIntent(
        "BTC", hold_bars["BTC"].index[0], "spot", "long", max_hold_bars=2
    )
    trade = _simulate(hold_bars, [held], _config())["trades"][0]
    assert trade["exit_reason"] == "max_hold"
    assert trade["exit_price"] == 102


def test_position_opened_on_last_available_bar_is_closed_at_end_of_data():
    bars = {"BTC": _bars([100])}
    intent = TradeIntent("BTC", bars["BTC"].index[0], "spot", "long")
    trade = _simulate(bars, [intent], _config())["trades"][0]
    assert trade["exit_reason"] == "end_of_data"
    assert trade["net_pnl"] == 0


def test_drawdown_metrics_use_equity_peak_in_dollars_and_percent():
    curve = pd.Series(
        [1000.0, 1100.0, 990.0, 1050.0],
        index=pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC"),
    )
    metrics = compute_metrics([], curve, fold_pnls=[1, -1, 2], trial_count=4)
    assert metrics["max_drawdown_dollars"] == pytest.approx(110)
    assert metrics["max_drawdown_percent"] == pytest.approx(0.10)
    assert metrics["positive_fold_ratio"] == pytest.approx(2 / 3)
    assert metrics["trade_count"] == 0
    assert metrics["selection_adjusted_sharpe_approximation"]["label"].startswith(
        "Approximate"
    )


def test_simulated_equity_drawdown_includes_initial_capital_peak():
    bars = {"BTC": _bars([90])}
    bars["BTC"].loc[:, "open"] = 100.0
    bars["BTC"].loc[:, "high"] = 100.0
    bars["BTC"].loc[:, "low"] = 90.0
    intent = TradeIntent("BTC", bars["BTC"].index[0], "spot", "long")
    result = _simulate(bars, [intent], _config())
    metrics = compute_metrics(result["trades"], result["equity"])
    assert metrics["max_drawdown_dollars"] == pytest.approx(10)
    assert metrics["max_drawdown_percent"] == pytest.approx(0.01)


def test_profit_factor_uses_null_plus_flag_when_losses_are_zero():
    trade = {
        "symbol": "BTC",
        "exit_time": pd.Timestamp("2026-01-01", tz="UTC").isoformat(),
        "net_pnl": 5.0,
    }
    equity = pd.Series(
        [1000, 1005],
        index=pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
    )
    metrics = compute_metrics([trade], equity)
    assert metrics["profit_factor"] is None
    assert metrics["profit_factor_infinite"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_equity", float("nan")),
        ("initial_equity", float("inf")),
        ("fee_bps", float("nan")),
        ("spread_bps", float("inf")),
        ("slippage_bps", float("-inf")),
        ("daily_loss_limit", float("nan")),
        ("max_positions", float("inf")),
    ],
)
def test_config_rejects_non_finite_values(field, value):
    with pytest.raises(ValueError, match="finite"):
        _config(**{field: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"fee_bps": 10_000},
        {"spread_bps": 10_000},
        {"slippage_bps": 10_000},
        {"spread_bps": 6_000, "slippage_bps": 4_000},
    ],
)
def test_config_rejects_costs_that_can_make_fills_non_positive(overrides):
    with pytest.raises(ValueError, match="basis points|fill"):
        _config(**overrides)


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", float("nan")),
        ("quantity", float("inf")),
        ("stop_price", float("nan")),
        ("target_price", float("inf")),
        ("trailing_pct", float("nan")),
        ("max_hold_bars", float("inf")),
    ],
)
def test_trade_intent_rejects_non_finite_values(field, value):
    kwargs = {
        "symbol": "BTC",
        "time": pd.Timestamp("2026-01-01", tz="UTC"),
        "venue": "perp",
        "side": "long",
        field: value,
    }
    with pytest.raises(ValueError, match="finite"):
        TradeIntent(**kwargs)


def test_sharpe_uses_daily_mark_to_market_equity_returns():
    index = _three_day_equity_index()
    equity = pd.Series([100.0, 110.0, 99.0, 108.9], index=index)
    metrics = compute_metrics(
        [],
        equity,
        evaluation_start=pd.Timestamp("2026-01-01", tz="UTC"),
        evaluation_end=pd.Timestamp("2026-01-04", tz="UTC"),
    )
    returns = pd.Series([0.10, -0.10, 0.10])
    expected = returns.mean() / returns.std(ddof=1) * (365 ** 0.5)
    assert metrics["sharpe"] == pytest.approx(expected)
    assert metrics["sharpe_basis"] == "daily_mark_to_market_equity_returns"


def test_metrics_infer_calendar_from_mtm_equity_without_trades():
    equity = pd.Series(
        [100.0, 110.0, 99.0, 108.9],
        index=_three_day_equity_index(),
    )
    explicit = compute_metrics(
        [],
        equity,
        evaluation_start=pd.Timestamp("2026-01-01", tz="UTC"),
        evaluation_end=pd.Timestamp("2026-01-04", tz="UTC"),
    )
    inferred = compute_metrics([], equity)
    assert inferred["evaluation_calendar_days"] == 3
    assert inferred["sharpe"] == pytest.approx(explicit["sharpe"])


def test_return_correlation_uses_mtm_equity_not_realized_pnl():
    index = _three_day_equity_index()
    symbol_equity = {
        "BTC": pd.Series([100.0, 110.0, 99.0, 108.9], index=index),
        "ETH": pd.Series([100.0, 90.0, 99.0, 89.1], index=index),
    }
    trades = [
        {
            "symbol": symbol,
            "exit_time": pd.Timestamp(f"2026-01-0{day} 12:00", tz="UTC").isoformat(),
            "net_pnl": pnl,
        }
        for symbol in ("BTC", "ETH")
        for day, pnl in ((1, 1.0), (2, 2.0), (3, 3.0))
    ]
    correlations = offline_walk_forward.compute_portfolio_correlations(
        trades,
        symbol_equity,
    )
    assert correlations["daily_pnl_correlation"]["BTC"]["ETH"] == pytest.approx(1)
    assert correlations["daily_return_correlation"]["BTC"]["ETH"] == pytest.approx(-1)
    assert correlations["daily_pnl_correlation_label"] == "daily_realized_net_pnl_usd"
    assert (
        correlations["daily_return_correlation_label"]
        == "daily_mark_to_market_equity_returns"
    )


def test_rolling_splits_are_chronological_and_holdout_is_untouched():
    index = pd.date_range("2026-01-01", periods=14, freq="1h", tz="UTC")
    splits, holdout = rolling_splits(index, train_bars=6, oos_bars=2, holdout_bars=2)
    assert len(splits) == 3
    assert all(train.max() < oos.min() for train, oos in splits)
    assert all(oos.max() < holdout.min() for _, oos in splits)
    assert list(holdout) == list(index[-2:])


def test_walk_forward_trials_never_receive_oos_or_holdout_for_selection():
    one_hour = {"BTC": _bars(range(100, 114), freq="1h")}
    fifteen = {"BTC": _bars(range(100, 156), freq="15min")}
    calls = []

    def strategy(one_hour_slice, fifteen_slice, params):
        calls.append(
            {
                "candidate": params["name"],
                "one_hour_start": one_hour_slice["BTC"].index.min(),
                "one_hour_end": one_hour_slice["BTC"].index.max(),
            }
        )
        return []

    manifest = evaluate_walk_forward(
        one_hour,
        fifteen,
        candidates=[{"name": "a"}, {"name": "b"}],
        strategy=strategy,
        config=_config(),
        train_bars=6,
        oos_bars=2,
        holdout_bars=2,
        evaluation_as_of=_as_of(fifteen),
    )
    assert manifest["holdout"]["start"] == one_hour["BTC"].index[-2].isoformat()
    assert all(
        trial["evaluation_end"] < trial["oos_start"]
        for trial in manifest["trials"]
        if trial["phase"] == "train"
    )
    assert all(
        trial["evaluation_end"] < manifest["holdout"]["start"]
        for trial in manifest["trials"]
        if trial["phase"] != "holdout"
    )
    assert len(manifest["trials"]) == 9  # 2 train trials + 1 OOS per fold
    assert {trial["config_hash"] for trial in manifest["trials"]}


def test_strategy_callback_cannot_inspect_future_or_current_unclosed_values():
    one_hour = {"BTC": _bars(range(100, 114), freq="1h")}
    fifteen = {"BTC": _bars(range(100, 156), freq="15min")}
    observations = []

    def malicious(hourly, quarter, params):
        decision_time = quarter["BTC"].index[-1]
        observations.append(
            {
                "decision_time": decision_time,
                "current_close": quarter["BTC"].iloc[-1]["close"],
                "hourly_end": hourly["BTC"].index[-1],
            }
        )
        return []

    evaluate_walk_forward(
        one_hour,
        fifteen,
        candidates=[{"name": "malicious"}],
        strategy=malicious,
        config=_config(),
        train_bars=6,
        oos_bars=2,
        holdout_bars=2,
        evaluation_as_of=_as_of(fifteen),
    )
    assert len(observations) > 10
    assert all(pd.isna(row["current_close"]) for row in observations)
    assert all(
        row["hourly_end"] + pd.Timedelta(hours=1) <= row["decision_time"]
        for row in observations
    )


def test_candidate_params_are_deep_copied_for_every_callback():
    one_hour, fifteen = _full_manifest_inputs()
    candidate = {
        "name": "immutable-candidate",
        "nested": {"threshold": 1},
        "values": [1, 2],
    }
    clean = evaluate_walk_forward(
        one_hour,
        fifteen,
        candidates=[candidate],
        strategy=_empty_strategy,
        config=_config(),
        train_bars=6,
        oos_bars=2,
        holdout_bars=2,
        evaluation_as_of=_as_of(fifteen),
    )
    observed = []

    def malicious(hourly, quarter, params):
        observed.append((params["nested"]["threshold"], list(params["values"])))
        params["nested"]["threshold"] = 999
        params["values"].append(999)
        params["injected"] = True
        return []

    manifest = evaluate_walk_forward(
        one_hour,
        fifteen,
        candidates=[candidate],
        strategy=malicious,
        config=_config(),
        train_bars=6,
        oos_bars=2,
        holdout_bars=2,
        evaluation_as_of=_as_of(fifteen),
    )
    assert observed and all(value == (1, [1, 2]) for value in observed)
    assert candidate == {
        "name": "immutable-candidate",
        "nested": {"threshold": 1},
        "values": [1, 2],
    }
    assert manifest["selected_candidate"] == candidate
    assert all(trial["candidate"] == candidate for trial in manifest["trials"])
    assert manifest["research_config_hash"] == clean["research_config_hash"]


def test_walk_forward_supplies_past_warmup_history_to_oos_and_holdout():
    one_hour = {"BTC": _bars(range(100, 114), freq="1h")}
    fifteen = {"BTC": _bars(range(100, 156), freq="15min")}
    manifest = evaluate_walk_forward(
        one_hour,
        fifteen,
        candidates=[
            {
                "fast": 2,
                "slow": 3,
                "venue": "spot",
                "quantity": 1,
                "max_hold_bars": 1,
            }
        ],
        strategy=closed_bar_ma_intents,
        config=_config(max_positions=1),
        train_bars=6,
        oos_bars=2,
        holdout_bars=2,
        evaluation_as_of=_as_of(fifteen),
    )
    assert all(
        trial["metrics"]["trade_count"] > 0
        for trial in manifest["trials"]
        if trial["phase"] == "oos"
    )
    assert manifest["holdout"]["trade_count"] > 0


def test_aggregate_oos_drawdown_preserves_fold_mark_to_market_curve():
    one_hour = {"BTC": _bars([100] * 10, freq="1h")}
    quarter = _bars([100] * 40, freq="15min")
    for row in (17, 25):
        quarter.iloc[row, quarter.columns.get_loc("close")] = 50
        quarter.iloc[row, quarter.columns.get_loc("low")] = 50
    fifteen = {"BTC": quarter}

    def hold_through_slice(hourly, bars, params):
        frame = bars["BTC"]
        return [
            TradeIntent(
                "BTC",
                frame.index[-1],
                "spot",
                "long",
            )
        ]

    manifest = evaluate_walk_forward(
        one_hour,
        fifteen,
        candidates=[{"name": "intratrade-drawdown"}],
        strategy=hold_through_slice,
        config=_config(initial_equity=1000, max_positions=1),
        train_bars=4,
        oos_bars=2,
        holdout_bars=2,
        evaluation_as_of=_as_of(fifteen),
    )
    assert manifest["metrics"]["net_pnl"] == pytest.approx(0)
    assert manifest["metrics"]["max_drawdown_dollars"] == pytest.approx(50)
    assert manifest["metrics"]["max_drawdown_percent"] == pytest.approx(0.05)


@pytest.mark.parametrize(
    "bad_bars,error",
    [
        (pd.DataFrame(), "empty"),
        (_bars([100]).drop(columns=["volume"]), "volume"),
        (_bars([100]).assign(close=-1), "positive"),
    ],
)
def test_invalid_bar_inputs_fail_clearly(bad_bars, error):
    with pytest.raises(ValueError, match=error):
        prepare_closed_bars(
            bad_bars,
            "15m",
            as_of=pd.Timestamp("2026-01-02", tz="UTC"),
        )


def test_exchange_loader_uses_existing_http_path_and_fails_without_data(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"data": None, "error": "No data available"}).encode()

    def fake_urlopen(url, timeout):
        seen["url"] = url
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="No historical OHLCV"):
        load_exchange_ohlcv(
            "http://127.0.0.1:8003",
            "hyperliquid",
            "BTC",
            "1h",
            100,
        )
    assert "/api/v1/market/ohlcv/hyperliquid/BTC?" in seen["url"]


def test_cli_baseline_only_enters_after_hourly_signal_bar_is_closed():
    one_hour = {"BTC": _bars([100, 101, 102, 103], freq="1h")}
    fifteen = {"BTC": _bars([100] * 16, freq="15min")}
    intents = closed_bar_ma_intents(
        one_hour,
        fifteen,
        {
            "fast": 2,
            "slow": 3,
            "venue": "spot",
            "quantity": 1,
            "max_hold_bars": 2,
        },
    )
    assert intents
    signal_bar = one_hour["BTC"].index[2]
    assert intents[0].time == signal_bar + pd.Timedelta(hours=1)


def test_cli_baseline_uses_prior_history_without_replaying_old_signals():
    all_hourly = _bars(range(100, 106), freq="1h")
    evaluation_15m = _bars([105] * 8, start="2026-01-01 04:00", freq="15min")
    intents = closed_bar_ma_intents(
        {"BTC": all_hourly},
        {"BTC": evaluation_15m},
        {
            "fast": 2,
            "slow": 3,
            "venue": "spot",
            "quantity": 1,
            "max_hold_bars": 2,
        },
    )
    assert [intent.time for intent in intents] == [
        pd.Timestamp("2026-01-01 04:00", tz="UTC"),
        pd.Timestamp("2026-01-01 05:00", tz="UTC"),
    ]


def test_direct_evaluation_requires_explicit_as_of():
    bars = {"BTC": _bars([100, 101])}
    with pytest.raises(ValueError, match="evaluation_as_of"):
        simulate_portfolio(bars, [], _config())


def test_walk_forward_excludes_forming_bars_end_to_end():
    one_hour = {"BTC": _bars(range(100, 115), freq="1h")}
    fifteen = {"BTC": _bars(range(100, 160), freq="15min")}
    as_of = pd.Timestamp("2026-01-01 14:30", tz="UTC")
    seen = []

    def strategy(hourly, quarter, params):
        seen.append((hourly["BTC"].index.max(), quarter["BTC"].index.max()))
        return []

    manifest = evaluate_walk_forward(
        one_hour,
        fifteen,
        candidates=[{"name": "forming-bar-contract"}],
        strategy=strategy,
        config=_config(),
        train_bars=6,
        oos_bars=2,
        holdout_bars=2,
        evaluation_as_of=as_of,
    )
    assert seen
    assert all(hour_end <= pd.Timestamp("2026-01-01 13:00", tz="UTC") for hour_end, _ in seen)
    assert all(quarter_end <= pd.Timestamp("2026-01-01 14:15", tz="UTC") for _, quarter_end in seen)
    assert manifest["evaluation_as_of"] == as_of.isoformat()


def test_entry_bar_ambiguous_touch_uses_stop_first():
    bars = {"BTC": _bars([100], highs=[110], lows=[90])}
    intent = TradeIntent(
        "BTC",
        bars["BTC"].index[0],
        "spot",
        "long",
        stop_price=95,
        target_price=105,
    )
    trade = _simulate(bars, [intent], _config())["trades"][0]
    assert trade["exit_reason"] == "stop"
    assert trade["exit_price"] == 95


@pytest.mark.parametrize(
    "side,second_open,stop,expected_base",
    [
        ("long", 90.0, 95.0, 90.0),
        ("short", 110.0, 105.0, 110.0),
    ],
)
def test_gap_through_stop_fills_at_worse_open_plus_costs(
    side, second_open, stop, expected_base
):
    bars = {"BTC": _bars([100, second_open])}
    bars["BTC"].iloc[1, bars["BTC"].columns.get_loc("open")] = second_open
    intent = TradeIntent(
        "BTC",
        bars["BTC"].index[0],
        "perp",
        side,
        stop_price=stop,
    )
    trade = _simulate(
        bars,
        [intent],
        _config(spread_bps=10, slippage_bps=10),
    )["trades"][0]
    adverse = -1 if side == "long" else 1
    assert trade["exit_price"] == pytest.approx(expected_base * (1 + adverse * 0.002))
    assert trade["exit_reason"] == "stop"


def test_metrics_include_no_trade_days_and_calendar_rolling_average():
    trades = [
        {
            "symbol": "BTC",
            "exit_time": pd.Timestamp("2026-01-01 12:00", tz="UTC").isoformat(),
            "net_pnl": 30.0,
        },
        {
            "symbol": "BTC",
            "exit_time": pd.Timestamp("2026-01-31 12:00", tz="UTC").isoformat(),
            "net_pnl": 60.0,
        },
    ]
    equity = pd.Series(
        [1000, 1090],
        index=pd.to_datetime(["2026-01-01", "2026-01-31"], utc=True),
    )
    metrics = compute_metrics(
        trades,
        equity,
        evaluation_start=pd.Timestamp("2026-01-01", tz="UTC"),
        evaluation_end=pd.Timestamp("2026-02-01", tz="UTC"),
    )
    assert metrics["evaluation_calendar_days"] == 31
    assert metrics["average_daily_pnl"] == pytest.approx(90 / 31)
    assert metrics["rolling_30d_average_pnl"] == pytest.approx(2.0)

    short = compute_metrics(
        trades[:1],
        equity.iloc[:1],
        evaluation_start=pd.Timestamp("2026-01-01", tz="UTC"),
        evaluation_end=pd.Timestamp("2026-01-04", tz="UTC"),
    )
    assert short["average_daily_pnl"] == pytest.approx(10)
    assert short["rolling_30d_average_pnl"] == pytest.approx(10)


def test_promotion_gate_requires_net_fifty_dollar_daily_average():
    gates = offline_walk_forward._target_gates(
        {
            "rolling_30d_average_pnl": 49.99,
            "max_drawdown_percent": 0.01,
            "profit_factor": 1.5,
            "profit_factor_infinite": False,
            "positive_fold_ratio": 0.8,
        },
        {"net_pnl": 1.0},
    )

    assert gates["rolling_30d_average_pnl_at_least_50"] is False
    assert gates["all_passed"] is False


def test_daily_halt_uses_intraday_unrealized_equity_loss():
    btc = _bars([100, 90, 90])
    eth = _bars([50, 50, 51])
    intents = [
        TradeIntent("BTC", btc.index[0], "spot", "long"),
        TradeIntent("ETH", eth.index[1], "spot", "long"),
    ]
    result = _simulate(
        {"BTC": btc, "ETH": eth},
        intents,
        _config(daily_loss_limit=5),
    )
    assert result["rejections"] == [
        {
            "symbol": "ETH",
            "time": eth.index[1].isoformat(),
            "reason": "daily_loss_halt",
        }
    ]


def test_overlap_samples_exposure_before_same_timestamp_closures():
    bars = {"BTC": _bars([100, 101]), "ETH": _bars([50, 51])}
    intents = [
        TradeIntent("BTC", bars["BTC"].index[0], "spot", "long", max_hold_bars=1),
        TradeIntent("ETH", bars["ETH"].index[0], "spot", "long", max_hold_bars=1),
    ]
    portfolio = _simulate(bars, intents, _config())["portfolio"]
    assert portfolio["max_concurrent_positions"] == 2
    assert portfolio["exposure_observations"] == 2
    assert portfolio["overlap_observations"] == 2
    assert portfolio["overlap_fraction"] == 1.0


def test_portfolio_reports_daily_return_correlation():
    btc = _bars([100] * 98)
    eth = _bars([100] * 98)
    for frame, first, second in ((btc, 101, 102), (eth, 102, 101)):
        for row, close in ((1, first), (97, second)):
            frame.iloc[row, frame.columns.get_loc("close")] = close
            frame.iloc[row, frame.columns.get_loc("high")] = close
    intents = [
        TradeIntent(symbol, frame.index[index], "spot", "long", max_hold_bars=1)
        for symbol, frame in (("BTC", btc), ("ETH", eth))
        for index in (0, 96)
    ]
    portfolio = _simulate({"BTC": btc, "ETH": eth}, intents, _config(max_positions=4))[
        "portfolio"
    ]
    assert portfolio["daily_return_correlation"]["BTC"]["ETH"] == pytest.approx(-1)


def _full_manifest_inputs():
    return (
        {"BTC": _bars(range(100, 114), freq="1h")},
        {"BTC": _bars(range(100, 156), freq="15min")},
    )


def _empty_strategy(one_hour, fifteen, params):
    return []


def test_full_manifests_are_byte_identical_and_config_hashes_are_distinct():
    one_hour, fifteen = _full_manifest_inputs()
    kwargs = {
        "candidates": [{"name": "stable"}],
        "strategy": _empty_strategy,
        "train_bars": 6,
        "oos_bars": 2,
        "holdout_bars": 2,
        "evaluation_as_of": _as_of(fifteen),
    }
    first = evaluate_walk_forward(one_hour, fifteen, config=_config(), **kwargs)
    second = evaluate_walk_forward(one_hour, fifteen, config=_config(), **kwargs)
    changed = evaluate_walk_forward(
        one_hour,
        fifteen,
        config=_config(fee_bps=1),
        **kwargs,
    )
    assert deterministic_json_bytes(first) == deterministic_json_bytes(second)
    assert first["research_config_hash"] == second["research_config_hash"]
    assert first["research_config_hash"] != changed["research_config_hash"]
    assert first["promotion_performed"] is False
    assert first["target_gates"]["promotion_performed"] is False
    assert "overlap_observations" in first["metrics"]["portfolio"]
    assert "daily_return_correlation" in first["metrics"]["portfolio"]


def test_cli_outputs_identical_full_manifest_bytes(monkeypatch, tmp_path):
    one_hour, fifteen = _full_manifest_inputs()

    def fake_load(url, exchange, symbol, timeframe, limit, **kwargs):
        return (one_hour if timeframe == "1h" else fifteen)[symbol]

    monkeypatch.setattr(evaluator_cli, "load_exchange_ohlcv", fake_load)
    common = [
        "--exchange",
        "hyperliquid",
        "--symbols",
        "BTC",
        "--venue",
        "perp",
        "--candidate",
        "2:3",
        "--train-bars",
        "6",
        "--oos-bars",
        "2",
        "--holdout-bars",
        "2",
        "--fee-bps",
        "1",
        "--spread-bps",
        "1",
        "--slippage-bps",
        "1",
        "--as-of",
        _as_of(fifteen).isoformat(),
    ]
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    assert evaluator_cli.main([*common, "--output", str(first)]) == 0
    assert evaluator_cli.main([*common, "--output", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()


def test_cli_unavailable_data_returns_two(monkeypatch, capsys):
    monkeypatch.setattr(
        evaluator_cli,
        "load_exchange_ohlcv",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no bars")),
    )
    result = evaluator_cli.main(
        [
            "--exchange",
            "hyperliquid",
            "--symbols",
            "BTC",
            "--venue",
            "perp",
            "--candidate",
            "2:3",
            "--train-bars",
            "6",
            "--oos-bars",
            "2",
            "--holdout-bars",
            "2",
            "--fee-bps",
            "1",
            "--spread-bps",
            "1",
            "--slippage-bps",
            "1",
            "--as-of",
            "2026-01-02T00:00:00+00:00",
        ]
    )
    assert result == 2
    assert "offline walk-forward unavailable: no bars" in capsys.readouterr().err


def test_cli_rejects_incomplete_arguments():
    with pytest.raises(SystemExit) as error:
        evaluator_cli.main(["--exchange", "hyperliquid"])
    assert error.value.code == 2


@pytest.mark.parametrize("timeframe", ["1h", "15m"])
def test_gapped_crypto_inputs_fail_continuity_validation(timeframe):
    freq = "1h" if timeframe == "1h" else "15min"
    bars = _bars([100, 101, 102, 103], freq=freq).drop(
        _bars([100, 101, 102, 103], freq=freq).index[1]
    )
    with pytest.raises(ValueError, match="continuous"):
        prepare_closed_bars(
            bars,
            timeframe,
            as_of=bars.index[-1] + pd.Timedelta(hours=2),
        )


def test_walk_forward_rejects_incomplete_15m_coverage_for_hourly_window():
    one_hour = {"BTC": _bars(range(100, 114), freq="1h")}
    fifteen = {
        "BTC": _bars(
            range(100, 155),
            start="2026-01-01 00:15",
            freq="15min",
        )
    }
    with pytest.raises(ValueError, match="15m coverage"):
        evaluate_walk_forward(
            one_hour,
            fifteen,
            candidates=[{"name": "coverage"}],
            strategy=_empty_strategy,
            config=_config(),
            train_bars=6,
            oos_bars=2,
            holdout_bars=2,
            evaluation_as_of=pd.Timestamp("2026-01-01 14:00", tz="UTC"),
        )
