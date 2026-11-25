"""
Henter data for Newfoundland and Labrador-sokkelen fra Canada-Newfoundland and
Labrador Offshore Energy Regulator (C-NLOER, tidligere C-NLOPB) og bygger
data/canada_nl.json og data/canada_nl_outlines.json til canada.html.

Kilder:
  - Statistikksiden (www.cnloer.ca/information/statistics/): månedlig
    produksjon per felt og reservoar (PDF per felt og år, 2002–), månedlige
    totaler (mpro), årlig produksjon per felt og reservoar (off_prod) og
    produksjon per brønn (1997–). Hibernia 1997–2001 og andre år uten
    feltfil bygges fra brønntallene, fordelt på reservoar etter årstallene
    i off_prod.
  - Ressurssiden (www.cnloer.ca/resource/information/): EUR, reserver og
    betingede ressurser per felt og reservoar (estrr_*), ressursoversikten
    (disc_rr) og godkjente utbyggingsplaner (dp_auth).
  - Produksjonssiden (www.cnloer.ca/offshore/): funnår per felt.
  - ArcGIS-huben (org JZPISF0sj1UatnZ8): brønner (leting, avgrensning,
    utvikling), innretninger, lisenser (PL, SDL, EL) med eierandeler og
    omriss, og sedimentære bassenger.

Alt sjekkes før noe skrives: månedene i hver feltfil må summere til
årstotalen, oljen i bbl må stemme med m³, feltene må summere til mpro, og
brønntallene må stemme med off_prod. Avvik gir exit 1 uten at filene røres.

Krever pdftotext (poppler-utils) og requests.

    python fetch_cnloer.py            # hent alt
    python fetch_cnloer.py --cache D  # mellomlagre PDF-er i D (gjenbrukes)
"""

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time

import requests

OUT_FILE = 'data/canada_nl.json'
OUTLINE_FILE = 'data/canada_nl_outlines.json'

SITE = 'https://www.cnloer.ca'
PAGES = {
    'stats': SITE + '/information/statistics/',
    'resource': SITE + '/resource/information/',
    'offshore': SITE + '/offshore/',
}
PAGE_MUST = {'stats': 'pro.pdf', 'resource': 'estrr_', 'offshore': 'Discovered in'}
ARC_ORG = 'JZPISF0sj1UatnZ8'
ARC = f'https://services5.arcgis.com/{ARC_ORG}/arcgis/rest/services'
ARC_SEARCH = 'https://www.arcgis.com/sharing/rest/search'

UA = 'Mozilla/5.0 (compatible; ncs-insight data fetch)'
BBL_PER_M3 = 6.2898
MMSCF_PER_E3M3 = 0.0353147

# Feltene. Koden er den C-NLOER bruker i filnavnene: {år}{kode}pro.pdf for
# feltets månedlige produksjon og {brønnkode}_oil_{år}.pdf per brønn.
FIELDS = [
    # id, navn, kode, brønnkode, PL som gir eierandelene i feltet
    ('hibernia', 'Hibernia', 'h', 'hib', 'PL 1001'),
    ('terra-nova', 'Terra Nova', 't', 'tn', 'PL 1002'),
    ('white-rose', 'White Rose', 'w', 'wr', 'PL 1006'),
    ('north-amethyst', 'North Amethyst', 'n', 'na', 'PL 1008'),
    ('hebron', 'Hebron', 'he', 'heb', 'PL 1012'),
]
RESERVE_FILES = {'hibernia': 'hib', 'terra-nova': 'tn', 'white-rose': 'wr',
                 'north-amethyst': 'na', 'hebron': 'heb'}
# Navnet på feltet i installasjons- og brønnlagene, når det avviker
FIELD_ALIASES = {'whiterose': 'white-rose', 'searose': 'white-rose'}

RESERVOIRS = ["Ben Nevis/Avalon", "Ben Nevis - Avalon", "Ben Nevis", "Jeanne d'Arc",
              "Hibernia", "Avalon", "Catalina", "Subtotal", "Total"]
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july',
          'august', 'september', 'october', 'november', 'december']
NUM = re.compile(r'^-?\d[\d ,]*(\.\d+)?$')
# Endringsdatoer i radene: 7/9/2026, Jan. 29/08, July 14/2006, Jan-11-2005, 14/2006, 12/06
DATES = [r'\b\d{1,2}/\d{1,2}/\d{2,4}\b', r'\b[A-Z][a-z]+\.?[ -]\d{1,2}[/-]\d{2,4}\b',
         r'\b\d{1,2}/\d{4}\b', r'\b\d{1,2}/\d{2}\b(?!\d)', r'\bFALSE\b', r'\bTRUE\b']


class DataError(Exception):
    pass


# ---------------------------------------------------------------- nett

SESSION = requests.Session()
SESSION.headers['User-Agent'] = UA


