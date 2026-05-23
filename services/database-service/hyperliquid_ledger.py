"""Local-dev shim; Docker build overwrites with core/hyperliquid_ledger.py."""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.hyperliquid_ledger import compute_hyperliquid_balance_amounts  # noqa: F401

__all__ = ["compute_hyperliquid_balance_amounts"]
