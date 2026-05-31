"""
MONEYGRAB — Streamlit Web App
==============================
Kör som en riktig app i webbläsaren / på mobilen.

Lokalt: streamlit run app.py
Cloud:  pusha till GitHub + anslut Streamlit Cloud
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="MoneyGrab",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# CUSTOM CSS — dark/yellow MoneyGrab branding
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500;600&display=swap');

.stApp { background: #0a0a0a; }
.main .block-container { padding-top: 1rem; padding-bottom: 2rem; max-width: 1400px; }

h1.moneygrab-title {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 56px;
    letter-spacing: 3px;
    color: #ffd60a;
    line-height: 1;
    margin: 0;
    padding-bottom: 8px;
    border-bottom: 3px solid #ffd60a;
}
h1.moneygrab-title span { color: #e8e8e8; }

.meta-row {
    color: #888;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    margin: 12px 0 24px 0;
    display: flex;
    gap: 24px;
    flex-wrap: wrap;
}
.meta-row b { color: #e8e8e8; }

.section-header {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 28px;
    letter-spacing: 1.5px;
    color: #ffd60a;
    margin: 20px 0 4px 0;
}
.section-subtitle { color: #888; font-size: 12px; margin-bottom: 12px; }

div[data-testid="stDataFrame"] {
    background: #141414;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
    padding: 8px;
}

.stButton>button {
    background: #ffd60a;
    color: #0a0a0a;
    font-family: 'Bebas Neue', sans-serif;
    font-size: 18px;
    letter-spacing: 1px;
    border: none;
    padding: 8px 24px;
}
.stButton>button:hover { background: #ffe347; color: #0a0a0a; }

[data-testid="stSidebar"] { background: #141414; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# UNIVERSE
# ============================================================
US_TICKERS = {
    'NVDA': 'AI infra', 'AMD': 'AI infra', 'AVGO': 'AI infra',
    'ALAB': 'AI infra', 'CRDO': 'AI infra', 'MRVL': 'AI infra',
    'ARM': 'AI infra', 'MU': 'AI infra', 'TSM': 'AI infra',
    'NBIS': 'AI infra', 'VRT': 'AI infra', 'CRWV': 'AI infra',
    'MSFT': 'AI software', 'GOOGL': 'AI software', 'META': 'AI software',
    'AMZN': 'AI software', 'ORCL': 'AI software', 'NOW': 'AI software',
    'PLTR': 'AI software', 'AI': 'AI software', 'BBAI': 'AI software',
    'OKLO': 'Nuclear', 'NNE': 'Nuclear', 'SMR': 'Nuclear',
    'BWXT': 'Nuclear', 'LEU': 'Nuclear', 'UEC': 'Nuclear',
    'CCJ': 'Nuclear', 'XE': 'Nuclear', 'ASPI': 'Nuclear',
    'COHR': 'Photonics', 'LITE': 'Photonics', 'AAOI': 'Photonics',
    'IPGP': 'Photonics', 'POET': 'Photonics', 'LWLG': 'Photonics',
    'RKLB': 'Space/Def', 'ASTS': 'Space/Def', 'ONDS': 'Space/Def',
    'AVAV': 'Space/Def', 'KTOS': 'Space/Def', 'RDW': 'Space/Def',
    'LUNR': 'Space/Def', 'ACHR': 'Space/Def',
    'USAR': 'Rare earth', 'MP': 'Rare earth', 'TMC': 'Rare earth',
    'IPX': 'Rare earth', 'REMX': 'Rare earth',
    'IONQ': 'Quantum', 'RGTI': 'Quantum', 'QUBT': 'Quantum', 'QBTS': 'Quantum',
    'OUST': 'Physical AI', 'INVZ': 'Physical AI', 'AEVA': 'Physical AI', 'MVIS': 'Physical AI',
    'RXRX': 'Biotech', 'CRSP': 'Biotech', 'NTLA': 'Biotech',
    'BEAM': 'Biotech', 'MRNA': 'Biotech', 'EXAS': 'Biotech',
    'TEM': 'Biotech AI', 'SDGR': 'Biotech AI', 'ABSI': 'Biotech AI',
    'SPY': 'BENCHMARK', 'QQQ': 'BENCHMARK',
}
SE_TICKERS = {
    'SIVE.ST': 'Photonics', 'SMOL.ST': 'Photonics', 'OBDU-B.ST': 'Photonics',
    'SAAB-B.ST': 'Defense', 'HEXA-B.ST': 'AI infra', 'NVZN.ST': 'AI software',
    'EVO.ST': 'Other', 'AZN.ST': 'Biotech', 'NIBE-B.ST': 'Other',
}
ALL_TICKERS = {**US_TICKERS, **SE_TICKERS}

# ============================================================
# INDICATORS & SCORING
# ============================================================
def calc_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def analyze_ticker(ticker, df, theme, spy_returns):
    if df is None or len(df) < 30: return None
    try:
        df = df.dropna()
        if len(df) < 30: return None
        close = df['Close']
        volume = df['Volume']
        last_price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        pct_today = (last_price / prev_close - 1) * 100
        pct_5d = (last_price / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else np.nan
        pct_20d = (last_price / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else np.nan
        ma20 = float(close.rolling(20).mean().iloc[-1])
        dist_ma20 = (last_price / ma20 - 1) * 100
        high_52w = float(close.tail(252).max())
        dist_52w_high = (last_price / high_52w - 1) * 100
        avg_vol_20d = float(volume.rolling(20).mean().iloc[-2])
        today_vol = float(volume.iloc[-1])
        vol_ratio = today_vol / avg_vol_20d if avg_vol_20d > 0 else 0
        rsi = float(calc_rsi(close).iloc[-1])
        dollar_vol = today_vol * last_price
        rs_vs_spy = pct_20d - spy_returns if (spy_returns is not None and not np.isnan(pct_20d)) else np.nan
        ranges = ((df['High'] - df['Low']) / df['Close']) * 100
        volatility = float(ranges.tail(14).mean())
        return {
            'ticker': ticker, 'theme': theme, 'price': last_price,
            'pct_today': pct_today, 'pct_5d': pct_5d, 'pct_20d': pct_20d,
            'dist_ma20': dist_ma20, 'dist_52w_high': dist_52w_high,
            'vol_ratio': vol_ratio, 'rsi': rsi,
            'dollar_vol_m': dollar_vol / 1e6,
            'rs_vs_spy': rs_vs_spy, 'volatility': volatility,
        }
    except Exception:
        return None

def score_hot_now(r):
    score = 0
    if 3 <= r['pct_today'] <= 15: score += 30
    elif 1.5 <= r['pct_today'] < 3: score += 15
    elif r['pct_today'] > 15: score += 5
    if 2 <= r['vol_ratio'] <= 8: score += 25
    elif r['vol_ratio'] > 8: score += 10
    elif 1.5 <= r['vol_ratio'] < 2: score += 10
    if 2 <= r['dist_ma20'] <= 15: score += 20
    elif 15 < r['dist_ma20'] <= 25: score += 5
    elif r['dist_ma20'] > 25: score -= 15
    if -20 <= r['dist_52w_high'] <= -3: score += 15
    elif r['dist_52w_high'] >= -3: score += 5
    if 50 <= r['rsi'] <= 70: score += 10
    elif r['rsi'] > 75: score -= 10
    return score

def score_building(r):
    score = 0
    if 5 <= r['pct_20d'] <= 25: score += 30
    elif 25 < r['pct_20d'] <= 50: score += 10
    if -10 <= r['dist_52w_high'] <= -2: score += 25
    elif -20 <= r['dist_52w_high'] < -10: score += 10
    if 0 <= r['dist_ma20'] <= 8: score += 20
    if r['volatility'] < 4: score += 15
    elif r['volatility'] < 7: score += 5
    if -2 <= r['pct_today'] <= 3: score += 10
    if r['rs_vs_spy'] and r['rs_vs_spy'] > 5: score += 10
    return score

def warning_flags(r):
    flags = []
    if r['dist_ma20'] > 25: flags.append('PARABOLISK')
    if r['rsi'] > 75: flags.append('ÖVERKÖPT')
    if r['vol_ratio'] > 10: flags.append('VOL-SPIKE')
    if r['volatility'] > 10: flags.append('HÖGRISK-VOL')
    if r['dist_52w_high'] < -50: flags.append('DOWNTREND')
    if r['dollar_vol_m'] < 1 and not r['ticker'].endswith('.ST'):
        flags.append('ILLIKVID')
    return flags

# ============================================================
# DATA (cached 30 min so app känns snabbt)
# ============================================================
@st.cache_data(ttl=1800)
def fetch_all_data():
    data = yf.download(list(ALL_TICKERS.keys()), period='3mo', interval='1d',
                       group_by='ticker', auto_adjust=True, progress=False, threads=True)
    spy_ret = None
    try:
        spy_close = data['SPY']['Close'].dropna()
        if len(spy_close) >= 21:
            spy_ret = (float(spy_close.iloc[-1]) / float(spy_close.iloc[-21]) - 1) * 100
    except Exception:
        pass
    results = []
    for ticker, theme in ALL_TICKERS.items():
        if theme == 'BENCHMARK': continue
        try:
            df = data[ticker]
        except KeyError:
            continue
        r = analyze_ticker(ticker, df, theme, spy_ret)
        if r:
            r['score_hot'] = score_hot_now(r)
            r['score_building'] = score_building(r)
            r['flags'] = ', '.join(warning_flags(r))
            results.append(r)
    return pd.DataFrame(results), spy_ret

# ============================================================
# UI
# ============================================================
st.markdown('<h1 class="moneygrab-title">MONEY<span>GRAB</span></h1>', unsafe_allow_html=True)

# Sidebar controls
with st.sidebar:
    st.markdown("### Inställningar")
    themes_selected = st.multiselect(
        "Teman",
        sorted(set(US_TICKERS.values()) | set(SE_TICKERS.values()) - {'BENCHMARK'}),
        default=None,
        placeholder="Alla teman"
    )
    min_score = st.slider("Min score (Heta nu)", 0, 100, 30)
    hide_warnings = st.checkbox("Dölj varningsflaggade", value=False)
    st.markdown("---")
    if st.button("🔄 Tvinga refresh av data"):
        st.cache_data.clear()
        st.rerun()

# Fetch
with st.spinner("Hämtar marknadsdata..."):
    df, spy_ret = fetch_all_data()

if df.empty:
    st.error("Kunde inte hämta data — yfinance blockerad eller helgen utan färska data.")
    st.stop()

# Filters
liquid = df[(df['dollar_vol_m'] > 0.5) | (df['ticker'].str.endswith('.ST'))].copy()
if themes_selected:
    liquid = liquid[liquid['theme'].isin(themes_selected)]
if hide_warnings:
    liquid = liquid[liquid['flags'] == '']

# Meta row
now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
spy_str = f"{spy_ret:+.2f}%" if spy_ret else "n/a"
st.markdown(f"""<div class="meta-row">
<span>GENERATED <b>{now_str}</b></span>
<span>SPY 20D <b>{spy_str}</b></span>
<span>UNIVERSE <b>{len(df)} TICKERS</b></span>
</div>""", unsafe_allow_html=True)

# Display function
def show_table(data, score_col, title, subtitle):
    st.markdown(f'<div class="section-header">{title}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="section-subtitle">{subtitle}</div>', unsafe_allow_html=True)
    
    show = data.copy()
    show = show.rename(columns={
        'ticker': 'Ticker', 'theme': 'Tema', 'price': 'Pris',
        'pct_today': 'Idag %', 'pct_5d': '5d %', 'pct_20d': '20d %',
        'vol_ratio': 'Vol', 'dist_ma20': 'vs MA20 %', 'dist_52w_high': 'vs 52vH %',
        'rsi': 'RSI', score_col: 'Score', 'flags': 'Flags'
    })
    cols = ['Ticker', 'Tema', 'Pris', 'Idag %', '5d %', '20d %',
            'Vol', 'vs MA20 %', 'vs 52vH %', 'RSI', 'Score', 'Flags']
    
    st.dataframe(
        show[cols],
        use_container_width=True,
        hide_index=True,
        height=min(580, 50 + 38 * len(show)),
        column_config={
            'Pris': st.column_config.NumberColumn(format="%.2f"),
            'Idag %': st.column_config.NumberColumn(format="%+.1f%%"),
            '5d %': st.column_config.NumberColumn(format="%+.1f%%"),
            '20d %': st.column_config.NumberColumn(format="%+.1f%%"),
            'Vol': st.column_config.NumberColumn(format="%.1fx"),
            'vs MA20 %': st.column_config.NumberColumn(format="%+.1f%%"),
            'vs 52vH %': st.column_config.NumberColumn(format="%+.1f%%"),
            'RSI': st.column_config.NumberColumn(format="%.0f"),
        }
    )

# Tabs
tab1, tab2, tab3 = st.tabs(["🔥 HETA NU", "📈 BYGGER UPP", "⚠️ VARNINGAR"])

with tab1:
    hot = liquid[liquid['score_hot'] >= min_score].sort_values('score_hot', ascending=False).head(20)
    show_table(hot, 'score_hot', '🔥 HETA NU',
               'Dag 1-3 av rörelse · volym-bekräftade · inte paraboliska. Bästa swing-entries.')

with tab2:
    bui = liquid.sort_values('score_building', ascending=False).head(20)
    show_table(bui, 'score_building', '📈 BYGGER UPP',
               'Konsoliderar nära 52v högsta · RS-leaders. Vänta på breakout.')

with tab3:
    warn = df[df['flags'] != ''].sort_values('pct_today', ascending=False).head(25)
    show_table(warn, 'score_hot', '⚠️ VARNINGAR',
               'Paraboliska toppar · överköpta · för låg likviditet. UNDVIK eller ta vinst.')

st.markdown("---")
st.caption("MONEYGRAB v1 · daglig batch · ej finansiell rådgivning · data: yfinance · cache 30 min")
