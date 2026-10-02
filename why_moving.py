"""
GRABIT  ·  why_moving.py
------------------------
"Varför rör sig $XYZ?" — samlar allt som förklarar en rörelse på ett ställe:

  Katalysator  (rubrik -> kategori: rapport, FDA, partnerskap, kontrakt, uppköp ...)
  Volym        (relativ volym mot 20-dagarssnittet)
  Optioner     (ovanliga optionsflöden, CBOE fördröjt)
  Insiders     (insider- och politikerköp senaste 45 dagarna)
  Analytiker   (upp-/nedgraderingar senaste dagarna)
  Nyhet        (senaste relevanta rubriken med tid)
  Tekniskt     (52-veckorstopp, utbrott, RS-rating)

Regel nummer ett: ingenting hittas på. Varje rad visas bara när det finns
riktig data bakom. Kategorin tas med ordregler ur rubriken (inte AI), och
AI-sammanfattningen får bara använda raderna ovan.

Notis: bevakade aktier (portföljen i appen) som rör sig ≥5 % under
handelsdagen får en notis med förklaringen, högst en per aktie och dag.
"""

import re
import time
import threading
import datetime as _dt

import daily_hub as H

_CACHE = {}          # tk -> (ts, data)
_TTL = 300
_lock = threading.Lock()

# Ordning = prioritet. (nyckel, svensk etikett, engelsk etikett, regex)
_CATS = [
    ("earnings", "Rapport", "Earnings",
     r"\b(earnings|results|quarter(ly)?|q[1-4]\b|eps|revenue|guidance|outlook|beats?|miss(es|ed)?|forecast)\b"),
    ("fda", "FDA / studie", "FDA / trial",
     r"\b(fda|approval|approves?|approved|phase [123i]+|trial|pdufa|clearance|breakthrough therapy|ema\b)"),
    ("mna", "Uppköp / fusion", "M&A",
     r"\b(acquir(e|es|ed|ing|ition)|merger|merge|buyout|takeover|to be acquired|tender offer|deal to buy)\b"),
    ("contract", "Kontrakt / order", "Contract",
     r"\b(contract|awarded|award|order|purchase agreement|supply agreement|selected by|wins?)\b"),
    ("partner", "Partnerskap", "Partnership",
     r"\b(partner(ship|s|ing)?|collaborat(e|ion|es)|alliance|teams? up|joint venture|agreement with)\b"),
    ("analyst", "Analytiker", "Analyst",
     r"\b(upgrade[sd]?|downgrade[sd]?|price target|initiat(es|ed) coverage|overweight|underweight|outperform|buy rating)\b"),
    ("offering", "Nyemission", "Offering",
     r"\b(offering|private placement|dilut|shelf registration|raises? \$|priced .* shares)\b"),
    ("buyback", "Återköp", "Buyback",
     r"\b(buyback|repurchase)\b"),
    ("legal", "Juridik / myndighet", "Legal / regulatory",
     r"\b(lawsuit|sues?|probe|investigation|sec charges|subpoena|antitrust|settlement|recall)\b"),
    ("product", "Produkt / lansering", "Product",
     r"\b(launch(es|ed)?|unveil(s|ed)?|introduc(es|ed)|new product|release[sd]?)\b"),
    ("macro", "Politik / makro", "Politics / macro",
     r"\b(tariff|trump|white house|sanction|fed\b|rate cut|china)\b"),
]
_CAT_RE = [(k, sv, en, re.compile(rx, re.I)) for k, sv, en, rx in _CATS]


def _cat_of(headline):
    for k, sv, en, rx in _CAT_RE:
        if rx.search(headline or ""):
            return {"key": k, "sv": sv, "en": en}
    return None


