"""
Nyheter til Companies → News: Sodirs pressemeldinger og selskapenes egne
RSS-feeder.

    pip install requests
    python fetch_company_news.py                     # alt
    python fetch_company_news.py --sodir             # bare Sodir (daglig, update-sodir-data.yml)
    python fetch_company_news.py --companies         # bare selskapenes feeder (daglig)
    python fetch_company_news.py --companies --new   # bare feeder som ikke er hentet før
                                                     # (straks lista endres fra appen)

Skriver to filer:

  data/sodir_press.json   Sodirs meldinger om boretillatelser, boreresultater
                          og generelle nyheter. Arkiv som bare vokser (feedene
                          har bare de siste par hundre sakene).
  data/company_news.json  Siste saker fra feedene i data/company_feeds.json,
                          med status per feed.

── Sodir ──────────────────────────────────────────────────────────────────────
Tre feeder (de andre adressene under /rss/ er bak Cloudflare og svarer 403):
  permit   /rss/drilling-permits/               «Drilling permit for wellbore 25/9-5 S»
  result   /rss/exploration-drilling-results/   «Oil and gas discovery … (30/8-7 S)»
  news     /rss/general-news/                   alt annet
Fra tittel og ingress hentes brønnbanenavn (wells), selskapsnavn (cmp, juridisk
enhet i companies.json) og felt (fields, felt-id med samme navnelogikk som
fetch_havtil.py). Frontend kobler så en melding til selskapet når selskapet er
operatør eller partner i brønnen, har andel i feltet, eller er nevnt. Generelle
nyheter som verken nevner brønn, felt eller selskap lagres ikke. Koblingene regnes ut
på nytt hver kjøring fra den lagrede teksten.

── Selskapenes feeder ─────────────────────────────────────────────────────────
data/company_feeds.json redigeres fra tannhjulet i Companies → News (via GitHubs
Contents API, samme token som hub-innstillingene) eller for hånd:

    {"version": 1, "feeds": [
      {"company": "Aker BP ASA", "url": "https://…", "label": "Press releases",
       "added": "2026-11-01"}]}

«company» er selskapet slik det heter i appen; frontend viser feeden for hele
konsernet (concernKey). En nyhetsside uten feed leses direkte når lista har en
kjent struktur (page_list / model_list: AEM «tilelist» som ORLEN, AEM-innholdsdokument som Shell,
nyhetskort som Harbour Energy). En søkeadresse fra NewsWeb (Oslo Børs) leses via
NewsWebs eget JSON-API, se read_newsweb. Er adressen en vanlig nettside, letes det etter
<link rel="alternate" type="application/rss+xml"> i den, så det holder å lime
inn nyhetssiden. RSS 2.0, RSS 1.0 (RDF) og Atom leses.

Per feed lagres de siste MAX_PER_FEED sakene. Saker beholdes selv om de faller
ut av feeden; en feed som fjernes fra company_feeds.json tar sakene sine med
seg. Feiler en feed, beholdes sakene og status settes til feilmeldingen med
datoen feilen startet. Status endres bare når noe faktisk endres, så en frisk
feed gir ingen daglig commit.

Feedinnholdet er fremmed tekst: HTML fjernes, lenker må være http(s), og
frontend escaper alt. DOCTYPE med entiteter avvises før parsing.
"""

import email.utils
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests

import fetch_havtil

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FEEDS = os.path.join(DIR, 'company_feeds.json')
OUT = os.path.join(DIR, 'company_news.json')
SODIR_OUT = os.path.join(DIR, 'sodir_press.json')

SODIR = 'https://www.sodir.no/en/whats-new/news/rss/'
SODIR_FEEDS = {
    'permit': SODIR + 'drilling-permits/',
    'result': SODIR + 'exploration-drilling-results/',
    'news': SODIR + 'general-news/',
}
MAX_PER_FEED = 100
MAX_BYTES = 4 * 1024 * 1024
SUMMARY = 300
RETRIES = 3
UA = 'ncs-production news reader (github.com/sheffielddivided/ncs-production)'

