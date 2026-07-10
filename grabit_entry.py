"""
GRABIT ENTRY  ·  grabit_entry.py
---------------------------------
Wrapper-entrypoint. Startar NASDAQ ROBBER i en bakgrundstråd och
serverar sedan din riktiga grabit-app — UTAN att röra api_v3.py.

Deploy: ändra render.yaml startCommand till:
    uvicorn grabit_entry:app --host 0.0.0.0 --port $PORT

Roboten kör som daemon-tråd. Kraschar den påverkas inte grabit.
Saknas Alpaca-keys startar den helt enkelt inte — grabit bootar normalt.
"""

# 1) Hämta din riktiga FastAPI-app (rör inte din kod).
try:
    from api_v3 import app          # det render.yaml pekar på
except ModuleNotFoundError:
    from api import app             # fallback om live-modulen heter api

# 2) Starta roboten i bakgrunden.
try:
    from nasdaq_robber import start_in_background
    start_in_background()
except Exception as e:
    # Roboten får ALDRIG hindra grabit från att starta.
    print(f"Robber kunde inte starta (grabit kör vidare): {e}")

# 2b) Äkta serversidig PRO-låsning för webben (Stripe).
try:
    import billing_web
    billing_web.register(app)
except Exception as e:
    # Får aldrig hindra grabit från att boota.
    print(f"billing_web kunde inte registreras (grabit kör vidare): {e}")

# 2c) Konto-light: e-post + engångskod (PRO-återställning + portfölj-synk).
try:
    import accounts
    accounts.register(app)
except Exception as e:
    print(f"accounts kunde inte registreras (grabit kör vidare): {e}")

# 3) TESTENDPOINT — avfyra ett skarpt formaterat (men fejkat) Telegram-larm
#    på begäran. Ligger på tvåsegments-väg så api.py:s catch-all /{fname}
#    inte slukar den. Öppna i mobilen:
#        https://grabit-api-80dh.onrender.com/robber/testsignal
import os as _os
from fastapi import HTTPException as _HTTPExc

def _robber_key_ok(key: str) -> bool:
    k = _os.environ.get("ROBBER_ADMIN_KEY", "")
    return bool(k) and key == k


@app.get("/robber/send")
def _robber_send(key: str = "", side: str = "LONG", ticker: str = "US100",
                 entry: str = "", sl: str = "", tp1: str = "", tp2: str = "",
                 conf: int = 75, note: str = ""):
    """Manuellt larm till ALLA prenumeranter + Telegram. Kräver ?key=<ROBBER_ADMIN_KEY>.
    Exempel: /robber/send?key=XXX&side=LONG&entry=29700&sl=29650&tp1=29800&conf=80"""
    if not _robber_key_ok(key):
        raise _HTTPExc(403, "fel eller saknad nyckel (sätt ROBBER_ADMIN_KEY i Render)")
    side = (side or "").upper()
    if side not in ("LONG", "SHORT"):
        raise _HTTPExc(400, "side måste vara LONG eller SHORT")
    if not (entry and sl):
        raise _HTTPExc(400, "entry och sl krävs")
    tg_err = push_err = None
    rikt_pil = "\U0001F4C8" if side == "LONG" else "\U0001F4C9"
    tps = " | TP: " + tp1 + ((" / " + tp2) if tp2 else "") if tp1 else ""
    try:
        from nasdaq_robber import notify
        notify(f"\U0001F6A8 <b>{side} \u2013 {ticker}</b> (manuell)\n"
               f"\U0001F525 Confidence: <b>{conf}/100</b>\n"
               f"Entry: <b>{entry}</b>\nSL: {sl}{tps}"
               + (f"\n{note}" if note else ""))
    except Exception as e:
        tg_err = str(e)
    try:
        import push_notify as PN
        r = PN.send_all(f"\u26A1 NASDAQ ROBBER\u2122 \u00b7 {conf}/100",
                        (f"NEW ENTRY SIGNAL {rikt_pil}\n{side} \u00b7 {ticker}\n"
                         f"Entry: {entry} | SL: {sl}{tps}"),
                        url="/", tag="robber-manual")
    except Exception as e:
        push_err = str(e); r = None
    return {"ok": not (tg_err and push_err), "telegram_fel": tg_err,
            "push_fel": push_err, "push_resultat": r}


