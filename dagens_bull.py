# =====================================================================
#  dagens_bull.py  —  Dagens Bull + Veckans Bull
#  Två kort sida vid sida. Dagens = blå, Veckans = lila.
#  Bolagsnamn hämtas via yfinance och visas under ticker.
#  Ingen st.tabs() här — renderas direkt i tabs[0] i app.py.
# =====================================================================

import streamlit as st

try:
    import yfinance as yf
except Exception:
    yf = None

from sok_module import fetch, analyze

ACCENT  = "#1199fa"   # blå  — Dagens Bull
WEEK_C  = "#b06bff"   # lila — Veckans Bull
POS     = "#16c784"
NEG     = "#f6465d"
MUTED   = "#848e9c"
TXT     = "#eaecef"
ROCK_C  = "#f0a020"

def _get_universe():     return st.session_state.get("_mg_universe", {})
def _get_ticker_theme(): return st.session_state.get("_mg_ticker_theme", {})
def _get_theme_color():  return st.session_state.get("_mg_theme_color", {})


# ---------------------------------------------------------------
#  BOLAGSNAMN
# ---------------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def _company_name(ticker: str) -> str:
    """Hämtar kortnamn. Cachas 24h. Returnerar '' vid fel."""
    if yf is None:
        return ""
    try:
        info = yf.Ticker(ticker).info or {}
        name = info.get("shortName") or info.get("longName") or ""
        # Förkorta vid behov
        if len(name) > 28:
            name = name[:26].rstrip() + "…"
        return name
    except Exception:
        return ""


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
        hetta   = round(min(100, score10 * 10 * min(rel_vol, 2.0)), 1)

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
#  SCAN + SORTERA
# ---------------------------------------------------------------
def _scan_all(tickers: list) -> list:
    results = []
    for t in tickers:
        d = _bull_data(t)
        if d:
            results.append(d)
    return results

def _top_day(results: list, n=10) -> list:
    f = [r for r in results if r["day_ret"] > 0 and r["rel_vol"] >= 1.1]
    return sorted(f, key=lambda x: x["hetta"], reverse=True)[:n]

def _top_week(results: list, n=10) -> list:
    f = [r for r in results if r["week_ret"] > 0 and r["score10"] >= 5]
    return sorted(f, key=lambda x: x["hetta"], reverse=True)[:n]


