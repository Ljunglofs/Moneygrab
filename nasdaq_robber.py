"""
NASDAQ ROBBER  —  multi-timeframe daytrading signal bot
SPOT THE SETUP. IGNORE THE NOISE.

Genererar LONG/SHORT-larm för daytrading. Larmar — lägger INGA ordrar.
Datalagret är abstraherat: byt fetch_ohlcv() för att koppla in en riktig feed.
"""

import os
import time
import json
import math
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import AverageTrueRange


# ==================================================================
# CONFIG
# ==================================================================
class Config:
    # --- Universe ---
    TICKERS = ["NQ=F", "GC=F", "BTC-USD"]   # US100 (Nasdaq futures), Guld futures, Bitcoin – via yfinance (dygnet-runt-data)
    NAMES   = {"NQ=F": "US100", "GC=F": "Guld", "BTC-USD": "Bitcoin"}

    # --- Timeframes (Alpaca-format) ---
    HTF_TIMEFRAME     = "1Hour"   # high-timeframe bias
    HTF_LOOKBACK_DAYS = 60
    MTF_TIMEFRAME     = "15Min"   # setup/entry timeframe
    MTF_LOOKBACK_DAYS = 12

    # --- Alpaca data ---
    ALPACA_KEY      = os.environ.get("APCA_API_KEY_ID", "")
    ALPACA_SECRET   = os.environ.get("APCA_API_SECRET_KEY", "")
    ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks"
    # Feed väljs automatiskt efter klockslag (se current_feed):
    #   overnight-sessionen -> OVERNIGHT_FEED, annars DAY_FEED.
    DAY_FEED        = "iex"        # gratis dagtid/pre-market/ordinarie
    OVERNIGHT_FEED  = "overnight"  # gratis Basic; "boats" om Algo Trader Plus

    # --- Confluence thresholds ---
    MIN_SCORE      = int(os.environ.get("ROBBER_MIN_SCORE", "5"))  # av 7; sänk t.ex. till 4 via env för fler larm
    RSI_LONG_LOW, RSI_LONG_HIGH   = 50, 70
    RSI_SHORT_LOW, RSI_SHORT_HIGH = 30, 50
    MIN_REL_VOLUME = 1.2          # volym mot 20-snitt
    MIN_ATR_PCT    = 0.0008       # filtrera bort död chop (ATR/pris)

    # --- Risk ---
    ATR_STOP_MULT  = 1.3          # stop = swing-buffert eller ATR*mult (tightast vinner ej, säkrast)
    TARGETS_R      = [1.5, 2.5, 4.0]   # mål i R-multiplar
    ACCOUNT_SIZE   = 100_000      # SEK, för positionsstorleksförslag
    RISK_PCT       = 0.01         # 1% risk per trade

    # --- Confidence (0-100) ---  summerar till 100 över de TA-komponenter boten faktiskt mäter
    CONF_WEIGHTS = {                        # summa 100 -- ICT-baserad
        "trend": 22,   # HTF-bias + EMA-stack + momentum (partiellt)
        "sweep": 22,   # Liquidity sweep
        "mss":   22,   # Market Structure Shift / CHOCH
        "fvg":   17,   # Fair Value Gap (retestad)
        "ob":    11,   # Order Block (retestad)
        "volym":  6,   # Relativ volym
    }
    CONF_GREEN    = int(os.environ.get("CONF_GREEN",  "90"))   # gron A+ / godkand
    CONF_YELLOW   = int(os.environ.get("CONF_YELLOW", "80"))   # gul / bevaka
    CONF_MIN_SEND = int(os.environ.get("CONF_MIN_SEND","80"))  # under denna: tyst (men shadow-loggas)
    SHADOW_LOG    = os.environ.get("SHADOW_LOG", "robber_shadow.jsonl")

    # --- Auto-trade (cTrader) -- AVSTANGD som default. Kan inte lagga riktig order
    #     utan att BADE AUTO_TRADE=1 OCH AUTO_TRADE_LIVE=1 ar satta. ---
    AUTO_TRADE      = os.environ.get("AUTO_TRADE", "0") == "1"
    AUTO_TRADE_LIVE = os.environ.get("AUTO_TRADE_LIVE", "0") == "1"
    AUTO_TRADE_MIN  = int(os.environ.get("AUTO_TRADE_MIN", "90"))

    # --- Session (svensk tid, DST-säkert via zoneinfo) ---
    LOCAL_TZ          = "Europe/Stockholm"
    SESSION_START     = (6, 0)            # 06:00 svensk tid
    SESSION_END       = (22, 0)           # 22:00 svensk tid
    TRADE_DAYS        = {0, 1, 2, 3, 4}   # mån–fre

    # --- Loop ---
    BAR_MINUTES   = 15
    BUFFER_SEC    = 20            # vänta efter bar-stängning innan hämtning
    STATE_FILE    = "robber_state.json"

    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    CHAT_ID        = os.environ.get("CHAT_ID", "")


