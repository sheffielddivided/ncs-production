"""
Oppdateringslogg: hva som er endret i dataene, dag for dag.

Sammenligner datafilene med forrige versjon i git og skriver én linje per
kilde og dag til data/update_log.json, som updates.html viser (lenke nederst
på forsiden). For de viktigste filene står det hva som er nytt («Production:
September 2026 published, 3 fields revised»), for resten at kilden er
oppdatert.

Hver dag er forskjellen fra slutten av dagen før til slutten av dagen, så
flere kjøringer samme dag gir nettoendringen (tall summert, siste dato), og
tidspunktene for committene som endret kilden. Kjøres av update-log.yml når en
oppdateringsworkflow er ferdig:

    python build_update_log.py               # regn de siste 3 dagene på nytt
    python build_update_log.py --backfill 90  # bygg hele loggen fra git-historikken

Krever git-historikk tilbake til starten av dagen før; en grunn klone utdypes
automatisk.

Detaljer: i tillegg skrives data/update_details/ÅÅÅÅ-MM-DD.json med hva
endringene er, ikke bare at noe er endret: nye produksjonstall per felt,
reviderte måneder (gammel → ny), nye og endrede brønner, funn, lisenser og
innretninger, nyhetstitler med lenke, Havtil-saker, Gassco-meldinger,
lisensoverdragelser og nye priser. Hver kilde får en liste med korte
faktasetninger på engelsk («facts»), egnet som grunnlag for en AI-generert
daglig oppsummering. data/update_details/index.json lister dagene.
"""

import argparse
import collections
import datetime as dt
import json
import os
import subprocess
import sys

OUT = 'data/update_log.json'
KEEP_DAYS = 180
DETAIL_DIR = 'data/update_details'
KEEP_DETAIL_DAYS = 90
MAX_FACTS = 40              # per kilde og dag; resten telles («… and 12 more»)
BBL_PER_SM3 = 6.2898
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December']

# Seksjoner i den rekkefølgen siden viser dem
SECTIONS = [
    ('ncs', 'NCS · Sodir'),
    ('activity', 'Activity · rigs, shipping, safety, gas transport'),
    ('news', 'News'),
    ('markets', 'Markets and macro'),
    ('shelves', 'Other shelves'),
    ('edits', 'Edited in the app'),
    ('other', 'Other'),
]

# fil → (seksjon, etikett). Filer som ikke står her, havner i «Other».
FILES = {
    'production': ('ncs', 'Production'),
    'reserves_field': ('ncs', 'Field reserves'),
    'reserves_field_hist': ('ncs', 'Reserve history'),
    'reserves': ('ncs', 'Company reserves'),
    'fields': ('ncs', 'Fields'),
    'field_profile': ('ncs', 'Field facts'),
    'field_descriptions': ('ncs', 'Field descriptions'),
    'field_outlines': ('ncs', 'Field outlines'),
    'field_investments': ('ncs', 'Field investments'),
    'field_operators': ('ncs', 'Field operators'),
    'field_wells': ('ncs', None),
    'discoveries': ('ncs', 'Discoveries'),
    'discovery_resources': ('ncs', 'Discovery resources'),
    'discovery_descriptions': ('ncs', 'Discovery descriptions'),
    'discovery_outlines': ('ncs', 'Discovery outlines'),
    'licences': ('ncs', 'Licences'),
    'licence_licensees': ('ncs', 'Licence ownership'),
    'licence_transfers': ('ncs', 'Licence transfers'),
    'licence_history': ('ncs', 'Licence history'),
    'licence_all': ('ncs', 'Licence register'),
    'licence_areas': ('ncs', 'Licence areas'),
    'licence_baa': ('ncs', 'Business arrangement areas'),
    'licence_outlines': ('ncs', 'Licence outlines'),
    'licensees': ('ncs', 'Licensee shares over time'),
    'companies': ('ncs', 'Companies'),
    'exploration_wells': ('ncs', 'Exploration wells'),
    'exploration_prospects': ('ncs', 'Prospect names'),
    'rig_wells': ('ncs', 'Development wells'),  # se SUMMARY
    'well_activity': ('ncs', None),
    'facilities': ('ncs', 'Facilities'),
    'pipelines': ('ncs', 'Pipelines'),
    'home': ('ncs', None),
    'meta': ('ncs', None),
    'changes': ('activity', 'News events'),
    'rig_now': ('activity', 'Rig positions'),
    'rig_tracks': ('activity', None),
    'rig_log': ('activity', None),
    'rig_health': ('activity', None),
    'rig_contracts': ('activity', 'Rig contracts'),
    'rig_facts': ('activity', 'Rig facts'),
    'shipping': ('activity', 'Shuttle tanker calls'),
    'shipping_log': ('activity', None),
    'tanker_obs': ('activity', 'Tanker draught readings'),
    'havtil': ('activity', 'Havtil cases'),
    'gassco_events': ('activity', 'Gassco events'),
    'gassco_downtime': ('activity', None),
    'sodir_press': ('news', 'Sodir press releases'),
    'sodir_news': ('news', 'Sodir production news'),
    'company_news': ('news', 'Company news'),
    'brent_eia': ('markets', 'Brent (EIA)'),
    'gas_prices': ('markets', 'Gas prices'),
    'ttf_daily': ('markets', 'TTF'),
    'eu_storage': ('markets', 'EU gas storage'),
    'eu_gas_flows': ('markets', 'EU gas flows'),
    'eu_lng': ('markets', 'EU LNG'),
    'steo': ('markets', 'EIA STEO'),
    'steo_vintages': ('markets', 'EIA STEO archive'),
    'us_supply': ('markets', 'US supply'),
    'refining': ('markets', 'Refining'),
    'fx': ('markets', 'Exchange rates'),
    'rates': ('markets', 'Interest rates and inflation'),
    'cpi': ('markets', 'Consumer prices'),
    'price_history': ('markets', 'Price history'),
    'power': ('markets', 'Power prices and reservoirs'),
    'normpris_brent': ('markets', 'Norm prices'),
    'normpris_diff': ('markets', None),
    'ssb_petroleum': ('markets', 'SSB petroleum statistics'),
    'dcs': ('shelves', 'Denmark (ENS)'),
    'dcs_outlines': ('shelves', None),
    'ukcs': ('shelves', 'UK (NSTA)'),
    'ukcs_licences': ('shelves', 'UK licences'),
    'ukcs_outlines': ('shelves', None),
    'canada_nl': ('shelves', 'Canada · Newfoundland and Labrador (C-NLOER)'),
    'canada_nl_outlines': ('shelves', None),
    'gom': ('shelves', 'US Gulf of Mexico (BOEM)'),
    'gom_prod': ('shelves', None),
    'gom_wells': ('shelves', None),
    'brazil': ('shelves', 'Brazil (ANP)'),
    'brazil_prod': ('shelves', None),
    'brazil_expl': ('shelves', None),
    'brazil_wells': ('shelves', None),
    'brazil_incidents': ('shelves', None),
    'brazil_outlines': ('shelves', None),
    'angola': ('shelves', 'Angola (ANPG)'),
    'angola_geo': ('shelves', None),
    'angola_fields': ('edits', 'Angola fields (Equinor)'),
    'angola_history': ('edits', 'Angola block history'),
    'hubs': ('edits', 'Hub settings'),
    'hub_connections': ('edits', 'Hub connections'),
    'exploration_name_overrides': ('edits', 'Well names'),
    'exploration_planned_manual': ('edits', 'Company drilling plans'),
    'company_feeds': ('edits', 'Company news feeds'),
    'shipping_fields': ('edits', 'Shipping fields'),
    'tanker_specs': ('edits', 'Tanker specifications'),
    'rig_ais': ('edits', 'Rig AIS links'),
    'havtil_aliases': ('edits', 'Havtil aliases'),
    'gassco_aliases': ('edits', 'Gassco aliases'),
    'canada_nl_manual': ('edits', 'Canada additions'),
    'beta_gate': (None, None),
    'coastline': (None, None),
    'canada_nl_coast': (None, None),
    'gom_coast': (None, None),
    'brazil_coast': (None, None),
    'angola_coast': (None, None),
    'brazil_aliases': ('edits', 'Brazil field links'),
    'update_log': (None, None),
    'crude_assays': ('markets', 'Crude oil assays'),
    'blends': ('edits', 'Blend mapping'),
    'sodir_forecasts': ('ncs', 'Sodir forecasts'),
    'baa': ('ncs', 'Business arrangement areas (register)'),
    'ukcs_companies': ('shelves', 'UK companies'),
}
# Rutinekilder: telles som antall kjøringer i stedet for egne linjer
ROUTINE = {'tanker_obs', 'rig_now'}


