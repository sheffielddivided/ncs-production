"""
Henter AIS-posisjoner for flyttbare borerigger fra Kystverkets åpne API
(Kystdatahuset) og bygger data/rig_tracks.json og data/rig_now.json til
riggprofilen og flåtekartet i Exploration.

    pip install requests
    python fetch_rigs.py

Kilder, alle åpne uten innlogging:
  · tracks/for-ships/by-mmsi — ferdige spor, bare mens skipet beveger seg.
    Rask, men går bare til siste månedlige innlasting (flere uker etter).
  · ais/positions/for-mmsis-time — alle meldinger. Dekker det sporene ikke
    gjør, men databasen tåler bare korte vinduer (høyst ~6 dager) og svarer
    av og til «too many clients». Hentes derfor i biter, med nye forsøk, og
    arbeidet lagres så neste kjøring kan fortsette.
  · ais/realtime/geojson — alle skip nå. Gir dagens posisjon, og fyller
    hullet mellom siste innlastede dag og i dag etter hvert som dagene går.

rig_log.json er arbeidsfila: tynnede posisjoner utover det sporene dekker.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

API = 'https://kystdatahuset.kystverket.no/ws/api'
OUT_DIR = 'data'
WINDOW_START = datetime(2025, 7, 1)
TIMEOUT = 150
CHUNK_DAYS = 6           # lengste posisjonsvindu databasen tåler
MAX_SECONDS = 1200       # tidsbudsjett for hele kjøringen; resten tas neste gang
THIN_KM = 0.12           # nytt punkt når riggen har flyttet seg så langt ...
THIN_HOURS = 3           # ... eller etter så mange timer
STAY_KM = 1.0            # et opphold holder seg innenfor denne radien
STAY_HOURS = 6           # og varer minst så lenge
GAP_HOURS = 36           # lengre hull i dataene, med flytting, er «ingen data»
MERGE_HOURS = 48         # oppholdene på samme sted slås sammen når hullet er kortere

T0 = datetime(1970, 1, 1)

# Området som er relevant: sokkelen og kysten, fra Nordsjøen i sør til Barentshavet
# i nord og øst. Posisjoner utenfor regnes som ugyldige. Den samme MMSI-en kan
# nemlig sendes fra et annet skip eller være feilregistrert (COSLInnovators
# 565797000 har hundrevis av posisjoner rundt Malaysia og Bengalbukta, mens
# riggen selv lå på Gullfaks Sør).
AREA = (-6.0, 54.0, 36.0, 76.0)    # lon_vest, lat_sør, lon_øst, lat_nord
MAX_KNOTS = 35                     # raskere enn dette mellom to punkter er et hopp, ikke en reise


class Busy(Exception):
    pass


class NoTable(Exception):
    pass


def minutes(dt):
    return int((dt - T0).total_seconds() // 60)


def from_minutes(m):
    return T0 + timedelta(minutes=m)


def parse_iso(s):
    return datetime.fromisoformat(s.replace('Z', '').split('+')[0].split('.')[0])


def in_area(lon, lat):
    return AREA[0] <= lon <= AREA[2] and AREA[1] <= lat <= AREA[3]


def km(lon1, lat1, lon2, lat2):
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371.0 * math.hypot(x, y)


# ── HTTP ─────────────────────────────────────────────────────────────────────
def call(method, path, body=None, retries=4):
    """Kall API-et. Hever Busy ved overbelastet database og NoTable når
    databasen ikke har dagen ennå; ellers prøver den på nytt med pause."""
    last = None
    for attempt in range(retries):
        try:
            if method == 'GET':
                resp = requests.get(API + path, timeout=TIMEOUT)
            else:
                resp = requests.post(API + path, json=body, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(5 * (attempt + 1))
            continue
        if isinstance(data, dict) and data.get('success') is False:
            msg = data.get('msg') or ''
            if '42P01' in msg:
                raise NoTable(msg)
            last = msg
            time.sleep(15 * (attempt + 1))
            continue
        return data
    raise Busy(str(last)[:120])


# ── SPOR OG POSISJONER ───────────────────────────────────────────────────────
def track_points(mmsis):
    """Punkter [minutter, lon, lat] fra sporsegmentene. Hvert segment har
    start- og sluttid og en linje; tiden fordeles jevnt langs den."""
    data = call('POST', '/tracks/for-ships/by-mmsi', {
        'mmsiIds': mmsis,
        'startTime': WINDOW_START.strftime('%Y-%m-%dT00:00:00Z'),
        'endTime': (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%dT00:00:00Z'),
    })['data'] or []
    pts, end = {}, None
    for seg in data:
        t0, t1 = parse_iso(seg['starttime']), parse_iso(seg['endtime'])
        coords = seg['geometry']['coordinates']
        n = len(coords)
        for i, (lon, lat) in enumerate(coords):
            t = t0 + (t1 - t0) * (i / (n - 1) if n > 1 else 0)
            pts[minutes(t)] = (lon, lat)
        end = t1 if end is None or t1 > end else end
    return [[t, p[0], p[1]] for t, p in sorted(pts.items())], end


def thin(rows, last=None):
    """Tynn ut posisjoner: bare punkter som har flyttet seg eller er gamle nok."""
    out = []
    for t, lon, lat in rows:
        ref = out[-1] if out else last
        if ref is None or km(ref[1], ref[2], lon, lat) >= THIN_KM or t - ref[0] >= THIN_HOURS * 60:
            out.append([t, lon, lat])
    return out


def gap_fill(mmsis, start, log, deadline, fetched, key):
    """Hent posisjoner fra start og framover, i biter, til databasen ikke har
    dagen ennå. Returnerer True hvis vi nådde slutten, False hvis vi ble
    stoppet av overbelastning eller tidsbudsjettet."""
    now = datetime.utcnow()
    cur = start
    while cur < now:
        if time.time() > deadline:
            return False
        end = min(cur + timedelta(days=CHUNK_DAYS), now)
        try:
            data = call('POST', '/ais/positions/for-mmsis-time', {
                'mmsiIds': mmsis,
                'start': cur.strftime('%Y%m%d%H%M'),
                'end': end.strftime('%Y%m%d%H%M'),
            })['data'] or []
        except NoTable:
            # Dagen er ikke innlastet. Prøv å gå videre dag for dag, så vi ikke
            # mister en innlastet dag midt i vinduet.
            day = cur
            while day < end:
                nxt = day + timedelta(days=1)
                try:
                    rows = call('POST', '/ais/positions/for-mmsis-time', {
                        'mmsiIds': mmsis, 'start': day.strftime('%Y%m%d%H%M'),
                        'end': nxt.strftime('%Y%m%d%H%M')}, retries=2)['data'] or []
                    add_rows(log, rows)
                    fetched[key] = minutes(nxt)
                except NoTable:
                    return True
                except Busy:
                    return False
                day = nxt
            return True
        except Busy:
            return False
        add_rows(log, data)
        fetched[key] = minutes(end)
        cur = end
    return True


def add_rows(log, rows):
    by = {}
    for r in rows:
        if in_area(r[2], r[3]):
            by.setdefault(r[0], []).append([minutes(parse_iso(r[1])), r[2], r[3]])
    for mmsi, pts in by.items():
        pts.sort()
        have = log.setdefault(str(mmsi), [])
        known = {p[0] for p in have}
        last = have[-1] if have else None
        for p in thin([p for p in pts if p[0] not in known], last):
            have.append(p)
        have.sort()


def sanitize(points):
    """Fjern posisjoner utenfor området og enkeltstående hopp.

    Et hopp er et punkt som både fra forrige og til neste gyldige punkt ville
    krevd mer enn MAX_KNOTS. Endepunktene sjekkes mot sin eneste nabo. Et vedvarende
    skifte av sted beholdes, for det er en reell flytting. Returnerer punktene som
    beholdes og «brudd»: par [t1, t2] der noe ble fjernet mellom to beholdte punkter."""
    pts = sorted(points)
    valid = [in_area(p[1], p[2]) for p in pts]

    def fast(a, b):
        hours = max((b[0] - a[0]) / 60, 1 / 60)
        return km(a[1], a[2], b[1], b[2]) / 1.852 / hours > MAX_KNOTS

    idx = [i for i, v in enumerate(valid) if v]
    for k, i in enumerate(idx):
        before = pts[idx[k - 1]] if k > 0 else None
        after = pts[idx[k + 1]] if k + 1 < len(idx) else None
        bad_b = before is None or fast(before, pts[i])
        bad_a = after is None or fast(pts[i], after)
        # Midt i en serie må begge sider være for raske; i endene er det nok med den ene.
        if (before is not None and after is not None and bad_b and bad_a) or \
           (before is None and after is not None and bad_a) or \
           (after is None and before is not None and bad_b):
            valid[i] = False
    kept, breaks, dropped_since = [], [], False
    for p, v in zip(pts, valid):
        if v:
            if kept and dropped_since:
                breaks.append([kept[-1][0], p[0]])
            kept.append(p)
            dropped_since = False
        else:
            dropped_since = True
    return kept, breaks


# ── OPPHOLD ──────────────────────────────────────────────────────────────────
def simplify(pts, tol=0.003):
    """Douglas-Peucker på [t, lon, lat]; beholder første og siste."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a][1], pts[a][2]
        bx, by = pts[b][1], pts[b][2]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = pts[i][1] - ax, pts[i][2] - ay
            d = abs(px * dy - py * dx) / norm if norm else math.hypot(px, py)
            if d > best:
                best, idx = d, i
        if best > tol and idx > 0:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(pts, keep) if k]


