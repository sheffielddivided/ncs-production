"""
Henter data for den amerikanske delen av Mexicogolfen (Gulf of America OCS,
føderalt område) fra Bureau of Ocean Energy Management (BOEM) og bygger
data/gom.json, data/gom_prod.json og data/gom_wells.json til gom.html.

Kilder (www.data.boem.gov, alle åpne):
  - OGOR-A: månedlig produksjon per brønnkomplettering, 1947– (én zip per år,
    eldre år samlet). Summeres per felt, per operatør, per lease og totalt.
  - Field & Reserves: «Estimated Oil and Gas Reserves, December 31, YYYY»
    (siste utgave finnes automatisk): Table 4 (alle felt med funnår, vanndyp,
    type, kallenavn, opprinnelige og gjenværende reserver P10/P50/P90/snitt),
    Table 9 (reserver og produksjon for hele området per år) og reserve-
    historikken per felt og år.
  - Borehole (5010): alle brønner med spud-dato, type, status, vanndyp og
    posisjon.
  - Lease Owner: dagens eierandeler per lease, til netto produksjon.
  - Company (All): selskapsnavn.
  - Platform Masters/Structures/Locations: innretninger.

Kontroller (exit 1, ingenting skrives): minst 1 000 000 produksjonsrader og
800 felt, ingen år i produksjonen som mangler mellom første og siste,
produksjonen siste hele år innenfor rimelige grenser, og akkumulert
produksjon per felt fra OGOR-A innenfor 3 % av BOEMs egen akkumulerte
produksjon i reservetabellen for de store feltene.

    python fetch_boem.py               # hent alt
    python fetch_boem.py --cache DIR   # gjenbruk nedlastede filer når BOEM
                                       # ikke har endret dem (HEAD-sjekk)
"""

import argparse
import calendar
import collections
import csv
import datetime as dt
import io
import json
import os
import re
import statistics
import sys
import tempfile
import time
import zipfile

import requests

BASE = 'https://www.data.boem.gov'
UA = 'Mozilla/5.0 (compatible; ncs-insight data fetch)'
OUT = 'data/gom.json'
OUT_PROD = 'data/gom_prod.json'
OUT_WELLS = 'data/gom_wells.json'
OUT_CMP = 'data/gom_company.json'   # selskap × felt per år, til Company med ett selskap valgt
CMP_FIELD_MIN = 1000        # felt med minst så mye (tusen fat o.e.) for selskapet siden 1985 får egen serie

MCF_PER_BOE = 5.62          # BOEMs omregning: 5 620 kubikkfot gass per fat o.e.
DEEP_FT = 1000              # BOEM: dypvann fra 1 000 fot
ULTRA_FT = 5000             # og ultradypvann fra 5 000 fot
FIELD_PROD_FROM = 1990      # månedlige serier per felt fra dette året (totalen går fra 1947)
OPS_FROM = 1985             # opererte volumer per konsern fra dette året
WELL_LIST_FROM = 2015       # brønner med alle detaljer fra dette spud-året
RECENT_FROM = dt.date.today().year - 3   # felt × operatør fra dette året


class DataError(Exception):
    pass


# ---------------------------------------------------------------- nett

S = requests.Session()
S.headers['User-Agent'] = UA


def get(url, binary=False, tries=5):
    last = None
    for k in range(tries):
        try:
            r = S.get(url, timeout=300)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content if binary else r.text
        except requests.RequestException as e:
            last = e
            time.sleep(5 * 2 ** k)
    raise DataError(f'{url}: {last}')


class Store:
    """Nedlastede filer. Med --cache gjenbrukes en fil når størrelse og
    Last-Modified fra BOEM er uendret."""

    def __init__(self, cache):
        self.dir = cache or tempfile.mkdtemp(prefix='boem-')
        os.makedirs(self.dir, exist_ok=True)
        self.cached = bool(cache)

    def path(self, rel):
        url = BASE + '/' + rel.lstrip('/').replace(' ', '%20')
        name = re.sub(r'[^A-Za-z0-9._-]', '_', rel.rsplit('/', 1)[1])
        p = os.path.join(self.dir, name)
        meta = p + '.meta'
        if self.cached and os.path.exists(p) and os.path.exists(meta):
            try:
                h = S.head(url, timeout=60, allow_redirects=True)
                stamp = f"{h.headers.get('Content-Length')}|{h.headers.get('Last-Modified')}"
                if h.ok and open(meta).read() == stamp and zipfile.is_zipfile(p):
                    return p
            except requests.RequestException:
                if zipfile.is_zipfile(p):
                    return p
        for k in range(5):
            try:
                with S.get(url, timeout=600, stream=True) as r:
                    if r.status_code == 404:
                        return None
                    r.raise_for_status()
                    with open(p, 'wb') as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
                    stamp = f"{r.headers.get('Content-Length')}|{r.headers.get('Last-Modified')}"
                if not zipfile.is_zipfile(p):
                    return None          # BOEM svarer med en HTML-side for filer som ikke finnes
                with open(meta, 'w') as fh:
                    fh.write(stamp)
                return p
            except requests.RequestException as e:
                last = e
                time.sleep(5 * 2 ** k)
        raise DataError(f'{url}: {last}')


def zip_lines(path, suffix=None):
    with zipfile.ZipFile(path) as zf:
        for n in zf.namelist():
            if suffix and not n.lower().endswith(suffix):
                continue
            with zf.open(n) as fh:
                yield from io.TextIOWrapper(fh, 'latin-1')


def zip_csv(path):
    return csv.reader(zip_lines(path))


def links(page_html, pattern):
    return sorted(set(re.findall(pattern, page_html)))


# ---------------------------------------------------------------- selskaper

