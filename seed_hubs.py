"""
Utleder et forslag til hub-mapping (felt -> prosesseringshub) fra Sodirs
feltbeskrivelser, og skriver det til data/hubs.json.

Sodir koder ikke hub-relasjonen strukturert. Men Transport-beskrivelsen til
hvert felt navngir som regel innretningen bronnstrommen sendes til, f.eks.
"The well stream is routed by pipeline to the Alvheim FPSO". Scriptet slar opp
slike innretningsnavn i fasilitetsregisteret for a finne hvilket FELT de
tilhorer - det feltet er huben.

VIKTIG: dette er et ENGANGS-hjelpemiddel, ikke en del av den daglige pipelinen.
data/hubs.json er brukerstyrt masterdata. Kjorer du scriptet pa nytt, overskriver
det manuelle rettelser - derfor spor det for det gjor det, og bevarer som
standard alle oppforinger brukeren har bekreftet.

    pip install requests
    python seed_hubs.py            # skriv forslag, bevar bekreftede rader
    python seed_hubs.py --force    # overskriv alt, ogsa bekreftede
    python seed_hubs.py --dry-run  # vis hva som ville blitt skrevet
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

BASE = 'https://factmaps.sodir.no/api/rest/services/DataService/Data/FeatureServer'

FIELD_LAYER    = 7100
FACILITY_REL   = 6003   # facility_belongs_to_rel_hst
DESCRIPTION    = 7102   # field_description

OUT_FILE  = 'data/hubs.json'
PAGE_SIZE = 2000
TIMEOUT   = 120
RETRIES   = 4

# Tekst som betyr eksport/videretransport, ikke prosessering. Et feltnavn som
# etterfolges av et av disse ordene er en rorledning eller terminal - ikke en
# hub. Uten dette blir f.eks. "Troll Oil Pipeline II" tolket som hub TROLL.
EXPORT_SUFFIX = re.compile(
    r'^\s+(oil\s+)?(pipeline|pipelines|terminal|gas\s+pipeline|oil\s+pipeline|'
    r'transport\s+system|area|gassled)\b', re.I)

# Setninger som handler om hvor bronnstrommen prosesseres.
PROCESSING_HINT = re.compile(
    r'well\s*stream|for\s+processing|processed\s+(at|on|via)|routed|tied\s*back|'
    r'transported\s+(by\s+pipeline\s+)?to|sent\s+to', re.I)


def _get(url, params):
    last = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if 'error' in data:
                raise RuntimeError(data['error'].get('message', 'ukjent API-feil'))
            return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"Ga opp etter {RETRIES} forsok: {last}")


def fetch_all(layer, out_fields, where='1=1'):
    total = _get(f'{BASE}/{layer}/query',
                 {'f': 'json', 'where': where, 'returnCountOnly': 'true'}).get('count', 0)
    rows, offset = [], 0
    while offset < total:
        feats = _get(f'{BASE}/{layer}/query', {
            'f': 'json', 'where': where, 'outFields': out_fields,
            'returnGeometry': 'false', 'resultOffset': offset,
            'resultRecordCount': PAGE_SIZE, 'orderByFields': 'OBJECTID ASC',
        }).get('features', [])
        if not feats:
            break
        rows.extend(f.get('attributes', {}) for f in feats)
        offset += len(feats)
    return rows


def build_facility_index(field_names):
    """Innretningsnavn -> feltnavn, for innretninger som tilhorer et felt.

    Korte eller tvetydige navn hoppes over: de gir for mange falske treff i
    lopende tekst.
    """
    rows = fetch_all(FACILITY_REL, 'fclName,fclBelongsToName,fclBelongsToKind')
    idx = {}
    for r in rows:
        if (r.get('fclBelongsToKind') or '').upper() != 'FIELD':
            continue
        fac = (r.get('fclName') or '').strip()
        fld = (r.get('fclBelongsToName') or '').strip().upper()
        if len(fac) < 4 or fld not in field_names:
            continue
        # Bronnrammer og enkeltbronner ("25/4-L-4 H") navngir ikke huben.
        if re.match(r'^\d', fac):
            continue
        idx.setdefault(fac.upper(), fld)
    return idx


def find_host(text, own_name, field_names, facility_idx):
    """Finn feltet bronnstrommen sendes til. Returnerer (feltnavn, kilde) eller None."""
    if not text:
        return None

    # Vurder kun setninger som faktisk handler om prosessering/transport av
    # bronnstrommen, ikke om eksport av ferdig produkt.
    for sentence in re.split(r'(?<=[.;])\s+', text):
        if not PROCESSING_HINT.search(sentence):
            continue

        candidates = []

        # 1. Innretningsnavn ("Alvheim FPSO", "Statfjord C", "Ekofisk Centre")
        for fac, fld in facility_idx.items():
            for m in re.finditer(re.escape(fac), sentence, re.I):
                if EXPORT_SUFFIX.match(sentence[m.end():]):
                    continue
                if fld != own_name:
                    candidates.append((m.start(), fld, 'facility'))

        # 2. Rent feltnavn ("to the Ula field", "to Heidrun for processing")
        for name in field_names:
            for m in re.finditer(r'\b' + re.escape(name) + r'\b', sentence, re.I):
                if EXPORT_SUFFIX.match(sentence[m.end():]):
                    continue
                if name != own_name:
                    candidates.append((m.start(), name, 'field'))

        if candidates:
            # Siste treff i setningen er destinasjonen: "via the Vilje field to
            # the Alvheim FPSO" skal gi ALVHEIM, ikke VILJE.
            candidates.sort(key=lambda c: c[0])
            _, fld, src = candidates[-1]
            return fld, src

    return None


def main():
    force   = '--force' in sys.argv
    dry_run = '--dry-run' in sys.argv

    print("Henter feltliste...")
    fields = {}
    for r in fetch_all(FIELD_LAYER, 'fldNpdidField,fldName'):
        fid, name = r.get('fldNpdidField'), (r.get('fldName') or '').strip().upper()
        if fid and name:
            fields[int(fid)] = name
    names = set(fields.values())
    print(f"  {len(fields)} felt")

    print("Bygger innretningsindeks...")
    facility_idx = build_facility_index(names)
    print(f"  {len(facility_idx)} innretninger knyttet til et felt")

    print("Henter feltbeskrivelser...")
    desc = {}
    for r in fetch_all(DESCRIPTION,
                       'fldNpdidField,fldDescriptionHeading,fldDescriptionText',
                       "fldCultureCode='en' AND fldDescriptionHeading='Transport'"):
        fid = r.get('fldNpdidField')
        if fid:
            desc[int(fid)] = re.sub(r'\s+', ' ', r.get('fldDescriptionText') or '')
    print(f"  {len(desc)} Transport-beskrivelser")

    # Behold rader brukeren har bekreftet (auto=false), med mindre --force.
    existing = {}
    if os.path.exists(OUT_FILE) and not force:
        with open(OUT_FILE, encoding='utf-8') as fh:
            existing = json.load(fh).get('assignments', {})
    confirmed = {k: v for k, v in existing.items() if not v.get('auto')}
    if confirmed:
        print(f"  bevarer {len(confirmed)} bekreftede oppforinger")

    print("\nUtleder huber...")
    assignments = dict(confirmed)
    hits = 0
    for fid, own in sorted(fields.items(), key=lambda x: x[1]):
        key = str(fid)
        if key in confirmed:
            continue
        found = find_host(desc.get(fid), own, names, facility_idx)
        if found:
            host, src = found
            assignments[key] = {'hub': host, 'auto': True, 'via': src}
            hits += 1

    # Et felt som er vert for andres bronnstrom har egen prosessering, og er
    # dermed selv en hub-rot. Uten denne regelen oppstar kjeder (BESTLA ->
    # BRAGE -> OSEBERG) der en hub selv ligger i en annen hub. Regelen fanger
    # ogsa reelle feil: Valhall prosesserer selv, men EKSPORTERER via Ekofisk,
    # og ble derfor feilaktig lagt under EKOFISK.
    hosts = {v['hub'] for v in assignments.values()}
    broken = []
    for key in list(assignments):
        own = fields[int(key)]
        if own in hosts and assignments[key].get('auto'):
            broken.append((own, assignments[key]['hub']))
            del assignments[key]
    if broken:
        print(f"  {len(broken)} kjeder brutt (feltet er selv vert, altsa egen hub):")
        for own, was in sorted(broken):
            print(f"     {own} losrevet fra {was}")

    standalone = len(fields) - len(assignments)
    print(f"  {hits} felt foreslatt knyttet til en annen hub")
    print(f"  {standalone} felt star som sin egen hub")

    # Grupper for lesbar utskrift
    groups = {}
    for key, v in assignments.items():
        groups.setdefault(v['hub'], []).append(fields[int(key)])
    print("\nForeslatte grupper (hub <- medlemmer):")
    for hub in sorted(groups, key=lambda h: (-len(groups[h]), h)):
        print(f"  {hub:22s} <- {', '.join(sorted(groups[hub]))}")

    payload = {
        'version': 1,
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'note': ('Felt som ikke star her er sin egen hub. auto=true betyr '
                 'maskinelt forslag fra Sodirs Transport-beskrivelse og bor '
                 'kvalitetssikres; auto=false er bekreftet av et menneske.'),
        'assignments': dict(sorted(assignments.items(), key=lambda x: int(x[0]))),
    }

    if dry_run:
        print(f"\n--dry-run: skriver ikke {OUT_FILE}")
        return 0

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write('\n')
    print(f"\nSkrev {OUT_FILE} ({len(assignments)} oppforinger)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
