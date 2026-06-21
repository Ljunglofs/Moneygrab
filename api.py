"""
=====================================================================
 GRABIT API  ·  api.py
 Exponerar din riktiga screening-logik som JSON så frontend:en kan
 hämta ekta data. Återanvänder fetch/analyze från sok_module och
 evaluate från breakout_engine — INGEN kopia av analysen.

 KÖR LOKALT:
   pip install -r requirements_api.txt
   uvicorn api:app --reload --port 8000
   → öppna http://localhost:8000/api/health
   → http://localhost:8000/docs  (interaktiv API-dok)

 Servern har ingen Streamlit-runtime, så vi neutraliserar
 @st.cache_data och lägger egen TTL-cache i stället.
=====================================================================
"""
import time
import types
from typing import Optional

# ---- Neutralisera Streamlit-cache INNAN sok_module importeras -------
import streamlit as st  # noqa
_noop = lambda *a, **k: (lambda f: f)          # @st.cache_data(...) -> passthrough
st.cache_data = _noop
st.cache_resource = _noop

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

# Din riktiga logik
from sok_module import fetch as _fetch_raw, analyze, fetch_many   # noqa
try:
    from breakout_engine import evaluate as engine_evaluate
except Exception:
    engine_evaluate = None

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse

# =====================================================================
#  ENKEL TTL-CACHE  (ersätter st.cache_data på servern)
# =====================================================================
_CACHE: dict = {}

def cached(ttl: int):
    def deco(fn):
        def wrap(*args):
            key = (fn.__name__, args)
            now = time.time()
            hit = _CACHE.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            val = fn(*args)
            _CACHE[key] = (now, val)
            return val
        wrap.__name__ = fn.__name__
        return wrap
    return deco

fetch = cached(300)(_fetch_raw)   # 5 min, som i appen

# ----- BATCH-PREFETCH (för stora universum) --------------------------
_PREFETCH: dict = {}              # ticker -> df (dagsdata), fylls inför en scan

@cached(600)
def _batch(tickers_tuple):
    return fetch_many(list(tickers_tuple))

def prefetch(tickers):
    """Hämtar dagsdata för många tickers i få anrop och cachar i _PREFETCH."""
    try:
        _PREFETCH.update(_batch(tuple(tickers)))
    except Exception:
        pass

# =====================================================================
#  UNIVERSE  (måste matcha app.py)
# =====================================================================
UNIVERSE = {
    "AI-infra":      ["NVDA","NBIS","CRDO","ALAB","MRVL","AVGO","AMD","SMCI","VRT","DGXX","CRWV","IREN","PENG","AAOI"],
    "Halvledare":    ["HIMX","SKYT","SNPS","NVTS","XFAB.PA"],
    "Photonics":     ["SIVE.ST","POET","LWLG","VIAV","LPKFF","HLIT"],
    "Quantum":       ["IONQ","QUBT","RGTI"],
    "Rare earth":    ["USAR","MP"],
    "Defense/Drone": ["ONDS","KTOS","AVAV"],
    "Lidar/Phys.AI": ["OUST","LAZR","AEVA"],
    "Nuclear/Energi":["OKLO","NNE","SMR","UEC","UUUU","VST","DNN","FLNC"],
    "Space":         ["RKLB","ASTS","RDW"],
    "Mjukvara":      ["NOW","PLTR","ZETA","TTWO","INFQ"],
    "Fintech/Krypto":["HOOD","HIVE"],
    "Bio":           ["RXRX","VIVO","HIMS"],
    "Mega":          ["MSFT","IBM","TSLA"],
    "Koppar":        ["FCX","HBM"],
    "Silver/Guld":   ["AG","PAAS","GAU"],
    "Sverige":       ["SUBGEN.ST","SMOL.ST","SHT-B.ST","ACCON.ST","SIVE.ST","OBDU-B.ST","XOM-B.ST","TERRNT-B.ST","VISC.ST"],
    "Bevakning":     ["SUU","IMSR","AIRJ","ORBT","ENAFF","TRT","ABTC","AMPG","KEEL.TO"],
    "Big Tech":      ["AAPL","GOOGL","AMZN","META","NFLX","ADBE","CRM","ORCL","CSCO","QCOM","TXN","INTC","INTU","AMAT","MU","LRCX","KLAC","ADI","PANW","CDNS","ARM","DELL","HPQ"],
    "SaaS/Moln":     ["UBER","ABNB","SHOP","CRWD","DDOG","SNOW","NET","MDB","ZS","TEAM","PYPL","SQ","ABNB"],
    "Finans":        ["JPM","BAC","WFC","GS","MS","C","V","MA","AXP","BLK","SCHW","SPGI","CB","PGR","COF"],
    "Hälsa":         ["UNH","JNJ","LLY","PFE","MRK","ABBV","TMO","ABT","AMGN","GILD","MRNA","ISRG","VRTX","BMY","DHR","CVS"],
    "Konsument":     ["WMT","COST","HD","NKE","MCD","SBUX","KO","PEP","PG","DIS","LOW","TGT","CMG","BKNG","MDLZ"],
    "Industri/Energi":["XOM","CVX","COP","BA","CAT","GE","HON","LMT","RTX","DE","UPS","UNP","SLB","OXY","NEE"],
    "EV/Clean":      ["RIVN","LCID","PLUG","ENPH","FSLR","RUN","CHPT"],
    "Auto/Telekom":  ["F","GM","T","VZ","TMUS"],
    "Krypto-proxy":  ["COIN","MSTR","MARA","RIOT","CLSK"],
}
TICKER_THEME = {t: k for k, v in UNIVERSE.items() for t in v}
ALL_TICKERS = sorted({t for v in UNIVERSE.values() for t in v})
INDICES = [("S&P 500", "^GSPC"), ("Nasdaq", "^IXIC"), ("Stockholm", "^OMX")]

