"""
Henter daglige TTF day-ahead-priser (EEX TTF Neutral Gas Price, EUR/MWh) fra
eugas-løsningen i repoet sheffielddivided/eia, som samler dem inn daglig, og
skriver dem til data/ttf_daily.json.

Brukes bare til prognosen for inneværende periode i Gas-fanen i Prices:
måneder IMF ennå ikke har publisert fylles med snittet av dagsprisene, og
resten av perioden med siste observerte pris. Dagsprisene vises ikke selv.

Feiler hentingen, beholdes forrige fil. Steget i arbeidsflyten er
continue-on-error.
"""

import json
import os
import sys
import time

import requests

SRC = 'https://raw.githubusercontent.com/sheffielddivided/eia/main/data/ttf_price.json'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ttf_daily.json')


def main():
    last = None
    for attempt in range(4):
        try:
            r = requests.get(SRC, timeout=60)
            r.raise_for_status()
            src = r.json()
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 ** (attempt + 1))
    else:
        print(f'TTF daily: henting feilet ({last}); beholder forrige fil', file=sys.stderr)
        return
    prices = src.get('prices') or {}
    rows = sorted([d, round(float(v), 3)] for d, v in prices.items() if v is not None)
    if not rows:
        print('TTF daily: ingen priser i kilden; beholder forrige fil', file=sys.stderr)
        return
    out = {'columns': ['date', 'eur_mwh'], 'rows': rows, 'updated': src.get('updatedAt'),
           'source': 'EEX TTF Neutral Gas Price (day-ahead), via sheffielddivided/eia (eugas)'}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f'TTF daily: {len(rows)} dager, {rows[0][0]} – {rows[-1][0]}')


if __name__ == '__main__':
    main()
