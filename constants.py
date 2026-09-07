"""法規定数。

本ファイル以外に法規の数値を直書きしないこと。
各値には根拠条文を必ずコメントで付す。

出典（建築基準法・同別表）:
  法52条  容積率
  法55条  第一種・第二種低層住居専用地域等内における建築物の高さの限度
  法56条  建築物の各部分の高さ（道路斜線・隣地斜線・北側斜線）
  法別表第三  道路斜線制限の適用距離および勾配

特定行政庁の指定・都市計画の定めによって値が変わる項目は、法の原則値
（＝指定がない場合に適用される値）を既定とし、どの値を採ったかを
VolumeResult.applied_rules / notes に必ず記録する。自治体が別の指定を
している場合は、その値で上書きして再計算すること。
"""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------------------
# 用途地域
# ---------------------------------------------------------------------------


class UseDistrict(str, Enum):
    """用途地域（法48条・都市計画法8条1項1号）。入力JSONの文字列と一致させる。"""

    LOW_RISE_1 = "第一種低層住居専用地域"
    LOW_RISE_2 = "第二種低層住居専用地域"
    FARM_RESIDENTIAL = "田園住居地域"
    MID_HIGH_1 = "第一種中高層住居専用地域"
    MID_HIGH_2 = "第二種中高層住居専用地域"
    RESIDENTIAL_1 = "第一種住居地域"
    RESIDENTIAL_2 = "第二種住居地域"
    QUASI_RESIDENTIAL = "準住居地域"
    NEIGHBORHOOD_COMMERCIAL = "近隣商業地域"
    COMMERCIAL = "商業地域"
    QUASI_INDUSTRIAL = "準工業地域"
    INDUSTRIAL = "工業地域"
    EXCLUSIVE_INDUSTRIAL = "工業専用地域"
    UNDESIGNATED = "指定なし"


class Table3Row(Enum):
    """法別表第三 (い)欄の行区分。道路斜線の適用距離・勾配はこの行で決まる。"""

    RESIDENTIAL = 1     # 1の項: 低層住専・田園住居・中高層住専・住居・準住居
    COMMERCIAL = 2      # 2の項: 近隣商業・商業
    INDUSTRIAL = 3      # 3の項: 準工業・工業・工業専用
    UNDESIGNATED = 4    # 4の項: 用途地域の指定のない区域


# 法別表第三 (い)欄 — 用途地域から行区分への対応
TABLE3_ROW: dict[UseDistrict, Table3Row] = {
    UseDistrict.LOW_RISE_1: Table3Row.RESIDENTIAL,
    UseDistrict.LOW_RISE_2: Table3Row.RESIDENTIAL,
    UseDistrict.FARM_RESIDENTIAL: Table3Row.RESIDENTIAL,
    UseDistrict.MID_HIGH_1: Table3Row.RESIDENTIAL,
    UseDistrict.MID_HIGH_2: Table3Row.RESIDENTIAL,
    UseDistrict.RESIDENTIAL_1: Table3Row.RESIDENTIAL,
    UseDistrict.RESIDENTIAL_2: Table3Row.RESIDENTIAL,
    UseDistrict.QUASI_RESIDENTIAL: Table3Row.RESIDENTIAL,
    UseDistrict.NEIGHBORHOOD_COMMERCIAL: Table3Row.COMMERCIAL,
    UseDistrict.COMMERCIAL: Table3Row.COMMERCIAL,
    UseDistrict.QUASI_INDUSTRIAL: Table3Row.INDUSTRIAL,
    UseDistrict.INDUSTRIAL: Table3Row.INDUSTRIAL,
    UseDistrict.EXCLUSIVE_INDUSTRIAL: Table3Row.INDUSTRIAL,
    UseDistrict.UNDESIGNATED: Table3Row.UNDESIGNATED,
}


# ---------------------------------------------------------------------------
# 道路斜線制限：勾配（法56条1項1号、法別表第三 (に)欄）
# ---------------------------------------------------------------------------
# 高さ h の点は、前面道路の反対側の境界線から水平距離 h / 勾配 以上
# 離れていなければならない。
#
# 特定行政庁の指定による変動:
#   ・1の項のうち中高層住居専用・住居・準住居地域で「特定行政庁が指定する区域」は
#     1.25 ではなく 1.5（別表第三 (に)欄1の項かっこ書き）。指定なしを既定とする。
#   ・4の項（用途地域の指定のない区域）は 1.25 または 1.5 を特定行政庁が定める。
#     無指定区域は容積率係数（法52条2項3号の原則 6/10）も隣地斜線（法56条1項2号ロの
#     31m・2.5）も非住居系と同じ扱いなので、道路斜線も非住居系と同じ 1.5 を既定とする。
ROAD_SLANT_GRADIENT: dict[Table3Row, float] = {
    Table3Row.RESIDENTIAL: 1.25,
    Table3Row.COMMERCIAL: 1.5,
    Table3Row.INDUSTRIAL: 1.5,
    Table3Row.UNDESIGNATED: 1.5,
}


