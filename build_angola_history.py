"""
Bygger data/angola_history.json: oljeproduksjon per blokk i Angola før ANPGs
månedsrapporter fikk blokktabeller (september 2024). Tabellene leses fra
årsrapportene (PDF, pdftotext -layout):

  - Sonangol, Relatório de Gestão e Contas 2016, 2017, 2018: produksjon per
    blokk og kvartal (Sonangol var konsesjonær til 2019).
  - ANPG, Relatório de Gestão 2019 og 2020: per blokk og kvartal.
  - EITI/ITIE Angola 2021 og 2022: per blokk og år (kilde: ANPG).

For 2023 og januar–august 2024 er det ikke funnet tabeller per blokk som tekst
(ANPGs rapport for 2023 har tabellen som bilde).

Rapportene endres ikke, så skriptet kjøres for hånd; PDF-ene mellomlagres i
--cache. Kontroller (exit 1): kvartalene skal summere til årstallet per blokk,
blokkene til TOTAL-raden, og landets totaler skal ligge innenfor 2 % av ANPGs
årstall (data/angola.json).

    python build_angola_history.py --cache DIR
"""

import argparse
import json
import os
import re
import subprocess
import sys

import requests

OUT = 'data/angola_history.json'
UA = 'Mozilla/5.0 (compatible; ncs-insight data fetch)'

# (kilde, url, år, kolonner): kolonnene er tallene i raden etter navnet, i
# rekkefølge; 'Q1'–'Q4' og 'T' er kvartaler og årstall for året, et tall er
# årstallet for det året.
DOCS = [
    ('Sonangol RC 2016', 'https://www.sonangol.co.ao/wp-content/uploads/2022/07/RC2016.pdf', 2016, ['Q1', 'Q2', 'Q3', 'Q4', 'T']),
    ('Sonangol RC 2017', 'https://www.sonangol.co.ao/wp-content/uploads/2022/07/RC2017.pdf', 2017, ['Q1', 'Q2', 'Q3', 'Q4', 'T']),
    ('Sonangol RC 2018', 'https://www.sonangol.co.ao/wp-content/uploads/2022/07/RC2018.pdf', 2018, [2017, 'Q1', 'Q2', 'Q3', 'Q4', 'T']),
    ('ANPG Relatório de Gestão 2019', 'http://anpg.co.ao/wp-content/uploads/2020/10/Relatorio_de_Gestao_2019_vf.pdf', 2019, ['Q1', 'Q2', 'Q3', 'Q4', 'T']),
    ('ANPG Relatório de Gestão 2020', 'https://anpg.co.ao/wp-content/uploads/2022/07/ANPG_Relatorio_de_Gestao_2020.pdf', 2020, ['Q1', 'Q2', 'Q3', 'Q4', 'T']),
    ('EITI Angola 2021', 'https://eiti.org/sites/default/files/2024-05/1%C2%BA%20Relato%CC%81rio%20ITIE%20Angola_31.12.2021%20(EN).pdf', 2021, [2021, 2020]),
    ('ITIE Angola 2022', 'https://c2a.portais.gov.ao/uploads/53_2024_itie_relatorio_2022_compressed_1879799133671255dca6212_d20695f00b.pdf', 2022, [2022]),
]
NAME = re.compile(r'^\s*(Bloco \d+(?:/\d+)?(?:\s?A|[Kk])?|Block \d+(?:/\d+)?(?:\s?A|K)?|[ÁA]rea [AB]|Associa[çc][ãa]o FST?|FST|FS|Cabinda (?:Sul|South)|Offshore|OFFSHORE|Onshore|ONSHORE|TOTAL|Total)\s{2,}(.*)$')


AGG = {'OFFSHORE', 'ONSHORE', 'TOTAL', 'AREAA', 'AREAB'}


def key(name):
    k = re.sub(r'^(Bloco|Block)\s*', '', name.strip(), flags=re.I)
    k = re.sub(r'^Associa[çc][ãa]o\s*', '', k, flags=re.I)
    k = k.replace(' ', '').upper()
    return {'CABINDASOUTH': 'CABINDASUL', 'ÁREAA': 'AREAA', 'ÁREAB': 'AREAB'}.get(k, k)


def nums(rest):
    out = []
    for t in re.split(r'\s{2,}', rest.strip()):
        t = t.strip()
        if '%' in t:
            continue
        if t in ('-', '‑', '–'):
            out.append(0)
            continue
        if re.fullmatch(r'\d{1,3}(?:[ .,]\d{3})+|\d+', t):
            out.append(int(re.sub(r'[ .,]', '', t)))
            continue
        break
    return out


