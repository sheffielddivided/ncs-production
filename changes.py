"""
Endringslogg: hva som er nytt siden forrige datakjøring.

Brukes av fetch_sodir.py (brønner), fetch_rigs.py (rigger) og fetch_havtil.py
(tilsynssaker) og skriver
data/changes.json, som Exploration viser i fanen News.

Hver hendelse har en stabil id, så en ny kjøring samme dag ikke dobler dem:
    {"id": "spud|10650", "d": "2026-10-25", "t": "spud", "w": 10650, ...}
Typene er
    plan       ny planlagt letebrønn
    spud       brønnen er startet
    done       brønnen er ferdig (tørr, shows eller annet)
    discovery  brønnen er ferdig og påviste olje eller gass i en funnbrønn
    postponed  planlagt start er flyttet med minst en uke
    cancelled  planlagt brønn er borte fra Sodirs data
    rig        en rigg har kommet til et nytt sted (AIS)
    havtil     ny tilsynsrapport, samtykke, gransking eller pålegg fra Havtil

Første gang fila lages, fylles den også ut fra datoene i dataene selv (starter og
avslutninger siste 30 dager), ellers ville den vært tom til noe skjer.
"""

import json
import os
from datetime import datetime, timedelta, timezone

PATH = os.path.join('data', 'changes.json')
KEEP_DAYS = 180
MAX_EVENTS = 800
BACKFILL_DAYS = 60
POSTPONED_DAYS = 7
HC = {'OIL', 'GAS', 'OIL/GAS', 'GAS/CONDENSATE', 'OIL/GAS/CONDENSATE'}


def today_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def exists():
    return os.path.exists(PATH)


def load():
    if not exists():
        return []
    with open(PATH, encoding='utf-8') as fh:
        return json.load(fh).get('events', [])


def merge(events):
    """Legg til nye hendelser (uten dubletter), fjern gamle og skriv fila."""
    have = {e['id']: e for e in load()}
    added = 0
    for e in events:
        if e['id'] not in have:
            have[e['id']] = e
            added += 1
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
    keep = sorted((e for e in have.values() if e['d'] >= cutoff), key=lambda e: (e['d'], e['id']), reverse=True)[:MAX_EVENTS]
    text = json.dumps({'events': keep}, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if exists():
        with open(PATH, encoding='utf-8') as fh:
            if fh.read() == text:
                print(f"  = changes.json uendret ({len(keep)} hendelser)")
                return added
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f"  + changes.json skrevet ({len(keep)} hendelser, {added} nye)")
    return added


# ── Brønner ──────────────────────────────────────────────────────────────────
def _rows(data):
    cols = data['columns']
    return {r[0]: dict(zip(cols, r)) for r in data['rows']}


def _state(w, today):
    if not w['entry'] or w['entry'] > today:
        return 'planned'
    if not w['completion'] or w['completion'] > today:
        return 'drilling'
    return 'completed'


def _well_event(kind, w, day, **extra):
    e = {'id': f"{kind}|{w['id']}" + extra.pop('suffix', ''), 'd': day, 't': kind, 'w': w['id'],
         'n': w['name'], 'op': w['operator'], 'rig': w['rig']}
    e.update({k: v for k, v in extra.items() if v not in ('', None)})
    return e


def _finished(w, day):
    """Ferdig brønn: funn når den er funnbrønn med olje eller gass, ellers vanlig."""
    if w['content'] in HC and w['discoveryWell']:
        return _well_event('discovery', w, day, c=w['content'], dn=w['discovery'])
    return _well_event('done', w, day, c=w['content'])


def well_events(old, new, today=None):
    """Hendelser fra forskjellen mellom forrige og nye brønndata."""
    today = today or today_iso()
    o, n = _rows(old), _rows(new)
    out = []
    for wid, w in n.items():
        st = _state(w, today)
        if wid not in o:
            if st == 'planned':
                out.append(_well_event('plan', w, today, planned=w['planned']))
            elif st == 'drilling':
                out.append(_well_event('spud', w, w['entry'] or today))
            else:
                out.append(_finished(w, w['completion'] or today))
            continue
        ow = o[wid]
        ost = _state(ow, today)
        if ost == 'planned' and st != 'planned':
            out.append(_well_event('spud', w, w['entry'] or today))
        if ost != 'completed' and st == 'completed':
            out.append(_finished(w, w['completion'] or today))
        if ost == 'planned' and st == 'planned' and ow['planned'] and w['planned'] and ow['planned'] != w['planned']:
            shift = (datetime.fromisoformat(w['planned']) - datetime.fromisoformat(ow['planned'])).days
            if abs(shift) >= POSTPONED_DAYS:
                out.append(_well_event('postponed', w, today, suffix='|' + w['planned'], frm=ow['planned'], to=w['planned']))
    for wid, ow in o.items():
        if wid not in n and _state(ow, today) == 'planned':
            out.append(_well_event('cancelled', ow, today))
    return out


def backfill_wells(new, today=None, days=BACKFILL_DAYS):
    """Starter og avslutninger de siste dagene, datert etter brønnen selv."""
    today = today or today_iso()
    since = (datetime.fromisoformat(today) - timedelta(days=days)).strftime('%Y-%m-%d')
    out = []
    for w in _rows(new).values():
        st = _state(w, today)
        if w['entry'] and since <= w['entry'] <= today:
            out.append(_well_event('spud', w, w['entry']))
        if st == 'completed' and w['completion'] and since <= w['completion'] <= today:
            out.append(_finished(w, w['completion']))
    return out


# ── Rigger ───────────────────────────────────────────────────────────────────
def _day(minutes):
    return datetime.fromtimestamp(minutes * 60, tz=timezone.utc).strftime('%Y-%m-%d')


def _rig_event(name, stays, i):
    s = stays[i]
    prev = stays[i - 1] if i > 0 else None
    return {'id': f"rig|{name}|{s[0]}", 'd': _day(s[0]), 't': 'rig', 'r': name, 'k': s[4], 'p': s[5],
            'f': s[0], **({'pk': prev[4], 'pp': prev[5]} if prev else {})}


def rig_events(old, new, backfill_since=None):
    """Nye opphold siden forrige kjøring, eller siste dager ved første kjøring.

    Opphold som dukker opp i tiden før det vi allerede hadde (sporene som tar igjen
    innlastingen) er ikke nyheter, og et nytt opphold på samme sted som forrige er
    en flytting mellom maler, ikke en ny destinasjon."""
    out = []
    for name, r in new.items():
        if 'alias' in r:
            continue
        stays = r.get('stays', [])
        known = {s[0] for s in (old.get(name, {}).get('stays', []) if old else [])}
        last_old = max(known) if known else None
        for i, s in enumerate(stays):
            if backfill_since is not None:
                if s[0] < backfill_since:
                    continue
            elif s[0] in known or (last_old is not None and s[0] <= last_old):
                continue
            if i > 0 and stays[i - 1][5] == s[5] and s[4] != 'port':
                continue
            out.append(_rig_event(name, stays, i))
    return out
