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

try:
    import yfinance as yf
except Exception:
    yf = None

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

# tema-färg per ticker (för logo-ikonerna) -> ger variation som riktiga loggor
THEME_COLOR = {
    "AI-infra":"2b7fff","Halvledare":"4f8cff","Photonics":"00c2c2","Quantum":"b06bff",
    "Rare earth":"f5a623","Defense/Drone":"ff5468","Lidar/Phys.AI":"21c45d",
    "Nuclear/Energi":"ffd23f","Space":"ff7ab8","Mjukvara":"7c5cff","Fintech/Krypto":"f7931a",
    "Bio":"2ecc71","Mega":"9aa7b5","Koppar":"d2691e","Silver/Guld":"c0c0c0",
    "Sverige":"006aa7","Bevakning":"5a6678",
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

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] {{ font-family:'Inter',-apple-system,sans-serif; }}
h1,h2,h3,h4 {{ color:#fff; font-weight:800; letter-spacing:-.4px; }}
.stApp h2, .stApp h3 {{ font-size:1.35rem; }}
.stTabs [data-baseweb="tab-list"] {{ gap:2px; border-bottom:1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ background:transparent; color:{MUTED}; border-radius:0;
    padding:9px 15px; font-weight:600; font-size:.85rem; border-bottom:2px solid transparent; }}
.stTabs [aria-selected="true"] {{ color:#fff; border-bottom:2px solid {ACCENT}; }}
.pill {{ display:inline-block; padding:4px 12px; border-radius:6px;
        font-size:.75rem; font-weight:700; letter-spacing:.5px; }}
/* nyckeltalsstrip */
.sstrip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
          gap:10px; margin:6px 0 14px; }}
.scard {{ background:{PANEL}; border:1px solid {LINE}; border-radius:14px; padding:12px 16px; }}
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


@st.cache_data(ttl=900, show_spinner=False)
def market_movers(source_key):
    """Hämtar dagens rörare från Yahoos screener. Returnerar ticker-lista."""
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
        "Poäng": int(a["score10"]), "Pris": float(a["last"]),
        "5d": float(a.get("ret_5", 0)), "20d": float(a["ret_20"]),
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
            "20d": st.column_config.NumberColumn("20d", format="%+.1f%%"),
            "Vol": st.column_config.NumberColumn("Vol", format="%.1fx"),
        })
    sel = ev.selection.rows if (ev and ev.selection) else []
    if sel:
        show_detail(df.iloc[sel[0]]["Ticker"])

def _escore_col(v, good_high=True):
    if good_high:
        return POS if v >= 70 else (ACCENT if v >= 50 else (MUTED if v >= 30 else NEG))
    return NEG if v >= 65 else (ROCK_C if v >= 40 else POS)


def render_engine(e):
    st.markdown("#### Trade-motor")
    ent, ex = e["entry"], e["exit"]
    cards = [("Breakout", f"{e['breakout_score']}", _escore_col(e['breakout_score'])),
             ("Exit-risk", f"{ex['risk']}", _escore_col(ex['risk'], False)),
             ("Swing", f"{e['swing_score']}", _escore_col(e['swing_score'])),
             ("Confidence", f"{e['confidence']}%", _escore_col(e['confidence']))]
    cells = "".join(f"<div class='mcard'><div class='ml'>{l}</div>"
                    f"<div class='mv' style='color:{c}'>{v}</div></div>" for l, v, c in cards)
    st.markdown(f"<div class='mgrid'>{cells}</div>", unsafe_allow_html=True)

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


# ---------------------------------------------------------------------
#  DETALJVY  (modal)
# ---------------------------------------------------------------------
@st.dialog(" ", width="large")
@st.cache_data(ttl=3600, show_spinner=False)
def company_info(ticker):
    """Hämtar bolagsnamn, sektor, börsvärde m.m. från Yahoo. Cachas 1h.
    Returnerar ALLTID en dict — kraschar aldrig."""
    if yf is None:
        return {}
    try:
        info = yf.Ticker(ticker).info or {}
        if not isinstance(info, dict):
            return {}
        return {
            "name": info.get("longName") or info.get("shortName") or "",
            "sector": info.get("sector") or info.get("industry") or "",
            "mcap": info.get("marketCap"),
            "currency": info.get("currency") or "",
            "summary": info.get("longBusinessSummary") or "",
            "country": info.get("country") or "",
        }
    except Exception:
        return {}


