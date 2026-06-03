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
from sok_module import fetch as _fetch_raw, analyze            # noqa
try:
    from breakout_engine import evaluate as engine_evaluate
except Exception:
    engine_evaluate = None

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

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
    "Bevakning":     ["SUU","IMSR","AIRJ","ORBT","ENAFF","TRT","ABTC"],
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


@app.get("/api/health")
def health():
    return {"status": "ok", "tickers": len(ALL_TICKERS), "yfinance": yf is not None}


@app.get("/api/universe")
def universe():
    return {"themes": UNIVERSE, "ticker_theme": TICKER_THEME}


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
