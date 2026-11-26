"""
Bygger data/angola_fields.json: produksjon per produksjonssenter (FPSO/felt) i
Angola, kvartalsvis fra 2007, fra Equinors regneark «Production per field and
asset» (Equinors investorsider). Regnearket har ett ark per kvartal med Equinors
egenandel per felt; feltets totale produksjon er egenandelen delt på Equinors
eierandel samme kvartal.

Dekker blokkene der Equinor er partner: Blokk 15 (Kizomba A og B, Marimba,
Kizomba-satellittene, Mondo, Saxi-Batuque), Blokk 17 (Girassol, Dália, Rosa,
Pazflor, CLOV) og Blokk 31 (PSVM) — om lag 60 % av Angolas produksjon.

Regnearket lastes ned for hånd fra Equinors investorsider og legges i
data/equinor-raw/production_per_field_and_asset.xlsx (mappen er sperret for
nettstedet); deretter kjøres skriptet. Nyere ark erstatter ikke eldre kvartaler,
så en ny fil hvert kvartal holder.

Kontroll (exit 1): summen av feltene per blokk skal ligge innenfor 8 % av
blokkens produksjon i ANPGs månedsrapporter (data/angola.json) for kvartalene
begge dekker, og ingen kvartaler mellom første og siste skal mangle.

    python build_angola_fields.py [sti til xlsx]
"""

import json
import os
import re
import sys

import openpyxl

SRC = 'data/equinor-raw/production_per_field_and_asset.xlsx'
OUT = 'data/angola_fields.json'
ANGOLA = 'data/angola.json'

# Navn i regnearket → (felt, blokk). Senere år slår Equinor sammen Marimba med
# Kizomba A og satellittene med Kizomba B; begge deler vises som rapportert.
NAMES = [
    (r'^kiz(omba)?\.? ?sat', 'Kizomba Satellites', 'Bloco 15'),
    (r'^kizomba a', 'Kizomba A', 'Bloco 15'),
    (r'^kizomba b', 'Kizomba B', 'Bloco 15'),
    (r'^marimba', 'Marimba', 'Bloco 15'),
    (r'^mondo', 'Mondo', 'Bloco 15'),
    (r'^saxi', 'Saxi-Batuque', 'Bloco 15'),
    (r'^girassol', 'Girassol', 'Bloco 17'),
    (r'^dalia|^dália', 'Dália', 'Bloco 17'),
    (r'^rosa', 'Rosa', 'Bloco 17'),
    (r'^pazflor', 'Pazflor', 'Bloco 17'),
    (r'^clov', 'CLOV', 'Bloco 17'),
    (r'^psvm|^angola block 31', 'PSVM', 'Bloco 31'),
]


def period(title):
    t = title.lower().replace(' ', '')
    m = re.search(r'([1-4])q(\d{2,4})', t) or re.search(r'q([1-4])(\d{4})', t)
    if not m:
        return None
    y = int(m.group(2))
    y = y + 2000 if y < 100 else y
    return f'{y}Q{m.group(1)}'


def field_of(label):
    s = re.sub(r'^\s*(angola\s+)?block\s*1[57]\s+', '', str(label).strip(), flags=re.I).strip()
    for p, name, block in NAMES:
        if re.search(p, s, re.I):
            return name, block
    return None


def read(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        q = period(ws.title)
        if not q or q in out:
            continue
        rows = {}
        for r in ws.iter_rows(values_only=True):
            r = list(r)
            for i, c in enumerate(r[:4]):
                if not isinstance(c, str):
                    continue
                f = field_of(c)
                if not f or re.match(r'^\s*angola block 1[57]', c, re.I):
                    continue
                nums = []
                for x in r[i + 1:i + 8]:
                    if isinstance(x, (int, float)):
                        nums.append(x)
                    elif isinstance(x, str) and re.fullmatch(r'\s*\d+(?:[.,]\d+)?\s*%\s*', x):
                        nums.append(float(x.strip().rstrip('%').replace(',', '.')) / 100)
                if len(nums) < 2 or not (0 < nums[0] < 1):
                    continue
                share = nums[0]
                total = nums[3] if len(nums) >= 4 else nums[1]
                liquid = nums[1]
                if total is None or total < 0:
                    continue
                # 1000 boe/d i alle kvartalsark; enkelte eldre ark i boe/d
                if total > 1000:
                    total, liquid = total / 1000, liquid / 1000
                name, block = f
                prev = rows.get(name)
                rows[name] = [block, share, (prev[2] if prev else 0) + total, (prev[3] if prev else 0) + liquid]
                break
        if rows:
            out[q] = rows
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else SRC
    data = read(path)
    qs = sorted(data)
    if len(qs) < 40:
        print(f'FEIL: bare {len(qs)} kvartaler lest')
        return 1
    y, q = int(qs[0][:4]), int(qs[0][-1])
    allq = []
    while f'{y}Q{q}' <= qs[-1]:
        allq.append(f'{y}Q{q}')
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    missing = [x for x in allq if x not in data]
    if missing:
        print(f'FEIL: mangler kvartaler {missing}')
        return 1
    names = sorted({n for rows in data.values() for n in rows}, key=lambda n: [p[1] for p in NAMES].index(n))
    fields = {}
    for n in names:
        block = next(rows[n][0] for rows in data.values() if n in rows)
        gross = [round(data[x][n][2] / data[x][n][1], 2) if n in data[x] and data[x][n][1] else None for x in allq]
        equity = [round(data[x][n][2], 3) if n in data[x] else None for x in allq]
        share = [round(data[x][n][1], 4) if n in data[x] else None for x in allq]
        fields[n] = {'block': block, 'gross': gross, 'equity': equity, 'share': share}
    # Kontroll mot ANPGs blokktall (månedlig fra september 2024)
    bad = []
    try:
        a = json.load(open(ANGOLA, encoding='utf-8'))
        bm = a['blockMonths']
        for pb in a['prodBlocks']:
            blk = pb['reg'] or pb['name']
            fs = [f for f in fields.values() if f['block'] == blk]
            if not fs:
                continue
            for i, qq in enumerate(allq):
                yy, qn = qq[:4], int(qq[-1])
                ms = [f'{yy}{m:02d}' for m in range(qn * 3 - 2, qn * 3 + 1)]
                if not all(m in bm for m in ms):
                    continue
                anpg = sum(pb['oil'][bm.index(m)] or 0 for m in ms) / 3
                eq = sum(f['gross'][i] or 0 for f in fs) * 1000
                ratio = eq / anpg if anpg else 0
                print(f'  {blk} {qq}: Equinor-felt {eq:,.0f}, ANPG {anpg:,.0f} ({ratio * 100:.1f} %)')
                if abs(ratio - 1) > 0.08:
                    bad.append(f'{blk} {qq}: {ratio * 100:.1f} %')
    except (OSError, KeyError, ValueError) as e:
        print(f'  ! ingen kontroll mot ANPG: {e}')
    if bad:
        print('FEIL: feltene avviker fra ANPGs blokktall: ' + '; '.join(bad))
        return 1
    payload = {'source': "Equinor, «Production per field and asset» (investor relations), equity production per field; gross = equity / Equinor's working interest",
               'units': 'thousand boe/day (gross: whole field; equity: Equinor share)', 'quarters': allq, 'fields': fields,
               'file': os.path.basename(path)}
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(',', ':'))
        fh.write('\n')
    print(f'Wrote {OUT}: {len(names)} felt, {allq[0]}–{allq[-1]} ({os.path.getsize(OUT) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
