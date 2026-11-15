"""
Henter britiske produksjons-, eier- og lisensdata fra North Sea Transition
Authority (NSTA) og lagrer dem i NCS-appens format under data/:

    data/ukcs.json           felt, operatør, status, eierandeler med historikk
                             og månedlig produksjon (juni 1975 →)
    data/ukcs_outlines.json  feltomriss (NSTA field determinations)
    data/ukcs_licences.json  gjeldende lisensunderområder med eiergrupper og omriss

Kilder (alle åpne, ingen nøkkel):

  * PPRS — «UKCS hydrocarbon field production reports PPRS points (WGS84)»,
    ArcGIS-element dd38204275a04618ab7ddd00f87224e3. Én rad per felt,
    rapporteringsenhet og måned.
  * Field Partners — Excel-arbeidsbok med eierandeler per felt og juridisk
    enhet, med start- og sluttdato (datanstauthority.blob.core.windows.net).
  * Field determinations — feltomriss, ArcGIS-element bef8788b07464a7f8a18a18eb638b9f5.
  * Licence subareas by equity group — gjeldende lisensunderområder, én rad
    per underområde og eiergruppe, ArcGIS-element 40c65d96a1a14da8b066f2abbb345fed.

Tjeneste-URL-ene slås opp fra element-ID-ene hver gang, så en flyttet tjeneste
ikke knekker hentingen.

Enheter som i resten av appen: olje (inkl. kondensat) og vann i mill Sm³, gass
i mrd Sm³. PPRS oppgir olje og kondensat i m³ og gass i 1000 Sm³ (britiske
standardbetingelser, 15 °C og 1,01325 bar — de samme som norske Sm³), så det
trengs ingen omregning ut over skalering. Gass er assosiert gass + tørrgass,
brutto produsert som NSTA rapporterer den.

Bare offshorefelt (LOCATION = Offshore) tas med — det er sokkelen. Enheter
med STORAGE eller INJECTION i navnet er gasslagre (Rough, Hatfield Moor,
Humbly Grove) og telles ikke som produksjon.

Eierandelene er per juridisk enhet. Konsernet (equity group) hentes fra
lisensdataene, der NSTA lister juridisk enhet og konsern side om side
(EQORG/EQORGGRP); enheter som ikke lenger har lisenser beholder sitt eget navn.

Kjøres daglig av .github/workflows/update-sodir-data.yml:

    pip install requests openpyxl
    python fetch_nsta.py

Feiler en kilde eller ser dataene feil ut (for få rader, manglende kolonner),
avbrytes skriptet med exit 1 uten at noe skrives. Filene skrives bare når
innholdet faktisk er endret.
"""

import collections
import datetime as dt
import io
import json
import os
import re
import sys
import time

import requests

ITEM_PPRS = 'dd38204275a04618ab7ddd00f87224e3'
ITEM_FIELDS = 'bef8788b07464a7f8a18a18eb638b9f5'
ITEM_LICENCES = '40c65d96a1a14da8b066f2abbb345fed'
PARTNERS_XLSX = 'https://datanstauthority.blob.core.windows.net/external/Documents/field_partners.xlsx'
PARTNER_COLS = ['Field Name', 'On Offshore', 'Median Line Flag', 'Status', 'Organisation Name',
                'Percentage Holding', 'Operator Flag', 'Start Date', 'End Date', 'Equity Share Time Period']

OUT_DIR = 'data'
TIMEOUT = 90
RETRIES = 5
PAGE = 2000
USER_AGENT = 'ncs-production/fetch_nsta (+https://github.com/sheffielddivided/ncs-production)'
# Forenkling av omrissene i tjenesten (grader, ~150 m) og antall desimaler.
SIMPLIFY = 0.0015
DECIMALS = 4
# Kontroller mot kilden: under dette er noe galt.
MIN_PPRS_ROWS = 100_000
MIN_PARTNER_ROWS = 3_000
MIN_LICENCE_ROWS = 1_000
MIN_OUTLINES = 200

session = requests.Session()
session.headers['User-Agent'] = USER_AGENT


def need(cond, msg):
    if not cond:
        print(f'FEIL: {msg}', file=sys.stderr)
        sys.exit(1)


