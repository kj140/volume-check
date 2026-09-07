"""断面図の幾何。

DXF出力（drawer.py）とWebのSVGプレビュー（web/svg_section.py）が
同じ形を描くための共有モジュール。ここが断面形状の唯一の定義。

座標系: (y, z) の 2 次元、単位 mm
  y = 道路から敷地奥へ向かう方向
        y = -道路幅員  … 前面道路の反対側の境界線（道路斜線の起点）
        y = 0          … 道路境界線
        y = 敷地奥行   … 反対側の隣地境界線
  z = GL からの高さ

描画範囲の上端 z_max を超える斜線はここでクリップする。クリップされたかは
road_slant_capped / neighbor_note で表現し、描画側はそれに応じて注記を変える。
"""

from __future__ import annotations

from dataclasses import dataclass

import constants as C
from models import VolumeResult

MM = C.M_TO_MM

Point = tuple[float, float]


@dataclass(frozen=True)
class FloorSection:
    """断面に現れる 1 階分の矩形。"""

    floor: int
    y_min_mm: float
    y_max_mm: float
    z_min_mm: float
    z_max_mm: float


@dataclass(frozen=True)
class SectionGeometry:
    """断面図 1 枚を描くのに必要な幾何と注記。"""

    road_width_mm: float
    depth_mm: float
    z_max_mm: float

    # 道路斜線。適用距離での頭打ちが描画範囲内なら 3 点、範囲外なら 2 点。
    road_slant: tuple[Point, ...]
    road_slant_capped: bool          # 適用距離での頭打ちが図内に現れるか
    road_gradient: float
    applicable_distance_mm: float

    # 隣地斜線。適用なし、または立ち上がりが描画範囲より上なら None。
    neighbor_slant: tuple[Point, ...] | None
    neighbor_note: str               # 斜線を描けないときに図中へ出す一文（不要なら空）
    neighbor_start_mm: float | None
    neighbor_gradient: float | None

    height_limit_mm: float | None    # 絶対高さ制限。描画範囲内のときだけ入る
    floors: tuple[FloorSection, ...]
    max_height_mm: float


def z_max(result: VolumeResult) -> float:
    """断面図の描画上端。

    原則は建物と斜線の両方が入る高さ。ただし絶対高さ制限がある地域は
    それより上に建てられないので、そこで打ち切って建物を大きく見せる。
    """
    candidates = [12.0 * MM, result.max_height_mm * 1.15]
    if result.height_limit_applied_mm is not None:
        candidates.append(result.height_limit_applied_mm * 1.25)
        return max(candidates)
    candidates.append(
        result.road_slant_applicable_distance_mm * result.road_slant_gradient * 1.05
    )
    if result.neighbor_slant_start_mm is not None:
        candidates.append(result.neighbor_slant_start_mm * 1.20)
    return max(candidates)


def build(result: VolumeResult) -> SectionGeometry:
    """VolumeResult から断面の幾何を組み立てる。"""
    site = result.input.site
    w = site.road_width_mm
    d = site.depth_mm
    top = z_max(result)

    # --- 道路斜線（法56条1項1号）------------------------------------------
    g = result.road_slant_gradient
    length = result.road_slant_applicable_distance_mm
    cap_z = length * g                      # 適用距離に達する高さ
    if cap_z <= top:
        road_slant: tuple[Point, ...] = (
            (-w, 0.0), (-w + length, cap_z), (-w + length, top),
        )
        capped = True
    else:
        # 適用距離に達する前に図の上端へ届く。頭打ちの位置は図の範囲外。
        road_slant = ((-w, 0.0), (-w + top / g, top))
        capped = False

    # --- 隣地斜線（法56条1項2号）------------------------------------------
    h0 = result.neighbor_slant_start_mm
    ng = result.neighbor_slant_gradient
    neighbor_slant: tuple[Point, ...] | None = None
    note = ""
    if h0 is None or ng is None:
        note = "隣地斜線：適用なし"
    elif h0 <= top:
        neighbor_slant = ((d, 0.0), (d, h0), (d - max(0.0, top - h0) / ng, top))
    else:
        # 立ち上がりが図の範囲より上 = この高さ範囲では建物形状を支配しない
        note = f"隣地斜線 立上り{h0 / MM:.0f}m 1:{ng}（図の範囲外）"

    # --- 絶対高さ制限（法55条 / 高度地区等）--------------------------------
    limit = result.height_limit_applied_mm
    if limit is not None and limit > top:
        limit = None

    floors = tuple(
        FloorSection(
            floor=f.floor,
            y_min_mm=f.y_min_mm,
            y_max_mm=f.y_max_mm,
            z_min_mm=f.level_mm,
            z_max_mm=f.top_mm,
        )
        for f in result.floors
    )

    return SectionGeometry(
        road_width_mm=w,
        depth_mm=d,
        z_max_mm=top,
        road_slant=road_slant,
        road_slant_capped=capped,
        road_gradient=g,
        applicable_distance_mm=length,
        neighbor_slant=neighbor_slant,
        neighbor_note=note,
        neighbor_start_mm=h0,
        neighbor_gradient=ng,
        height_limit_mm=limit,
        floors=floors,
        max_height_mm=result.max_height_mm,
    )
