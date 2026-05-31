# =====================================================================
#  MONEYGRAB  —  huvudfil  (trading-terminal-layout)
#  Datagrid med logotyper + lyftchans-stapel, klickbar detaljvy,
#  nyckeltalsstrip och Ask Grabit. Modulerna (sok_module, ai_module)
#  är orörda. Inget av detta är finansiell rådgivning.
# =====================================================================

import os
import urllib.parse
import streamlit as st
import pandas as pd
import numpy as np

from sok_module import render_sok_tab, fetch, analyze, tradingview_chart, guess_tv_symbol
from ai_module import render_ai_tab

st.set_page_config(page_title="MoneyGrab", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

BG, PANEL, LINE = "#0a0d12", "#11151d", "#1c2330"
ACCENT, POS, NEG, MUTED, TXT = "#2b7fff", "#3d8bff", "#ff5468", "#7b8698", "#e8edf5"
BULL_C, ROCK_C = "#21c45d", "#f5a623"

# tema-färg per ticker (för logo-ikonerna) -> ger variation som riktiga loggor
THEME_COLOR = {
    "AI-infra":"2b7fff","Photonics":"00c2c2","Quantum":"b06bff","Rare earth":"f5a623",
    "Defense/Drone":"ff5468","Lidar/Phys.AI":"21c45d","Nuclear":"ffd23f","Space":"ff7ab8",
    "Koppar":"d2691e","Silver/Guld":"9aa7b5",
}
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
TICKER_THEME = {t: k for k, v in UNIVERSE.items() for t in v}

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] {{ font-family:'Inter',-apple-system,sans-serif; }}
h1,h2,h3,h4 {{ color:#fff; font-weight:700; letter-spacing:-.2px; }}
.stApp h2, .stApp h3 {{ font-size:1.1rem; }}
.stTabs [data-baseweb="tab-list"] {{ gap:2px; border-bottom:1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ background:transparent; color:{MUTED}; border-radius:0;
    padding:9px 15px; font-weight:600; font-size:.85rem; border-bottom:2px solid transparent; }}
.stTabs [aria-selected="true"] {{ color:#fff; border-bottom:2px solid {ACCENT}; }}
.pill {{ display:inline-block; padding:4px 12px; border-radius:6px;
        font-size:.75rem; font-weight:700; letter-spacing:.5px; }}
/* nyckeltalsstrip */
.sstrip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
          gap:10px; margin:6px 0 14px; }}
.scard {{ background:{PANEL}; border:1px solid {LINE}; border-radius:10px; padding:10px 14px; }}
.sl {{ color:{MUTED}; font-size:.68rem; text-transform:uppercase; letter-spacing:.6px; }}
.sv {{ font-size:1.35rem; font-weight:800; margin-top:2px; }}
/* metric-kort i detaljvyn */
.mgrid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(96px,1fr));
         gap:8px; margin:12px 0; }}
.mcard {{ background:{BG}; border:1px solid {LINE}; border-radius:10px; padding:9px 11px; }}
.ml {{ color:{MUTED}; font-size:.66rem; text-transform:uppercase; letter-spacing:.5px; }}
.mv {{ font-size:1.05rem; font-weight:700; margin-top:3px; }}
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


def logo_url(ticker: str):
    color = THEME_COLOR.get(TICKER_THEME.get(ticker, ""), "1c2330")
    name = "".join(ch for ch in ticker if ch.isalpha())[:2] or ticker[:2]
    q = urllib.parse.urlencode({"name": name, "background": color, "color": "ffffff",
                                "bold": "true", "size": "64", "rounded": "true"})
    return f"https://ui-avatars.com/api/?{q}"


CFG = {
    "Logo":   st.column_config.ImageColumn("", width="small"),
    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
    "Läge":   st.column_config.TextColumn("Läge", width="small"),
    "Poäng":  st.column_config.ProgressColumn("Lyftchans", min_value=0, max_value=10, format="%d"),
    "Pris":   st.column_config.NumberColumn("Pris", format="%.2f"),
    "RSI":    st.column_config.NumberColumn("RSI", format="%d"),
    "20d":    st.column_config.NumberColumn("20d", format="%.1f%%"),
    "Topp":   st.column_config.NumberColumn("Mot topp", format="%.0f%%"),
    "Vol":    st.column_config.NumberColumn("Vol", format="%.1fx"),
}


def render_stats(rows):
    n = len(rows)
    nm = sum(1 for a in rows if a["label"] == "MOMENTUM")
    nv = sum(1 for a in rows if a["label"] == "VÄNDNING")
    avg = (sum(a["score10"] for a in rows) / n) if n else 0
    cards = [("Träffar", f"{n}", TXT), ("Momentum", f"{nm}", "#b06bff"),
             ("Vändning", f"{nv}", "#00c2c2"), ("Snittpoäng", f"{avg:.1f}", ACCENT)]
    cells = "".join(f"<div class='scard'><div class='sl'>{l}</div>"
                    f"<div class='sv' style='color:{c}'>{v}</div></div>" for l, v, c in cards)
    st.markdown(f"<div class='sstrip'>{cells}</div>", unsafe_allow_html=True)


def render_grid(rows, key):
    if not rows:
        st.info("Inga namn matchar just nu.")
        return
    rows.sort(key=lambda x: x["score10"], reverse=True)
    df = pd.DataFrame([{
        "Logo": logo_url(a["ticker"]), "Ticker": a["ticker"], "Läge": a["label"],
        "Poäng": int(a["score10"]), "Pris": float(a["last"]), "RSI": int(round(a["rsi"])),
        "20d": float(a["ret_20"]), "Topp": float(a["pct_from_high"]), "Vol": float(a["rel_vol"]),
    } for a in rows])
    h = min(len(df) * 36 + 40, 560)
    ev = st.dataframe(df, column_config=CFG, hide_index=True, use_container_width=True,
                      height=h, on_select="rerun", selection_mode="single-row", key=key)
    rowsel = ev.selection.rows if (ev and ev.selection) else []
    if rowsel:
        show_detail(df.iloc[rowsel[0]]["Ticker"])