def get(url, params=None, binary=False):
    for i in range(RETRIES):
        try:
            r = session.get(url, params=params, timeout=TIMEOUT)
            if r.status_code < 500:
                r.raise_for_status()
                if binary:
                    return r.content
                d = r.json()
                if isinstance(d, dict) and 'error' in d:
                    raise RuntimeError(f'ArcGIS-feil fra {url}: {d["error"]}')
                return d
        except (requests.ConnectionError, requests.Timeout) as e:
            if i == RETRIES - 1:
                raise
            print(f'  nettverksfeil ({e.__class__.__name__}), prøver igjen …')
        time.sleep(2 ** i)
    raise RuntimeError(f'{url}: ga opp etter {RETRIES} forsøk')


def layer_url(item):
    meta = get(f'https://www.arcgis.com/sharing/rest/content/items/{item}', {'f': 'json'})
    need(meta.get('url'), f'element {item} har ingen tjeneste-URL')
    return meta['url'].rstrip('/') + '/0'


def query_all(url, fields, geometry=False, where='1=1'):
    count = get(url + '/query', {'f': 'json', 'where': where, 'returnCountOnly': 'true'})['count']
    rows = []
    while len(rows) < count:
        p = {'f': 'json', 'where': where, 'outFields': ','.join(fields), 'orderByFields': 'OBJECTID',
             'resultOffset': len(rows), 'resultRecordCount': PAGE, 'returnGeometry': 'true' if geometry else 'false'}
        if geometry:
            p.update({'outSR': 4326, 'maxAllowableOffset': SIMPLIFY, 'geometryPrecision': DECIMALS})
        feats = get(url + '/query', p).get('features', [])
        if not feats:
            break
        rows.extend(feats)
    need(len(rows) == count, f'{url}: fikk {len(rows)} av {count} rader')
    return rows


def rings_of(geom):
    """Esri-ringer → [[lon, lat], …] per ring, med bare ytre ringer.
    Hull (ringer mot klokka) tas ikke med; kartene fyller omrisset uansett."""
    out = []
    for ring in (geom or {}).get('rings', []):
        if len(ring) < 4:
            continue
        area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))
        if area > 0:   # mot klokka = hull i Esri-formatet
            continue
        out.append([[round(x, DECIMALS), round(y, DECIMALS)] for x, y in ring])
    return out


def norm(name):
    return re.sub(r'\s+', ' ', str(name or '').strip().upper())


def slugify(name):
    s = re.sub(r'[^a-z0-9]+', '_', norm(name).lower()).strip('_')
    return s or 'field'


# ── Navn ─────────────────────────────────────────────────────────────────────
# NSTA skriver alt med store bokstaver. Visningsnavnene får vanlig skrivemåte,
# men forkortelser og selskapsnavn som skrives med store bokstaver beholdes.
KEEP_UPPER = {'UK', 'U.K.', 'BP', 'ENI', 'CNOOC', 'CNR', 'TAQA', 'NEO', 'INEOS', 'OMV', 'DNO', 'BG', 'BHP',
              'EOG', 'JX', 'MOL', 'ONE', 'PL', 'NSTA', 'E&P', 'US', 'USA', 'II', 'III', 'IV', 'DEA', 'RWE',
              'GDF', 'EDF', 'SSE', 'CATS', 'FPSO', 'LNG', 'NNS', 'CNS', 'SNS', 'WOS', 'IS', 'ABP', 'KUFPEC',
              'ONGC', 'PTTEP', 'OKEA', 'MCX', 'JAPEX', 'NAM', 'CEP', 'TGS', 'EnQuest', 'NEP', 'RSRUK',
              'ERT', 'KNOC', 'MPX', 'GB', 'JV', 'EP', 'NSMP', 'ITOCHU', 'NV', 'BV', 'AS', 'ASA', 'AB',
              'SA', 'AG', 'GMBH', 'PLC', 'LLC', 'LP', 'INC', 'LTD', 'SPA', 'S.P.A.'}
SPECIAL = {'PLC': 'plc', 'GMBH': 'GmbH', 'ENQUEST': 'EnQuest', 'LTD': 'Ltd', 'INC': 'Inc', 'LLC': 'LLC',
           'SPA': 'SpA', 'S.P.A.': 'S.p.A.', 'OKEA': 'OKEA', 'ITHACA': 'Ithaca', 'MCDERMOTT': 'McDermott'}


