"""
Henter data for brasiliansk sokkel og landområder fra Agência Nacional do
Petróleo, Gás Natural e Biocombustíveis (ANP) og bygger datafilene til
brazil.html:

  data/brazil.json            totaler, bassenger, delstater, pre-salt, felt,
                              innretninger, selskaper, reserver, prognose,
                              leteoversikt og hendelsesstatistikk
  data/brazil_prod.json       serier per felt og innretning (lastes ved behov)
  data/brazil_expl.json       blokker under kontrakt, letebrønner, funn,
                              PAD-er, kommersialitetserklæringer, lisensrunder
                              og overdragelser (ved behov)
  data/brazil_wells.json      brønner med posisjon til kartet (ved behov)
  data/brazil_incidents.json  hendelser siste to år (ved behov)
  data/brazil_outlines.json   omriss av felt og blokker

Alle kildene er åpne (gov.br/anp, «Dados abertos» og «Dados estatísticos»).
Lenkene leses fra datasettsidene, fordi ANP bytter filnavn fra år til år.

  - Produção de petróleo e gás natural por poço: månedlig produksjon per brønn
    til havs (pm, 1954–) og på land (pt, 1941–), med felt, basseng, delstat og
    innretning. Grunnlaget for alle produksjonstall.
  - Arkivet «por poço» 2005–2023 (zip per år/måned, Mar/Terra/PreSal): hvilke
    brønner ANP regner som pre-salt, operatør og kontrakt per brønn,
    installasjonstype og API-grad.
  - Produção por estado e localização: offisielle månedstotaler per delstat,
    til kontroll.
  - Relação de concessionários: dagens rettighetshavere og andeler per
    kontrakt.
  - Dados georreferenciados: felt, blokker og brønner (shapefiler, 2023).
  - Boletim anual de recursos e reservas (BAR): volum i reservoaret og
    akkumulert produksjon per felt (xlsx 2020–), og 1P/2P/3P per basseng i
    PDF-boletinen (siste utgave, og totalene bakover så langt PDF-ene lar seg
    lese).
  - Fase de exploração: blokker under kontrakt, letebrønner med funn, PAD-er
    og kommersialitetserklæringer (månedlige øyeblikksbilder).
  - Rodadas de licitações: blokker tilbudt og tildelt (runde 9–17) og
    overdragelser (cessões).
  - Participações governamentais: særskatt (participação especial) per felt.
  - Incidentes de segurança operacional: hendelser meldt til ANP.
  - Previsão de produção: operatørenes prognose per basseng (fem år fram).

Kontroller (exit 1, ingenting skrives): produksjonsfilene skal dekke hver
måned fra 1941 til siste måned uten hull, og summen per brønn skal stemme med
ANPs offisielle totaler for hav og land innenfor 1 % hvert år fra 1997; minst
300 felt med produksjon siste 12 måneder; dagens andeler skal dekke minst 90 %
av produksjonen siste 12 måneder.

    python fetch_anp.py                 # hent alt
    python fetch_anp.py --cache DIR     # gjenbruk filer som ikke endres
                                        # (eldre år, arkiv, PDF-er)
"""

import argparse
import collections
import csv
import datetime as dt
import glob
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile

import requests

try:
    import openpyxl
except ImportError:          # pragma: no cover
    openpyxl = None
try:
    import shapefile         # pyshp
except ImportError:          # pragma: no cover
    shapefile = None

# gov.br brøt tilkoblingen fra GitHub Actions med en egen User-Agent; vanlige
# nettleserhoder brukes derfor. Dataene er åpne (dekret 8.777/2016, fri bruk
# med kreditering av ANP som kilde).
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
DA = 'https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/'
PAGES = {
    'dp': DA + 'fase-de-desenvolvimento-e-producao',
    'poco': DA + 'producao-de-petroleo-e-gas-natural-por-poco',
    'uf': DA + 'producao-de-petroleo-e-gas-natural-por-estado-e-localizacao',
    'rc': DA + 'relacao-de-concessionarios',
    'geo': DA + 'dados-georreferenciados-das-bacias-sedimentares-brasileiras',
    'exp': DA + 'fase-exploracao',
    'rounds': DA + 'rodadas-de-licitacoes-de-petroleo-e-gas-natural',
    'pg': DA + 'participacoes-governamentais',
    'issm': DA + 'incidentes-seguranca-operacional',
}
RES_PAGE = 'https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-estatisticos/arquivos-reservas-nacionais-de-petroleo-e-gas-natural'

OUT = 'data/brazil.json'
OUT_PROD = 'data/brazil_prod.json'
OUT_EXPL = 'data/brazil_expl.json'
OUT_WELLS = 'data/brazil_wells.json'
OUT_INC = 'data/brazil_incidents.json'
OUT_OUTL = 'data/brazil_outlines.json'
ALIASES = 'data/brazil_aliases.json'

BBL_PER_M3 = 6.2898          # ANP
GAS_M3_PER_OIL_M3 = 1000     # ANP: 1 000 m³ gass = 1 m³ olje ekvivalent (boe)
SERIES_MONTHLY_FROM = 2000   # feltserier månedlig fra dette året (årlig før)
INST_FROM = 2010             # innretningsserier fra dette året
COMPANY_FROM = 2005          # egenandel og opererte volumer fra dette året
THIS_YEAR = dt.date.today().year
MONTHS_PT = {'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6, 'jul': 7,
             'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12}


class DataError(Exception):
    pass


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- nett

S = requests.Session()
S.headers.update({
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
})


def get(url, binary=False, tries=6):
    last = None
    for k in range(tries):
        try:
            r = S.get(url, timeout=300)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content if binary else r.text
        except requests.RequestException as e:
            last = e
            time.sleep(4 * 2 ** k)
    raise DataError(f'{url}: {last}')


def links(page, pattern):
    """Lenker på en datasettside som matcher pattern (regex), i sideorden."""
    html = get(page)
    if not html:
        raise DataError(f'Fant ikke {page}')
    out = []
    for m in re.finditer(r'href="([^"]+)"', html):
        u = m.group(1).replace('&amp;', '&')
        if re.search(pattern, u) and u not in out:
            out.append(u)
    return out


class Store:
    """Nedlastede filer. ANP svarer ikke på HEAD, så med --cache gjenbrukes
    bare filer som ikke endres lenger (frozen): eldre produksjonsår, arkivet
    per brønn, PDF-er og eldre BAR-tabeller. Resten lastes ned hver gang; feiler
    nedlastingen, brukes forrige kopi hvis den finnes."""

    def __init__(self, cache):
        self.dir = cache or tempfile.mkdtemp(prefix='anp-')
        os.makedirs(self.dir, exist_ok=True)
        self.cached = bool(cache)

    def path(self, url, frozen=False):
        name = re.sub(r'[^A-Za-z0-9._-]', '_', url.split('/arquivos/', 1)[-1])[-150:]
        p = os.path.join(self.dir, name)
        if self.cached and frozen and os.path.exists(p) and os.path.getsize(p) > 0:
            return p
        last = None
        for k in range(6):
            try:
                with S.get(url, timeout=900, stream=True) as r:
                    if r.status_code == 404:
                        return None
                    r.raise_for_status()
                    tmp = p + '.part'
                    with open(tmp, 'wb') as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
                    os.replace(tmp, p)
                    return p
            except requests.RequestException as e:
                last = e
                time.sleep(4 * 2 ** k)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            log(f'  ! {url}: {last} — bruker forrige kopi')
            return p
        raise DataError(f'{url}: {last}')


# ---------------------------------------------------------------- lesing

TH = re.compile(r'-?\d{1,3}(\.\d{3})+$')


def num(s):
    """Tall i ANPs filer: «1.234,56», «22,3», «,878», «3070», «3.070» (tusen)."""
    if s is None:
        return 0.0
    s = str(s).strip().replace('R$', '').replace('%', '').strip()
    if not s or s == '-':
        return 0.0
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    elif TH.match(s):
        s = s.replace('.', '')
    try:
        return float(s)
    except ValueError:
        return 0.0


def decode(b):
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode('latin1')


def read_csv(path_or_bytes, delim=None):
    """CSV med ANPs varianter: UTF-8 eller cp1252, komma eller semikolon, og
    filer der hele linjen står i anførselstegn (pm 2019–2021). Gir (header,
    rader); headeren uten klammer og BOM."""
    b = path_or_bytes if isinstance(path_or_bytes, bytes) else open(path_or_bytes, 'rb').read()
    t = decode(b)
    first = t.split('\n', 1)[0]
    d = delim or (';' if first.count(';') > first.count(',') else ',')
    rows = csv.reader(io.StringIO(t, newline=''), delimiter=d)
    head = None
    out = []
    for x in rows:
        if len(x) == 1 and d in x[0]:
            x = next(csv.reader([x[0]], delimiter=d))
        if head is None:
            head = [h.strip().strip('[]').strip('﻿').strip() for h in x]
            continue
        if not any(c.strip() for c in x):
            continue
        out.append(x)
    return head or [], out


def col(head, *names):
    """Indeks for første kolonne som starter med et av navnene (uten aksenter)."""
    hn = [key(h) for h in head]
    for n in names:
        k = key(n)
        for i, h in enumerate(hn):
            if h == k:
                return i
        for i, h in enumerate(hn):
            if h.startswith(k):
                return i
    raise DataError(f'Fant ikke kolonnen {names} i {head[:12]}')


def key(s):
    """Sammenligningsnøkkel: uten aksenter, store bokstaver, enkle mellomrom."""
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', s).strip().upper()


def ym_of(s):
    """«01/2026» eller «2026/01» → «202601»."""
    s = s.strip()
    m = re.match(r'(\d{1,2})/(\d{4})$', s)
    if m:
        return f'{m.group(2)}{int(m.group(1)):02d}'
    m = re.match(r'(\d{4})/(\d{1,2})$', s)
    if m:
        return f'{m.group(1)}{int(m.group(2)):02d}'
    m = re.match(r'([A-Za-zç]{3})[a-zç]*[/-](\d{4})$', s)       # «nov/2025»
    if m and key(m.group(1)).lower() in MONTHS_PT:
        return f'{m.group(2)}{MONTHS_PT[key(m.group(1)).lower()]:02d}'
    return None


def date_iso(s):
    """«26/03/2006», «26-03-2006», «26/03/2006 00:00» → «2006-03-26»."""
    s = (s or '').strip()
    m = re.match(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', s)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    return m.group(0) if m else None


def excel_date(v):
    try:
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(v)))).isoformat()
    except (TypeError, ValueError):
        return None


def ym_add(ym, n):
    y, m = int(ym[:4]), int(ym[4:]) - 1 + n
    return f'{y + m // 12}{m % 12 + 1:02d}'


def ym_range(a, b):
    out = []
    while a <= b:
        out.append(a)
        a = ym_add(a, 1)
    return out


