# ============================================================
#  MARKNADSPULS  —  drop-in module for GRABIT
#  Startsidans topp, helt på gratis-stacken (yfinance + Anthropic):
#    1) render_live_ticker()   — rullande prisband: BTC/guld/silver/koppar/olja/Nasdaq
#    2) render_sentiment_gauge()— marknadsmätare med glow (VIX + Nasdaq + BTC)
#    3) render_market_brief()  — Grabit skriver dagens marknadsläge (cachas/dag)
#
#  render_market_pulse() kör alla tre i rätt ordning.
#
#  ANVÄNDNING i app.py — direkt efter render_news_ticker():
#       from marknadspuls import render_market_pulse
#       render_market_pulse()
#
#  Eller styckvis om du vill placera dem olika:
#       from marknadspuls import (render_live_ticker,
#                                 render_sentiment_gauge, render_market_brief)
#
#  Inga nya beroenden. yfinance + anthropic + streamlit räcker.
#  AI-briefen återanvänder _client() från ai_module (samma API-nyckel).
# ============================================================

import datetime as _dt
import streamlit as st

try:
    import yfinance as yf
except Exception:
    yf = None

# Palett (matchar app.py)
BG, PANEL = "#0b0e16", "#161a23"
ACCENT, POS, NEG, MUTED, TXT = "#1199fa", "#16c784", "#f6465d", "#848e9c", "#eaecef"
GOLD = "#f0a020"
SUPER = "#2bff88"   # glödhet super-bullish


# ----------------------------------------------------------
#  DATAHÄMTNING  —  ett anrop, cachat 5 min
# ----------------------------------------------------------
# (namn, yfinance-symbol, antal decimaler, prefix, suffix)
_TICKER_DEFS = [
    ("Nasdaq",  "^IXIC",   0, "",  ""),
    ("BTC",     "BTC-USD", 0, "$", ""),
    ("Guld",    "GC=F",    0, "$", ""),
    ("Silver",  "SI=F",    2, "$", ""),
    ("Koppar",  "HG=F",    2, "$", ""),
    ("Brent",   "BZ=F",    2, "$", ""),
]


@st.cache_data(ttl=300, show_spinner=False)
def _quote(symbol: str):
    """Senaste pris + dagsförändring i %. None om det inte går att hämta."""
    if yf is None:
        return None
    try:
        df = yf.Ticker(symbol).history(period="5d", interval="1d")
        c = df["Close"].dropna()
        if len(c) < 2:
            return None
        last = float(c.iloc[-1])
        prev = float(c.iloc[-2])
        chg = (last / prev - 1) * 100 if prev else 0.0
        return {"last": last, "chg": chg}
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _vix():
    q = _quote("^VIX")
    return q["last"] if q else None


# ----------------------------------------------------------
#  1) LIVE-TICKER  —  rullande prisband
# ----------------------------------------------------------
def render_live_ticker():
    items = []
    for name, sym, dec, pre, suf in _TICKER_DEFS:
        q = _quote(sym)
        if not q:
            continue
        col = POS if q["chg"] >= 0 else NEG
        arrow = "\u25b2" if q["chg"] >= 0 else "\u25bc"
        price = f"{pre}{q['last']:,.{dec}f}{suf}"
        items.append(
            f"<span style='margin-right:34px;white-space:nowrap'>"
            f"<span style='color:{MUTED};font-weight:700;letter-spacing:.4px;font-size:.74rem;"
            f"text-transform:uppercase'>{name}</span> "
            f"<span style='color:{TXT};font-weight:700'>{price}</span> "
            f"<span style='color:{col};font-weight:700'>{arrow} {q['chg']:+.2f}%</span></span>")

    if not items:
        return  # tyst om datan inte är nåbar

    row = "".join(items)
    # dubblera raden så loopen blir sömlös
    st.markdown(
        "<style>@keyframes mg_scroll{from{transform:translateX(0)}"
        "to{transform:translateX(-50%)}}</style>"
        f"<div style='overflow:hidden;background:{PANEL};border:1px solid rgba(255,255,255,.06);"
        f"border-radius:16px;padding:10px 0;margin-bottom:12px'>"
        f"<div style='display:inline-block;white-space:nowrap;animation:mg_scroll 38s linear infinite;"
        f"font-size:.82rem'>{row}{row}</div></div>",
        unsafe_allow_html=True)


