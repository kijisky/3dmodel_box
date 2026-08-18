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
    snap_enabled=True,      # добавлять ли защёлки-замки (выступ на коробе / крючок на юбке крышки)
    snap_diameter=4.0,      # диаметр купола-крючка защёлки, мм
    snap_height=0.7,        # выступ купола-крючка над стенкой, мм
    snap_per_long_side=1,   # число защёлок на каждой длинной стенке (по X)
    snap_per_short_side=0,  # число защёлок на каждой короткой стенке (по Y)
    snap_arm_width=14.0,    # ширина гибкого язычка защёлки на юбке крышки, мм
    snap_arm_gap=1.0,       # ширина прорези, отделяющей язычок от остальной юбки, мм
    snap_tab_length=4.0,    # на сколько язычок выступает ниже юбки — за него берутся пальцами
    snap_flare=0.6,         # на сколько нижняя часть язычка выступает наружу (чтобы её было видно/удобно подцепить)

    # --- стекирование ---
    stacking_enabled=True,  # добавлять ли на крышке бортик для стекирования
    stack_clearance=0.25,   # зазор между бортиком и дном следующего короба (на сторону), мм
    lip_height=2.5,          # высота бортика для стекирования, мм
    lip_wall_thickness=1.5,  # толщина стенки бортика, мм

    # --- шарнир ---
    hinge_enabled=True,          # крышка на петлях с одной стороны + защёлка с противоположной
    hinge_wall="south",          # какая стенка короба — шарнир: north/south/east/west
    hinge_pin_diameter=3.0,      # диаметр стержня-шпильки (отдельная деталь), мм
    hinge_pin_clearance=0.3,     # зазор между шпилькой и отверстием петли (на диаметр), мм
    hinge_knuckle_diameter=7.0,  # внешний диаметр каждой петли-цилиндра, мм
    hinge_knuckle_count=5,       # общее число чередующихся петель короб/крышка (нечётное — по петле короба по краям)
    hinge_knuckle_gap=0.5,       # зазор между соседними петлями вдоль оси шарнира, мм
    hinge_margin=10.0,           # отступ ряда петель от боковых стенок короба, мм
    hinge_embed=1.2,             # насколько петля "утоплена" в стенку/крышку для прочного соединения, мм
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


def _halfspace_box(face_point, outward_dir, size: float = 200.0) -> cq.Workplane:
    """Большой прямоугольный параллелепипед, занимающий полупространство
    "наружу от плоскости грани" в точке `face_point` с нормалью
    `outward_dir` (единичный вектор, направленный вдоль одной из осей X/Y)."""
    fx, fy, fz = face_point
    dx, dy, dz = outward_dir
    if dx != 0:
        xmin, xmax = (fx, fx + size) if dx > 0 else (fx - size, fx)
        ymin, ymax = fy - size / 2, fy + size / 2
    else:
        ymin, ymax = (fy, fy + size) if dy > 0 else (fy - size, fy)
        xmin, xmax = fx - size / 2, fx + size / 2
    zmin, zmax = fz - size / 2, fz + size / 2
    return _box_from_bounds(xmin, xmax, ymin, ymax, zmin, zmax)


def _place_dome(diameter: float, height: float, face_point, outward_dir):
    """Купол (сферический сегмент), чья вершина выступает на `height` от
    точки `face_point` в направлении `outward_dir` (единичный вектор), а
    основание лежит на плоскости грани. Сфера обрезана полупространством
    "наружу от грани" — иначе при малой высоте купола и большом диаметре
    сфера может оказаться крупнее стенки и вылезти за её пределы (сквозь
    верх короба, сквозь стенку в отсек и т.п.)."""
    sphere, radius = _dome_sphere(diameter, height)
    fx, fy, fz = face_point
    dx, dy, dz = outward_dir
    setback = radius - height
    cx, cy, cz = fx - dx * setback, fy - dy * setback, fz - dz * setback
    sphere = sphere.translate((cx, cy, cz))
    return sphere.intersect(_halfspace_box(face_point, outward_dir))