def get(url, binary=False, tries=6):
    """Henter url. C-NLOER svarer av og til med en side som laster seg selv
    på nytt (botvern) i stedet for PDF-en; da ventes det og prøves igjen."""
    url = re.sub(r'^http://', 'https://', url)
    last = None
    for k in range(tries):
        try:
            r = SESSION.get(url, timeout=90)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            if binary:
                if r.content[:5] == b'%PDF-':
                    return r.content
                if b'Page not found' in r.content[:20000] or b'Page no' in r.content[:2000]:
                    return None
                last = 'got HTML instead of PDF'
            else:
                return r.text
        except requests.RequestException as e:
            last = str(e)
        time.sleep(min(60, 5 * 2 ** k))
    raise DataError(f'{url}: {last}')


def get_page(url, must, tries=6):
    """En HTML-side som skal inneholde `must`. Botvernet svarer med en side
    som laster seg selv på nytt; da ventes det og prøves igjen."""
    for k in range(tries):
        t = get(url)
        if t and must in t:
            return t
        time.sleep(min(60, 5 * 2 ** k))
    raise DataError(f'{url}: page without "{must}" (bot protection?)')


def get_json(url, params=None):
    for k in range(5):
        try:
            r = SESSION.get(url, params=params, timeout=120)
            r.raise_for_status()
            d = r.json()
            if 'error' in d:
                raise DataError(f"{url}: {d['error']}")
            return d
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(5 * 2 ** k)
    raise DataError(f'{url}: {last}')


class PdfStore:
    def __init__(self, cache):
        self.cache = cache
        if cache:
            os.makedirs(cache, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix='cnloer-')

    def text(self, url):
        """Teksten i PDF-en med layout bevart, eller None hvis den mangler."""
        name = url.rsplit('/', 1)[1]
        path = os.path.join(self.cache or self.tmp, name)
        cached = False
        if self.cache and os.path.exists(path):
            with open(path, 'rb') as fh:
                cached = fh.read(5) == b'%PDF-'
        if not cached:
            data = get(url, binary=True)
            if data is None:
                return None
            with open(path, 'wb') as fh:
                fh.write(data)
            time.sleep(0.3)
        out = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True,
                             text=True)
        if out.returncode != 0:
            raise DataError(f'pdftotext failed for {name}: {out.stderr.strip()}')
        return out.stdout


def pdf_links(page_html):
    links = {}
    for u in re.findall(r'href="([^"]+\.pdf)"', page_html):
        links.setdefault(u.rsplit('/', 1)[1].lower(), html.unescape(u))
    return links


# ---------------------------------------------------------------- tall

def num(t):
    return float(t.replace(' ', '').replace(',', ''))


def canon_res(s):
    s = s.replace('’', "'").strip()
    for r in RESERVOIRS:
        if s.lower().startswith(r.lower()):
            return 'Ben Nevis/Avalon' if r == 'Ben Nevis - Avalon' else r
    return None


def parse_row(line):
    """(måned, reservoar, tall) fra en rad i produksjonstabellene."""
    for d in DATES:
        line = re.sub(d, ' ', line)
    toks = [t for t in re.split(r'\s{2,}', line.strip()) if t]
    res, nums, month, names = None, [], None, []
    for t in toks:
        if re.fullmatch(r'(19|20)\d\d', t) and not nums:
            continue
        if NUM.match(t):
            nums.append(num(t))
            continue
        m = re.match(r'(%s)\b\s*(.*)' % '|'.join(MONTHS), t, re.I)
        if m:
            month = MONTHS.index(m.group(1).lower())
            t = m.group(2).strip()
        if t:
            c = canon_res(t)
            if c:
                names.append(c)
    if names:
        res = names[-1]
    return month, res, nums[:6]


def check_units(nums, where):
    """Olje i bbl og gass i MMscf skal stemme med m³ og 10³ m³."""
    if len(nums) != 6:
        raise DataError(f'{where}: expected 6 numbers, got {nums}')
    oil, bbl, gas, mmscf = nums[0], nums[1], nums[2], nums[3]
    if oil > 200 and abs(oil * BBL_PER_M3 - bbl) > max(50, 0.003 * bbl):
        raise DataError(f'{where}: oil {oil} m3 vs {bbl} bbl')
    if gas > 2000 and abs(gas * MMSCF_PER_E3M3 - mmscf) > max(3, 0.01 * mmscf):
        raise DataError(f'{where}: gas {gas} e3m3 vs {mmscf} MMscf')


