import json, logging, os, time, urllib.parse, urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TOKEN = "".join(os.getenv("TELEGRAM_BOT_TOKEN", "").split())
ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TD_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

CFG_FILE = os.getenv("CFG_FILE", "bot_config.json")
BAK_FILE = os.path.abspath(__file__) + ".bak"
PAIRS = ["XAUUSD", "EURUSD", "GBPUSD"]
API_SLEEP = 9  # TwelveData bepul tarif: ~8 so'rov/daqiqa

DEFAULT_CFG = {
    "min_rr": 2.0,
    "min_confirmations": 3,
    "all_sessions": True,
    "check_interval_sec": 300,
    "total_r": 0.0,
    "active_signals": [],
    "closed_results": [],
}

KB = {
    "keyboard": [[{"text": "📈 Natijalar"}, {"text": "📊 Holat"}], [{"text": "ℹ️ Yordam"}]],
    "resize_keyboard": True,
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    cfg["active_signals"] = []
    cfg["closed_results"] = []
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            logging.warning("Config read err: %s", e)
    return cfg


def save_cfg(cfg):
    try:
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("Config save err: %s", e)


def is_admin(cid):
    return bool(ADMIN_ID) and str(cid).strip() == str(ADMIN_ID).strip()


def get_json(url):
    url = str(url).strip()
    if not url.startswith(("https://", "http://")):
        logging.warning("Invalid URL")
        return {}
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        # token log'ga tushmasligi uchun URL yozilmaydi
        logging.warning("HTTP err: %s", type(e).__name__)
        return {}


def tg_post(method, payload):
    if not TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        logging.warning("TG err (%s): %s", method, type(e).__name__)
        return {}


def send_msg(cid, text, kb=None):
    if not cid:
        return
    p = {"chat_id": str(cid), "text": text}
    if kb:
        p["reply_markup"] = kb
    tg_post("sendMessage", p)


def send_code_file(cid):
    path = os.path.abspath(__file__)
    if not TOKEN or not os.path.exists(path):
        return
    b = "----Boundary7MA4YWxkTrZu0gW"
    with open(path, "rb") as f:
        data = f.read()
    body = (
        f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{cid}\r\n"
        f"--{b}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"signal_bot.py\"\r\n"
        f"Content-Type: text/x-python\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{b}--\r\n".encode("utf-8")
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={b}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=25).read()
    except Exception as e:
        logging.warning("Send doc err: %s", type(e).__name__)


def download_doc(file_id):
    info = get_json(f"https://api.telegram.org/bot{TOKEN}/getFile?file_id={urllib.parse.quote(file_id)}")
    fp = (info.get("result") or {}).get("file_path")
    if not fp:
        return None
    url = f"https://api.telegram.org/file/bot{TOKEN}/{fp}"
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return r.read().decode("utf-8")
    except Exception:
        return None


def apply_code(code, cfg, cid):
    c = code.strip()
    if c.startswith("```python"):
        c = c[9:].lstrip("\r\n")
    elif c.startswith("```"):
        c = c[3:].lstrip("\r\n")
    if c.endswith("```"):
        c = c[:-3].rstrip("\r\n")
    if not c.strip():
        send_msg(cid, "❌ Kod bo'sh.", KB)
        return
    try:
        compile(c, __file__, "exec")
    except SyntaxError as e:
        send_msg(cid, f"❌ Sintaksis xatosi ({e.lineno}-qator): {e.msg}", KB)
        return
    if "def main" not in c or "__main__" not in c:
        send_msg(cid, "❌ Kodda main() yoki __main__ bloki yo'q. Rad etildi.", KB)
        return
    path = os.path.abspath(__file__)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                old = f.read()
            with open(BAK_FILE, "w", encoding="utf-8") as f:
                f.write(old)
        with open(path, "w", encoding="utf-8") as f:
            f.write(c)
        save_cfg(cfg)
        send_msg(cid, "✅ Kod yangilandi. Qayta ishga tushmoqda...", KB)
        time.sleep(1)
        os._exit(0)
    except Exception as e:
        send_msg(cid, f"❌ Saqlash xatosi: {e}", KB)


def add_result(cfg, pair, outcome, r_val):
    cfg.setdefault("closed_results", []).append({"pair": pair, "outcome": outcome, "r": round(float(r_val), 2)})
    cfg["closed_results"] = cfg["closed_results"][-15:]
    cfg["total_r"] = round(float(cfg.get("total_r", 0.0)) + float(r_val), 2)
    save_cfg(cfg)


def fetch_candles(pair, outputsize=60):
    if not TD_KEY:
        return []
    sym = f"{pair[:3]}/{pair[3:]}" if len(pair) == 6 else pair
    q = urllib.parse.urlencode({"symbol": sym, "interval": "15min", "outputsize": outputsize, "apikey": TD_KEY})
    data = get_json("https://api.twelvedata.com/time_series?" + q)
    if data.get("status") == "error":
        logging.warning("TwelveData (%s): %s", pair, data.get("message"))
        return []
    vals = data.get("values") or []
    out = []
    for row in reversed(vals):  # eski -> yangi
        try:
            out.append({
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "dt": str(row.get("datetime", "")),
            })
        except Exception:
            continue
    return out


def ema(closes, n):
    # oddiy MA bilan boshlanadigan EMA (boshlang'ich qiymat aniqroq)
    if not closes:
        return 0.0
    k = 2.0 / (n + 1)
    seed = closes[:n] if len(closes) >= n else closes
    v = sum(seed) / len(seed)
    for p in closes[len(seed):]:
        v = p * k + v * (1 - k)
    return v


def atr(candles, n=14):
    trs = [
        max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"] - candles[i - 1]["close"]),
        )
        for i in range(1, len(candles))
    ]
    r = trs[-n:] if len(trs) >= n else trs
    return sum(r) / len(r) if r else 0.0


