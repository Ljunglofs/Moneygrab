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
    hgex = top[0] if top else None
    gpos = [k for k in sorted(strikes, key=lambda k: -per_strike[k]) if per_strike[k] > 0 and k != call_wall][:3]
    gneg = [k for k in sorted(strikes, key=lambda k: per_strike[k]) if per_strike[k] < 0 and k != put_wall][:3]

    # --- Närmaste expiry: 0DTE-väggar, max pain, ATM-straddle (expected move), IV ---
    rows = list(rows)
    exps = sorted({r["expiry"] for r in rows if r.get("oi")})
    near = exps[0] if exps else None
    nr = [r for r in rows if r["expiry"] == near and float(r.get("oi") or 0) > 0]
    ps0 = {}
    for r in nr:
        K = float(r["strike"]); T = _years_to(near, now)
        iv = float(r.get("iv") or 0); iv = iv if 0.01 < iv < 5 else 0.20
        sign = 1.0 if r["type"] == "C" else -1.0
        ps0[K] = ps0.get(K, 0.0) + bs_gamma(spot, K, T, iv) * float(r["oi"]) * 100 * spot * spot * 0.01 * sign
    cw0 = max(ps0, key=lambda k: ps0[k]) if ps0 else None
    pw0 = min(ps0, key=lambda k: ps0[k]) if ps0 else None

    mpain = None
    if nr:
        ks0 = sorted({float(r["strike"]) for r in nr})
        def pain(S):
            return sum((max(0.0, S - float(r["strike"])) if r["type"] == "C" else max(0.0, float(r["strike"]) - S)) * float(r["oi"]) for r in nr)
        mpain = min(ks0, key=pain)

    def _mid(r):
        b, a = float(r.get("bid") or 0), float(r.get("ask") or 0)
        if b > 0 and a > 0:
            return (a + b) / 2
        return float(r.get("last") or 0) or None
    em = iv_atm = None
    if nr:
        atm = min({float(r["strike"]) for r in nr}, key=lambda k: abs(k - spot))
        c = [r for r in nr if r["type"] == "C" and float(r["strike"]) == atm]
        pu = [r for r in nr if r["type"] == "P" and float(r["strike"]) == atm]
        if c and pu:
            cm, pm = _mid(c[0]), _mid(pu[0])
            if cm and pm:
                em = cm + pm                       # ATM-straddle ≈ marknadens förväntade rörelse till expiry
            ivs = [float(r.get("iv") or 0) for r in (c[0], pu[0]) if 0.01 < float(r.get("iv") or 0) < 5]
            iv_atm = sum(ivs) / len(ivs) if ivs else None
    iv_1d = spot * iv_atm * math.sqrt(1 / 252) if iv_atm else None

    return {
        "spot": spot, "net_gex": net, "regime": "positiv" if net > 0 else "negativ",
        "call_wall": call_wall, "put_wall": put_wall, "zero_gamma": zero,
        "top": [{"strike": k, "gex": per_strike[k]} for k in sorted(top)],
        "n_strikes": len(strikes),
        "hgex": hgex, "gpos": gpos, "gneg": gneg,
        "near_expiry": near, "call_wall_0": cw0, "put_wall_0": pw0,
        "max_pain": mpain, "em_straddle": em, "iv_atm": iv_atm, "iv_1d": iv_1d,
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
        def _n(v):
            try:
                v = float(v)
                return 0.0 if v != v else v          # NaN -> 0
            except Exception:
                return 0.0
        for typ, df in (("C", ch.calls), ("P", ch.puts)):
            for _, r in df.iterrows():
                rows.append({"strike": _n(r.get("strike")), "oi": _n(r.get("openInterest")),
                             "iv": _n(r.get("impliedVolatility")), "type": typ, "expiry": e,
                             "bid": _n(r.get("bid")), "ask": _n(r.get("ask")), "last": _n(r.get("lastPrice"))})
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
        "hgex": sc(levels.get("hgex")), "gpos": [sc(k) for k in levels.get("gpos", [])],
        "gneg": [sc(k) for k in levels.get("gneg", [])],
        "call_wall_0": sc(levels.get("call_wall_0")), "put_wall_0": sc(levels.get("put_wall_0")),
        "max_pain": sc(levels.get("max_pain")), "em": sc(levels.get("em_straddle")),
        "iv_1d": sc(levels.get("iv_1d")), "near_expiry": levels.get("near_expiry"),
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


# ---------------------------------------------------------------- TradingView-sträng
TICK = {"NQ": 0.25, "GC": 0.10}


def _fmt_px(x, tick):
    x = round(x / tick) * tick
    return f"{x:.2f}".rstrip("0").rstrip(".") if tick < 1 else f"{x:.0f}"


def levels_string(inst, g, open_price=None, atr_daily=None, today=None):
    """Bygger strängen till Pine-indikatorn 'GEX Daily Levels':
       pris,etikett,typ;...  med typerna res/sup/res0/sup0/flip/hgex/mpain/gpos/gneg/
       emh/eml/emb/ivh/ivl/opo/opu/opd. Etiketter utan komma/semikolon."""
    if not g or not g.get("futures"):
        return ""
    f = g["futures"]; tick = TICK.get(inst, 0.25)
    S = f["fut_price"]
    parts = []
    def add(px, label, kind):
        if px is not None and px > 0:
            parts.append(f"{_fmt_px(px, tick)},{label},{kind}")
    add(f.get("call_wall"), "Call Wall", "res")
    add(f.get("put_wall"), "Put Wall", "sup")
    ne = f.get("near_expiry") or ""
    tag0 = "0DTE" if (today and ne == str(today)) else (ne[5:] if ne else "near")
    if f.get("call_wall_0") and f["call_wall_0"] != f.get("call_wall"):
        add(f["call_wall_0"], f"Call Wall {tag0}", "res0")
    if f.get("put_wall_0") and f["put_wall_0"] != f.get("put_wall"):
        add(f["put_wall_0"], f"Put Wall {tag0}", "sup0")
    add(f.get("zero_gamma"), "Gamma Flip", "flip")
    if f.get("hgex") and f["hgex"] not in (f.get("call_wall"), f.get("put_wall")):
        add(f["hgex"], "HGEX", "hgex")
    add(f.get("max_pain"), f"Max Pain {tag0}", "mpain")
    for i, k in enumerate(f.get("gpos", [])[:2], 1):
        add(k, f"G+ {i}", "gpos")
    for i, k in enumerate(f.get("gneg", [])[:2], 1):
        add(k, f"G- {i}", "gneg")
    em = f.get("em")
    if em:
        add(S + em, "EM+", "emh"); add(S - em, "EM-", "eml")
        add(S + 0.5 * em, "EM +50%", "emb"); add(S - 0.5 * em, "EM -50%", "emb")
    iv = f.get("iv_1d")
    if iv:
        add(S + iv, "1D High IV", "ivh"); add(S - iv, "1D Low IV", "ivl")
    if open_price:
        add(open_price, "Open", "opo")
        if atr_daily:
            for m in (0.5, 1.0):
                add(open_price + m * atr_daily, f"O +{m:g} ATR", "opu")
                add(open_price - m * atr_daily, f"O -{m:g} ATR", "opd")
    return ";".join(parts)
