"""Reservetall per felt før 2004, fra Sodirs (Oljedirektoratets) ressursregnskap.

FactMaps-laget field_reserves (7113) starter med årgang 2004. Ressursregnskapene
for 1980–2003 ligger som Excel-filer i data/reserves-raw/ (lastet ned for hånd
fra sodir.no, som avviser automatiske nedlastinger). Dette skriptet leser
opprinnelig utvinnbart (salgbart) volum per felt fra dem og skriver
data/reserves_field_hist.json. Engangsskript: kjøres på nytt bare hvis filene
eller feltkoblingen endres. Krever xlrd (pip install xlrd).

Avgrensning og kontroll:
- 1975 er utelatt med vilje: grensedragningen mot britisk sokkel for Frigg og
  Statfjord var foreløpig og ble senere endret, og olje i tonn og gass i Nm3
  må regnes om med antatte faktorer. 1996 har bare summer per
  ressursklasse. Ellers mangler 1981–84 og 1986–89.
- Gjenværende reserver leses ikke: de finnes ikke for alle år, og appen regner
  dem ut som opprinnelig utvinnbart minus kumulativ produksjon.
- 2004-filen leses bare for kontroll mot APIet og skrives ikke ut. Alle felt
  skal stemme innenfor 1 %; skriptet stopper ellers.
- OE regnes som olje + gass + 1,9 × NGL (tonn) + kondensat, Sodirs faktor.
  I 1980 er NGL regnet inn i oljen.
- Felt som senere er slått inn i andre, legges sammen med dagens felt (ALIAS).
"""
import json
import re
import sys
from pathlib import Path

import xlrd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'data' / 'reserves-raw'
OUT = ROOT / 'data' / 'reserves_field_hist.json'
NGL = 1.9   # Sm3 o.e. per tonn NGL

# år -> [(ark, kategorifilter)]. Kategorifilteret er en regex mot
# kategoriteksten til venstre for feltnavnet (1997–1999 blander reserver og
# ressurser i samme ark), eller 'STOP' for RK0-arket, der felt med avsluttet
# produksjon står først og felt i produksjon følger etter summeringsraden.
CFG = {
    1980: [('Tabell 17', None)],
    1985: [('Tabell 2.4.2', None)],
    1990: [('Tabell 3.2', None)],
    1991: [('Tabell 3.1.a', None), ('Tabell 3.1.b', None)],
    1992: [('Tabell 2.10.1.a', None), ('Tabell 2.10.1.b', None)],
    1993: [('Tabell 2.11.1.a', None), ('Tabell2.11.1.b', None), ('Tabell 2.11.1.c', None)],
    1994: [('Tabell 2.12.1.b', None), ('Tabell 2.12.1.c', None), ('Tabell 2.12.1.d', None)],
    1995: [('Tabell 2.1.c', None), ('Tabell 2.1.d', None), ('Tabell 2.1.e', None)],
    1997: [('Resources in Fields', r'reserves')],
    1998: [('Resources in fields', r'reserves')],
    1999: [('Resources in fields', r'reserves')],
    2000: [('Felt', None)],
    2001: [('RK1,2,3', None), ('RK0-solgt og levert', 'STOP')],
    2002: [('RK1,2,3', None), ('RK0-solgt og levert', 'STOP')],
    2003: [('RK1,2', None), ('RK0-solgt og levert', 'STOP')],
    2004: [('RK1,2,3-felt', None), ('RK0-solgt og levert', 'STOP')],   # bare kontroll
}
CHECK_YEAR = 2004

