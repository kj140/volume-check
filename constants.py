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
# 北側斜線制限（法56条1項3号）
# ---------------------------------------------------------------------------
# 北側の隣地境界線（前面道路が北側にあるときはその反対側の境界線）から、
# 真北方向の水平距離 d だけ南に離れた点の高さの上限は
#   立ち上がり高さ H0 + 勾配 × d
# 値 = (立ち上がり高さ[m], 勾配) / None は「北側斜線制限の適用なし」。
#
# 道路斜線・隣地斜線が「その境界線に垂直な距離」で決まるのに対し、
# 北側斜線は向きが常に真北で、敷地の向きと無関係であることに注意。
#
# 第一種・第二種中高層住居専用地域は、日影規制（法56条の2）の対象区域として
# 指定されている場合は北側斜線の適用がない（法56条1項3号かっこ書き）。
# 低層住専・田園住居は日影規制の有無にかかわらず適用される。
NORTH_SLANT: dict[UseDistrict, tuple[float, float] | None] = {
    UseDistrict.LOW_RISE_1: (5.0, 1.25),
    UseDistrict.LOW_RISE_2: (5.0, 1.25),
    UseDistrict.FARM_RESIDENTIAL: (5.0, 1.25),
    UseDistrict.MID_HIGH_1: (10.0, 1.25),
    UseDistrict.MID_HIGH_2: (10.0, 1.25),
    # 以下は北側斜線制限の適用なし
    UseDistrict.RESIDENTIAL_1: None,
    UseDistrict.RESIDENTIAL_2: None,
    UseDistrict.QUASI_RESIDENTIAL: None,
    UseDistrict.NEIGHBORHOOD_COMMERCIAL: None,
    UseDistrict.COMMERCIAL: None,
    UseDistrict.QUASI_INDUSTRIAL: None,
    UseDistrict.INDUSTRIAL: None,
    UseDistrict.EXCLUSIVE_INDUSTRIAL: None,
    UseDistrict.UNDESIGNATED: None,
}

# 日影規制の指定があると北側斜線が外れる用途地域（法56条1項3号かっこ書き）
DISTRICTS_WHERE_SHADOW_RULE_REPLACES_NORTH_SLANT: frozenset[UseDistrict] = frozenset(
    {UseDistrict.MID_HIGH_1, UseDistrict.MID_HIGH_2}
)


# ---------------------------------------------------------------------------
# 防火地域・準防火地域（法61条・都市計画法8条1項5号）
# ---------------------------------------------------------------------------


class FireZone(str, Enum):
    """防火地域の指定。建蔽率の緩和（法53条3項・6項）の判定に使う。"""

    FIRE = "防火地域"
    QUASI_FIRE = "準防火地域"
    NONE = "指定なし"


# ---------------------------------------------------------------------------
# 建蔽率の緩和（法53条3項・6項）
# ---------------------------------------------------------------------------
# 法53条3項:
#   一 防火地域（建蔽率の限度が8/10とされている地域を除く）内の耐火建築物等
#     または 準防火地域内の耐火建築物等・準耐火建築物等   → 1/10 を加える
#   二 街区の角にある敷地等で特定行政庁が指定するもの      → 1/10 を加える
#   一と二の両方に該当する場合                            → 2/10 を加える
#
# 法53条6項1号:
#   建蔽率の限度が8/10とされている地域内の防火地域にある耐火建築物等は
#   建蔽率の制限を受けない（＝10/10）。
#
# 「耐火建築物等とするか」は計画side の判断なので入力で受け取る。
# 角地は特定行政庁の指定によるため、これも入力で受け取る。
BCR_RELAXATION_FIREPROOF = 0.1          # 法53条3項1号
BCR_RELAXATION_CORNER_LOT = 0.1         # 法53条3項2号
BCR_UNLIMITED_BASE = 0.8                # 法53条6項1号が対象とする指定建蔽率
BCR_UNLIMITED = 1.0                     # 同号適用時の建蔽率


