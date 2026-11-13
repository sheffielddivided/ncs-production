"""Bygger data/home.json: nøkkeltallene til startsiden, ferdig regnet ut.

Startsiden trengte tidligere hele produksjonsbundelen (1,3 MB) og
lisensiærlisten (0,65 MB) før den kunne vise noe. Denne fila er noen få kB og
hentes først, så startsiden står klar mens resten lastes i bakgrunnen. Tallene
regnes på samme måte som homeCompute() i index.html, som fortsatt brukes når
fila mangler.

Leser bare filer som alt ligger i data/ og bruker ikke nettet. Kjøres sist i
update-sodir-data.yml, etter at Sodir-dataene og produksjonsmeldingene er hentet.
"""
import calendar
import json
import os
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
BBL_PER_SM3 = 6.29
# Felt i prøveproduksjon som ikke står i feltlisten (samme som i index.html).
EXTRA_NAMES = {17196400: '16/1-12 TROLDHAUGEN'}


def load(name):
    try:
        with open(os.path.join(DATA, name + '.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def days(k):
    return calendar.monthrange(k // 12, k % 12 + 1)[1]


def boed(vol, d):
    return vol * 1e6 * BBL_PER_SM3 / d


def build():
    prod = load('production')
    if not prod or not prod.get('fields'):
        raise SystemExit('data/production.json mangler; home.json er ikke skrevet.')
    fields = prod['fields']

    mon, top = {}, 0
    for rows in fields.values():
        for y, m, _oil, gas, oe, *_ in rows:
            if not (m and m > 0):
                continue
            k = y * 12 + m - 1
            o = mon.setdefault(k, [0.0, 0.0])
            o[0] += oe or 0
            o[1] += gas or 0
            if (oe or 0) > 0 and k > top:
                top = k

    def month_boed(k, i=0):
        return boed(mon[k][i], days(k)) if k in mon else 0

    years = {}
    for k, (oe, gas) in mon.items():
        if k > top:
            continue
        o = years.setdefault(k // 12, [0.0, 0.0, 0, 0])
        o[0] += oe
        o[1] += gas
        o[2] += days(k)
        o[3] += 1
    annual = [{'y': y, 'tot': round(boed(o[0], o[2]), 1), 'gas': round(boed(o[1], o[2]), 1), 'partial': o[3] < 12}
              for y, o in sorted(years.items()) if o[0] > 0]

    names = dict(EXTRA_NAMES)
    fl = load('fields')
    if fl:
        names.update({int(r[0]): r[1] for r in fl['rows']})
    d_top = days(top)
    top_fields = []
    for fid, rows in fields.items():
        v = next((r[4] or 0 for r in rows if r[1] and r[0] * 12 + r[1] - 1 == top), 0)
        if v > 0:
            top_fields.append({'id': int(fid), 'name': names.get(int(fid), str(fid)), 'v': round(boed(v, d_top), 1)})
    top_fields.sort(key=lambda f: -f['v'])

    lic = load('licences')
    active = None
    if lic:
        i = lic['columns'].index('validTo')
        now = time.time() * 1000
        active = sum(1 for r in lic['rows'] if r[i] and r[i] > now)

    owners = load('licensees')
    companies = len({r[1] for r in owners['rows'] if r[1]}) if owners else None

    news = [{k: n.get(k, '') for k in ('title', 'link', 'date', 'description')}
            for n in ((load('sodir_news') or {}).get('items') or [])[:3]]

    prev = month_boed(top - 12)
    return {
        'generated': (load('meta') or {}).get('generated', ''),
        'top': top,
        'boed': round(month_boed(top), 1),
        'gas': round(month_boed(top, 1), 1),
        'yoy': round(month_boed(top) / prev - 1, 6) if prev else None,
        'annual': annual,
        'topFields': top_fields[:10],
        'producing': len(top_fields),
        'activeLic': active,
        'companies': companies,
        'news': news,
    }


if __name__ == '__main__':
    out = json.dumps(build(), ensure_ascii=False, separators=(',', ':'))
    path = os.path.join(DATA, 'home.json')
    old = None
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            old = f.read()
    if out == old:
        print('home.json: uendret')
    else:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(out)
        print(f'home.json: skrevet ({len(out)} byte)')