def table(text, cols):
    """Finn tabellen med produksjon per blokk: raden «Bloco 17» med nok tall, og
    les radene rundt den."""
    lines = text.splitlines()
    need = len(cols)
    anchor = None
    for i, ln in enumerate(lines):
        m = NAME.match(ln)
        if m and key(m.group(1)) == '17':
            v = nums(m.group(2))
            if len(v) >= need and max(v[:need]) > 5_000_000:
                anchor = i
                break
    if anchor is None:
        return None
    rows, dist = {}, {}
    for i in range(max(0, anchor - 30), min(len(lines), anchor + 30)):
        m = NAME.match(lines[i])
        if not m:
            continue
        v = nums(m.group(2))
        if len(v) < need:
            continue
        k = key(m.group(1))
        d = abs(i - anchor) + (0 if i >= anchor or k not in AGG else 100)    # samme tabell: nærmest ankeret
        if k not in rows or d < dist[k]:
            rows[k], dist[k] = v[:need], d
    return rows


def pdf(url, cache):
    name = re.sub(r'[^A-Za-z0-9._-]', '_', url.rsplit('/', 1)[-1])[-120:]
    p = os.path.join(cache, name)
    if not os.path.exists(p) or os.path.getsize(p) < 10000:
        r = requests.get(url, headers={'User-Agent': UA}, timeout=600)
        r.raise_for_status()
        open(p, 'wb').write(r.content)
    return subprocess.run(['pdftotext', '-layout', p, '-'], capture_output=True, text=True, timeout=300).stdout


def days(y, q=None):
    import calendar
    if q is None:
        return 366 if calendar.isleap(y) else 365
    return sum(calendar.monthrange(y, m)[1] for m in range(q * 3 - 2, q * 3 + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default='.angola-cache')
    args = ap.parse_args()
    os.makedirs(args.cache, exist_ok=True)
    quarters, annual, bad, sources = {}, {}, [], []
    for src, url, year, cols in DOCS:
        rows = table(pdf(url, args.cache), cols)
        if not rows:
            print(f'FEIL: fant ikke blokktabellen i {src}')
            return 1
        sources.append({'name': src, 'url': url})
        total_key = 'TOTAL'
        for k, v in rows.items():
            rec = dict(zip(cols, v))
            if 'Q1' in rec:
                qs = [rec[f'Q{q}'] for q in range(1, 5)]
                if rec.get('T') and abs(sum(qs) - rec['T']) > max(5, rec['T'] * (0.01 if k in AGG else 0.002)):
                    bad.append(f'{src} {k}: kvartaler {sum(qs)} ≠ år {rec["T"]}')
                for q in range(1, 5):
                    quarters.setdefault(f'{year}Q{q}', {})[k] = qs[q - 1]
                annual.setdefault(year, {})[k] = rec.get('T', sum(qs))
            for c, x in rec.items():
                if isinstance(c, int):
                    annual.setdefault(c, {}).setdefault(k, x)
        # Blokkene skal summere til TOTAL
        for c in [c for c in cols if isinstance(c, int)] + (['T'] if 'T' in cols else []):
            i = cols.index(c)
            blocks = sum(v[i] for k, v in rows.items() if k not in AGG)
            tot = rows.get(total_key, [None] * len(cols))[i]
            if tot and abs(blocks / tot - 1) > 0.005:
                bad.append(f'{src} {c}: blokker {blocks} ≠ total {tot}')
        print(f'  {src}: {len(rows)} rader')
    # Mot ANPGs årstall
    try:
        hist = dict((int(y), v) for y, v in json.load(open('data/angola.json', encoding='utf-8'))['annual'])
        for y, d in sorted(annual.items()):
            if 'TOTAL' in d and y in hist:
                bpd = d['TOTAL'] / days(y)
                r = bpd / hist[y]
                print(f'  {y}: {bpd:,.0f} bbl/d mot ANPG {hist[y]:,.0f} ({r * 100:.1f} %)')
                if abs(r - 1) > 0.02:
                    bad.append(f'{y}: {r * 100:.1f} % av ANPGs årstall')
    except (OSError, KeyError, ValueError) as e:
        print(f'  ! ingen kontroll mot ANPGs årstall: {e}')
    if bad:
        print('FEIL: ' + '; '.join(bad))
        return 1
    qs = sorted(quarters)
    ys = sorted(annual)
    keys = sorted({k for d in list(quarters.values()) + list(annual.values()) for k in d} - {'AREAA', 'AREAB'})
    out = {'source': 'Sonangol (Relatório de Gestão e Contas 2016–2018), ANPG (Relatório de Gestão 2019–2020), EITI/ITIE Angola (2021–2022)',
           'units': 'bbl/day (average)', 'sources': sources, 'quarters': qs, 'years': ys,
           'blocks': {k: {'q': [round(quarters[q][k] / days(int(q[:4]), int(q[-1]))) if k in quarters[q] else None for q in qs],
                          'a': [round(annual[y][k] / days(y)) if k in annual[y] else None for y in ys]} for k in keys},
           'block0Areas': {a: {'q': [round(quarters[q][a] / days(int(q[:4]), int(q[-1]))) if a in quarters[q] else None for q in qs]} for a in ('AREAA', 'AREAB')}}
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(',', ':'))
        fh.write('\n')
    print(f'Wrote {OUT}: {len(keys)} blokker, kvartaler {qs[0]}–{qs[-1]}, år {ys[0]}–{ys[-1]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