def build_rig(points, seen_until, breaks=()):
    """Fra tidssorterte punkter til veipunkter, opphold og hull."""
    if not points:
        return None
    pts = points[:]
    if seen_until and seen_until > pts[-1][0]:
        pts.append([seen_until, pts[-1][1], pts[-1][2]])   # riggen står der siste kjente posisjon er
    n = len(pts)
    at = {p[0]: i for i, p in enumerate(pts)}
    brk_after = {at[a] for a, b in breaks if a in at}      # brudd mellom punkt i og i + 1

    # Opphold: grådig fra hvert punkt, innenfor STAY_KM av ankerpunktet.
    clusters, i = [], 0
    while i < n:
        a = pts[i]
        j = i
        while j + 1 < n and j not in brk_after and km(a[1], a[2], pts[j + 1][1], pts[j + 1][2]) <= STAY_KM:
            j += 1
        dur_h = (pts[j][0] - a[0]) / 60
        # Siste gruppe er der riggen er nå, uansett hvor kort tid vi har sett den.
        if dur_h >= STAY_HOURS or (j == n - 1 and j > i):
            clusters.append([i, j])
            i = j + 1
        else:
            i += 1
    if not clusters:
        clusters = [[0, n - 1]]

    # Slå sammen oppholdene på samme sted med kort hull mellom seg.
    merged = [clusters[0]]
    for c in clusters[1:]:
        p = merged[-1]
        pa, pb = pts[p[1]], pts[c[0]]
        ca = centroid(pts[p[0]:p[1] + 1])
        cb = centroid(pts[c[0]:c[1] + 1])
        if km(ca[0], ca[1], cb[0], cb[1]) <= STAY_KM and (pb[0] - pa[0]) / 60 <= MERGE_HOURS \
                and p[1] not in brk_after:
            p[1] = c[1]
        else:
            merged.append(c)
    clusters = merged

    wp, stays, gaps = [], [], []
    prev_end = None
    for k, (a, b) in enumerate(clusters):
        cx, cy = centroid(pts[a:b + 1])
        t_from, t_to = pts[a][0], pts[b][0]
        # Bevegelsen mellom forrige opphold og dette.
        if prev_end is not None:
            move = pts[prev_end + 1:a]
            for t, lon, lat in simplify([pts[prev_end]] + move + [pts[a]])[1:-1]:
                wp.append([t, round(lon, 4), round(lat, 4)])
            # Hull: lang tid uten data mellom to punkter som ligger langt fra hverandre.
            seq = [pts[prev_end]] + move + [pts[a]]
            for p, q in zip(seq, seq[1:]):
                if (q[0] - p[0]) / 60 > GAP_HOURS and km(p[1], p[2], q[1], q[2]) > 3:
                    gaps.append([p[0], q[0]])
        wp.append([t_from, round(cx, 4), round(cy, 4)])
        if t_to > t_from:
            wp.append([t_to, round(cx, 4), round(cy, 4)])
        stays.append([t_from, t_to, round(cx, 4), round(cy, 4)])
        prev_end = b
    # Punkter før første opphold.
    if clusters[0][0] > 0:
        head = simplify(pts[:clusters[0][0] + 1])[:-1]
        wp = [[t, round(lo, 4), round(la, 4)] for t, lo, la in head] + wp
    # Punkter etter siste opphold.
    if prev_end is not None and prev_end < n - 1:
        tail = simplify(pts[prev_end:])[1:]
        wp += [[t, round(lo, 4), round(la, 4)] for t, lo, la in tail]
    # Brudd (posisjoner utenfor området eller hopp som ble fjernet) er også hull.
    for a, b in breaks:
        if [a, b] not in gaps:
            gaps.append([a, b])
    gaps.sort()
    # Fjern like tider (to veipunkter på samme minutt).
    out, last_t = [], None
    for w in wp:
        if w[0] == last_t:
            out[-1] = w
        else:
            out.append(w)
        last_t = w[0]
    return {'wp': out, 'stays': stays, 'gaps': gaps}


