import os
import ccxt

# Load API keys from environment variables
api_key = os.getenv('m5jjdWjtey1wmKS8iRHZj7')
api_secret = os.getenv('cxakp_BBgPh7TgccDd3V25rczWip')

if not api_key or not api_secret:
    print("Error: Please set EXCHANGE_CRYPTOCOM_API_KEY and EXCHANGE_CRYPTOCOM_API_SECRET in your environment.")
    exit(1)

# Initialize the exchange
exchange = ccxt.cryptocom({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'timeout': 60000,
})

try:
    balance = exchange.fetch_balance()
except Exception as e:
    print(f"Error fetching balance: {e}")
    exit(1)

print("\n--- Crypto.com Account Balances ---")
print(f"{'Asset':<10} {'Free':>20} {'Total':>20}")
print("-" * 55)
for asset, free_amt in balance.get('free', {}).items():
    total_amt = balance.get('total', {}).get(asset, 0)
    highlight = ' <== AAVE' if asset.upper() == 'AAVE' else ''
    print(f"{asset:<10} {free_amt:>20,.8f} {total_amt:>20,.8f}{highlight}")

if 'AAVE' in balance.get('free', {}):
    print(f"\nAAVE available: {balance['free']['AAVE']} (total: {balance['total']['AAVE']})")
else:
    print("\nAAVE not found in account balances.") 