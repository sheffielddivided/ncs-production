"""
Bygger data/rates.json til Interest-fanen i Market: styringsrente,
inflasjon og statsrentekurve for Norge, USA, euroområdet, Storbritannia og
Sverige.

Hovedkilden er dataene som repoet sheffielddivided/convert samler inn daglig
til nb.html (data/nb/interest.json og data/nb/inflation.json):

  Styringsrente  NO Norges Bank (foliorenten)   · EU ECB innskuddsrente
                 UK Bank of England Bank Rate   · SE Riksbanken styrränta
  Inflasjon      NO SSB KPI, 12-månedersendring · US CPI-U (FRED, pc1)
                 EU HICP euroområdet (Eurostat) · UK CPI (ONS D7G7)
                 SE HICP Sverige (Eurostat)
  Rentekurve     NO Norges Bank, statskasseveksler og statsobligasjoner
                 EU ECB AAA-kurve (og tyske Bund fra Bundesbank)
                 UK Bank of England, nominell spotkurve for gilts
                 SE Riksbanken, statsskuldväxlar og statsobligationer

USA hentes her i tillegg, fordi convert mangler 2 år i kurven og bruker
månedssnittet av effektiv Fed funds:
  · Feds målintervall (FRED DFEDTARL/DFEDTARU, daglig); serien er midtpunktet.
  · Hele Treasury-kurven (home.treasury.gov, Daily Treasury Par Yield Curve).
Feiler en av disse, brukes convert-tallene.

Styringsrentene lagres som endringspunkter pluss siste observasjon.
Feiler hovedkilden, beholdes forrige fil. Steget i arbeidsflyten er
continue-on-error.
"""

import csv
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone

import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'rates.json')
CONVERT = 'https://raw.githubusercontent.com/sheffielddivided/convert/main/data/nb/'
FRED = 'https://fred.stlouisfed.org/graph/fredgraph.csv'
TREASURY = ('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml'
            '?data=daily_treasury_yield_curve&field_tdr_date_value={}')
US_TENORS = {'BC_1MONTH': '1M', 'BC_3MONTH': '3M', 'BC_6MONTH': '6M', 'BC_1YEAR': '1Y', 'BC_2YEAR': '2Y',
             'BC_3YEAR': '3Y', 'BC_5YEAR': '5Y', 'BC_7YEAR': '7Y', 'BC_10YEAR': '10Y', 'BC_20YEAR': '20Y',
             'BC_30YEAR': '30Y'}
START = f'{date.today().year - 21}-01-01'


def req(url, **kw):
    last = None
    for attempt in range(4):
        try:
            # Standard-User-Agent: FRED avviser ukjente.
            r = requests.get(url, timeout=90, **kw)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(str(last))


def tenor_years(t):
    t = t.upper()
    if t.endswith('M'):
        return round(int(t[:-1]) / 12, 4)
    return float(t[:-1])


def tenor_label(t):
    """12M -> 1Y, så alle kurvene bruker samme navn."""
    y = tenor_years(t)
    return f'{int(y)}Y' if y >= 1 and y == int(y) else t.upper()


def curve(rates, dt):
    pts = sorted(((tenor_years(t), tenor_label(t), round(v, 3)) for t, v in (rates or {}).items() if v is not None))
    return {'date': dt, 'points': [[y, l, v] for y, l, v in pts]} if pts else None


def change_points(series):
    s = [[d, round(v, 3)] for d, v in series if v is not None]
    out = [r for i, r in enumerate(s) if i == 0 or r[1] != s[i - 1][1]]
    if s and out[-1] is not s[-1]:
        out.append(s[-1])
    return out


def fred(series_id):
    text = req(FRED, params={'id': series_id, 'cosd': START}).text
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        v = r.get(series_id)
        if v not in (None, '', '.'):
            out.append([r['observation_date'], float(v)])
    if not out:
        raise RuntimeError(f'ingen data for {series_id}')
    return out


def us_policy():
    lo, hi = dict(fred('DFEDTARL')), dict(fred('DFEDTARU'))
    days = sorted(set(lo) & set(hi))
    return change_points([[d, (lo[d] + hi[d]) / 2] for d in days]), [lo[days[-1]], hi[days[-1]]]


