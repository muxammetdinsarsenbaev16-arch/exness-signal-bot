import json, logging, os, time, urllib.parse, urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TOKEN = "".join(os.getenv("TELEGRAM_BOT_TOKEN", "").split())
ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TD_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

CFG_FILE = os.getenv("CFG_FILE", "bot_config.json")
BAK_FILE = os.path.abspath(__file__) + ".bak"
PAIRS = ["XAUUSD", "EURUSD", "GBPUSD"]
CUR_UPD = None  # hozir ishlanayotgan Telegram update id
ADX_MIN = 25  # ADX shu qiymatdan yuqori bo'lsa "trend bor"
API_SLEEP = 9  # TwelveData bepul tarif: ~8 so'rov/daqiqa

DEFAULT_CFG = {
    "min_rr": 2.0,
    "min_confirmations": 3,
    "all_sessions": True,
    "check_interval_sec": 300,
    "total_r": 0.0,
    "active_signals": [],
    "closed_results": [],
    "strategies": {"snr": True, "bank": True, "ict": True, "level": True, "london": True,
                   "smart": True, "cloud": True, "golden": True, "channel": True},
    "indicators": True,
    "last_sig": {},
    "strat_stats": {},
}

KB = {
    "keyboard": [[{"text": "📈 Natijalar"}, {"text": "📊 Holat"}], [{"text": "ℹ️ Yordam"}]],
    "resize_keyboard": True,
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    cfg["active_signals"] = []
    cfg["closed_results"] = []
    cfg["last_sig"] = {}
    cfg["strat_stats"] = {}
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            logging.warning("Config read err: %s", e)
    st = dict(DEFAULT_CFG["strategies"])
    st.update(cfg.get("strategies") or {})
    cfg["strategies"] = st
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
        if CUR_UPD is not None:  # buyruqni Telegramda "o'qildi" deb belgilaymiz, aks holda qayta ishga tushganda yana keladi
            get_json(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={CUR_UPD + 1}&timeout=0")
        time.sleep(1)
        os._exit(0)
    except Exception as e:
        send_msg(cid, f"❌ Saqlash xatosi: {e}", KB)


def add_result(cfg, pair, outcome, r_val, strat=""):
    cfg.setdefault("closed_results", []).append(
        {"pair": pair, "outcome": outcome, "r": round(float(r_val), 2), "strat": strat})
    cfg["closed_results"] = cfg["closed_results"][-15:]
    for name in [x for x in str(strat).split("+") if x]:  # birlashgan signal har bir ishtirokchi strategiyaga yoziladi
        st = cfg.setdefault("strat_stats", {}).setdefault(name, {"tp": 0, "sl": 0, "be": 0, "r": 0.0})
        st["tp" if outcome == "TP" else "sl" if outcome == "SL" else "be"] += 1
        st["r"] = round(st["r"] + float(r_val), 2)
    cfg["total_r"] = round(float(cfg.get("total_r", 0.0)) + float(r_val), 2)
    save_cfg(cfg)


def fetch_candles(pair, outputsize=60):
    if not TD_KEY:
        return []
    sym = f"{pair[:3]}/{pair[3:]}" if len(pair) == 6 else pair
    q = urllib.parse.urlencode({"symbol": sym, "interval": "15min", "outputsize": outputsize, "timezone": "UTC", "apikey": TD_KEY})
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


def kz_hour(h):
    return 7 <= h < 10 or 12 <= h < 16  # UTC: London / New York


def mirror(cs):
    """Narx oynasi: BUY mantig'i oynalangan shamlarda SELL beradi."""
    return [{"open": -c["open"], "high": -c["low"], "low": -c["high"], "close": -c["close"], "dt": c["dt"]} for c in cs]


def swings(cs, n=2):
    highs, lows = [], []
    for i in range(n, len(cs) - n):
        h, l = cs[i]["high"], cs[i]["low"]
        if all(h > cs[j]["high"] for j in range(i - n, i)) and all(h >= cs[j]["high"] for j in range(i + 1, i + n + 1)):
            highs.append((i, h))
        if all(l < cs[j]["low"] for j in range(i - n, i)) and all(l <= cs[j]["low"] for j in range(i + 1, i + n + 1)):
            lows.append((i, l))
    return highs, lows


def build_setup(pair, side, entry, sl, tp, name, reasons, dt, min_rr):
    dec = 2 if "XAU" in pair else 5
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if side == "BUY" and not (sl < entry < tp):
        return None
    if side == "SELL" and not (tp < entry < sl):
        return None
    rr = abs(tp - entry) / risk
    if rr < min_rr:
        return None
    return {"pair": pair, "side": side, "entry": round(entry, dec), "sl": round(sl, dec), "tp": round(tp, dec),
            "rr": round(rr, 2), "strategies": [name], "reasons": list(reasons), "confirmations": len(reasons),
            "be_moved": False, "opened": dt}


FLIP = [
    ("kechagi kun pasti (PDL)", "kechagi kun yuqorisi (PDH)"),
    ("PDL ostidagi", "PDH ustidagi"),
    ("pastki likvidligi", "yuqori likvidligi"),
    ("uzun pastki soya", "uzun yuqori soya"),
    ("bullish", "bearish"),
    ("zona tepasida yopildi", "zona ostida yopildi"),
    ("discount zonasida (BUY uchun qulay)", "premium zonasida (SELL uchun qulay)"),
    ("discount zonasida", "premium zonasida"),
    ("talab zonasi", "taklif zonasi"),
    ("Osiyo diapazoni yuqorisi impuls bilan yorildi", "Osiyo diapazoni pasti impuls bilan pastga yorildi"),
    ("impuls sham yuqorida yopildi", "impuls sham pastda yopildi"),
    ("sham yuqorida yopildi", "sham pastda yopildi"),
    ("bulutidan yuqorida", "bulutidan pastda"),
    ("yuqoriga kesishdi", "pastga kesishdi"),
    ("bulut yashil (SpanA > SpanB)", "bulut qizil (SpanA < SpanB)"),
    ("Tenkan Kijun ustida", "Tenkan Kijun ostida"),
    ("26 sham oldingidan yuqori", "26 sham oldingidan pastroq"),
    ("kanal yuqorisi yorildi", "kanal pasti pastga yorildi"),
]


def flip_text(t):
    """Strategiya matnlari BUY yo'nalishida yozilgan; SELL signal uchun teskari so'zlarga o'giriladi."""
    for old, new in FLIP:
        t = t.replace(old, new)
    return t


def run_both(fn, pair, cs, min_rr):
    out = []
    a = atr(cs, 14)
    if a <= 0:
        return out
    for side, data, sgn in (("BUY", cs, 1), ("SELL", mirror(cs), -1)):
        r = fn(data, a, min_rr)
        if not r:
            continue
        reasons = r["reasons"] if side == "BUY" else [flip_text(x) for x in r["reasons"]]
        st = build_setup(pair, side, sgn * data[-1]["close"], sgn * r["sl"], sgn * r["tp"],
                         r["name"], reasons, cs[-1]["dt"], min_rr)
        if st:
            out.append(st)
    return out


def _snr_buy(cs, a, min_rr):
    """SNR: chuqurlikdagi (trough) katta soyali sham zonasi. Narx keyingi safar shu zonaga kelganda BUY.
    TP = HH, SL = zona ortida."""
    N = len(cs)
    highs, lows = swings(cs, 2)
    last, prev = cs[-1], cs[-2]
    closes = [c["close"] for c in cs]
    ef, es = ema(closes, 8), ema(closes, 21)
    pick = None
    for (i, _l) in reversed(lows):
        if N - 1 - i < 6:
            continue
        cand = cs[i - 1:i + 2]
        c = max(cand, key=lambda x: min(x["open"], x["close"]) - x["low"])  # eng katta pastki soya
        wick = min(c["open"], c["close"]) - c["low"]
        if wick < 0.3 * a:
            continue
        zlow, zhigh = c["low"], min(c["open"], c["close"])
        after = cs[i + 2:-1]
        if any(x["close"] < zlow for x in after):
            continue  # zona sindirilgan
        touched = sum(1 for x in after if x["low"] <= zhigh)
        if last["low"] <= zhigh and last["close"] > zlow and prev["low"] > zhigh:
            pick = (zlow, zhigh, wick, touched)
            break
    if not pick:
        return None
    zlow, zhigh, wick, touched = pick
    hh = [h for (i, h) in highs if i > N - 60]
    if not hh or max(hh) <= last["close"]:
        return None
    reasons = [f"SNR: soyali sham zonasi (soya {wick / a:.1f} ATR)"]
    if last["close"] > last["open"]:
        reasons.append("SNR: zonadan bullish rad etilish")
    if wick >= 0.6 * a:
        reasons.append("SNR: soya juda katta (≥0.6 ATR)")
    if touched == 0:
        reasons.append("SNR: zona toza (birinchi test)")
    if last["close"] > zhigh:
        reasons.append("SNR: zona tepasida yopildi")
    if ef > es:
        reasons.append("SNR: EMA trendi yo'nalishda")
    return {"name": "SNR", "sl": zlow - 0.15 * a, "tp": max(hh), "reasons": reasons}


def _bank_buy(cs, a, min_rr):
    """Bank manipulyatsiyasi: Osiyo diapazoni pastki likvidligi olinadi (stop-hunt), narx diapazon ichiga qaytadi.
    TP = diapazon yuqorisi, SL = sweep ortida."""
    last = cs[-1]
    day = last["dt"][:10]
    hr = lambda c: int(c["dt"][11:13])
    if not 7 <= hr(last) < 17:
        return None
    asia = [c for c in cs if c["dt"][:10] == day and hr(c) < 7]
    if len(asia) < 16:
        return None
    al, ah = min(c["low"] for c in asia), max(c["high"] for c in asia)
    if ah - al < 0.5 * a:
        return None
    after = [c for c in cs if c["dt"][:10] == day and hr(c) >= 7]
    recent = after[-8:]
    sweeps = [c for c in recent if c["low"] < al]
    if not sweeps or last["close"] <= al or last["close"] >= (al + ah) / 2:
        return None
    sweep_low = min(c["low"] for c in recent)
    depth = al - sweep_low
    sc = min(sweeps, key=lambda c: c["low"])
    rng = sc["high"] - sc["low"]
    lw = min(sc["open"], sc["close"]) - sc["low"]
    closes = [c["close"] for c in cs]
    reasons = ["Bank: Osiyo diapazoni pastki likvidligi olindi va narx qaytdi (stop-hunt)"]
    if rng > 0 and lw >= 0.5 * rng:
        reasons.append("Bank: manipulyatsiya shamida uzun pastki soya")
    if depth >= 0.3 * a:
        reasons.append("Bank: sweep chuqurligi ≥0.3 ATR")
    if last["close"] > last["open"] and last["close"] - last["open"] >= 0.5 * a:
        reasons.append("Bank: kuchli bullish qaytish shami")
    if kz_hour(hr(last)):
        reasons.append("Bank: killzone vaqtida")
    if ema(closes, 8) > ema(closes, 21):
        reasons.append("Bank: EMA trendi yo'nalishda")
    return {"name": "BANK", "sl": sweep_low - 0.15 * a, "tp": ah, "reasons": reasons}


def _ict_buy(cs, a, min_rr):
    """ICT: likvidlik sweep + market structure shift (MSS) + FVG ga qaytish. TP = yuqoridagi likvidlik (swing high)."""
    N = len(cs)
    highs, lows = swings(cs, 2)
    last = cs[-1]
    found = None
    for (j, L) in reversed(lows):
        if N - 1 - j < 4:
            continue
        k = next((x for x in range(j + 1, N) if cs[x]["low"] < L), None)
        if k is None or k < N - 16:
            continue
        sh = max(cs[x]["high"] for x in range(j, k + 1))
        if not any(cs[x]["close"] > sh for x in range(k + 1, N)):
            continue
        fvg = None
        for m in range(N - 2, k, -1):
            if cs[m - 1]["high"] < cs[m + 1]["low"]:
                fvg = (cs[m - 1]["high"], cs[m + 1]["low"], m)
                break
        if not fvg or fvg[2] + 1 >= N - 1:
            continue
        fb, ft, m = fvg
        if last["low"] <= ft and last["close"] > fb:
            found = (k, fb, ft)
            break
    if not found:
        return None
    k, fb, ft = found
    sweep_low = min(cs[x]["low"] for x in range(k, N))
    price = last["close"]
    sl = sweep_low - 0.15 * a
    risk = price - sl
    if risk <= 0:
        return None
    cands = sorted(h for (i, h) in highs if h > price and i > N - 80)
    tp = next((h for h in cands if h - price >= min_rr * risk), None)
    if tp is None:
        return None
    closes = [c["close"] for c in cs]
    reasons = ["ICT: likvidlik olindi (sweep)", "ICT: market structure shift (MSS)", "ICT: FVG ga qaytish"]
    if any(abs(cs[x]["close"] - cs[x]["open"]) >= 1.2 * a for x in range(k, N)):
        reasons.append("ICT: displacement (kuchli impuls)")
    if kz_hour(int(last["dt"][11:13])):
        reasons.append("ICT: killzone vaqtida")
    rng_lo, rng_hi = min(c["low"] for c in cs[-40:]), max(c["high"] for c in cs[-40:])
    if price < (rng_lo + rng_hi) / 2:
        reasons.append("ICT: discount zonasida (BUY uchun qulay)")
    sc = cs[k]
    if sc["high"] - sc["low"] > 0 and (min(sc["open"], sc["close"]) - sc["low"]) >= 0.5 * (sc["high"] - sc["low"]):
        reasons.append("ICT: sweep shamida uzun soya")
    if ema(closes, 8) > ema(closes, 21):
        reasons.append("ICT: EMA trendi yo'nalishda")
    return {"name": "ICT", "sl": sl, "tp": tp, "reasons": reasons}


def rr_tp(price, sl, min_rr):
    """Trend strategiyalari uchun TP: RR asosida (strukturaviy nishon yo'q)."""
    return price + (min_rr + 0.05) * (price - sl)


def _prev_levels(cs):
    day = cs[-1]["dt"][:10]
    days = sorted({c["dt"][:10] for c in cs if c["dt"][:10] < day})
    if not days:
        return None
    pc = [c for c in cs if c["dt"][:10] == days[-1]]
    if len(pc) < 40:
        return None
    return max(c["high"] for c in pc), min(c["low"] for c in pc)


def _level_buy(cs, a, min_rr):
    """LEVEL (Kun Darajasi / Daraja Zarbasi): kechagi kun pasti (PDL) dan bullish qaytish. TP = kechagi kun yuqorisi (PDH)."""
    lv = _prev_levels(cs)
    if not lv:
        return None
    pdh, pdl = lv
    last, prev = cs[-1], cs[-2]
    if pdh - pdl < 2 * a or pdh <= last["close"]:
        return None
    if not (last["low"] <= pdl + 0.1 * a and last["close"] > pdl and last["close"] > last["open"]):
        return None
    if prev["close"] < pdl - 0.5 * a:
        return None
    rng = last["high"] - last["low"]
    reasons = ["LEVEL: kechagi kun pasti (PDL) dan qaytish"]
    if last["low"] < pdl:
        reasons.append("LEVEL: PDL ostidagi stoplar olinib qaytdi (sweep)")
    if rng > 0 and (min(last["open"], last["close"]) - last["low"]) >= 0.5 * rng:
        reasons.append("LEVEL: uzun pastki soya")
    if last["close"] - last["open"] >= 0.5 * a:
        reasons.append("LEVEL: kuchli bullish sham")
    if kz_hour(int(last["dt"][11:13])):
        reasons.append("LEVEL: killzone vaqtida")
    return {"name": "LEVEL", "sl": min(last["low"], pdl) - 0.25 * a, "tp": pdh, "reasons": reasons}


def _london_buy(cs, a, min_rr):
    """LONDON (London Impuls): London ochilishida Osiyo diapazoni yuqorisidan kuchli yuqoriga yorib o'tish. TP = RR asosida."""
    last = cs[-1]
    day, hr = last["dt"][:10], int(last["dt"][11:13])
    if not 7 <= hr < 12:
        return None
    asia = [c for c in cs if c["dt"][:10] == day and int(c["dt"][11:13]) < 7]
    if len(asia) < 16:
        return None
    ah, al = max(c["high"] for c in asia), min(c["low"] for c in asia)
    if ah - al < 1.5 * a:
        return None
    after = [c for c in cs if c["dt"][:10] == day and int(c["dt"][11:13]) >= 7]
    bc = next((c for c in after[-4:] if c["close"] > ah + 0.1 * a and c["close"] - c["open"] >= 0.8 * a), None)
    if bc is None or last["close"] <= ah or last["close"] - ah > 2.5 * a:
        return None
    sl = ah - 0.5 * a
    closes = [c["close"] for c in cs]
    reasons = ["LONDON: Osiyo diapazoni yuqorisi impuls bilan yorildi"]
    if ah - al <= 6 * a:
        reasons.append("LONDON: Osiyo diapazoni siqilgan (≤6 ATR)")
    if bc["close"] - bc["open"] >= 1.2 * a:
        reasons.append("LONDON: juda kuchli impuls shami (≥1.2 ATR)")
    if bc["high"] > bc["low"] and (bc["high"] - bc["close"]) <= 0.25 * (bc["high"] - bc["low"]):
        reasons.append("LONDON: impuls sham yuqorida yopildi")
    if hr < 10:
        reasons.append("LONDON: killzone (07-10 UTC)")
    if ema(closes, 8) > ema(closes, 21):
        reasons.append("LONDON: EMA trendi yo'nalishda")
    return {"name": "LONDON", "sl": sl, "tp": rr_tp(last["close"], sl, min_rr), "reasons": reasons}


def _smart_buy(cs, a, min_rr):
    """SMART (Smart Zone): order block (talab zonasi) + BOS. Narx zonaga birinchi marta qaytib bullish rad etilganda BUY."""
    N = len(cs)
    last = cs[-1]
    if N < 30:
        return None
    pick = None
    for i in range(N - 8, max(12, N - 46), -1):
        c = cs[i]
        if c["close"] >= c["open"]:
            continue
        top = max(x["close"] for x in cs[i + 1:i + 4])
        if top - c["high"] < 1.2 * a:
            continue
        if top <= max(x["high"] for x in cs[max(0, i - 10):i]):
            continue  # BOS yo'q
        zlow, zhigh = c["low"], c["open"]
        after = cs[i + 4:-1]
        if any(x["close"] < zlow for x in after) or any(x["low"] <= zhigh for x in after):
            continue  # zona sindi yoki allaqachon ishlatilgan
        if last["low"] <= zhigh and last["close"] > zlow and last["close"] > last["open"]:
            pick = (i, zlow, zhigh, top - c["high"])
            break
    if not pick:
        return None
    i, zlow, zhigh, disp = pick
    tp = max(x["high"] for x in cs[i + 1:])
    if tp <= last["close"]:
        return None
    closes = [c["close"] for c in cs]
    rng = last["high"] - last["low"]
    reasons = ["SMART: order block (talab zonasi) + BOS", "SMART: zonaga birinchi qaytish"]
    if disp >= 2 * a:
        reasons.append("SMART: impuls juda kuchli (≥2 ATR)")
    if rng > 0 and (min(last["open"], last["close"]) - last["low"]) >= 0.5 * rng:
        reasons.append("SMART: uzun pastki soya (rad etilish)")
    lo40, hi40 = min(c["low"] for c in cs[-40:]), max(c["high"] for c in cs[-40:])
    if last["close"] < (lo40 + hi40) / 2:
        reasons.append("SMART: discount zonasida")
    if ema(closes, 8) > ema(closes, 21):
        reasons.append("SMART: EMA trendi yo'nalishda")
    return {"name": "SMART", "sl": zlow - 0.15 * a, "tp": tp, "reasons": reasons}


def _ichi(cs, i):
    t = (max(c["high"] for c in cs[i - 8:i + 1]) + min(c["low"] for c in cs[i - 8:i + 1])) / 2
    k = (max(c["high"] for c in cs[i - 25:i + 1]) + min(c["low"] for c in cs[i - 25:i + 1])) / 2
    return t, k


def _cloud_buy(cs, a, min_rr):
    """CLOUD (Bulut / Ichimoku): narx bulut ustida va Tenkan/Kijun yuqoriga kesishgan yoki bulutdan yangi chiqqan. TP = RR asosida."""
    N = len(cs)
    if N < 80:
        return None
    last = cs[-1]
    j = N - 27
    tj, kj = _ichi(cs, j)
    span_a = (tj + kj) / 2
    span_b = (max(c["high"] for c in cs[j - 51:j + 1]) + min(c["low"] for c in cs[j - 51:j + 1])) / 2
    top, bot = max(span_a, span_b), min(span_a, span_b)
    if last["close"] <= top:
        return None
    tk, kj_now = _ichi(cs, N - 1)
    cross = False
    for i in range(N - 3, N):
        t1, k1 = _ichi(cs, i - 1)
        t2, k2 = _ichi(cs, i)
        if t1 <= k1 and t2 > k2:
            cross = True
    fresh = any(cs[i]["close"] <= top for i in range(N - 4, N - 1))
    if not (cross or fresh):
        return None
    sl = min(kj_now, top) - 0.2 * a
    if sl >= last["close"]:
        return None
    reasons = ["CLOUD: narx Ichimoku bulutidan yuqorida"]
    if cross:
        reasons.append("CLOUD: Tenkan/Kijun yuqoriga kesishdi")
    if fresh:
        reasons.append("CLOUD: bulutdan yangi chiqish")
    if span_a > span_b:
        reasons.append("CLOUD: bulut yashil (SpanA > SpanB)")
    if tk > kj_now:
        reasons.append("CLOUD: Tenkan Kijun ustida")
    if last["close"] > cs[N - 27]["close"]:
        reasons.append("CLOUD: Chikou (narx 26 sham oldingidan yuqori)")
    return {"name": "CLOUD", "sl": sl, "tp": rr_tp(last["close"], sl, min_rr), "reasons": reasons}


def _golden_buy(cs, a, min_rr):
    """GOLDEN (Oltin Qaytish): impuls to'lqinining Fibonacci 50–78.6% zonasiga qaytish. TP = impuls yuqorisi (yoki 1.272 kengaytma)."""
    N = len(cs)
    highs, lows = swings(cs, 2)
    if not highs:
        return None
    ih, H = highs[-1]
    if N - 1 - ih < 3 or ih < N - 60:
        return None
    st = max(0, ih - 40)
    seg = cs[st:ih]
    if len(seg) < 5:
        return None
    il = st + min(range(len(seg)), key=lambda x: seg[x]["low"])
    L = cs[il]["low"]
    R = H - L
    if R < 3 * a or il >= ih - 3:
        return None
    f50, f618, f786 = H - 0.5 * R, H - 0.618 * R, H - 0.786 * R
    last = cs[-1]
    after = cs[ih + 1:-1]
    if any(x["close"] < f786 for x in after):
        return None
    if not (last["low"] <= f50 and last["close"] > f786 and last["close"] > last["open"]):
        return None
    sl = min(last["low"], f786) - 0.15 * a
    price = last["close"]
    risk = price - sl
    if risk <= 0:
        return None
    tp = next((t for t in (H, H + 0.272 * R) if t > price and (t - price) >= min_rr * risk), None)
    if tp is None:
        return None
    closes = [c["close"] for c in cs]
    rng = last["high"] - last["low"]
    reasons = [f"GOLDEN: Fibonacci 50–78.6% zonasiga qaytish (impuls {R / a:.1f} ATR)"]
    if last["low"] <= f618:
        reasons.append("GOLDEN: 61.8% darajasiga yetdi")
    if not any(x["low"] <= f50 for x in after):
        reasons.append("GOLDEN: zonaga birinchi qaytish")
    if rng > 0 and (min(last["open"], last["close"]) - last["low"]) >= 0.5 * rng:
        reasons.append("GOLDEN: uzun pastki soya (rad etilish)")
    if R >= 5 * a:
        reasons.append("GOLDEN: impuls katta (≥5 ATR)")
    if ema(closes, 8) > ema(closes, 21):
        reasons.append("GOLDEN: EMA trendi yo'nalishda")
    return {"name": "GOLDEN", "sl": sl, "tp": tp, "reasons": reasons}


def _channel_buy(cs, a, min_rr):
    """CHANNEL (Kanal Yorig'i): 20 shamlik Donchian kanali yuqorisini kuchli sham bilan yorib o'tish. TP = RR asosida."""
    N = len(cs)
    if N < 45:
        return None
    last = cs[-1]
    prior = cs[-21:-1]
    hh, ll = max(c["high"] for c in prior), min(c["low"] for c in prior)
    width = hh - ll
    body = last["close"] - last["open"]
    if width < 2.5 * a or last["close"] <= hh or body < 0.6 * a or last["close"] - hh > 1.5 * a:
        return None
    sl = hh - 0.5 * a
    if sl >= last["close"]:
        return None
    old = cs[-41:-21]
    closes = [c["close"] for c in cs]
    reasons = ["CHANNEL: 20 shamlik kanal yuqorisi yorildi"]
    if width < (max(c["high"] for c in old) - min(c["low"] for c in old)):
        reasons.append("CHANNEL: kanal torayganidan keyin (squeeze)")
    if body >= 1.2 * a:
        reasons.append("CHANNEL: juda kuchli yorib o'tish shami (≥1.2 ATR)")
    if last["high"] > last["low"] and (last["high"] - last["close"]) <= 0.25 * (last["high"] - last["low"]):
        reasons.append("CHANNEL: sham yuqorida yopildi")
    if kz_hour(int(last["dt"][11:13])):
        reasons.append("CHANNEL: killzone vaqtida")
    if ema(closes, 8) > ema(closes, 21):
        reasons.append("CHANNEL: EMA trendi yo'nalishda")
    return {"name": "CHANNEL", "sl": sl, "tp": rr_tp(last["close"], sl, min_rr), "reasons": reasons}


STRATS = [("snr", _snr_buy), ("bank", _bank_buy), ("ict", _ict_buy), ("level", _level_buy),
          ("london", _london_buy), ("smart", _smart_buy), ("cloud", _cloud_buy),
          ("golden", _golden_buy), ("channel", _channel_buy)]
ALL_KEYS = [k for k, _ in STRATS]
DESC = {
    "snr": "SNR: katta soyali sham zonasiga qaytish (TP HH/LL)",
    "bank": "BANK: Osiyo diapazoni stop-hunt va qaytish",
    "ict": "ICT: sweep + MSS + FVG",
    "level": "LEVEL: kechagi kun high/low (PDH/PDL) dan qaytish",
    "london": "LONDON: London ochilishida Osiyo diapazonini impuls bilan yorish",
    "smart": "SMART: order block + BOS, zonaga qaytish",
    "cloud": "CLOUD: Ichimoku bulutidan chiqish + Tenkan/Kijun",
    "golden": "GOLDEN: Fibonacci 50–78.6% zonasiga qaytish",
    "channel": "CHANNEL: 20 shamlik kanalni yorib o'tish",
}


def combine(setups, min_conf):
    """Bir tomondagi barcha strategiyalar tasdiqlari qo'shiladi. Jami tasdiq min_conf dan kam bo'lsa signal yo'q."""
    if not setups:
        return None
    tot = {"BUY": 0, "SELL": 0}
    for st in setups:
        tot[st["side"]] += st["confirmations"]
    if tot["BUY"] == tot["SELL"]:
        return None
    side = "BUY" if tot["BUY"] > tot["SELL"] else "SELL"
    if tot[side] < min_conf:
        return None
    grp = [st for st in setups if st["side"] == side]
    merged = dict(max(grp, key=lambda st: st["confirmations"]))
    merged["strategies"], merged["reasons"] = [], []
    for st in grp:
        merged["strategies"] += [x for x in st["strategies"] if x not in merged["strategies"]]
        merged["reasons"] += st["reasons"]
    merged["confirmations"] = len(merged["reasons"])
    return merged


def ema_series(vals, n):
    k = 2.0 / (n + 1)
    v, out = vals[0], []
    for p in vals:
        v = p * k + v * (1 - k)
        out.append(v)
    return out


def macd_calc(closes):
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema_series(macd, 9)
    return macd, sig, [m - x for m, x in zip(macd, sig)]


def adx_calc(cs, n=14):
    if len(cs) < 3 * n:
        return None, 0.0, 0.0
    trs, pdm, mdm = [], [], []
    for i in range(1, len(cs)):
        up, dn = cs[i]["high"] - cs[i - 1]["high"], cs[i - 1]["low"] - cs[i]["low"]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        trs.append(max(cs[i]["high"] - cs[i]["low"], abs(cs[i]["high"] - cs[i - 1]["close"]),
                       abs(cs[i]["low"] - cs[i - 1]["close"])))
    s_tr, s_p, s_m = sum(trs[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs, pdi, mdi = [], 0.0, 0.0
    for i in range(n - 1, len(trs)):
        if i >= n:
            s_tr = s_tr - s_tr / n + trs[i]
            s_p = s_p - s_p / n + pdm[i]
            s_m = s_m - s_m / n + mdm[i]
        pdi = 100 * s_p / s_tr if s_tr else 0.0
        mdi = 100 * s_m / s_tr if s_tr else 0.0
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    if len(dxs) < n:
        return None, pdi, mdi
    adx = sum(dxs[:n]) / n
    for d in dxs[n:]:
        adx = (adx * (n - 1) + d) / n
    return adx, pdi, mdi


def indicator_confirms(cs, side):
    """Indikator tasdiqlari (BUY yo'nalishida hisoblanadi, SELL uchun narx oynalanadi).
    Qaytaradi: (tasdiqlar ro'yxati, H1 trend mosmi)."""
    d = cs if side == "BUY" else mirror(cs)
    closes = [c["close"] for c in d]
    w = "ustida" if side == "BUY" else "ostida"
    out, strong = [], False
    if len(closes) >= 200 and closes[-1] > ema(closes, 200):
        out.append(f"Indikator: narx EMA200 {w}")
    macd, sig, hist = macd_calc(closes)
    for i in range(max(1, len(macd) - 6), len(macd)):
        if macd[i - 1] <= sig[i - 1] and macd[i] > sig[i] and macd[i] > 0:
            out.append(f"Indikator: MACD kesishuvi nol {w}")
            break
    if hist[-1] > 0 and hist[-1] > hist[-2]:
        out.append("Indikator: MACD gistogrammasi mos tomonda")
    adx, pdi, mdi = adx_calc(d)
    if adx is not None and adx >= ADX_MIN and pdi > mdi:
        out.append(f"Indikator: ADX {adx:.0f} (trend bor)")
    h1 = {}
    for c in d:
        h1[c["dt"][:13]] = c["close"]  # har soatning oxirgi yopilishi
    h1c = list(h1.values())
    if len(h1c) >= 25 and ema(h1c, 9) > ema(h1c, 21):
        out.append("Indikator: yuqori timeframe (H1) trendi mos")
        strong = True
    return out, strong


def find_all(pair, cs, cfg):
    if len(cs) < 40:
        return None
    min_rr = float(cfg.get("min_rr", 2.0))
    enabled = cfg.get("strategies", {})
    setups = []
    for key, fn in STRATS:
        if enabled.get(key, True):
            try:
                setups += run_both(fn, pair, cs, min_rr)
            except Exception as e:
                logging.warning("Strategy %s err: %s", key, e)
    m = combine(setups, 1)
    if not m:
        return None
    m["strong"] = False
    if cfg.get("indicators", True):
        try:
            extra, m["strong"] = indicator_confirms(cs, m["side"])
            m["reasons"] += extra
        except Exception as e:
            logging.warning("Indicator err: %s", e)
    m["confirmations"] = len(m["reasons"])
    if m["confirmations"] < int(cfg.get("min_confirmations", 3)):
        return None
    return m


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
        cs = fetch_candles(pair, 100)
        time.sleep(API_SLEEP)
        if not cs:
            rem.append(sig)
            continue
        before = (sig.get("be_moved"), sig.get("sl"), sig.get("opened"))
        res = check_signal(sig, cs, cfg.get("min_rr", 2.0))
        if res:
            outcome, r = res
            add_result(cfg, pair, outcome, r, "+".join(sig.get("strategies", [])))
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
        cs = fetch_candles(pair, 300)
        ls = cfg.get("last_sig", {}).get(pair)
        if ls and ls in [c["dt"] for c in cs[-8:]]:
            time.sleep(API_SLEEP)
            continue  # yaqinda signal berilgan, takrorlamaymiz
        setup = find_all(pair, cs, cfg)
        if setup:
            cfg.setdefault("active_signals", []).append(setup)
            cfg.setdefault("last_sig", {})[pair] = setup["opened"]
            save_cfg(cfg)
            why = "\n".join(f"✔ {x}" for x in setup["reasons"])
            if cfg.get("indicators", True):
                why += "\n" + ("⭐ Kuchli signal: H1 trend bilan bir yo'nalishda" if setup.get("strong")
                               else "⚠️ H1 trend bilan mos emas")
            send_msg(
                ADMIN_ID,
                f"🚨 YANGI SIGNAL: {setup['pair']} ({setup['side']})\n"
                f"Asos: {' + '.join(setup['strategies'])}\n"
                f"Entry: {setup['entry']} | SL: {setup['sl']} | TP: {setup['tp']} ({setup['rr']}R)\n"
                f"Tasdiqlar ({setup['confirmations']} ta):\n{why}",
            )
        time.sleep(API_SLEEP)


def on_names(cfg):
    st = cfg.get("strategies", {})
    names = [k.upper() for k in ALL_KEYS if st.get(k, True)]
    return ", ".join(names) if names else "hech biri (o'chiq)"


def strategies_text(cfg):
    st = cfg.get("strategies", {})
    lines = ["🧠 STRATEGIYALAR"]
    for k in ALL_KEYS:
        lines.append(f"{'✅' if st.get(k, True) else '❌'} {DESC[k]}")
    lines.append("\nYoqish/o'chirish: /strategy <nom> on|off (masalan /strategy cloud off), hammasi: /strategy all on")
    lines.append(f"Min tasdiq (jami): {cfg.get('min_confirmations')} | Min RR: {cfg.get('min_rr')}")
    return "\n".join(lines)


def status_text(cfg):
    m = "Barcha sessiyalar (24/7)" if cfg.get("all_sessions", True) else "Killzone (07-10, 12-16 UTC)"
    return (
        f"📊 Holat\n• Juftliklar: {', '.join(PAIRS)}\n"
        f"• Min RR: {cfg.get('min_rr')}R | Tasdiq: {cfg.get('min_confirmations')} ta\n"
        f"• Rejim: {m}\n"
        f"• Strategiyalar: {on_names(cfg)}\n"
        f"• Indikator tasdiqlari: {'yoqiq' if cfg.get('indicators', True) else 'o`chiq'}\n"
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
            f"• {x.get('pair')} {x.get('side')} | Entry: {x.get('entry')} | SL: {x.get('sl')} | TP: {x.get('tp')} ({x.get('rr')}R) | {be}\n   Asos: {' + '.join(x.get('strategies', [])) or '-'} ({x.get('confirmations', '?')} tasdiq){' ⭐' if x.get('strong') else ''}"
        )
    return "\n".join(lines)


def strat_stats_text(cfg):
    ss = cfg.get("strat_stats", {})
    if not ss:
        return "\n\n🧠 Strategiyalar bo'yicha: hali yopilgan signal yo'q."
    lines = ["\n\n🧠 Strategiyalar bo'yicha (butun davr)"]
    for name in [k.upper() for k in ALL_KEYS]:
        st = ss.get(name)
        if not st:
            lines.append(f"• {name}: signal yo'q")
            continue
        n = st["tp"] + st["sl"] + st["be"]
        dec = st["tp"] + st["sl"]
        wr = f"{st['tp'] / dec * 100:.0f}%" if dec else "-"
        lines.append(f"• {name}: {n} ta | +{st['tp']} TP | {st['be']} BE | -{st['sl']} SL | {st['r']:+.2f}R | WR {wr}")
    lines.append("(birlashgan signal har bir ishtirokchi strategiyaga yoziladi; WR = TP/(TP+SL))")
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
        lines.append(f"• {x['pair']} — {x['outcome']}: {float(x['r']):+.2f}R {x.get('strat', '')}".rstrip())
    return "\n".join(lines) + strat_stats_text(cfg)


def read_self():
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as f:
        return f.read()


def handle_updates(cfg, last_id):
    global CUR_UPD
    q = {"timeout": 1, "allowed_updates": json.dumps(["message"])}
    if last_id is not None:
        q["offset"] = last_id + 1
    data = get_json(f"https://api.telegram.org/bot{TOKEN}/getUpdates?" + urllib.parse.urlencode(q))
    if not data.get("ok"):
        return last_id
    for item in data.get("result", []):
        if isinstance(item.get("update_id"), int):
            last_id = item["update_id"]
            CUR_UPD = last_id
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
                    f"🛠 ADMIN PANEL\n\n{status_text(cfg)}\n\nBuyruqlar:\n/set_rr 2.0\n/set_conf 3\n/ind on|off\n/strategies\n/strategy <nom|all> on|off\n/sessions all | killzone\n/signals\n/clear_signals\n/reset_r\n/get_code\n/set_code <kod> (yoki .py fayl)\n/add_func <kod>\n/replace\neski\n---\nyangi\n/rollback",
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
        elif low.startswith("/ind") and admin:
            parts = low.split()
            if len(parts) == 2 and parts[1] in ("on", "off"):
                cfg["indicators"] = (parts[1] == "on")
                save_cfg(cfg)
                send_msg(cid, f"✅ Indikator tasdiqlari: {parts[1]}", KB)
            else:
                send_msg(cid, "Namuna: /ind on yoki /ind off", KB)
        elif low.startswith("/strategies") and admin:
            send_msg(cid, strategies_text(cfg), KB)
        elif low.startswith("/strategy") and admin:
            parts = low.split()
            if len(parts) == 3 and parts[1] in ALL_KEYS + ["all"] and parts[2] in ("on", "off"):
                keys = ALL_KEYS if parts[1] == "all" else [parts[1]]
                for k in keys:
                    cfg.setdefault("strategies", {})[k] = (parts[2] == "on")
                save_cfg(cfg)
                send_msg(cid, strategies_text(cfg), KB)
            else:
                send_msg(cid, "Namuna: /strategy snr on | /strategy cloud off | /strategy all on", KB)
        elif low.startswith("/signals") and admin:
            send_msg(cid, signals_text(cfg), KB)
        elif low.startswith("/clear_signals") and admin:
            n = len(cfg.get("active_signals", []))
            cfg["active_signals"] = []
            save_cfg(cfg)
            send_msg(cid, f"✅ {n} ta aktiv signal tozalandi.", KB)
        elif low.startswith("/reset_r") and admin:
            cfg["total_r"], cfg["closed_results"], cfg["strat_stats"] = 0.0, [], {}
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
    # ishga tushganda eski (navbatda turgan) xabarlarni o'tkazib yuboramiz
    last_id, last_scan = None, 0.0
    d = get_json(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset=-1&timeout=0")
    res = d.get("result") or []
    if res and isinstance(res[-1].get("update_id"), int):
        last_id = res[-1]["update_id"]
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
