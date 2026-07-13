"""
GRABIT PUSH  ·  push_notify.py
------------------------------
PWA-pushnotiser via Web Push (VAPID + pywebpush).

Env som kravs i Render:
    VAPID_PUBLIC_KEY   (delas med webblasaren)
    VAPID_PRIVATE_KEY  (hemlig, signerar utskicken)
    VAPID_SUB          (kontakt, t.ex. mailto:info@hekab.nu — valfri)

Prenumerationer sparas i DATA_DIR/push_subs.json (samma persistenta
disk som robotens state). Doda prenumerationer (404/410 fran
pushtjansten) rensas automatiskt vid utskick.

Watchlist: varje prenumeration kan ha en lista bevakade tickers
("_tickers", synkas fran appens portfolj). send_watchlist() skickar
bara till dem som bevakar just den aktien — ingen spam till andra.
"""

import os
import json
import threading

def _ren(namn):
    """Hamtar env-varde och stadar bort vanliga inklistringsfel:
    citattecken, whitespace/radbrytningar, och 'NAMN=' om hela raden klistrats in."""
    v = os.environ.get(namn, "").strip().strip('"').strip("'").strip()
    if "=" in v and v.split("=", 1)[0].upper().startswith("VAPID"):
        v = v.split("=", 1)[1].strip().strip('"').strip("'")
    return "".join(v.split())  # ta bort ev. inbaddade mellanslag/radbrytningar

VAPID_PUBLIC  = _ren("VAPID_PUBLIC_KEY")
VAPID_PRIVATE = _ren("VAPID_PRIVATE_KEY")
VAPID_SUB     = os.environ.get("VAPID_SUB", "mailto:info@hekab.nu")
DATA_DIR      = os.environ.get("DATA_DIR", ".")
SUBS_FILE     = os.path.join(DATA_DIR, "push_subs.json")

_lock = threading.Lock()


