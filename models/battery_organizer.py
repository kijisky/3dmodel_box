#!/usr/bin/env python3
"""
Органайзер для батареек AA и AAA — только нижняя часть (без крышки).
Батарейки стоят вертикально в круглых гнёздах, уложенных «сотами»
(гексагональная упаковка). Левая зона — AA, правая — AAA. Гнёзда глубиной
в половину батарейки — удобно брать пальцами. Стенки тонкие (по умолчанию
1 мм, соседние гнёзда делят общую стенку), печатается без поддержек.

Использование:
    python3 battery_organizer.py [--aa-cols 8 --aa-rows 5 --aaa-cols 3 --aaa-rows 7]
"""

import argparse
import math

import cadquery as cq
import trimesh

DEFAULTS = dict(
    aa_diameter=14.5, aaa_diameter=10.5,   # диаметры батареек, мм
    clearance=0.5,                          # зазор гнезда (на диаметр), мм
    wall=1.2,                               # стенка (3 периметра сопла 0.4), мм
    floor=1.2,                              # толщина дна под гнёздами, мм
    height=26.0,                            # высота внешней стенки и AA-гнёзд, мм
    aaa_height=24.0,                        # высота колец AAA (батарейка короче), мм
    coin_height=18.0,                       # высота колец под «таблетки» (~5 шт. CR2032), мм
    aa_cols=8, aa_rows=5,
    aaa_cols=4, aaa_rows=7,
    # диаметры гнёзд для плоских батареек: CR2016/2025/2032 = 20 мм, CR2450 = 24.5 мм
    coin_diameters=(20.0,) * 6 + (24.5,) * 2,
)

_HEX = math.sqrt(3) / 2


def _zone(d, cols, rows, wall):
    """Гексагональная зона гнёзд диаметром d; шаг = d + wall (кольца делят стенку)."""
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
    c = p["clearance"]
    da, dd = p["aa_diameter"] + c, p["aaa_diameter"] + c
    coins = [d + c for d in p["coin_diameters"]]
    wa, ha, ca = _zone(da, p["aa_cols"], p["aa_rows"], wall)
    wd, hd, cd = _zone(dd, p["aaa_cols"], p["aaa_rows"], wall)

    # полоса «таблеток» вдоль переднего края (y = 0), далее батарейки
    coin_w = sum(d + wall for d in coins) + wall
    strip_h = max(coins) + 2 * wall
    y0 = strip_h - wall
    W = max(wa + wd - wall, coin_w)
    D = y0 + max(ha, hd)

    pts = []   # (x, y, диаметр гнезда, высота кольца)
    x = wall
    for d in coins:
        pts.append((x + (d + wall) / 2 - wall / 2 + wall / 2, strip_h / 2, d, p["coin_height"]))
        x += d + wall
    pts += [(cx, cy + y0, da, H) for cx, cy in ca]
    pts += [(cx + wa - wall, cy + y0, dd, p["aaa_height"]) for cx, cy in cd]

    # внешняя стенка (полная высота)
    outer = cq.Workplane("XY").box(W, D, H, centered=False)
    inner = (cq.Workplane("XY").center(W / 2, D / 2).rect(W - 2 * wall, D - 2 * wall).extrude(H))
    solid = outer.cut(inner)
    # кольца гнёзд; щели между кольцами остаются сквозными -> меньше пластика/времени
    for x, y, d, h in pts:
        ring = cq.Workplane("XY").center(x, y).circle(d / 2 + wall).extrude(h)
        solid = solid.union(ring)
    for x, y, d, h in pts:
        solid = solid.cut(cq.Workplane("XY").workplane(offset=fl).center(x, y)
                          .circle(d / 2).extrude(h))
    return solid, pts, (W, D, H)


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
    body, pts, (W, D, H) = build_organizer(aa_cols=a.aa_cols, aa_rows=a.aa_rows,
                                         aaa_cols=a.aaa_cols, aaa_rows=a.aaa_rows,
                                         height=a.height, wall=a.wall)
    cq.exporters.export(body, a.out, tolerance=0.01, angularTolerance=0.1)
    m = trimesh.load(a.out)
    print(f"{a.out}: {W:.1f} x {D:.1f} x {H:.1f} мм, гнёзд: {len(pts)}, watertight={m.is_watertight}, "
          f"объём={m.volume/1000:.1f} см3")


if __name__ == "__main__":
    main()
