#!/usr/bin/env python3
import asyncio
import httpx
from datetime import datetime

EXCHANGE = "cryptocom"
SYMBOL = "A2Z/USD"
EXCHANGE_SVC = "http://localhost:8003"
DB_SVC = "http://localhost:8002"

TOLERANCE = 0.05  # 5%

async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # fetch fills
        r_trades = await client.get(f"{EXCHANGE_SVC}/api/v1/trading/mytrades/{EXCHANGE}", params={"symbol": SYMBOL, "limit": 200})
        r_trades.raise_for_status()
        fills = r_trades.json().get("trades", [])
        sells = [t for t in fills if str(t.get("side")).lower() == "sell"]
        sells.sort(key=lambda x: x.get("timestamp", 0))

        # fetch open trades first
        r_open = await client.get(f"{DB_SVC}/api/v1/trades/open", params={"exchange": EXCHANGE})
        r_open.raise_for_status()
        open_trades = [t for t in r_open.json().get("trades", []) if t.get("pair") == SYMBOL]
        open_trades.sort(key=lambda x: x.get("entry_time"))

        closed_count = 0
        for fill in sells:
            ts = fill.get("timestamp")
            if ts:
                if ts > 1000000000000:
                    exit_dt = datetime.fromtimestamp(ts/1000)
                else:
                    exit_dt = datetime.fromtimestamp(ts)
            else:
                exit_dt = datetime.utcnow()
            exit_id = fill.get("order") or fill.get("id")
            price = float(fill.get("price", 0) or 0)
            amount = float(fill.get("amount", 0) or 0)
            if amount <= 0 or price <= 0:
                continue

            # find matching open trade by size tolerance
            match = None
            for t in open_trades:
                pos = float(t.get("position_size", 0) or 0)
                if pos <= 0:
                    continue
                if abs(pos - amount) <= TOLERANCE * amount:
                    match = t
                    break
            if not match and open_trades:
                # fallback: take oldest open trade
                match = open_trades[0]

            if not match:
                continue

            trade_id = match["trade_id"]
            entry_price = float(match.get("entry_price", 0) or 0)
            realized_pnl = (price - entry_price) * amount if entry_price > 0 else 0.0

            update = {
                "exit_price": price,
                "exit_time": exit_dt.isoformat(),
                "exit_id": str(exit_id) if exit_id else None,
                "realized_pnl": realized_pnl,
                "status": "CLOSED",
                "exit_reason": "exchange_backfill"
            }
            r_upd = await client.put(f"{DB_SVC}/api/v1/trades/{trade_id}", json=update)
            if r_upd.status_code == 200:
                closed_count += 1
                # remove from open_trades if matched
                open_trades = [t for t in open_trades if t["trade_id"] != trade_id]

        print({"closed_trades": closed_count})

        # Verify remaining CLOSED A2Z with missing exit fields
        r_closed = await client.get(f"{DB_SVC}/api/v1/trades", params={"status": "CLOSED", "exchange": EXCHANGE, "limit": 500})
        r_closed.raise_for_status()
        closed = [t for t in r_closed.json().get("trades", []) if t.get("pair") == SYMBOL]
        missing = [t for t in closed if (t.get("exit_time") is None or (t.get("exit_price") in (None, 0)))]
        print({"closed_total": len(closed), "missing_exit_fields": len(missing)})

if __name__ == "__main__":
    asyncio.run(main())
