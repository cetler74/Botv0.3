"""Timezone-aware session windows for day-trading filters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


@dataclass
class SessionFilterConfig:
    enabled: bool = True
    timezone: str = "Europe/Lisbon"
    block_weekends: bool = True
    blocked_dates: Optional[List[str]] = None
    preferred_windows: Optional[List[Dict[str, Any]]] = None
    blocked_windows: Optional[List[Dict[str, Any]]] = None
    require_preferred: bool = False


def _parse_time(value: str) -> time:
    parts = str(value).strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return time(h, m)


def _day_name(dt: datetime) -> str:
    return dt.strftime("%a").lower()[:3]  # mon, tue, ...


def _in_window(dt: datetime, window: Dict[str, Any]) -> bool:
    day = str(window.get("day", "")).lower()[:3]
    if day and _day_name(dt) != day:
        return False
    start = _parse_time(str(window.get("start", "00:00")))
    end = _parse_time(str(window.get("end", "23:59")))
    t = dt.time()
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def default_lisbon_session_config() -> SessionFilterConfig:
    """Mon night through Thu morning; block Mon AM, Fri PM, weekends."""
    return SessionFilterConfig(
        enabled=True,
        timezone="Europe/Lisbon",
        block_weekends=True,
        blocked_dates=[],
        preferred_windows=[
            {"day": "mon", "start": "20:00", "end": "23:59"},
            {"day": "tue", "start": "00:00", "end": "23:59"},
            {"day": "wed", "start": "00:00", "end": "23:59"},
            {"day": "thu", "start": "00:00", "end": "12:00"},
        ],
        blocked_windows=[
            {"day": "mon", "start": "00:00", "end": "12:00"},
            {"day": "fri", "start": "12:00", "end": "23:59"},
        ],
        require_preferred=False,
    )


def session_filter_from_params(params: Dict[str, Any]) -> SessionFilterConfig:
    raw = params.get("session_filter") or {}
    if not isinstance(raw, dict):
        raw = {}
    if not raw and params.get("session_tz"):
        cfg = default_lisbon_session_config()
        cfg.timezone = str(params.get("session_tz", "Europe/Lisbon"))
        return cfg
    return SessionFilterConfig(
        enabled=bool(raw.get("enabled", True)),
        timezone=str(raw.get("timezone") or params.get("session_tz", "Europe/Lisbon")),
        block_weekends=bool(raw.get("block_weekends", True)),
        blocked_dates=list(raw.get("blocked_dates") or []),
        preferred_windows=list(raw.get("preferred_windows") or default_lisbon_session_config().preferred_windows),
        blocked_windows=list(raw.get("blocked_windows") or default_lisbon_session_config().blocked_windows),
        require_preferred=bool(raw.get("require_preferred", False)),
    )


def is_session_allowed(
    now: Optional[datetime] = None,
    cfg: Optional[SessionFilterConfig] = None,
) -> tuple[bool, str]:
    """Return (allowed, reason_code)."""
    cfg = cfg or default_lisbon_session_config()
    if not cfg.enabled:
        return True, "session_filter_disabled"
    tz = ZoneInfo(cfg.timezone)
    dt = (now or datetime.utcnow()).astimezone(tz)
    iso_date = dt.date().isoformat()
    if cfg.blocked_dates and iso_date in cfg.blocked_dates:
        return False, "session_blocked_holiday"
    if cfg.block_weekends and dt.weekday() >= 5:
        return False, "session_blocked_weekend"
    for w in cfg.blocked_windows or []:
        if _in_window(dt, w):
            return False, "session_blocked_window"
    if cfg.require_preferred:
        for w in cfg.preferred_windows or []:
            if _in_window(dt, w):
                return True, "session_preferred"
        return False, "session_outside_preferred"
    return True, "session_ok"
