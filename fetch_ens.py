"""
Henter de danske produksjons-, reserve- og eierdataene fra ENS-løsningen
(sheffielddivided/ens) og lagrer dem i NCS-appens format under data/:

    data/dcs.json           felt, eiere, årlig og månedlig produksjon, reserver
    data/dcs_outlines.json  feltomriss til DCS → Map

ENS-repoet er og forblir kilden for de danske tallene: det henter
Energistyrelsens (ENS) årsfil og månedsrapporter, og publiserer resultatet som
JSON. Dette skriptet leser bare de publiserte filene og gjør dem om — det
henter ingenting fra ens.dk selv.

Kjøres daglig av .github/workflows/update-sodir-data.yml, men kan også kjøres
lokalt, enten mot GitHub eller mot en lokal kopi av ens-repoet:

    pip install requests
    python fetch_ens.py
    python fetch_ens.py --local ../ens

Enheter. ENS oppgir olje og vann i 1000 m³ og gass i mill. Nm³ (normal-
kubikkmeter, 0 °C). Appen ellers regner i Sm³ (15 °C) og fat o.e. med
1000 Sm³ gass = 1 Sm³ o.e. Gassen regnes derfor om fra Nm³ til Sm³
(× 288,15 / 273,15 ≈ 1,0549, ideell gass ved samme trykk), slik at danske og
norske fat o.e. er regnet likt. Olje og vann tas som de er. Merk at ENS'
egne visninger regner 1000 Nm³ = 1 m³ o.e., så gass i fat o.e. her ligger
om lag 5,5 % over ENS' egne tall.

Formatet sjekkes strengt: ukjent schema_version, manglende nøkler eller
uventede verdier gir en tydelig feil og exit 1 uten at noe skrives. Utdata er
deterministisk (sorterte nøkler og rader), og filene skrives bare når
innholdet faktisk er endret.
"""

import argparse
import json
import os
import sys
import time

ENS_REPO = 'sheffielddivided/ens'
ENS_RAW = f'https://raw.githubusercontent.com/{ENS_REPO}/main/'
ENS_PAGES = 'https://sheffielddivided.github.io/ens/'
FILES = {
    'combined':  'data/combined.json',
    'fields':    'data/fields.json',
    'ownership': 'data/ownership.json',
    'reserves':  'data/reserves.json',
    'outlines':  'docs/data/gis/fields.geojson',
}
SCHEMA_VERSION = 1
EXPECTED_UNITS = {'oil': '1000 m3', 'gas': 'mio. Nm3', 'water': '1000 m3'}
EXPECTED_RES_UNITS = {'oil_mill_m3': 'mill. m³', 'gas_bn_nm3': 'mrd. Nm³'}
NM3_TO_SM3 = 288.15 / 273.15
OUTLINE_DECIMALS = 4

OUT_DIR = 'data'
TIMEOUT = 60
RETRIES = 4
USER_AGENT = 'ncs-production/fetch_ens (+https://github.com/sheffielddivided/ncs-production)'


class SourceFormatError(Exception):
    """ENS-filene har et format dette skriptet ikke kjenner."""


def need(cond, msg):
    if not cond:
        raise SourceFormatError(msg)


# ── HENTING ──────────────────────────────────────────────────────────────────
def load_remote(path):
    import requests   # bare nødvendig mot nettet
    url = ENS_RAW + path
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={'User-Agent': USER_AGENT})
            r.raise_for_status()
            return r.json()
        except Exception as e:   # noqa: BLE001 – alle feil prøves på nytt
            if attempt == RETRIES - 1:
                raise RuntimeError(f'Kunne ikke hente {url}: {e}') from e
            wait = 2 ** (attempt + 1)
            print(f'  ! {path}: {e} – prøver igjen om {wait} s')
            time.sleep(wait)
        finally:
            time.sleep(1)   # høflig: maks én forespørsel i sekundet


def load_local(root, path):
    with open(os.path.join(root, path), encoding='utf-8') as fh:
        return json.load(fh)


# ── OMFORMING ────────────────────────────────────────────────────────────────
def r6(x):
    return None if x is None else round(x, 6)


def check_schema(name, doc):
    need(isinstance(doc, dict), f'{name}: ikke et JSON-objekt')
    need(doc.get('schema_version') == SCHEMA_VERSION,
         f'{name}: schema_version {doc.get("schema_version")!r}, forventet {SCHEMA_VERSION}')


