# Bygga om Android-appen (TWA)

GRABIT på Google Play är en **TWA** — ett skal runt grabitlabs.com. Allt
innehåll och all logik ligger på webben, så vanliga uppdateringar går ut
direkt via Render utan att appen behöver byggas om.

Appen behöver bara byggas om när **Google höjer kravet på API-nivå**
(händer ungefär varje år, deadline brukar vara sista augusti).

> **Nuvarande krav:** `targetSdkVersion 36` (Android 16).
> Google blockerar appuppdateringar från **31 aug 2026** om kravet inte uppfylls.

---

## Engångsuppsättning

```bash
npm install -g @bubblewrap/cli@latest
```

Lägg din **upload-nyckel** som `android.keystore` i projektroten
(committa den ALDRIG — den ligger i .gitignore).

## Bygga om vid ny API-nivå

```bash
# 1. Uppdatera Bubblewrap först — det är den som bestämmer targetSdkVersion
npm install -g @bubblewrap/cli@latest

# 2. Uppdatera projektmallen till senaste (drar in nya targetSdk)
bubblewrap update

# 3. Höj versionCode i twa-manifest.json (+1 mot förra släppet)

# 4. Bygg och signera
bubblewrap build
```

`bubblewrap build` producerar `app-release-bundle.aab` — den laddas upp i
Play Console.

**Kontrollera targetSdk innan uppladdning:**

```bash
grep -r "targetSdk" android/app/build.gradle
```

Står det fel värde kan det sättas explicit i `android/app/build.gradle`:

```gradle
android {
    compileSdkVersion 36
    defaultConfig {
        targetSdkVersion 36
    }
}
```

---

## ⚠️ Två fällor

### 1. Samma signeringsnyckel
Byggs appen med en **ny** nyckel avvisar Play uppdateringen — en app kan
bara uppdateras med samma upload-nyckel. Tappar du nyckeln går appen inte
att uppdatera alls (då krävs nyckelåterställning via Google-support).

### 2. Fingeravtryck och assetlinks
Ändras upload-nyckelns SHA-256 måste `/.well-known/assetlinks.json`
uppdateras, annars visar TWA:n en **webbläsar-adressrad** i appen.

Endpointen läser env-variabeln `ANDROID_CERT_SHA256` på Render och stödjer
**flera fingeravtryck kommaseparerat** — lägg in både gammalt och nytt under
övergången så bytet blir riskfritt:

```
ANDROID_CERT_SHA256 = AA:BB:CC:...(gammalt), DD:EE:FF:...(nytt)
```

Rätt fingeravtryck hittas i Play Console → **Appintegritet → Appsignering**.

---

## Släppordning

1. Kolla nuvarande SHA-256 i Play Console
2. Bygg om enligt ovan (samma `packageId`, samma nyckel)
3. Lägg ev. nytt fingeravtryck i `ANDROID_CERT_SHA256` på Render (behåll det gamla)
4. Ladda upp AAB:n till **intern testkanal**
5. Öppna appen — syns ingen adressrad är assetlinks korrekt
6. Promota till produktion

## Alternativ: PWABuilder

Byggdes appen ursprungligen på **pwabuilder.com** går det lika bra att
regenerera där. Sätt då **exakt** samma `Package ID`
(`com.grabitlabs.app`) och välj *"Use my existing signing key"*.

## Filer

| Fil | Vad |
|---|---|
| `twa-manifest.json` | Bubblewrap-konfiguration (versionshanterad) |
| `android.keystore` | Din upload-nyckel — **committas aldrig** |
| `android/` | Genereras av Bubblewrap — committas inte |