# Konsern fra navnet på det juridiske selskapet. Rekkefølgen betyr noe.
GROUPS = [
    (r'^SHELL|^ENTERPRISE OIL', 'Shell'),
    (r'^BP |^BP$|^AMOCO|^ARCO|^ATLANTIC RICHFIELD|^VASTAR|^STANOLIND', 'bp'),
    (r'^CHEVRON|^TEXACO|^UNOCAL|^UNION OIL CO.* CALIFORNIA|^GULF OIL', 'Chevron'),
    (r'^HESS|^AMERADA HESS|^NOBLE ENERGY|^TEXAS CO|^THE TEXAS CO', 'Chevron'),
    (r'^EXXON|^MOBIL|^XTO', 'ExxonMobil'),
    (r'^ANADARKO|^OCCIDENTAL|^OXY|^KERR.?MCGEE', 'Oxy'),
    (r'^CONOCO|^PHILLIPS|^BURLINGTON', 'ConocoPhillips'),
    (r'^EQUINOR|^STATOIL', 'Equinor'),
    (r'^BHP|^WOODSIDE', 'Woodside'),
    (r'^TALOS|^ENVEN|^ENERGY RESOURCE TECHNOLOGY|^ILX|^QUARTERNORTH|^STONE ENERGY|^ENERGY XXI|^EPL OIL', 'Talos'),
    (r'^FIELDWOOD|^GOM SHELF|^APACHE|^APA |^MARINER', 'Fieldwood/Apache'),
    (r'^LLOG', 'LLOG'),
    (r'^MURPHY|^MP GULF OF MEXICO', 'Murphy'),
    (r'^W ?& ?T|^W&T', 'W&T Offshore'),
    (r'^BEACON|^BOE EXPLORATION', 'Beacon'),
    (r'^KOSMOS|^DEEP GULF', 'Kosmos'),
    (r'^ARENA', 'Arena'),
    (r'^CANTIUM', 'Cantium'),
    (r'^WALTER', 'Walter'),
    (r'^COX ', 'Cox'),
    (r'^REPSOL', 'Repsol'),
    (r'^PETROBRAS', 'Petrobras'),
    (r'^ECOPETROL', 'Ecopetrol'),
    (r'^TOTAL|^ELF ', 'TotalEnergies'),
    (r'^ENI |^ENI$|^AGIP', 'Eni'),
    (r'^INPEX', 'Inpex'),
    (r'^MITSUI|^MOEX', 'Mitsui'),
    (r'^NEXEN|^CNOOC', 'CNOOC'),
    (r'^MARATHON', 'Marathon'),
    (r'^DEVON', 'Devon'),
    (r'^SHENANDOAH', 'Shenandoah'),
    (r'^RIDGEWOOD', 'Ridgewood'),
    (r'^HOUSTON ENERGY', 'Houston Energy'),
    (r'^RED WILLOW', 'Red Willow'),
    (r'^GULFSLOPE', 'GulfSlope'),
    (r'^MCMORAN|^FREEPORT', 'McMoRan'),
    (r'^MARINER', 'Mariner'),
    (r'^POGO', 'Pogo'),
    (r'^TENNECO', 'Tenneco'),
    (r'^PENNZ', 'Pennzoil'),
    (r'^SAMEDAN', 'Chevron'),          # Noble Energys tidligere navn
    (r'^ODECO', 'Murphy'),             # Ocean Drilling & Exploration, Murphys datterselskap
    (r'^SUN |^SUN$|^ORYX', 'Sun/Oryx'),
    (r'^CNG |^DOMINION', 'Dominion'),
    (r'^ENSERCH|^EP OPERATING', 'Enserch'),
]
GROUPS = [(re.compile(p, re.I), g) for p, g in GROUPS]


def group_of(name):
    n = (name or '').strip()
    for rx, g in GROUPS:
        if rx.search(n):
            return g
    n = re.sub(r'\b(L\.?L\.?C\.?|INC\.?|CORP(ORATION)?\.?|COMPANY|CO\.?|LTD\.?|L\.?P\.?|USA|U\.S\.A\.?|'
               r'OFFSHORE|EXPLORATION|PRODUCTION|PETROLEUM|ENERGY|OIL|GAS|& ?|THE|OF|AMERICA|NORTH|GULF)\b',
               ' ', n, flags=re.I)
    n = re.sub(r'[,.]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    n = n.title() if n else (name or '').strip().title()
    # Romertall og forkortelser som .title() ødelegger
    n = re.sub(r'\b(Ii|Iii|Iv|Vi|Vii)\b', lambda m: m.group(1).upper(), n)
    return re.sub(r'\b(Gom|Ecp|Heq|Tep|Ogog|Me)\b', lambda m: {'Gom': 'GoM'}.get(m.group(1), m.group(1).upper()), n)


# ---------------------------------------------------------------- produksjon

def read_ogor(store):
    """OGOR-A: summer per felt/måned, operatør/måned, lease/måned og totalt.
    Et år som finnes i flere filer (inneværende år ligger både i ogoradelimit
    og eventuelt ogoraYYYY) telles bare fra filen med flest rader for året."""
    page = get(BASE + '/Main/OGOR-A.aspx')
    files = links(page, r'(Production/Files/ogora[0-9-]*delimit\.zip)')
    if len(files) < 20:
        raise DataError(f'only {len(files)} OGOR-A files on the page')
    per_file = {}
    opname = {}
    for rel in files:
        p = store.path(rel)
        if not p:
            print(f'  {rel}: not available, skipped')
            continue
        agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0, 0.0]))
        cnt = collections.Counter()
        for r in zip_csv(p):
            if len(r) < 14:
                continue
            ym = r[2].strip()
            if len(ym) != 6 or not ym.isdigit():
                continue
            try:
                o, g, w = float(r[5] or 0), float(r[6] or 0), float(r[7] or 0)
            except ValueError:
                continue
            y = ym[:4]
            cnt[y] += 1
            f = r[13].strip()
            op = r[11].strip()
            if op:
                opname[op] = r[12].strip()
            lease = r[0].strip()
            keys = [('F', f), ('O', op), ('L', lease)]
            if int(y) >= RECENT_FROM:
                keys.append(('X', f + '\t' + op))     # felt × operatør, til feltoperatøren
                keys.append(('Y', lease + '\t' + f))  # lease × felt, til egenandel per felt
            if int(y) >= OPS_FROM:
                # Per år (ikke måned), til selskap × felt: lease × felt og felt × operatør
                keys.append(('Z', lease + '\t' + f, y))
                keys.append(('W', f + '\t' + op, y))
            for key in keys:
                a = agg[y][(key[0], key[1], key[2] if len(key) > 2 else ym)]
                a[0] += o
                a[1] += g
                a[2] += w
        per_file[rel] = (cnt, agg)
        print(f'  {os.path.basename(rel)}: {sum(cnt.values()):,} rows, years {min(cnt)}–{max(cnt)}' if cnt else f'  {rel}: empty')
    best = {}
    for rel, (cnt, _) in per_file.items():
        for y, n in cnt.items():
            if y not in best or n > per_file[best[y]][0][y]:
                best[y] = rel
    fld = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    opr = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    lease = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    fxo = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    ylf = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    lfy = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    foy = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    tot = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    rows = 0
    for y, rel in sorted(best.items()):
        cnt, agg = per_file[rel]
        rows += cnt[y]
        for (kind, key, ym), v in agg[y].items():
            tgt = {'F': fld, 'O': opr, 'L': lease, 'X': fxo, 'Y': ylf, 'Z': lfy, 'W': foy}[kind]
            a = tgt[(key, ym)]
            for k in range(3):
                a[k] += v[k]
            if kind == 'F':
                b = tot[ym]
                for k in range(3):
                    b[k] += v[k]
    years = sorted(int(y) for y in best)
    missing = [y for y in range(years[0], years[-1] + 1) if y not in years]
    if rows < 1_000_000:
        raise DataError(f'only {rows:,} production rows')
    if missing:
        raise DataError(f'production years missing: {missing}')
    print(f'  {rows:,} rows, {years[0]}–{years[-1]}')
    return fld, opr, lease, fxo, ylf, lfy, foy, tot, opname


