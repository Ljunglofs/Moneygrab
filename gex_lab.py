"""
GEX-labb  ·  gex_lab.py
-----------------------
Kör vår gammaräkning i flera varianter mot samma optionskedja och jämför
varje variant med nivåer från en annan tjänst. Syftet är att avgöra VARFÖR
två uträkningar skiljer sig: expiry-uppsättningen, väggdefinitionen eller
flippdefinitionen — inte att gissa.

Kedjan hämtas en gång per underliggande, sedan räknas alla varianter på
exakt samma rader. Skickar ingenting, ändrar ingenting; skriver bara i loggen.

    python gex_lab.py            # NQ (_NDX) och GC (GLD) mot inlagda facit
    python gex_lab.py NQ         # bara ett instrument

Facit anges i UNDERLIGGANDES pris (NDX-strike, GLD-strike), inte i futures,
så jämförelsen inte blandar in basis/kvot som mäts vid en annan tidpunkt.
"""
import os
import sys
from datetime import datetime, timezone, date

import gex

# Nivåer från gex dash 2026-09-15, omräknade till underliggande:
#   NQ: deras futuresnivåer − basis 312,1  (29582,1 -> NDX 29270)
#   GC: deras futuresnivåer / kvot 10,9957 (4570,0 -> GLD 415,61)
TARGETS = {
    "NQ": {"_basis": 312.1, "_unit": "NDX-strike",
           "call_wall": 29270.0, "put_wall": 28740.0, "zero_gamma": 29165.7,
           "max_pain": 29190.0, "put_wall_0": 29150.0},
    "GC": {"_ratio": 10.9957, "_unit": "GLD-strike",
           "call_wall": 415.61, "put_wall": 391.06, "zero_gamma": 392.77,
           "max_pain": 397.68, "call_wall_0": 396.52, "put_wall_0": 391.06},
}

UND = {"NQ": os.environ.get("LAB_NQ_UND", "_NDX"), "GC": "GLD"}


def _legs(rows, spot, exps):
    """(K, oi, iv, T, sign) för raderna i de valda expiries."""
    out = []
    keep = set(exps)
    for r in rows:
        if r["expiry"] not in keep:
            continue
        K = float(r["strike"]); oi = float(r.get("oi") or 0)
        if oi <= 0 or K <= 0:
            continue
        iv = float(r.get("iv") or 0)
        if not (0.01 < iv < 5):
            iv = 0.20
        out.append((K, oi, iv, gex._years_to(r["expiry"]), 1.0 if r["type"] == "C" else -1.0))
    return out


def _tables(legs, spot):
    """Per strike: dollar-gamma (vår modell), rå OI och rå gamma (utan S²)."""
    dg, oi_t, rg = {}, {}, {}
    for K, oi, iv, T, sign in legs:
        g = gex.bs_gamma(spot, K, T, iv)
        dg[K] = dg.get(K, 0.0) + g * oi * 100 * spot * spot * 0.01 * sign
        oi_t[K] = oi_t.get(K, 0.0) + oi * sign
        rg[K] = rg.get(K, 0.0) + g * oi * sign
    return dg, oi_t, rg


def _sided(legs, spot):
    """Dollar-gamma per strike, calls och puts var för sig (utan kvittning).
    Flera tjänster definierar väggarna så: call wall = striken med störst
    CALL-gamma, put wall = striken med störst PUT-gamma. Vår nettodefinition
    kvittar sidorna mot varandra, vilket drar put wall uppåt mot priset när
    samma strike har mycket av båda."""
    c, p = {}, {}
    for K, oi, iv, T, sign in legs:
        g = gex.bs_gamma(spot, K, T, iv) * oi * 100 * spot * spot * 0.01
        (c if sign > 0 else p)[K] = (c if sign > 0 else p).get(K, 0.0) + g
    return c, p


def _otm(legs, spot):
    """Bara out-of-the-money: calls över priset, puts under."""
    return [(K, oi, iv, T, sign) for K, oi, iv, T, sign in legs
            if (sign > 0 and K >= spot) or (sign < 0 and K <= spot)]


def _flip_shift(legs, spot, scale=True, pick="nearest"):
    """Nollgamma ur nettoprofilen när spot flyttas ±10 %."""
    def net_at(S):
        return sum(gex.bs_gamma(S, K, T, iv) * oi * (100 * S * S * 0.01 if scale else 1.0) * sign
                   for K, oi, iv, T, sign in legs)
    grid = [spot * (0.90 + 0.005 * i) for i in range(41)]
    vals = [net_at(S) for S in grid]
    zeros = []
    for i in range(1, len(grid)):
        a, b = vals[i - 1], vals[i]
        if a < 0 <= b or a > 0 >= b:
            zeros.append(grid[i - 1] + (grid[i] - grid[i - 1]) * (0 - a) / (b - a) if b != a else grid[i])
    if not zeros:
        return None
    return {"nearest": min(zeros, key=lambda z: abs(z - spot)),
            "lowest": min(zeros), "highest": max(zeros)}[pick]


