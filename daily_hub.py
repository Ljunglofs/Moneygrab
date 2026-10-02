"""
GRABIT DAILY HUB  ·  daily_hub.py
---------------------------------
Det som får folk att öppna appen på fasta tider varje dag:

  · Före öppning   terminer, premarket-rörelser, dagens händelser, GEX-avstånd
  · Öppningen      push 15 minuter in i sessionen: största rörelser + setups
  · Kvällsrapport  dagen i korthet, facit för morgonens setups, efterhandel,
                   vad som väntar i morgon (+ push efter stängning)
  · Makroutfall    faktiskt utfall mot förväntat, push när siffran kommer
  · Optionsflöden  ovanligt hög volym mot öppna kontrakt (CBOE, fördröjt)
  · Short squeeze  hög blankning + stigande volym + styrka
  · Smarta pengar  insider-/politikerköp i aktier som också har en setup
  · Teknisk mätare köp/neutral/sälj per tidsram (15m, 1h, dag, vecka)

Bara gratis källor: Alpaca (gratisnivån, IEX), Yahoo, CBOE:s fördröjda
optionsdata och FinancialJuice RSS. Varje del gömmer sig om datan saknas —
inget här får någonsin fälla appen, så allt är inlindat i try/except.
"""

import os
import re
import sys
import json
import time
import threading
import datetime as _dt

try:
    import requests
except Exception:          # pragma: no cover
    requests = None

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    _SE = ZoneInfo("Europe/Stockholm")
except Exception:          # pragma: no cover
    _ET = _SE = None

DATA_DIR = os.environ.get("DATA_DIR", ".")
_STATE_FILE = os.path.join(DATA_DIR, "daily_hub_state.json")
_LOCK = threading.Lock()


def _A():
    """Huvudmodulen (api). Hämtas lat så att importordningen inte spelar roll."""
    return sys.modules.get("api_v3") or sys.modules.get("api") or __import__("api")


# ---------------------------------------------------------------------------
#  Små hjälpare: tid, cache, state
# ---------------------------------------------------------------------------
def now_et():
    return _dt.datetime.now(_ET) if _ET else _dt.datetime.utcnow() - _dt.timedelta(hours=4)


def session(t=None):
    """pre 04:00–09:30 · open 09:30–16:00 · post 16:00–20:00 · closed (ET)."""
    t = t or now_et()
    if t.weekday() >= 5:
        return "closed"
    m = t.hour * 60 + t.minute
    if 240 <= m < 570:
        return "pre"
    if 570 <= m < 960:
        return "open"
    if 960 <= m < 1200:
        return "post"
    return "closed"


def _et_to_se(hhmm, day=None):
    """'08:30' ET -> '14:30' svensk tid (hanterar sommartid på båda sidor)."""
    try:
        d = day or now_et().date()
        h, m = [int(x) for x in hhmm.split(":")[:2]]
        et = _dt.datetime(d.year, d.month, d.day, h, m, tzinfo=_ET)
        return et.astimezone(_SE).strftime("%H:%M")
    except Exception:
        return ""


_MEMO = {}
_MEMO_BUSY = set()


def _memo(key, ttl, fn, *args):
    """Stale-while-revalidate: svarar alltid direkt med senaste värdet och
    räknar om i bakgrunden när det blivit gammalt. Första anropet räknas synkront."""
    now = time.time()
    hit = _MEMO.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if hit:
        if key not in _MEMO_BUSY:
            _MEMO_BUSY.add(key)

            def _run():
                try:
                    _MEMO[key] = (time.time(), fn(*args))
                except Exception as e:
                    print("[hub] %s: %s" % (key, e))
                finally:
                    _MEMO_BUSY.discard(key)
            threading.Thread(target=_run, daemon=True).start()
        return hit[1]
    val = fn(*args)
    _MEMO[key] = (now, val)
    # Taket på cachen: tekniska mätare per ticker får inte växa obegränsat.
    if len(_MEMO) > 400:
        for k, _v in sorted(_MEMO.items(), key=lambda kv: kv[1][0])[:100]:
            _MEMO.pop(k, None)
    return val


def _state():
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _state_save(st):
    try:
        tmp = "%s.%d.tmp" % (_STATE_FILE, threading.get_ident())
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, _STATE_FILE)
    except Exception as e:
        print("[hub] kunde inte spara state:", e)


def _num(v, default=None):
    try:
        v = float(v)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def _scan_rows():
    """Senaste skanningen utan att någonsin starta en ny synkront."""
    A = _A()
    for args in ((None,), ()):
        hit = A._CACHE.get(("scan_universe", args))
        if hit and hit[1]:
            return hit[1]
    return []


# ---------------------------------------------------------------------------
#  Alpaca (gratisnivån, IEX-flödet): snapshots, movers, nyheter
# ---------------------------------------------------------------------------
def _alp_keys():
    k = os.getenv("APCA_API_KEY_ID", "") or os.getenv("ALPACA_KEY", "")
    s = os.getenv("APCA_API_SECRET_KEY", "") or os.getenv("ALPACA_SECRET", "")
    return (k, s) if k and s else (None, None)


_ALP_LOGGED = {}


def _alp_get(path, params):
    k, s = _alp_keys()
    if not k or requests is None:
        return None
    try:
        r = requests.get("https://data.alpaca.markets" + path, params=params, timeout=12,
                         headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s})
        if r.status_code != 200:
            k2 = (path, r.status_code)
            if time.time() - _ALP_LOGGED.get(k2, 0) > 600:
                _ALP_LOGGED[k2] = time.time()
                print("[hub] alpaca %s -> %s %s" % (path, r.status_code, r.text[:120]))
            return None
        return r.json()
    except Exception as e:
        print("[hub] alpaca %s: %s" % (path, type(e).__name__))
        return None


