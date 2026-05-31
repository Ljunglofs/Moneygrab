# =====================================================================
#  MONEYGRAB  —  din egen aktie-screener
#  Bygger på din picks-and-shovels-tes. Inget av detta är finansiell
#  rådgivning — det är ett verktyg som hjälper dig se lägen snabbare.
# =====================================================================

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import os

try:
    import yfinance as yf
except Exception:
    yf = None

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
ACCENT      = "#2b7fff"   # loggans blå
ACCENT_SOFT = "#1e62cc"
BULL        = "#37d67a"   # grön
BEAR        = "#ff4d5e"   # röd
ROCKET      = "#b06bff"   # lila (sticker ut mot blått)
NEUTRAL     = "#8a93a6"

st.markdown(f"""
<style>
.stApp {{ background:#000000; color:#e9edf5; }}
section[data-testid="stSidebar"] {{ background:#070a10; border-right:1px solid #141a26; }}
h1,h2,h3,h4 {{ color:#ffffff; letter-spacing:.5px; }}
hr {{ border-color:#141a26; }}

/* Flikar */
.stTabs [data-baseweb="tab-list"] {{ gap:4px; border-bottom:1px solid #141a26; }}
.stTabs [data-baseweb="tab"] {{
    background:#0b0f17; color:#8a93a6; border-radius:8px 8px 0 0;
    padding:8px 16px; font-weight:600;
}}
.stTabs [aria-selected="true"] {{ background:{ACCENT}; color:#ffffff; }}

/* Knappar */
.stButton>button {{
    background:{ACCENT}; color:#fff; border:0; border-radius:8px;
    font-weight:700; padding:.5rem 1rem;
}}
.stButton>button:hover {{ background:{ACCENT_SOFT}; }}

/* Statuspiller */
.pill {{ display:inline-block; padding:3px 12px; border-radius:999px;
        font-size:.78rem; font-weight:800; letter-spacing:.5px; }}
.pill-bull   {{ background:{BULL};    color:#03210f; }}
.pill-bear   {{ background:{BEAR};    color:#2a0307; }}
.pill-rocket {{ background:{ROCKET};  color:#1a0533; }}
.pill-neu    {{ background:{NEUTRAL}; color:#10131a; }}

/* Stor poängsiffra */
.score-box {{ text-align:center; padding:1.2rem; border-radius:14px;
            background:#0b0f17; border:1px solid #141a26; }}
.score-num {{ font-size:3.4rem; font-weight:900; line-height:1; }}
.score-lab {{ font-size:.8rem; color:#8a93a6; letter-spacing:1px; }}

[data-testid="stMetricValue"] {{ font-size:1.1rem; }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------
#  LOGGA
# ---------------------------------------------------------------------
LOGO_PATH = "logo.png"
c1, c2 = st.columns([1, 3])
with c1:
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
    "AI-infra":    ["NVDA","NBIS","CRDO","ALAB","MRVL","AVGO","AMD","SMCI","VRT","DGXX"],
    "Photonics":   ["SIVE.ST","POET","LWLG"],
    "Quantum":     ["IONQ","QUBT","RGTI"],
    "Rare earth":  ["USAR","MP"],
    "Defense/Drone":["ONDS","KTOS","AVAV"],
    "Lidar/Phys.AI":["OUST","LAZR"],
    "Nuclear":     ["OKLO","NNE","SMR","UEC","UUUU"],
    "Space":       ["RKLB","ASTS","RDW"],
    "Koppar":      ["FCX","HBM"],
    "Silver/Guld": ["AG","PAAS","GAU"],
}
ALL_TICKERS = sorted({t for v in UNIVERSE.values() for t in v})

# ---------------------------------------------------------------------
#  DATAHÄMTNING + ANALYS
# ---------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def get_hist(ticker: str) -> pd.DataFrame | None:
    if yf is None:
        return None
    try:
        df = yf.Ticker(ticker).history(period="1y", interval="1d")
        return df if df is not None and not df.empty else None
    except Exception:
        return None


def _rsi(series: pd.Series, n: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean().iloc[-1]
    loss = -delta.clip(upper=0).rolling(n).mean().iloc[-1]
    if loss == 0 or np.isnan(loss):
        return 100.0
    rs = gain / loss
    return float(100 - (100 / (1 + rs)))


def analyze(ticker: str) -> dict | None:
    """Returnerar läge (BEAR/BULL/ROCKETCASE/NEUTRAL) och en
    lyftchans-poäng 1–10. Ren heuristik, ingen rådgivning."""
    df = get_hist(ticker)
    if df is None or len(df) < 60:
        return None

    close = df["Close"].dropna()
    vol   = df["Volume"].dropna()
    last  = float(close.iloc[-1])

    ma50  = float(close.rolling(50).mean().iloc[-1])
    win200 = min(len(close), 200)
    ma200 = float(close.rolling(win200).mean().iloc[-1])

    rsi   = _rsi(close)
    high52 = float(close.tail(252).max())
    dist_high = (last / high52 - 1) * 100               # neg = under topp
    dist_ma50 = (last / ma50 - 1) * 100
    mom20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0.0
    base = vol.tail(60).mean()
    relvol = float(vol.tail(5).mean() / base) if base else 1.0

    above50, above200 = last > ma50, last > ma200
    rising50 = ma50 > float(close.rolling(50).mean().iloc[-10]) if len(close) >= 60 else above50

    # ---- LÄGE ----
    if not above50 and not above200 and mom20 < 0:
        regime = "BEAR"
    elif above50 and above200 and rsi >= 55 and mom20 > 0:
        regime = "BULL"
    else:
        regime = "NEUTRAL"

    # ---- ROCKETCASE: coil under motstånd, redo att bryta ----
    rocket = (
        above50 and rising50
        and 45 <= rsi <= 68
        and -18 <= dist_high <= -2          # strax under 52v-topp
        and relvol >= 1.05
        and 0 <= mom20 <= 25                 # bygger, inte parabol
        and dist_ma50 <= 12                  # inte överutsträckt
    )
    if rocket:
        regime = "ROCKETCASE"

    # ---- LYFTCHANS 1–10 ----
    s = 0.0
    s += 2.0 if above50 else 0
    s += 1.0 if above200 else 0
    s += 1.5 if rising50 else 0
    s += 2.0 if 50 <= rsi <= 68 else (0.5 if 40 <= rsi < 50 else 0)
    s -= 1.5 if rsi > 78 else 0                     # överköpt = redan flugit
    s += 1.5 if -15 <= dist_high <= -2 else 0       # nära motstånd
    s += 1.0 if relvol >= 1.3 else (0.5 if relvol >= 1.05 else 0)
    s += 1.0 if 2 <= mom20 <= 20 else 0
    s -= 2.0 if mom20 > 35 else 0                   # parabol = sen
    s -= 2.5 if regime == "BEAR" else 0
    score = int(round(max(1, min(10, s))))

    return dict(
        ticker=ticker, last=last, regime=regime, score=score, rsi=rsi,
        dist_high=dist_high, mom20=mom20, relvol=relvol,
        dist_ma50=dist_ma50, above50=above50, above200=above200,
        spark=close.tail(30).tolist(),
    )


def regime_pill(regime: str) -> str:
    cls = {"BULL":"pill-bull","BEAR":"pill-bear",
           "ROCKETCASE":"pill-rocket","NEUTRAL":"pill-neu"}[regime]
    return f"<span class='pill {cls}'>{regime}</span>"


def score_color(score: int) -> str:
    if score >= 8:  return BULL
    if score >= 5:  return ACCENT
    if score >= 3:  return NEUTRAL
    return BEAR

# ---------------------------------------------------------------------
#  TRADINGVIEW-GRAF
# ---------------------------------------------------------------------
def tradingview_chart(symbol: str, height: int = 520):
    sym = symbol.upper().strip()
    html = f"""
    <div class="tradingview-widget-container" style="height:{height}px">
      <div id="tv_chart" style="height:{height}px"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
      new TradingView.widget({{
        "autosize": true,
        "symbol": "{sym}",
        "interval": "D",
        "timezone": "Europe/Stockholm",
        "theme": "dark",
        "style": "1",
        "locale": "se",
        "toolbar_bg": "#000000",
        "hide_side_toolbar": false,
        "allow_symbol_change": true,
        "container_id": "tv_chart"
      }});
      </script>
    </div>
    """
    components.html(html, height=height + 10)

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

if yf is None:
    st.sidebar.error("yfinance saknas – lägg till i requirements.txt")

# ---------------------------------------------------------------------
#  MARKNADSLÄGE  (S&P 500 vs 200-dagars)
# ---------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def market_regime():
    df = get_hist("^GSPC")
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
mcls = {"BULL":"pill-bull","BEAR":"pill-bear","BLANDAD":"pill-neu"}[mkt]
st.markdown(
    f"<div style='margin:.4rem 0 1rem'>"
    f"<span class='pill {mcls}'>MARKNAD: {mkt}</span> "
    f"<span style='color:#8a93a6;font-size:.85rem'>S&P 500 {mkt_pct:+.1f}% mot 200-dagars</span>"
    f"</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------
#  FLIKAR
# ---------------------------------------------------------------------
tabs = st.tabs(["🔎 SÖK", "🔥 HETA NU", "📈 BYGGER UPP",
                "⚠️ VARNINGAR", "⭐ WATCHLIST", "📊 MACRO", "🛢️ RÅVAROR"])

# ===== 0. SÖK ========================================================
with tabs[0]:
    st.subheader("Sök valfri aktie")
    q = st.text_input("Ticker", placeholder="t.ex. NVDA, SIVE.ST, TSLA …", key="search")
    tv = st.text_input("TradingView-symbol (valfritt)",
                       placeholder="t.ex. NASDAQ:NVDA eller OMXSTO:SIVE",
                       key="tvsym")
    if q:
        with st.spinner(f"Analyserar {q.upper()} …"):
            r = analyze(q.upper().strip())
        if r is None:
            st.error("Hittade ingen data. Kolla tickern (svenska aktier: lägg till .ST).")
        else:
            colA, colB = st.columns([1, 2])
            with colA:
                st.markdown(
                    f"<div class='score-box'>"
                    f"<div class='score-lab'>LYFTCHANS</div>"
                    f"<div class='score-num' style='color:{score_color(r['score'])}'>{r['score']}<span style='font-size:1.2rem;color:#8a93a6'>/10</span></div>"
                    f"<div style='margin-top:.6rem'>{regime_pill(r['regime'])}</div>"
                    f"</div>", unsafe_allow_html=True)
                st.metric("Pris", f"{r['last']:.2f}")
                st.metric("RSI (14)", f"{r['rsi']:.0f}")
                st.metric("Mot 52v-topp", f"{r['dist_high']:+.1f}%")
                st.metric("Momentum 20d", f"{r['mom20']:+.1f}%")
                st.metric("Rel. volym", f"{r['relvol']:.2f}x")
            with colB:
                st.caption("Signaler bakom poängen:")
                bullets = []
                bullets.append(("Över MA50", r["above50"]))
                bullets.append(("Över MA200", r["above200"]))
                bullets.append(("Nära 52v-topp (motstånd)", -18 <= r["dist_high"] <= -2))
                bullets.append(("RSI i lyftzon 50–68", 50 <= r["rsi"] <= 68))
                bullets.append(("Stigande relativ volym", r["relvol"] >= 1.05))
                bullets.append(("Inte parabolisk", r["mom20"] <= 25))
                for label, ok in bullets:
                    mark = "✅" if ok else "—"
                    st.write(f"{mark}  {label}")
            st.divider()
            tradingview_chart(tv.strip() if tv.strip() else q.upper().strip())
    else:
        st.info("Skriv en ticker ovan för läge (BEAR/BULL/ROCKETCASE) + lyftchans 1–10 och graf.")

# ===== Hjälp: bygg tabell för listflikar =============================
def render_list(tickers, regimes=None, sort_desc=True):
    rows = []
    prog = st.progress(0.0)
    for i, t in enumerate(tickers):
        r = analyze(t)
        prog.progress((i + 1) / max(1, len(tickers)))
        if r and (regimes is None or r["regime"] in regimes):
            rows.append(r)
    prog.empty()
    if not rows:
        st.info("Inga namn matchar just nu.")
        return
    rows.sort(key=lambda x: x["score"], reverse=sort_desc)
    for r in rows:
        c = st.columns([1.4, 0.8, 1.4, 1, 1, 1, 1])
        c[0].markdown(f"**{r['ticker']}**")
        c[1].markdown(f"<span style='color:{score_color(r['score'])};font-weight:800'>{r['score']}/10</span>",
                      unsafe_allow_html=True)
        c[2].markdown(regime_pill(r["regime"]), unsafe_allow_html=True)
        c[3].write(f"{r['last']:.2f}")
        c[4].write(f"RSI {r['rsi']:.0f}")
        c[5].write(f"{r['dist_high']:+.0f}%")
        c[6].write(f"{r['relvol']:.1f}x")

# ===== 1. HETA NU ====================================================
with tabs[1]:
    st.subheader("Heta nu — högst lyftchans i ditt universum")
    render_list(selected, regimes={"BULL", "ROCKETCASE"})

# ===== 2. BYGGER UPP =================================================
with tabs[2]:
    st.subheader("Bygger upp — coil/Rocketcase-lägen")
    render_list(selected, regimes={"ROCKETCASE", "NEUTRAL"})

# ===== 3. VARNINGAR ==================================================
with tabs[3]:
    st.subheader("Varningar — svaga eller överhettade")
    rows = []
    prog = st.progress(0.0)
    for i, t in enumerate(selected):
        r = analyze(t)
        prog.progress((i + 1) / max(1, len(selected)))
        if r and (r["regime"] == "BEAR" or r["rsi"] > 78 or r["mom20"] > 35):
            rows.append(r)
    prog.empty()
    if not rows:
        st.info("Inga röda flaggor just nu.")
    for r in rows:
        flag = "BEAR" if r["regime"] == "BEAR" else ("ÖVERKÖPT" if r["rsi"] > 78 else "PARABOL")
        st.markdown(f"**{r['ticker']}** — {flag} · RSI {r['rsi']:.0f} · "
                    f"mom {r['mom20']:+.0f}% · {r['last']:.2f}")

# ===== 4. WATCHLIST ==================================================
with tabs[4]:
    st.subheader("Watchlist — dina kärnnamn")
    render_list(watchlist)

# ===== 5. MACRO ======================================================
with tabs[5]:
    st.subheader("Macro")
    macro = {"S&P 500":"^GSPC", "Nasdaq 100":"^NDX", "VIX":"^VIX",
             "US 10y":"^TNX", "Dollar (DXY)":"DX-Y.NYB"}
    cols = st.columns(len(macro))
    for col, (name, tk) in zip(cols, macro.items()):
        df = get_hist(tk)
        if df is not None and len(df) > 1:
            c = df["Close"].dropna()
            chg = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
            col.metric(name, f"{float(c.iloc[-1]):.2f}", f"{chg:+.2f}%")
        else:
            col.metric(name, "—")

# ===== 6. RÅVAROR ====================================================
with tabs[6]:
    st.subheader("Råvaror")
    comm = {"Guld":"GC=F", "Silver":"SI=F", "Koppar":"HG=F",
            "Olja (WTI)":"CL=F", "Uran (URA)":"URA"}
    cols = st.columns(len(comm))
    for col, (name, tk) in zip(cols, comm.items()):
        df = get_hist(tk)
        if df is not None and len(df) > 1:
            c = df["Close"].dropna()
            chg = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
            col.metric(name, f"{float(c.iloc[-1]):.2f}", f"{chg:+.2f}%")
        else:
            col.metric(name, "—")

st.divider()
st.caption("MoneyGrab · ren heuristik på pris/volym, ingen finansiell rådgivning. "
           "Lyftchans 1–10 = styrkan i ett möjligt breakout-läge, inte en garanti.")