# Navn i regnskapene -> dagens felt (normalisert). Tom streng: utelates.
ALIAS = {
    # Forekomster som senere er slått inn i et annet felt.
    'gyda sør': 'gyda', '2/1 9 gyda sør': 'gyda',
    '30/6 gamma nord': 'oseberg', 'oseberg vest': 'oseberg',
    'gullfaks vest': 'gullfaks', 'gullveig': 'gullfaks sør', 'rimfaks': 'gullfaks sør',
    'loke': 'sleipner øst', 'borg': 'tordis', 'tordis øst': 'tordis',
    'ringhorne': 'balder', 'byggve': 'skirne',
    'troll i': 'troll', 'troll ii': 'troll', 'troll øst': 'troll', 'troll vest olje': 'troll',
    'troll vest (fase 2) olje': 'troll', 'troll vest olje(fase||': 'troll',
    'troll øst (fase l': 'troll', 'troll øst (fase |': 'troll',
    # Brønnbetegnelse foran navnet, stavevarianter og skrivefeil.
    '34/10 48 s gimle': 'gimle', '7121/4 1 snøhvit': 'snøhvit',
    'tommeliten': 'tommeliten gamma', 'v. ekofisk': 'vest ekofisk', 'valhall a': 'valhall',
    'n. ø. frigg': 'nordøst frigg', 'nord øst frigg': 'nordøst frigg', 'nordøstfrigg': 'nordøst frigg',
    'edd': 'edda', 'gugne': 'gungne', 'murchinson': 'murchison', 'gullfaksb),': 'gullfaks',
    # Sleipner Vest og Øst står som ett tall i 2002 og kan ikke deles.
    'sleipner vest og øst': '',
}

low = lambda v: str(v).strip().lower()
norm = lambda s: re.sub(r'\s+', ' ', s.replace('-', ' ').replace('.', '. ').casefold()).strip()
TEXT_NUMBERS = []


def num(v):
    """Tall; tekstceller med tall (f.eks. «28,.6» for Vest Ekofisk i 1992) tolkes."""
    if isinstance(v, float):
        return v
    t = str(v).strip().replace(',.', '.').replace(',', '.')
    if re.fullmatch(r'-?\d+(\.\d+)?', t):
        TEXT_NUMBERS.append(str(v))
        return float(t)
    return 0.0


def columns(sh):
    """Overskriftsraden og kolonnene for opprinnelig utvinnbart volum."""
    for h in range(min(20, sh.nrows)):
        row = [low(v) for v in sh.row_values(h)]
        oils = [i for i, v in enumerate(row) if re.match(r'^(olje|oil)(\b|\d)', v)]
        if not oils:
            continue
        start = oils[0]
        # Blokkoverskriften (opprinnelig utvinnbart / tilstedeværende /
        # gjenværende) står på en av radene over.
        for b in range(max(0, h - 3), h):
            for i, v in enumerate(low(x) for x in sh.row_values(b)):
                if re.search(r'salgbar|utvinnbar|recoverable', v) and not re.search(r'tilstede|in.?place', v):
                    start = min(oils, key=lambda o: (abs(o - i), o))
                    break
        end = next((o for o in oils if o > start), len(row))
        pick = lambda pat: next((i for i in range(start, end) if re.match(pat, row[i])), None)
        return h, {'oil': start, 'gas': pick(r'^(gass|gas)'), 'ngl': pick(r'^ngl'),
                   'cond': pick(r'^(kondensat|condensate|kond\.)')}
    raise SystemExit(f'Fant ingen overskriftsrad i arket {sh.name}')


def clean(raw):
    """Feltnavn uten fotnotemerker: «Albuskjell 1)», «GRANEb», «Oseberg *****»."""
    s = re.sub(r'[\s\d\)\*]+$', '', raw.strip())
    m = re.match(r'^([A-ZÆØÅ][A-ZÆØÅ \-\.]+?)([a-z]{1,2})$', s)
    if m:
        s = m.group(1)
    return re.sub(r'\s*\d\)?\s*$', '', s).strip()


def parse_year(year):
    wb = xlrd.open_workbook(RAW / f'ressursregnskap-for-{year}.xls')
    rows = []
    for name, catre in CFG[year]:
        sh = wb.sheet_by_name(name)
        h, c = columns(sh)
        cat = ''
        for r in range(h + 1, sh.nrows):
            row = sh.row_values(r)
            texts = [(i, str(v).strip()) for i, v in enumerate(row[:c['oil']])
                     if isinstance(v, str) and str(v).strip()]
            if not texts:
                continue
            if len(texts) > 1:
                cat = ' '.join(t for _, t in texts[:-1])
            fname = texts[-1][1]
            vals = {k: num(row[i]) if i is not None else 0.0 for k, i in c.items()}
            if not any(vals.values()):
                if len(texts) == 1 and texts[0][0] < 2:
                    cat = texts[0][1]
                continue
            if catre == 'STOP' and re.match(r'^(produksjon fra|sum nedstengte)', fname, re.I):
                break
            if re.match(r'^(sum|totalt?|total)\b', fname, re.I) or fname.endswith(':'):
                continue
            if re.search(r'planning|developed|development|reserves and resources|^resources|ressurser', fname, re.I):
                continue
            if catre and catre != 'STOP' and not re.search(catre, cat, re.I):
                continue
            rows.append((fname, vals))
    return rows