def snapshots(symbols):
    """{SYM: snapshot} för upp till några hundra symboler (100 per anrop)."""
    out = {}
    syms = [s for s in dict.fromkeys(symbols or []) if s and re.match(r"^[A-Z.]{1,6}$", s)]
    for i in range(0, len(syms), 100):
        part = syms[i:i + 100]
        j = _alp_get("/v2/stocks/snapshots", {"symbols": ",".join(part), "feed": "iex"})
        if isinstance(j, dict):
            # Svaret är antingen {SYM: snap} eller {"snapshots": {...}}
            j = j.get("snapshots", j)
            for k, v in j.items():
                if isinstance(v, dict):
                    out[k] = v
    return out


def _bar_date(b):
    try:
        t = _dt.datetime.fromisoformat(str(b.get("t")).replace("Z", "+00:00"))
        return t.astimezone(_ET).date() if _ET else t.date()
    except Exception:
        return None


def move_from_snapshot(s, sess=None):
    """Rörelse för en symbol, tolkad efter sessionen.
    pre/closed: senaste affär mot senaste stängning
    open:       senaste affär mot gårdagens stängning (dagens rörelse)
    post:       senaste affär mot dagens stängning (efterhandel) + dagens rörelse"""
    sess = sess or session()
    today = now_et().date()
    lt = s.get("latestTrade") or {}
    db = s.get("dailyBar") or {}
    pdb = s.get("prevDailyBar") or {}
    px = _num(lt.get("p"))
    if px is None:
        return None
    db_today = _bar_date(db) == today
    last_close = _num(pdb.get("c")) if db_today else _num(db.get("c"))
    out = {"price": round(px, 2)}
    trade_day = _bar_date(lt)
    fresh = trade_day == today
    if sess == "post" and db_today:
        c = _num(db.get("c"))
        pc = _num(pdb.get("c"))
        if c and pc:
            out["day"] = round((c / pc - 1) * 100, 2)
        if c:
            out["chg"] = round((px / c - 1) * 100, 2)
            out["ref"] = "close"
    elif last_close:
        out["chg"] = round((px / last_close - 1) * 100, 2)
        out["ref"] = "prev"
    out["fresh"] = bool(fresh)
    return out if "chg" in out else None


def _movers_universe():
    """Likvida aktier ur skanningen: börsvärde över 300 MUSD och pris över $3."""
    rows = _scan_rows()
    big = [r for r in rows if _num(r.get("last"), 0) >= 3 and _num(r.get("mcap_musd"), 0) >= 300]
    big.sort(key=lambda r: -_num(r.get("mcap_musd"), 0))
    uni = [r["ticker"] for r in big[:700]]
    hot = sorted([r for r in rows if _num(r.get("last"), 0) >= 2],
                 key=lambda r: -_num(r.get("rel_vol"), 0))[:100]
    for r in hot:
        if r["ticker"] not in uni:
            uni.append(r["ticker"])
    if len(uni) < 50:
        uni = [r["ticker"] for r in rows if _num(r.get("last"), 0) >= 3][:800]
    return uni


