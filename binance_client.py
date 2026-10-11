# ============================================================
#  BINANCE  —  publik marknadsdata (spot + USDⓈ-M futures)
#  Binance svarar HTTP 451 från vissa länder (t.ex. USA, där
#  Claude-molnsessionerna körs). Spot faller då tillbaka på
#  spegeln data-api.binance.vision, som har samma publika
#  endpoints. Futures (fapi) saknar spegel — där kastas felet
#  vidare. Render kör i Frankfurt och når båda direkt.
#
#  ANVÄNDNING:
#
#       from binance_client import spot_get, futures_get
#       spot_get("/api/v3/ticker/price", {"symbol": "BTCUSDT"})
#       futures_get("/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"})
# ============================================================

import requests

SPOT_HOSTS = ("https://api.binance.com", "https://data-api.binance.vision")
FUTURES_HOST = "https://fapi.binance.com"
GEO_BLOCKED = 451
TIMEOUT = 10

# Värden som svarade 451 hoppas över resten av processens livstid.
_blocked = set()


def _get(host, path, params):
    r = requests.get(host + path, params=params, timeout=TIMEOUT)
    if r.status_code == GEO_BLOCKED:
        _blocked.add(host)
    r.raise_for_status()
    return r.json()


def spot_get(path, params=None):
    """GET mot spot-API:t; byter till spegeln om api.binance.com är geospärrad."""
    hosts = [h for h in SPOT_HOSTS if h not in _blocked] or [SPOT_HOSTS[-1]]
    for host in hosts[:-1]:
        try:
            return _get(host, path, params)
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != GEO_BLOCKED:
                raise
    return _get(hosts[-1], path, params)


def futures_get(path, params=None):
    """GET mot USDⓈ-M futures. Ingen spegel finns — 451 kastas vidare."""
    return _get(FUTURES_HOST, path, params)