#: Направление "наружу" для каждой из 4 стенок короба (north/south — длинные
#: стенки, тянутся вдоль X; east/west — короткие, тянутся вдоль Y).
WALL_OUTWARD = {
    "north": (0.0, 1.0, 0.0),
    "south": (0.0, -1.0, 0.0),
    "east": (1.0, 0.0, 0.0),
    "west": (-1.0, 0.0, 0.0),
}


def _snap_positions(box_width, box_length, per_long_side, per_short_side, exclude_wall=None):
    """Точки (x, y, outward_dir) на внешних стенках короба для защёлок.
    Длинные стенки — по Y = ±box_length/2 (тянутся вдоль X, длина = box_width);
    короткие стенки — по X = ±box_width/2 (тянутся вдоль Y, длина = box_length).
    exclude_wall — имя стены ('north'/'south'/'east'/'west'), на которой
    защёлки не нужны (например, там, где стена занята петлями шарнира)."""
    positions = []

    def spread(n, span):
        if n <= 0:
            return []
        step = span / (n + 1)
        return [-span / 2 + step * (i + 1) for i in range(n)]

    if exclude_wall != "north":
        for x in spread(per_long_side, box_width):
            positions.append((x, box_length / 2, WALL_OUTWARD["north"]))
    if exclude_wall != "south":
        for x in spread(per_long_side, box_width):
            positions.append((x, -box_length / 2, WALL_OUTWARD["south"]))
    if exclude_wall != "east":
        for y in spread(per_short_side, box_length):
            positions.append((box_width / 2, y, WALL_OUTWARD["east"]))
    if exclude_wall != "west":
        for y in spread(per_short_side, box_length):
            positions.append((-box_width / 2, y, WALL_OUTWARD["west"]))

    return positions


def _box_from_bounds(xmin, xmax, ymin, ymax, zmin, zmax) -> cq.Workplane:
    """Прямоугольный параллелепипед по явным границам координат."""
    box = cq.Workplane("XY").box(
        xmax - xmin, ymax - ymin, zmax - zmin, centered=(False, False, False)
    )
    return box.translate((xmin, ymin, zmin))


def _wall_span_bounds(x_center, y_center, outward, tangent_span, box_width, box_length,
                       wall_thickness, fit_clearance, extra_out=0.0):
    """XY-границы участка стенки юбки шириной tangent_span в районе точки
    (x_center, y_center) на стенке с направлением наружу `outward`.
    Внутренняя грань — на границе короба + fit_clearance (как у юбки),
    внешняя — дальше на wall_thickness (+ extra_out, для "наплыва" язычка)."""
    dx, dy, _ = outward
    if dx != 0:
        face_in = dx * (box_width / 2 + fit_clearance)
        face_out = face_in + dx * (wall_thickness + extra_out)
        xmin, xmax = sorted((face_in, face_out))
        ymin, ymax = y_center - tangent_span / 2, y_center + tangent_span / 2
    else:
        face_in = dy * (box_length / 2 + fit_clearance)
        face_out = face_in + dy * (wall_thickness + extra_out)
        ymin, ymax = sorted((face_in, face_out))
        xmin, xmax = x_center - tangent_span / 2, x_center + tangent_span / 2
    return xmin, xmax, ymin, ymax


