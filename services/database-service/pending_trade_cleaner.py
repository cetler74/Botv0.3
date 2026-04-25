"""
Periodic cleanup for stale PENDING trades.

Marks trades as FAILED when they stay in PENDING beyond a timeout and still
have no entry_id (never acknowledged as opened).
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


class PendingTradeCleaner:
    """Background cleaner for stale PENDING trades."""

    def __init__(
        self,
        db_manager,
        stale_minutes: int = 60,
        check_interval_seconds: int = 300,
    ) -> None:
        self.db_manager = db_manager
        self.stale_minutes = max(5, int(stale_minutes))
        self.check_interval_seconds = max(30, int(check_interval_seconds))
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self.metrics: Dict[str, Any] = {
            "runs": 0,
            "cleaned_total": 0,
            "last_run_at": None,
            "last_cleaned_count": 0,
            "last_error": None,
        }

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True

        # Run one cleanup immediately on startup.
        await self.run_cleanup_once()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "✅ Pending Trade Cleaner started (stale>%sm, interval=%ss)",
            self.stale_minutes,
            self.check_interval_seconds,
        )

    async def stop(self) -> None:
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Pending Trade Cleaner stopped")

    async def _run_loop(self) -> None:
        while self.is_running:
            try:
                await asyncio.sleep(self.check_interval_seconds)
                await self.run_cleanup_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.metrics["last_error"] = str(e)
                logger.error("❌ Pending Trade Cleaner loop error: %s", e)
                await asyncio.sleep(min(self.check_interval_seconds, 60))

    async def run_cleanup_once(self) -> int:
        cutoff = datetime.utcnow() - timedelta(minutes=self.stale_minutes)
        reason = f"auto_cleanup_pending_timeout_{self.stale_minutes}m"

        try:
            self.metrics["runs"] += 1
            self.metrics["last_run_at"] = datetime.utcnow().isoformat()
            self.metrics["last_error"] = None

            # Count stale pending trades first (execute_query only returns rows for SELECT).
            count_query = """
                SELECT COUNT(*) AS stale_count
                FROM trading.trades
                WHERE status = 'PENDING'
                  AND COALESCE(entry_id, '') = ''
                  AND COALESCE(updated_at, entry_time) < %s
            """
            count_row = await self.db_manager.execute_single_query(count_query, (cutoff,))
            stale_count = int((count_row or {}).get("stale_count", 0) or 0)

            if stale_count <= 0:
                self.metrics["last_cleaned_count"] = 0
                return 0

            update_query = """
                UPDATE trading.trades
                SET status = 'FAILED',
                    exit_reason = CASE
                        WHEN COALESCE(exit_reason, '') = '' THEN %s
                        ELSE exit_reason
                    END,
                    exit_time = COALESCE(exit_time, NOW()),
                    updated_at = NOW()
                WHERE status = 'PENDING'
                  AND COALESCE(entry_id, '') = ''
                  AND COALESCE(updated_at, entry_time) < %s
            """
            await self.db_manager.execute_query(update_query, (reason, cutoff))

            self.metrics["last_cleaned_count"] = stale_count
            self.metrics["cleaned_total"] += stale_count
            logger.warning(
                "🧹 Pending Trade Cleaner marked %s stale PENDING trades as FAILED (cutoff=%s)",
                stale_count,
                cutoff.isoformat(),
            )
            return stale_count
        except Exception as e:
            self.metrics["last_error"] = str(e)
            logger.error("❌ Pending Trade Cleaner failed: %s", e)
            return 0

    def get_status(self) -> Dict[str, Any]:
        return {
            "status": "running" if self.is_running else "stopped",
            "stale_minutes": self.stale_minutes,
            "check_interval_seconds": self.check_interval_seconds,
            "metrics": self.metrics,
        }
