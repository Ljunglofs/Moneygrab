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
import os
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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response

# =====================================================================
#  ENKEL TTL-CACHE  (ersätter st.cache_data på servern)
# =====================================================================
_CACHE: dict = {}
_CACHE_REFRESHING: set = set()

def cached(ttl: int):
    def deco(fn):
        def wrap(*args):
            key = (fn.__name__, args)
            now = time.time()
            hit = _CACHE.get(key)
            if hit:
                if now - hit[0] < ttl:
                    return hit[1]                  # färsk
                # stale-while-revalidate: returnera gammalt DIREKT, uppdatera i bakgrunden
                if key not in _CACHE_REFRESHING:
                    _CACHE_REFRESHING.add(key)
                    def _refresh(k=key, f=fn, a=args):
                        try:
                            _CACHE[k] = (time.time(), f(*a))
                        except Exception:
                            pass
                        finally:
                            _CACHE_REFRESHING.discard(k)
                    import threading
                    threading.Thread(target=_refresh, daemon=True).start()
                return hit[1]                       # gammalt värde -> inget anrop blockerar
            val = fn(*args)                          # inget cachat än -> beräkna synkront
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
    "AI-infra": ["NVDA","NBIS","CRDO","ALAB","MRVL","AVGO","AMD","SMCI","VRT","DGXX","CRWV","IREN","PENG","AAOI","ANET","CIEN","COHR","LITE","PSTG","NTAP","WDC","STX","CLS","FN","SANM","MPWR","ONTO","TSM","APH","GLW"],
    "Halvledare": ["HIMX","SKYT","SNPS","NVTS","XFAB.PA","NXPI","MCHP","SWKS","QRVO","ENTG","TER","ASML","LSCC","RMBS","SITM","POWI","SLAB","AMKR","ACLS","AOSL","MTSI","DIOD","ON"],
    "Photonics": ["SIVE.ST","POET","LWLG","VIAV","LPKFF","HLIT","IPGP","MKSI","KOPN"],
    "Quantum": ["IONQ","QUBT","RGTI","QBTS","ARQQ"],
    "Rare earth": ["USAR","MP","TMC","UAMY","NB"],
    "Defense/Drone": ["ONDS","KTOS","AVAV","NOC","GD","LHX","HII","LDOS","BWXT","TXT","AXON","CW","HEI","TDG","RCAT","DRS","UMAC"],
    "Lidar/Phys.AI": ["OUST","LAZR","AEVA","INVZ","MVIS"],
    "Nuclear/Energi": ["OKLO","NNE","SMR","UEC","UUUU","VST","DNN","FLNC","CEG","CCJ","LEU","NXE","PEG","EXC","SO","DUK","AEP","D","ETR","NRG","PCG","EIX","XEL","SRE","WEC","ED"],
    "Space": ["RKLB","ASTS","RDW","LUNR","SPCE","PL","BKSY","SPIR"],
    "Mjukvara": ["NOW","PLTR","ZETA","TTWO","INFQ","ADSK","WDAY","SAP","OKTA","PATH","GTLB","APP","U","RBLX","DOCN","DT","HUBS","MNDY","ASAN","DOCU","TWLO","ZM","DBX"],
    "Fintech/Krypto": ["HOOD","HIVE","SOFI","AFRM","UPST","NU","BILL","TOST","FOUR","FI","GPN","FIS","LC","RKT"],
    "Bio": ["RXRX","VIVO","HIMS","REGN","BIIB","ALNY","BNTX","NBIX","SRPT","INCY","EXEL","UTHR","IONS","ARWR","CRSP","NTLA","BEAM","VKTX","MDGL","HALO","TGTX"],
    "Mega": ["MSFT","IBM","TSLA","BRK-B","GOOG"],
    "Koppar": ["FCX","HBM","SCCO","TECK","RIO","BHP","VALE","ERO"],
    "Silver/Guld": ["AG","PAAS","GAU","NEM","GOLD","WPM","FNV","AEM","KGC","HMY","EGO","AU","CDE","HL","RGLD","SAND","BTG"],
    "Sverige": ["SUBGEN.ST","SMOL.ST","SHT-B.ST","ACCON.ST","SIVE.ST","OBDU-B.ST","XOM-B.ST","TERRNT-B.ST","VISC.ST","VOLV-B.ST","ERIC-B.ST","SEB-A.ST","SWED-A.ST","INVE-B.ST","ATCO-A.ST","SAND.ST","HEXA-B.ST","EVO.ST","ASSA-B.ST","SHB-A.ST","ABB.ST","ALFA.ST","SKF-B.ST","BOL.ST","TELIA.ST","SAAB-B.ST","NIBE-B.ST"],
    "Bevakning": ["SUU","IMSR","AIRJ","ORBT","ENAFF","TRT","ABTC","AMPG","KEEL.TO","SOUN","BBAI","APLD","WULF"],
    "Big Tech": ["AAPL","GOOGL","AMZN","META","NFLX","ADBE","CRM","ORCL","CSCO","QCOM","TXN","INTC","INTU","AMAT","MU","LRCX","KLAC","ADI","PANW","CDNS","ARM","DELL","HPQ","ACN","ADP"],
    "SaaS/Moln": ["UBER","ABNB","SHOP","CRWD","DDOG","SNOW","NET","MDB","ZS","TEAM","PYPL","SQ","ABNB","VEEV","WIX","BOX","SMAR","PD","PCTY","PAYC","APPF","BL","FIVN","NICE","GWRE","MANH","TYL","SSNC","WK"],
    "Finans": ["JPM","BAC","WFC","GS","MS","C","V","MA","AXP","BLK","SCHW","SPGI","CB","PGR","COF","USB","PNC","TFC","BK","STT","MET","PRU","AIG","ALL","TRV","AFL","MMC","AON","ICE","CME","NDAQ","MCO","KKR"],
    "Hälsa": ["UNH","JNJ","LLY","PFE","MRK","ABBV","TMO","ABT","AMGN","GILD","MRNA","ISRG","VRTX","BMY","DHR","CVS","ELV","CI","HCA","MDT","SYK","BSX","BDX","ZTS","HUM","CNC","MCK","COR","CAH","IDXX","IQV","A","RMD","DXCM","EW"],
    "Konsument": ["WMT","COST","HD","NKE","MCD","SBUX","KO","PEP","PG","DIS","LOW","TGT","CMG","BKNG","MDLZ","PM","MO","CL","KMB","GIS","KHC","HSY","KDP","STZ","EL","CLX","SYY","KR","DG","DLTR","ROST","TJX","ORLY","AZO"],
    "Industri/Energi": ["XOM","CVX","COP","BA","CAT","GE","HON","LMT","RTX","DE","UPS","UNP","SLB","OXY","NEE","MMM","EMR","ETN","ITW","PH","ROK","PCAR","CMI","CARR","OTIS","JCI","FDX","WM","RSG","GWW","FAST","DOV","AME","ROP"],
    "EV/Clean": ["RIVN","LCID","PLUG","ENPH","FSLR","RUN","CHPT","NIO","XPEV","LI","QS","BE","FCEL","SEDG","NOVA","ARRY","SHLS","ALB","SQM","LAC","BEP","NEP"],
    "Auto/Telekom": ["F","GM","T","VZ","TMUS","STLA","TM","HMC","RACE","HOG","APTV","LEA","BWA","GT","CMCSA","CHTR","LUMN","AMX","VOD"],
    "Krypto-proxy": ["COIN","MSTR","MARA","RIOT","CLSK","BITF","HUT","CIFR","BTBT","CAN","BTDR","CORZ","BMNR"],
}
TICKER_THEME = {t: k for k, v in UNIVERSE.items() for t in v}
ALL_TICKERS = sorted({t for v in UNIVERSE.values() for t in v})
INDICES = [("S&P 500", "^GSPC"), ("Nasdaq", "^NDX"), ("Bitcoin", "BTC-USD")]

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


import gc as _gc
SCAN_CHUNK = int(os.environ.get("SCAN_CHUNK", "40"))   # Starter 512MB-säkert. Mer RAM? Höj via env SCAN_CHUNK.

@cached(600)
def scan_universe(theme_key: Optional[str] = None) -> list:
    """Scannar universumet i bitar och tömmer råprisdata mellan varje bit.
    Håller minnet nere på Render Starter (512 MB) -> inga OOM-omstarter."""
    tickers = UNIVERSE.get(theme_key, []) if theme_key else ALL_TICKERS
    out = []
    for i in range(0, len(tickers), SCAN_CHUNK):
        chunk = tickers[i:i + SCAN_CHUNK]
        prefetch(chunk)               # hämta bara denna bit
        for t in chunk:
            a = scan(t)
            if a:
                a["hetta"] = hetta_of(a)
                out.append(_jsonable(a))
        _PREFETCH.clear()             # släpp råa prisdataframes direkt
        _gc.collect()                 # ge minnet tillbaka till OS
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