def _fmt_mcap(v):
    if not v:
        return "—"
    if v >= 1e12: return f"{v/1e12:.1f}T"
    if v >= 1e9:  return f"{v/1e9:.1f}B"
    if v >= 1e6:  return f"{v/1e6:.0f}M"
    return f"{v:,.0f}"


def price_chart(df, color):
    """Ritar en snabb kursgraf (90 dagar) från Yahoo-datan med Streamlits area_chart."""
    try:
        closes = df["Close"].tail(90)
        chart_df = pd.DataFrame({"Pris": closes.values}, index=closes.index)
        st.area_chart(chart_df, color=color, height=220)
    except Exception:
        pass


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
    top = st.columns([2, 1, 1])
    top[0].markdown(f"## {ticker}")
    if info.get("name") and info["name"] != ticker:
        top[0].caption(f"{info['name']} · {info.get('sector','—')}")
    top[0].markdown(
        f"<span class='pill' style='background:{a['color']}22;color:{a['color']};"
        f"border:1px solid {a['color']}55'>{a['label']}</span>", unsafe_allow_html=True)
    top[1].metric("Pris", f"{a['last']:.2f}")
    top[2].metric("Lyftchans", f"{a['score10']}/10")

    # ---- STOR, TYDLIG 5-DAGARS-RÖRELSE ----
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
        f"</div>",
        unsafe_allow_html=True)

    # ---- KURSGRAF (90 dagar, snabb) ----
    if df_full is not None:
        price_chart(df_full, a["color"])

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
    # ---- TRADE-MOTOR (breakout / entry / exit / swing) ----
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

    # ---- OM BOLAGET (hopfällbart) ----
    if info.get("summary"):
        with st.expander(f"Om {info.get('name', ticker)}"):
            land = f" · {info['country']}" if info.get("country") else ""
            st.caption(f"{info.get('sector','—')}{land}")
            st.write(info["summary"])

    tradingview_chart(guess_tv_symbol(ticker), height=360)
    st.caption(f"Öppna **Ask Grabit**-fliken för att fråga AI:n om {ticker}.")

# ---------------------------------------------------------------------
#  MARKNADSLÄGE
# ---------------------------------------------------------------------
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
    ma50 = float(c.rolling(50).mean().iloc[-1])
    pct = (last / ma200 - 1) * 100
    if last > ma200 and ma50 > ma200:  return "BULL", pct
    if last < ma200:                   return "BEAR", pct
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

# ---------------------------------------------------------------------
#  FLIKAR
# ---------------------------------------------------------------------
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
    st.subheader("Marknad — det som rör sig idag")
    SOURCES = {
        "Dagens vinnare": "day_gainers",
        "Mest omsatta": "most_actives",
        "Small-cap raketer": "small_cap_gainers",
        "Tillväxt-tech": "growth_technology_stocks",
        "Dagens förlorare": "day_losers",
    }
    csrc, cflt = st.columns([3, 2])
    src_name = csrc.radio("Källa", list(SOURCES.keys()), horizontal=True, key="mkt_src")
    only_entry = cflt.checkbox("Visa bara entry-lägen", value=True, key="mkt_entry")
    if yf is None:
        st.error("yfinance saknas — kan inte scanna marknaden.")
    else:
        syms = market_movers(SOURCES[src_name])
        if not syms:
            st.warning("Kunde inte hämta marknadsdata just nu (Yahoo-screenern svarade inte). "
                       "Prova en annan källa eller tryck refresh.")
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
            st.caption(f"{src_name} · {len(r)} träffar efter din motor. "
                       f"Tryck på en ticker för detaljer.")
            render_stats(r)
            render_grid(r, "market")

with tabs[5]:
    render_sok_tab()

with tabs[6]:
    render_ai_tab()

with tabs[7]:
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

with tabs[8]:
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
