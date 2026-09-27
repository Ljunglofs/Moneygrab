"""
GRABIT  ·  discord_link.py
--------------------------
"Anslut Discord" för PRO-medlemmar. Kunden loggar in med Discord (OAuth2) och
läggs automatiskt till på GRABIT-servern med PRO-rollen. När prenumerationen
upphör tas rollen bort (via Stripe-webhooken, och en daglig kontroll som
säkerhet om ett webhook-anrop skulle missas).

  GET /api/pro/discord/connect?token=   skickar kunden till Discords inloggning
  GET /api/pro/discord/callback         Discord skickar tillbaka hit
  GET /api/pro/discord/status           för felsökning: är allt konfigurerat

ENV (Render) — från discord.com/developers/applications
  DISCORD_CLIENT_ID       appens Application ID
  DISCORD_CLIENT_SECRET   OAuth2 → Client Secret
  DISCORD_BOT_TOKEN       Bot → Token (boten måste vara med på servern)
  DISCORD_GUILD_ID        serverns id
  DISCORD_PRO_ROLE_ID     PRO-rollens id (botens roll måste ligga OVANFÖR den)
  DISCORD_REDIRECT_URI    (valfri) standard https://grabitlabs.com/api/pro/discord/callback
"""

import os
import json
import time
import hmac
import hashlib
import threading

try:
    import requests
except Exception:                      # pragma: no cover
    requests = None

API = "https://discord.com/api/v10"
_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "discord_links.json")
_LOCK = threading.Lock()
APP_URL = os.environ.get("APP_URL", "https://grabitlabs.com").rstrip("/")


def _cfg():
    e = os.environ.get
    return {
        "client_id": e("DISCORD_CLIENT_ID", "").strip(),
        "secret": e("DISCORD_CLIENT_SECRET", "").strip(),
        "bot": e("DISCORD_BOT_TOKEN", "").strip(),
        "guild": e("DISCORD_GUILD_ID", "").strip(),
        "role": e("DISCORD_PRO_ROLE_ID", "").strip(),
        "redirect": e("DISCORD_REDIRECT_URI", "").strip() or APP_URL + "/api/pro/discord/callback",
    }


def enabled() -> bool:
    c = _cfg()
    return all(c[k] for k in ("client_id", "secret", "bot", "guild", "role"))


def _load() -> dict:
    try:
        with open(_FILE) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(_FILE) or ".", exist_ok=True)
    tmp = "%s.%d.tmp" % (_FILE, threading.get_ident())
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, _FILE)


def _secret() -> bytes:
    from billing_web import _secret as s
    return s()


def _state_make(k: str, src: str) -> str:
    raw = "%s|%s|%d" % (k, src, int(time.time()))
    sig = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:24]
    return raw + "|" + sig


def _state_check(state: str):
    try:
        k, src, ts, sig = state.split("|")
        raw = "%s|%s|%s" % (k, src, ts)
        good = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:24]
        if hmac.compare_digest(good, sig) and time.time() - int(ts) < 900:
            return k, src
    except Exception:
        pass
    return None


def _bot_headers():
    return {"Authorization": "Bot " + _cfg()["bot"], "Content-Type": "application/json"}


def _set_role(uid: str, add: bool) -> bool:
    c = _cfg()
    url = "%s/guilds/%s/members/%s/roles/%s" % (API, c["guild"], uid, c["role"])
    try:
        r = (requests.put if add else requests.delete)(url, headers=_bot_headers(), timeout=15)
        if r.status_code in (204, 200):
            return True
        if not add and r.status_code == 404:          # har lämnat servern — inget att ta bort
            return True
        print("[discord] roll %s misslyckades: %s %s" % ("+" if add else "-", r.status_code, r.text[:200]))
    except Exception as e:
        print("[discord] roll-fel:", e)
    return False


def _join_with_role(uid: str, access_token: str) -> bool:
    """Lägger till användaren på servern med PRO-rollen (eller ger rollen om hen
    redan är medlem)."""
    c = _cfg()
    url = "%s/guilds/%s/members/%s" % (API, c["guild"], uid)
    try:
        r = requests.put(url, headers=_bot_headers(), timeout=15,
                         json={"access_token": access_token, "roles": [c["role"]]})
        if r.status_code == 201:
            return True                                  # ny medlem, rollen satt
        if r.status_code == 204:
            return _set_role(uid, True)                  # redan medlem: ge rollen
        print("[discord] join misslyckades: %s %s" % (r.status_code, r.text[:200]))
    except Exception as e:
        print("[discord] join-fel:", e)
    return False


def _tell_owner(text: str) -> None:
    try:
        from community import _tell_owner as t
        t(text)
    except Exception:
        pass


