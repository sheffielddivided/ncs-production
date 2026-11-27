"""
Tankskip (skytteltankere) som laster på utvalgte felt, fra Kystverkets åpne
AIS-API (Kystdatahuset), til Field → Shipping og Hub → Shipping. Andre skip
(forsyning, standby, fiske, last) hentes med i boksen, men lagres ikke.

    pip install requests
    python fetch_shipping.py            # henter nye dager for feltene i shipping_fields.json
    python fetch_shipping.py --rebuild  # bygger shipping.json fra loggen, uten nett

Kilder, åpne uten innlogging:
  · ais/positions/within-bbox-time — alle posisjoner i en boks rundt feltets
    innretninger (radius + 1 km).
    Utledet av AIS-sporene; data finnes foreløpig fra 1. januar 2026. Bare
    oppholdene lagres (data/shipping_log.json), ikke posisjonene.
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

Et besøk er et skip som har vært minst MIN_VISIT_MIN minutter innenfor
radiusen rundt en av feltets innretninger, og enten stoppet der (fart under
STOP_KNOTS) eller vært der minst LONG_VISIT_MIN minutter. Skip som bare passerer, er ikke
besøk. Opphold med hull under GAP_HOURS slås sammen, og det samme skipets
anløp med under SAME_SHIP_HOURS imellom er ett anløp. Det er de også når
skipet lå og ventet til sjøs under WAIT_NEAR_KM fra feltet mellom dem (under
WAIT_DAYS). Opphold under MIN_LOAD_HOURS er ikke lasting og tas ikke med,
unntatt når skipet var nærmere enn LOAD_NEAR_KM fra lasteinnretningen.

Boksen gir ofte bare noen få posisjoner av et opphold, og i perioder ingen.
Ankomst og avgang tas derfor fra skipets eget spor (arrival_departure), og
sporene til de kjente tankskipene leses for anløp boksen ikke så (gap_calls).
"""

import json
import math
import re
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from concurrent.futures import ThreadPoolExecutor

import requests

API = 'https://kystdatahuset.kystverket.no/ws/api'
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
CFG = os.path.join(DIR, 'shipping_fields.json')
LOG = os.path.join(DIR, 'shipping_log.json')
OUT = os.path.join(DIR, 'shipping.json')
SPECS = os.path.join(DIR, 'tanker_specs.json')
OBS = os.path.join(DIR, 'tanker_obs.json')

DATA_START = datetime(2026, 1, 1)   # API-et har foreløpig bare data fra denne datoen
CHUNK_DAYS = 3
KEEP_MARGIN_KM = 1.0   # posisjoner så langt utenfor radiusen tas med i boksen og loggen
WORKERS = 3            # felt som hentes samtidig
RADIUS_KM = 3.0        # standard radius for et besøk
STOP_KNOTS = 3.0
MIN_VISIT_MIN = 30
LONG_VISIT_MIN = 120   # uten stopp må skipet ha vært så lenge innenfor radiusen
GAP_HOURS = 6
SAME_SHIP_HOURS = 72   # anløp av samme skip på samme felt med kortere mellomrom er ett anløp
WAIT_DAYS = 7          # ventet skipet til sjøs nær feltet mellom to anløp, er de ett anløp så lenge
WAIT_NEAR_KM = 40      # så nær feltet ligger skipet når det venter
# Var skipet aldri lenger enn dette fra feltet mellom to anløp (skipets spor),
# losset det ikke imellom, og det er ett anløp: lastingen ble avbrutt (vær),
# og skipet lå i le eller ventet lenger unna enn WAIT_NEAR_KM (Aurora Spirit
# på Norne 2.–6. januar 2026, 95 km unna inne ved kysten).
WAIT_FAR_KM = 120
MIN_LOAD_HOURS = 6     # kortere opphold er ikke lasting (skipet venter eller går inn mens et annet laster)
# Så nær lasteinnretningen ligger tankskipet når det laster (antennen ca. 0,45–0,55
# km fra innretningens posisjon, innenfor sikkerhetssonen); skip som venter,
# ligger lenger unna. Slike opphold er lasting selv om de er korte: på
# Haltenbanken (Åsgard, Heidrun) er AIS-dekningen hullete, så lastingen ofte
# bare blir et par timer med posisjoner.
LOAD_NEAR_KM = 0.7
# Kapasitet og last. Dødvekt anslås som DWT_K × lengde × bredde × største
# dypgang (AIS-registeret) når tanker_specs.json ikke har oppgitt tall;
# DWT_K er kalibrert mot AETs oppgitte tall for Eagle Balder, Blane og Bergen
# (0,63–0,66), og stemmer med de ti oppgitte i tanker_specs.json (snitt 0,647). Lastekapasiteten er lastedødvekten (CARGO_SHARE av dødvekten)
# delt på råoljens tetthet. Lasten per anløp anslås fra dypgangen etter
# avgang: full last minus det skipet ligger høyere enn største dypgang,
# med vannlinjeareal WP_K × lengde × bredde.
DWT_K = 0.648
CARGO_SHARE = 0.95
OIL_DENSITY = 0.87     # tonn per m³, typisk råolje på sokkelen
BBL_PER_M3 = 6.2898
WP_K = 0.88
SEAWATER = 1.025
OBS_DAYS = 120         # så lenge dypgangsobservasjonene beholdes
# Lasten leses av dypgangen like etter avgang: fra Alvheim er skipet ved
# Mongstad etter om lag ti timer, og dypgangen etter lossing (ballast, på vei
# tilbake for å laste) må ikke tas for en liten last. Dypgang meldt etter
# LADEN_HOURS, eller med et felt som destinasjon, brukes ikke.
LADEN_HOURS = 24
ROUTE_DAYS = 7         # hvor langt før besøket det letes etter forrige stopp
TO_DAYS = 10           # hvor langt etter besøket det letes etter neste stopp
TO_VERSION = 3         # øk når regelen for «to» endres, så alle slås opp på nytt
EXIT_GAP_MIN = 12 * 60 # hull i sporet: stopp (samme sted etterpå) eller utseiling av AIS-dekningen
GAP_SAME_KM = 5        # så nær er skipet før og etter hullet når det har ligget stille
STAY_MIN = 60          # et stopp underveis varer minst så lenge
# Ankomst og avgang fra skipets eget spor. Boksen gir ofte bare noen få
# posisjoner av et opphold på Haltenbanken, så tidene fra den alene blir for
# korte. Sporet (for-mmsis-time) har posisjoner mens skipet går, men ikke
# mens det ligger stille: ankomsten er da når skipet kom inn i radiusen etter
# siste posisjon utenfor, avgangen når det gikk ut før første posisjon utenfor.
TIMES_DAYS = 4         # hvor langt før og etter besøket det letes
TIMES_VERSION = 1      # øk når regelen endres, så alle slås opp på nytt
MIN_APPROACH_KNOTS = 2 # lavere fart regnes som dette når gangtiden inn eller ut anslås
# Anløp fra sporene alene. På Haltenbanken gir boksen i perioder nesten ingen
# posisjoner (februar 2026: Åsgard C, som ligger fast og sender hele tiden,
# sees bare enkelte dager), så hele lastinger mangler. Sporene til de kjente
# tankskipene viser dem likevel: sporet slutter nær feltet i lav fart og
# kommer tilbake mange timer senere (skipet lå stille og laster), eller det
# kommer tilbake nær feltet etter å ha vært borte. Et slikt hull er et anløp
# når skipet etter gangtiden inn og ut var ved feltet minst MIN_LOAD_HOURS.
GAP_NEAR_KM = 6        # så nær lasteinnretningen er skipet på den ene siden av hullet
GAP_SLOW_KNOTS = 6     # og høyst så fort (et skip som seiler forbi, er ikke på vei inn)
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
             'surface': r[c['surface']], 'phase': r[c['phase']], 'kind': r[c['kind']], 'functions': r[c['functions']] or '', 'lon': r[c['lon']], 'lat': r[c['lat']]}
            for r in fb['rows'] if r[c['lat']] is not None and r[c['phase']] != 'REMOVED']
    return fields, facs