def ym_add(ym, n):
    y, m = int(ym[:4]), int(ym[4:])
    k = y * 12 + m - 1 + n
    return f'{k // 12}{k % 12 + 1:02d}'


def ym_range(a, b):
    out = []
    while a <= b:
        out.append(a)
        a = ym_add(a, 1)
    return out


def last_complete(tot):
    """Siste måned med fullstendig rapportering: operatørene rapporterer
    sent, så de siste månedene er ofte for lave. Måneder på slutten med
    mindre enn 85 % av medianen for de seks foregående kuttes."""
    ks = sorted(k for k in tot if tot[k][0] + tot[k][1] > 0)
    while len(ks) > 7:
        prev = statistics.median(tot[k][0] + tot[k][1] / MCF_PER_BOE for k in ks[-7:-1])
        cur = tot[ks[-1]][0] + tot[ks[-1]][1] / MCF_PER_BOE
        if cur >= 0.85 * prev:
            break
        ks.pop()
    return ks[0], ks[-1]


# ---------------------------------------------------------------- reserver

def read_reserves(store):
    import openpyxl
    page = get(BASE + '/Main/FieldReserves.aspx')
    pages = links(page, r'HtmlPage\.aspx\?page=estimated(\d{4})')
    if not pages:
        raise DataError('no reserve reports on the Field & Reserves page')
    year = max(pages)
    rp = get(f'{BASE}/Main/HtmlPage.aspx?page=estimated{year}')
    tables = next(iter(links(rp, r'(FieldReserves/Files/[^"]*Tables[^"]*\.zip)')), None)
    hist = next(iter(links(rp, r'(FieldReserves/Files/[^"]*Hist[^"]*\.zip)')), None)
    if not tables or not hist:
        raise DataError(f'reserve report {year}: tables or history missing')

    def workbook(rel):
        p = store.path(rel)
        with zipfile.ZipFile(p) as zf:
            n = next(n for n in zf.namelist() if n.lower().endswith('.xlsx'))
            return openpyxl.load_workbook(io.BytesIO(zf.read(n)), read_only=True, data_only=True)

    wb = workbook(tables)
    t4 = next(ws for ws in wb.worksheets if re.search(r'table\s*4', ws.title, re.I))
    fields = {}
    for r in t4.iter_rows(values_only=True):
        r = list(r)
        if len(r) < 16 or not isinstance(r[1], (int, float)) or not isinstance(r[2], str):
            continue
        code = r[2].strip()
        num = lambda v: float(v) if isinstance(v, (int, float)) else None
        fields[code] = {
            'rank': int(r[1]), 'nick': (r[3] or '').strip() if isinstance(r[3], str) else '',
            'disc': int(r[4]) if isinstance(r[4], (int, float)) else None,
            'wd': int(r[5]) if isinstance(r[5], (int, float)) else None,
            'type': (r[6] or '').strip() if isinstance(r[6], str) else '',
            'orig': [num(r[k]) for k in (7, 8, 9, 10)],        # P10, P50, P90, snitt (MMBOE)
            'cum': num(r[11]),
            'rem': [num(r[k]) for k in (12, 13, 14, 15)],
        }
    if len(fields) < 800:
        raise DataError(f'reserve table 4: only {len(fields)} fields')
    t9 = next(ws for ws in wb.worksheets if re.search(r'table\s*9', ws.title, re.I))
    # [år, antall felt, opprinnelig olje/gass/BOE, akkumulert olje/gass/BOE,
    # gjenværende olje/gass/BOE] i mrd. fat / Tcf / mrd. fat o.e.
    area = []
    for r in t9.iter_rows(values_only=True):
        v = [x for x in r if x is not None and x != '']
        if len(v) >= 11 and isinstance(v[0], (int, float)) and 1900 < v[0] < 2100 and all(isinstance(x, (int, float)) for x in v[1:11]):
            area.append([int(v[0]), int(v[1])] + [round(float(x), 3) for x in v[2:11]])
    if len(area) < 10:
        raise DataError(f'reserve table 9: {len(area)} years')

    wbh = workbook(hist)
    ws = next(ws for ws in wbh.worksheets if 'HIST' in ws.title.upper()) if any('HIST' in w.title.upper() for w in wbh.worksheets) else wbh.worksheets[0]
    # Kolonnene leses etter overskrift: arket har tomme kolonner innimellom
    history = collections.defaultdict(list)
    col = None
    for r in ws.iter_rows(values_only=True):
        if col is None:
            hdr = [str(c or '').strip().upper() for c in r]
            if 'FIELD' in hdr and 'YEAR' in hdr:
                col = {k: hdr.index(k) for k in ('FIELD', 'YEAR', 'ORIG BOE RESERVES', 'CUMBOE', 'BOE RESERVES')}
            continue
        f, y = r[col['FIELD']], r[col['YEAR']]
        if not isinstance(f, str) or not isinstance(y, (int, float)):
            continue
        # [år, opprinnelig BOE, akkumulert BOE, gjenværende BOE] i MMBOE
        history[f.strip()].append([int(y)] + [round((r[col[k]] or 0) / 1e6, 2) for k in ('ORIG BOE RESERVES', 'CUMBOE', 'BOE RESERVES')])
    if not col or len(history) < 500:
        raise DataError(f'reserve history: {len(history)} fields')
    return {'year': year, 'fields': fields, 'area': area, 'history': history}