# ---------------------------------------------------------------- hjelpere

def ym_txt(y, m):
    return f'{MONTHS[m - 1]} {y}'


def rows(d):
    return (d or {}).get('rows') or []


def row_keys(d, key=0):
    return {r[key] for r in rows(d) if r}


def plural(n, word, pl=None):
    return f'{n} {word if n == 1 else (pl or word + "s")}'


def names_list(ns, limit=4):
    # Ukjente navn (None) telles, men vises ikke
    unknown = sum(1 for x in ns if not x)
    ns = sorted(x for x in ns if x)
    if unknown and not ns:
        return ''
    if unknown:
        limit = min(limit, len(ns))
    if not ns:
        return ''
    rest = len(ns) - limit + unknown
    if rest <= 0:
        return ': ' + ', '.join(ns)
    return ': ' + ', '.join(ns[:limit]) + f' and {rest} more'


def latest_month(fields):
    """Siste måned med produksjon i production.json-formatet."""
    best = None
    for series in (fields or {}).values():
        for r in series:
            if r[1] and (r[4] or r[2] or r[3]) and (best is None or (r[0], r[1]) > best):
                best = (r[0], r[1])
    return best


# ---------------------------------------------------------------- oppsummeringer per fil
# Hver funksjon får (gammel, ny, navn) og returnerer teksten, eller None
# hvis endringen ikke er verdt en linje (filen er endret, men ikke innholdet
# som betyr noe).

def s_production(o, n, names):
    a, b = latest_month(o.get('fields')), latest_month(n.get('fields'))
    nf = n.get('fields') or {}
    of = o.get('fields') or {}
    changed = [names.get(k, k) for k in nf if nf[k] != of.get(k)]
    parts = []
    if b and (not a or b > a):
        parts.append(f'{ym_txt(*b)} published')
    # Revidert = en måned som fantes fra før, har fått nye tall. Årssummene
    # (måned 0) og nye måneder teller ikke.
    lim = a or (9999, 12)
    def months(series, cols):
        return [[r[c] for c in cols] for r in series if r[1] and (r[0], r[1]) <= lim]
    # Olje, gass og o.e. for seg, vann for seg (vannet ble en gang nullstilt
    # og gjenopprettet uten at produksjonen endret seg)
    rev = [names.get(k) for k in nf if k in of and months(nf[k], (0, 1, 2, 3, 4)) != months(of[k], (0, 1, 2, 3, 4))]
    wat = [names.get(k) for k in nf if k in of and months(nf[k], (0, 1, 5)) != months(of[k], (0, 1, 5))]
    if rev:
        parts.append(f'{plural(len(rev), "field")} revised' + names_list(rev))
    if wat:
        parts.append(f'produced water revised for {plural(len(wat), "field")}')
    return '; '.join(parts) or None


