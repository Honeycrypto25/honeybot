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
print(f"✅ Connected to Supabase project: {SUPABASE_URL.split('//')[1].split('.')[0]} (STB)")


# =====================================================
# 📘 SETTINGS – doar boti SELL_BUY
# =====================================================
def get_latest_settings():
    """Returnează toate setările active STB (SELL_BUY) din 'settings'."""
    try:
        data = supabase.table("settings").select("*").eq("active", True).execute()
        bots = data.data or []
        bots = [b for b in bots if str(b.get("strategy", "")).upper() in ("SELL_BUY", "STB")]
        print(f"♻️ Reloaded {len(bots)} active STB setting(s) from Supabase.")
        return bots
    except Exception as e:
        print(f"❌ Error reading latest settings (STB): {e}")
        return []


# =====================================================
# 💾 Salvare ordine (strategie SELL → BUY)
# =====================================================
def save_order(symbol, side, price, status, extra=None):
    """Salvează un ordin în tabelul 'orders' pentru strategia SELL → BUY (STB)."""
    data = {
        "symbol": symbol,
        "side": side,
        "price": float(price),
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "strategy": "SELL_BUY",
    }

    # SELL → ciclu nou dacă nu avem deja cycle_id
    if side.upper() == "SELL" and not (extra and extra.get("cycle_id")):
        data["cycle_id"] = str(uuid.uuid4())

    # Extra meta (order_id, cycle_id, etc.)
    if extra:
        data.update(extra)

    supabase.table("orders").insert(data).execute()
    print(
        f"[STB][{symbol}] 💾 Saved {side} ({status}) | price={price} | cycle_id={data.get('cycle_id')}"
    )


# =====================================================
# 💰 Profit per cycle (SELL → BUY, profit în USDT)
# =====================================================
def update_execution_time_and_profit(cycle_id: str):
    """
    Calculează durata și profitul efectiv pentru un ciclu SELL → BUY (STB).
    Profitul se calculează în USDT.
    """
    try:
        result = (
            supabase.table("orders")
            .select("side, price, created_at, last_updated, symbol, filled_size, strategy")
            .eq("cycle_id", cycle_id)
            .eq("strategy", "SELL_BUY")
            .eq("status", "executed")
            .execute()
        )
        orders = result.data or []
        if len(orders) < 2:
            print(f"[STB] ⚠️ Skipping profit calc: incomplete cycle {cycle_id}")
            return

        # Separăm SELL / BUY și ignorăm ordinele fără preț
        sells = [
            o for o in orders
            if str(o["side"]).upper() == "SELL" and float(o.get("price") or 0) > 0
        ]
        buys = [
            o for o in orders
            if str(o["side"]).upper() == "BUY" and float(o.get("price") or 0) > 0
        ]

        if not sells or not buys:
            print(f"[STB] ⚠️ Missing SELL/BUY prices for cycle {cycle_id}")
            return

        # Entry = primul SELL, Exit = ultimul BUY
        first_sell = sorted(sells, key=lambda o: o["created_at"])[0]
        last_buy = sorted(buys, key=lambda o: o["created_at"])[-1]

        symbol = first_sell["symbol"]
        sell_price = float(first_sell["price"])
        buy_price = float(last_buy["price"])

        sell_time = datetime.fromisoformat(
            (first_sell.get("last_updated") or first_sell["created_at"]).replace("Z", "+00:00")
        )
        buy_time = datetime.fromisoformat(
            (last_buy.get("last_updated") or last_buy["created_at"]).replace("Z", "+00:00")
        )

        sell_qty = float(first_sell.get("filled_size") or 0)
        buy_qty = float(last_buy.get("filled_size") or 0)

        qty = 0.0
        if sell_qty > 0 and buy_qty > 0:
            qty = min(sell_qty, buy_qty)
        else:
            qty = max(sell_qty, buy_qty)

        if sell_price <= 0 or buy_price <= 0 or qty <= 0:
            print(f"[STB] ⚠️ Invalid prices/qty for cycle {cycle_id}")
            return

        # Profit în USDT – raportat la prețul de SELL (intrare)
        profit_percent = round(((sell_price - buy_price) / sell_price) * 100, 2)
        profit_usdt = round((sell_price - buy_price) * qty, 6)
        profit_coin = 0.0  # pentru STB nu ne interesează COIN

        execution_time = abs(buy_time - sell_time)

        supabase.table("profit_per_cycle").upsert(
            {
                "cycle_id": cycle_id,
                "symbol": symbol,
                "strategy": "SELL_BUY",
                "sell_price": sell_price,
                "buy_price": buy_price,
                "profit_percent": profit_percent,
                "profit_usdt": profit_usdt,
                "profit_coin": profit_coin,
                "execution_time": str(execution_time),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()

        print(
            f"💰 [STB][{symbol}] cycle {cycle_id} → {profit_percent}% | USDT={profit_usdt}"
        )

    except Exception as e:
        print(f"❌ [STB] Error updating profit for {cycle_id}: {e}")
