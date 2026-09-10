"""
Engangsscript: fikser Companies-visningen slik at multi-select under
"Oil & Gas" viser en kombinert olje/gass-splitt i stedet for én serie
per selskap (som feilaktig duplikerte "OE per field"-oppforselen).

Kjores av .github/workflows/apply-companies-oilgas-fix.yml, som sletter
denne fila og seg selv etterpa (samme monster som index.html-koblingen
til SODIR-datafilene, av samme grunn: 13 KB base64-favicon som ikke kan
reproduseres palitelig av et sprakmodell-verktoy).
"""

import sys

PATH = 'index.html'

OLD = """  } else {
    companies.forEach((cmp, i) => {
      const cmpFields = getFieldsForCompany(cmp);
      const color = PALETTE[i%PALETTE.length][0];
      const d = labels.map(lbl => {
        if (period === 'quarterly') return +quarterBoedEquity(cmpFields, lbl, qMap, (v,dy)=>oeBoed(v,dy), a=>a.prfPrdOeNetMillSm3||0, cmp).toFixed(0);
        let tot=0;cmpFields.forEach(id=>{const row=(fieldProdCache[id]||[]).find(r=>periodKey(r.attributes)===lbl);const a=row?.attributes||{};if(!a.prfYear)return;const dy=days(a),share=getShare(id,midDate(a.prfYear,a.prfMonth||7),cmp)/100;tot+=oeBoed(a.prfPrdOeNetMillSm3||0,dy)*share;});return +tot.toFixed(0);
      });
      if (!hasAnyValue(d)) return;
      datasets.push({label:cmp,data:d,backgroundColor:color,borderWidth:0,stack:'s'});
      legend.push({label:cmp,color});
      lastDatasets.push({label:cmp,data:d});
    });
    setChartTitle(`${companies.length} companies · Net OE production`, `Equity-adjusted · boe/day`);
    setInfo(`<strong>Companies:</strong> ${companies.join(', ')} &nbsp;|&nbsp; <strong>Series:</strong> Net OE per company &nbsp;|&nbsp; <strong>Unit:</strong> boe/day &nbsp;|&nbsp; <strong>Basis:</strong> Equity-adjusted, all fields`);
  }"""

NEW = """  } else {
    const oilD = labels.map(lbl => {
      if (period === 'quarterly') {
        return +companies.reduce((tot, cmp) => {
          const cmpFields = getFieldsForCompany(cmp);
          return tot + quarterBoedEquity(cmpFields, lbl, qMap, (v,d)=>Math.max(0,oeBoed(v.oe,d)-gasBoed(v.gas,d)), a=>({oe:a.prfPrdOeNetMillSm3||0,gas:a.prfPrdGasNetBillSm3||0}), cmp);
        }, 0).toFixed(0);
      }
      let tot=0;
      companies.forEach(cmp => {
        const cmpFields = getFieldsForCompany(cmp);
        cmpFields.forEach(id=>{const row=(fieldProdCache[id]||[]).find(r=>periodKey(r.attributes)===lbl);const a=row?.attributes||{};if(!a.prfYear)return;const dy=days(a),share=getShare(id,midDate(a.prfYear,a.prfMonth||7),cmp)/100;tot+=oilBoed(a.prfPrdOilNetMillSm3||0,a.prfPrdGasNetBillSm3||0,a.prfPrdOeNetMillSm3||0,dy)*share;});
      });
      return +tot.toFixed(0);
    });
    const gasD = labels.map(lbl => {
      if (period === 'quarterly') {
        return +companies.reduce((tot, cmp) => {
          const cmpFields = getFieldsForCompany(cmp);
          return tot + quarterBoedEquity(cmpFields, lbl, qMap, (v,d)=>gasBoed(v,d), a=>a.prfPrdGasNetBillSm3||0, cmp);
        }, 0).toFixed(0);
      }
      let tot=0;
      companies.forEach(cmp => {
        const cmpFields = getFieldsForCompany(cmp);
        cmpFields.forEach(id=>{const row=(fieldProdCache[id]||[]).find(r=>periodKey(r.attributes)===lbl);const a=row?.attributes||{};if(!a.prfYear)return;const dy=days(a),share=getShare(id,midDate(a.prfYear,a.prfMonth||7),cmp)/100;tot+=gasBoed(a.prfPrdGasNetBillSm3||0,dy)*share;});
      });
      return +tot.toFixed(0);
    });
    datasets.push({label:'Oil',data:oilD,backgroundColor:OIL_COLOR,borderWidth:0,stack:'s'});
    datasets.push({label:'Gas',data:gasD,backgroundColor:GAS_COLOR,borderWidth:0,stack:'s'});
    legend.push({label:'Oil',color:OIL_COLOR},{label:'Gas',color:GAS_COLOR});
    lastDatasets = [{label:'Oil',data:oilD},{label:'Gas',data:gasD}];
    setChartTitle(`${companies.length} companies · Oil & Gas production`, `${companies.join(', ')} · boe/day`);
    setInfo(`<strong>Companies:</strong> ${companies.join(', ')} &nbsp;|&nbsp; <strong>Series:</strong> Oil, Gas (summed) &nbsp;|&nbsp; <strong>Unit:</strong> boe/day &nbsp;|&nbsp; <strong>Basis:</strong> Equity-adjusted, all fields`);
  }"""


def main():
    with open(PATH, encoding='utf-8') as fh:
        text = fh.read()

    if NEW in text:
        print("allerede anvendt - hopper over")
        return 0

    n = text.count(OLD)
    if n == 0:
        print("FEIL: fant ingen treff for gammel kode", file=sys.stderr)
        return 1
    if n > 1:
        print(f"FEIL: traff {n} steder (ma vaere unik)", file=sys.stderr)
        return 1

    text = text.replace(OLD, NEW)
    with open(PATH, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f"ok - {PATH} er na {len(text.encode('utf-8'))} bytes")
    return 0


if __name__ == '__main__':
    sys.exit(main())
