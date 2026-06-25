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
@app.get("/robber/testsignal")
def _robber_testsignal():
    from datetime import datetime, timezone
    import math
    try:
        from nasdaq_robber import send_telegram, format_alert, Config
    except Exception as e:
        return {"ok": False, "error": f"kan inte importera roboten: {e}"}

    price, atr, risk = 581.20, 1.85, 2.40
    stop    = round(price - risk, 2)
    targets = [round(price + risk * r, 2) for r in Config.TARGETS_R]
    shares  = math.floor((Config.ACCOUNT_SIZE * Config.RISK_PCT) / risk)

    sig = {
        "side": "LONG", "ticker": "QQQ",
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
    try:
        send_telegram(msg)
    except Exception as e:
        return {"ok": False, "error": f"telegram-fel: {e}"}
    return {"ok": True, "sent": True,
            "note": "Kolla Telegram — testlarmet ska ha kommit."}
