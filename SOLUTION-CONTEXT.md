# SOLUTION-CONTEXT — NCS Production (Norge)

Teknisk referansedokument skrevet for å kunne slå denne løsningen sammen med
søsterløsninger for andre land. Målet er at datainnsamlingen kan reimplementeres
og datamodellen forstås uten å lese kildekoden.

Alt nedenfor er verifisert mot kildekoden og mot live-APIet 2026-09-14.
Påstander som ikke lot seg verifisere er markert med **USIKKER:**.

Dokumenterer koden slik den står etter denne PR-en (basis: `f69eb13`).
NVE-appen som tidligere lå her som `nve.html` er flyttet til et eget repo
(`sheffielddivided/nve`) og er ikke en del av denne løsningen.

---

## 1. Oversikt

**Land:** Norge — norsk kontinentalsokkel (Norwegian Continental Shelf, NCS).

**Myndighet/kilde:** Sokkeldirektoratet (Sodir, engelsk: Norwegian Offshore
Directorate; het tidligere Oljedirektoratet / NPD). All data hentes fra Sodirs
åpne ArcGIS-tjeneste **FactMaps**, som er maskinlesbar speiling av FactPages.

- API-rot: `https://factmaps.sodir.no/api/rest/services/DataService/Data/FeatureServer`
  (`fetch_sodir.py:25`, `index.html:202`)
- Menneskelesbar kilde: `https://factpages.sodir.no`

**Hva løsningen gjør:** Én mobiloptimalisert enkeltsides webapp som viser
produksjonshistorikk på norsk sokkel som stablede søylediagram i **boe/dag**.
Brukeren velger enten *felt* eller *selskap*, og en periodeoppløsning
(månedlig / kvartalsvis / årlig). Data kan lastes ned som Excel.

**Arkitektur i to deler:**

1. **Innsamling** — en Python-jobb (`fetch_sodir.py`) kjører daglig i GitHub
   Actions, henter fire datasett fra Sodir og committer dem som kompakte
   JSON-filer under `data/` i selve repoet.
2. **Presentasjon** — `index.html` er en helt statisk fil som leser disse
   JSON-filene ved oppstart. Finnes de ikke, faller appen tilbake til å kalle
   Sodir-APIet direkte fra nettleseren (`index.html:562-595`, `690-701`, `760-766`).

Det finnes **ingen backend, ingen database og ingen byggesteg**. Repoet *er*
deployet.

---

## 2. Teknologistack

| Lag | Teknologi | Referanse |
|---|---|---|
| Frontend | Vanilla JS/HTML/CSS i én fil, ingen rammeverk, ingen bundler | `index.html` (1529 linjer, 82 KB) |
| Grafer | Chart.js 4.4.1 via cdnjs | `index.html:11` |
| Excel-eksport | ExcelJS 4.3.0 via cdnjs | `index.html:12` |
| Innsamling | Python 3.12 + `requests` (eneste avhengighet) | `fetch_sodir.py:23`, workflow linje 28, 31 |
| Lagring | Statiske JSON-filer i git under `data/` | `fetch_sodir.py:32` |
| Hosting | GitHub Pages fra `main`-branchens rot | repo-innstilling `has_pages: true` |
| CI/scheduler | GitHub Actions | `.github/workflows/update-sodir-data.yml` |

**Ingen database.** Ingen `package.json`, `requirements.txt`, lockfil,
testsuite eller linter finnes i repoet.

### Kjøre lokalt

```bash
# Innsamling (skriver/oppdaterer data/)
pip install requests
python fetch_sodir.py

# Frontend — må serveres over HTTP, ikke åpnes som file://,
# fordi den fetcher data/*.json med relativ sti (index.html:215, 221)
python3 -m http.server 8000
# -> http://localhost:8000/index.html
```

### Deploy

Push til `main` publiseres automatisk av GitHub Pages. Datafilene oppdateres av
workflowen, som committer direkte til `main` (workflow linje 40-46).

---

## 3. Datakilder

Alle fire datasett kommer fra samme ArcGIS FeatureServer. **Ingen autentisering,
ingen API-nøkkel, ingen rate limiting observert.**

| Lag-ID | Navn i tjenesten | Type | Rader (2026-09-14) | Brukes til |
|---|---|---|---|---|
| 7100 | `field` | Feature Layer (polygon) | 142 | Feltliste (id + navn) |
| 7300 | `profiles` | Table | 32 220 | Produksjonstall |
| 7108 | `field_licensee_hst` | Table | 10 090 | Eierandeler over tid |
| 7114 | `field_reserves_company` | Table | 417 | Reserver per selskap |

Lag-ID-ene er hardkodet to steder: `fetch_sodir.py:27-30` og `index.html:203-206`.

**Format:** ArcGIS REST JSON (`f=json`). Svar har formen
`{"features":[{"attributes":{...}}, ...]}`. `maxRecordCount` er 2000 på alle fire
lag — derfor er sidestørrelsen satt til nøyaktig 2000 (`fetch_sodir.py:33`).

**Oppdateringsfrekvens:** Sodir publiserer produksjonstall **månedlig**.
Innsamlingsjobben kjører likevel daglig (se §4). Verifisert: datafilene i repoet
var uendret fra 2026-08-31 til 2026-09-14 — en ny kjøring 2026-09-14 ga
byte-identisk resultat for alle fire datasett.

**Hvordan endringer oppdages:** Løsningen henter **alt** på nytt hver gang og
sammenligner den serialiserte JSON-teksten mot fila på disk
(`fetch_sodir.py:206-221`). Det finnes ingen inkrementell henting.

> **Merk for reimplementasjon:** Kilden *har* tidsstempler for endring som denne
> løsningen ikke bruker: `fldDateUpdated` og `fldDateUpdatedMax` (lag 7100),
> `fldLicenseeDateUpdated` (lag 7108), `cmpDateOffResEstDisplay` (lag 7114).
> En inkrementell strategi er mulig, men er altså ikke implementert her.

### Stabilitet i kilden

- **Kolonnenavn er stabile, men stygge.** Sodir har en egen skrivefeil i
  skjemaet: `fldCurrentActivitySatus` (mangler «t» i «Status»), lag 7100. Den har
  ligget der lenge og bør antas å forbli.