def pretty(name):
    def word(w):
        u = w.upper()
        if u in SPECIAL:
            return SPECIAL[u]
        if u in KEEP_UPPER:
            return u
        # Bokstav-og-tall (P1234, 9/13a) og ord uten vokaler (NSMP) beholdes.
        if re.search(r'\d', w) or (len(w) <= 4 and not re.search(r'[AEIOUY]', u)):
            return u
        return '-'.join(p[:1].upper() + p[1:].lower() for p in w.split('-'))
    out = re.sub(r"[A-Za-z0-9&.'+/-]+", lambda m: word(m.group(0)), norm(name)).replace("'S ", "'s ")
    return re.sub(r'(?<=\S) (And|Of|The|For|In)(?= )', lambda m: ' ' + m.group(1).lower(), out)


# «CLAIR-RIDGE [PART OF CLAIR]», «RAVENSPURN N[PT.OF RAVENSPURN]»: delfelt.
PART_OF = re.compile(r'\[\s*P(?:AR)?T\.?\s*OF\s+([^\]]+?)\s*\]', re.I)


def parent_of(name):
    m = PART_OF.search(norm(name))
    return norm(m.group(1)) if m else None


def field_title(name):
    """Visningsnavn for et PPRS-felt: delfeltmerket fjernes når navnet allerede
    begynner med hovedfeltet, ellers står hovedfeltet i parentes."""
    n, par = norm(name), parent_of(name)
    base = norm(PART_OF.sub('', n))
    if par and not base.startswith(par.split()[0]):
        base = f'{base} ({par})'
    base = re.sub(r'\s*\[([^\]]+)\]', r' (\1)', base)
    return pretty(base)


def outline_key(name, outlines):
    """Omrisset til et PPRS-felt: samme navn, hovedfeltet, eller navnet uten
    parenteser og klammer («BELL [PERENCO]» → BELL)."""
    n = norm(name)
    for cand in (n, parent_of(n), norm(re.sub(r'\[[^\]]*\]', '', n)), norm(re.sub(r'\([^)]*\)', '', n))):
        if cand and cand in outlines:
            return cand
    return None


# Ord som ikke sier hvilket konsern et selskap hører til.
GENERIC = {'NORTH', 'SOUTH', 'EAST', 'WEST', 'FIRST', 'OIL', 'GAS', 'ENERGY', 'PETROLEUM', 'BRITISH',
           'UNITED', 'INTERNATIONAL', 'EXPLORATION', 'PRODUCTION', 'RESOURCES', 'OFFSHORE', 'NEW', 'GREAT',
           'SEA', 'ATLANTIC', 'CENTRAL', 'NORTHERN', 'SOUTHERN', 'GLOBAL', 'UK', 'THE', 'ROYAL'}


def make_group_of(org_group):
    """Juridisk enhet → konsern. Enheter som ikke finnes i lisensdataene får
    konsernet til de andre enhetene med samme første ord, når alle de peker på
    samme konsern (ITHACA (NE) UKCS LIMITED → Ithaca Energy). Ellers står
    enheten som eget selskap."""
    by_first = collections.defaultdict(set)
    for o, g in org_group.items():
        w = o.split()[0]
        if len(w) >= 2 and w not in GENERIC:
            by_first[w].add(g)
    def group_of(org):
        if org in org_group:
            return org_group[org]
        w = org.split()[0] if org else ''
        gs = by_first.get(w)
        if gs and len(gs) == 1:
            return next(iter(gs))
        return pretty(org)
    return group_of


def strip_regno(name):
    """«PERENCO UK LIMITED (04653066)» → «PERENCO UK LIMITED». Parenteser uten
    tall, som «(U.K.)», er en del av navnet og beholdes."""
    return norm(re.sub(r'\s*\((?=[^)]*\d)[A-Z0-9 ]+\)', '', str(name or '')))


def split_pct(text):
    """«A (85%), B (15%)» → [('A', 85.0), ('B', 15.0)]."""
    return [(strip_regno(m.group(1).strip(' ,')), float(m.group(2)))
            for m in re.finditer(r'([^%]+?)\s*\((\d+(?:\.\d+)?)%\)', str(text or ''))]


# ── PPRS ─────────────────────────────────────────────────────────────────────
PPRS_FIELDS = ['OBJECTID', 'FIELDNAME', 'FIELDAREA', 'LOCATION', 'ORGGRPNM', 'UNITNAME', 'PERIODYRMN',
               'OILPRODM3', 'GCONDVOL', 'AGASPROKSM', 'DGASPROKSM', 'WATPRODVOL']


