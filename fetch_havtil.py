"""
Henter tilsynssaker fra Havindustritilsynet (Havtil) og indekserer dem per
felt og selskap. Skriver data/havtil.json til sakslista i feltprofilen.

    pip install requests
    python fetch_havtil.py              # daglig: feed + første listeside
    python fetch_havtil.py --backfill   # én gang: alle listesidene, uten tidsgrense

Kilder:
  · RSS-feeden https://www.havtil.no/rss/sistenytt/. Den har bare tittel,
    lenke, ingress og dato, og tilsynssakene i den går bare noen måneder
    bakover.
  · Listesidene per sakstype (/tilsyn/tilsynsrapporter/?p=N osv.), som går
    tilbake til 2012-2014. Den daglige kjøringen leser første side som
    sikkerhetsnett; --backfill leser alle.

Saken leses fra sakssiden første gang den dukker opp: antall avvik og
forbedringspunkter, rapport-PDF, kategori, og for samtykker feltene
Operatør, Utvinningsløyve, Brønnamn og Innretning. Eldre samtykker (før
ca. 2020) har ikke disse feltene; der hentes brønn og utvinningstillatelse fra
teksten. Sakssidene leses innenfor et tidsbudsjett; resten tas neste kjøring.

Alt legges i et arkiv som bare vokser. En sak som faller ut av feeden
beholdes.

Sakstyper (fra lenkens sti):
  inspection     /tilsyn/tilsynsrapporter/
  consent        /tilsyn/samtykker/
  investigation  /tilsyn/granskingsrapporter/
  notice         /tilsyn/viktige-meldinger/   (pålegg o.l.)
  news           /utforsk-fagstoff/fagstoff/  — bare når saken treffer et felt

Kobling til felt: Havtil skriver titlene som «Selskap – Innretning/felt –
tema». Feltnavn (fields.json) og innretningsnavn (facilities.json, med sitt
felt) letes etter i tittel, ingress og samtykkets Innretning-felt. Navnet må
stå med stor forbokstav, så «fram» i løpende tekst ikke blir Fram-feltet. Et
navn på ett ord teller ikke når det står inntil et annet ord med stor
forbokstav, som i personnavn («Helga Maria Brøgger», «Kristin Kragseth»).
Enkeltbokstaver, forkortelser og himmelretninger etter navnet (Heidrun B,
Balder FPU, Fram Øst) går fint.
Lengste navn vinner: «Oseberg Sør» gir ikke også Oseberg. Hver kobling lagrer
hvor den ble funnet (title, desc, facts, alias).

Selskap: delen av tittelen før første tankestrek, delt på «/», «,» og «og»,
og samtykkets Operatør-felt. Hvert navn kobles til én juridisk enhet i
companies.json (cmp), og vises slik Havtil skrev det, uten AS/ASA/Norge o.l.
(companies). Passer navnet flere enheter, velges den som er aktiv på sokkelen
og har roller der (rettighetshaver, operatør), så «Equinor» blir Equinor
Energy AS. Frontend grupperer enhetene i konsern med samme logikk som
lisensdataene (concernKey i index.html), så Statoil og Equinor, Det norske og
Aker BP osv. havner sammen uten egne alias her. Navn som ikke finnes i
companies.json tas ikke med.

Rigg: navnene på flyttbare innretninger i rig_facts.json letes etter i tittel,
ingress og samtykkets Innretning-felt, uten hensyn til store/små bokstaver og
med valgfritt mellomrom mellom delene, så «COSL Innovator» og «COSLInnovator»
begge blir COSLInnovator (rigs). Riggnavn som også er felt- eller
innretningsnavn (kranfartøyet BALDER) tas ikke med, og navn på ett ord må stå
med stor forbokstav, så «flotell» i løpende tekst ikke blir FLOTELL.

Brønn: brønnbanenavn (25/7-15 S) fra samtykkets Brønnamn-felt, tittel og
ingress (wells). Exploration kobler dem til letebrønnene.

Koblingene regnes ut på nytt hver kjøring fra de lagrede rådataene, så
rettelser i data/havtil_aliases.json virker bakover.

Nye saker de siste 60 dagene legges også i data/changes.json (type havtil),
som Exploration viser i News.

Havtils materiale kan gjenbrukes fritt når Havtil krediteres.
"""