- **Etatsnavn har endret seg** (Oljedirektoratet/NPD → Sokkeldirektoratet/Sodir).
  Prefiksen `npd` lever videre i kolonnenavn som `fldNpdidField`,
  `prfNpdidInformationCarrier`, `cmpNpdidCompany`. Gamle domener som
  `factpages.npd.no` er faset ut.
- **Selskapsnavn endres ved fusjoner/omorganiseringer.** Siden løsningen
  identifiserer selskaper på *navnestreng* (se §5), gir dette reell risiko.
- **USIKKER:** Jeg har ikke kunnet fastslå Sodirs formelle SLA, versjonerings-
  eller varslingspraksis for skjemaendringer. Lagnumrene (7100/7300/…) er
  interne ArcGIS-indekser og har ingen dokumentert garanti for stabilitet.

---

## 4. Innsamlingsprosess

### Trigger

`.github/workflows/update-sodir-data.yml`:

- **Schedule:** `cron: '15 4 * * *'` — 04:15 UTC daglig (linje 7)
- **Manuelt:** `workflow_dispatch` (linje 8)
- `permissions: contents: write` (linje 11-12) — nødvendig for å pushe
- `concurrency: group: sodir-data, cancel-in-progress: false` (linje 14-16)
- `timeout-minutes: 30` (linje 21)

> GitHubs cron-scheduler er «best effort» og kan forsinkes betydelig ved høy
> last. Siden dataene endres månedlig, er det uproblematisk her.

### Stegene

1. **Checkout + Python 3.12 + `pip install requests`** (workflow linje 24-31).
2. **`python fetch_sodir.py`** (linje 34). For hvert av de fire datasettene
   (`fetch_sodir.py:227-232`), i rekkefølge felt → produksjon → lisensiærer →
   reserver:
   - **Tell rader:** `returnCountOnly=true` (`fetch_sodir.py:75-79`).
   - **Finn sorteringsnøkkel:** hent lagets `objectIdField` (`fetch_sodir.py:61-72`).
     Dette er viktig — uten stabil sortering kan offset-basert paginering hoppe
     over eller duplisere rader. Feiler dette, fortsetter jobben *usortert* med
     en advarsel (linje 70-72).
   - **Paginer sekvensielt** i sider à 2000 med `resultOffset` og
     `orderByFields=<oid> ASC`, `returnGeometry=false` (`fetch_sodir.py:88-108`).
   - **Transformér** til kompakt form (`build_*`-funksjonene, se §5).
   - **Skriv kun ved endring** (`fetch_sodir.py:206-221`).
3. **Commit kun ved endring** (workflow linje 41-45): `git diff --staged --quiet`
   avgjør om det lages en commit. De fleste dagene blir dette en no-op.
4. **`meta.json` skrives bare hvis minst ett annet datasett endret seg**
   (`fetch_sodir.py:247-251`). Dette er bevisst: `meta.json` inneholder dagens
   dato, og ville ellers tvunget fram en commit hver eneste dag og dermed
   ødelagt no-op-egenskapen.

### Idempotens

**Ja, verifisert.** To kjøringer etter hverandre mot live kilde:
kjøring 1 skrev alle filer, kjøring 2 rapporterte «uendret» for alle fire og
skrev ingenting. Utdata er deterministisk fordi:

- radene sorteres eksplisitt før serialisering (`fetch_sodir.py:140`, `165`, `190`, `202`)
- JSON skrives med `sort_keys=True` og faste separatorer (`fetch_sodir.py:209`)
- flyttall rundes til 6 desimaler (`fetch_sodir.py:117`)

Sorteringen av lisensiærer må håndtere `None` eksplisitt, siden `None` ikke kan
sammenlignes med `int` i Python 3 (`fetch_sodir.py:190`).

### Feilhåndtering

- **HTTP-nivå:** 4 forsøk med eksponentiell backoff 2/4/8 s, timeout 120 s per
  request (`fetch_sodir.py:33-35`, `41-58`). ArcGIS returnerer feil som HTTP 200
  med `{"error": ...}` i kroppen — dette fanges eksplisitt (linje 49-50).
- **Datasettnivå:** Feiler ett datasett etter alle retries, **avbrytes hele
  jobben med exit 1 uten å skrive noe** (`fetch_sodir.py:237-243`). Repoet
  beholder da forrige gyldige datasett, og appen fortsetter på gamle tall.
  Konsekvensen er at datasettene alltid er innbyrdes konsistente — men også at
  én vedvarende feil fryser *alle* datasett.
- **Frontend:** Feiler henting av en `data/*.json`, returnerer `fetchBundle`
  `null` (`index.html:219-225`), og hver lastefunksjon faller gjennom til
  live-API-kall mot Sodir.

### Historiske korreksjoner

Sodir retter historiske tall i ettertid (vanlig i produksjonsstatistikk).
Løsningen håndterer dette **implisitt og korrekt**: siden hele datasettet hentes
på nytt hver dag og erstattes i sin helhet, plukkes enhver retting opp
automatisk ved neste kjøring. Det finnes ingen append-only-logikk, ingen
versjonering av tidligere verdier, og ingen sporing av *hva* som ble rettet —
git-historikken på `data/`-filene er det eneste revisjonssporet.

---

## 5. Datamodell

Fem filer under `data/`. Alle skrives som minifisert JSON med sorterte nøkler.
Alle bruker **kolonneorienterte array-rader**, ikke objekter per rad, for å
spare plass.

### `data/fields.json` — feltkatalog

```json
{"columns":["id","name"],"rows":[[23395946,"AASTA HANSTEEN"],[43437,"ALBUSKJELL"]]}
```

| Kolonne | Type | Kilde | Beskrivelse |
|---|---|---|---|
| `id` | int | `fldNpdidField` | Sodirs permanente felt-ID |
| `name` | string | `fldName` | Feltnavn, versalt |

142 rader. Sortert på navn (`fetch_sodir.py:140`).
Bygges av `fetch_sodir.py:132-141`.

### `data/production.json` — produksjonstall

```json
{"columns":["year","month","oil","gas","oe","water"],
 "fields":{"26376286":[[2026,5,3.16927,0.109115,3.33904,1.6872], ...]}}
```

Nøkkelen i `fields` er carrier-ID som **streng** (`fetch_sodir.py:156`).
Hver rad er et array i rekkefølgen gitt av `columns`:

