# ============================================================
#  🚀 ROCKETCASE VECKAN  —  MoneyGrab (EGET BRUK)
#  Skannar USA + Sverige + Frankrike/Europa, räknar veckobaserad
#  Rocketcase-poäng och plockar ut topp 15 kandidater som laddar
#  för utbrott. Två flikar: Sverige / Resten av världen (US-fokus).
#
#  Data: Yahoo Finance (yfinance) — gratis, lagligt för PRIVAT bruk.
#  INTE för kommersiell produkt (då gäller Polygon).
# ============================================================
#
#  INKOPPLING:
#    from rocketcase_veckan import render_rocketcase
#    with tab_rocketcase:
#        render_rocketcase()
#
#  Kräver: yfinance, pandas, numpy
# ============================================================

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# ----------------------------------------------------------
#  UNIVERSUM PER REGION
#  USA = bredast (fokus). Sverige = .ST. Frankrike = .PA.
#  Lägg gärna till egna tickers — det är bara listor.
# ----------------------------------------------------------
US = [
    # Mega/large (referens + likviditet)
    "NVDA","MSFT","AAPL","AMZN","GOOGL","META","AVGO","AMD","TSM","MU","MRVL",
    "ALAB","CRDO","SMCI","ANET","ARM","QCOM","ASML","AMAT","LRCX","KLAC","ON",
    "PLTR","SNOW","NET","CRWD","DDOG","PANW","ZS","MDB","NOW","ORCL","ADBE",
    # Fintech/momentum
    "COIN","HOOD","SOFI","MSTR","AFRM","UPST","LMND","DKNG","CVNA",
    # Quantum/foton (småbolag som rör sig hårt)
    "IONQ","QBTS","RGTI","LWLG","POET","COHR","LITE","QUBT","ARQQ","LASR","AAOI",
    # Defense/drönare/space (många small caps)
    "RKLB","ASTS","KTOS","AVAV","ONDS","LUNR","RDW","KULR","UAVS","ACHR","JOBY",
    # Nuclear/energi-momentum
    "OKLO","SMR","NNE","CEG","VST","UEC","CCJ","LEU","DNN","UUUU","NXE",
    # Rare earth/material microcaps
    "USAR","MP","TMC","ALB","FCX","HBM","SCCO","NB","REEMF","ARRT",
    # AI/data/mjukvara small caps
    "SOUN","AI","BBAI","PATH","U","TEM","RXRX","INOD","GITS","SERV","RR","LAES",
    # EV/fysisk AI/lidar
    "TSLA","RIVN","OUST","LAZR","NIO","MVST","QS","CHPT","EVGO",
    # Krypto-miners (extremt rörliga)
    "MARA","RIOT","CLSK","DGXX","BTDR","WULF","CIFR","HUT","IREN","APLD",
    # Bio/hälsa momentum
    "HIMS","CRSP","VKTX","SAVA","ANVS","CRDF","BNGO",
    # Bredd/övrigt rörligt
    "UBER","SHOP","ABNB","RBLX","SPOT","NFLX","SES","LICY","GEV",
    # Serenity-picks (small/microcap momentum) som saknades
    "AEHR","ENVX","WATT","HGRAF","VLN","FLY","VPG","PLAB","TRT","ASPI",
    "ADUR","MITK","ENAFF",
]

# Kanadensiska microcaps (TSX Venture .V / TSX .TO) — där case som
# Enablence lever. Yahoo har en del; testet nedan rensar bort de som saknas.
CA = [
    "ENA.V","POET.TO","DRX.TO","WELL.TO","HUT.TO","GLXY.TO","CBP.V",
    "NXE.TO","FCU.TO","DML.TO","PMET.TO","LITH.V","SCY.TO","NANO.V",
    "BHS.V","QNC.V","DBG.V","KRN.V","GENM.V","FOBI.V",
]

SE = [
    # Large/mid (likviditet + referens)
    "SIVE.ST","SAAB-B.ST","VOLV-B.ST","ERIC-B.ST","HM-B.ST","EVO.ST","ATCO-A.ST",
    "INVE-B.ST","ABB.ST","ASSA-B.ST","SAND.ST","SKF-B.ST","ALFA.ST","BOL.ST",
    "HEXA-B.ST","LIFCO-B.ST","NIBE-B.ST","EQT.ST","SINCH.ST","EMBRAC-B.ST",
    # Small/microcaps & First North (där det rör sig)
    "TOBII.ST","POLY.ST","CINT.ST","NOTE.ST","BICO.ST","CALTX.ST","HEART.ST",
    "MIPS.ST","TRUE-B.ST","STILL.ST","XBRANE.ST","CANTA.ST","IMMNOV.ST",
    "G5EN.ST","STAR-B.ST","DESK.ST","ARISE.ST","CRAD-B.ST","ENGCON-B.ST",
    "BEIA-B.ST","VITR.ST","FNOX.ST","NETI-B.ST","SVOL-B.ST",
    # Tillagda av användaren (First North-microcaps att bevaka)
    "SHT-B.ST","SMOL.ST","OBDU-B.ST","SUBGEN.ST",
]

