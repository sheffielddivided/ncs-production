"""
Henter nøkkeltall for petroleumsvirksomheten fra SSB og skriver dem til
data/ssb_petroleum.json, til Economy-fanen i NCS-modusen.

Tre tabeller fra SSBs åpne PxWeb-API (ingen nøkkel, JSON-stat2):

- 07840  Oljevirksomhet, hovedtall per næring (utvinning 06, tjenester 09.1,
         rørtransport 49.500): sysselsatte, lønnskostnader, produksjonsverdi,
         bearbeidingsverdi m.m., årlig fra 1965. Mill. kr, løpende priser.
- 04170  Påløpte investeringer i utvinning og rørtransport etter
         investeringsart, årlig fra 1985. Mill. kr, løpende priser.
- 08050  Investeringsundersøkelsen: antatte investeringer for et år slik de
         ble anslått fra februar året før og fram til påløpt. Gir prognosene
         for inneværende og neste år.

SSB oppdaterer disse noen få ganger i året. Skriptet kjøres likevel daglig
sammen med Sodir-hentingen, og fila skrives bare når innholdet er endret.
Feiler SSB, beholdes forrige fil; steget i arbeidsflyten er
continue-on-error, så Sodir-dataene committes uansett.

Kilde: Statistisk sentralbyrå (SSB), CC BY 4.0.
"""

import json
import os
import sys
import time

import requests

API = 'https://data.ssb.no/api/v0/no/table/'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ssb_petroleum.json')
RETRIES = 4

# Investeringsartene som summerer til «I alt» i 04170/08050. Leting heter
# '11' i 04170 og '111' (leting og konseptstudier) i 08050.
INVEST_TYPES = ['11', '121', '122', '123', '124', '2']
SURVEY_TYPES = {'111': '11', '121': '121', '122': '122', '123': '123', '124': '124', '2': '2', '0': '0'}


def post(table, query):
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.post(API + table, json={'query': query, 'response': {'format': 'json-stat2'}}, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f'SSB {table}: {last}')


def cube(d):
    """JSON-stat2 til (dimensjonskoder per dimensjon, oppslag (koder...) -> verdi).
    Manglende verdier (status '..') blir None."""
    dims = d['id']
    codes = [list(d['dimension'][k]['category']['index'].keys()) for k in dims]
    vals = d['value']
    out = {}
    stride = [1] * len(dims)
    for i in range(len(dims) - 2, -1, -1):
        stride[i] = stride[i + 1] * len(codes[i + 1])
    for n, v in enumerate(vals):
        key = tuple(codes[i][(n // stride[i]) % len(codes[i])] for i in range(len(dims)))
        out[key] = v
    return dims, codes, out, d.get('updated', '')


def main_figures():
    d = post('07840', [{'code': 'NACE2007', 'selection': {'filter': 'item', 'values': ['06', '09.1', '49.500']}}])
    dims, codes, val, updated = cube(d)
    nace, content, years = codes
    series = {n: {c: [val.get((n, c, y)) for y in years] for c in content} for n in nace}
    return {'table': '07840', 'updated': updated, 'years': [int(y) for y in years], 'series': series}


def investments():
    # Uten eksplisitt utvalg summerer SSB bort investeringsarten.
    d = post('04170', [{'code': 'InvestOljeGass', 'selection': {'filter': 'all', 'values': ['*']}}])
    dims, codes, val, updated = cube(d)
    kinds, _, years = codes
    c = codes[1][0]
    values = {k: [val.get((k, c, y)) for y in years] for k in INVEST_TYPES + ['0']}
    return {'table': '04170', 'updated': updated, 'years': [int(y) for y in years], 'values': values}


def survey():
    d = post('08050', [{'code': 'Investeringsomrade', 'selection': {'filter': 'all', 'values': ['*']}},
                       {'code': 'Anslagstidspunkt', 'selection': {'filter': 'all', 'values': ['*']}}])
    dims, codes, val, updated = cube(d)
    areas, points, _, years = codes
    c = codes[2][0]
    out = {}
    for a, k in SURVEY_TYPES.items():
        if a not in areas:
            continue
        out[k] = {p: [val.get((a, p, c, y)) for y in years] for p in points}
    return {'table': '08050', 'updated': updated, 'years': [int(y) for y in years],
            'points': points, 'values': out}


def main():
    try:
        payload = {
            'source': 'Statistics Norway (SSB), tables 07840, 04170 and 08050 - CC BY 4.0',
            'main': main_figures(),
            'invest': investments(),
            'survey': survey(),
        }
    except Exception as exc:  # noqa: BLE001
        print(f'FEIL under henting fra SSB: {exc} - beholder forrige fil', file=sys.stderr)
        return 1
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as fh:
            if fh.read() == text:
                print('  = ssb_petroleum.json uendret')
                return 0
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f'  + ssb_petroleum.json skrevet ({len(text.encode()) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
