"""Internt facit för likviditetshyllorna — syns ALDRIG i appen eller i något API.

Följer varje ny hylla från motorn (läser bara, ändrar ingenting) och vad priset gör
när det når den, jämfört med slumpnivåer på samma avstånd från priset:
  - rörd   : priset kommer inom 25 % av reaktionsavståndet R från hyllan
  - håller : efter beröring studsar priset R bort från hyllan innan det går R igenom
  - bryts  : priset går R igenom hyllan först
  - platt  : inget av dem inom 30 min efter beröring
  - orörd  : priset nådde aldrig nivån inom 4 h (räknas inte)
R = motorns tol per instrument (NQ 12 p, guld 2 $, BTC 150 $).
Resultat sparas på serverns disk; en sammanfattning skrivs i serverloggen var 6:e timme.
"""
import json
import os
import random
import threading
import time

_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "hfacit.jsonl")
TICK_S = 5
TOUCH_FRAC = 0.25
AFTER_TOUCH_S = 30 * 60
UNTOUCHED_S = 4 * 3600
SUMMARY_S = 6 * 3600

_started = {"v": False}


def _write(rec):
    try:
        with open(_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def summary():
    """Sammanställning av allt som loggats (för internt bruk)."""
    st = {}
    try:
        with open(_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                s = st.setdefault(r["i"], {}).setdefault(r["k"], {"hold": 0, "break": 0, "flat": 0, "untouched": 0})
                s[r["res"]] = s.get(r["res"], 0) + 1
    except FileNotFoundError:
        pass
    return st


def _fmt(st):
    parts = []
    for inst, kinds in sorted(st.items()):
        seg = []
        for k, name in (("shelf", "hyllor"), ("ctrl", "slump")):
            s = kinds.get(k)
            if not s:
                continue
            touched = s["hold"] + s["break"] + s["flat"]
            pct = (100.0 * s["hold"] / touched) if touched else 0
            seg.append("%s rörda=%d håller=%.0f%% bryts=%d platt=%d orörda=%d" % (
                name, touched, pct, s["break"], s["flat"], s["untouched"]))
        parts.append("%s: %s" % (inst, " | ".join(seg)))
    return "; ".join(parts) or "inga data än"


def _loop():
    import orderflow_engine as OF
    pend = []            # öppna nivåer som följs
    seen = {}            # (inst, sida, pris) -> senast sedd, så samma hylla inte loggas om
    last_sum = time.time()
    print("[hfacit] start · " + _fmt(summary()), flush=True)
    while True:
        try:
            now = time.time()
            for key, inst in OF.STATE.items():
                px = inst.price
                if px is None:
                    continue
                R = float(inst.cfg["tol"])
                # nya hyllor + en slumpnivå per hylla på liknande avstånd, samma sida
                for s in inst.shelves():
                    sk = (key, s["side"], s["px"])
                    if now - seen.get(sk, 0) < UNTOUCHED_S:
                        seen[sk] = now
                        continue
                    seen[sk] = now
                    d = abs(s["px"] - px)
                    if d < R * TOUCH_FRAC:
                        continue          # redan vid priset — inget att mäta
                    pend.append({"i": key, "k": "shelf", "side": s["side"], "px": s["px"], "usd": s["avg_usd"],
                                 "t0": now, "R": R, "touch": None})
                    dc = d * random.uniform(0.5, 1.5)
                    cpx = px - dc if s["side"] == "bid" else px + dc
                    if all(abs(cpx - x["px"]) > R for x in inst.shelves()):
                        pend.append({"i": key, "k": "ctrl", "side": s["side"], "px": round(cpx, 2), "usd": 0,
                                     "t0": now, "R": R, "touch": None})
                # följ öppna nivåer
                keep = []
                for p in pend:
                    if p["i"] != key:
                        keep.append(p)
                        continue
                    bid = p["side"] == "bid"
                    res = None
                    if p["touch"] is None:
                        near = (px <= p["px"] + R * TOUCH_FRAC) if bid else (px >= p["px"] - R * TOUCH_FRAC)
                        if near:
                            p["touch"] = now
                        elif now - p["t0"] > UNTOUCHED_S:
                            res = "untouched"
                    if p["touch"] is not None and res is None:
                        if (bid and px <= p["px"] - R) or (not bid and px >= p["px"] + R):
                            res = "break"
                        elif (bid and px >= p["px"] + R) or (not bid and px <= p["px"] - R):
                            res = "hold"
                        elif now - p["touch"] > AFTER_TOUCH_S:
                            res = "flat"
                    if res:
                        _write({"i": p["i"], "k": p["k"], "side": p["side"], "px": p["px"], "usd": p["usd"],
                                "t0": int(p["t0"]), "touch": int(p["touch"]) if p["touch"] else None,
                                "t1": int(now), "res": res})
                    else:
                        keep.append(p)
                pend = keep
            if len(seen) > 5000:
                seen = {k: v for k, v in seen.items() if now - v < UNTOUCHED_S}
            if now - last_sum > SUMMARY_S:
                print("[hfacit] " + _fmt(summary()), flush=True)
                last_sum = now
        except Exception as e:
            print("[hfacit] fel: %s" % e, flush=True)
        time.sleep(TICK_S)


def start():
    if _started["v"]:
        return
    _started["v"] = True
    threading.Thread(target=_loop, daemon=True, name="hfacit").start()
