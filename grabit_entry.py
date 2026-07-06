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

# 3) TESTENDPOINT — avfyra ett skarpt formaterat (men fejkat) Telegram-larm
#    på begäran. Ligger på tvåsegments-väg så api.py:s catch-all /{fname}
#    inte slukar den. Öppna i mobilen:
#        https://grabit-api-80dh.onrender.com/robber/testsignal
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
        "hogsta_conf_idag", "data_fel_total", "senaste_data_fel", "tickers")}
    out["regler"] = {"tickers": Config.TICKERS,
                     "min_score_av_7": Config.MIN_SCORE,
                     "conf_min_for_larm": Config.CONF_MIN_SEND,
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
def _robber_testsignal():
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
