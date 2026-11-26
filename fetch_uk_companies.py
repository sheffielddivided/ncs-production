"""
Selskapsfakta for UKCS → Company: hvem konsernene er juridisk, hvem som eier
dem, hvor lenge de har vært på britisk sokkel og hvor mye de borer.

    data/ukcs_companies.json

Kilder (alle åpne):

  * NSTA, licence subareas by equity group holder (samme lag som
    fetch_nsta.py) — dagens juridiske enheter med Companies House-nummer
    (LICORG/OPORG/ADMORG står som «NAVN (00140141)») og konsernet de
    hører til.
  * NSTA, licence blocks history — lisenser der enheten står som (siste)
    lisensinnehaver, også tilbakeleverte, og navn NSTA har registrert på
    samme nummer. Lisensens startdato sier ikke når selskapet kom inn, så
    den brukes ikke.
  * NSTA, offshore wellbores top holes — brønnbaner konsernet er ansvarlig
    for i dag (RESWOP), med boremål, spuddato og status.
  * Companies House — selskapsprofil (status, stiftet, forretningsadresse,
    SIC-koder, tidligere navn, siste regnskap) og eiere med vesentlig kontroll
    (PSC). Hentes gjennom Cloudflare-proxyen i sheffielddivided/convert, som
    har API-nøkkelen; skriptet selv trenger ingen nøkkel. CH_PROXY kan
    overstyres med miljøvariabelen CH_PROXY.
  * GLEIF — LEI per enhet (søk på registreringsnummer) og øverste
    morselskap (ultimate parent).

Konsernnavnene lages med de samme funksjonene som i fetch_nsta.py, så de
treffer navnene i data/ukcs.json og selskapsvelgeren.

Kjøres ukentlig av .github/workflows/update-uk-companies.yml:

    pip install requests
    python fetch_uk_companies.py

NSTA-lagene er påkrevd (exit 1 uten å skrive noe hvis de feiler). Companies
House og GLEIF er ikke det: feiler et oppslag, beholdes forrige verdi for
den enheten fra filen som ligger der.
"""

import collections
import datetime as dt
import json
import os
import re
import sys
import time

from fetch_nsta import ITEM_LICENCES, get, layer_url, make_group_of, need, norm, pretty, query_all, session, split_pct, strip_regno, write_if_changed

ITEM_BLOCKS_HIST = '855237fb38bb44b2afc52a3ea4a48903'
ITEM_WELLS = 'b5752e9f56a949e3ad44a08144e621a8'
CH_PROXY = os.environ.get('CH_PROXY', 'https://companies-house-proxy.batil-kjekken.workers.dev').rstrip('/')
GLEIF = 'https://api.gleif.org/api/v1'
OUT = 'ukcs_companies.json'
# Companies House tillater 600 kall per 5 min; vi holder oss godt under.
CH_PAUSE = 0.35
GLEIF_PAUSE = 1.1
MIN_ENTITIES = 80
MIN_WELLS = 10_000
MIN_HIST = 5_000

# «SHELL U.K. LIMITED (00140141), ADURA OPERATIONS LIMITED (16172712)»
# Nummeret er Companies House-nummeret: åtte tegn, eventuelt med prefiks
# (SC Skottland, NI Nord-Irland, FC utenlandsk selskap med britisk filial, OC LLP).
CRN = re.compile(r'\s*([^,]+?(?:\([^)]*[A-Za-z][^)]*\)[^,(]*?)*)\s*\(((?:[A-Z]{2})?\d{5,8})\)')


def entities_of(text):
    return [(norm(m.group(1)), m.group(2).zfill(8) if m.group(2).isdigit() else m.group(2))
            for m in CRN.finditer(str(text or ''))]


def ms_day(v):
    return dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).strftime('%Y-%m-%d') if v is not None else None


