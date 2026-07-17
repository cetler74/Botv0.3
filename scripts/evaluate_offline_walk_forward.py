#!/usr/bin/env python3
"""Run the deterministic 1h/15m offline walk-forward evaluator.

The CLI reads only the existing exchange-service OHLCV endpoint and writes a
research manifest. It does not change runtime configuration or promote a
candidate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.offline_walk_forward import (  # noqa: E402
    EvaluationConfig,
    closed_bar_ma_intents,
    deterministic_json_bytes,
    evaluate_walk_forward,
    load_exchange_ohlcv,
)


def _candidate(value: str):
    try:
        fast, slow = (int(part) for part in value.split(":", 1))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("candidate must be FAST:SLOW") from exc
    if fast < 1 or slow <= fast:
        raise argparse.ArgumentTypeError("candidate requires 0 < FAST < SLOW")
    return fast, slow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange-service-url", default="http://127.0.0.1:8003")
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--venue", choices=("spot", "perp"), required=True)
    parser.add_argument(
        "--candidate",
        action="append",
        type=_candidate,
        required=True,
        metavar="FAST:SLOW",
        help="repeat for each closed-1h MA baseline parameter trial",
    )
    parser.add_argument("--limit-1h", type=int, default=2000)
    parser.add_argument("--limit-15m", type=int, default=8000)
    parser.add_argument("--train-bars", type=int, required=True)
    parser.add_argument("--oos-bars", type=int, required=True)
    parser.add_argument("--holdout-bars", type=int, required=True)
    parser.add_argument(
        "--as-of",
        required=True,
        help="timezone-aware evaluation cutoff; bars ending later are excluded",
    )
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--quantity", type=float, default=1.0)
    parser.add_argument("--fee-bps", type=float, required=True)
    parser.add_argument("--spread-bps", type=float, required=True)
    parser.add_argument("--slippage-bps", type=float, required=True)
    parser.add_argument("--max-positions", type=int, default=1)
    parser.add_argument("--daily-loss-limit", type=float, default=0.0)
    parser.add_argument("--stop-pct", type=float)
    parser.add_argument("--target-pct", type=float)
    parser.add_argument("--trailing-pct", type=float)
    parser.add_argument("--max-hold-bars", type=int)
    parser.add_argument("--output", type=Path, help="manifest path; stdout when omitted")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        evaluation_as_of = pd.Timestamp(args.as_of)
        if evaluation_as_of.tzinfo is None:
            raise ValueError("--as-of must be timezone-aware")
        evaluation_as_of = evaluation_as_of.tz_convert("UTC")
        config = EvaluationConfig(
            initial_equity=args.initial_equity,
            fee_bps=args.fee_bps,
            spread_bps=args.spread_bps,
            slippage_bps=args.slippage_bps,
            max_positions=args.max_positions,
            daily_loss_limit=args.daily_loss_limit,
        )
        one_hour = {}
        fifteen = {}
        for symbol in sorted(set(args.symbols)):
            one_hour[symbol] = load_exchange_ohlcv(
                args.exchange_service_url,
                args.exchange,
                symbol,
                "1h",
                args.limit_1h,
                as_of=evaluation_as_of,
            )
            fifteen[symbol] = load_exchange_ohlcv(
                args.exchange_service_url,
                args.exchange,
                symbol,
                "15m",
                args.limit_15m,
                as_of=evaluation_as_of,
            )
        shared = {
            "venue": args.venue,
            "quantity": args.quantity,
            "stop_pct": args.stop_pct,
            "target_pct": args.target_pct,
            "trailing_pct": args.trailing_pct,
            "max_hold_bars": args.max_hold_bars,
        }
        candidates = [
            {"fast": fast, "slow": slow, **shared}
            for fast, slow in sorted(set(args.candidate))
        ]
        manifest = evaluate_walk_forward(
            one_hour,
            fifteen,
            candidates=candidates,
            strategy=closed_bar_ma_intents,
            config=config,
            train_bars=args.train_bars,
            oos_bars=args.oos_bars,
            holdout_bars=args.holdout_bars,
            evaluation_as_of=evaluation_as_of,
        )
        manifest["research_input"] = {
            "source": "exchange-service-http",
            "exchange": args.exchange,
            "symbols": sorted(set(args.symbols)),
            "timeframes": ["1h", "15m"],
            "baseline": "closed_bar_moving_average",
        }
        encoded = deterministic_json_bytes(manifest)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encoded)
        else:
            sys.stdout.buffer.write(encoded)
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"offline walk-forward unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
