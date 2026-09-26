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

## Grabit VP (`pine/grabit_vp.pine`)

VWAP, volymprofil, Initial Balance, sessioner, POC-nivåer och order blocks i
samma overlay.

Högerprofilen har tre typer:

- **Delta + Volym** (förval), samma upplägg som orderflow-plattformarnas
  profiler, ritad med en tunn linje per prisrad. Till **höger** om mittlinjen
  står den totala volymen på nivån, i en färg. Till
  **vänster** står deltat, köp − sälj på nivån. Linjen är cyan när köparna
  ledde och röd när säljarna gjorde det, och längden visar hur stor övervikten
  var. Deltasidan skalas för sig, eftersom deltat alltid är mycket mindre än
  volymen.
- **Köp/Sälj (delad)**: säljvolym åt vänster, köpvolym åt höger.
- **Klassisk**: den gamla enfärgade profilen.

Profilen täcker som förval **pågående session** ("Data i högerprofil"). Det
är dagens nivåer, och tillsammans med gårdagens (dVAH/dVAL, Yesterday POC) är
det dem man daytradar mot. "Senaste sessioner" (5 som förval) ger en
flerdagarsprofil för större nivåer och målzoner, och "Lookback" ett fast antal
bars. Högst 1999 bars räknas, vilket på 5-minutersgraf räcker
till drygt en vecka men på 1-minutersgraf bara till lite mer än en dag.

Överst står deltat för hela profilen, (köp − sälj) / total i procent och i
kontrakt. Underst står total volym uppdelad på köp och sälj. "Siffror per
nivå" slår ihop raderna till N nivåer och skriver ut värdena vid varje nivå,
som en enkel footprint.

Köp och sälj skattas ur 1-minutersbarer: en intrabar som stänger högt i sitt
spann räknas mest som köp, en som stänger lågt mest som sälj. Det är en
approximation, inte riktig bid/ask-data. Siffrorna blir därför något jämnare än
i en riktig footprint, och kan skilja lite från CVD-indikatorn ovan, som räknar
hela intrabaren åt ett håll.

## Grabit CVD (`pine/grabit_cvd.pine`)

CVD i egen panel med fyra rader i tabellen: SIDA (vem som leder dagen), DIV
(divergens eller absorption nyligen), BRÄNSLE (om deltat växer åt sidans håll)
och TRYCK.

- **Tryck 0–100** är en RSI räknad på deltat: andelen köpdelta av allt delta,
  utjämnat över 14 barer. Över 70 betyder att köparna har dominerat ovanligt
  mycket, under 30 att säljarna har gjort det. Välj "Panel: Tryck (0–100)" för
  att se linjen. Vill du se både CVD och Tryck, lägg till indikatorn två gånger.
  Överköpt betyder starkt flöde, inte automatiskt vändning. Signalen är när
  trycket lämnar zonen igen, särskilt vid en nivå, och det finns larm för det.
- **Divergens**: priset gör en ny extrem men CVD gör det inte, och priset stänger
  sedan tillbaka.
- **Absorption**: deltat är minst 30 % av barens volym, men baren rör sig lite
  och stänger åt andra hållet. Någon står emot trycket.
- **Nivåfilter** (på som förval): signaler ges bara inom 0,5 × ATR från VWAP
  ±1 SD, PDH/PDL eller en GEX-nivå. GEX-strängen klistras in som i GRABIT GEX
  Levels och används bara för det senaste dygnet, eftersom det är dagens nivåer.
  Etiketten i prisgrafen säger vilken nivå signalen kom vid.
- CVD nollställs som förval vid **RTH-öppningen** (15:30 svensk tid för NQ,
  COMEX 08:20 New York-tid för guld), så att nattens handel inte ligger med.

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

## Invite-only och automatisk åtkomst för PRO

Indikatorerna som ingår i PRO är GRABIT Flow Profile (`grabit_vp.pine`),
GRABIT CVD (`grabit_cvd.pine`) och GRABIT GEX Levels (`grabit_gex_levels.pine`).
All text som användaren ser är på engelska. Kommentarerna i koden är kvar på
svenska, eftersom ingen annan ser källkoden i ett invite-only-skript.

### 1. Publicera som invite-only (en gång per skript)

Det kräver TradingView Premium eller högre.

1. Öppna skriptet i Pine Editor, klicka **Publish script**.
2. Visibility: **Invite-only**. Skriv beskrivningen och publicera.
3. Uppdateringar görs sedan med **Publish script → Update existing script**.
   Då behåller alla medlemmar sin åtkomst.

### 2. Koppla appen (Render → grabit-api → Environment)

| Variabel | Värde |
|---|---|
| `TV_PINE_IDS` | Skriptens id:n, kommaseparerade, t.ex. `PUB;abc123,PUB;def456,PUB;ghi789`. Id:t står i Pine Editor under skriptets namn (… → *Copy script ID*) eller i nätverksfliken på skriptsidan. |
| `TV_SESSIONID` | Cookien `sessionid` från din inloggning på tradingview.com (DevTools → Application → Cookies). |
| `TV_SESSIONID_SIGN` | Cookien `sessionid_sign`, om den finns. |

Klistra aldrig in cookien i chatt eller i repot: den är din inloggning.
Loggar du ut från TradingView slutar cookien att gälla, och då behöver den
bytas.

### Så fungerar det

- En PRO-medlem skriver sitt TradingView-namn på medlemskortet i appen.
- Appen kollar att namnet finns och lägger till det i alla skript i
  `TV_PINE_IDS`, på samma sätt som *Manage access* gör. Du får en bekräftelse
  på Telegram.
- När prenumerationen upphör (uppsagd, obetald eller pausad efter
  provperioden) tas namnet bort automatiskt från alla skript.
- Saknas variablerna, eller svarar TradingView med fel, går begäran till dig
  på Telegram och du lägger till namnet för hand.
- `GET /api/pro/tradingview/status` visar om automatiken är på och hur många
  som har fått åtkomst.

Anropen går mot TradingViews interna gränssnitt, inte ett officiellt API.
Om TradingView ändrar det faller flödet tillbaka till Telegram.
