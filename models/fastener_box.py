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
    num_rows=4,            # число строк отсеков (по Y, короткая сторона)
    row_cols=6,             # число колонок в каждой строке (по X); int — одинаково
                             # для всех строк, либо список длиной num_rows —
                             # своё число колонок для каждой строки
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


def _normalize_row_cols(num_rows: int, row_cols):
    """row_cols может быть одним int (одинаковое число колонок во всех
    строках) либо списком длиной num_rows со своим числом колонок для
    каждой строки."""
    if isinstance(row_cols, int):
        if row_cols < 1:
            raise ValueError("Число колонок в строке должно быть не меньше 1")
        return [row_cols] * num_rows
    row_cols = list(row_cols)
    if len(row_cols) != num_rows:
        raise ValueError(f"row_cols должен содержать {num_rows} значений, получено {len(row_cols)}")
    if any(c < 1 for c in row_cols):
        raise ValueError("Число колонок в каждой строке должно быть не меньше 1")
    return row_cols


def build_fastener_box(
    box_width: float = DEFAULTS["box_width"],
    box_length: float = DEFAULTS["box_length"],
    box_height: float = DEFAULTS["box_height"],
    wall_thickness: float = DEFAULTS["wall_thickness"],
    num_rows: int = DEFAULTS["num_rows"],
    row_cols=DEFAULTS["row_cols"],
    corner_radius: float = DEFAULTS["corner_radius"],
    row_sizes=None,
    row_col_sizes=None,
) -> cq.Workplane:
    """Строит коробку-органайзер с решёткой отсеков.

    box_width, box_length, box_height — внешние габариты коробки, мм.
    wall_thickness — толщина внешних стенок, дна и перегородок, мм.
    num_rows — число строк отсеков (по Y).
    row_cols — число колонок (по X) в каждой строке: одно число (int) —
        одинаковое для всех строк, либо список длиной num_rows — своё
        число колонок в каждой строке (строки могут отличаться).
    corner_radius — радиус скругления внутренних вертикальных углов
        каждого отсека, мм.
    row_sizes — необязательный список длины каждой строки (мм, по Y).
        По умолчанию (None) — строки одинаковой длины.
    row_col_sizes — необязательный список длиной num_rows, каждый элемент —
        либо None (колонки этой строки равного размера), либо список
        ширин колонок этой строки (мм). Сумма ширин + перегородки должны
        точно заполнять внутреннюю ширину коробки.
    """
    if box_width <= 2 * wall_thickness or box_length <= 2 * wall_thickness:
        raise ValueError("Габариты коробки слишком малы относительно толщины стенок")
    if box_height <= wall_thickness:
        raise ValueError("Высота коробки должна быть больше толщины дна")

    inner_width = box_width - 2 * wall_thickness
    inner_length = box_length - 2 * wall_thickness

    row_cols_list = _normalize_row_cols(num_rows, row_cols)
    row_lengths = _compute_sizes(num_rows, inner_length, wall_thickness, row_sizes)

    if row_col_sizes is None:
        row_col_sizes = [None] * num_rows
    elif len(row_col_sizes) != num_rows:
        raise ValueError(f"row_col_sizes должен содержать {num_rows} значений, получено {len(row_col_sizes)}")

    row_col_widths = [
        _compute_sizes(row_cols_list[i], inner_width, wall_thickness, row_col_sizes[i])
        for i in range(num_rows)
    ]

    body = cq.Workplane("XY").box(
        box_width, box_length, box_height, centered=(True, True, False)
    )

    pocket_height = (box_height - wall_thickness) + CUT_OVERSHOOT
    x0 = -box_width / 2 + wall_thickness
    y0 = -box_length / 2 + wall_thickness

    cy = y0
    for row_idx, row_length in enumerate(row_lengths):
        cx = x0
        for w in row_col_widths[row_idx]:
            pocket = _rounded_pocket(w, row_length, pocket_height, corner_radius)
            pocket = pocket.translate((cx + w / 2, cy + row_length / 2, wall_thickness))
            body = body.cut(pocket)
            cx += w + wall_thickness
        cy += row_length + wall_thickness

    return body


def _parse_float_list(text):
    if text is None:
        return None
    return [float(v) for v in text.split(",") if v.strip() != ""]


def _parse_int_list(text):
    if text is None:
        return None
    return [int(v) for v in text.split(",") if v.strip() != ""]


def _parse_row_col_sizes(text, num_rows):
    """Разбирает строку вида "60,60,60|30,30,30,30,30,30|...": строки
    разделены '|', внутри строки ширины колонок через запятую. Пустая
    группа между '|' означает "равномерное деление" для этой строки."""
    if text is None:
        return None
    groups = text.split("|")
    if len(groups) != num_rows:
        raise ValueError(f"--row-col-sizes должен содержать {num_rows} групп через '|', получено {len(groups)}")
    result = []
    for g in groups:
        g = g.strip()
        result.append(None if g == "" else [float(v) for v in g.split(",")])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Генератор параметрической коробки для метизов")
    parser.add_argument("--width", type=float, default=DEFAULTS["box_width"], help="ширина коробки, мм")
    parser.add_argument("--length", type=float, default=DEFAULTS["box_length"], help="длина коробки, мм")
    parser.add_argument("--height", type=float, default=DEFAULTS["box_height"], help="высота (глубина) коробки, мм")
    parser.add_argument("--wall", type=float, default=DEFAULTS["wall_thickness"], help="толщина стенок/дна/перегородок, мм")
    parser.add_argument("--rows", type=int, default=DEFAULTS["num_rows"], help="число строк отсеков")
    parser.add_argument("--cols", type=int, default=DEFAULTS["row_cols"], help="число колонок в каждой строке (одинаковое для всех строк)")
    parser.add_argument("--row-cols", type=str, default=None, help="число колонок для каждой строки через запятую, напр. 3,6,6,6 (переопределяет --cols)")
    parser.add_argument("--radius", type=float, default=DEFAULTS["corner_radius"], help="радиус скругления углов отсеков, мм")
    parser.add_argument("--row-sizes", type=str, default=None, help="список длин строк через запятую, мм (переопределяет равномерное деление)")
    parser.add_argument("--row-col-sizes", type=str, default=None, help="ширины колонок по строкам: группы через '|', внутри группы через запятую, напр. '60,60,60|30,30,30,30,30,30|...'")
    parser.add_argument("--out", type=str, default="stl/fastener_box.stl", help="путь к выходному STL файлу")
    parser.add_argument("--step", type=str, default=None, help="дополнительно сохранить STEP файл по указанному пути")
    args = parser.parse_args(argv)

    row_cols = _parse_int_list(args.row_cols) if args.row_cols is not None else args.cols

    model = build_fastener_box(
        box_width=args.width,
        box_length=args.length,
        box_height=args.height,
        wall_thickness=args.wall,
        num_rows=args.rows,
        row_cols=row_cols,
        corner_radius=args.radius,
        row_sizes=_parse_float_list(args.row_sizes),
        row_col_sizes=_parse_row_col_sizes(args.row_col_sizes, args.rows),
    )

    cq.exporters.export(model, args.out)
    print(f"STL сохранён: {args.out}")

    if args.step:
        cq.exporters.export(model, args.step)
        print(f"STEP сохранён: {args.step}")


if __name__ == "__main__":
    sys.exit(main())
