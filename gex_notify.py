"""
GEX NOTIFY  ·  gex_notify.py
-----------------------------
Räknar dagens GEX-nivåer (gex_cli.run) och skickar strängarna till Telegram
som kodblock (tryck = kopiera i telefonen). Skriver även levels/NQ.txt,
levels/GC.txt och levels/latest.json så de går att öppna via GitHub.

Körs av .github/workflows/gex_daily.yml varje vardag, eller lokalt:
    TELEGRAM_TOKEN=... CHAT_ID=... python gex_notify.py

Utan TELEGRAM_TOKEN/CHAT_ID skrivs bara filerna.
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import gex_cli as CLI

OUT_DIR = os.environ.get("GEX_OUT_DIR", "levels")
STO = ZoneInfo("Europe/Stockholm")


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(results):
    now = datetime.now(STO).strftime("%a %d %b %H:%M")
    parts = [f"\U0001F4CB <b>GEX Daily Levels</b> · {now}"]
    for r in results:
        if r.get("error"):
            parts.append(f"\n<b>{r['inst']}</b>: {_esc(r['error'])}")
            continue
        flag = " ⚠ flip osäker" if r.get("flip_uncertain") else ""
        parts.append(
            f"\n<b>{r['inst']}</b> {r['fut_price']:.2f} · gamma {r['regime']}{flag}\n"
            f"Call Wall <b>{r['call_wall']}</b> · Put Wall <b>{r['put_wall']}</b> · Flip {r['zero_gamma']}\n"
            f"EM ±{r['expected_move'] and round(r['expected_move'])} · Max Pain {r['max_pain']} · HGEX {r['hgex']}\n"
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


def write_files(results):
    os.makedirs(OUT_DIR, exist_ok=True)
    for r in results:
        if not r.get("error"):
            with open(os.path.join(OUT_DIR, f"{r['inst']}.txt"), "w", encoding="utf-8") as f:
                f.write(r["string"] + "\n")
    with open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.now(STO).isoformat(timespec="seconds"), "levels": results},
                  f, indent=2, ensure_ascii=False)


def main(argv):
    insts = [a.upper() for a in argv if a.upper() in ("NQ", "GC")] or ["NQ", "GC"]
    results = [CLI.run(i) for i in insts]
    write_files(results)
    msg = build_message(results)
    print(msg.replace("<b>", "").replace("</b>", "").replace("<pre>", "\n").replace("</pre>", ""))
    send_telegram(msg)
    return 0 if all(not r.get("error") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
