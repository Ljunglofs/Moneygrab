# =====================================================================
#  MONEYGRAB  —  huvudfil
#  Binder ihop dina moduler (sok_module, ai_module) med screener-
#  flikarna, loggan och temat. Återanvänder din analyze/fetch-motor
#  så hela appen rankar likadant överallt.
#  Inget av detta är finansiell rådgivning.
# =====================================================================

import os
import streamlit as st
import pandas as pd
import numpy as np

# Dina moduler -------------------------------------------------------
from sok_module import render_sok_tab, fetch, analyze
from ai_module import render_ai_tab

# ---------------------------------------------------------------------
#  SIDKONFIG
# ---------------------------------------------------------------------
st.set_page_config(
    page_title="MoneyGrab",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------
#  TEMA  —  blått, matchar loggan (#2b7fff på svart)
# ---------------------------------------------------------------------
ACCENT      = "#2b7fff"
ACCENT_SOFT = "#1e62cc"
NEUTRAL     = "#8a93a6"

st.markdown(f"""
<style>
.stApp {{ background:#000000; color:#e9edf5; }}
section[data-testid="stSidebar"] {{ background:#070a10; border-right:1px solid #141a26; }}
h1,h2,h3,h4 {{ color:#ffffff; letter-spacing:.4px; }}
hr {{ border-color:#141a26; }}

.stTabs [data-baseweb="tab-list"] {{ gap:4px; border-bottom:1px solid #141a26; flex-wrap:wrap; }}
.stTabs [data-baseweb="tab"] {{
    background:#0b0f17; color:#8a93a6; border-radius:8px 8px 0 0;
    padding:8px 14px; font-weight:600;
}}
.stTabs [aria-selected="true"] {{ background:{ACCENT}; color:#ffffff; }}

.stButton>button {{
    background:{ACCENT}; color:#fff; border:0; border-radius:8px; font-weight:700;
}}
.stButton>button:hover {{ background:{ACCENT_SOFT}; }}

.pill {{ display:inline-block; padding:3px 12px; border-radius:999px;
        font-size:.78rem; font-weight:800; letter-spacing:.5px; }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------
#  LOGGA
# ---------------------------------------------------------------------
LOGO_PATH = "logo.png"
lc1, lc2 = st.columns([1, 3])
with lc1:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, use_container_width=True)
    else:
        st.markdown(
            f"<h1 style='margin:0'>MONEY<span style='color:{ACCENT}'>GRAB</span></h1>",
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------
#  AKTIE-UNIVERSUM  (dina teman)
# ---------------------------------------------------------------------
UNIVERSE = {
    "AI-infra":     ["NVDA","NBIS","CRDO","ALAB","MRVL","AVGO","AMD","SMCI","VRT","DGXX"],
    "Photonics":    ["SIVE.ST","POET","LWLG"],
    "Quantum":      ["IONQ","QUBT","RGTI"],
    "Rare earth":   ["USAR","MP"],
    "Defense/Drone":["ONDS","KTOS","AVAV"],
    "Lidar/Phys.AI":["OUST","LAZR"],
    "Nuclear":      ["OKLO","NNE","SMR","UEC","UUUU"],
    "Space":        ["RKLB","ASTS","RDW"],
    "Koppar":       ["FCX","HBM"],
    "Silver/Guld":  ["AG","PAAS","GAU"],
}

# ---------------------------------------------------------------------
#  SIDOPANEL
# ---------------------------------------------------------------------
st.sidebar.markdown("### ⚙️ Inställningar")

WATCHLIST_DEFAULT = "NVDA, QUBT, USAR, OUST, OKLO, NBIS, CRDO, SIVE.ST, IONQ, RKLB, ASTS, ONDS"
wl_raw = st.sidebar.text_area("⭐ Min watchlist", WATCHLIST_DEFAULT, height=120)
watchlist = [t.strip().upper() for t in wl_raw.replace("\n", ",").split(",") if t.strip()]

theme_sel = st.sidebar.multiselect("Filtrera teman", list(UNIVERSE.keys()),
                                   default=list(UNIVERSE.keys()))
selected = sorted({t for k in theme_sel for t in UNIVERSE[k]})

if st.sidebar.button("🔄 Tvinga refresh av data"):
    st.cache_data.clear()
    st.rerun()

# ---------------------------------------------------------------------
#  HJÄLPARE  —  kör din analyze-motor på en ticker
# ---------------------------------------------------------------------
def scan(ticker: str):
    """Returnerar din analyze()-dict + ticker, eller None."""
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
    return a


def render_list(tickers, keep_labels=None, sort_desc=True):
    rows = []
    prog = st.progress(0.0)
    for i, t in enumerate(tickers):
        a = scan(t)
        prog.progress((i + 1) / max(1, len(tickers)))
        if a and (keep_labels is None or a["label"] in keep_labels):
            rows.append(a)
    prog.empty()
    if not rows:
        st.info("Inga namn matchar just nu.")
        return
    rows.sort(key=lambda x: x["score10"], reverse=sort_desc)
    for a in rows:
        c = st.columns([1.4, 0.9, 1.7, 1, 1, 1, 1])
        c[0].markdown(f"**{a['ticker']}**")
        c[1].markdown(
            f"<span style='color:{a['color']};font-weight:800'>{a['score10']}/10</span>",
            unsafe_allow_html=True)
        c[2].markdown(
            f"<span class='pill' style='background:{a['color']}22;color:{a['color']};"
            f"border:1px solid {a['color']}'>{a['emoji']} {a['label']}</span>",
            unsafe_allow_html=True)
        c[3].write(f"{a['last']:.2f}")
        c[4].write(f"RSI {a['rsi']:.0f}")
        c[5].write(f"{a['pct_from_high']:+.0f}%")
        c[6].write(f"{a['rel_vol']:.1f}x")

# ---------------------------------------------------------------------
#  MARKNADSLÄGE  (S&P 500 vs 200-dagars)
# ---------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def market_regime():
    try:
        df, _ = fetch("^GSPC")
    except Exception:
        return "BLANDAD", 0.0
    if df is None or len(df) < 200:
        return "BLANDAD", 0.0
    c = df["Close"].dropna()
    last = float(c.iloc[-1]); ma200 = float(c.rolling(200).mean().iloc[-1])
    ma50 = float(c.rolling(50).mean().iloc[-1])
    pct = (last / ma200 - 1) * 100
    if last > ma200 and ma50 > ma200:  return "BULL", pct
    if last < ma200:                   return "BEAR", pct
    return "BLANDAD", pct

mkt, mkt_pct = market_regime()
mcol = {"BULL":"#21c45d", "BEAR":"#ff4b4b", "BLANDAD":NEUTRAL}[mkt]
st.markdown(
    f"<div style='margin:.4rem 0 1rem'>"
    f"<span class='pill' style='background:{mcol}22;color:{mcol};border:1px solid {mcol}'>"
    f"MARKNAD: {mkt}</span> "
    f"<span style='color:#8a93a6;font-size:.85rem'>S&P 500 {mkt_pct:+.1f}% mot 200-dagars</span>"
    f"</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------
#  FLIKAR   (ordning matchar dina moduler: SÖK=4, ANALYS=5)
# ---------------------------------------------------------------------
tabs = st.tabs(["🔥 HETA NU", "📈 BYGGER UPP", "⚠️ VARNINGAR", "⭐ Watchlist",
                "🔍 SÖK", "🤖 ANALYS", "📊 MACRO", "🛢 RÅVAROR"])

# ===== 0. HETA NU ====================================================
with tabs[0]:
    st.subheader("🔥 Heta nu — starka lägen i ditt universum")
    render_list(selected, keep_labels={"BULL", "SOON TO FLY"})

# ===== 1. BYGGER UPP =================================================
with tabs[1]:
    st.subheader("📈 Bygger upp — laddade setups")
    render_list(selected, keep_labels={"SOON TO FLY", "NEUTRAL/BYGGER"})

# ===== 2. VARNINGAR ==================================================
with tabs[2]:
    st.subheader("⚠️ Varningar — svaga eller överhettade")
    rows = []
    prog = st.progress(0.0)
    for i, t in enumerate(selected):
        a = scan(t)
        prog.progress((i + 1) / max(1, len(selected)))
        if a and (a["label"] in {"BEAR", "SVAG"} or a["rsi"] > 78 or a["ret_20"] > 35):
            rows.append(a)
    prog.empty()
    if not rows:
        st.info("Inga röda flaggor just nu.")
    for a in rows:
        if a["label"] == "BEAR":      flag = "🔻 BEAR"
        elif a["rsi"] > 78:           flag = "⚠️ ÖVERKÖPT"
        elif a["ret_20"] > 35:        flag = "🚨 PARABOL"
        else:                         flag = "🟠 SVAG"
        st.markdown(f"**{a['ticker']}** — {flag} · RSI {a['rsi']:.0f} · "
                    f"20d {a['ret_20']:+.0f}% · {a['last']:.2f}")

# ===== 3. WATCHLIST ==================================================
with tabs[3]:
    st.subheader("⭐ Watchlist — dina kärnnamn")
    render_list(watchlist)

# ===== 4. SÖK  (din modul) ==========================================
with tabs[4]:
    render_sok_tab()

# ===== 5. ANALYS  (din modul) =======================================
with tabs[5]:
    render_ai_tab()

# ===== 6. MACRO ======================================================
with tabs[6]:
    st.subheader("📊 Macro")
    macro = {"S&P 500":"^GSPC", "Nasdaq 100":"^NDX", "VIX":"^VIX",
             "US 10y":"^TNX", "Dollar (DXY)":"DX-Y.NYB"}
    cols = st.columns(len(macro))
    for col, (name, tk) in zip(cols, macro.items()):
        try:
            df, _ = fetch(tk)
        except Exception:
            df = None
        if df is not None and len(df) > 1:
            c = df["Close"].dropna()
            chg = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
            col.metric(name, f"{float(c.iloc[-1]):.2f}", f"{chg:+.2f}%")
        else:
            col.metric(name, "—")

# ===== 7. RÅVAROR ====================================================
with tabs[7]:
    st.subheader("🛢 Råvaror")
    comm = {"Guld":"GC=F", "Silver":"SI=F", "Koppar":"HG=F",
            "Olja (WTI)":"CL=F", "Uran (URA)":"URA"}
    cols = st.columns(len(comm))
    for col, (name, tk) in zip(cols, comm.items()):
        try:
            df, _ = fetch(tk)
        except Exception:
            df = None
        if df is not None and len(df) > 1:
            c = df["Close"].dropna()
            chg = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
            col.metric(name, f"{float(c.iloc[-1]):.2f}", f"{chg:+.2f}%")
        else:
            col.metric(name, "—")

st.divider()
st.caption("MoneyGrab · teknisk signalering på pris/volym, ingen finansiell rådgivning.")
