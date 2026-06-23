# Baka in NASDAQ ROBBER i grabit — checklista

Roboten körs som bakgrundstråd inuti grabit-api. Exakt timing, ingen delay,
ingen extra kostnad. Din api_v3.py rörs INTE.

## 1. Lägg till två filer i Moneygrab-repot
- `nasdaq_robber.py`   (roboten)
- `grabit_entry.py`    (wrapper som startar roboten + serverar din app)

## 2. Ersätt requirements_api.txt
Lägg till `ta` och `requests` (annars kraschar bygget på saknade paket).
Den färdiga filen finns med — ladda upp den och skriv över den gamla.

## 3. Ändra EN rad i render.yaml
    FÖRE:  startCommand: uvicorn api_v3:app --host 0.0.0.0 --port $PORT
    EFTER: startCommand: uvicorn grabit_entry:app --host 0.0.0.0 --port $PORT

(Om din live-modul heter `api` och inte `api_v3` fixar wrappern det
automatiskt — den provar api_v3 först, faller tillbaka på api.)

## 4. Lägg fyra env-vars på grabit-api-tjänsten i Render
Render → grabit-api → Environment → Add:
- ALPACA_KEY
- ALPACA_SECRET
- TELEGRAM_TOKEN
- CHAT_ID = 5456713725

## 5. Deploya om grabit
Commit:a ändringarna → Render auto-deployar (autoDeploy: true).

## 6. Verifiera i loggen
Leta efter:
    Robber: bakgrundstråd startad.
    === NASDAQ ROBBER startad ===
    Tickers: ['QQQ'] | 15Min setup / 1Hour bias | feed=iex
Och, under US-marknadstid, rader med pris/RSI/bias eller skickade larm.

## VIKTIGT
- grabit-api MÅSTE vara på betald/alltid-vaken plan. Gratis Render-webbtjänster
  somnar efter 15 min → då dör tråden och roboten slutar skanna.
  (render.yaml i repot säger `plan: free` — kolla att dashboarden säger annat.)
- Roboten larmar, lägger inga ordrar.
- Telegram måste ha fått /start från dig en gång annars skickar den inte.