FR_EU = [
    # Frankrike (.PA) — rör sig mycket
    "MC.PA","OR.PA","RMS.PA","AIR.PA","SAF.PA","STLAP.PA","DSY.PA","CAP.PA",
    "STMPA.PA","ALO.PA","UG.PA","VLA.PA","NANO.PA","ALVAS.PA","ALCLS.PA",
    "ALMDT.PA","ALDR.PA","ALGEV.PA","FDJ.PA","RNO.PA",
    # bredare Europa
    "ASML.AS","ADYEN.AS","SAP.DE","SIE.DE","RHM.DE","ARM.DE",
    # Serenity-picks (Europa/Australien) — .AX = Australien
    "LPK.DE","P4O.DE","AL2SI.DE","ALCJ.PA","EQR.AX","EOS.AX",
]

# Kanada-region (microcap-jakten)
REGIONS = {"US": US, "SE": SE, "FR_EU": FR_EU, "CA": CA}


def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


# ----------------------------------------------------------
#  VECKOBASERAD ROCKETCASE-POÄNG
#  Letar setups som LADDAR för utbrott på veckosikt:
#  - nära 52v-högsta (men inte över → inte redan flugit)
#  - tight konsolidering (sammandragen volatilitet)
#  - stigande/positiv trend (över EMA50)
#  - inte överköpt (RSI < 70)
#  - volym börjar komma in (rel.vol stödjer)
# ----------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner="Skannar marknaden…")
def scan_region(tickers, region):
    out = []
    data = yf.download(list(tickers), period="1y", interval="1d",
                       group_by="ticker", auto_adjust=False,
                       threads=True, progress=False)
    for t in tickers:
        try:
            df = data[t].dropna() if len(tickers) > 1 else data.dropna()
            if len(df) < 80:
                continue
            close = df["Close"]; vol = df["Volume"]
            last = close.iloc[-1]
            ema20 = _ema(close, 20).iloc[-1]
            ema50 = _ema(close, 50).iloc[-1]
            ema200 = _ema(close, 200).iloc[-1] if len(close) >= 200 else _ema(close, len(close)-1).iloc[-1]
            rsi = _rsi(close).iloc[-1]
            hi52 = close.tail(252).max()
            pfh = (last/hi52 - 1) * 100
            # veckorörelse (5 handelsdagar) och månad
            ret5 = (last/close.iloc[-6]-1)*100 if len(close) > 6 else 0
            ret20 = (last/close.iloc[-21]-1)*100 if len(close) > 21 else 0
            # konsolidering: hur tight de senaste 3 veckorna (15 dgr)
            tight = close.tail(15).std()/close.tail(15).mean()*100 if len(close) >= 15 else 99
            avgvol = vol.tail(20).mean()
            relvol = vol.tail(5).mean()/avgvol if avgvol else 1   # veckans snitt mot månadens

            # ----- ROCKETCASE-POÄNG (0-100), veckofokus -----
            score = 0
            # närhet till 52v-högsta (laddat, inte utflugit)
            if -10 <= pfh <= -1: score += 26
            elif -1 < pfh <= 1: score += 20
            elif -18 <= pfh < -10: score += 12
            # tight konsolidering = laddad fjäder
            if tight < 4: score += 22
            elif tight < 7: score += 13
            elif tight < 10: score += 6
            # trend-stöd
            if last > ema50: score += 14
            if ema20 > ema50: score += 8
            if last > ema200: score += 8
            # inte överköpt (vill fånga FÖRE, inte efter)
            if rsi < 60: score += 10
            elif rsi < 70: score += 5
            elif rsi > 78: score -= 8
            # volym börjar komma in
            if relvol >= 1.3: score += 8
            elif relvol >= 1.1: score += 4
            # straffa om redan rusat (då är caset borta)
            if ret5 > 25: score -= 12
            score = max(0, min(100, score))

            # läge (för etikett)
            if last < ema50 and last < ema200 and ret20 < -5:
                lage = "BEAR"
            elif (-10 <= pfh <= 1) and tight < 7 and rsi < 72 and last > ema50:
                lage = "ROCKETCASE"
            elif last > ema50 and last > ema200 and ret20 > 8:
                lage = "BULL"
            elif last > ema50:
                lage = "BYGGER"
            else:
                lage = "SVAG"

            # pump/risk-flagga (microcap-skydd)
            dollar_vol = last * avgvol
            flags = []
            if dollar_vol < 3_000_000: flags.append("låg likviditet")
            if relvol >= 3.5: flags.append("volymspik")
            if ret5 > 35: flags.append("redan parabolisk")
            if rsi > 82: flags.append("överköpt")
            risk = len(flags) >= 2

            out.append({
                "Ticker": t, "Region": region, "Läge": lage,
                "RC-poäng": round(score, 1), "Pris": round(last, 2),
                "FrånTopp%": round(pfh, 1), "Tight%": round(tight, 1),
                "RSI": round(rsi), "Vecka%": round(ret5, 1),
                "RelVol": round(relvol, 2), "Risk": risk,
                "RiskVarför": ", ".join(flags),
            })
        except Exception:
            continue
    return pd.DataFrame(out)


