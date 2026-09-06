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

## 6. Telegram-kommandon

| Kommando | Gör |
|---|---|
| `/desk` | status: spärr, P&L, budget, datafärskhet |
| `/levels nq` eller `/levels gc` | alla nivåer just nu |
| `/gex nq` | gamma-nivåer och regim |
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
