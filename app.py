# ============================================================
#  🔍 SÖK  —  drop-in module for MoneyGrab
#  Sök valfri aktie → TradingView-graf → BEAR/BULL/SOON-TO-FLY
#  + 1–10 ranking (styrka + momentum + setup)
# ============================================================
#
#  SÅ HÄR KOPPLAR DU IN DEN I app.py:
#
#  1) Lägg "🔍 SÖK" i din tabs-rad, t.ex:
#       tabs = st.tabs(["🔥 HETA NU", "📈 BYGGER UPP", "⚠️ VARNINGAR",
#                       "⭐ Watchlist", "🔍 SÖK", "📊 MACRO", "🛢 RÅVAROR"])
#
#  2) Importera överst i app.py:
#       from sok_module import render_sok_tab
#
#  3) Rendera fliken (matcha index till var du la "🔍 SÖK"):
#       with tabs[4]:
#           render_sok_tab()
#
#  Kräver: yfinance, pandas, numpy  (du har dem redan)
# ============================================================

import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
import numpy as np


# ----------------------------------------------------------
#  INDIKATORER
# ----------------------------------------------------------
def _rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ----------------------------------------------------------
#  HÄMTA DATA  (cache 5 min så vi inte spammar Yahoo)
# ----------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch(ticker):
    t = yf.Ticker(ticker)
    df = t.history(period="1y", interval="1d", auto_adjust=False)
    if df is None or df.empty or len(df) < 60:
        return None, None
    # intraday för en färskare "senaste"-känsla
    try:
        intr = t.history(period="1d", interval="5m", auto_adjust=False)
    except Exception:
        intr = None
    return df, intr