def _news_for(symbols, hours=18):
    """Senaste rubrik per symbol (Alpaca-nyheter)."""
    if not symbols:
        return {}
    start = (_dt.datetime.utcnow() - _dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    j = _alp_get("/v1beta1/news", {"symbols": ",".join(symbols[:40]), "limit": 50,
                                    "sort": "desc", "start": start})
    out = {}
    for n in (j or {}).get("news") or []:
        h = (n.get("headline") or "").strip()
        for s in n.get("symbols") or []:
            if s in symbols and s not in out and h:
                out[s] = h[:140]
    return out


def _compute_movers(sess):
    rows = {r["ticker"]: r for r in _scan_rows()}
    snaps = snapshots(_movers_universe())
    items = []
    for sym, s in snaps.items():
        m = move_from_snapshot(s, sess)
        if not m or not m.get("fresh"):
            continue
        r = rows.get(sym) or {}
        items.append({"tkr": sym, "name": r.get("name") or "", "price": m["price"],
                      "chg": m["chg"], "day": m.get("day"), "score": r.get("score10"),
                      "theme": r.get("theme") or ""})
    # Alpacas egen lista över marknadens största rörelser fyller på (kan ge
    # aktier utanför skanningen). Bara aktier över $2.
    j = _alp_get("/v1beta1/screener/stocks/movers", {"top": 20})
    seen = {i["tkr"] for i in items}
    for side in ("gainers", "losers"):
        for g in (j or {}).get(side) or []:
            sym = g.get("symbol")
            px = _num(g.get("price"))
            pc = _num(g.get("percent_change"))
            if sym and sym not in seen and px and px >= 2 and pc is not None and sess != "post":
                items.append({"tkr": sym, "name": (rows.get(sym) or {}).get("name") or "",
                              "price": round(px, 2), "chg": round(pc, 2), "score": None, "theme": ""})
                seen.add(sym)
    up = sorted([i for i in items if i["chg"] > 0], key=lambda i: -i["chg"])[:8]
    dn = sorted([i for i in items if i["chg"] < 0], key=lambda i: i["chg"])[:8]
    heads = _news_for([i["tkr"] for i in up + dn])
    for i in up + dn:
        if i["tkr"] in heads:
            i["news"] = heads[i["tkr"]]
    return {"up": up, "down": dn, "n": len(items), "asof": now_et().strftime("%H:%M")}


def movers(sess=None):
    sess = sess or session()
    return _memo(("movers", sess), 150, _compute_movers, sess)


# ---------------------------------------------------------------------------
#  Marknadsöversikt: terminer och tillgångsklasser (Yahoo, via appens cache)
# ---------------------------------------------------------------------------
_MARKETS = [("NQ", "NQ=F", "Nasdaq-termin"), ("ES", "ES=F", "S&P-termin"),
            ("VIX", "^VIX", "Volatilitet"), ("GULD", "GC=F", "Guld"),
            ("OLJA", "CL=F", "Olja"), ("DXY", "DX-Y.NYB", "Dollar"),
            ("10Y", "^TNX", "Ränta 10 år"), ("BTC", "BTC-USD", "Bitcoin")]


def _compute_markets():
    A = _A()
    out = []
    for key, tk, name in _MARKETS:
        try:
            px, pct = A._index_quote(tk)
        except Exception:
            px, pct = None, 0.0
        if px is None:
            continue
        out.append({"k": key, "tk": tk, "name": name, "price": round(px, 2), "chg": pct})
    return out


def markets():
    return _memo("markets", 240, _compute_markets)


# ---------------------------------------------------------------------------
#  Dagens händelser: makro (med utfall), rapporter, FDA
# ---------------------------------------------------------------------------
def _events_today(day=None):
    A = _A()
    day = day or now_et().date()
    lbl = day.strftime("%d %b")
    out = []
    try:
        for e in A._macro_events() or []:
            if e.get("date") != lbl:
                continue
            t = e.get("time") or ""
            out.append({"time_et": t, "time_se": _et_to_se(t, day) if t else "",
                        "country": e.get("country") or "", "title": e.get("title") or "",
                        "impact": e.get("impact") or "", "forecast": e.get("forecast") or "",
                        "previous": e.get("previous") or ""})
    except Exception as e:
        print("[hub] makro:", e)
    merge_actuals(out)
    try:
        import flash_news
        out += flash_news.scheduled_on(day)
    except Exception as e:
        print("[hub] flash:", e)
    out.sort(key=lambda x: x.get("time_et") or "99")
    return out


def _earnings_on(days_ahead=0):
    rows = [r for r in _scan_rows() if r.get("earnings_in") == days_ahead]
    rows.sort(key=lambda r: -_num(r.get("mcap_musd"), 0))
    return [{"tkr": r["ticker"], "name": r.get("name") or "", "mcap": r.get("mcap_musd")}
            for r in rows[:10]]


# ---------------------------------------------------------------------------
#  Makroutfall: FinancialJuice skriver "X Actual 3.1% (Forecast 3.0%, Previous 2.9%)"
# ---------------------------------------------------------------------------
_FJ_RE = re.compile(
    r"^(?P<name>.+?)\s+Actual:?\s*(?P<act>[-+]?[\d.,]+\s?[%KMBT]?)\s*"
    r"(?:\((?:Forecast|Consensus|Expected|Est\.?):?\s*(?P<fc>[^,)]+)"
    r"(?:,\s*Previous:?\s*(?P<pv>[^)]+))?\))?", re.I)
_BIG_US = ("CPI", "PCE", "NONFARM", "NON-FARM", "PAYROLL", "GDP", "RETAIL SALES", "PPI",
           "UNEMPLOYMENT", "JOBLESS", "ISM", "FED", "FOMC", "INTEREST RATE", "JOLTS")
_ACT = {"items": [], "t": 0.0}


def _fetch_actuals():
    try:
        import feedparser
    except Exception:
        return []
    try:
        f = feedparser.parse("https://www.financialjuice.com/feed.ashx?xy=rss")
    except Exception:
        return []
    out = []
    for e in (f.entries or [])[:60]:
        title = re.sub(r"^FinancialJuice:\s*", "", (e.get("title") or "").strip())
        m = _FJ_RE.search(title)
        if not m:
            continue
        ts = None
        try:
            if e.get("published_parsed"):
                ts = time.mktime(e.published_parsed) - time.timezone
        except Exception:
            ts = None
        name = m.group("name").strip(" :-")
        out.append({"name": name, "actual": m.group("act").strip(),
                    "forecast": (m.group("fc") or "").strip(),
                    "previous": (m.group("pv") or "").strip(),
                    "t": ts or time.time(),
                    "us": bool(re.match(r"^(US|U\.S\.|United States)\b", name, re.I)),
                    "big": any(k in name.upper() for k in _BIG_US)})
    return out


def actuals():
    """Dagens utfall (senaste 16 timmarna)."""
    if time.time() - _ACT["t"] > 90:
        items = _fetch_actuals()
        if items:
            _ACT["items"] = items
        _ACT["t"] = time.time()
    cut = time.time() - 16 * 3600
    return [a for a in _ACT["items"] if a["t"] >= cut]


def _norm(s):
    s = s.lower().replace("y/y", "yoy").replace("m/m", "mom").replace("q/q", "qoq")
    s = re.sub(r"\b(us|u\.s\.|usd|the)\b", " ", s)
    return set(w for w in re.findall(r"[a-z0-9]+", s) if len(w) > 1)


def merge_actuals(events):
    """Sätter 'actual' på makrohändelser vars namn matchar ett publicerat utfall."""
    try:
        acts = actuals()
    except Exception:
        acts = []
    for e in events:
        want = _norm(e.get("title") or "")
        if not want:
            continue
        for a in acts:
            if want <= _norm(a["name"]):
                e["actual"] = a["actual"]
                if not e.get("forecast") and a.get("forecast"):
                    e["forecast"] = a["forecast"]
                break
    return events


# ---------------------------------------------------------------------------
#  GEX-avstånd för NQ (bara PRO — nivåerna lämnar aldrig servern annars)
# ---------------------------------------------------------------------------
def _gex_context(token, nq_price):
    if not token or not nq_price:
        return None
    try:
        j = _A().gex(token) or {}
    except Exception:
        return None
    for g in j.get("instruments") or []:
        if str(g.get("inst", "")).upper() != "NQ":
            continue
        lv = g.get("levels") or []
        if not lv:
            return None
        nq_price = _num(g.get("fut_price")) or nq_price
        above = sorted([l for l in lv if l["price"] > nq_price], key=lambda l: l["price"])
        below = sorted([l for l in lv if l["price"] <= nq_price], key=lambda l: -l["price"])
        pick = lambda arr: ({"label": arr[0]["label"], "price": arr[0]["price"],
                             "dist": round(arr[0]["price"] - nq_price)} if arr else None)
        return {"above": pick(above), "below": pick(below), "regime": g.get("regime")}
    return None


# ---------------------------------------------------------------------------
#  Morgonens setups sparas så kvällen kan visa facit
# ---------------------------------------------------------------------------
def _record_morning_picks():
    st = _state()
    today = now_et().date().isoformat()
    if (st.get("picks") or {}).get("date") == today:
        return st["picks"]
    A = _A()
    rows = _scan_rows()
    if not rows:
        return None
    bull = [r for r in rows if A._scr_match("bull", r)]
    bull.sort(key=lambda r: (-(r.get("rs_rating") or 0), -(r.get("score10") or 0)))
    picks = [{"tkr": r["ticker"], "score": r.get("score10")} for r in bull[:10]]
    if not picks:
        return None
    st["picks"] = {"date": today, "items": picks}
    _state_save(st)
    return st["picks"]


def _picks_result():
    st = _state()
    p = st.get("picks") or {}
    if p.get("date") != now_et().date().isoformat() or not p.get("items"):
        return None
    snaps = snapshots([i["tkr"] for i in p["items"]])
    res = []
    for i in p["items"]:
        s = snaps.get(i["tkr"])
        if not s:
            continue
        db, pdb = s.get("dailyBar") or {}, s.get("prevDailyBar") or {}
        if _bar_date(db) != now_et().date():
            continue
        c, pc = _num(db.get("c")), _num(pdb.get("c"))
        if c and pc:
            res.append({"tkr": i["tkr"], "chg": round((c / pc - 1) * 100, 2)})
    if not res:
        return None
    green = sum(1 for r in res if r["chg"] > 0)
    avg = round(sum(r["chg"] for r in res) / len(res), 2)
    res.sort(key=lambda r: -r["chg"])
    return {"n": len(res), "green": green, "avg": avg, "items": res}


# ---------------------------------------------------------------------------
#  Före öppning / dagens nav
# ---------------------------------------------------------------------------
def premarket(token=""):
    sess = session()
    t = now_et()
    mk = markets()
    nq = next((m["price"] for m in mk if m["k"] == "NQ"), None)
    out = {"session": sess, "et": t.strftime("%H:%M"), "weekday": t.weekday(),
           "markets": mk, "movers": movers(sess) if sess in ("pre", "open", "post") else None,
           "events": _events_today(), "earnings": _earnings_on(0),
           "actuals": [a for a in actuals() if a["us"]][:8]}
    open_at = t.replace(hour=9, minute=30, second=0, microsecond=0)
    if sess == "pre":
        out["opens_in_min"] = int((open_at - t).total_seconds() // 60)
        try:
            _record_morning_picks()
        except Exception as e:
            print("[hub] picks:", e)
    g = _gex_context(token, nq)
    if g:
        out["gex"] = g
    try:
        import flash_news
        out["flash"] = flash_news.recent(6)
    except Exception:
        pass
    return out


def quotes(tickers):
    syms = [t.strip().upper() for t in (tickers or "").split(",") if t.strip()][:40]
    sess = session()
    snaps = snapshots(syms)
    rows = {r["ticker"]: r for r in _scan_rows()}
    out = []
    for s in syms:
        m = move_from_snapshot(snaps.get(s) or {}, sess) if snaps.get(s) else None
        r = rows.get(s) or {}
        item = {"tkr": s, "earnings_in": r.get("earnings_in")}
        if m:
            item.update(m)
        out.append(item)
    return {"session": sess, "items": out}


# ---------------------------------------------------------------------------
#  Kvällsrapport
# ---------------------------------------------------------------------------
def _compute_evening(lang="sv"):
    A = _A()
    t = now_et()
    sess = session(t)
    idx = []
    try:
        for i in A.indices().get("indices") or []:
            idx.append({"name": i.get("name"), "chg": i.get("pct"), "price": i.get("priceStr")})
    except Exception:
        pass
    rows = _scan_rows()
    bull = sum(1 for r in rows if r.get("label") in ("BULL", "MOMENTUM", "Rocketcase"))
    bear = sum(1 for r in rows if r.get("label") in ("BEAR", "AVSVALNING"))
    mv = movers("post" if sess == "post" else "open")
    day_up = sorted([i for i in (mv.get("up") or []) + (mv.get("down") or [])],
                    key=lambda i: -(i.get("day") if i.get("day") is not None else i["chg"]))
    facit = _picks_result()
    tomorrow = (t + _dt.timedelta(days=3 if t.weekday() == 4 else 1)).date()
    ev_tom = [e for e in _events_today(tomorrow) if e.get("impact") == "High"][:5]
    earn_tom = _earnings_on(1 if t.weekday() != 4 else 3)
    out = {"date": t.date().isoformat(), "session": sess, "indices": idx,
           "breadth": {"bull": bull, "bear": bear},
           "after": mv if sess == "post" else None,
           "facit": facit, "tomorrow": {"events": ev_tom, "earnings": earn_tom[:6]}}
    # En AI-text per dag och språk (delas av alla) — kostar några öre.
    facts = "INDEX: %s\nBREDD: bull %d / bear %d\nSETUPS I MORSE: %s\nI MORGON: %s; rapporter: %s" % (
        ", ".join("%s %+.1f%%" % (i["name"], i["chg"] or 0) for i in idx),
        bull, bear,
        ("%d av %d gröna, snitt %+.1f%%" % (facit["green"], facit["n"], facit["avg"])) if facit else "okänt",
        "; ".join("%s %s" % (e["time_se"], e["title"]) for e in ev_tom) or "inga större makrohändelser",
        ", ".join(e["tkr"] for e in earn_tom[:6]) or "inga större")
    try:
        sysp = ("Du är Grabit. Skriv 'Kvällsrapport' på svenska: 3 korta meningar. "
                "1) hur dagen blev på börsen, 2) hur morgonens setups gick, 3) vad som väntar i morgon. "
                "Använd BARA siffrorna du får, hitta aldrig på. Ingen rådgivning.")
        out["text"] = A._ai_text("evening:%s" % t.date().isoformat(), sysp, facts, 220, lang=lang)
    except Exception:
        out["text"] = ""
    return out


def evening(lang="sv"):
    return _memo(("evening", lang), 600, _compute_evening, lang)


# ---------------------------------------------------------------------------
#  Ovanliga optionsflöden (CBOE fördröjda kedjor, gratis)
# ---------------------------------------------------------------------------
_OPT_NAMES = ["NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL", "PLTR", "COIN",
              "MSTR", "AVGO", "NFLX", "HOOD", "SMCI", "MU", "ARM", "SOFI", "IONQ", "RKLB"]
_OPT_RE = re.compile(r"(\d{6})([CP])(\d{8})$")
_FLOW = {"items": [], "t": 0.0, "asof": ""}


def _rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


def _scan_options():
    if requests is None:
        return
    # Kedjorna kan vara flera MB var. Hoppa över rundan om minnet redan är högt.
    if _rss_mb() > float(os.environ.get("HUB_OPT_MAX_MB", "1100")):
        print("[hub] optionsflöde hoppas över (minne %.0f MB)" % _rss_mb())
        return
    names = list(_OPT_NAMES)
    for r in sorted(_scan_rows(), key=lambda r: -_num(r.get("rel_vol"), 0))[:5]:
        if r["ticker"] not in names and _num(r.get("mcap_musd"), 0) >= 2000:
            names.append(r["ticker"])
    today = now_et().date()
    found = []
    for sym in names:
        try:
            r = requests.get("https://cdn.cboe.com/api/global/delayed_quotes/options/%s.json" % sym,
                             timeout=25, headers={"User-Agent": "grabit-flow/1.0"})
            if r.status_code != 200:
                continue
            d = (r.json() or {}).get("data") or {}
        except Exception:
            continue
        spot = _num(d.get("current_price"))
        for o in d.get("options") or []:
            m = _OPT_RE.search(o.get("option") or "")
            if not m:
                continue
            vol = _num(o.get("volume"), 0)
            oi = _num(o.get("open_interest"), 0)
            last = _num(o.get("last_trade_price"), 0)
            if vol < 1000 or last <= 0:
                continue
            ymd = m.group(1)
            try:
                exp = _dt.date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
            except ValueError:
                continue
            dte = (exp - today).days
            if dte < 0 or dte > 60:
                continue
            ratio = vol / max(oi, 1)
            prem = vol * last * 100
            if ratio < 2.5 or prem < 250_000:
                continue
            typ = m.group(2)
            strike = int(m.group(3)) / 1000.0
            otm = ((strike / spot - 1) * 100) if spot else None
            found.append({"tkr": sym, "type": "CALL" if typ == "C" else "PUT", "strike": strike,
                          "exp": exp.isoformat(), "dte": dte, "vol": int(vol), "oi": int(oi),
                          "ratio": round(ratio, 1), "premium": int(prem),
                          "otm": round(otm, 1) if otm is not None else None,
                          "iv": round(_num(o.get("iv"), 0) * 100, 1) if _num(o.get("iv")) else None})
        d = r = None       # släpp kedjan direkt (kan vara flera MB)
        import gc
        gc.collect()
        time.sleep(1.0)
    found.sort(key=lambda x: -x["premium"])
    _FLOW["items"] = found[:30]
    _FLOW["t"] = time.time()
    _FLOW["asof"] = now_et().strftime("%H:%M")
    print("[hub] optionsflöde: %d ovanliga kontrakt" % len(found))


# ---------------------------------------------------------------------------
#  Short squeeze-kandidater (blankningsandel från Yahoo, bara för kandidater)
# ---------------------------------------------------------------------------
_SQUEEZE = {"items": [], "t": 0.0}


def _scan_squeeze():
    A = _A()
    rows = [r for r in _scan_rows()
            if _num(r.get("last"), 0) >= 2 and _num(r.get("rel_vol"), 0) >= 1.5
            and _num(r.get("ret_5"), 0) > 0 and _num(r.get("pct_from_high"), -100) >= -25]
    rows.sort(key=lambda r: -_num(r.get("rel_vol"), 0))
    out = []
    for r in rows[:40]:
        try:
            info = A.company_info(r["ticker"]) or {}
        except Exception:
            info = {}
        sf = _num(info.get("short_float"))
        if sf is None or sf < 0.12:
            continue
        rv = _num(r.get("rel_vol"), 0)
        score = round(sf * 100 * min(rv, 5) / 5 + max(_num(r.get("ret_5"), 0), 0) / 2, 1)
        out.append({"tkr": r["ticker"], "name": r.get("name") or "", "short": round(sf * 100, 1),
                    "rel_vol": round(rv, 1), "ret_5": round(_num(r.get("ret_5"), 0), 1),
                    "from_high": round(_num(r.get("pct_from_high"), 0), 1),
                    "score": score, "last": r.get("last")})
        time.sleep(0.3)
    out.sort(key=lambda x: -x["score"])
    _SQUEEZE["items"] = out[:20]
    _SQUEEZE["t"] = time.time()
    print("[hub] short squeeze: %d kandidater" % len(out))


# ---------------------------------------------------------------------------
#  Smarta pengar + setup: insider-/politikerköp i aktier med GRABIT-läge
# ---------------------------------------------------------------------------
def _compute_smart():
    A = _A()
    items = []
    for fn in ("_congress_flow", "_corp_flow", "_fmp_insider_flow"):
        try:
            items += list(getattr(A, fn)() or [])
        except Exception as e:
            print("[hub] smart %s: %s" % (fn, e))
    rows = {r["ticker"]: r for r in _scan_rows()}
    agg = {}
    for it in items:
        if it.get("action") != "KÖP" or (it.get("days_ago") or 999) > 45:
            continue
        tk = it.get("ticker")
        if not tk:
            continue
        a = agg.setdefault(tk, {"tkr": tk, "buyers": set(), "pol": 0, "ins": 0, "usd": 0.0,
                                "latest": 999})
        a["buyers"].add(it.get("person") or "?")
        if it.get("kind") == "politiker":
            a["pol"] += 1
        else:
            a["ins"] += 1
        a["usd"] += _num(it.get("usd_num"), 0) or 0
        a["latest"] = min(a["latest"], it.get("days_ago") or 999)
    out = []
    for tk, a in agg.items():
        r = rows.get(tk) or {}
        sc = _num(r.get("score10"))
        both = a["pol"] > 0 and a["ins"] > 0
        rank = len(a["buyers"]) * 2 + (4 if both else 0) + (sc or 0)
        out.append({"tkr": tk, "name": r.get("name") or "", "buyers": len(a["buyers"]),
                    "pol": a["pol"], "ins": a["ins"], "usd": int(a["usd"]), "latest": a["latest"],
                    "score": sc, "label": r.get("label") or "", "both": both, "rank": rank})
    out.sort(key=lambda x: -x["rank"])
    return out[:25]


def smart():
    return _memo("smart", 1800, _compute_smart)


# ---------------------------------------------------------------------------
#  Teknisk mätare per tidsram
# ---------------------------------------------------------------------------
def _gauge(df):
    import numpy as np
    c = df["Close"].dropna()
    if len(c) < 30:
        return None
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = float((100 - 100 / (1 + up / dn.replace(0, np.nan))).iloc[-1])
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])
    last = float(c.iloc[-1])
    s = 0
    s += 1 if last > float(ema20.iloc[-1]) else -1
    s += 1 if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) else -1
    s += 1 if rsi >= 55 else (-1 if rsi <= 45 else 0)
    s += 1 if hist > 0 else -1
    v = ("STRONG_BUY" if s >= 3 else "BUY" if s >= 1 else
         "STRONG_SELL" if s <= -3 else "SELL" if s <= -1 else "NEUTRAL")
    return {"score": s, "verdict": v, "rsi": round(rsi, 1)}


