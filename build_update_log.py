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


def entries_between(old_rev, new_rev, files):
    """Logglinjer for filene som er endret mellom to versjoner (new_rev None =
    arbeidskopien)."""
    names = field_names(load_at(new_rev, 'data/fields.json') or load_at(old_rev, 'data/fields.json'))
    out = []
    for path in sorted(files):
        name = os.path.basename(path)[:-5]
        new = load_at(new_rev, path)
        if new is None:
            continue
        old = load_at(old_rev, path) if old_rev else None
        if old == new:
            continue
        e = summarise(name, old, new, names)
        if e:
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
    entries = entries_between(base, None if worktree else end_rev, files)
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


def put_day(log, day, entries):
    order = {s: i for i, (s, _) in enumerate(SECTIONS)}
    entries.sort(key=lambda i: (order.get(i['s'], 99), i['l']))
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
        if json.dumps(log.get('days'), sort_keys=True) == before and os.path.exists(OUT):
            print('No changes to the log')
            return 0                     # filen røres ikke, ingen commit
    log['updated'] = now.strftime('%Y-%m-%dT%H:%MZ')
    write(log)
    return 0


if __name__ == '__main__':
    sys.exit(main())
