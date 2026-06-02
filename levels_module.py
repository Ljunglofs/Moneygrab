# ============================================================
#  LEVELS  —  drop-in module for MoneyGrab
#  Replikerar kärnan i RayAlgo SMC: BOS, Order Blocks, HH/HL,
#  ATR-baserade mål och en samlad SETUP-kvalitet (A/B/C).
#  Körs på OHLCV-historik (yfinance df) — inga externa beroenden.
#
#  ANVÄNDNING (sok_module.analyze anropar compute_levels och
#  mergar in resultatet i analysobjektet):
#
#       from levels_module import compute_levels
#       lv = compute_levels(df)
#       a.update(lv)
#
#  Returnerar bl.a:
#       bos            "BULLISH" / "BEARISH" / "INGEN"
#       structure      "HH/HL" / "LH/LL" / "BLANDAD"
#       ob_support     närmaste bullish OB-zon under pris (float|None)
#       ob_resist      närmaste bearish OB-zon över pris (float|None)
#       atr_up_1       pris + 1.0 ATR
#       atr_up_05      pris + 0.5 ATR
#       atr_dn_05      pris - 0.5 ATR
#       atr_dn_1       pris - 1.0 ATR
#       setup_grade    "A" / "B" / "C" / "D"
#       setup_score    0–100 (intern, för viktning)
#       levels_note    kort klartext-rad
# ============================================================

import numpy as np
import pandas as pd


# ----------------------------------------------------------
#  ATR (samma logik som sok_module för konsekvens)
# ----------------------------------------------------------
def _atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ----------------------------------------------------------
#  SWING-PIVOTS  —  fractal-baserade (vänster/höger fönster)
#  Returnerar index-listor för swing highs och swing lows.
# ----------------------------------------------------------
def _pivots(df, left=2, right=2):
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)
    piv_hi, piv_lo = [], []
    for i in range(left, n - right):
        win_h = highs[i - left:i + right + 1]
        win_l = lows[i - left:i + right + 1]
        if highs[i] == win_h.max() and (win_h == highs[i]).sum() == 1:
            piv_hi.append(i)
        if lows[i] == win_l.min() and (win_l == lows[i]).sum() == 1:
            piv_lo.append(i)
    return piv_hi, piv_lo


# ----------------------------------------------------------
#  STRUKTUR  —  HH/HL vs LH/LL utifrån de senaste pivoterna
# ----------------------------------------------------------
def _structure(df, piv_hi, piv_lo):
    highs = df["High"].values
    lows = df["Low"].values
    last_highs = [highs[i] for i in piv_hi[-2:]]
    last_lows = [lows[i] for i in piv_lo[-2:]]

    hh = len(last_highs) == 2 and last_highs[-1] > last_highs[-2]
    hl = len(last_lows) == 2 and last_lows[-1] > last_lows[-2]
    lh = len(last_highs) == 2 and last_highs[-1] < last_highs[-2]
    ll = len(last_lows) == 2 and last_lows[-1] < last_lows[-2]

    if hh and hl:
        return "HH/HL"
    if lh and ll:
        return "LH/LL"
    return "BLANDAD"


# ----------------------------------------------------------
#  BOS  —  Break of Structure
#  Bullish: close bryter senaste swing high.
#  Bearish: close bryter senaste swing low.
# ----------------------------------------------------------
def _bos(df, piv_hi, piv_lo, lookback=20):
    close = df["Close"].values
    last = close[-1]
    n = len(close)

    recent_hi = [i for i in piv_hi if i >= n - lookback - 1 and i < n - 1]
    recent_lo = [i for i in piv_lo if i >= n - lookback - 1 and i < n - 1]

    last_swing_high = df["High"].values[recent_hi[-1]] if recent_hi else None
    last_swing_low = df["Low"].values[recent_lo[-1]] if recent_lo else None

    bos = "INGEN"
    bos_level = None
    if last_swing_high is not None and last > last_swing_high:
        bos = "BULLISH"
        bos_level = round(float(last_swing_high), 2)
    elif last_swing_low is not None and last < last_swing_low:
        bos = "BEARISH"
        bos_level = round(float(last_swing_low), 2)
    return bos, bos_level


# ----------------------------------------------------------
#  ORDER BLOCKS  —  förenklad SMC-OB
#  Bullish OB: sista nedstängande candle innan en kraftig
#  uppåtrörelse (impuls). Bearish OB: tvärtom.
#  Vi tar zonen [low, high] för den candlen.
# ----------------------------------------------------------
def _order_blocks(df, atr, lookback=60, impulse_mult=1.2):
    o = df["Open"].values
    c = df["Close"].values
    h = df["High"].values
    l = df["Low"].values
    n = len(df)
    a = float(atr) if atr and not np.isnan(atr) else (df["Close"].iloc[-1] * 0.02)

    bull_obs, bear_obs = [], []
    start = max(1, n - lookback)
    for i in range(start, n - 1):
        move = c[i + 1] - c[i]
        # bullish OB: röd candle (c<o) följd av stark grön impuls upp
        if c[i] < o[i] and move > impulse_mult * a:
            bull_obs.append((float(l[i]), float(h[i])))
        # bearish OB: grön candle (c>o) följd av stark röd impuls ner
        if c[i] > o[i] and -move > impulse_mult * a:
            bear_obs.append((float(l[i]), float(h[i])))
    return bull_obs, bear_obs


