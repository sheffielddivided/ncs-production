"""
Importerer normpris-data fra Petroleumsprisrådet (xlsx-filer fra regjeringen.no)
og lagrer dem som kompakte JSON-filer under data/.

Rådata ligger i data/normpris-raw/. regjeringen.no ligger bak en Cloudflare-
utfordring og kan ikke hentes automatisk, så nye kvartalsfiler lastes ned
manuelt og legges i den mappa. Deretter kjører man:

    pip install openpyxl
    python fetch_normpris.py

Kjøres også automatisk av .github/workflows/update-normpris.yml når filer i
data/normpris-raw/ endres.

Hver fil har to ark vi bruker:
  'Differensialer'                  felt x måned, $/fat (D/F/H = måned 1/2/3)
  'Daglige normpriser (Dt. Brent)'  daglig Datert Brent, $/fat, tre
                                    dato/pris-par per rad (B/C, E/F, H/I)
Daglige normpriser per felt (NOK og $) og valutakurser leses ikke.

Noen kvartaler har i tillegg tilleggsfiler som retter eller fyller ut enkelte
felt/måneder ('...-troll-februar', '...-gudrun-juni' osv). Verdier fra en
tilleggsfil overstyrer hovedfilen, så en verdi som er rettet i ettertid vinner.
Manglende celle = ingen verdi (trolig ikke solgt olje den måneden); slike
måneder utelates, de lagres ikke som 0 eller null.

Filene varierer en del (2011-2026), så parseren er tolerant der det er trygt og
feiler høyt ellers:
  - Månedene leses fra overskriftene i rad 4 (kolonne C-I), ikke fra posisjon:
    i tilleggsfiler står én enkelt måned i første kolonne uansett hvilken måned
    det er, og eldre filer bruker andre kolonner.
  - Feltnavn normaliseres (store bokstaver, fotnotestjerner fjernes: 'TROLL*').
    Rader som er fotnoter ('* ...', '(*) ...') hoppes over.
  - 'N/A' er en manglende verdi.
  - Er en celle merket '*' eller '**', eller har et felt flere rader i samme
    måned ('GUDRUN (*)' / 'GUDRUN (**)' i 2Q 2018), hadde feltet flere
    differensialer i måneden (delperioder). Det finnes da ingen månedsverdi;
    slike måneder lagres under "split" og ikke som tall. Delperiodene i
    sideblokken (kolonne > I) importeres ikke.
  - Alt annet i en verdicelle (ukjent tekst) stopper importen.

Rekkefølge: filene med flest verdier leses først, og mindre filer (rettelser og
utfyllinger av enkeltfelt) overstyrer. Det er uavhengig av filnavn.

Utdata er deterministisk (sorterte rader og nøkler), slik at filene bare
endrer seg når dataene har endret seg.

  normpris_brent.json  {"columns":["date","usd"], "rows":[["2021-01-01",54.3],...]}
  normpris_diff.json   {"months":["2021-01",...], "fields":{"TROLL":{"2021-01":2.2,...}},
                        "split":{"ALVHEIM":["2020-04"]}}
"""

import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime

import openpyxl

RAW_DIR = os.path.join('data', 'normpris-raw')
OUT_DIR = 'data'

DIFF_SHEET  = 'Differensialer'
BRENT_SHEET = 'Daglige normpriser (Dt. Brent)'

MONTHS = {
    'januar': 1, 'februar': 2, 'mars': 3, 'april': 4, 'mai': 5, 'juni': 6,
    'juli': 7, 'august': 8, 'september': 9, 'oktober': 10, 'november': 11,
    'desember': 12,
}

# Månedsoverskriftene i Differensialer står i kolonne C-I (3-9). Kolonner til
# høyre for det er en sideblokk med delperioder, som ikke importeres.
DIFF_HEADER_COLS = range(3, 10)
# Brent-arket: (dato-kolonne, pris-kolonne) per måned.
BRENT_COLS = ((2, 3), (5, 6), (8, 9))

