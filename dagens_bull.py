# =====================================================================
#  dagens_bull.py  —  Dagens Bull + Veckans Bull för MoneyGrab
#  Renderas direkt i tabs[0] — inga egna tabs eller st.tabs() här.
#  Hetta-score = score10 * 10 viktat med relativ volym (0–100).
# =====================================================================

import streamlit as st
import pandas as pd
from sok_module import fetch, analyze

ACCENT = "#1199fa"
POS    = "#16c784"
NEG    = "#f6465d"
MUTED  = "#848e9c"
TXT    = "#eaecef"
ROCK_C = "#f0a020"

def _get_universe():    return st.session_state.get("_mg_universe", {})
def _get_ticker_theme():return st.session_state.get("_mg_ticker_theme", {})
def _get_theme_color(): return st.session_state.get("_mg_theme_color", {})


# ---------------------------------------------------------------
#  DATA PER TICKER
# ---------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _bull_data(ticker: str):
    try:
        df, _ = fetch(ticker)
    except Exception:
        return None
    if df is None or len(df) < 10:
        return None
    try:
        c = df["Close"].dropna()
        v = df["Volume"].dropna() if "Volume" in df.columns else None

        last      = float(c.iloc[-1])
        prev_day  = float(c.iloc[-2])
        prev_week = float(c.iloc[-6]) if len(c) >= 6 else float(c.iloc[0])

        day_ret  = (last / prev_day  - 1) * 100
        week_ret = (last / prev_week - 1) * 100

        rel_vol = 1.0
        if v is not None and len(v) >= 20:
            avg_vol = float(v.iloc[-21:-1].mean())
            if avg_vol > 0:
                rel_vol = float(v.iloc[-1]) / avg_vol

        a       = analyze(df)
        score10 = float(a.get("score10", 0))
        label   = a.get("label", "—")
        rsi     = float(a.get("rsi", 0))

        # Hetta = score viktat med volym, skala 0-100
        hetta = round(min(100, score10 * 10 * min(rel_vol, 2.0)), 1)

        return {
            "ticker":   ticker,
            "last":     last,
            "day_ret":  day_ret,
            "week_ret": week_ret,
            "rel_vol":  rel_vol,
            "label":    label,
            "score10":  score10,
            "hetta":    hetta,
            "rsi":      rsi,
        }
    except Exception:
        return None


# ---------------------------------------------------------------
#  SCAN ALLA + SORTERA
# ---------------------------------------------------------------
def _scan_all(tickers: list) -> list:
    results = []
    for t in tickers:
        d = _bull_data(t)
        if d:
            results.append(d)
    return results


def _top_day(results: list, n=10) -> list:
    """Dagens: positiv dag-ret + rel_vol >= 1.1, sorterat på hetta."""
    f = [r for r in results if r["day_ret"] > 0 and r["rel_vol"] >= 1.1]
    return sorted(f, key=lambda x: x["hetta"], reverse=True)[:n]


def _top_week(results: list, n=10) -> list:
    """Veckan: positiv vecko-ret + score >= 5, sorterat på hetta."""
    f = [r for r in results if r["week_ret"] > 0 and r["score10"] >= 5]
    return sorted(f, key=lambda x: x["hetta"], reverse=True)[:n]


