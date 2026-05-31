#!/usr/bin/env python3
"""Poll database-service for trading KPIs and expose Prometheus metrics."""

import logging
import os
import time

import httpx
from prometheus_client import Gauge, start_http_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_SERVICE_URL = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002").rstrip("/")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "8015"))

PERP_REALIZED = Gauge("trading_perp_paper_realized_pnl_usd", "Paper perp realized PnL USD (24h window)")
PERP_UNREALIZED = Gauge("trading_perp_paper_unrealized_pnl_usd", "Paper perp unrealized PnL USD")
PERP_CLOSED = Gauge("trading_perp_paper_closed_trades_24h", "Paper perp closed trades in report window")
PERP_WIN_RATE = Gauge("trading_perp_paper_win_rate", "Paper perp win rate 0-1")
PERP_STRATEGY_PNL = Gauge(
    "trading_perp_paper_by_strategy_pnl_usd",
    "Paper perp PnL by strategy USD",
    ["strategy"],
)
ALERTS_UNRESOLVED = Gauge("trading_alerts_unresolved_total", "Unresolved DB alerts count")
SPOT_CLOSED_24H = Gauge(
    "trading_spot_trades_closed_24h",
    "Spot trades closed in the last 24 hours (database)",
)
SPOT_OPEN = Gauge("trading_spot_trades_open", "Currently open spot trades (database)")
SPOT_REALIZED_24H = Gauge(
    "trading_spot_realized_pnl_24h_usd",
    "Spot realized PnL USD in the report window (database)",
)
SPOT_REALIZED_ALL = Gauge(
    "trading_spot_realized_pnl_usd",
    "Spot realized PnL USD all-time (database CLOSED trades)",
)
SPOT_UNREALIZED = Gauge(
    "trading_spot_unrealized_pnl_usd",
    "Spot unrealized PnL USD on open trades (database)",
)
HL_RSI_STOCH_LIVE_READY = Gauge(
    "trading_hl_rsi_stoch_live_readiness",
    "1 when rsi_stoch HL paper metrics meet live_promotion thresholds (informational)",
)


def _set_perp_metrics(report: dict) -> None:
    agg = report.get("aggregate") or report.get("summary") or {}
    PERP_REALIZED.set(float(agg.get("realized") or agg.get("realizedPnl") or agg.get("realized_pnl") or 0))
    PERP_UNREALIZED.set(float(agg.get("unrealized") or agg.get("unrealizedPnl") or agg.get("unrealized_pnl") or 0))
    closed = agg.get("closed") or agg.get("nClosed") or agg.get("closed_trades") or 0
    PERP_CLOSED.set(float(closed))
    win_rate_pct = float(agg.get("winRate") or 0)
    if win_rate_pct > 1:
        PERP_WIN_RATE.set(win_rate_pct / 100.0)
    else:
        wins = float(agg.get("wins") or 0)
        losses = float(agg.get("losses") or 0)
        total = wins + losses
        PERP_WIN_RATE.set((wins / total) if total > 0 else win_rate_pct)

    breakdowns = report.get("breakdowns") or {}
    by_strategy = (
        breakdowns.get("strategy")
        or report.get("by_strategy")
        or report.get("byStrategy")
        or []
    )
    for row in by_strategy:
        if not isinstance(row, dict):
            continue
        name = str(row.get("label") or row.get("strategy") or row.get("strategy_name") or "unknown")
        pnl = float(row.get("pnl") or row.get("realized_pnl") or row.get("realizedPnl") or 0)
        PERP_STRATEGY_PNL.labels(strategy=name).set(pnl)


def _poll() -> None:
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{DATABASE_SERVICE_URL}/api/v1/perps/paper-pnl-report", params={"hours": 24})
            if r.status_code == 200:
                _set_perp_metrics(r.json())
            else:
                logger.warning("paper-pnl-report status=%s", r.status_code)
            ready_r = client.get(
                f"{DATABASE_SERVICE_URL}/api/v1/perps/paper-readiness/rsi_stoch"
            )
            if ready_r.status_code == 200:
                HL_RSI_STOCH_LIVE_READY.set(
                    1.0 if (ready_r.json() or {}).get("ready") else 0.0
                )
            else:
                logger.warning("paper-readiness status=%s", ready_r.status_code)

            ar = client.get(f"{DATABASE_SERVICE_URL}/api/v1/alerts/unresolved/count")
            if ar.status_code == 200:
                data = ar.json()
                ALERTS_UNRESOLVED.set(int(data.get("count") or 0))

            ts = client.get(
                f"{DATABASE_SERVICE_URL}/api/v1/trades/stats/summary",
                params={"hours": 24},
            )
            if ts.status_code == 200:
                summary = ts.json()
                SPOT_CLOSED_24H.set(int(summary.get("closed_trades") or 0))
                SPOT_OPEN.set(int(summary.get("open_trades") or 0))
                SPOT_REALIZED_24H.set(float(summary.get("realized_pnl_24h") or 0))
                SPOT_REALIZED_ALL.set(float(summary.get("realized_pnl_all_time") or 0))
                SPOT_UNREALIZED.set(float(summary.get("unrealized_pnl_open") or 0))
    except Exception as e:
        logger.error("KPI poll failed: %s", e)


def main() -> None:
    start_http_server(METRICS_PORT)
    logger.info("KPI exporter on :%s polling %s every %ss", METRICS_PORT, DATABASE_SERVICE_URL, POLL_INTERVAL)
    while True:
        _poll()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
