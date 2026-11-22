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
        "Bot XAUUSD, EURUSD va GBPUSD juftliklarini kuzatadi va signallar hamda BE/TP/SL holatlari haqida xabar beradi.\n\n"
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
        f"Buyruqlar:\n"
        f"• /set_rr 2.0 — minimal RR ni o'zgartirish\n"
        f"• /set_conf 3 — minimal tasdiq sonini o'zgartirish\n"
        f"• /sessions all — barcha sessiyada ishlash\n"
        f"• /sessions killzone — faqat Killzone vaqtida ishlash\n"
        f"• /reset_r — umumiy R hisobini 0.0R qilish\n"
        f"• /update_code <yangi_kod> — signal_bot.py ni yangilash va qayta ishga tushirish\n"
        f"• /add_func <funksiya_kodi> — fayl oxiriga yangi funksiya qo'shish"
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
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()

        if not chat_id or not text:
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

        if lower.startswith("/update_code"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            new_code = text[len("/update_code"):].lstrip("\n ")
            if not new_code.strip():
                send_message(chat_id, "Namuna:\n/update_code\n<yangi python kod>", reply_markup=USER_KEYBOARD)
                continue
            try:
                with open(__file__, "w", encoding="utf-8") as f:
                    f.write(new_code)
                save_config(cfg)
                send_message(chat_id, "✅ Kod yangilandi. Bot qayta ishga tushmoqda...", reply_markup=USER_KEYBOARD)
                os._exit(0)
            except Exception as exc:
                send_message(chat_id, f"❌ Kodni yozishda xato: {exc}", reply_markup=USER_KEYBOARD)
            continue

        if lower.startswith("/add_func"):
            if not is_admin(chat_id):
                send_message(chat_id, "⛔ Bu buyruq faqat admin uchun.", reply_markup=USER_KEYBOARD)
                continue
            extra_code = text[len("/add_func"):].lstrip("\n ")
            if not extra_code.strip():
                send_message(chat_id, "Namuna:\n/add_func\ndef yangi_funksiya():\n    return 'ok'", reply_markup=USER_KEYBOARD)
                continue
            try:
                with open(__file__, "a", encoding="utf-8") as f:
                    f.write("\n\n" + extra_code + "\n")
                save_config(cfg)
                send_message(chat_id, "✅ Yangi funksiya qo'shildi. Bot qayta ishga tushmoqda...", reply_markup=USER_KEYBOARD)
                os._exit(0)
            except Exception as exc:
                send_message(chat_id, f"❌ Funksiya qo'shishda xato: {exc}", reply_markup=USER_KEYBOARD)
            continue

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
            logging.info(
                "Bozor tekshirilmoqda | Min RR: %s | Min tasdiq: %s | All sessions: %s",
                cfg.get("min_rr"),
                cfg.get("min_confirmations"),
                cfg.get("all_sessions"),
            )

        time.sleep(2)


if __name__ == "__main__":
    main()
