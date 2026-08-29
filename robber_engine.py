#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROBBER ENGINE v2 — "Quant Core"
================================
Ombyggd signalmotor för NQ/US100, inspirerad av MNQ Quant Trader-arkitekturen
(MarketState → viktad scoring → EN tröskel → paper-ledger med expectancy) men
byggd på data vi FAKTISKT har (5m-candles via yfinance — ingen tick/Level 2).

Skillnader mot v1 (den gamla dubbla score7+confidence-motorn):
  · EN sammanhängande 0–100-score med namngivna vikter — inget parallellt
    7-poängssystem som kunde säga emot confidence.
  · MarketState byggs FÖRST (session, VWAP, sessionsnivåer, delta-proxy,
    struktur) — sen poängsätts den. Läsbart, testbart, utbyggbart.
  · Sessionsnivåer (Asien/London high–low) + sweep-reclaim som setup —
    Quant Trader-idén, implementerad på riktig candledata.
  · "Delta" är en PROXY: CLV×volym ackumulerat (var candlen stänger i sitt
    range, viktat med volym). Det är INTE orderflow — vi kallar det aldrig
    det. Riktig delta kräver betalt feed.
  · Hårda gates behålls från lärdomarna: aldrig counter-trend, aldrig fel
    sida om VWAP, aldrig i natt-chop, aldrig utan volym.

Motorn är ren (inga nätverksanrop, ingen state) → enhetstestbar.
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ------------------------------------------------------------------
#  Parametrar (kan överstyras av anroparen via build_state/analyze)
# ------------------------------------------------------------------
MIN_CONFIDENCE = 70          # EN tröskel — under = ingen signal
WATCH_LEVEL    = 55          # 55–69 = "bevaka" (skickas inte som trade)
TP_R           = (2.0, 2.67)
SL_ATR_FLOOR   = 1.0         # SL aldrig tightare än så  (lärdom: 3xSL-dagen)
SL_ATR_CAP     = 1.8         # ...och aldrig längre bort än så
SWEEP_LOOKBACK = 6           # barer bakåt för sweep-reclaim
DELTA_BARS     = 12          # fönster för delta-proxyn

WEIGHTS = {                  # summa 100 — EN skala, inga sidosystem
    "vwap_slope": 13,        # VWAP lutar åt håll­et (flack VWAP ger 0)
    "delta":      13,        # CLV×volym-proxy trycker åt hållet
    "structure":  14,        # HH/HL (long) resp. LL/LH (short) på swingar
    "sweep":      18,        # sessionsnivå sveptes och återtogs — bästa signalen
    "volume":     14,        # rel_vol ≥ 1.3 (9) + spike ≥ 1.8 (5)
    "htf_bias":    8,        # 1h-trenden håller med
    "rsi":        10,        # momentum-zon (50–70 long / 30–50 short)
    "poc":        10,        # rätt sida om dagens volym-nod: long ÖVER POC, short UNDER
}


# ------------------------------------------------------------------
#  MarketState — marknadsbilden, byggd EN gång per bar
# ------------------------------------------------------------------
@dataclass
class MarketState:
    price: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    session: str = "OFF"             # ASIA / LONDON / NY / OFF
    bias: str = "NEUTRAL"            # 1h: LONG / SHORT / NEUTRAL

    vwap: Optional[float] = None
    vwap_slope_pct: float = 0.0      # % per ~30 min
    above_vwap: bool = False

    poc: Optional[float] = None      # dagens volym-nod (Point of Control)
    above_poc: bool = False

    asia_high: Optional[float] = None
    asia_low: Optional[float] = None
    london_high: Optional[float] = None
    london_low: Optional[float] = None

    ema20: float = 0.0
    ema50: float = 0.0
    ema_sep_atr: float = 0.0         # |ema20-ema50| i ATR — chop-mått

    rel_vol: float = 0.0
    vol_spike: bool = False

    delta_proxy: float = 0.0         # -1..+1 (CLV×vol / vol, senaste DELTA_BARS)

    hh_hl: bool = False
    ll_lh: bool = False

    sweep_long: Optional[str] = None   # "ASIA_LOW"/"LONDON_LOW" om svept+återtagen
    sweep_short: Optional[str] = None
    sweep_extreme_lo: Optional[float] = None   # sweep-botten (SL-ankare, long)
    sweep_extreme_hi: Optional[float] = None

    rsi: float = 50.0
    block: str = ""                  # sätts av gates — varför inget läge finns
    near_conf: int = 0               # bästa läge som klarade gates men föll på tröskeln
    near_dir: str = ""
    near_reasons: list = field(default_factory=list)