def _top15(df):
    """Bara äkta Rocketcase-lägen, sorterat på poäng, max 15."""
    rc = df[df["Läge"] == "ROCKETCASE"].copy()
    if rc.empty:                       # fallback: högsta poäng även om ej ren RC
        rc = df.copy()
    return rc.sort_values("RC-poäng", ascending=False).head(15).reset_index(drop=True)


def _render_table(df, title):
    st.markdown(f"##### {title}")
    if df.empty:
        st.info("Inga kandidater hittades i denna region just nu.")
        return
    show = df.copy()
    show["Flagga"] = np.where(show["Risk"], "⚠", "")
    show = show[["Ticker","Läge","RC-poäng","Pris","FrånTopp%","Tight%","RSI","Vecka%","RelVol","Flagga"]]
    show.columns = ["Ticker","Läge","Poäng","Pris","Från topp","Tight","RSI","Vecka","Rel.vol","Flagga"]
    st.dataframe(show, hide_index=True, use_container_width=True)


def render_rocketcase():
    st.subheader("🚀 Veckans Rocketcase")
    st.caption("Skannar USA + Sverige + Frankrike/Europa + Kanada (microcaps) efter setups "
               "som laddar för utbrott. Data ~15 min fördröjd (Yahoo). Eget bruk. "
               "Tickers som Yahoo inte känner igen hoppas tyst över.")

    # skanna alla regioner
    df_us = scan_region(tuple(US), "US")
    df_se = scan_region(tuple(SE), "SE")
    df_fr = scan_region(tuple(FR_EU), "FR_EU")
    df_ca = scan_region(tuple(CA), "CA")

    # ---- toppbanner: 15 bästa globalt (US-fokus via poäng) ----
    alldf = pd.concat([df_us, df_se, df_fr, df_ca], ignore_index=True)
    top15 = _top15(alldf)
    n_rc = (alldf["Läge"] == "ROCKETCASE").sum()
    st.markdown(
        f"<div style='padding:16px 20px;border-radius:16px;margin:4px 0 18px;"
        f"background:radial-gradient(130% 130% at 100% 0%, rgba(31,111,255,.15), transparent 55%), #11141d;"
        f"border:1px solid rgba(31,111,255,.3)'>"
        f"<span style='font-size:12px;letter-spacing:2px;color:#8a98a5;font-weight:700'>VECKANS 15</span><br>"
        f"<span style='font-size:15px;color:#cfd6e0'>{len(top15)} kandidater · "
        f"{n_rc} rena Rocketcase-lägen hittade · {len(alldf)} bolag skannade</span></div>",
        unsafe_allow_html=True)
    _render_table(top15, "Topp 15 — laddar för utbrott")

    st.divider()

    # ---- två flikar: Sverige / Resten av världen ----
    tab_world, tab_se = st.tabs(["🌍 Resten av världen (US-fokus)", "🇸🇪 Sverige"])
    with tab_world:
        world = pd.concat([df_us, df_fr, df_ca], ignore_index=True)
        _render_table(_top15(world), "USA + Frankrike/Europa + Kanada — topp Rocketcase")
        st.caption("Frankrike (.PA), Europa och kanadensiska microcaps (.V/.TO) ingår här. "
                   "USA väger tyngst i urvalet. Kanada-listan är där case som Enablence lever.")
    with tab_se:
        _render_table(_top15(df_se), "Svenska kandidater (.ST)")

    st.caption("⚠ = pump/risk-flagga (låg likviditet, volymspik, redan parabolisk, överköpt). "
               "Teknisk signalering för egen analys — inte finansiell rådgivning.")