TITLE_RE = re.compile(r'(\d)Q\s*(\d{4})')
MARKER_RE = re.compile(r'\*+')
NA_RE = re.compile(r'(n/?a|-+)', re.I)
FOOTNOTE_MARK_RE = re.compile(r'\s*\(?\*+\)?\s*$')


class ParseError(Exception):
    pass


def _quarter(ws, path):
    title = str(ws['B2'].value or ws['B1'].value or '')
    m = TITLE_RE.search(title)
    if not m:
        raise ParseError(f"{path}: fant ikke kvartal/år i tittel {title!r}")
    return int(m.group(1)), int(m.group(2))


def _num(value, where):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseError(f"{where}: forventet tall, fant {value!r}")
    return float(value)


def parse_diffs(wb, path):
    """Returnerer (verdier, delte) for én fil.

    verdier: liste av (felt, 'YYYY-MM', verdi); delte: liste av (felt, 'YYYY-MM')
    for celler merket '*' (flere differensialer i måneden).
    """
    ws = wb[DIFF_SHEET]
    quarter, year = _quarter(ws, path)

    month_of = {}
    for col in DIFF_HEADER_COLS:
        label = ws.cell(4, col).value
        if label is None:
            continue
        num = MONTHS.get(str(label).strip().lower())
        if num is None:
            raise ParseError(f"{path} {ws.cell(4, col).coordinate}: ukjent måned {label!r}")
        # Måneden må høre til kvartalet i tittelen, ellers er filen noe annet enn forventet.
        if (num - 1) // 3 + 1 != quarter:
            raise ParseError(
                f"{path} {ws.cell(4, col).coordinate}: {label!r} hører ikke til {quarter}Q {year}")
        month_of[col] = f'{year}-{num:02d}'
    if not month_of:
        raise ParseError(f"{path}: fant ingen månedsoverskrifter i rad 4")

    # Samle verdier per (felt, måned). Flere rader for samme felt og måned
    # (f.eks. 'GUDRUN (*)' og 'GUDRUN (**)') betyr delperioder.
    cells = defaultdict(list)    # (felt, ym) -> verdier, eller 'split'
    for row in range(5, ws.max_row + 1):
        raw = ws.cell(row, 2).value
        if raw is None or not str(raw).strip():
            continue
        raw = str(raw).strip()
        if raw.startswith(('*', '(')):
            continue                      # fotnoterad
        field = FOOTNOTE_MARK_RE.sub('', raw).strip().upper()
        for col, ym in month_of.items():
            cell = ws.cell(row, col)
            v = cell.value
            if isinstance(v, str):
                t = v.strip()
                if MARKER_RE.fullmatch(t):
                    cells[(field, ym)].append('split')
                    continue
                if t == '' or NA_RE.fullmatch(t):
                    continue
            value = _num(v, f'{path} {cell.coordinate}')
            if value is not None:
                cells[(field, ym)].append(value)

    values, split = [], []
    for (field, ym), vs in cells.items():
        if len(vs) == 1 and vs[0] != 'split':
            values.append((field, ym, vs[0]))
        else:
            split.append((field, ym))
    return values, split


def parse_brent(wb, path):
    """Returnerer liste av ('YYYY-MM-DD', pris) for én fil."""
    ws = wb[BRENT_SHEET]
    _quarter(ws, path)  # validerer at arket ser riktig ut
    out = []
    for row in range(5, ws.max_row + 1):
        for dcol, pcol in BRENT_COLS:
            d = ws.cell(row, dcol).value
            if d is None or (isinstance(d, str) and not d.strip()):
                continue
            if not isinstance(d, (datetime, date)):
                raise ParseError(f"{path} {ws.cell(row, dcol).coordinate}: forventet dato, fant {d!r}")
            price = _num(ws.cell(row, pcol).value, f'{path} {ws.cell(row, pcol).coordinate}')
            if price is not None:
                out.append((d.strftime('%Y-%m-%d'), price))
    return out


