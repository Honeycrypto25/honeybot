import time
import threading
import uuid
import os
import logging
from datetime import datetime, timezone
from exchange import init_client, market_sell, check_order_executed, place_limit_buy
from supabase_client import get_all_active_bots, save_order, supabase, update_execution_time

# =====================================================
# 🪵 Setup logging (scrie în logs/honeybot.log)
# =====================================================
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/honeybot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
console.setFormatter(formatter)
logging.getLogger("").addHandler(console)

logging.info("🚀 HONEYBOT Multi-Bot + Smart Cycle Recovery started...\n")

# =====================================================
# 🧠 Order Checker (rulează în thread separat)
# =====================================================
def update_order_status(order_id, new_status, avg_price=None, cycle_id=None):
    """Actualizează statusul unui ordin în Supabase."""
    data = {
        "status": new_status,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }
    if avg_price is not None:
        data["price"] = avg_price

    supabase.table("orders").update(data).eq("order_id", order_id).execute()
    logging.info(f"🟢 Updated {order_id}: {new_status} ({avg_price})")

    # dacă e BUY executat complet, actualizează durata ciclului
    if new_status == "executed" and cycle_id:
        update_execution_time(cycle_id)

def check_old_orders(client, symbol):
    """Verifică ultimele 5 ordine neînchise."""
    result = supabase.table("orders") \
        .select("*") \
        .eq("symbol", symbol) \
        .in_("status", ["pending", "open"]) \
        .order("last_updated", desc=False) \
        .limit(5) \
        .execute()

    orders = result.data or []
    if not orders:
        logging.info(f"[{symbol}] ✅ Nicio comandă de verificat.")
        return

    for order in orders:
        order_id = order.get("order_id")
        cycle_id = order.get("cycle_id")
        side = order.get("side")
        if not order_id:
            continue

        done, avg_price = check_order_executed(client, order_id)
        if done:
            update_order_status(order_id, "executed", avg_price, cycle_id)
            logging.info(f"[{symbol}] ✅ Ordin {side} executat: {order_id}")
        else:
            update_order_status(order_id, "pending")
            logging.info(f"[{symbol}] ⏳ Ordin {side} încă în așteptare: {order_id}")

def run_order_checker():
    """Rulează verificarea la fiecare oră."""
    while True:
        try:
            bots = get_all_active_bots()
            if not bots:
                logging.warning("⚠️ Niciun bot activ în settings.")
                time.sleep(3600)
                continue

            logging.info(f"\n🔍 Pornesc verificarea la {datetime.now(timezone.utc).isoformat()}...\n")

            for bot in bots:
                symbol = bot["symbol"]
                api_key = bot["api_key"]
                api_secret = bot["api_secret"]
                api_passphrase = bot["api_passphrase"]

                client = init_client(api_key, api_secret, api_passphrase)
                check_old_orders(client, symbol)

            logging.info("✅ Verificarea s-a terminat. Următoarea în 1 oră.\n")
            time.sleep(3600)

        except Exception as e:
            logging.error(f"❌ Eroare în order_checker: {e}")
            time.sleep(60)