def s_by_field(label):
    def f(o, n, names):
        nf, of = n.get('fields') or {}, o.get('fields') or {}
        ch = [names.get(k) for k in nf if nf[k] != of.get(k)]
        return f'{plural(len(ch), "field")} updated' + names_list(ch) if ch else None
    return f


def s_rows_added(label, key=0, name_col=None, noun='row', pl=None):
    def f(o, n, names):
        old = {r[key]: r for r in rows(o)}
        new = {r[key]: r for r in rows(n)}
        added = [k for k in new if k not in old]
        changed = [k for k in new if k in old and new[k] != old[k]]
        parts = []
        if added:
            parts.append(f'{plural(len(added), "new " + noun, "new " + (pl or noun + "s"))}' +
                         (names_list([str(new[k][name_col]) for k in added]) if name_col is not None else ''))
        if changed:
            parts.append(f'{plural(len(changed), noun, pl)} changed')
        return '; '.join(parts) or None
    return f


def s_transfers(o, n, names):
    d = len(rows(n)) - len(rows(o))
    return f'{plural(d, "new transfer")}' if d > 0 else ('register revised' if rows(n) != rows(o) else None)


def s_items(key_fields, noun):
    def f(o, n, names):
        def keys(d):
            return {tuple(i.get(k) for k in key_fields) for i in (d or {}).get('items') or []}
        new = keys(n) - keys(o)
        return plural(len(new), 'new ' + noun, 'new ' + noun + 's') if new else None
    return f


def s_havtil(o, n, names):
    ok = {i.get('id') for i in o.get('items') or []}
    new = [i for i in n.get('items') or [] if i.get('id') not in ok]
    if not new:
        return None
    kinds = collections.Counter((i.get('kind') or 'case') for i in new)
    return plural(len(new), 'new case') + ' (' + ', '.join(f'{v} {k}' for k, v in kinds.most_common()) + ')'


def s_gassco(o, n, names):
    oe, ne = o.get('events') or {}, n.get('events') or {}
    new = [k for k in ne if k not in oe]
    rev = [k for k in ne if k in oe and ne[k] != oe[k]]
    parts = []
    if new:
        parts.append(plural(len(new), 'new event'))
    if rev:
        parts.append(plural(len(rev), 'revised event'))
    return ', '.join(parts) or None


def s_changes(o, n, names):
    ok = {e.get('id') for e in o.get('events') or []}
    new = [e for e in n.get('events') or [] if e.get('id') not in ok]
    if not new:
        return None
    label = {'plan': 'planned well', 'spud': 'well spudded', 'done': 'well completed', 'discovery': 'discovery',
             'cancelled': 'well cancelled', 'rig': 'rig move', 'havtil': 'Havtil case'}
    c = collections.Counter(e.get('t') for e in new)
    return ', '.join(plural(v, label.get(k, k), label.get(k, k).replace('well ', 'wells ') if k in ('spud', 'done', 'cancelled') else None)
                     for k, v in c.most_common())


def s_shipping(o, n, names):
    def visits(d):
        return sum(len(f.get('visits') or []) for f in (d.get('fields') or {}).values())
    dv = visits(n) - visits(o)
    return plural(dv, 'new call') if dv > 0 else None


def s_dated_rows(label, col=0):
    def f(o, n, names):
        a = max((r[col] for r in rows(o)), default=None)
        b = max((r[col] for r in rows(n)), default=None)
        if b and b != a:
            return f'through {b}'
        return None
    return f


def s_fx(o, n, names):
    a, b = (o.get('dates') or [None])[-1], (n.get('dates') or [None])[-1]
    return f'through {b}' if b and b != a else None


def s_period(key, word='through'):
    def f(o, n, names):
        a, b = o.get(key), n.get(key)
        return f'{word} {b}' if b and b != a else None
    return f


def s_dcs(o, n, names):
    def last(d):
        m = ((d.get('production') or {}).get('monthly') or {})
        best = None
        for rows_ in m.values():
            for r in rows_:
                if r and r[0] and (best is None or r[0] > best):
                    best = r[0]
        return best
    a, b = last(o), last(n)
    return f'production through {b}' if b and b != a else None


def s_gom(o, n, names):
    a, b = (o.get('months') or [None, None])[1], (n.get('months') or [None, None])[1]
    if b and b != a:
        return f'production through {ym_txt(int(b[:4]), int(b[4:]))}'
    return None


def s_brazil(o, n, names):
    a, b = (o.get('months') or [None, None])[1], (n.get('months') or [None, None])[1]
    out = []
    if b and b != a:
        out.append(f'production through {ym_txt(int(b[:4]), int(b[4:]))}')
    ea, eb = (o.get('expl') or {}).get('wellsAsOf'), (n.get('expl') or {}).get('wellsAsOf')
    if eb and eb != ea:
        out.append(f'exploration panel {eb}')
    ra, rb = (o.get('reserves') or {}).get('pdfYear'), (n.get('reserves') or {}).get('pdfYear')
    if rb and rb != ra:
        out.append(f'reserves bulletin {rb}')
    return '; '.join(out) or None


def s_canada(o, n, names):
    def last(d):
        best = None
        for f in d.get('fields') or []:
            y, m = map(int, f['start'].split('-'))
            k = len(next(iter(f['prod'].values()))[0]) - 1
            t = y * 12 + m - 1 + k
            best = t if best is None or t > best else best
        return best
    a, b = last(o), last(n)
    if b and b != a:
        return f'production through {ym_txt(b // 12, b % 12 + 1)}'
    if [f.get('reserves') for f in o.get('fields') or []] != [f.get('reserves') for f in n.get('fields') or []]:
        return 'reserve estimates updated'
    return None


def s_company_news(o, n, names):
    ok = {i.get('u') for i in o.get('items') or []}
    new = [i for i in n.get('items') or [] if i.get('u') not in ok]
    return plural(len(new), 'new item') if new else None


