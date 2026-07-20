# Koppla NASDAQ ROBBER till Skilling (cTrader) — steg för steg

Boten skriver **bara en kö-rad** när den ger en signal. En fristående process,
`skilling_exec.py`, läser kön och lägger ordern på ditt Skilling **cTrader**-konto
via cTrader Open API. Två processer = en hängande mäklaranslutning kan aldrig
frysa skannern.

> **Handlar CFD, inte aktier.** Signalerna gäller US100 (NAS100). Du äger inget
> underliggande — det är en riktnings-CFD med hävstång. Demo först. Alltid.

---

## 1. Konto & API-nycklar (engångsjobb)

1. Logga in på Skilling → öppna ett **cTrader-konto**. Välj **Demo**.
2. Gå till **connect.spotware.com** → *Applications* → skapa en app.
   Du får **ClientId** och **ClientSecret**.
3. Kör OAuth-flödet för appen (Spotware visar en "Playground"/token-sida) →
   du får en **accessToken** och kan lista dina konton. Notera
   **ctidTraderAccountId** för ditt **demo**-konto (ett heltal).

## 2. Miljövariabler

Säkra defaultar: **demo + dry-run**. Inget skickas förrän du själv slår av dry-run.

```bash
# --- På boten (scannern) ---
SKILLING_ENABLED=1          # boten börjar skriva kö-rader

# --- På executorn (skilling_exec.py) ---
CT_MODE=demo                # demo | live   (börja demo)
CT_DRY_RUN=1                # 1 = logga tänkta ordrar, skicka INGET
CT_CLIENT_ID=...
CT_CLIENT_SECRET=...
CT_ACCESS_TOKEN=...
CT_ACCOUNT_ID=...           # ctidTraderAccountId (heltal)
CT_SYMBOL_NAME=US 100       # exakt som symbolen heter i din cTrader
DATA_DIR=/var/data          # SAMMA disk som boten, så kön delas

# Storlek — välj EN väg:
SKILLING_VOLUME=...         # (rek.) rått cTrader-volymvärde för önskad storlek
# ELLER
SKILLING_RISK_CCY=15        # auto-size: risk/trade i kontovaluta (verifiera!)

# Skydd:
CT_MAX_OPEN=1               # max samtidiga positioner
CT_MAX_DD_CCY=250           # kill-switch: stäng handel om dagens förlust > detta
CT_ONLY_SOURCES=VWAP-US,VWAP-LDN,ORB   # (valfritt) bara dessa setups
```

## 3. Installera & kör executorn

```bash
pip install -r requirements_skilling.txt
python skilling_exec.py
```

Utan nycklar, eller med `CT_DRY_ONLY=1`, kör den ren **kö-loggning** (ingen
mäklare alls) — bra för att se att signalerna når fram.

## 4. Verifieringsordning (hoppa inte över)

1. **Kö-loggning:** `CT_DRY_ONLY=1` → se att botens signaler dyker upp i loggen.
2. **Demo + dry-run:** riktig cTrader-auth, men `CT_DRY_RUN=1`. Kontrollera att
   den **loggade volymen** matchar en manuell testorder av den storlek du vill ha.
   Justera `SKILLING_VOLUME` tills det stämmer.
3. **Demo skarpt:** `CT_DRY_RUN=0`, fortfarande `CT_MODE=demo`. Låt den handla
   på demo i **veckor**. Jämför utfallet mot `vwap_signals.jsonl` /
   `robber_outcomes.jsonl`.
4. **Live:** först när demon visar positiv R. `CT_MODE=live`, liten storlek,
   `CT_MAX_DD_CCY` satt lågt. Öka aldrig storleken efter en förlust.

## Filer

| Fil | Vad |
|---|---|
| `skilling_queue.jsonl` | Botens signaler (in till executorn) |
| `skilling_fills.jsonl` | Vad executorn gjorde (dry-run + skarpa ordrar) |
| `skilling_state.json` | Dedup + kill-switch-status |

## Order-typer

| Setup | Order | Varför |
|---|---|---|
| Momentum (conf ≥ 85 + exceptionell candle) | MARKET | ta breakouten direkt |
| Huvudmotorn, retest | LIMIT i VWAP-zonen | köp värde, jaga aldrig |
| ORB / VWAP-retest | STOP på breakout-nivån | in när nivån bryts |

Varje order får SL och TP1 med sig. TP2/trailing sköter du manuellt tills vi
byggt positionsförvaltning i executorn (nästa steg om du vill).
