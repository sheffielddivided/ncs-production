"""
Henter prospektnavn til letebrønner fra Sodirs RSS-feed «Exploration drilling
results» og skriver dem til data/exploration_prospects.json.

Sodirs brønnlag gir bare navn til brønner som har gjort funn. Tørre brønner
har ikke navn der, men pressemeldingen nevner som regel prospektet
(«... dry well in the "Svarteknippa" prospect»). Brønnen står i tittelen,
prospektet i beskrivelsen. Saksidene blokkerer automatiserte kall, så det er
bare det feeden selv inneholder som brukes.

Fila er et arkiv: feeden har bare de siste sakene, og navn som en gang er
funnet beholdes. Feiler kilden, beholdes forrige fil. Steget i arbeidsflyten
er continue-on-error.

Nøkkelen er grunnbrønnen («35/10-15», uten sidesteg-suffikset), slik at alle
grener av samme brønn arver navnet.
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

FEED = 'https://www.sodir.no/en/whats-new/news/rss/exploration-drilling-results/'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'exploration_prospects.json')
RETRIES = 4

# 35/10-15 S, 7220/7-CD-1H, 31/4-A-15 D, 25/7- 9 S (Sodir skriver av og til feil)
WELL = re.compile(r'\b(\d{1,4}/\d{1,2}-(?:[A-Z]{1,3}[- ])?\s?\d{1,3})(?: ?([A-Z]{1,2}\d?))?\b')
Q_OPEN, Q_CLOSE = '«"“‘\'', '»"”’\''
NAME = rf'[{Q_OPEN}]([^{Q_OPEN}{Q_CLOSE}]{{2,50}}?)[{Q_CLOSE}]'
# «Skoll» and «Hati» prospects  /  the «Heidrun Cellar SE» prospect
QUOTED = re.compile(rf'((?:{NAME}(?:\s*(?:,|and|&)\s*)?)+)\s+prospects?\b', re.I)
# the Heisenberg prospect, the so-called E prospect
PLAIN = re.compile(r'\b(?:on|in|at|of|to|the|called)\s+(?:the\s+)?(?:so-called\s+)?'
                   r'([A-Z][\w-]*(?: [A-Z][\w-]*){0,2})\s+prospect\b')
SKIP = {'the', 'this', 'a', 'new', 'same', 'deep', 'shallow', 'exploration', 'wildcat'}


def get(url):
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            return r.content
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(str(last))


def base_well(name):
    """«35/10-15 S» -> «35/10-15»; «31/4-A-15 D» -> «31/4-A-15»."""
    name = re.sub(r'\s+', ' ', name.strip()).replace('- ', '-')
    m = WELL.fullmatch(name)
    return re.sub(r'\s', '', m.group(1)) if m else ''


def prospects_in(text):
    names = []
    for m in QUOTED.finditer(text):
        names += [n.strip() for n in re.findall(NAME, m.group(1))]
    if not names:
        names = [m.group(1) for m in PLAIN.finditer(text)]
    out = []
    for n in names:
        n = n.strip(' .,')
        if len(n) > 2 and n.lower() not in SKIP and n not in out:
            out.append(n)
    return out


def parse(xml):
    found = {}
    for it in ET.fromstring(xml).iter('item'):
        title = (it.findtext('title') or '').strip()
        desc = (it.findtext('description') or '').strip()
        link = (it.findtext('link') or '').strip()
        try:
            day = parsedate_to_datetime(it.findtext('pubDate')).strftime('%Y-%m-%d')
        except Exception:  # noqa: BLE001
            day = ''
        names = prospects_in(desc) or prospects_in(title)
        if not names:
            continue
        # Brønnene i tittelen først; står de bare i beskrivelsen, brukes de.
        wells = []
        for text in (title, desc):
            wells = [base_well(m.group(0)) for m in WELL.finditer(text)]
            wells = [w for w in dict.fromkeys(wells) if w]
            if wells:
                break
        for w in wells:
            found.setdefault(w, {'prospect': ' / '.join(names), 'date': day, 'url': link})
    return found


def main():
    try:
        found = parse(get(FEED))
    except Exception as exc:  # noqa: BLE001
        print(f'Feeden feilet, beholder forrige fil: {exc}')
        return
    try:
        with open(OUT, encoding='utf-8') as fh:
            old = json.load(fh)
    except Exception:  # noqa: BLE001
        old = {}
    merged = {**old, **found}
    if merged == old:
        print(f'Prospektnavn: ingen endring ({len(old)} brønner)')
        return
    merged = dict(sorted(merged.items()))
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    print(f'Prospektnavn: {len(merged)} brønner ({len(merged) - len(old)} nye)')


if __name__ == '__main__':
    main()