def add_latch_arms(
    lid: cq.Workplane,
    box_width: float,
    box_length: float,
    wall_thickness: float,
    skirt_depth: float,
    fit_clearance: float,
    arm_width: float,
    arm_gap: float,
    tab_length: float,
    flare: float,
    snap_per_long_side: int,
    snap_per_short_side: int,
    exclude_wall=None,
) -> cq.Workplane:
    """Освобождает в юбке крышки гибкие язычки-защёлки: с обеих сторон
    каждой защёлки прорезается узкая щель (на всю глубину юбки), так что
    язычок остаётся закреплён только сверху (на панели крышки) и может
    пружинить — по нему можно нажать/потянуть, чтобы отстегнуть крышку.
    Снизу язычок продолжается за пределы юбки на tab_length мм с небольшим
    наплывом (flare) наружу — за этот выступ удобно взяться пальцами."""
    inner_w = box_width + 2 * fit_clearance
    inner_l = box_length + 2 * fit_clearance
    slot_overshoot = 1.0

    for x, y, outward in _snap_positions(inner_w, inner_l, snap_per_long_side, snap_per_short_side, exclude_wall):
        # Язычок продолжается ниже юбки (z<0) с наплывом наружу — для захвата.
        txmin, txmax, tymin, tymax = _wall_span_bounds(
            x, y, outward, arm_width, box_width, box_length, wall_thickness, fit_clearance, extra_out=flare
        )
        tab = _box_from_bounds(txmin, txmax, tymin, tymax, -tab_length, 0.0)
        lid = lid.union(tab)

        # Две прорези по бокам язычка на всю его высоту (юбка + выступ ниже неё).
        for side in (-1, 1):
            dx, dy, _ = outward
            if dx != 0:
                center = y + side * arm_width / 2
                sxmin, sxmax, symin, symax = _wall_span_bounds(
                    x, center, outward, arm_gap, box_width, box_length,
                    wall_thickness, fit_clearance, extra_out=flare + 0.5,
                )
            else:
                center = x + side * arm_width / 2
                sxmin, sxmax, symin, symax = _wall_span_bounds(
                    center, y, outward, arm_gap, box_width, box_length,
                    wall_thickness, fit_clearance, extra_out=flare + 0.5,
                )
            slot = _box_from_bounds(sxmin, sxmax, symin, symax, -tab_length - slot_overshoot, skirt_depth)
            lid = lid.cut(slot)

    return lid


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
    exclude_wall=None,
) -> cq.Workplane:
    """Добавляет на внешние стенки короба выступы-защёлки на середине
    высоты будущего перекрытия юбкой крышки (верхние skirt_depth мм)."""
    z = box_height - skirt_depth / 2
    for x, y, outward in _snap_positions(box_width, box_length, snap_per_long_side, snap_per_short_side, exclude_wall):
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
    exclude_wall=None,
) -> cq.Workplane:
    """Вырезает на внутренней стороне юбки крышки углубления, отвечающие
    выступам-защёлкам на коробе (со небольшим запасом для лёгкой сборки)."""
    inner_w = box_width + 2 * fit_clearance
    inner_l = box_length + 2 * fit_clearance
    pocket_diameter = snap_diameter + 0.4
    pocket_height = snap_height + 0.15
    z_local = skirt_depth / 2
    for x, y, outward in _snap_positions(inner_w, inner_l, snap_per_long_side, snap_per_short_side, exclude_wall):
        pocket = _place_dome(pocket_diameter, pocket_height, (x, y, z_local), outward)
        lid = lid.cut(pocket)
    return lid


# ---------------------------------------------------------------------------
# Шарнир: короб и крышка соединены на одной стенке рядом чередующихся
# петель-цилиндров (короб/крышка/короб/...), через которые после печати
# продевается отдельная деталь — стержень-шпилька.
# ---------------------------------------------------------------------------

def _tangent_cylinder(axis: str, radius: float, t_min: float, t_max: float,
                       normal_coord: float, z_coord: float) -> cq.Workplane:
    """Цилиндр радиуса `radius`, ось которого идёт вдоль X (axis='x') или
    вдоль Y (axis='y') от t_min до t_max; `normal_coord` — координата оси
    по перпендикулярному горизонтальному направлению, `z_coord` — высота Z."""
    cyl = cq.Workplane("XY").circle(radius).extrude(t_max - t_min)
    if axis == "x":
        cyl = cyl.rotate((0, 0, 0), (0, 1, 0), 90)
        return cyl.translate((t_min, normal_coord, z_coord))
    cyl = cyl.rotate((0, 0, 0), (1, 0, 0), -90)
    return cyl.translate((normal_coord, t_min, z_coord))