# =====================================================================
#  HJÄLPARE  (samma logik som app.py, utan Streamlit)
# =====================================================================
def _jsonable(obj):
    """Gör numpy/pandas-typer JSON-säkra."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(obj, float):
        return None if (obj != obj) else obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def scan(ticker: str):
    try:
        df = _PREFETCH.get(ticker)
        if df is None:
            df, _ = fetch(ticker)
    except Exception:
        return None
    if df is None:
        return None
    try:
        a = analyze(df)
    except Exception:
        return None
    a["ticker"] = ticker
    a["theme"] = TICKER_THEME.get(ticker, "")
    return a


@cached(3600)
def company_info(ticker: str) -> dict:
    if yf is None:
        return {}
    try:
        info = yf.Ticker(ticker).info or {}
        if not isinstance(info, dict):
            return {}
        name = (info.get("longName") or info.get("shortName")
                or info.get("displayName") or "")
        web = info.get("website") or ""
        domain = (web.replace("https://", "").replace("http://", "")
                     .replace("www.", "").strip("/").split("/")[0]) if web else ""
        return {
            "name": name,
            "sector": info.get("sector") or info.get("industry") or "",
            "mcap": info.get("marketCap"),
            "currency": info.get("currency") or "",
            "summary": info.get("longBusinessSummary") or "",
            "country": info.get("country") or "",
            "website": web,
            "domain": domain,
            "pe": info.get("trailingPE"),
            "fwd_pe": info.get("forwardPE"),
            "beta": info.get("beta"),
            "div_yield": info.get("dividendYield"),
            "wk_high": info.get("fiftyTwoWeekHigh"),
            "wk_low": info.get("fiftyTwoWeekLow"),
            "avg_vol": info.get("averageVolume"),
            "prev_close": info.get("regularMarketPreviousClose") or info.get("previousClose"),
        }
    except Exception:
        return {}


def ai_score_components(a):
    tech = 0
    tech += 3 if a["last"] > a["ema50"] else 0
    tech += 3 if a["last"] > a["ema200"] else 0
    tech += 2 if 45 <= a["rsi"] <= 75 else 0
    tech += 2 if a["ret_20"] > 0 else 0
    tech = min(10, tech)
    momentum = min(10, max(0, int(a["momentum"] / 3.5)))
    sentiment = min(10, max(1, int(a["rel_vol"] * 4)))
    timing = 8
    if a["rel_vol"] < 1.2:      timing -= 3
    if a["pct_from_high"] > -5: timing -= 2
    risk = 8
    if a["atr_pct"] > 12: risk -= 3
    if a["rsi"] > 78:     risk -= 2
    fund = 5
    total = round((tech + momentum + sentiment + timing + risk + fund) / 6, 1)
    return {"ai_score": total, "technical": tech, "momentum_score": momentum,
            "sentiment": sentiment, "fundamental": fund, "risk": risk,
            "timing": max(1, timing)}


def trade_motor_v2(a):
    entry_q = 10; breakout_q = 10; fakeout_r = 2; exit_r = 2
    reasons = []; risks = []
    if a.get("rel_vol", 0) < 1.2:
        entry_q -= 3; breakout_q -= 2; fakeout_r += 2; risks.append("Låg relativ volym")
    if a.get("pct_from_high", -100) > -5:
        entry_q -= 2; risks.append("Nära motstånd/topp")
    if a.get("rsi", 0) > 75:
        entry_q -= 2; exit_r += 2; risks.append("Utsträckt RSI")
    if a.get("ret_20", 0) > 30:
        exit_r += 2; risks.append("Parabolisk rörelse")
    if a.get("last", 0) > a.get("ema50", 9e9):  reasons.append("Över EMA50")
    if a.get("last", 0) > a.get("ema200", 9e9): reasons.append("Över EMA200")
    if a.get("ret_5", 0) > 5:                   reasons.append("Stark 5d-fart")
    if a.get("momentum", 0) > 20:               reasons.append("Momentum starkt")
    confidence = round((entry_q + breakout_q + (10 - fakeout_r) + (10 - exit_r)) / 4, 1)
    return {"entry_quality": max(1, entry_q), "breakout_quality": max(1, breakout_q),
            "fakeout_risk": min(10, fakeout_r), "exit_risk": min(10, exit_r),
            "confidence": confidence, "reasons": reasons, "risks": risks}


def hetta_of(a) -> int:
    """0–100 'hetta': momentum + relativ volym. Approx för Hetast-listan."""
    base = a.get("momentum", 0) / 35 * 60          # 0–60 av momentum
    vol  = min(40, max(0, (a.get("rel_vol", 0) - 0.8) * 40))  # 0–40 av rel.vol
    return int(max(0, min(100, base + vol)))


@cached(600)
def regime_of(ticker):
    try:
        df, _ = fetch(ticker)
    except Exception:
        return "BLANDAD", 0.0
    if df is None or len(df) < 200:
        return "BLANDAD", 0.0
    c = df["Close"].dropna()
    last  = float(c.iloc[-1])
    ma200 = float(c.rolling(200).mean().iloc[-1])
    ma50  = float(c.rolling(50).mean().iloc[-1])
    pct   = (last / ma200 - 1) * 100
    if last > ma200 and ma50 > ma200: return "BULL", pct
    if last < ma200:                  return "BEAR", pct
    return "BLANDAD", pct


@cached(600)
def scan_universe(theme_key: Optional[str] = None) -> list:
    """Scannar hela universumet (eller ett tema). Cachas 10 min."""
    tickers = UNIVERSE.get(theme_key, []) if theme_key else ALL_TICKERS
    prefetch(tickers)                 # batch-hämta allt i få Yahoo-anrop
    out = []
    for t in tickers:
        a = scan(t)
        if a:
            a["hetta"] = hetta_of(a)
            out.append(_jsonable(a))
    return out

# =====================================================================
#  FASTAPI
# =====================================================================
app = FastAPI(title="GRABIT API", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # lås till din frontend-domän i produktion
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def index():
    import os
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return fh.read()
    return "<h1>GRABIT API</h1><p>index.html saknas i repot.</p>"


# ---- Statiska assets (hero-video + poster) -------------------------
_STATIC_FILES = {
    "bg_1280-1.mp4": "video/mp4",
    "bg_poster.jpg": "image/jpeg",
    "monthly_case.json": "application/json",
}

@app.get("/{fname}")
def static_asset(fname: str):
    media = _STATIC_FILES.get(fname)
    if not media:
        raise HTTPException(404, "Not found")
    import os
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
    if not os.path.exists(p):
        raise HTTPException(404, "File missing")
    return FileResponse(p, media_type=media)


@app.get("/api/health")
def health():
    return {"status": "ok", "tickers": len(ALL_TICKERS), "yfinance": yf is not None}


@app.get("/api/debug", include_in_schema=False)
def debug():
    """Diagnos: visar exakt vad yfinance ger på Render (enskild + bulk + scan)."""
    import traceback
    out = {}
    try:
        import yfinance as _yf
        out["yfinance_version"] = getattr(_yf, "__version__", "?")
    except Exception as e:
        out["yfinance_version"] = f"import-fail: {e}"
    try:
        df, _intr = _fetch_raw("AAPL")
        out["single_AAPL"] = {
            "rows": 0 if df is None else int(len(df)),
            "last": None if (df is None or len(df) == 0) else round(float(df["Close"].iloc[-1]), 2),
        }
    except Exception:
        out["single_AAPL"] = {"error": traceback.format_exc().strip().splitlines()[-1]}
    try:
        m = fetch_many(["AAPL", "MSFT", "NVDA"]) or {}
        out["bulk"] = {t: (0 if v is None else int(len(v))) for t, v in m.items()}
    except Exception:
        out["bulk"] = {"error": traceback.format_exc().strip().splitlines()[-1]}
    try:
        rows = scan_universe(None)
        out["scan_rows"] = len(rows)
        out["scan_sample"] = (rows[0] if rows else None)
    except Exception:
        out["scan_rows"] = {"error": traceback.format_exc().strip().splitlines()[-1]}
    return out


@app.get("/api/universe")
def universe():
    return {"themes": UNIVERSE, "ticker_theme": TICKER_THEME}


# ----- NYHETER (riktig RSS via feedparser) ---------------------------
@cached(600)
def _news_list():
    try:
        import feedparser
    except Exception:
        return []
    feeds = [
        "https://www.financialjuice.com/feed.ashx?xy=rss",
        "https://feeds.marketwatch.com/marketwatch/topstories/",
    ]
    out = []
    for url in feeds:
        try:
            f = feedparser.parse(url)
            for e in f.entries[:10]:
                t = (e.get("title") or "").strip()
                if t and t not in out:
                    out.append(t)
        except Exception:
            continue
    return out[:18]


@app.get("/api/news")
def news():
    return {"news": _news_list()}


# ----- KOMMANDE RAPPORTER (riktiga earnings-datum via yfinance) ------
@cached(6 * 3600)
def _events_list():
    if yf is None:
        return []
    import datetime as _dt
    today = _dt.date.today()
    base = list(dict.fromkeys(UNIVERSE.get("Bevakning", []) + ALL_TICKERS))[:30]
    out = []
    for t in base:
        dt = None
        try:
            cal = yf.Ticker(t).calendar
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                dt = ed[0] if isinstance(ed, (list, tuple)) and ed else ed
            elif cal is not None and hasattr(cal, "loc"):
                dt = cal.loc["Earnings Date"][0]
        except Exception:
            dt = None
        if dt is None:
            continue
        try:
            d = dt.date() if hasattr(dt, "date") else dt
            days = (d - today).days
        except Exception:
            continue
        if 0 <= days <= 45:
            out.append({"tkr": t, "date": str(d), "days": days})
    out.sort(key=lambda x: x["days"])
    return out[:8]


@app.get("/api/events")
def events():
    return {"events": _events_list()}


# ----- GRAF: pris + volym över olika tidsramar -----------------------
_RANGE = {
    "1d":  ("1d",  "5m"),
    "5d":  ("5d",  "30m"),
    "1mo": ("1mo", "1d"),
    "3mo": ("3mo", "1d"),
    "6mo": ("6mo", "1d"),
    "1y":  ("1y",  "1d"),
}

@cached(300)
def _chart(ticker, rng):
    if yf is None:
        return []
    period, interval = _RANGE.get(rng, ("1mo", "1d"))
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    out = []
    for ts, row in df.iterrows():
        try:
            c = float(row["Close"])
            if c != c:
                continue
            v = float(row.get("Volume", 0) or 0)
            out.append({"t": int(ts.timestamp()), "c": round(c, 4), "v": v})
        except Exception:
            continue
    return out


@app.get("/api/chart/{ticker}")
def chart(ticker: str, range: str = "1mo"):
    return {"ticker": ticker.upper(), "range": range, "points": _chart(ticker.upper(), range)}


# ----- ÄGANDE: institutionellt + insider + största ägare -------------
@cached(6 * 3600)
def _ownership(ticker):
    out = {"inst_pct": None, "insider_pct": None, "holders": []}
    if yf is None:
        return out
    tk = yf.Ticker(ticker)
    try:
        mh = tk.major_holders
        if mh is not None and hasattr(mh, "to_dict"):
            d = mh.to_dict()
            col = list(d.keys())[0]
            for k, v in d[col].items():
                kk = str(k).lower()
                try:
                    fv = float(v)
                except Exception:
                    continue
                pct = round(fv * 100, 2) if fv <= 1 else round(fv, 2)
                if "insider" in kk and out["insider_pct"] is None:
                    out["insider_pct"] = pct
                elif "institution" in kk and "float" not in kk and "count" not in kk and out["inst_pct"] is None:
                    out["inst_pct"] = pct
    except Exception:
        pass
    try:
        ih = tk.institutional_holders
        if ih is not None and hasattr(ih, "iterrows"):
            for _, row in ih.head(6).iterrows():
                try:
                    name = str(row.get("Holder") or row.get("holder") or "").strip()
                    if not name:
                        continue
                    raw = row.get("% Out")
                    if raw is None:
                        raw = row.get("pctHeld")
                    pct = None
                    if raw is not None:
                        fr = float(raw)
                        pct = round(fr * 100, 2) if fr <= 1 else round(fr, 2)
                    out["holders"].append({"name": name, "pct": pct})
                except Exception:
                    continue
    except Exception:
        pass
    return out


@app.get("/api/ownership/{ticker}")
def ownership(ticker: str):
    return {"ticker": ticker.upper(), **_ownership(ticker.upper())}


@app.get("/api/indices")
def indices():
    out = []
    for name, tk in INDICES:
        lbl, pct = regime_of(tk)
        out.append({"name": name, "label": lbl, "pct": round(pct, 1)})
    return {"indices": out}


@app.get("/api/screen")
def screen(themes: Optional[str] = Query(None, description="Komma-separerade teman, tomt = alla"),
           labels: Optional[str] = Query(None, description="Filtrera på etiketter, t.ex. BULL,MOMENTUM")):
    keys = [k.strip() for k in themes.split(",")] if themes else [None]
    rows: list = []
    seen = set()
    for k in keys:
        for a in scan_universe(k):
            if a["ticker"] in seen:
                continue
            seen.add(a["ticker"])
            rows.append(a)
    if labels:
        want = {l.strip().upper() for l in labels.split(",")}
        rows = [r for r in rows if str(r.get("label", "")).upper() in want]
    rows.sort(key=lambda x: x.get("score10", 0), reverse=True)
    return {"count": len(rows), "rows": rows}


@app.get("/api/stock/{ticker}")
def stock(ticker: str):
    ticker = ticker.upper()
    a = scan(ticker)
    if not a:
        raise HTTPException(404, f"Ingen data för {ticker}")
    payload = {
        "analysis": _jsonable(a),
        "ai_score": ai_score_components(a),
        "trade_motor": trade_motor_v2(a),
        "company": company_info(ticker),
        "engine": None,
    }
    # Breakout-motor (ringar + entry/exit) om modulen finns
    if engine_evaluate is not None:
        try:
            df, _ = fetch(ticker)
            try:
                bench, _ = fetch("^GSPC")
                bench_close = bench["Close"] if bench is not None else None
            except Exception:
                bench_close = None
            payload["engine"] = _jsonable(engine_evaluate(df, bench_close))
        except Exception:
            payload["engine"] = None
    return payload


@app.get("/api/overview")
def overview():
    """Komponerar startsidan: index, veckans urval, hetast, dagens bull, faktorer.
    OBS: pick-urvalet här är en första heuristik — kan portas exakt från
    featured_picks.py / dagens_bull.py i nästa steg."""
    rows = scan_universe(None)
    idx = indices()["indices"]

    by_score = sorted(rows, key=lambda x: x.get("score10", 0), reverse=True)
    by_hetta = sorted(rows, key=lambda x: x.get("hetta", 0), reverse=True)

    bull_like = [r for r in by_score if str(r.get("label")) in ("BULL", "MOMENTUM", "Rocketcase")]
    vand_like = [r for r in by_score if str(r.get("label")) == "VÄNDNING"]

    picks = {
        "dagens_setup": bull_like[0] if bull_like else (by_score[0] if by_score else None),
        "veckans_case": bull_like[1] if len(bull_like) > 1 else None,
        "wildcard":     vand_like[0] if vand_like else None,
    }

    hetast = by_hetta[:10]
    dagens_bull = by_hetta[0] if by_hetta else None

    n = len(rows)
    avg = round(sum(r.get("score10", 0) for r in rows) / n, 1) if n else 0
    factors = {
        "traffar":   n,
        "momentum":  sum(1 for r in rows if str(r.get("label")) == "MOMENTUM"),
        "vandning":  sum(1 for r in rows if str(r.get("label")) == "VÄNDNING"),
        "snittpoang": avg,
    }
    return {"indices": idx, "picks": picks, "hetast": hetast,
            "dagens_bull": dagens_bull, "factors": factors}


# =====================================================================
#  AI  ·  POST /api/ai   ("Fråga Grabit")
#  Portar systemprompt + Anthropic-logik från ai_module.py till API:t.
#  Skillnad mot Streamlit-versionen:
#    - nyckel läses från MILJÖVARIABEL (Render → Environment), ej st.secrets
#    - kontext byggs LIVE från scannern (scan/scan_universe/indices) i stället
#      för st.session_state, så Grabit kan svara på "Varför rör sig MRVL?",
#      jämföra aktier och ranka dagens lägen — utan att hitta på siffror.
#  Frontend-kontrakt (grabit_index.html → aiSend):
#    POST {question: str, history: [{r:"u"|"a", t:str}]}  ->  {answer: str}
# =====================================================================
import os
import re as _re

from pydantic import BaseModel

# Återanvänd EXAKT samma systemprompt som Streamlit-appen.
try:
    from ai_module import SYSTEM_PROMPT
except Exception:
    SYSTEM_PROMPT = (
        "Du är Grabit, AI-analytiker i appen GRABIT för teknisk aktieanalys. "
        "Svara på svenska, koncist och pedagogiskt. Du ger ALDRIG köp- eller "
        "säljråd, påminner om att detta inte är finansiell rådgivning, och "
        "varnar tydligt för risk (överköpt, parabol, tunn likviditet, utspädning). "
        "Du hittar inte på siffror — vet du inte, säger du det."
    )

AI_MODEL = os.environ.get("GRABIT_AI_MODEL", "claude-sonnet-4-6")


def _anthropic_client():
    """Anthropic-klient från ANTHROPIC_API_KEY. None om nyckel/paket saknas."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except Exception:
        return None
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _num(v, dec=2, suf="", sign=False):
    """Säker sifferformat — returnerar '—' för None/NaN."""
    if isinstance(v, (int, float)) and not (v != v):  # not NaN
        return (f"{v:+.{dec}f}{suf}" if sign else f"{v:.{dec}f}{suf}")
    return "—"


