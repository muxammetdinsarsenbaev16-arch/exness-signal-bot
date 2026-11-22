import os
import sqlite3
import json
import time
from datetime import datetime, timezone, timedelta
import requests
import anthropic

# ==============================================================================
# 1. SOZLAMALAR VA MUHIT O'ZGARUVCHILARI (ENV / SECRETS)
# ==============================================================================
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

DB_PATH = "exness_rr_signals.db"
LESSONS_FILE = "rr_lessons.txt"
PAIRS = ["XAUUSD", "EURUSD", "GBPUSD"]
MIN_RR_DEFAULT = 3.0
CHECK_INTERVAL_SECONDS = 300       # Har 5 daqiqada tekshiradi
SELF_LEARN_INTERVAL_DAYS = 7     # Har 7 kunda R statistikasi asosida tahlil qiladi
PENDING_EXPIRE_HOURS = 6         # 6 soatda kirilmasa signal bekor qilinadi

SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
}

client = anthropic.Anthropic(api_key=CLAUDE_API_KEY) if CLAUDE_API_KEY else None


# ==============================================================================
# 2. BAZA (SQLITE) VA R HISOB-KITOBI
# ==============================================================================
def get_conn():
    return sqlite3.connect(DB_PATH, timeout=30)


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT NOT NULL,
            direction TEXT NOT NULL,          -- BUY / SELL
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            planned_rr REAL NOT NULL,
            be_trigger REAL NOT NULL,
            status TEXT NOT NULL,             -- PENDING, ACTIVE, BE_MOVED, CLOSED_TP, CLOSED_BE, CLOSED_SL, CANCELLED
            result_r REAL DEFAULT 0.0,        -- Har doim R hisobida: +planned_rr, 0.0, -1.0
            confirmations TEXT NOT NULL,
            session TEXT NOT NULL,
            created_at TEXT NOT NULL,
            closed_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS meta_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    c.execute("INSERT OR IGNORE INTO meta_state (key, value) VALUES ('last_self_learn', '')")
    conn.commit()
    conn.close()


def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram xatosi: {e}")


def calculate_rr(direction: str, entry: float, sl: float, tp: float) -> float:
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    reward = (tp - entry) if direction == "BUY" else (entry - tp)
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)


def get_rr_stats_text() -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT pair, COUNT(*), COALESCE(SUM(result_r), 0.0), COALESCE(AVG(result_r), 0.0)
        FROM signals
        WHERE status IN ('CLOSED_TP', 'CLOSED_BE', 'CLOSED_SL')
        GROUP BY pair
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "Hali yopilgan signallar yo'q (0.0R)."

    lines = []
    total_r = 0.0
    total_count = 0
    for pair, cnt, sum_r, avg_r in rows:
        total_r += sum_r
        total_count += cnt
        lines.append(f"• <b>{pair}</b>: {cnt} ta | Jami: <b>{sum_r:+.2f}R</b> | O'rtacha: <b>{avg_r:+.2f}R</b>")

    avg_all = total_r / total_count if total_count else 0.0
    lines.append(f"• <b>JAMI</b>: {total_count} ta | Natija: <b>{total_r:+.2f}R</b> | O'rtacha: <b>{avg_all:+.2f}R</b>")
    return "\n".join(lines)


def has_duplicate_signal(pair: str, direction: str, entry: float) -> bool:
    tolerance = 2.5 if pair == "XAUUSD" else 0.0008
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM signals
        WHERE pair = ? AND direction = ?
          AND status IN ('PENDING', 'ACTIVE', 'BE_MOVED')
          AND ABS(entry - ?) <= ?
    """, (pair, direction, entry, tolerance))
    cnt = c.fetchone()[0]
    conn.close()
    return cnt > 0


def expire_stale_pending(max_hours: int = PENDING_EXPIRE_HOURS):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_hours)).isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, pair, direction FROM signals
        WHERE status = 'PENDING' AND created_at < ?
    """, (cutoff,))
    rows = c.fetchall()
    for s_id, pair, direction in rows:
        c.execute(
            "UPDATE signals SET status = 'CANCELLED', result_r = 0.0, closed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), s_id),
        )
        send_telegram(
            f"⌛ <b>SIGNAL BEKOR QILINDI (#{s_id})</b>\n"
            f"{pair} ({direction}) — {max_hours} soat ichida kirish narxiga yetmadi (<b>0.0R</b>)."
        )
    conn.commit()
    conn.close()


