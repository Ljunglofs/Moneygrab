"""
GRABIT  ·  flash_news.py
------------------------
Flash-nyheter: marknadsflyttande rubriker i realtid (Vita huset, Trump, Fed,
tullar, krig, OPEC ...) från FinancialJuice RSS (gratis).

- Rubrikerna visas överst i dagsnavet, översatta till svenska (en AI-rad per
  rubrik, delas av alla).
- Schemalagda besked ("Trump makes an announcement at 10a ET") får svensk tid,
  läggs in i "Dagens händelser" och ger en påminnelse-notis 10 min innan.
- Notiser: högst 1 per 15 min och 10 per dygn. Rubriker som kommer tätt slås
  ihop till en notis. Tyst 23–07 svensk tid — det som kom under natten skickas
  som en samlad notis kl 07.

Env (valfritt):
  FLASH_FEEDS        kommaseparerade RSS-URL:er (default FinancialJuice)
  FLASH_POLL_SEC     hur ofta vi kollar (default 60)
  FLASH_PUSH         "0" stänger av notiser (flödet visas ändå)
"""

import os
import re
import json
import time
import hashlib
import threading
import datetime as _dt

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    _SE = ZoneInfo("Europe/Stockholm")
except Exception:                       # pragma: no cover
    _ET = _SE = None

DATA_DIR = os.environ.get("DATA_DIR", ".")
_FILE = os.path.join(DATA_DIR, "flash_news.json")
_POLL = int(os.environ.get("FLASH_POLL_SEC", "60") or "60")
_KEEP = 60
_PUSH_GAP = 15 * 60
_PUSH_MAX_DAY = 10
_REMIND_MIN = 10

_HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}
_lock = threading.Lock()


def _feeds():
    raw = os.environ.get("FLASH_FEEDS", "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    return ["https://www.financialjuice.com/feed.ashx?xy=rss"]


# ---------------------------------------------------------------------------
#  Vad räknas som flash? Ord som flyttar hela marknaden.
# ---------------------------------------------------------------------------
_HOT = re.compile(
    r"\b(trump|white house|president|powell|fed chair|fomc|fed'?s|federal reserve|"
    r"rate decision|rate cut|rate hike|emergency|tariffs?|sanctions?|executive order|"
    r"announcement|announces?|press conference|address(?:es)? the nation|"
    r"ceasefire|invasion|invades?|missiles?|airstrikes?|attacks?|war\b|nuclear|"
    r"opec|shutdown|bessent|treasury secretary|xi jinping|\bxi\b|putin|"
    r"default|downgrades?|trading halt|halted|breaking|flash)\b", re.I)
# Datasläpp (Actual/Forecast) visas redan som makroutfall — inte dubbelt här.
_SKIP = re.compile(r"\bactual\b.*\b(forecast|previous)\b|\bprevious\b.*\brevised\b", re.I)

_TIME = re.compile(
    r"(?:\bat\b|@)?\s*\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m?\.?|p\.?m?\.?)?\s*(?:ET|EDT|EST)\b", re.I)
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _now_et():
    return _dt.datetime.now(_ET) if _ET else _dt.datetime.utcnow()


def _sched(title, seen_at):
    """'... announcement on America.gov at 10a ET' -> datetime i ET, annars None."""
    m = _TIME.search(title or "")
    if not m or _ET is None:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if h > 23 or mi > 59:
        return None
    if ap.startswith("p") and h < 12:
        h += 12
    elif ap.startswith("a") and h == 12:
        h = 0
    elif not ap and 1 <= h <= 6:
        h += 12                                   # "at 2 ET" = eftermiddag
    base = _dt.datetime.fromtimestamp(seen_at, _ET)
    day = base.date()
    low = (title or "").lower()
    for i, dn in enumerate(_DAYS):
        if re.search(r"\b" + dn + r"\b", low):
            ahead = (i - day.weekday()) % 7
            day = day + _dt.timedelta(days=ahead)
            break
    if "tomorrow" in low:
        day = base.date() + _dt.timedelta(days=1)
    when = _dt.datetime(day.year, day.month, day.day, h, mi, tzinfo=_ET)
    if when < base - _dt.timedelta(hours=1):    # redan passerat utan veckodag -> strunta i
        return None
    return when


def _clean_title(t):
    t = re.sub(r"^\s*FinancialJuice:\s*", "", t or "").strip()
    return re.sub(r"\s+", " ", t)


# ---------------------------------------------------------------------------
#  Lagring
# ---------------------------------------------------------------------------
def _load():
    try:
        with open(_FILE) as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    d.setdefault("items", [])
    d.setdefault("pending", [])
    d.setdefault("pushes", [])
    d.setdefault("last_push", 0)
    return d


def _save(d):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        d["items"] = d["items"][:_KEEP]
        d["pushes"] = [t for t in d["pushes"] if time.time() - t < 86400]
        tmp = _FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _FILE)
    except Exception as e:
        print("[flash] kunde inte spara:", e)


