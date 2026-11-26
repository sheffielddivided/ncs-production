"""
Henter data for Angola fra Agência Nacional de Petróleo, Gás e
Biocombustíveis (ANPG) og bygger data/angola.json til angola.html.

Kilden er JSON-API-et bak ANPGs eget nettsted (https://www.anpg.co.ao/api/…),
som svarer uten innlogging:

  - production-reports: de månedlige produksjonsrapportene («Resumo mensal
    sobre a produção petrolífera», jan. 2020–) som HTML. Tallene leses ut av
    teksten: olje (fat, prognose, landets andel), gass (produsert, prognose,
    injisert, til ALNG, til kraft, fakling), ALNG-anlegget, driftseffektivitet,
    løftinger, borerigger, brønnarbeid og boremeter, og fra september 2024
    tabellen med olje og gass per blokk.
  - production-monthly: de samme månedstallene i fast struktur (fra juni
    2026), med operatørenes andel, fordeling på vanndyp og brønner per blokk.
    Brukes foran teksten der begge finnes, og til kontroll.
  - production-historical: årlig oljeproduksjon fra 1955.
  - blocks: blokkregisteret (211 blokker) med status, operatør, konsortium med
    andeler, basseng, vanndyp, areal og midtpunkt.
  - exploration-seismic-campaigns, gas-map-*: seismikk og gassinfrastruktur
    til kartet.

API-ets «production» (per blokk og år 2018–2025) brukes ikke: tallene er
runde, summen per år stemmer ikke med landets produksjon, og de ser ut som
plassholdere.

Kontroller (exit 1, ingenting skrives): minst 60 månedsrapporter uten hull fra
januar 2020; olje per måned fra teksten innenfor 0,5 % av de strukturerte
månedstallene der begge finnes; blokktabellen skal summere til TOTAL-raden og
til landets produksjon innenfor 1 %; årssnittet fra månedene innenfor 2 % av
ANPGs årstall for hele år.

    python fetch_anpg.py
"""

import ast
import collections
import datetime as dt
import html
import json
import os
import re
import sys
import time
import unicodedata

import requests

API = 'https://www.anpg.co.ao/api/'
UA = 'Mozilla/5.0 (compatible; ncs-insight data fetch)'
OUT = 'data/angola.json'
MONTHS = {'JANEIRO': 1, 'FEVEREIRO': 2, 'MARCO': 3, 'ABRIL': 4, 'MAIO': 5, 'JUNHO': 6, 'JULHO': 7,
          'AGOSTO': 8, 'SETEMBRO': 9, 'OUTUBRO': 10, 'NOVEMBRO': 11, 'DEZEMBRO': 12}
MCF_PER_BOE = 6.0           # omregning gass → o.e. på siden (6 000 ft³ per fat)


class DataError(Exception):
    pass


def log(*a):
    print(*a, flush=True)


S = requests.Session()
S.headers['User-Agent'] = UA


def api(coll, **params):
    """Alle rader i en samling (sider à 500)."""
    out, off = [], 0
    while True:
        for k in range(5):
            try:
                r = S.get(API + coll, params={'limit': 500, 'offset': off, **params}, timeout=120)
                r.raise_for_status()
                d = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                if k == 4:
                    raise DataError(f'{coll}: {e}')
                time.sleep(4 * 2 ** k)
        rows = d.get('data') if isinstance(d, dict) else d
        out += rows or []
        total = d.get('total', len(out)) if isinstance(d, dict) else len(out)
        off += 500
        if not rows or len(out) >= total:
            return out


