"""GEX för NQ och guld räknat direkt på servern — med EXAKT samma kod som pipelinen
(gex_cli.run -> samma rad som levels/latest.json och TradingView-indikatorn).

Körs var 15:e minut i bakgrunden, så portalen inte är beroende av att GitHub-roboten startar.
Senaste lyckade resultat sparas på disk och används efter en omstart.
Varje resultat sparar också orderflödets pris vid beräkningen (src_px), så att nivåerna
kan översättas till orderflödets pris även när motorn saknar futures-basis.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

REFRESH_S = 15 * 60
_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "gex_live.json")
_C = {"data": {}, "t": 0, "busy": False, "loaded": False}
_L = threading.Lock()


def _refresh():
    try:
        import gex_cli
        out = dict(_C["data"])
        for inst in ("NQ", "GC"):
            try:
                r = gex_cli.run(inst, tries=2)
                if r and not r.get("error") and r.get("fut_price"):
                    src_px = None
                    try:
                        import orderflow_engine as OF
                        src_px = OF.STATE[inst].price
                    except Exception:
                        pass
                    r["src_px"] = src_px
                    r["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    out[inst] = r
                elif r and r.get("error"):
                    print("gex_live %s: %s" % (inst, r["error"]))
            except Exception as e:
                print("gex_live %s: %s" % (inst, e))
        _C["data"] = out
        _C["t"] = time.time()
        try:
            with open(_FILE, "w", encoding="utf-8") as f:
                json.dump(out, f)
        except Exception:
            pass
    finally:
        _C["busy"] = False


def get(inst):
    """Senaste pipeline-rad för NQ/GC (futures-nivåer) eller None."""
    with _L:
        if not _C["loaded"]:
            _C["loaded"] = True
            try:
                with open(_FILE, encoding="utf-8") as f:
                    d = json.load(f) or {}
                _C["data"] = {k: v for k, v in d.items() if isinstance(v, dict) and v.get("fut_price")}
            except Exception:
                pass
        if not _C["busy"] and time.time() - _C["t"] > REFRESH_S:
            _C["busy"] = True
            threading.Thread(target=_refresh, daemon=True).start()
    return _C["data"].get(inst)
