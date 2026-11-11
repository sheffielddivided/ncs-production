"""
Leser riggselskapenes «fleet status reports» (PDF) og fletter kontraktene inn
i data/rig_contracts.json, som riggtidslinjen og riggprofilen i Drilling viser.

    pip install pdfplumber
    python import_fleet_report.py <rapport.pdf> [--dry-run]

Rapportene kommer hvert kvartal eller oftere, i hvert selskaps eget format,
så hvert selskap har sin egen leser (i dag: Noble). Kjøres for hånd når en ny
rapport er lastet ned; den daglige jobben rører ikke fila.

Bare rigger Sodir kjenner (data/rig_facts.json) tas med, men da med alle
kontraktene deres, også utenfor norsk sokkel, så det synes hvor riggen er.

Historikk: rapportene viser bare pågående og kommende kontrakter. Det som er
avsluttet når en ny rapport kommer, blir liggende. En kommende kontrakt som
er borte fra den nye rapporten, er endret eller kansellert og fjernes. En
eldre rapport kan leses inn i etterkant; den fyller bare på med kontrakter
som var avsluttet før den nyeste rapporten fra samme selskap.

Fila kan også rettes og fylles på for hånd (kontrakter fra pressemeldinger,
selskaper uten rapport). Rader med en kilde som begynner med "manual" (f.eks.
"manual-saipem", beskrevet i sources) røres aldri av skriptet.
"""

import argparse
import calendar
import json
import os
import re
import sys
from datetime import date, timedelta

PATH = os.path.join('data', 'rig_contracts.json')
FACTS = os.path.join('data', 'rig_facts.json')
MATCH_DAYS = 150    # samme rigg, kunde og type, og start innenfor så mange dager: samme kontrakt

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}

NOTE = ('Kontrakter per rigg fra riggselskapenes fleet status reports (import_fleet_report.py) '
        'og manuelle rader (source "manual…", røres ikke av skriptet). start/end er ISO-datoer '
        'utledet av rapportens tekst (startText/endText) og precision (month, quarter, half, year, chart). '
        'kind: firm eller option. dayrate i USD per dag, null når den ikke er oppgitt (se dayrateText). '
        'rigs: riggdata fra siste rapport (design, byggeår, vanndybde).')


# ── Datoer ───────────────────────────────────────────────────────────────────
def _yr(s):
    y = int(s)
    return y + 2000 if y < 100 else y


def parse_when(text, is_end):
    """Rapportens datoformat til (iso, presisjon). Start gir første dag i
    perioden, slutt siste dag. «Jul-24», «Q4-29», «Q4 2027», «Q3/Q4-27»,
    «Mid 2027», «2030»."""
    t = ' '.join(text.split()).strip()
    if not t:
        return None, None

    def first_last(y, m0, m1):
        if is_end:
            return date(y, m1, calendar.monthrange(y, m1)[1]).isoformat()
        return date(y, m0, 1).isoformat()

    m = re.fullmatch(r'([A-Za-z]{3})[- ](\d{2,4})', t)
    if m and m.group(1).lower() in MONTHS:
        mo = MONTHS[m.group(1).lower()]
        return first_last(_yr(m.group(2)), mo, mo), 'month'
    m = re.fullmatch(r'Q([1-4])(?:/Q([1-4]))?[- ](\d{2,4})', t)
    if m:
        q0 = int(m.group(1))
        q1 = int(m.group(2) or q0)
        y = _yr(m.group(3))
        return first_last(y, (q0 - 1) * 3 + 1, q1 * 3), 'quarter'
    m = re.fullmatch(r'(Early|Mid|Late)[- ](\d{4})', t, re.I)
    if m:
        y, w = _yr(m.group(2)), m.group(1).lower()
        span = {'early': (1, 4), 'mid': (5, 8), 'late': (9, 12)}[w]
        return first_last(y, *span), 'half'
    m = re.fullmatch(r'(\d{4})', t)
    if m:
        return first_last(int(t), 1, 12), 'year'
    return None, None


def parse_dayrate(text):
    t = ' '.join(text.split())
    m = re.search(r'\$\s?([\d,]+(?:\.\d+)?)\s*(k|K)?', t)
    if not m:
        return None
    v = float(m.group(1).replace(',', ''))
    return int(v * 1000) if m.group(2) else int(v)


# ── Noble ────────────────────────────────────────────────────────────────────
def _lines(words, tol=2.5):
    """Ord gruppert i linjer på y-posisjon."""
    out = []
    for w in sorted(words, key=lambda w: (round(w['top']), w['x0'])):
        if out and abs(out[-1]['top'] - w['top']) <= tol:
            out[-1]['words'].append(w)
        else:
            out.append({'top': w['top'], 'words': [w]})
    for ln in out:
        ln['words'].sort(key=lambda w: w['x0'])
    return out