# =====================================================
# 🤖 Bot principal (SELL → CHECK → BUY)
# =====================================================
def run_bot(settings):
    symbol = settings["symbol"]
    amount = float(settings["amount"])
    buy_discount = float(settings["buy_discount"])
    check_delay = int(settings["check_delay"])
    cycle_delay = int(settings["cycle_delay"])
    api_key = settings["api_key"]
    api_secret = settings["api_secret"]
    api_passphrase = settings["api_passphrase"]

    logging.info(f"⚙️ Started bot for {symbol}: amount={amount}, discount={buy_discount*100}%, cycle={cycle_delay/3600}h")

    # 🧠 1️⃣ Verifică timpul scurs de la ultimul SELL executat
    try:
        last_sell = supabase.table("orders") \
            .select("created_at") \
            .eq("symbol", symbol) \
            .eq("side", "SELL") \
            .eq("status", "executed") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if last_sell.data and last_sell.data[0].get("created_at"):
            last_time = datetime.fromisoformat(last_sell.data[0]["created_at"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed = (now - last_time).total_seconds()
            remaining = cycle_delay - elapsed

            if remaining > 0:
                hrs = round(remaining / 3600, 2)
                logging.info(f"[{symbol}] ⏳ Ultimul SELL a fost acum {round(elapsed/3600,2)}h → Aștept {hrs}h până la următorul ciclu...")
                time.sleep(remaining)
            else:
                logging.info(f"[{symbol}] ✅ Timpul de așteptare a expirat. Încep un nou ciclu.")
        else:
            logging.info(f"[{symbol}] ℹ️ Nu există istoric anterior de SELL — pornesc direct.")
    except Exception as e:
        logging.warning(f"[{symbol}] ⚠️ Eroare la verificarea ultimului SELL: {e}")

    # 🔁 Bucla principală de ciclu
    while True:
        try:
            # ⚠️ Evită dublarea SELL dacă există unul pending
            pending = supabase.table("orders") \
                .select("*") \
                .eq("symbol", symbol) \
                .eq("status", "pending") \
                .execute()

            if pending.data and len(pending.data) > 0:
                logging.info(f"[{symbol}] ⚠️ Există deja un SELL pending. Aștept execuția...")
                time.sleep(60)
                continue

            # 1️⃣ Inițializează clientul KuCoin
            client = init_client(api_key, api_secret, api_passphrase)

            # 2️⃣ MARKET SELL → generăm cycle_id unic
            cycle_id = str(uuid.uuid4())
            sell_id = market_sell(client, symbol, amount)
            save_order(symbol, "SELL", 0, "pending", {"order_id": sell_id, "cycle_id": cycle_id})

            # 3️⃣ Verificăm execuția SELL
            executed = False
            avg_price = 0
            while not executed:
                time.sleep(check_delay)
                executed, avg_price = check_order_executed(client, sell_id)
                logging.info(f"[{symbol}] ⏳ Checking SELL... executed={executed}, avg={avg_price}")

                if executed:
                    supabase.table("orders").update({
                        "status": "executed",
                        "price": avg_price,
                        "last_updated": datetime.now(timezone.utc).isoformat()
                    }).eq("order_id", sell_id).execute()
                    logging.info(f"[{symbol}] ✅ SELL order updated → executed @ {avg_price}")

            # 4️⃣ BUY limit la -discount%
            if avg_price > 0:
                buy_price = round(avg_price * (1 - buy_discount), 4)
                buy_id = place_limit_buy(client, symbol, amount, buy_price)
                save_order(symbol, "BUY", buy_price, "open", {"order_id": buy_id, "cycle_id": cycle_id})
                logging.info(f"[{symbol}] 🟢 BUY limit placed la {buy_price}")

            logging.info(f"[{symbol}] ✅ Cycle complete. Waiting {cycle_delay/3600}h...\n")
            time.sleep(cycle_delay)

        except Exception as e:
            logging.error(f"[{symbol}] ❌ Error: {e}")
            time.sleep(30)

# =====================================================
# 🚀 Start toți boții + Order Checker
# =====================================================
def start_all_bots():
    bots = get_all_active_bots()
    if not bots:
        logging.warning("⚠️ No active bots found in Supabase.")
        return

    # pornește fiecare bot într-un thread propriu
    for settings in bots:
        thread = threading.Thread(target=run_bot, args=(settings,))
        thread.daemon = True
        thread.start()

    # pornește Order Checker într-un thread separat
    checker_thread = threading.Thread(target=run_order_checker)
    checker_thread.daemon = True
    checker_thread.start()

    while True:
        time.sleep(60)

if __name__ == "__main__":
    start_all_bots()
