from kucoin.client import Trade

# =====================================================
# 🔌 Inițializare client KuCoin
# =====================================================
def init_client(api_key, api_secret, api_passphrase):
    """Creează conexiunea la KuCoin Trade API."""
    return Trade(
        key=api_key,
        secret=api_secret,
        passphrase=api_passphrase
    )

# =====================================================
# 💰 Market SELL
# =====================================================
def market_sell(client, symbol, amount):
    """Plasează un ordin de vânzare MARKET."""
    order = client.create_market_order(symbol, 'sell', size=amount)
    order_id = order.get('orderId')
    print(f"[{symbol}] 🟠 Market SELL placed (orderId: {order_id})")
    return order_id

# =====================================================
# 🔍 Verificare status ordin (compatibil v1.0.26 și v2.x)
# =====================================================
def check_order_executed(client, order_id):
    """Verifică dacă un ordin a fost complet executat pe orice versiune a SDK-ului KuCoin."""

    # compatibilitate automată cu versiuni diferite de SDK
    if hasattr(client, "get_order_details"):
        status = client.get_order_details(order_id)   # vechile SDK-uri (ex: 1.0.26)
    else:
        status = client.get_order(order_id)           # noile SDK-uri (ex: 2.x)

    filled = float(status.get('dealSize', 0))
    total = float(status.get('size', 0))
    state = status.get('status', '')  # poate fi: done, open, cancel
    done = state == 'done' or filled >= total

    avg_price = 0
    if filled > 0:
        avg_price = float(status.get('dealFunds', 0)) / filled

    print(f"[{status.get('symbol', '')}] 🔎 check_order_executed → status={state}, filled={filled}/{total}, avg={avg_price}")
    return done, avg_price

# =====================================================
# 🟢 Limit BUY
# =====================================================
def place_limit_buy(client, symbol, amount, price):
    """Plasează un ordin de cumpărare LIMIT."""
    order = client.create_limit_order(symbol, 'buy', size=amount, price=str(price))
    order_id = order.get('orderId')
    print(f"[{symbol}] 🟢 Limit BUY order created at {price} (orderId: {order_id})")
    return order_id
