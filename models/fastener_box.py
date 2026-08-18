#!/usr/bin/env python3
"""
Параметрическая коробка для хранения метизов (винты, гайки, шайбы и т.д.),
с крышкой на защёлках и стекируемостью. Печатается плашмя, без поддержек.
Рассчитана на ANYCUBIC Kobra S1 (рабочая область печати 250x250x250 мм).

Использование:
    python3 fastener_box.py [опции]
    python3 fastener_box.py --help

По умолчанию генерируются два STL: основание (короб с отсеками) и крышка.
Крышка надевается сверху на короб и защёлкивается на небольших выступах
("snap"-соединение). На верхней стороне крышки — приподнятый бортик по
размеру основания короба: следующий короб, поставленный сверху, своим
дном садится внутрь этого бортика и не соскальзывает — так короба
стекируются друг на друга.

Параметры можно задавать через CLI либо менять значения по умолчанию
в DEFAULTS ниже и импортировать build_fastener_box()/build_lid() из
других скриптов.
"""

import argparse
import sys

import cadquery as cq
import trimesh

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

    # --- крышка ---
    lid_thickness=2.0,      # толщина верхней панели крышки, мм
    skirt_depth=6.0,        # на сколько мм юбка крышки перекрывает стенку короба
    fit_clearance=0.2,      # зазор между юбкой крышки и стенкой короба (на сторону), мм
    snap_enabled=True,      # добавлять ли защёлки (выступ на коробе / углубление в крышке)
    snap_diameter=3.0,      # диаметр защёлки-купола, мм
    snap_height=0.5,        # выступ защёлки над стенкой, мм
    snap_per_long_side=1,   # число защёлок на каждой длинной стенке (по X)
    snap_per_short_side=0,  # число защёлок на каждой короткой стенке (по Y)

    # --- стекирование ---
    stacking_enabled=True,  # добавлять ли на крышке бортик для стекирования
    stack_clearance=0.25,   # зазор между бортиком и дном следующего короба (на сторону), мм
    lip_height=2.5,          # высота бортика для стекирования, мм
    lip_wall_thickness=1.5,  # толщина стенки бортика, мм
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
    """Строит коробку-органайзер с решёткой отсеков (без крышки).

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


# ---------------------------------------------------------------------------
# Купол защёлки (сферический сегмент): выступ на стенке короба и, зеркально
# с чуть большим зазором, углубление на юбке крышки, в которое он входит со
# щелчком.
# ---------------------------------------------------------------------------

def _dome_sphere(diameter: float, height: float) -> cq.Workplane:
    """Сфера, сегмент которой (при пересечении плоской гранью) даёт купол
    заданного диаметра основания и высоты выступа."""
    if height <= 0 or diameter <= 0:
        raise ValueError("Диаметр и высота купола защёлки должны быть положительными")
    radius = (height / 2) + (diameter ** 2) / (8 * height)
    return cq.Workplane("XY").sphere(radius), radius


def _place_dome(diameter: float, height: float, face_point, outward_dir):
    """Купол, чья вершина выступает на `height` от точки `face_point` в
    направлении `outward_dir` (единичный вектор), а основание лежит на
    плоскости грани."""
    sphere, radius = _dome_sphere(diameter, height)
    fx, fy, fz = face_point
    dx, dy, dz = outward_dir
    setback = radius - height
    cx, cy, cz = fx - dx * setback, fy - dy * setback, fz - dz * setback
    return sphere.translate((cx, cy, cz))


def _snap_positions(box_width, box_length, per_long_side, per_short_side):
    """Точки (x, y, outward_dir) на внешних стенках короба для защёлок.
    Длинные стенки — по Y = ±box_length/2 (тянутся вдоль X, длина = box_width);
    короткие стенки — по X = ±box_width/2 (тянутся вдоль Y, длина = box_length)."""
    positions = []

    def spread(n, span):
        if n <= 0:
            return []
        step = span / (n + 1)
        return [-span / 2 + step * (i + 1) for i in range(n)]

    for x in spread(per_long_side, box_width):
        positions.append((x, box_length / 2, (0.0, 1.0, 0.0)))
        positions.append((x, -box_length / 2, (0.0, -1.0, 0.0)))

    for y in spread(per_short_side, box_length):
        positions.append((box_width / 2, y, (1.0, 0.0, 0.0)))
        positions.append((-box_width / 2, y, (-1.0, 0.0, 0.0)))

    return positions


def add_snap_bumps(
    body: cq.Workplane,
    box_width: float,
    box_length: float,
    box_height: float,
    skirt_depth: float,
    snap_diameter: float,
    snap_height: float,
    snap_per_long_side: int,
    snap_per_short_side: int,
) -> cq.Workplane:
    """Добавляет на внешние стенки короба выступы-защёлки на середине
    высоты будущего перекрытия юбкой крышки (верхние skirt_depth мм)."""
    z = box_height - skirt_depth / 2
    for x, y, outward in _snap_positions(box_width, box_length, snap_per_long_side, snap_per_short_side):
        dome = _place_dome(snap_diameter, snap_height, (x, y, z), outward)
        body = body.union(dome)
    return body


def add_snap_pockets(
    lid: cq.Workplane,
    box_width: float,
    box_length: float,
    skirt_depth: float,
    fit_clearance: float,
    snap_diameter: float,
    snap_height: float,
    snap_per_long_side: int,
    snap_per_short_side: int,
) -> cq.Workplane:
    """Вырезает на внутренней стороне юбки крышки углубления, отвечающие
    выступам-защёлкам на коробе (со небольшим запасом для лёгкой сборки)."""
    inner_w = box_width + 2 * fit_clearance
    inner_l = box_length + 2 * fit_clearance
    pocket_diameter = snap_diameter + 0.4
    pocket_height = snap_height + 0.15
    z_local = skirt_depth / 2
    for x, y, outward in _snap_positions(inner_w, inner_l, snap_per_long_side, snap_per_short_side):
        pocket = _place_dome(pocket_diameter, pocket_height, (x, y, z_local), outward)
        lid = lid.cut(pocket)
    return lid


def build_lid(
    box_width: float = DEFAULTS["box_width"],
    box_length: float = DEFAULTS["box_length"],
    box_height: float = DEFAULTS["box_height"],
    wall_thickness: float = DEFAULTS["wall_thickness"],
    lid_thickness: float = DEFAULTS["lid_thickness"],
    skirt_depth: float = DEFAULTS["skirt_depth"],
    fit_clearance: float = DEFAULTS["fit_clearance"],
    snap_enabled: bool = DEFAULTS["snap_enabled"],
    snap_diameter: float = DEFAULTS["snap_diameter"],
    snap_height: float = DEFAULTS["snap_height"],
    snap_per_long_side: int = DEFAULTS["snap_per_long_side"],
    snap_per_short_side: int = DEFAULTS["snap_per_short_side"],
    stacking_enabled: bool = DEFAULTS["stacking_enabled"],
    stack_clearance: float = DEFAULTS["stack_clearance"],
    lip_height: float = DEFAULTS["lip_height"],
    lip_wall_thickness: float = DEFAULTS["lip_wall_thickness"],
) -> cq.Workplane:
    """Строит крышку для короба с параметрами box_width/box_length/
    box_height/wall_thickness (они должны совпадать с параметрами короба,
    на который крышка должна сесть).

    Крышка = плоская панель + юбка (стенка), надевающаяся снаружи на
    стенки короба на skirt_depth мм, с зазором fit_clearance на сторону.
    При snap_enabled=True на юбке вырезаны углубления под защёлки короба
    (см. add_snap_bumps/add_snap_pockets).

    При stacking_enabled=True на верхней стороне крышки формируется
    приподнятый бортик высотой lip_height, внутренний размер которого
    равен box_width x box_length + 2*stack_clearance — дно следующего
    короба, поставленного сверху, садится внутрь бортика и не съезжает.

    Локальная система координат крышки: Z=0 — нижний край юбки (та
    сторона, которой крышка надевается на короб), крышка центрирована по
    X/Y так же, как и короб.
    """
    if skirt_depth <= 0 or skirt_depth >= box_height:
        raise ValueError("skirt_depth должен быть положительным и меньше высоты короба")
    if lid_thickness <= 0:
        raise ValueError("lid_thickness должен быть положительным")

    skirt_inner_w = box_width + 2 * fit_clearance
    skirt_inner_l = box_length + 2 * fit_clearance
    skirt_outer_w = skirt_inner_w + 2 * wall_thickness
    skirt_outer_l = skirt_inner_l + 2 * wall_thickness

    lip_outer_w = box_width + 2 * stack_clearance + 2 * lip_wall_thickness
    lip_outer_l = box_length + 2 * stack_clearance + 2 * lip_wall_thickness

    plate_w = max(skirt_outer_w, lip_outer_w)
    plate_l = max(skirt_outer_l, lip_outer_l)

    # Юбка: разница внешнего и внутреннего прямоугольников, высотой skirt_depth.
    skirt = (
        cq.Workplane("XY")
        .rect(skirt_outer_w, skirt_outer_l)
        .rect(skirt_inner_w, skirt_inner_l)
        .extrude(skirt_depth)
    )

    # Верхняя панель, сидит поверх юбки.
    plate = (
        cq.Workplane("XY")
        .rect(plate_w, plate_l)
        .extrude(lid_thickness)
        .translate((0, 0, skirt_depth))
    )

    lid = skirt.union(plate)

    if stacking_enabled:
        if lip_height <= 0 or lip_wall_thickness <= 0:
            raise ValueError("lip_height и lip_wall_thickness должны быть положительными")
        lip_inner_w = box_width + 2 * stack_clearance
        lip_inner_l = box_length + 2 * stack_clearance
        lip = (
            cq.Workplane("XY")
            .rect(lip_outer_w, lip_outer_l)
            .rect(lip_inner_w, lip_inner_l)
            .extrude(lip_height)
            .translate((0, 0, skirt_depth + lid_thickness))
        )
        lid = lid.union(lip)

    if snap_enabled:
        lid = add_snap_pockets(
            lid, box_width, box_length, skirt_depth, fit_clearance,
            snap_diameter, snap_height, snap_per_long_side, snap_per_short_side,
        )

    return lid


def build_box_and_lid(**kwargs):
    """Удобная обёртка: строит короб и подходящую к нему крышку из общего
    набора параметров. Принимает объединение параметров build_fastener_box
    и build_lid (пересекающиеся имена — box_width/box_length/box_height/
    wall_thickness — используются в обеих частях)."""
    box_keys = {
        "box_width", "box_length", "box_height", "wall_thickness",
        "num_rows", "row_cols", "corner_radius", "row_sizes", "row_col_sizes",
    }
    lid_keys = {
        "box_width", "box_length", "box_height", "wall_thickness",
        "lid_thickness", "skirt_depth", "fit_clearance",
        "snap_enabled", "snap_diameter", "snap_height",
        "snap_per_long_side", "snap_per_short_side",
        "stacking_enabled", "stack_clearance", "lip_height", "lip_wall_thickness",
    }
    box_args = {k: v for k, v in kwargs.items() if k in box_keys}
    lid_args = {k: v for k, v in kwargs.items() if k in lid_keys}

    base = build_fastener_box(**box_args)
    if kwargs.get("snap_enabled", DEFAULTS["snap_enabled"]):
        base = add_snap_bumps(
            base,
            box_width=kwargs.get("box_width", DEFAULTS["box_width"]),
            box_length=kwargs.get("box_length", DEFAULTS["box_length"]),
            box_height=kwargs.get("box_height", DEFAULTS["box_height"]),
            skirt_depth=kwargs.get("skirt_depth", DEFAULTS["skirt_depth"]),
            snap_diameter=kwargs.get("snap_diameter", DEFAULTS["snap_diameter"]),
            snap_height=kwargs.get("snap_height", DEFAULTS["snap_height"]),
            snap_per_long_side=kwargs.get("snap_per_long_side", DEFAULTS["snap_per_long_side"]),
            snap_per_short_side=kwargs.get("snap_per_short_side", DEFAULTS["snap_per_short_side"]),
        )
    lid = build_lid(**lid_args)
    return base, lid


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


def _export_stl(shape, path: str) -> bool:
    """Экспортирует STL и лечит редкие вырожденные (нулевой площади)
    треугольники, изредка возникающие в тесселяции OCCT на стыке купола
    защёлки с вырезом отсека — сама CAD-модель при этом валидна, но
    получившийся STL может быть не watertight. Возвращает True, если файл
    в итоге water-tight."""
    cq.exporters.export(shape, path)
    mesh = trimesh.load(path)
    if not mesh.is_watertight:
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
        mesh.merge_vertices()
        if not mesh.is_watertight:
            mesh.fill_holes()
        mesh.export(path)
        mesh = trimesh.load(path)
    return mesh.is_watertight


def _derive_lid_path(base_path: str) -> str:
    if "." in base_path.rsplit("/", 1)[-1]:
        head, ext = base_path.rsplit(".", 1)
        return f"{head}_lid.{ext}"
    return f"{base_path}_lid"


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

    parser.add_argument("--no-lid", action="store_true", help="не генерировать крышку")
    parser.add_argument("--lid-thickness", type=float, default=DEFAULTS["lid_thickness"], help="толщина верхней панели крышки, мм")
    parser.add_argument("--skirt-depth", type=float, default=DEFAULTS["skirt_depth"], help="глубина перекрытия юбкой крышки стенки короба, мм")
    parser.add_argument("--fit-clearance", type=float, default=DEFAULTS["fit_clearance"], help="зазор между юбкой крышки и стенкой короба, мм (на сторону)")
    parser.add_argument("--no-snap", action="store_true", help="не добавлять защёлки")
    parser.add_argument("--snap-diameter", type=float, default=DEFAULTS["snap_diameter"], help="диаметр купола защёлки, мм")
    parser.add_argument("--snap-height", type=float, default=DEFAULTS["snap_height"], help="высота выступа купола защёлки, мм")
    parser.add_argument("--snap-long", type=int, default=DEFAULTS["snap_per_long_side"], help="число защёлок на каждой длинной стенке")
    parser.add_argument("--snap-short", type=int, default=DEFAULTS["snap_per_short_side"], help="число защёлок на каждой короткой стенке")

    parser.add_argument("--no-stacking", action="store_true", help="не добавлять бортик для стекирования на крышке")
    parser.add_argument("--stack-clearance", type=float, default=DEFAULTS["stack_clearance"], help="зазор между бортиком и дном следующего короба, мм (на сторону)")
    parser.add_argument("--lip-height", type=float, default=DEFAULTS["lip_height"], help="высота бортика для стекирования, мм")
    parser.add_argument("--lip-wall", type=float, default=DEFAULTS["lip_wall_thickness"], help="толщина стенки бортика для стекирования, мм")

    parser.add_argument("--out", type=str, default="stl/fastener_box.stl", help="путь к выходному STL файлу короба")
    parser.add_argument("--lid-out", type=str, default=None, help="путь к выходному STL файлу крышки (по умолчанию — из --out с суффиксом _lid)")
    parser.add_argument("--step", type=str, default=None, help="дополнительно сохранить STEP короба по указанному пути")
    args = parser.parse_args(argv)

    row_cols = _parse_int_list(args.row_cols) if args.row_cols is not None else args.cols

    common = dict(
        box_width=args.width,
        box_length=args.length,
        box_height=args.height,
        wall_thickness=args.wall,
        lid_thickness=args.lid_thickness,
        skirt_depth=args.skirt_depth,
        fit_clearance=args.fit_clearance,
        snap_enabled=not args.no_snap,
        snap_diameter=args.snap_diameter,
        snap_height=args.snap_height,
        snap_per_long_side=args.snap_long,
        snap_per_short_side=args.snap_short,
        stacking_enabled=not args.no_stacking,
        stack_clearance=args.stack_clearance,
        lip_height=args.lip_height,
        lip_wall_thickness=args.lip_wall,
    )

    base = build_fastener_box(
        num_rows=args.rows,
        row_cols=row_cols,
        corner_radius=args.radius,
        row_sizes=_parse_float_list(args.row_sizes),
        row_col_sizes=_parse_row_col_sizes(args.row_col_sizes, args.rows),
        **{k: common[k] for k in ("box_width", "box_length", "box_height", "wall_thickness")},
    )
    if common["snap_enabled"]:
        base = add_snap_bumps(
            base,
            box_width=args.width, box_length=args.length, box_height=args.height,
            skirt_depth=args.skirt_depth, snap_diameter=args.snap_diameter,
            snap_height=args.snap_height, snap_per_long_side=args.snap_long,
            snap_per_short_side=args.snap_short,
        )

    ok = _export_stl(base, args.out)
    print(f"STL короба сохранён: {args.out}" + ("" if ok else " (ВНИМАНИЕ: STL не watertight)"))

    if args.step:
        cq.exporters.export(base, args.step)
        print(f"STEP короба сохранён: {args.step}")

    if not args.no_lid:
        lid = build_lid(**common)
        lid_out = args.lid_out or _derive_lid_path(args.out)
        ok = _export_stl(lid, lid_out)
        print(f"STL крышки сохранён: {lid_out}" + ("" if ok else " (ВНИМАНИЕ: STL не watertight)"))


if __name__ == "__main__":
    sys.exit(main())
