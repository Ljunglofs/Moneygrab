"""
GRABIT  ·  billing_web.py
-------------------------
Äkta serversidig PRO-låsning för webben via STRIPE (Payment Links + Managed
Payments — Stripe sköter momsen åt dig). Ingen inloggning/lösenord:

  1. Kunden klickar "Köp" -> skickas till din Stripe Payment Link, med en
     anonym `cid` som `client_reference_id`.
  2. Kunden betalar. Stripe skickar `checkout.session.completed` till vår
     webhook, som kopplar betalningen till `cid`.
  3. Appen pollar /api/pro/claim?cid=... när kunden kommer tillbaka och får
     en HMAC-SIGNERAD token. PRO-datan (Insider Flow bortom 2 kort) släpps
     bara när en giltig token skickas med — det är det verkliga låset, kan
     inte fejkas från webbläsarkonsolen.

Fyll i env i Render:
    STRIPE_PAYMENT_LINK     köp-länken, t.ex. https://buy.stripe.com/xxxxx
    STRIPE_WEBHOOK_SECRET   signeringshemlighet (whsec_...) för webhooken
    PRO_TOKEN_SECRET        lång slumpsträng — signerar tokens (VIKTIG)
    STRIPE_SECRET_KEY        (valfri) sk_..., för framtida API-anrop
Bakåtkompatibelt: PRO_UNLOCK_CODES (kommaseparerat) fungerar som gratis-/testkoder.
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
    seed = os.environ.get("STRIPE_SECRET_KEY", "") or "grabit-insecure-default-set-PRO_TOKEN_SECRET"
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
#  Gratis-/testkoder (env)
# --------------------------------------------------------------------------
def _env_codes() -> list:
    return [c.strip().lower() for c in os.getenv("PRO_UNLOCK_CODES", "").split(",") if c.strip()]


def _valid_code(code: str) -> bool:
    code = (code or "").strip().lower()
    return bool(code) and code in _env_codes()


# --------------------------------------------------------------------------
#  Auto-upplåsning: koppla ett köp till en anonym "cid". Webhooken lagrar
#  {cid -> betald}; /api/pro/claim utfärdar token när kunden kommer tillbaka.
# --------------------------------------------------------------------------
_CLAIMS_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "pro_claims.json")


def _load_claims() -> dict:
    try:
        with open(_CLAIMS_FILE) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_claims(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_CLAIMS_FILE) or ".", exist_ok=True)
        cutoff = int(time.time()) - 3 * 86400        # rensa poster äldre än 3 dygn
        d = {k: v for k, v in d.items() if int((v or {}).get("t", 0)) >= cutoff}
        tmp = _CLAIMS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _CLAIMS_FILE)
    except Exception:
        pass


def _store_claim(cid: str) -> None:
    cid = (cid or "").strip()
    if not cid:
        return
    d = _load_claims()
    d[cid] = {"paid": True, "t": int(time.time())}
    _save_claims(d)


def _has_claim(cid: str) -> bool:
    cid = (cid or "").strip()
    if not cid:
        return False
    return bool((_load_claims().get(cid) or {}).get("paid"))


def _drop_claim(cid: str) -> None:
    d = _load_claims()
    if cid in d:
        d.pop(cid, None)
        _save_claims(d)


# --------------------------------------------------------------------------
#  Stripe webhook-signatur  (schema: "t=<ts>,v1=<hex>")
# --------------------------------------------------------------------------
def _stripe_sig_ok(body: bytes, header: str, secret: str) -> bool:
    if not secret:
        return True                                  # ingen secret satt -> hoppa (dev)
    try:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        t = parts.get("t", "")
        v1 = parts.get("v1", "")
        if not (t and v1):
            return False
        signed = t.encode("ascii") + b"." + body
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, v1):
            return False
        # avvisa gamla signaturer (> 5 min) för att stoppa replay
        if abs(int(time.time()) - int(t)) > 300:
            return False
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
#  Rutter
# --------------------------------------------------------------------------
def register(app) -> None:
    from fastapi import Request, Response

    @app.get("/api/pro/checkout")
    def pro_checkout():
        """Ger frontenden Stripe Payment Link-URL:en (env STRIPE_PAYMENT_LINK)."""
        url = os.environ.get("STRIPE_PAYMENT_LINK", "").strip()
        return {"ok": bool(url), "url": url}

    @app.post("/api/pro/activate")
    async def pro_activate(request: Request):
        """Löser upp via gratis-/testkod (PRO_UNLOCK_CODES) och ger en token."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        code = str(body.get("key") or body.get("code") or "").strip()
        if _valid_code(code):
            kh = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
            return {"ok": True, "token": make_token(extra={"k": kh})}
        return {"ok": False}

    @app.get("/api/pro/verify")
    def pro_verify(token: str = ""):
        """Snabb koll som frontenden kan använda för att avgöra om token lever."""
        p = verify_token(token)
        return {"ok": p is not None, "exp": (p or {}).get("exp")}

    @app.get("/api/pro/claim")
    def pro_claim(cid: str = ""):
        """Auto-upplåsning: utfärdar token om webhooken markerat denna cid
        som betald. Frontenden pollar denna efter checkout."""
        if _has_claim(cid):
            _drop_claim(cid)
            kh = hashlib.sha256((cid or "").encode("utf-8")).hexdigest()[:16]
            return {"ok": True, "token": make_token(extra={"k": kh})}
        return {"ok": False}

    @app.post("/api/webhook/stripe")
    async def stripe_webhook(request: Request):
        """Stripe-webhook (signaturverifierad). Kopplar checkout -> cid så
        appen kan auto-låsa upp när kunden kommer tillbaka."""
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
        body = await request.body()
        sig = request.headers.get("Stripe-Signature", "")
        if not _stripe_sig_ok(body, sig, secret):
            return Response(status_code=401, content="bad signature")
        try:
            evt = json.loads(body or b"{}")
        except Exception:
            evt = {}
        etype = str(evt.get("type") or "")
        obj = ((evt.get("data") or {}).get("object")) or {}
        linked = False
        if etype == "checkout.session.completed":
            cid = str(obj.get("client_reference_id") or "").strip()
            paid = str(obj.get("payment_status") or "").lower() in ("paid", "no_payment_required")
            if cid and paid:
                _store_claim(cid)
                linked = True
        return {"ok": True, "event": etype, "linked": linked}
