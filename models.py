"""入出力のデータモデル。

単位の約束:
  ・入力JSONは m（メートル）
  ・内部計算・DXF出力は mm
  ・換算は VolumeInput.from_dict() の入口で 1 回だけ行う
  ・mm の値を持つフィールドには必ず _mm / _mm2 のサフィックスを付ける

座標系（solver / drawer 共通のローカル系）:
  x = 道路に平行な方向（間口方向）
  y = 道路から敷地奥へ向かう方向（y = 0 が前面道路の境界線）
  z = 高さ                          0 = GL
  図面での方位への回転は north_angle_rad（ローカル座標における北の向き）で行う。

敷地形状:
  矩形は frontage/depth/road_width/road_side から作る。任意形状は boundary と
  edges（辺ごとの道路/隣地の別）で与える。どちらの場合も SiteInput.shape が
  多角形としての敷地（geometry.SiteShape）を持ち、solver はそれだけを見る。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import geometry as G
from constants import M2_TO_MM2, M_TO_MM, FireZone, UseDistrict


class RoadSide(str, Enum):
    """前面道路がどの方位にあるか。作図の向きの決定にのみ使う。"""

    NORTH = "north"
    EAST = "east"
    SOUTH = "south"
    WEST = "west"


class Constraint(str, Enum):
    """その階の形状を削っている規定。"""

    ROAD_SLANT = "道路斜線"
    NEIGHBOR_SLANT = "隣地斜線"
    BCR = "建蔽率"

    @property
    def basis(self) -> str:
        return {
            Constraint.ROAD_SLANT: "法56条1項1号",
            Constraint.NEIGHBOR_SLANT: "法56条1項2号",
            Constraint.BCR: "法53条",
        }[self]


NO_CONSTRAINT_LABEL = "敷地形状・外壁後退のみ"


@dataclass(frozen=True)
class ConstraintImpact:
    """ある規定が「その階の床面積をどれだけ削っているか」。

    その規定 **だけ** を外したときに増える床面積（限界寄与）で表す。
    複数の規定が同時にかかっている場合、各値の合計は実際の減少量とは一致しない
    （制約どうしが掛け算で効くため）。「この制限が外れたらどれだけ増えるか」を
    見るための値であり、内訳の分解ではない。
    """

    constraint: Constraint
    area_gain_mm2: float          # その規定を外したときの床面積の増分
    setback_mm: float             # その規定による後退量（建蔽率は絞り込み量）


class StopReason(str, Enum):
    """階の積み上げを打ち切った理由。"""

    NO_EFFECTIVE_FOOTPRINT = "有効間口または有効奥行が0以下"
    BELOW_MIN_FLOOR_AREA = "床面積が最小成立面積未満"
    FAR_LIMIT_REACHED = "容積率の上限に到達"
    ABSOLUTE_HEIGHT_LIMIT = "絶対高さ制限に到達"
    MAX_FLOORS_REACHED = "max_floors に到達"


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


# ローカル座標における北の向き（+x 軸からの角度・ラジアン）。
# 前面道路の方位から決まる。道路が南にあれば敷地はその北側に広がるので +y が北。
_NORTH_ANGLE_BY_ROAD_SIDE: dict[RoadSide, float] = {
    RoadSide.SOUTH: math.pi / 2,      # +y が北
    RoadSide.NORTH: -math.pi / 2,     # -y が北
    RoadSide.EAST: 0.0,               # +x が北
    RoadSide.WEST: math.pi,           # -x が北
}


@dataclass(frozen=True)
class SiteInput:
    """敷地条件。矩形でも任意の単純多角形でも扱える。"""

    shape: G.SiteShape           # 多角形としての敷地（solver はこれだけを見る）
    road_side: RoadSide          # 主要な前面道路の方位（作図の向きに使う）
    north_angle_rad: float
    corner_lot: bool = False     # 法53条3項2号の角地指定
    is_rectangle: bool = True    # 従来の矩形入力から作られたか

    @property
    def area_mm2(self) -> float:
        return self.shape.area_mm2

    @property
    def road_edge(self) -> G.SiteEdge:
        """容積率の低減と断面の切り口に使う前面道路（最大幅員）。"""
        edge = self.shape.widest_road
        if edge is None:
            raise ValueError("道路に接する辺がありません")
        return edge

    @property
    def road_width_mm(self) -> float:
        return self.road_edge.road_width_mm

    @property
    def frontage_mm(self) -> float:
        """間口。前面道路に接する辺の長さ。"""
        return self.road_edge.length_mm

    @property
    def depth_mm(self) -> float:
        """奥行。前面道路の境界線から最も遠い敷地点までの距離。"""
        _, far = G.projection_range(self.shape.polygon, self.road_edge.angle_rad)
        near, _ = G.projection_range(self.road_edge.line(), self.road_edge.angle_rad)
        return far - near

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SiteInput:
        road_side = RoadSide(d.get("road_side", RoadSide.SOUTH.value))
        corner_lot = bool(d.get("corner_lot", False))
        north = d.get("north_angle")
        north_rad = (math.radians(float(north)) if north is not None
                     else _NORTH_ANGLE_BY_ROAD_SIDE[road_side])

        boundary = d.get("boundary")
        if boundary:
            points = [(float(x) * M_TO_MM, float(y) * M_TO_MM) for x, y in boundary]
            raw_edges = d.get("edges")
            if not raw_edges or len(raw_edges) != len(points):
                raise ValueError("edges は boundary の頂点数と同じ数だけ必要です")
            kinds = [
                (G.EdgeKind(e.get("kind", "neighbor")),
                 float(e.get("width", 0.0)) * M_TO_MM)
                for e in raw_edges
            ]
            if not any(k is G.EdgeKind.ROAD for k, _ in kinds):
                raise ValueError("道路に接する辺（kind: road）が1つ以上必要です")
            shape = G.polygon_site(points, kinds)
            return cls(shape=shape, road_side=road_side, north_angle_rad=north_rad,
                       corner_lot=corner_lot, is_rectangle=False)

        for key in ("frontage", "depth", "road_width"):
            if float(d[key]) <= 0:
                raise ValueError(f"site.{key} は正の値である必要があります")
        shape = G.rectangle_site(
            float(d["frontage"]) * M_TO_MM,
            float(d["depth"]) * M_TO_MM,
            float(d["road_width"]) * M_TO_MM,
        )
        return cls(shape=shape, road_side=road_side, north_angle_rad=north_rad,
                   corner_lot=corner_lot, is_rectangle=True)


@dataclass(frozen=True)
class ZoningInput:
    """法規条件。bcr / far_designated は倍率表記（0.8 = 80%、6.0 = 600%）。"""

    use_district: UseDistrict
    bcr: float
    far_designated: float
    height_limit_absolute_mm: float | None = None
    fire_zone: FireZone = FireZone.NONE

    def __post_init__(self) -> None:
        if not 0 < self.bcr <= 1.0:
            raise ValueError("zoning.bcr は 0 < bcr <= 1.0（倍率表記）で指定してください")
        if self.far_designated <= 0:
            raise ValueError("zoning.far_designated は正の値である必要があります")
        if self.height_limit_absolute_mm is not None and self.height_limit_absolute_mm <= 0:
            raise ValueError("zoning.height_limit_absolute は正の値または null です")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ZoningInput:
        raw_limit = d.get("height_limit_absolute")
        return cls(
            use_district=UseDistrict(d["use_district"]),
            bcr=float(d["bcr"]),
            far_designated=float(d["far_designated"]),
            height_limit_absolute_mm=None if raw_limit is None else float(raw_limit) * M_TO_MM,
            fire_zone=FireZone(d.get("fire_zone") or FireZone.NONE.value),
        )


@dataclass(frozen=True)
class ProgramInput:
    """計画条件。"""

    floor_height_mm: float      # 基準階の階高
    gf_height_mm: float         # 1階の階高
    wall_setback_mm: float      # 外壁後退（敷地境界から建物外面まで、全周）
    core_ratio: float           # コア比率。貸室面積 = 床面積 × (1 - core_ratio)
    max_floors: int
    # 耐火建築物等（準防火地域では準耐火建築物等を含む）とするか。
    # 建蔽率の緩和（法53条3項1号・6項1号）の判定に使う計画側の選択。
    fireproof: bool = False
    # 建物の向き。前面道路に平行を 0 とした振り角。None なら道路に平行。
    building_angle_rad: float | None = None

    def __post_init__(self) -> None:
        for name in ("floor_height_mm", "gf_height_mm"):
            if getattr(self, name) <= 0:
                raise ValueError(f"program.{name} は正の値である必要があります")
        if self.wall_setback_mm < 0:
            raise ValueError("program.wall_setback は 0 以上である必要があります")
        if not 0 <= self.core_ratio < 1.0:
            raise ValueError("program.core_ratio は 0 <= core_ratio < 1.0 で指定してください")
        if self.max_floors < 1:
            raise ValueError("program.max_floors は 1 以上である必要があります")

    def height_of_floor_mm(self, floor: int) -> float:
        """floor 階（1始まり）の階高。"""
        return self.gf_height_mm if floor == 1 else self.floor_height_mm

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProgramInput:
        return cls(
            floor_height_mm=float(d["floor_height"]) * M_TO_MM,
            gf_height_mm=float(d["gf_height"]) * M_TO_MM,
            wall_setback_mm=float(d["wall_setback"]) * M_TO_MM,
            core_ratio=float(d["core_ratio"]),
            max_floors=int(d["max_floors"]),
            fireproof=bool(d.get("fireproof", False)),
            building_angle_rad=(
                None if d.get("building_angle") is None
                else math.radians(float(d["building_angle"]))
            ),
        )


@dataclass(frozen=True)
class VolumeInput:
    """solve() への入力一式。"""

    site: SiteInput
    zoning: ZoningInput
    program: ProgramInput

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VolumeInput:
        return cls(
            site=SiteInput.from_dict(d["site"]),
            zoning=ZoningInput.from_dict(d["zoning"]),
            program=ProgramInput.from_dict(d["program"]),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> VolumeInput:
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppliedRule:
    """適用した規定とその根拠値。図面の表題欄・注記に出す。"""

    label: str      # 例: "道路斜線 勾配"
    value: str      # 例: "1.5"
    basis: str      # 例: "法56条1項1号・別表第三(に)欄2の項"


@dataclass(frozen=True)
class FloorResult:
    """1 つの階の算定結果。座標はモジュール冒頭のローカル系（mm）。"""

    floor: int                    # 1 始まり
    level_mm: float               # 床レベル（GL からの高さ）
    story_height_mm: float        # 階高
    x_min_mm: float               # 間口方向の建物外面
    x_max_mm: float
    y_min_mm: float               # 道路境界線からの距離（建物外面）
    y_max_mm: float
    setback_road_mm: float        # 道路斜線による後退量（外壁後退を含まない）
    setback_neighbor_mm: float    # 隣地斜線による後退量（外壁後退を含まない）
    governing: str                # この階の形状を決めた規定（表示用の文字列）

    # この階を削っている規定と、その規定を外したときの床面積の増分。
    # 増分の大きい順。空なら斜線も建蔽率もかかっていない。
    impacts: tuple[ConstraintImpact, ...] = ()

    # 道路斜線が適用距離で頭打ちになっているか（別表第三(は)欄）
    road_slant_capped: bool = False

    # 外壁後退だけを引いた、制限がかからなかった場合の床面積
    unconstrained_area_mm2: float = 0.0

    # 実際の階の外形（敷地ローカル座標の頂点列）と、その面積。
    # 矩形とは限らないので x/y の範囲とは別に持つ。
    outline: tuple[tuple[float, float], ...] = ()
    area_mm2: float = 0.0

    @property
    def constraints(self) -> tuple[Constraint, ...]:
        """この階を削っている規定（増分の大きい順）。"""
        return tuple(i.constraint for i in self.impacts)

    @property
    def dominant_constraint(self) -> Constraint | None:
        """最も大きく削っている規定。何もかかっていなければ None。"""
        return self.impacts[0].constraint if self.impacts else None

    @property
    def area_loss_mm2(self) -> float:
        """制限がかからなかった場合との床面積の差。"""
        return max(0.0, self.unconstrained_area_mm2 - self.gross_area_mm2)

    @property
    def top_mm(self) -> float:
        """階の天端高さ（GL 基準）。斜線判定はこの高さで行う。"""
        return self.level_mm + self.story_height_mm

    @property
    def width_mm(self) -> float:
        """有効間口。"""
        return self.x_max_mm - self.x_min_mm

    @property
    def depth_mm(self) -> float:
        """有効奥行。"""
        return self.y_max_mm - self.y_min_mm

    @property
    def gross_area_mm2(self) -> float:
        return self.area_mm2

    @property
    def far_area_mm2(self) -> float:
        """容積率対象床面積。

        MVP では床面積と同一とする。エレベーターシャフト・共用部・駐車場等の
        不算入（法52条3項〜6項、令2条1項4号・3項）は未考慮。
        """
        return self.gross_area_mm2

    def rentable_area_mm2(self, core_ratio: float) -> float:
        return self.gross_area_mm2 * (1.0 - core_ratio)


@dataclass
class VolumeResult:
    """solve() の戻り値。図面に必要な情報をすべて含む。"""

    input: VolumeInput

    # 容積率
    far_designated: float                 # 指定容積率（倍率表記）
    far_by_road: float | None             # 前面道路幅員による上限。12m以上なら None
    far_effective: float                  # 実効容積率
    far_road_coefficient: float           # 法52条2項の係数

    # 面積上限
    site_area_mm2: float
    max_far_area_mm2: float               # 敷地面積 × 実効容積率
    max_building_area_mm2: float          # 敷地面積 × BCR

    # 斜線の根拠値
    road_slant_gradient: float
    road_slant_applicable_distance_mm: float
    neighbor_slant_start_mm: float | None  # None は隣地斜線の適用なし
    neighbor_slant_gradient: float | None

    # 実際に適用した絶対高さ制限。入力が None でも用途地域によっては既定値が入る。
    height_limit_applied_mm: float | None = None

    # 緩和後の建蔽率（法53条3項・6項）と、適用した緩和 (説明, 根拠条文)
    bcr_effective: float = 0.0
    bcr_relaxations: list[tuple[str, str]] = field(default_factory=list)

    # 建物の向き（敷地ローカル座標での絶対角）と、建蔽率で絞る前の外形
    building_angle_rad: float = 0.0
    footprint: tuple[tuple[float, float], ...] = ()

    # 建蔽率による全階の一律絞り込み
    bcr_inset_mm: float = 0.0

    floors: list[FloorResult] = field(default_factory=list)
    stop_reason: StopReason | None = None
    stop_detail: str = ""
    applied_rules: list[AppliedRule] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # --- 集計 ---------------------------------------------------------------

    @property
    def floor_count(self) -> int:
        return len(self.floors)

    @property
    def total_gross_area_mm2(self) -> float:
        return sum(f.gross_area_mm2 for f in self.floors)

    @property
    def total_far_area_mm2(self) -> float:
        return sum(f.far_area_mm2 for f in self.floors)

    @property
    def total_rentable_area_mm2(self) -> float:
        ratio = self.input.program.core_ratio
        return sum(f.rentable_area_mm2(ratio) for f in self.floors)

    @property
    def building_area_mm2(self) -> float:
        """建築面積。上階が下階を超えない前提なので 1 階の面積とする。"""
        return self.floors[0].gross_area_mm2 if self.floors else 0.0

    @property
    def building_angle_deg(self) -> float:
        """前面道路に対する建物の振り角[度]。"""
        return math.degrees(self.building_angle_rad - self.input.site.road_edge.angle_rad)

    @property
    def max_height_mm(self) -> float:
        return self.floors[-1].top_mm if self.floors else 0.0

    @property
    def achieved_far(self) -> float:
        """達成容積率（倍率表記）。"""
        return self.total_far_area_mm2 / self.site_area_mm2 if self.site_area_mm2 else 0.0

    @property
    def achieved_bcr(self) -> float:
        """達成建蔽率（倍率表記）。"""
        return self.building_area_mm2 / self.site_area_mm2 if self.site_area_mm2 else 0.0

    # --- どの規定がボリュームを削っているか -----------------------------------

    @property
    def total_area_loss_mm2(self) -> float:
        """制限がかからなかった場合との延床面積の差。"""
        return sum(f.area_loss_mm2 for f in self.floors)

    def constraint_gains_mm2(self) -> dict[Constraint, float]:
        """規定ごとの「それを外したときに増える延床面積」（増分の大きい順）。

        現在の階数のまま各階の床面積が増える分だけを積み上げた値。制限が外れれば
        階数自体が増える可能性もあるが、それは含まない（過小評価になる側）。
        また各規定は掛け算で効くため、合計は total_area_loss_mm2 とは一致しない。
        """
        gains: dict[Constraint, float] = {}
        for floor in self.floors:
            for impact in floor.impacts:
                gains[impact.constraint] = gains.get(impact.constraint, 0.0) + impact.area_gain_mm2
        return dict(sorted(gains.items(), key=lambda kv: kv[1], reverse=True))

    @property
    def dominant_constraint(self) -> Constraint | None:
        """全体として最もボリュームを削っている規定。"""
        gains = self.constraint_gains_mm2()
        return next(iter(gains), None)

    # --- 表示用 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """確認・デバッグ用の要約（面積は m2、長さは m）。"""
        return {
            "far": {
                "designated": self.far_designated,
                "by_road": self.far_by_road,
                "effective": self.far_effective,
                "achieved": round(self.achieved_far, 4),
            },
            "site_area_m2": round(self.site_area_mm2 / M2_TO_MM2, 3),
            "building_area_m2": round(self.building_area_mm2 / M2_TO_MM2, 3),
            "bcr_achieved": round(self.achieved_bcr, 4),
            "floor_count": self.floor_count,
            "max_height_m": round(self.max_height_mm / M_TO_MM, 3),
            "total_gross_area_m2": round(self.total_gross_area_mm2 / M2_TO_MM2, 3),
            "total_far_area_m2": round(self.total_far_area_mm2 / M2_TO_MM2, 3),
            "total_rentable_area_m2": round(self.total_rentable_area_mm2 / M2_TO_MM2, 3),
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "stop_detail": self.stop_detail,
            "floors": [
                {
                    "floor": f.floor,
                    "level_m": round(f.level_mm / M_TO_MM, 3),
                    "story_height_m": round(f.story_height_mm / M_TO_MM, 3),
                    "top_m": round(f.top_mm / M_TO_MM, 3),
                    "width_m": round(f.width_mm / M_TO_MM, 3),
                    "depth_m": round(f.depth_mm / M_TO_MM, 3),
                    "area_m2": round(f.gross_area_mm2 / M2_TO_MM2, 3),
                    "setback_road_m": round(f.setback_road_mm / M_TO_MM, 3),
                    "setback_neighbor_m": round(f.setback_neighbor_mm / M_TO_MM, 3),
                    "governing": f.governing,
                }
                for f in self.floors
            ],
            "applied_rules": [
                {"label": r.label, "value": r.value, "basis": r.basis} for r in self.applied_rules
            ],
            "notes": list(self.notes),
        }