def _hinge_knuckle_segments(hinge_span: float, count: int, gap: float):
    """Список (t_min, t_max, owner) для `count` чередующихся петель вдоль
    оси шарнира длиной hinge_span, с зазором gap между соседними. owner —
    'box' для чётных индексов (петли короба, по краям ряда), 'lid' —
    для нечётных (петли крышки)."""
    if count < 1:
        raise ValueError("hinge_knuckle_count должен быть не меньше 1")
    usable = hinge_span - gap * (count - 1)
    if usable <= 0:
        raise ValueError("hinge_knuckle_gap слишком велик для hinge_knuckle_count петель на этой стенке")
    width = usable / count
    t0 = -hinge_span / 2
    segments = []
    for i in range(count):
        tmin = t0 + i * (width + gap)
        tmax = tmin + width
        owner = "box" if i % 2 == 0 else "lid"
        segments.append((tmin, tmax, owner))
    return segments


def _hinge_geometry(box_width, box_length, box_height, hinge_wall, hinge_knuckle_diameter, hinge_embed):
    """Общие для короба и крышки величины оси шарнира: направление
    "наружу", ось вдоль которой тянутся петли (x/y), координата этой оси
    по нормали к стенке (одна и та же у короба и крышки — чтобы петли
    были соосны), высота Z (в глобальных координатах короба) и полная
    длина стенки-шарнира (для расчёта отступов/протяжённости ряда петель)."""
    if hinge_wall not in WALL_OUTWARD:
        raise ValueError(f"hinge_wall должен быть одним из {list(WALL_OUTWARD)}")
    dx, dy, _ = WALL_OUTWARD[hinge_wall]
    axis = "x" if dy != 0 else "y"
    wall_span = box_width if axis == "x" else box_length
    sign = dy if dy != 0 else dx
    wall_outer = sign * ((box_length if axis == "x" else box_width) / 2)
    knuckle_radius = hinge_knuckle_diameter / 2
    pivot_normal = wall_outer + sign * (knuckle_radius - hinge_embed)
    pivot_z = box_height
    return axis, wall_span, pivot_normal, pivot_z


