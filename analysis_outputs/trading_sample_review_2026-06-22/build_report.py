from __future__ import annotations

import csv
import io
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
DB = [
    "docker", "exec", "trading-bot-postgres", "psql",
    "-U", "carloslarramba", "-d", "trading_bot_futures",
    "-v", "ON_ERROR_STOP=1", "-A", "-F", ",", "-P", "footer=off",
]
SHADOW_START = pd.Timestamp("2026-06-20 08:14:02+00:00")


def query_df(sql: str) -> pd.DataFrame:
    proc = subprocess.run(DB + ["-c", sql], check=True, capture_output=True, text=True)
    return pd.read_csv(io.StringIO(proc.stdout), quoting=csv.QUOTE_MINIMAL)


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return math.nan, math.nan
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def metrics(df: pd.DataFrame) -> dict[str, float]:
    pnl = df["pnl"].astype(float)
    pos = pnl[pnl > 0].sum()
    neg = -pnl[pnl < 0].sum()
    wins = int((pnl > 0).sum())
    lo, hi = wilson(wins, len(pnl))
    return {
        "n": len(pnl), "wins": wins, "pnl": pnl.sum(), "avg": pnl.mean(),
        "win": wins / len(pnl) if len(pnl) else math.nan,
        "win_lo": lo, "win_hi": hi, "pf": pos / neg if neg else math.inf,
    }


def bootstrap_mean_ci(values: pd.Series, samples: int = 20000) -> tuple[float, float]:
    arr = values.astype(float).to_numpy()
    rng = np.random.default_rng(20260622)
    means = rng.choice(arr, size=(samples, len(arr)), replace=True).mean(axis=1)
    return tuple(np.quantile(means, [0.025, 0.975]))


def independent_episodes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    df = df.sort_values(["strategy", "instrument", "side", "entry_time"])
    episode_ids: list[int] = []
    for _, group in df.groupby(["strategy", "instrument", "side"], sort=False):
        episode, running_exit = 0, None
        for row in group.itertuples():
            if running_exit is None or row.entry_time > running_exit:
                episode += 1
                running_exit = row.exit_time
            else:
                running_exit = max(running_exit, row.exit_time)
            episode_ids.append(episode)
    df["episode"] = episode_ids
    return df.groupby(["strategy", "instrument", "side", "episode"], sort=False).first().reset_index()


def chart_theme() -> None:
    sns.set_theme(style="whitegrid", rc={
        "figure.facecolor": "#FCFCFD", "axes.facecolor": "#FFFFFF",
        "axes.edgecolor": "#D7DBE7", "axes.labelcolor": "#1F2430",
        "grid.color": "#E6E8F0", "font.family": "sans-serif",
        "axes.spines.top": False, "axes.spines.right": False,
    })


def header(fig, ax, title: str, subtitle: str) -> None:
    ax.set_title("")
    fig.subplots_adjust(top=0.79, left=0.25, right=0.97, bottom=0.13)
    fig.text(0.25, 0.97, title, ha="left", va="top", fontsize=14,
             fontweight="semibold", color="#1F2430")
    fig.text(0.25, 0.91, subtitle, ha="left", va="top", fontsize=9,
             color="#6F768A")
    sns.despine(ax=ax)


shadow = query_df("""
COPY (
  SELECT trade_id, source_strategy AS strategy, coin AS instrument,
         position_side AS side, entry_time, exit_time, realized_pnl AS pnl,
         fees, metadata->>'market_regime' AS regime
  FROM trading.perp_paper_trades
  WHERE metadata->>'shadow_trade'='true'
    AND metadata->>'shadow_version'='2' AND status='CLOSED'
) TO STDOUT WITH CSV HEADER
""")
perps = query_df("""
COPY (
  SELECT trade_id, source_strategy AS strategy, coin AS instrument,
         position_side AS side, entry_time, exit_time, realized_pnl AS pnl, fees
  FROM trading.perp_paper_trades
  WHERE COALESCE(metadata->>'accounting_excluded','false')<>'true'
    AND status='CLOSED' AND exit_time >= '2026-06-20 08:14:02+00'
) TO STDOUT WITH CSV HEADER
""")
spot = query_df("""
COPY (
  SELECT trade_id, strategy, pair AS instrument, 'long' AS side,
         entry_time, exit_time, realized_pnl AS pnl, fees,
         (regexp_match(entry_reason,'stable_regime=([^,\\]]+)'))[1] AS regime,
         exchange, exit_reason
  FROM trading.trades
  WHERE status='CLOSED' AND strategy='supply_demand_3step'
) TO STDOUT WITH CSV HEADER
""")

