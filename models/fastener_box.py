#!/usr/bin/env python3
"""
Параметрическая коробка для хранения метизов (винты, гайки, шайбы и т.д.).
Печатается плашмя, без поддержек. Рассчитана на ANYCUBIC Kobra S1
(рабочая область печати 250x250x250 мм).

Использование:
    python3 fastener_box.py [опции]
    python3 fastener_box.py --help

Параметры можно задавать через CLI либо менять значения по умолчанию
в DEFAULTS ниже и импортировать build_fastener_box() из других скриптов.
"""

import argparse
import sys

import cadquery as cq

DEFAULTS = dict(
    box_width=190.0,      # ширина коробки (X), мм
    box_length=130.0,     # длина коробки (Y), мм
    box_height=30.0,      # глубина/высота стенок коробки (Z), мм
    wall_thickness=2.0,   # толщина внешних стенок, дна и перегородок, мм
    num_cols=4,           # число колонок отсеков (по X)
    num_rows=6,           # число строк отсеков (по Y)
    corner_radius=3.0,    # радиус скругления внутренних углов отсеков, мм
)

# Минимальный зазор, на который "пробойник" отсека выступает выше/ниже
# основного тела при булевом вычитании — исключает артефакты совпадающих
# поверхностей.
CUT_OVERSHOOT = 1.0


def _rounded_pocket(width: float, length: float, height: float, radius: float) -> cq.Workplane:
    """Прямоугольный "стакан" width x length x height со скруглёнными
    вертикальными рёбрами — заготовка для вычитания одного отсека."""
    r = max(0.0, min(radius, width / 2 - 0.01, length / 2 - 0.01))
    pocket = cq.Workplane("XY").rect(width, length).extrude(height)
    if r > 0:
        pocket = pocket.edges("|Z").fillet(r)
    return pocket


def _compute_sizes(count: int, total_span: float, divider: float, sizes=None):
    """Возвращает список размеров (мм) для `count` колонок/строк,
    занимающих `total_span` мм с перегородками толщиной `divider` между
    ними. Если `sizes` не задан — все ячейки одинакового размера."""
    if count < 1:
        raise ValueError("Количество колонок/строк должно быть не меньше 1")
    usable_span = total_span - divider * (count - 1)
    if usable_span <= 0:
        raise ValueError(
            f"Недостаточно места: {count} ячеек с перегородками {divider} мм "
            f"не помещаются в {total_span} мм"
        )
    if sizes is None:
        each = usable_span / count
        return [each] * count
    if len(sizes) != count:
        raise ValueError(f"Ожидалось {count} значений размеров, получено {len(sizes)}")
    if any(s <= 0 for s in sizes):
        raise ValueError("Все размеры колонок/строк должны быть положительными")
    if abs(sum(sizes) - usable_span) > 1e-6:
        raise ValueError(
            f"Сумма размеров ({sum(sizes):.3f} мм) должна равняться "
            f"доступному пространству ({usable_span:.3f} мм) = "
            f"{total_span} - {divider}*({count}-1)"
        )
    return list(sizes)


def build_fastener_box(
    box_width: float = DEFAULTS["box_width"],
    box_length: float = DEFAULTS["box_length"],
    box_height: float = DEFAULTS["box_height"],
    wall_thickness: float = DEFAULTS["wall_thickness"],
    num_cols: int = DEFAULTS["num_cols"],
    num_rows: int = DEFAULTS["num_rows"],
    corner_radius: float = DEFAULTS["corner_radius"],
    col_sizes=None,
    row_sizes=None,
) -> cq.Workplane:
    """Строит коробку-органайзер с решёткой отсеков.

    box_width, box_length, box_height — внешние габариты коробки, мм.
    wall_thickness — толщина внешних стенок, дна и перегородок, мм.
    num_cols, num_rows — число колонок (по X) и строк (по Y) отсеков.
    corner_radius — радиус скругления внутренних вертикальных углов
        каждого отсека, мм.
    col_sizes, row_sizes — необязательные списки ширины каждой колонки /
        длины каждой строки (мм), если нужны отсеки разного размера.
        Сумма значений + перегородки должна точно заполнять внутреннее
        пространство. По умолчанию (None) — отсеки равного размера.
    """
    if box_width <= 2 * wall_thickness or box_length <= 2 * wall_thickness:
        raise ValueError("Габариты коробки слишком малы относительно толщины стенок")
    if box_height <= wall_thickness:
        raise ValueError("Высота коробки должна быть больше толщины дна")

    inner_width = box_width - 2 * wall_thickness
    inner_length = box_length - 2 * wall_thickness

    col_widths = _compute_sizes(num_cols, inner_width, wall_thickness, col_sizes)
    row_lengths = _compute_sizes(num_rows, inner_length, wall_thickness, row_sizes)

    body = cq.Workplane("XY").box(
        box_width, box_length, box_height, centered=(True, True, False)
    )

    pocket_height = (box_height - wall_thickness) + CUT_OVERSHOOT
    x0 = -box_width / 2 + wall_thickness
    y0 = -box_length / 2 + wall_thickness

    cx = x0
    for w in col_widths:
        cy = y0
        for l in row_lengths:
            pocket = _rounded_pocket(w, l, pocket_height, corner_radius)
            pocket = pocket.translate((cx + w / 2, cy + l / 2, wall_thickness))
            body = body.cut(pocket)
            cy += l + wall_thickness
        cx += w + wall_thickness

    return body


def _parse_float_list(text):
    if text is None:
        return None
    return [float(v) for v in text.split(",") if v.strip() != ""]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Генератор параметрической коробки для метизов")
    parser.add_argument("--width", type=float, default=DEFAULTS["box_width"], help="ширина коробки, мм")
    parser.add_argument("--length", type=float, default=DEFAULTS["box_length"], help="длина коробки, мм")
    parser.add_argument("--height", type=float, default=DEFAULTS["box_height"], help="высота (глубина) коробки, мм")
    parser.add_argument("--wall", type=float, default=DEFAULTS["wall_thickness"], help="толщина стенок/дна/перегородок, мм")
    parser.add_argument("--cols", type=int, default=DEFAULTS["num_cols"], help="число колонок отсеков")
    parser.add_argument("--rows", type=int, default=DEFAULTS["num_rows"], help="число строк отсеков")
    parser.add_argument("--radius", type=float, default=DEFAULTS["corner_radius"], help="радиус скругления углов отсеков, мм")
    parser.add_argument("--col-sizes", type=str, default=None, help="список ширин колонок через запятую, мм (переопределяет равномерное деление)")
    parser.add_argument("--row-sizes", type=str, default=None, help="список длин строк через запятую, мм (переопределяет равномерное деление)")
    parser.add_argument("--out", type=str, default="stl/fastener_box.stl", help="путь к выходному STL файлу")
    parser.add_argument("--step", type=str, default=None, help="дополнительно сохранить STEP файл по указанному пути")
    args = parser.parse_args(argv)

    model = build_fastener_box(
        box_width=args.width,
        box_length=args.length,
        box_height=args.height,
        wall_thickness=args.wall,
        num_cols=args.cols,
        num_rows=args.rows,
        corner_radius=args.radius,
        col_sizes=_parse_float_list(args.col_sizes),
        row_sizes=_parse_float_list(args.row_sizes),
    )

    cq.exporters.export(model, args.out)
    print(f"STL сохранён: {args.out}")

    if args.step:
        cq.exporters.export(model, args.step)
        print(f"STEP сохранён: {args.step}")


if __name__ == "__main__":
    sys.exit(main())
