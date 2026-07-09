"""
GRABIT  ·  billing_web.py
-------------------------
Äkta serversidig PRO-låsning för webben via Lemon Squeezy (Merchant of
Record — de sköter all moms). Ingen inloggning/lösenord: kunden köper på
Lemon Squeezy, får en licensnyckel via mejl, matar in den i appen. Servern
validerar nyckeln mot Lemon Squeezy och utfärdar en HMAC-SIGNERAD token som
inte går att fejka från webbläsarkonsolen. PRO-datan (Insider Flow bortom 2
kort) släpps bara när en giltig token skickas med — det är det verkliga låset.

Fyll i env i Render när Lemon Squeezy-kontot är klart:
    LEMONSQUEEZY_API_KEY        (valfri; skickas med licensvalideringen)
    LEMON_CHECKOUT_URL          köp-länken från din produkt, t.ex.
                                https://<store>.lemonsqueezy.com/buy/<uuid>
    LEMONSQUEEZY_WEBHOOK_SECRET  signeringshemlighet för webhooken
    PRO_TOKEN_SECRET             lång slumpsträng — signerar tokens (VIKTIG)
Bakåtkompatibelt: PRO_UNLOCK_CODES (kommaseparerat) fungerar fortfarande som
gratis-/testkoder.

Paddle är en enkel swap: byt _validate_license() mot Paddles licens-API och
webhook-signaturen — resten (token + gating) är oförändrat.
"""

import os
import time
import json
import hmac
import base64
import hashlib

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

_TOKEN_DAYS = 7                          # token gäller 7 dygn, klienten förnyar


# --------------------------------------------------------------------------
#  Signerad token  (HMAC-SHA256 — kan inte förfalskas utan hemligheten)
# --------------------------------------------------------------------------
def _secret() -> bytes:
    s = os.environ.get("PRO_TOKEN_SECRET", "").strip()
    if s:
        return s.encode("utf-8")
    # Fallback så det funkar innan env satts — härled stabilt ur API-nyckeln
    # om den finns, annars en osäker default (sätt PRO_TOKEN_SECRET i Render!).
    seed = os.environ.get("LEMONSQUEEZY_API_KEY", "") or "grabit-insecure-default-set-PRO_TOKEN_SECRET"
    return hashlib.sha256(("grabit::" + seed).encode("utf-8")).digest()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(plan: str = "pro", days: int = _TOKEN_DAYS, extra: dict = None) -> str:
    payload = {"p": plan, "exp": int(time.time()) + int(days) * 86400}
    if extra:
        payload.update(extra)
    raw = _b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return raw + "." + sig


def verify_token(token: str):
    """Returnerar payload-dict om token är giltig och inte utgången, annars None."""
    if not token or "." not in token:
        return None
    raw, _, sig = token.partition(".")
    good = hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, sig):
        return None
    try:
        payload = json.loads(_b64u_dec(raw))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


# --------------------------------------------------------------------------
#  Licensvalidering  (Lemon Squeezy + bakåtkompatibla env-koder)
# --------------------------------------------------------------------------
def _env_codes() -> list:
    return [c.strip().lower() for c in os.getenv("PRO_UNLOCK_CODES", "").split(",") if c.strip()]


def _validate_license(key: str) -> bool:
    key = (key or "").strip()
    if not key:
        return False
    # 1) Gratis-/testkoder ur env — funkar direkt utan Lemon Squeezy.
    if key.lower() in _env_codes():
        return True
    # 2) Lemon Squeezy licensnyckel.
    if requests is None:
        return False
    try:
        headers = {"Accept": "application/json"}
        api = os.environ.get("LEMONSQUEEZY_API_KEY", "").strip()
        if api:
            headers["Authorization"] = "Bearer " + api
        r = requests.post(
            "https://api.lemonsqueezy.com/v1/licenses/validate",
            data={"license_key": key},
            headers=headers,
            timeout=12,
        )
        j = r.json()
    except Exception:
        return False
    if not j.get("valid"):
        return False
    status = str(((j.get("license_key") or {}).get("status")) or "").lower()
    return status in ("active", "valid")


# --------------------------------------------------------------------------
#  Rutter
# --------------------------------------------------------------------------
def register(app) -> None:
    from fastapi import Request, Response

    @app.get("/api/pro/checkout")
    def pro_checkout():
        """Ger frontenden Lemon Squeezy-köplänken (env LEMON_CHECKOUT_URL)."""
        url = os.environ.get("LEMON_CHECKOUT_URL", "").strip()
        return {"ok": bool(url), "url": url}

    @app.post("/api/pro/activate")
    async def pro_activate(request: Request):
        """Validerar licensnyckel/kod och utfärdar en signerad PRO-token."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        key = str(body.get("key") or body.get("code") or "").strip()
        if _validate_license(key):
            kh = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            return {"ok": True, "token": make_token(extra={"k": kh})}
        return {"ok": False}

    @app.get("/api/pro/verify")
    def pro_verify(token: str = ""):
        """Snabb koll som frontenden kan använda för att avgöra om token lever."""
        p = verify_token(token)
        return {"ok": p is not None, "exp": (p or {}).get("exp")}

    @app.post("/api/webhook/lemon")
    async def lemon_webhook(request: Request):
        """Lemon Squeezy-webhook (signaturverifierad). Kvitterar events."""
        secret = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "").encode("utf-8")
        body = await request.body()
        sig = request.headers.get("X-Signature", "")
        if secret:
            good = hmac.new(secret, body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(good, sig):
                return Response(status_code=401, content="bad signature")
        try:
            evt = json.loads(body or b"{}")
        except Exception:
            evt = {}
        name = (((evt.get("meta") or {}).get("event_name")) or "")
        return {"ok": True, "event": name}
