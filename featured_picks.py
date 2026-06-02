# ============================================================
#  FEATURED PICKS  —  drop-in module for MoneyGrab
#  Tre kort: Dagens Bull · Veckans Bull · Veckans Wildcard
#
#  - Dagens Bull   : starkaste momentum-/bull-läge just nu i universumet.
#  - Veckans Bull  : högst totalpoäng med bekräftad struktur (BOS↑ / A-B setup),
#                    hålls stabilt under veckan via cache.
#  - Veckans Wild  : microcap under $8 med rätt läge för en klättring
#                    (VÄNDNING/MOMENTUM/Rocketcase, BOS ej bearish, RSI ej överhett).
#
#  ANVÄNDNING i app.py (överst i HETA NU-fliken):
#       from featured_picks import render_featured_picks
#       render_featured_picks(scan, selected, market_movers,
#                             company_info, logo_url, THEME_COLOR, TICKER_THEME)
#
#  Funktionerna scan/market_movers/company_info/logo_url skickas in
#  så modulen slipper cirkulär import mot app.py.
# ============================================================

import datetime as _dt
import streamlit as st

POS, NEG, MUTED, TXT, ACCENT = "#16c784", "#f6465d", "#848e9c", "#eaecef", "#1199fa"
GOLD, PURPLE = "#f0a020", "#b06bff"


# ----------------------------------------------------------
#  Microcap-univers för wildcard (utöver day-screeners)
#  Billiga, volatila small/microcaps i dina teman.
# ----------------------------------------------------------
WILDCARD_POOL = [
    "QUBT", "BBAI", "RGTI", "ONDS", "OUST", "LAZR", "AEVA", "NNE", "DNN", "UUUU",
    "POET", "LWLG", "RXRX", "HIVE", "SOUN", "RKLB", "ASTS", "VIVO", "HOLO",
    "ARBE", "MVST", "INDI", "NA", "GSAT", "MNTS", "RDW",
]


def _is_microcap_under(a, info, price_cap=8.0, mcap_cap=2e9):
    last = a.get("last", 9999)
    if last is None or last > price_cap or last <= 0:
        return False
    mcap = info.get("mcap") if info else None
    if mcap is not None and mcap > mcap_cap:
        return False
    return True


def _wildcard_ok(a):
    """Rätt läge för en klättring: trendvändning/momentum, ej bearish, ej överhett."""
    if a.get("label") not in {"VÄNDNING", "MOMENTUM", "Rocketcase", "NEUTRAL/BYGGER"}:
        return False
    if a.get("bos") == "BEARISH":
        return False
    if a.get("cooling"):
        return False
    if a.get("rsi", 100) > 76:
        return False
    if a.get("ret_20", 0) < -25:        # inte i fritt fall
        return False
    return True


def _week_seed():
    """ISO-vecka som nyckel så veckans val ligger stabilt."""
    iso = _dt.date.today().isocalendar()
    return f"{iso[0]}-W{iso[1]}"


# ----------------------------------------------------------
#  KORT-RENDERING
# ----------------------------------------------------------
def _card(col, kicker, a, accent, company, logo, sub=""):
    if a is None:
        col.markdown(
            f"<div class='scard' style='border-color:{accent}33'>"
            f"<div class='sl' style='color:{accent}'>{kicker}</div>"
            f"<div style='color:{MUTED};font-size:.9rem;margin-top:8px'>"
            f"Ingen tydlig kandidat just nu.</div></div>",
            unsafe_allow_html=True)
        return None

    tkr = a["ticker"]
    last = a.get("last", 0)
    r20 = a.get("ret_20", 0)
    grade = a.get("setup_grade", "C")
    bos = a.get("bos", "INGEN")
    bos_txt = "BOS↑" if bos == "BULLISH" else "BOS↓" if bos == "BEARISH" else "BOS –"
    up1 = a.get("atr_up_1")
    sup = a.get("ob_support")
    name = company or tkr

    target_line = f"Mål +1 ATR: <b style='color:{POS}'>{up1:.2f}</b>" if up1 else ""
    stop_line = f"Stöd: <b style='color:{MUTED}'>{sup:.2f}</b>" if sup else ""

    col.markdown(
        f"<div class='scard' style='border-color:{accent}55;box-shadow:0 0 22px {accent}18'>"
        f"<div class='sl' style='color:{accent};font-weight:800'>{kicker}</div>"
        f"<div style='display:flex;align-items:center;gap:10px;margin-top:8px'>"
        f"<img src='{logo}' style='width:34px;height:34px;border-radius:8px'/>"
        f"<div><div style='font-size:1.15rem;font-weight:800;color:{TXT};line-height:1.1'>{tkr}</div>"
        f"<div style='font-size:.72rem;color:{MUTED}'>{name[:24]}</div></div>"
        f"<div style='margin-left:auto;text-align:right'>"
        f"<div style='font-size:1.2rem;font-weight:800;color:{accent}'>{a.get('score10',0)}/10</div>"
        f"<div style='font-size:.66rem;color:{MUTED}'>setup {grade}</div></div></div>"
        f"<div style='margin-top:10px;display:flex;gap:6px;flex-wrap:wrap'>"
        f"<span class='pill' style='background:{accent}22;color:{accent}'>{a.get('label','')}</span>"
        f"<span class='pill' style='background:rgba(255,255,255,.06);color:{TXT}'>{bos_txt}</span>"
        f"</div>"
        f"<div style='margin-top:10px;font-size:.84rem;line-height:1.7;color:{TXT}'>"
        f"Pris: <b>{last:.2f}</b> · 20d: "
        f"<b style='color:{POS if r20>=0 else NEG}'>{r20:+.1f}%</b><br>"
        f"{target_line}{' · ' if target_line and stop_line else ''}{stop_line}"
        f"</div>"
        + (f"<div style='margin-top:8px;font-size:.74rem;color:{MUTED}'>{sub}</div>" if sub else "")
        + "</div>",
        unsafe_allow_html=True)
    return tkr


