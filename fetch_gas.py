"""
Henter månedlige gasspriser til Gas-fanen i Prices og skriver dem til
data/gas_prices.json.

Kilder (alle åpne, med kildehenvisning):
- SSB tabell 11008, utenrikshandel etter varenummer: eksport av naturgass i
  gassform (27112100), alle land. Verdi (NOK) og mengde (Sm³ og kg) per
  måned; verdi delt på volum er hva norsk gass faktisk er solgt for.
  CC BY 4.0.
- IMF Primary Commodity Prices, «Natural gas, EU» (TTF-basert), USD/MMBtu,
  hentet fra FRED (PNGASEUUSDM). Fri bruk med kildehenvisning.
- Henry Hub spot, USD/MMBtu, månedssnitt: EIA (serie RNGWHHD) med nøkkelen i
  EIA_API_KEY, ellers FRED (MHHNGSP). Offentlig data.
- Norges Bank, månedssnitt av USD/NOK og EUR/NOK, til omregning mellom
  enheter. Fri bruk med kildehenvisning.

Daglige europeiske gasspriser (TTF, NBP) er lisensierte børs- og
prisbyrådata og er bevisst ikke med.

Feiler en kilde, beholdes forrige verdier for den; feiler alt, beholdes
forrige fil. Steget i arbeidsflyten er continue-on-error.
"""

import csv
import io
import json
import os
import sys
import time

import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'gas_prices.json')
START = '2000-01'
RETRIES = 4


def req(method, url, **kw):
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.request(method, url, timeout=90, **kw)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(str(last))


def fred_monthly(series):
    text = req('GET', 'https://fred.stlouisfed.org/graph/fredgraph.csv', params={'id': series, 'cosd': START + '-01'}).text
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        d, v = r.get('observation_date') or r.get('DATE'), r.get(series)
        if d and v not in (None, '', '.'):
            out[d[:7]] = round(float(v), 3)
    return out


def ssb_export():
    d = req('POST', 'https://data.ssb.no/api/v0/no/table/11008', json={'query': [
        {'code': 'Varekoder', 'selection': {'filter': 'item', 'values': ['27112100_1988']}},
        {'code': 'ImpEks', 'selection': {'filter': 'item', 'values': ['2']}},
        {'code': 'Land', 'selection': {'filter': 'item', 'values': ['00']}},
    ], 'response': {'format': 'json-stat2'}}).json()
    times = list(d['dimension']['Tid']['category']['index'].keys())
    content = list(d['dimension']['ContentsCode']['category']['index'].keys())
    n, v = len(times), d['value']
    col = {c: [v[i * n + j] for j in range(n)] for i, c in enumerate(content)}
    out = {}
    for j, t in enumerate(times):
        m = t.replace('M', '-')
        if m < START or not col['Mengde2'][j]:
            continue
        out[m] = {'nok': col['Verdi'][j], 'sm3': col['Mengde2'][j], 'kg': col['Mengde1'][j]}
    return out


def henry_hub():
    key = os.environ.get('EIA_API_KEY', '').strip()
    if key:
        try:
            data = req('GET', 'https://api.eia.gov/v2/natural-gas/pri/fut/data/', params={
                'api_key': key, 'frequency': 'monthly', 'data[0]': 'value', 'facets[series][]': 'RNGWHHD',
                'start': START, 'sort[0][column]': 'period', 'sort[0][direction]': 'asc', 'length': 5000,
            }).json()
            rows = (data.get('response') or {}).get('data') or []
            out = {p['period'][:7]: round(float(p['value']), 3) for p in rows if p.get('value') not in (None, '')}
            if out:
                return out, 'EIA'
        except Exception as exc:  # noqa: BLE001
            print(f'  ! EIA feilet ({exc}) - prøver FRED', file=sys.stderr)
    return fred_monthly('MHHNGSP'), 'FRED'


def norges_bank():
    text = req('GET', 'https://data.norges-bank.no/api/data/EXR/M.USD+EUR.NOK.SP',
               params={'format': 'csv', 'startPeriod': START, 'locale': 'en'}).text
    out = {}
    for r in csv.DictReader(io.StringIO(text), delimiter=';'):
        out.setdefault(r['TIME_PERIOD'], {})[r['BASE_CUR'].lower()] = float(r['OBS_VALUE'])
    return out


def main():
    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding='utf-8') as fh:
                old = json.load(fh)
        except ValueError:
            old = {}
    parts, sources = {}, dict(old.get('sources') or {})
    for name, fn in (('ssb', ssb_export), ('imf', lambda: fred_monthly('PNGASEUUSDM')), ('hh', henry_hub), ('fx', norges_bank)):
        try:
            res = fn()
            if name == 'hh':
                res, sources['hh'] = res
            parts[name] = res
        except Exception as exc:  # noqa: BLE001
            print(f'  ! {name} feilet ({exc}) - beholder forrige verdier', file=sys.stderr)
    if not parts:
        print('FEIL: ingen kilder svarte - beholder forrige fil', file=sys.stderr)
        return 1
    prev = {r[0]: r for r in old.get('rows', [])}
    cols = ['month', 'export_nok', 'export_sm3', 'export_kg', 'eu_usd_mmbtu', 'hh_usd_mmbtu', 'usdnok', 'eurnok']
    months = sorted(set().union(*[set(p) for p in parts.values()], set(prev)))
    rows = []
    for m in months:
        p = prev.get(m, [m] + [None] * 7)
        s = parts['ssb'].get(m) if 'ssb' in parts else None
        fx = parts['fx'].get(m) if 'fx' in parts else None
        row = [m,
               s['nok'] if s else (None if 'ssb' in parts else p[1]),
               s['sm3'] if s else (None if 'ssb' in parts else p[2]),
               s['kg'] if s else (None if 'ssb' in parts else p[3]),
               parts['imf'].get(m) if 'imf' in parts else p[4],
               parts['hh'].get(m) if 'hh' in parts else p[5],
               fx.get('usd') if fx else (None if 'fx' in parts else p[6]),
               fx.get('eur') if fx else (None if 'fx' in parts else p[7])]
        if any(x is not None for x in row[1:]):
            rows.append(row)
    sources.update({'ssb': 'SSB table 11008', 'imf': 'IMF via FRED (PNGASEUUSDM)', 'fx': 'Norges Bank'})
    sources.setdefault('hh', 'FRED')
    payload = {'columns': cols, 'rows': rows, 'sources': sources}
    text = json.dumps(payload, separators=(',', ':')) + '\n'
    # Uendret bare når både tallene og kildemerkene er like; ellers ville et
    # bytte fra FRED til EIA med identiske tall (FRED publiserer EIAs serie)
    # aldri blitt synlig i fila.
    if old and json.dumps(old.get('rows')) == json.dumps(rows) and old.get('sources') == sources:
        print('  = gas_prices.json uendret')
        return 0
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text)
    last = lambda i: max((r[0] for r in rows if r[i] is not None), default='-')
    print(f"  + gas_prices.json skrevet: SSB til {last(1)}, IMF til {last(4)}, Henry Hub ({sources['hh']}) til {last(5)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
