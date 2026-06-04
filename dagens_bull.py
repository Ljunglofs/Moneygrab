# =====================================================================
#  dagens_bull.py  —  Dagens Bull + Veckans Bull
#  Använder fetch() + analyze() från sok_module direkt.
#  Ingen dubbel-fetch. Staplade vertikalt. Blå / Lila.
# =====================================================================

import streamlit as st

try:
    import yfinance as yf
except Exception:
    yf = None

from sok_module import fetch, analyze

ACCENT = "#1199fa"
WEEK_C = "#b06bff"
POS    = "#16c784"
NEG    = "#f6465d"
MUTED  = "#848e9c"
TXT    = "#eaecef"
ROCK_C = "#f0a020"


def _get_universe():
    return st.session_state.get("_mg_universe", {})


@st.cache_data(ttl=86400, show_spinner=False)
def _company_name(ticker):
    if yf is None:
        return ""
    try:
        info = yf.Ticker(ticker).info or {}
        name = info.get("shortName") or info.get("longName") or ""
        if len(name) > 28:
            name = name[:26].rstrip() + "…"
        return name
    except Exception:
        return ""


@st.cache_data(ttl=600, show_spinner=False)
def _bull_data(ticker):
    """
    Hämtar data via fetch() (cachat 5 min i sok_module).
    Beräknar dag-rörelse direkt från df.
    Vecko-rörelse = ret_5 från analyze() — samma beräkning, ingen extra fetch.
    """
    try:
        df, _ = fetch(ticker)
    except Exception:
        return None
    if df is None or len(df) < 10:
        return None
    try:
        a = analyze(df)

        close    = df["Close"].dropna()
        last     = float(close.iloc[-1])
        prev_day = float(close.iloc[-2])
        day_ret  = (last / prev_day - 1) * 100

        # week_ret = ret_5 redan beräknat av analyze
        week_ret = float(a.get("ret_5", 0))

        rel_vol  = float(a.get("rel_vol", 1.0))
        score10  = float(a.get("score10", 0))
        label    = a.get("label", "—")
        rsi      = float(a.get("rsi", 0))
        hetta    = round(min(100, score10 * 10 * min(rel_vol, 2.0)), 1)

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


def _scan_all(tickers):
    results = []
    for t in tickers:
        d = _bull_data(t)
        if d is not None:
            results.append(d)
    return results


def _top_day(results, n=10):
    f = [r for r in results if r["day_ret"] > 0 and r["rel_vol"] >= 1.1]
    return sorted(f, key=lambda x: x["hetta"], reverse=True)[:n]


def _top_week(results, n=10):
    # Sållar bort paraboliska pumpar (ENAFF-typ) och avsvalnande namn.
    def _pump(r):
        # Extrem spik = nästan alltid blow-off, oavsett volym
        if r["week_ret"] > 80:
            return True
        # Stor spik + extremt RSI utan tydlig volymbekräftelse
        if r["week_ret"] > 40 and r["rsi"] >= 80 and r["rel_vol"] < 1.5:
            return True
        return False
    f = [r for r in results
         if r["week_ret"] > 0
         and r["label"] != "AVSVALNING"
         and not _pump(r)]
    return sorted(f, key=lambda x: x["week_ret"], reverse=True)[:n]


