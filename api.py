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

# ---- Streamlit-SHIM: fejkmodul i stället för hela streamlit-paketet -------
# API-servern behöver aldrig riktiga streamlit (bara @st.cache_data-dekoratorn
# i sok_module m.fl. vid import). Shimmen sparar ~150 MB deps och flera
# minuter byggtid på Render. UI-funktionerna (st.columns, st.metric ...)
# anropas aldrig av API:t — men blir ofarliga no-ops om de ändå skulle nås.
import sys as _sys

def _st_passthrough(*a, **k):
    # Funkar både som @st.cache_data och @st.cache_data(ttl=...)
    if len(a) == 1 and callable(a[0]) and not k:
        return a[0]
    return lambda f: f

def _st_getattr(name):
    # VIKTIGT: dunder-attribut (__path__, __spec__, __all__ ...) får ALDRIG
    # besvaras med en funktion — Pythons importsystem itererar __path__ vid
    # "import streamlit.x" och kraschar då med
    # "TypeError: 'function' object is not iterable". Rätt beteende är
    # AttributeError, så importmaskineriet hanterar det själv.
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    return lambda *a, **k: None

_st = types.ModuleType("streamlit")
_st.cache_data = _st_passthrough
_st.cache_resource = _st_passthrough
_st.session_state = {}
_st.__getattr__ = _st_getattr
_st.__path__ = []                      # markera som paket

# Submoduler som repo-koden importerar explicit (sok_module, grabit_app):
#   import streamlit.components.v1 as components
_st_comp = types.ModuleType("streamlit.components")
_st_comp.__path__ = []
_st_v1 = types.ModuleType("streamlit.components.v1")
_st_v1.html = lambda *a, **k: None
_st_v1.iframe = lambda *a, **k: None
_st_comp.v1 = _st_v1
_st.components = _st_comp

_sys.modules["streamlit"] = _st
_sys.modules["streamlit.components"] = _st_comp
_sys.modules["streamlit.components.v1"] = _st_v1
import streamlit as st  # noqa  (= shimmen ovan)

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
    "AI-infra": ["NVDA","NBIS","CRDO","ALAB","MRVL","AVGO","AMD","SMCI","VRT","DGXX","CRWV","IREN","PENG","AAOI","ANET","CIEN","COHR","LITE","PSTG","NTAP","WDC","STX","CLS","FN","SANM","MPWR","ONTO","TSM","APH","GLW","EQIX","DLR","GDS","VNET","POWL","MOD","AI"],
    "Halvledare": ["HIMX","SKYT","SNPS","NVTS","XFAB.PA","NXPI","MCHP","SWKS","QRVO","ENTG","TER","ASML","LSCC","RMBS","SITM","POWI","SLAB","AMKR","ACLS","AOSL","MTSI","DIOD","ON","WOLF","GFS","UMC","FORM","COHU","UCTT","ICHR","CAMT","NVMI","CRUS","SYNA","MXL","ALGM","INDI"],
    "Photonics": ["SIVE.ST","POET","LWLG","VIAV","LPKFF","HLIT","IPGP","MKSI","KOPN"],
    "Quantum": ["IONQ","QUBT","RGTI","QBTS","ARQQ","QMCO","QSI","LAES"],
    "Rare earth": ["USAR","MP","TMC","UAMY","NB","PPTA","IDR","TMQ"],
    "Defense/Drone": ["ONDS","KTOS","AVAV","NOC","GD","LHX","HII","LDOS","BWXT","TXT","AXON","CW","HEI","TDG","RCAT","DRS","UMAC","MRCY","SAIC","CACI","BAH","ESLT","JOBY","ACHR","EH"],
    "Lidar/Phys.AI": ["OUST","LAZR","AEVA","INVZ","MVIS","SERV","SYM","AMBA","RR"],
    "Nuclear/Energi": ["OKLO","NNE","SMR","UEC","UUUU","VST","DNN","FLNC","CEG","CCJ","LEU","NXE","PEG","EXC","SO","DUK","AEP","D","ETR","NRG","PCG","EIX","XEL","SRE","WEC","ED","GEV","TLN","BW","LTBR"],
    "Space": ["RKLB","ASTS","RDW","LUNR","SPCE","PL","BKSY","SPIR","IRDM","SATS","GSAT","VSAT","SIDU"],
    "Mjukvara": ["NOW","PLTR","ZETA","TTWO","INFQ","ADSK","WDAY","SAP","OKTA","PATH","GTLB","APP","U","RBLX","DOCN","DT","HUBS","MNDY","ASAN","DOCU","TWLO","ZM","DBX","CFLT","ESTC","AKAM","FSLY","BRZE","IOT"],
    "Fintech/Krypto": ["HOOD","HIVE","SOFI","AFRM","UPST","NU","BILL","TOST","FOUR","FI","GPN","FIS","LC","RKT","MELI","MQ","PAYO","STNE","PAGS","ALLY"],
    "Bio": ["RXRX","VIVO","HIMS","REGN","BIIB","ALNY","BNTX","NBIX","SRPT","INCY","EXEL","UTHR","IONS","ARWR","CRSP","NTLA","BEAM","VKTX","MDGL","HALO","TGTX","NVO","AZN","GSK","NVAX","TEM","RARE","AXSM","RVMD"],
    "Mega": ["MSFT","IBM","TSLA","BRK-B","GOOG"],
    "Koppar": ["FCX","HBM","SCCO","TECK","RIO","BHP","VALE","ERO"],
    "Silver/Guld": ["AG","PAAS","GAU","NEM","GOLD","WPM","FNV","AEM","KGC","HMY","EGO","AU","CDE","HL","RGLD","SAND","BTG","SSRM","MAG","EXK","SILV","GATO"],
    "Sverige": ["SUBGEN.ST","SMOL.ST","SHT-B.ST","ACCON.ST","SIVE.ST","OBDU-B.ST","XOM-B.ST","TERRNT-B.ST","VISC.ST","VOLV-B.ST","ERIC-B.ST","SEB-A.ST","SWED-A.ST","INVE-B.ST","ATCO-A.ST","SAND.ST","HEXA-B.ST","EVO.ST","ASSA-B.ST","SHB-A.ST","ABB.ST","ALFA.ST","SKF-B.ST","BOL.ST","TELIA.ST","SAAB-B.ST","NIBE-B.ST","HM-B.ST","SINCH.ST","EQT.ST","ELUX-B.ST","GETI-B.ST","HUSQ-B.ST","KINV-B.ST","SECU-B.ST","SSAB-A.ST","TEL2-B.ST","ESSITY-B.ST","AXFO.ST","LUND-B.ST","SBB-B.ST","BALD-B.ST"],
    "Bevakning": ["IMSR","AIRJ","ORBT","ENAFF","TRT","ABTC","AMPG","KEEL.TO","SOUN","BBAI","APLD","WULF"],
    "Big Tech": ["AAPL","GOOGL","AMZN","META","NFLX","ADBE","CRM","ORCL","CSCO","QCOM","TXN","INTC","INTU","AMAT","MU","LRCX","KLAC","ADI","PANW","CDNS","ARM","DELL","HPQ","ACN","ADP"],
    "SaaS/Moln": ["UBER","ABNB","SHOP","CRWD","DDOG","SNOW","NET","MDB","ZS","TEAM","PYPL","XYZ","VEEV","WIX","BOX","PD","PCTY","PAYC","APPF","BL","FIVN","NICE","GWRE","MANH","TYL","SSNC","WK"],
    "Finans": ["JPM","BAC","WFC","GS","MS","C","V","MA","AXP","BLK","SCHW","SPGI","CB","PGR","COF","USB","PNC","TFC","BK","STT","MET","PRU","AIG","ALL","TRV","AFL","MMC","AON","ICE","CME","NDAQ","MCO","KKR"],
    "Hälsa": ["UNH","JNJ","LLY","PFE","MRK","ABBV","TMO","ABT","AMGN","GILD","MRNA","ISRG","VRTX","BMY","DHR","CVS","ELV","CI","HCA","MDT","SYK","BSX","BDX","ZTS","HUM","CNC","MCK","COR","CAH","IDXX","IQV","A","RMD","DXCM","EW"],
    "Konsument": ["WMT","COST","HD","NKE","MCD","SBUX","KO","PEP","PG","DIS","LOW","TGT","CMG","BKNG","MDLZ","PM","MO","CL","KMB","GIS","KHC","HSY","KDP","STZ","EL","CLX","SYY","KR","DG","DLTR","ROST","TJX","ORLY","AZO"],
    "Industri/Energi": ["XOM","CVX","COP","BA","CAT","GE","HON","LMT","RTX","DE","UPS","UNP","SLB","OXY","NEE","MMM","EMR","ETN","ITW","PH","ROK","PCAR","CMI","CARR","OTIS","JCI","FDX","WM","RSG","GWW","FAST","DOV","AME","ROP"],
    "EV/Clean": ["RIVN","LCID","PLUG","ENPH","FSLR","RUN","CHPT","NIO","XPEV","LI","QS","BE","FCEL","SEDG","NOVA","ARRY","SHLS","ALB","SQM","LAC","BEP","NEP","EOSE","STEM","AMSC","GNRC","CSIQ","JKS","DQ"],
    "Auto/Telekom": ["F","GM","T","VZ","TMUS","STLA","TM","HMC","RACE","HOG","APTV","LEA","BWA","GT","CMCSA","CHTR","LUMN","AMX","VOD"],
    "Krypto-proxy": ["COIN","MSTR","MARA","RIOT","CLSK","BITF","HUT","CIFR","BTBT","CAN","BTDR","CORZ","BMNR","SMLR","BTCS"],
}
TICKER_THEME = {t: k for k, v in UNIVERSE.items() for t in v}
ALL_TICKERS = sorted({t for v in UNIVERSE.values() for t in v})
INDICES = [("Nasdaq", "^NDX"), ("S&P 500", "^GSPC"), ("Bitcoin", "BTC-USD"),
           ("Guld", "GC=F"), ("Silver", "SI=F"), ("Olja WTI", "CL=F"), ("VIX", "^VIX")]

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

_ICON_192 = "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"
_ICON_512 = "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"
_ICON_180 = "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"
_ICON_MASK = "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAEAAElEQVR4nOz9d8Akx3Ufiv5OVXX3zBd3F7tYLIBFInIgARKRIEiIEQSTKEqiRFPRCr7ytd71s31tvXuv7OckW5asYCrasoIpUbQkSxYVHGSJkhgkBjGIYA4IRMamL85Md9W5f3TPTHdPd0/HmZ5v5wCz30x31Tmnqqvrd86pRN21w4whERKIsm8XIcrPpZgsyvxZjktOJqVkTWYqzCZXhmpPLHduSv0xV8r9PNuj8pJmTZzvZmaymRMnfs2Zo7rMmoQkJy+hae4sNTzPiYz5ORVKWfGBccav8CU1ujClE1yC/3TRZTPVD/7nL/Dneo7tUHVJbaB4W+Dkm9QqY2CoF0e+5sxRXWZWkgICkpMXZFIoyzhhCSkpsvJzKpSSqhkBU2UFCcToR2qqJfjnEV02UyE2lCfDDMA/okcupRqnsRaERJ1SLi9pSRFKbSfjG+1pRiFNcipVXe8cnmIBIclJS2hZwmspXReJbaNk1qyUFR/W1JJSOAKQkr2ehn6AwL++VlMc/KslqJ67ZR5/nga+pCWVpnD7SYgMUOjGfKMCxSICbYsG1BoJmKJWnHfrIwEVW9a0kqrkTrJG8G/Eoa4O/qXgeF5efz2JquVsEfgvgR9oZyHnHxxvjFLBZdhXtsUQ4ImvWakxPVk+eRUFJCctqWEu3IxCYwkpszECqtsAmUZAQgSgbvCfzmkJ/mVlnj9e//kF+otamDx6L7iR0PqoQLFowDDHgYwGFDACSkqZgRFAqDofICorKjVjCKAiLcG/GpsGwf/AAP+i4iSABVe+AmWVe8GMg6nGwOIYAjOJBiyNgOlZ01LVYAQkkZhUp4auaQn+5dlQnsSzAv9cyjRCY8kJOsxPrZJECZ8lTdIC11Oiuv7F+ZYkJL2p7i5v7gIVkZyslk5/aqJ6uvz8XPKlpMqNKMmZUvHbS/CvJqdSY2oN8FeTVZYyvf2My+2idilZhzbzH9eOU4sjBYmudLhvnVdUIPA1G48G5BRQaV7AMhJQlsZy/G9qfHkJ/tXkVPD6cyVuEPzbDPztwtMEmp2CNTTJ8nILdjrNAly8YC00CMIqtmZ4IASp9eF0udwFmE8mLWGiLI2ABDkENWvwL8E09WcJDvmYHBDwb7vXv5jA35ximZznXR8F5U8zGuoFvpYbBJlRgXkZAvlWC1SPBrRoXsACGAG5BdRkBDQ3CTBDcKmUBxH8z1Ovf7GAv36FUjm2ruwVKaM8lNJ51dNNttQgaJUhMKtowBTmBSyM88EIqNtUmEZqKLQS1R76X4L/bLz+JfAnU33KJHJqVVnnRCl1kGQYVO8QU+Px86E2GwItjwYsjYBQqhqiAGoJ/mXlLCD4z8nrbz/w16PEEuxroKQmUqtR0KLoQOsMgVlEA5ZGQJuMgGpDAEvwL549M/EsQv5L4K9LgQkOcy/TAaUcRkE9BsGcjIHWGALFogGNDQnMaoXA0gioYADUDv7VM56P4N9ur/9gAf8S8FtEcV+hFoOgWpC7Mk0xBNoWDWhsSKAAyk4mXRQjoM5s5Y2AChGAusGfEr+WyJ2fyazAf+4h//Md+GsC/SXgt5cyDILi/eKcowIphgAFFw9WNKCJyYGLYATk51BnvCBO5QyA2jvCuhkuwX/W4J8a7p8baNZUr0vQX0wK+xOVogNzNAZSDYFZDwvMyQgowHgeRkB1qplfCXbFDYDaQ//VAaMwNC8w+Lct5H8QgP/88fKLFK4Fs+XrotqiA3MaIpgQOx4WmI02TQ8JLKYRUM9QQJ3+ffGhgGIGwBL8awL/g+T1twH4zyfQn5WyVeS03HhIiQ7k13pOUYEMQ2A2RkAgvJFoQA7rYmkE1G4E5DcAzjvwn0y8BP+whAQ5MwfSYgIXA/Rbq1gBmlaGFhkItRkDMzYE5jY/YI5DApWMAOTX6DwyAmrdCXAJ/tMSLnbIvx3h/gp12CpsbZUyM6a0ss/ZMEgwBlobFUjEtFnND5jjkEBpI6BA5txJ22wE5KN8BkBO778ULcG/fI55e/0tBv52eftzV2BBKKme5mQUBKq0Piowt2GBWQwJnI9GQF2ULwog8vBpLPS/BP/yOWYE/oRxl5Isv2kaa1AoNaFo1hqJMGcFDhDNuS5DootrMEOdJ8TQjKRT5M+UVOV4V2CYnKyANrmSpsZGS8jIxyF3qikJsw2AJsG/BC3BH7E+pXnwn+jEZtan5RcUgYe54MQS8GdHc6zrmCFQwixtlibE+BdmagRUx+z8uQ6SEVBcYC1GQOU5AKXBv2AtNQv+JbPnd9PrkTtT4I/JmanHXzDlXAB/Se2g+LOYYdgdKDhXYJixYR0nxMxibkBIKKULyrg1hXe1iQbl5BZlME5UWN5EhnwcqpYrPQKQ0/vPR3WCf/MZ5gH+U32EeXv9jVNxj3/20Yilh99+mvGzikUFCmVqmuYdDchIUbNrVNInbBaIquNWjfibwirZAMgJ/qXUq1ym6g0hK8O8wD9fguZe3/FLSUkXG6S2Av8S8A8Gzeg5ttUQmBAxi7kB042AHLeL52iFEVCxZktkz917JiScHAKoHfyreZKF4Hnu4F9zk56w4JuhCeBvVlwhATOqglkJqI/aqGqLlvcnU7jSGlJ2iH2FVg8M9WqwAgmRIQH/3yZXCgQCpxSN0m9l8K3GcDJJQS2mJh8nKF6+eKZ8HHKniq0MKDUHoBT4V5ZzvoJ/08Afk9ESkJ0d8LcRSdGA99Q88TSlWmUgNGwMhHAqP7w3bAhMsG96bkCsEmY5L2DRjYBiwkrLiRoAOb3/fFTNo1yCfzm+xeS3z+ufDfC3CD5TVGmRhrlpKv6nJZi7YdCgMVDaEDig0YClEVCSd11tIhoFUOHrhTvovKmaBP/CtAT/CfBvAfBHUh1U4E8Qv4hAX5ZS8T/pxtyMgoa88HzOcPN6pLKnpRFQQYs8EmZhBBQdClCRizn0KUxNg38h/uc3+M8+5N8G4J8TxJ7nYF+EEvF/7kZBQ1GBgG3+5YMzMARmOiRwwIyAXEnrNALqyuKnUqPvOZLnF184Uzlagn9B2bMK+Z+HwD+rqs1NTWgwOwTOZRTMTJ0GQLhNhsBMhwTOVyOgAk08n5oGkQhQeUP/+agan0IQ3TT41yd8eo5Zg/+cvf4DA/wztHVnKaUe+fX3iBP435CTPl2DeRsCzU5aHF44UEZAqdx1GgEVogDFheXmo+oL/VdzgVoH/qkJFwf8ZxvynyfwzwgYZ+Llzxvk66KsctQDKxH8n2l0oAHLo5AhMLtoQHNDAjM2AnIya60RMJFhOoc8MqYuAyzVHbUU/KtnK8Yv/whCU+DfnpB/rYGmAnLrFFGvtIMC9GUorezVvLcIp5lFB2oG45AhMNdowEyGBEJGAJCK2ym3pvPNebk0v1JJ6zQCqmfJNADyd1F19ZLNdoq5uM8U/Bc95D8vr79h8Gyk6s5nwM9L9bnxqdGBxoyB+g2BuUYDZjYkEBJUazSgvBHQkFlVj4QJ46yamVj5MKCqHVtzDn2J0H9NffQS/BcQ+GutsiXY10NJ9VguKDzK3bgxUCMgFx4WmM2QwGLNCyjHqNmhgFJJG+GQehjQQQr9Vwf/UrGQjJvNhPxnA/5jSVkpciSrVWZV1oSqUqpzWFIeqlbPkdyNPq4amYfa6MxkxtmGfjRXbRT5M1WVvPxKMppMUkD61KSU8K0u3vmzJBoApeBuCf5zBf+Jl78x8M++S9OTFZTXUIcWA/5KTJagPyeq9gwmDIE5GcxFWOXj1rwRMDQEGhO0NAIK8M6XOylV+nHApdg1wGcJ/jnkxoC/djGz9vobKEQC6JeHjCXgt5PKPZ/EqMAc3qEibOYSDZhguTQCqoguzKsBPhMGQClbopL3XxfND/zzyZgR+DckJetufV5/s53WEvTPJ6pmDEz+qIvqMwTaEg2YlxFQil/BW+lJmurPq2Qo3hpE1s1yShRJXqf3X5BqBP/U1AsN/vPw+mukSt7+EvQPFhV/nolRgdp1qoHFPKMBoR/zMALKGfPFb1WmAlGA6kZAsSwlhgDK11Rz4F/C+69H8AEG/+y7rfX6Y8BfXI8l6B9sKm8M1N88FjwacNCMgMI5C/Bq0ggoJihCIwMgXzbK/Jmf5gz+01G7hOSkG/W+EuOXfL7gX0+/1TbgX9L5RwfIECgUDaiREoyAplyexo2AHIxmZQQUools+VuBKi22YKZGrcMiMhYY/OszwNIlTL1bm9dfL5uant7i0jyKNLuzgRqmcOUVOJanWLacelRkRMixb8CoBNVkhdk1vnNgICSliorXXHlGk0lqeG7F1aiYwc9SYCOg8l5nIZjOzXsJ/nVLmHq3Fq+/BjrfgL+taufRa+GMhPzgGElZG6bWwCgbKycT1kETrJrYNOgAGAFTk40TVDMC8uVW+fqW8j1QM+BfWZH62cwK/Guvoxzg3xbgD7EqFrhdEGpTPU+lAludVcg+P8rv3k8YArVFA6bLnpZ9+pkCNRsBQASE2m8EZItphJo0AooJKrEVcFN9TCG+Bb3/zAR1QsnBAf+2ev0HBvgLq5fjWc2UfKnZ3UvG3SSlW2sU5APjiaGBNiBR7iGBGis/5ok2ZgSUulsg9RRGlYYCmjIwCvLNYQDMyPufC9ea4KSBHrhZ8F8gr/+gAH9utSYTtrREAKa3pOS+KKPDzZFsfjQvQ2AW0YBarZYZGAFxGblvVU6dnbMuZJ9NFKBYBKBAT9RM6L8E+E+P2ZdnEblZXze9BP8xi4UF/hKNs2UlqIWSy5RkGKTEc6ckmQ/N0xCoHg2Yz7yAuo2A7KGACfF5+ZVgUtoIaGoooEDiKQZAk+Hnhvlm8l6Cf+qdSvLOY+CfqsbBB/silBTnmGoUtM4gmIchUD0aMNMhgfPECChNMxkKSBeSsRHQgof+m+5hl+Cfm39RFvk4UT0yqxAhQw2KfGha8iUl1NGUWmtNheZTot4uowKTXO9ZjZVK0R/1Pq7CHkMlMflv14971Tgm5863E2AByYVgOjffOsG/Bt/yoIF/pU60hh44YJG/G51jj58T9JeAX52yDYKMxHOj6QqMUtSiawUmoXcuO1FNNEcjoJisjNRNGQEFAK1QWXIkTjEAmgKiZBFFEjZhBRVKeRDBvwHeubMvAvDnAH2Kgf6S6qeoQdBmY2AehkD5rEsjoFrq7Jx1GQEliVJ/AEg0AOoqfHlLqhJNR+4aWCwC+Gf3LK0A/9xc5tCTFwT9Jc2e2m8M5DNrcyatLCsra05zpTotuhEwF+yK3qzTCc4eAiggqU4/vTTXJsG/BK98nJoC//Q71cC/YmfQdq9/CfoLSYWMgZnSLKMBFRjkeicXyAgodTdn6ilMSkcBplJJIyAjccwAaAKMYpSbb13gn5+mG2CLD/7VZFX3+lsJ/KmYsQT9RaSpxsBcogIFDYHKsspnOxBGQAbDWmQ1ZQTMBHfHP0ocBzyFdwM1X2fIY14sJtmdR+Af8voblVOUlqB/4Cm3MTBTjXKkqCUaUD7b4hsBcf6zZ9JMV1syCpBCIQOgDkCqP9RRLXl+PtNZ1FO2uYF/pQ6lGvi3zuvPBP4l6B9UGj/beRsCs4oGVDACphrsbTcCsi2ZYnJqYVImQ718EqIAohLDIjlLJpyabQn+mYyqdSIVesXcXv+Met4UB3AJ/Ocf5TIEZtIY8hkC1Y338u/w0giYknoKk8nbOaXWmyw19+QQQAGOBaG6MM3iHZwV+OcXXB+j6uBfLWs+8G+YMkF/CfznM42xPgX1Z2oI5Lg7p2jA7I2AOqlOI6CSBo1xLcQ/lljV45EWNoOqJpySfM6hllROdTeFdoJ/a4A/5eLBA/x5lmju+/HWQsMa5NC3hJsz0SDzLlXRo2RmQo6DhOqsnIa2DG6KT2H2OTNMTVb+eQ6zFT8OOMSjyYRTs9UA/o3bDxF2dRhak1xT78wa/Nvi9R9Y4K/LOK5AiX1NlqDFMw7aYQhMgdpKerTYCIiwaOAEwVrwuxyTydt1GQGFkk3Q2ABYpND/TMC/nnItwb8i7xJ6xC8sFvDn0HZeBcojN9ILpWVov2Ew1RCYczSAK+lxPhoBAeNWGQF10JhrIf5B4tIRgNxC6k1YCy3BPz/PPFnb5/UvCvBnaNh+5ZMpV0AgKVE7jYJUQ2DO0YDqQwIlC7A0Amqkht37HCRGAnJSbu+/xaH/kgJKcDnA4E+YP/hTnL1/YeJya4hin5TL7VS+OmWWs90VMNYq5dk1KnnK3Uo6lMg49b2voUJibaO+Km4YJ6awn7ydU5/MZJTwLR/PChsB1d/qZwH+jdsPCHcUdfJuGfhPzd1gz7hQwJ8D8M9nymUQtIfmYwhkM6eJL2X4F8+yuEZAnPfUy7UyaaaZlOOqiuQr6dTXkbAWmg7+DehzAMF/bl5/ItsGdhCrRAnazFDBWYiayfB3RFjixbnSUCN/aGBWEwVjspL0SU9SmndWltkOB9RFAdMU3o2IzKPPbJMBKD0HoI5uhjJ+FRGZT5dZgX/E+1+Cf300wbZNwD870G9DmafpUPvM7QnGiRfnQsP33ddiFhMFsxkTDrIR0Mb5AOUYTN6uo8EU55HbAGjSqW8a/GvQoACXOrvoRQL/2QF/g9IKULOgP//ylac03St33C02BvyuN+b+NxYNyGZ8II2AEK/ajYCm+BQ2AsqLKss71xyA3L76wob+65JB8QuVOabemRX4E1oF/tSctJwU04AmL5XlWAOrVlOt5UxkMt/aG0unyRuNScy4U7o6yvUTjfYRsedca5WmMJt9S8opMTMZJXxLp1pOA6xCTXv/07NXf8z1g/8UWbME/6k5GyhsSsc+/66dEn9W4XZQwT4v1VIXEwzmW7vj/iDBUGxIWuad1hgBFakRIyC7k8svoxyDeb//Uw2Adnr/dVmTDYFXQ0zOG/CPXZhvV14d9JeAn58q11WqMTBbmm00YFGMgBoK32T9VeY9Q2yrKQpQIAJQf+HKe/9lpNXMPMKlrgaUzuTAg3+C1z88rGe2lOLtV+OypJJUizFQnVNp8iXOIhpwPhoBs9kfoGm7YPL27AyJTAOgSae+PPjnE1YxewEZdb7J7QD/7P6pgd4rxeufLVXvpJeg3yyVrt8E43IehkDz0YD0co3uHBQjIMSr1mpMYZZfRjkGTT6WrGQ5IwAzDG00yaOaKZzAiuIXKnPMd7U8v2lZzj+vP8HbL6DA4oP+Ympeqt4nMs32ybUmGjArI2C6JuVpot+ogyjypzKfBeCRugywnd5/HfwbeOGbBv9SLk/xLDMH/9iFefhk8a8Fc7aA2tDZtGEtvk+FDkMZZYj8aJwIKRsIzeLou+GdUvIKZgqST9GkqBK1ZS/KtLK4KQyafCRpyXJEADI6hyathJI8UlPV2GtHvP+mrcUDD/5jv2g2lODxF885B6KUTxuoPbqVjgqUy12axn1IeUM0r5TUO7OIBMzM21uQoYA6lCiULJtHogHQ5CvQdHvI5lmdeQT8G6RyL2jLwX+if53lDP9FAf72gGl1ml9ZFsEQGEuhpIs1Ssm4MyMjYFZPvR45dTl36axL3q5d9JQIwIy9/4rue9NAlviy1sAx8epBBP/YhTYD/+zg6iAAfVGaXZkLS5mTITAh57wyAuqKAvg/ajUCCt3JmbKwZVo1WfrNCQOg3uZOGb/K8Sicqqn3dwn+hWRFf8wS/JN0yM4xO0iaA+CnOeVztUFmo0BhQyD5RyM0NgLqdDAmJaTeOVBGQI1UGcOLKzaZo77CxTllnAVQh/dfIEuj71gD3n8N3BKvHnDwbzPwN0cNcm+CdVGeje113wjzEfepnCMJc+cqTWMJNJYT+lqPhPlPDEw/N6CuwtZ1XkCtlV+Idf2PIvmmiCepj+rg1g7vfwL8K/HNAP9Z0IEG/5AHldPda87nrNmjbY23nkCN6tZcYXNzjSScTYxosSIBtWtRA9Ma+5s5RAGa4THJKWUOwAHw/mniS818G6Cmvf9Zgf9EHzkr8E+SnZ66OUipyLmtQF+Uai9HMxVS3hBojhKNgKYRbXhnBv1QY1Q7b4r8aYJ1ydslMk3eFOm3qlABbhXd96YBLfIi1tKBpVw9SOAf+dE0+Me8/vypa5ZfkutBAfu8VFt566+wQoZAsRylaNz3NBENmL8R0EgUIJL9PJ0QWIBTQgRgxt5/BapoO+TkX1cJzj/wbx7P8nv9c/Qb07MedLDPS7UZA/Wqkz9Rs0ZAYjSgNu4ZdxbZCAjxabq6WhfQKBAFEKWFlJOeM2kFjZp6KSuxywD/phXJ1ZPVQAng3xwV9/obkV0m2xL0s6lSPdVvCORPNKtoQFxuPZzL3q4hQzNGQO2PIn9/M5VHzss1JJ7KSU1eqk9ukwDXtEcbeeFaY+KVA//GreyZg39cZmbK+mTOKFtdVKf4uWz2Gy5AIQVKZ0zllMklkogqy8wS08wKgWRGo6uF5RTI0Fx1RQrQ9KqA/MUoXuBSVZSZaXxTtc/7n4346WzqC0GlXp2BYXFwwD+/9zNX4J8h6M/Svpgmq3EDobIxMCNDoOElg4tjBBQTvThLA9FMXRTiWZ8CoTkAS++/fnZ1gn9LQmupbGYA/jmirPXoUDCc23B4n1I+baKZ6lhKQD0aTeUQEdNMDUw4KLWJyeivauKVlbyR/qr2R1CHlnX25WUz+TczNgKqVVrppLPmGXm5GtBzoV+mVDYzAv/pqeqR1VDyObGsyLUez7k+jikCyh0D2JzIhl3nuUQCCssorlQjtRV6FssoQJTUmFmGnIK0qN7/hGVdA7eityomPkDgn9/DmYFfV6ewGtnNIg6QV0b+zijOsbYOuRDDegyBeQ4JHDgjIDNpXYWrwwhI1yW/ljMyjqbUaY7jgPNKqT9p7qytiyhkhNKW4J9fyJSobfWgbgEO9USQI6yKsaOET5uovH61l6gQs2qSp+aOJKj/mU04LbVWYsrVwjKK4UMjfdgMMaIB6KgjcSKJRfL+s7O2yftP4d9knz1zZ3AG4J9XlSpy8iSroaDFQK7tYJ+Xipej1hIXNgSqicqXYJGMgKlCG+PdbBHq6Lvy9VO5eDSaIztTDRGAOXv/VRkn8ZjxQ63MZ2pbrAnBQj8WF/xzokINKFQO9hYV7PNS/rLWViO5mVSPBuRLsChGQEYUoLCMOfSLmVlrxozcdyqxrVtSXUMAPs3P+69G9bWLusJmdVqGiwD+oY53SkCqWvfcPPDPydddYJqhMTADQ2Bqzgh61vvs52IE1MArLWkjfVoTgZJFiAKkULoBkPtFyUkt9f4nXpqaqXHwb+pFSWTRFPhjan9Yx1OemqQG4M+X6nwH/TQqZgxUFpMrYXkR+eQvuBFQmH9dRkAdVONQQKE7ldiWS5ySpLYIwKJ6//XxnEOnfpDAP68ahfmXfznycp+B73oe0vR6q1yzM4gG5EtwvhkBddD5jR11FSHZAFh6/6U4JV5tyvvPF2csTy0A/2qw2ZzXvwT9WVN+Y6A061wJG2B/AIyA4vzrigLU03efz1GAWjYCWnr/KeyaeilK5yjDeH7gX4l3xSTlss0Z8Gcpfi6HAwDjQqavw06/W5ptVe6grFyjm5mpSsoM8ayXfUxOg7kIGVsFl6Qm6qISz+KZ6yjCZASg7k5kEbz/GYdvKlOTVvES/BOzTPc9Z9gOKOUzS5q7DtlCK0UE6klULNfCRALS63s+9m8dAHNQogDF+ZWcA1BVqzZ4/8211ka9/yX4l8tZAhHyAX/DNG+gL0pz0bdmQyBXpqURkHh1Lv1eHVQT97miffH8UQOgpHdU7mYFvrV4/3WwmsxcnF2xl6AWPjkELQ745+itFwn4Fwns89LMytSAIVBSXmm2szICauJY9FbFxFmmRzmq0y6aQxSgapSrxByAxfb+63sRGnFjq0itifGigX/528WyNFTzMwP5KqOFNSsZZ9fI6S/JjKmouHRWVbhm5xjdLM53usyAZ72sYzIaonyVVklALYcFVVKlajmK5a+0DLCpvqtJ778pViN2cwmBVSxI415Ztow2gH/NvmM+YY3UO6d82sYzRI3VRzLT5qIBNbJsKBKQX4HyDObXD5akWhnO3IWoxHccAcjFpdiDqpS/xqxRFg17/03wyNdT1KBG3d5/Nvi3BfhrYVReUEXKAuI6hSbJiV+r7SVNF1mJKee4WphNFY7ZORqIBESiALWwzmBQiHc9HvyBjQLUXZdBktIRgKX3n8Cuzcb6VKZL8C/NaJqQ2h9e2AOnjE+dlCUnDGM1RgdqL0aGx1qdTRWO6TkaiARMOEINdC5NBxWXUYB6+KpKudOohfya9P5rDv5NJG2+OZ1f4N94fTba+5UUwMVyRSC8dHSQ82bOz7oW12ySUalowKzmBcwiElADt8SrDUYBMqTm5pHGef5RgNnwKzAJsOqLPO/8dbDKyNhEp5/Js6LAkGexBP9FAf4cYmsaEaAwrzSeNK0/atC1bNAQOC+NgMpsMxjUDVxTeZYUWKueVZnNJn+pnQCbwqXErK3y/lN4F2JdTI9GAKsxoJox+LfF658h8E+ImjYMzynfqwoPIv2UcH8mmwTWaghUiAZMTdxuIyCf4PIMaix9YtLadwgMMW8qClCpmmu2s0T58F4Rmnf+OljVoUNdof+6VKjT+1808KdiTBpikVdERFR8qD18s6GJ+olyU2QTJ+jcFNUiJJlJfXZ9cQVTc9DUFCXkUPxCrdS0k1Qrj1rLP2/cm56/8CTApl7o88P7L8K41M0CfM938K9ADSJbIuAPKQ76SddnTdMMEUwpU11UmyEw7Uqh7GU5ZedotRFQz3tbRFxzRmZNfWQCk5niaAbVdhxwuvQZxken0aJ4/6mp66rL2YF/OX7VO5F0LhUNqAbaUCY4hr3uOOi3jZIMlATjpFFjoDLjZCMgN8upRkBNpW7aCKiBW/aV4jzq0GNmomujZq2HHAZA87WRLaG8/EX1/hvrGJtiWpvRPyV1AfBPvlqhQ6gE/ISksdFMtmlguigUHyZIiVJMNwbm+cxKajI1YX7F8gUVGni5m2K5SH1mwLwa79pbdm3ygYIRgJojXBWEzZLfHC3RunhQ9EetVbsw4F+SSmWNwxonXk2lRQb+JAqXJ2O4Irl+ksYXClAD0YB65NZtBFSnCYep5rpbKB6twZ/i/IqImmIAVNV6ev6mvP968mdwra8niCRt1ipsIPRf6m6B1AX6/VrBvxTeJGXi/Kziof5FB/44xVckZJSvVkOgcjRg2pWycmsyAgryms6lSX+6KPv8L3/DWjeSPx/n5uTnjgDMJlxRnzBK/TFjRWoRVY8HWzv4pzCsFfxzckjupmdl/acBP7cI+OPLA4p8GqCcZY3WbNryg4IyC9OkrELSmzYCaGqKQlRP3zkl8yIASt19Z1vgJESl9gHIJ23e3vuCef+pqesB//rqo07wny6mXLJZAn/SlZygGU9WG9aGpt/XxW9Ct4rM48MCU1iGjYCoWVXQaqpkZIWVTbuSO2spSmUzulGToDCfSiwr1FnRHIRG9wVo3cY+NTzqjAhA8+ZKI3ZDnMWCeP/NSqsr9F83+FdjND/wz/IIC4J/HXYucexTA99EOeFPSF6VXqjAsMBYlaTISsFC19gvVPcHaoqZ1RgJoJr4ZPJfRgHq41tGMHIOAdQUwVp4atL7r51H3Q13CrOFBf/CYJkVCi7h+RfBziQQbsNEgYhBgPINrpB3nja3oqARUErXRTACqlN9AcQZ9oWNO1Mto1xRs3SqZwighOjsFOUfYcRyPV+9//re3Ab4ZPCaB/hXSBy9UjLsX0xkTgZNtt9p8sPDDzRxOTfr+L4HqRkoIRJayJJAuVBqstRqwwE1xHRr5xXiU6d6Q3Y188whsUK2qtsDp8vPp1n9lZUSAZiTDdVi022hvP8Qj3qqNNugqsl3KZm7pBtXm9cP5HopkzbzSROVWKQkb38iJJBDQFUqIjOkcxn1ckYCwuwndc1JpSMBk22jGuXnMD0KUJc27enP8iZrMZTMUblJwVOHADJ1baQgNXn/c5BfRlRt3mwNWTMZNg3+ORhV6uDDWQoZcpTya0g5wX/IICl5JjjGdwSaFdAXpSzdEiYv5SnCsL6mVvE4QTIkFwgtlTYE0n7lylLkZr6UNRoB+QSWy1zMsaqDquND3S5MZarQrda7FXAukSkp6q6XGvnN3vuvg2pc83+QwL9k4uSsBcA/KXkm4CSB/iJR3CBIQfJpAQQKfc+k5EGArCupdOCMgGpUXxSgDppTFGAG+JRPRL2KJMwBaMuDLkMLpHuj3n/Nof8m+bQO/Et05GmU5u2X0KMWIiD/GGIgv7Yhx3B5EsZC4rfjSXMNoUQTTNoOua2J9GhNgUy5WGQmKqVEI3x8DgGfSuwmMxNmPRegKi2UsjGK6p45CbAmA7UAlWdKqT+qyV8Y77920ZTJtxZxCw3+UzqAJG8/U27Ns6sCJco/p+heAhFYq6zqFK3iOF1ocuBkghTImaJj/mRZmaobAWWkJt1ooI3NDQPzP7/kfQFKKh/KVq34DVReBsssaTUPAUzvbhJTLJDjXgs14f2HeNRWnbWAf3kmswP/aBw6KyrtUwHwz2RWwCOdRgSAGETDHQirgH8Ke8DnG17y1ySl1V3B4YBkVtOf8ihZYSoRRZraRmbCIgf/evqoxCvnIQ7kuJQvY0mKGQDzfAJVvf+qui+9/zwMFwb8c/bvxTvrnOCfKp9CnzqAPwz6s6PmjIGUXpFit0tWXal2lbstpfOduxFQkE8NwhqmnMIbc7ZaOBmwhOzUIYCpEctiOWZHLVEjlVrv/VPkT2U+teQsAf4lEtYC/rk78QrgT37+JFGzaP7Joxu+EcBAMKBbhXsKyoeNgKlzAtJvTppeOY2xwjZbNEM1k6+iwTjKXocWdRivKU+hJru4MWqNfgmKZOiWdquBVQDp1JzdsPT+6wH/OM9cl2vhXZuMEs+slIOXW3YS9wrua8jbT/rkZjPlUzR/5N5oi+CyNDWMkrOw6TpMcs7vUVahqdlraIg1BBJy8K/LUahC848CVKYEFrOs0lAEoB0hiUo5z2vvvw5K16OYhnVaEAUylQT/fFQU0NI4lwX/AscKT9EgMwGn3wrdzseKAi+8VJHDHnxGRKDEpMAkFkm/cmaqM/GULPl5Zadsixu7+FGAuUZ1KpEvOzECkPk+lXTj2wJxB4pq9f7rsujLg39KHKaS2MlENYJ/KiLXB/40GuPPp++EV17UzZ+StkiUQAAgVIkG5FhKUbsTlrOmC8ktEWtoyo2niS8V2LQhCnAwKV+VFnsfkm7NdAhggmppOPWHcagQ2zmFoZp46So77m0H/1Lcs9lx/GJ18B+BbACc0yApAso5gJsACCJDADbWul+95KKN93cd+ZXw9QmZCXwLGQLEoFKVnmQEFGU0ve5Ltb+WGwFN2RD18lzQ/re2/PWwKEti7hpUkD3RQZ2XVKP3X+hOfdRu8E8IO0+A/zRQygdAURCf7vVH7k8B/Eg+Ys1sBJvBl6+95viX3/iGV7o333jyEeL+Q8xGELGeyid0MU1OPDlQLJoxpqT6S+GSynhBjYCKlC2jmgZUA4+FJUr8Wo3RzIkmVwFkqnPgnvWCWp+tsuCnMJjCd+HAf+LWNI7ZwDORO+T158oztSPieCImIgHw3rELN09dd90VfONNl1uHDzk4dfrMqS9++dQjlm2fZGNMdIjQ5xOXMZpYHvyYsI0SVB3ODRiODOSLjaSN5VOUA09eKkKTWXMwKyQvmnhq1tQEFQpZQ/Z6eabUem5+ORMS6t0YqK2UUZz4rRqOA57epSamqAg457X1OaK2eP/lwL95qsnjykS9LMqehjV5MRv88wF/CCgp1kIIICLNzApwH733nhv7b3rd3Se6Xadz+cljW2dOn95+/Fd+7xlXm8slkTaxHY2YwzL8axHAjxkCWRMGGQwQTTUaopRlBMQ4JDKdOmMwpF/2lZlSRSMgO1W1svm5DxiAFqaqxwQj8THU8XSnUQ0GQBWqCSHmAjRVvf9mxc6dZymxdXr/VcE/azp8efBPzUnZG/nQxJek6EEIkhOYEWAEkegPBl988FXXnvvb3/WqOy668LBkIyWZlb3Xv/K+xx/9yuPP/Nf//pGPd7srzyfDmgE5yj924YP1/pO6hA2BLA9/bARgtG9APkMgC8SDzjC+gGAWRkCTUYCmqFVRgBRWdUcB6qTaRM7PgBItcNPmRCnwM7PqqCposb3/xQR/Qr5NbpKDjGXAf5QvxICGz57GHwo+0XTRj5A0MGzEyYvXnnrdg3e84LLL1h23d1ptrCraWLVXn3fphZe88dX3XXzNlce2DRshBHkxliPGRACJkMzRrVC7TLdFotdpEmqzazmrswxyc+xSCSrcRgvJKdhG0xtPCWnleGTnrt6fzYRSHbLzEwcjqwAyq6CkJ1TagZrK8/x8YPXRlN65CI+cl9NvLwr4F8gfkp/Zd08Bf/+Lz4XI/4S33qXx7Yi8sIFABAiCx4Y72vMee+ODt6+++pUv6gwGu1pJDXewj35vh21br951x02HX3X/HcIS7ieZ2RGCPArxSTUGRoI56GSHmSJ/kqsAmDACptbdVKKMn8VWZEy7UuR2VuKmjYDsrMv+tBrV4JAlMCht3mVkDN+a4zLAmhrcXNptTqF1W5uhl7WWYpfD7/lR4+CPFNTJyyka1p1uUGeBfxh1h0voeCQjDfgj2ca3DUnJnuc98jX3XPPIW7723pvBu/C8XbJthnZ3AB6Q299Dp2M23vDASy++/ZZrT3ue+7A/YdCPe4RlThgDoWv+72DGfzhDRp2kGQGRskUoB4jHozURRm0xAmZHTdgPURZVnYqUms7Nbw4VX5vI+TSa+e4DMDeq2tAWndILmr8KylkPhTvTgslKJg8yZcxdn4oXUfCfTmngP/Ty4Q/Qjbzp6cCf5KETAUpRn422rrx09eHv+vb77zh5yYrV2znFjnKF299Fp2Oh01UQ5MKyWF5/3YlLH3jgns3Njc5pZiOEgBdewz9hDIRlThi9gfFCPMqQbRgVmThZwghIZjSVCmcpCVpNRwGyU50nHWDdjtkC08gAmO6tFKfEbBXrONuPaBEtqPffJM/S4F9QeGnPf+JCXvCfyDElYRz8A2AkGk+0G7WfqNefBfyj6zT+AOwazV3d3/uLb3jTC69/8V1XO3u7TxlbudR1JJSSEARIYnRXLBA8ti3XeuX9t13+0vtu3dKe9wSBFBFMlG9UXqS9x6MBYL/VBkYNaDhTIAX8phgBcQNjOiU8GcqbN5Yl5Vc1qssIqE2FCizmHQWYJ81zGCAfr/itChGAFjyRmlQo1sBaUO5KlK5//pLVUQcFeBToEcuDf5VZYwWO4o2A/xg5I7vkjdJMev2pwE+YiAwIAS2FInfgfea+ey7efO2rX3gcvGuE2Rfa7cN4BhvrG9D7PfR7e5CWgB7sUn+wx5ddd/Hht77l/ouvuvzYw0S0J4RIlhcuSRz4J4wAHr9ro/xRcB6Xr8j+CVOAPG3PhhKNpRBUF+I/u3cqO9XB7d/iyWoraSuqrJwScxoCmGeNteJpFSNK/FoLv9qozr6wUMaGwX/qHjBlwD+K5jSRhpDm9acC/5ArASK4JgBm1sqWu09/w9fdf+2FR9f4zKknsdbpwBIWBrsDeB5graxCCgIYcBwLlsXQ/TN08w0nLn/7214Hr997lI0RBOg4yE/8HuqYoi/CRgDCRkvUEBjWRWZ1TuZIp1QjoPjyq+aMgALZ5vAOF2axgF3t+YZNCzgHYAEeUKp1WVX35vLn51xch8kc+esxb4Ly4J9xIccGcPllhcA/5qmHGU4F/5DsOPBHgRYeA8p1B5//1m/6muN33n69dAe77AghWBME21BwQAOCgAVLdTDo9QAhYDkWbT33FB9al87rH7jj+te99p6ntfYek1IyhhMCQ4ZGGOgjuoX1yTICRpnGF8f58m2FUiyc31YjoBDXSsKyU7Wvn6Lkyw3Jr0KLZfUIYIrKiTenF7Jktvw8a6rnYg1rDlS3bpX4pWQuhoRVFEjkUw38OX6hQPa8kwOGY+BRhJ4E/9B4fwhARwA/TBq+R5P3iMBKCgC0fe1lx554zSvuvGpzxYJFQNdZgTAWJBw41iqU6PiQLgQESRAT9KAPJZncwQ5feEwd+ba/8eorjx1Zf5KNVkSkk6IOo9+xe9ONgNjuhyHroXYjIHI7bgRMFZEiswKDHFzLJa2gS6v6m4aJalQv7b2uyKsYz2IZCXOJABxw6yy1UVW3iqtxSM/dhE1QmXeTj2qCd4KwKZu+FZoZmNAoksE/+BkH0LhnjVTgD9Kwx2wUu3uf/b7vfMOVt1x/0jG9PV6zO8KWDhyrC1t2IcgBhAKEBIigLBtSKAhtYCkCmz6Id3Ht1cdOvu0tr/SMHnyGiP0JgSGZYd0ooQzZ8wJo0ggIFb5Iq8+VMtUI4JwMSlJJ3lOzNYb15RlTxfxp8mfnrB1wjArRAg4BzIvabMrOguoof04eBXq9wlpFMqRsC5txkEZh8A+BZKIKIfCPAPzoXvA77vWHrsc+xlLSDNzB57/lG17mvOJlz798fUWxDQ2wgRQ2iCwIYYNIBavk/MiD0QbMviEANrAtJvA+W2KPXvE1t9x8+/OveprZnJPDCYFhfWLfkaRnuFyjSphiBBAi96dXeYE5GRMSixsBlPFrSuI6EjbMY5HpfC9/PmrEAEis+orPI2JV1vRsZ2dRlqSQ21SLmglMWun95+TaCPjnyVpWZPw3cQT8I817ijc9AbrBXylpoLVxrr3ygie/81te8/y1zoD3zzyHjgp29GEGoECQ8PfNdwH2APZAQsBAwh1o2JYD4w6wv7uF1Q7o8uOb62/52lccUQKPMxtBgnSSHnE9IxEMSi5XphEwqgtKuJFGU4yACVsiy0KbToWMgBJUwB6uh3cNRWiiv26EijSrHLyGXyrzrLuvTiGRybSp1jEXmoHeqY1p3nVWVf4MPZJ6kxXnkOBo0rQESRkiXm5SGh51E7nAP8Gzjnv/EtBg7nj9/Q//ne998PojhwX6+2fh0ICEAJSQGB7CBwIMaQAeABcgDVISYAGvbyClA0GA0fvEPODDx1bpxusuveSKy05sC8IugWUE0GO6RIyAcDni5QvVaxjkk4yAcQghD5U1AsodypJbrfoTNsijff0WJV+eiezFoGKWw4yHAA5QpS6ayLotyimZS/GeuasTo8rgHwKwrPoOLfWbBv5hII171sGeOhAABIGlItdoc/p1D1y/edONFx9XcsD728+QsQiAAPcHvpfNBgYemFyAXEB4gGQwa8AAdqcL4xlYjoOVtS62zjwHgotDm2vi5MlLNEHvKyH6gmAkRVcDTEQDYobLRDkjRnNCfcTrr5ARULTFhIyAlr0cM381ZtD/NE9LvJlGCzQHYFEfZlVqbvJfU/lT7Pba9CjMLZKhyh7wWSkT3dpJXjR8otFDh8JgGAf6EaCGwDb6l0CANgYdQb2//p7v/saTHcc1bm8LKw6ht7sNMxgAMNDahYEGswGzhoELQANkABgIQZBSQWsPe7t7ECTQWenSYOscDm10uq9+xV18ww3XPtzr9R8lIkEAh4yQ0bKiiBEQ/l6HEZCbUoYUorfTqWBDo4xf5agOjrN//6M5z99+exFIzURKxbqg1B/lFaDky+WJ0ti1oCHM1ZrPmbjeZBkZUjjwtFRpaDF88OlnAVA8aXipH8ZtcSJdgldNQcaYkWCkkHpra/tDf+9vv+yq44fdro0e4HrodLowuz0Y3Qc5Nox2ASFAEGBmGOMv/RNaAxgA0sCwBmDABtADQqe7jsE5DVvtd17zNVffceSQ+MJ//a8rT/3hH3/M6jj2SW1YhutJjL4BJlx9BHAwBMGBk80EPygx/BtOnFbjFKrtqQfG+7ympQpxx3j7ZUKBjJHcc0hYWNeZ86tJiWG7qU23SNvLlp2HV9i2r6TiDOo/3QAoCRotgLvFJ0r8Wo1R7jvFU1XL0SDXvNkqg396Xpr4HvVK4+CfGUoHJa4GEAIDZtO557bN573t628/Pth6hDuOoFV7E4PeAMpx/AkC2gNJBRgXIAmCBFgBLMFgaPQB9AAwhGCsdNehPY3BrgehFDTvwhan7Ffff8VNlx0/uv+RD3/6L7f65gqiAL8ZI3RmYhD7UQEe4nmaETC8HeqICRx08ingHc+Yo6dMTJVoa1QzAqZIrETlOE7PlZ2ifDkiOVthUCw21d0UCw4BVOneF9U0mKfeLayzmXv/lPCtDNVt8iSEfCiDAU0e6jMN/DECfRqB/zDcHppwxwA6luz/1T/6+28XW6efMApA13Kwt+OH8IkYmjWYDBgG4z0HKPIhMCDY15UA1gyChBAKCPbyN65m09/VF1+01vmOb3vDIc/zHpNivDnQ+ENBpIIyjZrw33i1Zk0KpNE/gb6ZTy49OhO7HZdQiijjVzkRBXRpYZex7EPL0GywdkHmANT7EMedR30Mk9m1oPElqNCUVqX41mEjTM2QYTdzNFVmgnBKCt/nVHvA/zI54386+NMIABNn1/vfWQjyXM979IGvuf6yiy/cOKakFEcPHyVCB2wkjAGsrgVmD54eBB44gZkC1f3vQ0NjZBAwQr0Dg8gfNrCkQ25fS1tpvOYVL7zhBTeceJiZFRF0XM/x5MChERM1YBD7O66vkNzQ79T6DVdsKk0xAhIp1G4KNsAmsL1ClvJ8W9CFJSkxuz68ItOWU+sNgMmXvBKH9hMlfq3GaCHzV+Axka3mgdEkoyptGCEAwDiYhb+KCPhHwTLJex59F6xBbF16TD35nd/xhkPu4ByvdTvMHsHtM9bWD8Eww9MehEUgQWOpRAAJBHA8+svMALMftudx1GKYh4ggLIntnVN07JjjfN/fevO1jqP/mggyrKNI0jdk1AwnDQ5ViVdtdI+A7McR/V6yzeRpIqWb9BzfhRbkr96Hz5OqWWhtL26tBkB7LciyNE/la5Bdt/oZ/Jq0zRozhAp7/yluQghXk6VPzvgfAlwYBCMh7zj4Dz8iAFgBCCLFevDFf/B33/yCY4chD63bWHMc2t/Zh4IFZdsQSvh7+1sSyrZATCChQBABbwEiCZAEs/AjA0Oth98DZQUEJDmQQkIpjdV1wbc8/5Ljr3/glos8z/uqkNAkkoA/bsiMyynCdRKr4nGVjpflxat5jPkcSpf2zMsOBbRlb4CcWWb43s+YyQLKrkhJzkaN7JMNgIOH5HOgdjb4fFrN29uowGPC3agr9B8H/zyh/zGfCKiFwBCIesap4BkC/+C7ZjY7d73wEu+eO6+32D1LFnvk9gborqzAWV2Du78DSwHSsQD4h/0IKSCEAEsCjyIAwp9sxwISAiAJQIKkhBDSX95HAlIqKGGht7WD1a6N/t4pEtgz3/UdX3f02BF+lBhSBGwzhi3GkQ4MDYtQXYSrOm4MpBkBCY8qT/vJ38JC7ah0057jO1Egf3aKdvZpS0qifJbDjIYAqjy8eh98zr6hEMPa2FHi13bQTL0KSvhWlWeVLOkPOT30Pw5dx0FNjK6Pw/1TwX8CQCEl73/k//t33not6dNSYh9efw8WSUil4A52wVKDpQbA0MxgCJC0QpMKgw+H3XARRBj8GIEggoKAJAECQXsaEASCwc7OWTg2i81Nm7/nu772ejbulygYaZgwWtLKFkQYCNFowGSFTg6hRNIkWmFJiSeXlGXcTqYC7YwyfpXlWULw3Cn5mdbAs619eZjpXPLmo1bPAajeaFr2FuSmGvTOZwDWQrk7uZlSyGuL01RnLpwvqVcIMUgBpLTNfkZGwRD4Q9qOwBHAhBcdBk4B7XreV//GW+68+9KLHOxuPw2bDBxpodtdhd7fR29vC2rVBpMHz3VBsEAkg06TQAhC/4FFwkQg8j1+/55vAIyPMfZ3ENSeh5WNDWiPYSsbkg3MYFs8+Oq7jtx712WS2ewS+ZsDxXTOMAKCH4jPh4g/pyl7LdAwDccvJj+/FF51DwXUSyWN41ISpl2shfMCUDXPos2lbtYAaHPJp9KiKl9V7xlYDpn8SnZwE9ZiHZ11BvjHRU78job+ozvkjS2CCVAUCWAZTccEyEsOuee++a2vtPa3HxddxYCnwRpwey6kIqyud6D3z0LwAELZYM8CPAFm449cUADywgd+EgokxlGBkfIiWLRPBgBDWQLoDSDJgoSEGRh0bYne7hn+v/7Pv3nFioOPDFlM6B8YNUMrh0JefbhexkZB7O+wPlMODUo8MLBWN3HSqMubK+1X3lulqBEkP1/7xTlSg6rXZgAscPVWouRyH1yLMU4z9ULqSFzI+88vkkJfomAVBn+aALmJMX5kgj+IQJLdh//3v/V115M5LQTvoGsr9Pf7MNrXXrMH1+xDw9/2Fx4DLALfmMECMMQhEyW06Q3I3wYgUg06WG7vGwGjZY0GABuQ8XBoQ5KtPPP2t9x7K9h7lAgmNYJBQ0OAJkB+vBQx1jmN6jV6aNDEQ8jdFuY5FFAfLWxfUUrxyUyLVP46qa5yTxoAjViN7aBanYG6edXJNIFFPq4z8A4yk9RRoSEvLU5Nh/4RBqkQiwD8R2ko1hZD/CYAM3qN2ZizL7rx0CW333YFsXuW1roC3n4f6yvrEKTAxkDDgxEaDB0c/wsw+4DPDBg2/lkAMGDDY/zn4X6oHNzXAGuAjf+XGEQGYIYkgi0tSDCgB1Dk4dC6Em/7ptdtnDhEZwk8aQBQqLgJxkG4DieiAeH6yhWFyTMUkJZ3Wqqq0aWqUYDm39PmYKCBTrPmPv1goB2QBwhmMAegSnXOK++i0mxfrlZ4NaVctxxMM/imgg6F0oUAfPRvDMwISPb647+DjyDQCu196u983zeTxI5YcQikDSQkpOiCyIDJC3b8A0AKgAUGjTb4C4MoG/YjAsHafx/oDQKbwb9ugqV15BsEhjUMuwAZKEvAkgDMAF5/D25/G0oM+G9++1uutBU9JmL6I27cpHwfG0OxMw8Q+0vRa5PPh2IX4ymntJWDEAVoq5OycHQwcay1kwAp9cfMpS8pFzXvlVTikdv7T3MBxiCeKDEGUiPwD4FYOF0q+IV/R8GfBfjp7/22V7/k5PFVKXkfRw8dgrs/QNdZRa+3Dw3/WF+SAAsBQxYAC4IIJDRI+If8EIahfH/jn/EHI89/tGVwMPkPRgPGgAgwRsPoHmB6AFywGcB4fWh3D/3ds/TKr7l9/fnXXDjQWj8n/AmBHJQhbVJj1EiI1PNk/Q2fVyKmhx9AOAowSpttBOSzJyntRgFqw/tyvtGM6yxuf7aQWmsALC6VeNR1NpQEBvl4zqCJ1i1iwkqsI/Sfdp8z9acQnzFYxWb6D0E+e5JfEvgDwODaS7q7r3rFXWZv91lasQW8vR7WVzbQ73mQUkFYAJQGC/iT+tiBoC6EkJDCgOAbAAAH/xswhSonYgwMIwM6GArwowpC+BMJtenB8/ZgzB4ILizyTxbrWKD+/jnzvd/1dddtruAhAIaGZxilGAEj+fEIwKgu/QsilDT+3NKjAMUp//tSdW+ASgo0LiTDDq5HailebYXSxaRaDIAmGkrrKc1RrMq0TTQV8GYqcgaU/VCz1vwPwSC8xn90OwZ0I0yLAV0a+BMRHEuc/jv/29+4qmu7JKmPri2xt70LIRwwA7bjQAgGSAcGhoJAB8QqmIToAcKMtgOOetuB+RIq+3BogNmAeWg4GHieBhED5MHwHlj3IKB9PY1/YBCZPXHB0RXv2992/8scmx4hwAgxZk/hT9gAQvT7qH4oqE+KrqZAPF38eaVFB6ZEASaoxtV/c3lvWtat1K1Q4iM9aFTascum8yYCcPAbyTwLl0N23T1U7d5/GvgnT/yLAjqNr4XAauzBBtfjYIeUa7H7zGb3pusu//zzb7iEB9tP0WZHQnU66HY62NveQXdtDX23B8M6kKcASPivt/SD5YFehseKCPY3+mGYYPzfH/JnBogZYlg1zDDEMNDwvD5IuJASIOGnhRFgj8CuhtR9SN3DutOTr3/tPd5lF7kXeZ55Fr6dwXEDB/F6iNdTpM7H9Tp+AHnOCsgzFJCQLUzThgJKvX5VLeyWv/OLStl+wIGiqAGQWOoqVXG+5Z0zlbYS21Hm8lo0tUHLlL3jR9c5BFaTKZPGt7PG/cPGAjN6F25Yn/jRH/yOl2098xmsOQMoeNA750CSYDkKvf42AIaAArENy1iQJP2IgNAwkqCFDUMWCMrf898AgAGI/TkC8Jf/+SaDgCQFgEarBwwYRC4sxwVTH5o1QDZIroBoBYocdJQFqXvYtF2Y3cepo7b4737/27sXHl3ZC/Yc4LRyRrz+8AcI1a3/K3Ybw6WJSbWfuDdAIs1uk592vG1AHk1qh4S50yLiSs15Q5daGQGYcO4Whqp5sq0qalXnpA5BubJk5K/s/SfnH2PRGHQo+DYEsRHQhzJQNHHEGIiAIfmT/ogIa13r3Pf/zQdfvHXqK7prDcjCwJ90pz1o7ULDgIghxNAMCbby5UA/oQNJ4ZP/xvvwk+/CZ5Rf+PVEJiivv4QQANgQwBJgAWIBYsCRgCU9rHYZCvvq2JEuvfAFFztS8hMAhBSTUYAJAylUL5H6G/1OWh0w/jHRPJLKlmisVZwQWOrFqO9tyuTUos6lev/eosJMo7b27wG10gBoPWXjRXmmc8lblXLIridJCmWH/nPlSxSeZ80/gUbpxugTwZaYMTABcAkfQWAQEYz33FUXSXHPPbcYNntSKR+AjTHwPA9aaxhjwEFyALEJfP66v+EYPvM4XM4h28d38f1Z/2LsTA9vAAgMCibACBBLwIR7NhMsPfQnFbrswnYsMLu0ueZ43/fdb7n4yOHO55nNPtgPbETqIqlehlUYmjAZr31Erqc/8HHWpKGAuqhYBCGX6PoSNUTt6bMS7bklTaWlAbCkxaKC7kPVFNnef3TWP8XS5Qr9T36YCMRs9h2hP/Vt3/zAsf2tx7G24sGfxR8DeQrW7mM8UW8IdP5GQGaUZ/iXjL8UkODP8gf8A4NSMSzYBtgfJpCAliAeRhN8mYYYEAwvkCelBOsBbDVQhw8p7+3f8JKXbqx1nk4bCkCoXkadeTwCAESiAuE6BCbTRoowfnDxO7HfZaMAaRmWtKT2UjMGQMtegvPCOkwoX74iF8tYuBobjUtW9f6nhHIo5ScNvf+RHxoBpTCojfLFMCIp9A8ChPDXG6501Nm3vfm2+66/9rir+88IR7kAuyOg94HU38+fGdBaBzP2wxEAv6z+jH5gtP4/UIKHVcHDMEBgCAQ3Cb7nLxhBpIMAo/ywP/yQPxNgyABCQ5MBBEFZDrQGlCRIDLC3/aR80+vu4ttuXDvBuv9F+KsJTSjiEZ30FzIE4nUVeRY0GQWY3tyK7w0wQam3y84jqO+FK/66lTSRz4P+tHVFbEChygZA6yppkWjhHYd6tC7HZXpnW54v57ANYqH/sLwwgMW/p0UEKAj9g0h73pObzu7u6x+4VwpzVm2sAtrdBuCNZREHH3+injHBtr6jBOFd/vxy8Uhv9of9DcdqMTAEaBhNmKwaAQGBYNyfpF8eAfDIUiAIZcEduCAmEGt0RZ+UOcPf+bbXOEcP2U9qo0+DaDQUMJSctipgXOZoOj+t/2PckdUxITCWr1y2GfJdvN6DUn8sqQhVrboGhwCqqLbMe/CpRJnz9BqVvX8K/ZsgLZQtvvwPGINUhEGCVzvh5fr8iAi0vuo8851vf+XVq92Ba/Eu2WIACQ0lxocJMY8LOjrBL5DpD/+P7w939hvt9T90/UOecxAiiFWR8T+xmvCPCx53HSQ42M3PH6RgIwAoQAOSGV2bsP3cV+W1zztq7n/xDbc6SjwDBglBHI+AxIdIRnUathRi9RxZHojx7/hjCEdl6o8ChCqv1Ou87AOWeZvKm06ht3h2QrOo9ZZh60JD7WtUzdP00H9yyaZZB2PvP418DInN+g6DftyLDV+OefzR0D8ZEmAy7sO33+Bc8ZpX3un2t55Sivehez10LAsCUQNgCPLhI3zHk/+CNCZaZn9ZX3lXmENGCBGBRpsFGLAESEhoJnS7mwBbUKygjEFHeuTuPMvf/rbXrl958eqe53qPAwQZPjUwWoWJSwTjUYGIekngPlmEZCNgetEL0OyWFNZDB6MPSbTj2kCU+HU+CsQuLScBHlAq3dCmgF8hCfUkiSXkIrnSmeUuZ9j75xH4R7z/4e0hWIU9fuLUiX/D74KIiQjG8N6hNfvhv/u/f/Pmc09+ASuOR2srFoQhCCNhTDLoh38LIUDB3AAfoAl+jD4YxR9ejw+qAyHDwcBoHSzzExE5gIEO5hL4hwX5+wcMDRIhJIwRMB5BwgFrAlxGV1rAYFeudd3BW998z21HNuyHiXzmFCDmRCRgqGGoPsOPbyIKgNh2wUlRgMjveM9cVxQggVUK5UpWMFFm8pKvThtxdUnVaWkAHARavp01ef+TmaOgE+3gKfR3IkQd/WcMWGGAI4CIhmv1xKojPv03vvbWO1dtPdjoeJbQfbi9fchg5n1UueG6/rH+RMM0HFpoN9SLQCxghjEMCpYbDAswqh4aTfKbrAkGCw8svOB3YFgwjSYLMjMMMzzjXwcrQAtINrAEY+fcE9bdd16n77v9kpsUvIcAkCDotCWRo/oN12HsOUV+hwzF6UAYmxCYSKGhluzbeW8cHFr2OwtPB94AWIaF2khlS1zDmv9E8WPwz9Js5P0nxaBjHmsEsEJGQfQ6gYIQuNGDL950ZfeqB19958rW6UesVQdg3Yc38CCVA60Zw013Iq8tixBXwN8ISAZ6+lLJHzyI7PU/qoZgFh+TAAsJQwJGABD+QkFDIlgwSOBgO2AmDhYOCAiWIEOAGZ4bEOwHwAYGDGj2Jxt6DGE8WLRLG6suf/0bX3zo8Jp9Smt9GkJOTgiMfyc/mjJR35H3KBoFyJwQOPkEG3gRixoB51dPQKk/WkJT+oODQPUbAAe9xhKpSqHnlbdhSTky5OZZa0+Rnj8VJIhDgDT26Kd7/+PfE0v+MNzNjoiZ9zdW7Me/8Y13X+DtPTeQeo/2t89AAFDSD8H3B14QZqdgnN83BIiCcD8oZAwMNwYaDgUM0/hAPwJ1EgApMAmQFEN9QP60/pGegIQhfztgQ+wbAAx/iMBISCP8HQVZAxjA8AAMDxycJMjB0AUbDx2lcfaZh9VFF672Xnf/NS/aXFOPg40UgrxI/SREAcZ1Nun1j39TpONOnRAYucBJF0NUZCiguGWfK1nBZj/bbvjg93+toZqLXMkAaF/1LxtTfkr2YkvnbZzyef+TmuX3/tMouuNfAqu4xx/+TtH7GHv+EAQPYNFR+vFXveTiW257wfN6Z557xFrrELx+D0oICCH9g35gMAr7c1J4PjREwASw9IF/OGOfguP4/On6Y6Mh0IfZP6nH9/QJLAgGflpDGF3X5EFDg33zxJ/xr/09AYTxwBgA6IHRB8OFgQtNAxgyYNJgdw8K++T2n1Ove/Cu1ROHzMrefv9TQa3o1ChAyCAY12Pod+yZlJ4QmNoOkiMJsdtFbjRAVSyOZf83m7z1UxVtWjgE0K7KjdCChITarWMZ7erqRMt5/+POP+r9Awne6PB72BAIrkUmAjI0M8F1vcePbODMGx940ZHdc4+rQyuSLAE4SoFYwO276Ls9OCs2OLKQXQRBcxGM2QdhfwzX5w/vD4cDCCRkcI+AUCRgGEkYRQwgwDwuLbEAQ/pT/0SwXRBLsBEQ7B8a5G8k6IJ4AFAPHHwM+jDsgdkD8wAEF6uOANxzam1N9L7pLfc97/ITh3oAKylJp0UBIs8n/DvhOcQnaEaeV9pzTjQj5kHtfXvbq9mYJt7F1lG7lGuhARBQu+qpvXRe1VOssEXH/tPup9UhDb3/mPgkAELM+x+mmwC0wPuXJEiw6jrmkVfdf9UdF164Njj17FfUoY0OvL4LBRvQgOt6UDZBWhjt9BdXeHJWP0VkDecACApHA8ZRAH9oIZg2R/5cASIBFgIMPwJA8LcSMjD+7n+B8cFGBpsKeQBrEAZg9ADqw1APTH0wDcDCBbNGx1bgfg+KXJw7/Yh1//0vMjddd+iw2+9/KqgmLx4FCHv1E8sCEfb2Y48v5N1nvibkpykyITD7dlzaAZ4QeF71PxWopfXUkAFQrrQtraMlFaUcDzL3s07bvi3h8lSekQTps7spkiY68S/iJ4pJjyO+jC3Mc2RKMLtgMv393ievuQwnX/3qF3lnTz+qLjyyCnewB2hAewZSKhARVlZXsb1zLqR1dEIfU6g0nFQ1QSRgOBeAJCg4GXC4J4AJjAt/2IBGxs/Q3PBT+fdouJFQINCvJYJ/xjCD2IDggkgDpDHaUIgA1uzvDugIrHVJ7p57yvumt7zkeScvPXqKmS1LScbQcAlZAFWjAIUmBE5pSIX6qZARUpnnsoM8EFT+MdbfADIMgGVrmwUtQi0vgo6TFO9wk7v9uPcYBpWI948wuETZhb3/8K1J79//KCUIZORFR9a3/+bX3XfysOy5K7QjVmwCPA0pJYxguKQBpTDoM8CWP1OfCAi2ABaBBx/emCexGoiDmfkc4CBDMMHfun84H4EgiAFP+9sBMECaQcwg4w8DKJawPAllCBIuiHpg0QMLL9jNxwZxF5K7kMaCMMKXAwKzApGNvV4fUinYpNAlATXYVlceX9v/1jfeetWFR9c/pY2xlCA3aeVEpE5j9U6x5xE2CsbXM1ryBFCnpS26N8D8aBHe20XQ8WBQck2LjHtLaopqqe8qTBLy5nfJ65WbSTUt+0u8n2fL1qH3P3knAjxJ34fpRh//igB7xrBatekLD957+dEX3HBlr3/2q866RZBaw1IKwpKAVP60PyWhjYCyVn0uwhcwnKg3mgpA7C+780/uiS35C3z0oRPMADjYoGhYFcF3w8b34Nn4hsYwLQgSBGEEFAMSHogGIwOAQWC2AXb8j7EhjfKXCYIAUgArWPaqf1jQwMDb7WFNQdjetn3XzSdO3nxCrJC79+Gg3t08UYDw90RDIJIsbxSg4lBA5XwzeMcSk83y3W6GxZIKELV5DkANFPfU2ketVm7OFAfo7LqaWpNJIJ62QGDCm0847S+UgRK+J437A9AkJA8G7sOXXGCfe+ub77mut3dW2ooEgaDIgSAFKSSEkFBSQkqCVGI83AD4y/cQwMkQ0IaH/wQg4+N2YAwEF4ZGCCcZCMOzAIT/e7jqYZg2uX4Z/uJAv9C+zDFi+6sK4K8YYP+cAGU5ILJAkiAVQ/MuiHbl6prce/BVL7rqxNH1PQYsKQWHw/h5ogDxqAxCYYTxI0stTHIDmJY0rVomlFlSOrW4gqY3hYWmA20AzIba1TzapU0yVdKxlPdPk/cJqYoEMJluQCZ6+ZNb/sazKwnDbKy1rnry9a+++fYjx1Z7O7vPWBsbGxCsgqV1AswCYrTnPgEw/p77QgQ6Bdvz0vBgHhrvAcDB/AAm+DP9hxqY8VyB0cC+Cf1AKDIQ2iEwgPDRyYJAyIIYysVIdrj+mSmYdBgMVQiJvsdwXYZlKXRWLQgxQL93BpbqdV5w23X9++6+7qQj+a/ZsE3Eg7QowOjZ5IgCRJ/jlLkfoTJnt9SyQwFTliW2nNqnY/s0WiRaGgAAFrYRtVHtWnQqG/rPT4mdP4Dosr/QvTTvfxJhYmP/w5n47DLD8gb9z7z41kMbr3rFze6zz31GbawLSCb/9N3h9rljkyIESBokAEgRjP0jeHuHXrIYfYtDHIMAIf3oAA2HA/xNfQwbINjb3//uE4sAAkdhEoNxiGC8XwBIAjyMcIyNEN/X9n8LkiChQCyhZAcDDfQG/hJBywYs20DQrvDc58QDL7/xquuuPCT2e/3PCBIM/2ziiShA2AhLjAKEfsceWSRdajtolCo05AP7zs+DFlbx2qheA+CA1+cBL94BoHLL/qKgQuNrIaSIe/URowBI9f6D30YIMp7Wpy9Y59Nf++DNN3WcfU3eObXeVejt7UGQwDCwLwQAISGEAgnlH/BDBCHgH/QDAEJAQAZWhgg2BpTA8PCfQKHhCy5IQlDgoRNjeMwvj7zdkOcOEzIWxiViBsRwW2FBfsSCBEASDAESBCEUAH8Ig8RwF8JgZQEEbKcLlgID48LTfQjBsBWDzD7cvWesiy7s9N/wqptvOn7IeYbBjpTwMucCxKMAE5di4YCghJmUN90CTQg8n+jA99M1FvAARQAafuwHvlUl0+yLXdz7p1wJxuCfXaZk7x+IglD83khUxPv3b0v/LXOEcT/7lgduufn5N17ef/bJLznrXYXd7S0IAqQgsNAgqQHBwRCAAwELJIRvBMD3thGc+DcUMFquRxQYEsI3EARCacOVMVTa9+rHJoC/WmAUJQhVGwcpSQhAEETwH9iXxfBlA8GJhKPvwcFCQkBICVdrQBA63S4sy4b2NNz+AG5/D12b4e09I55/4zHvpXedPCZ48EmCsAjshTUfPZukKED4+YSiAJFTAuPPNdYCaOJLMk1td5k0G+vgPO22ZlDwg1GzpQ2Ag1H8JQEo8DCrPPU8eUOdYiR5HXInPfnI73ioOB5KDt3L7/0TAPYAyfu7vU+8+ObNw2964AWb+7vPSmX65O7twVEWHMsCswsjegANYIQOuAgQlL8VL7M/6z8QNgbsMYADw3D/0CjwtRgODoS3w2EGDAPG+Kf3sTH+QT8wSNw2h3k0t41IBqsR/KiEf3aQGJlO/pyEwBAIDAJBgaHADIKAUravgyYAFogNhOlBYMtS2OHXvvSGG68+eWyb2UAqaSgYcYhHAUbPKsEQS6SQd59kwE2mq9j2pq0qzMpbRW7TIpbUKir7KFsWAVi2yOZpAet4xsv+xp5hKJAc8iZHf9K8/9A1KWjArOWlx5wzX//g9Td0HXdvsHNKdS0b5Ck4sgvP9WDggmkfhvpgeD7oG4IOTtsj8r1sCsCUSPhb+wZhfxLD5Xbke+jDNAHwDhUbmkFEBCkIUgb7CUgJKVSQx48mDEE+VIPBJEL/mhhFIXz5/uaAw5XFw1MLabTIgMEgMhBk4HketBaAsaGoCwUbetCHI13AfVZefvHq4NX3Xn9ZR5kPGc02AZEowLjSE6IA8WeCcNnjjy1jWWA4BJJJ9U8InB8tYP+wcNSeOm6ZAXB+Uflm0J4GVJTSNW+qM5wy6zv4EgaHuJePeLop3j+GAErsGWDFtvTnX37fJd1rnneoP9h93F5VBBow1pxNuPsG2nMhJYPhb5k7REzDEoaDFfgkADN0f4PfEP6mPRjO2g9An4MoQKDc8DjfoYFAND4t0N8GmEc8/HjBcNzeX40w5Asa7x44LK+g8RCDGM5PwNBG4NA2wwCYoc0AkBpaM4gtGG2BXQXhCSgmwN2D4m3R232SXvyiEyeff9WxNdf1vhpEAUwkAkDRZxiP2iSuBog8Vx4ZB6nto/FXLdruF/fNBspqv9hlXmxqpwFwkFvEQS5bE1TZ+w8oo97HPm5yutzePyJAY6QUnud6T1xxkbPz0jtO3CX5HK3aruLBAMIT0K4P4mIkYOhj+2vrWQTL9IJZ9QZ+uF6zBrOBxxps4IfwmYPNgQJvHwHAg8b5h0AcHAvM8PMaMz4LgBnBkcOB104AkX8qoT+eT4FR4Os83iNgXCH+csMx8A/3JoDxvX8C+2Xm4CAjE6wkMAZCu7DhwaJddWiDvZfdddXVh1bsL2ltHCEwiNVxYt1P7O4YMwyiFJ0AWZ6WEwJroYPcP7awbA0YAC0sZSvpANZT6SKVXfaXo1fN8u4AhHcDioSI83r/EV4YeadSYGAMdyTpL778nmuef/UVF/WNOWt7g13wwAUZgDVDawNPe9Bag0mCIYNl9gaAh+Ee+mAKZtUjmGwnICgI/wfDAQiWBw6HC4ZzAIgIHOwdIAJPXggBpRSkDDYesiSEGkYWCIbGxwD7c/j8TYlo6NkHFTDcW4BEsHHQaCmhP0NBw0AH2xAbaIANxAhzg4gCM4wB2GhIMGwYWNinc899BS+85UL76x+46Zg0/U+xQYfYnxCYGAWIPcSkKED8Ofpfp0UBmkbvgvwPYNdxQAvVANVbT62JACwff3VazDqs1rlmlzmlV4+koLEOCUiSuLHPxIWY90/QhtFhd/CF19x9fO3+O66k3rmnBNx9mH4fkgjKEpDSQFCwvh8SxB2AHQDKBx3yQOSChA+qQy+cSASAHiy9I+kvNRiG6FkEu/DxKIDABv4SPRIwxkBrwBgBYxjaGOiBC/aMv8xQkD+MEN7wZ/QZ7zY4rDdDIS8fwYRFNv6KAmbA+FsMg309OJiByCZIRwMAfRA02NNQJKDYg8SOYn3Ou+PmwzfecNkKtDFbwhIu0WSjmdgdcOJ+PE4RpmkrSBJDBwn8qkQBFidEsJj9TLuoLXXYGgNgSUuK0Kg/THtVck7OSui7o95/+qY/Sd4kxdKMvMzxhyWRxwa7x1f3zz3w4steuOFoz9s/Y9mkIFhASQEpDBg9CGlg2zaEWgHzCoAumJTPXwxAwvX9ZBqt6A8GwkOlDJcxOB/A1zs8dZ5HihrDMMEZv+RvLgDDYjTAPlxSSJFJfBwMDQT7BwyHHdgAbEJHFU8aU/7iQj2qcjHcioANQANA9EHCBZGBEgpu34MlgOOHV2AGT6sLj0n3VffffJEi9xNGc5cANxKFKRAFCF+PRgGG7SGtveQF6Hg6yr69pCXNkZYGQCWaox3XFhOyEjXcG9Jk9DYcro+cDpdQnxRLH/4bjwQM0wqwaxiOdnc+fv+Lrz159eUnXG/vaWvVElBkB+F7A6N3ob1tgAa+d2osENZAWIWA44Oa0JBS+2F/ISGiG/iP9vMf/vUnHlIwbk9+RGC0+U/gebN/HLBSEkJKCIAlkVG2bYSUbDyD0bbCwTAC89irBzACev+sgdAuAsFpQxR8xpUzlk8AYIInTwYQgQEg+yDhgQDY0oHbG8Dr7+DoYUsQztHzb1o/+sCLLzrmDQaflVKMJwSGn1c4CpBk+CUFhEY/ONoekqhtQwFtorn2RweiM5wLLQ2AJaVSfa9V3S9ogY4yVXSy9x8GkOHFad5/KALAJASzMTs3Xm47L7njkuOC9iF5R1gCIC3BRgbg6AJyAMv2gdHzAMIqiLsgUoEB4AHkjmbtC0nBbnvjNfYkBKQUkNKfoMc8nOEfMl/YP4yH2d/uVwgJZkC7BoYFGSZBDEEsSIMCb97P6u8lFPjA440ARsYGURQ2478xqrtgKWOw7bFgBMbCAEL2AemCpIZnNDp2F5IBd9BDtwMAZ9XmhnS/5iXXX3/iiLunjekQoRcJvKSFeWLPM2oAJjSRXKcFTqOU9knZt9MzVKMlPC4pjdS8FWiUli2/xdS09z/p0U16/+kz/xMvp3j/QxJgjxnOYLD3oTtfeP2NF124bnhwWioYGE+DlArG2AVAFoQCSNogCBhXQ5IXIK4O1uMrABYgLF+e1P7yPrYBskCQACmMDtwR7K8CYH8eAEiOFSaASQOS0R8YdpxNOn2m733qU188e+bsdn9tbZWuet4V65dcemTdHezAtg0sKcBagzwdbPRjMPQZmAmCGDD+5sCGCMSAZg0iggmeAQDA+HsZwHCQdjgZQMO3miTAFpgFSAr0XA/dlXUoctHb2UZXKriD58RNV12iv/FN98mf//WPPuwZcTkJNmxGOyhHnwsH3zna0ogwOvgwMsrkr1PEcFfFyOXwbxquymiKhoovqTUUbwgHiFIMgMVvgItfgrZRnTWa8TZN7f/yzPyfliZ75v/EsrGRx8+jm/GoAAGaQRZ73hMvvmXj6H23X3rBxiq8wel9BWhYHQUXHhRZYDCEsMCQGGgBoRTsjgRjG6SCg3PIAsEByIIhG5AAi74PQCAwLAjqBEaCvyyQJEEb7W/zL2Qw8U+CBMDwYEQfrmE21rrZdQX+5M8feuhX3/mbO1t7vQ1LCnP/K17yhde+7r7rrrhs4yLBHrO3T4I9f9jCCBAbaPYBW5AFMhrEDDbDumEw9/1Z/wHyEvuGAYz0oxPC+OcHsQEZAwMFNsY/AZEZLBlMBq72QOwfkQxtoMyOVPppfc8NR17wwasOf+4jnzu9J5VUbNgePr7Up04YHWsQAf/geshWGLefyiCf0ZAp+3Y9dIBRaw50MGpzshTLIYAltYfqWPYXUGoId+T9T3rylJQ+8p1GwB+5SYCU5IGgj6zsPfG6l7/whiOHVr2ds8+ojq3Q7a74a+nJP9yHhYIhC0wWQLa/Jp94NGTvh8slmGwwbDAs35sXNlhYYDE8Djg4GwDSB1DLQr/fB1kEu2tDey40G5CUGGhgv8/c88COc0j+1/f86eO/8Iu/uiMsuuPIoe4t6+vOC9735x84+Vu/9Ydf3t7TsFZWzZmz5+B6BmRZMAMX2tP+BEVIMGsABiAXgA4+BiStkfEiqANBjr+hsQBUYDD5BxPJ4GAjAcCCIBssLRhSwfJFAZCCgICCgA0A/V1x/IijX/M1tx632P2I0ewQsTd8BhGDLDZ0k9QYKOHaaC5AUppRm6kIBTW28yUtqQotDYAlzZiaD/2Pv+fTIWwIpHn/4SH1Ybqx98+uMey4g/2/eu0r7jj+ohc8T685JAQDQkmwJLiegaW6IGGDyIKADYIFIglAgqAgRHD4DylQsNufEMFWvnAArIB4BQQV7AfggYbnBpCAu7sPSyoIW0DrPUBpSJvgeQTXteB0jtPqyqXi1//zHz37rl/73S8LaV8lSCijecAgY9v2iY9/5JN41zt/+3OPfeVpeejwCd3vG+ye2wOtrgLSAliADQNkwOiDsQ+Ifd8QIOOXg7oQtApiByQsf+hDehCWCxI8GroAWSBhQwgbFNSHgAIjMI5ghYY4JPy1jX286AUXHfraB26+ith9mEgo+KcaTT73mKGWfy5AbHOgxHaUcxVKGk11KZdGwJKap6UBsKSZUbXIfl3ef+haqvdP0esJnuXoJoGlEMbz9HN3X7/efe3LrjlJ3ll2986KzfU1WErCYw1h2yDhe7pC2sE+/pZ/DRZA/il7HCzL87fk9U/S871kCc84YO74PqpgkNAQMBBgCAC23UF3bQP9rXPY3TuH7moHEAIDQ3A6h+C6a+Zd7/qfj73rnb//aaXsFzu2OOFpAyayjWFBYEtJ68UffN8n8avv/oNnv/rkWdldP2Y0KZx97gxEZxVmBLUGIA1/oyLP/w74xsnwQ1YQ+RAQBBBMMGkydFog/GONhRDBHgdytGHRMELgl19BSgvu/imJwdPe/XdefPLyw/tgZpIULAuMRwEmntlk5Cc5CoAcUQDkpJR2y+m3l8OXS5oVLQ2AJc2QZuj9J91O0GGa9z+NBHhgmB2rd/qx1770+puPrpOW3hkl2UXHsaBhYBTQXVuDgQyMAAVBNohsINjnX5ADYj+8Pj5IZwjtAgYSbDowxgIg/JUBQvsbCQEQ8Mf8IQSUbWNjcwOGgedOb7OGg519ufvTP/PrH373u/+ILavzMhJExh+qF8FSfmgNxcDAsLjuLz74ua1ffdcfnn322Z6AWIPV3cDuznbg+ZvQEECwSVEikI27F+PH/gEiGA6GL4TlTwCEBAXlJpIQwgIJGRgGgVEAATBgSRcdPCdOHNb8yvtuUd6g9zEDOCDomPDo34TnOj0KMIWqDgVMbV/LKMCSmqWlAbCkGdGUiX9l88Yoy/uPfM059h8fS455mppBjue6X/7619+ydvdtJ430zqJLHo6sr8Dr97G/v++P+wcw7Z/SJ/2wvgi28BUq8P5pNPufgy17RxsAsQDYNwRAIpijFnjfGADwMOj1wX0PlrMCQw529zRvbB6nc+d076d/7tc++Wfv+9BltiVPMvPAaGMxMxn2zxcYnidgDNtgGIJ43vv+7KFHfu4//Odnzu56IGsNwnKgSYPJBYhhIAC2Rh+CFdScBuDCCH8JIwsTHBik/Jn+NIxsBEcHD8su/CEPKYW/PfHwGGGQvwmR0XCkhxW1L6Q+xfc8/4JLX/6ClaOszWlJ0ITRDsUTQE8xkM8XBUhJk/C7FGVEAZa0pFnQ0gBY0nyprs6PEk+xH98OCwt36nm8/yRvEoAQ8Azz7tUX9d0HX37DNau2Zgf7UqEPoQfQrgtpKUAA++4AJEWwM18gh2gUIh8ev0vDPftHE/3Id6+FD5T+Eby+McDMYKPBxkBrD47jwLiMvS0Xzz25A411uLqDX/313/vsX3zor04S0UXasDYM2zCQ9tHMxCC2HOeW933wU1/9iXf8p8888tXnsN9n1tDQpKGZALYB0wHzCgyCaIZg3yiRLkgM4O9jABBsMDrBkkbhF0H6lSjkcK4DAZKC4YBgS2IKzh8gP9rAbg+it40OtsQFhzz9wMtuPNnZf3rPMGwQu3EAn2gTsWc59yjAkpY0R1oaAEuaAU3pJKtNDpjONuHAn6Tfad7/6HfoGoEHhuGg99ynvv7BO6+75MJVdveek1L3ITyNc2fPwLYtbGwegglO1xuVaHiIDhkMt+tl8j1gDrzhIeCPN9wBhDCAMn4a8ucFaBAMazBr7O7vYuAxrM5hSPsYb52l/Z/8yXd99M///MNHwXSpMayZoUYn/aV/yBgmZuZup/vCT37ikaM/9XPv+uudviENYbQBGBaMsWDYAdgBsx3438Y/u4C0/xEmGMZQfpifZDB/ARBEEMN9gYOjDMRwr2DBAfAz/Dl+BoAGex5MfwDHDEDuKbr8hOJveONdmmB2hBDJy5pp8nmOnndKFICmpUn4nU452vDSjljSHGhpACxpflRXCHSq908TQoqM/SdFBKQUIHD/Da+6dv3euy5noc8ZqfchNEOywlpnHcb1sL+zB7Dv1bIANJnx8b3sn5dHwRa62vOX0xGZAPAZLDgIjxto7MOyGFq7kEJBSAeeZghHAZJghIImm7d3CWe31Ff/wy++58Mf+ODHrjCGLjVMxjBUluef8JHGgC1LHf30Q4+KH/nxX/jK6XN98dQzeyztDbhswTUCriFIuwuhhvMTCCIY5Yh0MSKIZkiMohom2EqYWcOwB2M8cGDQGNOHMX2AvOBAJAMBAYe6sKWEg12x0tnjr7n38suv2tzbYcMQYDfPlsDT5gKMKXuzKP9eTei9NAKWNGNaGgBLOpCU6v1njP2P7QAOQIIT48hEGGjD9rGVc4888LLrbyTzHLv7z0pbDfcJkCOUEcGmOf72uQaCEIBgsHEOATycQacwGg9n4uHxOQAAIxhrG6vo7e/Atm1AKuzt9yGVA1IOjJCQ9grbnQ166hnv0Z/86V/90gf+8kPXA3yEwWA2gsOGR94PmAwbcjrOTZ/42CNP/eQ7ftVzOsd037WwcsEJuJrhHNoEBMN1XSjLglI2lOxASAeQ/jI+Q4EXL9ifEEhiZOhwYOiAGJoMNGsYeNDQMOx/GBrGeBBCgVhh79wOVrsEh3ZJ4RS+9Ztfs7pzbvv9TGRhuCxwwt2fNBRzRwESvsfbTTZViYItaUnN0NIAWNLMiFN/VKAE7z/aYU/x/uOZYigwCv1jhOkGDLvfG3zmwZfduf68k5sQ3jYE+mDSMELACAkTnKcjQMPgtx/uFwjWwsMf4xYYjf8LMVz6RvDd5CDUTxIEYNDbBRHDXtsAGwnNEmStYm9g0NcKewPFPW8F/+ldv/vIXz/00AuEkMcNA8bEPtM8/3h6/8OOs3rPxz5+6tkf+je//PDOvqUff+RJXjt0CL3ds8G4P4IxfgegLgSt+Gv8iUBCg4SLITYbGP8Y4VEUxq8DAPANFgSHDvmPj9lP72lAQ0IpB4NeD6x3aL3T4xuucNYfvOP41fCnS5qE5zbVABx+mUsUIBQN48QbS1pS/bQ0AJbUYqrgNVE0f2rnnzAuPGEUhO8LeCDgBZfAvve2S0/sbz1tOsITji2gje+3utAw8L1WAUCyBAUH8oxmvksf3AkSBgImAHpmAYbyj7ojBYKAEP4aeCUsOMrB3tmz2NvvY239CKS1ip2eQN/rGqt7gfixf/crH//gRz52jIQ8bBjQBjQxxm+mfGLpzXBeAANCqAs/9omHz/zgP/2xjzorh2lrt8dQErv9fVhrq/C0B+0B2gOM9h+BP4fB9/L9lYcMQGCM78FMf/BoYiSCVQHDuRFC+CsDDBtoNuiursHtG5jBAOuOpt2tR/hNr77lxOr2Y88wSBJFAiiR5zk0CiL3s6IAnB0FSLs2SUswX1K7aGkALCkn1dh5MXL0mPl2Wqs09h+6mNTxR7xHAsDsgYQaPHvqz9706luvPH7IoANNFgjGM/DYg0seXDmASx4MfPQkbSDY9/j9tfASxMNNb4IPSzBLAApgCYIFgg1BHYAcSOqAjAVLOlBsYW1tHa4hPHtmD8asspGHzE+849f/6s8/+PETSqrrCQHOTp/wN/WD0F8C5OrK6u2f/uxXV//Nv/2Fz+/saz53rg9tBMxgAG0MNDz4Axg89uJHextIMCsYlgD7ZQ3GAYKlhDJYCTGcMChHSwiJJCAJHrlwjYtOZx2KbLDXx6GuwcnjGm998wsdw2bPH8eJP/hkQ3Ai+JMYBYgnSrrX1FyAMnyXxsaSplOKAbD4jWfxS3BQKOVJNDTxL97BJ11L8/An9viPkZRkYIx4/asuvvyuW48JuM+aFaUJnj+Bj8HwxACecGGEAYJ95f0tbRTEcMMbkhBSQZAMVgD4pwP6u+cpf3OcYLMgISx/wyC2oV0Fkl3Yq+sQloO+68HprmN7ILZ/7Cd/7aPv/fOPndQax4eef8EJfwU+TJZl3/QXH/2c/Kmf+WWxu2tchg1XB5UmQisBwMEafgGjAyMnAHeQBAffh0Md/keM6yowHHwjAIBisGWw67kw3IGFDiQDDu0Re0/z3Xcc37z94q0tMJTw9waIDOOEH2/eKIBPPNVAWEYBDi4djKc2WYqDHQE4GE9tbtRI9dXm/WfTcDR54lqsk58AhqTO3Y8De4BQjvv4F1//4C2XKXGWbbErvL0dwCWQsGAkgZUBWx5YMVj6s+EVEeRwnF/64XxB/i53Qih/YltwWJC/VbAVAL8KTsuTMFpC2YcA18bOuXM4e/o0lGUZIW3zq7/2+1/54F9+8kqtzTETON3Ngf/4Q6Se9yd//sX3v/vdv2ttbbvuzm7feHBheADDe2DaA4JNgwAbYAfD/f6Hxg6RBeJgd0Thb4Lk75IoRxMo/Xrx6wlkANuD6ii4A4IUK5BGoL93Dh1rlzY6u/R1b7jnBM498yGABOJhIoq2g6Q2EEmHoaFAkXaT3vDyRa6yeSQlKf9OLLvBinSAK/BgGwCN0wFuGbUQx/4m/0zNlkZ1ev+jSWjRfjc2eYyFIONp3Xv7N7/kgqsu36R+7yw2D3VhWcrfzEYof897IXzwEOQDu7AA5S/TCzbzg79Gzj8HACI4I0Ao/whfIv+wILL9rYGZQMZAs4f9vV1ASNidDUB2jbDXxb//hd84874/+/gx1nxhMHuO/Bn8KD7jP/dnPCRgd1bv+f3/9cUP/+zPvdva7UnR6yvuuxoeD+CxB39fAIKEhEVWsPNhcPIhxAjYifwDgaS0IEQQ+Qh2SCQhIQRBqGCjJEh0rBXYTheW7WC/18OFx4/BtjU6Th9XXt7BW99wzS2uq58ZWoJJbWP43OPtICUpEg3KOL/0zPm7i9FrE8+w7G+SaVkvZWlpACwqLWKbn6n3n3AtwThInPQ1MRTAmg3su67Ye/yN99+6obRgS3Wp7wrsgyE7AsoGLCHQhY0VWoNDqxCyAyO78IQDYxGkZSCUASkBljYMOWDyj/fVwaw1u7sGAQfGSIhOF0oKAANYloFRA5zr7+G5LY+FdYH44R999yfe8wd/1eu75uLAKx+F/cNA3dQnkCXIdu5474ee+NQP/qt3feixZwa073W5r/2wvqc1+vt7gBLQxgORCygGKfZ3AGQF0jZgbBD7Qx1MAiwFYBFgA7DZPzwQDCVW4OAY2LUgJENTD53VDlzdhyUVbMvDijqH17/q2u4Nh5/m4KCg6GNNAfnsKECs8Wa245qiAItEi9gfLWlpACypKUrx+gt3FLEMub1/TvX+07y+JCLAkBA4Sk98/g0vv/oam3elZE2WvQKPJTqrq3BZwxgNJfyxfgULCv6Rvyws6OCc+6FChv3Xzh8S8GfAK0tBqQ729gYgoSCFBeO6gBQQloFrBmAp4UKY7sYx+uEf+9WP/c8//tgJ1zOXMGM432/8mQ7cmZ/JfMN9ASZlGAYg7Zsf+syTKz/z79/zhcee2tfSOmIGAwEiG7bdBfcHkIr8o4GDcf7h6gCQCfZICFZI+CEXQPjHJBPkcP4gFClIBMYC+RspQUnoYN2lTYyV7gBrK3t4+ze85KL9nb2PG8/9SmLIP942crSH3HMBQnMHSlFqtiXSLqk+asAAKNdAl826OrW+Dhvw/tODCjQaj00dGkj6PQz7B4YGgTUz08vuufyq++6+Ddrd535/Dx0nOLFOCBg2AEtI8gELLDA+3Y58YOMOWK8D6ARb4bog2QdRH1IARgsM+hpK2aDuCjxt0O/vAzBwDaPvATv70qxtXCJ+8Zd/58t//N6POsboC0fAbEJ/zWS4vqyHPwb/4LuZlDG8aTvOTZ986KmVd/6n3/O+/JUtQeKYEWoT0umCbAGyJIAVkNmA5BV/z3/lQqo9KLkLoXZBcgBJBMESMA4IXRA6IDgQ1Am2S/a3RIYgMCwIoyDZAhkJGIIwHro28MJbr+LX3L7yPGFZV1LQtuLgTrFrE8Ef8iMPwDAKkLf9NTUXYP7UQpUWjtoyo2MZAZgbHcDXiCe+RH9WLXLWlr+RsG5IUDyAEPL+E8P/UWIGWddePPjiq15+OwEuHAUa7G3BkgKSgN3tbSjp737HwdI+QSq0lz8HjBXAq4DpBOI8EPoQMBBMUEKg21mFFDa2Tj2HvcEOnFUbLhv0XQEWa3pt44T4Vz/yK5/4jd/6k2ch5Y0MYkZwUh4T2Phr38IfgzzefRr4D08KJJgwXx5/hvf8+yAS8pKPfvLZp37hl/7HFx5+dFd4Zs0YsYoBEYwAKJgMCFgQEBCkIVUfQvUh5ABEHhgG/q5CCjC2bwiYDgRbwbHCA0C4AAhEKqhb3xCAZuiBC/J66O0/Td/zna+5oNN78iGAd6cN90yAf0L7yTsZsJYIftp7MzE34CDQQSxT+6mdBsBBbgsHuWyVqFzFpHW0FPP+09Mle30Bj35/99yfvvmB5193zZVH5ZlTj0GKHixl4A32IdhAexpSKEjpb9qDYFObuJYMC8w2AAVhAGIDMVwnbwjEBGEpgDQ8sw9nRWHPHeDUuW2o7gWetI/Kd/zsb3/8f/yvD1/Ewr7dB2DQOCw/Os4X8SN+kybwZX9iaYe8Y/yjGw0A7B/GazSrKz74kYef+3c/89uffehzT8PlVdPzDDT5W/QxRFDPGv4pf/BLwCaIvBD8aXvBboijPQOUH/anHphcfwmlCU5GNARoCaEllGHsnnsOUp/B0UOGv+Ubb74JoFUGvLR2k7kkMGwk0HAAJCHdROOqKQpwPtFB7h9bWLZ2GgDnCbUlDFQvpXj/yT/zUwaIT3bC2b3mxOQ/JHT0vvffedP9Fz7/zltPGuOehmMbsLePQ+sd9Ho7gPFwaHMdrA20a8DBenYOTvUDwd/3PzjRbhxq9je2YSNBRoBZwHMNejvbAFysH+rA6ijs9QforB01WmyqH/137/74e/7wfRcag+MAyyhIs++xD9nHd/NDeNw+BOYpn8k8w/A/RrP/GfGIwsgAEQAbYTsv/Nhnn1Q/90vveeSzX3qGVg6d0OSswgPDkAcDA8DfJcHf+Gg4fCKD45ERAlACQ8DfMZDB0vMNh4ADGwnSwt92wDWggYFDBrbYxbNPfZpe9YpbzJUXPPMQCCoe8o+3lqS2MdmimosCTLwfqdGzNvYBy+HfRaPSBkAzD23ZFJqnhDqee7WX85TSxl4jY7WRmdwpGRMYEbF3ofXsx9/6+tsPH+70xGDvWRxad8DcgyUYlvCD3qtOZ+jPjtarE4I5AJL8jf+EASkPJHv+nviQADsgswoyK4ARwXi4wV7vHHb2t3B2ZxvdjQsM5GHxz3/4ne/7gz/60IWeMReDRtH5yIS+yOQ+ZHj3OWqb0/IH9+L8I4YAgpUBRI60ras/+bmn+J3v/v2trz7Tk+f2SA+MC5b7YDmAv0y/AzJrILMOmBV/OSD5px8SuQB5o78GDEMElgALgiABSAKRhmADMhqkGbqvsdntor9zBsKcAw+eE2957fNvsvbPvpcN7060jZT2EFkSmBIFiESMkiozlxUwpxcwUezcO4PzgOqv47IclxGAJRWgPNCR9rNgEw0nn3LcL5Ds/U/MB0C0P04K/wedvmGQdf+LLr/12iuOQu89h670QOzCVsDO9hlsrq9CSom9vR4sy4FUDljIYBe/4Sx3geEBQEL6BoAQGiQUBDlgdmDYCg7QYWhoQAqoziq06Or9gSN+8md+86/+13s/fKM2uBggYwwEM+KB6AnQHkXmM/b3n7oCYMoZAfGh6AkdQAbCuuoDH/nqE//uZ3/vodNbQsJyWJPLEAwjFCA6YHQArALUhYAFKaV/lDC5/kdoBGYH/HF//6xhFuTPSiANwAtmIhAsUtg5u4NVW6EjNQY7T+LG6y72brvKvockrQ7VTXn20XYSP00y5Vcy8WgCYfEeOo+hnsZ0CeJLykcHyABouNHz+fla5S9z2dopl48mvvi8Er1/TpngFf4duiiI6Jjz9Mff9tYHvDNPfRkOD6BYw+3vgowHKQlaez420HBzmuCwH6LQHv8CJFSwKZCGZWtYDkFrg17Pg9VZg3BWMBgM4BFAlgPXKO4NHI/kBfLnf/49D/3Bf//LYxDiCECa2V8/mOqhxwA+EsqPA3fGJ3HoIEkmkicVjp4GswDYc4244f1/+aXVH/mJ33jo9Lbh3YFnBizhssB+X4M663CNgGYCWbb/TCT7+yVJhhCAFORvBkTCPxdBOP7JiRb5ewpYBqQApQRsy4GAhGAJ4XrY7AqcfebL4nu/40FH7J59L7M5R0nWYbx9xNpNdIhoOAciei81CpA2tjCuraybpecCnLd9VuMFPxg1W68BcDDqZEl1kO+sVafQMqxciSe+Idr5xjvqqI5M4H6/P3j4277+7ls3V3tqVRno3i6MNwDrYL2/knA9FwYGUlnQAAwRhnvbIwB/BKfaSRL+Tn79PWijYa1vwFpZg+dpeEYDtoLsdLA3AAvrMB85cq16x0///if+4H9+1DGMk8zQzCyzAD/saMYBO+/M/7QoQdp8ggmDgREzFBiGoQC4zHTFX3740e47fvrXe/t9JfumO+h7CkY52NreBtkCwhIYuD1/siDD3x5ZSghJICUglYKQNgAbBH/rYCEYZBnAYj+QIgBPG6x01vwDlwwAr49V2xOba67+ltdd/xJBYjNoEjxtGCD+M9pu8s4FKBsFCBHH/k7cWNJ5QzU+8gMUAahCC/oStVFtTvk+NXGMMnrVcCc8Tja9MiJOWNLsf3+wwbntauy9+hV3eM888Vms2AR4w1PtgqVvxoBYjEBuxJkIgPDDxuHIACQsqwvHWQMbgtEuSGjsD3ZhRB/Wege7A/D65iVmbfNa8c/+1X9+/+//9w9vuB6uhi9d+jPxo2A/LHWWh8550iUBPorJ4FDi8aTAUZTAAuBatnXVe9/3xGf//X/808e2d2y7px3PSAVrRWG7fxqe2IdwGB4HM/yhAFggYfuHJ0kBKSyAbUhWgYFlQJIBxYCCv8siEYS0QKxgCQvG1bjq5EV46vGHxDe95W5140X4CxA0I5hVGG4ficMA6e0pUlNZUYCRgLSbMV6LRAuq9gIrXhstDYDK1K5GNB9tOPPnlNSZiYoEEVIn/+WzRFgQ9tfEc+//zm+6+yZ35zHVkQOceuYZrHTXABbQBvBnowez/YeciYJT/RgcgD/709j9vyQAo2CrdZCwsb+7A5IG9qYDlzR2egO2V4+g563Lf/Wj7/rA7/zBn18HyCsBeMZAhV3uaWAcL1XWZL74J1IZCfmSHnNkzsGI16SFYgwsAAMS3Rf+tz/+0rmf+rnf++zWrlAsOwa2xaQYrtmDsODv+S8kWFi+EcAy2GuAwSwg2QbB8g8PEhKQAiQVWErAknBWHHisYbSAJVeworqwyODYBqi391Xvmx685W7e3/mAMWYnrX35Dj6Pv4+uhRNxNH0acXRYISNZSZrdW9+u3g5oo0aLRAfaAODRP22lViuXk4qUIWd4IOIpRWOfaWP/meH/WJKJKID/lweud+qBl1x4741XX+RunX4Uh9YcCIa/xpwtEHzgIbL8mf5CgoWEv6J/yDMckyCACYIkBj1gf8fAGAVp2VArNjy42O653N04arZ7lvnhf/vrH/zPv/PHVyopjxLBY98FjoXdORXw0wyCCbBPMwxSDIRwLU+fAMgxYyCin02EvhD2zf/zTz9HP/8ff/fhU+dc0feUlvYaIDro9TSEdGCgAPiTKQ2GGw/5DCVJkBDB4UsKJGxA2oC0wJYAOgRIhnJsWFYHRhO2Tp3GpSc2sHfuK/KmGy/qvf4lJ262bVvCH2nQE+Aer9xYZGAa9E8anSGLdmoUoBErocXU4kIlGdYHiHwD4CCXsI1US31XYZKQNze7GTSWnN5/BPJTvf8EXtHwP8PPLa4/7j75+lfd1Xe3H7cOrdqQrHHBkaPo7XsQsIKJZ8HpfdKCkD4QQcJf8if9rX8BgoDwTwmEv0mNpVYhxCpY+9sHnzlzFjt7fT528UmzvS/lL/zS733ud//wg5fbljoBgmcYKuT4x0A9+gxSARkhzz2GyGlRAE64mTSkEJY7lj3WLhIZCOU1DAdAz7Lt6/7oTz+3/Uv/6fef7vWFktYFfYM1QKxiMBDQWkAbfydDFj5K+0cm+3UqMTw3QIFhw5ANFgqGBAbsAhYgLX+HRgELkiTc/g6ObjJh8FXrO77hziMXd079FWtzGvC3Hho1j6RhgJT2lLYkMInyRAHKE8f+5kw+/WJB+fNlsaQCxJkRgOXTmAUtQi2n61hW+6Lef35KH3/NWEpoeFe4O3/+jQ8+/46TJ9Ytds9ixSK4gz6kFJDKgZAdSGFDCgtS2iBpA1KCgiVrQkj/2FtBEP70dX9DGyFALGBJB5Js+AfbEAaeQWd1U+/tQ/7CL73nsd99zwe1stTFRDTgIOyfGtoHEN6pb1hTceCNeOIc3REwjN5JvyOf8ONI0Wuoz4SeIb1C+Tsg9JTdueUP/+izj/3yr/zxE25v1WHvUH/rHNiwFQy3GDA0gGDHQEHwT1vm0f5KDAXNHTBsaLLgEUHDhSEPLnsgkujaKziyeQinn3kKCj0onJabG6b3lldeca8EPwJ/D2eOtp/oYVJpqwFSLqRQkShAmduc8audtAg6HgxKrumGhgDKPVZO/bGkhaIcz25aH5ZkA1Do5kRHncQz3snGEgg/M5Gg1ZfceOymF9/5gn5/+2nRtTRkAOr7+z043RWQUv6MdGUHYWcJSMuPAEAEJ/tJPyqg/N++AhJEFjzN0Br+McCwsXHkogHDVr/4y+8581/f89FnB564hYj6WrOdBvrApMc9WowWc8/H4fhxhlSPf8o9JBkDQSb/2vh62hBB/J7R6AiifU3d23/z9//6iR//6fd86eyW5axuXEp9z2ZSFgCD4XbBzDpkSZgg4iMA4++qaNiC5mAohtifR0ACnmfguh7c/T42N1ZgvD101D5OPf0Z+zWvurv3/MuZBJmzwRJLM6qMJIDOPRkwFBFIfZCZP3PlWdICESd+Lc+kJmrhHIAWt/I2qjY3nVIET9WnAe8/Zd//pPB/aDdAP5Ph3Ys7/fd+79ffe2STTytrcBZKaniSYBwHRgEs+/5JdfY+IAwgbIBWQbwGoAMIB8qxwQR/MFkIMEkI2QGTBdg2uGvBrNjY9sBsH2VDx+1f/tUPfPa33/OJz/YH9EIhaN8YdpKqKQlUo161/2UcAQj9Dn3KUKoxEIb9WLQhrneaAtpwVxB6muzb3/37n9j9yV/8w0+e6tmec/i46RnBcsX2ZTBDkoLQAqw1QC4MGYAFyBCkBqT2DwiURoCMAzIWFGzogQfj7mPQ28JKR8BmF7S3jRW9L/T+E+J73vb8W1eU/jSD+4LGYaKEFSJRChuheScDIteE1GHt5Uw3Z2qjmm3UaUTtUq6FBkAVar5y2/X4kqndOmZoN8U2iH4Z/swRfqWQlzYMIgznlBn96BvvPHH/847JHs49LNekgSBGT2sMJIEdAU/0wGoPkH3/KFpSIF6FMKsQtAoSDoS04LEHCAHL6UJYHUh7BbA6GLCAKwTOegN2Dh8z2j5Kv/Rrf/rX73z3h8z2Ht1DknrGcHeiOoZ778eqaOTxh6/FDYEpVRI+0S/+Sc0Tlx8H/WmGQAI/bdAhgYGz2nn+H/7pZzZ+4ud/6zOndrXsbh719l3AZUCQDWEk4BGk8Jf+gfwjhQQIig0Ua/8vBKSxofsK7BKgDSzJUFKjt30WGPQhei42hcD+6S+ra6863HvRNatHBQmXmSUBJgzSUw2BtLullwTmfHtTkzX39re7X/FpNjouQk3ko0oGwMGpBqBaaeZVEzXrnBubTf3qhGialxQO/6d2tBkhXEEwzBAE9q47TucefODu/a1zj9lCevC0B/YAwQTB7M8dIAWmDsBOsLufgZADCDWAEAZKSgx6HpSzjpW1TYAUjAEGxoMRAiwl9nrMa4dOsLNyVP7KO//bIz//K38gPOYbhaA9o7kTCcWHATPqZEfQNc0YSKScIA8gtzEQVoITbk7oFopkhNMbA1sQ+o5jXfFHf/KQ/qmf+a3HtnfYUtaxvuZVQHQwMAZkCUjH3zKYhAMmAUMGRrhg2ofBPgz3wfD8ZR3EYAI8NvA0A+zA0xJK2Njf28HhNVs89sjn5BteccW1F2/uf9R4+ikgPqsPiVZA8uRATrwXp/xBrqIvFCd8y8t22f8tKlUpSf0RgINTr80ST3w5WJSjk4n8ypoYlYQuo0x5jYVoJ83Mu5bZ+8u3v+bau48dVsqInhiA4YJgWEIawDLG3ylGSJDZAJkN/wx74YLULkjtQVIf0k+Elc4RaC1wbmsHRgqwEnCNCyibDx8/CeCQ+Nmf/Z0v/4df/N2vEqnrhCDPaO6OQDTBdQ859pNAn2QMpNQfJ97NqrsgmpDBOO7954kGJOkO9lcHCOK+sjq3/rc/+sLT7/iZ333YHaw5G4evHuz3JcjqgLoKmiRYbABY8Z+LMIAcgMXANwTEAKSMPx6gAFbsL9UUFjyswONVQDkYuAMMBi5I96yLLzm0+/I71l6mJJ9iNpIIOqNixjQxDDC9PYZqYNTmc/UAhR7fIvUpHPmzpClUcz0dsCGASYp7IW2klqvXAKVHHzLnXiVHWRMvZHhhgfcP957rNq992T3X9089/WlFagCPBtDk79wnNUMaf/KZYYCNDTY2wBLELogGAPogof1dbiwH7Gpsnd0BS4nu5iaE04HHNturR/Ruv0Pv+Nnf/tjP/eJ/3yG1eq8gklobiZDLORE6j9dPmmedXtaYt50kKX5tErJHRkiajNiXNIMlUeqwMMwwBg6B96Xq3P6e//HFs//2Hb/9xWeeMnZn81I9kB3sM2HP8/z1/kL5GwApAIJBiiCUCpZnCggJkNKANCBFgLRhqAumFRgokJTo7+9jxWL0zn3JufeuK/evv0w8K2C2GVAATDgAkGeef1qazDadatwefGp9aeORrQNIB94AOC9opq2UY3/z6pCRoMDkv7EXz6k9bvyYt/CafyKAjd6+ZIM/8e1vetGFDj8r2HuahNULvEV/Vr9kQDCCQ+gIzAr+6jwGQ4PggciDgT87XZBCr9eH3XVw6MgRnDu3hdNbfd646HIz0Cvqp376tz71n3/7zzbtTuf5QlDf+MhHSZ75BASHvfykSEBiJWSDdt00YQSEf2d2pNHJiobRJYGesDu3/ub/+Jz3r3/y17787BlPonvM7cEGdbr+qYlk/PkYMgB+YUPJLhR14a+8ACBdkNRgafxJH+gA1IWnCcqxoSRBahfSnFIb3R163T2b96+I3seYeQCwDhcjzzBAJDkjLVE07VTLYspDjFd83nx10EFHx/OAxgZASx5mS9RYeMpXj+meeHmexTJMi9Ck94/jcGvmpj+h5OTvJissMl9+3e3HXnb5ibX9/TOPWusrGkR9P5xMCJb2IdjOx9/Nn6AB8nyZJMBMICMALWCYwGzgGRd2x4bHwNldlw9feJL7WJM//XPv+cJ/+b0PEDNdJYj2tT/bP7YXUfRXHOxb8V4kxfOnJB/9TTQCkpkZgw4R9rtd+/o//tCj/R/68V/98pPP7Fn22kV6QDY8eDDkgkkHvC0Qd0FmBYQuCJZ/HoDQIDWAEAOQ5NH2wkYIKCWwancBr48L1hXc7cftW2++vHfb1ZuXgc0jBLIISNw8Im1zoNzbAcRrYFq+gg8/M3nizekCWtH+DgC1ph4DRaZEAKqoW0NRW1NbIWpdWGiW2qTIKuX9h66NPKoURjk64fgFnrxkmCHBvHvFMevcAy+7dZ/7T9lGb8GSGpINBHsgMmAy/lnzJKFIQUKA5AAk+wAZCPgH0rCxwLD8IQJiCCWws9/Hmd0+n7zienh6Rbzjx9/9mV//7feRZ8RN0vf8J2b7+/ryuFZiwJ9WvdOMp9opKRY+xUiJRzLSdjOcyMfoEqGvHPuGP/nw04Mf/snf+OKXHtuTwjmMvtGsyYMhgCHArMDswOguYDoQ1IEiCSX82f9SmeAjIIlg2RY0GMYYrFgWbO1iTWlh6TPiO95yyxWXHzJPeZ5+MtjWMbuac44NTB8GyKqPvFGAgvlqofb0hjN/H/JSLTo1g8UtHQKYl+GxiHkXnAp4TRRHkwIkfOvC2Oh/6k33HH3p4XVPsHtWktAwxgCsQWAI+JFflgIQEgQJiwAiF0QupEVwPQMDG5AdQCl4MPBPtVuHx7ZZWztBWzuKfvTH3/3QO3/jzyzPiKtJoO8ZdsLhbkx8zwqUZ1CSleBHPMo4pYk0wWdKNCDzNuddqggH4L7l2Nd/4GNnuz/yE//lC888Z+CsHDEuFDQLeMEBTQbwT2g0gCILiiwIoSClgBKAlICUDGURhOWf42AMY8VeAfcYR1bWwL1T9lWXH+698SVX3Gex/iIzkyQq1NDCm/9Mr/vQboN1PaiFokXsbw9WP1/ZAGhfkRaIOPFrSynFv5vai0+/n5Yks0+MjcPGw7AEgGnUtxpmSAKfvelkV7zsruf1uf+UGgzOgVmAoUCaQToY2xcaRkjoYP95ZgPiPiQGkErCNQzXYyhnBa4ByLbAysa5Xc2r65eJ0+cc/LN/9c6P/sbvfOiwENbVQmBgNJw46k3YM6O4f2pVTVYBxRLEkZWq4QvBNyRGIy4pxkZYlSm+feRrkjEU+TA5YGjbUpd89BOn+Ad+8Bc+9eyzWkJcMHC5g74haGIIwVCKoI0LY4bRGwcSFgQkBBhKaFg2A9BwHAdOpwNJEqudQxADhSNrFp587OPizW+8vf/ye67SUmDXMAtQMK2EkttZ+G9WRScFT5J/5LhePFGrqIItv6QQVa26ZiIALXugrQ0N1UkJ5ctX5GIZi1djRo7QDn51hf8TkjMRGWbuHV/XD3376y+7Q+gzTOa0tG2G3VmDZxTAFgQIYA1mDZcNPAQhawMAfkRg0PfQ7a6ChITHjO1eD/baIfSMbUTnGPa8zf0f/cnf+cB//+OHLpNKXUwE1xjYE1US/yQmSChM0p0k0EngTVTgE2ab5a7HZjJw8q3Juxy9NfGORpNLMLtOx772oS/15A/841987OFH9m0jLtBkrcFlDx768LALzX0Y9o0AeDbIdCDQgYSCIPbncUgGEwV7OliQ3IGFFVhksLni2tvnHuE333fR/Zcelh9m5h4R6Zh2CWUL/c2M9ydRnrcq3zDAZKppXsZ02SWzLTbl8G1mTg0o1NIhgCUVo9Y11fyU0zXNuUggiYwBK89zv/jC69bvvOMF13gObTna3fI9RigMtH/Sn4ADCQVFwx3n/CAtCQUl/c1nDAj7/QEOHT2Crb09rB45gq0+sxEbLDsX8z/6wV/40B+//5N3WJY6CoZnGFZhjUc0Lmzcix/i4/A6xROksUv+FNs3mMaffI8vx0NLGhMJ3WOQBbBrd+wbPvFVm//1j/2XJx5/2pVkHTFG2dh1d7HrncHKhoJQBoIUCCsQvAaBVRA5IMEAaQiCP7wvCEwWCCsgciBJwVEGGDzl3HD9cffem47fY9zBx9mwytwbILS/NDhxwUBmwRd3GGCB+50lAVgaAOWoEeuwzS9Tim5TQyupbsnE9fh06yLh/wwyDEiA+y+4+vBz3/519yndO6Nt6pElAWVJeEZDWV2QXAXJFSjRgSIHlhBQQoKV8meOQwFGYdBjHDl2IU6f24XLEqzW2epewC6vy//jH/z4hz740c9eL5UUzEYDwal+0/XMoCgiphkCI2Mg5MUj9Dd7iUTCVYp+4tGBsPy0bLmsiWmVE/W7LYBdy1aX/fUTzP/sh9/59Bcf2xbCOWactQugScJa6QAyOJ1R2hDUBVEHghwQyTEnwUPDAh4kAAsEgjIaa86AeltfxBtefo2669ojxEY/wQwFZG0QVOwp5/KqEyNDGddSVWhj31KvTudFlLcBihoALanA+Y4PVRFYIm+e97eC+NI8ax0GSKCRB5nhdqaFUmMNJM0QIL/D9g6tuB/65gevvP+yE+uDfv+MI6XESqcLSRIsAGdlDSy6YLkCEl0IsmCzgCICS4IWAkJ0sHNugI3NC6CNwm7fRWfjAhism9NbUvyf//jnP/qhTzx6pVLyuDEG8LeoGapYQ50lGwJxfA+nonhaQthI4OCvIeIdADppCCCJNyNZ/vg55AD+bINkzGaCE1lg9izbuvizjyvzT3/onc8++SwLe/2kIecQTm3twQgJTzA0jL9zIEn/ACdhg4QVTO4HmAAD/9BhQ+wv6/QYmw5A/SesCw73B29+6QV3He32vgAAgjjBAAg9k/Txj4zLPLUa8lJmjZdsgE2E/6v37zPuo6tQnf17XRRSJEcEYF6VvUAPeeGpmuEySy0KRAY0AMt13UduufbosXvvvE5vnfmK41gKJASkVGBm2EJCKgW2OiCxCoEVWHBgQ0AxwQiCVhKeBi6+5Er09zVOPXcax46fhLIO697Akf//H/qPX/jQx5640LLUhQz2YCAndKstapQNrlmR5ASnntfWnM9cc90lHzlx4oJPEbAPZjdP/uQEHHwmhU3PnEyJY9oEZQwb21YXPfLMhvdP/+U7T58+uyY2Dl/DHndhoPz9/9mDx64/GVRKCGlDStuPBBD5mz4JCSbfCGAoSCh4vR2sd1xI76vyjhddom++4RIQeI+ZbIQPwhi1t0ktE4s57eLMhwGW/WQxWkQ8y8573gwBHPwQUVOFy8N3SmgyKUkBdRP7xIibO+lFCenfOHp09cz9L7nluIPTcn/3KX/5FxQGrr97n20ruJ4HVh2w3YWSa7CoCxsKliD4kX+Bjc3D2NveweraBrorm3j2uX0eeF35j//Zzz/xlx99pmPb1qWG2Q/7Zw6ZTCloVug98uHgk5VmckhgOOOSwd6hI2ufefUD9z331rd+rfy6t7xy67obLvnL/mDwRRBMoQmDQ12SizD5GQ4jxIYTpteVfzFYKCGZ2ShbXvS5r3bxz//Fr53z9IXU6V4KslYhOjbIMoBywcL44Q4hYEhBKRtKSQghwZJgLA0WBgQFKRwMevuwqQ+hn7Y79pb34N1HX3Z01fsQAJCANw5e5GvE6ZheNcxWJORXd/9wgDvTRoZ420m1GABNhIlaT+fDPICmhgFG4f9kvuWcoFAolqCNYekN3M++7PaLN1/7kucdPvPEF/SGw8Id7AEsYYyCrdbQUV3AAywpIYUAKQFWwdi/siCUDUiJbc+D11nDHtvoHrkC1upl+p/8i5/d+eDHnx0oy7rUH/P3w/6+sVnACAh5fsWd5YwhlKTUDD0YDL506SWH3/9N3/DqMy+555bDV166duWDr779+u/5jm88fNed1z0L0/8oMzRR2rGPxeUmEYW/xAs7UXgO/TsiCQaTFIf+8nPb+h/945/b7okLPaycgHHWoTtdaJtgbPjbBgc7O0IoQFiAMmDVB8k+2OpDS4DJhiVWgb5Bxwyw8/QXxD23njQvvu3aDrN5lhk2EOz/nK1wIiVGNLITTOUwNVvLupXl+H8JqnN4N0S0fujC2BBbYrLpjFIvluvOKcy1FIvJTPnVySkw1YMpoXCoNywfBUyvr3w846k4HaUnUifkTRORqKYP/aPbscjo8AuFrxEDRCNPLBjbNQQIw+bMLZfJT//f333nvZdeoN3B2S9Yqx0FSV0QSxBLSKEgbAfCccBCwWUN27bhjx4Da4c2wNLGub4G2RtQ3QvA4pDZ3RXiH/0/P/PZj3z62WNSygsI0CBIX/9QNCKhwU1cyvKCE6pu8iJhctQ/idgDpOjtbr//vpdcefJv/61vveiyS453Br09rK11MOgP2O50aWfP43//C7/58Lt+80+/anfW7g0smckhjSjvxK8ZlxITTaSLXAttkzSaGxCUl5lBBGNM/+qTzmfe8RPfd5swz7LST5PobcHBAP2z23CgsLKyiq39XWi4AO/BmD14rgfjChhvFeQJ2BiABttwLA89l+F2rug/cvaI869//dPv/eQje/dJIs8zxvFFD/Uk+BscjZ8BJ1QLhwoRndERSsexTBNEEwk46fbETUq6mErZjl05+ImYcTWN/xczAHIkTHXuqgyVZm3zVU6hnCZ/5qXWDgFw6o+ZS18gmq3enPqrmh5VXhQi9gDwYWf3k19734l7Tx5zevvnHrdWOjYkxLgHJglDFpgljGvguT0YPYCUhM76BtjuYGvfhWskOp1NuHIVe6bLauWo+P/8wL996MOffvaYUvIIfI4TAJnUNScZoFmQHQ2bp8X1wylT8oI9QUJube188NvffsdNP/Yvv/+Kyy7asMjb4UOrFoQeoKOYyN3DkTUb3/3tb7niW775VRf09vY+SoAU5J+Mlx6VCN1N0DE+qXCCEg3KxCRp94iZIQQ5X368d/U/+sGff9ylQyRXjkN0N+Gxje76BlY313FuZwsgC0I6UFLAEgRLCpAkQBKgLLgegciBHmisWITd049al52w+d5bjt/W39v7kDbGAdhNqgaK/Z4oQ3nrfrLUE9/SLtRNy/6xqLi21tikAVB7PH8R8y5pRHVW4zQESMmS9H10beRSEQjQzGS7g8Hnb7rqgqP33H0DGD0FEtDUgYsOPFLwhIBRCELABkZqCKmwtnEYu3t9eFpgde0wSK1CWmvoawuWdYztzoX83d/1A6ce+tzOUSnlBcyTakU8wCnlo4RbY6AcomYY6ONpEJoLEP0MJ+QRGY8Iamd7+y/+3t959dVve+trj/T2d82KI6VxezTo7aC76qC7sYLuigPAxfETh+lb3v51l7z5Tff3XLf3KYCFEGxoxDPjg6THPDQGEDIIcsTO4hGfbCJmgFmsf+LTO+v/7J/8lNk6s8JSnkDfW8FOn9EHQ3UUJAlIT4FcG8LrANoBaQdGEzwYDIwH0VEwirC1tw1laQHv1OBr7jiy+ZIXHBkAYBkcGDU2Xiaf8bS2W54KvJTz9aQOEC0ijk0PG7Q2AtAE1T5WlBoqKsdr+KUyz1rCRSk5qg42Znp70yAz5e6QJ/ljsyePm6e+9tVX3rS5Rv2drWdUp2vD1QIDFvAgYIQELAWyFYQiKFtiZa2D7d0tbFxwFFv7A2zvaWwevgS7A4XNw5ex663St3/n/+8zH/tcj4SUxzEcs4hRHAgiwfmkYY3Qb4p40ONUcbBHGOAj9yn+cYmEMu7u+7/32159zdc9+NLjHdLGElq4gx0Qu+iudWHYg+7vg0nD83q0u3WWLzzS3fyub3/zda9/zcvOwPQ/AJAgIj0hIywfiOoWMgqipR6Wi0Y2TlrdJIXJM4gYALPY+MDHvUf/zY/8Mm3vrpnOxmWwVo9jnyWo0wUJAcEM8gjsWRCeDWgBNhrGeBC2wI67By0Fep6GtICzpx5xbHFm8ODd3Zde4Jz9M23YEuB+XoM2PVoWK+VMMLpq+L+K1Crh/xSebe3TF4BqMwDOp0qbTvOsjXkOA1STTxNfCmkyMEyWt7Pz5y+54dBL7rz1Cja9Z21buei7+9CCoEnCYwUNBQ629oUgaGhs93ewsrGKntdDd20d0jmEnZ6C6lxknjst6Lu/71/81ecf9S6VUhxBCvgnFWYE4UkAF8b6tBD+BNiPwVdkg7FLgOX2dz/wd7/nG6/5/r/5lqNm77RxbCPWVi0IvY+NQ6uQZECmDyID4w7QXXFg2x7t757CRUesC//h333b3f/8//qem9et3vuIIAVhMMXoCHQJ+fix6EDSgPXI3onU29RaTiUh1RV/8le7X/qH/8+/w25/3awduRyetYpz+wNAEhQZCBAUS1hQUCAoGBAGIItxbncHAwOsbGxib28HthpADp6iu248RC+9/cqOMWabIaLLAuumHGVPfduK38hJ50/f1maqqyaajQAs9PNaaOUrUOnwQTrFO7IyUYTEwdXxRSWIAaYXPf+4efvX3iFXxPZgsPc02dJAgCFEAJrShyY2/mlw2hgY+FGAfa8HLRU2L7gIrFahVk7w/mBDfPf3/+tPfP6rvedJITZTSjSVIpGA4EvUv0f0V9zDTxhPH+WJDbtLgYEgWOztv/+H/u//7dpv/tqXX7h76kk+fvExQeThuccfhmMrsHGxu3UKbNlAtwNpEfruPnq9PaxtOOjtPovVjmc98Lr7Nv7pP/zOFx/uDD5MgC0l3MSpCCH9/etDg2CoLyOYpxHz+kfxj9HPBBshddggjYSynvfxr4j9v/cDPywefaan4RyDtXEhWEp4pKGhYeBB8ADSDCDMAIJdGHfgHxlMEp4QEEpBCUDxntVVg8Hr7jlx+/UXW3/FzCQFBpOSo3qmef8Tpcn1npUN/5fIf6BogcvdoOqtHgKoPny1qA+9Br0r4Xgx+VNTZ/rLSbmzOU6Ez4n7hsmy3a33vu7uQ3ddc8XR/u5zj9oY7MDr78OWgNA9KPKgiCGDmfP+5HECBDBgD4ePXoCVQ4fw0Be+jPWjJ3lrT9Jbv+MH3/f5x/sj8C9SM2Hvf6gzjy/FyjAE0gAkh/lj3n04bbCdfQR0laJ9Buxjx/j9/+KfvP32O190yVFv/zmw3qG9554Adrdw6OhhCEugt70Fa3UVbDxwfx8D7UEbF86qja2zz2DzcBfGOwez/STuuesG8Q//j79xe1fsfmjQHzwshdAUnJJHCPRIMwiCVNH7DAJHypz0kBObzejelKfht7vVTz7S2frH//Jn5VOnJRt1jF1lYyA1POpBiz0w7YJ4D9Lbhe31QdrD+soqBCT2dj2srGzC6xsoD9Db2/bJExviVS++9F69d+69zNIicD9Vh0hdZI5/FaBIML12as5uOI/6Y0782jqakQEwr0kQKdzaOmZUW6NpoMkl9NFlaRwGLjkWGb3DQijFbMQrX3aDev0rb+089dVPkzB7tGbbEJrB7gCs+1BGQxgPxB6IDYgZRgDCsmA56ziz4+LsjsZ1t91nvvDwKfqW7/3BDz55Vt8mhFgrXsCUW7F7cRAcTwGIhtETt+kNvgTXWSnqGcPdG66xP/ZPfuCt99x12yWO3n8c2juLjbUubGmwstkFs4e9c2ewsrkBy+pA9wf+roi2jU7HgRTA+voq3P4OLOEC3jbc/hl8zUtfQP/233z/nZddIjRgpJTUD28yFNYvbJggViYEwxjD55gwzzEa4QhdSKraxOoOty8SGx/7XOf09/+Ddzx5bn+FPOso94WDgZIgRRCKIeUAltCwoLFm2fB6LjzPg2078FwD2+74e0b0PAiz491710Xq3jsuspm1FP6MwNHgdil8zzgGuhRVnatTa746nLkUnm3ty8NM55I3HyUbAMvQUQ3UzvrKp1Vx3bMnOaVRVlcZ5TGRMrggBQZaa3nVJeJP3/yai2/fWNUD6W3bwtXQrgZ7jI5woFyN/XOnsGIDkvexs/M0LAforDoYaIbTOQqNQ1hdv0p/4fPPie/9/h/68CNP9W8RglZH+nCWxuM9AMbqRb3/OMiFB8VHIfMhSNIoe/QTA9vA8zZCELRn+L5bL3ro73/v62+79tKucHcfZSXPQVk9eNiDf5yNhmLA7jhg7YG1B2k7EJYNA0AbA609MLsQgqG9PgwPIOCi13sa115zCD/xo3//+puuPvzXYO4oS/RDeoTC/VMMlhjwhyMjqfUVfvATFkKIkiamkDjy+HMb/G3f81N/9fgZqXn9Yu5ccDG2XI097cLp2oDnoksEy9NwACgBMHlgoQFBMGRDig62zjyhjh3x3Lc+ePKFF6+c/VOt2SJgUKQ9ZxGl/uCEb0VEVjG4503t1aydlC8EXGsE4ODZDQs+4aVu9cv0Ojn7xDJdJxH3mUl11faf3vWCtTuvvmyz4/aeVWx6EAAULAhScHsDOMLBofVN6N4uBPVx9OgaXO5he38f9sphPHnGhdU5qR9/Rstv/d4fed9Tp9S1k54/Jw5nUCIYpYM/RdKN7xGGE/4CLxrRkDrFrg/Bnwii3x888rLbLn7sm950+01XXbwO6j3HytsiW/QAsQePd8HwwKzBMBBE8NjAACCSYCYYFmD2hTEbkDAg4YGgQcKDFC4E7cCx9vBPfvB7brn7tiv+mrVxLEu4RGAxqVtoPkAI+MGRukEobdjwidbNZL0nUcosegLASqlLzu51nvf3/+9f2H16x4HrHNdq8wTQ2cBuz8C2V6D7BqbvwgJBwC8/SwOWDJIC0rKx3mUa7D5KN1233nn5i6872e/3PqYk7QayxqGp6eqWzZGTX0208OH/BQahBqZkhanVcwCABZkHcKCGATj2tyw7Hv8pEhNN8HgoWTxLKaU2xtx6w4nON7z+zu6RDeUO9k4JwRqCbMAoWMqBNwDIk3CEhX5vH8oCtOjBgwvVWcXZfYETl93mfeJTz8lv/d6feO/Z3f+XvT+P1+W46rvR76rq7mfYe5995kHT0WzJkizJki3JsiyDMRhjGzAGPIBtSEKAwEvyhk+mN8mbvPde8ia5bxIu9xKSlwTCHEMgQGKHMNkGz/MoyZI1T2faZ0/P1F1V6/5R/TxPP8Oezt5b5xzbdT77PN3VVauru6prrfVbq1Ylt5lo85/++U0gAdO0//WZf1UjHlwYaMD95X7D+n2GWtW0UXVGRPLcPfMd33Sje9fb77v+xdceVJufxeZtmc1S6tYCHRwrBMkJ5cY3TgNBlRDinwZFfZh4YiOCMUQnSvW4XptG5mnW2vy9v/u2W779tbc8HFywEcBQh4CYMaYvxOcZh/vHEIIJoWDs/Q3LSKXMpgeYAGqMmX/yxJ7W3/77v/7cEyfFhsaVoS0HkfoRur5Bku0hTTJUPUhBkBw1OZ4eHgcmUEs91p1Nmknh3vxtNx6/76Z9rcLrfmvJ+43bLCI2Po7WTuM0tvpBbneG+hqH/9e/0zlXudBFjwteAIjpAvcDWP9O5zftsgS5VboTU9walTaa0o2h51xIju7RD333a2665YYrD+X5ysnE9bpkaYYGIXcBVcvM3DxpVme1VdCc24smGbXZeUxjlpVepscuv9l96KMPJ3/n//jVDyy2/V2l5l/hLFOgimntnsa9xi9XNV0qGnK16kBAUIzohINdKQjkxpikKPzJN3/77e13vfXea+cbPS3az0t3dQFxgcwmiBiCFhjjEfEQAmgAVUwA1IP3SFBENfpGBEUUrJTsVQzWxs1zaknGXDMjS9sYzvAjP/S66978ppc9VhT+WRGTGKEY1/oH/gBT0Iz+8cT7GTseKUNFCBh/9VMyqvIGEJLEXvLk801+8u/8l4898ERukrlrwmKxR2kcwtkmzmTYxGKMYCxgIOBRChRHgmfWBlZOPmWvPb5H3vmG2+7IF89+MGAVXcchcFraHn/Z0XThIrgXmRJXJXqBpy0KABe2Q8PupK9n6Gq70sNGhXXkZ80yk2zYgTHdbucT9956+EWvfcX1zWLxtG0vnJHEpmBSvAg2S2KIVxEckDQbpDPzBDvPmRVDY9/V4dAVN7s/+YsvJj/9f7zng4sd7kSkISXzrzKitdLQma1SdCPmVtF6qwyxyigRBuv8R4UCwYh4QbKFs62PvPFbbm798FvvvW42a5HooiTSJTUGMRaXK3m3QL1iUNCAqoIPiHoIHgkOUY/4EI+Dx5TCAEEZBCdCsJqQkGFJSSWgbpmZZqHveOurrvn+736ZP3vq7PsFSY2UywQrz9JfzdB/1vEVAyP+A2sIARPQyuB084NS42sOSZJccvpscu0/+pn//qkvPNo1c8deoh326VJhcUktbg+d1LDpDElaJ0kzjBGsOOppgnUgvZaQr/g7bjvaeNOrr51X72tJYscwiXUk3E27ymzho9s0F38h55av5zn0XNMLw2vXFgDO8f4X6+u+oNILYAaYfmWDu20CaZ3mf7X9NErDWlwIIbv9mnrne7/j5mP7mlosn3raivfUbIM89wQDjZk6iRVWOqvkNjBz4ABn2wGX7sMnR0Ouh+UTn3s+/Yf/8o8/2sr13tLhT8cR/lG+M/YSlIFNu19qM8yfcebHkAnGa0MpYdSurl5E7eKZMx/4kbfedf1P/dVvvTrV06TuLHvqQiNLSLIUsQmFU4IXUkmhCFHT12iDN95HZu8Dti8IBI9RMOqxwcf4CUT+I0ERsYhmLJ5cIBSeQ/vn8cWCzDSc/sBb77/8b/74G+5aOnPmAyKkRnDTkIuRQEHV52b0XUxj/CP9IJTLC8eSjJWrHFSMWwbw1poDp5bSy37m3/y3L37h0VWz//iLtb7/EnVZHZckFJISqGFMA2tqGDEkJqC5Q7rC/uYci6efSEQX3Q+89ZarD8+c/YDzoTZcFqij7dhCWkuAOFdL3PaKXVjw/9dzOudXuEbFF8YEsM2O3w0/gB03A6wJIV0Ao36nmrCGe8B4gd101RDVQoPB5SsfeO2rjt5y8437WDz9mDVFTsM2cU4IYuO2vhawSnMuY+7QLLlVnKnh7X5/yVX36B/92SPL//D//KMvrbbDXcQNfQbK23rNGWVQQxRDKtxnXc2/8juq9Vcj/MV8MxAIANGuEezZpfYnfvxHvuXed7317gP5yle1wVnqpkcoCoo8EIKASbFpSpKmpJIhPgUvJVMPUQggYFWjYOAdVkNcJhl8FIOClr9lsDsVrK0xM3+IWn0PvZUO9QSsLouVhfD2t93X+Ed//+33ri4tfAAhMYZcylgB42aA4fOO+TVU3s80Zl5ZPTiiGUulzDBzXT3cAt5ae+TpM8nef/Xvf+vMY6fPGrvvgKPZRGozqGkQtI76BrgaBBMrtT1ZaJJJQq91Rtqd5+Wyy5O5b/2m6692PjxrrTWsESFw0KLdgP7HC5yL5LGlG74Q6SKbu3fS/v8CvP+LxAcALpDR+MIlHR7sFgqw6fpVEluZVAaz8s6hACaR3Guo33JjM/vmV121T/OFYvXMs2ZvvUkqKatLXZqNJlk9Y6W1giRCOluno56uJMjMobD/6I38zz/7ovnnv/CBz5443brJmmgNH3+6iRXd0xTOKdrpRsy/X2+UIcrk9cqfMRRGpL640v3sT//N77n0+77rVUnee06bWUvwq2QJZFmKWAs2IUkSggHnPaqWWlIjkaS0/0feJBqiD4AocYt7z2hfRQFAgSCKomgIiHPYYEgbs1jASsGeGTUuP6mvf90dyT/5ez98b95qvR/IjMGXKxUmBB7TRzrG3kf1fAI5GbzjMe16nXFZFRPGLDYWcEmSXPalx9JT//hnfuvkUq7p4StvLmjsRbI5xDYQSbEmI5UM9YZ6OgtaY2VplZlGQtE9Y0P3jH/dN19/8LL9S486H1KRMLlb4IZp/DvZCc1pPRqbob9d7X8zZr6vxXRxPPALLABcHC9lMl2s7a6k7Zrzq5rXJipvRUrfrEwhhJ73zOzJlt//7u968dXXHJ13y2eeseIKep0c1/U00gwTPEWvRZYotWYNGrN0kv2smoN+z7Eb9X988AvmH/+rP/jAaifcZYwUPgTDkM/Ee01V1YacfVLbhImVAJtg/pEpjUbJM6NasSaWHEiLpROf+Of/4E37v//1N1zSXvyqNmtBsELPB0KaQpaiVrBJghcBDIlNIyIgCcYmGJNikhSxBjVCkIDiwGi8mRhU4v6/wYAKEUkRJYhDpIdITgiufDsJqharYIu2hNUT+rr7b0j+wU+95c6anv0UkFgrWhUCJm3/WxUCqpESxwSDNfpHxuzolc5OQIskzW74zGPmxE/+w//22Oce7aZzl93szZ69hMxhagVienhXkCazmGyOdgBnABxp0SKcfp7LannjnW+657qi6D2YJLYvVE42Zse0/02qm5v5VrdY5+JIF+tDvHDtNuve6hxHxoU5oC4yKOlCq/9C4VlrFwvWmuCce/z+O/df88pbjh4Jq2dF26tGnCdLMoxAr9cmTSyzzSYmMQQR2kVCnh7xh6+8zXz4M0/Yf/pv3vfBpXa4W0TqIWhCf4qeJonIKDRQLTKu/feFAKnO++sx/yrjZxImN4Jag1MlS3Ef+Jf/5M0ve9VtjStOP/GJMN/oSSOzLK20mNm7n2CTaOYXQY2iBAQhMQlG+q0yeC2PRVAjYCU6ShrQwV8pBBAFAQ+oUTAeJI8BgsTh8hyPBbW4XqBhoZn0JF9+Ul991+Wz/+hvve0O8rMfQ9VaSzBVIWDieWX4Pqa8r/gqFdDBtapgsGb/bZDKjk9FcGm9dssXnnbFP/7nv3Pisw+fsgcuvyFPm7O0Oi0azZR6s0ar02Wp1yE3iiaW1ZVV0uCZITcsnfGvvOWS/ffcvHehKELDypR9AjaNpu2878zXQv2LZ87eZjpnhW1rFS8aE8DXJZy0k/akNYhsi+65aBbnmKxoz3ltXHXMP/k9b3jpwXqGO/3809Z6sEZotVbABNIsYXmlhZOUxp6DtAtBsn3u2GXX2z/+sy90fuZn/+jDSyvhHiNSBw0l8r4h79AKtx8RAvp5Y8yoKgSsyfzH8sb+gokOAOlcrf3Rf/i333LvS2457s4uPB+amZiZZp2lswtYI9RmZul2ewT1sa39Ny9ETV4EkajOq4KqDsoM/BaMhbLM8MmGq+zLfXbR4HHqCSFE2iEyZWMAAonxNDMvSWjpnbfdUPzM//7jd6lrfRCVxFphGhLQf/6+SWBCCKi0ZC3mPyEDDDI2LRUkoEWWpdd/9tFw6l/+yz946itfOZ3tP3Rrb3b+CnIychMwTU+wHUxSIEj0OXFKYpx0uyfN3sZi+mNvf+ltTbv0AURU4gKUbaUt2/63S3sHPlxd8+RrOO2Y2faFSxeNAHD+0+Y12Iul80fTRq1e4/oWHnbTy7XGiolqISZJ6S2///vecM2t1xzf01hdPm18r4tgqddq5L5Lz3dIGimmVqfnEzpFSnPv5e7QsauSP/qjTy/9i5997+efO9F+hYj0t3GdPv5l8nQSUh5enBAIBvkVNGBM253uIT+A/4MVq6qEOdv+yN/+idfffedL5pJTz341Obhvv5mdmeXs6TNYEfbMzNBeWgLVyOTHWq4y1PgRgzFxPb9gBuiGloLBYGufEiUQLf9g8Kp8KUAEFYLGDZXinycEj2+vYG1gpo7kqyfTm685Fv7uT73tvtl6/tEQUCMYI/hpz0/l/QzeVxwA5fkQixlBVyovfnxhQLXvpm/GM6KSp6Laq9VqN3/qEVn6h//kvz/+yFdWakeue2W3q02Wii5mRsgagsFBKJipz+KCsrhyFmva4jrP+xsurzVf/6orb/BB69bQm3qrddOUgmvJMWvSvDhnofXS1rT/r73n3410HgSA89kx22diO37vLdTfHoW10ZOtm4E2X3ANEG9jwpXLxopzPtg7Xzxz9SvvvGo+9asudJfM/rk5UlKKPGd+X5OCHm3XIpubp+MbSP1YUZ+7NPnPv/PR1j/7//zxF08tFHdbmziiF9yaY3+oPPYZxvTZd5TZr6XpD5lQlbmNl6v8eRExSLBX7LGf/Ft/9XX3XHNp2nvuyU/rJQdn8XmHTmsZXEGzlmKN0G2tUssyrEhcv1/6FShErX7wHqMAIJIg/Qg3/dYFrYywQYsZCbgjpfW9z2w1SrsheEJwaMgJIYck0F05yUzao7f4hHnZjUf17//Ed9996d7ik6p0RcSK6OiWwpQrHgbvpWoekQrKMjQBTGj7DOuPdWTlvLJMc3qv1oTQTWrZzZ95Slf/8c+894nPfOiR+uFLb+mme47qidVllnurOM0pXJcsSxD15HmLmYZgirOmOPtk+MFvf1l6fL79fueZETQ/1+9oN6em9bX/c7/zCFq7TSq7mi5I4emFvbfZ8JYXkB/AbsBKWyNzHgbGBSXInpsAtZ1HEDT3gca8tD7wA9959+zh+cSLW7EzqSEzKUYNnW6Hbt4ibSZoZlnpORr7Lstn9l6e/tbvfnr1//qPn/js2RW911rjgveR8000atznf6hpTqDJVe1/Q+Y/aufvV530hgeDOhGxzvkz1x+a/dDffPe33nXrDfu7S889UDu2rybLi6dYXVqknibMNGq0V1fJu12a9TrqfNzeeNBAQ6nkg7GIWBQTHQJLTd9IgjE2XrfJiLBQmtsnkimDEoiAKAT1aPAxpLA60npK3j6LCV3SpODIgZTi7OPmhsv2dH70B775rusua3xOVVsiJhVw1eeP76DsC9FR4WhECJh83yNmmMluWtvE00cWRsxtUhc0r9Wymz/1jFv6e//n+z7/qc+frO+/5IbA7JGQJyk951ANtFtnURxZmhCKnJkUCSun5ap9bv+Pfs9LXh3aKx8QYxOiK8VaTTi3dEEysLF0Xpqy+ZvuWPN20lz7Atn/lYvSBHABDe610q45luxe/Rf2k9m0KcCLGC+d1oe+9Y7Dd99z65X7m+JEe23J1NBptQh46s06DiWbnYGsQX3+aG//kePZb/23zzz5c7/5+S8t92qvEBH1PtgJlZaSAYxp+UNzhUz7mcppZAyXHgl2Q5+Zxah8VeaPQGJNEGOSEMLySy7b8+hPvevV9156aeiePfVA/eorLsW1c1JjaNYzgsvxPsdaQ3COIs8rDemzUJByex4pb9SH77W058dkI1e3UUjop2kTg5TvxSgIvownEMp3FZ3eC1fge12y+SYa2iyfepLDBxNC5/nGrTde1vnhd9x/15WXJl/odotHjUhijTrKjYT6eyBUN0GaJgSMIy4DUKLC6SeYqlT6euKppiXJQNv1eu0lDy8ms//gX//ZRz/66QV77PhLTU8OOU1nMYnQ6Sygvos1Ke2VHhISahKkc/ap8KbX3M6d15hbvZPCTBE5dyeN2882VWpLV7fchh2ovzX4/3ymi6KRg3SeBIALC2K54AfXTkqXO0Zk92gONW51IWjjqgO9Q9/3xjvrzawT3OpZk6kSfE6aCiY1pI0Gtbm9tHKhvvfSXmPv4dqv/M5fPP7zv/HphYU2d1lLSyPDk+pNZEq7R5g4QyYjY/VGGNBom0chbCoMbOR8EONfrTGqqmZ1pfvFe2685Ms/8v0ve9mRI71Op/VI/cBeg0HodgJzMzOk1tDrtJEQotbpHabP3JTS62DY+OjNDwGNG/tYC9aAWCI7DDHgj4+2/JEnGmEeEt9V8BiNoYRFQoRSpI94RHpJmhKCwxddTOKw0mNuVvC9E40rL5/p/tA77rv75uuaLRfCWdQkViTE1YeVJZGMvbP+eflehxx/tA/GL01Yb8bztXqp8szxJk1Ue2maXv3Ugrnun/3Cxz/w3j99Mp/bf01C41jRdpDWkijEBUOWztLtKLU0wRaL0jr5sP74u74p1dUzHw2BdVGALUP/F6TqehE5/+2akrad9MLfexsCwPl5Ued/gJ3vUb1D0vVW0CIZ+91sG84Z2wQgCEbx7Y9893e8ZP6m6/ZrvnRSXGcVQg6ak9aAJFBIQsfNUJu7Jk9rl9R+5T0ff+rn/tPHF8+u+tsSK60QdLbammnNGlXo4/PJGPcfMQFMg6Arl9Zn/gNGp0bwaJBep/eZ17/i+vrb3nDj3VccT3utpUcbe5oJaZax0mozM7+H1dUVirxHLc1IrMU5h7WWWpah5QY/WvoAAHG3v34gH40ohwzwdjPoxn5I3dg2U4Ikw0aLShQSgo+MXwvEe9Q7CK68p0HFIklKMAaXFwQDs/N78MFRFB3SzIM7Xb/uqvn2j7771bfcffPBh7rt9qcVDeVrmXQCnPb+KsjNuElgvG+qfToxjDcxFhCpQSisNQeePuPu/dlf/uzH/vQjp59KmpenSfOKwibziG3gnJBkTZJag3ZnFSst8Z0T3Hbt/pk33nfgbsAZMz064PaSrnG8pawdsf3vTP3tpvNrpj1/T39udx4IAFv3A9g47YYfwA4S2d200xLmrg6yTUPy27q+BvYS09hMLEIRVOu3XV878vrXvORIe/kpUrrSrGUkVkgyoV2skktB21tm9l/fm9lzbfaf3vPpR//db37xZDuv3ZZY6TqvM+u1YsSzfA1Bp8JTpjL5cQi67zo3wbxMhbkJwRpRgaTbKb7wba+4ce6db77t2v17Ot1ET9X27UkJIdAtlFpzFqdRszZWMNagCtZakiTB+UCSZPRt+NI3BUg09ys6svQPJW7/O5AAYmOHDorRASBS6e9vG5EC0QDBo75AXNxJEO9LeobCQTApzqYEm9DJHd4ISb2GiGe2EajpQvPSQ7XeX3/HfXfff/tlR7qt3leMoIKulO9G1xIC+u0dbJJUef8jaMHIWKo8Z+V3mtA3dZyIpIAXEXt21dz3c7/y2ef++589/WRz9ioj6bGi1bGk9Xk8hp4vSOoJ2B579whJ77T+0Pfelx/I2n8ZAilTQwTvkvZffcAXYMrcGeXsIkRoB2mnIZRtUt3A/g/n1QfgwoJaLp5BthNpiyjAViS57QgBZRLwqtRm0vaHfvB7vnl2tu5Cr3WaWgpooFf0MPWMVvCsemjsu7JYzedr//aX3//Uv3/PF1c6rnaHtdL2XusjdCsqoUzjAmPySGQqQ+ZRZUbVgqMOakMuNE1z7TN/AYOqyfP8E6975Q3ytjfcem3K6d58w9VTDQSvWFvHJhkOP/CUMxKt5abEwVUNEkP1IWIRk6CUzNzI8As35UF0ABi0PZ718zRGCoZoGihPVBSVypjRvkatcZMgFAmlyCAJgRQlwUsKNiGYJMYisAl5p8XBuTpZWKztnS06P/lXvvnS77z/+jzv9j4nIjPWSJD+qsWKEDAGSpTvdFQIGOmTwXsf67/xITciwU1HByRWsaoarJFeO7cv/3fv+fwTv/++h8SkV6ez+24quqHOSlHQISfdYzG1hCJflrl6wTVH0rm3v/aaV3vvnjJm3AwwqsGf+xS0zscs65Zi6A/ydTIBfgP+H6SL0AnwIjID7BoKsLuBJqaigdOw0nNsxUa1jMGpavc1d8zfdc+tlx7W3iozdSOt1WW6vQ6tXpflThefNkjnL897Mp/+/K/+yRP/v//8iVNdn9xqDIUL2px2nwlmUT2V0ePhYVVTLg9HGJGOM/iBnXooNAyYmRfBqGqr21n9wDe97Ior3/7G625OeS6fTdu10F2l13bgM6zNEDGoeNQ4pLTdizEgBkPpuNeH88UgYjHGlN6FFoMpYf/YEKWigo68oFARDga2AZCh5j9uT4+xBaNTYIwDJCUKkYKpoaSoSVFJEBP/UlJWFhaYnwEtTjZmGqu9H/6+22//truuTHyef1oDKlG+CdPf4xTBaqQvqgLDJIIz0c8Tw6FysXzgymuyXjU1xri2Zi/+xT/8ygf/4M+eWZjZd0PatfuLpcJT39dgsbNE13vqzYyVleck0WV90+te3jlW6zyzNgow/buY+tlNZG7uO9z9OWNtxWIrVCZyNk3v6xX+P/c0IgBs3Qyw8SN/XZsBLui03Y91Z9L4FCuC94Ha/mbno2/59vtW67Iamqkz3uf4UGDShNm5Q6z2MuYOvKjwZn/2C7/254//3p8/dJa0/lJr6IVAOo2RT8D/UsL/UxiCaEXTl4rGyyiTGf7KVAY1prl6MVhBi87y0ifedP+V97/1TdcdyjtfLRrJQhbyVXyeE7yApAgQXA5FjjqPKQP5iIn2dsSCmlK7t6iUxxKvq1ikVKeH/gyxMVoN+qdasiQtg4Nr3DZYo7ggeERD9CeQhCAm+gqUnDop355Vwagt72UQ4tJDQ6nSe2V+bi9JUmN16SzzTUPeeraW957vvfN7br/5/puPHm+vrnyUoD1jpIxGPOkYWHmMIfpS6Q8Z9NeowCeVPyrl+/9N7OUwPZkQgjHGHFi1c6/+hf/+5Qd+8Q8+/WDz0NXpwctvLpbbDlubxWR1ej6Qd1cJ+RKXHbSNH/3+l97tivxBa8qQjef74xtpwgXQlos6nU/4f2sVq5fM12/Hn29p80JAASra3lq3WTNjwwsb3nlKjiLiQ9773A+94bL7bzg+s9cWy9JZXcAmlmymhkkTVrqw7/DNRe6OpL/0G5/66n/+oycWuz69zQi9EKhNs6mOMnipHDN6XGUKOqlplkA3SCk9S2R1DK5P0U5FENQhWPWhlXe6H/rOb7nx/je+5obcFs+5+fpyil8ktYKxGcakkXFrDj7HeE+qUiq0JsL+UjJ8a1AsmARh6MA3ZJZ9BiqlP0AVkRi+iegnEPpH9Jf1xbxQSkQQSsECsQOHwSh+CJYoPwz+ABPiu7BescBqq0OSNUhtRtHtMNewNLJOrdHo9N79tjsPfds9V9zY7fU+jao1Ut0/oLJFshm+59F+GHaojPfJeIdPQwHGy44KppVraoBgDXlHsnt/8b0PJr/7pw89ksweS0122PnQwCZzdPNAo9kkS510lp/hjd96t7vxABqCpNPG6Lppm9/f+qW2N5Pslva/K/UvSPj//KXzbALYIYvXhdx3aw64Cyhtu4E7IwQY1GvQ7EXHuoe/5f67VXxLU9qSUlCvGYwNdPIuzT2XFHm+J/2lX//w07/1R0/nTmq3GUMnKLVIaQLUjVpw5dLA+U+mT/5VIaB6YWh3rjCYCjOZ3PdeMOARSVDtdFutT77pm6599Q+85RXB+oWkadrJbGowPke1hxpHkB7O9wjOk6jQkJSGTQDFC3HHvvLmSqnxm7jmX7WM+z9wNuxr/8MHjNq7YiSuux9A/UIMJqRxbf8gsFB/K2DACwRjURNZvinfa4LGHQFFsarYoJgAJij4gPiAeo8xlpXlFWxSJ0nqhCKnmYH4M7WZuV7+tu+7Y9/r77/mQJKaLwAJYMz4/gGMMeS+82L1msC479/g+pS80SQTIYUnS2B8IDWGntfk2v/4uw92fvv3PrkwUztm6ulht7JS0GjMkWR1Vlur9DqLJLKQ/J2f/Kbrk9D5hIgM9gjY9flhrRtc4BPT1hSy85B2DP4/fw+5AwLAN8wAL3jacRRgq1c2WXJ8Al6/jooxIiH/0tu/9xX1y4/tkyS0kGKFuT11XPB0i5za7H6X1Q6kv/ybf/H8r733sVOFZDcaQxGUxoDSlBvK4F1VGOHI9UreGDMRSh4pMmQgVbt/v9z4X5QgHIINPixq3vn4W15/y/2vuf+6or38MAdn1WTqce1AZusUrgOmB7aHp4dVQyYJGYK4EGP2U13QNmy9GRVdBu9Xh0UwxmDMkFmWnnYDQSHG+A+jD10SUZQgoBh8GcpIJaIScaOgaCowIWBVy1gBAQk+/qlHvQcCzcYM3V7AFQZranRbbQxdiu5zmXcni7e95fbrX/vKaxoawmclhjGIPgEM2z6yl0DVNLNmf22AAgzQkrFvSmTibVdrhUBqRFzu02t/+fcf+eJ73/tAsadxSZLUD7rVjqcb4uL/xAZay09y9x2XmNfdqS9T1SjRbSBojHbitAsbE7jwtf+LPV1g8P8W0oQAsHU/gIs5vUBmgIsBdlqLh29mgqoSGC+/wSNWFOzgQ7D33lo/9vKXXLmvaVqyt+7EuB7LLccKewizVzlfvzT5pd/+8OO/9sdPPOWS+u3WiNcYZGVAcTC/j8/4k3xzUvuvaInV69LXKGWsXF8QqKIBJfM3hmBEEu/9gnRbX3jrt99w/3e/9qaiac4mtjhr2+2z2CQhzRoUXpA0Q6xBEsHa6MinAk6V3HlUy6A+ooN+ESFq8toXDUobfl/zLxefB1WKECjUo6KYJEETgyOU/wQSiyQGFzzOK2hk9MGDBMGSgAriicv+VAmlL4ECqgFVF//KvQE0FGhwcafC4PGuwOUF3ilFoQSfIqaGADMp7K/1Mr/8ePEtLz947WvuvvTqUBQfQrUnFSFgZM+A6vtntB+r/Tze5+MoQP9nOB6l/K36BUwb42pCUGOM1Dth9mW/8AdPfOS/fODxR9N9VySLWnMdlGTW4nWVGm1k5Tn5sXd/92oz4wGGItbaabeg/wto6vnGPLzDaZ3HmURdN6qx6+nrwAywG2k3UIDdMgVsQghQsPXUffav/MAb0mNHapq3ntG6dqmnCVo7QDFztS8aVye//DufPfVv/+BZ19HGy4yQe1+ugev/N8bl44WxBohS1etGmH2FxGi+jJTp25uHTKhcN99HBQQ1IloU/kRYaT/4I99z033f+U2H2/nCJ9M5uyLzsym1rEYgIUcwWYOgGS4khCIBNQSJTNsZQbIMJ5TBdwP9pXuiAVGHUGAlIOIhhFF4UhXvFa8BJ+Ct4EQp1OEkoIkFUXzwMd6PKiHEB1QsSkrQBHVgNWAJg/cXzQKeIIFgAl5znPbw2iOUfz7kFCE6cdqgiHdkxoAaermi1BBvkTynViyzV0+ne3m++P7XHpv7zvsveWleFB9V1U60puDHoyz2+6Ha+6P+F6PowAj3rwgEA6FqLWFWqiNsRFQwqkpipbZC89X/z195KPnN//nAI8n+y5MVY30ndPHaIfSWaPRaXLVnZvbN33TV8aBhhdL9cqqcvVvcWycOtkHm61T7/xqA/yOet0OEzqnEBWEG+Ib0GdNOmALOLQl4H8KZ7/3Wy2675rLZuUMH90jwXlo9D9kcPtvvk8YR+x9+9U9O/fvffvQxF9JrjRUXlHSigVOU/apm2D/WDbT/0aVlYxv6TGM2fXgaELQQQdrd/KliafmZH3nb3a941T3XueVTjzT3NqFGhMWjxBAJxLX7loQEYw3WmmhfT8plfgIiMc+IGazLj22L9v+oGhsUxXtPURR45zHGkGSWmZkmM40m6graK0sUvS61LMFaEHH0uqvk7RXq9YyskaKls7qt1TAmofDFcGVACKgGQgiEEg3wWpq1+46DA4haY54o3vfwoYPzXbzmeAp86BF8D0JB6C6zp+45Mq9psfxMePXLjzXe/Nqr7m53uh8DzY1Ri+jQJ2ACERjru/47WqePGcnvZ1YGVbVcdVyNfhgSgoqI9hR7xb/+1ScO/upvf+qxs4uz1qaX+bSxP+5Z4ZdYWf4q7/ieu2pH54tPs4t+WLsJ/e9cutjn34sX/oc1Bt/XlxngIk47igKM09xU9k4QUBXs8Tn3lXe8+Zt7B+bgma8+jNomoXGIE+2at/Uj9ld+433P/NrvP/ZkMI2XGyMhhKHmP8RudWRCH0kDbh21/36VcWY/ARdXbMAjmuQIoxkRELwYUuf86bTbPvlT777npa+668rO6tknk/m5OYxNwMQWRMf6oZd+IgZjDCIJUq73jwzfYozFlBsJGQwJCSolvI9AuSpAjMUmKdba+CeCUUiMQbs9/OoqJi/YM1Nnz755XHuF048/wurZ08zumWFmT4PQWyHkbUxqQDxFbxW1nsZMA19uJhQ3FQrlrxJCQEMZenjsL9oIbKwnOSpdsB0CLbysotJFieGdE6t412P57Clm6t7OzwZ91csuSb/ndVe9rOjlHy5cOGEiz/eTjH4KKjDen2PjYbLPddjX43VGik7V2UVVasbgROz8r7+3e+jP/vz0iaeeatqVznxou4Sz7hS2vsDRPcvm7/7wHffXJH+AaXEBdgP6101X3yT9r1Pt/0JOW4D/YUQAuJjNADs/EC8uKXQXaZwT2c0JAf05uGbc4//0f339PVcdsbUzJx5m3/59nDjrWDV7Q/PIi+1v/N5fnPil333KOK3fYSAPQfvO9iPMeXxSnqatVxvSv6aDwmNMvkJodK/64Z+p0DeGYAzW+bDQKFae+Fs/9MqXv/yWK7qrZ77amGtI6XUvQ8ZIyW/6iHO5tn4zf/Q1f5OAJKhJcF7wQUFiiODUGhKjEW3Ie4jPsRZsAqHXw50+ydmnHuexh77Ekw98jqVHH8Avn8E0athMyFsL5N1lsppFrNLttOKqAQJIP6BdldkLQSGuLo4rEuKfokRhIa4PLAiSQ+IQGxATyijGSrPZJHglS4RUc+phxR6aK+S1dx2t/Y0fvO7V0ls4Xbhw0hgxhrg6gHGTAExBBaY7BFZ9XAZbEE8OmNFBpf3L/Y4bRQtCIEEVFZq/8IdPLfz673zm2ZX2nFl2tRBqNZKGp7f4sLzmZdcUr73Z36iqRVk7rDNJsPaFrX6kF+5888LOu+eYdkzxOv88dwfhp/NpBthuuiAacW5px2xR02luIntbtDWEs29/1dyVt774QDDuJJccmuGZp0+y/+j1utDbZ/7NL73vxM/+xld6RZCj5Y622Ub0J+buceYtUF0jNiJEjEDEQ82+X39AZ6BhRnW03O5VvAsLc37piZ/8wTvuuOW62V6x9Gj9QB1SjZvmaFC01JZRKbV8i8FGMwAmxu/TGEgHNSXET0QIpL+mXyLjlzSG2sUQguAUXPD4EEqEIWDFU+66G+MGFF2Wn3qMk49+BXqrHNlTJ/VtnnvyYZ575Iv0nn8cLVrUakKWKN51cK5HEF+uASj3F1Q/toNg2QUVAScmg1E70j9xC+bSd0JC6cyY4r1BgpCoMN/IsMUKaX5KDjSXk5uvqfX++jtvuYnOwqL3YZUyYqCRYV+tJ6xJlfOPXx9j4lTqVcuOjt+hq+D4mFOIMFWa3fiHn29lv/rbHzvx7IKYtszqUqfNfM3hVx5L3v2Ob8n3NsMX43uji25/w6Ddns10zZOLLV0AjT+P8D+sIwC88GaAHcWmLty0yyjA9qmsj6asIbNvquSUFI7NrTzy7h9+YzHfbJuit8Dy4jL7D18ZVnoN+Zmf+52Tv/hfn2wE7BUQQoixZNbCZCezp2n/1cldhkF9RpCCsXojqEAFXu4zHROd8wWvS3vNmdM/8c47br/2ykbeXnq4tnfGI65DkXcQSqg8YugRNaiCymWcfxhG7hv8EoUEKybG1iuj/FHGBFBjsFmNNKlFOsGD+nKdvkaumLcJp5+jfeJZ2q0lktCjTk4zcczXYTYJtJdP8fQjX+TMo18mX11CTMBSYLWgkUjcCEjjPgH9wMJCABnyLS2RjBgvpzrFCKoJwddQzSAYNAhBDaqWIAmdbsCYJoYGRTewp9lEuy20fZqDzSK786aj4a+/65XXu9bKl5zXBZFhoMEy5sIkklPh5IP+HRkso8x/AgUotf5hP42OpSnDC0G9Kl0gYOyB3/lUO/2FX/7ThZPLMxLMUTXSQIqWXHPlbPKuN+y7Q/DPiNCcRnUEbRi/0Vj+Oaphm04jiOsOUJrIuQjm7wuM0KZIrnVpTAC40N/+9LQzg/IFgqN2I+34rc+R4MTUtcYyZ+3DT/lD7/r+O152+LBLV7tP4wOQ7NdC58zf+Sc/e/ZPPrmU28TOEpVKO9imd+x2a0G2/WV7MKrtjU/+07y++9qiqTJ8xtAB4ko9FYyEsHLZTPH8j771nuuvvMTmqZ7I9s95apmnVotwvNFKxDyRGLN/BNqPxFWiUXggEJTMfvxPTZSH1MSQuzZLsEmCMQZrDMYo4KDooO1l2itnOXP2JO3WIs1MqGdCt3WWlTPP014+Regt0pCCmvZYfP5pnvjy5zjzxFfxvRYSCor2Kqo5GvKBgIHGDYEmkYCKuYK4VlBIwdfBNxHXAF8DzUATIMOHlFpjHyoNME1mZg7hCsuexhyH5veR+I5Ifsa86uWXuP/H3/22u23r9LJ6XSr7RgdMn+moTf9CVeuvwv7jZp+RcTSZPWYGGB94WBFmiPs+YBO796NPHF78N//u/QsLK4dkodXUmfkDdFaele/89pcXV+ztPUvQnAlxg8nTc047PFGcV3Zxvs2uu7sfy+6mYct32AP1YjYDvIDpgkcBNrrDDpRWLa495nnta+4Mq6tn6OaekBxUb47I3/r7/+/ljz6cWpukl4ag0xDWNe8xhvKOemqPaP+MTfzVYDIyggpM8zLvn6NaGA2r1x1Mnvyxd7zqhpe86FDRMK1sNlWyxNDttchDC5PKgNnHgDzDv2qcfpU+AtCPd2sZRQNMhbFSqQuiASUG3THEdfea98hXV1k5e4Z2ewnVApuCtZ4QOqSJcmDfDHvqKZk6Et8jdFfIQoHJuzz/xKM88rnPsvTc06RWMS7HBgehAF8gWqD4uIVA0KEWXnnh/Z0JBcFqnSTMYrSJ0RpWLabvwGgTurnHpjMUPmG5lZPVZvDB0G7niIf9zQTXejq5ZB/ub//11x7fE84sqw+tMjCQUjHbVPtt0O+VPh+2bzg2qmNoJLs/rsZHYmW8rTNI4woBm1z98YcP7v/nP/ffls505qTDLBaRPXVvfvT7bn1ZMwtPjkApwFa+tqklN/hytpK+7rX/nU7nGf6HDQSAi8kMMDI4z5nMN1CAzRLcnhAg1Kx/5N3f//Ibm3UnrU5BbfYKHn/Wyk//b/++9aHH9lqxZlajWjk6RqepY2NMf3yyHpnMZUo5YMA4GJ34xzXIAdRc8upe7h6/6ZhZ/hvvvOumKy9VT/d02rQpRmsIGWktQ2qKJh5jo4e+pBaxpmIrl4gLaPQBiNv4Rs0/iMEjBDEYk5Rr+j0SlEQoPf0D6gt83sMEh5VAKHJ6KyusnD1DZ3WRkHcjVG88PvTIXYegPTT06LRWKHotxOfRUdA7xOUkIaceAtpqceLRR3nsi1/AdVrYUJAlYEIOriA1QpZaDAEDgzY65wheMUEwWq5m0BTRFKspVhMEWz4zBAlImtANCrUMW5+h60BsHWubhJCiecGs9GjqqeSma5rhJ3/oWy47aBaXVbUjjO4bMBDiKoLbiAAHk8LBNIY5Pr5Gyo1f0eqwZGy0qgj+Y4/umf+H/9d/bn3mq8uk9UNot2tf/803Fq9/8cq1EtypYdVtMn9Ya+vH7aevR+1fhwc7YUjZ0XQO8D9MFQDOU89+HUp/O44C7NgAHSO4c32jAEI4820v9TfeestRf3rhaUnSA5xZ3uv+6c/8eu/9jzYsQnNgIi8rjWpZQ9heqv+PzcXTNLZx7b8K7zM4H0aXq5oQ4pJ9QSRGwlltdT/3qpvm9/61t7/i2MG5jrf5SVsXR6oJVmpADTVKSDxqQ+ks2N9Fz2CsxZgk7vBnDdFFX4i7+SUx3n4F8i+CR0SwNuZ77/GuC8GTCmQZEHJ6rWU6q4v0Osuo6yHqAAdaoKEghJwQHCF4QvDRnq8hBhByBeILbCiw3kWNv+iRFAX58iJf+uwnOX3iWbTISVILoaDbatHrdKJJAwgxihBZltFoNEjTFFUo8hwG7wCsFawoxmpcCWBBraLWo1bwEtf6BWMJYkHSuPGQ62KLZbJwwt5wdUP/7k9857Ej9vQyol7idgn0l3qO9rnS39RoIAxQ2ThpTJuvjpXB2BgbZ/3xSJ925YKOFgIQVawI4bPPHZ75tf/64c7nHzxNUSRYXZW/9u5vDfv3ZjkRyNh+0omDbZLaifngG9o/cB6fd/TGuxCEYrfdUDZJ96JGAb5mTQGiGjoH0uUHX/OaG/TokYaZac7y/Okm3/Xun//LT5+Yz0SkppXVWP3Jc+Je0s8d9TMYTNhjWt4YyjvG+EcD/VAyi6o9eZhPEDCdbv6V+26YPfz2N95y+PBcT01x1qYhJwYnSDGSYZIamqSQgFgBaxBrSKzFWIOY8s9aJLFYY7EmxZoEY6KpQEwCg61/hcinI9RvREkMJFJg6aF5h6K7RLe9RKe1RJG3wEd7vfoC9S4iBb4glOcheHxw+IFz4kAnRwIkGrDBIa6H9Y6ageeefIyHv/BZTj/5BCkwMzdLvZ4RnIsMWspQxih50aNddAkEmrNN0hSMcRjr428SMOIR8YhxYAvUONQUhMQTTEBNQEXi7odqyUzGfNMyk66Srz5oDuw5zU//2OuOzveeWYrmhhgaYZTR65CBV/6qA6PSx5NpPRRgTHCYGKZj+RoDsOmffjZr/Ntf+p/t504p7VYvufa6y/3bv3n28sz6J1VVpKK/r9Um2ElofnraTeh/12jsmvb/AvGvbZbabNpQALiYzAA7U/8FTFsC+TZPc1cIrmXKX6fm2KSloNRSab37e6+597bbbwzPPXtSHn18kZ/46Z//fCvseSVjfHpi8pQp+RUNfeLeU2bNkcm/zJjUFPv5/WVq5bmIGit4H86++ua55o++81XH5mut0F18yhjXATxa1lEDagJWDJYEown9df4xsp+taP6R0Ys1qBGwFrEl4zcSyxiDTRMCinMOgpImQmIFCQ7XW6W9fJruyll8dxV8l0R9GbZXoxAQCggF4lwUBkJAy0h+QQWvBpVyoV8AoxqDCAlYYgjgulEounSXl1k4dYoTzzzN8umTaJFT3zNL3usgQJoOgxGlaYKxlm6vW67/zxHrQUIMCyBxPZ8hqshIIFgPJo+Bg/BARCr6JpPgHK3lE8zUOiTuGdk/39Z/8tNvP3Rls7WoqsEYQjUMcBwjFUGvovEPUAAGAMGIQLg+CjDp6LrRJxh9RyLM//Fnjjb+xc++p/3MacdTJxble9/6eg7vTR5V1a7G+fncpuCq9r+pOWHzjHUn08Wn/V+A/Okc4X9YUwDY/R6Zeoedvu0O0vvaRAE2e4+1hICKbrA+ZKmAqGrrsgP2gVe/5jV0O4lZXG3yt//Rn37wROfAi4k7ym4IfY7fawjBbqD9y+hEPhQEZJQhDMqX2MCAiYiKIO12/vh9L6nl7/reWy+r6QlN3YJpWEUUfAAnOS7poKaD4DBqsK6B0axk/OVN+l5rJjI/MfFYTBnSV8xAIFAxqAjGpqRpjSzLooYdHKG3StFdwXWWcJ0lQt7Cao/UBARHCD1C6KHqMMGTeI94h3gP3qMBVA1BhaCCCwanggtKt/AUwROEOFMEh+u2STVQTwTjeiydOcWzTzzGieefJV88S3NmhlqjhqrS7XbJ8y5ZmtBo1DBG8CYnJDnB5ki5BbGoxYQUE1KsGoQQzRUD00UPQo5RRwiO5dU2hQ/snZ/HBEczcUj3eWnWFvTH3vXaA1fP9JZQjEH9KBMfRoEcGR/VMTcOFY0djw7PClRfXtNqmbXqDm1aIigfP3ks+d/+5e+1TizXk57a/N3f/+JXzc8mzw8KrJE2xfy3mb62tP/dve126L0wMtDkXXYpDvX5k5J2ZsC+gCLprqMA2zQFbHIyWRcJKGe+eq2m3/mGV16yd+8xVlZn/Q//zfd9aDnMvwLK3fxkSGhCg68wdR27HunraNG1BImRSXxtx78xE4AagZWzqx/6pltn973zu+87MmPa2jr9VakZRyoOJUdNwIujoIeX0js+CGlIMMEM+1r6y/iiLwAmiWvYTAzli5gSQZDSRBA95D2CSWvYeh0Roddt0VpdpLu6iBYdEgmkJmBxiC8IPqdweUQMVDGqWK+kPv6aABIirK6S4LFxq1+1OBWCEFGLcrliYmGmXsNowHU7uLyHCR4NjsUzZ3j0qw+zungW9Y40y6LtPwR6vR7qlfpMhqTgpEBFCUYQTRGfgc8gZIimGJ9gg2BK1EHUIzgsHu9ybGppzM6yvNTGqqXodsi0Q3vhEZmfWeYHv++eA1ft7SwgWBFcid6UAt64D0C/zyfHwsj5BAowaacf/wZk7HdaIQURkexLZy6x/8v//mufOLVcJG/8ru8wVx6rP6MaVvrmgvFhvDuGVp1ytnvY9wuv/W+fL2yPwoXHlzYlALygZoALGA66qFCADatu3xa2kdrfr6GKGPEn7n0Zvde97o7LP/yxx5//Kz/1ix/qSXabUN3Kd/IeUydRqkpUeb2clDfS/kfOx8tWkIAK01BjTejlxXOvvfvoi37wu1+9z62eUV1ZkqsvuZS8tUSWQa1u8dIjmAKVGCHPBLDekHpDUm7WI6aylr9v55fhb184GC4TjI6C1loU8CF6+Oe9DkW3i8tz0AKDw2r8M3j6Dn/Ge1Q9IQTEeUzhwHtwijriUv4Y1yeG9BFDsBasRa2gqUETE4UbjehBM02YqackRnF5j7zTpuj28HnBgw8+yONPPE7e7VDfM8fsvn3YJKGdt2i1WzhxaKJ4Y1BSNNSQ0MCEGYxvxmMyrNawwWKCkiBxkLiCudkGSdPw2FNPUmvso9s1HNhzmJmszv45w0x6loP7Orz7Ha/af/WBYkGExJgSxBhHdSpjS/pH1TEwbZiPCZv9sTdyeROfxXgRQesnludf8vf+0X/41FNPLoW/9oP333V0v/uMalhmCC5sTPH8Gqh3o/LWaOyGQrWT6QXkl5u51ToCwO5LK7s7Vte3XW+WyguSdn3QvjCrAqrZ43Pi7GztxJvf/J17nj1xOv9H/+I/f2XZ1+8RYYbpdUbTNChVSiFAxouOaWbjdWU42Q/m+bWEhrisTDQE++13H+O7X3vjwZm0o/M1J/UUWivLNJtNWp0uRVFgrRkRNAbtK23cVspNgAae/QY1pd1fouYvpQ9AXwCwRjDRBYAkAZe3WF5aYLW1BHhm6in1LENVy93/CnzhEYVEbNxVEAjOlc6DMXAPI5v1lOfe47wf2eFPVQkaKJyjKHKcK4UJiaGJsyylkaUYDRTdLnVrWHj2WR760uc5/eQTSJGTJZZamlCvZXFzIky5SVH5vGJRSp8HTTGaYsp/Fhv9BBRSm9Bpr5L3ulx2+WWEAM2ZPZw8s0KeO/bONNDOIvPJKpfNO77vdbftv+5AvuC9doyIG/arjo4HGR8b66EAleiRlX5mbCzKWN74jtSjYzOqFiJSe/Jk86af/gc/+4krr7nJ3/CiK5uq2qkWGjkYp6F9AXg7X7tO/L8b6aLR/itv/fzKVrvDj2V276F1KMuUow2LbpS5hWqb0jDXoSvVk3OmMjV30zS3cHNZ947nlmT0JJ5uYTBNnbj63LeK14/echwd3bNn5tlrrz329FceeOJwq9u7DCQdVpWRZ69q5gONrHpeLVNenwwBO7kPfFXLH4TxhaFpnoH5HRNDxEm73fvSd33L8X1vvPfyS/ZmLZLQw+AwmmODJxUhGF9q9ibC2jau6xeJQW5sn7En0ePfWItYixI17VBq+UmS4co53BoTw/6WyIBooNVaIYSiVNd9bC+hdPDzqI/OgSEEJMQpK4RA8AHvQ4S/1ROc9tkY3geK3BG8HyAQ2l8R0O+8cuc/UY9o3F0wCHGjn5J7BpSAwQVPECGoR03C/v0HOHbpMbJmk16vS+FyAooEO2DywSneOxChyHtoKKhlhhAKfFEgodzHwAdcCKgoKkJXc1AhhECKIVULeUA9ODI6zLIU5vmF3/7wwhefT/cDzhX0XNDcGplXVaMKYSgDoejgeJBP/7oOzulfL6/BkEb/s6nmV4X8atDEkffc/6oCrcOHZh/zXhpnlzvHg/dJpcx0e4D2P4A1tIktTCE6ItZMu+FW0mTlrTP/ndD+z7cAsLbydM4CwAYVdZ2zftq0ADB5tm7RzdTYRNVtMD6G7G4LTVmrIZM5W6K3ycJrCgBbvuEaVbcoAKxVTCgBSZ3K7cezREStNblzIRNUqluuVspUjivNrgoAY0LCeL6panFVRl+WGV4f9QY31bKiGJEgBrO6uPoX3/NtV930rfdds/9QtkDql0j6Afq09F6nhO9RMJFxYwwqGr35S+ZvMXH9vrGojdv9eiNgLJikdP6LMQGMNWRl5LwQFF8U+FAQfBfUoz6uDusH3SGEyPR9/CWU3Klc4+9DwAWPCHj1hKBICSiHwuOLguD9oB9CUHQsHl1/D4P+++8zriEDE1QhSRMK7+gWOblz5D5QbzY4fMkxLr3kEkxjlqLdJe90yiWRCbl3uKIo+1BAPd71cEVRLiuMgo5zBfi4HXJXe/SMIwAqBhssWWHIglLznk6vh202OZXXdSW7TH72Nz/03MNPcUwgL5wahCTuXjhk+EPGP5ZXEQr6jD2MMf0JQQAGSvlIXqUcJd3hWT8TmVxbUKk/7YIy/DjW+243kUYEgB1m/oPcTdPdmrKyzh23nkb6Zgc08HNm/muUXKfyGmLXRNrAB2B3YIeLp/46VHeD9LoS7M7cYMfo99dLrXmnyrGqOOdrEjeGn0JrWGOcZP/SqLAweixjeVV4turMN1Kn8jcAiuK0qyKYdjt/8FvvOHrbd9537f55OaNSrCDqY7jbPvMv7fhSbgcYN+gpNX+TlszcYm2CSRIksbFcv40Dx7Qycl3oa9mRaXvvcHmHotcl73apwvZ9rVzLpXHVHfjKzXfjngIm0jdlaGEpH7j/b/Rdy8A8EN/7+P0Urx5f7gIYSsHDe0/wnhAc7Xa09aOwZ3aOZqPGysoSTz32VR75yld4/vFHMaFgZv8+xAiry4sYDeyZm4XgCd5hk7gzorVRaDLlngeUTpHGGFKbRBNHeY6AWiWXgsXOWWb3pnhdwveeEc2f1L/6/Xcdu+aS9tNByZLEGMqtd4fjpDJ2RMbGhowVmhxv08bu2MsdQ7h0rUqCTNr7N/xu+2jcNj9wrfy/G+ninDvPNx/avfobIADwQqAAa5b4BgpwbjQ2UXVTlNYdGTqmqk+nOzFPTtX+q8dSKbuOxj+m/Veh/uoGPqYkMqrljyEGA6RAvbXGrKx0PvuGe6665Pu//eYjnTOf08v2G/EUCDqIwmdEsCU8b60dNEaMwZRr+xmL+Q9ljH9TbuQjJkb7s0kJ9RtC9MYrd9zTcqlewAeHtcQteAcIQMn0S9hfQ0BUURevS19AKK8HiY586hQjJi5dLKL2HbyPAoAqvgwIpKqlZqsDho+USICaCeEAIlIQVAkmMm+swTlH7gt63ZzmTJN9+w6w/+Ah9uyZQ0VwhYvhjUVwweG9wwilzwS4ood3BcYIiRrwgQJHjsOZgKfckakAGxypeoreIklqyDE8v+Sxc5eHxU7d/O77vvDEx76SHQYaIQYYsBNmgBEUIJpL+vB/FfYfh/5HUIBQQQEYPZ7Q/CdRgPj+x/OYVq5yoJWP5hxQgCHzl7VvvOn0De1/hNgaJDZHeXe0f9jUKoDdl17WL3G+pad1qL7gkuw2brjVqutNIDJWaNODcTONGJYZ1ZimH0vlt1+pP4UNhYhRmgPEYEQoCIUYsd1e8dSbX3PlpW994y1HiqVH9PjeVKS7jAk9DIpowPS3wMUP58pSkhATHfviVrhCfxmdlsF/pO/ZX/oHGGNJbHT2s0ZBA64ocHkP3+uhPgoeiWGo7ZcW98AUBgwx/DCBIEqIG/FNvmXVCVv/tJ4Yhfp1gID00QcJfvCnJTJQr9WoWUuv3aa1uIh6Tz2JzoIhzzn5zNM8+tADnHz6aSidBBOBxBoSW3oGlI4ZQSA6ZVjUJDEuAgycKg3xDyXum2AsZAl5gJ4vmJ1JueJQg3D2YXPNXDu88ztuOP7Km8IJ78LzJkYi9iPjAqASCnpompLheKmOobHBOlDuZUp+ZSxuLIBPWOKnd9DowXb1pSm0d67yN7T/c6G8u/xvZ+MA7HQv7AA9XfPkBW7IjkizO9GEOJHv3KvYnBBQRUxG5qgpzL26nG8aMZl2bXxiLm8YTQBVSHcS9jWiASRtrXQ++bbXX813vOrKw72zXwx7syUhX4YiL4ELH80EpRd/RBaiI1x/WV9fECg3DkAxqJpSo1OCGFSiZqxm+PmFECjyguAKhECMHCxxawAtt94No3B/mKKBj771+NP39B+UZex8y39DAaCajFKuFnAYERr1OvWsBs4RegWZGCiiMGBC4InHH+XLn/88K2fPUJuZQYBULDPNmYgGuALv4iZKaa0GEgMUYS2IDH0wEGy5vNIFOLO0ysy+Q0hSY2WlQ2txgeuPHUQXnzSHs7Z/91vuvvKum3231ep9Ni5IECeD7SB1ZGwMB93keJs29qqXZaLodEY9ItRWDpTJ206SGqOp5zZ5aOV/1hEKzzVtnfm/wHPuGtV25E1cgHyxn7YsAOyWpPUNFGBLd90ROucuBIxPiKVWswbBc0MkS5oVoWEgROik9j8uYAzOq86Fg3zta3Mqglcw7TOLH/iBbz1++f03Nq+odx8LB+rLppn1COrIZueiV74xpe2f0gxQhuotbezRjFD6AhiDYAFTRvIrUYD+jcvldRocrshxeU6R98A7REPUiCXC/ASPOjfQ/GMXxOV8VeYfg9yEwburMnwnioZYR0I8HmfsoY8whL5/QUQJ/PjSwOAm/A/6aX5mBlFPt9VGC0eSJCTGYgCDYbZeI0UJeQ/RQK/V4vGHv8oTDz1A3mrFCSl4UptAaRJRa7FphhiLF0GtjUKUGBIxpAIWiyAktsae+aOstg0zc5fRaSU0awcwPmVPNkPD5bYhC/5tb7ztym9/+eyR3mrvc4ImUjqojDuRMjAPRXGyamqaQJ/642yc85ewQHWMbpRGGPKUi7Le9S2mde91jtResPQ1rf3v5B2nJ5mdPzTN4XRa0SlH6xbb7IX1S8i6VzeVZLz+OZOarLiVj3rLN58yuZwTnZE0zrzHXcHYBLeeXmBAaUw4kAHXHtOUKs834fk/yJfB9cGEK8M6IxPyyLlUtHUG3v7DZX8STNxwLm0trX7wb7zl9lfcc/NsMpecCtp73sw1DRoCRciwaR0BjO3b8wUlwRhKD/7o7Y8IYmNMfyTqpv1Y/n1tzibRMVBFBhvwaBmJJ3rABwSDJWAobc/BE1TxJoptwcd6pq+wlefWlNp+ufSvr+V77/E+mi6C96jX2F4F76o+AKUCGbS0iYcS9Ve8jwzfiEI0PoCC0aEOoaoYEYIGQuijIjGQXVyKWJAkFlfkBIUsq+GBldU2GGHfgf3RN2DfPpqzsxTB01pdJmggrWUEH3AuxjigjA4YdzoM5EEI3uCD4EN0vMy7XRqJJcNTrKxQt4ImhlM4OrX9Pi8O2//+J08+/bt/+vCj6Z65ewHxPogOQyWM+gQw9AWo2vwH/gBjefSPGdr8+0qDMrxeze//DM+lFNVl7NNbT4AfLzvlcklygvlrtdBW0/Qb7qr2v6YAsH3tf1tKUpXYFCKbo7u2ALi5GhvfZTIK2ybShtDUOfKmqVW3QW+UxA4QmkJDKTXfTZPeWjt2otWj1MaJ6oBFb676uAAR01AjGcUu15Pf1rrnqFCgAw1sXIar/o1r/xOaWqWiqvjEGFVC2mmvfOBv/pV7brnvhvnEdp8JDe2YJKuB83hrCClgA0m5ejEKFuXSPmMAW4bv7a9xGHKDuCIwwgy2tPsHKTXqyGlLhhEwKMZYlICE0sJf3q+vZUvJnKXkPqL93itZRiAy/f4atTJfBIyJS/9Eyi3mKmYB+g6HgPjyXqG8bykVSNkhcenhgHMMzCF9FMD5Sn+pgo/PaEpmFpyH0n4fhRSopSlF8CwuLLG0tMr83rMcvfQS5vftY25mD528Q1FEfwtr09h+L+Dju7Am+g8U6ulpVOOttWAygknpFjmSzlBQAJ4Dsw0Wei2b6Ir/gTe99LIUs+c3/vjhj9XmZ+4xIoVHE9GBa0E5bKR8R8KIx32VdwqV99IvXvZVeS6jVUbGs45fG8rNY0xUR+pMpH4710vrMv/+yfZnnq0z0PPI/Mdo7ASVc2f+m6e3Hbpm8zW3+zrOd/3dIXX+aG71hrru6YZpSwJOlf5wmpJqiSozH9P+J287rD8Br1ZOR5Zt9cWaMq8vDCg4EWzQkIhf+eD/8q57bnv1nQf3J+H5UDOrxgaPlRSxTVTSGLwnU4yViACU2n3f9o+RGDK3rwiLlBH8yoA/YjB95g2ldhzj54fg0OCiU6H0nfuiQDA0p1Rg/hAheULJV0qO0TcN9Hf3G2qpkSZCDJ5ToddfNUAIZSjg0kRAf3ngcID0/RuGwtTw38A5sHz/xpgBiqCD9kazhIiJQkgfmQhxO2IrhkQseMXlBSefP8GDX/4STz7xGM4VNGpNkiQp+zNBTIZNaqhYXB5Qr2RWSK1iNKdeE1Rz0npKgRISQ6hnuCwlJAlFXlALjiN7saH3tPvW+67Y89e+65artFd8QSEzIiLRy3NU0ByRJDcYh+W7nlZlGvI1Tmt4OKKqs6UPd61vdhqJ6ZLE5u+105xpG7e7MGieb363ifpKaQKATU7wMuVow6IbZW6h6vYkUqlS3xapycpbb94WCst6pTdLZ8qAkNETWaPYaPnJAtNbUNHa11j6Nz4J9stXJ9ERmyyVDVuqGn45y04s9Rsra8CLEeucf+5Ys/XMD7z5vjvuuXVeTH4qpG7RWF9ELV8ErMHjCUmBTQxZyDDE5XqUGn9clx7X9geNmq9JUhKbDNeuI2hQvEQNMQbTKW3spc5nJYb7jUp0DORjZIhZRQW9vw6/z1ArL9SXew9QBvHpe+iX5oUwWCaoBO/wvo8gKMF5XBEjAfad6kJQgh/GEoASAg8BG0/KFQj9fiodHFFMKIPr+BgueBCYTrUUgMrVApgyLz6nd4GA0M1zgipJlqJGmJ2b49LLL+PgkUOAstrJKTwk1pIKiOsR8hWCdjEZJElK2ymOhCJkpZAUV2wYirgjorMxK4WQ1AnJPrfUTpP/8ZfPPfpb7/3KUz1bf4UxWOdVATtqDlBUx0wB5TOOmgVKcauSx9jvAKcZO+6nIc+Xwfublqbny1oXBiTX1v7H6GyY1tHDt8THzrP2XxW6z53KkNi2tP+tV9Z1ztaqcE4mgD75rfPQ7cJK26s/UntnEK5R2luiubUGbP/JNyIaJ7Y1ha/+LLbl55vO/PslzBTmv1YTBs8xxW16VDStGDUGgoAWGEm9D8sHk8XTP/WOb7rzJdceCK0zj2qWdkySghoIJkHFICZC56koFoOx6UAAUCm3BywDABmxkY31of6Bn0CpBVNq7xKPq1pzGcNm4OEPEdYO5cuI46oE+QdIwtAkIGW3EDS2CSmZf8WhL4ToJ1Cd2JQJp83+uv+4Z8DwfGiCUCS6Jcb5TcPgffvBs/bFAY1MdtDGUmgoxxnat4vHGqIGQiCzCYrgvVL0ck61T7G62uLswlmOXnaUuT37yAul02mTe6hnGY3GHlyweNoEHMYGrCgEQxlEGCUQxEEQms19uHbB8upz7Dti6PqTSSOtu7d82zVXh6K45Fff+5UPaWPuVcaI8UG9gK28tmkDc4R59Mdyn5lPFOmPyz6PGH4mQ9MBlb49F3a0jhmgL5BNb9i00uc20+9ajTWZ/zbSZmShcyH2AtU/1zsOBYBN9fMWBsMOM9ido7cThHb64bZzu222ZVwIEJlgDMDIJLWZFCfBvrozvYmyxiWp/FaRlaFNv2JnLq9P2Psr56C5iGTe68Kh7MxjP/KWW++4+eqsK+0n6ntsEbVVmxKMIVhFJceY6FluqWNCXLs/cPYro/9pGa5Xyk17YpQ/AUpmHkqNN0QP+Gh/L59a+syeklmEIaPsz806tNBpGAb3KTnaMBRwSUeCH/oIlP4AkUBZpvIO+y9uiJZIydTHOjkM+1Aq5PrtCSXtUGrGUnIwr2D68kEYCj4yIFsyfogCQUm3ntXJXUFrdYW0Xqder7OyvMLDZxdYWjrLFcev5OChQ8zPztHrOXqtFj5VGjNzoMJqZ4UkTeKqDBRDgkcJJQwkYuj5gA/KgSOHWW6dwqvj4P5jyYnTTxZv+Y4X162t3/kr/+0LHwzZzD0GakFiwKAozIxJnNXXVXL0qi/AyCDXIYMf/5ymfl4j0pqsGeZ/zTSljTo9YxtpHQI7zqU3Sjtxwx1q9E4/+5bobU77ByomANgkH5F1ztYtupWbbFB1e8x3t6IDDnK3RHMLhdfRkNens4kBMTIDTUMCpmv/62rs0r/30BwwztD7p1NNAjK8Njiv5E1s+CNlLP/yRpFXay5I5r0/fcV8+/Ef/8E77rz9uvlucfrRekMCmWlSFEqRJIRECWkPlRxrDBkZaWhCSBDjISnj/pdR/pB+aFqJnv2G0k6vcZYfKMiK2CT6C2jkpKqeKChUQsb133TJEEWj4hlUo60cAXWl416/V/ou65SrBYbe/32q3rtyaV8UNEKh5XsvVwgULjrkASYoflBWh/dHMSFyt9Dfc0AYmDV8+cjGmri6wfcRA8r2xGcXKXEA7TNCExEPBVTIiwJFSbMs7iWQ9+JGidbS63UQI1x51SVcdfWN1PZdQq/dpbV6liAFtYZi0kDe6xL9E1LAxvZLgUoXVcH1muBgbkZITJfV1QUUwWb7KTjoexy1v/JfPln8zp8+9GGfzt2UJMn+EFQ1YBUGwk51c6Dx1QEM8qfnQd8Zc9jzI/B/6Pfv8BtaT/Odmq9Q/UInmP+m01pf+XRauvalLdFZq+japc+R647IQzukvU8hs3nKW6s8eWnzAkAykbnrKMA2NNZtKrs7S2gdGjvWzkm6uqYQsNZNNzns1ip2rs9REdZGVmCMKVAT1caY/6T2XxUWSs1oRPuXivavOUiWF+6pGw/pib/xA99054uvn+8US19tGN/BE0iaMxQ+QubWKCIOFY0r+I3BaDQJGGNQy3DXP6p7AESzQH8G7wfeEWIoYimZpEJcq9/nCGW+lCphny33afSdAkMJ4xukXAFQ8fQXwEcNPMYV8APG3We+A+dBKZ0QVUENthT6+haJPqJQdSIctkXjJkID5z4d7Oyg9AGHQPAyiDFAH+FgoBwTKJ0E1UMYrq+PDojxOAoFsY6h3zahWa/hXJenH3uMlYVVLr36Ro5e+SL2N+dZWD5Dx61ST8DYpERE6nHbQhQfChDBCfiawWQJ7aLLXFYjlSZ5r8vMjOX04gnrU+ff9LobE6/F/b/3509/AGvvM4L30f4hkyhA5ePpv9MxFEAqeSIMUJ7qZzc4738u2o8GuI3JZBpSseU0bW7ZKea/xWac28WduMELRGZbksPmCleKnLMPQJXWLvG6dehu766x9lCqPndSk5W3Tm633uB2UmVpYP+L3qr2X9IZPZ583xshJrLWSYXpx/Nylh1MzloIkhW5e+L6I3L2f33nq++89opmZ/H5hxr1pEWzkaHOkfu8hIeJ2/YSYXqLISHBiMUDJjExfoCxlJ6CAwc5NcIgdv+A+QrGROHHiBnYyKk2U/tveeglX7WLK1SYKYTgMd6XTn7xkYdlAs65Mq/ktp4B543mekHVQIyeX4l6E00qGhTn40ZEg14LUWiA0qYf+trvkPkPJAgBKX0U46PKQKITE4lFQSEKLEJpJih70vtAlmY472m3OtjE0Kg3KUJBXuTUailzszXyTsHi2ZMsf8mxtFxw6bU3Mr//CK1uRmtlkdn6DEYNRtP4dkMoIytAoMDYcnWEJiytdmnaBnOzTdorKzTrGRqWbCMz7g3fdgtdz3Xv+9Dzn8JmL7NGnPdqAdERPL7fqVTyKmO7UrTf2wNmL4z6Agz8O7VSpzxexwwwLkwMM4djaUzVvUCmna1xy3XEjm3efocc/7Z8ZbfuuLk0agKALQwKmXK0brHNXthk1e2NXhm/wzmTm15x603cQuE1UYBxOtscHtX13FsRAEZe61BT7b9zgUEMSqmU778zU82TocZfPe7zFinXpvfPTUQEvAi2180fuOmKvPfjb3vlbTddPt9ZXXiskdqc1BY0ahaXF4RQBuxJUsQomIAYxZiURDIsNYIKEgPTIWLBKEp0FAQzeJahjTs+oymD4AhxTZkLRNQgBNQoRj1QMngJDDbgCaF0yDSl9l964IeAiMcCakC9I2j06g9BsSahKDxF7kkSS5rUCOpxLscVATEZIYAruqgWYDzB98oQxIqoJTM1rMnIC0+R5xgLiQXnC1xQ1CY479AiR0LACCTGImrx3gAZRSkgAARTIhniQaMXPlrGQcBGtVgNEgR0GFMghNInIhGwRDu+egxdMhMQm7HaMfS8Ze+ho1x13TUcPLyfkEDotCmcR4KQphmKUhRdCi0QEyMqBo1bJYsv9zFwORocST3FpHVOLrTphj1Fbvemv/O+hx5571+cXg22dpsgzgdNJgMCrREgiOEvWoH9K4y8ej0KCxV2PZArKqL0Gp/11GwlCnpVKeSc0sbzyta1/y0UXrf55/hgOnqyIwLAVvpms6XWqazrnG2m0qQAAJvkRZsUANYssDmGt37VHRQCtkVqsvLWm7jFBmxKCNgZuVOm3Ghd7X8AHfTh/zGhoK8Vliq8DPI3KwRMxvjvXzNGczCZK9yTt17Wa//I2190w1VH57rF4vP1Zj06h6FRixYxg939MCZC/5IgRuKGPcZgS3h/EOmvbJSKICZqyFoKHgNtV8Y7J2r2vgzEg8aNesxA8y/f12BtPlHLR+Js7wUNHhM8xvRd62M8gRAcISioodvJybI6ia2T5z263RwjQpIaNBhU6+QO8qKNsZ60JsFY5/PcqStUequF9YWaRm0G13MEl5MmCupwrkchShvBZpYaivUF4hxpMIhmZSS+Gg6LkuIJOPHR/k6BkQITCkR9+WxxnwQNMT4ACBIEwUYkQ5RglCA+RkEUsKIkIVB4B5IQjKVwjnqjyeVXXsGx45dTqzfodjsREbEx3KPzBV4diRjIe5hQoMYTxON8uZNi0Ng2VyDG4kxKOzTyE0tZ9ofvP/XYH398YbXr5BYRcu9JlYFFZmAymcb8KX/7Owky9qtQbk4cMwd1Bv/1BYBSbV+HGY7nb50pr5em4gzbuM/WGNZFof1PIbR52luvvGUBYKzINgSA0YLfQAHWyN1FFGDL5DeT+tCgjJ6P32djAWCa9l+9Xtmgh1EUYEQQqDB3KsexTEUAiMwhB8kK55685bJw+ife8dKXXnFU2qunHmvum6kjeERsKUCUvV9u1asSN5dhsIwvxvY3NobMtUkWX0XZ0FA+Wx/KlrI8/TIyfJUReqc0AcSV76ESsx9CZIBGY+S8UAoZpX9A8JEJWy1Q78l9P9SujYgEgChF4QCPBMU7Tyh8DNWrMTxupxAK29SZ5rw3WVNXW9305MnTtNsdms06e+ZnsFaL1tJpU3SWTMMiNQM+72GCYOuGTnCItRiviPfYAAYLauI2wpgYr59oFgkEJLjYd+qjeaBcVReIwEccZlrKNVIKf8M4BHF5ZnzXiYVUoNvtkiQpWS2uGmjnBVktY+++fRy/+ir2HjwAacLK8iK5K6g16xgRWq0WiYdEwUmPXsjBCKm1SFBct4sWXdIU6s0mC62cxXxvr2eO1P7rnzzxlf/x4edcpxdeLEaC8yp9F47+WOhbQ6qCwPiCjHGnwGH5IRMZoAOD/yoowCYFgAGO0HccGr+4Q5OHThxsqdamin5D+9/o0ta1f1hLAIBvoADnSGlq7i4KATsuAEzcoz88hisDtqr9TxMA+oxxcC6j16bB/hN5FQfAqPlLVhTu8Zsv58yPff/L7jh+tNHpLDzc2NMAI44kcuoSmo/R/MSUnvyllh+Mxdp+mT6ML4i1hL6QMHi60YljuHVspbH030cYLPWLnK4P/TP0DaBcgz8y60c4nFCQUBBCwPsMH1LQDFVTOvY5bOrpdVcIeY9GVic1GXnb02t7FWkEnZ0PbbL0zILjqWeW+OwDZ1efPrP6RR/yolGv2UOHZm+4+fr5/XMNx2w9p6Y9lzpn6HZNEgy1zIDxOO/xRSB4EFJsYlFRXCiQNKB4RBwmxO2TrVPEW9CEnBpesigcSEDVIwRQH6UB1bjerq8Jl50d+h0fohmhVquhQOFD9MUwCYV35EVBo9ngkuNXcOzyy2g0GhQEgi9w3tHrFdRNAyuWQru08xaFK7AiZDYB76mnSt5dptvrMLN3Hyu+xpnWbM+ZfbX3vO/Br/yPv3i6VVC7XUS894qCHUUBxlcIjCICE8xfhwLQgPFvEwUYMP/+V7djKMD0+11UzH+k6tev9g87KABsWOUCRQEihZ1AAaZXPrcmnichYJpWMBIUYBgOdo2mbFL7r/JHmdTu+/XWZPwj2r9aK6pBjS+KB2+91nb/yve85Lbjh5vtzunHmk3jycRhawYjGnfws1F7Nna477y1USvVMQRAypt4kUFZU8mXsrGhdJwbvC3pR8oTKN3Poitg/11LXPpWOnYFodzdr9xlTzz95XyhNFn4UMRofdoEnxCcjUv41BNCQV6skNUMYhI6rZxO14bEzgVoWO9qcnI18Kmvnuw+/NSZzz391Bl3ulXzBy7Z86p+k06cWP3L/fWOXHX8UO3YHnvn1UctR/amNFN1Nu9JyLvie11Tb2TUswwphZhAZOYuuNKrLyAhYDVgvSHxinWCSkJXMgpJiLsaejwOxIM6hLixj1AGBlIojUH0zShGAmm5lBCRuKqgrBX/lDwvCAYOHz3C1dddy+z+/fi8QzfPqWV1eh0HKqj1qHhyn5PnBSYIzTSFvEMiOSpFFCpsnSKZYzWv52dbJvv9//nomf/2l6cfJmncHYAQcEDSZ+Tr+QKMowMw1PyrfgHbQQHGRmJ5OPbVVmSDraXRSWJXmX9ZfHqNnWD+O8P+z7/2v8k7bUkAgG+gAOdIaWrubqEAZfHpNaZx9K2kIRMf3mp6tMC1tP9Rh8BKW6uCwYCRjmr840KAqV6P6EGgVMq1yL9054ts7cd+4GXX7m+6zvLzDzWOzDapi8G5AsolfNYMt/DF2hjgRwSbxAUxasq1/hIFgb7dP5Tb/SpSaW//CfoT+DB07vh7FAKmz7hCHyI35Svrr5HvCwAKEpmhquLUEVC6LmAkIdEU44BehPoNDlVPVktYarVY6ji1tXlPsj85cabHZ794gufO6pe++sRzy4+elvzopfP3D/rESCBobgzWB1IA58Pq4rNLn7zsgDaOH509fuPx+tFLj6Yc3NOErne+6Jrg2kbpkqVKlgmKp/AeKc0BJhiMV1InmABJUIJATwyFNShR4w9aoOIIFAh+MIRELaLllsrBlMv5hDSBNIF2axVjLUlWJ3eBTreLSTLqjQZ57jFpQs8VYIRjlxzj6muupjE/R9HtURSBbt7DiyPJYlyHPM8peo5MhZpA3llhfi5DrbLUapHM7KFVQCfM5D2/L/vN//rlZ/7gQ88/VZjarUYkCQEzRAKUoDIUBhhj/mE4F/eRgsEoGkcIKvn9kbQeCtDX/HU0g4l5oDo1bDhNTC+wdea/xQrf0P43WePcmD/ssACwYZVdQwE2R2Nj+l8LQsD4l30OtAapMjRGXs2oEDDaBTpyPlX7hxGG3/8drOEfY/yD62Ve6ekfJK7LzkPe/dw9N6RH//oP3n353manu3LiofqB2ZQZK1hn8IBawZhk4KxnRAimjPCXmIEgoKVwYExSIhN9YSHtz7lxCu5rbeVx3zQwIQCUkLCoxWDjlFPuAyBaIBoIeEQCRkyFIfQ1YMGr4oLipYwS6ADnkGIYcCeoUGji27mw6o1dbAtffDI/+YWvPP3EFx4420327Lm8WU+vVMAY8RpCoYpFJOk/dWpFjgAAbtpJREFUEqpOBI+Ymmr0HT9zuvWRvdIy1143K1dfcvj6Gy7ds3euZshS74NbENc7Y0S71BKN2/sWIMEgIcU4E30EfNTTFcUbwRvFl6iI4AkUBCJ6MAghLHFlgKjBqEWDYFSiH4E40sQSgtItfOnMmeAQnIuohE1Seq6g3emQ1DKOHruEK664jLm9czRn5+jlbVq9FZwWiC33dPAGCk8jzQi9HHVdGjM1OkWXVrfD/MGDLLcDLb+3kPRo+p9+6yP5b/3Zkx819dlXlUw+BMWMOwPSP6bC/CtlqtdHGP+4Er8BCqDj/0/M7OsIAWumKpHhvKL9j2FL6euH+a+TvblSL4D2DyCz8wd13VHw9YgCbJvcZOVza+YWCk/ILuM9vtUHmhwxMkJizCdgivY/TQCY1P5H8waOdGYoAIwjAgZRIzgE69qtD93/kvTOd33vHY3Dc6HoLT+RziQFdSukEteWK5bE1kpHvb5WX9H2E4sxQwQgCgClU58pn0bswCOxOlZ0MB2Pvt8+E4gngoQmorWS+RdAgdABcpC4Ta0h+iZoENAEtA5qCEEIQdBE6bkeWhTgHSiEYNR5Cb2QmaUVJ0+d7PCphxZPfP6rTz/z+NnZzsGDM/cOe4xuiE+YTjR4tOcDqDMCqpL1n+7MyZW/ePEVWfPIgZlLX3Ld3qPHDiXUZcVLsSTGt42EQKIWIcGEFAkWfLTvm2goIJHBTgHlSocYETFIiB7/OoT3UYFyDb/RGKeAMtphkiR45+kVHptkJGlG4QPdXoFNUlZaq9jEMjM7S7dbsLi8yvz8DFdddZxLLj3M7N4GpMpqZ5mO65BmdWq2jss9FMpsfY5et4PLezRmGhTe4fFoktLKLcnMpUW3aNpf/+1PLfzW+x9/KCe9A6SuEIKqmQb3DwUCnWD420UBqsyfsbrDtN05QEZzt8RBt8b8166xE8w/nny9a/+wCwLAhlW2gQKsWeprFgXYYgWplp7W62vQmqoJrDF9TBECRrX7SeYPFU2+cm06CkAf3o/BYwb5McyvCBgjBUpK0fnYK29KrvrRt999YNYsh+7S0+mlh/agRZter0OSJZhE8E5IiALAYNkfMhAAMAYpnf60DO0rMhQAQv/JhIFfQP96v+0hDN9XPx5AnJgVCUKghoSstFc4gvYQcoQCQo6IYo1Q5DHsvJEUoyk+CKq2jA8AnaLA+YAkWTBpQ1tdb5947ixPPJfzhUdOff7hR0/6Z1Zm2/MHZ+8FMEIvBEUj0zdsNak6gcJY0aA0ARYXWh85VF+pX318f/MlV+550dWXzTGbBk+xKkYLUdeT4OKGPHET3xAFH+9JsRiN4X/7/YAoLngKPEEkbpokcYtjQlzVYKXc78AkaDB0uh0ChjRJURV6uSudBi2KicGZAjjvcCEQygBKQT1Hj+7n8isv4dIrjiA1peO6KAERi3MefIoUlkZ9BgBXOJIspVe0wUJan2VxpcDWj3pJ99tf/+2P5P/3f3/oI97U7jJGrA9YVTVaMQPQFwAqzL+q/fd/10UBRvj7KAowws7WZaBT5oBNfv/9+tPRhfXSFlntRa79b4v5b0BgJ7V/2DEBYLTgC44CDC5sTwCIFHYPBRjk7hYKUGpOss7HuyVaa9UckZH6THAntP9K/vgKgbKMMRQgaWrC4996+2x46+tvuHpv1i4ydzJtWMDFWPJqDSHxhCQgwZL4hKT08O8vn+szdPo7+wGYZNQBsBQANHqgle2rbqJTCg7lhjcyMg8PHQHj9gAGaw3Bh7jWHI0kfQyfZ43Q6+VYG00V6j0qlqCeXs+TMkMw9VCQ0vKY5xZyPvXw6e5nHnjmkc98ebG19/Dhu/o9YUTzoBiQZK3AMZtFiVQ190FX00RmJe48UBcgd/4kZ06eeNGV83vvuumyy1907SFmaz3yfCHk3SXBt0W0INFAKnFzpZQaUgjiwWKwJRhRhEChnmAFtUohDqcF6gvQHKOuRA8szlsEQ1CJwYm8okHQ0rHSh+hiqCEaGAaAggIm4LsdsrrhkiuOcO2LjrPv6F66vkOn28LYGkKKpQYhQzUBlbic0YJYpci7WFvDacZKbn0we/iDP/5y9//+w0c+nfvkvtIPQJXh3k1Umf8Yw58QBCrHG6EA/VgRa2nl544CrKN/D+Icb4/WWkV3l/nvDPtfr6EXlPa/QbFSAIBvoADV2rsnBJxbMzdTuN/LUaNau8bYla1o/2MZw2fpIwHDeuMr4gY0NtT+x8sN1/obg0fFJtY99c23z7X/2vfe+aKGLua0ns321MAEjxWDQ6OdOVF84kg0oRYy+jv3RTTBRAHARO/+vjCgpfNfn8kPwv1WjgfL/VQHDxAjwsgaXaVgHF5djPIXALVYtYhG/4DgFecciU0QY8nzLnnRI8ksoNotRA37TauX8OTzK3zmK6ee+/SXH1/8whPmxL4je1+tgDUUIajXIAkyDPM9LgBsNG2MP4IqXlXbxkhTBIviMOpA6gCtVu9LjdUFObjfJPe94ubDx4+le48eTJGwqr2V00pvxWQ2ULOWTEC8YpxgvCBeiJssWNQm9IiCgDcRBUA8hAJ1OT7Pow+EaRKCgFI62xn6kZhRxYUymiD95XilX3zJfZtpSqfdxknOoWN7uepFxzly2QFsPW5GlKR1vDPkPSFJZ0jTOsE7er5HkgAuIEFozDU4tXiWpZ64uX2X2t973wPP/sfff/LsUitciWG2jHxspgUJ6vfDiKmA4fFmUIBBwGadvLZ+X0+ZA0ay12P+/YKbFQC2xvw3uPu5JR09+Yb2P7y4gwLAaMGLGwWo0LkgUIDNVBjr6bV40TnQWqt0H8HtlzLraP/98lXtfySvKgCMlJM+8w8oJsv02W9/RbP7ltdec/XhGZsnxVKW5D0yNAbTEcHbQGEcIVVIFKOWWkgHG/P0H1Erzn5SNkb7mj/DwD5aQg/jr6g6lfRfxUgsgEphNT28aUMAQ4aRFHWQdwN5HkiSOs36HItLSziiU5orQ+sITlbzlE98bonPP3Tqqc988WTnrOzr7Jmr36oKxpC7oCpIBipravzraleTaeqjjFyTAFoIUuvnnTmz+pfHD/QO3vriy/e++Pjc0eNHauypB1xnQVfOPoXqqtRSaCaGutYxDlwOwQleLT01cTGgAScghrgFn/cEXxA8eE3jzUJ0fuwvEYwM3hBCyXAHO/b58vkVMBhvsEmCdz3aeYvG3oSrXnQZx685yszeGbpFgWKwdg4XLN6VjpxJQCgwRUIzm6NbnCUPq8wd3Mezp1bD0mrdfOQTi/zrX/ny+52tv1SM7ClXcsr4Ur81fQA2iQIMBIZ+Cmv37YbifJWXl7EpptIYEJLKyXpzyRZZ7a5r/zuj+3+taP9jAgB8AwWo1t49FGCQu2NCwBq9vJEQcK7aP5X2j8lJVe2eynGVyQ+YffW4KhBUGHJFKNDMhue+/b6Z7l/53luvrutyvvzcY9n+ZoOZpIF30YYeDKj1BBPtyRiwYkkkGTybQLn2XkglQawMhfpB46sMXxFrhhoXw8m5X0qqkkX5bs3gJQXExJC4ufPRlj2I8y/YpIHzlqWVLllthp5PtNUN0i0CZxfbfO7Ljy5/4gsnTz/0dHImnd9/RZbaIyiIaE8VoyrpSO9VNcYpfTeSqgW28vmPjq2AaCEilmj2Z7WVf2kvC/aOGw7P3Hr94cuvOtZkttmhG55V5xcwvVxoK7aATC2pSTEmo+cDnRBwlIKA1xiFUKNvhnpi6F6hEjGxH2kxMqboj2FiRERC3KBIQrmzoFBLm/S6BapQb9Zw2mE1X+LA0SY33HIVl15zJYil3VGC1DDGxi2SQw9jAuITxCcY08HpIh6HJ6VbzIeF5Rn/wY88m/78b3zxg72s8XJBbFASBWEaCrCWIFDJGxcA1lodsBZf2hQK0G/Mhsx/k7QuOOYfT76h/Y9elJn5g5WVSxcCCrC5m21c9WtRCNi6ALD+LTb+cNfU/qsXZfS1myllNqv9V/OjN35U4gz+K6+/p374Xd97+/75bNmF1WeSGkLN1CAkKBZJINgyoIwIRpK4nW8ZR96IDhh1X8BISgfAPjOpavCqigG8RgFg6nuY9upFBuFr4r0E7y2FKx0ZrSMQN+nJfUEQQ5A6PU11tW2lU9R46rkun/7iM09/8pNfzZ9t7Xl2fv/MK6NsIYqGIqgkDHfLLRs88jM4mSbnrTc3yJQy42jA+Gcm/SpKMFa9qmQCBNVW77mTj91+Q+PgbbdftufFL2k2k/oZeguL6EpH6y5IUyxWPXmnF8MApRlODC4YXKFoIRg1JFjECD746Cioinop907QUcaoESFQGWxNWKI+oF4BS2qbZGkNFwq6bgVJOqQzwpErjnLltVdx4NLjoCnLZ5fIvaPeMBjj8c7gC0iNQ2lT5B3mZvfQLTJOLdfUZJeGP/yTB/P/72997uOFSe9HxGtQu64ZYOx3gvFXy1c6ZwTtWYeJbowC6GjHy/BwDQJr09q4wkTRi535r5O9uVLnQfuHCQEALnoUYHBhewLAkMKGXHRL1KbmbEsI2GAwTGv+CEeQsQvr322QNy4bDZh3JD6u4VeFhHXh/8p1YyKxUPQ+/sa799z0fW+6qXFkb6FJcdp2F57l8kuuIM8dSysdarNNgvVgCqwEjE9IQw1Lk2A8uS0Q099ASDDKIOKfMYY+SjxNAIgBfmRkjX8f0h3sd18RLETHJpsgSEjRkICAL5fEqRUclm5udDVXObnQ5fETHT734PPPfvTTz5719aM36YC+5kGJ4fSqcsf45Dl2PjHtbWUWnJB/x4T7tcZA/C8I6owYE1QT5/3p1tnWl664rHX5jTdkc7ddf83B6w4fkKzXortwUkNnURIpyIseeXBIalDSGBHQW8RHnwkIqC0dLsutkkMotf8KNxTtCwX9vP4+u4Guy5mdnSVhhpXlHsEr83tnSLIOZ5ZXWS0Kjlw+xw033cgll16Jyeq44AmhiyvaRNfKjLzdpp5YmrWM08+foN7cT23+KE88vRzS+eP8ycceO/Wv/tMn6fbCIUBCUBnX9qdq/1OEA3QY4W+wBn8a4zwnFGAMUtgS86/S2XSFiaLTa2yTZVceYPvMvyS4Lea/TsnzoP1DKQDAJvX2CwwFWLPU1zQKMF5hE4OhPzFPUwVH6J2D9l89NDCIAFhy9bUEgappYF34n/CVN9y958D3fMu1+w7Pe03copXuIof3zbO62sGhpM06XQokzSFxpBjSIiNzTbIwQ2ED3awXbbgSd5oTZWS9v/QRgH4+QybfFwAYz6sICINVAzqcJAbhgQMkJiG1GUWudHqe3CeQzNLOLSfO5Hz1mdX8E198ZOnjXzz5dJg/fLsCBnFBgypiKZ3J+t1UtmLqJD0BEU9Lm4EA1skeQQQGwp1MXIvL/tVZIzYoBgHvwpmabz188xX7r33pVYcOvPjyeZlLu6wuP0U3P4tKj9x3CQGsTUklxQYLzqAhj7sIa4ie/aEfPEkGTD/2Z/8N+UrLAl48thHoFqCFIZM5LHW8c5B0qTVBaoEzSz06LnDdi67hpttfztz+vYTOIt63KUyOMwU2pJheQuIt2ot7NmjN0iWhncyoyw7I7//PL/d+7j999qtB7Ys1eiOOLg9kHUGg/G/o7Dfsb1WZzjy3LABsh/lX60+fQzZMXwPa/24y/8nLO6f9K1MFgMmzzV5aq9C5CQEXIAqwbZI7LQRsYVj3J+k1hYA12jUtb9rrkJL5j2qB9EMHT/P67/9OQQHiLB6KB777m49f8l3fdPmeS+e6SH5GjO+yf65JZ3UVH5TazAw99RQUmMyD9SQYUpeSFTNkoUmRejppD5KANXa4oU+f2StR02SoyfcB/+hNHtGCPtPv/4UQBlvBGSmfVLV0TgMNMZwvktJ1ll5okNbmcM6y2oJnTnb5wgNPtf7iIw/lDz5vv9C8ZN+rNL6DIsR9g5LqBKljs6VOmTzXZP5bAAIm+l2m58uUcTDIG9QZCAWlzkowMQyAFSCcOvWx2y5rXn/bS44nVx3fOzc74whuhXZrgW5nGaOehsRQwpo7QnAxamNJtz+19z39QWJAJWUQPhhClJ4kEExBRxVNINEEGzISraFqQHKC5DgcSc3isJxd7lBrNHjJS27mmpuvI6kJuVuhJz2KrifxGbPZHMYJRa9LAJJGRlvg6YVOaOy9Qt/zvk92/uN7nipyJ/voLw9UJgUAJvNUK085IiiU379OLvpdywl0K0xQB/9tNm1xPirpXxzMvyS6G9r/lpj/Ju+2Se1/RACAb6AA69NfYxY8R2pTc7ff1A1vvRUh4JwEgH6eqZaXirPc8HcK44cy3o4vik//0OvmX/R9b7qlkfTOSFIsSpJAYon7tktcyocIJkkw1uApg8comCCkZFhTI6TgEo+agNV4L4OUcfcik7DYgRc5xG1qYkPjxr/9zYKkv/teEdCgg+ezIrjCIUHJUkvhC3q9DiJgsxlWzSFO9fZzeinnyadOhS8/+ETnY599+rlT3ZnWzEzt1tIE68vVhHYgo49ph4wdj2tq05i+jmdsKq0hxK/Z95X88TKVvkalj8B4iUqsFYFOu/PJObt04JV3XXfw5uuvqO9thjTxy7iVk0i+hOm1kNDDeiWVBrVaDedziuCiWwQeH8CQkNgkrk3QclMnBV8UqHrUBkLmyDUgwWC8xRDDDYsqHocxBh8CwRhIU7oupxccR44d4ZaX3szh45dQhB69oo3LOxgMqSbgDCk1jAY6vVXsTMZKCOrrB/SXf+dT3f/wu0vqvc70GftUrX9cABgbA1XmXw3Hu3UUYI1JII6/bfHfTaXdYv4j1XeQ+Y/QnXKrzdLYIoHd1P5hTQFg8myzl9YqdP5QgM3TWZ/MTqEAaxN4QYWAqWk4KaxVZqoz2EA20pEyVW1wsHtfpc44MtCP9gd6+p3fvM+++Vuu3ls3p0i1I6nx0YFO+u51gikj+RljibWHrnd9eN9Yi1oINpS1BIkhaKIQoQA+rvmPuwZU3kb1YVO89xACIkpibYzsHxT1Hh8Czik+GLwaEEuazSBiWFjp8shJ+NyT3d7nvvRI+PJXFr/k0wPXWmv2IhI0lH6O5Suc0AYrvTNtLtLKhDd6LoxdGkvjmVN6fWLYj/bx8H2PlYtMfkJuHl8VQlyMocS1GADYsPSBO2/Y//KXXH85l+63jaa0cK0F8tYiaXDUjKXoduj22lgrNJsZ1iaEEPu2s9pFMKQ2I0kSRBXncrwvUBNjRAT66/ME1GBKT9GgirUW5wNF8FGwlEDXO4wRsHDTrTfz4ttvo5A2ScPR6bRxXU89mcXkKb7boTljcW6J3HpOd4127GX6nvd+qffLv3tKfND6RvB/GNf8K4NgeLg1AWBYbjD9T17fbcZf3n73mX882f7jrM3818neXMnzrP3DmAAAu4cCbFhtN1CAwYUdRgG2TXIdAWDbtDd3+ymf/kiBc9X+YWySN9XJf8wcUD0WMCJBDOGdr51b+bZ7rty7v1aQuBVJkxxr3MQae2uTcuKP8G91G9+qg5+IR3GIpKA1ICMQ15IHUcTkqHRRkyPGRcalFqMJqAVNSWuzOOcp8g5GcxLjsSagzuGcx3nBprOonWOxDYsr0PV1TpxuhS8/9Fj44KefXP7qqfQLtbk9rxBIEQ1ayiTaf/vVuWYDzR8qNmSRkUJTtb9znQnXEBjHv4dR4W+KUDAmFI6jB+W5iomhGFRFdHnp/dcfzu+98/ariyuPHWwemEshP03rzJM0aqDOIRowwVPkHqNCPWtgsXjvcQGQgNeIFKhxWDGIt1AuDvAIapKIHSk4FGPiRkO+cFgj1BKLNRC8RwSWWz32HTvE3a++k9n9KbnrYZIMQooJKTWbELpLdN0SpgGLXWXF7aWrl+kf/tHD+S/+3oM2BE3WQgL6TLp6bbpg2H9rU+b9NVGAtYW+6hjcfFoDSViv+Lq32QbL3nHmXxLdDea/AYFzYv4bFtOJIusIAJNnm720VsFzQwE2f7P1QYQdFgIuGBRgix9g5UajtbR6aSrNzQgAsYwwmJ5kUsuvav99GtHErt2//l2XudfcfWSO9jN6zcED0lo8i5gck2q5QVB1O16LtXbgmJckyYgAIGIwBqx40DxG3NMGKnWUDG8MwXhUCjA9kFIAIIYPtqRISBHNWOn0qNXrzDbrGDxFd5VOZxXnAmBpzh3kzIpjYQU0PchzC8F9+BNfsn/y4a9+abGYzdJ6en0JMdipGv6aDD/27xAKrkzYY1x9nMmvNaFvNJWs9xmOX5tAhCoZfZ+Q/niqjp/NCAQSA/0Zt9r5+AG7cvzu2y89/NKbr9ArjiamaJ+mvbKI77RI1GOCRwpHKoZ6llEUBc45HIFgAioOr4EQIPEpEhJCANd3FzBEISCGGozPERRcRH2s2LhkVEFJWO700Kzglpdfw/UvuQGbprgg+NxSdNo06pDWhbMLTzF74BAr3RrLnVlcuJz3/OHHil/8w0dyRGamQf7DPGHg+DchAEhZeisowDSmWMF1zoljbl0A2H3t/xvQ/3oX1xUA4GsVBdg8nfXJ7BQKsDaBrQkBAzDnnG5f+fyntGrsaHyyr+RNwP/j95CqAKBUTAIKiPf+qZ98y1X7X3//8ZnOwgMcmbPYrmOmntFzPQoUm9iBLb6arLWlti9lrP++Bhp/E/EYceWTZnjJ8JISJO4pHM3+AUNAiAhAojb6C6hBMWhiKJzDFQWCJcuyGMAnpBSacXKhp0tdyzOnOsWf/8Uns4881PmLXGZeIapFQOpVB75Jhj+uyfUn/SGu0ncGGx6P0ho5HzvZiclw2idUHQ7TtP1YRkbHSFUgkGHrBmOk+lsSE8qovj6s1sPK5+69MXvVLTdd4668/KBNXJti6TnprZzG+i41CYReF0pUSBKLTROCEfIip5MHEmZRn4AG4t7KilKUjoSKc4pgSJJoVghOoAy7UPQ8jaTOTK3GmdUF2tLlihfv5cbbbqMxM4dIjcQYuqtLzO9L6XXP4HxBku1htZ3SyfeS28P8h9/5y+I33vf0Y9j0ehiH/Mc2EaLyOzjeqgAwnQEMvtxzZv5DOpspvvvMP55sf8yv//F8LWj/sKEAMHm22UtrFTwvKMDgwg6jADtCcjtCwER3ntPtx/2Ip/X/xtp/qfVXBYDKRD7U+IYCAJH3iumufuBvvOOld9370r11ek9wzeEZlk8+xWySkGV1egG8lQGjH9y6JGqtjRvomH7E3tHxZowiphQARAgYvETGjom/cRWAwYao5UXmDwmCSqAQjyQpJq2hpHS6Kcst9GwnaKGz8oUHngof/PAn7RefsX/hTP3lXsUKmsBaE3h5OE0oQEa0+9EAN5XjKfRGrm1Lc5kymsaFu2r2WoJf/3SKgLDmGKnSG6OrIp7gFxK38uDNV/j7br7+Cm657lg4MAv50vNSLJ+STAuC6+HyAucibG/SBAWcF5zW0WAxIYDGwFES4rbFEnTQBz7E8MSSZJikEceNA7+Sk7jAzP6Utmnz7GKHdDbhpXddw4tuuIPEWnzepts9w/69Ca2Vs6y0u+zbdwTnZzi9ktKpX8F/+PU/zt/zP575uGYz94a4a1B0SQn9LZNZRwDoH44Lj5VUESjHskePdaKnN5G2OPdcVMy/JHoemP/k5d3T/mENAQC2wLI3NXa2gAKsWeDCQAGGFC4EIWBa152LEDAEltdqyWYEgOnLAMv6UkEIIhIQRNQkvZX3//hbb7z3Da+5IQ2tJ9hTy6m5gnoCRb6KDw61dTSGmx/eX6K2Hx0BZcD4o4AwmD4RI6gx5Yqw/t7zkbv3lwCiCaIJJsRfCTa6E6qJnuMEktmMxdU2S20IyXwg3a9PneqZD33iAfmfH3r48aXeXFdELg9IjXIjnqmOW2PMfD2hAHRDe++aCMA2mX8/rfcpyliejBfp9/nI9T46w8i1/s9a+RPHqj0jOIt79Pj8mVtuv+EQt77oUo4f2eNXTz4laciN63botlbIex0sYBMDxlJogkcQHzAhxO2GNYYMVhfIshqq0O45ukHxYggkOBFcgEbIqPuEIrTIsy4+g+VCKYJw/PhBbrn5Vo4dOURwy7higflDswT1dM4uktXnKJJ9nOxktHU/v/+Hn85//j2PfF7T2p2UfokDBAA2ITgOx0Xlp1J+bX1yOHa2OmesNZLWoHMxMv8J2mvccjN0tlD5nLT/DYtM1/5hUwLA5NlmL61V8PyiAJuns/E91lCJzpHamlfWvLzFD3E9GuUEPa1mbMMaI6KCIEybqKcKAKAiIlmx/P6feNtL733tvcfSpHiOuSwnKboY4g5wNoPCFxg7g1CPIWB16PCXJJY0jTH+VcMIOjBgPsYQjMGVgoHgUAnYuOVOKQDE5WOiMVqfagqaghqCRuFhNQ8q9ZnQ9ol+5bETyV9+/Ev8+afbf9nWmatdYI8RmZ2qkevkhLuWMDCYwNaoP1Fn5GD6JtA76dU9bu+vsJ+tIQAj12VtdGD8fI1j1bBoCcsNt9h69UvMjd9y313MWOdS9eLai7a9fAbfa4P24vhJyk2CvMcGAyGGGtYgECDveUJQJI3afzBCO+/R8wVJmmGLGqEVkKQg1Ht4gZ6CN+A9NJIat9x8IzffdA2JbVP4FUziwDiStE6rgI7M4uwhvB7iv/zhJzv/+lc+/2lvG/dGnh0XNm5GABj0wlQBYG2GqCM0zmG+mJqm0FmX+W9EbyvNuQiY/yYIvJDaP6wjAMCFiAJs+mYbVL/IUICplzcaHJtt0JDOhP2+SmnsvQ01PNlYAKgKF0IwRoLprvzlj3/f1fd+52tvSRt+gdQvk6nDaIEajzc91CoegTwBzTBWsEYw1paOfv12j70LYcTk4DH01GCNoWYTEmsIvYKQt7EJZJkhqKPwilNBTROvM/R8HVfUCy8NHn9mMf3CQ4/zsU8/xIOnmh9wJr2pCDJjkMbIW1yH4W+s5Y9fY+LaVG1/rE713jueZMr4WOszmCoMjtYZZfTrowNVGtM+46BhJTXaybx74PZLuvff//LbuOGaQ0VGm+WFZ0x75aSlWAHXQVx8PcaAkuGDxTuDYvGeGFkwAdThXJe8iBWsEYKmeF8nSA8vPQLDaH2igs/jV3HD9Zdz863Xsu/YHoJbItc2aZYRrKUbLF1fw8le0tnLec9//Xj7n/3bz3yOrHGXBjSoWhjr/4mxJETGN00AKLW+aeNinNZIT6yXtjjnrM9LN0FvK835WoP+N3nHbWj/sGkBYPJss5fOkeLFhwLsCMmtCAGbGY5b+6gnJtwp+fFwtNC4A2C1TvXXCEHBkHc//r/9yO233XfnvqwZFpjFk3ofcXNRvOnhbE5hPR7F5DWsplgRTBIj+YnIwGlLqgx/IG1ojPgngtgUbyx5JwenpCaNuwDiIOQ4zck1pzk/T9sJz5/NNdh9hZp9PPjwc9mHP/EgH/9S51OroZ45L4cw9iAlzD8xX2yovW+g2VW6ZOJ4hM5kmZG8qScbZgMbfzvTrk+uBphCT6Yfr83oZeqYmnIPlcqwVNXCEs5kyhPH64t3fcsrr+EVd72YZqbFwnOP4hefSHtLZ2j3FESwaYM8QFFYkISgGlcQFDkijrqBNAEIOA89Z3HUUQoC+eBlioIJhszWSG3KqbPLHLu0we0vfzGXXnUEmwRy6aII9XqT1W5BNrOf55e8mual8uGPP9v66f/Xnz8eSG8Soz4E7OYFgCiMT9v7YWRsVQ62JgBscb65WJn/BO01brkZOlsgcE7Mf8Ni62v/sIEAALsrBJxfFGDzdDa+x/+/vTcLtuQ4z8S+zKqz3KXvvb1iaQDdDTT2fSEIEiAAklo44mjGMxyNHqiYUJhyyBxbirFDER6HH/xkW+EXh01ZI2lkSSPOjGSOh55waCRxMBruILgAIAmAbOxbd6OX292373aWqsr0w9lqyazKzMpazrnnjwD6VlX+35+15ff9f9apKkkEaAsAOV4cQ5q5CWKP95mMyH8wEKX9AoAOkiRKgs63/4fPP3jP049eu3p4YZejc4k4XQaXOaCkAUaAwOnDc7rwaA8MARy/jQZvDIifRveHcz7+6d9gf4ZVCRJpBM59uMQFYRQs4GDB4CVB1HHAiYvtvodNj3O+dMDrY6H54k/P4C+e+T7ePn/w7d1Ov+EFdNmhdC101DgwfhOwOENXzfKH/5MN0LKHAKVxITGTUVJy+aRdL6OFtIzftDoQFxGxdjy8hQBgnn96acFBi/M3PvHA0tM//7F7cOzggt+7ep5dOHeucenyOtnpbIPxAIRSOM6AyDnjCAIfge+B+wD8wbEPOMBcAuY2wZkPyoPBGyU54HCCBhrw+gF6PYrrju7HrreDje0ujt26hA999EM4cM0B7O5uwSEuGs0Gtv0t0HYbvnuAb3aXybe+f/ny//jbX3lpJ2g/RQhhg08bD2aq5KJxrHySp5hP7orRsuBPSE+0oGW6jfoyJ38dAJ6ypBsqvjFV7FcpAJTchA3UWTZdBOQXABOUlPTIEFG4lgD6N5CsUxIBEFpBIMjuxn+Myv+pAoATCkZAqMN2v/3f/+OH7/vYQ/tX9rlbvOVvkAXC4fQJBi/oaSEA4DsBfNoFc3bACUODteDy5rAvobRv+E5+x3FACQUfF2OHc7xsOJPKfYAxtBotOKQBxl1QOij7bnUYdnvUd/btD06v77rf/fHrzl9+68w7V7192NjsgVL3eOxojXZYnKHzyZAUJn0irAZkZ/lZhC8b8Is0mX6WETMQqwhkVAcSzxoIKgfhKpQk7vg8EQCcs16Tsg8WF9obH75u94HH7zuGW2+/lRPi9z84+5Zz9fI5t9fZQHdnG5wDLZei5Q6uNx5QMJ+AMwqn4aDHGTpBDw44aMDhAHABOIyAMheO68J1m+h5XXQDD06Lwl0AGssN3HzyBO6//1641AVcH33/MmjbwUbPQ58c4D67kfynr5/Z/V++8Nff32JLHwXgDj8jHHpLJImQ/eS7AJMdHxsPf0wICRvjTQ6XsIW6kWLJPwJhi/yHoLnJX9Jai/w1IpYhAIB5FUAths0qgBxkMKLZSuVSBMBwZboACO978ieAAPjwGzrUYd3v/dNfv/f2n3n86GrLv8BJZ53sX1xEb2cbC+02GHcQsBYC5oBTgDsBKN0BdTyAtUAxfBd/uDPDL6KR4Rv/wvvEGQcbfnGFgoMQDsYp+n2GfkDAsMgDstzveA164YrX+Jtv/Agvvnq58/7u4ttbXb7acJ2jg3iDmQYe3itExqBRxPSsHjEyV8zyUwk/ZaAVZXy5TEHfxjW1kiAgScxUsSCsFoR+pcLD6wdnYbh6fOmQwHtj31ITB93O4tMfPnb9I/fdjJbjexsX3yH9rXXX272Kfm8Hfo8BjAx+Yuo0EHgEO7t9wGVoLBAgCAakD8BhgMsxeJiQEIByUKeBPgIQh4A4FD2/i+bCAm644Trcc+cdWN7fRuvwAi6dexMeBfYfvgHvnQ3Y6v7b6V/89Sveb/+zrz+7hcVHCCFtNviEIBULgOgf8WuOx1eGt0ePfOpWJRN8nCgXXirEPPvP2piFpiQAAA3KViI/G1UA5WAzKAK4Ib74Bk8d1AfpVnTQDf2RIgAGP7nnCBZJ/7nf/JV77vzk44cPL7gbrB1s0H1Niivrl3Dw4EF4AUMAwGODp/AJoXAI0HB6cJwAnFEwEv1a36DiH3r3PwbTA5EKAfhgQOIU/YDB8ykYWeDuwkp/u+e6P339tPPMN07h+Tfo1zY63rW7PbiNZuPkEJ+NPvQXzcInv9EX8Hkq6adNA4SdZFUBGeHLyL6ISkBapUi4SUDgo3+4qE0ML0sMAEj95kDIOCXgfPKhR/T7/turS05vyaHnn7pv4aknHzmOtaVWr3f1ArauXmjubGyQ3a0dBEEA13VAnSYGvwzxwNEHhQPKKGgwqNETwgDOEDCGvs+xuOyCuw1wTuD7DP1eHwFnaC+0sbp/CSduvRE33Xw9ltda2O1eBXccLKwcwenzHba07xb6//2HF/v/0+9+/zs9uvQkCIYVrVFdLVsAjEVoyoUgFwAm5D8WypmRjGzmyV8xYmaT5PgkMwMBkFxS3WSImNJAnQXTRYAVti5ABMhvJvNuJ0VApgAYuoX+FK+bDMKcUgSMw3XRe/Fznz5y8lf+7sP72qTLeOcCXVsMsLt1Ge2FJhgFeqAIqDPsCUGDUzQZRQsOKAf8BuBThtELhEYDGhmqk9Fy+DOxhACcM/gBRzdoMkaWPOru55e3e42Xf/q+88yzb3VevdB8/cqVXdrnjROEkKWh34jfJ9l+BhmPB1giyN51Mv1Y21ESG79B4xmdSjKQZ7DM0skiUZB1u4quIUBM+NK2Cb+BEpBVrEJ4bHiuBu+AZGyj7QYfHFmil2+/Jnj8F55+ANceXOXB7tVeZ/uCs3npnLu7cZFwj6HRcEEoEDCAD98ZQdiIkH0w7gEuB2052O348H2KRgNo0hYIGiAB0Au66JM+mksNnDh+Iz702CNYOLCEq5c/AJwAzeVVbPouay9dT//tX7589X/+ne//aNtvPUwJbQWMO8OrNFUAJK4RrQqAKfnHceSRTPDDC/bErbyMphdD0LoI8s9spp79AxoCALBdBdBCzF0FkLa0WAWYoBQtAkIMZSwC0m/X8bbYiEticQWDN3co8RnjDXD/5c9++qb9/+gXbjjaCjaDA4tLjtPfAQ12QR0PnPTQpwwdOIDjAi6BSylcTtAMKFpeG2AO2AKD5/jgnIGzwRy/QyjoME0f7I0DQlxw4iBgHP0+g88JazaX+oG70j59fgffe+FNfP25D66+u9V88/JVr00bzbtC3R9n/EDy5knMxacReexvHdIXlnFF4HGTj2OFWvwa5IhdT6LrRLZNIiiBUcUnfu2FruFYeYCEeiO7T8jgS4RjIRAEbN3l/tnDB5e2b97XueOpB44cePjek1hbIMHV9bPehbNvNravXnJ8b3ANUtIEZQ544IAxhoB74KQP0EG5odPjaLYIXKeBoAs4vIkGbaIXdOHRDuASABT7D6zhwYfuw023XI8eroJTD55DsB20WGv5Rvqlf/ejq7/9B29c7Pb5SUqJFzDeiAhQRP/gSL9eI+vjRyQ3+Y9w5FFM8MMLVsk/gS9dlY2jCcJTlnRDxRuo9t9QACSXVDcZImY0UAuY7V6ACLACKb+hbBQwlAVAaFn0zgBCwCklnDNOHe79+LO/eNPqZz5117FDzfXA7V11FhotuJyBBz00XIZ+vwPm0MEoTB1wl4A4DhqEDub8fYK+zwbfcCcBWs0GFpotsG4P/d0dOISg3WoC1EUAit0+wdVdBtLa768cOu5vbvvtV155Dc987/TFn5xmb165stlY3+TtRqt5NwBQQgIOTvio9DAyUdYdIe5ogyTRk3RRICB97Sw/I7MfD+U2VYGq7pdk4KOFOIxQUBJMHnUh0ZOTWiUYr4+NLSIBEAIdasnxhyb8Xv+VI/vd7v6F9tbdx5ynf/7jt+PGo9eju32pe/H0y83d829Qb5eh3wEaxIULCs4AHngIgsGXBdtLDhgh2NkO0Pc4FtoLcBsLg68Koo9m00HP72Gn6+Hg4UU8/OEHcezW42DooI9t8AZBz2n5Xb7gfvHLL73xu//y3NZur/Wg45Ag8Pnko1Kjf0MrsgRsZJt8U7YJhSeJNshroRulDPJPWa3ecgqyfwAgSyuH0mvAcYeUJdVNsoZKLlm1RRP3yIa6iwDJ7ZYjhpYAGP0vXA0YdsxxSMAYd1u8/9xn/9ZN13/mb9960+EVz3d2190WDUD44KEqEDZ49zoGb/BrNBvDn2G5g5cFMI5+wOC4LSwsLgwypn4PhAXgngfu+VhuN7HYWkCn00e3zxGQNkf7gOcsXYP1Td787vOv45nnzrz97mV28YOL24twmveE9ingg/euUwgs+eS9mPDjg584uyfy9vG4sZU8sTHlxq5JBSCxLbZdSQwksKNvHBRUnRJ9yRIEcd+hcQLOOMhYCHDOO4T1T914dM07cXRx+akPH73rvhP7sY9d7F54/x33wpmL7u7GJijjaMABZRzgDBQUu7sccICFpSZos4VuP4Dnc1BC4XoBCAnQWqJgLrB+uQPSaOOuu4/jrvtuw+qhBrDIsdU5B7q85vfJIfcLv/eD07//by76PnOPU4JuwNAeHJ3QtSO5TgoRANJrTj5WmcQY/VE/8pe0VgCwn/3rkz9QMwGg5JazCpANUYAAsAYrL9Hl6bq2ABj9QcYMyh1KAsa52ybBD3/5qeuv+6VfvOuam4+5fmfjTXe5EQCBDxY4CBgB4xScAJQwEBqg1aLw+h3wgKPZbMPlTXg9iqA/+FhPc8FHN9gEOEO73QLnHP2+D8+n6HntwGkf8Rldabx3Zot+8wfv4ftv7n7zvXNXGueusn2tUbZPScAY5wP5Mcz0ZAOAYLQUkXja2/fkpE9SM/s46ctuZp1X/NoYOHUuLWG2LQAKP7wX3cTlwjOGlSR6yd+CTqZe90CAwWF2R+v6nvfGtYfc83cdaR96+q7Dt99z50kcObLUv/jBW/z9t192Ny+fc0gAuISC+BzNoafvuwChaDT54C2WDGjQJnZ3t8AawMI+B7sewaWNPtqNJq49ej3uuPsEbrn3BEA2cWV7A8sHT/Y/WKfN/+tfvPjqH//7txs9n9xMKXoBQ4sPO5p6XWUKgNjJyLIyBGfsZrAqAIoifwWQumT/wEgAAFrnftpFQCVVAHuw6fG0Y/BkP8OYaQJgsJ07ZET+3gv/8JM3rf3nv/TYzauL257DzjaadBcN4oEwhl7gggUOGFwwAjgOA6E+WNBB0x18eY/4BKTfgItFULYw+CQ7NkCaHtAg2Ol2sdPtA61V5rQOeru9ZuvVN9fxN89e6L3yvv+dd85stPpO+6Tj0MMEACHEx6DMP8nqIrufcTumZPnjthmkH/fjkT+SP5sKP1SY0dVkX7Q2xM+5pGGOW3x8/XDJ+uHG+LUry/LH2zTFQNwvHkNokycLg+HDoQ1wjl7fO3VkAZduPHyIP3bb6hOPP3Ycx4/tg99Z773+6guNc++/SxcdB/ACIBgqTjp4Z0bgOQj8AI1GA06Tost68AjQWnLBSANXNwh2twMsLDXxkY8+gGO3HMbSWhvdwIFPVvosONT853/2zVO/++VX3K5PTxKCDmNYSK0eQXbtjKh/lFgoDh6Z5K8pJmQQoYX6Zf/TT/7ghgIg2jzD0UAEKLnsSREgYYZ4PK0YPPXIZwgA5gw+kNZsoPfCZz5+/eHf+NWfufGatcBj3dMNv3MBiy0XXt8DQBGw4ZN2hA4yMAIQysH8PtrNJpocCPp9wGNokAZc6iJgHB7n2O720WMErX37fWfxYHB5x2/98Cdv4CvPXX7t1Xd3z7192ltwFtsPYZjdE8I9cEI54KQdjsQNw6OvUxUNnLlIX6Ht6OBGhULMiKjvSYx4gyQUiWxPbhHjyNzT7qvYXk02aRC1khgQtFOJI4rHo+sDQglnjLvgw4cGvd6rt9x8MLjrxrXDTz5w4M47b78eywte7+1Xf0g3zp1pdDY30KKD9wN4XaABjlZzAVev9LCwsowe38SWx0FcIEAD3S6FS1ewvbkJALj7/uvw2BN3Y3ltDR6hoM7+nscWWv/bn37t1B98+U3a9xq3EYIu42incVvyOg6ThoYAUCJ/qGFlQQwXZoX8k01skP+kgfZxiggAYE9VAdQg6iYCYsNRWjzlGPEZoNiSXAAwSgk445Qy75VPf3Tf0ud/9enjtx5f6/c33mwGnXUc2b+Mq5e3wHhz8HIUwkEIG3zIZ/jeVgqCZqOFfqcHBAztpoNW04Hf30Wvu4O+F4DRwwFtH/ZpYwXrVzutF15+B1/5waVTr7x/8fy7F+nhZrNx16Rb3OOEuKIjEEs6FQfKKOFH/rRK+vJ4k/WSkzqsJIwWEoo+z2UsyRwj17T0xVSSHguy9fEmS2Ig8qdGPKkRjvGLp4AAg2cFGsDgEPX73qnrloNz99969NjHHzl04r67DmOp2e/5O+v07DtvOBdOn6OsBzQJ4KINwpbgcYZucAW+A9Am0PUBr+dioX0QLOC4tL6O5iLH9Tet4d4HbsBtd96CvsfAG2u9bbq/9b/+wVdP/at/d3qXkdZDjkP8wOfuqD9x4/GLMbKkKACUyR/ZWCoQtSR/c5C6Zf9AXAAAcxGQ2FAnERA/xTZEgGiQDtUExAKAgYACpL+I3e/97ONHjnzusx++7ZYTyz22e7blb19Em3As0BY8L0A3CABKBw8/MYDCgTv8/RXhAHUoAsZA3EFloOcF6PT63G01/OXVI2yns9h6671N/ODHZ/DsS+vf/8nbF3fPd5uHms3R0/zwOEA4hxMZ60n6Ucv8aZRozMhJ+kJ/ofNoFYltjO6U8c2b21Iik3ibAcHoiAHR9ZsmBuI4Uv+UmOJODE18rTACHhBCwDgaANDt9X+05vQ3Hn3gJnrPdfjYo/fcgOsOLyLob/Uun3+Xnn3/dGPr4i4WsQZCKeBuwyMeAsIH38HwKQLPAbiL9gLB1a0OmMtxzVEHJ04ewl333YX20kFsY63XxUrrn/3hV9//w//n/W3mNO50CDrBaDogZjzlIlMSAFrkj3QsJYg5+Wc3y0f+AECWVg5y+V2Vblp0bSACtG9Sg4CprfRSacVYeUSApBacFi8zhiyvHPY2OYj6lBAecDDa3X7uM08feeS/+sc/t3TNdY4XdN9roL+BRhAAXYagw+A0XfRIb/zWNIc5aDAXlBM4AAgFfBbAabfQCQJsdhlvLK55i/sPu+tXtulPXnsf33xh582X3rj0/quvXyZ9d+GE6zo3gRBQwj2AEM4nD2ql7+nk/+GzIc3wR8siYs5J+mM/VdJP6V8dLSkIyhMD0ocQJYJgUkAR9CuS1BLpSSOD6QEwNviML+N819ne/d4tNyy6D91y4IGnH712+ZbjhxAE272ti1fI+dcuNbc2LsPjl+A0Bq+pJi6HSwG/y0G4Cx+A0wKcFsNOn6HRBm6/6whuv+d+tPcdQmvf9d2Ll1j7//yjr7/6xb96z+8GjbtdB10vGPw6YLwLaQpzvNcpAkCb/CHHUoKwSf5D8BRA9VjmCiJec8kTTtTAngAA5lWAxIaCRIAVAZAOlC0Cst4EGNnKCUEXHAuk13n2l588cOevfe5n1268oekRfqEZ9C+C93bRhAN/tw/qE3S8LhpLDnw/gMMduGgOvvwHF2BAz++jx4D2yoGAtw8EWz2neeGKjx+/dhnPvnzu26+cesN752LrQLPZvG88SBPeJ4P5fSXin3R+vMvpB0WWpeuQPiTCIeEcxiOhjRPSL4Lw0zCLqCNEb6USKwOQC4Lx9QDBczAc0P7mBicgg+8GMg7SBADO2W6/033xlsOBd8fJmxpP3L3/8TtuuR5H9jX99bNvs/Pn3yZbm+cb3u7gq4ILDgXvE7Rai9je7aKxEMBpcXR8Dk4JnCbHgSMruP+BR7By4Bq0l2/odfxW63f+xd+89ntffr3f8Rv3UDqoBIy7lfH0aKoAMCJ/iLGUYAog/wi+JKwOliZIXbN/YCwAgDyZqXLOPhcByWOlBWs2bKfvgsqrgAct6OANf24z6D3/yx8/cOA3fu1nTxw+3Oh3tt5tumQHhHfBez30d7pwGEfLbcJtEuz0dgCXot1YAmEU25tdeD7QXlxBY2HVI80lfv5yp/nGe1fxg1d3vB++ufvsi6+cRt9Z/BAhZBEAXEo8xhjjoC4weaJfz4Y1gPgOSwSB9Et8w4U6k36RVYK8d4KWGEi5TVQyfZEYCA+diUoFjy6a2BCCEXCPg7QGKwk4551VuvW9W09cR558aPnJu082cfMNh7C7uel98NY7ZOPcuosuQ4s68Ho+mk0C4voISABGALgU1HWxvd3H/rVFPPjIw7j+5lvB1q7tbaxvt/75l77z+p/8xenO5W3c57rU833WALjCr0fExB/6J31PhaZx9GIVinnpX6WZ4bGKDWIhAQDUdSpAyW3qRIDJsc463SYiIPs1EIQAlJAu42g3iHfqP/vo0sJv/ZefPHb4QKO/e+Xd5kLDQ39nE4QRNCkFDQIEvS4aDaDRaqDrc/QDjr4XwA8ISGsfay8fCnqsgfUru42XTm3guZfW33rh1JnTr5910VxafHJ0jCjlPc7hyLJ9Wd+z7x8CLsjweEgMxPhAn/QlHeER5PykX/WUgOldkSDf0DERflMupxgYGBdvj4yCo0A5j+zgO4QMHAGhYIyjNY7R3frabScpufu2E85T9+5/4t7jR9B2Av/SmffIuXdfc7zONsCCwQOzDgFAEQQEhC2i6S6i7++AthhuvedmHLrpOiwdPtbD4mrr9//Vs+984YunLm50mh+ilDDG2OhNl+IuTo5EfGUO8hdgKsHM5/3VmtnI/odnXioABItppkzXRQmA1EazIgJUTreuCMgWAJSixzlaFP6pv/3EEv8nn3vizv1LXs/ffq+10qLgnS7azgJYH2C9PlrNJlpNjl7vKjr9ANRdRTcASHPBd9r74bst94P1Dr79wrv46g/Offe7P9rsdZ2FlWar+cAg0aFewNjoJSx00OVQmVJ1YA7V/GUe6euJ/N5PGxNUSD/UzmTQq5r008zkDklem9liQBaHRA/vsF200BnRG/H1qn0dL6kNyBxghHDPodTxh0/sA8A1+za//uSDdy598oG1R24/voCVRd/f3TzHL3zwvnPp3GUa9BkaxAHvL4L3F7HYXkW3twkPGwiaPtauX8EdD9+Mw7fc3+/7+5p/+MUfbHzhz149tdFxHqOUBkHABgpC3KfoHlkh/ximEsxskb+4SX1K/xIBAMynArI22hEAEySd461zyjOEQCSsHJcS9DjQarn+qz/3kf38v/iVh+84dpj2di691lqkXSwQB36XoUUXwf0GfD8ACOBSgMGDFzicN1cDp71GibtI33xvC//hO2+fe+6Vt9849U6Pd9zVj5Bhdk8J7zEOAjKYP7V3pAeWnpVnrUsRA6mYIeESaqc72OUaHG2MrIYnw54YkEwRDNuF10+OdpT0x3/GdJi0n+ZDYTi6YAGcgPdBCaeUur7PXBYEV1aD7ZeefuTAdZ9+8p5bj93YxOoyQFjHv3j2DDn//gcO6/TRCBrwuj4CP8DSiottv49tv4eVIxQ3njyBW+/7WL/RPtb8/T/95voX/uzltza69NFBJYAT0W5EBICkwiXbiXTLOGKFkf8QPAVwFrL/vKX/kaULAMFims2nAvRNTwTonnZVESAeWClBn3E0KfNe/fufOEw//7nHb11tb3X59jvtRdoD2+2ABhQuXYLf5yDOAuA00fc5POawhfYyo60l9+z6Vbz8+mV896dbLz7/0lvdU+st0mq3HwMASgkHZz2AuJA91KdQUJIeGZX7l0vWS2GIaGWorbjEn4afHdPUQbMap3MgjRMEg/YZUwTjLZLqUPy1w/IYgvVZHZzoE8GGqCUTMc4JIX3G0QIHPM97xensXHnikevow/dce8cTjxw7cOLoErydi2zjg7fZ1QtnnO7mFeLSBWxc6YE2fDSXXHyw7qO9D7jlzptx/I4nvKV9NzX+6F9/a+N3/s2PT216zcccSoKAcRrfNR66lu2Rf/gYZEHNH/pTb2Y3+weEAgCwUwXIcNbCVa4tZDSadhFgcquoiICkAKAEPuNwG8T7yd/9yL7Wb/7ax25ZW+n1ts690lpr+WhygqDDQWkLjDURUIou55zRBUYXD5B+r0HPnO3hlXc3d771w9dfeuHlK/4GVh4khCwBgEvRZYyDE9IiABGN0LaOcEomJj6i8TEkXCHl8VF/tBQmpRKz/UhjgXi3OboK8Xh0uwaUbujwHzx0rKNiYHJeCBGzWlohL7FatZ3AxId+1KdQjYIPpwfA+xxk/BO+Xmf3m3fcwBqPPnhi7RMPX3fHvcdX4fq72L50zt/aOEcvXdigvV4HHAE87oJTFzteF/uPXI+HPvwJf3H1qPsnf/6N7f/9Sz99uYf2Y6OHeKP9IwWQ/2T/0qFmj/yTTTSipjY1JP+EQyyVEQsAwFQE1KIKkNpomkWA6e2SIQIQ/fjKiPwJ81/7zMeWV3/rVx+95vB+1r+6/mqzEWyDeF0sOKtouau4dLWLwGmgubaP9RqMbvWBsxcbeOGVznvf+Pbr5597tbPbWFx+alSmdSjxGOPgGLw4JTwYh+lU/RVtKSYty4vbyrP/EetE+5VsT4xPkbKbjPBtk72qJeLqCwLVMx2XXNKMnofbjO4sSWVAoROp/UvbyMf/SzQcrY0/S0IGbxn0CSF89LW/IPDfvmZh+8xj9x9Z/vh9x+/78D3X0nbjKph/mW2sb/L33r1AG45LGq0Gzq1fgc8DHLz+IE7e86i/cuiE83t//B+3/ugvz7zRDZoPUYLe6GHE0QOv9sk/ub9JqL1A/hqRS8n+BSOWkgAQLKZZLUSA8V2r2LISEZDnllETAcPvolMn8H76qQ+trfx3v/b40QPN031v90zz0OEV9Do72NrcQat5ED2vzftkgS+sHaSb/T5Ond7As8+/+9OvfvuDzTcuLLWazeYDAOBQdDnjlIG4w4+wCTsj+7Z71MQDqqxVVgMl0o/SzsQklV6eaCDubW7Sr73lEwNpmZQKXIJySfh8mAsC3X7E6xOJlfHtw22EwCNAwDjao+b7gs2v332M7/u5jz908O6TS8euO7CKlYUVfPDum8H21XO01eySne1z6PQ6aK+t4qbbHvYXVm91fveL37jwZ8+sX97uO3dSSnwWcJdDVPuT9VzXYrjTTv4KQPUmf7F3igAA6jwVoORqoQqgBlOWCMh726SJAD56pW6D+v0ff/r+xvHf+vVPrRxZvuS53ruNZsvBds+DzxtoLB3kvLnGu6xJr2wDP3r1Mv7jt374+rd+sHlu21+5j1C6CgCUoAvA4cPXpMa7knVcrR1VLvxT0EyB9CXYoapuioXKvirNx4FDB6SKLN+KjVgtdevYsvJSobZQHmbUxIA6Pk/bOG4R/SO2LZ13GAH3HGf06wEOz+v/+OZD/f4nP3b/8kfuOX7y7hsX3H3uFrzO+6y/8z71vCvoBT10eQtrR+/3Sftm94+/9MPNf/3MhXO7fXIbIfADTlJepGVxrJka8jcHMib/zKbFlP5HliEAADsiwFYVQAs1o5GFKkBkYxkiwMatI+4nJdzjHA3K/Lc+db+38luf/9SB/Uu7AemcaSy3AZ81uU8WOG3tp7S5H6fPdfG9n5zZfeabL53+xvO7F3hz312E0gOuS/wg4MHg+2aTF/ZkHcPEdlMRICX7JHtGsh+eHMSz7svU7WkJUErDyTNsBZC+7sEsSGyMKUDxuta5/EnEIQNPsiIpCMQnQVzGCv0t4aYswo+5hxc4JdwnQODzwfQA57y7Sra++3OP3njkqYdOXvfgyX1r1x4MQOgldnX7NLmys4ld3yGtlZt90rqF/vmXn9/+07/+4EIvcE+CEMa56OeBFseZCsk/Y5N6a+3Svy3ynzQogvwBXQEgWFT3rGgqILXRXhUBUexR2Z8y751P3O8v/uavP3744GonYP3z7sF9RzhnC5z5Teo213DmHMFXvvrKub/6Ty9s/uRc6yxZ2vc0ADiUBJyxYPiFNCJL8XVOM4kuRiybMkSaPDY6x14JmHfaIMUlfccTryZMNs5RKEt2KAssq0SRq7IpcRY9vR9bpXMMVG/J5OZodWDSLkcumZcPYkDDxYAQ7juO4/g+cwGAdXa+/pGj7Zv+3i88fPRDD642F5Yvo73cxaUr6/z8pW1y5Jq7A0oPkX/559/y//zrO+/teM5JQgjn4Z8CWDOyR8lfI3KFpf+RKQgAYOqnAlIbTZsIsHuTEgLOOYiL4P2P399d+43P/63lQ/s9RvyLzmKrxbvdBbKzu4g3393Z+eZ3Xjz/zNcuXj3dX+bNZvMhAHAo8Qev6B0Rv+Ds5BECMQdVPy5qnc0x0gZGpJ9pukch/f6xd+VlW3aCoTisaAxWcVcdU6kMyJtwrWtYl/ilzcTEn2hFCHxK4PsBFjjnO+7O1vN3Hu0f/Zmfvb394YdvOnr8xiVsb1/Flcvb/Pprj7HAJ/iT//vr5E+/2n/VCxp3EoBxcOGLgsxsTv55wsY3FpX9A8oCAKjzVICSqxp7a0aVbSxQBEgGozwRKPw3/s5j/tF/8pv/oLm8wDnztl14PWxuAd/4/oXz33rup91nX+6/3afLTwOAQ0mXc0YBQvmgzJ9ZDVVh8HxCLmaRq5qIV2f4lk76Ktm5AkwhUmDcN6NRMQUvH5CxhFJ0TM38ZfoyD9tJBu8sSD4YFnxCCGF88EXCwPdf2xdsrz/+0OLxv/d3nm7ffevagd7uBbRaCLp0jf/Rv30x+NJfXdoJGA5A7+rL6H90vJpK8lcAKo78Jw2KJH8AIIsrBxMFSEnT1EV1z70gAuwOwFEBxRU6oWScEHDKvNd+8YnlE//tf/0PnNVl5nq9XXxw9iq++rUXz3/lmTeCl66srrtu8z4AcNzhT/gGn98d7KlWVmV+DQlBFbRQ9BbIIN9E+2yzkq+Gh17Z33EYO8N1scahtm/SfRltKLAqIHVOKZsqt8ywjAxQSUcJWIgAAeE8YCAtDoBzvuN4vZd+/hH/piefuHfprtsOrN5y18N4+2Kr+9v/x//7g+eeP3u/77N9hnsR7vLQJufMLvkPEfcI+WuiGlXTyOKwAqAtAoqaCtDCrmYqQA2m3iKAEMLBgksfvXfl3D/9b37pngWngxef/2H3O8/9cPvZ7+2ePRvscxy3cTcAUEo8MD7I9iUxcwmB9MZRi1/TocKC3s0/KVMWQ/rDGFkgo45rEaNZSGtmOqpn7Wf4mOQMbnIYiOl9ZZHldPJl6Vf+Jtc1IwAnlPDRC4Acf/cbD53cvv2ue+8nD3z4Y42LV8mpf/9X3+i99JNzHw0YH0/jGXRcZVVOS79h9eNVUPovivwTDtneHNoCINayKBFQZBUgtVG9RYAQ0fx2JQ0Xb37273/8zM76T25/85WXGq+8v/jjbmP5UULIIgEHIfA5j5b403ZN7909JPfREVX5VbhDuSogi5VqGewtI766Z/M2LX4MgHQhlAqUbdmHNomTVh3QKfWLYsvd1PZHgfhFWzihhDEGF+DgnF1ZaW29fOOxm4Llfdc23njrzMHLG9t3KHUgo9v2iT+EOs3kn9m0PPIftRoLAMCgCqDuJGha4VRAaqNpEAGx0dEwBHVI3+/7Zwkhi4TSIwBACRjnhHOAkuTj6dFwsmqAVp8sH5+wEAjdB6ZUUetsX9sqTG3TQpRUFYjCaNeLCjwaOYk/BCHXBuMDywgBI4RQNvg2ABgLzjiUHuIgLYl7ZtzRQva3RU1sL5B/tEHRpf/xbbUYewhwOqcCtJEzGtVdBIxus/wiABhOB4Cz4c+Bxk8Dk4xhT2X31KsCdo9RuNf6tKfqkdFORGK1JHpdK0EYhENZrQpMtqscJVlipSdyldDFWxV3J10bSO9jRggIN2XtBPkDRYx1yViSLujgGQDVkvwTDurkD8i+vqYEQRJ/anpmO2thayFrYRm3Hm+wP+oPEM3PQwRr8IUbJ7F+CCp7Elpl98JZS7oYUEr1pG7Sy56oIXPpUtxLgfTjzUoRAWXOIcRjWRYEWccn9YQSQUP59SvySnhIauqi9WqaX9If3cOYj/wBgGrHjMUeLRQjCUsifyPP6ST/uCUEgNHYVBsRoGmp2HUWAYObWigCAMvHa1DWSxMBACbvcUk7lWnXJxm58iHoeCmfhQBGExokuUnRFK5TlbK1tfNTJumnmYh0LcGl/Z16nGX0ni0GUpplGk/8YQFUApOONrpjCxBnsRV6NRXNQGWQf5EFrdLI38xFWAFQo6oimdnUDKoAUysCwtCxkdB6mEktIG0AzbWLPI5cwPU1DMAyZ05kcWP7HlcUMheS0UbL6nbPxc2yGAhDpR5nngwvtKT8EyVR9o9yeeSfkfXnjh1emPrMXwEsV/ZfmmX3SdTCcAogDDkDUwFTKQImN/mE/s3Ph4qpTAkAatUA1YiWgKSVssljjoq/Soh0KQQUnt+QZa3GVnfSl5lFMSA8ply8XqUaFd4o+jF9Sng9m4GsPwEpKvnbukZniPxLy/7Nz7dUABjxx1wEpLeOiABoYarHLVYETG5+RSFgJb7+8VK7JUikIZdweKILor9HJQy1SWC1vs2U2RADGn6CKQL5mJn6BIBwjVqF1JIlqmNxK5D4E7AFz/cn4mWuVsM0AJsO8s/nkloBUBu/Y63mIiC9dSKDzDPQJ0t9QhEAFMAnk5/8yGbrOWxWA8IxkmDq90TG9TX6M3RohQ8vjg6xNNs3zSFnjfhFlizDiy1juyz7jw1HSqGkwOkiN+lhl/jliHH5XXTWP1gh64uVQPn4Wt1jpsg/GyGtRc4pgHCIkgauWRABkY32j11CBBQTBqNLi4eYMC4GItWASYOcUcOXdBagYcB4QjIUBCT0tzHgGMD6XMGUWZi1RcfHzAzGyBTLrg5khTCppCbx0mR2AaZM/pYC7Rnyt2n5g2UKAHXeiGWdRVUBctrsiQDxYDkRAaM2OcOk2gSYh+OF+sCTTQ1MZTAuYAfjYmAYRi9S+DyJ+q0jaqbZZINWPjEQPi/FDsJZ1QGV6lTKMRCSv8zKIH+eXBWxPNfqNJC/bbOV/at5Z7VyVUZkozG7SBGQowpg2KFcmFWKgMHWmDgDCuAYMXBcEAC61YC041IBUYbFQCy8uDejc6ND8FbzyQoszzAanx4IXywpg+dUZV5xEUFUuNBifFXYrKx/tsg/t2NmU1vkb8/FHVx/tlLDGE5NRYCy61SJgKzY+c6NXjSEwOUZkZoQ0O1kYQpHHmq0KBQEtvtjMpJZGKhLtyhJ8shx1EqTLVoBQbmstF9SfCl0USX/ULCakb96pcYkhk3yt3Vm+HAKQEEEqPNFPmaZiwDT45de+5yIgFHbHKGUTK2yBNh9PiCJbh04JWQsSx0/MFBqLwRWFYmbW7THoww5Xkkpvye2YdXRyyJ/rhjN9Gqek38+syUVByJa6yFAIxFQKMno4s9FwKBV7PwABZ0jNfBihYB6P8xNUuaPVwgkXZiGon5RJrxapZdwfHogvN42Qe414h+sUIs2J3+TpkYmEGdaLhk2EQAKVYBxM40AJk7aND0XASHLHgwn1YAypgRG4MgMUJ4QsAEePs4aHRacGvE3F2dLGEivSKMBND6Hbq8kWpjVifiF8HPy1+6FUtMc2b/BJaAsEYYN3fj6qXweQNumSQRAC1cnPhD7qmDRiXJthEA4im4Ak4f6NLoSXp0BWyeBoFEFtWx5qwLlEO30Ez9gdsVx4Z8prfRxDcFykb9mhPzkb1HkhqCSUwBWE+58xK1F09qhpkAEACEhoEtSYZCsPpRZDRgFQGaQcoRAOJIsiGG2n9cyTh8vqRuZVnQZVMnSqgJxMVBCh+tG/MIQJZG/ynVsgpsDLDf5ayje/GdWOa/XbiR5BiCbAYxEgAGx7GkREGlQHCtPRAAQOVehxWJMbZ/KEwLhaABAYT3bt221IN+6WVwAl0j+2sSv31rbJNnknPwNe1E0+XPpgpqLRisqb6eWPWqbgRNPWcqPr4Wu2FD/9leD0905Ah2ZJhwWCieYzL1PtlR3MTAS+i9O/DUj/7kZWPj8WrThNal3aRZ6IU9CxFboRS2G/M32fK+Rv00XLmwoFgBjVFsXp21SmT0RoA5ncuvoVSQSMUoYp3IJgdx9UyEFa8HmVqjpnKecYiAUqpbEn8gkdX/br3tcQkHzc7W6Vw3J38gklZr8JiZ/wMK3ADgMnwdQdzRpnttB2T21oXYnsuEwaqCLrfdcwMAj3zkzs8hOKrUETKYH8uxI/BjOKwPVma1BMv4MQXZIyxLfnkmyfnWrU8k/w6s25J+ruTH5572i5BWAMbo6YWRbvkqAdp4+i5WASAOT0z8N1QD9QGpVgYLKv5Gg8wpBsVbGsRZcJ8bZfti5YKsk6x/FiceWtjLDzgFYDvlzwV+m+DbJX579AyoVAA6lnwYaVwI0TTtPn8VKQKSByfGclmpAuAdQCpisCpAKEnTRcZ1XCfStBmIq8ape1T6V3PdcWb+Fatic/E3QTQNaIX9AdQqgSBFgQCR7SQRACTKzpcR0hUDZ7w0Q9yI7YHTQJjyxqQKbi4J0qwHZj4wL/xxa1lTBNBE/YH4Nph4klU3q+IagU0H+iYqNpktaK4WG6s8AFJnxzUVAPkggJARMTpKeEKjmJ4PxXoQDZgvTkdVDDIxMdrwr71iBViOiD5san8UsLgZK3DdJ2VitB3mvryKz/gzPmSV/y6aIrfkQYIFTAXtKBEC3IwVPCYxM7UGo1GmBOEwhlm8KaYxSKzEQNpW7t1YdHlpNyV1mRqSfApI4JwUcDyGkStZv63qpqOSvATqd5G85+1c0PQFQ5FSAnqPEZVpEgFFHNEUAtPGjlj2YCacFIv2wYSpAZkw+HWJAZlNGtnUx66SfZrILyTCyVrm/iAddZf2QtjTDzwE6J3+10v/I9H8GOBcBlYsApHlZmRIQmXwwszMtYDlD0cScbjEwt1QrlfRVzGCAE6zgpV2cFZb8NYDn5K9H/oDpewDmIkBPBCCtsRlJl1sNSLPYA3ex9eVNC4jMLLhUDOhDza0K46mL+cDKNCnxV9CBKshfA3RO/vrkD1h4EZA9y5+tViECoAqRGqtAEQCEhEDx7DUJmbciYNPMU/oE/8+rA/U0K1m+PQS74csm/lA0haBVkr+4WQ3J3yxo4WYuAKxXAWKtDfmqbBGgBZEpAqDbGTWv0qoB0ZCDSILqTjldkFg+BuexhQTCXBQUb1YzfLso9rug+7M+i52YupK/hrNWcwvkHx84TNzSWhl2LF8FoBARYMNRE6Q2IsCoM+qwQEgIlMNUo9xF+KBgbFX5lj+dT60QmMPOLWyFEL5dpFxWC+IfxAz/o9DSPEZO4CrI39gMIYomfwBwc1OBdREgyBhzPQ+gCDIjIgDK0OUycL2FAGBDDMRRAIEgyB9idk1yrOwSYU1IH6gf8cf+zGiZL04O8KrI32jfJdM52m6yVjkvFncUbC4CTOLkFAFIa2zOjHWtBkxC11kIAAIat4Y0RtzLwqAUoi8WNZfVhvgHccP/KLQ0j2EBeE7+oVY5LxgOmw8BzkWAGURVUwKRRuWz73QIgZHZqQ7IEMMmFQZ2wxdrhWaRFjpQldWR+GN/KrQ2j5MTeE7+oVaWLhp3xAJl5oGzKQIQcaqLCIj2KqtRudWASfjwL5pjvxoIraqH2asOqKDHLVMgpDpb7EgxrlMTUdkkpC/dVIqVkfUreOcif4OezQT527FRLHeyaEEEKFYBJhGVQU0cU1yKEAFJJ61ktiARoOxZYTUgGlUQv5ZVgZGJbtviOpprkBg6p/WuxlQ6tPr3EEC9iV+xE3UgfytZv7LLNJC/ndL/6C83urpcEaAJKl00QFADsSACtGAyic6cCc2qAWax8tj0CoGwlSsKdG1KKBTT1NOxzYnfGvheIX+tABbJHwCoaHPubnM1FL04PHVRFUH7NNu5EvRgMhubnyHle4GHF8q3SReSZ228aqr4gQv+m9vEpvj4SLs8WFnt3vBwV1Rb54tlAbwq8jc+VznIX7lGYJn8gSLfBKhYCdBLsvNXApJuNawEKDUuuBoQ6UN1qXf4ok08JxBuUJ8EW8PS7uip3KEMmyJSV7FaZvsjq1HWrxmgSvI3sikgf5kJBMBg1DfkVgHUXhcBSESKrtFylzQyfzZACX7cqFq2nUQX9CN8g8wEd6re8XXY2erprjST7upeJH4FhCqIX9lt75C/bE9dMX/MRYDUASax6lsNUO5LDYVAZlUgtno2rXrKmXmrPekDusSv0czcu7ZZf7ThXiV/IPEMQNLNysXN1ZD0YvHURTOUIu+cpJMWjKai1TWuE4KHF6q1SXcke8Dlm+Y2N6mlXjeDDfW5rPjkH0VenpO/cRSJ4/SRPzASADVSuHMRkOGe6ZD/1tbTGvUYBifjdcrIPRcDc0szJdKvG/GHyF+9dc6Y9oLMyb84y9xTHn4GgENSLh1skG7W6Y31dwQIWht2NOqmCGIUa3QyDJ8LUIqrjagfIhEmf0xbFr7whVME0UZ16PLcqrLUEbn8BEjNuPBPRY/8MS0EsUb8Wm7TQv75s38V8gfiDwFmjPp7TwQgG8j4oCQdtaCUupePlJW9ayoEAAMxINg8txkyxQpa/UgfmAXilzcvkvyNarwKsepO/tkNks8ASMtfloyr4+lF5amLhihqQMa1NaNoBg75zp/y7kUa1qdQOrJJhTe11ouszXObMss8n6Orok7l/bCFeqXYQTv7UWPy1xqUckWSONaT/DMjx1aJ3wMgTEUHK3NXAcZQ01IJ0AAyipfMmLVz6BKqAVoINa4IjExcGQDm1YEZMI0UqH5kH7YqMn5FlNpn/cmGe4H8U69swSrNFwHNRYCBY9ERNR3yn71ZEgJAcpiQThUkG9dxd/aeaWSC9Sb8kVVF/IpIc/I3c01rWTT5S0wuAKQ8sbdEACKu1YgAJNZmQGQ62CFj5d0UCoH88Ysy5epAtHFqs7lZMgMCmDrSFyxqeNqJbyGQvHl55J/r2MwS+UtWp1cA5iJA4KohAmASU+xYXDUgGUs3jDJConF9qwIjk1cHRjYXBYVZ2dleJTYFxG8QrJqsP9p4Tv7pqwGVKYC5CBC4ahBX1VMCUHEqcVog3HjsUH8hMLLkvaRQJRA7pjbfU6Y9CE5bdi8y/TK/YfN8SAbBprrkL3SeTfIHVJ8BmIsAiesUTAkox7c3LaCFIpwemC5WzK4SANrCIMNtqsx4oBPJrWm3KSF+g4B1KPnniCZxnl3yB3QeApw1EQAtgJS4ZYgAmEbNgsnT0C6KUAjk70cVJsuAtIRBOpjcyjhc1ll4Fok+bOZlfoPm+ZGqzvq1XC2Rv9Bxtskf0P0VQAbrTJUI0AeIuCHiqiECoo6aUS1VA5Sc7AoBZaQpnh7IMj1hAOS6OGtn1iliCqwO2b4GmjXiNwTTdsukwRwxp4H8TRtMTPNngJBw3WRl/UUAoh7WpgQ0iKqAaoBiZIM+WDmjYyTooM1QVSDNsgZRtb2t4phkjzSzTfAiq0u2r4lYdblfy7UeJX+91kWQvwBQM4a+ABgFSREBVqwwESDwsCYCNMAsVwM0Ihv0wW4WbiwExk6zKwZEpnZPR1sVcVT2HpmrWj7SN3SxgzjFWX+OiBLnepN/ZnSDGGYCQGoDCrImBWZdBOSKa7kaoORUsRAQOu0tMaBqc7Iu2uwMwHua+LVd9zb5W5rwiBi1r9p4+majOCY5kIFHzus4eXkWdzNmORtBKndZY9800LTHgoST3X7NbW5Ji11jBhev0fWu26+czURuultygAoacsmSjbgzQP6GcdyJt5VH4iMbprYSAG2QlPiKPcoV12I1QMvRbkXAGDFRBIjfDfPKwNxMzU6mn8PNDqr1Ps9C1q+POEvkD/DwFMBsiQBoxRXg5ui4sQjIGbcQIaD16L59IaCNKnQ0RpvbnrS6k74mcl0yfm33+pC/tkyYAvIHEs8AzI4ISO2WtDWiHjlFQBRNgyRz86m440a7o9UX+0IgF+pcDMxN2eyRfk5Xe8jzrF8CUFTWP2w9JeQPCB8C3MsiQOCRs+MzVQ1QdiyGZHOhCmcF5lMFe9fsEr4Fd3vIdSJ+bfc5+UfjFkf+gPRXAHMRUIvnAizErlYI5IqkhGqMnFkdMEaeWy3N9kBqxd0u+lQTf9KhypK/UespI38AcOVUNC0iAEqIuUWAGUjEFRF3TWLMfeDk0wIavcjhWIwQCCMbo0t5fy4IpteKIXxLEHYjWCf+HKBG7kVn/fqo6q25CbxC3OLJHxhWAKZaBAAF/0IAUa+qpwRi3dGPLgYw3q0aCYEweq4Ic0EwhVYc4VuEsRslR6dmMusXAugjapG/xQujbPLnCE0BWCPlTEDLImAMWZQIEHhZEAGIQFRRDUjGy0XNxkLAOGJ5EZQFQe5Ic1OylJGwkAG5SDOIUkfi14awTPxCkDn5Z8Vz4yst5oa1FgHQji0QAfogaYjCNanOOeMXKgS0AIqtCoQjWImSyvuyO3UuDPQtY9QrgKHLIX2DSIUl5lXMiUxzyX/YemrIP+kcXpN4CHCviIDUrul41KEaoNFcDmLx+QBjgOKrAvEo1iJlFgPmwkBuCiNcQcxcHuEbRiu0Ij/l5X4pyJz8Zc7xNa46p81FwMQDUS8LO5CrGmClD3K2Ll8IWImqHWlkVq9FkSkJA2HjKTWNUaxgRi6X8HNErTPxG0FMe8l/2HqGyB8YVQBqIgJgji6ALFIECLwsTQlEIaqqBohBqhUCuSJrWWGCQBYgbloCQQnEklVBHFMVLn/kOfEb9mF6yF/piJRA/uDhKYAaiICc6AJIdcYxIzf7UwJiCINqAPL2owQhoA1SvhiIRy0lsurNn9qRPcO0Eau2G9WQfjZEVcKtnuRv1HoGyR9Q+Bzw1IsAYGqnBKKoBrRr5UAWKARygVQjBuKRq+nB0GpCulVZPXY/Ry+mgfiNYAogfiHQ9GT9ydgVkn/IogJAQhhTLQLGYNM3JSDuSxXTAulAVqg4F0h1YkDUg7DNwgx+1VYPog9bnUnfUhAjGH0CMu/HnPyznKVwoQ3JCkAtRAAwfQ8HSrwKqQYYAJcgBKyFmXIxELa5MFC3+hF92KolfTWYqog/6VRc1m+GXg/yTwGtgPwB2RRA5SJgsrFqEQDt+JIpAX0gIXKuaoDFvpQiBHID1UsMhC3rfq9Xb+1YvQleZDl7vAeJ3xhGCaRo4h967BHyB9KeAZh5EQBl1OmoBiTXGgDl6JEYrKQwmgDGIKWZ7phQxd5MH6FnWSn1eUtQpQVSdpr6rN8sjEL8epI/kPUQYJkiADL3gkQAUNKUAKKeFqsBSRiDXhZzYKVbxFsNw+QCmy5BkGWzR8ZlWJWZsymU5TM9g+V+fS+7WX80vgQ4d7z85A8o/AqgNBGQ6l6ACBjDFj0lMA6UucrE6jUtoAZoNZw1ZTFbgmBuIqs6a84DVz/iN4ZRgzZCr7rkH+1DvckfUBEAI5AaiQDkiyCA1evz9FQDNMErEgLWQlrl8LkgmH6rL+GrQ86JPxdUlkdhl0j9yR8AXGU6q5EIsBAhCQs+BCxySkDiabEagARUnYSAHNR6SLWwhmDWQOdmzSpj6AJg67IvBRK/FGzaS/7JpazVplFSIRVjuZPWe1wEjAHLmhKIeVpkQLkQMHg+IAmU00quCoiArYHPRUE1VhkzFwxdF+JPOs4G8Q895uQ/buiGF6ZLBAB1eC5g7GIezBaYArohoxfCyupVAathCweX3X1zYaBvBTJyCSGmj/TFznPyV+lDCnBNyR9IPANQlAiAGq5GrPDGQkSAwZSAeusMz1KqAYYBKqrVlyYGCgkgClJ4wCmxEki+xHCVkX5u2Dnxm/ejKPIXO9sif0D4EGARIkADV+YKmXtBImAMXXE1AMaAClB1EwJqwIWKgXiAQgOlBSy1AwVZyeReUXj1MHPizxtlasi/gKw/FdaA/AHprwBqKAJS3SciAPkiSKD1RYBZPySelqcFkhHqLATSwQsXA6JApQRU6cDcxlbbQkLlcwzKztZ7WmnWP/QqrOSfXMpabRolE9aQ/IHUnwFOpwiwEEEMrTklkK8fxU4LyOHqKATC4OkBShMDooClBt7jVpEWqgXp54afLuI38yw6608uZa02jZIJm4P8gcz3AEyjCADqNiUA474UOy0gh7MgBAzdbQWIX+6l8bLshpwLA32bqhmEOpO+GKAc4jePVJesf4ia+CulUe4oSrA5yR9QehGQhghAsmn5ImCysVgRAC10q9WAfIAaUXKqjUKrAuEA2UEqEwSyDoRtL4uDGs1sGGWaRds0EL8UtOSsP1/ILOR08DLJXytWemO1NwHqsI0kaa1SBCBfFDE0UEE1IOZdwM6lCwHDYKXU5vUovnJBEDbVG3qahEKNiF1mtSR8K2E0M0nrocok/qFXVSX/jE0mkVLXWiR/QFkAjMBqKgIggyhrSkDaAblbvqDJVXpdUIoihqx7VSAeSC1YrQSBzKaAVOtsZRaiqwlVNfHni7b3sn45SBnkD2gJgBFoPhEAIYIFeq5ySgCovhqQH1QnUv5gpVQFRMHUAk6FIJhbqtWe8K2F22vEP/Qq6FRVSf52Dq96Y00BEAa3/XCgBfaqakpgDK8vMfJXA5BEKFAIJGEtnTcxeEGWXxCoec2tLCubfnKZtZAaBGI/lJWIdSL/Wpb8teLpd8w1JyBFTy0RoIFr1K3JxuKqAdzo54J6HooIBakdMawlFi9tikAUdGRqwWW321wYFGf5x/0K51GmLdtPBZ4d4h8iS5dUN5lEyoQtkPyBYQVgJkUAZBCTjYWIgFEIw2cD9DyEgZVX57XCpgfCEDlh8gfX78BcGOQ3e+N8hYRvNXzJxJ8KXkG5P19YFfT0AFWIt4LJnyM0BTBzIiATooQpAWCmpwWyoS1XBXLC2OnAyPQ7knWb7iWBUMxYXjHZj6xg0rcaQgt8nvXbipYJXQL5A4AbZpu9KgIsRUoJUVU1QIBQghAQw1sKnC85t2j283ydW7mOYqE8+q0J0Y/MencqyPZTA1RE/PlDZ6ELl1Q3mUbLhC6J/IFRBaBsEYBk01LKyVVOCQDG1QBoeykgFCgE0uEtp/OVVwfiVs4EQM0osCCr8V6WRPqFhFIOUAXxDz0L3OnpL/lrNxZ78fCvAEIjduEiIKVp1dWAQnmRD/+n+ZDg2FXbSwGhYAJVqwpYCl47MRC2+QRA0mpM8CLbE6SfP3r9iT+5pLrJNFomdJnkH3JP/gxwSJJ7VwQAhVYDxn0wi5C/XylSoqSqgDiE5eC1mSpQNdWbuvY7gqkjdpkVthsVlfgzg+TvQf3JPyPIHiJ/AHCFlLIXRADSYGa9GpCBUrAQSA9RUBo/dYJAZjNCrnW0Qg9thdl+ZpC9QPzJJdVNeSJmwldI/sC4AjCNIgDq+Jpx4xvLqQZAO8osCQFxmAJr+jMjCOZmbBXW2UuTcXUmfjtdyIogXFLdZBpNaUvF5A/w8BRAughAcqtG+CIeDhxtLbIaEJ0SkPcjp43B964QCIcRhyqYseeCYPatQsIvJbxysDnx2+1DjbN+KcRgpZtcKRYBkq0a3anxlEAmTInVACDX8wEw8hShCJAKTMg1eqHcwloHCgwzt4KsHin2TJF+fpRiy/3DCNIl1U15ImbC14j8AeG3AOYiQL4RKLwaMA7FjZ4PGLsbeWoglVQVCIeShytJmcjux7kwqM5KZVi1oKV3aU786sSvsNk0aiZ8zcgfkH4MaFpFANTx0+KmwpRUDRgHKGUCxhypRCEQDicPWUEtfy4MirdKiF49eP10SE2IPz+IahThkuqmPBGVttSQ/IHUrwHWWwRAijKL1QDUTAgI0EqcHhCFlIetQBDIQodtLg6SVinJh62mhJ8Z2F6vZor4FTabRlWCryn5A5mfAy5SBEDNOyP5LFwESGLH45RSDQCMnw+IQNjoT42qAvGw6aErFARhU7nPZ0kk1IbcRabWufoWIepC/EOEEg5U+Vm/HKzKrD/hqUH+QKYAGDkXIQI0vauaEkgPEolTCu/x4f8Mnw8YQxh7a6BVUBUQhU4PXxNBILI8g1cRu1FrEtexKSB8pQ7MiV+jsbWoSlumgPwBJQEwApH/Pq8OIgBSlDKrAYMGe1sISBArFAPx8OldEN0wNRIFqlY5e9XF1A9ELQ5ZiaRvB22WiV8OVnXJP+KZQ4UoCoARmDwFz0cmmiJAEqj6akC0QeHTAqMgtRICCogVi4F4F0Y286Jg5m3KyH5kSp3Zm8Q/jCRdymhsLarSFu3YZp3NzvrVsTUEwAg0nXnNSU/TU3tKwCBGGgzSoEquBoxD1lUIpKDWQAyMTG8yQHaDzYVB8aY/cNaK8IFKSN8O4qwTfzrYLJE/oC0AwuAVPxyYEqiUKQElqJKrAaNAloSAOYIhao3EAGCa96fdfDXYqakxS/OidbKpJf0hSokHtm7kX6uSvxRGH9tAAISD1eThQIiblzYlkApVQTVgHDafEBjD5EJIQ01BrpkYGFm+vD/rBq3RjhZuVnLR+ltFpG8PdW8Tf+qWkrL+hKcl8gdyCYBR0BqIgJTmpUwJjKCQBjcXAunIKeg1FQNhszMhoHsT1+lgFMcSU0H0YauQ9O0h7xXiTwecZfIHcguAUfD0Xwgg2UIDW8PbeEpAI4ZhH0QNplkI5EPJgT4FYiBsxeb8U0eNQpuJvZgJ0h8iVUb84jU6m/NGVwpTm5J/PnzAigAYdaKohwM1vY2mBDRj5OiDqIHFyNkWEQKTPhhD5UJQQU+JEL/2p0AQxE319p3CXZsNYpeZ8s4VexSsZfv2wHSjCpcyGluPrrSlVll/PvyRWRIAQK1EQEZ3IEUqoBqQClfRtEAkdP7IxSflihFmQBDIbKbJdBqspKyvXPQ58SuHmkHyB6wKAEDKeFZFgAA/rXlK8lhKNUAJbjaEQBguP5JKhIwoMywI5law1Yjw7UeYAuJXbJKnB8qhSrwWipzvF5llATAyyXMBIRGAZIt8+GlNJcHqVQ2INqpMCFh4TiAOWew+aEiOuSCYm8xKzPCqi1Lu/H4oqnRJwcF6D5RDVZX1S6Hsn7yCBAAgFQGAxWpA/imBbKQCqgHIgpzELF0IjIPaeU5gDGcFSSeSQrS5INi7VlPCtx+pmmxfHLLscn86aC3Jv+CSf9wKFABAOc8FQB2hLtUAJchog+qEAKxND4Qh7aDpRFOIKLrH5qJg+s1o7CyXMe1HmxO/UbiShWGV5A8ULgCA4p8LMECoSzVACbJGQsBiVSACawVNN6Ji1LkomC4zHivLZ8pZIn1x2CrK/emg00X+xZ/IEgTAyGr0XEBGwPpVA5KNKhECkcB2e1C+GIhH1Yguuy/nwqA8yzU2VsSQhUWeE38WqF3iN3YSe1ZE/kCpAgAo57mAEJhhlxQ2mcVS6UsmZM2EgOWqQATaGqJpdM1epN2zc3Ggb1bGwOrIvtgeVEv64tD1I/7MrbXL+vPH0LGSBQBQ/HMBBgjG1QCDWDn7I2tUmRCIBLffC4OifQFmYR5A5Z7eSyKhAiIoy4rtxZz4VYHrlPUnvGtA/kAlAgBQfS5A0CJ/jCyXlGqAHK0g+p1WIVBAVSARwjqyrhUwD2By79dBNJRORvUg+rDNOumLw08h8Ss1yO2Q7l1hyT9uFQmAkRU9JWCAkMGgpU8LKMPWSAiEO1CSGLCPbmpZN3JB18ZMWf13qvge1oP0xV2oJ/Fnbp1n/QmrWAAA5U0JQA+lbtMCaoETjSoXAuFOFCgGImEKi2DD9vpcQA0YzcDK6XV9SB+oG/Gng9eN+BMINSR/oBYCAChnSiAGaMGlkmkBZegaCgGgNDEQCVVolCLMdFAocw9rwlIFWXl7Vy/SB2aI+JUaWHOSe9eo5B+3mgiAkZU1JRACtOBSybSAMnR01rw2QgAoVQxEwhUeqSqrx6AyjVbukasf6QPx7ih2riLiz9w6z/qVrGYCAFCdEoC4Vf44hi5q0wLpLYxNGXrSMCoLamAli4FIyJDV4ljMrVCrZvidBtIXr1F0tGg5iF+pgTUnufcUkD9QSwEApE4JhFbXtRqQjlgvIVBwj8xMKAaAMno4FwWzZdUNt1z4Z11sTvy5nOQIU0L8I6upABiZWjUg/wBtWA2A3C0b0U7PpdBQgRdPDyi5lmXxu6uk6oC0CyGrzTGaW02G13pm+SMzJn3NpmZWdrk/l6MYYcrIH6i9AADKnRIwQKnrtIA2fM2rAiOrsDog7UrManfMZsjqNZTWO8sf2cwSv1IDa07pCFNI/sBUCACgvCkBQ5Q6TwuE4ZVCiIWAkmvZJq0OAFX2NuuWr91xrJHVe7icDsIHZN2rQ5lfLUgxxJ/LUYwwpcQ/sv8fBbiaDy1c2FUAAAAASUVORK5CYII="

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
    "id": "/",
    "lang": "sv",
    "categories": ["finance"],
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
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data && e.data.text() }; }
  const title = d.title || 'GRABIT';
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || '',
    tag: d.tag || 'grabit',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: { url: d.url || '/' },
    vibrate: [100, 40, 100],
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(ws => {
    for (const w of ws) { if ('focus' in w) { w.navigate(url); return w.focus(); } }
    return clients.openWindow(url);
  }));
});
"""

@app.get("/sw.js")
def _sw():
    return Response(content=_SW_JS, media_type="application/javascript",
                   headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


# ---- Google Play (TWA): Digital Asset Links ------------------------
# Kopplar Android-appen till webbappen så den kör i fullskärm utan
# webbläsarram. Fyll i env-varsen i Render när Play-paketet är skapat:
#   ANDROID_PACKAGE_NAME   t.ex. com.hekab.grabit
#   ANDROID_CERT_SHA256    SHA-256 från Play Console -> App signing
@app.get("/.well-known/assetlinks.json")
def assetlinks():
    import json as _j
    pkg = os.environ.get("ANDROID_PACKAGE_NAME", "").strip()
    sha = os.environ.get("ANDROID_CERT_SHA256", "").strip()
    if not (pkg and sha):
        return Response(content="[]", media_type="application/json")
    data = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app",
                   "package_name": pkg,
                   "sha256_cert_fingerprints": [f.strip() for f in sha.split(",") if f.strip()]},
    }]
    return Response(content=_j.dumps(data), media_type="application/json")


_PRIVACY_HTML = """<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GRABIT — Integritetspolicy</title>
<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0A0E12;color:#e8edf5;
max-width:680px;margin:0 auto;padding:28px 20px;line-height:1.6}
h1{font-size:22px;color:#F5C542}h2{font-size:16px;margin-top:26px;color:#F5C542}
p,li{font-size:14.5px;color:#c7d0dc}a{color:#F5C542}</style></head><body>
<h1>Integritetspolicy för GRABIT</h1>
<p>Senast uppdaterad: juli 2026. GRABIT är en app för teknisk aktieanalys.
Vi samlar in så lite data som möjligt och säljer aldrig data vidare.</p>
<h2>Vilka uppgifter behandlas?</h2>
<ul>
<li><b>Push-prenumeration</b> — om du slår på notiser sparas din webbläsares
push-adress (en teknisk identifierare, ingen personlig information) på vår
server så att vi kan skicka notiserna du bett om.</li>
<li><b>Bevakade aktier</b> — tickersymbolerna i din portfölj/watchlist sparas
tillsammans med push-prenumerationen så att larmen kan riktas rätt, samt dina
egna prislarm (ticker och nivå).</li>
<li><b>Lokalt i din enhet</b> — portföljens innehav sparas i din webbläsares
lokala lagring och lämnar inte enheten i annat syfte än ovan.</li>
</ul>
<p>Vi använder inga annonsnätverk, ingen spårning över andra webbplatser och
inga tredjeparts-analysverktyg. Inga konton, inga personuppgifter som namn,
e-post eller telefonnummer samlas in.</p>
<h2>Delas något med tredje part?</h2>
<p>Nej. Kursdata och nyheter hämtas från externa källor (t.ex. Yahoo Finance)
av vår server — dina uppgifter skickas inte dit. Push-notiser levereras via din
webbläsares pushtjänst (t.ex. Google FCM), vilket är tekniskt nödvändigt för
funktionen.</p>
<h2>Radering</h2>
<p>Stäng av notiserna i appen (klockan) så raderas din push-prenumeration,
bevakade tickers och prislarm från servern. Rensa webbläsardata för att ta bort
den lokala portföljen.</p>
<h2>Viktigt om innehållet</h2>
<p>GRABIT tillhandahåller teknisk analys och information — ingen finansiell
rådgivning. Handel med värdepapper innebär risk och kan leda till förluster.</p>
<h2>Kontakt</h2>
<p>Frågor om integritet: <a href="mailto:info@hekab.nu">info@hekab.nu</a></p>
</body></html>"""


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return HTMLResponse(content=_PRIVACY_HTML)


# ---- Push-notiser (Web Push / VAPID) --------------------------------
@app.get("/api/push/pubkey")
def push_pubkey():
    import push_notify as PN
    return {"key": PN.VAPID_PUBLIC}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    import push_notify as PN
    sub = await request.json()
    tickers = None
    if isinstance(sub, dict):
        tickers = sub.pop("tickers", None)
    n = PN.add_subscription(sub, tickers)
    return {"ok": True, "prenumeranter": n}


@app.post("/api/push/watchlist")
async def push_watchlist(request: Request):
    """Synkar vilka tickers en prenumerant bevakar (= portfoljen i appen)."""
    import push_notify as PN
    body = await request.json() or {}
    n = PN.set_tickers((body or {}).get("endpoint", ""), (body or {}).get("tickers") or [])
    return {"ok": True, "bevakade": n}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    import push_notify as PN
    body = await request.json()
    n = PN.remove_subscription((body or {}).get("endpoint", ""))
    return {"ok": True, "prenumeranter": n}


@app.get("/api/push/test")
def push_test():
    """Skickar en testnotis till alla prenumeranter. Öppna i mobilen efter aktivering."""
    import push_notify as PN
    res = PN.send_all("GRABIT \U0001F514", "Testnotis — push-röret funkar!", url="/")
    res["prenumeranter"] = PN.sub_count()
    return res


@app.get("/api/push/status")
def push_status():
    import push_notify as PN
    pub = PN.VAPID_PUBLIC
    ok_format = False
    try:
        import base64 as _b
        raw = _b.urlsafe_b64decode(pub + "=" * (-len(pub) % 4))
        ok_format = (len(raw) == 65 and raw[0] == 4)
    except Exception:
        pass
    return {"prenumeranter": PN.sub_count(),
            "vapid_konfigurerad": bool(pub and PN.VAPID_PRIVATE),
            "pubkey_format_ok": ok_format,   # True = 65 bytes okomprimerad P-256
            "pubkey_langd": len(pub),         # ska vara 87
            "pubkey_borjan": pub[:10],
            "bevakade_tickers": PN.all_watch_tickers(),
            "lagring": PN.SUBS_FILE}




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


# ----- GLOBAL TICKER-SÖKNING (namn -> symbol via Yahoo) --------------
# Gör att sökningen hittar bolag som INTE ligger i universumet,
# t.ex. "Corning" -> GLW. Frontend visar träffarna under "Globalt"
# och öppnar dem via /api/stock/{ticker} som redan klarar valfri symbol.
import requests as _rq

@cached(3600)
def _yahoo_lookup(q: str) -> list:
    try:
        r = _rq.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": q, "quotesCount": 8, "newsCount": 0,
                    "listsCount": 0, "enableFuzzyQuery": "true"},
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0 Safari/537.36"},
            timeout=8,
        )
        r.raise_for_status()
        quotes = (r.json() or {}).get("quotes") or []
    except Exception as e:
        print(f"[lookup] yahoo-sök fel för '{q}': {e}")
        return []
    out = []
    for it in quotes:
        if it.get("quoteType") not in ("EQUITY", "ETF"):
            continue
        sym = (it.get("symbol") or "").upper()
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": it.get("shortname") or it.get("longname") or sym,
            "exch": it.get("exchDisp") or it.get("exchange") or "",
            "type": it.get("quoteType"),
        })
    return out[:8]


@app.get("/api/lookup")
def lookup(q: str = Query(..., min_length=1, max_length=40)):
    """Sök bolag globalt på namn eller symbol. Cache 1h per fråga."""
    q = q.strip()
    if not q:
        return {"results": []}
    return {"results": _yahoo_lookup(q.lower())}


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
        for _forsok in range(2):                      # 2 försök per host
            try:
                r = requests.get(url, headers=_MACRO_HEADERS, timeout=12)
                if r.status_code != 200:
                    errs.append("%s -> HTTP %s" % (url.split("//")[1].split("/")[0], r.status_code))
                    break                              # samma svar lär komma igen
                raw = r.json()
                _MACRO_LAST_GOOD["src"] = url
                break
            except Exception as e:
                errs.append("%s -> %s" % (url.split("//")[1].split("/")[0], type(e).__name__))
                time.sleep(1.5)
        if raw is not None:
            break
    _MACRO_LAST_GOOD["err"] = "; ".join(errs)
    if raw is None:
        # ForexFactory blockerad -> Finnhub economic calendar som backup
        fh_key = os.getenv("FINNHUB_API_KEY", "")
        if fh_key:
            try:
                today = _dt.date.today()
                r = requests.get("https://finnhub.io/api/v1/calendar/economic",
                                 params={"from": today.isoformat(),
                                         "to": (today + _dt.timedelta(days=7)).isoformat(),
                                         "token": fh_key}, timeout=10)
                fh = (r.json() or {}).get("economicCalendar") or []
                raw = [{"title": x.get("event", ""), "country": x.get("country", ""),
                        "date": (x.get("time") or "").replace(" ", "T"),
                        "impact": {"3": "High", "2": "Medium"}.get(str(x.get("impact", "")), "Low"),
                        "forecast": x.get("estimate", ""), "previous": x.get("prev", "")}
                       for x in fh]
                _MACRO_LAST_GOOD["src"] = "finnhub"
            except Exception as e:
                _MACRO_LAST_GOOD["err"] += "; finnhub -> " + type(e).__name__
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


_FDA_ORD = ("fda", "pdufa", "crl", "approval", "approved", "clearance", "510(k)",
            "advisory committee", "adcomm", "breakthrough therapy", "fast track",
            "phase 3", "phase iii", "topline", "nda ", " bla ", "ind ")

@cached(1800)
def _fda_news():
    """FDA-relaterade nyheter for Bio-universumet via Alpaca News. Tyst [] vid fel."""
    import os as _os
    k = _os.getenv("APCA_API_KEY_ID", "") or _os.getenv("ALPACA_KEY", "")
    s = _os.getenv("APCA_API_SECRET_KEY", "") or _os.getenv("ALPACA_SECRET", "")
    bio = UNIVERSE.get("Bio", [])
    if not (k and s and bio):
        return []
    try:
        import requests as _rq
        r = _rq.get("https://data.alpaca.markets/v1beta1/news",
                    headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s},
                    params={"symbols": ",".join(bio[:40]), "limit": 50, "sort": "desc"},
                    timeout=10)
        out = []
        for n in (r.json().get("news") or []):
            h = (n.get("headline") or "").strip()
            hl = " " + h.lower() + " "
            if not h or not any(w in hl for w in _FDA_ORD):
                continue
            syms = [x for x in (n.get("symbols") or []) if x in bio]
            out.append({"rubrik": h, "ticker": (syms[0] if syms else ""),
                        "tid": (n.get("created_at") or "")[:16].replace("T", " "),
                        "url": n.get("url") or ""})
            if len(out) >= 8:
                break
        return out
    except Exception:
        return []


@app.get("/api/fda")
def fda():
    return {"news": _fda_news()}


@app.get("/api/macro")
def macro():
    ev = _macro_events()
    out = {"events": ev}
    if not ev and _MACRO_LAST_GOOD.get("err"):
        out["fel"] = _MACRO_LAST_GOOD["err"]
    return out


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
        hist = []
        try:
            df, _ = fetch(tk)
            if df is not None:
                hist = [round(float(x), 4) for x in df["Close"].dropna().iloc[-15:]]
        except Exception:
            pass
        out.append({"name": name, "tk": tk, "price": price,
                    "priceStr": _fmt_idx_price(price), "pct": pct, "hist": hist})
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

AI_MODEL = os.environ.get("GRABIT_AI_MODEL", "claude-haiku-4-5")        # masstexter: setups, nyheter, bolagsinfo, dagens läge
AI_MODEL_SMART = os.environ.get("GRABIT_AI_MODEL_SMART", "claude-sonnet-4-6")  # Fråga Grabit: få anrop, ska resonera vasst
# Fable/Opus tänker alltid innan svaret och tankarna räknas in i max_tokens ->
# ge rejält med utrymme så svaren aldrig klipps av.
_SMART_MAXTOK = 6000 if ("fable" in AI_MODEL_SMART or "opus" in AI_MODEL_SMART) else 1500


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
                model=AI_MODEL_SMART,
                max_tokens=_SMART_MAXTOK,
                system=sys_live,
                messages=msgs,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
            )
        except Exception:
            # SDK/modell stödjer kanske inte web_search -> kör utan, så AI:n aldrig dör
            resp = client.messages.create(
                model=AI_MODEL_SMART,
                max_tokens=_SMART_MAXTOK,
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
    # Fallback: AI:n svarade inte (nyckel saknas/timeout) -> bygg en saklig
    # svensk beskrivning av verifierad Finnhub-data i stället för tom sträng.
    # Då slipper frontenden falla tillbaka på sin generiska malltext.
    if not summary and (prof.get("name") or prof.get("industry")):
        land_map = {"US": "USA", "SE": "Sverige", "DE": "Tyskland", "FR": "Frankrike",
                    "CA": "Kanada", "GB": "Storbritannien", "NL": "Nederländerna",
                    "FI": "Finland", "NO": "Norge", "DK": "Danmark", "JP": "Japan",
                    "CN": "Kina", "TW": "Taiwan", "AU": "Australien", "CH": "Schweiz"}
        land = land_map.get(prof.get("country", ""), prof.get("country", ""))
        parts = [name]
        if prof.get("industry"):
            parts.append("är verksamt inom " + prof["industry"])
        if land:
            parts.append("med säte i " + land)
        summary = " ".join(parts) + "."
        if prof.get("weburl"):
            summary += " Webb: " + prof["weburl"].replace("https://", "").replace("http://", "").rstrip("/") + "."
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
_AI_CACHE_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "ai_text_cache.json")


def _ai_cache_load():
    """Las senast sparade AI-texter fran disk sa de overlever omstart."""
    import json as _j
    try:
        with open(_AI_CACHE_FILE) as f:
            _AI_TEXT_CACHE.update(_j.load(f))
    except Exception:
        pass


def _ai_cache_save():
    import json as _j
    try:
        keep = dict(list(_AI_TEXT_CACHE.items())[-400:])
        tmp = _AI_CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            _j.dump(keep, f)
        os.replace(tmp, _AI_CACHE_FILE)
    except Exception:
        pass


_ai_cache_load()


_AI_LAST_ERR = {"err": ""}


@app.get("/api/ai/debug", include_in_schema=False)
def ai_debug(ticker: str = "GLW"):
    """Diagnos för AI-kedjan: nyckel, modell, testanrop, scan.
    Öppna i mobilen: /api/ai/debug  (valfritt ?ticker=XXX)"""
    out = {}
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    out["key_finns"] = bool(key)
    out["key_ser_ut_som"] = (key[:10] + "…" + key[-4:]) if len(key) > 16 else ("(för kort: %d tecken)" % len(key))
    out["model"] = AI_MODEL
    out["model_smart"] = AI_MODEL_SMART
    try:
        import anthropic
        out["anthropic_sdk"] = getattr(anthropic, "__version__", "?")
    except Exception as e:
        out["anthropic_sdk"] = f"IMPORT-FEL: {e}"
        return out
    # 1) Skarpt minianrop mot Anthropic
    try:
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(model=AI_MODEL, max_tokens=20,
                                   messages=[{"role": "user", "content": "Svara bara: OK"}])
        out["testanrop"] = "".join(getattr(b, "text", "") for b in r.content).strip() or "(tomt svar)"
    except Exception as e:
        out["testanrop"] = f"FEL {type(e).__name__}: {e}"
    # 2) Kan servern ens scanna tickern? (ai_setup kräver detta FÖRE AI-anropet)
    tk = ticker.upper().strip()
    try:
        a = scan(tk)
        out["scan_" + tk] = ("OK, kurs %s" % a.get("last")) if a else "MISSLYCKADES (yfinance gav inget -> ai_setup returnerar tomt utan att ens fråga AI:n)"
    except Exception as e:
        out["scan_" + tk] = f"FEL: {e}"
    # 3) Alpaca-nycklar (krävs för ai_news)
    out["alpaca_nycklar_for_ai_news"] = bool(os.environ.get("APCA_API_KEY_ID")) and bool(os.environ.get("APCA_API_SECRET_KEY"))
    out["senaste_ai_fel"] = _AI_LAST_ERR.get("err") or "(inget registrerat)"
    return out


@app.get("/api/robber/exec", include_in_schema=False)
def robber_exec_status():
    """Status för exekveringen: läge, spärrar, konto, positioner, öppna ordrar."""
    from nasdaq_robber import Config as RC, _alp_get, _alp_base, _EXEC
    out = {
        "mode": RC.EXECUTE,
        "ticker": RC.EXEC_TICKER,
        "min_conf": RC.EXEC_MIN_CONF,
        "max_pos_usd": RC.MAX_POS_USD,
        "dagsforlustgrans_pct": RC.DAILY_LOSS_LIMIT_PCT,
        "pausad_idag": _EXEC.get("halted", False),
        "endpoint": _alp_base(),
        "nycklar_finns": bool(RC.APCA_KEY and RC.APCA_SECRET),
    }
    if RC.EXECUTE in ("paper", "live") and out["nycklar_finns"]:
        try:
            a = _alp_get("/v2/account")
            out["equity"] = a.get("equity")
            out["gardagens_equity"] = a.get("last_equity")
            out["kop_kraft"] = a.get("buying_power")
            out["positioner"] = [
                {"symbol": p.get("symbol"), "antal": p.get("qty"),
                 "snitt": p.get("avg_entry_price"), "pl_usd": p.get("unrealized_pl")}
                for p in _alp_get("/v2/positions")]
            out["oppna_ordrar"] = [
                {"symbol": o.get("symbol"), "side": o.get("side"),
                 "typ": o.get("type"), "antal": o.get("qty"), "status": o.get("status")}
                for o in _alp_get("/v2/orders?status=open")]
        except Exception as e:
            out["fel"] = str(e)[:200]
    return out


def _ai_text(cache_key: str, system: str, user: str, max_tokens: int = 220) -> str:
    """Generisk cachad Claude-text. Tom sträng om nyckel saknas/fel."""
    if cache_key in _AI_TEXT_CACHE:
        return _AI_TEXT_CACHE[cache_key]
    client = _anthropic_client()
    if client is None:
        _AI_LAST_ERR["err"] = "ANTHROPIC_API_KEY saknas eller klienten kunde inte skapas"
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
            _AI_LAST_ERR["err"] = ""
            _ai_cache_save()
        return text
    except Exception as e:
        _AI_LAST_ERR["err"] = f"{type(e).__name__}: {e}"
        print("AI-fel:", _AI_LAST_ERR["err"])
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
    # Dagens makrohändelser = "vad kan röra marknaden idag"
    try:
        ev = _macro_events()[:4]
        ev_line = "; ".join("%s %s %s (%s)" % (e.get("date",""), e.get("time",""),
                            e.get("title",""), e.get("impact","")) for e in ev)
    except Exception:
        ev_line = ""
    sysp = ("Du är Grabit, svensk marknadsanalytiker. Skriv 'Dagens läge' i exakt detta format: "
            "Börja med ETT av orden BULLISH, BEARISH eller NEUTRAL i versaler, följt av ' — ' "
            "och en mening som motiverar riktningen utifrån indexrörelserna. "
            "Sedan en mening om vad som sticker ut bland de hetaste aktierna (nämn 1-2 tickers). "
            "Avsluta med en mening om vad som kan röra marknaden framöver, baserat på "
            "makrohändelserna om sådana finns, annars på det allmänna läget (helgdag, tunn volym osv). "
            "Max 4 meningar totalt. Vardaglig men skarp svenska. Ingen rådgivning, "
            "hitta aldrig på siffror utöver de givna.")
    txt = _ai_text(key, sysp,
                   "INDEX: %s\nHETAST: %s\nMAKRO KOMMANDE: %s\nIdag är det %s.\nSkriv 'Dagens läge'."
                   % (idx_line, hot_line, ev_line or "(inga större händelser)",
                      _dt.date.today().strftime("%A %d %B")), 300)
    if not txt:
        # AI:n nere/nyckel saknas -> visa senaste lyckade "Dagens läge" i stället för tomt
        stale = [v for k2, v in _AI_TEXT_CACHE.items() if k2.startswith("daily:")]
        if stale:
            txt = stale[-1]
    return {"text": txt, "indices": idx_line, "hot": hot_line, "makro": ev_line}


@app.get("/api/ai_news/{ticker}")
def ai_news(ticker: str):
    """Svensk sammanfattning + stämpel av bolagets nyheter (kräver Alpaca-nycklar)."""
    tk = ticker.upper().strip()
    heads = []
    try:
        import os as _os
        import requests
        k = _os.getenv("APCA_API_KEY_ID", "") or _os.getenv("ALPACA_KEY", "")
        s = _os.getenv("APCA_API_SECRET_KEY", "") or _os.getenv("ALPACA_SECRET", "")
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
            "ret_1": round(float(a.get("ret_1") or 0), 1),   # dagens %
            "ret_5": round(float(a.get("ret_5") or 0), 1),   # 5 dagar
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
    out = {"text": txt, "positions": positions, "total": round(total, 2)}
    if not txt:
        out["error"] = _AI_LAST_ERR.get("err") or "AI-svaret blev tomt"
    return out


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
        ret1 = a.get("ret_1") or 0          # dagens % — det som visas vid kursen
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
                     "chg": round(float(ret1 or 0), 1),      # dagens %, inte 5d
                     "chg5": round(float(ret5 or 0), 1)})

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
                             "chg": round(float(a.get("ret_1") or 0), 1),
                             "chg5": round(float(a.get("ret_5") or 0), 1)})

    # --- Nyheter (Alpaca) ---
    if _os.getenv("APCA_API_KEY_ID") and _os.getenv("APCA_API_SECRET_KEY"):
        heads = _alpaca_news_latest(tops)
        for tk in tops:
            if tk in heads:
                a = by_tk.get(tk, {})
                feed.append({"tkr": tk, "i": "bell", "c": CYAN, "time": "Nyhet",
                             "h": "Ny nyhet", "detalj": heads[tk][:140],
                             "score": a.get("score10") or 0, "kurs": "$" + _num(a.get("last")),
                             "chg": round(float(a.get("ret_1") or 0), 1),
                             "chg5": round(float(a.get("ret_5") or 0), 1)})

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


# ---- Watchlist-push -------------------------------------------------
# Skannar aktierna i anvandarnas portfoljer under USA:s handelstid och
# pushar nar nagot viktigt hander: stor dagsrorelse eller starkt
# setup-lage. Dedupliceras per dag sa samma larm inte skickas tva ganger.
_WATCH_STATE_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "push_watch_state.json")
WATCH_MOVE_PCT = float(os.environ.get("GRABIT_WATCH_MOVE_PCT", "4"))   # larma vid +-4 % pa dagen
WATCH_SCORE_MIN = int(os.environ.get("GRABIT_WATCH_SCORE_MIN", "8"))   # larma vid setup-score >= 8


def _watch_state_load():
    import json as _j
    try:
        with open(_WATCH_STATE_FILE) as f:
            return _j.load(f)
    except Exception:
        return {}


def _watch_state_save(st):
    import json as _j
    try:
        tmp = _WATCH_STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            _j.dump(st, f)
        os.replace(tmp, _WATCH_STATE_FILE)
    except Exception:
        pass


def _us_market_open():
    import datetime as _dt2
    now = _dt2.datetime.utcnow()
    if now.weekday() >= 5:
        return False
    return 13 <= now.hour < 21   # tacker 9:30-16:00 ET bade sommar- och vintertid


def _watchlist_scan_once():
    import push_notify as PN
    import datetime as _dt2
    tickers = PN.all_watch_tickers()
    if not tickers:
        return
    st = _watch_state_load()
    today = _dt2.date.today().isoformat()
    for tk in tickers[:60]:
        try:
            a = scan(tk)
        except Exception:
            a = None
        if not a:
            continue
        ret1 = float(a.get("ret_1") or 0)
        score = int(a.get("score10") or 0)
        last = a.get("last")
        if abs(ret1) >= WATCH_MOVE_PCT:
            key = "%s:%s:move:%s" % (tk, today, "upp" if ret1 > 0 else "ner")
            if key not in st:
                st[key] = 1
                PN.send_watchlist(tk, "%s %+.1f%% idag" % (tk, ret1),
                                  "Kurs %s. En av dina bevakade aktier ror sig kraftigt." % (last,))
        if score >= WATCH_SCORE_MIN:
            key = "%s:%s:setup" % (tk, today)
            if key not in st:
                st[key] = 1
                PN.send_watchlist(tk, "%s \u2014 setup %d/10" % (tk, score),
                                  "%s har ett starkt tekniskt lage just nu%s." % (
                                      tk, (" (" + a.get("label") + ")") if a.get("label") else ""))
    st = {k: v for k, v in st.items() if (":%s:" % today) in k}
    _watch_state_save(st)


def _watchlist_loop():
    time.sleep(90)   # lat servern boota och cachen varmas forst
    while True:
        try:
            if _us_market_open():
                _watchlist_scan_once()
        except Exception as e:
            print("Watchlist-push fel:", e)
        time.sleep(600)   # var 10:e minut


@app.on_event("startup")
def _start_watchlist_push():
    _threading.Thread(target=_watchlist_loop, daemon=True).start()


# ---- Top Opportunity --------------------------------------------------------
# Dagens starkaste setup med konkreta nivåer (entry/stopp/targets) från
# levels-modulen. Rankas på setup-kvalitet + score + hetta.


def _risk_of(atr_pct):
    try:
        a = float(atr_pct or 0)
    except Exception:
        return "MEDEL"
    if a < 3:
        return "LÅG"
    if a <= 6:
        return "MEDEL"
    return "HÖG"


def _pct_of(level, last):
    try:
        return round((float(level) - float(last)) / float(last) * 100, 1)
    except Exception:
        return None


def _opp_of(r):
    """Bygger ett presentationsobjekt (score 0-100, nivåer, risk) av en scan-rad."""
    last = r.get("last")
    sc100 = int(r.get("setup_score") or 0)
    if sc100 <= 0:
        sc100 = int(round(float(r.get("score10") or 0) * 10))
    grade = str(r.get("setup_grade") or "C")
    prob = {"A": "HÖG", "B": "MEDEL"}.get(grade, "LÅG")
    up05 = _pct_of(r.get("atr_up_05"), last)
    up1 = _pct_of(r.get("atr_up_1"), last)
    stop = r.get("ob_support") or r.get("atr_dn_1")
    move = None
    if up05 is not None and up1 is not None:
        move = "+%s%% – +%s%%" % (up05, up1)
    return {
        "ticker": r.get("ticker"), "pris": last,
        "score": sc100, "grade": grade,
        "sannolikhet": prob, "risk": _risk_of(r.get("atr_pct")),
        "vantad_rorelse": move,
        "entry": round(float(last), 2) if last is not None else None,
        "stopp": round(float(stop), 2) if stop is not None else None,
        "target1": round(float(r.get("atr_up_05")), 2) if r.get("atr_up_05") is not None else None,
        "target2": round(float(r.get("atr_up_1")), 2) if r.get("atr_up_1") is not None else None,
        "label": r.get("label", ""), "hetta": r.get("hetta", 0),
        "score10": r.get("score10"), "levels_note": r.get("levels_note") or "",
    }


def _topop_rank(r):
    return ((r.get("setup_score") or 0) * 0.6
            + float(r.get("score10") or 0) * 4.0
            + float(r.get("hetta") or 0) * 0.2)


@app.get("/api/topop")
def topop():
    rows = [r for r in scan_universe(None)
            if str(r.get("label")) in ("BULL", "MOMENTUM", "VÄNDNING", "Rocketcase", "NEUTRAL/BYGGER")]
    if not rows:
        return {"pick": None}
    best = max(rows, key=_topop_rank)
    return {"pick": _opp_of(best)}


# ---- Facit / track record ---------------------------------------------------
# Loggar dagens picks varje börsdag och utvärderar dem efter ~en handelsvecka.
# Det är det här som visar att appen faktiskt levererar — öppet och ärligt.
_FACIT_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "facit.json")
_FACIT_EVAL_DAGAR = 7      # kalenderdagar innan utfallet mäts (~5 handelsdagar)


def _facit_load():
    import json as _j
    try:
        with open(_FACIT_FILE) as f:
            return _j.load(f)
    except Exception:
        return []


def _facit_save(rows):
    import json as _j
    try:
        tmp = _FACIT_FILE + ".tmp"
        with open(tmp, "w") as f:
            _j.dump(rows[-400:], f, ensure_ascii=False)
        os.replace(tmp, _FACIT_FILE)
    except Exception as e:
        print("Facit: kunde inte spara:", e)


def _facit_log_today():
    import datetime as _dt2
    today = _dt2.date.today()
    if today.weekday() >= 5:
        return
    rows = _facit_load()
    tid = today.isoformat()
    if any(e.get("datum") == tid for e in rows):
        return
    scanned = scan_universe(None)
    if not scanned:
        return
    by_score = sorted(scanned, key=lambda x: x.get("score10", 0), reverse=True)
    bull_like = [r for r in by_score if str(r.get("label")) in ("BULL", "MOMENTUM", "Rocketcase")]
    vand_like = [r for r in by_score if str(r.get("label")) == "VÄNDNING"]
    val = [("Top Opportunity", max(scanned, key=_topop_rank)),
           ("Dagens Bull", bull_like[0] if bull_like else None),
           ("Veckans Setup", bull_like[1] if len(bull_like) > 1 else None),
           ("Wildcard", vand_like[0] if vand_like else None)]
    seen = set()
    for roll, r in val:
        if not r or not r.get("last") or r.get("ticker") in seen:
            continue
        seen.add(r.get("ticker"))
        rows.append({"datum": tid, "roll": roll, "ticker": r["ticker"],
                     "pris": round(float(r["last"]), 2),
                     "score": int(r.get("setup_score") or 0) or int(round(float(r.get("score10") or 0) * 10)),
                     "label": r.get("label", "")})
    _facit_save(rows)
    print("Facit: loggade %d picks för %s" % (len(seen), tid))


def _facit_evaluate():
    """Mäter utfall för ~en handelsvecka gamla picks. Max 3 per pass (yfinance-snällt)."""
    import datetime as _dt2
    if yf is None:
        return
    rows = _facit_load()
    cutoff = (_dt2.date.today() - _dt2.timedelta(days=_FACIT_EVAL_DAGAR)).isoformat()
    todo = [e for e in rows if "utfall_pct" not in e and e.get("datum", "9999") <= cutoff][:3]
    if not todo:
        return
    for e in todo:
        try:
            h = yf.Ticker(e["ticker"]).history(start=e["datum"], auto_adjust=True)
            if h is None or h.empty:
                e["utfall_pct"] = 0.0
            else:
                closes = h["Close"].dropna()
                ref = closes.iloc[:6]        # ~5 handelsdagar efter loggning
                slut = float(ref.iloc[-1])
                e["utfall_pct"] = round((slut - float(e["pris"])) / float(e["pris"]) * 100, 2)
            e["traff"] = bool(e["utfall_pct"] > 0)
        except Exception as ex:
            print("Facit: kunde inte utvärdera %s: %s" % (e.get("ticker"), ex))
    _facit_save(rows)


@app.get("/api/facit")
def facit():
    rows = _facit_load()
    klara = [e for e in rows if "utfall_pct" in e]
    senaste = klara[-20:]
    n = len(senaste)
    if n:
        traffar = sum(1 for e in senaste if e.get("traff"))
        snitt = round(sum(e["utfall_pct"] for e in senaste) / n, 1)
        basta = max(senaste, key=lambda e: e["utfall_pct"])
        stats = {"antal": n, "traffar": traffar,
                 "traffprocent": round(traffar / n * 100),
                 "snitt_pct": snitt,
                 "basta": {"ticker": basta["ticker"], "pct": basta["utfall_pct"]}}
    else:
        stats = {"antal": 0}
    return {"stats": stats, "rader": list(reversed(senaste[-8:])),
            "loggade_totalt": len(rows)}


def _facit_loop():
    import datetime as _dt2
    time.sleep(180)   # låt warmup-cachen fyllas först
    while True:
        try:
            now = _dt2.datetime.utcnow()
            if now.weekday() < 5 and now.hour >= 14:   # efter USA-öppning
                _facit_log_today()
            _facit_evaluate()
        except Exception as e:
            print("Facit-fel:", e)
        time.sleep(1800)   # var 30:e minut


@app.on_event("startup")
def _start_facit():
    _threading.Thread(target=_facit_loop, daemon=True).start()


# ---- Egna prislarm -----------------------------------------------------------
# Användaren sätter "larma när TSLA går över 200" på aktiekortet. Larmet är
# kopplat till push-prenumerationen och skickas bara till den personen.
# Engångslarm: tas bort när det utlösts.
_PALERT_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "price_alerts.json")
_palert_lock = _threading.Lock()


def _palert_load():
    import json as _j
    try:
        with open(_PALERT_FILE) as f:
            return _j.load(f)
    except Exception:
        return []


def _palert_save(rows):
    import json as _j
    try:
        tmp = _PALERT_FILE + ".tmp"
        with open(tmp, "w") as f:
            _j.dump(rows[-500:], f)
        os.replace(tmp, _PALERT_FILE)
    except Exception as e:
        print("Prislarm: kunde inte spara:", e)


@app.post("/api/alerts")
async def alerts_add(request: Request):
    body = await request.json() or {}
    ep = str(body.get("endpoint") or "").strip()
    tk = str(body.get("ticker") or "").strip().upper()
    riktning = "over" if body.get("riktning") == "over" else "under"
    try:
        niva = float(body.get("niva"))
    except Exception:
        return {"ok": False, "fel": "ogiltig nivå"}
    if not ep or not tk or niva <= 0:
        return {"ok": False, "fel": "endpoint, ticker och nivå krävs"}
    with _palert_lock:
        rows = _palert_load()
        mina = [a for a in rows if a.get("endpoint") == ep]
        if len(mina) >= 30:
            return {"ok": False, "fel": "max 30 aktiva larm"}
        nid = (max((a.get("id", 0) for a in rows), default=0) + 1)
        rows.append({"id": nid, "endpoint": ep, "ticker": tk,
                     "riktning": riktning, "niva": round(niva, 4)})
        _palert_save(rows)
    return {"ok": True, "id": nid,
            "larm": [{k: a[k] for k in ("id", "ticker", "riktning", "niva")}
                     for a in _palert_load() if a.get("endpoint") == ep]}


@app.get("/api/alerts")
def alerts_list(endpoint: str = ""):
    return {"larm": [{k: a[k] for k in ("id", "ticker", "riktning", "niva")}
                     for a in _palert_load() if a.get("endpoint") == endpoint]}


@app.post("/api/alerts/remove")
async def alerts_remove(request: Request):
    body = await request.json() or {}
    ep = str(body.get("endpoint") or "")
    try:
        aid = int(body.get("id"))
    except Exception:
        return {"ok": False}
    with _palert_lock:
        rows = [a for a in _palert_load()
                if not (a.get("id") == aid and a.get("endpoint") == ep)]
        _palert_save(rows)
    return {"ok": True}


def _price_alerts_scan():
    import push_notify as PN
    alerts = _palert_load()
    if not alerts:
        return
    priser = {}
    for tk in {a["ticker"] for a in alerts}:
        try:
            a = scan(tk)
            if a and a.get("last") is not None:
                priser[tk] = float(a["last"])
        except Exception:
            pass
    utlosta = []
    for a in alerts:
        last = priser.get(a["ticker"])
        if last is None:
            continue
        traff = last >= a["niva"] if a["riktning"] == "over" else last <= a["niva"]
        if traff:
            jmf = "över" if a["riktning"] == "over" else "under"
            PN.send_to_endpoint(a["endpoint"],
                                "%s %s din larmnivå" % (a["ticker"], jmf),
                                "Kurs nu %s — din nivå var %s." % (round(last, 2), a["niva"]))
            utlosta.append(a["id"])
    if utlosta:
        with _palert_lock:
            rows = [a for a in _palert_load() if a.get("id") not in set(utlosta)]
            _palert_save(rows)


# ---- Morgonbrief + rapportvarningar -----------------------------------------
_DAILY_PUSH_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "daily_push_state.json")


def _daily_push_state():
    import json as _j
    try:
        with open(_DAILY_PUSH_FILE) as f:
            return _j.load(f)
    except Exception:
        return {}


def _daily_push_save(st):
    import json as _j
    try:
        tmp = _DAILY_PUSH_FILE + ".tmp"
        with open(tmp, "w") as f:
            _j.dump(st, f)
        os.replace(tmp, _DAILY_PUSH_FILE)
    except Exception:
        pass


def _morning_push_once():
    """Morgonbrief till alla prenumeranter strax före USA-öppning (en gång/dag)."""
    import push_notify as PN
    import datetime as _dt2
    if os.environ.get("GRABIT_MORNING_PUSH", "1") != "1":
        return
    now = _dt2.datetime.utcnow()
    if now.weekday() >= 5 or now.hour != 13:
        return
    st = _daily_push_state()
    today = _dt2.date.today().isoformat()
    if st.get("morgonbrief") == today:
        return
    if PN.sub_count() == 0:
        return
    try:
        j = ai_daily()
        text = (j.get("text") or j.get("indices") or "").strip()
    except Exception:
        text = ""
    kort = (text[:200] + "…") if len(text) > 200 else text
    try:
        p = topop().get("pick")
    except Exception:
        p = None
    if p:
        kort += "\nTop Opportunity: %s (%s)" % (p.get("ticker"), p.get("score"))
    if not kort.strip():
        return
    PN.send_all("GRABIT · Morgonbrief", kort, url="/", tag="grabit-morgon")
    st["morgonbrief"] = today
    _daily_push_save(st)


def _next_earnings_date(tk):
    """Nästa rapportdatum via yfinance. None om okänt."""
    import datetime as _dt2
    try:
        cal = yf.Ticker(tk).calendar
    except Exception:
        return None
    datum = None
    try:
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)) and ed:
                datum = ed[0]
            elif ed is not None:
                datum = ed
        elif cal is not None and hasattr(cal, "loc"):
            datum = cal.loc["Earnings Date"][0]
    except Exception:
        return None
    if datum is None:
        return None
    if hasattr(datum, "date") and not isinstance(datum, _dt2.date):
        datum = datum.date()
    if isinstance(datum, _dt2.datetime):
        datum = datum.date()
    return datum if isinstance(datum, _dt2.date) else None


def _earnings_alerts_once():
    """Varnar 3 dagar och 1 dag innan bevakade aktier rapporterar (en gång/dag)."""
    import push_notify as PN
    import datetime as _dt2
    now = _dt2.datetime.utcnow()
    if now.weekday() >= 5 or now.hour != 13:
        return
    st = _daily_push_state()
    today = _dt2.date.today()
    if st.get("rapportkoll") == today.isoformat():
        return
    st["rapportkoll"] = today.isoformat()
    _daily_push_save(st)
    skickade = st.setdefault("rapport_skickade", [])
    for tk in PN.all_watch_tickers()[:40]:
        try:
            ed = _next_earnings_date(tk)
        except Exception:
            ed = None
        if not ed:
            continue
        dgr = (ed - today).days
        if dgr not in (1, 3):
            continue
        key = "%s:%s:%s" % (tk, ed.isoformat(), dgr)
        if key in skickade:
            continue
        skickade.append(key)
        nar = "imorgon" if dgr == 1 else ("om %d dagar" % dgr)
        PN.send_watchlist(tk, "%s rapporterar %s" % (tk, nar),
                          "Rapportdatum %s. Rapporter kan ge stora rörelser — se över läget."
                          % ed.strftime("%d %b"))
    st["rapport_skickade"] = skickade[-200:]
    _daily_push_save(st)


def _daily_extra_loop():
    import datetime as _dt2
    time.sleep(240)   # låt cachen värmas först
    while True:
        try:
            _morning_push_once()
        except Exception as e:
            print("Morgonbrief-fel:", e)
        try:
            _earnings_alerts_once()
        except Exception as e:
            print("Rapportvarning-fel:", e)
        try:
            now = _dt2.datetime.utcnow()
            if now.weekday() < 5 and 7 <= now.hour < 21:   # täcker Sthlm + USA:s handelsdag
                _price_alerts_scan()
        except Exception as e:
            print("Prislarm-fel:", e)
        time.sleep(600)


@app.on_event("startup")
def _start_daily_extra():
    _threading.Thread(target=_daily_extra_loop, daemon=True).start()


# ---- Play Billing: logga köptoken -------------------------------------------
# Sparar purchase tokens från Play så prenumerationer kan verifieras mot
# Google Play Developer API i nästa steg (server-side receipt validation).
_BILLING_LOG = os.path.join(os.environ.get("DATA_DIR", "."), "billing_log.json")


@app.post("/api/billing/log")
async def billing_log(request: Request):
    import json as _j
    import datetime as _dt2
    body = await request.json() or {}
    try:
        try:
            with open(_BILLING_LOG) as f:
                rows = _j.load(f)
        except Exception:
            rows = []
        rows.append({"tid": _dt2.datetime.utcnow().isoformat(timespec="seconds"),
                     "sku": str(body.get("sku", ""))[:64],
                     "token": str(body.get("token", ""))[:512]})
        tmp = _BILLING_LOG + ".tmp"
        with open(tmp, "w") as f:
            _j.dump(rows[-1000:], f)
        os.replace(tmp, _BILLING_LOG)
    except Exception as e:
        print("Billing-logg fel:", e)
    return {"ok": True}