# Brønnbaner: 25/9-5 S, 6407/5-3 S, 33/9-F-4 DH, 35/7-2, 7220/7-CD-1 H
WELL = re.compile(r'(?<![\d/])\d{1,4}/\d{1,2}-(?:[A-Z]{1,3}-)?\d{1,3}(?: ?[A-Z]{1,2}\d?\b)?')
# Første ord i et selskapsnavn som er for vanlige til å stå alene.
GENERIC = {'North', 'Northern', 'Norske', 'Not', 'Bridge', 'Stella', 'Source', 'Lime', 'Norpipe',
           'Waldorf', 'Petrolia', 'Kistos'}
# Kortnavn som ikke kan utledes av companies.json.
EXTRA = {'Shell': 'Norske Shell', 'Statoil': 'Equinor Energy'}


def today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=1) + '\n'
    try:
        with open(path, encoding='utf-8') as f:
            if f.read() == text:
                print(f'  = {os.path.basename(path)} uendret')
                return
    except OSError:
        pass
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f'  + {os.path.basename(path)} skrevet')


class FeedError(RuntimeError):
    """Feil som vises i feedlisten i appen; teksten er derfor på engelsk."""


def http_message(code):
    if code in (401, 403):
        return (f'The site refuses automated reading (HTTP {code}). It blocks servers such as the GitHub '
                'job that fetches the feeds, so this feed cannot be used. Try another source for the '
                "company's news, e.g. its newswire page on Cision or a Google News search feed.")
    if code == 404:
        return 'Not found (HTTP 404). Check the address.'
    if code == 410:
        return 'The feed has been removed (HTTP 410).'
    return f'The site answered HTTP {code}.'


def get(url):
    """(bytes, content-type, endelig adresse). Store svar avvises.

    Nettsteder som avviser automatiserte klienter (403 fra Akamai, Cloudflare
    o.l.) gis opp med en forklaring; det forsøkes ikke å omgå sperren. Bare
    nettverksfeil, 429 og 5xx prøves på nytt."""
    last = None
    for attempt in range(RETRIES):
        try:
            with requests.get(url, timeout=30, stream=True, headers={
                    'User-Agent': UA,
                    'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8'}) as r:
                if r.status_code >= 400:
                    if r.status_code != 429 and r.status_code < 500:
                        raise FeedError(http_message(r.status_code))
                    raise RuntimeError(http_message(r.status_code))
                buf = b''
                for chunk in r.iter_content(65536):
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        raise FeedError('The response is larger than 4 MB.')
                return buf, r.headers.get('content-type', ''), r.url
        except FeedError:
            raise
        except requests.RequestException as exc:
            last = FeedError(f'Could not reach the site ({type(exc).__name__}).')
        except Exception as exc:  # noqa: BLE001
            last = exc
        if attempt < RETRIES - 1:
            time.sleep(2 ** (attempt + 1))
    raise FeedError(str(last)[:300])


# ── FEEDLESING ───────────────────────────────────────────────────────────────

def local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def child(el, *names):
    for c in el:
        if local(c.tag) in names:
            return c
    return None


def text_of(el, *names):
    c = child(el, *names)
    return ' '.join((c.text or '').split()) if c is not None and c.text else ''


def clean(s, limit=None):
    s = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', s or '')).split())
    if limit and len(s) > limit:
        s = s[:limit].rsplit(' ', 1)[0] + ' …'
    return s


def iso_date(s):
    s = (s or '').strip()
    if not s:
        return ''
    try:
        return email.utils.parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    m = re.match(r'(\d{4}-\d{2}-\d{2})', s)
    return m.group(1) if m else ''


def is_feed(body):
    head = body[:2000].lower()
    return b'<rss' in head or b'<feed' in head or b'<rdf' in head


def discover(page, base):
    """Feedadressen fra <link rel="alternate" …> på en vanlig nettside."""
    txt = page[:300000].decode('utf-8', 'replace')
    for tag in re.findall(r'<link\b[^>]*>', txt, re.I):
        attrs = dict((k.lower(), v) for k, _, v in re.findall(r'([\w-]+)\s*=\s*(["\'])(.*?)\2', tag))
        if 'alternate' in attrs.get('rel', '').lower() and re.search(r'(rss|atom)\+xml', attrs.get('type', ''), re.I):
            if attrs.get('href'):
                return urljoin(base, html.unescape(attrs['href']))
    return None