def _compute_tech(tk):
    A = _A()
    out = {}
    try:
        import yfinance as yf
    except Exception:
        yf = None
    for key, period, interval in ((("15m", "5d", "15m"), ("1h", "1mo", "60m")) if yf else ()):
        try:
            df = yf.Ticker(tk).history(period=period, interval=interval, auto_adjust=False)
            g = _gauge(df) if df is not None and len(df) else None
            if g:
                out[key] = g
        except Exception:
            pass
    try:
        dd = A._fetch_daily(tk)
        if dd is not None and len(dd):
            g = _gauge(dd)
            if g:
                out["1d"] = g
            wk = dd.resample("W").agg({"Close": "last"}).dropna()
            g = _gauge(wk)
            if g:
                out["1w"] = g
    except Exception:
        pass
    return out


def tech(tk):
    tk = (tk or "").upper().strip()
    if not re.match(r"^[A-Z0-9.\-^=]{1,12}$", tk):
        return {}
    return _memo(("tech", tk), 300, _compute_tech, tk)


# ---------------------------------------------------------------------------
#  Pushar: personlig morgon, öppning, kväll, makroutfall
# ---------------------------------------------------------------------------
def _fmt_pct(v):
    return ("%+.1f%%" % v).replace(".", ",")


def watchlist_line(tickers, snaps, rows, sess="pre", n=4):
    """'NVDA +3,1% · TSLA −2,0% (rapport idag)' för en prenumerants aktier."""
    parts = []
    moves = []
    for tk in tickers or []:
        s = snaps.get(tk)
        m = move_from_snapshot(s, sess) if s else None
        if m and m.get("fresh"):
            moves.append((tk, m["chg"]))
    moves.sort(key=lambda x: -abs(x[1]))
    for tk, chg in moves[:n]:
        extra = " (rapport idag)" if (rows.get(tk) or {}).get("earnings_in") == 0 else ""
        parts.append("%s %s%s" % (tk, _fmt_pct(chg), extra))
    return " · ".join(parts)


