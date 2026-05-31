# ============================================================
#  SÖK  —  drop-in module for MoneyGrab
#  Sök valfri aktie → TradingView-graf → BEAR/BULL/Rocketcase
#  + 1–10 ranking (styrka + momentum + setup)
# ============================================================
#
#  SÅ HÄR KOPPLAR DU IN DEN I app.py:
#
#  1) Lägg "SÖK" i din tabs-rad, t.ex:
#       tabs = st.tabs(["HETA NU", "BYGGER UPP", "VARNINGAR",
#                       "Watchlist", "SÖK", "MACRO", "RÅVAROR"])
#
#  2) Importera överst i app.py:
#       from sok_module import render_sok_tab
#
#  3) Rendera fliken (matcha index till var du la "SÖK"):
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

    # ---------- ENTRY-SIGNALER (snabba entrys, vändningar, momentum) ----------
    ema50_ser = _ema(close, 50)
    rsi_ser = _rsi(close)
    reclaimed50 = bool(len(close) > 6 and close.iloc[-6] < ema50_ser.iloc[-6] and last > ema50)
    rsi_rising = bool(len(close) > 6 and rsi > rsi_ser.iloc[-6])
    thrust = bool(ret_5 >= 4 and ret_20 >= 8 and rel_vol >= 1.2 and last > ema20 and rsi < 75)
    turnaround = bool(last < ema200 and last > ema50 and ret_20 > 0 and rsi_rising and rsi >= 45)

    # rullar över: rasat hårt senaste dagarna trots att längre trend ser fin ut
    ret_2 = (last / close.iloc[-3] - 1) * 100 if len(close) > 3 else 0
    hi5 = close.tail(5).max()
    pull5 = (last / hi5 - 1) * 100 if hi5 else 0
    rolling_over = bool(ret_2 <= -8 or (pull5 <= -10 and ret_2 < 0))

    # ---------- MACD-VÄNDNING + RSI NER (dagsgraf) ----------
    # Regel: när MACD vänder mot/under signallinjen OCH RSI börjar dyka,
    # är läget inte längre bullish — det svalnar.
    ema12_ser = _ema(close, 12)
    ema26_ser = _ema(close, 26)
    macd_line = ema12_ser - ema26_ser
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histo = macd_line - signal_line
    macd_below = bool(macd_line.iloc[-1] < signal_line.iloc[-1])      # MACD under signal
    macd_falling = bool(len(histo) > 2 and histo.iloc[-1] < histo.iloc[-2])  # histogram krymper/vänder
    rsi_falling = bool(len(rsi_ser) > 2 and rsi_ser.iloc[-1] < rsi_ser.iloc[-2])  # RSI vänder ner
    # "cooling" = din regel: MACD vänder ner mot signal + RSI dyker
    cooling = bool((macd_falling or macd_below) and rsi_falling)

    # entry-bonus: lyfter färska lägen som annars straffas för att de är under EMA200
    bonus = 0
    if reclaimed50: bonus += 4
    if rsi_rising and 45 <= rsi <= 65: bonus += 3
    if rel_vol >= 1.5: bonus += 3
    if thrust: bonus += 3
    if rolling_over: bonus -= 6
    if cooling: bonus -= 7          # MACD vänder + RSI ner = dra ner poängen

    total = min(strength + momentum + setup + bonus, 100)   # 0–100
    score10 = max(1, min(10, round(total / 10)))

    # ---------- ETIKETT  (prioriterar entry-lägen) ----------
    if (rolling_over or cooling) and (last > ema50 or ret_20 > 0):
        label, emoji, color = "AVSVALNING", "", "#ff6b3d"
    elif (-8 <= pct_from_high <= 1) and tight < 5 and rsi < 72 and last > ema50 and not cooling:
        label, emoji, color = "Rocketcase", "", "#f5a623"
    elif turnaround and not cooling:
        label, emoji, color = "VÄNDNING", "", "#00c2c2"
    elif last > ema50 and last > ema200 and momentum > 18 and not cooling:
        label, emoji, color = "BULL", "", "#21c45d"
    elif thrust and not cooling:
        label, emoji, color = "MOMENTUM", "", "#b06bff"
    elif last < ema50 and last < ema200 and ret_20 < -5 and ret_5 <= 0:
        label, emoji, color = "BEAR", "", "#ff4b4b"
    elif last > ema50:
        label, emoji, color = "NEUTRAL/BYGGER", "", "#f5d142"
    else:
        label, emoji, color = "SVAG", "", "#ff9f43"

    return {
        "last": last, "rsi": rsi, "atr_pct": atr_pct,
        "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "pct_from_high": pct_from_high, "rng_pos": rng_pos,
        "ret_5": ret_5, "ret_20": ret_20, "rel_vol": rel_vol, "tight": tight,
        "strength": strength, "momentum": momentum, "setup": setup,
        "total": total, "score10": score10,
        "macd_below": macd_below, "macd_falling": macd_falling,
        "rsi_falling": rsi_falling, "cooling": cooling,
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
    st.subheader("SÖK — analysera valfri aktie")
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

    # spara kontext så ANALYS-fliken vet vad användaren tittar på
    st.session_state["sok_context"] = {
        "ticker": ticker, "label": a["label"], "score10": a["score10"],
        "last": a["last"], "rsi": a["rsi"], "pct_from_high": a["pct_from_high"],
        "ret_20": a["ret_20"], "rel_vol": a["rel_vol"],
        "strength": a["strength"], "momentum": a["momentum"], "setup": a["setup"],
    }

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
            f"{a['label']}</span></div>",
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
    with st.expander("Detaljerade nyckeltal"):
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
                "över " if a['last'] > a['ema50'] else "under ",
                "över " if a['last'] > a['ema200'] else "under ",
            ],
        })
        st.dataframe(m, hide_index=True, use_container_width=True)

    # ---- tolkning i klartext ----
    st.markdown("##### Tolkning")
    notes = []
    if a["label"] == "AVSVALNING":
        notes.append("**Rullar över** — har fallit hårt de senaste dagarna trots att den längre trenden "
                     "ser stark ut. Ofta toppbildning eller distribution. Undvik nya köp tills det stabiliseras.")
    elif a["label"] == "Rocketcase":
        notes.append("**Laddad setup** — nära 52v-topp, tight konsolidering och inte överköpt. "
                     "Den typ av läge som föregår breakouts. Bekräftelse: utbrott på hög volym.")
    elif a["label"] == "VÄNDNING":
        notes.append("**Turnaround** — har återtagit EMA50 underifrån med stigande RSI och positiv 20d, "
                     "men ligger ännu under EMA200. Tidig vändning: hög potential, men obekräftad — "
                     "risk för att det är en studs. Vill se att EMA200 återtas för bekräftelse.")
    elif a["label"] == "MOMENTUM":
        notes.append("**Snabb fart** — stark 5d/20d-rörelse på stigande volym över EMA20. "
                     "Klassiskt momentum-entry. Akta överköpt och sätt stop tight — fart kan vända snabbt.")
    elif a["label"] == "BULL":
        notes.append("**Stark trend** — över EMA50 & EMA200 med levande momentum. Trend-följa, inte jaga toppar.")
    elif a["label"] == "BEAR":
        notes.append("**Nedtrend** — under nyckelmedelvärden och fortfarande fallande. Vänta på vändning, inte 'köp dippen' än.")
    elif a["label"] == "NEUTRAL/BYGGER":
        notes.append("**Bygger** — över EMA50 men momentum saknas. Kan bli setup om volym kommer in.")
    else:
        notes.append("**Svag** — under nyckelnivåer. Låg prioritet.")

    if a["rsi"] > 78:
        notes.append(f"RSI {a['rsi']:.0f} — överköpt, risk för rekyl på kort sikt.")
    if a["rel_vol"] >= 1.5:
        notes.append(f"Volym {a['rel_vol']:.1f}x snittet — något händer just nu.")
    if a["pct_from_high"] > -3 and a["label"] != "BEAR":
        notes.append("Mindre än 3% från 52v-topp — breakout-zon.")
    for n in notes:
        st.markdown(f"- {n}")

    st.caption("Detta är teknisk signalering, inte finansiell rådgivning. Rankingen väger styrka, "
               "momentum och setup — den säger inget om bolagets fundamenta eller nyhetsrisk.")

    # ---- TRADINGVIEW-GRAF ----
    st.markdown("##### TradingView")
    st.caption("Du kan byta symbol direkt i grafen (klicka på tickern uppe till vänster).")
    tradingview_chart(guess_tv_symbol(ticker))
