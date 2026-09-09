"""
GEX NOTIFY  ·  gex_notify.py
-----------------------------
Räknar dagens GEX-nivåer (gex_cli.run) och skickar strängarna till Telegram
som kodblock (tryck = kopiera i telefonen). Skriver även levels/NQ.txt,
levels/GC.txt och levels/latest.json så de går att öppna via GitHub.

Körs av .github/workflows/gex_daily.yml varje vardag, eller lokalt:
    TELEGRAM_TOKEN=... CHAT_ID=... python gex_notify.py

Utan TELEGRAM_TOKEN/CHAT_ID skrivs bara filerna.

GitHub Actions startar schemalagda jobb när de har plats — ibland timmar sent.
Därför körs jobbet flera gånger per fönster (london/usa/rth) med:
    python gex_notify.py --window london --once
--once  = hoppa över körningen om fönstret redan skickats i dag (levels/sent.json).
--force = skicka ändå (manuell körning) men lås fönstret så schemat inte dubblar.
Misslyckad körning markeras som försök: nästa retry räknar om, men skickar bara
ett nytt meddelande om det gick vägen (inga upprepade felmeddelanden).
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import gex_cli as CLI

OUT_DIR = os.environ.get("GEX_OUT_DIR", "levels")
STO = ZoneInfo("Europe/Stockholm")
WINDOWS = ("london", "usa", "rth")
MAX_FALLBACK_DAYS = int(os.environ.get("GEX_FALLBACK_DAYS", "4"))   # hur gamla reservnivåer som får skickas


def _state_path():
    return os.path.join(OUT_DIR, "sent.json")


def read_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(state):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def today_str():
    return datetime.now(STO).date().isoformat()


def mark(window, ok):
    """Notera dagens försök för fönstret. ok=True låser fönstret för dagen."""
    if not window:
        return
    state = read_state()
    entry = state.get(window) or {}
    if entry.get("day") == today_str() and entry.get("ok"):
        return
    state[window] = {"day": today_str(), "ok": bool(ok),
                     "at": datetime.now(STO).isoformat(timespec="seconds")}
    write_state(state)


def status(window):
    """(redan_skickat, redan_forsokt) för fönstret i dag."""
    entry = (read_state().get(window) or {}) if window else {}
    if entry.get("day") != today_str():
        return False, False
    return bool(entry.get("ok")), True


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(results, fallback=None):
    now = datetime.now(STO).strftime("%a %d %b %H:%M")
    parts = [f"\U0001F4CB <b>GEX Daily Levels</b> · {now}"]
    for r in results:
        if r.get("error"):
            parts.append(f"\n<b>{r['inst']}</b>: {_esc(r['error'])}")
            old = (fallback or {}).get(r["inst"])
            if usable_fallback(old):
                stamp = str(old.get("_generated", ""))[:16].replace("T", " ")
                parts.append(
                    f"⚠ Reserv: senast dugliga nivåer ({stamp}). Väggarna bygger på open "
                    f"interest och flyttar sig sällan över natten — EM-band och öppningsgrid "
                    f"är däremot gamla.\n<pre>{_esc(old['string'])}</pre>")
            continue
        flag = " ⚠ flip osäker" if r.get("flip_uncertain") else ""
        def num(v, d=0):
            """Tal utan efterhängande nollor, '–' när värdet saknas."""
            if v is None:
                return "–"
            return f"{v:.{d}f}".rstrip("0").rstrip(".") if d else f"{v:.0f}"

        parts.append(
            f"\n<b>{r['inst']}</b> {r['fut_price']:.2f} · gamma {r['regime']}{flag}"
            + (f" · {r['source']}" if r.get("source") else "")
            + (" · kvot från RTH" if r.get("ratio_source", "live") != "live" else "") + "\n"
            f"Call Wall <b>{num(r['call_wall'], 2)}</b> · Put Wall <b>{num(r['put_wall'], 2)}</b> · Flip {num(r['zero_gamma'], 2)}\n"
            f"EM ±{num(r['expected_move'])} · Max Pain {num(r['max_pain'], 2)} · HGEX {num(r['hgex'], 2)}\n"
            f"Fält: {'NQ' if r['inst'] == 'NQ' else 'GC'} — levels string ↓\n"
            f"<pre>{_esc(r['string'])}</pre>")
    return "\n".join(parts)


def send_telegram(text):
    token, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("CHAT_ID", "")
    if not (token and chat):
        print("TELEGRAM_TOKEN/CHAT_ID saknas — skickar inte.")
        return False
    import requests
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=20)
    ok = r.status_code == 200
    print("Telegram:", "skickat" if ok else f"{r.status_code} {r.text[:200]}")
    return ok


def _previous():
    """Senast sparade nivåer per instrument, så ett misslyckat försök inte
    raderar det som gick att öppna i telefonen. Varje post får datumet med sig."""
    try:
        with open(os.path.join(OUT_DIR, "latest.json"), encoding="utf-8") as f:
            d = json.load(f)
        gen = d.get("generated") or ""
        return {l["inst"]: dict(l, _generated=l.get("_generated") or gen)
                for l in d.get("levels", []) if l.get("inst")}
    except Exception:
        return {}


def usable_fallback(old, max_days=MAX_FALLBACK_DAYS):
    """Duger de sparade nivåerna som reserv? Open interest ändras en gång per
    dygn, så gårdagens väggar är fortfarande vettiga — förra veckans är det inte."""
    if not old or old.get("error") or not old.get("string"):
        return False
    try:
        gen = datetime.fromisoformat(old["_generated"]).date()
    except Exception:
        return False
    return 0 <= (datetime.now(STO).date() - gen).days <= max_days


def save_ratios(results):
    """Sparar kvoten futures/ETF när den mätts med USA-börsen öppen. Morgon- och
    nattkörningar använder den i stället för att räkna mot gårdagens ETF-stängning."""
    path = os.path.join(OUT_DIR, "ratio.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    changed = False
    for r in results:
        if r.get("error") or not r.get("ratio_rth") or not r.get("ratio"):
            continue
        d[r["inst"]] = {"ratio": round(float(r["ratio"]), 4), "at": r.get("ratio_at"),
                        "etf": r.get("etf_spot"), "fut": r.get("fut_price")}
        changed = True
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)


def write_files(results):
    os.makedirs(OUT_DIR, exist_ok=True)
    save_ratios(results)
    prev = _previous()
    out = []
    for r in results:
        if r.get("error"):
            old = prev.get(r["inst"])
            if old and not old.get("error"):
                old = dict(old); old["stale"] = True; old["last_error"] = r["error"]
                out.append(old)          # behåll gårdagens/senaste dugliga nivåer
                continue
            out.append(r)
            continue
        out.append(r)
        with open(os.path.join(OUT_DIR, f"{r['inst']}.txt"), "w", encoding="utf-8") as f:
            f.write(r["string"] + "\n")
    with open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.now(STO).isoformat(timespec="seconds"), "levels": out},
                  f, indent=2, ensure_ascii=False)


def parse_args(argv):
    """-> (window, once, force, instrument). Tar både --window=usa och --window usa."""
    window, once, force, insts = None, False, False, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--window="):
            window = a.split("=", 1)[1].lower()
        elif a == "--window" and i + 1 < len(argv):
            window = argv[i + 1].lower()
            i += 1
        elif a == "--once":
            once = True
        elif a == "--force":
            force = True
        elif a.upper() in ("NQ", "GC"):
            insts.append(a.upper())
        i += 1
    return window, once, force, insts or ["NQ", "GC"]


def main(argv):
    window, once, force, insts = parse_args(argv)
    if window and window not in WINDOWS:
        print(f"Okänt fönster {window!r} — använd {'/'.join(WINDOWS)}.")
        return 2

    sent_today, tried_today = status(window)
    if once and sent_today and not force:
        print(f"Fönstret {window} är redan skickat {today_str()} — hoppar över.")
        return 0

    prev = _previous()
    results = [CLI.run(i) for i in dict.fromkeys(insts)]
    write_files(results)
    ok = all(not r.get("error") for r in results)
    msg = build_message(results, fallback=prev)
    print(msg.replace("<b>", "").replace("</b>", "").replace("<pre>", "\n").replace("</pre>", ""))
    if ok or force or not tried_today:
        # lyckade nivåer skickas alltid, felmeddelande bara en gång per fönster och dag
        sent = send_telegram(msg)
    else:
        sent = False
        print("Fel igen i samma fönster — tyst retry, inget nytt Telegram-meddelande.")
    mark(window, ok and sent)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