| Pos | Kolonne | Type | Kilde | Enhet |
|---|---|---|---|---|
| 0 | `year` | int | `prfYear` | — |
| 1 | `month` | int | `prfMonth` | 1–12, **eller 0 for årssum** |
| 2 | `oil` | float | `prfPrdOilNetMillSm3` | mill Sm³ |
| 3 | `gas` | float | `prfPrdGasNetBillSm3` | mrd Sm³ |
| 4 | `oe` | float | `prfPrdOeNetMillSm3` | mill Sm³ o.e. |
| 5 | `water` | float | `prfPrdProducedWaterInFieldMillS` | mill Sm³ |

Radene sorteres på `(year, month)` per carrier (`fetch_sodir.py:165`).
Bygges av `fetch_sodir.py:144-169`.

> **`month = 0` er ikke en måned — det er årssummen.** Kilden leverer både
> månedsrader og en aggregert årsrad per år i samme tabell. Koden skiller dem i
> `isValidRow` (`index.html:414-416`). Blander man dem, dobbelttelles alt.

### `data/licensees.json` — eierandeler over tid

```json
{"columns":["fieldId","company","share","from","to"],
 "rows":[[43437,"Aquitaine Norge A/S",2.698,167616000000,236476800000]]}
```

| Pos | Kolonne | Type | Kilde | Beskrivelse |
|---|---|---|---|---|
| 0 | `fieldId` | int | `fldNpdidField` | Felt-ID |
| 1 | `company` | string | `cmpLongName` | Selskapsnavn (**er nøkkelen**) |
| 2 | `share` | float | `fldCompanyShare` | Eierandel i prosent |
| 3 | `from` | int\|null | `fldLicenseeFrom` | Epoch **millisekunder**, UTC |
| 4 | `to` | int\|null | `fldLicenseeTo` | Epoch millisekunder, `null` = fortsatt aktiv |

10 090 rader, 263 unike selskaper, 142 felt. 436 rader er aktive (`to = null`).
Bygges av `fetch_sodir.py:172-191`.

### `data/reserves.json` — reserver aggregert per selskap

```json
{"companies":{"Equinor Energy AS":3462.051955,"Petoro AS":2691.322247}}
```

22 selskaper. Verdien er **opprinnelig utvinnbar** oljeekvivalent i mill Sm³ o.e.,
summert over alle felt selskapet har andel i (`fetch_sodir.py:194-202`).
Brukes **kun til å sortere selskapslisten** (`index.html:789-798`) — aldri vist.

### `data/meta.json`

```json
{"generated":"2026-08-31","source":"Norwegian Offshore Directorate (Sodir) FactPages - https://factpages.sodir.no - NLOD 2.0"}
```

`generated` vises i bunnteksten på siden (`index.html:176`, `index.html:232-234`).

### Entitetsidentifikasjon — kritisk for sammenslåing

| Entitet | Identifiseres ved | Fra kilden? | Kommentar |
|---|---|---|---|
| Felt | `fldNpdidField` (int) | Ja | Stabil, permanent |
| Produksjonsenhet | `prfNpdidInformationCarrier` (int) | Ja | **Ikke det samme som felt** — se under |
| **Selskap** | **`cmpLongName` (streng)** | Ja | **Ingen ID brukes** — se under |
| Lisens / brønn | — | — | **Ikke modellert i det hele tatt** |

**Ingen ID-er er laget lokalt.** Alt er kildens egne nøkler.

#### Fallgruve A: carrier ≠ felt

Lag 7300 inneholder to typer bærere, gitt av kolonnen
`prfInformationCarrierKind`: **`FIELD`** og **`DISCOVERY`**. Lag 7100 inneholder
kun felt. Derfor:

- `production.json` har **186** carrier-ID-er
- `fields.json` har **142** felt

De 44 overskytende er *funn* (discoveries) med prøveproduksjon, som ennå ikke er
godkjent som felt. Verifisert mot kilden:

```
17196400: '16/1-12 Troldhaugen'  kind=DISCOVERY
25288497: '7220/11-1 (Alta)'     kind=DISCOVERY
44576:    '33/9-6 DELTA'         kind=DISCOVERY
```

Kun 3 av de 44 har produksjon > 0; resten er rene nullrader.
Koden henter **ikke** `prfInformationCarrierKind`, og har derfor ingen generell
mekanisme for dette — den håndterer ett enkelt funn med hardkoding (se §11).

#### Fallgruve B: selskap identifiseres på navn

Både lag 7108 og 7114 har kolonnen **`cmpNpdidCompany`** (heltalls-ID), men
løsningen henter den ikke. Alle koblinger mellom eierandeler, reserver og
diagramserier går på eksakt strengmatch av `cmpLongName`
(`index.html:536`, `548`, `754`, `778`). Konsekvenser:

- Skrivemåteendring eller fusjon splitter ett selskap i to i UI-et
- `getShare` returnerer stille `0` ved navnebom (`index.html:556`)
- Sammenslåing på tvers av land blir skjør

**Anbefaling ved reimplementasjon: bruk `cmpNpdidCompany` som nøkkel og behandle
navn som visningsattributt.**

### Tilgjengelige, men ubrukte kolonner

Verdt å kjenne til ved sammenslåing:

- **Lag 7300:** `prfPrdNGLNetMillSm3`, `prfPrdCondensateNetMillSm3`,
  `prfPrdOilGrossMillSm3`, `prfPrdGasGrossBillSm3`, `prfPrdOeGrossMillSm3`,
  `prfPrdCondensateGrossMillSm3`, `prfInvestmentsMillNOK`,
  `prfInformationCarrierKind`, `prfInformationCarrier` (navn), `prfPeriod`
- **Lag 7108:** `cmpNpdidCompany`, `cmpShortName`, **`fldSdfiShare`** (SDØE/statens
  direkte andel), `fldOwnerKind`, `fldOwnerName`, `cmpNationCode`,
  `fldLicenseeDateUpdated`, `fldLicenseeGUID`
- **Lag 7100:** `fldCurrentActivitySatus`, `fldDiscoveryYear`, `fldHcType`,
  `fldMainArea`, `cmpLongName` (operatør), `cmpNpdidCompany`, `wlbName`
  (funnbrønn), `fldDateUpdated`, `fldGUID`
