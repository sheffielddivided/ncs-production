"""
Henter data fra repoet sheffielddivided/eia, som samler dem inn daglig (eugas
og EIA-delen), til disse filene:

- data/ttf_daily.json: daglige TTF day-ahead-priser (EEX TTF Neutral Gas
  Price, EUR/MWh). Brukes bare til prognosen for inneværende periode i
  Gas-fanen i Market; dagsprisene vises ikke selv.
- data/eu_storage.json: fyllingsgraden i europeiske gasslagre per dag, i % av
  kapasitet (AGSI+, Gas Infrastructure Europe), for EU totalt og Tyskland,
  Italia, Nederland og Frankrike. Til EU storage-fanen i Market.
- data/eu_lng.json: LNG send-out (regassifisering) fra europeiske
  LNG-terminaler per dag, GWh/d (ALSI+, Gas Infrastructure Europe), for EU
  totalt og Spania, Frankrike, Italia og Nederland. Til samme fane.
- data/us_supply.json: amerikanske lagre av råolje og produkter (EIA, ukentlig,
  tusen fat), LNG-eksport (EIA, månedlig, MMcf) og LNG-eksportprisen (EIA,
  månedlig, USD/MMBtu) og råoljeproduksjonen (EIA, ukentlig WCRFPUS2 og
  månedlig MCRFPUS2, tusen fat per dag). Til US supply-fanen og
  gassprisgrafen i Market.

Feiler en henting, beholdes forrige fil for den. Steget i arbeidsflyten er
continue-on-error.
"""

import json
import os
import sys
import time

import requests

BASE = 'https://raw.githubusercontent.com/sheffielddivided/eia/main/data/'
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def get(name):
    last = None
    for attempt in range(4):
        try:
            r = requests.get(BASE + name, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 ** (attempt + 1))
    print(f'eugas: henting av {name} feilet ({last}); beholder forrige fil', file=sys.stderr)
    return None


def write(name, obj):
    with open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


def ttf():
    src = get('ttf_price.json')
    if not src:
        return
    rows = sorted([d, round(float(v), 3)] for d, v in (src.get('prices') or {}).items() if v is not None)
    if not rows:
        print('eugas: ingen TTF-priser i kilden; beholder forrige fil', file=sys.stderr)
        return
    write('ttf_daily.json', {'columns': ['date', 'eur_mwh'], 'rows': rows, 'updated': src.get('updatedAt'),
                             'source': 'EEX TTF Neutral Gas Price (day-ahead), via sheffielddivided/eia (eugas)'})
    print(f'eugas: TTF {len(rows)} dager, {rows[0][0]} – {rows[-1][0]}')


def storage():
    src = get('agsi.json')
    sel = (src or {}).get('selections') or {}
    # Bare fyllingsgraden (full), per år og MM-DD.
    out = {k: {y: {d: v for d, v in days.items() if v is not None} for y, days in (s.get('full') or {}).items()}
           for k, s in sel.items()}
    if not out.get('eu'):
        print('eugas: ingen lagerdata i kilden; beholder forrige fil', file=sys.stderr)
        return
    write('eu_storage.json', {'updated': src.get('updatedAt'), 'full': out,
                              'source': 'AGSI+ (Gas Infrastructure Europe), via sheffielddivided/eia (eugas)'})
    print(f'eugas: lager {", ".join(out)}; år {", ".join(sorted(out["eu"]))}')


def lng():
    src = get('alsi.json')
    sel = (src or {}).get('selections') or {}
    out = {k: {y: {d: v for d, v in days.items() if v is not None} for y, days in (s.get('sendOut') or {}).items()}
           for k, s in sel.items()}
    if not out.get('eu'):
        print('eugas: ingen LNG-data i kilden; beholder forrige fil', file=sys.stderr)
        return
    write('eu_lng.json', {'updated': src.get('updatedAt'), 'sendOut': out,
                          'source': 'ALSI+ (Gas Infrastructure Europe), via sheffielddivided/eia (eugas)'})
    print(f'eugas: LNG {", ".join(out)}; år {", ".join(sorted(out["eu"]))}')


def us_supply():
    inv = get('eia/inventory.json')
    flows = get('eia/flows.json')
    prices = get('eia/prices.json')
    if not inv or not (inv.get('series') or {}).get('WCRSTUS1'):
        print('eugas: ingen EIA-lagerdata i kilden; beholder forrige fil', file=sys.stderr)
        return
    def pairs(arr):
        return [[x['date'], x['value']] for x in (arr or []) if x.get('value') is not None]
    write('us_supply.json', {
        'updated': inv.get('updatedAt'),
        'inventory': {k: pairs(v) for k, v in inv['series'].items()},
        'lng': pairs((flows or {}).get('lng')),
        'lngPrice': pairs((prices or {}).get('lng_price')),
        'crudeProd': {
            'weekly': pairs(((flows or {}).get('petroleum') or {}).get('WCRFPUS2')),
            'monthly': pairs(((flows or {}).get('petroleum_monthly') or {}).get('MCRFPUS2')),
        },
        'source': 'U.S. Energy Information Administration (EIA), via sheffielddivided/eia',
    })
    print(f'eugas: US supply {", ".join(inv["series"])}; LNG {len((flows or {}).get("lng") or [])} mnd')


if __name__ == '__main__':
    ttf()
    storage()
    lng()
    us_supply()