for frame in (shadow, perps, spot):
    frame["pnl"] = pd.to_numeric(frame["pnl"])

episodes = independent_episodes(shadow)
supply_shadow_raw = shadow[shadow.strategy == "supply_demand_3step"]
supply_shadow_ep = episodes[episodes.strategy == "supply_demand_3step"]
supply_perps = perps[perps.strategy == "supply_demand_3step"]

raw_by = shadow.groupby("strategy").size().rename("raw")
ep_by = episodes.groupby("strategy").size().rename("episodes")
counts = pd.concat([raw_by, ep_by], axis=1).fillna(0).astype(int).reset_index()
counts.to_csv(ROOT / "shadow_sample_counts.csv", index=False)

cohort_rows = []
for name, frame in [
    ("Shadow rows (invalid unit)", supply_shadow_raw),
    ("Shadow episodes", supply_shadow_ep),
    ("Executable perps", supply_perps),
    ("Spot closes", spot),
]:
    m = metrics(frame)
    lo, hi = bootstrap_mean_ci(frame.pnl)
    cohort_rows.append({"cohort": name, **m, "mean_ci_lo": lo, "mean_ci_hi": hi,
                        "instruments": frame.instrument.nunique()})
cohorts = pd.DataFrame(cohort_rows)
cohorts.to_csv(ROOT / "supply_demand_cohorts.csv", index=False)

spot_regime = spot.groupby("regime", dropna=False).agg(
    n=("pnl", "size"), pnl=("pnl", "sum"), avg=("pnl", "mean"),
    wins=("pnl", lambda s: int((s > 0).sum())),
).reset_index()
spot_regime["regime"] = spot_regime.regime.fillna("unknown")
spot_regime.to_csv(ROOT / "spot_supply_by_regime.csv", index=False)

chart_theme()
ordered = counts.sort_values("raw", ascending=True)
long = ordered.melt(id_vars="strategy", value_vars=["raw", "episodes"],
                    var_name="unit", value_name="count")
fig, ax = plt.subplots(figsize=(10.5, 6.6))
sns.barplot(data=long, y="strategy", x="count", hue="unit", orient="h", ax=ax,
            palette={"raw": "#A3BEFA", "episodes": "#F0986E"}, edgecolor="#464C55")
header(fig, ax, "Shadow rows versus independent episodes",
       "Closed shadow-v2 records, June 20–22, 2026 UTC; overlapping strategy/coin/side positions collapse into one episode")
ax.set_xlabel("Count"); ax.set_ylabel(""); ax.legend(title="", loc="lower right")
fig.savefig(ASSETS / "shadow_rows_vs_episodes.png", dpi=180, bbox_inches="tight")
plt.close(fig)

plot_cohorts = cohorts.copy().iloc[::-1]
fig, ax = plt.subplots(figsize=(10.5, 4.9))
colors = ["#A3BEFA" if v >= 0 else "#F0986E" for v in plot_cohorts.pnl]
sns.barplot(data=plot_cohorts, y="cohort", x="pnl", orient="h", ax=ax,
            hue="cohort", palette=dict(zip(plot_cohorts.cohort, colors)), legend=False,
            edgecolor="#464C55")
