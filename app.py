# =====================================================================
#  MONEYGRAB  —  huvudfil
#  Screener-flikar + klickbar detaljvy + Ask Grabit.
#  Binder ihop dina moduler (sok_module, ai_module).
#  Inget av detta är finansiell rådgivning.
# =====================================================================

import os
import streamlit as st
import pandas as pd
import numpy as np

from sok_module import render_sok_tab, fetch, analyze, tradingview_chart, guess_tv_symbol
from ai_module import render_ai_tab

st.set_page_config(page_title="MoneyGrab", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

# ---------------------------------------------------------------------
#  FÄRGER / TEMA-TILLÄGG  (grundtemat sätts i .streamlit/config.toml)
# ---------------------------------------------------------------------
BG, PANEL, LINE = "#0a0d12", "#11151d", "#1c2330"
ACCENT, POS, NEG, MUTED, TXT = "#2b7fff", "#3d8bff", "#ff5468", "#7b8698", "#e8edf5"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] {{ font-family:'Inter',-apple-system,sans-serif; }}
h1,h2,h3,h4 {{ color:#fff; font-weight:700; letter-spacing:-.2px; }}
.stApp h2, .stApp h3 {{ font-size:1.15rem; }}

.stTabs [data-baseweb="tab-list"] {{ gap:2px; border-bottom:1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ background:transparent; color:{MUTED}; border-radius:0;
    padding:10px 16px; font-weight:600; font-size:.9rem; border-bottom:2px solid transparent; }}
.stTabs [aria-selected="true"] {{ color:#fff; border-bottom:2px solid {ACCENT}; }}

.pill {{ display:inline-block; padding:4px 12px; border-radius:6px;
        font-size:.75rem; font-weight:700; letter-spacing:.5px; }}

/* rad-rubriker */
.lhead {{ color:{MUTED}; font-size:.7rem; font-weight:600; text-transform:uppercase;
         letter-spacing:.6px; padding:4px 0; border-bottom:1px solid {LINE}; }}
.cell {{ padding-top:6px; font-size:.92rem; }}
.lab  {{ font-weight:600; font-size:.82rem; letter-spacing:.3px; }}

/* ticker-knapp som rad-länk */
div[data-testid="column"] .stButton>button {{
    background:transparent; border:1px solid transparent; color:#fff;
    font-weight:700; text-align:left; padding:2px 6px; }}
div[data-testid="column"] .stButton>button:hover {{
    border-color:{ACCENT}; color:{ACCENT}; }}

/* metric-kort i detaljvyn */
.mgrid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr));
         gap:8px; margin:12px 0; }}
.mcard {{ background:{PANEL}; border:1px solid {LINE}; border-radius:10px; padding:10px 12px; }}
.ml {{ color:{MUTED}; font-size:.68rem; text-transform:uppercase; letter-spacing:.5px; }}
.mv {{ font-size:1.1rem; font-weight:700; margin-top:3px; }}
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
        st.markdown(f"<h1 style='margin:0'>MONEY<span style='color:{ACCENT}'>GRAB</span></h1>",
                    unsafe_allow_html=True)

# ---------------------------------------------------------------------
#  UNIVERSUM
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
st.sidebar.markdown("### Inställningar")
WATCHLIST_DEFAULT = "NVDA, QUBT, USAR, OUST, OKLO, NBIS, CRDO, SIVE.ST, IONQ, RKLB, ASTS, ONDS"
wl_raw = st.sidebar.text_area("Min watchlist", WATCHLIST_DEFAULT, height=120)
watchlist = [t.strip().upper() for t in wl_raw.replace("\n", ",").split(",") if t.strip()]
theme_sel = st.sidebar.multiselect("Filtrera teman", list(UNIVERSE.keys()),
                                   default=list(UNIVERSE.keys()))
selected = sorted({t for k in theme_sel for t in UNIVERSE[k]})
if st.sidebar.button("Tvinga refresh av data", type="primary"):
    st.cache_data.clear()
    st.rerun()

