"""
GRABIT  ·  trump_signal.py
--------------------------
"Trump Signal" — bevakar Donald Trumps Truth Social-konto. Nya inlägg visas i
appen och de som slagit på notiser för det får en push direkt.

Trumps inlägg rör ofta marknaden (tullar, Fed, Kina, olja, enskilda bolag), så
det är en trading-relevant signal.

Env (allt valfritt — bra defaults):
  TRUMP_ACCOUNT_ID     Truth Social-konto-id (default = realDonaldTrump)
  TRUMP_SOURCE_URL     hela käll-URL:en om du vill peka på annan källa/spegel
  TRUMP_SOURCE_TYPE    "mastodon" (default) eller "rss"
  TRUMP_POLL_SEC       hur ofta vi kollar (default 90 s)
  TRUMP_NOTIFY_FILTER  kommaseparerade ord — notera BARA inlägg som matchar
                       (tomt = notera alla). Alla inlägg visas ändå i flödet.

OBS: Truth Social har hårt bot-skydd. Funkar deras API inte från servern —
peka TRUMP_SOURCE_URL på en fungerande RSS-spegel så funkar resten oförändrat.
"""

import os
import re
import json
import time
import html
import threading
from urllib.parse import quote as _quote

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_POSTS_FILE = os.path.join(_DATA_DIR, "trump_posts.json")
_ACCOUNT_ID = os.environ.get("TRUMP_ACCOUNT_ID", "107780257626128497")  # realDonaldTrump
_POLL_SEC = int(os.environ.get("TRUMP_POLL_SEC", "90") or "90")
_MAX_KEEP = 40

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_lock = threading.Lock()
_TAG_RE = re.compile(r"<[^>]+>")


