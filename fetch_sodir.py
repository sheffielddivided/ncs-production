"""
Henter alle datasett fra Sodir (Norwegian Offshore Directorate) FactMaps og
lagrer dem som kompakte JSON-filer under data/.

Kjøres automatisk daglig av .github/workflows/update-sodir-data.yml,
men kan også kjøres lokalt:

    pip install requests
    python fetch_sodir.py

Utdata er deterministisk (sorterte rader og nøkler) slik at filene kun
endrer seg når selve dataene har endret seg. Det gjør at workflowen ikke
lager en ny commit hver dag – kun når Sodir faktisk har publisert nye tall.
data/meta.json oppdateres derfor også bare når noe annet har endret seg.
"""

import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

BASE = 'https://factmaps.sodir.no/api/rest/services/DataService/Data/FeatureServer'

FIELD_LAYER = 7100
PROD_LAYER  = 7300
LIC_LAYER   = 7108
RES_LAYER   = 7114
FLDRES_LAYER = 7113
DISC_LAYER   = 7000    # discovery
DISCDESCR_LAYER = 7001 # discovery_description
DISCRES_LAYER = 7008   # discovery_reserves
PRL_LIC_LAYER = 3007   # licence_licensee_hst
BAA_LIC_LAYER = 3304   # bsns_arr_area_licensee_hst
STATUS_LAYER  = 7101   # field_activity_status_hst
DESCR_LAYER   = 7102   # field_description
FACILITY_LAYER = 6000  # facility
FACILITY_PHASE_LAYER = 6002  # facility_phase_hst
FLDINV_EXP_LAYER = 7107  # field_investment_expected
LICENCE_LAYER = 3000   # licence
COMPANY_LAYER = 1200   # company
WELL_LAYER    = 5000   # wellbore
OUTLINE_SIMPLIFY = 0.001   # grader (~110 m) for Douglas-Peucker
OUTLINE_DECIMALS = 4

OUT_DIR   = 'data'
PAGE_SIZE = 2000
TIMEOUT   = 120
RETRIES   = 4

