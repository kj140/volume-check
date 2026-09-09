"""平面（配置図・各階平面図）の幾何。

DXF出力（drawer.py）とWebのSVG（web/svg_plan.py）が同じ形を描くための共有モジュール。
断面については section.py が同じ役割を持つ。

座標系: 敷地ローカル (x, y)、単位 mm。反時計回りを正とする。

Frame が方位による回転を担う。敷地ローカル座標における北の向き
（SiteInput.north_angle_rad）を図面の上向きに合わせるので、前面道路が
どちらにあっても図は「北が上」になり、道路は正しい側に描かれる。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import geometry as G
from models import Constraint, VolumeResult

Point = tuple[float, float]
Outline = tuple[Point, ...]


class Frame:
    """敷地ローカル座標 (x, y) → 描画座標への変換（Y上向きの数学座標系）。

    北が図面の上（+Y）を向くよう回転させ、local_bbox で与えた範囲の左下が
    (base_x, base_y) に来るよう平行移動する。SVG のように Y が下向きの系で
    使う場合は、描画側で最後に上下を反転させる。
    """

    def __init__(self, north_angle_rad: float, base_x: float, base_y: float,
                 local_bbox: tuple[float, float, float, float]):
        # 北を +Y に向ける回転
        theta = math.pi / 2 - north_angle_rad
        self._cos, self._sin = math.cos(theta), math.sin(theta)
        x0, y0, x1, y1 = local_bbox
        corners = [self._rot(x, y) for x in (x0, x1) for y in (y0, y1)]
        self._ox = base_x - min(c[0] for c in corners)
        self._oy = base_y - min(c[1] for c in corners)

    def _rot(self, x: float, y: float) -> Point:
        return (x * self._cos - y * self._sin, x * self._sin + y * self._cos)

    def __call__(self, x: float, y: float) -> Point:
        rx, ry = self._rot(x, y)
        return (rx + self._ox, ry + self._oy)

    def path(self, outline: Outline) -> list[Point]:
        """頂点列をまとめて変換する。"""
        return [self(x, y) for x, y in outline]

    def bbox(self, outline: Outline) -> tuple[float, float, float, float]:
        """頂点列を変換したときの (minx, miny, maxx, maxy)。

        回転がかかるため、ラベルの位置決めはローカル座標のオフセットではなく
        この描画座標のバウンディングボックスを基準にする。
        """
        pts = self.path(outline)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class RoadStrip:
    """道路の描画用の帯。敷地の辺から幅員分だけ外側に出した四角形。"""

    outline: Outline
    width_mm: float
    center_line: tuple[Point, Point]


@dataclass(frozen=True)
class FloorPlan:
    """1 階分の平面。座標は敷地ローカル系（mm）。"""

    floor: int
    outline: Outline
    area_mm2: float
    dominant: Constraint | None
    setback_road_mm: float
    setback_neighbor_mm: float


@dataclass(frozen=True)
class PlanGeometry:
    """配置図・各階平面図を描くのに必要な幾何。"""

    site_outline: Outline
    roads: tuple[RoadStrip, ...]
    floors: tuple[FloorPlan, ...]
    unconstrained: Outline          # 斜線・建蔽率がなければ建てられた範囲
    north_angle_rad: float
    frontage_mm: float
    depth_mm: float
    road_width_mm: float
    is_rectangle: bool

    def local_bbox(self, pad_mm: float = 0.0) -> tuple[float, float, float, float]:
        """敷地と道路を含む範囲。作図の外枠に使う。"""
        pts = list(self.site_outline)
        for road in self.roads:
            pts.extend(road.outline)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs) - pad_mm, min(ys) - pad_mm, max(xs) + pad_mm, max(ys) + pad_mm)


def _road_strip(edge: G.SiteEdge) -> RoadStrip:
    """敷地の辺から幅員分だけ外側へ出した帯を作る。

    敷地は反時計回りなので、辺の進行方向の右手side が敷地の外になる。
    """
    (ax, ay), (bx, by) = edge.start, edge.end
    length = math.dist(edge.start, edge.end)
    nx, ny = (by - ay) / length, -(bx - ax) / length     # 外向き法線
    w = edge.road_width_mm
    return RoadStrip(
        outline=((ax, ay), (bx, by), (bx + nx * w, by + ny * w), (ax + nx * w, ay + ny * w)),
        width_mm=w,
        center_line=((ax + nx * w / 2, ay + ny * w / 2), (bx + nx * w / 2, by + ny * w / 2)),
    )


def build(result: VolumeResult) -> PlanGeometry:
    """VolumeResult から平面の幾何を組み立てる。"""
    site = result.input.site
    shape = site.shape

    unconstrained = G.buildable_region(
        shape, [0.0] * len(shape.edges), result.input.program.wall_setback_mm
    )

    return PlanGeometry(
        site_outline=G.outline(shape.polygon),
        roads=tuple(_road_strip(e) for e in shape.road_edges if e.road_width_mm > 0),
        floors=tuple(
            FloorPlan(
                floor=f.floor,
                outline=f.outline,
                area_mm2=f.gross_area_mm2,
                dominant=f.dominant_constraint,
                setback_road_mm=f.setback_road_mm,
                setback_neighbor_mm=f.setback_neighbor_mm,
            )
            for f in result.floors
        ),
        unconstrained=G.outline(unconstrained),
        north_angle_rad=site.north_angle_rad,
        frontage_mm=site.frontage_mm,
        depth_mm=site.depth_mm,
        road_width_mm=site.road_width_mm,
        is_rectangle=site.is_rectangle,
    )