# ==================================================================
# DATA LAYER  — Alpaca (IEX free feed)
# ==================================================================
def current_feed() -> str:
    """
    Väljer Alpaca-feed efter aktuell New York-tid (DST-säkert).
    Overnight-sessionen är 20:00–04:00 ET -> OVERNIGHT_FEED.
    Övrig tid (pre-market/ordinarie/after-hours) -> DAY_FEED.
    """
    from zoneinfo import ZoneInfo
    et = datetime.now(ZoneInfo("America/New_York"))
    h = et.hour
    if h >= 20 or h < 4:
        return Config.OVERNIGHT_FEED
    return Config.DAY_FEED


def fetch_ohlcv(ticker: str, timeframe: str, lookback_days: int) -> pd.DataFrame:
    """
    Hamtar OHLCV-barer via yfinance. Futures (NQ=F, GC=F) handlas nastan
    dygnet runt -> data finns aven fore USA-oppning. timeframe i Alpaca-format
    mappas till yfinance-intervall. Returnerar tz-aware DataFrame.
    """
    import yfinance as yf
    iv_map = {"1Hour": "1h", "60Min": "1h", "30Min": "30m",
              "15Min": "15m", "5Min": "5m", "1Min": "1m"}
    interval = iv_map.get(timeframe, "15m")
    period = f"{max(1, int(lookback_days))}d"
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=False, threads=False)
    except Exception as e:
        raise RuntimeError(f"yfinance fel ({ticker} {interval}): {e}")

    if df is None or df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).title() for c in df.columns]
    want = ["Open", "High", "Low", "Close", "Volume"]
    keep = [c for c in want if c in df.columns]
    if len(keep) < 5:
        return pd.DataFrame(columns=want)
    df = df[keep].dropna()
    if getattr(df.index, "tz", None) is None:
        df.index = df.index.tz_localize("UTC")
    return df


# ==================================================================
# INDIKATORER
# ==================================================================
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"]  = EMAIndicator(df["Close"], 20).ema_indicator()
    df["ema50"]  = EMAIndicator(df["Close"], 50).ema_indicator()
    df["rsi"]    = RSIIndicator(df["Close"], 14).rsi()
    macd = MACD(df["Close"])
    df["macd"]      = macd.macd()
    df["macd_sig"]  = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()
    df["vol_avg"] = df["Volume"].rolling(20).mean()
    df["rel_vol"] = df["Volume"] / df["vol_avg"]
    df["atr"]     = AverageTrueRange(df["High"], df["Low"], df["Close"], 14).average_true_range()
    return df


def htf_bias(htf: pd.DataFrame) -> str:
    """LONG / SHORT / NEUTRAL baserat på 1h-struktur."""
    ema50  = EMAIndicator(htf["Close"], 50).ema_indicator()
    ema200 = EMAIndicator(htf["Close"], 200).ema_indicator()
    price = htf["Close"].iloc[-1]
    e50, e200 = ema50.iloc[-1], ema200.iloc[-1]
    if any(pd.isna(x) for x in (e50, e200)):
        return "NEUTRAL"
    if price > e200 and e50 > e200:
        return "LONG"
    if price < e200 and e50 < e200:
        return "SHORT"
    return "NEUTRAL"


# ==================================================================
# CONFLUENCE-MOTOR
# ==================================================================
def score_long(cur, prev, bias) -> tuple[int, list]:
    s, reasons = 0, []
    if bias == "LONG":
        s += 1; reasons.append("HTF-bias LONG")
    if cur.Close > cur.ema20 > cur.ema50:
        s += 1; reasons.append("Pris > EMA20 > EMA50")
    if Config.RSI_LONG_LOW < cur.rsi < Config.RSI_LONG_HIGH:
        s += 1; reasons.append(f"RSI momentum ({cur.rsi:.0f})")
    if cur.macd_hist > 0 and cur.macd_hist > prev.macd_hist:
        s += 1; reasons.append("MACD-hist stigande > 0")
    if cur.rel_vol >= Config.MIN_REL_VOLUME:
        s += 1; reasons.append(f"Relativ volym {cur.rel_vol:.1f}x")
    # pullback-reclaim: förra baren nära/under ema20, nu tillbaka över
    if prev.Low <= prev.ema20 and cur.Close > cur.ema20:
        s += 1; reasons.append("Pullback + reclaim av EMA20")
    # högre lågpunkt (enkel struktur)
    if cur.Low > prev.Low:
        s += 1; reasons.append("Högre lågpunkt")
    return s, reasons