import email.utils
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import changes

FEED = 'https://www.havtil.no/rss/sistenytt/'
SITE = 'https://www.havtil.no'
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
OUT = os.path.join(OUT_DIR, 'havtil.json')
ALIASES = os.path.join(OUT_DIR, 'havtil_aliases.json')
RETRIES = 3
MAX_SECONDS = 420        # tidsbudsjett for sakssidene i daglig kjøring; resten tas neste gang
PAUSE = 0.5              # sekunder mellom sidene
OSLO = ZoneInfo('Europe/Oslo')

KINDS = {
    'tilsyn/tilsynsrapporter': 'inspection',
    'tilsyn/samtykker': 'consent',
    'tilsyn/granskingsrapporter': 'investigation',
    'tilsyn/viktige-meldinger': 'notice',
    'utforsk-fagstoff/fagstoff': 'news',
}
DETAIL_KINDS = {'inspection', 'consent', 'investigation', 'notice'}

# Samtykkesidenes faktafelt, på bokmål og nynorsk.
FACTS = {
    'operatør': 'operator',
    'utvinningsløyve': 'licence', 'utvinningstillatelse': 'licence',
    'brønnamn': 'wells', 'brønnavn': 'wells', 'brønnnavn': 'wells',
    'innretning': 'facility',
    'prospektnamn': 'prospect', 'prospektnavn': 'prospect',
}
COMPASS = {'Nord', 'Sør', 'Øst', 'Vest', 'Nordøst', 'Nordvest', 'Sørøst', 'Sørvest'}
LEGAL = re.compile(r'\b(ASA|AS|A/S|Norge|Norway|Skandinavia|Norsk|Limited|Ltd)\b\.?', re.I)


def get(url):
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, timeout=60, headers={'User-Agent': 'ncs-production (github.com/sheffielddivided)'})
            r.raise_for_status()
            return r.text
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(str(last))


def clean(s):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', s or '')).split())


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def table(name):
    d = load_json(os.path.join(OUT_DIR, f'{name}.json'), {'columns': [], 'rows': []})
    return [dict(zip(d['columns'], r)) for r in d['rows']]


# ── FEED OG SAKSSIDER ────────────────────────────────────────────────────────

def feed_items():
    root = ET.fromstring(get(FEED).encode('utf-8'))
    out = []
    for it in root.iter('item'):
        link = (it.findtext('link') or '').strip()
        m = re.match(r'https?://www\.havtil\.no/(([^/]+/[^/]+)/.+)$', link)
        if not m or m.group(2) not in KINDS:
            continue
        try:
            date = email.utils.parsedate_to_datetime(it.findtext('pubDate')).date().isoformat()
        except (TypeError, ValueError):
            continue
        out.append({'id': '/' + m.group(1).rstrip('/') + '/', 'kind': KINDS[m.group(2)], 'date': date,
                    'title': clean(it.findtext('title')), 'desc': clean(it.findtext('description'))})
    return out


