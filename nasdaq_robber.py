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
    TICKERS = ["QQQ", "GLD"]      # QQQ = Nasdaq 100, GLD = guld. (Bitcoin kräver Alpacas krypto-endpoint – separat.)

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
    Hämtar OHLCV-barer från Alpaca. Returnerar DataFrame med
    Open/High/Low/Close/Volume och tz-aware index (UTC).
    Hanterar paginering. Feed väljs automatiskt (dag vs overnight).
    """
    import requests

    feed = current_feed()
    headers = {
        "APCA-API-KEY-ID": Config.ALPACA_KEY,
        "APCA-API-SECRET-KEY": Config.ALPACA_SECRET,
    }
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{Config.ALPACA_DATA_URL}/{ticker}/bars"
    params = {
        "timeframe": timeframe,
        "start": start,
        "limit": 10000,
        "feed": feed,
        "adjustment": "raw",
        "sort": "asc",
    }

    rows, token = [], None
    while True:
        if token:
            params["page_token"] = token
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Alpaca {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        rows.extend(data.get("bars") or [])
        token = data.get("next_page_token")
        if not token:
            break

    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df = df.set_index("t").rename(columns={
        "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"
    })[["Open", "High", "Low", "Close", "Volume"]]
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


def format_alert(sig):
    emoji = "🔥" if sig["side"] == "LONG" else "🩸"
    tlines = "\n".join(f"  T{i+1} ({r}R): ${t}"
                       for i, (t, r) in enumerate(zip(sig["targets"], Config.TARGETS_R)))
    reasons = "\n".join(f"  ✓ {r}" for r in sig["reasons"])
    return (
        f"{emoji} <b>{sig['side']} {sig['ticker']}</b>  "
        f"[{sig['score']}/{sig['max_score']}]\n"
        f"Pris: <b>${sig['price']}</b>   ATR: ${sig['atr']}\n"
        f"Stop: ${sig['stop']}   (risk ${sig['risk_per_share']}/aktie)\n"
        f"{tlines}\n"
        f"Förslag storlek: {sig['shares']} st (1% av {Config.ACCOUNT_SIZE:,})\n"
        f"HTF-bias: {sig['bias']}\n"
        f"<i>Confluence:</i>\n{reasons}"
    )


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
def scan_once():
    state = load_state()
    fired = 0
    print(f"[scan] feed={current_feed()}")
    for ticker in Config.TICKERS:
        try:
            htf = fetch_ohlcv(ticker, Config.HTF_TIMEFRAME, Config.HTF_LOOKBACK_DAYS)
            mtf = fetch_ohlcv(ticker, Config.MTF_TIMEFRAME, Config.MTF_LOOKBACK_DAYS)
            if len(htf) < 200 or len(mtf) < 50:
                print(f"{ticker}: för lite data"); continue

            # färsk bar?
            last = mtf.index[-1]
            age = (pd.Timestamp.now(tz=last.tz) - last).total_seconds() / 60
            if age > Config.BAR_MINUTES * 3:
                print(f"{ticker}: stale ({age:.0f} min) – stängt?"); continue

            mtf = add_indicators(mtf)
            bias = htf_bias(htf)
            sig = build_signal(ticker, mtf, bias)

            if sig and is_fresh(sig, state):
                msg = format_alert(sig)
                print(msg, "\n")
                send_telegram(msg)
                state[f"{sig['ticker']}:{sig['side']}"] = sig["bar_time"]
                fired += 1
            else:
                why = "ingen setup" if not sig else "redan larmat denna bar"
                print(f"{ticker}: {why} (bias {bias})")
        except Exception as e:
            print(f"{ticker} fel: {e}")
    save_state(state)
    return fired


# ==================================================================
# LOOP — exakt på bar-stängning
# ==================================================================
def run_loop():
    print("=== NASDAQ ROBBER startad ===")
    if not Config.ALPACA_KEY or not Config.ALPACA_SECRET:
        print("VARNING: ALPACA_KEY/ALPACA_SECRET saknas i miljön.")
    print(f"Tickers: {Config.TICKERS}  |  {Config.MTF_TIMEFRAME} setup / {Config.HTF_TIMEFRAME} bias  |  feed={current_feed()} (auto)")
    ok = send_telegram(
        "\u2705 <b>NASDAQ ROBBER startad</b>\n"
        f"Bevakar: {', '.join(Config.TICKERS)} \u00b7 setup {Config.MTF_TIMEFRAME} / bias {Config.HTF_TIMEFRAME}\n"
        f"Session 06:00\u201322:00 (m\u00e5n\u2013fre) \u00b7 larm vid \u2265{Config.MIN_SCORE}/7 confluence\n"
        f"Feed: {current_feed()}"
    )
    print(f"Startup-ping till Telegram: {'OK' if ok else 'MISSLYCKADES (kolla TOKEN/CHAT_ID)'}")
    while True:
        try:
            if in_session():
                scan_once()
            else:
                print("Utanför session — vilar.")
        except Exception as e:
            # En miss i en cykel får ALDRIG döda tråden / värd-appen.
            print(f"Robber loop-fel (hoppar över cykel): {e}")
        sleep = seconds_to_next_bar()
        print(f"Sover {sleep:.0f}s till nästa bar...\n")
        time.sleep(sleep)


def start_in_background():
    """
    Startar roboten i en daemon-tråd. Anropas från en värd-app (t.ex. grabit).
    Kraschar tråden påverkas inte värd-appen. Startar inte utan Alpaca-keys.
    """
    import threading
    if not (Config.ALPACA_KEY and Config.ALPACA_SECRET):
        print("Robber: hoppar över start — ALPACA_KEY/SECRET saknas.")
        return None
    t = threading.Thread(target=run_loop, daemon=True, name="nasdaq-robber")
    t.start()
    print("Robber: bakgrundstråd startad.")
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
