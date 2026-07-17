# Live Strategy Audit Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the selected strategies executable in live mode while making their 1h/15m technical analysis, evidence gates, and trade-ledger behavior deterministic, auditable, and risk-constrained.

**Architecture:** The strategy service will normalize and close bars once before every strategy evaluation, then attach a bounded technical-analysis evidence payload to every actionable signal. The orchestrator will use the correct closed-trade ledger and fail closed when a blocking setup-memory read fails; it will constrain size or block using independently evaluated real and shadow evidence. A portfolio report will measure the $50/day objective from net, out-of-sample results rather than treat it as a parameter-optimization target.

**Tech Stack:** Python 3.9, pandas, PyYAML, FastAPI, HTTPX, PostgreSQL (`linkuup_db`), Docker Compose, pytest.

---

## Audit baseline and non-negotiable constraints

- Active spot strategies: `rsi_stoch_reversal_15m`, `rsi_oversold_checklist`, and `weekly_fibonacci_spot`.
- Active Hyperliquid strategies: `macd_momentum`, `rsi_oversold_checklist`, `macd_ema_vwap_scalper`, `supertrend`, `swing_hull_rsi_ema`, `rsi_oversold_override`, `pullback_long_scalping`, `vwap_bounce_scalping`, `small_size_momentum_scalp`, `rsi_stoch_reversal_15m`, `sma_reclaim_bull_flag`, `supply_demand_3step`, and `dual_sma_daytrade`.
- Do not create or alter database tables or modify existing trade records. Use the existing `trading.perp_paper_trades` and `trading.perp_live_trades` ledgers.
- Do not promote an OOS-failing candidate merely to pursue a profit target. The latest manifest reports `net_pnl=-31.73`, `profit_factor=0.89`, and a negative holdout; current promoted shadow cohort count is zero.
- Preserve the project’s 1h/15m execution contract. Strategies that intrinsically require a different timeframe remain disabled or attribution-only.

## File structure

- `strategy/hyperliquid/indicators.py` — canonical OHLCV normalization, formed-bar removal, indicator snapshots, and finite-value validation.
- `services/strategy-service/hyperliquid_strategy_manager.py` — loads the required 1h/15m windows once, gives each active strategy the same closed data, and adds TA evidence to signals.
- `strategy/hyperliquid/intraday_base_perp.py` — applies the canonical snapshot to the five intraday strategies sharing this base class.
- `strategy/hyperliquid/{macd_momentum,rsi_oversold_checklist,macd_ema_vwap_scalper,supertrend,swing_hull_rsi_ema,rsi_oversold_override,rsi_stoch_reversal_15m,sma_reclaim_bull_flag,supply_demand_3step,dual_sma_daytrade}_perp.py` — exposes the closed-bar TA values used by each strategy and rejects incomplete/non-finite inputs before emitting an entry.
- `core/setup_memory.py` — independent evidence floors and bounded outcome details.
- `services/orchestrator-service/main.py` — bounded history fetch, ledger selection, failed-fetch blocking behavior, and entry audit payload.
- `services/database-service/main.py` — validates history limits and gives paper/live list endpoints equivalent response semantics.
- `core/offline_walk_forward.py` and `scripts/evaluate_offline_walk_forward.py` — strategy-level, net-of-cost OOS evaluation plus a portfolio $50/day reporting gate.
- `config/config.yaml` — only explicit controls for history bound, evidence policy, and portfolio reporting thresholds.
- `tests/unit/test_strategy_timeframe_contract.py`, `tests/unit/test_hyperliquid_strategy_manager.py`, `tests/unit/test_hyperliquid_perp_strategies.py`, `tests/unit/test_setup_memory.py`, `tests/unit/test_hyperliquid_perps.py`, `tests/unit/test_strategy_service_market_data_limits.py`, `tests/unit/test_offline_walk_forward.py` — regression coverage.

### Task 1: Define canonical closed-bar technical-analysis evidence

**Files:**
- Modify: `strategy/hyperliquid/indicators.py`
- Modify: `services/strategy-service/hyperliquid_strategy_manager.py:61-275`
- Test: `tests/unit/test_hyperliquid_strategy_manager.py`
- Test: `tests/unit/test_strategy_timeframe_contract.py`

