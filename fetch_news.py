"""
Henter Sodirs siste produksjonsmeldinger fra RSS-feeden og skriver dem til
data/sodir_news.json. NCS → Production lenker til den nyeste.

Feeden kan ikke leses direkte fra nettleseren (CORS), derfor hentes den her.
Feiler kilden, beholdes forrige fil. Steget i arbeidsflyten er
continue-on-error.
"""

import json
import os
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


def main():
    news = items(get(FEED))
    if not news:
        raise SystemExit('Ingen meldinger i feeden - beholder forrige fil.')
    data = {'source': FEED, 'items': news}
    try:
        with open(OUT, encoding='utf-8') as f:
            if json.load(f) == data:
                print('Uendret.')
                return
    except (OSError, ValueError):
        pass
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f'Skrev {len(news)} meldinger, nyeste: {news[0]["title"]}')


if __name__ == '__main__':
    main()