def load_ports():
    """Havner, kaier og ankerplasser. Kaier heter ofte bare «Kai 1», så de får
    navnet på havnen de hører til (quayportlocationid), ellers kommunen."""
    ports = []
    try:
        feats = call('GET', '/location/norway/all/geojson', retries=5)['features']
        names = {str(f['properties'].get('locationid')): f['properties'].get('locationnamenor') for f in feats}
        for f in feats:
            p = f['properties']
            if p.get('systemname') in ('HARBOUR', 'PORT_FACILITY', 'QUAY', 'ANCHORAGE') and f.get('geometry'):
                lon, lat = f['geometry']['coordinates'][:2]
                if not (lon or lat):
                    continue
                name = p.get('locationnamenor') or ''
                if p.get('systemname') != 'HARBOUR':
                    name = names.get(str(p.get('quayportlocationid') or '')) or p.get('councilname') or name
                ports.append({'name': name, 'lon': lon, 'lat': lat})
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


# ── BESØK ────────────────────────────────────────────────────────────────────
# Loggen holder ikke posisjoner, bare oppholdene: per felt «open» (skip som
# er, eller nylig var, innenfor radiusen: [start, slutt, nærmeste km, stoppet])
# og «visits» (avsluttede opphold som er besøk: [mmsi, start, slutt, km]).
# Hver ny posisjon innenfor radiusen forlenger skipets åpne opphold, eller
# starter et nytt når det er gått mer enn GAP_HOURS siden forrige.

def is_visit(seg):
    """Minst MIN_VISIT_MIN minutter innenfor radiusen, og enten et stopp eller
    minst LONG_VISIT_MIN minutter. Ett enkelt punkt (skip i kanten av
    radiusen) eller et skip som seiler gjennom feltet, er ikke et besøk."""
    return seg[1] - seg[0] >= MIN_VISIT_MIN and (seg[3] or seg[1] - seg[0] >= LONG_VISIT_MIN)


def feed(flog, m, rows, centres, radius):
    """Legger skipets posisjoner [min, lon, lat, sog] inn i oppholdene."""
    opened, closed = flog['open'], flog['visits']
    for q in sorted(rows, key=lambda r: r[0]):
        d = min(km(q[1], q[2], c[0], c[1]) for c in centres)
        if d > radius:
            continue
        slow = q[3] is not None and q[3] < STOP_KNOTS
        seg = opened.get(m)
        if seg and q[0] <= seg[1]:
            continue
        if seg and q[0] - seg[1] <= GAP_HOURS * 60:
            seg[1] = q[0]
            seg[2] = min(seg[2], round(d, 2))
            seg[3] = seg[3] or slow
        else:
            if seg and is_visit(seg):
                closed.append([m, seg[0], seg[1], seg[2]])
            opened[m] = [q[0], q[0], round(d, 2), slow]


def close_stale(flog, until_min):
    """Avslutter opphold der skipet ikke har vært innenfor radiusen på GAP_HOURS."""
    for m, seg in list(flog['open'].items()):
        if until_min - seg[1] > GAP_HOURS * 60:
            if is_visit(seg):
                flog['visits'].append([m, seg[0], seg[1], seg[2]])
            del flog['open'][m]


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


COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def sea_area(lon, lat):
    """Grovt farvann for et punkt, til teksten der skipet forlot dekningen."""
    if lat < 59.5 and lon > 7:
        return 'Skagerrak'
    if lat < 62:
        return 'North Sea'
    if lat < 70.5:
        return 'Norwegian Sea'
    return 'Barents Sea'


