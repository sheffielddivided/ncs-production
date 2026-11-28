"""
Henter Equinors crude oil assays (https://www.equinor.com/energy/crude-oil-assays)
og lagrer egenskapene for hele råoljen som data/crude_assays.json.

Siden er en Next.js/Sanity-side. Tabellen (oppdatert dato, navn, beskrivelse,
opprinnelse, API) ligger som Sanity-blokker i sidens innebygde JSON, og hvert
navn lenker til et xlsx-regneark på cdn.sanity.io. Fra regnearkets
'Summary'-ark leses «Whole Crude Properties» (API, tetthet, svovel, TAN,
hellepunkt osv.) og prøve- og assaydato, ved å slå opp etiketten og ta tallet
i cellen til høyre. Mangler et regneark eller et tall, brukes API-et fra
tabellen og resten utelates.

    pip install requests openpyxl
    python fetch_assays.py

I tillegg leses Vår Energis assays (https://varenergi.no/en/operations/crude-oil-assays/)
for blends Equinor ikke har (Balder Blend). Vår Energi legger dem ut som xlsx
under wp-content/uploads/ÅÅÅÅ/MM/; første ark er blenden, med etiketten i
første kolonne, eventuelt en enhet, og så verdien for hel råolje. Regnearket
har ingen prøvedato, så opplastingsmåneden i adressen brukes som «updated».
Goliat ligger der som PDF og er identisk med Equinors (samme prøve fra 2022).

BP (https://www.bp.com/.../crudes-assays.html) avviser skript (403), så BP kan
ikke hentes automatisk. Der Equinors prøve er vesentlig eldre enn BPs, legges
BPs tall inn for hånd i BP_OVERRIDES (API, svovel og TAN fra BPs tabell, med
prøvedato); tetthet og hellepunkt fra den gamle prøven utelates da. Krysssjekket
2026-11-28: Alvheim, Grane, Johan Sverdrup og Skarv stemmer med Equinor
(±0,2 API); Ekofisk avvek med 1,5 API (Equinor 2015, BP 2026).

Kjøres månedlig av .github/workflows/update-assays.yml.

  crude_assays.json  {"source": url,
                      "assays": [{"name", "desc", "origin", "updated", "url", "file",
                                  "api", "density", "sulphur", "tan", "pour", "sample", "src"}...]}
"""

import io
import json
import os
import re
import sys
from datetime import datetime

import openpyxl
import requests

PAGE = 'https://www.equinor.com/energy/crude-oil-assays'
VE_PAGE = 'https://varenergi.no/en/operations/crude-oil-assays/'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'crude_assays.json')
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ncs-insight-data/1.0)'}

# Etikett i regnearket -> nøkkel i utdata.
PROPS = {
    'API Gravity': 'api',
    'Density @ 15°C (g/cc)': 'density',
    'Total Sulphur (% wt)': 'sulphur',
    'Total Acid Number (mgKOH/g)': 'tan',
    'Pour Point (°C)': 'pour',
}
DATE_RE = re.compile(r'^\d\d\.\d\d\.\d{4}$')
NUM_RE = re.compile(r'^\d+(\.\d+)?$')


def table_rows(html):
    """Radene i tabellen: (oppdatert, navn, beskrivelse, opprinnelse, api, url, fil)."""
    s = html.replace('\\"', '"')
    start = s.find('Click on the name of the assay')
    if start < 0:
        sys.exit('Fant ikke assay-tabellen på siden; har sideoppsettet endret seg?')
    s = s[start:]
    # Lenkene: teksten i spennet og vedlegget det peker på (via markDefs-nøkkelen).
    links = {}
    for m in re.finditer(r'"marks":\["([0-9a-f]+)"\],"text":"([^"]*)"\}\],"markDefs":\[\{"_key":"\1","_type":"attachment",'
                         r'"attachment":\{[^}]*?"originalFilename":"([^"]*)"[^}]*?"url":"([^"]*)"', s):
        links.setdefault(m.group(2).strip(), (m.group(4), m.group(3)))
    texts = re.findall(r'"text":"([^"]*)"', s)
    rows, seen, k = [], set(), 0
    while k + 4 < len(texts):
        t = texts[k:k + 5]
        if DATE_RE.match(t[0]) and NUM_RE.match(t[4]):
            name = t[1].strip()
            if name not in seen:   # tabellen ligger to ganger i sidedataene
                seen.add(name)
                url, fn = links.get(name, (None, None))
                rows.append((t[0], name, t[2].strip(), t[3].strip(), float(t[4]), url, fn))
            k += 5
        else:
            k += 1
    if len(rows) < 20:
        sys.exit(f'Fant bare {len(rows)} rader i assay-tabellen; avbryter.')
    return rows


