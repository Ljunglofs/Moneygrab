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
  PRO_TRIAL_CODES         provkoder utan kort (kommaseparerat), gäller PRO_TRIAL_DAYS.
                          KOD:ÅÅÅÅ-MM-DD = länken går att lösa in t.o.m. det datumet.
  PRO_TRIAL_DAYS          provperiodens längd i dagar (standard 7)
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

# Stripes kundportal: kunden loggar in med sin mejl och kan avsluta, byta kort
# och se kvitton. Skapad i Stripe (billing_portal.configuration, login_page).
PORTAL_URL = os.getenv("STRIPE_PORTAL_URL", "https://billing.stripe.com/p/login/dRmdRb0ME5y5cyJ7ei4ZG00")

_CODE_DAYS = int(os.getenv("PRO_CODE_DAYS", "365"))   # give-away/egna koder: 1 år
_PAID_DAYS = int(os.getenv("PRO_PAID_DAYS", "2"))     # köp: kort, förnyas via status
_TRIAL_DAYS = int(os.getenv("PRO_TRIAL_DAYS", "7"))   # provkoder: gratis test utan kort

_PRO_WELCOME_HTML = (
    "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:460px;margin:0 auto;"
    "padding:24px;background:#0A0E12;color:#e8edf5;border-radius:14px'>"
    "<img src='https://grabitlabs.com/email-logo.jpg' alt='GRABIT' width='220' "
    "style='width:220px;max-width:80%;display:block;margin:0 auto 18px'>"
    # --- Svenska ---
    "<h2 style='color:#F5C542;margin:0 0 10px'>Välkommen till GRABIT PRO</h2>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>Tack för att du prenumererar! Din PRO är "
    "aktiv och allt är upplåst — GEX-nivåer för NQ &amp; Guld, The Trump Signal, Insider Flow, Ask Grabit och alla screeners.</p>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>På en annan enhet?</b> "
    "Öppna GRABIT, välj “Återställ köp” och logga in med den här "
    "mejladressen — så följer din PRO med överallt.</p>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>Ditt medlemskap</b> finns överst i appen: "
    "gå med i vår Discord, följ NASDAQ Robber på Telegram och skriv ditt TradingView-namn för att få "
    "GRABIT-indikatorerna.</p>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>NASDAQ ROBBER</b> har en egen flik "
    "i appen. Öppna den och skriv koden <b style='color:#F5C542'>daq</b> i hans ruta för en direktkoll "
    "på NASDAQ 100 (US100). Han larmar bara när det finns ett riktigt entry-läge — inga låtsas-signaler.</p>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>Slå på notiser</b> för att få "
    "robotens larm och signaler direkt — samt pris-, nyhets- och rapportlarm på aktierna i din portfölj.</p>"
    "<p style='color:#e8edf5;font-size:14px;line-height:1.6;background:rgba(245,197,66,.08);"
    "border:1px solid rgba(245,197,66,.35);border-radius:10px;padding:12px 14px'><b>Provperiod:</b> "
    "de första 7 dagarna är gratis. Säger du inte upp inom 7 dagar <b>förnyas prenumerationen "
    "automatiskt</b> och det valda priset dras, sedan varje period tills du säger upp. Du får ett "
    "påminnelsemejl innan första dragningen. <a href='https://billing.stripe.com/p/login/dRmdRb0ME5y5cyJ7ei4ZG00' style='color:#F5C542'>Säg upp eller "
    "hantera din prenumeration här</a> (logga in med den här mejladressen).</p>"
    "<p style='color:#8a93a3;font-size:12.5px;margin-top:16px'>Ingen finansiell rådgivning.</p>"
    # --- English ---
    "<div style='border-top:1px solid rgba(255,255,255,.1);margin:20px 0'></div>"
    "<h2 style='color:#F5C542;margin:0 0 10px'>Welcome to GRABIT PRO</h2>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>Thanks for subscribing! Your PRO is active "
    "and everything is unlocked — GEX levels for NQ &amp; Gold, The Trump Signal, Insider Flow, Ask Grabit and all screeners.</p>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>On another device?</b> "
    "Open GRABIT, choose “Restore purchase” and log in with this email — your PRO follows you everywhere.</p>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>Your membership</b> is at the top of the app: "
    "join our Discord, follow NASDAQ Robber on Telegram and enter your TradingView username to get the "
    "GRABIT indicators.</p>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>NASDAQ ROBBER</b> has its own tab in "
    "the app. Open it and type the code <b style='color:#F5C542'>daq</b> in his box for an instant check on "
    "the NASDAQ 100 (US100). It only alerts when there's a genuine entry setup — no fake signals.</p>"
    "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'><b>Turn on notifications</b> to get the "
    "robot's alerts and signals instantly — plus price, news and earnings alerts on the stocks in your "
    "portfolio.</p>"
    "<p style='color:#e8edf5;font-size:14px;line-height:1.6;background:rgba(245,197,66,.08);"
    "border:1px solid rgba(245,197,66,.35);border-radius:10px;padding:12px 14px'><b>Free trial:</b> "
    "the first 7 days are free. If you don't cancel within 7 days, the subscription <b>renews "
    "automatically</b> and the selected price is charged, then every period until you cancel. We email "
    "you a reminder before the first charge. <a href='https://billing.stripe.com/p/login/dRmdRb0ME5y5cyJ7ei4ZG00' style='color:#F5C542'>Cancel or manage your "
    "subscription here</a> (log in with this email address).</p>"
    "<p style='color:#8a93a3;font-size:12.5px;margin-top:16px'>Not financial advice.</p>"
    "<p style='color:#F5C542;font-weight:700;margin-top:14px'>Spot the setup. Ignore the noise.</p>"
    "<p style='color:#5b6675;font-size:12px;margin-top:16px;border-top:1px solid rgba(255,255,255,.08);"
    "padding-top:12px'>Frågor / Questions? <a href='mailto:support@grabitlabs.com' "
    "style='color:#F5C542;text-decoration:none'>support@grabitlabs.com</a></p></div>")


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


