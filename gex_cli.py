"""
GEX CLI  ·  gex_cli.py
-----------------------
Räkna GEX-nivåerna lokalt, utan server. Skriver ut strängen till TradingView-
indikatorn "GRABIT GEX Levels" (pine/grabit_gex_levels.pine).

    pip install yfinance pandas numpy
    python gex_cli.py            # NQ + GC
    python gex_cli.py NQ         # bara NQ (samma nivåer gäller MNQ)
    python gex_cli.py GC --json  # allt som JSON, för egen vidarebearbetning

Data: QQQ/GLD-optioner + futurespris från Yahoo (10 min fördröjt — spelar ingen
roll för dagsnivåer). Öppning och dags-ATR hämtas också från Yahoo.
"""
import json
import os
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import gex as GX

ET = ZoneInfo("America/New_York")
FUT = {"NQ": "NQ=F", "GC": "GC=F"}

# Rimlighetskontroll: Yahoo svarar ibland med en halv kedja (rate-limit) — då blir
# väggarna nonsens. Hellre "försök igen" än fel nivåer i Telegram.
MIN_STRIKES = int(os.environ.get("GEX_MIN_STRIKES", "20"))
MAX_WALL_PCT = float(os.environ.get("GEX_MAX_WALL_PCT", "0.25"))   # vägg får ligga max 25 % från priset
MAX_NEAR_DAYS = int(os.environ.get("GEX_MAX_NEAR_DAYS", "5"))      # närmaste expiry max 5 dagar bort
# Kvoten futures/ETF är strukturell: NQ/QQQ ligger nära 41, GC/GLD nära 11 och
# rör sig bara långsamt. Hamnar den utanför bandet är ETF-kursen fel, och då är
# varenda nivå felskalad.
RATIO_BAND = {"NQ": (39.0, 42.5), "GC": (10.5, 11.5)}
# Index: futures = index + basis. Basisen är ränta minus utdelning fram till
# förfall, alltså små tal — inte 1 % av priset.
# Kvartalskontraktet ligger ungefär 1 % över index (ränta minus utdelning under
# tre månader), så en basis på 300 punkter är normal efter rullen. Är terminspriset
# framräknat ur put-call-paritet är basisen validerad av konstruktionen och får
# vara större; annars hålls den hårdare.
MAX_BASIS_PCT = float(os.environ.get("GEX_MAX_BASIS_PCT", "0.03"))
MAX_BASIS_PCT_UNVERIFIED = float(os.environ.get("GEX_MAX_BASIS_PCT_RAW", "0.015"))
MIN_BASIS_DIFF = float(os.environ.get("GEX_MIN_BASIS_DIFF", "5"))   # punkter
TRIES = int(os.environ.get("GEX_TRIES", "3"))
RETRY_SLEEP = int(os.environ.get("GEX_RETRY_SLEEP", "20"))

# Kvoten futures/ETF ska mätas när båda handlas. ETF:en handlas bara 09:30-16:00
# ET; utanför det är dess kurs gårdagens stängning medan futuren rört sig, och
# en live-kvot skulle då flytta alla nivåer med nattens rörelse.
OUT_DIR = os.environ.get("GEX_OUT_DIR", "levels")
RATIO_MAX_DAYS = int(os.environ.get("GEX_RATIO_MAX_DAYS", "7"))
RATIO_MIN_DIFF = float(os.environ.get("GEX_RATIO_MIN_DIFF", "0.001"))   # under 0,1 % spelar ingen roll


def _fut_price(inst):
    import yfinance as yf
    t = yf.Ticker(FUT[inst])
    try:
        p = float(t.fast_info.get("last_price") or 0)
        if p > 0:
            return p
    except Exception:
        pass
    h = t.history(period="2d", interval="5m")
    return float(h["Close"].iloc[-1]) if h is not None and len(h) else None


