"""GEX för Bitcoin från Deribits optioner (publik data, ingen nyckel).

Samma modell som för Nasdaq och guld: gamma per option (Black-Scholes på Deribits mark-IV)
x open interest x spot^2 x 1 %, köpoptioner positiva och säljoptioner negativa (handlarnas position).
  - Call Wall  = strike med störst positiv GEX
  - Put Wall   = strike med störst negativ GEX
  - Gamma Flip = där nettogamman byter tecken när spot flyttas ±10 % (närmaste förfallen)
Nivåerna är i Deribits indexpris (≈ spot). Utdata har samma form som levels/latest.json.
"""
import math
import threading
import time
from datetime import datetime, timezone

import requests

URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
MONTHS = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
MAX_DAYS = 45          # förfall längre bort än så påverkar dagens nivåer lite
FLIP_EXPIRIES = 3      # flippen räknas på de närmaste förfallen (som för NQ/GC)
REFRESH_S = 300

_C = {"t": 0, "data": None, "busy": False}
_L = threading.Lock()


def _exp_dt(code):
    """'19OCT26' -> förfall 08:00 UTC (Deribits standard)."""
    d, m, y = int(code[:-5]), MONTHS[code[-5:-2]], 2000 + int(code[-2:])
    return datetime(y, m, d, 8, 0, tzinfo=timezone.utc)


def _gamma(S, K, T, iv):
    if S <= 0 or K <= 0 or T <= 0 or iv <= 0:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * iv * math.sqrt(T))


def compute():
    rows = requests.get(URL, timeout=15).json()["result"]
    now = datetime.now(timezone.utc)
    legs = []
    spot = None
    for r in rows:
        try:
            _, exp, k, cp = r["instrument_name"].split("-")
            oi = float(r.get("open_interest") or 0)
            iv = float(r.get("mark_iv") or 0) / 100
            if oi <= 0 or not (0.05 < iv < 5):
                continue
            ed = _exp_dt(exp)
            T = max((ed - now).total_seconds(), 3600) / (365 * 24 * 3600)
            if T * 365 > MAX_DAYS:
                continue
            legs.append((float(k), oi, iv, T, 1.0 if cp == "C" else -1.0, ed))
            if spot is None and r.get("estimated_delivery_price"):
                spot = float(r["estimated_delivery_price"])
        except Exception:
            continue
    if not legs or not spot:
        return None
    flip_exps = sorted({l[5] for l in legs})[:FLIP_EXPIRIES]

    def net_at(S, near=False):
        return sum(_gamma(S, K, T, iv) * oi * S * S * 0.01 * sg for K, oi, iv, T, sg, ed in legs
                   if not near or ed in flip_exps)

    per = {}
    for K, oi, iv, T, sg, ed in legs:
        per[K] = per.get(K, 0.0) + _gamma(spot, K, T, iv) * oi * spot * spot * 0.01 * sg
    near = {k: v for k, v in per.items() if abs(k / spot - 1) <= 0.15}
    call_wall = max(near, key=lambda k: near[k]) if near else None
    put_wall = min(near, key=lambda k: near[k]) if near else None
    # Gamma Flip: nollgenomgång närmast spot, spot flyttas ±10 % i steg om 0,25 %
    grid = [spot * (0.90 + 0.0025 * i) for i in range(81)]
    vals = [net_at(S, near=True) for S in grid]
    flip = None
    best = None
    for i in range(1, len(grid)):
        if vals[i - 1] == 0 or (vals[i - 1] < 0) != (vals[i] < 0):
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (-vals[i - 1]) / ((vals[i] - vals[i - 1]) or 1)
            if best is None or abs(x - spot) < best:
                best, flip = abs(x - spot), x
    net_now = net_at(spot, near=True)
    return {
        "generated": now.isoformat(timespec="seconds"),
        "levels": [{
            "inst": "BTC", "underlying": "BTC (Deribit index)", "source": "deribit", "fut_price": round(spot, 1),
            "regime": "positiv" if net_now >= 0 else "negativ",
            "call_wall": call_wall, "put_wall": put_wall, "zero_gamma": round(flip, 1) if flip else None,
            "expiries": sorted({l[5].date().isoformat() for l in legs}), "n_legs": len(legs),
        }],
    }


def _refresh():
    try:
        d = compute()
        if d:
            _C["data"] = d
            _C["t"] = time.time()
    except Exception as e:
        print(f"gex_btc: {e}")
    finally:
        _C["busy"] = False


def get():
    """Senaste BTC-GEX (räknas om i bakgrunden var 5:e minut)."""
    with _L:
        if not _C["busy"] and time.time() - _C["t"] > REFRESH_S:
            _C["busy"] = True
            threading.Thread(target=_refresh, daemon=True).start()
    return _C["data"]