def parse_monthly(text, where, single=None):
    """Månedlig produksjon fra en feltfil eller mpro.

    Månedsnavnet står midt i blokken når feltet har flere reservoarer, og
    noen filer har en «Total»-rad per måned, andre ikke. Radene samles derfor
    i blokker: en blokk slutter ved «Total», eller når et reservoar dukker
    opp igjen. Returnerer ({måned: {reservoar: [olje, gass, vann]}},
    {reservoar: årstotal}, siste oppdatering)."""
    lines = [l for l in text.splitlines() if l.strip()]
    upd = None
    for l in lines[:8]:
        m = re.search(r'Last Updated:\s*(.+)', l)
        if m:
            upd = parse_date(m.group(1))
    try:
        i = next(k for k, l in enumerate(lines) if 'Monthly Details' in l)
    except StopIteration:
        raise DataError(f'{where}: no "Monthly Details" section')
    blocks, cur, tot, mode, prev = [], None, {}, 'm', None

    def new():
        b = {'m': None, 'rows': {}}
        blocks.append(b)
        return b

    for l in lines[i + 1:]:
        if l.strip() == prev:      # fet skrift gir doble linjer
            continue
        prev = l.strip()
        if re.match(r'\s*\*', l) or 'amendment' in l.lower():
            break
        if re.match(r'\s*Total\s*-?\s*(19|20)\d\d', l):
            mode = 't'
            l = re.sub(r'Total\s*-?\s*\d{4}', '', l)
        month, res, nums = parse_row(l)
        if mode == 't':
            # Regnearkfeil i kilden («FALSE» i stedet for et tall): totalen
            # kan ikke brukes til kontroll
            if re.search(r'\b(FALSE|TRUE|#\w+)', l):
                continue
            if nums:
                tot[res or single or 'Total'] = nums
            continue
        if res is None and nums:
            res = single or 'Total'
        if res is None:
            if month is not None:
                if cur is None or cur['m'] is not None:
                    cur = new()
                cur['m'] = month
            continue
        if res == 'Total':
            if cur is None:
                cur = new()
            cur['rows']['Total'] = nums
            if month is not None:
                cur['m'] = month
            cur = None
            continue
        if cur is None or res in cur['rows'] or (month is not None and cur['m'] is not None):
            cur = new()
        cur['rows'][res] = nums
        if month is not None:
            cur['m'] = month

    # Blokker uten måned etter desember er årstotalen for første reservoar
    # (North Amethyst skriver den uten «Total»-etikett).
    out = {}
    last = -1
    for b in blocks:
        m = b['m']
        if m is None:
            if last == 11:
                for r, n in b['rows'].items():
                    if n:
                        tot.setdefault(r, n)
                continue
            m = last + 1
        if m <= last or m > 11:
            raise DataError(f'{where}: months out of order ({m} after {last})')
        last = m
        rows = {}
        for r, n in b['rows'].items():
            if not n:
                continue
            check_units(n, f'{where} {MONTHS[m]} {r}')
            rows[r] = [n[0], n[2], n[4]]
        if rows:
            out[m] = rows

    # Månedene skal summere til årstotalen per reservoar
    named = {r for rows in out.values() for r in rows if r != 'Total'}
    if single and single != 'Total' and len(named) == 1:
        only = next(iter(named))
        tot = {(only if k in (single, None, 'Total') else k): v for k, v in tot.items()}
    for r, n in tot.items():
        s = sum(rows[r][0] for rows in out.values() if r in rows)
        if r == 'Total' and not any('Total' in rows for rows in out.values()):
            s = sum(v[0] for rows in out.values() for k, v in rows.items() if k != 'Total')
        if abs(s - n[0]) > max(5, 0.001 * n[0]):
            raise DataError(f'{where}: {r} months sum to {s:.0f} m3, year total {n[0]:.0f}')
    return out, tot, upd


def parse_well_production(text, where):
    """Sum per måned over alle brønnene i en {felt}_oil_{år}.pdf:
    {måned: [olje, gass, vann]}."""
    out = {}
    pat = re.compile(r'^\s*(?:\S+\s+)?(?:(?:19|20)\d\d\s+)?(\d{1,2})\s+'
                     r'(-?[\d,]+\.?\d*)\s+(-?[\d,]+\.?\d*)\s+(-?[\d,]+\.?\d*)\s*$')
    n = 0
    for l in text.splitlines():
        m = pat.match(l)
        if not m:
            continue
        mo = int(m.group(1)) - 1
        if not 0 <= mo <= 11:
            continue
        v = [float(m.group(k).replace(',', '')) for k in (2, 3, 4)]
        acc = out.setdefault(mo, [0.0, 0.0, 0.0])
        for k in range(3):
            acc[k] += v[k]
        n += 1
    if not n:
        raise DataError(f'{where}: no well rows')
    return out


def parse_annual(text):
    """Årlig produksjon per felt og reservoar fra off_prod:
    {år: {felt-id: {reservoar: [olje, gass, vann]}}}.

    Feltnavnet står midt i blokken for felt med flere reservoarer, så bare
    reservoarradene og årstallet brukes; et reservoarnavn som går igjen
    innen samme år betyr nytt felt, og feltene kommer i fast rekkefølge."""
    lines = [l for l in text.splitlines() if l.strip()]
    i = next(k for k, l in enumerate(lines) if 'Yearly Details' in l)
    out, rows, year, prev = {}, [], None, None
    names = {f[1].lower(): f[0] for f in FIELDS}
    names['whiterose'] = 'white-rose'
    for l in lines[i + 1:]:
        if l.strip() == prev:
            continue
        prev = l.strip()
        m = re.match(r'\s*((?:19|20)\d\d)\b', l)
        if m:
            year = int(m.group(1))
        l2 = l
        for d in DATES:
            l2 = re.sub(d, ' ', l2)
        toks = [t for t in re.split(r'\s{2,}', l2.strip()) if t]
        field = None
        res = None
        nums = []
        for t in toks:
            if re.fullmatch(r'(19|20)\d\d', t) and not nums:
                continue
            if NUM.match(t):
                nums.append(num(t))
                continue
            t2 = re.sub(r'\d+\.$', '', t).strip()
            key = t2.lower().replace(' ', '')
            if key in names or t2.lower() in names:
                field = names.get(t2.lower(), names.get(key))
            c = canon_res(t2)
            if c:
                res = c
        if res is None or len(nums) < 6:
            continue
        rows.append((year, field, res, nums[:6]))
        if res == 'Total':
            out_year = {r[0] for r in rows if r[0]}
            y = out_year.pop() if len(out_year) == 1 else year
            out[y] = rows
            rows = []
    return out