@app.get("/robber/mode")
def _robber_mode(key: str = "", set: str = ""):
    """Ställ robotens läge efter marknaden — utan omdeploy. Kräver ?key=<ROBBER_ADMIN_KEY>.
      /robber/mode?key=XXX                 -> visar nuvarande läge
      /robber/mode?key=XXX&set=aggressiv   -> starkt trend/bull, fler larm
      /robber/mode?key=XXX&set=normal      -> balanserat (default)
      /robber/mode?key=XXX&set=defensiv    -> choppigt, bara A+-lägen"""
    if not _robber_key_ok(key):
        raise _HTTPExc(403, "fel eller saknad nyckel (sätt ROBBER_ADMIN_KEY i Render)")
    try:
        import nasdaq_robber as R
    except Exception as e:
        raise _HTTPExc(500, f"kan inte importera roboten: {e}")
    if set:
        R.set_mode(set)
    m = R.RUNTIME.get("mode", "normal")
    p = R._MODE_PRESETS.get(m, {})
    return {"ok": True, "lage": m,
            "trosklar": {"min_score_av_7": p.get("min_score"),
                         "confidence_min_for_larm": p.get("conf_min_send"),
                         "min_rel_volym": p.get("min_rel_volume")},
            "val": {"aggressiv": "starkt trend/bull — fler fortsättningslarm",
                    "normal": "balanserat (default)",
                    "defensiv": "choppigt/osäkert — bara A+-lägen"}}


@app.get("/robber/scan")
def _robber_scan(key: str = "", send: int = 0):
    """On-demand-koll NU: 'ser du en setup just nu?'. Kräver ?key=<ROBBER_ADMIN_KEY>.
      /robber/scan?key=XXX          -> kollar och rapporterar (skickar inget)
      /robber/scan?key=XXX&send=1   -> skickar larm om ett läge är över tröskeln"""
    if not _robber_key_ok(key):
        raise _HTTPExc(403, "fel eller saknad nyckel (sätt ROBBER_ADMIN_KEY i Render)")
    try:
        import nasdaq_robber as R
    except Exception as e:
        raise _HTTPExc(500, f"kan inte importera roboten: {e}")
    try:
        res = R.scan_now(send=bool(send))
    except Exception as e:
        raise _HTTPExc(500, f"scan-fel: {type(e).__name__}: {e}")
    return {"ok": True, **res}


_QUICK_LAST = {"t": 0.0}

@app.get("/robber/quickscan")
def _robber_quickscan(code: str = ""):
    """Publik direktkoll bakom en enkel kod (env ROBBER_QUICK_CODE, default 'daq').
    Kör en koll NU och skickar signal (Telegram + push) om ett läge är över tröskeln.
    45s cooldown mot spam. Används av bannern i appen."""
    import time as _t
    want = _os.environ.get("ROBBER_QUICK_CODE", "daq")
    if (code or "").strip().lower() != want.strip().lower():
        raise _HTTPExc(403, "fel kod")
    now = _t.time()
    gone = now - _QUICK_LAST["t"]
    if gone < 45:
        return {"ok": True, "cooldown": int(45 - gone)}
    _QUICK_LAST["t"] = now
    try:
        import nasdaq_robber as R
        res = R.scan_now(send=True)
    except Exception as e:
        _QUICK_LAST["t"] = 0.0
        raise _HTTPExc(500, f"scan-fel: {type(e).__name__}: {e}")
    tickers = res.get("tickers", [])
    found = any(t.get("over_troskel") and t.get("fardata") for t in tickers)
    sent = sum(1 for t in tickers if t.get("skickat"))
    return {"ok": True, "found": found, "sent": sent, "lage": res.get("lage"),
            "handelsfonster": res.get("handelsfonster"), "tickers": tickers}