def revoke_for_keys(keys) -> int:
    """Prenumerationen har upphört: ta bort PRO-rollen för alla Discord-konton
    som anslöts med någon av nycklarna."""
    keys = {k for k in (keys or []) if k}
    if not keys or not enabled():
        return 0
    with _LOCK:
        hits = [(uid, e) for uid, e in _load().items() if e.get("k") in keys and e.get("status") == "linked"]
    n = 0
    for uid, e in hits:
        if _set_role(uid, False):
            with _LOCK:
                d = _load()
                if uid in d:
                    d[uid]["status"] = "revoked"
                    d[uid]["ts"] = int(time.time())
                    _save(d)
            n += 1
    if n:
        _tell_owner("\U0001F512 <b>Discord PRO-roll borttagen</b> för %d konto(n) (prenumerationen upphörde)" % n)
    return n


def _active_keys():
    """Nycklar (k) som hör till en aktiv prenumeration just nu, plus de som
    kommer från medlemskoder (de styrs inte av Stripe)."""
    from billing_web import _load_ent, _sub_active
    d = _load_ent()
    h = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]  # noqa: E731
    active = set()
    for cid, sid in d.get("cid2sub", {}).items():
        if _sub_active(d["subs"].get(sid)):
            active.add(h(cid))
    for sid, sub in d.get("subs", {}).items():
        if sub.get("email") and _sub_active(sub):
            active.add(h(sub["email"]))
    return active


def _reconcile_loop():
    """En gång per dygn: ta bort rollen för anslutna konton vars prenumeration
    inte längre är aktiv (säkerhetsnät om ett webhook-anrop missats)."""
    time.sleep(600)
    while True:
        try:
            if enabled():
                active = _active_keys()
                with _LOCK:
                    stale = [e.get("k") for e in _load().values()
                             if e.get("status") == "linked" and e.get("src") != "code"
                             and e.get("k") not in active]
                if stale:
                    revoke_for_keys(stale)
        except Exception as e:
            print("[discord] avstämning fel:", e)
        time.sleep(86400)


def register(app) -> None:
    from fastapi.responses import RedirectResponse
    threading.Thread(target=_reconcile_loop, daemon=True).start()
    if enabled():
        print("[discord] Anslut Discord är aktiverat")
    else:
        miss = [k for k, v in _cfg().items() if not v and k != "redirect"]
        print("[discord] Anslut Discord inte aktiverat — saknar: " + ", ".join(miss))

    def _back(status: str):
        return RedirectResponse(APP_URL + "/?app=1&discord=" + status, status_code=302)

    @app.get("/api/pro/discord/connect")
    def discord_connect(token: str = ""):
        if not enabled():
            return _back("off")
        from billing_web import verify_token
        p = verify_token(token or "")
        if not p:
            return _back("pro")
        from urllib.parse import urlencode
        c = _cfg()
        q = urlencode({"client_id": c["client_id"], "redirect_uri": c["redirect"],
                       "response_type": "code", "scope": "identify guilds.join",
                       "state": _state_make(p.get("k", ""), p.get("src", "")), "prompt": "none"})
        return RedirectResponse("https://discord.com/oauth2/authorize?" + q, status_code=302)

    @app.get("/api/pro/discord/callback")
    def discord_callback(code: str = "", state: str = "", error: str = ""):
        if error or not code:
            return _back("cancel")
        st = _state_check(state)
        if not st:
            return _back("expired")
        k, src = st
        c = _cfg()
        try:
            r = requests.post(API + "/oauth2/token", timeout=15, data={
                "client_id": c["client_id"], "client_secret": c["secret"],
                "grant_type": "authorization_code", "code": code, "redirect_uri": c["redirect"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            tok = r.json().get("access_token")
            if not tok:
                print("[discord] token-fel:", r.status_code, r.text[:200])
                return _back("error")
            me = requests.get(API + "/users/@me", timeout=15,
                              headers={"Authorization": "Bearer " + tok}).json()
            uid, uname = str(me.get("id") or ""), me.get("username") or ""
        except Exception as e:
            print("[discord] callback-fel:", e)
            return _back("error")
        if not uid or not _join_with_role(uid, tok):
            return _back("error")
        with _LOCK:
            d = _load()
            d[uid] = {"k": k, "src": src, "username": uname, "status": "linked", "ts": int(time.time())}
            _save(d)
        _tell_owner("✅ <b>Discord ansluten</b>: <code>%s</code> fick PRO-rollen" % uname)
        return _back("ok")

    @app.get("/api/pro/discord/status")
    def discord_status():
        d = _load()
        cnt = {}
        for e in d.values():
            cnt[e.get("status", "?")] = cnt.get(e.get("status", "?"), 0) + 1
        return {"enabled": enabled(), "links": cnt}
