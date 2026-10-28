# SOLUTION-CONTEXT — NCS Insights (Norge)

Teknisk referansedokument skrevet for å kunne slå denne løsningen sammen med
søsterløsninger for andre land. Målet er at datainnsamlingen kan reimplementeres
og datamodellen forstås uten å lese kildekoden.

Alt nedenfor er verifisert mot kildekoden og mot live-APIet 2026-09-14, og
oppdatert fortløpende etter hvert som hub- og reservedimensjonen kom til.
Påstander som ikke lot seg verifisere er markert med **USIKKER:**.

Dokumentert tilstand: `main` etter PR #36. **Samtlige 197
linjereferanser er etterkontrollert maskinelt mot kilden 2026-10-16** — hver
peker på en ikke-tom blokk som inneholder det dokumentet beskriver. Endres
`index.html`, forskyver de seg igjen; referansen til et navngitt symbol er
derfor mer varig enn linjenummeret ved siden av.
NVE-appen som tidligere lå her som `nve.html` er flyttet til et eget repo
(`sheffielddivided/nve`) og er ikke en del av denne løsningen.

---

## 1. Oversikt

**Land:** Norge — norsk kontinentalsokkel (Norwegian Continental Shelf, NCS).

**Myndighet/kilde:** Sokkeldirektoratet (Sodir, engelsk: Norwegian Offshore
Directorate; het tidligere Oljedirektoratet / NPD). All data hentes fra Sodirs
åpne ArcGIS-tjeneste **FactMaps**, som er maskinlesbar speiling av FactPages.

- API-rot: `https://factmaps.sodir.no/api/rest/services/DataService/Data/FeatureServer`
  (`fetch_sodir.py:27`, `index.html:375`)
- Menneskelesbar kilde: `https://factpages.sodir.no`

**Hva løsningen gjør:** Én mobiloptimalisert enkeltsides webapp som viser
produksjonshistorikk på norsk sokkel som stablede søylediagram i **boe/dag**.
Brukeren velger *hub*, *felt*, *funn* eller *selskap*, og en periodeoppløsning
(månedlig / kvartalsvis / årlig). For hub og felt finnes i tillegg en
reservevisning i **mill fat o.e.** som har sin egen årsakse og ignorerer
periodevelgeren, og både felt og funn har en profilvisning som samler alt
Sodir har om den enkelte forekomsten. Data kan lastes ned som Excel.

**Arkitektur i to deler:**

1. **Innsamling** — en Python-jobb (`fetch_sodir.py`) kjører daglig i GitHub
   Actions, henter 16 datasett fra Sodir og committer dem som kompakte
   JSON-filer under `data/` i selve repoet.
2. **Presentasjon** — `index.html` er en helt statisk fil som leser disse
   JSON-filene ved oppstart. Finnes de ikke, faller appen tilbake til å kalle
   Sodir-APIet direkte fra nettleseren (`index.html:819-841`, `837-845`, `903-909`).

Det finnes **ingen backend, ingen database og ingen byggesteg**. Repoet *er*
deployet.

---

## 2. Teknologistack

