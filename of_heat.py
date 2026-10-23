"""Likviditets-heatmap: orderboken över tid. Läser motorns senaste boksnapshot (ändrar
ingenting i motorn), tar medel per minut och sparar ett dygn. Ut skickas bara relativ
styrka 0–1 per prisnivå och stapel — inga belopp."""
import json
import os
import threading
import time

KEEP = 24 * 60                     # minuter
EVERY = 20                         # sekunder mellan avläsningar
_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "of_heat.json")
_H = {}                            # inst -> {minut: {px: usd}}
_ACC = {}                          # inst -> (minut, {px: [summa, n]})
_LOCK = threading.Lock()
_STARTED = {"v": False}


def _load():
    try:
        with open(_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        cut = int(time.time() // 60) - KEEP
        for inst, cols in raw.items():
            _H[inst] = {int(m): {float(p): u for p, u in c} for m, c in cols.items() if int(m) >= cut}
    except Exception:
        pass


def _save():
    try:
        with _LOCK:
            raw = {i: {str(m): [[p, round(u)] for p, u in c.items()] for m, c in h.items()} for i, h in _H.items()}
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, separators=(",", ":"))
        os.replace(tmp, _FILE)
    except Exception as e:
        print(f"[heat] kunde inte spara: {e}")


def _sample():
    import orderflow_engine as _OFE
    now_m = int(time.time() // 60)
    for inst, st in list(_OFE.STATE.items()):
        with st.lock:
            book = dict(st.last_book or {})
        if not book:
            continue
        with _LOCK:
            m, acc = _ACC.get(inst, (now_m, {}))
            if m != now_m:                               # minuten klar -> spara medel
                if acc:
                    h = _H.setdefault(inst, {})
                    h[m] = {p: s / n for p, (s, n) in acc.items()}
                    for k in [k for k in h if k < now_m - KEEP]:
                        del h[k]
                m, acc = now_m, {}
            for p, (_side, usd) in book.items():
                a = acc.setdefault(p, [0.0, 0])
                a[0] += usd
                a[1] += 1
            _ACC[inst] = (m, acc)


def _loop():
    last_save = time.time()
    while True:
        try:
            _sample()
            if time.time() - last_save > 300:
                _save()
                last_save = time.time()
        except Exception as e:
            print(f"[heat] {e}")
        time.sleep(EVERY)


def start():
    if _STARTED["v"]:
        return
    _STARTED["v"] = True
    _load()
    threading.Thread(target=_loop, daemon=True, name="of_heat").start()


def get(inst, tf=1):
    """Kolumner per stapel (tf minuter): [[t, [[px, styrka 0–1], ...]], ...]."""
    import orderflow_engine as _OFE
    cfg = _OFE.INSTR.get(inst) or {}
    with _LOCK:
        cols = dict(_H.get(inst) or {})
    if not cols:
        return {"bin": cfg.get("book_bin"), "cols": []}
    tf = max(1, int(tf))
    bars = {}
    for m, c in cols.items():
        t = (m // tf) * tf * 60
        b = bars.setdefault(t, [{}, 0])
        for p, u in c.items():
            b[0][p] = b[0].get(p, 0.0) + u
        b[1] += 1
    vals = sorted(u / n for c, n in bars.values() for u in c.values())
    ref = vals[int(len(vals) * 0.97)] if vals else 1.0     # 97:e percentilen = full styrka
    out = []
    for t in sorted(bars):
        c, n = bars[t]
        row = [[p, round(min(1.0, (u / n) / ref), 2)] for p, u in c.items() if (u / n) / ref >= 0.05]
        if row:
            out.append([t, row])
    return {"bin": cfg.get("book_bin"), "cols": out}
