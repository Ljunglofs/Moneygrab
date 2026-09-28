"""
GRABIT MARKET EXTRAS  ·  market_extras.py
-----------------------------------------
Det bästa från Investing och TipRanks, byggt på gratis data:

  · Fed-räntekoll      sannolikheter för nästa Fed-beslut (Polymarket)
  · Rapporthistorik    slog/missade de senaste rapporterna + kursreaktionen (Yahoo)
  · Rapporter i går    utfall mot förväntan för gårdagens rapporter + dagens reaktion
  · Analytiker         upp-/nedgraderingar och riktkursändringar (Yahoo)
  · Finansiell hälsa   betyg A–F på lönsamhet, tillväxt, skuld, kassaflöde (Yahoo)
  · Community-röst     bullish/bearish per aktie och dag + mest bevakade i GRABIT
  · Portföljanalys     sektorer, beta, koncentration och samvariation
  · Listor             nya 52v-toppar och volymexplosion ur skanningen

Varje del gömmer sig om datan saknas och får aldrig fälla appen.
"""

import os
import re
import json
import time
import hashlib
import threading
import datetime as _dt

import daily_hub as H

try:
    import requests
except Exception:          # pragma: no cover
    requests = None

_VOTE_FILE = os.path.join(H.DATA_DIR, "votes.json")
_VLOCK = threading.Lock()


def _A():
    return H._A()


def _yf():
    try:
        import yfinance as yf
        return yf
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Fed-räntekoll (Polymarket, gratis och utan nyckel)
# ---------------------------------------------------------------------------
def _label_fed(q):
    s = (q or "").lower()
    if "no change" in s or "unchanged" in s or "pause" in s:
        return "HOLD"
    bps = re.search(r"(\d+)\+?\s*bps?", s)
    n = int(bps.group(1)) if bps else 25
    if "increase" in s or "hike" in s or "raise" in s:
        return "HIKE%d" % n
    if "decrease" in s or "cut" in s or "lower" in s:
        return "CUT%d%s" % (n, "+" if "+" in s else "")
    return None


def _compute_fed():
    if requests is None:
        return None
    try:
        r = requests.get("https://gamma-api.polymarket.com/events",
                         params={"closed": "false", "active": "true", "limit": 300,
                                 "order": "volume24hr", "ascending": "false"},
                         headers={"User-Agent": "grabit/1.0"}, timeout=12)
        evs = r.json() if r.status_code == 200 else []
    except Exception:
        return None
    best = None
    for e in evs or []:
        t = (e.get("title") or "")
        if not re.search(r"\bfed\b.*\b(decision|interest rate|rates?)\b", t, re.I):
            continue
        if re.search(r"how many|by (the )?end|emergency|cuts? in 20", t, re.I):
            continue
        end = e.get("endDate") or ""
        if best is None or end < (best.get("endDate") or "9999"):
            best = e
    if not best:
        return None
    opts = []
    for m in best.get("markets") or []:
        try:
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices or "[]")
            yes = float(prices[0]) if prices else None
        except Exception:
            yes = None
        lab = _label_fed(m.get("question") or m.get("groupItemTitle") or "")
        if yes is None or not lab or m.get("closed"):
            continue
        opts.append({"k": lab, "pct": round(yes * 100)})
    if not opts:
        return None
    opts.sort(key=lambda o: -o["pct"])
    return {"title": best.get("title"), "date": (best.get("endDate") or "")[:10],
            "options": opts[:4], "src": "Polymarket"}


def fed():
    return H._memo("fed", 900, _compute_fed)


