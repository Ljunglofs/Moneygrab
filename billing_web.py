"""
GRABIT  ·  billing_web.py
-------------------------
Äkta serversidig PRO-låsning för webben via STRIPE (Payment Links + Managed
Payments — Stripe sköter momsen åt dig). Ingen inloggning/lösenord.

MODELL
  * Ingen kunddatabas i kontobemärkelse. "Användaren" = enheten, som bär en
    HMAC-signerad token i webbläsaren. Token släpper PRO-datan server-sidan.
  * Prenumerationsstatus är server-sanning: Stripe-webhooken uppdaterar en
    entitlement (aktiv / avslutad / utgången) per prenumeration, kopplad till
    en anonym `cid` (client_reference_id från checkouten).
  * Appen frågar /api/pro/status vid varje öppning:
      aktiv     -> ny kort token  (fortsatt upplåst)
      avslutad  -> ingen token     (appen låser & blurrar igen)
  * Köp-tokens är korta (dagar) och förnyas via status → avslut slår igenom
    nästa gång appen öppnas. Kod-upplåsning styrs av koden (lång token) och
    påverkas inte av prenumerationsstatus.

ENV (Render)
  STRIPE_PAYMENT_LINK     köp-länken, https://buy.stripe.com/...
  STRIPE_WEBHOOK_SECRET   whsec_... för webhooken
  PRO_TOKEN_SECRET        lång slumpsträng — signerar tokens (VIKTIG)
  PRO_UNLOCK_CODES        give-away/egna koder (kommaseparerat)
Webhooken måste prenumerera på: checkout.session.completed,
customer.subscription.updated, customer.subscription.deleted.
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

_CODE_DAYS = int(os.getenv("PRO_CODE_DAYS", "365"))   # give-away/egna koder: 1 år
_PAID_DAYS = int(os.getenv("PRO_PAID_DAYS", "2"))     # köp: kort, förnyas via status


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


def make_token(plan: str = "pro", days: int = 7, extra: dict = None) -> str:
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
#  Entitlements (prenumerationsstatus)  — server-sanning, uppdateras av webhook
#    subs:    { <sub_id>: {status, period_end, email} }
#    cid2sub: { <cid>: <sub_id> }        (kopplar enheten till prenumerationen)
# --------------------------------------------------------------------------
_ENT_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "entitlements.json")


def _load_ent() -> dict:
    try:
        with open(_ENT_FILE) as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    d.setdefault("subs", {})
    d.setdefault("cid2sub", {})
    return d


def _save_ent(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_ENT_FILE) or ".", exist_ok=True)
        tmp = _ENT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _ENT_FILE)
    except Exception:
        pass


def _sub_active(ent: dict) -> bool:
    if not ent:
        return False
    if str(ent.get("status", "")).lower() not in ("active", "trialing"):
        return False
    pe = int(ent.get("period_end") or 0)
    if pe and pe < int(time.time()):
        return False
    return True


def _set_sub(sub_id, status=None, period_end=None, email=None) -> None:
    sub_id = str(sub_id or "").strip()
    if not sub_id:
        return
    d = _load_ent()
    cur = d["subs"].get(sub_id, {})
    if status is not None:
        cur["status"] = str(status)
    if period_end is not None:
        cur["period_end"] = int(period_end or 0)
    if email:
        cur["email"] = str(email).lower()
    cur["t"] = int(time.time())
    d["subs"][sub_id] = cur
    _save_ent(d)


def _map_cid(cid, sub_id) -> None:
    cid = str(cid or "").strip()
    sub_id = str(sub_id or "").strip()
    if not (cid and sub_id):
        return
    d = _load_ent()
    d["cid2sub"][cid] = sub_id
    _save_ent(d)


def _active_for_cid(cid: str) -> bool:
    cid = str(cid or "").strip()
    if not cid:
        return False
    d = _load_ent()
    sub_id = d["cid2sub"].get(cid)
    if not sub_id:
        return False
    return _sub_active(d["subs"].get(sub_id))


def _active_for_email(email: str) -> bool:
    """Har den här mejladressen en aktiv prenumeration? (för e-post-återställning)"""
    email = str(email or "").strip().lower()
    if not email:
        return False
    d = _load_ent()
    for sub in d["subs"].values():
        if str((sub or {}).get("email", "")).lower() == email and _sub_active(sub):
            return True
    return False


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
        if abs(int(time.time()) - int(t)) > 300:     # replayskydd (5 min)
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
    def pro_checkout(plan: str = "monthly"):
        """Ger frontenden rätt Stripe Payment Link (månad/år)."""
        if plan == "annual":
            url = (os.environ.get("STRIPE_PAYMENT_LINK_ANNUAL", "").strip()
                   or os.environ.get("STRIPE_PAYMENT_LINK", "").strip())
        else:
            url = os.environ.get("STRIPE_PAYMENT_LINK", "").strip()
        return {"ok": bool(url), "url": url, "plan": plan}

    @app.post("/api/pro/activate")
    async def pro_activate(request: Request):
        """Löser upp via gratis-/testkod (PRO_UNLOCK_CODES) och ger en lång token."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        code = str(body.get("key") or body.get("code") or "").strip()
        if _valid_code(code):
            kh = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
            return {"ok": True, "token": make_token(days=_CODE_DAYS, extra={"k": kh, "src": "code"})}
        return {"ok": False}

    @app.get("/api/pro/verify")
    def pro_verify(token: str = ""):
        p = verify_token(token)
        return {"ok": p is not None, "exp": (p or {}).get("exp")}

    def _status_for(cid: str) -> dict:
        if _active_for_cid(cid):
            kh = hashlib.sha256((cid or "").encode("utf-8")).hexdigest()[:16]
            return {"ok": True, "pro": True,
                    "token": make_token(days=_PAID_DAYS, extra={"k": kh, "src": "sub"})}
        return {"ok": True, "pro": False}

    @app.get("/api/pro/status")
    def pro_status(cid: str = ""):
        """Prenumerationsstatus för enheten. Aktiv -> token, annars pro=false
        (appen låser & blurrar igen). Frontenden pollar denna vid varje öppning."""
        return _status_for(cid)

    @app.get("/api/pro/claim")
    def pro_claim(cid: str = ""):
        """Auto-upplåsning direkt efter köp (samma logik som status)."""
        return _status_for(cid)

    @app.post("/api/webhook/stripe")
    async def stripe_webhook(request: Request):
        """Stripe-webhook (signaturverifierad). Håller prenumerationsstatusen
        uppdaterad så avslut slår igenom i appen."""
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
        handled = False

        if etype == "checkout.session.completed":
            cid = str(obj.get("client_reference_id") or "").strip()
            sub_id = obj.get("subscription") or ("sess_" + str(obj.get("id") or ""))
            paid = str(obj.get("payment_status") or "").lower() in ("paid", "no_payment_required")
            email = ((obj.get("customer_details") or {}).get("email")) or obj.get("customer_email")
            if paid:
                # markera aktiv direkt; period_end fylls på av subscription-eventet
                _set_sub(sub_id, status="active", email=email)
                if cid:
                    _map_cid(cid, sub_id)
                handled = True

        elif etype in ("customer.subscription.created", "customer.subscription.updated"):
            _set_sub(obj.get("id"), status=obj.get("status"),
                     period_end=obj.get("current_period_end"))
            handled = True

        elif etype == "customer.subscription.deleted":
            _set_sub(obj.get("id"), status="canceled")
            handled = True

        return {"ok": True, "event": etype, "handled": handled}
