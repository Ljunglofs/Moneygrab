# GRABIT DESK — NQ & guld intraday på propkonto

Realtidsdata från TradingView in, färdiga tradeplaner ut i Telegram. Du lägger ordrarna.

```
TradingView (1m, CME-realtid)  ──Pine "GRABIT Feed"──►  POST /desk/bar
                                                          │
                                   nivåer · VWAP-band · volymprofil · CVD · RVOL
                                   gamma-nivåer (QQQ→NQ, GLD→GC, gex.py)
                                                          │
                                              setup-motor + prop-spärrar
                                                          │
                                   Telegram + Discord + push:  entry / stop / TP / storlek
```

Koden ligger i `desk.py` (motor), `gex.py` (gamma), `prop_rules.py` (regler) och
`pine/grabit_feed.pine` (TradingView). Monteras av `grabit_entry.py`. `api.py` rörs inte.

## 1. Render: env-vars

| Variabel | Värde | Kommentar |
|---|---|---|
| `TV_WEBHOOK_SECRET` | lång slumpad sträng | Samma som TradingView-bryggan. Skickas i URL:en. |
| `DESK_ACCOUNTS` | `lucid_pro_25k,tradeify_growth_25k` | Aktiva konton. Desken tar den strängaste gränsen. |
| `DESK_DAILY_BUDGET_USD` | `400` | Egen daglig förlustbudget. Under Tradeifys 600. |
| `DESK_RISK_PER_TRADE` | `150` | Max risk per trade i USD. |
| `DESK_MAX_TRADES_DAY` | `4` | Stopp för overtrading. |
| `DESK_MAX_MICROS` | `10` | Tak på micros per position. Tradeifys kontraktsgräns är inte verifierad, sätt den du ser i dashboarden. |
| `DESK_MIN_CONF` | `68` | Lägsta confidence för larm. |
| `DESK_COOLDOWN_MIN` | `30` | Samma setup larmas inte igen inom X min. |
| `DATA_DIR` | `/var/data` | Persistent disk. Barer, state och gamma-cache. |

Telegram, Discord och push använder nycklarna som redan finns.

## 2. TradingView: två larm

Kräver betald plan och **CME-realtidsdata** (paketet CME/CBOT/COMEX/NYMEX). Utan det är
allt 10 minuter fördröjt och desken handlar på gammal information.

1. Öppna **CME_MINI:NQ1!** på 1-minutsdiagram.
2. Pine Editor → klistra in `pine/grabit_feed.pine` → Add to chart.
3. Create Alert. Condition: **GRABIT Feed → Any alert() function call**. Expiration: open-ended.
   Webhook URL:
   ```
   https://grabit-api-80dh.onrender.com/desk/bar?key=DIN_TV_WEBHOOK_SECRET
   ```
   Message: lämna tom, scriptet bygger JSON själv.
4. Gör om samma sak på **COMEX:GC1!**.

Kontrollera i `/desk/status` att `bars_received` tickar upp en gång per minut och instrument.

Scriptet skickar open, high, low, close, volym, TradingViews CVD (ankrad till handelsdagen)
och sessions-VWAP. Saknas CVD räknar desken en proxy ur barformen.

## 3. Vad desken räknar

| Komponent | Källa | Används till |
|---|---|---|
| PDH, PDL, PDC | föregående RTH-dag | nivåer, mål, sweep-fade |
| ONH, ONL, Asia, London | sessionen från 18:00 ET | nivåer, mål |
| IB (första 60 min), OR (första 15 min) | RTH | ORB-setup, nivåer |
| VWAP ±1σ, ±2σ | sessionen (TradingViews värde om det finns) | återtag/avvisning, band-fade |
| Volymprofil POC, VAH, VAL | sessionen och föregående dag, bin 2,5 p NQ / 1 p GC | värdeområde-setups, mål |
| CVD + lutning + divergens | TradingView eller proxy | bekräftelse, sweep-fade |
| RVOL | RTH-volym mot samma klockslag 3 dagar bakåt | filter |
| ATR 1m/5m, EMA20/50 5m | barerna | stopp, bias |
| Gamma: call wall, put wall, zero gamma, regim | QQQ/GLD-optioner via Yahoo, skalat med live-kvot | fade-nivåer, regim |

Killzones: NY open 09:30–11:30 ET +10, London 02:00–05:00 +5, lunch −10, sista timmen −20, natt −10.

## 4. Setups som motorn letar efter