# ---------------------------------------------------------------------------
#  Hämtning
# ---------------------------------------------------------------------------
def _fetch():
    if requests is None:
        return []
    import xml.etree.ElementTree as ET
    out = []
    for url in _feeds():
        try:
            r = requests.get(url, headers=_HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                el = it.find("title")
                title = _clean_title(el.text if el is not None else "")
                if title:
                    g = it.find("guid")
                    out.append({"title": title,
                                "guid": (g.text if g is not None and g.text else title)})
            r = root = None
        except Exception as e:
            print("[flash] %s: %s" % (url.split("//")[-1][:30], type(e).__name__))
    return out


def _translate(title):
    """Kort svensk version av rubriken (cachad per rubrik i api._ai_text)."""
    try:
        import api as A
        key = "flash:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
        sysp = ("Översätt nyhetsrubriken till kort, korrekt svenska för en trading-app. "
                "Behåll siffror, tider (ET), namn och förkortningar exakt. "
                "Svara bara med den översatta rubriken, inga citattecken.")
        sv = A._ai_text(key, sysp, title, 120, lang="sv")
        sv = (sv or "").strip().strip('"').strip()
        return sv if 0 < len(sv) < 300 else ""
    except Exception:
        return ""


def poll_once():
    now = time.time()
    fresh = [x for x in _fetch() if _HOT.search(x["title"]) and not _SKIP.search(x["title"])]
    if not fresh:
        return 0
    with _lock:
        d = _load()
        first = not d["items"]
        known = {i["id"] for i in d["items"]}
        new = []
        for x in reversed(fresh):                 # äldst först
            iid = hashlib.sha1(x["guid"].encode("utf-8")).hexdigest()[:12]
            if iid in known:
                continue
            known.add(iid)
            it = {"id": iid, "title": x["title"], "ts": int(now)}
            w = _sched(x["title"], now)
            if w:
                it["at"] = int(w.timestamp())
            new.append(it)
    if not new:
        return 0
    for it in new[-8:]:                           # översätt bara det nyaste (kostnad)
        it["sv"] = _translate(it["title"])
    with _lock:
        d = _load()
        d["items"] = list(reversed(new)) + d["items"]
        if not first:                             # första körningen: inga notiser bakåt i tiden
            d["pending"] += [i["id"] for i in new]
        _save(d)
    print("[flash] %d nya" % len(new))
    return len(new)


# ---------------------------------------------------------------------------
#  Notiser
# ---------------------------------------------------------------------------
def _quiet():
    if _SE is None:
        return False
    h = _dt.datetime.now(_SE).hour
    return h >= 23 or h < 7


def _fmt_at(ts):
    if not ts or _SE is None:
        return ""
    return _dt.datetime.fromtimestamp(ts, _SE).strftime("%H:%M")


def _push_tick(PN):
    if PN is None or os.environ.get("FLASH_PUSH", "1") == "0":
        return
    now = time.time()
    with _lock:
        d = _load()
        by_id = {i["id"]: i for i in d["items"]}
        changed = False
        # 1) Påminnelse strax före schemalagda besked (alltid, utom nattetid)
        remind = [i for i in d["items"] if i.get("at") and not i.get("reminded")
                  and 0 <= i["at"] - now <= _REMIND_MIN * 60 + 30]
        for i in remind:
            i["reminded"] = True
            changed = True
        # 2) Nya rubriker: max 1 notis / 15 min, 10 / dygn, tyst på natten
        pend = [by_id[p] for p in d["pending"] if p in by_id and now - by_id[p]["ts"] < 3 * 3600]
        send_batch = []
        if pend and not _quiet() and now - d["last_push"] >= _PUSH_GAP \
                and len([t for t in d["pushes"] if now - t < 86400]) < _PUSH_MAX_DAY:
            send_batch = pend
            d["pending"] = []
            d["last_push"] = now
            d["pushes"].append(now)
            changed = True
        elif len(pend) != len(d["pending"]):
            d["pending"] = [i["id"] for i in pend]
            changed = True
        if changed:
            _save(d)
    for i in remind:
        if _quiet():
            continue
        txt = i.get("sv") or i["title"]
        try:
            PN.send_all("⚡ Om %d min · kl %s" % (max(1, round((i["at"] - now) / 60)), _fmt_at(i["at"])),
                        txt[:150], url="/", tag="flash-remind")
        except Exception as e:
            print("[flash] push-fel:", e)
    if send_batch:
        top = send_batch[-1]                      # nyaste först i notisen
        txt = top.get("sv") or top["title"]
        if top.get("at"):
            txt += " (kl %s svensk tid)" % _fmt_at(top["at"])
        more = len(send_batch) - 1
        title = "⚡ Flash" + (" · +%d till" % more if more else "")
        try:
            PN.send_all(title, txt[:170], url="/", tag="flash")
        except Exception as e:
            print("[flash] push-fel:", e)


# ---------------------------------------------------------------------------
#  Publika hjälpare (används av dagsnavet)
# ---------------------------------------------------------------------------
def recent(limit=12):
    with _lock:
        items = _load()["items"]
    now = time.time()
    out = []
    for i in items[:limit]:
        o = {"id": i["id"], "title": i["title"], "sv": i.get("sv") or "", "ts": i["ts"],
             "time_se": _fmt_at(i["ts"])}
        if i.get("at"):
            o["at"] = i["at"]
            o["at_se"] = _fmt_at(i["at"])
            o["at_et"] = _dt.datetime.fromtimestamp(i["at"], _ET).strftime("%H:%M") if _ET else ""
            o["in_min"] = int((i["at"] - now) // 60)
        out.append(o)
    return out


def scheduled_on(day):
    """Schemalagda besked en viss ET-dag, i dagsnavets händelseformat."""
    out = []
    for i in recent(_KEEP):
        if not i.get("at") or _ET is None:
            continue
        if _dt.datetime.fromtimestamp(i["at"], _ET).date() != day:
            continue
        out.append({"time_et": i["at_et"], "time_se": i["at_se"], "country": "⚡",
                    "title": i["sv"] or i["title"], "title_en": i["title"],
                    "impact": "High", "forecast": "", "previous": "", "flash": True})
    return out


def _loop():
    time.sleep(45)
    try:
        import push_notify as PN
    except Exception:
        PN = None
    while True:
        try:
            poll_once()
        except Exception as e:
            print("[flash] loop-fel:", e)
        try:
            _push_tick(PN)
        except Exception as e:
            print("[flash] push-tick:", e)
        time.sleep(_POLL)


def register(app):
    @app.get("/api/flash")
    def flash_feed(limit: int = 12):
        return {"items": recent(max(1, min(limit, 30)))}

    if os.environ.get("FLASH_DISABLE") != "1":
        threading.Thread(target=_loop, daemon=True).start()
    print("[flash] Flash-nyheter registrerad")