def build_production(combined, slugs):
    """Årlig og månedlig produksjon per felt.

    Rader: [periode, olje mill Sm³, gass mrd Sm³, vann mill m³, foreløpig 0/1].
    Perioden er 'YYYY' (årlig) eller 'YYYY-MM' (månedlig).
    """
    check_schema('combined.json', combined)
    need(combined.get('unit_definitions') == EXPECTED_UNITS,
         f'combined.json: uventede enheter {combined.get("unit_definitions")!r}')
    series = combined.get('series')
    need(isinstance(series, dict) and series, 'combined.json: mangler series')
    out = {'yearly': {}, 'monthly': {}}
    for slug, s in series.items():
        if slug == '_total':   # appen summerer feltene selv
            continue
        need(slug in slugs, f'combined.json: feltet {slug!r} finnes ikke i fields.json')
        for res, plen in (('yearly', 4), ('monthly', 7)):
            rows = {}
            for m in ('oil', 'gas', 'water'):
                pts = s.get(res, {}).get(m)
                need(isinstance(pts, list), f'combined.json: {slug}/{res}/{m} mangler')
                for p in pts:
                    t, v = p.get('t'), p.get('v')
                    need(isinstance(t, str) and len(t) == plen, f'combined.json: {slug}/{res}: ugyldig periode {t!r}')
                    need(v is None or isinstance(v, (int, float)), f'combined.json: {slug}/{res}/{t}: ugyldig verdi {v!r}')
                    row = rows.setdefault(t, {'p': False})
                    row[m] = v
                    row['p'] = row['p'] or bool(p.get('p'))
            out[res][slug] = [
                [t,
                 r6((r.get('oil') or 0) / 1000),
                 r6((r.get('gas') or 0) / 1000 * NM3_TO_SM3),
                 r6((r.get('water') or 0) / 1000),
                 1 if r['p'] else 0]
                for t, r in sorted(rows.items())
            ]
    return out


def build_reserves(reserves, slugs):
    """Gjenværende reserver per felt per 1. januar.

    Rader: [år, olje mill Sm³, gass mrd Sm³]. null der ENS ikke har tall.
    """
    check_schema('reserves.json', reserves)
    units = reserves.get('units') or {}
    for k, v in EXPECTED_RES_UNITS.items():
        need(units.get(k) == v, f'reserves.json: enheten for {k} er {units.get(k)!r}, forventet {v!r}')
    years = reserves.get('years')
    need(isinstance(years, list) and years, 'reserves.json: mangler years')
    fields = {}
    for slug, f in (reserves.get('fields') or {}).items():
        need(slug in slugs, f'reserves.json: feltet {slug!r} finnes ikke i fields.json')
        oil, gas = f.get('oil_mill_m3') or {}, f.get('gas_bn_nm3') or {}
        fields[slug] = [[int(y),
                         r6(oil.get(y)),
                         None if gas.get(y) is None else r6(gas[y] * NM3_TO_SM3)] for y in years]
    need(fields, 'reserves.json: ingen felt')
    tot = reserves.get('reported_total') or {}
    t_oil, t_gas = tot.get('oil_mill_m3') or {}, tot.get('gas_bn_nm3') or {}
    total = [[int(y), r6(t_oil.get(y)),
              None if t_gas.get(y) is None else r6(t_gas[y] * NM3_TO_SM3)] for y in years]
    flags = {}
    for slug, f in (reserves.get('fields') or {}).items():
        for m, by_year in (f.get('flags') or {}).items():
            for y, flag in by_year.items():
                need(flag in ('reconstructed', 'unresolved'), f'reserves.json: ukjent flagg {flag!r}')
                flags.setdefault(slug, []).append([int(y), m, flag])
    for v in flags.values():
        v.sort()
    return {'columns': ['year', 'oil', 'gas'], 'fields': fields, 'reportedTotal': total,
            'flags': flags, 'includedIn': reserves.get('included_in') or {}}


