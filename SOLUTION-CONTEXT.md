# SOLUTION-CONTEXT — NCS Production (Norge)

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
  (`fetch_sodir.py:27`, `index.html:333`)
- Menneskelesbar kilde: `https://factpages.sodir.no`

**Hva løsningen gjør:** Én mobiloptimalisert enkeltsides webapp som viser
produksjonshistorikk på norsk sokkel som stablede søylediagram i **boe/dag**.
Brukeren velger *hub*, *felt* eller *selskap*, og en periodeoppløsning
(månedlig / kvartalsvis / årlig). For hub og felt finnes i tillegg en
reservevisning i **mill fat o.e.** som har sin egen årsakse og ignorerer
periodevelgeren. Data kan lastes ned som Excel.

**Arkitektur i to deler:**

1. **Innsamling** — en Python-jobb (`fetch_sodir.py`) kjører daglig i GitHub
   Actions, henter fem datasett fra Sodir og committer dem som kompakte
   JSON-filer under `data/` i selve repoet.
2. **Presentasjon** — `index.html` er en helt statisk fil som leser disse
   JSON-filene ved oppstart. Finnes de ikke, faller appen tilbake til å kalle
   Sodir-APIet direkte fra nettleseren (`index.html:772-794`, `837-845`, `903-909`).

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
| Lagring | Statiske JSON-filer i git under `data/` | `fetch_sodir.py:43` |
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
# fordi den fetcher data/*.json med relativ sti (index.html:259, 221)
python3 -m http.server 8000
# -> http://localhost:8000/index.html
```

### Deploy

Push til `main` publiseres automatisk av GitHub Pages. Datafilene oppdateres av
workflowen, som committer direkte til `main` (workflow linje 40-46).

---

## 3. Datakilder

Alle fem datasett kommer fra samme ArcGIS FeatureServer. **Ingen autentisering,
ingen API-nøkkel, ingen rate limiting observert.**

| Lag-ID | Navn i tjenesten | Type | Rader (2026-09-14) | Brukes til |
|---|---|---|---|---|
| 7100 | `field` | Feature Layer (polygon) | 142 | Feltliste (id + navn) |
| 7300 | `profiles` | Table | 32 220 | Produksjonstall |
| 7108 | `field_licensee_hst` | Table | 10 090 | Eierandeler over tid |
| 7114 | `field_reserves_company` | Table | 417 | Reserver per selskap |
| 7113 | `field_reserves` | Table | 2 333 | Reserver per felt per år (2004→) |
| 7000 | `discovery` | Feature Layer (polygon) | 649 | Funn: navn, status, funnår, eier |
| 7008 | `discovery_reserves` | Table | 105 | Ressursanslag per funn |
| 3007 | `licence_licensee_hst` | Table | 33 321 | Eierandeler i utvinningstillatelser |
| 3304 | `bsns_arr_area_licensee_hst` | Table | 5 609 | Eierandeler i forretningsavtaleområder |

Lag-ID-ene er hardkodet to steder: `fetch_sodir.py:29-33` og `index.html:334-338`.

**Lag 7113 er en tidsserie, ikke et øyeblikksbilde.** `fldVersion` er årstallet
estimatet gjelder for, og tabellen inneholder én rad per felt per årgang tilbake
til 2004. 144 felt er dekket; 111 av dem har minst ti årganger og 66 har hele
serien. Dette er den eneste kilden i løsningen med historikk for *estimater* —
lag 7114 har kun siste vurdering.

**Format:** ArcGIS REST JSON (`f=json`). Svar har formen
`{"features":[{"attributes":{...}}, ...]}`. `maxRecordCount` er 2000 på alle fire
lag — derfor er sidestørrelsen satt til nøyaktig 2000 (`fetch_sodir.py:44`).

**Oppdateringsfrekvens:** Sodir publiserer produksjonstall **månedlig**.
Innsamlingsjobben kjører likevel daglig (se §4). Verifisert: datafilene i repoet
var uendret fra 2026-08-31 til 2026-09-14 — en ny kjøring 2026-09-14 ga
byte-identisk resultat for alle fire datasett.

**Hvordan endringer oppdages:** Løsningen henter **alt** på nytt hver gang og
sammenligner den serialiserte JSON-teksten mot fila på disk
(`fetch_sodir.py:457-472`). Det finnes ingen inkrementell henting.

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
2. **`python fetch_sodir.py`** (linje 34). For hvert av de fire datasettene
   (`fetch_sodir.py:478-490`), i rekkefølge felt → produksjon → lisensiærer →
   reserver → reserver per felt:
   - **Tell rader:** `returnCountOnly=true` (`fetch_sodir.py:85-89`).
   - **Finn sorteringsnøkkel:** hent lagets `objectIdField` (`fetch_sodir.py:71-82`).
     Dette er viktig — uten stabil sortering kan offset-basert paginering hoppe
     over eller duplisere rader. Feiler dette, fortsetter jobben *usortert* med
     en advarsel (linje 70-72).
   - **Paginer sekvensielt** i sider à 2000 med `resultOffset` og
     `orderByFields=<oid> ASC`, `returnGeometry=false` (`fetch_sodir.py:98-118`).
   - **Transformér** til kompakt form (`build_*`-funksjonene, se §5).
   - **Skriv kun ved endring** (`fetch_sodir.py:457-472`).
3. **Commit kun ved endring** (workflow linje 41-45): `git diff --staged --quiet`
   avgjør om det lages en commit. De fleste dagene blir dette en no-op.
4. **`meta.json` skrives bare hvis minst ett annet datasett endret seg**
   (`fetch_sodir.py:504-508`). Dette er bevisst: `meta.json` inneholder dagens
   dato, og ville ellers tvunget fram en commit hver eneste dag og dermed
   ødelagt no-op-egenskapen.

### Idempotens

**Ja, verifisert.** To kjøringer etter hverandre mot live kilde:
kjøring 1 skrev alle filer, kjøring 2 rapporterte «uendret» for alle fire og
skrev ingenting. Utdata er deterministisk fordi:

- radene sorteres eksplisitt før serialisering (`fetch_sodir.py:150`, `165`, `190`, `202`)
- JSON skrives med `sort_keys=True` og faste separatorer (`fetch_sodir.py:219`)
- flyttall rundes til 6 desimaler (`fetch_sodir.py:127`)

Sorteringen av lisensiærer må håndtere `None` eksplisitt, siden `None` ikke kan
sammenlignes med `int` i Python 3 (`fetch_sodir.py:200`).

### Feilhåndtering

- **HTTP-nivå:** 4 forsøk med eksponentiell backoff 2/4/8 s, timeout 120 s per
  request (`fetch_sodir.py:43-45`, `41-58`). ArcGIS returnerer feil som HTTP 200
  med `{"error": ...}` i kroppen — dette fanges eksplisitt (linje 49-50).
- **Datasettnivå:** Feiler ett datasett etter alle retries, **avbrytes hele
  jobben med exit 1 uten å skrive noe** (`fetch_sodir.py:494-500`). Repoet
  beholder da forrige gyldige datasett, og appen fortsetter på gamle tall.
  Konsekvensen er at datasettene alltid er innbyrdes konsistente — men også at
  én vedvarende feil fryser *alle* datasett.
- **Frontend:** Feiler henting av en `data/*.json`, returnerer `fetchBundle`
  `null` (`index.html:358-364`), og hver lastefunksjon faller gjennom til
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

142 rader. Sortert på navn (`fetch_sodir.py:150`).
Bygges av `fetch_sodir.py:142-151`.

### `data/production.json` — produksjonstall

```json
{"columns":["year","month","oil","gas","oe","water"],
 "fields":{"26376286":[[2026,5,3.16927,0.109115,3.33904,1.6872], ...]}}
```

Nøkkelen i `fields` er carrier-ID som **streng** (`fetch_sodir.py:166`).
Hver rad er et array i rekkefølgen gitt av `columns`:

| Pos | Kolonne | Type | Kilde | Enhet |
|---|---|---|---|---|
| 0 | `year` | int | `prfYear` | — |
| 1 | `month` | int | `prfMonth` | 1–12, **eller 0 for årssum** |
| 2 | `oil` | float | `prfPrdOilNetMillSm3` | mill Sm³ |
| 3 | `gas` | float | `prfPrdGasNetBillSm3` | mrd Sm³ |
| 4 | `oe` | float | `prfPrdOeNetMillSm3` | mill Sm³ o.e. |
| 5 | `water` | float | `prfPrdProducedWaterInFieldMillS` | mill Sm³ |

Radene sorteres på `(year, month)` per carrier (`fetch_sodir.py:175`).
Bygges av `fetch_sodir.py:154-179`.

> **`month = 0` er ikke en måned — det er årssummen.** Kilden leverer både
> månedsrader og en aggregert årsrad per år i samme tabell. Koden skiller dem i
> `isValidRow` (`index.html:580-582`). Blander man dem, dobbelttelles alt.

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
Bygges av `fetch_sodir.py:182-201`.

### `data/reserves.json` — reserver aggregert per selskap

```json
{"companies":{"Equinor Energy AS":3462.051955,"Petoro AS":2691.322247}}
```

22 selskaper. Verdien er **opprinnelig utvinnbar** oljeekvivalent i mill Sm³ o.e.,
summert over alle felt selskapet har andel i (`fetch_sodir.py:205-213`).
Brukes **kun til å sortere selskapslisten** (`index.html:999-1008`) — aldri vist.

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
`production.json`. Radene er sortert stigende på år (`fetch_sodir.py:232-253`).

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
(`fetch_sodir.py:216-222`).

**144 felt, 2 333 rader, 187 KB.** Kun `fldRecoverableOE` og `fldRemainingOE`
brukes av frontenden i dag; de tolv andre kolonnene hentes, men vises ikke.

### `data/discoveries.json` — funn

```json
{"columns":["id","name","status","ownerKind","owner","hc","year","field"],
 "rows":[[43814,"1/2-1 Blane","Producing","BUSINESS ARRANGEMENT AREA",
          "BLANE UNIT","OIL",1989,"BLANE"]]}
```

Alle 649 funn, ikke bare de med ressursanslag — statusfordelingen og funnårene
er nyttige i seg selv, og fila er liten (66 KB rå / 13 KB gzip).
`owner` er navnet på utvinningstillatelsen eller forretningsavtaleområdet som
eier funnet, og `ownerKind` sier hvilken av de to.

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

Frontenden bruker **kun 4F, 5F og 7F** (`CONTINGENT_RC`, `index.html:1321`).
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

`generated` vises i bunnteksten på siden (`index.html:285`, `index.html:374-376`).

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
(`index.html:702`, `644`, `894`, `918`). Konsekvenser:

- Skrivemåteendring eller fusjon splitter ett selskap i to i UI-et
- `getShare` returnerer stille `0` ved navnebom (`index.html:726`)
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
const BBL_PER_SM3 = 6.29;   // index.html:343
```

Hardkodet ett sted, i `index.html`. Brukes i alle fire konverteringsfunksjoner
(`index.html:588-595`). Hentescriptet gjør **ingen** enhetskonvertering — det
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

- Månedlig: `daysInMonth(år, måned)` via `new Date(y,m,0).getDate()` (`index.html:476`)
- Årlig: `daysInYear` med korrekt skuddårsregel inkl. 100/400-unntak (`index.html:477`)
- Kvartalsvis: sum av dagene i kvartalets tre måneder (`index.html:601-604`)

Faller en rad bort, brukes **30 dager** som nøddivisor
(`index.html:1769`, `1370`, `1374`, `1378`, `1405`). Verdien er vilkårlig, men
treffer bare rader som uansett er tomme.

### Avrunding

- Innsamling: 6 desimaler (`fetch_sodir.py:127`)
- Enkeltfelt-visning: 1 desimal (`.toFixed(1)`, `index.html:1770-1772`)
- Alle andre visninger: 0 desimaler (`.toFixed(0)`)
- Tooltip: `Math.round` (`index.html:2923`)

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

Hentescriptet setter eksplisitt `returnGeometry: 'false'` (`fetch_sodir.py:104`),
og ingen av JSON-filene under `data/` inneholder koordinater.

> **Merk:** ArcGIS' standardverdi for `returnGeometry` er `true`. Frontendens
> fallback-sti satte den ikke, og lastet dermed ned polygongeometri den aldri
> brukte når den falt tilbake til live-API mot lag 7100 (verifisert live: ett
> felt returnerte en ring med 258 punkter). `queryLayer` setter den nå eksplisitt
> til `'false'` (`index.html:518-530`). Tabellagene var uansett upåvirket, siden
> de ikke har geometri.

---

## 8. Forretningslogikk

### 8.1 «Oil» i grafen er ikke oljekolonnen

Dette er den viktigste ikke-åpenbare regelen i hele løsningen.

```js
function oilBoed(oilMillSm3, gasBillSm3, oeMillSm3, d) {   // index.html:490-494
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
tidspunktet** (`index.html:709-727`):

1. Beregn en referansedato for perioden via `midDate` (`index.html:570-574`):
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

Kvartaler bygges kun av månedsrader, aldri av årsrader (`index.html:608-621`).

**Regelen for hvilke kvartaler som tas med er subtil:** et kvartal beholdes hvis
unionen av *alle valgte felt* dekker minst 3 distinkte måneder
(`index.html:620`). Det kreves altså ikke at hvert enkelt felt har alle tre
månedene. Velger man to felt der det ene har jan+feb og det andre har mar, regnes
Q1 som komplett.

Selve verdien er et **dagvektet gjennomsnitt**, ikke et enkelt snitt
(`index.html:626-639`):

```
kvartalsrate = Σ(månedsrate × dager_i_måned) / dager_i_kvartal
```

### 8.4 Filtrering av ufullstendige perioder

`isCompletePeriod` (`index.html:658-673`):

- **Årlig:** inneværende år ekskluderes helt (`+label < nowYear`)
- **Kvartalsvis:** inneværende kvartal ekskluderes til siste måned er passert
- **Månedlig:** returnerer alltid `true` — **inneværende, ufullstendige måned vises**

Asymmetrien er tilsiktet, men udokumentert i koden.

### 8.5 Trimming av ledende nullperioder

`trimLeadingZeros` (`index.html:2883-2891`) fjerner perioder fra starten der
*alle* dataserier er 0 eller mangler. Kalles fra `renderChart`
(`index.html:2900`). Formålet er at grafen skal begynne der produksjonen faktisk
startet. Nullperioder *inne i* eller på slutten av serien beholdes.

### 8.6 Håndtering av manglende verdier

- Innsamling: `None` → `0` for tall (`fetch_sodir.py:122-129`), men
  `None` bevares for datoer (`fetch_sodir.py:132-138`)
- Rader uten `fldNpdidField`/`cmpLongName` forkastes (`fetch_sodir.py:147`, `180`)
- Rader uten `prfYear` forkastes (`fetch_sodir.py:164`)
- Frontend: `|| 0` overalt ved uthenting av måleverdier
- Serier som er null hele veien skjules fra grafen via `hasAnyValue`
  (`index.html:729`, brukt `1407`, `1556`, `1662`, `1709`)
- Felt uten navn vises som `Field <id>` (`index.html:2300`)

### 8.7 Sortering og standardvalg

- **Selskapsliste:** `Aker BP ASA` tvinges alltid øverst, deretter synkende
  reserver, deretter alfabetisk (`index.html:999-1008`). Kun 22 av 263 selskaper
  har reserver, så resten faller til alfabetisk.
- **Feltliste:** felt der Aker BP er lisensiær sorteres øverst ved oppstart
  (`index.html:3304-3315`).
- **Standardvalg ved oppstart:** modus = `companies`, `Aker BP ASA`
  forhåndsvalgt (`index.html:3317`), `JOHAN SVERDRUP` forhåndsvalgt i feltlisten
  (`index.html:791-792`), og i hub-listen den første huben hvis navn starter med
  `alvheim` (`index.html:754-758`, kalt fra `index.html:3297`). Hub-defaulten
  slås opp på prefiks fordi hub-navn er fritekst og kan endres i
  settings-visningen; finnes ingen treff, står Hubs-viewet tomt.

Dette er ikke nøytral produktlogikk — det er en innebygd preferanse for ett
selskap. Ved sammenslåing bør det parametriseres.

### 8.8 Reserver — utledet produksjon og videreføring av estimater

Reserves-visningen (`index.html:1914-2184`) er den eneste visningen som ikke
bygger på produksjonstidsserien. To utledninger er verdt å kjenne:

**Produsert volum finnes ikke i kilden.** Sodir oppgir opprinnelig utvinnbart og
gjenstående, aldri produsert. Visningen regner `produsert = utvinnbart −
gjenstående`, klemt til minst null (`index.html:1937`). Søylehøyden blir dermed
opprinnelig utvinnbart det året, og splitten viser uttømmingen.

Dette er *ikke* det samme som akkumulert produksjon fra lag 7300, men ligger
nær. Kontrollert for Johan Sverdrup, estimat 2025: 422,017 − 200,597 = 221,42
mill Sm³ o.e., mot 221,93 summert fra månedsradene i `production.json` t.o.m.
2025 — 0,2 % avvik.

**Sodir revurderer ikke alle felt hvert år.** Mangler et felt en årgang,
videreføres feltets forrige estimat i stedet for å telles som null
(`index.html:1908-1912`). Uten dette ville summen over flere felt falle og
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
(`index.html:1936-1937`).

### 8.9 Årsvelgerens oppførsel ved periodebytte

`setPeriod` (`index.html:1082-1107`) overstyrer brukerens årsvalg:

- **Til årlig:** setter `yearFrom = 1970`, tømmer cache, henter, og «snapper»
  deretter til første år med faktisk produksjon via `firstYearInCache`
  (`index.html:1060-1080`), som kun ser på årsrader med `oe > 0`.
- **Til månedlig/kvartalsvis:** setter `yearFrom = currentYear - 1`.

Begge veier nullstiller altså et årsintervall brukeren måtte ha satt selv.

---

## 9. Frontend

Én skjerm, ingen ruting, ingen tilstand i URL. Alt er i `index.html`.

### Kontroller (topp til bunn)

| Kontroll | Element | Verdier |
|---|---|---|
| Modusvelger | `#modeBtnHubs` / `#modeBtnFields` / `#modeBtnDiscoveries` / `#modeBtnCompanies` (`:218-221`) | **Companies** (standard) |
| Årsintervall | `#yearFrom` / `#yearTo` (`:227-229`) | 1970–inneværende år, **synkende** (`:465`); standard `[år−1, år]` (`:472-473`) |
| Periode | `#btnMonthly/Quarterly/Annual` (`:232-234`) | **Monthly** standard |
| Undervisning | `#viewTabs` (`:239`) | Avhenger av modus, se under |
| Produsert vann | `#waterToggle` (`:244`) | Kun synlig for Fields + Oil & Gas (`:1043-1045`) |
| Valg | `#selectFab` → bottom sheet (`:287`, `:314-328`) | Søk, «Select all», «Clear all» |
| Eksport | `#downloadBtn` (`:275`) | Excel (.xlsx) |
| Nullstill | `.topbar-title` (`:204`) | Sidetittelen er en knapp — `resetView` setter alt tilbake |
| Del visning | `#shareBtn` (`:268`) | Kopierer lenke til gjeldende visning |

### Maksbredde

Fra 1148 px sentreres innholdet i en spalte på **1100 px**
(`index.html:217-227`). Under det gjelder ingenting av det, så mobil- og
nettbrettlayouten er uendret.

Topplinjen og kontrollraden har bakgrunnsfarge som skal gå helt ut til
skjermkanten. De sentreres derfor med padding i stedet for maksbredde. Den
flytende velgerknappen og bunnarket følger spalten, ikke skjermkanten.

Regelblokken ligger **sist** i stilarket. Flere av elementene setter sin egen
`margin` lenger nede i filen, og uten den plasseringen ville `margin:auto`
tapt mot dem — en mediaspørring gir ikke høyere spesifisitet.

### Nullstilling

Sidetittelen øverst er en `<button>` som kaller `resetView`
(`index.html:1409-1452`). Den setter modus, faner, periode, årsintervall,
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
oppdateres fortløpende med `history.replaceState` (`syncUrl`,
`index.html:1493-1495`, kalt fra `refresh`, `setWater` og
`setHubReserveGrouping`). Hash er valgt fordi GitHub Pages serverer samme fil
uansett sti, og fordi hash aldri sendes til serveren. `replaceState` framfor
`pushState` gjør at hver justering ikke fyller nettleserhistorikken.

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
| `h`, `c` | hub- og selskapsnavn, skilt med `~` (`SEP`, `index.html:1464`) |
| `w` | produsert vann på |
| `g` | `company` når hub-reservetabellen er selskapsgruppert |

Felt lagres som **ID**, men huber og selskaper som **navn**. Hub-navn er
fritekst og kan endres i settings-visningen; da faller oppføringen bort ved
åpning i stedet for å peke på feil hub. `applyHash` (`index.html:1499-1562`)
validerer alt mot det som faktisk finnes og ignorerer resten, så en gammel
lenke åpner det den fortsatt kan i stedet for å feile. Verifisert for ukjent
modus, ukjent fane, ukjent felt-ID, omdøpt hub og årstall utenfor
1970–inneværende år.

`applyHash` kalles i `init` etter at felt- og hublistene er bygget og
standardvalgene satt, og overstyrer dem. Finnes ingen hash, returnerer den
`false` og standardvalgene står.

Rundturen er stabil: åpnes en lenke og tegnes visningen, er `stateToHash`
(`:1473-1491`) identisk med hashen som ble åpnet.

`copyViewLink` (`:1564-1579`) legger lenken på utklippstavlen. Utklippstavlen
krever sikker kontekst og kan avvises; da vises lenken i knappen i stedet,
slik at den kan merkes manuelt.

### De femten visningene

| Modus | Fane | Funksjon | Hva som tegnes |
|---|---|---|---|
| Hubs | Oil & Gas | `drawFieldsOilGas({ids, scope})` | Stablet Oil/Gas summert over hubens medlemsfelt, brutto |
| Hubs | OE per field | `drawFieldsOePerField({ids, scope})` | Én serie per medlemsfelt i huben |
| Hubs | Reserves | `drawFieldsReserves({ids, scope})` (`:1914`) | Reserver over tid, summert over hubens medlemsfelt. Tabell under: per felt og partner |
| Fields | Oil & Gas | `drawFieldsOilGas` (`:1742`) | Stablet Oil/Gas (+Water). Ett felt: 1 desimal. Flere felt: **summert**, 0 desimaler |
| Fields | OE per field | `drawFieldsOePerField` (`:1813`) | Én serie per felt, brutto o.e. |
| Fields | OE per company | `drawFieldsOePerCompany` (`:2187`) | Én serie per selskap, **equity-justert** |
| Fields | Reserves | `drawFieldsReserves` (`:1914`) | Stablet gjenstående/produsert per år, **mill fat o.e.** Tabell under: per partner |
| Discoveries | Resources | `drawDiscoveriesPerDiscovery` (`:2402`) | Betingede ressurser per funn, stablet Liquids/Gas. Tabell under: per funn og partner |
| Discoveries | Per company | `drawDiscoveriesPerCompany` (`:2434`) | Samme volumer fordelt på lisensandel, én søyle per selskap. Tabell under: per selskap og funn |
| Discoveries | Maturity | `drawDiscoveriesMaturity` (`:2459`) | Per selskap, stablet på ressursklasse 4F/5F/7F |
| Fields | Profile | `drawFieldProfile` (`:2122-2184`) | Faktaark for **ett** felt: årlig produksjon i grafen, og under den nøkkeltall, kart, volumer, eierskap og Sodirs beskrivelse |
| Companies | Oil & Gas | `drawCompaniesOilGas` (`:2216`) | Stablet Oil/Gas, equity-justert, **summert over valgte selskaper** |
| Companies | OE per field | `drawCompaniesOePerField` (`:2285`) | Én serie per felt, equity-justert |
| Companies | OE per hub | `drawCompaniesOePerHub` (`:2325`) | Som over, men feltene summert opp i huben de tilhører. Seriene sorteres synkende etter total produksjon |
| Companies | Reserves | `drawCompaniesReserves` (`:2506`) | **Kun gjenstående** reserver over tid, stablet Liquids/Gas, summert over valgte selskaper, equity-justert. Tabell under: per hub og felt |

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
gjelder per årsslutt (`index.html:2530`). Et selskap bidrar dermed kun i de
årene det faktisk var lisensiær, og oppkjøp vises som hopp i serien. Verifisert
eksempel: Aker BPs andel i Johan Sverdrup går fra 11,5733 til 31,5733 med
virkning 2023-01-01 i lag 7108, og selskapets gjenstående reserver hopper
tilsvarende fra 778,5 til 1 764,4 mill fat o.e. mellom årgang 2022 og 2023.

Alle tre reservevisningene har en tabell under grafen. Alle viser **kun siste
årgang** og **kun gjenstående** reserver, delt i Liquids / Gas / Total OE, med
grupper og rader sortert synkende etter total OE. De deler byggeren
`reserveTableHtml` (`index.html:2675-2707`):

| Visning | Gruppe / rad | Eierandel | Funksjon |
|---|---|---|---|
| Companies | hub / felt | ja | `renderReserveTableByHub` (`:2711-2751`) |
| Hubs | felt / partner **eller** selskap / felt | ja | `renderReserveTableByFieldPartner` (`:2816-2875`) |

Hubs-tabellen kan grupperes to veier, med en vekslerad over tabellen
(`hubGroupingToggle`, `index.html:2803-2809`). Valget ligger i
`hubReserveGrouping` (`:2794`) og huskes gjennom en ny `refresh`, men ikke
over en sidelasting — det er et lesevalg, ikke en innstilling. Veksleren
tegner tabellen om alene fra `lastHubReserve` (`setHubReserveGrouping`,
`:2797-2801`); grafen røres ikke. Tømmes tabellen, nullstilles også
`lastHubReserve` (`index.html:1698`), slik at en forsvunnet veksler ikke kan
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
| Fields | partner (flat) | ja | `renderReserveTableByPartner` (`:2884-2909`) |

Alle tre har en **Share**-kolonne (`opts.shareHead`). Andelen settes kun på
radene der den er definert, og står tom ellers — blant annet på totalraden.

I Companies-tabellen betyr andelen to ting, som i hub-tabellens
selskapsgruppering. På **feltraden** er den de valgte selskapenes samlede
lisensandel i det feltet. På **hubraden** er den deres beregnede andel av hele
huben: equity-justert OE delt på hubens brutto gjenstående reserver, der
nevneren summerer over **alle** medlemsfelt — også de selskapene ikke er inne
i. Det er dét som gjør tallet til en eierandel i huben, og ikke et snitt av
lisensandelene (`index.html:2732-2741`).

Eksempel, Aker BP: Yggdrasil-huben står på 64,87 %, satt sammen av 87,70 % i
Hugin, 50,00 % i Munin og 47,70 % i Fulla. Med Equinor i tillegg stiger
hubandelen til 93,09 %.

I Fields-tabellen er partnerne summert over flere felt, og eierandelen er
feltspesifikk. Er andelen den samme i alle feltene partneren bidrar i —
typisk når bare ett felt er valgt — vises den eksakt. Ellers vises et snitt
vektet med partnerens egen gjenstående OE per felt, merket med `~`
(`index.html:2884-2909`). Eksempel, Johan Sverdrup + Troll: Aker BP står
umerket på 31,57 % fordi selskapet bare er inne i Johan Sverdrup, mens
Equinor står `~34,50 %` som vektet snitt av 42,63 % i Johan Sverdrup og 30,55 % i Troll.

Companies-tabellen er equity-justert som grafen. Hubs- og Fields-tabellene
dekomponerer **brutto** feltreserver på lisensiærene, siden de grafene er
brutto. En gruppe hvis eneste underrad gjentar gruppenavnet får ingen
underrad.

`fieldPartners` (`index.html:2647-2663`) slår opp lisensiærene per 31.12 i
årgangen, med samme datologikk som `getShare` — inkludert at to-datoen dekker
hele den siste dagen. Summerer ikke andelene til 100 %, legges resten på en
rad merket `Unallocated (cross-border share)` (`index.html:2665`), slik at
gruppesummen fortsatt stemmer med grafen.

> Med dagens data utløses den raden aldri: alle 107 felt med gjenstående
> reserver i årgang 2025 har lisensandeler som summerer til 100,00 %, også
> grensefeltene — Sodir fører de britiske partnerne eksplisitt (f.eks.
> `Equinor UK Limited` på Statfjord). Raden er en sikring, ikke en observert
> korreksjon. Merk at dette gjelder lag **7108**; andelene i lag **7114** er
> en annen sak, se advarselen i §5.

`renderChart` nullstiller beholderen ved hver tegning (`index.html:2930`);
`showSpinner` og `setChartEmpty` tømmer den også.

### Funntabellen under companies/reserves

`companyDiscoveryTable` (`index.html:2757-2785`) legger en **andre** tabell
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

> **USIKKER:** Lag 7113 inneholder ett felt, `fldNpdidField = 33310197`, med
> 42,953 mill Sm³ o.e. gjenstående i siste årgang, som **ikke finnes i lag
> 7100, 7102 eller 7108** (verifisert live 2026-10-16: alle tre returnerer
> null treff). Feltet er dermed usynlig i appen, siden alt feltutvalg går via
> `allFields` fra lag 7100. Jeg har ikke klart å finne ut hvilket felt det er.
> Søsterløsningene bør ikke anta at reservelaget og feltlaget dekker samme
> mengde felt.

«OE per hub» i Companies-modus bruker `hubOfField` fra `data/hubs.json`. Felt
uten oppføring er sin egen hub, så ingen produksjon faller utenfor
grupperingen; summen over hub-seriene er lik summen over felt-seriene.

### Profile

Profilvisningen krever **nøyaktig ett** valgt felt; ellers vises en melding om
å velge ett. Den har ingen tidsakse-kontroller — grafen viser alltid hele den
årlige historikken, uavhengig av periodevelgeren, som derfor skjules.

På skjermer fra 900 px flyter kortene i **to spalter** (`column-count`,
`index.html:112-116`). Et rutenett ble prøvd først, men kortene er svært
ulike i høyde, og rutenettet låste dem parvis og etterlot store tomrom ved
siden av det høyeste i hver rad — profilen ble 1 627 px høy mot 938 px med
spalteflyt. Målt i Chromium på 1400 px. Under 900 px er alt én spalte som før.

Innholdet ligger i `#profileBox` (`index.html:263`), en egen beholder under
`#chartTable`. `setProfile` er eneste vei inn, og den tømmes de samme tre
stedene som tabellen (`renderChart`, `showSpinner`, `setChartEmpty`).

| Del | Kilde | Funksjon |
|---|---|---|
| Nøkkeltall | `data/field_profile.json` | `profileRow` (`:1973-1985`) |
| Kart | `data/field_outlines.json` + `data/coastline.json` | `profileMap` (`:2010-2075`) |
| Volumer | `data/reserves_field.json` | `profileVolumes` (`:2077-2105`) |
| Eierskap | lag 7108 via `fieldPartners` | `profileOwnership` (`:2107-2120`) |
| Beskrivelse | `data/field_descriptions.json` | `fieldDescriptions` (`:1987-1998`) |

**Produksjonsstart** finnes ikke i feltlaget. Den utledes av fra-datoen til
den første `Producing`-raden i lag 7101 (`fetch_sodir.py:334-370`).
Kontrollert mot Ekofisk, som skal være **15. juni 1971**. 133 av 142 felt har
en slik dato.

**Kartet** er inline SVG uten noe kartbibliotek: kystlinje, nabofelt i grått
og feltet selv uthevet, i en enkel ekvirektangulær projeksjon med
cosinus-korreksjon for breddegrad. Utsnittet legges rundt feltet med margin
og låses til minst 1,6° bredde, så små felt ikke zoomes inn til
kontekstløshet.

To ting holder SVG-en nede. Punkter som havner nærmere enn 1–3 piksler fra
forrige punkt droppes, og ringer som dekker under 4 × 4 piksler utelates helt
— kartet er 560 px bredt, så finere detalj er usynlig. Uten det blir Troll
66 KB SVG; med det er snittet 12 KB per profil og verste tilfelle (Vega, ved
den oppstykkede Helgelandskysten) 40 KB.

`SINDRE` mangler geometri i lag 7100 og får derfor ikke kart. Kortet utelates
i stedet for å vises tomt.

**Volumtabellen** har tre kolonner: original in place, original recoverable
og remaining. NGL oppgis i **mill tonn** og konverteres ikke — det er masse,
ikke volum — så den raden står i sin egen enhet med merknad. Gass regnes som
ellers i appen, 1 bill Sm³ = 1 mill Sm³ o.e.

**Eierskapet** viser gjeldende lisensiærer med andel og en andelsstolpe, og
operatøren fra lag 7100 merkes med en etikett.

### Discoveries

Funnvisningene bygger på `loadDiscoveries` (`index.html:1328-1398`), som slår
sammen de tre nye datasettene til `allDiscoveries` (kun funn med ikke-null
betingede ressurser, sortert synkende), `discoveryRes` (volumer totalt og per
ressursklasse) og `licenceOwners` (eier → partnere). `discoveryPartners`
(`:1402-1452`) slår opp partnerne via `ownerKind|owner`.

Alle tre fanene er **tverrsnitt uten tidsakse** — lag 7008 har bare én årgang
— så periode- og årsvelgeren skjules i hele modusen (`isReservesView` dekker
nå `topMode === 'discoveries'`). Enheten er mill fat o.e., som i
reservevisningene.

Ved første lasting forhåndsvelges **alle** funn, siden porteføljen samlet er
det naturlige utgangspunktet. Det skjer bare én gang, så «Clear all» blir
ikke overstyrt etterpå. Et utvalg fra en delt lenke respekteres, og ID-er som
ikke lenger har anslag faller bort.

`discoveryByCompany` (`:2377-2400`) deles av fanene «Per company» og
«Maturity», og legger andelen som ikke er dekket av kjente partnere på
`Unallocated`, slik at totalen stemmer med fanen «Resources».

Reserves-visningen skiller seg ut: X-aksen er reserveestimatets årgang, ikke
periodevelgeren, og Y-aksen er mill fat o.e. i stedet for boe/dag. Periode- og
årsvelgeren skjules derfor mens den er aktiv (`index.html:1048`, via
`isReservesView()` `:1846-1852`). Den er også den eneste visningen som ikke
venter på produksjonsdata (`index.html:1611-1617`, `:1631-1635`, `:1671-1676`).

Alle grafer er stablede søylediagram (`stack:'s'`) med delt Y-akse i boe/dag,
felles tooltip med totalsum (`index.html:2915-2939`). Fargepaletten har 10
farger og resirkuleres med modulo (`index.html:396-407`).

Under grafen viser `#infoBox` en tekstlinje som eksplisitt oppgir serier, enhet
og beregningsgrunnlag («Gross field production» vs «Equity-adjusted»).

### Nettverkskall per visning

**Normaltilfellet (datafiler finnes):** nøyaktig **14 GET-kall**, alle ved oppstart,
alle statiske filer (`index.html:366-376`):

```
GET data/fields.json
GET data/production.json
GET data/licensees.json
GET data/reserves.json
GET data/reserves_field.json
GET data/discoveries.json
GET data/discovery_resources.json
GET data/licence_licensees.json
GET data/field_profile.json
GET data/field_descriptions.json
GET data/field_outlines.json
GET data/coastline.json
GET data/meta.json
GET data/hubs.json
```

Alle hentes parallelt med `cache:'no-cache'` (revalidering mot ETag).
**Etter dette gjør ingen brukerhandling noe nettverkskall** — all filtrering,
periodebytte og selskapsvalg skjer i minnet. `prefetchAllFields` avbryter
umiddelbart når bundelen finnes (`index.html:3337`).

**Fallback (datafiler mangler):** appen kaller Sodir-APIet direkte:

| Situasjon | Kall |
|---|---|
| Feltliste | 1 kall mot lag 7100 (`:776-778`) |
| Selskapsliste | 1 `returnCountOnly` + N parallelle sider mot 7108 (`:926-940`) |
| Reserver | `queryAllParallel` mot 7114 (`:982-985`) |
| Reserver per felt | 1 kall mot lag 7113 (`index.html:1887`) |
| Produksjon | Batcher à 50 felt-ID-er med `IN (...)` mot 7300 (`:826-846`) |
| Bakgrunn | Etter 2 s: batcher à 30 for alle felt (`:3336`, `:3358`) |

Fallbacken har egen sesjonscache i `sessionStorage` med versjonerte nøkler
(`index.html:481-482`), og faller tilbake til en `Map` i minnet hvis
`sessionStorage` kaster — noe Safari med sporingsvern gjør (`index.html:484-502`).

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

`downloadExcel` (`index.html:2952-3005`) genererer i nettleseren en `.xlsx` med
to ark: **Info** (genereringstidspunkt, periodetype, årsintervall,
databeskrivelse, kildehenvisning) og **Data** (én kolonne per serie, sebrastriper).
Filnavn: `NCS_Production_<Periode>_<fra>-<til>.xlsx`.

Reserves-visningen eksporterer annerledes, siden periodetype og årsintervall
ikke gjelder der. `renderChart` tar `opts.xTitle`, som lagres i `lastXTitle`
(`index.html:2896-2902`); er den satt, heter fila `NCS_Reserves.xlsx`,
Info-arket utelater periodetype og årsintervall, og første kolonne får
overskriften `Year` i stedet for `Period` (`index.html:2960-2962`, `:2974-2976`,
`:2987-2988`). `opts.allTicks` styrer i tillegg at hver etikett vises på x-aksen
(`index.html:2933`).

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
| `reserves_field.json` | **2 333** | 144 felt × årganger 2004–2025 |
| — felt med ≥10 årganger | 111 | |
| — felt med hele serien (22) | 66 | |

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
| `reserves_field.json` | 187,3 KB | 53,5 KB |
| `hubs.json` | 6,4 KB | 1,0 KB |
| `discoveries.json` | 64,4 KB | 12,9 KB |
| `discovery_resources.json` | 4,2 KB | 1,5 KB |
| `licence_licensees.json` | 96,4 KB | 9,2 KB |
| `field_profile.json` | 13,9 KB | 2,9 KB |
| `field_descriptions.json` | 213,8 KB | 44,1 KB |
| `field_outlines.json` | 168,9 KB | 53,5 KB |
| `coastline.json` | 50,3 KB | 17,8 KB |
| `meta.json` | 0,1 KB | 0,1 KB |
| **Sum `data/`** | **2888,2 KB** | **708,1 KB** |
| `index.html` | 169,3 KB | 51,3 KB |

GitHub Pages komprimerer tekst automatisk, så reell overføring ved førstegangs
last er ca. **708 KB** for data pluss selve HTML-fila.

Tallene for `reserves_field.json`, `hubs.json` og `index.html` er målt
2026-09-25; de øvrige 2026-09-14.

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

Merk at hentescriptet paginerer **sekvensielt** (`fetch_sodir.py:99`), mens
frontendens fallback paginerer **parallelt** (`index.html:558-560`, `860-867`).
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
const TRIAL_PROD_OWNERSHIP = {          // index.html:694-696
  17196400: { 'Aker BP ASA': 80.0, 'OMV (Norge) AS': 20.0 },
};
const TRIAL_PROD_FIELDS = [             // index.html:783-785
  { id: 17196400, name: '16/1-12 TROLDHAUGEN', trialProd: true },
];
```

**Hvorfor:** `16/1-12 Troldhaugen` er et *funn* (DISCOVERY), ikke et felt. Det
har prøveproduksjon i lag 7300, men finnes ikke i lag 7100 og har ingen rader i
lisensiærtabellen 7108. Uten denne hardkodingen ville det verken hatt navn eller
eierandeler i appen.

**Problemet:** eierandelene 80/20 er skrevet inn for hånd, har ingen kilde, og
oppdateres aldri. `getShare` sjekker dette oppslaget *før* lisensiærdataene
(`index.html:711-713`), så hardkodingen vinner alltid. Og som vist i §5 finnes
det **43 andre** funn-carriers som ikke har fått samme behandling — to av dem med
faktisk produksjon (`25288497` = 7220/11-1 Alta, `44576` = 33/9-6 DELTA). De
dukker opp som `Field <id>` uten eierandeler.

Spesialtilfellet er spredt over elleve steder: `:694-701`, `:703-706`, `:711-712`, `:783-787`, `:868-869`, `:1065-1067`,
`:1661-1667`, `:3307-3309`, og i hvert av de fem `relevantIds`-uttrykkene
(`:2216-2218`, `:2289-2291`, `:2329-2331`, `:2510-2512`).

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

Hele cache-laget (`index.html:479-515`, `:810-816`, `:859`, `:917-922`, `:945`)
kjører aldri så lenge `data/`-filene finnes, fordi bundle-stien returnerer tidlig
(`index.html:800-805`, `:907-915`). `sessionClearProd()` kalles fortsatt ved
årsbytte (`:467`) og periodebytte (`:1091`, `:1103`), men er en no-op for
korrektheten. Koden er ikke feil — bare uvirksom.

### 11.10 Årsvelgeren overstyres ved periodebytte

Se §8.8. Bytter brukeren periode, mistes et manuelt satt `yearFrom`.
`setPeriod` tømmer i tillegg hele `fieldProdCache` (`:1092`, `:1104`) — harmløst
når bundelen finnes, men en full refetch i fallback-modus.

### 11.11 Inneværende måned vises ufullstendig

`isCompletePeriod` returnerer `true` for månedlig (`index.html:672`). Er kilden
delvis oppdatert for inneværende måned, vises et kunstig lavt tall som siste
søyle. Årlig og kvartalsvis er beskyttet; månedlig er det ikke.

### 11.12 Avrunding før aggregering

Alle tegnefunksjonene runder med `.toFixed(0)` per periode *før* verdiene havner
i `lastDatasets` og dermed i Excel. Summerer man mange felt, akkumuleres
avrundingsfeil på inntil 0,5 boe/dag per felt per periode. Ubetydelig for visning,
men merkbart hvis noen bruker Excel-eksporten til avstemming.

### 11.13 Én feilende kilde fryser alle fire datasettene

`main` returnerer 1 ved første feilende datasett, før noe skrives
(`fetch_sodir.py:494-500`). Det garanterer innbyrdes konsistens, men betyr at en
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

> **USIKKER:** Koden oppgir konsekvent «NLOD 2.0» (`fetch_sodir.py:48`,
> `index.html:2894`). FactPages' egen forsidetekst nevner NLOD uten
> versjonsnummer. NLOD 2.0 er gjeldende versjon, så påstanden er etter alt å
> dømme riktig, men jeg fant ingen eksplisitt versjonsangivelse hos Sodir som
> bekrefter det. Bør sjekkes mot Sodirs vilkårsside før publisering.

> **Merk forbeholdet om tredjepartsrettigheter** i sitatet over. Det er ikke
> vurdert i denne løsningen.

### Attribusjon som vises i dag

1. **I appen**, nederst på siden (`index.html:285`):
   > [Norwegian Offshore Directorate](https://factpages.sodir.no) – NLOD 2.0 · data per ÅÅÅÅ-MM-DD

   Datoen fylles inn fra `data/meta.json` (`index.html:374-376`).

2. **I Excel-eksporten**, på Info-arket (`index.html:2894`):
   > Norwegian Offshore Directorate (Sodir) FactPages – https://factpages.sodir.no – License: NLOD 2.0

3. **I datafilene**, i `meta.json` (`fetch_sodir.py:48`):
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
