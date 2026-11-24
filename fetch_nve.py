"""
Henter magasinfylling og strømpriser fra nve-løsningen (sheffielddivided/nve)
og lagrer dem kompakt i data/power.json til Macro → NO power.

nve-repoet er og forblir kilden: det henter magasinstatistikken fra NVEs
åpne API (ukentlig, onsdag kl. 13) og day-ahead-priser fra ENTSO-E (daglig),
og publiserer resultatet som JSON under docs/. Dette skriptet leser bare de
publiserte filene og gjør dem om — det henter ingenting fra NVE eller ENTSO-E
selv.

    data/power.json
      reservoir.areas   NO (hele landet) og NO1–NO5 (elspotområdene):
                        [år, ISO-uke, fyllingsgrad %, fylling TWh]
                        (kapasiteten er fylling / fyllingsgrad)
      reservoir.next    neste publiseringsdato fra NVE
      prices.areas      NO1–NO5 og DE-LU: [år, ISO-uke, EUR/MWh, timer med pris]
                        (siste uke kan være ufullstendig: timer < 168)
      flows.net         netto eksport per utenlandsforbindelse, «NO2>DE-LU»:
                        [år, ISO-uke, MW (ut minus inn, ukesnitt), timer]

Kjøres daglig av .github/workflows/update-sodir-data.yml, men kan også kjøres
lokalt, mot GitHub eller mot en lokal kopi av nve-repoet:

    pip install requests
    python fetch_nve.py
    python fetch_nve.py --local ../nve

Formatet sjekkes: manglende nøkler, for få rader eller uventede verdier gir en
tydelig feil og exit 1 uten at noe skrives. Fila skrives bare når innholdet
faktisk er endret.
"""

import argparse
import json
import os
import sys
import time

NVE_REPO = 'sheffielddivided/nve'
NVE_RAW = f'https://raw.githubusercontent.com/{NVE_REPO}/main/'
FILES = {'reservoir': 'docs/data.json', 'prices': 'docs/prices.json', 'flows': 'docs/flows.json'}
OUT = os.path.join('data', 'power.json')

# Magasinfyllingen tas med for inneværende år og de 20 foregående. Appen
# sammenligner med de siste 20 hele årene, som nve-løsningen.
FIRST_YEAR = time.gmtime().tm_year - 20
AREAS = {('NO', 0): 'NO', ('EL', 1): 'NO1', ('EL', 2): 'NO2', ('EL', 3): 'NO3', ('EL', 4): 'NO4', ('EL', 5): 'NO5'}
PRICE_AREAS = ['NO1', 'NO2', 'NO3', 'NO4', 'NO5']
PRICE_REF = 'DE-LU'


def load(key, local):
    path = FILES[key]
    if local:
        with open(os.path.join(local, path), encoding='utf-8') as fh:
            return json.load(fh)
    import requests
    last = None
    for attempt in range(4):
        try:
            r = requests.get(NVE_RAW + path, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f'kunne ikke hente {path}: {last}')


def build_reservoir(rows):
    if not isinstance(rows, list) or len(rows) < 1000:
        raise ValueError('magasindata: forventet en liste med minst 1000 rader')
    out = {v: [] for v in AREAS.values()}
    nxt = None
    for r in rows:
        key = AREAS.get((r.get('omrType'), r.get('omrnr')))
        if not key:
            continue
        y, w, f = r.get('iso_aar'), r.get('iso_uke'), r.get('fyllingsgrad')
        if not isinstance(y, int) or not isinstance(w, int) or not 1 <= w <= 53:
            raise ValueError(f'magasindata: ugyldig år/uke i {r}')
        if y < FIRST_YEAR or f is None:
            continue
        if not 0 <= f <= 1.05:
            raise ValueError(f'magasindata: fyllingsgrad utenfor 0–1 i {r}')
        out[key].append([y, w, round(f * 100, 2), round(r.get('fylling_TWh') or 0, 3)])
        p = r.get('neste_Publiseringsdato') or ''
        if p[:4] > '2000' and (nxt is None or p > nxt):
            nxt = p
    for k, v in out.items():
        if len(v) < 400:
            raise ValueError(f'magasindata: for få rader for {k} ({len(v)})')
        v.sort()
    return {'areas': out, 'next': nxt[:10] if nxt else None}


def build_prices(d):
    if not isinstance(d, dict) or d.get('enhet') != 'EUR/MWh':
        raise ValueError('prisdata: ukjent format eller enhet')
    src = dict(d.get('omrader') or {})
    src.update({k: v for k, v in (d.get('referanse') or {}).items() if k == PRICE_REF})
    out = {}
    for k in PRICE_AREAS + [PRICE_REF]:
        rows = src.get(k)
        if not rows or len(rows) < 200:
            raise ValueError(f'prisdata: mangler eller for få uker for {k}')
        out[k] = sorted([r['iso_aar'], r['iso_uke'], round(r['pris'], 2), round(r.get('timer') or 0)]
                        for r in rows if r.get('pris') is not None)
    return {'areas': out, 'unit': 'EUR/MWh', 'source': d.get('kilde')}


# Fysisk flyt (ENTSO-E A11) per retning blir netto eksport per forbindelse:
# ut av Norge minus inn, i MW som ukesnitt. Mangler den ene retningen en uke,
# regnes den som 0 (de nye kablene har bare data fra de startet).
def build_flows(d):
    if not isinstance(d, dict) or 'MW' not in (d.get('enhet') or ''):
        raise ValueError('flytdata: ukjent format eller enhet')
    conns = d.get('forbindelser') or []
    ser = d.get('serier') or {}
    if len(conns) < 5:
        raise ValueError('flytdata: for få forbindelser')
    out = {}
    for c in conns:
        no, ab = c['norsk'], c['utland']
        ut = {(r['iso_aar'], r['iso_uke']): r for r in ser.get(f'{no}>{ab}', [])}
        inn = {(r['iso_aar'], r['iso_uke']): r for r in ser.get(f'{ab}>{no}', [])}
        rows = []
        for k in sorted(set(ut) | set(inn)):
            a, b = ut.get(k), inn.get(k)
            h = min(x['timer'] for x in (a, b) if x)
            rows.append([k[0], k[1], round((a['mw'] if a else 0) - (b['mw'] if b else 0)), round(h, 1)])
        if rows:
            out[f'{no}>{ab}'] = rows
    if len(out) < 5:
        raise ValueError('flytdata: for få forbindelser med data')
    return {'net': out, 'unit': 'MW', 'source': d.get('kilde')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--local', help='les fra en lokal kopi av nve-repoet')
    args = ap.parse_args()
    try:
        payload = {
            'reservoir': build_reservoir(load('reservoir', args.local)),
            'prices': build_prices(load('prices', args.local)),
            'flows': build_flows(load('flows', args.local)),
            'source': f'https://github.com/{NVE_REPO}',
        }
    except Exception as exc:  # noqa: BLE001
        print(f'FEIL: {exc}', file=sys.stderr)
        return 1
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as fh:
            if fh.read() == text:
                print('= power.json uendret')
                return 0
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f'+ power.json skrevet ({len(text.encode()) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