def build_production():
    url = layer_url(ITEM_PPRS)
    print('  PPRS …')
    rows = query_all(url, PPRS_FIELDS, where="LOCATION = 'Offshore'")
    need(len(rows) >= MIN_PPRS_ROWS, f'PPRS: bare {len(rows)} rader')
    vol = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    info = {}
    storage = set()
    for f in rows:
        a = f['attributes']
        unit = norm(a['UNITNAME'])
        if 'STORAGE' in unit or 'INJECTION' in unit:
            storage.add(unit)
            continue
        name, ym = norm(a['FIELDNAME']), str(a['PERIODYRMN'])
        need(re.fullmatch(r'\d{6}', ym), f'PPRS: uventet periode {ym!r}')
        v = vol[(name, ym)]
        v[0] += (a['OILPRODM3'] or 0) + (a['GCONDVOL'] or 0)       # m³
        v[1] += (a['AGASPROKSM'] or 0) + (a['DGASPROKSM'] or 0)    # 1000 Sm³
        v[2] += a['WATPRODVOL'] or 0                               # m³
        # Siste periode bestemmer område og operatør.
        if name not in info or ym >= info[name]['ym']:
            info[name] = {'ym': ym, 'area': a['FIELDAREA'], 'operator': a['ORGGRPNM']}
    print(f'    {len(rows)} rader, {len(info)} felt; lagre utelatt: {", ".join(sorted(storage)) or "ingen"}')
    monthly = collections.defaultdict(list)
    for (name, ym), (oil, gas, wat) in sorted(vol.items()):
        if oil or gas or wat:
            # mill Sm³ olje, mrd Sm³ gass, mill m³ vann
            monthly[name].append([f'{ym[:4]}-{ym[4:]}', round(oil / 1e6, 6), round(gas / 1e6, 6), round(wat / 1e6, 6)])
    return info, monthly


# ── Eierandeler ──────────────────────────────────────────────────────────────
def build_partners():
    import openpyxl   # bare her, så resten av skriptet kan kjøres uten
    print('  Field Partners …')
    raw = get(PARTNERS_XLSX, binary=True)
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    need('Report 1' in wb.sheetnames, f'Field Partners: fant ikke arket «Report 1» ({wb.sheetnames})')
    it = wb['Report 1'].iter_rows(values_only=True)
    head = [str(h).strip() if h else '' for h in next(it)]
    need(head[:len(PARTNER_COLS)] == PARTNER_COLS, f'Field Partners: uventede kolonner {head}')
    day = lambda d: d.strftime('%Y-%m-%d') if isinstance(d, (dt.date, dt.datetime)) else None
    by = collections.defaultdict(lambda: {'status': '', 'rows': []})
    n = 0
    for r in it:
        if not r or not r[0]:
            continue
        n += 1
        name, org = norm(r[0]), strip_regno(r[4])
        pct = float(r[5] or 0)
        start, end = day(r[7]), day(r[8])
        need(start, f'Field Partners: {name}/{org} mangler startdato')
        if end and end <= start:
            continue   # hendelser uten varighet er ikke et aktivt intervall
        x = by[name]
        x['status'] = str(r[3] or '').strip()
        x['rows'].append([org, round(pct, 4), start, end, 1 if str(r[6]).upper() == 'Y' else 0])
    need(n >= MIN_PARTNER_ROWS, f'Field Partners: bare {n} rader')
    print(f'    {n} rader, {len(by)} felt')
    return by


# ── Lisenser ─────────────────────────────────────────────────────────────────
LIC_FIELDS = ['OBJECTID', 'SUBAREAID', 'SUBAREANAM', 'BLOCKREF', 'LICTYPE', 'LICREF', 'LICSUBTYPE',
              'LICSTARTDT', 'LICENDDT', 'LICSTATUS', 'RNDNO', 'OPORGGRP', 'EQORG', 'EQORGGRP', 'EQGRPHOLD', 'EQUITY']