def next_stop_or_exit(rows, away_from, radius, end_min):
    """Første stopp etter avgang, eller stedet der skipet forlot Kystverkets
    AIS-dekning. Posisjonene kommer fra AIS-sporene, og et skip som ligger
    stille gir ingen spor, så et hull på over EXIT_GAP_MIN minutter er et
    stopp når skipet er på samme sted etterpå (under GAP_SAME_KM), og et
    utseiling av dekningen når det er langt unna, eller når sporet slutter i
    fart. Etter et utseiling er et stopp trolig på returen, så det regnes ikke.
    ('stop', lon, lat) | ('exit', lon, lat, retning) | None."""
    if not rows:
        return None
    upto, gap_stop = len(rows), None
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if b[0] - a[0] > EXIT_GAP_MIN:
            upto = i
            if km(a[1], a[2], b[1], b[2]) < GAP_SAME_KM and \
                    min(km(a[1], a[2], c[0], c[1]) for c in away_from) > radius + 2:
                gap_stop = (a[1], a[2])
            break
    stop = last_stop(rows[:upto], False, True, away_from, radius)
    if stop:
        return ('stop', stop[0], stop[1])
    if gap_stop:
        return ('stop', gap_stop[0], gap_stop[1])
    q = rows[upto - 1]
    ended = upto == len(rows) and end_min - q[0] > EXIT_GAP_MIN
    if ended and q[3] is not None and q[3] < STOP_KNOTS and \
            min(km(q[1], q[2], c[0], c[1]) for c in away_from) > radius + 2:
        return ('stop', q[1], q[2])   # sporet slutter mens skipet ligger stille
    gone = upto < len(rows) or ended
    if not gone:
        return None
    q = rows[upto - 1]
    p = next((r for r in reversed(rows[:upto - 1]) if q[0] - r[0] >= 120), rows[0])
    dy, dx = q[2] - p[2], (q[1] - p[1]) * math.cos(math.radians(q[2]))
    heading = COMPASS[round(math.degrees(math.atan2(dx, dy)) % 360 / 45) % 8] if (dx or dy) else ''
    return ('exit', q[1], q[2], heading)


def approach_min(q, centres, radius):
    """Minutter skipet i posisjon q trenger inn til radiusen (0 innenfor)."""
    d = min(km(q[1], q[2], c[0], c[1]) for c in centres)
    return max(0.0, (d - radius) / (max(q[3] or 0, MIN_APPROACH_KNOTS) * 1.852) * 60)


def gap_calls(m, seq, setups):
    """Anløp i hull i skipets spor (se GAP_NEAR_KM). seq: [min, lon, lat, sog]
    sortert, eventuelt med forrige kjørings siste posisjon først. Gir
    [(feltnøkkel, [mmsi, start, slutt, km, innretning])]."""
    out = []
    for a, b in zip(seq, seq[1:]):
        if b[0] - a[0] < MIN_LOAD_HOURS * 60:
            continue
        near = []
        for st in setups:
            da = min(km(a[1], a[2], c[0], c[1]) for c in st['centres'])
            db = min(km(b[1], b[2], c[0], c[1]) for c in st['centres'])
            ok_a = da <= GAP_NEAR_KM and (a[3] or 0) <= GAP_SLOW_KNOTS
            ok_b = db <= GAP_NEAR_KM and (b[3] or 0) <= GAP_SLOW_KNOTS
            near.append((st, da, db, ok_a, ok_b))
        hits = [x for x in near if x[3] or x[4]]
        if len(hits) != 1:
            continue    # ingen felt, eller skipet var ved to felt (lastet trolig begge steder)
        st, da, db, ok_a, ok_b = hits[0]
        # Den andre siden av hullet ved et annet felt: skipet kan ha lastet der.
        if any(x[0] is not st and min(x[1], x[2]) <= GAP_NEAR_KM for x in near):
            continue
        s_ = a[0] + approach_min(a, st['centres'], st['radius'])
        e_ = b[0] - approach_min(b, st['centres'], st['radius'])
        if e_ - s_ < MIN_LOAD_HOURS * 60:
            continue
        q = a if ok_a and (not ok_b or da <= db) else b
        fac = min(st['load'], key=lambda f: km(q[1], q[2], f['lon'], f['lat']))['name']
        out.append((st['key'], [m, int(s_), int(e_), round(min(da, db), 2), fac]))
    return out


def arrival_departure(before, after, s, e, centres, radius):
    """(ankomst, avgang) i minutter fra skipets spor rundt besøket [s, e].
    before/after: [min, lon, lat, sog] før s og etter e, sortert. Ankomst:
    siste posisjon utenfor radiusen før s, pluss gangtiden inn til radiusen,
    eller neste posisjon i sporet om den kommer før; aldri senere enn s.
    Avgang tilsvarende etter e. None når sporet ikke har posisjoner utenfor
    radiusen på den siden."""
    def dist(q):
        return min(km(q[1], q[2], c[0], c[1]) for c in centres)

    def travel(q):   # minutter mellom q og radiusen
        return (dist(q) - radius) / (max(q[3] or 0, MIN_APPROACH_KNOTS) * 1.852) * 60
    arr = dep = None
    out = [i for i, q in enumerate(before) if q[0] < s and dist(q) > radius]
    if out:
        q = before[out[-1]]
        cand = [s, q[0] + travel(q)] + [r[0] for r in before[out[-1] + 1:out[-1] + 2] if r[0] < s]
        arr = int(min(cand))
    out = [i for i, q in enumerate(after) if q[0] > e and dist(q) > radius]
    if out:
        q = after[out[0]]
        cand = [e, q[0] - travel(q)] + [r[0] for r in after[max(0, out[0] - 1):out[0]] if r[0] > e]
        dep = int(max(cand))
    return arr, dep


def ship_rows(mmsi, start, end):
    """Skipets posisjoner [min, lon, lat, sog]. Databasen tåler bare korte
    vinduer, så lengre perioder hentes i biter på CHUNK_DAYS dager."""
    out, t = [], start
    while t < end:
        t1 = min(t + timedelta(days=CHUNK_DAYS), end)
        data = call('POST', '/ais/positions/for-mmsis-time', {
            'mmsiIds': [int(mmsi)], 'start': t.strftime('%Y%m%d%H%M'), 'end': t1.strftime('%Y%m%d%H%M'),
        })['data'] or []
        out += [[minutes(parse_iso(r[1])), r[2], r[3], r[5]] for r in data if r[2] is not None and r[3] is not None]
        t = t1
    return sorted(out, key=lambda r: r[0])


