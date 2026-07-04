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
"""

import os
import json
import threading

VAPID_PUBLIC  = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "")
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


def add_subscription(sub: dict) -> int:
    """Sparar en prenumeration (dedup pa endpoint). Returnerar totalantal."""
    with _lock:
        subs = _load()
        ep = (sub or {}).get("endpoint")
        if not ep:
            return len(subs)
        subs = [s for s in subs if s.get("endpoint") != ep]
        subs.append(sub)
        _save(subs)
        return len(subs)


def remove_subscription(endpoint: str) -> int:
    with _lock:
        subs = [s for s in _load() if s.get("endpoint") != endpoint]
        _save(subs)
        return len(subs)


def sub_count() -> int:
    return len(_load())


def send_all(title: str, body: str, url: str = "/", tag: str = "grabit") -> dict:
    """Skickar en notis till alla prenumeranter. Rensar doda endpoints.
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
    with _lock:
        subs = _load()
    sent, dead, err = 0, [], 0
    for s in subs:
        try:
            webpush(
                subscription_info=s,
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
