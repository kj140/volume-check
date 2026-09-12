"""天空率による斜線制限の緩和可能性の判定（法56条7項・令135条の5〜10）。

## 何を出すか

斜線制限は、天空率が「適合建築物」以上であれば適用されない（法56条7項）。
本モジュールは道路斜線と隣地斜線について、

  計画建築物  斜線を外して建蔽率・容積率だけで建てた案
  適合建築物  当該境界線の斜線なりに敷地いっぱいに建てた立体
              （道路＝令135条の6、隣地＝令135条の7）

の天空率を算定位置ごとに比べ、「天空率を使えばこの案が建つ見込みがあるか」を返す。
企画段階で「天空率を検討する価値があるか」を判断するための試算であって、
確認申請に用いる判定ではない（下の「精度と適用範囲」を参照）。

適合建築物は境界線ごとに別々に作る（敷地区分方式）。道路斜線は当該前面道路の
斜線だけ、隣地斜線は当該隣地境界線の斜線だけに適合させ、他の境界の制限は課さない。

## 算定位置

道路・隣地とも「斜線面をどの点から見ても仰角が一定になる線」の上に取る。

  道路（令135条の9第1項）
      前面道路の反対側の境界線上。境界線からの距離＝道路幅員。
      間隔は幅員の 1/2 以下。高さは前面道路の路面の中心＝本モデルの GL。

  隣地（令135条の10）
      隣地境界線から「立ち上がり ÷ 勾配」だけ外側の線上。
      住居系（20m・1.25）で 16m、それ以外（31m・2.5）で 12.4m。
      間隔はその距離の 1/2 以下（8m / 6.2m）。高さは平均地盤面＝本モデルの GL。

## 天空率の算定（令135条の5）

天空率 = (As - Ab) / As
  As  半径 R の半球を水平面に正射影した円の面積 = πR^2
  Ab  建築物を同じく正射影した面積

算定位置から見て方位角 θ の方向に建築物が仰角 φmax(θ) まで見えるとき、
その方向の正射影半径は cos(φmax) から 1 までが塞がれるので

  Ab / As = (1/2π) ∫ sin^2(φmax(θ)) dθ

建物は地盤面に建ち、算定位置も同じ高さにあるので、どの方位でも建築物の
シルエットは地平線から連続する（上階が下階を超えない形状のため穴が開かない）。
したがって仰角の最大値だけを追えばよい。

天端 h、水平距離 d の面の仰角は atan(h/d) なので sin^2 = h^2 / (h^2 + d^2)。
三角関数を使わずに済む。

## 精度と適用範囲

  ・北側斜線（令135条の8・11）は未対応。北側斜線がかかる用途地域では
    別途の検討が要る。
  ・適合建築物の高さは、道路・隣地とも計画建築物の最高高さで頭打ちにする。
    道路斜線では適用距離を超える範囲に制限がなく、隣地斜線では敷地の奥ほど
    包絡線が高くなるため、上限を置かないと適合建築物が計画建築物より
    はるかに大きくなってしまう。適合建築物を小さく採る側なので判定は厳しめ。
  ・適合建築物は高さ方向を層に刻んだ立体で近似する。各層は真の斜線包絡線の
    内側に収まるので、これも適合建築物を小さめに見積もる側に働く。
  ・方位角は等分割で数値積分する。分割数は _AZIMUTH_DIVISIONS。
  ・法56条4項の後退距離による緩和、令132条の2以上の前面道路の緩和、
    法56条5項の高低差による隣地斜線の緩和は未考慮。
  ・高低差区分区域（令135条の7第2項等）は扱わない。敷地は平坦とみなす。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import constants as C
import geometry as G
from models import VolumeInput, VolumeResult
from solver import neighbor_setback, road_setback

# 方位角の分割数。0.125度刻み。天空率は計画・適合の差で判断するため、
# 同じ分割数を使えば数値積分の誤差は大きく打ち消し合う。
_AZIMUTH_DIVISIONS = 2880

# 適合建築物を刻む高さ方向の層厚[mm]。
#
# 粗くても答えが変わらない。道路斜線の斜面は「反対側の境界線からの水平距離 × 勾配」
# なので、その境界線上にある算定位置から見ると斜面上のどの点も仰角が同じ
# （tan = 勾配）になる。刻み方によらず仰角の最大値は変わらず、効くのは頭打ちの
# 平らな部分だけで、それは最上層がそのまま表す。層厚を250mmから3000mmまで
# 振っても余裕は小数第4位まで一致することを確認している。
_ENVELOPE_LAYER_MM = 1000.0

# 天空率の比較の許容差。数値積分の誤差の範囲を「同等」とみなす幅。
_SKY_FACTOR_EPS = 1e-5

# 隣地の適合建築物の高さを、計画建築物の最高高さで頭打ちにするか。
#
# 道路（令135条の6）は、適用距離を超える範囲の高さを計画建築物の最高高さと
# するのが条文の建て付けで、これは動かない。隣地（令135条の7）は隣地斜線が
# 敷地全体にかかるため「適用距離の外」に当たる部分がなく、頭打ちの規定を
# 置くべきかが条文から一義に読み取れなかった。
#
# ここでは頭打ちにする側を既定とする。適合建築物を小さく採る＝その天空率を
# 大きく採ることになり、判定は厳しい側に倒れるため。頭打ちにしない読み方を
# 採れば適合建築物は大きくなり、余裕は必ず今より広がる（compliant_is_larger
# ... のテストで担保している）。つまり本ツールが出す余裕は下限であって、
# 「通る」と出たならどちらの読み方でも通る。
NEIGHBOR_ENVELOPE_CAPPED = True

# 「どれだけ後退させれば通るか」の探索。刻みと上限の段数、探索中の方位分割。
_SEARCH_STEP_MM = 500.0
_SEARCH_STEPS = 10
_SEARCH_DIVISIONS = 720

_LENGTH_EPS_MM = 1e-6
_AREA_EPS_MM2 = 1.0


# ---------------------------------------------------------------------------
# 立体の表し方
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Slab:
    """地盤面から天端 top_mm まで立ち上がる、水平断面 polygon のかたまり。

    建築物はこれを重ねて表す。上の層が下の層からはみ出さない前提。
    """

    polygon: G.Polygon
    top_mm: float


@dataclass(frozen=True)
class PointCheck:
    """1 つの算定位置での比較結果。"""

    position: tuple[float, float]   # 敷地ローカル座標[mm]
    planned: float                  # 計画建築物の天空率（0〜1）
    compliant: float                # 適合建築物の天空率（0〜1）

    @property
    def margin(self) -> float:
        """計画 - 適合。0 以上なら斜線制限の適用を受けない。"""
        return self.planned - self.compliant

    @property
    def passes(self) -> bool:
        return self.margin >= -_SKY_FACTOR_EPS

    def to_dict(self) -> dict:
        return {
            "x_m": round(self.position[0] / C.M_TO_MM, 3),
            "y_m": round(self.position[1] / C.M_TO_MM, 3),
            "planned_pct": round(self.planned * 100.0, 3),
            "compliant_pct": round(self.compliant * 100.0, 3),
            "margin_pct": round(self.margin * 100.0, 3),
            "passes": self.passes,
        }


@dataclass
class EdgeCheck:
    """1 つの境界線（前面道路または隣地境界）についての判定。"""

    edge_index: int
    kind: G.EdgeKind
    offset_mm: float          # 境界線から算定位置の線までの水平距離
    points: list[PointCheck] = field(default_factory=list)

    @property
    def is_road(self) -> bool:
        return self.kind is G.EdgeKind.ROAD

    @property
    def slant_name(self) -> str:
        return "道路斜線" if self.is_road else "隣地斜線"

    @property
    def label(self) -> str:
        """表や図に出す見出し。"""
        if self.is_road:
            return f"前面道路 W={self.offset_mm / C.M_TO_MM:.1f}m"
        return f"隣地境界（算定線 {self.offset_mm / C.M_TO_MM:.1f}m 外側）"

    @property
    def passes(self) -> bool:
        return bool(self.points) and all(p.passes for p in self.points)

    @property
    def worst(self) -> PointCheck | None:
        return min(self.points, key=lambda p: p.margin) if self.points else None

    def to_dict(self) -> dict:
        worst = self.worst
        return {
            "edge_index": self.edge_index,
            "kind": self.kind.value,
            "slant": self.slant_name,
            "label": self.label,
            "offset_m": round(self.offset_mm / C.M_TO_MM, 2),
            "passes": self.passes,
            "worst_margin_pct": round(worst.margin * 100.0, 3) if worst else None,
            "points": [p.to_dict() for p in self.points],
        }


@dataclass(frozen=True)
class Suggestion:
    """通るようになる案（外壁後退を増やしたもの）。"""

    wall_setback_mm: float
    floor_count: int
    max_height_mm: float
    total_gross_area_mm2: float
    worst_margin: float
    outline: tuple[tuple[float, float], ...] = ()

    def to_dict(self) -> dict:
        return {
            "wall_setback_m": round(self.wall_setback_mm / C.M_TO_MM, 2),
            "floor_count": self.floor_count,
            "max_height_m": round(self.max_height_mm / C.M_TO_MM, 2),
            "total_gross_area_m2": round(self.total_gross_area_mm2 / C.M2_TO_MM2, 2),
            "worst_margin_pct": round(self.worst_margin * 100.0, 3),
        }


@dataclass
class SkyFactorStudy:
    """天空率の検討結果。"""

    edges: list[EdgeCheck] = field(default_factory=list)

    # 計画建築物（斜線を外した案）の規模
    planned_floor_count: int = 0
    planned_max_height_mm: float = 0.0
    planned_total_gross_area_mm2: float = 0.0
    planned_outline: tuple[tuple[float, float], ...] = ()   # 全階共通の外形

    # 斜線を守った案（solve() の結果）との差
    base_total_gross_area_mm2: float = 0.0
    base_max_height_mm: float = 0.0

    # 計画建築物に用いた外壁後退。通る案を探したときはその値になる。
    wall_setback_mm: float = 0.0

    # 通らなかったとき、外壁後退を増やして通るようになる案
    suggestion: Suggestion | None = None
    suggestion_searched: bool = False       # 探索を実際に行ったか

    verdict: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def gain_mm2(self) -> float:
        """天空率が通った場合に増える延床面積。"""
        return self.planned_total_gross_area_mm2 - self.base_total_gross_area_mm2

    @property
    def worth_studying(self) -> bool:
        """天空率を検討する価値があるか（増える床があるか）。"""
        return self.gain_mm2 > _AREA_EPS_MM2

    @property
    def passes(self) -> bool:
        """すべての境界の、すべての算定位置で適合建築物以上か。"""
        return bool(self.edges) and all(e.passes for e in self.edges)

    @property
    def worst_margin(self) -> float | None:
        margins = [e.worst.margin for e in self.edges if e.worst is not None]
        return min(margins) if margins else None

    def failing(self) -> list[EdgeCheck]:
        """通らなかった境界（余裕の小さい順）。"""
        return sorted((e for e in self.edges if not e.passes),
                      key=lambda e: e.worst.margin if e.worst else 0.0)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "worth_studying": self.worth_studying,
            "passes": self.passes,
            "worst_margin_pct": (round(self.worst_margin * 100.0, 3)
                                 if self.worst_margin is not None else None),
            "gain_m2": round(self.gain_mm2 / C.M2_TO_MM2, 2),
            "planned": {
                "floor_count": self.planned_floor_count,
                "max_height_m": round(self.planned_max_height_mm / C.M_TO_MM, 2),
                "total_gross_area_m2": round(
                    self.planned_total_gross_area_mm2 / C.M2_TO_MM2, 2),
                "wall_setback_m": round(self.wall_setback_mm / C.M_TO_MM, 2),
            },
            "suggestion": self.suggestion.to_dict() if self.suggestion else None,
            "base": {
                "max_height_m": round(self.base_max_height_mm / C.M_TO_MM, 2),
                "total_gross_area_m2": round(
                    self.base_total_gross_area_mm2 / C.M2_TO_MM2, 2),
            },
            "edges": [e.to_dict() for e in self.edges],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# 天空率の算定
# ---------------------------------------------------------------------------


def _segments(shape) -> np.ndarray:
    """図形の輪郭を線分の配列 (M, 4) = (x1, y1, x2, y2) にばらす。"""
    if shape is None or shape.is_empty:
        return np.zeros((0, 4))
    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    rings = []
    for g in geoms:
        if g.is_empty:
            continue
        rings.append(np.asarray(g.exterior.coords))
        rings.extend(np.asarray(r.coords) for r in g.interiors)
    if not rings:
        return np.zeros((0, 4))
    out = [np.hstack([r[:-1], r[1:]]) for r in rings if len(r) >= 2]
    return np.vstack(out) if out else np.zeros((0, 4))


def _near_distance(shape, px: float, py: float,
                   ux: np.ndarray, uy: np.ndarray) -> np.ndarray:
    """算定位置から各方位へ伸ばした半直線が図形に最初に当たるまでの距離。

    当たらない方位は inf。図形の内側に算定位置がある場合は 0 を返す。
    """
    segs = _segments(shape)
    if len(segs) == 0:
        return np.full(ux.shape, np.inf)
    if shape.covers(G.Point(px, py)):
        return np.zeros(ux.shape)

    x1, y1, x2, y2 = segs.T
    dx, dy = x2 - x1, y2 - y1
    wx, wy = x1 - px, y1 - py

    # 半直線 P + t*u と線分 p + s*d の交点。t = (w×d)/(u×d), s = (w×u)/(u×d)
    denom = ux[:, None] * dy[None, :] - uy[:, None] * dx[None, :]
    safe = np.where(np.abs(denom) > 1e-12, denom, np.nan)
    t = (wx[None, :] * dy[None, :] - wy[None, :] * dx[None, :]) / safe
    s = (wx[None, :] * uy[:, None] - wy[None, :] * ux[:, None]) / safe

    valid = np.isfinite(t) & (t > 0.0) & (s >= 0.0) & (s <= 1.0)
    return np.where(valid, t, np.inf).min(axis=1)


def sky_factor(slabs: list[Slab], position: tuple[float, float],
               divisions: int = _AZIMUTH_DIVISIONS) -> float:
    """算定位置から見た天空率（0〜1）。何も建っていなければ 1.0（令135条の5）。

    position は地盤面上の点（敷地ローカル座標[mm]）。道路斜線の算定位置は
    前面道路の路面の中心の高さなので、本モデルでは GL と同じ高さになる。
    """
    theta = (np.arange(divisions) + 0.5) * (2.0 * math.pi / divisions)
    ux, uy = np.cos(theta), np.sin(theta)
    px, py = position

    blocked = np.zeros(divisions)
    for slab in slabs:
        if slab.top_mm <= _LENGTH_EPS_MM or slab.polygon.is_empty:
            continue
        d = _near_distance(slab.polygon, px, py, ux, uy)
        h2 = slab.top_mm ** 2
        with np.errstate(invalid="ignore"):
            # sin^2(atan(h/d)) = h^2 / (h^2 + d^2)。d = inf なら 0。
            fraction = np.where(np.isinf(d), 0.0, h2 / (h2 + d * d))
        blocked = np.maximum(blocked, fraction)

    return float(1.0 - blocked.mean())


# ---------------------------------------------------------------------------
# 適合建築物（道路＝令135条の6 / 隣地＝令135条の7）
# ---------------------------------------------------------------------------


def slant_applies(result: VolumeResult, edge_index: int) -> bool:
    """その境界線に斜線制限がかかるか。

    隣地斜線は低層住専・田園住居では適用がない（法56条1項2号・法55条による）。
    """
    if result.input.site.shape.edges[edge_index].kind is G.EdgeKind.ROAD:
        return True
    return result.neighbor_slant_start_mm is not None


def measurement_offset_mm(result: VolumeResult, edge_index: int) -> float:
    """境界線から算定位置の線までの水平距離。

    道路は道路幅員（＝反対側の境界線まで／令135条の9第1項）、
    隣地は「立ち上がり ÷ 勾配」（令135条の10）。どちらの線からも斜線面は
    仰角が一定に見える。
    """
    edge = result.input.site.shape.edges[edge_index]
    if edge.kind is G.EdgeKind.ROAD:
        return edge.road_width_mm
    start_mm, gradient = result.neighbor_slant_start_mm, result.neighbor_slant_gradient
    if start_mm is None or gradient is None:
        return 0.0
    return start_mm / gradient


def _envelope_setback_mm(result: VolumeResult, edge_index: int, z_mm: float) -> float:
    """高さ z における、その境界線からの後退量。斜線の式は solver と同じもの。"""
    edge = result.input.site.shape.edges[edge_index]
    if edge.kind is G.EdgeKind.ROAD:
        setback, _ = road_setback(z_mm, edge.road_width_mm,
                                  result.road_slant_gradient,
                                  result.road_slant_applicable_distance_mm)
        return setback
    return neighbor_setback(z_mm, result.neighbor_slant_start_mm,
                            result.neighbor_slant_gradient)


def envelope_top_mm(result: VolumeResult, edge_index: int, planned_top_mm: float,
                    capped: bool = NEIGHBOR_ENVELOPE_CAPPED) -> float:
    """適合建築物の天端高さ。

    道路は常に計画建築物の最高高さ（令135条の6第1項）。隣地は既定で同じ扱いに
    するが、capped=False なら斜線の包絡線をそのまま伸ばし、敷地内で最も遠い点の
    高さまでを適合建築物とする（NEIGHBOR_ENVELOPE_CAPPED のコメントを参照）。
    """
    edge = result.input.site.shape.edges[edge_index]
    if capped or edge.kind is G.EdgeKind.ROAD:
        return planned_top_mm
    start_mm, gradient = result.neighbor_slant_start_mm, result.neighbor_slant_gradient
    if start_mm is None or gradient is None:
        return planned_top_mm
    line = edge.line()
    reach = max(line.distance(G.Point(x, y))
                for x, y in G.outline(result.input.site.shape.polygon))
    return max(planned_top_mm, start_mm + gradient * reach)


def compliant_slabs(result: VolumeResult, edge_index: int, top_mm: float,
                    layer_mm: float = _ENVELOPE_LAYER_MM) -> list[Slab]:
    """当該境界線の斜線なりに敷地いっぱいに建てた立体（令135条の6・7）。

    高さ top_mm までを層に刻む。top_mm には計画建築物の最高高さを渡す。
    道路斜線は適用距離を超える範囲に制限がなく、隣地斜線は敷地の奥ほど包絡線が
    高くなるので、どちらも上限を置かないと適合建築物が青天井になる。

    課すのは当該境界線の斜線だけで、他の境界の制限は課さない（敷地区分方式）。
    各層は真の包絡線の内側に収まるため、適合建築物は小さめ＝その天空率は
    大きめに出る。判定は厳しい側に倒れる。
    """
    site = result.input.site.shape

    # 後退量が同じ層は同じ形なので、その中で最も高い天端だけを残す。
    # 立ち上がりより下（隣地）や適用距離の内側（道路）は後退量が 0 のまま
    # 変わらないので、ここで層数がまとまって減る。結果は変わらない。
    highest_by_setback: dict[float, float] = {}
    z = layer_mm
    while z < top_mm + layer_mm:
        z = min(z, top_mm)
        highest_by_setback[_envelope_setback_mm(result, edge_index, z)] = z
        if z >= top_mm:
            break
        z += layer_mm

    slabs: list[Slab] = []
    for setback_mm, top in sorted(highest_by_setback.items(), key=lambda kv: kv[1]):
        setbacks = [0.0] * len(site.edges)
        setbacks[edge_index] = setback_mm
        region = G.buildable_region(site, setbacks, 0.0)
        if not region.is_empty:
            slabs.append(Slab(polygon=region, top_mm=top))
    return slabs


def measurement_positions(result: VolumeResult, edge_index: int
                          ) -> list[tuple[float, float]]:
    """算定位置（道路＝令135条の9第1項 / 隣地＝令135条の10）。

    境界線を外側へ measurement_offset_mm だけ平行移動した線の上に、
    その距離の 1/2 以下の等間隔で並べる（両端を含む）。高さは道路なら
    前面道路の路面の中心、隣地なら平均地盤面で、どちらも本モデルの GL。
    """
    site = result.input.site.shape
    edge = site.edges[edge_index]
    (ax, ay), (bx, by) = edge.start, edge.end
    length = math.dist(edge.start, edge.end)
    offset = measurement_offset_mm(result, edge_index)
    if length <= _LENGTH_EPS_MM or offset <= _LENGTH_EPS_MM:
        return []

    # 反時計回りの多角形では、進行方向の右手が敷地の外側になる。
    nx, ny = (by - ay) / length, -(bx - ax) / length
    ox, oy = ax + nx * offset, ay + ny * offset          # 算定線の始点

    ratio = (C.SKY_FACTOR_ROAD_POINT_INTERVAL_RATIO
             if edge.kind is G.EdgeKind.ROAD
             else C.SKY_FACTOR_NEIGHBOR_POINT_INTERVAL_RATIO)
    count = max(1, math.ceil(length / (offset * ratio) - 1e-9))
    return [
        (ox + (bx - ax) * i / count, oy + (by - ay) * i / count)
        for i in range(count + 1)
    ]


# ---------------------------------------------------------------------------
# 計画建築物（斜線を外した案）
# ---------------------------------------------------------------------------


def planned_slabs(result: VolumeResult, wall_setback_mm: float | None = None
                  ) -> tuple[list[Slab], int, float, float]:
    """斜線制限を外し、建蔽率・容積率・絶対高さだけで建てた案を組み立てる。

    戻り値は (立体, 階数, 最高高さ[mm], 延床面積[mm2])。

    形は「全階同一平面の箱」。斜線を天空率で外す狙いはまさに上階の後退をなくす
    ことなので、比較する計画建築物もその形にする。階数は容積率を使い切る最小の
    階数とし、板の大きさを容積率ちょうどに絞る。階数を増やせば板は小さく高さは
    高くなるだけで、天空率にも延床にも不利になる。

    wall_setback_mm を与えると外壁後退だけを差し替えて組み立てる（後退を増やすと
    通るようになるかを探すのに使う）。
    """
    inp: VolumeInput = result.input
    site, program = inp.site, inp.program
    zero = [0.0] * len(site.shape.edges)
    wall_mm = program.wall_setback_mm if wall_setback_mm is None else wall_setback_mm

    region = G.buildable_region(site.shape, zero, wall_mm)
    footprint = G.largest_inscribed_rectangle(region, result.building_angle_rad) or region

    def area_at(inset_mm: float) -> float:
        shrunk = G.buildable_region(site.shape, zero, wall_mm, inset_mm)
        return G.area_mm2(shrunk.intersection(footprint))

    # 建蔽率いっぱいの板（法53条）
    inset_mm = G.inset_for_area(
        area_at, result.max_building_area_mm2, math.sqrt(site.area_mm2) / 2.0 + 1.0
    )
    plate = G.buildable_region(
        site.shape, zero, wall_mm, inset_mm
    ).intersection(footprint)
    max_area_mm2 = G.area_mm2(plate)
    min_area_mm2 = C.MIN_VIABLE_FLOOR_AREA_M2 * C.M2_TO_MM2
    if max_area_mm2 < min_area_mm2:
        return [], 0, 0.0, 0.0

    # 絶対高さ制限・最大階数で積める上限の階数
    limit_mm = result.height_limit_applied_mm
    level_mm = 0.0
    max_count = 0
    for floor in range(1, program.max_floors + 1):
        top_mm = level_mm + program.height_of_floor_mm(floor)
        if limit_mm is not None and top_mm > limit_mm + _LENGTH_EPS_MM:
            break
        level_mm = top_mm
        max_count = floor
    if max_count == 0:
        return [], 0, 0.0, 0.0

    # 容積率を使い切る最小の階数
    count = min(max_count,
                max(1, math.ceil(result.max_far_area_mm2 / max_area_mm2 - 1e-9)))
    target_mm2 = min(max_area_mm2, result.max_far_area_mm2 / count)
    if target_mm2 < max_area_mm2 - _AREA_EPS_MM2:
        plate, _ = G.shrink_to_area(plate, target_mm2)
    area_mm2 = G.area_mm2(plate)
    if area_mm2 < min_area_mm2:
        return [], 0, 0.0, 0.0

    height_mm = sum(program.height_of_floor_mm(f) for f in range(1, count + 1))
    return [Slab(polygon=plate, top_mm=height_mm)], count, height_mm, count * area_mm2


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


def evaluate(result: VolumeResult,
             divisions: int = _AZIMUTH_DIVISIONS,
             layer_mm: float = _ENVELOPE_LAYER_MM,
             wall_setback_mm: float | None = None,
             suggest: bool = True,
             neighbor_capped: bool = NEIGHBOR_ENVELOPE_CAPPED) -> SkyFactorStudy:
    """天空率で道路斜線を緩和できる見込みがあるかを判定する（法56条7項1号）。"""
    study = SkyFactorStudy(
        base_total_gross_area_mm2=result.total_gross_area_mm2,
        base_max_height_mm=result.max_height_mm,
        wall_setback_mm=(result.input.program.wall_setback_mm
                         if wall_setback_mm is None else wall_setback_mm),
    )
    slabs, count, height_mm, total_mm2 = planned_slabs(result, wall_setback_mm)
    study.planned_floor_count = count
    study.planned_max_height_mm = height_mm
    study.planned_total_gross_area_mm2 = total_mm2
    study.planned_outline = G.outline(slabs[0].polygon) if slabs else ()

    if not slabs:
        study.verdict = "計画建築物が成立しないため、天空率の検討対象になりません。"
        return study

    if not study.worth_studying:
        study.verdict = (
            "斜線制限を外しても延床面積は増えません"
            "（容積率または絶対高さで頭打ちになっています）。"
            "天空率を検討する必要はありません。"
        )
        _add_notes(study, result)
        return study

    for i, edge in enumerate(result.input.site.shape.edges):
        if not slant_applies(result, i):
            continue
        check = EdgeCheck(edge_index=i, kind=edge.kind,
                          offset_mm=measurement_offset_mm(result, i))
        compliant = compliant_slabs(
            result, i, envelope_top_mm(result, i, height_mm, neighbor_capped),
            layer_mm)
        for position in measurement_positions(result, i):
            check.points.append(PointCheck(
                position=position,
                planned=sky_factor(slabs, position, divisions),
                compliant=sky_factor(compliant, position, divisions),
            ))
        if check.points:
            study.edges.append(check)

    if suggest and not study.passes:
        study.suggestion_searched = True
        study.suggestion = search_wall_setback(result, layer_mm)
    _set_verdict(study)
    _add_notes(study, result)
    return study


def search_wall_setback(result: VolumeResult,
                        layer_mm: float = _ENVELOPE_LAYER_MM
                        ) -> Suggestion | None:
    """外壁後退を増やして天空率が通るようになる最小の値を探す。

    後退を増やすと板は小さくなるが、境界から離れる分だけ天空率は上がる。
    容積率を使い切る階数が増えるので延床は落ちにくい。探索中は方位分割を
    粗くして時間を抑える（採否の判断は最終値で作り直したものを使う）。
    """
    base_mm = result.input.program.wall_setback_mm
    for i in range(1, _SEARCH_STEPS + 1):
        setback_mm = base_mm + i * _SEARCH_STEP_MM
        trial = evaluate(result, divisions=_SEARCH_DIVISIONS, layer_mm=layer_mm,
                         wall_setback_mm=setback_mm, suggest=False)
        if trial.passes and trial.worth_studying:
            return Suggestion(
                wall_setback_mm=setback_mm,
                floor_count=trial.planned_floor_count,
                max_height_mm=trial.planned_max_height_mm,
                total_gross_area_mm2=trial.planned_total_gross_area_mm2,
                worst_margin=trial.worst_margin or 0.0,
                outline=trial.planned_outline,
            )
    return None


def _set_verdict(study: SkyFactorStudy) -> None:
    gain_m2 = study.gain_mm2 / C.M2_TO_MM2
    worst = study.worst_margin
    if not study.edges or worst is None:
        study.verdict = "斜線制限のかかる境界線がないため判定できません。"
        return

    kinds = sorted({e.slant_name for e in study.edges})
    if study.passes:
        study.verdict = (
            f"天空率で{'と'.join(kinds)}を緩和できる見込みがあります。"
            f"すべての算定位置で計画建築物の天空率が適合建築物を上回り、"
            f"最も不利な位置でも {worst * 100:+.2f} ポイントの余裕があります。"
            f"通れば延床は {gain_m2:,.1f}m2 増え、"
            f"高さは {study.planned_max_height_mm / C.M_TO_MM:.2f}m になります。"
        )
        return

    ng = sum(1 for e in study.edges for p in e.points if not p.passes)
    total = sum(len(e.points) for e in study.edges)
    failing = study.failing()
    where = "・".join(sorted({e.slant_name for e in failing}))
    study.verdict = (
        f"全階同じ大きさの箱のままでは天空率でも通りません。"
        f"{total} か所の算定位置のうち {ng} か所で適合建築物を下回り"
        f"（{where}）、"
        f"最も不利な位置で {worst * 100:+.2f} ポイント足りません"
        f"（通れば延床 {gain_m2:,.1f}m2 の上積み）。"
    )
    s = study.suggestion
    if s is not None:
        study.verdict += (
            f" 外壁後退を {s.wall_setback_mm / C.M_TO_MM:.1f}m まで広げると"
            f"{s.floor_count}階・{s.max_height_mm / C.M_TO_MM:.2f}m・"
            f"延床 {s.total_gross_area_mm2 / C.M2_TO_MM2:,.1f}m2 で通る見込みです"
            f"（最も不利な位置で {s.worst_margin * 100:+.2f} ポイント）。"
        )
    elif study.suggestion_searched:
        study.verdict += (
            f" 外壁後退を {_SEARCH_STEPS * _SEARCH_STEP_MM / C.M_TO_MM:.0f}m"
            f"広げる範囲では通る案が見つかりませんでした。"
            f"上階を後退させる・建物を振るなどの検討が必要です。"
        )


def _add_notes(study: SkyFactorStudy, result: VolumeResult) -> None:
    study.notes.append(
        "天空率は令135条の5の定義（半球の正射影面積の比）により算定。"
        "方位角を等分割した数値積分で、企画段階の試算値です。"
    )
    study.notes.append(
        "適合建築物は境界線ごとに、その境界線の斜線なりに敷地いっぱいに建てた"
        "立体としています（道路＝令135条の6、隣地＝令135条の7）。他の境界の"
        "制限は課しません（敷地区分方式）。"
    )
    study.notes.append(
        "適合建築物の高さは計画建築物の最高高さで頭打ちにしています。"
        "道路斜線は適用距離を超える範囲に制限がなく、隣地斜線は敷地の奥ほど"
        "包絡線が高くなるためで、適合建築物を小さく採る＝判定は厳しい側です。"
    )
    study.notes.append(
        "算定位置は、道路が前面道路の反対側の境界線上で幅員の1/2以下の等間隔"
        "（令135条の9第1項）、隣地が境界線から立ち上がり÷勾配だけ外側の線上で"
        "その距離の1/2以下の等間隔（令135条の10）。高さはいずれも地盤面です。"
    )
    study.notes.append(
        "計画建築物は斜線制限だけを外し、建蔽率・容積率・絶対高さは満たした案です。"
    )
    study.notes.append(
        "北側斜線（令135条の8・11）の天空率は未対応。法56条4項の後退距離による"
        "緩和、令132条の2以上の前面道路の緩和、法56条5項の高低差による隣地斜線の"
        "緩和も未考慮。敷地は平坦とみなし、高低差区分区域は扱いません。"
    )
    study.notes.append(
        "確認申請の判定は専用ソフトによる算定が必要です。本結果は"
        "「天空率を検討する価値があるか」を見るためのものです。"
    )
    if any(not e.passes and not e.is_road for e in study.edges):
        study.notes.append(
            "隣地側は、適合建築物の高さを計画建築物の最高高さで頭打ちにした"
            "厳しい側で判定しています。頭打ちにしない読み方なら適合建築物は"
            "大きくなり、余裕は必ず今より広がります。隣地側だけで落ちている案は"
            "実際には通る可能性があります。"
        )
    if result.neighbor_slant_start_mm is None:
        study.notes.append(
            f"{result.input.zoning.use_district.value}は隣地斜線の適用がないため"
            f"（法55条の絶対高さ制限による）、道路斜線だけを判定しています。"
        )