- [ ] **Step 1: Write failing tests for shared 1h/15m closed inputs.**

```python
@pytest.mark.asyncio
async def test_active_perp_strategy_receives_only_closed_bars(monkeypatch, manager):
    formed = _ohlcv_with_last_timestamp(datetime.now(timezone.utc))
    monkeypatch.setattr(manager.adapter, "get_ohlcv", AsyncMock(return_value=formed))

    result = await manager.analyze_symbol("BTC")

    assert result["ta_contract"]["timeframes"] == ["1h", "15m"]
    assert result["ta_contract"]["dropped_forming_bar"] is True
    assert result["strategies"]["rsi_stoch_reversal_15m"]["ta_evidence"]["bar_closed"] is True

def test_enabled_perp_strategies_have_only_1h_15m_target_timeframes(config):
    enabled = config["strategies_hyperliquid"]
    invalid = {
        name: item.get("target_timeframes", [])
        for name, item in enabled.items()
        if item.get("enabled") and not set(item.get("target_timeframes", [])) <= {"1h", "15m"}
    }
    assert invalid == {}
```

- [ ] **Step 2: Run the tests to verify failure.**

Run: `python3 -m pytest tests/unit/test_hyperliquid_strategy_manager.py tests/unit/test_strategy_timeframe_contract.py -q`

Expected: FAIL because manager output does not yet carry `ta_contract` or per-signal `ta_evidence`.

- [ ] **Step 3: Add one canonical snapshot builder.**

```python
def closed_bar_snapshot(
    frame: pd.DataFrame, *, now: datetime | None = None
) -> tuple[pd.DataFrame, dict[str, object]]:
    normalized = ohlcv_dict_to_df(frame).sort_index()
    closed = prepare_closed_ohlcv(normalized, now=now)
    if len(closed) < 2 or not closed.index.is_monotonic_increasing:
        raise ValueError("insufficient closed OHLCV bars")
    if not np.isfinite(closed[["open", "high", "low", "close", "volume"]].to_numpy()).all():
        raise ValueError("non-finite OHLCV input")
    return closed, {
        "bar_closed": True,
        "bar_time": closed.index[-1].isoformat(),
        "bar_count": len(closed),
        "dropped_forming_bar": len(closed) != len(normalized),
    }
```

Make the manager request only each configured 1h/15m window once per coin/cycle, replace raw frames with this closed snapshot, and attach the snapshot metadata to the returned strategy payload. Do not calculate different indicator values in the manager.

- [ ] **Step 4: Run the focused tests.**

Run: `python3 -m pytest tests/unit/test_hyperliquid_strategy_manager.py tests/unit/test_strategy_timeframe_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated change.**

```bash
git add strategy/hyperliquid/indicators.py services/strategy-service/hyperliquid_strategy_manager.py tests/unit/test_hyperliquid_strategy_manager.py tests/unit/test_strategy_timeframe_contract.py
git commit -m "fix: standardize closed bars for perp signals"
```

### Task 2: Make every enabled strategy publish its actual TA inputs

**Files:**
- Modify: `strategy/hyperliquid/intraday_base_perp.py:309-430`
- Modify: `strategy/hyperliquid/macd_momentum_perp.py:220-410`
- Modify: `strategy/hyperliquid/rsi_oversold_checklist_perp.py:174-350`
- Modify: `strategy/hyperliquid/macd_ema_vwap_scalper_perp.py:141-330`
- Modify: `strategy/hyperliquid/supertrend_perp.py:331-540`
- Modify: `strategy/hyperliquid/swing_hull_rsi_ema_perp.py:244-470`
- Modify: `strategy/hyperliquid/rsi_oversold_override_perp.py:94-270`
- Modify: `strategy/hyperliquid/rsi_stoch_reversal_15m_perp.py`
- Modify: `strategy/hyperliquid/sma_reclaim_bull_flag_perp.py:55-290`
- Modify: `strategy/hyperliquid/supply_demand_3step_perp.py:55-270`
- Modify: `strategy/hyperliquid/dual_sma_daytrade_perp.py:53-280`
- Test: `tests/unit/test_hyperliquid_perp_strategies.py`

- [ ] **Step 1: Write parameterized failing tests for TA evidence and warm-up refusal.**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_name", ENABLED_PERP_STRATEGIES)
async def test_enabled_perp_entry_has_finite_closed_bar_ta_evidence(strategy_name, strategy_factory):
    strategy = strategy_factory(strategy_name)
    signal, confidence, strength = await strategy.generate_signal(_valid_market_data())

    evidence = strategy.last_ta_evidence
    assert evidence["bar_closed"] is True
    assert evidence["timeframes"] == ["1h", "15m"]
    assert all(math.isfinite(value) for value in evidence["inputs"].values())
    assert signal in {"buy", "sell", "hold"}
    assert 0.0 <= confidence <= 1.0 and 0.0 <= strength <= 1.0

@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_name", ENABLED_PERP_STRATEGIES)
async def test_enabled_perp_strategy_holds_on_insufficient_warmup(strategy_name, strategy_factory):
    signal, _, _ = await strategy_factory(strategy_name).generate_signal(_short_market_data())
    assert signal == "hold"
```

