"""
GRABIT DESK  ·  desk.py
------------------------
Intraday-desk för NQ (Nasdaq 100) och GC (guld) på propkonto.

Dataflöde
    TradingView (Pine "GRABIT Feed", realtid via CME-paketet)
        -> POST /desk/bar   1-minutsbar + CVD + VWAP vid varje barstängning
        -> bars sparas (minne + DATA_DIR/desk_bars_<INST>.jsonl)
        -> analys: sessionsnivåer, VWAP-band, volymprofil (POC/VAH/VAL),
           CVD + divergens, RVOL, ATR, 5m-trend, gamma-nivåer (gex.py)
        -> setup-motor: konfluens -> plan (entry/stop/TP1/TP2/storlek/confidence)
        -> Telegram + Discord (nasdaq_robber.notify) + push (push_notify)
    Prop-regler (prop_rules.py) spärrar nya entries: daglig budget, max trades,
    flat-tid, paus.

Endpoints (monteras av grabit_entry.py via mount(app))
    POST /desk/bar?key=<TV_WEBHOOK_SECRET>       <- TradingView-feeden
    GET  /desk/status
    GET  /desk/levels?inst=NQ
    GET  /desk/gex?inst=NQ
    GET  /desk/gexstring?inst=NQ|GC|all           sträng till TradingView-indikatorn "GEX Daily Levels"
    GET  /desk/plan?inst=NQ[&send=1&key=ADMIN]   kör motorn nu
    GET  /desk/setups?limit=30
    GET  /desk/pnl?key=ADMIN&add=-120 | &set=0    rapportera dagens P&L (för budgeten)
    GET  /desk/toggle?key=ADMIN&on=0|1
    GET  /desk/bootstrap?key=ADMIN                hämta 5 dagars 1m-historik från Yahoo

Telegram-kommandon (via nasdaq_robber.command_listener -> handle_command)
    /desk  /levels [nq|gc]  /gex [nq|gc]  /tvgex [nq|gc|all]  /plan [nq|gc]  /risk  /pnl -120  /paus  /kör

Env
    DESK_MIN_CONF        lägsta confidence för att larma (default 68)
    DESK_COOLDOWN_MIN    samma setup larmas inte igen inom X min (default 30)
    DESK_PUSH            "all" | "none" (default all)
    DESK_BOOTSTRAP       "1" (default) hämta historik från Yahoo när lagret är tomt
    + prop_rules.py:s DESK_ACCOUNTS / DESK_DAILY_BUDGET_USD / DESK_RISK_PER_TRADE ...
"""
import json
import math
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request

import prop_rules as PR

ET = ZoneInfo("America/New_York")
DATA_DIR = os.environ.get("DATA_DIR", ".")
MAX_BARS = 3 * 1440 + 300
INSTRUMENTS = ("NQ", "GC")
BIN = {"NQ": 2.5, "GC": 1.0}
YF_SYMBOL = {"NQ": "NQ=F", "GC": "GC=F"}
TV_IPS = {"52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"}

_lock = threading.RLock()
BARS = {i: deque(maxlen=MAX_BARS) for i in INSTRUMENTS}
STATUS = {"bars_received": 0, "rejected": 0, "last_bar": {}, "setups_sent": 0,
          "last_setup": None, "last_error": None, "bootstrap": {}}
STATE_FILE = os.path.join(DATA_DIR, "desk_state.json")
SETUPS_FILE = os.path.join(DATA_DIR, "desk_setups.jsonl")
_STATE = None


# ================================================================== util
def _bars_file(inst):
    return os.path.join(DATA_DIR, f"desk_bars_{inst}.jsonl")


def _now():
    return datetime.now(timezone.utc)


def et(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)


def tdate(ts):
    """Handelsdag: CME-sessionen börjar 18:00 ET dagen innan."""
    return (et(ts) + timedelta(hours=6)).date()


def _hm(ts):
    d = et(ts)
    return d.hour * 60 + d.minute


def inst_of(sym):
    s = (sym or "").upper().split(":")[-1]
    if re.match(r"^M?NQ", s) or s in ("US100", "NAS100", "USTEC", "NDX"):
        return "NQ"
    if re.match(r"^M?GC", s) or s in ("XAUUSD", "GOLD", "XAU"):
        return "GC"
    return None


def _f(v):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return None


def _parse_time(v):
    """TradingView: ISO ('2026-09-06T13:30:00Z'), epoch ms eller epoch s."""
    if v is None or v == "":
        return None
    try:
        x = float(v)
        return x / 1000 if x > 1e11 else x
    except Exception:
        pass
    try:
        s = str(v).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


# ================================================================== lagring
def _load_bars():
    for inst in INSTRUMENTS:
        try:
            with open(_bars_file(inst), encoding="utf-8") as f:
                rows = [json.loads(x) for x in f if x.strip()]
            rows.sort(key=lambda b: b["t"])
            BARS[inst].extend(rows[-MAX_BARS:])
        except FileNotFoundError:
            pass
        except Exception as e:
            STATUS["last_error"] = f"läsfel {inst}: {e}"


def _persist(inst):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _bars_file(inst) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for b in BARS[inst]:
                f.write(json.dumps(b) + "\n")
        os.replace(tmp, _bars_file(inst))
    except Exception as e:
        STATUS["last_error"] = f"skrivfel {inst}: {e}"


def add_bar(inst, bar, persist=True):
    """Lägg in/ersätt en 1m-bar (nyckel = t). Returnerar True om den är den senaste."""
    with _lock:
        dq = BARS[inst]
        if dq and bar["t"] < dq[-1]["t"]:
            # historisk bar (bootstrap/omsändning): sortera in
            lst = [b for b in dq if b["t"] != bar["t"]] + [bar]
            lst.sort(key=lambda b: b["t"])
            dq.clear(); dq.extend(lst[-MAX_BARS:])
            latest = False
        elif dq and bar["t"] == dq[-1]["t"]:
            dq[-1] = bar; latest = True
        else:
            dq.append(bar); latest = True
        STATUS["last_bar"][inst] = {"t": datetime.fromtimestamp(bar["t"], tz=timezone.utc).isoformat(timespec="seconds"),
                                    "c": bar["c"], "src": bar.get("src", "tv")}
        if persist and (latest and len(dq) % 5 == 0 or not latest):
            _persist(inst)
    return latest