| Setup | Trigger | Bra när |
|---|---|---|
| VWAP-återtag / avvisning | stängning genom VWAP med CVD i riktningen | trend, NY open |
| VWAP-band fade | avvisning vid ±2σ | positiv gamma |
| Värdeområde-avvisning | wick vid VAH/VAL, stängning innanför | positiv gamma, mål POC |
| Acceptans över VAH / under VAL | stängning utanför med volym | negativ gamma, trend |
| Gamma fade | avvisning vid call wall / put wall | positiv gamma |
| ORB | stängning utanför OR 09:45–10:30 med RVOL > 1,2 | trenddagar |
| Sweep-fade | sweep av PDH/PDL/ONH/ONL med CVD-divergens | range-dagar |

Confidence 0–100. Under 60 skickas inget. 60–74 gul, 75+ grön A+.
Stop ligger bakom strukturen plus en halv ATR. TP1 är närmaste logiska nivå på minst 1,5R,
annars 2R. TP2 nästa nivå eller 3R. Storlek räknas i micros så att stoppet = riskbudgeten.

## 5. Prop-spärrar

Inbyggt från firmornas publika regler (sept 2026). Verifiera mot din dashboard.

| | Lucid Pro 25k | Tradeify Growth 25k |
|---|---|---|
| Vinstmål | 1 250 USD | 1 500 USD |
| Max loss | 1 000 USD EOD | 1 000 USD EOD trailing |
| Daglig gräns | ingen på 25k | 600 USD, soft (dagen stängs) |
| Consistency | 40 % när funded | 35 % vid payout |
| Kontrakt | 2 minis / 20 micros | ej verifierat |
| Flat senast | 16:45 ET | 16:59 ET |
| Nyheter | tillåtet | tillåtet |

Desken stoppar nya entries när: dagens rapporterade P&L når budgeten, max antal trades
är nådda, det är mindre än 20 minuter till flat-tiden, eller desken är pausad.
Risken på nästa trade halveras när halva budgeten är borta.

**Du rapporterar resultatet själv** eftersom propkontot inte har API:
`/pnl -120` efter en förlust, `/pnl 240` efter en vinst. Det räknar även upp antalet trades.

## 5b. GEX-nivåer till TradingView-indikatorn "GEX Daily Levels"

Indikatorn ritar nivåer från en sträng i formatet `pris,etikett,typ;…`. Beräkningsmotorn i
`gex.py` tar fram alla typer den förstår och `desk.py` levererar strängen färdig att klistra in.
Samma nivåer gäller NQ och MNQ (samma pris). GC och MGC likaså.

| Typ | Nivå | Så räknas den |
|---|---|---|
| `res` / `sup` | Call Wall / Put Wall | strike med störst positiv resp. negativ dealer-GEX över de 4 närmaste expiries |
| `resw` / `supw` | samma vägg, men svag | väggen vinner mindre än `GEX_WALL_MIN_MARGIN` gånger över nästa strike — ritas prickad, märks "?" och ger inga larm |
| `res0` / `sup0` | 0DTE-väggar | samma sak, bara närmaste expiry |
| `flip` | Gamma Flip | spotnivå där nettoprofilen byter tecken (över: dämpning, under: trend), räknad på de 3 närmaste expiries |
| `hgex` | HGEX | strike med störst absolut gamma, dagens magnet |
| `mpain` | Max Pain | strike där optionsinnehavarnas totala värde är minst, närmaste expiry |
| `gpos` / `gneg` | G+ / G− | näst största positiva resp. negativa gammastrikes |
| `emh` / `eml` | Expected Move | pris ± ATM-straddle (call + put mid) för närmaste expiry |
| `emb` | EM-band | ± 50 % av expected move |
| `ivh` / `ivl` | 1D min/max | pris ± spot × ATM-IV × √(1/252) |
| `opo` / `opu` / `opd` | Öppning + ATR-grid | dagens RTH-öppning ur feeden, ± 0,5 och 1,0 dags-ATR |

Regimen (positiv eller negativ gamma) läses ur nettot vid priset, inte ur var priset står mot
Gamma Flip. Tumregeln "över flippen = positiv gamma" gäller bara när profilen korsar noll en
gång. I en putdominerad kedja kan nettot vända tillbaka och bli negativt ovanför flippen — då
sätts flaggan "flip omvänd" i meddelandet, och det är regimraden som gäller. Flippen väljs som
den nollkorsning som ligger närmast priset.