def _text(words):
    return ' '.join(w['text'] for w in sorted(words, key=lambda w: (round(w['top']), w['x0'])))


def noble_tables(pdf):
    """Kontraktsrader fra tabellsidene («Fleet Contract Status»)."""
    rows, rigs = [], {}
    for page in pdf.pages:
        text = page.extract_text() or ''
        if 'Contract Status' not in text:
            continue
        words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
        # Hver tabell på siden har sin egen overskriftslinje: Rig Name … Comments.
        heads = [w for w in words if w['text'] == 'Rig']
        heads = [h for h in heads if any(w['text'] == 'Name' and abs(w['top'] - h['top']) < 2 for w in words)]
        heads.sort(key=lambda h: h['top'])
        for hi, h in enumerate(heads):
            y0 = h['top'] - 12
            y1 = heads[hi + 1]['top'] - 30 if hi + 1 < len(heads) else page.height - 40
            hdr = {w['text']: w for w in words if abs(w['top'] - h['top']) < 2}
            # Kolonnegrenser fra overskriften. «Start Date»/«End Date» har to ord.
            starts = [w for w in words if w['text'] == 'Start' and abs(w['top'] - h['top']) < 2]
            ends = [w for w in words if w['text'] == 'End' and abs(w['top'] - h['top']) < 2]
            if not (starts and ends and 'Dayrate' in hdr and 'Operator' in hdr and 'Location' in hdr):
                continue
            col = {
                'name': (0, hdr['Design']['x0'] - 2),
                'design': (hdr['Design']['x0'] - 2, hdr['Built']['x0'] - 2),
                'built': (hdr['Built']['x0'] - 2, hdr['Built']['x1'] + 10),
                'depth': (hdr['Built']['x1'] + 10, hdr['Location']['x0'] - 2),
                'location': (hdr['Location']['x0'] - 2, hdr['Operator']['x0'] - 2),
                'operator': (hdr['Operator']['x0'] - 2, starts[0]['x0'] - 4),
                'start': (starts[0]['x0'] - 4, ends[0]['x0'] - 4),
                'end': (ends[0]['x0'] - 4, hdr['Dayrate']['x0'] - 4),
                'dayrate': (hdr['Dayrate']['x0'] - 4, hdr['Comments']['x0'] - 4),
                'comments': (hdr['Comments']['x0'] - 4, page.width),
            }
            body = [w for w in words if h['top'] + 14 < w['top'] < y1 and w['top'] > y0
                    and not w['text'].startswith('Key') and 'Depth' not in w['text']]
            # Linjer under overskriften (overskriften har en ekstra linje «Depth (ft)»).
            body = [w for w in body if not (abs(w['top'] - h['top']) < 16 and w['x0'] >= col['depth'][0] and w['x1'] <= col['depth'][1] + 30)]
            incol = lambda w, c: col[c][0] <= (w['x0'] + w['x1']) / 2 < col[c][1]
            cw = {c: [w for w in body if incol(w, c)] for c in col}

            # Riggnavn: blokker av linjer i navnekolonnen som henger sammen.
            name_lines = _lines(cw['name'])
            blocks = []
            for ln in name_lines:
                if blocks and ln['top'] - blocks[-1]['bottom'] < 13:
                    blocks[-1]['words'] += ln['words']
                    blocks[-1]['bottom'] = ln['top']
                else:
                    blocks.append({'top': ln['top'], 'bottom': ln['top'], 'words': list(ln['words'])})
            for b in blocks:
                b['name'] = _text(b['words'])
                b['mid'] = (b['top'] + b['bottom']) / 2
            if not blocks:
                continue

            # En kontraktsrad har en dato i startkolonnen (eller «Available» som operatør).
            start_lines = _lines(cw['start'])
            row_y = [ln['top'] for ln in start_lines if parse_when(_text(ln['words']), False)[0]]
            for ln in _lines(cw['operator']):
                if _text(ln['words']) == 'Available':
                    row_y.append(ln['top'])
            row_y.sort()

            def nearest_block(y):
                return min(blocks, key=lambda b: abs(b['mid'] - y))

            # Riggens grenser: halvveis mellom nabonavnene.
            mids = sorted(b['mid'] for b in blocks)

            def rig_span(b):
                i = mids.index(b['mid'])
                lo = (mids[i - 1] + mids[i]) / 2 if i else y0
                hi = (mids[i] + mids[i + 1]) / 2 if i + 1 < len(mids) else y1
                return lo, hi

            # Kommentarene er avsnitt sentrert på sin rad; hvert avsnitt går til raden nærmest midten.
            paras = []
            for ln in _lines(cw['comments']):
                if paras and ln['top'] - paras[-1]['bottom'] < 12:
                    paras[-1]['words'] += ln['words']
                    paras[-1]['bottom'] = ln['top']
                else:
                    paras.append({'top': ln['top'], 'bottom': ln['top'], 'words': list(ln['words'])})
            note_at = {}
            for pa in paras:
                if row_y:
                    y = min(row_y, key=lambda y: abs(y - (pa['top'] + pa['bottom']) / 2))
                    note_at[y] = (note_at.get(y, '') + ' ' + _text(pa['words'])).strip()

            per_rig = {}
            for y in row_y:
                per_rig.setdefault(id(nearest_block(y)), (nearest_block(y), []))[1].append(y)
            for b, ys in per_rig.values():
                lo, hi = rig_span(b)
                def field(c, y, i, inherit=True):
                    on_line = [w for w in cw[c] if abs(w['top'] - y) < 3]
                    if on_line:
                        return _text(on_line)
                    if not inherit:
                        return ''
                    # Sentrert over flere rader: nærmeste tekst innenfor riggen.
                    near = [w for w in cw[c] if lo <= w['top'] < hi]
                    if not near:
                        return ''
                    ln = min(_lines(near), key=lambda ln: abs(ln['top'] - y))
                    return _text(ln['words'])

                meta = {
                    'design': _text([w for w in cw['design'] if lo <= w['top'] < hi]),
                    'built': _text([w for w in cw['built'] if lo <= w['top'] < hi]),
                    'waterDepthFt': _text([w for w in cw['depth'] if lo <= w['top'] < hi]).replace(',', ''),
                }
                rigs[b['name']] = meta
                for i, y in enumerate(ys):
                    op = field('operator', y, i)
                    if op == 'Available':
                        continue
                    st = field('start', y, i, inherit=False)
                    en = field('end', y, i)
                    rows.append({
                        'rig': b['name'],
                        'location': field('location', y, i),
                        'client': op,
                        'startText': st,
                        'endText': en,
                        'dayrateText': field('dayrate', y, i),
                        'note': note_at.get(y, ''),
                    })
    return rows, rigs


