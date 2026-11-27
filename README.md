# NCS Insight

Repo: `ncs-production`.

Webapp for norsk sokkel (Norwegian Continental Shelf): produksjon, reserver og
ressurser per felt, hub, funn og selskap, med profiler for felt, huber, funn,
selskaper, lisenser, brønner og rigger. I tillegg leting, rigger, lisenser og
overdragelser, normpriser og gasspriser. Seksjonene **DCS** og **UKCS** (lengst
til høyre i menyen) viser dansk og britisk sokkel med data fra Energistyrelsen
og North Sea Transition Authority, helt adskilt fra de norske dataene.

Hovedkilden er Sokkeldirektoratets (Sodir) åpne FactMaps-API. I tillegg brukes
Havtil, Kystverkets AIS, SSB, Petroleumsprisrådet, EIA, FRED/IMF, Norges Bank og
Natural Earth. De danske tallene i DCS kommer fra ENS-løsningen
([`sheffielddivided/ens`](https://github.com/sheffielddivided/ens)); de britiske i
UKCS hentes direkte fra NSTA. Alt lagres
som statiske JSON-filer i dette repoet.

**Live:** https://energy.kbak.no/

## Innhold

| Fil | Rolle |
|---|---|
| `index.html` | Hele frontenden. Én fil, vanilla JS, ingen byggesteg |
| `fetch_sodir.py` | Henter data fra Sodir og skriver `data/*.json` |
| `build_home.py` | Regner ut nøkkeltallene til startsiden fra filene i `data/` og skriver `data/home.json` (noen få kB). Startsiden viser dem med en gang, mens de store datafilene lastes i bakgrunnen. Kjøres sist i `update-sodir-data.yml`; bruker ikke nettet |
| `og-image.png` | Bildet som vises når en lenke til siden deles (Open Graph) |
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
| `data/pipelines.json` | Transportrørledninger (lag 6100) med attributter og forenklet trasé, til NCS → Gas exports → Infrastructure. Bygges av `fetch_sodir.py` |
| `data/facilities.json` | Innretninger per felt (lag 6000) — til hubprofilen |
| `data/exploration_wells.json` | Letebrønner (lag 5000) — planlagte, pågående og avsluttede, med eierskap per spud-dato. Lastes først når Exploration åpnes |
| `fetch_prospects.py` | Henter prospektnavn til letebrønner fra Sodirs RSS-feed «Exploration drilling results» (brønn fra tittelen, prospekt fra beskrivelsen) og bygger `data/exploration_prospects.json` — et arkiv som bare vokser. Brukes som navn på brønner uten funnnavn, typisk tørre |
| `data/exploration_name_overrides.json` | Navn på letebrønner satt for hånd i appen (Exploration → brønn → «Edit name»). Skrives rett til GitHub med samme token som hub-innstillingene og gjelder for alle. Går foran både funnnavn og prospektnavn; tomt navn fjerner overstyringen |
| `data/exploration_planned_manual.json` | Planlagte letebrønner lagt inn for hånd fra selskapenes egne planer (Exploration → Wells → «Company plans»), når de ligger lenger fram enn Sodirs data. Navn, operatør, lisens, formål, forventet start (år, halvår, kvartal, måned eller dato), rigg, kilde og merknad. Skrives rett til GitHub med samme token. Vises bare i brønnlisten, merket «Company plan», og skjules når Sodir har brønnen |
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
| `fetch_shipping.py` | Tankskip (skytteltankere) som laster på utvalgte felt, fra Kystverkets åpne AIS-API (Kystdatahuset): posisjoner rundt feltets innretninger fra 1. januar 2026, anløp (minst en halvtime innenfor radiusen med stopp eller minst to timer), skipenes navn og lengde, og forrige og neste stopp (havn, annet felt, sjø). Andre skip lagres ikke. Bygger `data/shipping.json` til Field → Shipping og Hub → Shipping. Fortsetter der forrige kjøring slapp, innenfor et tidsbudsjett (`SHIPPING_SECONDS`, standard 300) |
| `data/shipping_fields.json` | Feltene tankskipene hentes for, med valgfri radius i km. Redigeres for hånd; foreløpig Skarv, Alvheim, Norne, Åsgard, Balder (med Jotun), Goliat, Johan Castberg, Njord og Heidrun |
| `data/shipping.json` | Tankskipenes anløp per felt (`mmsi, start, end, minKm, open, from, to`) og skipene (navn, AIS-type, lengde, IMO) |
| `data/tanker_specs.json` | Oppgitt dødvekt (og eventuelt kapasitet) per tankskip, nøkkel IMO, med kilde. Redigeres for hånd; skip som mangler, får dødvekten anslått fra målene |
| `data/tanker_obs.json` | Dypgang og AIS-destinasjon for tankskipene, lest fra sanntids-AIS hver tredje time av `update-tankers.yml` (`fetch_shipping.py --realtime`). Gir anslått last per anløp |
| `data/shipping_log.json` | Arbeidsfil for fetch_shipping.py: åpne og avsluttede opphold per felt og skip, skipsregisteret og hvor langt hentingen har kommet |
| `fetch_gassco.py` | Henter Gasscos hendelser (UMM) fra umm.gassco.no — forsiden og arkivet, etter å ha godtatt vilkårene der — til arkivet `data/gassco_events.json`, og regner dem om til nedetid per felt i `data/gassco_downtime.json`. Kjøres daglig (arkivets siste 120 dager); `--backfill` leser alt fra 1. januar 2025 |
| `data/gassco_events.json` | Arkiv over Gasscos hendelser fra 2025, siste revisjon per hendelse. Bare vokser, så historikken beholdes når Gassco fjerner gamle hendelser (de holder om lag to år) |
| `data/gassco_downtime.json` | Dager og tapt gassvolum per felt og måned (planlagt og uplanlagt), fra januar 2025 og så langt fram som hendelsene går. Vises i Field, Hub, Company og NCS → Gas exports → Outages som volumeffekt: produksjon over null, tapt volum under, i boe/day eller Sm³/day |
| `data/gassco_aliases.json` | Gasscos anleggsnavn → feltnavn i Sodir, og anlegg som ikke er felt (prosessanlegg, terminaler). Redigeres for hånd |
| `fetch_ens.py` | Leser de publiserte JSON-filene fra ENS-løsningen (`sheffielddivided/ens`) og bygger `data/dcs.json` og `data/dcs_outlines.json` til DCS-seksjonen. Regner gass om fra Nm³ til Sm³. Ukjent format gir feil og exit 1 uten at noe skrives. `--local ../ens` leser fra en lokal kopi |
| `data/dcs.json` | Danske felt: operatør, gjeldende eierandeler og eierskap over tid (1999–), årlig (1972–) og månedlig (2018–) produksjon med foreløpig-flagg, reserver per 1. januar, og brønnene til havs (1966–) med type, felt og posisjon til DCS → Drilling og DCS → Map |
| `data/dcs_outlines.json` | Omriss av de danske feltene, til DCS → Map |
| `fetch_nsta.py` | Henter britiske data fra North Sea Transition Authority (NSTA): produksjon per felt og måned (PPRS), daterte eierandeler (Field Partners-arbeidsboken), feltomriss og gjeldende lisensunderområder med eiergrupper. Bygger `data/ukcs*.json` til UKCS-seksjonen. Bare offshorefelt; gasslagre utelates. For få rader eller ukjente kolonner gir exit 1 uten at noe skrives. Krever `requests` og `openpyxl` |
| `data/ukcs.json` | Britiske felt: område, status, operatør, gjeldende og historiske eierandeler per konsern, og månedlig produksjon (olje inkl. kondensat, gass, vann) fra juni 1975 i kompakt form |
| `data/ukcs_outlines.json` | Omriss av de britiske feltene (NSTA field determinations, forenklet) |
| `data/ukcs_licences.json` | Gjeldende britiske lisenser og underområder med eiergruppe, andel og omriss, til UKCS → Company |
| `canada.html` | Egen side for kanadisk sokkel, foreløpig bare Newfoundland and Labrador: Production, Fields (med faktaark), Resources, Company, Drilling og Map. Samme designsystem som `index.html`, men egen fil uten byggesteg. Lenkes fra menyen «Other regions» i `index.html` |
| `fetch_cnloer.py` | Henter data fra Canada-Newfoundland and Labrador Offshore Energy Regulator (C-NLOER): månedlig produksjon per felt og reservoar fra PDF-ene på statistikksiden (2002–; Hibernia 1997–2001 og North Amethyst 2010 summert fra produksjonen per brønn), reserver og betingede ressurser per felt og funn, og brønner, innretninger, lisenser med eierandeler og bassenger fra ArcGIS-huben. Kontrollerer månedene mot årstotalene, fat mot m³, feltene mot områdetotalen og brønntallene mot årsproduksjonen; avvik gir exit 1 uten at noe skrives. Krever `requests` og `pdftotext` (poppler-utils). `--cache D` gjenbruker nedlastede PDF-er |
| `data/canada_nl.json` | Newfoundland and Labrador: feltene med fakta, månedlig produksjon per reservoar (olje og vann i m³, gass i 1000 m³), reserver (1P/2P/3P, metrisk og C-NLOERs egne fat/bcf), betingede ressurser (2C) for felt og funn, innretninger, lisenser med dagens eierandeler (C-NLOER publiserer ikke historikk) og alle brønner til havs |
| `data/canada_nl_outlines.json` | Omriss av lisensene (PL, SDL, EL) og de sedimentære bassengene, til kartene i `canada.html` |
| `data/canada_nl_manual.json` | Håndført tillegg til `canada.html`, med kilde per oppføring: stans for FPSO-ombygging (skraveres i produksjonsgrafene), forventet levetid, reservoaregenskaper per formasjon (provinsens faktaark 2023), prosjekter under utvikling (Bay du Nord med milepæler) og navn på C-NLOERs landområder. Endres for hånd; `fetch_cnloer.py` rører den ikke |
| `data/canada_nl_coast.json` | Kystlinje for Newfoundland og Labrador (Natural Earth 10m), bygget én gang med `make_coastline.py --box` |
| `gom.html` | Egen side for amerikansk del av Mexicogolfen (føderalt område, BOEMs «Gulf of America OCS Region»): Production (vanndyp, felt, total), Fields (med faktaark), Reserves, Company (egenandel per selskap over tid etter lease-eierskapet, eller opererte volumer), Drilling og Map. Samme designsystem som `canada.html`. Lenkes fra menyen «Other regions» i `index.html` |
| `fetch_boem.py` | Henter fra BOEM Data Center: produksjon per brønnkomplettering (OGOR-A, 1947–) summert per felt, operatør, lease og totalt; reserveestimatene (siste utgave, tabell 4 og 9 og historikk per felt); brønner (borehole), innretninger, selskaper og dagens lease-eiere. Kontrollerer blant annet at akkumulert produksjon per felt stemmer med BOEMs reservetabell (290 av 293 store felt innenfor 3 %). `--cache D` gjenbruker filer BOEM ikke har endret. Krever `requests` og `openpyxl` |
| `data/gom.json` | Månedlig totalproduksjon fra 1947 (olje, gass, vann) og per vanndypsklasse, felt som rader (`fieldCols`) med vanndyp, funnår, operatør, reserver P10/P50/P90/snitt, produksjon siste 12 måneder og posisjon; opererte volumer per konsern fra 1985, netto produksjon etter dagens lease-eiere, reserver for hele området per år, stående innretninger og brønntall per år, type og vanndyp |
| `data/gom_prod.json` | Serier per felt fra 1990 (månedlig for felt i produksjon, årlig for avsluttede) og reservehistorikk per felt. Lastes først når gom.html trenger den |
| `data/gom_wells.json` | Brønner spuddet fra 2015 med detaljer, til Drilling, kart og faktaark. Lastes ved behov |
| `data/gom_coast.json` | Kystlinje for Mexicogolfen (Natural Earth 10m, `make_coastline.py --box`) |
| `brazil.html` | Egen side for Brasil, til havs og på land: Production (pre-salt/post-salt/land, basseng, delstat, felt, innretning; produserende brønner, gassreinjeksjon, operatørenes prognose, de mest produktive brønnene), Fields (faktaark med feltområder, rettighetshavere, volum i reservoaret, særskatt, innretninger og kart), Platforms (FPSO-er og andre produksjonsenheter med faktaark og hendelser), Reserves (1P/2P/3P over tid, pre-salt, per basseng, største felt etter olje i reservoaret), Company (egenandel med dagens andeler eller opererte volumer), Exploration (letebrønner og funn, nye produserende brønner, PAD-er, kommersialitetserklæringer), Licensing (blokker under kontrakt, lisensrunder, overdragelser), Safety (hendelser meldt til ANP) og Map. Samme designsystem som `gom.html`. Lenkes fra menyen «Other regions» i `index.html` |
| `fetch_anp.py` | Henter fra ANPs åpne data: produksjon per brønn til havs (1954–) og på land (1941–), arkivet per brønn 2005–2023 (pre-salt-brønner, operatør, kontrakt, API), offisielle totaler per delstat (kontroll), rettighetshavere, BAR-tabellene og reserveboletinen (PDF), letepanelet, lisensrunder, overdragelser, særskatt per felt, hendelser, prognosen og geodata (shapefiler for felt, blokker og brønner, 2023). Kontrollerer at summen per brønn stemmer med ANPs totaler (1 %), og at dagens andeler dekker minst 90 % av produksjonen. `--cache D` gjenbruker filer som ikke endres. Krever `requests`, `openpyxl`, `pyshp` og `pdftotext` (poppler-utils) |
| `data/brazil.json` | Brasil: månedlige totaler fra 1941 (olje, gass, vann) per hav/land, pre-salt, basseng og delstat; gassreinjeksjon, produserende og nye brønner, de mest produktive brønnene; felt og innretninger som rader (`fieldCols`, `instCols`); egenandel og opererte volumer per konsern fra 2005; reserver (bassenger, historikk 2016–, BAR); prognose; leteoversikt og hendelsesstatistikk |
| `data/brazil_prod.json` | Serier per felt (årlig før 2000, månedlig fra 2000, produserende brønner siste ti år) og per innretning (månedlig fra 2010). Lastes ved behov |
| `data/brazil_expl.json` | Blokker under kontrakt med andeler, letebrønner med funn (1998–), PAD-er, kommersialitetserklæringer, blokker tilbudt og tildelt i runde 9–17, og overdragelser (2001–). Lastes ved behov |
| `data/brazil_wells.json` | Brønner med posisjon fra ANPs brønnregister (shapefil 2023) til kartene: alle til havs, og lete- og avgrensningsbrønner på land |
| `data/brazil_incidents.json` | Hendelser meldt til ANP de siste to årene, med innretning, selskap, type, alvorlighet og operatørens beskrivelse |
| `data/brazil_outlines.json` | Omriss av felt og blokker under kontrakt (ANPs shapefiler, august 2023, forenklet) |
| `data/brazil_coast.json` | Kystlinje for Brasil (Natural Earth 10m, `make_coastline.py --box`) |
| `data/brazil_aliases.json` | Valgfri, håndført: `fieldContracts` kobler felt som ikke finnes i feltomrissene eller arkivet til en kontrakt (nummer eller navn i relação de concessionários). `fetch_anp.py` leser den hvis den finnes |
| `angola.html` | Egen side for Angola: Production (olje per blokk og år fra 1955 med valgfritt startår: landets total før 2008, per blokk fra 2008 (rekonstruert fra Equinor 2008–2015 og 2023–2024); boe/dag, ingen enhetsvalg; gassbruk, Angola LNG, driftseffektivitet, boreaktivitet, månedsrapportene), Blocks (produserende blokker med faktaark), Company (egenandel med dagens andeler), Licensing (alle blokker i ANPGs register) og Map (blokkenes midtpunkter, gassinfrastruktur, seismikk). Samme designsystem som `brazil.html`. Lenkes fra menyen «Other regions» i `index.html` |
| `fetch_anpg.py` | Henter fra JSON-API-et bak ANPGs nettsted: månedsrapportene (tallene leses ut av teksten, blokktabellen fra HTML-en), strukturerte månedstall, årsproduksjon fra 1955, blokkregisteret, seismikk og gasskartet. Kontrollerer tekst mot strukturerte tall, blokktabell mot total og måneder mot årstall. Krever `requests` |
| `data/angola.json` | Angola: månedsserier fra januar 2020 (olje, prognose, landets andel, gass og bruken av den, fakling, Angola LNG, effektivitet, løftinger, rigger, brønnarbeid), årsproduksjon fra 1955, olje og gass per blokk fra september 2024, egenandel per konsern, blokkregisteret (211 blokker), seismikk og gassinfrastruktur |
| `data/angola_coast.json` | Kystlinje for Angola (Natural Earth 10m, `make_coastline.py --box`) |
| `data/angola_geo.json` | Kartlag fra ANPGs interaktive konsesjonskart (`gis.anpg.co.ao`, ArcGIS): blokkomriss med status, operatør og partnere, felt og utbyggingsområder, felt og funn, 3 100 brønner med spud-dato og status, FPSO-er og plattformer, rørledninger fra 5 km. Bygges av `fetch_anpg.py`; feiler kartlagene, beholdes forrige fil |
| `build_angola_fields.py` | Leser Equinors regneark «Production per field and asset» (investorsidene, ett ark per kvartal fra 4. kv. 2007) i `data/equinor-raw/` og bygger `data/angola_fields.json`: produksjon per produksjonssenter i Blokk 15, 17 og 31 (Equinors egenandel delt på eierandelen). Kontrollerer mot ANPGs blokktall. Kjøres for hånd når et nytt kvartal er lastet ned |
| `data/equinor-raw/` | Equinors regneark (lastes ned for hånd; sperret for nettstedet som andre `-raw`-mapper) |
| `data/angola_fields.json` | Angola per felt og kvartal fra 2007: Kizomba A/B, Marimba, Kizomba-satellittene, Mondo, Saxi-Batuque, Girassol, Dália, Rosa, Pazflor, CLOV og PSVM (hele feltet, tusen fat o.e. per dag) |
| `build_angola_history.py` | Leser tabellene med produksjon per blokk i Sonangols årsrapporter (2016–2018), ANPGs årsrapporter (2019–2020) og EITI-rapportene (2021–2022) og bygger `data/angola_history.json`. Kontrollerer kvartaler mot år, blokker mot total og totalen mot ANPGs årstall. Kjøres for hånd (`--cache D`); krever `pdftotext` |
| `data/angola_history.json` | Angola: olje per blokk per kvartal 2016–2020 og per år 2016–2022 (fat per dag), Blokk 0 delt i område A og B, og partneres bruttotall for 2023–2024 (Afentra, Blokk 3/05). Siden fyller 2023–2024 for Blokk 15, 17 og 31 fra `angola_fields.json` |
| `cloudflare/anp-proxy/` | Cloudflare Worker som henter ANPs filer for `update-anp.yml`, fordi gov.br bryter tilkoblinger fra GitHub Actions. Bare GET med token, bare `https://www.gov.br/anp/`. Oppsett i mappens README; `fetch_anp.py` bruker den når `ANP_PROXY` og `ANP_PROXY_TOKEN` er satt. Holdes utenfor nettstedet (`.htaccess`, FTP-publiseringen) |
| `updates.html` | Oppdateringsloggen: hva som er endret i dataene, dag for dag, gruppert per seksjon (NCS, aktivitet, nyheter, markeder, andre sokler, endringer gjort i appen), med høydepunkter eller alt. Lenket nederst på forsiden |
| `build_update_log.py` | Bygger `data/update_log.json` fra git-historikken: for hver dag sammenlignes hver datafil med hvordan den sto ved slutten av dagen før, og det skrives én linje per kilde («Production: September 2026 published; 9 fields revised», «2 new wells: …», «3 new Havtil cases»). Kjøres av `update-log.yml` etter hver oppdateringsworkflow og regner de siste tre dagene på nytt; `--backfill N` bygger hele loggen |
| `data/update_log.json` | Oppdateringsloggen for de siste 180 dagene: per dag linjer med kilde, seksjon, tekst og tidspunktene (UTC) for committene |
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
| `data/baa.json` | Forretningsavtaleområder (units m.m.): fakta, eierandeler over tid, operatører og overdragelser (lag 3300/3304/3302/3305) — til unit-faktaarket, Transactions og selskapenes transaksjonskort. Lastes lazy |
| `data/companies.json` | Selskapsfakta (lag 1200) — konsern, orgnr., roller |
| `data/coastline.json` | Kystlinje til profilkartet — Natural Earth, bygget én gang |
| `make_coastline.py` | Engangsscript som bygger `data/coastline.json`; med `--box` og `--out` også andre utsnitt (`data/canada_nl_coast.json`) |
| `seed_hubs.py` | Engangsscript som foreslår hub-mapping fra Sodirs feltbeskrivelser |
| `.github/workflows/update-sodir-data.yml` | Kjører hentejobben daglig |
| `.htaccess` | Apache-oppsett for energy.kbak.no: skjuler skript og rådata, kort cache og komprimering av datafilene |
| `data/normpris-raw/` | Normpris-xlsx fra regjeringen.no (lastes ned manuelt) |
| `fetch_gas.py` | Henter månedlige gasspriser (SSB eksportpris, IMF Europa, Henry Hub, Norges Bank-kurser) til `data/gas_prices.json` |
| `fetch_nve.py` | Leser de publiserte JSON-filene fra nve-løsningen (`sheffielddivided/nve`): NVEs magasinstatistikk og ENTSO-Es day-ahead-priser, og bygger `data/power.json` til Macro → NO power. Ukjent format gir exit 1 uten at noe skrives. `--local ../nve` leser fra en lokal kopi |
| `data/power.json` | Magasinfylling per uke for Norge og NO1–NO5 (inneværende år og de 20 foregående), ukepriser for NO1–NO5 og Tyskland fra 2015, og netto eksport per utenlandsforbindelse (MW, ukesnitt) fra 2015 |
| `fetch_eugas.py` | Henter data fra eia-repoet (`sheffielddivided/eia`) til Macro: TTF-priser, gasslagre (`data/eu_storage.json`), LNG-utsendelse (`data/eu_lng.json`), amerikansk forsyning, raffineringspriser, STEO-oljebalansen (`data/steo.json`) med tidligere STEO-utgaver fra EIAs arkiv (`data/steo_vintages.json`, siste to år, til endringsmodusen «Δ vs. earlier STEO» i Oil market) og gass inn i EU etter opprinnelse og russisk rørgass per rute (`data/eu_gas_flows.json`, fra eia-repoets `gas_flows.json`). Feiler en henting, beholdes forrige fil |
| `fetch_fx.py` | Henter daglige valutakurser fra Norges Bank (NOK, USD, EUR, GBP, SEK, DKK, CHF, CAD) til `data/fx.json`, Forex-fanen i Macro |
| `fetch_rates.py` | Bygger `data/rates.json` (styringsrente, inflasjon og rentekurver for NO, US, EU, UK, SE) fra convert-repoets nb-data, pluss Feds målintervall og Treasury-kurven, til Interest-fanen i Macro |
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

Siden publiseres bare på **energy.kbak.no** (Servetheworld, Plesk). Plesks
Git-utvidelse henter repoet til `httpdocs` hver gang `main` får en ny commit
(webhook i GitHub → Plesk). Det gjelder også de daglige datacommitene fra
workflowene. `.htaccess` sperrer skript, workflows og rådata og setter cache og
komprimering. GitHub Pages er slått av.

Redigering i appen (huber, feeder, brønnavn) skriver fortsatt til GitHub, og
endringen når energy.kbak.no via samme webhook.

**Reserve hvis Git i Plesk faller ut:** Actions → *Publiser via FTP (reserve)* →
*Run workflow* (`.github/workflows/deploy-ftp.yml`). Den laster opp over FTPS
det som er endret siden forrige FTP-publisering (commiten står i
`.deploy-commit` på serveren), eller alt ved første kjøring eller med *full*
krysset av. Skript, dokumentasjon, workflows, `counter/` og `data/*-raw/` holdes
utenfor. *dry_run* viser fillisten uten å laste opp. Secrets: `DEPLOY_HOST`
(`plesk1.lg.dynavee.net`, navnet i FTP-sertifikatet), `DEPLOY_USER`,
`DEPLOY_PASSWORD` og `DEPLOY_PATH` (`/`, FTP-brukerens hjemmemappe).

## Passord for Valuation BETA

Fanene **Valuation BETA** under Company, Hub og Field krever passord. Passordet ligger
bare i GitHub-secreten `BETA_PASSWORD`. Workflowen
`.github/workflows/beta-password.yml` skriver en saltet PBKDF2-SHA256-hash av det
til `data/beta_gate.json`, og appen sjekker det inntastede passordet mot hashen.

Slik setter eller bytter du passordet:

1. GitHub → Settings → Secrets and variables → Actions → **New repository
   secret** (eller oppdater `BETA_PASSWORD`).
2. Actions → **Sett passord for Valuation BETA** → **Run workflow**.

Secrets utløser ingen kjøring selv, så steg 2 må gjøres hver gang. Hver kjøring
lager nytt salt, så alle må taste passordet på nytt. Nettleseren husker at den er
låst opp (localStorage). Sperren holder nysgjerrige ute, men er ingen
tilgangskontroll: koden og dataene er offentlige og kan leses i nettleseren.

## Redigere huber

En hub er en gruppe felt som deler prosesseringsanlegg. Åpne **Hubs**-visningen
og klikk tannhjulet øverst til høyre. Endringer lagres direkte til
`data/hubs.json` og gjelder for alle når energy.kbak.no har hentet commiten.

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

Britiske data (UKCS): [North Sea Transition Authority (NSTA)](https://www.nstauthority.co.uk/data-and-insights/data/themes/fields/) —
PPRS-produksjonsrapporter, Field Partners, field determinations og lisensunderområder.

Kanadiske data (canada.html): [Canada-Newfoundland and Labrador Offshore Energy Regulator (C-NLOER)](https://www.cnloer.ca/information/statistics/) —
produksjonsstatistikk, ressursanslag og [Data and Information Hub](https://home-cnlopb.hub.arcgis.com/).
Oppdateres av `update-cnloer.yml` mandag og torsdag.

Amerikanske data (gom.html): [Bureau of Ocean Energy Management (BOEM) Data Center](https://www.data.boem.gov/) —
OGOR-A-produksjon, reserveestimater, brønner, innretninger og lease-eiere for Gulf of America OCS Region
(føderalt område; delstatsfarvann er ikke med). Oppdateres av `update-boem.yml` hver lørdag.

Tilsynssaker: [Havindustritilsynet (Havtil)](https://www.havtil.no). Havtils
materiale kan gjenbrukes fritt i alle formater og medier når Havtil krediteres.

## Normpriser (Petroleumsprisrådet)

Månedlige normpris-differensialer per felt ($/fat, relativt til Datert Brent)
og daglig Datert Brent, hentet fra kvartalsvise xlsx-filer på
[regjeringen.no](https://www.regjeringen.no/no/tema/energi/olje-og-gass/petroleumsprisradet--og-fastsetting-av-n/id661459/).
Siden ligger bak Cloudflare og kan ikke hentes automatisk: last ned nye filer
og last dem opp i appen (Macro → Oil → tannhjulet, samme GitHub-token som
hub-innstillingene), eller legg dem i `data/normpris-raw/` og push til `main`.
Appen sjekker filene i nettleseren med de samme reglene som importen, skriver
dem i én commit og viser resultatet når jobben er ferdig. Workflowen
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