def in_killzone():
    h = time.gmtime().tm_hour  # UTC: London 07-10, New York 12-16
    return 7 <= h < 10 or 12 <= h < 16


def find_setup(pair, candles, min_rr, min_conf):
    if len(candles) < 30:
        return None
    closes = [c["close"] for c in candles]
    last, prev = candles[-1], candles[-2]
    price = last["close"]
    ef, es, a = ema(closes, 8), ema(closes, 21), atr(candles, 14)
    if a <= 0 or price <= 0:
        return None
    slw = min(c["low"] for c in candles[-8:-1])
    shg = max(c["high"] for c in candles[-8:-1])

    b_conf = sum([
        ef > es,
        price > ef,
        last["close"] > last["open"],
        last["low"] <= slw * 1.0005 and last["close"] > slw,
        last["close"] > prev["high"],
    ])
    s_conf = sum([
        ef < es,
        price < ef,
        last["close"] < last["open"],
        last["high"] >= shg * 0.9995 and last["close"] < shg,
        last["close"] < prev["low"],
    ])
    dec = 2 if "XAU" in pair else 5

    if b_conf >= s_conf and b_conf >= min_conf:
        dist = max(a * 1.1, abs(price - slw))
        sl = round(price - dist, dec)
        risk = price - sl
        tp = round(price + risk * min_rr, dec)
        rr = round((tp - price) / risk, 2) if risk > 0 else 0
        if rr >= min_rr and tp > price > sl:
            return {"pair": pair, "side": "BUY", "entry": round(price, dec), "sl": sl, "tp": tp,
                    "rr": rr, "confirmations": b_conf, "be_moved": False, "opened": last["dt"]}

    if s_conf > b_conf and s_conf >= min_conf:
        dist = max(a * 1.1, abs(shg - price))
        sl = round(price + dist, dec)
        risk = sl - price
        tp = round(price - risk * min_rr, dec)
        rr = round((price - tp) / risk, 2) if risk > 0 else 0
        if rr >= min_rr and sl > price > tp:
            return {"pair": pair, "side": "SELL", "entry": round(price, dec), "sl": sl, "tp": tp,
                    "rr": rr, "confirmations": s_conf, "be_moved": False, "opened": last["dt"]}
    return None


