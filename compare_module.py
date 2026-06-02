# ============================================================
#  COMPARE / UI-KOMPONENTER  —  drop-in module for MoneyGrab
#  Barebone-inspirerat, MoneyGrabs egna färger.
#
#  Innehåller:
#   - segment_bar()      : segmenterad stapel (Speed/Intelligence-stil)
#   - ranked_rows()      : rankade rad-kort med nummer-badge + logo
#   - render_compare_tab(): JÄMFÖR-vy, skriv in tickers → duell sida vid sida
#
#  ANVÄNDNING i app.py:
#       from compare_module import render_compare_tab, ranked_rows, segment_bar
#       ...
#       with tabs[X]:
#           render_compare_tab(scan, company_info, logo_url)
#
#  scan/company_info/logo_url skickas in för att undvika cirkulär import.
# ============================================================

import streamlit as st

# MoneyGrabs palett
ACCENT, CYAN = "#1199fa", "#58d5e0"
POS, NEG, MUTED, TXT = "#16c784", "#f6465d", "#848e9c", "#eaecef"
GOLD, SILVER, BRONZE = "#f0a020", "#c0c8d4", "#cd7f32"
PURPLE = "#b06bff"


# ----------------------------------------------------------
#  SEGMENT-STAPEL  (Barebone "Speed/Intelligence"-stil)
#  value 0–max, ritas som ifyllda + tomma segment.
# ----------------------------------------------------------
def segment_bar(label, value, maxv=10, segments=11, color=ACCENT):
    filled = int(round((max(0, min(value, maxv)) / maxv) * segments))
    segs = ""
    for i in range(segments):
        on = i < filled
        bg = color if on else "rgba(255,255,255,.08)"
        glow = f"box-shadow:0 0 6px {color}88;" if on else ""
        segs += (f"<span style='display:inline-block;width:14px;height:7px;border-radius:3px;"
                 f"margin-right:3px;background:{bg};{glow}'></span>")
    return (
        f"<div style='display:flex;align-items:center;gap:10px;margin:5px 0'>"
        f"<span style='width:84px;color:{MUTED};font-size:.72rem;text-transform:uppercase;"
        f"letter-spacing:.5px'>{label}</span>"
        f"<span style='flex:1;white-space:nowrap'>{segs}</span>"
        f"<span style='color:{TXT};font-weight:700;font-size:.85rem;min-width:30px;"
        f"text-align:right;font-family:Space Grotesk,sans-serif'>{value:.1f}</span></div>"
    )


# ----------------------------------------------------------
#  RANKADE RAD-KORT  (Barebone "Trending"-stil)
#  rows: lista av analysobjekt (från scan). Klickbar → detaljvy.
# ----------------------------------------------------------
def _badge(rank):
    col = {1: GOLD, 2: SILVER, 3: BRONZE}.get(rank)
    if col:
        return (f"<div style='width:30px;height:30px;border-radius:50%;flex:none;"
                f"background:{col}22;border:1.5px solid {col};color:{col};"
                f"display:flex;align-items:center;justify-content:center;"
                f"font-weight:800;font-size:.9rem'>{rank}</div>")
    return (f"<div style='width:30px;height:30px;flex:none;color:{MUTED};"
            f"display:flex;align-items:center;justify-content:center;"
            f"font-weight:700;font-size:.9rem'>{rank}</div>")


def ranked_rows(rows, logo_url, company_info, key, on_click=True):
    """Renderar rankade rad-kort. Returnerar vald ticker (eller None).
    Varje rad = HTML-kort ovanpå en transparent klick-knapp."""
    if not rows:
        st.info("Inga namn matchar just nu.")
        return None

    rows = sorted(rows, key=lambda x: x.get("score10", 0), reverse=True)
    chosen = None

    for i, a in enumerate(rows, start=1):
        tkr = a["ticker"]
        try:
            name = company_info(tkr).get("name", "") if company_info else ""
        except Exception:
            name = ""
        name = (name[:30] + "…") if len(name) > 31 else name
        last = a.get("last", 0)
        r5 = a.get("ret_5", 0)
        chg_col = POS if r5 >= 0 else NEG
        acc = a.get("color", ACCENT)
        bos = a.get("bos", "INGEN")
        bos_txt = "BOS↑" if bos == "BULLISH" else "BOS↓" if bos == "BEARISH" else ""
        grade = a.get("setup_grade", "")

        if on_click and st.button(f"{tkr}", key=f"{key}_{tkr}_{i}", use_container_width=True):
            chosen = tkr

        st.markdown(
            f"<div class='rank-row' style='margin-top:-56px;pointer-events:none;position:relative'>"
            f"<div style='display:flex;align-items:center;gap:12px'>"
            f"{_badge(i)}"
            f"<img src='{logo_url(tkr)}' style='width:34px;height:34px;border-radius:9px;flex:none'/>"
            f"<div style='flex:1;min-width:0'>"
            f"<div style='font-weight:800;color:{TXT};font-size:1.02rem;line-height:1.1'>{tkr}"
            f"<span style='font-size:.7rem;font-weight:700;color:{acc};margin-left:8px'>{a.get('label','')}</span></div>"
            f"<div style='color:{MUTED};font-size:.74rem;white-space:nowrap;overflow:hidden;"
            f"text-overflow:ellipsis'>{name}</div></div>"
            f"<div style='text-align:right;flex:none'>"
            f"<div style='font-weight:800;color:{TXT};font-size:1rem'>{last:,.2f}</div>"
            f"<div style='font-size:.8rem;font-weight:700;color:{chg_col}'>{r5:+.1f}%</div></div>"
            f"<div style='text-align:right;flex:none;min-width:54px'>"
            f"<div style='font-weight:800;color:{acc};font-size:1.05rem'>{a.get('score10',0)}/10</div>"
            f"<div style='font-size:.66rem;color:{MUTED}'>{('setup '+grade) if grade else ''} {bos_txt}</div>"
            f"</div></div></div>",
            unsafe_allow_html=True)

    return chosen