def personal_send(title, body, tag="grabit-morgon", sess="pre"):
    """Skickar samma grundtext till alla, med en egen rad om deras bevakade aktier."""
    import push_notify as PN
    with PN._lock:
        subs = PN._load()
    if not subs:
        return {"skickade": 0}
    all_tk = sorted({t for s in subs for t in (s.get("_tickers") or [])})
    snaps = snapshots(all_tk) if all_tk else {}
    rows = {r["ticker"]: r for r in _scan_rows()}
    plain, sent = [], 0
    for s in subs:
        line = watchlist_line(s.get("_tickers"), snaps, rows, sess)
        if line:
            PN._send_to([s], title, (body + "\nDina aktier: " + line)[:480], "/", tag)
            sent += 1
        else:
            plain.append(s)
    if plain:
        PN._send_to(plain, title, body[:480], "/", tag)
    return {"personliga": sent, "vanliga": len(plain)}


def premarket_line():
    """'Premarket: SMCI +12,4% · NKE −8,1%' för morgonpushen."""
    try:
        mv = movers("pre")
    except Exception:
        return ""
    up = (mv.get("up") or [])[:2]
    dn = (mv.get("down") or [])[:1]
    if not up and not dn:
        return ""
    return "Premarket: " + " · ".join("%s %s" % (i["tkr"], _fmt_pct(i["chg"])) for i in up + dn)