# ---------------------------------------------------------------- datoer

def parse_date(s):
    s = re.sub(r'(\d)(st|nd|rd|th)\b', r'\1', s.strip())
    s = re.sub(r'^(December|January)\s+(?=(January|December))', '', s)
    for fmt in ('%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%B, %Y', '%B %Y', '%b %Y'):
        try:
            d = dt.datetime.strptime(s.strip(' .'), fmt)
            return d.strftime('%Y-%m-%d') if '%d' in fmt else d.strftime('%Y-%m')
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------- produksjon

def build_production(store, links):
    """Månedlig produksjon per felt og reservoar, fra første måned med
    produksjon. Verdier: olje m³, gass 10³ m³, vann m³."""
    now = dt.date.today()
    annual = None
    fields = {}
    updated = {}
    for fid, name, code, wcode, _ in FIELDS:
        months = {}     # (år, måned) -> {reservoar: [o, g, w]}
        sources = {}
        first_year = None
        for y in range(1997, now.year + 1):
            key = f'{y}{code}pro.pdf'
            if key in links:
                t = store.text(links[key])
            else:
                t = None
            if t:
                data, tot, upd = parse_monthly(t, key, single='X')
                if upd:
                    updated[fid] = max(updated.get(fid, ''), upd)
                for m, rows in data.items():
                    rows = {r: v for r, v in rows.items() if r not in ('Total', 'Subtotal')}
                    if rows:
                        months[(y, m)] = rows
                sources[y] = 'field'
                continue
            wkey = f'{wcode}_oil_{y}.pdf'
            if wkey not in links:
                continue
            wt = store.text(links[wkey])
            if not wt:
                continue
            wells = parse_well_production(wt, wkey)
            if annual is None:
                annual = parse_annual(store.text(links['off_prod-1.pdf']))
            split = annual_split(annual, y, fid, name)
            ysum = sum(v[0] for v in wells.values())
            atot = sum(v[0] for v in split.values())
            if atot and abs(ysum - atot) > max(50, 0.01 * atot):
                raise DataError(f'{wkey}: wells sum to {ysum:.0f} m3, off_prod says {atot:.0f}')
            for m, v in wells.items():
                rows = {}
                for r, rv in split.items():
                    rows[r] = [v[k] * (rv[k] / sum(x[k] for x in split.values())
                                       if sum(x[k] for x in split.values()) else 1 / len(split))
                               for k in range(3)]
                months[(y, m)] = rows
            sources[y] = 'wells'
        if not months:
            raise DataError(f'{name}: no production found')
        keys = sorted(k for k, rows in months.items() if any(v[0] or v[1] for v in rows.values()))
        start, end = keys[0], keys[-1]
        res_order = []
        for k in sorted(months):
            for r in months[k]:
                if r not in res_order:
                    res_order.append(r)
        n = (end[0] - start[0]) * 12 + end[1] - start[1] + 1
        series = {r: [[0] * n, [0] * n, [0] * n] for r in res_order}
        for (y, m), rows in months.items():
            i = (y - start[0]) * 12 + m - start[1]
            if not 0 <= i < n:
                continue
            for r, v in rows.items():
                for k in range(3):
                    series[r][k][i] = round(v[k])
        fields[fid] = {
            'start': f'{start[0]}-{start[1] + 1:02d}',
            'reservoirs': res_order,
            'prod': series,
            'fromWells': sorted(y for y, s in sources.items() if s == 'wells'),
        }
        print(f'  {name}: {fields[fid]["start"]} – {end[0]}-{end[1] + 1:02d}, '
              f'{len(res_order)} reservoirs, wells for {fields[fid]["fromWells"]}')

    # Feltene skal summere til områdetotalen i mpro (2002–)
    total = {}
    for y in range(2002, now.year + 1):
        key = f'{y}mpro.pdf'
        if key not in links:
            continue
        t = store.text(links[key])
        if not t:
            continue
        data, _, upd = parse_monthly(t, key, single='Total')
        if upd:
            updated['total'] = max(updated.get('total', ''), upd)
        for m, rows in data.items():
            total[(y, m)] = rows.get('Total')
    worst = 0
    for (y, m), v in total.items():
        s = [0, 0, 0]
        for f in fields.values():
            sy, sm = map(int, f['start'].split('-'))
            i = (y - sy) * 12 + m - (sm - 1)
            for r in f['prod'].values():
                if 0 <= i < len(r[0]):
                    for k in range(3):
                        s[k] += r[k][i]
        if v is None:
            continue
        d = abs(s[0] - v[0])
        worst = max(worst, d / max(v[0], 1))
        if d > max(100, 0.002 * v[0]):
            raise DataError(f'{y}-{m + 1:02d}: fields sum to {s[0]} m3 oil, mpro says {v[0]:.0f}')
    print(f'  Checked {len(total)} months against mpro (worst {worst:.4%})')
    return fields, updated