# ---------------------------------------------------------------
#  BULL-KORT  (toppa listan)
# ---------------------------------------------------------------
def _render_hero_card(winner: dict, period: str, theme_color: dict, ticker_theme: dict):
    period_label = "DAGENS BULL" if period == "day" else "VECKANS BULL"
    ret_val      = winner["day_ret"] if period == "day" else winner["week_ret"]
    ret_days     = "idag" if period == "day" else "5 dagar"

    theme  = ticker_theme.get(winner["ticker"], "")
    color  = "#" + theme_color.get(theme, "1199fa")
    rsi    = winner["rsi"]
    rsi_col= NEG if rsi > 75 else (ROCK_C if rsi > 65 else TXT)
    vol_col= POS if winner["rel_vol"] >= 1.5 else (ROCK_C if winner["rel_vol"] >= 1.1 else MUTED)

    st.markdown(f"""
    <div style="
        background:linear-gradient(135deg,rgba(17,153,250,.08),rgba(13,17,24,.96));
        border:1px solid {color}55;
        border-radius:24px;
        padding:22px 26px 18px;
        margin-bottom:4px;
    ">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <span style="width:8px;height:8px;border-radius:50%;
                background:{color};display:inline-block"></span>
            <span style="font-size:.68rem;font-weight:700;letter-spacing:1.2px;
                color:{MUTED};text-transform:uppercase">{period_label}</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">
            <div>
                <div style="font-size:2.4rem;font-weight:800;color:#fff;
                    font-family:'Space Grotesk',sans-serif;line-height:1">{winner['ticker']}</div>
                <div style="margin-top:8px">
                    <span style="padding:3px 12px;border-radius:999px;font-size:.72rem;
                        font-weight:700;background:{color}22;color:{color};
                        border:1px solid {color}44">{winner['label']}</span>
                </div>
            </div>
            <div style="text-align:right">
                <div style="font-size:.62rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.8px">HETTA</div>
                <div style="font-size:2.8rem;font-weight:800;color:{color};
                    font-family:'Space Grotesk',sans-serif;line-height:1">{int(winner['hetta'])}</div>
            </div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:16px">
            <div>
                <div style="font-size:.62rem;color:{MUTED};text-transform:uppercase;letter-spacing:.6px">PRIS</div>
                <div style="font-size:1.15rem;font-weight:700;color:{TXT}">{winner['last']:.2f}</div>
            </div>
            <div>
                <div style="font-size:.62rem;color:{MUTED};text-transform:uppercase;letter-spacing:.6px">REL. VOLYM</div>
                <div style="font-size:1.15rem;font-weight:700;color:{vol_col}">{winner['rel_vol']:.2f}x</div>
            </div>
            <div>
                <div style="font-size:.62rem;color:{MUTED};text-transform:uppercase;letter-spacing:.6px">{ret_days.upper()}</div>
                <div style="font-size:1.15rem;font-weight:700;color:{POS}">+{ret_val:.1f}%</div>
            </div>
            <div>
                <div style="font-size:.62rem;color:{MUTED};text-transform:uppercase;letter-spacing:.6px">RANKING</div>
                <div style="font-size:1.15rem;font-weight:700;color:{ACCENT}">{int(winner['score10'])}/10</div>
            </div>
        </div>
        <div style="margin-top:12px">
            <div style="font-size:.62rem;color:{MUTED};text-transform:uppercase;letter-spacing:.6px">RSI</div>
            <div style="font-size:1.15rem;font-weight:700;color:{rsi_col}">{rsi:.0f}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("Öppna detaljer", key=f"hero_{period}_{winner['ticker']}"):
        st.session_state["detail_req"] = winner["ticker"]


# ---------------------------------------------------------------
#  RANKINGTABELL  (under kortet)
# ---------------------------------------------------------------
def _render_table(rows: list, period: str):
    if not rows:
        return
    ret_key   = "day_ret"  if period == "day"  else "week_ret"
    ret_label = "Dag %"    if period == "day"  else "Vecka %"

    table_rows = "".join(
        f"<tr style='border-bottom:1px solid rgba(255,255,255,.04)'>"
        f"<td style='padding:7px 10px;font-weight:700;color:{TXT}'>{r['ticker']}</td>"
        f"<td style='padding:7px 10px;color:{MUTED};font-size:.82rem'>{r['label']}</td>"
        f"<td style='padding:7px 10px;font-weight:700;color:{ACCENT}'>{r['hetta']:.0f}</td>"
        f"<td style='padding:7px 10px;color:{'#1199fa' if r['rel_vol']>=1.3 else TXT}'>{r['rel_vol']:.2f}</td>"
        f"<td style='padding:7px 10px;color:{POS}'>+{r[ret_key]:.1f}%</td>"
        f"<td style='padding:7px 10px;color:{ACCENT}'>{int(r['score10'])}</td>"
        f"</tr>"
        for r in rows
    )

    st.markdown(f"""
    <div style="border-radius:16px;overflow:hidden;border:1px solid rgba(255,255,255,.06);margin-top:10px">
        <table style="width:100%;border-collapse:collapse;font-size:.85rem">
            <thead>
                <tr style="background:rgba(255,255,255,.04)">
                    <th style="padding:8px 10px;text-align:left;color:{MUTED};font-size:.68rem;
                        text-transform:uppercase;letter-spacing:.7px;font-weight:600">Ticker</th>
                    <th style="padding:8px 10px;text-align:left;color:{MUTED};font-size:.68rem;
                        text-transform:uppercase;letter-spacing:.7px;font-weight:600">Läge</th>
                    <th style="padding:8px 10px;text-align:left;color:{MUTED};font-size:.68rem;
                        text-transform:uppercase;letter-spacing:.7px;font-weight:600">Hetta</th>
                    <th style="padding:8px 10px;text-align:left;color:{MUTED};font-size:.68rem;
                        text-transform:uppercase;letter-spacing:.7px;font-weight:600">Rel.vol</th>
                    <th style="padding:8px 10px;text-align:left;color:{MUTED};font-size:.68rem;
                        text-transform:uppercase;letter-spacing:.7px;font-weight:600">{ret_label}</th>
                    <th style="padding:8px 10px;text-align:left;color:{MUTED};font-size:.68rem;
                        text-transform:uppercase;letter-spacing:.7px;font-weight:600">Rank</th>
                </tr>
            </thead>
            <tbody style="background:rgba(13,17,24,.85)">
                {table_rows}
            </tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------
#  PUBLIK ENTRY-PUNKT
# ---------------------------------------------------------------
def render_dagens_bull():
    """
    Kallas från app.py i tabs[0].
    Kräver att app.py har satt:
        st.session_state["_mg_universe"]
        st.session_state["_mg_ticker_theme"]
        st.session_state["_mg_theme_color"]
    """
    universe     = _get_universe()
    theme_color  = _get_theme_color()
    ticker_theme = _get_ticker_theme()

    if not universe:
        return

    # Bygg unik ticker-lista
    seen = set(); tickers = []
    for ts in universe.values():
        for t in ts:
            if t not in seen:
                seen.add(t); tickers.append(t)

    with st.spinner(f"Scannar {len(tickers)} aktier..."):
        all_data = _scan_all(tickers)

    day_top  = _top_day(all_data)
    week_top = _top_week(all_data)

    total_scanned = len(all_data)

    # Två kolumner: Dagens | Veckans
    c1, c2 = st.columns(2)

    with c1:
        if day_top:
            _render_hero_card(day_top[0], "day", theme_color, ticker_theme)
            _render_table(day_top, "day")
        else:
            st.markdown(
                f"<div style='padding:20px;border-radius:20px;border:1px solid {MUTED}33;"
                f"color:{MUTED};text-align:center;font-size:.9rem'>"
                f"Ingen tydlig dagsvinnare just nu</div>",
                unsafe_allow_html=True)

    with c2:
        if week_top:
            _render_hero_card(week_top[0], "week", theme_color, ticker_theme)
            _render_table(week_top, "week")
        else:
            st.markdown(
                f"<div style='padding:20px;border-radius:20px;border:1px solid {MUTED}33;"
                f"color:{MUTED};text-align:center;font-size:.9rem'>"
                f"Ingen tydlig veckavinnare just nu</div>",
                unsafe_allow_html=True)

    st.caption(
        f"Skannade {total_scanned} aktier · uppdateras var 10:e minut · data ~15 min fördröjd. "
        f"Hetta = ranking viktat med relativ volym.")
