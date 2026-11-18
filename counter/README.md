# Besøksteller (ncs-io.kbak.no)

Egen, personvernvennlig teller for energy.kbak.no. Appen (`index.html`, se
«BESØKSTELLER») sender ett lite treff når seksjon eller fane byttes, og ved
noen handlinger. Dette PHP-programmet tar imot treffene og lagrer dem som
summer per dag i en SQLite-fil på webhotellet.

## Hva som lagres

- Per dag: unike besøkende, sidevisninger og besøk per side
  (`/company/production/per-hub`, `/dcs/valuation-beta` …), sidevisninger per time.
- Per ny besøkende per dag: nettleser, system og enhet i grove grupper, språk (to bokstaver),
  land og nettverkseier (for eksempel «Telenor Norge AS»).
- Henvisende nettsted (bare vertsnavnet) ved første treff i et sidebesøk.
- Hendelser: `excel`, `ppt`, `ppt-add`, `ppt-add-card`, `ppt-basket`, `link`, `beta-unlock`, med siden de skjedde på.

Ikke lagret: IP-adresser, User-Agent, cookies eller noen identifikator, og
heller ikke hvilket felt, hub eller selskap som vises. Land og nettverkseier
slås opp fra IP-adressen i det treffet kommer (DB-IP Lite, se under); bare
resultatet lagres, i dagssummene. Finnes ikke IP-en, brukes nettleserens
tidssone for land. Nettverk med færre enn 3 besøkende i perioden vises ikke
hver for seg på statistikksiden eller i eksporten, men samles. Unike besøkende telles
med en SHA-256-hash av (dagens tilfeldige salt + IP + User-Agent); saltet og
hashene slettes ved første treff neste dag. Do Not Track og Global Privacy
Control respekteres, og roboter avvises.

## Filer

| Fil | Hva |
|---|---|
| `public/h.php` | Mottak (åpent). Svarer alltid 204. |
| `public/s/index.php` | Statistikksiden (passordbeskyttes i Plesk). CSV-eksport. |
| `public/_lib/io.php` | Database og tellelogikk. Kan ikke hentes direkte. |
| `public/_lib/update-geo.php` | Bygger `geo.sqlite` (land og nettverkseier) fra DB-IP. Kjøres månedlig som planlagt oppgave; bare fra kommandolinjen. |
| `public/_lib/config.sample.php` | Mal for `config.php` hvis standardverdiene skal endres. |

Databasen opprettes automatisk som `ncs-io-data/io.sqlite` i mappen *over*
subdomenets dokumentrot (webområdets rot i Plesk), altså utenfor det som kan
lastes ned. Geodatabasen (`geo.sqlite`, ca. 60 MB) legges i samme mappe.

Land og nettverkseier: «IP to Country Lite» og «IP to ASN Lite» fra
[DB-IP](https://db-ip.com), lisens CC BY 4.0 (kreditert på statistikksiden).

## Oppsett i Plesk (én gang)

1. **HTTPS:** *Websites & Domains → ncs-io.kbak.no → SSL/TLS Certificates* →
   Let's Encrypt. Påkrevd: energy.kbak.no er HTTPS, og nettleseren sender ikke
   treff til en HTTP-adresse.
2. **PHP:** *PHP Settings* for ncs-io.kbak.no: PHP 8.1 eller nyere (FPM).
   SQLite (`pdo_sqlite`) er med som standard.
3. **Last opp filene:** åpne *File Manager*, gå til subdomenets dokumentrot
   (vanligvis mappen `ncs-io.kbak.no`), og last opp innholdet i
   `counter/public/` slik at du får:
   ```
   ncs-io.kbak.no/h.php
   ncs-io.kbak.no/_lib/io.php
   ncs-io.kbak.no/_lib/.htaccess
   ncs-io.kbak.no/_lib/config.sample.php
   ncs-io.kbak.no/s/index.php
   ```
4. **Passord på statistikken:** *Password-Protected Directories → Add Protected
   Directory* → katalog `/s`, og legg til en bruker.
5. **Sjekk:**
   - `https://ncs-io.kbak.no/h.php` → tom side (statuskode 204).
   - `https://ncs-io.kbak.no/_lib/io.php` → 403 eller 404.
   - `https://ncs-io.kbak.no/s/` → ber om passord, og viser så en tom statistikkside.
     Gir den feilen «Could not open the database», har ikke PHP lov til å lage
     mappen `ncs-io-data`: lag den selv i webområdets rot (ved siden av
     `httpdocs`) i File Manager.
6. **Geodatabasen (planlagt oppgave):** *Websites & Domains → Scheduled Tasks
   → Add Task*:
   - Task type: **Run a PHP script**
   - Script path: `ncs-io.kbak.no/_lib/update-geo.php` (velg den med bla-knappen)
   - PHP-versjon: samme som for subdomenet
   - Run: **Monthly**, dag 3, et klokkeslett om natten (f.eks. 04:17)
   - Notify: «Errors only»

   Lagre, og trykk **Run Now** én gang. Den laster ned ca. 12 MB og bygger
   databasen på noen sekunder. Statistikksiden viser «database built …» nederst
   når den er på plass.
7. Når appen med telleren er publisert: åpne energy.kbak.no (uten annonseblokker
   og uten Do Not Track), og se at «Today» på statistikksiden øker.

Endres koden her senere, lastes de endrede filene opp på nytt; databasen
berøres ikke.