def days_in(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return (dt.date(y + m // 12, m % 12 + 1, 1) - dt.date(y, m, 1)).days


def cn(x):
    """Kompakt tall til JSON (filstørrelsen): tre gjeldende siffer."""
    if x is None:
        return None
    if x == 0:
        return 0
    a = abs(x)
    if a >= 100:
        return int(round(x))
    if a >= 10:
        return round(x, 1)
    if a >= 1:
        return round(x, 2)
    return round(x, 3) if a >= 0.001 else 0


def unaccent_title(s):
    """«NORDESTE DE SAPINHOÁ» → «Nordeste de Sapinhoá»."""
    small = {'DE', 'DO', 'DA', 'DOS', 'DAS', 'E', 'DI'}
    words = re.split(r'(\s+|-)', s.strip())
    out = []
    for i, w in enumerate(words):
        if not w.strip() or w == '-':
            out.append(w)
            continue
        u = w.upper()
        if u in small and i:
            out.append(w.lower())
        elif re.fullmatch(r'[IVX]+|[A-Z]{1,3}\d*|\d+[A-Z]*', u) and len(u) <= 4 and not re.fullmatch(r'[A-Z]{3,}', u):
            out.append(u)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return ''.join(out)


def field_name(code):
    """Visningsnavn for ANPs feltnavn. _ECO: overskuddsvolumene fra
    cessão onerosa (partilha); AnC_: område utenfor kontrakt (União)."""
    s = code
    suffix = ''
    if re.match(r'(?i)anc_', s):
        s = s[4:]
        suffix = ' (AnC)'
    if s.upper().endswith('_ECO'):
        s = s[:-4]
        suffix = ' ECO' + suffix
    s = s.replace('_', ' ')
    return unaccent_title(s) + suffix


def complex_of(code, known):
    """Feltkomplekset et ANP-felt hører til: BÚZIOS_ECO → BÚZIOS,
    AnC_Jubarte_Nordeste → JUBARTE, AnC_NORTE_ATAPU → ATAPU."""
    s = re.sub(r'(?i)^anc_', '', code)
    s = re.sub(r'(?i)_eco$', '', s)
    up = s.upper()
    if up in known:
        return up
    parts = up.split('_')
    for p in parts:
        if p in known:
            return p
    for i in range(len(parts), 0, -1):
        j = ' '.join(parts[:i])
        if j in known:
            return j
    return code if code in known else up


# ---------------------------------------------------------------- selskaper

GROUPS = [
    (r'petrobras|petroleo brasileiro', 'Petrobras'),
    (r'^shell|^bg (e&p|brasil)|^bg$', 'Shell'),
    (r'^total', 'TotalEnergies'),
    (r'maersk', 'TotalEnergies'),
    (r'equinor|statoil', 'Equinor'),
    (r'petrogal|galp', 'Galp'),
    (r'repsol', 'Repsol Sinopec'),
    (r'^sinopec', 'Sinopec'),
    (r'cnooc', 'CNOOC'),
    (r'^cnpc|petrochina', 'CNPC'),
    (r'exxon|esso', 'ExxonMobil'),
    (r'^prio|petro ?rio', 'PRIO'),
    (r'^brava|^3r|enauta|qgep|queiroz galv', 'Brava Energia'),
    (r'petronas', 'Petronas'),
    (r'qatar', 'QatarEnergy'),
    (r'^bp energy|^bp brasil|^bp$', 'bp'),
    (r'^bprl', 'BPRL'),
    (r'sinochem', 'Sinochem'),
    (r'^ongc', 'ONGC'),
    (r'karoon', 'Karoon'),
    (r'chevron', 'Chevron'),
    (r'^eneva', 'Eneva'),
    (r'petroreconcavo|potiguar e&p', 'PetroReconcavo'),
    (r'^origem', 'Origem Energia'),
    (r'seacrest', 'Seacrest'),
    (r'ecopetrol', 'Ecopetrol'),
    (r'murphy', 'Murphy'),
    (r'^bw ', 'BW Energy'),
    (r'perenco', 'Perenco'),
    (r'geopark', 'GeoPark'),
    (r'trident', 'Trident Energy'),
    (r'^ogx|dommo', 'Dommo'),
    (r'mitsui', 'Mitsui'),
    (r'wintershall|harbour', 'Harbour Energy'),
    (r'^uniao|ppsa|pre-sal petroleo', 'PPSA (Union)'),
    (r'^panoro', 'Panoro'),
    (r'^alvopetro', 'Alvopetro'),
    (r'^imetame', 'Imetame'),
    (r'^petro-victory', 'Petro-Victory'),
    (r'^elysian', 'Elysian Petroleum'),
]
_GROUP_RE = [(re.compile(p), g) for p, g in GROUPS]


def group_of(name):
    n = key(name).lower()
    n = re.sub(r'^\(operador\)\s*-\s*', '', n).strip()
    for rx, g in _GROUP_RE:
        if rx.search(n):
            return g
    s = re.sub(r'^\(Operador\)\s*-\s*', '', str(name)).strip()
    s = re.sub(r'\s+(S\.?A\.?|Ltda\.?|Brasil|do Brasil|Petróleo|EP)$', '', s, flags=re.I).strip()
    return s or str(name).strip()


def regime_of(bid):
    b = (bid or '').strip().upper()
    if b.startswith(('PP', 'OPP', 'EXC')):
        return 'Production sharing'
    if b.startswith(('CO_', 'C_ONEROSA')):
        return 'Transfer of rights'
    return 'Concession'


def round_label(bid):
    b = (bid or '').strip().upper()
    m = re.match(r'BID(\d+)(AM)?$', b)
    if m:
        return f'Round {m.group(1)}' + (' (marginal fields)' if m.group(2) else '')
    m = re.match(r'OP(\d+)_(BE|AM)$', b)
    if m:
        return f'Permanent offer, cycle {m.group(1)}' + (' (marginal)' if m.group(2) == 'AM' else '')
    m = re.match(r'OPP(\d+)$', b)
    if m:
        return f'Permanent offer, sharing cycle {m.group(1)}'
    m = re.match(r'PP(\d+)$', b)
    if m:
        return f'Production sharing round {m.group(1)}'
    m = re.match(r'AM(\d+)$', b)
    if m:
        return f'Marginal fields round {m.group(1)}'
    if b.startswith('EXC_CO'):
        return 'Transfer-of-rights surplus ' + b[-4:]
    if b.startswith('CO_'):
        return 'Transfer of rights (2010)'
    return bid.strip()


# ---------------------------------------------------------------- produksjon

def production_links():
    pm = links(PAGES['dp'], r'/pm/[^"]+\.csv$')
    pt = links(PAGES['dp'], r'/pt/[^"]+\.csv$')
    if len(pm) < 10 or len(pt) < 50:
        raise DataError(f'For få produksjonsfiler på datasettsiden ({len(pm)} hav, {len(pt)} land)')
    return pm, pt


def file_years(url):
    ys = [int(y) for y in re.findall(r'(?<!\d)(19[4-9]\d|20[0-4]\d)(?!\d)', url.rsplit('/', 1)[-1])]
    if not ys:
        ys = [int(y) for y in re.findall(r'/(19[4-9]\d|20[0-4]\d)/', url)]
    return ys


class Prod:
    """Summer per måned (olje+kondensat m³, gass 1000 m³, vann m³)."""

    def __init__(self):
        z = lambda: collections.defaultdict(lambda: [0.0, 0.0, 0.0])
        self.total = z()
        self.env = collections.defaultdict(z)
        self.basin = collections.defaultdict(z)
        self.state = collections.defaultdict(z)
        self.field = collections.defaultdict(z)
        self.salt = collections.defaultdict(z)
        self.inst = collections.defaultdict(z)
        self.inj = collections.defaultdict(float)           # gassinjeksjon (1000 m³), totalt
        self.field_inj = collections.defaultdict(lambda: collections.defaultdict(float))
        self.field_meta = collections.defaultdict(lambda: {'basin': collections.Counter(), 'state': collections.Counter(), 'env': collections.Counter()})
        self.inst_meta = collections.defaultdict(lambda: {'fields': collections.Counter(), 'env': collections.Counter(), 'basin': collections.Counter(), 'state': collections.Counter()})
        self.wells_live = collections.defaultdict(lambda: [set(), set()])   # ym → [hav, land]
        self.well_first = {}
        self.well_recent = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))
        self.well_info = {}
        self.field_wells = collections.defaultdict(lambda: collections.defaultdict(set))
        self.inst_wells = collections.defaultdict(set)
        self.state_on = collections.defaultdict(lambda: [0.0, 0.0])   # (delstat-nøkkel, ym) → land
        self.field_salt = collections.defaultdict(lambda: [0.0, 0.0])
        self.rows = 0


def read_production(store, prod, url, presal, recent_from):
    yrs = file_years(url)
    frozen = bool(yrs) and max(yrs) <= THIS_YEAR - 2
    p = store.path(url, frozen=frozen)
    if not p:
        raise DataError(f'Mangler {url}')
    head, rows = read_csv(p)
    iy = col(head, 'Mês/Ano')
    ist, iba, ifi, iwe, ien, iin = (col(head, n) for n in ('Estado', 'Bacia', 'Campo', 'Poço', 'Ambiente', 'Instalação'))
    io_, ic = col(head, 'Produção de Óleo'), col(head, 'Produção de Condensado')
    iga, ign = col(head, 'Produção de Gás Associado'), col(head, 'Produção de Gás Não Associado')
    iwa = col(head, 'Produção de Água')
    iinj = col(head, 'Injeção de Gás (')
    ico2 = col(head, 'Injeção de Gás Carbônico')
    n = 0
    for x in rows:
        if len(x) <= iwa:
            continue
        ym = ym_of(x[iy])
        if not ym:
            continue
        env = 'off' if key(x[ien]).startswith('MAR') else 'on'
        field = norm_field(x[ifi])
        if not field:
            continue
        well = x[iwe].strip()
        oil = num(x[io_]) + num(x[ic])
        gas = num(x[iga]) + num(x[ign])
        wat = num(x[iwa])
        inj = num(x[iinj]) + num(x[ico2])
        n += 1
        basin = x[iba].strip()
        state = x[ist].strip()
        inst = re.sub(r'\s+', ' ', x[iin]).strip()
        if key(inst) in ('NAO INFORMADO', '', '-'):
            inst = None
        v = (oil, gas, wat)
        for tgt in (prod.total[ym], prod.env[env][ym], prod.basin[basin][ym], prod.state[state][ym], prod.field[field][ym]):
            tgt[0] += oil
            tgt[1] += gas
            tgt[2] += wat
        if env == 'on':
            t = prod.state_on[(key(state), ym)]
            t[0] += oil
            t[1] += gas
        if env == 'off':
            cls = classify_salt(well, field, ym, presal)
        else:
            cls = 'onshore'
        if env == 'off' and ym >= recent_from:
            fs = prod.field_salt[(field, ym)]
            fs[1] += oil + gas
            if cls == 'pre':
                fs[0] += oil + gas
        t = prod.salt[cls][ym]
        t[0] += oil
        t[1] += gas
        t[2] += wat
        if inst and int(ym[:4]) >= INST_FROM:
            t = prod.inst[inst][ym]
            t[0] += oil
            t[1] += gas
            t[2] += wat
            im = prod.inst_meta[inst]
            im['fields'][field] += oil + gas / GAS_M3_PER_OIL_M3
            im['env'][env] += 1
            im['basin'][basin] += 1
            im['state'][state] += 1
        if inj:
            prod.inj[ym] += inj
            prod.field_inj[field][ym] += inj
        fm = prod.field_meta[field]
        fm['basin'][basin] += 1
        fm['state'][state] += 1
        fm['env'][env] += 1
        if oil + gas > 0:
            prod.wells_live[ym][0 if env == 'off' else 1].add(well)
            if well not in prod.well_first or ym < prod.well_first[well][0]:
                prod.well_first[well] = (ym, env, field)
            prod.field_wells[field][ym].add(well)
            if ym >= recent_from:
                r = prod.well_recent[well][ym]
                r[0] += oil
                r[1] += gas
                prod.well_info[well] = (field, inst, env, cls)
                if inst:
                    prod.inst_wells[inst].add(well)
    prod.rows += n
    return n


def norm_field(s):
    """Feltnavn som i ANPs filer, med AnC-prefikset skrevet likt (ANC_TUPI = AnC_TUPI)."""
    s = re.sub(r'\s+', ' ', s or '').strip()
    if re.match(r'(?i)anc_', s):
        return 'AnC_' + s[4:].upper()
    return s.upper()


def classify_salt(well, field, ym, presal):
    """Pre-salt eller post-salt for en brønn til havs. ANPs arkiv (2005–2023)
    sier hvilke brønner som produserer fra pre-salt. Brønner som ikke finnes i
    arkivet (nye etter 2023) følger feltets fordeling i arkivets siste år, og
    nye felt i Santos-bassenget regnes som pre-salt."""
    if well in presal['wells']:
        return 'pre'
    if ym <= presal['last'] or well in presal['seen']:
        return 'post'
    share = presal['field_share'].get(field)
    if share is not None:
        return 'pre' if share >= 0.5 else 'post'
    return 'pre' if field in presal['santos_new'] else 'post'


def read_well_archive(store):
    """Arkivet «por poço» 2005–2023: zip per år eller måned med filene Mar,
    Terra og PreSal (semikolon, cp1252). Gir pre-salt-brønnene, operatør og
    kontrakt per felt og år, installasjonstype og API-grad per felt."""
    urls = links(PAGES['poco'], r'\.zip$')
    urls = [u for u in urls if 'producao' in u.lower()]
    if len(urls) < 20:
        raise DataError(f'For få filer i arkivet per brønn ({len(urls)})')
    presal_wells, seen = set(), set()
    last = '000000'
    op = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))   # felt → år → operatør
    contract = collections.defaultdict(collections.Counter)
    inst_type = collections.defaultdict(collections.Counter)
    api = collections.defaultdict(lambda: [0.0, 0.0])          # felt → [Σ API×olje, Σ olje] siste år
    salt_field = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))  # år → felt → [pre, alle]
    for u in urls:
        p = store.path(u, frozen=True)
        if not p or not zipfile.is_zipfile(p):
            log(f'  ! hopper over {u}')
            continue
        with zipfile.ZipFile(p) as z:
            for name in z.namelist():
                if not name.lower().endswith('.csv'):
                    continue
                kind = 'pre' if 'presal' in name.lower().replace('_', '').replace('-', '') else ('on' if 'terra' in name.lower() else 'off')
                head, rows = read_archive_csv(z.read(name))
                try:
                    iw, ifi, iop, ict, ipe = (col(head, n) for n in ('Nome Poço ANP', 'Campo', 'Operador', 'Número do Contrato', 'Período'))
                    ipet, igas = col(head, 'Petróleo (bbl/dia)'), col(head, 'Gás Natural (Mm³/dia) Total', 'Gás Natural (Mm³/dia) Gás Total')
                except DataError:
                    log(f'  ! ukjent format i {name}')
                    continue
                try:
                    iit, iinst = col(head, 'Tipo Instalação'), col(head, 'Instalação Destino')
                except DataError:
                    iit = iinst = None
                try:
                    iapi = col(head, 'Grau API')
                except DataError:
                    iapi = None
                for x in rows:
                    if len(x) <= igas:
                        continue
                    w = x[iw].strip()
                    ym = ym_of(x[ipe]) or ''
                    if ym > last:
                        last = ym
                    field = norm_field(x[ifi])
                    pet, gas = num(x[ipet]), num(x[igas])
                    wgt = pet + gas * BBL_PER_M3      # bbl/d + (Mm³/d → boe/d)
                    if kind == 'pre':
                        presal_wells.add(w)
                    seen.add(w)
                    y = ym[:4]
                    if kind != 'on' and y:
                        # Mar-filen har alle havbrønnene, PreSal-filen pre-salt-brønnene en gang til
                        salt_field[y][field][0 if kind == 'pre' else 1] += wgt
                    if kind == 'pre' and y:
                        # PreSal-filen gjentar brønnene i Mar; teller ikke dobbelt
                        continue
                    if y and x[iop].strip():
                        op[field][y][x[iop].strip()] += wgt or 1e-6
                    if x[ict].strip():
                        contract[field][re.sub(r'\D', '', x[ict])] += wgt or 1e-6
                    if iit is not None and len(x) > max(iit, iinst) and x[iinst].strip():
                        inst_type[re.sub(r'\s+', ' ', x[iinst]).strip()][x[iit].strip()] += 1
                    if iapi is not None and len(x) > iapi and y >= '2022':
                        a = num(x[iapi])
                        if 5 < a < 70 and pet > 0:
                            api[field][0] += a * pet
                            api[field][1] += pet
    if len(presal_wells) < 100:
        raise DataError(f'Bare {len(presal_wells)} pre-salt-brønner i arkivet')
    last_y = last[:4]
    field_share = {f: min(1.0, v[0] / v[1]) for f, v in salt_field.get(last_y, {}).items() if v[1] > 0}
    log(f'  arkiv per brønn: {len(urls)} filer, {len(seen)} brønner, {len(presal_wells)} pre-salt, til {last}')
    return {
        'wells': presal_wells, 'seen': seen, 'last': last, 'field_share': field_share, 'santos_new': set(),
        'op': op, 'contract': contract, 'inst_type': inst_type,
        'api': {f: round(v[0] / v[1], 1) for f, v in api.items() if v[1] > 0},
        'salt_field': salt_field,
    }