def parse(body):
    """(kanalnavn, [{t, u, d, s}]) fra RSS 2.0, RSS 1.0 eller Atom."""
    if re.search(rb'<!ENTITY', body[:5000], re.I):
        raise FeedError('The feed defines XML entities and is rejected.')
    root = ET.fromstring(body)
    channel = child(root, 'channel')
    title = text_of(channel if channel is not None else root, 'title')
    out = []
    for el in root.iter():
        kind = local(el.tag)
        if kind not in ('item', 'entry'):
            continue
        if kind == 'entry':
            link = ''
            for l in el:
                if local(l.tag) == 'link' and l.get('rel', 'alternate') == 'alternate' and l.get('href'):
                    link = l.get('href')
                    break
            date = text_of(el, 'published', 'updated', 'date')
            summary = text_of(el, 'summary') or text_of(el, 'content')
        else:
            link = text_of(el, 'link')
            g = child(el, 'guid')
            if not link and g is not None and g.get('isPermaLink', 'true') != 'false':
                link = (g.text or '').strip()
            date = text_of(el, 'pubDate', 'date', 'published', 'updated')
            summary = text_of(el, 'description', 'summary', 'encoded')
        t = clean(text_of(el, 'title'), 300)
        link = link.strip()
        if not t or not re.match(r'^https?://', link):
            continue
        out.append({'t': t, 'u': link, 'd': iso_date(date), 's': clean(summary, SUMMARY)})
    return clean(title, 120), out


# ── NEWSWEB (Oslo Børs) ──────────────────────────────────────────────────────
# Børsmeldinger fra newsweb.oslobors.no, for selskaper uten egen RSS. NewsWeb
# har ingen feed, men nettsiden henter lista fra et åpent JSON-API uten
# innlogging (api_large i /urls.json, så /v1/newsreader/list). Det er den samme
# lista siden viser, hentet én gang per kjøring; ingen sperrer omgås.
#
# I company_feeds.json legges søkeadressen fra NewsWeb inn som den er, f.eks.
#   https://newsweb.oslobors.no/search?category=&issuer=1015&...
# Filtrene i adressen (issuer, category, market, messageTitle, fromDate,
# toDate) sendes videre til API-et. Uten fromDate gir API-et omtrent siste år.
NEWSWEB = 'newsweb.oslobors.no'
NEWSWEB_API = 'https://api3.oslo.oslobors.no'
NEWSWEB_PARAMS = ('issuer', 'category', 'market', 'messageTitle', 'fromDate', 'toDate')


def is_newsweb(url):
    return urlparse(url).hostname == NEWSWEB


def read_newsweb(url):
    q = parse_qs(urlparse(url).query)
    params = {k: q[k][0] for k in NEWSWEB_PARAMS if q.get(k) and q[k][0]}
    if not params.get('issuer'):
        raise FeedError('Choose a company in NewsWeb first; the address needs an issuer (…&issuer=1015…).')
    api = NEWSWEB_API
    try:
        api = json.loads(get(f'https://{NEWSWEB}/urls.json')[0]).get('api_large') or NEWSWEB_API
    except Exception:  # noqa: BLE001
        pass
    if not re.match(r'^https://[\w.-]+\.oslobors\.no$', api):
        api = NEWSWEB_API
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.post(f'{api}/v1/newsreader/list?{urlencode(params)}', timeout=30,
                              headers={'User-Agent': UA, 'Accept': 'application/json', 'Content-Type': 'application/json'})
            if r.status_code >= 400:
                raise FeedError(http_message(r.status_code))
            data = r.json().get('data') or {}
            break
        except FeedError:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    else:
        raise FeedError(f'Could not read NewsWeb ({type(last).__name__}).')
    msgs = [m for m in data.get('messages') or [] if not m.get('test') and m.get('messageId')]
    if not msgs:
        raise FeedError('NewsWeb has no messages for this search.')
    issuer = msgs[0].get('issuerName') or ''
    items = []
    for m in msgs:
        cat = ((m.get('category') or [{}])[0].get('category_en') or '').capitalize()
        corr = ' (correction)' if m.get('correctionForMessageId') else ''
        items.append({'t': clean(m.get('title', ''), 300) + corr,
                      'u': f'https://{NEWSWEB}/message/{int(m["messageId"])}',
                      'd': iso_date(m.get('publishedTime', '')), 's': clean(cat, SUMMARY)})
    return url, clean(f'NewsWeb · {issuer}', 120), items


