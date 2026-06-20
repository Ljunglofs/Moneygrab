"""
=====================================================================
 GRABIT API - Fixed version
=====================================================================
Fixad version av api.py med borttaget syntaxfel på rad 151.
"""

import time
import threading
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

try:
    import yfinance as yf
except Exception:
    yf = None

from sok_module import fetch as _fetch_raw, analyze, fetch_many

try:
    from breakout_engine import evaluate as engine_evaluate
except Exception:
    engine_evaluate = None

try:
    from forklaring import forklara
except Exception:
    def forklara(a): return "—"


app = FastAPI(title="GRABIT API", version="2.1")

@app.get("/")
def root():
    return {
        "message": "GRABIT API is running",
        "version": "2.1",
        "endpoints": ["/api/health", "/api/hot", "/api/winners", "/api/stock/{ticker}"]
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== CACHE =====================
_CACHE = {}

def cached(ttl: int):
    def deco(fn):
        def wrap(*args, **kwargs):
            key = (fn.__name__, str(args), str(kwargs))
            now = time.time()
            hit = _CACHE.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            val = fn(*args, **kwargs)
            _CACHE[key] = (now, val)
            return val
        return wrap
    return deco

fetch = cached(300)(_fetch_raw)

# ===================== UNIVERSE =====================
UNIVERSE = {
    "AI-infra": ["NVDA","NBIS","CRDO","ALAB","MRVL","AVGO","AMD","SMCI","VRT","DGXX","CRWV","IREN","PENG","AAOI"],
    "Halvledare": ["HIMX","SKYT","SNPS","NVTS","XFAB.PA"],
    "Photonics": ["SIVE.ST","POET","LWLG","VIAV","LPKFF","HLIT"],
    "Quantum": ["IONQ","QUBT","RGTI"],
    "Rare earth": ["USAR","MP"],
    "Defense/Drone": ["ONDS","KTOS","AVAV"],
    "Lidar/Phys.AI": ["OUST","LAZR","AEVA"],
    "Nuclear/Energi": ["OKLO","NNE","SMR","UEC","UUUU","VST","DNN","FLNC"],
    "Space": ["RKLB","ASTS","RDW"],
    "Mjukvara": ["NOW","PLTR","ZETA","TTWO","INFQ"],
    "Fintech/Krypto": ["HOOD","HIVE"],
    "Bio": ["RXRX","VIVO","HIMS"],
    "Mega": ["MSFT","IBM","TSLA"],
    "Koppar": ["FCX","HBM"],
    "Silver/Guld": ["AG","PAAS","GAU"],
    "Sverige": ["SUBGEN.ST","SMOL.ST","SHT-B.ST","ACCON.ST","SIVE.ST","OBDU-B.ST","XOM-B.ST","TERRNT-B.ST","VISC.ST"],
    "Bevakning": ["SUU","IMSR","AIRJ","ORBT","ENAFF","TRT","ABTC","AMPG","KEEL.TO"],
    "Big Tech": ["AAPL","GOOGL","AMZN","META","NFLX","ADBE","CRM","ORCL","CSCO","QCOM","TXN","INTC","INTU","AMAT","MU","LRCX","KLAC","ADI","PANW","CDNS","ARM","DELL","HPQ"],
}

TICKER_THEME = {t: k for k, v in UNIVERSE.items() for t in v}
ALL_TICKERS = sorted({t for v in UNIVERSE.values() for t in v})

# ===================== HELPERS =====================
def _jsonable(obj):
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

_PREFETCH = {}   # <-- Fixat här (tog bort mellanslaget)

@cached(600)
def _batch(tickers_tuple):
    return fetch_many(list(tickers_tuple))

def prefetch(tickers):
    try:
        _PREFETCH.update(_batch(tuple(tickers)))
    except Exception:
        pass

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
    try:
        a["forklaring"] = forklara(a)
    except Exception:
        a["forklaring"] = "—"
    return a

@cached(3600)
def company_info(ticker: str):
    if yf is None:
        return {}
    try:
        info = yf.Ticker(ticker).info or {}
        return {
            "name": info.get("longName") or info.get("shortName") or "",
            "sector": info.get("sector") or "",
        }
    except Exception:
        return {}

def hetta_of(a) -> int:
    base = a.get("momentum", 0) / 35 * 60
    vol = min(40, max(0, (a.get("rel_vol", 0) - 0.8) * 40))
    return int(max(0, min(100, base + vol)))

@cached(600)
def scan_universe(theme_key: Optional[str] = None):
    tickers = UNIVERSE.get(theme_key, []) if theme_key else ALL_TICKERS
    prefetch(tickers)
    out = []
    for t in tickers:
        a = scan(t)
        if a:
            a["hetta"] = hetta_of(a)
            out.append(_jsonable(a))
    return out

# ===================== ENDPOINTS =====================

@app.get("/api/health")
def health():
    return {"status": "ok", "tickers": len(ALL_TICKERS)}

@app.get("/api/hot")
def hot_list(limit: int = 10):
    rows = scan_universe(None)
    hot = sorted(rows, key=lambda x: x.get("hetta", 0), reverse=True)[:limit]
    return {"title": "10 hetaste just nu", "count": len(hot), "rows": hot}

@app.get("/api/winners")
def winners_today(limit: int = 10):
    rows = scan_universe(None)
    win = [r for r in rows if r.get("ret_5", 0) > 0]
    win = sorted(win, key=lambda x: (x.get("ret_5", 0), x.get("score10", 0)), reverse=True)[:limit]
    return {"title": "Vinnare i dag", "count": len(win), "rows": win}

@app.get("/api/overview")
def overview():
    all_rows = scan_universe(None)
    return {
        "ts": int(time.time()),
        "total": len(all_rows),
        "hot": sorted(all_rows, key=lambda x: x.get("hetta", 0), reverse=True)[:10],
        "winners": sorted([r for r in all_rows if r.get("ret_5", 0) > 2], 
                          key=lambda x: x.get("ret_5", 0), reverse=True)[:8],
    }

@app.get("/api/stock/{ticker}")
def stock(ticker: str):
    ticker = ticker.upper()
    a = scan(ticker)
    if not a:
        raise HTTPException(404, f"Ingen data för {ticker}")
    return {
        "analysis": a,
        "forklaring": a.get("forklaring", "—"),
        "company": company_info(ticker),
    }

# ===================== WARMUP =====================
def _warmup():
    try:
        scan_universe(None)
    except Exception:
        pass

@app.on_event("startup")
def startup_event():
    threading.Thread(target=_warmup, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
