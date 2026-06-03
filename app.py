# =====================================================================
#  MONEYGRAB  —  huvudfil
#  Bas: app.py + moneygrab_pro_v4 pro-tillägg fullt integrerade.
#  Buggfixar: company_info cachas separat och anropas utanför dialog,
#             bolagsnamn visas i grid + detaljvy.
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

try:
    from featured_picks import render_featured_picks
except Exception:
    render_featured_picks = None

try:
    from compare_module import render_compare_tab, ranked_rows
except Exception:
    render_compare_tab = None
    ranked_rows = None

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

# Gör UNIVERSE + färger tillgängliga för dagens_bull.py utan cirkulär import
st.session_state["_mg_universe"]     = UNIVERSE
st.session_state["_mg_ticker_theme"] = TICKER_THEME
st.session_state["_mg_theme_color"]  = THEME_COLOR

# =====================================================================
#  CSS
# =====================================================================
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap');
html, body, [class*="css"] {{ font-family:'Inter',-apple-system,sans-serif; }}
h1,h2,h3,h4 {{ font-family:'Space Grotesk','Inter',sans-serif; color:#fff; font-weight:700; letter-spacing:-.5px; }}
.stApp h2, .stApp h3 {{ font-size:1.35rem; }}
.stApp {{
  background:
    radial-gradient(1100px 640px at 100% -8%, rgba(17,153,250,.16), transparent 58%),
    radial-gradient(900px 560px at -8% 4%, rgba(88,213,224,.09), transparent 54%),
    radial-gradient(700px 700px at 50% 120%, rgba(45,95,200,.10), transparent 60%),
    linear-gradient(180deg, #070a11 0%, #0a0e16 45%, #080b12 100%);
  background-attachment: fixed;
}}
section[data-testid="stSidebar"] {{ background:rgba(7,10,17,.94); border-right:1px solid rgba(255,255,255,.06); }}
.stTabs [data-baseweb="tab-list"] {{ gap:6px; border-bottom:0; flex-wrap:wrap; }}
.stTabs [data-baseweb="tab"] {{ background:rgba(19,25,34,.6); color:{MUTED}; border-radius:999px;
    padding:7px 16px; font-weight:600; font-size:.82rem; border:1px solid rgba(255,255,255,.06);
    transition:all .18s ease; }}
.stTabs [data-baseweb="tab"]:hover {{ border-color:rgba(17,153,250,.3); color:#cfe6ff; }}
.stTabs [aria-selected="true"] {{ background:linear-gradient(135deg,{ACCENT},#58d5e0);
    color:#04121f; border:1px solid transparent; box-shadow:0 4px 18px rgba(17,153,250,.3); }}
.pill {{ display:inline-block; padding:4px 12px; border-radius:999px;
        font-size:.74rem; font-weight:700; letter-spacing:.4px; }}
.sstrip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px; margin:8px 0 16px; }}
.scard {{ background:linear-gradient(160deg, rgba(22,28,40,.94), rgba(14,18,27,.94));
    border:1px solid rgba(255,255,255,.06); border-radius:22px;
    padding:16px 18px; backdrop-filter:blur(18px);
    box-shadow:0 10px 30px rgba(0,0,0,.4), inset 0 1px 0 rgba(255,255,255,.05);
    transition:transform .2s ease, box-shadow .2s ease, border-color .2s ease; }}
.scard:hover {{ transform:translateY(-2px); border-color:rgba(17,153,250,.28);
    box-shadow:0 16px 44px rgba(0,0,0,.5), 0 0 26px rgba(17,153,250,.12); }}
.sl {{ color:{MUTED}; font-size:.66rem; text-transform:uppercase; letter-spacing:.7px; }}
.sv {{ font-size:1.5rem; font-weight:800; margin-top:3px; font-family:'Space Grotesk',sans-serif; }}
.mgrid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(100px,1fr)); gap:10px; margin:14px 0; }}
.mcard {{ background:rgba(13,17,24,.8); border:1px solid rgba(255,255,255,.05); border-radius:16px;
    padding:11px 13px; box-shadow:inset 0 1px 0 rgba(255,255,255,.04); }}
.ml {{ color:{MUTED}; font-size:.64rem; text-transform:uppercase; letter-spacing:.6px; }}
.mv {{ font-size:1.1rem; font-weight:700; margin-top:3px; font-family:'Space Grotesk',sans-serif; }}
.ring {{ width:94px; height:94px; border-radius:50%; display:flex; align-items:center;
        justify-content:center; flex:none; }}
.ring-inner {{ width:76px; height:76px; border-radius:50%; background:#0c1018;
    display:flex; flex-direction:column; align-items:center; justify-content:center; }}

/* ---- BAREBONE-STIL: hero, skill-kort, stylade knappar/input ---- */
.hero-title {{ font-family:'Space Grotesk',sans-serif; font-size:2.4rem; font-weight:700;
    line-height:1.05; letter-spacing:-1px; text-align:center; margin:8px 0 4px;
    background:linear-gradient(120deg,#fff 30%,#9fd2ff 70%); -webkit-background-clip:text;
    -webkit-text-fill-color:transparent; }}
.hero-sub {{ text-align:center; color:{MUTED}; font-size:.95rem; margin-bottom:18px; }}
.skill-card {{ background:linear-gradient(160deg, rgba(26,33,48,.9), rgba(15,20,30,.9));
    border:1px solid rgba(255,255,255,.07); border-radius:22px; padding:18px 18px 16px;
    height:100%; transition:all .2s ease; cursor:pointer;
    box-shadow:0 8px 26px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.04); }}
.skill-card:hover {{ transform:translateY(-3px); border-color:rgba(17,153,250,.4);
    box-shadow:0 18px 48px rgba(0,0,0,.5), 0 0 30px rgba(17,153,250,.18); }}
.skill-ico {{ width:42px; height:42px; border-radius:12px; display:flex; align-items:center;
    justify-content:center; font-size:1.2rem; margin-bottom:12px;
    background:rgba(17,153,250,.14); border:1px solid rgba(17,153,250,.25); }}
.skill-title {{ font-family:'Space Grotesk',sans-serif; font-size:1.05rem; font-weight:700;
    color:#fff; line-height:1.2; }}
.skill-desc {{ color:{MUTED}; font-size:.82rem; margin-top:6px; line-height:1.4; }}

/* rankade rad-kort (Trending-stil) */
.rank-row {{ background:linear-gradient(160deg, rgba(20,26,38,.85), rgba(13,17,26,.85));
    border:1px solid rgba(255,255,255,.06); border-radius:16px; padding:12px 14px;
    transition:all .18s ease; box-shadow:0 4px 16px rgba(0,0,0,.28); }}
.rank-row:hover {{ border-color:rgba(17,153,250,.3);
    box-shadow:0 10px 30px rgba(0,0,0,.4), 0 0 20px rgba(17,153,250,.12); }}

/* gör Streamlit-knappar i Grabit till glas-stil */
div[data-testid="stButton"] > button {{
    background:linear-gradient(160deg, rgba(26,33,48,.85), rgba(15,20,30,.85));
    border:1px solid rgba(255,255,255,.08); border-radius:16px; color:#dce8f5;
    font-weight:600; padding:12px 16px; transition:all .18s ease;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.04); }}
div[data-testid="stButton"] > button:hover {{
    border-color:rgba(17,153,250,.45); color:#fff; transform:translateY(-1px);
    box-shadow:0 10px 26px rgba(0,0,0,.4), 0 0 22px rgba(17,153,250,.15); }}
div[data-testid="stButton"] > button[kind="primary"] {{
    background:linear-gradient(135deg,{ACCENT},#2d8fd6); border:1px solid transparent;
    color:#fff; box-shadow:0 6px 22px rgba(17,153,250,.35); }}

/* stylat textfält + chat-input med accent-glow */
div[data-testid="stTextInput"] input, div[data-testid="stChatInput"] textarea,
.stChatInput textarea {{
    background:rgba(13,18,28,.9) !important; border:1px solid rgba(17,153,250,.25) !important;
    border-radius:16px !important; color:#eaf2ff !important; }}
div[data-testid="stTextInput"] input:focus {{
    border-color:rgba(17,153,250,.6) !important; box-shadow:0 0 0 3px rgba(17,153,250,.12) !important; }}

[data-testid="stDataFrame"] {{ border-radius:16px; overflow:hidden; border:1px solid rgba(255,255,255,.06); }}
.ring-num {{ font-family:'Space Grotesk',sans-serif; font-size:1.5rem; font-weight:700; line-height:1; }}
.ring-lab {{ font-size:.56rem; color:{MUTED}; text-transform:uppercase; letter-spacing:.5px; margin-top:2px; }}
</style>
""", unsafe_allow_html=True)

# =====================================================================
#  NEWS TICKER
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
#  COMPANY INFO — cachas utanför dialog (Streamlit krav)
# =====================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def company_info(ticker: str) -> dict:
    """Hämtar bolagsnamn, sektor, börsvärde. Cachas 1h. Kraschar aldrig."""
    if yf is None:
        return {}
    try:
        info = yf.Ticker(ticker).info or {}
        if not isinstance(info, dict):
            return {}
        name = (info.get("longName") or info.get("shortName")
                or info.get("displayName") or "")
        return {
            "name":     name,
            "sector":   info.get("sector") or info.get("industry") or "",
            "mcap":     info.get("marketCap"),
            "currency": info.get("currency") or "",
            "summary":  info.get("longBusinessSummary") or "",
            "country":  info.get("country") or "",
            "website":  info.get("website") or "",
        }
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def live_price(ticker: str):
    """Senaste handelspris. Cachas 1 min. Returnerar None vid fel."""
    if yf is None:
        return None
    try:
        info = yf.Ticker(ticker).info or {}
        p = (info.get("regularMarketPrice")
             or info.get("currentPrice")
             or info.get("previousClose"))
        return float(p) if p else None
    except Exception:
        return None

# =====================================================================
#  SCAN + HJÄLPARE
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


def get_short_name(ticker: str) -> str:
    """Returnerar kortnamn för bolaget, max 22 tecken. Tomt om ej hittat."""
    info = company_info(ticker)
    name = info.get("name", "")
    if not name or name.upper() == ticker.upper():
        return ""
    # Förkorta långa namn
    if len(name) > 22:
        name = name[:20].rstrip() + "…"
    return name


# =====================================================================
#  LOGGA (riktig) + FLAGGA + BESKRIVNING
#  Clearbit-logo-API:t stängdes 2025-12-08. Använder logo.dev om en
#  nyckel finns i secrets, annars Googles favicon (ingen inloggning).
#  Monogram som sista fallback via onerror.
# =====================================================================
COUNTRY_ISO = {
    "United States": "us", "Sweden": "se", "Canada": "ca", "United Kingdom": "gb",
    "Germany": "de", "France": "fr", "Netherlands": "nl", "Switzerland": "ch",
    "China": "cn", "Taiwan": "tw", "Japan": "jp", "South Korea": "kr", "Israel": "il",
    "Ireland": "ie", "Norway": "no", "Finland": "fi", "Denmark": "dk", "Australia": "au",
    "India": "in", "Singapore": "sg", "Hong Kong": "hk", "Brazil": "br", "Mexico": "mx",
    "Italy": "it", "Spain": "es", "Bermuda": "bm", "Cayman Islands": "ky", "Luxembourg": "lu",
}


def flag_html(country: str, h: int = 15) -> str:
    code = COUNTRY_ISO.get((country or "").strip())
    if not code:
        return ""
    return (f"<img src='https://flagcdn.com/h40/{code}.png' "
            f"style='height:{h}px;border-radius:2px;vertical-align:-2px;margin-left:8px' "
            f"alt='{country}'/>")


def _logodev_token() -> str:
    """Hämtar logo.dev-nyckel från Streamlit-secrets om den finns. Annars tomt."""
    try:
        return st.secrets.get("LOGODEV_TOKEN", "") or ""
    except Exception:
        return ""


def _domain_from(info: dict) -> str:
    web = (info or {}).get("website", "") or ""
    if not web:
        return ""
    return (web.replace("https://", "").replace("http://", "")
               .replace("www.", "").strip("/").split("/")[0])


def _logo_src(domain: str) -> str:
    if not domain:
        return ""
    tok = _logodev_token()
    if tok:
        return f"https://img.logo.dev/{domain}?token={tok}&size=128&format=png"
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"


def company_logo_html(ticker: str, info: dict, size: int = 46) -> str:
    """Riktig logga via logo.dev/favicon. Monogram-bakgrund som fallback."""
    color = THEME_COLOR.get(TICKER_THEME.get(ticker, ""), "1c2330")
    mono  = ("".join(ch for ch in ticker if ch.isalpha())[:2] or ticker[:2]).upper()
    src   = _logo_src(_domain_from(info))
    img = ""
    if src:
        img = (f"<img src='{src}' onerror=\"this.remove()\" "
               f"style='position:absolute;inset:0;width:100%;height:100%;object-fit:contain;"
               f"padding:6px;background:#0c1018;border-radius:12px'/>")
    return (f"<div style='position:relative;width:{size}px;height:{size}px;border-radius:12px;"
            f"background:#{color}22;border:1px solid #{color}55;display:flex;align-items:center;"
            f"justify-content:center;overflow:hidden;flex:none;"
            f"font-family:\"Space Grotesk\",sans-serif;font-weight:800;font-size:15px;"
            f"color:#{color}'>{mono}{img}</div>")


def short_blurb(text: str, max_len: int = 320) -> str:
    """Kortar bolagsbeskrivning till ~3–4 rader, bryter helst vid meningsslut."""
    if not text:
        return ""
    s = " ".join(text.split())
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    dot = cut.rfind(". ")
    return cut[:dot + 1] if dot > 120 else cut.rstrip() + "…"


def _fmt_mcap(v):
    if not v:       return "—"
    if v >= 1e12:   return f"{v/1e12:.1f}T"
    if v >= 1e9:    return f"{v/1e9:.1f}B"
    if v >= 1e6:    return f"{v/1e6:.0f}M"
    return f"{v:,.0f}"

# =====================================================================
#  GRID + STATS
# =====================================================================
def render_stats(rows):
    n   = len(rows)
    nm  = sum(1 for a in rows if a["label"] == "MOMENTUM")
    nv  = sum(1 for a in rows if a["label"] == "VÄNDNING")
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

    # Vy-toggle: snygga rad-kort (default) eller tät tabell
    view = st.radio("Vy", ["Kort", "Tabell"], horizontal=True, key=f"view_{key}",
                    label_visibility="collapsed")

    if view == "Kort" and ranked_rows is not None:
        chosen = ranked_rows(rows, logo_url, company_info, key=f"rr_{key}")
        if chosen:
            st.session_state["detail_req"] = chosen
        return

    # ---- TABELL-VY (fallback / tät översikt) ----
    grid_data = []
    for a in rows:
        short = get_short_name(a["ticker"])
        display = f"{a['ticker']}  {short}" if short else a["ticker"]
        bos = a.get("bos", "INGEN")
        bos_arrow = "↑" if bos == "BULLISH" else "↓" if bos == "BEARISH" else "–"
        grid_data.append({
            "Logo":   logo_url(a["ticker"]),
            "Bolag":  display,
            "Läge":   a["label"],
            "Setup":  a.get("setup_grade", "C"),
            "BOS":    bos_arrow,
            "Poäng":  int(a["score10"]),
            "Pris":   float(a["last"]),
            "5d":     float(a.get("ret_5", 0)),
            "1mån":   float(a["ret_20"]),
            "Vol":    float(a["rel_vol"]),
            "_ticker": a["ticker"],
        })

    df = pd.DataFrame(grid_data)
    h  = min(len(df) * 35 + 38, 560)

    ev = st.dataframe(
        df.drop(columns=["_ticker"]),
        hide_index=True, use_container_width=True, height=h,
        on_select="rerun", selection_mode="single-row", key=f"grid_{key}",
        column_config={
            "Logo":  st.column_config.ImageColumn("", width="small"),
            "Bolag": st.column_config.TextColumn("Bolag"),
            "Läge":  st.column_config.TextColumn("Läge"),
            "Setup": st.column_config.TextColumn("Setup", help="Setup-kvalitet A/B/C/D (BOS + OB + struktur)"),
            "BOS":   st.column_config.TextColumn("BOS", help="Break of Structure: ↑ bullish, ↓ bearish, – ingen"),
            "Poäng": st.column_config.NumberColumn("Poäng", format="%d/10"),
            "Pris":  st.column_config.NumberColumn("Pris", format="%.2f"),
            "5d":    st.column_config.NumberColumn("5d", format="%+.1f%%"),
            "1mån":  st.column_config.NumberColumn("1mån", format="%+.1f%%"),
            "Vol":   st.column_config.NumberColumn("Vol", format="%.1fx"),
        })
    sel = ev.selection.rows if (ev and ev.selection) else []
    if sel:
        st.session_state["detail_req"] = grid_data[sel[0]]["_ticker"]

# =====================================================================
#  AI SCORE + TRADE MOTOR
# =====================================================================
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
    if a["rel_vol"] < 1.2:        timing -= 3
    if a["pct_from_high"] > -5:   timing -= 2
    risk = 8
    if a["atr_pct"] > 12: risk -= 3
    if a["rsi"] > 78:     risk -= 2
    fund  = 5
    total = round((tech + momentum + sentiment + timing + risk + fund) / 6, 1)
    return {"ai_score": total, "technical": tech, "momentum_score": momentum,
            "sentiment": sentiment, "fundamental": fund, "risk": risk,
            "timing": max(1, timing)}


def trade_motor_v2(a):
    entry_q = 10; breakout_q = 10; fakeout_r = 2; exit_r = 2
    reasons = []; risks = []
    if a.get("rel_vol", 0) < 1.2:
        entry_q -= 3; breakout_q -= 2; fakeout_r += 2
        risks.append("Låg relativ volym")
    if a.get("pct_from_high", -100) > -5:
        entry_q -= 2; risks.append("Nära motstånd/topp")
    if a.get("rsi", 0) > 75:
        entry_q -= 2; exit_r += 2; risks.append("Utsträckt RSI")
    if a.get("ret_20", 0) > 30:
        exit_r += 2; risks.append("Parabolisk rörelse")
    if a.get("last", 0) > a.get("ema50", 999999):  reasons.append("Över EMA50")
    if a.get("last", 0) > a.get("ema200", 999999): reasons.append("Över EMA200")
    if a.get("ret_5", 0) > 5:                      reasons.append("Stark 5d-fart")
    if a.get("momentum", 0) > 20:                  reasons.append("Momentum starkt")
    confidence = round((entry_q + breakout_q + (10 - fakeout_r) + (10 - exit_r)) / 4, 1)
    return {"entry_quality": max(1, entry_q), "breakout_quality": max(1, breakout_q),
            "fakeout_risk": min(10, fakeout_r), "exit_risk": min(10, exit_r),
            "confidence": confidence, "reasons": reasons, "risks": risks}


def render_ai_score_panel(a):
    sc = ai_score_components(a)
    tm = trade_motor_v2(a)
    st.markdown("#### AI Score")
    cells = "".join(
        f"<div class='mcard'><div class='ml'>{l}</div>"
        f"<div class='mv' style='color:{ACCENT}'>{v}/10</div></div>"
        for l, v in [("AI Score", sc["ai_score"]), ("Technical", sc["technical"]),
                     ("Momentum", sc["momentum_score"]), ("Sentiment", sc["sentiment"]),
                     ("Risk", sc["risk"]), ("Timing", sc["timing"])])
    st.markdown(f"<div class='mgrid'>{cells}</div>", unsafe_allow_html=True)
    st.progress(min(sc["ai_score"] / 10, 1.0))

    st.markdown("#### Trade Motor")
    cells2 = "".join(
        f"<div class='mcard'><div class='ml'>{l}</div>"
        f"<div class='mv' style='color:{c}'>{v}/10</div></div>"
        for l, v, c in [
            ("Entry",     tm["entry_quality"],    POS if tm["entry_quality"] >= 7 else ROCK_C),
            ("Breakout",  tm["breakout_quality"], POS if tm["breakout_quality"] >= 7 else ROCK_C),
            ("Fakeout",   tm["fakeout_risk"],     NEG if tm["fakeout_risk"] >= 6 else POS),
            ("Exit-risk", tm["exit_risk"],        NEG if tm["exit_risk"] >= 6 else POS),
            ("Confidence",tm["confidence"],       POS if tm["confidence"] >= 7 else ROCK_C),
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
#  BREAKOUT ENGINE RENDER
# =====================================================================
def _escore_col(v, good_high=True):
    if good_high:
        return POS if v >= 70 else (ACCENT if v >= 50 else (MUTED if v >= 30 else NEG))
    return NEG if v >= 65 else (ROCK_C if v >= 40 else POS)


def render_engine(e):
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
        f"{_ring(e['breakout_score'], 'Breakout', bcol)}{_ring(e['confidence'], 'Confidence', ccol)}"
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
    reasons_txt = ", ".join(ex["reasons"]) if ex["reasons"] else "inga tydliga varningar"
    c2.markdown(
        f"<div class='mcard'><div class='ml'>EXIT-PLAN</div>"
        f"<div style='font-size:.9rem;line-height:1.8;margin-top:4px'>"
        f"Exit-risk: <b style='color:{_escore_col(ex['risk'], False)}'>{ex['risk']}/100</b><br>"
        f"Åtgärd: <b>{ex['action']}</b><br>Trailing stop: <b>{ex['trail']}</b><br>"
        f"Tecken: {reasons_txt}</div></div>", unsafe_allow_html=True)

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


# =====================================================================
#  DETALJVY  — company_info anropas FÖRE @st.dialog, skickas som arg
# =====================================================================
def price_chart(df, color):
    try:
        closes = df["Close"].tail(90)
        chart_df = pd.DataFrame({"Pris": closes.values}, index=closes.index)
        st.area_chart(chart_df, color=color, height=220)
    except Exception:
        pass


@st.dialog(" ", width="large")
def show_detail(ticker: str, info: dict):
    """Renderar detaljvyn. info skickas in färdighämtat — ingen cache inuti dialog."""
    a = scan(ticker)
    if not a:
        st.error(f"Hittade ingen data för {ticker}.")
        return
    try:
        df_full, _ = fetch(ticker)
    except Exception:
        df_full = None

    st.session_state["sok_context"] = {
        "ticker": ticker, "label": a["label"], "score10": a["score10"],
        "last": a["last"], "rsi": a["rsi"], "pct_from_high": a["pct_from_high"],
        "ret_20": a["ret_20"], "rel_vol": a["rel_vol"],
        "strength": a["strength"], "momentum": a["momentum"], "setup": a["setup"],
        "bos": a.get("bos"), "structure": a.get("structure"),
        "setup_grade": a.get("setup_grade"), "setup_score": a.get("setup_score"),
        "ob_support": a.get("ob_support"), "ob_resist": a.get("ob_resist"),
        "atr_up_1": a.get("atr_up_1"), "atr_up_05": a.get("atr_up_05"),
        "atr_dn_05": a.get("atr_dn_05"), "atr_dn_1": a.get("atr_dn_1"),
        "levels_note": a.get("levels_note"),
    }

    # ---- RUBRIK: featured-kort-stil (matchar Veckans urval) ----
    name   = info.get("name", "")
    sector = info.get("sector", "") or ""
    _live = live_price(ticker)
    price_display = _live if _live else a["last"]
    price_label   = "Live" if _live else "Stängning"
    # dagens förändring (senaste stapeln mot föregående stängning)
    day_chg = None
    if df_full is not None:
        _c = df_full["Close"].dropna()
        if len(_c) >= 2 and float(_c.iloc[-2]):
            day_chg = (float(price_display) / float(_c.iloc[-2]) - 1) * 100
    day_col = POS if (day_chg is not None and day_chg >= 0) else NEG
    cur = info.get("currency", "") or ""
    acc = a["color"]
    r5 = a["ret_5"]
    r5_color = POS if r5 >= 0 else NEG
    arrow = "▲" if r5 >= 0 else "▼"
    bos = a.get("bos", "INGEN")
    bos_txt = "BOS↑" if bos == "BULLISH" else "BOS↓" if bos == "BEARISH" else "BOS –"
    grade = a.get("setup_grade", "C")
    disp_name = name if (name and name.upper() != ticker.upper()) else ""
    flag = flag_html(info.get("country", ""))
    sub_name = f"{disp_name}{flag}" if disp_name else (flag or "")

    st.markdown(
        f"<div class='scard' style='border-color:{acc}55;box-shadow:0 0 26px {acc}22;padding:20px'>"
        # rad 1: logo + ticker/namn + score
        f"<div style='display:flex;align-items:center;gap:14px'>"
        f"{company_logo_html(ticker, info, 46)}"
        f"<div style='flex:1;min-width:0'>"
        f"<div style='font-size:1.7rem;font-weight:800;color:{TXT};line-height:1'>{ticker}</div>"
        f"<div style='font-size:.84rem;color:{MUTED};margin-top:3px'>{sub_name}</div>"
        f"</div>"
        f"<div style='text-align:right;flex:none'>"
        f"<div style='font-size:.95rem;font-weight:800;color:{acc};line-height:1'>{a['score10']}/10</div>"
        f"<div style='font-size:.58rem;color:{MUTED};text-transform:uppercase;letter-spacing:.6px;margin-bottom:9px'>lyftchans</div>"
        f"<div style='font-size:2.8rem;font-weight:800;color:{TXT};line-height:.95;font-family:\"Space Grotesk\",sans-serif'>"
        f"{price_display:.2f}<span style='font-size:.85rem;font-weight:600;color:{MUTED};margin-left:4px'>{cur}</span></div>"
        f"<div style='font-size:.82rem;font-weight:700;color:{day_col};margin-top:3px'>"
        f"{(f'{day_chg:+.2f}% · ' if day_chg is not None else '')}{price_label}</div>"
        f"</div></div>"
        # rad 2: pillrar
        f"<div style='margin-top:12px;display:flex;gap:6px;flex-wrap:wrap'>"
        f"<span class='pill' style='background:{acc}22;color:{acc}'>{a['label']}</span>"
        f"<span class='pill' style='background:rgba(255,255,255,.06);color:{TXT}'>{bos_txt}</span>"
        f"<span class='pill' style='background:rgba(255,255,255,.06);color:{TXT}'>setup {grade}</span>"
        f"</div>"
        # rad 3: 5d-rörelse (mindre än priset) + nyckeltal
        f"<div style='display:flex;gap:28px;align-items:baseline;margin-top:16px;flex-wrap:wrap'>"
        f"<div><div style='font-size:11px;color:{MUTED};letter-spacing:1px'>SENASTE 5 DAGARNA</div>"
        f"<div style='font-size:30px;font-weight:800;line-height:1;color:{r5_color}'>{arrow} {r5:+.1f}%</div></div>"
        f"<div style='align-self:flex-end'><span style='font-size:12px;color:{MUTED}'>20d: </span>"
        f"<span style='font-size:18px;font-weight:700;color:{POS if a['ret_20']>=0 else NEG}'>{a['ret_20']:+.1f}%</span></div>"
        f"<div style='align-self:flex-end'><span style='font-size:12px;color:{MUTED}'>Börsvärde: </span>"
        f"<span style='font-size:18px;font-weight:700;color:{TXT}'>{_fmt_mcap(info.get('mcap'))} {cur}</span></div>"
        f"</div>"
        f"</div>", unsafe_allow_html=True)

    # ---- OM BOLAGET (synlig beskrivning + sektor + land) ----
    _blurb = short_blurb(info.get("summary", ""))
    if _blurb or sector or info.get("country"):
        _meta_bits = [b for b in [sector, info.get("country", "")] if b]
        _meta = " · ".join(_meta_bits)
        _meta_line = (f"<div style='font-size:.66rem;color:{MUTED};text-transform:uppercase;"
                      f"letter-spacing:.7px;font-weight:600;margin-bottom:6px'>{_meta}{flag}</div>"
                      if _meta else "")
        _body = (f"<div style='font-size:.86rem;color:#b6c0cc;line-height:1.55'>{_blurb}</div>"
                 if _blurb else "")
        st.markdown(
            f"<div style='margin:12px 0 4px;padding:13px 16px;background:rgba(19,25,34,.55);"
            f"border:1px solid rgba(255,255,255,.05);border-radius:14px'>{_meta_line}{_body}</div>",
            unsafe_allow_html=True)

    # ---- KURSGRAF ----
    if df_full is not None:
        price_chart(df_full, a["color"])

    # ---- NYCKELTAL ----
    items = [
        ("RSI",        f"{a['rsi']:.0f}",             TXT),
        ("20d",        f"{a['ret_20']:+.1f}%",         POS if a["ret_20"] >= 0 else NEG),
        ("5d",         f"{a['ret_5']:+.1f}%",          POS if a["ret_5"] >= 0 else NEG),
        ("Mot topp",   f"{a['pct_from_high']:+.1f}%",  MUTED),
        ("52v-range",  f"{a['rng_pos']:.0f}%",         TXT),
        ("Rel.vol",    f"{a['rel_vol']:.2f}x",         TXT),
        ("Volatilitet",f"{a['atr_pct']:.1f}%",         TXT),
        ("EMA50",  "över"  if a["last"] > a["ema50"]  else "under",
                   POS     if a["last"] > a["ema50"]  else NEG),
        ("EMA200", "över"  if a["last"] > a["ema200"] else "under",
                   POS     if a["last"] > a["ema200"] else NEG),
    ]
    cells = "".join(f"<div class='mcard'><div class='ml'>{l}</div>"
                    f"<div class='mv' style='color:{c}'>{v}</div></div>" for l, v, c in items)
    st.markdown(f"<div class='mgrid'>{cells}</div>", unsafe_allow_html=True)
    st.progress(min(a["total"] / 100, 1.0),
                text=f"Score {a['total']:.0f}/100 · Styrka {a['strength']:.0f}/40 · "
                     f"Momentum {a['momentum']:.0f}/35 · Setup {a['setup']:.0f}/25")

    # ---- NIVÅER & STRUKTUR (RayAlgo-stil) ----
    if a.get("levels_note"):
        bos = a.get("bos", "INGEN")
        bos_col = POS if bos == "BULLISH" else NEG if bos == "BEARISH" else MUTED
        grade = a.get("setup_grade", "C")
        grade_col = {"A": POS, "B": ACCENT, "C": "#f5d142", "D": ROCK_C}.get(grade, MUTED)
        lvitems = [
            ("BOS", bos, bos_col),
            ("Struktur", a.get("structure", "—"), TXT),
            ("Setup", grade, grade_col),
            ("Stöd OB", f"{a['ob_support']:.2f}" if a.get("ob_support") is not None else "—", POS),
            ("Motstånd OB", f"{a['ob_resist']:.2f}" if a.get("ob_resist") is not None else "—", NEG),
            ("Mål +1 ATR", f"{a['atr_up_1']:.2f}" if a.get("atr_up_1") is not None else "—", POS),
            ("Risk −1 ATR", f"{a['atr_dn_1']:.2f}" if a.get("atr_dn_1") is not None else "—", NEG),
        ]
        lvcells = "".join(f"<div class='mcard'><div class='ml'>{l}</div>"
                          f"<div class='mv' style='color:{c}'>{v}</div></div>" for l, v, c in lvitems)
        st.markdown("#### Nivåer & struktur", unsafe_allow_html=True)
        st.markdown(f"<div class='mgrid'>{lvcells}</div>", unsafe_allow_html=True)
        st.caption(a["levels_note"])

    # ---- BREAKOUT ENGINE ----
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

    # ---- AI SCORE + TRADE MOTOR ----
    render_ai_score_panel(a)

    tradingview_chart(guess_tv_symbol(ticker), height=420)

    # ---- GRABIT INLINE (skill-kort-stil) ----
    st.divider()
    try:
        from ai_module import render_grabit_inline
        render_grabit_inline(ticker)
    except Exception:
        st.caption(f"Öppna **Ask Grabit**-fliken för att fråga AI:n om {ticker}.")


# =====================================================================
#  MARKNADSLÄGE
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
    last  = float(c.iloc[-1])
    ma200 = float(c.rolling(200).mean().iloc[-1])
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
                "MARKNAD", "JÄMFÖR", "SÖK", "ASK GRABIT", "MACRO", "RÅVAROR"])


def collect(tickers, keep_labels=None, warn=False):
    out  = []
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
    if render_featured_picks is not None:
        try:
            render_featured_picks(scan, selected, market_movers, company_info,
                                  logo_url, THEME_COLOR, TICKER_THEME)
        except Exception as _e:
            st.caption(f"Veckans urval kunde inte laddas: {_e}")
        st.divider()
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
        warn_data = []
        for a in r:
            short = get_short_name(a["ticker"])
            display = f"{a['ticker']}  {short}" if short else a["ticker"]
            warn_data.append({
                "Logo":   logo_url(a["ticker"]),
                "Bolag":  display,
                "Flagga": warnflag(a),
                "RSI":    int(round(a["rsi"])),
                "20d":    float(a["ret_20"]),
                "Pris":   float(a["last"]),
                "_ticker": a["ticker"],
            })
        df_w = pd.DataFrame(warn_data)
        cfg_w = {
            "Logo":   st.column_config.ImageColumn("", width="small"),
            "Bolag":  st.column_config.TextColumn("Bolag"),
            "Flagga": st.column_config.TextColumn("Flagga"),
            "RSI":    st.column_config.NumberColumn("RSI", format="%d"),
            "20d":    st.column_config.NumberColumn("20d", format="%.1f%%"),
            "Pris":   st.column_config.NumberColumn("Pris", format="%.2f"),
        }
        ev_w = st.dataframe(df_w.drop(columns=["_ticker"]), column_config=cfg_w,
                            hide_index=True, use_container_width=True,
                            height=min(len(df_w) * 36 + 40, 560),
                            on_select="rerun", selection_mode="single-row", key="warn")
        sel_w = ev_w.selection.rows if (ev_w and ev_w.selection) else []
        if sel_w:
            st.session_state["detail_req"] = warn_data[sel_w[0]]["_ticker"]

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
    if render_compare_tab is not None:
        render_compare_tab(scan, company_info, logo_url)
    else:
        st.info("Jämför-modulen kunde inte laddas.")

with tabs[6]:
    render_sok_tab()

with tabs[7]:
    render_ai_tab()

with tabs[8]:
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

with tabs[9]:
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
#  DETALJVY-TRIGGER — hämtar company_info UTANFÖR dialog, skickar in
# =====================================================================
_req = st.session_state.get("detail_req")
if _req and _req != st.session_state.get("detail_shown"):
    st.session_state["detail_shown"] = _req
    _info = company_info(_req)          # cachat anrop utanför @st.dialog
    show_detail(_req, _info)