def annual_split(annual, year, fid, name):
    """Årsproduksjon per reservoar for feltet i off_prod: {reservoar: [o, g, w]}."""
    rows = annual.get(year) or []
    # Felt i fast rekkefølge: rader før første «Subtotal»/feltbytte hører til
    # første felt. For årene brønntallene trengs (Hibernia 1997–2001,
    # North Amethyst 2010) er feltet entydig fra radene med feltnavn.
    groups, cur = [], []
    for yr, field, res, nums in rows:
        if res in ('Total',):
            if cur:
                groups.append(cur)
            cur = []
            continue
        if res == 'Subtotal':
            groups.append(cur)
            cur = []
            continue
        if any(r[2] == res for r in cur) or (field and any(r[1] and r[1] != field for r in cur)):
            groups.append(cur)
            cur = []
        cur.append((yr, field, res, nums))
    if cur:
        groups.append(cur)
    for g in groups:
        ids = {r[1] for r in g if r[1]}
        if ids == {fid}:
            return {r[2]: [r[3][0], r[3][2], r[3][4]] for r in g}
    raise DataError(f'off_prod: no rows for {name} {year}')


# ---------------------------------------------------------------- reserver

SECTIONS = [
    ('eur', r'Estimated Ultimate'),
    ('cum', r'Cumulative Oil Production'),
    ('rem', r'Oil Reserves'),
    ('pct', r'Percent of EUR'),
    ('coil', r'Contingent Oil'),
    ('cgas', r'Contingent Gas'),
    ('cngl', r'(Contingent )?NGL Resources'),
]


def parse_reserves(text, where):
    """EUR, produsert, reserver og betingede ressurser per reservoar.
    Verdier i 10⁶ m³ (gass 10⁹ m³); EUR, reserver og ressurser som
    [1P, 2P, 3P]. Innrykkede rader (pooler) får sub=True."""
    lines = text.splitlines()
    upd = None
    asof = None
    for l in lines:
        m = re.search(r'Last Updated:\s*(.+)', l)
        if m and not upd:
            upd = parse_date(m.group(1))
        m = re.search(r'\(as of ([A-Za-z]+ \d{1,2}, \d{4})\)', l)
        if m and not asof:
            asof = parse_date(m.group(1))
    sec = None
    out = {}
    order = []
    for l in lines:
        if re.match(r'^\s*\d\s*$', l) or l.strip().startswith('"'):
            continue
        hit = None
        for k, pat in SECTIONS:
            if re.match(r'\s*' + pat, l):
                hit = k
        if hit:
            sec = hit
            continue
        if not sec or not l.strip():
            continue
        if re.match(r'\s*\d+\s+"', l) or 'Reservoir' in l and not re.search(r'\d', l):
            continue
        m = re.match(r'^(\s*)([A-Za-z][A-Za-z\'’ ./&-]*?[A-Za-z0-9])\s{2,}(.+)$', l)
        if not m:
            continue
        name = m.group(2).replace('’', "'").strip()
        if name.lower().startswith(('10', 'million', 'proved')):
            continue
        vals = [v for v in re.split(r'\s{2,}|\s(?=\d)', m.group(3).strip()) if v]
        nums = []
        for v in vals:
            v = v.replace(',', '').rstrip('%')
            try:
                nums.append(float(v))
            except ValueError:
                pass
        if not nums:
            continue
        indent = len(m.group(1))
        r = out.get(name)
        if r is None:
            r = out[name] = {'name': name, 'indent': indent}
            order.append(name)
        # Tallene står i par: metrisk og C-NLOERs egne fat/bcf. Begge lagres,
        # så siden viser kildens tall i stedet for å regne om (gass er
        # oppgitt med en litt annen faktor enn 35,31 bcf per mrd. m³).
        if sec == 'cum':
            r['cum'] = nums[0]
            if len(nums) > 1:
                r['cumI'] = nums[1]
        elif sec == 'pct':
            r['pct'] = nums[:3]
        elif len(nums) >= 6:
            r[sec] = [nums[0], nums[2], nums[4]]
            r[sec + 'I'] = [nums[1], nums[3], nums[5]]
        elif len(nums) == 3:
            r[sec] = nums
        else:
            raise DataError(f'{where}: cannot read "{l.strip()}"')
    if not order:
        raise DataError(f'{where}: no reservoirs')
    base = min(out[n]['indent'] for n in order)
    rows = []
    for n in order:
        r = out[n]
        r['sub'] = r.pop('indent') > base + 2
        rows.append(r)
    # Totalen for oljen: «Total»-raden hvis den har EUR (White Rose har
    # bare gass i sin), ellers det eneste reservoaret på øverste nivå
    tot = next((r for r in rows if r['name'] == 'Total' and 'eur' in r), None)
    if tot is None:
        top = [r for r in rows if not r['sub'] and 'eur' in r and r['name'] != 'Total']
        if len(top) == 1:
            tot = top[0]
    if not tot or 'cum' not in tot:
        raise DataError(f'{where}: no total EUR')
    total = {k: tot[k] for k in ('eur', 'cum', 'rem', 'pct', 'eurI', 'cumI', 'remI') if k in tot}
    return {'updated': upd, 'asOf': asof, 'total': total, 'rows': rows}


