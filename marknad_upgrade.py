# =====================================================================
#  MONEYGRAB — BREDDA UNIVERSUM  (drop-in upgrade)
#  Två block. Ersätt motsvarande block i app.py. Inga andra ändringar.
# =====================================================================


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 1  ·  Ersätt market_movers()  (rad ~246–267 i app.py)
#  Nytt: tar EN källa (str, bakåtkompatibelt) ELLER FLERA (tuple/list),
#        slår ihop och deduplicerar. Cap 35/källa, 200 totalt.
# ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def market_movers(source_key):
    if yf is None:
        return []
    keys = [source_key] if isinstance(source_key, str) else list(source_key)
    seen, out = set(), []
    for key in keys:
        res = None
        try:
            res = yf.screen(key)
        except Exception:
            try:
                from yfinance import Screener
                s = Screener()
                s.set_predefined_body(key)
                res = s.response
            except Exception:
                res = None
        quotes = res.get("quotes", []) if isinstance(res, dict) else []
        n_key = 0
        for q in quotes:
            sym = q.get("symbol", "") if isinstance(q, dict) else ""
            if not sym or any(ch in sym for ch in (".", "-", "=", "^")):
                continue
            sym = sym.upper()
            if sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
            n_key += 1
            if n_key >= 35:          # tak per källa
                break
    return out[:200]                 # tak totalt (skyddar free-tier)


# ─────────────────────────────────────────────────────────────────────
#  BLOCK 2  ·  Ersätt hela "with tabs[4]:"-blocket (MARKNAD, rad ~785–816)
#  Nytt: flera källor på en gång, dedupe mot ditt universum (bara NYA
#        namn), samt riktiga screening-rattar (min poäng, min RVOL).
# ─────────────────────────────────────────────────────────────────────
with tabs[4]:
    st.subheader("Marknad — bredda universum")
    SOURCES = {
        "Dagens vinnare":          "day_gainers",
        "Mest omsatta":            "most_actives",
        "Small-cap raketer":       "small_cap_gainers",
        "Aggressiva small-caps":   "aggressive_small_caps",
        "Tillväxt-tech":           "growth_technology_stocks",
        "Undervärderad tillväxt":  "undervalued_growth_stocks",
        "Mest blankade":           "most_shorted_stocks",
        "Dagens förlorare":        "day_losers",
    }
    c1, c2 = st.columns([3, 2])
    src_sel = c1.multiselect(
        "Källor", list(SOURCES.keys()),
        default=["Dagens vinnare", "Mest omsatta", "Small-cap raketer", "Tillväxt-tech"],
        key="mkt_src")
    only_new = c2.checkbox("Bara nya namn (ej i mitt universum)", value=True, key="mkt_new")

    f1, f2, f3 = st.columns(3)
    min_score  = f1.slider("Min poäng", 0, 10, 6, key="mkt_minscore")
    min_rvol   = f2.slider("Min RVOL", 0.0, 3.0, 1.2, 0.1, key="mkt_rvol")
    only_entry = f3.checkbox("Bara entry-lägen", value=True, key="mkt_entry")

    if yf is None:
        st.error("yfinance saknas — kan inte scanna marknaden.")
    elif not src_sel:
        st.info("Välj minst en källa ovan.")
    else:
        keys = tuple(SOURCES[s] for s in src_sel)   # tuple = hashbar för cache
        syms = market_movers(keys)
        if only_new:
            known = set(TICKER_THEME.keys())
            syms = [s for s in syms if s not in known]
        if not syms:
            st.warning("Inga symboler från valda källor just nu. Prova fler källor eller tryck refresh.")
        else:
            r = []
            prog = st.progress(0.0)
            for i, sym in enumerate(syms):
                a = scan(sym)
                prog.progress((i + 1) / len(syms))
                if a:
                    r.append(a)
            prog.empty()

            # ── Screening-filter ──
            r = [a for a in r
                 if a["score10"] >= min_score
                 and a.get("rel_vol", 0) >= min_rvol]
            if only_entry:
                r = [a for a in r if a["label"] in {"MOMENTUM", "BULL", "Rocketcase", "VÄNDNING"}]

            st.caption(f"{len(syms)} skannade · {len(r)} träffar efter filter. Klicka en rad för detaljer.")
            render_stats(r)
            render_grid(r, "market")