def _source_urls() -> list:
    """En eller flera källor (kommaseparerade i TRUMP_SOURCE_URL). Slås ihop."""
    raw = os.environ.get("TRUMP_SOURCE_URL", "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    # Default: hans Truth Social-inlägg (spegel) + MARKNADS-inriktade Trump-nyheter
    # (Google News-frågan är redan filtrerad mot börs-relevanta ämnen).
    news_q = ("Trump (stocks OR tariff OR tariffs OR Fed OR \"interest rate\" OR economy "
              "OR trade OR market OR China OR oil OR tax OR inflation OR sanctions) when:2d")
    return [
        "https://trumpstruth.org/feed",
        "https://news.google.com/rss/search?q=" + _quote(news_q) + "&hl=en-US&gl=US&ceid=US:en",
    ]


def _type_for(url: str) -> str:
    forced = os.environ.get("TRUMP_SOURCE_TYPE", "").strip().lower()
    if forced:
        return forced
    low = url.lower()
    if "truthsocial.com/api" in low:
        return "mastodon"
    return "rss"


def _clean(txt: str) -> str:
    txt = _TAG_RE.sub(" ", txt or "")
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _load_posts() -> list:
    try:
        with open(_POSTS_FILE) as f:
            return json.load(f) or []
    except Exception:
        return []


def _save_posts(posts) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = _POSTS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(posts[:_MAX_KEEP], f, ensure_ascii=False)
        os.replace(tmp, _POSTS_FILE)
    except Exception as e:
        print("[trump] kunde inte spara:", e)


def _fetch_one(url: str) -> list:
    if requests is None:
        return []
    try:
        r = requests.get(url, headers=_HEADERS, timeout=12)
        if r.status_code != 200:
            print("[trump] %s -> HTTP %s" % (url.split("//")[-1][:30], r.status_code))
            return []
    except Exception as e:
        print("[trump] hämtningsfel:", type(e).__name__)
        return []
    out = []
    if _type_for(url) == "mastodon":
        try:
            for s in (r.json() or []):
                out.append({"id": str(s.get("id") or ""),
                            "text": _clean(s.get("content") or ""),
                            "url": s.get("url") or s.get("uri") or "",
                            "ts": s.get("created_at") or ""})
        except Exception as e:
            print("[trump] json-parse fel:", e)
    else:  # RSS/Atom
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                def _g(tag):
                    el = it.find(tag)
                    return (el.text or "") if el is not None else ""
                tt, dd = _clean(_g("title")), _clean(_g("description"))
                text = dd if len(dd) > len(tt) else tt          # ta den fylligare
                out.append({"id": _g("guid") or _g("link") or text[:40],
                            "text": text, "url": _g("link"), "ts": _g("pubDate")})
        except Exception as e:
            print("[trump] rss-parse fel:", e)
    return [p for p in out if p.get("id") and p.get("text")]


def _fetch() -> list:
    """Hämtar alla källor, slår ihop och dedupar. Tyst [] vid totalt fel."""
    merged, seen = [], set()
    for url in _source_urls():
        for p in _fetch_one(url):
            key = p.get("id") or p.get("text", "")[:40]
            if key in seen:
                continue
            seen.add(key)
            merged.append(p)
    return merged


# Marknadspåverkande ord — bara inlägg som nämner något av dessa behålls/visas.
_MARKET_WORDS = [
    "stock", "stocks", "market", "wall street", "dow", "nasdaq", "s&p", "shares",
    "tariff", "tariffs", "trade", "trade deal", "import", "export", "sanction",
    "fed", "federal reserve", "interest rate", "rate cut", "powell", "inflation",
    "economy", "economic", "gdp", "jobs", "unemployment", "recession",
    "tax", "taxes", "tax cut", "deregulat", "regulation",
    "oil", "energy", "gas", "opec", "crude", "drill",
    "china", "chip", "chips", "semiconductor", "ai ", "crypto", "bitcoin",
    "dollar", "bond", "yields", "deal", "boeing", "tesla", "apple",
]


def _market_words():
    raw = os.environ.get("TRUMP_MARKET_FILTER", "").strip()
    if raw.lower() in ("off", "none", "0"):
        return []                         # avstängt = visa allt
    if raw:
        return [w.strip().lower() for w in raw.split(",") if w.strip()]
    return _MARKET_WORDS


def _is_market(text: str) -> bool:
    words = _market_words()
    if not words:
        return True
    low = " " + (text or "").lower() + " "
    return any(w in low for w in words)


def poll_once(push=None) -> int:
    """Hämtar, marknadsfiltrerar, sparar nya inlägg och pushar. Returnerar antal nya."""
    fresh = [p for p in _fetch() if _is_market(p.get("text", ""))]
    if not fresh:
        return 0
    with _lock:
        old = _load_posts()
        seen = {p.get("id") for p in old}
        new = [p for p in fresh if p.get("id") not in seen]
        if new:
            merged = new + old
            _save_posts(merged)
    if not new:
        return 0
    # Första körningen (tom historik) -> pusha inte allt bakåt i tiden.
    # Notis går till ALLA som slagit på notiser (ingen separat opt-in).
    if old:
        for p in reversed(new):                 # äldsta först
            body = p["text"][:140] + ("…" if len(p["text"]) > 140 else "")
            try:
                if push:
                    push.send_all("The Trump Signal", body,
                                  url=p.get("url") or "/", tag="trump")
            except Exception as e:
                print("[trump] push-fel:", e)
    print("[trump] %d nya marknads-inlägg" % len(new))
    return len(new)


def recent(limit: int = 20) -> list:
    with _lock:
        return _load_posts()[:max(1, min(limit, _MAX_KEEP))]


def _loop():
    time.sleep(20)
    try:
        import push_notify as PN
    except Exception:
        PN = None
    while True:
        try:
            poll_once(PN)
        except Exception as e:
            print("[trump] loop-fel:", e)
        time.sleep(_POLL_SEC)


def start_in_background():
    threading.Thread(target=_loop, daemon=True).start()


def register(app) -> None:
    @app.get("/api/trump")
    def trump_feed(limit: int = 20):
        return {"posts": recent(limit)}

    start_in_background()