def score_short(cur, prev, bias) -> tuple[int, list]:
    s, reasons = 0, []
    if bias == "SHORT":
        s += 1; reasons.append("HTF-bias SHORT")
    if cur.Close < cur.ema20 < cur.ema50:
        s += 1; reasons.append("Pris < EMA20 < EMA50")
    if Config.RSI_SHORT_LOW < cur.rsi < Config.RSI_SHORT_HIGH:
        s += 1; reasons.append(f"RSI momentum ({cur.rsi:.0f})")
    if cur.macd_hist < 0 and cur.macd_hist < prev.macd_hist:
        s += 1; reasons.append("MACD-hist fallande < 0")
    if cur.rel_vol >= Config.MIN_REL_VOLUME:
        s += 1; reasons.append(f"Relativ volym {cur.rel_vol:.1f}x")
    if prev.High >= prev.ema20 and cur.Close < cur.ema20:
        s += 1; reasons.append("Studs + reject av EMA20")
    if cur.High < prev.High:
        s += 1; reasons.append("Lägre högpunkt")
    return s, reasons


def build_signal(ticker, df, bias):
    cur, prev = df.iloc[-1], df.iloc[-2]
    price = round(float(cur.Close), 2)
    atr   = float(cur.atr)
    atr_pct = atr / price if price else 0

    if atr_pct < Config.MIN_ATR_PCT:
        return None  # för död marknad

    long_s, long_r   = score_long(cur, prev, bias)
    short_s, short_r = score_short(cur, prev, bias)

    side = None
    if long_s >= Config.MIN_SCORE and long_s >= short_s:
        side, score, reasons = "LONG", long_s, long_r
    elif short_s >= Config.MIN_SCORE and short_s > long_s:
        side, score, reasons = "SHORT", short_s, short_r
    else:
        return None

    # Risk: stop = swing-extrem (senaste 5 barer) buffrad med 0.25*ATR,
    # men aldrig längre bort än ATR*mult.
    lookback = df.iloc[-6:-1]
    if side == "LONG":
        swing = float(lookback.Low.min()) - 0.25 * atr
        atr_stop = price - atr * Config.ATR_STOP_MULT
        stop = round(max(swing, atr_stop), 2)
        risk = price - stop
        targets = [round(price + risk * r, 2) for r in Config.TARGETS_R]
    else:
        swing = float(lookback.High.max()) + 0.25 * atr
        atr_stop = price + atr * Config.ATR_STOP_MULT
        stop = round(min(swing, atr_stop), 2)
        risk = stop - price
        targets = [round(price - risk * r, 2) for r in Config.TARGETS_R]

    if risk <= 0:
        return None

    shares = math.floor((Config.ACCOUNT_SIZE * Config.RISK_PCT) / risk)

    return {
        "ticker": ticker, "side": side, "score": score, "max_score": 7,
        "price": price, "atr": round(atr, 2), "stop": stop,
        "risk_per_share": round(risk, 2), "targets": targets,
        "shares": shares, "bias": bias, "reasons": reasons,
        "bar_time": df.index[-1].isoformat(),
    }


# ==================================================================
# CONFIDENCE 0-100  (additivt -- ror inte score_long/score_short)
# ==================================================================
def _swings(H, L, k=2):
    """Fraktal-swingar: k barer pa varje sida."""
    sh, sl = [], []
    n = len(H)
    for i in range(k, n - k):
        if H[i] > max(H[i-k:i]) and H[i] >= max(H[i+1:i+k+1]):
            sh.append(i)
        if L[i] < min(L[i-k:i]) and L[i] <= min(L[i+1:i+k+1]):
            sl.append(i)
    return sh, sl


def detect_sweep(df, side, lookback=20):
    """Liquidity sweep: wick bortom tidigare extrem som sedan stanger tillbaka (rejection)."""
    H = df["High"].to_numpy(); L = df["Low"].to_numpy(); C = df["Close"].to_numpy()
    n = len(C)
    if n < 6:
        return False, None
    a = max(0, n - lookback - 2); b = n - 2
    if b <= a:
        return False, None
    if side == "LONG":
        prior_low = float(L[a:b].min())
        for i in (n - 2, n - 1):
            if L[i] < prior_low and C[i] > prior_low:
                return True, round(prior_low, 2)
    else:
        prior_high = float(H[a:b].max())
        for i in (n - 2, n - 1):
            if H[i] > prior_high and C[i] < prior_high:
                return True, round(prior_high, 2)
    return False, None


