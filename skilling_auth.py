#!/usr/bin/env python3
"""
GRABIT · cTrader OAuth-hjälpare
================================

Kör detta EN gång för att få ut det executorn behöver:
  · access token   -> CT_ACCESS_TOKEN
  · konto-id       -> CT_ACCOUNT_ID  (ctidTraderAccountId)

Använder bara vanlig HTTP (requests) — ingen Twisted/SDK behövs för auth.

Så här:
  export CT_CLIENT_ID=...          # från connect.spotware.com
  export CT_CLIENT_SECRET=...      # din app-hemlighet (dela ALDRIG)
  python skilling_auth.py

Skriptet:
  1. Skriver ut en inloggnings-länk. Öppna den i webbläsaren, godkänn ditt
     DEMO-konto.
  2. Du landar på  http://localhost/?code=XXXX  (sidan laddar inte — normalt).
  3. Klistra in HELA adressen (eller bara code-värdet) i terminalen.
  4. Skriptet byter koden mot en token och listar dina konton med id.
"""
import os
import sys
import json

try:
    import requests
except ImportError:
    print("Saknar 'requests' (pip install requests)"); sys.exit(1)

AUTH_HOST   = os.environ.get("CT_AUTH_HOST", "https://openapi.ctrader.com")
ACCOUNTS_URL = os.environ.get("CT_ACCOUNTS_URL",
                              "https://api.spotware.com/connect/tradingaccounts")
REDIRECT    = os.environ.get("CT_REDIRECT_URI", "http://localhost/")
SCOPE       = os.environ.get("CT_SCOPE", "trading")   # 'trading' = kan lägga ordrar

CLIENT_ID     = os.environ.get("CT_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CT_CLIENT_SECRET", "").strip()


def _need(name, val):
    if not val:
        print(f"Saknar env {name}. Sätt den och kör igen.")
        sys.exit(1)


def main():
    _need("CT_CLIENT_ID", CLIENT_ID)
    _need("CT_CLIENT_SECRET", CLIENT_SECRET)

    from urllib.parse import urlencode, urlparse, parse_qs
    auth_url = f"{AUTH_HOST}/apps/auth?" + urlencode({
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
        "scope": SCOPE, "product": "web"})

    print("\n1) Öppna den här länken i webbläsaren och godkänn ditt DEMO-konto:\n")
    print("   " + auth_url + "\n")
    print("2) Du hamnar på en http://localhost/?code=... som inte laddar (normalt).")
    raw = input("3) Klistra in hela adressen (eller bara code): ").strip()

    code = raw
    if "code=" in raw:
        try:
            code = parse_qs(urlparse(raw).query)["code"][0]
        except Exception:
            code = raw.split("code=", 1)[1].split("&", 1)[0]
    code = code.strip()
    if not code:
        print("Ingen kod hittad."); sys.exit(1)

    print("\nByter kod mot token…")
    r = requests.get(f"{AUTH_HOST}/apps/token", params={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT, "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET}, timeout=20)
    try:
        tok = r.json()
    except Exception:
        print("Oväntat svar:", r.status_code, r.text[:300]); sys.exit(1)
    access = tok.get("accessToken") or tok.get("access_token")
    refresh = tok.get("refreshToken") or tok.get("refresh_token")
    if not access:
        print("Fick ingen token:", json.dumps(tok)[:300]); sys.exit(1)
    print("✅ access token mottagen.")

    print("Hämtar dina konton…")
    ra = requests.get(ACCOUNTS_URL, params={"oauth_token": access}, timeout=20)
    accounts = []
    try:
        data = ra.json()
        accounts = data.get("data") if isinstance(data, dict) else data
    except Exception:
        print("Kunde inte lista konton:", ra.status_code, ra.text[:200])

    print("\n" + "=" * 60)
    print("KLART — lägg in dessa som env på Render (executorn):")
    print("=" * 60)
    print(f"CT_ACCESS_TOKEN={access}")
    if refresh:
        print(f"# (spara även refresh, för förnyelse senare)")
        print(f"CT_REFRESH_TOKEN={refresh}")
    if accounts:
        print("\nDina konton (välj DEMO-kontots id till CT_ACCOUNT_ID):")
        for a in accounts:
            aid = a.get("accountId") or a.get("ctidTraderAccountId")
            num = a.get("accountNumber") or a.get("traderLogin") or "?"
            live = a.get("live")
            kind = "LIVE" if live else "DEMO"
            broker = a.get("brokerName") or a.get("brokerTitleShort") or ""
            print(f"   CT_ACCOUNT_ID={aid}   (#{num} · {kind} · {broker})")
    else:
        print("\nHittade inga konton automatiskt — koppla appen till ditt konto på")
        print("connect.spotware.com under 'Trading Accounts', kör sen igen.")
    print("=" * 60)


if __name__ == "__main__":
    main()