def noble_chart(pdf):
    """Stolpene på tidslinjesidene: lyseblå er opsjoner, mørkeblå fast periode."""
    out = []
    for page in pdf.pages:
        text = page.extract_text() or ''
        if 'Contract Timeline' not in text:
            continue
        words = page.extract_words()
        years = sorted((w for w in words if re.fullmatch(r'20\d\d', w['text']) and w['top'] < 130), key=lambda w: w['x0'])
        if not years:
            continue
        cells = sorted({(round(r['x0'], 1), round(r['x1'], 1)) for r in page.rects
                        if 8 < r['height'] < 10 and r['top'] < 130 and r['width'] < 30})
        if len(cells) < 12:
            continue
        y0 = int(years[0]['text'])
        months = [(c, date(y0 + i // 12, i % 12 + 1, 1)) for i, c in enumerate(cells)]
        right = months[-1][0][1]

        def x2date(x):
            for (a, b), d in months:
                if a - 0.5 <= x <= b + 0.5:
                    n = calendar.monthrange(d.year, d.month)[1]
                    return d + timedelta(days=round((x - a) / (b - a) * (n - 1)))
            return months[-1][1] if x > right else months[0][1]

        names = {}
        for ln in _lines([w for w in words if w['x0'] < cells[0][0] and w['top'] > 130]):
            t = _text(ln['words'])
            if t.startswith('Noble'):
                names[ln['top']] = t
        bars = [r for r in page.rects if r['height'] < 7 and r['top'] > 130
                and isinstance(r.get('non_stroking_color'), (list, tuple)) and len(r['non_stroking_color']) == 3]
        for r in bars:
            if r['x0'] < 60:
                continue    # forklaringen nederst
            top = min(names, key=lambda y: abs(y - r['top'] + 1), default=None)
            if top is None or abs(top - r['top']) > 6:
                continue
            out.append({'rig': names[top], 'start': x2date(r['x0']).isoformat(),
                        'end': x2date(r['x1']).isoformat(), 'clipped': r['x1'] >= right - 0.5,
                        'before': r['x0'] <= cells[0][0] + 0.5,
                        'kind': 'option' if r['non_stroking_color'][0] > 0.5 else 'firm'})
    return out


def read_noble(pdf, text):
    m = re.search(r'Fleet Status Report\s+([A-Z][a-z]+ \d{1,2}, \d{4})', text)
    from datetime import datetime
    as_of = datetime.strptime(m.group(1), '%B %d, %Y').date().isoformat()
    rows, rigs = noble_tables(pdf)
    out = []
    for r in rows:
        s, sp = parse_when(r['startText'], False)
        e, ep = parse_when(r['endText'], True)
        if not s:
            print(f'  ! kunne ikke tolke start «{r["startText"]}» for {r["rig"]} / {r["client"]}', file=sys.stderr)
            continue
        if not e:
            e, ep = s, sp
        out.append({**r, 'kind': 'firm', 'start': s, 'end': e,
                    'precision': sp if sp == ep else f'{sp}/{ep}',
                    'dayrate': parse_dayrate(r['dayrateText'])})
    # Opsjoner knyttes til kontrakten som slutter rett før (eller der opsjonen starter).
    chart = noble_chart(pdf)
    # Faste perioder i grafen som ingen tabellrad dekker, er som regel avsluttede
    # kontrakter rapporten ikke lister lenger. De tas med som historikk.
    for o in [c for c in chart if c['kind'] == 'firm']:
        a, b = date.fromisoformat(o['start']), date.fromisoformat(o['end'])
        cover = sum(max(0, (min(b, date.fromisoformat(r['end'])) - max(a, date.fromisoformat(r['start']))).days)
                    for r in out if r['rig'] == o['rig'])
        if cover < 0.2 * max(1, (b - a).days) and (b - a).days >= 20:
            out.append({**({'startOpen': True} if o['before'] else {}), 'rig': o['rig'], 'location': '', 'client': 'Undisclosed', 'kind': 'firm',
                        'start': o['start'], 'end': o['end'], 'startText': '', 'endText': '',
                        'precision': 'chart', 'dayrate': None, 'dayrateText': '',
                        'note': 'Firm period read from the contract timeline chart; the report does not list this contract.'
                                + (' It began before the chart does, so the start is unknown.' if o['before'] else '')})
    for o in [c for c in chart if c['kind'] == 'option']:
        before = [r for r in out if r['rig'] == o['rig'] and r['end'] <= (date.fromisoformat(o['start']) + timedelta(days=45)).isoformat()]
        base = max(before, key=lambda r: r['end'], default=None)
        out.append({
            'rig': o['rig'], 'location': base['location'] if base else '', 'client': base['client'] if base else '',
            'kind': 'option', 'start': o['start'], 'end': o['end'], 'startText': '', 'endText': '',
            'precision': 'chart', 'dayrate': None, 'dayrateText': '',
            'note': 'Option period read from the contract timeline chart; client is the contract it follows.'
                    + (' The bar runs past the end of the chart.' if o['clipped'] else ''),
        })
    return 'Noble', as_of, out, rigs


# ── Transocean ───────────────────────────────────────────────────────────────
TRANSOCEAN_STATUS = {'Firm': 'firm', 'Priced Option': 'option', 'Priced Options': 'option'}


def read_transocean(pdf, text):
    """Én tabell over hele flåten: én linje per kontrakt eller opsjon, med
    status (Firm, Priced Options, Out of Service, Stacked), datoer og dagrate.
    Riggnavnet står bare på første linje for hver rigg."""
    from datetime import datetime
    m = re.search(r'—\s*([A-Z][a-z]+ \d{1,2}, \d{4})\s*—', text) or re.search(r'As of ([A-Z][a-z]+ \d{1,2}, \d{4})', text)
    as_of = datetime.strptime(m.group(1), '%B %d, %Y').date().isoformat()
    out, rigs = [], {}
    for page in pdf.pages:
        words = page.extract_words()
        hdr = {w['text']: w for w in words if w['text'] in ('Location', 'Customer', 'Status', 'Comments')}
        if len(hdr) < 4:
            continue
        top = hdr['Customer']['top']
        start = next(w for w in words if w['text'] == 'Start' and abs(w['top'] - top) < 15)
        end = next(w for w in words if w['text'] == 'End' and abs(w['top'] - top) < 15)
        dollars = next(w for w in words if w['text'] == '(Dollars)')
        foot = next((w for w in words if w['text'] == 'Footnote'), None)
        entered = next((w for w in words if w['text'] == 'Service' and abs(w['top'] - top) < 3), None)
        ctr = lambda w: (w['x0'] + w['x1']) / 2
        cols = [('location', ctr(hdr['Location'])), ('client', ctr(hdr['Customer'])), ('status', ctr(hdr['Status'])),
                ('start', ctr(start)), ('end', ctr(end)), ('dayrate', ctr(dollars))]
        bounds = {}
        for i, (k, c) in enumerate(cols):
            lo = (cols[i - 1][1] + c) / 2 if i else c - 45
            hi = (c + cols[i + 1][1]) / 2 if i + 1 < len(cols) else hdr['Comments']['x0'] - 25
            bounds[k] = (lo, hi)
        name_hi = foot['x0'] - 4 if foot else 240
        body = [w for w in words if w['top'] > top + 8]
        cur = None
        for ln in _lines(body):
            ws = ln['words']
            get = lambda k: _text([w for w in ws if bounds[k][0] <= ctr(w) < bounds[k][1]])
            name = _text([w for w in ws if w['x1'] < name_hi])
            st, en = get('start'), get('end')
            if re.fullmatch(r'[A-Z][a-z]{2}-\d\d', st or '') is None:
                if name and not re.search(r'\(\d+\)$', name) and get('status') in ('Stacked', 'Idle'):
                    cur = name    # rigg uten kontrakt
                continue
            if name:
                cur = name
                yr = _text([w for w in ws if entered and abs(ctr(w) - ctr(entered)) < 25])
                rigs[cur] = {'built': yr} if re.fullmatch(r'\d{4}', yr or '') else {}
            status = get('status')
            kind = TRANSOCEAN_STATUS.get(status)
            if not kind or not cur:
                continue    # Out of Service: verft, mobilisering
            s, sp = parse_when(st, False)
            e, ep = parse_when(en, True)
            client = get('client')
            rate = get('dayrate')
            out.append({
                'rig': cur, 'location': get('location'), 'client': 'Undisclosed' if client == 'Not Disclosed' else client,
                'kind': kind, 'start': s, 'end': e or s, 'startText': st, 'endText': en, 'precision': 'month',
                'dayrate': int(rate.replace(',', '')) if re.fullmatch(r'[\d,]+', rate) else None,
                'dayrateText': rate if not re.fullmatch(r'[\d,]+', rate) else '',
                'note': _text([w for w in ws if ctr(w) >= hdr['Comments']['x0'] - 25]),
            })
    return 'Transocean', as_of, out, rigs


READERS = [('Noble Corporation', read_noble), ('Transocean', read_transocean)]


# ── Nettsider ────────────────────────────────────────────────────────────────
# Odfjell og COSL har ingen tabell, bare en grafisk tidslinje på nettsiden.
# Begge leses rett fra HTML-en; datoene er det grafen viser, så de er omtrentlige.
WEB_ALIAS = {'DEEPSEA MIRA': 'WEST MIRA', 'HERCULES': 'WEST HERCULES'}   # nettsidens navn -> Sodirs
WEB_LOC = {'UK': 'United Kingdom', 'Norwegian Continental Shelf': 'Norway'}
UA = {'User-Agent': 'Mozilla/5.0 (compatible; ncs-production rig contracts)'}


def _get(url):
    import requests
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return r.text


def _frac_date(y0, years):
    """Dato for et punkt «years» år etter 1. januar y0."""
    whole = int(years // 1)
    y = y0 + whole
    d = date(y, 1, 1) + timedelta(days=round((years - whole) * (366 if calendar.isleap(y) else 365)))
    return d.isoformat()


def _frac_end(y0, years):
    """Sluttdato: dagen før punktet, så slutten av et år blir 31. desember."""
    return (date.fromisoformat(_frac_date(y0, years)) - timedelta(days=1)).isoformat()


def _web_row(rig, client, location, kind, s, e, note, open_start=False):
    return {**({'startOpen': True} if open_start else {}), 'rig': rig, 'client': client or 'Undisclosed', 'location': location, 'kind': kind, 'start': s, 'end': e,
            'startText': '', 'endText': '', 'precision': 'chart', 'dayrate': None, 'dayrateText': '', 'note': note}


def read_odfjell():
    """odfjelldrilling.com/our-fleet: per rigg en stolpe av perioder
    (contract, option, available, empty) med bredde i prosent, og årsmerker
    under. Merkene står med lik avstand, og det siste har bredde 0, så stolpen
    går fra 1. januar første år til 1. januar siste år. Breddene er avstanden
    mellom påfølgende sluttdatoer; en negativ bredde er en periode som sluttet
    før stolpen starter (Deepsea Stavanger)."""
    import html as H
    url = 'https://www.odfjelldrilling.com/our-fleet/'
    page = _get(url)
    out, rigs = [], {}
    for a in re.findall(r'<a class="fleet-rig"[\s\S]*?</a>', page):
        name = H.unescape(re.search(r'fleet-rig__title">([^<]*)', a).group(1)).strip()
        spec = {k.strip(' :'): H.unescape(v).strip() for k, v in
                re.findall(r'spec-title">([^<]*)</div>\s*<div class="fleet-rig__spec-value">([^<]*)', a)}
        per = [(k, float(v)) for k, v in re.findall(r'availability__period (\w+)" style="width: ([-\d.]+)%', a)]
        years = [int(y) for y in re.findall(r'tick-year">\s*(\d{4})', a)]
        if not per or len(years) < 2:
            continue
        y0, span = years[0], years[-1] - years[0]
        rig = WEB_ALIAS.get(name.upper(), name)
        rigs[rig] = {'built': spec.get('Year delivered', '')}
        loc = WEB_LOC.get(spec.get('Location', ''), spec.get('Location', ''))
        client = spec.get('Current client', '')
        today = date.today().isoformat()
        pos, mine = 0.0, []
        for kind, w in per:
            a0, pos = pos, pos + w / 100 * span
            if w <= 0 or kind == 'empty':
                continue
            s, e = _frac_date(y0, max(a0, 0)), _frac_end(y0, pos)
            note = 'Read from the timeline on odfjelldrilling.com.'
            if kind == 'contract':
                mine.append(_web_row(rig, '', loc, 'firm', s, e, note
                                     + (' Began before the timeline does, so the start is unknown.' if a0 <= 0 else ''), a0 <= 0))
            else:
                mine.append(_web_row(rig, '', loc, 'option', s, e, note
                                     + (' Continued optionality under a frame agreement.' if kind == 'available' else '')))
        # Nettsiden oppgir bare «Current client»: den gjelder kontrakten som løper
        # i dag, eller den første kommende. Opsjoner får kunden til kontrakten de følger.
        firm = [r for r in mine if r['kind'] == 'firm']
        cur = next((r for r in firm if r['start'] <= today <= r['end']), None) or next((r for r in firm if r['start'] > today), None)
        for r in mine:
            if r['kind'] == 'firm':
                r['client'] = client if r is cur and client else 'Undisclosed'
                if r['client'] == 'Undisclosed':
                    r['note'] += ' The website names only the current client.'
            else:
                prev = [f for f in firm if f['end'] <= r['start']]
                r['client'] = prev[-1]['client'] if prev else 'Undisclosed'
        out += mine
    return 'Odfjell', date.today().isoformat(), out, rigs, url


def read_cosl():
    """cosl.no: tidslinjen tegnes av et skript med én status per år:
    booked (hele året fast), options, partial-booked (bookedPercentage % fast
    fra venstre, resten opsjon) og alternate-booked-right (alternateBookedPercentage
    % ledig fra høyre, resten opsjon). Alle år er skravert (opsjon) som
    utgangspunkt. Klient og sted står i teksten over hver tidslinje."""
    import html as H
    url = 'https://www.cosl.no/'
    page = _get(url)
    heads = [(m.start(), m.group(1), re.sub(r'<[^>]+>', ' ', m.group(2)))
             for m in re.finditer(r'<h2[^>]*><a href="/cosl\w+">(\w+)</a></h2>([\s\S]{0,800}?)</div>', page)]
    script = next(m.group(1) for m in re.finditer(r'<script[^>]*>([\s\S]*?)</script>', page) if 'createTimeline' in m.group(1))
    data = {}
    for m in re.finditer(r'const (timelineData\d+) = \[([\s\S]*?)\];', script):
        data[m.group(1)] = [dict(year=int(y), status=s, pct=float(p) if p else None) for y, s, p in re.findall(
            r"year:\s*(\d{4}),\s*status:\s*'([\w-]+)'(?:,\s*\w+Percentage:\s*([\d.]+))?", m.group(2))]
    calls = re.findall(r"createTimeline\('(timeline\d+)',\s*(timelineData\d+)\)", script)
    tl_pos = {t: page.find(f'id="{t}"') for t, _ in calls}
    out, rigs = [], {}
    for tl, var in calls:
        # Riggen er overskriften rett før tidslinjen på siden.
        head = max((h for h in heads if h[0] < tl_pos[tl]), key=lambda h: h[0], default=None)
        if not head or var not in data:
            continue
        rig = head[1]
        txt = ' '.join(H.unescape(head[2]).split())
        client = (re.search(r'Client:\s*(.*?)\s*(?:Location:|$)', txt) or [None, ''])[1]
        loc = WEB_LOC.get((re.search(r'Location:\s*(.*)$', txt) or [None, ''])[1].strip(), '')
        # Hvert år blir to delperioder: [fast | opsjon | ledig] i brøkdeler av året.
        segs = []
        for d in data[var]:
            st, p = d['status'], (d['pct'] or 0) / 100
            parts = {'booked': [('firm', 1)], 'options': [('option', 1)],
                     'partial-booked': [('firm', p), ('option', 1 - p)],
                     'alternate-booked-right': [('option', 1 - p), ('free', p)],
                     'alternate-booked': [('free', p), ('option', 1 - p)]}.get(st, [('option', 1)])
            x = d['year']
            for k, f in parts:
                if f > 0:
                    segs.append([k, x, x + f])
                x += f
        merged = []
        for k, a, b in segs:
            if merged and merged[-1][0] == k and abs(merged[-1][2] - a) < 1e-6:
                merged[-1][2] = b
            else:
                merged.append([k, a, b])
        y0 = data[var][0]['year']
        for k, a, b in merged:
            if k == 'free':
                continue
            s, e = _frac_date(y0, a - y0), _frac_end(y0, b - y0)
            out.append(_web_row(rig, client, loc, k, s, e,
                                'Read from the timeline on cosl.no.' + (' Began before the timeline does, so the start is unknown.'
                                                                        if k == 'firm' and a == y0 else '')
                                + (' Client assumed to be the current client.' if client else ''), a == y0))
        rigs[rig] = {}
    return 'COSL', date.today().isoformat(), out, rigs, url


WEB = {'odfjell': read_odfjell, 'cosl': read_cosl}


# ── Fletting ─────────────────────────────────────────────────────────────────
def load():
    if not os.path.exists(PATH):
        return {'note': NOTE, 'sources': {}, 'rigs': {}, 'contracts': []}
    with open(PATH, encoding='utf-8') as fh:
        return json.load(fh)


def sodir_rigs():
    with open(FACTS, encoding='utf-8') as fh:
        f = json.load(fh)
    i = f['columns'].index('name')
    return {r[i].upper(): r[i] for r in f['rows']}


def same(a, b):
    if not (a['rig'] == b['rig'] and a['kind'] == b['kind'] and a['client'].lower() == b['client'].lower()):
        return False
    if a.get('startOpen') or b.get('startOpen'):
        # Starten er bare starten på grafen; samme kontrakt hvis periodene overlapper.
        return a['start'] <= b['end'] and b['start'] <= a['end']
    return abs((date.fromisoformat(a['start']) - date.fromisoformat(b['start'])).days) <= MATCH_DAYS


CLIENT = [(r'^A/S Norske Shell$', 'Shell'), (r'\s+(ASA|AS|S\.A\.|Limited|Ltd\.?|plc)$', ''), (r'^Bp$', 'bp'),
          (r'^Not Disclosed$', 'Undisclosed')]


def clean_client(c):
    c = ' '.join((c or '').split())
    for pat, rep in CLIENT:
        c = re.sub(pat, rep, c)
    return c or 'Undisclosed'


def merge(db, contractor, as_of, rows, rigs, src_id, title):
    known = sodir_rigs()
    keep_rows = []
    for r in rows:
        name = known.get(r['rig'].upper())
        if not name:
            continue
        keep_rows.append({**r, 'rig': name, 'client': clean_client(r['client']), 'contractor': contractor, 'source': src_id})
    old = db['contracts']
    mine = [c for c in old if c.get('contractor') == contractor and not str(c.get('source', '')).startswith('manual')]
    others = [c for c in old if c not in mine]
    newest = max((db['sources'][c['source']]['date'] for c in mine if c['source'] in db['sources']), default='')
    log = []
    result = []
    used = set()
    for c in mine:
        c_date = db['sources'].get(c['source'], {}).get('date', '')
        hit = next((i for i, n in enumerate(keep_rows) if i not in used and same(c, n)), None)
        if hit is not None:
            used.add(hit)
            if as_of >= c_date:
                n = keep_rows[hit]
                if n.get('startOpen') and c['start'] < n['start']:
                    n['start'] = c['start']    # tidligere kjent start; grafen har flyttet seg
                if any(n.get(k) != c.get(k) for k in ('start', 'end', 'dayrate', 'location', 'note')):
                    log.append(f'  ~ {n["rig"]}: {n["client"]} {c["start"]}–{c["end"]} → {n["start"]}–{n["end"]}')
                result.append(n)
            else:
                result.append(c)
        elif as_of >= c_date and c['end'] >= as_of:
            log.append(f'  - {c["rig"]}: {c["client"]} {c["start"]}–{c["end"]} ({c["kind"]}) er borte fra rapporten, fjernet')
        else:
            result.append(c)    # avsluttet før rapporten: historikk
    for i, n in enumerate(keep_rows):
        if i in used:
            continue
        if newest and as_of < newest and n['end'] >= newest:
            continue    # eldre rapport: bare det som var avsluttet før den nyeste
        log.append(f'  + {n["rig"]}: {n["client"]} {n["start"]}–{n["end"]} ({n["kind"]}{", $" + format(n["dayrate"], ",") if n["dayrate"] else ""})')
        result.append(n)
    db['contracts'] = sorted(others + result, key=lambda c: (c['rig'], c['start'], c['kind']))
    db['sources'][src_id] = {'contractor': contractor, 'title': title, 'date': as_of}
    if not newest or as_of >= newest:
        for name, meta in rigs.items():
            s = known.get(name.upper())
            if s:
                db['rigs'][s] = {'contractor': contractor, **{k: v for k, v in meta.items() if v}, 'source': src_id}
    db['note'] = NOTE
    return log


# ── Kobling til Sodirs selskaper ─────────────────────────────────────────────
# Rapportene bruker kortnavn («Aker BP», «Equinor»). Hver kontrakt får feltet
# companies med Sodirs selskapsnavn (data/companies.json), så selskapsfilteret i
# Drilling treffer også rigger som bare har en kommende kontrakt for selskapet.
# Kjente kunder står her; andre kobles til det aktive selskapet hvis navn begynner
# med kundenavnet (operatør foran rettighetshaver). Delte kunder («Harbour
# Energy / OMV») kobles til hver av dem.
CLIENT_COMPANY = {
    'aker bp': 'Aker BP ASA',
    'conocophillips': 'ConocoPhillips Skandinavia AS',
    'dno': 'DNO Norge AS',
    'equinor': 'Equinor Energy AS',
    'harbour energy': 'Harbour Energy Norge AS',
    'omv': 'OMV Norge AS',
    'orlen': 'ORLEN Upstream Norway AS',
    'shell': 'A/S Norske Shell',
    'vår energi': 'Vår Energi ASA',
}
NO_COMPANY = {'undisclosed', 'various', 'bp'}   # bp er ikke aktiv på norsk sokkel


def link_companies(db):
    with open(os.path.join('data', 'companies.json'), encoding='utf-8') as fh:
        cb = json.load(fh)
    rows = [dict(zip(cb['columns'], r)) for r in cb['rows']]
    names = {r['name'] for r in rows}
    active = sorted((r for r in rows if r.get('activeNcs')),
                    key=lambda r: (-int(r.get('licenceOper') or 0), -int(r.get('licensee') or 0), r['name']))

    def one(client):
        k = client.strip().lower()
        if not k or k in NO_COMPANY:
            return None
        if k in CLIENT_COMPANY and CLIENT_COMPANY[k] in names:
            return CLIENT_COMPANY[k]
        hit = next((r['name'] for r in active if r['name'].lower() == k or r['name'].lower().startswith(k + ' ')), None)
        return hit

    changed, missing = False, set()
    for c in db['contracts']:
        found = []
        for part in re.split(r'\s*/\s*', c['client']):
            n = one(part)
            if n:
                found.append(n)
            elif part.strip().lower() not in NO_COMPANY:
                missing.add(part)
        if c.get('companies') != found:
            c['companies'] = found
            changed = True
    if missing:
        print('  uten selskapskobling: ' + ', '.join(sorted(missing)))
    return changed


def prune_sources(db):
    used = {c.get('source') for c in db['contracts']} | {r.get('source') for r in db['rigs'].values()}
    db['sources'] = {k: v for k, v in db['sources'].items() if k in used}


def run(db, contractor, as_of, rows, rigs, title, url=None):
    src_id = f'{contractor.lower()}-{as_of}'
    log = merge(db, contractor, as_of, rows, rigs, src_id, title)
    if url:
        db['sources'][src_id]['url'] = url
    print(f'{contractor} {as_of}: {len(rows)} rader, '
          f'{sum(1 for c in db["contracts"] if c.get("source") == src_id)} på rigger Sodir kjenner')
    print('\n'.join(log) or '  ingen endringer')
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf', nargs='*', help='fleet status report(er) som PDF')
    ap.add_argument('--web', nargs='*', choices=sorted(WEB), help='les nettsidene (uten navn: alle)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    db = load()
    changed = False
    for path in a.pdf:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            text = '\n'.join((p.extract_text() or '') for p in pdf.pages[:3])
            reader = next((fn for key, fn in READERS if key in text), None)
            if not reader:
                sys.exit('Kjenner ikke igjen rapporten. Lesere finnes for: ' + ', '.join(k for k, _ in READERS))
            contractor, as_of, rows, rigs = reader(pdf, text)
        changed |= bool(run(db, contractor, as_of, rows, rigs, f'{contractor} Fleet Status Report {as_of}'))
    if a.web is not None:
        for name in (a.web or sorted(WEB)):
            # Nettsidene leses daglig. Uten endringer skrives ingenting, så kilden
            # beholder datoen da tidslinjen sist endret seg.
            trial = json.loads(json.dumps(db))
            try:
                contractor, as_of, rows, rigs, url = WEB[name]()
            except Exception as e:  # nettsiden endret eller nede: behold forrige datasett
                print(f'{name}: feilet ({e}); beholder forrige datasett', file=sys.stderr)
                continue
            if run(trial, contractor, as_of, rows, rigs, f'{contractor} website (fleet timeline) {as_of}', url):
                db = trial
                changed = True
    prune_sources(db)
    changed |= link_companies(db)
    if a.dry_run or not changed:
        return
    with open(PATH, 'w', encoding='utf-8') as fh:
        json.dump(db, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    print(f'skrev {PATH}')


if __name__ == '__main__':
    main()