def build_licences():
    url = layer_url(ITEM_LICENCES)
    print('  Lisensunderområder …')
    rows = query_all(url, LIC_FIELDS, geometry=True)
    need(len(rows) >= MIN_LICENCE_ROWS, f'lisenser: bare {len(rows)} rader')
    ms = lambda v: dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).strftime('%Y-%m-%d') if v is not None else None
    groups = collections.Counter()   # juridisk enhet → konsern, talt opp
    subareas, licences = {}, {}
    for f in rows:
        a = f['attributes']
        sid = str(a['SUBAREAID'])
        lic = norm(a['LICREF'])
        orgs, grps = split_pct(a['EQORG']), split_pct(a['EQORGGRP'])
        # Navnene står i samme rekkefølge med samme prosent; bare da stoler vi på parene.
        if len(orgs) == len(grps) and all(o[1] == g[1] for o, g in zip(orgs, grps)):
            for (o, _), (g, _) in zip(orgs, grps):
                groups[(o, g)] += 1
        s = subareas.setdefault(sid, {'lic': lic, 'name': pretty(a['SUBAREANAM']), 'block': norm(a['BLOCKREF']),
                                      'holders': {}, 'rings': rings_of(f.get('geometry'))})
        grp = pretty(a['EQGRPHOLD'])
        s['holders'][grp] = round(s['holders'].get(grp, 0) + float(a['EQUITY'] or 0), 4)
        L = licences.setdefault(lic, {'type': str(a['LICSUBTYPE'] or a['LICTYPE'] or '').strip(),
                                      'status': str(a['LICSTATUS'] or '').strip(),
                                      'round': str(a['RNDNO'] or ''), 'start': ms(a['LICSTARTDT']),
                                      'end': ms(a['LICENDDT']), 'operators': set()})
        if a['OPORGGRP']:
            L['operators'].add(pretty(a['OPORGGRP']))
    print(f'    {len(rows)} rader, {len(subareas)} underområder, {len(licences)} lisenser')
    # Mest brukte konsern per juridisk enhet.
    org_group = {}
    for (o, g), _ in sorted(groups.items(), key=lambda kv: -kv[1]):
        org_group.setdefault(o, pretty(g))
    for L in licences.values():
        L['operators'] = sorted(L['operators'])
    for s in subareas.values():
        s['holders'] = sorted(([g, v] for g, v in s['holders'].items()), key=lambda x: (-x[1], x[0]))
    return subareas, licences, org_group


# ── Feltomriss ───────────────────────────────────────────────────────────────
def build_outlines():
    url = layer_url(ITEM_FIELDS)
    print('  Feltomriss …')
    rows = query_all(url, ['OBJECTID', 'FIELD_NO', 'FIELDNAME', 'FD_STAT'], geometry=True)
    need(len(rows) >= MIN_OUTLINES, f'feltomriss: bare {len(rows)}')
    out = {}
    for f in rows:
        a = f['attributes']
        name = norm(a['FIELDNAME'])
        rings = rings_of(f.get('geometry'))
        if not rings:
            continue
        o = out.setdefault(name, {'no': str(a['FIELD_NO'] or ''), 'stat': str(a['FD_STAT'] or ''), 'rings': []})
        o['rings'].extend(rings)
    print(f'    {len(out)} felt med omriss')
    return out


def compact(rows):
    """[[måned, olje, gass, vann], …] → [første måned, [olje…], [gass…], [vann…]]
    i 1000 Sm³ / mill Sm³ / 1000 m³, én verdi per måned uten hull."""
    idx = lambda ym: int(ym[:4]) * 12 + int(ym[5:]) - 1
    i0 = idx(rows[0][0])
    n = idx(rows[-1][0]) - i0 + 1
    cols = [[0] * n for _ in range(3)]
    for ym, oil, gas, wat in rows:
        i = idx(ym) - i0
        for c, v in zip(cols, (oil, gas, wat)):
            x = round(v * 1000, 3)
            c[i] = int(x) if x == int(x) else x
    return [rows[0][0]] + cols


def write_if_changed(name, payload):
    path = os.path.join(OUT_DIR, name)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            if fh.read() == text:
                print(f'  = {name} uendret')
                return False
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
    os.replace(tmp, path)
    print(f'  + {name} skrevet ({len(text.encode("utf-8")) / 1024:.0f} KB)')
    return True