@dataclass
class Setup:
    direction: str
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    confidence: int
    setup_type: str
    reasons: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)
    session: str = ""


# ------------------------------------------------------------------
#  Byggstenar
# ------------------------------------------------------------------
def _session_of(hour_min: float) -> str:
    """Svensk tid (timmar med decimal). Natt 22–02 handlas ALDRIG."""
    if 2.0 <= hour_min < 9.0:
        return "ASIA"
    if 9.0 <= hour_min < 15.5:
        return "LONDON"
    if 15.5 <= hour_min < 22.0:
        return "NY"
    return "OFF"


def _session_range(df: pd.DataFrame, hours_se, lo_h: float, hi_h: float):
    """High/low för dagens barer i ett SE-timfönster."""
    try:
        m = (hours_se >= lo_h) & (hours_se < hi_h)
        if not m.any():
            return None, None
        return float(df["High"][m].max()), float(df["Low"][m].min())
    except Exception:
        return None, None


def _anchored_vwap(df: pd.DataFrame, hours_se, session: str):
    """NY: ankrad i RTH-öppningen (15:30 SE). Övrigt: dagens start (00:00 SE
    ≈ Globex-dygnet). Returnerar (vwap_serie_för_ankrade_barer, nu-värde)."""
    try:
        m = (hours_se >= 15.5) if session == "NY" else (hours_se >= 0.0)
        if not m.any() or int(m.sum()) < 4:
            return None, None
        H, L, C, V = (df["High"][m].to_numpy(float), df["Low"][m].to_numpy(float),
                      df["Close"][m].to_numpy(float), df["Volume"][m].to_numpy(float))
        V = V.copy(); V[V <= 0] = 1.0
        tp = (H + L + C) / 3.0
        vw = np.cumsum(tp * V) / np.cumsum(V)
        return vw, float(vw[-1])
    except Exception:
        return None, None


def _volume_profile_poc(df: pd.DataFrame, bins: int = 40) -> Optional[float]:
    """POC (Point of Control) — prisnivån med mest handlad volym. Varje bars
    volym fördelas jämnt över de prisbinnar som barens High–Low täcker.
    Approximation ur candledata (riktig profil kräver tick) — men det är
    riktiga volymer, inget påhittat."""
    try:
        H = df["High"].to_numpy(float)
        L = df["Low"].to_numpy(float)
        V = df["Volume"].to_numpy(float)
        lo, hi = float(np.min(L)), float(np.max(H))
        if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
            return None
        edges = np.linspace(lo, hi, bins + 1)
        vol = np.zeros(bins)
        for h, l, v in zip(H, L, V):
            if v <= 0:
                v = 1.0
            i0 = max(0, min(bins - 1, int(np.searchsorted(edges, l, side="right") - 1)))
            i1 = max(i0 + 1, min(bins, int(np.searchsorted(edges, h, side="left"))))
            vol[i0:i1] += v / (i1 - i0)
        k = int(np.argmax(vol))
        return float((edges[k] + edges[k + 1]) / 2.0)
    except Exception:
        return None


def _delta_proxy(df: pd.DataFrame, bars: int = DELTA_BARS) -> float:
    """CLV×volym normerat till -1..+1. PROXY för köp/säljtryck — inte orderflow."""
    try:
        H = df["High"].to_numpy(float)[-bars:]
        L = df["Low"].to_numpy(float)[-bars:]
        C = df["Close"].to_numpy(float)[-bars:]
        V = df["Volume"].to_numpy(float)[-bars:]
        rng = np.maximum(H - L, 1e-9)
        clv = ((C - L) - (H - C)) / rng            # -1..+1 per bar
        tot = float(V.sum())
        return float((clv * V).sum() / tot) if tot > 0 else 0.0
    except Exception:
        return 0.0