# ----------------------------------------------------------
#  HUVUDFUNKTION
# ----------------------------------------------------------
def render_featured_picks(scan, selected, market_movers, company_info,
                          logo_url, theme_color=None, ticker_theme=None):
    st.markdown("### Veckans urval")
    st.caption("Dagens Bull uppdateras löpande · Veckans Bull & Wildcard låses per ISO-vecka.")

    # ---- scanna kärnuniversum (cachat via scan/fetch i app) ----
    scored = []
    for t in selected:
        a = scan(t)
        if a:
            scored.append(a)

    if not scored:
        st.info("Kunde inte hämta data för urvalet just nu.")
        return

    # ---------- DAGENS BULL: starkaste momentum/bull just nu ----------
    bull_cands = [a for a in scored
                  if a["label"] in {"MOMENTUM", "BULL", "Rocketcase"} and not a.get("cooling")]
    dagens = max(bull_cands, key=lambda x: (x["score10"], x.get("setup_score", 0)),
                 default=None)

    # ---------- VECKANS BULL: högst poäng + bekräftad struktur, låst per vecka ----------
    week = _week_seed()
    wk_bull_cands = [a for a in scored
                     if a.get("setup_grade") in {"A", "B"}
                     and a.get("bos") != "BEARISH"
                     and a["label"] in {"MOMENTUM", "BULL", "Rocketcase", "VÄNDNING"}]
    wk_bull_cands.sort(key=lambda x: (x.get("setup_score", 0), x["score10"], x.get("ret_20", 0)),
                       reverse=True)
    if st.session_state.get("_wk_bull_week") != week or "_wk_bull_tkr" not in st.session_state:
        if wk_bull_cands:
            st.session_state["_wk_bull_tkr"] = wk_bull_cands[0]["ticker"]
            st.session_state["_wk_bull_week"] = week
    veckans_bull_tkr = st.session_state.get("_wk_bull_tkr")
    veckans_bull = next((a for a in scored if a["ticker"] == veckans_bull_tkr), None) \
        or (wk_bull_cands[0] if wk_bull_cands else None)

    # ---------- VECKANS WILDCARD: microcap < $8 med rätt läge ----------
    # scanna både kärnuniversum + dedikerad pool + dagens small-cap-raketer
    wild_universe = set(WILDCARD_POOL)
    try:
        movers = market_movers("small_cap_gainers")
        wild_universe.update(movers[:25])
    except Exception:
        pass

    wild_cands = []
    seen = {a["ticker"]: a for a in scored}
    for t in wild_universe:
        a = seen.get(t) or scan(t)
        if not a:
            continue
        info = company_info(t) if company_info else {}
        if _is_microcap_under(a, info) and _wildcard_ok(a):
            # wildcard-score: väg setup + momentum + R/R mot stöd
            ws = a.get("setup_score", 0) * 0.5 + a["score10"] * 4 + max(0, a.get("ret_5", 0))
            wild_cands.append((ws, a, info))
    wild_cands.sort(key=lambda x: x[0], reverse=True)

    if st.session_state.get("_wk_wild_week") != week or "_wk_wild_tkr" not in st.session_state:
        if wild_cands:
            st.session_state["_wk_wild_tkr"] = wild_cands[0][1]["ticker"]
            st.session_state["_wk_wild_week"] = week
    wild_tkr = st.session_state.get("_wk_wild_tkr")
    wildcard = next(((a, i) for _, a, i in wild_cands if a["ticker"] == wild_tkr), None)
    if wildcard is None and wild_cands:
        wildcard = (wild_cands[0][1], wild_cands[0][2])

    # ---- rendera tre kort ----
    c1, c2, c3 = st.columns(3)

    def _name(t):
        try:
            return company_info(t).get("name", "") if company_info else ""
        except Exception:
            return ""

    if dagens:
        _card(c1, "DAGENS BULL", dagens, POS, _name(dagens["ticker"]),
              logo_url(dagens["ticker"]), sub="Starkaste fart-läget i ditt univers just nu.")
    else:
        _card(c1, "DAGENS BULL", None, POS, "", "")

    if veckans_bull:
        _card(c2, "VECKANS BULL", veckans_bull, GOLD, _name(veckans_bull["ticker"]),
              logo_url(veckans_bull["ticker"]),
              sub="Högsta setup-kvalitet med bekräftad struktur. Låst för veckan.")
    else:
        _card(c2, "VECKANS BULL", None, GOLD, "", "")

    if wildcard:
        wa, wi = wildcard
        _card(c3, "VECKANS WILDCARD", wa, PURPLE, (wi or {}).get("name", ""),
              logo_url(wa["ticker"]),
              sub=f"Microcap < $8 med läge för en klättring. {wa.get('levels_note','')[:60]}")
    else:
        _card(c3, "VECKANS WILDCARD", None, PURPLE, "", "")

    # ---- klickbara knappar för detaljvy ----
    bcols = st.columns(3)
    for col, pick in zip(bcols,
                         [dagens, veckans_bull, wildcard[0] if wildcard else None]):
        if pick:
            if col.button(f"Öppna {pick['ticker']}", key=f"feat_{pick['ticker']}",
                          use_container_width=True):
                st.session_state["detail_req"] = pick["ticker"]
                st.rerun()
