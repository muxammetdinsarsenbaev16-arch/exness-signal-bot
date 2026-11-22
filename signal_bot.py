import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

CONFIG_FILE = "bot_config.json"
BACKUP_FILE = os.path.abspath(__file__) + ".bak"
PAIRS = ["XAUUSD", "EURUSD", "GBPUSD"]

DEFAULT_CONFIG = {
    "min_rr": 2.0,
    "min_confirmations": 3,
    "all_sessions": True,
    "check_interval_sec": 300,
    "total_r": 0.0,
    "active_signals": [],
    "closed_results": [],
}

USER_KEYBOARD = {
    "keyboard": [
        [{"text": "📈 Natijalar"}, {"text": "📊 Holat"}],
        [{"text": "ℹ️ Yordam"}],
    ],
    "resize_keyboard": True,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["active_signals"] = list(DEFAULT_CONFIG["active_signals"])
    cfg["closed_results"] = list(DEFAULT_CONFIG["closed_results"])
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception as exc:
            logging.warning("Konfiguratsiyani o'qishda xato: %s", exc)
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logging.warning("Konfiguratsiyani saqlashda xato: %s", exc)


def is_admin(chat_id: str) -> bool:
    return bool(TELEGRAM_CHAT_ID) and str(chat_id).strip() == str(TELEGRAM_CHAT_ID).strip()


def http_get_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        logging.warning("HTTP so'rov xatosi: %s", exc)
        return {}
    except Exception as exc:
        logging.warning("JSON o'qishda xato: %s", exc)
        return {}


def tg_request(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        logging.warning("Telegram so'rov xatosi (%s): %s", method, exc)
        return {}
    except Exception as exc:
        logging.warning("Telegram kutilmagan xato (%s): %s", method, exc)
        return {}


def send_message(chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    if not chat_id:
        return
    payload = {
        "chat_id": str(chat_id),
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    tg_request("sendMessage", payload)


def send_document(chat_id: str, file_path: str, caption: str = "") -> None:
    if not TELEGRAM_BOT_TOKEN or not chat_id or not os.path.exists(file_path):
        return
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode("utf-8"))
    if caption:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode("utf-8"))
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: text/x-python\r\n\r\n")
    body.extend(file_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            resp.read()
    except Exception as exc:
        logging.warning("Fayl yuborishda xato: %s", exc)


def download_telegram_file(file_id: str) -> str | None:
    if not TELEGRAM_BOT_TOKEN or not file_id:
        return None
    info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={urllib.parse.quote(file_id)}"
    info = http_get_json(info_url)
    if not info.get("ok"):
        return None
    file_path = (info.get("result") or {}).get("file_path")
    if not file_path:
        return None
    dl_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    try:
        with urllib.request.urlopen(dl_url, timeout=25) as resp:
            return resp.read().decode("utf-8")
    except Exception as exc:
        logging.warning("Telegram faylni yuklashda xato: %s", exc)
        return None


def strip_markdown_codeblock(code: str) -> str:
    text = code.strip()
    if text.startswith("```python"):
        text = text[len("```python"):].lstrip("\r\n")
    elif text.startswith("```"):
        text = text[3:].lstrip("\r\n")
    if text.endswith("```"):
        text = text[:-3].rstrip("\r\n")
    return text


def apply_code_safely(new_code: str, cfg: dict, chat_id: str) -> None:
    clean_code = strip_markdown_codeblock(new_code)
    if not clean_code.strip():
        send_message(chat_id, "❌ Kod bo'sh bo'lmasligi kerak.", reply_markup=USER_KEYBOARD)
        return

    try:
        compile(clean_code, __file__, "exec")
    except SyntaxError as exc:
        send_message(
            chat_id,
            f"❌ Sintaksis xatosi ({exc.lineno}-qator): {exc.msg}\nKod o'zgartirilmadi.",
            reply_markup=USER_KEYBOARD,
        )
        return

    target_path = os.path.abspath(__file__)
    try:
        if os.path.exists(target_path):
            with open(target_path, "r", encoding="utf-8") as src:
                old_code = src.read()
            with open(BACKUP_FILE, "w", encoding="utf-8") as bak:
                bak.write(old_code)

        with open(target_path, "w", encoding="utf-8") as dst:
            dst.write(clean_code)

        save_config(cfg)
        send_message(
            chat_id,
            "✅ Kod muvaffaqiyatli yangilandi va sintaksis tekshirildi. Bot qayta ishga tushmoqda...",
            reply_markup=USER_KEYBOARD,
        )
        time.sleep(1)
        os._exit(0)
    except Exception as exc:
        send_message(chat_id, f"❌ Kodni saqlashda xato: {exc}", reply_markup=USER_KEYBOARD)
    def record_closed_result(cfg: dict, pair: str, outcome: str, r_value: float) -> None:
        entry = {
        "pair": pair,
        "outcome": outcome,
        "r": round(float(r_value), 2),
    }
    cfg.setdefault("closed_results", []).append(entry)
    cfg["closed_results"] = cfg["closed_results"][-15:]
    cfg["total_r"] = round(float(cfg.get("total_r", 0.0)) + float(r_value), 2)
    save_config(cfg)


def format_symbol_for_twelvedata(pair: str) -> str:
    if len(pair) == 6 and pair.isalpha():
        return f"{pair[:3]}/{pair[3:]}"
    return pair


def fetch_candles(pair: str, interval: str = "15min", outputsize: int = 40) -> list[dict]:
    if not TWELVEDATA_API_KEY:
        logging.warning("TWELVEDATA_API_KEY kiritilmagan.")
        return []

    symbol = format_symbol_for_twelvedata(pair)
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVEDATA_API_KEY,
        }
    )
    url = f"https://api.twelvedata.com/time_series?{params}"
    data = http_get_json(url)

    if data.get("status") == "error":
        logging.warning("TwelveData xatosi (%s): %s", pair, data.get("message"))
        return []

    values = data.get("values")
    if not isinstance(values, list) or len(values) < 20:
        return []

    candles: list[dict] = []
    for row in reversed(values):
        try:
            candles.append(
                {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "datetime": str(row.get("datetime", "")),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    return candles


def calc_ema(closes: list[float], period: int) -> float:
    if not closes:
        return 0.0
    k = 2.0 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema


def calc_atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_c = candles[i - 1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    recent = trs[-period:] if len(trs) >= period else trs
    return sum(recent) / len(recent) if recent else 0.0


def evaluate_setup(pair: str, candles: list[dict], min_rr: float, min_conf: int) -> dict | None:
    if len(candles) < 22:
        return None

    closes = [c["close"] for c in candles]
    last = candles[-1]
    prev = candles[-2]
    price = last["close"]

    ema_fast = calc_ema(closes[-20:], 8)
    ema_slow = calc_ema(closes[-25:], 21)
    atr = calc_atr(candles, 14)
    if atr <= 0 or price <= 0:
        return None

    recent_lows = [c["low"] for c in candles[-8:-1]]
    recent_highs = [c["high"] for c in candles[-8:-1]]
    swing_low = min(recent_lows) if recent_lows else last["low"]
    swing_high = max(recent_highs) if recent_highs else last["high"]

    buy_conf = 0
    if ema_fast > ema_slow:
        buy_conf += 1
    if price > ema_fast:
        buy_conf += 1
    if last["close"] > last["open"]:
        buy_conf += 1
    if last["low"] <= swing_low * 1.0005 and last["close"] > swing_low:
        buy_conf += 1
    if last["close"] > prev["high"]:
        buy_conf += 1

    sell_conf = 0
    if ema_fast < ema_slow:
        sell_conf += 1
    if price < ema_fast:
        sell_conf += 1
    if last["close"] < last["open"]:
        sell_conf += 1
    if last["high"] >= swing_high * 0.9995 and last["close"] < swing_high:
        sell_conf += 1
    if last["close"] < prev["low"]:
        sell_conf += 1

    decimals = 2 if "XAU" in pair else 5

    if buy_conf >= sell_conf and buy_conf >= min_conf:
        sl_dist = max(atr * 1.1, abs(price - swing_low))
        if sl_dist <= 0:
            return None
        sl = round(price - sl_dist, decimals)
        risk = price - sl
        tp = round(price + risk * min_rr, decimals)
        rr = round((tp - price) / risk, 2) if risk > 0 else 0.0
        if rr >= min_rr and tp > price > sl:
            return {
                "pair": pair,
                "side": "BUY",
                "entry": round(price, decimals),
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "confirmations": buy_conf,
                "be_moved": False,
                "opened_at": last["datetime"],
            }

    if sell_conf > buy_conf and sell_conf >= min_conf:
        sl_dist = max(atr * 1.1, abs(swing_high - price))
        if sl_dist <= 0:
            return None
        sl = round(price + sl_dist, decimals)
        risk = sl - price
        tp = round(price - risk * min_rr, decimals)
        rr = round((price - tp) / risk, 2) if risk > 0 else 0.0
        if rr >= min_rr and sl > price > tp:
            return {
                "pair": pair,
                "side": "SELL",
                "entry": round(price, decimals),
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "confirmations": sell_conf,
                "be_moved": False,
                "opened_at": last["datetime"],
            }

    return None


def monitor_active_signals(cfg: dict) -> None:
    active = list(cfg.get("active_signals", []))
    if not active:
        return

    by_pair: dict[str, list[dict]] = {}
    for sig in active:
        p = sig.get("pair")
        if p and p not in by_pair:
            by_pair[p] = fetch_candles(p, interval="15min", outputsize=25)
            time.sleep(9)

    remaining: list[dict] = []
    changed = False

    for sig in active:
        pair = sig.get("pair", "")
        candles = by_pair.get(pair, [])
        if not candles:
            remaining.append(sig)
            continue

        last = candles[-1]
        high = last["high"]
        low = last["low"]
        side = sig.get("side")
        entry = float(sig.get("entry", 0.0))
        sl = float(sig.get("sl", entry))
        tp = float(sig.get("tp", entry))
        rr = float(sig.get("rr", cfg.get("min_rr", 2.0)))

        if side == "BUY":
            half_way = entry + (tp - entry) * 0.5
            if not sig.get("be_moved") and high >= half_way:
                sig["be_moved"] = True
                sig["sl"] = entry
                sl = entry
                changed = True
                if TELEGRAM_CHAT_ID:
                    send_message(
                        TELEGRAM_CHAT_ID,
                        f"🛡 BE (Break-Even) o'tkazildi: {pair} BUY\n• Entry: {entry}\n• Yangi SL: {sl} (0.0R)",
                    )

            if high >= tp:
                record_closed_result(cfg, pair, "TP", rr)
                changed = True
                if TELEGRAM_CHAT_ID:
                    send_message(
                        TELEGRAM_CHAT_ID,
                        f"🎯 TP YOPILDI: {pair} BUY\n• Natija: +{rr:.2f}R\n• Umumiy R: {cfg['total_r']:+.2f}R",
                    )
                continue

            if low <= sl:
                outcome = "BE" if sig.get("be_moved") or abs(sl - entry) < 1e-9 else "SL"
                r_val = 0.0 if outcome == "BE" else -1.0
                record_closed_result(cfg, pair, outcome, r_val)
                changed = True
                if TELEGRAM_CHAT_ID:
                    send_message(
                        TELEGRAM_CHAT_ID,
                        f"🛑 {outcome} YOPILDI: {pair} BUY\n• Natija: {r_val:+.2f}R\n• Umumiy R: {cfg['total_r']:+.2f}R",
                    )
                continue

        elif side == "SELL":
            half_way = entry - (entry - tp) * 0.5
            if not sig.get("be_moved") and low <= half_way:
                sig["be_moved"] = True
                sig["sl"] = entry
                sl = entry
                changed = True
                if TELEGRAM_CHAT_ID:
                    send_message(
                        TELEGRAM_CHAT_ID,
                        f"🛡 BE (Break-Even) o'tkazildi: {pair} SELL\n• Entry: {entry}\n• Yangi SL: {sl} (0.0R)",
                    )

            if low <= tp:
                record_closed_result(cfg, pair, "TP", rr)
                changed = True
                if TELEGRAM_CHAT_ID:
                    send_message(
                        TELEGRAM_CHAT_ID,
                        f"🎯 TP YOPILDI: {pair} SELL\n• Natija: +{rr:.2f}R\n• Umumiy R: {cfg['total_r']:+.2f}R",
                    )
                continue

            if high >= sl:
                outcome = "BE" if sig.get("be_moved") or abs(sl - entry) < 1e-9 else "SL"
                r_val = 0.0 if outcome == "BE" else -1.0
                record_closed_result(cfg, pair, outcome, r_val)
                changed = True
                if TELEGRAM_CHAT_ID:
                    send_message(
                        TELEGRAM_CHAT_ID,
                        f"🛑 {outcome} YOPILDI: {pair} SELL\n• Natija: {r_val:+.2f}R\n• Umumiy R: {cfg['total_r']:+.2f}R",
                    )
                continue

        remaining.append(sig)

    if changed:
        cfg["active_signals"] = remaining
        save_config(cfg)


def scan_market_for_signals(cfg: dict) -> None:
    monitor_active_signals(cfg)

    active_pairs = {s.get("pair") for s in cfg.get("active_signals", [])}
    min_rr = float(cfg.get("min_rr", 2.0))
    min_conf = int(cfg.get("min_confirmations", 3))

    for pair in PAIRS:
        if pair in active_pairs:
            continue

        candles = fetch_candles(pair, interval="15min", outputsize=40)
        setup = evaluate_setup(pair, candles, min_rr, min_conf)
        if setup:
            cfg.setdefault("active_signals", []).append(setup)
            save_config(cfg)
            if TELEGRAM_CHAT_ID:
                send_message(
                    TELEGRAM_CHAT_ID,
                    f"🚨 YANGI SIGNAL: {setup['pair']} ({setup['side']})\n"
                    f"• Entry: {setup['entry']}\n"
                    f"• SL: {setup['sl']}\n"
                    f"• TP: {setup['tp']} ({setup['rr']}R)\n"
                    f"• Tasdiqlar: {setup['confirmations']} ta",
                )

        time.sleep(9)


def format_status(cfg: dict) -> str:
    mode_text = "Barcha sessiyalar (24/7)" if cfg.get("all_sessions", True) else "Faqat Killzone"
    return (
        f"📊 MTrade Signal Bot Holati\n"
        f"• Juftliklar: {', '.join(PAIRS)}\n"
        f"• Min RR: {cfg.get('min_rr', 2.0)}R\n"
        f"• Min tasdiq: {cfg.get('min_confirmations', 3)} ta\n"
        f"• Sessiya rejimi: {mode_text}\n"
        f"• Umumiy natija: {cfg.get('total_r', 0.0):+.2f}R\n"
        f"• Aktiv signallar: {len(cfg.get('active_signals', []))} ta"
    )


def format_results(cfg: dict) -> str:
    results = cfg.get("closed_results", [])
    total_r = float(cfg.get("total_r", 0.0))
    active_count = len(cfg.get("active_signals", []))

    wins = sum(1 for item in results if float(item.get("r", 0.0)) > 0)
    losses = sum(1 for item in results if float(item.get("r", 0.0)) < 0)
    be_count = sum(1 for item in results if float(item.get("r", 0.0)) == 0.0)

    lines = [
        "📈 NATIJALAR BO'LIMI",
        f"• Umumiy R hisobi: {total_r:+.2f}R",
        f"• Aktiv signallar: {active_count} ta",
        f"• Yopilganlar (oxirgi {len(results)} ta): +{wins} TP | {be_count} BE | -{losses} SL",
    ]

    if not results:
        lines.append("\nHozircha yopilgan natijalar qayd etilmagan.")
    else:
        lines.append("\nOxirgi natijalar:")
        for item in reversed(results[-8:]):
            pair = item.get("pair", "PAIR")
            outcome = item.get("outcome", "NATIJA")
            r_val = float(item.get("r", 0.0))
            lines.append(f"• {pair} — {outcome}: {r_val:+.2f}R")

    return "\n".join(lines)


def format_help() -> str:
    return (
        "ℹ️ YORDAM BO'LIMI\n\n"
        "Bot XAUUSD, EURUSD va GBPUSD juftliklarini TwelveData narxlari asosida kuzatadi va signallar hamda BE/TP/SL holatlari haqida xabar beradi.\n\n"
        "Foydalanuvchi bo'limlari:\n"
        "• /start — asosiy menyuni ochish\n"
        "• 📈 Natijalar (/results) — R hisobi va oxirgi natijalar\n"
        "• 📊 Holat (/status) — joriy filtrlar va sessiya rejimi\n"
        "• ℹ️ Yordam (/help) — qisqa yo'riqnoma\n\n"
        "Shuningdek, istalgan savol yoki xabar yozsangiz, bot javob qaytaradi."
    )


def format_admin_panel(cfg: dict) -> str:
    return (
        f"🛠 ADMIN PANEL\n\n"
        f"{format_status(cfg)}\n\n"
        f"Sozlamalar:\n"
        f"• /set_rr 2.0 — minimal RR ni o'zgartirish\n"
        f"• /set_conf 3 — minimal tasdiq sonini o'zgartirish\n"
        f"• /sessions all — barcha sessiyada ishlash\n"
        f"• /sessions killzone — faqat Killzone vaqtida ishlash\n"
        f"• /reset_r — umumiy R hisobini 0.0R qilish\n\n"
        f"Botdan kodni o'zgartirish:\n"
        f"• /get_code — joriy signal_bot.py faylini yuborish\n"
        f"• /set_code <kod> (yoki .py fayl) — kodni to'liq almashtirish\n"
        f"• /add_func <kod> — fayl oxiriga yangi funksiya qo'shish\n"
        f"• /replace\neski_matn\n---\nyangi_matn — kod ichidagi matnni almashtirish\n"
        f"• /rollback — zaxira nusxa (.bak) dan tiklash"
)
        def handle_incoming_updates(cfg: dict, last_update_id: int | None) -> int | None:
    params = {"timeout": 1, "allowed_updates": ["message"]}
    if last_update_id is not None:
        params["offset"] = last_update_id + 1

    query_string = urllib.parse.urlencode(params)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?{query_string}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return last_update_id

    if not data.get("ok"):
        return last_update_id

    for item in data.get("result", []):
        update_id = item.get("update_id")
        if isinstance(update_id, int):
            last_update_id = update_id

        msg = item.get("message") or {}
        text = (msg.get("text") or msg.get("caption") or "").strip()
        doc = msg.get("document") or {}
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()

        if not chat_id:
            continue

        lower = text.lower()

        if lower.startswith("/start"):
            send_message(
                chat_id,
                "Assalomu alaykum! MTrade signal botiga xush kelibsiz.\n\n"
                "Kerakli bo'limni pastdagi menyudan tanlang yoki xabar yozing:",
                reply_markup=USER_KEYBOARD,
            )
            continue

        if "natijalar" in lower or lower.startswith("/results"):
            send_message(chat_id, format_results(cfg), reply_markup=USER_KEYBOARD)
            continue

        if "holat" in lower or lower.startswith("/status"):
            send_message(chat_id, format_status(cfg), reply_markup=USER_KEYBOARD)
            continue

        if "yordam" in lower or lower.startswith("/help"):
            send_message(chat_id, format_help(), reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/admin"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            send_message(chat_id, format_admin_panel(cfg), reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/get_code"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            send_document(chat_id, os.path.abspath(__file__), caption="Joriy signal_bot.py kodi")
            continue

        if lower.startswith("/set_rr"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "Namuna: /set_rr 2.0", reply_markup=USER_KEYBOARD)
                continue
            try:
                val = float(parts[1].replace(",", "."))
                if val <= 0:
                    raise ValueError()
                cfg["min_rr"] = round(val, 2)
                save_config(cfg)
                send_message(chat_id, f"✅ Minimal RR {cfg['min_rr']}R qilib yangilandi.", reply_markup=USER_KEYBOARD)
            except ValueError:
                send_message(chat_id, "❌ Noto'g'ri son. Masalan: /set_rr 2.0", reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/set_conf"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "Namuna: /set_conf 3", reply_markup=USER_KEYBOARD)
                continue
            try:
                conf = int(parts[1].strip())
                if conf < 1 or conf > 10:
                    raise ValueError()
                cfg["min_confirmations"] = conf
                save_config(cfg)
                send_message(chat_id, f"✅ Minimal tasdiqlar soni {conf} ta qilib yangilandi.", reply_markup=USER_KEYBOARD)
            except ValueError:
                send_message(chat_id, "❌ 1 dan 10 gacha butun son kiriting. Masalan: /set_conf 3", reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/sessions"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            parts = text.split(maxsplit=1)
            mode = parts[1].strip().lower() if len(parts) > 1 else ""
            if mode == "all":
                cfg["all_sessions"] = True
                save_config(cfg)
                send_message(chat_id, "✅ Sessiya rejimi: Barcha sessiyalar (24/7).", reply_markup=USER_KEYBOARD)
            elif mode == "killzone":
                cfg["all_sessions"] = False
                save_config(cfg)
                send_message(chat_id, "✅ Sessiya rejimi: Faqat Killzone vaqtlari.", reply_markup=USER_KEYBOARD)
            else:
                send_message(chat_id, "Namuna: /sessions all yoki /sessions killzone", reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/reset_r"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            cfg["total_r"] = 0.0
            cfg["closed_results"] = []
            save_config(cfg)
            send_message(chat_id, "✅ Umumiy R hisobi va natijalar tarixi tozalandi.", reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/set_code") or lower.startswith("/update_code") or (doc.get("file_id") and not lower.startswith("/add_func") and not lower.startswith("/replace")):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue

            code_from_doc = download_telegram_file(doc.get("file_id", "")) if doc.get("file_id") else None
            if code_from_doc is not None:
                apply_code_safely(code_from_doc, cfg, chat_id)
                continue

            cmd_len = len("/update_code") if lower.startswith("/update_code") else len("/set_code")
            new_code = text[cmd_len:].lstrip("\n ")
            apply_code_safely(new_code, cfg, chat_id)
            continue

        if lower.startswith("/add_func"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            extra_raw = text[len("/add_func"):].lstrip("\n ")
            extra_code = strip_markdown_codeblock(extra_raw)
            if not extra_code.strip():
                send_message(
                    chat_id,
                    "Namuna:\n/add_func\ndef yangi_funksiya():\n    return 'ok'",
                    reply_markup=USER_KEYBOARD,
                )
                continue

            target_path = os.path.abspath(__file__)
            try:
                with open(target_path, "r", encoding="utf-8") as src:
                    current_code = src.read()
                with open(BACKUP_FILE, "w", encoding="utf-8") as bak:
                    bak.write(current_code)

                candidate_code = current_code.rstrip() + "\n\n\n" + extra_code + "\n"
                compile(candidate_code, __file__, "exec")

                with open(target_path, "w", encoding="utf-8") as dst:
                    dst.write(candidate_code)

                save_config(cfg)
                send_message(chat_id, "✅ Yangi funksiya qo'shildi. Bot qayta ishga tushmoqda...", reply_markup=USER_KEYBOARD)
                time.sleep(1)
                os._exit(0)
            except SyntaxError as exc:
                send_message(chat_id, f"❌ Funksiya sintaksis xatosi ({exc.lineno}-qator): {exc.msg}", reply_markup=USER_KEYBOARD)
            except Exception as exc:
                send_message(chat_id, f"❌ Funksiya qo'shishda xato: {exc}", reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/replace"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            payload = text[len("/replace"):].lstrip("\r\n")
            if "\n---\n" not in payload:
                send_message(
                    chat_id,
                    "Namuna:\n/replace\neski_matn\n---\nyangi_matn",
                    reply_markup=USER_KEYBOARD,
                )
                continue
            old_part, new_part = payload.split("\n---\n", 1)
            target_path = os.path.abspath(__file__)
            try:
                with open(target_path, "r", encoding="utf-8") as src:
                    current_code = src.read()
                if old_part not in current_code:
                    send_message(chat_id, "❌ Ko'rsatilgan 'eski_matn' kod ichidan topilmadi.", reply_markup=USER_KEYBOARD)
                    continue
                updated_code = current_code.replace(old_part, new_part, 1)
                apply_code_safely(updated_code, cfg, chat_id)
            except Exception as exc:
                send_message(chat_id, f"❌ Almashtirishda xato: {exc}", reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/rollback"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            if not os.path.exists(BACKUP_FILE):
                send_message(chat_id, "❌ Zaxira nusxa (.bak) topilmadi.", reply_markup=USER_KEYBOARD)
                continue
            try:
                with open(BACKUP_FILE, "r", encoding="utf-8") as bak:
                    bak_code = bak.read()
                with open(os.path.abspath(__file__), "w", encoding="utf-8") as dst:
                    dst.write(bak_code)
                save_config(cfg)
                send_message(chat_id, "✅ Zaxira nusxa tiklandi. Bot qayta ishga tushmoqda...", reply_markup=USER_KEYBOARD)
                time.sleep(1)
                os._exit(0)
            except Exception as exc:
                send_message(chat_id, f"❌ Tiklashda xato: {exc}", reply_markup=USER_KEYBOARD)
            continue

        if text:
            send_message(
                chat_id,
                f"Xabaringiz qabul qilindi: \"{text}\"\n"
                f"Natijalarni ko'rish uchun '📈 Natijalar' (/results) yoki holat uchun '📊 Holat' (/status) ni bosing.",
                reply_markup=USER_KEYBOARD,
            )

    return last_update_id


def main() -> None:
    cfg = load_config()
    save_config(cfg)

    logging.info("MTrade bot ishga tushdi. Config: %s", cfg)
    if TELEGRAM_CHAT_ID:
        send_message(
            TELEGRAM_CHAT_ID,
            f"📡 EXNESS SIGNAL BOT ISHGA TUSHDI\n"
            f"Juftliklar: {', '.join(PAIRS)}\n"
            f"Talab: Kamida {cfg['min_confirmations']} ta tasdiq | Min RR: {cfg['min_rr']}R\n"
            f"Rejim: {'Barcha sessiyalar (24/7)' if cfg['all_sessions'] else 'Killzone'}\n"
            f"Admin panel: /admin",
            reply_markup=USER_KEYBOARD,
        )

    last_update_id: int | None = None
    last_scan_time = 0.0

    while True:
        last_update_id = handle_incoming_updates(cfg, last_update_id)

        now = time.time()
        interval = int(cfg.get("check_interval_sec", 300))
        if now - last_scan_time >= interval:
            last_scan_time = now
            logging.info("TwelveData orqali bozor tekshirilmoqda...")
            scan_market_for_signals(cfg)

        time.sleep(2)


if __name__ == "__main__":
    main()
    