def key(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', s).strip().upper()


def bkey(s):
    """Blokknøkkel: «Bloco 3/05 A» = «Bloco 3/05A» = «3/05A»."""
    k = key(s)
    k = re.sub(r'^BLOCO\s*', '', k)
    k = re.sub(r'\s*(ASSOCIACOES|ASSOCIACAO)$', '', k)
    k = k.replace('NGC', 'NCG')
    k = re.sub(r'^NCG\b.*', 'NCG', k)
    k = re.sub(r'\s*&.*$', '', k)          # «14K & A-IMI» → «14K»
    k = re.sub(r'_(M|G|VF)$', '', k)        # kartlagenes delflater («Bloco 17_M»)
    return k.replace(' ', '').replace('-', '')


def num(s):
    """«32 078 208», «1.034.781», «93,48» → tall."""
    if s is None:
        return None
    s = str(s).strip().replace('\xa0', ' ')
    if not s:
        return None
    s = s.replace(' ', '')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(\.\d{3})+', s):
        s = s.replace('.', '')
    try:
        return float(s)
    except ValueError:
        return None


def lit(v):
    """Feltene i API-et er ofte Python-literaler som tekst («[{'share': 39.2, …}]»)."""
    if v is None or isinstance(v, (list, dict, int, float)):
        return v
    s = str(v).strip()
    if s in ('', 'None', 'null'):
        return None
    for f in (json.loads, ast.literal_eval):
        try:
            return f(s)
        except (ValueError, SyntaxError):
            pass
    return v


def days_in(y, m):
    return (dt.date(y + m // 12, m % 12 + 1, 1) - dt.date(y, m, 1)).days


def cn(x, d=1):
    if x is None:
        return None
    return int(round(x)) if abs(x) >= 100 else round(x, d)


# ---------------------------------------------------------------- rapportene

N = r'(\d{1,3}(?:[ . ]\d{3})*(?:,\d+)?)'


def find(pat, t, flags=re.I):
    m = re.search(pat, t, flags)
    return num(m.group(1)) if m else None


def text_of(content):
    t = re.sub(r'<[^>]+>', ' ', content or '')
    return re.sub(r'\s+', ' ', html.unescape(t)).strip()


def block_table(content):
    """Tabellen «Blocos | Óleo (BOPD) | Gás produzido (MMSCFD)» som rader."""
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', content or '', re.S | re.I):
        cells = [text_of(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S | re.I)]
        if len(cells) < 3:
            continue
        name = cells[0].strip()
        o, g = num(cells[1]), num(cells[2])
        if o is None or not name or key(name).startswith('BLOCOS'):
            continue
        rows.append([name, o, g])
    return rows


def parse_report(r):
    title = key(r.get('title'))
    if 'ANUAL' in title:
        return None
    m = re.search(r'(JANEIRO|FEVEREIRO|MARCO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)\s*(?:DE\s*)?/?\s*(\d{4})', title)
    if not m:
        return None
    y, mo = int(m.group(2)), MONTHS[m.group(1)]
    t = text_of(r.get('content'))
    d = days_in(y, mo)
    oil_bbl = find(r'produ[çc][ãa]o de (?:[óo]leo|petr[óo]leo)(?: bruto)?(?: (?:de|em) Angola)?[^.]{0,40}? foi de ' + N + r' barris', t)
    rec = {
        'ym': f'{y}{mo:02d}', 'date': (r.get('report_date') or '')[:10], 'slug': r.get('slug'),
        'oilBbl': oil_bbl,
        'oil': oil_bbl / d if oil_bbl else None,
        'oilFc': find(r'contra (?:os |a previs[ãa]o inicial de )?' + N + r' (?:BOPD|barris de petr[óo]leo por dia)', t),
        'share': find(r'quota parte do pa[íi]s foi de ' + N + r' barris', t),
        'gasMmscf': find(r'produ[çc][ãa]o de g[áa]s(?: associado)?[^.]{0,40}? foi de ' + N + r' milh[õo]es de p[ée]s c[úu]bicos', t),
        'gasFc': find(r'(?:abaixo|acima) da previs[ãa]o de ' + N + r' MMSCFD', t),
        'gasInj': find(N + r' MMSCFD (?:foi |foram )?(?:re)?inje(?:c)?tad', t),
        'gasAlng': find(N + r' MMSCFD (?:foi |foram )?disponibilizad\w* (?:para |a |à )?(?:a )?f[áa]brica', t),
        'gasPower': find(N + r' MMSCFD (?:foi |foram )?(?:utilizad\w* )?para (?:a )?gera[çc][ãa]o de energia', t),
        'flare': find(r'queima de g[áa]s natural foi de ' + N + r' MMSCFD', t),
        'alngBoe': find(r'f[áa]brica (?:de )?ALNG[^.]{0,60}?produ[çc][ãa]o de ' + N + r' (?:BOE|barris de [óo]leo equivalente)', t)
                   or find(r'f[áa]brica (?:de )?ALNG produziu ' + N + r' (?:BOE|barris de [óo]leo equivalente)', t),
        'eff': find(r'efici[êe]ncia operacional das instala[çc][õo]es foi de ' + N + r' ?%', t),
        'effFc': find(r'efici[êe]ncia operacional das instala[çc][õo]es foi de [\d,]+ ?% contra ' + N + r' ?%', t),
        'lift': find(r'levantamentos(?: de petr[óo]leo bruto)?(?: de Angola)? foram de ' + N + r' barris', t),
        'rigs': find(r'\((\d+)\) unidades de sondagem', t),
        'wellsWorked': find(r'trabalhos em [\w\s-]{0,30}?\((\d+)\) po[çc]os', t),
        'drilledM': find(r'(?:perfura[çc][ãa]o de|perfurados|perfazendo um total de perfura[çc][ãa]o de) ' + N + r' metros', t),
        'blocks': block_table(r.get('content')),
    }
    if rec['gasMmscf']:
        rec['gas'] = rec['gasMmscf'] / d
    else:
        rec['gas'] = find(r'correspondente a (?:uma )?m[ée]dia(?: di[áa]ria)? de ' + N + r' (?:MMSCFD|milh[õo]es de p[ée]s c[úu]bicos)', t)
    if rec['alngBoe']:
        rec['alng'] = rec['alngBoe'] / d
    if rec['lift']:
        rec['liftD'] = rec['lift'] / d
    return rec


# ---------------------------------------------------------------- selskaper

GROUPS = [
    (r'^ssi\b|sonangol sinopec', 'SSI (Sonangol Sinopec)'),
    (r'sonangol|^snl\b', 'Sonangol'),
    (r'^total', 'TotalEnergies'),
    (r'^azule', 'Azule Energy'),
    (r'^etu ', 'ETU Energias'),
    (r'^esso|exxon', 'ExxonMobil'),
    (r'chevron|cabinda gulf', 'Chevron'),
    (r'^trident', 'Trident Energy'),
    (r'^eni\b', 'Eni'),
    (r'^bp\b', 'bp'),
    (r'equinor|statoil', 'Equinor'),
    (r'^shell', 'Shell'),
    (r'petronas', 'Petronas'),
    (r'^ssi\b|sonangol sinopec', 'SSI (Sonangol Sinopec)'),
    (r'sinopec', 'Sinopec'),
    (r'^cnooc', 'CNOOC'),
    (r'^inpex', 'INPEX'),
    (r'^galp', 'Galp'),
    (r'^soc\.? nat\.? des p[ée]t\.? du congo|snpc', 'SNPC (Congo)'),
    (r'^afentra', 'Afentra'),
    (r'^somoil', 'Somoil'),
    (r'^sirius', 'Sirius'),
    (r'^namcor', 'NAMCOR'),
]


def group_of(name):
    n = key(name).lower()
    for p, g in GROUPS:
        if re.search(p, n):
            return g
    s = re.sub(r'\s+(S\.?A\.?U?|Lda\.?|Ltd\.?|B\.?V\.?|Ang(ola)?|Block [\w ]+|Prod(uction)?)$', '', str(name).strip(), flags=re.I)
    return s.strip() or str(name).strip()


# ---------------------------------------------------------------- hoved

def build():
    log('Rapporter…')
    reports = api('production-reports', status='published')
    recs = {}
    for r in reports:
        p = parse_report(r)
        if p and p['oilBbl'] and (p['ym'] not in recs or (p['date'] or '') > (recs[p['ym']]['date'] or '')):
            recs[p['ym']] = p
    if len(recs) < 60:
        raise DataError(f'Bare {len(recs)} lesbare månedsrapporter')
    yms = sorted(recs)
    first, last = yms[0], yms[-1]
    ms, y, m = [], int(first[:4]), int(first[4:])
    while f'{y}{m:02d}' <= last:
        ms.append(f'{y}{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    missing = [x for x in ms if x not in recs]
    if missing:
        raise DataError(f'Mangler månedsrapporter for {missing}')
    log(f'  {len(recs)} måneder {first}–{last}')

    log('Strukturerte månedstall…')
    monthly = {f"{int(r['ref_year'])}{int(r['ref_month']):02d}": r for r in api('production-monthly', status='published')}
    bad = []
    for ym, r in monthly.items():
        if ym in recs and recs[ym]['oil']:
            a, b = recs[ym]['oil'], num(r.get('oil_daily_bopd'))
            if b and abs(a / b - 1) > 0.005:
                bad.append(f'{ym}: tekst {a:.0f}, tabell {b:.0f}')
    if bad:
        raise DataError('Oljen i rapportteksten avviker fra månedstallene: ' + '; '.join(bad))
    # Strukturerte tall foran teksten der de finnes
    for ym, r in monthly.items():
        if ym not in recs:
            continue
        rc = recs[ym]
        for k, f in (('oil', 'oil_daily_bopd'), ('oilFc', 'oil_daily_forecast_bopd'), ('gas', 'gas_daily_mmscfd'),
                     ('gasFc', 'gas_daily_forecast_mmscfd'), ('flare', 'gas_flared_mmscfd'), ('eff', 'efficiency_pct'),
                     ('effFc', 'efficiency_forecast_pct')):
            v = num(r.get(f))
            if v is not None:
                rc[k] = v
        sb = num(r.get('country_share_bopd'))
        if sb is not None:
            rc['share'] = sb * days_in(int(ym[:4]), int(ym[4:]))
        pb = lit(r.get('production_by_block')) or []
        if pb:
            rc['blocks'] = [[x.get('block'), num(x.get('oil_bopd')), num(x.get('gas_mmscfd'))] for x in pb]

    # Blokktabellene: sum = TOTAL-rad = landets produksjon
    bad = []
    for ym in ms:
        rows = recs[ym]['blocks']
        if not rows:
            continue
        tot = [r for r in rows if key(r[0]) == 'TOTAL']
        body = [r for r in rows if key(r[0]) != 'TOTAL']
        s = sum(r[1] or 0 for r in body)
        ref = tot[0][1] if tot else recs[ym]['oil']
        if ref and abs(s / ref - 1) > 0.01:
            bad.append(f'{ym}: blokker {s:.0f}, total {ref:.0f}')
        if recs[ym]['oil'] and abs(s / recs[ym]['oil'] - 1) > 0.01:
            bad.append(f'{ym}: blokker {s:.0f}, land {recs[ym]["oil"]:.0f}')
        recs[ym]['blocks'] = body
    if bad:
        raise DataError('Blokktabellen stemmer ikke: ' + '; '.join(bad))

    log('Årstall…')
    hist = sorted((int(r['year']), num(r.get('oil_bopd'))) for r in api('production-historical', status='published') if r.get('oil_bopd'))
    hy = dict(hist)
    bad = []
    for y in sorted({int(x[:4]) for x in ms}):
        months = [x for x in ms if x.startswith(str(y))]
        if len(months) < 12 or y not in hy:
            continue
        avg = sum(recs[x]['oilBbl'] for x in months) / sum(days_in(y, int(x[4:])) for x in months)
        if abs(avg / hy[y] - 1) > 0.02:
            bad.append(f'{y}: måneder {avg:.0f}, år {hy[y]:.0f}')
    if bad:
        raise DataError('Månedene stemmer ikke med årstallene: ' + '; '.join(bad))
    log('  kontroller: OK')

    log('Blokker, seismikk, gass…')
    blocks_raw = api('blocks')
    blocks = []
    for b in blocks_raw:
        if str(b.get('is_active', '1')) in ('0', 'False'):
            continue
        cons = lit(b.get('consortium')) or []
        if not isinstance(cons, list):
            cons = []
        holders = [[c.get('company'), num(c.get('share')), bool(c.get('operator'))] for c in cons if isinstance(c, dict) and c.get('company')]
        co = lit(b.get('coordinates'))
        loc = [round(float(co['lng']), 3), round(float(co['lat']), 3)] if isinstance(co, dict) and co.get('lat') and co.get('lng') else None
        blocks.append({
            'name': b.get('block_name'), 'key': bkey(b.get('block_name')), 'basin': b.get('basin'), 'status': b.get('status'),
            'type': b.get('block_type'), 'offer': b.get('offer_type'), 'depth': b.get('depth_category'),
            'wd': num(b.get('water_depth_m')), 'area': num(b.get('area_km2')), 'op': b.get('operator') if b.get('operator') not in (None, 'None') else None,
            'holders': holders, 'loc': loc,
            'desc': (b.get('description') if b.get('description') not in (None, 'None') else None),
            'fpso': b.get('fpso_name') if b.get('fpso_name') not in (None, 'None') else None,
            'disc': b.get('discovery_year') if b.get('discovery_year') not in (None, 'None') else None,
        })
    by_key = {b['key']: b for b in blocks}
    seis = []
    for s in api('exploration-seismic-campaigns'):
        g = lit(s.get('geometry')) or {}
        coords = g.get('coordinates') if isinstance(g, dict) else None
        if not coords:
            continue

        def fix(p):
            a, b2 = float(p[0]), float(p[1])
            return [round(b2, 3), round(a, 3)] if a < 0 and b2 > 0 else [round(a, 3), round(b2, 3)]   # [lat, lon] → [lon, lat]
        if g.get('type') == 'LineString':
            geom = ['L', [fix(p) for p in coords]]
        elif g.get('type') == 'Polygon':
            ring = coords[0] if coords and isinstance(coords[0], list) and coords[0] and isinstance(coords[0][0], list) else coords
            geom = ['P', [fix(p) for p in ring]]
        elif g.get('type') == 'Point':
            geom = ['T', fix(coords)]
        else:
            continue
        seis.append([s.get('name'), s.get('survey_code'), s.get('year'), (s.get('survey_type') or '').upper(), s.get('operator'),
                     num(s.get('coverage')), s.get('coverage_unit'), s.get('basin_code'), geom])
    gas = {'features': [], 'pipelines': [], 'hubs': []}
    for f in api('gas-map-features'):
        if str(f.get('is_active', 1)) in ('0', 'False') or not f.get('lat'):
            continue
        gas['features'].append([f.get('kind'), f.get('category'), f.get('name') or f.get('label'), f.get('status'), f.get('operator'),
                                round(float(f['lng']), 3), round(float(f['lat']), 3), (f.get('description') or '')[:240]])
    for p in api('gas-map-pipelines'):
        path = lit(p.get('path'))
        if isinstance(path, list) and path:
            gas['pipelines'].append([p.get('name'), p.get('style'), [[round(float(a), 3), round(float(b2), 3)] for a, b2 in path]])
    for h in api('gas-map-hubs'):
        if str(h.get('is_active', 1)) in ('0', 'False'):
            continue
        gas['hubs'].append({'key': h.get('hub_key'), 'name': h.get('name'), 'subtitle': h.get('subtitle'), 'basins': h.get('basins'),
                            'headline': h.get('headline'), 'reserves': lit(h.get('reserves')), 'stats': lit(h.get('stats'))})

    # Produksjon per blokk og måned (fra blokktabellene)
    block_names = {}
    per_block = collections.defaultdict(dict)
    for ym in ms:
        for name, o, g in recs[ym]['blocks']:
            k = bkey(name)
            block_names.setdefault(k, name)
            per_block[k][ym] = [o or 0, g or 0]
    bms = [ym for ym in ms if recs[ym]['blocks']]
    prod_blocks = []
    for k, ser in per_block.items():
        reg = by_key.get(k)
        last12 = bms[-12:]
        l12 = [sum(ser.get(x, [0, 0])[i] * days_in(int(x[:4]), int(x[4:])) for x in last12) / sum(days_in(int(x[:4]), int(x[4:])) for x in last12) for i in (0, 1)]
        prod_blocks.append({
            'key': k, 'name': block_names[k], 'reg': reg['name'] if reg else None,
            'oil': [cn(ser.get(x, [None])[0]) if x in ser else None for x in bms],
            'gas': [cn(ser.get(x, [None, None])[1]) if x in ser else None for x in bms],
            'l12': [cn(l12[0]), cn(l12[1])],
        })
    prod_blocks.sort(key=lambda b: -b['l12'][0])
    unmatched = [b['name'] for b in prod_blocks if not b['reg'] and b['l12'][0] > 100]
    if unmatched:
        log(f'  ! produserende blokker uten treff i registeret: {unmatched}')

    # Egenandel med dagens andeler i registeret
    eq = collections.defaultdict(lambda: [0.0] * len(bms))
    eq_blocks = collections.defaultdict(list)
    cov = [0.0, 0.0]
    for pb in prod_blocks:
        reg = by_key.get(pb['key'])
        hs = reg['holders'] if reg else []
        tot = sum(h[1] or 0 for h in hs)
        for i, v in enumerate(pb['oil']):
            if v is None:
                continue
            if i >= len(bms) - 12:
                cov[1] += v
                if hs and abs(tot - 100) < 1.5:
                    cov[0] += v
            if not hs or not tot:
                continue
            shares = collections.Counter()
            for h in hs:
                shares[group_of(h[0])] += (h[1] or 0) / tot
            for g, s in shares.items():
                eq[g][i] += v * s
        if hs and tot:
            shares = collections.Counter()
            for h in hs:
                shares[group_of(h[0])] += (h[1] or 0) / tot
            for g, s in shares.items():
                eq_blocks[g].append([pb['key'], round(s * 100, 2), cn(pb['l12'][0] * s)])
    coverage = cov[0] / cov[1] if cov[1] else 0
    log(f'  andeler dekker {coverage * 100:.1f} % av oljen siste 12 måneder')

    latest = monthly[max(monthly)] if monthly else None
    snap = None
    if latest:
        snap = {'ym': f"{int(latest['ref_year'])}{int(latest['ref_month']):02d}",
                'operatorsOil': lit(latest.get('operators_oil')), 'operatorsGas': lit(latest.get('operators_gas')),
                'environment': lit(latest.get('environment')), 'wells': lit(latest.get('blocks')),
                'wellsTotal': [num(latest.get('wells_total')), num(latest.get('wells_offshore')), num(latest.get('wells_onshore'))],
                'concessions': [num(latest.get('concessions_total')), num(latest.get('concessions_offshore')), num(latest.get('concessions_onshore'))],
                'depthShare': [num(latest.get('shallow_share_pct')), num(latest.get('deep_share_pct')), num(latest.get('ultradeep_share_pct'))],
                'summary': latest.get('public_summary')}

    cols = ['oil', 'oilFc', 'share', 'gas', 'gasFc', 'gasInj', 'gasAlng', 'gasPower', 'flare', 'alng', 'eff', 'effFc', 'liftD', 'rigs', 'wellsWorked', 'drilledM']
    series = {c: [] for c in cols}
    for ym in ms:
        r = recs[ym]
        d = days_in(int(ym[:4]), int(ym[4:]))
        for c in cols:
            v = r.get(c)
            if c == 'share' and v is not None:
                v = v / d
            series[c].append(cn(v) if v is not None else None)
    fetched = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    return {
        'source': 'Agência Nacional de Petróleo, Gás e Biocombustíveis (ANPG), www.anpg.co.ao',
        'fetched': fetched,
        'months': [first, last],
        'units': {'oil': 'bbl/day (oil incl. condensate as ANPG reports it)', 'gas': 'MMscf/day', 'alng': 'boe/day', 'liftD': 'bbl/day',
                  'drilledM': 'metres', 'eff': '%', 'boe': f'{MCF_PER_BOE} Mcf per boe'},
        'series': series,
        'reports': [[ym, recs[ym]['date'], recs[ym]['slug']] for ym in ms],
        'annual': hist,
        'blockMonths': bms,
        'prodBlocks': prod_blocks,
        'equity': {'groups': {g: [cn(v) for v in vals] for g, vals in sorted(eq.items(), key=lambda kv: -sum(kv[1][-12:]))},
                   'blocks': {g: sorted(v, key=lambda r: -(r[2] or 0)) for g, v in eq_blocks.items()}, 'coverage': round(coverage, 3)},
        'snapshot': snap,
        'blocks': blocks,
        'seismic': seis,
        'gas': gas,
    }


# ---------------------------------------------------------------- kartlag (ArcGIS)

GIS = 'https://gis.anpg.co.ao/hosting/rest/services/Hosted/'
OUT_GEO = 'data/angola_geo.json'


def gis(layer, where='1=1', fields='*', offset_deg=None):
    """Alle objekter i et kartlag som GeoJSON-features (sider à 1000)."""
    out, off = [], 0
    while True:
        params = {'where': where, 'outFields': fields, 'outSR': 4326, 'f': 'geojson',
                  'resultOffset': off, 'resultRecordCount': 1000, 'returnGeometry': 'true'}
        if offset_deg:
            params['maxAllowableOffset'] = offset_deg
        for k in range(5):
            try:
                r = S.get(f'{GIS}{layer}/FeatureServer/0/query', params=params, timeout=180)
                r.raise_for_status()
                d = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                if k == 4:
                    raise DataError(f'{layer}: {e}')
                time.sleep(4 * 2 ** k)
        feats = d.get('features') or []
        out += feats
        off += len(feats)
        if len(feats) < 1000 and not d.get('exceededTransferLimit') and not (d.get('properties') or {}).get('exceededTransferLimit'):
            return out


def clean(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v if v and v not in ('<Null>', 'Null') else None
    return v


def rnd(c):
    """Koordinater med tre desimaler (~100 m); ringer og linjer uten gjentatte punkter."""
    if isinstance(c, (int, float)):
        return round(c, 3)
    if c and isinstance(c[0], (int, float)):
        return [round(c[0], 3), round(c[1], 3)]
    out = [rnd(x) for x in c]
    if out and isinstance(out[0], list) and out[0] and isinstance(out[0][0], (int, float)):
        ded = [out[0]] + [p for i, p in enumerate(out[1:], 1) if p != out[i - 1]]
        return ded
    return out


def rings_of(g):
    if not g:
        return []
    if g['type'] == 'Polygon':
        return [rnd(r) for r in g['coordinates']]
    if g['type'] == 'MultiPolygon':
        return [rnd(r) for poly in g['coordinates'] for r in poly]
    return []


def lines_of(g):
    if not g:
        return []
    if g['type'] == 'LineString':
        return [rnd(g['coordinates'])]
    if g['type'] == 'MultiLineString':
        return [rnd(x) for x in g['coordinates']]
    return []


def fluid_of(t):
    k = key(t)
    if 'CONDENSAD' in k:
        return 'cond'
    if 'OLEO' in k and 'GAS' in k:
        return 'oil+gas'
    if 'GAS' in k:
        return 'gas'
    if 'OLEO' in k:
        return 'oil'
    return None


def ms_year(v):
    try:
        return dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=int(v))
    except (TypeError, ValueError, OverflowError):
        return None


def build_geo():
    log('Kartlag fra gis.anpg.co.ao…')
    geo = {'source': 'ANPG, Interactive map of petroleum concessions (gis.anpg.co.ao)'}
    blocks = []
    for f in gis('AGO_Blocks_All', offset_deg=0.002):
        p = f.get('properties') or {}
        rings = rings_of(f.get('geometry'))
        if not rings:
            continue
        blocks.append({'name': clean(p.get('name')), 'block': clean(p.get('bloco')), 'lease': clean(p.get('lease_id')),
                       'cat': clean(p.get('categoria')), 'sub': clean(p.get('categoria_')), 'status': clean(p.get('status')),
                       'contract': clean(p.get('contracto')), 'op': clean(p.get('operador')), 'opShare': clean(p.get('operador__')),
                       'partners': clean(p.get('parceiro')), 'basin': clean(p.get('bacias')), 'area': clean(p.get('basin_area')),
                       'key': bkey(p.get('name') or p.get('bloco')), 'rings': rings})
    geo['blocks'] = blocks
    fields = []
    for f in gis('AGO_Fields_and_DAs_New', offset_deg=0.001):
        p = f.get('properties') or {}
        rings = rings_of(f.get('geometry'))
        if not rings:
            continue
        fields.append({'name': clean(p.get('nome_final')) or clean(p.get('nome')), 'block': clean(p.get('bloco')), 'cat': clean(p.get('categoria')),
                       'kind': clean(p.get('categoria_')), 'op': clean(p.get('operador')), 'contract': clean(p.get('contracto')),
                       'km2': clean(p.get('área_km2') or p.get('area_km2')), 'key': bkey(p.get('bloco')), 'rings': rings})
    geo['fields'] = fields
    disc = []
    for f in gis('AGO_Prospects_NEW', offset_deg=0.001):
        p = f.get('properties') or {}
        t = key(p.get('type'))
        if not (t.startswith('CAMPO') or t.startswith('DESCOBERTA')):
            continue
        rings = rings_of(f.get('geometry'))
        if not rings:
            continue
        disc.append({'name': clean(p.get('field_name')) or clean(p.get('name')), 'block': clean(p.get('bloco')) or clean(p.get('block')),
                     'kind': 'field' if t.startswith('CAMPO') else 'discovery', 'fluid': fluid_of(t), 'op': clean(p.get('opr_curr')),
                     'env': clean(p.get('ons_off')), 'key': bkey(p.get('bloco') or p.get('block')), 'rings': rings})
    geo['discoveries'] = disc
    wells = []
    for f in gis('AGO_Wells', fields='well_name,short_name,operator,current_st,field,block,spud_date,province'):
        g = f.get('geometry') or {}
        p = f.get('properties') or {}
        if g.get('type') != 'Point' or not g.get('coordinates'):
            continue
        lon, lat = g['coordinates'][:2]
        sp = ms_year(p.get('spud_date'))
        wells.append([clean(p.get('well_name')), round(lon, 4), round(lat, 4), clean(p.get('current_st')), clean(p.get('field')),
                      clean(p.get('block')), clean(p.get('operator')), sp.strftime('%Y-%m-%d') if sp else None])
    geo['wellCols'] = ['name', 'lon', 'lat', 'status', 'field', 'block', 'op', 'spud']
    geo['wells'] = wells
    fac = []
    keep = {'FPSO', 'FSO', 'PLATAFORMA DE PRODUCAO', 'PLATAFORMA DE PRODUCAO E PERFURACAO', 'PLATAFORMA FIXA', 'PROCESSING PLATAFORM',
            'GAS PROCESSING PLATFORM', 'GATHERING STATION PLATFORM', 'COMPRESSION PLATFORM', 'GAS INJECTION PLATFORM', 'WATER INJECTION PLATFORM',
            'TERMINAL', 'REFINARIA', 'MONOBOIA', 'PLATAFORMA CABECA DE POCO', 'SUB-SEA TEMPLATE', 'MANIFOLD'}
    for f in gis('AGO_FPSO_FSO'):
        g = f.get('geometry') or {}
        p = f.get('properties') or {}
        t = key(p.get('tipo_de_in'))
        if g.get('type') != 'Point' or t not in keep:
            continue
        lon, lat = g['coordinates'][:2]
        fac.append([clean(p.get('nome')), t, round(lon, 4), round(lat, 4), clean(p.get('bloco')), clean(p.get('operador')),
                    clean(p.get('nome_do_ca')) or clean(p.get('field_name')), clean(p.get('operaciona'))])
    geo['facCols'] = ['name', 'type', 'lon', 'lat', 'block', 'op', 'field', 'operating']
    geo['facilities'] = fac
    pipes = []
    for f in gis('AGO_Pipelines', where='length_pip>=5000', fields='assetname,contents,operatings,length_pip,dateinstal', offset_deg=0.002):
        p = f.get('properties') or {}
        for ln in lines_of(f.get('geometry')):
            if len(ln) >= 2:
                yr = ms_year(p.get('dateinstal'))
                pipes.append([clean(p.get('assetname')), clean(p.get('contents')), clean(p.get('operatings')), p.get('length_pip'),
                              yr.year if yr and yr.year > 1950 else None, ln])
    geo['pipeCols'] = ['name', 'contents', 'status', 'length', 'installed', 'path']
    geo['pipelines'] = pipes
    log(f'  {len(blocks)} blokkflater, {len(fields)} felt/utbyggingsområder, {len(disc)} felt og funn, {len(wells)} brønner, '
        f'{len(fac)} innretninger, {len(pipes)} rørledninger')
    if len(blocks) < 50 or len(wells) < 1000:
        raise DataError('For få blokker eller brønner i kartlagene')
    return geo


def write(path, payload):
    try:
        old = json.load(open(path, encoding='utf-8'))
        if {**old, 'fetched': None} == {**json.loads(json.dumps(payload)), 'fetched': None}:
            payload['fetched'] = old['fetched']
    except (OSError, ValueError):
        pass
    os.makedirs('data', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(',', ':'))
        fh.write('\n')
    log(f'Wrote {path} ({os.path.getsize(path) / 1024:.0f} KB)')


def main():
    t0 = time.time()
    try:
        payload = build()
    except DataError as e:
        log(f'FEIL: {e}')
        return 1
    write(OUT, payload)
    # Kartlagene er en egen kilde: feiler de, beholdes forrige kartfil
    try:
        geo = build_geo()
        geo['fetched'] = payload['fetched']
        write(OUT_GEO, geo)
    except DataError as e:
        log(f'  ! kartlagene: {e} — beholder {OUT_GEO}')
    log(f'Tid: {time.time() - t0:.0f} s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