# ── NYHETSLISTER UTEN FEED ───────────────────────────────────────────────────
# Noen selskaper har bare en nyhetsside. Har siden ingen feed, leses lista på
# siden selv når den har en kjent, fast struktur. Bare den ene siden brukeren
# har limt inn leses, én gang per kjøring, og bare den ferdige HTML-en (ikke
# API-er bak siden, som robots.txt ofte forbyr). Kjente strukturer:
#
#  tilelist  Adobe Experience Manager (ORLEN):
#    <li class="cmp-tilelist__item"><a class="cmp-tilelist__item-link" href="…">
#      <span class="cmp-tilelist__item-date">29.10.2026</span>
#      <h2 class="cmp-tilelist__item-title">…</h2><p class="cmp-tilelist__item-description">…</p>
#  model     AEM «headless» (Shell): siden bygges med JavaScript fra sitt eget
#            innholdsdokument, som HTML-en forhåndslaster
#            (<link rel="preload" href="….model.json">). Sakene er elementer med
#            organism «PressRelease»: model.title, model.text, model.date (ISO)
#            og model.links[0].value. Bare oversiktens eget dokument hentes.
#  card      nyhetskort (Harbour Energy):
#    <div class="card … card--news"> <time datetime="22/09/26">…</time>
#      <h2 class="card-title …">…</h2> <p>…</p> <a href="…">Read article</a>
#
# Datoene er dag først (europeisk), med to- eller firesifret år.
def list_date(s):
    s = (s or '').strip()
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return m.group(0)
    m = re.match(r'(\d{1,2})[./](\d{1,2})[./](\d{2}|\d{4})\b', s)
    if not m:
        return ''
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y + 2000 if y < 100 else y
    return f'{y}-{mo:02d}-{d:02d}' if 1 <= mo <= 12 and 1 <= d <= 31 else ''


def _list_item(base, href, title, date, desc):
    link = urljoin(base, html.unescape(href))
    if not re.match(r'^https?://', link):
        return None
    t = clean(title, 300)
    return {'t': t, 'u': link, 'd': list_date(clean(date)), 's': clean(desc, SUMMARY)} if t else None


def page_list(page, base):
    txt = page.decode('utf-8', 'replace')
    out = []
    for li in re.findall(r'<li[^>]*class="[^"]*cmp-tilelist__item\b[^"]*"[^>]*>(.*?)</li>', txt, re.S):
        href = re.search(r'<a[^>]*href="([^"]+)"', li)
        title = re.search(r'cmp-tilelist__item-title[^>]*>(.*?)</', li, re.S)
        date = re.search(r'cmp-tilelist__item-date[^>]*>(.*?)</', li, re.S)
        desc = re.search(r'cmp-tilelist__item-description[^>]*>(.*?)</p>', li, re.S)
        if href and title:
            out.append(_list_item(base, href.group(1), title.group(1), date.group(1) if date else '',
                                  desc.group(1) if desc else ''))
    parts = re.split(r'(?=<div class="card\b[^"]*\bcard--news\b)', txt)[1:]
    for card in parts:
        title = re.search(r'class=["\'][^"\']*card-title[^"\']*["\'][^>]*>(.*?)</h\d>', card, re.S)
        href = re.search(r'<a[^>]*href="([^"]+)"', card)
        date = re.search(r'<time[^>]*datetime="([^"]+)"', card) or re.search(r'<time[^>]*>(.*?)</time>', card, re.S)
        desc = re.search(r'</h\d>\s*<p[^>]*>(.*?)</p>', card, re.S)
        if href and title:
            out.append(_list_item(base, href.group(1), title.group(1), date.group(1) if date else '',
                                  desc.group(1) if desc else ''))
    # Fremhevede saker står ofte to ganger på siden; første forekomst vinner.
    seen, uniq = set(), []
    for it in out:
        if it and it['u'] not in seen:
            seen.add(it['u'])
            uniq.append(it)
    return uniq