# Gzip-komprimering: kapar 1.24 MB index.html till ~250-600 KB och krymper all JSON.
app.add_middleware(GZipMiddleware, minimum_size=600)


# index.html: tillåt cache MEN revalidera varje gång (no-cache + ETag).
# -> aterbesok = 304 (laddar direkt), efter deploy = ny ETag = farsk fil.
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    import os
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if not os.path.exists(p):
        return HTMLResponse("<h1>GRABIT API</h1><p>index.html saknas i repot.</p>",
                            headers={"Cache-Control": "no-cache"})
    st = os.stat(p)
    etag = f'W/"{int(st.st_mtime)}-{st.st_size}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    with open(p, encoding="utf-8") as fh:
        html = fh.read()
    return HTMLResponse(html, headers={"ETag": etag, "Cache-Control": "no-cache"})


# =====================================================================
#  PWA  ·  manifest, service worker, ikoner (installerbar app)
# =====================================================================
import base64 as _b64

_ICON_192 = "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAYAAABS3GwHAAADzklEQVR42u3dsVUbQRRG4Z1XABGJqcDdUKu7cQVyokgN4NSBQbvSCu28/7sZh0TM3DtvFh3EWA7Gy+vbx4K2XM6ncaTXM8iO5CgG6ZEcwyA+kkMYxEdyCIP4SA5hEB/JIRT5MRN7OzaIj+RpUORH8jQo8iM5giI/kiMo8iM5giI/kiMo8iM5giI/kiMo8iM5giI/kiMoy4RkyumP5ClQ5EdyBK5AcAVy+iN1CpgAMAGc/kidAiYATACnP1KngAkAE8Dpj9QpYALABACiA3D9Qeo1yASACQAIABAAkMXwAAwTABAAIABAAIAAAAFgb/78+nnX9yGA6eX/TPJr34cA2pz8W7+GANpde9ZOBDwO7wQ/+c6/hh/vvy2kCZApv0kggGj5RSCAePlFIIB4+UUggHj5RSCAePlFIIB4+UUggHj5RSCAFvLv8SaXCAQwtfwiEED8yS8CAcRfe0QggPg7vwgEEP/AKwIBxMovAgHEyy8CAcTLLwIBxMsvAgHEyy8CAcTLLwIBxMsvAgHEyy8CAcTLLwIBxMsvgv2I/WCsvTb+CB9a5cO3TIBY+U0CAUTLLwIBxMsvAgHEyy8CAcTLLwIBxMsvAgHEyy+C9bR9H8Dvxq1F7ASw4SZBbADkF0FsAOQXQWwA5BdBbADkF0FsAOQXQWwA5BdBbADkF0FsAOQXQWwA5BdBbADkF0FsAOQXQWwA5BdBbADkF0FsAOQXwaM57N8DdJP/lp/nKK+/80FU5EfyJCjyIzmCIj+SIyjyIzmC6rKw5LdX01+BnvnvRJG5VzX7wpLfXrUKYMtCkd9etQxgzYKR3161DuCrhSO/vYoI4H8LSH57FRXAvwtJfnsVGQD5ez4YCwAQACAAQACAAAABAAIABAAIABAAIABAAIAAAAEAAgAEAAgAEAAgAEAAgAAAAQACAAQACAAQACAACAAQACAAQACAAAABAO0ZL69vH5YBJgAgAEAAgAAAAQACAAQACAAQACAAQACAAAABALMGcDmfhmVAIpfzaZgAcAUCBAAIAAgMwIMwEh+ATQCYAJYAAnANQuD1xwSACfBZGUD3098EgAlwrRCg6+lvAsAEWFsK0O30NwFgAmwtBuhy+l+dACJAZ/ldgeAKdG9BwKyn/+oJIAJ0lH/TFUgE6Cb/5mcAEaCT/Dc9BIsAXeS/KQARoIv8NwcgAnSQ/64ARIDZ5b87ABFgZvmXZVl2ldd/nccs4u82AUwDzCr/7hPANMAs4j88ACFghlvFt11ZhIAjXqe//c4uBBzpOfKpD61iwDOkP0wAoiD7s/kLPGlENkWCP54AAAAASUVORK5CYII="
_ICON_512 = "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"
_ICON_180 = "iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAYAAAA9zQYyAAADc0lEQVR42u3dsXETYRSF0f1fAY6c4AroxrXSDRWIRJEasFMCC0srsfu/u+fLGALM7pk7zzAjj2WCXl7fPha173I+jb2/hgGwkoAPiJWEe0CsJNwDZCXBLpiV9I8BA2QlrXXBrKS1HiAraa0LZiWtdcGsJNQFs5JQF8xKQl0wKwl1wawk1AWzklCXx6SkyjoraaULZiWhdnIo/+Swzuq60gWzklA7OZR7clhndV9pC638bwql9qCdG0o4Oyy0nBzS1KCdG0o5Oyy0nBwS0NIGDfezLLQEtAS0BLSAloDW9f78+vnQ7wvo6TBfQ/vd7wvoaZf53l8L6OnPjFsXW4/nfwo3uplv6cf7bw/SQmdgttRAR2GGGug4zFADHYcZaqDjMEMNdBxmqIGOxQU10HGooAZ6KkzP+E8TqIGeCjPUQMctM9RAx50ZUAMddzNDDXTcN4BQAx2DGWqg4zBDDXQcZqiBjsMMNdBxmKEGOg4z1EDHYYYa6DjMUAMdhxlqoOMwQw10HGaogY7DDDXQcZihPjjo5M+ag/pgoI/wwYlQHwT0kT4FFOpw0Ef8SFuoQ0Ef+fOZoQ4D7cPGoY4BDTPUMaBhhjoGNMxQx4CGGeoY0DBDHQMaZqhjQMMMdQxomKGOAQ0z1DGgYYY6BjTMUMeAhhnqtY2X17cPmPf9O+z59aaNR8FsqZOWumBWEuqCWUmoC2Yloa7uDxFm72O6k2PPn02irPdRXR8izN7H1KDveSgwex8tQN/ycGD2PlqB/tdDgtn7aAn6q4cFs/fRGvTfDw1m7yMCNMzeRxxoCWgBLQEtAS0BLQEtoCWgJaAloCWgBbQEtAS0BLQEtICWgJaAloCWgBbQEtAS0BLQEtACWgJaAlpa2XQ/61uy0BLQAloCWgJaAloCWkBLQEtAS0ALaAloaT7Ql/NpeAxK6HI+DQstJ4cEtLQVaHe0Eu5nCy0nh9QCtLND3c8NCy0nh9QGtLNDnc8NC638k8NKq+s6X11oqNURs5ND+SeHlVbXdf52oaFWJ8xODh3n5LDS6rbONy801OqA+a6TA2rNjvnuGxpqzYx51TeFUGtWzKtAQ61ZMa8GDbVmxLwsy/IUlH40nPaG/PBCW2vNhvlpC22tNcsY1uxfoGDedaGttfYcvc3WFG6It/hzdjkP4IY4CjTgAP+vPgGQBh+snTnKGAAAAABJRU5ErkJggg=="
_ICON_MASK = "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAAKmElEQVR42u3cwW3j2BJAUdEBcMWNGIGzUazORhk4DHrtlQCLj6Rxz1l/NHroV1W3G39mmpd1uwEAKR8+AQAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAAAeATAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAABAAAIAABAAAAAAgAAEAAAgAAATvD99fmvf31AAAB/PM6jjvToXx8QAMCbfzLf+0iP/vUBAQC8eZz3PtKjf31AAAA7Hee9jvToXx8QAMDOx/ndIz361wcEADDoOF/1fw8IAGDw8b/a3xgAAgA4+Mie/f8ZAAQAcNJxPevfGgAEAHDyUT36vxsACADgIsf0qP9yIHBN07ysm88AreN/pPvj6QcJ/gYAqP0J2t8EgAAAokdTBIAAAKLHUgSAAACiR1IEgAAAosdRBIAAAKJHUQSAAADHP3oMRQAIAHD8/fMDAgAcP98BEADg6PkegAAAx853AQQAOHK+DyAAwHHznQABAI6a7wUIAHDMfDdAAIAj5vuBAAAcL98RBADgaPmeIAAAx8p3BQEAOFK+LwgAwHHynUEAgKOE7w0CABwjfHcQAOAI4fuDAADH5zz3x9PPAQQAUDz+IgAEABD9k78IAAEAxI6/CAABAESPvwgAAQBEj78IAAEARI+/CAABANza/6qfCAABAI5/7PiLABAA4PhHj78IAAEAjn/0+IsAEADg+EePvwgAAQCOf/T4iwAQAOD4R4+/CAABAI5/9PiLABAA4PhHj78IAAEAln/0+IsAEABg6cePrwgAAQCWffToigAQAGDJR4+tCAABAJZ79MiKABAAYKlHj6sIAAEAlnn0qIoAEABgiUePqQgAAQCWd/SIigAQAGBpB4+nCAABAJZ18PiLABAAYElHj78IAAEAlnP0+IsAEABgKUePvwgAAQCWcfT4iwAQAGAJR4+/CAABAI5/9PiLABAA4PjHiQAQAOD4iwDvEgQAOP4iwPsEAYDj7/iLAO8UBACOv+MmArxXEAA4/o6aCPBuQQDg+Dtmvpv3CwIAy9MR8/0AAYDj73j5jt4yAgAsTEfL9/SmEQBgUTr+vqu3jQAAC9Lx9329cQQAWIyOv+8sAhAAYCE6/r63CEAAgOOP7y4CEADg+OP7iwAEADj++DmIAAQAjr+jg5+HCEAA4Pg7NogAEAA4/o4MIgAEAI6/44IIAAGAheaoIAJAAGCROSaIABAAWGCOCCIABAAWl+MvAswSCAAsLMdfBJgpEABYVI6/CDBbCABw/P0gRYAZQwCA448IMGsIAHD8EQFmDgEAjj9+7mYPAYDj7wjg528GEQA4/pY/mEUEABaO44+3YCYRAFg0Fj7ehNlEAGDBWPR4G2YUAYDFYsHjjYgABAAWisWOtyICEABYJI4/3owIQABggTj+eDsiAAGAxeH44w2JAAQAjj+IABGAAMDxBxEAAgDHH0QAAgDH34IGEYAAwPG3mEEEIABw/C1kEAEIABx/ixgRYAcgADD4FjAiwC5AAGDgLV5EgJ2AAMCgW7iIALsBAYABd/wRAXYEAgCD7fgjAuwKBAAG2vHHW7UzEAAYZMcfb9buQADg+IO3a4cgAHD8wRu2SxAAOP7gLdspCADH38IEb9puQQA4/hYleNsiAAHg+FuQ4I2LAAGAgbQYwVsXAQIAg2ghgjcvAgQABtAiBG9fBAgADJ4FCGZABAgADJzjDyIAAYBBc/xBBCAAMGCOP4gABACOPyACEAA4/oAIQADg+AMiAAHg+FtkIALsMASA42+BgQiwy9jHNC/r5jMYGIvLO/Hz9rP2RvwNAAbdoIOZstsEAAbE8QezZccJAAyG4w9mTAQIAAyE4w9mTQQIABx/wMyJAAGA4w+YPREgAHD8ATMoAgSA42/xAGZRBAgAx9/CAUQAAsDxt2gAEYAA8LAtGEAEIAAMpt8/YNcgADxsv2/ArkEAeOAGEuwav18EgIduIMGu8ftEAHjwBhLsGr8/AYCHbyDBrvH7EgC0B8BAgl1j1wgAYoNgIMGusWsEALGBMJBg19g1AoDYYBhIsGvsGgFAbDANJNg1do0AIDaYBhLsGrtGABAbTAMJ2DUCgNhgGkjArkEAxAbTQAJ2DQIgNpgGErBrEACxwTSQgF2DAIgNpoEE7BoEQGwwDSRg1yAAYoNpIAG7BgEQG0wDCdg1CIDYYBpIwK5BAMQG00ACdg0CIDaYBhKwaxAA8ToHsGsQAACAAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAwC/TvKybzwAA/gYAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAAPDaD3jc7dYNthF4AAAAAElFTkSuQmCC"

