"""
Henter Gasscos hendelser (UMM) fra umm.gassco.no og regner dem om til
nedetid per felt, i dager med tapt produksjon per måned. Skriver
data/gassco_events.json (arkiv) og data/gassco_downtime.json.

    pip install requests
    python fetch_gassco.py              # daglig: arkivets siste 120 dager
    python fetch_gassco.py --backfill   # hele arkivet fra 1. januar 2025

Historikk: Gassco holder bare om lag to år i arkivet sitt. Hendelsene lagres
derfor i data/gassco_events.json, ett oppslag per hendelse med siste
revisjon. Arkivet bare vokser: en hendelse som forsvinner hos Gassco,
beholdes. En nyere revisjon (også Dismissed) erstatter den lagrede. Er
arkivet tomt, leses Gasscos arkiv fra HISTORY_START som ved --backfill.

Kilder (krever at vilkårene på umm.gassco.no godtas, se accept_terms):
  · Forsiden: pågående og kommende hendelser i tre tabeller (Fields and
    Processing Plants, Exit terminals, Other). Melkøya ligger i Other.
  · /archive: avsluttede hendelser, filtrert på sluttdato. 20 rader per side.
Begge viser siste revisjon av hver hendelse. Samme hendelse kan stå begge
steder; da brukes den høyeste revisjonen. Kansellerte (Dismissed) tas ikke med.

Periode: fra januar 2025 (HISTORY_START) og så langt fram som hendelsene
går, så nedetiden kan legges oppå faktisk produksjon fra Sodir.

Beregning: Gassco melder bortfall over 5 MSm³/d per anlegg (en node der gass
går inn i transportsystemet) med teknisk, tilgjengelig og utilgjengelig
kapasitet. For hver node deles tiden opp der en hendelse starter eller
slutter. I hvert stykke er tapt andel = (største tekniske kapasitet − laveste
tilgjengelige kapasitet) / største tekniske kapasitet. Overlappende hendelser
summeres altså ikke: Gassco melder dem på to måter, og begge gir riktig svar
med laveste tilgjengelige kapasitet:
  · trinnvis: den nye hendelsens tekniske kapasitet er den tilgjengelige fra
    den første (Troll 18,2/132,2, så 39,4/114,2 → 57,6 borte)
  · erstattende: den nye oppgir hele bortfallet på nytt (Åsgard 5,2/29,2 og
    7,2/29,2 samtidig → 7,2 borte, ikke 12,4)
Tapt andel × timer / 24 gir dager med tapt produksjon. Tiden føres på
gassdøgnet (06:00–06:00 norsk tid), så et døgn som starter 31. kl. 06 hører
til den måneden. Planlagt/uplanlagt: bortfallet trappes ned fra teknisk
kapasitet via hendelsenes tilgjengelige kapasitet, og hvert trinn føres på
typen til hendelsen som gir det.

Prosessanlegg (Kollsnes, Kårstø, Nyhamna) og terminaler fordeles ikke på felt.
Gassco melder bortfallet på feltnodene også når årsaken er et anlegg (Nyhamna
helt stengt i mai 2027 står også som 100 % bortfall på Ormen Lange og Aasta
Hansteen), så fordeling ville telt det samme to ganger.

Volum: tapt kapasitet (MSm³/d) × dager gir tapt gassvolum i MSm³ per måned,
delt på planlagt og uplanlagt på samme måte.

Kobling til felt: data/gassco_aliases.json (fields, ignore), ellers samme
navn som i fields.json. En node kan dekke flere felt som leverer gjennom
den; Gassco skiller ikke på dem, så tallene gjelder noden og føres på feltet
med samme navn. Er en node koblet til flere felt, får hvert felt samme dager,
mens volumet fordeles etter feltenes andel av gassproduksjonen de siste 12
månedene (share), så det ikke telles flere ganger.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import requests

BASE = "https://umm.gassco.no"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "gassco_downtime.json")
ARCHIVE = os.path.join(DATA, "gassco_events.json")
HISTORY_START = (2025, 1)
DAILY_DAYS = 120  # daglig kjøring leser Gasscos arkiv så langt bakover
OSLO = ZoneInfo("Europe/Oslo")
GAS_DAY_START = timedelta(hours=6)
PAGE_SIZE = 20
MAX_PAGES = 100
PAUSE = 1.0  # sekunder mellom kall
UA = "ncs-insight/1.0 (+https://github.com/sheffielddivided/ncs-production)"


# ---------------------------------------------------------------- henting

def session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def accept_terms(s):
    """Godtar vilkårene (Terms & conditions) slik knappen «Accept» gjør.
    Gir sesjonscookien som resten av sidene krever."""
    s.get(f"{BASE}/disclaimer", timeout=60).raise_for_status()
    r = s.get(f"{BASE}/disclaimer/acceptDisclaimer", timeout=60)
    r.raise_for_status()
    if "/disclaimer" in r.url:
        raise RuntimeError("vilkårene ble ikke godtatt (sendt tilbake til /disclaimer)")
    return r.text


def get(s, path, params=None):
    time.sleep(PAUSE)
    r = s.get(f"{BASE}{path}", params=params, timeout=60)
    r.raise_for_status()
    if "/disclaimer" in r.url:
        raise RuntimeError(f"{path}: sesjonen ble sendt tilbake til vilkårssiden")
    return r.text


class TableParser(HTMLParser):
    """Leser alle <table class="msgTable"> til lister av {kolonne: tekst}."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._t = self._row = self._cell = None
        self._h2 = None
        self._title = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h2":
            self._h2 = ""
        elif tag == "table" and a.get("class") == "msgTable":
            self._t = {"title": self._title, "rows": []}
            self.tables.append(self._t)
        elif self._t is not None:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th"):
                self._cell = ""

    def handle_endtag(self, tag):
        if tag == "h2" and self._h2 is not None:
            self._title = " ".join(self._h2.split())
            self._h2 = None
        elif self._t is not None:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(" ".join(self._cell.split()))
                self._cell = None
            elif tag == "tr" and self._row is not None:
                self._t["rows"].append(self._row)
                self._row = None
            elif tag == "table":
                self._t = None

    def handle_data(self, d):
        if self._h2 is not None:
            self._h2 += d
        if self._cell is not None:
            self._cell += d


