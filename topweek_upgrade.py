# =====================================================================
#  MONEYGRAB — VECKANS RAKETER  (drop-in)
#  Topp 5 starkast senaste 5 dagarna, överst på förstasidan.
#  Återanvänder scan() + ret_5 ur analyze() — ingen ny motor behövs.
# =====================================================================


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 1  ·  Klistra in nära dina andra helpers i app.py
#  (efter company_logo_html / get_short_name).
#  KRÄVER company_logo_html() från detaljvy-uppgraden. Saknar du den,
#  byt ut anropet mot logo_url()/monogram.
# ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def top_week_data(tickers):
    """Scannar universumet och returnerar topp 5 på 5-dagarsavkastning.
    Cachas 15 min och delar fetch-cache med HETA NU — ingen dubbel scan."""
    rows = []
    for t in tickers:
        a = scan(t)
        if not a:
            continue
        r5 = a.get("ret_5")
        if r5 is None:
            continue
        rows.append({
            "ticker": t, "ret_5": float(r5), "ret_20": float(a.get("ret_20", 0)),
            "label": a["label"], "color": a["color"], "score10": a["score10"],
            "rel_vol": float(a.get("rel_vol", 1)),
        })
    rows.sort(key=lambda x: x["ret_5"], reverse=True)
    return rows[:5]


def render_top_week(tickers):
    data = top_week_data(tuple(sorted(tickers)))
    if not data:
        return

    st.markdown(
        "<div style='display:flex;align-items:baseline;gap:12px;margin:.1rem 0 .7rem'>"
        "<div style='font-family:\"Space Grotesk\",sans-serif;font-weight:700;font-size:1.18rem;"
        "color:#fff;letter-spacing:-.3px'>Veckans raketer</div>"
        f"<div style='font-size:.78rem;color:{MUTED}'>Starkast senaste 5 dagarna · ditt universum</div>"
        "</div>", unsafe_allow_html=True)

    cols = st.columns(len(data))
    for i, (col, d) in enumerate(zip(cols, data)):
        tk    = d["ticker"]
        info  = company_info(tk)
        name  = get_short_name(tk) or ""
        c     = d["color"]
        r5    = d["ret_5"]
        rcol  = POS if r5 >= 0 else NEG
        arrow = "▲" if r5 >= 0 else "▼"
        logo  = company_logo_html(tk, info, size=34)

        col.markdown(
            f"<div style='background:#131922;border:1px solid rgba(255,255,255,.06);"
            f"border-top:2px solid {rcol};border-radius:15px;padding:13px 13px 12px;"
            f"position:relative;min-height:152px'>"
            f"<div style='position:absolute;top:11px;right:13px;font-family:\"Space Grotesk\",sans-serif;"
            f"font-weight:700;font-size:.7rem;color:{c}'>{d['score10']}/10</div>"
            f"<div style='font-family:\"Space Grotesk\",sans-serif;font-weight:700;font-size:.72rem;"
            f"color:{MUTED};margin-bottom:8px'>#{i+1}</div>"
            f"<div style='display:flex;align-items:center;gap:9px;margin-bottom:10px'>{logo}"
            f"<div style='min-width:0'><div style='font-family:\"Space Grotesk\",sans-serif;font-weight:700;"
            f"font-size:1rem;color:#fff;line-height:1'>{tk}</div>"
            f"<div style='font-size:.64rem;color:{MUTED};white-space:nowrap;overflow:hidden;"
            f"text-overflow:ellipsis;max-width:92px;margin-top:2px'>{name}</div></div></div>"
            f"<div style='font-family:\"Space Grotesk\",sans-serif;font-weight:800;font-size:1.5rem;"
            f"color:{rcol};line-height:1'>{arrow} {r5:+.1f}%</div>"
            f"<div style='font-size:.58rem;color:{MUTED};margin-top:3px;letter-spacing:.6px'>5 DAGAR · "
            f"{d['rel_vol']:.1f}x VOL</div>"
            f"<span class='pill' style='background:{c}22;color:{c};border:1px solid {c}55;"
            f"margin-top:9px;display:inline-block;font-size:.6rem'>{d['label']}</span>"
            f"</div>", unsafe_allow_html=True)

        if col.button("Detaljer", key=f"tw_{tk}", use_container_width=True):
            st.session_state["detail_req"] = tk
            st.session_state.pop("detail_shown", None)   # tillåt att samma öppnas igen
            st.rerun()

    st.divider()


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 2  ·  Anropa den överst på förstasidan.
#  Lägg in DIREKT efter index-pillsen (app.py ~691), FÖRE "tabs = st.tabs(...)".
# ─────────────────────────────────────────────────────────────────────
#   ... index-pills st.markdown(...) ...
#
#   render_top_week(selected)        # <-- LÄGG TILL DENNA RAD HÄR
#
#   tabs = st.tabs(["HETA NU", ...])