- [ ] **Step 2: Run tests to verify failure.**

Run: `python3 -m pytest tests/unit/test_hyperliquid_perp_strategies.py -q`

Expected: FAIL because enabled strategies do not consistently expose `last_ta_evidence`.

- [ ] **Step 3: Add the shared evidence contract and strategy snapshots.**

```python
def _set_ta_evidence(self, *, inputs: Mapping[str, float], timeframes: Sequence[str]) -> None:
    values = {key: float(value) for key, value in inputs.items()}
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("non-finite technical-analysis input")
    self.last_ta_evidence = {
        "bar_closed": True,
        "timeframes": list(timeframes),
        "inputs": values,
    }
```

In every enabled strategy, call `_set_ta_evidence` immediately after calculating values and before entry gates:

```python
# MACD momentum
self._set_ta_evidence(
    timeframes=("1h", "15m"),
    inputs={"macd_1h": macd_1h, "signal_1h": signal_1h,
            "hist_15m": hist_15m, "rsi_15m": rsi_15m,
            "ema_15m": ema_15m, "volume_ratio_15m": volume_ratio},
)
```

Use strategy-specific inputs rather than synthetic common values: RSI/Stoch `%K/%D` for reversal, VWAP/EMA/MACD/ATR for scalpers, SuperTrend line/direction/ATR, Hull/RSI/EMA, supply-zone bounds/ATR, SMA20/SMA200/slope, and bull-flag SMA/RSI/volume-profile values. Return `("hold", 0.0, 0.0)` when the required lookback has not produced finite values. Do not change entry thresholds in this task.

- [ ] **Step 4: Run tests to verify pass.**

Run: `python3 -m pytest tests/unit/test_hyperliquid_perp_strategies.py tests/unit/test_hyperliquid_strategy_manager.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated change.**

```bash
git add strategy/hyperliquid tests/unit/test_hyperliquid_perp_strategies.py
git commit -m "feat: record closed-bar perp indicator evidence"
```

### Task 3: Finish setup-memory and ledger-path correctness

**Files:**
- Modify: `core/setup_memory.py:324-500`
- Modify: `services/orchestrator-service/main.py:7600-7750,9000-9050,9950-10035,13740-13845`
- Modify: `services/database-service/main.py:2698-2755,3225-3288`
- Modify: `config/config.yaml:2585-2611`
- Test: `tests/unit/test_setup_memory.py`
- Test: `tests/unit/test_hyperliquid_perps.py`
- Test: `tests/unit/test_strategy_service_market_data_limits.py`

- [ ] **Step 1: Write failing tests for independent evidence floors, bounded requests, failed reads, and live ledger selection.**

```python
def test_setup_memory_mixed_real_and_shadow_cannot_bypass_shadow_floor():
    decision = evaluate_setup_memory(
        _signal(), _cfg(min_real_samples=2, min_shadow_episodes=3),
        real_closed_trades=_losing_real_rows(2),
        shadow_closed_trades=_winning_shadow_episodes(2),
    )
    assert decision.action == "block"
    assert decision.evidence["shadow_episode_count"] == 2

