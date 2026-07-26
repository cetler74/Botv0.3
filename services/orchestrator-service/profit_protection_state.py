"""Profit-protection / setup-milestone state helpers for spot and paper-perp exits.

Design choice (armed floor breach): once a floor is armed
(``profit_guaranteed`` / ``setup_breakeven`` / milestone with trigger), a mark
at or below that floor must EXIT. LOSS-GUARD / NET-GUARD must not strand an
armed trade below entry or below ``min_net_profit_usd`` — those guards only
apply when deciding whether to *arm*. Prefer executable protection over
preserving an already-breached floor state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

# Locked floors that must not be overwritten by milestones.
LOCKED_PROFIT_PROTECTION_STATES = frozenset(
    {
        "profit_guaranteed",
        "setup_breakeven",
    }
)

# Milestone markers (not a locked floor). Legacy setup_partial is accepted.
MILESTONE_PROFIT_PROTECTION_STATES = frozenset(
    {
        "target_progress",
        "setup_partial",  # legacy alias
    }
)

# Canonical milestone label written going forward.
TARGET_PROGRESS_STATE = "target_progress"

# Exit-reason tokens (distinct) + legacy alias for dashboards.
EXIT_REASON_PROFIT_GUARANTEED_BREACH = "profit_guaranteed_breach"
EXIT_REASON_TARGET_PROGRESS_BREACH = "target_progress_breach"
EXIT_REASON_SETUP_BREAKEVEN_BREACH = "setup_breakeven_breach"
EXIT_REASON_PROFIT_PROTECTION_LATE_BREACH = "profit_protection_late_breach"
EXIT_REASON_PROFIT_PROTECTION_BREACH_LEGACY = "profit_protection_breach"


def normalize_profit_protection_state(status: Any) -> str:
    return str(status or "").strip().lower()


def is_profit_protection_locked(status: Any) -> bool:
    """True when a real floor is armed (blocks re-arming / milestone overwrite)."""
    return normalize_profit_protection_state(status) in LOCKED_PROFIT_PROTECTION_STATES


def is_setup_milestone(status: Any) -> bool:
    return normalize_profit_protection_state(status) in MILESTONE_PROFIT_PROTECTION_STATES


def can_arm_profit_protection(status: Any) -> bool:
    """Milestone / inactive / empty may upgrade to profit_guaranteed."""
    state = normalize_profit_protection_state(status)
    if not state or state == "inactive":
        return True
    if state in MILESTONE_PROFIT_PROTECTION_STATES:
        return True
    return False


def is_feature_enabled(config: Optional[Mapping[str, Any]], *, default: bool = True) -> bool:
    """Honor explicit ``enabled: false``; missing key keeps ``default``."""
    if not isinstance(config, Mapping):
        return bool(default)
    if "enabled" not in config:
        return bool(default)
    return bool(config.get("enabled"))


def _safe_nonneg_decimal(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value if value is not None else default)
    except (TypeError, ValueError):
        num = float(default or 0.0)
    if num < 0:
        return 0.0
    return num


def resolve_profit_lock_floor_decimal(
    trailing_stop_config: Optional[Mapping[str, Any]] = None,
    profit_protection_config: Optional[Mapping[str, Any]] = None,
    *,
    default_floor: float = 0.003,
) -> float:
    """Effective lock floor = max(breakeven_floor, guaranteed_min, break_even_plus).

    Ensures ``guaranteed_min_profit`` / ``break_even_plus`` knobs actually raise
    the armed floor when they exceed the trail breakeven floor.
    """
    trail = trailing_stop_config if isinstance(trailing_stop_config, Mapping) else {}
    pp = profit_protection_config if isinstance(profit_protection_config, Mapping) else {}
    breakeven = _safe_nonneg_decimal(
        trail.get("breakeven_floor_percentage"), default_floor
    )
    guaranteed = _safe_nonneg_decimal(pp.get("guaranteed_min_profit"), 0.0)
    break_even_plus = _safe_nonneg_decimal(pp.get("break_even_plus"), 0.0)
    floor = max(breakeven, guaranteed, break_even_plus)
    if floor <= 0:
        floor = max(0.0, float(default_floor or 0.0))
    return floor


def floor_trigger_price(entry_price: float, floor_decimal: float, *, side: str = "long") -> float:
    entry = float(entry_price or 0.0)
    floor = max(0.0, float(floor_decimal or 0.0))
    if entry <= 0:
        return 0.0
    side_l = str(side or "long").strip().lower()
    if side_l == "short":
        return entry * (1.0 - floor)
    return entry * (1.0 + floor)


def is_price_at_or_through_floor(
    current_price: float,
    floor_price: float,
    *,
    side: str = "long",
) -> bool:
    mark = float(current_price or 0.0)
    floor = float(floor_price or 0.0)
    if mark <= 0 or floor <= 0:
        return False
    side_l = str(side or "long").strip().lower()
    if side_l == "short":
        return mark >= floor
    return mark <= floor


@dataclass(frozen=True)
class ProfitProtectionArmDecision:
    """Result of evaluating whether to arm profit protection at this tick."""

    action: str  # "arm" | "late_exit" | "skip"
    floor_price: float = 0.0
    floor_decimal: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class TieredProfitLockDecision:
    """Highest earned peak-lock tier and the action needed for its stop floor."""

    action: str  # "raise" | "late_exit" | "skip"
    floor_price: float = 0.0
    floor_decimal: float = 0.0
    activation_decimal: float = 0.0
    tier_index: int = -1
    reason: str = ""


def evaluate_tiered_profit_lock(
    *,
    config: Optional[Mapping[str, Any]],
    peak_pct: float,
    entry_price: float,
    current_price: float,
    existing_trigger: Any = None,
    side: str = "long",
    minimum_floor_decimal: float = 0.0,
) -> TieredProfitLockDecision:
    """Raise a stop to the highest fee-aware floor earned by peak MFE.

    Tier values are decimals, for example activation=0.0055 means +0.55% MFE.
    If the mark has already crossed a newly earned floor, callers must exit
    immediately instead of persisting a stale lock.
    """
    if not is_feature_enabled(config, default=False):
        return TieredProfitLockDecision(action="skip", reason="tiered_lock_disabled")
    raw_tiers = config.get("tiers") if isinstance(config, Mapping) else None
    if not isinstance(raw_tiers, Sequence) or isinstance(raw_tiers, (str, bytes)):
        return TieredProfitLockDecision(action="skip", reason="no_tiered_lock_tiers")

    qualified = []
    peak_decimal = max(0.0, float(peak_pct or 0.0) / 100.0)
    for index, raw in enumerate(raw_tiers):
        if not isinstance(raw, Mapping):
            continue
        activation = _safe_nonneg_decimal(raw.get("activation"), 0.0)
        floor = max(
            _safe_nonneg_decimal(raw.get("floor"), 0.0),
            _safe_nonneg_decimal(minimum_floor_decimal, 0.0),
        )
        if activation > 0 and floor >= 0 and peak_decimal >= activation:
            qualified.append((activation, floor, index))
    if not qualified:
        return TieredProfitLockDecision(action="skip", reason="peak_below_first_tier")

    activation, floor, index = max(qualified, key=lambda item: (item[0], item[1]))
    desired = floor_trigger_price(entry_price, floor, side=side)
    if desired <= 0:
        return TieredProfitLockDecision(action="skip", reason="invalid_tier_floor")

    try:
        existing = float(existing_trigger or 0.0)
    except (TypeError, ValueError):
        existing = 0.0
    side_l = str(side or "long").strip().lower()
    improves = (
        existing <= 0
        or (side_l == "short" and desired < existing)
        or (side_l != "short" and desired > existing)
    )
    if not improves:
        return TieredProfitLockDecision(
            action="skip",
            floor_price=desired,
            floor_decimal=floor,
            activation_decimal=activation,
            tier_index=index,
            reason="existing_trigger_is_equal_or_better",
        )

    action = (
        "late_exit"
        if is_price_at_or_through_floor(current_price, desired, side=side_l)
        else "raise"
    )
    return TieredProfitLockDecision(
        action=action,
        floor_price=desired,
        floor_decimal=floor,
        activation_decimal=activation,
        tier_index=index,
        reason=f"tier_{index}_{action}",
    )


def evaluate_profit_protection_arm(
    *,
    status: Any,
    peak_pct: float,
    activation_pct: float,
    entry_price: float,
    current_price: float,
    floor_decimal: float,
    trailing_active: bool = False,
    enabled: bool = True,
    side: str = "long",
) -> ProfitProtectionArmDecision:
    """Decide arm / late immediate exit / skip.

    Late-arm zombie prevention: if peak qualifies but mark is already at/below
    the would-be floor, do NOT leave ``profit_guaranteed`` — exit immediately
    with ``profit_protection_late_breach`` (caller sets exit_reason).
    """
    if not enabled:
        return ProfitProtectionArmDecision(action="skip", reason="profit_protection_disabled")
    if trailing_active:
        return ProfitProtectionArmDecision(action="skip", reason="trailing_stop_active")
    if not can_arm_profit_protection(status):
        return ProfitProtectionArmDecision(
            action="skip",
            reason=f"status_blocks_arm:{normalize_profit_protection_state(status) or 'locked'}",
        )
    if float(peak_pct or 0.0) < float(activation_pct or 0.0):
        return ProfitProtectionArmDecision(
            action="skip",
            reason=(
                f"peak {float(peak_pct or 0.0):.2f}% < "
                f"{float(activation_pct or 0.0):.2f}%"
            ),
        )

    floor_px = floor_trigger_price(entry_price, floor_decimal, side=side)
    if floor_px <= 0:
        return ProfitProtectionArmDecision(action="skip", reason="invalid_floor_price")

    if is_price_at_or_through_floor(current_price, floor_px, side=side):
        return ProfitProtectionArmDecision(
            action="late_exit",
            floor_price=floor_px,
            floor_decimal=float(floor_decimal or 0.0),
            reason=EXIT_REASON_PROFIT_PROTECTION_LATE_BREACH,
        )

    return ProfitProtectionArmDecision(
        action="arm",
        floor_price=floor_px,
        floor_decimal=float(floor_decimal or 0.0),
        reason="arm_profit_guaranteed",
    )


def breach_exit_reason_token(status: Any) -> str:
    """Distinct breach token by armed/milestone state (legacy alias kept separately)."""
    state = normalize_profit_protection_state(status)
    if state == "target_progress" or state == "setup_partial":
        return EXIT_REASON_TARGET_PROGRESS_BREACH
    if state == "setup_breakeven":
        return EXIT_REASON_SETUP_BREAKEVEN_BREACH
    if state == "profit_guaranteed":
        return EXIT_REASON_PROFIT_GUARANTEED_BREACH
    return EXIT_REASON_PROFIT_PROTECTION_BREACH_LEGACY


def format_breach_exit_reason(
    status: Any,
    *,
    pnl_percentage: float,
    trigger_price: float,
    current_price: float,
    include_legacy_alias: bool = True,
) -> str:
    """Build exit_reason string with distinct token (+ optional legacy alias)."""
    token = breach_exit_reason_token(status)
    detail = (
        f"{token}@{float(pnl_percentage):.2f}%"
        f"_trigger{float(trigger_price):.6f}_px{float(current_price):.6f}"
    )
    if include_legacy_alias and token != EXIT_REASON_PROFIT_PROTECTION_BREACH_LEGACY:
        # Dashboards that grep the legacy substring still match.
        detail = f"{detail}|{EXIT_REASON_PROFIT_PROTECTION_BREACH_LEGACY}"
    return detail


def format_late_arm_exit_reason(
    *,
    pnl_percentage: float,
    floor_price: float,
    current_price: float,
) -> str:
    return (
        f"{EXIT_REASON_PROFIT_PROTECTION_LATE_BREACH}@{float(pnl_percentage):.2f}%"
        f"_floor{float(floor_price):.6f}_px{float(current_price):.6f}"
        f"|{EXIT_REASON_PROFIT_PROTECTION_BREACH_LEGACY}"
    )


def milestone_floor_price(
    entry_price: float,
    *,
    floor_pct: float = 0.001,
) -> float:
    """Raise stop to a small fee-aware floor above entry when setup target hits 50%."""
    entry = float(entry_price or 0.0)
    if entry <= 0:
        return 0.0
    return entry * (1.0 + max(0.0, float(floor_pct or 0.0)))


def peak_pnl_pct(entry_price: float, highest_price: float, fallback_pct: float = 0.0) -> float:
    entry = float(entry_price or 0.0)
    high = float(highest_price or 0.0)
    if entry <= 0 or high <= 0:
        return float(fallback_pct or 0.0)
    return ((high - entry) / entry) * 100.0


def is_profit_protection_near_miss(
    *,
    peak_pct: float,
    current_pnl_pct: float,
    activation_pct: float,
    status: Any,
    band_pct: float = 0.15,
    giveback_pct: float = 0.30,
) -> bool:
    """Peak reached within band of arm threshold, then faded, without a locked floor."""
    if is_profit_protection_locked(status):
        return False
    act = float(activation_pct or 0.0)
    peak = float(peak_pct or 0.0)
    now = float(current_pnl_pct or 0.0)
    band = max(0.0, float(band_pct or 0.0))
    giveback = max(0.0, float(giveback_pct or 0.0))
    if act <= 0 or peak <= 0:
        return False
    if peak < (act - band) or peak >= act:
        return False
    return now <= (peak - giveback)


def near_miss_metadata_payload(
    *,
    peak_pct: float,
    current_pnl_pct: float,
    activation_pct: float,
    status: Any,
) -> Dict[str, Any]:
    """JSONB patch for dashboard/analytics when a near-miss is detected."""
    return {
        "profit_protection_near_miss": True,
        "profit_protection_near_miss_peak_pct": round(float(peak_pct or 0.0), 4),
        "profit_protection_near_miss_pnl_pct": round(float(current_pnl_pct or 0.0), 4),
        "profit_protection_near_miss_activation_pct": round(float(activation_pct or 0.0), 4),
        "profit_protection_near_miss_status": normalize_profit_protection_state(status)
        or "inactive",
    }


def should_breach_exit_for_status(status: Any) -> bool:
    """Price-breach exits apply to locked floors and milestone floors with a trigger."""
    state = normalize_profit_protection_state(status)
    return state in LOCKED_PROFIT_PROTECTION_STATES or state in MILESTONE_PROFIT_PROTECTION_STATES


def armed_floor_breach_must_exit(status: Any) -> bool:
    """True when LOSS/NET guards must not block an executable floor exit."""
    return should_breach_exit_for_status(status)


def may_reset_profit_protection_for_trail(status: Any) -> bool:
    """Trail activation must never wipe locked or milestone floors."""
    state = normalize_profit_protection_state(status)
    if not state or state == "inactive":
        return True
    if state in LOCKED_PROFIT_PROTECTION_STATES or state in MILESTONE_PROFIT_PROTECTION_STATES:
        return False
    # Unknown non-empty states (e.g. "trailing") — do not force-clear.
    return False


def merge_trail_trigger(existing: Optional[float], candidate: float) -> float:
    """Keep the higher (more protective for longs) trigger price."""
    try:
        prev = float(existing or 0.0)
    except (TypeError, ValueError):
        prev = 0.0
    try:
        nxt = float(candidate or 0.0)
    except (TypeError, ValueError):
        nxt = 0.0
    return max(prev, nxt)


def merge_trail_trigger_for_side(
    existing: Optional[float],
    candidate: float,
    *,
    side: str = "long",
) -> float:
    """Long: higher trigger; short: lower (more protective) trigger."""
    side_l = str(side or "long").strip().lower()
    try:
        prev = float(existing or 0.0)
    except (TypeError, ValueError):
        prev = 0.0
    try:
        nxt = float(candidate or 0.0)
    except (TypeError, ValueError):
        nxt = 0.0
    if side_l == "short":
        if prev <= 0:
            return nxt
        if nxt <= 0:
            return prev
        return min(prev, nxt)
    return max(prev, nxt)