def _open_and_atr(inst):
    """Dagens RTH-öppning (09:30 ET) om den finns, annars senaste dagsöppning; ATR14 på dagsbarer."""
    import yfinance as yf
    t = yf.Ticker(FUT[inst])
    open_px = atr = None
    try:
        d = t.history(period="3mo", interval="1d")
        if len(d) > 15:
            hi, lo, cl = d["High"].values, d["Low"].values, d["Close"].values
            trs = [max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])) for i in range(1, len(d))]
            atr = sum(trs[-14:]) / 14
            open_px = float(d["Open"].iloc[-1])
    except Exception:
        pass
    try:
        m = t.history(period="1d", interval="1m")
        if m is not None and len(m):
            idx = m.index.tz_convert(ET)
            rth = m[(idx.hour * 60 + idx.minute) >= 570]
            if len(rth):
                open_px = float(rth["Open"].iloc[0])
    except Exception:
        pass
    return open_px, atr


def rth_now(now=None):
    """Är USA-börsen (och därmed ETF:en) öppen just nu?"""
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


def stored_map(inst, key="ratio", max_days=RATIO_MAX_DAYS):
    """Senast uppmätta kvot eller basis medan USA-börsen var öppen
    (levels/ratio.json). key är "ratio" för ETF och "basis" för index."""
    try:
        with open(os.path.join(OUT_DIR, "ratio.json"), encoding="utf-8") as f:
            e = (json.load(f) or {}).get(inst) or {}
        v = float(e[key])
        stamp = e["at"]
        age = (datetime.now(ET) - datetime.fromisoformat(stamp)).days
        if 0 <= age <= max_days and (key == "basis" or v > 0):
            return v, stamp
    except Exception:
        pass
    return None, None


def stored_ratio(inst, max_days=RATIO_MAX_DAYS):
    return stored_map(inst, "ratio", max_days)


def sanity(g, fut, inst=None, today=None):
    """Ser kedjan rimlig ut? Returnerar felsträng, annars None."""
    lv, f = g.get("levels") or {}, g.get("futures") or {}
    today = today or datetime.now(ET).date()
    if g.get("stale"):
        return "bara gammal cache — ingen källa svarade"
    n = lv.get("n_strikes") or 0
    if n < MIN_STRIKES:
        return f"tunn optionskedja ({n} strikes med OI) — bara en del av kedjan kom fram"
    if len(g.get("expiries") or []) < 2:
        return "färre än två expiries hämtades — halv optionskedja"
    near = f.get("near_expiry")
    if not near:
        return "ingen närmaste expiry i kedjan"
    days = (date.fromisoformat(near) - today).days
    if days > MAX_NEAR_DAYS:
        return f"närmaste expiry är {near} ({days} dagar bort) — dagens expiries saknas i svaret"
    cw, pw = f.get("call_wall"), f.get("put_wall")
    if not cw or not pw:
        return "call/put wall saknas"
    # Put wall över call wall är inte i sig fel: faller priset ur hela strukturen
    # hamnar den tyngsta negativa striken ovanför den tyngsta positiva. Guld såg
    # precis så ut den 14 september — GLD-strikes 400 och 405 med priset i 392.
    # Den trasiga kedjan den 8 september fångas av avståndsregeln nedan i stället
    # (put wall låg 32 % från priset).
    for name, px in (("call wall", cw), ("put wall", pw)):
        if abs(px / fut - 1) > MAX_WALL_PCT:
            return f"{name} {px} ligger {abs(px / fut - 1) * 100:.0f} % från priset {fut:.0f} — orimligt"
    if not (f.get("iv_1d") or f.get("em")):
        return "varken IV eller expected move gick att räkna — kedjan saknar priser"
    if f.get("basis") is not None:
        verified = g.get("spot_source") == "put-call-paritet"
        cap = MAX_BASIS_PCT if verified else MAX_BASIS_PCT_UNVERIFIED
        if abs(f["basis"]) > cap * fut:
            return (f"basis {f['basis']} är {abs(f['basis']) / fut * 100:.1f} % av priset"
                    + ("" if verified else " och terminspriset gick inte att bekräfta ur paritet")
                    + f" — kontrollera {lv.get('spot')}")
    else:
        lo, hi = RATIO_BAND.get(inst, (0, 1e9))
        if not (lo <= f.get("ratio", 0) <= hi):
            return (f"kvoten {f.get('ratio')} ligger utanför {lo}-{hi} — ETF-kursen "
                    f"{lv.get('spot')} ser gammal ut, alla nivåer skulle bli felskalade")
    return None


