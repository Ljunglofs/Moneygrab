# Koppla TradingView till GRABIT

Dina TradingView-larm (alerts) skickas via webhook rakt in i GRABIT. Varje larm:

- sparas och visas i appen under **Signaler → TradingView**
- går ut i **Telegram + Discord** (samma kedja som NASDAQ ROBBER)
- skickas som **push** till de som bevakar aktien i appen (valbart)

Ingenting i `api.py` rörs. Bryggan ligger i `tradingview_bridge.py` och monteras av
`grabit_entry.py` — samma mönster som roboten.

## 1. Env-vars i Render (grabit-api → Environment)

| Variabel | Krävs | Beskrivning |
|---|---|---|
| `TV_WEBHOOK_SECRET` | ja | Valfri lång slumpad sträng. TradingView kan inte sätta headers, så hemligheten skickas i URL:en (`?key=`) eller som `"secret"` i JSON. |
| `TV_PUSH_DEFAULT` | nej | `watchlist` (default: bara de som bevakar aktien), `all` (alla prenumeranter) eller `none`. |
| `TV_IP_CHECK` | nej | `1` = släpp bara in TradingViews publicerade IP-adresser. Lämna av om du testar med curl. |
| `DATA_DIR` | rek. | Persistent disk (t.ex. `/var/data`) så signalerna överlever omdeploy. Samma som roboten. |

Telegram/Discord/push använder de nycklar som redan finns (`TELEGRAM_TOKEN`, `CHAT_ID`,
`DISCORD_WEBHOOK_URL`, VAPID).

Deploya om efter att du lagt till variablerna.

## 2. Skapa larmet i TradingView

Webhooks kräver TradingView **Pro, Pro+ eller Premium**.

1. Öppna grafen → klocksymbolen → **Create Alert**.
2. Välj villkor (pris korsar nivå, indikator, strategi …).
3. Fliken **Notifications** → bocka i **Webhook URL** och klistra in:

   ```
   https://grabit-api-80dh.onrender.com/tv/webhook?key=DIN_HEMLIGHET
   ```

4. Fliken **Settings** → rutan **Message**. Klistra in JSON (TradingView fyller i
   `{{...}}`-fälten själv):

   ```json
   {"ticker":"{{ticker}}","exchange":"{{exchange}}","action":"buy","price":{{close}},"interval":"{{interval}}","time":"{{time}}","note":"Breakout över motstånd"}
   ```

   Fält:

   | Fält | Värden | Kommentar |
   |---|---|---|
   | `action` | `buy` / `sell` / `close` / `alert` | Även `long`, `short`, `köp`, `sälj`, `exit`. Saknas det gissar vi ur texten. |
   | `ticker`, `exchange` | `{{ticker}}`, `{{exchange}}` | `OMXSTO:VOLV_B` blir `VOLV-B.ST`, `CAPITALCOM:US100` blir `US100`, `OANDA:XAUUSD` blir `XAU`. |
   | `price` | `{{close}}` | Valfritt. |
   | `stop`, `target` | tal | Valfritt. Visas som SL/TP. |
   | `interval` | `{{interval}}` | Valfritt. |
   | `strategy` | text | Valfritt namn, t.ex. `EMA-cross`. Strategilarm: `{{strategy.order.action}}` funkar som `action`. |
   | `note` | text | Fri text som visas i kortet och larmet. |
   | `push` | `all` / `watchlist` / `none` | Överstyr `TV_PUSH_DEFAULT` för just detta larm. |

   **Ren text funkar också.** Skriver du bara `NVDA buy – breakout` i rutan så
   plockas ticker och riktning ur texten och resten blir `note`.

5. Spara larmet. Klart.

## 3. Verifiera

- `GET /tv/status` — visar om hemligheten är satt, hur många signaler som sparats,
  senaste signalen samt räknare för mottagna/avvisade/dubbletter.
- `GET /tv/test?key=<ROBBER_ADMIN_KEY>` — kör en fejkad signal (NVDA LONG) genom hela
  kedjan: sparas, Telegram, Discord, push. Lägg till `&send=0` för att bara spara.
- `GET /tv/signals?limit=30` — det appen läser. `&tkr=NVDA` filtrerar på aktie.
- Manuell webhook från terminalen:

  ```bash
  curl -X POST "https://grabit-api-80dh.onrender.com/tv/webhook?key=DIN_HEMLIGHET" \
       -H "Content-Type: application/json" \
       -d '{"ticker":"NVDA","exchange":"NASDAQ","action":"buy","price":512.3,"interval":"60","note":"test"}'
  ```

Identisk signal inom 60 sekunder räknas som dubblett och skickas inte igen
(TradingView kan avfyra samma larm flera gånger på en stapel).

## Felsökning

| Symptom | Orsak |
|---|---|
| `503 TV_WEBHOOK_SECRET saknas` | Variabeln är inte satt i Render, eller ingen omdeploy efter att den lades till. |
| `403 fel eller saknad hemlighet` | `?key=` i URL:en matchar inte, eller `"secret"` i JSON saknas/felstavat. |
| `403 avsändare … är inte TradingView` | `TV_IP_CHECK=1` och anropet kom inte från TradingView (t.ex. curl). |
| Larmet syns i appen men inte i Telegram | `TELEGRAM_TOKEN`/`CHAT_ID` saknas — kolla `/api/robber/test`. |
| Ingen push | Ingen bevakar aktien (`watchlist`-läge) eller VAPID-nycklar saknas. Testa `"push":"all"`. |
| Signalerna försvinner vid omdeploy | `DATA_DIR` pekar inte på Persistent Disk. |