def s_steo(o, n, names):
    a, b = o.get('release'), n.get('release')
    return f'release {b}' if b and b != a else None


def s_steo_vintages(o, n, names):
    ok = {v.get('release') for v in o.get('vintages') or []}
    new = sorted(v.get('release') for v in n.get('vintages') or [] if v.get('release') not in ok)
    return f'release {", ".join(new)} archived' if new else None


# ---------------------------------------------------------------- detaljer per fil
# Hver funksjon får (gammel, ny, ctx) og returnerer en liste med korte
# faktasetninger. ctx: names (felt-id → navn), licences (id → navn).

def fnum(v, nd=1):
    if v is None:
        return 'n/a'
    return f'{v:,.{nd}f}'


def days_in(y, m):
    import calendar
    return calendar.monthrange(y, m)[1]


def kboed(msm3, y, m):
    """Millioner Sm³ i måneden → tusen fat per dag."""
    return msm3 * 1e6 * BBL_PER_SM3 / days_in(y, m) / 1000


def d_production(o, n, ctx):
    names = ctx['names']
    nf, of = n.get('fields') or {}, o.get('fields') or {}
    a, b = latest_month(of), latest_month(nf)
    out = []
    if b and (not a or b > a):
        y, m = b
        tot = [0.0, 0.0, 0.0]
        per = []
        for k, series in nf.items():
            for r in series:
                if r[0] == y and r[1] == m:
                    tot = [tot[0] + (r[2] or 0), tot[1] + (r[3] or 0), tot[2] + (r[4] or 0)]
                    if r[4]:
                        per.append((r[4], names.get(k, k), r[2] or 0, r[3] or 0))
        out.append(f'NCS total {ym_txt(y, m)}: {fnum(kboed(tot[0], y, m), 0)} kbbl/d oil and condensate, '
                   f'{fnum(tot[1] * 1000 / days_in(y, m), 1)} MSm3/d gas, {fnum(kboed(tot[2], y, m), 0)} kboe/d in total '
                   f'({len(per)} producing fields)')
        # Forrige måned til sammenligning
        py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
        prev = sum((r[4] or 0) for s_ in nf.values() for r in s_ if r[0] == py and r[1] == pm)
        if prev:
            out.append(f'Total change from {ym_txt(py, pm)}: {fnum(kboed(tot[2], y, m) - kboed(prev, py, pm), 0)} kboe/d')
        for oe, nm, oil, gas in sorted(per, reverse=True)[:12]:
            out.append(f'{nm} {ym_txt(y, m)}: {fnum(kboed(oe, y, m), 0)} kboe/d ({fnum(kboed(oil, y, m), 0)} kbbl/d liquids)')
    lim = a or (9999, 12)
    for k in nf:
        if k not in of:
            continue
        old = {(r[0], r[1]): r for r in of[k] if r[1]}
        for r in nf[k]:
            if not r[1] or (r[0], r[1]) > lim:
                continue
            q = old.get((r[0], r[1]))
            if q and q[4] != r[4] and abs((r[4] or 0) - (q[4] or 0)) > 1e-6:
                out.append(f'{names.get(k, k)} {ym_txt(r[0], r[1])} revised: {fnum(kboed(q[4] or 0, r[0], r[1]), 1)} → '
                           f'{fnum(kboed(r[4] or 0, r[0], r[1]), 1)} kboe/d')
    return out


def d_reserves_field(o, n, ctx):
    cols = n.get('columns') or []
    i = {c: k for k, c in enumerate(cols)}
    out = []
    for k, rws in (n.get('fields') or {}).items():
        old = (o.get('fields') or {}).get(k)
        if rws == old or not rws:
            continue
        r = rws[-1]
        q = old[-1] if old else None
        nm = ctx['names'].get(k, k)
        rem = r[i['fldRemainingOE']] if 'fldRemainingOE' in i else None
        if q and q[0] == r[0]:
            out.append(f'{nm}: remaining reserves (end-{r[0]}) revised {fnum(q[i["fldRemainingOE"]], 2)} → {fnum(rem, 2)} MSm3 o.e.')
        else:
            out.append(f'{nm}: new estimate for end-{r[0]}, remaining {fnum(rem, 2)} MSm3 o.e.'
                       + (f' (end-{q[0]}: {fnum(q[i["fldRemainingOE"]], 2)})' if q else ''))
    return out


def d_rows(name_col, show, watch=None, key=0):
    """Nye rader med utvalgte kolonner, og endrede rader med hvilke kolonner som er endret."""
    def f(o, n, ctx):
        cols = n.get('columns') or []
        ci = {c: k for k, c in enumerate(cols)}
        old = {r[key]: r for r in rows(o)}
        out = []
        for r in rows(n):
            q = old.get(r[key])
            nm = r[ci[name_col]] if name_col in ci else r[key]
            if q is None:
                det = ', '.join(f'{c}: {r[ci[c]]}' for c in show if c in ci and r[ci[c]] not in (None, '', 0))
                out.append(f'New: {nm}' + (f' ({det})' if det else ''))
            elif q != r:
                ch = [f'{c} {q[ci[c]] if q[ci[c]] not in (None, "") else "–"} → {r[ci[c]] if r[ci[c]] not in (None, "") else "–"}'
                      for c in (watch or cols) if c in ci and ci[c] < len(q) and q[ci[c]] != r[ci[c]]]
                if ch:
                    out.append(f'{nm}: ' + '; '.join(ch))
                else:
                    other = [c for c in cols if ci[c] < len(q) and q[ci[c]] != r[ci[c]]]
                    if other:
                        out.append(f'{nm}: {", ".join(other)} updated')
        return out
    return f


