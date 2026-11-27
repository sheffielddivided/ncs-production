"""
Henter data fra repoet sheffielddivided/eia, som samler dem inn daglig (eugas
og EIA-delen), til disse filene:

- data/ttf_daily.json: daglige TTF day-ahead-priser (EEX TTF Neutral Gas
  Price, EUR/MWh). Brukes bare til prognosen for inneværende periode i
  Gas-fanen i Macro; dagsprisene vises ikke selv.
- data/eu_storage.json: fyllingsgraden i europeiske gasslagre per dag, i % av
  kapasitet (AGSI+, Gas Infrastructure Europe), for EU totalt og Tyskland,
  Italia, Nederland og Frankrike. Til EU storage-fanen i Macro.
- data/eu_lng.json: LNG send-out (regassifisering) fra europeiske
  LNG-terminaler per dag, GWh/d (ALSI+, Gas Infrastructure Europe), for EU
  totalt og Spania, Frankrike, Italia og Nederland. Til samme fane.
- data/us_supply.json: amerikanske lagre av råolje og produkter (EIA, ukentlig,
  tusen fat), LNG-eksport (EIA, månedlig, MMcf) og LNG-eksportprisen (EIA,
  månedlig, USD/MMBtu) og råoljeproduksjonen (EIA, ukentlig WCRFPUS2 og
  månedlig MCRFPUS2, tusen fat per dag). Til US supply-fanen og
  gassprisgrafen i Macro.
- data/refining.json: ukentlige spotpriser til 3-2-1-raffineringsmarginen i
  Refining-fanen i Macro: WTI Cushing (RWTC, USD/fat), NY Harbor bensin
  (regular, USD/gallon) og NY Harbor nr. 2 fyringsolje/diesel (USD/gallon),
  samme serier som Refining-visningen i eia-repoet. Brent (RBRTE) er med til
  varianten mot Brent. Marginen regnes ut i appen.
- data/eu_gas_flows.json: daglig gass inn i EU etter opprinnelse, GWh/d:
  rørgass ved EUs yttergrense (ENTSOG) per leverandør og LNG-utsendelse
  (ALSI), og russisk rørgass per rute. Bygget fra data/gas_flows.json i
  eia-repoet med samme gruppering og dagsutvalg som gassimport.html der
  (Import etter opprinnelse og Russisk gass etter rute). Til EU gas-fanen i Macro.
- data/steo.json: EIAs Short-Term Energy Outlook, tabell 3a og 3c (verdens
  oljebalanse: produksjon, forbruk, lagertrekk og OECD-lagre; månedlig,
  kvartalsvis og årlig, historikk og prognose). Kopieres uendret fra
  data/eia/steo.json i eia-repoet, til Oil market-fanen i Macro.
- data/steo_vintages.json: tidligere STEO-utgaver (siste to år) med de samme
  seriene, fra EIAs arkiv over STEO-arbeidsbøkene. Kopieres uendret fra
  data/eia/steo_vintages.json i eia-repoet, til endringsmodusen («Δ vs.
  earlier STEO») i Oil market.

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


# Seriene bak 3-2-1-marginen i eia-repoet (index.html, loadRefining).
REFINING_SERIES = {
    'wti': 'RWTC',                          # WTI Cushing, USD/fat
    'brent': 'RBRTE',                       # Brent spot, USD/fat
    'gasoline': 'EER_EPMRU_PF4_Y35NY_DPG',  # NY Harbor regular bensin, USD/gallon
    'diesel': 'EER_EPD2F_PF4_Y35NY_DPG',    # NY Harbor nr. 2 fyringsolje, USD/gallon
}


def refining():
    prices = get('eia/prices.json')
    out = {k: [[x['date'], x['value']] for x in ((prices or {}).get(sid) or []) if x.get('value') is not None]
           for k, sid in REFINING_SERIES.items()}
    if not (out['wti'] and out['gasoline'] and out['diesel']):
        print('eugas: mangler WTI-, bensin- eller dieselpriser i kilden; beholder forrige fil', file=sys.stderr)
        return
    write('refining.json', {'updated': (prices or {}).get('updatedAt'), 'series': REFINING_SERIES, **out,
                            'source': 'U.S. Energy Information Administration (EIA), weekly spot prices, via sheffielddivided/eia'})
    print(f'eugas: raffinering {", ".join(f"{k} {len(v)}" for k, v in out.items())}; siste {out["wti"][-1][0]}')


def steo():
    src = get('eia/steo.json')
    d = (src or {}).get('data') or {}
    if not ((d.get('quarterly') or {}).get('PAPR_WORLD') and src.get('lastHistMonth')):
        print('eugas: ingen STEO-data i kilden; beholder forrige fil', file=sys.stderr)
        return
    src['source'] = 'U.S. Energy Information Administration (EIA), Short-Term Energy Outlook, via sheffielddivided/eia'
    write('steo.json', src)
    print(f'eugas: STEO {src.get("release")}, historikk til {src["lastHistMonth"]}, {len(src.get("meta") or {})} serier')


def steo_vintages():
    src = get('eia/steo_vintages.json')
    vs = (src or {}).get('vintages') or []
    if not vs or not all(v.get('release') and (v.get('data') or {}).get('quarterly') for v in vs):
        print('eugas: ingen tidligere STEO-utgaver i kilden; beholder forrige fil', file=sys.stderr)
        return
    write('steo_vintages.json', src)
    print(f'eugas: tidligere STEO-utgaver {vs[-1]["release"]}–{vs[0]["release"]} ({len(vs)})')


# Grupperingen fra gassimport.html i eia-repoet. LNG er egen serie i kilden.
FLOW_GROUPS = {'NO': ['NO'], 'RU': ['RU_NS', 'RU_UA', 'RU_YA', 'RU_TS', 'RU_BALT'],
               'DZ': ['DZ'], 'AZ': ['AZ'], 'OTH': ['UK', 'LY']}
RU_ROUTES = ['RU_NS', 'RU_UA', 'RU_YA', 'RU_TS', 'RU_BALT']


def gas_flows(src=None):
    src = src or get('gas_flows.json')
    if not src or not src.get('routes') or not src.get('lng') or not src.get('start'):
        print('eugas: ingen gasstrømmer i kilden; beholder forrige fil', file=sys.stderr)
        return
    pts, routes, lng = src['points'], src['routes'], src['lng']
    n = len(lng)

    def route(rk, i):
        return sum((pts.get(p) or [None] * n)[i] or 0 for p in routes.get(rk, {}).get('points', []))

    from datetime import date, timedelta
    d0 = date.fromisoformat(src['start'])
    days, groups, ru = [], {k: [] for k in list(FLOW_GROUPS) + ['LNG']}, {k: [] for k in RU_ROUTES}
    for i in range(n):
        # Som i gassimport.html: bare dager der Norge er rapportert og LNG finnes.
        if lng[i] is None or not any((pts.get(p) or [None] * n)[i] is not None for p in routes['NO']['points']):
            continue
        days.append((d0 + timedelta(days=i)).isoformat())
        for g, rks in FLOW_GROUPS.items():
            groups[g].append(round(sum(route(rk, i) for rk in rks), 1))
        groups['LNG'].append(round(lng[i], 1))
        for rk in RU_ROUTES:
            ru[rk].append(round(route(rk, i), 1))
    if not days:
        print('eugas: ingen gyldige dager i gasstrømmene; beholder forrige fil', file=sys.stderr)
        return
    write('eu_gas_flows.json', {'updated': src.get('updatedAt'), 'unit': 'GWh/d', 'days': days,
                                'groups': groups, 'russia': ru,
                                'source': 'ENTSOG Transparency Platform (pipeline entry flows) and GIE ALSI (LNG send-out), via sheffielddivided/eia'})
    print(f'eugas: gasstrømmer {len(days)} dager, {days[0]}–{days[-1]}')


if __name__ == '__main__':
    ttf()
    storage()
    lng()
    us_supply()
    refining()
    gas_flows()
    steo()
    steo_vintages()