def _png(b64s):
    return Response(content=_b64.b64decode(b64s), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=604800"})

@app.get("/icon-192.png")
def _icon192(): return _png(_ICON_192)

@app.get("/icon-512.png")
def _icon512(): return _png(_ICON_512)

@app.get("/icon-180.png")
def _icon180(): return _png(_ICON_180)

@app.get("/icon-maskable.png")
def _iconmask(): return _png(_ICON_MASK)

_MANIFEST = {
    "name": "GRABIT",
    "short_name": "GRABIT",
    "description": "Spot the setup. Ignore the noise.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#080D10",
    "theme_color": "#080D10",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

@app.get("/manifest.webmanifest")
def _manifest():
    import json as _j
    return Response(content=_j.dumps(_MANIFEST), media_type="application/manifest+json",
                   headers={"Cache-Control": "no-cache"})

_SW_JS = """
const V = 'grabit-v1';
const SHELL = ['/', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png', '/icon-180.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(V).then(c => c.addAll(SHELL).catch(()=>{})).then(()=>self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k=>k!==V).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.pathname.startsWith('/api/')) return;              // API: alltid nätverk
  if (req.mode === 'navigate') {                              // appskal: visa cache direkt, uppdatera i bakgrunden
    e.respondWith(caches.open(V).then(c => c.match('/').then(hit => {
      const net = fetch(req).then(res => { if (res && res.status === 200) c.put('/', res.clone()); return res; }).catch(()=>hit);
      return hit || net;
    })));
    return;
  }
  e.respondWith(caches.open(V).then(c => c.match(req).then(hit => {  // statiska resurser: cache-first + bakgrundsuppdatering
    const net = fetch(req).then(res => { if (res && res.status === 200 && res.type !== 'opaque') c.put(req, res.clone()); return res; }).catch(()=>hit);
    return hit || net;
  })));
});
"""

@app.get("/sw.js")
def _sw():
    return Response(content=_SW_JS, media_type="application/javascript",
                   headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})




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


# ---- Research / screener (skanner + Alpaca-nyheter + Finnhub) -------
def _alpaca_news_counts(symbols):
    """Antal farska nyheter per symbol via Alpaca (om APCA-nycklar finns). Tyst fallback {}."""
    import os
    key = os.getenv("APCA_API_KEY_ID", ""); sec = os.getenv("APCA_API_SECRET_KEY", "")
    if not (key and sec) or not symbols:
        return {}
    try:
        import requests
        r = requests.get(
            "https://data.alpaca.markets/v1beta1/news",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
            params={"symbols": ",".join([x for x in symbols[:40] if x]), "limit": 50, "sort": "desc"},
            timeout=8,
        )
        cnt = {}
        for n in (r.json().get("news") or []):
            for sym in n.get("symbols", []):
                cnt[sym] = cnt.get(sym, 0) + 1
        return cnt
    except Exception:
        return {}


# Finnhub: market cap + insiderkop (gratis-nivan). Nyckel = env FINNHUB_API_KEY.
_FH_CACHE = {}

def _finnhub(path, params):
    import os, requests
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        return None
    try:
        p = dict(params); p["token"] = key
        r = requests.get("https://finnhub.io/api/v1" + path, params=p, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def _fh_market_cap(ticker):
    """Market cap i miljoner USD (eller None)."""
    k = ("mcap", ticker)
    if k in _FH_CACHE:
        return _FH_CACHE[k]
    j = _finnhub("/stock/profile2", {"symbol": ticker})
    mc = (j or {}).get("marketCapitalization")
    _FH_CACHE[k] = mc
    return mc

def _fh_profile(ticker):
    """Finnhub profile2 (gratis, exakt per ticker): namn, bransch, land. Auktoritativ identitet."""
    k = ("prof", ticker)
    if k in _FH_CACHE:
        return _FH_CACHE[k]
    j = _finnhub("/stock/profile2", {"symbol": ticker}) or {}
    out = {"name": (j.get("name") or "").strip(),
           "industry": (j.get("finnhubIndustry") or "").strip(),
           "country": (j.get("country") or "").strip(),
           "logo": j.get("logo") or "", "weburl": j.get("weburl") or ""}
    _FH_CACHE[k] = out
    return out


def _fh_insider_buys(ticker):
    """Antal insider-KOP (transactionCode P, positiv forandring)."""
    k = ("ins", ticker)
    if k in _FH_CACHE:
        return _FH_CACHE[k]
    j = _finnhub("/stock/insider-transactions", {"symbol": ticker})
    buys = 0
    for t in (j or {}).get("data", []):
        code = (t.get("transactionCode") or "").upper()
        if code == "P" and (t.get("change") or 0) > 0:
            buys += 1
    _FH_CACHE[k] = buys
    return buys


@app.get("/api/research")
def research(max_price: float = 15.0, min_relvol: float = 1.3,
             min_momentum: float = 0.0, themes: str = "",
             needs_news: bool = False, max_mcap: float = 0.0,
             insider_buys: bool = False, limit: int = 40):
    """Filtrerar hela universumet. Tekniskt (kurs/relvol/momentum/tema) gar direkt;
       nyheter via Alpaca; market cap + insiderkop via Finnhub (om nyckel finns)."""
    import os
    has_fh = bool(os.getenv("FINNHUB_API_KEY", ""))
    rows = scan_universe(None) or []
    want = set(t.strip().lower() for t in themes.split(",") if t.strip())
    hits = []
    for r in rows:
        last = r.get("last") or 0
        if max_price and last and last > max_price:              # Kurs under $X
            continue
        if min_relvol and (r.get("rel_vol") or 0) < min_relvol:  # Hog relativ volym
            continue
        if min_momentum and (r.get("momentum") or 0) < min_momentum:  # Kraftigt momentum
            continue
        if want:                                                 # Tema-exponering
            th = (r.get("theme") or "").lower()
            if not any(w in th for w in want):
                continue
        hits.append(r)

    # Rank tekniskt forst -> Finnhub/Alpaca bara pa toppkandidaterna (snallt mot rate-limit)
    def _rs0(r):
        return (r.get("rel_vol", 0) * 2.0) + (r.get("momentum", 0) * 0.1) + (r.get("score10", 0) * 1.0)
    hits.sort(key=_rs0, reverse=True)
    top = hits[:max(limit, 30)]

    # Nyheter (Alpaca)
    news = _alpaca_news_counts([r.get("ticker") for r in top])
    for r in top:
        r["news_count"] = news.get(r.get("ticker"), 0)

    # Finnhub-berikning (kapat till 25 for att halla oss under gratis-rate-limit)
    use_fh = has_fh and ((max_mcap and max_mcap > 0) or insider_buys)
    if use_fh:
        for r in top[:25]:
            tk = r.get("ticker")
            if max_mcap and max_mcap > 0:
                r["market_cap"] = _fh_market_cap(tk)
            if insider_buys:
                r["insider_buys"] = _fh_insider_buys(tk)

    # Filter som kraver berikning (hoppas tyst over om data saknas)
    out = []
    for r in top:
        if needs_news and r.get("news_count", 0) <= 0:
            continue
        if has_fh and max_mcap and max_mcap > 0:
            mc = r.get("market_cap")
            if mc is None or mc > max_mcap:                      # market cap i miljoner USD
                continue
        if has_fh and insider_buys and r.get("insider_buys", 0) <= 0:
            continue
        out.append(r)

    def _rs(r):
        return _rs0(r) + (r.get("news_count", 0) * 0.8) + (r.get("insider_buys", 0) * 0.6)
    out.sort(key=_rs, reverse=True)
    return {
        "count": len(out),
        "finnhub": has_fh,
        "filters": {"max_price": max_price, "min_relvol": min_relvol,
                    "min_momentum": min_momentum, "themes": sorted(want),
                    "needs_news": needs_news, "max_mcap": max_mcap,
                    "insider_buys": insider_buys},
        "rows": out[:limit],
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "tickers": len(ALL_TICKERS), "yfinance": yf is not None}


@app.get("/api/robber/test")
def robber_test():
    """Tvingar fram ett Telegram-testmeddelande. Oppna i webblasaren for att
    verifiera hela kedjan (token + chat_id). Visar config-status i svaret."""
    try:
        from nasdaq_robber import send_telegram, format_alert, Config
    except Exception as e:
        return {"sent": False, "error": "kunde inte importera nasdaq_robber: " + str(e)}

    cfg = {
        "telegram_token": bool(Config.TELEGRAM_TOKEN),
        "chat_id": bool(Config.CHAT_ID),
        "alpaca_keys": bool(Config.ALPACA_KEY and Config.ALPACA_SECRET),
        "tickers": Config.TICKERS,
        "min_score": Config.MIN_SCORE,
    }
    if not cfg["telegram_token"] or not cfg["chat_id"]:
        return {"sent": False, "reason": "TELEGRAM_TOKEN och/eller CHAT_ID saknas i Render-miljon", "config": cfg}

    sample = {
        "ticker": "QQQ", "side": "LONG", "score": 6, "max_score": 7,
        "price": 512.34, "atr": 1.85, "stop": 509.10, "risk_per_share": 3.24,
        "targets": [517.20, 520.44, 525.30], "shares": 30, "bias": "LONG",
        "reasons": ["HTF-bias LONG", "Pris > EMA20 > EMA50", "RSI momentum (58)"],
        "bar_time": "test",
    }
    try:
        msg = "\U0001F9EA <b>TESTSIGNAL</b> (manuell) \u2014 ser du detta funkar hela Telegram-kedjan.\n\n" + format_alert(sample)
        ok = send_telegram(msg)
        return {"sent": bool(ok), "config": cfg}
    except Exception as e:
        return {"sent": False, "error": str(e), "config": cfg}


@app.get("/api/robber/status")
def robber_status():
    """Visar om skannings-traden lever och vad senaste skanningen gav.
    thread_alive=false betyder att roboten ALDRIG startade (fel startkommando
    eller saknade Alpaca-nycklar) — aven om /api/robber/test fungerar.
    shadow_tail = senaste kandidaterna (aven under trosklen) med confidence,
    sa man ser direkt om setups finns men stoppas av CONF_MIN_SEND."""
    import threading
    import json as _json
    alive = any(t.name == "nasdaq-robber" and t.is_alive() for t in threading.enumerate())
    out = {"thread_alive": alive}
    try:
        import nasdaq_robber as R
        out["status"] = R.STATUS
        out["config"] = {
            "telegram": bool(R.Config.TELEGRAM_TOKEN and R.Config.CHAT_ID),
            "alpaca_keys": bool(R.Config.ALPACA_KEY and R.Config.ALPACA_SECRET),
            "tickers": R.Config.TICKERS,
            "min_score": R.Config.MIN_SCORE,
            "conf_min_send": R.Config.CONF_MIN_SEND,
        }
        # Senaste 15 kandidaterna ur shadow-loggen (nyast forst)
        try:
            with open(R.Config.SHADOW_LOG) as f:
                rows = [_json.loads(x) for x in f.readlines()[-15:] if x.strip()]
            out["shadow_tail"] = [
                {"ts": r.get("ts"), "side": r.get("side"),
                 "score7": r.get("score7"), "confidence": r.get("confidence"),
                 "sent": r.get("sent")} for r in reversed(rows)]
            confs = [r.get("confidence") or 0 for r in rows]
            out["shadow_best_conf"] = max(confs) if confs else None
        except Exception:
            out["shadow_tail"] = []
            out["shadow_note"] = ("Ingen shadow-logg hittad — antingen inga kandidater "
                                  "sedan senaste deploy, eller sa saknas Persistent Disk "
                                  "(DATA_DIR) sa filen nollstalldes.")
        if not alive:
            out["hint"] = ("Traden lever inte. Kontrollera att Render Start Command ar "
                           "'uvicorn grabit_entry:app ...' OCH att APCA_API_KEY_ID/APCA_API_SECRET_KEY finns.")
    except Exception as e:
        out["error"] = str(e)
    return out


_PM_CACHE = {"t": 0.0, "data": None}

@app.get("/api/polymarket")
def polymarket(limit: int = 24, show_all: bool = False):
    """Trendande Polymarket-marknader via publika Gamma API (ingen auth).
    Filtrerar till aktier/index/ravaror/crypto (sport & politik bort) om inte show_all=true."""
    import time, json as _json, requests
    try:
        from nasdaq_robber import poly_relevant as _rel
    except Exception:
        _rel = lambda x: True
    now = time.time()
    if (not show_all) and _PM_CACHE["data"] is not None and now - _PM_CACHE["t"] < 60:
        return {"markets": _PM_CACHE["data"], "cached": True}
    lim = max(1, min(int(limit), 50))
    try:
        r = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"closed": "false", "active": "true",
                    "order": "volume24hr", "ascending": "false", "limit": 200},
            headers={"User-Agent": "grabit/1.0"},
            timeout=12,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return {"markets": [], "error": str(e)}

    out = []
    for m in (raw or []):
        try:
            q = m.get("question") or m.get("title") or ""
            if (not show_all) and not _rel(q):
                continue
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                prices = _json.loads(prices or "[]")
            outcomes = m.get("outcomes")
            if isinstance(outcomes, str):
                outcomes = _json.loads(outcomes or "[]")
            yes = float(prices[0]) if prices else None
            if yes is not None:
                no = float(prices[1]) if len(prices) > 1 else round(1.0 - yes, 4)
            else:
                no = None
            out.append({
                "question": q,
                "slug": m.get("slug"),
                "outcomes": outcomes,
                "yes": yes,
                "no": no,
                "yesPct": round(yes * 100) if yes is not None else None,
                "volume": float(m.get("volumeNum") or m.get("volume") or 0),
                "volume24h": float(m.get("volume24hr") or m.get("volume24hrClob") or 0),
                "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
                "endDate": m.get("endDate"),
                "category": m.get("category"),
                "icon": m.get("icon") or m.get("image"),
            })
            if len(out) >= lim:
                break
        except Exception:
            continue
    if not show_all:
        _PM_CACHE["t"] = now
        _PM_CACHE["data"] = out
    return {"markets": out, "cached": False}






@app.get("/api/polymarket/insiders")
def polymarket_insiders(min_usd: float = 5000, limit: int = 40, show_all: bool = False):
    """Slimmad insider/smart-money-detektor: stora taker-trades fran Polymarkets Data API.
    Filtrerar till dina teman (aktier/index/makro/krypto/ravaror) om inte show_all=true."""
    import requests
    try:
        from nasdaq_robber import poly_relevant as _rel
    except Exception:
        _rel = lambda x: True
    lim = max(1, min(int(limit), 100))
    try:
        r = requests.get(
            "https://data-api.polymarket.com/trades",
            params={"takerOnly": "true", "filterType": "CASH",
                    "filterAmount": float(min_usd), "limit": lim},
            headers={"User-Agent": "grabit/1.0"},
            timeout=12,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return {"trades": [], "error": str(e)}

    out = []
    for t in (raw or []):
        try:
            title = t.get("title") or ""
            if not show_all and not _rel(title):
                continue
            size = float(t.get("size") or 0)
            price = float(t.get("price") or 0)
            usd = size * price
            out.append({
                "wallet": t.get("proxyWallet"),
                "trader": t.get("name") or t.get("pseudonym") or "",
                "side": t.get("side") or "",
                "outcome": t.get("outcome"),
                "usd": round(usd),
                "size": round(size),
                "price": round(price, 4),
                "question": title,
                "slug": t.get("slug"),
                "eventSlug": t.get("eventSlug"),
                "icon": t.get("icon"),
                "ts": t.get("timestamp"),
                "tx": t.get("transactionHash"),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["usd"], reverse=True)
    return {"trades": out, "min_usd": min_usd, "count": len(out)}


@app.get("/api/polymarket/test-alert")
def polymarket_test_alert(min_usd: float = 1000):
    """Tvingar fram ett Polymarket-Telegram-larm pa den storsta aktuella traden.
    Verifierar hela kedjan: Polymarket Data API -> format -> Telegram."""
    import requests
    try:
        from nasdaq_robber import send_telegram, Config
    except Exception as e:
        return {"sent": False, "error": "import nasdaq_robber: " + str(e)}
    if not (Config.TELEGRAM_TOKEN and Config.CHAT_ID):
        return {"sent": False, "reason": "TELEGRAM_TOKEN/CHAT_ID saknas i miljon"}
    try:
        r = requests.get(
            "https://data-api.polymarket.com/trades",
            params={"takerOnly": "true", "filterType": "CASH",
                    "filterAmount": float(min_usd), "limit": 20},
            headers={"User-Agent": "grabit/1.0"}, timeout=12,
        )
        r.raise_for_status()
        rows = r.json() or []
    except Exception as e:
        return {"sent": False, "error": str(e)}
    if not rows:
        return {"sent": False, "reason": f"inga trades over ${min_usd:.0f} just nu"}

    def _usd(t):
        try:
            return float(t.get("size") or 0) * float(t.get("price") or 0)
        except Exception:
            return 0.0
    t = max(rows, key=_usd)
    usd = _usd(t)
    who = t.get("name") or t.get("pseudonym") or (str(t.get("proxyWallet") or "")[:8])
    msg = ("\U0001F9EA <b>TEST \u2013 Polymarket stor trade</b>\n"
           f"{t.get('side','BUY')} {t.get('outcome','')} \u00b7 ${usd:,.0f}\n"
           f"{t.get('title','')}\n"
           f"Trader: {who}")
    ok = send_telegram(msg)
    return {"sent": bool(ok), "usd": round(usd), "question": t.get("title")}


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
    """Kommande rapportdatum via Finnhub (funkar på Render, kräver FINNHUB_API_KEY)."""
    import datetime as _dt
    today = _dt.date.today()
    horizon = today + _dt.timedelta(days=45)
    base = list(dict.fromkeys(UNIVERSE.get("Bevakning", []) + ALL_TICKERS))[:30]
    out = []
    for t in base:
        j = _finnhub("/calendar/earnings",
                     {"symbol": t, "from": str(today), "to": str(horizon)})
        if not j:
            continue
        for e in (j.get("earningsCalendar") or []):
            ds = (e.get("date") or "")[:10]
            if not ds:
                continue
            try:
                d = _dt.date.fromisoformat(ds)
                days = (d - today).days
            except Exception:
                continue
            if 0 <= days <= 45:
                out.append({"tkr": t, "date": str(d), "days": days})
                break
    out.sort(key=lambda x: x["days"])
    return out[:8]


@app.get("/api/events")
def events():
    return {"events": _events_list()}


# ----- MAKROKALENDER (gratis, ingen nyckel — ForexFactory veckofeed) --
_FF_IMPACT = {"High": "High", "Medium": "Medium", "Low": "Low", "Holiday": "Low"}
_FF_COUNTRY_FLAG = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CHF": "🇨🇭",
    "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿", "CNY": "🇨🇳", "SEK": "🇸🇪",
}

_MACRO_LAST_GOOD = {"ts": 0.0, "events": [], "err": "", "src": ""}

_MACRO_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9,sv;q=0.8",
    "Referer": "https://www.forexfactory.com/",
    "Cache-Control": "no-cache",
}
_MACRO_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
]

