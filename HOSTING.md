# Var grabit ska köra

Render tar ungefär tusen kronor i månaden för det här. Det går att få ner på
tre sätt: stanna kvar men rätta planen, flytta till en gratis Oracle-maskin,
eller flytta till Hetzner för runt fyrtio kronor. Filerna i repot (`Dockerfile`,
`docker-compose.yml`, `Caddyfile`, `deploy.sh`) gör flytten till samma handgrepp
oavsett vilken värd du väljer — men läs **Alternativ 0** först, för appen i sig
är inte dyr att köra.

Appkoden är oförändrad. Det enda som skiljer mot Render är att HTTPS sköts av
Caddy i stället för av plattformen, och att den persistenta disken är en
Docker-volym.

## Vad du behöver ta med dig från Render

**1. Miljövariablerna.** Dashboard → grabit-api → Environment. Kopiera värdena
till `.env` på den nya servern (mallen ligger i `.env.example`). De som gör ont
att tappa:

| Variabel | Vad som händer utan den |
|---|---|
| `TELEGRAM_TOKEN`, `CHAT_ID` | inga larm i Telegram |
| `TV_WEBHOOK_SECRET` | TradingViews larm avvisas |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | alla push-prenumeranter måste lägga till appen på nytt |
| `ANTHROPIC_API_KEY` | inga AI-texter |
| `FINNHUB_API_KEY`, `FMP_API_KEY` | tunnare kursdata |
| `ALPACA_KEY`, `ALPACA_SECRET` | roboten kan inte lägga order |

Klistra dem aldrig i en chatt eller i repot — repot är publikt. Skriv dem direkt
i `.env` på servern.

**2. Innehållet i den persistenta disken** (`DATA_DIR`), om du kommer åt den:

| Fil | Innehåll | Om den tappas |
|---|---|---|
| `push_subs.json` | push-prenumeranter | alla måste prenumerera om sig |
| `facit.json` | träffstatistik på gamla signaler | historiken nollställs |
| `price_alerts.json` | dina prislarm | måste läggas in igen |
| `desk_state.json`, `desk_bars_*.jsonl` | deskens dagsläge | byggs upp igen nästa dag |
| `besok.json`, `ai_text_cache.json` | besöksräknare, AI-cache | struntsak |

Är tjänsten redan avstängd kommer du sannolikt inte åt disken. Inget av det är
kritiskt — appen startar med tomma filer och fyller på igen.

**3. Kolla fakturan medan du är inne.** Starter-planen i `render.yaml` kostar
bråkdelen av tusen kronor i månaden. Är det flera tjänster, en stor disk eller
en uppgraderad instans som drar? Det avgör om något mer behöver flyttas.

Koden behöver du inte ta med — den ligger här.

## Alternativ 0: stanna på Render, men billigare

Appen är inte dyr att köra — en tusenlapp i månaden är ungefär tio gånger vad
starter-planen kostar. Innan du flyttar, kontrollera i tur och ordning:

**Vilken plan tjänsten faktiskt ligger på.** `render.yaml` säger `starter`, men
den filen styr bara om tjänsten skapats som en Blueprint. Är den skapad för hand
i dashboarden gäller inställningen där, och en tjänst som råkat hamna på standard
eller pro kostar mångdubbelt utan att du märker något annat än på fakturan.
Dashboard → grabit-api → Settings → Instance Type.

**Hur många tjänster som faktiskt är igång.** Fakturan är per tjänst. Ligger det
kvar en gammal testtjänst, en streamlit-variant av `app.py` eller en cron job
sedan tidigare betalar du för dem också.

**Diskens storlek.** Render tar betalt per gigabyte och månad. `DATA_DIR` innehåller
jsonl-filer som växer — `desk_bars_*.jsonl` får en rad per minutbar och varje
dag. Är disken tilltagen i överkant går den att krympa.

**Bandbredd.** Bakgrundsslingorna i `api.py` hämtar kursdata dygnet runt. Ligger
du över fribeloppet syns det som en egen rad på fakturan.

Landar du på starter är appen tillbaka på en normal kostnad, och det enda som
återstår är att den har 512 MB att röra sig på. Det räcker, men med liten
marginal — pandas, numpy och yfinance tar det mesta. Vill du ha luft kan
bakgrundsslingorna (watchlist, facit, daily extra) flyttas till GitHub Actions
på samma sätt som GEX-jobbet redan är. Repot är publikt, så de körningarna är
gratis utan minutgräns, och webbtjänsten behöver då bara svara på anrop.

