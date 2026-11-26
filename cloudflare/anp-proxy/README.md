# ANP-proxy (Cloudflare Worker)

gov.br bryter tilkoblinger fra GitHub Actions sine IP-adresser («Connection
reset by peer»), så `update-anp.yml` får ikke hentet ANPs åpne data direkte.
Denne Workeren henter filene fra Cloudflares nettverk og strømmer dem videre.
ANPs data er åpne (dekret 8.777/2016: fri bruk med kreditering av kilden).

Workeren slipper bare gjennom `GET` med riktig token, og bare adresser under
`https://www.gov.br/anp/`, også etter videresendinger.

## Oppsett

1. Cloudflare-dashbordet → **Workers & Pages** → **Create** → **Create Worker**.
   Gi den et navn (f.eks. `anp-proxy`) og trykk **Deploy**.
2. **Edit code**: erstatt innholdet med `worker.js` fra denne mappen og trykk
   **Deploy**.
3. **Settings** → **Variables and Secrets** → **Add**: type **Secret**, navn
   `TOKEN`, verdi = en lang tilfeldig streng (f.eks. fra
   `openssl rand -hex 32`). Lagre og deploy.
4. I GitHub: repoet → **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**:
   - `ANP_PROXY` = Workerens adresse, f.eks. `https://anp-proxy.<konto>.workers.dev`
   - `ANP_PROXY_TOKEN` = samme streng som `TOKEN`
5. Kjør **Oppdater Brasil (ANP)** fra Actions-fanen.

Uten `ANP_PROXY` henter `fetch_anp.py` direkte fra gov.br som før (det virker
fra de fleste andre steder enn GitHub Actions).

## Test

```bash
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' \
  -H "Authorization: Bearer $TOKEN" \
  "$ANP_PROXY/?url=https%3A%2F%2Fwww.gov.br%2Fanp%2Fpt-br%2Fcentrais-de-conteudo%2Fdados-abertos%2Frelacao-de-concessionarios"
```

`200` og noen hundre kB betyr at gov.br slipper Cloudflare inn. `403` betyr
feil token; `502` eller en tilkoblingsfeil betyr at gov.br stenger ute
Cloudflare også — da er reserven en tilsvarende PHP-fil på energy.kbak.no.

Gratisplanen holder: en kjøring er noen hundre forespørsler, og strømming
bruker nesten ikke CPU-tid.