def d_changes(o, n, ctx):
    ok = {e.get('id') for e in o.get('events') or []}
    out = []
    for e in n.get('events') or []:
        if e.get('id') in ok:
            continue
        t = e.get('t')
        if t == 'rig':
            out.append(f'Rig {e.get("r")} moved to {e.get("pp") or e.get("p")}' + (f' ({e.get("pk")})' if e.get('pk') else ''))
        elif t == 'havtil':
            out.append(f'Havtil {e.get("k")}: {e.get("n")}' + (f', {e.get("nc")} nonconformities' if e.get('nc') else ''))
        elif t == 'plan':
            out.append(f'Planned well {e.get("n")} ({e.get("op")}), rig {e.get("rig") or "n/a"}, planned start {e.get("planned") or "n/a"}')
        elif t == 'spud':
            out.append(f'Well {e.get("n")} spudded' + (f' ({e.get("op")})' if e.get('op') else '') + (f', rig {e.get("rig")}' if e.get('rig') else ''))
        elif t in ('done', 'discovery'):
            out.append(f'Well {e.get("n")} completed' + (f': {e.get("c")}' if e.get('c') else '') + (' – discovery' if t == 'discovery' else ''))
        elif t == 'postponed':
            out.append(f'Well {e.get("n")} postponed to {e.get("planned")}')
        elif t == 'cancelled':
            out.append(f'Planned well {e.get("n")} removed from Sodir data')
        else:
            out.append(f'{t}: {e.get("n") or e.get("id")}')
    return out


def d_havtil(o, n, ctx):
    ok = {i.get('id') for i in o.get('items') or []}
    out = []
    for i in n.get('items') or []:
        if i.get('id') in ok:
            continue
        extra = [x for x in (f'{i["nonconf"]} nonconformities' if i.get('nonconf') else '',
                             f'{i["improve"]} improvement points' if i.get('improve') else '') if x]
        out.append(f'{i.get("date")} {i.get("kind") or "case"}: {i.get("title")}'
                   + (f' ({", ".join(i.get("companies") or [])}; {", ".join(i.get("facilities") or [])})' if i.get('companies') else '')
                   + (f' – {"; ".join(extra)}' if extra else '') + (f'. {i.get("desc")}' if i.get('desc') else ''))
    return out


def d_gassco(o, n, ctx):
    oe, ne = o.get('events') or {}, n.get('events') or {}
    out = []
    for k, e in ne.items():
        if oe.get(k) == e:
            continue
        what = 'New' if k not in oe else 'Revised'
        out.append(f'{what} {(e.get("type") or "").lower()} event at {e.get("asset")} ({e.get("status")}): '
                   f'{e.get("start", "")[:16].replace("T", " ")} – {e.get("stop", "")[:16].replace("T", " ")}, '
                   f'{fnum(e.get("unavail"))} of {fnum(e.get("tech"))} {e.get("unit") or ""} unavailable')
    return out


def d_items(key, title, summary=None, link=None, date=None, extra=None):
    def f(o, n, ctx):
        ok = {i.get(key) for i in o.get('items') or []}
        out, stale = [], 0
        # Saker datert mer enn to uker før dagen er gamle saker som er indeksert
        # på nytt (typisk Google News), ikke nyheter; de telles bare.
        cutoff = (dt.date.fromisoformat(ctx['day']) - dt.timedelta(days=14)).isoformat() if ctx.get('day') else ''
        for i in n.get('items') or []:
            if i.get(key) in ok:
                continue
            if date and cutoff and (i.get(date) or '9') < cutoff:
                stale += 1
                continue
            src = extra(i, n) if extra else ''
            out.append(f'{(i.get(date) + " ") if date and i.get(date) else ""}{src}{i.get(title)}'
                       + (f' – {i.get(summary)}' if summary and i.get(summary) else '')
                       + (f' <{i.get(link)}>' if link and i.get(link) else ''))
        if stale:
            out.append(f'{plural(stale, "older item")} (dated more than two weeks back) newly picked up, not listed')
        return out
    return f


def news_source(i, n):
    f = (n.get('feeds') or {}).get(i.get('f')) or {}
    c = f.get('company')
    return f'[{c}] ' if c else ''


def d_transfers(o, n, ctx):
    old = {tuple(r) for r in rows(o)}
    cmp_, kinds = n.get('companies') or [], n.get('kinds') or []
    out = []
    for r in rows(n):
        if tuple(r) in old:
            continue
        day, lic, sel, buy, share, kind = r
        d = (dt.date(1970, 1, 1) + dt.timedelta(days=day)).isoformat()
        nm = lambda k: cmp_[k] if isinstance(k, int) and 0 <= k < len(cmp_) else str(k)
        out.append(f'{d} PL {ctx["licences"].get(lic, lic)}: {fnum(share, 3).rstrip("0").rstrip(".")} % from {nm(sel)} to {nm(buy)}'
                   f' ({(kinds[kind] if isinstance(kind, int) and kind < len(kinds) else kind).lower()})')
    return out


def d_series(unit, col=1):
    def f(o, n, ctx):
        old = {r[0] for r in rows(o)}
        new = [r for r in rows(n) if r[0] not in old]
        if not new:
            return []
        vals = [r[col] for r in new if r[col] is not None]
        out = [f'{r[0]}: {fnum(r[col], 2)} {unit}' for r in new[-10:]]
        if len(new) > 10:
            out.insert(0, f'{len(new)} new values {new[0][0]} – {new[-1][0]}, range {fnum(min(vals), 2)}–{fnum(max(vals), 2)} {unit}')
        prev = [r for r in rows(n) if r[0] < new[0][0]]
        if prev and vals:
            out.append(f'Change from {prev[-1][0]} ({fnum(prev[-1][col], 2)}) to {new[-1][0]}: {fnum(new[-1][col] - prev[-1][col], 2)} {unit}')
        return out
    return f


