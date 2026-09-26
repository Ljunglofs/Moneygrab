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
  TV_PINE_IDS           skriptens id:n, kommaseparerade (PUB;xxxx — syns i skriptets URL/källa)
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


def _tv_cfg():
    sid = os.environ.get("TV_SESSIONID", "").strip()
    ids = [p.strip() for p in os.environ.get("TV_PINE_IDS", "").split(",") if p.strip()]
    return sid, ids


def _tv_session():
    import requests
    sid, _ = _tv_cfg()
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


def register(app) -> None:
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
                        "Lägg till i GRABIT Flow Profile, GRABIT CVD och GRABIT GEX Levels "
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
        return {"auto": bool(sid and ids), "scripts": len(ids), "requests": cnt}
