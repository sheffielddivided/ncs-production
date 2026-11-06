# Tilgangskontroll med Cloudflare Access

Mål: bare personer med e-post i firmadomenet (eksempelet bruker `@firma.no`)
kommer inn på NCS Insight, og koden og datafilene er ikke lenger offentlige.

GitHub Pages har ingen innlogging for vanlige kontoer, og repoet er offentlig.
Løsningen er å publisere fra Cloudflare Pages med Cloudflare Access foran,
og så gjøre repoet privat. Cloudflare Zero Trust er gratis opp til 50 brukere.

Rekkefølgen er viktig: sett opp og test den nye adressen **før** repoet gjøres
privat, så siden aldri er nede.

## 1. Publiser fra Cloudflare Pages

1. Opprett en konto på <https://dash.cloudflare.com>.
2. **Workers & Pages → Create → Pages → Connect to Git**, koble til GitHub og
   velg `sheffielddivided/ncs-production`. Gi Cloudflare-appen tilgang til
   akkurat dette repoet; det virker også når repoet senere blir privat.
3. Byggeinnstillinger:
   - Production branch: `main`
   - Framework preset: `None`
   - Build command: *(tom)*
   - Build output directory: `/`
4. **Save and Deploy**. Siden får adressen `https://<prosjektnavn>.pages.dev`.
5. **Settings → Builds → Branch control**: sett *Preview branches* til
   **None**. Gratisplanen har 500 bygg i måneden, og uten dette bygger hver
   push til en arbeidsbranch også. Datajobbene og vanlige merger til `main`
   holder seg godt under grensen.

Siden er nå offentlig på `pages.dev`. Gå videre til steg 2 med en gang.

## 2. Legg innlogging foran (Cloudflare Access)

1. **Zero Trust** i Cloudflare-dashbordet. Første gang velges et team-navn
   og gratisplanen (krever betalingskort, men koster ingenting under 50
   brukere).
2. **Settings → Authentication → Login methods**:
   - *One-time PIN* er på som standard: brukeren skriver inn e-posten sin og
     får en kode. Det holder for å begrense til et domene.
   - Bruker firmaet Microsoft 365 eller Google Workspace, kan *Microsoft
     Entra ID* eller *Google Workspace* legges til, så innloggingen blir den
     vanlige firmapåloggingen (SSO).
3. **Access → Applications → Add an application → Self-hosted**:
   - Application name: `NCS Insight`
   - Session duration: f.eks. `1 month`
   - Application domain: `<prosjektnavn>.pages.dev`
   - Legg til et domene til: `*.<prosjektnavn>.pages.dev` (dekker
     forhåndsvisninger og enkeltbygg, som ellers har egne åpne adresser).
4. Policy:
   - Action: **Allow**
   - Include → **Emails ending in** → `@firma.no`
5. Lagre, og test i et privat nettleservindu: siden skal be om innlogging,
   en `@firma.no`-adresse slippes inn, en annen avvises.

## 3. Gjør repoet privat og slå av GitHub Pages

1. GitHub → repoet → **Settings → Pages**: *Unpublish site* (eller sett
   *Source* til *None*). Den gamle adressen
   `sheffielddivided.github.io/ncs-production` slutter da å virke.
2. **Settings → General → Danger zone → Change visibility → Private**.
3. Kjør **Actions → Oppdater SODIR-data → Run workflow** én gang og sjekk at
   Cloudflare bygger på nytt etterpå.

## Etterpå

- **Huber, nyhetskilder og brønnavn** redigeres som før. Token-et må gi
  tilgang til repoet (fine-grained, `Contents: read and write` på
  `ncs-production`). Et token som er laget for «Public repositories», virker
  ikke mot et privat repo og må lages på nytt.
- **Endringer** publiseres som før ved push til `main`; Cloudflare bygger
  innen et par minutter.
- **Nye kolleger** trenger ingenting å gjøre: alle med `@firma.no` kommer
  inn. Enkeltpersoner utenfor domenet legges til med en ekstra
  *Include → Emails*-regel i samme policy.
- **Egen adresse** (f.eks. `ncs.firma.no`) kan legges til under Pages →
  *Custom domains*; legg samme domene til i Access-applikasjonen.
- Oppdater lenken under **Live** i `README.md` til den nye adressen.