def fetch_ships(mmsis, ships):
    """Navn, type og lengde fra static-messages; siste gyldige registrering vinner."""
    end = datetime.now(timezone.utc).replace(tzinfo=None, day=1, hour=0, minute=0, second=0, microsecond=0)
    todo = list(mmsis)
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
            beam = (r.get('dimensionToPort') or 0) + (r.get('dimensionToStarboard') or 0)
            ships[k] = {'name': (r.get('name') or '').strip(), 'type': r.get('shipType'), 'len': length or None,
                        'beam': beam or None, 'draught': r.get('draught') or None, 'imo': r.get('imoNumber') or None}


# Kategorier. AIS-typen er ofte 0 eller 90, så lange skip av ukjent type regnes
# som tankskip (skytteltankerne er 250–280 m; rørleggere og kranfartøy er
# gjerne under 220 m).
def category(ship):
    if not ship:
        return 'unknown'
    t, n = ship.get('type') or 0, ship.get('len') or 0
    if 80 <= t <= 89 or (t in (0, 90) and n >= 220):
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


# ── KAPASITET OG LAST ────────────────────────────────────────────────────────
def tanker_spec(ship, specs):
    """{dwt, dwtSrc, cap} for et tankskip: oppgitt dødvekt fra tanker_specs.json,
    ellers anslått fra målene. cap = lastekapasitet i fat."""
    given = specs.get(str(ship.get('imo') or '')) or {}
    dwt, src = given.get('dwt'), 'stated'
    if not dwt and ship.get('len') and ship.get('beam') and ship.get('draught'):
        dwt, src = round(DWT_K * ship['len'] * ship['beam'] * ship['draught'], -2), 'estimated'
    if not dwt:
        return {}
    cap = given.get('bbl') or (round(given['m3'] * BBL_PER_M3, -3) if given.get('m3') else None) \
        or round(dwt * CARGO_SHARE / OIL_DENSITY * BBL_PER_M3, -3)
    return {'dwt': dwt, 'dwtSrc': src, 'cap': cap, **({'source': given['source']} if given.get('source') else {})}


def cargo_bbl(ship, spec, draught):
    """Anslått last i fat ved en gitt dypgang, eller None."""
    if not (spec.get('dwt') and draught and ship.get('draught') and ship.get('len') and ship.get('beam')):
        return None
    tonnes = CARGO_SHARE * spec['dwt'] - max(0.0, ship['draught'] - draught) * WP_K * ship['len'] * ship['beam'] * SEAWATER
    return max(0, round(tonnes / OIL_DENSITY * BBL_PER_M3, -3))


