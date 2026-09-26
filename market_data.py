"""
GRABIT  ·  market_data.py
-------------------------
Hela USA-marknaden via Polygon.io.

Polygons "grouped daily" ger dagsdata (OHLCV) för VARJE amerikansk aktie i ett
enda anrop. Ett års historik är alltså ~260 anrop — en gång — och sedan ett
anrop per dag. Det är det snabbaste och billigaste sättet att skanna hela
marknaden (Yahoo per aktie tar timmar och spärrar moln-IP:n).

Datan sparas på disken (DATA_DIR/polygon/ÅÅÅÅ-MM-DD.json.gz) och överlever
omstarter. frames() bygger en DataFrame per aktie för de likvida vanliga
aktierna (pris >= MARKET_MIN_PRICE, snittomsättning >= MARKET_MIN_DVOL).

ENV
  POLYGON_API_KEY    nyckeln från polygon.io (utan nyckel är modulen avstängd)
  POLYGON_RPM        anrop per minut. Gratisplanen: 5 (standard). Betald plan: 0 = obegränsat
  POLYGON_BASE       API-adress (standard https://api.polygon.io)
  POLYGON_DAYS       handelsdagar att hålla (standard 270)
  MARKET_MIN_PRICE   lägsta aktiekurs för att skannas (standard 1)
  MARKET_MIN_DVOL    lägsta snittomsättning per dag i USD, 20 d (standard 2 000 000)
"""
import os
import json
import gzip
import time
import glob
import threading
import datetime as dt

import numpy as np
import pandas as pd

BASE = os.environ.get("POLYGON_BASE", "https://api.polygon.io").rstrip("/")
DIR = os.path.join(os.environ.get("DATA_DIR", "."), "polygon")
DAYS = int(os.environ.get("POLYGON_DAYS", "270"))
RPM = int(os.environ.get("POLYGON_RPM", "5"))
MIN_PRICE = float(os.environ.get("MARKET_MIN_PRICE", "1"))
MIN_DVOL = float(os.environ.get("MARKET_MIN_DVOL", "2e6"))

STATE = {"status": "off", "days": 0, "need": DAYS, "asof": None,
         "tickers": 0, "fetched": 0, "error": None, "updated": None}
_last_call = [0.0]
_lock = threading.Lock()


def key():
    return os.environ.get("POLYGON_API_KEY", "").strip()


def enabled():
    return bool(key())


def _get(path=None, params=None, url=None, _retry=2):
    import requests
    if RPM > 0:
        wait = 60.0 / RPM - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
    _last_call[0] = time.time()
    p = dict(params or {})
    p["apiKey"] = key()
    r = requests.get(url or (BASE + path), params=p, timeout=40)
    if r.status_code == 429 and _retry > 0:          # för många anrop: vänta och försök igen
        time.sleep(30)
        return _get(path, params, url, _retry - 1)
    if r.status_code in (401, 403):
        raise RuntimeError("Polygon nekade anropet (%s) — kontrollera POLYGON_API_KEY och planen" % r.status_code)
    r.raise_for_status()
    return r.json()


def _norm(t):
    """Polygon skriver klassaktier med punkt (BRK.B), Yahoo med bindestreck (BRK-B).
    Appen använder Yahoo-formatet överallt, så aktiekortet hittar samma aktie."""
    return t.replace(".", "-")


def _path(d):
    return os.path.join(DIR, d + ".json.gz")


def _ny_today():
    try:
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = dt.datetime.utcnow() - dt.timedelta(hours=4)
    return now


def _wanted_dates():
    """Vardagar bakåt från senaste färdiga handelsdag (helgdagar blir tomma filer)."""
    now = _ny_today()
    d = now.date()
    # Dagens data finns först en stund efter stängning.
    if not (now.hour >= 18 and d.weekday() < 5):
        d -= dt.timedelta(days=1)
    out = []
    need = int(DAYS * 1.06) + 12            # marginal för helgdagar
    while len(out) < need:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out


def _load(d):
    try:
        with gzip.open(_path(d), "rt") as f:
            return json.load(f).get("bars") or {}
    except Exception:
        return None


def _fetch_day(d):
    j = _get("/v2/aggs/grouped/locale/us/market/stocks/%s" % d, {"adjusted": "true"})
    bars = {}
    for x in (j.get("results") or []):
        t, c = x.get("T"), x.get("c")
        if not t or c is None:
            continue
        bars[_norm(t)] = [x.get("o"), x.get("h"), x.get("l"), c, x.get("v") or 0]
    tmp = _path(d) + ".tmp"
    with gzip.open(tmp, "wt") as f:
        json.dump({"d": d, "bars": bars, "fetched": time.time()}, f)
    os.replace(tmp, _path(d))
    return len(bars)


