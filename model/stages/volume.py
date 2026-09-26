"""ボリュームの段の結果（StageResult.data の中身）。M02。

既存の算定結果（VolumeResult・SkyFactorStudy）の項目を、捨てずにすべて持つ。
architecture.md の 4.6 と M02 の指示に沿って、次のまとまりに分ける。

  law             斜線・容積率・建蔽率の根拠値（既存の算定が使った値）
  placement       建物の向き、1階の外形、建蔽率の絞り込み量
  floors          階ごとの Level・形状・面積・削減要因
  totals          延床面積・建築面積・階数・最高高さ など
  rule_reductions 規定ごとの削減量（増分の大きい順。順序も意味を持つ）
  stop            打ち切り理由
  sky_factor      天空率の判定（道路・隣地の境界ごと）
  legacy_applied_rules / legacy_notes
                  既存の出力そのまま（StageResult の applied_rules・not_considered・
                  warnings に構造化して移したものの元。対応を追えるように残す）

単位：長さ mm、面積 mm²、角度はラジアンと度の両方（既存のまま）。
このモジュールは model の外を import しない。
"""

from __future__ import annotations

from pydantic import Field

from model.types import Level, _Strict

Point = tuple[float, float]


class LawParams(_Strict):
    far_designated: float
    far_by_road: float | None
    far_effective: float
    far_road_coefficient: float
    site_area_mm2: float
    max_far_area_mm2: float
    max_building_area_mm2: float
    road_slant_gradient: float
    road_slant_applicable_distance_mm: float
    neighbor_slant_start_mm: float | None
    neighbor_slant_gradient: float | None
    north_slant_start_mm: float | None
    north_slant_gradient: float | None
    height_limit_applied_mm: float | None
    bcr_effective: float
    bcr_relaxations: list[tuple[str, str]]      # (説明, 根拠条文)


class Placement(_Strict):
    building_angle_rad: float
    building_angle_deg: float
    footprint: list[Point]
    bcr_inset_mm: float


class Impact(_Strict):
    constraint: str
    area_gain_mm2: float
    setback_mm: float


class FloorData(_Strict):
    floor: int
    level: Level                     # name・fl_mm（＝既存の level_mm）・floor_height_mm（＝story_height_mm）
    top_mm: float
    x_min_mm: float
    x_max_mm: float
    y_min_mm: float
    y_max_mm: float
    width_mm: float
    depth_mm: float
    setback_road_mm: float
    setback_neighbor_mm: float
    setback_north_mm: float
    governing: str
    impacts: list[Impact]
    dominant_constraint: str | None
    road_slant_capped: bool
    unconstrained_area_mm2: float
    outline: list[Point]
    area_mm2: float
    gross_area_mm2: float
    far_area_mm2: float
    rentable_area_mm2: float
    area_loss_mm2: float


class Totals(_Strict):
    floor_count: int
    total_gross_area_mm2: float
    total_far_area_mm2: float
    total_rentable_area_mm2: float
    building_area_mm2: float
    max_height_mm: float
    achieved_far: float
    achieved_bcr: float
    total_area_loss_mm2: float


class RuleReduction(_Strict):
    constraint: str                  # 既存の Constraint の値（"道路斜線" など）
    area_gain_mm2: float             # この規定を外した場合の延床の増分


class StopInfo(_Strict):
    reason: str | None
    detail: str


class SkyPoint(_Strict):
    position: Point
    planned: float
    compliant: float
    margin: float
    passes: bool


class SkyEdge(_Strict):
    edge_index: int
    kind: str
    label: str
    offset_mm: float
    passes: bool
    points: list[SkyPoint]


class SkySuggestion(_Strict):
    wall_setback_mm: float
    floor_count: int
    max_height_mm: float
    total_gross_area_mm2: float
    worst_margin: float
    outline: list[Point]


class SkyFactorData(_Strict):
    verdict: str
    passes: bool
    worth_studying: bool
    worst_margin: float | None
    gain_mm2: float
    north_slant_unchecked: bool
    planned_floor_count: int
    planned_max_height_mm: float
    planned_total_gross_area_mm2: float
    planned_outline: list[Point]
    base_total_gross_area_mm2: float
    base_max_height_mm: float
    wall_setback_mm: float
    suggestion_searched: bool
    suggestion: SkySuggestion | None
    edges: list[SkyEdge]
    notes: list[str]


class LegacyRule(_Strict):
    label: str
    value: str
    basis: str


class VolumeStageData(_Strict):
    law: LawParams
    placement: Placement
    floors: list[FloorData]
    totals: Totals
    rule_reductions: list[RuleReduction]
    dominant_constraint: str | None
    stop: StopInfo
    sky_factor: SkyFactorData | None
    legacy_applied_rules: list[LegacyRule] = Field(default_factory=list)
    legacy_notes: list[str] = Field(default_factory=list)