def read_archive_csv(b):
    """Filene i arkivet per brønn: eldre filer har fire linjer forord og
    overskriften over to linjer («Nome Poço» / «ANP», «Operador»)."""
    t = decode(b)
    rows = list(csv.reader(io.StringIO(t, newline=''), delimiter=';'))
    hi = next((i for i, r in enumerate(rows[:12]) if r and key(r[0]) == 'ESTADO'), None)
    if hi is None:
        return [], []
    head = [h.strip() for h in rows[hi]]
    body = rows[hi + 1:]
    if body and not body[0][0].strip() and len(body[0]) > 2 and key(body[0][2]) == 'ANP':
        sub = body[0]
        merged, prev = [], ''
        for i, h in enumerate(head):
            sb = sub[i].strip() if i < len(sub) else ''
            if h:
                prev = h
                merged.append(f'{h} {sb}'.strip() if sb else h)
            else:
                merged.append(f'{prev} {sb}'.strip() if sb else '')
        head = merged
        body = body[1:]
    return head, [r for r in body if len(r) > 5 and r[0].strip()]


def read_uf_totals(store):
    """Offisielle månedstotaler per delstat og land/hav: {(ym, env, delstat): [olje m³, gass 1000 m³]}.
    Paraná på land er utelatt: der er skiferolje (SIX, fra 2024) med i totalene,
    og den kommer ikke fra brønner."""
    urls = links(PAGES['uf'], r'\.csv$')
    out = collections.defaultdict(lambda: [0.0, 0.0])
    for u in urls:
        name = u.rsplit('/', 1)[-1].lower()
        kind = 0 if 'producao-petroleo' in name else 1 if 'producao-gas-natural' in name else None
        if kind is None:
            continue
        head, rows = read_csv(store.path(u))
        iy, im, iu, il, iv = col(head, 'ANO'), col(head, 'MÊS'), col(head, 'UNIDADE DA FEDERAÇÃO'), col(head, 'LOCALIZAÇÃO'), col(head, 'PRODUÇÃO')
        for x in rows:
            if len(x) <= iv:
                continue
            mon = MONTHS_PT.get(key(x[im])[:3].lower())
            if not mon:
                continue
            env = 'off' if key(x[il]).startswith('MAR') else 'on'
            st = key(x[iu])
            if env == 'on' and st == 'PARANA':
                continue
            out[(f'{x[iy].strip()}{mon:02d}', env, st)][kind] += num(x[iv])
    if len(out) < 1000:
        raise DataError('For få rader i produção por estado')
    return out


# ---------------------------------------------------------------- kontrakter

def read_concessions(store):
    u = [x for x in links(PAGES['rc'], r'relacao-concessionarios\.csv$')]
    if not u:
        raise DataError('Fant ikke relação de concessionários')
    head, rows = read_csv(store.path(u[0]), delim=';')
    ib, inr, ifa, ifn, iba, inm, ish, ia = (col(head, n) for n in ('BID', 'Número Contrato', 'Fase', 'Nome Fantasia', 'Bacia', 'Nome', '% Participação', 'Ambiente'))
    contracts = {}
    for x in rows:
        if len(x) <= ia:
            continue
        nr = re.sub(r'\D', '', x[inr])
        c = contracts.setdefault(nr, {'nr': nr, 'name': x[ifn].strip(), 'bid': x[ib].strip(), 'phase': x[ifa].strip(),
                                      'basin': x[iba].strip(), 'env': x[ia].strip(), 'holders': [], 'byPhase': collections.defaultdict(list)})
        nm = x[inm].strip()
        is_op = nm.startswith('(Operador)')
        nm = re.sub(r'^\(Operador\)\s*-\s*', '', nm).strip()
        # Samme kontrakt kan stå én gang per fase, og med flere områder etter hverandre (BM-S-9: Sapinhoá og
        # Lapa) under samme navn; en ny gruppe begynner når andelene har nådd 100 %
        groups = c['byPhase'][x[ifa].strip()]
        if not groups or sum(h[1] for h in groups[-1]) >= 99.5:
            groups.append([])
        groups[-1].append([nm, num(x[ish]), is_op])
    if len(contracts) < 300:
        raise DataError(f'Bare {len(contracts)} kontrakter i relação de concessionários')
    for c in contracts.values():
        bp = c.pop('byPhase')
        ph = next((p for p in bp if key(p).startswith('PRODU')), None) or next(iter(bp))
        c['phase'] = ph
        c['groups'] = [g for g in bp[ph] if g]
        c['holders'] = c['groups'][0]
        c['regime'] = regime_of(c['bid'])
        c['round'] = round_label(c['bid'])
        ops = [h[0] for h in c['holders'] if h[2]]
        c['operator'] = ops[0] if ops else (max(c['holders'], key=lambda h: h[1])[0] if c['holders'] else None)
    return contracts


# ---------------------------------------------------------------- geodata

def rdp(pts, tol):
    """Douglas–Peucker. Lukkede ringer deles i punktet lengst fra starten."""
    if len(pts) < 3:
        return pts
    if pts[0] == pts[-1] and len(pts) > 4:
        x0, y0 = pts[0]
        k = max(range(len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
        if 0 < k < len(pts) - 1:
            return rdp(pts[:k + 1], tol) + rdp(pts[k:], tol)[1:]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-12
        best, bi = -1, None
        for i in range(a + 1, b):
            d = abs(dy * (pts[i][0] - ax) - dx * (pts[i][1] - ay)) / L
            if d > best:
                best, bi = d, i
        if best > tol and bi is not None:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def shape_rings(shp, tol=0.002):
    rings = []
    parts = list(shp.parts) + [len(shp.points)]
    for a, b in zip(parts, parts[1:]):
        pts = [(round(x, 4), round(y, 4)) for x, y in shp.points[a:b]]
        r = rdp(pts, tol)
        if len(r) < 4:
            r = rdp(pts, tol / 10) if len(pts) >= 4 else r
        if len(r) >= 4:
            rings.append([[round(x, 3), round(y, 3)] for x, y in r])
    return rings


def centroid(rings):
    best = None
    for r in rings:
        a = sum(r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1] for i in range(len(r))) / 2
        if not best or abs(a) > abs(best[0]):
            best = (a, r)
    if not best:
        return None
    a, r = best
    if abs(a) < 1e-9:
        return [round(sum(p[0] for p in r) / len(r), 3), round(sum(p[1] for p in r) / len(r), 3)]
    cx = sum((r[i][0] + r[i - 1][0]) * (r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1]) for i in range(len(r))) / (6 * a)
    cy = sum((r[i][1] + r[i - 1][1]) * (r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1]) for i in range(len(r))) / (6 * a)
    return [round(cx, 3), round(cy, 3)]