def list_page(section, page):
    """Sakene på én listeside. Tom liste når sidene er brukt opp."""
    text = get(f'{SITE}/{section}/?p={page}')
    out = []
    for li in text.split('<li itemscope')[1:]:
        li = li.split('</a>', 1)[0]     # hele kortet ligger i én lenke
        href = re.search(r'<a class="pcard" href="(/' + re.escape(section) + r'/\d{4}/[^"]+)"', li)
        when = re.search(r'datetime="([^"]+)"', li)
        title = re.search(r'pcard-title">(.*?)</div>', li, re.S)
        if not (href and when and title):
            continue
        try:
            date = datetime.fromisoformat(when.group(1).replace('Z', '+00:00')).astimezone(OSLO).date().isoformat()
        except ValueError:
            continue
        desc = re.search(r'pcard-text">(.*?)</div>', li, re.S)
        tags = [clean(t) for t in re.findall(r'<li class="list-inline-item">(.*?)</li>', li, re.S)]
        item = {'id': html.unescape(href.group(1).rstrip('/')) + '/', 'kind': KINDS[section], 'date': date,
                'title': clean(title.group(1)), 'desc': clean(desc.group(1) if desc else '')}
        if any(tags):
            item['tags'] = [t for t in tags if t]
        out.append(item)
    return out


def list_items(full):
    """Første listeside per sakstype, eller alle med full=True."""
    out = []
    for section, kind in KINDS.items():
        if kind not in DETAIL_KINDS:
            continue
        page = 1
        while True:
            try:
                got = list_page(section, page)
            except Exception as exc:  # noqa: BLE001
                print(f'  ! {section} side {page}: {exc}')
                break
            out += got
            if not full or not got:
                break
            page += 1
            time.sleep(PAUSE)
        if full:
            print(f'  {section}: {page - 1} sider')
    return out


WELL = re.compile(r'(?<![\d/])\d{1,4}/\d{1,2}-\d{1,3}(?:\s?[A-Z]{1,2}\b)?')
LICENCE = re.compile(r'utvinnings(?:løyve|tillatelse)\s+(?:nr\.?\s*)?(\d{1,4}(?:\s?[A-Z]{1,2}\b)?)', re.I)


def parse_page(text, kind=None):
    """Det som står på saksiden utover feeden."""
    d = {}
    body = re.search(r'class="clearfix article-body"(.*?)(?:<footer|$)', text, re.S)
    body = body.group(1) if body else text
    tags = [clean(t) for t in re.findall(r'<li class="list-inline-item">(.*?)</li>',
                                         (re.search(r'<ul class="custom-category[^"]*">(.*?)</ul>', text, re.S) or [None, ''])[1], re.S)]
    if tags:
        d['tags'] = [t for t in tags if t]
    facts = {}
    for label, value in re.findall(r'<p>\s*<strong>\s*([^<:]+?)\s*:\s*</strong>(.*?)</p>', body, re.S):
        key = FACTS.get(clean(label).lower())
        if key and clean(value) and key not in facts:
            facts[key] = clean(value)
    if kind == 'consent':
        # Eldre samtykker er skrevet som løpende tekst.
        prose = clean(body)
        if 'wells' not in facts:
            wells = list(dict.fromkeys(WELL.findall(prose)))
            if wells:
                facts['wells'] = ', '.join(wells)
        if 'licence' not in facts:
            m = LICENCE.search(prose)
            if m:
                facts['licence'] = m.group(1)
    if facts:
        d['facts'] = facts
    plain = clean(text)
    for key, word in (('nonconf', 'Avvik'), ('improve', 'Forbedringspunkter')):
        m = re.search(word + r'\s*\((\d+)\)', plain)
        if m:
            d[key] = int(m.group(1))
    pdf = re.search(r'href="(/contentassets/[^"]+\.pdf)"', text)
    if pdf:
        d['pdf'] = SITE + html.unescape(pdf.group(1))
    return d


# ── KOBLING ──────────────────────────────────────────────────────────────────

def name_pattern(name):
    """Hvert ord med stor forbokstav, resten uten hensyn til store/små."""
    words = [w for w in re.split(r'[\s\-]+', name) if w]
    if not words or len(name.replace(' ', '')) < 3:
        return None
    part = lambda w: re.escape(w[0].upper()) + (f'(?i:{re.escape(w[1:])})' if len(w) > 1 else '')
    return re.compile(r'(?<!\w)' + r'[\s\-]+'.join(part(w) for w in words) + r'(?!\w)')


