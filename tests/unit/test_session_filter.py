"""Session filter (Europe/Lisbon defaults)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy.indicators.session_filter import (
    SessionFilterConfig,
    default_lisbon_session_config,
    is_session_allowed,
)


def test_monday_morning_blocked_lisbon():
    cfg = default_lisbon_session_config()
    # Monday 2024-01-08 10:00 Lisbon
    dt = datetime(2024, 1, 8, 10, 0, tzinfo=ZoneInfo("Europe/Lisbon"))
    allowed, reason = is_session_allowed(dt, cfg)
    assert allowed is False
    assert reason == "session_blocked_window"


def test_monday_night_allowed_lisbon():
    cfg = default_lisbon_session_config()
    dt = datetime(2024, 1, 8, 21, 0, tzinfo=ZoneInfo("Europe/Lisbon"))
    allowed, reason = is_session_allowed(dt, cfg)
    assert allowed is True


def test_weekend_blocked():
    cfg = default_lisbon_session_config()
    dt = datetime(2024, 1, 6, 15, 0, tzinfo=ZoneInfo("Europe/Lisbon"))  # Saturday
    allowed, _ = is_session_allowed(dt, cfg)
    assert allowed is False


def test_session_filter_disabled():
    cfg = SessionFilterConfig(enabled=False)
    allowed, reason = is_session_allowed(cfg=cfg)
    assert allowed is True
    assert reason == "session_filter_disabled"