def _news(tk, hours=36):
    start = (_dt.datetime.utcnow() - _dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    j = H._alp_get("/v1beta1/news", {"symbols": tk, "limit": 12, "sort": "desc", "start": start})
    out = []
    for n in (j or {}).get("news") or []:
        h = (n.get("headline") or "").strip()
        syms = n.get("symbols") or []
        if not h or tk not in syms:
            continue
        out.append({"headline": h[:180], "ts": n.get("created_at") or "",
                    "source": n.get("source") or "", "focused": len(syms) <= 3})
    # Nyheter som handlar om just den här aktien först (inte listor med 20 bolag)
    out.sort(key=lambda x: (not x["focused"], -_ts(x["ts"])))
    return out


def _ts(iso):
    try:
        return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _row(tk):
    for r in H._scan_rows():
        if r.get("ticker") == tk:
            return r
    return None


def _row_fallback(tk):
    """Aktie utanför skanningen: kör motorn på dagsdatan (cachad 5 min)."""
    try:
        A = H._A()
        df = A._fetch_daily(tk)
        if df is None or not len(df):
            return None
        a = A.analyze(df)
        if a.get("data_jump"):
            return None
        return a
    except Exception:
        return None


def _hi52_pct(r):
    try:
        return float(r.get("pct_from_high"))
    except Exception:
        return None


def compute(tk):
    tk = (tk or "").upper().strip()
    if not re.match(r"^[A-Z][A-Z0-9.\-]{0,9}$", tk):
        return {"tkr": tk, "rows": [], "notable": False}
    sess = H.session()
    r = _row(tk) or _row_fallback(tk) or {}
    # --- Rörelsen
    chg = None
    try:
        snap = H.snapshots([tk]).get(tk)
        m = H.move_from_snapshot(snap, sess) if snap else None
        if m:
            chg = m.get("day") if (sess == "post" and m.get("day") is not None) else m.get("chg")
    except Exception:
        pass
    if chg is None and r.get("ret_1") is not None:
        chg = round(float(r["ret_1"]), 2)
    rows = []
    facts = []
    # --- Nyheter + katalysator
    news = _news(tk)
    cat = None
    top = None
    # Bara rubriker som handlar om just den här aktien — inte "20 aktier att bevaka"-listor.
    news = [n for n in news if n["focused"]]
    for n in news:
        c = _cat_of(n["headline"])
        if c:
            cat, top = c, n
            break
    if top is None and news:
        top = news[0]
    # Rapport ur kalendern slår rubriker (säkrare källa)
    ed = str(r.get("earnings_date") or "")[:10]
    today = H.now_et().date()
    rep = None
    if ed:
        try:
            d = _dt.date.fromisoformat(ed)
            delta = (today - d).days
            if 0 <= delta <= 2:
                rep = {"sv": "Rapport " + ("i dag" if delta == 0 else ("i går" if delta == 1 else "i förrgår")),
                       "en": "Earnings " + ("today" if delta == 0 else ("yesterday" if delta == 1 else "2 days ago"))}
        except Exception:
            pass
    if rep:
        rows.append({"k": "catalyst", "sv": rep["sv"], "en": rep["en"]})
        facts.append(rep["sv"])
    elif cat:
        rows.append({"k": "catalyst", "sv": cat["sv"], "en": cat["en"]})
        facts.append("Katalysator: " + cat["sv"])
    # --- Volym
    try:
        rv = float(r.get("rel_vol"))
        if rv >= 1.5:
            txt_sv = ("%.1f× snittvolymen" % rv).replace(".", ",")
            txt_en = "%.1f× average volume" % rv
            if r.get("src") == "eod":
                txt_sv += " (senaste dagen)"
                txt_en += " (last session)"
            rows.append({"k": "volume", "sv": txt_sv, "en": txt_en, "hot": rv >= 3})
            facts.append("Volym " + txt_sv)
    except Exception:
        pass
    # --- Optioner (ovanliga flöden ur CBOE-skanningen)
    try:
        fl = [x for x in (H._FLOW.get("items") or []) if x.get("tkr") == tk]
        if fl:
            calls = [x for x in fl if x["type"] == "CALL"]
            puts = [x for x in fl if x["type"] == "PUT"]
            prem = sum(x["premium"] for x in fl)
            pm = ("$%.1fM" % (prem / 1e6)) if prem >= 1e6 else ("$%dk" % round(prem / 1e3))
            if len(calls) >= len(puts):
                sv, en = "Ovanligt många köpoptioner (calls) · %s premie" % pm, "Unusual call buying · %s premium" % pm
            else:
                sv, en = "Ovanligt många säljoptioner (puts) · %s premie" % pm, "Unusual put buying · %s premium" % pm
            rows.append({"k": "options", "sv": sv, "en": en})
            facts.append("Optioner: " + sv)
    except Exception:
        pass
    # --- Insiders / politiker (senaste 45 dagarna, från smarta pengar-listan)
    try:
        hit = H._MEMO.get("smart")
        if hit is None:
            threading.Thread(target=H.smart, daemon=True).start()
        for x in (hit[1] if hit else []) or []:
            if x.get("tkr") == tk:
                who = []
                if x.get("ins"):
                    who.append("%d insiderköp" % x["ins"])
                if x.get("pol"):
                    who.append("%d politikerköp" % x["pol"])
                sv = ", ".join(who) + " senaste 45 d"
                en = ", ".join(w.replace("insiderköp", "insider buys").replace("politikerköp", "politician buys")
                               for w in who) + " last 45 days"
                rows.append({"k": "insiders", "sv": sv, "en": en})
                facts.append("Insiders: " + sv)
                break
    except Exception:
        pass
    # --- Analytiker (senaste 3 dagarna)
    try:
        import market_extras as X
        cut = (today - _dt.timedelta(days=3)).isoformat()
        for x in X._RATINGS.get("items") or []:
            if x.get("tkr") != tk or x.get("date", "") < cut:
                continue
            act = {"up": ("Uppgradering", "Upgrade"), "down": ("Nedgradering", "Downgrade"),
                   "init": ("Ny bevakning", "Initiated")}.get(x.get("action"), ("Ny riktkurs", "New target"))
            pt = (" · riktkurs $%g" % x["pt"]) if x.get("pt") else ""
            sv = "%s från %s%s" % (act[0], x.get("firm") or "analytiker", pt)
            en = "%s by %s%s" % (act[1], x.get("firm") or "analyst", pt.replace("riktkurs", "target"))
            rows.append({"k": "analyst", "sv": sv, "en": en})
            facts.append("Analytiker: " + sv)
            break
    except Exception:
        pass
    # --- Nyhet
    if top:
        rows.append({"k": "news", "sv": top["headline"], "en": top["headline"], "ts": top["ts"],
                     "source": top["source"]})
        facts.append("Nyhet: " + top["headline"])
    # --- Tekniskt
    tech_sv, tech_en = [], []
    ph = _hi52_pct(r)
    if ph is not None and chg is not None:
        if chg > 0 and ph >= -0.5:
            tech_sv.append("Ny 52-veckorstopp"); tech_en.append("New 52-week high")
        elif chg > 0 and ph >= -5:
            tech_sv.append("Nära 52-veckorstoppen (%.1f %%)" % ph); tech_en.append("Near 52-week high (%.1f%%)" % ph)
        elif chg < 0 and ph <= -40:
            tech_sv.append("%.0f %% under 52-veckorstoppen" % -ph); tech_en.append("%.0f%% below 52-week high" % -ph)
    if r.get("bos") and (chg or 0) > 0:
        tech_sv.append("Utbrott ur struktur"); tech_en.append("Structure breakout")
    try:
        rs = int(r.get("rs_rating") or 0)
        if rs >= 85:
            tech_sv.append("RS %d – bland marknadens starkaste" % rs); tech_en.append("RS %d – among the strongest" % rs)
        elif 0 < rs <= 15:
            tech_sv.append("RS %d – bland marknadens svagaste" % rs); tech_en.append("RS %d – among the weakest" % rs)
    except Exception:
        pass
    if tech_sv:
        rows.append({"k": "tech", "sv": " · ".join(tech_sv[:2]).replace(".", ","),
                     "en": " · ".join(tech_en[:2])})
        facts.append("Tekniskt: " + " · ".join(tech_sv[:2]))
    notable = (chg is not None and abs(chg) >= 3) or any(x["k"] in ("catalyst", "options") for x in rows)
    out = {"tkr": tk, "name": r.get("name") or "", "chg": chg, "session": sess,
           "rows": rows, "notable": bool(notable), "has_news": bool(top)}
    # --- Sammanfattning: en mening, bara ur fakta ovan
    if notable and chg is not None:
        out["summary_facts"] = facts
    return out


def _summary(tk, data, lang):
    facts = data.get("summary_facts")
    if not facts:
        return ""
    try:
        A = H._A()
        hour = H.now_et().strftime("%Y-%m-%d-%H")
        sysp = ("Du är Grabit. Skriv EN kort mening (max 22 ord) på svenska om varför aktien rör sig. "
                "Använd BARA fakta i listan. Hitta aldrig på orsaker. Finns ingen nyhet eller katalysator, "
                "skriv att rörelsen saknar tydlig nyhet och drivs av volym/teknik. Ingen rådgivning.")
        user = "%s %+.1f %%\n%s" % (tk, data["chg"], "\n".join(facts))
        return A._ai_text("why:%s:%s" % (tk, hour), sysp, user, 90, lang=lang) or ""
    except Exception:
        return ""


def why(tk, lang="sv"):
    tk = (tk or "").upper().strip()
    now = time.time()
    with _lock:
        hit = _CACHE.get(tk)
    if hit and now - hit[0] < _TTL:
        data = hit[1]
    else:
        data = compute(tk)
        with _lock:
            _CACHE[tk] = (now, data)
            if len(_CACHE) > 300:
                for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:100]:
                    _CACHE.pop(k, None)
    out = {k: v for k, v in data.items() if k != "summary_facts"}
    s = _summary(tk, data, lang)
    if s:
        out["summary"] = s
    return out