# ── NSTA: dagens enheter og konsern ─────────────────────────────────────────
def current_entities():
    print('  Lisensunderområder …')
    rows = query_all(layer_url(ITEM_LICENCES), ['OBJECTID', 'LICREF', 'LICORG', 'LICORGGRP', 'OPORG', 'ADMORG',
                                                 'EQORG', 'EQORGGRP', 'EQGRPHOLD'])
    pairs = collections.Counter()
    for f in rows:
        a = f['attributes']
        orgs, grps = split_pct(a['EQORG']), split_pct(a['EQORGGRP'])
        if len(orgs) == len(grps) and all(o[1] == g[1] for o, g in zip(orgs, grps)):
            for (o, _), (g, _) in zip(orgs, grps):
                pairs[(o, g)] += 1
    org_group = {}
    for (o, g), _ in sorted(pairs.items(), key=lambda kv: -kv[1]):
        org_group.setdefault(o, pretty(g))
    group_of = make_group_of(org_group)

    ents = {}
    for f in rows:
        a = f['attributes']
        lic = norm(a['LICREF'])
        licensees = entities_of(a['LICORG'])
        grps = [g.strip() for g in str(a['LICORGGRP'] or '').split(',')]
        for i, (name, crn) in enumerate(licensees):
            e = ents.setdefault(crn, {'name': pretty(name), 'key': name, 'group': None, 'roles': set(), 'lics': set()})
            e['roles'].add('licensee')
            e['lics'].add(lic)
            if not e['group']:
                # Konsernet fra eierparene; ellers konsernkolonnen i samme rekkefølge.
                e['group'] = org_group.get(name) or (pretty(grps[i]) if len(grps) == len(licensees) and grps[i] else None)
        for name, crn in entities_of(a['OPORG']):
            if crn in ents:
                ents[crn]['roles'].add('operator')
        for name, crn in entities_of(a['ADMORG']):
            if crn in ents:
                ents[crn]['roles'].add('administrator')
    for e in ents.values():
        e['group'] = e['group'] or group_of(e['key'])
    need(len(ents) >= MIN_ENTITIES, f'bare {len(ents)} juridiske enheter med registreringsnummer')
    print(f'    {len(rows)} rader, {len(ents)} juridiske enheter, {len({e["group"] for e in ents.values()})} konsern')
    return ents, group_of


# ── NSTA: lisenshistorikk ───────────────────────────────────────────────────
def licence_history(ents):
    print('  Lisensblokkhistorikk …')
    rows = query_all(layer_url(ITEM_BLOCKS_HIST), ['OBJECTID', 'LICREF', 'LICORG', 'LICHISNAME'])
    need(len(rows) >= MIN_HIST, f'blokkhistorikk: bare {len(rows)} rader')
    lics = collections.defaultdict(set)
    names = collections.defaultdict(set)   # tidligere navn per nummer, slik NSTA har registrert dem
    for f in rows:
        a = f['attributes']
        for name, crn in entities_of(a['LICORG']) + entities_of(a['LICHISNAME']):
            if crn not in ents:
                continue
            lics[crn].add(norm(a['LICREF']))
            names[crn].add(name)
    print(f'    {len(rows)} rader, historikk for {len(lics)} av {len(ents)} enheter')
    return lics, names


# ── NSTA: brønnbaner ─────────────────────────────────────────────────────────
WELL_INTENT = {'exploration': 'E', 'appraisal': 'A', 'development': 'D'}