def _once(inst):
    fut = _fut_price(inst)
    if not fut:
        return {"inst": inst, "error": "fick inget futurespris från Yahoo"}
    g = GX.get_gex(inst, fut_price=fut, force=True)
    if not g or not g.get("futures"):
        return {"inst": inst, "error": "kunde inte hämta optionskedjan (rate-limit?)"}

    # Utanför USA-börsens öppettider är index- respektive ETF-kursen gårdagens
    # stängning medan futuren rört sig. Räkna då om med den senast uppmätta
    # kopplingen i stället: basis för index, kvot för ETF.
    is_rth = rth_now()
    index_mode = g.get("mapping") == "basis"
    ratio_src = "live"
    ratio_at = datetime.now(ET).isoformat(timespec="seconds")
    if not is_rth:
        if index_mode:
            prev, stamp = stored_map(inst, "basis")
            live = g["futures"].get("basis")
            if prev is None:
                # Ingen basis uppmätt med index och futures öppna samtidigt ännu.
                # Indexkursen är då gårdagens stängning medan futuren rört sig i
                # natt, så basisen skulle svälja hela nattrörelsen. Ta ETF-vägen
                # i stället tills en riktig basis finns.
                alt = GX.get_gex(inst, fut_price=fut, force=True, prefer=GX.FALLBACK_UNDERLYING.get(inst))
                if alt and alt.get("futures") and alt.get("mapping") != "basis":
                    print(f"[gex] {inst}: ingen RTH-basis sparad — använder {alt.get('underlying')} tills vidare")
                    g, index_mode, ratio_src = alt, False, "ingen RTH-basis, ETF-vägen"
                    prev, stamp = stored_map(inst, "ratio")
                    live = g["futures"]["ratio"]
                    if prev and abs(live / prev - 1) > RATIO_MIN_DIFF:
                        g = dict(g)
                        g["futures"] = GX.to_futures(g["levels"], fut, g["levels"]["spot"], ratio=prev)
                        ratio_src = "senaste RTH " + str(stamp)[:16].replace("T", " ")
                        ratio_at = stamp
            elif live is not None and abs(live - prev) > MIN_BASIS_DIFF:
                g = dict(g)
                g["futures"] = GX.to_futures(g["levels"], fut, g["levels"]["spot"], basis=prev)
                ratio_src = "senaste RTH " + str(stamp)[:16].replace("T", " ")
                ratio_at = stamp
        else:
            prev, stamp = stored_map(inst, "ratio")
            live = g["futures"]["ratio"]
            if prev and abs(live / prev - 1) > RATIO_MIN_DIFF:
                g = dict(g)
                g["futures"] = GX.to_futures(g["levels"], fut, g["levels"]["spot"], ratio=prev)
                ratio_src = "senaste RTH " + str(stamp)[:16].replace("T", " ")
                ratio_at = stamp

    bad = sanity(g, fut, inst)
    if bad:
        src = g.get("source")
        return {"inst": inst, "error": f"{bad}{f' [källa: {src}]' if src else ''}"}
    open_px, atr = _open_and_atr(inst)
    s = GX.levels_string(inst, g, open_price=open_px, atr_daily=atr, today=datetime.now(ET).date())
    f = g["futures"]
    return {"inst": inst, "fut_price": fut, "underlying": g["underlying"], "etf_spot": g["levels"]["spot"],
            "ratio": f["ratio"], "regime": f["regime"], "expiries": g["expiries"],
            "source": g.get("source"), "spot_source": g.get("spot_source"),
            "mapping": g.get("mapping", "ratio"), "basis": f.get("basis"),
            "ratio_source": ratio_src, "ratio_rth": is_rth, "ratio_at": ratio_at,
            "n_strikes": g["levels"].get("n_strikes"),
            "call_wall": f["call_wall"], "put_wall": f["put_wall"], "zero_gamma": f["zero_gamma"],
            "zero_gamma_all": f.get("zero_gamma_all"), "flip_expiries": f.get("flip_expiries"),
            "hgex": f.get("hgex"), "call_wall_0dte": f.get("call_wall_0"), "put_wall_0dte": f.get("put_wall_0"),
            "max_pain": f.get("max_pain"), "expected_move": f.get("em"), "em_src": f.get("em_src"),
            "iv_1d": f.get("iv_1d"), "flip_uncertain": f.get("flip_uncertain"),
            "flip_inverted": f.get("flip_inverted"),
            "walls_inverted": bool(f.get("put_wall") and f.get("call_wall") and f["put_wall"] >= f["call_wall"]),
            "open": open_px, "atr_daily": atr, "string": s}