# ----------------------------------------------------------
#  2) SENTIMENT-GAUGE  —  0..100 mätare med glow
# ----------------------------------------------------------
def _sentiment_score():
    """Väger ihop Nasdaq-momentum, VIX och BTC till 0..100.
    Returnerar (score, label, color, drivkrafter:list)."""
    drivers = []
    score = 50.0

    nq = _quote("^IXIC")
    if nq:
        # dagsrörelse på Nasdaq: ±2% -> ±20p
        score += max(-20, min(20, nq["chg"] * 10))
        drivers.append(("Nasdaq", nq["chg"]))

    vix = _vix()
    if vix is not None:
        # VIX 12 (lugnt) -> +18, VIX 30 (oro) -> -18, linjärt
        score += max(-18, min(18, (20 - vix) * 2))
        drivers.append(("VIX", vix))

    bq = _quote("BTC-USD")
    if bq:
        # BTC som risk-aptit-proxy: ±5% -> ±12p
        score += max(-12, min(12, bq["chg"] * 2.4))
        drivers.append(("BTC", bq["chg"]))

    score = max(0, min(100, score))

    if score >= 82:
        label, color = "SUPER BULLISH", SUPER
    elif score >= 60:
        label, color = "BULLISH", POS
    elif score >= 40:
        label, color = "NEUTRAL", GOLD
    else:
        label, color = "BEARISH", NEG
    return score, label, color, drivers


def render_sentiment_gauge():
    score, label, color, drivers = _sentiment_score()

    # halvcirkel-gauge i SVG. Vinkel 180° (vänster=0, höger=100).
    import math
    ang = math.pi * (1 - score / 100)            # pi (vänster) -> 0 (höger)
    cx, cy, r = 130, 120, 100
    nx = cx + r * math.cos(ang)
    ny = cy - r * math.sin(ang)

    # bakgrundsbåge (grå) + färgad båge upp till score
    def _arc(frac):
        a = math.pi * (1 - frac)
        x = cx + r * math.cos(a)
        y = cy - r * math.sin(a)
        large = 0
        return x, y, large
    fx, fy, _ = _arc(score / 100)
    bx, by, _ = _arc(1.0)

    svg = (
        f"<svg viewBox='0 0 260 150' width='100%' style='max-width:300px'>"
        f"<defs><filter id='glow' x='-50%' y='-50%' width='200%' height='200%'>"
        f"<feGaussianBlur stdDeviation='4' result='b'/>"
        f"<feMerge><feMergeNode in='b'/><feMergeNode in='SourceGraphic'/></feMerge></filter></defs>"
        # grå bakgrundsbåge
        f"<path d='M {cx-r} {cy} A {r} {r} 0 0 1 {bx} {by}' fill='none' "
        f"stroke='rgba(255,255,255,.08)' stroke-width='14' stroke-linecap='round'/>"
        # färgad värdebåge
        f"<path d='M {cx-r} {cy} A {r} {r} 0 0 1 {fx} {fy}' fill='none' "
        f"stroke='{color}' stroke-width='14' stroke-linecap='round' filter='url(#glow)'/>"
        # nål
        f"<line x1='{cx}' y1='{cy}' x2='{nx:.1f}' y2='{ny:.1f}' stroke='{TXT}' stroke-width='3'/>"
        f"<circle cx='{cx}' cy='{cy}' r='6' fill='{TXT}'/>"
        # värde
        f"<text x='{cx}' y='{cy-26}' text-anchor='middle' fill='{color}' "
        f"font-size='30' font-weight='800' font-family='Space Grotesk,sans-serif'>{int(score)}</text>"
        f"<text x='{cx}' y='{cy-8}' text-anchor='middle' fill='{MUTED}' font-size='10'>/ 100</text>"
        f"</svg>")

    drv = ""
    for name, val in drivers:
        if name == "VIX":
            drv += f"<span style='color:{MUTED};margin-right:14px'>VIX <b style='color:{TXT}'>{val:.1f}</b></span>"
        else:
            c = POS if val >= 0 else NEG
            drv += f"<span style='color:{MUTED};margin-right:14px'>{name} <b style='color:{c}'>{val:+.2f}%</b></span>"

    st.markdown(
        f"<div style='background:linear-gradient(160deg,{color}10 0%,{PANEL} 60%);"
        f"border:1px solid {color}40;border-radius:20px;padding:16px 20px;margin-bottom:12px'>"
        f"<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>"
        f"<div style='flex:none'>{svg}</div>"
        f"<div style='flex:1;min-width:160px'>"
        f"<div style='font-size:.64rem;letter-spacing:1.5px;color:{MUTED};font-weight:700;"
        f"text-transform:uppercase'>Marknadsmätare</div>"
        f"<div style='font-size:1.7rem;font-weight:800;color:{color};font-family:Space Grotesk,sans-serif;"
        f"line-height:1.1;margin:2px 0 8px'>{label}</div>"
        f"<div style='font-size:.8rem'>{drv}</div></div></div></div>",
        unsafe_allow_html=True)
    return score, label


