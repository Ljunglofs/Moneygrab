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