def _macro_events():
    """Kommande makrohändelser (räntebesked, CPI, NFP osv), gratis ForexFactory-feed.
    Cloudflare blockerar requests utan browser-headers -> vi skickar riktiga headers
    och provar två hosts. Senast lyckade svar behålls i minnet som fallback."""
    import requests
    import datetime as _dt
    now_ts = time.time()
    # Färskt nog? (2h) -> använd cache direkt, spara anrop
    if _MACRO_LAST_GOOD["events"] and now_ts - _MACRO_LAST_GOOD["ts"] < 7200:
        return _MACRO_LAST_GOOD["events"]
    raw, errs = None, []
    for url in _MACRO_URLS:
        try:
            r = requests.get(url, headers=_MACRO_HEADERS, timeout=8)
            if r.status_code != 200:
                errs.append("%s -> HTTP %s" % (url.split("//")[1].split("/")[0], r.status_code))
                continue
            raw = r.json()
            _MACRO_LAST_GOOD["src"] = url
            break
        except Exception as e:
            errs.append("%s -> %s" % (url.split("//")[1].split("/")[0], type(e).__name__))
    _MACRO_LAST_GOOD["err"] = "; ".join(errs)
    if raw is None:
        return _MACRO_LAST_GOOD["events"]  # senast kända istället för tomt
    out = []
    now = _dt.datetime.utcnow()
    for e in (raw or []):
        impact = _FF_IMPACT.get(e.get("impact", ""), "Low")
        if impact == "Low":
            continue  # bara Medium/High är relevant att visa
        title = (e.get("title") or "").strip()
        country = e.get("country") or ""
        if not title:
            continue
        ts = e.get("date") or ""  # ISO 8601, t.ex. 2026-07-03T12:30:00-04:00
        try:
            dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            date_s = dt.strftime("%d %b")
            time_s = dt.strftime("%H:%M")
            if dt.replace(tzinfo=None) < now - _dt.timedelta(hours=6):
                continue  # redan passerat
        except Exception:
            date_s, time_s = "", ""
            dt = None
        out.append({
            "date": date_s, "time": time_s,
            "country": _FF_COUNTRY_FLAG.get(country, country),
            "title": title, "impact": impact,
            "forecast": e.get("forecast") or "", "previous": e.get("previous") or "",
            "_sort": dt.isoformat() if dt else "9999",
        })
    out.sort(key=lambda x: x["_sort"])
    for e in out:
        e.pop("_sort", None)
    out = out[:15]
    if out:
        _MACRO_LAST_GOOD["events"] = out
        _MACRO_LAST_GOOD["ts"] = now_ts
    return out or _MACRO_LAST_GOOD["events"]