def model_list(page, base):
    """Saker fra sidens eget AEM-innholdsdokument (.model.json), se over."""
    txt = page[:300000].decode('utf-8', 'replace')
    m = re.search(r'<link[^>]*rel="preload"[^>]*href="([^"]+\.model\.json)"', txt)
    if not m:
        return []
    url = urljoin(base, html.unescape(m.group(1)))
    if urlparse(url).hostname != urlparse(base).hostname:
        return []
    try:
        data = json.loads(get(url)[0])
    except Exception:  # noqa: BLE001
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            mdl = o.get('model') if isinstance(o.get('model'), dict) else None
            if o.get('organism') == 'PressRelease' and mdl and mdl.get('title') and mdl.get('links'):
                href = (mdl['links'][0] or {}).get('value', '')
                it = _list_item(base, href, mdl['title'], mdl.get('date', ''), mdl.get('text', ''))
                if it:
                    out.append(it)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    seen = set()
    return [it for it in out if not (it['u'] in seen or seen.add(it['u']))]


def page_title(page):
    m = re.search(rb'<title[^>]*>(.*?)</title>', page[:200000], re.S | re.I)
    return clean(m.group(1).decode('utf-8', 'replace'), 120) if m else ''


def read_feed(url):
    """(endelig feedadresse, kanalnavn, saker). Nettsider slås opp til feeden sin."""
    if is_newsweb(url):
        return read_newsweb(url)
    body, ctype, final = get(url)
    if not is_feed(body):
        found = discover(body, final)
        if not found:
            listed = page_list(body, final) or model_list(body, final)
            if listed:
                return url, page_title(body), listed
            raise FeedError('No RSS or Atom feed at the address, and the page does not link to one '
                            'or have a news list the fetch job can read.')
        body, ctype, final = get(found)
        if not is_feed(body):
            raise FeedError('The page links to a feed, but the link does not return one.')
    title, items = parse(body)
    if not items:
        raise FeedError('The feed has no items.')
    return final, title, items


# ── SODIR ────────────────────────────────────────────────────────────────────

class Mentions:
    """Selskapsnavn i løpende tekst → juridisk enhet i companies.json."""

    def __init__(self):
        self.m = fetch_havtil.Matcher({})
        roles = ('licensee', 'licenceOper', 'facilityOper', 'tufOper', 'baaOper', 'baaPartner', 'tufPartner')
        keys = {}
        for c in fetch_havtil.table('companies'):
            if not c.get('activeNcs') or not any(c.get(r) for r in roles):
                continue
            short = re.sub(r'\s*\([^)]*\)\s*', ' ', fetch_havtil.short_company(c['name'])).strip()
            if len(short) < 3:
                continue
            keys.setdefault(short, short)
            first = short.split()[0]
            if len(first) >= 3 and first not in GENERIC and short not in GENERIC:
                keys.setdefault(first, first)
        keys.update(EXTRA)
        self.keys = []
        for k, target in keys.items():
            hit = self.m.company(target)
            if hit:
                pat = re.compile(r'(?<![\w-])' + re.escape(k) + r'(?![\w-])', re.I)
                self.keys.append((len(k), pat, hit[1]))
        self.keys.sort(key=lambda x: -x[0])

    def __call__(self, text):
        found, taken = [], []
        for _, pat, legal in self.keys:
            for m in pat.finditer(text):
                if not m.group(0)[0].isupper():
                    continue
                if any(m.start() < b and a < m.end() for a, b in taken):
                    continue
                taken.append((m.start(), m.end()))
                if legal not in found:
                    found.append(legal)
        return found


def wells_in(text):
    out = {}
    for w in WELL.findall(text or ''):
        out.setdefault(w.replace(' ', ''), ' '.join(w.split()))
    return list(out.values())