def _swing_pivots(H, L, k: int = 2):
    ph, pl = [], []
    for i in range(k, len(H) - k):
        if H[i] >= max(H[i - k:i + k + 1]):
            ph.append(float(H[i]))
        if L[i] <= min(L[i - k:i + k + 1]):
            pl.append(float(L[i]))
    return ph, pl


def _sweep_reclaim(df: pd.DataFrame, level: Optional[float], side: str,
                   lookback: int = SWEEP_LOOKBACK):
    """Sveptes nivån och återtogs? LONG: någon av senaste barerna handlade UNDER
    nivån men senaste baren STÄNGER över den. Returnerar (bool, extrem)."""
    if level is None or len(df) < lookback + 1:
        return False, None
    H = df["High"].to_numpy(float)[-lookback:]
    L = df["Low"].to_numpy(float)[-lookback:]
    close = float(df["Close"].iloc[-1])
    if side == "LONG":
        pierced = L.min() < level
        return (bool(pierced and close > level), float(L.min()) if pierced else None)
    pierced = H.max() > level
    return (bool(pierced and close < level), float(H.max()) if pierced else None)


# ------------------------------------------------------------------
#  build_state — hela marknadsbilden på en gång
# ------------------------------------------------------------------
def build_state(df: pd.DataFrame, bias: str, now_local) -> MarketState:
    """df = 5m-barer med kolumnerna från add_indicators (ema20/ema50/rsi/
    rel_vol/atr). now_local = tz-medveten svensk tid."""
    st = MarketState()
    if df is None or len(df) < 30:
        st.block = "för lite data"
        return st

    cur = df.iloc[-1]
    st.price = float(cur.Close)
    st.atr = float(cur.atr) if not pd.isna(cur.atr) else 0.0
    st.atr_pct = st.atr / st.price if st.price else 0.0
    st.bias = bias or "NEUTRAL"
    st.rsi = float(cur.rsi) if not pd.isna(cur.rsi) else 50.0
    st.ema20 = float(cur.ema20) if not pd.isna(cur.ema20) else st.price
    st.ema50 = float(cur.ema50) if not pd.isna(cur.ema50) else st.price
    st.ema_sep_atr = abs(st.ema20 - st.ema50) / st.atr if st.atr > 0 else 0.0
    st.rel_vol = float(cur.rel_vol) if not pd.isna(cur.rel_vol) else 0.0
    st.vol_spike = st.rel_vol >= 1.8

    hm = now_local.hour + now_local.minute / 60.0
    st.session = _session_of(hm)

    # dagens barer i svensk tid
    try:
        idx_se = df.index.tz_convert("Europe/Stockholm")
    except Exception:
        idx_se = df.index
    try:
        today = now_local.date()
        m_today = pd.Series([t.date() == today for t in idx_se], index=df.index)
        d_today = df[m_today.values]
        hrs = pd.Series([t.hour + t.minute / 60.0 for t in idx_se[m_today.values]],
                        index=d_today.index)
    except Exception:
        d_today, hrs = df.iloc[0:0], pd.Series(dtype=float)

    if len(d_today) >= 2:
        st.asia_high, st.asia_low = _session_range(d_today, hrs, 2.0, 9.0)
        st.london_high, st.london_low = _session_range(d_today, hrs, 9.0, 15.5)
        vw_ser, st.vwap = _anchored_vwap(d_today, hrs, st.session)
        if vw_ser is not None and len(vw_ser) >= 7:
            st.vwap_slope_pct = (float(vw_ser[-1]) - float(vw_ser[-7])) / st.price * 100.0
    st.above_vwap = bool(st.vwap is not None and st.price > st.vwap)

    # Dagens volym-nod (POC). Tunn dag -> falla tillbaka på senaste ~RTH-längden.
    prof = d_today if len(d_today) >= 12 else df.tail(78)
    st.poc = _volume_profile_poc(prof)
    st.above_poc = bool(st.poc is not None and st.price > st.poc)

    st.delta_proxy = _delta_proxy(df)

    H = df["High"].to_numpy(float)[-40:]
    L = df["Low"].to_numpy(float)[-40:]
    ph, pl = _swing_pivots(H, L, 2)
    if len(ph) >= 2 and len(pl) >= 2:
        st.hh_hl = ph[-1] > ph[-2] and pl[-1] > pl[-2]
        st.ll_lh = ph[-1] < ph[-2] and pl[-1] < pl[-2]

    # sweep-reclaim mot sessionsnivåerna (bästa nivån vinner)
    for name, lvl in (("LONDON_LOW", st.london_low), ("ASIA_LOW", st.asia_low)):
        ok, ext = _sweep_reclaim(df, lvl, "LONG")
        if ok:
            st.sweep_long, st.sweep_extreme_lo = name, ext
            break
    for name, lvl in (("LONDON_HIGH", st.london_high), ("ASIA_HIGH", st.asia_high)):
        ok, ext = _sweep_reclaim(df, lvl, "SHORT")
        if ok:
            st.sweep_short, st.sweep_extreme_hi = name, ext
            break
    return st


