"""Deterministic, cost-aware 1h/15m offline portfolio research.

The module is intentionally strategy-agnostic: callers provide closed bars and
a function that produces :class:`TradeIntent` objects from each isolated
research slice. Nothing here promotes candidates or mutates runtime state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd


_TIMEFRAMES = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1)}
_OHLCV = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class EvaluationConfig:
    initial_equity: float = 10_000.0
    fee_bps: float = 0.0
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    max_positions: int = 1
    daily_loss_limit: float = 0.0

    def __post_init__(self) -> None:
        numeric = {
            "initial_equity": self.initial_equity,
            "fee_bps": self.fee_bps,
            "spread_bps": self.spread_bps,
            "slippage_bps": self.slippage_bps,
            "max_positions": self.max_positions,
            "daily_loss_limit": self.daily_loss_limit,
        }
        if any(not math.isfinite(float(value)) for value in numeric.values()):
            raise ValueError("evaluation configuration values must be finite")
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if any(
            value < 0
            for value in (
                self.fee_bps,
                self.spread_bps,
                self.slippage_bps,
                self.daily_loss_limit,
            )
        ):
            raise ValueError("costs and daily_loss_limit cannot be negative")
        if self.fee_bps >= 10_000:
            raise ValueError("fee basis points must be less than 10000")
        if self.spread_bps + self.slippage_bps >= 10_000:
            raise ValueError("spread plus slippage must preserve a positive fill price")
        if int(self.max_positions) != self.max_positions or self.max_positions < 1:
            raise ValueError("max_positions must be at least one")


@dataclass(frozen=True)
class TradeIntent:
    symbol: str
    time: pd.Timestamp
    venue: str
    side: str
    quantity: float = 1.0
    stop_price: float | None = None
    target_price: float | None = None
    trailing_pct: float | None = None
    max_hold_bars: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).strip())
        object.__setattr__(self, "venue", str(self.venue).lower())
        object.__setattr__(self, "side", str(self.side).lower())
        timestamp = pd.Timestamp(self.time)
        if timestamp.tzinfo is None:
            raise ValueError("intent time must be timezone-aware")
        object.__setattr__(self, "time", timestamp.tz_convert("UTC"))
        if not self.symbol:
            raise ValueError("intent symbol is required")
        if self.venue not in {"spot", "perp"}:
            raise ValueError("venue must be spot or perp")
        if self.side not in {"long", "short"}:
            raise ValueError("side must be long or short")
        if self.venue == "spot" and self.side != "long":
            raise ValueError("spot trades must be long")
        numeric = {
            "quantity": self.quantity,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "trailing_pct": self.trailing_pct,
            "max_hold_bars": self.max_hold_bars,
        }
        if any(
            value is not None and not math.isfinite(float(value))
            for value in numeric.values()
        ):
            raise ValueError("trade intent numeric values must be finite")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        for name in ("stop_price", "target_price"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.trailing_pct is not None and not 0 < self.trailing_pct < 1:
            raise ValueError("trailing_pct must be between zero and one")
        if self.max_hold_bars is not None and (
            int(self.max_hold_bars) != self.max_hold_bars
            or self.max_hold_bars < 1
        ):
            raise ValueError("max_hold_bars must be a positive integer")


def prepare_closed_bars(
    bars: pd.DataFrame,
    timeframe: str,
    *,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Validate OHLCV and retain bars whose complete interval ended by as_of."""
    if timeframe not in _TIMEFRAMES:
        raise ValueError("timeframe must be 1h or 15m")
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        raise ValueError("bars cannot be empty")
    missing = [column for column in _OHLCV if column not in bars.columns]
    if missing:
        raise ValueError(f"bars missing OHLCV column(s): {', '.join(missing)}")
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("bars must use a DatetimeIndex")
    if bars.index.tz is None:
        raise ValueError("bar timestamps must be timezone-aware")

    frame = bars.loc[:, _OHLCV].copy()
    frame.index = frame.index.tz_convert("UTC")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("bar timestamps must be sorted and unique")
    interval = _TIMEFRAMES[timeframe]
    if any(timestamp.value % interval.value for timestamp in frame.index):
        raise ValueError(f"bar timestamps must be aligned to {timeframe}")
    if len(frame.index) > 1 and not (frame.index.to_series().diff().dropna() == interval).all():
        raise ValueError(f"{timeframe} crypto bars must be continuous")
    for column in _OHLCV:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    values = frame.loc[:, _OHLCV].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("OHLCV values must be finite")
    if (frame.loc[:, ("open", "high", "low", "close")] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (frame["volume"] < 0).any():
        raise ValueError("volume cannot be negative")
    if (
        (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any()
        or (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise ValueError("OHLC high/low relationships are invalid")

    cutoff = (
        pd.Timestamp.now(tz="UTC")
        if as_of is None
        else _utc_timestamp(as_of, "as_of")
    )
    closed = frame.loc[frame.index + interval <= cutoff]
    if closed.empty:
        raise ValueError("no closed bars available")
    return closed


def load_exchange_ohlcv(
    service_url: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    limit: int,
    *,
    timeout: float = 20.0,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Load OHLCV through the repository exchange-service HTTP endpoint."""
    if timeframe not in _TIMEFRAMES:
        raise ValueError("timeframe must be 1h or 15m")
    if limit < 1:
        raise ValueError("limit must be positive")
    path = "/api/v1/market/ohlcv/{}/{}".format(
        urllib.parse.quote(str(exchange), safe=""),
        urllib.parse.quote(str(symbol), safe=""),
    )
    query = urllib.parse.urlencode({"timeframe": timeframe, "limit": int(limit)})
    url = service_url.rstrip("/") + path + "?" + query
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        raise RuntimeError(
            f"Historical OHLCV request failed for {exchange}/{symbol} {timeframe}: {exc}"
        ) from exc
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not data:
        detail = payload.get("error") if isinstance(payload, Mapping) else None
        raise RuntimeError(
            f"No historical OHLCV available for {exchange}/{symbol} {timeframe}"
            + (f": {detail}" if detail else "")
        )
    try:
        frame = pd.DataFrame(data)
        timestamps = pd.to_datetime(frame.pop("timestamp"), unit="ms", utc=True)
        frame.index = timestamps
        return prepare_closed_bars(frame, timeframe, as_of=as_of)
    except Exception as exc:
        raise RuntimeError(
            f"Invalid historical OHLCV for {exchange}/{symbol} {timeframe}: {exc}"
        ) from exc


def closed_bar_ma_intents(
    one_hour: Mapping[str, pd.DataFrame],
    fifteen_minute: Mapping[str, pd.DataFrame],
    params: Mapping[str, Any],
) -> List[TradeIntent]:
    """Generate a simple CLI research baseline using only closed 1h bars.

    Signals execute on the first 15m bar at or after the signal hour closes.
    This is a reproducible evaluator input, not a production strategy.
    """
    fast = int(params.get("fast", 0))
    slow = int(params.get("slow", 0))
    venue = str(params.get("venue", "spot")).lower()
    if fast < 1 or slow <= fast:
        raise ValueError("MA candidate requires 0 < fast < slow")
    if venue not in {"spot", "perp"}:
        raise ValueError("candidate venue must be spot or perp")
    quantity = float(params.get("quantity", 1.0))
    stop_pct = _optional_fraction(params.get("stop_pct"), "stop_pct")
    target_pct = _optional_fraction(params.get("target_pct"), "target_pct")
    trailing_pct = _optional_fraction(params.get("trailing_pct"), "trailing_pct")
    max_hold = params.get("max_hold_bars")
    max_hold = int(max_hold) if max_hold is not None else None
    intents: List[TradeIntent] = []

    for symbol in sorted(one_hour):
        hourly = one_hour[symbol]
        quarter = fifteen_minute[symbol]
        fast_ma = hourly["close"].rolling(fast, min_periods=fast).mean()
        slow_ma = hourly["close"].rolling(slow, min_periods=slow).mean()
        for timestamp in hourly.index[slow - 1 :]:
            direction = "long" if fast_ma.loc[timestamp] > slow_ma.loc[timestamp] else "short"
            if fast_ma.loc[timestamp] == slow_ma.loc[timestamp]:
                continue
            if venue == "spot" and direction == "short":
                continue
            execution_time = timestamp + _TIMEFRAMES["1h"]
            if execution_time < quarter.index[0]:
                continue
            available = quarter.index[quarter.index >= execution_time]
            if not len(available):
                continue
            execution_time = available[0]
            entry = float(quarter.loc[execution_time, "open"])
            side_sign = 1 if direction == "long" else -1
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    time=execution_time,
                    venue=venue,
                    side=direction,
                    quantity=quantity,
                    stop_price=(
                        entry * (1 - side_sign * stop_pct)
                        if stop_pct is not None
                        else None
                    ),
                    target_price=(
                        entry * (1 + side_sign * target_pct)
                        if target_pct is not None
                        else None
                    ),
                    trailing_pct=trailing_pct,
                    max_hold_bars=max_hold,
                )
            )
    return intents


def simulate_portfolio(
    bars_by_symbol: Mapping[str, pd.DataFrame],
    intents: Iterable[TradeIntent],
    config: EvaluationConfig,
    *,
    evaluation_as_of: pd.Timestamp | None = None,
) -> Dict[str, Any]:
    """Simulate synchronized positions with conservative intrabar exits.

    Event order per timestamp is explicit: mark existing positions at the bar
    open, reserve their capacity, admit/reject new open-priced entries, then
    resolve all intrabar exits stop-first and finally mark equity at the close.
    An intrabar exit therefore cannot free capacity retroactively at that open.
    """
    if evaluation_as_of is None:
        raise ValueError("evaluation_as_of is required for direct DataFrame evaluation")
    bars = _validate_portfolio_bars(bars_by_symbol, evaluation_as_of)
    ordered_intents = sorted(
        list(intents), key=lambda item: (item.time, item.symbol, item.venue, item.side)
    )
    for intent in ordered_intents:
        if intent.symbol not in bars:
            raise ValueError(f"no 15m bars for intent symbol {intent.symbol}")
        if intent.time not in bars[intent.symbol].index:
            raise ValueError(f"intent time is not a bar timestamp for {intent.symbol}")

    entries: Dict[pd.Timestamp, List[TradeIntent]] = {}
    for intent in ordered_intents:
        entries.setdefault(intent.time, []).append(intent)
    timeline = sorted(set().union(*(set(frame.index) for frame in bars.values())))
    positions: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    rejections: List[Dict[str, str]] = []
    realized = 0.0
    realized_by_symbol = {symbol: 0.0 for symbol in bars}
    equity_points: List[Tuple[pd.Timestamp, float]] = [
        (timeline[0] - pd.Timedelta(nanoseconds=1), config.initial_equity)
    ]
    symbol_equity_points = {
        symbol: [(timeline[0] - pd.Timedelta(nanoseconds=1), config.initial_equity)]
        for symbol in bars
    }
    concurrent_counts: List[int] = []
    current_day = None
    day_start_equity = config.initial_equity
    last_equity = config.initial_equity
    daily_halted = False

    for timestamp in timeline:
        if timestamp.date() != current_day:
            current_day = timestamp.date()
            day_start_equity = last_equity
            daily_halted = False
        exposure_count = len(positions)
        pre_entry_equity = (
            config.initial_equity
            + realized
            + _mark_to_market(
                positions,
                bars,
                timestamp,
                config,
                price_column="open",
            )
        )
        if (
            config.daily_loss_limit > 0
            and pre_entry_equity <= day_start_equity - config.daily_loss_limit
        ):
            daily_halted = True
        for intent in entries.get(timestamp, []):
            rejection = None
            if daily_halted:
                rejection = "daily_loss_halt"
            elif len(positions) >= config.max_positions:
                rejection = "max_positions"
            if rejection:
                rejections.append(
                    {
                        "symbol": intent.symbol,
                        "time": timestamp.isoformat(),
                        "reason": rejection,
                    }
                )
                continue
            base_entry = float(bars[intent.symbol].loc[timestamp, "open"])
            entry_price = _adverse_fill(base_entry, intent.side, True, config)
            positions.append(
                {
                    "intent": intent,
                    "entry_time": timestamp,
                    "entry_price": entry_price,
                    "entry_fee": entry_price * intent.quantity * config.fee_bps / 10_000,
                    "held_bars": 0,
                    "best_price": base_entry,
                    "trailing_stop": None,
                }
            )
            exposure_count = max(exposure_count, len(positions))
            pre_entry_equity = (
                config.initial_equity
                + realized
                + _mark_to_market(
                    positions,
                    bars,
                    timestamp,
                    config,
                    price_column="open",
                )
            )
            if (
                config.daily_loss_limit > 0
                and pre_entry_equity
                <= day_start_equity - config.daily_loss_limit
            ):
                daily_halted = True

        remaining: List[Dict[str, Any]] = []
        for position in positions:
            frame = bars[position["intent"].symbol]
            if timestamp not in frame.index:
                remaining.append(position)
                continue
            row = frame.loc[timestamp]
            if timestamp > position["entry_time"]:
                position["held_bars"] += 1
            exit_spec = _exit_for_bar(position, row)
            if exit_spec is None and timestamp == frame.index[-1]:
                exit_spec = (float(row["close"]), "end_of_data")
            if exit_spec is None:
                remaining.append(position)
                continue
            trade = _close_trade(position, timestamp, *exit_spec, config)
            trades.append(trade)
            realized += trade["net_pnl"]
            realized_by_symbol[trade["symbol"]] += trade["net_pnl"]
        positions = remaining

        last_equity = (
            config.initial_equity
            + realized
            + _mark_to_market(positions, bars, timestamp, config)
        )
        equity_points.append((timestamp, last_equity))
        for symbol in bars:
            symbol_equity_points[symbol].append(
                (
                    timestamp,
                    config.initial_equity
                    + realized_by_symbol[symbol]
                    + _mark_to_market(
                        positions,
                        bars,
                        timestamp,
                        config,
                        symbol=symbol,
                    ),
                )
            )
        concurrent_counts.append(exposure_count)

    curve = pd.Series(
        [point[1] for point in equity_points],
        index=pd.DatetimeIndex([point[0] for point in equity_points]),
        dtype=float,
        name="equity",
    )
    symbol_equity = {
        symbol: pd.Series(
            [point[1] for point in points],
            index=pd.DatetimeIndex([point[0] for point in points]),
            dtype=float,
            name=f"{symbol}_equity",
        )
        for symbol, points in symbol_equity_points.items()
    }
    return {
        "trades": trades,
        "rejections": rejections,
        "equity": curve,
        "symbol_equity": symbol_equity,
        "portfolio": _portfolio_information(
            trades,
            concurrent_counts,
            config.max_positions,
            symbol_equity,
        ),
    }


def _exit_for_bar(
    position: Dict[str, Any], row: pd.Series
) -> Tuple[float, str] | None:
    intent: TradeIntent = position["intent"]
    open_price = float(row["open"])
    high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
    trailing = position["trailing_stop"]
    if intent.side == "long":
        stops = [value for value in (intent.stop_price, trailing) if value is not None]
        stop = max(stops) if stops else None
        if stop is not None and low <= stop:
            return min(open_price, float(stop)), (
                "trailing_stop" if trailing == stop else "stop"
            )
        if intent.target_price is not None and high >= intent.target_price:
            return float(intent.target_price), "target"
        position["best_price"] = max(position["best_price"], high)
        if intent.trailing_pct is not None:
            position["trailing_stop"] = position["best_price"] * (1 - intent.trailing_pct)
    else:
        stops = [value for value in (intent.stop_price, trailing) if value is not None]
        stop = min(stops) if stops else None
        if stop is not None and high >= stop:
            return max(open_price, float(stop)), (
                "trailing_stop" if trailing == stop else "stop"
            )
        if intent.target_price is not None and low <= intent.target_price:
            return float(intent.target_price), "target"
        position["best_price"] = min(position["best_price"], low)
        if intent.trailing_pct is not None:
            position["trailing_stop"] = position["best_price"] * (1 + intent.trailing_pct)
    if intent.max_hold_bars is not None and position["held_bars"] >= intent.max_hold_bars:
        return close, "max_hold"
    return None


def _mark_to_market(
    positions: Sequence[Mapping[str, Any]],
    bars: Mapping[str, pd.DataFrame],
    timestamp: pd.Timestamp,
    config: EvaluationConfig,
    *,
    price_column: str = "close",
    symbol: str | None = None,
) -> float:
    unrealized = 0.0
    for position in positions:
        intent: TradeIntent = position["intent"]
        if symbol is not None and intent.symbol != symbol:
            continue
        frame = bars[intent.symbol]
        available = frame.index[frame.index <= timestamp]
        if not len(available):
            continue
        mark_time = available[-1]
        column = price_column if mark_time == timestamp else "close"
        base_exit = float(frame.loc[mark_time, column])
        estimated_exit = _adverse_fill(base_exit, intent.side, False, config)
        direction = 1.0 if intent.side == "long" else -1.0
        unrealized += (
            direction
            * (estimated_exit - position["entry_price"])
            * intent.quantity
            - position["entry_fee"]
            - estimated_exit * intent.quantity * config.fee_bps / 10_000
        )
    return float(unrealized)


def _close_trade(
    position: Mapping[str, Any],
    exit_time: pd.Timestamp,
    base_exit: float,
    reason: str,
    config: EvaluationConfig,
) -> Dict[str, Any]:
    intent: TradeIntent = position["intent"]
    exit_price = _adverse_fill(base_exit, intent.side, False, config)
    exit_fee = exit_price * intent.quantity * config.fee_bps / 10_000
    fees = position["entry_fee"] + exit_fee
    direction = 1.0 if intent.side == "long" else -1.0
    gross = direction * (exit_price - position["entry_price"]) * intent.quantity
    return {
        "symbol": intent.symbol,
        "venue": intent.venue,
        "side": intent.side,
        "entry_time": position["entry_time"].isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_price": float(position["entry_price"]),
        "exit_price": float(exit_price),
        "quantity": float(intent.quantity),
        "gross_pnl": float(gross),
        "fees": float(fees),
        "net_pnl": float(gross - fees),
        "exit_reason": reason,
    }


def _adverse_fill(
    price: float, side: str, entry: bool, config: EvaluationConfig
) -> float:
    adverse = (config.spread_bps + config.slippage_bps) / 10_000
    direction = 1 if side == "long" else -1
    sign = direction if entry else -direction
    return float(price * (1 + sign * adverse))


def _daily_mtm_returns(
    equity: pd.Series,
    calendar: pd.DatetimeIndex,
) -> pd.Series:
    """Daily close-to-close returns, including the first evaluation day."""
    if calendar.empty:
        return pd.Series(dtype=float)
    curve = equity.sort_index().astype(float)
    returns = []
    previous_end = None
    for day in calendar:
        day_end = day + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
        through_end = curve.loc[curve.index <= day_end]
        if through_end.empty:
            end_equity = previous_end if previous_end is not None else float(curve.iloc[0])
        else:
            end_equity = float(through_end.iloc[-1])
        if previous_end is None:
            before_day = curve.loc[curve.index < day]
            start_equity = (
                float(before_day.iloc[-1])
                if not before_day.empty
                else float(curve.iloc[0])
            )
        else:
            start_equity = previous_end
        returns.append(
            end_equity / start_equity - 1.0
            if start_equity > 0 and end_equity > 0
            else 0.0
        )
        previous_end = end_equity
    return pd.Series(returns, index=calendar, dtype=float)


def compute_metrics(
    trades: Sequence[Mapping[str, Any]],
    equity: pd.Series,
    *,
    fold_pnls: Sequence[float] = (),
    trial_count: int = 1,
    evaluation_start: pd.Timestamp | None = None,
    evaluation_end: pd.Timestamp | None = None,
) -> Dict[str, Any]:
    """Compute reproducible net, risk, robustness, and concentration metrics."""
    if not isinstance(equity, pd.Series) or equity.empty:
        raise ValueError("equity must be a non-empty Series")
    equity = pd.to_numeric(equity, errors="raise").astype(float)
    if not np.isfinite(equity.to_numpy()).all():
        raise ValueError("equity values must be finite")
    peaks = equity.cummax()
    drawdown = peaks - equity
    drawdown_pct = drawdown / peaks.replace(0, np.nan)
    pnls = np.asarray([float(trade["net_pnl"]) for trade in trades], dtype=float)
    profits = pnls[pnls > 0]
    losses = -pnls[pnls < 0]
    gross_profit, gross_loss = float(profits.sum()), float(losses.sum())

    if (evaluation_start is None) != (evaluation_end is None):
        raise ValueError("evaluation_start and evaluation_end must be provided together")
    if evaluation_start is not None:
        start = _utc_timestamp(evaluation_start, "evaluation_start")
        end = _utc_timestamp(evaluation_end, "evaluation_end")
        if end <= start:
            raise ValueError("evaluation_end must be after evaluation_start")
        calendar = pd.date_range(
            start.floor("D"),
            (end - pd.Timedelta(nanoseconds=1)).floor("D"),
            freq="D",
            tz="UTC",
        )
    else:
        if not isinstance(equity.index, pd.DatetimeIndex) or equity.index.tz is None:
            raise ValueError(
                "evaluation dates are required when equity lacks a timezone-aware index"
            )
        equity.index = equity.index.tz_convert("UTC")
        first_mark = equity.index.min()
        end_of_first_day = (
            first_mark.floor("D")
            + pd.Timedelta(days=1)
            - pd.Timedelta(nanoseconds=1)
        )
        first_day = (
            first_mark.ceil("D")
            if first_mark == end_of_first_day
            else first_mark.floor("D")
        )
        calendar = pd.date_range(
            first_day,
            equity.index.max().floor("D"),
            freq="D",
            tz="UTC",
        )

    if trades:
        pnl_rows = pd.Series(
            pnls,
            index=pd.to_datetime([trade["exit_time"] for trade in trades], utc=True),
        )
        daily = pnl_rows.groupby(pnl_rows.index.floor("D")).sum().sort_index()
        daily = daily.reindex(calendar, fill_value=0.0)
    else:
        daily = pd.Series(0.0, index=calendar, dtype=float)
    daily_returns = _daily_mtm_returns(equity, calendar)
    daily_std = (
        float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
    )
    sharpe = (
        float(daily_returns.mean()) / daily_std * math.sqrt(365)
        if daily_std > 0
        else 0.0
    )
    adjustment = (
        math.sqrt(2 * math.log(max(1, int(trial_count)))) / math.sqrt(max(1, len(daily)))
        if trial_count > 1
        else 0.0
    )
    rolling_30 = daily.rolling(30, min_periods=1).mean()
    return {
        "net_pnl": float(pnls.sum()) if len(pnls) else 0.0,
        "net_expectancy": float(pnls.mean()) if len(pnls) else 0.0,
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss > 0
            else None
        ),
        "profit_factor_infinite": bool(gross_profit > 0 and gross_loss == 0),
        "average_daily_pnl": float(daily.mean()) if len(daily) else 0.0,
        "rolling_30d_average_pnl": (
            float(rolling_30.iloc[-1]) if len(rolling_30) else 0.0
        ),
        "evaluation_calendar_days": int(len(calendar)),
        "max_drawdown_dollars": float(drawdown.max()),
        "max_drawdown_percent": float(drawdown_pct.max(skipna=True) or 0.0),
        "sharpe": sharpe,
        "sharpe_basis": "daily_mark_to_market_equity_returns",
        "selection_adjusted_sharpe_approximation": {
            "label": "Approximate selection-adjusted Sharpe (not a formal deflated-Sharpe theorem)",
            "value": float(sharpe - adjustment),
            "trials": int(trial_count),
        },
        "winner_concentration": (
            float(profits.max() / gross_profit) if gross_profit > 0 else 0.0
        ),
        "positive_fold_ratio": (
            float(sum(value > 0 for value in fold_pnls) / len(fold_pnls))
            if fold_pnls
            else 0.0
        ),
        "trade_count": int(len(trades)),
    }


def rolling_splits(
    index: pd.DatetimeIndex,
    *,
    train_bars: int,
    oos_bars: int,
    holdout_bars: int,
) -> Tuple[List[Tuple[pd.DatetimeIndex, pd.DatetimeIndex]], pd.DatetimeIndex]:
    """Return fixed-width rolling train/OOS folds and a final untouched holdout."""
    if min(train_bars, oos_bars, holdout_bars) < 1:
        raise ValueError("split sizes must be positive")
    if not isinstance(index, pd.DatetimeIndex) or not index.is_monotonic_increasing:
        raise ValueError("split index must be a chronological DatetimeIndex")
    development_size = len(index) - holdout_bars
    if development_size < train_bars + oos_bars:
        raise ValueError("insufficient bars for train, OOS, and holdout")
    holdout = index[development_size:]
    folds = []
    for start in range(0, development_size - train_bars - oos_bars + 1, oos_bars):
        train = index[start : start + train_bars]
        oos = index[start + train_bars : start + train_bars + oos_bars]
        folds.append((train, oos))
    return folds, holdout


def evaluate_walk_forward(
    one_hour: Mapping[str, pd.DataFrame],
    fifteen_minute: Mapping[str, pd.DataFrame],
    *,
    candidates: Sequence[Mapping[str, Any]],
    strategy: Callable[
        [Mapping[str, pd.DataFrame], Mapping[str, pd.DataFrame], Mapping[str, Any]],
        Iterable[TradeIntent],
    ],
    config: EvaluationConfig,
    train_bars: int,
    oos_bars: int,
    holdout_bars: int,
    evaluation_as_of: pd.Timestamp | None = None,
) -> Dict[str, Any]:
    """Evaluate train-selected candidates on isolated OOS folds and holdout."""
    if not candidates:
        raise ValueError("at least one candidate is required")
    if evaluation_as_of is None:
        raise ValueError("evaluation_as_of is required for direct DataFrame evaluation")
    evaluation_as_of = _utc_timestamp(evaluation_as_of, "evaluation_as_of")
    hour = _validate_timeframe_map(one_hour, "1h", evaluation_as_of)
    quarter = _validate_timeframe_map(fifteen_minute, "15m", evaluation_as_of)
    if set(hour) != set(quarter):
        raise ValueError("1h and 15m symbol sets must match")
    common_index = hour[next(iter(sorted(hour)))].index
    for frame in hour.values():
        common_index = common_index.intersection(frame.index)
    if common_index.empty:
        raise ValueError("1h symbol inputs have no common evaluation window")
    required_start = common_index[0]
    required_end = common_index[-1] + _TIMEFRAMES["1h"]
    for symbol, frame in quarter.items():
        if (
            frame.index[0] > required_start
            or frame.index[-1] + _TIMEFRAMES["15m"] < required_end
        ):
            raise ValueError(
                f"15m coverage for {symbol} must span the complete 1h evaluation window"
            )
    folds, holdout_index = rolling_splits(
        common_index,
        train_bars=train_bars,
        oos_bars=oos_bars,
        holdout_bars=holdout_bars,
    )
    candidate_rows = [
        (
            copy.deepcopy(dict(candidate)),
            _config_hash(
                {
                    "candidate": copy.deepcopy(dict(candidate)),
                    "execution": asdict(config),
                    "train_bars": train_bars,
                    "oos_bars": oos_bars,
                    "holdout_bars": holdout_bars,
                }
            ),
        )
        for candidate in candidates
    ]
    candidate_rows.sort(key=lambda row: row[1])
    trials: List[Dict[str, Any]] = []
    oos_results: List[Dict[str, Any]] = []
    selected_hashes: List[str] = []

    for fold_number, (train_index, oos_index) in enumerate(folds):
        scored = []
        train_hour, train_quarter = _slice_period(hour, quarter, train_index)
        for candidate, config_hash in candidate_rows:
            result = simulate_portfolio(
                train_quarter,
                _causal_strategy_intents(
                    strategy,
                    hour,
                    quarter,
                    candidate,
                    train_index,
                    history_start=train_index[0],
                ),
                config,
                evaluation_as_of=evaluation_as_of,
            )
            metrics = compute_metrics(
                result["trades"],
                result["equity"],
                trial_count=len(candidate_rows),
                evaluation_start=train_index[0],
                evaluation_end=train_index[-1] + _TIMEFRAMES["1h"],
            )
            scored.append((metrics["net_pnl"], config_hash, candidate))
            trials.append(
                _trial_record(
                    fold_number,
                    "train",
                    candidate,
                    config_hash,
                    train_index,
                    oos_index[0],
                    metrics,
                )
            )
        _, selected_hash, selected = max(scored, key=lambda row: (row[0], -int(row[1], 16)))
        selected_hashes.append(selected_hash)
        oos_hour, oos_quarter = _slice_period(
            hour, quarter, oos_index, history_start=train_index[0]
        )
        result = simulate_portfolio(
            oos_quarter,
            _causal_strategy_intents(
                strategy,
                hour,
                quarter,
                selected,
                oos_index,
                history_start=train_index[0],
            ),
            config,
            evaluation_as_of=evaluation_as_of,
        )
        metrics = compute_metrics(
            result["trades"],
            result["equity"],
            trial_count=len(candidate_rows),
            evaluation_start=oos_index[0],
            evaluation_end=oos_index[-1] + _TIMEFRAMES["1h"],
        )
        oos_results.append(result)
        trials.append(
            _trial_record(
                fold_number,
                "oos",
                selected,
                selected_hash,
                oos_index,
                holdout_index[0],
                metrics,
            )
        )

    selected_hash = sorted(
        set(selected_hashes), key=lambda value: (-selected_hashes.count(value), value)
    )[0]
    selected = next(row[0] for row in candidate_rows if row[1] == selected_hash)
    holdout_hour, holdout_quarter = _slice_period(
        hour, quarter, holdout_index, history_start=common_index[0]
    )
    holdout_result = simulate_portfolio(
        holdout_quarter,
        _causal_strategy_intents(
            strategy,
            hour,
            quarter,
            selected,
            holdout_index,
            history_start=common_index[0],
        ),
        config,
        evaluation_as_of=evaluation_as_of,
    )
    fold_pnls = [sum(trade["net_pnl"] for trade in result["trades"]) for result in oos_results]
    combined_trades = [
        trade for result in oos_results for trade in result["trades"]
    ]
    combined_equity = _combine_fold_equity(
        oos_results,
        config.initial_equity,
    )
    metrics = compute_metrics(
        combined_trades,
        combined_equity,
        fold_pnls=fold_pnls,
        trial_count=len(candidate_rows) * len(folds),
        evaluation_start=folds[0][1][0],
        evaluation_end=holdout_index[0],
    )
    metrics["portfolio"] = _combined_portfolio_information(
        combined_trades, oos_results
    )
    holdout_metrics = compute_metrics(
        holdout_result["trades"],
        holdout_result["equity"],
        trial_count=len(candidate_rows),
        evaluation_start=holdout_index[0],
        evaluation_end=holdout_index[-1] + _TIMEFRAMES["1h"],
    )
    gates = _target_gates(metrics, holdout_metrics)
    return _json_safe(
        {
            "schema_version": 1,
            "engine": "offline_walk_forward_1h_15m",
            "evaluation_as_of": evaluation_as_of.isoformat(),
            "config": asdict(config),
            "research_config_hash": _config_hash(
                {
                    "candidates": [dict(candidate) for candidate in candidates],
                    "execution": asdict(config),
                    "train_bars": train_bars,
                    "oos_bars": oos_bars,
                    "holdout_bars": holdout_bars,
                }
            ),
            "selected_config_hash": selected_hash,
            "selected_candidate": selected,
            "folds": len(folds),
            "trials": trials,
            "metrics": metrics,
            "holdout": {
                "start": holdout_index[0].isoformat(),
                "end": holdout_index[-1].isoformat(),
                "config_hash": selected_hash,
                **holdout_metrics,
                "portfolio": holdout_result["portfolio"],
            },
            "target_gates": gates,
            "promotion_performed": False,
        }
    )


def deterministic_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Serialize a manifest to stable, strict JSON bytes."""
    return (
        json.dumps(
            _json_safe(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _trial_record(
    fold: int,
    phase: str,
    candidate: Mapping[str, Any],
    config_hash: str,
    evaluation_index: pd.DatetimeIndex,
    next_boundary: pd.Timestamp,
    metrics: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "fold": fold,
        "phase": phase,
        "candidate": copy.deepcopy(dict(candidate)),
        "config_hash": config_hash,
        "evaluation_start": evaluation_index[0].isoformat(),
        "evaluation_end": evaluation_index[-1].isoformat(),
        "oos_start": next_boundary.isoformat(),
        "metrics": dict(metrics),
    }


def _causal_strategy_intents(
    strategy: Callable[
        [Mapping[str, pd.DataFrame], Mapping[str, pd.DataFrame], Mapping[str, Any]],
        Iterable[TradeIntent],
    ],
    hour: Mapping[str, pd.DataFrame],
    quarter: Mapping[str, pd.DataFrame],
    params: Mapping[str, Any],
    evaluation_index: pd.DatetimeIndex,
    *,
    history_start: pd.Timestamp,
) -> List[TradeIntent]:
    """Invoke a legacy strategy callback through a causal bar-open view.

    At each 15m decision open, hourly rows are limited to fully closed bars.
    The current 15m row exposes its known open while high/low/close/volume are
    masked; no later row is present. Only intents for that exact open survive.
    """
    start = evaluation_index[0]
    end = evaluation_index[-1] + _TIMEFRAMES["1h"]
    decision_times = quarter[next(iter(sorted(quarter)))].index
    decision_times = decision_times[(decision_times >= start) & (decision_times < end)]
    accepted: List[TradeIntent] = []
    seen = set()
    for decision_time in decision_times:
        visible_hour = {
            symbol: frame.loc[
                (frame.index >= history_start)
                & (frame.index + _TIMEFRAMES["1h"] <= decision_time)
            ].copy()
            for symbol, frame in hour.items()
        }
        if any(frame.empty for frame in visible_hour.values()):
            continue
        visible_quarter = {}
        for symbol, frame in quarter.items():
            visible = frame.loc[
                (frame.index >= history_start) & (frame.index <= decision_time)
            ].copy()
            if decision_time in visible.index:
                visible.loc[
                    decision_time,
                    ["high", "low", "close", "volume"],
                ] = np.nan
            visible_quarter[symbol] = visible
        if any(frame.empty for frame in visible_quarter.values()):
            continue
        callback_params = copy.deepcopy(dict(params))
        for intent in strategy(visible_hour, visible_quarter, callback_params):
            if intent.time != decision_time:
                continue
            key = (intent.symbol, intent.time, intent.venue, intent.side)
            if key not in seen:
                accepted.append(intent)
                seen.add(key)
    return accepted


def _slice_period(
    hour: Mapping[str, pd.DataFrame],
    quarter: Mapping[str, pd.DataFrame],
    period: pd.DatetimeIndex,
    *,
    history_start: pd.Timestamp | None = None,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    start, end = period[0], period[-1] + _TIMEFRAMES["1h"]
    context_start = start if history_start is None else history_start
    hour_slice = {
        symbol: frame.loc[
            (frame.index >= context_start) & (frame.index <= period[-1])
        ].copy()
        for symbol, frame in hour.items()
    }
    quarter_slice = {
        symbol: frame.loc[(frame.index >= start) & (frame.index < end)].copy()
        for symbol, frame in quarter.items()
    }
    if any(frame.empty for frame in hour_slice.values()) or any(
        frame.empty for frame in quarter_slice.values()
    ):
        raise ValueError("each fold requires 1h and 15m bars for every symbol")
    return hour_slice, quarter_slice


def _validate_timeframe_map(
    frames: Mapping[str, pd.DataFrame],
    timeframe: str,
    evaluation_as_of: pd.Timestamp,
) -> Dict[str, pd.DataFrame]:
    if not frames:
        raise ValueError(f"{timeframe} bars are required")
    output = {}
    for symbol, frame in sorted(frames.items()):
        output[str(symbol)] = prepare_closed_bars(
            frame,
            timeframe,
            as_of=evaluation_as_of,
        )
    return output


def _validate_portfolio_bars(
    frames: Mapping[str, pd.DataFrame],
    evaluation_as_of: pd.Timestamp,
) -> Dict[str, pd.DataFrame]:
    return _validate_timeframe_map(frames, "15m", evaluation_as_of)


def _utc_timestamp(value: Any, name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return timestamp.tz_convert("UTC")


def _optional_fraction(value: Any, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not 0 < number < 1:
        raise ValueError(f"{name} must be between zero and one")
    return number


def _config_hash(config: Mapping[str, Any]) -> str:
    encoded = deterministic_json_bytes(dict(config))
    return hashlib.sha256(encoded).hexdigest()[:16]


def _combine_fold_equity(
    results: Sequence[Mapping[str, Any]],
    initial_equity: float,
) -> pd.Series:
    """Rebase and concatenate fold simulator curves without losing MTM paths."""
    return _combine_equity_series(
        [result["equity"] for result in results],
        initial_equity,
    )


def _combine_equity_series(
    curves: Sequence[pd.Series],
    initial_equity: float,
) -> pd.Series:
    pieces = []
    running_equity = float(initial_equity)
    for raw_curve in curves:
        curve = raw_curve.sort_index().astype(float)
        rebased = curve - float(initial_equity) + running_equity
        pieces.append(rebased)
        running_equity = float(rebased.iloc[-1])
    if not pieces:
        raise ValueError("at least one fold equity curve is required")
    combined = pd.concat(pieces).sort_index()
    return combined.loc[~combined.index.duplicated(keep="last")]


def _portfolio_information(
    trades: Sequence[Mapping[str, Any]],
    concurrent_counts: Sequence[int],
    limit: int,
    symbol_equity: Mapping[str, pd.Series],
) -> Dict[str, Any]:
    exposure_observations = sum(value > 0 for value in concurrent_counts)
    overlap_observations = sum(value > 1 for value in concurrent_counts)
    return {
        "max_concurrent_positions": int(max(concurrent_counts, default=0)),
        "position_limit": int(limit),
        "exposure_observations": int(exposure_observations),
        "overlap_observations": int(overlap_observations),
        "overlap_fraction": (
            float(overlap_observations / exposure_observations)
            if exposure_observations
            else 0.0
        ),
        **compute_portfolio_correlations(trades, symbol_equity),
    }


def _combined_portfolio_information(
    trades: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    portfolios = [result["portfolio"] for result in results]
    exposure_observations = sum(
        row["exposure_observations"] for row in portfolios
    )
    overlap_observations = sum(
        row["overlap_observations"] for row in portfolios
    )
    symbols = sorted(
        {
            symbol
            for result in results
            for symbol in result["symbol_equity"]
        }
    )
    combined_symbol_equity = {
        symbol: _combine_equity_series(
            [
                result["symbol_equity"][symbol]
                for result in results
                if symbol in result["symbol_equity"]
            ],
            float(next(iter(results))["equity"].iloc[0]),
        )
        for symbol in symbols
    }
    return {
        "max_concurrent_positions": max(
            (row["max_concurrent_positions"] for row in portfolios), default=0
        ),
        "exposure_observations": int(exposure_observations),
        "overlap_observations": int(overlap_observations),
        "overlap_fraction": (
            float(overlap_observations / exposure_observations)
            if exposure_observations
            else 0.0
        ),
        **compute_portfolio_correlations(trades, combined_symbol_equity),
    }


def compute_portfolio_correlations(
    trades: Sequence[Mapping[str, Any]],
    symbol_equity: Mapping[str, pd.Series],
) -> Dict[str, Any]:
    """Return separately labeled realized-PnL and MTM equity-return correlations."""
    return_series = {}
    for symbol, raw_curve in sorted(symbol_equity.items()):
        curve = raw_curve.sort_index().astype(float)
        if len(curve) < 2:
            continue
        calendar = pd.date_range(
            curve.index[1].floor("D"),
            curve.index[-1].floor("D"),
            freq="D",
            tz="UTC",
        )
        return_series[str(symbol)] = _daily_mtm_returns(curve, calendar)
    return_frame = pd.DataFrame(return_series)
    return {
        "daily_pnl_correlation_label": "daily_realized_net_pnl_usd",
        "daily_pnl_correlation": _pnl_correlations(trades),
        "daily_return_correlation_label": "daily_mark_to_market_equity_returns",
        "daily_return_correlation": _frame_correlations(return_frame),
    }


def _frame_correlations(frame: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    if frame.empty:
        return {}
    corr = frame.corr(min_periods=2)
    return {
        str(left): {
            str(right): float(value)
            for right, value in corr.loc[left].items()
            if np.isfinite(value)
        }
        for left in corr.index
    }


def _pnl_correlations(trades: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, float]]:
    if not trades:
        return {}
    rows = pd.DataFrame(
        {
            "date": [pd.Timestamp(row["exit_time"]).floor("D") for row in trades],
            "symbol": [row["symbol"] for row in trades],
            "pnl": [float(row["net_pnl"]) for row in trades],
        }
    )
    pivot = rows.pivot_table(index="date", columns="symbol", values="pnl", aggfunc="sum")
    pivot = pivot.fillna(0.0)
    return _frame_correlations(pivot)


def _target_gates(
    metrics: Mapping[str, Any], holdout: Mapping[str, Any]
) -> Dict[str, bool]:
    profit_factor = metrics.get("profit_factor")
    pf_pass = bool(metrics.get("profit_factor_infinite")) or (
        isinstance(profit_factor, (int, float)) and profit_factor >= 1.25
    )
    gates = {
        "rolling_30d_average_pnl_at_least_50": metrics[
            "rolling_30d_average_pnl"
        ]
        >= 50,
        "max_drawdown_percent_at_most_5": metrics["max_drawdown_percent"] <= 0.05,
        "profit_factor_at_least_1_25": bool(pf_pass),
        "positive_fold_ratio_at_least_70_percent": metrics["positive_fold_ratio"]
        >= 0.70,
        "positive_holdout": holdout["net_pnl"] > 0,
    }
    gates["all_passed"] = all(gates.values())
    gates["promotion_performed"] = False
    return gates


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value