def _trial_status(code: str) -> str:
    """"ok", "expired" eller "" (ingen provkod). KOD:ÅÅÅÅ-MM-DD i env sätter
    sista dagen länken går att lösa in (svensk tid, hela dagen)."""
    import datetime as _dt
    code = (code or "").strip().lower()
    if not code:
        return ""
    for raw in os.getenv("PRO_TRIAL_CODES", "").split(","):
        name, _, until = raw.strip().partition(":")
        if name.strip().lower() != code:
            continue
        until = until.strip()
        if until:
            try:
                last = _dt.date.fromisoformat(until)
                today = (_dt.datetime.utcnow() + _dt.timedelta(hours=2)).date()
                if today > last:
                    return "expired"
            except ValueError:
                pass
        return "ok"
    return ""


def _valid_trial(code: str) -> bool:
    return _trial_status(code) == "ok"


# --------------------------------------------------------------------------
#  Provperiodens slutmejl: den som löst in en provkod kan lämna sin mejl och
#  får då ett mejl när de 7 dagarna är slut ("vill du fortsätta med PRO?").
# --------------------------------------------------------------------------
_TRIAL_FILE = os.path.join(os.environ.get("DATA_DIR", "."), "trial_emails.json")


def _trial_load() -> dict:
    try:
        with open(_TRIAL_FILE) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _trial_save(d: dict) -> None:
    try:
        tmp = _TRIAL_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _TRIAL_FILE)
    except Exception as e:
        print("[trial] kunde inte spara:", e)


def _trial_end_html(en: bool) -> str:
    url = "https://grabitlabs.com/?pw=1"
    if en:
        h, p1, p2, b = ("Your free GRABIT PRO trial has ended",
                        "Thanks for trying GRABIT PRO. Your 7 free days are over, so the PRO tools are locked again.",
                        "Want to keep the GEX levels, The Trump Signal, Insider Flow, Ask Grabit and all screeners?",
                        "Continue with PRO")
    else:
        h, p1, p2, b = ("Din gratisperiod av GRABIT PRO är slut",
                        "Tack för att du testade GRABIT PRO. Dina 7 gratisdagar är över, så PRO-verktygen är låsta igen.",
                        "Vill du behålla GEX-nivåerna, The Trump Signal, Insider Flow, Ask Grabit och alla screeners?",
                        "Fortsätt med PRO")
    return ("<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:460px;margin:0 auto;"
            "padding:24px;background:#0A0E12;color:#e8edf5;border-radius:14px'>"
            "<img src='https://grabitlabs.com/email-logo.jpg' alt='GRABIT' width='220' "
            "style='width:220px;max-width:80%;display:block;margin:0 auto 18px'>"
            "<h2 style='color:#F5C542;margin:0 0 10px'>" + h + "</h2>"
            "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>" + p1 + "</p>"
            "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>" + p2 + "</p>"
            "<p style='text-align:center;margin:22px 0 6px'><a href='" + url + "' style='background:#F5C542;"
            "color:#0A0E12;font-weight:700;text-decoration:none;padding:12px 22px;border-radius:10px;"
            "display:inline-block'>" + b + " &rarr;</a></p></div>")


