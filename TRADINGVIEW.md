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

## GRABIT Delta · CVD (`pine/grabit_delta_cvd.pine`)

Volymdelta och kumulativ delta i egen panel, som filter till GEX-nivåerna:
väggen säger var priset möter motstånd, deltat säger om någon orkar dit.

Varje bar delas i intrabarer (1-minuters på en 5-minutersgraf, ställbart).
Intrabaren räknas som köp om den stängde upp och sälj om den stängde ner, och
volymen får det tecknet. Summan är barens delta, summan över dagen är CVD.
Samma metod som TradingViews egen volymdelta — en approximation av bid/ask,
inte licensierad orderflow, men nära på NQ och GC.

- CVD ritas som ljus: kroppen är barens delta, nivån är dagens ackumulerade.
- Signallinjen (EMA 21) plus riktning ger raden ÖVERTAG: KÖPARE, SÄLJARE eller JÄMNT.
- Divergenser markeras: ny topp i priset utan ny topp i CVD = "Säljare absorberar",
  ny botten utan ny botten i CVD = "Köpare absorberar". Båda kan larma.
- Statusrutan visar också vilken intrabar-upplösning som faktiskt användes. Står
  det "saknas" hittade TradingView ingen intrabar-data så långt bakåt, och baren
  klassas grovt på sin egen stängning — då är siffran ungefärlig.
- CVD nollställs per dag som förval; Session (RTH), Vecka eller Aldrig går att välja.
- Auto använder minutupplösning. Sekundtidsramar (1S/5S/15S) ger finare delta men
  kräver TradingView Premium — utan Premium ger de körfelet RE10063.

## GRABIT Breakout · strategy (`pine/grabit_breakout_strategy.pine`)

Backtestbar version av den signaltyp som säljs som "Weakness Below X /
Strength Above X": bryt en nivå, stop på andra sidan, tre mål med avskalning
(en kontrakt per mål, tre totalt).

Finns för att kunna mäta i stället för att tro. Vinstprocent på T1 säger
nästan ingenting när T1 ligger närmare än stoppen — med stop 36 punkter och
T1 16 punkter krävs 69 % träff bara för nollresultat, före courtage. Tabellen
visar hur ofta T1, T2 och T3 nås, hur ofta stoppen tas utan att T1 nåtts, och
vilken träffprocent som krävs för att gå jämnt ut. Resten läser du i Strategy
Tester: nettoresultat, profit factor, max drawdown.

- Nivå: bekräftad pivot eller högsta/lägsta de senaste N barerna.
- Stop: ATR-multipel, fast punktantal eller motsatt struktur.
- Mål i multiplar av risken (förval 0,5R / 1,0R / 1,5R), break even efter T1.
- Filter, alla avstängda i förval utom tid: session, EMA-trend, GEX (long bara
  över Gamma Flip, short bara under, och signaler för nära motsatt vägg
  hoppas över) och volymdelta i signalens riktning.

Innan ett resultat betyder något: sätt Commission och Slippage i Properties
(NQ ligger kring 4-5 USD per round turn och minst en tick), kör Deep Backtest
över några hundra affärer, och kontrollera i Bar Replay att signalen står kvar
när baren stängt.