def add_hinge(
    base: cq.Workplane,
    lid: cq.Workplane,
    box_width: float = DEFAULTS["box_width"],
    box_length: float = DEFAULTS["box_length"],
    box_height: float = DEFAULTS["box_height"],
    skirt_depth: float = DEFAULTS["skirt_depth"],
    hinge_wall: str = DEFAULTS["hinge_wall"],
    hinge_pin_diameter: float = DEFAULTS["hinge_pin_diameter"],
    hinge_pin_clearance: float = DEFAULTS["hinge_pin_clearance"],
    hinge_knuckle_diameter: float = DEFAULTS["hinge_knuckle_diameter"],
    hinge_knuckle_count: int = DEFAULTS["hinge_knuckle_count"],
    hinge_knuckle_gap: float = DEFAULTS["hinge_knuckle_gap"],
    hinge_margin: float = DEFAULTS["hinge_margin"],
    hinge_embed: float = DEFAULTS["hinge_embed"],
):
    """Добавляет к коробу и крышке чередующиеся петли шарнира на стенке
    hinge_wall и возвращает (base, lid, pin) — короб, крышку и отдельную
    деталь-шпильку для сборки (продевается в отверстия петель после
    печати). Петли короба и крышки соосны при "закрытом" положении
    крышки (as build_lid размещает её при skirt_depth-посадке)."""
    axis, wall_span, pivot_normal, pivot_z = _hinge_geometry(
        box_width, box_length, box_height, hinge_wall, hinge_knuckle_diameter, hinge_embed
    )
    hinge_span = wall_span - 2 * hinge_margin
    if hinge_span <= 0:
        raise ValueError("hinge_margin слишком велик для длины стенки-шарнира")

    segments = _hinge_knuckle_segments(hinge_span, hinge_knuckle_count, hinge_knuckle_gap)
    knuckle_radius = hinge_knuckle_diameter / 2
    bore_radius = hinge_pin_diameter / 2 + hinge_pin_clearance / 2
    clearance_radius = knuckle_radius + 0.3
    bore_overshoot = 0.5

    for tmin, tmax, owner in segments:
        z = pivot_z if owner == "box" else skirt_depth
        knuckle = _tangent_cylinder(axis, knuckle_radius, tmin, tmax, pivot_normal, z)
        bore = _tangent_cylinder(axis, bore_radius, tmin - bore_overshoot, tmax + bore_overshoot, pivot_normal, z)
        knuckle = knuckle.cut(bore)
        # На "закрытой" крышке (build_lid/CLI сажают её ровно поверх короба
        # без поворота) петли короба и крышки соосны и лежат в одном
        # Z-диапазоне — значит, в сегментах чужого владельца нужно вырезать
        # у СВОЕЙ детали зазор под петлю другой, иначе петля короба упрётся
        # в панель крышки, а петля крышки — в стенку короба.
        clearance = _tangent_cylinder(
            axis, clearance_radius, tmin - bore_overshoot, tmax + bore_overshoot, pivot_normal,
            skirt_depth if owner == "box" else pivot_z,
        )
        if owner == "box":
            base = base.union(knuckle)
            lid = lid.cut(clearance)
        else:
            lid = lid.union(knuckle)
            base = base.cut(clearance)

    pin_length = hinge_span - hinge_knuckle_gap
    pin_radius = hinge_pin_diameter / 2
    pin = _tangent_cylinder(axis, pin_radius, -pin_length / 2, pin_length / 2, pivot_normal, pivot_z)

    return base, lid, pin