RIG_SKIP = {'FLOTELL'}     # vanlig ord, ikke en bestemt rigg


def rig_pattern(name):
    """«COSLInnovator» og «DEEPSEA YANTAI» som mønstre med valgfritt mellomrom mellom delene."""
    parts = re.findall(r'[A-ZÆØÅ]+(?=[A-ZÆØÅ][a-zæøå])|[A-ZÆØÅ]?[a-zæøå]+|[A-ZÆØÅ]+|\d+', name)
    if sum(len(x) for x in parts) < 4 or name.upper() in RIG_SKIP:
        return None
    if len(parts) == 1:
        # Ett ord: stor forbokstav, resten uten hensyn til store/små.
        w = parts[0]
        return re.compile(r'(?<!\w)' + re.escape(w[0].upper()) + f'(?i:{re.escape(w[1:])})' + r'(?!\w)')
    return re.compile(r'(?<!\w)' + r'[\s\-]?'.join(re.escape(x) for x in parts) + r'(?!\w)', re.I)


def wells_in(text):
    """Brønnbanenavn i teksten, uten dubletter (25/7-15 S og 25/7-15S er samme)."""
    out = {}
    for w in WELL.findall(text or ''):
        out.setdefault(w.replace(' ', ''), ' '.join(w.split()))
    return list(out.values())


def in_name(text, start, end):
    """Står treffet inntil et ord med stor forbokstav, som i et personnavn?"""
    before = re.search(r'(\w+)\s+$', text[:start])
    after = re.match(r'\s+(\w+)', text[end:])
    proper = lambda w: len(w) > 1 and w[0].isupper() and not w.isupper()
    return bool(before and proper(before.group(1))
                or after and proper(after.group(1)) and after.group(1) not in COMPASS)


def short_company(name):
    return ' '.join(LEGAL.sub(' ', name).split()).strip(' ,.-')


