import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

# =====================================================
# 🔌 Load environment variables (.env)
# =====================================================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Missing SUPABASE_URL or SUPABASE_KEY. Check your .env file.")

# =====================================================
# ⚙️ Create Supabase client
# =====================================================
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
print(f"✅ Connected to Supabase project: {SUPABASE_URL.split('//')[1].split('.')[0]}")

# =====================================================
# 📘 FUNCTIONS
# =====================================================

def get_all_active_bots():
    """Return all active bots from 'settings' table"""
    result = supabase.table("settings").select("*").eq("active", True).execute()
    bots = result.data or []
    print(f"🔍 Found {len(bots)} active bot(s).")
    return bots


def save_order(symbol, side, price, status, extra=None):
    """
    Salvează un ordin în tabelul 'orders'.
    - SELL → generează cycle_id nou
    - BUY → folosește cycle_id primit în `extra`
    """
    data = {
        "symbol": symbol,
        "side": side,
        "price": float(price),
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    # SELL → creează un nou ciclu
    if side.upper() == "SELL":
        data["cycle_id"] = str(uuid.uuid4())

    # BUY → folosește cycle_id existent
    if extra and "cycle_id" in extra:
        data["cycle_id"] = extra["cycle_id"]

    # Alte câmpuri suplimentare (ex: order_id, filled_size etc.)
    if extra:
        data.update(extra)

    supabase.table("orders").insert(data).execute()
    print(f"[{symbol}] 💾 Saved {side} ({status}) | price={price} | cycle_id={data.get('cycle_id')}")


def update_execution_time_and_profit(cycle_id):
    """
    Calculează durata și profitul efectiv în USDT pentru fiecare ciclu complet (SELL + BUY).
    """
    try:
        result = supabase.table("orders") \
            .select("side, price, created_at, last_updated, symbol, filled_size") \
            .eq("cycle_id", cycle_id) \
            .execute()

        orders = result.data or []
        if len(orders) < 2:
            print(f"⚠️ Skipping execution_time: incomplete cycle {cycle_id}")
            return

        symbol = None
        sell_price = buy_price = sell_time = buy_time = filled_size = None

        for o in orders:
            symbol = o["symbol"]
            if o["side"].upper() == "SELL":
                sell_price = float(o["price"])
                sell_time = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
                filled_size = float(o.get("filled_size") or 0)
            elif o["side"].upper() == "BUY":
                buy_price = float(o["price"])
                src = o.get("last_updated") or o.get("created_at")
                buy_time = datetime.fromisoformat(str(src).replace("Z", "+00:00"))

        if not (sell_price and buy_price):
            print(f"⚠️ Missing price data for {cycle_id}")
            return

        # Profit procentual
        profit_percent = round(((sell_price - buy_price) / buy_price) * 100, 2)

        # Profit efectiv în USDT
        profit_usdt = round((sell_price - buy_price) * filled_size, 6)

        # Durata execuției
        execution_time = None
        if sell_time and buy_time:
            execution_time = buy_time - sell_time

        # 🧾 Salvare / actualizare în profit_per_cycle
        supabase.table("profit_per_cycle").upsert({
            "cycle_id": cycle_id,
            "symbol": symbol,
            "sell_price": sell_price,
            "buy_price": buy_price,
            "profit_percent": profit_percent,
            "profit_usdt": profit_usdt,
            "execution_time": str(execution_time) if execution_time else None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }).execute()

        print(f"💰 [{symbol}] Profit updated: {profit_percent}% → {profit_usdt} USDT")

    except Exception as e:
        print(f"❌ Error updating profit for {cycle_id}: {e}")