# ------------------------------------------------------------------
#  Gates + scoring — EN skala
# ------------------------------------------------------------------
def _gates(st: MarketState, direction: str) -> str:
    """Returnerar blockskäl ('' = fri väg). Lärdomarna som hårda regler."""
    if st.session == "OFF":
        return "natt/chop-fönstret (22–02) handlas inte"
    if st.atr <= 0 or st.atr_pct < 0.0008:
        return "död marknad (ATR)"
    if st.bias in ("LONG", "SHORT") and direction != st.bias:
        return "counter-trend mot 1h-bias"
    if st.vwap is None:
        return "VWAP ej etablerad"
    if direction == "LONG" and not st.above_vwap:
        return "fel sida om VWAP"
    if direction == "SHORT" and st.above_vwap:
        return "fel sida om VWAP"
    if st.ema_sep_atr < 0.08:
        return "sidledes (EMA20≈EMA50)"
    if st.rel_vol < 1.0:
        return "låg volym"
    return ""


def _score(st: MarketState, direction: str):
    W = WEIGHTS
    s, reasons, groups = 0, [], {}

    slope_ok = (st.vwap_slope_pct >= 0.02) if direction == "LONG" \
        else (st.vwap_slope_pct <= -0.02)
    if slope_ok:
        s += W["vwap_slope"]; reasons.append("VWAP lutar %s (%.3f%%/30m)"
                                             % ("upp" if direction == "LONG" else "ner",
                                                st.vwap_slope_pct))
    groups["VWAP-lutning"] = slope_ok

    d_ok = (st.delta_proxy >= 0.15) if direction == "LONG" else (st.delta_proxy <= -0.15)
    if d_ok:
        s += W["delta"]; reasons.append("Köp/säljtryck-proxy %+.2f" % st.delta_proxy)
    groups["Tryck-proxy"] = d_ok

    struct_ok = st.hh_hl if direction == "LONG" else st.ll_lh
    if struct_ok:
        s += W["structure"]; reasons.append("Struktur %s"
                                            % ("HH/HL" if direction == "LONG" else "LL/LH"))
    groups["Struktur"] = struct_ok

    sweep = st.sweep_long if direction == "LONG" else st.sweep_short
    if sweep:
        s += W["sweep"]; reasons.append("Sweep-reclaim av %s" % sweep)
    groups["Sweep-reclaim"] = bool(sweep)

    # POC-regeln (volymprofil): long ÖVER noden, short UNDER — noden agerar
    # stöd resp. motstånd. Ingen POC-data => inga poäng, aldrig gissning.
    poc_ok = (st.poc is not None) and (st.above_poc if direction == "LONG"
                                       else not st.above_poc)
    if poc_ok:
        s += W["poc"]; reasons.append("%s POC (%.0f)"
                                      % ("Över" if direction == "LONG" else "Under", st.poc))
    groups["POC-sida"] = poc_ok

    vol_pts = 0
    if st.rel_vol >= 1.3:
        vol_pts += 9
    if st.vol_spike:
        vol_pts += 5
    if vol_pts:
        s += vol_pts; reasons.append("Volym %.1fx" % st.rel_vol)
    groups["Volym"] = vol_pts > 0

    bias_ok = st.bias == direction
    if bias_ok:
        s += W["htf_bias"]; reasons.append("1h-bias %s" % st.bias)
    groups["1h-bias"] = bias_ok

    rsi_ok = (50 <= st.rsi <= 70) if direction == "LONG" else (30 <= st.rsi <= 50)
    if rsi_ok:
        s += W["rsi"]; reasons.append("RSI-zon (%.0f)" % st.rsi)
    groups["RSI-zon"] = rsi_ok

    return s, reasons, groups