def parse_xlsx(data):
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb['Summary'] if 'Summary' in wb.sheetnames else wb[wb.sheetnames[0]]
    out = {}
    for row in ws.iter_rows(min_row=1, max_row=40, values_only=True):
        cells = list(row)
        for i, c in enumerate(cells):
            if not isinstance(c, str):
                continue
            lab = c.strip()
            nxt = next((x for x in cells[i + 1:] if x is not None and not (isinstance(x, str) and not x.strip())), None)
            if lab in PROPS and isinstance(nxt, (int, float)) and PROPS[lab] not in out:
                out[PROPS[lab]] = round(float(nxt), 4)
            elif lab == 'Sample Date:' and isinstance(nxt, (str, datetime)) and 'sample' not in out:
                try:
                    d = nxt if isinstance(nxt, datetime) else datetime.strptime(nxt.strip(), '%d %B %Y')
                    out['sample'] = d.strftime('%Y-%m-%d')
                except ValueError:
                    pass
    return out


# Vår Energi: etikett i første kolonne -> nøkkel; verdien er første tall etter etiketten.
VE_PROPS = {'api gravity @ 60°f': 'api', 'density @15°c': 'density', 'sulphur': 'sulphur',
            'acidity': 'tan', 'pour pt.': 'pour'}


def parse_ve_xlsx(data):
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    out = {}
    for row in ws.iter_rows(min_row=1, max_row=60, values_only=True):
        cells = [c for c in row if c is not None and not (isinstance(c, str) and not c.strip())]
        if not cells or not isinstance(cells[0], str):
            continue
        key = VE_PROPS.get(cells[0].strip().lower())
        num = next((c for c in cells[1:] if isinstance(c, (int, float))), None)
        if key and num is not None and key not in out:
            out[key] = round(float(num), 4)
    return wb.sheetnames[0], out


def ve_assays(known):
    """Vår Energis xlsx-assays for blends som ikke er i Equinors liste."""
    try:
        r = requests.get(VE_PAGE, headers=HEADERS, timeout=60)
        r.raise_for_status()
    except Exception as e:
        print(f'  Vår Energi: kunne ikke hente siden ({e})', file=sys.stderr)
        return []
    out = []
    for url in sorted(set(re.findall(r'href="(https://varenergi\.no/wp-content/uploads/(\d{4})/(\d{2})/[^"]+\.xlsx)"', r.text))):
        u, y, m = url
        try:
            x = requests.get(u, headers=HEADERS, timeout=60)
            x.raise_for_status()
            sheet, props = parse_ve_xlsx(x.content)
        except Exception as e:
            print(f'  Vår Energi {u}: kunne ikke lese regnearket ({e})', file=sys.stderr)
            continue
        name = ' '.join(w.capitalize() for w in sheet.split())
        if name.lower() in known or 'api' not in props:
            continue
        out.append({'name': name, 'desc': '', 'origin': 'Norway', 'updated': f'{y}-{m}-01',
                    'url': u, 'file': u.rsplit('/', 1)[1], 'src': 'Vår Energi', **props})
    return out


BP_PAGE = ('https://www.bp.com/en/global/bp-supply-trading-and-shipping/documents-and-downloads/'
           'technical-downloads/crudes-assays.html')
BP_OVERRIDES = {
    'Ekofisk Blend': {'api': 40.4, 'sulphur': 0.18, 'tan': 0.12, 'sample': '2026-01-16', 'src': 'BP',
                      'url': 'https://www.bp.com/content/dam/bp/business-sites/en/global/bp-supply-trading-and-shipping/'
                             'documents/technical-documents-and-downloads/crudes/ekofisk.xls', 'file': 'ekofisk.xls'},
}


def main():
    r = requests.get(PAGE, headers=HEADERS, timeout=60)
    r.raise_for_status()
    assays = []
    for upd, name, desc, origin, api, url, fn in table_rows(r.text):
        a = {'name': name, 'desc': desc, 'origin': origin,
             'updated': datetime.strptime(upd, '%d.%m.%Y').strftime('%Y-%m-%d'),
             'url': url, 'file': fn, 'api': api, 'src': 'Equinor'}
        if url:
            try:
                x = requests.get(url, headers=HEADERS, timeout=60)
                x.raise_for_status()
                props = parse_xlsx(x.content)
                # API-et i tabellen er avrundet; regnearkets tall er mer presist.
                a.update({k: v for k, v in props.items() if k != 'api' or abs(v - api) < 0.6})
            except Exception as e:   # ett regneark som feiler stopper ikke resten
                print(f'  {name}: kunne ikke lese regnearket ({e})', file=sys.stderr)
        assays.append(a)
        print(f"{name:30s} API {a['api']:6.2f}  S {a.get('sulphur', '–')}")
    for a in assays:
        o = BP_OVERRIDES.get(a['name'])
        if o and o['sample'] > a.get('sample', a['updated']):
            for k in ('density', 'pour'):
                a.pop(k, None)
            a.update(o)
            print(f"{a['name']:30s} -> BP {o['sample']}: API {o['api']}, S {o['sulphur']}")
    for a in ve_assays({a['name'].lower() for a in assays}):
        assays.append(a)
        print(f"{a['name']:30s} API {a['api']:6.2f}  S {a.get('sulphur', '–')}  (Vår Energi)")
    assays.sort(key=lambda a: a['name'].lower())
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'source': PAGE, 'sources': [PAGE, VE_PAGE, BP_PAGE], 'assays': assays},
                  f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write('\n')
    print(f'{len(assays)} assays -> {OUT}')


if __name__ == '__main__':
    main()
