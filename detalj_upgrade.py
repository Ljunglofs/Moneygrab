# =====================================================================
#  MONEYGRAB — DETALJVY-UPPGRADE  (drop-in)
#  Logga · dagens % · landsflagga · bolagsbeskrivning · Grabit inline
#  Fyra block. Följ kommentarerna för var de ska in.
# =====================================================================


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 1  ·  company_info()  — lägg till EN rad i return-dicten (app.py ~202)
#  (Behövs för att hämta bolagets hemsida → logotyp.)
# ─────────────────────────────────────────────────────────────────────
#   ...
#   "summary":  info.get("longBusinessSummary") or "",
#   "country":  info.get("country") or "",
#   "website":  info.get("website") or "",        # <-- LÄGG TILL DENNA RAD
#   ...


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 2  ·  Hjälpfunktioner — klistra in nära dina andra helpers i app.py
#  (t.ex. precis efter logo_url() / get_short_name())
# ─────────────────────────────────────────────────────────────────────
COUNTRY_ISO = {
    "United States":"us","Sweden":"se","Canada":"ca","United Kingdom":"gb",
    "Germany":"de","France":"fr","Netherlands":"nl","Switzerland":"ch",
    "China":"cn","Taiwan":"tw","Japan":"jp","South Korea":"kr","Israel":"il",
    "Ireland":"ie","Norway":"no","Finland":"fi","Denmark":"dk","Australia":"au",
    "India":"in","Singapore":"sg","Hong Kong":"hk","Brazil":"br","Mexico":"mx",
    "Italy":"it","Spain":"es","Bermuda":"bm","Cayman Islands":"ky","Luxembourg":"lu",
}

def flag_html(country: str, h: int = 14) -> str:
    code = COUNTRY_ISO.get((country or "").strip())
    if not code:
        return ""
    return (f"<img src='https://flagcdn.com/h20/{code}.png' "
            f"style='height:{h}px;border-radius:2px;vertical-align:-2px;margin-left:7px' "
            f"alt='{country}'/>")

def company_logo_html(ticker: str, info: dict, size: int = 46) -> str:
    """Riktig logotyp via Clearbit (bolagets domän). Faller tillbaka på monogram."""
    color = THEME_COLOR.get(TICKER_THEME.get(ticker, ""), "1c2330")
    mono  = ("".join(ch for ch in ticker if ch.isalpha())[:2] or ticker[:2]).upper()
    web   = info.get("website", "") or ""
    domain = ""
    if web:
        domain = (web.replace("https://", "").replace("http://", "")
                     .replace("www.", "").strip("/").split("/")[0])
    img = ""
    if domain:
        img = (f"<img src='https://logo.clearbit.com/{domain}' "
               f"onerror=\"this.remove()\" "
               f"style='position:absolute;inset:0;width:100%;height:100%;object-fit:contain;"
               f"background:#0c1018;border-radius:13px'/>")
    return (f"<div style='position:relative;width:{size}px;height:{size}px;border-radius:13px;"
            f"background:#{color}22;border:1px solid #{color}55;display:flex;align-items:center;"
            f"justify-content:center;overflow:hidden;flex:none;"
            f"font-family:\"Space Grotesk\",sans-serif;font-weight:700;font-size:15px;"
            f"color:#{color}'>{mono}{img}</div>")

