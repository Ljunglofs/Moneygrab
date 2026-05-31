# ============================================================
#  DAGENS BULL  —  drop-in module for MoneyGrab
#  Skannar en bred lista (~100 likvida aktier), räknar en
#  HETTA-poäng (volym × ranking) och lyfter dagens hetaste i
#  ett stort kort. Flaggar misstänkta pump-mönster.
# ============================================================
#
#  INKOPPLING:
#    from dagens_bull import render_dagens_bull, HETTA_UNIVERSE
#    # överst på HETA NU-fliken, FÖRE din vanliga tabell:
#    render_dagens_bull()
#
#  Kräver: yfinance, pandas, numpy (du har dem).
#
#  OBS DATA: Yahoo orkar ~100 tickers per körning. Resultatet
#  cachas 10 min. "Hela marknaden" + microcap-skanning kommer
#  i backend-steget för den riktiga appen — det går inte att
#  skanna tusentals tickers live i Streamlit.
# ============================================================

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# ----------------------------------------------------------
#  UNIVERSUM — bred lista likvida US-aktier över sektorer.
#  (Inte microcaps — de skannar vi i backend med riskfilter.)
# ----------------------------------------------------------
HETTA_UNIVERSE = [
    # Mega/AI-infra
    "NVDA","MSFT","AAPL","AMZN","GOOGL","META","AVGO","AMD","TSM","MU",
    "MRVL","ALAB","CRDO","SMCI","DELL","ANET","INTC","QCOM","TXN","ARM",
    # Halvledare/utrustning
    "ASML","AMAT","LRCX","KLAC","NXPI","ON","MCHP","SWKS","TER",
    # Mjukvara/moln
    "PLTR","SNOW","NET","CRWD","DDOG","PANW","ZS","MDB","NOW","ORCL","ADBE",
    # Quantum/foton/emerging
    "IONQ","QBTS","RGTI","LWLG","POET","COHR","LITE",
    # Defense/space
    "RKLB","ASTS","LMT","RTX","NOC","KTOS","AVAV","ONDS","LUNR",
    # Nuclear/energi
    "OKLO","SMR","NNE","CEG","VST","UEC","CCJ","LEU",
    # Rare earth/material
    "USAR","MP","TMC","ALB","FCX","HBM","SCCO",
    # EV/fysisk AI
    "TSLA","RIVN","OUST","LAZR","PONY",
    # Fintech/krypto-proxy
    "COIN","HOOD","SOFI","MSTR","MARA","RIOT","CLSK","DGXX",
    # Bio/hälsa-momentum
    "HIMS","TEM","RXRX","CRSP","VKTX",
    # Bredd/likvid övrigt
    "UBER","SHOP","ABNB","DASH","RBLX","SPOT","NFLX","BABA","NIO","SOUN",
    "AI","BBAI","PATH","U","AFRM","DKNG","CVNA","SES","JOBY","ACHR",
]


def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