SOURCE = 'Norwegian Offshore Directorate (Sodir) FactPages - https://factpages.sodir.no - NLOD 2.0'


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _get(url, params):
    """GET med retry og eksponentiell backoff. Sodir svarer av og til tregt."""
    last = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if 'error' in data:
                raise RuntimeError(data['error'].get('message', 'ukjent API-feil'))
            return data
        except Exception as exc:  # noqa: BLE001 - vi vil retry-e på alt
            last = exc
            if attempt < RETRIES - 1:
                wait = 2 ** (attempt + 1)
                print(f"    ! {exc} - prover igjen om {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"Ga opp etter {RETRIES} forsok: {last}")


def oid_field(layer):
    """Hent navnet pa objectId-feltet, slik at paginering blir deterministisk.

    Uten en stabil sortering kan offset-basert paginering hoppe over eller
    duplisere rader nar serveren returnerer radene i vilkarlig rekkefolge.
    """
    try:
        meta = _get(f'{BASE}/{layer}', {'f': 'json'})
        return meta.get('objectIdField') or None
    except Exception as exc:  # noqa: BLE001
        print(f"    ! klarte ikke lese objectIdField ({exc}) - fortsetter usortert")
        return None


def count_rows(layer, where='1=1'):
    data = _get(f'{BASE}/{layer}/query', {
        'f': 'json', 'where': where, 'returnCountOnly': 'true',
    })
    return data.get('count', 0)


def fetch_layer(layer, out_fields, where='1=1'):
    """Hent alle rader fra et lag med paginering."""
    total = count_rows(layer, where)
    order = oid_field(layer)
    print(f"  Lag {layer}: {total} rader")

    rows, offset = [], 0
    while offset < total:
        params = {
            'f': 'json',
            'where': where,
            'outFields': out_fields,
            'returnGeometry': 'false',
            'resultOffset': offset,
            'resultRecordCount': PAGE_SIZE,
        }
        if order:
            params['orderByFields'] = f'{order} ASC'

        feats = _get(f'{BASE}/{layer}/query', params).get('features', [])
        if not feats:
            break
        rows.extend(f.get('attributes', {}) for f in feats)
        offset += len(feats)
        print(f"    {offset}/{total}")

    return rows


# ── HJELPERE ─────────────────────────────────────────────────────────────────
def num(value):
    """Rund av til 6 desimaler. None og tomt blir 0."""
    if value is None:
        return 0
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0


def as_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── DATASETT ─────────────────────────────────────────────────────────────────
def build_fields():
    rows = fetch_layer(FIELD_LAYER, 'fldNpdidField,fldName')
    out = []
    for r in rows:
        fid, name = as_int(r.get('fldNpdidField')), (r.get('fldName') or '').strip()
        if fid is None or not name:
            continue
        out.append([fid, name])
    out.sort(key=lambda x: x[1])
    return {'columns': ['id', 'name'], 'rows': out}


def build_production():
    rows = fetch_layer(
        PROD_LAYER,
        'prfNpdidInformationCarrier,prfYear,prfMonth,prfPrdOilNetMillSm3,'
        'prfPrdGasNetBillSm3,prfPrdOeNetMillSm3,prfPrdProducedWaterInFieldMillS',
    )
    fields = {}
    for r in rows:
        fid  = as_int(r.get('prfNpdidInformationCarrier'))
        year = as_int(r.get('prfYear'))
        if fid is None or year is None:
            continue
        fields.setdefault(str(fid), []).append([
            year,
            as_int(r.get('prfMonth')) or 0,
            num(r.get('prfPrdOilNetMillSm3')),
            num(r.get('prfPrdGasNetBillSm3')),
            num(r.get('prfPrdOeNetMillSm3')),
            num(r.get('prfPrdProducedWaterInFieldMillS')),
        ])
    for series in fields.values():
        series.sort(key=lambda x: (x[0], x[1]))
    return {
        'columns': ['year', 'month', 'oil', 'gas', 'oe', 'water'],
        'fields': fields,
    }


# Investeringer per felt: årlige historiske tall fra produksjonsprofilene
# (prfInvestmentsMillNOK på årsradene, nominelle MNOK) og Sodirs anslag for
# gjenstående framtidige investeringer (lag 7107, MNOK i faste kroner for
# fldInvExpFixYear). Holdes utenfor production.json, som appen også kan
# bygge direkte fra APIet med de kolonnene den har i dag.
def build_field_investments():
    rows = fetch_layer(
        PROD_LAYER,
        'prfNpdidInformationCarrier,prfInformationCarrierKind,prfYear,prfInvestmentsMillNOK',
        'prfMonth=0 AND prfInvestmentsMillNOK IS NOT NULL',
    )
    fields = {}
    for r in rows:
        fid, year = as_int(r.get('prfNpdidInformationCarrier')), as_int(r.get('prfYear'))
        if fid is None or year is None or _clean(r.get('prfInformationCarrierKind')) != 'FIELD':
            continue
        mnok = round(num(r.get('prfInvestmentsMillNOK')), 1)
        # Nullår utelates; appen fyller hull med null. Det halverer fila, og
        # Sodir rapporterer uansett null for år som ennå ikke er tallfestet.
        if mnok:
            fields.setdefault(str(fid), []).append([year, mnok])
    for series in fields.values():
        series.sort()
    expected = {}
    for r in fetch_layer(FLDINV_EXP_LAYER, 'fldNpdidField,fldInvestmentExpected,fldInvExpFixYear'):
        fid = as_int(r.get('fldNpdidField'))
        if fid is None or r.get('fldInvestmentExpected') is None:
            continue
        expected[str(fid)] = [round(num(r.get('fldInvestmentExpected')), 1), as_int(r.get('fldInvExpFixYear'))]
    return {'columns': ['year', 'mnok'], 'fields': fields,
            'expectedColumns': ['mnok', 'fixYear'], 'expected': expected}


def build_licensees():
    rows = fetch_layer(
        LIC_LAYER,
        'fldNpdidField,cmpLongName,fldCompanyShare,fldLicenseeFrom,fldLicenseeTo',
    )
    out = []
    for r in rows:
        fid, cmp_name = as_int(r.get('fldNpdidField')), (r.get('cmpLongName') or '').strip()
        if fid is None or not cmp_name:
            continue
        out.append([
            fid,
            cmp_name,
            num(r.get('fldCompanyShare')),
            as_int(r.get('fldLicenseeFrom')),
            as_int(r.get('fldLicenseeTo')),
        ])
    # Sorter deterministisk. None sist, siden None ikke kan sammenlignes med int.
    out.sort(key=lambda x: (x[0], x[1], x[3] is None, x[3] or 0))
    return {'columns': ['fieldId', 'company', 'share', 'from', 'to'], 'rows': out}


def build_reserves():
    rows = fetch_layer(RES_LAYER, 'cmpLongName,cmpRecoverableOE')
    totals = {}
    for r in rows:
        cmp_name = (r.get('cmpLongName') or '').strip()
        if not cmp_name:
            continue
        totals[cmp_name] = round(totals.get(cmp_name, 0) + num(r.get('cmpRecoverableOE')), 6)
    return {'companies': dict(sorted(totals.items()))}


# Reservetall per felt per ar. fldVersion er arstallet estimatet gjelder for,
# sa dette er en tidsserie 2004 -> i dag, ikke et oyeblikksbilde.
#
# Merk enhetene: olje og kondensat i mill Sm3, gass i bill Sm3, men NGL i
# mill TONN - altsa masse, ikke volum. NGL kan derfor ikke summeres med de
# ovrige. Bruk fldRecoverableOE/fldRemainingOE nar noe skal sammenliknes;
# Sodir har da allerede gjort konverteringen.
RESERVE_COLUMNS = [
    'fldRecoverableOil', 'fldRecoverableGas', 'fldRecoverableNGL',
    'fldRecoverableCondensate', 'fldRecoverableOE',
    'fldRemainingOil', 'fldRemainingGas', 'fldRemainingNGL',
    'fldRemainingCondensate', 'fldRemainingOE',
    'fldInplaceOil', 'fldInplaceAssLiquid', 'fldInplaceAssGas', 'fldInplaceFreeGas',
]


def build_field_reserves():
    rows = fetch_layer(
        FLDRES_LAYER,
        'fldNpdidField,fldVersion,' + ','.join(RESERVE_COLUMNS),
    )
    fields = {}
    for r in rows:
        fid  = as_int(r.get('fldNpdidField'))
        year = as_int(r.get('fldVersion'))
        if fid is None or year is None:
            continue
        fields.setdefault(str(fid), []).append(
            [year] + [num(r.get(c)) for c in RESERVE_COLUMNS]
        )
    for series in fields.values():
        series.sort(key=lambda x: x[0])
    return {
        'columns': ['year'] + RESERVE_COLUMNS,
        'fields': fields,
    }


# Funn. Hele listen hentes, ikke bare de med ressursanslag — statusfordelingen
# og funnaarene er nyttige i seg selv, og fila er liten.
def build_discoveries():
    rows = fetch_layer(
        DISC_LAYER,
        'dscNpdidDiscovery,dscName,dscCurrentActivityStatus,dscOwnerKind,'
        'dscOwnerName,dscHcType,dscDiscoveryYear,fldName,'
        'cmpLongName,wlbName,nmaName,dscResInclInDiscoveryName',
    )
    out = []
    for r in rows:
        did, name = as_int(r.get('dscNpdidDiscovery')), (r.get('dscName') or '').strip()
        if did is None or not name:
            continue
        out.append([
            did, name,
            (r.get('dscCurrentActivityStatus') or '').strip(),
            (r.get('dscOwnerKind') or '').strip(),
            (r.get('dscOwnerName') or '').strip(),
            (r.get('dscHcType') or '').strip(),
            as_int(r.get('dscDiscoveryYear')),
            (r.get('fldName') or '').strip(),
            # Kolonnene under brukes av Profile-visningen. cmpLongName er
            # operatoren, nmaName hovedomradet, og dscResInclInDiscoveryName
            # det funnet ressursene eventuelt er bokfort under.
            (r.get('cmpLongName') or '').strip(),
            (r.get('wlbName') or '').strip(),
            (r.get('nmaName') or '').strip(),
            (r.get('dscResInclInDiscoveryName') or '').strip(),
        ])
    out.sort(key=lambda x: x[1])
    return {'columns': ['id', 'name', 'status', 'ownerKind', 'owner', 'hc', 'year', 'field',
                        'operator', 'wellbore', 'area', 'inclIn'],
            'rows': out}


# Sodirs egne funnbeskrivelser, engelsk utgave. Lag 7001 er tynt besatt -
# bare en handfull funn har tekst - sa Profile-visningen utelater kortet nar
# funnet ikke har noen.
def build_discovery_descriptions():
    rows = fetch_layer(DISCDESCR_LAYER,
                       'dscNpdidDiscovery,dscDescriptionHeading,dscDescriptionText',
                       "dscCultureCode='en'")
    out = []
    for r in rows:
        did = as_int(r.get('dscNpdidDiscovery'))
        head = (r.get('dscDescriptionHeading') or '').strip()
        text = re.sub(r'\s+', ' ', r.get('dscDescriptionText') or '').strip()
        if did is None or not head or not text:
            continue
        out.append([did, head, text])
    out.sort(key=lambda x: (x[0], x[1]))
    return {'columns': ['id', 'heading', 'text'], 'rows': out}


# Ressursanslag per funn. Et funn kan ha flere rader, en per ressursklasse.
# Klassekoden er Sodirs egen: RC4/RC5/RC7 er betingede ressurser, suffiks F
# betyr forste utbyggingsprosjekt. Se SOLUTION-CONTEXT §5.
def build_discovery_resources():
    rows = fetch_layer(
        DISCRES_LAYER,
        'dscNpdidDiscovery,dscReservesRC,dscRecoverableOil,dscRecoverableGas,'
        'dscRecoverableNGL,dscRecoverableCondensate,dscRecoverableOe',
    )
    out = []
    for r in rows:
        did = as_int(r.get('dscNpdidDiscovery'))
        if did is None:
            continue
        out.append([
            did, (r.get('dscReservesRC') or '').strip(),
            num(r.get('dscRecoverableOil')), num(r.get('dscRecoverableGas')),
            num(r.get('dscRecoverableNGL')), num(r.get('dscRecoverableCondensate')),
            num(r.get('dscRecoverableOe')),
        ])
    out.sort(key=lambda x: (x[0], x[1]))
    return {'columns': ['id', 'rc', 'oil', 'gas', 'ngl', 'cond', 'oe'], 'rows': out}


# Eierandeler i utvinningstillatelser og forretningsavtaleomraader, slaatt
# sammen til en tabell. Et funn peker paa ett av dem via navn + type.
#
# Kun GJELDENDE rader hentes (to-dato er null). Ressursanslagene finnes bare
# for en aargang, saa historiske eierandeler har ingen nytte her, og hele
# historikken er 33 000+ rader mot 1 751.
def build_licence_licensees():
    out = []
    for layer, kind, pre, where in (
        (PRL_LIC_LAYER, 'PRODUCTION LICENCE', 'prl', 'prlLicenseeDateValidTo IS NULL'),
        (BAA_LIC_LAYER, 'BUSINESS ARRANGEMENT AREA', 'baa', 'baaLicenseeDateValidTo IS NULL'),
    ):
        name_f = 'prlName' if pre == 'prl' else 'baaName'
        int_f = f'{pre}LicenseeInterest'
        for r in fetch_layer(layer, f'{name_f},cmpLongName,{int_f}', where):
            owner = (r.get(name_f) or '').strip()
            cmp_name = (r.get('cmpLongName') or '').strip()
            if not owner or not cmp_name:
                continue
            out.append([owner, kind, cmp_name, num(r.get(int_f))])
    out.sort(key=lambda x: (x[1], x[0], -x[3], x[2]))
    return {'columns': ['owner', 'ownerKind', 'company', 'share'], 'rows': out}


# Faktaark per felt: funnår, operatør, HC-type, område og produksjonsstart.
#
# Produksjonsstart finnes ikke i feltlaget. Lag 7101 har fasehistorikken, og
# fra-datoen til den forste 'Producing'-raden er startdatoen. Kontrollert mot
# Ekofisk, som skal vaere 15. juni 1971.
def build_field_profile():
    meta = fetch_layer(
        FIELD_LAYER,
        'fldNpdidField,fldName,fldDiscoveryYear,cmpLongName,fldHcType,fldMainArea,'
        'fldMainSupplyBase,fldCurrentActivitySatus,wlbName',
    )
    # Feltets utvinningstillatelse eller forretningsavtaleomrade, fra de
    # gjeldende lisensradene. Brukes til infokolonnen i selskapsprofilens
    # lisenstabell, som sier hvilket felt en lisens eier. 128 av 142 felt har
    # gjeldende rader; resten er nedstengte og star uten eier.
    owners = {}
    for r in fetch_layer(LIC_LAYER, 'fldNpdidField,fldOwnerKind,fldOwnerName',
                         'fldLicenseeTo IS NULL'):
        fid = as_int(r.get('fldNpdidField'))
        kind = (r.get('fldOwnerKind') or '').strip()
        name = (r.get('fldOwnerName') or '').strip()
        if fid is not None and kind and name:
            owners[fid] = (kind, name)

    starts = {}
    for r in fetch_layer(STATUS_LAYER, 'fldNpdidField,fldStatus,fldStatusFromDate',
                         "fldStatus='Producing'"):
        fid, frm = as_int(r.get('fldNpdidField')), as_int(r.get('fldStatusFromDate'))
        if fid is None or frm is None:
            continue
        if fid not in starts or frm < starts[fid]:
            starts[fid] = frm

    out = []
    for r in meta:
        fid = as_int(r.get('fldNpdidField'))
        if fid is None:
            continue
        out.append([
            fid,
            as_int(r.get('fldDiscoveryYear')),
            (r.get('cmpLongName') or '').strip(),
            (r.get('fldHcType') or '').strip(),
            (r.get('fldMainArea') or '').strip(),
            (r.get('fldMainSupplyBase') or '').strip(),
            (r.get('fldCurrentActivitySatus') or '').strip(),
            (r.get('wlbName') or '').strip(),
            starts.get(fid),
            owners.get(fid, ('', ''))[0],
            owners.get(fid, ('', ''))[1],
        ])
    out.sort(key=lambda x: x[0])
    return {'columns': ['id', 'discoveryYear', 'operator', 'hc', 'area', 'supplyBase',
                        'status', 'wellbore', 'productionStart',
                        'ownerKind', 'owner'],
            'rows': out}


# Sodirs egne feltbeskrivelser, engelsk utgave. En rad per overskrift.
def build_field_descriptions():
    rows = fetch_layer(DESCR_LAYER, 'fldNpdidField,fldDescriptionHeading,fldDescriptionText',
                       "fldCultureCode='en'")
    out = []
    for r in rows:
        fid = as_int(r.get('fldNpdidField'))
        head = (r.get('fldDescriptionHeading') or '').strip()
        text = re.sub(r'\s+', ' ', r.get('fldDescriptionText') or '').strip()
        if fid is None or not head or not text:
            continue
        out.append([fid, head, text])
    out.sort(key=lambda x: (x[0], x[1]))
    return {'columns': ['id', 'heading', 'text'], 'rows': out}


def _simplify(points, tol):
    """Douglas-Peucker pa en apen punktrekke."""
    if len(points) < 3:
        return list(points)

    def dist(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy)
        if n < 1e-12:
            return math.hypot(x - x1, y - y1)
        return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / n

    keep = [0, len(points) - 1]
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        best, bi = -1.0, a
        for i in range(a + 1, b):
            dd = dist(points[i], points[a], points[b])
            if dd > best:
                best, bi = dd, i
        if best > tol:
            keep.append(bi)
            stack.append((a, bi))
            stack.append((bi, b))
    return [points[i] for i in sorted(set(keep))]


def _simplify_ring(ring, tol):
    """Lukket ring. Deles ved punktet lengst fra startpunktet, ellers faller
    start og slutt sammen og avstandsmalet blir meningslost."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    if len(pts) < 4:
        return None
    x0, y0 = pts[0]
    m = max(range(1, len(pts)), key=lambda i: (pts[i][0]-x0)**2 + (pts[i][1]-y0)**2)
    s = _simplify(pts[:m+1], tol)[:-1] + _simplify(pts[m:] + [pts[0]], tol)[:-1]
    if len(s) < 3:
        return None
    return s + [s[0]]


# Innretninger til hubprofilen. Lag 6000 knytter anlegget til et felt via
# fclNpdidBelongsTo, og det er nettopp den koblingen som gjor en hub til en
# hub: hvilket prosessanlegg feltene deler. Bare anlegg som henger pa et felt
# hentes - landanleggene (Nyhamna, Melkoya) henger pa et transportsystem
# (TUF), ikke pa feltet, og hadde ikke latt seg plassere i en hub uansett.
#
# Punktgeometrien folger med, sa kartet kan vise anleggene der de star.
def build_facilities():
    where = "fclBelongsToKind='FIELD'"
    total = count_rows(FACILITY_LAYER, where)
    print(f"  Lag {FACILITY_LAYER}: {total} rader")
    order = oid_field(FACILITY_LAYER)

    feats, offset = [], 0
    while offset < total:
        params = {
            'f': 'json', 'where': where,
            'outFields': ('fclNpdidFacility,fclName,fclNpdidBelongsTo,fclKind,'
                          'fclFunctions,fclPhase,fclStartupDate,fclWaterDepth,'
                          'fclCurrentOperatorName,fclSurface'),
            'returnGeometry': 'true', 'outSR': 4326,
            'resultOffset': offset, 'resultRecordCount': PAGE_SIZE,
        }
        if order:
            params['orderByFields'] = f'{order} ASC'
        page = _get(f'{BASE}/{FACILITY_LAYER}/query', params).get('features', [])
        if not page:
            break
        feats.extend(page)
        offset += len(page)
        print(f"    {offset}/{total}")

    out = []
    for f in feats:
        a = f.get('attributes') or {}
        fid = as_int(a.get('fclNpdidFacility'))
        fld = as_int(a.get('fclNpdidBelongsTo'))
        name = (a.get('fclName') or '').strip()
        if fid is None or fld is None or not name:
            continue
        g = f.get('geometry') or {}
        lon, lat = g.get('x'), g.get('y')
        out.append([
            fid, name, fld,
            (a.get('fclKind') or '').strip(),
            (a.get('fclFunctions') or '').strip(),
            (a.get('fclPhase') or '').strip(),
            as_int(a.get('fclStartupDate')),
            num(a.get('fclWaterDepth')),
            (a.get('fclCurrentOperatorName') or '').strip(),
            1 if (a.get('fclSurface') or '').upper() == 'Y' else 0,
            round(lon, 4) if isinstance(lon, (int, float)) else None,
            round(lat, 4) if isinstance(lat, (int, float)) else None,
        ])
    out.sort(key=lambda x: (x[2], x[1]))
    return {'columns': ['id', 'name', 'fieldId', 'kind', 'functions', 'phase',
                        'startup', 'waterDepth', 'operator', 'surface', 'lon', 'lat'],
            'rows': out}


# Utvinningstillatelser til selskapsprofilens lisenstabell. Bare de aktive:
# de 540 aktive lisensene er nayaktig de som har gjeldende rettighetshavere i
# lag 3007, sa de inaktive 1 273 ville aldri blitt vist. cmpLongName er
# lisensens operator.
def build_licences():
    rows = fetch_layer(
        LICENCE_LAYER,
        'prlNpdidLicence,prlName,prlMainArea,prlPhaseCurrent,prlDateGranted,'
        'prlDateValidTo,cmpLongName,prlLicensingActivityName,prlCurrentArea,'
        'prlOriginalArea,prlPartitionFrom',
        "prlActive='Y'",
    )
    out = []
    for r in rows:
        pid = as_int(r.get('prlNpdidLicence'))
        name = (r.get('prlName') or '').strip()
        if pid is None or not name:
            continue
        out.append([
            pid, name,
            (r.get('prlMainArea') or '').strip(),
            (r.get('prlPhaseCurrent') or '').strip(),
            as_int(r.get('prlDateGranted')),
            as_int(r.get('prlDateValidTo')),
            (r.get('cmpLongName') or '').strip(),
            (r.get('prlLicensingActivityName') or '').strip(),
            num(r.get('prlCurrentArea')),
            num(r.get('prlOriginalArea')),
            (r.get('prlPartitionFrom') or '').strip(),
        ])
    out.sort(key=lambda x: x[1])
    return {'columns': ['id', 'name', 'area', 'phase', 'granted', 'validTo', 'operator',
                        'round', 'areaKm2', 'origKm2', 'partitionFrom'],
            'rows': out}


# Lisensomriss til Licences-kartene. Nokkelen er lisensnavnet, siden det er
# det eierandelene og tabellene kobler pa. Lisensene er grove blokker, sa
# forenklingen kan vaere grovere enn for felt og funn.
LICENCE_SIMPLIFY = 0.003   # grader (~330 m)


def build_licence_outlines():
    total = count_rows(LICENCE_LAYER, "prlActive='Y'")
    print(f"  Lag {LICENCE_LAYER} (geometri): {total} rader")
    feats, offset = [], 0
    order = oid_field(LICENCE_LAYER)
    while offset < total:
        params = {
            'f': 'json', 'where': "prlActive='Y'", 'outFields': 'prlName',
            'returnGeometry': 'true', 'outSR': 4326,
            'resultOffset': offset, 'resultRecordCount': PAGE_SIZE,
        }
        if order:
            params['orderByFields'] = f'{order} ASC'
        page = _get(f'{BASE}/{LICENCE_LAYER}/query', params).get('features', [])
        if not page:
            break
        feats.extend(page)
        offset += len(page)
    out = {}
    for f in feats:
        name = ((f.get('attributes') or {}).get('prlName') or '').strip()
        if not name:
            continue
        rings = []
        for ring in (f.get('geometry') or {}).get('rings', []):
            s = _simplify_ring(ring, LICENCE_SIMPLIFY)
            if s:
                rings.append([[round(x, OUTLINE_DECIMALS), round(y, OUTLINE_DECIMALS)]
                              for x, y in s])
        if rings:
            out[name] = rings
    return {'licences': dict(sorted(out.items()))}


# Selskapsfakta til selskapsprofilen. Hele laget hentes - a filtrere til de
# selskapene appen kjenner ville koblet denne byggeren til lisensiarlaget uten
# a spare mer enn 16 KB gzip.
#
# Rolleflaggene er Sodirs egne Y/N-kolonner, og lagres som 0/1.
def build_companies():
    flags = ['cmpActiveOnNcsCurrent', 'cmpLicenceOperCurrent',
             'cmpLicenceLicenseeCurrent', 'cmpBsnsArrAreaOperCurrent',
             'cmpBsnsArrAreaPartnerCurrent', 'cmpTufOperCurrent',
             'cmpTufPartnerCurrent', 'cmpFacilityFixedOperator']
    rows = fetch_layer(COMPANY_LAYER,
                       'cmpNpdidCompany,cmpLongName,cmpGroup,cmpOrgNumberBrReg,'
                       'cmpNationCode,' + ','.join(flags))
    out = []
    for r in rows:
        cid = as_int(r.get('cmpNpdidCompany'))
        name = (r.get('cmpLongName') or '').strip()
        if cid is None or not name:
            continue
        out.append([
            cid, name,
            (r.get('cmpGroup') or '').strip(),
            (r.get('cmpOrgNumberBrReg') or '').strip(),
            (r.get('cmpNationCode') or '').strip(),
        ] + [1 if (r.get(f) or '').strip().upper() == 'Y' else 0 for f in flags])
    out.sort(key=lambda x: x[1])
    return {'columns': ['id', 'name', 'group', 'orgNumber', 'nation',
                        'activeNcs', 'licenceOper', 'licensee', 'baaOper',
                        'baaPartner', 'tufOper', 'tufPartner', 'facilityOper'],
            'rows': out}


# Hvilke forretningsavtaleomrader (BAA) en lisens inngar i, fra arealhistorikken
# i lag 3301. Bare gjeldende rader. BAA-navnene er de samme som i
# licence_licensees.json, sa et felt eller funn som eies av et BAA kan finnes
# fra lisensen. Raden uten lisensnavn (utvinningstillatelser-uten-PL, EXL) droppes.
BAA_AREA_LAYER = 3301   # bsns_arr_area_area_poly_hst


def build_licence_baa():
    rows = fetch_layer(BAA_AREA_LAYER, 'baaName,baaKind,prlName',
                       'baaAreaPolyDateValidTo IS NULL')
    out = set()
    for r in rows:
        lic = (r.get('prlName') or '').strip()
        baa = (r.get('baaName') or '').strip()
        if lic and baa:
            out.add((lic, baa, (r.get('baaKind') or '').strip()))
    return {'columns': ['licence', 'baa', 'kind'], 'rows': [list(x) for x in sorted(out)]}


# ── Lisenshistorikk ──────────────────────────────────────────────────────────
# Til Licences-fanene Transactions, Rounds, Overlap og Map. Alle datoer lagres
# som dager siden 1970-01-01 (heltall) for a holde filene sma; appen gjor dem om
# til ms ved innlasting. Lisenser identifiseres med Sodirs NPDID (lag 3000).
TRANSFER_LAYER = 3003   # licence_transfer_hst
OPER_LAYER = 3008       # licence_oper_hst
AREA_HST_LAYER = 3002   # licence_area_poly_hst
AREA_SIMPLIFY = 0.004   # grader (~440 m)


def _d(ms):
    """ms -> dager siden epoch (heltall), None hvis tom."""
    return None if ms is None else int(ms // 86400000)


# Alle lisenser noensinne (1 813), ogsa de tilbakelevert. Brukes til runder.
def build_licence_all():
    rows = fetch_layer(
        LICENCE_LAYER,
        'prlNpdidLicence,prlName,prlMainArea,prlLicensingActivityName,prlDateGranted,'
        'prlDateValidTo,prlActive,prlOriginalArea,prlCurrentArea,cmpLongName')
    out = []
    for r in rows:
        pid = as_int(r.get('prlNpdidLicence'))
        name = (r.get('prlName') or '').strip()
        if pid is None or not name:
            continue
        out.append([pid, name, (r.get('prlMainArea') or '').strip(),
                    (r.get('prlLicensingActivityName') or '').strip(),
                    _d(as_int(r.get('prlDateGranted'))), _d(as_int(r.get('prlDateValidTo'))),
                    1 if r.get('prlActive') == 'Y' else 0,
                    num(r.get('prlOriginalArea')), num(r.get('prlCurrentArea')),
                    (r.get('cmpLongName') or '').strip()])
    out.sort(key=lambda x: x[0])
    return {'columns': ['id', 'name', 'area', 'round', 'granted', 'validTo', 'active',
                        'origKm2', 'areaKm2', 'operator'], 'rows': out}


# Eierandeler og operatorer med full historikk (lag 3007 og 3008). Selskapsnavn
# lagres en gang i 'companies'; radene peker pa dem med indeks.
def build_licence_history():
    companies, idx = [], {}

    def ci(name):
        name = (name or '').strip()
        if name not in idx:
            idx[name] = len(companies)
            companies.append(name)
        return idx[name]

    shares = []
    for r in fetch_layer(PRL_LIC_LAYER, 'prlNpdidLicence,cmpLongName,prlLicenseeDateValidFrom,'
                         'prlLicenseeDateValidTo,prlLicenseeInterest'):
        pid, frm = as_int(r.get('prlNpdidLicence')), _d(as_int(r.get('prlLicenseeDateValidFrom')))
        if pid is None or frm is None or not (r.get('cmpLongName') or '').strip():
            continue
        shares.append([pid, ci(r.get('cmpLongName')), frm,
                       _d(as_int(r.get('prlLicenseeDateValidTo'))),
                       num(r.get('prlLicenseeInterest'))])
    oper = []
    for r in fetch_layer(OPER_LAYER, 'prlNpdidLicence,cmpLongName,prlOperDateValidFrom,'
                         'prlOperDateValidTo'):
        pid, frm = as_int(r.get('prlNpdidLicence')), _d(as_int(r.get('prlOperDateValidFrom')))
        if pid is None or frm is None or not (r.get('cmpLongName') or '').strip():
            continue
        oper.append([pid, ci(r.get('cmpLongName')), frm, _d(as_int(r.get('prlOperDateValidTo')))])
    key = lambda x: (x[0], x[2], x[1], x[3] if x[3] is not None else 10**9)
    shares.sort(key=key)
    oper.sort(key=key)
    return {'companies': companies, 'shares': shares, 'oper': oper}


# Overdragelser (lag 3003). Hver overdragelse star som to rader — FROM for
# selgeren og TO for kjoperen — pa samme lisens, dato og type. Radene pares her,
# storst mot storst, sa appen far en rad per overforing:
#   [dag, lisens, selger, kjoper, andel %, type]
# der selger/kjoper er indeks i 'companies', eller -1 nar motparten mangler i
# kilden. Typene (se 'kinds') er Sodirs prlTransferKind; navnebytter og fusjoner
# er selskapsendringer og ikke kjop og salg, sa appen skiller dem.
def build_licence_transfers():
    rows = fetch_layer(TRANSFER_LAYER,
                       'prlNpdidLicence,cmpLongName,prlTransferDateValidFrom,'
                       'prlTransferDirection,prlTransferKind,prlTransferredInterest')
    companies, kinds, cidx, kidx = [], [], {}, {}

    def ci(name):
        if name not in cidx:
            cidx[name] = len(companies)
            companies.append(name)
        return cidx[name]

    groups = {}
    for r in rows:
        pid, day = as_int(r.get('prlNpdidLicence')), _d(as_int(r.get('prlTransferDateValidFrom')))
        name = (r.get('cmpLongName') or '').strip()
        kind = (r.get('prlTransferKind') or '').strip() or 'UNKNOWN'
        side = (r.get('prlTransferDirection') or '').strip()
        share = num(r.get('prlTransferredInterest'))
        if pid is None or day is None or not name or side not in ('FROM', 'TO') or share is None:
            continue
        g = groups.setdefault((pid, day, kind), {'FROM': [], 'TO': []})
        g[side].append([share, name])
    out = []
    for (pid, day, kind), g in groups.items():
        if kind not in kidx:
            kidx[kind] = len(kinds)
            kinds.append(kind)
        sellers = sorted(g['FROM'], key=lambda x: (-x[0], x[1]))
        buyers = sorted(g['TO'], key=lambda x: (-x[0], x[1]))
        while sellers or buyers:
            s = sellers[0] if sellers else None
            b = buyers[0] if buyers else None
            amt = min(x[0] for x in (s, b) if x)
            out.append([day, pid, ci(s[1]) if s else -1, ci(b[1]) if b else -1,
                        round(amt, 4), kidx[kind]])
            for x in (s, b):
                if x:
                    x[0] = round(x[0] - amt, 6)
            if s and s[0] <= 1e-6:
                sellers.pop(0)
            if b and b[0] <= 1e-6:
                buyers.pop(0)
    out.sort(key=lambda x: (x[0], x[1], x[5], x[2], x[3], x[4]))
    return {'companies': companies, 'kinds': kinds,
            'columns': ['day', 'licence', 'seller', 'buyer', 'share', 'kind'], 'rows': out}


# Lisensenes arealhistorikk med geometri (lag 3002): én post per lisens og
# gyldighetsperiode — [fra-dag, til-dag eller null, areal km2, ringer]. Arealet
# er summen av delpolygonenes areal. Det stemmer med lisensens areal i dag for
# 527 av 536 aktive; de resterende har overlappende polygoner for ulike
# stratigrafiske nivaer, og summen blir da litt hoyere.
def build_licence_areas():
    total = count_rows(AREA_HST_LAYER)
    print(f"  Lag {AREA_HST_LAYER} (geometri): {total} rader")
    order = oid_field(AREA_HST_LAYER)
    feats, offset = [], 0
    while offset < total:
        params = {
            'f': 'json', 'where': '1=1',
            'outFields': 'prlNpdidLicence,prlAreaPolyDateValidFrom,prlAreaPolyDateValidTo,'
                         'prlAreaPolyPolyArea',
            'returnGeometry': 'true', 'outSR': 4326,
            'resultOffset': offset, 'resultRecordCount': PAGE_SIZE,
        }
        if order:
            params['orderByFields'] = f'{order} ASC'
        page = _get(f'{BASE}/{AREA_HST_LAYER}/query', params).get('features', [])
        if not page:
            break
        feats.extend(page)
        offset += len(page)
    byv = {}
    for f in feats:
        a = f.get('attributes') or {}
        pid, frm = as_int(a.get('prlNpdidLicence')), _d(as_int(a.get('prlAreaPolyDateValidFrom')))
        if pid is None or frm is None:
            continue
        v = byv.setdefault((pid, frm, _d(as_int(a.get('prlAreaPolyDateValidTo')))), [0.0, []])
        v[0] += num(a.get('prlAreaPolyPolyArea')) or 0
        for ring in (f.get('geometry') or {}).get('rings', []):
            sring = _simplify_ring(ring, AREA_SIMPLIFY)
            if sring:
                v[1].append([[round(x, 3), round(y, 3)] for x, y in sring])
    out = {}
    for (pid, frm, to), (km2, rings) in sorted(
            byv.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] if kv[0][2] is not None else 10**9)):
        if rings:
            out.setdefault(str(pid), []).append([frm, to, round(km2), rings])
    return {'licences': out}


# Feltomriss til profilkartet. Hentes i WGS84 og forenkles kraftig: kartet er
# en miniatyr, og rastallene fra Sodir er langt mer detaljerte enn nodvendig
# (92 000 punkter mot 10 000 etter forenkling).
def build_field_outlines():
    total = count_rows(FIELD_LAYER)
    print(f"  Lag {FIELD_LAYER} (geometri): {total} rader")
    feats = _get(f'{BASE}/{FIELD_LAYER}/query', {
        'f': 'json', 'where': '1=1', 'outFields': 'fldNpdidField',
        'returnGeometry': 'true', 'outSR': 4326, 'resultRecordCount': PAGE_SIZE,
    }).get('features', [])
    out = {}
    for f in feats:
        fid = as_int((f.get('attributes') or {}).get('fldNpdidField'))
        if fid is None:
            continue
        rings = []
        for ring in (f.get('geometry') or {}).get('rings', []):
            s = _simplify_ring(ring, OUTLINE_SIMPLIFY)
            if s:
                rings.append([[round(x, OUTLINE_DECIMALS), round(y, OUTLINE_DECIMALS)]
                              for x, y in s])
        if rings:
            out[str(fid)] = rings
    return {'fields': dict(sorted(out.items(), key=lambda x: int(x[0])))}


# Funnomriss til funnprofilens kart. Samme forenkling som feltomrissene, men
# bare for de funnene appen faktisk lister - de som har et ressursanslag i lag
# 7008. Hele lag 7000 ville vaert 324 KB mot 53 KB, og de ovrige funnene vises
# ingen steder. Noen ferske funn har anslag men enna ingen polygon i FactMaps;
# da utelates kartet i profilen.
def build_discovery_outlines():
    want = {as_int(r.get('dscNpdidDiscovery'))
            for r in fetch_layer(DISCRES_LAYER, 'dscNpdidDiscovery')}
    want.discard(None)
    if not want:
        return {'discoveries': {}}

    ids = ','.join(str(i) for i in sorted(want))
    print(f"  Lag {DISC_LAYER} (geometri): {len(want)} funn")
    feats = _get(f'{BASE}/{DISC_LAYER}/query', {
        'f': 'json', 'where': f'dscNpdidDiscovery IN ({ids})',
        'outFields': 'dscNpdidDiscovery',
        'returnGeometry': 'true', 'outSR': 4326, 'resultRecordCount': PAGE_SIZE,
    }).get('features', [])

    out = {}
    for f in feats:
        did = as_int((f.get('attributes') or {}).get('dscNpdidDiscovery'))
        if did is None:
            continue
        rings = []
        for ring in (f.get('geometry') or {}).get('rings', []):
            s = _simplify_ring(ring, OUTLINE_SIMPLIFY)
            if s:
                rings.append([[round(x, OUTLINE_DECIMALS), round(y, OUTLINE_DECIMALS)]
                              for x, y in s])
        if rings:
            out[str(did)] = rings
    return {'discoveries': dict(sorted(out.items(), key=lambda x: int(x[0])))}


# Letebronner: ferdige, pagaende og planlagte, fra brønnlaget (5000).
#
# Planlagte bronner star i samme lag, men uten status og uten wlbEntryDate —
# bare wlbEntryDatePlanned er satt. Bronner Sodir har oppgitt at ikke skal
# bores ('WILL NEVER BE DRILLED') droppes.
#
# Eierskap: lisensnummeret pa bronnen kobles mot lag 3007 (lisensiaer med
# gyldighetsdatoer), og andelene fastsettes per dato bronnen ble startet — ikke
# per i dag, for lisenser overdras. Bronner som ikke er startet far dagens
# eiere. Hver unike eierliste lagres én gang i 'owners', og bronnen peker pa
# den med indeks, sa fila holder seg liten.
EXPL_PURPOSES = "'WILDCAT','APPRAISAL'"


def _day(ms):
    if ms is None:
        return ''
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')


def _clean(v):
    return (v or '').strip()


def build_exploration_wells():
    rows = fetch_layer(
        WELL_LAYER,
        'wlbNpdidWellbore,wlbWellboreName,wlbWell,wlbProductionLicence,wlbPurpose,wlbPurposePlanned,'
        'wlbStatus,wlbContent,wlbDrillingOperator,wlbDrillingFacility,wlbFacilityTypeDrilling,'
        'wlbEntryDate,wlbCompletionDate,wlbEntryDatePlanned,wlbDiscovery,wlbDiscoveryWellbore,'
        'dscNpdidDiscovery,wlbField,wlbNsDecDeg,wlbEwDecDeg,wlbMainArea,wlbWaterDepth,'
        'wlbTotalDepth,wlbDrillingDays,wlbFormationAtTd,wlbAgeAtTd,wlbFormationWithHc1,'
        'wlbAgeWithHc1,wlbPressReleaseUrl,wlbDrillPermit',
        f"(wlbPurpose IN ({EXPL_PURPOSES}) OR wlbPurposePlanned IN ({EXPL_PURPOSES}))"
        " AND (wlbStatus IS NULL OR wlbStatus <> 'WILL NEVER BE DRILLED')",
    )

    lic_rows = fetch_layer(
        PRL_LIC_LAYER,
        'prlName,cmpLongName,prlLicenseeInterest,prlLicenseeDateValidFrom,prlLicenseeDateValidTo',
    )
    by_lic = {}
    for r in lic_rows:
        name, cmp_name = _clean(r.get('prlName')), _clean(r.get('cmpLongName'))
        share = num(r.get('prlLicenseeInterest'))
        if not name or not cmp_name or share <= 0:
            continue
        by_lic.setdefault(name, []).append(
            (cmp_name, share, r.get('prlLicenseeDateValidFrom'), r.get('prlLicenseeDateValidTo')))

    owners, owner_idx = [], {}
    now_ms = int(time.time() * 1000)

    def owners_at(licence, at_ms):
        ref = at_ms if at_ms is not None else now_ms
        parts = {}
        for cmp_name, share, frm, to in by_lic.get(licence, []):
            if (frm is None or frm <= ref) and (to is None or to > ref):
                parts[cmp_name] = parts.get(cmp_name, 0) + share
        if not parts:
            return None
        key = tuple(sorted(parts.items(), key=lambda kv: (-kv[1], kv[0])))
        if key not in owner_idx:
            owner_idx[key] = len(owners)
            owners.append([[n, round(v, 4)] for n, v in key])
        return owner_idx[key]

    out = []
    for r in rows:
        wid = as_int(r.get('wlbNpdidWellbore'))
        name = _clean(r.get('wlbWellboreName'))
        if wid is None or not name:
            continue
        lic = _clean(r.get('wlbProductionLicence'))
        entry = r.get('wlbEntryDate')
        lat, lon = r.get('wlbNsDecDeg'), r.get('wlbEwDecDeg')
        out.append([
            wid, name, lic, _clean(r.get('wlbWell')),
            _clean(r.get('wlbPurpose')) or _clean(r.get('wlbPurposePlanned')),
            _clean(r.get('wlbStatus')), _clean(r.get('wlbContent')),
            _clean(r.get('wlbDrillingOperator')),
            _clean(r.get('wlbDrillingFacility')), _clean(r.get('wlbFacilityTypeDrilling')),
            _day(entry), _day(r.get('wlbCompletionDate')), _day(r.get('wlbEntryDatePlanned')),
            as_int(r.get('dscNpdidDiscovery')), _clean(r.get('wlbDiscovery')),
            1 if _clean(r.get('wlbDiscoveryWellbore')) == 'YES' else 0,
            _clean(r.get('wlbField')),
            round(lat, 5) if lat is not None else None,
            round(lon, 5) if lon is not None else None,
            _clean(r.get('wlbMainArea')),
            num(r.get('wlbWaterDepth')) or None, num(r.get('wlbTotalDepth')) or None,
            as_int(r.get('wlbDrillingDays')),
            _clean(r.get('wlbFormationAtTd')), _clean(r.get('wlbAgeAtTd')),
            _clean(r.get('wlbFormationWithHc1')), _clean(r.get('wlbAgeWithHc1')),
            _clean(r.get('wlbPressReleaseUrl')), _clean(r.get('wlbDrillPermit')),
            owners_at(lic, entry),
        ])
    out.sort(key=lambda x: x[0])
    return {'columns': ['id', 'name', 'licence', 'well', 'purpose', 'status', 'content', 'operator',
                        'rig', 'rigType', 'entry', 'completion', 'planned',
                        'discoveryId', 'discovery', 'discoveryWell', 'field', 'lat', 'lon',
                        'area', 'waterDepth', 'td', 'days', 'formationAtTd', 'ageAtTd',
                        'hcFormation', 'hcAge', 'pressUrl', 'permit', 'owners'],
            'rows': out, 'owners': owners}


# Utviklingsbronner til riggoversikten i Exploration. Bare det riggene
# trenger: hvem boret hva, hvor og nar. Fra de to siste arene — eldre bronner
# er utenfor tidslinjen og ville bare gjort fila stor.
#
# Utviklingsbronner har ingen planlagt startdato i Sodirs data, sa her finnes
# bare fortid og nåtid. Sluttdato rapporteres med etterslep: statusen er
# 'ONLINE/OPERATIONAL' fra dag én, og wlbCompletionDate fylles inn senere.
# Fila skiller derfor ikke ut «bores nå»; det avgjør frontenden.
# Jordprover (SOIL DRILLING) og observasjonsbronner er utelatt.
def build_rig_wells():
    year = datetime.now(timezone.utc).year
    rows = fetch_layer(
        WELL_LAYER,
        'wlbNpdidWellbore,wlbWellboreName,wlbWell,wlbField,wlbPurpose,wlbDrillingOperator,'
        'wlbDrillingFacility,wlbFacilityTypeDrilling,wlbDrillingFacilityFixedOrMove,'
        'wlbEntryDate,wlbCompletionDate,wlbProductionLicence',
        f"wlbPurpose IN ('PRODUCTION','INJECTION') AND wlbEntryYear >= {year - 2}",
    )
    out = []
    for r in rows:
        wid, name = as_int(r.get('wlbNpdidWellbore')), _clean(r.get('wlbWellboreName'))
        rig, entry = _clean(r.get('wlbDrillingFacility')), r.get('wlbEntryDate')
        if wid is None or not name or not rig or entry is None:
            continue
        out.append([
            wid, name, _clean(r.get('wlbWell')), _clean(r.get('wlbField')),
            _clean(r.get('wlbPurpose')), _clean(r.get('wlbDrillingOperator')),
            rig, _clean(r.get('wlbFacilityTypeDrilling')),
            1 if _clean(r.get('wlbDrillingFacilityFixedOrMove')) == 'FIXED' else 0,
            _day(entry), _day(r.get('wlbCompletionDate')), _clean(r.get('wlbProductionLicence')),
        ])
    out.sort(key=lambda x: x[0])
    return {'columns': ['id', 'name', 'well', 'field', 'purpose', 'operator', 'rig', 'rigType',
                        'fixed', 'entry', 'completion', 'licence'],
            'rows': out}


# Fakta om riggene til riggprofilen i Exploration: type, ansvarlig selskap og
# når riggen forst ble satt i drift. Alle flyttbare innretninger, pluss de
# faste som borer bronner. Brønndataene kjenner riggene bare ved navn, så
# navnet er nokkelen.
#
# For flyttbare innretninger er fclStartupDate tom; «In service» hentes derfor
# fra fasehistorikken (forste periode med fasen IN SERVICE), med
# fclStartupDate som reserve.
def build_rig_facts():
    # Faste innretninger tas med når boring er blant funksjonene, eller når
    # navnet står som borerigg i brønndataene (de skrives tidligere i samme
    # kjoring). Det siste fanger plattformer som Eldfisk S, der Sodir ikke
    # oppgir boring som funksjon. Filteret gjøres her og ikke i spørringen:
    # Sodirs brannmur avviser «... OR fclFunctions LIKE '%...%'».
    rig_names = set()
    for fname, col in (('exploration_wells.json', 'rig'), ('rig_wells.json', 'rig')):
        try:
            with open(os.path.join(OUT_DIR, fname), encoding='utf-8') as fh:
                b = json.load(fh)
            i = b['columns'].index(col)
            rig_names |= {r[i].upper() for r in b['rows'] if r[i]}
        except (OSError, ValueError, KeyError):
            pass
    fields = ('fclNpdidFacility,fclName,fclKind,fclFixedOrMoveable,fclPhase,fclFunctions,'
              'fclCurrentRespCompanyName,fclCurrentOperatorName,fclStartupDate,fclNationName,fclFactPageUrl')
    rows = fetch_layer(FACILITY_LAYER, fields, "fclFixedOrMoveable='MOVEABLE'")
    rows += [r for r in fetch_layer(FACILITY_LAYER, fields, "fclFixedOrMoveable='FIXED'")
             if 'DRILLING' in _clean(r.get('fclFunctions')) or _clean(r.get('fclName')).upper() in rig_names]
    first_in_service = {}
    for r in fetch_layer(FACILITY_PHASE_LAYER, 'fclNPDIDFacility,fclPhaseName,fclFromDate'):
        fid, start = as_int(r.get('fclNPDIDFacility')), r.get('fclFromDate')
        if fid is None or start is None or _clean(r.get('fclPhaseName')) != 'IN SERVICE':
            continue
        if fid not in first_in_service or start < first_in_service[fid]:
            first_in_service[fid] = start
    out = []
    for r in rows:
        fid, name = as_int(r.get('fclNpdidFacility')), _clean(r.get('fclName'))
        if fid is None or not name:
            continue
        moveable = _clean(r.get('fclFixedOrMoveable')) == 'MOVEABLE'
        out.append([
            fid, name, _clean(r.get('fclKind')), 0 if moveable else 1,
            _clean(r.get('fclCurrentRespCompanyName')) or _clean(r.get('fclCurrentOperatorName')),
            _day(first_in_service.get(fid, r.get('fclStartupDate'))),
            _clean(r.get('fclPhase')), _clean(r.get('fclNationName')), _clean(r.get('fclFactPageUrl')),
        ])
    out.sort(key=lambda x: x[0])
    return {'columns': ['id', 'name', 'kind', 'fixed', 'company', 'inService', 'phase', 'nation', 'url'],
            'rows': out}


# ── SKRIVING ─────────────────────────────────────────────────────────────────
def write_if_changed(name, payload):
    """Skriv fila kun hvis innholdet faktisk er endret. Returnerer True ved endring."""
    path = os.path.join(OUT_DIR, name)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'

    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            if fh.read() == text:
                print(f"  = {name} uendret")
                return False

    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    size_kb = len(text.encode('utf-8')) / 1024
    print(f"  + {name} skrevet ({size_kb:.0f} KB)")
    return True


def record_well_changes(payload):
    """Skriv endringsloggen for brønner (data/changes.json) fra forskjellen mot
    forrige fil. Feil her skal ikke stoppe datahentingen."""
    try:
        import changes
        path = os.path.join(OUT_DIR, 'exploration_wells.json')
        events = []
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                events += changes.well_events(json.load(fh), payload)
        if not changes.exists():
            events += changes.backfill_wells(payload)
        changes.merge(events)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! endringsloggen for bronner feilet: {exc}", file=sys.stderr)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    datasets = [
        ('fields.json',     'felt',        build_fields),
        ('production.json', 'produksjon',  build_production),
        ('licensees.json',  'lisensiaerer', build_licensees),
        ('reserves.json',   'reserver',    build_reserves),
        ('reserves_field.json', 'reserver per felt', build_field_reserves),
        ('discoveries.json', 'funn', build_discoveries),
        ('discovery_resources.json', 'funnressurser', build_discovery_resources),
        ('discovery_descriptions.json', 'funnbeskrivelser', build_discovery_descriptions),
        ('discovery_outlines.json', 'funnomriss', build_discovery_outlines),
        ('licence_licensees.json', 'lisensandeler', build_licence_licensees),
        ('field_profile.json', 'feltfakta', build_field_profile),
        ('field_descriptions.json', 'feltbeskrivelser', build_field_descriptions),
        ('field_outlines.json', 'feltomriss', build_field_outlines),
        ('field_investments.json', 'investeringer per felt', build_field_investments),
        ('facilities.json', 'innretninger', build_facilities),
        ('licences.json', 'utvinningstillatelser', build_licences),
        ('licence_outlines.json', 'lisensomriss', build_licence_outlines),
        ('licence_baa.json', 'lisens-BAA-kobling', build_licence_baa),
        ('licence_all.json', 'alle lisenser', build_licence_all),
        ('licence_history.json', 'lisenshistorikk', build_licence_history),
        ('licence_transfers.json', 'overdragelser', build_licence_transfers),
        ('licence_areas.json', 'lisensareal over tid', build_licence_areas),
        ('companies.json', 'selskaper', build_companies),
        ('exploration_wells.json', 'letebronner', build_exploration_wells),
        ('rig_wells.json', 'utviklingsbronner til riggoversikt', build_rig_wells),
        ('rig_facts.json', 'riggfakta', build_rig_facts),
    ]

    changed = False
    for filename, label, builder in datasets:
        print(f"\nHenter {label}...")
        try:
            payload = builder()
        except Exception as exc:  # noqa: BLE001
            # Avbryt uten a skrive noe. Da beholder repoet forrige gyldige
            # datasett, og appen fortsetter a fungere pa gamle tall.
            print(f"FEIL under henting av {label}: {exc}", file=sys.stderr)
            return 1
        if filename == 'exploration_wells.json':
            record_well_changes(payload)
        if write_if_changed(filename, payload):
            changed = True

    if changed:
        write_if_changed('meta.json', {
            'generated': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'source': SOURCE,
        })
        print("\nFerdig - data er oppdatert.")
    else:
        print("\nFerdig - ingen endringer siden forrige kjoring.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