@app.get("/robber/status")
def _robber_status():
    """Robotens hälsokontroll: kör den, vad har den sett, varför larmar den inte?"""
    from datetime import datetime, timezone, timedelta
    try:
        from nasdaq_robber import STATUS, Config
    except Exception as e:
        return {"ok": False, "error": f"kan inte importera roboten: {e}"}
    out = {"ok": True}
    out["status"] = {k: STATUS.get(k) for k in (
        "started", "last_scan", "scans_total", "fired_total", "fired_last",
        "hogsta_conf_idag", "data_fel_total", "senaste_data_fel", "tickers",
        "orb_last", "blocked_last")}
    try:
        import nasdaq_robber as _R
        _lage = _R.RUNTIME.get("mode", "normal")
        _minscore = _R.cur_min_score(); _confmin = _R.cur_conf_min_send()
    except Exception:
        _lage = "normal"; _minscore = Config.MIN_SCORE; _confmin = Config.CONF_MIN_SEND
    out["regler"] = {"tickers": Config.TICKERS,
                     "lage": _lage,
                     "min_score_av_7": _minscore,
                     "conf_min_for_larm": _confmin,
                     "larmfonster": "06:00-22:00 mån-fre (svensk tid)"}
    # Skuggloggen: ALLA kandidatsetups senaste 7 dygnen, även de som inte larmats
    import json as _j
    rows = []
    try:
        with open(Config.SHADOW_LOG) as f:
            for line in f:
                try:
                    rows.append(_j.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    grans = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    last7 = [r for r in rows if (r.get("ts") or "") >= grans]
    from collections import Counter
    out["skugglogg_7d"] = {
        "kandidater_totalt": len(last7),
        "larm_skickade": sum(1 for r in last7 if r.get("sent")),
        "hogsta_confidence": max((r.get("confidence") or 0) for r in last7) if last7 else None,
        "per_dag": dict(sorted(Counter((r.get("ts") or "")[:10] for r in last7).items())),
        "obs": ("Skuggloggen låg på flyktig disk fram till idag — historik före "
                "diskbytet är borta. Från och med nu sparas allt beständigt."),
    }
    return out


@app.get("/robber/testsignal")
def _robber_testsignal(key: str = ""):
    if not _robber_key_ok(key):
        raise _HTTPExc(403, "fel eller saknad nyckel — lägg till ?key=<ROBBER_ADMIN_KEY>")
    from datetime import datetime, timezone
    import math
    try:
        from nasdaq_robber import send_telegram, format_alert, Config
    except Exception as e:
        return {"ok": False, "error": f"kan inte importera roboten: {e}"}

    price, atr, risk = 18956.0, 32.0, 46.0
    stop    = round(price - risk, 2)
    targets = [round(price + risk * r, 2) for r in Config.TARGETS_R]
    shares  = math.floor((Config.ACCOUNT_SIZE * Config.RISK_PCT) / risk)

    sig = {
        "side": "LONG", "ticker": "NQ=F",
        "score": 6, "max_score": 7,
        "price": price, "atr": atr, "stop": stop,
        "risk_per_share": round(risk, 2),
        "targets": targets, "shares": shares,
        "bias": "BULL · 1H EMA50 > EMA200",
        "reasons": [
            "15m stänger över EMA50",
            "MACD vänder upp ur svalka",
            "RSI > 50 och stigande",
            "Pris reagerar på bull Order Block",
            "Bryter senaste 15m-swinghög",
            "HTF-bias bekräftar long",
        ],
        "bar_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    msg = ("🧪 <b>TESTSIGNAL</b> — ej skarp, bara ett rörtest.\n"
           "Så här ser ett riktigt larm ut:\n\n" + format_alert(sig))
    tg_err = push_err = None
    try:
        send_telegram(msg)
    except Exception as e:
        tg_err = str(e)
    try:
        import push_notify as PN
        PN.send_all("\u26A1 NASDAQ ROBBER\u2122 \u00b7 TESTSIGNAL",
                    (f"NEW ENTRY SIGNAL \U0001F4C8\n"
                     f"LONG \u00b7 US100\n"
                     f"Entry: {price} | SL: {stop} | TP: {targets[0]}"),
                    url="/", tag="robber-test")
    except Exception as e:
        push_err = str(e)
    return {"ok": not (tg_err and push_err), "telegram_fel": tg_err, "push_fel": push_err,
            "note": "Kolla Telegram OCH telefonens notiser — testlarmet ska ha kommit i båda."}