@app.get("/api/macro")
def macro():
    return {"events": _macro_events()}


@app.get("/api/macro/debug")
def macro_debug():
    """Felsökning: visar senaste fel, källa och cache-status för makrokalendern."""
    ev = _macro_events()
    return {
        "events_count": len(ev),
        "last_error": _MACRO_LAST_GOOD.get("err", ""),
        "source": _MACRO_LAST_GOOD.get("src", ""),
        "cache_age_s": int(time.time() - _MACRO_LAST_GOOD["ts"]) if _MACRO_LAST_GOOD["ts"] else None,
    }


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


def _index_quote(ticker):
    """(senaste pris, dagsforandring %) for ett index/krypto via dagliga barer."""
    try:
        df, _ = fetch(ticker)
    except Exception:
        return None, 0.0
    if df is None or len(df) < 2:
        return None, 0.0
    c = df["Close"].dropna()
    if len(c) < 2:
        return None, 0.0
    last = float(c.iloc[-1])
    prev = float(c.iloc[-2])
    pct = (last / prev - 1) * 100 if prev else 0.0
    return last, round(pct, 2)


def _fmt_idx_price(p):
    if p is None:
        return "—"
    if p >= 10000:
        return f"{p:,.0f}"
    if p >= 1000:
        return f"{p:,.1f}"
    return f"{p:,.2f}"


@app.get("/api/indices")
def indices():
    out = []
    for name, tk in INDICES:
        price, pct = _index_quote(tk)
        out.append({"name": name, "tk": tk, "price": price,
                    "priceStr": _fmt_idx_price(price), "pct": pct})
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


