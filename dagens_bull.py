# =====================================================================
#  dagens_bull.py  —  Dagens Bull + Veckans Bull för MoneyGrab
#  Scannar UNIVERSE, väljer starkaste rörelsen per period.
#  Renderas i tab[0] (Heta nu) via render_dagens_bull().
#  Inget av detta är finansiell rådgivning.
# =====================================================================

import streamlit as st
import pandas as pd

from sok_module import fetch, analyze

# Importera UNIVERSE + färger från app-scope via session_state-trick
# (undviker cirkulär import — app.py sätter dessa vid start)
def _get_universe() -> dict:
    return st.session_state.get("_mg_universe", {})

def _get_ticker_theme() -> dict:
    return st.session_state.get("_mg_ticker_theme", {})

def _get_theme_color() -> dict:
    return st.session_state.get("_mg_theme_color", {})

# Fallback-färger om session_state inte är satt
ACCENT  = "#1199fa"
POS     = "#16c784"
NEG     = "#f6465d"
MUTED   = "#848e9c"
TXT     = "#eaecef"
ROCK_C  = "#f0a020"


# ---------------------------------------------------------------
#  CORE: hämta kandidat-data för en ticker
# ---------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _bull_data(ticker: str) -> dict | None:
    """
    Returnerar dict med dag- och vecko-rörelse + volym-ratio.
    Cachas 10 min.
    """
    try:
        df, _ = fetch(ticker)
    except Exception:
        return None
    if df is None or len(df) < 10:
        return None
    try:
        c   = df["Close"].dropna()
        v   = df["Volume"].dropna() if "Volume" in df.columns else None

        last      = float(c.iloc[-1])
        prev_day  = float(c.iloc[-2])
        prev_week = float(c.iloc[-6]) if len(c) >= 6 else float(c.iloc[0])

        day_ret  = (last / prev_day  - 1) * 100
        week_ret = (last / prev_week - 1) * 100

        # Relativ volym (dag vs 20-dagars snitt)
        rel_vol = 1.0
        if v is not None and len(v) >= 20:
            avg_vol = float(v.iloc[-21:-1].mean())
            if avg_vol > 0:
                rel_vol = float(v.iloc[-1]) / avg_vol

        # Hämta label + score från analyze
        a = analyze(df)
        label    = a.get("label", "—")
        score10  = a.get("score10", 0)
        rsi      = a.get("rsi", 0)

        return {
            "ticker":   ticker,
            "last":     last,
            "day_ret":  day_ret,
            "week_ret": week_ret,
            "rel_vol":  rel_vol,
            "label":    label,
            "score10":  score10,
            "rsi":      rsi,
        }
    except Exception:
        return None


# ---------------------------------------------------------------
#  VÄLJ VINNARE
# ---------------------------------------------------------------
def _pick_bull(tickers: list[str], period: str) -> dict | None:
    """
    period = 'day'  → sorterar på day_ret, kräver rel_vol > 1.1
    period = 'week' → sorterar på week_ret, kräver score10 >= 5
    Returnerar bästa kandidat eller None.
    """
    candidates = []
    for t in tickers:
        d = _bull_data(t)
        if not d:
            continue
        if period == "day":
            if d["rel_vol"] >= 1.1 and d["day_ret"] > 0:
                candidates.append(d)
        else:  # week
            if d["score10"] >= 5 and d["week_ret"] > 0:
                candidates.append(d)

    if not candidates:
        return None

    key = "day_ret" if period == "day" else "week_ret"
    return max(candidates, key=lambda x: x[key])