def _fmt_stock_ctx(a: dict) -> str:
    """En kompakt kontextrad per aktie ur analyze()-datan."""
    name = company_info(a.get("ticker", "")).get("name") or a.get("ticker", "")
    last = a.get("last", 0) or 0
    ema50 = "över" if last > (a.get("ema50") or 9e9) else "under"
    ema200 = "över" if last > (a.get("ema200") or 9e9) else "under"
    return (
        f"- {a.get('ticker')} ({name}) · tema {a.get('theme', '—')}\n"
        f"  GRABIT-score {a.get('score10', '?')}/10 · bedömning {a.get('label', '?')}\n"
        f"  pris {_num(last)} · RSI {_num(a.get('rsi'), 0)} · "
        f"från 52v-topp {_num(a.get('pct_from_high'), 1, '%', sign=True)} · "
        f"5d {_num(a.get('ret_5'), 1, '%', sign=True)} · "
        f"20d {_num(a.get('ret_20'), 1, '%', sign=True)} · "
        f"rel.volym {_num(a.get('rel_vol'))}x\n"
        f"  EMA50 {ema50} · EMA200 {ema200} · "
        f"BOS {a.get('bos', '—')} · struktur {a.get('structure', '—')}"
    )


def _ai_context(question: str) -> str:
    """Bygg live-kontext från scannern utifrån frågans innehåll."""
    qU = question.upper()
    tokens = set(_re.split(r"[^A-Z0-9.]+", qU))
    hits = [t for t in ALL_TICKERS if t in tokens]

    blocks = []
    for t in hits[:3]:                       # nämnda aktier (cachas 5 min via fetch)
        a = scan(t)
        if a:
            blocks.append(_fmt_stock_ctx(a))

    wide = (not hits) or _re.search(
        r"MARKNAD|IDAG|RÖR SIG|HETAST|BÄST|TOPP|VÄNDNING|R/R|RISK|SETUP", qU)
    if wide:
        try:
            idx = indices()["indices"]
            idx_line = " · ".join(
                f"{i['name']} {i['label']} ({_num(i['pct'], 1, '%', sign=True)})" for i in idx)
            hot = sorted(scan_universe(None), key=lambda x: x.get("hetta", 0),
                         reverse=True)[:5]
            hot_line = ", ".join(
                f"{r['ticker']} {r.get('score10', '?')}/10 ({r.get('label', '')})" for r in hot)
            blocks.append(f"MARKNADSLÄGE: {idx_line}\nHETAST NU: {hot_line}")
        except Exception:
            pass

    if not blocks:
        return ""
    return ("\n\n[LIVE-DATA FRÅN GRABIT-SCANNERN — använd bara om frågan rör det, "
            "och hitta inte på siffror utöver dessa:]\n"
            + "\n".join(blocks)
            + "\n[Slut på live-data.]")


