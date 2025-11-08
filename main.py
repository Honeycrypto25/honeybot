import time
import threading
import uuid
import os
import logging
from datetime import datetime, timezone
from exchange import (
    init_client,
    market_sell,
    check_order_executed,
    place_limit_buy,
)
from supabase_client import (
    get_latest_settings,
    save_order,
    supabase,
    update_execution_time_and_profit,
)

# =====================================================
# 🪵 Setup logging
# =====================================================
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/stb.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
console.setFormatter(formatter)
logging.getLogger("").addHandler(console)

logging.info("🚀 STB BOT (Sell-Then-Buy) started...\n")

# =====================================================
# ⚙️ Constante
# =====================================================
MARKET_TIMEOUT_SECONDS = 600  # 10 minute max pentru execuție MARKET
TICK_SIZE = 0.00001  # pentru HONEY-USDT

# =====================================================
# 🧮 Tick Size Adjust
# =====================================================
def adjust_price_to_tick(price, tick_size=TICK_SIZE):
    return round(round(price / tick_size) * tick_size, 5)

# =====================================================
# 💾 Save wrapper
# =====================================================
def safe_save_order(symbol, side, price, status, meta):
    try:
        save_order(symbol, side, price, status, meta)
        logging.info(
            f"[{symbol}] 💾 Saved {side} ({status}) | price={price} | cycle_id={meta.get('cycle_id')}"
        )
    except Exception as e:
        logging.error(f"[{symbol}] ❌ save_order failed: {e}")

# =====================================================
# ⏱️ Așteaptă execuția MARKET cu timeout
# =====================================================
def wait_market_execution(client, symbol, order_id, amount, check_delay, cycle_id):
    start_ts = time.time()
    while True:
        executed, avg_price = check_order_executed(client, order_id)
        if executed:
            supabase.table("orders").update(
                {
                    "status": "executed",
                    "price": avg_price,
                    "filled_size": amount,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("order_id", order_id).execute()
            logging.info(f"[{symbol}] ✅ SELL executat @ {avg_price}")
            return True, avg_price

        if time.time() - start_ts > MARKET_TIMEOUT_SECONDS:
            logging.warning(f"[{symbol}] ⏰ Timeout MARKET SELL — ordin pending, skip cycle.")
            return False, 0

        time.sleep(check_delay)

# =====================================================
# 🔍 Verificare ordine vechi (ultimele 5)
# =====================================================
def update_order_status(order_id, new_status, avg_price=None, filled_size=None, cycle_id=None):
    data = {
        "status": new_status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if avg_price is not None:
        data["price"] = avg_price
    if filled_size is not None:
        data["filled_size"] = filled_size

    supabase.table("orders").update(data).eq("order_id", order_id).execute()
    if new_status == "executed" and cycle_id:
        update_execution_time_and_profit(cycle_id)
    logging.info(f"🟢 Updated {order_id}: {new_status} ({avg_price})")


def check_old_orders(client, symbol):
    result = (
        supabase.table("orders")
        .select("*")
        .eq("symbol", symbol)
        .eq("strategy", "STB")
        .in_("status", ["pending", "open"])
        .order("last_updated", desc=False)
        .limit(5)
        .execute()
    )

    orders = result.data or []
    if not orders:
        logging.info(f"[{symbol}] ✅ Nicio comandă veche de verificat.")
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

# =====================================================
# 🕒 Checker periodic
# =====================================================
def run_order_checker():
    while True:
        try:
            bots = get_latest_settings()
            stb_bots = [b for b in bots if str(b.get("strategy", "")).lower() == "sell_buy"]

            if not stb_bots:
                logging.warning("⚠️ Niciun bot STB activ în settings.")
                time.sleep(3600)
                continue

            logging.info(f"\n🔍 Pornesc verificarea la {datetime.now(timezone.utc).isoformat()}...\n")
            for bot in stb_bots:
                symbol = bot["symbol"]
                client = init_client(bot["api_key"], bot["api_secret"], bot["api_passphrase"])
                check_old_orders(client, symbol)

            logging.info("✅ Verificarea STB s-a terminat. Următoarea în 1 oră.\n")
            time.sleep(3600)

        except Exception as e:
            logging.error(f"❌ Eroare în STB order_checker: {e}")
            time.sleep(60)

# =====================================================
# 🤖 Bot principal STB
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

    if buy_discount > 1:
        buy_discount = buy_discount / 100.0

    logging.info(f"[{symbol}] ⚙️ STB bot started | amount={amount}, buy-={buy_discount*100:.2f}%, cycle={cycle_delay/3600}h")

    while True:
        try:
            client = init_client(api_key, api_secret, api_passphrase)
            cycle_id = str(uuid.uuid4())
            logging.info(f"[{symbol}] 🧠 New STB cycle {cycle_id} started...")

            # 1️⃣ SELL MARKET
            sell_id = market_sell(client, symbol, amount, "STB")
            if not sell_id:
                logging.warning(f"[{symbol}] ⚠️ Market SELL failed — skipping cycle.")
                time.sleep(cycle_delay)
                continue

            safe_save_order(symbol, "SELL", 0, "pending", {"order_id": sell_id, "cycle_id": cycle_id, "strategy": "STB"})
            ok, avg_price = wait_market_execution(client, symbol, sell_id, amount, check_delay, cycle_id)
            if not ok or avg_price <= 0:
                time.sleep(cycle_delay)
                continue

            # 2️⃣ BUY LIMIT
            buy_price = adjust_price_to_tick(avg_price * (1 - buy_discount))
            buy_id = place_limit_buy(client, symbol, amount, buy_price, "STB")
            if not buy_id:
                logging.warning(f"[{symbol}] ⚠️ Limit BUY failed — skipping cycle.")
                time.sleep(cycle_delay)
                continue

            safe_save_order(symbol, "BUY", buy_price, "open", {"order_id": buy_id, "cycle_id": cycle_id, "strategy": "STB"})
            logging.info(f"[{symbol}] 🟢 BUY limit placed @ {buy_price} (-{buy_discount*100:.2f}%)")

            # Așteaptă următorul ciclu
            logging.info(f"[{symbol}] ⏳ Cycle complete → waiting {cycle_delay/3600}h\n")
            time.sleep(cycle_delay)

        except Exception as e:
            logging.error(f"[{symbol}] ❌ Error: {e}")
            time.sleep(30)

# =====================================================
# 🚀 Start doar pentru strategia STB
# =====================================================
def start_stb_bot():
    bots = get_latest_settings()
    stb_bots = [b for b in bots if str(b.get("strategy", "")).lower() == "sell_buy"]

    if not stb_bots:
        logging.warning("⚠️ No SELL_BUY bots found in Supabase.")
        return

    for i, settings in enumerate(stb_bots):
        threading.Thread(target=run_bot, args=(settings,), daemon=True).start()
        logging.info(f"🕒 Delay 10s înainte de următorul bot ({i+1}/{len(stb_bots)})...")
        time.sleep(10)

    threading.Thread(target=run_order_checker, daemon=True).start()
    while True:
        time.sleep(60)


if __name__ == "__main__":
    start_stb_bot()
