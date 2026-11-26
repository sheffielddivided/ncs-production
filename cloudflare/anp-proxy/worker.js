// Cloudflare Worker: henter ANPs åpne data (www.gov.br/anp/…) for fetch_anp.py.
//
// gov.br bryter tilkoblinger fra GitHub Actions sine IP-adresser. Workeren
// henter filen fra Cloudflares nettverk og strømmer den videre uendret.
//
//   GET https://<worker>/?url=https%3A%2F%2Fwww.gov.br%2Fanp%2F…
//   Authorization: Bearer <TOKEN>
//
// Sikring: bare GET, bare med riktig token (hemmeligheten TOKEN på Workeren),
// og bare adresser under https://www.gov.br/anp/ — også etter videresendinger.
// Workeren er altså ingen åpen proxy. Oppsett: se README.md i samme mappe.

const ALLOWED_HOST = 'www.gov.br';
const ALLOWED_PREFIX = '/anp/';
const MAX_REDIRECTS = 5;

function allowed(u) {
  return u.protocol === 'https:' && u.hostname === ALLOWED_HOST && u.pathname.startsWith(ALLOWED_PREFIX);
}

// Sammenligning som ikke avslører tokenet gjennom svartiden
function sameToken(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export default {
  async fetch(request, env) {
    if (request.method !== 'GET') return new Response('Method not allowed\n', {status: 405});
    const auth = request.headers.get('Authorization') || '';
    if (!sameToken(auth, `Bearer ${env.TOKEN || ''}`) || !env.TOKEN) return new Response('Forbidden\n', {status: 403});

    let target;
    try {
      target = new URL(new URL(request.url).searchParams.get('url') || '');
    } catch {
      return new Response('Missing or invalid url\n', {status: 400});
    }
    if (!allowed(target)) return new Response('Only https://www.gov.br/anp/ is allowed\n', {status: 403});

    // Videresendinger følges manuelt, så de ikke kan føre ut av ANP
    let upstream;
    for (let hop = 0; ; hop++) {
      upstream = await fetch(target.toString(), {
        headers: {'User-Agent': request.headers.get('User-Agent') || 'ncs-insight data fetch', 'Accept': '*/*'},
        redirect: 'manual',
      });
      if (upstream.status < 300 || upstream.status >= 400) break;
      const loc = upstream.headers.get('Location');
      if (!loc || hop >= MAX_REDIRECTS) return new Response('Too many or invalid redirects\n', {status: 502});
      target = new URL(loc, target);
      if (!allowed(target)) return new Response('Redirect outside https://www.gov.br/anp/\n', {status: 502});
    }

    const headers = new Headers();
    for (const k of ['content-type', 'content-length', 'last-modified', 'etag']) {
      const v = upstream.headers.get(k);
      if (v) headers.set(k, v);
    }
    headers.set('Cache-Control', 'no-store');
    return new Response(upstream.body, {status: upstream.status, headers});
  },
};