def d_fx(o, n, ctx):
    od, nd = set(o.get('dates') or []), n.get('dates') or []
    if not nd or nd[-1] in od:
        return []
    i = len(nd) - 1
    return [f'NOK per unit on {nd[-1]}: ' + ', '.join(f'{c} {fnum((n.get("rates") or {}).get(c, [None])[i], 4)}'
                                                  for c in n.get('currencies') or [])]


def d_gas_prices(o, n, ctx):
    cols = n.get('columns') or []
    ci = {c: k for k, c in enumerate(cols)}
    old = {r[0]: r for r in rows(o)}
    out = []
    for r in rows(n):
        if old.get(r[0]) == r:
            continue
        nok = r[ci['export_nok']] / r[ci['export_sm3']] if r[ci['export_nok']] and r[ci['export_sm3']] else None
        out.append(f'{r[0]}: Norwegian export {fnum(nok, 2)} NOK/Sm3, Europe (IMF) {fnum(r[ci["eu_usd_mmbtu"]], 2)} USD/MMBtu, '
                   f'Henry Hub {fnum(r[ci["hh_usd_mmbtu"]], 2)} USD/MMBtu' + (' (revised)' if r[0] in old else ''))
    return out


def d_rig_contracts(o, n, ctx):
    key = lambda c: (c.get('rig'), c.get('client'), c.get('start'))
    old = {key(c): c for c in o.get('contracts') or []}
    out = []
    for c in n.get('contracts') or []:
        q = old.get(key(c))
        if q == c:
            continue
        what = 'New contract' if q is None else 'Contract changed'
        out.append(f'{what}: {c.get("rig")} for {c.get("client")} ({c.get("kind")}), {c.get("start")} – {c.get("end")}'
                   + (f', day rate {c.get("dayrateText") or c.get("dayrate")}' if c.get('dayrate') or c.get('dayrateText') else ''))
    return out


def d_shipping(o, n, ctx):
    out = []
    for k, f in (n.get('fields') or {}).items():
        of = (o.get('fields') or {}).get(k) or {}
        cols = f.get('columns') or []
        ci = {c: j for j, c in enumerate(cols)}
        # Et anløp kan få justert starttid når AIS-sporet oppdateres; samme skip
        # med start innen ett døgn regnes som samme anløp.
        def ts(x):
            try:
                return dt.datetime.fromisoformat(x.replace('Z', '+00:00')).timestamp()
            except (AttributeError, ValueError):
                return None
        old = collections.defaultdict(list)
        for v in of.get('visits') or []:
            old[v[0]].append(ts(v[1]))
        new = [v for v in f.get('visits') or []
               if not any(t is not None and ts(v[1]) is not None and abs(t - ts(v[1])) < 86400 for t in old.get(v[0], []))]
        if not new:
            continue
        cargo = sum(v[ci['cargoBbl']] or 0 for v in new) if 'cargoBbl' in ci else 0
        dest = sorted({v[ci['to']][3] for v in new if 'to' in ci and isinstance(v[ci['to']], list) and len(v[ci['to']]) > 3 and v[ci['to']][3]})
        out.append(f'{f.get("name") or ctx["names"].get(k, k)}: {plural(len(new), "new tanker call")}'
                   + (f', about {fnum(cargo / 1e6, 2)} million bbl loaded (draught-based estimate)' if cargo else '')
                   + (f'; heading to {", ".join(dest)}' if dest else ''))
    return out


def d_descriptions(o, n, ctx):
    old = {(r[0], r[1]): r[2] for r in rows(o)}
    ch = collections.defaultdict(list)
    for r in rows(n):
        if old.get((r[0], r[1])) != r[2]:
            ch[r[0]].append(r[1] + ('' if (r[0], r[1]) in old else ' (new)'))
    # Overskriftene kan ha lange forklaringer i parentes («Status (No longer
    # maintained by Sodir …)»); de kuttes. Mange felt samtidig er nesten alltid
    # en omlegging hos Sodir, så de slås sammen til én linje.
    short = lambda h: h.split(' (')[0]
    if len(ch) > 8:
        heads = sorted({short(h) for v in ch.values() for h in v})
        nm = sorted(ctx['names'].get(str(k), str(k)) for k in ch)
        return [f'Description texts revised for {len(ch)} fields ({", ".join(nm[:6])} and others); sections: {", ".join(heads)}']
    return [f'{ctx["names"].get(str(k), k)}: {", ".join(sorted({short(h) for h in v}))} text changed' for k, v in ch.items()]


DETAILS = {
    'shipping': d_shipping,
    'field_descriptions': d_descriptions,
    'production': d_production,
    'reserves_field': d_reserves_field,
    'exploration_wells': d_rows('name', ['purpose', 'status', 'content', 'operator', 'rig', 'planned', 'entry', 'licence'],
                                ['purpose', 'status', 'content', 'operator', 'rig', 'entry', 'completion', 'planned', 'discovery', 'td']),
    'rig_wells': d_rows('name', ['field', 'purpose', 'operator', 'rig', 'entry'], ['purpose', 'rig', 'entry', 'completion']),
    'discoveries': d_rows('name', ['status', 'hc', 'year', 'operator', 'area'], ['status', 'hc', 'field', 'operator', 'inclIn']),
    'licences': d_rows('name', ['area', 'phase', 'operator', 'round'], ['phase', 'validTo', 'operator', 'areaKm2']),
    'fields': d_rows('name', []),
    'facilities': d_rows('name', ['kind', 'phase', 'operator'], ['kind', 'phase', 'operator', 'startup']),
    'changes': d_changes,
    'havtil': d_havtil,
    'gassco_events': d_gassco,
    'sodir_press': d_items('u', 't', 's', 'u', 'd'),
    'sodir_news': d_items('link', 'title', 'description', 'link', 'date'),
    'company_news': d_items('u', 't', None, 'u', 'd', news_source),
    'licence_transfers': d_transfers,
    'brent_eia': d_series('USD/bbl'),
    'normpris_brent': d_series('USD/bbl'),
    'ttf_daily': d_series('EUR/MWh'),
    'fx': d_fx,
    'gas_prices': d_gas_prices,
    'rig_contracts': d_rig_contracts,
}