def check_signal(sig, candles, default_rr):
    """Signal ochilgandan keyingi BARCHA shamlarni tekshiradi.
    Qaytaradi: (outcome, r) yoki None. Bir shamda SL va TP ikkalasi tegsa, konservativ: SL."""
    side = sig["side"]
    ent, tp = float(sig["entry"]), float(sig["tp"])
    rr = float(sig.get("rr", default_rr))
    opened = sig.get("opened", "")
    sig.setdefault("sl0", sig["sl"])
    last_idx = len(candles) - 1
    for idx, c in enumerate(candles):
        if c["dt"] <= opened:
            continue
        # BE o'rnatilgan shamning o'zida eski SL ishlatiladi (qayta tekshiruvda soxta BE bo'lmasligi uchun)
        sl = float(sig["sl0"]) if c["dt"] == sig.get("be_dt") else float(sig["sl"])
        hi, lo = c["high"], c["low"]
        if side == "BUY":
            sl_hit, tp_hit = lo <= sl, hi >= tp
            half = hi >= ent + (tp - ent) * 0.5
        else:
            sl_hit, tp_hit = hi >= sl, lo <= tp
            half = lo <= ent - (ent - tp) * 0.5
        if sl_hit:
            return ("BE", 0.0) if sig.get("be_moved") else ("SL", -1.0)
        if tp_hit:
            return "TP", rr
        if half and not sig.get("be_moved"):
            sig["be_moved"], sig["sl"], sig["be_dt"] = True, ent, c["dt"]
            send_msg(ADMIN_ID, f"🛡 BE o'tkazildi: {sig['pair']} {side} | SL: {ent} (0.0R)")
        if idx < last_idx:  # oxirgi sham hali yopilmagan bo'lishi mumkin, uni keyingi skanda qayta tekshiramiz
            sig["opened"] = c["dt"]
    return None


def scan_market(cfg):
    rem, changed = [], False
    for sig in list(cfg.get("active_signals", [])):
        pair = sig.get("pair", "")
        cs = fetch_candles(pair, 60)
        time.sleep(API_SLEEP)
        if not cs:
            rem.append(sig)
            continue
        before = (sig.get("be_moved"), sig.get("sl"), sig.get("opened"))
        res = check_signal(sig, cs, cfg.get("min_rr", 2.0))
        if res:
            outcome, r = res
            add_result(cfg, pair, outcome, r)
            changed = True
            icon = "🎯" if outcome == "TP" else "🛑"
            send_msg(ADMIN_ID, f"{icon} {outcome} YOPILDI: {pair} {sig['side']} ({r:+.2f}R) | Jami: {cfg['total_r']:+.2f}R")
        else:
            rem.append(sig)
            if before != (sig.get("be_moved"), sig.get("sl"), sig.get("opened")):
                changed = True

    if changed:
        cfg["active_signals"] = rem
        save_cfg(cfg)

    if not cfg.get("all_sessions", True) and not in_killzone():
        return

    busy = {s.get("pair") for s in cfg.get("active_signals", [])}
    for pair in PAIRS:
        if pair in busy:
            continue
        setup = find_setup(pair, fetch_candles(pair, 60), float(cfg.get("min_rr", 2.0)),
                           int(cfg.get("min_confirmations", 3)))
        if setup:
            cfg.setdefault("active_signals", []).append(setup)
            save_cfg(cfg)
            send_msg(
                ADMIN_ID,
                f"🚨 YANGI SIGNAL: {setup['pair']} ({setup['side']})\nEntry: {setup['entry']} | SL: {setup['sl']} | TP: {setup['tp']} ({setup['rr']}R) | Tasdiq: {setup['confirmations']}",
            )
        time.sleep(API_SLEEP)


def status_text(cfg):
    m = "Barcha sessiyalar (24/7)" if cfg.get("all_sessions", True) else "Killzone (07-10, 12-16 UTC)"
    return (
        f"📊 Holat\n• Juftliklar: {', '.join(PAIRS)}\n"
        f"• Min RR: {cfg.get('min_rr')}R | Tasdiq: {cfg.get('min_confirmations')} ta\n"
        f"• Rejim: {m}\n"
        f"• Jami: {cfg.get('total_r', 0.0):+.2f}R | Aktiv: {len(cfg.get('active_signals', []))} ta"
    )