def _load_state():
    global _STATE
    if _STATE is None:
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                _STATE = json.load(f)
        except Exception:
            _STATE = {}
    today = str((datetime.now(ET) + timedelta(hours=6)).date())
    if _STATE.get("date") != today:
        _STATE = {"date": today, "pnl_today": 0.0, "trades_today": 0,
                  "halted": _STATE.get("halted", False) if _STATE else False, "last_sent": {}}
        _save_state()
    return _STATE


def _save_state():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_STATE, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        STATUS["last_error"] = f"state: {e}"


def _log_setup(s):
    try:
        with open(SETUPS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_setups(limit=30):
    try:
        with open(SETUPS_FILE, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        return list(reversed(rows[-limit:]))
    except Exception:
        return []


# ================================================================== analys
def _vwap(bars):
    pv = vv = var = 0.0
    out = []
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        v = max(b["v"], 0.0)
        pv += tp * v; vv += v
        w = pv / vv if vv else tp
        var += v * (tp - w) ** 2
        out.append(w)
    if not vv:
        return None
    sd = math.sqrt(var / vv) if vv else 0.0
    return {"vwap": out[-1], "sd": sd, "series": out}


def volume_profile(bars, bin_size):
    if not bars:
        return None
    lo = min(b["l"] for b in bars); hi = max(b["h"] for b in bars)
    if hi <= lo:
        return None
    nb = int((hi - lo) / bin_size) + 1
    while nb > 2500:
        bin_size *= 2; nb = int((hi - lo) / bin_size) + 1
    vol = [0.0] * nb
    for b in bars:
        v = max(b["v"], 0.0)
        i0 = int((b["l"] - lo) / bin_size); i1 = int((b["h"] - lo) / bin_size)
        i0 = max(0, min(i0, nb - 1)); i1 = max(0, min(i1, nb - 1))
        per = v / (i1 - i0 + 1)
        for i in range(i0, i1 + 1):
            vol[i] += per
    total = sum(vol)
    if total <= 0:
        return None
    poc = max(range(nb), key=lambda i: vol[i])
    lo_i = hi_i = poc; acc = vol[poc]
    while acc < 0.70 * total and (lo_i > 0 or hi_i < nb - 1):
        up = vol[hi_i + 1] if hi_i < nb - 1 else -1
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1; acc += up
        else:
            lo_i -= 1; acc += dn
    ctr = lambda i: round(lo + (i + 0.5) * bin_size, 2)
    hvn = sorted(range(nb), key=lambda i: -vol[i])[:3]
    return {"poc": ctr(poc), "vah": ctr(hi_i), "val": ctr(lo_i), "total": total,
            "hvn": sorted(ctr(i) for i in hvn), "bin": bin_size, "lo": lo, "hi": hi}


def _resample(bars, minutes):
    out = []
    for b in bars:
        k = int(b["t"] // (minutes * 60))
        if out and out[-1]["k"] == k:
            o = out[-1]
            o["h"] = max(o["h"], b["h"]); o["l"] = min(o["l"], b["l"]); o["c"] = b["c"]; o["v"] += b["v"]
        else:
            out.append({"k": k, "t": k * minutes * 60, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
    return out


def _atr(bars, n=14):
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    trs = trs[-n:]
    return sum(trs) / len(trs) if trs else None


def _ema(vals, n):
    if not vals:
        return None
    k = 2 / (n + 1); e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def _cvd_series(bars):
    """TradingViews CVD om den finns i ≥80 % av barerna, annars proxy ur barformen."""
    have = sum(1 for b in bars if b.get("cvd") is not None)
    if bars and have >= 0.8 * len(bars):
        last = 0.0; out = []
        for b in bars:
            if b.get("cvd") is not None:
                last = b["cvd"]
            out.append(last)
        return out, "tradingview"
    c = 0.0; out = []
    for b in bars:
        rng = b["h"] - b["l"]
        d = b["v"] * ((b["c"] - b["l"]) - (b["h"] - b["c"])) / rng if rng > 0 else 0.0
        c += d; out.append(c)
    return out, "proxy"


def _hl(bars):
    return (max(b["h"] for b in bars), min(b["l"] for b in bars)) if bars else (None, None)


def analyze(inst):
    with _lock:
        bars = list(BARS[inst])
    if len(bars) < 30:
        return None
    last = bars[-1]; price = last["c"]; t = last["t"]
    D = tdate(t)
    sess = [b for b in bars if tdate(b["t"]) == D]
    rth = [b for b in sess if 570 <= _hm(b["t"]) < 960]
    on = [b for b in sess if _hm(b["t"]) >= 1080 or _hm(b["t"]) < 570]      # 18:00 -> 09:30 ET
    asia = [b for b in sess if _hm(b["t"]) >= 1080 or _hm(b["t"]) < 120]
    london = [b for b in sess if 120 <= _hm(b["t"]) < 510]
    ib = [b for b in rth if _hm(b["t"]) < 630]
    orb = [b for b in rth if _hm(b["t"]) < 585]
    prev_dates = sorted({tdate(b["t"]) for b in bars if tdate(b["t"]) < D})
    pd_bars = [b for b in bars if prev_dates and tdate(b["t"]) == prev_dates[-1]]
    pd_rth = [b for b in pd_bars if 570 <= _hm(b["t"]) < 960] or pd_bars

    L = {"inst": inst, "price": price, "t": t, "time_et": et(t).strftime("%Y-%m-%d %H:%M"),
         "session_bars": len(sess), "rth_bars": len(rth), "src": last.get("src", "tv")}
    L["pdh"], L["pdl"] = _hl(pd_rth)
    L["pdc"] = pd_rth[-1]["c"] if pd_rth else None
    L["onh"], L["onl"] = _hl(on)
    L["asia_h"], L["asia_l"] = _hl(asia)
    L["ldn_h"], L["ldn_l"] = _hl(london)
    L["ibh"], L["ibl"] = _hl(ib) if len(ib) >= 55 or (ib and _hm(t) >= 630) else (None, None)
    L["orh"], L["orl"] = _hl(orb) if orb and _hm(t) >= 585 else (None, None)
    L["rth_h"], L["rth_l"] = _hl(rth)

    vw = _vwap(sess); vr = _vwap(rth)
    L["vwap"] = vw["vwap"] if vw else None; L["vwap_sd"] = vw["sd"] if vw else None
    L["vwap_rth"] = vr["vwap"] if vr else None; L["vwap_rth_sd"] = vr["sd"] if vr else None
    L["vwap_series"] = vw["series"] if vw else []
    tv_vwap = [b.get("vwap") for b in sess[-3:] if b.get("vwap") is not None]
    if tv_vwap:
        L["vwap_tv"] = tv_vwap[-1]

    L["vp"] = volume_profile(sess, BIN[inst])
    L["vp_rth"] = volume_profile(rth, BIN[inst]) if len(rth) >= 10 else None
    L["vp_pd"] = volume_profile(pd_rth, BIN[inst]) if len(pd_rth) >= 10 else None

    cvd, cvd_src = _cvd_series(sess)
    L["cvd"] = cvd[-1] if cvd else 0.0; L["cvd_src"] = cvd_src
    L["cvd_slope10"] = (cvd[-1] - cvd[-11]) if len(cvd) > 11 else 0.0
    # divergens över senaste 30 barer
    w = sess[-30:]; wc = cvd[-30:]
    L["div"] = None
    if len(w) >= 15:
        hi_i = max(range(len(w)), key=lambda i: w[i]["h"]); lo_i = min(range(len(w)), key=lambda i: w[i]["l"])
        if hi_i >= len(w) - 3 and wc[-1] < max(wc) - 1e-9 and wc.index(max(wc)) < hi_i - 2:
            L["div"] = "bearish"
        elif lo_i >= len(w) - 3 and wc[-1] > min(wc) + 1e-9 and wc.index(min(wc)) < lo_i - 2:
            L["div"] = "bullish"

    b5 = _resample(bars[-1500:], 5)
    L["atr1"] = _atr(bars[-40:]) or 0.0
    L["atr5"] = _atr(b5[-40:]) or L["atr1"] * 2.2
    closes5 = [b["c"] for b in b5[-120:]]
    e20, e50 = _ema(closes5[-60:], 20), _ema(closes5, 50)
    L["ema20_5m"], L["ema50_5m"] = e20, e50
    L["bias"] = "LONG" if e20 and e50 and e20 > e50 and price > e20 else \
                "SHORT" if e20 and e50 and e20 < e50 and price < e20 else "NEUTRAL"

    # RVOL: RTH-volym hittills mot samma klockslag tidigare dagar
    L["rvol"] = None
    if rth:
        elapsed = _hm(t) - 570
        ref = []
        for d in prev_dates[-3:]:
            vols = [b["v"] for b in bars if tdate(b["t"]) == d and 570 <= _hm(b["t"]) <= 570 + elapsed]
            if vols:
                ref.append(sum(vols))
        cur = sum(b["v"] for b in rth)
        if ref and sum(ref) > 0:
            L["rvol"] = round(cur / (sum(ref) / len(ref)), 2)

    try:
        import gex as GX
        g = GX.get_gex(inst, fut_price=price)
        L["gex"] = g.get("futures") if g else None
        L["gex_stale"] = bool(g and g.get("stale"))
    except Exception as e:
        L["gex"] = None; L["gex_err"] = str(e)

    L["tol"] = max(L["atr1"] * 1.0, price * 0.0006)
    L["levels"] = _level_list(L)
    return L


def _level_list(L):
    out = []
    def add(name, v):
        if v is not None:
            out.append((name, float(v)))
    add("PDH", L.get("pdh")); add("PDL", L.get("pdl")); add("PDC", L.get("pdc"))
    add("ONH", L.get("onh")); add("ONL", L.get("onl"))
    add("Asia H", L.get("asia_h")); add("Asia L", L.get("asia_l"))
    add("London H", L.get("ldn_h")); add("London L", L.get("ldn_l"))
    add("IBH", L.get("ibh")); add("IBL", L.get("ibl"))
    add("ORH", L.get("orh")); add("ORL", L.get("orl"))
    add("VWAP", L.get("vwap"))
    if L.get("vwap") and L.get("vwap_sd"):
        add("VWAP+1σ", L["vwap"] + L["vwap_sd"]); add("VWAP-1σ", L["vwap"] - L["vwap_sd"])
        add("VWAP+2σ", L["vwap"] + 2 * L["vwap_sd"]); add("VWAP-2σ", L["vwap"] - 2 * L["vwap_sd"])
    for key, pre in (("vp", ""), ("vp_pd", "PD ")):
        vp = L.get(key)
        if vp:
            add(pre + "POC", vp["poc"]); add(pre + "VAH", vp["vah"]); add(pre + "VAL", vp["val"])
    g = L.get("gex")
    if g:
        add("Call wall", g.get("call_wall")); add("Put wall", g.get("put_wall")); add("Zero gamma", g.get("zero_gamma"))
    return out


def _near(L, price, tol=None, exclude=()):
    tol = tol or L["tol"]
    return [(n, v) for n, v in L["levels"] if abs(v - price) <= tol and n not in exclude]


def _killzone(hm, weekday):
    if weekday >= 5:
        return -30, "helg"
    if 570 <= hm < 690:
        return 10, "NY open"
    if 120 <= hm < 300:
        return 5, "London"
    if 690 <= hm < 810:
        return -10, "lunch"
    if 810 <= hm < 945:
        return 3, "NY PM"
    if 945 <= hm < 1080:
        return -20, "stängning"
    return -10, "natt"


# ================================================================== setup-motor
def _targets(L, side, entry, stop):
    R = abs(entry - stop)
    if R <= 0:
        return None, None, []
    sgn = 1 if side == "LONG" else -1
    cands = sorted([(n, v) for n, v in L["levels"] if sgn * (v - entry) >= 1.5 * R],
                   key=lambda x: sgn * (x[1] - entry))
    names = []
    if cands:
        tp1 = cands[0][1]; names.append(cands[0][0])
        nxt = [c for c in cands if sgn * (c[1] - tp1) >= 0.8 * R]
        tp2 = nxt[0][1] if nxt else entry + sgn * 3 * R
        if nxt:
            names.append(nxt[0][0])
    else:
        tp1, tp2 = entry + sgn * 2 * R, entry + sgn * 3 * R
    return round(tp1, 2), round(tp2, 2), names


def _mk(L, side, name, entry, stop, base, reasons):
    sgn = 1 if side == "LONG" else -1
    tick = PR.CONTRACTS[L["inst"]]["tick"]
    stop = round(stop / tick) * tick
    if sgn * (entry - stop) <= 2 * tick or abs(entry - stop) > 3 * L["atr5"]:
        return None
    conf = base; rs = list(reasons)
    kz, kzname = _killzone(_hm(L["t"]), et(L["t"]).weekday())
    conf += kz; rs.append(f"tid: {kzname} ({kz:+d})")
    if L["bias"] == side:
        conf += 8; rs.append("5m-trend med")
    elif L["bias"] != "NEUTRAL":
        conf -= 15; rs.append("MOT 5m-trend")
    if (side == "LONG" and L["cvd_slope10"] > 0) or (side == "SHORT" and L["cvd_slope10"] < 0):
        conf += 12; rs.append(f"CVD bekräftar ({L['cvd_src']})")
    else:
        conf -= 6; rs.append("CVD bekräftar inte")
    if L.get("div") == ("bullish" if side == "LONG" else "bearish"):
        conf += 10; rs.append(f"CVD-divergens {L['div']}")
    elif L.get("div"):
        conf -= 10; rs.append(f"CVD-divergens MOT ({L['div']})")
    if L.get("rvol") and L["rvol"] >= 1.2:
        conf += 8; rs.append(f"RVOL {L['rvol']}x")
    elif L.get("rvol") and L["rvol"] < 0.7:
        conf -= 8; rs.append(f"RVOL lågt {L['rvol']}x")
    near = sorted(_near(L, entry, tol=L["tol"] * 1.5), key=lambda x: abs(x[1] - entry))
    extra = max(0, len(near) - 1)
    if extra:
        conf += min(20, 7 * extra)
        rs.append("konfluens: " + ", ".join(n for n, _ in near[:4]) + (f" +{len(near) - 4}" if len(near) > 4 else ""))
    g = L.get("gex")
    if g:
        fade = name.lower().startswith(("fade", "vwap-band", "gamma", "värdeområde-avvisning"))
        if g["regime"] == "positiv" and fade:
            conf += 10; rs.append("positiv gamma stöder fade")
        elif g["regime"] == "negativ" and not fade:
            conf += 8; rs.append("negativ gamma stöder trend")
        elif g["regime"] == "negativ" and fade:
            conf -= 8; rs.append("negativ gamma: fade riskabelt")
    conf = max(0, min(100, int(round(conf))))
    tp1, tp2, tnames = _targets(L, side, entry, stop)
    R = abs(entry - stop)
    lim = PR.effective_limits()
    risk = PR.remaining_risk(_load_state(), lim)["risk_next_trade"]
    size = PR.size_position(L["inst"], R, risk, lim)
    return {"inst": L["inst"], "side": side, "name": name, "entry": round(entry, 2), "stop": stop,
            "tp1": tp1, "tp2": tp2, "tp_names": tnames, "r_points": round(R, 2),
            "rr1": round(abs(tp1 - entry) / R, 2) if R else None, "conf": conf, "reasons": rs,
            "size": size, "time_et": L["time_et"], "price": L["price"], "bias": L["bias"],
            "gex_regime": g["regime"] if g else None, "key": f"{L['inst']}:{side}:{name}"}


def find_setups(L):
    with _lock:
        bars = list(BARS[L["inst"]])
    if len(bars) < 30:
        return []
    last, prev = bars[-1], bars[-2]
    p = last["c"]; a = L["atr1"]; tol = L["tol"]
    lows3 = min(b["l"] for b in bars[-3:]); highs3 = max(b["h"] for b in bars[-3:])
    out = []
    vw, sd = L.get("vwap"), L.get("vwap_sd") or 0

    # 1) VWAP reclaim / rejection
    if vw:
        if prev["c"] < vw <= p and last["c"] > last["o"]:
            out.append(_mk(L, "LONG", "VWAP-återtag", p, min(lows3, vw - 0.5 * a) - 0.5 * a, 45,
                           [f"stängde över VWAP {vw:.2f}"]))
        if prev["c"] > vw >= p and last["c"] < last["o"]:
            out.append(_mk(L, "SHORT", "VWAP-avvisning", p, max(highs3, vw + 0.5 * a) + 0.5 * a, 45,
                           [f"stängde under VWAP {vw:.2f}"]))
        # 2) VWAP-band fade (2σ)
        if sd and last["h"] >= vw + 2 * sd and p < vw + 2 * sd and last["c"] < last["o"]:
            out.append(_mk(L, "SHORT", "VWAP-band fade", p, last["h"] + 0.5 * a, 42,
                           [f"avvisad vid +2σ {vw + 2 * sd:.2f}"]))
        if sd and last["l"] <= vw - 2 * sd and p > vw - 2 * sd and last["c"] > last["o"]:
            out.append(_mk(L, "LONG", "VWAP-band fade", p, last["l"] - 0.5 * a, 42,
                           [f"avvisad vid −2σ {vw - 2 * sd:.2f}"]))

    # 3) Värdeområde (VAH/VAL): avvisning -> fade mot POC, acceptans -> fortsättning
    vp = L.get("vp")
    if vp:
        vah, val = vp["vah"], vp["val"]
        if last["h"] >= vah - tol and p < vah and last["c"] < last["o"] and prev["c"] < vah + tol:
            out.append(_mk(L, "SHORT", "värdeområde-avvisning VAH", p, last["h"] + 0.5 * a, 42,
                           [f"avvisad vid VAH {vah:.2f}, mål POC {vp['poc']:.2f}"]))
        if last["l"] <= val + tol and p > val and last["c"] > last["o"] and prev["c"] > val - tol:
            out.append(_mk(L, "LONG", "värdeområde-avvisning VAL", p, last["l"] - 0.5 * a, 42,
                           [f"avvisad vid VAL {val:.2f}, mål POC {vp['poc']:.2f}"]))
        if prev["c"] <= vah < p - 0.3 * a and last["c"] > last["o"]:
            out.append(_mk(L, "LONG", "acceptans över VAH", p, vah - 0.7 * a, 40,
                           [f"stängde över VAH {vah:.2f}"]))
        if prev["c"] >= val > p + 0.3 * a and last["c"] < last["o"]:
            out.append(_mk(L, "SHORT", "acceptans under VAL", p, val + 0.7 * a, 40,
                           [f"stängde under VAL {val:.2f}"]))

    # 4) Gamma-väggar
    g = L.get("gex")
    if g:
        cw, pw = g.get("call_wall"), g.get("put_wall")
        if cw and abs(last["h"] - cw) <= tol and p < cw and last["c"] < last["o"]:
            out.append(_mk(L, "SHORT", "gamma fade call wall", p, max(last["h"], cw) + 0.6 * a, 44,
                           [f"call wall {cw:.0f} håller"]))
        if pw and abs(last["l"] - pw) <= tol and p > pw and last["c"] > last["o"]:
            out.append(_mk(L, "LONG", "gamma fade put wall", p, min(last["l"], pw) - 0.6 * a, 44,
                           [f"put wall {pw:.0f} håller"]))

    # 5) Opening range breakout (09:45–10:30 ET)
    hm = _hm(L["t"])
    if L.get("orh") and 585 <= hm < 630:
        if prev["c"] <= L["orh"] < p - 0.25 * a:
            out.append(_mk(L, "LONG", "ORB", p, L["orl"] if (p - L["orl"]) <= 2.5 * L["atr5"] else p - 1.5 * a, 44,
                           [f"bröt ORH {L['orh']:.2f}"]))
        if prev["c"] >= L["orl"] > p + 0.25 * a:
            out.append(_mk(L, "SHORT", "ORB", p, L["orh"] if (L["orh"] - p) <= 2.5 * L["atr5"] else p + 1.5 * a, 44,
                           [f"bröt ORL {L['orl']:.2f}"]))

    # 6) Nivå + divergens (PDH/PDL/ONH/ONL): sweep med CVD-divergens = fade
    if L.get("div"):
        keys = ("PDH", "ONH", "London H", "Asia H", "IBH") if L["div"] == "bearish" else ("PDL", "ONL", "London L", "Asia L", "IBL")
        hit = [(n, v) for n, v in L["levels"] if n in keys and abs((last["h"] if L["div"] == "bearish" else last["l"]) - v) <= tol]
        if hit:
            n, v = hit[0]
            if L["div"] == "bearish" and p < v:
                out.append(_mk(L, "SHORT", f"fade sweep {n}", p, last["h"] + 0.5 * a, 46, [f"sweep av {n} {v:.2f} utan CVD-stöd"]))
            if L["div"] == "bullish" and p > v:
                out.append(_mk(L, "LONG", f"fade sweep {n}", p, last["l"] - 0.5 * a, 46, [f"sweep av {n} {v:.2f} utan CVD-stöd"]))

    out = [s for s in out if s]
    out.sort(key=lambda s: -s["conf"])
    return out


# ================================================================== GEX-sträng till TradingView
_ATR_D = {}   # inst -> {"t": epoch, "atr": float}


def daily_open(inst):
    """Dagens RTH-öppning (09:30 ET) om den finns, annars sessionsöppningen (18:00 ET)."""
    with _lock:
        bars = list(BARS[inst])
    if not bars:
        return None
    D = tdate(bars[-1]["t"])
    sess = [b for b in bars if tdate(b["t"]) == D]
    rth = [b for b in sess if 570 <= _hm(b["t"]) < 960]
    if rth:
        return rth[0]["o"]
    return sess[0]["o"] if sess else None


def atr_daily(inst, n=14):
    """Dags-ATR: Yahoo dagsbarer (cache 6 h), annars dagsintervall ur egna barer."""
    c = _ATR_D.get(inst)
    if c and time.time() - c["t"] < 6 * 3600:
        return c["atr"]
    atr = None
    try:
        import yfinance as yf
        df = yf.download(YF_SYMBOL[inst], period="2mo", interval="1d", progress=False,
                         auto_adjust=False, threads=False)
        if df is not None and len(df) > n:
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            df.columns = [str(x).title() for x in df.columns]
            rows = [{"h": float(r["High"]), "l": float(r["Low"]), "c": float(r["Close"])} for _, r in df.iterrows()]
            atr = _atr(rows, n)
    except Exception:
        atr = None
    if not atr:
        with _lock:
            bars = list(BARS[inst])
        days = {}
        for b in bars:
            d = days.setdefault(tdate(b["t"]), {"h": b["h"], "l": b["l"], "c": b["c"]})
            d["h"] = max(d["h"], b["h"]); d["l"] = min(d["l"], b["l"]); d["c"] = b["c"]
        rows = [days[k] for k in sorted(days)]
        atr = _atr(rows, n) if len(rows) >= 2 else None
    if atr:
        _ATR_D[inst] = {"t": time.time(), "atr": atr}
    return atr


def gex_string(inst):
    """Strängen till Pine-indikatorn 'GEX Daily Levels' (pris,etikett,typ;...)."""
    import gex as GX
    L = analyze(inst)
    price = L["price"] if L else None
    if not price:
        with _lock:
            price = BARS[inst][-1]["c"] if BARS[inst] else None
    g = GX.get_gex(inst, fut_price=price)
    if not g or not g.get("futures"):
        return ""
    return GX.levels_string(inst, g, open_price=daily_open(inst), atr_daily=atr_daily(inst),
                            today=datetime.now(ET).date())


def gex_string_text(inst):
    s = gex_string(inst)
    if not s:
        return f"GEX {inst}: ingen data att bygga sträng av (Yahoo nåddes inte och ingen cache)."
    n = s.count(";") + 1
    return (f"\U0001F4CB <b>GEX Daily Levels · {inst}</b> ({n} nivåer)\n"
            f"Klistra in i indikatorns fält \u201c{inst if inst == 'NQ' else 'GOLD GC'} \u2014 levels string\u201d:\n"
            f"<pre>{s}</pre>")


def _maybe_morning_gex():
    """Postar GEX-strängarna för NQ och GC en gång per dag runt 09:10 ET (första bar efter)."""
    st = _load_state()
    now = datetime.now(ET)
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 550:
        return
    if st.get("gex_posted") == st.get("date"):
        return
    st["gex_posted"] = st.get("date"); _save_state()
    for inst in INSTRUMENTS:
        try:
            _notify(gex_string_text(inst))
        except Exception as e:
            STATUS["last_error"] = f"morgon-gex {inst}: {e}"


# ================================================================== text
def _fmt(v, nd=2):
    return "—" if v is None else f"{v:.{nd}f}"


def levels_text(L):
    if not L:
        return "Inga barer ännu — koppla TradingView-feeden (se DESK.md) eller kör /desk/bootstrap."
    name = PR.CONTRACTS[L["inst"]]["name"]
    vp = L.get("vp") or {}
    g = L.get("gex")
    lines = [f"\U0001F4CB <b>{L['inst']} · {name}</b> · {L['price']:.2f} · {L['time_et']} ET"
             + (" · <i>Yahoo-historik</i>" if L.get("src") == "yf" else ""),
             f"Bias 5m: <b>{L['bias']}</b> · ATR1m {L['atr1']:.2f} · ATR5m {L['atr5']:.2f}"
             + (f" · RVOL {L['rvol']}x" if L.get("rvol") else ""),
             f"VWAP {_fmt(L.get('vwap'))} (±1σ {_fmt((L.get('vwap') or 0) - (L.get('vwap_sd') or 0))} / {_fmt((L.get('vwap') or 0) + (L.get('vwap_sd') or 0))})"
             + (f" · RTH-VWAP {_fmt(L.get('vwap_rth'))}" if L.get("vwap_rth") else ""),
             f"Profil: POC {_fmt(vp.get('poc'))} · VAH {_fmt(vp.get('vah'))} · VAL {_fmt(vp.get('val'))}",
             f"PDH {_fmt(L.get('pdh'))} · PDL {_fmt(L.get('pdl'))} · PDC {_fmt(L.get('pdc'))}",
             f"ONH {_fmt(L.get('onh'))} · ONL {_fmt(L.get('onl'))} · London {_fmt(L.get('ldn_l'))}–{_fmt(L.get('ldn_h'))}"]
    if L.get("ibh"):
        lines.append(f"IB {_fmt(L['ibl'])}–{_fmt(L['ibh'])}" + (f" · OR {_fmt(L.get('orl'))}–{_fmt(L.get('orh'))}" if L.get("orh") else ""))
    cvd_dir = "↑" if L["cvd_slope10"] > 0 else "↓" if L["cvd_slope10"] < 0 else "→"
    lines.append(f"CVD {cvd_dir} {L['cvd']:,.0f} ({L['cvd_src']})" + (f" · <b>divergens {L['div']}</b>" if L.get("div") else ""))
    if g:
        lines.append(f"Gamma {g['regime']}: call wall {_fmt(g.get('call_wall'), 0)} · put wall {_fmt(g.get('put_wall'), 0)}"
                     + (f" · zero {_fmt(g.get('zero_gamma'), 0)}" if g.get("zero_gamma") else "")
                     + (" ⚠ gammal" if L.get("gex_stale") else ""))
    near = _near(L, L["price"], tol=L["tol"] * 2)
    if near:
        lines.append("Nära nu: " + ", ".join(f"{n} {v:.2f}" for n, v in near))
    return "\n".join(lines)


def plan_text(s, gate_note=None):
    e = "\U0001F4C8" if s["side"] == "LONG" else "\U0001F4C9"
    tier = "\U0001F7E2 A+" if s["conf"] >= 75 else "\U0001F7E1 B" if s["conf"] >= 60 else "\U0001F534 svag"
    lines = [f"{e} <b>{s['inst']} {s['side']} · {s['name']}</b> · {tier} {s['conf']}/100",
             f"Entry <b>{s['entry']}</b> · Stop <b>{s['stop']}</b> ({s['r_points']} p)",
             f"TP1 <b>{s['tp1']}</b> ({s['rr1']}R" + (f", {s['tp_names'][0]}" if s["tp_names"] else "") + f") · TP2 <b>{s['tp2']}</b>"
             + (f" ({s['tp_names'][1]})" if len(s["tp_names"]) > 1 else ""),
             f"Storlek: <b>{s['size']['text']}</b>",
             "Varför: " + "; ".join(s["reasons"][:6]),
             f"<i>{s['time_et']} ET · bias {s['bias']}" + (f" · gamma {s['gex_regime']}" if s.get("gex_regime") else "") + "</i>"]
    if gate_note:
        lines.append(f"⛔ <b>Ingen ny entry:</b> {gate_note}")
    lines.append("Förvaltning: flytta stop till break-even vid TP1, ta 50 %, trail resten.")
    return "\n".join(lines)


def status_text():
    st = _load_state(); lim = PR.effective_limits(); rr = PR.remaining_risk(st, lim)
    now = datetime.now(ET); ok, why = PR.day_gate(st, now, lim)
    lines = ["\U0001F3E6 <b>GRABIT DESK</b> · " + now.strftime("%a %H:%M ET"),
             f"Status: {'AKTIV' if ok else 'SPÄRRAD'} ({why})",
             f"Dagens P&L: {st.get('pnl_today', 0):+.0f} USD · trades {st.get('trades_today', 0)}/{lim['max_trades_day']}",
             f"Budget kvar: {rr['budget_left']:.0f} USD · risk nästa trade {rr['risk_next_trade']:.0f} USD"]
    for inst in INSTRUMENTS:
        lb = STATUS["last_bar"].get(inst)
        if lb:
            age = (_now() - datetime.fromisoformat(lb["t"])).total_seconds() / 60
            lines.append(f"{inst}: {lb['c']} · senaste bar {age:.0f} min sedan ({lb['src']}) · {len(BARS[inst])} barer")
        else:
            lines.append(f"{inst}: ingen data")
    if STATUS.get("last_setup"):
        lines.append(f"Senaste larm: {STATUS['last_setup']}")
    return "\n".join(lines)


# ================================================================== larm
def _push(title, body):
    if os.environ.get("DESK_PUSH", "all") == "none":
        return None
    try:
        import push_notify as PN
        return PN.send_all(title, body, url="/", tag="desk")
    except Exception as e:
        return f"fel: {e}"


def _notify(text):
    try:
        from nasdaq_robber import notify
        return bool(notify(text))
    except Exception as e:
        STATUS["last_error"] = f"notify: {e}"
        return False


def maybe_alert(inst):
    """Körs vid varje ny bar. Larmar bästa setupen om den klarar tröskel, spärr och cooldown."""
    L = analyze(inst)
    if not L:
        return None
    setups = find_setups(L)
    if not setups:
        return None
    best = setups[0]
    min_conf = int(os.environ.get("DESK_MIN_CONF", "68"))
    if best["conf"] < min_conf:
        return None
    st = _load_state()
    ok, why = PR.day_gate(st, datetime.now(ET))
    if not ok:
        return None
    cd = int(os.environ.get("DESK_COOLDOWN_MIN", "30")) * 60
    last = st.setdefault("last_sent", {}).get(best["key"], 0)
    if time.time() - last < cd:
        return None
    st["last_sent"][best["key"]] = time.time(); _save_state()
    text = plan_text(best)
    sent = _notify(text)
    _push(f"{best['inst']} {best['side']} · {best['conf']}/100 · {best['name']}",
          f"Entry {best['entry']} · SL {best['stop']} · TP1 {best['tp1']} · {best['size']['text']}")
    best = dict(best); best["sent"] = sent; best["ts"] = _now().isoformat(timespec="seconds")
    _log_setup(best)
    STATUS["setups_sent"] += 1
    STATUS["last_setup"] = f"{best['inst']} {best['side']} {best['name']} {best['conf']} @ {best['time_et']}"
    return best


# ================================================================== bootstrap (Yahoo 1m-historik)
def bootstrap(inst=None, days=5):
    """Fyller lagret med 1m-historik från Yahoo (10 min fördröjd — duger för nivåer,
    inte för entries). Körs i bakgrund vid start om lagret är tomt."""
    res = {}
    for i in ([inst] if inst else INSTRUMENTS):
        try:
            import yfinance as yf
            df = yf.download(YF_SYMBOL[i], period=f"{days}d", interval="1m", progress=False,
                             auto_adjust=False, threads=False)
            if df is None or df.empty:
                res[i] = "0 rader"; continue
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            df.columns = [str(c).title() for c in df.columns]
            n = 0
            for ts, r in df.iterrows():
                t = ts.timestamp() if getattr(ts, "tzinfo", None) else ts.tz_localize("UTC").timestamp()
                bar = {"t": float(t), "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]),
                       "c": float(r["Close"]), "v": float(r.get("Volume") or 0), "src": "yf"}
                if any(math.isnan(x) for x in (bar["o"], bar["h"], bar["l"], bar["c"])):
                    continue
                add_bar(i, bar, persist=False); n += 1
            _persist(i)
            res[i] = f"{n} barer"
        except Exception as e:
            res[i] = f"fel: {type(e).__name__}: {e}"
    STATUS["bootstrap"] = {**STATUS.get("bootstrap", {}), **res, "t": _now().isoformat(timespec="seconds")}
    return res


def _auto_bootstrap():
    if os.environ.get("DESK_BOOTSTRAP", "1") != "1":
        return
    need = [i for i in INSTRUMENTS if len(BARS[i]) < 300]
    if need:
        threading.Thread(target=lambda: [bootstrap(i) for i in need], daemon=True, name="desk-bootstrap").start()


# ================================================================== Telegram-kommandon
def _inst_arg(text, default="NQ"):
    parts = text.split()
    if len(parts) > 1:
        i = inst_of(parts[1]) or (parts[1].upper() if parts[1].upper() in INSTRUMENTS else None)
        if i:
            return i
    return default


def handle_command(cmd, text):
    """Returnerar HTML-svar eller None om kommandot inte är deskens."""
    cmd = cmd.lower()
    if cmd == "/desk":
        return status_text()
    if cmd in ("/levels", "/nivaer", "/nivåer"):
        return levels_text(analyze(_inst_arg(text)))
    if cmd == "/gex":
        import gex as GX
        inst = _inst_arg(text); L = analyze(inst)
        return GX.gex_text(inst, GX.get_gex(inst, fut_price=L["price"] if L else None))
    if cmd == "/plan":
        inst = _inst_arg(text); L = analyze(inst)
        if not L:
            return levels_text(None)
        s = find_setups(L)
        ok, why = PR.day_gate(_load_state(), datetime.now(ET))
        if not s:
            return f"Ingen setup i {inst} just nu.\n\n" + levels_text(L)
        return plan_text(s[0], None if ok else why) + (f"\n\n<i>Alternativ: {s[1]['side']} {s[1]['name']} ({s[1]['conf']})</i>" if len(s) > 1 else "")
    if cmd in ("/tvgex", "/gexstring", "/gexsträng", "/gexstrang"):
        parts = text.split()
        if len(parts) > 1 and parts[1].lower() in ("all", "alla", "båda", "bada"):
            return gex_string_text("NQ") + "\n\n" + gex_string_text("GC")
        return gex_string_text(_inst_arg(text))
    if cmd in ("/risk", "/regler"):
        return PR.rules_text() + "\n\n" + status_text()
    if cmd == "/pnl":
        st = _load_state(); parts = text.split()
        if len(parts) > 1:
            v = _f(parts[1].replace("+", ""))
            if v is None:
                return "Skriv t.ex. <code>/pnl -120</code> (dagens resultat i USD, ackumuleras) eller <code>/pnl set 0</code>."
            if parts[1].lower() == "set" and len(parts) > 2:
                st["pnl_today"] = _f(parts[2]) or 0.0
            else:
                st["pnl_today"] = float(st.get("pnl_today", 0)) + v
                st["trades_today"] = int(st.get("trades_today", 0)) + 1
            _save_state()
        return status_text()
    if cmd in ("/paus", "/pause"):
        st = _load_state(); st["halted"] = True; _save_state()
        return "⏸ Desken pausad — inga nya setups skickas. /kör för att starta igen."
    if cmd in ("/kör", "/kor", "/run"):
        st = _load_state(); st["halted"] = False; _save_state()
        return "▶ Desken igång.\n" + status_text()
    return None


HELP_TEXT = ("\U0001F3E6 <b>DESK</b>\n/desk – status · /levels nq|gc – nivåer · /gex nq|gc – gamma\n"
             "/tvgex nq|gc|all – sträng till TradingView-indikatorn\n"
             "/plan nq|gc – setup nu · /risk – regler · /pnl -120 – rapportera resultat\n/paus · /kör")


# ================================================================== routes
def mount(app):
    _load_bars()
    _auto_bootstrap()

    def _secret_ok(key, payload):
        secret = os.environ.get("TV_WEBHOOK_SECRET", "").strip()
        supplied = key or (payload or {}).get("secret") or (payload or {}).get("key") or ""
        return bool(secret) and str(supplied) == secret

    def _admin(key):
        k = os.environ.get("ROBBER_ADMIN_KEY", "")
        if not (k and key == k):
            raise HTTPException(403, "fel eller saknad nyckel (ROBBER_ADMIN_KEY)")

    @app.post("/desk/bar")
    async def desk_bar(request: Request, key: str = ""):
        raw = (await request.body())[:8192]
        try:
            p = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            STATUS["rejected"] += 1
            raise HTTPException(400, "body måste vara JSON")
        if not isinstance(p, dict) or not _secret_ok(key, p):
            STATUS["rejected"] += 1
            raise HTTPException(403, "fel eller saknad hemlighet")
        if os.environ.get("TV_IP_CHECK", "") == "1":
            ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                  or (request.client.host if request.client else ""))
            if ip not in TV_IPS:
                STATUS["rejected"] += 1
                raise HTTPException(403, f"avsändare {ip} är inte TradingView")
        inst = inst_of(p.get("sym") or p.get("ticker") or p.get("symbol"))
        if not inst:
            STATUS["rejected"] += 1
            raise HTTPException(400, "okänd symbol — väntar NQ/MNQ eller GC/MGC")
        t = _parse_time(p.get("t") or p.get("time"))
        o, h, l, c = (_f(p.get(k1) if p.get(k1) is not None else p.get(k2)) for k1, k2 in
                      (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")))
        v = _f(p.get("v") if p.get("v") is not None else p.get("volume")) or 0.0
        if None in (o, h, l, c) or t is None:
            STATUS["rejected"] += 1
            raise HTTPException(400, "kräver t, o, h, l, c (och v)")
        bar = {"t": float(t), "o": o, "h": h, "l": l, "c": c, "v": v, "src": "tv"}
        if p.get("cvd") is not None and _f(p.get("cvd")) is not None:
            bar["cvd"] = _f(p.get("cvd"))
        if p.get("vwap") is not None and _f(p.get("vwap")) is not None:
            bar["vwap"] = _f(p.get("vwap"))
        latest = add_bar(inst, bar)
        STATUS["bars_received"] += 1
        alerted = None
        if latest:
            try:
                alerted = maybe_alert(inst)
            except Exception as e:
                STATUS["last_error"] = f"alert: {type(e).__name__}: {e}"
            if os.environ.get("DESK_MORNING_GEX", "1") == "1":
                try:
                    _maybe_morning_gex()
                except Exception as e:
                    STATUS["last_error"] = f"morgon-gex: {e}"
        return {"ok": True, "inst": inst, "bars": len(BARS[inst]),
                "alert": (f"{alerted['side']} {alerted['name']} {alerted['conf']}" if alerted else None)}

    @app.get("/desk/status")
    def desk_status():
        st = _load_state(); lim = PR.effective_limits()
        ok, why = PR.day_gate(st, datetime.now(ET), lim)
        return {"ok": True, "gate": {"open": ok, "why": why}, "state": st,
                "limits": {k: (v.strftime("%H:%M") if hasattr(v, "strftime") else v) for k, v in lim.items()},
                "bars": {i: len(BARS[i]) for i in INSTRUMENTS}, "status": STATUS,
                "feed_configured": bool(os.environ.get("TV_WEBHOOK_SECRET"))}

    @app.get("/desk/levels")
    def desk_levels(inst: str = "NQ"):
        inst = inst.upper()
        if inst not in INSTRUMENTS:
            raise HTTPException(400, "inst måste vara NQ eller GC")
        L = analyze(inst)
        if not L:
            return {"ok": False, "reason": "för få barer", "bars": len(BARS[inst])}
        pub = {k: v for k, v in L.items() if k not in ("vwap_series",)}
        pub["levels"] = [{"name": n, "value": v} for n, v in L["levels"]]
        return {"ok": True, "levels": pub, "text": levels_text(L)}

    @app.get("/desk/gex")
    def desk_gex(inst: str = "NQ", force: int = 0):
        import gex as GX
        inst = inst.upper(); L = analyze(inst)
        g = GX.get_gex(inst, fut_price=L["price"] if L else None, force=bool(force))
        return {"ok": bool(g), "gex": g, "text": GX.gex_text(inst, g)}

    @app.get("/desk/gexstring")
    def desk_gexstring(inst: str = "NQ"):
        """Strängen till TradingView-indikatorn 'GEX Daily Levels'. inst=NQ|GC|all."""
        from fastapi.responses import PlainTextResponse
        inst = inst.upper()
        inst = {"MNQ": "NQ", "MGC": "GC"}.get(inst, inst)     # micros = samma pris, samma nivåer
        if inst == "ALL":
            return {"ok": True, "NQ": gex_string("NQ"), "GC": gex_string("GC"),
                    "format": "pris,etikett,typ;...  (res sup res0 sup0 flip hgex mpain gpos gneg emh eml emb ivh ivl opo opu opd)"}
        if inst not in INSTRUMENTS:
            raise HTTPException(400, "inst måste vara NQ, GC eller all")
        return PlainTextResponse(gex_string(inst))

    @app.get("/desk/plan")
    def desk_plan(inst: str = "NQ", send: int = 0, key: str = ""):
        inst = inst.upper(); L = analyze(inst)
        if not L:
            return {"ok": False, "reason": "för få barer"}
        s = find_setups(L)
        ok, why = PR.day_gate(_load_state(), datetime.now(ET))
        out = {"ok": True, "gate": {"open": ok, "why": why}, "setups": s, "levels_text": levels_text(L)}
        if s:
            out["text"] = plan_text(s[0], None if ok else why)
            if send:
                _admin(key); out["sent"] = _notify(out["text"])
        return out

    @app.get("/desk/setups")
    def desk_setups(limit: int = 30):
        return {"ok": True, "setups": _read_setups(max(1, min(limit, 200)))}

    @app.get("/desk/pnl")
    def desk_pnl(key: str = "", add: str = "", set: str = ""):
        _admin(key); st = _load_state()
        if set != "":
            st["pnl_today"] = _f(set) or 0.0
        if add != "":
            st["pnl_today"] = float(st.get("pnl_today", 0)) + (_f(add) or 0.0)
            st["trades_today"] = int(st.get("trades_today", 0)) + 1
        _save_state()
        return {"ok": True, "state": st, "text": status_text()}

    @app.get("/desk/toggle")
    def desk_toggle(key: str = "", on: int = 1):
        _admin(key); st = _load_state(); st["halted"] = not bool(on); _save_state()
        return {"ok": True, "halted": st["halted"]}

    @app.get("/desk/bootstrap")
    def desk_bootstrap(key: str = "", inst: str = ""):
        _admin(key)
        return {"ok": True, "result": bootstrap(inst.upper() or None)}

    return app
