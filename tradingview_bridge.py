"""
TRADINGVIEW BRIDGE  ·  tradingview_bridge.py
---------------------------------------------
Kopplar TradingView till GRABIT. Ett TradingView-larm (alert) med webhook
POST:ar hit -> vi verifierar hemligheten, normaliserar signalen, sparar den
och skickar vidare till Telegram + Discord (samma kedja som roboten) och
push till appen. Appen visar flödet under Signaler -> TradingView.

Monteras av grabit_entry.py via  mount(app)  — api.py rörs inte.

Endpoints (tvåsegments-vägar så api.py:s catch-all /{fname} inte slukar dem):
    POST /tv/webhook?key=<TV_WEBHOOK_SECRET>   <- TradingView postar hit
    GET  /tv/signals?limit=30                  <- appens flöde (publik)
    GET  /tv/status                            <- hälsokoll (hemlighet satt? antal? senaste?)
    GET  /tv/test?key=<ROBBER_ADMIN_KEY>       <- kör en fejkad signal genom hela kedjan

Env-vars i Render:
    TV_WEBHOOK_SECRET   krävs — TradingView kan inte sätta headers, så hemligheten
                        skickas antingen i URL:en (?key=) eller som "secret" i JSON.
    TV_PUSH_DEFAULT     "watchlist" (default) | "all" | "none"  — vem får push.
    TV_IP_CHECK         "1" = släpp bara in TradingViews publicerade IP-adresser.
    DATA_DIR            persistent disk (samma som roboten) — tv_signals.jsonl.

Meddelandemall i TradingView (fliken "Message" på larmet):
    {"secret":"DIN_HEMLIGHET","ticker":"{{ticker}}","exchange":"{{exchange}}",
     "action":"buy","price":{{close}},"interval":"{{interval}}","time":"{{time}}",
     "note":"Breakout över {{plot_0}}"}
Ren text fungerar också — då blir hela texten "note" och vi gissar ticker/riktning.
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

from fastapi import HTTPException, Request

DATA_DIR = os.environ.get("DATA_DIR", ".")
SIGNALS_FILE = os.path.join(DATA_DIR, "tv_signals.jsonl")
MAX_KEEP = 500           # rader i filen
DEDUPE_SEC = 60          # identisk signal inom X s = ignorera
MAX_BODY = 16 * 1024     # TradingView-meddelanden är små

# TradingViews publicerade webhook-avsändare (https://www.tradingview.com/support/solutions/43000529348)
TV_IPS = {"52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"}

_lock = threading.Lock()
STATUS = {"received": 0, "rejected": 0, "duplicates": 0, "last_received": None,
          "last_error": None, "forwarded_telegram": 0, "forwarded_push": 0}

_ACTION_WORDS = {
    "buy": "LONG", "long": "LONG", "köp": "LONG", "kop": "LONG", "bull": "LONG",
    "sell": "SHORT", "short": "SHORT", "sälj": "SHORT", "salj": "SHORT", "bear": "SHORT",
    "close": "EXIT", "exit": "EXIT", "tp": "EXIT", "sl": "EXIT", "stopp": "EXIT",
    "stop": "EXIT", "alert": "INFO", "info": "INFO", "watch": "INFO", "bevaka": "INFO",
}


_TEXT_STOPWORDS = {"LONG", "SHORT", "BUY", "SELL", "CLOSE", "EXIT", "ALERT", "SL", "TP",
                   "TP1", "TP2", "VWAP", "RSI", "EMA", "SMA", "MACD", "ATR", "OB", "FVG",
                   "HTF", "LTF", "ORB", "BOS", "CHOCH", "USD", "SEK", "EUR", "OK", "TV"}


# ---------------------------------------------------------------- helpers
def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _secret():
    return os.environ.get("TV_WEBHOOK_SECRET", "").strip()


def _admin_key_ok(key):
    k = os.environ.get("ROBBER_ADMIN_KEY", "")
    return bool(k) and key == k


def _client_ip(request: Request):
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def grabit_ticker(ticker, exchange=""):
    """TradingView-symbol -> GRABIT-ticker. Spegelvänder appens tvSymbol():
       OMXSTO:VOLV_B -> VOLV-B.ST,  CAPITALCOM:US100 -> US100,
       OANDA:XAUUSD -> XAU,  NASDAQ:NVDA -> NVDA."""
    t = (ticker or "").strip().upper()
    ex = (exchange or "").strip().upper()
    if ":" in t and not ex:
        ex, t = t.split(":", 1)
    if not t:
        return ""
    if t in ("XAUUSD", "GOLD"):
        return "XAU"
    if t in ("US100", "NAS100", "USTEC", "NDX", "NQ1!"):
        return "US100"
    if ex in ("OMXSTO", "OMX", "STO") or t.endswith(".ST"):
        base = t[:-3] if t.endswith(".ST") else t
        return base.replace("_", "-") + ".ST"
    return t


def normalize(payload, raw_text=""):
    """Gör om godtyckligt TradingView-innehåll till en enhetlig signal."""
    p = payload if isinstance(payload, dict) else {}
    note = str(p.get("note") or p.get("message") or p.get("comment") or "").strip()
    if not p and raw_text:
        note = raw_text.strip()

    # Riktning: explicit fält, annars gissa ur texten
    act = str(p.get("action") or p.get("side") or p.get("signal") or "").strip().lower()
    side = _ACTION_WORDS.get(act)
    if not side:
        low = (act + " " + note).lower()
        for w, s in _ACTION_WORDS.items():
            if re.search(r"\b" + re.escape(w) + r"\b", low):
                side = s
                break
    side = side or "INFO"

    ticker = p.get("ticker") or p.get("symbol") or ""
    if not ticker and raw_text:
        # Första VERSAL-ordet som inte är ett handelsord: "US100 short vid VWAP" -> US100
        for m in re.finditer(r"\b([A-Z][A-Z0-9]{0,5}(?:[_\-][A-Z])?(?:\.ST)?)\b", raw_text):
            if m.group(1) not in _TEXT_STOPWORDS:
                ticker = m.group(1)
                break
    tkr = grabit_ticker(ticker, p.get("exchange", ""))

    def _num(v):
        try:
            return round(float(str(v).replace(",", ".")), 4)
        except Exception:
            return None

    sig = {
        "ts": _now_iso(),
        "tkr": tkr,
        "tv_symbol": (str(p.get("exchange", "")).upper() + ":" if p.get("exchange") else "")
                     + str(ticker).upper(),
        "side": side,
        "price": _num(p.get("price") or p.get("close")),
        "stop": _num(p.get("stop") or p.get("sl")),
        "target": _num(p.get("target") or p.get("tp") or p.get("tp1")),
        "interval": str(p.get("interval") or p.get("timeframe") or "")[:8],
        "strategy": str(p.get("strategy") or p.get("name") or "")[:60],
        "note": note[:400],
        "bar_time": str(p.get("time") or "")[:32],
        "push": str(p.get("push") or "").lower(),
        "source": "tradingview",
    }
    return sig


def _dedupe_key(sig):
    return json.dumps({k: sig.get(k) for k in ("tkr", "side", "price", "interval",
                                               "strategy", "note")}, sort_keys=True)


def _load(limit=None):
    rows = []
    try:
        with open(SIGNALS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    except Exception as e:
        STATUS["last_error"] = f"läsfel: {e}"
    if limit:
        rows = rows[-limit:]
    return rows


def _append(sig):
    with _lock:
        rows = _load()
        rows.append(sig)
        rows = rows[-MAX_KEEP:]
        tmp = SIGNALS_FILE + ".tmp"
        try:
            os.makedirs(os.path.dirname(SIGNALS_FILE) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp, SIGNALS_FILE)
        except Exception as e:
            STATUS["last_error"] = f"skrivfel: {e}"


def _is_duplicate(sig):
    key = _dedupe_key(sig)
    now = time.time()
    for r in reversed(_load(limit=20)):
        try:
            age = now - datetime.fromisoformat(r["ts"]).timestamp()
        except Exception:
            continue
        if age > DEDUPE_SEC:
            break
        if _dedupe_key(r) == key:
            return True
    return False


# ---------------------------------------------------------------- formatting
def _emoji(side):
    return {"LONG": "\U0001F4C8", "SHORT": "\U0001F4C9", "EXIT": "\U0001F3C1"}.get(side, "\U0001F514")


def format_telegram(sig):
    """HTML för Telegram (nasdaq_robber.send_discord strippar taggarna själv)."""
    head = f"{_emoji(sig['side'])} <b>TRADINGVIEW · {sig['side']}</b>"
    if sig.get("tkr"):
        head += f" – <b>{sig['tkr']}</b>"
    lines = [head]
    meta = []
    if sig.get("interval"):
        meta.append(f"{sig['interval']}m" if sig["interval"].isdigit() else sig["interval"])
    if sig.get("strategy"):
        meta.append(sig["strategy"])
    if meta:
        lines.append(" · ".join(meta))
    if sig.get("price") is not None:
        lines.append(f"Pris: <b>{sig['price']}</b>")
    tail = []
    if sig.get("stop") is not None:
        tail.append(f"SL: {sig['stop']}")
    if sig.get("target") is not None:
        tail.append(f"TP: {sig['target']}")
    if tail:
        lines.append(" | ".join(tail))
    if sig.get("note"):
        lines.append(sig["note"])
    lines.append("<i>Källa: ditt TradingView-larm</i>")
    return "\n".join(lines)


def format_push(sig):
    title = f"{_emoji(sig['side'])} TradingView · {sig['side']}"
    if sig.get("tkr"):
        title += f" · {sig['tkr']}"
    body = []
    if sig.get("price") is not None:
        body.append(f"Pris {sig['price']}")
    if sig.get("interval"):
        body.append(f"{sig['interval']}m" if sig["interval"].isdigit() else sig["interval"])
    if sig.get("note"):
        body.append(sig["note"])
    return title, " · ".join(body)[:180] or "Nytt larm från TradingView"


# ---------------------------------------------------------------- forwarding
def forward(sig):
    """Telegram + Discord via robotens notify(), push via push_notify."""
    out = {"telegram": None, "push": None}
    try:
        from nasdaq_robber import notify
        out["telegram"] = bool(notify(format_telegram(sig)))
        if out["telegram"]:
            STATUS["forwarded_telegram"] += 1
    except Exception as e:
        out["telegram"] = f"fel: {e}"

    mode = sig.get("push") or os.environ.get("TV_PUSH_DEFAULT", "watchlist").lower()
    if mode not in ("all", "watchlist", "none"):
        mode = "watchlist"
    if mode == "none":
        out["push"] = "avstängt"
        return out
    try:
        import push_notify as PN
        title, body = format_push(sig)
        url = "/?tkr=" + sig["tkr"] if sig.get("tkr") else "/"
        if mode == "all" or not sig.get("tkr"):
            r = PN.send_all(title, body, url=url, tag="tv-signal")
        else:
            r = PN.send_watchlist(sig["tkr"], title, body, url=url)
        out["push"] = r
        STATUS["forwarded_push"] += int((r or {}).get("skickade", 0) or 0)
    except Exception as e:
        out["push"] = f"fel: {e}"
    return out


def ingest(payload, raw_text="", forward_it=True):
    """Normalisera -> dedupe -> spara -> skicka vidare. Returnerar (signal, resultat)."""
    sig = normalize(payload, raw_text)
    if _is_duplicate(sig):
        STATUS["duplicates"] += 1
        return sig, {"duplicate": True}
    _append(sig)
    STATUS["received"] += 1
    STATUS["last_received"] = sig["ts"]
    res = forward(sig) if forward_it else {"forwarded": False}
    return sig, res


def public_view(sig):
    """Det appen får se — aldrig hemligheter."""
    return {k: sig.get(k) for k in ("ts", "tkr", "tv_symbol", "side", "price", "stop",
                                    "target", "interval", "strategy", "note", "bar_time")}


# ---------------------------------------------------------------- routes
def mount(app):
    @app.post("/tv/webhook")
    async def tv_webhook(request: Request, key: str = ""):
        secret = _secret()
        if not secret:
            STATUS["rejected"] += 1
            raise HTTPException(503, "TV_WEBHOOK_SECRET saknas i Render-miljön")

        if os.environ.get("TV_IP_CHECK", "") == "1":
            ip = _client_ip(request)
            if ip not in TV_IPS:
                STATUS["rejected"] += 1
                raise HTTPException(403, f"avsändare {ip} är inte TradingView")

        raw = (await request.body())[:MAX_BODY]
        text = raw.decode("utf-8", errors="replace")
        payload = None
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            payload = None

        supplied = key or (payload or {}).get("secret") or (payload or {}).get("key") or ""
        if str(supplied) != secret:
            STATUS["rejected"] += 1
            raise HTTPException(403, "fel eller saknad hemlighet (?key= eller \"secret\" i JSON)")

        if payload:
            payload = {k: v for k, v in payload.items() if k not in ("secret", "key")}
        sig, res = ingest(payload, raw_text="" if payload else text)
        return {"ok": True, "duplicate": bool(res.get("duplicate")),
                "signal": public_view(sig), "forwarded": res if not res.get("duplicate") else None}

    @app.get("/tv/signals")
    def tv_signals(limit: int = 30, tkr: str = ""):
        limit = max(1, min(int(limit or 30), 200))
        rows = _load()
        if tkr:
            t = tkr.strip().upper()
            rows = [r for r in rows if (r.get("tkr") or "").upper() == t]
        rows = [public_view(r) for r in reversed(rows[-limit:])]
        return {"ok": True, "count": len(rows), "signals": rows,
                "configured": bool(_secret())}

    @app.get("/tv/status")
    def tv_status():
        rows = _load()
        return {"ok": True,
                "configured": bool(_secret()),
                "webhook_url": "/tv/webhook?key=<TV_WEBHOOK_SECRET>",
                "push_default": os.environ.get("TV_PUSH_DEFAULT", "watchlist"),
                "ip_check": os.environ.get("TV_IP_CHECK", "") == "1",
                "stored": len(rows),
                "file": SIGNALS_FILE,
                "status": STATUS,
                "latest": public_view(rows[-1]) if rows else None,
                "hint": None if _secret() else
                        "Sätt TV_WEBHOOK_SECRET i Render -> Environment och deploya om."}

    @app.get("/tv/test")
    def tv_test(key: str = "", send: int = 1):
        """Kör en fejkad TradingView-signal genom hela kedjan. Kräver ROBBER_ADMIN_KEY."""
        if not _admin_key_ok(key):
            raise HTTPException(403, "fel eller saknad nyckel (sätt ROBBER_ADMIN_KEY i Render)")
        sample = {"ticker": "NVDA", "exchange": "NASDAQ", "action": "buy", "price": 512.34,
                  "interval": "60", "strategy": "TEST", "note": "Testsignal från /tv/test",
                  "push": "none" if not send else ""}
        sig, res = ingest(sample, forward_it=bool(send))
        return {"ok": True, "signal": public_view(sig), "forwarded": res}

    return app