def open_shp(store, pattern, frozen=False):
    """Shapefil fra en zip på geodatasiden → pyshp-leser (dbf-kodingen prøves)."""
    if shapefile is None:
        raise DataError('pyshp mangler (pip install pyshp)')
    u = [x for x in links(PAGES['geo'], pattern)]
    if not u:
        raise DataError(f'Fant ikke {pattern} på geodatasiden')
    p = store.path(u[0], frozen=frozen)
    d = p + '.d'
    if not os.path.isdir(d) or os.path.getmtime(d) < os.path.getmtime(p):
        os.makedirs(d, exist_ok=True)
        with zipfile.ZipFile(p) as z:
            z.extractall(d)
        os.utime(d)
    shp = glob.glob(os.path.join(d, '**', '*.shp'), recursive=True)
    if not shp:
        raise DataError(f'Ingen .shp i {u[0]}')
    base = shp[0][:-4]
    for enc in ('utf-8', 'latin1'):
        try:
            r = shapefile.Reader(base, encoding=enc)
            r.record(0)
            for i in range(0, len(r), max(1, len(r) // 50)):
                r.record(i)
            return r, os.path.basename(base), enc
        except Exception:
            continue
    raise DataError(f'Kunne ikke lese {base}')


def recs(r):
    names = [f[0] for f in r.fields[1:]]
    for i in range(len(r)):
        yield i, dict(zip(names, r.record(i)))


def read_field_shapes(store):
    r, name, _ = open_shp(store, r'campos-producao\.zip$')
    out = {}
    for i, rec in recs(r):
        rings = shape_rings(r.shape(i))
        out[key(rec.get('NOM_CAMPO'))] = {
            'name': str(rec.get('NOM_CAMPO') or '').strip(),
            'contract': re.sub(r'\D', '', str(rec.get('NUM_CONTRA') or '')),
            'round': str(rec.get('NUM_RODADA') or '').strip(),
            'op': str(rec.get('OPERADOR_C') or '').strip(),
            'disc': date_iso(str(rec.get('DAT_DESCOB') or '')),
            'start': date_iso(str(rec.get('DAT_INICIO') or '')),
            'stage': str(rec.get('ETAPA') or '').strip(),
            'wd': float(rec.get('MED_LAMINA') or 0) or None,
            'fluid': str(rec.get('FLUIDO_PRI') or '').strip(),
            'area': float(rec.get('AREA') or 0) or None,
            'env': str(rec.get('Localizaca') or '').strip(),
            'rings': rings,
        }
    log(f'  feltomriss: {len(out)} ({name})')
    return out, name


def read_block_shapes(store):
    r, name, _ = open_shp(store, r'blocos-exploratorios\.zip$')
    out = {}
    for i, rec in recs(r):
        out[key(rec.get('NOM_BLOCO') or rec.get('COD_BLOCO'))] = shape_rings(r.shape(i), tol=0.003)
    log(f'  blokkomriss: {len(out)} ({name})')
    return out, name


WELL_CAT = {'1': 'wildcat', '2': 'strat', '3': 'extension', '4': 'adjacent', '5': 'shallower', '6': 'deeper',
            '7': 'development', '8': 'injection', '9': 'special'}


def read_well_shapes(store):
    """Brønnene i ANPs brønnregister (shapefil, «tabelão»): posisjon, type,
    status, vanndyp, rigg og om brønnen nådde pre-salt."""
    r, name, _ = open_shp(store, r'shapefile-de-pocos\.zip$')
    names = [f[0] for f in r.fields[1:]]
    want = ['POCO', 'OPERADOR', 'CAMPO', 'BLOCO', 'BACIA', 'ESTADO', 'TERRA_MAR', 'TIPO', 'CATEGORIA', 'RECLASSIFI', 'SITUACAO',
            'INICIO', 'TERMINO', 'CONCLUSAO', 'LAT_DD', 'LONG_DD', 'LAMINA_DAG', 'PROF_SOND', 'NOME_SONDA', 'AT_PRESAL']
    idx = {k: names.index(k) for k in want if k in names}
    out = {}
    for i in range(len(r)):
        rec = r.record(i)
        g = lambda k: rec[idx[k]] if k in idx else None
        w = str(g('POCO') or '').strip()
        if not w:
            continue
        try:
            lat, lon = float(g('LAT_DD') or 0), float(g('LONG_DD') or 0)
        except (TypeError, ValueError):
            lat = lon = 0
        out[w] = {
            'op': str(g('OPERADOR') or '').strip(), 'field': str(g('CAMPO') or '').strip(), 'block': str(g('BLOCO') or '').strip(),
            'basin': str(g('BACIA') or '').strip(), 'state': str(g('ESTADO') or '').strip(), 'env': 'off' if str(g('TERRA_MAR')).strip() == 'M' else 'on',
            'cat': WELL_CAT.get(str(g('TIPO') or '').strip(), 'other'), 'result': str(g('RECLASSIFI') or '').strip(),
            'status': str(g('SITUACAO') or '').strip(), 'spud': date_iso(str(g('INICIO') or '')), 'end': date_iso(str(g('CONCLUSAO') or '') or str(g('TERMINO') or '')),
            'lat': round(lat, 4) if lat else None, 'lon': round(lon, 4) if lon else None,
            'wd': float(g('LAMINA_DAG') or 0) or None, 'td': float(g('PROF_SOND') or 0) or None,
            'rig': str(g('NOME_SONDA') or '').strip(), 'presal': str(g('AT_PRESAL') or '').strip() == 'S',
        }
    log(f'  brønnregister: {len(out)} brønner ({name})')
    return out, name


# ---------------------------------------------------------------- reserver

def read_bar(store):
    """BAR-tabellene (xlsx): VOIP, VGIP, akkumulert produksjon og
    utvinningsgrad per felt og år."""
    if openpyxl is None:
        raise DataError('openpyxl mangler')
    pages = [RES_PAGE, RES_PAGE + '?b_start:int=20', RES_PAGE + '?b_start:int=40']
    urls = []
    for pg in pages:
        try:
            urls += [u for u in links(pg, r'(?i)bar-\d{4}\.xlsx') if u not in urls]
        except DataError:
            pass
    urls = [re.sub(r'/view$', '', u) for u in urls]
    by_year = {}
    years = sorted({int(re.search(r'(\d{4})\.xlsx', u).group(1)) for u in urls})
    for u in sorted(set(urls)):
        y = int(re.search(r'(\d{4})\.xlsx', u).group(1))
        p = store.path(u, frozen=y < years[-1])
        if not p:
            continue
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
        head = [str(h or '') for h in rows[0]]
        try:
            ifi = col(head, 'Campo/Área')
            iba, ies, iv, ig, ico, icg, irf, isi = (col(head, n) for n in ('Bacia', 'Estado', 'VOIP', 'VGIP', 'Petróleo Acumulado', 'Gás Natural Acumulado', 'Fração Recuperada', 'Situação'))
        except DataError as e:
            log(f'  ! BAR {y}: {e}')
            continue
        d = {}
        need = max(ifi, iba, ies, iv, ig, ico, icg, irf, isi)
        for r in rows[1:]:
            if not r or len(r) <= need or not r[ifi]:
                continue
            d[key(r[ifi])] = {'name': str(r[ifi]).strip(), 'basin': r[iba], 'state': r[ies], 'voip': float(r[iv] or 0), 'vgip': float(r[ig] or 0),
                              'cumO': float(r[ico] or 0), 'cumG': float(r[icg] or 0), 'rf': float(r[irf] or 0), 'mature': str(r[isi] or '').strip()}
        by_year[y] = d
    if not by_year:
        raise DataError('Fant ingen BAR-tabeller')
    log(f'  BAR: {sorted(by_year)}')
    return by_year


def pdf_text(path):
    try:
        return subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def parse_basin_table(lines, start):
    """Rader «Bacia  1P 2P 3P Contingentes» fra linjen etter start til «Total»."""
    out = {}
    for ln in lines[start + 1:start + 40]:
        m = re.match(r'\s*([A-Za-zÀ-ú][A-Za-zÀ-ú .]+?)\s{2,}([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)(?:\s+([\d.,]+))?\s*$', ln)
        if not m:
            continue
        name = m.group(1).strip()
        vals = [num(m.group(i)) for i in (2, 3, 4, 5) if m.group(i) is not None]
        out[name] = vals
        if key(name) == 'TOTAL':
            break
    return out


def read_reserve_pdfs(store):
    """Boletim anual de recursos e reservas (PDF): 1P/2P/3P og betingede
    ressurser per basseng (siste utgave), og totalene per år så langt
    tabellene lar seg lese."""
    pages = [RES_PAGE, RES_PAGE + '?b_start:int=20', RES_PAGE + '?b_start:int=40']
    urls = []
    for pg in pages:
        try:
            urls += [re.sub(r'/view$', '', u) for u in links(pg, r'(?i)(boletim[-_]anual[-_]reservas|boletim[-_]reservas)[-_]\d{4}\.pdf')]
        except DataError:
            pass
    urls = sorted(set(urls), key=lambda u: -int(re.search(r'(\d{4})\.pdf', u).group(1)))
    # Nyeste boletin som lar seg lese: tabellene per basseng og grafene med
    # utviklingen over tid (tallene over søylene, plassert etter årsaksen)
    for u in urls[:3]:
        y = int(re.search(r'(\d{4})\.pdf', u).group(1))
        p = store.path(u, frozen=True)
        if not p:
            continue
        lines = pdf_text(p).splitlines()
        tabs = {'all': {}, 'off': {}, 'on': {}}
        mode = 'all'
        for i, ln in enumerate(lines):
            k = key(ln)
            # Tabell 1–2: alle miljøer, 3–4: hav, 5–6: land
            m = re.match(r'\s*TABELA (\d)', k)
            if m:
                mode = {'1': 'all', '3': 'off', '5': 'on'}.get(m.group(1), mode)
            if 'PETROLEO (MILHOES DE BARRIS' in k:
                t = parse_basin_table(lines, i)
                if t and 'oil' not in tabs[mode]:
                    tabs[mode]['oil'] = t
            elif 'GAS (MILHOES DE METROS CUBICOS' in k or 'GAS NATURAL (MILHOES DE METROS CUBICOS' in k:
                t = parse_basin_table(lines, i)
                if t and 'gas' not in tabs[mode]:
                    tabs[mode]['gas'] = t
        a = tabs['all']
        tot = lambda t: next((v for n, v in t.items() if key(n) == 'TOTAL'), None) if t else None
        to, tg = tot(a.get('oil')), tot(a.get('gas'))
        if not (to and tg and len(to) >= 3 and len(tg) >= 3 and 5000 < to[0] < 60000):
            log(f'  ! reservetabellen i boletinen {y} lot seg ikke lese')
            continue
        charts = {}
        for n, label in ((1, 'oil'), (2, 'gas'), (3, 'oilSalt'), (4, 'gasSalt')):
            c = parse_chart(lines, n, 3 if n <= 2 else 2)
            if c:
                charts[label] = c
        # Grafene må stemme med tabellen for siste år, ellers brukes de ikke
        hist = []
        oc, gc = charts.get('oil'), charts.get('gas')
        if oc and gc and y in oc and y in gc and abs(oc[y][0] - to[0]) <= max(2, to[0] * 0.002) and abs(gc[y][0] - tg[0]) <= max(2, tg[0] * 0.002):
            for yy in sorted(oc):
                if yy in gc:
                    o, g = oc[yy], gc[yy]
                    hist.append([yy, [o[0], o[0] + o[1], o[0] + o[1] + o[2]], [g[0], g[0] + g[1], g[0] + g[1] + g[2]],
                                 (charts.get('oilSalt') or {}).get(yy), (charts.get('gasSalt') or {}).get(yy)])
        else:
            log(f'  ! grafene i boletinen {y} stemmer ikke med tabellen; bare siste år')
            hist = [[y, to[:3], tg[:3], None, None]]
        basins = {}
        for mode in ('all', 'off', 'on'):
            for pr in ('oil', 'gas'):
                for nm, v in (tabs[mode].get(pr) or {}).items():
                    if key(nm) == 'TOTAL':
                        continue
                    basins.setdefault(nm, {}).setdefault(mode, {})[pr] = v
        log(f'  reserver: boletin {y}, {len(basins)} bassenger, historikk {hist[0][0]}–{hist[-1][0]}')
        return {'year': y, 'basins': basins, 'total': {'oil': to[:4], 'gas': tg[:4]}, 'history': hist}
    log('  ! fant ingen lesbar reservetabell i boletinene')
    return {'year': None, 'basins': {}, 'total': None, 'history': []}


def parse_chart(lines, n, stack):
    """Tallene i «Gráfico n» (stablede søyler, ett tall per segment) per år.
    Årsaksen er linjen med årstallene; hvert tall knyttes til nærmeste år etter
    tegnposisjon, og segmentene leses nedenfra (nærmest aksen først)."""
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf'\s*Gr[aá]fico {n}\.', ln) and i + 3 < len(lines):
            start = i
            break
    if start is None:
        return None
    axis = None
    for i in range(start + 1, min(start + 60, len(lines))):
        ys = list(re.finditer(r'\b(20[0-4]\d)\b', lines[i]))
        if len(ys) >= 5 and not re.search(r'[A-Za-z]{4,}', lines[i]):
            axis = i
            break
    if axis is None:
        return None
    cols = [((m.start() + m.end()) / 2, int(m.group(1))) for m in re.finditer(r'\b(20[0-4]\d)\b', lines[axis])]
    step = (cols[-1][0] - cols[0][0]) / max(1, len(cols) - 1)
    vals = collections.defaultdict(list)
    for i in range(axis - 1, start, -1):
        if re.search(r'Gr[aá]fico', lines[i]):
            break
        if re.search(r'[A-Za-z]{3,}|[()]', lines[i]):
            continue
        for m in re.finditer(r'(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?(?![\d.,])', lines[i]):
            c = (m.start() + m.end()) / 2
            if c < cols[0][0] - step * 0.6 or c > cols[-1][0] + step * 0.6:
                continue
            cx, yy = min(cols, key=lambda t: abs(t[0] - c))
            vals[yy].append(num(m.group(0)))
    out = {}
    for yy, v in vals.items():
        if len(v) == stack:
            out[yy] = v
    return out if len(out) >= len(cols) - 1 else None


def read_forecast(store):
    u = links(PAGES['dp'], r'previsao-producao\.csv$')
    if not u:
        return None
    head, rows = read_csv(store.path(u[0]))
    ib, ia, iy, iv, ig = (col(head, n) for n in ('BACIA', 'AMBIENTE', 'ANO', 'VOLUME PETRÓLEO', 'VOLUME GÁS'))
    out = collections.defaultdict(dict)
    for x in rows:
        if len(x) <= ig:
            continue
        env = 'off' if key(x[ia]).startswith('MAR') else 'on'
        k = f'{x[ib].strip()}|{env}'
        # olje m³/år, gass 1000 m³/år → per måned i samme enheter som produksjonen (1000 m³, mill. m³)
        out[k][x[iy].strip()] = [cn(num(x[iv]) / 1000 / 12), cn(num(x[ig]) / 1000 / 12)]
    return {'unit': 'per month: oil thousand m³, gas million m³', 'series': out}


# ---------------------------------------------------------------- leting

def latest_snapshot(urls):
    """Siste månedsfil: år fra mappen, måned fra filnavnet."""
    best, bk = None, None
    for u in urls:
        m = re.search(r'/(20\d\d)/([^/]+)\.csv$', u)
        if not m:
            continue
        nm = key(m.group(2)).lower()
        mon = next((v for k, v in MONTHS_PT.items() if re.search(r'(^|-)' + k, nm)), None)
        if mon is None:
            continue
        k = (int(m.group(1)), mon)
        if bk is None or k > bk:
            best, bk = u, k
    return best, bk


def snapshot_urls(kind):
    return links(PAGES['exp'], rf'/painel-dinamico-da-fase-de-exploracao/{kind}/20\d\d/[^"]+\.csv$')


def parse_holders(s):
    """«*Petrobras - 60%, BP Energy - 40%» → [[navn, 60.0, operatør?], …]."""
    out = []
    for part in re.split(r',\s*(?=\*?[^,]+ - [\d.,]+%)', s or ''):
        m = re.match(r'\s*(\*?)(.+?)\s+-\s+([\d.,]+)%', part)
        if m:
            v = m.group(3)
            v = float(v.replace(',', '.')) if v.count('.') <= 1 else num(v)
            out.append([m.group(2).strip(), round(v, 3), bool(m.group(1))])
    return out


def read_exploration(store, wells_reg, block_shapes):
    out = {}
    # Blokker under kontrakt (siste måned) og antall over tid
    urls = snapshot_urls('bsc')
    u, when = latest_snapshot(urls)
    if not u:
        raise DataError('Fant ikke blocos sob contrato')
    head, rows = read_csv(store.path(u), delim=';')
    I = {n: col(head, n) for n in ('BLOCO', 'CONTRATO', 'OPERADOR', 'CONTRATADOS', 'AMBIENTE', 'BACIAS AGRUPADAS', 'BACIA', 'ÁREA',
                                    'RODADA', 'REGIME CONTRATUAL', 'PERÍODO', 'PRESENÇA PAD', 'DATA ASSINATURA', 'FASE EXPLORATÓRIA VENCIMENTO',
                                    'QTD POÇOS PERFURADOS')}
    blocks = []
    for x in rows:
        if len(x) <= I['QTD POÇOS PERFURADOS']:
            continue
        g = lambda k: x[I[k]].strip()
        blocks.append({
            'block': g('BLOCO'), 'contract': re.sub(r'\D', '', g('CONTRATO')), 'op': g('OPERADOR'), 'opG': group_of(g('OPERADOR')),
            'holders': parse_holders(g('CONTRATADOS')), 'env': 'off' if key(g('AMBIENTE')).startswith('MAR') else 'on',
            'region': g('BACIAS AGRUPADAS'), 'basin': g('BACIA'), 'area': round(num(g('ÁREA')) * 1000, 1),
            'round': round_label(g('RODADA')), 'bid': g('RODADA'), 'regime': 'Production sharing' if 'PARTILHA' in key(g('REGIME CONTRATUAL')) else 'Concession',
            'period': g('PERÍODO'), 'pad': g('PRESENÇA PAD'), 'signed': date_iso(g('DATA ASSINATURA')), 'expires': date_iso(g('FASE EXPLORATÓRIA VENCIMENTO')),
            'wells': int(num(g('QTD POÇOS PERFURADOS'))), 'shape': key(g('BLOCO')) in block_shapes,
        })
    out['blocks'] = {'asOf': f'{when[0]}-{when[1]:02d}', 'list': blocks}
    hist = []
    for uu in urls:
        m = re.search(r'/(20\d\d)/([^/]+)\.csv$', uu)
        _, k = latest_snapshot([uu])
        if not k:
            continue
        try:
            h2, r2 = read_csv(store.path(uu, frozen=k < when), delim=';')
            ie, ia = col(h2, 'AMBIENTE'), col(h2, 'ÁREA')
        except DataError:
            continue
        n = collections.Counter()
        area = collections.Counter()
        for x in r2:
            if len(x) > max(ie, ia):
                e = 'off' if key(x[ie]).startswith('MAR') else 'on'
                n[e] += 1
                area[e] += num(x[ia]) * 1000
        hist.append([f'{k[0]}-{k[1]:02d}', n['off'], n['on'], round(area['off']), round(area['on'])])
    out['blockHistory'] = sorted({h[0]: h for h in hist}.values())
    # Letebrønner (alle under kontraktsregimet, 1998–)
    u, when = latest_snapshot(snapshot_urls('pocos-exploratorios'))
    head, rows = read_csv(store.path(u), delim=';')
    I = {n: col(head, n) for n in ('POÇO ANP', 'BLOCO', 'BACIA', 'BACIAS AGRUPADAS', 'ESTADO', 'AMBIENTE', 'OPERADOR', 'INÍCIO DA PERFURAÇÃO',
                                    'CONCLUSÃO DO POÇO', 'PROFUNDIDADE FINAL', 'ATINGIU PRÉ-SAL', 'NOTIFICAÇÃO DE DESCOBERTA', 'DATA 1ª DESCOBERTA',
                                    'FLUIDO NOTIFICAÇÃO')}
    ew = []
    for x in rows:
        if len(x) <= I['FLUIDO NOTIFICAÇÃO']:
            continue
        g = lambda k: x[I[k]].strip()
        w = g('POÇO ANP')
        reg = wells_reg.get(w) or {}
        ew.append({
            'well': w, 'block': g('BLOCO'), 'basin': g('BACIA'), 'region': g('BACIAS AGRUPADAS'), 'state': g('ESTADO'),
            'env': 'off' if key(g('AMBIENTE')).startswith('MAR') else 'on', 'op': g('OPERADOR'), 'opG': group_of(g('OPERADOR')),
            'spud': date_iso(g('INÍCIO DA PERFURAÇÃO')), 'end': date_iso(g('CONCLUSÃO DO POÇO')), 'td': num(g('PROFUNDIDADE FINAL')) or None,
            'presal': key(g('ATINGIU PRÉ-SAL')) == 'SIM', 'disc': key(g('NOTIFICAÇÃO DE DESCOBERTA')) == 'SIM',
            'discDate': date_iso(g('DATA 1ª DESCOBERTA')), 'fluid': g('FLUIDO NOTIFICAÇÃO'),
            'cat': reg.get('cat'), 'wd': reg.get('wd'), 'lat': reg.get('lat'), 'lon': reg.get('lon'), 'rig': reg.get('rig') or None,
            'result': reg.get('result') or None,
        })
    out['wells'] = {'asOf': f'{when[0]}-{when[1]:02d}', 'list': ew}
    # PAD-er og kommersialitetserklæringer
    for kind, name, cols in (('pads-andamento', 'padsActive', ('CÓDIGO DO PAD', 'BLOCO', 'BACIA', 'AMBIENTE', 'OPERADOR', 'CONTRATADOS', 'RODADA', 'SITUAÇÃO', 'INÍCIO EFETIVO', 'TÉRMINO PREVISTO')),
                             ('pads-concluidos', 'padsDone', ('CÓDIGO DO PAD', 'BLOCO', 'BACIA', 'AMBIENTE', 'OPERADOR', 'RODADA', 'INÍCIO EFETIVO', 'TÉRMINO EFETIVO', 'DECLAROU COMERCIALIDADE', 'ÁREA DE DESENVOLVIMENTO')),
                             ('dcfe', 'commerciality', ('CÓDIGO DO PAD', 'BLOCO', 'BACIA', 'AMBIENTE', 'OPERADOR', 'RODADA', 'DATA DECLARAÇÃO COMERCIALIDADE', 'ÁREA DE DESENVOLVIMENTO'))):
        u, when = latest_snapshot(snapshot_urls(kind))
        if not u:
            out[name] = []
            continue
        head, rows = read_csv(store.path(u), delim=';')
        try:
            I = [col(head, c) for c in cols]
        except DataError as e:
            log(f'  ! {kind}: {e}')
            out[name] = []
            continue
        lst = []
        for x in rows:
            if len(x) <= max(I):
                continue
            vals = [x[i].strip() for i in I]
            vals = [date_iso(v) or v if re.match(r'\d{1,2}/\d{1,2}/\d{4}', v) else v for v in vals]
            lst.append(vals)
        out[name] = lst
    out['padCols'] = {'padsActive': ['pad', 'block', 'basin', 'env', 'op', 'holders', 'round', 'status', 'start', 'end'],
                      'padsDone': ['pad', 'block', 'basin', 'env', 'op', 'round', 'start', 'end', 'commercial', 'area'],
                      'commerciality': ['pad', 'block', 'basin', 'env', 'op', 'round', 'date', 'area']}
    return out


def read_rounds(store):
    urls = links(PAGES['rounds'], r'\.csv$')
    get_u = lambda pat: next((u for u in urls if re.search(pat, u)), None)
    out = {'offered': [], 'awarded': [], 'transfers': []}
    u = get_u(r'blocos-ofertados')
    if u:
        head, rows = read_csv(store.path(u), delim=';')
        ir, ib, ise, ibl, ibo, il, ia = (col(head, n) for n in ('Rodada', 'Bacia', 'Setor', 'Bloco', 'Bonus Mínimo', 'Localização', 'Bloco arrematado'))
        for x in rows:
            if len(x) > ia:
                out['offered'].append([rnum(x[ir]), x[ib].strip(), x[ise].strip(), x[ibl].strip(), cn(num(x[ibo]) / 1e6), x[il].strip(), key(x[ia]) == 'SIM'])
    u = get_u(r'ofertas-vencedoras')
    if u:
        head, rows = read_csv(store.path(u), delim=';')
        ir, ib, ise, ibl, ie, ibo = (col(head, n) for n in ('Rodada', 'Bacia', 'Setor', 'Bloco', 'Empresa', 'Bônus'))
        try:
            ipem = col(head, 'PEM (R$)')
        except DataError:
            ipem = None
        for x in rows:
            if len(x) > ibo:
                hs = []
                for part in re.split(r'\s*/\s*|\s*;\s*|,\s*(?=[A-Z])', x[ie]):
                    m = re.match(r'\s*(.+?)\s*\(([\d.,]+)%\)\s*(\*)?', part)
                    if m:
                        hs.append([m.group(1).strip(), num(m.group(2)), bool(m.group(3))])
                out['awarded'].append([rnum(x[ir]), x[ib].strip(), x[ise].strip(), x[ibl].strip(), x[ie].strip(), hs,
                                       cn(num(x[ibo]) / 1e6), cn(num(x[ipem]) / 1e6) if ipem is not None and len(x) > ipem else None])
    u = get_u(r'cessao-contratos')
    if u:
        head, rows = read_csv(store.path(u), delim=';')
        I = {n: col(head, n) for n in ('Tipo de solicitação', 'Data do requerimento', 'Rodada', 'Contrato', 'Bacia', 'Ambiente operacional',
                                        'Antes da cessão', 'Cedente', 'Cessionária', 'Após efetivação', 'Data da aprovação', 'Assinatura do Termo')}
        for x in rows:
            if len(x) <= I['Assinatura do Termo'] or not x[I['Contrato']].strip():
                continue
            g = lambda k: x[I[k]].strip()
            m = re.search(r'\(([^)]+)\)', g('Contrato'))
            out['transfers'].append({
                'type': g('Tipo de solicitação'), 'req': date_iso(g('Data do requerimento')), 'round': g('Rodada'),
                'contract': m.group(1).strip() if m else g('Contrato'), 'basin': g('Bacia'), 'env': g('Ambiente operacional'),
                'before': [l.strip() for l in g('Antes da cessão').splitlines() if l.strip()],
                'from': g('Cedente'), 'to': g('Cessionária'), 'fromG': group_of(g('Cedente')), 'toG': group_of(g('Cessionária')),
                'after': [l.strip() for l in g('Após efetivação').splitlines() if l.strip()],
                'approved': date_iso(g('Data da aprovação')), 'signed': date_iso(g('Assinatura do Termo')) or g('Assinatura do Termo')[:40],
            })
    return out


def rnum(s):
    m = re.match(r'\s*0*(\d+)', s or '')
    return int(m.group(1)) if m else s.strip()


def read_pe(store):
    """Særskatt (participação especial) per felt og kvartal, R$."""
    u = [x for x in links(PAGES['pg'], r'pe_campo\.csv$')]
    if not u:
        return {}
    head, rows = read_csv(store.path(u[0]), delim=';')
    ic, ifi, iv = col(head, 'competencia'), col(head, 'campo'), col(head, 'valor')
    out = collections.defaultdict(lambda: collections.defaultdict(float))
    for x in rows:
        if len(x) <= iv:
            continue
        d = excel_date(x[ic]) or date_iso(x[ic])
        if not d:
            continue
        out[key(x[ifi])][d[:4]] += num(x[iv])
    return out


def read_incidents(store):
    urls = links(PAGES['issm'], r'\.csv$')
    get_u = lambda pat: next((u for u in urls if re.search(pat, u)), None)
    u = get_u(r'/incidentes\.csv$')
    if not u:
        return None
    head, rows = read_csv(store.path(u), delim=';')
    I = {n: col(head, n) for n in ('Numero', 'Empresa', 'Data_de_criacao', 'Instalacao', 'Latitude', 'Longitude', 'Data_estimada_do_incidente',
                                    'Numero_de_feridos_graves', 'Numero_Fatalidades', 'Breve_Descricao_Incidente')}
    inc = {}
    for x in rows:
        if len(x) <= I['Breve_Descricao_Incidente']:
            continue
        g = lambda k: x[I[k]].strip()
        d = date_iso(g('Data_estimada_do_incidente')) or date_iso(g('Data_de_criacao'))
        if not d:
            continue
        comp = re.sub(r'\s*\(\d+\)\s*$', '', g('Empresa')).strip()
        inc[g('Numero')] = {'id': g('Numero'), 'date': d, 'company': comp, 'group': group_of(comp), 'inst': re.sub(r'\s+', ' ', g('Instalacao')),
                            'lat': round(num(g('Latitude')), 3) or None, 'lon': round(num(g('Longitude')), 3) or None,
                            'inj': int(num(g('Numero_de_feridos_graves'))), 'fat': int(num(g('Numero_Fatalidades'))),
                            'desc': re.sub(r'\s+', ' ', g('Breve_Descricao_Incidente'))[:200], 'types': [], 'sev': None, 'acc': None, 'cls': []}
    u = get_u(r'incidentes-tipo\.csv$')
    if u:
        head, rows = read_csv(store.path(u), delim=';')
        for x in rows:
            if len(x) >= 4 and x[0].strip() in inc:
                r = inc[x[0].strip()]
                if x[1].strip() and x[1].strip() not in r['types']:
                    r['types'].append(x[1].strip())
                sev = x[2].strip().upper()
                order = ['', 'LEVE', 'MODERADO', 'GRAVE']
                if sev and (r['sev'] is None or (order.index(sev) if sev in order else 0) > (order.index(r['sev']) if r['sev'] in order else 0)):
                    r['sev'] = sev
                if x[3].strip():
                    r['acc'] = 'acc' if key(x[3]).startswith('ACIDENTE') or r['acc'] == 'acc' else 'near'
    u = get_u(r'incidentes-classificacao\.csv$')
    if u:
        head, rows = read_csv(store.path(u), delim=';')
        for x in rows:
            if len(x) >= 2 and x[0].strip() in inc and x[1].strip() not in inc[x[0].strip()]['cls']:
                inc[x[0].strip()]['cls'].append(x[1].strip())
    return list(inc.values())


# ---------------------------------------------------------------- hoved

def series_from(dct, months):
    """{ym: [o, g, w]} → [[o…], [g…], [w…]] på aksen months (1000 m³ / mill. m³)."""
    out = [[], [], []]
    for ym in months:
        v = dct.get(ym)
        out[0].append(cn(v[0] / 1000) if v else 0)
        out[1].append(cn(v[1] / 1000) if v else 0)
        out[2].append(cn(v[2] / 1000) if v else 0)
    return out


def boe_m(v):
    """[olje m³, gass 1000 m³, …] → boe."""
    return (v[0] + v[1]) * BBL_PER_M3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', help='mappe for nedlastede filer')
    ap.add_argument('--skip-archive', action='store_true', help='ikke les arkivet per brønn (raskere testing)')
    args = ap.parse_args()
    store = Store(args.cache)
    t0 = time.time()
    try:
        return build(store, args)
    except DataError as e:
        log(f'FEIL: {e}')
        return 1
    finally:
        log(f'Tid: {time.time() - t0:.0f} s')


def build(store, args):
    aliases = {}
    if os.path.exists(ALIASES):
        aliases = json.load(open(ALIASES, encoding='utf-8'))

    log('Arkiv per brønn (pre-salt, operatører, kontrakter)…')
    if args.skip_archive:
        arch = {'wells': set(), 'seen': set(), 'last': '202312', 'field_share': {}, 'santos_new': set(), 'op': {}, 'contract': {}, 'inst_type': {}, 'api': {}, 'salt_field': {}}
    else:
        arch = read_well_archive(store)

    log('Feltomriss, blokker og brønnregister…')
    fshapes, fshape_name = read_field_shapes(store)
    bshapes, bshape_name = read_block_shapes(store)
    wreg, wreg_name = read_well_shapes(store)

    log('Produksjon per brønn…')
    pm, pt = production_links()
    prod = Prod()
    recent_from = f'{THIS_YEAR - 2}01'
    # Nye felt (ikke i arkivet) i Santos-bassenget regnes som pre-salt. De
    # finnes ved en rask lesing av havfilene etter arkivets siste år.
    arch_fields = set(arch['field_share'])
    santos_hint = set()
    for u in pm:
        ys = file_years(u)
        if ys and max(ys) >= int(arch['last'][:4] or 2023) + 1:
            p = store.path(u, frozen=max(ys) <= THIS_YEAR - 2)
            head, rows = read_csv(p)
            ib, ifi = col(head, 'Bacia'), col(head, 'Campo')
            for x in rows:
                if len(x) > ifi and key(x[ib]) == 'SANTOS':
                    f = norm_field(x[ifi])
                    if f not in arch_fields:
                        santos_hint.add(f)
    arch['santos_new'] = santos_hint
    for u in pm + pt:
        n = read_production(store, prod, u, arch, recent_from)
        log(f'  {u.rsplit("/", 1)[-1]}: {n} rader')
    months_all = sorted(prod.total)
    first, last = months_all[0], months_all[-1]
    # Siste måned må ha både hav og land; ellers kuttes den (landfilene kommer kvartalsvis)
    while last > first and (prod.env['on'].get(last, [0])[0] <= 0 or prod.env['off'].get(last, [0])[0] <= 0):
        last = ym_add(last, -1)
    months = ym_range(first, last)
    missing = [m for m in months if m not in prod.total]
    if missing:
        raise DataError(f'Måneder uten produksjon: {missing[:10]}')
    log(f'Produksjon {first}–{last}: {prod.rows} rader, {len(prod.field)} felt')

    # Kontroll mot ANPs offisielle totaler per delstat (hav/land)
    uf = read_uf_totals(store)
    off_m = collections.defaultdict(lambda: [0.0, 0.0])
    for (ym, env, st), v in uf.items():
        t = off_m[(ym, env)]
        t[0] += v[0]
        t[1] += v[1]
    # Måneder der landfilene mangler brønner (ANP har hull i kvartalsfilene): under 97 % av totalen
    gaps = []
    for ym in months:
        if ym < '200601':
            continue
        o = off_m.get((ym, 'on'))
        ours = prod.env['on'].get(ym, [0, 0, 0])
        if o and o[0] > 0 and ours[0] / o[0] < 0.97:
            gaps.append(ym)
    if len(gaps) > 24:
        raise DataError(f'For mange måneder med hull i produksjonen på land: {gaps}')
    bad = []
    for y in range(1997, int(last[:4]) + 1):
        ys = str(y)
        ms = [f'{ys}{m:02d}' for m in range(1, 13) if f'{ys}{m:02d}' <= last and f'{ys}{m:02d}' not in gaps]
        for env in ('off', 'on'):
            ours = sum(prod.env[env].get(m, [0])[0] for m in ms)
            theirs = sum(off_m.get((m, env), [0])[0] for m in ms)
            tol = 0.01 if env == 'off' else 0.02 if y >= 2006 else 0.04
            if theirs and abs(ours / theirs - 1) > tol:
                bad.append(f'{ys} {env}: {ours / theirs * 100:.2f} %')
    if bad:
        raise DataError('Produksjonen per brønn avviker fra ANPs totaler: ' + ', '.join(bad))
    log(f'  kontroll mot totalene per delstat: OK' + (f'; hull på land fylt fra totalene: {gaps}' if gaps else ''))
    # Hull på land: totalene (land, nasjonalt, pre-salt-fordelingen og delstatene) fylles med differansen
    state_name = {key(s): s for s in prod.state}
    gap_out = []
    for ym in gaps:
        o = off_m[(ym, 'on')]
        ours = prod.env['on'].get(ym, [0, 0, 0])
        d = [max(0.0, o[0] - ours[0]), max(0.0, o[1] - ours[1])]
        for tgt in (prod.total[ym], prod.env['on'][ym], prod.salt['onshore'][ym]):
            tgt[0] += d[0]
            tgt[1] += d[1]
        for (ym2, env, st), v in uf.items():
            if ym2 != ym or env != 'on':
                continue
            mine = prod.state_on.get((st, ym), [0, 0])
            ds = [max(0.0, v[0] - mine[0]), max(0.0, v[1] - mine[1])]
            nm = state_name.get(st, st.title())
            prod.state[nm][ym][0] += ds[0]
            prod.state[nm][ym][1] += ds[1]
        gap_out.append([ym, round(ours[0] / o[0], 3) if o[0] else 0])

    log('Kontrakter og andeler…')
    contracts = read_concessions(store)
    by_name = collections.defaultdict(list)
    for c in contracts.values():
        by_name[key(c['name']).replace('_EXC', '_ECO')].append(c)

    # Felt
    n12 = months[-12:]
    days12 = sum(days_in(m) for m in n12)
    known = {f.upper() for f in prod.field if not re.match(r'(?i)anc_', f)}
    bar = read_bar(store)
    bar_last_y = max(bar)
    pe = read_pe(store)
    field_alias = aliases.get('fieldContracts', {})
    fields = []
    for f, ser in prod.field.items():
        fm = prod.field_meta[f]
        k = key(f)
        shp = fshapes.get(k) or fshapes.get(key(field_name(f)))
        l12 = [sum(ser.get(m, [0, 0, 0])[i] for m in n12) / days12 for i in range(3)]
        cum = [sum(v[i] for v in ser.values()) for i in range(3)]
        live_ms = [m for m, v in ser.items() if v[0] + v[1] > 0]
        # Kontrakt: feltomrisset, arkivet, navnet, manuell kobling
        cnr = None
        how = None
        if f in field_alias:
            cnr = re.sub(r'\D', '', str(field_alias[f])) or None
            how = 'manual'
            if cnr not in contracts:
                cand = by_name.get(key(field_alias[f]))
                cnr = cand[0]['nr'] if cand else None
        if not cnr and shp and shp['contract'] in contracts:
            cnr, how = shp['contract'], 'outline'
        if not cnr and arch['contract'].get(f):
            c = arch['contract'][f].most_common(1)[0][0]
            if c in contracts:
                cnr, how = c, 'archive'
        if not cnr:
            cand = by_name.get(k) or by_name.get(key(f.replace('_ECO', '_EXC')))
            if cand:
                cnr, how = cand[0]['nr'], 'name'
        c = contracts.get(cnr) if cnr else None
        anc = bool(re.match(r'(?i)anc_', f))
        if anc:
            holders = [['PPSA (Union)', 100.0, False]]
            op_cur = None
        elif c:
            holders = c['holders']
            if len(c['groups']) > 1:
                # Flere områder i samme kontrakt: gruppen der operatøren er feltets operatør i arkivet
                oh = arch['op'].get(f) or {}
                aop = group_of(oh[max(oh)].most_common(1)[0][0]) if oh else None
                holders = next((g for g in c['groups'] if any(h[2] and group_of(h[0]) == aop for h in g)), holders)
            op_cur = next((h[0] for h in holders if h[2]), c['operator'])
        else:
            holders = []
            op_cur = None
        # Operatør: dagens fra kontrakten; ellers siste i arkivet
        op_hist = arch['op'].get(f) or {}
        last_arch_op = None
        if op_hist:
            y = max(op_hist)
            last_arch_op = op_hist[y].most_common(1)[0][0]
        b = bar.get(bar_last_y, {}).get(k)
        bh = [[y, cn(bar[y][k]['voip'] / 1e6), cn(bar[y][k]['vgip'] / 1e9), cn(bar[y][k]['cumO'] / 1e6), cn(bar[y][k]['cumG'] / 1e9), round(bar[y][k]['rf'], 4)]
              for y in sorted(bar) if k in bar[y]]
        wells_now = len(prod.field_wells[f].get(months[-1], ()))
        api = arch['api'].get(f)
        pe_f = pe.get(k) or pe.get(key(field_name(f)))
        sf = prod.salt
        fields.append({
            'code': f, 'name': field_name(f), 'cx': complex_of(f, known),
            'basin': fm['basin'].most_common(1)[0][0], 'state': fm['state'].most_common(1)[0][0],
            'env': fm['env'].most_common(1)[0][0],
            'first': min(live_ms) if live_ms else None, 'last': max(live_ms) if live_ms else None,
            'l12': [cn(l12[0]), cn(l12[1]), cn(l12[2])],                    # m³/d, 1000 m³/d, m³/d
            'cum': [cn(cum[0] / 1e6), cn(cum[1] / 1e6), cn(cum[2] / 1e6)],  # mill. m³, mrd. m³, mill. m³
            'wells': wells_now,
            'contract': c['name'] if c else None, 'cnr': cnr, 'cHow': how,
            'regime': 'Union (non-contracted area)' if anc else (c['regime'] if c else None),
            'round': c['round'] if c else (shp['round'] if shp else None),
            'holders': [[h[0], h[1], h[2]] for h in holders],
            'op': group_of(op_cur) if op_cur else (group_of(last_arch_op) if last_arch_op else None),
            'opLegal': op_cur or last_arch_op,
            'disc': shp['disc'] if shp else None, 'start': shp['start'] if shp else None,
            'stage': shp['stage'] if shp else None, 'wd': shp['wd'] if shp else None,
            'fluid': shp['fluid'] if shp else None, 'area': shp['area'] if shp else None,
            'loc': centroid(shp['rings']) if shp and shp['rings'] else None,
            'outline': key(shp['name']) if shp else None,
            'api': api,
            'bar': [cn(b['voip'] / 1e6), cn(b['vgip'] / 1e9), cn(b['cumO'] / 1e6), cn(b['cumG'] / 1e9), round(b['rf'], 4), b['mature']] if b else None,
            'barHist': bh if len(bh) > 1 else None,
            'pe': {y: cn(v / 1e6) for y, v in sorted(pe_f.items())} if pe_f else None,
        })
    # AnC-områder: operatøren i hovedfeltet
    by_code = {fo['code']: fo for fo in fields}
    for fo in fields:
        if fo['op'] is None and fo['code'] != fo['cx']:
            main_f = next((x for x in fields if x['code'].upper() == fo['cx']), None)
            if main_f:
                fo['op'], fo['opLegal'] = main_f['op'], main_f['opLegal']
    live = [fo for fo in fields if fo['l12'][0] + fo['l12'][1] > 0]
    if len(live) < 250:
        raise DataError(f'Bare {len(live)} felt med produksjon siste 12 måneder')
    # Pre-salt-andel per felt siste 12 måneder (bare hav)
    # (enkelt anslag fra arkivets siste år + nye brønner, se classify_salt)
    salt_l12 = {}
    for (f, ym), v in prod.field_salt.items():
        if ym in n12:
            t = salt_l12.setdefault(f, [0.0, 0.0])
            t[0] += v[0]
            t[1] += v[1]
    for fo in fields:
        s = salt_l12.get(fo['code'])
        fo['presal'] = round(s[0] / s[1], 3) if s and s[1] > 0 else (0 if fo['env'] == 'on' else None)

    # Kontroll: dekning av andelene
    cov_num = cov_den = 0.0
    for fo in live:
        v = boe_m(fo['l12'])
        cov_den += v
        if fo['holders'] and abs(sum(h[1] for h in fo['holders']) - 100) < 1.5:
            cov_num += v
    coverage = cov_num / cov_den if cov_den else 0
    log(f'  andeler dekker {coverage * 100:.1f} % av produksjonen siste 12 måneder')
    if coverage < 0.9:
        unmatched = sorted((fo for fo in live if not fo['holders']), key=lambda fo: -(fo['l12'][0] + fo['l12'][1]))[:15]
        raise DataError('For lav dekning av eierandeler. Uten kontrakt: ' + ', '.join(fo['code'] for fo in unmatched))

    # Kontroll (myk): akkumulert produksjon mot BAR for store felt
    chk = []
    for fo in fields:
        b = bar.get(bar_last_y, {}).get(key(fo['code']))
        if not b or b['cumO'] < 50e6:
            continue
        ours = sum(prod.field[fo['code']].get(m, [0])[0] for m in months if m <= f'{bar_last_y}12') * BBL_PER_M3
        chk.append(ours / b['cumO'])
    if chk:
        within = sum(1 for r in chk if abs(r - 1) <= 0.05)
        log(f'  akkumulert olje mot BAR {bar_last_y}: {within} av {len(chk)} store felt innenfor 5 % (median {statistics.median(chk):.3f})')

    # Serier per felt: månedlig fra SERIES_MONTHLY_FROM, årlig før
    mfrom = f'{SERIES_MONTHLY_FROM}01'
    prod_fields = {}
    for fo in fields:
        ser = prod.field[fo['code']]
        ms = sorted(m for m, v in ser.items() if v[0] + v[1] + v[2] > 0)
        if not ms:
            continue
        entry = {}
        early = [m for m in ms if m < mfrom]
        if early:
            ys = list(range(int(early[0][:4]), min(int(early[-1][:4]), SERIES_MONTHLY_FROM - 1) + 1))
            a = [[], [], []]
            for y in ys:
                for i in range(3):
                    a[i].append(cn(sum(ser.get(f'{y}{mm:02d}', [0, 0, 0])[i] for mm in range(1, 13)) / 1000))
            entry['a'] = [ys[0]] + a
        late = [m for m in ms if m >= mfrom]
        if late:
            rng = ym_range(late[0], min(late[-1], last))
            s = series_from(ser, rng)
            entry['m'] = [rng[0]] + s
        inj = prod.field_inj.get(fo['code'])
        if inj and any(inj.get(m, 0) for m in n12):
            fo['inj12'] = cn(sum(inj.get(m, 0) for m in n12) / days12)   # 1000 m³/d
        # Produserende brønner per måned (siste 10 år)
        fw = prod.field_wells[fo['code']]
        wr = ym_range(max(ms[0], ym_add(last, -119)), last) if ms[-1] >= ym_add(last, -119) else []
        if wr:
            entry['w'] = [wr[0], [len(fw.get(m, ())) for m in wr]]
        prod_fields[fo['code']] = entry

    # Innretninger
    insts = []
    prod_inst = {}
    for name, ser in prod.inst.items():
        im = prod.inst_meta[name]
        l12 = [sum(ser.get(m, [0, 0, 0])[i] for m in n12) / days12 for i in range(3)]
        ms = sorted(m for m, v in ser.items() if v[0] + v[1] > 0)
        if not ms:
            continue
        tot_f = sum(im['fields'].values()) or 1
        # Posisjon: median av brønnene som produserte til innretningen (brønnregisteret)
        pts = [(wreg[w]['lon'], wreg[w]['lat']) for w in prod.inst_wells.get(name, ()) if w in wreg and wreg[w]['lat']]
        loc = [round(statistics.median(p[0] for p in pts), 3), round(statistics.median(p[1] for p in pts), 3)] if pts else None
        flist = [[f, round(v / tot_f * 100, 1)] for f, v in im['fields'].most_common(12) if v / tot_f >= 0.005]
        if not loc and flist:
            fo = by_code.get(flist[0][0])
            loc = fo['loc'] if fo else None
        itype = arch['inst_type'].get(name)
        insts.append({
            'name': name, 'env': im['env'].most_common(1)[0][0], 'basin': im['basin'].most_common(1)[0][0],
            'state': im['state'].most_common(1)[0][0], 'fields': flist,
            'first': ms[0], 'last': ms[-1], 'l12': [cn(x) for x in l12],
            'cum': [cn(sum(v[0] for v in ser.values()) / 1e6), cn(sum(v[1] for v in ser.values()) / 1e6)],
            'wells': len([w for w in prod.inst_wells.get(name, ()) if any(prod.well_recent[w].get(m, [0, 0])[0] + prod.well_recent[w].get(m, [0, 0])[1] > 0 for m in [last])]),
            'type': itype.most_common(1)[0][0] if itype else None, 'loc': loc,
        })
        rng = ym_range(ms[0], min(ms[-1], last))
        prod_inst[name] = [rng[0]] + series_from(ser, rng)
    insts.sort(key=lambda i: -(i['l12'][0] + i['l12'][1]))

    # Brønner: de mest produktive siste måned, produserende brønner per måned, nye brønner per år
    top_wells = []
    for w, by in prod.well_recent.items():
        v = by.get(last)
        if not v or v[0] + v[1] <= 0:
            continue
        f, inst, env, cls = prod.well_info[w]
        top_wells.append([w, f, inst, env, cls, cn(v[0] / days_in(last)), cn(v[1] / days_in(last))])
    top_wells.sort(key=lambda r: -(r[5] + r[6]))
    live_wells = [[m, len(prod.wells_live[m][0]), len(prod.wells_live[m][1])] for m in months if int(m[:4]) >= 1990]
    new_wells = collections.defaultdict(lambda: [0, 0])
    for w, (ym, env, f) in prod.well_first.items():
        if ym <= last:
            new_wells[ym[:4]][0 if env == 'off' else 1] += 1
    # Første år i filene er ikke «nye» brønner (alt som produserte da)
    nw = [[y, v[0], v[1]] for y, v in sorted(new_wells.items()) if int(y) > int(first[:4])]

    # Selskaper: egenandel med dagens andeler, og opererte volumer
    cfrom = f'{COMPANY_FROM}01'
    cms = ym_range(cfrom, last)
    eq = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0, 0.0]))
    opd = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0, 0.0]))
    field_eq = {}
    eq_fields = collections.defaultdict(list)
    for fo in fields:
        ser = prod.field[fo['code']]
        hs = fo['holders']
        tot = sum(h[1] for h in hs)
        shares = collections.Counter()
        for h in hs:
            shares[group_of(h[0])] += h[1] / tot if tot else 0
        if shares:
            field_eq[fo['code']] = [[g, round(s * 100, 2)] for g, s in shares.most_common()]
        oh = arch['op'].get(fo['code']) or {}
        for m in cms:
            v = ser.get(m)
            if not v:
                continue
            for g, s in shares.items():
                t = eq[g][m]
                t[0] += v[0] * s
                t[1] += v[1] * s
                t[2] += v[2] * s
            y = m[:4]
            if y in oh:
                og = group_of(oh[y].most_common(1)[0][0])
            elif int(y) > int(arch['last'][:4] or 2023) and fo['op']:
                og = fo['op']
            elif oh:
                og = group_of(oh[max(oh)].most_common(1)[0][0])
            else:
                og = fo['op'] or 'Unknown'
            t = opd[og][m]
            t[0] += v[0]
            t[1] += v[1]
            t[2] += v[2]
        l12b = boe_m([fo['l12'][0], fo['l12'][1]])
        for g, s in shares.items():
            if l12b * s >= 1:
                eq_fields[g].append([fo['code'], cn(l12b * s), round(s * 100, 2)])

    def groups_out(src, top_n=14):
        size = {g: sum(boe_m(src[g].get(m, [0, 0])) for m in n12) for g in src}
        keep = [g for g, v in sorted(size.items(), key=lambda kv: -kv[1]) if v > 0][:top_n]
        return {g: series_from(src[g], cms) for g in keep}, size

    eq_out, eq_size = groups_out(eq)
    op_out, op_size = groups_out(opd)
    net12 = sorted(([g, cn(v / days12)] for g, v in eq_size.items() if v / days12 >= 1), key=lambda r: -r[1])
    op12 = sorted(([g, cn(v / days12)] for g, v in op_size.items() if v / days12 >= 1), key=lambda r: -r[1])
    for g in eq_fields:
        eq_fields[g].sort(key=lambda r: -r[1])

    # Reserver og prognose
    log('Reserver og prognose…')
    resv = read_reserve_pdfs(store)
    fc = read_forecast(store)
    bar_tot = {y: [cn(sum(v['voip'] for v in d.values()) / 1e9), cn(sum(v['vgip'] for v in d.values()) / 1e12),
                   cn(sum(v['cumO'] for v in d.values()) / 1e9), cn(sum(v['cumG'] for v in d.values()) / 1e12), len(d)] for y, d in bar.items()}

    # Leting, runder og overdragelser
    log('Leting, runder, overdragelser…')
    expl = read_exploration(store, wreg, bshapes)
    rounds = read_rounds(store)
    blocks = expl['blocks']['list']
    ew = expl['wells']['list']
    wells_by_year = collections.defaultdict(lambda: {'off': 0, 'on': 0, 'disc': 0, 'presal': 0})
    for w in ew:
        if w['spud']:
            r = wells_by_year[w['spud'][:4]]
            r[w['env']] += 1
            r['disc'] += w['disc']
            r['presal'] += w['presal']
    # Utbyggingsbrønner per år fra brønnregisteret (til 2023) og nye produserende brønner
    dev_by_year = collections.defaultdict(lambda: [0, 0])
    for w in wreg.values():
        if w['cat'] == 'development' and w['spud']:
            dev_by_year[w['spud'][:4]][0 if w['env'] == 'off' else 1] += 1

    # Hendelser
    log('Hendelser…')
    inc = read_incidents(store) or []
    inc_year = collections.defaultdict(lambda: {'n': 0, 'acc': 0, 'near': 0, 'inj': 0, 'fat': 0})
    inc_type = collections.Counter()
    inc_inst = collections.Counter()
    inc_group = collections.Counter()
    for r in inc:
        y = r['date'][:4]
        if not ('2000' <= y <= str(THIS_YEAR)):
            continue
        s = inc_year[y]
        s['n'] += 1
        s['acc'] += r['acc'] == 'acc'
        s['near'] += r['acc'] == 'near'
        s['inj'] += r['inj']
        s['fat'] += r['fat']
        if y >= str(THIS_YEAR - 2):
            for t in r['types']:
                inc_type[t] += 1
            inc_group[r['group']] += 1
        inc_inst[key(r['inst'])] += 1
    inst_keys = {key(i['name']): i for i in insts}
    for k2, n in inc_inst.items():
        if k2 in inst_keys:
            inst_keys[k2]['inc'] = n
    inc_recent = sorted((r for r in inc if r['date'] >= f'{THIS_YEAR - 2}-01-01'), key=lambda r: r['date'], reverse=True)

    # Omriss: felt og blokker
    outlines = {'fields': {key(s['name']): s['rings'] for s in fshapes.values() if s['rings']},
                'blocks': {}}
    cur_blocks = {key(b['block']) for b in blocks}
    for k2, rings in bshapes.items():
        if k2 in cur_blocks and rings:
            outlines['blocks'][k2] = rings

    # Brønner til kartet
    map_wells = []
    for w, r in wreg.items():
        if not r['lat'] or not r['lon']:
            continue
        if r['env'] == 'on' and r['cat'] in ('development', 'injection', 'special'):
            continue
        map_wells.append([w, r['lat'], r['lon'], r['cat'], r['env'], (r['spud'] or '')[:4], r['field'] or r['block'], 1 if r['presal'] else 0,
                          r['status'][:40], r['result'][:40] if r['result'] else '', r['wd'], r['op']])

    fetched = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    FIELD_COLS = ['code', 'name', 'cx', 'basin', 'state', 'env', 'first', 'last', 'l12', 'cum', 'wells', 'contract', 'regime', 'round',
                  'holders', 'op', 'opLegal', 'disc', 'start', 'stage', 'wd', 'fluid', 'area', 'loc', 'outline', 'api', 'bar', 'barHist',
                  'presal', 'inj12', 'pe']
    INST_COLS = ['name', 'env', 'basin', 'state', 'fields', 'first', 'last', 'l12', 'cum', 'wells', 'type', 'loc', 'inc']
    tot = lambda d: series_from(d, months)
    payload = {
        'source': 'Agência Nacional do Petróleo, Gás Natural e Biocombustíveis (ANP)',
        'fetched': fetched,
        'months': [first, last],
        'units': {'oil': 'thousand m³ per month (oil + condensate)', 'gas': 'million m³ per month', 'water': 'thousand m³ per month',
                  'boe': f'{BBL_PER_M3} bbl per m³; 1 000 m³ gas = 1 m³ oil equivalent (ANP)',
                  'l12': 'per day: oil m³, gas thousand m³, water m³', 'cum': 'oil million m³, gas billion m³, water million m³',
                  'bar': 'VOIP million bbl, VGIP billion m³, produced oil million bbl, produced gas billion m³, recovery factor, maturity',
                  'pe': 'R$ million per year'},
        'total': tot(prod.total),
        'byEnv': {e: tot(prod.env[e]) for e in ('off', 'on')},
        'bySalt': {s: tot(prod.salt[s]) for s in ('pre', 'post', 'onshore') if s in prod.salt},
        'byBasin': {b: tot(v) for b, v in sorted(prod.basin.items(), key=lambda kv: -sum(boe_m(x) for x in kv[1].values()))},
        'byState': {s: tot(v) for s, v in sorted(prod.state.items(), key=lambda kv: -sum(boe_m(x) for x in kv[1].values()))},
        'gasInjection': [cn(prod.inj.get(m, 0) / 1000) for m in months],
        'liveWells': live_wells,
        'newWells': nw,
        'topWells': top_wells[:60],
        'fieldCols': FIELD_COLS,
        'fields': [[fo.get(c) for c in FIELD_COLS] for fo in sorted(fields, key=lambda fo: -boe_m(fo['l12']))],
        'instCols': INST_COLS,
        'installations': [[i.get(c) for c in INST_COLS] for i in insts],
        'company': {'start': cfrom, 'equity': eq_out, 'operated': op_out, 'net12': net12, 'op12': op12,
                    'fieldEquity': field_eq, 'equityFields': {g: v[:40] for g, v in eq_fields.items()}, 'coverage': round(coverage, 3)},
        'contracts': {'n': len(contracts), 'production': sum(1 for c in contracts.values() if c['phase'].startswith('Produ'))},
        'reserves': {'pdfYear': resv['year'], 'basins': resv['basins'], 'history': resv['history'], 'bar': bar_tot, 'barYear': bar_last_y},
        'forecast': fc,
        'presalArchive': arch['last'],
        'onshoreGaps': gap_out,
        'expl': {'blocksAsOf': expl['blocks']['asOf'], 'wellsAsOf': expl['wells']['asOf'],
                 'nBlocks': len(blocks), 'blockHistory': expl['blockHistory'],
                 'wellsByYear': {y: v for y, v in sorted(wells_by_year.items())},
                 'devByYear': {y: v for y, v in sorted(dev_by_year.items())}},
        'incidents': {'byYear': {y: v for y, v in sorted(inc_year.items())}, 'types': inc_type.most_common(15),
                      'groups': inc_group.most_common(15), 'n': len(inc)},
        'sources': {'fieldOutlines': fshape_name, 'blockOutlines': bshape_name, 'wellRegister': wreg_name},
    }
    prod_payload = {'fetched': fetched, 'fields': prod_fields, 'installations': prod_inst}
    expl_payload = {'fetched': fetched, **{k: v for k, v in expl.items() if k not in ('blockHistory',)}, 'rounds': rounds}
    wells_payload = {'fetched': fetched, 'cols': ['well', 'lat', 'lon', 'cat', 'env', 'spud', 'field', 'presal', 'status', 'result', 'wd', 'op'],
                     'wells': map_wells}
    inc_payload = {'fetched': fetched, 'from': f'{THIS_YEAR - 2}-01-01',
                   'cols': ['id', 'date', 'company', 'group', 'inst', 'lat', 'lon', 'inj', 'fat', 'desc', 'types', 'sev', 'acc', 'cls'],
                   'list': [[r[c] for c in ['id', 'date', 'company', 'group', 'inst', 'lat', 'lon', 'inj', 'fat', 'desc', 'types', 'sev', 'acc', 'cls']] for r in inc_recent]}
    outl_payload = {'fetched': fetched, **outlines}

    for path, obj in ((OUT, payload), (OUT_PROD, prod_payload), (OUT_EXPL, expl_payload), (OUT_WELLS, wells_payload),
                      (OUT_INC, inc_payload), (OUT_OUTL, outl_payload)):
        try:
            old = json.load(open(path, encoding='utf-8'))
            if {**old, 'fetched': None} == {**json.loads(json.dumps(obj)), 'fetched': None}:
                obj['fetched'] = old['fetched']
        except (OSError, ValueError):
            pass
        os.makedirs('data', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(obj, fh, ensure_ascii=False, separators=(',', ':'))
            fh.write('\n')
        log(f'Wrote {path} ({os.path.getsize(path) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