def _levels(st: MarketState, direction: str) -> tuple:
    """SL: sweep-extremen (bästa ankaret) buffrad, annars ATR — med golv+tak."""
    a = st.atr
    if direction == "LONG":
        anchor = st.sweep_extreme_lo
        sl = (anchor - 0.5 * a) if anchor is not None else (st.price - 1.2 * a)
        sl = max(sl, st.price - SL_ATR_CAP * a)
        sl = min(sl, st.price - SL_ATR_FLOOR * a)
        risk = st.price - sl
        return round(sl, 2), round(st.price + TP_R[0] * risk, 2), round(st.price + TP_R[1] * risk, 2)
    anchor = st.sweep_extreme_hi
    sl = (anchor + 0.5 * a) if anchor is not None else (st.price + 1.2 * a)
    sl = min(sl, st.price + SL_ATR_CAP * a)
    sl = max(sl, st.price + SL_ATR_FLOOR * a)
    risk = sl - st.price
    return round(sl, 2), round(st.price - TP_R[0] * risk, 2), round(st.price - TP_R[1] * risk, 2)


def analyze(st: MarketState, min_confidence: int = MIN_CONFIDENCE) -> Optional[Setup]:
    """Quant Trader-flödet: båda riktningarna poängsätts, bästa som klarar
    gates + tröskel vinner. En signal max — aldrig både long och short."""
    best = None
    for direction in ("LONG", "SHORT"):
        blk = _gates(st, direction)
        if blk:
            if not st.block:
                st.block = "%s: %s" % (direction, blk)
            continue
        conf, reasons, groups = _score(st, direction)
        if conf < min_confidence:
            st.block = "%s: conf %d under tröskeln %d" % (direction, conf, min_confidence)
            if conf > st.near_conf:      # nästan-läge: klarade ALLA gates, föll på poängen
                st.near_conf, st.near_dir, st.near_reasons = int(conf), direction, reasons
            continue
        if best is None or conf > best.confidence:
            sl, tp1, tp2 = _levels(st, direction)
            stype = ("SWEEP_RECLAIM" if (st.sweep_long if direction == "LONG" else st.sweep_short)
                     else ("VWAP_TREND" if groups.get("VWAP-lutning") else "MOMENTUM"))
            best = Setup(direction=direction, entry=round(st.price, 2),
                         stop_loss=sl, tp1=tp1, tp2=tp2, confidence=int(conf),
                         setup_type=stype, reasons=reasons, groups=groups,
                         session=st.session)
    return best


# ------------------------------------------------------------------
#  Expectancy — det Quant Trader lovade, räknat på RIKTIGA utfall
# ------------------------------------------------------------------
def expectancy(rows: list) -> Optional[dict]:
    """rows = outcomes-ledgern (dictar med 'r' och 'outcome').
    Ärlig räkning: R-summan är facit; TP/SL/BE-frekvenser redovisas separat."""
    rows = [r for r in rows if r.get("r") is not None]
    n = len(rows)
    if not n:
        return None
    rs = [float(r.get("r") or 0) for r in rows]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    def _rate(kind):
        return sum(1 for r in rows if str(r.get("outcome", "")).upper().startswith(kind)) / n * 100
    return {
        "n": n,
        "win_rate": len(wins) / n * 100,
        "tp_rate": _rate("TP"), "sl_rate": _rate("SL"), "be_rate": _rate("BE"),
        "avg_win_r": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss_r": (sum(losses) / len(losses)) if losses else 0.0,
        "expectancy_r": sum(rs) / n,
        "total_r": sum(rs),
    }