def parse_tables(html):
    p = TableParser()
    p.feed(html)
    out = []
    for t in p.tables:
        if not t["rows"]:
            continue
        hdr = t["rows"][0]
        rows = [dict(zip(hdr, r)) for r in t["rows"][1:] if len(r) >= len(hdr)]
        out.append((t["title"], rows))
    return out


def total_records(html):
    m = re.search(r"Total records:\s*(\d+)", html)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- tolking

ID_RE = re.compile(r"^(.*?)_*(\d{3})$")


def parse_time(s):
    # 2026-11-05T06:00:00+0100
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def to_event(row, table):
    mid = row.get("Message ID", "")
    m = ID_RE.match(mid)
    if not m:
        return None
    try:
        start = parse_time(row["Event Start"])
        stop = parse_time(row["Event Stop"])
    except (KeyError, ValueError):
        return None
    return {
        "id": m.group(1).rstrip("_"),
        "rev": int(m.group(2)),
        "asset": row.get("Affected Asset or Unit", "").strip(),
        "status": row.get("Event Status", ""),
        "type": row.get("Type of Unavailability", ""),
        "unit": row.get("Unit of Measurement", ""),
        "start": start,
        "stop": stop,
        "tech": num(row.get("Technical Capacity")),
        "unavail": num(row.get("Unavailable Capacity")),
        "table": table,
    }