Oavsett vad du väljer: `buildFilter` i `render.yaml` stoppar ombyggena som
GEX-nivåerna utlöste. Trettiofyra deployer den senaste månaden, varav två om
dagen mitt i USA-sessionen, med allt desken höll i minnet nollställt varje gång.

## Alternativ 1: Oracle Cloud Always Free (0 kr)

Fyra ARM-kärnor och 24 GB att fördela, 200 GB disk, alltid vaken. Kort krävs vid
registrering men debiteras inte.

1. Skapa konto på `cloud.oracle.com`, välj en region nära dig (Stockholm eller
   Frankfurt). ARM-kapaciteten tar tidvis slut — får du "out of capacity",
   försök igen senare eller välj annan region.
2. Compute → Instances → Create. Shape: **VM.Standard.A1.Flex**, 2 kärnor,
   12 GB. Image: **Ubuntu 24.04**. Ladda ner SSH-nyckeln.
3. Networking → öppna port 80 och 443 i security list (ingress, 0.0.0.0/0).
4. På maskinen måste brandväggen också öppnas — Oracles Ubuntu-image har
   iptables på:
   ```
   sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   ```
5. Gå vidare till **Installationen** nedan.

## Alternativ 2: Hetzner (~4 €/mån)

Enklare, inget kapacitetslotteri, och du slipper Oracles brandväggspill.

1. `console.hetzner.cloud` → New project → New server.
2. Läge Helsingfors eller Nürnberg, image **Ubuntu 24.04**, typ **CAX11**
   (2 vCPU ARM, 4 GB) — räcker med marginal.
3. Lägg in din SSH-nyckel, skapa servern. Port 80 och 443 är öppna som standard.
4. Gå vidare till **Installationen**.

## Domännamn

Caddy behöver ett namn för att hämta certifikat. Har du ingen domän: skapa en
gratis subdomän på `duckdns.org`, peka den på serverns IP och använd den.
Fungerar likadant som en köpt domän.

## Installationen

Samma på båda värdarna:

```bash
ssh ubuntu@SERVERNS_IP          # Hetzner: root@SERVERNS_IP

# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && exec su -l $USER

# Koden
git clone https://github.com/Ljunglofs/Moneygrab.git grabit
cd grabit

# Hemligheterna
cp .env.example .env
nano .env                       # klistra in värdena från Render, sätt DOMAIN

# Igång
docker compose up -d --build
docker compose logs -f grabit   # Ctrl+C när den svarar
```

Första bygget tar några minuter (pandas och numpy). Testa sedan:

```
https://din.domän/api/health
```

## Efter flytten

**Peka om TradingView.** Larmen i `TRADINGVIEW.md` och `DESK.md` går mot
`grabit-api-80dh.onrender.com`. Byt värdnamnet i varje larm-URL till din nya
domän — resten av adressen är oförändrad.

**Peka om webbappen.** `index.html` rad 1287:

```js
const CONFIG = { API_BASE: "https://grabit-api-80dh.onrender.com" };
```

Byt till din domän. Ligger sidan på GitHub Pages eller Cloudflare Pages
(gratis) behöver du inte servera den från appen alls.

**Stäng av Render** först när det nya svarar. Ta med att autodeployen där
byggde om appen varje gång GEX-jobbet committade nivåer till main — tre gånger
om dagen helt i onödan. Här styr du det själv med `./deploy.sh`.

## Underhåll

```bash
./deploy.sh                      # uppdatera till senaste main
docker compose logs -f grabit    # läs loggen
docker compose restart grabit    # starta om
docker compose down              # stoppa allt
```

Appen startas om automatiskt om den kraschar eller om servern bootar, och
`HEALTHCHECK` i Dockerfile fångar en hängd process. Certifikatet förnyas av
Caddy utan att du gör något.

## Det som inte behöver någon server

GEX-nivåerna räknas och skickas av GitHub Actions (`.github/workflows/gex_daily.yml`)
och påverkas inte av att appen ligger nere. De fortsatte gå ut i Telegram hela
tiden medan Render var avstängt. Repot är publikt, så de körningarna är gratis
utan minutgräns.
