"""
Skipstrafikk til og fra utvalgte felt, fra Kystverkets åpne AIS-API
(Kystdatahuset), til Field → Shipping.

    pip install requests
    python fetch_shipping.py            # henter nye dager for feltene i shipping_fields.json
    python fetch_shipping.py --rebuild  # bygger besøkene på nytt fra loggen, uten nett for posisjonene

Kilder, åpne uten innlogging:
  · ais/positions/within-bbox-time — alle posisjoner i en boks rundt feltet.
    Utledet av AIS-sporene; data finnes foreløpig fra 1. januar 2026.
    Hentes i biter på CHUNK_DAYS dager, og arbeidet lagres i
    data/shipping_log.json, så neste kjøring fortsetter der forrige slapp.
  · ais/positions/for-mmsis-time — skipets posisjoner før og etter besøket,
    for å finne hvor det kom fra og dro videre til (havn, annet felt, sjø).
  · ais/static-messages — navn, type og lengde per MMSI. Går bare til starten
    av inneværende måned, så skip som er nye denne måneden står med MMSI til
    neste måned.

Feltene velges i data/shipping_fields.json (feltnavn som i Sodir, valgfri
radius). Senteret er feltets innretninger på overflaten (FPSO, plattform),
ellers alle innretninger i drift.

Et besøk er et skip som har vært innenfor radiusen og enten stoppet der
(fart under STOP_KNOTS) eller vært der minst MIN_VISIT_MIN minutter. Skip som
bare passerer, er ikke besøk. Opphold med hull under GAP_HOURS slås sammen.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

API = 'https://kystdatahuset.kystverket.no/ws/api'
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
CFG = os.path.join(DIR, 'shipping_fields.json')
LOG = os.path.join(DIR, 'shipping_log.json')
OUT = os.path.join(DIR, 'shipping.json')

DATA_START = datetime(2026, 1, 1)   # API-et har foreløpig bare data fra denne datoen
CHUNK_DAYS = 3
BOX_KM = 12            # boksen rundt senteret; større enn radiusen, så innseilingen er med
RADIUS_KM = 3.0        # standard radius for et besøk
STOP_KNOTS = 3.0
MIN_VISIT_MIN = 30
GAP_HOURS = 6
THIN_KM = 0.3          # nytt punkt i loggen når skipet har flyttet seg så langt ...
THIN_MIN = 60          # ... eller etter så mange minutter
ROUTE_DAYS = 4         # hvor langt før og etter besøket det letes etter forrige og neste stopp
STAY_MIN = 60          # et stopp underveis varer minst så lenge
MAX_SECONDS = int(os.environ.get("SHIPPING_SECONDS", 300))   # tidsbudsjett per kjøring; resten tas neste gang
TIMEOUT = 150
T0 = datetime(1970, 1, 1)
UA = 'ncs-production shipping reader (github.com/sheffielddivided/ncs-production)'


# ── HJELPERE ─────────────────────────────────────────────────────────────────
def minutes(dt):
    return int((dt - T0).total_seconds() // 60)


def from_minutes(m):
    return T0 + timedelta(minutes=m)


def iso(m):
    return from_minutes(m).strftime('%Y-%m-%dT%H:%MZ')


def parse_iso(s):
    return datetime.fromisoformat(s.replace('Z', '').split('+')[0].split('.')[0])


def km(lon1, lat1, lon2, lat2):
    r = math.radians
    a = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


class Busy(Exception):
    pass


def call(method, path, body=None, params=None, retries=4):
    last = None
    for attempt in range(retries):
        try:
            if method == 'GET':
                resp = requests.get(API + path, params=params, timeout=TIMEOUT, headers={'User-Agent': UA})
            else:
                resp = requests.post(API + path, json=body, timeout=TIMEOUT, headers={'User-Agent': UA})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(5 * (attempt + 1))
            continue
        if isinstance(data, dict) and data.get('success') is False:
            last = data.get('msg') or 'success=false'
            time.sleep(10 * (attempt + 1))
            continue
        return data
    raise Busy(str(last)[:160])


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, payload, indent=None):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(',', ':') if indent is None else None, indent=indent)
    os.replace(tmp, path)


# ── STEDER ───────────────────────────────────────────────────────────────────
def load_fields_and_facilities():
    with open(os.path.join(DIR, 'fields.json'), encoding='utf-8') as fh:
        fj = json.load(fh)
    fcol = {c: i for i, c in enumerate(fj['columns'])}
    fields = {r[fcol['id']]: r[fcol['name']] for r in fj['rows']}
    with open(os.path.join(DIR, 'facilities.json'), encoding='utf-8') as fh:
        fb = json.load(fh)
    c = {k: i for i, k in enumerate(fb['columns'])}
    facs = [{'name': r[c['name']], 'fieldId': r[c['fieldId']], 'field': fields.get(r[c['fieldId']], ''),
             'surface': r[c['surface']], 'phase': r[c['phase']], 'lon': r[c['lon']], 'lat': r[c['lat']]}
            for r in fb['rows'] if r[c['lat']] is not None and r[c['phase']] != 'REMOVED']
    return fields, facs


def load_ports():
    ports = []
    try:
        feats = call('GET', '/location/norway/all/geojson', retries=2)['features']
        for f in feats:
            p = f['properties']
            if p.get('systemname') in ('HARBOUR', 'PORT_FACILITY', 'QUAY', 'ANCHORAGE') and f.get('geometry'):
                lon, lat = f['geometry']['coordinates'][:2]
                if lon or lat:
                    ports.append({'name': p.get('locationnamenor') or p.get('locationname') or '', 'lon': lon, 'lat': lat})
    except Exception as exc:  # noqa: BLE001
        print(f'  ! kunne ikke hente havner: {exc}', file=sys.stderr)
    return ports


def nearest(items, lon, lat, limit):
    best, bd = None, limit
    for it in items:
        if abs(it['lat'] - lat) > 0.2:
            continue
        d = km(lon, lat, it['lon'], it['lat'])
        if d <= bd:
            best, bd = it, d
    return best


def place_name(lon, lat, facs, ports):
    """['port' | 'field' | 'sea', navn]."""
    port = nearest(ports, lon, lat, 3.0)
    if port:
        return ['port', port['name']]
    fac = nearest(facs, lon, lat, 3.0)
    if fac:
        return ['field', fac['field'] or fac['name']]
    return ['sea', f"{lat:.2f}°N {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"]


# ── POSISJONER I BOKSEN ──────────────────────────────────────────────────────
def bbox(centre, box_km):
    lon, lat = centre
    dlat = box_km / 111.2
    dlon = box_km / (111.2 * math.cos(math.radians(lat)))
    return f'{lon - dlon:.4f},{lat - dlat:.4f},{lon + dlon:.4f},{lat + dlat:.4f}'


def fetch_box(box, start, end):
    data = call('POST', '/ais/positions/within-bbox-time', {
        'bbox': box, 'start': start.strftime('%Y%m%d%H%M'), 'end': end.strftime('%Y%m%d%H%M'), 'minSpeed': -1,
    })['data'] or []
    out = {}
    for r in data:
        mmsi, ts, lon, lat, sog = r[0], r[1], r[2], r[3], r[5]
        if lon is None or lat is None:
            continue
        out.setdefault(str(mmsi), []).append([minutes(parse_iso(ts)), round(lon, 5), round(lat, 5),
                                              round(sog, 1) if sog is not None else None])
    return out


def thin_into(log_pts, rows):
    """Legger radene til loggen, tynnet: nytt punkt ved flytting, stopp/start
    eller etter THIN_MIN minutter."""
    rows.sort(key=lambda q: q[0])
    last = log_pts[-1] if log_pts else None
    for q in rows:
        if last and q[0] <= last[0]:
            continue
        if last:
            moved = km(last[1], last[2], q[1], q[2])
            slow_a = last[3] is not None and last[3] < STOP_KNOTS
            slow_b = q[3] is not None and q[3] < STOP_KNOTS
            if moved < THIN_KM and q[0] - last[0] < THIN_MIN and slow_a == slow_b:
                continue
        log_pts.append(q)
        last = q


# ── BESØK ────────────────────────────────────────────────────────────────────
def visits_from(pts, centres, radius):
    """[[start, slutt, nærmeste km], ...] for ett skip. pts: [min, lon, lat, sog]."""
    inside = []
    for q in pts:
        d = min(km(q[1], q[2], c[0], c[1]) for c in centres)
        if d <= radius:
            inside.append((q, d))
    out = []
    cur = None
    for q, d in inside:
        if cur and q[0] - cur['end'] <= GAP_HOURS * 60:
            cur['end'] = q[0]
            cur['min'] = min(cur['min'], d)
            cur['stop'] = cur['stop'] or (q[3] is not None and q[3] < STOP_KNOTS)
        else:
            if cur:
                out.append(cur)
            cur = {'start': q[0], 'end': q[0], 'min': d, 'stop': q[3] is not None and q[3] < STOP_KNOTS}
    if cur:
        out.append(cur)
    return [[v['start'], v['end'], round(v['min'], 2)] for v in out
            if v['stop'] or v['end'] - v['start'] >= MIN_VISIT_MIN]


def last_stop(rows, before, after, away_from, radius):
    """Siste (eller første) stopp på minst STAY_MIN minutter utenfor feltet.
    rows: [min, lon, lat, sog] sortert. before=True: siste stopp før besøket."""
    seq = rows if not before else list(reversed(rows))
    run = []
    for q in seq:
        far = min(km(q[1], q[2], c[0], c[1]) for c in away_from) > radius + 2
        slow = q[3] is not None and q[3] < STOP_KNOTS
        if far and slow:
            run.append(q)
            if abs(run[-1][0] - run[0][0]) >= STAY_MIN:
                lon = sum(p[1] for p in run) / len(run)
                lat = sum(p[2] for p in run) / len(run)
                return lon, lat
        else:
            run = []
    return None


def ship_rows(mmsi, start, end):
    data = call('POST', '/ais/positions/for-mmsis-time', {
        'mmsiIds': [int(mmsi)], 'start': start.strftime('%Y%m%d%H%M'), 'end': end.strftime('%Y%m%d%H%M'),
    })['data'] or []
    return [[minutes(parse_iso(r[1])), r[2], r[3], r[5]] for r in data if r[2] is not None and r[3] is not None]


def fetch_ships(mmsis, ships):
    """Navn, type og lengde fra static-messages; siste gyldige registrering vinner."""
    end = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    todo = [m for m in mmsis if m not in ships]
    for i in range(0, len(todo), 40):
        part = todo[i:i + 40]
        params = [('start', DATA_START.strftime('%Y-%m-%dT00:00:00Z')), ('end', end.strftime('%Y-%m-%dT00:00:00Z'))]
        params += [('mmsi', m) for m in part]
        try:
            data = call('GET', '/ais/static-messages', params=params, retries=2)['data'] or []
        except Busy as exc:
            print(f'  ! static-messages: {exc}', file=sys.stderr)
            return
        best = {}
        for r in data:
            k = str(r['mmsi'])
            if k not in best or (r.get('validFrom') or '') > (best[k].get('validFrom') or ''):
                best[k] = r
        for k, r in best.items():
            length = (r.get('dimensionToBow') or 0) + (r.get('dimensionToStern') or 0)
            ships[k] = {'name': (r.get('name') or '').strip(), 'type': r.get('shipType'), 'len': length or None,
                        'imo': r.get('imoNumber') or None}


# Kategorier til visningen. AIS-typen er ofte 0 eller 90 for offshorefartøy, så
# lange skip av ukjent type regnes som tankskip (skytteltankere er 200–300 m).
def category(ship):
    if not ship:
        return 'unknown'
    t, n = ship.get('type') or 0, ship.get('len') or 0
    if 80 <= t <= 89 or (t in (0, 90) and n >= 180):
        return 'tanker'
    if t == 30:
        return 'fishing'
    if 70 <= t <= 79:
        return 'cargo'
    if 60 <= t <= 69:
        return 'passenger'
    if t in (31, 32, 52):
        return 'tug'
    return 'offshore'


# ── HOVEDLØP ─────────────────────────────────────────────────────────────────
def main():
    rebuild = '--rebuild' in sys.argv
    deadline = time.time() + MAX_SECONDS
    cfg = load_json(CFG, {'fields': []})
    fields, facs = load_fields_and_facilities()
    by_name = {v.upper(): k for k, v in fields.items()}
    log = load_json(LOG, {'fields': {}})
    out = load_json(OUT, {'fields': {}})
    out_fields = {}
    ports = None
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    for fc in cfg.get('fields', []):
        fid = by_name.get(str(fc['name']).upper())
        if fid is None:
            print(f"  ! ukjent felt i shipping_fields.json: {fc['name']}", file=sys.stderr)
            continue
        own = [f for f in facs if f['fieldId'] == fid and f['phase'] == 'IN SERVICE']
        surf = [f for f in own if f['surface']] or own
        if not surf:
            print(f"  ! {fc['name']}: ingen innretninger med posisjon", file=sys.stderr)
            continue
        centres = [(f['lon'], f['lat']) for f in surf]
        centre = (sum(c[0] for c in centres) / len(centres), sum(c[1] for c in centres) / len(centres))
        radius = float(fc.get('radiusKm') or RADIUS_KM)
        box = bbox(centre, BOX_KM)
        key = str(fid)
        flog = log['fields'].setdefault(key, {'until': None, 'pts': {}})
        prev = (out.get('fields') or {}).get(key) or {}

        # 1. Nye posisjoner i boksen.
        t = parse_iso(flog['until']) if flog['until'] else DATA_START
        while not rebuild and t < now and time.time() < deadline:
            t1 = min(t + timedelta(days=CHUNK_DAYS), now)
            try:
                got = fetch_box(box, t, t1)
            except Busy as exc:
                print(f'  ! {fc["name"]} {t:%Y-%m-%d}: {exc}', file=sys.stderr)
                break
            for m, rows in got.items():
                thin_into(flog['pts'].setdefault(m, []), rows)
            flog['until'] = t1.strftime('%Y-%m-%dT%H:%M:%SZ')
            print(f'  {fc["name"]}: {t:%Y-%m-%d}–{t1:%Y-%m-%d}, {sum(len(v) for v in got.values())} posisjoner, {len(got)} skip')
            t = t1

        # 2. Besøk.
        visits = []
        for m, pts in flog['pts'].items():
            for s, e, d in visits_from(pts, centres, radius):
                visits.append({'m': m, 's': s, 'e': e, 'd': d})
        visits.sort(key=lambda v: v['s'])
        until_min = minutes(parse_iso(flog['until'])) if flog['until'] else 0

        # 3. Skipene.
        ships = dict(prev.get('ships') or {})
        if not rebuild:
            fetch_ships(sorted({v['m'] for v in visits}), ships)

        # 4. Hvor skipet kom fra og dro til. Gjenbrukes fra forrige kjøring.
        old_route = {f"{r[0]}|{r[1]}": (r[5], r[6]) for r in prev.get('visits') or []}
        rows_out = []
        for v in visits:
            start, end = iso(v['s']), iso(v['e'])
            open_ = until_min - v['e'] < 60   # skipet er (trolig) der ennå
            frm, to = old_route.get(f"{v['m']}|{start}", (None, None))
            if not rebuild and time.time() < deadline and (frm is None or (to is None and not open_)):
                if ports is None:
                    ports = load_ports()
                try:
                    if frm is None:
                        a = from_minutes(v['s'])
                        st = last_stop(ship_rows(v['m'], a - timedelta(days=ROUTE_DAYS), a), True, False, centres, radius)
                        frm = place_name(st[0], st[1], facs, ports) if st else ['', '']
                    if to is None and not open_:
                        b = from_minutes(v['e'])
                        if (now - b).days >= 1:
                            st = last_stop(ship_rows(v['m'], b, min(b + timedelta(days=ROUTE_DAYS), now)), False, True, centres, radius)
                            to = place_name(st[0], st[1], facs, ports) if st else (['', ''] if (now - b).days >= ROUTE_DAYS else None)
                except Busy as exc:
                    print(f'  ! rute {v["m"]}: {exc}', file=sys.stderr)
            rows_out.append([v['m'], start, end, v['d'], 1 if open_ else 0, frm, to])

        ship_ids = {r[0] for r in rows_out}
        out_fields[key] = {
            'name': fields[fid], 'centre': [round(centre[0], 5), round(centre[1], 5)], 'radiusKm': radius,
            'facilities': [f['name'] for f in surf],
            'from': DATA_START.strftime('%Y-%m-%d'), 'until': flog['until'],
            'columns': ['mmsi', 'start', 'end', 'minKm', 'open', 'from', 'to'],
            'visits': rows_out,
            'ships': {m: {**ships.get(m, {}), 'cat': category(ships.get(m))} for m in sorted(ship_ids)},
        }
        print(f'  {fc["name"]}: {len(rows_out)} besøk, {len(ship_ids)} skip, data til {flog["until"]}')

    write_json(LOG, log)
    write_json(OUT, {'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                     'source': 'Kystverket, Kystdatahuset AIS (NLOD 2.0)', 'fields': out_fields})


if __name__ == '__main__':
    main()