def centroid(pts):
    return (sum(p[1] for p in pts) / len(pts), sum(p[2] for p in pts) / len(pts))


# ── STEDSNAVN ────────────────────────────────────────────────────────────────
def load_places():
    """Innretninger, letebrønner og havner til stedsnavn på oppholdene."""
    with open(os.path.join(OUT_DIR, 'facilities.json'), encoding='utf-8') as fh:
        fb = json.load(fh)
    fc = {c: i for i, c in enumerate(fb['columns'])}
    with open(os.path.join(OUT_DIR, 'fields.json'), encoding='utf-8') as fh:
        field_name = {r[0]: r[1] for r in json.load(fh)['rows']}
    facs = [{'name': r[fc['name']], 'field': field_name.get(r[fc['fieldId']], ''),
             'lon': r[fc['lon']], 'lat': r[fc['lat']]}
            for r in fb['rows'] if r[fc['lat']] is not None and r[fc['phase']] != 'REMOVED']
    with open(os.path.join(OUT_DIR, 'exploration_wells.json'), encoding='utf-8') as fh:
        wb = json.load(fh)
    wc = {c: i for i, c in enumerate(wb['columns'])}
    wells = [{'id': r[wc['id']], 'name': r[wc['name']], 'lon': r[wc['lon']], 'lat': r[wc['lat']]}
             for r in wb['rows'] if r[wc['lat']] is not None]
    ports = []
    try:
        feats = call('GET', '/location/norway/all/geojson', retries=2)['features']
        for f in feats:
            p = f['properties']
            if p['systemname'] in ('HARBOUR', 'PORT_FACILITY', 'QUAY', 'ANCHORAGE') and f.get('geometry'):
                lon, lat = f['geometry']['coordinates']
                if lon or lat:
                    ports.append({'name': p['locationnamenor'], 'lon': lon, 'lat': lat})
    except Exception as exc:  # noqa: BLE001
        print(f"  ! kunne ikke hente havner: {exc}", file=sys.stderr)
    return facs, wells, ports


