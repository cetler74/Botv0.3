"""Rule-level validation for rsi_stoch fast/live payloads."""

from datetime import datetime, timedelta, timezone

from strategy.fast_signal_cache import validate_rsi_stoch_actionable
from strategy.playbooks.rsi_stoch_reversal_5m_engine import EngineParams


def _recent_bar() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()


def _payload(signal: str, **ind) -> dict:
    return {"signal": signal, "indicators": ind}


def test_validator_rejects_short_when_stoch_not_overbought():
    ok, reason = validate_rsi_stoch_actionable(
        _payload(
            "short",
            entry_reason="stoch_overbought_cross",
            rsi=56.0,
            stoch_rsi_k=77.29,
            stoch_rsi_d=87.08,
            bar_close_time=_recent_bar(),
        ),
        allow_short=True,
        params=EngineParams(stoch_overbought=80.0),
    )
    assert not ok
    assert reason == "short_rules_failed"


def test_validator_accepts_long_fixture():
    ok, reason = validate_rsi_stoch_actionable(
        _payload(
            "long",
            entry_reason="rsi_oversold_stoch_cross",
            rsi=28.0,
            stoch_rsi_k=22.0,
            stoch_rsi_d=18.0,
            bar_close_time=_recent_bar(),
        ),
        allow_short=False,
        params=EngineParams(),
    )
    assert ok
    assert reason == "long_ok"


def test_validator_accepts_short_fixture():
    ok, reason = validate_rsi_stoch_actionable(
        _payload(
            "short",
            entry_reason="stoch_overbought_cross",
            rsi=55.0,
            stoch_rsi_k=85.0,
            stoch_rsi_d=90.0,
            bar_close_time=_recent_bar(),
        ),
        allow_short=True,
        params=EngineParams(stoch_overbought=80.0),
    )
    assert ok
    assert reason == "short_ok"


def test_validator_rejects_without_entry_reason():
    ok, reason = validate_rsi_stoch_actionable(
        _payload(
            "long",
            skip_reason="regime_low_volatility",
            rsi=20.0,
            stoch_rsi_k=15.0,
            stoch_rsi_d=18.0,
        ),
        allow_short=False,
    )
    assert not ok
    assert "entry_reason" in reason or "skip_reason" in reason