# ---------------------------------------------------------------------------
#  Notis för bevakade aktier
# ---------------------------------------------------------------------------
_PUSH_MIN = 5.0


def push_line(data, lang="sv"):
    parts = []
    for k in ("catalyst", "volume", "options", "analyst", "insiders", "tech"):
        for r in data.get("rows") or []:
            if r["k"] == k:
                parts.append(r[lang] if k != "tech" else r[lang].split(" · ")[0])
    return " · ".join(parts[:3]) or ("Ingen tydlig nyhet – drivs av volym och teknik" if lang == "sv"
                                     else "No clear news – driven by volume and technicals")


def _push_scan():
    import push_notify as PN
    if H.session() != "open":
        return
    watch = PN.all_watch_tickers()[:120]
    if not watch:
        return
    st = H._state()
    today = H.now_et().date().isoformat()
    sent = st.get("why_push") or {}
    if sent.get("day") != today:
        sent = {"day": today, "tks": []}
    snaps = H.snapshots(watch)
    for tk, s in snaps.items():
        if tk in sent["tks"]:
            continue
        m = H.move_from_snapshot(s, "open")
        if not m or not m.get("fresh") or m.get("chg") is None or abs(m["chg"]) < _PUSH_MIN:
            continue
        data = compute(tk)
        with _lock:
            _CACHE[tk] = (time.time(), data)
        sign = "+" if m["chg"] >= 0 else ""
        title = "%s %s%s %%" % (tk, sign, ("%.1f" % m["chg"]).replace(".", ","))
        body = "Varför? " + push_line(data)
        try:
            PN.send_watchlist(tk, title, body[:170])
        except Exception as e:
            print("[why] push-fel:", e)
        sent["tks"].append(tk)
    st["why_push"] = sent
    H._state_save(st)


def _loop():
    time.sleep(360)
    while True:
        try:
            _push_scan()
        except Exception as e:
            print("[why] loop:", e)
        time.sleep(300)


def register(app):
    @app.get("/api/why/{ticker}")
    def why_route(ticker: str, lang: str = "sv"):
        try:
            return why(ticker, "en" if lang == "en" else "sv")
        except Exception as e:
            print("[why] %s: %s" % (ticker, e))
            return {"tkr": ticker.upper(), "rows": [], "notable": False}

    threading.Thread(target=_loop, daemon=True).start()
    print("[why] Varför rör sig-kortet registrerat")