def detect_mss(df, side, k=2):
    """Market Structure Shift: stang bortom senaste bekraftade motsatta swing."""
    H = df["High"].to_numpy(); L = df["Low"].to_numpy(); C = df["Close"].to_numpy()
    n = len(C)
    sh, sl = _swings(H, L, k)
    cur = float(C[-1])
    if side == "LONG":
        prior = [i for i in sh if i <= n - 1 - k]
        if not prior:
            return False, None
        lvl = float(H[prior[-1]])
        return bool(cur > lvl), round(lvl, 2)
    else:
        prior = [i for i in sl if i <= n - 1 - k]
        if not prior:
            return False, None
        lvl = float(L[prior[-1]])
        return bool(cur < lvl), round(lvl, 2)


def detect_fvg(df, side, lookback=20):
    """Fair Value Gap (3-candle imbalance) som priset retestar pa sista baren."""
    H = df["High"].to_numpy(); L = df["Low"].to_numpy(); C = df["Close"].to_numpy()
    n = len(C)
    lo = max(2, n - lookback)
    for k in range(n - 2, lo - 1, -1):
        if side == "LONG" and H[k-2] < L[k]:
            gb, gt = float(H[k-2]), float(L[k])
            retest = (L[-1] <= gt) and (C[-1] >= gb)
            return bool(retest), (round(gb, 2), round(gt, 2))
        if side == "SHORT" and L[k-2] > H[k]:
            gt, gb = float(L[k-2]), float(H[k])
            retest = (H[-1] >= gb) and (C[-1] <= gt)
            return bool(retest), (round(gb, 2), round(gt, 2))
    return False, None


def detect_ob(df, side, lookback=20, impulse_atr=0.8):
    """Order Block: sista motsatt-fargade candle fore en impuls i ratt riktning, retestad."""
    O = df["Open"].to_numpy(); H = df["High"].to_numpy()
    L = df["Low"].to_numpy(); C = df["Close"].to_numpy()
    n = len(C)
    atr = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0
    if atr <= 0:
        atr = float(np.nanmean(H[-20:] - L[-20:])) or 1.0
    for k in range(n - 3, max(0, n - lookback) - 1, -1):
        if k + 2 > n - 1:
            continue
        if side == "LONG":
            if C[k] < O[k] and C[k+1] > H[k] and (C[k+2] - C[k]) > impulse_atr * atr:
                ob_bot = float(min(O[k], C[k], L[k])); ob_top = float(max(O[k], C[k]))
                if L[-1] <= ob_top:
                    return True, (round(ob_bot, 2), round(ob_top, 2))
        else:
            if C[k] > O[k] and C[k+1] < L[k] and (C[k] - C[k+2]) > impulse_atr * atr:
                ob_top = float(max(O[k], C[k], H[k])); ob_bot = float(min(O[k], C[k]))
                if H[-1] >= ob_bot:
                    return True, (round(ob_bot, 2), round(ob_top, 2))
    return False, None


def compute_confidence(df, bias, side):
    """0-100 ur trend + ICT-detektorer + volym. Ror inte score_long/short."""
    cur, prev = df.iloc[-1], df.iloc[-2]
    if side == "LONG":
        htf = (bias == "LONG")
        ema = bool(cur.Close > cur.ema20 > cur.ema50)
        mom = bool((cur.macd_hist > 0 and cur.macd_hist > prev.macd_hist)
                   or (Config.RSI_LONG_LOW < cur.rsi < Config.RSI_LONG_HIGH))
    else:
        htf = (bias == "SHORT")
        ema = bool(cur.Close < cur.ema20 < cur.ema50)
        mom = bool((cur.macd_hist < 0 and cur.macd_hist < prev.macd_hist)
                   or (Config.RSI_SHORT_LOW < cur.rsi < Config.RSI_SHORT_HIGH))
    trend_frac = (int(htf) + int(ema) + int(mom)) / 3.0
    vol = bool(cur.rel_vol >= Config.MIN_REL_VOLUME)
    sweep, sweep_lvl = detect_sweep(df, side)
    mss,   mss_lvl   = detect_mss(df, side)
    fvg,   fvg_zone  = detect_fvg(df, side)
    ob,    ob_zone   = detect_ob(df, side)
    W = Config.CONF_WEIGHTS
    conf = (W["trend"] * trend_frac
            + (W["sweep"] if sweep else 0)
            + (W["mss"]   if mss   else 0)
            + (W["fvg"]   if fvg   else 0)
            + (W["ob"]    if ob    else 0)
            + (W["volym"] if vol   else 0))
    conf = max(0, min(100, int(round(conf))))
    groups = {
        "Trend": trend_frac >= 0.5, "Liquidity Sweep": bool(sweep), "MSS": bool(mss),
        "FVG": bool(fvg), "Order Block": bool(ob), "Volym": vol,
    }
    comps = {
        "trend_frac": round(trend_frac, 2), "htf": htf, "ema": ema, "mom": mom,
        "sweep": bool(sweep), "sweep_lvl": sweep_lvl, "mss": bool(mss), "mss_lvl": mss_lvl,
        "fvg": bool(fvg), "fvg_zone": fvg_zone, "ob": bool(ob), "ob_zone": ob_zone, "volym": vol,
    }
    return conf, comps, groups