def fetch_events(s, horizon_start):
    """Siste revisjon av hver hendelse på forsiden og i arkivet som slutter
    etter horizon_start."""
    events = {}

    def add(ev):
        if ev is None:
            return
        old = events.get(ev["id"])
        if old is None or ev["rev"] > old["rev"]:
            events[ev["id"]] = ev

    front = accept_terms(s)
    tables = parse_tables(front)
    if len(tables) < 3:
        raise RuntimeError(f"forsiden: fant {len(tables)} hendelsestabeller, ventet 3")
    for title, rows in tables:
        for r in rows:
            add(to_event(r, title))
    n_front = len(events)

    # Arkivet. Filteret tar norsk lokal tid uten sone (datetime-local).
    since = (horizon_start - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    params = {"eventEndFromDate": since}
    total = None
    n_arch = 0
    for page in range(1, MAX_PAGES + 1):
        html = get(s, "/archive", {**params, "page.page": page})
        if total is None:
            total = total_records(html)
        rows = [r for _, rs in parse_tables(html) for r in rs]
        for r in rows:
            add(to_event(r, "Past events"))
        n_arch += len(rows)
        if len(rows) < PAGE_SIZE or (total is not None and n_arch >= total):
            break
    if total is not None and n_arch < total:
        print(f"Advarsel: arkivet ga {n_arch} av {total} rader", file=sys.stderr)
    print(f"Hendelser: {n_front} fra forsiden, {n_arch} rader fra arkivet, "
          f"{len(events)} unike", file=sys.stderr)
    return list(events.values())


# ---------------------------------------------------------------- perioden

def last_production_month():
    with open(os.path.join(DATA, "production.json"), encoding="utf-8") as f:
        prod = json.load(f)
    today = datetime.now(OSLO)
    best = None
    for rows in prod["fields"].values():
        for r in rows:
            y, m = r[0], r[1]
            if m and (y, m) <= (today.year, today.month):
                if best is None or (y, m) > best:
                    best = (y, m)
    return best


def add_months(y, m, k):
    i = y * 12 + (m - 1) + k
    return i // 12, i % 12 + 1


# ---------------------------------------------------------------- arkiv

def ev_to_json(e):
    return {k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in e.items() if k not in ("id", "table")}


def ev_from_json(eid, d):
    e = dict(d, id=eid)
    e["start"] = datetime.fromisoformat(d["start"])
    e["stop"] = datetime.fromisoformat(d["stop"])
    return e


def load_archive():
    try:
        with open(ARCHIVE, encoding="utf-8") as f:
            a = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    return {eid: ev_from_json(eid, d) for eid, d in a.get("events", {}).items()}


def save_archive(events):
    out = {
        "note": "Arkiv over Gasscos hendelser (UMM) fra umm.gassco.no, siste revisjon per hendelse. "
                "Bygges av fetch_gassco.py og bare vokser; ikke rediger for hånd.",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events": {eid: ev_to_json(e) for eid, e in sorted(events.items())},
    }
    with open(ARCHIVE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


# ---------------------------------------------------------------- beregning

def gas_month_bounds(t):
    """Gassmåneden t (aware) hører til, og når den neste starter."""
    lt = (t.astimezone(OSLO) - GAS_DAY_START)
    y, m = lt.year, lt.month
    ny, nm = add_months(y, m, 1)
    nxt = datetime(ny, nm, 1, tzinfo=OSLO) + GAS_DAY_START
    return (y, m), nxt


def downtime_by_asset(events, start_ym):
    """{asset: {(y, m): [dager, planlagt, uplanlagt, volum, planlagt, uplanlagt]}}
    Volum i MSm³ (mcm) tapt i måneden."""
    horizon_t = datetime(*start_ym, 1, tzinfo=OSLO) + GAS_DAY_START
    by_asset = {}
    for ev in events:
        by_asset.setdefault(ev["asset"], []).append(ev)

    out = {}
    for asset, evs in by_asset.items():
        cuts = sorted({e["start"] for e in evs} | {e["stop"] for e in evs})
        acc = {}
        for a, b in zip(cuts, cuts[1:]):
            act = [e for e in evs if e["start"] <= a and e["stop"] >= b]
            if not act:
                continue
            tech = max(e["tech"] for e in act)
            if tech <= 0:
                continue
            # Tilgjengelig kapasitet per hendelse, høyest først. Bortfallet er
            # trappen fra tech ned til den laveste; hvert trinn får typen til
            # hendelsen som tar kapasiteten ned dit.
            steps = sorted(((max(0.0, e["tech"] - e["unavail"]), e["type"]) for e in act), reverse=True)
            level, lost, lost_pl = tech, 0.0, 0.0
            for avail, typ in steps:
                if avail < level:
                    lost += level - avail
                    if typ == "Planned":
                        lost_pl += level - avail
                    level = avail
            if lost <= 0:
                continue
            frac = min(1.0, lost / tech)
            planned = lost_pl / lost
            t = max(a, horizon_t)
            while t < b:
                ym, nxt = gas_month_bounds(t)
                end = min(b, nxt)
                dur = (end - t).total_seconds() / 86400
                d = frac * dur
                vol = min(lost, tech) * dur
                v = acc.setdefault(ym, [0.0] * 6)
                v[0] += d
                v[1] += d * planned
                v[2] += d * (1 - planned)
                v[3] += vol
                v[4] += vol * planned
                v[5] += vol * (1 - planned)
                t = end
        if acc:
            out[asset] = acc
    return out


# ---------------------------------------------------------------- felt

def gas_weights(ids):
    """Andel av gassproduksjonen de siste 12 månedene med tall, per felt.
    Brukes når en node er koblet til flere felt (Sleipner → Øst og Vest), så
    volumet fordeles i stedet for å telles på hvert felt. Like andeler hvis
    ingen av dem har produksjon."""
    with open(os.path.join(DATA, "production.json"), encoding="utf-8") as f:
        prod = json.load(f)["fields"]
    last = last_production_month()
    lo = add_months(*last, -11) if last else (0, 0)
    tot = {fid: sum(r[3] or 0 for r in prod.get(str(fid), [])
                    if r[1] and lo <= (r[0], r[1]) <= (last or (9999, 12)))
           for fid in ids}
    s = sum(v for v in tot.values() if v > 0)
    return {fid: (max(v, 0) / s if s > 0 else 1 / len(ids)) for fid, v in tot.items()}


def load_fields():
    with open(os.path.join(DATA, "fields.json"), encoding="utf-8") as f:
        fj = json.load(f)
    return {name.upper(): fid for fid, name in fj["rows"]}


def load_aliases():
    with open(os.path.join(DATA, "gassco_aliases.json"), encoding="utf-8") as f:
        a = json.load(f)
    return a.get("fields", {}), {x.strip() for x in a.get("ignore", [])}


def main():
    fy, fm = HISTORY_START
    last = last_production_month()
    hist_t = datetime(fy, fm, 1, tzinfo=OSLO)

    archive = load_archive()
    backfill = "--backfill" in sys.argv or not archive
    since = hist_t if backfill else max(hist_t, datetime.now(OSLO) - timedelta(days=DAILY_DAYS))
    print(f"Leser Gassco fra {since:%Y-%m-%d}{' (backfill)' if backfill else ''}; "
          f"{len(archive)} hendelser i arkivet", file=sys.stderr)

    s = session()
    n_new = n_rev = 0
    for e in fetch_events(s, since):
        old = archive.get(e["id"])
        if old is None:
            n_new += 1
        elif e["rev"] > old["rev"]:
            n_rev += 1
        if old is None or e["rev"] >= old["rev"]:
            e = dict(e)
            e.pop("table", None)
            archive[e["id"]] = e
    # Hendelser som sluttet før historikken starter, trengs ikke.
    archive = {k: e for k, e in archive.items() if e["stop"] > hist_t}
    save_archive(archive)
    print(f"Arkiv: {len(archive)} hendelser ({n_new} nye, {n_rev} nye revisjoner)", file=sys.stderr)

    events = list(archive.values())
    fields = load_fields()
    aliases, ignore = load_aliases()

    keep = []
    for e in events:
        if e["status"] == "Dismissed" or e["asset"] in ignore:
            continue
        if e["unit"] and e["unit"] != "mcm/d":
            print(f"Hopper over {e['id']}: enhet {e['unit']}", file=sys.stderr)
            continue
        if e["tech"] is None or e["unavail"] is None or e["stop"] <= e["start"]:
            continue
        # Ikke bortfall: økt kapasitet (0 eller negativ utilgjengelig) eller
        # teknisk kapasitet 0 (Sleipner 2026-324: -6,1/0,0).
        if e["unavail"] <= 0 or e["tech"] <= 0:
            continue
        keep.append(e)

    dt = downtime_by_asset(keep, (fy, fm))

    out_fields = {}
    unmatched = []
    to_ym = (fy, fm)
    for asset, months in sorted(dt.items()):
        names = aliases.get(asset) or [asset.upper()]
        ids = [fields[n.upper()] for n in names if n.upper() in fields]
        if not ids:
            unmatched.append(asset)
            continue
        w = gas_weights(ids) if len(ids) > 1 else {ids[0]: 1.0}
        for fid in ids:
            k = w[fid]
            rows = [[y, m, round(v[0], 2), round(v[1], 2), round(v[2], 2),
                     round(v[3] * k, 3), round(v[4] * k, 3), round(v[5] * k, 3)]
                    for (y, m), v in sorted(months.items()) if v[0] >= 0.005]
            if not rows:
                continue
            to_ym = max(to_ym, max((r[0], r[1]) for r in rows))
            out_fields[str(fid)] = {"asset": asset, "rows": rows}
            if k < 1:
                out_fields[str(fid)]["share"] = round(k, 3)

    if unmatched:
        print(f"Uten felt: {', '.join(unmatched)}", file=sys.stderr)

    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Gassco UMM - https://umm.gassco.no",
        "from": f"{fy}-{fm:02d}",
        "to": f"{to_ym[0]}-{to_ym[1]:02d}",
        "lastProductionMonth": f"{last[0]}-{last[1]:02d}" if last else None,
        "columns": ["year", "month", "days", "planned", "unplanned",
                    "volume", "volumePlanned", "volumeUnplanned"],
        "volumeUnit": "MSm3 per month (gas)",
        "fields": out_fields,
        "unmatched": unmatched,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"Skrev {OUT}: {len(out_fields)} felt, {out['from']}–{out['to']}", file=sys.stderr)


if __name__ == "__main__":
    main()
