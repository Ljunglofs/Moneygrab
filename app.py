"""
MONEYGRAB — Discovery Screener (v3)
===================================
Marknadsbred discovery: Nasdaq + NYSE + svenska marknaden, inkl. micro caps.

Flikar:
  🚀 READY TO FLY  — konsoliderar nära högsta, på väg att bryta ut (pre-breakout)
  🔥 BULL RUN      — redan i klättring, trend bekräftad
  🐻 SOON BEAR     — toppar ur / rullar över / pump-flaggor (var försiktig / blanka-case)
  📅 TRIGGERS      — katalysator-kalender (FDA/event) + earnings & nyheter per ticker
  📰 MACRO         — FinancialJuice live-flöde

Klassificering: pris vs EMA20/EMA50, EMA-lutning, RSI(14), range-kontraktion.
Bilder: lägg logo.png + bull_bear.jpg i repo-roten (bredvid app.py).
Lokalt: streamlit run app.py   ·   Cloud: pusha till GitHub + Streamlit Cloud
"""

import os
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
from yfinance import EquityQuery as EQ
import pandas as pd
import numpy as np
from datetime import datetime, timezone, date
import warnings
warnings.filterwarnings("ignore")

st.set_page_config(page_title="MoneyGrab", page_icon="💰",
                   layout="wide", initial_sidebar_state="expanded")

# ============================================================
# REDIGERA HÄR
# ============================================================
KNOWN = {
    "NVDA","QUBT","USAR","OUST","OKLO","NBIS","CRDO","HBM","SIVE","POET","LWLG",
    "IONQ","RGTI","XNDU","ONDS","UEC","NNE","SMR","RKLB","ASTS","RDW","ALAB",
    "MRVL","FCX","AG","PAAS","GAU","TGB","KRKNF","BZAI","HIMS","VST","NOW",
    "PLTR","AMZN","NVO","OBDU","HLIT","GPCR","SIVE.ST",
}

# Manuell katalysator-kalender: (datum 'YYYY-MM-DD', ticker/MACRO, event, typ)
CATALYSTS = [
    ("2026-06-01", "NVDA",  "Keynote Computex Taipei (Jensen Huang)", "Event"),
    ("2026-06-18", "MACRO", "FOMC räntebesked", "Macro"),
    # ("2026-07-15", "XYZ", "PDUFA-datum FDA-beslut", "FDA"),  # lägg in fler du bevakar
]

# Råvaror för Market Pulse-strippen + RÅVAROR-fliken (Yahoo futures + URA som uran-proxy)
COMMODITIES = {
    "Guld":        "GC=F",
    "Silver":      "SI=F",
    "Olja (Brent)":"BZ=F",
    "Koppar":      "HG=F",
    "Naturgas":    "NG=F",
    "Uran (URA)":  "URA",
}

# Index för Market Pulse-strippen
INDICES = {
    "S&P 500": "^GSPC",
    "Nasdaq":  "^IXIC",
    "OMXS30":  "^OMX",
}

# MACRO-fliken. Hämta din officiella kod på financialjuice.com/widgets/get-widget.aspx
# (välj Headlines = Dark) och klistra in <iframe>/<script> nedan om standard är tom.
FJ_HEADLINES_EMBED = (
    '<iframe frameborder="0" scrolling="yes" height="640" width="100%" '
    'src="https://www.financialjuice.com/widgets/latest-news?'
    'newsType=latest&backColor=0a0a0a&fontColor=e8e8e8&innerBackColor=141414&'
    'subColor=ffd60a&newsCount=40"></iframe>'
)