def load_all(raw_dir=RAW_DIR):
    """Leser alle xlsx-filer. Største filer først, mindre filer (rettelser) overstyrer."""
    paths = sorted(glob.glob(os.path.join(raw_dir, '*.xlsx')))
    if not paths:
        raise ParseError(f"ingen xlsx-filer i {raw_dir}")

    parsed = []
    for path in paths:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
        try:
            values, marked = parse_diffs(wb, path)
            days = parse_brent(wb, path)
        finally:
            wb.close()
        parsed.append((path, values, marked, days))
    parsed.sort(key=lambda p: (-(len(p[1]) + len(p[2])), os.path.basename(p[0])))

    diffs = {}   # (felt, måned) -> (verdi, fil)
    split = {}   # (felt, måned) -> fil, for måneder med delperioder
    brent = {}   # dato -> (pris, fil)
    conflicts = []
    for path, values, marked, days in parsed:
        for field, ym, value in values:
            prev = diffs.get((field, ym))
            if prev and prev[0] != value:
                conflicts.append(('diff', f'{field} {ym}', prev, (value, path)))
            diffs[(field, ym)] = (value, path)
        for field, ym in marked:
            split[(field, ym)] = path
        for d, price in days:
            prev = brent.get(d)
            if prev and prev[0] != price:
                conflicts.append(('brent', d, prev, (price, path)))
            brent[d] = (price, path)

    # En verdi (f.eks. fra en tilleggsfil) går foran en '*' fra hovedfilen.
    split = {k: v for k, v in split.items() if k not in diffs}
    return paths, diffs, brent, split, conflicts


def build_payloads(diffs, brent, split):
    months = sorted({ym for _, ym in diffs} | {ym for _, ym in split})
    # Fullstendig månedsliste fra første til siste, så grafer kan vise hull.
    y, m = map(int, months[0].split('-'))
    ye, me = map(int, months[-1].split('-'))
    full = []
    while (y, m) <= (ye, me):
        full.append(f'{y}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    fields = defaultdict(dict)
    for (field, ym), (value, _) in diffs.items():
        fields[field][ym] = value
    split_by_field = defaultdict(list)
    for field, ym in split:
        split_by_field[field].append(ym)

    return {
        'normpris_brent.json': {
            'columns': ['date', 'usd'],
            'rows': [[d, brent[d][0]] for d in sorted(brent)],
        },
        'normpris_diff.json': {
            'months': full,
            'fields': {f: dict(sorted(v.items())) for f, v in sorted(fields.items())},
            'split': {f: sorted(v) for f, v in sorted(split_by_field.items())},
        },
    }


def write_if_changed(name, payload):
    path = os.path.join(OUT_DIR, name)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            if fh.read() == text:
                print(f"  = {name} uendret")
                return False
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f"  + {name} skrevet ({len(text.encode('utf-8')) / 1024:.0f} KB)")
    return True


def main():
    try:
        paths, diffs, brent, split, conflicts = load_all()
    except ParseError as exc:
        # Avbryt uten å skrive noe, så forrige gyldige datasett består.
        print(f"FEIL: {exc}", file=sys.stderr)
        return 1

    print(f"Leste {len(paths)} filer: {len(diffs)} differensialer, {len(brent)} Brent-dager, "
          f"{len(split)} felt-måneder med delperioder (ikke importert)")
    for kind, key, old, new in conflicts:
        print(f"  ! {kind} {key}: {old[0]} ({os.path.basename(old[1])}) "
              f"overstyres av {new[0]} ({os.path.basename(new[1])})")

    payloads = build_payloads(diffs, brent, split)
    for name, payload in payloads.items():
        write_if_changed(name, payload)
    return 0


if __name__ == '__main__':
    sys.exit(main())