- **Lag 7114:** `cmpRemainingOE` (gjenværende), `cmpRecoverableOil/Gas/NGL/Condensate`,
  `cmpShare`, `fldName`

Merk at løsningen bruker `cmpRecoverableOE` = *opprinnelig* utvinnbart, ikke
`cmpRemainingOE` = *gjenværende*. For sortering etter «hvor stort er selskapet i
dag» er gjenværende trolig riktigere.

---

## 6. Enheter og konverteringer

### Kildens enheter (fra feltaliaser i lag 7300, verifisert live)

| Felt | Alias i kilden | Enhet |
|---|---|---|
| `prfPrdOilNetMillSm3` | Net – oil [mill Sm3] | millioner standardkubikkmeter |
| `prfPrdGasNetBillSm3` | Net – gas [bill Sm3] | **milliarder** standardkubikkmeter |
| `prfPrdOeNetMillSm3` | Net – oil equivalents [mill Sm3] | mill Sm³ oljeekvivalenter |
| `prfPrdProducedWaterInFieldMillS` | Produced water in field [mill Sm3] | mill Sm³ |
| `prfPrdNGLNetMillSm3` | Net – NGL [mill Sm3] | *hentes ikke* |
| `prfPrdCondensateNetMillSm3` | Net – condensate [mill Sm3] | *hentes ikke* |

Reserver (lag 7114): `cmpRecoverableOE` i mill Sm³ o.e.; NGL oppgis i **mill tonn**,
ikke volum.

### Den eneste eksplisitte faktoren

```js
const BBL_PER_SM3 = 6.29;   // index.html:207
```

Hardkodet ett sted, i `index.html`. Brukes i alle fire konverteringsfunksjoner
(`index.html:422-429`). Hentescriptet gjør **ingen** enhetskonvertering — det
lagrer kildens tall som de er.

### Den implisitte faktoren — viktig

Alle fire funksjonene multipliserer med `1e6`, også gassfunksjonen:

```js
function oeBoed(oeMillSm3, d)   { return (oeMillSm3  * 1e6 * BBL_PER_SM3) / d; }  // :419
function gasBoed(gasBillSm3, d) { return (gasBillSm3 * 1e6 * BBL_PER_SM3) / d; }  // :425
```

For olje er det trivielt: mill Sm³ × 1e6 = Sm³.
For gass er det **ikke** en enhetsomregning til Sm³ — det er en skjult
oljeekvivalent-konvertering:

```
1 mrd Sm³ gass = 1e9 Sm³ gass
              = 1e9 / 1000  Sm³ o.e.      (bransjestandard 1000 Sm³ gass = 1 Sm³ o.e.)
              = 1e6 Sm³ o.e.
```

**Forholdstallet 1000:1 for gass→o.e. er altså bakt inn i tallet `1e6` og står
ingen steder i koden som en navngitt konstant.** Dette er den enkeltfaktoren som
er lettest å ta feil av ved reimplementasjon.

### Tidsoppløsning og dagnormalisering

Kilden gir **månedlige** volumer, pluss en årsrad (`month=0`) per år.
Alle tall i UI-et vises som **rate: boe/dag**, ikke volum. Divisoren er faktiske
dager i perioden:

- Månedlig: `daysInMonth(år, måned)` via `new Date(y,m,0).getDate()` (`index.html:315`)
- Årlig: `daysInYear` med korrekt skuddårsregel inkl. 100/400-unntak (`index.html:316`)
- Kvartalsvis: sum av dagene i kvartalets tre måneder (`index.html:435-438`)

Faller en rad bort, brukes **30 dager** som nøddivisor
(`index.html:1102`, `1136`, `1140`, `1144`, `1173`). Verdien er vilkårlig, men
treffer bare rader som uansett er tomme.

### Avrunding

- Innsamling: 6 desimaler (`fetch_sodir.py:117`)
- Enkeltfelt-visning: 1 desimal (`.toFixed(1)`, `index.html:1103-1105`)
- Alle andre visninger: 0 desimaler (`.toFixed(0)`)
- Tooltip: `Math.round` (`index.html:1338`)

Merk at `.toFixed()` gir **streng**, som umiddelbart konverteres tilbake med
unær `+`. Avrundingen er altså destruktiv og skjer *før* summering i Excel-eksporten.

---

## 7. Geodata

**Løsningen bruker ingen geodata, har ingen kart, og serverer ingen geometri.**

Det finnes likevel geometri i kilden, og det er relevant ved sammenslåing:

- **Lag 7100 (`field`) er en ekte Feature Layer** med
  `geometryType: esriGeometryPolygon` — feltomriss som polygoner.
- **Koordinatsystem i kilden:** `wkid 4230` = **ED50** (European Datum 1950),
  geografiske grader. Dette er Sodirs historiske datum, ikke WGS84. Ved
  sammenstilling med andre land må det transformeres (ED50 → WGS84 gir typisk
  et skift i størrelsesorden 100–200 m i Nordsjøen).
- **Lag 7300, 7108 og 7114 er rene tabeller** (`type: Table`, ingen geometri).
- Lag 7100 har også `Shape__Area` og `Shape__Length`.

Hentescriptet setter eksplisitt `returnGeometry: 'false'` (`fetch_sodir.py:94`),
og ingen av JSON-filene under `data/` inneholder koordinater.

> **Merk:** ArcGIS' standardverdi for `returnGeometry` er `true`. Frontendens
> fallback-sti satte den ikke, og lastet dermed ned polygongeometri den aldri
> brukte når den falt tilbake til live-API mot lag 7100 (verifisert live: ett
> felt returnerte en ring med 258 punkter). `queryLayer` setter den nå eksplisitt
> til `'false'` (`index.html:357-372`). Tabellagene var uansett upåvirket, siden
> de ikke har geometri.

---

## 8. Forretningslogikk

### 8.1 «Oil» i grafen er ikke oljekolonnen

Dette er den viktigste ikke-åpenbare regelen i hele løsningen.

```js
function oilBoed(oilMillSm3, gasBillSm3, oeMillSm3, d) {   // index.html:420-424
  const gasBoe = (gasBillSm3 * 1e6 * BBL_PER_SM3) / d;
  const totBoe = oeBoed(oeMillSm3, d);
  return Math.max(0, totBoe - gasBoe);
}
```