def _remove_wall_skirt(lid: cq.Workplane, box_width, box_length, wall_thickness, fit_clearance,
                        skirt_depth, wall: str) -> cq.Workplane:
    """Вырезает из юбки крышки участок стенки `wall` целиком (там, где
    вместо скользящей посадки будет ряд петель шарнира)."""
    overshoot = 2.0
    skirt_inner_w = box_width + 2 * fit_clearance
    skirt_inner_l = box_length + 2 * fit_clearance
    skirt_outer_w = skirt_inner_w + 2 * wall_thickness
    skirt_outer_l = skirt_inner_l + 2 * wall_thickness
    dx, dy, _ = WALL_OUTWARD[wall]
    if dx != 0:
        face_in = dx * (skirt_inner_w / 2)
        face_out = dx * (skirt_outer_w / 2 + overshoot)
        xmin, xmax = sorted((face_in, face_out))
        ymin, ymax = -skirt_outer_l / 2 - overshoot, skirt_outer_l / 2 + overshoot
    else:
        face_in = dy * (skirt_inner_l / 2)
        face_out = dy * (skirt_outer_l / 2 + overshoot)
        ymin, ymax = sorted((face_in, face_out))
        xmin, xmax = -skirt_outer_w / 2 - overshoot, skirt_outer_w / 2 + overshoot
    notch = _box_from_bounds(xmin, xmax, ymin, ymax, -overshoot, skirt_depth + overshoot)
    return lid.cut(notch)


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
    snap_arm_width: float = DEFAULTS["snap_arm_width"],
    snap_arm_gap: float = DEFAULTS["snap_arm_gap"],
    snap_tab_length: float = DEFAULTS["snap_tab_length"],
    snap_flare: float = DEFAULTS["snap_flare"],
    stacking_enabled: bool = DEFAULTS["stacking_enabled"],
    stack_clearance: float = DEFAULTS["stack_clearance"],
    lip_height: float = DEFAULTS["lip_height"],
    lip_wall_thickness: float = DEFAULTS["lip_wall_thickness"],
    hinge_enabled: bool = DEFAULTS["hinge_enabled"],
    hinge_wall: str = DEFAULTS["hinge_wall"],
) -> cq.Workplane:
    """Строит крышку для короба с параметрами box_width/box_length/
    box_height/wall_thickness (они должны совпадать с параметрами короба,
    на который крышка должна сесть).

    Крышка = плоская панель + юбка (стенка), надевающаяся снаружи на
    стенки короба на skirt_depth мм, с зазором fit_clearance на сторону.

    При hinge_enabled=True юбка на стенке hinge_wall вырезается целиком —
    там вместо скользящей посадки формируется ряд петель шарнира (см.
    add_hinge, вызывается отдельно после build_fastener_box/build_lid,
    т.к. затрагивает обе детали). Защёлки (если включены) автоматически
    исключаются с этой стенки — по умолчанию (`snap_per_long_side=1`,
    hinge_wall="south") получается ровно одна защёлка на противоположной
    длинной стенке, как у обычного чемодана.

    При snap_enabled=True на юбке (кроме стенки шарнира) вырезаются
    защёлки-замки: узкими прорезями (add_latch_arms) от остальной юбки
    отделяется гибкий язычок шириной snap_arm_width, продолжающийся ниже
    юбки на snap_tab_length мм (с наплывом snap_flare — чтобы его было
    видно и удобно подцепить пальцами). На язычке — крючок-купол
    (add_snap_pockets), цепляющийся за ответный выступ на стенке короба
    (add_snap_bumps). Так как язычок отделён от жёсткой юбки, его можно
    отогнуть и отстегнуть крышку, не ломая защёлку.

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
    if snap_enabled and skirt_depth + snap_tab_length >= box_height:
        raise ValueError("skirt_depth + snap_tab_length должны быть меньше высоты короба")
    if hinge_enabled and hinge_wall not in WALL_OUTWARD:
        raise ValueError(f"hinge_wall должен быть одним из {list(WALL_OUTWARD)}")

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

    hinge_exclude = hinge_wall if hinge_enabled else None
    if hinge_enabled:
        lid = _remove_wall_skirt(lid, box_width, box_length, wall_thickness, fit_clearance, skirt_depth, hinge_wall)

    if snap_enabled:
        lid = add_latch_arms(
            lid, box_width, box_length, wall_thickness, skirt_depth, fit_clearance,
            snap_arm_width, snap_arm_gap, snap_tab_length, snap_flare,
            snap_per_long_side, snap_per_short_side, exclude_wall=hinge_exclude,
        )
        lid = add_snap_pockets(
            lid, box_width, box_length, skirt_depth, fit_clearance,
            snap_diameter, snap_height, snap_per_long_side, snap_per_short_side,
            exclude_wall=hinge_exclude,
        )

    return lid


def build_box_and_lid(**kwargs):
    """Удобная обёртка: строит короб, подходящую к нему крышку и (если
    hinge_enabled) деталь-шпильку шарнира из общего набора параметров.
    Принимает объединение параметров build_fastener_box, build_lid и
    add_hinge (пересекающиеся имена — box_width/box_length/box_height/
    wall_thickness — используются в нескольких частях). Возвращает
    (base, lid, pin) — pin равен None, если шарнир отключён."""
    box_keys = {
        "box_width", "box_length", "box_height", "wall_thickness",
        "num_rows", "row_cols", "corner_radius", "row_sizes", "row_col_sizes",
    }
    lid_keys = {
        "box_width", "box_length", "box_height", "wall_thickness",
        "lid_thickness", "skirt_depth", "fit_clearance",
        "snap_enabled", "snap_diameter", "snap_height",
        "snap_per_long_side", "snap_per_short_side",
        "snap_arm_width", "snap_arm_gap", "snap_tab_length", "snap_flare",
        "stacking_enabled", "stack_clearance", "lip_height", "lip_wall_thickness",
        "hinge_enabled", "hinge_wall",
    }
    hinge_keys = {
        "box_width", "box_length", "box_height", "skirt_depth", "hinge_wall",
        "hinge_pin_diameter", "hinge_pin_clearance", "hinge_knuckle_diameter",
        "hinge_knuckle_count", "hinge_knuckle_gap", "hinge_margin", "hinge_embed",
    }
    box_args = {k: v for k, v in kwargs.items() if k in box_keys}
    lid_args = {k: v for k, v in kwargs.items() if k in lid_keys}
    hinge_args = {k: v for k, v in kwargs.items() if k in hinge_keys}

    hinge_enabled = kwargs.get("hinge_enabled", DEFAULTS["hinge_enabled"])
    hinge_wall = kwargs.get("hinge_wall", DEFAULTS["hinge_wall"])
    snap_exclude = hinge_wall if hinge_enabled else None

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
            exclude_wall=snap_exclude,
        )
    lid = build_lid(**lid_args)

    pin = None
    if hinge_enabled:
        base, lid, pin = add_hinge(base, lid, **hinge_args)

    return base, lid, pin


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
    parser.add_argument("--no-snap", action="store_true", help="не добавлять защёлки-замки (только посадка с натягом)")
    parser.add_argument("--snap-diameter", type=float, default=DEFAULTS["snap_diameter"], help="диаметр купола-крючка защёлки, мм")
    parser.add_argument("--snap-height", type=float, default=DEFAULTS["snap_height"], help="выступ купола-крючка защёлки, мм")
    parser.add_argument("--snap-long", type=int, default=DEFAULTS["snap_per_long_side"], help="число защёлок на каждой длинной стенке")
    parser.add_argument("--snap-short", type=int, default=DEFAULTS["snap_per_short_side"], help="число защёлок на каждой короткой стенке")
    parser.add_argument("--snap-arm-width", type=float, default=DEFAULTS["snap_arm_width"], help="ширина гибкого язычка защёлки, мм")
    parser.add_argument("--snap-arm-gap", type=float, default=DEFAULTS["snap_arm_gap"], help="ширина прорези, отделяющей язычок защёлки от юбки, мм")
    parser.add_argument("--snap-tab-length", type=float, default=DEFAULTS["snap_tab_length"], help="на сколько язычок защёлки выступает ниже юбки (для захвата пальцами), мм")
    parser.add_argument("--snap-flare", type=float, default=DEFAULTS["snap_flare"], help="наплыв наружу нижней части язычка защёлки (чтобы было видно/удобно подцепить), мм")

    parser.add_argument("--no-stacking", action="store_true", help="не добавлять бортик для стекирования на крышке")
    parser.add_argument("--stack-clearance", type=float, default=DEFAULTS["stack_clearance"], help="зазор между бортиком и дном следующего короба, мм (на сторону)")
    parser.add_argument("--lip-height", type=float, default=DEFAULTS["lip_height"], help="высота бортика для стекирования, мм")
    parser.add_argument("--lip-wall", type=float, default=DEFAULTS["lip_wall_thickness"], help="толщина стенки бортика для стекирования, мм")

    parser.add_argument("--no-hinge", action="store_true", help="не добавлять шарнир (крышка полностью съёмная на защёлках со всех сторон)")
    parser.add_argument("--hinge-wall", type=str, default=DEFAULTS["hinge_wall"], choices=["north", "south", "east", "west"], help="какая стенка короба — шарнир")
    parser.add_argument("--hinge-pin-diameter", type=float, default=DEFAULTS["hinge_pin_diameter"], help="диаметр стержня-шпильки шарнира, мм")
    parser.add_argument("--hinge-pin-clearance", type=float, default=DEFAULTS["hinge_pin_clearance"], help="зазор между шпилькой и отверстием петли, мм")
    parser.add_argument("--hinge-knuckle-diameter", type=float, default=DEFAULTS["hinge_knuckle_diameter"], help="внешний диаметр петель шарнира, мм")
    parser.add_argument("--hinge-knuckle-count", type=int, default=DEFAULTS["hinge_knuckle_count"], help="общее число чередующихся петель короб/крышка")
    parser.add_argument("--hinge-knuckle-gap", type=float, default=DEFAULTS["hinge_knuckle_gap"], help="зазор между соседними петлями шарнира, мм")
    parser.add_argument("--hinge-margin", type=float, default=DEFAULTS["hinge_margin"], help="отступ ряда петель шарнира от боковых стенок короба, мм")
    parser.add_argument("--hinge-embed", type=float, default=DEFAULTS["hinge_embed"], help="насколько петля утоплена в стенку/крышку, мм")

    parser.add_argument("--out", type=str, default="stl/fastener_box.stl", help="путь к выходному STL файлу короба")
    parser.add_argument("--lid-out", type=str, default=None, help="путь к выходному STL файлу крышки (по умолчанию — из --out с суффиксом _lid)")
    parser.add_argument("--pin-out", type=str, default=None, help="путь к выходному STL файлу шпильки шарнира (по умолчанию — из --out с суффиксом _pin)")
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
        snap_arm_width=args.snap_arm_width,
        snap_arm_gap=args.snap_arm_gap,
        snap_tab_length=args.snap_tab_length,
        snap_flare=args.snap_flare,
        stacking_enabled=not args.no_stacking,
        stack_clearance=args.stack_clearance,
        lip_height=args.lip_height,
        lip_wall_thickness=args.lip_wall,
        hinge_enabled=(not args.no_hinge) and (not args.no_lid),
        hinge_wall=args.hinge_wall,
    )

    snap_exclude = common["hinge_wall"] if common["hinge_enabled"] else None

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
            snap_per_short_side=args.snap_short, exclude_wall=snap_exclude,
        )

    if args.step:
        cq.exporters.export(base, args.step)
        print(f"STEP короба сохранён: {args.step}")

    if not args.no_lid:
        lid = build_lid(**common)
        if common["hinge_enabled"]:
            base, lid, pin = add_hinge(
                base, lid,
                box_width=args.width, box_length=args.length, box_height=args.height,
                skirt_depth=args.skirt_depth, hinge_wall=args.hinge_wall,
                hinge_pin_diameter=args.hinge_pin_diameter,
                hinge_pin_clearance=args.hinge_pin_clearance,
                hinge_knuckle_diameter=args.hinge_knuckle_diameter,
                hinge_knuckle_count=args.hinge_knuckle_count,
                hinge_knuckle_gap=args.hinge_knuckle_gap,
                hinge_margin=args.hinge_margin, hinge_embed=args.hinge_embed,
            )
            pin_out = args.pin_out or _derive_lid_path(args.out).replace("_lid.", "_pin.")
            ok = _export_stl(pin, pin_out)
            print(f"STL шпильки шарнира сохранён: {pin_out}" + ("" if ok else " (ВНИМАНИЕ: STL не watertight)"))

        ok = _export_stl(base, args.out)
        print(f"STL короба сохранён: {args.out}" + ("" if ok else " (ВНИМАНИЕ: STL не watertight)"))

        lid_out = args.lid_out or _derive_lid_path(args.out)
        ok = _export_stl(lid, lid_out)
        print(f"STL крышки сохранён: {lid_out}" + ("" if ok else " (ВНИМАНИЕ: STL не watertight)"))
    else:
        ok = _export_stl(base, args.out)
        print(f"STL короба сохранён: {args.out}" + ("" if ok else " (ВНИМАНИЕ: STL не watertight)"))


if __name__ == "__main__":
    sys.exit(main())