header(fig, ax, "Supply-demand net PnL by evidence cohort",
       "Paper PnL in USD; cohorts have different horizons and are not additive. Raw shadow rows are shown only to expose duplication bias")
ax.axvline(0, color="#464C55", linewidth=1)
ax.set_xlabel("Net PnL (USD)"); ax.set_ylabel("")
for patch, row in zip(ax.patches, plot_cohorts.itertuples()):
    x = patch.get_width()
    label_x = x + 2 if x >= 0 else x / 2
    ax.text(label_x, patch.get_y() + patch.get_height()/2,
            f"${x:,.1f} · n={row.n}", va="center", ha="left" if x >= 0 else "center",
            fontsize=9, color="#1F2430")
fig.savefig(ASSETS / "supply_demand_cohorts.png", dpi=180, bbox_inches="tight")
plt.close(fig)

reg = spot_regime.sort_values("pnl", ascending=True)
fig, ax = plt.subplots(figsize=(10.5, 4.7))
palette = {r.regime: ("#A3D576" if r.pnl >= 0 else "#F0986E") for r in reg.itertuples()}
sns.barplot(data=reg, y="regime", x="pnl", hue="regime", palette=palette,
            legend=False, orient="h", edgecolor="#464C55", ax=ax)
header(fig, ax, "Spot supply-demand results by market regime",
       "All 56 closed spot trades, June 7–21, 2026 UTC; net realized PnL in USD")
ax.axvline(0, color="#464C55", linewidth=1)
ax.set_xlabel("Net PnL (USD)"); ax.set_ylabel("")
for patch, row in zip(ax.patches, reg.itertuples()):
    x = patch.get_width()
    label_x = x + 1 if x >= 0 else x / 2
    ax.text(label_x, patch.get_y() + patch.get_height()/2,
            f"${x:,.1f} · n={row.n}", va="center", ha="left" if x >= 0 else "center", fontsize=9)
fig.savefig(ASSETS / "spot_supply_regime.png", dpi=180, bbox_inches="tight")
plt.close(fig)

sm = metrics(supply_shadow_ep)
pm = metrics(supply_perps)
spm = metrics(spot)
all_shadow_ep = len(episodes)
as_of = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")

def pf(v: float) -> str:
    return "∞" if math.isinf(v) else f"{v:.2f}"

report = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trade sample review</title><style>
:root{{--ink:#1f2430;--muted:#6f768a;--line:#e2e5ea;--blue:#eaf1fe;--orange:#ffedde;--panel:#fff}}
*{{box-sizing:border-box}} body{{margin:0;background:#fcfcfd;color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,sans-serif}}
main{{max-width:1040px;margin:auto;padding:48px 24px 72px}} header{{margin-bottom:26px}} h1{{font-size:38px;letter-spacing:-.03em;margin:0 0 8px}}
h2{{font-size:25px;letter-spacing:-.02em;margin:42px 0 12px}} h3{{margin:26px 0 8px}} p,li{{line-height:1.62}} .meta{{color:var(--muted)}}
.summary{{background:linear-gradient(135deg,#eaf1fe,#fff4c2);border:1px solid #cedffe;border-radius:18px;padding:22px 26px}}
.summary h2{{margin:0 0 8px}} .summary li+li{{margin-top:9px}} figure{{margin:24px 0 34px}} img{{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:14px;background:white}}
figcaption{{color:var(--muted);font-size:14px;margin-top:8px}} table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;background:white;border-radius:12px;overflow:hidden}}
th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:right}} th:first-child,td:first-child{{text-align:left}} th{{background:#f4f5f7;font-size:13px}}
.callout{{border-left:4px solid #f0986e;background:#ffedde;padding:14px 18px;border-radius:0 12px 12px 0}} code{{background:#f4f5f7;padding:2px 5px;border-radius:4px}}
.priority{{display:inline-block;background:#2e4780;color:white;border-radius:999px;padding:3px 9px;font-size:12px;margin-right:7px}} footer{{color:var(--muted);font-size:13px;margin-top:48px}}
@media(max-width:700px){{main{{padding:28px 16px}}h1{{font-size:30px}}table{{font-size:13px}}th,td{{padding:8px 6px}}}}
</style></head><body><main data-report-audience="product stakeholders">
<header><h1>Trade sample review</h1><p class="meta">Shadow results and closed spot/perp trades · As of {as_of}</p></header>
<section class="summary"><h2>Executive Summary</h2><ul>
<li><strong>The shadow total is materially overstated.</strong> Shadow v2 has {len(shadow)} closed rows but only {all_shadow_ep} non-overlapping episodes. For <code>supply_demand_3step</code>, 55 rows collapse to 11 episodes; its apparent +${supply_shadow_raw.pnl.sum():.2f} becomes ${supply_shadow_ep.pnl.sum():.2f} using the first entry per episode.</li>
<li><strong>There is not enough clean evidence to scale perp supply-demand.</strong> It has 11 shadow episodes (PF {pf(sm['pf'])}) and only {pm['n']} executable perp closes (PF {pf(pm['pf'])}, ${pm['pnl']:.2f}). Keep it paper/canary-sized.</li>
<li><strong>There is enough evidence to stop the current broad spot supply-demand gate.</strong> Across {spm['n']} closes, 30 pairs, 3 venues and 9 entry days, it lost ${abs(spm['pnl']):.2f}, won {spm['win']:.1%}, and produced PF {pf(spm['pf'])}; every venue was negative.</li>
<li><strong>Overall shadow history is too short for durable tuning.</strong> The episode-adjusted sample covers only three entry days. Counts support triage, not promotion or large parameter changes.</li>
</ul></section>

<section><h2>Repeated shadow scans created false confidence</h2>
<p>The 45 overlapping MSTR supply-demand rows are one continuous opportunity, not 45 independent trials. They contributed most of the headline profit. Episode-level measurement reverses the supply-demand conclusion from strongly positive to approximately flat/negative.</p>
<figure><img src="assets/shadow_rows_vs_episodes.png" alt="Shadow rows and independent episodes by strategy"><figcaption>An episode is a chain of overlapping positions for the same strategy, instrument and side. The first entry represents the episode.</figcaption></figure>
<figure><img src="assets/supply_demand_cohorts.png" alt="Supply-demand PnL by evidence cohort"><figcaption>The executable-perp window begins with shadow collection on June 20. Spot covers all recorded supply-demand closes from June 7–21.</figcaption></figure>
</section>

<section><h2>Spot supply-demand fails across the deployed breadth</h2>
<p>The loss is not isolated to one exchange: Binance lost $25.92, Crypto.com $20.20, and Bybit $8.02. Sideways trades account for 41 of 56 closes and −$45.50; trending-up also lost $10.34. Breakout is +$4.24 but has only three trades—far too few to validate an exception.</p>
<figure><img src="assets/spot_supply_regime.png" alt="Spot supply-demand PnL by regime"><figcaption>Regime is parsed from the persisted entry reason. The strategy is long-only on spot.</figcaption></figure>
<div class="callout"><strong>Exit economics also need correction.</strong> Four setup-target exits produced only $0.22 total, while 37 stop-loss exits lost $67.70. Historical setup targets as small as 0.20% indicate that the existing 1.2% minimum target floor was not consistently applied on every spot path.</div>
</section>

<section><h2>Sample sufficiency by decision</h2>
<table><thead><tr><th>Decision</th><th>Clean sample</th><th>Coverage</th><th>Assessment</th></tr></thead><tbody>
<tr><td>Scale supply-demand perps</td><td>11 shadow episodes + 5 executable closes</td><td>3 days / 4 coins</td><td>No</td></tr>
<tr><td>Keep current broad spot supply-demand gate</td><td>56 executable closes</td><td>9 days / 30 pairs / 3 venues</td><td>No—stop it</td></tr>
<tr><td>Enable breakout-only spot gate</td><td>3 closes</td><td>3 pairs</td><td>No—paper test only</td></tr>
<tr><td>Tune other shadow strategies</td><td>1–61 episodes each</td><td>2–3 days</td><td>Directional triage only</td></tr>
</tbody></table>
<p>The 95% Wilson interval for spot supply-demand win rate is {spm['win_lo']:.1%}–{spm['win_hi']:.1%}; the bootstrap 95% interval for mean PnL is ${cohorts.iloc[3].mean_ci_lo:.2f} to ${cohorts.iloc[3].mean_ci_hi:.2f}. This is strong evidence against the deployed broad spot policy. The corresponding shadow-episode win-rate interval is {sm['win_lo']:.1%}–{sm['win_hi']:.1%}, which is too wide to support scaling.</p>
</section>

<section><h2>Recommended changes</h2>
<ol>
<li><span class="priority">Immediate</span><strong>Disable new spot <code>supply_demand_3step</code> entries.</strong> If continued experimentation is required, run breakout-only in paper/shadow mode; do not treat the three winning breakout closes as validation.</li>
<li><span class="priority">Immediate</span><strong>Revert the perp size increase.</strong> Change the supply-demand specialist multiplier from 0.85 back to 0.40 (or lower) and retain a single active position per coin/side until clean requalification. The 0.85 change was justified by duplicated shadow rows.</li>
<li><span class="priority">Immediate</span><strong>Enforce the existing minimum setup-target floor on every spot exit path.</strong> Reject targets below <code>max(min_setup_target_pct, round-trip fees + slippage buffer)</code>; add a regression test proving a 0.20% target cannot be armed when the configured floor is 1.2%.</li>
<li><strong>Make shadow reporting episode-native.</strong> Persist a stable <code>shadow_episode_id</code> from zone creation through invalidation, expose both raw-row and independent-episode counts, and exclude legacy overlapping rows from performance totals.</li>
<li><strong>Version every policy change.</strong> Persist gate, sizing, exit-policy, and config versions on each entry. Compare only like-for-like cohorts after a change.</li>
<li><strong>Use explicit requalification gates.</strong> Parameter tuning starts at 30 independent closes over at least 14 days, 10 instruments and two regimes. Scaling requires at least 60 clean closes, PF ≥1.25, positive net expectancy after fees, no instrument above 20% of episodes, and a positive holdout window. Full promotion should wait for 100 independent closes.</li>
</ol></section>

<section><h2>Further questions</h2><ul>
<li>Why did the spot path execute setup targets below the configured 1.2% floor?</li>
<li>Are shadow entries using the same spread, liquidity, fee and slippage checks as executable entries?</li>
<li>Can a stable zone identifier replace time-based fingerprints so a setup remains one episode until invalidated?</li>
</ul></section>

<section><h2>Caveats and assumptions</h2><p>All timestamps are UTC. Perp results are paper trades; spot records are simulation trades. Realized PnL is treated as the canonical net result and fees are not subtracted again. Shadow episodes are conservatively defined as chains of overlapping positions for the same strategy/instrument/side; the first row represents each episode. Historical trades span different policy versions, so conclusions are strongest for stopping an evidently losing policy and weakest for selecting replacement thresholds.</p></section>
<footer>Source: local PostgreSQL tables <code>trading.perp_paper_trades</code> and <code>trading.trades</code>. Generated from live data on {as_of}. Supporting CSVs and the reproducible script are stored beside this report.</footer>
<!-- Source metadata: local Postgres; shadow filter shadow_trade=true, accounting_excluded=true, shadow_version=2; executable perps exclude accounting_excluded; closed trades use exit_time windows. -->
</main></body></html>"""
(ROOT / "trade_sample_review.html").write_text(report, encoding="utf-8")
print(cohorts.to_string(index=False))
print(f"shadow raw={len(shadow)} episodes={len(episodes)}")
print(ROOT / "trade_sample_review.html")