Parameteren `oilMillSm3` **mottas, men brukes aldri**. Oljeserien beregnes som
*oljeekvivalenter minus gass*. Siden `prfPrdOeNetMillSm3` også inneholder NGL og
kondensat, betyr det at grafens «Oil» i realiteten er **alle væsker**
(olje + NGL + kondensat).

Verifisert numerisk på Johan Sverdrup 2026-06:

```
oil = 2.988533, gas = 0.090489, oe = 3.125740   (mill Sm³ / mrd Sm³)
oe − oil − gas          = 0.046718   ← NGL + kondensat
grafens «Oil» = oe−gas  = 3.035251   = 2.988533 + 0.046718
```

Avviket er ~1,5 % for Johan Sverdrup, men blir stort for kondensatrike gassfelt.

`Math.max(0, ...)` demper tilfeller der gass-o.e. overstiger totalt o.e. på grunn
av avrundingsstøy i kilden.

### 8.2 Equity-justering (eierandel)

Selskapsvisninger vekter produksjon med eierandelen som gjaldt **på det aktuelle
tidspunktet** (`index.html:543-557`):

1. Beregn en referansedato for perioden via `midDate` (`index.html:404-408`):
   - månedlig/kvartalsvis: **den 15. i måneden**
   - årlig: **1. juli** (`new Date(year, 6, 1)` — merk at 6 = juli, 0-indeksert)
2. Finn **første** lisensiærrad der selskapsnavnet matcher og datoen ligger
   innenfor `[from, to]`. `null` tolkes som åpen ende i begge retninger.
3. Del på 100 og multipliser med produksjonen.

Ingen match gir `0` (`index.html:556`) — stille, uten advarsel.

`records.find(...)` tar **første** treff. Finnes overlappende perioder for samme
selskap, vinner den som tilfeldigvis kom først etter sorteringen. Dette er ikke
validert noe sted.

### 8.3 Kvartalsaggregering

Kvartaler bygges kun av månedsrader, aldri av årsrader (`index.html:442-455`).

**Regelen for hvilke kvartaler som tas med er subtil:** et kvartal beholdes hvis
unionen av *alle valgte felt* dekker minst 3 distinkte måneder
(`index.html:454`). Det kreves altså ikke at hvert enkelt felt har alle tre
månedene. Velger man to felt der det ene har jan+feb og det andre har mar, regnes
Q1 som komplett.

Selve verdien er et **dagvektet gjennomsnitt**, ikke et enkelt snitt
(`index.html:460-473`):

```
kvartalsrate = Σ(månedsrate × dager_i_måned) / dager_i_kvartal
```

### 8.4 Filtrering av ufullstendige perioder

`isCompletePeriod` (`index.html:492-507`):

- **Årlig:** inneværende år ekskluderes helt (`+label < nowYear`)
- **Kvartalsvis:** inneværende kvartal ekskluderes til siste måned er passert
- **Månedlig:** returnerer alltid `true` — **inneværende, ufullstendige måned vises**

Asymmetrien er tilsiktet, men udokumentert i koden.

### 8.5 Trimming av ledende nullperioder

`trimLeadingZeros` (`index.html:1305-1313`) fjerner perioder fra starten der
*alle* dataserier er 0 eller mangler. Kalles fra `renderChart`
(`index.html:1316`). Formålet er at grafen skal begynne der produksjonen faktisk
startet. Nullperioder *inne i* eller på slutten av serien beholdes.

### 8.6 Håndtering av manglende verdier

- Innsamling: `None` → `0` for tall (`fetch_sodir.py:112-119`), men
  `None` bevares for datoer (`fetch_sodir.py:122-128`)
- Rader uten `fldNpdidField`/`cmpLongName` forkastes (`fetch_sodir.py:137`, `180`)
- Rader uten `prfYear` forkastes (`fetch_sodir.py:154`)
- Frontend: `|| 0` overalt ved uthenting av måleverdier
- Serier som er null hele veien skjules fra grafen via `hasAnyValue`
  (`index.html:559`, brukt `1175`, `1205`, `1316`)
- Felt uten navn vises som `Field <id>` (`index.html:1283`)

### 8.7 Sortering og standardvalg

- **Selskapsliste:** `Aker BP ASA` tvinges alltid øverst, deretter synkende
  reserver, deretter alfabetisk (`index.html:789-798`). Kun 22 av 263 selskaper
  har reserver, så resten faller til alfabetisk.
- **Feltliste:** felt der Aker BP er lisensiær sorteres øverst ved oppstart
  (`index.html:1435-1446`).
- **Standardvalg ved oppstart:** modus = `companies` (`index.html:271`),
  `Aker BP ASA` forhåndsvalgt (`index.html:1448`), og `JOHAN SVERDRUP`
  forhåndsvalgt i feltlisten (`index.html:581-582`).

Dette er ikke nøytral produktlogikk — det er en innebygd preferanse for ett
selskap. Ved sammenslåing bør det parametriseres.

### 8.8 Årsvelgerens oppførsel ved periodebytte

`setPeriod` (`index.html:850-875`) overstyrer brukerens årsvalg:

- **Til årlig:** setter `yearFrom = 1970`, tømmer cache, henter, og «snapper»
  deretter til første år med faktisk produksjon via `firstYearInCache`
  (`index.html:828-848`), som kun ser på årsrader med `oe > 0`.
- **Til månedlig/kvartalsvis:** setter `yearFrom = currentYear - 1`.

Begge veier nullstiller altså et årsintervall brukeren måtte ha satt selv.

---

## 9. Frontend

Én skjerm, ingen ruting, ingen tilstand i URL. Alt er i `index.html`.

### Kontroller (topp til bunn)

| Kontroll | Element | Verdier |
|---|---|---|
| Modusvelger | `#modeBtnFields` / `#modeBtnCompanies` (`:122-123`) | Fields / **Companies** (standard) |
| Årsintervall | `#yearFrom` / `#yearTo` (`:129-131`) | 1970–inneværende år, **synkende** (`:304`); standard `[år−1, år]` (`:311-312`) |
| Periode | `#btnMonthly/Quarterly/Annual` (`:134-136`) | **Monthly** standard |
| Undervisning | `#viewTabs` (`:141`) | Avhenger av modus, se under |
| Produsert vann | `#waterToggle` (`:146`) | Kun synlig for Fields + Oil & Gas (`:817-818`) |
| Valg | `#selectFab` → bottom sheet (`:178`, `:184-198`) | Søk, «Select all», «Clear all» |
| Eksport | `#downloadBtn` (`:166`) | Excel (.xlsx) |