# ---------- AKTIEKORT: nyheter + analytiker (riktkurs/konsensus) ----------
@cached(900)
def _company_news(ticker):
    """Bolagsnyheter via Finnhub /company-news (gratis-nivå)."""
    import datetime as _dt
    if not os.getenv("FINNHUB_API_KEY"):
        return []
    today = _dt.date.today()
    frm = today - _dt.timedelta(days=14)
    j = _finnhub("/company-news", {"symbol": ticker, "from": str(frm), "to": str(today)})
    out = []
    for n in (j or []):
        h = (n.get("headline") or "").strip()
        if not h:
            continue
        out.append({
            "headline": h,
            "source": n.get("source", "") or "",
            "url": n.get("url", "") or "",
            "ts": int(n.get("datetime", 0) or 0),
            "image": n.get("image", "") or "",
        })
        if len(out) >= 6:
            break
    return out


@cached(1800)
def _analyst(ticker):
    """Analytiker: konsensus (gratis recommendation) + riktkurs (price-target, ofta premium)."""
    out = {"target": None, "high": None, "low": None,
           "consensus": None, "rating": None, "n": None,
           "source": None, "asof": None, "currency": "USD"}
    rec = _finnhub("/stock/recommendation", {"symbol": ticker})
    if isinstance(rec, list) and rec:
        r = rec[0]
        sb = int(r.get("strongBuy", 0) or 0); b = int(r.get("buy", 0) or 0)
        h = int(r.get("hold", 0) or 0); se = int(r.get("sell", 0) or 0)
        ss = int(r.get("strongSell", 0) or 0)
        tot = sb + b + h + se + ss
        out["consensus"] = {"strongBuy": sb, "buy": b, "hold": h, "sell": se, "strongSell": ss}
        out["n"] = tot
        if tot:
            sc = (sb * 2 + b - se - ss * 2) / tot
            out["rating"] = ("Starkt köp" if sc >= 1.2 else "Köp" if sc >= 0.4
                             else "Håll" if sc > -0.4 else "Sälj" if sc > -1.2 else "Starkt sälj")
    pt = _finnhub("/stock/price-target", {"symbol": ticker})
    if isinstance(pt, dict) and pt.get("targetMean"):
        out["target"] = pt.get("targetMean")
        out["high"] = pt.get("targetHigh")
        out["low"] = pt.get("targetLow")
        out["source"] = "finnhub"
    # Grabit Riktkurs (AI) hämtas separat via /api/stock/{ticker}/target -> kortet laddar snabbt.
    return out


@cached(1800)
def _company_insider(ticker):
    """Senaste insider-transaktioner (Finnhub, gratis). P=köp, S=sälj."""
    j = _finnhub("/stock/insider-transactions", {"symbol": ticker})
    out = []
    for t in (j or {}).get("data", []):
        code = (t.get("transactionCode") or "").upper()
        if code not in ("P", "S"):
            continue
        chg = t.get("change") or 0
        out.append({
            "name": (t.get("name") or "").strip(),
            "buy": code == "P",
            "shares": abs(int(chg)) if chg else 0,
            "price": t.get("transactionPrice") or 0,
            "date": (t.get("transactionDate") or "")[:10],
        })
        if len(out) >= 14:
            break
    return out


@app.get("/api/stock/{ticker}/extras")
def stock_extras(ticker: str):
    ticker = ticker.upper()
    return {"news": _company_news(ticker), "analyst": _analyst(ticker), "insider": _company_insider(ticker)}


