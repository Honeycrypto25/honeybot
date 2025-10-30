import os
import uuid
from datetime import datetime
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
# 📘 Functions
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
    Dacă e SELL -> generează un cycle_id nou.
    Dacă e BUY -> folosește cycle_id-ul din extra (dacă există).
    """
    data = {
        "symbol": symbol,
        "side": side,
        "price": price,
        "status": status,
        "last_updated": datetime.utcnow().isoformat()
    }

    # Dacă e SELL -> generează cycle_id nou
    if side == "SELL":
        data["cycle_id"] = str(uuid.uuid4())
        data["created_at"] = datetime.utcnow().isoformat()

    # Dacă e BUY -> folosește același cycle_id din extra
    if extra and "cycle_id" in extra:
        data["cycle_id"] = extra["cycle_id"]

    # Include restul informațiilor suplimentare (order_id, etc.)
    if extra:
        data.update(extra)

    supabase.table("orders").insert(data).execute()
    print(f"[{symbol}] 💾 Saved {side} ({status}) | price={price} | cycle_id={data.get('cycle_id')}")


def update_execution_time(cycle_id):
    """
    Calculează și actualizează durata (execution_time)
    între SELL și BUY pentru un anumit cycle_id.
    """
    try:
        result = supabase.table("orders").select("created_at, side").eq("cycle_id", cycle_id).execute()
        orders = result.data or []
        if len(orders) < 2:
            return  # nu avem pereche completă SELL + BUY

        sell_time = None
        buy_time = None

        for o in orders:
            if o["side"] == "SELL":
                sell_time = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
            elif o["side"] == "BUY":
                buy_time = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))

        if sell_time and buy_time:
            duration = buy_time - sell_time
            supabase.table("orders").update({"execution_time": str(duration)}).eq("cycle_id", cycle_id).execute()
            print(f"🕒 Updated execution_time for {cycle_id}: {duration}")
    except Exception as e:
        print("❌ Error updating execution_time:", e)
