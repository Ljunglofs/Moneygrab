"""
GRABIT  ·  accounts.py
----------------------
Konto-light: e-post + engångskod (inget lösenord). Löser:
  * PRO-återställning på ny enhet (mejl -> kod -> PRO om aktiv prenumeration)
  * Portfölj-/watchlist-synk över enheter
Skickar kod + välkomstmejl via Resend. Env:
  RESEND_API_KEY        din Resend-API-nyckel
  ACCOUNT_EMAIL_FROM    avsändare, t.ex. "GRABIT <noreply@grabitlabs.com>"
Utan RESEND_API_KEY loggas mejlen istället (dev), inget kraschar.
Delar PRO_TOKEN_SECRET med billing_web för signering.
"""

import os
import time
import json
import hmac
import base64
import hashlib
import secrets

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_ACC_FILE = os.path.join(_DATA_DIR, "accounts.json")
_CODE_FILE = os.path.join(_DATA_DIR, "acc_codes.json")
_ACC_DAYS = 120


# --------------------------------------------------------------------------
#  Signerad kontotoken (samma hemlighet som billing_web)
# --------------------------------------------------------------------------
def _secret() -> bytes:
    s = os.environ.get("PRO_TOKEN_SECRET", "").strip()
    if s:
        return s.encode("utf-8")
    seed = os.environ.get("STRIPE_SECRET_KEY", "") or "grabit-insecure-default-set-PRO_TOKEN_SECRET"
    return hashlib.sha256(("grabit-acc::" + seed).encode("utf-8")).digest()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _norm(email) -> str:
    return str(email or "").strip().lower()


def acc_token(email: str, days: int = _ACC_DAYS) -> str:
    payload = {"e": _norm(email), "exp": int(time.time()) + int(days) * 86400}
    raw = _b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return raw + "." + sig


def acc_email(token: str):
    if not token or "." not in token:
        return None
    raw, _, sig = token.partition(".")
    if not hmac.compare_digest(hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest(), sig):
        return None
    try:
        p = json.loads(_b64u_dec(raw))
    except Exception:
        return None
    if int(p.get("exp", 0)) < int(time.time()):
        return None
    return p.get("e")


# --------------------------------------------------------------------------
#  Persistens
# --------------------------------------------------------------------------
def _load(f: str) -> dict:
    try:
        with open(f) as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _save(f: str, d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(f) or ".", exist_ok=True)
        tmp = f + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(d, fh)
        os.replace(tmp, f)
    except Exception:
        pass


# --------------------------------------------------------------------------
#  E-post (Resend)
# --------------------------------------------------------------------------
def _send_email(to: str, subject: str, html: str) -> bool:
    key = os.environ.get("RESEND_API_KEY", "").strip()
    frm = os.environ.get("ACCOUNT_EMAIL_FROM", "GRABIT <onboarding@resend.dev>").strip()
    if not key or requests is None:
        print(f"[accounts] (ingen RESEND_API_KEY) skulle mejla {to}: {subject}")
        return False
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={"from": frm, "to": [to], "subject": subject, "html": html},
            timeout=12,
        )
        return r.status_code < 300
    except Exception as e:
        print("[accounts] mejlfel:", e)
        return False


def _gen_code() -> str:
    return "%06d" % secrets.randbelow(1000000)


_CODE_HTML = ("<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:420px;"
              "margin:0 auto;padding:24px;background:#0A0E12;color:#e8edf5;border-radius:14px'>"
              "<h2 style='color:#F5C542;margin:0 0 8px'>Din GRABIT-kod</h2>"
              "<p style='font-size:34px;font-weight:800;letter-spacing:8px;color:#fff;margin:12px 0'>%s</p>"
              "<p style='color:#c7d0dc;font-size:14px'>Gäller i 10 minuter. Skriv in den i GRABIT för att "
              "låsa upp PRO och synka din portfölj.</p>" "<p style='color:#5b6675;font-size:12px;margin-top:16px;border-top:1px solid rgba(255,255,255,.08);padding-top:12px'>Frågor? Kontakta oss på <a href='mailto:support@grabitlabs.com' style='color:#F5C542;text-decoration:none'>support@grabitlabs.com</a></p></div>")