Väggarna räknas på de fyra närmaste expiries, flippen och regimen på de tre närmaste
(`GEX_EXPIRIES` respektive `GEX_FLIP_EXPIRIES`). Det är inte godtyckligt. Ett kvartalsförfall
som ligger några dagar bort bär så mycket open interest att det drar nollgammanivån hundratals
punkter — den 15 september 2026 flyttade förfallet den 18:e flippen 293 NDX-punkter nedåt, från
strax ovanför priset till en bit under, alltså från negativ till positiv regim, medan den gamma
som faktiskt förföll den veckan sa negativ. Väggarna behöver tvärtom ha kvartalen med: först då
träffar de. Skiljer sig hela kedjans flipp mer än en halv procent visas den inom parentes i
Telegram, för efter förfallet hoppar nivån dit.

En vägg är bara värd att handla på om den vinner övertygande över nästa strike
på samma sida. Den 22 september låg guldets två tyngsta callstrikes, 410 och 414,
båda på 0,01 miljarder — vilken av dem som blev "call wall" avgjordes av brus i
open interest, medan putsidan samma dag vann med sex gångers marginal. Därför
mäts marginalen mot den tyngsta rivalen utanför väggens egen zon: grannstrikes
inom `GEX_WALL_ZONE_PCT` (0,5 %) räknas inte som konkurrenter, de är samma vägg
utspridd över två priser. Vinner väggen mindre än `GEX_WALL_MIN_MARGIN` (1,5)
gånger får den typen `resw`/`supw`, ritas prickad och dämpad, märks med "?" i
etiketten och statusraden, ger inga larm, och kan döljas helt med kryssrutan
"Visa svaga väggar". Telegram skriver ut marginalen: "Call wall svag (1,2× nästa
strike)".

Källa är NDX-indexoptioner för NQ och GLD-optioner för GC, hämtade från CBOE:s fördröjda kedja med
yfinance som reserv (`GEX_SOURCE=auto|cboe|yahoo`). NDX är samma underliggande som NQ, så
nivåerna översätts additivt med futuresbasisen NQ−NDX i stället för en ETF-kvot — strikes ligger
var tionde punkt i stället för var 41:e, och ingen kvot kan bli fel. QQQ finns kvar som reserv
(`GEX_NQ_SOURCE=qqq`). GC skalas fortfarande med
live-kvoten futures/ETF. Open interest uppdateras en gång per dygn, så strängen är stabil
under dagen; expected move och IV läses från aktuella premier.

Basisen NQ−NDX måste mätas mellan två priser från samma ögonblick. Under USA-börsens öppettider
går det direkt. Utanför dem är kedjan gårdagens stängning medan futuren rört sig i natt, och en
basis räknad mot live-priset skulle svälja hela nattrörelsen — då hamnar varenda nivå fel.
Ordningen är därför: live-mätning när börsen är öppen, annars kedjans eget terminspris ur
put-call-paritet parat med futurepriset vid kedjans stängning (16:15 ET), annars en sparad
RTH-mätning från `levels/ratio.json`, och först om allt det saknas QQQ-kedjan som reserv.

Den sista vägen syns i Telegram som `⚠ reservkedja QQQ — inte NDX`. Den 16 september 2026 gick
morgonkörningen på QQQ utan att det syntes någonstans, och eftersom QQQ:s strikes ligger 41
NQ-punkter isär i stället för 10 låg put wall 329 punkter från vad en NDX-baserad tjänst visade.
Meddelandet säger nu alltid vilken kedja nivåerna kommer från.

Hämta strängen:

- Telegram: `/tvgex nq`, `/tvgex gc` eller `/tvgex all`. Svaret är ett kodblock, tryck för att kopiera.
- HTTP: `/desk/gexstring?inst=NQ` (ren text), `/desk/gexstring?inst=all` (JSON med båda).
- Automatiskt via GitHub Actions: vardagar 08:00 (London-uppsättning, EM/öppning centrerade på nattens pris) och 13:05 svensk tid (USA-uppsättning med dagens open interest). Desken på Render postar dessutom runt 09:10 ET om den kör; stäng av med `DESK_MORNING_GEX=0`.
  - Två avsändare: desken på Render har en egen klocka som postar 08:00, 13:05 och 16:05 svensk tid (`DESK_GEX_TIMER=0` stänger av), och GitHub Actions kör samma tre fönster som backup. GitHub startar inte alltid sina schemalagda jobb — den 10 september kom ingen av morgonens fem croner igång — så desken är den som ska komma i tid. Kör båda får du samma nivåer två gånger; de har separata dagslås.
  - Desken räknar numera via `gex_cli.run()`, alltså med samma rimlighetskontroll, källbyte och RTH-kvot som jobbet. Det gäller även `/tvgex`.
  - Tre fönster per vardag: 08:00 (London), 13:05 (USA) och 16:05 svensk tid (RTH, en halvtimme efter USA-öppning). RTH-körningen är den enda som ligger inom 09:30-16:00 ET och därmed den enda som kan mäta kvoten futures/ETF live — den sparas i `levels/ratio.json` och används av morgonkörningarna, som annars skulle räkna mot ETF:ens gårdagsstängning.
  - GitHub startar schemalagda jobb när det finns kapacitet — de kan bli timmar sena eller utebli. Därför startas varje fönster fem gånger (08:00-10:00, 13:05-15:05 och 16:05-17:05 svensk sommartid). Första körningen som lyckas låser fönstret för dagen i `levels/sent.json`, resten hoppar över. Blir det fel kommer ett meddelande om varför, en gång per fönster, och nästa körning provar igen.
  - Nivåerna sanity-testas innan de skickas (antal strikes, minst två expiries, närmaste expiry inom fem dagar, put wall under call wall, väggar inom 25 % av priset, IV/EM måste gå att räkna). En halv optionskedja från Yahoo ger alltså ett felmeddelande och ett nytt försök i stället för nonsensnivåer. Trösklarna kan ändras med `GEX_MIN_STRIKES`, `GEX_MAX_WALL_PCT`, `GEX_MAX_NEAR_DAYS`, `GEX_TRIES`, `GEX_RETRY_SLEEP`.
  - Behöver du nivåerna direkt: kör workflowen manuellt (Actions -> GEX Daily Levels -> Run workflow, funkar från GitHub-appen i telefonen). Manuell körning skickar alltid och låser samtidigt fönstret så schemat inte dubblar.

Indikatorn finns i repot: `pine/grabit_gex_levels.pine`. Klistra in den i Pine Editor, lägg till på grafen och
klistra in strängarna i fälten "NQ — levels string" och "GC — levels string". Den ritar bara GEX-nivåerna.

Förvalet "Ren" visar exakt tre linjer — Call Wall, Gamma Flip och Put Wall — med korta prispiller i högerkanten och en statusrad överst: underliggande → instrument, pris, C/Z/P och om priset ligger över eller under flippen. "Nyckelnivåer" lägger till HGEX, Max Pain och EM±, "Alla" resten (0DTE-väggar, G+/G−, IV-range och ATR-grid). Färgtemat GRABIT har röd Call Wall och lila Put Wall, Klassisk har grön respektive röd. Larm går vid korsning av väggar, 0DTE-väggar, flip och HGEX — dock aldrig på
en svag vägg. Andra väggen (CW2/PW2) är avstängd som förval: tre linjer räcker
för de flesta dagar, och resten ligger kvar i strängen för den som kryssar i
dem eller byter preset.

## 6. Telegram-kommandon

| Kommando | Gör |
|---|---|
| `/desk` | status: spärr, P&L, budget, datafärskhet |
| `/levels nq` eller `/levels gc` | alla nivåer just nu |
| `/gex nq` | gamma-nivåer och regim |
| `/tvgex nq`, `/tvgex all` | sträng till TradingView-indikatorn GEX Daily Levels |
| `/plan nq` | kör motorn nu och visar bästa setupen även under tröskeln |
| `/risk` | aktiva regler och gränser |
| `/pnl -120` | rapportera resultat. `/pnl set 0` nollställer |
| `/paus`, `/kör` | stoppa/starta larmen |

Samma sak via HTTP: `/desk/status`, `/desk/levels?inst=NQ`, `/desk/gex?inst=GC`,
`/desk/plan?inst=NQ`, `/desk/setups`, `/desk/pnl?key=ADMIN&add=-120`.

## 7. Första start

Lagret är tomt tills TradingView börjat skicka. Desken hämtar därför automatiskt fem dagars
1-minutshistorik från Yahoo för NQ och GC vid uppstart så att nivåer finns direkt. Yahoo är
10 minuter fördröjt och används bara till historik, aldrig till entries. Framtvinga med
`/desk/bootstrap?key=ADMIN`.

## Steg två (när steg ett sitter)

- Äkta orderflöde med tick-data, footprint och riktig delta: Databento CME live, cirka 179–199 USD/mån.
- AI-kommentar på varje setup via Anthropic-nyckeln som redan finns i api.py.
- Automatisk utfallsspårning av skickade planer (TP/SL-träff) som robotens journal.