def us_curve():
    year = date.today().year
    entries = []
    for y in (year, year - 1):
        xml = req(TREASURY.format(y)).text
        entries = re.split(r'<entry[\s>]', xml)[1:]
        if entries:
            break
    best = None
    for e in entries:
        m = re.search(r'<d:NEW_DATE[^>]*>([^<T]*)', e)
        if m and (best is None or m.group(1) > best[0]):
            best = (m.group(1), e)
    if not best:
        raise RuntimeError('ingen Treasury-kurve')
    rates = {}
    for key, t in US_TENORS.items():
        m = re.search(rf'<d:{key}[^>]*>([^<]*)<', best[1])
        if m and m.group(1).strip():
            rates[t] = float(m.group(1))
    return curve(rates, best[0])


def monthly(series):
    return [[d[:7], round(v, 2)] for d, v in series if v is not None]


def main():
    try:
        ir = req(CONVERT + 'interest.json').json()
        inf = req(CONVERT + 'inflation.json').json()
    except Exception as exc:  # noqa: BLE001
        print(f'FEIL: fikk ikke hentet data fra convert ({exc}) - beholder forrige fil', file=sys.stderr)
        return 1
    upd = (ir.get('updated') or '')[:10] or None

    cpi = inf.get('cpi') or {}
    no_inf = [[l.replace('M', '-'), v] for l, v in zip(cpi.get('labels', []), (cpi.get('series') or {}).get('Tolvmanedersendring', []))
              if v is not None]

    eu = ir.get('euYields') or {}
    c = {
        'NO': {'policy': change_points(ir.get('policy') or []), 'inflation': no_inf,
               'curve': curve(ir.get('nbYields'), upd)},
        'US': {'policy': change_points(ir.get('fed') or []), 'inflation': monthly(inf.get('usCpi') or []),
               'curve': curve((ir.get('usYields') or {}).get('rates'), (ir.get('usYields') or {}).get('date'))},
        'EU': {'policy': change_points(ir.get('ecb') or []), 'inflation': monthly(inf.get('euHicp') or []),
               'curve': curve((eu.get('ecb') or {}).get('rates'), (eu.get('ecb') or {}).get('date')),
               'curve2': curve((eu.get('de') or {}).get('rates'), (eu.get('de') or {}).get('date'))},
        'UK': {'policy': change_points(ir.get('boe') or []), 'inflation': monthly(inf.get('ukCpi') or []),
               'curve': curve((ir.get('ukYields') or {}).get('rates'), (ir.get('ukYields') or {}).get('date'))},
        'SE': {'policy': change_points(ir.get('riksbank') or []), 'inflation': monthly(inf.get('seHicp') or []),
               'curve': curve((ir.get('seYields') or {}).get('rates'), (ir.get('seYields') or {}).get('date'))},
    }
    c['US']['policyKind'] = 'fedfunds'
    try:
        c['US']['policy'], c['US']['range'] = us_policy()
        c['US']['policyKind'] = 'target'
    except Exception as exc:  # noqa: BLE001
        print(f'  ! Feds målintervall feilet ({exc}) - bruker Fed funds fra convert', file=sys.stderr)
    try:
        c['US']['curve'] = us_curve()
    except Exception as exc:  # noqa: BLE001
        print(f'  ! Treasury-kurven feilet ({exc}) - bruker convert', file=sys.stderr)

    empty = [k for k, v in c.items() if not v['policy'] or not v['inflation']]
    if len(empty) == len(c):
        print('FEIL: ingen data - beholder forrige fil', file=sys.stderr)
        return 1
    if empty:
        print(f'  ! mangler data for {", ".join(empty)}', file=sys.stderr)
    out = {'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'convertUpdated': upd, 'countries': c}
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, separators=(',', ':'))
    for k, v in c.items():
        cv = v.get('curve') or {}
        print(f'{k}: styringsrente {v["policy"][-1] if v["policy"] else "-"}, inflasjon {v["inflation"][-1] if v["inflation"] else "-"}, '
              f'kurve {cv.get("date")} {[p[1] for p in cv.get("points", [])]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
