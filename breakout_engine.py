# ============================================================
#  MoneyGrab — TRADE ENGINE (breakout + entry + exit + swing)
#  Tar en daglig OHLCV-DataFrame och returnerar:
#   - breakout_score 0-100 (struktur, volym, momentum, trend, rel.styrka, squeeze)
#   - entry-nivåer (aggressive/confirmed/retest) + stop + targets + RR
#   - exit_risk 0-100 (momentum decay, blowoff, volymklimax, lägre toppar)
#   - swing_score 0-100
#   - confidence % + förklaring (+/- faktorer)
#   - fake_breakout-risk
#
#  ÄRLIGA GRÄNSER:
#   - Daglig + vecko-timeframe (ingen intraday/daytrade på gratis-data)
#   - Poängen är heuristiska, INTE backtestade sannolikheter
#   - Ingen news/sentiment/float här (separat lager)
# ============================================================

import numpy as np
import pandas as pd


def _ema(s, n): return s.ewm(span=n, adjust=False).mean()


def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def _atr(df, p=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def _clamp(x, lo, hi): return max(lo, min(hi, x))


def evaluate(df, bench_close=None):
    """df: daglig OHLCV. bench_close: index-Close för relativ styrka (valfritt)."""
    if df is None or len(df) < 60:
        return None
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    last = float(close.iloc[-1])
    atr = float(_atr(df).iloc[-1])
    atr_pct = atr / last * 100 if last else 0

    ema20 = float(_ema(close, 20).iloc[-1])
    ema50 = float(_ema(close, 50).iloc[-1])
    ema200 = float(_ema(close, 200).iloc[-1]) if len(close) >= 200 else float(_ema(close, len(close) - 1).iloc[-1])
    rsi_ser = _rsi(close)
    rsi = float(rsi_ser.iloc[-1])
    rsi_prev = float(rsi_ser.iloc[-6]) if len(close) > 6 else rsi

    # MACD
    macd = _ema(close, 12) - _ema(close, 26)
    signal = _ema(macd, 9)
    hist = macd - signal
    macd_slope = float(hist.iloc[-1] - hist.iloc[-5]) if len(hist) > 5 else 0

    ret5 = (last / float(close.iloc[-6]) - 1) * 100 if len(close) > 6 else 0
    ret20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0
    roc10 = (last / float(close.iloc[-11]) - 1) * 100 if len(close) > 11 else 0

    resistance = float(high.tail(20).max())
    pfh = (last / float(close.tail(252).max()) - 1) * 100
    tight = float(close.tail(12).std() / close.tail(12).mean() * 100) if len(close) >= 12 else 99
    low10 = float(low.tail(10).min())
    higher_lows = float(low.tail(5).min()) > float(low.tail(15).min())

    avgvol = float(vol.tail(20).mean())
    relvol = float(vol.iloc[-1] / avgvol) if avgvol else 1
    vol_dryup = float(vol.tail(5).mean()) < float(vol.tail(20).mean()) * 0.85

    # Bollinger/ATR squeeze
    bb_w = float(close.tail(20).std() / close.tail(20).mean() * 100) if len(close) >= 20 else 99
    bb_hist = (close.rolling(20).std() / close.rolling(20).mean() * 100).tail(120)
    squeeze_pct = float((bb_hist <= bb_w).mean() * 100) if len(bb_hist) else 50  # lägre = tightare

    # vecko-trend (HTF)
    try:
        wk = close.resample("W").last().dropna()
        wk_trend = float(wk.iloc[-1]) > float(_ema(wk, 10).iloc[-1]) if len(wk) >= 10 else last > ema50
    except Exception:
        wk_trend = last > ema50

    expl = []

    # ---------- STRUCTURE (25) ----------
    struc = 0
    if tight < 3: struc += 10; expl.append(("Tight konsolidering", +10))
    elif tight < 5: struc += 6; expl.append(("Hyfsat tight range", +6))
    if -8 <= pfh <= 1: struc += 9; expl.append(("Nära 52v-topp (motstånd)", +9))
    elif -15 <= pfh < -8: struc += 4; expl.append(("Inom 15% från topp", +4))
    if higher_lows: struc += 6; expl.append(("Stigande bottnar", +6))
    struc = _clamp(struc, 0, 25)

    # ---------- VOLUME (20) ----------
    v = 0
    if relvol >= 2: v += 9; expl.append((f"Hög relativ volym {relvol:.1f}x", +9))
    elif relvol >= 1.3: v += 6; expl.append((f"Stigande volym {relvol:.1f}x", +6))
    elif relvol < 1.0: expl.append((f"Svag volym {relvol:.1f}x", 0))
    if vol_dryup: v += 5; expl.append(("Volym-dry-up före utbrott", +5))
    if last > float(close.iloc[-2]) and relvol >= 1.2: v += 6; expl.append(("Expansionsdag på volym", +6))
    v = _clamp(v, 0, 20)

    # ---------- MOMENTUM (15) ----------
    mo = 0
    if rsi > rsi_prev: mo += 5; expl.append(("RSI accelererar", +5))
    if 50 <= rsi <= 70: mo += 4; expl.append(("RSI i frisk styrkezon", +4))
    elif rsi > 78: mo -= 4; expl.append(("RSI överköpt", -4))
    if macd_slope > 0: mo += 4; expl.append(("MACD stiger", +4))
    if roc10 > 5: mo += 3; expl.append(("Positiv ROC", +3))
    mo = _clamp(mo, 0, 15)

    # ---------- TREND ALIGNMENT (20) — daglig + vecka ----------
    tr = 0
    if last > ema20: tr += 4
    if ema20 > ema50: tr += 4
    if last > ema200: tr += 5
    if ema50 > ema200: tr += 3
    if wk_trend: tr += 4; expl.append(("Veckotrend bullish", +4))
    tr = _clamp(tr, 0, 20)
    if last > ema50 and last > ema200 and wk_trend:
        expl.append(("Daglig + vecka i linje", +0))

    # ---------- RELATIVE STRENGTH (10) ----------
    rs = 5
    if bench_close is not None and len(bench_close) > 21:
        bret = (float(bench_close.iloc[-1]) / float(bench_close.iloc[-21]) - 1) * 100
        diff = ret20 - bret
        rs = _clamp(5 + diff / 3, 0, 10)
        if diff > 5: expl.append(("Slår index tydligt", +round(rs - 5)))
        elif diff < -5: expl.append(("Sämre än index", -round(5 - rs)))
    rs = _clamp(rs, 0, 10)

    # ---------- VOLATILITY SQUEEZE (10) ----------
    sq = 0
    if squeeze_pct <= 20: sq = 10; expl.append(("Volatility squeeze (coiled)", +10))
    elif squeeze_pct <= 40: sq = 6; expl.append(("Sammandragen volatilitet", +6))
    sq = _clamp(sq, 0, 10)

    breakout = struc + v + mo + tr + rs + sq

    # ---------- FAKE-BREAKOUT-RISK ----------
    fake = 0
    if (-8 <= pfh <= 1) and relvol < 1.2: fake += 25; expl.append(("Utbrott utan volym", -25))
    if not wk_trend: fake += 18; expl.append(("Svag HTF-trend", -18))
    if rsi > 80: fake += 12
    if last < ema200: fake += 10
    fake_level = "HÖG" if fake >= 35 else ("MEDEL" if fake >= 18 else "LÅG")
    breakout = _clamp(breakout - 0.4 * fake, 0, 100)
    confidence = int(_clamp(breakout - 0.3 * fake, 5, 95))

    # ---------- SETUP-NAMN ----------
    if squeeze_pct <= 20 and tight < 4:
        setup = "Volatility squeeze"
    elif (-8 <= pfh <= 1) and tight < 5:
        setup = "Bas/flagga under motstånd"
    elif higher_lows and last > ema50:
        setup = "Stigande bottnar"
    elif last > ema50 and last < ema200:
        setup = "Tidig vändning"
    else:
        setup = "Ingen tydlig setup"

    # ---------- ENTRY ENGINE ----------
    stop = round(min(low10, last - 1.8 * atr), 2)
    aggressive = round(last, 2)
    confirmed = round(resistance * 1.01, 2)
    retest = round(max(ema20, resistance * 0.99), 2)
    t1 = round(last + 2.0 * atr, 2)
    t2 = round(last + 3.5 * atr, 2)
    risk = last - stop
    rr = round((t1 - last) / risk, 1) if risk > 0 else 0

    # ---------- EXIT ENGINE ----------
    ex = 0
    ereas = []
    move_atr = (last - float(close.iloc[-6])) / atr if atr else 0
    if move_atr >= 5: ex += 30; ereas.append("parabolisk (>5 ATR på en vecka)")
    elif move_atr >= 3: ex += 18; ereas.append("utsträckt rörelse")
    # RSI-divergens: pris högre men RSI lägre
    if len(close) > 11 and last > float(close.iloc[-11]) and rsi < float(rsi_ser.iloc[-11]):
        ex += 22; ereas.append("RSI-divergens")
    if macd_slope < 0: ex += 12; ereas.append("MACD vänder ner")
    # övre vek (blowoff): stor wick idag
    rng = float(high.iloc[-1] - low.iloc[-1])
    wick = float(high.iloc[-1] - close.iloc[-1])
    if rng > 0 and wick / rng > 0.5 and ret5 > 10: ex += 16; ereas.append("lång övre vek")
    # lägre toppar senaste 3 dagar
    if len(high) >= 3 and float(high.iloc[-1]) < float(high.iloc[-2]) < float(high.iloc[-3]):
        ex += 10; ereas.append("lägre dagstoppar")
    if rsi > 82: ex += 10; ereas.append("kraftigt överköpt")
    exit_risk = int(_clamp(ex, 0, 100))

    if exit_risk >= 65:
        action = "Ta hem del av vinst, dra upp stop hårt"
        trail = round(last - 1.3 * atr, 2)
    elif exit_risk >= 40:
        action = "Dra upp stop (ATR-trail)"
        trail = round(last - 2.0 * atr, 2)
    else:
        action = "Behåll, normal ATR-trail"
        trail = round(last - 2.8 * atr, 2)

    # ---------- SWING SCORE ----------
    sw = 0
    if last > ema50: sw += 25
    if wk_trend: sw += 25
    if 40 <= rsi <= 60 and last > ema50: sw += 20      # frisk pullback i trend
    elif 50 <= rsi <= 70: sw += 12
    if higher_lows: sw += 15
    if rsi < 80: sw += 15
    swing = int(_clamp(sw, 0, 100))

    # sortera förklaring: störst påverkan först
    expl = [e for e in expl if e[1] != 0]
    expl.sort(key=lambda x: abs(x[1]), reverse=True)

    return {
        "breakout_score": int(round(breakout)),
        "components": {"Struktur": round(struc), "Volym": round(v), "Momentum": round(mo),
                       "Trend": round(tr), "Rel.styrka": round(rs), "Squeeze": round(sq)},
        "setup": setup, "confidence": confidence,
        "fake_risk": fake_level,
        "entry": {"aggressive": aggressive, "confirmed": confirmed, "retest": retest,
                  "stop": stop, "t1": t1, "t2": t2, "rr": rr},
        "exit": {"risk": exit_risk, "action": action, "trail": trail,
                 "reasons": ereas},
        "swing_score": swing,
        "explain": expl,
        "atr_pct": round(atr_pct, 1),
    }