def main():
    fields = json.loads((ROOT / 'data' / 'fields.json').read_text())['rows']
    byname = {norm(n): i for i, n in fields}
    alias = {norm(k): v for k, v in ALIAS.items()}
    out, unmatched = {}, {}
    for year in CFG:
        for raw, v in parse_year(year):
            k0, fid = norm(clean(raw)), None
            # «Baldera», «Gullfaks Sørc»: fotnotebokstav rett på navnet.
            for k in (k0, k0[:-1], k0[:-2]):
                if k in alias:
                    if alias[k] == '':
                        fid = 'skip'
                        break
                    k = norm(alias[k])
                if k in byname:
                    fid = byname[k]
                    break
            if fid == 'skip':
                continue
            if fid is None:
                unmatched.setdefault(raw, []).append(year)
                continue
            e = out.setdefault(fid, {}).setdefault(year, {'oil': 0, 'gas': 0, 'ngl': 0, 'cond': 0, 'parts': []})
            for q in ('oil', 'gas', 'ngl', 'cond'):
                e[q] += v[q]
            e['parts'].append(raw)
    if unmatched:
        raise SystemExit(f'Feltnavn uten treff: {unmatched}')

    # Kontroll mot APIet for overlappsåret.
    api = json.loads((ROOT / 'data' / 'reserves_field.json').read_text())
    iO = api['columns'].index('fldRecoverableOE')
    bad, n = [], 0
    for fid, ys in out.items():
        e = ys.get(CHECK_YEAR)
        a = next((r[iO] for r in api['fields'].get(str(fid), []) if r[0] == CHECK_YEAR), None)
        if e is None or a is None:
            continue
        n += 1
        oe = e['oil'] + e['gas'] + NGL * e['ngl'] + e['cond']
        if abs(oe - a) > max(0.5, 0.01 * a):
            bad.append((fid, round(oe, 2), a))
    if bad or not n:
        raise SystemExit(f'{CHECK_YEAR}: avvik mot APIet: {bad}')
    print(f'{CHECK_YEAR}: {n} felt stemmer med APIet innenfor 1 %')

    cols = ['year', 'fldRecoverableOil', 'fldRecoverableGas', 'fldRecoverableNGL',
            'fldRecoverableCondensate', 'fldRecoverableOE']
    res = {}
    for fid, ys in sorted(out.items()):
        rows = []
        for y in sorted(ys):
            if y >= CHECK_YEAR:
                continue
            e = ys[y]
            oe = e['oil'] + e['gas'] + NGL * e['ngl'] + e['cond']
            rows.append([y] + [round(e[q], 3) for q in ('oil', 'gas', 'ngl', 'cond')] + [round(oe, 3)])
        if rows:
            res[str(fid)] = rows
    OUT.write_text(json.dumps({
        'source': "Sodir (Oljedirektoratet), ressursregnskap 1980–2003, data/reserves-raw/",
        'note': 'Opprinnelig utvinnbart (salgbart) volum per felt per 31.12. Gass i mrd Sm3, '
                'NGL i mill tonn, øvrige i mill Sm3; OE = olje + gass + 1,9 × NGL + kondensat. '
                'I 1980 er NGL regnet inn i oljen. Ingen tall for 1981–84, 1986–89 og 1996.',
        'columns': cols, 'fields': res,
    }, ensure_ascii=False, separators=(',', ':')) + '\n')
    years = sorted({r[0] for rows in res.values() for r in rows})
    print(f'Skrev {OUT.relative_to(ROOT)}: {len(res)} felt, årgangene {years}')
    if TEXT_NUMBERS:
        print('Tall lagret som tekst:', TEXT_NUMBERS)


if __name__ == '__main__':
    sys.exit(main())