def wells(ents, group_of):
    print('  Brønnbaner …')
    rows = query_all(layer_url(ITEM_WELLS), ['OBJECTID', 'RESWOP', 'RESWOPGRP', 'SPUDDATE', 'ORIGINTENT', 'WELLOPSTAT',
                                              'WELLBRSTAT', 'COUNTRYCOD'])
    need(len(rows) >= MIN_WELLS, f'brønnbaner: bare {len(rows)}')
    by_name = {e['key']: e['group'] for e in ents.values()}
    out = collections.defaultdict(lambda: {'total': 0, 'open': 0, 'spud': collections.defaultdict(lambda: [0, 0, 0])})
    for f in rows:
        a = f['attributes']
        op = norm(a['RESWOP'])
        if not op or op.startswith('NO DATA') or str(a['COUNTRYCOD'] or '').lower() not in ('', 'seaward'):
            continue
        g = by_name.get(op) or group_of(op)
        w = out[g]
        w['total'] += 1
        # «Decommissioned» (sic: kilden skriver også «Decomissioned») = ferdig plugget.
        if not str(a['WELLOPSTAT'] or '').lower().startswith('decom'):
            w['open'] += 1
        spud, kind = ms_day(a['SPUDDATE']), WELL_INTENT.get(str(a['ORIGINTENT'] or '').strip().lower())
        if spud and kind:
            w['spud'][spud[:4]]['EAD'.index(kind)] += 1
    print(f'    {len(rows)} brønnbaner, {len(out)} ansvarlige konsern')
    return {g: {'total': w['total'], 'open': w['open'], 'spud': dict(sorted(w['spud'].items()))} for g, w in out.items()}


# ── Companies House ──────────────────────────────────────────────────────────
def ch(path):
    for i in range(4):
        try:
            r = session.get(CH_PROXY + path, timeout=30, headers={'Accept': 'application/json'})
        except Exception as e:
            print(f'    CH {path}: {e.__class__.__name__}')
            time.sleep(2 ** i)
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (i + 1))
            continue
        if r.ok:
            time.sleep(CH_PAUSE)
            return r.json()
        print(f'    CH {path}: HTTP {r.status_code}')
        return False
    return False


def ch_entity(crn):
    p = ch(f'/company/{crn}')
    if not p:
        return p
    addr = p.get('registered_office_address') or {}
    out = {
        'name': p.get('company_name'), 'status': p.get('company_status'), 'type': p.get('type'),
        'created': p.get('date_of_creation'), 'ceased': p.get('date_of_cessation'),
        'juris': p.get('jurisdiction'),
        'office': ', '.join(x for x in [addr.get('locality') or addr.get('address_line_2'), addr.get('postal_code'),
                                        addr.get('country')] if x),
        'sic': p.get('sic_codes') or [],
        'prev': [[x.get('name'), x.get('effective_from'), x.get('ceased_on')] for x in p.get('previous_company_names') or []],
        'accounts': ((p.get('accounts') or {}).get('last_accounts') or {}).get('made_up_to'),
        'accountsType': ((p.get('accounts') or {}).get('last_accounts') or {}).get('type'),
        'insolvency': bool(p.get('has_insolvency_history')),
    }
    psc = ch(f'/company/{crn}/persons-with-significant-control')
    if psc:
        out['psc'] = [{'name': x.get('name'), 'kind': re.sub(r'-person-with-significant-control$', '', x.get('kind', '')),
                       'country': ((x.get('identification') or {}).get('country_registered') or (x.get('address') or {}).get('country')),
                       'regNo': (x.get('identification') or {}).get('registration_number'),
                       'control': x.get('natures_of_control') or []}
                      for x in psc.get('items') or [] if not x.get('ceased_on') and not x.get('ceased')]
    elif psc is False:
        return False
    return out


# ── GLEIF ────────────────────────────────────────────────────────────────────
def gleif_get(path, params=None):
    for i in range(4):
        try:
            r = session.get(GLEIF + path, params=params, timeout=60, headers={'Accept': 'application/vnd.api+json'})
            time.sleep(GLEIF_PAUSE)
            if r.status_code == 404:
                return None
            if r.ok:
                return r.json()
        except Exception as e:
            print(f'    GLEIF {path}: {e.__class__.__name__}')
        time.sleep(2 ** (i + 1))
    return False