# ---------------------------------------------------------------------------
#  Rapporthistorik: slog/missade + kursreaktion dagen efter
# ---------------------------------------------------------------------------
def _compute_earnhist(tk):
    yf = _yf()
    if yf is None:
        return {}
    t = yf.Ticker(tk)
    try:
        ed = t.get_earnings_dates(limit=16)
    except Exception:
        try:
            ed = t.earnings_dates
        except Exception:
            ed = None
    if ed is None or not len(ed):
        return {}
    try:
        px = t.history(period="4y", interval="1d", auto_adjust=False)["Close"].dropna()
        px.index = [d.date() for d in px.index]
    except Exception:
        px = None
    rows = []
    for idx, r in ed.iterrows():
        rep = H._num(r.get("Reported EPS"))
        est = H._num(r.get("EPS Estimate"))
        if rep is None:
            continue
        try:
            ts = idx.to_pydatetime()
            if ts.tzinfo and H._ET:
                ts = ts.astimezone(H._ET)
        except Exception:
            continue
        d = ts.date()
        surp = H._num(r.get("Surprise(%)"))
        if surp is None and est not in (None, 0):
            surp = (rep - est) / abs(est) * 100
        move = None
        if px is not None and len(px):
            dates = list(px.index)
            try:
                # Före öppning (före 12 ET): reaktionen är samma dag. Efter stängning: dagen efter.
                if ts.hour < 12:
                    after = next(i for i, x in enumerate(dates) if x >= d)
                else:
                    after = next(i for i, x in enumerate(dates) if x > d)
                if after >= 1:
                    move = round((float(px.iloc[after]) / float(px.iloc[after - 1]) - 1) * 100, 1)
            except StopIteration:
                move = None
        rows.append({"date": d.isoformat(), "est": est, "eps": rep,
                     "surprise": round(surp, 1) if surp is not None else None,
                     "beat": (rep >= est) if est is not None else None, "move": move})
        if len(rows) >= 8:
            break
    if not rows:
        return {}
    beats = sum(1 for x in rows if x["beat"])
    known = sum(1 for x in rows if x["beat"] is not None)
    moves = [abs(x["move"]) for x in rows if x["move"] is not None]
    return {"items": rows, "beats": beats, "n": known,
            "avg_move": round(sum(moves) / len(moves), 1) if moves else None}


def earnhist(tk):
    tk = (tk or "").upper().strip()
    if not re.match(r"^[A-Z0-9.\-]{1,10}$", tk):
        return {}
    return H._memo(("earnhist", tk), 12 * 3600, _compute_earnhist, tk)


# ---------------------------------------------------------------------------
#  Rapporter i går: loggar dagens rapportörer, visar utfallet nästa handelsdag
# ---------------------------------------------------------------------------
def _log_reporters():
    st = H._state()
    today = H.now_et().date().isoformat()
    log = st.setdefault("earn_log", {})
    if today in log:
        return
    tks = [e["tkr"] for e in H._earnings_on(0)][:10]
    if not tks:
        return
    log[today] = tks
    for k in sorted(log)[:-6]:
        log.pop(k, None)
    H._state_save(st)


def _compute_reported():
    st = H._state()
    log = st.get("earn_log") or {}
    today = H.now_et().date().isoformat()
    prev = [k for k in sorted(log) if k < today]
    if not prev:
        return []
    day = prev[-1]
    tks = log.get(day) or []
    snaps = H.snapshots(tks)
    out = []
    for tk in tks:
        h = earnhist(tk)
        last = (h.get("items") or [None])[0]
        if not last or last.get("date") < day:
            continue
        m = H.move_from_snapshot(snaps.get(tk) or {}) if snaps.get(tk) else None
        out.append({"tkr": tk, "surprise": last.get("surprise"), "beat": last.get("beat"),
                    "move": (m or {}).get("chg") if m and m.get("fresh") else last.get("move")})
    return out


def reported():
    return H._memo("reported", 900, _compute_reported)


# ---------------------------------------------------------------------------
#  Analytiker: upp-/nedgraderingar och riktkurser (Yahoo, gratis)
# ---------------------------------------------------------------------------
_BIG = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX", "PLTR",
        "JPM", "LLY", "COST", "CRM", "ORCL", "ADBE", "INTC", "MU", "QCOM", "SMCI", "COIN", "UBER",
        "SHOP", "PYPL", "BA", "DIS", "NKE", "WMT", "XOM"]
_RATINGS = {"items": [], "t": 0.0}
_ACT_SV = {"up": "höjer", "down": "sänker", "init": "inleder", "main": "behåller", "reit": "upprepar"}


