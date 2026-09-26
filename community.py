"""
GRABIT  ·  community.py
-----------------------
PRO-förmåner utanför appen: Discord, NASDAQ Robber på Telegram och
GRABIT-indikatorerna (invite-only-skript) på TradingView.

  GET  /api/pro/community?token=   länkar till Discord och Telegram — bara för PRO
  POST /api/pro/tradingview         {token, username} — begär åtkomst till skripten

TradingView har inget officiellt API för invite-only-åtkomst. Är TV_SESSIONID
och TV_PINE_IDS satta används samma anrop som sidan "Manage access" gör
(pine_perm/add och pine_perm/remove) med ägarens inloggning: namnet läggs till
direkt och tas bort när prenumerationen upphör. Annars, eller om anropet
misslyckas, går begäran till ägaren på Telegram för att läggas till för hand.
Alla begäran sparas i DATA_DIR/tv_access.json.

ENV (Render)
  DISCORD_INVITE_URL    inbjudan till Discord-servern (standard nedan)
  TELEGRAM_INVITE_URL   inbjudan till Telegram-kanalen där roboten postar
  TELEGRAM_TOKEN, CHAT_ID   ägarens Telegram — dit TradingView-begäran skickas
  TV_SESSIONID          cookien "sessionid" från ägarens inloggning på tradingview.com
  TV_SESSIONID_SIGN     cookien "sessionid_sign" (valfri, krävs på vissa konton)
  TV_PINE_IDS           (valfri) skriptens id:n, kommaseparerade (PUB;xxxx). Saknas den
                        hittas ägarens publicerade skript med "GRABIT" i namnet automatiskt.
  TV_SCRIPT_MATCH       (valfri) text som skriptnamnen ska innehålla, standard GRABIT
"""

import os
import re
import json
import time
import threading

_DISCORD_DEFAULT = "https://discord.gg/wkQR2fyBX"
_TV_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "tv_access.json")
_TV_LOCK = threading.Lock()
# TradingView-namn: bokstäver, siffror, _ . - (2-40 tecken).
_TV_NAME = re.compile(r"^[A-Za-z0-9_.\-]{2,40}$")


def _is_pro(token: str) -> bool:
    try:
        from billing_web import verify_token
        return verify_token(token or "") is not None
    except Exception:
        return False


def _load_tv() -> dict:
    try:
        with open(_TV_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_tv(d: dict) -> None:
    os.makedirs(os.path.dirname(_TV_FILE) or ".", exist_ok=True)
    tmp = _TV_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _TV_FILE)


def _tell_owner(text: str) -> bool:
    tok, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("CHAT_ID", "")
    if not tok or not chat:
        print("[community] Telegram ej konfigurerat:\n" + text)
        return False
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print("[community] Telegram-fel:", e)
        return False


# --------------------------------------------------------------------------
#  TradingView: ge och ta bort åtkomst
# --------------------------------------------------------------------------
_TV_BASE = "https://www.tradingview.com"


_TV_FOUND = {"ts": 0.0, "scripts": []}


def _tv_discover():
    """Hittar ägarens publicerade skript själv (samma lista som TradingView visar
    under "My scripts"), så TV_PINE_IDS inte behöver fyllas i för hand.
    Tar skript vars namn börjar med TV_SCRIPT_MATCH (standard "GRABIT").
    Cachas tio minuter, så nypublicerade skript kommer med snabbt."""
    if time.time() - _TV_FOUND["ts"] < 600 and _TV_FOUND["scripts"]:
        return _TV_FOUND["scripts"]
    if not os.environ.get("TV_SESSIONID", "").strip():
        return []
    match = os.environ.get("TV_SCRIPT_MATCH", "GRABIT").lower()
    found = []
    try:
        r = _tv_session().get("https://pine-facade.tradingview.com/pine-facade/list/",
                              params={"filter": "published"}, timeout=15)
        items = r.json() if r.status_code == 200 else []
        if isinstance(items, dict):
            items = items.get("results") or items.get("scripts") or []
        print("[community] TradingView listade %d publicerade skript: %s" % (
            len(items or []), "; ".join("%s (%s)" % (it.get("scriptName") or it.get("scriptTitle"),
                                                     str(it.get("scriptIdPart") or "")[:4]) for it in (items or [])[:30])))
        for it in items or []:
            pid = str(it.get("scriptIdPart") or "")
            name = str(it.get("scriptName") or it.get("scriptTitle") or "")
            # Namnet ska BÖRJA med GRABIT — den gamla "vwap - … by Grabit" ska inte delas ut.
            if pid.startswith("PUB;") and name.lower().startswith(match):
                found.append({"id": pid, "name": name})
        if r.status_code != 200:
            print("[community] pine-facade list: %s %s" % (r.status_code, r.text[:200]))
    except Exception as e:
        print("[community] kunde inte lista skript:", e)
    _TV_FOUND.update(ts=time.time(), scripts=found)
    return found