def signals_text(cfg):
    act = cfg.get("active_signals", [])
    if not act:
        return "📭 Aktiv signal yo'q."
    lines = [f"📌 AKTIV SIGNALLAR ({len(act)} ta)"]
    for x in act:
        be = "BE ✅" if x.get("be_moved") else "BE ❌"
        lines.append(
            f"• {x.get('pair')} {x.get('side')} | Entry: {x.get('entry')} | SL: {x.get('sl')} | TP: {x.get('tp')} ({x.get('rr')}R) | {be}"
        )
    return "\n".join(lines)


def results_text(cfg):
    res = cfg.get("closed_results", [])
    w = sum(1 for x in res if float(x.get("r", 0)) > 0)
    l = sum(1 for x in res if float(x.get("r", 0)) < 0)
    b = sum(1 for x in res if float(x.get("r", 0)) == 0)
    lines = [
        f"📈 NATIJALAR\n• Umumiy R: {cfg.get('total_r', 0.0):+.2f}R\n"
        f"• Aktiv: {len(cfg.get('active_signals', []))} ta\n"
        f"• Oxirgi {len(res)} ta: +{w} TP | {b} BE | -{l} SL"
    ]
    for x in reversed(res[-8:]):
        lines.append(f"• {x['pair']} — {x['outcome']}: {float(x['r']):+.2f}R")
    return "\n".join(lines)


def read_self():
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as f:
        return f.read()