_WELCOME_HTML = ("<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:460px;"
                 "margin:0 auto;padding:24px;background:#0A0E12;color:#e8edf5;border-radius:14px'>"
                 "<h2 style='color:#F5C542;margin:0 0 8px'>Välkommen till GRABIT</h2>"
                 "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>Ditt konto är nu kopplat till "
                 "den här mejladressen. Din PRO, portfölj och bevakningar följer med dig på alla enheter — "
                 "logga bara in med mejlen.</p>"
                 "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>NASDAQ ROBBER</b> "
                 "— skriv koden <b style='color:#F5C542'>daq</b> i appen för en direktkoll. Han larmar bara "
                 "när det finns ett riktigt entry-läge på NASDAQ 100 (US100).</p>"
                 "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>Slå på notiser</b> "
                 "för robotens larm och signaler — samt pris-, nyhets- och rapportlarm på aktierna i din "
                 "portfölj.</p>"
                 "<p style='color:#F5C542;font-weight:700;margin-top:16px'>Spot the setup. Ignore the noise.</p>" "<p style='color:#5b6675;font-size:12px;margin-top:16px;border-top:1px solid rgba(255,255,255,.08);padding-top:12px'>Frågor? Kontakta oss på <a href='mailto:support@grabitlabs.com' style='color:#F5C542;text-decoration:none'>support@grabitlabs.com</a></p></div>")


# --------------------------------------------------------------------------
#  Rutter
# --------------------------------------------------------------------------
def register(app) -> None:
    from fastapi import Request
    from billing_web import make_token
    try:
        from billing_web import _active_for_email
    except Exception:                   # pragma: no cover
        def _active_for_email(_e):
            return False

    def _pro_bundle(email: str) -> dict:
        pro = bool(_active_for_email(email))
        out = {"pro": pro}
        if pro:
            kh = hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]
            out["pro_token"] = make_token(days=35, extra={"k": kh, "src": "email"})
        return out

    @app.post("/api/account/request")
    async def acc_request(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        email = _norm(body.get("email"))
        if "@" not in email or "." not in email.split("@")[-1]:
            return {"ok": False, "error": "Ogiltig e-postadress"}
        now = int(time.time())
        codes = _load(_CODE_FILE)
        cur = codes.get(email) or {}
        if int(cur.get("h", -1)) == now // 3600 and int(cur.get("n", 0)) >= 5:
            return {"ok": False, "error": "För många försök — vänta en stund."}
        code = _gen_code()
        codes[email] = {"code": code, "exp": now + 600, "h": now // 3600,
                        "n": (int(cur.get("n", 0)) + 1 if int(cur.get("h", -1)) == now // 3600 else 1)}
        codes = {k: v for k, v in codes.items() if int((v or {}).get("exp", 0)) > now - 3600}
        _save(_CODE_FILE, codes)
        _send_email(email, "Din GRABIT-kod: " + code, _CODE_HTML % code)
        return {"ok": True}

    @app.post("/api/account/verify")
    async def acc_verify(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        email = _norm(body.get("email"))
        code = str(body.get("code") or "").strip()
        codes = _load(_CODE_FILE)
        rec = codes.get(email) or {}
        if not rec or str(rec.get("code")) != code or int(rec.get("exp", 0)) < int(time.time()):
            return {"ok": False, "error": "Fel eller utgången kod"}
        codes.pop(email, None)
        _save(_CODE_FILE, codes)
        users = _load(_ACC_FILE)
        is_new = email not in users
        u = users.get(email) or {"created": int(time.time())}
        users[email] = u
        _save(_ACC_FILE, users)
        if is_new:
            _send_email(email, "Välkommen till GRABIT", _WELCOME_HTML)
        out = {"ok": True, "token": acc_token(email),
               "portfolio": u.get("portfolio", []), "watchlist": u.get("watchlist", [])}
        out.update(_pro_bundle(email))
        return out

    @app.post("/api/account/sync")
    async def acc_sync(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        email = acc_email(str(body.get("token") or ""))
        if not email:
            return {"ok": False, "error": "Ej inloggad"}
        users = _load(_ACC_FILE)
        u = users.get(email) or {"created": int(time.time())}
        if isinstance(body.get("portfolio"), list):
            u["portfolio"] = body["portfolio"][:200]
        if isinstance(body.get("watchlist"), list):
            u["watchlist"] = body["watchlist"][:200]
        u["updated"] = int(time.time())
        users[email] = u
        _save(_ACC_FILE, users)
        return {"ok": True}

    @app.get("/api/account/data")
    def acc_data(token: str = ""):
        email = acc_email(token)
        if not email:
            return {"ok": False}
        users = _load(_ACC_FILE)
        u = users.get(email) or {}
        out = {"ok": True, "portfolio": u.get("portfolio", []), "watchlist": u.get("watchlist", [])}
        out.update(_pro_bundle(email))
        return out