def _tv_cfg():
    sid = os.environ.get("TV_SESSIONID", "").strip()
    ids = [p.strip() for p in os.environ.get("TV_PINE_IDS", "").split(",") if p.strip()]
    if sid and not ids:
        ids = [x["id"] for x in _tv_discover()]
    return sid, ids


def _tv_session():
    import requests
    sid = os.environ.get("TV_SESSIONID", "").strip()   # inte _tv_cfg(): den anropar _tv_discover -> hit
    s = requests.Session()
    s.cookies.set("sessionid", sid, domain=".tradingview.com")
    sign = os.environ.get("TV_SESSIONID_SIGN", "").strip()
    if sign:
        s.cookies.set("sessionid_sign", sign, domain=".tradingview.com")
    s.headers.update({"Origin": _TV_BASE, "Referer": _TV_BASE + "/",
                      "User-Agent": "Mozilla/5.0 (GRABIT access manager)"})
    return s


def tv_user_exists(name: str):
    """True/False om TradingView svarar, None om det inte gick att kolla."""
    try:
        import requests
        r = requests.get(_TV_BASE + "/username_hint/", params={"s": name}, timeout=10)
        if r.status_code != 200:
            return None
        return any(str(u.get("username", "")).lower() == name.lower() for u in (r.json() or []))
    except Exception:
        return None


def _tv_perm(action: str, name: str) -> bool:
    """action = "add" eller "remove". True om alla skript lyckades."""
    sid, ids = _tv_cfg()
    if not (sid and ids):
        return False
    try:
        s = _tv_session()
        ok = True
        for pid in ids:
            r = s.post(f"{_TV_BASE}/pine_perm/{action}/",
                       data={"pine_id": pid, "username_recip": name}, timeout=15)
            try:
                st = (r.json() or {}).get("status")
            except Exception:
                st = None
            # "exists" vid add och "not_exists" vid remove betyder att läget redan stämmer.
            if r.status_code != 200 or st not in ("ok", "exists", "not_exists"):
                print(f"[community] pine_perm/{action} {pid} {name}: {r.status_code} {r.text[:200]}")
                ok = False
        return ok
    except Exception as e:
        print(f"[community] pine_perm/{action} fel:", e)
        return False


def revoke_for_keys(keys) -> int:
    """Tar bort TradingView-åtkomst för alla namn begärda med någon av nycklarna
    (k i PRO-token, = hash av enhetens cid). Anropas när en prenumeration upphör."""
    keys = {k for k in (keys or []) if k}
    if not keys:
        return 0
    n = 0
    with _TV_LOCK:
        d = _load_tv()
        hits = [e for e in d.values() if e.get("k") in keys and e.get("status") == "granted"]
    for e in hits:
        name = e["username"]
        auto = _tv_perm("remove", name)
        with _TV_LOCK:
            d = _load_tv()
            cur = d.get(name.lower())
            if cur:
                cur["status"] = "revoked" if auto else "revoke_pending"
                cur["ts"] = int(time.time())
                _save_tv(d)
        _tell_owner(("\U0001F512 <b>TradingView-access borttagen</b>\n" if auto else
                     "\u26A0\uFE0F <b>Ta bort TradingView-access</b> (prenumerationen har upphört)\n")
                    + f"Namn: <code>{name}</code>")
        n += 1
    return n