THEME_KEYWORDS = [
    ("Nuclear",    ["uranium","nuclear","atomic","reactor"]),
    ("Quantum",    ["quantum","qubit"]),
    ("Rare earth", ["rare earth","rare-earth","lithium","lanthan","neodym"]),
    ("Space",      ["space","satellite","orbital","launch","rocket","aerospace"]),
    ("Robotik",    ["robot","humanoid","automation","autonomous"]),
    ("Defense",    ["defense","defence","drone","missile","munition","tactical"]),
    ("AI infra",   ["semiconductor","chip","data center","datacenter","gpu",
                    "interconnect","photonic","optical","cloud comput"]),
    ("Biotech",    ["therapeut","pharma","biotech","bioscience","clinical","oncolog","gene"]),
    ("Energi",     ["energy","power","solar","battery","grid","hydrogen"]),
    ("Mining",     ["mining","gold","silver","copper","metals","resources"]),
]

# ============================================================
# CSS
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500;600&display=swap');
.stApp{background:#0a0a0a;}
.block-container{padding-top:1.0rem;max-width:1550px;}
h1.mg{font-family:'Bebas Neue',sans-serif;font-size:58px;letter-spacing:3px;color:#ffd60a;
 line-height:1;margin:0;padding-bottom:6px;border-bottom:3px solid #ffd60a;}
h1.mg span{color:#e8e8e8;}
.meta{color:#8a8a8a;font-family:'JetBrains Mono',monospace;font-size:12px;margin:8px 0 4px;}
.sec{font-family:'Bebas Neue',sans-serif;font-size:26px;letter-spacing:1.5px;color:#ffd60a;margin:8px 0 2px;}
.sub{color:#888;font-size:12px;margin-bottom:10px;}
section[data-testid="stSidebar"]{background:#111;}
.stButton>button{background:#ffd60a;color:#0a0a0a;font-family:'Bebas Neue',sans-serif;
 font-size:18px;letter-spacing:1px;border:0;}
div[data-testid="stDataFrame"]{background:#141414;border:1px solid #2a2a2a;border-radius:6px;}
div[data-testid="stMetric"]{background:#141414;border:1px solid #2a2a2a;border-radius:8px;
 padding:10px 12px;}
div[data-testid="stMetricLabel"]{color:#8a8a8a;font-size:12px;letter-spacing:.5px;}
div[data-testid="stMetricValue"]{font-family:'JetBrains Mono',monospace;color:#e8e8e8;}
.pulsebar{font-family:'JetBrains Mono',monospace;font-size:12px;color:#8a8a8a;
 margin:4px 0 10px;display:flex;gap:10px;align-items:center;}
.pill{font-family:'Bebas Neue',sans-serif;letter-spacing:1px;padding:2px 10px;border-radius:20px;font-size:14px;}
.pill.open{background:#103a1a;color:#3ad96b;border:1px solid #1d6b34;}
.pill.closed{background:#3a1010;color:#ff5b5b;border:1px solid #6b1d1d;}
img{border-radius:8px;}
</style>
""", unsafe_allow_html=True)

# ============================================================
# PARSNING
# ============================================================
def _f(d,*keys,default=np.nan):
    for k in keys:
        if k in d and d[k] is not None:
            try: return float(d[k])
            except (TypeError,ValueError): continue
    return default

def _name(d):
    for k in ("displayName","shortName","longName"):
        if d.get(k): return str(d[k])
    return d.get("symbol","?")

def _theme(name,sector,industry):
    text=" ".join(str(x).lower() for x in (name,sector,industry) if x)
    for label,kws in THEME_KEYWORDS:
        if any(kw in text for kw in kws): return label
    return sector if sector else "Övrigt"

def quotes_to_df(quotes,src=""):
    rows=[]
    for q in quotes:
        sym=q.get("symbol")
        if not sym: continue
        price=_f(q,"regularMarketPrice"); prev=_f(q,"regularMarketPreviousClose")
        chg=_f(q,"regularMarketChangePercent"); vol=_f(q,"regularMarketVolume")
        avg=_f(q,"averageDailyVolume3Month","averageDailyVolume10Day")
        mcap=_f(q,"marketCap"); hi=_f(q,"fiftyTwoWeekHigh"); lo=_f(q,"fiftyTwoWeekLow")
        sec=q.get("sector") or ""; ind=q.get("industry") or ""; cur=q.get("currency") or "USD"
        pct=(price/prev-1)*100 if (np.isfinite(price) and np.isfinite(prev) and prev>0) else chg
        pfh=(price/hi-1)*100 if (np.isfinite(price) and np.isfinite(hi) and hi>0) else np.nan
        pal=(price/lo-1)*100 if (np.isfinite(price) and np.isfinite(lo) and lo>0) else np.nan
        rv=vol/avg if (np.isfinite(vol) and np.isfinite(avg) and avg>0) else np.nan
        rows.append(dict(symbol=sym,name=_name(q),theme=_theme(_name(q),sec,ind),
            price=price,pct_today=pct,rel_vol=rv,mcap=mcap,pct_from_high=pfh,
            pct_above_low=pal,avg_vol=avg,currency=cur,source=src))
    df=pd.DataFrame(rows)
    if df.empty: return df
    df=df.drop_duplicates(subset="symbol",keep="first").reset_index(drop=True)
    df["tag"]=np.where(df["symbol"].isin(KNOWN),"KÄND","NY")
    return df

def base_flags(df):
    out=[]
    for _,r in df.iterrows():
        f=[]
        if np.isfinite(r["price"]) and r["currency"]=="USD" and r["price"]<2: f.append("PENNY")
        if np.isfinite(r["mcap"]) and r["mcap"]<50e6: f.append("MICRO")
        if np.isfinite(r["pct_today"]) and r["pct_today"]>35: f.append("PARABOL")
        if np.isfinite(r["avg_vol"]) and r["avg_vol"]<150_000: f.append("TUNN")
        if np.isfinite(r["pct_above_low"]) and r["pct_above_low"]>300: f.append("UTSTRÄCKT")
        out.append(" ".join(f))
    df=df.copy(); df["flags"]=out; return df

# ============================================================
# TREND / RSI
# ============================================================
def _ema(s,n): return s.ewm(span=n,adjust=False).mean()
def _rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean()
    dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+up/dn.replace(0,np.nan))

def trend_signals(close):
    close=close.dropna()
    if len(close)<30: return None
    e20,e50=_ema(close,20),_ema(close,50); price=close.iloc[-1]
    r10=(close[-10:].max()-close[-10:].min())/close[-10:].mean()
    r30=(close[-30:].max()-close[-30:].min())/close[-30:].mean()
    ret5=(price/close.iloc[-6]-1)*100 if len(close)>6 else np.nan
    return dict(rsi=float(_rsi(close).iloc[-1]),
        above20=bool(price>e20.iloc[-1]),above50=bool(price>e50.iloc[-1]),
        ema20_rising=bool(e20.iloc[-1]>e20.iloc[-4]),
        contracting=bool(r10<r30*0.6),ret5=float(ret5),
        spark=[round(float(x),3) for x in close[-30:].tolist()])

def classify(pct_today,pct_from_high,rel_vol,flags,e):
    r=e.get("rsi",np.nan) if e else np.nan
    if flags: return "BEAR"
    if e:
        if (not e["above20"]): return "BEAR"
        if e["above20"] and e["contracting"] and (45<=r<=68) and pct_today<8 \
           and -12<=(pct_from_high if pct_from_high is not None else -99)<=0:
            return "READY"
        if e["above20"] and e["above50"] and e["ema20_rising"] and e["ret5"]>0 and r>=50:
            return "BULL"
        return "READY" if r<55 else "BULL"
    if pct_today>=5 and (rel_vol or 0)>=1.5: return "BULL"
    if -10<=(pct_from_high if pct_from_high is not None else -99)<=-1: return "READY"
    return "BULL" if pct_today>0 else "BEAR"

def overbought(e): return bool(e and np.isfinite(e.get("rsi",np.nan)) and e["rsi"]>85)

# ============================================================
# DATAHÄMTNING
# ============================================================
@st.cache_data(ttl=1800,show_spinner=False)
def fetch_candidates(regions,min_pct,min_mcap_musd,price_min,price_max,presets):
    frames=[]; errors=[]
    for reg in regions:
        try:
            conds=[EQ("gt",["percentchange",float(min_pct)]),
                   EQ("gt",["intradaymarketcap",float(min_mcap_musd)*1e6]),
                   EQ("eq",["region",reg])]
            if price_min>0: conds.append(EQ("gt",["intradayprice",float(price_min)]))
            if price_max>0: conds.append(EQ("lt",["intradayprice",float(price_max)]))
            res=yf.screen(EQ("and",conds),size=100,sortField="percentchange",sortAsc=False)
            frames.append(quotes_to_df(res.get("quotes",[]),f"custom-{reg}"))
        except Exception as ex:
            errors.append(f"Custom {reg}: {ex}")
    for key in presets:
        try:
            res=yf.screen(key,size=100)
            frames.append(quotes_to_df(res.get("quotes",[]),key))
        except Exception as ex:
            errors.append(f"{key}: {ex}")
    dfs=[f for f in frames if isinstance(f,pd.DataFrame) and not f.empty]
    if not dfs: return pd.DataFrame(),errors
    df=pd.concat(dfs,ignore_index=True).drop_duplicates(subset="symbol",keep="first").reset_index(drop=True)
    return base_flags(df),errors

@st.cache_data(ttl=1800,show_spinner=False)
def enrich(symbols):
    out={}
    syms=[s for s in symbols if s]
    if not syms: return out
    try:
        data=yf.download(syms,period="4mo",interval="1d",group_by="ticker",
                         progress=False,threads=True,auto_adjust=True)
    except Exception:
        return out
    for s in syms:
        try:
            close=data[s]["Close"] if len(syms)>1 else data["Close"]
            sig=trend_signals(close)
            if sig: out[s]=sig
        except Exception:
            continue
    return out

@st.cache_data(ttl=3600,show_spinner=False)
def ticker_detail(sym):
    info={"earnings":None,"news":[]}
    try:
        cal=yf.Ticker(sym).calendar
        if isinstance(cal,dict):
            ed=cal.get("Earnings Date")
            if ed: info["earnings"]=str(ed[0] if isinstance(ed,(list,tuple)) else ed)
    except Exception: pass
    try:
        for n in (yf.Ticker(sym).news or [])[:6]:
            c=n.get("content",n)
            title=c.get("title") or n.get("title")
            prov=c.get("provider") if isinstance(c.get("provider"),dict) else None
            pub=prov.get("displayName") if prov else n.get("publisher")
            cu=c.get("canonicalUrl") if isinstance(c.get("canonicalUrl"),dict) else None
            url=cu.get("url") if cu else n.get("link")
            if title: info["news"].append((title,pub or "",url or ""))
    except Exception: pass
    return info

@st.cache_data(ttl=900,show_spinner=False)
def fetch_series(items):
    """items: tuple av (label, ticker). Funkar för både index och råvaror."""
    out=[]
    tks=[t for _,t in items]
    if not tks: return out
    try:
        data=yf.download(tks,period="3mo",interval="1d",group_by="ticker",
                         progress=False,threads=True,auto_adjust=True)
    except Exception:
        return out
    for label,tk in items:
        try:
            close=(data[tk]["Close"] if len(tks)>1 else data["Close"]).dropna()
            if len(close)<2: continue
            price=float(close.iloc[-1]); prev=float(close.iloc[-2])
            d1=(price/prev-1)*100
            d5=(price/float(close.iloc[-6])-1)*100 if len(close)>6 else np.nan
            sig=trend_signals(close)
            if sig and sig["above20"] and sig["above50"]:   status="BULL"
            elif sig and not sig["above20"]:                 status="BEAR"
            else:                                            status="NEUTRAL"
            out.append(dict(label=label,ticker=tk,price=price,d1=d1,d5=d5,status=status,
                            rsi=round(sig["rsi"],0) if sig else np.nan,
                            spark=sig["spark"] if sig else None))
        except Exception:
            continue
    return out

# ============================================================
# HEADER + HERO (bilder)
# ============================================================
if os.path.exists("logo.png"):
    st.image("logo.png", width=140)
else:
    st.markdown('<h1 class="mg">MONEY<span>GRAB</span></h1>', unsafe_allow_html=True)

if os.path.exists("bull_bear.jpg"):
    c1,c2,c3=st.columns([1,2,1])
    with c2: st.image("bull_bear.jpg", use_container_width=True)

# ---- MARKET PULSE (råvaru-strip i monitor-stil) ----
_now=datetime.now(timezone.utc)
_h=_now.hour + _now.minute/60          # US-börs öppen ca 13:30–20:00 UTC, vardag
_open=(_now.weekday()<5) and (13.5<=_h<20.0)
st.markdown(
    f'<div class="pulsebar"><span class="pill {"open" if _open else "closed"}">'
    f'{"MARKNAD ÖPPEN" if _open else "MARKNAD STÄNGD"}</span>'
    f'<span>MARKET PULSE · uppdaterad {_now.strftime("%H:%M UTC")}</span></div>',
    unsafe_allow_html=True)
_comms=fetch_series(tuple(COMMODITIES.items()))
_indices=fetch_series(tuple(INDICES.items()))
if _indices:
    st.markdown('<div class="pulsebar"><span>INDEX</span></div>',unsafe_allow_html=True)
    for col,c in zip(st.columns(len(_indices)),_indices):
        col.metric(c["label"], f'{c["price"]:,.0f}',
                   f'{c["d1"]:+.2f}%' if np.isfinite(c["d1"]) else None)
if _comms:
    st.markdown('<div class="pulsebar"><span>RÅVAROR</span></div>',unsafe_allow_html=True)
    for col,c in zip(st.columns(len(_comms)),_comms):
        col.metric(c["label"], f'{c["price"]:.2f}',
                   f'{c["d1"]:+.2f}%' if np.isfinite(c["d1"]) else None)

# ============================================================
# SIDOPANEL
# ============================================================
PRESETS={"day_gainers":"Dagens vinnare","small_cap_gainers":"Small-cap vinnare",
 "aggressive_small_caps":"Aggressiva small caps","most_actives":"Mest omsatta",
 "most_shorted_stocks":"Mest blankade","growth_technology_stocks":"Tech-tillväxt"}

with st.sidebar:
    st.markdown("### 🌍 Marknader")
    regions=[]
    if st.checkbox("USA (Nasdaq/NYSE)",value=True): regions.append("us")
    if st.checkbox("Sverige (Stockholm)",value=True): regions.append("se")

    st.markdown("### 🎯 Kriterier")
    micro=st.checkbox("Inkl. micro caps (snabba rörelser)",value=True)
    min_mcap=10 if micro else 300
    st.caption(f"Min market cap: {min_mcap} MUSD")
    min_pct=st.slider("Min % idag",0,30,4)
    cc1,cc2=st.columns(2)
    price_min=cc1.number_input("Pris min",0.0,value=(0.3 if micro else 1.0),step=0.5)
    price_max=cc2.number_input("Pris max (0=av)",0.0,value=0.0,step=10.0)

    st.markdown("### 📡 Färdiga screeners (US)")
    presets=[k for k,lbl in PRESETS.items()
             if st.checkbox(lbl,value=(k in ("day_gainers","small_cap_gainers")),key=k)]

    st.markdown("### 🔎 Filter")
    only_new=st.checkbox("Visa bara NYA",value=False)
    theme_filter=st.multiselect("Tema",[t[0] for t in THEME_KEYWORDS])
    if st.button("🔄 Tvinga refresh"):
        st.cache_data.clear(); st.rerun()

df,errors=fetch_candidates(tuple(regions),min_pct,min_mcap,price_min,price_max,tuple(presets))
ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

tabs=st.tabs(["🚀 READY TO FLY","🔥 BULL RUN","🐻 SOON BEAR","📅 TRIGGERS","📰 MACRO","🛢️ RÅVAROR"])

if df.empty:
    for t in tabs[:3]:
        with t:
            st.markdown(f'<div class="meta">Ingen data · {ts}</div>',unsafe_allow_html=True)
            for e in errors: st.warning(e)
            if not errors:
                st.info("Inga träffar. Sänk 'Min % idag', bocka i micro caps eller fler screeners. "
                        "På helger/efter stängning är data från senaste handelsdag.")
else:
    df["pre"]=(df["pct_today"].fillna(0).clip(-30,30)+
               df["rel_vol"].fillna(0).clip(0,8)*4+
               (20+df["pct_from_high"].fillna(-99)).clip(0,20)).round(1)
    top=df.sort_values("pre",ascending=False).head(45)
    sig=enrich(tuple(top["symbol"].tolist()))

    cls,ob,spk,rsis=[],[],[],[]
    for _,r in df.iterrows():
        e=sig.get(r["symbol"])
        cls.append(classify(r["pct_today"],r["pct_from_high"],r["rel_vol"],r["flags"],e))
        ob.append("⚠️ÖK" if overbought(e) else "")
        spk.append(e.get("spark") if e else None)
        rsis.append(round(e.get("rsi"),0) if e and np.isfinite(e.get("rsi",np.nan)) else np.nan)
    df["bucket"]=cls; df["ob"]=ob; df["spark"]=spk; df["rsi"]=rsis

    view=df.copy()
    if only_new: view=view[view["tag"]=="NY"]
    if theme_filter: view=view[view["theme"].isin(theme_filter)]

    n_new=int((view["tag"]=="NY").sum())
    meta=(f'{len(view)} träffar · <b>{n_new} nya</b> · marknader: {",".join(regions) or "–"} · '
          f'uppdaterad {ts} · cache 30 min')

    COLS=["symbol","name","tag","theme","spark","price","pct_today","rel_vol","rsi","pct_from_high","ob","flags"]
    CFG={"symbol":"Ticker","name":"Bolag","tag":"Tagg","theme":"Tema",
         "spark":st.column_config.LineChartColumn("30d",width="small"),
         "price":st.column_config.NumberColumn("Pris",format="%.2f"),
         "pct_today":st.column_config.NumberColumn("% idag",format="%.1f%%"),
         "rel_vol":st.column_config.NumberColumn("Rel.vol",format="%.1fx"),
         "rsi":st.column_config.NumberColumn("RSI",format="%.0f"),
         "pct_from_high":st.column_config.NumberColumn("vs 52v-högsta",format="%.0f%%"),
         "ob":"","flags":"Flaggor"}

    def show(d,title,sub):
        st.markdown(f'<div class="meta">{meta}</div>',unsafe_allow_html=True)
        for e in errors: st.warning(e)
        st.markdown(f'<div class="sec">{title}</div><div class="sub">{sub}</div>',unsafe_allow_html=True)
        if d.empty: st.caption("Inga träffar här just nu."); return
        st.dataframe(d[COLS],column_config=CFG,hide_index=True,use_container_width=True)

    with tabs[0]:
        show(view[view["bucket"]=="READY"].sort_values("pre",ascending=False).head(30),
             "🚀 READY TO FLY","Konsoliderar nära 52v-högsta, RSI neutral, range drar ihop sig. På väg att bryta ut.")
    with tabs[1]:
        show(view[view["bucket"]=="BULL"].sort_values("pre",ascending=False).head(30),
             "🔥 BULL RUN","Pris > EMA20 > EMA50, stigande, positiv momentum. Klättringen är redan igång.")
    with tabs[2]:
        show(view[view["bucket"]=="BEAR"].sort_values("pct_today",ascending=False).head(30),
             "🐻 SOON BEAR / WARNING","Under EMA20 (rullar över) · överköpt · parabol/penny/tunn. Försiktigt — eller blanka-case.")

# ---- TRIGGERS ----
with tabs[3]:
    st.markdown('<div class="sec">📅 Katalysatorer & event</div>'
                '<div class="sub">Kommande triggers du bevakar (redigeras i CATALYSTS i app.py).</div>',
                unsafe_allow_html=True)
    today=date.today()
    cat=pd.DataFrame(CATALYSTS,columns=["Datum","Ticker","Event","Typ"])
    if not cat.empty:
        cat["d"]=pd.to_datetime(cat["Datum"],errors="coerce")
        cat=cat[cat["d"].dt.date>=today].sort_values("d")
        cat["Dagar kvar"]=(cat["d"].dt.date-today).apply(lambda x:x.days)
        if cat.empty: st.caption("Inga kommande katalysatorer inlagda.")
        else: st.dataframe(cat[["Datum","Dagar kvar","Ticker","Event","Typ"]],hide_index=True,use_container_width=True)
    else:
        st.caption("Inga kommande katalysatorer inlagda.")
    st.markdown('<div class="sec">🔍 Earnings & nyheter per ticker</div>'
                '<div class="sub">Slå upp nästa rapportdatum och senaste rubriker för valfri aktie.</div>',
                unsafe_allow_html=True)
    q=st.text_input("Ticker (ex NVDA, OKLO, SIVE.ST)","").strip().upper()
    if q:
        d=ticker_detail(q)
        st.write(f"**Nästa earnings:** {d['earnings'] or 'okänt / hittades ej'}")
        if d["news"]:
            st.write("**Senaste rubriker:**")
            for title,pub,url in d["news"]:
                st.markdown(f"- [{title}]({url}) · *{pub}*" if url else f"- {title} · *{pub}*")
        else:
            st.caption("Inga nyheter hittades.")

# ---- MACRO ----
with tabs[4]:
    st.markdown('<div class="sec">📰 FinancialJuice — live macro-flöde</div>'
                '<div class="sub">Realtids marknadsrubriker. Tom ruta? Klistra in din egen embed från '
                'financialjuice.com/widgets/get-widget.aspx i FJ_HEADLINES_EMBED i app.py.</div>',
                unsafe_allow_html=True)
    try:
        components.html(FJ_HEADLINES_EMBED,height=660,scrolling=True)
    except Exception as e:
        st.warning(f"Kunde inte ladda widget: {e}")
    st.caption("Tips: lägg även in deras 'Economic Calendar'-widget här för makro-event (Fed, CPI, NFP).")

# ---- RÅVAROR ----
with tabs[5]:
    st.markdown('<div class="sec">🛢️ Råvaror</div>'
                '<div class="sub">Guld · Silver · Olja (Brent) · Koppar · Naturgas · Uran. '
                'Trend = pris vs EMA20/EMA50. Uran via URA-ETF som proxy.</div>',
                unsafe_allow_html=True)
    if _comms:
        cdf=pd.DataFrame(_comms)
        cdf=cdf.rename(columns={"label":"Råvara","ticker":"Ticker","price":"Pris",
                                "d1":"% 1d","d5":"% 5d","rsi":"RSI","status":"Trend","spark":"30d"})
        CCFG={"Råvara":"Råvara","Ticker":"Ticker",
              "30d":st.column_config.LineChartColumn("30d",width="small"),
              "Pris":st.column_config.NumberColumn("Pris",format="%.2f"),
              "% 1d":st.column_config.NumberColumn("% 1d",format="%.2f%%"),
              "% 5d":st.column_config.NumberColumn("% 5d",format="%.2f%%"),
              "RSI":st.column_config.NumberColumn("RSI",format="%.0f"),"Trend":"Trend"}
        st.dataframe(cdf[["Råvara","Ticker","30d","Pris","% 1d","% 5d","RSI","Trend"]],
                     column_config=CCFG,hide_index=True,use_container_width=True)
    else:
        st.info("Kunde inte hämta råvarudata just nu. Tryck '🔄 Tvinga refresh' eller försök strax igen.")

st.markdown("---")
st.caption("MONEYGRAB v3 · multi-marknad discovery · trend-klassificering (EMA/RSI) · "
           "ej finansiell rådgivning · data fördröjd, senaste handelsdag på helger")
