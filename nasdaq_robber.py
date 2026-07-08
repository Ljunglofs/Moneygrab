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
import requests
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
    TICKERS = ["NQ=F"]   # Nasdaq-only (US100). Lägg tillbaka "GC=F","BTC-USD" för fler instrument.
    NAMES   = {"NQ=F": "US100", "GC=F": "Guld", "BTC-USD": "Bitcoin"}
    # Killzone-viktning (confidence-justering efter svensk tid) — Nasdaq lever på US-sessionen
    KZ_OPEN = int(os.environ.get("KZ_OPEN", "12"))    # bonus: US-öppning 15:30-17:30
    KZ_US   = int(os.environ.get("KZ_US",   "6"))     # bonus: övrig US-session 17:30-22:00
    KZ_OFF  = int(os.environ.get("KZ_OFF",  "-15"))   # straff: natt/Asien (lågvolym-chop)
    KZ_LDN  = int(os.environ.get("KZ_LONDON", "6"))    # bonus: London-öppningen 08-11 (bra NQ-rörelser)

    # --- Timeframes (Alpaca-format) ---
    HTF_TIMEFRAME     = "1Hour"   # high-timeframe bias
    HTF_LOOKBACK_DAYS = 60
    # Setup/entry-timeframe: 5Min som standard (NQ-daytrading) — reagerar 3x
    # snabbare än gamla 15Min. Byt via env MTF_TF=15Min om det blir för brusigt.
    MTF_TIMEFRAME     = os.environ.get("MTF_TF", "5Min")
    MTF_LOOKBACK_DAYS = 12

    # --- Data ---
    # Datakälla är yfinance (NQ=F handlas nästan dygnet runt). Alpaca-lagret
    # är borttaget — det användes inte längre och gav vilseledande varningar.

    # --- Confluence thresholds ---
    MIN_SCORE      = int(os.environ.get("ROBBER_MIN_SCORE", "5"))  # av 7; sänk t.ex. till 4 via env för fler larm
    RSI_LONG_LOW, RSI_LONG_HIGH   = 50, 82   # tak höjt: stark RSI i en pump = momentum, inte svaghet
    RSI_SHORT_LOW, RSI_SHORT_HIGH = 18, 50   # golv sänkt: stark nedmomentum räknas
    MIN_REL_VOLUME = 1.2          # volym mot 20-snitt
    MIN_ATR_PCT    = 0.0008       # filtrera bort död chop (ATR/pris)

    # --- Risk ---
    ATR_STOP_MULT  = 1.3          # stop = swing-buffert eller ATR*mult (tightast vinner ej, säkrast)
    TARGETS_R      = [2.0, 2.67]  # TP1 ~2R, TP2 ~2.67R  (≈300/400 kr vid 150 kr risk)
    # Nivå-medvetna TP: snäpp R-målet till närmaste logiska nivå (PDH/PDL, runda tal)
    SNAP_TP    = os.environ.get("SNAP_TP", "1") == "1"
    ROUND_STEP = float(os.environ.get("ROUND_STEP", "100"))   # runda nivåer (NQ: 100-punkterssteg)
    SNAP_WIN_R = float(os.environ.get("SNAP_WIN_R", "0.6"))    # snäpp-fönster ovanför R-målet (i R)
    ACCOUNT_SIZE   = 100_000      # SEK, för positionsstorleksförslag
    RISK_PCT       = 0.01         # 1% risk per trade (appens förslag)

    # --- Live-risk (cTrader) ---  storlek sizas så SL-avståndet = RISK_PER_TRADE_KR
    RISK_PER_TRADE_KR = int(os.environ.get("RISK_PER_TRADE_KR", "150"))   # max förlust per trade
    RISK_BUDGET_KR    = int(os.environ.get("RISK_BUDGET_KR", "2600"))     # hård kill-switch (total drawdown)
    MAX_OPEN          = int(os.environ.get("MAX_OPEN", "1"))              # max samtidiga positioner

    # --- Confidence (0-100) ---  summerar till 100 över de TA-komponenter boten faktiskt mäter
    CONF_WEIGHTS = {                        # summa 100 -- ICT-baserad
        "trend":     20,   # HTF-bias + EMA-stack + momentum (partiellt)
        "sweep":     20,   # Liquidity sweep
        "bos":       16,   # Break of Structure -- brytning MED htf-bias (fortsättning)
        "choch":      8,   # Change of Character -- brytning MOT htf-bias (tidig vändning, svagare tills bekräftad)
        "fvg":       14,   # Fair Value Gap (retestad)
        "ob":        10,   # Order Block (retestad)
        "volym":      4,   # Relativ volym
        "orderflow":  8,   # Volymproxy köp/säljtryck (CLV x volym, ackumulerat)
        "retest":    15,   # Breakout + Retest -- entry VID nivån, inte jaga rörelsen
    }
    CONF_GREEN    = int(os.environ.get("CONF_GREEN",  "75"))   # gron A+ (stark konfluens)
    CONF_YELLOW   = int(os.environ.get("CONF_YELLOW", "55"))   # gul / bevaka
    CONF_MIN_SEND = int(os.environ.get("CONF_MIN_SEND","55"))  # 5/7-basen ar triggern; confidence ar betyget
    SHADOW_LOG    = os.environ.get("SHADOW_LOG", "robber_shadow.jsonl")
    # --- Trade-journal (utfall + stats) ---
    DATA_DIR      = os.environ.get("DATA_DIR", ".")   # peka på Render Persistent Disk (t.ex. /var/data) för beständighet
    OPEN_TRADES   = os.path.join(DATA_DIR, "robber_open_trades.json")
    OUTCOMES_LOG  = os.path.join(DATA_DIR, "robber_outcomes.jsonl")
    TRADE_MAX_HRS = float(os.environ.get("TRADE_MAX_HRS", "24"))   # timeout om varken TP/SL nås
    TRACK_SHADOW  = os.environ.get("TRACK_SHADOW", "0") == "1"     # följ även osända kandidater (default bara sända)
    NOTIFY_CLOSE  = os.environ.get("NOTIFY_CLOSE", "1") == "1"     # notis när en trade stänger
    STATS_ANY_CHAT = os.environ.get("STATS_ANY_CHAT", "0") == "1"  # svara på /stats från valfri chat (default bara ägaren)

    # --- EXEKVERING via Alpaca (paper ar default-vagen) --------------
    # EXECUTE=off  -> bara signaler (som idag)
    # EXECUTE=paper -> riktiga bracketordrar i Alpaca PAPER (latsaspengar)
    # EXECUTE=live  -> riktiga pengar; kraver DESSUTOM EXEC_LIVE_OK=JA
    EXECUTE       = os.environ.get("EXECUTE", "off").strip().lower()
    EXEC_TICKER   = os.environ.get("EXEC_TICKER", "QQQ").upper()
    EXEC_MIN_CONF = int(os.environ.get("EXEC_MIN_CONF", "72"))
    MAX_POS_USD   = float(os.environ.get("MAX_POS_USD", "5000"))
    DAILY_LOSS_LIMIT_PCT = float(os.environ.get("DAILY_LOSS_LIMIT_PCT", "3"))
    APCA_KEY      = os.environ.get("APCA_API_KEY_ID", "")
    APCA_SECRET   = os.environ.get("APCA_API_SECRET_KEY", "")

    # --- Session (svensk tid, DST-säkert via zoneinfo) ---
    LOCAL_TZ          = "Europe/Stockholm"
    SESSION_START     = (6, 0)            # 06:00 svensk tid
    SESSION_END       = (22, 0)           # 22:00 svensk tid
    TRADE_DAYS        = {0, 1, 2, 3, 4}   # mån–fre

    # --- Loop ---
    # Skanna i takt med setup-timeframen (5Min -> var 5:e min). Härleds ur
    # MTF_TIMEFRAME så de aldrig glider isär.
    BAR_MINUTES   = int("".join(c for c in MTF_TIMEFRAME if c.isdigit()) or "5")
    BUFFER_SEC    = 20            # vänta efter bar-stängning innan hämtning
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    CHAT_ID        = os.environ.get("CHAT_ID", "")


