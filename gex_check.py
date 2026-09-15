"""
GEX CHECK  ·  gex_check.py
---------------------------
Jämför de källor nivåerna kan byggas på och skriver ut allt i klartext.
Skickar ingenting och rör inga filer — finns för att kunna avgöra vilken
källa som ljuger.

    python gex_check.py
"""
import sys

import gex as GX
import gex_cli as CLI

PAR = (("NQ", "QQQ", "NQ=F"), ("GC", "GLD", "GC=F"))


def _yahoo_spot(sym):
    """(fast_info, senaste 5-minutersbar) — de skiljer sig när fast_info är gammal."""
    import yfinance as yf
    t = yf.Ticker(sym)
    fast = bar = None
    try:
        fast = float(t.fast_info.get("last_price") or 0) or None
    except Exception as e:
        print(f"    fast_info {sym}: {type(e).__name__}: {e}")
    try:
        h = t.history(period="1d", interval="5m")
        if h is not None and len(h):
            bar = float(h["Close"].iloc[-1])
    except Exception as e:
        print(f"    history {sym}: {type(e).__name__}: {e}")
    return fast, bar


def ndx_compare():
    """NQ mot NDX-kedjan i stället för QQQ.

    NDX är samma underliggande som NQ — ingen ETF, ingen kvot på 41, och
    strikes var tionde punkt i stället för var 41:e. Andra GEX-tjänster räknar
    på NDX, och skillnaden syns direkt i väggarna. Det här jämför de två.
    """
    import gex as GX
    print("=" * 78)
    print("NQ  ·  NDX-kedjan mot QQQ-kedjan")
    fut = CLI._fut_price("NQ")
    for sym in ("_NDX", "NDX"):
        try:
            spot, rows, exps = GX._fetch_chain_cboe(sym)
        except Exception as e:
            print(f"  {sym}: {type(e).__name__}: {e}")
            continue
        n_oi = sum(1 for r in rows if (r.get("oi") or 0) > 0)
        if not rows:
            print(f"  {sym}: tom kedja")
            continue
        print(f"  {sym}: index {spot} · {len(rows)} rader, {n_oi} med OI · expiries {', '.join(exps)}")
        lv = GX.gex_from_chain(spot, rows)
        if not lv:
            print(f"  {sym}: kunde inte räkna nivåer")
            break
        basis = fut - spot
        print(f"  futures {fut} · basis NQ-NDX {basis:+.1f} · kvot {fut / spot:.4f}")
        for namn, k in (("call wall", "call_wall"), ("put wall", "put_wall"),
                        ("zero gamma", "zero_gamma"), ("max pain", "max_pain"),
                        ("call wall 0DTE", "call_wall_0"), ("put wall 0DTE", "put_wall_0")):
            v = lv.get(k)
            if v:
                print(f"    {namn:15} NDX {v:>10.1f}   ->  NQ {v + basis:>10.1f}")
        strikes = sorted({round(t["strike"]) for t in lv.get("top", [])})
        print(f"    tyngsta strikes: {strikes}")
        break
    print("=" * 78)


def main():
    try:
        ndx_compare()
    except Exception as e:
        print(f"NDX-jämförelse misslyckades: {type(e).__name__}: {e}")

    for inst, etf, fut in PAR:
        print("=" * 78)
        print(f"{inst}  ·  {etf} / {fut}")
        futpx = CLI._fut_price(inst)
        print(f"  futurespris {fut}: {futpx}")

        quotes = {}
        try:
            spot, rows, exps = GX._fetch_chain_cboe(etf)
            n_oi = sum(1 for r in rows if (r.get("oi") or 0) > 0)
            quotes["cboe"] = spot
            print(f"  cboe:  spot {spot} · {len(rows)} rader, {n_oi} med OI · expiries {', '.join(exps)}")
        except Exception as e:
            print(f"  cboe:  {type(e).__name__}: {e}")

        try:
            spot, rows, exps = GX._fetch_chain(etf)
            n_oi = sum(1 for r in rows if (r.get("oi") or 0) > 0)
            quotes["yahoo kedja"] = spot
            print(f"  yahoo: spot {spot} · {len(rows)} rader, {n_oi} med OI · expiries {', '.join(exps)}")
        except Exception as e:
            print(f"  yahoo: {type(e).__name__}: {e}")

        fast, bar = _yahoo_spot(etf)
        quotes["yahoo fast_info"] = fast
        quotes["yahoo 5m-bar"] = bar
        print(f"  yahoo {etf}-kurs: fast_info {fast} · senaste 5m-bar {bar}")

        print("  kvot futures/ETF med respektive kurs:")
        for namn, px in quotes.items():
            if px and futpx:
                print(f"    {namn:16} {px:>10.2f}  ->  {futpx / px:.4f}")
        print("  (NQ/QQQ ska ligga nära 41, GC/GLD nära 11 — avviker en kurs "
              "därifrån är den gammal, och alla nivåer skalas fel.)")

        for src in ("cboe", "yahoo"):
            GX.SOURCE = src
            GX._cache.clear()
            r = CLI.run(inst, tries=1)
            if r.get("error"):
                print(f"  nivåer via {src}: FEL — {r['error']}")
                continue
            print(f"  nivåer via {src}: call wall {r['call_wall']} · put wall {r['put_wall']} · "
                  f"flip {r['zero_gamma']} · max pain {r['max_pain']} · EM {r['expected_move']} · "
                  f"{r['n_strikes']} strikes · kvot {r['ratio']}")
            if r.get("mapping") == "basis":
                b = r.get("basis") or 0
                print(f"    i {r.get('underlying')}-termer: call {round(r['call_wall'] - b, 1)} · "
                      f"put {round(r['put_wall'] - b, 1)} · "
                      f"flip {r['zero_gamma'] and round(r['zero_gamma'] - b, 1)}"
                      f" · basis {b} · kurs från {r.get('spot_source')}")
            else:
                print(f"    i {etf}-termer: call {round(r['call_wall'] / r['ratio'], 2)} · "
                      f"put {round(r['put_wall'] / r['ratio'], 2)} · "
                      f"flip {r['zero_gamma'] and round(r['zero_gamma'] / r['ratio'], 2)}"
                      f" · ETF-kurs från {r.get('spot_source')}")
            print(f"    sträng ({inst} — levels string):")
            print(f"    {r['string']}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