# ---------------------------------------------------------------------------
# 道路斜線制限：適用距離（法56条1項1号、法別表第三 (は)欄）
# ---------------------------------------------------------------------------
# 前面道路の反対側の境界線からこの距離を超える範囲には道路斜線制限がかからない。
# 判定に用いる容積率は (ろ)欄「都市計画において定められた容積率」＝指定容積率であり、
# 前面道路幅員による低減後の実効容積率ではない。
#
# 各要素 = (容積率の上限値（この値以下なら該当。None は上限なし）, 適用距離[m])
# 容積率は倍率表記（4.0 = 400%）。
ROAD_SLANT_APPLICABLE_DISTANCE_M: dict[Table3Row, tuple[tuple[float | None, float], ...]] = {
    # 1の項: 低層住専・田園住居・中高層住専・住居・準住居
    Table3Row.RESIDENTIAL: (
        (2.0, 20.0),      # 200%以下
        (3.0, 25.0),      # 200%超 300%以下
        (4.0, 30.0),      # 300%超 400%以下
        (None, 35.0),     # 400%超
    ),
    # 2の項: 近隣商業・商業
    Table3Row.COMMERCIAL: (
        (4.0, 20.0),      # 400%以下
        (6.0, 25.0),      # 400%超 600%以下
        (8.0, 30.0),      # 600%超 800%以下
        (10.0, 35.0),     # 800%超 1000%以下
        (11.0, 40.0),     # 1000%超 1100%以下
        (12.0, 45.0),     # 1100%超 1200%以下
        (None, 50.0),     # 1200%超
    ),
    # 3の項: 準工業・工業・工業専用
    Table3Row.INDUSTRIAL: (
        (2.0, 20.0),      # 200%以下
        (3.0, 25.0),      # 200%超 300%以下
        (4.0, 30.0),      # 300%超 400%以下
        (None, 35.0),     # 400%超
    ),
    # 4の項: 用途地域の指定のない区域
    Table3Row.UNDESIGNATED: (
        (2.0, 20.0),      # 200%以下
        (3.0, 25.0),      # 200%超 300%以下
        (None, 30.0),     # 300%超
    ),
}


# ---------------------------------------------------------------------------
# 容積率：前面道路幅員による低減の係数（法52条2項）
# ---------------------------------------------------------------------------
# 前面道路の幅員が 12m 未満の場合、
#   実効容積率 = min(指定容積率, 幅員[m] × 係数)
# 幅員が 12m 以上の場合はこの低減を適用しない（＝指定容積率がそのまま上限）。
#
# 特定行政庁が都市計画審議会の議を経て指定する区域では係数が変わるが、
# 本ツールは各号の原則値のみを扱う。
# 法52条9項の特定道路による緩和（幅員15m以上の道路から70m以内）はスコープ外。
FAR_ROAD_WIDTH_COEFFICIENT_THRESHOLD_M: float = 12.0   # 法52条2項柱書

FAR_ROAD_WIDTH_COEFFICIENT: dict[UseDistrict, float] = {
    # 法52条2項1号: 住居系用途地域 → 4/10
    UseDistrict.LOW_RISE_1: 0.4,
    UseDistrict.LOW_RISE_2: 0.4,
    UseDistrict.FARM_RESIDENTIAL: 0.4,
    UseDistrict.MID_HIGH_1: 0.4,
    UseDistrict.MID_HIGH_2: 0.4,
    UseDistrict.RESIDENTIAL_1: 0.4,
    UseDistrict.RESIDENTIAL_2: 0.4,
    UseDistrict.QUASI_RESIDENTIAL: 0.4,
    # 法52条2項2号・3号: 上記以外 → 6/10
    UseDistrict.NEIGHBORHOOD_COMMERCIAL: 0.6,
    UseDistrict.COMMERCIAL: 0.6,
    UseDistrict.QUASI_INDUSTRIAL: 0.6,
    UseDistrict.INDUSTRIAL: 0.6,
    UseDistrict.EXCLUSIVE_INDUSTRIAL: 0.6,
    # 法52条2項3号の原則値は 6/10。特定行政庁が指定する区域では 4/10 または 8/10。
    UseDistrict.UNDESIGNATED: 0.6,
}


