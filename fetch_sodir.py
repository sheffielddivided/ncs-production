"""
Henter alle datasett fra Sodir (Norwegian Offshore Directorate) FactMaps og
lagrer dem som kompakte JSON-filer under data/.

Kjøres automatisk daglig av .github/workflows/update-sodir-data.yml,
men kan også kjøres lokalt:

    pip install requests
    python fetch_sodir.py

Utdata er deterministisk (sorterte rader og nøkler) slik at filene kun
endrer seg når selve dataene har endret seg. Det gjør at workflowen ikke
lager en ny commit hver dag – kun når Sodir faktisk har publisert nye tall.
data/meta.json oppdateres derfor også bare når noe annet har endret seg.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

BASE = 'https://factmaps.sodir.no/api/rest/services/DataService/Data/FeatureServer'

FIELD_LAYER = 7100
PROD_LAYER  = 7300
LIC_LAYER   = 7108
RES_LAYER   = 7114

OUT_DIR   = 'data'
PAGE_SIZE = 2000
TIMEOUT   = 120
RETRIES   = 4

SOURCE = 'Norwegian Offshore Directorate (Sodir) FactPages - https://factpages.sodir.no - NLOD 2.0'


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _get(url, params):
    """GET med retry og eksponentiell backoff. Sodir svarer av og til tregt."""
    last = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if 'error' in data:
                raise RuntimeError(data['error'].get('message', 'ukjent API-feil'))
            return data
        except Exception as exc:  # noqa: BLE001 - vi vil retry-e på alt
            last = exc
            if attempt < RETRIES - 1:
                wait = 2 ** (attempt + 1)
                print(f"    ! {exc} - prover igjen om {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"Ga opp etter {RETRIES} forsok: {last}")


def oid_field(layer):
    """Hent navnet pa objectId-feltet, slik at paginering blir deterministisk.

    Uten en stabil sortering kan offset-basert paginering hoppe over eller
    duplisere rader nar serveren returnerer radene i vilkarlig rekkefolge.
    """
    try:
        meta = _get(f'{BASE}/{layer}', {'f': 'json'})
        return meta.get('objectIdField') or None
    except Exception as exc:  # noqa: BLE001
        print(f"    ! klarte ikke lese objectIdField ({exc}) - fortsetter usortert")
        return None


def count_rows(layer, where='1=1'):
    data = _get(f'{BASE}/{layer}/query', {
        'f': 'json', 'where': where, 'returnCountOnly': 'true',
    })
    return data.get('count', 0)


def fetch_layer(layer, out_fields, where='1=1'):
    """Hent alle rader fra et lag med paginering."""
    total = count_rows(layer, where)
    order = oid_field(layer)
    print(f"  Lag {layer}: {total} rader")

    rows, offset = [], 0
    while offset < total:
        params = {
            'f': 'json',
            'where': where,
            'outFields': out_fields,
            'returnGeometry': 'false',
            'resultOffset': offset,
            'resultRecordCount': PAGE_SIZE,
        }
        if order:
            params['orderByFields'] = f'{order} ASC'

        feats = _get(f'{BASE}/{layer}/query', params).get('features', [])
        if not feats:
            break
        rows.extend(f.get('attributes', {}) for f in feats)
        offset += len(feats)
        print(f"    {offset}/{total}")

    return rows


# ── HJELPERE ─────────────────────────────────────────────────────────────────
def num(value):
    """Rund av til 6 desimaler. None og tomt blir 0."""
    if value is None:
        return 0
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0


def as_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── DATASETT ─────────────────────────────────────────────────────────────────
def build_fields():
    rows = fetch_layer(FIELD_LAYER, 'fldNpdidField,fldName')
    out = []
    for r in rows:
        fid, name = as_int(r.get('fldNpdidField')), (r.get('fldName') or '').strip()
        if fid is None or not name:
            continue
        out.append([fid, name])
    out.sort(key=lambda x: x[1])
    return {'columns': ['id', 'name'], 'rows': out}


def build_production():
    rows = fetch_layer(
        PROD_LAYER,
        'prfNpdidInformationCarrier,prfYear,prfMonth,prfPrdOilNetMillSm3,'
        'prfPrdGasNetBillSm3,prfPrdOeNetMillSm3,prfPrdProducedWaterInFieldMillS',
    )
    fields = {}
    for r in rows:
        fid  = as_int(r.get('prfNpdidInformationCarrier'))
        year = as_int(r.get('prfYear'))
        if fid is None or year is None:
            continue
        fields.setdefault(str(fid), []).append([
            year,
            as_int(r.get('prfMonth')) or 0,
            num(r.get('prfPrdOilNetMillSm3')),
            num(r.get('prfPrdGasNetBillSm3')),
            num(r.get('prfPrdOeNetMillSm3')),
            num(r.get('prfPrdProducedWaterInFieldMillS')),
        ])
    for series in fields.values():
        series.sort(key=lambda x: (x[0], x[1]))
    return {
        'columns': ['year', 'month', 'oil', 'gas', 'oe', 'water'],
        'fields': fields,
    }


def build_licensees():
    rows = fetch_layer(
        LIC_LAYER,
        'fldNpdidField,cmpLongName,fldCompanyShare,fldLicenseeFrom,fldLicenseeTo',
    )
    out = []
    for r in rows:
        fid, cmp_name = as_int(r.get('fldNpdidField')), (r.get('cmpLongName') or '').strip()
        if fid is None or not cmp_name:
            continue
        out.append([
            fid,
            cmp_name,
            num(r.get('fldCompanyShare')),
            as_int(r.get('fldLicenseeFrom')),
            as_int(r.get('fldLicenseeTo')),
        ])
    # Sorter deterministisk. None sist, siden None ikke kan sammenlignes med int.
    out.sort(key=lambda x: (x[0], x[1], x[3] is None, x[3] or 0))
    return {'columns': ['fieldId', 'company', 'share', 'from', 'to'], 'rows': out}


def build_reserves():
    rows = fetch_layer(RES_LAYER, 'cmpLongName,cmpRecoverableOE')
    totals = {}
    for r in rows:
        cmp_name = (r.get('cmpLongName') or '').strip()
        if not cmp_name:
            continue
        totals[cmp_name] = round(totals.get(cmp_name, 0) + num(r.get('cmpRecoverableOE')), 6)
    return {'companies': dict(sorted(totals.items()))}


# ── SKRIVING ─────────────────────────────────────────────────────────────────
def write_if_changed(name, payload):
    """Skriv fila kun hvis innholdet faktisk er endret. Returnerer True ved endring."""
    path = os.path.join(OUT_DIR, name)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'

    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            if fh.read() == text:
                print(f"  = {name} uendret")
                return False

    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    size_kb = len(text.encode('utf-8')) / 1024
    print(f"  + {name} skrevet ({size_kb:.0f} KB)")
    return True


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    datasets = [
        ('fields.json',     'felt',        build_fields),
        ('production.json', 'produksjon',  build_production),
        ('licensees.json',  'lisensiaerer', build_licensees),
        ('reserves.json',   'reserver',    build_reserves),
    ]

    changed = False
    for filename, label, builder in datasets:
        print(f"\nHenter {label}...")
        try:
            payload = builder()
        except Exception as exc:  # noqa: BLE001
            # Avbryt uten a skrive noe. Da beholder repoet forrige gyldige
            # datasett, og appen fortsetter a fungere pa gamle tall.
            print(f"FEIL under henting av {label}: {exc}", file=sys.stderr)
            return 1
        if write_if_changed(filename, payload):
            changed = True

    if changed:
        write_if_changed('meta.json', {
            'generated': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'source': SOURCE,
        })
        print("\nFerdig - data er oppdatert.")
    else:
        print("\nFerdig - ingen endringer siden forrige kjoring.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