@pytest.mark.asyncio
async def test_live_setup_memory_reads_live_closed_ledger_and_bounds_limit(orchestrator, httpx_mock):
    httpx_mock.add_response(url=re.compile(r"/perps/live-trades\?.*limit=1000"), json={"trades": []})
    await orchestrator._load_perp_setup_memory_trades(live_execution=True)
    assert not httpx_mock.get_requests()[0].url.query.decode().endswith("limit=1001")

@pytest.mark.asyncio
async def test_blocking_setup_memory_fetch_error_blocks_live_entry(orchestrator, httpx_mock):
    httpx_mock.add_response(status_code=503, url=re.compile(r"/perps/live-trades"))
    decision = await orchestrator._evaluate_perp_setup_memory(_signal(), live_execution=True)
    assert decision.action == "block"
    assert decision.evidence["history_fetch_status"] == "failed"
```

- [ ] **Step 2: Run tests to verify failure.**

Run: `python3 -m pytest tests/unit/test_setup_memory.py tests/unit/test_hyperliquid_perps.py tests/unit/test_strategy_service_market_data_limits.py -q`

Expected: FAIL until all error and ledger paths provide the specified result.

- [ ] **Step 3: Implement fail-closed history behavior and preserve bounded evidence.**

```python
limit = min(1000, max(1, int(perps_cfg.get("closed_trade_fetch_limit", 1000))))
ledger_path = "/api/v1/perps/live-trades" if live_execution else "/api/v1/perps/paper-trades"
response = await client.get(f"{database_url}{ledger_path}", params={"status": "CLOSED", "limit": limit})
if response.is_error:
    return SetupMemoryDecision.blocked(
        "setup-memory history unavailable",
        evidence={"history_fetch_status": "failed", "ledger": ledger_path, "limit": limit},
    )
```

In `evaluate_setup_memory`, exclude timestamp-less rows when a lookback is active, collapse shadows through `independent_closed_episode_rows`, and require real and shadow cohorts to independently meet their respective floors before either can influence an allow/size-down decision. Keep `SetupMemoryDecision.to_dict()` bounded by truncating matched outcome samples and reason lists; always include source counts, episode count, match level, and fetch status.

Make the database-service paper and live list endpoints both reject limits above 1000 using the same FastAPI validation and return `{"trades": [...], "total": N}`. Set both configured closed-trade limits to `1000`; run `./scripts/apply_config.sh` after the configuration edit.

- [ ] **Step 4: Run targeted tests.**

Run: `python3 -m pytest tests/unit/test_setup_memory.py tests/unit/test_hyperliquid_perps.py tests/unit/test_strategy_service_market_data_limits.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated change.**

```bash
git add core/setup_memory.py services/orchestrator-service/main.py services/database-service/main.py config/config.yaml tests/unit/test_setup_memory.py tests/unit/test_hyperliquid_perps.py tests/unit/test_strategy_service_market_data_limits.py
git commit -m "fix: fail closed on unavailable live setup evidence"
```

### Task 4: Make strategy evidence visible at the execution boundary

**Files:**
- Modify: `services/strategy-service/hyperliquid_strategy_manager.py:250-285`
- Modify: `services/orchestrator-service/main.py:9950-10610`
- Modify: `services/orchestrator-service/redis_order_manager.py:450-480`
- Test: `tests/unit/test_strategy_evidence_contract.py`
- Test: `tests/unit/test_strategy_evidence_review_fixes.py`

- [ ] **Step 1: Write failing execution-payload tests.**

```python
def test_actionable_perp_signal_carries_closed_bar_ta_and_memory_evidence():
    order = _build_order_from_signal(_actionable_signal())
    assert order["metadata"]["ta_evidence"]["bar_closed"] is True
    assert order["metadata"]["setup_memory"]["history_fetch_status"] == "ok"
    assert len(order["metadata"]["ta_evidence"]["inputs"]) > 0

def test_unavailable_blocking_history_never_reaches_order_submission():
    result = _submit_with_memory_decision(SetupMemoryDecision.blocked("history unavailable"))
    assert result["submitted"] is False
    assert result["reason_code"] == "setup_memory"
```

