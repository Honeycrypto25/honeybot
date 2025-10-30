import time
from datetime import datetime
from exchange import init_client, check_order_executed
from supabase_client import get_all_active_bots, supabase

print("🕒 Order Checker started... (runs every hour)\n")

# =====================================================
# 🧱 Actualizare status în Supabase
# =====================================================
def update_order_status(order_id, new_status, avg_price=None):
    """Actualizează statusul unui ordin în Supabase"""
    data = {
        "status": new_status,
        "last_updated": datetime.utcnow().isoformat()
    }
    if avg_price is not None:
        data["price"] = avg_price

    supabase.table("orders").update(data).eq("order_id", order_id).execute()

# =====================================================
# 🔍 Verificare ordine vechi (ultimele 5)
# =====================================================
def check_old_orders(client, symbol):
    """Verifică ultimele 5 ordine neînchise pentru simbolul dat"""
    result = supabase.table("orders") \
        .select("*") \
        .eq("symbol", symbol) \
        .in_("status", ["pending", "open"]) \
        .order("last_updated", desc=False) \
        .limit(5) \
        .execute()

    orders = result.data or []
    if not orders:
        print(f"[{symbol}] ✅ Nicio comandă de verificat.")
        return

    for order in orders:
        order_id = order.get("order_id")
        if not order_id:
            continue

        done, avg_price = check_order_executed(client, order_id)
        if done:
            update_order_status(order_id, "executed", avg_price)
            print(f"[{symbol}] ✅ Ordin executat: {order_id} | preț mediu: {avg_price}")
        else:
            update_order_status(order_id, "pending")
            print(f"[{symbol}] ⏳ Încă în așteptare: {order_id}")

# =====================================================
# 🔁 Bucla principală (rulează din oră în oră)
# =====================================================
def run_checker():
    """Rulare periodică la fiecare oră"""
    while True:
        try:
            bots = get_all_active_bots()
            if not bots:
                print("⚠️ Niciun bot activ în settings.")
                time.sleep(3600)
                continue

            print(f"\n🔍 Pornesc verificarea la {datetime.utcnow().isoformat()}...\n")

            for bot in bots:
                symbol = bot["symbol"]
                api_key = bot["api_key"]
                api_secret = bot["api_secret"]
                api_passphrase = bot["api_passphrase"]

                client = init_client(api_key, api_secret, api_passphrase)
                check_old_orders(client, symbol)

            print("\n✅ Verificarea s-a terminat. Următoarea în 1 oră.\n")
            time.sleep(3600)

        except Exception as e:
            print("❌ Eroare în order_checker:", e)
            time.sleep(60)

# =====================================================
# 🚀 Start
# =====================================================
if __name__ == "__main__":
    run_checker()
