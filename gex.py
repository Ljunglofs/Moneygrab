"""
GEX  ·  gex.py
---------------
Gamma-exponering (dealer GEX) per strike från optionskedjor, översatt till
futures-nivåer. QQQ -> NQ, GLD -> GC.

Varför ETF-optioner: NQ har inget gratis optionsflöde, men QQQ (och NDX-
positionering via QQQ) styr samma dealers. Nivåerna översätts med live-kvoten
futures/ETF (NQ/QQQ ≈ 41, GC/GLD ≈ 10.9) som räknas fram vid varje uppdatering.

Modell (SpotGamma-stil, standardantagande dealers long calls / short puts):
    GEX_strike = Σ  Γ(S, K, T, σ) · OI · 100 · S² · 0,01      (calls +, puts −)
    Call wall  = strike med störst positiv GEX      (tak / magnet uppåt)
    Put wall   = strike med störst negativ GEX      (golv / stödzon)
    Zero gamma = spotnivå där nettoprofilen byter tecken
                 över zero gamma: dealers dämpar (mean reversion, fade kanterna)
                 under zero gamma: dealers förstärker (trend, bredare stopp)

Open interest uppdateras en gång per dygn -> ingen realtidsdata behövs.
Data via yfinance (gratis). Cache 30 min, senaste lyckade sparas på disk.
"""
import json
import math
import os
import time
import threading
from datetime import datetime, timezone

DATA_DIR = os.environ.get("DATA_DIR", ".")
CACHE_FILE = os.path.join(DATA_DIR, "desk_gex.json")
CACHE_SEC = int(os.environ.get("GEX_CACHE_SEC", "1800"))
RISK_FREE = 0.04
N_EXPIRIES = int(os.environ.get("GEX_EXPIRIES", "4"))    # närmaste expiries (0DTE + veckor + månad)

UNDERLYING = {"NQ": "QQQ", "GC": "GLD"}

_lock = threading.Lock()
_cache = {}      # inst -> {"t": epoch, "data": {...}}


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_gamma(S, K, T, sigma):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _norm_pdf(d1) / (S * sigma * math.sqrt(T))


def _years_to(expiry_str, now=None):
    """Optioner löper ut vid 16:00 ET ~ 20:00 UTC. Minst 1 timme kvar (0DTE-skydd)."""
    now = now or datetime.now(timezone.utc)
    y, m, d = (int(x) for x in expiry_str.split("-"))
    exp = datetime(y, m, d, 20, 0, tzinfo=timezone.utc)
    secs = max((exp - now).total_seconds(), 3600)
    return secs / (365 * 24 * 3600)


def gex_from_chain(spot, rows, now=None):
    """rows: iterable av dict(strike, oi, iv, type in {'C','P'}, expiry 'YYYY-MM-DD').
    Returnerar per-strike-tabell och nyckelnivåer (i UNDERLIGGANDES pris)."""
    per_strike = {}
    legs = []
    for r in rows:
        K = float(r["strike"]); oi = float(r.get("oi") or 0)
        if oi <= 0 or K <= 0:
            continue
        iv = float(r.get("iv") or 0)
        if not (0.01 < iv < 5):
            iv = 0.20
        T = _years_to(r["expiry"], now)
        sign = 1.0 if r["type"] == "C" else -1.0
        g = bs_gamma(spot, K, T, iv) * oi * 100 * spot * spot * 0.01 * sign
        per_strike[K] = per_strike.get(K, 0.0) + g
        legs.append((K, oi, iv, T, sign))
    if not per_strike:
        return None

    strikes = sorted(per_strike)
    net = sum(per_strike.values())
    call_wall = max(strikes, key=lambda k: per_strike[k])
    put_wall = min(strikes, key=lambda k: per_strike[k])

    # Zero gamma: nettoprofil när spot flyttas ±10 %
    def net_at(S):
        return sum(bs_gamma(S, K, T, iv) * oi * 100 * S * S * 0.01 * sign
                   for K, oi, iv, T, sign in legs)
    grid = [spot * (0.90 + 0.005 * i) for i in range(41)]
    vals = [net_at(S) for S in grid]
    zero = None
    for i in range(1, len(grid)):
        if vals[i - 1] < 0 <= vals[i] or vals[i - 1] > 0 >= vals[i]:
            a, b = vals[i - 1], vals[i]
            zero = grid[i - 1] + (grid[i] - grid[i - 1]) * (0 - a) / (b - a) if b != a else grid[i]
            break

    top = sorted(strikes, key=lambda k: -abs(per_strike[k]))[:7]
    return {
        "spot": spot, "net_gex": net, "regime": "positiv" if net > 0 else "negativ",
        "call_wall": call_wall, "put_wall": put_wall, "zero_gamma": zero,
        "top": [{"strike": k, "gex": per_strike[k]} for k in sorted(top)],
        "n_strikes": len(strikes),
    }


