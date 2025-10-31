from kucoin.client import Trade
import time

# =====================================================
# 🔌 Inițializare client KuCoin
# =====================================================
def init_client(api_key, api_secret, api_passphrase):
    """Creează conexiunea la KuCoin Trade API."""
    try:
        client = Trade(
            key=api_key,
            secret=api_secret,
            passphrase=api_passphrase
        )
        print("✅ KuCoin client initialized.")
        return client
    except Exception as e:
        print(f"❌ Eroare la inițializarea clientului KuCoin: {e}")
        raise


# =====================================================
# 💰 Market SELL
# =====================================================
def market_sell(client, symbol, amount):
    """Plasează un ordin de vânzare MARKET."""
    try:
        order = client.create_market_order(symbol, 'sell', size=str(amount))
        order_id = order.get('orderId') or order.get('id')
        print(f"[{symbol}] 🟠 Market SELL placed (orderId: {order_id})")
        return order_id
    except Exception as e:
        print(f"[{symbol}] ❌ Eroare la plasarea ordinului MARKET SELL: {e}")
        time.sleep(5)
        raise


# =====================================================
# 🔍 Verificare status ordin (compatibil v1.0.26 și v2.x)
# =====================================================
def check_order_executed(client, order_id):
    """
    Verifică dacă un ordin a fost complet executat.
    Compatibil atât cu versiunile vechi cât și cu cele noi ale SDK-ului KuCoin.
    """
    try:
        # Compatibilitate automată între SDK-uri
        if hasattr(client, "get_order_details"):
            status = client.get_order_details(order_id)   # vechi SDK (1.0.26)
        else:
            status = client.get_order(order_id)           # nou SDK (>=2.0)

        # Extragem datele utile
        filled = float(status.get('dealSize', 0))
        total = float(status.get('size', 0))
        deal_funds = float(status.get('dealFunds', 0))
        state = status.get('status', '')  # poate fi: done, open, cancel

        done = state == 'done' or filled >= total
        avg_price = (deal_funds / filled) if filled > 0 else 0

        print(f"[{status.get('symbol', '')}] 🔎 check_order_executed → status={state}, filled={filled}/{total}, avg={avg_price}")
        return done, avg_price

    except Exception as e:
        print(f"❌ Eroare la check_order_executed pentru {order_id}: {e}")
        time.sleep(5)
        return False, 0


# =====================================================
# 🟢 Limit BUY
# =====================================================
def place_limit_buy(client, symbol, amount, price):
    """Plasează un ordin de cumpărare LIMIT."""
    try:
        order = client.create_limit_order(symbol, 'buy', size=str(amount), price=str(price))
        order_id = order.get('orderId') or order.get('id')
        print(f"[{symbol}] 🟢 Limit BUY order created at {price} (orderId: {order_id})")
        return order_id
    except Exception as e:
        print(f"[{symbol}] ❌ Eroare la plasarea ordinului LIMIT BUY: {e}")
        time.sleep(5)
        raise