def _scan_ratings():
    yf = _yf()
    if yf is None:
        return
    try:
        import push_notify as PN
        watch = PN.all_watch_tickers()[:25]
    except Exception:
        watch = []
    names = list(dict.fromkeys(_BIG + watch))
    cut = (H.now_et() - _dt.timedelta(days=4)).date()
    found = []
    for tk in names:
        try:
            df = yf.Ticker(tk).upgrades_downgrades
        except Exception:
            df = None
        if df is None or not len(df):
            continue
        for idx, r in df.head(8).iterrows():
            try:
                d = idx.date() if hasattr(idx, "date") else _dt.date.fromisoformat(str(idx)[:10])
            except Exception:
                continue
            if d < cut:
                continue
            act = str(r.get("Action") or "").lower()
            pta = str(r.get("priceTargetAction") or "")
            if act not in ("up", "down", "init") and pta not in ("Raises", "Lowers"):
                continue
            found.append({"tkr": tk, "date": d.isoformat(), "firm": str(r.get("Firm") or ""),
                          "action": act, "to": str(r.get("ToGrade") or ""),
                          "from": str(r.get("FromGrade") or ""), "pt_action": pta,
                          "pt": H._num(r.get("currentPriceTarget")),
                          "pt_prev": H._num(r.get("priorPriceTarget"))})
        time.sleep(0.4)
    order = {"up": 0, "down": 1, "init": 2}
    found.sort(key=lambda x: (x["date"], -order.get(x["action"], 3)), reverse=True)
    _RATINGS["items"] = found[:60]
    _RATINGS["t"] = time.time()
    print("[extras] analytiker: %d ändringar senaste dagarna" % len(found))


# ---------------------------------------------------------------------------
#  Finansiell hälsa A–F
# ---------------------------------------------------------------------------
def _grade(v, steps):
    """steps = [(tröskel, poäng), ...] i fallande ordning; högsta som klaras vinner."""
    if v is None:
        return None
    for th, pts in steps:
        if v >= th:
            return pts
    return 0


def _compute_health(tk):
    yf = _yf()
    if yf is None:
        return {}
    try:
        info = yf.Ticker(tk).info or {}
    except Exception:
        return {}
    g = H._num
    de = g(info.get("debtToEquity"))
    parts = {
        "profit": _grade(g(info.get("profitMargins")), [(0.2, 4), (0.1, 3), (0.03, 2), (0.0, 1)]),
        "growth": _grade(g(info.get("revenueGrowth")), [(0.25, 4), (0.1, 3), (0.03, 2), (0.0, 1)]),
        "debt": (None if de is None else (4 if de < 30 else 3 if de < 80 else 2 if de < 150 else 1 if de < 300 else 0)),
        "cash": (None if g(info.get("freeCashflow")) is None else (4 if g(info.get("freeCashflow")) > 0 else 0)),
        "liquidity": _grade(g(info.get("currentRatio")), [(2.0, 4), (1.5, 3), (1.0, 2), (0.7, 1)]),
        "roe": _grade(g(info.get("returnOnEquity")), [(0.2, 4), (0.12, 3), (0.05, 2), (0.0, 1)]),
    }
    got = [v for v in parts.values() if v is not None]
    if len(got) < 3:
        return {}
    avg = sum(got) / len(got)
    grade = "A" if avg >= 3.4 else "B" if avg >= 2.7 else "C" if avg >= 2.0 else "D" if avg >= 1.3 else "E" if avg >= 0.7 else "F"
    val = {"pe": g(info.get("trailingPE")), "fpe": g(info.get("forwardPE")),
           "ps": g(info.get("priceToSalesTrailing12Months")), "peg": g(info.get("trailingPegRatio")) or g(info.get("pegRatio"))}
    return {"grade": grade, "score": round(avg, 2), "parts": parts,
            "raw": {"margin": g(info.get("profitMargins")), "growth": g(info.get("revenueGrowth")),
                    "de": de, "fcf": g(info.get("freeCashflow")), "cr": g(info.get("currentRatio")),
                    "roe": g(info.get("returnOnEquity"))},
            "valuation": {k: (round(v, 1) if v is not None else None) for k, v in val.items()}}


def health(tk):
    tk = (tk or "").upper().strip()
    if not re.match(r"^[A-Z0-9.\-]{1,10}$", tk):
        return {}
    return H._memo(("health", tk), 12 * 3600, _compute_health, tk)


# ---------------------------------------------------------------------------
#  Community-röst + mest bevakade
# ---------------------------------------------------------------------------
_RATE = {}