def sync():
    """Hämtar de dagar som saknas, nyast först så att fönstret fylls framifrån."""
    if not enabled():
        STATE["status"] = "off"
        return
    os.makedirs(DIR, exist_ok=True)
    wanted = _wanted_dates()
    now = time.time()
    missing = []
    for d in wanted:
        p = _path(d)
        if not os.path.exists(p):
            missing.append(d)
            continue
        # En tom fil från de senaste dagarna kan betyda att datan inte var klar — hämta igen.
        if os.path.getsize(p) < 200 and (dt.date.today() - dt.date.fromisoformat(d)).days <= 4 \
                and now - os.path.getmtime(p) > 3600:
            missing.append(d)
    STATE["status"] = "syncing" if missing else "ready"
    for i, d in enumerate(missing):
        try:
            _fetch_day(d)
            STATE["fetched"] += 1
        except Exception as e:
            STATE["error"] = str(e)[:200]
            print("[market] %s: %s" % (d, e))
            if "nekade" in str(e):
                STATE["status"] = "error"
                return
        STATE["days"] = sum(1 for x in wanted if os.path.exists(_path(x)))
    # Städa bort filer som fallit ur fönstret.
    keep = set(wanted)
    for f in glob.glob(os.path.join(DIR, "*.json.gz")):
        if os.path.basename(f)[:10] not in keep:
            try:
                os.remove(f)
            except Exception:
                pass
    STATE["days"] = sum(1 for x in wanted if os.path.exists(_path(x)))
    STATE["status"] = "ready"
    STATE["updated"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())


# ---- Referenslista: vanliga aktier + ADR (inte ETF:er, warranter, units) ----
_NAMES_FILE = lambda: os.path.join(DIR, "names.json")  # noqa: E731


def names():
    """{ticker: namn} för vanliga aktier och ADR:er. Uppdateras en gång i veckan."""
    try:
        with open(_NAMES_FILE()) as f:
            j = json.load(f)
        if time.time() - j.get("ts", 0) < 7 * 86400 and j.get("names"):
            return j["names"]
    except Exception:
        j = None
    if not enabled():
        return (j or {}).get("names") or {}
    out = {}
    try:
        for typ in ("CS", "ADRC"):
            url, params = None, {"market": "stocks", "type": typ, "active": "true", "limit": 1000}
            path = "/v3/reference/tickers"
            for _ in range(20):
                r = _get(path, params, url)
                for x in r.get("results") or []:
                    if x.get("ticker"):
                        out[_norm(x["ticker"])] = x.get("name") or x["ticker"]
                url = r.get("next_url")
                if not url:
                    break
                path, params = None, {}
        os.makedirs(DIR, exist_ok=True)
        with open(_NAMES_FILE(), "w") as f:
            json.dump({"ts": time.time(), "names": out}, f)
    except Exception as e:
        print("[market] referenslistan:", e)
        return (j or {}).get("names") or out
    return out


def frames(min_days=210):
    """(dict ticker -> OHLCV-DataFrame, SPY-stängningar, senaste datum) för de
    likvida vanliga aktierna. None om historiken inte räcker än."""
    files = sorted(glob.glob(os.path.join(DIR, "*.json.gz")))
    days = []
    for f in files:
        b = _load(os.path.basename(f)[:10])
        if b:                                  # hoppa över helgdagar/tomma
            days.append((os.path.basename(f)[:10], b))
    days = days[-DAYS:]
    if len(days) < min_days:
        return None
    nm = names()
    last20 = days[-20:]
    last = days[-1][1]
    elig = []
    for t, bar in last.items():
        if nm and t not in nm:
            continue
        if not nm and (not t.isupper() or len(t) > 5):
            continue
        c = bar[3] or 0
        if c < MIN_PRICE:
            continue
        dv = [(b[t][3] or 0) * (b[t][4] or 0) for _, b in last20 if t in b]
        if len(dv) >= 15 and sum(dv) / len(dv) >= MIN_DVOL:
            elig.append(t)
    idx = pd.DatetimeIndex([d for d, _ in days])
    n = len(days)
    arr = {t: np.full((n, 5), np.nan, dtype=float) for t in elig + ["SPY"]}
    for i, (_, b) in enumerate(days):
        for t, a in arr.items():
            v = b.get(t)
            if v:
                a[i] = [np.nan if x is None else x for x in v]
    out = {}
    for t in elig:
        df = pd.DataFrame(arr[t], index=idx, columns=["Open", "High", "Low", "Close", "Volume"])
        df = df[df["Close"].notna()]
        if len(df) >= min_days:
            out[t] = df
    spy = pd.DataFrame(arr["SPY"], index=idx, columns=["Open", "High", "Low", "Close", "Volume"])["Close"].dropna()
    STATE["tickers"] = len(out)
    STATE["asof"] = days[-1][0]
    return out, spy, days[-1][0], nm