def _hero_card(winner, period):
    is_day  = (period == "day")
    color   = ACCENT if is_day else WEEK_C
    title   = "DAGENS BULL" if is_day else "VECKANS BULL"
    ret_val = winner["day_ret"] if is_day else winner["week_ret"]
    ret_lbl = "IDAG" if is_day else "5 DAGAR"

    name    = _company_name(winner["ticker"])
    rsi     = winner["rsi"]
    rsi_col = NEG if rsi > 75 else (ROCK_C if rsi > 65 else TXT)
    vol_col = POS if winner["rel_vol"] >= 1.5 else (ROCK_C if winner["rel_vol"] >= 1.1 else MUTED)
    name_html = (
        f'<div style="font-size:.78rem;color:{MUTED};margin-top:4px">{name}</div>'
        if name else ""
    )

    st.markdown(f"""
    <div style="
        background:linear-gradient(160deg,{color}10 0%,rgba(11,14,22,.97) 55%);
        border:1px solid {color}55;
        border-radius:24px;
        padding:20px 22px 18px;
        margin-bottom:12px;
    ">
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:10px">
            <span style="width:8px;height:8px;border-radius:50%;background:{color};
                display:inline-block;box-shadow:0 0 7px {color}aa"></span>
            <span style="font-size:.64rem;font-weight:700;letter-spacing:1.5px;
                color:{MUTED};text-transform:uppercase">{title}</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
                <div style="font-size:2.5rem;font-weight:800;color:#fff;
                    font-family:'Space Grotesk',sans-serif;line-height:1;
                    letter-spacing:-1px">{winner['ticker']}</div>
                {name_html}
                <div style="margin-top:9px">
                    <span style="padding:4px 13px;border-radius:999px;font-size:.72rem;
                        font-weight:700;background:{color}20;color:{color};
                        border:1px solid {color}45">{winner['label']}</span>
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
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px">
            <div>
                <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.6px;font-weight:600">PRIS</div>
                <div style="font-size:1.05rem;font-weight:700;color:{TXT};margin-top:2px">
                    {winner['last']:.2f}</div>
            </div>
            <div>
                <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.6px;font-weight:600">REL. VOLYM</div>
                <div style="font-size:1.05rem;font-weight:700;color:{vol_col};margin-top:2px">
                    {winner['rel_vol']:.1f}x</div>
            </div>
            <div>
                <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.6px;font-weight:600">{ret_lbl}</div>
                <div style="font-size:1.05rem;font-weight:700;color:{POS};margin-top:2px">
                    +{ret_val:.1f}%</div>
            </div>
            <div>
                <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                    letter-spacing:.6px;font-weight:600">RANKING</div>
                <div style="font-size:1.05rem;font-weight:700;color:{ACCENT};margin-top:2px">
                    {int(winner['score10'])}/10</div>
            </div>
        </div>
        <div style="margin-top:12px">
            <div style="font-size:.60rem;color:{MUTED};text-transform:uppercase;
                letter-spacing:.6px;font-weight:600">RSI</div>
            <div style="font-size:1.05rem;font-weight:700;color:{rsi_col};margin-top:2px">
                {rsi:.0f}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("Öppna detaljer", key=f"bull_{period}_{winner['ticker']}",
                 use_container_width=True):
        st.session_state["detail_req"] = winner["ticker"]


def _ranking_table(rows, period):
    if not rows:
        return
    color   = ACCENT if period == "day" else WEEK_C
    ret_key = "day_ret" if period == "day" else "week_ret"
    title   = "Hetast just nu" if period == "day" else "Starkast denna vecka"

    st.markdown(
        f"<div style='font-size:1rem;font-weight:700;color:#fff;"
        f"margin:14px 0 6px'>{title}</div>",
        unsafe_allow_html=True)

    rows_html = ""
    for r in rows:
        rows_html += (
            f"<tr style='border-bottom:1px solid rgba(255,255,255,.04)'>"
            f"<td style='padding:7px 10px;font-weight:700;color:{TXT}'>{r['ticker']}</td>"
            f"<td style='padding:7px 10px;color:{MUTED};font-size:.82rem'>{r['label']}</td>"
            f"<td style='padding:7px 10px;font-weight:700;color:{color}'>{r['hetta']:.0f}</td>"
            f"<td style='padding:7px 10px;color:{POS if r['rel_vol']>=1.3 else TXT}'>"
            f"{r['rel_vol']:.2f}</td>"
            f"<td style='padding:7px 10px;color:{POS}'>+{r[ret_key]:.1f}%</td>"
            f"<td style='padding:7px 10px;color:{ACCENT}'>{int(r['score10'])}</td>"
            f"</tr>"
        )

    headers_html = "".join(
        f"<th style='padding:8px 10px;text-align:left;color:{MUTED};"
        f"font-size:.65rem;text-transform:uppercase;letter-spacing:.7px;"
        f"font-weight:600'>{h}</th>"
        for h in ["Ticker", "Läge", "Hetta", "Rel.vol", "5d %", "Rank"]
    )

    st.markdown(f"""
    <div style="border-radius:16px;overflow:hidden;border:1px solid rgba(255,255,255,.06)">
        <table style="width:100%;border-collapse:collapse;font-size:.84rem">
            <thead>
                <tr style="background:rgba(255,255,255,.04)">{headers_html}</tr>
            </thead>
            <tbody style="background:rgba(13,17,24,.85)">{rows_html}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


def render_dagens_bull():
    universe = _get_universe()
    if not universe:
        st.warning("Universe ej laddat.")
        return

    seen = set()
    tickers = []
    for ts in universe.values():
        for t in ts:
            if t not in seen:
                seen.add(t)
                tickers.append(t)

    with st.spinner(f"Scannar {len(tickers)} aktier..."):
        all_data = _scan_all(tickers)

    day_top  = _top_day(all_data)
    week_top = _top_week(all_data)

    # ---- HETAST JUST NU (ren ranking — Dagens Bull bor i Veckans urval, ej dubblerad här) ----
    if day_top:
        _ranking_table(day_top, "day")
    else:
        st.info("Inga dagsvinnare matchade filtret just nu.")

    st.markdown(
        "<hr style='border:none;border-top:1px solid rgba(255,255,255,.06);margin:28px 0'>",
        unsafe_allow_html=True)

    # ---- STARKAST DENNA VECKA (ren ranking — Veckans Bull bor i Veckans urval) ----
    if week_top:
        _ranking_table(week_top, "week")
    else:
        st.info("Inga veckavinnare matchade filtret just nu.")

    st.caption(
        f"Skannade {len(all_data)} aktier · uppdateras var 10:e minut · "
        f"data ~15 min fördröjd. Hetta = ranking viktat med relativ volym.")
