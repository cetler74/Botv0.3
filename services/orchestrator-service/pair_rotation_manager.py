import logging
from typing import Any, Awaitable, Callable, Dict, List

import httpx
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


async def rotate_pair_after_loss(
    *,
    exchange_name: str,
    pair: str,
    realized_pnl_pct: float,
    pair_selections: Dict[str, List[str]],
    config_service_url: str,
    database_service_url: str,
    get_config_value: Callable[[str, Any], Awaitable[Any]],
    generate_and_store_pairs: Callable[[httpx.AsyncClient, str, int, str], Awaitable[None]],
    temp_blacklist_until: Dict[str, datetime],
) -> None:
    """Remove badly-losing pair from selected list and replace it."""
    try:
        loss_threshold_pct = float(
            await get_config_value(
                "trading.pair_rotation.remove_pair_loss_threshold_pct", -1.0
            )
            or -1.0
        )
        if realized_pnl_pct > loss_threshold_pct:
            return
        cooldown_hours = float(
            await get_config_value(
                "trading.pair_rotation.temp_blacklist_cooldown_hours", 12
            )
            or 12
        )

        logger.warning(
            "[PairRotation] Triggered for %s %s: realized_pnl_pct=%.2f%% <= threshold=%.2f%%",
            exchange_name,
            pair,
            realized_pnl_pct,
            loss_threshold_pct,
        )
        blacklist_key = f"{exchange_name}|{pair}"
        temp_blacklist_until[blacklist_key] = datetime.utcnow() + timedelta(hours=cooldown_hours)
        logger.warning(
            "[PairRotation] Temp-blacklisted %s on %s for %.1fh (until %s)",
            pair,
            exchange_name,
            cooldown_hours,
            temp_blacklist_until[blacklist_key].isoformat(),
        )

        from core.pair_selector import select_top_pairs_ccxt

        async with httpx.AsyncClient(timeout=45.0) as client:
            exchange_cfg_resp = await client.get(
                f"{config_service_url}/api/v1/config/exchanges/{exchange_name}"
            )
            if exchange_cfg_resp.status_code == 200:
                exchange_cfg = exchange_cfg_resp.json()
                max_pairs = int(exchange_cfg.get("max_pairs", 10) or 10)
                base_currency = str(exchange_cfg.get("base_currency", "USDC") or "USDC")
            else:
                max_pairs = 10
                base_currency = "USDC"

            current_pairs = list(pair_selections.get(exchange_name, []))
            if not current_pairs:
                db_pairs_resp = await client.get(f"{database_service_url}/api/v1/pairs/{exchange_name}")
                if db_pairs_resp.status_code == 200:
                    current_pairs = list((db_pairs_resp.json() or {}).get("pairs", []) or [])

            if not current_pairs:
                logger.warning(
                    "[PairRotation] No existing pair list for %s. Regenerating full list.",
                    exchange_name,
                )
                await generate_and_store_pairs(client, exchange_name, max_pairs, base_currency)
                return

            filtered_pairs = [p for p in current_pairs if p != pair]
            if len(filtered_pairs) == len(current_pairs):
                logger.info(
                    "[PairRotation] Pair %s not present in current %s selection; skipping replacement",
                    pair,
                    exchange_name,
                )
                return

            replacement = None
            selector_result = await select_top_pairs_ccxt(
                exchange_name, base_currency, max_pairs * 4, "spot"
            )
            selector_pairs = []
            if isinstance(selector_result, dict):
                selector_pairs = list(selector_result.get("selected_pairs", []) or [])

            for candidate in selector_pairs:
                candidate_key = f"{exchange_name}|{candidate}"
                if (
                    candidate != pair
                    and candidate not in filtered_pairs
                    and (
                        candidate_key not in temp_blacklist_until
                        or temp_blacklist_until[candidate_key] <= datetime.utcnow()
                    )
                ):
                    replacement = candidate
                    break

            if replacement is None:
                logger.warning(
                    "[PairRotation] No replacement from selector for %s/%s; regenerating pool",
                    exchange_name,
                    pair,
                )
                await generate_and_store_pairs(client, exchange_name, max_pairs, base_currency)
                refreshed = list(pair_selections.get(exchange_name, []) or [])
                filtered_pairs = [p for p in refreshed if p != pair]
                for candidate in refreshed:
                    candidate_key = f"{exchange_name}|{candidate}"
                    if (
                        candidate != pair
                        and candidate not in filtered_pairs
                        and (
                            candidate_key not in temp_blacklist_until
                            or temp_blacklist_until[candidate_key] <= datetime.utcnow()
                        )
                    ):
                        replacement = candidate
                        break

            if replacement:
                filtered_pairs.append(replacement)

            final_pairs = filtered_pairs[:max_pairs]
            store_resp = await client.post(
                f"{database_service_url}/api/v1/pairs/{exchange_name}", json=final_pairs
            )
            if store_resp.status_code not in (200, 201):
                logger.error(
                    "[PairRotation] Failed storing updated pairs for %s (status=%s)",
                    exchange_name,
                    store_resp.status_code,
                )
                return

            pair_selections[exchange_name] = final_pairs
            logger.warning(
                "[PairRotation] Removed %s on %s after %.2f%% loss. Replacement=%s. Final count=%s/%s",
                pair,
                exchange_name,
                realized_pnl_pct,
                replacement or "none",
                len(final_pairs),
                max_pairs,
            )
    except Exception as e:
        logger.error(
            "[PairRotation] Error rotating %s on %s after loss %.2f%%: %s",
            pair,
            exchange_name,
            realized_pnl_pct,
            e,
        )
