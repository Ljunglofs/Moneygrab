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
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import gex as GX

ET = ZoneInfo("America/New_York")
FUT = {"NQ": "NQ=F", "GC": "GC=F"}


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


def run(inst):
    fut = _fut_price(inst)
    g = GX.get_gex(inst, fut_price=fut, force=True)
    if not g or not g.get("futures"):
        return {"inst": inst, "error": "kunde inte hämta optionskedjan från Yahoo (rate-limit?) — försök igen om en minut"}
    open_px, atr = _open_and_atr(inst)
    s = GX.levels_string(inst, g, open_price=open_px, atr_daily=atr, today=datetime.now(ET).date())
    f = g["futures"]
    return {"inst": inst, "fut_price": fut, "underlying": g["underlying"], "etf_spot": g["levels"]["spot"],
            "ratio": f["ratio"], "regime": f["regime"], "expiries": g["expiries"],
            "call_wall": f["call_wall"], "put_wall": f["put_wall"], "zero_gamma": f["zero_gamma"],
            "hgex": f.get("hgex"), "call_wall_0dte": f.get("call_wall_0"), "put_wall_0dte": f.get("put_wall_0"),
            "max_pain": f.get("max_pain"), "expected_move": f.get("em"), "iv_1d": f.get("iv_1d"),
            "open": open_px, "atr_daily": atr, "string": s}


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
        print(f"0DTE: call {r['call_wall_0dte']} / put {r['put_wall_0dte']}  ·  Max pain {r['max_pain']}  ·  EM ±{r['expected_move']}  ·  IV 1D ±{r['iv_1d']}")
        print(f"Öppning {r['open']}  ·  ATR dag {r['atr_daily'] and round(r['atr_daily'], 2)}  ·  expiries {', '.join(r['expiries'][:4])}")
        print("-" * 78)
        print("Klistra in i indikatorn (fältet " + ("NQ" if r["inst"] == "NQ" else "GC") + " — levels string):")
        print(r["string"])
    print("=" * 78)


if __name__ == "__main__":
    main(sys.argv[1:])