RESOURCE_NAMES = {'Whiterose': 'White Rose'}


def parse_resources(text):
    """Ressursoversikten (disc_rr): 2P EUR for feltene og 2C-ressurser for
    feltene og funnene, per område. [olje 10⁶ m³, gass 10⁹ m³, NGL 10⁶ m³]."""
    upd = None
    m = re.search(r'Updated\s+([A-Za-z]+ \d{1,2}, \d{4})', text)
    if m:
        upd = parse_date(m.group(1))
    area, sec = None, None
    eur, cont = [], []
    for l in text.splitlines():
        s = l.strip()
        if not s:
            continue
        if s in ('Grand Banks', 'Labrador Shelf'):
            area = s
            continue
        if s.startswith('Proved and Probable'):
            sec = 'eur'
            continue
        if s.startswith('Contingent Resources'):
            sec = 'cont'
            continue
        if re.match(r'(Produced|Reserves|Sub-Total|Total)', s) or not sec:
            continue
        m = re.match(r'^([A-Za-z][A-Za-z\'’ .-]*?)(\d)?\s{2,}(.+)$', s)
        if not m:
            continue
        name = m.group(1).replace('’', "'").strip()
        name = RESOURCE_NAMES.get(name, name)
        toks = re.split(r'\s{2,}', m.group(3).strip())
        vals = []
        for t in toks:
            t = t.replace(',', '')
            if t == '-':
                vals.append(None)
            elif re.fullmatch(r'\d+(\.\d+)?', t):
                vals.append(float(t))
            elif 'Assessment' in t:
                vals += [None, None]
        if sec == 'eur':
            if len(vals) >= 2:
                eur.append({'name': name, 'oil': vals[0], 'oilI': vals[1], 'area': area})
        else:
            if len(vals) < 6:
                vals += [None] * (6 - len(vals))
            cont.append({'name': name, 'area': area, 'oil': vals[0], 'gas': vals[2],
                         'ngl': vals[4], 'oilI': vals[1], 'gasI': vals[3], 'nglI': vals[5]})
    if not eur or not cont:
        raise DataError('disc_rr: could not read the resource table')
    return {'updated': upd, 'eur2P': eur, 'contingent2C': cont}


def parse_dp(text):
    out = []
    for l in text.splitlines():
        toks = [t for t in re.split(r'\s{2,}', l.strip()) if t]
        if len(toks) == 6 and re.search(r'\d{4}$', toks[1]):
            out.append({'field': toks[0], 'approval': parse_date(toks[1]) or toks[1],
                        'operator': toks[2], 'installation': toks[3], 'type': toks[4],
                        'installed': parse_date(toks[5]) or toks[5]})
    return out


def parse_discovered(page):
    txt = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', page)))
    out = {}
    for fid, name, *_ in FIELDS:
        m = re.search(r'%s\s+Discovered in (\d{4})' % re.escape(name), txt)
        if m:
            out[fid] = int(m.group(1))
    return out


# ---------------------------------------------------------------- ArcGIS

def arc_query(service, where='1=1', geometry=False, offset=None):
    feats = []
    start = 0
    while True:
        params = {'where': where, 'outFields': '*', 'f': 'json', 'outSR': 4326,
                  'returnGeometry': 'true' if geometry else 'false',
                  'resultOffset': start, 'resultRecordCount': 1000}
        if geometry and offset:
            params['maxAllowableOffset'] = offset
        d = get_json(f'{ARC}/{service}/FeatureServer/0/query', params)
        fs = d.get('features', [])
        feats += fs
        if not d.get('exceededTransferLimit') or not fs:
            return feats
        start += len(fs)


def arc_services(title_query):
    """Tjenestene i C-NLOER-organisasjonen med tittel som treffer søket."""
    out = []
    start = 1
    while start > 0:
        d = get_json(ARC_SEARCH, {
            'q': f'orgid:{ARC_ORG} AND type:"Feature Service" AND title:"{title_query}"',
            'num': 100, 'start': start, 'f': 'json'})
        out += d['results']
        start = d.get('nextStart', -1)
    return out


def clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def to_float(v):
    try:
        return float(re.sub(r'[^\d.\-]', '', v)) if v else None
    except ValueError:
        return None


WELL_LAYERS = [
    ('Exploration_Wells_View', 'exploration'),
    ('Delineation_Wells_View', 'delineation'),
    ('Development_Wells_View', 'development'),
    ('Dual_Classified_Wells_View', 'dual'),
]


def field_of_well(name):
    n = name.lower().replace(' ', '')
    for fid, fname, *_ in FIELDS:
        if n.startswith(fname.lower().replace(' ', '')):
            return fid
    for k, fid in FIELD_ALIASES.items():
        if n.startswith(k):
            return fid
    return None