def nearest(items, lon, lat, limit):
    best, bd = None, limit
    for it in items:
        d = km(lon, lat, it['lon'], it['lat'])
        if d <= bd:
            best, bd = it, d
    return best


def name_stay(stay, facs, wells, ports):
    t_from, t_to, lon, lat = stay
    port = nearest(ports, lon, lat, 3.0)
    if port:
        return ['port', port['name'], '', []]
    near_wells = sorted(((km(lon, lat, w['lon'], w['lat']), w) for w in wells
                         if abs(w['lat'] - lat) < 0.02 and km(lon, lat, w['lon'], w['lat']) <= 1.2),
                        key=lambda x: x[0])
    fac = nearest(facs, lon, lat, 2.5)
    wid = sorted({w['id'] for _, w in near_wells})
    if fac:
        return ['field', fac['field'] or fac['name'], fac['name'], wid]
    if near_wells:
        return ['well', near_wells[0][1]['name'], '', wid]
    return ['sea', f"{lat:.2f}°N {lon:.2f}°E", '', []]


# ── HOVEDLØP ─────────────────────────────────────────────────────────────────
def load_json(name, default):
    path = os.path.join(OUT_DIR, name)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    return default


def write_json(name, payload):
    path = os.path.join(OUT_DIR, name)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            if fh.read() == text:
                print(f"  = {name} uendret")
                return
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f"  + {name} skrevet ({len(text) / 1024:.0f} KB)")