# ---------------------------------------------------------------
#  HERO-KORT
# ---------------------------------------------------------------
def _render_hero(winner: dict, period: str):
    is_day   = period == "day"
    color    = ACCENT if is_day else WEEK_C
    title    = "DAGENS BULL" if is_day else "VECKANS BULL"
    ret_val  = winner["day_ret"] if is_day else winner["week_ret"]
    ret_lbl  = "5 DAGAR" if is_day else "5 DAGAR"   # båda visar 5d i screenshoten

    name     = _company_name(winner["ticker"])
    rsi      = winner["rsi"]
    rsi_col  = NEG if rsi > 75 else (ROCK_C if rsi > 65 else TXT)
    vol_col  = POS if winner["rel_vol"] >= 1.5 else (ROCK_C if winner["rel_vol"] >= 1.1 else MUTED)

    st.markdown(f"""
    <div style="
        background: linear-gradient(160deg, {color}12 0%, rgba(11,14,22,.97) 60%);
        border: 1px solid {color}60;
        border-radius: 24px;
        padding: 20px 22px 18px;
        margin-bottom: 2px;
    ">
        <!-- Rubrik-rad -->
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:12px">
            <span style="width:8px;height:8px;border-radius:50%;
                background:{color};display:inline-block;
                box-shadow:0 0 6px {color}"></span>
            <span style="font-size:.65rem;font-weight:700;letter-spacing:1.4px;
                color:{MUTED};text-transform:uppercase">{title}</span>
        </div>

        <!-- Ticker + Hetta -->
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
                <div style="font-size:2.6rem;font-weight:800;color:#fff;
                    font-family:'Space Grotesk',sans-serif;line-height:1;
                    letter-spacing:-1px">{winner['ticker']}</div>
                {"" if not name else f"<div style='font-size:.78rem;color:{MUTED};margin-top:3px;font-weight:500'>{name}</div>"}
                <div style="margin-top:9px">
                    <span style="padding:4px 13px;border-radius:999px;font-size:.72rem;
                        font-weight:700;background:{color}20;color:{color};
                        border:1px solid {color}50">{winner['label']}</span>
                </div>
            </div>
            <div style="text-align:right">
                <div style="font-size:.62rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.8px;font-weight:600">HETTA</div>
                <div style="font-size:3rem;font-weight:800;color:{color};
                    font-family:'Space Grotesk',sans-serif;line-height:1">
                    {int(winner['hetta'])}</div>
            </div>
        </div>

        <!-- Nyckeltal-rad -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);
            gap:10px;margin-top:16px">
            <div>
                <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.6px;font-weight:600">PRIS</div>
                <div style="font-size:1.05rem;font-weight:700;color:{TXT};
                    margin-top:2px">{winner['last']:.2f}</div>
            </div>
            <div>
                <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.6px;font-weight:600">REL. VOLYM</div>
                <div style="font-size:1.05rem;font-weight:700;color:{vol_col};
                    margin-top:2px">{winner['rel_vol']:.1f}x</div>
            </div>
            <div>
                <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.6px;font-weight:600">{ret_lbl}</div>
                <div style="font-size:1.05rem;font-weight:700;color:{POS};
                    margin-top:2px">+{ret_val:.1f}%</div>
            </div>
            <div>
                <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.6px;font-weight:600">RANKING</div>
                <div style="font-size:1.05rem;font-weight:700;color:{ACCENT};
                    margin-top:2px">{int(winner['score10'])}/10</div>
            </div>
        </div>

        <!-- RSI -->
        <div style="margin-top:12px">
            <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                letter-spacing:.6px;font-weight:600">RSI</div>
            <div style="font-size:1.05rem;font-weight:700;color:{rsi_col};
                margin-top:2px">{rsi:.0f}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("Öppna detaljer", key=f"bull_btn_{period}_{winner['ticker']}",
                 use_container_width=True):
        st.session_state["detail_req"] = winner["ticker"]


# ---------------------------------------------------------------
#  RANKINGTABELL
# ---------------------------------------------------------------
def _render_table(rows: list, period: str):
    if not rows:
        return
    color     = ACCENT if period == "day" else WEEK_C
    ret_key   = "day_ret" if period == "day" else "week_ret"
    ret_label = "5d %" 

    title = "Hetast just nu" if period == "day" else "Starkast denna vecka"
    st.markdown(f"<div style='font-size:1rem;font-weight:700;color:#fff;"
                f"margin:14px 0 6px'>{title}</div>", unsafe_allow_html=True)

    table_rows = "".join(
        f"<tr style='border-bottom:1px solid rgba(255,255,255,.04)'>"
        f"<td style='padding:7px 10px;font-weight:700;color:{TXT}'>{r['ticker']}</td>"
        f"<td style='padding:7px 10px;color:{MUTED};font-size:.82rem'>{r['label']}</td>"
        f"<td style='padding:7px 10px;font-weight:700;color:{color}'>{r['hetta']:.0f}</td>"
        f"<td style='padding:7px 10px;color:{POS if r['rel_vol']>=1.3 else TXT}'>{r['rel_vol']:.2f}</td>"
        f"<td style='padding:7px 10px;color:{POS}'>+{r[ret_key]:.1f}%</td>"
        f"<td style='padding:7px 10px;color:{ACCENT}'>{int(r['score10'])}</td>"
        f"</tr>"
        for r in rows
    )

    st.markdown(f"""
    <div style="border-radius:16px;overflow:hidden;
        border:1px solid rgba(255,255,255,.06)">
        <table style="width:100%;border-collapse:collapse;font-size:.84rem">
            <thead>
                <tr style="background:rgba(255,255,255,.04)">
                    {"".join(
                        f"<th style='padding:8px 10px;text-align:left;color:{MUTED};"
                        f"font-size:.65rem;text-transform:uppercase;letter-spacing:.7px;"
                        f"font-weight:600'>{h}</th>"
                        for h in ["Ticker","Läge","Hetta","Rel.vol","5d %","Rank"]
                    )}
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
    universe = _get_universe()
    if not universe:
        return

    theme_color  = _get_theme_color()
    ticker_theme = _get_ticker_theme()

    # Unik ticker-lista
    seen = set(); tickers = []
    for ts in universe.values():
        for t in ts:
            if t not in seen:
                seen.add(t); tickers.append(t)

    with st.spinner(f"Scannar {len(tickers)} aktier..."):
        all_data = _scan_all(tickers)

    day_top  = _top_day(all_data)
    week_top = _top_week(all_data)

    c1, c2 = st.columns(2)

    with c1:
        if day_top:
            _render_hero(day_top[0], "day")
            _render_table(day_top, "day")
        else:
            st.markdown(
                f"<div style='padding:20px;border-radius:20px;"
                f"border:1px solid {MUTED}33;color:{MUTED};"
                f"text-align:center'>Ingen dagsvinnare just nu</div>",
                unsafe_allow_html=True)

    with c2:
        if week_top:
            _render_hero(week_top[0], "week")
            _render_table(week_top, "week")
        else:
            st.markdown(
                f"<div style='padding:20px;border-radius:20px;"
                f"border:1px solid {MUTED}33;color:{MUTED};"
                f"text-align:center'>Ingen veckavinnare just nu</div>",
                unsafe_allow_html=True)

    st.caption(
        f"Skannade {len(all_data)} aktier · uppdateras var 10:e minut · "
        f"data ~15 min fördröjd. Hetta = ranking viktat med relativ volym.")
