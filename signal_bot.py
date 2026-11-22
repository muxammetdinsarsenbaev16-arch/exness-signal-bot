import json, logging, os, time, urllib.parse, urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TOKEN = "".join(os.getenv("TELEGRAM_BOT_TOKEN", "9049560133:AAGeA_Lb1OGf7D6PRzokS4qY5smUQyPQvsc").split())
ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TD_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

CFG_FILE = "bot_config.json"
BAK_FILE = os.path.abspath(__file__) + ".bak"
PAIRS = ["XAUUSD", "EURUSD", "GBPUSD"]

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


def _sanitize_url(url):
    u = str(url).strip()
    i = u.find("https://")
    if i == -1:
        i = u.find("http://")
    if i != -1:
        u = u[i:]
    u = u.split("]")[0].split(")")[0].strip()
    return u


def _label_url(u):
    base = u.split("?")[0]
    if "/bot" in base:
        return base.split("/bot")[0] + "/bot<TOKEN>" + base.split("/bot", 1)[1].split("/", 1)[-1]
    return base


def get_json(url):
    clean = _sanitize_url(url)
    if not clean.startswith(("https://", "http://")):
        logging.warning("Invalid URL: %r", clean)
        return {}
    req = urllib.request.Request(clean, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        logging.warning("HTTP err (%s): %s", _label_url(clean), e)
        return {}


def tg_post(method, payload):
    if not TOKEN:
        return {}
    clean = _sanitize_url(f"https://api.telegram.org/bot{TOKEN}/{method}")
    req = urllib.request.Request(
        clean,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        logging.warning("TG err (%s): %s", method, e)
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
    clean = _sanitize_url(f"https://api.telegram.org/bot{TOKEN}/sendDocument")
    req = urllib.request.Request(
        clean,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={b}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=25).read()
    except Exception as e:
        logging.warning("Send doc err: %s", e)


def download_doc(file_id):
    info = get_json(f"https://api.telegram.org/bot{TOKEN}/getFile?file_id={urllib.parse.quote(file_id)}")
    fp = (info.get("result") or {}).get("file_path")
    if not fp:
        return None
    clean = _sanitize_url(f"https://api.telegram.org/file/bot{TOKEN}/{fp}")
    try:
        with urllib.request.urlopen(clean, timeout=25) as r:
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


def fetch_candles(pair, outputsize=40):
    if not TD_KEY:
        return []
    sym = f"{pair[:3]}/{pair[3:]}" if len(pair) == 6 else pair
    q = urllib.parse.urlencode({"symbol": sym, "interval": "15min", "outputsize": outputsize, "apikey": TD_KEY})
    url = "[https://api.twelvedata.com/time_series](https://api.twelvedata.com/time_series)?" + q
    data = get_json(url)
    if data.get("status") == "error":
        logging.warning("TwelveData (%s): %s", pair, data.get("message"))
        return []
    vals = data.get("values") or []
    out = []
    for row in reversed(vals):
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
    k = 2.0 / (n + 1)
    v = closes[0]
    for p in closes[1:]:
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


def find_setup(pair, candles, min_rr, min_conf):
    if len(candles) < 22:
        return None
    closes = [c["close"] for c in candles]
    last, prev = candles[-1], candles[-2]
    price = last["close"]
    ef, es, a = ema(closes[-20:], 8), ema(closes[-25:], 21), atr(candles, 14)
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
            return {
                "pair": pair,
                "side": "BUY",
                "entry": round(price, dec),
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "confirmations": b_conf,
                "be_moved": False,
            }

    if s_conf > b_conf and s_conf >= min_conf:
        dist = max(a * 1.1, abs(shg - price))
        sl = round(price + dist, dec)
        risk = sl - price
        tp = round(price - risk * min_rr, dec)
        rr = round((price - tp) / risk, 2) if risk > 0 else 0
        if rr >= min_rr and sl > price > tp:
            return {
                "pair": pair,
                "side": "SELL",
                "entry": round(price, dec),
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "confirmations": s_conf,
                "be_moved": False,
            }
    return None


def scan_market(cfg):
    active = list(cfg.get("active_signals", []))
    rem, changed = [], False
    for sig in active:
        pair = sig.get("pair", "")
        cs = fetch_candles(pair, 25)
        time.sleep(9)
        if not cs:
            rem.append(sig)
            continue
        hi, lo = cs[-1]["high"], cs[-1]["low"]
        side, ent, sl, tp = sig["side"], float(sig["entry"]), float(sig["sl"]), float(sig["tp"])
        rr = float(sig.get("rr", cfg.get("min_rr", 2.0)))

        if side == "BUY":
            if not sig.get("be_moved") and hi >= ent + (tp - ent) * 0.5:
                sig["be_moved"], sig["sl"], sl, changed = True, ent, ent, True
                send_msg(ADMIN_ID, f"🛡 BE o'tkazildi: {pair} BUY | SL: {sl} (0.0R)")
            if hi >= tp:
                add_result(cfg, pair, "TP", rr)
                changed = True
                send_msg(ADMIN_ID, f"🎯 TP YOPILDI: {pair} BUY (+{rr:.2f}R) | Jami: {cfg['total_r']:+.2f}R")
                continue
            if lo <= sl:
                out = "BE" if sig.get("be_moved") or abs(sl - ent) < 1e-9 else "SL"
                rv = 0.0 if out == "BE" else -1.0
                add_result(cfg, pair, out, rv)
                changed = True
                send_msg(ADMIN_ID, f"🛑 {out} YOPILDI: {pair} BUY ({rv:+.2f}R) | Jami: {cfg['total_r']:+.2f}R")
                continue
        else:
            if not sig.get("be_moved") and lo <= ent - (ent - tp) * 0.5:
                sig["be_moved"], sig["sl"], sl, changed = True, ent, ent, True
                send_msg(ADMIN_ID, f"🛡 BE o'tkazildi: {pair} SELL | SL: {sl} (0.0R)")
            if lo <= tp:
                add_result(cfg, pair, "TP", rr)
                changed = True
                send_msg(ADMIN_ID, f"🎯 TP YOPILDI: {pair} SELL (+{rr:.2f}R) | Jami: {cfg['total_r']:+.2f}R")
                continue
            if hi >= sl:
                out = "BE" if sig.get("be_moved") or abs(sl - ent) < 1e-9 else "SL"
                rv = 0.0 if out == "BE" else -1.0
                add_result(cfg, pair, out, rv)
                changed = True
                send_msg(ADMIN_ID, f"🛑 {out} YOPILDI: {pair} SELL ({rv:+.2f}R) | Jami: {cfg['total_r']:+.2f}R")
                continue
        rem.append(sig)

    if changed:
        cfg["active_signals"] = rem
        save_cfg(cfg)

    busy = {s.get("pair") for s in cfg.get("active_signals", [])}
    for pair in PAIRS:
        if pair in busy:
            continue
        setup = find_setup(pair, fetch_candles(pair, 40), float(cfg.get("min_rr", 2.0)), int(cfg.get("min_confirmations", 3)))
        if setup:
            cfg.setdefault("active_signals", []).append(setup)
            save_cfg(cfg)
            send_msg(
                ADMIN_ID,
                f"🚨 YANGI SIGNAL: {setup['pair']} ({setup['side']})\nEntry: {setup['entry']} | SL: {setup['sl']} | TP: {setup['tp']} ({setup['rr']}R) | Tasdiq: {setup['confirmations']}",
            )
        time.sleep(9)


def status_text(cfg):
    m = "Barcha sessiyalar (24/7)" if cfg.get("all_sessions", True) else "Killzone"
    return (
        f"📊 Holat\n• Juftliklar: {', '.join(PAIRS)}\n"
        f"• Min RR: {cfg.get('min_rr')}R | Tasdiq: {cfg.get('min_confirmations')} ta\n"
        f"• Rejim: {m}\n"
        f"• Jami: {cfg.get('total_r', 0.0):+.2f}R | Aktiv: {len(cfg.get('active_signals', []))} ta"
    )


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


def handle_updates(cfg, last_id):
    q = {"timeout": 1, "allowed_updates": ["message"]}
    if last_id is not None:
        q["offset"] = last_id + 1
    url = "[https://api.telegram.org/bot](https://api.telegram.org/bot)" + TOKEN + "/getUpdates?" + urllib.parse.urlencode(q)
    data = get_json(url)
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

        if low.startswith("/start"):
            send_msg(cid, "Assalomu alaykum! MTrade botiga xush kelibsiz.\nMenyudan bo'lim tanlang:", KB)
        elif "natijalar" in low or low.startswith("/results"):
            send_msg(cid, results_text(cfg), KB)
        elif "holat" in low or low.startswith("/status"):
            send_msg(cid, status_text(cfg), KB)
        elif "yordam" in low or low.startswith("/help"):
            send_msg(cid, "ℹ️ Yordam\n• 📈 Natijalar (/results)\n• 📊 Holat (/status)\n• Savol yozsangiz bot javob qaytaradi.", KB)
        elif low.startswith("/admin"):
            if not is_admin(cid):
                send_msg(cid, "⛔ Faqat admin uchun.", KB)
            else:
                send_msg(
                    cid,
                    f"🛠 ADMIN PANEL\n\n{status_text(cfg)}\n\nBuyruqlar:\n/set_rr 2.0\n/set_conf 3\n/sessions all | killzone\n/reset_r\n/get_code\n/set_code <kod> (yoki .py fayl)\n/add_func <kod>\n/replace\neski\n---\nyangi\n/rollback",
                    KB,
                )
        elif low.startswith("/get_code") and is_admin(cid):
            send_code_file(cid)
        elif low.startswith("/set_rr") and is_admin(cid):
            try:
                cfg["min_rr"] = round(float(text.split(maxsplit=1)[1].replace(",", ".")), 2)
                save_cfg(cfg)
                send_msg(cid, f"✅ Min RR: {cfg['min_rr']}R", KB)
            except Exception:
                send_msg(cid, "Namuna: /set_rr 2.0", KB)
        elif low.startswith("/set_conf") and is_admin(cid):
            try:
                cfg["min_confirmations"] = int(text.split(maxsplit=1)[1])
                save_cfg(cfg)
                send_msg(cid, f"✅ Min tasdiq: {cfg['min_confirmations']}", KB)
            except Exception:
                send_msg(cid, "Namuna: /set_conf 3", KB)
        elif low.startswith("/sessions") and is_admin(cid):
            m = text.split(maxsplit=1)[1].strip().lower() if len(text.split()) > 1 else ""
            if m in ("all", "killzone"):
                cfg["all_sessions"] = (m == "all")
                save_cfg(cfg)
                send_msg(cid, f"✅ Rejim: {m}", KB)
            else:
                send_msg(cid, "Namuna: /sessions all yoki /sessions killzone", KB)
        elif low.startswith("/reset_r") and is_admin(cid):
            cfg["total_r"], cfg["closed_results"] = 0.0, []
            save_cfg(cfg)
            send_msg(cid, "✅ R va natijalar tozalandi.", KB)
        elif (
            low.startswith("/set_code")
            or low.startswith("/update_code")
            or (doc.get("file_id") and not low.startswith("/add_func") and not low.startswith("/replace"))
        ) and is_admin(cid):
            code = download_doc(doc["file_id"]) if doc.get("file_id") else text[len("/update_code" if low.startswith("/update_code") else "/set_code"):]
            apply_code(code or "", cfg, cid)
        elif low.startswith("/add_func") and is_admin(cid):
            extra = text[len("/add_func"):].strip()
            with open(os.path.abspath(__file__), "r", encoding="utf-8") as f:
                cur = f.read()
            apply_code(cur.rstrip() + "\n\n\n" + extra + "\n", cfg, cid)
        elif low.startswith("/replace") and is_admin(cid):
            body = text[len("/replace"):].lstrip("\r\n")
            if "\n---\n" not in body:
                send_msg(cid, "Namuna:\n/replace\neski\n---\nyangi", KB)
            else:
                o, n = body.split("\n---\n", 1)
                with open(os.path.abspath(__file__), "r", encoding="utf-8") as f:
                    cur = f.read()
                if o not in cur:
                    send_msg(cid, "❌ Eski matn topilmadi.", KB)
                else:
                    apply_code(cur.replace(o, n, 1), cfg, cid)
        elif low.startswith("/rollback") and is_admin(cid):
            if os.path.exists(BAK_FILE):
                with open(BAK_FILE, "r", encoding="utf-8") as f:
                    apply_code(f.read(), cfg, cid)
            else:
                send_msg(cid, "❌ Zaxira fayl topilmadi.", KB)
        elif text:
            send_msg(cid, f"Qabul qilindi: \"{text}\"\nNatijalar: /results | Holat: /status", KB)
    return last_id


def main():
    cfg = load_cfg()
    save_cfg(cfg)
    send_msg(
        ADMIN_ID,
        f"📡 EXNESS SIGNAL BOT ISHGA TUSHDI\nJuftliklar: {', '.join(PAIRS)} | Min RR: {cfg['min_rr']}R | Tasdiq: {cfg['min_confirmations']}\nAdmin: /admin",
        KB,
    )
    last_id, last_scan = None, 0.0
    while True:
        last_id = handle_updates(cfg, last_id)
        if time.time() - last_scan >= int(cfg.get("check_interval_sec", 300)):
            last_scan = time.time()
            scan_market(cfg)
        time.sleep(2)


if __name__ == "__main__":
    main()