# ---------------------------------------------------------------- brønner

WELL_TYPES = {'E': 'exploration', 'D': 'development'}
WELL_STATUS = {
    'COM': 'Completed', 'PA': 'Plugged and abandoned', 'TA': 'Temporarily abandoned', 'DRL': 'Drilling',
    'ST': 'Sidetracked', 'CNL': 'Cancelled', 'DSI': 'Drilling suspended', 'AST': 'Approved sidetrack',
    'APD': 'Approved permit to drill', 'VCW': 'Volume chamber well', 'BP': 'Bypass', 'SI': 'Shut in',
    'PRO': 'Producing', 'INJ': 'Injection', 'TD': 'Total depth reached', 'RIG': 'Rig on location',
}


def depth_class(ft):
    if ft is None:
        return None
    return 'ultra' if ft >= ULTRA_FT else 'deep' if ft >= DEEP_FT else 'shelf'


def read_wells(store, names):
    p = store.path('Well/Files/5010delimit.zip')
    if not p:
        raise DataError('no borehole file')
    counts = collections.Counter()
    loc = collections.defaultdict(list)
    detail = []
    n = 0
    for r in zip_csv(p):
        if len(r) < 29:
            continue
        n += 1
        spud = r[5].strip()
        typ = WELL_TYPES.get(r[16].strip(), 'other')
        try:
            wd = int(r[19]) if r[19].strip() else None
        except ValueError:
            wd = None
        dc = depth_class(wd) or 'unknown'
        if len(spud) == 8 and spud.isdigit():
            counts[(spud[:4], typ, dc)] += 1
        f = r[4].strip()
        try:
            lon, lat = float(r[22] or r[20]), float(r[23] or r[21])
        except ValueError:
            lon = lat = None
        if f and lon and lat and -100 < lon < -80 and 20 < lat < 32:
            loc[f].append((lon, lat))
        if len(spud) == 8 and spud[:4] >= str(WELL_LIST_FROM):
            blk = r[13].strip() or r[11].strip()
            area, block = (blk[:2], blk[2:].strip().lstrip('0')) if blk else ('', '')
            num = lambda v: int(v) if v.strip().isdigit() else None
            detail.append([
                r[0].strip(), f'{area} {block} {r[1].strip()}{r[2].strip()[:0]}'.strip(), typ,
                names.get(r[3].strip(), r[3].strip()), f or None, spud, r[14].strip() or None,
                r[18].strip(), wd, num(r[8]), num(r[9]),
                round(lat, 4) if lat else None, round(lon, 4) if lon else None,
            ])
    if n < 30000:
        raise DataError(f'only {n} boreholes')
    field_loc = {}
    for f, pts in loc.items():
        field_loc[f] = [round(statistics.median(x for x, _ in pts), 3), round(statistics.median(y for _, y in pts), 3)]
    detail.sort(key=lambda w: w[5])
    years = sorted({k[0] for k in counts})
    cnt = {}
    for (y, t, d), v in counts.items():
        cnt.setdefault(t, {}).setdefault(d, {})[y] = v
    return {'n': n, 'counts': cnt, 'years': [years[0], years[-1]], 'list': detail}, field_loc


# ---------------------------------------------------------------- innretninger

