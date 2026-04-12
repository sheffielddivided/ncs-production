"""
Henter produksjonsforecast fra Woodmac og lagrer som woodmac_forecast.json.
Kjør lokalt og push json-filen til GitHub.

Bruk:
    pip install requests
    python fetch_woodmac.py
"""

import json
import requests
from datetime import date

API_KEY  = '96a71880-1bc0-11f1-aa18-c3ae722064ac'
BASE_URL = 'https://data.woodmac.com'
ODATA    = '/query/upstream_weekly/imp-met/odata/field_annual_production_kbd'
FILTER   = "country_name eq 'Norway' and field_is_top_level eq 'Y' and year ge 2024"
SELECT   = 'field_name,year,liquid_gas,metric_value,unit'
OUT_FILE = 'woodmac_forecast.json'

def fetch_all():
    rows = []
    url  = f"{BASE_URL}{ODATA}"
    params = {
        '$select': SELECT,
        '$filter': FILTER,
        '$top':    5000,
    }
    headers = { 'apikey': API_KEY, 'Accept': 'application/json' }

    while url:
        print(f"  Henter: {url[:80]}...")
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get('value', [])
        rows.extend(batch)
        print(f"  → {len(batch)} rader (totalt {len(rows)})")

        # OData paginering
        next_link = data.get('@odata.nextLink')
        if next_link:
            url    = next_link
            params = {}          # nextLink har allerede alle params
        else:
            url = None

    return rows

def main():
    print("Henter Woodmac forecast-data for Norge...")
    rows = fetch_all()

    if not rows:
        print("Ingen data mottatt – avbryter.")
        return

    # Pivot: { field_name: { year: { liquid: x, gas: x } } }
    fields = {}
    for r in rows:
        name = r.get('field_name', '').strip()
        year = r.get('year')
        lg   = (r.get('liquid_gas') or '').lower()
        val  = float(r.get('metric_value') or 0)
        unit = r.get('unit', 'kbd')

        if not name or not year:
            continue

        if name not in fields:
            fields[name] = {}
        if year not in fields[name]:
            fields[name][year] = { 'liquid': 0, 'gas': 0 }

        if 'liquid' in lg:
            fields[name][year]['liquid'] += val
        elif 'gas' in lg:
            fields[name][year]['gas'] += val

    output = {
        'generated': date.today().isoformat(),
        'source':    'Woodmac Lens Direct – field_annual_production_kbd',
        'unit':      'kbd',
        'fields':    fields
    }

    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nFerdig! {len(fields)} felt lagret i {OUT_FILE}")
    print(f"Totalt {len(rows)} rader hentet.")

if __name__ == '__main__':
    main()