# ==============================================================================
# 3. BOZOR MA'LUMOTLARI VA 5 TA TASDIQ ANALIZATORI
# ==============================================================================
def fetch_candles(symbol_td: str, interval: str, outputsize: int = 120) -> list[dict]:
    if not TWELVEDATA_API_KEY:
        return []
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol_td,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=15).json()
    values = resp.get("values")
    if not values or not isinstance(values, list):
        return []

    return [
        {
            "datetime": item["datetime"],
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
        }
        for item in reversed(values)
    ]


def fetch_market_snapshot() -> dict:
    prices = {}
    candles = {}
    for pair, td_symbol in SYMBOL_MAP.items():
        h4_c = fetch_candles(td_symbol, "4h", outputsize=100)
        m15_c = fetch_candles(td_symbol, "15m", outputsize=120)
        m5_c = fetch_candles(td_symbol, "5m", outputsize=120)
        if m5_c:
            prices[pair] = m5_c[-1]["close"]
        candles[pair] = {"H4": h4_c, "M15": m15_c, "M5": m5_c}
    return {"prices": prices, "candles": candles}


def detect_liquidity_sweep(candles: list[dict], lookback: int = 20) -> dict | None:
    if len(candles) < lookback + 2:
        return None
    recent = candles[-(lookback + 1):-1]
    last_c = candles[-1]
    prev_high = max(x["high"] for x in recent)
    prev_low = min(x["low"] for x in recent)

    if last_c["high"] > prev_high and last_c["close"] < prev_high:
        return {"type": "BEARISH_SWEEP", "swept_level": prev_high, "wick_high": last_c["high"]}
    if last_c["low"] < prev_low and last_c["close"] > prev_low:
        return {"type": "BULLISH_SWEEP", "swept_level": prev_low, "wick_low": last_c["low"]}
    return None


def detect_fvg(candles: list[dict]) -> dict | None:
    if len(candles) < 3:
        return None
    c1, _, c3 = candles[-3], candles[-2], candles[-1]
    if c1["high"] < c3["low"]:
        return {"type": "BULLISH_FVG", "low": c1["high"], "high": c3["low"]}
    if c1["low"] > c3["high"]:
        return {"type": "BEARISH_FVG", "low": c3["high"], "high": c1["low"]}
    return None


def detect_snr_levels(h4_candles: list[dict], lookback: int = 50) -> dict:
    if len(h4_candles) < lookback:
        return {"supports": [], "resistances": []}
    sub = h4_candles[-lookback:]
    supports, resistances = [], []
    for i in range(1, len(sub) - 1):
        p, c, n = sub[i - 1], sub[i], sub[i + 1]
        if c["low"] < p["low"] and c["low"] < n["low"]:
            supports.append(c["low"])
        if c["high"] > p["high"] and c["high"] > n["high"]:
            resistances.append(c["high"])
    return {
        "supports": sorted(set(supports))[-3:],
        "resistances": sorted(set(resistances))[-3:],
    }


