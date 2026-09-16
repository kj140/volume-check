"""型の定義（docs/architecture.md 4.2〜4.7）。pydantic v2。

単位の約束：長さは mm、面積は mm²、角度は度。座標系は既存コードのもの
（docs/current_state.md 6.）を正とする。

値の出所：
  FactValue   敷地事実。敷地で決まり、人が確かめる。取得できなければ source="unavailable"、
              value=None。推測値で埋めない。
  PlanValue   計画条件。設計者が選ぶ。既定値で進めたときは origin="default"。

このモジュールは他のモジュールを import しない（solver → model の向きだけを許すため）。
用途地域名・防火地域名の妥当性は文字列のままとし、算定側（constants.py）で検査する。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")

Source = Literal["api", "manual", "drawn", "unavailable"]
Origin = Literal["default", "designer"]
CreatedBy = Literal["ui", "chat", "batch"]
Stage = Literal["volume", "estimate", "core", "grid", "drawing", "site"]


class _Strict(BaseModel):
    """余計なキーを拒否し、代入時も検査する共通設定。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# 値の出所
# ---------------------------------------------------------------------------


class FactValue(_Strict, Generic[T]):
    """敷地事実。取得できない事実は source="unavailable"・value=None。"""

    value: T | None = None
    source: Source = "unavailable"
    source_name: str | None = None      # 例：「不動産情報ライブラリ API」
    retrieved_at: datetime | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _unavailable_has_no_value(self) -> FactValue[T]:
        if self.source == "unavailable" and self.value is not None:
            raise ValueError("source='unavailable' の事実に値は置けません（推測値で埋めない）")
        if self.source != "unavailable" and self.value is None:
            raise ValueError(f"source='{self.source}' の事実には値が必要です")
        return self

    @property
    def available(self) -> bool:
        return self.source != "unavailable"


class PlanValue(_Strict, Generic[T]):
    """計画条件。設計者が選んだ値か、既定値で進めた値か。"""

    value: T
    origin: Origin = "default"
    note: str | None = None


# ---------------------------------------------------------------------------
# 敷地事実
# ---------------------------------------------------------------------------


class Edge(_Strict):
    """敷地の 1 辺。頂点 i → i+1。"""

    kind: Literal["road", "neighbor"]
    road_width_mm: float = 0.0          # kind="road" のときだけ意味を持つ
    road_type: str | None = None        # 道路種別（法42条）。公開データにないので None＝不明


class SiteFacts(_Strict):
    """敷地事実（architecture.md 4.3）。既存の入力項目はすべて含む。"""

    # 形。座標は敷地ローカル座標 mm（既存の座標系）。
    boundary: FactValue[list[tuple[float, float]]] = Field(default_factory=FactValue)
    edges: FactValue[list[Edge]] = Field(default_factory=FactValue)
    # 既存入力の形式。矩形（frontage/depth/road_width）か多角形か。
    # 矩形は boundary が (0,0)-(間口,0)-(間口,奥行)-(0,奥行)、辺 0 が道路になる。
    shape_form: Literal["rectangle", "polygon"] = "polygon"
    # 主要な前面道路の方位（既存の road_side。矩形入力の真北と断面図の向きに使う）
    road_side: FactValue[Literal["north", "east", "south", "west"]] = Field(default_factory=FactValue)
    # 真北。ローカル座標の +x 軸から反時計回りの角度[度]（既存の north_angle）。
    # 未指定なら算定側が road_side から導く。
    true_north_deg: FactValue[float] = Field(default_factory=FactValue)
    corner_lot: FactValue[bool] = Field(default_factory=FactValue)      # 角地等の指定（法53条3項2号）

    use_district: FactValue[str] = Field(default_factory=FactValue)     # 用途地域（constants.UseDistrict の名前）
    coverage_ratio: FactValue[float] = Field(default_factory=FactValue) # 指定建蔽率（倍率 0.8＝80%）
    far_ratio: FactValue[float] = Field(default_factory=FactValue)      # 指定容積率（倍率 6.0＝600%）
    fire_zone: FactValue[str] = Field(default_factory=FactValue)        # 防火地域／準防火地域／指定なし
    absolute_height_limit_mm: FactValue[float] = Field(default_factory=FactValue)  # 法55条・高度地区等
    shadow_regulation: FactValue[bool] = Field(default_factory=FactValue)  # 日影規制の対象区域か（手入力）
    height_district: FactValue[str] = Field(default_factory=FactValue)  # 高度地区（手入力、なければ unavailable）
    district_plan: FactValue[str] = Field(default_factory=FactValue)    # 地区計画（手入力、なければ unavailable）

    def values(self) -> dict[str, Any]:
        """ハッシュ用：出所を除いた値だけ。"""
        out: dict[str, Any] = {"shape_form": self.shape_form}
        for name, field in type(self).model_fields.items():
            if name == "shape_form":
                continue
            fact = getattr(self, name)
            out[name] = fact.model_dump(mode="json")["value"]
        return out


# ---------------------------------------------------------------------------
# 計画条件
# ---------------------------------------------------------------------------