def build_fields(fields_doc, ownership):
    check_schema('fields.json', fields_doc)
    check_schema('ownership.json', ownership)
    fields = fields_doc.get('fields')
    need(isinstance(fields, dict) and fields, 'fields.json: mangler fields')
    owners = ownership.get('fields') or {}
    out = []
    for slug, f in fields.items():
        need(f.get('slug') == slug and f.get('display_name'), f'fields.json: ugyldig felt {slug!r}')
        own = owners.get(slug) or {}
        total = sum(own.values())
        need(not own or abs(total - 1) < 0.002, f'ownership.json: andelene i {slug!r} summerer til {total}')
        out.append({
            'slug': slug,
            'name': f['display_name'],
            'operator': f.get('operator'),
            'firstYear': f.get('first_year'),
            'lastYear': f.get('last_year'),
            'owners': sorted(([c, r6(s * 100)] for c, s in own.items()), key=lambda x: (-x[1], x[0])),
        })
    for slug in owners:
        need(slug in fields, f'ownership.json: feltet {slug!r} finnes ikke i fields.json')
    out.sort(key=lambda f: f['name'])
    return out


def build_outlines(geo, slugs):
    need(isinstance(geo, dict) and geo.get('type') == 'FeatureCollection', 'fields.geojson: ikke en FeatureCollection')
    out = {}
    for feat in geo.get('features') or []:
        props, geom = feat.get('properties') or {}, feat.get('geometry') or {}
        slug = props.get('slug')
        need(isinstance(slug, str) and slug, 'fields.geojson: feature uten slug')
        kind, coords = geom.get('type'), geom.get('coordinates')
        need(kind in ('Polygon', 'MultiPolygon'), f'fields.geojson: {slug}: geometritype {kind!r}')
        polys = [coords] if kind == 'Polygon' else coords
        item = out.setdefault(slug, {'name': props.get('Label') or props.get('name') or slug, 'rings': []})
        if props.get('Label'):
            item['name'] = props['Label']
        for poly in polys:
            item['rings'].append([[round(x, OUTLINE_DECIMALS), round(y, OUTLINE_DECIMALS)] for x, y, *_ in poly[0]])
    # Felt med produksjon skal hete det samme i kartet som i grafene.
    for slug, item in out.items():
        if slug in slugs:
            item['name'] = slugs[slug]
    return {'fields': out}


# ── SKRIVING ─────────────────────────────────────────────────────────────────
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
    os.replace(tmp, path)   # atomisk: aldri en halvskrevet fil
    print(f'  + {name} skrevet ({len(text.encode("utf-8")) / 1024:.0f} KB)')
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--local', metavar='DIR', help='les fra en lokal kopi av ens-repoet i stedet for GitHub')
    args = ap.parse_args(argv)

    load = (lambda p: load_local(args.local, p)) if args.local else load_remote
    try:
        docs = {k: load(p) for k, p in FILES.items()}
        fields = build_fields(docs['fields'], docs['ownership'])
        slugs = {f['slug']: f['name'] for f in fields}
        prod = build_production(docs['combined'], slugs)
        dcs = {
            'source': {
                'name': 'Danish Energy Agency (Energistyrelsen, ENS)',
                'url': 'https://ens.dk/en/energy-sources/monthly-and-yearly-production',
                'reservesUrl': docs['reserves'].get('source'),
                'ownershipUrl': docs['ownership'].get('source'),
                'via': f'https://github.com/{ENS_REPO}',
                'pages': ENS_PAGES,
            },
            'updated': docs['combined'].get('last_updated'),
            'ownershipUpdated': docs['ownership'].get('generated_at'),
            'units': {'oil': 'mill Sm3', 'gas': 'bill Sm3 (converted from Nm3)', 'water': 'mill m3',
                      'nm3ToSm3': r6(NM3_TO_SM3)},
            'fields': fields,
            'production': {'columns': ['period', 'oil', 'gas', 'water', 'prelim'], **prod},
            'reserves': build_reserves(docs['reserves'], slugs),
        }
        outlines = build_outlines(docs['outlines'], slugs)
    except (SourceFormatError, RuntimeError, OSError, ValueError) as e:
        print(f'FEIL: {e}', file=sys.stderr)
        print('Ingenting er skrevet; forrige data/dcs*.json beholdes.', file=sys.stderr)
        return 1

    print('ENS (Danmark):')
    write_if_changed('dcs.json', dcs)
    write_if_changed('dcs_outlines.json', outlines)
    return 0


if __name__ == '__main__':
    sys.exit(main())
