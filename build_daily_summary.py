"""
Daglig AI-oppsummering av endringene i dataene, til toppen av updates.html.

Leser data/update_details/<dag>.json (build_update_log.py), sender faktaene
til Claude (Anthropic API) med en fast instruks og lagrer svaret som punkter i
data/daily_summary.json. Kjøres av .github/workflows/update-summary.yml like
etter midnatt UTC for dagen før, når kveldskjøringen av loggen er ferdig.

    pip install anthropic
    ANTHROPIC_API_KEY=... python build_daily_summary.py            # i går (UTC)
    python build_daily_summary.py --day 2026-11-28 --force          # lag på nytt
    python build_daily_summary.py --day 2026-11-28 --dry-run        # vis teksten som sendes

Kilder uten faktasetninger (bare «updated») sendes som én navneliste, så
modellen vet at de er oppdatert, men ikke finner på innhold. Har dagen færre
enn MIN_FACTS fakta, lages ingen oppsummering (og ingen API-kall).
"""

import argparse
import datetime as dt
import json
import os
import sys

DETAIL_DIR = 'data/update_details'
OUT = 'data/daily_summary.json'
KEEP_DAYS = 60
MIN_FACTS = 3
MODEL = 'claude-opus-5-5'

SYSTEM = """You write the daily data briefing for NCS Insight, a website about oil and gas on the \
Norwegian Continental Shelf (NCS) and neighbouring shelves. You get the changes registered in the \
site's data sources on one day, as short facts per source.

Write 3-7 bullet points in English on what matters most, most important first: new production \
figures and revisions, wells spudded or completed, discoveries, licence and ownership changes, \
safety cases, gas transport outages, notable company or regulator news, and price moves.

Rules:
- Use only the facts given. Do not add background, causes, forecasts or opinions.
- Keep names, well names, numbers and units as given; round sensibly.
- Merge related facts into one bullet (for example several Gassco events at the same asset).
- Leave out routine updates with no facts, and minor items when there is more important news.
- Each bullet is one or two plain sentences, at most 40 words, no headings, no bold, no links.
- Start every bullet on its own line with "- ".
- If nothing in the facts is worth a briefing, reply with exactly: NONE"""


def load_details(day):
    path = os.path.join(DETAIL_DIR, f'{day}.json')
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def prompt_text(day, doc):
    """Faktaene som ren tekst, gruppert per seksjon og kilde."""
    lines, routine, n = [f'Changes registered on {day} (UTC):', ''], [], 0
    for s in doc.get('sources') or []:
        facts = s.get('facts') or []
        if not facts:
            routine.append(s.get('source') or s.get('key'))
            continue
        lines.append(f"## {s.get('source')} ({s.get('section')}) - {s.get('summary')}")
        lines += [f'- {f}' for f in facts]
        lines.append('')
        n += len(facts)
    if routine:
        lines.append('Also updated, no details: ' + ', '.join(routine) + '.')
    return '\n'.join(lines), n


def summarise(text):
    import anthropic                      # bare nødvendig når API-et faktisk kalles
    client = anthropic.Anthropic()        # ANTHROPIC_API_KEY fra miljøet
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        output_config={'effort': 'medium'},
        # Avvises forespørselen av sikkerhetsfiltrene, kjøres den på en annen modell.
        betas=['server-side-fallback-2026-07-01'],
        fallbacks='default',
        system=SYSTEM,
        messages=[{'role': 'user', 'content': text}],
    )
    u = resp.usage
    print(f'Model {resp.model}, stop {resp.stop_reason}, tokens in {u.input_tokens} / out {u.output_tokens}', file=sys.stderr)
    if resp.stop_reason == 'refusal':
        raise SystemExit('The request was declined; no summary written.')
    out = ''.join(b.text for b in resp.content if b.type == 'text').strip()
    return out, resp.model


def bullets(text):
    if text.strip().upper() == 'NONE':
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line[:2] in ('- ', '* ', '• '):
            out.append(line[2:].strip())
        elif line and out:                # en punkttekst som er brutt over flere linjer
            out[-1] += ' ' + line
    return [b for b in out if b]


def load_out():
    try:
        with open(OUT, encoding='utf-8') as fh:
            d = json.load(fh)
            if isinstance(d.get('days'), list):
                return d
    except (OSError, ValueError):
        pass
    return {'days': []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--day', help='YYYY-MM-DD (default: yesterday, UTC)')
    ap.add_argument('--force', action='store_true', help='replace an existing summary for the day')
    ap.add_argument('--dry-run', action='store_true', help='print the prompt and stop')
    args = ap.parse_args()
    day = args.day or (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()

    doc = load_details(day)
    if doc is None:
        print(f'{day}: no details file, nothing to summarise')
        return 0
    text, n = prompt_text(day, doc)
    if args.dry_run:
        print(text)
        print(f'\n[{n} facts]', file=sys.stderr)
        return 0
    log = load_out()
    if any(x.get('d') == day for x in log['days']) and not args.force:
        print(f'{day}: summary exists (use --force to replace)')
        return 0
    if n < MIN_FACTS:
        print(f'{day}: only {n} facts, no summary')
        return 0

    raw, model = summarise(text)
    pts = bullets(raw)
    print('\n'.join(f'- {p}' for p in pts) or '(NONE)')
    entry = {'d': day, 'bullets': pts, 'model': model, 'facts': n,
             'generated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}
    days = [x for x in log['days'] if x.get('d') != day] + [entry]
    cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    log = {'note': 'AI-written daily briefing of the changes in data/update_details/<d>.json. '
                   'Generated by build_daily_summary.py; bullets is empty when nothing was worth a briefing.',
           'days': sorted((x for x in days if x['d'] >= cutoff), key=lambda x: x['d'], reverse=True)}
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(log, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