# 既定値。既存の画面（web/static/index.html）と死活監視（/healthz）が使っている値
# （docs/decisions/0003）。新しい既定値を決めるときはユーザーに確認し、decisions に記録する。
DEFAULT_FLOOR_HEIGHT_MM = 4200.0
DEFAULT_GF_HEIGHT_MM = 4500.0
DEFAULT_WALL_SETBACK_MM = 500.0
DEFAULT_CORE_RATIO = 0.18
DEFAULT_MAX_FLOORS = 30
DEFAULT_FIREPROOF = False
DEFAULT_BUILDING_ANGLE_DEG: float | None = None     # None＝前面道路に平行


def _default(value: T) -> PlanValue[T]:
    return PlanValue(value=value, origin="default")


class PlanConditions(_Strict):
    """計画条件（architecture.md 4.4）。既存の program の項目。"""

    floor_height_mm: PlanValue[float] = Field(default_factory=lambda: _default(DEFAULT_FLOOR_HEIGHT_MM))
    gf_height_mm: PlanValue[float] = Field(default_factory=lambda: _default(DEFAULT_GF_HEIGHT_MM))
    wall_setback_mm: PlanValue[float] = Field(default_factory=lambda: _default(DEFAULT_WALL_SETBACK_MM))
    core_ratio: PlanValue[float] = Field(default_factory=lambda: _default(DEFAULT_CORE_RATIO))
    max_floors: PlanValue[int] = Field(default_factory=lambda: _default(DEFAULT_MAX_FLOORS))
    fireproof: PlanValue[bool] = Field(default_factory=lambda: _default(DEFAULT_FIREPROOF))
    # 前面道路に対する振り角[度]。None＝道路に平行
    building_angle_deg: PlanValue[float | None] = Field(
        default_factory=lambda: _default(DEFAULT_BUILDING_ANGLE_DEG))

    def values(self) -> dict[str, Any]:
        """ハッシュ用：出所を除いた値だけ。"""
        return {name: getattr(self, name).model_dump(mode="json")["value"]
                for name in type(self).model_fields}

    def defaults_used(self) -> list[str]:
        """origin="default" のまま進めた項目のパス（StageResult.defaults_used に入れる）。"""
        return [f"plan_conditions.{name}" for name in type(self).model_fields
                if getattr(self, name).origin == "default"]


# ---------------------------------------------------------------------------
# 案
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Scheme(_Strict):
    """案（architecture.md 4.5）。保存後に書き換えない。変更は derive で子案を作る。"""

    id: str
    parent_id: str | None = None
    site_id: str                           # 同じ敷地の案をまとめる。子案は親から引き継ぐ
    created_at: datetime
    created_by: CreatedBy
    label: str | None = None
    site_facts: SiteFacts
    plan_conditions: PlanConditions
    input_hash: str                        # model.hashing.input_hash(site_facts, plan_conditions)

    @model_validator(mode="after")
    def _hash_matches(self) -> Scheme:
        from model.hashing import input_hash   # 循環 import を避ける

        expected = input_hash(self.site_facts, self.plan_conditions)
        if self.input_hash != expected:
            raise ValueError("input_hash が内容と一致しません（案は書き換えない。derive で子案を作る）")
        return self

    @classmethod
    def new(cls, site_facts: SiteFacts, plan_conditions: PlanConditions, *,
            created_by: CreatedBy, label: str | None = None,
            parent_id: str | None = None, site_id: str | None = None,
            id: str | None = None, created_at: datetime | None = None) -> Scheme:
        """内容からハッシュを計算して案を作る。id・時刻はテストのために差し替えられる。"""
        from model.hashing import input_hash

        scheme_id = id or str(uuid4())
        return cls(
            id=scheme_id,
            parent_id=parent_id,
            site_id=site_id or scheme_id,          # 根の案は自分の id を敷地 id にする
            created_at=created_at or _now(),
            created_by=created_by,
            label=label,
            site_facts=site_facts,
            plan_conditions=plan_conditions,
            input_hash=input_hash(site_facts, plan_conditions),
        )


# ---------------------------------------------------------------------------
# 段の結果
# ---------------------------------------------------------------------------


class AppliedRule(_Strict):
    rule_id: str                  # solver/law で定義した ID
    article: str                  # 例：「法56条1項1号」
    summary: str
    effect_mm2: float | None = None   # この規定を外した場合の増分（出せる段だけ）


class NotConsidered(_Strict):
    item: str
    direction: Literal["larger_than_actual", "smaller_than_actual", "unknown"]
    note: str


class StageResult(_Strict):
    """段の結果（architecture.md 4.6）。data の中身は段ごとに別の型で定める（M02〜）。"""

    scheme_id: str
    stage: Stage
    input_hash: str               # 段の名前・その段が使う入力・上流の input_hash から作る（M02）
    solver_version: str
    status: Literal["ok", "infeasible", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    applied_rules: list[AppliedRule] = Field(default_factory=list)
    not_considered: list[NotConsidered] = Field(default_factory=list)
    defaults_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 骨格データ（型だけ。中身は後の段で入れる）
# ---------------------------------------------------------------------------


class Level(_Strict):
    name: str                     # "1F", "RF" など
    fl_mm: float                  # GL からの床高さ
    floor_height_mm: float
    ceiling_height_mm: PlanValue[float] | None = None   # M07 以降
    beam_bottom_mm: PlanValue[float] | None = None      # M07 以降


class GridAxis(_Strict):
    name: str                     # "X1", "Y1" など
    direction: Literal["X", "Y"]
    offset_mm: float              # 建物座標での位置
