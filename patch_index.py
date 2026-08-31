"""
Engangsscript: kobler index.html til de statiske SODIR-datafilene under data/.

Kjores av .github/workflows/apply-index-patch.yml, som sletter denne fila
etterpa. Grunnen til at endringen gjores slik, og ikke ved a pushe hele
index.html, er at fila inneholder en 13 KB base64-kodet favicon som ikke kan
reproduseres palitelig av et sprakmodell-verktoy. Her rores kun ren tekst,
og favicon-linja er aldri en del av noen erstatning.
"""

import sys

PATH = 'index.html'

BUNDLE_BLOCK = """const RES_LAYER   = 7114;
const BBL_PER_SM3 = 6.29;

// ── STATISK DATA-BUNDLE ──────────────────────────────────────────────────────
// Dataene leses normalt fra JSON-filer i repoet, oppdatert daglig av
// .github/workflows/update-sodir-data.yml. Det gir én rask fetch per fil i
// stedet for titalls paginerte API-kall, og gjør appen uavhengig av at Sodir
// svarer akkurat i det siden lastes.
// Mangler eller feiler filene, faller alt tilbake til live-API-et under.
const DATA_BASE = 'data/';

const bundle = { fields:null, production:null, licensees:null, reserves:null, meta:null };

async function fetchBundle(name) {
  try {
    const res = await fetch(`${DATA_BASE}${name}.json`, {cache:'no-cache'});
    if (!res.ok) return null;
    return await res.json();
  } catch(e) { return null; }
}

async function loadBundles() {
  const names = ['fields','production','licensees','reserves','meta'];
  const results = await Promise.all(names.map(fetchBundle));
  names.forEach((n, i) => { bundle[n] = results[i]; });
  const gen = bundle.meta && bundle.meta.generated;
  const el = document.getElementById('dataDate');
  if (gen && el) el.textContent = ` · data per ${gen}`;
}

// Bygg om kompakte [year,month,oil,gas,oe,water]-rader til samme form som
// API-et returnerer, slik at all tegnekode kan brukes uendret.
function prodRowsFromBundle(fieldId) {
  const series = bundle.production && bundle.production.fields[fieldId];
  if (!series) return [];
  return series.map(([year, month, oil, gas, oe, water]) => ({
    attributes: {
      prfNpdidInformationCarrier: +fieldId,
      prfYear: year,
      prfMonth: month,
      prfPrdOilNetMillSm3: oil,
      prfPrdGasNetBillSm3: gas,
      prfPrdOeNetMillSm3: oe,
      prfPrdProducedWaterInFieldMillS: water,
    }
  }));
}"""