def analyze_5_confirmations(pair: str, pair_candles: dict, session: str, lessons: str) -> dict | None:
    if not client:
        return None
    h4 = pair_candles.get("H4", [])
    m15 = pair_candles.get("M15", [])
    m5 = pair_candles.get("M5", [])
    if len(h4) < 50 or len(m15) < 25 or len(m5) < 10:
        return None

    snr = detect_snr_levels(h4)
    sweep = detect_liquidity_sweep(m15, lookback=20)
    fvg = detect_fvg(m5)

    if not sweep and not fvg:
        return None

    prompt = f"""
    Sen ICT, Malaysian SNR va R:R risk-menejment bo'yicha qat'iy signal tahlilchisan.
    Exness bozori ({pair}) uchun faqat quyidagi 5 ta tasdiqning BARCHASI (kamida 5 ta aniq tasdiq) mavjud bo'lsagina signal ber.
    Birortasi shubhali yoki yetishmayotgan bo'lsa, "approve": false qaytar.

    5 MAJBURIY TASDIQ:
    1. Malaysian SNR (H4): Narx muhim Support/Resistance darajasiga yaqin yoki uning zonasida.
    2. ICT Killzone: Sessiya "{session}" (London yoki New York Killzone).
    3. Liquidity Sweep: M15/bozorda likvidlik yechilishi.
    4. MSS / CHoCH + Displacement: M15/M5 da impulsli struktura sinishi.
    5. FVG + R:R >= 3.0: FVG zonasi mavjud va TP/SL nisbati kamida 3.0R.

    Joriy narx: {m5[-1]["close"]}
    H4 SNR: {json.dumps(snr)}
    M15 Sweep: {json.dumps(sweep)}
    M5 FVG: {json.dumps(fvg)}
    Oxirgi 5 ta M5 sham: {json.dumps(m5[-5:])}
    O'z-o'zini o'rganish qoidalari (lessons): {lessons}

    Qoidalar:
    - be_trigger: BUY da entry va tp oralig'ining ~50% nuqtasi; SELL da entry va tp oralig'ining ~50% nuqtasi.
    - Natijani FAQAT quyidagi JSON formatda yoz:
    {{
        "approve": true,
        "direction": "BUY",
        "entry": 0.0,
        "sl": 0.0,
        "tp": 0.0,
        "be_trigger": 0.0,
        "confirmations": [
            "1. Malaysian SNR: ...",
            "2. Killzone: ...",
            "3. Liquidity Sweep: ...",
            "4. MSS/CHoCH: ...",
            "5. FVG & R:R: ..."
        ]
    }}
    """

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").replace("json", "", 1).strip()

    data = json.loads(raw)
    if not data.get("approve") or len(data.get("confirmations", [])) < 5:
        return None
    return data


