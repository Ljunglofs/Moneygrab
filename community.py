"""
GRABIT  ·  community.py
-----------------------
PRO-förmåner utanför appen: Discord, NASDAQ Robber på Telegram och
GRABIT-indikatorerna (invite-only-skript) på TradingView.

  GET  /api/pro/community?token=   länkar till Discord och Telegram — bara för PRO
  POST /api/pro/tradingview         {token, username} — begär åtkomst till skripten

TradingView har inget API för att ge åtkomst till invite-only-skript. Varje
begäran sparas därför i DATA_DIR/tv_access.json och skickas till ägaren på
Telegram, som lägger till namnet för hand (skriptet -> Manage access).

ENV (Render)
  DISCORD_INVITE_URL    inbjudan till Discord-servern (standard nedan)
  TELEGRAM_INVITE_URL   inbjudan till Telegram-kanalen där roboten postar
  TELEGRAM_TOKEN, CHAT_ID   ägarens Telegram — dit TradingView-begäran skickas
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
        if not _is_pro(str(body.get("token") or "")):
            return JSONResponse({"ok": False, "error": "pro"}, status_code=403)
        name = str(body.get("username") or "").strip().lstrip("@")
        if not _TV_NAME.match(name):
            return JSONResponse({"ok": False, "error": "name"}, status_code=400)
        key = name.lower()
        with _TV_LOCK:
            d = _load_tv()
            prev = d.get(key)
            if prev and time.time() - prev.get("ts", 0) < 3600:
                return {"ok": True, "status": prev.get("status", "pending"), "dup": True}
            d[key] = {"username": name, "ts": int(time.time()),
                      "status": (prev or {}).get("status", "pending")}
            _save_tv(d)
        _tell_owner("\U0001F511 <b>TradingView-access begärd</b>\n"
                    f"Namn: <code>{name}</code>\n"
                    "Lägg till i GRABIT Flow Profile, GRABIT CVD och GRABIT GEX Levels "
                    "(skriptet → Manage access).")
        return {"ok": True, "status": d[key]["status"]}