def read_owners(store):
    """Eierhistorikk per lease: {lease: {eiergruppe: [(fra YYYYMM, {selskap: andel 0–1}), …]}}.

    Sluttdatoen er tom i filen, så en eierfordeling gjelder fra virknings-
    datoen til neste. En overdragelse består av rader med fortløpende
    løpenummer som til sammen gir 100 %; flere overdragelser kan ha samme
    virkningsdato, og da gjelder den siste. Eiergruppen skiller dybde- eller
    arealdelte rettigheter i samme lease."""
    rows = collections.defaultdict(list)
    current = collections.defaultdict(dict)
    for r in zip_csv(store.path('Leasing/Files/lseowndelimit.zip')):
        if len(r) < 11:
            continue
        try:
            rid, pct = int(r[1]), float(r[9] or 0)
        except ValueError:
            continue
        eff = (r[10].strip() or r[2].strip())[:6]
        if len(eff) != 6:
            continue
        key = (r[5].strip(), r[8].strip())
        rows[key].append((rid, eff, r[4].strip(), pct))
        if r[6].strip() == 'C':
            current[key][r[4].strip()] = current[key].get(r[4].strip(), 0) + pct
    out = collections.defaultdict(dict)
    agree = 0
    for key, rs in rows.items():
        rs.sort()
        snaps, cur, cum, eff = [], {}, 0.0, ''
        for rid, e, comp, pct in rs:
            cur[comp] = cur.get(comp, 0) + pct
            cum += pct
            eff = max(eff, e)
            if cum >= 99.5:
                snaps.append((eff, cur))
                cur, cum, eff = {}, 0.0, ''
        if cur:
            snaps.append((eff, cur))
        tl = {}
        for e, sh in snaps:
            tot = sum(sh.values()) or 1
            tl[e] = {c: v / tot for c, v in sh.items()}        # siste med samme dato vinner
        tl = sorted(tl.items())
        out[key[0]][key[1]] = tl
        if key in current:
            c = current[key]
            t = sum(c.values()) or 1
            last = tl[-1][1]
            if all(abs(last.get(k, 0) - v / t) < 0.01 for k, v in c.items()):
                agree += 1
    print(f'  {len(out)} leases; latest ownership matches BOEM\'s current owners for {agree}/{len(current)} lease groups')
    if current and agree < 0.9 * len(current):
        raise DataError('ownership history does not reproduce the current owners')
    return out


def shares_at(timeline, ym):
    """Andeler per selskap for en lease i måneden ym: snitt over eiergruppene
    som har en eierfordeling da. None hvis ingen."""
    import bisect
    res, n = collections.defaultdict(float), 0
    for gc, tl in timeline.items():
        i = bisect.bisect_right([e for e, _ in tl], ym) - 1
        if i < 0:
            continue
        n += 1
        for c, v in tl[i][1].items():
            res[c] += v
    if not n:
        return None
    return {c: v / n for c, v in res.items()}


def fixed(line, start, length):
    return line[start - 1:start - 1 + length].strip()


def read_platforms(store, names):
    mast, stru, locs = {}, collections.defaultdict(list), {}
    for line in zip_lines(store.path('Platform/Files/platmastfixed.zip')):
        cid = fixed(line, 1, 8)
        if cid:
            mast[cid] = {'company': fixed(line, 21, 5), 'wd': fixed(line, 51, 5), 'field': fixed(line, 70, 8),
                         'major': fixed(line, 27, 1), 'area': fixed(line, 88, 2), 'block': fixed(line, 90, 6)}
    for line in zip_lines(store.path('Platform/Files/platstrufixed.zip')):
        cid = fixed(line, 9, 8)
        if cid:
            stru[cid].append({'install': fixed(line, 20, 11), 'removed': fixed(line, 44, 11),
                              'name': fixed(line, 67, 15), 'type': fixed(line, 85, 5),
                              'slots': fixed(line, 58, 3)})
    for line in zip_lines(store.path('Platform/Files/platlocfixed.zip')):
        cid = fixed(line, 4, 8)
        try:
            lon, lat = float(fixed(line, 86, 14)), float(fixed(line, 100, 13))
        except ValueError:
            continue
        locs.setdefault(cid, (lon, lat))
    out = []
    for cid, m in mast.items():
        ss = stru.get(cid, [])
        live = [s for s in ss if not s['removed']]
        if not live or cid not in locs:
            continue
        try:
            wd = int(m['wd'])
        except ValueError:
            wd = None
        inst = min((s['install'] for s in live if s['install']), default='')
        y = re.search(r'(\d{4})', inst)
        lon, lat = locs[cid]
        # Navnet står ofte i parentes etter strukturbokstaven: «A (Mad Dog)»,
        # og er kuttet til 15 tegn i kilden
        nm = next((s['name'] for s in live if '(' in s['name']), live[0]['name'])
        m2 = re.search(r'\(([^)]*)\)?', nm)
        name = m2.group(1).strip() if m2 and m2.group(1).strip() else nm
        out.append([cid, name, m['area'] + ' ' + m['block'].lstrip('0'), wd, int(y.group(1)) if y else None,
                    live[0]['type'], m['field'] or None, group_of(names.get(m['company'], '')),
                    1 if m['major'] == 'Y' else 0, round(lat, 4), round(lon, 4)])
    if len(out) < 200:
        raise DataError(f'only {len(out)} platforms')
    return out


# ---------------------------------------------------------------- hoved

def cn(x):
    """Kompakt tall: heltall fra 100, ellers én desimal (filstørrelsen)."""
    if x is None:
        return None
    return int(round(x)) if abs(x) >= 100 else round(x, 1)


