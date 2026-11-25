"""
Bygger data/coastline.json — en kraftig forenklet kystlinje som profilkartet
tegner som bakgrunn, slik at man ser hvor pa sokkelen feltet ligger.

ENGANGSSCRIPT, ikke en del av den daglige pipelinen. Kystlinjer endrer seg
ikke, og kilden er ikke Sodir.

Kilden er Natural Earth 10m (public domain), via TopoJSON-bakgrunnskartet i
sosterrepoet sheffielddivided/map (data/basemap.json), som ble bygget en gang
med mapshaper. Scriptet dekoder land-objektet, klipper til vinduet norsk
sokkel ligger i, og forenkler til ~6 km nojaktighet — kartet er en miniatyr,
sa mer detalj er bortkastede byte.

    python make_coastline.py ../map/data/basemap.json

Med --box og --out lages andre utsnitt, f.eks. Newfoundland til canada.html
fra world-atlas (Natural Earth 10m i samme TopoJSON-format):

    python make_coastline.py land-10m.json --box=-68,40,-40,61 \\
        --out data/canada_nl_coast.json --source "Natural Earth 10m via world-atlas"

Ringer som går ut av vinduet klippes mot kanten, så store landmasser
(Nord-Amerika) ikke tas med i sin helhet.
"""

import argparse
import json
import math
import os
import sys

OUT_FILE  = 'data/coastline.json'
BOX       = (-8.0, 52.0, 36.0, 75.0)   # vest, sor, ost, nord
TOLERANCE = 0.05                        # grader, ~6 km
DECIMALS  = 3                           # ~110 m, langt under toleransen
MIN_POINTS = 6                          # dropp smaoyer som blir til ingenting


def decode_arcs(topo):
    sx, sy = topo['transform']['scale']
    tx, ty = topo['transform']['translate']
    arcs = []
    for a in topo['arcs']:
        x = y = 0
        pts = []
        for dx, dy in a:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def rings_of(geom, arcs):
    polys = geom['arcs'] if geom['type'] == 'MultiPolygon' else [geom['arcs']]
    out = []
    for poly in polys:
        for ring in poly:
            pts = []
            for i in ring:
                seg = arcs[~i][::-1] if i < 0 else arcs[i]
                pts += seg[1:] if pts else list(seg)
            out.append(pts)
    return out


def simplify(points, tol):
    """Douglas-Peucker, iterativt — ringene er for lange for rekursjon."""
    if len(points) < 3:
        return list(points)

    def dist(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy)
        if n < 1e-12:
            return math.hypot(x - x1, y - y1)
        return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / n

    keep, stack = {0, len(points) - 1}, [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        best, bi = -1.0, a
        for i in range(a + 1, b):
            d = dist(points[i], points[a], points[b])
            if d > best:
                best, bi = d, i
        if best > tol:
            keep.add(bi)
            stack.append((a, bi))
            stack.append((bi, b))
    return [points[i] for i in sorted(keep)]


def clip(ring, box):
    """Klipper en ring mot vinduet (Sutherland-Hodgman). Biter av kysten
    langs kanten blir rette linjer, som ikke synes når kartet viser vinduet."""
    w, s, e, n = box
    edges = [
        (lambda p: p[0] >= w, lambda a, b: _cut_x(a, b, w)),
        (lambda p: p[0] <= e, lambda a, b: _cut_x(a, b, e)),
        (lambda p: p[1] >= s, lambda a, b: _cut_y(a, b, s)),
        (lambda p: p[1] <= n, lambda a, b: _cut_y(a, b, n)),
    ]
    pts = list(ring)
    for inside, cut in edges:
        if not pts:
            break
        out = []
        prev = pts[-1]
        for p in pts:
            if inside(p):
                if not inside(prev):
                    out.append(cut(prev, p))
                out.append(p)
            elif inside(prev):
                out.append(cut(prev, p))
            prev = p
        pts = out
    return pts


def _cut_x(a, b, x):
    t = (x - a[0]) / (b[0] - a[0])
    return (x, a[1] + t * (b[1] - a[1]))


def _cut_y(a, b, y):
    t = (y - a[1]) / (b[1] - a[1])
    return (a[0] + t * (b[0] - a[0]), y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', nargs='?', default='../map/data/basemap.json')
    ap.add_argument('--box', help='vest,sor,ost,nord (standard: norsk sokkel)')
    ap.add_argument('--out', default=OUT_FILE)
    ap.add_argument('--source', default='Natural Earth 10m (public domain), via sheffielddivided/map')
    args = ap.parse_args()
    src = args.src
    box = tuple(float(v) for v in args.box.split(',')) if args.box else BOX
    out_file = args.out
    if not os.path.exists(src):
        print(f"Finner ikke {src}. Angi stien til basemap.json fra sheffielddivided/map.",
              file=sys.stderr)
        return 1

    with open(src, encoding='utf-8') as fh:
        topo = json.load(fh)
    arcs = decode_arcs(topo)

    w, s, e, n = box
    out = []
    for geom in topo['objects']['land']['geometries']:
        for ring in rings_of(geom, arcs):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            if max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n:
                continue
            if args.box:
                ring = clip(ring, box)
                if len(ring) < 3:
                    continue
            simp = simplify(ring, TOLERANCE)
            if len(simp) < MIN_POINTS:
                continue
            out.append([[round(x, DECIMALS), round(y, DECIMALS)] for x, y in simp])

    payload = {
        'source': args.source,
        'box': list(box),
        'tolerance': TOLERANCE,
        'rings': out,
    }
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        fh.write('\n')
    pts = sum(len(r) for r in out)
    size = os.path.getsize(out_file) / 1024
    print(f"Skrev {out_file}: {len(out)} ringer, {pts} punkter, {size:.0f} KB")
    return 0


if __name__ == '__main__':
    sys.exit(main())
