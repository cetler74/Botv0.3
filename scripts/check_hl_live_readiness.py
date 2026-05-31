#!/usr/bin/env python3
"""Exit 0 when rsi_stoch HL paper metrics meet live_promotion thresholds."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.hyperliquid_live_readiness import evaluate_live_readiness  # noqa: E402


def _load_config() -> dict:
    cfg_path = ROOT / "config" / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _fetch_trades(database_url: str, limit: int) -> list:
    resp = httpx.get(
        f"{database_url.rstrip('/')}/api/v1/perps/paper-trades",
        params={"limit": limit},
        timeout=60.0,
    )
    resp.raise_for_status()
    return (resp.json() or {}).get("trades") or []


def main() -> int:
    parser = argparse.ArgumentParser(description="Check HL rsi_stoch paper live readiness")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_SERVICE_URL", "http://localhost:8012"),
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--json", action="store_true", help="Print full result JSON")
    args = parser.parse_args()

    cfg = _load_config()
    hl = ((cfg.get("trading") or {}).get("hyperliquid_perps") or {})
    promo = hl.get("live_promotion") or {}
    strategy = str(promo.get("strategy") or "rsi_stoch_reversal_5m")

    rows = _fetch_trades(args.database_url, args.limit)
    result = evaluate_live_readiness(rows, promo, strategy=strategy)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        metrics = result.get("metrics") or {}
        print(f"ready={result.get('ready')}")
        for k, v in metrics.items():
            print(f"  {k}: {v}")
        if result.get("reasons"):
            print("reasons:")
            for r in result["reasons"]:
                print(f"  - {r}")

    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