def short_blurb(text: str, max_len: int = 280) -> str:
    if not text:
        return ""
    s = " ".join(text.split())
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    dot = cut.rfind(". ")
    return cut[:dot + 1] if dot > 90 else cut.rstrip() + "…"


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 3  ·  Ersätt RUBRIK + 5D-block i show_detail()  (app.py ~551–585)
#  Allt mellan "# ---- RUBRIK ----" och slutet på 5D-blocket.
# ─────────────────────────────────────────────────────────────────────
    # ---- RUBRIK: logga + namn + flagga + live-kurs + dagens % ----
    name   = info.get("name", "")
    sector = info.get("sector", "") or ""
    flag   = flag_html(info.get("country", ""))

    _live = live_price(ticker)
    price_display = _live if _live else a["last"]
    price_label   = "Live" if _live else "Stängning"

    # dagens förändring (senaste dagsstapeln mot föregående)
    day_chg = None
    if df_full is not None:
        c = df_full["Close"].dropna()
        if len(c) >= 2 and c.iloc[-2]:
            day_chg = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100

    top = st.columns([2, 1, 1])
    title = name if (name and name.upper() != ticker.upper()) else ticker
    sub   = f"{ticker} · {sector}" if sector else ticker
    top[0].markdown(
        f"<div style='display:flex;align-items:center;gap:13px'>{company_logo_html(ticker, info)}"
        f"<div><div style='font-family:\"Space Grotesk\",sans-serif;font-weight:700;font-size:1.5rem;"
        f"line-height:1.1;color:#fff'>{title}</div>"
        f"<div style='font-size:.8rem;color:{MUTED};margin-top:2px'>{sub}{flag}</div></div></div>",
        unsafe_allow_html=True)
    top[0].markdown(
        f"<span class='pill' style='background:{a['color']}22;color:{a['color']};"
        f"border:1px solid {a['color']}55;margin-top:8px;display:inline-block'>{a['label']}</span>",
        unsafe_allow_html=True)
    # Pris med dagens % som färgad delta (st.metric grönt/rött automatiskt)
    top[1].metric(price_label, f"{price_display:.2f}",
                  f"{day_chg:+.2f}% idag" if day_chg is not None else None)
    top[2].metric("Lyftchans", f"{a['score10']}/10")

    # ---- KORT OM BOLAGET (vad dom gör) ----
    blurb = short_blurb(info.get("summary", ""))
    if blurb:
        st.markdown(
            f"<div style='font-size:.86rem;color:#b6c0cc;line-height:1.5;margin:10px 0 2px;"
            f"padding:11px 14px;background:rgba(19,25,34,.55);border:1px solid rgba(255,255,255,.05);"
            f"border-radius:12px'>{blurb}</div>", unsafe_allow_html=True)

    # ---- STOR 5D-RÖRELSE ----
    r5 = a["ret_5"]
    r5_color = POS if r5 >= 0 else NEG
    arrow = "▲" if r5 >= 0 else "▼"
    st.markdown(
        f"<div style='display:flex;gap:24px;align-items:baseline;margin:12px 0 4px;flex-wrap:wrap'>"
        f"<div><div style='font-size:12px;color:{MUTED};letter-spacing:1px'>SENASTE 5 DAGARNA</div>"
        f"<div style='font-size:40px;font-weight:800;line-height:1;color:{r5_color}'>"
        f"{arrow} {r5:+.1f}%</div></div>"
        f"<div style='align-self:flex-end'><span style='font-size:13px;color:{MUTED}'>20 dagar: </span>"
        f"<span style='font-size:18px;font-weight:700;color:{POS if a['ret_20']>=0 else NEG}'>"
        f"{a['ret_20']:+.1f}%</span></div>"
        f"<div style='align-self:flex-end'><span style='font-size:13px;color:{MUTED}'>Börsvärde: </span>"
        f"<span style='font-size:18px;font-weight:700;color:{TXT}'>"
        f"{_fmt_mcap(info.get('mcap'))} {info.get('currency','')}</span></div>"
        f"</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 4a  ·  Lägg till i ai_module.py  (ny funktion, längst ner i filen)
#  Återanvänder _client(), SYSTEM_PROMPT och _context_block() som redan finns.
# ─────────────────────────────────────────────────────────────────────
def render_grabit_inline(ticker: str):
    """Liten Grabit-ruta för detaljvyn. Frågar AI:n om just denna aktie."""
    st.markdown(f"#### Fråga Grabit om {ticker}")
    quick = ["Vad tror du om aktien?", "Förklara setupen", "Största risken just nu?"]
    qcols = st.columns(len(quick))
    pending = None
    for i, q in enumerate(quick):
        if qcols[i].button(q, key=f"gq_{ticker}_{i}", use_container_width=True):
            pending = q

    c_in, c_btn = st.columns([4, 1])
    custom = c_in.text_input("Egen fråga", key=f"gq_txt_{ticker}",
                             placeholder="…eller skriv en egen fråga",
                             label_visibility="collapsed")
    if c_btn.button("Fråga", key=f"gq_send_{ticker}", type="primary",
                    use_container_width=True) and custom.strip():
        pending = custom.strip()

    ans_key = f"grabit_ans_{ticker}"

    if pending:
        client = _client()
        if client is None:
            return
        msgs = [{"role": "user", "content": pending + _context_block()}]
        ph, full = st.empty(), ""
        try:
            with client.messages.stream(model="claude-sonnet-4-6", max_tokens=900,
                                        system=SYSTEM_PROMPT, messages=msgs) as stream:
                for t in stream.text_stream:
                    full += t
                    ph.markdown(full + "▌")
            ph.markdown(full)
        except Exception as e:
            st.error(f"Kunde inte nå AI:n: {e}")
            return
        st.session_state[ans_key] = {"q": pending, "a": full}
        st.caption("AI-analys, inte finansiell rådgivning. Kontrollera fakta själv.")
    elif st.session_state.get(ans_key):
        prev = st.session_state[ans_key]
        st.caption(f"Fråga: {prev['q']}")
        st.markdown(prev["a"])
        st.caption("AI-analys, inte finansiell rådgivning. Kontrollera fakta själv.")


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 4b  ·  Koppla in den i app.py
#  (i) ändra importraden (app.py rad 26):
#        from ai_module import render_ai_tab, render_grabit_inline
#
#  (ii) i slutet av show_detail(), ersätt de två sista raderna
#       (tradingview_chart + "Öppna Ask Grabit"-caption, app.py ~656–657):
# ─────────────────────────────────────────────────────────────────────
    tradingview_chart(guess_tv_symbol(ticker), height=360)
    st.divider()
    render_grabit_inline(ticker)
