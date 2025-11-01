import time
import threading
import uuid
import os
import logging
from datetime import datetime, timezone
from exchange import init_client, market_sell, check_order_executed, place_limit_buy
from supabase_client import get_all_active_bots, save_order, supabase, update_execution_time_and_profit

# =====================================================
# 🪵 Setup logging
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
# 🧠 Order Checker (thread separat)
# =====================================================
def update_order_status(order_id, new_status, avg_price=None, filled_size=None, cycle_id=None):
    """Actualizează statusul ordinului și, dacă e complet executat, calculează profitul."""
    data = {
        "status": new_status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    if avg_price is not None:
        data["price"] = avg_price
    if filled_size is not None:
        data["filled_size"] = filled_size

    supabase.table("orders").update(data).eq("order_id", order_id).execute()
    logging.info(f"🟢 Updated {order_id}: {new_status} ({avg_price})")

    # dacă este un BUY executat → calculează profitul
    if new_status == "executed" and cycle_id:
        update_execution_time_and_profit(cycle_id)


def check_old_orders(client, symbol):
    """Verifică ultimele 5 ordine neexecutate (pending/open)."""
    result = (
        supabase.table("orders")
        .select("*")
        .eq("symbol", symbol)
        .in_("status", ["pending", "open"])
        .order("last_updated", desc=False)
        .limit(5)
        .execute()
    )

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
            update_order_status(order_id, "executed", avg_price, None, cycle_id)
            logging.info(f"[{symbol}] ✅ Ordin {side} executat: {order_id}")
        else:
            update_order_status(order_id, "pending")
            logging.info(f"[{symbol}] ⏳ Ordin {side} încă în așteptare: {order_id}")


def run_order_checker():
    """Rulează verificarea automată a ordinelor o dată pe oră."""
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
                client = init_client(bot["api_key"], bot["api_secret"], bot["api_passphrase"])
                check_old_orders(client, symbol)

            logging.info("✅ Verificarea s-a terminat. Următoarea în 1 oră.\n")
            time.sleep(3600)

        except Exception as e:
            logging.error(f"❌ Eroare în order_checker: {e}")
            time.sleep(60)

# =====================================================
# 🤖 Bot principal SELL → BUY
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

    # 🧠 Verifică timpul de la ultimul SELL executat
    try:
        last_sell = (
            supabase.table("orders")
            .select("created_at")
            .eq("symbol", symbol)
            .eq("side", "SELL")
            .eq("status", "executed")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if last_sell.data and last_sell.data[0].get("created_at"):
            last_time = datetime.fromisoformat(last_sell.data[0]["created_at"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed = (now - last_time).total_seconds()
            remaining = cycle_delay - elapsed

            if remaining > 0:
                hrs = round(remaining / 3600, 2)
                logging.info(
                    f"[{symbol}] ⏳ Ultimul SELL a fost acum {round(elapsed/3600,2)}h → Aștept {hrs}h până la următorul ciclu..."
                )
                time.sleep(remaining)
            else:
                logging.info(f"[{symbol}] ✅ Timpul de așteptare a expirat. Încep un nou ciclu.")
        else:
            logging.info(f"[{symbol}] ℹ️ Nu există istoric anterior de SELL — pornesc direct.")
    except Exception as e:
        logging.warning(f"[{symbol}] ⚠️ Eroare la verificarea ultimului SELL: {e}")

    # 🔁 Buclă principală a botului
    while True:
        try:
            # ⚠️ Evită dublarea SELL dacă există un pending recent (<30min)
            pending = (
                supabase.table("orders")
                .select("created_at")
                .eq("symbol", symbol)
                .eq("side", "SELL")
                .eq("status", "pending")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

            if pending.data and len(pending.data) > 0:
                last_pending = pending.data[0]
                created_at = datetime.fromisoformat(last_pending["created_at"].replace("Z", "+00:00"))
                elapsed_minutes = (datetime.now(timezone.utc) - created_at).total_seconds() / 60

                if elapsed_minutes < 30:
                    logging.info(
                        f"[{symbol}] ⚠️ Există un SELL pending de {round(elapsed_minutes,1)} minute → aștept execuția."
                    )
                    time.sleep(60)
                    continue
                else:
                    logging.warning(
                        f"[{symbol}] ⏳ Ordinul pending este mai vechi de 30 min → continui ciclul nou (considerat expirat)."
                    )

            # 1️⃣ Inițializează clientul KuCoin
            client = init_client(api_key, api_secret, api_passphrase)

            # 2️⃣ MARKET SELL → inițiere ciclu nou
            cycle_id = str(uuid.uuid4())
            sell_id = market_sell(client, symbol, amount)
            save_order(symbol, "SELL", 0, "pending", {"order_id": sell_id, "cycle_id": cycle_id})

            # 3️⃣ Așteaptă execuția SELL
            executed = False
            avg_price = 0
            while not executed:
                time.sleep(check_delay)
                executed, avg_price = check_order_executed(client, sell_id)
                logging.info(f"[{symbol}] ⏳ Checking SELL... executed={executed}, avg={avg_price}")
                if executed:
                    supabase.table("orders").update(
                        {
                            "status": "executed",
                            "price": avg_price,
                            "filled_size": amount,
                            "last_updated": datetime.now(timezone.utc).isoformat(),
                        }
                    ).eq("order_id", sell_id).execute()
                    logging.info(f"[{symbol}] ✅ SELL executat @ {avg_price}")

            # 4️⃣ BUY LIMIT imediat după SELL
            if avg_price > 0:
                buy_price = round(avg_price * (1 - buy_discount), 4)
                buy_id = place_limit_buy(client, symbol, amount, buy_price)
                save_order(symbol, "BUY", buy_price, "open", {"order_id": buy_id, "cycle_id": cycle_id})
                logging.info(f"[{symbol}] 🟢 BUY limit placed la {buy_price}")

                # verifică execuția BUY după delay
                time.sleep(check_delay)
                executed_buy, buy_avg = check_order_executed(client, buy_id)
                if executed_buy:
                    supabase.table("orders").update(
                        {
                            "status": "executed",
                            "price": buy_avg if buy_avg > 0 else buy_price,
                            "filled_size": amount,
                            "last_updated": datetime.now(timezone.utc).isoformat(),
                        }
                    ).eq("order_id", buy_id).execute()

                    # 🧾 actualizează profitul + durata ciclului
                    update_execution_time_and_profit(cycle_id)
                    logging.info(f"[{symbol}] ✅ BUY executat instant @ {buy_avg or buy_price}")
                else:
                    logging.info(f"[{symbol}] ⏳ BUY încă deschis — va fi verificat ulterior.")

            logging.info(f"[{symbol}] ✅ Cycle complete. Waiting {cycle_delay/3600}h...\n")
            time.sleep(cycle_delay)

        except Exception as e:
            logging.error(f"[{symbol}] ❌ Error: {e}")
            time.sleep(30)

# =====================================================
# 🚀 Pornire toți boții + order checker
# =====================================================
def start_all_bots():
    bots = get_all_active_bots()
    if not bots:
        logging.warning("⚠️ No active bots found in Supabase.")
        return

    # Rulează fiecare bot în thread separat
    for settings in bots:
        threading.Thread(target=run_bot, args=(settings,), daemon=True).start()

    # Thread separat pentru verificarea ordinelor
    threading.Thread(target=run_order_checker, daemon=True).start()

    while True:
        time.sleep(60)


if __name__ == "__main__":
    start_all_bots()
