"""平面（配置図・各階平面図）の幾何。

DXF出力（drawer.py）とWebのSVG（web/svg_plan.py）が同じ形を描くための共有モジュール。
断面については section.py が同じ役割を持つ。

座標系: 敷地ローカル (x, y)、単位 mm
  x = 道路に平行な方向（間口方向）  0 .. 間口
  y = 道路から敷地奥へ向かう方向    0 .. 奥行   （y = 0 が道路境界線）

Frame が方位による回転を担う。前面道路がどちらにあっても図は「北が上」になり、
道路は指定された方位の側に描かれる。
"""

from __future__ import annotations

from dataclasses import dataclass

from models import Constraint, RoadSide, VolumeResult

Point = tuple[float, float]

# 方位ごとの、敷地ローカル座標から描画座標への回転。
#   v = 道路から敷地奥へ向かう方向（ローカル +y）
#   u = 道路に平行な方向（ローカル +x） = v を時計回りに 90 度回した向き
# たとえば道路が東にあれば敷地は道路の西側に広がるので v = (-1, 0)。
# どの方位でも北が上を向く。
ROAD_VECTORS: dict[RoadSide, tuple[float, float]] = {
    RoadSide.SOUTH: (0.0, 1.0),
    RoadSide.NORTH: (0.0, -1.0),
    RoadSide.EAST: (-1.0, 0.0),
    RoadSide.WEST: (1.0, 0.0),
}


class Frame:
    """敷地ローカル座標 (x, y) → 描画座標への変換（Y上向きの数学座標系）。

    local_bbox で与えた範囲の左下が (base_x, base_y) に来るよう平行移動する。
    SVG のように Y が下向きの系で使う場合は、描画側で最後に上下を反転させる。
    """

    def __init__(self, road_side: RoadSide, base_x: float, base_y: float,
                 local_bbox: tuple[float, float, float, float]):
        vx, vy = ROAD_VECTORS[road_side]
        self._u = (vy, -vx)
        self._v = (vx, vy)
        x0, y0, x1, y1 = local_bbox
        corners = [self._rot(x, y) for x in (x0, x1) for y in (y0, y1)]
        self._ox = base_x - min(c[0] for c in corners)
        self._oy = base_y - min(c[1] for c in corners)

    def _rot(self, x: float, y: float) -> Point:
        return (x * self._u[0] + y * self._v[0], x * self._u[1] + y * self._v[1])

    def __call__(self, x: float, y: float) -> Point:
        rx, ry = self._rot(x, y)
        return (rx + self._ox, ry + self._oy)

    def bbox(self, x0: float, y0: float, x1: float, y1: float
             ) -> tuple[float, float, float, float]:
        """ローカル矩形を変換したときの (minx, miny, maxx, maxy)。

        回転がかかるため、ラベルの位置決めはローカル座標のオフセットではなく
        この描画座標のバウンディングボックスを基準にする。
        """
        pts = [self(x, y) for x in (x0, x1) for y in (y0, y1)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class FloorPlan:
    """1 階分の平面。座標は敷地ローカル系（mm）。"""

    floor: int
    x_min_mm: float
    x_max_mm: float
    y_min_mm: float
    y_max_mm: float
    area_mm2: float
    dominant: Constraint | None
    setback_road_mm: float
    setback_neighbor_mm: float

    @property
    def width_mm(self) -> float:
        return self.x_max_mm - self.x_min_mm

    @property
    def depth_mm(self) -> float:
        return self.y_max_mm - self.y_min_mm


@dataclass(frozen=True)
class PlanGeometry:
    """配置図・各階平面図を描くのに必要な幾何。"""

    frontage_mm: float
    depth_mm: float
    road_width_mm: float
    road_side: RoadSide
    wall_setback_mm: float
    floors: tuple[FloorPlan, ...]

    @property
    def unconstrained(self) -> tuple[float, float, float, float] | None:
        """斜線・建蔽率がない場合の範囲（外壁後退のみ）。潰れる場合は None。"""
        s = self.wall_setback_mm
        if self.frontage_mm - 2 * s <= 0 or self.depth_mm - 2 * s <= 0:
            return None
        return (s, s, self.frontage_mm - s, self.depth_mm - s)


def build(result: VolumeResult) -> PlanGeometry:
    """VolumeResult から平面の幾何を組み立てる。"""
    site = result.input.site
    return PlanGeometry(
        frontage_mm=site.frontage_mm,
        depth_mm=site.depth_mm,
        road_width_mm=site.road_width_mm,
        road_side=site.road_side,
        wall_setback_mm=result.input.program.wall_setback_mm,
        floors=tuple(
            FloorPlan(
                floor=f.floor,
                x_min_mm=f.x_min_mm,
                x_max_mm=f.x_max_mm,
                y_min_mm=f.y_min_mm,
                y_max_mm=f.y_max_mm,
                area_mm2=f.gross_area_mm2,
                dominant=f.dominant_constraint,
                setback_road_mm=f.setback_road_mm,
                setback_neighbor_mm=f.setback_neighbor_mm,
            )
            for f in result.floors
        ),
    )