def _ai_fallback(question: str, ctx: str) -> str:
    msg = ("Grabit AI är inte aktiverad på servern ännu — sätt miljövariabeln "
           "ANTHROPIC_API_KEY på backenden (Render → Environment) så svarar jag "
           "med riktig analys i stället för det här.")
    if ctx:
        msg += "\n\nMen jag har live-data för din fråga:" + ctx
    return msg


class AiPayload(BaseModel):
    question: str
    history: list = []


@app.post("/api/ai")
def ai(payload: AiPayload):
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(400, "Tom fråga")

    ctx = _ai_context(q)

    # Mappa frontend-historiken (r:"u"/"a") -> Anthropic (user/assistant).
    msgs = []
    for m in (payload.history or []):
        if not isinstance(m, dict):
            continue
        text = (m.get("t") or "").strip()
        if not text or text == "…":
            continue
        msgs.append({"role": "user" if m.get("r") == "u" else "assistant",
                     "content": text})

    # Säkerställ att sista turn är just den här frågan.
    if not msgs or msgs[-1]["role"] != "user" or msgs[-1]["content"] != q:
        msgs.append({"role": "user", "content": q})
    # Anthropic kräver att konversationen börjar med 'user'.
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    # Injicera live-kontexten i sista användarmeddelandet.
    if ctx:
        msgs[-1]["content"] += ctx

    client = _anthropic_client()
    if client is None:
        return {"answer": _ai_fallback(q, ctx), "demo": True}

    try:
        resp = client.messages.create(
            model=AI_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=msgs,
        )
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        if not text:
            text = "Jag fick inget svar just nu — försök igen om en stund."
        return {"answer": text}
    except Exception as e:
        # Mjukt fel: frontenden visar svaret i chatten i stället för en krasch.
        return {"answer": f"Kunde inte nå Grabit AI just nu ({type(e).__name__}). "
                          f"Försök igen om en stund.", "error": True}