def _fetch_chain(underlying, n_exp=N_EXPIRIES):
    import yfinance as yf
    t = yf.Ticker(underlying)
    spot = None
    try:
        spot = float(t.fast_info.get("last_price") or 0) or None
    except Exception:
        pass
    exps = list(t.options or [])[:n_exp]
    rows = []
    for e in exps:
        try:
            ch = t.option_chain(e)
        except Exception:
            continue
        for typ, df in (("C", ch.calls), ("P", ch.puts)):
            for _, r in df.iterrows():
                rows.append({"strike": r.get("strike"), "oi": r.get("openInterest"),
                             "iv": r.get("impliedVolatility"), "type": typ, "expiry": e})
    if spot is None and rows:
        # fallback: ATM ~ strike med högst total OI nära mitten
        ks = sorted({float(r["strike"]) for r in rows})
        spot = ks[len(ks) // 2]
    return spot, rows, exps


def _load_disk():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_disk(all_data):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE) or ".", exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(all_data, f)
        os.replace(tmp, CACHE_FILE)
    except Exception as e:
        print("[gex] kunde inte spara cache:", e)


def to_futures(levels, fut_price, etf_price):
    """Skala ETF-nivåer till futures med live-kvot."""
    if not levels or not fut_price or not etf_price:
        return None
    r = fut_price / etf_price
    def sc(x):
        return round(x * r, 2) if x is not None else None
    return {
        "ratio": round(r, 4), "fut_price": fut_price, "etf_price": etf_price,
        "net_gex": levels["net_gex"], "regime": levels["regime"],
        "call_wall": sc(levels["call_wall"]), "put_wall": sc(levels["put_wall"]),
        "zero_gamma": sc(levels["zero_gamma"]),
        "top": [{"level": sc(t["strike"]), "strike": t["strike"], "gex": t["gex"]} for t in levels["top"]],
    }


def get_gex(inst, fut_price=None, force=False):
    """Huvudingång. inst 'NQ'|'GC'. Returnerar dict med ETF-nivåer + futures-nivåer
    (om fut_price ges) eller None. Aldrig exception utåt."""
    und = UNDERLYING.get(inst)
    if not und:
        return None
    with _lock:
        c = _cache.get(inst)
        if c and not force and time.time() - c["t"] < CACHE_SEC:
            data = c["data"]
        else:
            data = None
            try:
                spot, rows, exps = _fetch_chain(und)
                lv = gex_from_chain(spot, rows) if rows else None
                if lv:
                    data = {"underlying": und, "expiries": exps, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "levels": lv, "stale": False}
            except Exception as e:
                print(f"[gex] {und}: {type(e).__name__}: {e}")
            if data:
                _cache[inst] = {"t": time.time(), "data": data}
                disk = _load_disk(); disk[inst] = data; _save_disk(disk)
            else:
                data = (c or {}).get("data") or _load_disk().get(inst)
                if data:
                    data = dict(data); data["stale"] = True
                    _cache[inst] = {"t": time.time() - CACHE_SEC + 300, "data": data}   # försök igen om 5 min
    if not data:
        return None
    out = dict(data)
    if fut_price:
        out["futures"] = to_futures(data["levels"], fut_price, data["levels"]["spot"])
    return out


def gex_text(inst, g):
    if not g:
        return f"GEX {inst}: ingen data (Yahoo nåddes inte, ingen cache)."
    lv = g["levels"]; f = g.get("futures")
    name = {"NQ": "NQ (via QQQ)", "GC": "GC (via GLD)"}.get(inst, inst)
    bn = lv["net_gex"] / 1e9
    lines = [f"\U0001F9F2 <b>GAMMA · {name}</b>" + (" ⚠ gammal data" if g.get("stale") else ""),
             f"Regim: <b>{lv['regime']}</b> (netto {bn:+.2f} mdr USD/1 %) — "
             + ("dealers dämpar: fade kanterna, mean reversion" if lv["net_gex"] > 0
                else "dealers förstärker: trendläge, bredare stopp")]
    if f:
        lines.append(f"Call wall: <b>{f['call_wall']}</b> · Put wall: <b>{f['put_wall']}</b>"
                     + (f" · Zero gamma: <b>{f['zero_gamma']}</b>" if f["zero_gamma"] else ""))
        lines.append("Största strikes: " + ", ".join(f"{t['level']:.0f}" for t in f["top"]))
        lines.append(f"<i>{g['underlying']} {lv['spot']:.2f} · kvot {f['ratio']} · exp {', '.join(g['expiries'][:3])}</i>")
    else:
        lines.append(f"{g['underlying']}: call wall {lv['call_wall']} · put wall {lv['put_wall']}"
                     + (f" · zero gamma {lv['zero_gamma']:.2f}" if lv["zero_gamma"] else ""))
    return "\n".join(lines)