def _open_push_once():
    import push_notify as PN
    t = now_et()
    if session(t) != "open" or not (575 <= t.hour * 60 + t.minute <= 600):
        return
    st = _state()
    today = t.date().isoformat()
    if st.get("open_push") == today or PN.sub_count() == 0:
        return
    st["open_push"] = today
    _state_save(st)
    mv = movers("open")
    up = (mv.get("up") or [])[:3]
    dn = (mv.get("down") or [])[:2]
    if not up and not dn:
        return
    A = _A()
    rows = _scan_rows()
    bull = {r["ticker"] for r in rows if A._scr_match("bull", r)}
    brk = [i["tkr"] for i in up if i["tkr"] in bull]
    body = "Starkast: " + ", ".join("%s %s" % (i["tkr"], _fmt_pct(i["chg"])) for i in up)
    if dn:
        body += "\nSvagast: " + ", ".join("%s %s" % (i["tkr"], _fmt_pct(i["chg"])) for i in dn)
    if brk:
        body += "\nSetups som bryter ut: " + ", ".join(brk)
    personal_send("GRABIT · Öppningen", body, tag="grabit-open", sess="open")


def _evening_push_once():
    import push_notify as PN
    t = now_et()
    if session(t) != "post" or not (975 <= t.hour * 60 + t.minute <= 1020):
        return
    st = _state()
    today = t.date().isoformat()
    if st.get("evening_push") == today or PN.sub_count() == 0:
        return
    st["evening_push"] = today
    _state_save(st)
    ev = _compute_evening("sv")
    _MEMO[("evening", "sv")] = (time.time(), ev)
    parts = []
    for i in (ev.get("indices") or [])[:2]:
        parts.append("%s %s" % (i["name"], _fmt_pct(i["chg"] or 0)))
    body = " · ".join(parts)
    f = ev.get("facit")
    if f:
        body += "\nMorgonens setups: %d av %d gröna, snitt %s" % (f["green"], f["n"], _fmt_pct(f["avg"]))
    aft = (ev.get("after") or {}).get("up") or []
    if aft:
        body += "\nEfterhandel: " + ", ".join("%s %s" % (i["tkr"], _fmt_pct(i["chg"])) for i in aft[:2])
    tom = (ev.get("tomorrow") or {}).get("events") or []
    if tom:
        body += "\nI morgon: " + "; ".join("%s %s" % (e["time_se"], e["title"]) for e in tom[:2])
    personal_send("GRABIT · Kvällsrapport", body.strip(), tag="grabit-kvall", sess="post")