def handle_updates(cfg, last_id):
    q = {"timeout": 1, "allowed_updates": json.dumps(["message"])}
    if last_id is not None:
        q["offset"] = last_id + 1
    data = get_json(f"https://api.telegram.org/bot{TOKEN}/getUpdates?" + urllib.parse.urlencode(q))
    if not data.get("ok"):
        return last_id
    for item in data.get("result", []):
        if isinstance(item.get("update_id"), int):
            last_id = item["update_id"]
        msg = item.get("message") or {}
        text = (msg.get("text") or msg.get("caption") or "").strip()
        doc = msg.get("document") or {}
        cid = str((msg.get("chat") or {}).get("id", "")).strip()
        if not cid:
            continue
        low = text.lower()
        admin = is_admin(cid)
        is_py_doc = bool(doc.get("file_id")) and str(doc.get("file_name", "")).lower().endswith(".py")

        if low.startswith("/start"):
            send_msg(cid, "Assalomu alaykum! MTrade botiga xush kelibsiz.\nMenyudan bo'lim tanlang:", KB)
        elif low.startswith("/results") or (not low.startswith("/") and "natijalar" in low):
            send_msg(cid, results_text(cfg), KB)
        elif low.startswith("/status") or (not low.startswith("/") and "holat" in low):
            send_msg(cid, status_text(cfg), KB)
        elif low.startswith("/help") or (not low.startswith("/") and "yordam" in low):
            send_msg(cid, "ℹ️ Yordam\n• 📈 Natijalar (/results)\n• 📊 Holat (/status)", KB)
        elif low.startswith("/admin"):
            if not admin:
                send_msg(cid, "⛔ Faqat admin uchun.", KB)
            else:
                send_msg(
                    cid,
                    f"🛠 ADMIN PANEL\n\n{status_text(cfg)}\n\nBuyruqlar:\n/set_rr 2.0\n/set_conf 3\n/sessions all | killzone\n/signals\n/clear_signals\n/reset_r\n/get_code\n/set_code <kod> (yoki .py fayl)\n/add_func <kod>\n/replace\neski\n---\nyangi\n/rollback",
                    KB,
                )
        elif low.startswith("/get_code") and admin:
            send_code_file(cid)
        elif low.startswith("/set_rr") and admin:
            try:
                cfg["min_rr"] = round(float(text.split(maxsplit=1)[1].replace(",", ".")), 2)
                save_cfg(cfg)
                send_msg(cid, f"✅ Min RR: {cfg['min_rr']}R", KB)
            except Exception:
                send_msg(cid, "Namuna: /set_rr 2.0", KB)
        elif low.startswith("/set_conf") and admin:
            try:
                cfg["min_confirmations"] = int(text.split(maxsplit=1)[1])
                save_cfg(cfg)
                send_msg(cid, f"✅ Min tasdiq: {cfg['min_confirmations']}", KB)
            except Exception:
                send_msg(cid, "Namuna: /set_conf 3", KB)
        elif low.startswith("/sessions") and admin:
            m = text.split(maxsplit=1)[1].strip().lower() if len(text.split()) > 1 else ""
            if m in ("all", "killzone"):
                cfg["all_sessions"] = (m == "all")
                save_cfg(cfg)
                send_msg(cid, f"✅ Rejim: {m}", KB)
            else:
                send_msg(cid, "Namuna: /sessions all yoki /sessions killzone", KB)
        elif low.startswith("/signals") and admin:
            send_msg(cid, signals_text(cfg), KB)
        elif low.startswith("/clear_signals") and admin:
            n = len(cfg.get("active_signals", []))
            cfg["active_signals"] = []
            save_cfg(cfg)
            send_msg(cid, f"✅ {n} ta aktiv signal tozalandi.", KB)
        elif low.startswith("/reset_r") and admin:
            cfg["total_r"], cfg["closed_results"] = 0.0, []
            save_cfg(cfg)
            send_msg(cid, "✅ R va natijalar tozalandi.", KB)
        elif (low.startswith("/set_code") or low.startswith("/update_code") or is_py_doc) and admin:
            if is_py_doc:
                code = download_doc(doc["file_id"])
            else:
                cut = len("/update_code") if low.startswith("/update_code") else len("/set_code")
                code = text[cut:]
            apply_code(code or "", cfg, cid)
        elif low.startswith("/add_func") and admin:
            extra = text[len("/add_func"):].strip()
            apply_code(read_self().rstrip() + "\n\n\n" + extra + "\n", cfg, cid)
        elif low.startswith("/replace") and admin:
            body = text[len("/replace"):].lstrip("\r\n")
            if "\n---\n" not in body:
                send_msg(cid, "Namuna:\n/replace\neski\n---\nyangi", KB)
            else:
                o, n = body.split("\n---\n", 1)
                cur = read_self()
                if o not in cur:
                    send_msg(cid, "❌ Eski matn topilmadi.", KB)
                else:
                    apply_code(cur.replace(o, n, 1), cfg, cid)
        elif low.startswith("/rollback") and admin:
            if os.path.exists(BAK_FILE):
                with open(BAK_FILE, "r", encoding="utf-8") as f:
                    apply_code(f.read(), cfg, cid)
            else:
                send_msg(cid, "❌ Zaxira fayl topilmadi.", KB)
        elif text and admin:
            send_msg(cid, "Buyruq tushunilmadi. /admin yoki /help", KB)
    return last_id


def main():
    cfg = load_cfg()
    save_cfg(cfg)
    logging.info("CFG check | TOKEN_len=%d | ADMIN_set=%s | TD_len=%d", len(TOKEN), bool(ADMIN_ID), len(TD_KEY))
    send_msg(
        ADMIN_ID,
        f"📡 EXNESS SIGNAL BOT ISHGA TUSHDI\nJuftliklar: {', '.join(PAIRS)} | Min RR: {cfg['min_rr']}R | Tasdiq: {cfg['min_confirmations']}\nAdmin: /admin",
        KB,
    )
    last_id, last_scan = None, 0.0
    while True:
        try:
            last_id = handle_updates(cfg, last_id)
            if time.time() - last_scan >= int(cfg.get("check_interval_sec", 300)):
                last_scan = time.time()
                scan_market(cfg)
        except Exception as e:
            logging.warning("Loop err: %s", e)
        time.sleep(2)


if __name__ == "__main__":
    main()