def update_sodir():
    old = load(SODIR_OUT, {'items': []})
    have = {it['u']: it for it in old.get('items', [])}
    fetched = 0
    for kind, url in SODIR_FEEDS.items():
        try:
            body, _, _ = get(url)
            _, items = parse(body)
        except Exception as exc:  # noqa: BLE001
            print(f'  ! Sodir {kind}: {exc}')
            continue
        fetched += 1
        for it in items:
            prev = have.get(it['u'], {})
            have[it['u']] = {'k': kind, 't': it['t'], 'u': it['u'],
                             'd': it['d'] or prev.get('d') or today(), 's': it['s']}
        print(f'  · Sodir {kind}: {len(items)} saker')
    if not fetched:
        print('  ! Ingen Sodir-feeder svarte - beholder forrige fil.')
        return
    mention = Mentions()
    out = []
    for it in have.values():
        text = it['t'] + ' ' + it['s']
        rec = {k: it[k] for k in ('k', 't', 'u', 'd', 's')}
        wells = wells_in(text)
        cmp = mention(text)
        fields = sorted(mention.m.fields({'id': it['u'], 'title': it['t'], 'desc': it['s']})[0])
        if wells:
            rec['wells'] = wells
        if cmp:
            rec['cmp'] = cmp
        if fields:
            rec['fields'] = fields
        if it['k'] == 'news' and not (wells or cmp or fields):
            continue
        out.append(rec)
    out.sort(key=lambda x: (x['d'], x['u']), reverse=True)
    save(SODIR_OUT, {'source': 'Norwegian Offshore Directorate (Sodir), RSS', 'feeds': SODIR_FEEDS, 'items': out})


# ── SELSKAPENES FEEDER ───────────────────────────────────────────────────────

def update_companies(only_new=False):
    conf = load(FEEDS, {'feeds': []}).get('feeds', [])
    old = load(OUT, {'feeds': {}, 'items': []})
    status, items = old.get('feeds', {}), old.get('items', [])
    wanted = {f['url']: f for f in conf if re.match(r'^https?://', f.get('url', ''))}
    status = {u: s for u, s in status.items() if u in wanted}
    items = [it for it in items if it.get('f') in wanted]

    for url, f in wanted.items():
        # --new: bare feeder som ikke er hentet før (eller er flyttet til et
        # annet selskap). Brukes når lista endres fra appen.
        if only_new and url in status and status[url].get('company') == f.get('company', ''):
            continue
        st = dict(status.get(url) or {})
        st['company'] = f.get('company', '')
        try:
            final, title, got = read_feed(url)
        except Exception as exc:  # noqa: BLE001
            msg = clean(str(exc) if isinstance(exc, FeedError) else f'Could not read the feed: {exc}', 400)
            if st.get('error') != msg:
                st['error'], st['since'] = msg, today()
            status[url] = st
            print(f'  ! {url}: {msg}')
            continue
        st.pop('error', None)
        st.pop('since', None)
        st['title'] = title
        if final != url:
            st['feed'] = final
        else:
            st.pop('feed', None)
        mine = {it['u']: it for it in items if it['f'] == url}
        for it in got:
            prev = mine.get(it['u'], {})
            mine[it['u']] = {'f': url, 't': it['t'], 'u': it['u'], 'd': it['d'] or prev.get('d') or today(), 's': it['s']}
        keep = sorted(mine.values(), key=lambda x: (x['d'], x['u']), reverse=True)[:MAX_PER_FEED]
        items = [it for it in items if it['f'] != url] + keep
        st['n'] = len(keep)
        status[url] = st
        print(f'  · {url}: {len(got)} i feeden, {len(keep)} lagret')

    items.sort(key=lambda x: (x['d'], x['u']), reverse=True)
    save(OUT, {'feeds': status, 'items': items})


def main():
    args = set(sys.argv[1:])
    if '--companies' not in args:
        print('Sodir:')
        update_sodir()
    if '--sodir' not in args:
        print('Selskapenes feeder:')
        update_companies(only_new='--new' in args)


if __name__ == '__main__':
    main()