### De fem visningene

| Modus | Fane | Funksjon | Hva som tegnes |
|---|---|---|---|
| Fields | Oil & Gas | `drawFieldsOilGas` (`:1076`) | Stablet Oil/Gas (+Water). Ett felt: 1 desimal. Flere felt: **summert**, 0 desimaler |
| Fields | OE per field | `drawFieldsOePerField` (`:1146`) | Én serie per felt, brutto o.e. |
| Fields | OE per company | `drawFieldsOePerCompany` (`:1170`) | Én serie per selskap, **equity-justert** |
| Companies | Oil & Gas | `drawCompaniesOilGas` (`:1199`) | Stablet Oil/Gas, equity-justert, **summert over valgte selskaper** |
| Companies | OE per field | `drawCompaniesOePerField` (`:1268`) | Én serie per felt, equity-justert |

Merk at Companies-modus **ikke** har en «OE per company»-fane (`:806-809`) —
det er selskapene man allerede har valgt.

Alle grafer er stablede søylediagram (`stack:'s'`) med delt Y-akse i boe/dag,
felles tooltip med totalsum (`index.html:1330-1350`). Fargepaletten har 10
farger og resirkuleres med modulo (`index.html:254-265`).

Under grafen viser `#infoBox` en tekstlinje som eksplisitt oppgir serier, enhet
og beregningsgrunnlag («Gross field production» vs «Equity-adjusted»).

### Nettverkskall per visning

**Normaltilfellet (datafiler finnes):** nøyaktig **5 GET-kall**, alle ved oppstart,
alle statiske filer (`index.html:227-236`):

```
GET data/fields.json
GET data/production.json
GET data/licensees.json
GET data/reserves.json
GET data/meta.json
```

Alle hentes parallelt med `cache:'no-cache'` (revalidering mot ETag).
**Etter dette gjør ingen brukerhandling noe nettverkskall** — all filtrering,
periodebytte og selskapsvalg skjer i minnet. `prefetchAllFields` avbryter
umiddelbart når bundelen finnes (`index.html:1464`).

**Fallback (datafiler mangler):** appen kaller Sodir-APIet direkte:

| Situasjon | Kall |
|---|---|
| Feltliste | 1 kall mot lag 7100 (`:566-568`) |
| Selskapsliste | 1 `returnCountOnly` + N parallelle sider mot 7108 (`:716-730`) |
| Reserver | `queryAllParallel` mot 7114 (`:772-775`) |
| Produksjon | Batcher à 50 felt-ID-er med `IN (...)` mot 7300 (`:616-636`) |
| Bakgrunn | Etter 2 s: batcher à 30 for alle felt (`:1460`, `:1485`) |

Fallbacken har egen sesjonscache i `sessionStorage` med versjonerte nøkler
(`index.html:320-321`), og faller tilbake til en `Map` i minnet hvis
`sessionStorage` kaster — noe Safari med sporingsvern gjør (`index.html:323-341`).

### Excel-eksport

`downloadExcel` (`index.html:1365-1411`) genererer i nettleseren en `.xlsx` med
to ark: **Info** (genereringstidspunkt, periodetype, årsintervall,
databeskrivelse, kildehenvisning) og **Data** (én kolonne per serie, sebrastriper).
Filnavn: `NCS_Production_<Periode>_<fra>-<til>.xlsx`.

Eksporten hadde en forskyvningsfeil som er rettet — se §11.0.

---

## 10. Datavolum

Målt 2026-09-14 på committet innhold.

### Rader

| Datasett | Rader | Detalj |
|---|---|---|
| `fields.json` | 142 | felt |
| `production.json` | **32 220** | fordelt på **186** carriers |
| — månedsrader | 28 348 | `month` 1–12 |
| — årsrader | 3 872 | `month = 0` |
| `licensees.json` | **10 090** | 263 selskaper × 142 felt |
| — aktive nå | 436 | `to = null` |
| `reserves.json` | 22 | selskaper (aggregert fra 417 kilderader) |

Rader per carrier: min 1, median 130, maks 718.
90 % av produksjonsradene har `oe > 0`; 3 181 rader er rene nullrader.

### Historikkens lengde

- Første månedsrad: **1971-06**
- Siste måned med produksjon: **2026-06** → ca. **2,5 måneders etterslep** fra
  kilden til i dag
- Nominelt årsspenn i fila: **1970–2100**

De framtidige årene er ikke prognoser. Verifisert mot kilden: rader for
2027–2100 er **nullrader** (`prfPrdOeNetMillSm3 = 0`, `month = 0`), f.eks. felt
`EIRIN` med tomme årsrader helt til 2100. De filtreres i praksis bort fordi
`yearTo` maksimalt er inneværende år, og `isCompletePeriod` uansett utelukker
år ≥ inneværende.

### Filstørrelser

| Fil | Rå | Gzip |
|---|---|---|
| `production.json` | 1 268,0 KB | 416,7 KB |
| `licensees.json` | 648,8 KB | 60,9 KB |
| `fields.json` | 2,8 KB | 1,3 KB |
| `reserves.json` | 0,7 KB | 0,4 KB |
| `meta.json` | 0,1 KB | 0,1 KB |
| **Sum `data/`** | **1 920,4 KB** | **479,5 KB** |
| `index.html` | 83,1 KB | — |

GitHub Pages komprimerer tekst automatisk, så reell overføring ved førstegangs
last er ca. **480 KB** for data pluss selve HTML-fila.

### Kjøretid

Full innsamling mot live kilde, målt to ganger 2026-09-14:

- **Kjøring med skriving: 33 sekunder**
- Kjøring uten endringer (idempotent no-op): 30 sekunder

Hele workflow-kjøringen inkludert checkout og Python-oppsett tok **38 sekunder**
(GitHub Actions run `33444480744`). Tiden domineres av sekvensiell paginering:
ca. 17 sider for produksjon + 6 for lisensiærer + 1 for felt + 1 for reserver,
pluss ett `returnCountOnly`- og ett metadata-kall per lag ≈ 33 HTTP-kall.