# ---------------------------------------------------------------------------
# 隣地斜線制限（法56条1項2号）
# ---------------------------------------------------------------------------
# 立ち上がり高さ H0 を超える部分について、隣地境界線から
#   水平距離 (h - H0) / 勾配 以上 離す。
# 値 = (立ち上がり高さ[m], 勾配) / None は「隣地斜線制限の適用なし」。
#
# 第一種・第二種低層住居専用地域および田園住居地域は隣地斜線制限の適用がなく、
# 代わりに法55条の絶対高さ制限（10m または 12m）による。
#
# 中高層住専・住居・準住居で「特定行政庁が指定する区域」は (31.0, 2.5)（イのかっこ書き）。
# 指定なしを既定とし (20.0, 1.25) を用いる。
# 用途地域の指定のない区域は法56条1項2号ロに列挙されており、近隣商業以下と同じ
# 31m・2.5 が適用される（特定行政庁の指定を要しない）。
NEIGHBOR_SLANT: dict[UseDistrict, tuple[float, float] | None] = {
    UseDistrict.LOW_RISE_1: None,          # 適用なし（法55条の絶対高さ制限による）
    UseDistrict.LOW_RISE_2: None,          # 同上
    UseDistrict.FARM_RESIDENTIAL: None,    # 同上
    UseDistrict.MID_HIGH_1: (20.0, 1.25),
    UseDistrict.MID_HIGH_2: (20.0, 1.25),
    UseDistrict.RESIDENTIAL_1: (20.0, 1.25),
    UseDistrict.RESIDENTIAL_2: (20.0, 1.25),
    UseDistrict.QUASI_RESIDENTIAL: (20.0, 1.25),
    UseDistrict.NEIGHBORHOOD_COMMERCIAL: (31.0, 2.5),
    UseDistrict.COMMERCIAL: (31.0, 2.5),
    UseDistrict.QUASI_INDUSTRIAL: (31.0, 2.5),
    UseDistrict.INDUSTRIAL: (31.0, 2.5),
    UseDistrict.EXCLUSIVE_INDUSTRIAL: (31.0, 2.5),
    UseDistrict.UNDESIGNATED: (31.0, 2.5),    # 法56条1項2号ロに明記
}


# ---------------------------------------------------------------------------
# 絶対高さ制限（法55条1項）
# ---------------------------------------------------------------------------
# 第一種・第二種低層住居専用地域・田園住居地域では 10m または 12m のうち
# 都市計画で定められた値。指定の多数は 10m なのでこれを既定とし、
# 入力 zoning.height_limit_absolute が与えられていればそちらを優先する。
# 既定値を使った場合は VolumeResult.notes にその旨を記録する。
ABSOLUTE_HEIGHT_LIMIT_CHOICES_M: tuple[float, float] = (10.0, 12.0)
DEFAULT_ABSOLUTE_HEIGHT_LIMIT_M: float = 10.0

DISTRICTS_REQUIRING_ABSOLUTE_HEIGHT_LIMIT: frozenset[UseDistrict] = frozenset(
    {UseDistrict.LOW_RISE_1, UseDistrict.LOW_RISE_2, UseDistrict.FARM_RESIDENTIAL}
)


# ---------------------------------------------------------------------------
# 以下は法規定数ではない。本ツールの運用上のしきい値・単位換算。
# ---------------------------------------------------------------------------

# 1フロアとして成立させる最小床面積[m2]。これを下回った階で打ち切る。
MIN_VIABLE_FLOOR_AREA_M2: float = 100.0

# 単位換算（入力JSONは m、内部計算は mm）
M_TO_MM: float = 1000.0
M2_TO_MM2: float = 1_000_000.0


# ---------------------------------------------------------------------------
# 参照ヘルパー
# ---------------------------------------------------------------------------


def road_slant_gradient(district: UseDistrict) -> float:
    """道路斜線の勾配（法56条1項1号・別表第三 (に)欄）。"""
    return ROAD_SLANT_GRADIENT[TABLE3_ROW[district]]


def road_slant_applicable_distance_m(district: UseDistrict, far_designated: float) -> float:
    """道路斜線の適用距離[m]（法別表第三 (は)欄）。

    far_designated は都市計画で定められた指定容積率（倍率表記、4.0 = 400%）。
    実効容積率ではないことに注意（別表第三 (ろ)欄）。
    """
    for upper, distance in ROAD_SLANT_APPLICABLE_DISTANCE_M[TABLE3_ROW[district]]:
        if upper is None or far_designated <= upper:
            return distance
    raise AssertionError("適用距離テーブルの末尾は上限なし(None)であるはず")


def far_road_width_coefficient(district: UseDistrict) -> float:
    """容積率算定に用いる前面道路幅員の係数（法52条2項）。"""
    return FAR_ROAD_WIDTH_COEFFICIENT[district]


def neighbor_slant(district: UseDistrict) -> tuple[float, float] | None:
    """隣地斜線の (立ち上がり高さ[m], 勾配)。適用がない地域は None（法56条1項2号）。"""
    return NEIGHBOR_SLANT[district]