| Lag | Teknologi | Referanse |
|---|---|---|
| Frontend | Vanilla JS/HTML/CSS i én fil, ingen rammeverk, ingen bundler | `index.html` (3403 linjer, 169 KB) |
| Grafer | Chart.js 4.4.1 via cdnjs | `index.html:11` |
| Excel-eksport | ExcelJS 4.3.0 via cdnjs | `index.html:12` |
| Hub-seeding | `seed_hubs.py` — engangsscript, **ikke** i daglig pipeline |
| Kystlinje | `make_coastline.py` — engangsscript, **ikke** i daglig pipeline |
| Innsamling | Python 3.12 + `requests` (eneste avhengighet) | `fetch_sodir.py:25`, workflow linje 28, 31 |
| Lagring | Statiske JSON-filer i git under `data/` | `fetch_sodir.py:44` |
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
# fordi den fetcher data/*.json med relativ sti (index.html:301, 221)
python3 -m http.server 8000
# -> http://localhost:8000/index.html
```

### Deploy

Push til `main` publiseres automatisk av GitHub Pages. Datafilene oppdateres av
workflowen, som committer direkte til `main` (workflow linje 40-46).

---

## 3. Datakilder

Alle datasettene kommer fra samme ArcGIS FeatureServer. **Ingen autentisering,
ingen API-nøkkel, ingen rate limiting observert.**

| Lag-ID | Navn i tjenesten | Type | Rader (2026-09-14) | Brukes til |
|---|---|---|---|---|
| 7100 | `field` | Feature Layer (polygon) | 142 | Feltliste (id + navn) |
| 7300 | `profiles` | Table | 32 220 | Produksjonstall |
| 7108 | `field_licensee_hst` | Table | 10 090 | Eierandeler over tid |
| 7114 | `field_reserves_company` | Table | 417 | Reserver per selskap |
| 7113 | `field_reserves` | Table | 2 333 | Reserver per felt per år (2004→) |
| 7000 | `discovery` | Feature Layer (polygon) | 649 | Funn: navn, status, funnår, eier, operatør — og omriss til funnprofilen |
| 7001 | `discovery_description` | Table | 18 | Sodirs funnbeskrivelser (9 på engelsk) |
| 7008 | `discovery_reserves` | Table | 105 | Ressursanslag per funn |
| 3007 | `licence_licensee_hst` | Table | 33 321 | Eierandeler i utvinningstillatelser |
| 3304 | `bsns_arr_area_licensee_hst` | Table | 5 609 | Eierandeler i forretningsavtaleområder |
| 6000 | `facility` | Feature Layer (punkt) | 798 på felt | Innretninger — type, funksjoner, fase, posisjon |
| 3000 | `licence` | Feature Layer (polygon) | 540 aktive | Utvinningstillatelser — område, fase, gyldighet, operatør |
| 1200 | `company` | Table | 1 039 | Selskapsfakta — konsern, orgnr., nasjonalitet, roller |

Lag-ID-ene er hardkodet to steder: `fetch_sodir.py:29-41` og `index.html:411-419`.

**Lag 7113 er en tidsserie, ikke et øyeblikksbilde.** `fldVersion` er årstallet
estimatet gjelder for, og tabellen inneholder én rad per felt per årgang tilbake
til 2004. 144 felt er dekket; 111 av dem har minst ti årganger og 66 har hele
serien. Dette er den eneste kilden i løsningen med historikk for *estimater* —
lag 7114 har kun siste vurdering.

**Format:** ArcGIS REST JSON (`f=json`). Svar har formen
`{"features":[{"attributes":{...}}, ...]}`. `maxRecordCount` er 2000 på alle fire
lag — derfor er sidestørrelsen satt til nøyaktig 2000 (`fetch_sodir.py:45`).

**Oppdateringsfrekvens:** Sodir publiserer produksjonstall **månedlig**.
Innsamlingsjobben kjører likevel daglig (se §4). Verifisert: datafilene i repoet
var uendret fra 2026-08-31 til 2026-09-14 — en ny kjøring 2026-09-14 ga
byte-identisk resultat for samtlige datasett.

**Hvordan endringer oppdages:** Løsningen henter **alt** på nytt hver gang og
sammenligner den serialiserte JSON-teksten mot fila på disk
(`fetch_sodir.py:522-537`). Det finnes ingen inkrementell henting.

> **Merk for reimplementasjon:** Kilden *har* tidsstempler for endring som denne
> løsningen ikke bruker: `fldDateUpdated` og `fldDateUpdatedMax` (lag 7100),
> `fldLicenseeDateUpdated` (lag 7108), `cmpDateOffResEstDisplay` (lag 7114),
> `fldDateOffResEstDisplay` (lag 7113).
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
2. **`python fetch_sodir.py`** (linje 34). For hvert av de 16 datasettene
   (`fetch_sodir.py:604-619`), i rekkefølge felt → produksjon → lisensiærer →
   reserver → reserver per felt → funn → funnressurser → funnbeskrivelser →
   funnomriss → lisensandeler → feltfakta → feltbeskrivelser → feltomriss →
   innretninger → utvinningstillatelser → selskaper:
   - **Tell rader:** `returnCountOnly=true` (`fetch_sodir.py:86-90`).
   - **Finn sorteringsnøkkel:** hent lagets `objectIdField` (`fetch_sodir.py:72-83`).
     Dette er viktig — uten stabil sortering kan offset-basert paginering hoppe
     over eller duplisere rader. Feiler dette, fortsetter jobben *usortert* med
     en advarsel (linje 70-72).
   - **Paginer sekvensielt** i sider à 2000 med `resultOffset` og
     `orderByFields=<oid> ASC`, `returnGeometry=false` (`fetch_sodir.py:99-119`).
   - **Transformér** til kompakt form (`build_*`-funksjonene, se §5).
   - **Skriv kun ved endring** (`fetch_sodir.py:522-537`).
3. **Commit kun ved endring** (workflow linje 41-45): `git diff --staged --quiet`
   avgjør om det lages en commit. De fleste dagene blir dette en no-op.
4. **`meta.json` skrives bare hvis minst ett annet datasett endret seg**
   (`fetch_sodir.py:571-575`). Dette er bevisst: `meta.json` inneholder dagens
   dato, og ville ellers tvunget fram en commit hver eneste dag og dermed
   ødelagt no-op-egenskapen.

### Idempotens

**Ja, verifisert.** To kjøringer etter hverandre mot live kilde:
kjøring 1 skrev alle filer, kjøring 2 rapporterte «uendret» for alle og
skrev ingenting. Utdata er deterministisk fordi:

- radene sorteres eksplisitt før serialisering — hver `build_*` avslutter med
  en `out.sort` eller `sorted()` (`fetch_sodir.py:152`, `202`, `214`, `285`,
  `306`, `330`, `354`, `394`, `412`, `482`, `518`)
- JSON skrives med `sort_keys=True` og faste separatorer (`fetch_sodir.py:220`)
- flyttall rundes til 6 desimaler (`fetch_sodir.py:128`)

Sorteringen av lisensiærer må håndtere `None` eksplisitt, siden `None` ikke kan
sammenlignes med `int` i Python 3 (`fetch_sodir.py:201`).

### Feilhåndtering

- **HTTP-nivå:** 4 forsøk med eksponentiell backoff 2/4/8 s, timeout 120 s per
  request (`fetch_sodir.py:44-46`, `41-58`). ArcGIS returnerer feil som HTTP 200
  med `{"error": ...}` i kroppen — dette fanges eksplisitt (linje 49-50).
- **Datasettnivå:** Feiler ett datasett etter alle retries, **avbrytes hele
  jobben med exit 1 uten å skrive noe** (`fetch_sodir.py:563-568`). Repoet
  beholder da forrige gyldige datasett, og appen fortsetter på gamle tall.
  Konsekvensen er at datasettene alltid er innbyrdes konsistente — men også at
  én vedvarende feil fryser *alle* datasett.
- **Frontend:** Feiler henting av en `data/*.json`, returnerer `fetchBundle`
  `null` (`index.html:400-406`), og hver lastefunksjon faller gjennom til
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

142 rader. Sortert på navn (`fetch_sodir.py:151`).
Bygges av `fetch_sodir.py:143-152`.

### `data/production.json` — produksjonstall

```json
{"columns":["year","month","oil","gas","oe","water"],
 "fields":{"26376286":[[2026,5,3.16927,0.109115,3.33904,1.6872], ...]}}
```

Nøkkelen i `fields` er carrier-ID som **streng** (`fetch_sodir.py:167`).
Hver rad er et array i rekkefølgen gitt av `columns`:

| Pos | Kolonne | Type | Kilde | Enhet |
|---|---|---|---|---|
| 0 | `year` | int | `prfYear` | — |
| 1 | `month` | int | `prfMonth` | 1–12, **eller 0 for årssum** |
| 2 | `oil` | float | `prfPrdOilNetMillSm3` | mill Sm³ |
| 3 | `gas` | float | `prfPrdGasNetBillSm3` | mrd Sm³ |
| 4 | `oe` | float | `prfPrdOeNetMillSm3` | mill Sm³ o.e. |
| 5 | `water` | float | `prfPrdProducedWaterInFieldMillS` | mill Sm³ |

Radene sorteres på `(year, month)` per carrier (`fetch_sodir.py:176`).
Bygges av `fetch_sodir.py:155-180`.

> **`month = 0` er ikke en måned — det er årssummen.** Kilden leverer både
> månedsrader og en aggregert årsrad per år i samme tabell. Koden skiller dem i
> `isValidRow` (`index.html:791-793`). Blander man dem, dobbelttelles alt.

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
Bygges av `fetch_sodir.py:183-202`.

### `data/reserves.json` — reserver aggregert per selskap

```json
{"companies":{"Equinor Energy AS":3462.051955,"Petoro AS":2691.322247}}
```

22 selskaper. Verdien er **opprinnelig utvinnbar** oljeekvivalent i mill Sm³ o.e.,
summert over alle felt selskapet har andel i (`fetch_sodir.py:206-214`).
Brukes **kun til å sortere selskapslisten** (`index.html:1046-1055`) — aldri vist.

### `data/reserves_field.json` — reserver per felt per år

```json
{"columns":["year","fldRecoverableOil","fldRecoverableGas","fldRecoverableNGL",
            "fldRecoverableCondensate","fldRecoverableOE","fldRemainingOil",
            "fldRemainingGas","fldRemainingNGL","fldRemainingCondensate",
            "fldRemainingOE","fldInplaceOil","fldInplaceAssLiquid",
            "fldInplaceAssGas","fldInplaceFreeGas"],
 "fields":{"26376286":[[2015, ...], [2016, ...], ...]}}
```

Nøkkelen er `fldNpdidField` som streng — samme identifikator som i
`production.json`. Radene er sortert stigende på år (`fetch_sodir.py:233-254`).

**Ekte rad, Johan Sverdrup 2025:**

```json
[2025, 402.403, 11.951, 4.033, 0.0, 422.017,
       192.901,  4.893, 1.475, 0.0, 200.597,
       601.7, 0.0, 24.477, 0.0]
```

Altså: opprinnelig utvinnbart 402,403 mill Sm³ olje + 11,951 bill Sm³ gass +
4,033 mill **tonn** NGL = 422,017 mill Sm³ o.e., hvorav 200,597 gjenstår.

**Enhetene er ikke ensartede i denne tabellen.** Olje og kondensat er mill Sm³,
gass er bill Sm³, men **NGL er mill tonn — masse, ikke volum**. De fem
kolonnene i en familie kan derfor ikke summeres. `fldRecoverableOE` og
`fldRemainingOE` er Sodirs egne o.e.-summer og er den eneste trygge veien til
et sammenlignbart tall. Dette er dokumentert i koden
(`fetch_sodir.py:217-223`).

**144 felt, 2 333 rader, 187 KB.** Kun `fldRecoverableOE` og `fldRemainingOE`
brukes av frontenden i dag; de tolv andre kolonnene hentes, men vises ikke.

### `data/discoveries.json` — funn

```json
{"columns":["id","name","status","ownerKind","owner","hc","year","field",
            "operator","wellbore","area","inclIn"],
 "rows":[[43814,"1/2-1 Blane","Producing","BUSINESS ARRANGEMENT AREA",
          "BLANE UNIT","OIL",1989,"BLANE",
          "Repsol Norge AS","1/2-1","North sea",""]]}
```

Alle 649 funn, ikke bare de med ressursanslag — statusfordelingen og funnårene
er nyttige i seg selv, og fila er liten (96 KB rå / 16 KB gzip).
`owner` er navnet på utvinningstillatelsen eller forretningsavtaleområdet som
eier funnet, og `ownerKind` sier hvilken av de to.

De fire siste kolonnene kom til med funnprofilen: `operator` (`cmpLongName`),
`wellbore` (`wlbName`, funnbrønnen), `area` (`nmaName`, havområdet) og
`inclIn` (`dscResInclInDiscoveryName`, funnet ressursene eventuelt er bokført
under). De ligger sist, så eldre posisjonsbasert lesing er upåvirket.

### `data/discovery_descriptions.json` — Sodirs funnbeskrivelser

```json
{"columns":["id","heading","text"],
 "rows":[[45002,"Discovery description","7120/12-2 (Alke Sør) is a discovery in the southern Barents Sea, …"]]}
```

Lag 7001, engelsk utgave. **Bare 9 funn har tekst** (18 rader med begge
språk), mot 142 felt i lag 7102. Brukes kun av funnprofilen, som utelater
kortet når funnet mangler beskrivelse.

### `data/discovery_outlines.json` — funnomriss

```json
{"discoveries":{"105682":[[[6.3563,64.733],[6.3489,64.7332],[6.3494,64.7344]]]}}
```

Polygonene fra lag 7000, forenklet som feltomrissene (Douglas-Peucker,
0,001° ≈ 110 m, fire desimaler). **Kun de funnene som har en rad i lag 7008** —
87 av 95 — siden resten ikke vises noe sted i appen. Hele laget ville vært
324 KB mot 53 KB.

De 8 som mangler er alle fra 2024–2025: Sodir publiserer ressursanslaget før
polygonet. Funnprofilen utelater kartkortet for dem.

### `data/discovery_resources.json` — ressursanslag per funn

```json
{"columns":["id","rc","oil","gas","ngl","cond","oe"],
 "rows":[[43886,"5F",0.775,0.489,0.0,0.0,1.264]]}
```

105 rader, 95 funn — et funn kan ha én rad per ressursklasse. Alle er datert
**31.12.2025**; i motsetning til feltreservene (lag 7113, 2004→) finnes ingen
historikk, så funnvisningene er tverrsnitt og ikke tidsserier.

**Ressursklassen er Sodirs egen.** Verifisert mot
[Sodirs ressursklassifiseringssystem](https://www.sodir.no/globalassets/1-sodir/regelverk/tematiske-veiledninger/ressursklassifisering_e.pdf)
(publisert 2001, oppdatert 2016, 2018 og 2024), tabell 2 og 4:

| Kode | Klasse | Gruppe |
|---|---|---|
| RC0 / RC1 / RC2 / RC3 | Produced / In production / Approved for production / Decided for production | Reserver |
| **RC4** | Production in clarification phase | Betingede ressurser |
| **RC5** | Production likely, but not clarified | Betingede ressurser |
| RC6 | Production unlikely | Betingede ressurser |
| **RC7** | Production not evaluated | Betingede ressurser |
| RC8 / RC9 | Prospects / Unmapped resources | Uoppdagede |

Suffikset **F = «First»**, første utbyggingsprosjekt for én eller flere
forekomster; brukes for RC 2, 3, 4, 5 og 7. **A = «Additional»**, tiltak for
økt utvinning. RC6 publiseres uten anslag, noe som forklarer hvorfor ingen av
de 185 «Production is unlikely»-funnene har rader her.

Frontenden bruker **kun 4F, 5F og 7F** (`CONTINGENT_RC`, `index.html:1404`).
De øvrige fem radene i datasettet er klasse `0` (3 stk, produserte volumer i
nedstengte funn), `0+1` (1) og `2F` (1) — produserte eller besluttede volumer
som ikke hører hjemme i en ressursportefølje.

**92 funn har en 4F/5F/7F-rad, men bare 89 vises:** Gjøa Nord, Rhombi og
35/11-30 A står med ressursklasse og null volum i alle kolonner. Summen for de
89 er **3 303,3 mill fat o.e.**

### `data/licence_licensees.json` — eierandeler i lisenser

```json
{"columns":["owner","ownerKind","company","share"],
 "rows":[["BLANE UNIT","BUSINESS ARRANGEMENT AREA","DNO North Sea (UK) Limited",43.49321]]}
```

Lag 3007 og 3304 slått sammen til én tabell, siden et funn peker på ett av dem
via navn + type. **Kun gjeldende rader** (til-dato er null): 1 560 fra 3007 og
191 fra 3304, mot 33 321 + 5 609 med full historikk. Uten historikk i
ressursanslagene har de eldre radene ingen nytte her.

Verifisert: alle 540 gjeldende utvinningstillatelser og alle 55
forretningsavtaleområder har andeler som summerer til nøyaktig 100,00 %.

> Av de 89 funnene som vises mangler **ett** — `6407/8-8 S (Calypso)` — eier i
> kilden helt. Det havner på `Unallocated`-raden, som i reservetabellene.

### `data/facilities.json` — innretninger

```json
{"columns":["id","name","fieldId","kind","functions","phase","startup",
            "waterDepth","operator","surface","lon","lat"],
 "rows":[[271245,"OSEBERG A",43625,"CONDEEP 4 SHAFTS",
          "DRILLING - PROCESSING - QUARTER","IN SERVICE",596937600000,109.0,
          "Equinor Energy AS",1,2.8256,60.4913]]}
```

Lag 6000, **kun anlegg som henger på et felt** (`fclBelongsToKind='FIELD'`):
798 rader, hvorav 796 beholdes. Det er `fclNpdidBelongsTo` som gjør datasettet
hub-relevant — den knytter anlegget til feltet, og dermed til huben.

`surface` er 1 for anlegg over vann (172 stk) og 0 for undervannsrammer og
-strukturer. `functions` er Sodirs egen strengliste, der `PROCESSING` er det
som avgjør om anlegget er et prosessanlegg.

**Landanleggene er ikke med.** Nyhamna, Melkøya, Kårstø og de øvrige har
`fclBelongsToKind='TUF'` — de henger på et transportsystem, ikke på et felt,
og lar seg ikke plassere i en hub uten å gå veien om lag 6100/6200. Det er
derfor seks huber (Brynhild, Gudrun, Ormen Lange, Snøhvit, Trym, Tyrihans)
ikke har noe prosessanlegg i datasettet i det hele tatt, mens seks andre
(Glitne, Heimdal, Knarr, Old Frigg, Varg, Veslefrikk) har ett som er fjernet.

**To rader forkastes:** `16/1-CA TROLDHAUGEN` og `16/1-CB TROLDHAUGEN` har
`fclBelongsToKind='FIELD'`, men `fclNpdidBelongsTo` er null. Uten felt-ID kan
de ikke plasseres. **22 rader mangler koordinat** — stort sett undervanns-
strukturer og anlegg i fase `FUTURE`; de blir med i tabellen, men ikke på
kartet.

### `data/licences.json` — utvinningstillatelser

```json
{"columns":["id","name","area","phase","granted","validTo","operator"],
 "rows":[[20756,"001","North sea","PRODUCTION EXTENDED",-136771200000,
          1898553600000,"Vår Energi ASA"]]}
```

Lag 3000, **kun de aktive**. De 540 aktive lisensene er nøyaktig de som har
gjeldende rettighetshavere i `licence_licensees.json` — kontrollert, differansen
begge veier er tom — så de 1 273 inaktive ville aldri blitt vist.
`cmpLongName` er lisensens operatør.

Koblingen **lisens → felt** ligger ikke her, men i `field_profile.json`, som
fikk to nye kolonner: `ownerKind` og `owner` — utvinningstillatelsen eller
forretningsavtaleområdet som eier feltet, fra de gjeldende radene i lag 7108.
128 av 142 felt har en slik eier; resten er nedstengte uten gjeldende
lisensrader. Koblingen **lisens → funn** finnes allerede i
`discoveries.json` (`ownerKind`/`owner`).

`licences.json` har fire kolonner på slutten som ble lagt til for Licences-modusen:
`round` (lisensieringsrunde, `prlLicensingActivityName`), `areaKm2` (`prlCurrentArea`,
dagens areal), `origKm2` (`prlOriginalArea`) og `partitionFrom`. **Brutto areal** er
lisensens hele `areaKm2`; **netto areal** er den multiplisert med selskapets andel.

### `data/licence_outlines.json` og `data/licence_baa.json` — Licences-modusen

`licence_outlines.json` er `{"licences": {lisensnavn: ringer}}`, forenklet til ~330 m
(lag 3000, kun aktive). `licence_baa.json` er `[lisens, BAA, type]` fra lag 3301
(gjeldende rader). Et felt eller funn som eies av en BAA knyttes til lisensene i den,
slik at en lisens i en unit viser feltet unitet driver. Begge filene lastes lazy av
`loadLicences()`. Modusen har fanene Overview, Company, Transactions, Rounds, Overlap og Map.

### Lisenshistorikk — Transactions, Rounds, Overlap og Map

Fire filer lastes lazy av Licences-fanene. **Alle datoer er dager siden 1970-01-01**
(heltall); appen regner om med `DAY_MS`. Lisenser identifiseres med NPDID.

| Fil | Innhold |
|---|---|
| `licence_all.json` | Kolonnene `id,name,area,round,granted,validTo,active,origKm2,areaKm2,operator` for alle 1 813 lisenser (lag 3000 uten aktiv-filter) |
| `licence_history.json` | `companies` + `shares` `[lisens,selskapsindeks,fra,til,andel]` (lag 3007, 33 321 rader) + `oper` `[lisens,selskapsindeks,fra,til]` (lag 3008) |
| `licence_transfers.json` | `rows` `[dag,lisens,selger,kjøper,andel,type]` — selger/kjøper er indeks i `companies` (−1 = mangler), type indeks i `kinds` |
| `licence_areas.json` | `{lisens-id: [[fra,til,areal km2,ringer]]}` fra lag 3002, forenklet til ~440 m |

**Overdragelser.** Lag 3003 har to rader per overføring (FROM og TO) på samme lisens,
dato og type. `build_licence_transfers` pares dem, størst mot størst, så appen får én
rad per overføring. Typene er Sodirs `prlTransferKind`: `TRANSFER`, `SDFI`,
`WITHDRAWAL FROM THE JOINT VENTURE`, `MERGER/TAKEOVER`, `CHANGE OF COMPANY NAME` og
`UNKNOWN`. Navnebytter er en selskapsendring og ikke handel, så de er av som standard i
Transactions. Forretningsavtaleområder (lag 3305) er ikke med.

**Konsern.** Juridiske enheter skifter navn og slås sammen, så Transactions og Map
følger konsern (`group` i `companies.json`) som standard, med `LIC_GROUP_ALIAS` for
etterfølgere Sodir har som egne grupper (Statoil → Equinor, Det norske → Aker BP). Det
gir et riktig bilde av Aker BP før 2016, men gruppene er ikke perfekte; «Legal entities»
viser rådataene. Overføringer innad i ett konsern er skjult i konsernvisningen.

**Runder.** `prlLicensingActivityName`. «Awarded additionally to round X» regnes med
i X. `TFO<år>` vises som «APA <år>», tall som «N. round». Eierne ved tildeling er
eierradene med tidligst fra-dato; operatøren er første rad i `oper`.

**Arealhistorikk.** Arealet for en periode er summen av delpolygonenes areal. Det
stemmer med `prlCurrentArea` for 527 av 536 aktive; de resterende har overlappende
polygoner for ulike stratigrafiske nivåer. Tidslinjekartet viser en lisens med den
arealversjonen som gjaldt på datoen; før første versjon brukes den første.

### `data/companies.json` — selskapsfakta

```json
{"columns":["id","name","group","orgNumber","nation","activeNcs","licenceOper",
            "licensee","baaOper","baaPartner","tufOper","tufPartner","facilityOper"],
 "rows":[[28544099,"Aker BP ASA","AKER BP","989795848","NO",1,1,1,1,1,1,1,1]]}
```

Hele lag 1200, 1 039 rader. Å filtrere til de 263 selskapene appen kjenner
ville spart 16 KB gzip, men koblet byggeren til lisensiærlaget; her er
enkelhet verdt mer enn byten.

`group` er konsernet selskapet tilhører, og forklarer sammenhenger appen ellers
ikke kan vise — `DONG E&P Norge AS` står som **INEOS**. Rolleflaggene er Sodirs
egne Y/N-kolonner lagret som 0/1. Av de 263: 34 er aktive på sokkelen i dag,
16 er lisensoperatører, 12 opererer faste innretninger. **48 mangler
organisasjonsnummer** (utenlandske enheter) og fire mangler konsern
(Talisman/Repsol-selskaper). Nasjonalitet er `NO` (194) eller `GB` (69).

### `data/hubs.json` — hub-tilhørighet (brukerstyrt masterdata)

```json
{
  "version": 1,
  "updated": "2026-09-24",
  "assignments": {
    "3392471": {"hub": "ALVHEIM", "auto": true, "via": "field"}
  }
}
```

| Nøkkel | Type | Beskrivelse |
|---|---|---|
| nøkkel i `assignments` | string | `fldNpdidField` som streng |
| `hub` | string | Hubens navn — konvensjonelt navnet på vertsfeltet |
| `auto` | bool | `true` = maskinelt forslag som bør kvalitetssikres, `false` = bekreftet av et menneske |
| `via` | string | Hvordan forslaget ble utledet: `facility` eller `field` |

**Et felt som ikke står i fila er sin egen hub.** Det betyr at nye felt fra
Sodir automatisk dukker opp som egne huber, og at fila bare inneholder avvik
(79 av 142 felt per 2026-09-24).

> **Dette er det eneste datasettet som ikke kommer fra Sodir.** Hub-begrepet
> finnes ikke i API-et — verifisert: `facility_belongs_to_rel_hst` (lag 6003)
> peker tilbake til feltets *eget* felt, ikke til verten. Fila er derfor
> lokalt eid masterdata, og `fetch_sodir.py` rører den aldri.
>
> For sammenslåingen betyr det at søsterløsningene trenger sin egen
> ekvivalent. Merk også at dette bryter med «ingen ID-er er laget lokalt»
> under: hubene identifiseres av et *navn* vi selv velger, ikke av en
> kilde-ID.

### `data/meta.json`

```json
{"generated":"2026-08-31","source":"Norwegian Offshore Directorate (Sodir) FactPages - https://factpages.sodir.no - NLOD 2.0"}
```

`generated` vises i bunnteksten på siden (`index.html:327`, `index.html:416-418`).

### Entitetsidentifikasjon — kritisk for sammenslåing

| Entitet | Identifiseres ved | Fra kilden? | Kommentar |
|---|---|---|---|
| Felt | `fldNpdidField` (int) | Ja | Stabil, permanent |
| Produksjonsenhet | `prfNpdidInformationCarrier` (int) | Ja | **Ikke det samme som felt** — se under |
| **Selskap** | **`cmpLongName` (streng)** | Ja | **Ingen ID brukes** — se under |
| Lisens / brønn | — | — | **Ikke modellert i det hele tatt** |

**Ingen ID-er er laget lokalt** for felt, produksjon eller selskap — alt er
kildens egne nøkler. Unntaket er **hub**, som er lokalt definert masterdata
identifisert ved navnestreng (se `data/hubs.json` over).

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
(`index.html:749`, `644`, `894`, `918`). Konsekvenser:

- Skrivemåteendring eller fusjon splitter ett selskap i to i UI-et
- `getShare` returnerer stille `0` ved navnebom (`index.html:773`)
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
- **Lag 7113:** alle kolonner unntatt `fldRecoverableOE`/`fldRemainingOE` hentes
  til `data/reserves_field.json`, men vises ikke — herunder hele *inplace*-familien
  (`fldInplaceOil`, `fldInplaceAssLiquid`, `fldInplaceAssGas`, `fldInplaceFreeGas`),
  som gir utvinningsgrad når den holdes mot utvinnbart. `fldResEstComments` og
  `fldDateOffResEstDisplay` hentes ikke i det hele tatt.
- **Lag 7000:** `dscFactPageUrl`, `dscFactMapUrl`, `wlbFactPageUrl`,
  `fldFactPageUrl` (direktelenker til FactPages), `ngpName` (geografisk
  provinskode som `N2`), `dscDateFromInclInField`, `cmpNpdidCompany`,
  `wlbNpdidWellbore`, `fldNpdidField`, `dscNpdidResInclInDisc`
- **Egne funnlag som ikke brukes:** 7002 `discovery_extends_into` (blokker og
  lisenser funnet strekker seg inn i), 7003 `discovery_licensee_hst` (bare
  selskaps-ID, **ingen andel og ingen dato**, derfor ubrukelig til eierskap),
  7006 `discovery_operator_hst` (operatørhistorikk — nåværende operatør ligger
  allerede i lag 7000), 7007 `discovery_owner_hst` (eierhistorikk)

> **Advarsel om lag 7114 ved aggregering:** `cmpShare` summerer **ikke** til 100 %
> for felt som deles med britisk sokkel — kun den norske andelen er med. Verifisert
> live 2026-09-25: STATFJORD 85,47, UTGARD 62,00, ENOCH 20,00, ISLAY 5,51,
> TOMMELITEN A 99,57. Aggregerer man feltnivå fra selskapstabellen uten å vite
> dette, blir feltene for små.

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
const BBL_PER_SM3 = 6.29;   // index.html:385
```

Hardkodet ett sted, i `index.html`. Brukes i alle fire konverteringsfunksjoner
(`index.html:635-642`). Hentescriptet gjør **ingen** enhetskonvertering — det
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

- Månedlig: `daysInMonth(år, måned)` via `new Date(y,m,0).getDate()` (`index.html:523`)
- Årlig: `daysInYear` med korrekt skuddårsregel inkl. 100/400-unntak (`index.html:524`)
- Kvartalsvis: sum av dagene i kvartalets tre måneder (`index.html:648-651`)

Faller en rad bort, brukes **30 dager** som nøddivisor
(`index.html:1945`, `1370`, `1374`, `1378`, `1405`). Verdien er vilkårlig, men
treffer bare rader som uansett er tomme.

### Avrunding

- Innsamling: 6 desimaler (`fetch_sodir.py:128`)
- Enkeltfelt-visning: 1 desimal (`.toFixed(1)`, `index.html:1946-1948`)
- Alle andre visninger: 0 desimaler (`.toFixed(0)`)
- Tooltip: `Math.round` (`index.html:4711`)

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

Hentescriptet setter eksplisitt `returnGeometry: 'false'` (`fetch_sodir.py:105`),
og ingen av JSON-filene under `data/` inneholder koordinater.

> **Merk:** ArcGIS' standardverdi for `returnGeometry` er `true`. Frontendens
> fallback-sti satte den ikke, og lastet dermed ned polygongeometri den aldri
> brukte når den falt tilbake til live-API mot lag 7100 (verifisert live: ett
> felt returnerte en ring med 258 punkter). `queryLayer` setter den nå eksplisitt
> til `'false'` (`index.html:565-577`). Tabellagene var uansett upåvirket, siden
> de ikke har geometri.

---

## 8. Forretningslogikk

### 8.1 «Oil» i grafen er ikke oljekolonnen

Dette er den viktigste ikke-åpenbare regelen i hele løsningen.

```js
function oilBoed(oilMillSm3, gasBillSm3, oeMillSm3, d) {   // index.html:537-541
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
tidspunktet** (`index.html:756-774`):

1. Beregn en referansedato for perioden via `midDate` (`index.html:781-785`):
   - månedlig/kvartalsvis: **den 15. i måneden**
   - årlig: **1. juli** (`Date.UTC(year, 6, 1)` — merk at 6 = juli, 0-indeksert)
2. Finn **første** lisensiærrad der selskapsnavnet matcher og datoen ligger
   innenfor `[from, to]`. `null` tolkes som åpen ende i begge retninger, og
   `to` dekker **hele** den siste dagen, ikke bare midnatt.
3. Del på 100 og multipliser med produksjonen.

Ingen match gir `0` — stille, uten advarsel.

> **Referansedatoen må være UTC.** `fldLicenseeFrom`/`fldLicenseeTo` er
> UTC-midnatt. Ble datoen bygget i lokal tid, ble 1. juli i Norge
> `30. juni 23:00 UTC`, som faller mellom en periode som slutter 30. juni og
> den neste som starter 1. juli. Et eierskifte med virkning 1. juli ga da
> **null allokert produksjon for hele året**, mens feltproduksjonen sto urørt.
> Feilen var usynlig i UTC og rammet kun årlig oppløsning. Rettet i PR #25 —
> se §11.0.

`records.find(...)` tar **første** treff. Finnes overlappende perioder for samme
selskap, vinner den som tilfeldigvis kom først etter sorteringen. Dette er ikke
validert noe sted.

### 8.3 Kvartalsaggregering

Kvartaler bygges kun av månedsrader, aldri av årsrader (`index.html:655-668`).

**Regelen for hvilke kvartaler som tas med er subtil:** et kvartal beholdes hvis
unionen av *alle valgte felt* dekker minst 3 distinkte måneder
(`index.html:667`). Det kreves altså ikke at hvert enkelt felt har alle tre
månedene. Velger man to felt der det ene har jan+feb og det andre har mar, regnes
Q1 som komplett.

Selve verdien er et **dagvektet gjennomsnitt**, ikke et enkelt snitt
(`index.html:673-686`):

```
kvartalsrate = Σ(månedsrate × dager_i_måned) / dager_i_kvartal
```

### 8.4 Filtrering av ufullstendige perioder

`isCompletePeriod` (`index.html:869-884`):

- **Årlig:** inneværende år ekskluderes helt (`+label < nowYear`)
- **Kvartalsvis:** inneværende kvartal ekskluderes til siste måned er passert
- **Månedlig:** returnerer alltid `true` — **inneværende, ufullstendige måned vises**

Asymmetrien er tilsiktet, men udokumentert i koden.

### 8.5 Trimming av ledende nullperioder

`trimLeadingZeros` (`index.html:4608-4616`) fjerner perioder fra starten der
*alle* dataserier er 0 eller mangler. Kalles fra `renderChart`
(`index.html:4747`). Formålet er at grafen skal begynne der produksjonen faktisk
startet. Nullperioder *inne i* eller på slutten av serien beholdes.

### 8.6 Håndtering av manglende verdier

- Innsamling: `None` → `0` for tall (`fetch_sodir.py:123-130`), men
  `None` bevares for datoer (`fetch_sodir.py:133-139`)
- Rader uten `fldNpdidField`/`cmpLongName` forkastes (`fetch_sodir.py:149`, `192`)
- Rader uten `prfYear` forkastes (`fetch_sodir.py:166`)
- Frontend: `|| 0` overalt ved uthenting av måleverdier
- Serier som er null hele veien skjules fra grafen via `hasAnyValue`
  (`index.html:776`, brukt `1407`, `1556`, `1662`, `1709`)
- Felt uten navn vises som `Field <id>` (`index.html:3523`)

### 8.7 Sortering og standardvalg

- **Selskapsliste:** `Aker BP ASA` tvinges alltid øverst, deretter synkende
  reserver, deretter alfabetisk (`index.html:1046-1055`). Kun 22 av 263 selskaper
  har reserver, så resten faller til alfabetisk.
- **Feltliste:** felt der Aker BP er lisensiær sorteres øverst ved oppstart
  (`index.html:4735-4746`).
- **Standardvalg ved oppstart:** modus = `companies`, `Aker BP ASA`
  forhåndsvalgt (`index.html:4748`), `JOHAN SVERDRUP` forhåndsvalgt i feltlisten
  (`index.html:838-839`), og i hub-listen den første huben hvis navn starter med
  `alvheim` (`index.html:825-829`, kalt fra `index.html:4729`). Hub-defaulten
  slås opp på prefiks fordi hub-navn er fritekst og kan endres i
  settings-visningen; finnes ingen treff, står Hubs-viewet tomt.

Dette er ikke nøytral produktlogikk — det er en innebygd preferanse for ett
selskap. Ved sammenslåing bør det parametriseres.

### 8.8 Reserver — utledet produksjon og videreføring av estimater

Reserves-visningen (`index.html:2137-3068`) er den eneste visningen som ikke
bygger på produksjonstidsserien. To utledninger er verdt å kjenne:

**Produsert volum finnes ikke i kilden.** Sodir oppgir opprinnelig utvinnbart og
gjenstående, aldri produsert. Visningen regner `produsert = utvinnbart −
gjenstående`, klemt til minst null (`index.html:2160`). Søylehøyden blir dermed
opprinnelig utvinnbart det året, og splitten viser uttømmingen.

Dette er *ikke* det samme som akkumulert produksjon fra lag 7300, men ligger
nær. Kontrollert for Johan Sverdrup, estimat 2025: 422,017 − 200,597 = 221,42
mill Sm³ o.e., mot 221,93 summert fra månedsradene i `production.json` t.o.m.
2025 — 0,2 % avvik.

**Sodir revurderer ikke alle felt hvert år.** Mangler et felt en årgang,
videreføres feltets forrige estimat i stedet for å telles som null
(`index.html:2087-2135`). Uten dette ville summen over flere felt falle og
sprette tilbake i vilkårlige år uten at noe faktisk hadde endret seg. Før
feltets *første* estimat bidrar det ikke, slik at nye felt kommer inn på det
året de ble estimert. Infoboksen sier fra når videreføring er i bruk.

Konsekvens: velger man flere felt, hopper totalen det året et nytt felt får sitt
første estimat. Det er korrekt oppførsel, men ser ut som en revisjon.

**Estimatserien er ikke revisjonshistorikk for ett fast volum.** Når Sodir
omdefinerer et felt — slår sammen enheter eller flytter en forekomst — endrer
utvinnbart seg uten at noe fysisk har skjedd. USIKKER: jeg har ikke verifisert
et konkret tilfelle av dette i datasettet, kun konstatert at `fldVersion`-serien
inneholder hopp som ikke lar seg forklare av produksjon alene.

**Enhet.** Visningen er den eneste i løsningen som ikke er i boe/dag. Den viser
**millioner fat o.e.**, altså mill Sm³ o.e. × 6,29 uten dagnormalisering
(`index.html:2159-2160`).

### 8.9 Årsvelgerens oppførsel ved periodebytte

`setPeriod` (`index.html:1306-1331`) overstyrer brukerens årsvalg:

- **Til årlig:** setter `yearFrom = 1970`, tømmer cache, henter, og «snapper»
  deretter til første år med faktisk produksjon via `firstYearInCache`
  (`index.html:1284-1304`), som kun ser på årsrader med `oe > 0`.
- **Til månedlig/kvartalsvis:** setter `yearFrom = currentYear - 1`.

Begge veier nullstiller altså et årsintervall brukeren måtte ha satt selv.

### 8.10 Skarvs todelte reserveoppføring i 2018 og 2019

Sodir delte Skarvs volumer på **to oppføringer** i årgangene 2018 og 2019.
Den andre, `fldNpdidField = 33310197`, finnes ikke i noe annet lag og har
dermed ikke noe navn hos Sodir. Den var usynlig i appen — alt feltutvalg går
via `allFields` fra lag 7100 — mens Skarv så ut til å miste en tredjedel av
reservene i to år og så få dem tilbake.

At de to hører sammen er regning, ikke gjetning:

| Årgang | Skarv alene | 33310197 | Sum |
|---|---|---|---|
| 2017 | 109,723 | — | 109,723 |
| 2018 | 60,767 | 48,645 | 109,412 |
| 2019 | 60,245 | 50,817 | 111,062 |
| 2020 | 111,017 | — | 111,017 |

Alle fire fraksjonene faller på plass på samme måte — olje 24,4 → 25,8 →
24,3 → 25,2 og gass 70,3 → 68,9 → 71,5 → 70,6 — og oppføringen finnes i
nøyaktig de to årene Skarv faller. `mergeSplitReserveEntries` slår derfor
radene sammen i `loadFieldReserves`, kolonne for kolonne.

Korreksjonen ligger i frontenden, ikke i innsamlingen: `data/*.json` skal
speile Sodir uendret, slik at en retting fra kilden plukkes opp automatisk
(§4). `SPLIT_RESERVE_ENTRIES` er oppslagstabellen, og den har i dag én
oppføring.

Bare årgangene 2018 og 2019 endres. 2 312 andre feltårganger er kontrollert
uendret, og siste årgang (2025) er urørt, så reserve- og profilvisningenes
nåtall er de samme som før.

---

## 9. Frontend

Én skjerm, ingen ruting, ingen tilstand i URL. Alt er i `index.html`.

### Kontroller (topp til bunn)

| Kontroll | Element | Verdier |
|---|---|---|
| Modusvelger | `#modeBtnHubs` / `#modeBtnFields` / `#modeBtnDiscoveries` / `#modeBtnCompanies` (`:260-263`) | **Companies** (standard) |
| Årsintervall | `#yearFrom` / `#yearTo` (`:269-271`) | 1970–inneværende år, **synkende** (`:512`); standard `[år−1, år]` (`:519-520`) |
| Periode | `#btnMonthly/Quarterly/Annual` (`:274-276`) | **Monthly** standard |
| Undervisning | `#viewTabs` (`:281`) | Avhenger av modus, se under |
| Produsert vann | `#waterToggle` (`:286`) | Kun synlig for Oil & Gas utenom Companies (`:1118-1120`) |
| Valg | `#selectFab` → bottom sheet (`:329`, `:356-370`) | Søk, «Select all», «Clear all» |
| Eksport | `#downloadBtn` (`:317`) | Excel (.xlsx) |
| Nullstill | `.topbar-title` (`:246`) | Sidetittelen er en knapp — `resetView` setter alt tilbake |
| Del visning | `#shareBtn` (`:310`) | Kopierer lenke til gjeldende visning |

### Maksbredde

Fra 1148 px sentreres innholdet i en spalte på **1100 px**
(`index.html:259-269`). Under det gjelder ingenting av det, så mobil- og
nettbrettlayouten er uendret.

Topplinjen og kontrollraden har bakgrunnsfarge som skal gå helt ut til
skjermkanten. De sentreres derfor med padding i stedet for maksbredde. Den
flytende velgerknappen og bunnarket følger spalten, ikke skjermkanten.

Elementene deler seg i to: de som er sin egen boks (`.res-table-wrap`,
`.profile-wrap`, `.info-box`) får maksbredde **1100**, mens de som har 12 px
egen padding (`.chart-wrap`, `.dl-row`, `.source-note`) får **1124**, slik at
innholdet deres lander på de samme 1100. Uten det skillet ble grafkortet
12 px smalere på hver side enn kortene under det.

Regelblokken ligger **sist** i stilarket. Flere av elementene setter sin egen
`margin` lenger nede i filen, og uten den plasseringen ville `margin:auto`
tapt mot dem — en mediaspørring gir ikke høyere spesifisitet.

### Nullstilling

Sidetittelen øverst er en `<button>` som kaller `resetView`
(`index.html:1712-1757`). Den setter modus, faner, periode, årsintervall,
vann-bryteren, hub-grupperingen og alle fire utvalgene tilbake til nøyaktig
den tilstanden en ren sidelasting uten hash gir — inkludert
standardvalgene `Aker BP ASA`, `JOHAN SVERDRUP`, Alvheim-huben og alle funn.
Søkefeltet tømmes, bunnarket lukkes, og produksjonscachen nullstilles fordi
årsintervallet endres og cachen er nøklet på det i fallback-modus.

Adressen tømmes ikke eksplisitt: `refresh` kaller `syncUrl` til slutt og
skriver standardtilstanden dit, så en delt visning man har åpnet forsvinner
også fra adressefeltet.

### Delbar lenke

Hele visningstilstanden ligger i **hash-delen** av adressen, og adressefeltet
oppdateres fortløpende av `syncUrl` (kalt fra `refresh` og ved hver
filterendring). Hash er valgt fordi GitHub Pages serverer samme fil uansett
sti, og fordi hash aldri sendes til serveren.

**Tilbake-knappen.** Navigasjon kaller `markNav()` før endringen, og neste
`syncUrl` bruker da `history.pushState`: bytte av side eller fane, en profil
(også via lenker og radioknappvalg i listen i profilvisningene), et brønn-
eller riggpanel, valgt lisens eller selskap i Licences, og tittelklikket.
Alt annet — periode, filtre, søk, sortering, avkryssing — bruker
`replaceState`, så hvert tastetrykk ikke blir et steg. Standardvalg som settes
etter navigasjonen havner i samme steg. `popstate` leser adressen med
`applyHash` og tegner visningen, som for en delt lenke. Brønn- og
riggpanelet står i adressen på alle sider (`xw`, `xg`); lukkes det med ×,
Close, Escape eller klikk utenfor, går appen ett steg tilbake når panelet ble
åpnet som eget steg, ellers lukkes det på stedet (delt lenke).

```
#m=companies&v=oe_per_hub&p=quarterly&y=2015-2026&c=Aker+BP+ASA%7EA%2FS+Norske+Shell
#m=hubs&v=reserves&h=Alvheim+hub&g=company
#m=fields&v=oilgas&p=annual&y=1970-2026&f=43506&w=1
```

| Nøkkel | Betydning |
|---|---|
| `m` | modus: `fields`, `hubs`, `companies` |
| `v` | undervisning, f.eks. `oilgas`, `oe_per_hub`, `reserves` |
| `p`, `y` | periode og årsintervall — utelates i reservevisningene, som ikke har tidsakse |
| `f` | felt-ID-er, kommaseparert |
| `d` | funn-ID-er, kommaseparert |
| `h`, `c` | hub- og selskapsnavn, skilt med `~` (`SEP`, `index.html:1598`) |
| `w` | produsert vann på |
| `g` | `company` når hub-reservetabellen er selskapsgruppert |

Felt lagres som **ID**, men huber og selskaper som **navn**. Hub-navn er
fritekst og kan endres i settings-visningen; da faller oppføringen bort ved
åpning i stedet for å peke på feil hub. `applyHash` (`index.html:1820-1912`)
validerer alt mot det som faktisk finnes og ignorerer resten, så en gammel
lenke åpner det den fortsatt kan i stedet for å feile. Verifisert for ukjent
modus, ukjent fane, ukjent felt-ID, omdøpt hub og årstall utenfor
1970–inneværende år.

`applyHash` kalles i `init` etter at felt- og hublistene er bygget og
standardvalgene satt, og overstyrer dem. Finnes ingen hash, returnerer den
`false` og standardvalgene står.

Rundturen er stabil: åpnes en lenke og tegnes visningen, er `stateToHash`
(`:1780-1812`) identisk med hashen som ble åpnet.

`copyViewLink` (`:1914-1929`) legger lenken på utklippstavlen. Utklippstavlen
krever sikker kontekst og kan avvises; da vises lenken i knappen i stedet,
slik at den kan merkes manuelt.

### De nitten visningene

| Modus | Fane | Funksjon | Hva som tegnes |
|---|---|---|---|
| Hubs | Prod per field | `drawFieldsOePerField({ids, scope})` | Én serie per medlemsfelt i huben |
| Hubs | Prod per company | `drawFieldsOePerCompany({ids, scope})` | Én serie per selskap over hubens medlemsfelt, **equity-justert** |
| Hubs | Oil & Gas | `drawFieldsOilGas({ids, scope})` | Stablet Oil/Gas summert over hubens medlemsfelt, brutto |

| Hubs | Reserves | `drawFieldsReserves({ids, scope})` (`:2137`) | Reserver over tid, summert over hubens medlemsfelt. Tabell under: per felt og partner |
| Hubs | Profile | `drawHubProfile` | Faktaark for **én** hub: årlig produksjon per medlemsfelt i grafen, og under den nøkkeltall, kart, feltliste, volumer, selskapsandeler, innretningstabell og transportbeskrivelsene |
| Fields | Prod per field | `drawFieldsOePerField` (`:1989`) | Én serie per felt, brutto o.e. |
| Fields | Prod per company | `drawFieldsOePerCompany({ids, scope})` | Én serie per selskap, **equity-justert** |
| Fields | Oil & Gas | `drawFieldsOilGas` (`:1953`) | Stablet Oil/Gas (+Water). Ett felt: 1 desimal. Flere felt: **summert**, 0 desimaler |
| Fields | Reserves | `drawFieldsReserves` (`:2137`) | Stablet gjenstående/produsert per år, **mill fat o.e.** Tabell under: per partner |
| Discoveries | Resources | `drawDiscoveriesPerDiscovery` (`:3990`) | Betingede ressurser per funn, stablet Liquids/Gas. Tabell under: per funn og partner |
| Discoveries | Per company | `drawDiscoveriesPerCompany` (`:4032`) | Samme volumer fordelt på lisensandel, én søyle per selskap. Tabell under: per selskap og funn |
| Discoveries | Maturity | `drawDiscoveriesMaturity` (`:4089`) | Per selskap, stablet på ressursklasse 4F/5F/7F |
| Discoveries | Profile | `drawDiscoveryProfile` (`:3735-3787`) | Faktaark for **ett** funn: nøkkeltall, kart, volumer per ressursklasse, eierskap og Sodirs beskrivelse. Eneste visning **uten graf** |
| Fields | Profile | `drawFieldProfile` (`:2916-2977`) | Faktaark for **ett** felt: årlig produksjon i grafen, og under den nøkkeltall, kart, volumer, eierskap og Sodirs beskrivelse |
| Companies | Prod per hub | `drawCompaniesOePerHub` (`:3940`) | Som Prod per field, men feltene summert opp i huben de tilhører. Seriene sorteres synkende etter total produksjon |
| Companies | Prod per field | `drawCompaniesOePerField` (`:3535`) | Én serie per felt, equity-justert |
| Companies | Oil & Gas | `drawCompaniesOilGas` (`:3402`) | Stablet Oil/Gas, equity-justert, **summert over valgte selskaper** |
| Companies | Reserves | `drawCompaniesReserves` (`:4109`) | **Kun gjenstående** reserver over tid, stablet Liquids/Gas, summert over valgte selskaper, equity-justert. Tabell under: per hub og felt |
| Companies | Operated | `drawCompaniesOperated` | Ett selskap (enkeltvalg). Egenandelsjustert netto OE stablet som operert (felt der selskapet var operatør på datoen, fra `data/field_operators.json`, Sodir lag 7110, bygget av `build_field_operators()`) og partner. Tabell under: feltene i siste periode med rolle, andel og produksjon |
| Companies | Exploration | `drawCompaniesExploration` | Letebrønner per spuddår (grupperte brønner, sidesteg slått sammen), stablet operert/ikke-operert, med funnraten (funnbrønner / avsluttede brønner) som linje på høyre akse. Veksler «Wildcats only» (`cw=1`). Klikk på et år viser brønnene i tabellen under (`cy=`). Eierskap og selskapsnavn er som på spuddatoen |
| Companies | Profile | `drawCompanyProfile` | Faktaark for **ett** selskap: årlig egenandelsjustert produksjon per hub i grafen, og under den nøkkeltall, kart, portefølje gruppert per hub, ressurser og en sorterbar lisenstabell |

**Fire av hub-visningene er feltvisninger med hubens medlemsfelt.**

#### Prices-modusen

Normprisene for olje, tidligere den frittstående siden `normpris.html`, som
nå bare sender videre til `index.html#m=prices`. Data fra
`data/normpris_brent.json` og `data/normpris_diff.json` (bygget av
`fetch_normpris.py` fra regnearkene i `data/normpris-raw/`, egen
arbeidsflyt). Fire faner: **Dated Brent** (daglig eller månedssnitt, `pm=1`),
**Differentials** (månedlige differensialer for opptil åtte felt, med
favoritter lagret i nettleseren under samme nøkkel som den gamle siden),
**Gas** og **Oil vs gas** (under). Den tidligere fanen Heatmap er fjernet;
gamle lenker (`v=heatmap`) åpner Oil vs gas.
Periode (`py=2023-2026`) og feltutvalg (`pf`) deles mellom fanene og ligger
i lenken. Korrelasjonsstudien (`normpris-korrelasjon.html`) er fortsatt en
egen side; lenken tilbake derfra peker på Prices. Gasspriser er ikke med.

**Gas** (`drawPriceGas`) viser månedlige gasspriser fra
`data/gas_prices.json`, hentet av `fetch_gas.py` (eget steg i arbeidsflyten,
`continue-on-error`): norsk eksportpris = SSBs eksportverdi / eksportvolum for
naturgass i gassform (tabell 11008, HS 27112100), IMFs «Natural gas, EU»
(TTF-basert, via FRED PNGASEUUSDM) og Henry Hub (EIA RNGWHHD med
`EIA_API_KEY`, ellers FRED MHHNGSP). Norges Banks månedssnitt for USD/NOK og
EUR/NOK brukes til omregning, og 40 MJ/Sm³ som antatt brennverdi
(`GAS_MJ_PER_SM3`). Enhet velges med `pu` (EUR/MWh standard, USD/MMBtu,
USD/boe, NOK/Sm³). USD/boe bruker Sodirs o.e.-omregning (1 000 Sm³ gass =
1 Sm³ o.e. = 6,29 fat), som resten av appen. Daglige hubpriser (TTF, NBP) er lisensierte og er bevisst ikke med.

**Realpriser** (`pr=1`, knappene Nominal / Real på Dated Brent og Gas):
nominell pris × KPI(siste måned) / KPI(prisens måned), med indeksen for
valutaen som vises: USD bruker amerikansk CPI-U (BLS via FRED CPIAUCSL), EUR
euroområdets HICP (Eurostat `prc_hicp_minr`) og NOK SSBs KPI (tabell 14710).
Basisen er alltid siste publiserte KPI-måned for valutaen (`cpiLast`), så
den flytter seg av seg selv når nye tall kommer; måneder etter den får
faktor 1. Indeksene hentes av `fetch_cpi.py` til `data/cpi.json` (eget steg,
`continue-on-error`). Enkeltmåneder som mangler midt i en serie fylles med
snittet av naboene og merkes i `interpolated` (USD 2025-10, som BLS ikke
publiserte). Realkurven er hovedlinjen, med nominell pris som blek linje.
Differentials er differanser og vises bare nominelt.

**Oil vs gas** (`drawPriceOilGas`): Datert Brent mot gasspris, begge i
USD/boe, én observasjon per måned i perioden. Brent er månedssnittet av den
sammenskjøtte dagsserien (`brentMonthly`), gassen regnes om med
`gasConvert(r, kind, 'usdboe')`. Gassprisen velges med `pg` (norsk
eksportpris standard, `eu`, `hh`), og Real realjusterer begge aksene med
amerikansk KPI. To visninger (`po`): **Scatter** med lineær trendlinje
(minste kvadrater, `olsFit`; ligning og R² i infofeltet), punktene blekere
jo eldre og siste måned i oransje; og **Count grid** (`po=grid`), antall
måneder per kombinasjon i intervaller på 5 USD/boe (`OG_BIN`). De tykke
skillelinjene ligger på intervallgrensen nærmest medianen langs hver akse
og deler månedene i fire kvadranter, som telles i tabellen under. I PPT blir
punktgrafen en ekte graf uten linjer med trendlinjen lagt over som figur
(faste akser og plottområde, `spec.markers`), og rutenettet en tabell med de
tykke linjene som cellekanter.

**Konstruksjonen av Datert Brent og skjøten.** Petroleumsprisrådets Datert
Brent for dato *d* er snittet av de **fem neste publiserte Platts Dated
Brent-noteringene (PCAAS00) etter *d*** — ikke *d* selv — rundet til nærmeste
0,05 $/fat med halve opp. Helger får fredagens verdi, og britiske
helligdager (der Platts ikke publiserer) hoppes over. Regelen gjenskaper
serien eksakt for alle 982 dager i perioden okt. 2023–jun. 2026, kontrollert
mot daglige Platts-data levert av bruker (ikke i repoet, lisensiert).

Serien kommer med flere måneders forsinkelse, så grafen skjøtes med EIAs
Brent spot (RBRTE) fra `data/brent_eia.json`, hentet daglig av
`fetch_eia.py` med GitHub-hemmeligheten `EIA_API_KEY` (uten nøkkel, eller
når EIA ikke svarer: samme serie fra FRED, DCOILBRENTEU). `priceSplice`
bruker samme femdagersregel på EIA-prisene for dagene etter Rådets siste
dato (oransje linje); mot Rådets serie gir det median avvik 0,24 $/fat
(2020–2026), mot 1,34 for rå dagspriser. De siste dagene, som ennå ikke har
fem noteringer etter seg, vises som rå EIA-dagspriser (stiplet hale).

Rådets serie starter 1.1.2011. EIA-serien hentes fra 1990 (EIA har den fra
20.5.1987; 1990 er valgt fordi KPI-seriene starter der), og `priceSplice`
skjøter også bakover med samme regel (`head`), for hver kalenderdag fra første
EIA-notering til 31.12.2010. Kontrollert mot Rådets tall for alle 5 660 dager
2011–2026: median avvik 0,17 $/fat, snitt −0,08 $/fat (ingen systematisk
skjevhet), 95 % innenfor 0,88 $/fat; i månedssnitt 0,18 $/fat. Begge skjøtene
tegnes som samme oransje serie, og de inngår i månedssnittet Oil vs gas
bruker, som dermed går tilbake til 2000 der gassprisene starter.

#### PowerPoint-eksport

«Download PPT» (`downloadPpt`) lager en .pptx med grafen som ekte
PowerPoint-graf med innebygde data, via PptxGenJS 3.12.0 fra jsDelivr (ikke
på cdnjs), som lastes ved første klikk. En nettleser kan bare legge tekst,
HTML og PNG på utklippstavlen, så en innlimbar Office-graf er ikke mulig;
fila er veien dit, og grafen forblir ekte når den kopieres videre.
`pptSpecFromChart` leser Chart.js-instansen som vises: stablede eller
side-om-side søyler, linjer, kombinasjoner med høyreakse (`yAxisID: 'y1'`)
og punktgraf (creaming-kurven, der x-verdiene slås sammen og hull spennes
over). Skriptbare farger (uthevet år, bleke prognoser) får full farge.
Visninger uten Chart.js setter `pptSpec` selv (rangeringene i NCS);
`drawChart` nullstiller den. Knappen følger Excel-knappens tilstand via en
MutationObserver, og er skjult i Exploration og Licences. Lysbildet er 16:9
med tittel, undertittel og kildelinje (Sodir eller SSB etter infofeltet).

**Kurv** (`pptBasket`): «Add to PPT» legger grafen som vises i kurven, og
hvert kort med en SVG-figur (kart, kakediagram, `licYearChart`-grafer,
riggtidslinjen, letekartet) får en «+ PPT»-knapp (`pptDecorate`, lagt på av
en MutationObserver) som legger figuren inn som SVG-bilde. SVG-en
serialiseres med de beregnede CSS-stilene lagt inn (`pptSerializeSvg`, bare
der de avviker fra forelder/standard). PptxGenJS lagrer den som SVG med
PNG-reserve, så PowerPoint 365 viser vektorgrafikk som kan konverteres til
figurer. Kortets forklaring legges på lysbildet som fargeruter med tekst,
og noten som fotnote. Kort med søyler bygd i HTML (lisensoversikten og
tildelinger per selskap i Rounds) har `data-ppt-spec` og en grafbeskrivelse
i `pptCardSpecs`, og «+ PPT» gir der en ekte graf. Overlappsmatrisen blir en
PowerPoint-tabell (`kind: 'table'`) med cellefargene blandet mot hvitt og
skrift som krymper med antall selskaper. Kurven lagres i localStorage og lastes ned som én fil
med ett lysbilde per element, i den rekkefølgen panelet viser.

#### NCS-modusen

Standardvalg: appen åpner på Companies / Prod per hub med Aker BP.
NCS / Production bruker årsoppløsning fra 1970 når man går inn i NCS
(`ncsEnterPeriod`); perioden man hadde ellers settes tilbake når man går
ut, med mindre den er endret i NCS. Discoveries åpner på Per company,
Exploration / Rigs med «MODUs only» på (`xm=0` i lenken slår det av), og
Prices fra året før inneværende år.

Hele sokkelen samlet, uten utvalg (velgerknappen er skjult, `body.ncs`).
Tilstanden ligger i `ncsF` og i lenken (`np`, `nr`, `nw`, `ny`, `nk`, `nc`, `na`, `nf`, `ne`, `ns`).

| Fane | Funksjon | Hva som tegnes |
|---|---|---|
| Production | `drawNcsProduction` | Summen av alle felt, olje/gass(/vann) eller OE per havområde (`np=area`). Én gjennomgang av radene i stedet for oppslag per periode og felt, men samme regler for gyldige rader, komplette perioder og kvartaler som feltvisningene |
| Reserves | `drawNcsReserves` | Gjenstående reserver per årgang, stablet per havområde, veksler Total OE / Liquids / Gas (`nr`). Tabell: per havområde med endring fra året før, og de 15 største feltene |
| Replacement | `drawNcsReplacement` | Tilført = gjenstående(y) − gjenstående(y−1) + produsert(y), mot produsert, med erstatningsgraden som linje. Produksjonen tas fra årsradene i produksjonsbundelen, så fanen krever bundelen |
| Exploration | `drawNcsExploration` | Letebrønner per spuddår: funn (wlbDiscoveryWell), øvrige, tørre, med funnrate og «Wildcats only» (`nw`). Felles graf og tabell med Companies/Exploration (`explYearChart`, `explYearTableHtml`) |
| Companies | `drawNcsCompanies` | Rangering etter gjenstående reserver (siste årgang × dagens lisensandel), eventuelt pluss betingede ressurser i funn (`nk=all`) |
| Fields | `drawNcsFields` | Rangering etter gjennomsnittlig produksjon siste 12 måneder med data, med andel og konsentrasjon (topp 10, antall felt for halve produksjonen). Veksler `nf=timeline`: felt i produksjon og nedstengt per år (produksjonsstart; siste produksjonsår for status «Shut down») med antall producerende felt som linje |
| Water | `drawNcsWater` | Olje og produsert vann per år (bbl/d) og vannkuttet, vann / (vann + olje). Starter ved første år med rapportert vann (2000). Tabell: de største vannprodusentene siste hele år |
| Creaming | `drawNcsCreaming` | Creaming-kurve: samlet funnvolum mot samlet antall wildcats, år for år (`nc=year` gir funnvolum per funnår med wildcats som linje). Volum: feltenes utvinnbare volum på feltets funnår pluss betingede ressurser i funn på funnåret. Total eller per havområde (`na=1`) |
| Investments | `drawNcsInvestments` | Investeringer per år og havområde (nominelt), og forventede framtidige investeringer per felt (faste kroner) |

**Economy** (`drawNcsEconomy`) bruker SSB-data fra `data/ssb_petroleum.json`,
hentet av `fetch_ssb.py` (eget steg i arbeidsflyten, `continue-on-error`).
Tre tabeller fra SSBs åpne PxWeb-API: 07840 (sysselsatte, lønnskostnader,
produksjonsverdi, bearbeidingsverdi og investeringer per næring 06 / 09.1 /
49.500, fra 1965), 04170 (påløpte investeringer per art fra 1985) og 08050
(investeringsundersøkelsens anslag per år og runde). Veksler `ne`:
`employment` (sysselsatte per næring, stiplet linje ved bruddet i
tjenesteserien 2008–2009, og nøkkeltall siste år), `investments` (påløpt per
art, med siste anslag for de kommende årene som blekere søyler) og `survey`
(hvordan anslaget for hvert år utviklet seg gjennom rundene, med underveksler
`ns=acc` for treffsikkerheten: første anslag mot påløpt fra 1986) og `share`
(olje og gass mot industri, kraftforsyning og bergverk, med andelen som
linje). Treffsikkerheten og andelen bruker tabell 07155, som er den samme
undersøkelsen som 08050, men med næringene ved siden av hverandre og
anslag tilbake til 1986 — uten fordeling på investeringsart. Undersøkelsen
fyller ikke-publiserte anslag med 0; de behandles som manglende. Alt i
løpende kroner, kilde SSB (CC BY 4.0).

Havområdet hentes fra feltfaktaene (`ncsArea`); felt uten område havner under
«Other».
`drawFieldsOilGas`, `drawFieldsOePerField`, `drawFieldsOePerCompany` og
`drawFieldsReserves` tar `{ids, scope}`: `ids` overstyrer feltutvalget og
`scope` gir tittel og infoboks hubens navn i stedet for feltlisten. Tallene er
dermed per definisjon de samme som i feltmodus med de samme feltene valgt —
kontrollert for alle 45 hubene i både årlig og månedlig oppløsning.

«OE per company» krever eierandelene, så `refreshHubs` laster dem for den
fanen akkurat som `refreshFields` gjør i feltmodus.

Merk at Companies-modus **ikke** har en «OE per company»-fane — det er
selskapene man allerede har valgt.

Reserves i Companies-modus viser **bare gjenstående**, splittet i væsker og
gass og summert over de valgte selskapene — på linje med Oil & Gas-fanen i
samme modus.

«Liquids» er `fldRemainingOE − fldRemainingGas`, altså olje + NGL +
kondensat. Det er samme definisjon som «Oil» bruker i produksjonsgrafene
(§8.1), og den er nødvendig her fordi NGL oppgis i mill tonn og ikke kan
summeres med volumkolonnene. `fldRemainingGas` er i bill Sm³, som numerisk er
det samme som mill Sm³ o.e. — samme implisitte 1000:1 som §6 beskriver.

Verifisert mot rådata, Johan Sverdrup årgang 2025:
`200,597 − 4,893 − 192,901 − 0,000 = 2,803`, som er bidraget fra de 1,475 mill
tonn NGL. Identiteten holder for alle felt i datasettet.

 Produsert volum er
utledet av feltets samlede historikk (`utvinnbart − gjenstående`) og lar seg
ikke meningsfullt fordeles på dagens lisensiærer — et selskap som kjøpte seg
inn i fjor har ikke produsert feltets historie. Gjenstående er derimot en
beholdning, og kan andelsberegnes.

Eierandelen slås opp per **31. desember i årgangen**, fordi reserveestimatet
gjelder per årsslutt (`index.html:4309`). Et selskap bidrar dermed kun i de
årene det faktisk var lisensiær, og oppkjøp vises som hopp i serien. Verifisert
eksempel: Aker BPs andel i Johan Sverdrup går fra 11,5733 til 31,5733 med
virkning 2023-01-01 i lag 7108, og selskapets gjenstående reserver hopper
tilsvarende fra 778,5 til 1 764,4 mill fat o.e. mellom årgang 2022 og 2023.

Alle tre reservevisningene har en tabell under grafen. Alle viser **kun siste
årgang** og **kun gjenstående** reserver, delt i Liquids / Gas / Total OE, med
grupper og rader sortert synkende etter total OE. De deler byggeren
`reserveTableHtml` (`index.html:4349-4401`):

| Visning | Gruppe / rad | Eierandel | Funksjon |
|---|---|---|---|
| Companies | hub / felt | ja | `renderReserveTableByHub` (`:4405-4445`) |
| Hubs | felt / partner **eller** selskap / felt | ja | `renderReserveTableByFieldPartner` (`:4511-4570`) |

Hubs-tabellen kan grupperes to veier, med en vekslerad over tabellen
(`hubGroupingToggle`, `index.html:4590-4596`). Valget ligger i
`hubReserveGrouping` (`:4581`) og huskes gjennom en ny `refresh`, men ikke
over en sidelasting — det er et lesevalg, ikke en innstilling. Veksleren
tegner tabellen om alene fra `lastHubReserve` (`setHubReserveGrouping`,
`:4584-4588`); grafen røres ikke. Tømmes tabellen, nullstilles også
`lastHubReserve` (`index.html:1856`), slik at en forsvunnet veksler ikke kan
tegne om et gammelt datasett.

**Andelen betyr forskjellige ting på de to nivåene i selskapsgrupperingen.**
På selskapsraden er den selskapets *beregnede* andel av hele hubens
gjenstående reserver — selskapets OE delt på hubens OE. På feltradene under
er den *lisensandelen* i det enkelte feltet. Bildeteksten sier hvilken som er
hvilken. Selskapsandelene summerer derfor til 100 % av huben, mens
lisensandelene summerer til 100 % innenfor hvert felt.

Eksempel, Alvheim hub årgang 2025: Aker BP står på 77,36 % av huben, satt
sammen av 80,00 % i Alvheim, 61,19 % i Tyrving, 80,00 % i Bøyla, 100,00 % i
Volund, 75,76 % i Vilje og 65,00 % i Skogul.
| Fields | partner (flat) | ja | `renderReserveTableByPartner` (`:4579-4604`) |

Alle tre har en **Share**-kolonne (`opts.shareHead`). Andelen settes kun på
radene der den er definert, og står tom ellers — blant annet på totalraden.

I Companies-tabellen betyr andelen to ting, som i hub-tabellens
selskapsgruppering. På **feltraden** er den de valgte selskapenes samlede
lisensandel i det feltet. På **hubraden** er den deres beregnede andel av hele
huben: equity-justert OE delt på hubens brutto gjenstående reserver, der
nevneren summerer over **alle** medlemsfelt — også de selskapene ikke er inne
i. Det er dét som gjør tallet til en eierandel i huben, og ikke et snitt av
lisensandelene (`index.html:4519-4528`).

Eksempel, Aker BP: Yggdrasil-huben står på 64,87 %, satt sammen av 87,70 % i
Hugin, 50,00 % i Munin og 47,70 % i Fulla. Med Equinor i tillegg stiger
hubandelen til 93,09 %.

I Fields-tabellen er partnerne summert over flere felt, og eierandelen er
feltspesifikk. Er andelen den samme i alle feltene partneren bidrar i —
typisk når bare ett felt er valgt — vises den eksakt. Ellers vises et snitt
vektet med partnerens egen gjenstående OE per felt, merket med `~`
(`index.html:4672-4697`). Eksempel, Johan Sverdrup + Troll: Aker BP står
umerket på 31,57 % fordi selskapet bare er inne i Johan Sverdrup, mens
Equinor står `~34,50 %` som vektet snitt av 42,63 % i Johan Sverdrup og 30,55 % i Troll.

Companies-tabellen er equity-justert som grafen. Hubs- og Fields-tabellene
dekomponerer **brutto** feltreserver på lisensiærene, siden de grafene er
brutto. En gruppe hvis eneste underrad gjentar gruppenavnet får ingen
underrad.

`fieldPartners` (`index.html:4300-4316`) slår opp lisensiærene per 31.12 i
årgangen, med samme datologikk som `getShare` — inkludert at to-datoen dekker
hele den siste dagen. Summerer ikke andelene til 100 %, legges resten på en
rad merket `Unallocated (cross-border share)` (`index.html:4444`), slik at
gruppesummen fortsatt stemmer med grafen.

> Med dagens data utløses den raden aldri: alle 107 felt med gjenstående
> reserver i årgang 2025 har lisensandeler som summerer til 100,00 %, også
> grensefeltene — Sodir fører de britiske partnerne eksplisitt (f.eks.
> `Equinor UK Limited` på Statfjord). Raden er en sikring, ikke en observert
> korreksjon. Merk at dette gjelder lag **7108**; andelene i lag **7114** er
> en annen sak, se advarselen i §5.

`renderChart` nullstiller beholderen ved hver tegning (`index.html:4718`);
`showSpinner` og `setChartEmpty` tømmer den også.

### Funntabellen under companies/reserves

`companyDiscoveryTable` (`index.html:4451-4480`) legger en **andre** tabell
under reservetabellen i Companies/Reserves, med de valgte selskapenes
betingede ressurser per funn. Mellom tabellene står en oppsummering:

```
Remaining reserves      1 548,8
Contingent resources      554,6
Total, million boe      2 103,4
```

Oppsummeringen har en eksplisitt merknad om at reserver har produksjonsvedtak
og betingede ressurser ikke har det, og at totalen er en sum og ikke en
prognose. De to tallene er ikke direkte sammenlignbare, og en tabell som
stiller dem ved siden av hverandre uten forbehold ville invitert til nettopp
den sammenligningen.

Merk at tabellene **ikke** følger med i Excel-eksporten — den eksporterer
`lastLabels`/`lastDatasets`, altså seriene i grafen.

> **Løst:** `fldNpdidField = 33310197` i lag 7113 er **Skarv**, delt ut som en
> egen oppføring i årgangene 2018 og 2019. Se §8.10. ID-en finnes ikke i noe
> annet lag — 7100, 7101, 7102, 7108, 7000 og 7103 returnerer alle null treff
> (verifisert live 2026-10-19) — så den har ikke noe navn hos Sodir og var
> usynlig i appen inntil radene ble slått sammen med Skarv.
> Søsterløsningene bør ikke anta at reservelaget og feltlaget dekker samme
> mengde felt.

«OE per hub» i Companies-modus bruker `hubOfField` fra `data/hubs.json`. Felt
uten oppføring er sin egen hub, så ingen produksjon faller utenfor
grupperingen; summen over hub-seriene er lik summen over felt-seriene.

Hubradene i tabellen under grafen har en **Profile-knapp** som hopper til
hubens egen profil (`openHubProfile`). Knappen settes bare når gruppenavnet
faktisk er en hub i `allHubs` — navnet faller tilbake på `Field <id>` for felt
som ikke er i feltlisten, og et slikt navn er ingen hub. Feltradene og
totalraden har ingen knapp.

`periodTableHtml` deles med «Maturity» i funnmodus, der gruppene er selskaper;
knappen krever at gruppen bærer en `hub`, så den tabellen er uberørt.

Hubnavn er fritekst og kan endres i settings-visningen, så navnet escapes både
som JS-streng og som HTML-attributt på vei inn i `onclick`. Testen dekker navn
med apostrof, `&` og vinkelparenteser.

#### Profile-knapper mellom visningene

Fire visninger lar deg hoppe videre til en profil. Tre av dem er lenker på et
navn i en tabell; den fjerde er en knapp i fargeforklaringen, fordi «OE per
company» ikke har noen tabell å legge en lenke i.

| Fra | På | Til | Funksjon |
|---|---|---|---|
| Hub- og selskapsprofilens feltliste | feltnavnet | feltprofilen | `openFieldProfile` |
| Funnvisningenes tabeller | funnnavnet | funnprofilen | `openDiscoveryProfile` |
| companies/oe per hub, tabellen | knapp på hubraden | hubprofilen | `openHubProfile` |
| **«OE per company», fargeforklaringen** | **knapp per selskap** | **selskapsprofilen** | `openCompanyProfile` |
| Reserves-tabellene i Hubs og Fields | felt- og selskapsnavnet | felt- eller selskapsprofilen | `openFieldProfile` / `openCompanyProfile` (via `fieldId`/`company` på radene i `reserveTableHtml`) |

Fanene «OE per …» heter nå «Prod per …» i grensesnittet; id-ene
(`oe_per_field`, `oe_per_company`, `oe_per_hub`) og dermed delte lenker er
uendret. Profilfanene i Fields og Hubs er, som i Discoveries og Companies,
enkeltvalg: listen viser radioknapper, og `syncFieldSelection` /
`syncHubSelection` snevrer inn til én ved fanebytte.

Den siste gjelder både Fields og Hubs, siden de deler
`drawFieldsOePerCompany`. Legendens oppføringer bærer en `company`, og
`renderChart` tegner knappen når den finnes — de øvrige visningenes legender
er felt, huber eller faste serier som «Oil» og «Gas», og er uberørt.

Alle fire bruker `scrollToTop`, og alle navn som havner i et `onclick` går
gjennom `jsStr` for JS og `esc` for HTML. Legendens etiketter escapes nå også,
noe de ikke ble før — 13 selskapsnavn har `&` i seg.

### Profile

Profilvisningen krever **nøyaktig ett** valgt felt; ellers vises en melding om
å velge ett. Den har ingen tidsakse-kontroller — grafen viser alltid hele den
årlige historikken, uavhengig av periodevelgeren, som derfor skjules.

Grafen stopper ved inneværende år. Sodirs årsrader går for enkelte felt helt
til 2100 som nullrader, og etterfølgende tomme år trimmes bort i tillegg.

**Inneværende år skaleres etter månedene som faktisk er publisert.** Årsraden
dekker bare dem, så deling på et helt år ga en kunstig lav siste søyle selv om
tallene er per dag — Ekofisk falt fra 50 470 i 2025 til 26 539 i 2026, mot
45 692 med riktig divisor. Divisoren er summen av dagene i de månedene som
finnes i månedsradene, og etiketten får månedsintervallet: `2026 (Jan–Jul)`.
Infoboksen gjentar det i klartekst.

På skjermer fra 900 px flyter kortene i **to spalter** (`column-count`,
`index.html:118-121`). Et rutenett ble prøvd først, men kortene er svært
ulike i høyde, og rutenettet låste dem parvis og etterlot store tomrom ved
siden av det høyeste i hver rad — profilen ble 1 627 px høy mot 938 px med
spalteflyt. Målt i Chromium på 1400 px. Under 900 px er alt én spalte som før.

Innholdet ligger i `#profileBox` (`index.html:305`), en egen beholder under
`#chartTable`. `setProfile` er eneste vei inn, og den tømmes de samme tre
stedene som tabellen (`renderChart`, `showSpinner`, `setChartEmpty`).

| Del | Kilde | Funksjon |
|---|---|---|
| Nøkkeltall | `data/field_profile.json` | `profileRow` (`:2415-2427`) |
| Kart | `data/field_outlines.json` + `data/coastline.json` | `profileMap` (`:2728-2739`), via `outlineMap` (`:2457-2620`) |
| Volumer | `data/reserves_field.json` | `profileVolumes` (`:2805-2810`) |
| Eierskap | lag 7108 via `fieldPartners` | `profileOwnership` (`:2862-2867`), via `ownershipPie` (`:2815-2860`) |
| Beskrivelse | `data/field_descriptions.json` | `fieldDescriptions` (`:2429-2440`) |

**Produksjonsstart** finnes ikke i feltlaget. Den utledes av fra-datoen til
den første `Producing`-raden i lag 7101 (`fetch_sodir.py:363-399`).
Kontrollert mot Ekofisk, som skal være **15. juni 1971**. 133 av 142 felt har
en slik dato.

**Kartet** er inline SVG uten noe kartbibliotek: kystlinje, nabofelt i grått,
funn i blekgrønt og feltet selv uthevet, i en enkel ekvirektangulær
projeksjon med cosinus-korreksjon for breddegrad. Utsnittet legges rundt
feltet med margin og låses til minst 1,6° bredde, så små felt ikke zoomes inn
til kontekstløshet.

**Etikettene og oversiktskartet bærer stedsfølelsen.** Uten dem var kartet
nesten kontekstløst — 133 av 141 feltkart har ikke kystlinje i utsnittet i det
hele tatt, og åtte har heller ingen nabo i bildet. Nå navngis de største
omrissene i bildet, det uthevede først og med halvfet skrift, så man ser at
man ligger mellom Oseberg og Brage framfor å se grå former, og et
oversiktskart nede til høyre viser hvor på sokkelen utsnittet er. Snittet er
5,7 etiketter per feltkart. Se «Profilkartene» under for reglene.

To ting holder SVG-en nede. Punkter som havner nærmere enn 1–3 piksler fra
forrige punkt droppes, og ringer som dekker under 4 × 4 piksler utelates helt
— kartet er 560 px bredt, så finere detalj er usynlig. Uten det blir Troll
66 KB SVG; med det er snittet 12 KB per profil og verste tilfelle (Vega, ved
den oppstykkede Helgelandskysten) 40 KB.

`SINDRE` mangler geometri i lag 7100 og får derfor ikke kart. Kortet utelates
i stedet for å vises tomt.

**Volumtabellen** har fire kolonner: original in place, original recoverable,
EUR og remaining. Volumene er i mill fat o.e.

**EUR** er utvinningsgraden — original recoverable delt på original in place.
Den vises bare der kilden har begge tallene, så kondensat- og NGL-radene står
tomme. Verdier over 100 % utelates også: Sodir deler væske i reservoaret
mellom `fldInplaceOil` og `fldInplaceAssLiquid`, og på noen gass/kondensat-felt
ligger nesten alt i den andre kolonnen mens det utvinnbare føres som olje.
Vega gir da 293 %. Det gjelder tre av 142 felt — Vega, Tune og Idun Nord.

Verifisert mot uavrundede kildeverdier: alle 374 viste EUR-celler stemmer, og
nøyaktig de tre tilfellene utelates. Ekofisk får 52,2 % totalt, mot Sodirs egen
formulering «now estimated to be over 50 per cent» i feltbeskrivelsen.

Sodir oppgir NGL som **masse** (mill tonn), ikke volum, så det kan ikke
regnes om direkte uten en tetthet. NGL utledes derfor som **restleddet** av
oljeekvivalenten: total o.e. minus olje, gass og kondensat. Da summerer
kolonnen eksakt, og tallet bygger på Sodirs egen omregning framfor en antatt
tetthet. Verifisert: for alle 142 felt og alle tre kolonnene — 426
kolonnesummer — stemmer radene med totalraden, og ingen NGL-verdi blir
negativ. Gass regnes som ellers i appen, 1 bill Sm³ = 1 mill Sm³ o.e.

**Eierskapet** vises som et kakediagram med legende: gjeldende lisensiærer
sortert synkende, med andel og fargeprikk, og operatøren fra lag 7100 merket
med en etikett. Diagrammet er inline SVG uten bibliotek, som kartet.

Operatøren beholder den grønne fargen; de øvrige tar paletten i rekkefølge,
men hopper over palettens egen grønne, som ellers ligger så nær at
operatørmarkeringen mister betydning. En eneste lisensiær gir sammenfallende
start- og sluttpunkt på buen, så da tegnes hele sirkelen i stedet.

126 av 142 felt har gjeldende lisensiærer og dermed diagram; resten er
nedstengte felt der alle lisensradene har en sluttdato.

Fire ting er generiske og deles av profilvisningene: `outlineMap` tegner et
hvilket som helst omrisssett med vilkårlig mange bakgrunnslag, `ownershipPie`
tar en liste `{name, share}`, `volumeTableHtml` summerer én eller flere
reserverader til én tabell, og `annualProduction` bygger den årlige serien
for ett felt.

#### Profilkartene

Alle fire kartene går gjennom `outlineMap`, som tar **navngitte oppføringer**
(`{name, rings}`) framfor løse ringer. Navnene er det som gjør etikettene
mulige, og det er derfor et felt som er medlem av en hub kan merkes for seg
mens nabofeltene tegnes som ett lag.

Lagene er de samme overalt: felt i grått (`#cbd6e2`), funn i blekgrønt
(`#cfe7db`), og den uthevede oppføringen øverst i visningens egen farge.
`fieldLayer` og `discoveryLayer` bygger de to bakgrunnslagene; et lag uten
synlige omriss i utsnittet tegnes ikke i det hele tatt, så 254 av de 296
kartene viser begge.

**Etikettene** navngir de største omrissene i bildet. Uthevede først og med
halvfet skrift, deretter naboene etter størrelse, høyst sju i alt. Alle ville
blitt uleselig: medianen er sju nabofelt i utsnittet og Ekofisk-området har
29. Hver etikett prøver fire plasseringer — over, under, høyre, venstre — før
den gir opp, og en boks som kolliderer med en allerede plassert hoppes over.
Uten alternativene mistet tette områder nettopp de navnene som betyr mest;
Ekofisk-huben klarte ikke å få plass til «EKOFISK» selv.

To detaljer som koster mer enn de ser ut til:

- Boksbredden anslås til 5,8 px per tegn mot 9 px-fonten. Et lavere anslag
  gjør at navn i kanten skjæres, siden klemmingen mot rammen bruker samme tall.
- Koordinatene avrundes **før** kollisjonstesten, ikke etter. Ellers kan to
  bokser som så vidt gikk klar av hverandre overlappe i den tegnede utgaven —
  det traff «URD» og «VERDANDE» på to av kartene.

**Oversiktskartet** svarer på det etikettene ikke gjør: hvor på sokkelen man
er. Et lite kart nede til høyre viser hele sokkelen fra Skottland til
Barentshavet, med et rektangel der hovedkartet står. Rammen er lon −6…31,
lat 55…74,5 — den dekker alle omrissene (lon 1,5–24,5, lat 56,2–73,5) med god
margin, og strekker seg vest til Skottland fordi et nordsjøfelt gir mer mening
med begge sider av havet i bildet.

Tre ting måtte løses:

- **Vindusrektangelet må ha en minstestørrelse.** Et 1,6°-utsnitt blir under
  3 px på et 70 px bredt oversiktskart, altså usynlig nettopp der det trengs
  mest. Minstemålet er 8 × 8 px.
- **Og en maksimalstørrelse.** En selskapsportefølje kan spenne over mer enn
  oversiktskartet viser; da klippes rektangelet til rammen. Uten det stakk det
  26–74 px utenfor for DNO, Harbour Energy og OMV.
- **Etikettene må holde seg unna.** Hjørnet legges inn i `placed` før
  etikettene plasseres, så ingen havner under oversiktskartet. Det koster i
  snitt én etikett per kart — 5,7 mot 6,6 — og er verdt det.

Kystlinjen tegnes mye grovere enn i hovedkartet (punkter under 3 px fra
forrige droppes, ringer under 5 × 5 px utelates): 10 ringer og ~1,1 KB, mot
77 ringer i full detalj. Et nestet `<svg>` klipper innholdet til rammen uten
`clipPath`.

**`preserveAspectRatio` er `meet`, ikke `slice`.** Kortbredden varierer fra
406 px på mobil til 1 100 px i full bredde, og med `slice` beskjæres utsnittet
i den retningen kortet er trangest — vannrett i to spalter, loddrett i full
bredde. Det tok både omriss og etiketter i kanten: «TROLL» lå 43 px utenfor
rammen. `.pf-map` har derfor `aspect-ratio: 560/230` i stedet for fast høyde,
så elementet følger utsnittet nøyaktig og det blir verken beskjæring eller
tomme striper.

### Hubs: Profile

`drawHubProfile` er hubens faktaark og krever **én** valgt hub. Det som gjør
en hub til en hub er det delte prosessanlegget, så innretningene fra lag 6000
bærer visningen: de står på kartet, i en egen tabell, og operatøren deres er
hubens operatør.

**Grafen** viser hele den årlige historikken med én serie per medlemsfelt,
stablet. Den forteller når hver tilknytning kom på — noe periodevelgerens
vindu i «Oil & Gas»-fanen skjuler. Hvert felt skaleres med sin egen divisor
via `annualProduction`, så inneværende år er sammenlignbart.

Årsaksen er en **sammenhengende rekke** fra første til siste år, ikke bare de
årene som har rader. Feltene starter på ulike tidspunkt, så de fleste årene er
uansett nullfylt for noen av dem — og Sodir mangler årsrader helt for Grane
1997–98, det eneste slike hullet blant de 142 feltene. Feltprofilen beholder
sin egen akse og viser derfor fortsatt hoppet 1996 → 1999 for Grane.

| Del | Kilde | Funksjon |
|---|---|---|
| Nøkkeltall | `field_profile` + `facilities` + `hubs` | i `drawHubProfile` |
| Kart | `field_outlines` + `coastline` + `facilities` | `hubMap` |
| Felt | `field_profile` + `reserves_field` | `hubMemberTable` |
| Volumer | `reserves_field` | `hubVolumes` → `volumeTableHtml` |
| Selskaper | lag 7108 via `fieldPartners` | `hubOwnership` → `ownershipPie` |
| Innretninger | `facilities` | `hubFacilityTable` |
| Transport | `field_descriptions` | `hubConnections` |

**Hubens operatør** er den som driver prosessanlegget, ikke en liste over
medlemsfeltenes operatører — 37 av 45 huber har uansett bare én operatør, men
de åtte andre har tilknytninger operert av andre, og det er verten som driver
huben. `hubHosts` plukker prosessanleggene som fortsatt står, sortert etter
oppstart. Tolv huber har ingen, og da faller visningen tilbake på
medlemsfeltenes operatører.

Feltet **Processing** skiller de tre tilfellene: anlegg i drift listes med
navn, en hub der alle prosessanleggene er fjernet får «— removed», og en hub
som aldri hadde noe på feltet får «Off-field — see below», der «below» er
transportkortet.

**Kartet** uthever alle medlemsfeltene samlet, med de øvrige feltene i grått
og funnene i blekgrønt. Hvert medlemsfelt er sin egen oppføring og får sin
egen etikett, så Ekofisk-huben navngir Ekofisk, Eldfisk, Tor, Vest Ekofisk,
Cod, Albuskjell og Tommeliten A.

Kartet hadde innretningene som prikker en periode. De er fjernet igjen —
anleggene står i tabellen under, og prikkene konkurrerte med omrissene om
oppmerksomheten uten å si hvor huben lå.

**Selskapskortet** viser andel av hubens **gjenværende reserver**, ikke
eierskap i huben. Det siste finnes ikke: lisensandelene varierer fra felt til
felt, så en hub har ingen enkelt eierbrøk. 38 av 45 huber får kake; resten er
nedstengte huber uten gjenværende volum.

**Innretningstabellen** viser alle anleggene, med de fjernede nedtonet —
historikken er halve poenget med en hub, og Ekofisk-huben har 32 fjernede av
64. Kortet er det eneste som ligger over begge spaltene (`column-span: all`,
`.pf-wide`); sju kolonner får ikke plass i en halv spalte.

**Transportkortet** gjengir Transport-avsnittene fra Sodirs feltbeskrivelser.
Det er nettopp de avsnittene `seed_hubs.py` leser for å foreslå hub-mapping,
så de forklarer hvorfor feltene henger sammen.

**Feltnavnene i feltlisten lenker til feltprofilen** via `openFieldProfile`,
som bytter både modus og fane. Samme mekanikk som funnlenkene, og begge bruker
`scrollToTop`.

**Hub-tilhørighet er brukerstyrte masterdata**, ikke Sodir-data, så nøkkeltallene
oppgir hvor mange av medlemskapene som fortsatt står som maskinelt foreslått
(`auto: true`). 46 av 109 tilordninger gjør det i dag.

### Discoveries

Funnvisningene bygger på `loadDiscoveries` (`index.html:1580-1660`), som slår
sammen de tre nye datasettene til `allDiscoveries` (kun funn med ikke-null
betingede ressurser, sortert synkende), `discoveryRes` (volumer totalt og per
ressursklasse) og `licenceOwners` (eier → partnere). `discoveryPartners`
(`:1705-1707`) slår opp partnerne via `ownerKind|owner`.

Alle fire fanene er **tverrsnitt uten tidsakse** — lag 7008 har bare én årgang
— så periode- og årsvelgeren skjules i hele modusen (`isReservesView` dekker
nå `topMode === 'discoveries'`). Enheten er mill fat o.e., som i
reservevisningene.

#### Utvalgsreglene

Funnmodusen har **to** utvalgsregler, og `syncDiscoverySelection` håndhever
dem. De tre listevisningene viser hele porteføljen — den samlede ressursbasen
er poenget med dem — mens profilen viser nøyaktig ett funn.

Reglene slår inn ved **fanebytte** (`setSubView`), **modusbytte**
(`switchMode`) og når funnlisten er lastet (`loadDiscoveries`). Innenfor én
fane står utvalget fritt: avkryssingen i listen og «Clear all» virker som før
og overstyres ikke før man bytter fane.

Standardvalget i profilen er **det største funnet**. `allDiscoveries` er
sortert synkende på o.e., så det første treffet blant de valgte er det
største; kommer man fra et delutvalg, velges det største i utvalget, ikke det
største av alle. Et utvalg som allerede er på ett funn beholdes.

Listen i arket blir en **radioknapp** i profilen — `type="radio"` og
`toggleDiscovery` som bytter i stedet for å krysse av — og «Select all» /
«Clear all» skjules, siden de ikke har noen mening når bare ett funn kan
velges. Arktittelen blir «Select a discovery».

`force`-flagget skiller de to anledningene. Ved fanebytte settes det, og
listevisningene får hele porteføljen tilbake. Ved lasting settes det ikke, og
da beholdes et håndplukket utvalg fra en delt lenke — ellers ville en delt
lenke til to funn i «Resources» åpnet med alle 89. En profil-lenke med flere
ID-er snevres derimot alltid inn til den største av dem.

ID-er som ikke lenger har anslag faller bort før reglene kjører.
`loadDiscoveries` kaller `updateFab()` til slutt: funnene er først kjent der,
etter at `updateFab` allerede har kjørt én gang, og uten kallet sto knappen på
«0 selected» mens tabellen viste alle 89.

`discoveryByCompany` (`:3466-3491`) deles av fanene «Per company» og
«Maturity», og legger andelen som ikke er dekket av kjente partnere på
`Unallocated`, slik at totalen stemmer med fanen «Resources».

#### Fanen Profile

`drawDiscoveryProfile` (`:3735-3787`) er funnets faktaark, bygget som
feltprofilen og med samme kortoppsett og tospaltede flyt. Den krever
**nøyaktig ett** valgt funn, og `syncDiscoverySelection` sørger for at det
alltid er det — se utvalgsreglene over. Meldingen om for mange valgte er
derfor en sikring som i praksis ikke vises.

**Visningen har ingen graf.** Et funn har ingen produksjonshistorikk, og en
søyle per ressursklasse ga ingen innsikt volumtabellen ikke gir bedre — 7 av
de 89 funnene har mer enn én klasse, resten bare én. `setChartNone`
(`:2082-2088`) tømmer grafområdet uten å røre tittelen, rydder bort tabellen
fra forrige fane og slår av Excel-knappen, siden det ikke finnes noe datasett
å eksportere. Kortet øverst blir dermed en ren overskrift med funnets navn,
operatør, havområde og status. Ressursklassene står i infoboksen nederst.

| Del | Kilde | Funksjon |
|---|---|---|
| Nøkkeltall | `data/discoveries.json` | direkte fra `allDiscoveries` |
| Kart | `data/discovery_outlines.json` + `field_outlines` + `coastline` | `discoveryMap` (`:3601-3613`) |
| Volumer | `data/discovery_resources.json` via `discoveryRes` | `discoveryVolumes` (`:3615-3652`) |
| Eierskap | `data/licence_licensees.json` via `discoveryPartners` | `discoveryOwnership` (`:3659-3672`) |
| Beskrivelse | `data/discovery_descriptions.json` | `discoveryDescriptions` (`:3590-3596`) |

**Nøkkeltallene** kom til ved å utvide `discoveries.json` med fire kolonner fra
lag 7000: `operator` (`cmpLongName`), `wellbore` (`wlbName`, funnbrønnen),
`area` (`nmaName`, havområdet) og `inclIn` (`dscResInclInDiscoveryName`, funnet
ressursene eventuelt er bokført under). Alle 89 har funnår, brønn og område;
88 har operatør, 9 har `inclIn` og 5 er del av et felt.

**Kartet** bruker samme motor som feltkartet: feltene i grått og de øvrige
funnene i blekgrønt, med funnet selv i fullgrønt øverst og navngitt.
`discovery_outlines.json` inneholder bare de funnene som har et ressursanslag
i lag 7008 — hele lag 7000 ville vært 324 KB mot 53 KB, og de øvrige 554
funnene vises ingen steder i appen.

**8 av de 89 funnene mangler omriss** og får derfor ikke kart: Countach S3,
Elgol, 7220/7-CD-1 H, Omega, Alfa, Sigma NE, Sigma og Goliat Nord. Alle er
fra 2024–2025 — Sodir publiserer polygonet senere enn ressursanslaget. Kortet
utelates i stedet for å vises tomt, som for `SINDRE` i feltprofilen.

**Volumtabellen** har én kolonne per ressursklasse funnet har, pluss en
totalkolonne når det er mer enn én. NGL utledes som restleddet av
oljeekvivalenten, nøyaktig som i feltprofilen — lag 7008 har en egen
`dscRecoverableNGL`, men den er **masse** (mill tonn). Kontrollert for alle
34 anslagene som har NGL: forholdet mellom restleddet og kildens NGL-kolonne
ligger på 1,889–1,909 Sm³ per tonn hele veien, altså en ren
masse/volum-omregning. Ingen restledd blir negativt.

Bare de tre betingede klassene vises, som ellers i funnmodusen. To funn har
i tillegg en ikke-betinget rad — 44648 (`2F`) og 17196400 (`0+1`) — og de
volumene utelates her.

**Eierskapet** er utvinningstillatelsens eller forretningsavtaleområdets
rettighetshavere; funnet har ingen egne andeler hos Sodir. Lag 7003
(`discovery_licensee_hst`) har bare selskaps-ID uten andel eller dato, og er
derfor ikke i bruk. Operatøren (`cmpLongName` fra lag 7000) merkes i legenden
når den også er rettighetshaver.

88 av 89 funn får kakediagram. **6407/8-8 S (Calypso)** har ingen
`dscOwnerName` i kilden og dermed verken operatør eller eierkort.

**Beskrivelsen** er tynn: lag 7001 har bare 9 funn på engelsk, mot 142 felt i
lag 7102. Kortet vises bare for dem som har tekst.

#### Vei inn til profilen

Funnavnene i tabellene under «Resources» og «Per company» er lenker til
profilen. `reserveTableHtml` gjør navnecellen til en lenke når raden bærer en
`discoveryId`; funnvisningen setter den på **gruppene** i «Resources» (der
gruppen er et funn) og på **barna** i «Per company» (der barnet er et funn).
De øvrige kallerne — hub- og feltreservene, og funntabellen under
companies/reserves — sender den ikke, og får ren tekst som før. «Maturity»
har ingen funnrader i det hele tatt.

`openDiscoveryProfile` (`:3725-3733`) setter utvalget til det ene funnet,
bytter fane, tegner om og ruller til topps. Uten rullingen blir man stående
midt i en tabell som er byttet ut under føttene. Adressen oppdateres av
`refresh` → `syncUrl`, så en profil man har hoppet til kan deles videre.

Navnene **escapes** nå i `reserveTableHtml`. Det var nødvendig for lenkene, og
retter samtidig 13 selskapsnavn med `&` i (`DONG E&P Norge AS` og liknende)
som tidligere ble lagt rått inn i HTML-en. Hub-navn er brukerredigerbare og
gikk samme vei.

Reserves-visningen skiller seg ut: X-aksen er reserveestimatets årgang, ikke
periodevelgeren, og Y-aksen er mill fat o.e. i stedet for boe/dag. Periode- og
årsvelgeren skjules derfor mens den er aktiv (`index.html:1097`, via
`isReservesView()` `:2022-2028`). Den er også den eneste visningen som ikke
venter på produksjonsdata (`index.html:1745-1751`, `:1765-1770`, `:1822-1827`).

Alle grafer er stablede søylediagram (`stack:'s'`) med delt Y-akse i boe/dag,
felles tooltip med totalsum (`index.html:4703-4727`). Fargepaletten har 10
farger og resirkuleres med modulo (`index.html:442-454`).

Under grafen viser `#infoBox` en tekstlinje som eksplisitt oppgir serier, enhet
og beregningsgrunnlag («Gross field production» vs «Equity-adjusted»).

### Companies: Profile

`drawCompanyProfile` er selskapets faktaark og krever **ett** valgt selskap.
Visningen handler om **dagens portefølje** — historiske andeler hører til
tidsseriene i de andre fanene, og er bevisst utelatt her.

Enkeltvalget håndheves av `syncCompanySelection`, som er enklere enn
funnmodusens: listen er allerede sortert med Aker BP øverst og deretter
synkende reserver, så det første av de valgte er det største. Listen i arket
blir en radioknapp og masseknappene skjules, akkurat som i funnprofilen.

**Grafen** er egenandelsjustert årsproduksjon over hele historikken, med **én
serie per hub**, stablet og sortert med den største huben først. Andelen slås
opp per år med `getShare`, så oppkjøp og salg vises som hopp — samme logikk
som «OE per hub»-fanen, men med fast årsakse i stedet for periodevelgerens
vindu. 215 av 263 selskaper har produksjon å vise; de 48 øvrige får
`setChartNone` og en profil uten graf.

Grafen bygger på **alle** feltene selskapet har hatt lisensrader i, ikke bare
dagens. Et felt som ble solgt i 2015 bidrar til og med 2015 og ikke lenger,
siden andelen slås opp per år — det er produksjonshistorikken, ikke dagens
portefølje. Kortene under viser derimot bare gjeldende andeler.

> **Merk at årstallene er juridiske, ikke geologiske.** Equinor Energy AS har
> tidligste lisensrad i 2018, fordi det er da selskapet overtok radene etter
> navneskiftet fra Statoil. Grafen starter derfor i 2018, ikke på 1970-tallet.
> Det er korrekt gitt dataene — den juridiske enheten hadde ikke andelene før —
> men det er ikke selskapets historie på sokkelen. Av samme grunn er det ikke
> lagt inn noe «på sokkelen siden»-felt i nøkkeltallene.

| Del | Kilde | Funksjon |
|---|---|---|
| Nøkkeltall | `companies` + lisensradene | i `drawCompanyProfile` |
| Kart | `field_outlines` + `coastline` | `companyMap` |
| Portefølje | lisensradene + `reserves_field` | `companyPortfolio` |
| Ressurser | `reserves_field` + funndataene | `companyDiscoveryTable` |
| Lisenser | `licences` + `licence_licensees` | `licenceTableHtml` |

**Porteføljen** er feltene selskapet er lisensiær i nå — lisensrader uten
sluttdato. Prøveproduksjon har ingen slike rader hos Sodir og legges til fra
`TRIAL_PROD_OWNERSHIP`; det er derfor 30 selskaper får porteføljekort mens
bare 29 har gjeldende lisensrader. Feltnavnene lenker til feltprofilen, og
selskapets egne operatørskap merkes i operatørkolonnen.

Tabellen er **gruppert per hub**, sortert synkende etter hubens delsum.
Hubraden bærer selskapets beregnede eierandel av HELE hubens gjenstående
reserver — selskapets o.e. delt på hubens o.e. over alle medlemsfelt, ikke
bare dem selskapet sitter i. Det er samme definisjon som selskapsgrupperingen
i hubenes reservetabell, og den er merket med `~` fordi den er avledet og
ikke en lisensandel. Feltradene viser derimot lisensandelen i det enkelte
feltet, uten markør. En hub som bare består av ett felt får andelen uten `~`,
siden de to da er samme tall.

**Kartet** skiller seg fra de to andre profilkartene på to punkter, begge
fordi en portefølje kan spenne over hele sokkelen mens et felt eller en hub
ligger samlet. Utsnittet får `pad: 1.25` i stedet for 3 — med 3 ble OKEAs ti
felt usynlige prikker i et bilde av hele Skandinavia. Og det uthevede laget
beholder små ringer (`keepSmall`), som ellers droppes under 4 × 4 piksler;
uten det forsvant halve porteføljen.

Hvert felt selskapet sitter i er sin egen oppføring og får sin egen etikett,
så OKEAs kart navngir Draugen, Statfjord, Gjøa, Nova, Brage, Bestla og
Ivar Aasen langs norskekysten.

**Lisenstabellen** lister alle aktive utvinningstillatelser selskapet har en
andel i — Equinor har 333, medianselskapet 37. Forretningsavtaleområder er
ikke med; kolonnene område, fase og gyldighet finnes bare for
utvinningstillatelser.

Infokolonnen sier hva som ligger i lisensen: feltet den eier først, deretter
funnene i den. Av de 540 aktive lisensene har **69 et felt, 175 funn, og 364
ingen av delene** og står blanke. Alle funn tas med, ikke bare de 89 med
ressursanslag — spørsmålet er hvilke funn som ligger der, ikke hvor store de
er — men bare de med anslag får lenke til funnprofilen. Lange lister kuttes
ved tre med «+N more»; PL 050 har 26 funn.

**Sortering** skjer på klikk i overskriften. `licenceSort` holder
`{key, dir}`, `setLicenceSort` snur retningen når samme kolonne klikkes på
nytt, og tegner om `#licenceBox` alene — resten av profilen røres ikke.
Tomme verdier sorteres sist ved stigende rekkefølge (`\uffff` som
sorteringsnøkkel), slik at de drøyt 360 blanke infocellene ikke legger seg øverst.

Porteføljen, ressurskortet og lisenstabellen ligger over begge spaltene
(`.pf-wide`); fem til sju kolonner får ikke plass i en halv spalte.

Det fantes et **Operatorships**-kort med opererte felt og innretninger. Det er
fjernet igjen — opplysningene er allerede synlige som operatørmerker i
porteføljen og i operatørkolonnen i lisenstabellen.

### Nettverkskall per visning

**Normaltilfellet (datafiler finnes):** nøyaktig **19 GET-kall**, alle ved oppstart,
alle statiske filer (`index.html:408-418`):

```
GET data/fields.json
GET data/production.json
GET data/licensees.json
GET data/reserves.json
GET data/reserves_field.json
GET data/discoveries.json
GET data/discovery_resources.json
GET data/licence_licensees.json
GET data/discovery_descriptions.json
GET data/discovery_outlines.json
GET data/field_profile.json
GET data/field_descriptions.json
GET data/field_outlines.json
GET data/facilities.json
GET data/licences.json
GET data/companies.json
GET data/coastline.json
GET data/meta.json
GET data/hubs.json
```

Alle hentes parallelt med `cache:'no-cache'` (revalidering mot ETag).
**Etter dette gjør ingen brukerhandling noe nettverkskall** — all filtrering,
periodebytte og selskapsvalg skjer i minnet. `prefetchAllFields` avbryter
umiddelbart når bundelen finnes (`index.html:4769`).

**Fallback (datafiler mangler):** appen kaller Sodir-APIet direkte:

| Situasjon | Kall |
|---|---|
| Feltliste | 1 kall mot lag 7100 (`:823-825`) |
| Selskapsliste | 1 `returnCountOnly` + N parallelle sider mot 7108 (`:973-987`) |
| Reserver | `queryAllParallel` mot 7114 (`:1029-1032`) |
| Reserver per felt | 1 kall mot lag 7113 (`index.html:2145`) |
| Produksjon | Batcher à 50 felt-ID-er med `IN (...)` mot 7300 (`:873-893`) |
| Bakgrunn | Etter 2 s: batcher à 30 for alle felt (`:4724`, `:4746`) |

Fallbacken har egen sesjonscache i `sessionStorage` med versjonerte nøkler
(`index.html:528-529`), og faller tilbake til en `Map` i minnet hvis
`sessionStorage` kaster — noe Safari med sporingsvern gjør (`index.html:531-549`).

### Hub-innstillinger — appens eneste skriveoperasjon

Tannhjulet øverst til høyre vises kun i Hub-modus (`index.html`, `renderViewTabs`).
Det åpner et fullskjermspanel som redigerer `data/hubs.json`:

- Feltene er gruppert under huben de tilhører. Hub-navnet er et fritekstfelt —
  endres det, følger alle medlemsfeltene med.
- Hvert felt har et «flytt til»-felt med autofullfør på eksisterende huber. Skriver
  du et nytt navn, opprettes huben.
- Settes en hub lik feltets eget navn, **slettes** oppføringen i stedet for å
  lagres. Feltet blir dermed standalone igjen, og følger automatisk med hvis
  Sodir senere endrer feltnavnet.
- Rader merket `auto` er maskinelle forslag fra `seed_hubs.py`. Enhver manuell
  endring setter `auto: false`.

**Lagring går rett mot GitHubs Contents API fra nettleseren.** Appen har ingen
backend, så dette er den eneste måten en endring kan bli gjeldende for alle
uten å innføre hosting. Flyten er:

1. `GET /repos/{owner}/{repo}/contents/data/hubs.json?ref=main` → blob-sha
2. `PUT` samme sti med base64-kodet innhold og den sha-en

Sha-en hentes rett før skriving og fungerer som optimistisk lås: har noen andre
lagret i mellomtiden, avviser GitHub med 409/422 og brukeren får beskjed om å
laste siden på nytt.

> **Token:** brukeren limer inn et fine-grained GitHub-token med
> `Contents: read and write` første gang hen lagrer. Det lagres i
> `localStorage` på brukerens egen maskin og ligger **aldri** i sidekoden —
> repoet er offentlig. Avvises tokenet (401/403), slettes det og brukeren bes
> om et nytt. Repo-koordinatene er fire konstanter i `index.html`
> (`GH_OWNER`, `GH_REPO`, `GH_BRANCH`, `HUBS_PATH`) som må endres ved
> sammenslåing.
>
> Konsekvensen er at **kun personer med skrivetilgang til repoet kan redigere**.
> Alle andre ser gjeldende gruppering, men får en feilmelding ved lagring.

### Excel-eksport

`downloadExcel` (`index.html:4678-4731`) genererer i nettleseren en `.xlsx` med
to ark: **Info** (genereringstidspunkt, periodetype, årsintervall,
databeskrivelse, kildehenvisning) og **Data** (én kolonne per serie, sebrastriper).
Filnavn: `NCS_Production_<Periode>_<fra>-<til>.xlsx`.

Reserves-visningen eksporterer annerledes, siden periodetype og årsintervall
ikke gjelder der. `renderChart` tar `opts.xTitle`, som lagres i `lastXTitle`
(`index.html:4749`); er den satt, heter fila `NCS_Reserves.xlsx`,
Info-arket utelater periodetype og årsintervall, og første kolonne får
overskriften `Year` i stedet for `Period` (`index.html:4805-4808`,
`:4819-4822`, `:4831`). `opts.allTicks` styrer i tillegg at hver etikett vises
på x-aksen (`index.html:4778`).

Eksporten hadde en forskyvningsfeil som er rettet — se §11.0.

---

## 9b. Exploration (letebrønner)

Femte hovedkategori, `topMode === 'exploration'`. Skiller seg fra de andre
modusene: ingen diagram, ingen utvalgsark, og filtrene ligger i selve
visningen (`#explBar`). Tre faner: **Wells** (liste), **Map** (oversiktskart
som zoomer til filtrert utvalg) og **Rigs** (tidslinje og tabell per rigg).
Et klikk på en brønn åpner en detaljvisning (`#wdOverlay`) med kart, eierskap,
nøkkeldata og lenke til pressemelding.

**Data:** `data/exploration_wells.json`, bygget av `build_exploration_wells()`
i `fetch_sodir.py` fra brønnlaget (5000). Bare formål WILDCAT og APPRAISAL;
'WILL NEVER BE DRILLED' droppes. Filen lastes lazily (`loadExploration`).

**Status utledes av datoer, ikke av `wlbStatus`:** planlagte brønner har
`wlbStatus = NULL`, ingen `wlbEntryDate` og kun `wlbEntryDatePlanned`.
Ingen startdato eller startdato i fremtiden gir *planned*; startet uten
sluttdato gir *drilling*; ellers *completed*. En planlagt dato som er passert
markeres med ⚠. Riggtidslinjen tegner slike brønner likevel (se under), siden Sodir
fortsatt regner dem som planlagte, men Sodir oppdaterer ikke datoen når en brønn utsettes.

**Eierskap** fastsettes per spud-dato fra lag 3007 (lisensiærer med
`ValidFrom`/`ValidTo`), ikke fra dagens lisensiærer, for lisenser overdras.
Ikke-startede brønner får dagens eiere. Unike eierlister ligger i `owners`,
og brønnene peker på dem med indeks. Kobling skjer på `wlbProductionLicence`
(lisens ved brønnhodet). Selskapsfilteret treffer operatør og/eller partnere.

**Funnnavn:** `wlbDiscovery` har formatene «35/8-8 S (Camilla Nord)» og
«16/4-6 S Solveig». `explDiscoveryLabel` henter navnet ut. Funnnavnet erstatter
brønnnavnet for alle brønner som hører til et funn, også avgrensningsbrønner;
brønnnavnet står som undertekst. Bare i sammendragsteksten skilles selve
funnbrønnen (`wlbDiscoveryWellbore = YES`) fra de øvrige.

**Grener grupperes til én brønn:** Sodir regner sidesteg og re-entries («S»,
«A», «R2») som egne brønnbaner. `explGroup` grupperer dem på `wlbWell` (kolonnen
`well`), og resten av visningen jobber med disse gruppene: 2176 brønnbaner blir
1779 brønner. Gruppen har id-en til den eldste grenen, og `explById` slår opp
fra hvilken som helst grens id, så lenker med `xw=` til en side­gren fortsatt
virker. Status er *drilling* hvis en gren bores, *planned* hvis alle er
planlagt, ellers *completed*. Resultatet er det beste blant de avsluttede
grenene (oil/gas > oil > gas > shows > dry), startdato er den eldste og
sluttdato den siste. Detaljvisningen har en tabell over grenene. Riggfanen
bruker brønnbanene, ikke gruppene, siden et sidesteg kan bores av en annen
rigg enn hovedhullet.

**Investeringer per felt:** `data/field_investments.json` (bygget av
`build_field_investments()`) har årlige historiske investeringer per felt fra
årsradene i produksjonsprofilene (lag 7300, `prfInvestmentsMillNOK`, nominelle
MNOK; nullår utelatt) og Sodirs anslag for gjenstående framtidige investeringer
(lag 7107, `fldInvestmentExpected` i faste kroner for `fldInvExpFixYear`).
Selskapsprofilen viser det samme egenandelsjustert, gruppert per hub
(historikk med andelen midt i året, framtidig anslag med dagens andel).
Beløp skrives «NOK 45.8 bn» (`nokBn`).
Felt- og hubprofilen viser dem i kortet «Investments» (`investmentCard`):
søyler per år (for huber stablet per medlemsfelt, med tabell per felt) og det
framtidige anslaget som eget nøkkeltall, ikke i grafen, siden prisgrunnlaget er
et annet.

**Riggfakta:** `data/rig_facts.json` (bygget av `build_rig_facts()`) har
Sodirs innretningsdata for alle flyttbare innretninger og de faste som borer
(boring blant funksjonene, eller navnet står som rigg i brønndataene): type
(`fclKind`), fast/flyttbar, ansvarlig selskap (`fclCurrentRespCompanyName`,
operatøren for faste), fase, flagg og FactPages-lenke. «In service» er første
`fromDate` med fasen `IN SERVICE` i fasehistorikken (lag 6002), siden
`fclStartupDate` er tom for flyttbare innretninger. Riggprofilen viser dem i et
faktakort øverst (`rigFactsCard`), slått opp på riggnavnet uten hensyn til
store og små bokstaver. To spørringer (MOVEABLE og FIXED) i stedet for én med
`OR … LIKE '%…%'`, fordi Sodirs brannmur avviser den siste.

**Utviklingsbrønner i riggoversikten:** `data/rig_wells.json` (bygget av
`build_rig_wells()`) har produksjons- og injeksjonsbrønner fra de siste to årene,
bare med det riggtidslinjen trenger. Jordprøver og observasjonsbrønner er
utelatt. Sodir har *ingen planlagt startdato* for utviklingsbrønner, så de
dukker opp først når boringen har startet, og «Next planned» gjelder fortsatt
bare letebrønner. Sluttdato rapporteres med etterslep (statusen er
`ONLINE/OPERATIONAL` fra første dag), så frontenden (`loadRigWells`) tolker:
uten sluttdato og startet de siste 60 dagene = «mulig pågående» (grønn,
stiplet); uten sluttdato og eldre = tegnet med typisk lengde på 21 dager
(stripet). Faste plattformer (`wlbDrillingFacilityFixedOrMove = FIXED`) er
skjult som standard og slås på med en bryter. Tilstanden ligger i hashen som
`xd=0` (uten utviklingsbrønner) og `xf=1` (med faste plattformer).

**Kampanjer og overlappsregelen (`planRigBars`):** en rigg bor bare én brønn om
gangen, men Sodirs startdato er ofte en forboring — riggen går videre og kommer
tilbake for å fullføre — og mange brønner mangler sluttdato. Tidslinjen skal
ikke ha overlapp, og regelen er per rigg:

1. **Kampanje:** utviklingsbrønner på samme felt, med høyst 30 dager mellom
   dem, er én kampanje, også når den har brønner uten sluttdato eller en som
   bores nå. En kampanje vises som én strek med feltnavnet. En brønn uten felt
   står alene. Har kampanjen en «mulig pågående» brønn, tegnes den blå fram til
   den brønnen starter og grønn stiplet derfra.
2. **Sluttdatoen er master:** letebrønner og kampanjer med rapportert slutt
   sorteres på slutt, og en start som ligger før forrige slutt flyttes til den.
   En kampanje som fortsetter etter en letebrønn i samme område starter
   dermed der letebrønnen sluttet (Munin etter 30/11-16 på Deepsea Stavanger).
3. **Forboring uten rapportert slutt:** en kampanje der ingen brønn har
   sluttdato starter etter aktiviteten den ligger inne i, og slutter der
   neste aktivitet på riggen starter, høyst 60 dager. Uten neste aktivitet
   er lengden 21 dager, men aldri forbi i dag. Den tegnes stripet.
4. **Etterbehandling:** kampanjer på samme felt som nå ligger rett etter
   hverandre (høyst 30 dager) slås sammen, for eksempel en forboring og
   kampanjen den hørte til (Irpa på Transocean Norge).

Regelen kjøres på det fulle settet *før* filtrene, så en brønn som filtreres
bort ikke gir feil starter på resten, og planlagte brønner justeres ikke.
Resultatet er kontrollert uten overlapp på alle rigger (2267 streker).
Detaljvisningen viser kampanjens brønnbaner med Sodirs egne datoer. Det som
gjenstår av overlapp i tegningen er bare klipping mot venstre kant av
vinduet.

**Riggposisjoner (AIS), `fetch_rigs.py`:** to nye faner i Exploration — *Fleet*
(kart og tabell over alle rigger nå) og en *riggprofil* som åpnes ved klikk på
en rigg (Fleet, riggtidslinjen eller riggtabellen). Profilen har kart med spor og
opphold, avspilling (spill/pause, fire hastigheter, tidsslider, periode) og en
liste over oppholdene. `xg=<rigg>` i hashen åpner en profil direkte.

*Kilder, alle åpne uten innlogging og med `access-control-allow-origin: *`:*
Kystdatahuset (`kystdatahuset.kystverket.no/ws/api`, flyttet fra
`kystdatahuset.no`). `tracks/for-ships/by-mmsi` er rask og kompakt (15-minutters
segmenter, bare mens skipet beveger seg), men går bare til siste månedlige
innlasting, i dag flere uker bak. `ais/positions/for-mmsis-time` har alle
meldinger og går ca. to uker bak, men databasen tåler bare vinduer på inntil ca.
6 dager og svarer ellers «53300 too many clients» / «connection pool exhausted»;
manglende dager gir «42P01 relation does not exist». `ais/realtime/geojson` gir
alle skip nå (4 MB, 5 s) inkludert AIS-destinasjon (skrevet av mannskapet og
rotete). BarentsWatch-historikken (siste 14 dager) krever registrering og er
ikke tatt i bruk. Dekningen er norsk økonomisk sone, Svalbard og Jan Mayen.

*Hvorfor statiske filer, ikke oppslag fra nettleseren:* historikken er for treg
og ustabil til live-bruk, og sporene har hull. Den daglige jobben henter derfor
og skriver `rig_tracks.json` (veipunkter `[t, lon, lat]`, opphold, hull; tider er
minutter siden 1970) og `rig_now.json`. Stillestående rigger har få punkter, så
avspillingen interpolerer lineært mellom veipunktene. `rig_log.json` er
arbeidsfila med tynnede posisjoner utover det sporene dekker; steget har et
tidsbudsjett og fortsetter neste kjøring, og feiler det, committes Sodir-dataene
likevel (`continue-on-error`). Hullet mellom siste innlastede dag og i dag fylles
av dagens øyeblikksbilde, så en forflytning i det hullet vises som en rett,
stiplet linje.

*Identifikasjon:* `data/rig_ais.json` kobler Sodirs riggnavn til MMSI/IMO. Navnesøk
i AIS-registeret ga treff på 22 av 24, men tre ga småbåter (Askeladden, Askepott,
Linus; riktig skip har lengde > 80 m og IMO ≠ 0), COSLInnovator har to MMSI, og
Deepsea Bergen og Bollsta er samme skip (`alias`). Fila rettes for hånd.

*Opphold* (`build_rig`): punkter innenfor 1 km av et ankerpunkt i minst 6 timer er et
opphold; oppholdene på samme sted slås sammen når hullet er under 48 timer;
bevegelsen mellom dem forenkles (Douglas-Peucker, 0,003°). Stedsnavn: havn
(innen 3 km, fra Kystdatahusets lokasjonsliste), ellers brønn (1,2 km), ellers
innretning/felt (2,5 km), ellers posisjonen. Tilstanden i Fleet (`rigState`) er
«on location» (opphold på felt eller brønn), «under way» (over 1,5 knop), «at quay»,
«at sea» og «no recent AIS» (siste melding over tre dager gammel).

**AIS som fasit for riggtidslinjen (`aisPlace`, `planRigBars`):** der riggen har
AIS-opphold avgjør oppholdet når en aktivitet startet og sluttet; reglene i
avsnittet over gjelder bare det som ikke lar seg koble til et opphold. Bar med
en tynn mørk linje øverst er AIS-målt.

- *Kobling:* en letebrønn kobles til oppholdet som har brønn-id-en sin i `wells`
  og overlapper i tid (± 5 dager). En utviklingskampanje kobles til opphold på
  feltet med samme navn (normalisert, innen ± 30 dager av kampanjen). Passer
  ikke navnet (Irpa ved Dvalin, Alve ved Norne) tas oppholdet på et felt eller
  en brønn som overlapper mest i tid, minst tre dager. På dagens data koblet 48 av
  50 kampanjer på navn og alle 40 letebrønnene på brønn-id.
- *Fordeling:* oppholdet gir ytre grenser, og Sodir bestemmer rekkefølgen
  inni det (sluttdato sorterer). Første aktivitet starter når riggen kom, siste
  varer til den dro (eller til nå), og de i mellom slutter på sin rapporterte
  sluttdato, begrenset til oppholdet. Ukjent slutt deler resten likt. Første
  opphold i datavinduet kan starte opptil 21 dager før vinduet, ved Sodirs start,
  siden vi ikke vet når riggen kom.
- *Kampanjer:* et opphold på kampanjens felt uten egne brønner (riggen flyttet
  mellom malene) får nærmeste opphold sine brønner. Etterfølgende AIS-biter på
  samme felt slås sammen når det er under 20 dager mellom dem og ingen
  letebrønn, havneanløp eller annet opphold ligger imellom.
- *Bare AIS:* opphold ingen aktivitet forklarer vises som bleke bar (havn fra ett
  døgn, ellers fra tre dager), i hullene mellom de andre. De skjules når et
  filter (selskap, søk, formål) er satt, og kan slås av med «Port calls & other
  stays» (`xo=0`). Klikk på en slik bar åpner riggprofilen og hopper til oppholdet.
- *Prioritet:* AIS-målt går foran regelbasert, regelbasert foran bare AIS. Den
  som taper trimmes, og en aktivitet som havner helt inni en AIS-bar skjules
  (tallet står under grafen). Resultatet er kontrollert uten overlapp på alle rigger.
- *Fallback:* rigger uten AIS, og tid før AIS-vinduet (fra 1. juli 2025), bruker
  de gamle reglene (kampanje, sluttdato er master, forboring til neste aktivitet).

*Mulig videre arbeid:* ankomstsignal for planlagte brønner (riggen ligger ved den
planlagte posisjonen), og å vise Sodirs egne datoer ved siden av de AIS-målte.

**Planlagte brønner med passert dato i riggtidslinjen:** Sodir har ingen brukbar dato for
dem, men rigg, og de står fortsatt som «ikke startet». De tegnes som stiplede ravgule bar
med ⚠, fra det tidligste riggen kan være ledig (etter siste aktivitet, aldri før i dag), i
rekkefølge etter Sodirs gamle dato, 60 dager hver (placeholder), og kortes inn mot en
senere planlagt brønn med gyldig dato så de ikke overlapper. Tooltip og tabellen
(«Next planned») viser Sodirs opprinnelige dato. Tidligere ble de utelatt, og bare
brønner med fremtidig dato vistes.

**Sodir mot AIS (avviksoversikten):** `<details>`-kortet under riggtabellen på
Rigs-fanen. `planRigBars` samler tre slags avvik i `res.disc`: *shifted* (hver
AIS-målt bar, med hvor mange dager AIS flytter start og slutt mot Sodir),
*hidden* (aktiviteter som havner inni et opphold AIS plasserer riggen et annet
sted, og derfor ikke tegnes) og *unmatched* (aktiviteter i AIS-dekningen som ikke
fant noe opphold: hull i AIS, feil kobling i `rig_ais.json`, eller feil dato eller
posisjon i Sodir). Sammendraget viser median forskyvning og antall som avviker en
uke eller mer; tabellen viser de 80 største, konflikter øverst. Filtrene gjelder.

**Endringslogg og fanen News (`changes.py`, `data/changes.json`):** den daglige
jobben sammenligner nye data med forrige fil og skriver hendelser med stabil id
(`spud|<brønn-id>`, `rig|<rigg>|<opphold-start>` osv., så en ny kjøring samme dag
ikke dobler): *plan* (ny planlagt brønn), *spud*, *done*, *discovery* (ferdig funnbrønn
med olje eller gass), *postponed* (planlagt start flyttet minst syv dager),
*cancelled* (planlagt brønn borte fra Sodirs data) og *rig* (nytt opphold,
som ikke er en flytting mellom maler på samme felt). Første gang fylles loggen
ut fra brønnenes egne datoer (siste 60 dager), og for rigger fra de siste
oppholdene. Opphold som dukker opp i tiden før det vi allerede hadde (sporene tar
igjen innlastingen) er ikke nyheter. Loggen beholder 180 dager og høyst 800
hendelser. Klienten setter teksten sammen (`newsLine`), så funnnavn og tittel
følger resten av visningen; klikk åpner brønnen eller riggprofilen ved oppholdet.
Selskapsfilteret treffer brønnhendelser (ikke rigger).

**Driftsrapport for AIS-steget (`health_report` i `fetch_rigs.py`):** hver kjøring
skriver `data/rig_health.json` og, i Actions, en oppsummering (`GITHUB_STEP_SUMMARY`)
og `::warning::`-merknader. Den melder fra om (1) rigger med aktivitet i
Sodir-dataene som mangler kobling i `data/rig_ais.json` (faste plattformer og
oppføringer under `excluded` hoppes over), (2) rigger Sodir sier bores nå, uten
AIS-signal på tre dager, (3) rigger med minst 20 fjernede posisjoner, som tyder på
en delt eller feilregistrert MMSI, og (4) at kilden feiler (teller kjøringer på rad
i `rig_log.json`; tre på rad gir egen advarsel). Fleet-fanen viser det som et kort
nederst når noe skal følges opp. Steget har `continue-on-error`, så advarslene er
den eneste måten en feil blir synlig på.

**Feltbilde i feltprofilen:** `fieldPhoto` hotlinker
`https://factpages.sodir.no/pbl/field_jpgs/{NPDID}_{Navn}.jpg`, med understrek
for mellomrom i navnet. Kontrollert mot alle 142 felt: 80 har bilde. Det finnes
ingen liste over hvilke, så kortet fjernes via `onerror` når bildet mangler.
Bildene er ~1,5 MB og lastes lazy, direkte fra Sodir.

**Pressemelding:** `wlbPressReleaseUrl` peker til sodir.no, som ligger bak
Cloudflare (403 mot skripter), mangler CORS og sender `X-Frame-Options:
SAMEORIGIN`. Den kan derfor verken hentes inn i datafilene eller vises i en
iframe, og detaljvisningen viser en lenke pluss en tekst generert fra Sodirs
strukturerte felt (`explSummaryText`).

**Kryssbruk:** `outlineMap` tar `opts.wells` og `opts.ownWell` for
brønnmarkører. Funn- og selskapsprofilene får et kort med brønnene sine
(`explLinkCard`), og detaljvisningen lenker til funnprofilen og operatøren.
Tilstanden ligger i hashen: `xc` selskap, `xr` rolle, `xs` status, `xz`
resultat, `xp` formål, `xy` fra-år (`all` for alle), `xq` søk, `xw` åpen brønn.

**Kollapsbar tabell i Discoveries / Per company:** `reserveTableHtml` tar
`opts.collapsible`. Hvert selskap er en rad med pil og antall funn, og funnradene
under er skjult til man klikker på selskapet (eller trykker Enter/mellomrom).
«Expand all» og «Collapse all» står over tabellen. Åpne selskaper huskes på
navn i `discOpen`, så de forblir åpne når tabellen tegnes på nytt ved
utvalgsendring; utgangspunktet er alt kollapset. De andre tabellene som deler
funksjonen er uendret.

## 10. Datavolum

Målt 2026-09-14 på committet innhold.

### Rader

| Datasett | Rader | Detalj |
|---|---|---|
| `fields.json` | 142 | felt |
| `coastline.json` | 50,3 KB | 17,8 KB |
| `companies.json` | 76,5 KB | 21,8 KB |
| `discoveries.json` | 95,7 KB | 16,4 KB |
| `discovery_descriptions.json` | 4,6 KB | 1,5 KB |
| `discovery_outlines.json` | 52,9 KB | 17,1 KB |
| `discovery_resources.json` | 4,2 KB | 1,5 KB |
| `exploration_wells.json` | 796,8 KB | 144,4 KB |
| `facilities.json` | 107,9 KB | 19,6 KB |
| `field_descriptions.json` | 213,8 KB | 44,1 KB |
| `field_outlines.json` | 168,9 KB | 53,5 KB |
| `field_profile.json` | 18,3 KB | 3,7 KB |
| `fields.json` | 2,8 KB | 1,3 KB |
| `hubs.json` | 7,7 KB | 1,1 KB |
| `licence_all.json` | 151,3 KB | 27,8 KB |
| `licence_areas.json` | 847,8 KB | 136,8 KB |
| `licence_baa.json` | 5,2 KB | 0,9 KB |
| `licence_history.json` | 1065,8 KB | 177,9 KB |
| `licence_licensees.json` | 96,0 KB | 9,1 KB |
| `licence_outlines.json` | 128,9 KB | 22,8 KB |
| `licence_transfers.json` | 253,0 KB | 51,9 KB |
| `licences.json` | 61,2 KB | 10,6 KB |
| `licensees.json` | 649,5 KB | 61,0 KB |
| `meta.json` | 0,1 KB | 0,1 KB |
| `normpris_brent.json` | 115,4 KB | 21,0 KB |
| `normpris_diff.json` | 44,2 KB | 10,3 KB |
| `production.json` | 1276,6 KB | 419,5 KB |
| `reserves.json` | 0,6 KB | 0,4 KB |
| `reserves_field.json` | 187,3 KB | 53,5 KB |
| `rig_wells.json` | 54,7 KB | 8,4 KB |
| **Sum `data/`** | **6 538,0 KB** | **1 356,0 KB** |
| `index.html` | 374,5 KB | 112,3 KB |

GitHub Pages komprimerer tekst automatisk, så reell overføring ved førstegangs
last er ca. **1,4 MB** for data pluss selve HTML-fila.

Hele tabellen er målt på nytt 2026-10-25, med `gzip -9`. Datasettet vokste
kraftig med Exploration- og Licences-visningene; de fire største filene er
`licence_history`, `licence_areas`, `exploration_wells` og `production`.

### Kjøretid

Full innsamling mot live kilde, målt to ganger 2026-09-14:

- **Kjøring med skriving: 33 sekunder**
- Kjøring uten endringer (idempotent no-op): 30 sekunder

Hele workflow-kjøringen inkludert checkout og Python-oppsett tok **38 sekunder**
(GitHub Actions run `33444480744`). Tiden domineres av sekvensiell paginering:
ca. 17 sider for produksjon + 6 for lisensiærer + 1 for felt + 1 for reserver +
2 for reserver per felt, pluss ett `returnCountOnly`- og ett metadata-kall per
lag ≈ 37 HTTP-kall. USIKKER: kjøretiden på 33 s ble målt før lag 7113 ble lagt
til, og er ikke målt på nytt.

Merk at hentescriptet paginerer **sekvensielt** (`fetch_sodir.py:100`), mens
frontendens fallback paginerer **parallelt** (`index.html:605-607`, `860-867`).
Scriptet kunne parallelliseres, men 33 s i en daglig batchjobb gjør det unødvendig.

---

## 11. Fallgruver og kjente problemer

### 11.0 Rettet — historikk

Følgende ble funnet under dokumentasjonsarbeidet og **er rettet** (PR #12).
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
| `midDate` + `getShare` | Eierskifte med virkning 1. juli ga null equity-justert produksjon for hele året (PR #25) | Referansedato bygges i UTC, og `to` dekker hele den siste dagen |

**Detaljer om tidssonefeilen**, siden feilklassen er direkte overførbar til
søsterløsningene:

`midDate` bygget referansedatoen med `new Date(year, 6, 1)`, altså 1. juli i
**nettleserens lokale tid**. Sodirs lisensgrenser er UTC-midnatt. I Norge
(UTC+1) ble sonden dermed `30. juni 23:00 UTC`:

```
gammel lisens  to   = 1977-06-30 00:00 UTC   ->  ts > to    -> utelukket
ny lisens      from = 1977-07-01 00:00 UTC   ->  ts < from  -> utelukket
```

Sonden landet i et ettdøgns tomrom mellom de to periodene, én time på feil side
av begge. Alle andeler ble 0, og hele årets equity-justerte produksjon forsvant
— mens feltproduksjonen, som ikke går via `getShare`, sto urørt.

15 felt-år var rammet, verifisert mot datafilene: EKOFISK 1977, VEST EKOFISK
1977, FRIGG 1977, COD 1977, STATFJORD 1994 og 1997, STATFJORD ØST 1997,
STATFJORD NORD 1997, MURCHISON 1994 og 1997, DRAUGEN 1995, BLANE 2010,
BRAGE 2011, KRISTIN 2005, ORMEN LANGE 2013. 1977-klyngen er Phillips/Elf-
gruppens felt, som alle skiftet eierskap samme dato.

To ting er verdt å merke seg for søsterløsningene. Feilen var **usynlig i
UTC** — en test på en server i UTC ga 0 avvik, den slo kun ut i nettleseren
til noen i en annen tidssone. Og den rammet **kun årlig** oppløsning;
månedlig og kvartalsvis slår opp den 15., som aldri ligger inntil en
døgngrense.

Rettingen er todelt: referansedatoen bygges med `Date.UTC`, og `to`-datoen
tolkes som hele det døgnet (`+ 86399999 ms`). Hver av dem lukker alle 15 alene;
den andre gjør oppslaget robust mot at `to` og `from` i kilden ligger ett døgn
fra hverandre istedenfor å møtes.

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
const TRIAL_PROD_OWNERSHIP = {          // index.html:741-743
  17196400: { 'Aker BP ASA': 80.0, 'OMV (Norge) AS': 20.0 },
};
const TRIAL_PROD_FIELDS = [             // index.html:830-832
  { id: 17196400, name: '16/1-12 TROLDHAUGEN', trialProd: true },
];
```

**Hvorfor:** `16/1-12 Troldhaugen` er et *funn* (DISCOVERY), ikke et felt. Det
har prøveproduksjon i lag 7300, men finnes ikke i lag 7100 og har ingen rader i
lisensiærtabellen 7108. Uten denne hardkodingen ville det verken hatt navn eller
eierandeler i appen.

**Problemet:** eierandelene 80/20 er skrevet inn for hånd, har ingen kilde, og
oppdateres aldri. `getShare` sjekker dette oppslaget *før* lisensiærdataene
(`index.html:758-760`), så hardkodingen vinner alltid. Og som vist i §5 finnes
det **43 andre** funn-carriers som ikke har fått samme behandling — to av dem med
faktisk produksjon (`25288497` = 7220/11-1 Alta, `44576` = 33/9-6 DELTA). De
dukker opp som `Field <id>` uten eierandeler.

Spesialtilfellet er spredt over elleve steder: `:741-748`, `:750-753`, `:758-759`, `:830-834`, `:915-916`, `:1117-1119`,
`:1812-1818`, `:4668-4670`, og i hvert av de fem `relevantIds`-uttrykkene
(`:3402-3404`, `:3512-3514`, `:3917-3919`, `:4113-4115`).

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

Hele cache-laget (`index.html:526-562`, `:857-863`, `:906`, `:964-969`, `:992`)
kjører aldri så lenge `data/`-filene finnes, fordi bundle-stien returnerer tidlig
(`index.html:847-852`, `:954-962`). `sessionClearProd()` kalles fortsatt ved
årsbytte (`:549`) og periodebytte (`:1180`, `:1192`), men er en no-op for
korrektheten. Koden er ikke feil — bare uvirksom.

### 11.10 Årsvelgeren overstyres ved periodebytte

Se §8.8. Bytter brukeren periode, mistes et manuelt satt `yearFrom`.
`setPeriod` tømmer i tillegg hele `fieldProdCache` (`:1146`, `:1158`) — harmløst
når bundelen finnes, men en full refetch i fallback-modus.

### 11.11 Inneværende måned vises ufullstendig

`isCompletePeriod` returnerer `true` for månedlig (`index.html:754`). Er kilden
delvis oppdatert for inneværende måned, vises et kunstig lavt tall som siste
søyle. Årlig og kvartalsvis er beskyttet; månedlig er det ikke.

### 11.12 Avrunding før aggregering

Alle tegnefunksjonene runder med `.toFixed(0)` per periode *før* verdiene havner
i `lastDatasets` og dermed i Excel. Summerer man mange felt, akkumuleres
avrundingsfeil på inntil 0,5 boe/dag per felt per periode. Ubetydelig for visning,
men merkbart hvis noen bruker Excel-eksporten til avstemming.

### 11.13 Én feilende kilde fryser alle datasettene

`main` returnerer 1 ved første feilende datasett, før noe skrives
(`fetch_sodir.py:563-568`). Det garanterer innbyrdes konsistens, men betyr at en
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

> **USIKKER:** Koden oppgir konsekvent «NLOD 2.0» (`fetch_sodir.py:49`,
> `index.html:4682`). FactPages' egen forsidetekst nevner NLOD uten
> versjonsnummer. NLOD 2.0 er gjeldende versjon, så påstanden er etter alt å
> dømme riktig, men jeg fant ingen eksplisitt versjonsangivelse hos Sodir som
> bekrefter det. Bør sjekkes mot Sodirs vilkårsside før publisering.

> **Merk forbeholdet om tredjepartsrettigheter** i sitatet over. Det er ikke
> vurdert i denne løsningen.

### Havtil

Tilsynssakene i feltprofilen (`fetch_havtil.py`, `data/havtil.json`) kommer
fra Havindustritilsynet. Ifølge Havtils nettsted kan materialet gjenbrukes
gratis i alle formater og medier, under forutsetning av at Havtil krediteres.
Kortet i feltprofilen oppgir Havtil som kilde, og hver sak lenker til saken
hos Havtil.

### Attribusjon som vises i dag

1. **I appen**, nederst på siden (`index.html:327`):
   > [Norwegian Offshore Directorate](https://factpages.sodir.no) – NLOD 2.0 · data per ÅÅÅÅ-MM-DD

   Datoen fylles inn fra `data/meta.json` (`index.html:416-418`).

2. **I Excel-eksporten**, på Info-arket (`index.html:4682`):
   > Norwegian Offshore Directorate (Sodir) FactPages – https://factpages.sodir.no – License: NLOD 2.0

3. **I datafilene**, i `meta.json` (`fetch_sodir.py:49`):
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