Merk at hentescriptet paginerer **sekvensielt** (`fetch_sodir.py:89`), mens
frontendens fallback paginerer **parallelt** (`index.html:397-399`, `720-727`).
Scriptet kunne parallelliseres, men 33 s i en daglig batchjobb gjør det unødvendig.

---

## 11. Fallgruver og kjente problemer

### 11.0 Rettet — historikk

Følgende ble funnet under dokumentasjonsarbeidet og **rettet i samme PR**.
De er beholdt her fordi feilklassene er relevante for søsterløsningene.

| Var | Problem | Fiks |
|---|---|---|
| Excel-eksport | Forskjøvet når ledende nullperioder ble trimmet bort | `lastDatasets` utledes nå av de trimmede seriene inne i `renderChart` |
| Fallback-sti | Lastet ned feltgeometri den aldri brukte | `returnGeometry:'false'` er nå eksplisitt i `queryLayer` |
| `apply-companies-oilgas-fix.yml` | Etterlatt engangs-workflow som refererte en slettet fil | Slettet |
| `labelValue` | Definert, aldri kalt | Slettet |
| `showSpinner(progressMsg)` | Parameter aldri sendt inn | Parameter fjernet |
| `loadReserves` | Ubrukt variabel + villedende kommentar | Ryddet |
| `index.html` | Redigeringsartefakten `← ny linje` var committet inn | Fjernet |
| `README.md` | Tom | Skrevet |

**Detaljer om Excel-feilen**, siden mønsteret er lett å gjenskape:
`renderChart` trimmet `labels` og `datasets` og lagret den trimmede
etiketterekka i `lastLabels`, mens `lastDatasets` var satt av tegnefunksjonen
**før** kallet og fortsatt pekte på de **utrimmede** arrayene. `trimLeadingZeros`
muterer ikke — den returnerer nye arrays — så den opprinnelige referansen forble
utrimmet. `downloadExcel` paret så `lastLabels[i]` med `lastDatasets[…].data[i]`,
og hele eksporten ble forskjøvet N plasser.

```
labels  = ['2020','2021','2022'],  Oil = [0, 5, 7],  Gas = [0, 2, 3]
etter trimming:  lastLabels = ['2021','2022'],  lastDatasets[0].data = [0,5,7]

Excel fikk:     2021 → Oil=0, Gas=0        2022 → Oil=5, Gas=2
Korrekt:        2021 → Oil=5, Gas=2        2022 → Oil=7, Gas=3
```

Grafen på skjermen var riktig — kun nedlastingen var feil, og bare når trimming
faktisk skjedde (typisk årsvisning for felt som startet sent).
**Lærdommen for en reimplementasjon:** det som eksporteres må utledes av nøyaktig
den samme datastrukturen som tegnes, på ett sted.

---

De følgende punktene er **ikke rettet**. De krever produktbeslutninger eller
større refaktorering, og er beskrevet som beslutningsgrunnlag.

### 11.2 «Oil» er egentlig alle væsker

Se §8.1. `oilBoed` ignorerer oljekolonnen og regner `oe − gass`, som inkluderer
NGL og kondensat. Kolonnen `prfPrdOilNetMillSm3` hentes, lagres i
`production.json` og sendes inn i funksjonen — men brukes aldri.

Dette er sannsynligvis **tilsiktet** (for å få stablede søyler som summerer
nøyaktig til totalt o.e.), men det er ikke dokumentert noe sted, og
serieetiketten sier «Oil». Kilden har egne NGL- og kondensatkolonner som ville
gjort en ærlig firedeling mulig.

### 11.3 Hardkodet spesialtilfelle for ett enkelt funn

```js
const TRIAL_PROD_OWNERSHIP = {          // index.html:525-530
  17196400: { 'Aker BP ASA': 80.0, 'OMV (Norge) AS': 20.0 },
};
const TRIAL_PROD_FIELDS = [             // index.html:570-572
  { id: 17196400, name: '16/1-12 TROLDHAUGEN', trialProd: true },
];
```

**Hvorfor:** `16/1-12 Troldhaugen` er et *funn* (DISCOVERY), ikke et felt. Det
har prøveproduksjon i lag 7300, men finnes ikke i lag 7100 og har ingen rader i
lisensiærtabellen 7108. Uten denne hardkodingen ville det verken hatt navn eller
eierandeler i appen.

**Problemet:** eierandelene 80/20 er skrevet inn for hånd, har ingen kilde, og
oppdateres aldri. `getShare` sjekker dette oppslaget *før* lisensiærdataene
(`index.html:545-547`), så hardkodingen vinner alltid. Og som vist i §5 finnes
det **43 andre** funn-carriers som ikke har fått samme behandling — to av dem med
faktisk produksjon (`25288497` = 7220/11-1 Alta, `44576` = 33/9-6 DELTA). De
dukker opp som `Field <id>` uten eierandeler.

Spesialtilfellet er spredt over seks steder: `:528`, `:537`, `:545`, `:573`,
`:658-662`, `:833`, `:1034-1040`, `:1272-1274`, `:1438-1440`.

### 11.4 Selskaper kobles på navnestreng

Se §5, fallgruve B. `cmpNpdidCompany` finnes i kilden, men brukes ikke. Enhver
navneendring splitter historikken. `getShare` feiler stille med `0`.

### 11.5 Faviconen ligger som base64 inne i `index.html`

`index.html` har en ~13 KB base64-kodet PNG på **linje 7** (`apple-touch-icon`).
Det gir to problemer:

1. Den lastes ned på nytt ved hver sidelast i stedet for å caches som egen fil,
   og utgjør ~16 % av HTML-fila.
2. Den gjør fila upraktisk å redigere programmatisk. Ethvert verktøy som må
   reprodusere hele filinnholdet risikerer å korrumpere strengen. Repoets
   historikk inneholder flere engangs-workflows som eksisterte utelukkende for å
   patche `index.html` på en GitHub-runner og dermed unngå å skrive hele fila —
   og én av dem hadde et eget verifiseringssteg som feilet hvis favicon-linja ble
   berørt.

**Ved sammenslåing bør faviconen flyttes til en egen fil.** Ikke rettet her
fordi det endrer hvordan siden refererer ikonet.

