"""
GRABIT  ·  play_billing.py
--------------------------
Verifierar Google Play-prenumerationsköp server-side (så PRO inte kan fejkas)
och delar ut samma signerade PRO-token som Stripe-flödet.

Android-appen (TWA) köper via Google Play Billing (Digital Goods API). Klienten
skickar köp-token hit -> vi frågar Google om den är giltig/aktiv -> vi bekräftar
köpet (annars återbetalar Google efter 3 dygn) -> vi lagrar prenumerationen i
samma entitlement-store som Stripe och returnerar en signerad pro_token.

Env:
  GOOGLE_SA_JSON          hela service-account-JSON-nyckeln (klistra in innehållet)
  ANDROID_PACKAGE_NAME    t.ex. com.grabitlabs.app
Delar PRO_TOKEN_SECRET + entitlements.json med billing_web.
"""

import os
import json
import time
import base64

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

# Samma token-/entitlement-system som Stripe-flödet
from billing_web import make_token, _load_ent, _save_ent, _sub_active

_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API = "https://androidpublisher.googleapis.com/androidpublisher/v3"

_PRO_DAYS = 35          # pro_token giltig i 35 dagar, förnyas när appen re-verifierar
_ACTIVE_STATES = ("SUBSCRIPTION_STATE_ACTIVE", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD")

# Cache för OAuth-access-token
_tok = {"v": "", "exp": 0}


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _sa() -> dict:
    raw = os.environ.get("GOOGLE_SA_JSON", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _access_token() -> str:
    """Skapar (och cachar) ett OAuth2-access-token från service-account-nyckeln."""
    now = int(time.time())
    if _tok["v"] and _tok["exp"] - 60 > now:
        return _tok["v"]
    sa = _sa()
    if not sa or requests is None:
        return ""
    header = {"alg": "RS256", "typ": "JWT"}
    claim = {"iss": sa.get("client_email", ""), "scope": _SCOPE,
             "aud": _TOKEN_URL, "iat": now, "exp": now + 3600}
    signing_input = (_b64u(json.dumps(header, separators=(",", ":")).encode())
                     + "." + _b64u(json.dumps(claim, separators=(",", ":")).encode()))
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_pem_private_key(sa["private_key"].encode("utf-8"), password=None)
        sig = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:
        print("[play_billing] JWT-signering misslyckades:", e)
        return ""
    assertion = signing_input + "." + _b64u(sig)
    try:
        r = requests.post(_TOKEN_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion}, timeout=12)
        j = r.json()
        at = j.get("access_token", "")
        if at:
            _tok["v"] = at
            _tok["exp"] = now + int(j.get("expires_in", 3600))
        return at
    except Exception as e:
        print("[play_billing] token-utbyte fel:", e)
        return ""


def _pkg() -> str:
    return os.environ.get("ANDROID_PACKAGE_NAME", "").strip()


def _get_sub(token: str) -> dict:
    """Hämtar prenumerationens status från Google (subscriptionsv2)."""
    at = _access_token()
    if not (at and token and _pkg()):
        return {}
    url = "%s/applications/%s/purchases/subscriptionsv2/tokens/%s" % (_API, _pkg(), token)
    try:
        r = requests.get(url, headers={"Authorization": "Bearer " + at}, timeout=12)
        if r.status_code >= 300:
            print("[play_billing] subscriptionsv2 %s: %s" % (r.status_code, r.text[:200]))
            return {}
        return r.json() or {}
    except Exception as e:
        print("[play_billing] get_sub fel:", e)
        return {}


def _acknowledge(product_id: str, token: str) -> None:
    """Bekräftar köpet — annars återbetalar Google automatiskt efter 3 dygn."""
    at = _access_token()
    if not (at and product_id and token and _pkg()):
        return
    url = "%s/applications/%s/purchases/subscriptions/%s/tokens/%s:acknowledge" % (
        _API, _pkg(), product_id, token)
    try:
        requests.post(url, headers={"Authorization": "Bearer " + at,
                                    "Content-Type": "application/json"},
                      data="{}", timeout=12)
    except Exception as e:
        print("[play_billing] acknowledge fel:", e)


def _expiry_epoch(sub: dict) -> int:
    """Plockar ut senaste utgångstiden ur lineItems (RFC3339 -> epoch)."""
    best = 0
    for li in (sub.get("lineItems") or []):
        et = li.get("expiryTime") or ""
        if not et:
            continue
        try:
            # RFC3339, t.ex. 2026-08-11T12:00:00.000Z
            s = et.replace("Z", "+0000")
            # trimma till sekunder + tz
            import datetime as _dt
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    ep = int(_dt.datetime.strptime(s, fmt).timestamp())
                    best = max(best, ep)
                    break
                except Exception:
                    continue
        except Exception:
            continue
    return best


def verify_purchase(token: str, email: str = "") -> dict:
    """
    Verifierar en Google Play-köp-token. Vid aktiv prenumeration lagras den i
    entitlement-storen och en signerad pro_token returneras.
    """
    sub = _get_sub(token)
    if not sub:
        return {"ok": False, "error": "Kunde inte verifiera köpet"}
    state = str(sub.get("subscriptionState", ""))
    active = state in _ACTIVE_STATES
    if not active:
        return {"ok": False, "error": "Prenumerationen är inte aktiv", "state": state}

    # Produkt-id (för bekräftelse) + utgångstid
    product_id = ""
    for li in (sub.get("lineItems") or []):
        if li.get("productId"):
            product_id = li["productId"]
            break
    exp = _expiry_epoch(sub)

    # Bekräfta om Google inte redan gjort det
    if str(sub.get("acknowledgementState", "")) == "ACKNOWLEDGEMENT_STATE_PENDING":
        _acknowledge(product_id, token)

    # Lagra i samma entitlement-store som Stripe (sub_id = google-order/token)
    sub_id = "gplay:" + (sub.get("latestOrderId") or token[:32])
    d = _load_ent()
    cur = d["subs"].get(sub_id, {})
    cur["status"] = "active"
    cur["period_end"] = int(exp or 0)
    cur["source"] = "gplay"
    cur["product"] = product_id
    if email:
        cur["email"] = str(email).strip().lower()
    cur["t"] = int(time.time())
    d["subs"][sub_id] = cur
    _save_ent(d)

    pro_token = make_token(plan="pro", days=_PRO_DAYS, extra={"src": "gplay"})
    return {"ok": True, "pro_token": pro_token, "expires": int(exp or 0)}


# --------------------------------------------------------------------------
#  Rutter
# --------------------------------------------------------------------------
def register(app) -> None:
    from fastapi import Request

    @app.post("/api/billing/verify")
    async def billing_verify(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = str(body.get("token") or "").strip()
        email = str(body.get("email") or "").strip()
        if not token:
            return {"ok": False, "error": "Ingen köp-token"}
        return verify_purchase(token, email)

    @app.get("/api/billing/config")
    def billing_config():
        # Låter klienten veta att Google-betalning är korrekt konfigurerad server-side
        return {"ready": bool(_sa() and _pkg())}