REPLACEMENTS = [
    # 1. Bundle-laget rett etter konstantene
    ("""const RES_LAYER   = 7114;
const BBL_PER_SM3 = 6.29;""", BUNDLE_BLOCK),

    # 2. Session-cache versjonsbump (gamle v1-oppforinger skal ikke gjenbrukes)
    ("""const SESSION_PROD_PREFIX = 'ncs_prod_v1_';
const SESSION_LIC_KEY     = 'ncs_lic_v1';""",
     """const SESSION_PROD_PREFIX = 'ncs_prod_v2_';
const SESSION_LIC_KEY     = 'ncs_lic_v2';"""),

    # 3. loadFields leser fra bundle
    ("""async function loadFields() {
  const features = await queryLayer(FIELD_LAYER, {
    outFields:'fldNpdidField,fldName', orderByFields:'fldName ASC', resultRecordCount:500
  });
  allFields = features
    .map(f => ({id:f.attributes.fldNpdidField, name:f.attributes.fldName}))
    .sort((a,b) => a.name.localeCompare(b.name,'en'));""",
     """async function loadFields() {
  if (bundle.fields && bundle.fields.rows) {
    allFields = bundle.fields.rows.map(([id, name]) => ({id, name}));
  } else {
    const features = await queryLayer(FIELD_LAYER, {
      outFields:'fldNpdidField,fldName', orderByFields:'fldName ASC', resultRecordCount:500
    });
    allFields = features.map(f => ({id:f.attributes.fldNpdidField, name:f.attributes.fldName}));
  }
  allFields.sort((a,b) => a.name.localeCompare(b.name,'en'));"""),

    # 4. loadProdForFields leser fra bundle
    ("""async function loadProdForFields(ids) {
  const fy = +document.getElementById('yearFrom').value;
  const ty = +document.getElementById('yearTo').value;

  // Warm from session cache""",
     """async function loadProdForFields(ids) {
  // Bundelen har alle år for alle felt, så ingenting trenger å hentes.
  // Array.isArray dekker både "ikke lastet" (undefined) og "forrige forsøk
  // feilet" (null).
  if (bundle.production) {
    ids.forEach(id => {
      if (!Array.isArray(fieldProdCache[id])) fieldProdCache[id] = prodRowsFromBundle(id);
    });
    return;
  }

  const fy = +document.getElementById('yearFrom').value;
  const ty = +document.getElementById('yearTo').value;

  // Warm from session cache"""),

    # 5. Lisensiaerer fra bundle, for session-cachen sjekkes
    ("""  document.getElementById('cmpLoadingNotice').classList.add('visible');
  try {
    // ── 1. Try session cache first ──────────────────────────────────────────
    const cached = sessionGet(SESSION_LIC_KEY);""",
     """  document.getElementById('cmpLoadingNotice').classList.add('visible');
  try {
    // ── 0. Statisk bundle først ─────────────────────────────────────────────
    if (bundle.licensees && bundle.licensees.rows) {
      _ingestLicRecords(bundle.licensees.rows.map(
        ([fldNpdidField, cmpLongName, fldCompanyShare, fldLicenseeFrom, fldLicenseeTo]) =>
          ({fldNpdidField, cmpLongName, fldCompanyShare, fldLicenseeFrom, fldLicenseeTo})
      ));
      allLicLoaded = true;
      return;
    }

    // ── 1. Try session cache first ──────────────────────────────────────────
    const cached = sessionGet(SESSION_LIC_KEY);"""),

    # 6. Reserver fra bundle
    ("""async function loadReserves() {
  if (reservesLoaded) return;
  try {""",
     """async function loadReserves() {
  if (reservesLoaded) return;
  if (bundle.reserves && bundle.reserves.companies) {
    companyReserves = bundle.reserves.companies;
    reservesLoaded = true;
    return;
  }
  try {"""),

    # 7. init laster bundelen forst
    ("""async function init() {
  renderViewTabs();
  updateFab();
  setInfo('');

  // ── Phase 1: Load fields list (fast, ~100 records) ──""",
     """async function init() {
  renderViewTabs();
  updateFab();
  setInfo('');

  // ── Phase 0: Prøv de statiske datafilene. Finnes de, serveres alt herfra;
  //    hvis ikke faller lastefunksjonene under tilbake til live-API-et. ──
  await loadBundles();

  // ── Phase 1: Load fields list (fast, ~100 records) ──"""),

    # 8. Bakgrunns-prefetch er unodvendig nar bundelen finnes
    ("""async function prefetchAllFields() {
  const fy = +document.getElementById('yearFrom').value;
  const ty = +document.getElementById('yearTo').value;""",
     """async function prefetchAllFields() {
  if (bundle.production) return; // alt ligger allerede i bundelen

  const fy = +document.getElementById('yearFrom').value;
  const ty = +document.getElementById('yearTo').value;"""),

    # 9. Vis datostempel for datasettet i kildehenvisningen
    ("""<div class="source-note"><a href="https://factpages.sodir.no" target="_blank">Norwegian Offshore Directorate</a> – NLOD 2.0</div>""",
     """<div class="source-note"><a href="https://factpages.sodir.no" target="_blank">Norwegian Offshore Directorate</a> – NLOD 2.0<span id="dataDate"></span></div>"""),
]


def main():
    with open(PATH, encoding='utf-8') as fh:
        text = fh.read()

    for i, (old, new) in enumerate(REPLACEMENTS, 1):
        # Sjekk "allerede anvendt" FORST. Flere av erstatningene inneholder sin
        # egen sokestreng, sa en ren count-sjekk ville patchet dem om igjen.
        if new in text:
            print(f"  {i}. allerede anvendt - hopper over")
            continue
        n = text.count(old)
        if n == 0:
            print(f"FEIL: erstatning {i} fant ingen treff", file=sys.stderr)
            return 1
        if n > 1:
            print(f"FEIL: erstatning {i} traff {n} steder (ma vaere unik)", file=sys.stderr)
            return 1
        text = text.replace(old, new)
        print(f"  {i}. ok")

    with open(PATH, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f"\nFerdig - {PATH} er {len(text.encode('utf-8'))} bytes")
    return 0


if __name__ == '__main__':
    sys.exit(main())
