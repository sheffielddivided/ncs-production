"""
Henter daglig Brent spotpris (EIA-serie RBRTE, «Europe Brent Spot Price FOB»)
og skriver den til data/brent_eia.json fra 1990. Brukes til å skjøte Datert
Brent-serien i Prices i begge ender: framover, fordi Petroleumsprisrådets tall
kommer med flere måneders forsinkelse, og bakover, fordi de starter i 2011.
1990 er valgt fordi KPI-seriene (realpriser) starter der; EIA har tall fra 1987.

Kilde: U.S. Energy Information Administration (EIA), offentlig data. Krever
API-nøkkel i miljøvariabelen EIA_API_KEY (GitHub-hemmelighet med samme navn).
Mangler nøkkelen, eller svarer ikke EIA, hentes den samme serien fra FRED
(DCOILBRENTEU, som er EIAs tall publisert av St. Louis Fed), uten nøkkel.

Fila har rå dagspriser. Femdagerssnittet som gjør serien sammenlignbar med
Petroleumsprisrådets Datert Brent, regnes ut i appen (se priceSplice i
index.html).

Feiler begge kildene, beholdes forrige fil; steget i arbeidsflyten er
continue-on-error.
"""

import csv
import io
import json
import os
import sys
import time

import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'brent_eia.json')
START = '1990-01-01'   # skjøtes bakover før Petroleumsprisrådets første dato (2011), og framover
RETRIES = 4


def get(url, params=None):
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(str(last))


def from_eia(key):
    rows, offset = [], 0
    while True:
        data = get('https://api.eia.gov/v2/petroleum/pri/spt/data/', {
            'api_key': key, 'frequency': 'daily', 'data[0]': 'value',
            'facets[series][]': 'RBRTE', 'start': START,
            'sort[0][column]': 'period', 'sort[0][direction]': 'asc',
            'offset': offset, 'length': 5000,
        }).json()
        page = (data.get('response') or {}).get('data') or []
        rows += [[p['period'], round(float(p['value']), 3)] for p in page if p.get('value') not in (None, '')]
        if len(page) < 5000:
            break
        offset += 5000
    return rows


def from_fred():
    text = get('https://fred.stlouisfed.org/graph/fredgraph.csv', {'id': 'DCOILBRENTEU', 'cosd': START}).text
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        d, v = r.get('observation_date') or r.get('DATE'), r.get('DCOILBRENTEU')
        if d and v not in (None, '', '.') and d >= START:
            rows.append([d, round(float(v), 3)])
    return rows


def main():
    key = os.environ.get('EIA_API_KEY', '').strip()
    source, rows = None, []
    if key:
        try:
            rows, source = from_eia(key), 'EIA'
        except Exception as exc:  # noqa: BLE001
            print(f'  ! EIA feilet ({exc}) - prøver FRED', file=sys.stderr)
    if not rows:
        try:
            rows, source = from_fred(), 'FRED'
        except Exception as exc:  # noqa: BLE001
            print(f'FEIL: verken EIA eller FRED svarte ({exc}) - beholder forrige fil', file=sys.stderr)
            return 1
    rows.sort()
    payload = {'series': 'RBRTE', 'source': source, 'columns': ['date', 'usd'], 'rows': rows}
    text = json.dumps(payload, separators=(',', ':')) + '\n'
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as fh:
            old = fh.read()
        if old == text:
            print('  = brent_eia.json uendret')
            return 0
        # Samme tall fra FRED som allerede ligger der fra EIA: ikke bytt kilde-
        # merket fram og tilbake uten at tallene er endret.
        try:
            if json.loads(old).get('rows') == rows:
                print('  = brent_eia.json uendret (samme tall)')
                return 0
        except ValueError:
            pass
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f'  + brent_eia.json skrevet fra {source}: {len(rows)} dager, siste {rows[-1][0]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
