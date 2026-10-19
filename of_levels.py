"""Tillägg till orderflow-portalen — rör inte motorn (orderflow_engine.py).

1. Nivåer: sessionens high/low, Asia/London high/low (från motorns 1m-bars) och gårdagens
   high/low/POC/VAH/VAL (från Hyperliquids 5m-candles för samma marknad).
2. Profiler: föregående session och composite (senaste 5 sessionerna).
3. Sannolikhet att nå en nivå (Quant Probability Engine):
   - Historik: ~20 dagar 5m-candles. För varje bar och horisont (15/30/60 min, sessionens slut)
     mäts om priset nådde +/- d, där d = z * sigma * sqrt(k) (sigma = realiserad 5m-volatilitet).
   - Baslinje: historisk träff-frekvens per z-hink.
   - Modell: logistisk regression på z, z^2, momentum mot nivån och läge mot VWAP.
   - Walk-forward: tränas på de första 70 % av sessionerna, testas på resten (Brier score).
     Modellen används bara om den slår baslinjen, annars baslinjen. För lite data -> N/A.
"""
import math
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import requests

HL_INFO = "https://api.hyperliquid.xyz/info"
NY = ZoneInfo("America/New_York")
COIN = {"NQ": ("xyz:XYZ100", 1.0), "GC": ("xyz:GOLD", 0.5), "BTC": ("BTC", 10.0)}
HORIZONS = {"15m": 3, "30m": 6, "60m": 12, "close": None}   # i 5m-bars; close = till 17:00 New York
Z_GRID = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
Z_EDGES = (0.375, 0.625, 0.875, 1.125, 1.375, 1.75, 2.25, 2.75, 3.5)
SIG_N = 48            # realiserad volatilitet: senaste 48 x 5m = 4 h
HIST_DAYS = 20
REFRESH_S = 180       # nya candles var 3:e minut
REFIT_S = 3600        # modellen tränas om varje timme

_C = {}               # inst -> {"bars": [...], "t": ts, "fit_t": ts, "model": {...}, "busy": bool}
_LOCK = threading.Lock()


# ── tid / sessioner ───────────────────────────────────────────────────────
def _ny(ms):
    return datetime.fromtimestamp(ms / 1000, NY)


def _tday(ms):
    d = _ny(ms)
    return (d + timedelta(days=1)).date() if d.hour >= 18 else d.date()


def _closed(ms):
    """CME är stängt: fredag 17:00 – söndag 18:00 New York (Hyperliquid handlas ändå)."""
    d = _ny(ms)
    wd = d.weekday()
    return wd == 5 or (wd == 4 and d.hour >= 17) or (wd == 6 and d.hour < 18)


