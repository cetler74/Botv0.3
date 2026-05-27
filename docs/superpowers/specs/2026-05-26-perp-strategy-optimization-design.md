# Hyperliquid Perp Strategy Optimization -- Design Spec

**Date:** 2026-05-26
**Context:** Paper trading daily review showed -$12.10 realized PnL across 48 closed trades (47.9% WR, profit factor 0.43). Six actionable recommendations emerged from the analysis.

## Problem Statement

The Hyperliquid perps paper trading system has a structural win/loss asymmetry: average winner is $0.42 while average loser is $0.87 (2.1:1 ratio). The system needs a 67%+ win rate to break even at these ratios but only achieves 48%. Contributing factors include a non-performing strategy (heikin_ashi), counter-trend entries, overtrading, and uniform position sizing across all session windows.

## Changes

### Change 1: Disable Heikin Ashi Strategy

**Rationale:** 0% win rate today (-$4.91 across 4 trades). Lifetime perp short record: 0 wins on 28 closed trades (-$29.24). Already disabled on spot side with similar findings.

**Implementation:**
- Set `enabled: false` under `strategies_hyperliquid.heikin_ashi` in `config/config.yaml`
- Add a `DEPRECATED_STRATEGIES` set in `HyperliquidStrategyManager` (`services/strategy-service/hyperliquid_strategy_manager.py`) that logs a warning during `_initialize_strategies()` if a deprecated strategy is re-enabled, referencing paper trading results
- Strategy code in `strategy/hyperliquid/heikin_ashi_perp.py` remains intact for potential future revisit

**Test:** Unit test verifying disabled strategies are not loaded and deprecation warning fires when re-enabled.

### Change 2: Widen Trailing Stop Parameters

**Rationale:** Current trailing stop activates at 0.35% with 0.25% callback. Today's average winning trail exit was +0.59%. Winners are being cut at sub-1% moves while losers run to the full 1.5% stop loss.

**New parameters:**

| Parameter | Current | New |
|---|---|---|
| `activation_threshold` | 0.35% | 0.75% |
| `step_percentage` (callback) | 0.25% | 0.50% |
| `tighten_profit_threshold` | 0.35% | 1.50% |
| `tightened_step_percentage` | 0.20% | 0.30% |
| `breakeven_floor` | 0.35% | 0.50% |
| `profit_protection.activation` | 0.35% | 0.50% |

**Implementation:**
- Update `PaperPerpExitConfig` dataclass defaults in `services/orchestrator-service/hyperliquid_perps.py`
- Update corresponding values in `config/config.yaml` under `trading.hyperliquid_perps`

**Test:** Unit tests for trail arming, callback distance, tightening threshold, and breakeven floor.

### Change 3: Block Counter-Trend Entries

**Rationale:** Multiple shorts opened in trending_up regimes got squeezed (TON, TIA, ARB). Longs in trending_down regimes also lost.

**Implementation:**
- New function `hyperliquid_regime_direction_gate(signal_side, regime, confidence, strength)` in `services/orchestrator-service/hyperliquid_perps.py`
- Called during `_run_hyperliquid_strategy_entries` after standalone/specialist gates, before duplicate/balance checks

**Logic:**
```
COUNTER_TREND_BLOCKS:
  trending_up -> block short
  trending_down -> block long

Override: confidence >= 0.90 AND strength >= 0.80
  -> allow with 0.5x size multiplier and log
```

All other regimes (sideways, breakout, high_volatility, low_volatility, reversal_zone) are unaffected.

**Test:** Unit tests covering block, override, and passthrough scenarios.

### Change 4: Per-Side Standalone Gate for VWMA Hull

**Rationale:** VWMA Hull longs: +$2.16, 71% WR. Shorts: -$0.59. The short side lacks the 4h macro veto that longs have.

**Implementation:**
- Extend `hyperliquid_standalone_entry_gate()` in `services/orchestrator-service/hyperliquid_perps.py` to check for `min_confidence_{side}` before falling back to generic `min_confidence`
- Add `min_confidence_short: 0.85` under `standalone_strategy_gates.vwma_hull` in config (generic `min_confidence` stays at 0.70 for longs)

**Code pattern:**
```python
side = signal.get("side", "long")
side_key = f"min_confidence_{side}"
min_conf = gate_cfg.get(side_key, gate_cfg.get("min_confidence", default))
```

This is a general mechanism reusable by any strategy without further code changes.

**Test:** Unit tests: vwma_hull long at 0.72 passes, short at 0.72 blocked, short at 0.86 passes.

### Change 5: Reduce Overtrading

**Rationale:** 52 trades in 13 hours (4/hour). Fees consumed $6.73. Profit protection exits triggered 11 times for net -$0.21 (breakeven trades paying fees for nothing).

**Implementation -- Part A: Raise global confidence thresholds:**
- `min_confidence_long`: 0.55 -> 0.62
- `min_confidence_short`: 0.55 -> 0.62
- Updated in both `config.yaml` and the code defaults in `hyperliquid_perps.py`

**Implementation -- Part B: Per-coin re-entry cooldown:**
- New function `hyperliquid_reentry_cooldown_check(coin, side, closed_trades, cooldown_minutes)` in `hyperliquid_perps.py`
- Scans recently closed paper trades for same coin+side within `perp_reentry_cooldown_minutes` (default: 30)
- This is distinct from the existing `block_after_negative_realized_hours` (12h) which only blocks after losses; this new cooldown applies after ANY exit
- Config key: `trading.hyperliquid_perps.perp_reentry_cooldown_minutes: 30`

**Test:** Unit test for cooldown logic with time mocking; threshold filtering test.

### Change 6: Session-Aware Position Sizing

**Rationale:** London open (06-08 UTC) was profitable. US transition (10-12 UTC) was the worst window. The `session_filter` module exists but is not wired into the perp orchestrator.

**Implementation:**
- New lightweight helper `is_caution_window(utc_hour, config)` in `services/orchestrator-service/hyperliquid_perps.py` (not reusing the full session_filter schedule which is spot-specific Mon-Thu Lisbon time)
- Called in `_run_hyperliquid_strategy_entries` after all gates pass, before position sizing
- If in a caution window: multiply position size by `session_caution_multiplier` (default: 0.5)
- Config:
  ```yaml
  trading.hyperliquid_perps.session_sizing:
    enabled: true
    caution_multiplier: 0.5
    caution_windows:
      - start_utc: 10
        end_utc: 12
  ```

**Test:** Unit test verifying 0.5x during 10-12 UTC, 1.0x outside, disabled flag respected.

## File Change Summary

| File | Changes |
|---|---|
| `config/config.yaml` | Disable HA, trailing stop params, confidence thresholds, reentry cooldown, session sizing, per-side gate |
| `services/orchestrator-service/hyperliquid_perps.py` | PaperPerpExitConfig defaults, regime direction gate, per-side gate extension, reentry cooldown, session sizing helper |
| `services/orchestrator-service/main.py` | Wire new gates into `_run_hyperliquid_strategy_entries` |
| `services/strategy-service/hyperliquid_strategy_manager.py` | DEPRECATED_STRATEGIES warning |
| `tests/unit/test_hyperliquid_perps.py` | Tests for trailing stop, regime gate, per-side gate, cooldown, session sizing |
| `tests/unit/test_hyperliquid_strategy_manager.py` | Test for deprecation warning |

## Out of Scope

- Porting spot exit features to perps (stagnant loser, structural exits, regime-aware exits) -- identified as a separate, larger project
- Per-strategy trailing stop overrides -- may revisit after validating global changes
- Changes to the spot trading stack
- Modifying heikin_ashi strategy code itself
