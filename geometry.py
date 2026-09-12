"""敷地形状と建築可能領域の幾何。

矩形限定をやめ、任意の単純多角形の敷地を扱えるようにするための土台。
ここだけが shapely に依存する（solver は本モジュール経由で幾何を扱う）。

## 建築可能領域の考え方

法56条の斜線制限は「境界線までの水平距離」で決まる。つまり高さ h における
建築可能領域は、敷地多角形から

  ・各道路境界線について  max(0, min(h / 勾配, 適用距離) - 道路幅員)
  ・各隣地境界線について  max(0, (h - 立ち上がり) / 勾配)

の距離だけ内側に削った領域になる。「境界線からの距離」なので、辺ごとに
帯（その辺から距離 d 以内の領域）を差し引けばよい。

外壁後退は「外壁面を境界からこれだけ離す」という計画側の指定なので、斜線と
足し合わせるのではなく **大きいほうを採る**。斜線が 1.16m を要求していて外壁後退が
0.5m なら、離すべき距離は 1.16m であって 1.66m ではない。建蔽率による絞り込み
（inset）は領域全体を一律に内側へ寄せるものなので、こちらは最後に足す。

  辺ごとの帯の幅 = max(外壁後退, その辺の斜線後退) + 建蔽率の絞り込み

帯は端部を平らに切った（cap_style=flat）長方形で作る。閉じた多角形なら
隣り合う辺の帯が頂点まわりを覆うため、凸の頂点ではこれで過不足がない。
凹（reflex）の頂点だけは帯が届かない扇形が残るので、そこにだけ円を足す。
この作りにより、矩形敷地では従来の単純計算と完全に同じ結果になる。

## 座標系

敷地ローカル座標 (x, y)、単位 mm。反時計回りを正とする。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

# 呼び出し側が図形を組み立てられるよう再輸出する
__all__ = [
    "EdgeKind", "SiteEdge", "SiteShape", "Polygon", "LineString", "Point",
    "polygon_site", "rectangle_site", "buildable_region", "rotated_rectangle",
    "shrink_to_area", "largest_inscribed_rectangle", "inset_for_area",
    "projection_range", "outline", "area_mm2", "north_slant_region",
]

# 円を多角形で近似するときの分割数。頂点まわりの補正にのみ使う。
_QUAD_SEGS = 32

# 面積・長さ比較用の許容誤差（mm 系）
_AREA_EPS_MM2 = 1.0
_LENGTH_EPS_MM = 1e-6


class EdgeKind(str, Enum):
    """敷地の辺の種別。"""

    ROAD = "road"          # 道路境界線
    NEIGHBOR = "neighbor"  # 隣地境界線


@dataclass(frozen=True)
class SiteEdge:
    """敷地の 1 辺。"""

    kind: EdgeKind
    start: tuple[float, float]
    end: tuple[float, float]
    road_width_mm: float = 0.0   # kind が ROAD のときのみ意味を持つ

    @property
    def length_mm(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def angle_rad(self) -> float:
        """辺の向き（x軸からの角度）。"""
        return math.atan2(self.end[1] - self.start[1], self.end[0] - self.start[0])

    def line(self) -> LineString:
        return LineString([self.start, self.end])


@dataclass(frozen=True)
class SiteShape:
    """敷地の形と、各辺が道路か隣地かの情報。"""

    polygon: Polygon
    edges: tuple[SiteEdge, ...]

    @property
    def area_mm2(self) -> float:
        return self.polygon.area

    @property
    def road_edges(self) -> tuple[SiteEdge, ...]:
        return tuple(e for e in self.edges if e.kind is EdgeKind.ROAD)

    @property
    def widest_road(self) -> SiteEdge | None:
        """最も広い前面道路。容積率の低減（法52条2項）はこれで判定する。"""
        roads = self.road_edges
        return max(roads, key=lambda e: e.road_width_mm) if roads else None

    def edge_angles_rad(self) -> tuple[float, ...]:
        """辺の向きの一覧。建物の向きの候補に使う（重複は除く）。"""
        seen: list[float] = []
        for e in self.edges:
            a = e.angle_rad % math.pi          # 180度の重複はまとめる
            if not any(abs(a - s) < math.radians(0.5) for s in seen):
                seen.append(a)
        return tuple(seen)


# ---------------------------------------------------------------------------
# 敷地の作成
# ---------------------------------------------------------------------------


def polygon_site(boundary: list[tuple[float, float]],
                 edge_kinds: list[tuple[EdgeKind, float]]) -> SiteShape:
    """頂点列と辺ごとの種別から敷地を作る。

    boundary は閉じていない頂点列（mm）。edge_kinds は辺 i（頂点 i → i+1）の
    (種別, 道路幅員) を並べたもので、頂点数と同じ長さでなければならない。
    """
    if len(boundary) < 3:
        raise ValueError("敷地の頂点は3つ以上必要です")
    if len(edge_kinds) != len(boundary):
        raise ValueError("edge_kinds の数が敷地の辺の数と一致しません")

    polygon = Polygon(boundary)
    if not polygon.is_valid:
        raise ValueError("敷地の形状が不正です（辺が交差しているなど）")
    if polygon.area <= 0:
        raise ValueError("敷地の面積が0です")

    # 反時計回りに揃える（凹凸の判定を単純にするため）
    if not polygon.exterior.is_ccw:
        boundary = list(reversed(boundary))
        edge_kinds = list(reversed([edge_kinds[-1], *edge_kinds[:-1]]))
        polygon = Polygon(boundary)

    edges = tuple(
        SiteEdge(kind=kind, start=boundary[i], end=boundary[(i + 1) % len(boundary)],
                 road_width_mm=width)
        for i, (kind, width) in enumerate(edge_kinds)
    )
    return SiteShape(polygon=polygon, edges=edges)


def rectangle_site(frontage_mm: float, depth_mm: float, road_width_mm: float) -> SiteShape:
    """矩形敷地。ローカル座標で y = 0 が道路境界線、y が奥へ向かう。

    従来の入力（間口・奥行・道路幅員）をそのまま多角形として表す。
    """
    boundary = [
        (0.0, 0.0), (frontage_mm, 0.0),
        (frontage_mm, depth_mm), (0.0, depth_mm),
    ]
    kinds = [
        (EdgeKind.ROAD, road_width_mm),   # 下辺 = 道路境界
        (EdgeKind.NEIGHBOR, 0.0),
        (EdgeKind.NEIGHBOR, 0.0),
        (EdgeKind.NEIGHBOR, 0.0),
    ]
    return polygon_site(boundary, kinds)


# ---------------------------------------------------------------------------
# 建築可能領域
# ---------------------------------------------------------------------------


def _reflex_vertices(polygon: Polygon) -> list[tuple[float, float]]:
    """凹（内角が180度を超える）頂点。反時計回り前提。"""
    coords = list(polygon.exterior.coords)[:-1]
    n = len(coords)
    out = []
    for i in range(n):
        ax, ay = coords[i - 1]
        bx, by = coords[i]
        cx, cy = coords[(i + 1) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross < 0:                      # 反時計回りで右に曲がる = 凹
            out.append((bx, by))
    return out


def _edge_band(edge: SiteEdge, distance_mm: float) -> Polygon | None:
    """辺から距離 distance_mm 以内の帯。端部は平らに切る。"""
    if distance_mm <= _LENGTH_EPS_MM:
        return None
    return edge.line().buffer(distance_mm, cap_style="flat", join_style="mitre")


def buildable_region(
    site: SiteShape,
    setback_by_edge_mm: list[float],
    wall_setback_mm: float = 0.0,
    inset_mm: float = 0.0,
) -> Polygon | MultiPolygon:
    """各辺の後退量を差し引いた建築可能領域を返す。

    setback_by_edge_mm は辺ごとの斜線による後退量（外壁後退を含まない）。
    辺ごとの帯の幅は max(外壁後退, 斜線後退) + inset。外壁後退と斜線は
    どちらも「境界線からこれだけ離す」という条件なので、厳しいほうだけが効く。
    inset は建蔽率による全周一律の絞り込みで、これは最後に足す。

    領域が消える場合は面積0のポリゴンを返す（例外にはしない）。
    """
    if len(setback_by_edge_mm) != len(site.edges):
        raise ValueError("後退量の数が敷地の辺の数と一致しません")

    def band_mm(slant_mm: float) -> float:
        return max(wall_setback_mm, slant_mm) + inset_mm

    bands: list[Polygon] = []
    for edge, slant in zip(site.edges, setback_by_edge_mm):
        band = _edge_band(edge, band_mm(slant))
        if band is not None:
            bands.append(band)

    # 凹の頂点では隣り合う帯が扇形を覆えないので、そこだけ円で補う。
    # 凸の頂点では隣の帯が覆うため不要（矩形が厳密に一致するのはこのため）。
    for vx, vy in _reflex_vertices(site.polygon):
        radius = max(
            (band_mm(slant)
             for edge, slant in zip(site.edges, setback_by_edge_mm)
             if math.dist(edge.start, (vx, vy)) < _LENGTH_EPS_MM
             or math.dist(edge.end, (vx, vy)) < _LENGTH_EPS_MM),
            default=0.0,
        )
        if radius > _LENGTH_EPS_MM:
            bands.append(Point(vx, vy).buffer(radius, quad_segs=_QUAD_SEGS))

    if not bands:
        return site.polygon
    region = site.polygon.difference(unary_union(bands))
    return region if not region.is_empty else Polygon()


def north_slant_region(region, site: Polygon, north_angle_rad: float,
                       distance_mm: float, step_mm: float = 500.0):
    """北側斜線（法56条1項3号）で残る範囲を返す。

    道路斜線・隣地斜線が「その境界線に垂直な距離」で決まるのに対し、北側斜線は
    真北方向の水平距離で決まる。したがって辺ごとの帯では表せない。

    高さ h の点に必要な真北方向の距離を d とすると、残せるのは
    「その点から真北へ d 進んでも敷地の外に出ない点」の集合になる。これは
    敷地を真北向きの線分で収縮（erosion）したものにほかならない。

    凸な敷地では「敷地 ∩ 敷地を南へ d ずらしたもの」がそのまま答えになる。
    凹な敷地は途中でいったん敷地の外に出る経路がありうるので、step_mm 刻みの
    途中点でも敷地内であることを課す。刻みを細かくすれば真の収縮に収束する。
    """
    if distance_mm <= _LENGTH_EPS_MM or region.is_empty:
        return region

    # 真北の単位ベクトル。南へずらすので符号は反転させる。
    nx, ny = math.cos(north_angle_rad), math.sin(north_angle_rad)

    steps = [distance_mm]
    if not site.equals(site.convex_hull):
        count = max(1, int(math.ceil(distance_mm / step_mm)))
        steps = [distance_mm * k / count for k in range(1, count + 1)]

    result = region
    for d in steps:
        result = result.intersection(
            affinity.translate(site, -nx * d, -ny * d)
        )
        if result.is_empty:
            return Polygon()
    return result


def north_projection_mm(shape, site: Polygon, north_angle_rad: float) -> float:
    """図形が敷地の北端からどれだけ南へ下がっているか（真北方向の距離）。

    北側斜線の「後退量」として表示するための値。図形が空なら 0。
    """
    if shape is None or shape.is_empty or site.is_empty:
        return 0.0
    # projection_range は指定した角度に直交する向きの範囲を返すので 90 度ずらす
    axis = north_angle_rad - math.pi / 2
    _, site_north = projection_range(site, axis)
    _, shape_north = projection_range(shape, axis)
    return max(0.0, site_north - shape_north)


# ---------------------------------------------------------------------------
# 建物の配置
# ---------------------------------------------------------------------------


def rotated_rectangle(center: tuple[float, float], width_mm: float, depth_mm: float,
                      angle_rad: float) -> Polygon:
    """中心・寸法・回転角から矩形を作る。"""
    hw, hd = width_mm / 2.0, depth_mm / 2.0
    rect = Polygon([(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)])
    rect = affinity.rotate(rect, angle_rad, origin=(0, 0), use_radians=True)
    return affinity.translate(rect, center[0], center[1])


def shrink_to_area(shape: Polygon, target_area_mm2: float,
                   tolerance_mm2: float = _AREA_EPS_MM2) -> tuple[Polygon, float]:
    """面積が target 以下になるまで内側に一定量だけ縮める。

    戻り値は (縮めた形, 縮めた量[mm])。既に target 以下なら何もしない。
    建蔽率で全周を等しく絞り込むのに使う（法53条）。
    """
    if shape.is_empty or shape.area <= target_area_mm2 + tolerance_mm2:
        return shape, 0.0

    lo, hi = 0.0, math.sqrt(shape.area) / 2.0 + 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        candidate = shape.buffer(-mid, join_style="mitre")
        if candidate.is_empty or candidate.area <= target_area_mm2:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-7:            # 面積誤差にして 1e-8 m2 未満
            break
    result = shape.buffer(-hi, join_style="mitre")
    return (result if not result.is_empty else Polygon()), hi


def largest_inscribed_rectangle(region: Polygon | MultiPolygon, angle_rad: float,
                                grid_mm: float = 250.0) -> Polygon | None:
    """指定した向きで領域に収まる最大面積の矩形を探す。

    領域を -angle だけ回して軸平行にし、格子に落として
    「ヒストグラム中の最大長方形」で解く。格子刻みは既定 250mm。
    企画段階の当たりを付ける用途なので厳密解までは求めない。
    """
    if region.is_empty:
        return None

    rotated = affinity.rotate(region, -angle_rad, origin=(0, 0), use_radians=True)
    minx, miny, maxx, maxy = rotated.bounds

    # 領域そのものが、その向きの矩形（＝外接矩形と一致）なら格子探索は不要。
    # 矩形敷地が従来の計算と厳密に一致するのはこの短絡による。
    box = Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])
    if rotated.covers(box):
        return affinity.rotate(box, angle_rad, origin=(0, 0), use_radians=True)
    cols = max(1, int((maxx - minx) / grid_mm))
    rows = max(1, int((maxy - miny) / grid_mm))
    if cols * rows > 400_000:                 # 大きすぎる敷地は刻みを粗くする
        scale = math.sqrt(cols * rows / 400_000)
        grid_mm *= scale
        cols = max(1, int((maxx - minx) / grid_mm))
        rows = max(1, int((maxy - miny) / grid_mm))

    # 格子の中心が領域内かをまとめて判定する。1点ずつ shapely を呼ぶと
    # 1回の算定で数千〜数万回になり、複数案の生成が目に見えて遅くなる。
    xs = minx + (np.arange(cols) + 0.5) * grid_mm
    ys = miny + (np.arange(rows) + 0.5) * grid_mm
    gx, gy = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(rotated, gx.ravel(), gy.ravel()).reshape(rows, cols)

    best = (0.0, 0, 0, 0, 0)          # (面積, r0, c0, r1, c1)
    heights = [0] * cols
    for r in range(rows):
        row = inside[r]
        for c in range(cols):
            heights[c] = heights[c] + 1 if row[c] else 0
        for c0, c1, h in _max_rectangles(heights):
            area = (c1 - c0) * h
            if area > best[0]:
                best = (area, r - h + 1, c0, r, c1)

    if best[0] <= 0:
        return None
    _, r0, c0, r1, c1 = best
    rect = Polygon([
        (minx + c0 * grid_mm, miny + r0 * grid_mm),
        (minx + c1 * grid_mm, miny + r0 * grid_mm),
        (minx + c1 * grid_mm, miny + (r1 + 1) * grid_mm),
        (minx + c0 * grid_mm, miny + (r1 + 1) * grid_mm),
    ])
    return affinity.rotate(rect, angle_rad, origin=(0, 0), use_radians=True)


def _max_rectangles(heights: list[int]):
    """ヒストグラムに収まる極大長方形を (左, 右, 高さ) で列挙する。"""
    stack: list[tuple[int, int]] = []      # (開始列, 高さ)
    for i, h in enumerate(heights + [0]):
        start = i
        while stack and stack[-1][1] >= h:
            s, sh = stack.pop()
            if sh > 0:
                yield s, i, sh
            start = s
        stack.append((start, h))


def area_mm2(shape) -> float:
    """空でも安全に面積を返す。"""
    return 0.0 if shape is None or shape.is_empty else shape.area


def inset_for_area(area_of, target_area_mm2: float, hi_mm: float) -> float:
    """area_of(inset) が target 以下になる最小の inset を二分探索で求める。

    area_of は inset[mm] を受け取って面積[mm2]を返す単調非増加の関数。
    建蔽率の絞り込み量を求めるのに使う。
    """
    if area_of(0.0) <= target_area_mm2 + _AREA_EPS_MM2:
        return 0.0
    lo, hi = 0.0, hi_mm
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if area_of(mid) <= target_area_mm2:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-7:
            break
    return hi


def projection_range(shape, angle_rad: float) -> tuple[float, float]:
    """指定方向の軸に射影したときの範囲。断面図の切り口に使う。"""
    if shape is None or shape.is_empty:
        return (0.0, 0.0)
    rotated = affinity.rotate(shape, -angle_rad, origin=(0, 0), use_radians=True)
    minx, miny, maxx, maxy = rotated.bounds
    return (miny, maxy)


def outline(shape) -> tuple[tuple[float, float], ...]:
    """外周の頂点列（閉じない）。空なら空タプル。"""
    if shape is None or shape.is_empty:
        return ()
    if isinstance(shape, MultiPolygon):
        shape = max(shape.geoms, key=lambda g: g.area)
    return tuple((x, y) for x, y in list(shape.exterior.coords)[:-1])
