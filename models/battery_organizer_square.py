#!/usr/bin/env python3
"""
Органайзер для батареек AA, AAA и плоских («таблеток») — вариант с квадратными
ячейками, только нижняя часть (без крышки). Ячейки идут сплошной сеткой: общие
стенки, никаких пустот между ячейками, сплошное дно. Размеры ячеек подгоняются,
чтобы зоны AA / AAA / «таблетки» ровно заполнили прямоугольник коробки.

Использование:
    python3 battery_organizer_square.py [--aa-cols 8 --aa-rows 5 --aaa-cols 4 --aaa-rows 7]
"""

import argparse

import cadquery as cq
import trimesh

DEFAULTS = dict(
    aa_diameter=14.5, aaa_diameter=10.5, clearance=0.5,
    wall=0.8, floor=1.0,
    height=26.0, aaa_height=24.0, coin_height=18.0,
    aa_cols=8, aa_rows=5, aaa_cols=4, aaa_rows=7,
    coin_diameters=(20.0,) * 6 + (24.5,) * 2,
    corner_radius=1.0,
)


def _line(sizes, start, wall):
    """Ячейки подряд с общими стенками: [(начало, размер)], конец последней."""
    out, x = [], start
    for s in sizes:
        out.append((x, s))
        x += s + wall
    return out, x - wall


def build_organizer(**kw):
    p = {**DEFAULTS, **kw}
    wall, fl, H, R = p["wall"], p["floor"], p["height"], p["corner_radius"]
    c = p["clearance"]
    sa, sd = p["aa_diameter"] + c, p["aaa_diameter"] + c
    coins = [d + c for d in p["coin_diameters"]]
    na, nd = p["aa_cols"], p["aaa_cols"]

    # --- ширина: внутренняя, выравниваем зоны по X ---
    nat_batt = na * sa + nd * sd + (na + nd - 1) * wall + wall   # + стенка между зонами
    nat_coin = sum(coins) + (len(coins) - 1) * wall
    inner_w = max(nat_batt, nat_coin)
    kb = (inner_w - nat_batt) / (na + nd)          # прибавка к ширине ячеек батареек
    kc = (inner_w - nat_coin) / len(coins)         # прибавка к ширине «таблеток»

    # --- глубина: выравниваем AA и AAA по Y ---
    nat_a = p["aa_rows"] * sa + (p["aa_rows"] - 1) * wall
    nat_d = p["aaa_rows"] * sd + (p["aaa_rows"] - 1) * wall
    batt_d = max(nat_a, nat_d)
    ya = (batt_d - (p["aa_rows"] - 1) * wall) / p["aa_rows"]    # реальная глубина ячейки AA
    yd = (batt_d - (p["aaa_rows"] - 1) * wall) / p["aaa_rows"]
    coin_d = max(coins)
    inner_d = coin_d + wall + batt_d
    W, D = inner_w + 2 * wall, inner_d + 2 * wall

    zones = []   # (ячейки [(x, y, w, d)], высота)
    xs_c, _ = _line([d + kc for d in coins], wall, wall)
    zones.append(([(x, wall, w, coin_d) for x, w in xs_c], p["coin_height"]))
    y_b = wall + coin_d + wall
    xs_a, end_a = _line([sa + kb] * na, wall, wall)
    xs_d, _ = _line([sd + kb] * nd, end_a + wall, wall)
    ys_a, _ = _line([ya] * p["aa_rows"], y_b, wall)
    ys_d, _ = _line([yd] * p["aaa_rows"], y_b, wall)
    zones.append(([(x, y, w, h) for x, w in xs_a for y, h in ys_a], H))
    zones.append(([(x, y, w, h) for x, w in xs_d for y, h in ys_d], p["aaa_height"]))

    shell = (cq.Workplane("XY").rect(W, D).extrude(H)
             .cut(cq.Workplane("XY").rect(W - 2 * wall, D - 2 * wall).extrude(H))
             .translate((W / 2, D / 2, 0)))
    solid = shell
    for cells, h in zones:
        x0 = min(x for x, _, _, _ in cells) - wall
        x1 = max(x + w for x, _, w, _ in cells) + wall
        y0 = min(y for _, y, _, _ in cells) - wall
        y1 = max(y + d for _, y, _, d in cells) + wall
        solid = solid.union(cq.Workplane("XY").center((x0 + x1) / 2, (y0 + y1) / 2)
                            .rect(x1 - x0, y1 - y0).extrude(h))
    n = 0
    for cells, h in zones:
        for x, y, w, d in cells:
            solid = solid.cut(cq.Workplane("XY").workplane(offset=fl)
                              .center(x + w / 2, y + d / 2).rect(w, d).extrude(h)
                              .edges("|Z").fillet(R))
            n += 1
    return solid, n, (W, D, H)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aa-cols", type=int, default=DEFAULTS["aa_cols"])
    ap.add_argument("--aa-rows", type=int, default=DEFAULTS["aa_rows"])
    ap.add_argument("--aaa-cols", type=int, default=DEFAULTS["aaa_cols"])
    ap.add_argument("--aaa-rows", type=int, default=DEFAULTS["aaa_rows"])
    ap.add_argument("--height", type=float, default=DEFAULTS["height"])
    ap.add_argument("--wall", type=float, default=DEFAULTS["wall"])
    ap.add_argument("--out", default="stl/battery_organizer_square.stl")
    a = ap.parse_args()
    body, n, (W, D, H) = build_organizer(aa_cols=a.aa_cols, aa_rows=a.aa_rows,
                                         aaa_cols=a.aaa_cols, aaa_rows=a.aaa_rows,
                                         height=a.height, wall=a.wall)
    cq.exporters.export(body, a.out, tolerance=0.01, angularTolerance=0.1)
    m = trimesh.load(a.out)
    print(f"{a.out}: {W:.1f} x {D:.1f} x {H:.1f} мм, ячеек: {n}, watertight={m.is_watertight}, "
          f"объём={m.volume/1000:.1f} см3")


if __name__ == "__main__":
    main()