def _bars_to_close(ms):
    """Antal 5m-bars kvar till sessionens slut (17:00 New York)."""
    d = _ny(ms)
    end = d.replace(hour=17, minute=0, second=0, microsecond=0)
    if d.hour >= 17:
        end += timedelta(days=1)
    return max(0, int((end - d).total_seconds() // 300))


# ── data ──────────────────────────────────────────────────────────────────
def _fetch(coin, days):
    now = int(time.time() * 1000)
    out = {}
    start = now - days * 86400000
    while start < now:
        end = min(now, start + 10 * 86400000)     # 10 dagar = 2880 bars < 5000-gränsen
        try:
            cs = requests.post(HL_INFO, json={"type": "candleSnapshot", "req": {
                "coin": coin, "interval": "5m", "startTime": start, "endTime": end}}, timeout=20).json()
        except Exception:
            cs = []
        for c in cs or []:
            out[int(c["t"])] = (int(c["t"]), float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c["v"]))
        start = end
    return [out[k] for k in sorted(out)]


def _profile(bars, row):
    m = {}
    for t, o, h, l, c, v in bars:
        p = round(((h + l + c) / 3) / row) * row
        m[p] = m.get(p, 0.0) + v
    ks = sorted(m)
    if len(ks) < 3:
        return None
    vs = [m[k] for k in ks]
    tot = sum(vs)
    pi = max(range(len(vs)), key=lambda i: vs[i])
    up = dn = pi
    acc = vs[pi]
    n = len(ks)
    while acc < 0.7 * tot and (up < n - 1 or dn > 0):
        vu = sum(vs[up + 1:up + 3])
        vd = sum(vs[max(0, dn - 2):dn])
        if (vu >= vd and up < n - 1) or dn == 0:
            st = min(2, n - 1 - up); acc += sum(vs[up + 1:up + 1 + st]); up += st
        else:
            st = min(2, dn); acc += sum(vs[dn - st:dn]); dn -= st
    return {"poc": ks[pi], "vah": ks[up], "val": ks[dn],
            "rows": [{"px": k, "v": round(m[k], 3)} for k in ks]}


FINE = {"NQ": 0.25, "GC": 0.1, "BTC": 5.0}
MAX_ROWS = 900


def _fine_profile(hlv, base, va=True):
    """Volymprofil som i TradingView: varje bars volym sprids jämnt över dess high–low.
    Radstorleken är base (NQ 0,25 / guld 0,1), grövre bara om det annars blir > MAX_ROWS rader."""
    if not hlv:
        return None
    lo = min(l for h, l, v in hlv); hi = max(h for h, l, v in hlv)
    step = base * max(1, math.ceil((hi - lo) / base / MAX_ROWS))
    n = int(round((hi - lo) / step)) + 1
    if n < 3:
        return None
    acc = np.zeros(n)
    for h, l, v in hlv:
        a = int(round((l - lo) / step)); b = int(round((h - lo) / step))
        acc[a:b + 1] += v / (b - a + 1)
    out = {"step": step, "rows": [{"px": round(lo + i * step, 4), "v": round(float(acc[i]), 4)} for i in range(n) if acc[i] > 0]}
    if va:
        tot = acc.sum(); pi = int(acc.argmax()); up = dn = pi; s = acc[pi]
        while s < 0.7 * tot and (up < n - 1 or dn > 0):
            vu = acc[up + 1:up + 3].sum(); vd = acc[max(0, dn - 2):dn].sum()
            if (vu >= vd and up < n - 1) or dn == 0:
                st = min(2, n - 1 - up); s += acc[up + 1:up + 1 + st].sum(); up += st
            else:
                st = min(2, dn); s += acc[dn - st:dn].sum(); dn -= st
        out.update({"poc": round(lo + pi * step, 4), "vah": round(lo + up * step, 4), "val": round(lo + dn * step, 4)})
    return out


def _sessions(bars):
    days = {}
    for b in bars:
        days.setdefault(_tday(b[0]), []).append(b)
    return days


# ── sannolikhetsmodell ────────────────────────────────────────────────────
def _sig(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _logit_fit(X, y, lam=1e-3, it=30):
    w = np.zeros(X.shape[1])
    for _ in range(it):
        p = _sig(X @ w)
        g = X.T @ (y - p) - lam * w
        Hm = (X * (p * (1 - p))[:, None]).T @ X + lam * np.eye(X.shape[1])
        step = np.linalg.solve(Hm, g)
        w += step
        if np.max(np.abs(step)) < 1e-6:
            break
    return w


def _feats(z, mom, vwz):
    return np.column_stack([np.ones_like(z), z, z * z, mom, vwz])


def _samples(bars, cme=True):
    """Bygger (z, mom, vwz, hit, sessionsindex) per horisont från 5m-bars."""
    n = len(bars)
    if n < SIG_N + 20:
        return None
    T = np.array([b[0] for b in bars], dtype=np.int64)
    H = np.array([b[2] for b in bars]); L = np.array([b[3] for b in bars]); C = np.array([b[4] for b in bars])
    V = np.array([b[5] for b in bars]); HLC = (H + L + C) / 3
    dC = np.diff(C, prepend=C[0])
    sig = np.array([np.std(dC[max(1, i - SIG_N + 1):i + 1]) if i >= SIG_N else np.nan for i in range(n)])
    tdays = [_tday(int(t)) for t in T]
    sid = np.zeros(n, dtype=int)
    vw = np.zeros(n)
    pv = vv = 0.0
    s = 0
    for i in range(n):
        if i and tdays[i] != tdays[i - 1]:
            s += 1; pv = vv = 0.0
        sid[i] = s
        pv += HLC[i] * V[i]; vv += V[i]
        vw[i] = pv / vv if vv else C[i]
    last_idx = {}
    for i in range(n):
        last_idx[sid[i]] = i
    mom6 = np.sign(C - np.concatenate([np.full(6, C[0]), C[:-6]]))
    out = {}
    for hz, k0 in HORIZONS.items():
        rows = []
        for i in range(SIG_N, n - 1):
            if not sig[i] or np.isnan(sig[i]):
                continue
            if cme and _closed(int(T[i])):
                continue
            k = k0 if k0 else _bars_to_close(int(T[i]))
            if k < 1 or i + k >= n:
                continue
            if k0 is None and last_idx[sid[i]] - i < k:     # sessionen saknas i datan
                continue
            hi = H[i + 1:i + k + 1].max(); lo = L[i + 1:i + k + 1].min()
            sc = sig[i] * math.sqrt(k)
            if sc <= 0:
                continue
            for z in Z_GRID:
                d = z * sc
                for dr in (1, -1):
                    hit = (hi >= C[i] + d) if dr > 0 else (lo <= C[i] - d)
                    rows.append((z, mom6[i] * dr, np.clip(dr * (C[i] - vw[i]) / sc, -3, 3), float(hit), sid[i]))
        if rows:
            out[hz] = np.array(rows)
    return out


HOLD_R = {"NQ": 10.0, "GC": 3.0, "BTC": 150.0}     # reaktionsavstånd för håller/bryts


def _hold_stats(bars, row, R, cme=True):
    """Hur ofta nivåtyper har HÅLLIT när priset rörde dem (studs R innan R igenom),
    jämfört med slumpnivåer på samma avstånd. Per typ: [håller, antal]."""
    days = _sessions(bars)
    keys = [k for k in sorted(days) if not cme or k.weekday() < 5]   # CME: helgsessioner räknas inte
    stats, ctrl = {}, [0, 0]
    rng = np.random.default_rng(7)

    def test(level, seq):
        above = seq[0][4] > level
        touched = False
        for b in seq[1:]:
            if not touched:
                if (above and b[3] <= level) or (not above and b[2] >= level):
                    touched = True
                else:
                    continue
            brk = (b[3] <= level - R) if above else (b[2] >= level + R)
            hold = (b[2] >= level + R) if above else (b[3] <= level - R)
            if brk:
                return "break"
            if hold:
                return "hold"
        return None

    for i in range(1, len(keys)):
        pb, cb = days[keys[i - 1]], days[keys[i]]
        if len(pb) < 50 or len(cb) < 50:
            continue
        prof = _profile(pb, row) or {}
        lv = {"prevH": (max(b[2] for b in pb), cb[0][0]), "prevL": (min(b[3] for b in pb), cb[0][0]),
              "prevPOC": (prof.get("poc"), cb[0][0]), "prevVAH": (prof.get("vah"), cb[0][0]),
              "prevVAL": (prof.get("val"), cb[0][0])}
        asia = [b for b in cb if _ny(b[0]).hour >= 18 or _ny(b[0]).hour < 2]
        lon = [b for b in cb if 2 <= _ny(b[0]).hour < 8]
        if asia:
            tr = asia[-1][0] + 300000
            lv["asiaH"] = (max(b[2] for b in asia), tr); lv["asiaL"] = (min(b[3] for b in asia), tr)
        if lon:
            tr = lon[-1][0] + 300000
            lv["lonH"] = (max(b[2] for b in lon), tr); lv["lonL"] = (min(b[3] for b in lon), tr)
        for name, (px, t_ready) in lv.items():
            if px is None:
                continue
            seq = [b for b in cb if b[0] >= t_ready]
            if len(seq) < 3:
                continue
            res = test(px, seq)
            if res:
                st = stats.setdefault(name, [0, 0]); st[1] += 1; st[0] += res == "hold"
            d = abs(px - seq[0][4]) * rng.uniform(0.5, 1.5)
            rc = test(seq[0][4] + (d if px > seq[0][4] else -d), seq)
            if rc:
                ctrl[1] += 1; ctrl[0] += rc == "hold"
    return {"types": stats, "baseline": ctrl}


def _fit(bars, cme=True):
    S = _samples(bars, cme)
    if not S:
        return None
    model = {}
    for hz, A in S.items():
        z, mom, vwz, y, sid = A[:, 0], A[:, 1], A[:, 2], A[:, 3], A[:, 4]
        nsess = int(sid.max()) + 1
        cut = int(nsess * 0.7)
        tr, te = sid < cut, sid >= cut
        bucket = np.digitize(z, Z_EDGES)
        if tr.sum() < 500 or te.sum() < 200:
            model[hz] = {"ok": False, "n": int(len(y))}
            continue
        base_tab = np.array([y[tr & (bucket == b)].mean() if (tr & (bucket == b)).any() else np.nan for b in range(len(Z_GRID))])
        w = _logit_fit(_feats(z[tr], mom[tr], vwz[tr]), y[tr])
        p_m = _sig(_feats(z[te], mom[te], vwz[te]) @ w)
        p_b = np.nan_to_num(base_tab[bucket[te]], nan=y[tr].mean())
        brier_m = float(np.mean((p_m - y[te]) ** 2))
        brier_b = float(np.mean((p_b - y[te]) ** 2))
        # tillförlitlighet: förutsagt vs utfall i 5 hinkar (testdata)
        rel = []
        for a, b in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
            m = (p_m >= a) & (p_m < b)
            if m.sum() >= 30:
                rel.append([round(float(p_m[m].mean()), 3), round(float(y[te][m].mean()), 3), int(m.sum())])
        use_model = brier_m <= brier_b
        # slutlig modell tränas på all data (walk-forward-testet ovan avgör bara metoden)
        w_all = _logit_fit(_feats(z, mom, vwz), y)
        base_all = np.array([y[bucket == b].mean() if (bucket == b).any() else np.nan for b in range(len(Z_GRID))])
        model[hz] = {"ok": True, "w": w_all.tolist(), "base": np.nan_to_num(base_all, nan=0.0).tolist(),
                     "method": "logistic" if use_model else "baseline", "brier": round(brier_m, 4),
                     "brier_base": round(brier_b, 4), "n_train": int(tr.sum()), "n_test": int(te.sum()),
                     "sessions": nsess, "reliability": rel}
    return model


def _predict(mod, z, mom, vwz):
    if not mod or not mod.get("ok"):
        return None
    if mod["method"] == "logistic":
        zz = min(z, 4.0)
        p = float(_sig(_feats(np.array([zz]), np.array([mom]), np.array([vwz])) @ np.array(mod["w"]))[0])
    else:
        p = float(mod["base"][int(np.digitize([min(z, 4.0)], Z_EDGES)[0])])
    if z > 4.0:            # utanför träningsdatan: avtar som normalfördelningens svans
        p *= math.exp(-(z * z - 16) / 2)
    return max(0.0, min(1.0, p))


# ── uppdatering i bakgrunden ──────────────────────────────────────────────
def _refresh(inst):
    coin, row = COIN[inst]
    c = _C.setdefault(inst, {"bars": [], "t": 0, "fit_t": 0, "model": None, "busy": False})
    try:
        if not c["bars"]:
            bars = _fetch(coin, HIST_DAYS)
        else:
            new = _fetch(coin, 1)
            m = {b[0]: b for b in c["bars"]}
            m.update({b[0]: b for b in new})
            cut = int(time.time() * 1000) - (HIST_DAYS + 1) * 86400000
            bars = [m[k] for k in sorted(m) if k >= cut]
        if bars:
            c["bars"] = bars
            c["t"] = time.time()
        if bars and time.time() - c["fit_t"] > REFIT_S:
            mdl = _fit(bars[:-1], cme=inst != "BTC")   # sista (pågående) baren utesluts; BTC handlas även helger
            if mdl:
                try:
                    mdl["hold"] = _hold_stats(bars[:-1], row, HOLD_R.get(inst, 1.0), cme=inst != "BTC")
                except Exception as e:
                    print(f"of_levels hold {inst}: {e}")
                c["model"] = mdl
                c["fit_t"] = time.time()
    except Exception as e:
        print(f"of_levels {inst}: {e}")
    finally:
        c["busy"] = False


def _ensure(inst):
    with _LOCK:
        c = _C.setdefault(inst, {"bars": [], "t": 0, "fit_t": 0, "model": None, "busy": False})
        if not c["busy"] and time.time() - c["t"] > REFRESH_S:
            c["busy"] = True
            threading.Thread(target=_refresh, args=(inst,), daemon=True).start()
    return c


# ── CME-profil för NQ/GC (1m futuresstaplar med volym, fördelad över stapelns spann) ──
YF = {"NQ": "NQ=F", "GC": "GC=F"}
_CME = {}


def _cme_refresh(inst):
    c = _CME[inst]
    try:
        import yfinance as yf
        h = yf.Ticker(YF[inst]).history(period="5d", interval="1m")
        rows = [(int(ts.timestamp() * 1000), float(r["High"]), float(r["Low"]), float(r["Close"]), float(r["Volume"]))
                for ts, r in h.iterrows() if r["Volume"] == r["Volume"]]
        if rows:
            c["bars"] = rows
            c["t"] = time.time()
    except Exception as e:
        print(f"of_levels cme {inst}: {e}")
    finally:
        c["busy"] = False


def cme_bars(inst):
    """CME-staplar (t_ms, high, low, close, volym) — uppdateras i bakgrunden varje minut."""
    if inst not in YF:
        return []
    with _LOCK:
        c = _CME.setdefault(inst, {"bars": [], "t": 0, "busy": False})
        if not c["busy"] and time.time() - c["t"] > 60:
            c["busy"] = True
            threading.Thread(target=_cme_refresh, args=(inst,), daemon=True).start()
    return c["bars"]


def _cme_profiles(inst, td, B):
    """Dagens och gårdagens profil ur CME-volymen, flyttad till orderflödets pris (− basis B)."""
    cb = cme_bars(inst)
    if not cb or B is None:
        return None, None
    by = {}
    for b in cb:
        by.setdefault(_tday(b[0]), []).append(b)

    def prof(rows):
        if not rows or len(rows) < 30:
            return None
        f = _fine_profile([(h, l, v) for t, h, l, c_, v in rows if v > 0], FINE[inst])
        if not f:
            return None
        f["rows"] = [{"px": round(r["px"] - B, 4), "v": r["v"]} for r in f["rows"]]
        for k in ("poc", "vah", "val"):
            f[k] = round(f[k] - B, 2)
        f["hi"] = round(max(h for t, h, l, c_, v in rows) - B, 2)
        f["lo"] = round(min(l for t, h, l, c_, v in rows) - B, 2)
        f["src"] = "cme"
        return f
    prev_keys = [k for k in sorted(by) if k < td and k.weekday() < 5]
    return prof(by.get(td)), (prof(by[prev_keys[-1]]) if prev_keys else None)


# ── tillägg till motorns snapshot ─────────────────────────────────────────
def augment(snap):
    """Lägger till levels, sannolikheter och extra profiler i ett (fullt) snapshot."""
    inst = snap.get("inst")
    if inst not in COIN or not snap.get("bars") or snap.get("price") is None:
        return snap
    c = _ensure(inst)
    row = COIN[inst][1]
    price = snap["price"]
    bars = snap["bars"]
    now_ms = bars[-1]["t"] * 1000
    td = _tday(now_ms)
    sess = [b for b in bars if _tday(b["t"] * 1000) == td]

    def ext(f):
        s = [b for b in sess if f(_ny(b["t"] * 1000))]
        return (max(b["h"] for b in s), min(b["l"] for b in s)) if s else (None, None)

    sH, sL = ext(lambda d: True)
    aH, aL = ext(lambda d: d.hour >= 18 or d.hour < 2)
    lH, lL = ext(lambda d: 2 <= d.hour < 8)
    lv = []

    def add(name, px, kind, group):
        if px is not None:
            lv.append({"name": name, "px": round(px, 2), "kind": kind, "group": group})

    add("Session High", sH, "session", "session"); add("Session Low", sL, "session", "session")
    if aH is not None and not (_ny(now_ms).hour >= 18 or _ny(now_ms).hour < 2):
        add("Asia High", aH, "session", "session"); add("Asia Low", aL, "session", "session")
    if lH is not None and _ny(now_ms).hour >= 8 and _ny(now_ms).hour < 18:
        add("London High", lH, "session", "session"); add("London Low", lL, "session", "session")

    hist = c["bars"]
    prof_prev = prof_comp = None
    if hist:
        days = _sessions(hist)
        prev_keys = [k for k in sorted(days) if k < td and (inst == "BTC" or k.weekday() < 5)]
        if prev_keys:
            pb = days[prev_keys[-1]]
            add("Yesterday's High", max(b[2] for b in pb), "prev", "prev")
            add("Yesterday's Low", min(b[3] for b in pb), "prev", "prev")
            prof_prev = _profile(pb, row)
            if prof_prev:
                add("Yesterday's POC", prof_prev["poc"], "prev", "prev")
                add("Yesterday's VAH", prof_prev["vah"], "prev", "prev")
                add("Yesterday's VAL", prof_prev["val"], "prev", "prev")
            comp = [b for k in prev_keys[-4:] for b in days[k]] + [b for b in hist if _tday(b[0]) == td]
            prof_comp = _fine_profile([(b[2], b[3], b[5]) for b in comp], FINE[inst])
            fp = _fine_profile([(b[2], b[3], b[5]) for b in pb], FINE[inst])
            if fp and prof_prev:          # visningen fin, nivåerna (POC/VAH/VAL) som ovan
                fp.update({k: prof_prev[k] for k in ("poc", "vah", "val")})
            prof_prev = fp or prof_prev

    cme_sess = cme_prev = None
    if inst in YF:
        cme_sess, cme_prev = _cme_profiles(inst, td, snap.get("basis"))
        if cme_sess:
            snap["poc"], snap["vah"], snap["val"] = cme_sess["poc"], cme_sess["vah"], cme_sess["val"]
        if cme_prev:
            prof_prev = cme_prev
            for L0 in lv:
                k_ = {"Yesterday's POC": "poc", "Yesterday's VAH": "vah", "Yesterday's VAL": "val",
                      "Yesterday's High": "hi", "Yesterday's Low": "lo"}.get(L0["name"])
                if k_:
                    L0["px"] = round(cme_prev[k_], 2)
    snap["profile_src"] = "cme" if cme_sess else "flow"
    add("POC", snap.get("poc"), "profile", "profile")
    add("VAH", snap.get("vah"), "profile", "profile")
    add("VAL", snap.get("val"), "profile", "profile")
    add("VWAP", snap.get("vwap"), "vwap", "profile")
    for g in snap.get("gex") or []:
        if g.get("px") is not None:
            add(g["label"], g["px"], "gex", "gex")
    for s in (snap.get("shelves") or [])[:4]:
        add("Buyer shelf" if s["side"] == "bid" else "Seller shelf", s["px"], "bid" if s["side"] == "bid" else "ask", "liquidity")

    # sannolikheter
    mdl = c.get("model")
    sig = None
    if hist and len(hist) > SIG_N + 1:
        cl = np.array([b[4] for b in hist[-SIG_N - 1:]])
        sig = float(np.std(np.diff(cl)))
    p30 = bars[-31]["c"] if len(bars) > 31 else bars[0]["c"]
    vwap = snap.get("vwap") or price
    k_close = max(1, _bars_to_close(int(time.time() * 1000)))
    for L in lv:
        dist = L["px"] - price
        L["dist"] = round(dist, 2)
        L["touched"] = sH is not None and sL <= L["px"] <= sH
        L["p"] = {}
        if not mdl or not sig:
            continue
        dr = 1 if dist >= 0 else -1
        for hz, k0 in HORIZONS.items():
            k = k0 or k_close
            sc = sig * math.sqrt(k)
            if sc <= 0:
                continue
            z = abs(dist) / sc
            mom = float(np.sign(price - p30)) * dr
            vwz = max(-3.0, min(3.0, dr * (price - vwap) / sc))
            p = _predict(mdl.get(hz), z, mom, vwz)
            if p is not None:
                L["p"][hz] = round(p, 3)
    HT = {"Yesterday's High": "prevH", "Yesterday's Low": "prevL", "Yesterday's POC": "prevPOC",
          "Yesterday's VAH": "prevVAH", "Yesterday's VAL": "prevVAL", "Asia High": "asiaH", "Asia Low": "asiaL",
          "London High": "lonH", "London Low": "lonL"}
    hs = (mdl or {}).get("hold") or {}
    for L0 in lv:
        st = (hs.get("types") or {}).get(HT.get(L0["name"], ""))
        if st and st[1] >= 5:
            L0["hold"] = [round(st[0] / st[1], 3), st[1]]
    bl = hs.get("baseline") or [0, 0]
    snap["hold_base"] = [round(bl[0] / bl[1], 3), bl[1]] if bl[1] else None
    # sannolikhetskurva: P(nå) för valfritt avstånd uppåt/nedåt, i steg om 0,1 sigma*sqrt(k)
    curve = {}
    if mdl and sig:
        for hz, k0 in HORIZONS.items():
            k = k0 or k_close
            sc = sig * math.sqrt(k)
            if sc <= 0 or not (mdl.get(hz) or {}).get("ok"):
                continue
            row_ = {"sc": round(sc, 4)}
            for side, dr in (("up", 1), ("dn", -1)):
                mom = float(np.sign(price - p30)) * dr
                vwz = max(-3.0, min(3.0, dr * (price - vwap) / sc))
                row_[side] = [round(_predict(mdl[hz], z / 10, mom, vwz) or 0, 4) for z in range(0, 61)]
            curve[hz] = row_
    snap["pcurve"] = curve
    snap["levels"] = lv
    meta = {}
    for hz in HORIZONS:
        m = (mdl or {}).get(hz) or {}
        if m.get("ok"):
            q = "calibrated" if m["n_test"] >= 20000 and m["sessions"] >= 10 else "preliminary"
            meta[hz] = {k: m[k] for k in ("method", "brier", "brier_base", "n_train", "n_test", "sessions", "reliability")}
            meta[hz]["quality"] = q
        else:
            meta[hz] = {"quality": "n/a"}
    snap["prob_meta"] = {"horizons": meta, "sigma_5m": round(sig, 3) if sig else None,
                         "fitted": int(c["fit_t"]) or None, "source": "Hyperliquid 5m candles, %d days" % HIST_DAYS}
    # relativ volym: sessionens volym hittills mot snittet för de 5 senaste sessionerna vid samma tid
    rvol = None
    if hist:
        days = _sessions(hist)
        prev_keys = [k for k in sorted(days) if k < td and (inst == "BTC" or k.weekday() < 5)][-5:]
        now_t = hist[-1][0]
        cur = [b for b in days.get(td, [])]
        if cur and prev_keys:
            el = now_t - cur[0][0]
            refs = []
            for k in prev_keys:
                db = days[k]
                if len(db) > 20:
                    refs.append(sum(b[5] for b in db if b[0] - db[0][0] <= el))
            ref = sum(refs) / len(refs) if refs else 0
            if ref > 0:
                rvol = round(sum(b[5] for b in cur) / ref, 3)
    snap["rvol"] = rvol
    # sessionens profil i fin upplösning för visning (POC/VAH/VAL kommer fortfarande från motorn)
    sf = cme_sess or _fine_profile([(b["h"], b["l"], b["v"]) for b in sess], FINE[inst], va=False)
    if sf and not cme_sess:
        sf.update({"poc": snap.get("poc"), "vah": snap.get("vah"), "val": snap.get("val")})
    snap["profiles"] = {"session": sf, "previous": prof_prev, "composite": prof_comp}
    return snap


# ── Historik för grafen (äldre staplar än motorns dygn) ───────────────────
_HIVL = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}
_HC = {}


def history(inst, tf):
    """Upp till 5000 staplar bakåt i vald tidsram (pris och volym, ingen köp/sälj-uppdelning)."""
    if inst not in COIN or tf not in _HIVL:
        return []
    key = (inst, tf)
    c = _HC.get(key)
    if c and time.time() - c[0] < 60:
        return c[1]
    now = int(time.time() * 1000)
    span = tf * 60000 * 5000
    try:
        cs = requests.post(HL_INFO, json={"type": "candleSnapshot", "req": {
            "coin": COIN[inst][0], "interval": _HIVL[tf], "startTime": now - span, "endTime": now}}, timeout=20).json()
        out = [{"t": int(x["t"]) // 1000, "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]),
                "c": float(x["c"]), "v": float(x["v"])} for x in cs or []]
    except Exception:
        out = c[1] if c else []
    _HC[key] = (time.time(), out)
    return out


def warm(insts=("NQ", "GC", "BTC")):
    for i in insts:
        _ensure(i)