# ----------------------------------------------------------
#  SKANNA UNIVERSUM  (batch-nedladdning, cache 10 min)
# ----------------------------------------------------------
@st.cache_data(ttl=600, show_spinner="Skannar marknaden…")
def scan_universe(tickers):
    rows = []
    # batch-nedladdning är snabbare och snällare mot Yahoo
    data = yf.download(tickers, period="6mo", interval="1d",
                       group_by="ticker", auto_adjust=False,
                       threads=True, progress=False)
    for t in tickers:
        try:
            df = data[t].dropna()
            if len(df) < 60:
                continue
            close = df["Close"]; vol = df["Volume"]
            last = close.iloc[-1]
            ema20 = _ema(close, 20).iloc[-1]
            ema50 = _ema(close, 50).iloc[-1]
            ema200 = _ema(close, 200).iloc[-1] if len(close) >= 200 else _ema(close, len(close)-1).iloc[-1]
            rsi = _rsi(close).iloc[-1]
            ret20 = (last/close.iloc[-21]-1)*100 if len(close) > 21 else 0
            ret5 = (last/close.iloc[-6]-1)*100 if len(close) > 6 else 0
            hi52 = close.tail(252).max()
            pfh = (last/hi52-1)*100
            avgvol = vol.tail(20).mean()
            relvol = vol.iloc[-1]/avgvol if avgvol else 1
            tight = close.tail(10).std()/close.tail(10).mean()*100 if len(close) >= 10 else 99
            dollar_vol = last * avgvol     # likviditetsmått

            # ----- ranking 0-100 (samma logik som SÖK, komprimerad) -----
            s = 0
            s += 10 if last > ema20 else 0
            s += 8 if last > ema50 else 0
            s += 12 if last > ema200 else 0
            s += 6 if ema20 > ema50 else 0
            s += 4 if ema50 > ema200 else 0
            m = np.interp(ret20, [-15,0,30],[0,8,18]) + np.interp(ret5,[-10,0,15],[0,4,9])
            if 50 <= rsi <= 70: m += 8
            elif 40 <= rsi < 50: m += 4
            elif rsi > 78: m -= 4
            su = 0
            if -8 <= pfh <= 0: su += 10
            elif -15 <= pfh < -8: su += 5
            if tight < 3: su += 7
            elif tight < 5: su += 4
            if relvol >= 1.5: su += 5
            elif relvol >= 1.1: su += 3
            total = max(0, min(100, s + max(0,min(m,35)) + min(su,25)))
            score10 = max(1, min(10, round(total/10)))

            # ----- läge -----
            if last < ema50 and last < ema200 and ret20 < -5:
                lage = "BEAR"
            elif (-8 <= pfh <= 1) and tight < 5 and rsi < 72 and last > ema50:
                lage = "ROCKETCASE"
            elif last > ema50 and last > ema200 and m > 18:
                lage = "BULL"
            elif last > ema50:
                lage = "BYGGER"
            else:
                lage = "SVAG"

            # ----- HETTA-poäng: volym × ranking (0-100) -----
            # relvol komprimeras (log) så en 10x-spik inte helt dominerar
            vol_factor = np.clip(np.log1p(relvol) / np.log1p(4), 0, 1.3)  # ~1.0 vid 3x
            hetta = round(total * (0.55 + 0.45 * vol_factor), 1)
            # bara positiva lägen kan vara "Dagens Bull"
            if lage in ("BEAR", "SVAG"):
                hetta *= 0.4

            # ----- PUMP-RISK-flagga -----
            pump_flags = []
            if dollar_vol < 5_000_000:
                pump_flags.append("låg likviditet")
            if relvol >= 4:
                pump_flags.append("extrem volymspik")
            if ret5 > 40:
                pump_flags.append("parabolisk (+40% 5d)")
            if rsi > 82:
                pump_flags.append("kraftigt överköpt")
            pump_risk = len(pump_flags) >= 2   # två varningstecken = flagga

            rows.append({
                "Ticker": t, "Läge": lage, "Pris": last,
                "Ranking": score10, "Hetta": hetta,
                "RelVol": round(relvol, 2), "Ret5": round(ret5, 1),
                "Ret20": round(ret20, 1), "RSI": round(rsi),
                "FrånTopp": round(pfh, 1),
                "PumpRisk": pump_risk, "PumpVarför": ", ".join(pump_flags),
            })
        except Exception:
            continue
    return pd.DataFrame(rows)


