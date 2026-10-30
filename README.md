# NCS Insight

Repo: `ncs-production`.

Webapp for norsk sokkel (Norwegian Continental Shelf): produksjon, reserver og
ressurser per felt, hub, funn og selskap, med profiler for felt, huber, funn,
selskaper, lisenser, brønner og rigger. I tillegg leting, rigger, lisenser og
overdragelser, normpriser og gasspriser.

Hovedkilden er Sokkeldirektoratets (Sodir) åpne FactMaps-API. I tillegg brukes
Havtil, Kystverkets AIS, SSB, Petroleumsprisrådet, EIA, FRED/IMF, Norges Bank og
Natural Earth. Alt lagres som statiske JSON-filer i dette repoet.

**Live:** https://sheffielddivided.github.io/ncs-production/

## Innhold

| Fil | Rolle |
|---|---|
| `index.html` | Hele frontenden. Én fil, vanilla JS, ingen byggesteg |
| `fetch_sodir.py` | Henter data fra Sodir og skriver `data/*.json` |
| `data/` | Datasnapshot: felt, produksjon, lisensiærer, reserver |
| `data/reserves_field.json` | Reserver per felt per år (2004→), fra Sodir lag 7113 |
| `data/hubs.json` | Hub-tilhørighet per felt — brukerstyrt, ikke fra Sodir |
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
| `data/rig_wells.json` | Utviklingsbrønner (lag 5000, siste to år) til riggoversikten i Exploration |
| `fetch_rigs.py` | Henter AIS-posisjoner for borerigger fra Kystverkets åpne API og bygger `rig_*.json` |
| `data/rig_ais.json` | Kobling riggnavn (Sodir) til MMSI/IMO — redigeres for hånd |
| `data/rig_tracks.json` | Veipunkter og opphold per rigg, til riggprofilen med avspilling |
| `data/rig_now.json` | Dagens posisjon, status og AIS-destinasjon per rigg, til flåtekartet |
| `fetch_havtil.py` | Henter tilsynssaker fra Havtils RSS-feed, listesider og saksider, kobler dem til felt og selskap og bygger `data/havtil.json`. `--backfill` leser alle listesidene (tilbake til 2012–2014) |
| `data/havtil.json` | Arkiv over tilsynsrapporter, samtykker, granskinger og pålegg fra Havtil, koblet til felt og til juridisk enhet i `companies.json`. Vises i felt-, hub-, selskaps-, brønn- og riggprofilen (selskap gruppert per konsern som i Licences), og lastes først når en av dem åpnes. Nye saker legges også i `changes.json` og vises i News |
| `data/havtil_aliases.json` | Navn Havtil bruker som ikke finnes i Sodir-dataene (f.eks. Yggdrasil), skrivefeil og tvetydige selskapsnavn, og manuelle unntak — redigeres for hånd |
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