# ----------------------------------------------------------
#  BOLAGSPROFIL  (svensk sektor + beskrivning)
#  yfinance .info funkar lokalt men blockas på Render -> AI-fallback.
#  Cachas per ticker, anropas BARA från detaljvyn (ej i skannern).
# ----------------------------------------------------------
_company_blurb_cache: dict = {}


def _ai_company(tk: str, name: str) -> dict:
    client = _anthropic_client()
    if client is None:
        return {"sector": "", "summary": ""}
    try:
        import json as _j
        who = tk + (f' ("{name}")' if name and name != tk else "")
        prompt = (
            f"Aktien med tickern {who}. Svara ENBART med giltig JSON (inga kodblock), på svenska:\n"
            '{"sector": "<sektor på ett ord, t.ex. Teknik, Energi, Finans, Konsument, '
            'Hälsa, Industri, Råvaror, Fastighet, Kommunikation>", '
            '"summary": "<2 korta meningar om vad bolaget gör och varför det är intressant, på svenska>"}\n'
            "Känner du inte till bolaget – sätt båda fälten till tom sträng."
        )
        resp = client.messages.create(
            model=AI_MODEL, max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        data = _j.loads(text)
        return {"sector": str(data.get("sector", "") or "").strip(),
                "summary": str(data.get("summary", "") or "").strip()}
    except Exception:
        return {"sector": "", "summary": ""}


@app.get("/api/company/{ticker}")
def company_blurb(ticker: str):
    tk = ticker.upper().strip()
    if tk in _company_blurb_cache:
        return _company_blurb_cache[tk]
    base = company_info(tk)
    name = base.get("name") or tk
    sector = base.get("sector") or ""
    summary = base.get("summary") or ""
    if not sector or not summary:          # tomt (t.ex. Render) -> AI-fallback
        ai = _ai_company(tk, name)
        sector = sector or ai.get("sector", "")
        summary = summary or ai.get("summary", "")
    out = {"ticker": tk, "name": name, "sector": sector, "summary": summary}
    if sector or summary:                  # cacha bara lyckade svar
        _company_blurb_cache[tk] = out
    return out


# =====================================================================
#  DAYTRADE  ·  /api/daytrade
#  Regelbaserade intraday-setups från live-data. Inga magiska siffror:
#  Entry/SL/TP/RR/confidence härleds ur VWAP, RSI, ATR och EMA-trend.
# =====================================================================
_DT_WATCH = [
    ("GC=F", "XAUUSD", "Guld (futures)",       True),
    ("NQ=F", "US100",  "Nasdaq 100 (futures)", True),
    ("ES=F", "US500",  "S&P 500 (futures)",    False),
    ("AAPL", "AAPL",   "Apple Inc.",           False),
    ("NVDA", "NVDA",   "NVIDIA Corp.",         False),
    ("TSLA", "TSLA",   "Tesla Inc.",           False),
]
_DT_INTERVAL = "15m"
_DT_ATR_K = 0.6


def _dt_rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _dt_atr(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def _dt_vwap(df):
    day = df[df.index.date == df.index[-1].date()]
    if day.empty:
        day = df.tail(26)
    tp = (day["High"] + day["Low"] + day["Close"]) / 3
    return float((tp * day["Volume"]).cumsum().iloc[-1] / day["Volume"].cumsum().iloc[-1])


def _dt_build(sym, ticker, name, pinned):
    if yf is None:
        return None
    df = yf.download(sym, period="5d", interval=_DT_INTERVAL, progress=False, auto_adjust=False)
    if df is None or len(df) < 30:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    import math
    price = float(df["Close"].iloc[-1])
    vwap = _dt_vwap(df)
    rsi = _dt_rsi(df["Close"])
    atr = _dt_atr(df)
    ema20 = float(df["Close"].ewm(span=20).mean().iloc[-1])
    ema50 = float(df["Close"].ewm(span=50).mean().iloc[-1])
    vol = float(df["Volume"].iloc[-1])
    vol_avg = float(df["Volume"].tail(20).mean())
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in [price, vwap, rsi, atr, ema20]):
        return None

    up_trend = ema20 >= ema50 and price >= vwap
    dn_trend = ema20 < ema50 and price < vwap
    if up_trend and rsi < 68:
        bias = "long"
    elif dn_trend and rsi > 32:
        bias = "short"
    else:
        bias = "wait"

    risk = max(_DT_ATR_K * atr, price * 0.0008)
    if bias == "long":
        entry = vwap if abs(price - vwap) < risk else price
        sl, tp1, tp2 = entry - risk, entry + 2 * risk, entry + 3 * risk
    elif bias == "short":
        entry = vwap if abs(price - vwap) < risk else price
        sl, tp1, tp2 = entry + risk, entry - 2 * risk, entry - 3 * risk
    else:
        entry, sl, tp1, tp2 = price, price - risk, price + 2 * risk, price + 3 * risk

    rr = abs(tp1 - entry) / abs(entry - sl) if entry != sl else 0

    score = 50
    if (bias == "long" and up_trend) or (bias == "short" and dn_trend): score += 15
    if bias == "long" and 40 <= rsi <= 58: score += 10
    if bias == "short" and 42 <= rsi <= 60: score += 10
    if vol > vol_avg: score += 10
    if 0.0005 * price < atr < 0.02 * price: score += 8
    if abs(price - vwap) < 0.5 * risk: score += 7
    score = max(50, min(90, score)) if bias != "wait" else max(50, min(64, score))

    rdec = 4 if price < 10 else (2 if price < 1000 else 1)
    f = lambda v: round(v, rdec)
    ctx = (f"{'Över' if price >= vwap else 'Under'} VWAP"
           f'<span class="sep">·</span>RSI {rsi:.0f}'
           f'<span class="sep">·</span>{"hög" if vol > vol_avg else "normal"} volym'
           f'<span class="sep">·</span>ATR {f(atr)}')

    return {
        "ticker": ticker, "name": name, "bias": bias, "pinned": pinned,
        "price": f(price), "entry": f(entry),
        "entryLabel": f"{f(entry - 0.3 * risk)}–{f(entry + 0.3 * risk)}" if bias != "wait" else "—",
        "sl": f(sl), "tp1": f(tp1), "tp2": f(tp2),
        "rr": f"{rr:.1f}", "confidence": int(round(score)), "context": ctx,
    }


@cached(180)
def _dt_all():
    out = []
    for sym, ticker, name, pinned in _DT_WATCH:
        try:
            s = _dt_build(sym, ticker, name, pinned)
            if s:
                out.append(s)
        except Exception:
            continue
    out.sort(key=lambda s: (not s["pinned"], -s["confidence"]))
    return out


@app.get("/api/daytrade")
def daytrade():
    return {"setups": _dt_all(), "interval": _DT_INTERVAL, "ts": int(time.time())}


# =====================================================================
#  BAKGRUNDS-WARMUP
#  Värmer den tunga 193-ticker-skanningen (overview + screen) UTANFÖR
#  request-vägen, så Render-kallstart inte timeout:ar frontenden.
# =====================================================================
import threading as _threading

def _warmup_once():
    try:
        scan_universe(None)          # värmer /api/overview och /api/screen
    except Exception:
        pass

def _warmup_loop():
    while True:
        _warmup_once()
        time.sleep(480)              # uppdatera var 8:e min (cache-TTL = 10 min)

@app.on_event("startup")
def _start_warmup():
    _threading.Thread(target=_warmup_loop, daemon=True).start()