# ----------------------------------------------------------
#  SCORING-MOTOR  →  0–100, sen 1–10 + etikett
#  Kombo: STYRKA (40) + MOMENTUM (35) + SETUP (25)
# ----------------------------------------------------------
def analyze(df):
    close = df["Close"]
    vol = df["Volume"]
    last = close.iloc[-1]

    ema20 = _ema(close, 20).iloc[-1]
    ema50 = _ema(close, 50).iloc[-1]
    ema200 = _ema(close, 200).iloc[-1] if len(close) >= 200 else _ema(close, len(close) - 1).iloc[-1]
    rsi = _rsi(close).iloc[-1]
    atr = _atr(df).iloc[-1]
    atr_pct = (atr / last) * 100 if last else 0

    hi52 = close.tail(252).max()
    lo52 = close.tail(252).min()
    pct_from_high = (last / hi52 - 1) * 100      # 0 = på topp, negativt = under
    rng_pos = (last - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 50

    ret_5 = (last / close.iloc[-6] - 1) * 100 if len(close) > 6 else 0
    ret_20 = (last / close.iloc[-21] - 1) * 100 if len(close) > 21 else 0

    avg_vol = vol.tail(20).mean()
    rel_vol = vol.iloc[-1] / avg_vol if avg_vol else 1

    # konsolidering: hur tight har 10 senaste dagarna varit
    tight = close.tail(10).std() / close.tail(10).mean() * 100 if len(close) >= 10 else 99

    # ---------- STYRKA (max 40) ----------
    s = 0
    if last > ema20: s += 10
    if last > ema50: s += 8
    if last > ema200: s += 12
    if ema20 > ema50: s += 6
    if ema50 > ema200: s += 4
    strength = min(s, 40)

    # ---------- MOMENTUM (max 35) ----------
    m = 0
    m += np.interp(ret_20, [-15, 0, 30], [0, 8, 18])
    m += np.interp(ret_5, [-10, 0, 15], [0, 4, 9])
    if 50 <= rsi <= 70: m += 8         # frisk styrka
    elif 40 <= rsi < 50: m += 4
    elif rsi > 78: m -= 4              # överköpt
    momentum = float(max(0, min(m, 35)))

    # ---------- SETUP (max 25) ----------
    su = 0
    # nära 52w-högsta = breakout-läge
    if -8 <= pct_from_high <= 0: su += 10
    elif -15 <= pct_from_high < -8: su += 5
    # tight konsolidering = laddad fjäder
    if tight < 3: su += 7
    elif tight < 5: su += 4
    # volym-bekräftelse
    if rel_vol >= 1.5: su += 5
    elif rel_vol >= 1.1: su += 3
    # i övre delen av 52w-range
    if rng_pos >= 70: su += 3
    setup = min(su, 25)

    total = strength + momentum + setup     # 0–100
    score10 = max(1, min(10, round(total / 10)))

    # ---------- ETIKETT ----------
    if last < ema50 and last < ema200 and ret_20 < -5:
        label, emoji, color = "BEAR", "🔻", "#ff4b4b"
    elif (-8 <= pct_from_high <= 1) and tight < 5 and rsi < 72 and last > ema50:
        label, emoji, color = "SOON TO FLY", "🚀", "#f5a623"
    elif last > ema50 and last > ema200 and momentum > 18:
        label, emoji, color = "BULL", "🟢", "#21c45d"
    elif last > ema50:
        label, emoji, color = "NEUTRAL/BYGGER", "🟡", "#f5d142"
    else:
        label, emoji, color = "SVAG", "🟠", "#ff9f43"

    return {
        "last": last, "rsi": rsi, "atr_pct": atr_pct,
        "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "pct_from_high": pct_from_high, "rng_pos": rng_pos,
        "ret_5": ret_5, "ret_20": ret_20, "rel_vol": rel_vol, "tight": tight,
        "strength": strength, "momentum": momentum, "setup": setup,
        "total": total, "score10": score10,
        "label": label, "emoji": emoji, "color": color,
    }


# ----------------------------------------------------------
#  TRADINGVIEW-WIDGET
# ----------------------------------------------------------
def tradingview_chart(tv_symbol, height=460):
    html = f"""
    <div class="tradingview-widget-container" style="height:{height}px;width:100%">
      <div id="tv_chart" style="height:{height}px;width:100%"></div>
      <script type="text/javascript"
        src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
        new TradingView.widget({{
          "autosize": true,
          "symbol": "{tv_symbol}",
          "interval": "D",
          "timezone": "Europe/Stockholm",
          "theme": "dark",
          "style": "1",
          "locale": "se",
          "toolbar_bg": "#0e1117",
          "enable_publishing": false,
          "hide_side_toolbar": false,
          "allow_symbol_change": true,
          "studies": ["STD;EMA","STD;RSI"],
          "container_id": "tv_chart"
        }});
      </script>
    </div>
    """
    components.html(html, height=height + 10)


def guess_tv_symbol(raw):
    """Gissa TradingView-symbol. Svenska .ST → OMXSTO:, annars rått."""
    t = raw.strip().upper()
    if t.endswith(".ST"):
        return "OMXSTO:" + t[:-3]
    return t


# ----------------------------------------------------------
#  HUVUDFLIK
# ----------------------------------------------------------
def render_sok_tab():
    st.subheader("🔍 SÖK — analysera valfri aktie")
    st.caption("Data ~15 min fördröjd (Yahoo Finance). Skriv ticker, t.ex. NVDA, OKLO, RKLB, eller svensk: SIVE.ST")

    col_in, col_btn = st.columns([4, 1])
    with col_in:
        ticker = st.text_input("Ticker", value="NVDA", label_visibility="collapsed").strip().upper()
    with col_btn:
        go = st.button("Analysera", use_container_width=True, type="primary")

    if not ticker:
        st.info("Skriv en ticker ovan och tryck Analysera.")
        return

    df, intr = fetch(ticker)
    if df is None:
        st.error(f"Hittade ingen data för **{ticker}**. Kolla stavning. "
                 f"Svenska aktier behöver `.ST` (ex SIVE.ST), tyska `.DE`, osv.")
        return

    a = analyze(df)

    # senaste pris från intraday om möjligt
    live = intr["Close"].iloc[-1] if intr is not None and not intr.empty else a["last"]

    # ---- HEADER: etikett + ranking ----
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        st.markdown(
            f"<div style='padding:14px;border-radius:10px;background:{a['color']}22;"
            f"border:1px solid {a['color']}'>"
            f"<span style='font-size:13px;opacity:.7'>BEDÖMNING</span><br>"
            f"<span style='font-size:30px;font-weight:800;color:{a['color']}'>"
            f"{a['emoji']} {a['label']}</span></div>",
            unsafe_allow_html=True)
    with c2:
        st.metric("Ranking", f"{a['score10']} / 10")
    with c3:
        st.metric("Senaste", f"{live:,.2f}", f"{a['ret_5']:+.1f}% (5d)")

    # ---- breakdown ----
    st.markdown("##### Så räknas rankingen")
    b1, b2, b3 = st.columns(3)
    b1.metric("Styrka", f"{a['strength']:.0f} / 40", help="Pris vs EMA20/50/200 + trendordning")
    b2.metric("Momentum", f"{a['momentum']:.0f} / 35", help="5d & 20d avkastning + RSI-läge")
    b3.metric("Setup", f"{a['setup']:.0f} / 25", help="Närhet 52v-topp, konsolidering, volym")
    st.progress(min(a["total"] / 100, 1.0), text=f"Totalscore: {a['total']:.0f} / 100")

    # ---- nyckeltal ----
    with st.expander("📊 Detaljerade nyckeltal"):
        m = pd.DataFrame({
            "Mått": ["RSI(14)", "Från 52v-topp", "52v-range pos", "Avkastn. 20d",
                     "Rel. volym", "Volatilitet (ATR%)", "Konsolidering (10d std%)",
                     "Pris vs EMA50", "Pris vs EMA200"],
            "Värde": [
                f"{a['rsi']:.0f}",
                f"{a['pct_from_high']:+.1f}%",
                f"{a['rng_pos']:.0f}%",
                f"{a['ret_20']:+.1f}%",
                f"{a['rel_vol']:.2f}x",
                f"{a['atr_pct']:.1f}%",
                f"{a['tight']:.1f}%",
                "över ✅" if a['last'] > a['ema50'] else "under ❌",
                "över ✅" if a['last'] > a['ema200'] else "under ❌",
            ],
        })
        st.dataframe(m, hide_index=True, use_container_width=True)

    # ---- tolkning i klartext ----
    st.markdown("##### Tolkning")
    notes = []
    if a["label"] == "SOON TO FLY":
        notes.append("🚀 **Laddad setup** — nära 52v-topp, tight konsolidering och inte överköpt. "
                     "Den typ av läge som föregår breakouts. Bekräftelse: utbrott på hög volym.")
    elif a["label"] == "BULL":
        notes.append("🟢 **Stark trend** — över EMA50 & EMA200 med levande momentum. Trend-följa, inte jaga toppar.")
    elif a["label"] == "BEAR":
        notes.append("🔻 **Nedtrend** — under nyckelmedelvärden och negativ 20d. Vänta på vändning, inte 'köp dippen' än.")
    elif a["label"] == "NEUTRAL/BYGGER":
        notes.append("🟡 **Bygger** — över EMA50 men momentum saknas. Kan bli setup om volym kommer in.")
    else:
        notes.append("🟠 **Svag** — under nyckelnivåer. Låg prioritet.")

    if a["rsi"] > 78:
        notes.append(f"⚠️ RSI {a['rsi']:.0f} — överköpt, risk för rekyl på kort sikt.")
    if a["rel_vol"] >= 1.5:
        notes.append(f"📈 Volym {a['rel_vol']:.1f}x snittet — något händer just nu.")
    if a["pct_from_high"] > -3 and a["label"] != "BEAR":
        notes.append("🎯 Mindre än 3% från 52v-topp — breakout-zon.")
    for n in notes:
        st.markdown(f"- {n}")

    st.caption("Detta är teknisk signalering, inte finansiell rådgivning. Rankingen väger styrka, "
               "momentum och setup — den säger inget om bolagets fundamenta eller nyhetsrisk.")

    # ---- TRADINGVIEW-GRAF ----
    st.markdown("##### 📈 TradingView")
    st.caption("Du kan byta symbol direkt i grafen (klicka på tickern uppe till vänster).")
    tradingview_chart(guess_tv_symbol(ticker))