def build_wells():
    wells = []
    for svc, cls in WELL_LAYERS:
        for f in arc_query(svc):
            a = f['attributes']
            name = clean(a.get('Wellname'))
            if not name:
                continue
            reent = sum(1 for k in range(1, 6) if clean(a.get(f'RE{k}SpudDte')))
            last_term = None
            for k in range(5, 0, -1):
                t = clean(a.get(f'RE{k}TermDte'))
                if t:
                    last_term = parse_date(t)
                    break
            wells.append({
                'name': name,
                'cls': cls,
                'op': clean(a.get('Operator')),
                'no': clean(a.get('WellNumber')),
                'spud': parse_date(clean(a.get('SpudDate')) or ''),
                'term': parse_date(clean(a.get('TermDate')) or ''),
                're': reent,
                'reTerm': last_term,
                'status': clean(a.get('WellStatus')),
                'lic': clean(a.get('Licence1')),
                'area': clean(a.get('LTR')),
                'rig': clean(a.get('DrillUnit1')),
                'md': to_float(clean(a.get('TotDpthMD'))),
                'wd': to_float(clean(a.get('WaterDepth'))),
                'lat': to_float(clean(a.get('DecimalLat'))),
                'lon': to_float(clean(a.get('DecimalLon'))),
                'slot': clean(a.get('Slot')),
                'field': field_of_well(name) if cls == 'development' else None,
            })
    if len(wells) < 400:
        raise DataError(f'only {len(wells)} wells from ArcGIS')
    wells.sort(key=lambda w: (w['spud'] or '9999', w['name']))
    return wells


def build_installations():
    out = []
    for f in arc_query('Installations_view'):
        a = f['attributes']
        lics = [clean(a.get(f'Licence{k}')) for k in range(1, 6)]
        out.append({
            'name': clean(a.get('Prod_Instl')),
            'operator': clean(a.get('Operator')),
            'licences': [l for l in lics if l],
            'firstOil': parse_date(clean(a.get('FirstOil')) or ''),
            'basin': clean(a.get('Basin')),
            'status': clean(a.get('CurrStatus')),
            'lat': dms(clean(a.get('Latitude'))),
            'lon': -abs(dms(clean(a.get('Longitude'))) or 0) or None,
        })
    return out


def dms(s):
    if not s:
        return None
    m = re.match(r'\s*(\d+)°\s*(\d+)\'\s*([\d.]+)', s)
    if not m:
        return to_float(s)
    return round(int(m.group(1)) + int(m.group(2)) / 60 + float(m.group(3)) / 3600, 5)


HOLDER = re.compile(r'^(.*?)\s*\(([\d.]+)\s*%\)\s*$')


def build_licences():
    """Gjeldende lisenser fra ArcGIS: én tjeneste per lisens. Bruker
    LicenceTable som fasit for hvilke som finnes."""
    table = arc_query('LicenceTable')
    wanted = {}
    for f in table:
        a = f['attributes']
        lid = a['Licence_Name'].replace('-', ' ').strip()
        wanted[lid] = {'type': a['Type'], 'issued': a.get('Year_Issued')}
    services = {}
    for prefix in ('PL', 'SDL', 'EL'):
        for it in arc_services(prefix):
            t = it['title']
            m = re.match(r'^(PL|SDL|EL)[ _]?(\d+[A-C]?)(.*)$', t, re.I)
            if not m:
                continue
            lid = f'{m.group(1).upper()} {m.group(2).upper()}'
            rest = m.group(3).lower()
            if '200' in rest:
                continue
            score = 2 if 'view1' in rest else (1 if 'view' in rest else 0)
            svc = it['url'].rstrip('/').split('/services/')[1].split('/')[0]
            if lid not in services or score > services[lid][0]:
                services[lid] = (score, svc)
    out = []
    outlines = {}
    missing = []
    for lid, meta in sorted(wanted.items(), key=lambda kv: (kv[0].split()[0], int(re.sub(r'\D', '', kv[0])), kv[0])):
        if lid not in services:
            missing.append(lid)
            continue
        feats = arc_query(services[lid][1], geometry=True, offset=0.002)
        if not feats:
            missing.append(lid)
            continue
        a = feats[0]['attributes']
        holders = []
        for k in range(1, 20):
            h = clean(a.get(f'IntHold{k}'))
            if not h:
                continue
            m = HOLDER.match(h)
            if m:
                holders.append([m.group(1).strip(), float(m.group(2))])
            else:
                holders.append([h, None])
        wells = [clean(a.get(f'Well{k}')) for k in range(1, 13)]
        out.append({
            'id': lid,
            'type': meta['type'],
            'issued': meta['issued'],
            'rep': clean(a.get('LicenceRep')),
            'ha': to_float(clean(a.get('Hectares'))),
            'effective': clean(a.get('Effective')),
            'holders': holders,
            'area': clean(a.get('ComDisArea')),
            'discWell': clean(a.get('RelDisWell')),
            'wells': [w for w in wells if w],
            'cfb': clean(a.get('RelCFBYear')),
            'exp1': parse_date(clean(a.get('Period1Exp')) or ''),
            'exp2': parse_date(clean(a.get('Period2Exp')) or ''),
            'bid': clean(a.get('BidAmount')),
            'region': clean(a.get('LTR')),
        })
        rings = []
        for f in feats:
            for ring in (f.get('geometry') or {}).get('rings', []):
                rings.append([[round(x, 3), round(y, 3)] for x, y in ring])
        outlines[lid] = rings
        time.sleep(0.1)
    if missing:
        print(f'  WARNING: no layer for {", ".join(missing)}')
    if len(out) < 0.8 * len(wanted):
        raise DataError(f'only {len(out)} of {len(wanted)} licences found')
    return out, outlines