def _flip_cum(table):
    """Nollgamma som strike där den kumulativa GEX:en (nerifrån och upp)
    byter tecken — definitionen flera dashar använder."""
    ks = sorted(table)
    run = 0.0
    prev_k = prev = None
    for k in ks:
        run += table[k]
        if prev is not None and ((prev < 0 <= run) or (prev > 0 >= run)):
            return prev_k + (k - prev_k) * (0 - prev) / (run - prev) if run != prev else k
        prev, prev_k = run, k
    return None


def _walls(table):
    ks = sorted(table)
    if not ks:
        return None, None
    return max(ks, key=lambda k: table[k]), min(ks, key=lambda k: table[k])


def _oi_walls(rows, exps):
    """Vägg = strike med störst öppen balans på respektive sida (ingen gamma)."""
    keep = set(exps); c, p = {}, {}
    for r in rows:
        if r["expiry"] not in keep:
            continue
        oi = float(r.get("oi") or 0); K = float(r["strike"])
        if oi <= 0:
            continue
        (c if r["type"] == "C" else p)[K] = (c if r["type"] == "C" else p).get(K, 0.0) + oi
    return (max(c, key=c.get) if c else None), (max(p, key=p.get) if p else None)


def _max_pain(rows, exp):
    nr = [r for r in rows if r["expiry"] == exp and float(r.get("oi") or 0) > 0]
    if not nr:
        return None
    ks = sorted({float(r["strike"]) for r in nr})
    def pain(S):
        return sum((max(0.0, S - float(r["strike"])) if r["type"] == "C"
                    else max(0.0, float(r["strike"]) - S)) * float(r["oi"]) for r in nr)
    return min(ks, key=pain)


def _d(got, want):
    return "      -" if got is None or want is None else f"{got - want:+7.1f}"


def _sets(exps, today):
    """Uppsättningar att prova: antal närmaste, och allt inom N dagar."""
    out = [(f"n={n}", exps[:n]) for n in (1, 2, 3, 4, 5, 6, 8, 10) if n <= len(exps)]
    for days in (7, 14, 30, 45):
        sel = [e for e in exps if (date.fromisoformat(e) - today).days <= days]
        if sel and ("≤%dd" % days, sel) not in out:
            out.append((f"<={days}d", sel))
    out.append(("alla", exps))
    seen, uniq = set(), []
    for name, sel in out:
        key = tuple(sel)
        if key and key not in seen:
            seen.add(key); uniq.append((name, sel))
    return uniq


