#!/usr/bin/env python3
"""
Органайзер для батареек AA и AAA — только нижняя часть (без крышки).
Батарейки стоят вертикально в круглых гнёздах, уложенных «сотами»
(гексагональная упаковка). Левая зона — AA, правая — AAA. Гнёзда глубиной
в половину батарейки — удобно брать пальцами. Стенки тонкие (по умолчанию
1 мм, соседние гнёзда делят общую стенку), печатается без поддержек.

Использование:
    python3 battery_organizer.py [--aa-cols 6 --aa-rows 5 --aaa-cols 5 --aaa-rows 7]
"""

import argparse
import math

import cadquery as cq
import trimesh

DEFAULTS = dict(
    aa_diameter=14.5, aaa_diameter=10.5,   # диаметры батареек, мм
    clearance=0.5,                          # зазор гнезда (на диаметр), мм
    wall=1.0,                               # стенка между гнёздами и внешняя стенка, мм
    floor=1.2,                              # толщина дна, мм
    height=26.0,                            # полная высота короба, мм
    aa_cols=6, aa_rows=5,
    aaa_cols=5, aaa_rows=7,
)

_HEX = math.sqrt(3) / 2


def _zone(d, cols, rows, wall):
    """Гнездо диаметром d; шаг = d + wall (стенки соседних колец сливаются)."""
    pitch = d + wall
    w = (cols + 0.5) * pitch + wall
    h = (rows - 1) * pitch * _HEX + pitch + wall
    centers = [(wall / 2 + pitch / 2 + c * pitch + (pitch / 2 if r % 2 else 0),
                wall / 2 + pitch / 2 + r * pitch * _HEX)
               for r in range(rows) for c in range(cols)]
    return w, h, centers


def build_organizer(**kw):
    p = {**DEFAULTS, **kw}
    wall, fl, H = p["wall"], p["floor"], p["height"]
    da = p["aa_diameter"] + p["clearance"]
    dd = p["aaa_diameter"] + p["clearance"]
    wa, ha, ca = _zone(da, p["aa_cols"], p["aa_rows"], wall)
    wd, hd, cd = _zone(dd, p["aaa_cols"], p["aaa_rows"], wall)
    W = wa + wd - wall          # зоны делят общую стенку
    D = max(ha, hd)
    # внешняя стенка шире на wall со всех сторон -> учтено в _zone (wall/2 с каждой стороны)
    pts = [(x, y, da) for x, y in ca] + [(x + wa - wall, y, dd) for x, y in cd]

    body = cq.Workplane("XY").box(W, D, H, centered=False)
    cut = None
    for x, y, d in pts:
        c = (cq.Workplane("XY").workplane(offset=fl).center(x, y)
             .circle(d / 2).extrude(H - fl))
        cut = c if cut is None else cut.union(c)
    # всё, что вне колец вокруг гнёзд, выбираем (пустоты между кольцами) — тонкие стенки
    voids = (cq.Workplane("XY").workplane(offset=fl).center(W / 2, D / 2)
             .rect(W - 2 * wall, D - 2 * wall).extrude(H - fl))
    rings = None
    for x, y, d in pts:
        r = (cq.Workplane("XY").workplane(offset=fl).center(x, y)
             .circle(d / 2 + wall).extrude(H - fl))
        rings = r if rings is None else rings.union(r)
    voids = voids.cut(rings)
    return body.cut(cut).cut(voids), len(pts), (W, D, H)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aa-cols", type=int, default=DEFAULTS["aa_cols"])
    ap.add_argument("--aa-rows", type=int, default=DEFAULTS["aa_rows"])
    ap.add_argument("--aaa-cols", type=int, default=DEFAULTS["aaa_cols"])
    ap.add_argument("--aaa-rows", type=int, default=DEFAULTS["aaa_rows"])
    ap.add_argument("--height", type=float, default=DEFAULTS["height"])
    ap.add_argument("--wall", type=float, default=DEFAULTS["wall"])
    ap.add_argument("--out", default="stl/battery_organizer.stl")
    a = ap.parse_args()
    body, n, (W, D, H) = build_organizer(aa_cols=a.aa_cols, aa_rows=a.aa_rows,
                                         aaa_cols=a.aaa_cols, aaa_rows=a.aaa_rows,
                                         height=a.height, wall=a.wall)
    cq.exporters.export(body, a.out, tolerance=0.01, angularTolerance=0.1)
    m = trimesh.load(a.out)
    print(f"{a.out}: {W:.1f} x {D:.1f} x {H:.1f} мм, гнёзд: {n}, watertight={m.is_watertight}, "
          f"объём={m.volume/1000:.1f} см3")


if __name__ == "__main__":
    main()