def r1(v):
    return cn(v / 1000)             # bbl → tusen fat, Mcf → MMcf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', help='folder for downloaded files')
    args = ap.parse_args()
    store = Store(args.cache)
    try:
        print('Companies')
        names = {}
        for r in zip_csv(store.path('Company/Files/compalldelimit.zip')):
            if len(r) > 3:
                names[r[0].strip()] = (r[3] or r[2]).strip()
        print(f'  {len(names)} companies')

        print('Production (OGOR-A)')
        fld, opr, lease, fxo, ylf, lfy, foy, tot, opname = read_ogor(store)
        names.update({k: v for k, v in opname.items() if k not in names})
        first, last = last_complete(tot)
        months = ym_range(first, last)
        print(f'  complete months {first}–{last}')

        print('Reserves')
        res = read_reserves(store)
        print(f'  report {res["year"]}: {len(res["fields"])} fields')

        print('Wells')
        wells, field_loc = read_wells(store, names)
        print(f'  {wells["n"]} boreholes, {len(wells["list"])} listed since {WELL_LIST_FROM}')

        print('Platforms')
        plats = read_platforms(store, names)
        print(f'  {len(plats)} standing complexes')

        print('Lease owners')
        owners = read_owners(store)
    except DataError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1

    # ---- felt: månedsserier, nøkkeltall og metadata
    rf = res['fields']
    by_field = collections.defaultdict(dict)
    for (f, ym), v in fld.items():
        if first <= ym <= last:
            by_field[f][ym] = v
    win12 = months[-12:]
    days12 = sum(calendar.monthrange(int(m[:4]), int(m[4:]))[1] for m in win12)
    # Operatør per felt: konsernet med mest opererte volumer (fat o.e.) i feltet
    # de siste 24 hele månedene
    win24 = set(months[-24:])
    fop_v = collections.defaultdict(collections.Counter)
    for (key, ym), v in fxo.items():
        if ym in win24:
            f, op = key.split('\t')
            fop_v[f][op] += v[0] + v[1] / MCF_PER_BOE
    fop = {f: names.get(c.most_common(1)[0][0], c.most_common(1)[0][0]) for f, c in fop_v.items() if c}
    fields_out = []
    prod_out = {}
    codes = set(by_field) | set(rf)
    cum_check = []
    for f in sorted(codes):
        if not f or f == 'WILD':          # WILD = wildcat, brønner utenfor felt
            continue
        series = by_field.get(f, {})
        r = rf.get(f, {})
        cum_oil = sum(v[0] for v in series.values())
        cum_gas = sum(v[1] for v in series.values())
        cum_boe = (cum_oil + cum_gas / MCF_PER_BOE) / 1e6
        prod_ms = sorted(m for m, v in series.items() if v[0] or v[1])
        l12 = [series.get(m, [0, 0, 0]) for m in win12]
        oil12 = sum(v[0] for v in l12) / days12
        gas12 = sum(v[1] for v in l12) / days12
        wd = r.get('wd')
        if r.get('cum') and r['cum'] > 50 and r.get('disc'):
            cum_check.append((f, cum_boe, r['cum']))
        rr = lambda xs: [round(x, 2) if x is not None else None for x in xs] if xs else None
        fields_out.append({
            'code': f, 'name': r.get('nick') or '', 'disc': r.get('disc'), 'wd': wd, 'dc': depth_class(wd),
            'type': r.get('type') or None, 'rank': r.get('rank'),
            'orig': rr(r.get('orig')), 'cumR': round(r['cum'], 2) if r.get('cum') is not None else None, 'rem': rr(r.get('rem')),
            'first': prod_ms[0] if prod_ms else None, 'last': prod_ms[-1] if prod_ms else None,
            'cum': [round(cum_oil / 1e6, 3), round(cum_gas / 1e6, 3)],     # MMbbl, Bcf
            'l12': [round(oil12), round(gas12)],                          # bbl/d, Mcf/d
            'loc': field_loc.get(f),
        })
        # Månedsserie fra 1990 for felt som produserer nå, eller som har gitt
        # minst 1 mill. fat o.e. siden 1990; resten havner i «Other fields»
        p0 = f'{FIELD_PROD_FROM}01'
        since = sum(v[0] + v[1] / MCF_PER_BOE for m, v in series.items() if m >= p0) / 1e6
        if prod_ms and prod_ms[-1] >= p0 and oil12 + gas12 > 0:
            # i produksjon: månedlig
            s = max(prod_ms[0], p0)
            ms = ym_range(s, prod_ms[-1])
            prod_out[f] = [s] + [[r1(series.get(m, [0, 0, 0])[k]) for m in ms] for k in range(3)]
        elif prod_ms and prod_ms[-1] >= p0 and since >= 1:
            # avsluttet: årlig (sum per år), start som 'YYYY'
            ys = list(range(max(int(prod_ms[0][:4]), FIELD_PROD_FROM), int(prod_ms[-1][:4]) + 1))
            prod_out[f] = [str(ys[0])] + [[r1(sum(v[k] for m, v in series.items() if m[:4] == str(y))) for y in ys] for k in range(3)]

    # Kontroll: akkumulert produksjon fra OGOR-A mot BOEMs reservetabell
    # (som gjelder til og med reserveåret; OGOR-A går lenger, så vi sammenligner
    # med produksjonen til og med 31.12 i reserveåret)
    ry = str(res['year'])
    bad = []
    for f, _, cum_r in cum_check:
        s = by_field.get(f, {})
        c = sum(v[0] + v[1] / MCF_PER_BOE for m, v in s.items() if m[:4] <= ry) / 1e6
        if abs(c - cum_r) > 0.03 * cum_r + 1:
            bad.append((f, round(c, 1), cum_r))
    ok_share = 1 - len(bad) / max(1, len(cum_check))
    print(f'  cumulative check: {len(cum_check) - len(bad)}/{len(cum_check)} large fields within 3 % of BOEM')
    if ok_share < 0.9:
        print(f'ERROR: cumulative production differs from BOEM for {bad[:10]}', file=sys.stderr)
        return 1

    # ---- totaler: hele området og per vanndypsklasse
    dc_of = {fo['code']: fo['dc'] for fo in fields_out}
    total = [[r1(tot.get(m, [0, 0, 0])[k]) for m in months] for k in range(3)]
    by_depth = {c: [[0.0] * len(months) for _ in range(2)] for c in ('shelf', 'deep', 'ultra', 'unknown')}
    idx = {m: i for i, m in enumerate(months)}
    for (f, ym), v in fld.items():
        i = idx.get(ym)
        if i is None:
            continue
        c = dc_of.get(f) or 'unknown'
        by_depth[c][0][i] += v[0]
        by_depth[c][1][i] += v[1]
    by_depth = {c: [[r1(x) for x in s] for s in v] for c, v in by_depth.items() if any(any(s) for s in v)}
    y_last = int(last[:4]) - (0 if last.endswith('12') else 1)
    oil_y = sum(tot.get(m, [0])[0] for m in months if m.startswith(str(y_last))) / (366 if calendar.isleap(y_last) else 365)
    if not 500_000 < oil_y < 3_500_000:
        print(f'ERROR: implausible oil production {oil_y:,.0f} bbl/d in {y_last}', file=sys.stderr)
        return 1

    # ---- operatører (opererte volumer, historiske) per konsern
    grp = collections.defaultdict(lambda: [[0.0] * len(months), [0.0] * len(months)])
    gl12 = collections.Counter()
    legal = collections.defaultdict(collections.Counter)
    for (op, ym), v in opr.items():
        i = idx.get(ym)
        if i is None:
            continue
        g = group_of(names.get(op, op))
        grp[g][0][i] += v[0]
        grp[g][1][i] += v[1]
        if ym in win12:
            gl12[g] += v[0] + v[1] / MCF_PER_BOE
            legal[g][names.get(op, op)] += v[0] + v[1] / MCF_PER_BOE
    # de største: topp 15 siste 12 måneder og topp 25 i noe år siden 1985
    peak = collections.Counter()
    for g, (o, ga) in grp.items():
        for y in range(1985, int(last[:4]) + 1):
            s = sum(o[i] + ga[i] / MCF_PER_BOE for m, i in idx.items() if m.startswith(str(y)))
            peak[g] = max(peak[g], s)
    keep = {g for g, _ in gl12.most_common(15)} | {g for g, _ in peak.most_common(25)}
    o0 = next(i for i, m in enumerate(months) if m >= f'{OPS_FROM}01')
    ops = {'start': months[o0], 'groups': {g: [[r1(x) for x in s[o0:]] for s in grp[g]] for g in keep}}

    for fo in fields_out:
        op = fop.get(fo['code'])
        fo['op'] = group_of(op) if op else None
        fo['opLegal'] = op

    # ---- egenandel (equity): produksjon per lease og måned × eierandelene
    # som gjaldt den måneden, summert per konsern
    eq = collections.defaultdict(lambda: [[0.0] * len(months), [0.0] * len(months)])
    cov_y = collections.defaultdict(lambda: [0.0, 0.0])
    gname = {}
    for (ls, ym), v in lease.items():
        i = idx.get(ym)
        if i is None:
            continue
        b = v[0] + v[1] / MCF_PER_BOE
        cov_y[ym[:4]][1] += b
        tl = owners.get(ls)
        sh = shares_at(tl, ym) if tl else None
        if not sh:
            continue
        cov_y[ym[:4]][0] += b
        for comp, frac in sh.items():
            g = gname.get(comp) or gname.setdefault(comp, group_of(names.get(comp, comp)))
            a = eq[g]
            a[0][i] += v[0] * frac
            a[1][i] += v[1] * frac
    eq12 = collections.Counter()
    eqpeak = collections.Counter()
    for g, (o, ga) in eq.items():
        eq12[g] = sum(o[idx[m]] + ga[idx[m]] / MCF_PER_BOE for m in win12)
        for y in range(OPS_FROM, int(last[:4]) + 1):
            eqpeak[g] = max(eqpeak[g], sum(o[i] + ga[i] / MCF_PER_BOE for m, i in idx.items() if m.startswith(str(y))))
    eq_keep = {g for g, _ in eq12.most_common(20)} | {g for g, _ in eqpeak.most_common(25)}
    equity = {'start': months[o0], 'groups': {g: [[r1(x) for x in s[o0:]] for s in eq[g]] for g in eq_keep},
              'coverage': {y: round(c / t, 3) for y, (c, t) in sorted(cov_y.items()) if t and int(y) >= OPS_FROM}}
    net_out = [[g, round(v / days12)] for g, v in eq12.most_common() if v / days12 >= 50]
    cov12 = sum(cov_y[y][0] for y in {m[:4] for m in win12}) / max(1, sum(cov_y[y][1] for y in {m[:4] for m in win12}))
    print(f'  equity: ownership covers {cov12:.1%} of production in the last 12 months')
    if cov12 < 0.9:
        print('ERROR: lease ownership covers too little of recent production', file=sys.stderr)
        return 1
    # Egenandel per felt for hvert konsern, siste 12 måneder
    eqf = collections.defaultdict(collections.Counter)
    w12 = set(win12)
    for (key, ym), v in ylf.items():
        if ym not in w12:
            continue
        ls, f = key.split('\t')
        tl = owners.get(ls)
        sh = shares_at(tl, ym) if tl else None
        if not sh or not f or f == 'WILD':
            continue
        b = v[0] + v[1] / MCF_PER_BOE
        for comp, frac in sh.items():
            eqf[gname.get(comp) or group_of(names.get(comp, comp))][f] += b * frac
    equity_fields = {g: [[f, round(v / days12)] for f, v in c.most_common(40) if v / days12 >= 10]
                     for g, c in eqf.items() if sum(c.values()) / days12 >= 50}
    # Egenandeler per felt i dag (snitt over feltets leaser vektet med produksjon siste 12 mnd)
    fshare = collections.defaultdict(collections.Counter)
    ftot = collections.Counter()
    for g, c in eqf.items():
        for f, v in c.items():
            fshare[f][g] += v
            ftot[f] += v
    field_eq = {f: [[g, round(v / ftot[f] * 100, 1)] for g, v in c.most_common() if v / ftot[f] >= 0.005]
                for f, c in fshare.items() if ftot[f] > 0}

    # ---- selskap × felt per år fra 1985, til Company med ett selskap valgt.
    # Egenandel: leasens månedsproduksjon × eierandelene den måneden, fordelt
    # på feltene i leasen etter feltenes andel av leasens olje og gass det året.
    # Opererte: produksjonen fra kompletteringene konsernet opererte, per felt.
    y0, y1 = OPS_FROM, int(last[:4])
    ny = y1 - y0 + 1
    lf_frac = collections.defaultdict(dict)       # (lease, år) → {felt: (andel olje, andel gass)}
    lf_tot = collections.defaultdict(lambda: [0.0, 0.0])
    for (key, y), v in lfy.items():
        ls, f = key.split('\t')
        lf_tot[(ls, y)][0] += v[0]
        lf_tot[(ls, y)][1] += v[1]
    for (key, y), v in lfy.items():
        ls, f = key.split('\t')
        t = lf_tot[(ls, y)]
        if f and f != 'WILD' and (v[0] or v[1]):
            lf_frac[(ls, y)][f] = (v[0] / t[0] if t[0] else 0.0, v[1] / t[1] if t[1] else 0.0)
    new = lambda: [[0.0] * ny, [0.0] * ny]
    cf_eq = collections.defaultdict(lambda: collections.defaultdict(new))
    for (ls, ym), v in lease.items():
        if ym > last or int(ym[:4]) < y0:
            continue
        fr = lf_frac.get((ls, ym[:4]))
        tl = owners.get(ls)
        if not fr or not tl:
            continue
        sh = shares_at(tl, ym)
        if not sh:
            continue
        j = int(ym[:4]) - y0
        for comp, frac in sh.items():
            g = gname.get(comp) or gname.setdefault(comp, group_of(names.get(comp, comp)))
            if g not in eq_keep:
                continue
            for f, (fo, fg) in fr.items():
                a = cf_eq[g][f]
                a[0][j] += v[0] * frac * fo
                a[1][j] += v[1] * frac * fg
    cf_op = collections.defaultdict(lambda: collections.defaultdict(new))
    for (key, y), v in foy.items():
        f, op = key.split('\t')
        if not f or f == 'WILD' or int(y) > y1:
            continue
        g = group_of(names.get(op, op))
        if g not in keep:
            continue
        a = cf_op[g][f]
        a[0][int(y) - y0] += v[0]
        a[1][int(y) - y0] += v[1]

    def cmp_out(cf):
        # [første årindeks, [olje…], [gass…]] uten nuller i endene; små felt utelates
        out = {}
        for g, fs in cf.items():
            o = {}
            for f, (oil, gas) in fs.items():
                if sum(oil) + sum(gas) / MCF_PER_BOE < CMP_FIELD_MIN:
                    continue
                nz = [i for i in range(ny) if oil[i] or gas[i]]
                a, b = nz[0], nz[-1] + 1
                o[f] = [a, [r1(x) for x in oil[a:b]], [r1(x) for x in gas[a:b]]]
            out[g] = o
        return out
    cmp_payload = {'from': y0, 'last': last, 'units': 'thousand bbl / MMcf per year',
                   'equity': cmp_out(cf_eq), 'operated': cmp_out(cf_op)}

    fetched = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    cmp_payload['fetched'] = fetched
    # Feltene som rader (kolonnene i FIELD_COLS) for å holde filen liten
    FIELD_COLS = ['code', 'name', 'disc', 'wd', 'dc', 'type', 'rank', 'orig', 'cumR', 'rem', 'first', 'last', 'cum', 'l12', 'loc', 'op', 'opLegal']
    fields_rows = [[fo.get(k) for k in FIELD_COLS] for fo in fields_out]
    payload = {
        'source': 'Bureau of Ocean Energy Management (BOEM), Gulf of America OCS Region',
        'fetched': fetched,
        'months': [first, last],
        'units': {'oil': 'thousand bbl per month', 'gas': 'million cubic feet (MMcf) per month', 'water': 'thousand bbl per month',
                  'boe': f'{MCF_PER_BOE} Mcf per boe (BOEM)', 'reserves': 'million boe (MMBOE)'},
        'total': total,
        'byDepth': by_depth,
        'operators': ops,
        'operatorsLegal': {g: [n for n, _ in legal[g].most_common(4)] for g in keep},
        'net12': {'window': [win12[0], win12[-1]], 'coverage': round(cov12, 3), 'groups': net_out},
        'equity': equity,
        'equityFields': equity_fields,
        'fieldEquity': field_eq,
        'fieldCols': FIELD_COLS,
        'fields': fields_rows,
        'reserves': {'year': res['year'], 'area': res['area']},
        'platforms': plats,
        'wells': {'n': wells['n'], 'years': wells['years'], 'counts': wells['counts']},
    }
    # Reservehistorikk for felt i produksjon og felt med minst 10 mill. fat o.e.
    live = {fo['code'] for fo in fields_out if fo['l12'][0] + fo['l12'][1] > 0}
    hist = {}
    for f, h in res['history'].items():
        if f in live or ((rf.get(f, {}).get('orig') or [None] * 4)[3] or 0) >= 10:
            h = sorted(h)
            ys = list(range(h[0][0], h[-1][0] + 1))
            by = {r[0]: r for r in h}
            # [første år, [opprinnelig…], [akkumulert…], [gjenværende…]] i MMBOE
            hist[f] = [ys[0]] + [[cn((by.get(y) or [None, None, None, None])[k]) for y in ys] for k in (1, 2, 3)]
    prod_payload = {'fetched': fetched, 'fields': prod_out, 'resHist': hist}
    wells_payload = {'fetched': fetched, 'from': WELL_LIST_FROM,
                     'cols': ['api', 'name', 'type', 'operator', 'field', 'spud', 'td', 'status', 'wd', 'md', 'tvd', 'lat', 'lon'],
                     'status': WELL_STATUS, 'wells': wells['list']}

    # Uendret innhold gir ingen commit
    for path, obj in ((OUT, payload), (OUT_PROD, prod_payload), (OUT_WELLS, wells_payload), (OUT_CMP, cmp_payload)):
        try:
            old = json.load(open(path, encoding='utf-8'))
            if {**old, 'fetched': None} == {**obj, 'fetched': None}:
                obj['fetched'] = old['fetched']
        except (OSError, ValueError):
            pass
        os.makedirs('data', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(obj, fh, ensure_ascii=False, separators=(',', ':'))
            fh.write('\n')
        print(f'Wrote {path} ({os.path.getsize(path) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
