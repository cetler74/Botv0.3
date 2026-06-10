# Adaptive PnL Control Progress

Goal: replace static PnL reporting with a live paper-perp control loop that explains and records applied changes.

Reporting contract:

> Due to `<detected situation>`, config `<config path>` was updated from `<old value>` to `<new value>` and is live since `<timestamp>`. Current outcome since the change is `<outcome>`.

## Task List

- [x] Confirm metric sources and decision evidence for paper perps: closed trades, fees, gross/net PnL, exit reason, strategy, side, regime, and accounting exclusions.
- [x] Implement the first version of the adaptive PnL controller that rebuilds runtime overlays each paper-perp cycle from rolling trade evidence.
- [x] Add decision records in the exact audit format: detected situation, config changed, old value, new value, live-since timestamp, evidence window, and intended effect.
- [x] Make the controller adaptive in both directions: reduce/block weak slices and scale/unblock improved slices.
- [x] Wire adaptive entry sizing into strategy entries, including bounded scale-up when fee-adjusted edge supports larger sizing.
- [x] Wire adaptive exit tuning into paper-perp exit logic, including faster loss control and trailing/TP profile adjustments where supported.
- [x] Expose current adaptive-control state through an orchestrator API endpoint.
- [x] Proxy adaptive-control state into the web-dashboard portfolio-intelligence payload.
- [x] Add a `/portfolio-dashboard` webpage panel showing live applied changes, reason, evidence, live-since time, and current outcome.
- [x] Move persistent adaptive decision history to Postgres (`trading.hyperliquid_adaptive_pnl_decisions`) so the webpage reads durable records from the database, not a JSON audit file.
- [x] Add a live Docker apply/reload workflow for adaptive config changes, with API/webpage proof that the running containers loaded the new values.
- [x] Add downloadable timestamped snapshots from the webpage to crystallize the current changes and outcomes.
- [x] Add tests for reduce, block, scale-up, unblock, and exit-profile adaptation behavior.
- [x] Add focused database-service, orchestrator, and dashboard tests for Postgres-backed decision history.
- [x] Run syntax checks and focused unit tests for the current controller and dashboard integration.
- [x] Restart or run services locally and verify the webpage/API shows the adaptive changes correctly.
- [x] Fix durable decision release after restart/disable/improved evidence by syncing the full active decision-key set and releasing stale active rows that are no longer present.
- [x] Fix manual `/reevaluate` status so decisions are recorded as `pending_cycle` until an actual trading cycle applies the runtime overlay.
- [x] Fix the adaptive apply script so it no longer passes the large dashboard payload through an environment variable.
- [x] Add short-window adaptive sizing for recent deterioration: a strategy side can be reduced from the last 6h window even if the 168h window has not turned negative yet.
- [x] Add a 12h minimum hold for recent-loss reductions so they do not release merely because losing trades aged out of the short window without positive recovery evidence.
- [x] Lower adaptive scale-up sample requirement from 30 to 10 closed trades so strong fee-adjusted winners can receive capital sooner.
- [x] Add adaptive max-hold tightening for `rsi_stoch_reversal_5m` when loss-drag exit tuning is active, reducing fee-only flat holds from 240 minutes to a 180 minute soft cap and 240 minute hard cap.
- [x] Observe live/paper outcomes after changes and tune thresholds only when enough post-change evidence exists. Latest review found all active adaptive changes applied live on orchestrator cycle 2, so future tuning should use outcomes after `2026-06-06T15:57:48Z`.

## Implementation Notes

- Current scope is Hyperliquid paper perpetuals only.
- The runtime overlay must not rewrite `config/config.yaml`.
- Runtime overlays must only be marked `live` after they are applied to the in-memory orchestrator config for an actual paper-perp cycle. Manual reevaluation records use `pending_cycle` until the next cycle applies them.
- Adaptive threshold/config edits in `config/config.yaml` are not live until the Docker apply/reload workflow rebuilds/recreates baked-config services, reloads config-service, restarts cached consumers, and verifies the loaded adaptive values from running containers.
- The webpage report must show apply/reload status such as `pending reload`, `applied live`, or `apply failed`, not only the decision reason.
- Blocks and reductions are reversible: when the rolling evidence no longer triggers the condition, the overlay disappears automatically.
- Persistent audit history is required because the webpage must show what changed over time, not just the current dashboard state.
- Durable decision records must be stored in Postgres in the `trading` schema. A dedicated table, `trading.hyperliquid_adaptive_pnl_decisions`, has been approved.
- Database naming note: the project rule references `linkuup_db`, but the live Docker stack currently exposes `trading_bot_futures` only and services are configured to use it. The migration was applied to the running configured database.
- The JSON audit path (`HL_ADAPTIVE_PNL_AUDIT_PATH`) is now only a runtime/migration fallback. Durable records are synced to Postgres and the webpage prefers `decisionHistory.source = postgres`.
- Live verification after restart: database-service returned 12 durable records from `trading.hyperliquid_adaptive_pnl_decisions` (6 active `applied_live`, 6 released superseded rows); `/api/v1/perps/adaptive-pnl-control` and `/api/v1/dashboard/portfolio-intelligence` both reported `historySource = postgres`, active control, and 6 active runtime decisions.
- Apply/reload workflow: `./scripts/apply_adaptive_pnl_config.sh` rebuilds/reloads config, waits for services, forces adaptive reevaluation, and verifies config-service, orchestrator, and dashboard live payloads. The dashboard payload is fetched inside the web-dashboard container to avoid OS environment-size failures.
- Live verification on 2026-06-06 15:57:48 UTC: active decisions were `applied_live` on orchestrator cycle 2. Active keys were `reduce_recent_strategy_side:rsi_stoch_reversal_5m:long`, `scale_up_strategy_side:rsi_stoch_reversal_5m:short`, `tighten_loss_and_trailing_exits:rsi_stoch_reversal_5m`, `reduce_strategy_side:rsi_stoch_reversal_1m:long`, `reduce_strategy_side:vwma_hull:short`, and regime-side blocks for `high_volatility:short`, `reversal_zone:long`, and `trending_up:long`.
- Live verification on 2026-06-06 19:05:01 UTC: `recent_release_hold_hours = 12` was live in config-service and the orchestrator adaptive endpoint. Active decisions were `applied_live` and Postgres-backed.
- PnL diagnosis on 2026-06-06: the last 24h stayed negative because wins were too small relative to losses and fees. The dashboard/API showed 17 closed trades, `-$3.46` realized, `$1.26` gross before fees, `$4.72` fees, 52.9% win rate, average win `$0.74`, average loss `-$1.27`, and profit factor `0.66`.
