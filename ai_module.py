# ============================================================
#  AI ANALYS  —  drop-in module for MoneyGrab
#  AI-assistent som svarar på aktierelaterade frågor.
#  Kan ta emot kontext från SÖK-fliken (vald aktie + score).
# ============================================================
#
#  SÅ HÄR KOPPLAR DU IN DEN:
#
#  1) Lägg "ANALYS" i din tabs-rad:
#       tabs = st.tabs([..., "SÖK", "ANALYS", ...])
#
#  2) Importera överst i app.py:
#       from ai_module import render_ai_tab
#
#  3) Rendera fliken:
#       with tabs[5]:
#           render_ai_tab()
#
#  ---- VIKTIGT OM API-NYCKEL ----
#  I den riktiga appen anropas Anthropics API via din backend.
#  För Streamlit-testet: lägg din nyckel i .streamlit/secrets.toml:
#       ANTHROPIC_API_KEY = "sk-ant-..."
#  Filen committas ALDRIG till GitHub (lägg i .gitignore).
#  På Streamlit Cloud: klistra in den under App → Settings → Secrets.
#
#  Kräver: anthropic  (lägg "anthropic" i requirements.txt)
# ============================================================

import streamlit as st

# ----------------------------------------------------------
#  SYSTEM PROMPT — håller AI:n på rätt sida om juridiken.
#  Den UTBILDAR och ANALYSERAR, men ger inga köprekommendationer.
# ----------------------------------------------------------
SYSTEM_PROMPT = """Du är Grabit, AI-analytikern i appen MoneyGrab, ett verktyg för teknisk \
aktieanalys. Du svarar på svenska, koncist och pedagogiskt.

DINA REGLER (viktiga):
- Du förklarar tekniska begrepp, marknadsmekanik, bolag och sektorer.
- Du analyserar data som visas i appen (RSI, EMA, momentum, setup, ranking).
- Du ger ALDRIG köp- eller säljrekommendationer. Du säger aldrig "köp X" eller \
"sälj X" som ett råd. Du beskriver vad data visar och vad det BRUKAR betyda.
- Du påminner kort om att detta är teknisk information, inte finansiell rådgivning, \
när någon ber om ett konkret köpbeslut.
- Du varnar tydligt för risk: överköpta lägen, paraboliska rörelser, tunn likviditet, \
utspädning, pump-mönster.
- Om någon frågar om en uppenbar bluff eller orealistiska avkastningslöften, är du \
skeptisk och förklarar varningssignalerna.
- Du hittar inte på siffror. Om du inte vet ett aktuellt pris eller en aktuell siffra, \
säger du det istället för att gissa.

TON: rak, kunnig, lite av en erfaren trader-kompis. Inte hype. Inte "to the moon"."""


def _client():
    """Skapa Anthropic-klient från secrets. Returnerar None om nyckel saknas."""
    try:
        import anthropic
    except ImportError:
        st.error("Paketet `anthropic` saknas. Lägg till `anthropic` i requirements.txt.")
        return None
    key = st.secrets.get("ANTHROPIC_API_KEY", None)
    if not key:
        st.warning("Ingen API-nyckel hittad. Lägg `ANTHROPIC_API_KEY` i appens Secrets "
                   "(Streamlit Cloud → Settings → Secrets) för att aktivera AI:n.")
        return None
    return anthropic.Anthropic(api_key=key)


def _context_block():
    """Bygg en kontextsträng från SÖK-fliken om en aktie analyserats."""
    ctx = st.session_state.get("sok_context")
    if not ctx:
        return ""
    base = (
        f"\n\n[KONTEXT FRÅN APPEN — användaren tittar just nu på denna aktie:]\n"
        f"Ticker: {ctx.get('ticker')}\n"
        f"Bedömning: {ctx.get('label')}  |  Ranking: {ctx.get('score10')}/10\n"
        f"Senaste pris: {ctx.get('last'):.2f}\n"
        f"RSI: {ctx.get('rsi'):.0f}  |  Från 52v-topp: {ctx.get('pct_from_high'):+.1f}%  "
        f"|  20d avkastn: {ctx.get('ret_20'):+.1f}%  |  Rel.volym: {ctx.get('rel_vol'):.2f}x\n"
        f"Delpoäng — Styrka {ctx.get('strength')}/40, Momentum {ctx.get('momentum'):.0f}/35, "
        f"Setup {ctx.get('setup')}/25\n"
    )
    # Levels/struktur om de finns (RayAlgo-replika)
    if ctx.get("levels_note"):
        sup = ctx.get("ob_support")
        res = ctx.get("ob_resist")
        base += (
            f"NIVÅER & STRUKTUR — BOS: {ctx.get('bos')}  |  Struktur: {ctx.get('structure')}  "
            f"|  Setup-betyg: {ctx.get('setup_grade')} ({ctx.get('setup_score')}/100)\n"
            f"Stöd (OB): {sup if sup is not None else '—'}  |  Motstånd (OB): {res if res is not None else '—'}\n"
            f"ATR-mål: +1 {ctx.get('atr_up_1')} / +0.5 {ctx.get('atr_up_05')} | "
            f"risk −0.5 {ctx.get('atr_dn_05')} / −1 {ctx.get('atr_dn_1')}\n"
        )
    base += "[Använd denna kontext när du svarar, men bara om frågan rör den.]"
    return base


