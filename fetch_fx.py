"""
Henter daglige valutakurser fra Norges Bank til Forex-fanen i Market og
skriver dem til data/fx.json.

Kilde: Norges Banks datatjeneste (data.norges-bank.no), datasett EXR,
serien B.<valuta>.NOK.SP — offisielle daglige kurser (midtkurs fastsatt ved
ECBs konsultasjon kl. 14.15). Samme kilde som nb.html i repoet
sheffielddivided/convert bruker.

Norges Bank oppgir kursen som kroner per enhet, men noen valutaer (SEK, DKK)
per 100 enheter (UNIT_MULT=2). Her regnes alt om til kroner per 1 enhet, så
appen kan regne kryss­kurser direkte: 1 A = kurs(A) / kurs(B) B.

Format:
  {"updated": ..., "source": ..., "currencies": [...],
   "dates": ["YYYY-MM-DD", ...],
   "rates": {"USD": [kurs | null, ...], ...}}   # NOK per 1 enhet

Feiler kilden, beholdes forrige fil. Steget i arbeidsflyten er
continue-on-error.
"""

import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'fx.json')
CURRENCIES = ['USD', 'EUR', 'GBP', 'SEK', 'DKK', 'CHF', 'CAD']
START = '2000-01-01'
URL = 'https://data.norges-bank.no/api/data/EXR/B.{}.NOK.SP'
RETRIES = 4


def fetch():
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(URL.format('+'.join(CURRENCIES)),
                             params={'format': 'csv', 'startPeriod': START, 'locale': 'en'},
                             headers={'User-Agent': 'ncs-insight-fx/1.0'}, timeout=120)
            r.raise_for_status()
            return r.text
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(str(last))


def main():
    try:
        text = fetch()
    except Exception as exc:  # noqa: BLE001
        print(f'FEIL: Norges Bank svarte ikke ({exc}) - beholder forrige fil', file=sys.stderr)
        return 1
    by_cur = {c: {} for c in CURRENCIES}
    for r in csv.DictReader(io.StringIO(text), delimiter=';'):
        cur, v = r.get('BASE_CUR'), r.get('OBS_VALUE')
        if cur not in by_cur or v in (None, '', 'NaN'):
            continue
        mult = 10 ** int(r.get('UNIT_MULT') or 0)
        by_cur[cur][r['TIME_PERIOD']] = round(float(v.replace(',', '')) / mult, 6)  # locale=en har tusenskille
    missing = [c for c in CURRENCIES if not by_cur[c]]
    if missing:
        print(f'FEIL: ingen data for {", ".join(missing)} - beholder forrige fil', file=sys.stderr)
        return 1
    dates = sorted(set().union(*[set(s) for s in by_cur.values()]))
    out = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'Norges Bank, exchange rates (EXR, daily, NOK per unit)',
        'currencies': CURRENCIES,
        'dates': dates,
        'rates': {c: [by_cur[c].get(d) for d in dates] for c in CURRENCIES},
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print(f'Skrev {OUT}: {len(dates)} dager, {dates[0]}–{dates[-1]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
