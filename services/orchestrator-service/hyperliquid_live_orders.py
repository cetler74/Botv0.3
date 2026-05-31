"""
Hyperliquid live perp orders (rsi_stoch_reversal_5m only).

Requires HYPERLIQUID_PRIVATE_KEY (or HYPERLIQUID_API_SECRET) in the environment.
Paper ledger (trading.perp_paper_trades) is never written from this module.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

STRATEGY_KEY = "rsi_stoch_reversal_5m"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"


def _private_key() -> Optional[str]:
    for name in ("HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_API_SECRET", "HL_PRIVATE_KEY"):
        val = os.getenv(name, "").strip()
        if val:
            return val
    return None


def is_live_trading_configured() -> bool:
    return bool(_private_key())


class HyperliquidLiveOrderExecutor:
  """Place and track live HL perp orders for allowlisted strategies."""

  def __init__(
      self,
      cfg: Dict[str, Any],
      *,
      database_service_url: str,
  ) -> None:
      self.cfg = cfg or {}
      self.database_service_url = database_service_url.rstrip("/")
      self._exchange = None

  def _allowlisted(self, strategy: str) -> bool:
      allow = [
          str(s).strip().lower()
          for s in (self.cfg.get("live_strategy_allowlist") or [STRATEGY_KEY])
      ]
      return str(strategy or "").strip().lower() in allow

  def _exchange_client(self):
      if self._exchange is not None:
          return self._exchange
      key = _private_key()
      if not key:
          raise RuntimeError("HYPERLIQUID_PRIVATE_KEY not set")
      try:
          import eth_account
          from hyperliquid.exchange import Exchange
          from hyperliquid.utils import constants

          wallet = eth_account.Account.from_key(key)
          base_url = os.getenv("HYPERLIQUID_API_URL", constants.MAINNET_API_URL)
          self._exchange = Exchange(wallet, base_url)
          return self._exchange
      except ImportError as exc:
          raise RuntimeError(
              "hyperliquid-python-sdk and eth-account required for live orders"
          ) from exc

  async def open_position(
      self,
      *,
      coin: str,
      side: str,
      size: float,
      strategy: str,
      trade_payload: Dict[str, Any],
  ) -> Dict[str, Any]:
      if bool(self.cfg.get("live_kill_switch", False)):
          return {"ok": False, "error": "live_kill_switch"}
      if not self._allowlisted(strategy):
          return {"ok": False, "error": "strategy_not_allowlisted"}
      if strategy != STRATEGY_KEY:
          return {"ok": False, "error": "only_rsi_stoch_live_supported"}

      max_margin = float(self.cfg.get("live_max_margin_per_trade", 25) or 25)
      margin_used = float(trade_payload.get("margin_used") or 0)
      if margin_used > max_margin:
          return {"ok": False, "error": f"margin {margin_used} > cap {max_margin}"}

      is_buy = str(side).lower() == "long"
      try:
          ex = self._exchange_client()
          result = ex.market_open(str(coin).upper(), is_buy, float(size), None, 0.01)
          record = dict(trade_payload)
          record["mode"] = "live"
          record["metadata"] = {
              **(trade_payload.get("metadata") or {}),
              "hl_exchange_result": result,
          }
          await self._persist_live_trade(record)
          trade_id = record.get("trade_id")
          logger.warning(
              "[HyperliquidLive] OPEN %s %s size=%.6f strategy=%s",
              side,
              coin,
              size,
              strategy,
          )
          return {"ok": True, "trade_id": trade_id, "exchange": result}
      except Exception as exc:
          logger.error("[HyperliquidLive] open failed %s %s: %s", coin, side, exc)
          return {"ok": False, "error": str(exc)}

  async def close_position(
      self,
      *,
      coin: str,
      side: str,
      size: float,
      trade_id: str,
      exit_reason: str,
  ) -> Dict[str, Any]:
      if bool(self.cfg.get("live_kill_switch", False)):
          return {"ok": False, "error": "live_kill_switch"}
      is_buy = str(side).lower() == "short"
      try:
          ex = self._exchange_client()
          result = ex.market_close(str(coin).upper(), is_buy, float(size), None, 0.01)
          await self._update_live_trade(
              trade_id,
              {
                  "status": "CLOSED",
                  "exit_time": datetime.now(timezone.utc).isoformat(),
                  "exit_reason": exit_reason,
                  "metadata_patch": {"hl_close_result": result},
              },
          )
          logger.warning(
              "[HyperliquidLive] CLOSE %s %s trade=%s reason=%s",
              side,
              coin,
              trade_id,
              exit_reason,
          )
          return {"ok": True, "exchange": result}
      except Exception as exc:
          logger.error("[HyperliquidLive] close failed %s: %s", coin, exc)
          return {"ok": False, "error": str(exc)}

  async def _persist_live_trade(self, record: Dict[str, Any]) -> None:
      async with httpx.AsyncClient(timeout=30.0) as client:
          resp = await client.post(
              f"{self.database_service_url}/api/v1/perps/live-trades",
              json=record,
          )
          if resp.status_code not in (200, 201):
              logger.warning(
                  "[HyperliquidLive] live trade persist %s: %s",
                  resp.status_code,
                  resp.text[:300],
              )

  async def _update_live_trade(self, trade_id: str, patch: Dict[str, Any]) -> None:
      async with httpx.AsyncClient(timeout=30.0) as client:
          await client.put(
              f"{self.database_service_url}/api/v1/perps/live-trades/{trade_id}",
              json=patch,
          )

  async def fetch_open_live_trades(self) -> List[Dict[str, Any]]:
      async with httpx.AsyncClient(timeout=30.0) as client:
          resp = await client.get(
              f"{self.database_service_url}/api/v1/perps/live-trades/open",
          )
          if resp.status_code != 200:
              return []
          return (resp.json() or {}).get("trades") or []
