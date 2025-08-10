#!/usr/bin/env python3
import asyncio
import httpx

EXCHANGE = "cryptocom"
SYMBOL = "A2Z/USD"
EXCHANGE_SVC = "http://localhost:8003"
DB_SVC = "http://localhost:8002"

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        # fetch exchange data
        recent_trades = []
        try:
            r_trades = await client.get(f"{EXCHANGE_SVC}/api/v1/trading/mytrades/{EXCHANGE}", params={"symbol": SYMBOL, "limit": 50})
            r_trades.raise_for_status()
            recent_trades = r_trades.json().get("trades", [])
        except Exception as e:
            # fallback to orders history if mytrades is not available
            print("mytrades fetch failed, falling back to orders history:", e)
            r_hist = await client.get(f"{EXCHANGE_SVC}/api/v1/trading/orders/history/{EXCHANGE}", params={"symbol": SYMBOL, "limit": 50})
            r_hist.raise_for_status()
            orders = r_hist.json().get("orders", [])
            # map orders to trades-like records where possible
            for o in orders:
                if str(o.get("status")).lower() in ("closed", "filled", "done"):
                    recent_trades.append({
                        "id": o.get("id") or o.get("order") or o.get("clientOrderId"),
                        "timestamp": o.get("timestamp"),
                        "symbol": o.get("symbol"),
                        "side": o.get("side"),
                        "amount": o.get("amount") or o.get("filled"),
                        "price": o.get("price"),
                        "cost": o.get("cost")
                    })

        r_open = await client.get(f"{EXCHANGE_SVC}/api/v1/trading/orders/{EXCHANGE}", params={"symbol": SYMBOL})
        r_open.raise_for_status()
        open_orders = r_open.json().get("orders", [])

        r_bal = await client.get(f"{EXCHANGE_SVC}/api/v1/account/balance/{EXCHANGE}")
        r_bal.raise_for_status()
        balance = r_bal.json()

        # post to comprehensive sync
        payload = {
            "exchange": EXCHANGE,
            "recent_trades": recent_trades,
            "current_balance": balance,
            "open_orders": open_orders,
            "sync_type": "targeted"
        }
        r_sync = await client.post(f"{DB_SVC}/api/v1/trades/comprehensive-sync", json=payload)
        r_sync.raise_for_status()
        print("Sync result:", r_sync.json())

        # verify: count closed A2Z trades missing exit data
        q = await client.get(f"{DB_SVC}/api/v1/trades", params={"status": "CLOSED", "exchange": EXCHANGE, "limit": 200})
        q.raise_for_status()
        trades = [t for t in q.json().get("trades", []) if t.get("pair") == SYMBOL]
        missing = [t for t in trades if (t.get("exit_time") is None or (t.get("exit_price") in (None, 0)))]
        print(f"Closed A2Z trades: {len(trades)}, missing exit fields: {len(missing)}")
        if missing:
            print("Examples with missing exit fields:")
            for t in missing[:5]:
                print(t.get("trade_id"), t.get("entry_id"), t.get("exit_id"), t.get("exit_price"), t.get("exit_time"))

if __name__ == "__main__":
    asyncio.run(main())
