# ncs-production

Webapp som viser produksjonshistorikk på norsk sokkel (Norwegian Continental
Shelf) som stablede søylediagram i boe/dag, per felt eller per selskap.

Data hentes fra Sokkeldirektoratets (Sodir) åpne FactMaps-API og lagres som
statiske JSON-filer i dette repoet.

**Live:** https://sheffielddivided.github.io/ncs-production/

## Innhold

| Fil | Rolle |
|---|---|
| `index.html` | Hele frontenden. Én fil, vanilla JS, ingen byggesteg |
| `fetch_sodir.py` | Henter data fra Sodir og skriver `data/*.json` |
| `data/` | Datasnapshot: felt, produksjon, lisensiærer, reserver |
| `data/hubs.json` | Hub-tilhørighet per felt — brukerstyrt, ikke fra Sodir |
| `seed_hubs.py` | Engangsscript som foreslår hub-mapping fra Sodirs feltbeskrivelser |
| `.github/workflows/update-sodir-data.yml` | Kjører hentejobben daglig |
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

## Dokumentasjon

Se [`SOLUTION-CONTEXT.md`](SOLUTION-CONTEXT.md) for datamodell, enheter og
konverteringer, forretningslogikk og kjente fallgruver.

## Kilde og lisens

Data: [Sokkeldirektoratet (Sodir) FactPages](https://factpages.sodir.no) —
brukt under [NLOD](https://data.norge.no/nlod/en).

Sodir gir ingen garanti for at dataene er korrekte eller komplette.