### 11.6 Død kode som gjenstår

- **`|| 7`** i `midDate(a.prfYear, a.prfMonth||7)` — i årsvisning ignorerer
  `midDate` månedsargumentet uansett, og i månedsvisning er `prfMonth` alltid
  > 0. Defensiv, men uten effekt. Ikke fjernet: det står i fire tette
  one-linere der risikoen for en skrivefeil er større enn gevinsten.

Annen død kode (`labelValue`, ubrukt `showSpinner`-parameter, ubrukt variabel i
`loadReserves`) er fjernet — se §11.0.

### 11.8 sessionStorage-laget er i praksis dødt

Hele cache-laget (`index.html:318-354`, `:600-606`, `:649`, `:707-712`, `:735`)
kjører aldri så lenge `data/`-filene finnes, fordi bundle-stien returnerer tidlig
(`index.html:590-595`, `:697-704`). `sessionClearProd()` kalles fortsatt ved
årsbytte (`:306`) og periodebytte (`:859`, `:871`), men er en no-op for
korrektheten. Koden er ikke feil — bare uvirksom.

### 11.10 Årsvelgeren overstyres ved periodebytte

Se §8.8. Bytter brukeren periode, mistes et manuelt satt `yearFrom`.
`setPeriod` tømmer i tillegg hele `fieldProdCache` (`:860`, `:872`) — harmløst
når bundelen finnes, men en full refetch i fallback-modus.

### 11.11 Inneværende måned vises ufullstendig

`isCompletePeriod` returnerer `true` for månedlig (`index.html:506`). Er kilden
delvis oppdatert for inneværende måned, vises et kunstig lavt tall som siste
søyle. Årlig og kvartalsvis er beskyttet; månedlig er det ikke.

### 11.12 Avrunding før aggregering

Alle tegnefunksjonene runder med `.toFixed(0)` per periode *før* verdiene havner
i `lastDatasets` og dermed i Excel. Summerer man mange felt, akkumuleres
avrundingsfeil på inntil 0,5 boe/dag per felt per periode. Ubetydelig for visning,
men merkbart hvis noen bruker Excel-eksporten til avstemming.

### 11.13 Én feilende kilde fryser alle fire datasettene

`main` returnerer 1 ved første feilende datasett, før noe skrives
(`fetch_sodir.py:237-243`). Det garanterer innbyrdes konsistens, men betyr at en
vedvarende feil i f.eks. reservelaget (417 rader, kun brukt til sortering)
blokkerer oppdatering av produksjonstallene.

### 11.14 Årsrader for framtidige år

Kilden leverer nullrader helt til år 2100 (se §10). De filtreres bort av
årsintervallet i dag, men en reimplementasjon som utvider `yearTo` eller dropper
`isCompletePeriod` vil få en lang hale av tomme søyler.

### 11.15 Kvartalsregelen bruker unionen av felt

Se §8.3. Et kvartal kan regnes som komplett selv om ingen enkeltfelt har alle tre
månedene. For felt som starter eller stenger midt i et kvartal gir dette et
kvartalstall som ikke er sammenlignbart med nabokvartalene.

### 11.16 Ingen tester, ingen validering, ingen overvåking

Repoet har ingen tester, ingen skjemavalidering av API-svaret, og ingen varsling
hvis den daglige jobben feiler. Feiler den, blir datafilene stille stående på
gammelt innhold; appen fortsetter å fungere og viser bare en gammel dato i
bunnteksten. Eneste synlige signal er et rødt kryss i Actions-fanen.

---

## 12. Lisens og attribusjon

### Kildedata

Data fra Sodir FactPages/FactMaps kan brukes i henhold til **NLOD — Norsk lisens
for offentlige data** (Norwegian Licence for Open Government Data).
Verifisert 2026-09-14 på `https://factpages.sodir.no`:

> «The content on the FactPages may be used in accordance with Norwegian Licence
> for Open Government Data (NLOD). In this context we emphasize that there may be
> limitations with respect to the use of information subject to third [party rights].»

Lisenstekst: `https://data.norge.no/nlod/en`

NLOD tillater fri bruk, viderebruk og kommersiell utnyttelse, mot at kilden
navngis. Lisensen gir ingen garanti for at dataene er korrekte eller komplette.

> **USIKKER:** Koden oppgir konsekvent «NLOD 2.0» (`fetch_sodir.py:37`,
> `index.html:1389`). FactPages' egen forsidetekst nevner NLOD uten
> versjonsnummer. NLOD 2.0 er gjeldende versjon, så påstanden er etter alt å
> dømme riktig, men jeg fant ingen eksplisitt versjonsangivelse hos Sodir som
> bekrefter det. Bør sjekkes mot Sodirs vilkårsside før publisering.

> **Merk forbeholdet om tredjepartsrettigheter** i sitatet over. Det er ikke
> vurdert i denne løsningen.

### Attribusjon som vises i dag

1. **I appen**, nederst på siden (`index.html:176`):
   > [Norwegian Offshore Directorate](https://factpages.sodir.no) – NLOD 2.0 · data per ÅÅÅÅ-MM-DD

   Datoen fylles inn fra `data/meta.json` (`index.html:232-234`).

2. **I Excel-eksporten**, på Info-arket (`index.html:1389`):
   > Norwegian Offshore Directorate (Sodir) FactPages – https://factpages.sodir.no – License: NLOD 2.0

3. **I datafilene**, i `meta.json` (`fetch_sodir.py:37`):
   > Norwegian Offshore Directorate (Sodir) FactPages - https://factpages.sodir.no - NLOD 2.0

### Krav ved sammenslåing

Attribusjonen må følge med de norske dataene inn i den felles applikasjonen.
Siden søsterløsningene henter fra andre myndigheter med egne lisensvilkår, bør
den sammenslåtte løsningen ha attribusjon **per datakilde**, ikke én felles —
og `meta.json`-mønsteret (kilde + genereringsdato lagret sammen med dataene)
egner seg godt til nettopp det.

### Løsningens egen kode

Repoet har **ingen LICENSE-fil**. Opphavsretten ligger dermed hos
repo-eieren (`sheffielddivided`), uten noen bruksrettighet gitt til andre.
Bør avklares før sammenslåing.