def main():
    deadline = time.time() + MAX_SECONDS
    cfg = load_json('rig_ais.json', {'rigs': {}})['rigs']
    primary = {n: v for n, v in cfg.items() if 'mmsi' in v}
    log = load_json('rig_log.json', {'pts': {}, 'seen': {}})
    pts_log, seen = log['pts'], log['seen']
    fetched = log.setdefault('fetched', {})
    # Siste innhentede tidspunkt per MMSI, så posisjoner vi fjerner fra loggen ikke
    # henter samme dager på nytt. Eldre loggfiler hadde ikke feltet.
    for k, v in pts_log.items():
        if v:
            fetched.setdefault(k, v[-1][0])
        pts_log[k] = [q for q in v if in_area(q[1], q[2])]

    print("Henter dagens posisjoner...")
    try:
        feats = call('GET', '/ais/realtime/geojson')['features']
    except Busy as exc:
        print(f"  ! sanntidsdata utilgjengelig: {exc}", file=sys.stderr)
        feats = []
    now_by_mmsi = {}
    wanted = {m for v in primary.values() for m in v['mmsi']}
    for f in feats:
        p = f['properties']
        coords = (f.get('geometry') or {}).get('coordinates') or []
        if p.get('mmsi') not in wanted or not coords:
            continue
        lon, lat = coords[-1]
        if not in_area(lon, lat):
            continue
        now_by_mmsi[p['mmsi']] = {
            'lon': round(lon, 5), 'lat': round(lat, 5), 'sog': p.get('speed'), 'cog': p.get('cog'),
            'heading': p.get('true_heading'), 'status': p.get('status'),
            'dest': (p.get('destination') or '').strip(), 't': minutes(parse_iso(p['date_time_utc'])),
        }

    facs, wells, ports = load_places()
    print(f"  {len(facs)} innretninger, {len(wells)} letebrønner, {len(ports)} havner")

    rigs_out, now_out = {}, {}
    for name in sorted(primary):
        info = primary[name]
        mmsis = info['mmsi']
        print(f"{name} ({', '.join(map(str, mmsis))})")
        try:
            tpts, track_end = track_points(mmsis)
        except Busy as exc:
            print(f"  ! spor utilgjengelig: {exc}", file=sys.stderr)
            tpts, track_end = [], None
        track_end_m = minutes(track_end) if track_end else minutes(WINDOW_START)

        # Posisjoner utover sporene. Eldre enn sporene trengs ikke lenger.
        ks = [str(m) for m in mmsis]
        for k in ks:
            pts_log[k] = [p for p in pts_log.get(k, []) if p[0] > track_end_m]
        start_m = max([track_end_m] + [fetched.get(k, 0) for k in ks])
        done = gap_fill(mmsis, from_minutes(start_m), pts_log, deadline, fetched, ks[0])
        if not done:
            print("  (ikke ferdig, fortsetter neste kjøring)")

        # Dagens posisjon, som siste punkt i loggen.
        cur = next((now_by_mmsi[m] for m in mmsis if m in now_by_mmsi), None)
        if cur:
            k = str(next(m for m in mmsis if m in now_by_mmsi))
            last = pts_log.get(k, [None])[-1] if pts_log.get(k) else (tpts[-1] if tpts else None)
            if last is None or cur['t'] > last[0]:
                pts_log.setdefault(k, []).extend(thin([[cur['t'], cur['lon'], cur['lat']]], last))
            seen[k] = max(seen.get(k, 0), cur['t'])

        raw = sorted({p[0]: p for p in tpts + [q for k in ks for q in pts_log.get(k, [])]}.values())
        points, breaks = sanitize(raw)
        if len(points) < len(raw):
            print(f"  {len(raw) - len(points)} posisjoner fjernet (utenfor området eller hopp)")
        seen_until = max([seen.get(k, 0) for k in ks] + [0]) or None
        built = build_rig(points, seen_until, breaks)
        if not built:
            continue
        stays = []
        for s in built['stays']:
            stays.append(s + name_stay(s, facs, wells, ports))
        built['stays'] = stays
        built.update({'mmsi': mmsis, 'imo': info.get('imo')})
        rigs_out[name] = built
        if cur:
            last_stay = stays[-1] if stays else None
            now_out[name] = {**cur, 'mmsi': next(m for m in mmsis if m in now_by_mmsi),
                             'place': last_stay[4:7] if last_stay else None,
                             'since': last_stay[0] if last_stay else None}
        print(f"  {len(points)} punkter -> {len(built['wp'])} veipunkter, {len(stays)} opphold")
        write_json('rig_log.json', {'pts': pts_log, 'seen': seen, 'fetched': fetched})   # arbeidet skal ikke gå tapt ved feil

    for name, v in cfg.items():
        if 'alias' in v and v['alias'] in rigs_out:
            rigs_out[name] = {'alias': v['alias']}
            if v['alias'] in now_out:
                now_out[name] = {'alias': v['alias']}

    write_json('rig_log.json', {'pts': pts_log, 'seen': seen, 'fetched': fetched})
    write_json('rig_tracks.json', {
        'columns': {'wp': ['t', 'lon', 'lat'],
                    'stays': ['from', 'to', 'lon', 'lat', 'kind', 'label', 'detail', 'wells'],
                    'gaps': ['from', 'to']},
        'note': 't er minutter siden 1970-01-01 UTC',
        'from': minutes(WINDOW_START), 'rigs': rigs_out,
    })
    asof = max([v['t'] for v in now_out.values() if 't' in v] + [0])
    write_json('rig_now.json', {'asOf': asof, 'rigs': now_out})
    return 0


if __name__ == '__main__':
    sys.exit(main())