def _shadow_log(sig, sent):
    """Loggar varje kandidat-setup (aven de under trosklen) for senare validering."""
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ticker": sig["ticker"], "side": sig["side"],
            "score7": sig.get("score"), "confidence": sig.get("confidence"),
            "entry": sig["price"], "sl": sig["stop"], "targets": sig["targets"],
            "rr": Config.TARGETS_R, "bias": sig["bias"],
            "components": sig.get("components"), "bar_time": sig.get("bar_time"),
            "sent": bool(sent),
        }
        with open(Config.SHADOW_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        print("shadow-log fel:", e)


def maybe_autotrade(sig):
    """cTrader-exekvering -- AVSTANGD som default.
    Lagger ALDRIG en riktig order utan bade AUTO_TRADE=1 och AUTO_TRADE_LIVE=1."""
    if not Config.AUTO_TRADE:
        return
    if sig.get("confidence", 0) < Config.AUTO_TRADE_MIN:
        return
    intent = (f"[auto] {sig['side']} {sig['ticker']} @ {sig['price']} "
              f"SL {sig['stop']} TP {sig['targets']} (conf {sig.get('confidence')})")
    if not Config.AUTO_TRADE_LIVE:
        print(intent, "-- LIVE av, ingen order lagd (torrkorning)")
        return
    # === LIVE cTrader-exekvering laggs har (kraver creds + uttestning) ===
    # Avsiktligt ej implementerad: ingen oprovad bot ska kunna fyra skarpt.
    print(intent, "-- AUTO_TRADE_LIVE=1 men exekvering ej aktiverad i kod (sakerhetssparr)")


# ==================================================================
# LARM / STATE
# ==================================================================
def send_telegram(text):
    if not Config.TELEGRAM_TOKEN or not Config.CHAT_ID:
        miss = []
        if not Config.TELEGRAM_TOKEN: miss.append("TELEGRAM_TOKEN")
        if not Config.CHAT_ID: miss.append("CHAT_ID")
        print(f"[telegram ej konfigurerad – saknar {', '.join(miss)} i miljön]\n" + text)
        return False
    import requests
    url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": Config.CHAT_ID, "text": text,
                                     "parse_mode": "HTML"}, timeout=10)
        if r.status_code != 200:
            print(f"Telegram-API {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print("Telegram-fel:", e)
        return False


def _tier(conf):
    if conf >= Config.CONF_GREEN:  return "\U0001F7E2", "A+ GODKÄND"
    if conf >= Config.CONF_YELLOW: return "\U0001F7E1", "BEVAKA"
    return "\U0001F534", "SVAG"


def format_alert(sig):
    check, cross, wait = "\u2705", "\u274C", "\u23F3"
    side_txt = "LONG" if sig["side"] == "LONG" else "SHORT"
    name = Config.NAMES.get(sig["ticker"], sig["ticker"])
    conf = sig.get("confidence")
    if conf is None:
        conf = int(round(sig["score"] / max(1, sig.get("max_score", 7)) * 100))
    dot, tier = _tier(conf)
    bias_txt = ("Bullish " + check) if sig["bias"] == "LONG" else \
               (("Bearish " + check) if sig["bias"] == "SHORT" else "Neutral")
    groups = sig.get("groups") or {}
    _it = list(groups.items())
    core_lines = ["   ".join(f"{k}: {check if v else cross}" for k, v in _it[i:i+3])
                  for i in range(0, len(_it), 3)]
    tgs = sig["targets"]; rr = Config.TARGETS_R
    tplines = "   ".join(f"TP{i+1}: {t}" for i, t in enumerate(tgs))
    rrtxt = " / ".join(f"1:{r:g}" for r in rr)
    lines = [
        f"{dot} <b>{side_txt} \u2013 {name}</b>  ({sig['ticker']})",
        f"\U0001F525 Confidence: <b>{conf}/100</b>  \u00b7  {tier}",
        f"HTF Bias: {bias_txt}",
    ]
    lines += core_lines
    lines += [
        "\u2500\u2500\u2500\u2500\u2500",
        f"Entry: <b>{sig['price']}</b>",
        f"SL: {sig['stop']}   (risk {sig['risk_per_share']}/enhet)",
        tplines,
        f"Risk/Reward: {rrtxt}",
    ]
    return "\n".join(lines)

def load_state():
    try:
        with open(Config.STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(Config.STATE_FILE, "w") as f:
        json.dump(state, f)


def is_fresh(sig, state):
    """En signal per (ticker, side, bar) — ingen spam."""
    key = f"{sig['ticker']}:{sig['side']}"
    return state.get(key) != sig["bar_time"]


# ==================================================================
# SESSION
# ==================================================================
def in_session(now=None):
    from zoneinfo import ZoneInfo
    local = now or datetime.now(ZoneInfo(Config.LOCAL_TZ))
    if local.weekday() not in Config.TRADE_DAYS:
        return False
    start = local.replace(hour=Config.SESSION_START[0],
                          minute=Config.SESSION_START[1], second=0, microsecond=0)
    end = local.replace(hour=Config.SESSION_END[0],
                        minute=Config.SESSION_END[1], second=0, microsecond=0)
    return start <= local <= end


def seconds_to_next_bar():
    now = datetime.now(timezone.utc)
    m = (now.minute // Config.BAR_MINUTES + 1) * Config.BAR_MINUTES
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=m)
    return max(1, (nxt - now).total_seconds()) + Config.BUFFER_SEC


# ==================================================================
# EN SKANNING
# ==================================================================
STATUS = {
    "started": None, "last_scan": None, "last_feed": None,
    "in_session": None, "fired_total": 0, "fired_last": 0, "tickers": {},
}


def scan_once():
    from zoneinfo import ZoneInfo
    state = load_state()
    fired = 0
    feed = "yfinance"
    print(f"[scan] kalla={feed}")
    STATUS["last_feed"] = feed
    results = {}
    for ticker in Config.TICKERS:
        try:
            htf = fetch_ohlcv(ticker, Config.HTF_TIMEFRAME, Config.HTF_LOOKBACK_DAYS)
            mtf = fetch_ohlcv(ticker, Config.MTF_TIMEFRAME, Config.MTF_LOOKBACK_DAYS)
            if len(htf) < 200 or len(mtf) < 50:
                results[ticker] = f"för lite data (htf={len(htf)}, mtf={len(mtf)})"
                print(f"{ticker}: {results[ticker]}"); continue

            # färsk bar?
            last = mtf.index[-1]
            age = (pd.Timestamp.now(tz=last.tz) - last).total_seconds() / 60
            if age > Config.BAR_MINUTES * 3:
                results[ticker] = f"stale ({age:.0f} min) – marknad stängd?"
                print(f"{ticker}: {results[ticker]}"); continue

            mtf = add_indicators(mtf)
            bias = htf_bias(htf)
            sig = build_signal(ticker, mtf, bias)
            if sig:
                conf, comps, groups = compute_confidence(mtf, bias, sig["side"])
                sig["confidence"] = conf; sig["groups"] = groups; sig["components"] = comps

            if sig and is_fresh(sig, state):
                send_ok = sig["confidence"] >= Config.CONF_MIN_SEND
                _shadow_log(sig, sent=send_ok)
                state[f"{sig['ticker']}:{sig['side']}"] = sig["bar_time"]
                if send_ok:
                    msg = format_alert(sig)
                    ctx = poly_context(sig["ticker"])
                    if ctx:
                        msg = msg + "\n\n" + ctx
                    print(msg, "\n")
                    send_telegram(msg)
                    fired += 1
                    results[ticker] = f"LARM {sig['side']} {sig['confidence']}/100"
                    maybe_autotrade(sig)
                else:
                    results[ticker] = f"under tröskel ({sig['confidence']}/100) -- shadow-loggad"
                    print(f"{ticker}: {results[ticker]}")
            else:
                why = "ingen setup" if not sig else "redan larmat denna bar"
                results[ticker] = f"{why} (bias {bias})"
                print(f"{ticker}: {results[ticker]}")
        except Exception as e:
            results[ticker] = f"fel: {e}"
            print(f"{ticker} fel: {e}")
    save_state(state)
    STATUS["last_scan"] = datetime.now(ZoneInfo(Config.LOCAL_TZ)).isoformat(timespec="seconds")
    STATUS["fired_last"] = fired
    STATUS["fired_total"] += fired
    STATUS["tickers"] = results
    return fired


# ==================================================================
# LOOP — exakt på bar-stängning
# ==================================================================
POLY_MIN_USD = float(os.environ.get("POLY_MIN_USD", "20000"))
# Ämnesfilter: bara marknader vars fraga matchar dina teman larmar (sport/politik filtreras bort).
# Justerbart via env POLY_KEYWORDS (kommaseparerat).
import re as _re

POLY_KEYWORDS = [k.strip().lower() for k in os.environ.get(
    "POLY_KEYWORDS",
    "nasdaq,ndx,s&p,s&p 500,sp 500,dow jones,russell,stock,stocks,equity,equities,"
    "nvidia,tesla,apple,microsoft,"
    "fed,federal reserve,fomc,powell,interest rate,rate cut,rate hike,cpi,inflation,recession,gdp,unemployment,jobs report,"
    "bitcoin,btc,ethereum,ether,crypto,solana,coinbase,microstrategy,"
    "gold,xau,silver,oil,crude,wti,brent"
).split(",") if k.strip()]
_POLY_SEEN = set()
_POLY_PRIMED = False


def _kw_match(text, kws):
    """Ordgräns-matchning sa korta ord (dow, btc, oil) inte trillar in i
    andra ord (down, btca, spoil)."""
    t = (text or "").lower()
    for k in kws:
        if _re.search(r"\b" + _re.escape(k) + r"\b", t):
            return True
    return False


# Blocklista: sport/politik/nojen filtreras bort aven om ett nyckelord rakar matcha
# (t.ex. "gold medal", "Senegal vs Iraq", "Will France win on ...").
POLY_BLOCK = [
    " vs.", " vs ", " v ", "medal", "olympic", "world cup", "champions league",
    "premier league", "la liga", "serie a", "bundesliga", "super bowl", "playoff",
    "grand prix", "formula 1", " nba ", " nfl ", " ufc ", "boxing", "cricket",
    "election", "president", "senate", "governor", "prime minister", "parliament",
    "referendum", "mayor", "win on ", "to win the", "tournament", "championship",
    "grammy", "oscar", "box office", "rotten tomatoes", "time person",
]


def poly_relevant(title):
    """True bara for aktier/index/ravaror/crypto. Sport & politik blockas forst."""
    t = (title or "").lower()
    if any(b in t for b in POLY_BLOCK):
        return False
    return _kw_match(title, POLY_KEYWORDS)


def poly_scan():
    """Pollar Polymarkets Data API efter stora taker-trades och larmar pa nya
    SOM matchar dina teman (POLY_KEYWORDS). Forsta korningen primar bara seen-set."""
    global _POLY_PRIMED
    import requests
    try:
        r = requests.get(
            "https://data-api.polymarket.com/trades",
            params={"takerOnly": "true", "filterType": "CASH",
                    "filterAmount": POLY_MIN_USD, "limit": 40},
            headers={"User-Agent": "grabit/1.0"}, timeout=12,
        )
        r.raise_for_status()
        rows = r.json() or []
    except Exception as e:
        print("poly_scan fel:", e)
        return 0

    fired = 0
    for t in rows:
        tx = t.get("transactionHash") or (str(t.get("proxyWallet")) + str(t.get("timestamp")))
        if tx in _POLY_SEEN:
            continue
        _POLY_SEEN.add(tx)
        if not _POLY_PRIMED:
            continue  # seeda bara forsta varvet, larma inte
        title = t.get("title") or ""
        if not poly_relevant(title):
            continue  # fel amne (sport/politik osv) -> hoppa
        try:
            size = float(t.get("size") or 0)
            price = float(t.get("price") or 0)
            usd = size * price
        except Exception:
            continue
        if usd < POLY_MIN_USD:
            continue
        side = t.get("side") or "BUY"
        who = t.get("name") or t.get("pseudonym") or (str(t.get("proxyWallet") or "")[:8])
        msg = ("\U0001F40B <b>Polymarket \u2013 stor trade</b>\n"
               f"{side} {t.get('outcome','')} \u00b7 ${usd:,.0f}\n"
               f"{title}\n"
               f"Trader: {who}")
        if send_telegram(msg):
            fired += 1

    _POLY_PRIMED = True
    if len(_POLY_SEEN) > 4000:
        _POLY_SEEN.clear()
        _POLY_PRIMED = False  # re-seeda utan att spamma
    STATUS["poly_fired_total"] = STATUS.get("poly_fired_total", 0) + fired
    STATUS["poly_last"] = len(rows)
    return fired


_POLY_INSTR = {
    "NQ=F": ["nasdaq", "ndx", "s&p", "s&p 500", "stock market", "dow jones"],
    "GC=F": ["gold", "xau"],
    "BTC-USD": ["bitcoin", "btc", "microstrategy"],
}
_GAMMA_CACHE = {"t": 0.0, "rows": []}


def _gamma_markets():
    """Cachead lista over aktiva Polymarket-marknader (5 min)."""
    import time, requests
    now = time.time()
    if _GAMMA_CACHE["rows"] and now - _GAMMA_CACHE["t"] < 300:
        return _GAMMA_CACHE["rows"]
    try:
        r = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"closed": "false", "active": "true",
                    "order": "volume24hr", "ascending": "false", "limit": 120},
            headers={"User-Agent": "grabit/1.0"}, timeout=10,
        )
        r.raise_for_status()
        rows = r.json() or []
    except Exception as e:
        print("gamma fel:", e)
        return _GAMMA_CACHE["rows"]
    _GAMMA_CACHE["t"] = now
    _GAMMA_CACHE["rows"] = rows
    return rows


