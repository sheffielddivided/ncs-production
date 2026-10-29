"""
Henter månedlige konsumprisindekser til realprisvisningen i Prices og
skriver dem til data/cpi.json.

- USD: amerikansk KPI, CPI-U alle varer, sesongjustert (BLS, via FRED
  CPIAUCSL). Offentlig data.
- EUR: euroområdets harmoniserte KPI, HICP alle varer (Eurostat
  prc_hicp_minr, 2025=100). CC BY 4.0.
- NOK: KPI, historisk serie (SSB tabell 14710, 2025=100). CC BY 4.0.
  Fra 1970, så NCS / Investments kan regnes om til faste kroner.

Indeksnivået er det kildene oppgir; appen bruker bare forholdet mellom
måneder, med siste observasjon som basis. Mangler en måned midt i en serie
(oktober 2025 i amerikansk KPI, da BLS ikke publiserte), fylles den med
snittet av nabomånedene, og måneden merkes som interpolert.

Feiler en kilde, beholdes forrige verdier for den. Steget i arbeidsflyten er
continue-on-error.
"""

import csv
import io
import json
import os
import sys
import time

import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'cpi.json')
START = '1990-01'
START_NOK = '1970-01'
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


def us():
    text = req('GET', 'https://fred.stlouisfed.org/graph/fredgraph.csv', params={'id': 'CPIAUCSL', 'cosd': START + '-01'}).text
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        v = r.get('CPIAUCSL')
        if v not in (None, '', '.'):
            out[r['observation_date'][:7]] = float(v)
    return out


def ea():
    d = req('GET', 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr',
            params={'geo': 'EA', 'coicop18': 'TOTAL', 'unit': 'I25', 'format': 'JSON'}).json()
    idx = d['dimension']['time']['category']['index']
    vals = d['value']
    return {t: float(vals[str(i)]) for t, i in idx.items() if str(i) in vals and t >= START}


def no():
    d = req('POST', 'https://data.ssb.no/api/v0/no/table/14710', json={'query': [], 'response': {'format': 'json-stat2'}}).json()
    times = list(d['dimension']['Tid']['category']['index'].keys())
    out = {}
    for t, v in zip(times, d['value']):
        m = t.replace('M', '-')
        if v is not None and m >= START_NOK:
            out[m] = float(v)
    return out


def months_between(a, b):
    y, m = int(a[:4]), int(a[5:])
    out = []
    while f'{y:04d}-{m:02d}' <= b:
        out.append(f'{y:04d}-{m:02d}')
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def fill_gaps(series):
    """Fyll enkeltmåneder som mangler midt i serien med snittet av naboene."""
    if not series:
        return series, []
    ms = months_between(min(series), max(series))
    filled, interp = dict(series), []
    for i, m in enumerate(ms):
        if m in filled:
            continue
        prev = next((filled[x] for x in reversed(ms[:i]) if x in filled), None)
        nxt = next((series[x] for x in ms[i + 1:] if x in series), None)
        if prev is not None and nxt is not None:
            filled[m] = round((prev + nxt) / 2, 3)
            interp.append(m)
    return filled, interp


def main():
    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding='utf-8') as fh:
                old = json.load(fh)
        except ValueError:
            old = {}
    out, interp = {}, {}
    for key, fn in (('USD', us), ('EUR', ea), ('NOK', no)):
        try:
            series, gaps = fill_gaps(fn())
            out[key] = dict(sorted(series.items()))
            interp[key] = gaps
        except Exception as exc:  # noqa: BLE001
            print(f'  ! {key} feilet ({exc}) - beholder forrige verdier', file=sys.stderr)
            if key in (old.get('series') or {}):
                out[key] = old['series'][key]
                interp[key] = (old.get('interpolated') or {}).get(key, [])
    if not out:
        print('FEIL: ingen kilder svarte - beholder forrige fil', file=sys.stderr)
        return 1
    payload = {'series': out, 'interpolated': interp,
               'sources': {'USD': 'BLS CPI-U via FRED (CPIAUCSL)', 'EUR': 'Eurostat HICP euro area (prc_hicp_minr)',
                           'NOK': 'SSB table 14710'}}
    text = json.dumps(payload, separators=(',', ':')) + '\n'
    if old == payload:
        print('  = cpi.json uendret')
        return 0
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print('  + cpi.json skrevet: ' + ', '.join(f'{k} til {max(v)}' for k, v in out.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