def _load():
    try:
        with open(SUBS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save(subs):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SUBS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(subs, f)
        os.replace(tmp, SUBS_FILE)
    except Exception as e:
        print(f"[push] kunde inte spara prenumerationer: {e}")


def _norm_tickers(tickers):
    out = []
    for t in (tickers or []):
        t = str(t).strip().upper()
        if t and t not in out:
            out.append(t)
    return out[:60]


def add_subscription(sub: dict, tickers=None) -> int:
    """Sparar en prenumeration (dedup pa endpoint). Returnerar totalantal.
    `tickers` = bevakade aktier fran appens portfolj (None = behall gamla)."""
    with _lock:
        subs = _load()
        ep = (sub or {}).get("endpoint")
        if not ep:
            return len(subs)
        old = next((s for s in subs if s.get("endpoint") == ep), None)
        sub = dict(sub)
        if tickers is not None:
            sub["_tickers"] = _norm_tickers(tickers)
        elif old and "_tickers" in old:
            sub["_tickers"] = old["_tickers"]
        subs = [s for s in subs if s.get("endpoint") != ep]
        subs.append(sub)
        _save(subs)
        return len(subs)


def set_tickers(endpoint: str, tickers) -> int:
    """Uppdaterar vilka tickers en prenumerant bevakar. Returnerar antal tickers."""
    tks = _norm_tickers(tickers)
    with _lock:
        subs = _load()
        for s in subs:
            if s.get("endpoint") == endpoint:
                s["_tickers"] = tks
        _save(subs)
    return len(tks)


def all_watch_tickers():
    """Alla tickers som nagon prenumerant bevakar (unika, sorterade)."""
    out = set()
    with _lock:
        subs = _load()
    for s in subs:
        out.update(s.get("_tickers") or [])
    return sorted(out)


def set_flag(endpoint: str, key: str, val: bool) -> bool:
    """Sätter en av/på-flagga (t.ex. ett topic som 'trump') på en prenumeration."""
    with _lock:
        subs = _load()
        found = False
        for s in subs:
            if s.get("endpoint") == endpoint:
                s[key] = bool(val)
                found = True
        if found:
            _save(subs)
        return found


def flag_count(key: str) -> int:
    with _lock:
        return sum(1 for s in _load() if s.get(key))


def remove_subscription(endpoint: str) -> int:
    with _lock:
        subs = [s for s in _load() if s.get("endpoint") != endpoint]
        _save(subs)
        return len(subs)


def sub_count() -> int:
    return len(_load())


def _send_to(subs, title: str, body: str, url: str = "/", tag: str = "grabit") -> dict:
    """Skickar en notis till angivna prenumeranter. Rensar doda endpoints.
    Returnerar {"skickade": n, "doda": n, "fel": n}."""
    if not (VAPID_PUBLIC and VAPID_PRIVATE):
        print("[push] VAPID-nycklar saknas -- inget utskick")
        return {"skickade": 0, "doda": 0, "fel": 0, "not": "VAPID saknas"}
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("[push] pywebpush saknas i requirements -- inget utskick")
        return {"skickade": 0, "doda": 0, "fel": 0, "not": "pywebpush saknas"}

    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    sent, dead, err = 0, [], 0
    for s in subs:
        try:
            webpush(
                subscription_info={k: v for k, v in s.items() if not k.startswith("_")},
                data=payload,
                vapid_private_key=VAPID_PRIVATE,
                vapid_claims={"sub": VAPID_SUB},
                ttl=3600,
            )
            sent += 1
        except WebPushException as ex:
            code = getattr(getattr(ex, "response", None), "status_code", 0)
            if code in (404, 410):
                dead.append(s.get("endpoint"))
            else:
                err += 1
                print(f"[push] fel ({code}): {str(ex)[:120]}")
        except Exception as ex:
            err += 1
            print(f"[push] fel: {str(ex)[:120]}")
    if dead:
        with _lock:
            live = [s for s in _load() if s.get("endpoint") not in set(dead)]
            _save(live)
    print(f"[push] '{title}' -> {sent} skickade, {len(dead)} rensade, {err} fel")
    return {"skickade": sent, "doda": len(dead), "fel": err}


def send_all(title: str, body: str, url: str = "/", tag: str = "grabit") -> dict:
    """Skickar en notis till ALLA prenumeranter (test + ROBBER-larm)."""
    with _lock:
        subs = _load()
    return _send_to(subs, title, body, url, tag)


def send_to_endpoint(endpoint: str, title: str, body: str, url: str = "/",
                     tag: str = "grabit-larm") -> dict:
    """Skickar en notis till EN specifik prenumerant (t.ex. eget prislarm)."""
    with _lock:
        subs = [s for s in _load() if s.get("endpoint") == endpoint]
    if not subs:
        return {"skickade": 0, "doda": 0, "fel": 0, "not": "okand prenumerant"}
    return _send_to(subs, title, body, url, tag)


def send_watchlist(ticker: str, title: str, body: str, url: str = "/") -> dict:
    """Skickar en notis ENDAST till prenumeranter som bevakar `ticker`
    (aktier i deras portfolj i appen). Prenumeranter utan bevakning
    far ingenting — de har inte bett om bolagslarm."""
    tk = (ticker or "").strip().upper()
    with _lock:
        subs = [s for s in _load() if tk in (s.get("_tickers") or [])]
    if not subs:
        return {"skickade": 0, "doda": 0, "fel": 0, "not": "inga bevakare av " + tk}
    return _send_to(subs, title, body, url, tag="grabit-" + tk)


def send_flagged(key: str, title: str, body: str, url: str = "/", tag: str = "grabit") -> dict:
    """Skickar bara till prenumeranter som slagit på flaggan `key` (t.ex. 'trump')."""
    with _lock:
        subs = [s for s in _load() if s.get(key)]
    if not subs:
        return {"skickade": 0, "doda": 0, "fel": 0, "not": "inga med flagga " + key}
    return _send_to(subs, title, body, url, tag)