- [ ] **Step 2: Run tests to verify failure.**

Run: `python3 -m pytest tests/unit/test_strategy_evidence_contract.py tests/unit/test_strategy_evidence_review_fixes.py -q`

Expected: FAIL because the new TA payload is not preserved through the order manager.

- [ ] **Step 3: Preserve bounded evidence without adding storage.**

```python
signal["ta_evidence"] = {
    "bar_closed": bool(evidence.get("bar_closed")),
    "timeframes": list(evidence.get("timeframes", ()))[:2],
    "inputs": dict(list((evidence.get("inputs") or {}).items())[:16]),
}
signal["setup_memory"] = setup_memory_decision.to_dict()
```

Copy these fields into the existing order metadata/audit event only when an order is eligible for submission. Log a compact one-line summary containing strategy, side, closed bar time, and setup-memory action. Never write a new database record for audit telemetry in this task.

- [ ] **Step 4: Run tests to verify pass.**

Run: `python3 -m pytest tests/unit/test_strategy_evidence_contract.py tests/unit/test_strategy_evidence_review_fixes.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated change.**

```bash
git add services/strategy-service/hyperliquid_strategy_manager.py services/orchestrator-service/main.py services/orchestrator-service/redis_order_manager.py tests/unit/test_strategy_evidence_contract.py tests/unit/test_strategy_evidence_review_fixes.py
git commit -m "feat: retain perp signal evidence through execution"
```

### Task 5: Implement a measured $50/day promotion report

**Files:**
- Modify: `core/offline_walk_forward.py`
- Modify: `scripts/evaluate_offline_walk_forward.py`
- Modify: `config/config.yaml`
- Test: `tests/unit/test_offline_walk_forward.py`

- [ ] **Step 1: Write failing tests for portfolio promotion gates.**

```python
def test_daily_profit_goal_is_a_promotion_gate_not_a_signal_multiplier():
    report = evaluate_walk_forward(_bars(), _candidates(), target_daily_pnl=50.0)
    assert report["target_gates"]["rolling_30d_average_pnl_at_least_50"] is False
    assert report["promotion_performed"] is False
    assert report["selected_candidate"]["quantity"] == _candidates()[0]["quantity"]

def test_candidate_is_not_approved_when_holdout_is_negative():
    report = evaluate_walk_forward(_losing_holdout_bars(), _candidates(), target_daily_pnl=50.0)
    assert report["selection_decision"] == "reject_portfolio"
    assert report["approved_strategies"] == []
