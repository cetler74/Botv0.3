# Short Strategy Feasibility Review

Date: 2026-05-23
Linear: COD-13

## Decision

Short trades are not safe to enable in the current live system.

The codebase is operationally spot-long: it opens with buy orders, closes with sell orders, stores one positive `position_size`, calculates PnL as long PnL in multiple closure paths, and the configured exchange clients use spot markets. A short strategy can be designed, but it must not be wired into live execution until the order lifecycle and accounting are made side-aware.

## Evidence

- `config/config.yaml` configures Binance, Bybit, and Crypto.com as `market_type: spot`.
- `services/exchange-service/main.py` sets Binance `defaultType: spot` and Bybit `defaultType: spot`; Bybit WebSocket is also configured for `wss://stream.bybit.com/v5/public/spot`.
- `services/orchestrator-service/main.py` approves entries by sending a signal with `signal: 'buy'` into `_execute_trade_entry`.
- `services/orchestrator-service/redis_order_manager.py` converts an entry signal to `side: "buy"` for buy signals. Since approved entries are buy-only, this path is buy-to-open.
- `_execute_trade_exit` in `services/orchestrator-service/main.py` explicitly documents and executes a spot market sell exit. It sizes exits from the recorded base-asset position.
- `services/order-queue-service/main.py` treats `sell` as a balance-constrained disposal of the base asset. It checks available base-asset balance before placing a sell. That cannot open a real short without margin/futures borrow support.
- Database closure and fill materialization close trades on `sell` fills and ignore `buy` fills for closure. A true short needs the opposite lifecycle: sell-to-open, buy-to-close.
- Realized PnL is long-only in the active closure paths: `(exit_price - entry_price) * position_size`, minus fees. This appears in orchestrator exit handling, centralized trade closure, Redis immediate closure, sell order tracker, and periodic fill checker.
- Some helper modules contain partial short-aware math, but they are not the authoritative live lifecycle and do not make the system short-capable.

## Main Blockers

1. No trade-side model

The trade schema and API model do not carry a reliable `position_side` on normal trades. Current behavior infers long from positive `position_size`.

2. Entry and exit sides are hard-coded by lifecycle

Current lifecycle is buy-to-open and sell-to-close. Short support needs explicit `open_side` and `close_side` derived from `position_side`.

3. Spot exchange configuration

The configured clients are spot. Spot sell orders require owned base asset. They do not create shorts. Bybit is explicitly configured as spot, despite Bybit derivatives support existing outside this path.

4. PnL, stops, trailing, and profit protection are long-biased

Current PnL and exit logic treats price rising as favorable. Short trades need lowest-price tracking, inverse stop logic, inverse trailing logic, inverse profit-protection activation, and side-aware slippage interpretation.

5. Fill processing would misclassify short orders

A sell fill is currently interpreted as an exit/closure. A short entry is also a sell fill, so enabling shorts now risks closing or corrupting trade records immediately.

6. Balance and risk management are not margin-aware

There is no durable model for margin mode, leverage, borrowed assets, liquidation risk, funding, reduce-only orders, or venue-specific futures symbols.

## Bybit-Specific Finding

Bybit should be the first venue considered for short support, but not through the current spot path.

The current Bybit integration uses spot CCXT settings and spot WebSocket endpoints. A Bybit short implementation should be a separate derivatives execution path using the correct market category, symbols, account mode, leverage, margin checks, position fetches, and reduce-only close orders. It should not reuse the current spot sell path as a short entry path.

## Recommended Implementation Plan

1. Add a side-aware trade model

Add `position_side` with `long` or `short`, plus explicit `open_side`, `close_side`, `market_type`, `leverage`, and `margin_mode` fields. Default all existing trades to `long`.

2. Add execution guards

Reject `short` signals unless the exchange and pair are explicitly configured for futures or margin. Keep spot exchanges buy-only for entries.

3. Refactor lifecycle side handling

Centralize side derivation:

- Long: open `buy`, close `sell`
- Short: open `sell`, close `buy`

All order creation, fill processing, Redis mapping, and closure logic should use this helper instead of raw signal strings.

4. Make PnL and exits side-aware

Use one PnL function everywhere:

- Long gross PnL: `(exit_price - entry_price) * size`
- Short gross PnL: `(entry_price - exit_price) * size`

Then update stop-loss, trailing stop, profit protection, slippage, MFE/MAE, and dashboards to read `position_side`.

5. Add a short-only paper simulator first

Before live Bybit derivatives orders, run short strategies in paper mode using separate accounting and compare expectancy against current spot-long recovery mode.

6. Build a Bybit derivatives pilot

Only after simulator validation, add a Bybit derivatives adapter with explicit category, reduce-only closes, leverage/margin checks, and tight max risk. Start disabled by default.

## Safe Near-Term Action

Do not enable legacy `sell` signals as standalone short entries. In the current system, `sell` means close a spot position, not open a short. Enabling that directly would create accounting and order-routing risk.