# ---------------------------------------------------------------------
#  HJÄLPARE
# ---------------------------------------------------------------------
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
    return a


def pct_span(v):
    return f"<span style='color:{POS if v >= 0 else NEG}'>{v:+.1f}%</span>"

# ---------------------------------------------------------------------
#  DETALJVY  (modal när man klickar på en aktie)
# ---------------------------------------------------------------------
@st.dialog(" ", width="large")
def show_detail(ticker):
    a = scan(ticker)
    if not a:
        st.error(f"Hittade ingen data för {ticker}.")
        return

    # spara kontext så Ask Grabit vet vilken aktie det gäller
    st.session_state["sok_context"] = {
        "ticker": ticker, "label": a["label"], "score10": a["score10"],
        "last": a["last"], "rsi": a["rsi"], "pct_from_high": a["pct_from_high"],
        "ret_20": a["ret_20"], "rel_vol": a["rel_vol"],
        "strength": a["strength"], "momentum": a["momentum"], "setup": a["setup"],
    }

    top = st.columns([2, 1, 1])
    top[0].markdown(f"## {ticker}")
    top[0].markdown(
        f"<span class='pill' style='background:{a['color']}22;color:{a['color']};"
        f"border:1px solid {a['color']}55'>{a['label']}</span>", unsafe_allow_html=True)
    top[1].metric("Pris", f"{a['last']:.2f}")
    top[2].metric("Lyftchans", f"{a['score10']}/10")

    items = [
        ("RSI (14)", f"{a['rsi']:.0f}", TXT),
        ("20d", f"{a['ret_20']:+.1f}%", POS if a['ret_20'] >= 0 else NEG),
        ("5d", f"{a['ret_5']:+.1f}%", POS if a['ret_5'] >= 0 else NEG),
        ("Mot 52v-topp", f"{a['pct_from_high']:+.1f}%", MUTED),
        ("52v-range", f"{a['rng_pos']:.0f}%", TXT),
        ("Rel. volym", f"{a['rel_vol']:.2f}x", TXT),
        ("Volatilitet", f"{a['atr_pct']:.1f}%", TXT),
        ("EMA50", "över" if a['last'] > a['ema50'] else "under",
         POS if a['last'] > a['ema50'] else NEG),
        ("EMA200", "över" if a['last'] > a['ema200'] else "under",
         POS if a['last'] > a['ema200'] else NEG),
    ]
    cells = "".join(f"<div class='mcard'><div class='ml'>{l}</div>"
                    f"<div class='mv' style='color:{c}'>{v}</div></div>" for l, v, c in items)
    st.markdown(f"<div class='mgrid'>{cells}</div>", unsafe_allow_html=True)

    st.progress(min(a["total"] / 100, 1.0),
                text=f"Score {a['total']:.0f}/100  ·  Styrka {a['strength']:.0f}/40 · "
                     f"Momentum {a['momentum']:.0f}/35 · Setup {a['setup']:.0f}/25")

    tradingview_chart(guess_tv_symbol(ticker), height=360)
    st.caption(f"Öppna **Ask Grabit**-fliken för att fråga AI:n om {ticker} — "
               f"den vet redan att du tittar på den.")

# ---------------------------------------------------------------------
#  LIST-RENDERARE  (klickbara rader)
# ---------------------------------------------------------------------
RATIOS = [1.1, 1.5, 0.9, 1, 0.8, 1, 1, 0.9]
HEADS  = ["Ticker", "Läge", "Poäng", "Pris", "RSI", "20d", "Mot topp", "Vol"]