def poly_context(ticker):
    """Polymarkets crowd-odds for instrumentets tema -> confluence i signalen.
    Returnerar en kort textblock med de mest relevanta marknaderna + 24h-riktning."""
    import json as _json
    kws = _POLY_INSTR.get(ticker)
    if not kws:
        return ""
    hits = []
    for m in _gamma_markets():
        q = m.get("question") or ""
        ql = q.lower()
        if any(b in ql for b in POLY_BLOCK):
            continue
        if not _kw_match(q, kws):
            continue
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = _json.loads(prices or "[]")
            except Exception:
                prices = []
        if not prices:
            continue
        try:
            yes = float(prices[0])
        except Exception:
            continue
        chg = m.get("oneDayPriceChange")
        try:
            chg = float(chg) if chg is not None else None
        except Exception:
            chg = None
        hits.append((q, yes, chg))
        if len(hits) >= 3:
            break
    if not hits:
        return ""
    lines = ["\U0001F4CA <b>Polymarket-l\u00e4ge</b>"]
    for q, yes, chg in hits:
        if chg is None:
            arrow = ""
        elif chg > 0.005:
            arrow = " \u2191"
        elif chg < -0.005:
            arrow = " \u2193"
        else:
            arrow = " \u2192"
        short = q if len(q) <= 58 else q[:57] + "\u2026"
        lines.append(f"\u2022 {short}: {round(yes * 100)}%{arrow}")
    return "\n".join(lines)


