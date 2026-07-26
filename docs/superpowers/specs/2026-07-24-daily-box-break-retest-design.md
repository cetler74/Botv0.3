# Daily Box Break + Retest (shadow-first)

Date: 2026-07-24  
Status: approved for implementation

## Goal

Add a longer-horizon playbook that trades **previous-day high/low (UTC daily box)** with **break → retest → confirmation** on 1h, gated by **4h EMA trend** and regime. Shadow-only until promotion (≥20 episodes); not on Dual SMA paper allowlist.

## Design

- Levels: `compute_daily_box()` previous completed daily bar H/L (UTC).
- Bias TF: 4h EMA20 — long only if close > EMA; short only if close < EMA.
- Entry TF: 1h closed bars — break beyond box, retest within N bars + tolerance, confirmation close back through level.
- Risk: stop beyond opposite box side (+ buffer); target ≥ min_reward_risk (default 2.5R).
- Spot: long-only wrapper. HL paper: long+short, shadow via allowlist omission + require_promotion.
- Exits: use swing exit profile (setup targets; trail/PP similar to ema20/fib swing), stagnant disabled.

## Non-goals

- Not replacing Dual SMA or weekly fib.
- Not real paper executable on day one.