def _macro_push_scan():
    """En push när viktiga amerikanska makroutfall publiceras. Utfall som kommer
    samtidigt (t.ex. KPI m/m, å/å och kärn-KPI) samlas i samma notis."""
    import push_notify as PN
    if os.environ.get("GRABIT_MACRO_PUSH", "1") != "1" or PN.sub_count() == 0:
        return
    st = _state()
    sent = st.setdefault("macro_sent", [])
    new = []
    for a in actuals():
        if not (a["us"] and a["big"] and time.time() - a["t"] < 1800):
            continue
        key = "%s|%s" % (a["name"], a["actual"])
        if key in sent:
            continue
        sent.append(key)
        new.append(a)
    if not new:
        return
    st["macro_sent"] = sent[-200:]
    _state_save(st)
    lines = []
    for a in new[:3]:
        ln = "%s: %s" % (re.sub(r"^(US|U\.S\.)\s+", "", a["name"]), a["actual"])
        if a.get("forecast"):
            ln += " mot väntat %s" % a["forecast"]
        lines.append(ln)
    PN.send_all("GRABIT · Makro USA", "\n".join(lines), url="/", tag="grabit-makro")


_MEM_PREV = {}


def _mem_report():
    """En rad i loggen per timme: vad håller minnet? (för att hitta läckor)"""
    try:
        import gc
        A = _A()
        rss = 0
        try:
            with open("/proc/self/status") as f:
                for ln in f:
                    if ln.startswith("VmRSS:"):
                        rss = int(ln.split()[1]) // 1024
        except Exception:
            pass
        by_fn = {}
        for k in list(getattr(A, "_CACHE", {}).keys()):
            n = k[0] if isinstance(k, tuple) and k else str(k)
            by_fn[n] = by_fn.get(n, 0) + 1
        top = sorted(by_fn.items(), key=lambda kv: -kv[1])[:6]
        # Vilka objekttyper växer? Jämför mot förra timmen (hittar läckor).
        import collections
        cnt = collections.Counter(type(o).__name__ for o in gc.get_objects())
        prev = _MEM_PREV.get("cnt") or {}
        grow = sorted(((k, v - prev.get(k, 0)) for k, v in cnt.items()), key=lambda kv: -kv[1])[:8]
        _MEM_PREV["cnt"] = dict(cnt)
        cnt = None
        try:
            from sok_module import prune_yf_threads
            pr = prune_yf_threads()
            if pr:
                print("[mem] rensade %d döda yfinance-trådar" % pr)
        except Exception:
            pass
        print("[mem] rss=%dMB cache=%d memo=%d ai=%d fh=%d trådar=%d objekt=%d topp=%s" % (
            rss, len(getattr(A, "_CACHE", {})), len(_MEMO),
            len(getattr(A, "_AI_TEXT_CACHE", {}) or {}), len(getattr(A, "_FH_CACHE", {}) or {}),
            threading.active_count(), len(gc.get_objects()),
            ",".join("%s:%d" % kv for kv in top)))
        if prev:
            print("[mem] växer: " + ", ".join("%s +%d" % kv for kv in grow if kv[1] > 0))
    except Exception as e:
        print("[mem] fel:", e)