class Matcher:
    def __init__(self, aliases):
        fields = table('fields')
        self.field_name = {f['id']: f['name'] for f in fields}
        by_name = {f['name'].upper(): f['id'] for f in fields}
        # (navn, mønster, felt-id, innretning eller None)
        names = {}
        for f in fields:
            names[f['name'].upper()] = (f['id'], None)
        for fac in table('facilities'):
            n = (fac['name'] or '').upper()
            if fac.get('fieldId') and n and n not in names:
                names[n] = (fac['fieldId'], fac['name'])
        for alias, targets in (aliases.get('fields') or {}).items():
            ids = [by_name[t.upper()] for t in targets if t.upper() in by_name]
            if ids:
                names[alias.upper()] = (ids, None)
        self.names = []
        for n, (fid, fac) in names.items():
            p = name_pattern(n)
            if p:
                self.names.append((len(n), p, fid, fac, n in {a.upper() for a in aliases.get('fields') or {}},
                                   len(n.split()) == 1))
        self.names.sort(key=lambda x: -x[0])

        self.renames = {k.lower(): v for k, v in (aliases.get('companies') or {}).items()}
        self.companies = {}
        self.legal = {c['name'] for c in table('companies')}
        for c in table('companies'):
            short = short_company(c['name']).lower()
            if short:
                self.companies.setdefault(short, []).append(c)
        self.ignore = {k: set(v) for k, v in (aliases.get('ignore') or {}).items()}
        self.rigs = []
        taken = set(by_name) | {(f['name'] or '').upper() for f in table('facilities')}
        for r in table('rig_facts'):
            p = None if r.get('fixed') or r['name'].upper() in taken else rig_pattern(r['name'])
            if p:
                self.rigs.append((len(r['name']), p, r['name']))
        self.rigs.sort(key=lambda x: -x[0])

    def rigs_of(self, item):
        found = []
        for text in (item['title'], item.get('desc', ''), (item.get('facts') or {}).get('facility', '')):
            taken = []
            for _, pat, name in self.rigs:
                for m in pat.finditer(text):
                    if any(m.start() < b and a < m.end() for a, b in taken):
                        continue
                    taken.append((m.start(), m.end()))
                    if name not in found:
                        found.append(name)
        return found

    def fields(self, item):
        found = {}     # felt-id -> kilde
        facs = []
        texts = [('title', item['title']), ('desc', item.get('desc', '')),
                 ('facts', (item.get('facts') or {}).get('facility', ''))]
        for src, text in texts:
            taken = []
            for _, pat, fid, fac, is_alias, single in self.names:
                for m in pat.finditer(text):
                    if any(m.start() < b and a < m.end() for a, b in taken):
                        continue
                    if single and in_name(text, m.start(), m.end()):
                        continue
                    taken.append((m.start(), m.end()))
                    for f in (fid if isinstance(fid, list) else [fid]):
                        found.setdefault(f, 'alias' if is_alias else src)
                    if fac and fac not in facs:
                        facs.append(fac)
        skip = self.ignore.get(item['id'], set())
        found = {f: s for f, s in found.items() if self.field_name.get(f, '').upper() not in {x.upper() for x in skip}}
        return found, facs

    def company(self, part):
        """(navn som vist, juridisk enhet i companies.json), eller None."""
        part = re.sub(r'^(varsel om pålegg til|pålegg til|sut-tilsyn)\s*', '', part.strip(), flags=re.I)
        name = short_company(part)
        if name.lower() in self.renames:
            name = self.renames[name.lower()]
            # Et alias kan peke rett på en juridisk enhet, der kortnavnet er tvetydig
            # (to ulike selskaper har hett «Det norske oljeselskap»).
            if name in self.legal:
                return short_company(name), name
        low = name.lower()
        if not low:
            return None
        cands = [(0, c) for c in self.companies.get(low, [])]
        cands += [(1, c) for k, v in self.companies.items() if k.startswith(low + ' ') for c in v]
        if not cands:
            return None
        roles = ('licensee', 'licenceOper', 'facilityOper', 'tufOper', 'baaOper')
        rank = lambda x: (-(x[1].get('activeNcs') or 0), -any(x[1].get(r) for r in roles), x[0],
                          '(old' in x[1]['name'], len(x[1]['name']))
        return name, min(cands, key=rank)[1]['name']

    def companies_of(self, item):
        """To lister i samme rekkefølge: navnene slik de vises, og juridisk enhet."""
        out = []
        head = re.split(r'\s[–—-]\s', item['title'], maxsplit=1)
        parts = re.split(r'/|,|\s+og\s+|\s+and\s+', head[0]) if len(head) > 1 else []
        op = (item.get('facts') or {}).get('operator')
        for p in parts + ([op] if op else []):
            c = self.company(p)
            # «Equinor» fra tittelen og «Equinor Energy» fra Operatør-feltet er samme selskap.
            if c and c[1] not in (o[1] for o in out):
                out.append(c)
        return [c[0] for c in out], [c[1] for c in out]


# ── KJØRING ──────────────────────────────────────────────────────────────────

NEWS_DAYS = 60


def news_events(items, known):
    """Nye saker til News (changes.json). Første gang: alle saker de siste 60 dagene."""
    since = (datetime.now(OSLO).date() - timedelta(days=NEWS_DAYS)).isoformat()
    seeded = any(e.get('t') == 'havtil' for e in changes.load())
    events = []
    for it in items:
        if it['date'] < since or (seeded and it['id'] in known):
            continue
        e = {'id': 'havtil|' + it['id'], 'd': it['date'], 't': 'havtil', 'k': it['kind'], 'n': it['title'], 'u': it['id']}
        if it['companies']:
            e['op'] = ', '.join(it['companies'])
        if it.get('nonconf') is not None:
            e['nc'] = it['nonconf']
        events.append(e)
    if events or not seeded:
        changes.merge(events)