def run(inst, tries=TRIES):
    """Hämtar nivåerna och gör om försöket om Yahoo skickar en trasig kedja."""
    r = {}
    for i in range(1, tries + 1):
        try:
            r = _once(inst)
        except Exception as e:                       # nätverk/Yahoo-strul ska inte krascha jobbet
            r = {"inst": inst, "error": f"{type(e).__name__}: {e}"[:180]}
        if not r.get("error"):
            return r
        print(f"[gex] {inst}: {r['error']} (försök {i}/{tries})")
        if i < tries:
            time.sleep(RETRY_SLEEP)
    r["error"] = f"{r.get('error', 'okänt fel')} — {tries} försök"
    return r


def main(argv):
    as_json = "--json" in argv
    insts = [a.upper() for a in argv if a.upper() in ("NQ", "GC", "MNQ", "MGC")]
    insts = [{"MNQ": "NQ", "MGC": "GC"}.get(i, i) for i in insts] or ["NQ", "GC"]
    out = [run(i) for i in dict.fromkeys(insts)]
    if as_json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return
    for r in out:
        print("=" * 78)
        if r.get("error"):
            print(f"{r['inst']}: {r['error']}")
            continue
        print(f"{r['inst']}  ·  {r['underlying']} {r['etf_spot']:.2f} × {r['ratio']} = {r['fut_price']:.2f}  ·  gamma {r['regime']}")
        print(f"Call wall {r['call_wall']}  ·  Put wall {r['put_wall']}  ·  Flip {r['zero_gamma']}  ·  HGEX {r['hgex']}")
        print(f"0DTE: call {r['call_wall_0dte']} / put {r['put_wall_0dte']}  ·  Max pain {r['max_pain']}  ·  EM ±{r['expected_move']} ({r['em_src']})  ·  IV 1D ±{r['iv_1d']}")
        if r.get("flip_uncertain"):
            print("VARNING: Gamma Flip ligger >2 % från priset. Putdominerad optionskedja — regimen (positiv/negativ gamma) är osäker. Lita på väggar, EM och IV-range.")
        print(f"Källa {r.get('source')} ({r.get('underlying')})  ·  "
              f"{'basis ' + str(r.get('basis')) if r.get('mapping') == 'basis' else 'kvot ' + str(r.get('ratio'))}"
              f" {r.get('ratio_source')}  ·  Öppning {r['open']}  ·  ATR dag {r['atr_daily'] and round(r['atr_daily'], 2)}  ·  expiries {', '.join(r['expiries'][:4])}")
        print("-" * 78)
        print("Klistra in i indikatorn (fältet " + ("NQ" if r["inst"] == "NQ" else "GC") + " — levels string):")
        print(r["string"])
    print("=" * 78)


if __name__ == "__main__":
    main(sys.argv[1:])