def _skill_card(col, icon, title, desc, key):
    """Renderar ett Barebone-stil skill-kort som är klickbart.
    Returnerar True om klickat. Kortets HTML läggs ovanpå en transparent knapp."""
    clicked = col.button(f"{title}", key=key, use_container_width=True)
    col.markdown(
        f"<div class='skill-card' style='margin-top:-58px;pointer-events:none;position:relative'>"
        f"<div class='skill-ico'>{icon}</div>"
        f"<div class='skill-title'>{title}</div>"
        f"<div class='skill-desc'>{desc}</div></div>",
        unsafe_allow_html=True)
    return clicked


def render_ai_tab():
    # ---- HERO ----
    st.markdown("<div class='hero-title'>Var vill du börja?</div>", unsafe_allow_html=True)

    ctx = st.session_state.get("sok_context")
    if ctx:
        st.markdown(
            f"<div class='hero-sub'>Kopplad till <b style='color:#9fd2ff'>{ctx.get('ticker')}</b> "
            f"· {ctx.get('label')} · {ctx.get('score10')}/10</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='hero-sub'>Din AI-analytiker. Sök en aktie i SÖK först "
                    "så svarar Grabit i kontext.</div>", unsafe_allow_html=True)

    # ---- SKILL-KORT ----
    skills = [
        ("⚡", "Förklara setupen", "Vad säger struktur, BOS och nivåerna"),
        ("📊", "Vad visar datan?", "RSI, EMA, momentum och ranking i klartext"),
        ("⚠️", "Största risken nu", "Överköpt, parabol, tunn likviditet, utspädning"),
        ("🎯", "Nivåer att bevaka", "Stöd, motstånd och ATR-mål"),
    ]
    cols = st.columns(2)
    for i, (ico, title, desc) in enumerate(skills):
        if _skill_card(cols[i % 2], ico, title, desc, key=f"skill_{i}"):
            st.session_state.setdefault("ai_messages", [])
            st.session_state["ai_pending"] = title

    # chathistorik i session
    if "ai_messages" not in st.session_state:
        st.session_state["ai_messages"] = []

    # visa historik
    for msg in st.session_state["ai_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # input
    prompt = st.chat_input("Fråga Grabit vad som helst…")
    if "ai_pending" in st.session_state:
        prompt = st.session_state.pop("ai_pending")

    if not prompt:
        return

    st.session_state["ai_messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    client = _client()
    if client is None:
        return

    # bygg meddelanden + injicera kontext i sista user-meddelandet
    api_messages = [{"role": m["role"], "content": m["content"]}
                    for m in st.session_state["ai_messages"]]
    api_messages[-1]["content"] += _context_block()

    with st.chat_message("assistant"):
        placeholder = st.empty()
        full = ""
        try:
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=api_messages,
            ) as stream:
                for text in stream.text_stream:
                    full += text
                    placeholder.markdown(full + "▌")
            placeholder.markdown(full)
        except Exception as e:
            placeholder.error(f"Kunde inte nå AI:n: {e}")
            return

    st.session_state["ai_messages"].append({"role": "assistant", "content": full})
    st.caption("AI-analys, inte finansiell rådgivning. Kontrollera alltid fakta själv.")


# ============================================================
#  GRABIT INLINE — liten frågeruta för detaljvyn
# ============================================================
def render_grabit_inline(ticker: str):
    """Liten Grabit-ruta i detaljvyn i Barebone-stil. Skill-kort + eget fält.
    (st.chat_input funkar ej i @st.dialog → knappar + textfält i stället.)"""
    st.markdown(f"<div style='font-family:Space Grotesk,sans-serif;font-size:1.2rem;"
                f"font-weight:700;color:#fff;margin:6px 0 12px'>Fråga Grabit om {ticker}</div>",
                unsafe_allow_html=True)
    quick = [
        ("⚡", "Förklara setupen", "Struktur, BOS & nivåer"),
        ("📈", "Vad tror du?", "Vad datan pekar mot"),
        ("⚠️", "Största risken?", "Vad du bör akta dig för"),
    ]
    qcols = st.columns(len(quick))
    pending = None
    for i, (ico, title, desc) in enumerate(quick):
        if qcols[i].button(title, key=f"gq_{ticker}_{i}", use_container_width=True):
            pending = title
        qcols[i].markdown(
            f"<div class='skill-card' style='margin-top:-58px;pointer-events:none;"
            f"position:relative;padding:14px'>"
            f"<div class='skill-ico' style='width:34px;height:34px;font-size:1rem;margin-bottom:8px'>{ico}</div>"
            f"<div class='skill-title' style='font-size:.92rem'>{title}</div>"
            f"<div class='skill-desc' style='font-size:.74rem'>{desc}</div></div>",
            unsafe_allow_html=True)

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