def details(name, old, new, ctx):
    fn = DETAILS.get(name)
    if not fn or old is None:
        return []
    try:
        facts = fn(old, new, ctx) or []
    except Exception as e:                       # detaljene skal aldri stoppe loggen
        print(f'  {name}: details failed ({e})', file=sys.stderr)
        return []
    if len(facts) > MAX_FACTS:
        facts = facts[:MAX_FACTS] + [f'… and {len(facts) - MAX_FACTS} more']
    return facts


SUMMARY = {
    'production': s_production,
    'reserves_field': s_by_field('Field reserves'),
    'discoveries': s_rows_added('Discoveries', 0, 1, 'discovery', 'discoveries'),
    'licences': s_rows_added('Licences', 0, 1, 'licence'),
    'licence_transfers': s_transfers,
    'exploration_wells': s_rows_added('Exploration wells', 0, 1, 'well'),
    'rig_wells': s_rows_added('Development wells', 0, 1, 'well'),
    'facilities': s_rows_added('Facilities', 0, 1, 'facility', 'facilities'),
    'fields': s_rows_added('Fields', 0, 1, 'field'),
    'changes': s_changes,
    'havtil': s_havtil,
    'gassco_events': s_gassco,
    'shipping': s_shipping,
    'sodir_press': s_items(('u',), 'release'),
    'sodir_news': s_items(('link',), 'item'),
    'company_news': s_company_news,
    'brent_eia': s_dated_rows('Brent'),
    'normpris_brent': s_dated_rows('Norm prices'),
    'gas_prices': s_dated_rows('Gas prices'),
    'ttf_daily': s_dated_rows('TTF'),
    'fx': s_fx,
    'ukcs': s_period('lastPeriod', 'production through'),
    'dcs': s_dcs,
    'gom': s_gom,
    'brazil': s_brazil,
    'angola': s_gom,
    'canada_nl': s_canada,
    'steo': s_steo,
    'steo_vintages': s_steo_vintages,
}


def field_names(data):
    try:
        return {str(r[0]): r[1].title() for r in rows(data)}
    except Exception:
        return {}


def summarise(name, old, new, names):
    """Én logglinje for en endret fil: {k, s, l, t} eller None."""
    sec, label = FILES.get(name, ('other', name))
    if sec is None or label is None:
        return None
    if old is None:
        return {'k': name, 's': sec, 'l': label, 't': 'added'}
    fn = SUMMARY.get(name)
    txt = None
    if fn:
        try:
            txt = fn(old, new, names)
        except Exception as e:                       # en uventet struktur skal ikke stoppe loggen
            txt = 'updated'
            print(f'  {name}: summary failed ({e})', file=sys.stderr)
        if txt is None:
            # Filen er endret, men ikke noe av det oppsummeringen ser etter
            # (tidsstempel, rekkefølge). Rutinefiler logges likevel.
            if name not in ROUTINE:
                return None
            txt = 'updated'
    else:
        txt = 'updated'
    return {'k': name, 's': sec, 'l': label, 't': txt}


# ---------------------------------------------------------------- git

def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True, check=False).stdout