def _tv_selfcheck():
    """Vid start: skriv i loggen om TradingView-kopplingen fungerar, så att den
    kan verifieras i Render-loggen utan att någon behöver testa för hand."""
    time.sleep(20)
    if not os.environ.get("TV_SESSIONID", "").strip():
        print("[community] TradingView: TV_SESSIONID saknas — begäran går till Telegram")
        return
    try:
        ids = [p.strip() for p in os.environ.get("TV_PINE_IDS", "").split(",") if p.strip()]
        found = _tv_discover() if not ids else [{"id": i, "name": i} for i in ids]
        if found:
            print("[community] TradingView kopplad: %d skript — %s"
                  % (len(found), ", ".join(x["name"] for x in found)))
        else:
            print("[community] TradingView: inloggningen hittade inga GRABIT-skript "
                  "(fel/utgången cookie, eller inga publicerade skript med GRABIT i namnet)")
    except Exception as e:
        print("[community] TradingView-kontroll misslyckades:", e)


def register(app) -> None:
    threading.Thread(target=_tv_selfcheck, daemon=True).start()
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.get("/api/pro/community")
    def pro_community(token: str = ""):
        """Länkarna lämnas bara ut till PRO — utan token ingen inbjudan."""
        if not _is_pro(token):
            return {"pro": False}
        return {"pro": True,
                "discord": os.environ.get("DISCORD_INVITE_URL", _DISCORD_DEFAULT),
                "telegram": os.environ.get("TELEGRAM_INVITE_URL", "")}

    @app.post("/api/pro/tradingview")
    async def pro_tradingview(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            from billing_web import verify_token
            payload = verify_token(str(body.get("token") or ""))
        except Exception:
            payload = None
        if payload is None:
            return JSONResponse({"ok": False, "error": "pro"}, status_code=403)
        name = str(body.get("username") or "").strip().lstrip("@")
        if not _TV_NAME.match(name):
            return JSONResponse({"ok": False, "error": "name"}, status_code=400)
        key = name.lower()
        with _TV_LOCK:
            prev = _load_tv().get(key)
        if prev and prev.get("status") == "granted":
            return {"ok": True, "status": "granted", "dup": True}
        if prev and time.time() - prev.get("ts", 0) < 3600 and prev.get("status") == "pending":
            return {"ok": True, "status": "pending", "dup": True}
        import asyncio
        if await asyncio.to_thread(tv_user_exists, name) is False:
            return JSONResponse({"ok": False, "error": "unknown_user"}, status_code=400)
        granted = await asyncio.to_thread(_tv_perm, "add", name)
        entry = {"username": name, "ts": int(time.time()), "k": payload.get("k", ""),
                 "src": payload.get("src", ""), "status": "granted" if granted else "pending"}
        with _TV_LOCK:
            d = _load_tv()
            d[key] = entry
            _save_tv(d)
        if granted:
            await asyncio.to_thread(_tell_owner, "\u2705 <b>TradingView-access tillagd automatiskt</b>\n"
                        f"Namn: <code>{name}</code>")
        else:
            await asyncio.to_thread(_tell_owner, "\U0001F511 <b>TradingView-access begärd</b>\n"
                        f"Namn: <code>{name}</code>\n"
                        "Lägg till i GRABIT CVD 3.0, GRABIT Flow Profile och GRABIT GEX Levels "
                        "(skriptet → Manage access).")
        return {"ok": True, "status": entry["status"]}

    @app.get("/api/pro/tradingview/status")
    def pro_tradingview_status():
        """För felsökning: är automatiken konfigurerad och hur många begäran finns."""
        sid, ids = _tv_cfg()
        d = _load_tv()
        cnt = {}
        for e in d.values():
            cnt[e.get("status", "?")] = cnt.get(e.get("status", "?"), 0) + 1
        names = [x["name"] for x in _TV_FOUND["scripts"]] if not os.environ.get("TV_PINE_IDS") else []
        return {"auto": bool(sid and ids), "session_set": bool(sid), "scripts": len(ids),
                "script_names": names, "requests": cnt}
