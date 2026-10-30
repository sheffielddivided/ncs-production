"""
Henter årlige prisbaner fra 1970 til den simulerte kontantstrømmen i
hubprofilen og skriver dem til data/price_history.json.

- brent: Crude oil, Brent, USD/bbl (Verdensbanken, Commodity Price Data
  «The Pink Sheet», årlige nominelle priser). CC BY 4.0.
- gas: Natural gas, Europe, USD/MMBtu (samme kilde). Verdensbankens
  europeiske gasspris; i dag TTF, tidligere snitt av importpriser.
- usdnok: NOK per USD, årssnitt (FRED AEXNOUS / EXNOUS, Federal Reserve
  H.10). 1970 er Bretton Woods-pariteten 7,142857, siden serien starter 1971.
- uscpi: amerikansk KPI, CPI-U alle varer, årssnitt av månedstall (FRED
  CPIAUCSL). Brukes til å deflatere sjablongkostnaden for opex.

Hele år kommer fra Verdensbankens årsfil. Året etter siste hele år (i dag
inneværende år) regnes som snittet av de månedene som finnes i
Verdensbankens månedsfil, og merkes som delår i "partial".

Feiler en kilde, beholdes forrige verdier for den. Steget i arbeidsflyten er
continue-on-error.
"""

import csv
import io
import json
import os
import re
import sys
import time
from datetime import date

import openpyxl
import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'price_history.json')
START = 1970
WB_PAGE = 'https://www.worldbank.org/en/research/commodity-markets'
WB_COLS = {'brent': 'Crude oil, Brent', 'gas': 'Natural gas, Europe'}
PARITY_1970 = 7.142857
RETRIES = 4


def req(url, **kw):
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, timeout=120, **kw)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(str(last))


def wb_link(kind):
    """Lenken til års- eller månedsfilen endres ved hver publisering, så den
    leses fra Verdensbankens råvaresider."""
    html = req(WB_PAGE).text
    m = re.search(r'https://thedocs\.worldbank\.org/[^"\']*CMO-Historical-Data-' + kind + r'\.xlsx', html)
    if not m:
        raise RuntimeError(f'fant ikke lenke til CMO-Historical-Data-{kind}.xlsx')
    return m.group(0)


def wb_sheet(kind, sheet):
    data = req(wb_link(kind)).content
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = list(wb[sheet].iter_rows(values_only=True))
    hdr = next(i for i, r in enumerate(rows) if r and WB_COLS['brent'] in r)
    idx = {k: rows[hdr].index(name) for k, name in WB_COLS.items()}
    return rows[hdr + 1:], idx


def num(v):
    return float(v) if isinstance(v, (int, float)) else None


def worldbank():
    """{år: {brent, gas}} for hele år, og delåret fra månedsfilen."""
    rows, idx = wb_sheet('Annual', 'Annual Prices (Nominal)')
    out = {}
    for r in rows:
        y = str(r[0] or '')[:4]
        if not y.isdigit() or int(y) < START:
            continue
        vals = {k: num(r[j]) for k, j in idx.items()}
        if all(v is not None for v in vals.values()):
            out[int(y)] = vals
    partial = None
    nxt = max(out) + 1
    if nxt <= date.today().year:
        mrows, midx = wb_sheet('Monthly', 'Monthly Prices')
        acc = {k: [] for k in WB_COLS}
        for r in mrows:
            m = re.match(r'^(\d{4})M(\d{2})$', str(r[0] or ''))
            if not m or int(m.group(1)) != nxt:
                continue
            for k, j in midx.items():
                v = num(r[j])
                if v is not None:
                    acc[k].append(v)
        n = min(len(v) for v in acc.values())
        if n:
            out[nxt] = {k: sum(v[:n]) / n for k, v in acc.items()}
            partial = {'year': nxt, 'months': n}
    return out, partial


def fred(series):
    text = req('https://fred.stlouisfed.org/graph/fredgraph.csv', params={'id': series}).text
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        v = r.get(series)
        if v not in (None, '', '.'):
            out.append((r['observation_date'][:7], float(v)))
    return out


def annual_mean(monthly):
    acc = {}
    for m, v in monthly:
        acc.setdefault(int(m[:4]), []).append(v)
    return {y: sum(v) / len(v) for y, v in acc.items() if y >= START}


def usdnok():
    out = {y: v for (m, v) in fred('AEXNOUS') for y in [int(m[:4])] if y >= START}
    # Årsserien kommer med noen måneders forsinkelse; årene etter fylles med
    # snittet av månedsserien.
    for y, v in annual_mean(fred('EXNOUS')).items():
        out.setdefault(y, v)
    out.setdefault(1970, PARITY_1970)
    return out


def uscpi():
    return annual_mean(fred('CPIAUCSL'))


def main():
    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding='utf-8') as fh:
                old = json.load(fh)
        except ValueError:
            old = {}
    prev = {r[0]: dict(zip(old.get('columns', []), r)) for r in old.get('rows', [])}
    series, partial = {}, old.get('partial')
    try:
        wb, partial = worldbank()
        series['brent'] = {y: v['brent'] for y, v in wb.items()}
        series['gas'] = {y: v['gas'] for y, v in wb.items()}
    except Exception as exc:  # noqa: BLE001
        print(f'  ! Verdensbanken feilet ({exc}) - beholder forrige verdier', file=sys.stderr)
    for key, fn in (('usdnok', usdnok), ('uscpi', uscpi)):
        try:
            series[key] = fn()
        except Exception as exc:  # noqa: BLE001
            print(f'  ! {key} feilet ({exc}) - beholder forrige verdier', file=sys.stderr)
    cols = ['year', 'brent', 'gas', 'usdnok', 'uscpi']
    for key in cols[1:]:
        if key not in series:
            series[key] = {y: r[key] for y, r in prev.items() if r.get(key) is not None}
    years = sorted(set().union(*[set(s) for s in series.values()]))
    if not years:
        print('FEIL: ingen kilder svarte - beholder forrige fil', file=sys.stderr)
        return 1
    rows = [[y] + [None if series[k].get(y) is None else round(series[k][y], 4) for k in cols[1:]]
            for y in years]
    payload = {
        'columns': cols, 'rows': rows, 'partial': partial,
        'sources': {
            'brent': 'World Bank Commodity Price Data (Pink Sheet): Crude oil, Brent',
            'gas': 'World Bank Commodity Price Data (Pink Sheet): Natural gas, Europe',
            'usdnok': 'Federal Reserve H.10 via FRED (AEXNOUS, EXNOUS); 1970 at the Bretton Woods parity',
            'uscpi': 'BLS CPI-U via FRED (CPIAUCSL), annual average',
        },
    }
    if old == payload:
        print('  = price_history.json uendret')
        return 0
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(payload, separators=(',', ':')) + '\n')
    print(f'  + price_history.json skrevet: {years[0]}-{years[-1]}'
          + (f', {partial["year"]} er {partial["months"]} måneder' if partial else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