def build_basins():
    out = {}
    for it in arc_services('Basin'):
        m = re.match(r'View_(.+?) Basin(?: (\d))?$', it['title'])
        if not m:
            continue
        name = m.group(1) + (f' {m.group(2)}' if m.group(2) else '')
        svc = it['url'].rstrip('/').split('/services/')[1].split('/')[0]
        rings = []
        for f in arc_query(svc, geometry=True, offset=0.02):
            for ring in (f.get('geometry') or {}).get('rings', []):
                rings.append([[round(x, 2), round(y, 2)] for x, y in ring])
        if rings:
            out[name] = rings
    return out


# ---------------------------------------------------------------- hoved

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', help='folder for downloaded PDFs (reused between runs)')
    args = ap.parse_args()
    store = PdfStore(args.cache)
    try:
        print('Reading C-NLOER pages')
        pages = {k: get_page(u, PAGE_MUST[k]) for k, u in PAGES.items()}
        links = pdf_links(pages['stats'])
        links.update(pdf_links(pages['resource']))
        print(f'  {len(links)} PDF links')

        print('Production')
        prod, prod_upd = build_production(store, links)

        print('Reserves')
        reserves = {}
        for fid, code in RESERVE_FILES.items():
            key = f'estrr_{code}.pdf'
            if key not in links:
                raise DataError(f'no {key} on the resource page')
            reserves[fid] = parse_reserves(store.text(links[key]), key)
        rkey = next((k for k in links if k.startswith('disc_rr')), None)
        if not rkey:
            raise DataError('no disc_rr on the resource page')
        resources = parse_resources(store.text(links[rkey]))
        dp = parse_dp(store.text(links['dp_auth.pdf'])) if 'dp_auth.pdf' in links else []
        discovered = parse_discovered(pages['offshore'])

        print('ArcGIS')
        wells = build_wells()
        print(f'  {len(wells)} wells')
        inst = build_installations()
        licences, lic_outlines = build_licences()
        print(f'  {len(licences)} licences')
        basins = build_basins()
        print(f'  {len(basins)} basins')
    except DataError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1

    lic_by_id = {l['id']: l for l in licences}
    fields = []
    for fid, name, code, wcode, pl in FIELDS:
        p = prod[fid]
        dpr = next((d for d in dp if d['field'].lower() == name.lower()), None)
        lic = lic_by_id.get(pl)
        field_lics = sorted(l['id'] for l in licences
                            if l['type'] == 'Production' and l['area']
                            and l['area'].lower().startswith(name.lower()))
        insts = [i for i in inst if set(i['licences']) & set(field_lics)]
        fields.append({
            'id': fid,
            'name': name,
            'discovered': discovered.get(fid),
            'operator': (insts[0]['operator'] if insts else None) or (lic or {}).get('rep'),
            'installation': insts[0]['name'] if insts else (dpr or {}).get('installation'),
            'installationType': (dpr or {}).get('type'),
            'basin': insts[0]['basin'] if insts else None,
            'dpApproval': (dpr or {}).get('approval'),
            # Innretningens første olje gjelder bare feltet som startet den
            # (North Amethyst produserer til SeaRose, som White Rose)
            'firstOil': next((i['firstOil'] for i in insts
                              if i['firstOil'] and i['firstOil'][:7] == p['start']), p['start']),
            'licences': field_lics,
            'equityLicence': pl if lic else None,
            'equity': (lic or {}).get('holders', []),
            'start': p['start'],
            'reservoirs': p['reservoirs'],
            'prod': p['prod'],
            'fromWells': p['fromWells'],
            'updated': prod_upd.get(fid),
            'reserves': reserves[fid],
        })

    payload = {
        'source': 'Canada-Newfoundland and Labrador Offshore Energy Regulator (C-NLOER)',
        'fetched': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'updated': prod_upd.get('total'),
        'units': {'oil': 'm3', 'gas': '1000 m3', 'water': 'm3',
                  'reserves': 'million m3 (gas: billion m3); *I = as published in million bbl / bcf'},
        'fields': fields,
        'resources': resources,
        'installations': inst,
        'licences': licences,
        'wells': wells,
    }
    # Uendrede data skal ikke gi ny commit: behold forrige hentetidspunkt
    # når alt annet er likt.
    try:
        with open(OUT_FILE, encoding='utf-8') as fh:
            old = json.load(fh)
        if {**old, 'fetched': None} == {**payload, 'fetched': None}:
            payload['fetched'] = old['fetched']
            print('No changes since the last fetch')
    except (OSError, ValueError, KeyError):
        pass
    outlines = {'licences': lic_outlines, 'basins': basins}
    os.makedirs('data', exist_ok=True)
    for path, obj in ((OUT_FILE, payload), (OUTLINE_FILE, outlines)):
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(obj, fh, ensure_ascii=False, separators=(',', ':'))
            fh.write('\n')
        print(f'Wrote {path} ({os.path.getsize(path) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