# ----------------------------------------------------------
#  STORT "DAGENS BULL"-KORT
# ----------------------------------------------------------
def _big_card(r):
    risk = r["PumpRisk"]
    accent = "#ff9f1c" if risk else "#1F6FFF"
    glow = "rgba(255,159,28,.5)" if risk else "rgba(31,111,255,.55)"
    lage_color = {"ROCKETCASE": "#27baff", "BULL": "#22d37a",
                  "BYGGER": "#ffd23f"}.get(r["Läge"], "#9fb0bd")

    risk_banner = ""
    if risk:
        risk_banner = (
            f"<div style='margin-top:14px;padding:10px 14px;border-radius:10px;"
            f"background:rgba(255,159,28,.12);border:1px solid rgba(255,159,28,.45);"
            f"color:#ffb84d;font-size:13px;font-weight:600'>"
            f"RISKFLAGGA — {r['PumpVarför']}. Mönstret kan vara en pump. "
            f"Hög volym betyder inte hög kvalitet. Var extra försiktig.</div>"
        )

    st.markdown(
        f"""
        <div style="position:relative;border-radius:22px;padding:26px;margin:6px 0 22px;
            background:radial-gradient(130% 130% at 100% 0%, {glow.replace('.5','.18').replace('.55','.18')}, transparent 55%), #11141d;
            border:1px solid {accent}55; box-shadow:0 20px 60px -25px {glow};">
          <div style="font-size:13px;letter-spacing:3px;color:#8a98a5;font-weight:700">
            <span style="display:inline-block;width:7px;height:7px;border-radius:50%;
            margin-right:8px;vertical-align:middle;background:{accent};
            box-shadow:0 0 8px {accent}"></span>DAGENS BULL</div>
          <div style="display:flex;align-items:flex-end;justify-content:space-between;margin-top:10px">
            <div>
              <div style="font-size:44px;font-weight:800;line-height:1;letter-spacing:.5px">{r['Ticker']}</div>
              <div style="margin-top:8px;display:inline-block;padding:5px 12px;border-radius:999px;
                  background:{lage_color}22;border:1px solid {lage_color}66;color:{lage_color};
                  font-weight:700;font-size:13px;letter-spacing:.5px">{r['Läge']}</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:12px;color:#8a98a5;letter-spacing:1px">HETTA</div>
              <div style="font-size:46px;font-weight:800;line-height:1;color:{accent}">{r['Hetta']:.0f}</div>
            </div>
          </div>
          <div style="display:flex;gap:22px;margin-top:18px;flex-wrap:wrap">
            <div><div style="font-size:11px;color:#8a98a5">PRIS</div>
                 <div style="font-size:17px;font-weight:700">{r['Pris']:.2f}</div></div>
            <div><div style="font-size:11px;color:#8a98a5">REL. VOLYM</div>
                 <div style="font-size:17px;font-weight:700;color:{accent}">{r['RelVol']:.1f}×</div></div>
            <div><div style="font-size:11px;color:#8a98a5">5 DAGAR</div>
                 <div style="font-size:17px;font-weight:700;color:{'#22d37a' if r['Ret5']>=0 else '#ff4d63'}">{r['Ret5']:+.1f}%</div></div>
            <div><div style="font-size:11px;color:#8a98a5">RANKING</div>
                 <div style="font-size:17px;font-weight:700">{r['Ranking']}/10</div></div>
            <div><div style="font-size:11px;color:#8a98a5">RSI</div>
                 <div style="font-size:17px;font-weight:700">{r['RSI']}</div></div>
          </div>
          {risk_banner}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------
#  HUVUDRENDER
# ----------------------------------------------------------
def render_dagens_bull(universe=None):
    universe = universe or HETTA_UNIVERSE
    df = scan_universe(tuple(universe))   # tuple för cache-hashning

    if df is None or df.empty:
        st.warning("Kunde inte hämta marknadsdata just nu. Försök igen om en stund.")
        return

    df = df.sort_values("Hetta", ascending=False).reset_index(drop=True)

    # Dagens Bull = högsta hetta som INTE är pumpflaggad (helst).
    clean = df[~df["PumpRisk"]]
    top = (clean.iloc[0] if not clean.empty else df.iloc[0])
    _big_card(top)

    # info-rad
    st.caption(f"Skannade {len(df)} aktier · uppdateras var 10:e minut · "
               f"data ~15 min fördröjd. Hetta = ranking viktat med relativ volym.")

    # ---- topp 10 hetta-tabell ----
    st.markdown("##### Hetast just nu")
    show = df.head(10).copy()
    show["Pump"] = np.where(show["PumpRisk"], "RISK", "")
    show = show[["Ticker","Läge","Hetta","RelVol","Ret5","Ranking","RSI","Pump"]]
    show.columns = ["Ticker","Läge","Hetta","Rel.vol","5d %","Rank","RSI","Flagga"]
    st.dataframe(show, hide_index=True, use_container_width=True)

    st.caption("Riskflagga = mönster som liknar pump (låg likviditet, extrem volym, "
               "parabolisk rörelse, kraftigt överköpt). Teknisk signalering, inte "
               "finansiell rådgivning.")