def realtime(log, cfg):
    """Dypgang og destinasjon for de kjente tankskipene fra sanntidsdataene,
    lagret i tanker_obs.json når de endres. Kjøres hver tredje time."""
    excluded = {str(k) for k in (cfg.get('excludeShips') or {})}
    want = {m for m, sh in (log.get('ships') or {}).items() if category(sh) == 'tanker' and m not in excluded}
    feats = call('GET', '/ais/realtime/geojson')['features']
    obs = load_json(OBS, {'ships': {}})
    cut = (datetime.now(timezone.utc) - timedelta(days=OBS_DAYS)).strftime('%Y-%m-%dT%H:%M')
    changed = 0
    for f in feats:
        p = f.get('properties') or {}
        m = str(p.get('mmsi'))
        if m not in want or not p.get('draught'):
            continue
        t = (p.get('date_time_utc') or '')[:16]
        dest = ' '.join(str(p.get('destination') or '').split())
        rows = obs['ships'].setdefault(m, [])
        if rows and rows[-1][1] == p['draught'] and rows[-1][2] == dest:
            continue
        rows.append([t, p['draught'], dest])
        changed += 1
    for m in list(obs['ships']):
        obs['ships'][m] = [r for r in obs['ships'][m] if r[0] >= cut]
    obs['updated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    if changed:
        write_json(OBS, obs, indent=0)
    print(f'  sanntid: {len(want)} tankskip følges, {changed} nye observasjoner')


# Lastesteder utenfor Sodirs feltliste som tankskipene melder som destinasjon,
# og ord som betyr felt eller lagerskip.
FOREIGN_FIELDS = {'MARINER', 'PENGUINS', 'CLAIR', 'SCHIEHALLION', 'FOINAVEN', 'CAPTAIN', 'GRYPHON', 'ALBA', 'KRAKEN'}
FIELD_WORDS = {'FPSO', 'FSO', 'FPU', 'FSU', 'FIELD', 'OF'}


def dest_words(text):
    """Store bokstaver, æ/ø/å skrevet om (ÅSGARD = AASGARD), og bare ord."""
    t = (text or '').upper().replace('Å', 'AA').replace('Ø', 'O').replace('Æ', 'AE')
    return ' ' + re.sub(r'[^A-Z0-9]+', ' ', t).strip() + ' '


def loading_dest(dest, names):
    """Destinasjonen er et felt eller lastested (skipet er på vei for å laste):
    et feltnavn fra Sodir som eget ord (ALVHEIM, NO AASGARD, JOTUN / BALDER),
    et utenlandsk lastefelt (MARINER), et ord som FPSO eller OF (DRAUGEN OF),
    eller en offshorekode som begynner på XZ (XZBAL). names: dest_words(feltnavn)."""
    d = dest_words(dest)
    return d.strip().startswith('XZ') or any(n in d for n in names) \
        or any(f' {w} ' in d for w in FOREIGN_FIELDS | FIELD_WORDS)


def laden_after(obs_rows, arrived, left, until, names):
    """Dypgang og destinasjon like etter avgang, som 'YYYY-MM-DDTHH:MM':
    dypgangen som gjaldt ved avgang (meldt etter ankomst) og den som ble meldt
    innen LADEN_HOURS etter avgang og før neste anløp (until). Den største
    gjelder; meldinger med et felt som destinasjon regnes ikke (se
    loading_dest). (None, None) når ingen passer."""
    stop = min(until, iso(minutes(parse_iso(left + 'Z')) + LADEN_HOURS * 60)[:16])
    before = [r for r in obs_rows if arrived <= r[0] <= left]
    rows = before[-1:] + [r for r in obs_rows if left < r[0] <= stop]
    rows = [r for r in rows if not loading_dest(r[2], names)]
    if not rows:
        return None, None
    best = max(rows, key=lambda r: r[1])
    return best[1], next((r[2] for r in rows if r[2]), None)


# ── HOVEDLØP ─────────────────────────────────────────────────────────────────
def main():
    if '--realtime' in sys.argv:
        realtime(load_json(LOG, {'fields': {}}), load_json(CFG, {}))
        return
    rebuild = '--rebuild' in sys.argv
    reroute = '--reroute' in sys.argv   # slår opp From/To på nytt for alle besøk
    deadline = time.time() + MAX_SECONDS
    cfg = load_json(CFG, {'fields': []})
    fields, facs = load_fields_and_facilities()
    by_name = {v.upper(): k for k, v in fields.items()}
    log = load_json(LOG, {'fields': {}})
    log.setdefault('ships', {})
    log.setdefault('tracks', {})       # per tankskip: hvor langt sporet er lest, og siste posisjon
    log.setdefault('trackCalls', {})   # per felt: anløp funnet i hull i sporene [mmsi, start, slutt, km, innretning]
    excluded = {str(k) for k in (cfg.get('excludeShips') or {})}
    out = load_json(OUT, {'fields': {}})
    out_fields = {}
    ports = None
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)

    # Oppsett per felt: innretninger, senter, radius og boks.
    setups = []
    for fc in cfg.get('fields', []):
        fid = by_name.get(str(fc['name']).upper())
        if fid is None:
            print(f"  ! ukjent felt i shipping_fields.json: {fc['name']}", file=sys.stderr)
            continue
        # Ilandføringer og anlegg på land (Goliat: Hyggevannet) er ikke på feltet.
        own = [f for f in facs if f['fieldId'] == fid and f['phase'] == 'IN SERVICE'
               and not any(w in (f['kind'] or '') for w in ('LAND', 'ONSHORE'))]
        surf = [f for f in own if f['surface']] or own
        if not surf:
            print(f"  ! {fc['name']}: ingen innretninger med posisjon", file=sys.stderr)
            continue
        # Tankskip laster ved innretninger med lastefunksjon (Sodir: OFFLOADING,
        # eller lastesystem/-bøye); uten slike, ved overflateinnretningene.
        # Havbunnsrammene er ikke med: tankskip som venter i området, ligger
        # ofte nær dem.
        load = [f for f in own if 'OFFLOADING' in f['functions'] or 'LOADING' in (f['kind'] or '')] or surf
        centres = [(f['lon'], f['lat']) for f in load]
        centre = (sum(c[0] for c in centres) / len(centres), sum(c[1] for c in centres) / len(centres))
        radius = float(fc.get('radiusKm') or RADIUS_KM)
        # Boksen dekker alle innretningene med radius og litt margin.
        reach = max(km(centre[0], centre[1], c[0], c[1]) for c in centres) + radius + KEEP_MARGIN_KM
        box = bbox(centre, reach)
        key = str(fid)
        # Endres innretningene eller radiusen, hentes feltet på nytt.
        sig = f"{radius}|" + ';'.join(f'{c[0]:.4f},{c[1]:.4f}' for c in sorted(centres))
        if (log['fields'].get(key) or {}).get('sig') != sig:
            log['fields'][key] = {'until': None, 'sig': sig}
            log['tracks'] = {}   # sporene leses på nytt, så hullene finnes mot de nye innretningene
            log['trackCalls'] = {}
        flog = log['fields'][key]
        flog.setdefault('open', {})
        flog.setdefault('visits', [])
        # Eldre logger hadde posisjonene; de gjøres om til opphold én gang.
        if 'pts' in flog:
            for m, pts in flog.pop('pts').items():
                feed(flog, m, pts, centres, radius)
        setups.append({'fc': fc, 'fid': fid, 'key': key, 'own': own, 'surf': surf, 'load': load, 'centre': centre,
                       'centres': centres, 'radius': radius, 'box': box, 'flog': flog})

    # 1. Nye posisjoner, flere felt samtidig.
    def fetch_field(st):
        fc, flog = st['fc'], st['flog']
        t = parse_iso(flog['until']) if flog['until'] else DATA_START
        while t < now and time.time() < deadline:
            t1 = min(t + timedelta(days=CHUNK_DAYS), now)
            try:
                got = fetch_box(st['box'], t, t1)
            except Busy as exc:
                print(f'  ! {fc["name"]} {t:%Y-%m-%d}: {exc}', file=sys.stderr)
                return
            for m, rows in got.items():
                feed(flog, m, rows, st['centres'], st['radius'])
            flog['until'] = t1.strftime('%Y-%m-%dT%H:%M:%SZ')
            print(f'  {fc["name"]}: {t:%Y-%m-%d}–{t1:%Y-%m-%d}, {sum(len(v) for v in got.values())} posisjoner, {len(got)} skip', flush=True)
            t = t1
    if not rebuild:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(fetch_field, setups))
        write_json(LOG, log, indent=0)

    # 1b. Sporene til de kjente tankskipene, for anløp boksen ikke så (gap_calls).
    # Leses videre fra der forrige kjøring slapp, med høyst halve tiden som er igjen.
    if not rebuild and setups:
        tdead = time.time() + (deadline - time.time()) / 2
        tankers = sorted(m for m, sh in log['ships'].items() if category(sh) == 'tanker' and m not in excluded)

        def read_track(m):
            cur = log['tracks'].setdefault(m, {'until': None, 'last': None})
            t = parse_iso(cur['until']) if cur['until'] else DATA_START
            while t < now and time.time() < tdead:
                t1 = min(t + timedelta(days=CHUNK_DAYS), now)
                try:
                    rows = ship_rows(m, t, t1)
                except Busy as exc:
                    print(f'  ! spor {m}: {exc}', file=sys.stderr)
                    return
                found = gap_calls(m, ([cur['last']] if cur['last'] else []) + rows, setups)
                for key, call_ in found:
                    have = log['trackCalls'].setdefault(key, [])
                    if not any(c[0] == call_[0] and c[1] == call_[1] for c in have):
                        have.append(call_)
                if rows:
                    cur['last'] = rows[-1]
                cur['until'] = t1.strftime('%Y-%m-%dT%H:%M:%SZ')
                t = t1
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(read_track, tankers))
        behind = sum(1 for m in tankers if (log['tracks'][m]['until'] or '') < (now - timedelta(days=1)).strftime('%Y-%m-%dT%H'))
        print(f"  spor: {len(tankers)} tankskip, {sum(len(v) for v in log['trackCalls'].values())} anløp fra hull i sporene"
              + (f', {behind} skip ikke lest helt fram ennå' if behind else ''))
        write_json(LOG, log, indent=0)

    # 2. Besøk og 3. skip per felt.
    for st in setups:
        flog = st['flog']
        st['prev'] = (out.get('fields') or {}).get(st['key']) or {}
        st['until_min'] = minutes(parse_iso(flog['until'])) if flog['until'] else 0
        close_stale(flog, st['until_min'])
        flog['visits'].sort(key=lambda v: v[1])
        visits = [{'m': m, 's': s_, 'e': e, 'd': d} for m, s_, e, d in flog['visits']]
        visits += [{'m': m, 's': g[0], 'e': g[1], 'd': g[2]} for m, g in flog['open'].items() if is_visit(g)]
        # Anløp fra hull i sporene (bare til og med det boksen er lest, så et
        # skip som ligger der nå, kommer med fra boksen som pågående).
        visits += [{'m': c[0], 's': c[1], 'e': c[2], 'd': c[3], 'tfac': c[4]}
                   for c in log['trackCalls'].get(st['key'], []) if c[2] <= st['until_min']]
        visits.sort(key=lambda v: v['s'])
        # Bare tankskip er med. Skipsregisteret slås opp for skip vi ikke
        # kjenner (felles for alle felt, i loggen); besøk av andre skip fjernes
        # fra loggen, mens skip uten registrering ennå (nye denne måneden)
        # beholdes til de kan slås opp.
        ships = log['ships']
        if not rebuild:
            # Tankskip registrert før bredde og dypgang ble lagret, slås opp igjen.
            stale = {m for m in ships if category(ships[m]) == 'tanker' and 'beam' not in ships[m]}
            fetch_ships(sorted(({v['m'] for v in visits} - set(ships)) | stale), ships)
        flog['visits'] = [v for v in flog['visits'] if v[0] not in ships or category(ships[v[0]]) == 'tanker']
        # Lagerskip som ligger fast på feltet (Åsgard C, Njord Bravo, Heidrun FSU)
        # og andre unntak står i shipping_fields.json → excludeShips.
        st['visits'] = [v for v in visits if v['m'] in ships and category(ships[v['m']]) == 'tanker'
                        and v['m'] not in excluded]
        # Samme skip på samme felt med under SAME_SHIP_HOURS timer imellom er ett
        # anløp: tankskipet går til side og venter, og kommer tilbake for å laste.
        merged = []
        for v in sorted(st['visits'], key=lambda v: (v['m'], v['s'])):
            cur = merged[-1] if merged else None
            if cur and cur['m'] == v['m'] and v['s'] - cur['e'] <= SAME_SHIP_HOURS * 60:
                cur['e'] = max(cur['e'], v['e'])
                cur['d'] = min(cur['d'], v['d'])
            else:
                merged.append(dict(v))
        st['visits'] = sorted(merged, key=lambda v: v['s'])
        st['ships'] = ships
        for v in st['visits']:
            v['open'] = st['until_min'] - v['e'] < 60   # skipet er (trolig) der ennå
        # Korte opphold er ikke lasting: tankskipet går inn i radiusen mens et
        # annet laster, eller passerer sakte. Pågående opphold beholdes.
        st['visits'] = [v for v in st['visits'] if v['open'] or v['e'] - v['s'] >= MIN_LOAD_HOURS * 60 or v['d'] <= LOAD_NEAR_KM]
        prev_rows = [] if reroute else st['prev'].get('visits') or []
        # Anløp som tidligere er slått sammen fordi skipet ventet nær feltet.
        st['joins'] = set() if reroute else {tuple(j) for j in st['prev'].get('joins') or []}
        # Par som er sjekket mot sporet og ikke slått sammen (skipet losset imellom).
        st['apart'] = set() if reroute else {tuple(j) for j in st['prev'].get('apart') or []}
        # Tomme fra/til funnet med et kortere vindu slås opp på nytt.
        retry = (st['prev'].get('routeDays') or 4) < ROUTE_DAYS
        keep = lambda p: p if not (retry and p and not p[1]) else None
        old_from = {f"{r[0]}|{r[1]}": keep(r[5]) for r in prev_rows}
        redo_to = (st['prev'].get('toVersion') or 1) < TO_VERSION   # ny regel for «to»: slå opp alle på nytt
        old_to = {} if redo_to else {f"{r[0]}|{r[2]}": keep(r[6]) for r in prev_rows if not r[4]}
        joined_e = {(j[0], j[1]) for j in st['joins']}
        joined_s = {(j[0], j[2]) for j in st['joins']}
        for v in st['visits']:
            v['from'] = old_from.get(f"{v['m']}|{iso(v['s'])}")
            v['to'] = old_to.get(f"{v['m']}|{iso(v['e'])}")
            # Fra/til midt i et sammenslått anløp trengs ikke på nytt.
            if (v['m'], iso(v['s'])) in joined_s:
                v['from'] = ['join', '']
            if (v['m'], iso(v['e'])) in joined_e:
                v['to'] = ['join', '']

    # 4. Hvor skipet kom fra og dro til, for besøkene som mangler det.
    # Gjenbrukes fra forrige kjøring; slås opp for flere skip samtidig.
    jobs = [(st, v) for st in setups for v in st['visits']
            if v['from'] is None or (v['to'] is None and not v['open'])]
    if jobs and not rebuild and time.time() < deadline:
        ports = load_ports()
        if not ports:
            print('  ! uten havnelista slås ikke fra/til opp denne gangen', file=sys.stderr)
            jobs = []

        def route(job):
            st, v = job
            if time.time() >= deadline:
                return
            try:
                if v['from'] is None:
                    a = from_minutes(v['s'])
                    stop = last_stop(ship_rows(v['m'], a - timedelta(days=ROUTE_DAYS), a), True, False, st['centres'], st['radius'])
                    v['from'] = place_name(stop[0], stop[1], facs, ports) if stop else ['', '']
                if v['to'] is None and not v['open']:
                    b = from_minutes(v['e'])
                    if (now - b).days >= 1:
                        end = min(b + timedelta(days=TO_DAYS), now)
                        res = next_stop_or_exit(ship_rows(v['m'], b, end), st['centres'], st['radius'], minutes(end))
                        if res and res[0] == 'stop':
                            v['to'] = place_name(res[1], res[2], facs, ports)
                        elif res:
                            v['to'] = ['exit', f"{res[2]:.2f}°N {abs(res[1]):.2f}°{'E' if res[1] >= 0 else 'W'}", res[3], sea_area(res[1], res[2])]
                        else:
                            v['to'] = ['', ''] if (now - b).days >= TO_DAYS else None
            except Busy as exc:
                print(f'  ! rute {v["m"]}: {exc}', file=sys.stderr)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(route, jobs))
        print(f'  ruter: {len(jobs)} besøk manglet fra/til, {sum(1 for _, v in jobs if v["from"] is None or (v["to"] is None and not v["open"]))} mangler fortsatt')

    # 5. Lå skipet og ventet til sjøs nær feltet mellom to anløp (forrige
    # anløps «to» og nesteanløps «from» er stopp til sjøs under WAIT_NEAR_KM
    # fra feltet), eller var det aldri lenger enn WAIT_FAR_KM unna ifølge
    # sporet, er det ett anløp: skipet gikk til side eller lå i le, eller
    # AIS-sporet falt ut en stund. Sammenslåtte par huskes i shipping.json →
    # joins, par som er sjekket og holdt adskilt i → apart.
    for st in setups:
        def waiting(p):
            if not p or p[0] != 'sea':
                return False
            lat, lon = p[1].split('°N ')
            lon = float(lon[:-2]) * (1 if lon.endswith('E') else -1)
            return km(lon, float(lat), *st['centre']) <= WAIT_NEAR_KM
        out_v = []
        for v in sorted(st['visits'], key=lambda v: (v['m'], v['s'])):
            cur = out_v[-1] if out_v else None
            if cur and cur['m'] == v['m'] and v['s'] - cur['e'] <= WAIT_DAYS * 1440:
                key = (v['m'], iso(cur['e']), iso(v['s']))
                if key not in st['joins'] and key not in st['apart'] and not (waiting(cur['to']) and waiting(v['from'])) \
                        and not rebuild and time.time() < deadline:
                    try:
                        rows = ship_rows(v['m'], from_minutes(cur['e']), from_minutes(v['s']))
                    except Busy as exc:
                        print(f'  ! mellom anløp {v["m"]}: {exc}', file=sys.stderr)
                        rows = None
                    if rows:   # uten posisjoner vet vi ikke, og anløpene holdes adskilt (slås opp igjen)
                        far = max(km(r[1], r[2], *st['centre']) for r in rows)
                        # Et hull i sporet der skipet var i fart før hullet, er trolig en
                        # utseiling av dekningen (Maran Scott fra Balder vestover mot
                        # britisk sektor): hvor langt det gikk, vet vi ikke.
                        left = any(b[0] - a[0] > EXIT_GAP_MIN and (a[3] or 0) >= STOP_KNOTS
                                   for a, b in zip(rows, rows[1:])) or \
                            (rows[-1][3] or 0) >= STOP_KNOTS and v['s'] - rows[-1][0] > EXIT_GAP_MIN
                        (st['joins'] if far <= WAIT_FAR_KM and not left else st['apart']).add(key)
                if key in st['joins'] or (waiting(cur['to']) and waiting(v['from'])):
                    st['joins'].add(key)
                    cur.update(e=v['e'], d=min(cur['d'], v['d']), open=v['open'], to=v['to'])
                    continue
            out_v.append(dict(v))
        st['visits'] = sorted(out_v, key=lambda v: v['s'])

    # 5b. Ankomst og avgang fra skipets eget spor (se arrival_departure).
    # Gjenbrukes fra forrige kjøring; aldri før forrige anløp av samme skip
    # sluttet eller etter neste begynte.
    for st in setups:
        prev_rows = [] if reroute or (st['prev'].get('timesVersion') or 0) < TIMES_VERSION else st['prev'].get('visits') or []
        old_a = {f"{r[0]}|{r[1]}": r[11] for r in prev_rows if len(r) > 12}
        old_d = {f"{r[0]}|{r[2]}": r[12] for r in prev_rows if len(r) > 12 and not r[4]}
        for v in st['visits']:
            k_a, k_d = f"{v['m']}|{iso(v['s'])}", f"{v['m']}|{iso(v['e'])}"
            v['arr'] = old_a.get(k_a) or '?'
            v['dep'] = (old_d.get(k_d) or '?') if not v['open'] else None
        mine = sorted(st['visits'], key=lambda v: (v['m'], v['s']))
        for a, b in zip(mine, mine[1:]):
            if a['m'] == b['m']:
                a['next_s'], b['prev_e'] = b['s'], a['e']
    jobs = [(st, v) for st in setups for v in st['visits'] if v['arr'] == '?' or v['dep'] == '?']
    if jobs and not rebuild and time.time() < deadline:
        def times(job):
            st, v = job
            if time.time() >= deadline:
                return
            try:
                a, b = from_minutes(v['s']), from_minutes(v['e'])
                lo = max(a - timedelta(days=TIMES_DAYS), from_minutes(v['prev_e']) if v.get('prev_e') else a - timedelta(days=TIMES_DAYS))
                hi = min(b + timedelta(days=TIMES_DAYS), from_minutes(v['next_s']) if v.get('next_s') else now, now)
                before = ship_rows(v['m'], lo, a) if v['arr'] == '?' else []
                after = ship_rows(v['m'], b, hi) if v['dep'] == '?' and hi > b else []
            except Busy as exc:
                print(f'  ! tider {v["m"]}: {exc}', file=sys.stderr)
                return
            arr, dep = arrival_departure(before, after, v['s'], v['e'], st['centres'], st['radius'])
            if v['arr'] == '?':
                v['arr'] = iso(max(arr, minutes(lo))) if arr is not None else iso(v['s'])
            if v['dep'] == '?':
                if dep is not None:
                    v['dep'] = iso(min(dep, minutes(hi)))
                elif hi >= b + timedelta(days=TIMES_DAYS) or v.get('next_s'):
                    v['dep'] = iso(v['e'])   # hele vinduet uten posisjon utenfor: tiden fra boksen
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(times, jobs))
        print(f'  tider: {len(jobs)} anløp manglet ankomst/avgang, {sum(1 for _, v in jobs if "?" in (v["arr"], v["dep"]))} mangler fortsatt')

    # 6. Hvilken lasteinnretning skipet lå ved, for felt med flere (Balder:
    # Balder FPU og Jotun FPSO; Åsgard: A og C). Lagrene er ulike store, og et
    # løft kan ikke være større enn lageret det lastes fra. Innretningen er den
    # med flest av skipets posisjoner innenfor radiusen under anløpet.
    # Gjenbrukes fra forrige kjøring.
    for st in setups:
        names = [f['name'] for f in st['load']]
        old_fac = {} if reroute else {f"{r[0]}|{r[1]}": r[10] for r in st['prev'].get('visits') or [] if len(r) > 10}
        for v in st['visits']:
            v['fac'] = names[0] if len(names) == 1 else old_fac.get(f"{v['m']}|{iso(v['s'])}")
    jobs = [(st, v) for st in setups for v in st['visits'] if v['fac'] is None and not v['open']]
    if jobs and not rebuild and time.time() < deadline:
        def which(job):
            st, v = job
            if time.time() >= deadline:
                return
            try:
                rows = ship_rows(v['m'], from_minutes(v['s']), from_minutes(v['e']))
            except Busy as exc:
                print(f'  ! innretning {v["m"]}: {exc}', file=sys.stderr)
                return
            hits = [0] * len(st['load'])
            for q in rows:
                d = [km(q[1], q[2], f['lon'], f['lat']) for f in st['load']]
                i = min(range(len(d)), key=d.__getitem__)
                if d[i] <= st['radius']:
                    hits[i] += 1
            if any(hits):
                v['fac'] = st['load'][max(range(len(hits)), key=hits.__getitem__)]['name']
            else:   # ingen posisjoner ved feltet (anløp fra hull i sporet): nærmeste ved hullet, ellers ukjent
                v['fac'] = v.get('tfac') or ''
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(which, jobs))
        print(f'  innretninger: {len(jobs)} anløp manglet, {sum(1 for _, v in jobs if v["fac"] is None)} mangler fortsatt')

    specs = (load_json(SPECS, {}).get('ships') or {})
    obs = load_json(OBS, {'ships': {}})['ships']
    # Feltnavn som ord, for å kjenne igjen destinasjoner som er lastesteder.
    field_words = {dest_words(n) for n in fields.values() if n and len(n) >= 4}
    for st in setups:
        rows_out = []
        by_ship = {}
        for v in st['visits']:
            by_ship.setdefault(v['m'], []).append(v)
        for v in st['visits']:
            sh = st['ships'].get(v['m']) or {}
            nxt = [w['s'] for w in by_ship[v['m']] if w['s'] > v['e']]
            until = iso(min([v['e'] + TO_DAYS * 1440] + nxt))[:16]
            arrived = v['arr'] if v.get('arr') not in (None, '?') else iso(v['s'])
            left = v['dep'] if v.get('dep') not in (None, '?') else iso(v['e'])
            dr, dest = (None, None) if v['open'] else \
                laden_after(obs.get(v['m'], []), arrived[:16], left[:16], until, field_words)
            rows_out.append([v['m'], iso(v['s']), iso(v['e']), v['d'], 1 if v['open'] else 0, v['from'], v['to'],
                             dr, cargo_bbl(sh, tanker_spec(sh, specs), dr), dest, v.get('fac'),
                             None if v.get('arr') == '?' else v.get('arr'), None if v.get('dep') == '?' else v.get('dep')])
        ship_ids = {r[0] for r in rows_out}
        out_fields[st['key']] = {
            'name': fields[st['fid']], 'centre': [round(st['centre'][0], 5), round(st['centre'][1], 5)], 'radiusKm': st['radius'],
            'facilities': [f['name'] for f in st['load']], 'nFacilities': len(st['load']),
            'from': DATA_START.strftime('%Y-%m-%d'), 'until': st['flog']['until'], 'routeDays': ROUTE_DAYS, 'toVersion': TO_VERSION, 'timesVersion': TIMES_VERSION,
            'storage': st['fc'].get('storage') or [],
            'columns': ['mmsi', 'start', 'end', 'minKm', 'open', 'from', 'to', 'draught', 'cargoBbl', 'dest', 'facility', 'arrived', 'left'],
            'visits': rows_out,
            'joins': sorted(list(j) for j in st['joins'] if j[0] in ship_ids),
            'apart': sorted(list(j) for j in st['apart'] if j[0] in ship_ids),
            'ships': {m: {**st['ships'][m], **tanker_spec(st['ships'][m], specs)} for m in sorted(ship_ids)},
        }
        print(f'  {st["fc"]["name"]}: {len(rows_out)} besøk, {len(ship_ids)} skip, data til {st["flog"]["until"]}')

    write_json(LOG, log, indent=0)
    write_json(OUT, {'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                     'source': 'Kystverket, Kystdatahuset AIS (NLOD 2.0)', 'fields': out_fields})


if __name__ == '__main__':
    main()