def _nearest_support(bull_obs, price):
    """Närmaste bullish OB-zon vars topp ligger under/vid pris."""
    cands = [zone for zone in bull_obs if zone[1] <= price * 1.005]
    if not cands:
        return None
    # närmast under pris = högsta toppen bland kandidaterna
    best = max(cands, key=lambda z: z[1])
    return round((best[0] + best[1]) / 2, 2)


def _nearest_resist(bear_obs, price):
    """Närmaste bearish OB-zon vars botten ligger över/vid pris."""
    cands = [zone for zone in bear_obs if zone[0] >= price * 0.995]
    if not cands:
        return None
    best = min(cands, key=lambda z: z[0])
    return round((best[0] + best[1]) / 2, 2)


# ----------------------------------------------------------
#  HUVUDFUNKTION
# ----------------------------------------------------------
def compute_levels(df):
    out = {
        "bos": "INGEN", "bos_level": None, "structure": "BLANDAD",
        "ob_support": None, "ob_resist": None,
        "atr_up_1": None, "atr_up_05": None, "atr_dn_05": None, "atr_dn_1": None,
        "setup_grade": "C", "setup_score": 0, "levels_note": "",
    }
    if df is None or len(df) < 30:
        return out

    needed = {"Open", "High", "Low", "Close"}
    if not needed.issubset(set(df.columns)):
        return out

    close = df["Close"]
    last = float(close.iloc[-1])
    atr = _atr(df).iloc[-1]
    atr = float(atr) if atr and not np.isnan(atr) else last * 0.02

    piv_hi, piv_lo = _pivots(df, left=2, right=2)
    structure = _structure(df, piv_hi, piv_lo)
    bos, bos_level = _bos(df, piv_hi, piv_lo)
    bull_obs, bear_obs = _order_blocks(df, atr)

    ob_support = _nearest_support(bull_obs, last)
    ob_resist = _nearest_resist(bear_obs, last)

    out.update({
        "bos": bos,
        "bos_level": bos_level,
        "structure": structure,
        "ob_support": ob_support,
        "ob_resist": ob_resist,
        "atr_up_1": round(last + atr, 2),
        "atr_up_05": round(last + atr * 0.5, 2),
        "atr_dn_05": round(last - atr * 0.5, 2),
        "atr_dn_1": round(last - atr, 2),
    })

    # ---------- SETUP-KVALITET (0–100 → A/B/C/D) ----------
    sc = 0
    # struktur intakt = trendföljande
    if structure == "HH/HL":
        sc += 30
    elif structure == "BLANDAD":
        sc += 12
    # BOS bekräftad uppåt = bränslet i en breakout
    if bos == "BULLISH":
        sc += 30
    elif bos == "INGEN":
        sc += 8
    # stöd nära (OB under pris ger tydlig stop-nivå + R/R)
    if ob_support is not None:
        dist = (last - ob_support) / last * 100
        if 0 < dist <= 6:
            sc += 22          # tight stöd = bra R/R
        elif 6 < dist <= 12:
            sc += 12
        else:
            sc += 5
    # tak: om motstånd är långt borta finns plats att klättra
    if ob_resist is not None:
        head = (ob_resist - last) / last * 100
        if head >= 6:
            sc += 18
        elif head >= 3:
            sc += 10
        else:
            sc += 3
    else:
        sc += 14              # inget OB-tak ovanför = clear sky

    sc = min(100, sc)
    grade = "A" if sc >= 75 else "B" if sc >= 55 else "C" if sc >= 35 else "D"

    # ---------- KLARTEXT ----------
    parts = []
    if bos == "BULLISH":
        parts.append("BOS↑ bekräftad")
    elif bos == "BEARISH":
        parts.append("BOS↓ (struktur bruten ner)")
    parts.append(f"struktur {structure}")
    if ob_support is not None:
        parts.append(f"stöd {ob_support}")
    if ob_resist is not None:
        parts.append(f"motstånd {ob_resist}")
    parts.append(f"mål +1 ATR {out['atr_up_1']}")

    out["setup_score"] = sc
    out["setup_grade"] = grade
    out["levels_note"] = " · ".join(parts)
    return out


# ----------------------------------------------------------
#  Hur mycket levels får påverka huvudpoängen i analyze().
#  Hålls litet så att din befintliga score-balans inte rubbas:
#  −6 … +8 poäng beroende på setup-kvalitet och BOS.
# ----------------------------------------------------------
def levels_score_bonus(lv):
    bonus = 0
    g = lv.get("setup_grade", "C")
    bonus += {"A": 6, "B": 3, "C": 0, "D": -3}.get(g, 0)
    if lv.get("bos") == "BULLISH":
        bonus += 2
    elif lv.get("bos") == "BEARISH":
        bonus -= 3
    return bonus