def _votes_load():
    try:
        with open(_VOTE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _votes_save(v):
    try:
        tmp = _VOTE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(v, f)
        os.replace(tmp, _VOTE_FILE)
    except Exception as e:
        print("[extras] röster:", e)


def vote_get(tk, voter=""):
    tk = (tk or "").upper()
    day = H.now_et().date().isoformat()
    with _VLOCK:
        v = _votes_load().get(day, {}).get(tk) or {}
    b, s = int(v.get("bull", 0)), int(v.get("bear", 0))
    hv = hashlib.sha256(("v:" + voter).encode()).hexdigest()[:12] if voter else ""
    mine = (v.get("who") or {}).get(hv) if hv else None
    return {"tkr": tk, "bull": b, "bear": s, "n": b + s,
            "bull_pct": round(b / (b + s) * 100) if b + s else None, "mine": mine}


def vote_cast(tk, side, voter, ip=""):
    tk = (tk or "").upper()
    if side not in ("bull", "bear") or not voter or not re.match(r"^[A-Z0-9.\-]{1,10}$", tk):
        return {"ok": False}
    now = time.time()
    k = ip or voter
    hits = [t for t in _RATE.get(k, []) if now - t < 3600]
    if len(hits) >= 60:
        return {"ok": False, "fel": "för många röster"}
    hits.append(now)
    _RATE[k] = hits
    if len(_RATE) > 5000:
        _RATE.clear()
    day = H.now_et().date().isoformat()
    hv = hashlib.sha256(("v:" + voter).encode()).hexdigest()[:12]
    with _VLOCK:
        allv = _votes_load()
        for d in sorted(allv)[:-3]:
            allv.pop(d, None)
        v = allv.setdefault(day, {}).setdefault(tk, {"bull": 0, "bear": 0, "who": {}})
        prev = v["who"].get(hv)
        if prev == side:
            pass
        else:
            if prev in ("bull", "bear"):
                v[prev] = max(0, v[prev] - 1)
            v[side] += 1
            v["who"][hv] = side
        _votes_save(allv)
    r = vote_get(tk, voter)
    r["ok"] = True
    return r


def popular():
    """Mest bevakade aktier i GRABIT (prenumeranters bevakning) + mest röstade i dag."""
    cnt = {}
    try:
        import push_notify as PN
        with PN._lock:
            subs = PN._load()
        for s in subs:
            for t in s.get("_tickers") or []:
                cnt[t] = cnt.get(t, 0) + 1
    except Exception:
        pass
    day = H.now_et().date().isoformat()
    with _VLOCK:
        today = _votes_load().get(day, {})
    rows = {r["ticker"]: r for r in H._scan_rows()}
    keys = set(cnt) | set(today)
    out = []
    for t in keys:
        v = today.get(t) or {}
        b, s = int(v.get("bull", 0)), int(v.get("bear", 0))
        out.append({"tkr": t, "watchers": cnt.get(t, 0), "votes": b + s,
                    "bull_pct": round(b / (b + s) * 100) if b + s else None,
                    "score": (rows.get(t) or {}).get("score10")})
    out.sort(key=lambda x: -(x["watchers"] * 2 + x["votes"]))
    # Visas bara när det finns riktig aktivitet — aldrig uppfyllt med påhittade siffror.
    return [o for o in out if o["watchers"] + o["votes"] >= 2][:20]


# ---------------------------------------------------------------------------
#  Portföljanalys
# ---------------------------------------------------------------------------
def pfstats(holdings):
    A = _A()
    hs = []
    for h in (holdings or [])[:25]:
        tk = str(h.get("tkr") or "").upper().strip()
        q = H._num(h.get("qty"), 0) or 0
        if tk and q > 0 and re.match(r"^[A-Z0-9.\-^=]{1,12}$", tk):
            hs.append((tk, q))
    if not hs:
        return {}
    import pandas as pd
    closes, pos = {}, []
    for tk, q in hs:
        try:
            df = A._fetch_daily(tk)
            c = df["Close"].dropna() if df is not None else None
        except Exception:
            c = None
        if c is None or len(c) < 30:
            continue
        closes[tk] = c.iloc[-90:]
        try:
            info = A.company_info(tk) or {}
        except Exception:
            info = {}
        pos.append({"tkr": tk, "value": float(c.iloc[-1]) * q,
                    "sector": info.get("sector") or "Övrigt", "beta": H._num(info.get("beta"))})
    tot = sum(p["value"] for p in pos)
    if not tot:
        return {}
    sectors = {}
    for p in pos:
        p["w"] = p["value"] / tot
        sectors[p["sector"]] = sectors.get(p["sector"], 0) + p["w"]
    betas = [(p["beta"], p["w"]) for p in pos if p["beta"] is not None]
    bw = sum(w for _, w in betas)
    beta = round(sum(b * w for b, w in betas) / bw, 2) if bw else None
    corr = None
    if len(closes) >= 2:
        try:
            rets = pd.DataFrame(closes).pct_change().dropna(how="all")
            cm = rets.corr().values
            n = cm.shape[0]
            vals = [cm[i][j] for i in range(n) for j in range(i + 1, n) if cm[i][j] == cm[i][j]]
            corr = round(float(sum(vals) / len(vals)), 2) if vals else None
        except Exception:
            corr = None
    top = max(pos, key=lambda p: p["w"])
    return {"sectors": sorted([{"name": k, "w": round(v * 100, 1)} for k, v in sectors.items()],
                              key=lambda x: -x["w"]),
            "beta": beta, "corr": corr, "n": len(pos),
            "top": {"tkr": top["tkr"], "w": round(top["w"] * 100, 1)}}


# ---------------------------------------------------------------------------
#  Listor ur skanningen
# ---------------------------------------------------------------------------
def lists(kind):
    rows = [r for r in H._scan_rows() if H._num(r.get("last"), 0) >= 2]
    if kind == "highs":
        sel = [r for r in rows if H._num(r.get("pct_from_high"), -100) >= -0.5]
        sel.sort(key=lambda r: -(r.get("rs_rating") or 0))
    elif kind == "volume":
        sel = [r for r in rows if H._num(r.get("rel_vol"), 0) >= 2.5]
        sel.sort(key=lambda r: -H._num(r.get("rel_vol"), 0))
    else:
        return []
    return [{"tkr": r["ticker"], "name": r.get("name") or "", "last": r.get("last"),
             "ret_1": r.get("ret_1"), "rel_vol": r.get("rel_vol"), "rs": r.get("rs_rating"),
             "score": r.get("score10")} for r in sel[:40]]


# ---------------------------------------------------------------------------
#  Bakgrund + routes
# ---------------------------------------------------------------------------
def _loop():
    time.sleep(420)
    last_rat = 0.0
    while True:
        try:
            if H.now_et().weekday() < 5 and H.session() in ("pre", "open", "post"):
                _log_reporters()
        except Exception as e:
            print("[extras] rapportlogg:", e)
        try:
            if time.time() - last_rat > 3 * 3600:
                last_rat = time.time()
                _scan_ratings()
        except Exception as e:
            print("[extras] analytiker:", e)
        time.sleep(300)


def register(app):
    from fastapi import Request

    def _pro(token):
        return H._is_pro(token)

    @app.get("/api/hub/fed")
    def _x_fed():
        try:
            return {"fed": fed()}
        except Exception:
            return {"fed": None}

    @app.get("/api/hub/earnhist/{ticker}")
    def _x_earn(ticker: str):
        try:
            return earnhist(ticker)
        except Exception as e:
            print("[extras] earnhist %s: %s" % (ticker, e))
            return {}

    @app.get("/api/hub/reported")
    def _x_rep():
        try:
            return {"items": reported()}
        except Exception:
            return {"items": []}

    @app.get("/api/hub/ratings")
    def _x_rat(token: str = "", ticker: str = ""):
        items = _RATINGS["items"]
        if ticker:
            items = [i for i in items if i["tkr"] == ticker.upper()]
        pro = _pro(token)
        return {"items": items if pro else items[:2], "total": len(items), "pro": pro}

    @app.get("/api/hub/health/{ticker}")
    def _x_health(ticker: str):
        try:
            return health(ticker)
        except Exception as e:
            print("[extras] health %s: %s" % (ticker, e))
            return {}

    @app.get("/api/hub/vote/{ticker}")
    def _x_vget(ticker: str, voter: str = ""):
        return vote_get(ticker, voter[:64])

    @app.post("/api/hub/vote")
    async def _x_vpost(request: Request):
        try:
            b = await request.json()
        except Exception:
            b = {}
        ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        return vote_cast(b.get("tkr"), b.get("side"), str(b.get("voter") or "")[:64], ip)

    @app.get("/api/hub/popular")
    def _x_pop(token: str = ""):
        items = popular()
        pro = _pro(token)
        return {"items": items if pro else items[:2], "total": len(items), "pro": pro}

    @app.post("/api/hub/pfstats")
    async def _x_pf(request: Request):
        try:
            b = await request.json()
            return pfstats(b.get("holdings") or [])
        except Exception as e:
            print("[extras] pfstats:", e)
            return {}

    @app.get("/api/hub/lists")
    def _x_lists(k: str = "highs", token: str = ""):
        items = lists(k)
        pro = _pro(token)
        return {"items": items if pro else items[:2], "total": len(items), "pro": pro}

    @app.on_event("startup")
    def _x_start():
        threading.Thread(target=_loop, daemon=True).start()

    print("[extras] Market extras registrerad (Fed, rapporter, analytiker, hälsa, röster, portfölj, listor)")
