# Operator guide — strategy service

## Market regime timeframe

Regime detection (`MarketRegimeDetector.detect_regime`) runs on the **first** timeframe passed into `StrategyManager.analyze_pair`, not necessarily on the execution timeframe.

- Default call pattern uses `timeframes=['1h', '4h', '15m']` (see `services/strategy-service/main.py`).
- Therefore `**primary_timeframe` is `1h`**: ADX, RSI, Bollinger, ATR, and regime scores are computed on **1-hour** OHLCV.
- **15m and 4h** candles are still loaded for strategies that need them (MACD momentum, Heikin-Ashi hierarchy, VWMA Hull macro veto, etc.).

If you change the order of `timeframes`, you change which series drives regime classification.

## Consensus and RSI overrides (audit summary)

Weighted consensus (`_calculate_consensus`) uses regime-based **primary / secondary** weights (1.0 / 0.7) for: `heikin_ashi`, `vwma_hull`, `macd_momentum`, `multi_timeframe_confluence`, `engulfing_multi_tf`.

**Independent strategies (weight `0.0` in the weighted vote):**

- `rsi_oversold_override`
- `rsi_oversold_checklist`

They do **not** participate in the weighted BUY/SELL/HOLD tally. Instead, after the main consensus (including primary-override and SELL-veto logic), the service applies **explicit overrides**:

1. If `rsi_15m_oversold_buy_override` is set in context → consensus is forced to **BUY** and confidence/strength floors are raised.
2. If `rsi_checklist_buy_override` is set → consensus is forced to **BUY** with slightly higher floors.

**Important:** Step 4 (RSI overrides) runs **after** the SELL veto. A strong **SELL** (confidence ≥ `SELL_VETO_THRESHOLD`, default 0.60) can block a weighted **BUY**, but an RSI override flag can still force **BUY** afterward. Treat this as **aggressive** behavior; tune override enablement in `config.yaml` if you want veto to always win.

Constants (tunable in code): `PRIMARY_OVERRIDE_THRESHOLD` (0.58), `SELL_VETO_THRESHOLD` (0.60).

## VWAP in `multi_timeframe_confluence`

Two modes (parameter `vwap_mode`):


| Mode          | Meaning                                                                                                                                                                    |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rolling`     | Last `vwap_period` bars: \sum (TP \times V) / \sum V with TP = (H+L+C)/3. **Not** session VWAP.                                                                            |
| `session_utc` | Cumulative VWAP from **UTC midnight** through the current bar (resets each UTC day). Closer to typical **day-session** VWAP teaching; still bar-based (not exchange tape). |


Timezone is configurable via `vwap_session_timezone` (default `UTC`). Crypto has no single “exchange session”; UTC is a common anchor.

When `multi_timeframe_confluence` is **disabled** in config, VWAP settings have no effect until the strategy is re-enabled.

## TA parity tests

- `tests/unit/test_session_vwap.py` — rolling vs **UTC-anchored** session VWAP (`strategy/vwap_utils.py`); runs with **pandas only**.
- `tests/unit/test_ta_indicator_parity.py` — RSI/MACD vs reference formulas; **skipped** if `pandas_ta` is not installed.
- `tests/unit/test_regime_detector_synthetic.py` — synthetic OHLCV grid; **skipped** without `pandas_ta`.

From the repo root (set `PYTHONPATH` to the project root):

```bash
PYTHONPATH=. pytest tests/unit/test_session_vwap.py \
  tests/unit/test_ta_indicator_parity.py \
  tests/unit/test_regime_detector_synthetic.py -q
```

Run **`python3 -m pip install -r requirements-test.txt`** with the same **`python3` you use for pytest**. On **Homebrew 3.13**, that installs **`pandas-ta-openbb`** (provides `pandas_ta`). On **3.12**, it installs **`pandas-ta`** (matches Docker). Pytest warns once if `pandas_ta` is still missing.