def _loop():
    time.sleep(300)                 # låt skanning och cache värmas först
    last_opt = last_sq = 0.0
    last_mem = 0.0
    while True:
        if time.time() - last_mem > 3600:
            last_mem = time.time()
            _mem_report()
        t = now_et()
        sess = session(t)
        for fn in (_open_push_once, _evening_push_once):
            try:
                fn()
            except Exception as e:
                print("[hub] %s: %s" % (fn.__name__, e))
        try:
            if t.weekday() < 5 and 7 <= t.hour < 17:
                _macro_push_scan()
        except Exception as e:
            print("[hub] makropush:", e)
        try:
            if sess == "pre":
                _record_morning_picks()
        except Exception as e:
            print("[hub] picks:", e)
        try:
            # Optionsflödet uppdateras var 20:e minut under handelsdagen (fördröjt data).
            if sess == "open" and time.time() - last_opt > 1200:
                last_opt = time.time()
                _scan_options()
        except Exception as e:
            print("[hub] optioner:", e)
        try:
            if sess in ("pre", "open", "post") and time.time() - last_sq > 3600:
                last_sq = time.time()
                _scan_squeeze()
        except Exception as e:
            print("[hub] squeeze:", e)
        time.sleep(60)


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------
def _is_pro(token):
    try:
        from billing_web import verify_token
        return verify_token(token) is not None
    except Exception:
        return False


def register(app):
    from fastapi import Query

    @app.get("/api/hub/premarket")
    def _hub_pre(token: str = ""):
        return premarket(token)

    @app.get("/api/hub/quotes")
    def _hub_quotes(tickers: str = ""):
        return quotes(tickers)

    @app.get("/api/hub/evening")
    def _hub_evening(lang: str = "sv"):
        return evening("en" if str(lang).lower().startswith("en") else "sv")

    @app.get("/api/hub/actuals")
    def _hub_actuals():
        return {"items": actuals()}

    @app.get("/api/hub/options")
    def _hub_options(token: str = ""):
        items = _FLOW["items"]
        pro = _is_pro(token)
        return {"items": items if pro else items[:2], "total": len(items), "pro": pro,
                "asof": _FLOW["asof"], "delayed": True}

    @app.get("/api/hub/squeeze")
    def _hub_squeeze(token: str = ""):
        items = _SQUEEZE["items"]
        pro = _is_pro(token)
        return {"items": items if pro else items[:2], "total": len(items), "pro": pro}

    @app.get("/api/hub/smart")
    def _hub_smart(token: str = ""):
        items = smart()
        pro = _is_pro(token)
        return {"items": items if pro else items[:2], "total": len(items), "pro": pro}

    @app.get("/api/hub/tech/{ticker}")
    def _hub_tech(ticker: str):
        try:
            tf = tech(ticker)
        except Exception as e:
            print("[hub] tech %s: %s" % (ticker, e))
            tf = {}
        return {"ticker": ticker.upper(), "tf": tf}

    @app.on_event("startup")
    def _hub_start():
        threading.Thread(target=_loop, daemon=True).start()

    print("[hub] Daily hub registrerad (före öppning, kvällsrapport, makro, optioner, squeeze)")