def gleif(crns):
    """Registreringsnummer → {lei, status, parent: [navn, land, lei]}."""
    out = {}
    plain = [c for c in crns if not c.startswith('FC')]
    for i in range(0, len(plain), 50):
        d = gleif_get('/lei-records', {'filter[entity.registeredAs]': ','.join(plain[i:i + 50]),
                                       'filter[entity.legalAddress.country]': 'GB', 'page[size]': 200})
        if not d:
            return None
        for r in d.get('data', []):
            e = r['attributes']['entity']
            no = e.get('registeredAs')
            # Flere LEI-er på samme nummer: den utstedte (ISSUED) og aktive vinner.
            score = (r['attributes']['registration']['status'] == 'ISSUED', e.get('status') == 'ACTIVE')
            if no in out and out[no]['_score'] >= score:
                continue
            out[no] = {'lei': r['id'], 'leiStatus': r['attributes']['registration']['status'], '_score': score}
    for no, x in out.items():
        x.pop('_score')
        p = gleif_get(f'/lei-records/{x["lei"]}/ultimate-parent')
        if p and p.get('data'):
            e = p['data']['attributes']['entity']
            x['parent'] = [e['legalName']['name'], e['legalAddress'].get('country'), p['data']['id']]
        elif p is False:
            x['parent'] = None
    return out


def main():
    print('Henter selskapsfakta for UKCS …')
    ents, group_of = current_entities()
    lics, hist_names = licence_history(ents)
    well_stats = wells(ents, group_of)

    path = os.path.join('data', OUT)
    old = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            old = json.load(fh).get('entities', {})

    print(f'  Companies House ({len(ents)} enheter, via {CH_PROXY}) …')
    ch_ok = ch_fail = 0
    chd = {}
    for crn in sorted(ents):
        x = ch_entity(crn)
        if x is False:
            ch_fail += 1
            x = (old.get(crn) or {}).get('ch')
        elif x:
            ch_ok += 1
        chd[crn] = x
    print(f'    {ch_ok} profiler, {ch_fail} feil (forrige verdi beholdt)')

    print('  GLEIF …')
    lei = gleif(sorted(ents))
    if lei is None:
        print('    GLEIF svarte ikke; forrige verdier beholdes')
        lei = {c: (old.get(c) or {}).get('lei') for c in ents if (old.get(c) or {}).get('lei')}
    print(f'    LEI for {len(lei)} av {len(ents)} enheter')

    entities = {}
    for crn, e in sorted(ents.items()):
        # Andre navn NSTA har registrert på samme nummer (ikke bare annen tegnsetting).
        same = lambda n: re.sub(r'[^A-Z0-9]', '', n)
        known = {same(e['key'])} | {same(norm(p[0])) for p in ((chd.get(crn) or {}).get('prev') or [])}
        prev_nsta = sorted(n for n in hist_names.get(crn, ())
                           if same(n) not in known and not re.fullmatch(r'(LIMITED|LTD\.?|LLC|INC\.?|PLC)', n))
        entities[crn] = {
            'name': e['name'], 'group': e['group'], 'roles': sorted(e['roles']),
            'licences': len(e['lics']), 'licencesEver': len(lics.get(crn, ())),
            'nstaNames': [pretty(n) for n in prev_nsta],
            'ch': chd.get(crn), 'lei': lei.get(crn),
        }
    groups = collections.defaultdict(lambda: {'entities': []})
    for crn, e in entities.items():
        groups[e['group']]['entities'].append(crn)
    for g, x in groups.items():
        crns = x['entities']
        x['licencesEver'] = len(set().union(*(lics.get(c, set()) for c in crns)))
        parents = collections.Counter(tuple(entities[c]['lei']['parent']) for c in crns
                                      if entities[c]['lei'] and entities[c]['lei'].get('parent'))
        x['ultimateParent'] = list(parents.most_common(1)[0][0]) if parents else None
        x['wells'] = well_stats.get(g)
    payload = {
        'updated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'NSTA (licence subareas, licence blocks history, offshore wellbores), Companies House, GLEIF',
        'groups': dict(sorted(groups.items())), 'entities': entities,
    }
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            prev = json.load(fh)
        if {**prev, 'updated': payload['updated']} == json.loads(json.dumps(payload)):
            print(f'  = {OUT} uendret')
            return
    write_if_changed(OUT, payload)


if __name__ == '__main__':
    main()
