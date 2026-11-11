# NCS Insight

Repo: `ncs-production`.

Webapp for norsk sokkel (Norwegian Continental Shelf): produksjon, reserver og
ressurser per felt, hub, funn og selskap, med profiler for felt, huber, funn,
selskaper, lisenser, brønner og rigger. I tillegg leting, rigger, lisenser og
overdragelser, normpriser og gasspriser. Seksjonen **DCS** (lengst til høyre i
menyen) viser dansk sokkel med data fra Energistyrelsen, helt adskilt fra de
norske dataene.

Hovedkilden er Sokkeldirektoratets (Sodir) åpne FactMaps-API. I tillegg brukes
Havtil, Kystverkets AIS, SSB, Petroleumsprisrådet, EIA, FRED/IMF, Norges Bank og
Natural Earth. De danske tallene i DCS kommer fra ENS-løsningen
([`sheffielddivided/ens`](https://github.com/sheffielddivided/ens)). Alt lagres
som statiske JSON-filer i dette repoet.

**Live:** https://energy.kbak.no/ (speil: https://sheffielddivided.github.io/ncs-production/)

## Innhold

| Fil | Rolle |
|---|---|
| `index.html` | Hele frontenden. Én fil, vanilla JS, ingen byggesteg |
| `fetch_sodir.py` | Henter data fra Sodir og skriver `data/*.json` |
| `data/` | Datasnapshot: felt, produksjon, lisensiærer, reserver |
| `data/reserves_field.json` | Reserver per felt per år (2004→), fra Sodir lag 7113 |
| `data/hubs.json` | Hub-tilhørighet per felt — brukerstyrt, ikke fra Sodir |
| `data/hub_connections.json` | Fysiske koblinger per hub (rørledninger, broer, brønner fra plattform, historiske) til hub- og feltkartet. Redigeres for hånd; en hub som står her, tegnes etter modellen i stedet for de automatiske koblingene fra feltbeskrivelsene. Foreløpig Sleipner area og Alvheim area. Skiller bekreftede koblinger fra skjematiske (prikket) |
| `data/discoveries.json` | Funn (lag 7000) — navn, status, funnår, eier, operatør |
| `data/discovery_resources.json` | Ressursanslag per funn (lag 7008) |
| `data/discovery_descriptions.json` | Sodirs funnbeskrivelser (lag 7001) |
| `data/discovery_outlines.json` | Forenklede funnomriss til funnprofilens kart |
| `data/licence_licensees.json` | Eierandeler i lisenser og forretningsavtaleområder |
| `data/field_profile.json` | Faktaark per felt — funnår, operatør, produksjonsstart |
| `data/field_descriptions.json` | Sodirs feltbeskrivelser (lag 7102) |
| `data/field_outlines.json` | Forenklede feltomriss til profilkartet |
| `data/facilities.json` | Innretninger per felt (lag 6000) — til hubprofilen |
| `data/exploration_wells.json` | Letebrønner (lag 5000) — planlagte, pågående og avsluttede, med eierskap per spud-dato. Lastes først når Exploration åpnes |
| `fetch_prospects.py` | Henter prospektnavn til letebrønner fra Sodirs RSS-feed «Exploration drilling results» (brønn fra tittelen, prospekt fra beskrivelsen) og bygger `data/exploration_prospects.json` — et arkiv som bare vokser. Brukes som navn på brønner uten funnnavn, typisk tørre |
| `data/exploration_name_overrides.json` | Navn på letebrønner satt for hånd i appen (Exploration → brønn → «Edit name»). Skrives rett til GitHub med samme token som hub-innstillingene og gjelder for alle. Går foran både funnnavn og prospektnavn; tomt navn fjerner overstyringen |
| `data/rig_wells.json` | Utviklingsbrønner (lag 5000, siste to år) til riggoversikten i Exploration |
| `fetch_rigs.py` | Henter AIS-posisjoner for borerigger fra Kystverkets åpne API og bygger `rig_*.json` |
| `data/rig_ais.json` | Kobling riggnavn (Sodir) til MMSI/IMO — redigeres for hånd |
| `import_fleet_report.py` | Riggkontrakter til `data/rig_contracts.json`: fleet status reports som PDF (Noble, Transocean; for hånd) og tidslinjene på Odfjells og COSLs nettsider (`--web`, daglig) |
| `data/rig_contracts.json` | Riggkontrakter (kunde, periode, opsjoner, dagrate, kilde) med historikk — fra rapportene og manuelle rader |
| `data/rig_tracks.json` | Veipunkter og opphold per rigg, til riggprofilen med avspilling |
| `data/rig_now.json` | Dagens posisjon, status og AIS-destinasjon per rigg, til flåtekartet |
| `fetch_havtil.py` | Henter tilsynssaker fra Havtils RSS-feed, listesider og saksider, kobler dem til felt og selskap og bygger `data/havtil.json`. `--backfill` leser alle listesidene (tilbake til 2012–2014) |
| `data/havtil.json` | Arkiv over tilsynsrapporter, samtykker, granskinger og pålegg fra Havtil, koblet til felt og til juridisk enhet i `companies.json`. Vises i felt-, hub-, brønn- og riggprofilen og i Companies → News (selskap gruppert per konsern som i Licences), og lastes først når en av dem åpnes. Nye saker legges også i `changes.json` og vises i News |
| `data/havtil_aliases.json` | Navn Havtil bruker som ikke finnes i Sodir-dataene (f.eks. Yggdrasil), skrivefeil og tvetydige selskapsnavn, og manuelle unntak — redigeres for hånd |
| `fetch_company_news.py` | Henter Sodirs pressemeldinger (boretillatelser, boreresultater, generelle nyheter) og selskapenes egne RSS/Atom-feeder til Companies → News. `--sodir` / `--companies` kjører bare den ene delen |
| `data/sodir_press.json` | Arkiv over Sodirs pressemeldinger med brønner, felt og selskaper hentet ut av teksten. Frontend kobler dem til selskapet via rollen i brønnen, andel i feltet eller navnet |
| `data/company_feeds.json` | Selskapenes nyhetsfeeder. Redigeres fra tannhjulet i Companies → News (samme token som hub-innstillingene) eller for hånd; en endring starter `update-company-news.yml`, som henter de nye feedene med en gang, og appen viser resultatet så snart jobben har committet |
| `data/company_news.json` | Siste saker (inntil 100 per feed) og status per feed fra feedene i `company_feeds.json` |
| `fetch_gassco.py` | Henter Gasscos hendelser (UMM) fra umm.gassco.no — forsiden og arkivet, etter å ha godtatt vilkårene der — til arkivet `data/gassco_events.json`, og regner dem om til nedetid per felt i `data/gassco_downtime.json`. Kjøres daglig (arkivets siste 120 dager); `--backfill` leser alt fra 1. januar 2025 |
| `data/gassco_events.json` | Arkiv over Gasscos hendelser fra 2025, siste revisjon per hendelse. Bare vokser, så historikken beholdes når Gassco fjerner gamle hendelser (de holder om lag to år) |
| `data/gassco_downtime.json` | Dager og tapt gassvolum per felt og måned (planlagt og uplanlagt), fra januar 2025 og så langt fram som hendelsene går. Vises i Field, Hub, Company og NCS → Gas outage som volumeffekt: produksjon over null, tapt volum under, i boe/day eller Sm³/day |
| `data/gassco_aliases.json` | Gasscos anleggsnavn → feltnavn i Sodir, og anlegg som ikke er felt (prosessanlegg, terminaler). Redigeres for hånd |
| `fetch_ens.py` | Leser de publiserte JSON-filene fra ENS-løsningen (`sheffielddivided/ens`) og bygger `data/dcs.json` og `data/dcs_outlines.json` til DCS-seksjonen. Regner gass om fra Nm³ til Sm³. Ukjent format gir feil og exit 1 uten at noe skrives. `--local ../ens` leser fra en lokal kopi |
| `data/dcs.json` | Danske felt: operatør og gjeldende eierandeler, årlig (1972–) og månedlig (2018–) produksjon med foreløpig-flagg, og reserver per 1. januar |
| `data/dcs_outlines.json` | Omriss av de danske feltene, til DCS → Map |
| `changes.py` | Endringslogg: hva som er nytt siden forrige datakjøring (brukes av `fetch_sodir.py` og `fetch_rigs.py`) |
| `data/changes.json` | Siste 180 dagers hendelser: nye planlagte brønner, starter, avslutninger, funn, utsettelser og rigger som har flyttet seg. Vises i fanen News |
| `data/rig_health.json` | Driftsrapport fra AIS-steget: rigger uten kobling, rigger som bores uten signal, mistenkelige MMSI og om kilden feiler |
| `data/rig_log.json` | Arbeidsfil for fetch_rigs.py: tynnede posisjoner utover det sporene dekker |
| `data/licences.json` | Aktive utvinningstillatelser (lag 3000) |
| `data/licence_outlines.json` | Forenklede lisensomriss til Licences-kartene. Lastes først når Licences åpnes |
| `data/licence_baa.json` | Hvilke forretningsavtaleområder (BAA) hver lisens inngår i (lag 3301) |
| `data/licence_all.json` | Alle 1 813 lisenser noensinne, med runde, areal og status — til Rounds og lisensprofilen. Lastes lazy |
| `data/licence_history.json` | Eierandeler og operatører med full historikk (lag 3007/3008) — til Rounds, Map og lisensprofilen. Lastes lazy |
| `data/licence_transfers.json` | Overdragelser (lag 3003), parret selger→kjøper — til Transactions, Map og lisensprofilen. Lastes lazy |
| `data/licence_areas.json` | Lisensenes areal og omriss over tid (lag 3002) — til Rounds, Map og lisensprofilen. Lastes lazy |
| `data/companies.json` | Selskapsfakta (lag 1200) — konsern, orgnr., roller |
| `data/coastline.json` | Kystlinje til profilkartet — Natural Earth, bygget én gang |
| `make_coastline.py` | Engangsscript som bygger `data/coastline.json` |
| `seed_hubs.py` | Engangsscript som foreslår hub-mapping fra Sodirs feltbeskrivelser |
| `.github/workflows/update-sodir-data.yml` | Kjører hentejobben daglig |
| `.htaccess` | Apache-oppsett for energy.kbak.no: skjuler skript og rådata, kort cache og komprimering av datafilene |
| `data/normpris-raw/` | Normpris-xlsx fra regjeringen.no (lastes ned manuelt) |
| `fetch_gas.py` | Henter månedlige gasspriser (SSB eksportpris, IMF Europa, Henry Hub, Norges Bank-kurser) til `data/gas_prices.json` |
| `fetch_cpi.py` | Henter konsumprisindekser (US CPI-U, euroområdets HICP, SSBs KPI) til `data/cpi.json`, realprisene i Prices |
| `fetch_eia.py` | Henter daglig Brent spot fra 1990 (EIA RBRTE, ev. FRED) til `data/brent_eia.json`. Skjøter Datert Brent bakover før 2011 og framover etter Petroleumsprisrådets siste dato |
| `fetch_normpris.py` | Bygger `data/normpris_*.json` fra xlsx-filene |
| `data/normpris_brent.json` | Daglig Datert Brent, $/fat |
| `data/normpris_diff.json` | Månedlige normpris-differensialer per felt, $/fat |
| `normpris.html` | Sender videre til modusen Prices i `index.html`, der normprisene nå vises |
| `normpris-korrelasjon.html` | Studie: korrelasjon mellom feltenes differensialer (beregnes i nettleseren fra `normpris_diff.json`) |
| `.github/workflows/update-normpris.yml` | Bygger normpris-JSON når rådata endres |
| `SOLUTION-CONTEXT.md` | Teknisk dokumentasjon — datamodell, enheter, fallgruver |

## Kjøre lokalt

```bash
# Oppdater datafilene
pip install requests
python fetch_sodir.py

# Server frontenden (må gå over HTTP — den fetcher data/*.json)
python3 -m http.server 8000
# -> http://localhost:8000/index.html
```

Mangler `data/`-filene, faller appen automatisk tilbake til å kalle
Sodir-APIet direkte fra nettleseren.

## Dataoppdatering

Workflowen kjører 04:15 UTC hver dag og committer **kun** når Sodir faktisk har
publisert nye tall. Sodir oppdaterer produksjonstall månedlig, så de fleste
kjøringene blir en no-op. Jobben kan også kjøres manuelt fra Actions-fanen.

## Publisering

Siden publiseres to steder fra `main`:

- **energy.kbak.no** (Servetheworld, Plesk): Plesks Git-utvidelse henter
  repoet til `httpdocs` hver gang `main` får en ny commit (webhook i GitHub →
  Plesk). Det gjelder også de daglige datacommitene fra workflowene.
  `.htaccess` sperrer skript, workflows og rådata og setter cache og
  komprimering.
- **GitHub Pages:** som før.

Redigering i appen (huber, feeder, brønnavn) skriver fortsatt til GitHub, og
endringen når energy.kbak.no via samme webhook.

## Redigere huber

En hub er en gruppe felt som deler prosesseringsanlegg. Åpne **Hubs**-visningen
og klikk tannhjulet øverst til høyre. Endringer lagres direkte til
`data/hubs.json` og gjelder for alle når GitHub Pages har publisert.

Første gang du lagrer blir du bedt om et
[fine-grained token](https://github.com/settings/personal-access-tokens/new)
med `Contents: read and write` på dette repoet. Det lagres kun i din egen
nettleser.

`seed_hubs.py` kan generere et nytt forslag fra Sodirs feltbeskrivelser, men
bevarer alt du har bekreftet manuelt. Den kjøres aldri automatisk.

## Dokumentasjon

Se [`SOLUTION-CONTEXT.md`](SOLUTION-CONTEXT.md) for datamodell, enheter og
konverteringer, forretningslogikk og kjente fallgruver.

## Kilde og lisens

Data: [Sokkeldirektoratet (Sodir) FactPages](https://factpages.sodir.no) —
brukt under [NLOD](https://data.norge.no/nlod/en).

Sodir gir ingen garanti for at dataene er korrekte eller komplette.

Danske data (DCS): [Energistyrelsen (ENS)](https://ens.dk/en/energy-sources/monthly-and-yearly-production),
via ENS-løsningen [`sheffielddivided/ens`](https://github.com/sheffielddivided/ens).

Tilsynssaker: [Havindustritilsynet (Havtil)](https://www.havtil.no). Havtils
materiale kan gjenbrukes fritt i alle formater og medier når Havtil krediteres.

## Normpriser (Petroleumsprisrådet)

Månedlige normpris-differensialer per felt ($/fat, relativt til Datert Brent)
og daglig Datert Brent, hentet fra kvartalsvise xlsx-filer på
[regjeringen.no](https://www.regjeringen.no/no/tema/energi/olje-og-gass/petroleumsprisradet--og-fastsetting-av-n/id661459/).
Siden ligger bak Cloudflare og kan ikke hentes automatisk: last ned nye filer
manuelt til `data/normpris-raw/`, og push til `main`. Workflowen
`update-normpris.yml` kjører da `fetch_normpris.py` (`pip install openpyxl`),
som også kan kjøres lokalt.

- `normpris_brent.json`: `{"columns":["date","usd"],"rows":[["2021-01-01",54.3],...]}`
- `normpris_diff.json`: `{"months":["2011-01",...,"2026-03"],"fields":{"TROLL":{"2011-01":2.7,...}},"split":{"ALVHEIM":["2020-04"]}}`.
  Måneder uten verdi finnes ikke under feltet (trolig ikke solgt olje), så
  grafer kan bruke `months` som akse og tegne hull. `split` lister felt-måneder
  der kilden har flere differensialer i måneden (merket `*`, delperioder i
  2018 og 2020); de har ingen månedsverdi og er ikke importert. Feltnavn er som i filene
  (store bokstaver), ikke Sodir-id.
- Tilleggsfiler (`...-troll-februar.xlsx` o.l.) retter eller fyller ut enkelte
  felt/måneder og overstyrer hovedfilen. Importen feiler uten å skrive noe hvis
  et ark ikke ser ut som forventet.
- Daglige normpriser per felt, valutakurser og Draugens løftebaserte
  differensialer (arket `DRA` i 2022-filene) importeres ikke.
- Foreløpig tall: 3Q 2011 finnes bare som «foreløpige differensialer».
- Filnavnene varierer (2011-q1, np-q3-2016, ...). Hvilke filer som er
  hovedfiler og hvilke som er rettelser avgjøres av innholdet: filer med flest
  verdier leses først, mindre filer overstyrer. 4Q 2015 mangler dollar-blokk i
  feltarkene og er derfor ikke kryssjekket.
- Kjente avvik i kilden: STATFJORD februar 2011 er 0,0 i Differensialer, men
  -0,4 i feltarket (tabellen følges). KNARR juli 2021 er tom i Differensialer, men feltarket
  har priser for måneden. Tabellen følges, så måneden mangler.
