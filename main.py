import time
import threading
import uuid
import os
import logging
from datetime import datetime, timezone
from exchange import (
    init_client,
    market_sell,
    market_buy,
    check_order_executed,
    place_limit_buy,
    place_limit_sell,
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
    filename="logs/honeybot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
console.setFormatter(formatter)
logging.getLogger("").addHandler(console)

logging.info("🚀 HONEYBOT Multi-Bot + Smart Cycle Recovery (dual strategy + live reload + staggered start + tick-size safe) started...\n")

# =====================================================
# 🧮 Tick Size Adjust
# =====================================================
def adjust_price_to_tick(price, tick_size=0.00001):
    """Ajustează prețul la tick size-ul permis de exchange (KuCoin)."""
    adjusted = round(round(price / tick_size) * tick_size, 5)
    return adjusted

# =====================================================
# 🧠 Order Checker
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
    logging.info(f"🟢 Updated {order_id}: {new_status} ({avg_price})")

    if new_status == "executed" and cycle_id:
        update_execution_time_and_profit(cycle_id)


def check_old_orders(client, symbol, strategy_label):
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
        logging.info(f"[{symbol}][{strategy_label}] ✅ Nicio comandă de verificat.")
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
            logging.info(f"[{symbol}][{strategy_label}] ✅ Ordin {side} executat: {order_id}")
        else:
            update_order_status(order_id, "pending")
            logging.info(f"[{symbol}][{strategy_label}] ⏳ Ordin {side} încă în așteptare: {order_id}")


def run_order_checker():
    while True:
        try:
            bots = get_latest_settings()
            if not bots:
                logging.warning("⚠️ Niciun bot activ în settings.")
                time.sleep(3600)
                continue

            logging.info(f"\n🔍 Pornesc verificarea la {datetime.now(timezone.utc).isoformat()}...\n")
            for bot in bots:
                symbol = bot["symbol"]
                strategy_label = bot.get("strategy", "SELL_BUY").upper()
                client = init_client(bot["api_key"], bot["api_secret"], bot["api_passphrase"])
                check_old_orders(client, symbol, strategy_label)

            logging.info("✅ Verificarea s-a terminat. Următoarea în 1 oră.\n")
            time.sleep(3600)
        except Exception as e:
            logging.error(f"❌ Eroare în order_checker: {e}")
            time.sleep(60)

# =====================================================
# 🤖 Bot principal cu discount normalizat + tick-size
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
    strategy = settings.get("strategy", "sell_buy")
    strategy_label = strategy.upper()

    # normalizează discount (dacă e introdus 5 în loc de 0.05)
    if buy_discount > 1:
        buy_discount = buy_discount / 100

    logging.info(f"[{symbol}][{strategy_label}] ⚙️ Started bot | amount={amount}, discount={buy_discount*100:.2f}%, cycle={cycle_delay/3600}h")

    while True:
        try:
            # ♻️ reîncarcă setările active + potrivire după symbol + strategy
            bots = get_latest_settings()
            for bot in bots:
                if bot["symbol"] == symbol and bot.get("strategy", "").lower() == strategy.lower():
                    settings = bot
                    buy_discount = float(bot["buy_discount"])
                    if buy_discount > 1:
                        buy_discount = buy_discount / 100
                    cycle_delay = int(bot["cycle_delay"])
                    strategy = bot.get("strategy", strategy)
                    strategy_label = strategy.upper()
                    break

            client = init_client(api_key, api_secret, api_passphrase)
            cycle_id = str(uuid.uuid4())
            logging.info(f"[{symbol}][{strategy_label}] 🧠 Running strategy...")

            # =====================================================
            # SELL → BUY
            # =====================================================
            if strategy == "sell_buy":
                sell_id = market_sell(client, symbol, amount, strategy_label)
                if not sell_id:
                    logging.warning(f"[{symbol}][{strategy_label}] ⚠️ Market SELL failed — skipping this cycle.")
                    time.sleep(cycle_delay)
                    continue

                save_order(symbol, "SELL", 0, "pending", {
                    "order_id": sell_id,
                    "cycle_id": cycle_id,
                    "strategy": strategy_label
                })

                executed, avg_price = False, 0
                while not executed:
                    time.sleep(check_delay)
                    executed, avg_price = check_order_executed(client, sell_id)
                    if executed:
                        supabase.table("orders").update(
                            {"status": "executed", "price": avg_price, "filled_size": amount,
                             "last_updated": datetime.now(timezone.utc).isoformat()}
                        ).eq("order_id", sell_id).execute()
                        logging.info(f"[{symbol}][{strategy_label}] ✅ SELL executat @ {avg_price}")

                buy_price = adjust_price_to_tick(avg_price * (1 - buy_discount))
                buy_id = place_limit_buy(client, symbol, amount, buy_price, strategy_label)
                if not buy_id:
                    logging.warning(f"[{symbol}][{strategy_label}] ⚠️ Limit BUY failed — skipping cycle.")
                    time.sleep(cycle_delay)
                    continue

                save_order(symbol, "BUY", buy_price, "open", {
                    "order_id": buy_id,
                    "cycle_id": cycle_id,
                    "strategy": strategy_label
                })
                logging.info(f"[{symbol}][{strategy_label}] 🟢 BUY limit placed @ {buy_price} (−{buy_discount*100:.2f}%)")

            # =====================================================
            # BUY → SELL
            # =====================================================
            elif strategy == "buy_sell":
                buy_id = market_buy(client, symbol, amount, strategy_label)
                if not buy_id:
                    logging.warning(f"[{symbol}][{strategy_label}] ⚠️ Market BUY failed — skipping this cycle.")
                    time.sleep(cycle_delay)
                    continue

                save_order(symbol, "BUY", 0, "pending", {
                    "order_id": buy_id,
                    "cycle_id": cycle_id,
                    "strategy": strategy_label
                })

                executed, avg_price = False, 0
                while not executed:
                    time.sleep(check_delay)
                    executed, avg_price = check_order_executed(client, buy_id)
                    if executed:
                        supabase.table("orders").update(
                            {"status": "executed", "price": avg_price, "filled_size": amount,
                             "last_updated": datetime.now(timezone.utc).isoformat()}
                        ).eq("order_id", buy_id).execute()
                        logging.info(f"[{symbol}][{strategy_label}] ✅ BUY executat @ {avg_price}")

                sell_price = adjust_price_to_tick(avg_price * (1 + buy_discount))
                sell_id = place_limit_sell(client, symbol, amount, sell_price, strategy_label)
                if not sell_id:
                    logging.warning(f"[{symbol}][{strategy_label}] ⚠️ Limit SELL failed — skipping cycle.")
                    time.sleep(cycle_delay)
                    continue

                save_order(symbol, "SELL", sell_price, "open", {
                    "order_id": sell_id,
                    "cycle_id": cycle_id,
                    "strategy": strategy_label
                })
                logging.info(f"[{symbol}][{strategy_label}] 🔴 SELL limit placed @ {sell_price} (+{buy_discount*100:.2f}%)")

            logging.info(f"[{symbol}][{strategy_label}] ⏳ Aștept următorul ciclu ({cycle_delay/3600}h)...\n")
            time.sleep(cycle_delay)

        except Exception as e:
            logging.error(f"[{symbol}][{strategy_label}] ❌ Error: {e}")
            time.sleep(30)

# =====================================================
# 🚀 Start all bots (10s staggered start)
# =====================================================
def start_all_bots():
    bots = get_latest_settings()
    if not bots:
        logging.warning("⚠️ No active bots found in Supabase.")
        return

    for i, settings in enumerate(bots):
        threading.Thread(target=run_bot, args=(settings,), daemon=True).start()
        logging.info(f"🕒 Delay 10s înainte de pornirea următorului bot ({i+1}/{len(bots)})...")
        time.sleep(10)

    threading.Thread(target=run_order_checker, daemon=True).start()

    while True:
        time.sleep(60)


if __name__ == "__main__":
    start_all_bots()