def run(inst):
    und = UND[inst]
    t = TARGETS[inst]
    print(f"\n{'=' * 78}\n{inst} · {und} · facit i {t['_unit']}\n{'=' * 78}")
    spot, rows, exps, src = _fetch_wide(und)
    if not rows:
        print("ingen kedja — avbryter")
        return
    spot, spot_src = gex._checked_spot(und, spot, src)
    if gex.is_index(und) and exps:
        fwd = gex.forward_from_chain(rows, exps[0], hint=spot)
        if fwd and (not spot or abs(fwd / spot - 1) < 0.10):
            print(f"terminspris ur paritet {fwd:.1f} (kvoterat {spot})")
            spot, spot_src = fwd, "paritet"
    today = datetime.now(timezone.utc).date()
    print(f"källa {src} · spot {spot:.2f} ({spot_src}) · {len(rows)} rader · "
          f"expiries {', '.join(exps[:12])}")
    print(f"facit: CW {t.get('call_wall')} · PW {t.get('put_wall')} · "
          f"flip {t.get('zero_gamma')} · max pain {t.get('max_pain')}\n")

    hdr = (f"{'uppsättn':>9} | {'CW netto':>9}{'Δ':>8} {'PW netto':>9}{'Δ':>8} | "
           f"{'CW call':>8}{'Δ':>8} {'PW put':>8}{'Δ':>8} | "
           f"{'flip':>8}{'Δ':>8} {'flip OTM':>9}{'Δ':>8}")
    print(hdr); print("-" * len(hdr))
    for name, sel in _sets(exps, today):
        legs = _legs(rows, spot, sel)
        if not legs:
            continue
        dg, oi_t, rg = _tables(legs, spot)
        cw, pw = _walls(dg)
        cs, ps = _sided(legs, spot)
        cw_s = max(cs, key=cs.get) if cs else None
        pw_s = max(ps, key=ps.get) if ps else None
        f_shift = _flip_shift(legs, spot)
        f_otm = _flip_shift(_otm(legs, spot), spot)
        print(f"{name:>9} | {cw:9.1f}{_d(cw, t.get('call_wall'))} {pw:9.1f}{_d(pw, t.get('put_wall'))} | "
              f"{(cw_s or 0):8.1f}{_d(cw_s, t.get('call_wall'))} "
              f"{(pw_s or 0):8.1f}{_d(pw_s, t.get('put_wall'))} | "
              f"{(f_shift or 0):8.1f}{_d(f_shift, t.get('zero_gamma'))} "
              f"{(f_otm or 0):9.1f}{_d(f_otm, t.get('zero_gamma'))}")

    # Flippvarianter på den uppsättning vi kör skarpt (N_EXPIRIES)
    sel = exps[:gex.N_EXPIRIES]
    legs = _legs(rows, spot, sel)
    dg, oi_t, rg = _tables(legs, spot)
    print(f"\nflippvarianter, facit {t.get('zero_gamma')} (spot {spot:.1f}):")
    rows_v = [("skift, n=1 (0DTE)", _flip_shift(_legs(rows, spot, exps[:1]), spot)),
              ("skift, n=2", _flip_shift(_legs(rows, spot, exps[:2]), spot)),
              ("skift, n=3", _flip_shift(_legs(rows, spot, exps[:3]), spot)),
              (f"skift, n={len(sel)} (vår)", _flip_shift(legs, spot)),
              ("skift, bara OTM", _flip_shift(_otm(legs, spot), spot)),
              ("skift utan S²-vikt", _flip_shift(legs, spot, scale=False)),
              ("kumulativ dollar-gamma", _flip_cum(dg))]
    for label, val in rows_v:
        print(f"  {label:<24}{(val if val is not None else float('nan')):9.1f}{_d(val, t.get('zero_gamma'))}")
    net = sum(dg.values())
    print(f"  netto-GEX vid spot: {net / 1e9:+.2f} mdr -> regim "
          f"{'positiv (flip UNDER pris)' if net > 0 else 'negativ (flip ÖVER pris)'}")

    # 0DTE/närmaste expiry och max pain
    near = exps[0]
    legs0 = _legs(rows, spot, [near])
    dg0, oi0, _ = _tables(legs0, spot)
    cw0, pw0 = _walls(dg0)
    cs0, ps0 = _sided(legs0, spot)
    cw0_s = max(cs0, key=cs0.get) if cs0 else None
    pw0_s = max(ps0, key=ps0.get) if ps0 else None
    cwo0, pwo0 = _oi_walls(rows, [near])
    mp = _max_pain(rows, near)
    print(f"\nnärmaste expiry {near}:")
    print(f"  CW netto {cw0:.1f}{_d(cw0, t.get('call_wall_0'))}   PW netto {pw0:.1f}{_d(pw0, t.get('put_wall_0'))}")
    print(f"  CW call {cw0_s:.1f}{_d(cw0_s, t.get('call_wall_0'))}   PW put {pw0_s:.1f}{_d(pw0_s, t.get('put_wall_0'))}")
    print(f"  CW OI {cwo0:.1f}{_d(cwo0, t.get('call_wall_0'))}   PW OI {pwo0:.1f}{_d(pwo0, t.get('put_wall_0'))}")
    print(f"  max pain {mp:.1f}{_d(mp, t.get('max_pain'))}")


def _fetch_wide(und, n=14):
    """Hela kedjan, fler expiries än produktionen använder."""
    try:
        if gex.is_index(und):
            spot, rows, exps = gex._fetch_chain_cboe(und, n_exp=n)
            return spot, rows, exps, "cboe"
        try:
            spot, rows, exps = gex._fetch_chain_cboe(und, n_exp=n)
            if sum(1 for r in rows if (r.get("oi") or 0) > 0) >= gex.MIN_OI_ROWS:
                return spot, rows, exps, "cboe"
        except Exception as e:
            print(f"[lab] {und}: cboe misslyckades — {type(e).__name__}: {e}")
        spot, rows, exps = gex._fetch_chain(und, n_exp=n)
        return spot, rows, exps, "yahoo"
    except Exception as e:
        print(f"[lab] {und}: {type(e).__name__}: {e}")
        return None, [], [], "ingen"


if __name__ == "__main__":
    for inst in (sys.argv[1:] or ["NQ", "GC"]):
        try:
            run(inst.upper())
        except Exception as e:
            print(f"[lab] {inst}: {type(e).__name__}: {e}")