# ----------------------------------------------------------
#  3) AI-MARKNADSBRIEF  —  Grabit skriver dagens läge
# ----------------------------------------------------------
_BRIEF_SYSTEM = """Du är Grabit, marknadsanalytikern i appen GRABIT. Skriv en kort, kärnfull \
marknadssammanfattning på svenska — 3 till 5 meningar, inga punktlistor. Fokus: Nasdaq/teknik/AI, \
räntor, råvaror (guld/silver/olja), krypto och makro. Ton: rak, kunnig trader-kompis, ingen hype. \
Du ger ALDRIG köp-/säljråd och nämner inga enskilda kursmål. Du beskriver läget och vad det brukar \
betyda. Hitta inte på exakta siffror utöver de som ges i kontexten."""


@st.cache_data(ttl=3600, show_spinner=False)
def _brief_text(context: str, daykey: str):
    """Cachas per timme + dag så vi inte bränner API-anrop vid varje rerun."""
    try:
        from ai_module import _client
        client = _client()
    except Exception:
        return None
    if client is None:
        return None
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=_BRIEF_SYSTEM,
            messages=[{"role": "user",
                       "content": f"Sammanfatta marknadsläget just nu.\n\n[DATA]\n{context}"}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception:
        return None


def render_market_brief(score=None, label=None):
    # bygg kontext från det vi redan hämtat
    parts = []
    if score is not None:
        parts.append(f"Sentiment-gauge: {int(score)}/100 ({label}).")
    for name, sym, *_ in _TICKER_DEFS:
        q = _quote(sym)
        if q:
            parts.append(f"{name}: {q['last']:,.2f} ({q['chg']:+.2f}% idag).")
    vix = _vix()
    if vix is not None:
        parts.append(f"VIX: {vix:.1f}.")
    context = " ".join(parts) if parts else "Begränsad data tillgänglig just nu."

    daykey = _dt.date.today().isoformat()
    text = _brief_text(context, daykey)

    st.markdown(
        f"<div style='background:{PANEL};border:1px solid rgba(255,255,255,.06);"
        f"border-radius:20px;padding:16px 20px;margin-bottom:12px'>"
        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:8px'>"
        f"<span style='width:7px;height:7px;border-radius:50%;background:{ACCENT};"
        f"box-shadow:0 0 8px {ACCENT}'></span>"
        f"<span style='font-size:.64rem;letter-spacing:1.5px;color:{MUTED};font-weight:700;"
        f"text-transform:uppercase'>Dagens marknadsläge · Grabit</span></div>",
        unsafe_allow_html=True)

    if text:
        st.markdown(
            f"<div style='font-size:.92rem;line-height:1.6;color:#d7e0ea'>{text}</div></div>",
            unsafe_allow_html=True)
        st.caption("AI-sammanfattning, inte finansiell rådgivning.")
    else:
        st.markdown(
            f"<div style='font-size:.86rem;color:{MUTED}'>"
            f"Lägg <code>ANTHROPIC_API_KEY</code> i appens Secrets så skriver Grabit dagens läge här. "
            f"Sentiment och prisband ovan funkar ändå.</div></div>",
            unsafe_allow_html=True)


# ----------------------------------------------------------
#  ALLT-I-ETT
# ----------------------------------------------------------
def render_market_pulse(show_brief: bool = True):
    render_live_ticker()
    score, label = render_sentiment_gauge()
    if show_brief:
        render_market_brief(score, label)