def _trial_mail_loop():
    """Var 30:e minut: mejla dem vars provperiod gått ut och som inte fått mejlet än."""
    try:
        from accounts import _send_email
    except Exception:
        _send_email = None
    while True:
        try:
            d = _trial_load()
            now = int(time.time())
            changed = False
            for email, e in d.items():
                if e.get("sent") or int(e.get("exp", 0)) > now:
                    continue
                en = str(e.get("lang", "sv")).startswith("en")
                subj = "Your free GRABIT PRO trial has ended" if en else "Din gratisperiod av GRABIT PRO är slut"
                ok = bool(_send_email and _send_email(email, subj, _trial_end_html(en)))
                e["sent"] = now if ok else 0
                e["tries"] = int(e.get("tries", 0)) + 1
                if ok or e["tries"] >= 3:
                    e["sent"] = e["sent"] or -1      # -1 = gav upp efter 3 försök
                changed = True
            if changed:
                _trial_save(d)
        except Exception as ex:
            print("[trial] mejlslinga:", ex)
        time.sleep(1800)


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


def _trial_reminder(sub: dict) -> None:
    """Påminnelsemejl innan första dragningen: när provperioden slutar, vad
    som dras och hur man säger upp. Mejladressen sparades vid köpet."""
    try:
        sub_id = str(sub.get("id") or "")
        ent = _load_ent()["subs"].get(sub_id) or {}
        email = ent.get("email")
        if not email:
            print("[billing] provperiods-påminnelse: ingen mejl för", sub_id)
            return
        end = int(sub.get("trial_end") or 0)
        import datetime as _dt
        day = _dt.datetime.utcfromtimestamp(end).strftime("%Y-%m-%d") if end else ""
        amt, per = "", ""
        try:
            it = ((sub.get("items") or {}).get("data") or [{}])[0]
            pr = it.get("price") or {}
            if pr.get("unit_amount") is not None:
                amt = "%s kr" % format(int(pr["unit_amount"]) // 100, ",").replace(",", " ")
            per = (pr.get("recurring") or {}).get("interval") or ""
        except Exception:
            pass
        per_sv = {"month": "/mån", "year": "/år"}.get(per, "")
        per_en = {"month": "/month", "year": "/year"}.get(per, "")
        html = (
            "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:460px;margin:0 auto;"
            "padding:24px;background:#0A0E12;color:#e8edf5;border-radius:14px'>"
            "<h2 style='color:#F5C542;margin:0 0 10px'>Din provperiod slutar snart</h2>"
            "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>Din gratis provperiod av GRABIT PRO "
            "slutar <b>%s</b>. Då förnyas prenumerationen automatiskt och <b>%s%s</b> dras från ditt kort.</p>"
            "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>Vill du inte fortsätta? Säg upp före "
            "%s så dras ingenting: <a href='%s' style='color:#F5C542'>Säg upp eller hantera prenumerationen</a> "
            "(logga in med den här mejladressen).</p>"
            "<div style='border-top:1px solid rgba(255,255,255,.1);margin:18px 0'></div>"
            "<h2 style='color:#F5C542;margin:0 0 10px'>Your trial ends soon</h2>"
            "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>Your free GRABIT PRO trial ends on "
            "<b>%s</b>. The subscription then renews automatically and <b>%s%s</b> is charged to your card.</p>"
            "<p style='color:#c7d0dc;font-size:14.5px;line-height:1.6'>Don't want to continue? Cancel before "
            "%s and nothing is charged: <a href='%s' style='color:#F5C542'>Cancel or manage your subscription</a> "
            "(log in with this email address).</p>"
            "<p style='color:#5b6675;font-size:12px;margin-top:16px'>Frågor / Questions? "
            "<a href='mailto:support@grabitlabs.com' style='color:#F5C542'>support@grabitlabs.com</a></p></div>"
        ) % (day, amt or "priset", per_sv, day, PORTAL_URL, day, amt.replace(" kr", "").join(["SEK ", ""]) if amt else "the price",
             per_en, day, PORTAL_URL)
        import accounts as _acc
        _acc._send_email(email, "GRABIT PRO: din provperiod slutar %s / your trial ends %s" % (day, day), html)
        print("[billing] provperiods-påminnelse skickad för", sub_id)
    except Exception as e:
        print("[billing] provperiods-påminnelse fel:", e)


def _period_end(sub: dict):
    """Periodslut för en prenumeration. Sedan API-versionen 2025-03-31 ligger
    fältet på prenumerationsraderna, inte på själva prenumerationen."""
    pe = sub.get("current_period_end")
    if pe:
        return pe
    items = ((sub.get("items") or {}).get("data")) or []
    ends = [i.get("current_period_end") for i in items if i.get("current_period_end")]
    if ends:
        return max(ends)
    return sub.get("trial_end")


def _revoke_extras(sub_id) -> None:
    """Prenumerationen har upphört: ta bort det som ligger utanför appen
    (TradingView-skripten). Appen själv låses via /api/pro/status."""
    sub_id = str(sub_id or "").strip()
    if not sub_id:
        return
    try:
        d = _load_ent()
        keys = [hashlib.sha256(cid.encode("utf-8")).hexdigest()[:16]
                for cid, sid in d["cid2sub"].items() if sid == sub_id]
        # Den som återställt PRO via mejl har en nyckel byggd på mejladressen.
        em = (d["subs"].get(sub_id) or {}).get("email")
        if em:
            keys.append(hashlib.sha256(em.encode("utf-8")).hexdigest()[:16])
        if keys:
            import threading
            import community
            threading.Thread(target=community.revoke_for_keys, args=(keys,), daemon=True).start()
            try:
                import discord_link
                threading.Thread(target=discord_link.revoke_for_keys, args=(keys,), daemon=True).start()
            except Exception as e:
                print("[billing] discord-revoke fel:", e)
    except Exception as e:
        print("[billing] revoke-fel:", e)


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
        if _trial_status(code) == "expired":
            return {"ok": False, "expired": True}
        if _valid_trial(code):
            # Provkod: PRO i _TRIAL_DAYS dagar, sedan låser appen igen (exp).
            kh = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
            exp = int(time.time()) + _TRIAL_DAYS * 86400
            return {"ok": True, "trial": True, "days": _TRIAL_DAYS, "exp": exp,
                    "token": make_token(days=_TRIAL_DAYS, extra={"k": kh, "src": "trial"})}
        return {"ok": False}

    @app.post("/api/pro/trial_email")
    async def pro_trial_email(request: Request):
        """Sparar mejlen för den som löst in en provkod. Kräver provtoken, så
        bara den som faktiskt har en provperiod kan lägga till sig."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        email = str(body.get("email") or "").strip().lower()[:200]
        p = verify_token(str(body.get("token") or ""))
        if not p or p.get("src") != "trial" or "@" not in email or "." not in email.split("@")[-1]:
            return {"ok": False}
        d = _trial_load()
        d[email] = {"exp": int(p.get("exp", 0)), "lang": "en" if str(body.get("lang", "")).startswith("en") else "sv",
                    "sent": 0, "t": int(time.time())}
        _trial_save(d)
        return {"ok": True}

    @app.on_event("startup")
    def _trial_start():
        import threading
        threading.Thread(target=_trial_mail_loop, daemon=True).start()

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
                if email:
                    # Välkomstmejl vid köp (lazy import undviker cirkulär import).
                    try:
                        import accounts as _acc
                        _acc._send_email(email, "Välkommen till GRABIT PRO", _PRO_WELCOME_HTML)
                    except Exception:
                        pass
                handled = True

        elif etype in ("customer.subscription.created", "customer.subscription.updated"):
            _set_sub(obj.get("id"), status=obj.get("status"),
                     period_end=_period_end(obj))
            if str(obj.get("status", "")).lower() not in ("active", "trialing", "past_due"):
                _revoke_extras(obj.get("id"))
            handled = True

        elif etype == "customer.subscription.trial_will_end":
            # Stripe skickar detta 3 dagar innan provperioden slutar.
            _trial_reminder(obj)
            handled = True

        elif etype == "customer.subscription.deleted":
            _set_sub(obj.get("id"), status="canceled")
            _revoke_extras(obj.get("id"))
            handled = True

        return {"ok": True, "event": etype, "handled": handled}
