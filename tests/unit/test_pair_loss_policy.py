"""Unit tests for pair loss cooldown helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "services" / "orchestrator-service"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from pair_loss_policy import (  # noqa: E402
    loss_scaled_cooldown_hours,
    qualifies_for_hard_loss_cooldown,
)


TIERED_CFG = {
    "enabled": True,
    "base_hours": 1.0,
    "max_hours": 12.0,
    "tiers": [
        {"min_loss_pct": 0.0, "hours": 1},
        {"min_loss_pct": -0.5, "hours": 2},
        {"min_loss_pct": -1.0, "hours": 4},
        {"min_loss_pct": -1.5, "hours": 8},
    ],
}


def test_qualifies_for_hard_loss_any_strict_loss():
    assert qualifies_for_hard_loss_cooldown(-0.01, 0.0) is True
    assert qualifies_for_hard_loss_cooldown(0.0, 0.0) is False


def test_loss_scaled_cooldown_tiers():
    assert loss_scaled_cooldown_hours(0.5, TIERED_CFG) == 0.0
    assert loss_scaled_cooldown_hours(-0.2, TIERED_CFG) == 1.0
    assert loss_scaled_cooldown_hours(-0.8, TIERED_CFG) == 2.0
    assert loss_scaled_cooldown_hours(-1.2, TIERED_CFG) == 4.0
    assert loss_scaled_cooldown_hours(-2.0, TIERED_CFG) == 8.0


def test_loss_scaled_cooldown_respects_max_hours():
    cfg = {**TIERED_CFG, "max_hours": 6.0, "tiers": [{"min_loss_pct": -1.5, "hours": 8}]}
    assert loss_scaled_cooldown_hours(-3.0, cfg) == 6.0