# ==============================================================================
# 4. SIGNALNI TO'LIQ KUZATISH (PENDING -> ACTIVE -> BE_MOVED -> TP/BE/SL)
# ==============================================================================
def monitor_signals_rr(current_prices: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, pair, direction, entry, sl, tp, planned_rr, be_trigger, status
        FROM signals
        WHERE status IN ('PENDING', 'ACTIVE', 'BE_MOVED')
    """)
    active_rows = c.fetchall()

    for s_id, pair, direction, entry, sl, tp, planned_rr, be_trigger, status in active_rows:
        price = current_prices.get(pair)
        if price is None:
            continue

        now_iso = datetime.now(timezone.utc).isoformat()

        if status == "PENDING":
            entered = (direction == "BUY" and price <= entry) or (
                direction == "SELL" and price >= entry
            )
            if entered:
                c.execute("UPDATE signals SET status = 'ACTIVE' WHERE id = ?", (s_id,))
                send_telegram(
                    f"🟢 <b>SIGNAL AKTIVLASHDI (#{s_id}) — {pair}</b>\n"
                    f"Yo'nalish: <b>{direction}</b> | Entry: <code>{entry}</code>\n"
                    f"SL: <code>{sl}</code> | TP: <code>{tp}</code> (<b>+{planned_rr}R</b>)"
                )

        elif status == "ACTIVE":
            hit_tp = (direction == "BUY" and price >= tp) or (direction == "SELL" and price <= tp)
            hit_sl = (direction == "BUY" and price <= sl) or (direction == "SELL" and price >= sl)
            hit_be = (direction == "BUY" and price >= be_trigger) or (
                direction == "SELL" and price <= be_trigger
            )

            if hit_tp:
                c.execute(
                    "UPDATE signals SET status = 'CLOSED_TP', result_r = ?, closed_at = ? WHERE id = ?",
                    (planned_rr, now_iso, s_id),
                )
                send_telegram(
                    f"🎯 <b>TP URILDI (#{s_id}) — {pair}</b>\n"
                    f"Natija: <b>+{planned_rr}R</b>\n\n{get_rr_stats_text()}"
                )
            elif hit_sl:
                c.execute(
                    "UPDATE signals SET status = 'CLOSED_SL', result_r = -1.0, closed_at = ? WHERE id = ?",
                    (now_iso, s_id),
                )
                send_telegram(
                    f"🛑 <b>SL URILDI (#{s_id}) — {pair}</b>\n"
                    f"Natija: <b>-1.0R</b>\n\n{get_rr_stats_text()}"
                )
            elif hit_be:
                c.execute("UPDATE signals SET status = 'BE_MOVED', sl = ? WHERE id = ?", (entry, s_id))
                send_telegram(
                    f"🛡 <b>BE ZONASIGA O'TDI (#{s_id}) — {pair}</b>\n"
                    f"SL <code>{entry}</code> ga ko'chirildi. Endi minimal himoya: <b>0.0R</b>."
                )

        elif status == "BE_MOVED":
            hit_tp = (direction == "BUY" and price >= tp) or (direction == "SELL" and price <= tp)
            hit_be_stop = (direction == "BUY" and price <= entry) or (
                direction == "SELL" and price >= entry
            )

            if hit_tp:
                c.execute(
                    "UPDATE signals SET status = 'CLOSED_TP', result_r = ?, closed_at = ? WHERE id = ?",
                    (planned_rr, now_iso, s_id),
                )
                send_telegram(
                    f"🎯 <b>BE'DAN KEYIN TP URILDI (#{s_id}) — {pair}</b>\n"
                    f"Natija: <b>+{planned_rr}R</b>\n\n{get_rr_stats_text()}"
                )
            elif hit_be_stop:
                c.execute(
                    "UPDATE signals SET status = 'CLOSED_BE', result_r = 0.0, closed_at = ? WHERE id = ?",
                    (now_iso, s_id),
                )
                send_telegram(
                    f"⚖️ <b>BE ZONADA YOPILDI (#{s_id}) — {pair}</b>\n"
                    f"Natija: <b>0.0R</b>\n\n{get_rr_stats_text()}"
                )

    conn.commit()
    conn.close()


# ==============================================================================
# 5. HAFTALIK O'Z-O'ZINI RIVOJLANTIRISH (FAQAT R HISOBIDA)
# ==============================================================================
def maybe_run_weekly_self_learning():
    if not client:
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM meta_state WHERE key = 'last_self_learn'")
    row = c.fetchone()
    last_str = row[0] if row and row[0] else ""

    now = datetime.now(timezone.utc)
    if last_str:
        last_dt = datetime.fromisoformat(last_str)
        if now - last_dt < timedelta(days=SELF_LEARN_INTERVAL_DAYS):
            conn.close()
            return

    c.execute("""
        SELECT pair, direction, planned_rr, result_r, confirmations, session
        FROM signals
        WHERE status IN ('CLOSED_TP', 'CLOSED_BE', 'CLOSED_SL')
        ORDER BY id DESC LIMIT 50
    """)
    history = c.fetchall()

    if len(history) < 5:
        conn.close()
        return

    prompt = f"""
    Sen ICT, Malaysian SNR va R:R tahlilchisan.
    Exness (XAUUSD, EURUSD, GBPUSD) signallari natijasini FAQAT R (+R, 0.0R, -1.0R) ko'rsatkichlari asosida tahlil qil.
    Pul miqdorini yozma — faqat R bilan bahola.

    Tarix (pair, direction, planned_rr, result_r, confirmations, session):
    {json.dumps(history, ensure_ascii=False)}

    Quyidagilarni aniq yoz:
    1. Har bir juftlik bo'yicha jami R va eng kuchli tasdiqlar.
    2. -1.0R yoki 0.0R ko'p bo'layotgan sessiya yoki holat.
    3. Keyingi hafta uchun 3 ta aniq filtr qoidasi.
    """

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    report = resp.content[0].text.strip()

    with open(LESSONS_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    c.execute(
        "INSERT OR REPLACE INTO meta_state (key, value) VALUES ('last_self_learn', ?)",
        (now.isoformat(),),
    )
    conn.commit()
    conn.close()

    send_telegram(f"🧠 <b>HAFTALIK O'Z-O'ZINI RIVOJLANTIRISH (R)</b>\n\n{report[:3500]}")


# ==============================================================================
# 6. ASOSIY SIGNAL SIKLI
# ==============================================================================
def run_signal_bot():
    init_db()
    send_telegram(
        "📡 <b>EXNESS SIGNAL BOT ISHGA TUSHDI</b>\n"
        "Juftliklar: <b>XAUUSD, EURUSD, GBPUSD</b>\n"
        "Talab: <b>Kamida 5 ta tasdiq</b> | Hisob: <b>R</b>"
    )

    while True:
        try:
            expire_stale_pending(PENDING_EXPIRE_HOURS)
            snapshot = fetch_market_snapshot()
            prices = snapshot.get("prices", {})
            candles_by_pair = snapshot.get("candles", {})

            monitor_signals_rr(prices)
            maybe_run_weekly_self_learning()

            hour_utc = datetime.now(timezone.utc).hour
            session = (
                "London Killzone"
                if 7 <= hour_utc <= 10
                else "New York Killzone"
                if 12 <= hour_utc <= 15
                else None
            )

            if session:
                lessons = ""
                if os.path.exists(LESSONS_FILE):
                    with open(LESSONS_FILE, "r", encoding="utf-8") as f:
                        lessons = f.read()

                for pair in PAIRS:
                    pair_candles = candles_by_pair.get(pair)
                    if not pair_candles:
                        continue

                    candidate = analyze_5_confirmations(pair, pair_candles, session, lessons)
                    if not candidate:
                        continue

                    rr_val = calculate_rr(
                        candidate["direction"],
                        float(candidate["entry"]),
                        float(candidate["sl"]),
                        float(candidate["tp"]),
                    )
                    if rr_val < MIN_RR_DEFAULT:
                        continue

                    if has_duplicate_signal(pair, candidate["direction"], float(candidate["entry"])):
                        continue

                    conn = get_conn()
                    c = conn.cursor()
                    c.execute(
                        """
                        INSERT INTO signals (
                            pair, direction, entry, sl, tp, planned_rr, be_trigger,
                            status, result_r, confirmations, session, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0.0, ?, ?, ?)
                        """,
                        (
                            pair,
                            candidate["direction"],
                            float(candidate["entry"]),
                            float(candidate["sl"]),
                            float(candidate["tp"]),
                            rr_val,
                            float(candidate["be_trigger"]),
                            json.dumps(candidate["confirmations"], ensure_ascii=False),
                            session,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    s_id = c.lastrowid
                    conn.commit()
                    conn.close()

                    conf_text = "\n".join(f"• {x}" for x in candidate["confirmations"])
                    send_telegram(
                        f"🚨 <b>YANGI SIGNAL (#{s_id}) — {pair}</b>\n"
                        f"Yo'nalish: <b>{candidate['direction']}</b> ({session})\n"
                        f"Entry: <code>{candidate['entry']}</code> | SL: <code>{candidate['sl']}</code> | TP: <code>{candidate['tp']}</code>\n"
                        f"Reja: <b>+{rr_val}R</b> | BE: <code>{candidate['be_trigger']}</code>\n"
                        f"Tasdiqlar:\n{conf_text}"
                    )

        except Exception as e:
            send_telegram(f"⚠️ <b>Xatolik:</b> <code>{e}</code>")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_signal_bot()