def run_loop():
    print("=== NASDAQ ROBBER startad ===")
    if not Config.ALPACA_KEY or not Config.ALPACA_SECRET:
        print("VARNING: ALPACA_KEY/ALPACA_SECRET saknas i miljön.")
    print(f"Tickers: {Config.TICKERS}  |  {Config.MTF_TIMEFRAME} setup / {Config.HTF_TIMEFRAME} bias  |  data=yfinance (futures)")
    names = ", ".join(Config.NAMES.get(t, t) for t in Config.TICKERS)
    ok = send_telegram(
        "\u2705 <b>NASDAQ ROBBER startad</b>\n"
        f"Bevakar: {names}\n"
        f"Setup {Config.MTF_TIMEFRAME} / bias {Config.HTF_TIMEFRAME} \u00b7 larm vid \u2265{Config.MIN_SCORE}/7\n"
        "Data: yfinance (futures, dygnet runt) \u00b7 session 06:00\u201322:00 m\u00e5n\u2013fre\n"
        f"+ Polymarket-monitor: larm vid trades \u2265 ${int(POLY_MIN_USD):,}"
    )
    print(f"Startup-ping till Telegram: {'OK' if ok else 'MISSLYCKADES (kolla TOKEN/CHAT_ID)'}")
    from zoneinfo import ZoneInfo
    STATUS["started"] = datetime.now(ZoneInfo(Config.LOCAL_TZ)).isoformat(timespec="seconds")
    while True:
        try:
            sess = in_session()
            STATUS["in_session"] = sess
            if sess:
                scan_once()
            else:
                print("Utanför session — vilar.")
            poly_scan()  # Polymarket 24/7, oberoende av aktie-session
        except Exception as e:
            # En miss i en cykel får ALDRIG döda tråden / värd-appen.
            print(f"Robber loop-fel (hoppar över cykel): {e}")
        sleep = seconds_to_next_bar()
        print(f"Sover {sleep:.0f}s till nästa bar...\n")
        time.sleep(sleep)


def start_in_background():
    """
    Startar roboten i en daemon-tråd. Datakälla är yfinance — inga API-nycklar
    krävs. Kraschar tråden påverkas inte värd-appen.
    """
    import threading
    try:
        import yfinance  # noqa: F401
    except Exception as e:
        print(f"Robber: yfinance saknas, startar inte: {e}")
        return None
    t = threading.Thread(target=run_loop, daemon=True, name="nasdaq-robber")
    t.start()
    print("Robber: bakgrundstråd startad (yfinance-data).")
    return t


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        sample = {
            "ticker": "QQQ", "side": "LONG", "score": 6, "max_score": 7,
            "price": 512.34, "atr": 1.85, "stop": 509.10, "risk_per_share": 3.24,
            "targets": [517.20, 520.44, 525.30], "shares": 30, "bias": "LONG",
            "reasons": ["HTF-bias LONG", "Pris > EMA20 > EMA50", "RSI momentum (58)"],
            "bar_time": "test",
        }
        ok = send_telegram("\U0001F9EA <b>TESTSIGNAL</b> (manuell)\n\n" + format_alert(sample))
        print("Testsignal skickad:", ok)
    elif "--once" in sys.argv:
        scan_once()
    else:
        run_loop()
