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
"""

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


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else '../map/data/basemap.json'
    if not os.path.exists(src):
        print(f"Finner ikke {src}. Angi stien til basemap.json fra sheffielddivided/map.",
              file=sys.stderr)
        return 1

    with open(src, encoding='utf-8') as fh:
        topo = json.load(fh)
    arcs = decode_arcs(topo)

    w, s, e, n = BOX
    out = []
    for geom in topo['objects']['land']['geometries']:
        for ring in rings_of(geom, arcs):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            if max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n:
                continue
            simp = simplify(ring, TOLERANCE)
            if len(simp) < MIN_POINTS:
                continue
            out.append([[round(x, DECIMALS), round(y, DECIMALS)] for x, y in simp])

    payload = {
        'source': 'Natural Earth 10m (public domain), via sheffielddivided/map',
        'box': list(BOX),
        'tolerance': TOLERANCE,
        'rings': out,
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        fh.write('\n')
    pts = sum(len(r) for r in out)
    size = os.path.getsize(OUT_FILE) / 1024
    print(f"Skrev {OUT_FILE}: {len(out)} ringer, {pts} punkter, {size:.0f} KB")
    return 0


if __name__ == '__main__':
    sys.exit(main())