# --- Persistens: alla robotens filer går via DATA_DIR ---------------
# OBS: Renders filsystem är flyktigt — raderas vid varje deploy/omstart.
# Sätt env DATA_DIR=/var/data (Render Persistent Disk) så överlever
# dedupe-state, öppna trades och utfallslogg. Utan disk körs allt ändå,
# men efter omstart nollställs state (ev. ett dubbelt larm på aktuell bar).
Config.STATE_FILE  = os.path.join(Config.DATA_DIR, "robber_state.json")
Config.SHADOW_LOG  = (Config.SHADOW_LOG if os.path.sep in Config.SHADOW_LOG
                      else os.path.join(Config.DATA_DIR, Config.SHADOW_LOG))
try:
    os.makedirs(Config.DATA_DIR, exist_ok=True)
except Exception as _e:
    print(f"Robber: kunde inte skapa DATA_DIR ({Config.DATA_DIR}): {_e}")


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
    df = None
    for attempt in (1, 2):                      # en retry — Yahoo rate-limitar ofta Render-IP:n
        try:
            df = yf.download(ticker, period=period, interval=interval,
                             progress=False, auto_adjust=False, threads=False)
        except Exception as e:
            print(f"[data] yfinance FEL ({ticker} {interval}, försök {attempt}/2): {e}")
            df = None
        if df is not None and not df.empty:
            break
        if attempt == 1:
            time.sleep(5)

    if df is None or df.empty:
        # Detta är skillnaden mellan "strikt config" och "riktigt datafel":
        # syns denna rad i loggen är det Yahoo/nätet, inte dina trösklar.
        print(f"[data] yfinance gav 0 rader för {ticker} ({interval}) — "
              f"trolig rate-limit/blockad från Yahoo, inte ett configfel.")
        try:
            STATUS["data_fel_total"] = STATUS.get("data_fel_total", 0) + 1
            STATUS["senaste_data_fel"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            pass
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
    if (prev.Low <= prev.ema20 and cur.Close > cur.ema20) or (cur.Close > prev.High):
        s += 1; reasons.append("Pullback-reclaim / breakout")
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
    if (prev.High >= prev.ema20 and cur.Close < cur.ema20) or (cur.Close < prev.Low):
        s += 1; reasons.append("Studs-reject / breakdown")
    if cur.High < prev.High:
        s += 1; reasons.append("Lägre högpunkt")
    return s, reasons


def _tp_levels(df, price):
    """Logiska nivåer nära priset: gårdagens high/low (PDH/PDL) + runda tal."""
    levels = {}
    try:
        dates = np.array([t.date() for t in df.index])
        uniq = sorted(set(dates.tolist()))
        if len(uniq) >= 2:
            m = dates == uniq[-2]
            levels[round(float(df["High"].values[m].max()), 2)] = "PDH"
            levels[round(float(df["Low"].values[m].min()), 2)]  = "PDL"
    except Exception:
        pass
    step = Config.ROUND_STEP
    if step > 0:
        base = round(price / step) * step
        for k in range(-4, 5):
            v = round(base + k * step, 2)
            levels.setdefault(v, f"rund {v:g}")
    return levels


def _snap_targets(targets, price, stop, side, levels):
    """Behåll R som golv, men snäpp varje TP till närmaste logiska nivå inom ett fönster."""
    if not Config.SNAP_TP or not levels:
        return list(targets), ["" for _ in targets]
    risk = abs(price - stop) or 1e-9
    win, tol = risk * Config.SNAP_WIN_R, risk * 0.05
    out, labs = [], []
    items = sorted(levels.items())
    for bt in targets:
        cand, lab, best = bt, "", win + tol + 1
        for v, name in items:
            lo, hi = (bt - tol, bt + win) if side == "LONG" else (bt - win, bt + tol)
            if lo <= v <= hi and abs(v - bt) < best:
                best, cand, lab = abs(v - bt), round(v, 2), name
        out.append(cand); labs.append(lab)
    for i in range(len(out)):                      # håll bortom entry
        beyond = out[i] > price if side == "LONG" else out[i] < price
        if not beyond:
            out[i], labs[i] = targets[i], ""
    for i in range(1, len(out)):                   # håll ordning TP1<TP2 (resp. omvänt)
        bad = out[i] <= out[i-1] if side == "LONG" else out[i] >= out[i-1]
        if bad:
            out[i], labs[i] = targets[i], ""
    return out, labs


# =====================================================================
#  ENTRY-SKYDD  ·  lärdomar från live-handel (3xSL 2026-07-07)
#  1. Jaga inte: har rörelsen redan gått > CHASE_ATR x ATR i signalens
#     riktning (12 barer) är läget sent — hoppa över, vänta på rekyl.
#  2. Gå inte emot stark motcandle: shorta inte när senaste baren är en
#     kraftig grön candle (V-vändning) och omvänt.
#  3. SL-cooldown: efter en stopp på en sida — 90 min paus; efter två
#     stoppar samma sida samma dag — sidan stängd till imorgon.
#  Justeras via env: CHASE_ATR (std 3.5), SL_COOLDOWN_MIN (std 90),
#  SL_MAX_PER_SIDE (std 2).
# =====================================================================
def _entry_guards(ticker, side, df, price, atr):
    """Returnerar blockeringsskäl (str) eller None om entryn är OK."""
    try:
        chase = float(os.environ.get("CHASE_ATR", "3.5"))
        look = df.iloc[-min(13, len(df)):-1]
        if side == "SHORT":
            ext = float(look.High.max()) - price
        else:
            ext = price - float(look.Low.min())
        if atr > 0 and ext > chase * atr:
            return "rörelsen redan %.1f ATR i riktningen — jagat läge" % (ext / atr)
    except Exception:
        pass
    try:
        cur = df.iloc[-1]
        body = float(cur.Close) - float(cur.Open)
        if side == "SHORT" and body > 0.5 * atr:
            return "senaste baren stark grön (+%.0f) — shortar inte in i rekyl" % body
        if side == "LONG" and -body > 0.5 * atr:
            return "senaste baren stark röd (%.0f) — köper inte in i ras" % body
    except Exception:
        pass
    try:
        cool = int(os.environ.get("SL_COOLDOWN_MIN", "90"))
        maxsl = int(os.environ.get("SL_MAX_PER_SIDE", "2"))
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        with open(Config.OUTCOMES_LOG) as f:
            rows = [json.loads(x) for x in f.readlines()[-200:] if x.strip()]
        sls = [r for r in rows
               if r.get("ticker") == ticker and r.get("side") == side
               and r.get("outcome") == "SL"
               and str(r.get("closed_ts", "")).startswith(today)]
        if len(sls) >= maxsl:
            return "%d SL på %s idag — sidan pausad till imorgon" % (len(sls), side)
        if sls:
            last = datetime.fromisoformat(str(sls[-1]["closed_ts"]).replace("Z", "+00:00"))
            mins = (now - last).total_seconds() / 60
            if mins < cool:
                return "SL för %d min sedan — cooldown %d min" % (int(mins), cool)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return None


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

    block = _entry_guards(ticker, side, df, price, atr)
    if block:
        STATUS["blocked_last"] = "%s %s %s: %s" % (
            datetime.now(timezone.utc).strftime("%H:%M"), side, ticker, block)
        print("[guard] %s %s: %s" % (side, ticker, block))
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

    targets, tp_labels = _snap_targets(targets, price, stop, side, _tp_levels(df, price))

    shares = math.floor((Config.ACCOUNT_SIZE * Config.RISK_PCT) / risk)

    return {
        "ticker": ticker, "side": side, "score": score, "max_score": 7,
        "price": price, "atr": round(atr, 2), "stop": stop,
        "risk_per_share": round(risk, 2), "targets": targets, "tp_labels": tp_labels,
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


def detect_retest(df, side, lookback=24):
    """Breakout + Retest (⭐ setup): en swingnivå bryts, priset kommer TILLBAKA
    och testar den brutna nivån som nu håller (rejection). Ger entry VID nivån
    i stället för att jaga den redan gångna rörelsen.
    LONG: swing-high bryts uppåt -> pris rekylerar ner till nivån -> stänger över.
    SHORT: swing-low bryts nedåt -> pris rekylerar upp till nivån -> stänger under.
    Returnerar (bool, brott-nivå)."""
    H = df["High"].to_numpy(); L = df["Low"].to_numpy(); C = df["Close"].to_numpy()
    n = len(C)
    if n < 8:
        return False, None
    atr = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0
    if atr <= 0:
        atr = float(np.nanmean(H[-20:] - L[-20:])) or 1.0
    band = 0.45 * atr                       # hur nära nivån räknas som retest
    sh, sl = _swings(H, L, k=2)
    lo = max(0, n - lookback)
    if side == "LONG":
        for si in reversed([i for i in sh if lo <= i <= n - 4]):
            lvl = H[si]
            broke = any(C[j] > lvl + 0.1 * atr for j in range(si + 1, n - 1))   # bekräftat brott
            if not broke:
                continue
            retest = (L[-1] <= lvl + band) and (C[-1] > lvl) and (C[-1] >= C[-2] * 0.999)
            if retest:
                return True, round(float(lvl), 2)
    else:
        for si in reversed([i for i in sl if lo <= i <= n - 4]):
            lvl = L[si]
            broke = any(C[j] < lvl - 0.1 * atr for j in range(si + 1, n - 1))
            if not broke:
                continue
            retest = (H[-1] >= lvl - band) and (C[-1] < lvl) and (C[-1] <= C[-2] * 1.001)
            if retest:
                return True, round(float(lvl), 2)
    return False, None


def detect_orderflow(df, side, lookback=10):
    """Volymproxy for orderflow (ingen tick-data tillganglig via yfinance for NQ).
    Close-Location-Value (Chaikin-stil) x volym, ackumulerat over `lookback` barer:
    stangning nara high med volym = kopptryck, nara low med volym = saljtryck.
    Returnerar (aligned: bool, norm: -1..1 dar norm ar nettotryckets andel av total volym)."""
    H = df["High"].to_numpy(); L = df["Low"].to_numpy()
    C = df["Close"].to_numpy(); V = df["Volume"].to_numpy()
    n = len(C)
    a = max(0, n - lookback)
    if n - a < 3:
        return False, 0.0
    rng = H[a:n] - L[a:n]
    rng = np.where(rng == 0, np.nan, rng)
    clv = ((C[a:n] - L[a:n]) - (H[a:n] - C[a:n])) / rng
    clv = np.nan_to_num(clv, nan=0.0)
    vol_sum = float(np.nansum(V[a:n])) or 1.0
    norm = float(np.nansum(clv * V[a:n])) / vol_sum   # -1..1
    aligned = (norm > 0.15) if side == "LONG" else (norm < -0.15)
    return bool(aligned), round(norm, 2)


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
    # BOS = strukturbrytning MED htf-bias (fortsättning) -> stark signal
    # CHoCH = strukturbrytning MOT htf-bias, eller bias NEUTRAL -> tidig vändning, svagare tills bekräftad
    bos   = bool(mss and bias == side)
    choch = bool(mss and not bos)
    fvg,   fvg_zone  = detect_fvg(df, side)
    ob,    ob_zone   = detect_ob(df, side)
    flow_ok, flow_delta = detect_orderflow(df, side)
    retest, retest_lvl = detect_retest(df, side)
    W = Config.CONF_WEIGHTS
    conf = (W["trend"] * trend_frac
            + (W["sweep"]     if sweep   else 0)
            + (W["bos"]       if bos     else 0)
            + (W["choch"]     if choch   else 0)
            + (W["fvg"]       if fvg     else 0)
            + (W["ob"]        if ob      else 0)
            + (W["volym"]     if vol     else 0)
            + (W["orderflow"] if flow_ok else 0)
            + (W.get("retest", 0) if retest else 0))
    conf = max(0, min(100, int(round(conf))))
    groups = {
        "Trend": trend_frac >= 0.5, "Liquidity Sweep": bool(sweep), "BOS": bos, "CHoCH": choch,
        "FVG": bool(fvg), "Order Block": bool(ob), "Retest": bool(retest),
        "Volym": vol, "Orderflow": flow_ok,
    }
    comps = {
        "trend_frac": round(trend_frac, 2), "htf": htf, "ema": ema, "mom": mom,
        "sweep": bool(sweep), "sweep_lvl": sweep_lvl, "bos": bos, "choch": choch, "mss_lvl": mss_lvl,
        "fvg": bool(fvg), "fvg_zone": fvg_zone, "ob": bool(ob), "ob_zone": ob_zone, "volym": vol,
        "orderflow": flow_ok, "orderflow_delta": flow_delta,
        "retest": bool(retest), "retest_lvl": retest_lvl,
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
    """Exekvering via Alpaca. Styrs av env EXECUTE=off|paper|live."""
    if Config.EXECUTE in ("paper", "live"):
        execute_signal(sig)
    else:
        print("[exec] EXECUTE=off -- signal skickad men ingen order "
              "(satt EXECUTE=paper i Render for att handla i Alpaca paper)")


# ==================================================================
# EXEKVERING -- Alpaca bracketordrar (paper forst, live bakom dubbelspärr)
# ==================================================================
_EXEC = {"day": "", "halted": False}


def _alp_base():
    return ("https://api.alpaca.markets" if Config.EXECUTE == "live"
            else "https://paper-api.alpaca.markets")


def _alp_hdr():
    return {"APCA-API-KEY-ID": Config.APCA_KEY,
            "APCA-API-SECRET-KEY": Config.APCA_SECRET}


def _alp_get(path, base=None):
    r = requests.get((base or _alp_base()) + path, headers=_alp_hdr(), timeout=10)
    r.raise_for_status()
    return r.json()


def _alp_post(path, payload):
    r = requests.post(_alp_base() + path, headers=_alp_hdr(), json=payload, timeout=10)
    if r.status_code >= 400:
        raise RuntimeError(f"Alpaca {r.status_code}: {r.text[:200]}")
    return r.json()


def _exec_last_price():
    j = _alp_get(f"/v2/stocks/{Config.EXEC_TICKER}/trades/latest?feed=iex",
                 base="https://data.alpaca.markets")
    return float(j["trade"]["p"])


def execute_signal(sig):
    """Lagger en bracketorder (entry + SL + TP) i Alpaca utifran en NQ-signal.

    Sakerhetssparrar, i ordning:
      1. live kraver EXEC_LIVE_OK=JA utover EXECUTE=live
      2. confidence under EXEC_MIN_CONF -> ingen order
      3. borsen maste vara oppen (bracket kraver RTH)
      4. dagsforlustgrans: equity < gardagens * (1 - DAILY_LOSS_LIMIT_PCT%) -> paus till nasta dag
      5. max EN position/oppen order i EXEC_TICKER at gangen
      6. storlek: risk = equity * RISK_PCT, cappad av MAX_POS_USD
    NQ-nivaerna oversatts till EXEC_TICKER via PROCENT (inte dollar), sa
    SL/TP far exakt samma relativa avstand som signalen."""
    mode = Config.EXECUTE
    if mode == "live" and os.environ.get("EXEC_LIVE_OK", "") != "JA":
        print("[exec] EXECUTE=live men EXEC_LIVE_OK=JA saknas -- ingen order (dubbelsparr)")
        return
    if not (Config.APCA_KEY and Config.APCA_SECRET):
        print("[exec] APCA_API_KEY_ID/SECRET saknas -- kan inte handla")
        return
    conf = sig.get("confidence", 0)
    if conf < Config.EXEC_MIN_CONF:
        print(f"[exec] conf {conf} < {Config.EXEC_MIN_CONF} -- skippar")
        return
    try:
        # dagsaterstallning av spärren
        from zoneinfo import ZoneInfo as _ZI
        today = datetime.now(_ZI("America/New_York")).strftime("%Y-%m-%d")
        if _EXEC["day"] != today:
            _EXEC["day"] = today
            _EXEC["halted"] = False
        if _EXEC["halted"]:
            print("[exec] pausad (dagsforlustgrans) -- ingen order idag")
            return

        clock = _alp_get("/v2/clock")
        if not clock.get("is_open"):
            print("[exec] borsen stangd -- bracketorder kraver oppen bors, skippar")
            return

        acct = _alp_get("/v2/account")
        eq = float(acct.get("equity") or 0)
        last_eq = float(acct.get("last_equity") or eq)
        if last_eq and eq < last_eq * (1 - Config.DAILY_LOSS_LIMIT_PCT / 100):
            _EXEC["halted"] = True
            send_telegram(f"\U0001F6D1 <b>ROBBER EXEC pausad</b>: dagsforlustgransen "
                          f"({Config.DAILY_LOSS_LIMIT_PCT}%) nadd. Ateraktiveras imorgon.")
            return

        for p in _alp_get("/v2/positions"):
            if p.get("symbol") == Config.EXEC_TICKER:
                print("[exec] position redan oppen i", Config.EXEC_TICKER, "-- skippar")
                return
        if _alp_get(f"/v2/orders?status=open&symbols={Config.EXEC_TICKER}"):
            print("[exec] oppen order finns redan -- skippar")
            return

        # Oversatt NQ-nivaer -> exec-tickern via procentavstand
        e = float(sig["price"]); s = float(sig["stop"]); t1 = float(sig["targets"][0])
        stop_pct = abs(e - s) / e
        tp_pct   = abs(t1 - e) / e
        q = _exec_last_price()
        is_long = sig["side"] == "LONG"
        stop_px = round(q * (1 - stop_pct) if is_long else q * (1 + stop_pct), 2)
        tp_px   = round(q * (1 + tp_pct)   if is_long else q * (1 - tp_pct), 2)

        risk_usd = eq * Config.RISK_PCT
        qty = math.floor(risk_usd / max(q * stop_pct, 0.01))
        qty = min(qty, math.floor(Config.MAX_POS_USD / max(q, 0.01)))
        if qty < 1:
            print(f"[exec] qty<1 (risk {risk_usd:.0f} USD, stop {stop_pct*100:.2f}%) -- skippar")
            return

        order = _alp_post("/v2/orders", {
            "symbol": Config.EXEC_TICKER, "qty": str(qty),
            "side": "buy" if is_long else "sell",
            "type": "market", "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{tp_px:.2f}"},
            "stop_loss":   {"stop_price": f"{stop_px:.2f}"},
        })
        tag = "PAPER" if mode == "paper" else "LIVE \u26A0\uFE0F"
        send_telegram(
            f"\U0001F916 <b>ROBBER EXEC [{tag}]</b>\n"
            f"{'KOPT' if is_long else 'BLANKAT'} {qty} {Config.EXEC_TICKER} @ ~${q:.2f}\n"
            f"SL ${stop_px:.2f} ({stop_pct*100:.2f}%)  \u00b7  TP ${tp_px:.2f} ({tp_pct*100:.2f}%)\n"
            f"Fran {sig['ticker']}-signal, conf {conf}/100")
        print(f"[exec] {tag}-order lagd: {order.get('id', '?')} "
              f"{qty}x{Config.EXEC_TICKER} SL {stop_px} TP {tp_px}")
        try:
            import push_notify as PN
            PN.send_all(f"\U0001F916 ROBBER [{tag}]",
                        f"{'KOPT' if is_long else 'BLANKAT'} {qty} {Config.EXEC_TICKER} @ ${q:.2f} \u00b7 SL ${stop_px:.2f} \u00b7 TP ${tp_px:.2f}",
                        url="/", tag="robber-exec")
        except Exception:
            pass
    except Exception as ex:
        print(f"[exec] FEL: {ex}")
        try:
            send_telegram(f"\u26A0\uFE0F ROBBER EXEC-fel: {str(ex)[:180]}")
        except Exception:
            pass


# ==================================================================
# LARM / STATE
# ==================================================================
def _jload(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _jsave(path, obj):
    try:
        with open(path, "w") as f:
            json.dump(obj, f)
    except Exception as e:
        print("journal-skrivfel:", e)


def open_trade(sig, sent):
    """Lägg en signal som öppen trade att följa upp (TP1/SL)."""
    if not sent and not Config.TRACK_SHADOW:
        return
    trades = _jload(Config.OPEN_TRADES, [])
    tid = f"{sig['ticker']}:{sig['side']}:{sig['bar_time']}"
    if any(t.get("id") == tid for t in trades):
        return
    trades.append({
        "id": tid, "ticker": sig["ticker"], "side": sig["side"],
        "entry": sig["price"], "sl": sig["stop"], "targets": sig["targets"],
        "confidence": sig.get("confidence"), "score7": sig.get("score"),
        "killzone": sig.get("killzone", ""), "bias": sig.get("bias"),
        "sent": bool(sent),
        "opened_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bar_time": sig["bar_time"],
    })
    _jsave(Config.OPEN_TRADES, trades)


def _eval_trade(t, df, r1):
    """(outcome, R, tid) eller (None,None,None) om fortfarande öppen. SL först vid krock."""
    side, sl, tp1 = t["side"], t["sl"], t["targets"][0]
    try:
        after = df[df.index > pd.Timestamp(t["bar_time"])]
    except Exception:
        after = df.iloc[0:0]
    for ts, row in after.iterrows():
        hi, lo = float(row["High"]), float(row["Low"])
        sl_hit = (lo <= sl) if side == "LONG" else (hi >= sl)
        tp_hit = (hi >= tp1) if side == "LONG" else (lo <= tp1)
        if sl_hit:
            return "SL", -1.0, ts
        if tp_hit:
            return "TP1", round(float(r1), 2), ts
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(t["opened_ts"])).total_seconds() / 3600.0
    except Exception:
        age_h = 0.0
    if age_h > Config.TRADE_MAX_HRS and len(df):
        last = float(df["Close"].iloc[-1]); risk = abs(t["entry"] - t["sl"]) or 1e-9
        r = (last - t["entry"]) / risk if side == "LONG" else (t["entry"] - last) / risk
        return "timeout", round(r, 2), df.index[-1]
    return None, None, None


def settle_trades(ticker, df):
    """Stänger öppna trades för en ticker som nått TP1/SL/timeout. Returnerar stängda."""
    trades = _jload(Config.OPEN_TRADES, [])
    if not trades:
        return []
    r1 = Config.TARGETS_R[0]
    still, closed = [], []
    for t in trades:
        if t.get("ticker") != ticker:
            still.append(t); continue
        outcome, r, when = _eval_trade(t, df, r1)
        if outcome is None:
            still.append(t); continue
        rec = {**t, "outcome": outcome, "r": r,
               "closed_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "closed_bar": str(when)}
        try:
            with open(Config.OUTCOMES_LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception as e:
            print("outcome-skrivfel:", e)
        closed.append(rec)
    if closed:
        _jsave(Config.OPEN_TRADES, still)
    return closed


def _summarize(rows):
    n = len(rows)
    if not n:
        return None
    wins = sum(1 for r in rows if r.get("outcome") == "TP1")
    losses = sum(1 for r in rows if r.get("outcome") == "SL")
    tos = sum(1 for r in rows if r.get("outcome") == "timeout")
    decided = wins + losses
    totR = sum(float(r.get("r") or 0) for r in rows)
    return dict(n=n, wins=wins, losses=losses, tos=tos,
                wr=(wins / decided * 100) if decided else 0.0,
                totR=totR, avgR=totR / n)


def stats_text():
    """Läser outcomes-loggen och bygger en stats-rapport."""
    recs = []
    try:
        with open(Config.OUTCOMES_LOG) as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    except Exception:
        pass
    rows = [r for r in recs if r.get("sent")] or recs
    s = _summarize(rows)
    if not s:
        return "\U0001F4CA <b>Robber-stats</b>: inga avgjorda trades \u00e4n."
    out = [
        "\U0001F4CA <b>Robber-stats</b>",
        f"Trades: {s['n']}  \u00b7  \u2705 {s['wins']}  \u274c {s['losses']}"
        + (f"  \u23F1 {s['tos']}" if s['tos'] else ""),
        f"Tr\u00e4ffprocent: <b>{s['wr']:.0f}%</b>",
        f"Summa: <b>{s['totR']:+.1f}R</b>  \u00b7  snitt {s['avgR']:+.2f}R/trade",
    ]
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        by[r.get("killzone") or "-"].append(r)
    if len(by) > 1:
        out.append("\u2500" * 5)
        for kz, rws in sorted(by.items(), key=lambda x: -len(x[1])):
            ss = _summarize(rws)
            if ss:
                out.append(f"{kz}: {ss['n']}st \u00b7 {ss['wr']:.0f}% \u00b7 {ss['totR']:+.1f}R")
    return "\n".join(out)


def send_telegram(text, chat_id=None):
    if not Config.TELEGRAM_TOKEN or not Config.CHAT_ID:
        miss = []
        if not Config.TELEGRAM_TOKEN: miss.append("TELEGRAM_TOKEN")
        if not Config.CHAT_ID: miss.append("CHAT_ID")
        print(f"[telegram ej konfigurerad – saknar {', '.join(miss)} i miljön]\n" + text)
        return False
    import requests
    url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id or Config.CHAT_ID, "text": text,
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
    _labs = sig.get("tp_labels") or []
    tplines = "   ".join(
        f"TP{i+1}: {t}" + (f" ({_labs[i]})" if i < len(_labs) and _labs[i] else "")
        for i, t in enumerate(tgs))
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
        f"Plan: risk {Config.RISK_PER_TRADE_KR} kr  \u00b7  TP-mål "
        + " / ".join(f"~{int(round(Config.RISK_PER_TRADE_KR*r))} kr" for r in Config.TARGETS_R),
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
    "scans_total": 0,                 # antal genomforda scanningar sedan start
    "data_fel_total": 0,              # antal ganger yfinance gett 0 rader/fel
    "senaste_data_fel": None,         # nar det senast hande
    "hogsta_conf_idag": 0,            # hogsta confidence som setts idag (aven under troskel)
}


def killzone_adjust(now_local):
    """Confidence-justering för Nasdaq efter session (svensk tid).
    US cash-sessionen 15:30-22:00 är prime (öppningen 15:30-17:30 bäst);
    förmarknad/överlapp neutral; natt/Asien/EU-morgon är lågvolym-chop och straffas."""
    h = now_local.hour + now_local.minute / 60.0
    if 15.5 <= h < 17.5:
        return Config.KZ_OPEN, "NY open killzone"
    if 17.5 <= h < 22.0:
        return Config.KZ_US, "US-session"
    if 13.0 <= h < 15.5:
        return 0, "EU/US-överlapp"
    if 8.0 <= h < 11.0:
        return Config.KZ_LDN, "London killzone"
    if 11.0 <= h < 13.0:
        return 0, "EU-session"
    return Config.KZ_OFF, "lågvolym/chop"


def scan_once():
    from zoneinfo import ZoneInfo
    state = load_state()
    tradable = in_session()   # 06-22 mån-fre: trade-larm/auto tillåtna. Annars: bevaka + shadow.
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
            for _c in settle_trades(ticker, mtf):
                if Config.NOTIFY_CLOSE and _c.get("sent"):
                    _e = "\u2705" if _c["outcome"] == "TP1" else ("\u274c" if _c["outcome"] == "SL" else "\u23F1")
                    send_telegram(f"{_e} <b>{Config.NAMES.get(ticker, ticker)} {_c['side']}</b> st\u00e4ngd: "
                                  f"{_c['outcome']} ({_c['r']:+.2f}R) \u00b7 entry {_c['entry']}")
            bias = htf_bias(htf)
            sig = build_signal(ticker, mtf, bias)
            if sig:
                conf, comps, groups = compute_confidence(mtf, bias, sig["side"])
                kz_delta, kz_label = killzone_adjust(datetime.now(ZoneInfo(Config.LOCAL_TZ)))
                conf = max(0, min(100, conf + kz_delta))
                sig["confidence"] = conf; sig["groups"] = groups; sig["components"] = comps
                sig["killzone"] = kz_label; sig["kz_delta"] = kz_delta

            if sig:
                try:
                    STATUS["hogsta_conf_idag"] = max(STATUS.get("hogsta_conf_idag", 0),
                                                     int(sig.get("confidence", 0)))
                except Exception:
                    pass
            if sig and is_fresh(sig, state):
                strong  = sig["confidence"] >= Config.CONF_MIN_SEND
                send_ok = strong and tradable          # larm bara i handelsfönstret
                _shadow_log(sig, sent=send_ok)
                open_trade(sig, send_ok)
                state[f"{sig['ticker']}:{sig['side']}"] = sig["bar_time"]
                if send_ok:
                    msg = format_alert(sig)
                    if sig.get("killzone"):
                        _d = sig.get("kz_delta", 0)
                        msg += f"\n\U0001F551 {sig['killzone']}" + (f" ({_d:+d})" if _d else "")
                    ctx = poly_context(sig["ticker"])
                    if ctx:
                        msg = msg + "\n\n" + ctx
                    print(msg, "\n")
                    send_telegram(msg)
                    try:
                        import push_notify as PN
                        _tps = sig.get("targets") or []
                        _tp = (" | TP: " + str(_tps[0])) if _tps else ""
                        _pil = "\U0001F4C8" if sig["side"] == "LONG" else "\U0001F4C9"
                        PN.send_all(
                            f"\u26A1 NASDAQ ROBBER\u2122 \u00b7 {sig['confidence']}/100",
                            (f"NEW ENTRY SIGNAL {_pil}\n"
                             f"{sig['side']} \u00b7 {Config.NAMES.get(sig['ticker'], sig['ticker'])}\n"
                             f"Entry: {sig['price']} | SL: {sig['stop']}{_tp}"),
                            url="/", tag="robber-signal")
                    except Exception as _pe:
                        print(f"[push] hoppade over: {_pe}")
                    fired += 1
                    results[ticker] = f"LARM {sig['side']} {sig['confidence']}/100"
                    maybe_autotrade(sig)
                elif strong and not tradable:
                    results[ticker] = f"\U0001F319 natt-setup {sig['confidence']}/100 (bevakad, ej skickad)"
                    print(f"{ticker}: {results[ticker]}")
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
    STATUS["scans_total"] = STATUS.get("scans_total", 0) + 1
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


def morning_brief():
    """\U0001F305 Kort lagesbild vid handelsstart: trend + natt-range (Asien) per instrument."""
    lines = ["\U0001F305 <b>Morgonbrief</b> \u2013 laget infor dagen"]
    for ticker in Config.TICKERS:
        name = Config.NAMES.get(ticker, ticker)
        try:
            htf = fetch_ohlcv(ticker, Config.HTF_TIMEFRAME, Config.HTF_LOOKBACK_DAYS)
            mtf = fetch_ohlcv(ticker, Config.MTF_TIMEFRAME, Config.MTF_LOOKBACK_DAYS)
            if len(htf) < 50 or len(mtf) < 10:
                lines.append(f"\u2022 <b>{name}</b>: data saknas"); continue
            bias = htf_bias(htf)
            recent = mtf.iloc[-32:]                       # ~8h = natt/Asien-range
            hi = float(recent["High"].max()); lo = float(recent["Low"].min())
            price = float(mtf["Close"].iloc[-1])
            tag = ("\U0001F7E2 Bullish" if bias == "LONG"
                   else ("\U0001F534 Bearish" if bias == "SHORT" else "\u26AA Neutral"))
            lines.append(f"\u2022 <b>{name}</b>: {tag} \u00b7 natt-range {lo:.1f}\u2013{hi:.1f} \u00b7 nu {price:.1f}")
        except Exception as e:
            lines.append(f"\u2022 <b>{name}</b>: fel ({e})")
    lines.append("\n<i>Skannar dygnet runt \u00b7 trade-larm 06:00\u201322:00.</i>")
    send_telegram("\n".join(lines))


def _open_text():
    trades = _jload(Config.OPEN_TRADES, [])
    if not trades:
        return "Inga \u00f6ppna trades just nu."
    out = [f"\U0001F4C2 <b>\u00d6ppna trades: {len(trades)}</b>"]
    for t in trades[:20]:
        out.append(f"{Config.NAMES.get(t['ticker'], t['ticker'])} {t['side']} \u00b7 entry {t['entry']} "
                   f"\u00b7 SL {t['sl']} \u00b7 TP {t['targets'][0]} \u00b7 conf {t.get('confidence')}")
    return "\n".join(out)


def command_listener():
    """Lyssnar p\u00e5 Telegram-kommandon (/stats, /open) via long-polling i egen tr\u00e5d."""
    if not Config.TELEGRAM_TOKEN:
        return
    import requests
    base = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}"
    offset = None
    try:                                   # dr\u00e4nera gamla updates -> svara bara p\u00e5 nya kommandon
        r = requests.get(f"{base}/getUpdates", params={"timeout": 0}, timeout=15).json()
        if r.get("ok") and r.get("result"):
            offset = r["result"][-1]["update_id"] + 1
    except Exception:
        pass
    print("Kommando-lyssnare ig\u00e5ng (/stats).")
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{base}/getUpdates", params=params, timeout=40).json()
            if not r.get("ok"):
                time.sleep(5); continue
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post") or {}
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                if not Config.STATS_ANY_CHAT and chat_id != str(Config.CHAT_ID):
                    continue
                cmd = text.split()[0].lower().split("@")[0]
                if cmd in ("/stats", "/statistik"):
                    send_telegram(stats_text(), chat_id)
                elif cmd == "/open":
                    send_telegram(_open_text(), chat_id)
                elif cmd in ("/start", "/help"):
                    send_telegram("\U0001F916 <b>NASDAQ ROBBER</b>\nKommandon:\n"
                                  "/stats \u2013 tr\u00e4ffprocent &amp; R\n/open \u2013 \u00f6ppna trades", chat_id)
        except Exception as e:
            print("command-listener fel:", e)
            time.sleep(5)



# =====================================================================
#  GRABIT ORB 15/5  ·  Opening Range Breakout på NQ
#  1) Första 15-min-candeln efter US-öppning (15:30-15:45 svensk tid)
#     sätter range (high/low).
#  2) Nästa 5-min-candle (15:45-15:50): STÄNGER den över high -> LONG,
#     under low -> SHORT. Bara stängning räknas, inte en spik igenom.
#  3) SL under/över breakout-candeln (eller range-kanten om den är
#     orimligt tajt). TP1 = 2R, TP2 = 3R.
#  Filter som höjer confidence: volym över snitt, EMA20/50 i riktningen.
#  Av/på + trösklar via env: ORB_ENABLED (std 1), ORB_MIN_CONF (std 60).
# =====================================================================
_ORB_STATE = {"day": "", "fired": False, "news_day": "", "news": None}

def _orb_news_near_open(now_se):
    """High-impact USD-makrohändelse inom ±45 min från US-öppning?
    Returnerar (True, titel) eller (False, ""). Fel -> (False, "") tyst."""
    today = now_se.strftime("%Y-%m-%d")
    if _ORB_STATE["news_day"] == today and _ORB_STATE["news"] is not None:
        return _ORB_STATE["news"]
    result = (False, "")
    try:
        import requests
        from api import _MACRO_URLS, _MACRO_HEADERS
        raw = None
        for url in _MACRO_URLS:
            try:
                r = requests.get(url, headers=_MACRO_HEADERS, timeout=8)
                if r.status_code == 200:
                    raw = r.json()
                    break
            except Exception:
                continue
        if raw:
            open_se = now_se.replace(hour=15, minute=30, second=0, microsecond=0)
            for e in raw:
                if str(e.get("impact")) != "High":
                    continue
                if str(e.get("country")) not in ("USD", "ALL"):
                    continue
                try:
                    t = datetime.fromisoformat(str(e.get("date")).replace("Z", "+00:00"))
                except Exception:
                    continue
                if t.tzinfo is None:
                    continue
                if abs((t - open_se).total_seconds()) <= 45 * 60:
                    result = (True, str(e.get("title") or "makrohändelse"))
                    break
    except Exception:
        pass
    _ORB_STATE["news_day"] = today
    _ORB_STATE["news"] = result
    return result

def _orb_check():
    if os.environ.get("ORB_ENABLED", "1") != "1":
        return
    from zoneinfo import ZoneInfo
    tz_se = ZoneInfo("Europe/Stockholm")
    now_se = datetime.now(tz_se)
    if now_se.weekday() > 4:
        return
    # Fönstret: breakout-candeln stänger 15:50 -> kolla 15:50-16:20
    minutes = now_se.hour * 60 + now_se.minute
    if not (15 * 60 + 50 <= minutes <= 16 * 60 + 20):
        return
    today = now_se.strftime("%Y-%m-%d")
    if _ORB_STATE["day"] == today and _ORB_STATE["fired"]:
        return
    _ORB_STATE["day"] = today

    try:
        import yfinance as yf
        df = yf.download("NQ=F", period="1d", interval="5m", progress=False, auto_adjust=False)
        if df is None or len(df) < 4:
            STATUS["orb_last"] = f"{today}: ingen/för lite 5m-data ({0 if df is None else len(df)} barer)"
            return
        try:
            import pandas as _pd
            if isinstance(df.columns, _pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
        except Exception:
            pass
        tz_ny = ZoneInfo("America/New_York")
        try:
            idx = df.index.tz_convert(tz_ny)
        except TypeError:
            idx = df.index.tz_localize("UTC").tz_convert(tz_ny)
        d0 = now_se.astimezone(tz_ny).date()

        def bar_at(h, m):
            for i, t in enumerate(idx):
                if t.date() == d0 and t.hour == h and t.minute == m:
                    return df.iloc[i]
            return None

        b1 = bar_at(9, 30); b2 = bar_at(9, 35); b3 = bar_at(9, 40)
        bo = bar_at(9, 45)
        if b1 is None or b2 is None or b3 is None or bo is None:
            STATUS["orb_last"] = (f"{today}: väntar på barer "
                                  f"(9:30 {'ok' if b1 is not None else '-'}, 9:35 {'ok' if b2 is not None else '-'}, "
                                  f"9:40 {'ok' if b3 is not None else '-'}, 9:45 {'ok' if bo is not None else '-'})")
            return  # baren inte klar än — provas igen nästa cykel
        or_high = max(float(b1["High"]), float(b2["High"]), float(b3["High"]))
        or_low = min(float(b1["Low"]), float(b2["Low"]), float(b3["Low"]))
        c = float(bo["Close"])

        if c > or_high:
            side = "LONG"
        elif c < or_low:
            side = "SHORT"
        else:
            _ORB_STATE["fired"] = True   # ingen breakout idag — klart för idag
            STATUS["orb_last"] = f"{today}: ingen breakout (range {or_low:.1f}-{or_high:.1f}, stängning {c:.1f})"
            print("[orb] ingen breakout idag")
            return

        # SL: breakout-candelns extrem; range-kanten om den är orimligt tajt (<0.05%)
        if side == "LONG":
            sl = float(bo["Low"])
            if c - sl < c * 0.0005:
                sl = or_low
            risk = c - sl
            tp1, tp2 = c + 2 * risk, c + 3 * risk
        else:
            sl = float(bo["High"])
            if sl - c < c * 0.0005:
                sl = or_high
            risk = sl - c
            tp1, tp2 = c - 2 * risk, c - 3 * risk
        if risk <= 0:
            return

        # Filter -> confidence
        conf = 60
        try:
            vol_avg = float(df["Volume"].tail(30).mean())
            if float(bo["Volume"]) > vol_avg:
                conf += 15
        except Exception:
            pass
        try:
            ema20 = float(df["Close"].ewm(span=20).mean().iloc[-1])
            ema50 = float(df["Close"].ewm(span=50).mean().iloc[-1])
            if (side == "LONG" and ema20 > ema50) or (side == "SHORT" and ema20 < ema50):
                conf += 15
        except Exception:
            pass
        news_risk, news_title = _orb_news_near_open(now_se)
        if news_risk:
            conf -= 20     # stor nyhet vid öppning -> breakouts blir opålitliga
        conf = min(conf, 93)
        if conf < int(os.environ.get("ORB_MIN_CONF", "60")):
            _ORB_STATE["fired"] = True
            STATUS["orb_last"] = f"{today}: {side} men conf {conf} under tröskeln"
            return

        _ORB_STATE["fired"] = True
        STATUS["orb_last"] = f"{today}: {side} conf {conf} entry {c:.1f}"
        f = lambda v: f"{v:,.1f}".replace(",", " ")
        send_telegram(
            "\U0001F680 <b>OPENING RANGE BREAKOUT</b> \u00b7 GRABIT ORB 15/5\n"
            f"\U0001F552 Range: 15:30\u201315:45 ({f(or_low)}\u2013{f(or_high)})\n"
            f"\U0001F4C8 Breakout: <b>{side}</b> \u2013 US100\n"
            f"\U0001F3AF Entry: <b>{f(c)}</b>\n"
            f"\U0001F6D1 Stop: {f(sl)}\n"
            f"\U0001F4B0 TP1: {f(tp1)}  \u00b7  TP2: {f(tp2)}\n"
            f"\U0001F916 Confidence: <b>{conf}/100</b>"
            + (f"\n\u26A0\uFE0F Makronyhet nära öppning: {news_title}" if news_risk else ""))
        try:
            import push_notify as PN
            pil = "\U0001F4C8" if side == "LONG" else "\U0001F4C9"
            PN.send_all(
                f"\U0001F680 ORB 15/5 \u00b7 {side} US100 \u00b7 {conf}/100",
                (f"OPENING RANGE BREAKOUT {pil}\n"
                 f"Entry: {f(c)} | SL: {f(sl)} | TP1: {f(tp1)}"),
                url="/", tag="grabit-orb")
        except Exception as e:
            print("[orb] push-fel:", e)
        print(f"[orb] {side} skickad: entry {c:.1f} sl {sl:.1f} tp1 {tp1:.1f} conf {conf}")
    except Exception as e:
        STATUS["orb_last"] = f"{_ORB_STATE.get('day','?')}: FEL {type(e).__name__}: {str(e)[:140]}"
        print("[orb] fel:", e)


def run_loop():
    print("=== NASDAQ ROBBER startad ===")
    print(f"Tickers: {Config.TICKERS}  |  {Config.MTF_TIMEFRAME} setup / {Config.HTF_TIMEFRAME} bias  |  data=yfinance (futures)")
    _persist = "PERSISTENT" if Config.DATA_DIR not in (".", "") else "FLYKTIG (nollställs vid omstart — sätt DATA_DIR=/var/data + Render Disk)"
    print(f"State/journal: {Config.DATA_DIR}  [{_persist}]")
    # Startup-ping i Telegram ar avstangd som standard — varje deploy startar
    # om servern och spammade kanalen med "startad". Satt ROBBER_STARTUP_PING=1
    # i Render om du vill ha tillbaka den.
    if os.environ.get("ROBBER_STARTUP_PING") == "1":
        names = ", ".join(Config.NAMES.get(t, t) for t in Config.TICKERS)
        ok = send_telegram(
            "\u2705 <b>NASDAQ ROBBER startad</b>\n"
            f"Bevakar: {names}\n"
            f"Setup {Config.MTF_TIMEFRAME} / bias {Config.HTF_TIMEFRAME} \u00b7 larm vid \u2265{Config.MIN_SCORE}/7\n"
            "Skannar 24/7 \u00b7 trade-larm 06:00\u201322:00 m\u00e5n\u2013fre \u00b7 \U0001F305 morgonbrief 06:00\n"
            f"+ Polymarket-monitor: larm vid trades \u2265 ${int(POLY_MIN_USD):,}"
        )
        print(f"Startup-ping till Telegram: {'OK' if ok else 'MISSLYCKADES (kolla TOKEN/CHAT_ID)'}")
    import threading as _th
    _th.Thread(target=command_listener, daemon=True, name="cmd-listener").start()
    from zoneinfo import ZoneInfo
    STATUS["started"] = datetime.now(ZoneInfo(Config.LOCAL_TZ)).isoformat(timespec="seconds")
    last_win = None
    while True:
        try:
            win = in_session()                 # 06-22 mån-fre = handelsfönster
            STATUS["in_session"] = win
            if win and last_win is False:       # precis öppnat -> morgonbrief
                try:
                    morning_brief()
                except Exception as e:
                    print("morgonbrief-fel:", e)
                try:
                    send_telegram(stats_text())
                except Exception as e:
                    print("stats-fel:", e)
            last_win = win
            scan_once()                         # skannar 24/7 (håller koll på natten/Asien)
            _orb_check()                        # GRABIT ORB 15/5 vid US-öppningen
            poly_scan()                         # Polymarket 24/7
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