def load_at(rev, path):
    if rev is None:
        try:
            with open(path, encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None
    out = subprocess.run(['git', 'show', f'{rev}:{path}'], capture_output=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def entries_between(old_rev, new_rev, files, day=None):
    """Logglinjer for filene som er endret mellom to versjoner (new_rev None =
    arbeidskopien)."""
    names = field_names(load_at(new_rev, 'data/fields.json') or load_at(old_rev, 'data/fields.json'))
    lic = load_at(new_rev, 'data/licences.json') or {}
    ctx = {'names': names, 'licences': {r[0]: r[1] for r in rows(lic)}, 'day': day}
    out = []
    for path in sorted(files):
        # Bare filene rett under data/; undermapper (gom_company/, equinor-raw/ …)
        # er kildemateriale eller deler av en annen kilde.
        if os.path.dirname(path) != 'data':
            continue
        name = os.path.basename(path)[:-5]
        new = load_at(new_rev, path)
        if new is None:
            continue
        old = load_at(old_rev, path) if old_rev else None
        if old == new:
            continue
        e = summarise(name, old, new, names)
        if e:
            f = details(name, old, new, ctx)
            if f:
                e['f'] = f
            out.append(e)
    return out


def changed_files_worktree():
    out = git('status', '--porcelain', '--', 'data/')
    files = []
    for line in out.splitlines():
        p = line[3:].strip()
        if p.endswith('.json') and p.startswith('data/'):
            files.append(p)
        elif p.endswith('/') and p.startswith('data/'):
            pass
    return files


# ---------------------------------------------------------------- loggen

def load_log():
    try:
        with open(OUT, encoding='utf-8') as fh:
            d = json.load(fh)
            if isinstance(d.get('days'), list):
                return d
    except (OSError, ValueError):
        pass
    return {'days': []}


def rev_before(when_iso):
    """Siste commit før tidspunktet (på HEAD). Utdyper en grunn klone ved behov."""
    for attempt in range(2):
        r = git('rev-list', '-1', f'--before={when_iso}', 'HEAD').strip()
        if r:
            return r
        if git('rev-parse', '--is-shallow-repository').strip() == 'true' and attempt == 0:
            day = when_iso[:10]
            since = (dt.date.fromisoformat(day) - dt.timedelta(days=3)).isoformat()
            subprocess.run(['git', 'fetch', '--quiet', f'--shallow-since={since}', 'origin'], check=False)
    return None


def day_entries(day, end_rev, worktree=False):
    """Linjene for én dag: forskjellen fra slutten av dagen før til slutten av
    dagen (end_rev, eller arbeidskopien). Tidspunktene er committene som
    endret hver fil i løpet av dagen."""
    start = f'{day}T00:00:00Z'
    base = rev_before(start)
    end = end_rev or 'HEAD'
    files = set()
    if base:
        files |= {p for p in git('diff', '--name-only', base, end, '--', 'data/').splitlines() if p.endswith('.json')}
    if worktree:
        files |= set(changed_files_worktree())
    entries = entries_between(base, None if worktree else end_rev, files, day)
    times = collections.defaultdict(list)
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    t = None
    for line in git('log', f'--since={start}', f'--until={nxt}T00:00:00Z', '--reverse', '--format=@@%cI', '--name-only', end, '--', 'data/').splitlines():
        if line.startswith('@@'):
            t = dt.datetime.fromisoformat(line[2:]).astimezone(dt.timezone.utc).strftime('%H:%M')
        elif line.strip().endswith('.json') and t:
            times[os.path.basename(line.strip())[:-5]].append(t)
    now = dt.datetime.now(dt.timezone.utc).strftime('%H:%M')
    wt = {os.path.basename(p)[:-5] for p in changed_files_worktree()} if worktree else set()
    for e in entries:
        at = sorted(set(times.get(e['k'], []) + ([now] if e['k'] in wt else [])))
        e['at'] = at
        e['runs'] = max(1, len(at))
    return entries


def write_details(day, entries):
    """data/update_details/<dag>.json: linjene med faktasetningene, og index.json."""
    os.makedirs(DETAIL_DIR, exist_ok=True)
    sec = dict(SECTIONS)
    doc = {'date': day, 'note': 'Changes to the NCS Insight data on this day (UTC), per source. '
                                't is the short summary shown on the updates page; facts are the details.',
           'sources': [{'section': sec.get(e['s'], e['s']), 'source': e['l'], 'key': e['k'], 'summary': e['t'],
                        'times_utc': e.get('at', []), 'facts': e.get('f', [])} for e in entries]}
    path = os.path.join(DETAIL_DIR, f'{day}.json')
    txt = json.dumps(doc, ensure_ascii=False, indent=1) + '\n'
    try:
        with open(path, encoding='utf-8') as fh:
            if fh.read() == txt:
                return
    except OSError:
        pass
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(txt)


def prune_details(today):
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=KEEP_DETAIL_DAYS)).isoformat()
    if not os.path.isdir(DETAIL_DIR):
        return
    days = []
    for fn in sorted(os.listdir(DETAIL_DIR)):
        if len(fn) == 15 and fn.endswith('.json') and fn[:4].isdigit():
            if fn[:10] < cutoff:
                os.remove(os.path.join(DETAIL_DIR, fn))
            else:
                days.append(fn[:10])
    idx = {'days': sorted(days, reverse=True), 'path': 'data/update_details/<date>.json'}
    path = os.path.join(DETAIL_DIR, 'index.json')
    txt = json.dumps(idx, indent=1) + '\n'
    try:
        with open(path, encoding='utf-8') as fh:
            if fh.read() == txt:
                return
    except OSError:
        pass
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(txt)


def put_day(log, day, entries):
    order = {s: i for i, (s, _) in enumerate(SECTIONS)}
    entries.sort(key=lambda i: (order.get(i['s'], 99), i['l']))
    if entries:
        write_details(day, entries)
    # Detaljene står i egne filer; update_log.json (som siden leser) holdes liten.
    entries = [{k: v for k, v in e.items() if k != 'f'} for e in entries]
    days = {d['d']: d for d in log['days']}
    if entries:
        days[day] = {'d': day, 'items': entries}
    else:
        days.pop(day, None)
    cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    log['days'] = sorted([x for x in days.values() if x['d'] >= cutoff], key=lambda x: x['d'], reverse=True)
    log['sections'] = SECTIONS
    return log


def write(log):
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(log, fh, ensure_ascii=False, separators=(',', ':'))
        fh.write('\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill', type=int, metavar='DAYS', help='rebuild the whole log from git history')
    ap.add_argument('--days', type=int, default=3, help='days to recompute in the normal run (default 3)')
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    if args.backfill:
        log = {'days': []}
        days = sorted({dt.datetime.fromisoformat(t).astimezone(dt.timezone.utc).date().isoformat()
                       for t in git('log', f'--since={args.backfill} days ago', '--format=%cI', '--', 'data/').split()})
        for day in days:
            nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
            end = rev_before(f'{nxt}T00:00:00Z')
            log = put_day(log, day, day_entries(day, end))
        print(f'Backfilled {len(log["days"])} days')
        prune_details(today)
    else:
        # Standard: regn de siste dagene på nytt fra git-historikken (og
        # arbeidskopien, hvis noe er endret der). Da blir loggen riktig også om
        # en kjøring er hoppet over eller endringer er gjort i appen.
        log = load_log()
        before = json.dumps(log.get('days'), sort_keys=True)
        for k in range(args.days - 1, -1, -1):
            day = (now.date() - dt.timedelta(days=k)).isoformat()
            if k == 0:
                entries = day_entries(day, None, worktree=True)
            else:
                nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
                entries = day_entries(day, rev_before(f'{nxt}T00:00:00Z'))
            log = put_day(log, day, entries)
            print(f'{day}: {len(entries)} source(s)')
        prune_details(today)
        if json.dumps(log.get('days'), sort_keys=True) == before and os.path.exists(OUT):
            print('No changes to the log')
            return 0                     # filen røres ikke, ingen commit
    log['updated'] = now.strftime('%Y-%m-%dT%H:%MZ')
    write(log)
    return 0


if __name__ == '__main__':
    sys.exit(main())