def render_list(tickers, prefix, keep_labels=None):
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
    rows.sort(key=lambda x: x["score10"], reverse=True)

    hc = st.columns(RATIOS)
    for col, head in zip(hc, HEADS):
        col.markdown(f"<div class='lhead'>{head}</div>", unsafe_allow_html=True)

    for a in rows:
        c = st.columns(RATIOS)
        if c[0].button(a["ticker"], key=f"{prefix}_{a['ticker']}"):
            show_detail(a["ticker"])
        c[1].markdown(f"<div class='cell lab' style='color:{a['color']}'>{a['label']}</div>",
                      unsafe_allow_html=True)
        c[2].markdown(f"<div class='cell' style='color:{a['color']};font-weight:800'>{a['score10']}/10</div>",
                      unsafe_allow_html=True)
        c[3].markdown(f"<div class='cell'>{a['last']:.2f}</div>", unsafe_allow_html=True)
        c[4].markdown(f"<div class='cell'>{a['rsi']:.0f}</div>", unsafe_allow_html=True)
        c[5].markdown(f"<div class='cell'>{pct_span(a['ret_20'])}</div>", unsafe_allow_html=True)
        c[6].markdown(f"<div class='cell' style='color:{MUTED}'>{a['pct_from_high']:+.0f}%</div>",
                      unsafe_allow_html=True)
        c[7].markdown(f"<div class='cell'>{a['rel_vol']:.1f}x</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------
#  MARKNADSLÄGE
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
mcol = {"BULL": POS, "BEAR": NEG, "BLANDAD": MUTED}[mkt]
st.markdown(
    f"<div style='margin:.4rem 0 1rem'>"
    f"<span class='pill' style='background:{mcol}22;color:{mcol};border:1px solid {mcol}55'>"
    f"MARKNAD: {mkt}</span> "
    f"<span style='color:{MUTED};font-size:.85rem;margin-left:8px'>"
    f"S&P 500 {mkt_pct:+.1f}% mot 200-dagars</span></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------
#  FLIKAR   (SÖK=4, ASK GRABIT=5)
# ---------------------------------------------------------------------
tabs = st.tabs(["HETA NU", "BYGGER UPP", "VARNINGAR", "WATCHLIST",
                "SÖK", "ASK GRABIT", "MACRO", "RÅVAROR"])

with tabs[0]:
    st.subheader("Heta nu — starka lägen i ditt universum")
    render_list(selected, "hot", keep_labels={"BULL", "Rocketcase"})

with tabs[1]:
    st.subheader("Bygger upp — laddade setups")
    render_list(selected, "build", keep_labels={"Rocketcase", "NEUTRAL/BYGGER"})

with tabs[2]:
    st.subheader("Varningar — svaga eller överhettade")
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
    else:
        hc = st.columns([1.1, 1.4, 0.8, 0.9, 1])
        for col, head in zip(hc, ["Ticker", "Flagga", "RSI", "20d", "Pris"]):
            col.markdown(f"<div class='lhead'>{head}</div>", unsafe_allow_html=True)
        for a in rows:
            if a["label"] == "BEAR":   flag, col_ = "BEAR", NEG
            elif a["rsi"] > 78:        flag, col_ = "ÖVERKÖPT", "#f5a623"
            elif a["ret_20"] > 35:     flag, col_ = "PARABOL", "#f5a623"
            else:                      flag, col_ = "SVAG", MUTED
            c = st.columns([1.1, 1.4, 0.8, 0.9, 1])
            if c[0].button(a["ticker"], key=f"warn_{a['ticker']}"):
                show_detail(a["ticker"])
            c[1].markdown(f"<div class='cell lab' style='color:{col_}'>{flag}</div>", unsafe_allow_html=True)
            c[2].markdown(f"<div class='cell'>{a['rsi']:.0f}</div>", unsafe_allow_html=True)
            c[3].markdown(f"<div class='cell'>{pct_span(a['ret_20'])}</div>", unsafe_allow_html=True)
            c[4].markdown(f"<div class='cell'>{a['last']:.2f}</div>", unsafe_allow_html=True)

with tabs[3]:
    st.subheader("Watchlist — dina kärnnamn")
    render_list(watchlist, "wl")

with tabs[4]:
    render_sok_tab()

with tabs[5]:
    render_ai_tab()

with tabs[6]:
    st.subheader("Macro")
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

with tabs[7]:
    st.subheader("Råvaror")
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