# ---------------------------------------------------------------
#  RENDER-HJÄLPARE
# ---------------------------------------------------------------
def _bull_card(winner: dict, period: str, theme_color: dict, ticker_theme: dict):
    """Renderar ett premium bull-kort."""
    label_map = {"day": "DAGENS BULL", "week": "VECKANS BULL"}
    period_label = label_map.get(period, "BULL")

    ret_val   = winner["day_ret"] if period == "day" else winner["week_ret"]
    ret_label = "idag" if period == "day" else "5 dagar"
    theme     = ticker_theme.get(winner["ticker"], "")
    color     = "#" + theme_color.get(theme, "1199fa")
    rsi       = winner["rsi"]
    rsi_col   = NEG if rsi > 75 else (ROCK_C if rsi > 65 else POS)
    vol_col   = POS if winner["rel_vol"] >= 1.5 else (ROCK_C if winner["rel_vol"] >= 1.1 else MUTED)

    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, {color}18, rgba(13,17,24,.92));
        border: 1px solid {color}55;
        border-radius: 24px;
        padding: 20px 24px;
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        gap: 24px;
        flex-wrap: wrap;
    ">
        <div style="flex:none">
            <div style="font-size:.68rem;color:{MUTED};text-transform:uppercase;
                        letter-spacing:.8px;font-weight:700">{period_label}</div>
            <div style="font-size:2rem;font-weight:800;color:{color};
                        font-family:'Space Grotesk',sans-serif;line-height:1.1;margin-top:2px">
                {winner['ticker']}
            </div>
            <div style="margin-top:6px">
                <span style="display:inline-block;padding:3px 10px;border-radius:999px;
                    font-size:.72rem;font-weight:700;background:{color}22;
                    color:{color};border:1px solid {color}44">
                    {winner['label']}
                </span>
            </div>
        </div>
        <div style="flex:1;min-width:160px;display:grid;
                    grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:12px">
            <div>
                <div style="font-size:.65rem;color:{MUTED};text-transform:uppercase;
                            letter-spacing:.6px">Rörelse {ret_label}</div>
                <div style="font-size:1.8rem;font-weight:800;color:{POS};
                            font-family:'Space Grotesk',sans-serif;line-height:1.1">
                    +{ret_val:.1f}%
                </div>
            </div>
            <div>
                <div style="font-size:.65rem;color:{MUTED};text-transform:uppercase;
                            letter-spacing:.6px">Pris</div>
                <div style="font-size:1.2rem;font-weight:700;color:{TXT}">
                    {winner['last']:.2f}
                </div>
            </div>
            <div>
                <div style="font-size:.65rem;color:{MUTED};text-transform:uppercase;
                            letter-spacing:.6px">RSI</div>
                <div style="font-size:1.2rem;font-weight:700;color:{rsi_col}">
                    {rsi:.0f}
                </div>
            </div>
            <div>
                <div style="font-size:.65rem;color:{MUTED};text-transform:uppercase;
                            letter-spacing:.6px">Rel.vol</div>
                <div style="font-size:1.2rem;font-weight:700;color:{vol_col}">
                    {winner['rel_vol']:.2f}x
                </div>
            </div>
            <div>
                <div style="font-size:.65rem;color:{MUTED};text-transform:uppercase;
                            letter-spacing:.6px">Poäng</div>
                <div style="font-size:1.2rem;font-weight:700;color:{ACCENT}">
                    {int(winner['score10'])}/10
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------
#  PUBLIK ENTRY-PUNKT — anropas från app.py
# ---------------------------------------------------------------
def render_dagens_bull():
    """
    Renderar Dagens Bull + Veckans Bull.
    app.py måste ha satt dessa session_state-nycklar innan anrop:
        st.session_state["_mg_universe"]      = UNIVERSE
        st.session_state["_mg_ticker_theme"]  = TICKER_THEME
        st.session_state["_mg_theme_color"]   = THEME_COLOR
    """
    universe    = _get_universe()
    theme_color = _get_theme_color()
    ticker_theme= _get_ticker_theme()

    if not universe:
        st.warning("UNIVERSE ej initierat — kontrollera app.py.")
        return

    all_tickers = [t for tickers in universe.values() for t in tickers]
    # Ta bort dubbletter men bevara ordning
    seen = set(); tickers = []
    for t in all_tickers:
        if t not in seen:
            seen.add(t); tickers.append(t)

    c1, c2 = st.columns(2)

    with c1:
        with st.spinner("Letar dagens bull..."):
            day_winner = _pick_bull(tickers, "day")
        if day_winner:
            _bull_card(day_winner, "day", theme_color, ticker_theme)
            if st.button("Öppna detaljer", key="open_day_bull"):
                st.session_state["detail_req"] = day_winner["ticker"]
        else:
            st.markdown(
                f"<div style='padding:18px;border-radius:20px;border:1px solid {MUTED}33;"
                f"color:{MUTED};text-align:center'>Ingen tydlig dagsvinnare just nu</div>",
                unsafe_allow_html=True)

    with c2:
        with st.spinner("Letar veckans bull..."):
            week_winner = _pick_bull(tickers, "week")
        if week_winner:
            _bull_card(week_winner, "week", theme_color, ticker_theme)
            if st.button("Öppna detaljer", key="open_week_bull"):
                st.session_state["detail_req"] = week_winner["ticker"]
        else:
            st.markdown(
                f"<div style='padding:18px;border-radius:20px;border:1px solid {MUTED}33;"
                f"color:{MUTED};text-align:center'>Ingen tydlig veckavinnare just nu</div>",
                unsafe_allow_html=True)
