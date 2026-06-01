# =====================================================================
#  MONEYGRAB  —  huvudfil
#  Bas: app.py (senaste design + universum + breakout_engine)
#  Pro-tillägg: ai_score_components, trade_motor_v2, news_ticker
#  från moneygrab_pro_v4 — fullt integrerade, inga lösa kommentarsblock.
#  Inget av detta är finansiell rådgivning.
# =====================================================================

import os
import urllib.parse
import streamlit as st
import pandas as pd
import numpy as np

try:
    import yfinance as yf
except Exception:
    yf = None

try:
    import feedparser
except Exception:
    feedparser = None

from sok_module import render_sok_tab, fetch, analyze, tradingview_chart, guess_tv_symbol
from ai_module import render_ai_tab

try:
    from dagens_bull import render_dagens_bull
except Exception:
    render_dagens_bull = None

try:
    from breakout_engine import evaluate as engine_evaluate
except Exception:
    engine_evaluate = None

st.set_page_config(page_title="MoneyGrab", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

BG, PANEL, LINE = "#0b0e16", "#161a23", "#252b38"
ACCENT, POS, NEG, MUTED, TXT = "#1199fa", "#16c784", "#f6465d", "#848e9c", "#eaecef"
BULL_C, ROCK_C = "#16c784", "#f0a020"

THEME_COLOR = {
    "AI-infra":      "2b7fff", "Halvledare":    "4f8cff", "Photonics":     "00c2c2",
    "Quantum":       "b06bff", "Rare earth":    "f5a623", "Defense/Drone": "ff5468",
    "Lidar/Phys.AI": "21c45d", "Nuclear/Energi":"ffd23f", "Space":         "ff7ab8",
    "Mjukvara":      "7c5cff", "Fintech/Krypto":"f7931a", "Bio":           "2ecc71",
    "Mega":          "9aa7b5", "Koppar":        "d2691e", "Silver/Guld":   "c0c0c0",
    "Sverige":       "006aa7", "Bevakning":     "5a6678",
}
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

# =====================================================================
#  CSS — trading-terminal-layout (Space Grotesk + glassmorphism)
# =====================================================================
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap');
html, body, [class*="css"] {{ font-family:'Inter',-apple-system,sans-serif; }}
h1,h2,h3,h4 {{ font-family:'Space Grotesk','Inter',sans-serif; color:#fff; font-weight:700; letter-spacing:-.5px; }}
.stApp h2, .stApp h3 {{ font-size:1.35rem; }}

.stApp {{
  background:
    radial-gradient(900px 520px at 100% -5%, rgba(17,153,250,.10), transparent 55%),
    radial-gradient(700px 420px at -5% 0%, rgba(88,213,224,.06), transparent 50%),
    linear-gradient(180deg, #0b0f16 0%, #0c1018 100%);
  background-attachment: fixed;
}}
section[data-testid="stSidebar"] {{ background:rgba(8,11,17,.92); border-right:1px solid rgba(255,255,255,.05); }}

.stTabs [data-baseweb="tab-list"] {{ gap:6px; border-bottom:0; flex-wrap:wrap; }}
.stTabs [data-baseweb="tab"] {{ background:rgba(19,25,34,.7); color:{MUTED}; border-radius:999px;
    padding:7px 16px; font-weight:600; font-size:.82rem; border:1px solid rgba(255,255,255,.05); }}
.stTabs [aria-selected="true"] {{ background:linear-gradient(135deg,{ACCENT},#58d5e0);
    color:#04121f; border:1px solid transparent; }}

.pill {{ display:inline-block; padding:4px 12px; border-radius:999px;
        font-size:.74rem; font-weight:700; letter-spacing:.4px; }}

.sstrip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px; margin:8px 0 16px; }}
.scard {{ background:rgba(19,25,34,.92); border:1px solid rgba(255,255,255,.05); border-radius:20px;
    padding:16px 18px; backdrop-filter:blur(16px);
    box-shadow:0 8px 24px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.04);
    transition:transform .2s ease, box-shadow .2s ease, border-color .2s ease; }}
.scard:hover {{ transform:translateY(-2px); border-color:rgba(17,153,250,.25);
    box-shadow:0 14px 38px rgba(0,0,0,.45), 0 0 22px rgba(17,153,250,.10); }}
.sl {{ color:{MUTED}; font-size:.66rem; text-transform:uppercase; letter-spacing:.7px; }}
.sv {{ font-size:1.5rem; font-weight:800; margin-top:3px; font-family:'Space Grotesk',sans-serif; }}

.mgrid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(100px,1fr)); gap:10px; margin:14px 0; }}
.mcard {{ background:rgba(13,17,24,.85); border:1px solid rgba(255,255,255,.05); border-radius:16px;
    padding:11px 13px; box-shadow:inset 0 1px 0 rgba(255,255,255,.03); }}
.ml {{ color:{MUTED}; font-size:.64rem; text-transform:uppercase; letter-spacing:.6px; }}
.mv {{ font-size:1.1rem; font-weight:700; margin-top:3px; font-family:'Space Grotesk',sans-serif; }}

.ring {{ width:94px; height:94px; border-radius:50%; display:flex; align-items:center;
        justify-content:center; flex:none; }}
.ring-inner {{ width:76px; height:76px; border-radius:50%; background:#0c1018;
    display:flex; flex-direction:column; align-items:center; justify-content:center; }}
.ring-num {{ font-family:'Space Grotesk',sans-serif; font-size:1.5rem; font-weight:700; line-height:1; }}
.ring-lab {{ font-size:.56rem; color:{MUTED}; text-transform:uppercase; letter-spacing:.5px; margin-top:2px; }}

[data-testid="stDataFrame"] {{ border-radius:16px; overflow:hidden; border:1px solid rgba(255,255,255,.05); }}
</style>
""", unsafe_allow_html=True)

# =====================================================================
#  NEWS TICKER  (feedparser — syns överst om feedparser är installerat)
# =====================================================================
@st.cache_data(ttl=300, show_spinner=False)
def _get_market_news():
    if feedparser is None:
        return []
    try:
        feed = feedparser.parse("https://www.financialjuice.com/feed.ashx?xy=rss")
        return [e.title for e in feed.entries[:20]]
    except Exception:
        return []

def render_news_ticker():
    news = _get_market_news()
    if not news:
        return
    txt = " \u2022 ".join(news)
    st.markdown(
        f'<div style="overflow:hidden;background:rgba(19,25,34,.92);border-radius:20px;'
        f'padding:11px 18px;margin-bottom:14px;border:1px solid rgba(255,255,255,.06);'
        f'color:#eaf2ff;white-space:nowrap;font-weight:600;font-size:.82rem">'
        f'\u26a1 {txt}</div>',
        unsafe_allow_html=True)

# =====================================================================
#  LOGGA
# =====================================================================
LOGO_PATH = "logo.png"
lc1, lc2 = st.columns([1, 3])
with lc1:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, use_container_width=True)
    else:
        st.markdown(f"<h1 style='margin:0'>MONEY<span style='color:{ACCENT}'>GRAB</span></h1>",
                    unsafe_allow_html=True)

render_news_ticker()

# =====================================================================
#  SIDOPANEL
# =====================================================================
st.sidebar.markdown("### Inställningar")
WATCHLIST_DEFAULT = ("NVDA, QUBT, USAR, OUST, OKLO, NBIS, CRDO, SIVE.ST, IONQ, RKLB, ASTS, "
                     "ONDS, PLTR, TSLA, RDW, IREN, CRWV, SMR, AAOI, RXRX, SMCI, AEVA, "
                     "DNN, UEC, NVTS, HIMS, DGXX, SHT-B.ST, SMOL.ST, OBDU-B.ST, SUBGEN.ST, "
                     "XOM-B.ST, XFAB.PA, SKYT, TERRNT-B.ST, VISC.ST, ENAFF")
wl_raw = st.sidebar.text_area("Min watchlist", WATCHLIST_DEFAULT, height=120)
watchlist = [t.strip().upper() for t in wl_raw.replace("\n", ",").split(",") if t.strip()]
theme_sel = st.sidebar.multiselect("Filtrera teman", list(UNIVERSE.keys()),
                                   default=list(UNIVERSE.keys()))
selected = sorted({t for k in theme_sel for t in UNIVERSE[k]})
if st.sidebar.button("Tvinga refresh av data", type="primary"):
    st.cache_data.clear()
    st.rerun()

# =====================================================================
#  HJÄLPARE
# =====================================================================
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


@st.cache_data(ttl=900, show_spinner=False)
def market_movers(source_key):
    if yf is None:
        return []
    res = None
    try:
        res = yf.screen(source_key)
    except Exception:
        try:
            from yfinance import Screener
            s = Screener()
            s.set_predefined_body(source_key)
            res = s.response
        except Exception:
            return []
    quotes = res.get("quotes", []) if isinstance(res, dict) else []
    syms = []
    for q in quotes:
        sym = q.get("symbol", "") if isinstance(q, dict) else ""
        if sym and all(ch not in sym for ch in (".", "-", "=", "^")):
            syms.append(sym.upper())
    return syms[:35]


def logo_url(ticker: str):
    color = THEME_COLOR.get(TICKER_THEME.get(ticker, ""), "1c2330")
    name = "".join(ch for ch in ticker if ch.isalpha())[:2] or ticker[:2]
    q = urllib.parse.urlencode({"name": name, "background": color, "color": "ffffff",
                                "bold": "true", "size": "64", "rounded": "true"})
    return f"https://ui-avatars.com/api/?{q}"


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
        "Poäng": int(a["score10"]), "Pris": float(a["last"]),
        "5d": float(a.get("ret_5", 0)), "1mån": float(a["ret_20"]),
        "Vol": float(a["rel_vol"]),
    } for a in rows])
    h = min(len(df) * 35 + 38, 560)
    ev = st.dataframe(
        df, hide_index=True, use_container_width=True, height=h,
        on_select="rerun", selection_mode="single-row", key=f"grid_{key}",
        column_config={
            "Logo": st.column_config.ImageColumn("", width="small"),
            "Ticker": st.column_config.TextColumn("Ticker", width="small"),
            "Läge": st.column_config.TextColumn("Läge"),
            "Poäng": st.column_config.NumberColumn("Poäng", format="%d/10"),
            "Pris": st.column_config.NumberColumn("Pris", format="%.2f"),
            "5d": st.column_config.NumberColumn("5d", format="%+.1f%%"),
            "1mån": st.column_config.NumberColumn("1mån", format="%+.1f%%"),
            "Vol": st.column_config.NumberColumn("Vol", format="%.1fx"),
        })
    sel = ev.selection.rows if (ev and ev.selection) else []
    if sel:
        st.session_state["detail_req"] = df.iloc[sel[0]]["Ticker"]


# =====================================================================
#  AI SCORE COMPONENTS  (Danelfin-stil, från moneygrab_pro_v4)
# =====================================================================
def ai_score_components(a):
    """Danelfin-stil multi-faktor scoring. Returnerar dict med delscore 0-10."""
    tech = 0
    tech += 3 if a["last"] > a["ema50"] else 0
    tech += 3 if a["last"] > a["ema200"] else 0
    tech += 2 if 45 <= a["rsi"] <= 75 else 0
    tech += 2 if a["ret_20"] > 0 else 0
    tech = min(10, tech)

    momentum = min(10, max(0, int(a["momentum"] / 3.5)))
    sentiment = min(10, max(1, int(a["rel_vol"] * 4)))

    timing = 8
    if a["rel_vol"] < 1.2:   timing -= 3
    if a["pct_from_high"] > -5: timing -= 2

    risk = 8
    if a["atr_pct"] > 12: risk -= 3
    if a["rsi"] > 78:     risk -= 2

    fund = 5
    total = round((tech + momentum + sentiment + timing + risk + fund) / 6, 1)

    return {
        "ai_score":       total,
        "technical":      tech,
        "momentum_score": momentum,
        "sentiment":      sentiment,
        "fundamental":    fund,
        "risk":           risk,
        "timing":         max(1, timing),
    }


# =====================================================================
#  TRADE MOTOR V2  (från moneygrab_pro_v4)
# =====================================================================
def trade_motor_v2(a):
    """Entry/breakout/fakeout/exit quality med orsaker och risker."""
    entry_q = 10; breakout_q = 10; fakeout_r = 2; exit_r = 2
    reasons = []; risks = []

    if a.get("rel_vol", 0) < 1.2:
        entry_q -= 3; breakout_q -= 2; fakeout_r += 2
        risks.append("Låg relativ volym")
    if a.get("pct_from_high", -100) > -5:
        entry_q -= 2
        risks.append("Nära motstånd/topp")
    if a.get("rsi", 0) > 75:
        entry_q -= 2; exit_r += 2
        risks.append("Utsträckt RSI")
    if a.get("ret_20", 0) > 30:
        exit_r += 2
        risks.append("Parabolisk rörelse")

    if a.get("last", 0) > a.get("ema50", 999999):   reasons.append("Över EMA50")
    if a.get("last", 0) > a.get("ema200", 999999):  reasons.append("Över EMA200")
    if a.get("ret_5", 0) > 5:                       reasons.append("Stark 5d-fart")
    if a.get("momentum", 0) > 20:                   reasons.append("Momentum starkt")

    confidence = round(
        (entry_q + breakout_q + (10 - fakeout_r) + (10 - exit_r)) / 4, 1)

    return {
        "entry_quality":    max(1, entry_q),
        "breakout_quality": max(1, breakout_q),
        "fakeout_risk":     min(10, fakeout_r),
        "exit_risk":        min(10, exit_r),
        "confidence":       confidence,
        "reasons":          reasons,
        "risks":            risks,
    }


# =====================================================================
#  RENDER-HJÄLPARE FÖR DETALJVYN
# =====================================================================
def _escore_col(v, good_high=True):
    if good_high:
        return POS if v >= 70 else (ACCENT if v >= 50 else (MUTED if v >= 30 else NEG))
    return NEG if v >= 65 else (ROCK_C if v >= 40 else POS)


def render_engine(e):
    """Renderar breakout_engine-resultat (entry/exit/swing) i detaljvyn."""
    st.markdown("#### Trade-motor")
    ent, ex = e["entry"], e["exit"]
    bcol = _escore_col(e["breakout_score"])
    ccol = _escore_col(e["confidence"])
    ecol = _escore_col(ex["risk"], False)
    scol = _escore_col(e["swing_score"])

    def _ring(v, lab, col):
        deg = int(max(0, min(100, v)) / 100 * 360)
        return (f"<div class='ring' style='background:conic-gradient({col} {deg}deg,"
                f"rgba(255,255,255,.06) {deg}deg)'><div class='ring-inner'>"
                f"<div class='ring-num' style='color:{col}'>{v}</div>"
                f"<div class='ring-lab'>{lab}</div></div></div>")

    st.markdown(
        f"<div style='display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:6px 0 14px'>"
        f"{_ring(e['breakout_score'], 'Breakout', bcol)}"
        f"{_ring(e['confidence'], 'Confidence', ccol)}"
        f"<div style='flex:1;min-width:220px;display:grid;grid-template-columns:1fr 1fr;gap:10px'>"
        f"<div class='mcard'><div class='ml'>Exit-risk</div>"
        f"<div class='mv' style='color:{ecol}'>{ex['risk']}/100</div></div>"
        f"<div class='mcard'><div class='ml'>Swing</div>"
        f"<div class='mv' style='color:{scol}'>{e['swing_score']}/100</div></div>"
        f"</div></div>", unsafe_allow_html=True)

    fcol = {"HÖG": NEG, "MEDEL": ROCK_C, "LÅG": POS}[e["fake_risk"]]
    st.markdown(f"<div style='margin:2px 0 10px'>Setup: <b>{e['setup']}</b> · "
                f"Fake-breakout-risk: <span style='color:{fcol};font-weight:700'>{e['fake_risk']}</span>"
                f"</div>", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    rr_col = POS if ent["rr"] >= 2 else (ROCK_C if ent["rr"] >= 1 else NEG)
    c1.markdown(
        f"<div class='mcard'><div class='ml'>ENTRY-PLAN</div>"
        f"<div style='font-size:.9rem;line-height:1.8;margin-top:4px'>"
        f"Aggressiv: <b>{ent['aggressive']}</b><br>Bekräftad: <b>{ent['confirmed']}</b><br>"
        f"Retest: <b>{ent['retest']}</b><br>Stop: <b style='color:{NEG}'>{ent['stop']}</b><br>"
        f"Mål 1: <b style='color:{POS}'>{ent['t1']}</b> · Mål 2: <b style='color:{POS}'>{ent['t2']}</b><br>"
        f"RR: <b style='color:{rr_col}'>{ent['rr']}</b></div></div>", unsafe_allow_html=True)
    reasons = ", ".join(ex["reasons"]) if ex["reasons"] else "inga tydliga varningar"
    c2.markdown(
        f"<div class='mcard'><div class='ml'>EXIT-PLAN</div>"
        f"<div style='font-size:.9rem;line-height:1.8;margin-top:4px'>"
        f"Exit-risk: <b style='color:{_escore_col(ex['risk'], False)}'>{ex['risk']}/100</b><br>"
        f"Åtgärd: <b>{ex['action']}</b><br>Trailing stop: <b>{ex['trail']}</b><br>"
        f"Tecken: {reasons}</div></div>", unsafe_allow_html=True)

    bars = "".join(
        f"<div style='margin:3px 0'><span style='display:inline-block;width:84px;color:{MUTED};"
        f"font-size:.78rem'>{k}</span>"
        f"<span style='display:inline-block;height:8px;width:{int(val*4)}px;max-width:160px;"
        f"background:{ACCENT};border-radius:4px;vertical-align:middle'></span>"
        f"<span style='font-size:.78rem;color:{TXT};margin-left:6px'>{val}</span></div>"
        for k, val in e["components"].items())
    st.markdown(f"<div style='margin:10px 0'><div class='ml'>POÄNG-FÖRDELNING</div>{bars}</div>",
                unsafe_allow_html=True)

    if e["explain"]:
        st.markdown("<div class='ml' style='margin-top:6px'>VARFÖR</div>", unsafe_allow_html=True)
        lines = "".join(
            f"<div style='font-size:.85rem'><span style='color:{POS if p>0 else NEG};"
            f"font-weight:700'>{'+' if p>0 else ''}{p}</span> {txt}</div>"
            for txt, p in e["explain"][:8])
        st.markdown(lines, unsafe_allow_html=True)


def render_ai_score_panel(a):
    """Renderar Danelfin-stil AI Score + Trade Motor i detaljvyn."""
    sc = ai_score_components(a)
    tm = trade_motor_v2(a)

    st.markdown("#### AI Score")
    cells = "".join(
        f"<div class='mcard'><div class='ml'>{l}</div>"
        f"<div class='mv' style='color:{ACCENT}'>{v}/10</div></div>"
        for l, v in [
            ("AI Score",  sc["ai_score"]),
            ("Technical", sc["technical"]),
            ("Momentum",  sc["momentum_score"]),
            ("Sentiment", sc["sentiment"]),
            ("Risk",      sc["risk"]),
            ("Timing",    sc["timing"]),
        ])
    st.markdown(f"<div class='mgrid'>{cells}</div>", unsafe_allow_html=True)
    st.progress(min(sc["ai_score"] / 10, 1.0))

    st.markdown("#### Trade Motor")
    cells2 = "".join(
        f"<div class='mcard'><div class='ml'>{l}</div>"
        f"<div class='mv' style='color:{c}'>{v}/10</div></div>"
        for l, v, c in [
            ("Entry",    tm["entry_quality"],    POS if tm["entry_quality"] >= 7 else ROCK_C),
            ("Breakout", tm["breakout_quality"], POS if tm["breakout_quality"] >= 7 else ROCK_C),
            ("Fakeout",  tm["fakeout_risk"],     NEG if tm["fakeout_risk"] >= 6 else POS),
            ("Exit-risk",tm["exit_risk"],        NEG if tm["exit_risk"] >= 6 else POS),
            ("Confidence",tm["confidence"],      POS if tm["confidence"] >= 7 else ROCK_C),
        ])
    st.markdown(f"<div class='mgrid'>{cells2}</div>", unsafe_allow_html=True)
    st.progress(min(tm["confidence"] / 10, 1.0))

    c1, c2 = st.columns(2)
    if tm["reasons"]:
        c1.markdown("<div class='ml'>PLUS</div>" +
                    "".join(f"<div style='font-size:.85rem;color:{POS}'>+ {r}</div>"
                            for r in tm["reasons"]), unsafe_allow_html=True)
    if tm["risks"]:
        c2.markdown("<div class='ml'>RISKER</div>" +
                    "".join(f"<div style='font-size:.85rem;color:{NEG}'>- {r}</div>"
                            for r in tm["risks"]), unsafe_allow_html=True)


# =====================================================================
#  DETALJVY  (modal — öppnas via @st.dialog)
# =====================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def company_info(ticker):
    if yf is None:
        return {}
    try:
        info = yf.Ticker(ticker).info or {}
        if not isinstance(info, dict):
            return {}
        return {
            "name":    info.get("longName") or info.get("shortName") or "",
            "sector":  info.get("sector") or info.get("industry") or "",
            "mcap":    info.get("marketCap"),
            "currency":info.get("currency") or "",
            "summary": info.get("longBusinessSummary") or "",
            "country": info.get("country") or "",
        }
    except Exception:
        return {}


def _fmt_mcap(v):
    if not v:       return "—"
    if v >= 1e12:   return f"{v/1e12:.1f}T"
    if v >= 1e9:    return f"{v/1e9:.1f}B"
    if v >= 1e6:    return f"{v/1e6:.0f}M"
    return f"{v:,.0f}"


def price_chart(df, color):
    try:
        closes = df["Close"].tail(90)
        chart_df = pd.DataFrame({"Pris": closes.values}, index=closes.index)
        st.area_chart(chart_df, color=color, height=220)
    except Exception:
        pass


@st.dialog(" ", width="large")
def show_detail(ticker):
    a = scan(ticker)
    if not a:
        st.error(f"Hittade ingen data för {ticker}.")
        return
    info = company_info(ticker) or {}
    try:
        df_full, _ = fetch(ticker)
    except Exception:
        df_full = None

    st.session_state["sok_context"] = {
        "ticker": ticker, "label": a["label"], "score10": a["score10"],
        "last": a["last"], "rsi": a["rsi"], "pct_from_high": a["pct_from_high"],
        "ret_20": a["ret_20"], "rel_vol": a["rel_vol"],
        "strength": a["strength"], "momentum": a["momentum"], "setup": a["setup"],
    }

    # ---- RUBRIK ----
    top = st.columns([2, 1, 1])
    top[0].markdown(f"## {ticker}")
    if info.get("name") and info["name"] != ticker:
        top[0].caption(f"{info['name']} · {info.get('sector','—')}")
    top[0].markdown(
        f"<span class='pill' style='background:{a['color']}22;color:{a['color']};"
        f"border:1px solid {a['color']}55'>{a['label']}</span>", unsafe_allow_html=True)
    top[1].metric("Pris", f"{a['last']:.2f}")
    top[2].metric("Lyftchans", f"{a['score10']}/10")

    # ---- STOR 5D-RÖRELSE ----
    r5 = a["ret_5"]
    r5_color = POS if r5 >= 0 else NEG
    arrow = "▲" if r5 >= 0 else "▼"
    st.markdown(
        f"<div style='display:flex;gap:24px;align-items:baseline;margin:8px 0 4px;flex-wrap:wrap'>"
        f"<div><div style='font-size:12px;color:{MUTED};letter-spacing:1px'>SENASTE 5 DAGARNA</div>"
        f"<div style='font-size:40px;font-weight:800;line-height:1;color:{r5_color}'>"
        f"{arrow} {r5:+.1f}%</div></div>"
        f"<div style='align-self:flex-end'><span style='font-size:13px;color:{MUTED}'>20 dagar: </span>"
        f"<span style='font-size:18px;font-weight:700;color:{POS if a['ret_20']>=0 else NEG}'>"
        f"{a['ret_20']:+.1f}%</span></div>"
        f"<div style='align-self:flex-end'><span style='font-size:13px;color:{MUTED}'>Börsvärde: </span>"
        f"<span style='font-size:18px;font-weight:700;color:{TXT}'>"
        f"{_fmt_mcap(info.get('mcap'))} {info.get('currency','')}</span></div>"
        f"</div>", unsafe_allow_html=True)

    # ---- KURSGRAF ----
    if df_full is not None:
        price_chart(df_full, a["color"])

    # ---- NYCKELTAL ----
    items = [
        ("RSI",        f"{a['rsi']:.0f}",              TXT),
        ("20d",        f"{a['ret_20']:+.1f}%",          POS if a["ret_20"] >= 0 else NEG),
        ("5d",         f"{a['ret_5']:+.1f}%",           POS if a["ret_5"] >= 0 else NEG),
        ("Mot topp",   f"{a['pct_from_high']:+.1f}%",   MUTED),
        ("52v-range",  f"{a['rng_pos']:.0f}%",          TXT),
        ("Rel.vol",    f"{a['rel_vol']:.2f}x",          TXT),
        ("Volatilitet",f"{a['atr_pct']:.1f}%",          TXT),
        ("EMA50",      "över"  if a["last"] > a["ema50"]  else "under",
                       POS     if a["last"] > a["ema50"]  else NEG),
        ("EMA200",     "över"  if a["last"] > a["ema200"] else "under",
                       POS     if a["last"] > a["ema200"] else NEG),
    ]
    cells = "".join(f"<div class='mcard'><div class='ml'>{l}</div>"
                    f"<div class='mv' style='color:{c}'>{v}</div></div>" for l, v, c in items)
    st.markdown(f"<div class='mgrid'>{cells}</div>", unsafe_allow_html=True)
    st.progress(min(a["total"] / 100, 1.0),
                text=f"Score {a['total']:.0f}/100 · Styrka {a['strength']:.0f}/40 · "
                     f"Momentum {a['momentum']:.0f}/35 · Setup {a['setup']:.0f}/25")

    # ---- BREAKOUT ENGINE (om tillgänglig) ----
    if df_full is not None and engine_evaluate is not None:
        try:
            _bench, _ = fetch("^GSPC")
        except Exception:
            _bench = None
        try:
            eng = engine_evaluate(df_full, _bench["Close"] if _bench is not None else None)
        except Exception:
            eng = None
        if eng:
            render_engine(eng)

    # ---- AI SCORE + TRADE MOTOR (alltid tillgänglig) ----
    render_ai_score_panel(a)

    # ---- OM BOLAGET ----
    if info.get("summary"):
        with st.expander(f"Om {info.get('name', ticker)}"):
            land = f" · {info['country']}" if info.get("country") else ""
            st.caption(f"{info.get('sector','—')}{land}")
            st.write(info["summary"])

    tradingview_chart(guess_tv_symbol(ticker), height=360)
    st.caption(f"Öppna **Ask Grabit**-fliken för att fråga AI:n om {ticker}.")


# =====================================================================
#  MARKNADSLÄGE  (index-pills)
# =====================================================================
@st.cache_data(ttl=1800, show_spinner=False)
def regime_of(ticker):
    try:
        df, _ = fetch(ticker)
    except Exception:
        return "BLANDAD", 0.0
    if df is None or len(df) < 200:
        return "BLANDAD", 0.0
    c = df["Close"].dropna()
    last = float(c.iloc[-1]); ma200 = float(c.rolling(200).mean().iloc[-1])
    ma50  = float(c.rolling(50).mean().iloc[-1])
    pct   = (last / ma200 - 1) * 100
    if last > ma200 and ma50 > ma200: return "BULL", pct
    if last < ma200:                  return "BEAR", pct
    return "BLANDAD", pct

INDICES = [("S&P 500", "^GSPC"), ("Nasdaq", "^IXIC"), ("Stockholm", "^OMX")]
pills = []
for iname, itk in INDICES:
    lbl, pct = regime_of(itk)
    col = {"BULL": POS, "BEAR": NEG, "BLANDAD": MUTED}[lbl]
    pills.append(
        f"<span class='pill' style='background:{col}22;color:{col};border:1px solid {col}55'>"
        f"{iname}: {lbl}</span>"
        f"<span style='color:{MUTED};font-size:.78rem;margin:0 16px 0 6px'>{pct:+.1f}% mot 200d</span>")
st.markdown("<div style='margin:.3rem 0 1.1rem;display:flex;flex-wrap:wrap;"
            "gap:4px 0;align-items:center'>" + "".join(pills) + "</div>",
            unsafe_allow_html=True)

# =====================================================================
#  FLIKAR
# =====================================================================
tabs = st.tabs(["HETA NU", "VÄNDNINGAR", "VARNINGAR", "WATCHLIST",
                "MARKNAD", "SÖK", "ASK GRABIT", "MACRO", "RÅVAROR"])


def collect(tickers, keep_labels=None, warn=False):
    out = []
    prog = st.progress(0.0)
    for i, t in enumerate(tickers):
        a = scan(t)
        prog.progress((i + 1) / max(1, len(tickers)))
        if not a:
            continue
        if warn:
            if a["label"] in {"BEAR", "SVAG", "AVSVALNING"} or a["rsi"] > 78 or a["ret_20"] > 35:
                out.append(a)
        elif keep_labels is None or a["label"] in keep_labels:
            out.append(a)
    prog.empty()
    return out


with tabs[0]:
    if render_dagens_bull is not None:
        render_dagens_bull()
        st.divider()
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
            if a["label"] == "AVSVALNING": return "RULLAR ÖVER"
            if a["label"] == "BEAR":       return "BEAR"
            if a["rsi"] > 78:              return "ÖVERKÖPT"
            if a["ret_20"] > 35:           return "PARABOL"
            return "SVAG"
        r.sort(key=lambda x: x["rsi"], reverse=True)
        df_w = pd.DataFrame([{
            "Logo": logo_url(a["ticker"]), "Ticker": a["ticker"], "Flagga": warnflag(a),
            "RSI": int(round(a["rsi"])), "20d": float(a["ret_20"]), "Pris": float(a["last"]),
        } for a in r])
        cfg_w = {
            "Logo":   st.column_config.ImageColumn("", width="small"),
            "Ticker": st.column_config.TextColumn("Ticker", width="small"),
            "Flagga": st.column_config.TextColumn("Flagga"),
            "RSI":    st.column_config.NumberColumn("RSI", format="%d"),
            "20d":    st.column_config.NumberColumn("20d", format="%.1f%%"),
            "Pris":   st.column_config.NumberColumn("Pris", format="%.2f"),
        }
        ev_w = st.dataframe(df_w, column_config=cfg_w, hide_index=True, use_container_width=True,
                            height=min(len(df_w) * 36 + 40, 560),
                            on_select="rerun", selection_mode="single-row", key="warn")
        sel_w = ev_w.selection.rows if (ev_w and ev_w.selection) else []
        if sel_w:
            st.session_state["detail_req"] = df_w.iloc[sel_w[0]]["Ticker"]

with tabs[3]:
    st.subheader("Watchlist — dina kärnnamn")
    r = collect(watchlist)
    render_stats(r); render_grid(r, "wl")

with tabs[4]:
    st.subheader("Marknad — det som rör sig idag")
    SOURCES = {
        "Dagens vinnare":   "day_gainers",
        "Mest omsatta":     "most_actives",
        "Small-cap raketer":"small_cap_gainers",
        "Tillväxt-tech":    "growth_technology_stocks",
        "Dagens förlorare": "day_losers",
    }
    csrc, cflt = st.columns([3, 2])
    src_name   = csrc.radio("Källa", list(SOURCES.keys()), horizontal=True, key="mkt_src")
    only_entry = cflt.checkbox("Visa bara entry-lägen", value=True, key="mkt_entry")
    if yf is None:
        st.error("yfinance saknas — kan inte scanna marknaden.")
    else:
        syms = market_movers(SOURCES[src_name])
        if not syms:
            st.warning("Kunde inte hämta marknadsdata just nu. Prova en annan källa eller tryck refresh.")
        else:
            r = []
            prog = st.progress(0.0)
            for i, sym in enumerate(syms):
                a = scan(sym)
                prog.progress((i + 1) / len(syms))
                if a:
                    r.append(a)
            prog.empty()
            if only_entry:
                r = [a for a in r if a["label"] in {"MOMENTUM", "BULL", "Rocketcase", "VÄNDNING"}]
            st.caption(f"{src_name} · {len(r)} träffar. Klicka en rad för detaljer.")
            render_stats(r)
            render_grid(r, "market")

with tabs[5]:
    render_sok_tab()

with tabs[6]:
    render_ai_tab()

with tabs[7]:
    st.subheader("Macro")
    macro = {"S&P 500":"^GSPC","Nasdaq 100":"^NDX","VIX":"^VIX",
             "US 10y":"^TNX","Dollar (DXY)":"DX-Y.NYB"}
    cols = st.columns(len(macro))
    for col, (name, tk) in zip(cols, macro.items()):
        try:
            df_m, _ = fetch(tk)
        except Exception:
            df_m = None
        if df_m is not None and len(df_m) > 1:
            c = df_m["Close"].dropna()
            chg = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
            col.metric(name, f"{float(c.iloc[-1]):.2f}", f"{chg:+.2f}%")
        else:
            col.metric(name, "—")

with tabs[8]:
    st.subheader("Råvaror")
    comm = {"Guld":"GC=F","Silver":"SI=F","Koppar":"HG=F","Olja (WTI)":"CL=F","Uran (URA)":"URA"}
    cols = st.columns(len(comm))
    for col, (name, tk) in zip(cols, comm.items()):
        try:
            df_c, _ = fetch(tk)
        except Exception:
            df_c = None
        if df_c is not None and len(df_c) > 1:
            c = df_c["Close"].dropna()
            chg = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
            col.metric(name, f"{float(c.iloc[-1]):.2f}", f"{chg:+.2f}%")
        else:
            col.metric(name, "—")

st.divider()
st.caption("MoneyGrab · teknisk signalering på pris/volym, ingen finansiell rådgivning.")

# =====================================================================
#  DETALJVY — triggas en gång (undviker dubbel-dialog från flera grids)
# =====================================================================
_req = st.session_state.get("detail_req")
if _req and _req != st.session_state.get("detail_shown"):
    st.session_state["detail_shown"] = _req
    show_detail(_req)