@app.get("/api/stock/{ticker}/target")
def stock_target(ticker: str):
    """Grabit Riktkurs (AI + webbsök) — separat endpoint så aktiekortet visar resten direkt."""
    tk = ticker.upper()
    ai = _ai_price_target(tk, _fh_profile(tk).get("name", ""))
    if ai and ai.get("target"):
        return {"target": ai["target"], "high": ai["high"], "low": ai["low"],
                "asof": ai.get("asof") or None, "currency": ai.get("currency") or "USD",
                "source": "grabit"}
    return {"target": None, "source": None}


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

    import datetime as _dt
    _today = _dt.date.today().isoformat()
    sys_live = (SYSTEM_PROMPT +
                f"\n\nDagens datum är {_today}. Du har tillgång till webbsökning. "
                "Använd den för aktuella fakta (kurser, IPO:er, bolagsnyheter, vem som äger vad) "
                "istället för att svara från minnet, eftersom din träningsdata kan vara inaktuell. "
                "Svara alltid på svenska.")

    try:
        try:
            resp = client.messages.create(
                model=AI_MODEL,
                max_tokens=1500,
                system=sys_live,
                messages=msgs,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
            )
        except Exception:
            # SDK/modell stödjer kanske inte web_search -> kör utan, så AI:n aldrig dör
            resp = client.messages.create(
                model=AI_MODEL,
                max_tokens=1024,
                system=sys_live,
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


def _ai_company(tk: str, name: str = "") -> dict:
    client = _anthropic_client()
    if client is None:
        return {"name": "", "sector": "", "summary": ""}
    try:
        import json as _j
        import re as _re2
        who = tk + (f' ("{name}")' if name and name != tk else "")
        prompt = (
            f"Aktien med tickern {who}. Svara ENBART med giltig JSON (inga kodblock), på svenska:\n"
            '{"name": "<bolagets fullständiga namn>", '
            '"sector": "<sektor på ett ord, t.ex. Teknik, Energi, Finans, Konsument, '
            'Hälsa, Industri, Råvaror, Fastighet, Kommunikation>", '
            '"summary": "<2 korta meningar om vad bolaget gör och varför det är intressant, på svenska>"}\n'
            "Känner du inte till EXAKT vilket bolag tickern avser – sätt ALLA fält till tom sträng. Gissa aldrig ett annat bolag."
        )
        resp = client.messages.create(
            model=AI_MODEL, max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        m = _re2.search(r"\{.*\}", text, _re2.S)   # robust JSON-extraktion
        if m:
            text = m.group(0)
        data = _j.loads(text)
        return {"name": str(data.get("name", "") or "").strip(),
                "sector": str(data.get("sector", "") or "").strip(),
                "summary": str(data.get("summary", "") or "").strip()}
    except Exception:
        return {"name": "", "sector": "", "summary": ""}


@app.get("/api/company/{ticker}")
def company_blurb(ticker: str):
    tk = ticker.upper().strip()
    if tk in _company_blurb_cache:
        return _company_blurb_cache[tk]
    # Identiteten förankras i Finnhub profile2 (exakt per ticker) -> AI gissar aldrig fel bolag.
    prof = _fh_profile(tk)
    ai = _ai_company(tk, prof.get("name", ""))
    name = prof.get("name") or ai.get("name") or tk
    sector = prof.get("industry") or ai.get("sector") or ""
    summary = ai.get("summary") or ""
    out = {"ticker": tk, "name": name, "sector": sector,
           "summary": summary, "country": prof.get("country", "")}
    if sector or summary:
        _company_blurb_cache[tk] = out
    return out


# ---------- GRABIT RIKTKURS: AI + webbsök hämtar analytiker-konsensus ----------
_ai_pt_cache: dict = {}

def _ai_price_target(ticker: str, name: str = ""):
    """Grabit Riktkurs: konsensus-riktkurs via Claude + web_search. Ungefärlig, källmärkt."""
    if ticker in _ai_pt_cache:
        return _ai_pt_cache[ticker]
    client = _anthropic_client()
    if client is None:
        return None
    try:
        import json as _j
        import re as _re3
        who = ticker + (f' ("{name}")' if name and name != ticker else "")
        prompt = (
            f"Sök upp den senaste Wall Street-analytikerkonsensusen (price target) för aktien {who}. "
            "Gäller ENBART denna exakta ticker/bolag. Är du osäker på vilket bolag det är: sätt target till null. "
            "Använd webbsök. Svara ENBART med giltig JSON, ingen text runt, inga kodblock:\n"
            '{"target": <snitt-riktkurs som tal eller null>, "high": <högsta eller null>, '
            '"low": <lägsta eller null>, "n": <antal analytiker eller null>, '
            '"asof": "<t.ex. juni 2026 eller tom sträng>", "currency": "<t.ex. USD>"}\n'
            "Använd ENDAST siffror du kan belägga från sökresultaten. "
            "Hittar du ingen tillförlitlig riktkurs: sätt target, high, low och n till null."
        )
        resp = client.messages.create(
            model=AI_MODEL, max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        m = _re3.search(r"\{.*\}", text, _re3.S)
        if not m:
            return None
        d = _j.loads(m.group(0))

        def _num(x):
            try:
                return round(float(x), 2)
            except Exception:
                return None

        out = {"target": _num(d.get("target")), "high": _num(d.get("high")),
               "low": _num(d.get("low")), "n": d.get("n"),
               "asof": str(d.get("asof", "") or "").strip(),
               "currency": str(d.get("currency", "USD") or "USD").strip()}
        if out["target"]:
            _ai_pt_cache[ticker] = out
            return out
        return None
    except Exception:
        return None


# =====================================================================
#  AI-TEXTER  ·  cachade Claude-anrop (setup, dagsläge, nyheter)
#  Återanvänder _anthropic_client() + AI_MODEL. Allt cachas så samma
#  fråga aldrig kostar mer än en gång i en varm process.
# =====================================================================
_AI_TEXT_CACHE: dict = {}


def _ai_text(cache_key: str, system: str, user: str, max_tokens: int = 220) -> str:
    """Generisk cachad Claude-text. Tom sträng om nyckel saknas/fel."""
    if cache_key in _AI_TEXT_CACHE:
        return _AI_TEXT_CACHE[cache_key]
    client = _anthropic_client()
    if client is None:
        return ""
    try:
        kw = {"model": AI_MODEL, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": user}]}
        if system:
            kw["system"] = system
        resp = client.messages.create(**kw)
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        if text:
            _AI_TEXT_CACHE[cache_key] = text
        return text
    except Exception:
        return ""


@app.get("/api/ai_setup/{ticker}")
def ai_setup(ticker: str):
    """En till två meningar på svenska som förklarar bolagets tekniska setup."""
    tk = ticker.upper().strip()
    a = scan(tk)
    if not a:
        return {"ticker": tk, "text": ""}
    sysp = ("Du är Grabit, teknisk aktieanalytiker. Förklara setupen på svenska i EN "
            "till TVÅ korta meningar — vardagligt och konkret utifrån siffrorna. Nämn "
            "det viktigaste (trend, brott, volym, momentum) och en risk om den finns "
            "(överköpt, parabol, tunn likviditet). Inga köp/säljråd. Hitta inte på siffror.")
    txt = _ai_text("setup:%s:%s:%s" % (tk, a.get("score10"), a.get("last")),
                   sysp, "Förklara setupen:\n" + _fmt_stock_ctx(a), 170)
    return {"ticker": tk, "text": txt}


@app.get("/api/ai_daily")
def ai_daily():
    """Kort 'Dagens läge'-text för Översikt, byggd på index + hetast."""
    import datetime as _dt
    key = "daily:" + _dt.datetime.utcnow().strftime("%Y%m%d%H")  # ny var timme
    try:
        idx = indices()["indices"]
        idx_line = " · ".join("%s %s (%s)" % (i.get("name"), i.get("label", ""),
                              _num(i.get("pct"), 1, "%", sign=True)) for i in idx)
    except Exception:
        idx_line = ""
    try:
        hot = sorted(scan_universe(None), key=lambda x: x.get("hetta", 0), reverse=True)[:6]
        hot_line = ", ".join("%s %s/10" % (r["ticker"], r.get("score10", "?")) for r in hot)
    except Exception:
        hot_line = ""
    sysp = ("Du är Grabit. Skriv en kort svensk marknadssammanfattning på 2-3 meningar "
            "('Dagens läge') utifrån index och de hetaste aktierna. Sakligt, ingen "
            "rådgivning, hitta inte på siffror utöver de givna.")
    txt = _ai_text(key, sysp, "INDEX: %s\nHETAST: %s\nSkriv 'Dagens läge'." % (idx_line, hot_line), 240)
    return {"text": txt, "indices": idx_line, "hot": hot_line}


@app.get("/api/ai_news/{ticker}")
def ai_news(ticker: str):
    """Svensk sammanfattning + stämpel av bolagets nyheter (kräver Alpaca-nycklar)."""
    tk = ticker.upper().strip()
    heads = []
    try:
        import os as _os
        import requests
        k = _os.getenv("APCA_API_KEY_ID", ""); s = _os.getenv("APCA_API_SECRET_KEY", "")
        if k and s:
            r = requests.get("https://data.alpaca.markets/v1beta1/news",
                             headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s},
                             params={"symbols": tk, "limit": 6, "sort": "desc"}, timeout=8)
            for n in (r.json().get("news") or []):
                h = (n.get("headline") or "").strip()
                if h:
                    heads.append(h)
    except Exception:
        pass
    if not heads:
        return {"ticker": tk, "text": "", "sentiment": "", "headlines": []}
    sysp = ("Du är Grabit. Sammanfatta nyhetsläget för aktien på svenska i 1-2 meningar: "
            "vad hände och varför det kan röra kursen. Avsluta med EXAKT TVÅ rader:\n"
            "'Stämpel: Positivt' eller 'Stämpel: Negativt' eller 'Stämpel: Neutralt'\n"
            "'Kategori: X' där X är EN av: FDA, Kontrakt, Förvärv, Produktlansering, "
            "Rapport, Analytiker, Personal, Marknad, Övrigt — välj den som bäst matchar "
            "huvudnyheten. Tolka bara rubrikerna, hitta inte på.")
    txt = _ai_text("news:%s:%x" % (tk, hash(tuple(heads[:6])) & 0xffffff),
                   sysp, "Aktie %s. Rubriker:\n- %s" % (tk, "\n- ".join(heads[:6])), 240)
    low = txt.lower()
    senti = "pos" if "positivt" in low else "neg" if "negativt" in low else "neu" if "neutralt" in low else ""
    _CAT_TAXONOMY = ["FDA", "Kontrakt", "Förvärv", "Produktlansering", "Rapport",
                      "Analytiker", "Personal", "Marknad", "Övrigt"]
    _CAT_LOOKUP = {c.lower(): c for c in _CAT_TAXONOMY}
    kat_m = _re.search(r"Kategori:\s*(\w+)", txt, _re.I)
    kategori = _CAT_LOOKUP.get(kat_m.group(1).strip().lower(), "") if kat_m else ""
    return {"ticker": tk, "text": txt, "sentiment": senti, "headlines": heads[:6], "category": kategori}


_SV_MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli",
              "augusti", "september", "oktober", "november", "december"]


@app.get("/api/ai_monthly")
def ai_monthly(ticker: str = ""):
    """Auto-skrivet 'Månadens case'. Väljer starkaste bolaget om ingen ticker anges."""
    import datetime as _dt
    import json as _j
    import re as _re3
    tk = (ticker or "").upper().strip()
    if not tk:
        try:
            hot = sorted(scan_universe(None),
                         key=lambda x: (x.get("score10", 0), x.get("hetta", 0)), reverse=True)
            tk = hot[0]["ticker"] if hot else ""
        except Exception:
            tk = ""
    if not tk:
        return {}
    a = scan(tk) or {}
    now = _dt.datetime.utcnow()
    manad = _SV_MONTHS[now.month - 1].capitalize() + " " + str(now.year)
    name = ""
    try:
        ci = company_info(tk)
        name = (ci.get("name") if isinstance(ci, dict) else "") or ""
    except Exception:
        pass
    if not name:
        name = _ai_company(tk).get("name") or tk
    score = a.get("score10")
    nyckeltal = []
    if a.get("last") is not None:
        nyckeltal.append({"k": "Pris", "v": "$" + _num(a.get("last"))})
    if score is not None:
        nyckeltal.append({"k": "GRABIT-score", "v": "%s/10" % score})
    if a.get("rsi") is not None:
        nyckeltal.append({"k": "RSI", "v": _num(a.get("rsi"), 0)})
    if a.get("rel_vol") is not None:
        nyckeltal.append({"k": "Rel.volym", "v": _num(a.get("rel_vol")) + "x"})
    if a.get("pct_from_high") is not None:
        nyckeltal.append({"k": "Från 52v-topp", "v": _num(a.get("pct_from_high"), 1, "%", sign=True)})

    sysp = ("Du är Grabit, aktieanalytiker. Skriv 'Månadens case' på svenska. Svara ENBART "
            "med giltig JSON (inga kodblock):\n"
            '{"tagline":"<slogan, max 8 ord>","verdict":"<ett ord: Stark/Lovande/Spekulativ>",'
            '"summary":"<2-3 meningar: vad bolaget gör och varför det är manadens case>",'
            '"sektioner":[{"h":"Varför nu","t":"<2-3 meningar>"},'
            '{"h":"Setupen","t":"<2-3 meningar utifrån tekniken>"},'
            '{"h":"Risker","t":"<2-3 meningar>"}]}\n'
            "Sakligt, ingen köp/säljrådgivning, hitta inte på siffror utöver de givna.")
    user = "Bolag: %s (%s)\nManad: %s\n%s" % (tk, name, manad, _fmt_stock_ctx(a))
    raw = _ai_text("monthly:%s:%s" % (tk, now.strftime("%Y%m")), sysp, user, 900)
    tagline = summary = verdict = ""
    sektioner = []
    if raw:
        try:
            mt = _re3.search(r"\{.*\}", raw, _re3.S)
            data = _j.loads(mt.group(0) if mt else raw)
            tagline = str(data.get("tagline", "") or "").strip()
            summary = str(data.get("summary", "") or "").strip()
            verdict = str(data.get("verdict", "") or "").strip()
            for s in (data.get("sektioner") or []):
                if isinstance(s, dict):
                    sektioner.append({"h": str(s.get("h", "")).strip(),
                                      "t": str(s.get("t", "")).strip()})
        except Exception:
            pass
    return {"ticker": tk, "name": name, "manad": manad, "score": score,
            "verdict": verdict, "tagline": tagline, "summary": summary,
            "sektioner": sektioner, "nyckeltal": nyckeltal, "ai": bool(summary)}


def _alpaca_news_latest(symbols):
    """Senaste rubrik per symbol via Alpaca. {} om nycklar saknas."""
    import os as _os
    key = _os.getenv("APCA_API_KEY_ID", ""); sec = _os.getenv("APCA_API_SECRET_KEY", "")
    if not (key and sec) or not symbols:
        return {}
    try:
        import requests
        r = requests.get("https://data.alpaca.markets/v1beta1/news",
                         headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
                         params={"symbols": ",".join([x for x in symbols[:20] if x]),
                                 "limit": 50, "sort": "desc"}, timeout=8)
        out = {}
        for n in (r.json().get("news") or []):
            h = (n.get("headline") or "").strip()
            for sym in n.get("symbols", []):
                if h and sym not in out:
                    out[sym] = h
        return out
    except Exception:
        return {}


# =====================================================================
#  PORTFÖLJ  ·  lätta kurser + AI-granskning av användarens innehav
# =====================================================================
@app.get("/api/quotes")
def quotes(tickers: str = ""):
    """Lätta kurser för portföljen. ?tickers=AAPL,NVDA,VOLV-B.ST (max 30).
    Använder scan() som redan är cachad/förvärmd -> billigt."""
    out = {}
    for tk in [t.strip().upper() for t in tickers.split(",") if t.strip()][:30]:
        a = scan(tk)
        if not a:
            continue
        out[tk] = {
            "last": a.get("last"),
            "score": a.get("score10"),
            "label": a.get("label", ""),
            "ret_5": round(float(a.get("ret_5") or 0), 1),
        }
    return {"quotes": out}


class PfPayload(BaseModel):
    holdings: list = []


@app.post("/api/ai_portfolio")
def ai_portfolio(payload: PfPayload):
    """Grabit AI granskar användarens portfölj. Body:
    {"holdings":[{"tkr":"NVDA","qty":10,"avg":95.5}, ...]}"""
    import hashlib
    import datetime as _dt
    holds = []
    for h in (payload.holdings or [])[:30]:
        if not isinstance(h, dict):
            continue
        tk = str(h.get("tkr", "")).strip().upper()
        try:
            qty = float(h.get("qty") or 0)
            avg = float(h.get("avg") or 0)
        except Exception:
            continue
        if tk and qty > 0:
            holds.append({"tkr": tk, "qty": qty, "avg": avg})
    if not holds:
        return {"text": "", "positions": [], "error": "Inga innehav skickades."}

    positions, lines, total = [], [], 0.0
    for h in holds:
        a = scan(h["tkr"]) or {}
        last = float(a.get("last") or 0)
        val = last * h["qty"] if last else 0.0
        pl = ((last - h["avg"]) / h["avg"] * 100) if (last and h["avg"]) else None
        total += val
        positions.append({
            "tkr": h["tkr"], "qty": h["qty"], "avg": h["avg"],
            "last": last or None, "value": round(val, 2) if val else None,
            "pl_pct": round(pl, 1) if pl is not None else None,
            "score": a.get("score10"), "label": a.get("label", ""),
        })
        if a:
            lines.append(_fmt_stock_ctx(a) +
                         f"\n  INNEHAV: {h['qty']:g} st · GAV {h['avg']:g} · "
                         f"P/L {('%+.1f%%' % pl) if pl is not None else '—'}")
        else:
            lines.append(f"- {h['tkr']} · {h['qty']:g} st · GAV {h['avg']:g} · "
                         "(ingen live-data hittades för tickern)")
    for p in positions:
        p["weight"] = round(p["value"] / total * 100, 1) if (p["value"] and total) else None

    wline = " · ".join(f"{p['tkr']} {p['weight']}%" for p in positions if p["weight"])
    sysp = ("Du är Grabit, en skarp svensk portföljanalytiker. Granska portföljen "
            "sakligt och konkret på svenska. Struktur: 1) Helhetsbild (2-3 meningar), "
            "2) Styrkor, 3) Risker — var extra tydlig med koncentration (vikter), "
            "korrelation mellan innehav och tekniskt svaga positioner, 4) Kort "
            "kommentar per innehav (en rad var). Använd BARA siffrorna i underlaget, "
            "hitta aldrig på. Inga köp/säljråd — men var rak med vad som sticker ut. "
            "Ingen markdown-formatering, skriv löpande text med radbrytningar.")
    key = "pf:" + hashlib.md5(
        ("|".join(f"{h['tkr']}:{h['qty']}:{h['avg']}" for h in holds)
         + _dt.datetime.utcnow().strftime("%Y%m%d%H")).encode()).hexdigest()
    user = ("PORTFÖLJ (vikter: %s)\n\n%s\n\nGranska portföljen." % (wline or "—", "\n".join(lines)))
    txt = _ai_text(key, sysp, user, 700)
    return {"text": txt, "positions": positions, "total": round(total, 2)}


@app.get("/api/signals")
def signals():
    """Live signal-feed: setups (skanner) + insiderköp (Finnhub) + nyheter (Alpaca)."""
    import os as _os
    rows = scan_universe(None) or []
    if not rows:
        return {"feed": []}
    GOLD, GREEN, CYAN = "#F5C542", "#22c55e", "#22d3ee"
    by = sorted(rows, key=lambda x: (x.get("score10", 0), x.get("hetta", 0)), reverse=True)
    feed = []

    def _pp(v, dec=1):
        try:
            return "%+.*f" % (dec, float(v))
        except Exception:
            return "0.0"

    # --- Setup-signaler (skanner) ---
    for a in by[:12]:
        tk = a.get("ticker")
        sc = a.get("score10") or 0
        relv = a.get("rel_vol") or 0
        struct = a.get("structure") or ""
        bos = a.get("bos") or ""
        ret5 = a.get("ret_5") or 0
        theme = a.get("theme") or "Signal"
        if sc >= 9:
            h, ic, c = "Ny extrem möjlighet", "flame", GOLD
            det = "Score %s/10 · rel.volym %.1fx. %s." % (sc, relv, struct or "stark struktur")
        elif "ekräft" in str(bos) or "ekräft" in str(struct):
            h, ic, c = "Setup bekräftad", "trend", GREEN
            det = "%s. Rel.volym %.1fx, 5d %s%%." % (bos or "Trend bekräftad", relv, _pp(ret5))
        elif relv >= 1.6:
            h, ic, c = "Momentum ökar", "pulse", GOLD
            det = "Rel.volym %.1fx snittet. %s, 5d %s%%." % (relv, struct or "stigande", _pp(ret5))
        else:
            h, ic, c = "Ny tidig signal", "dot", CYAN
            det = "Tidigt mönster bildas. %s, score %s/10." % (struct or "relativ styrka", sc)
        feed.append({"tkr": tk, "i": ic, "c": c, "time": theme, "h": h, "detalj": det,
                     "score": sc, "kurs": "$" + _num(a.get("last")),
                     "chg": round(float(ret5 or 0), 1)})

    by_tk = {a.get("ticker"): a for a in by}
    tops = [a.get("ticker") for a in by[:12]]

    # --- Insiderköp (Finnhub) ---
    if _os.getenv("FINNHUB_API_KEY"):
        for tk in tops[:8]:
            try:
                n = _fh_insider_buys(tk)
            except Exception:
                n = 0
            if n and n > 0:
                a = by_tk.get(tk, {})
                feed.append({"tkr": tk, "i": "bank", "c": GREEN, "time": "Insider",
                             "h": "Insiderköp", "detalj": "%s insiderköp registrerade nyligen." % n,
                             "score": a.get("score10") or 0, "kurs": "$" + _num(a.get("last")),
                             "chg": round(float(a.get("ret_5") or 0), 1)})

    # --- Nyheter (Alpaca) ---
    if _os.getenv("APCA_API_KEY_ID") and _os.getenv("APCA_API_SECRET_KEY"):
        heads = _alpaca_news_latest(tops)
        for tk in tops:
            if tk in heads:
                a = by_tk.get(tk, {})
                feed.append({"tkr": tk, "i": "bell", "c": CYAN, "time": "Nyhet",
                             "h": "Ny nyhet", "detalj": heads[tk][:140],
                             "score": a.get("score10") or 0, "kurs": "$" + _num(a.get("last")),
                             "chg": round(float(a.get("ret_5") or 0), 1)})

    return {"feed": feed[:16]}


# =====================================================================
#  DAYTRADE  ·  /api/daytrade
#  Regelbaserade intraday-setups från live-data. Inga magiska siffror:
#  Entry/SL/TP/RR/confidence härleds ur VWAP, RSI, ATR och EMA-trend.
# =====================================================================
_DT_WATCH = [
    ("^NDX", "US100", "Nasdaq 100 (cash)", True),
    ("GC=F", "XAU",   "Guld",              True),
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
