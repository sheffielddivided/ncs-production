"""
Henter Sodirs siste produksjonsmeldinger fra RSS-feeden og skriver dem til
data/sodir_news.json. NCS → Production lenker til den nyeste.

Ingressen har Sodirs foreløpige tall for måneden: snitt per dag av olje, NGL
og kondensat (fat). Disse samles i «prelim», et arkiv per måned som bare
vokser (feeden har bare de seks siste meldingene); en ny melding for samme
måned erstatter den gamle. NCS → Production (Monthly, Oil & Gas) viser dem
som foreløpige tall, også for måneder feltdataene ennå ikke har. Gass og
Sodirs prognose står bare i selve artikkelen, som er bak Cloudflare (403) og
derfor ikke hentes.

Feeden kan ikke leses direkte fra nettleseren (CORS), derfor hentes den her.
Feiler kilden, beholdes forrige fil. Steget i arbeidsflyten er
continue-on-error.
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

FEED = 'https://www.sodir.no/en/whats-new/news/rss/production-figures/'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'sodir_news.json')
KEEP = 6
RETRIES = 4


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


def items(xml):
    out = []
    for it in ET.fromstring(xml).iter('item'):
        txt = lambda tag: ' '.join((it.findtext(tag) or '').split())
        date = txt('pubDate')
        try:
            # Sodir skriver «Z» som sone, som parsedate tolker som UTC.
            date = parsedate_to_datetime(date).date().isoformat()
        except (TypeError, ValueError):
            pass
        if txt('link') and txt('title'):
            out.append({'title': txt('title'), 'link': txt('link'),
                        'description': txt('description'), 'date': date})
    out.sort(key=lambda x: x['date'], reverse=True)
    return out[:KEEP]


MONTHS = {m: i + 1 for i, m in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july',
     'august', 'september', 'october', 'november', 'december'])}
# «Preliminary production figures for October 2026 show an average daily
# production of 2 030 000 barrels of oil, NGL and condensate.»
PRELIM = re.compile(r'figures for (\w+) (\d{4}) show an average daily production of '
                    r'([\d\s,.]+?) barrels of oil, NGL and condensate', re.I)


def prelim(item):
    """{month, liquids, date, link} fra meldingen, eller None."""
    m = PRELIM.search(item['description'])
    if not m or m.group(1).lower() not in MONTHS:
        return None
    bbl = int(re.sub(r'\D', '', m.group(3)) or 0)
    # Sokkelen har ligget mellom 1 og 4 mill. fat per dag; noe annet er feiltolket.
    if not 500_000 <= bbl <= 5_000_000:
        return None
    return {'month': f'{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}', 'liquids': bbl,
            'date': item['date'], 'link': item['link']}


def main():
    news = items(get(FEED))
    if not news:
        raise SystemExit('Ingen meldinger i feeden - beholder forrige fil.')
    old = {}
    try:
        with open(OUT, encoding='utf-8') as f:
            old = json.load(f)
    except (OSError, ValueError):
        pass
    arc = {p['month']: p for p in old.get('prelim') or []}
    for it in reversed(news):   # eldste først, så nyeste melding for en måned vinner
        p = prelim(it)
        if p:
            arc[p['month']] = p
    data = {'source': FEED, 'items': news, 'prelim': [arc[k] for k in sorted(arc)]}
    if old == data:
        print('Uendret.')
        return
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f'Skrev {len(news)} meldinger, nyeste: {news[0]["title"]}')


if __name__ == '__main__':
    main()
