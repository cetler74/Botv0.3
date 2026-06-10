"""Tests for setup target resolution floor."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "services" / "orchestrator-service"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from hyperliquid_perps import resolve_setup_target_pct  # noqa: E402


def test_resolve_setup_target_pct_applies_min_floor():
    setup = {"entry_price": 100.0, "target_hint": 100.2}  # 0.2% target
    assert resolve_setup_target_pct(setup, 100.0, min_target_pct=0.0) == pytest.approx(0.2)
    assert resolve_setup_target_pct(setup, 100.0, min_target_pct=1.2) == pytest.approx(1.2)