# ----------------------------------------------------------
#  JÄMFÖR-VY  (Barebone "Compare and decide"-stil)
# ----------------------------------------------------------
def _verdict(cards):
    """Enkel sammanvägd vinnare: högst (score10, setup_score)."""
    if not cards:
        return None
    best = max(cards, key=lambda a: (a.get("score10", 0), a.get("setup_score", 0)))
    return best["ticker"]


def render_compare_tab(scan, company_info, logo_url):
    st.markdown("<div class='hero-title'>Jämför & avgör</div>", unsafe_allow_html=True)
    st.markdown("<div class='hero-sub'>Skriv 2–4 tickers och ställ dem mot varandra — "
                "score, setup, BOS och nivåer sida vid sida.</div>", unsafe_allow_html=True)

    raw = st.text_input("Tickers", placeholder="t.ex. NVDA, AMD, MRVL",
                        label_visibility="collapsed", key="cmp_input")
    tickers = [t.strip().upper() for t in raw.replace(" ", ",").split(",") if t.strip()][:4]

    if not tickers:
        st.caption("Tips: separera med komma eller mellanslag. Max 4 åt gången.")
        return

    cards = []
    prog = st.progress(0.0)
    for i, t in enumerate(tickers):
        a = scan(t)
        prog.progress((i + 1) / len(tickers))
        if a:
            cards.append(a)
    prog.empty()

    if not cards:
        st.error("Hittade ingen data. Kolla stavning (svenska aktier behöver .ST).")
        return

    winner = _verdict(cards)

    # ---- duell-kort sida vid sida ----
    cols = st.columns(len(cards))
    for col, a in zip(cols, cards):
        tkr = a["ticker"]
        try:
            name = company_info(tkr).get("name", "") if company_info else ""
        except Exception:
            name = ""
        acc = a.get("color", ACCENT)
        is_win = (tkr == winner)
        win_badge = (f"<span class='pill' style='background:{GOLD}22;color:{GOLD};"
                     f"border:1px solid {GOLD}'>VINNARE</span>") if is_win else ""
        border = GOLD if is_win else acc
        r5 = a.get("ret_5", 0)
        sup = a.get("ob_support")
        res = a.get("ob_resist")

        body = (
            f"<div class='scard' style='border-color:{border}{'aa' if is_win else '55'};"
            f"box-shadow:0 0 26px {border}{'33' if is_win else '18'};padding:16px'>"
            f"<div style='display:flex;align-items:center;gap:10px'>"
            f"<img src='{logo_url(tkr)}' style='width:38px;height:38px;border-radius:10px'/>"
            f"<div style='flex:1;min-width:0'>"
            f"<div style='font-size:1.3rem;font-weight:800;color:{TXT};line-height:1'>{tkr}</div>"
            f"<div style='font-size:.68rem;color:{MUTED};white-space:nowrap;overflow:hidden;"
            f"text-overflow:ellipsis'>{name[:18]}</div></div></div>"
            f"<div style='margin-top:8px'>{win_badge}"
            f"<span class='pill' style='background:{acc}22;color:{acc};margin-left:4px'>{a.get('label','')}</span></div>"
            f"<div style='margin:12px 0 6px'>"
            + segment_bar("Score", a.get("score10", 0), 10, 11, acc)
            + segment_bar("Setup", a.get("setup_score", 0) / 10, 10, 11, GOLD)
            + segment_bar("Momentum", a.get("momentum", 0) / 3.5, 10, 11, PURPLE)
            + segment_bar("Styrka", a.get("strength", 0) / 4, 10, 11, CYAN)
            + "</div>"
            f"<div style='font-size:.8rem;line-height:1.7;color:{TXT};margin-top:8px'>"
            f"Pris: <b>{a.get('last',0):,.2f}</b> · 5d: <b style='color:{POS if r5>=0 else NEG}'>{r5:+.1f}%</b><br>"
            f"BOS: <b>{a.get('bos','—')}</b> · Setup <b>{a.get('setup_grade','—')}</b><br>"
            f"Stöd: <b>{sup if sup is not None else '—'}</b> · Motstånd: <b>{res if res is not None else '—'}</b>"
            f"</div></div>"
        )
        col.markdown(body, unsafe_allow_html=True)

    # ---- slutsats ----
    if winner:
        wcard = next(a for a in cards if a["ticker"] == winner)
        st.markdown(
            f"<div class='scard' style='margin-top:14px;border-color:{GOLD}55'>"
            f"<span class='sl' style='color:{GOLD}'>MOTORNS SLUTSATS</span>"
            f"<div style='font-size:1rem;color:{TXT};margin-top:6px'>"
            f"<b style='color:{GOLD}'>{winner}</b> har starkast samlat läge här "
            f"({wcard.get('score10')}/10, setup {wcard.get('setup_grade')}, "
            f"{wcard.get('bos')}). {wcard.get('levels_note','')}</div></div>",
            unsafe_allow_html=True)
        st.caption("Teknisk jämförelse, inte finansiell rådgivning. Säger inget om fundamenta eller nyhetsrisk.")
