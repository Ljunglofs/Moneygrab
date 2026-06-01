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
    return (
        f"\n\n[KONTEXT FRÅN APPEN — användaren tittar just nu på denna aktie:]\n"
        f"Ticker: {ctx.get('ticker')}\n"
        f"Bedömning: {ctx.get('label')}  |  Ranking: {ctx.get('score10')}/10\n"
        f"Senaste pris: {ctx.get('last'):.2f}\n"
        f"RSI: {ctx.get('rsi'):.0f}  |  Från 52v-topp: {ctx.get('pct_from_high'):+.1f}%  "
        f"|  20d avkastn: {ctx.get('ret_20'):+.1f}%  |  Rel.volym: {ctx.get('rel_vol'):.2f}x\n"
        f"Delpoäng — Styrka {ctx.get('strength')}/40, Momentum {ctx.get('momentum'):.0f}/35, "
        f"Setup {ctx.get('setup')}/25\n"
        f"[Använd denna kontext när du svarar, men bara om frågan rör den.]"
    )


def render_ai_tab():
    st.subheader("Ask Grabit — din AI-analytiker")

    ctx = st.session_state.get("sok_context")
    if ctx:
        st.caption(f"Kopplad till din senaste sökning: **{ctx.get('ticker')}** "
                   f"({ctx.get('label')}, {ctx.get('score10')}/10). "
                   f"Fråga t.ex. \"förklara setupen\" eller \"vad betyder RSI här?\"")
    else:
        st.caption("Ställ vilken aktierelaterad fråga som helst. Sök på en aktie i SÖK "
                   "först, så kan AI:n svara i kontext om just den.")

    # förslag på frågor
    st.markdown("**Exempelfrågor:**")
    examples = [
        "Vad betyder 'SOON TO FLY' egentligen?",
        "Förklara skillnaden mellan EMA50 och EMA200",
        "Vad är en tight konsolidering och varför spelar den roll?",
        "Hur ska jag tänka kring överköpt RSI?",
    ]
    cols = st.columns(2)
    for i, ex in enumerate(examples):
        if cols[i % 2].button(ex, key=f"ex_{i}", use_container_width=True):
            st.session_state.setdefault("ai_messages", [])
            st.session_state["ai_pending"] = ex

    # chathistorik i session
    if "ai_messages" not in st.session_state:
        st.session_state["ai_messages"] = []

    # visa historik
    for msg in st.session_state["ai_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # input
    prompt = st.chat_input("Fråga Grabit…")
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