# ---------------------------------------------------------------------
#  DETALJVY  (modal)
# ---------------------------------------------------------------------
@st.dialog(" ", width="large")
def show_detail(ticker):
    a = scan(ticker)
    if not a:
        st.error(f"Hittade ingen data för {ticker}.")
        return
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
        ("RSI", f"{a['rsi']:.0f}", TXT),
        ("20d", f"{a['ret_20']:+.1f}%", POS if a['ret_20'] >= 0 else NEG),
        ("5d", f"{a['ret_5']:+.1f}%", POS if a['ret_5'] >= 0 else NEG),
        ("Mot topp", f"{a['pct_from_high']:+.1f}%", MUTED),
        ("52v-range", f"{a['rng_pos']:.0f}%", TXT),
        ("Rel.vol", f"{a['rel_vol']:.2f}x", TXT),
        ("Volatilitet", f"{a['atr_pct']:.1f}%", TXT),
        ("EMA50", "över" if a['last'] > a['ema50'] else "under", POS if a['last'] > a['ema50'] else NEG),
        ("EMA200", "över" if a['last'] > a['ema200'] else "under", POS if a['last'] > a['ema200'] else NEG),
    ]
    cells = "".join(f"<div class='mcard'><div class='ml'>{l}</div>"
                    f"<div class='mv' style='color:{c}'>{v}</div></div>" for l, v, c in items)
    st.markdown(f"<div class='mgrid'>{cells}</div>", unsafe_allow_html=True)
    st.progress(min(a["total"] / 100, 1.0),
                text=f"Score {a['total']:.0f}/100 · Styrka {a['strength']:.0f}/40 · "
                     f"Momentum {a['momentum']:.0f}/35 · Setup {a['setup']:.0f}/25")
    tradingview_chart(guess_tv_symbol(ticker), height=360)
    st.caption(f"Öppna **Ask Grabit**-fliken för att fråga AI:n om {ticker}.")

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
    f"<div style='margin:.3rem 0 1rem'>"
    f"<span class='pill' style='background:{mcol}22;color:{mcol};border:1px solid {mcol}55'>"
    f"MARKNAD: {mkt}</span> "
    f"<span style='color:{MUTED};font-size:.85rem;margin-left:8px'>"
    f"S&P 500 {mkt_pct:+.1f}% mot 200-dagars</span></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------
#  FLIKAR
# ---------------------------------------------------------------------
tabs = st.tabs(["HETA NU", "VÄNDNINGAR", "VARNINGAR", "WATCHLIST",
                "SÖK", "ASK GRABIT", "MACRO", "RÅVAROR"])

def collect(tickers, keep_labels=None, warn=False):
    out = []
    prog = st.progress(0.0)
    for i, t in enumerate(tickers):
        a = scan(t)
        prog.progress((i + 1) / max(1, len(tickers)))
        if not a:
            continue
        if warn:
            if a["label"] in {"BEAR", "SVAG"} or a["rsi"] > 78 or a["ret_20"] > 35:
                out.append(a)
        elif keep_labels is None or a["label"] in keep_labels:
            out.append(a)
    prog.empty()
    return out

with tabs[0]:
    st.subheader("Heta nu — fart & breakout-lägen")
    r = collect(selected, keep_labels={"MOMENTUM", "BULL", "Rocketcase"})
    render_stats(r); render_grid(r, "hot")

with tabs[1]:
    st.subheader("Vändningar — turnarounds & tidiga lägen")
    r = collect(selected, keep_labels={"VÄNDNING", "NEUTRAL/BYGGER"})
    render_stats(r); render_grid(r, "turn")

with tabs[2]:
    st.subheader("Varningar — svaga eller överhettade")
    r = collect(selected, warn=True)
    if not r:
        st.info("Inga röda flaggor just nu.")
    else:
        def warnflag(a):
            if a["label"] == "BEAR": return "BEAR"
            if a["rsi"] > 78:        return "ÖVERKÖPT"
            if a["ret_20"] > 35:     return "PARABOL"
            return "SVAG"
        r.sort(key=lambda x: x["rsi"], reverse=True)
        df = pd.DataFrame([{
            "Logo": logo_url(a["ticker"]), "Ticker": a["ticker"], "Flagga": warnflag(a),
            "RSI": int(round(a["rsi"])), "20d": float(a["ret_20"]), "Pris": float(a["last"]),
        } for a in r])
        cfg = {"Logo": st.column_config.ImageColumn("", width="small"),
               "Ticker": st.column_config.TextColumn("Ticker", width="small"),
               "Flagga": st.column_config.TextColumn("Flagga"),
               "RSI": st.column_config.NumberColumn("RSI", format="%d"),
               "20d": st.column_config.NumberColumn("20d", format="%.1f%%"),
               "Pris": st.column_config.NumberColumn("Pris", format="%.2f")}
        ev = st.dataframe(df, column_config=cfg, hide_index=True, use_container_width=True,
                          height=min(len(df) * 36 + 40, 560),
                          on_select="rerun", selection_mode="single-row", key="warn")
        sel = ev.selection.rows if (ev and ev.selection) else []
        if sel:
            show_detail(df.iloc[sel[0]]["Ticker"])

with tabs[3]:
    st.subheader("Watchlist — dina kärnnamn")
    r = collect(watchlist)
    render_stats(r); render_grid(r, "wl")

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