```

- [ ] **Step 2: Run tests to verify failure.**

Run: `python3 -m pytest tests/unit/test_offline_walk_forward.py -q`

Expected: FAIL until the daily target is explicitly represented in the output gate.

- [ ] **Step 3: Add explicit, net-of-cost promotion gates.**

```python
target_gates = {
    "rolling_30d_average_pnl_at_least_50": metrics["rolling_30d_average_pnl"] >= 50.0,
    "profit_factor_at_least_1_25": metrics["profit_factor"] >= 1.25,
    "positive_fold_ratio_at_least_70_percent": metrics["positive_fold_ratio"] >= 0.70,
    "positive_holdout": holdout["net_pnl"] > 0,
    "max_drawdown_percent_at_most_5": metrics["max_drawdown_percent"] <= 0.05,
}
promotion_performed = all(target_gates.values())
```

Use the configured fee, spread, and slippage assumptions in every fold and holdout. Generate a manifest that separates `approved_strategies`, `candidate_strategy_status`, rejected reasons, calendar-day count, and source timeframes. Do not change live quantities, enable flags, or thresholds from evaluation output; promotion remains an explicit human-reviewed configuration action.

- [ ] **Step 4: Run focused tests and evaluator.**

Run: `python3 -m pytest tests/unit/test_offline_walk_forward.py -q && python3 scripts/evaluate_offline_walk_forward.py --output analysis_outputs/strategy_portfolio_validation_manifest.json`

Expected: PASS; the manifest reports whether the $50/day gate passes without modifying live configuration.

- [ ] **Step 5: Commit the isolated change.**

```bash
git add core/offline_walk_forward.py scripts/evaluate_offline_walk_forward.py config/config.yaml tests/unit/test_offline_walk_forward.py analysis_outputs/strategy_portfolio_validation_manifest.json
git commit -m "feat: gate strategy promotion on net daily performance"
```

### Task 6: Validate and deploy safely to the current stack

**Files:**
- Modify: none unless a prior verification identifies a deterministic defect.
- Test: all files listed in Tasks 1–5.

- [ ] **Step 1: Run the complete relevant test suite and static checks.**

Run:

```bash
python3 -m pytest \
  tests/unit/test_hyperliquid_strategy_manager.py \
  tests/unit/test_strategy_timeframe_contract.py \
  tests/unit/test_hyperliquid_perp_strategies.py \
  tests/unit/test_setup_memory.py \
  tests/unit/test_hyperliquid_perps.py \
  tests/unit/test_strategy_service_market_data_limits.py \
  tests/unit/test_strategy_evidence_contract.py \
  tests/unit/test_strategy_evidence_review_fixes.py \
  tests/unit/test_offline_walk_forward.py -q && \
python3 -m py_compile \
  core/setup_memory.py \
  core/offline_walk_forward.py \
  services/orchestrator-service/main.py \
  services/database-service/main.py \
  services/strategy-service/hyperliquid_strategy_manager.py && \
git diff --check
```

Expected: all tests pass, compilation exits 0, and no whitespace errors.

- [ ] **Step 2: Apply configuration and recreate changed services.**

Run:

```bash
./scripts/apply_config.sh && \
docker compose up -d --build database-service strategy-service orchestrator-service
```

Expected: config-service becomes healthy, strategy/orchestrator caches restart, and the changed images run.

- [ ] **Step 3: Run live-read verification without placing orders.**

Run:

```bash
python3 scripts/audit_hl_perp_guardrails.py --limit 1000 && \
docker compose logs --since 10m strategy-service orchestrator-service database-service | \
rg "setup_memory|ta_evidence|limit=1000|422|history unavailable"
```

Expected: history requests use `limit=1000` or less; no new 422 request appears; each actionable signal log has a closed-bar TA summary; failed history reads produce a block in blocking mode; live execution reads `/api/v1/perps/live-trades`, while paper execution reads `/api/v1/perps/paper-trades`.

- [ ] **Step 4: Compare live configuration to the manifest.**

Run:

```bash
curl -fsS http://127.0.0.1:8001/api/v1/config/trading | \
python3 -c 'import json,sys; c=json.load(sys.stdin); print(json.dumps(c["trading"]["setup_memory"], indent=2, sort_keys=True))' && \
python3 -m json.tool analysis_outputs/strategy_portfolio_validation_manifest.json
```

Expected: setup-memory limits are at most 1000, evidence modes match configuration, and no rejected candidate is shown as approved.

- [ ] **Step 5: Commit deployment-safe source changes only.**

```bash
git status --short
git add config/config.yaml core services strategy scripts tests analysis_outputs
git commit -m "fix: constrain live strategy execution with verified evidence"
```

Do not commit `.env`, runtime logs, Docker volumes, or generated artifacts outside the explicit validation manifest.

## Plan self-review

- Coverage: Tasks 1–2 cover active 1h/15m TA calculation and strategy wiring; Task 3 covers the identified setup-memory, API-bound, and paper/live ledger defects; Task 4 retains evidence at order submission; Task 5 makes the requested $50/day objective measurable without guaranteeing it; Task 6 validates and rolls out.
- Scope: no new tables, no record mutation, and no strategy promotion from this work.
- Consistency: the same `ta_evidence`, `SetupMemoryDecision`, 1,000-row bound, and 1h/15m contract are used across tests, strategy service, orchestration, and deployment.
