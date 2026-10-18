"""GEX för NQ och guld räknat direkt på servern (oberoende av GitHub-roboten).

Källa: CBOE:s fördröjda optionskedjor (NDX-index för Nasdaq, GLD för guld) var 15:e minut.
  - GEX per strike = gamma x open interest x 100 x spot^2 x 1 %, calls +, puts −
  - Call Wall / Put Wall = störst positiv / negativ GEX inom ±10 % av spot (4 närmaste förfallen)
  - Gamma Flip = nollgenomgång närmast spot när spot flyttas ±8 % (3 närmaste förfallen)
Nivåerna räknas i underliggandes pris och översätts till orderflödets pris med kvoten
källpris / underliggande vid SAMMA tidpunkt (CBOE:s last_trade_time).
Senaste lyckade beräkning sparas på disk så att nivåerna finns kvar efter en omstart.
"""
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
UND = {"NQ": "_NDX", "GC": "GLD"}
N_EXP, FLIP_EXP = 4, 3
REFRESH_S = 15 * 60
_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "gex_live.json")
_OPT = re.compile(r"(\d{6})([CP])(\d{8})$")
_NY = ZoneInfo("America/New_York")

_C = {"data": {}, "t": 0, "busy": False, "loaded": False}
_L = threading.Lock()


def _f(v):
    try:
        v = float(v)
        return 0.0 if v != v else v
    except (TypeError, ValueError):
        return 0.0


def _bs_gamma(S, K, T, iv):
    if S <= 0 or K <= 0 or T <= 0 or iv <= 0:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * iv * math.sqrt(T))


def _compute(sym):
    j = requests.get(URL.format(sym), headers={"User-Agent": "grabit-gex/1.0"}, timeout=40).json()
    d = j.get("data") or {}
    spot = _f(d.get("current_price"))
    lt = d.get("last_trade_time")
    if not spot or not lt:
        return None
    t_ms = int(datetime.fromisoformat(lt).replace(tzinfo=_NY).timestamp() * 1000)
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    by = {}
    for o in d.get("options") or []:
        m = _OPT.search(o.get("option") or "")
        if not m:
            continue
        ymd, typ, k = m.group(1), m.group(2), int(m.group(3)) / 1000.0
        exp = "20%s-%s-%s" % (ymd[:2], ymd[2:4], ymd[4:])
        oi = _f(o.get("open_interest"))
        if exp < today or k <= 0 or oi <= 0:
            continue
        by.setdefault(exp, []).append((k, oi, _f(o.get("iv")), _f(o.get("gamma")), typ))
    exps = sorted(by)
    if not exps:
        return None
    wall_exps, flip_exps = exps[:N_EXP], exps[:FLIP_EXP]

    def T_of(e):
        y, mo, da = (int(x) for x in e.split("-"))
        return max((datetime(y, mo, da, 20, 0, tzinfo=timezone.utc) - now).total_seconds(), 3600) / (365 * 24 * 3600)

    per = {}
    for e in wall_exps:
        for k, oi, iv, g, typ in by[e]:
            g = g if g > 0 else _bs_gamma(spot, k, T_of(e), iv)
            per[k] = per.get(k, 0.0) + g * oi * 100 * spot * spot * 0.01 * (1 if typ == "C" else -1)
    near = {k: v for k, v in per.items() if abs(k / spot - 1) <= 0.10}
    if not near:
        return None
    call_wall = max(near, key=lambda k: near[k])
    put_wall = min(near, key=lambda k: near[k])
    legs = [(k, oi, iv, T_of(e), 1 if typ == "C" else -1) for e in flip_exps for k, oi, iv, g, typ in by[e] if iv > 0.01]

    def net(S):
        return sum(_bs_gamma(S, k, T, iv) * oi * S * S * sg for k, oi, iv, T, sg in legs)

    grid = [spot * (0.92 + 0.0025 * i) for i in range(65)]
    vals = [net(S) for S in grid]
    flip, best = None, None
    for i in range(1, len(grid)):
        if (vals[i - 1] < 0) != (vals[i] < 0):
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (-vals[i - 1]) / ((vals[i] - vals[i - 1]) or 1)
            if best is None or abs(x - spot) < best:
                best, flip = abs(x - spot), x
    return {"spot": spot, "t_ms": t_ms, "call_wall": call_wall, "put_wall": put_wall,
            "flip": round(flip, 2) if flip else None, "regime": "positiv" if net(spot) >= 0 else "negativ",
            "expiries": wall_exps, "generated": now.isoformat(timespec="seconds")}


def _refresh():
    try:
        out = dict(_C["data"])
        for inst, sym in UND.items():
            try:
                r = _compute(sym)
                if r:
                    out[inst] = r
            except Exception as e:
                print("gex_live %s: %s" % (inst, e))
        _C["data"] = out
        _C["t"] = time.time()
        try:
            with open(_FILE, "w", encoding="utf-8") as f:
                json.dump(out, f)
        except Exception:
            pass
    finally:
        _C["busy"] = False


def get(inst):
    """Senaste nivåer för NQ/GC (i underliggandes pris) eller None."""
    with _L:
        if not _C["loaded"]:
            _C["loaded"] = True
            try:
                with open(_FILE, encoding="utf-8") as f:
                    _C["data"] = json.load(f) or {}
            except Exception:
                pass
        if not _C["busy"] and time.time() - _C["t"] > REFRESH_S:
            _C["busy"] = True
            threading.Thread(target=_refresh, daemon=True).start()
    return _C["data"].get(inst)


def source_price_at(inst, t_ms, bars_1m=None):
    """Orderflödets pris vid tidpunkten t_ms: motorns 1m-bars först, annars 5m-historiken."""
    for b in reversed(bars_1m or []):
        if b["t"] * 1000 <= t_ms < b["t"] * 1000 + 60000:
            return b["c"]
    try:
        import of_levels
        hist = (of_levels._C.get(inst) or {}).get("bars") or []
        for b in reversed(hist):
            if b[0] <= t_ms < b[0] + 300000:
                return b[4]
    except Exception:
        pass
    return None