def main():
    backfill = '--backfill' in sys.argv
    old = load_json(OUT, {})
    archive = {it['id']: it for it in old.get('items', [])}
    raw_keys = ('id', 'kind', 'date', 'title', 'desc', 'tags', 'facts', 'nonconf', 'improve', 'pdf', 'detail', 'tries')
    archive = {k: {x: v[x] for x in raw_keys if x in v} for k, v in archive.items()}

    try:
        fresh = feed_items()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f'Feeden svarer ikke ({exc}) - beholder forrige fil.')
    if not fresh:
        sys.exit('Ingen saker i feeden - beholder forrige fil.')

    aliases = load_json(ALIASES, {})
    match = Matcher(aliases)

    # Listesidene først, feeden sist: der begge har saken, vinner feeden.
    for it in list_items(backfill) + fresh:
        prev = archive.get(it['id'], {})
        archive[it['id']] = {**prev, **it}

    # Sakssidene for tilsynssaker som ikke er lest ennå.
    todo = [it for it in archive.values() if it['kind'] in DETAIL_KINDS and not it.get('detail')]
    todo.sort(key=lambda x: x['date'], reverse=True)
    read, t0 = 0, time.time()
    for it in todo:
        if not backfill and time.time() - t0 > MAX_SECONDS:
            break
        try:
            it.update(parse_page(get(SITE + it['id']), it['kind']))
            it['detail'] = True
            read += 1
        except Exception as exc:  # noqa: BLE001
            # En side som feiler tre kjøringer på rad, gis opp, så den ikke
            # spiser tidsbudsjettet for alltid.
            it['tries'] = it.get('tries', 0) + 1
            if it['tries'] >= 3:
                it['detail'] = True
            print(f'  ! {it["id"]}: {exc}')
        if backfill and read % 100 == 0:
            print(f'  {read}/{len(todo)} saksider', flush=True)
        time.sleep(PAUSE)

    items = []
    for it in archive.values():
        found, facs = match.fields(it)
        out = dict(it)
        out['fields'] = sorted(found)
        out['fieldSrc'] = [found[f] for f in out['fields']]
        if facs:
            out['facilities'] = facs
        out['companies'], out['cmp'] = match.companies_of(it)
        rigs = match.rigs_of(it)
        if rigs:
            out['rigs'] = rigs
        wells = wells_in(' '.join([(it.get('facts') or {}).get('wells', ''), it['title'], it.get('desc', '')]))
        if wells:
            out['wells'] = wells
        # Fagstoff tas bare med når det gjelder et bestemt felt.
        if it['kind'] == 'news' and not out['fields']:
            continue
        items.append(out)
    items.sort(key=lambda x: (x['date'], x['id']), reverse=True)

    news_events(items, {it['id'] for it in old.get('items', [])})

    payload = {
        'source': FEED,
        'credit': 'Havindustritilsynet (Havtil) - https://www.havtil.no',
        'items': items,
    }
    # Én sak per linje, så endringene leses greit i git.
    text = '{"source":%s,"credit":%s,"items":[\n%s\n]}\n' % (
        json.dumps(payload['source']), json.dumps(payload['credit'], ensure_ascii=False),
        ',\n'.join(json.dumps(i, ensure_ascii=False, sort_keys=True, separators=(',', ':')) for i in items))
    try:
        with open(OUT, encoding='utf-8') as f:
            if f.read() == text:
                print('= havtil.json uendret')
                return
    except OSError:
        pass
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(text)
    linked = sum(1 for i in items if i['fields'])
    print(f'+ havtil.json: {len(items)} saker ({read} saksider lest, {len(todo) - read} gjenstår), '
          f'{linked} koblet til felt')


if __name__ == '__main__':
    main()