def main():
    print('Henter NSTA-data …')
    info, monthly = build_production()
    partners = build_partners()
    subareas, licences, org_group = build_licences()
    outlines = build_outlines()

    group_of = make_group_of(org_group)
    # Feltlisten: alle offshorefelt med produksjon i PPRS. Slug fra navnet.
    names = sorted(monthly)
    slugs, used = {}, set()
    for n in names:
        s = base = slugify(n)
        i = 2
        while s in used:
            s, i = f'{base}_{i}', i + 1
        used.add(s)
        slugs[n] = s
    today = dt.date.today().isoformat()
    fields = []
    matched_owners = matched_outline = 0
    for n in names:
        p = partners.get(n)
        rows = [[group_of(o), pct, a, b, op, pretty(o)] for o, pct, a, b, op in (p['rows'] if p else [])]
        rows.sort(key=lambda r: (r[2], -r[1], r[0]))
        cur = [r for r in rows if r[2] <= today and (r[3] is None or r[3] > today)]
        # Gjeldende andeler per konsern; operatøren er konsernet med operatørflagg.
        owners = collections.defaultdict(float)
        for r in cur:
            owners[r[0]] += r[1]
        op = next((r[0] for r in cur if r[4]), None) or pretty(info[n]['operator'] or '')
        matched_owners += bool(p)
        ok = outline_key(n, outlines)
        matched_outline += bool(ok)
        first, last = monthly[n][0][0], monthly[n][-1][0]
        fields.append({
            'slug': slugs[n], 'name': field_title(n), 'outline': slugify(ok) if ok else None, 'area': info[n]['area'] or '', 'operator': op,
            'status': re.sub(r'^\d+\s*-\s*', '', p['status']).capitalize() if p else '',
            'owners': sorted(([c, round(v, 4)] for c, v in owners.items() if v > 0), key=lambda x: (-x[1], x[0])),
            # [konsern, andel %, fra, til (null = gjeldende), operatør 1/0, juridisk enhet]
            'equity': rows,
            'first': first, 'last': last,
        })
    print(f'  {len(fields)} felt · eierandeler for {matched_owners} · omriss for {matched_outline}')
    need(matched_owners >= len(fields) * 0.6, 'for få felt fikk eierandeler — har NSTA endret feltnavnene?')

    periods = sorted({r[0] for rows in monthly.values() for r in rows})
    # Første år der eierandelene dekker minst 95 % av produksjonen hvert år
    # etterpå. Før det er Field Partners for ufullstendig til selskapstall.
    cover = collections.defaultdict(lambda: [0.0, 0.0])
    for f in fields:
        for ym, oil, gas, _ in monthly[next(k for k in names if slugs[k] == f['slug'])]:
            mid = ym + '-15'
            tot = sum(r[1] for r in f['equity'] if r[2] <= mid and (r[3] is None or r[3] > mid))
            c = cover[int(ym[:4])]
            c[1] += oil + gas
            if 99 <= tot <= 101:
                c[0] += oil + gas
    equity_from = periods[-1][:4]
    for y in sorted(cover, reverse=True):
        if cover[y][1] and cover[y][0] / cover[y][1] < 0.95:
            break
        equity_from = str(y)
    print(f'  eierandelene dekker produksjonen fra {equity_from}')
    meta = {'updated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source': 'North Sea Transition Authority (NSTA): PPRS, Field Partners, field determinations, licence subareas',
            'firstPeriod': periods[0], 'lastPeriod': periods[-1], 'equityFrom': int(equity_from)}
    ukcs = {**meta, 'units': {'oil': '1000 Sm3 (oil + condensate)', 'gas': 'mill Sm3', 'water': '1000 m3'},
            # Per felt: første måned og sammenhengende månedsserier fra den.
            'columns': ['first month', 'oil', 'gas', 'water'],
            'fields': fields,
            'production': {slugs[n]: compact(monthly[n]) for n in names}}
    outl = {'fields': {slugify(n): {'name': pretty(n), 'stat': o['stat'], 'rings': o['rings']}
                       for n, o in sorted(outlines.items())}}
    lic = {'updated': meta['updated'],
           'licences': {k: licences[k] for k in sorted(licences)},
           'subareas': {k: subareas[k] for k in sorted(subareas, key=int)}}
    # Det bygges ikke noe nytt bare fordi tidsstempelet endres: skriv
    # tidsstempelet bare når innholdet ellers har endret seg.
    changed = False
    for name, payload in [('ukcs.json', ukcs), ('ukcs_outlines.json', outl), ('ukcs_licences.json', lic)]:
        path = os.path.join(OUT_DIR, name)
        if os.path.exists(path) and 'updated' in payload:
            with open(path, encoding='utf-8') as fh:
                old = json.load(fh)
            if {**old, 'updated': payload['updated']} == json.loads(json.dumps(payload)):
                print(f'  = {name} uendret')
                continue
        changed |= write_if_changed(name, payload)
    print('Ferdig.' if changed else 'Ingen endringer.')


if __name__ == '__main__':
    main()