def relaxed_bcr(
    bcr: float,
    fire_zone: FireZone | None,
    fireproof: bool,
    corner_lot: bool,
) -> tuple[float, list[tuple[str, str]]]:
    """緩和後の建蔽率と、適用した緩和の一覧 (説明, 根拠条文) を返す。

    fire_zone が None または指定なしのときは防火系の緩和を適用しない。
    """
    applied: list[tuple[str, str]] = []

    # 法53条6項1号: 建蔽率8/10の地域内の防火地域で耐火建築物等 → 制限なし
    if (fire_zone is FireZone.FIRE and fireproof
            and abs(bcr - BCR_UNLIMITED_BASE) < 1e-9):
        applied.append(("防火地域内の耐火建築物等（指定建蔽率80%）→ 建蔽率の制限なし",
                        "法53条6項1号"))
        return BCR_UNLIMITED, applied

    result = bcr
    if fireproof and fire_zone in (FireZone.FIRE, FireZone.QUASI_FIRE):
        result += BCR_RELAXATION_FIREPROOF
        applied.append((f"{fire_zone.value}内の耐火建築物等 → +10%", "法53条3項1号"))
    if corner_lot:
        result += BCR_RELAXATION_CORNER_LOT
        applied.append(("角地等（特定行政庁の指定）→ +10%", "法53条3項2号"))

    return min(result, BCR_UNLIMITED), applied


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
# 天空率（法56条7項・令135条の5〜9）
# ---------------------------------------------------------------------------
# 天空率が適合建築物のそれ以上であれば、斜線制限は適用されない（法56条7項）。
# 天空率の定義は令135条の5（半球を水平面に正射影した面積の比）。

# 道路斜線の算定位置の間隔（令135条の9第1項）。
# 前面道路の反対側の境界線上を、道路幅員の 1/2 以下の等間隔に分ける。
SKY_FACTOR_ROAD_POINT_INTERVAL_RATIO: float = 0.5

# 道路斜線の算定位置の高さ[m]（令135条の9第1項）。
# 「前面道路の路面の中心の高さ」。本ツールは GL＝道路中心高さとして扱う。
SKY_FACTOR_ROAD_POINT_HEIGHT_M: float = 0.0

# 隣地斜線の算定位置の間隔（令135条の10）。境界線から算定線までの距離の 1/2 以下。
# 勾配1.25（水平距離16m）なら8m以下、勾配2.5（12.4m）なら6.2m以下になる。
SKY_FACTOR_NEIGHBOR_POINT_INTERVAL_RATIO: float = 0.5

# 隣地斜線の算定位置の高さ[m]（令135条の10）。「平均地盤面」＝本ツールの GL。
SKY_FACTOR_NEIGHBOR_POINT_HEIGHT_M: float = 0.0


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


def neighbor_slant_measurement_distance_m(district: UseDistrict) -> float | None:
    """隣地斜線の算定位置までの水平距離[m]（令135条の10）。適用がなければ None。

    隣地境界線から「立ち上がり ÷ 勾配」だけ外側。住居系（20m・1.25）なら 16m、
    それ以外（31m・2.5）なら 12.4m となり、令が定める値と一致する。

    この距離である理由は幾何的にはっきりしている。境界線から敷地内へ距離 d の点で
    斜線の高さは 立ち上がり + 勾配×d なので、境界線の 立ち上がり/勾配 だけ外側から
    見た仰角の正接は
        (立ち上がり + 勾配×d) / (立ち上がり/勾配 + d) = 勾配
    となり、斜線面上のどの点でも一定になる。道路斜線で反対側の境界線を算定線に
    採るのと同じ性質（令135条の9）。
    """
    slant = NEIGHBOR_SLANT[district]
    if slant is None:
        return None
    start_m, gradient = slant
    return start_m / gradient


def north_slant(district: UseDistrict,
                shadow_rule_applies: bool = False) -> tuple[float, float] | None:
    """北側斜線の (立ち上がり高さ[m], 勾配)。適用がなければ None（法56条1項3号）。

    中高層住専で日影規制の対象区域に指定されている場合は、北側斜線に代えて
    日影規制によることとされているため None を返す（同号かっこ書き）。
    """
    if (shadow_rule_applies
            and district in DISTRICTS_WHERE_SHADOW_RULE_REPLACES_NORTH_SLANT):
        return None
    return NORTH_SLANT[district]
