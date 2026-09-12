"""solver.solve() の検証。

前半は性質テスト（どんな入力でも成り立つべきこと）、
後半は手計算で検算した具体ケース。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constants as C  # noqa: E402
import models as C_  # noqa: E402
from models import StopReason, VolumeInput  # noqa: E402
from solver import solve  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

M2 = C.M2_TO_MM2
MM = C.M_TO_MM

# 手計算ケースの基準入力（samples/case_road12.json と同じ）
BASE = {
    "site": {"frontage": 20.0, "depth": 30.0, "road_width": 12.0, "road_side": "south"},
    "zoning": {
        "use_district": "商業地域",
        "bcr": 0.8,
        "far_designated": 6.0,
        "height_limit_absolute": None,
    },
    "program": {
        "floor_height": 4.2,
        "gf_height": 4.5,
        "wall_setback": 0.5,
        "core_ratio": 0.18,
        "max_floors": 30,
    },
}


def make(**overrides):
    """BASE を部分的に上書きした VolumeInput を作る。"""
    d = copy.deepcopy(BASE)
    for section, values in overrides.items():
        d[section].update(values)
    return VolumeInput.from_dict(d)


# ---------------------------------------------------------------------------
# 性質テスト
# ---------------------------------------------------------------------------


# 単調性について（重要）
#
# 当初「道路幅員を広げると階数が減らない」を性質として置いたが、これは成り立たない。
#   例（BASE、商業地域・指定600%・上限3600m2）:
#     幅員 13.5m → 9階・延床 3592.20m2（9階が100m2未満になり打ち切り）
#     幅員 14.0m → 8階・延床 3471.13m2（各階が大きくなり9階を足すと3600m2超）
# 幅員を広げると道路斜線の後退が減って各階の床面積が増えるため、容積率の上限が
# 一定なら階数はむしろ減り得る。「最終階は端数調整せず切り捨て」ため延床面積も
# 単調にはならない。
#
# 実際に単調なのは以下。ここではそれを検証する。
WIDTH_SWEEP = [4.0 + 0.5 * i for i in range(33)]     # 4.0m 〜 20.0m


def test_wider_road_never_reduces_effective_far():
    """道路幅員を広げると実効容積率と容積上限床面積が減らない（法52条2項）。"""
    prev_far = prev_area = -1.0
    for w in WIDTH_SWEEP:
        r = solve(make(site={"road_width": w}))
        assert r.far_effective >= prev_far - 1e-9, f"幅員 {w}m で実効容積率が減少"
        assert r.max_far_area_mm2 >= prev_area - 1.0, f"幅員 {w}m で容積上限床面積が減少"
        prev_far, prev_area = r.far_effective, r.max_far_area_mm2


def test_wider_road_never_shrinks_any_floor():
    """道路幅員を広げると、同じ階の道路側後退は増えず、床面積も減らない。"""
    prev = None
    for w in WIDTH_SWEEP:
        floors = solve(make(site={"road_width": w})).floors
        if prev is not None:
            for a, b in zip(prev, floors):     # 両方に存在する階だけ比較
                assert b.setback_road_mm <= a.setback_road_mm + 1e-6, f"幅員 {w}m {a.floor}階"
                assert b.gross_area_mm2 >= a.gross_area_mm2 - 1.0, f"幅員 {w}m {a.floor}階"
        prev = floors


def test_floor_count_monotonic_while_road_far_reduction_binds():
    """前面道路幅員が容積率を支配している範囲では、幅員を広げると階数が減らない。

    この範囲では幅員を広げると容積上限そのものが増えるため、単調性が成立する。
    """
    prev_count = -1
    for w in WIDTH_SWEEP:
        r = solve(make(site={"road_width": w}))
        if r.far_by_road is None or r.far_by_road >= r.far_designated:
            break      # 指定容積率が支配し始めたら対象外
        assert r.floor_count >= prev_count, f"幅員 {w}m で階数が減少"
        prev_count = r.floor_count
    assert prev_count >= 1, "検証対象となる幅員域が存在しない"


@pytest.mark.parametrize("road_width", [4.0, 6.0, 8.0, 12.0, 16.0, 25.0])
@pytest.mark.parametrize("district", ["商業地域", "第一種住居地域", "準工業地域"])
def test_all_floors_inside_site_boundary(road_width, district):
    """全階の各辺が敷地境界の内側にあり、外壁後退も確保されている。"""
    inp = make(
        site={"road_width": road_width},
        zoning={"use_district": district, "far_designated": 4.0, "bcr": 0.6},
    )
    r = solve(inp)
    setback = inp.program.wall_setback_mm
    for f in r.floors:
        assert f.x_min_mm >= setback - 1e-6, f"{f.floor}階 x_min が外壁後退の外側"
        assert f.x_max_mm <= inp.site.frontage_mm - setback + 1e-6
        assert f.y_min_mm >= setback - 1e-6
        assert f.y_max_mm <= inp.site.depth_mm - setback + 1e-6
        assert f.width_mm > 0 and f.depth_mm > 0


@pytest.mark.parametrize("road_width", [4.0, 6.0, 8.0, 12.0, 20.0])
def test_total_far_area_within_limit(road_width):
    """累計容積対象床面積 ≦ 敷地面積 × 実効容積率。"""
    r = solve(make(site={"road_width": road_width}))
    assert r.total_far_area_mm2 <= r.site_area_mm2 * r.far_effective + 1.0


@pytest.mark.parametrize("bcr", [0.4, 0.6, 0.8, 1.0])
def test_building_area_within_bcr(bcr):
    """1階建築面積 ≦ 敷地面積 × BCR。"""
    r = solve(make(zoning={"bcr": bcr}))
    assert r.building_area_mm2 <= r.site_area_mm2 * bcr + 1.0


def test_zero_floors_returns_reason_without_exception_small_site():
    """階数が0でも例外を投げず、理由を返す（敷地が小さすぎる場合）。"""
    r = solve(make(site={"frontage": 8.0, "depth": 10.0}))
    assert r.floor_count == 0
    assert r.stop_reason is StopReason.BELOW_MIN_FLOOR_AREA
    assert r.stop_detail
    assert r.max_height_mm == 0.0
    assert r.total_far_area_mm2 == 0.0


def test_zero_floors_returns_reason_without_exception_height_limit():
    """階数が0でも例外を投げず、理由を返す（絶対高さ制限が1階の階高未満）。"""
    r = solve(make(zoning={"height_limit_absolute": 3.0}))
    assert r.floor_count == 0
    assert r.stop_reason is StopReason.ABSOLUTE_HEIGHT_LIMIT


def test_zero_floors_returns_reason_without_exception_no_footprint():
    """階数が0でも例外を投げず、理由を返す（外壁後退で間口が消える場合）。"""
    r = solve(make(site={"frontage": 2.0}, program={"wall_setback": 1.5}))
    assert r.floor_count == 0
    assert r.stop_reason is StopReason.NO_EFFECTIVE_FOOTPRINT


def test_stop_reason_is_always_recorded():
    """どの入力でも打ち切り理由が必ず入る。"""
    for w in (4.0, 6.0, 12.0, 30.0):
        for far in (2.0, 4.0, 6.0, 10.0):
            r = solve(make(site={"road_width": w}, zoning={"far_designated": far}))
            assert r.stop_reason is not None
            assert r.stop_detail != ""


def test_floors_are_stacked_without_gaps():
    """階レベルが連続している（下階の天端 = 上階の床）。"""
    r = solve(make())
    assert r.floors[0].level_mm == 0.0
    for lower, upper in zip(r.floors, r.floors[1:]):
        assert upper.level_mm == pytest.approx(lower.top_mm)


def test_upper_floors_never_exceed_lower_floors():
    """上階が下階より外に出ない（斜線は高さとともに厳しくなるだけ）。"""
    r = solve(make(site={"road_width": 6.0}))
    for lower, upper in zip(r.floors, r.floors[1:]):
        assert upper.x_min_mm >= lower.x_min_mm - 1e-6
        assert upper.x_max_mm <= lower.x_max_mm + 1e-6
        assert upper.y_min_mm >= lower.y_min_mm - 1e-6
        assert upper.y_max_mm <= lower.y_max_mm + 1e-6


def test_max_floors_is_respected():
    r = solve(make(program={"max_floors": 3}, zoning={"far_designated": 20.0}))
    assert r.floor_count == 3
    assert r.stop_reason is StopReason.MAX_FLOORS_REACHED


def test_result_always_records_applied_rules_and_notes():
    r = solve(make())
    labels = {rule.label for rule in r.applied_rules}
    assert {"用途地域", "指定建蔽率", "指定容積率", "実効容積率", "道路斜線 勾配",
            "道路斜線 適用距離", "隣地斜線"} <= labels
    assert all(rule.basis for rule in r.applied_rules), "根拠条文のない適用規定がある"
    assert r.notes, "未考慮事項の注記が空"


# ---------------------------------------------------------------------------
# 法52条2項（前面道路幅員による容積率の低減）
# ---------------------------------------------------------------------------


def test_far_reduction_not_applied_when_road_is_12m_or_wider():
    r = solve(make(site={"road_width": 12.0}))
    assert r.far_by_road is None
    assert r.far_effective == pytest.approx(6.0)


def test_far_reduction_applied_for_narrow_road_non_residential():
    """非住居系の係数 6/10。6m × 0.6 = 360% < 指定600% → 360%。"""
    r = solve(make(site={"road_width": 6.0}))
    assert r.far_road_coefficient == pytest.approx(0.6)
    assert r.far_by_road == pytest.approx(3.6)
    assert r.far_effective == pytest.approx(3.6)


def test_far_reduction_applied_for_narrow_road_residential():
    """住居系の係数 4/10。6m × 0.4 = 240% < 指定300% → 240%。"""
    r = solve(
        make(
            site={"road_width": 6.0},
            zoning={"use_district": "第一種住居地域", "far_designated": 3.0},
        )
    )
    assert r.far_road_coefficient == pytest.approx(0.4)
    assert r.far_effective == pytest.approx(2.4)


def test_designated_far_wins_when_smaller():
    """道路幅員による上限より指定容積率が小さければ指定値を採る。"""
    r = solve(make(site={"road_width": 10.0}, zoning={"far_designated": 2.0}))
    assert r.far_by_road == pytest.approx(6.0)
    assert r.far_effective == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 斜線制限
# ---------------------------------------------------------------------------


def test_road_slant_setback_matches_formula():
    """道路側後退 = max(0, 天端/勾配 - 幅員)。商業地域・幅員6m・勾配1.5。"""
    r = solve(make(site={"road_width": 6.0}))
    for f in r.floors:
        expected = max(0.0, min(f.top_mm / 1.5, r.road_slant_applicable_distance_mm) - 6.0 * MM)
        assert f.setback_road_mm == pytest.approx(expected)


def test_neighbor_slant_starts_above_31m_in_commercial():
    """商業地域の隣地斜線は 31m 立ち上がり・勾配2.5。"""
    r = solve(make())
    assert r.neighbor_slant_start_mm == pytest.approx(31.0 * MM)
    assert r.neighbor_slant_gradient == pytest.approx(2.5)
    for f in r.floors:
        if f.top_mm <= 31.0 * MM:
            assert f.setback_neighbor_mm == 0.0
        else:
            assert f.setback_neighbor_mm == pytest.approx((f.top_mm - 31.0 * MM) / 2.5)


def test_road_slant_capped_by_applicable_distance():
    """適用距離を超えたら道路斜線はそれ以上厳しくならない。"""
    r = solve(make())
    limit = r.road_slant_applicable_distance_mm - r.input.site.road_width_mm
    assert r.road_slant_applicable_distance_mm == pytest.approx(25.0 * MM)  # 商業・指定600%
    for f in r.floors:
        assert f.setback_road_mm <= limit + 1e-6


def test_low_rise_district_defaults_to_standard_absolute_height_limit():
    """低層住専で絶対高さ制限が未指定なら、指定の多数である 10m を既定適用する。"""
    r = solve(
        make(
            zoning={
                "use_district": "第一種低層住居専用地域",
                "far_designated": 1.0,
                "bcr": 0.5,
                "height_limit_absolute": None,
            },
            program={"floor_height": 3.0, "gf_height": 3.0},
        )
    )
    assert r.height_limit_applied_mm == pytest.approx(
        C.DEFAULT_ABSOLUTE_HEIGHT_LIMIT_M * MM
    )
    assert r.max_height_mm <= C.DEFAULT_ABSOLUTE_HEIGHT_LIMIT_M * MM + 1e-6
    assert any("既定" in n for n in r.notes), "既定値を使った旨の注記がない"
    assert any(rule.label == "絶対高さ制限" for rule in r.applied_rules)


def test_explicit_absolute_height_limit_overrides_default():
    r = solve(
        make(
            zoning={
                "use_district": "第一種低層住居専用地域",
                "far_designated": 1.0,
                "bcr": 0.5,
                "height_limit_absolute": 12.0,
            },
            program={"floor_height": 3.0, "gf_height": 3.0},
        )
    )
    assert r.height_limit_applied_mm == pytest.approx(12.0 * MM)
    assert not any("既定" in n for n in r.notes)


def test_undesignated_district_uses_non_residential_defaults():
    """用途地域無指定は法の原則値（道路斜線1.5・係数6/10・隣地31m/2.5）で扱う。"""
    r = solve(
        make(
            site={"road_width": 8.0},
            zoning={"use_district": "指定なし", "far_designated": 2.0, "bcr": 0.6},
        )
    )
    assert r.road_slant_gradient == pytest.approx(1.5)
    assert r.far_road_coefficient == pytest.approx(0.6)
    assert r.far_effective == pytest.approx(2.0)       # 8m × 0.6 = 480% > 指定200%
    assert r.neighbor_slant_start_mm == pytest.approx(31.0 * MM)
    assert r.neighbor_slant_gradient == pytest.approx(2.5)
    assert r.road_slant_applicable_distance_mm == pytest.approx(20.0 * MM)  # 200%以下
    assert r.floor_count >= 1
    assert any("原則値" in n for n in r.notes)


def test_low_rise_district_has_no_neighbor_slant():
    r = solve(
        make(
            zoning={
                "use_district": "第一種低層住居専用地域",
                "far_designated": 1.0,
                "bcr": 0.5,
                "height_limit_absolute": 10.0,
            },
            program={"floor_height": 3.0, "gf_height": 3.0},
        )
    )
    assert r.neighbor_slant_start_mm is None
    assert all(f.setback_neighbor_mm == 0.0 for f in r.floors)
    assert r.max_height_mm <= 10.0 * MM + 1e-6


# ---------------------------------------------------------------------------
# 手計算で検算した具体ケース
# ---------------------------------------------------------------------------
#
# 共通条件: 敷地 20m × 30m = 600m2 / 商業地域 / BCR 80% / 指定容積率 600%
#           1階 4.5m、基準階 4.2m、外壁後退 0.5m
#   道路斜線 勾配 1.5、適用距離 25m（商業・指定600% → 別表第三2の項）
#   隣地斜線 立ち上がり 31m、勾配 2.5
#
# 辺ごとの控除量:
#   外壁後退と斜線はどちらも「境界線からの離れ」なので大きいほうだけが効く。
#   建蔽率の絞り込み t は領域全体を一律に寄せるものなので、これに足す。
#     控除量 = max(外壁後退 0.5m, その辺の斜線後退) + t
#
# 建蔽率による絞り込み t:
#   1階は斜線がかからず  間口 20 - 0.5×2 = 19.0m、奥行 30 - 0.5×2 = 29.0m → 551.0m2
#   建蔽率上限 600 × 0.8 = 480m2 を超えるので (19-2t)(29-2t) = 480 を解く
#   4t^2 - 96t + 71 = 0 → t = (48 - sqrt(2304 - 284)) / 4 = (48 - 44.9444) / 4 = 0.76390m
#   → 斜線のかからない階は 間口 17.4722m × 奥行 27.4722m = 480.0m2


def test_hand_calc_bcr_inset():
    r = solve(make())
    assert r.bcr_inset_mm == pytest.approx(0.76390 * MM, abs=0.01 * MM)
    f1 = r.floors[0]
    assert f1.width_mm == pytest.approx(17.4722 * MM, abs=0.01 * MM)
    assert f1.depth_mm == pytest.approx(27.4722 * MM, abs=0.01 * MM)
    assert f1.gross_area_mm2 == pytest.approx(480.0 * M2, rel=1e-6)


def test_hand_calc_road12():
    """道路12m。容積低減なし（600%、上限3600m2）。

    道路側後退 = max(0, min(天端/1.5, 25m) - 12m)
    隣地側後退 = max(0, (天端 - 31m) / 2.5)
    辺ごとの控除量 = max(0.5m, その辺の斜線後退) + t   （t = 0.76390m）

      階  天端   道路   隣地   間口              奥行                      面積
       1   4.5   0.00   0.00  20-2(0.5+t)=17.4722  30-2(0.5+t)=27.4722   480.000
       2   8.7   0.00   0.00  17.4722              27.4722               480.000
       3  12.9   0.00   0.00  17.4722              27.4722               480.000
       4  17.1   0.00   0.00  17.4722              27.4722               480.000
       5  21.3   2.20   0.00  17.4722  30-(2.2+t)-(0.5+t)     =25.7722   450.297
       6  25.5   5.00   0.00  17.4722  30-(5.0+t)-(0.5+t)     =22.9722   401.375
       7  29.7   7.80   0.00  17.4722  30-(7.8+t)-(0.5+t)     =20.1722   352.453
       8  33.9  10.60   1.16  20-2(1.16+t)=16.1522  30-(10.6+t)-(1.16+t)=16.7122  269.939
       9  38.1  13.00   2.84  20-2(2.84+t)=12.7922  30-(13.0+t)-(2.84+t)=12.6322  161.594

      9階の道路側は 38.1/1.5 = 25.4m > 適用距離25m なので 25-12 = 13.0m で頭打ち。
      10階（天端42.3m）は面積 103.303m2 で最小成立面積は満たすが、
      累計 3555.658 + 103.303 = 3658.96m2 が上限3600m2 を超えるため打ち切り。

    累計 3555.658m2 ≦ 3600m2、最高高さ 38.1m
    """
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    assert r.far_effective == pytest.approx(6.0)
    assert r.max_far_area_mm2 == pytest.approx(3600.0 * M2)
    assert r.floor_count == 9
    assert r.max_height_mm == pytest.approx(38.1 * MM)
    assert r.stop_reason is StopReason.FAR_LIMIT_REACHED

    expected_setback_road_m = [0, 0, 0, 0, 2.2, 5.0, 7.8, 10.6, 13.0]
    expected_area_m2 = [480.0, 480.0, 480.0, 480.0, 450.297,
                        401.375, 352.453, 269.939, 161.594]
    for f, sb, area in zip(r.floors, expected_setback_road_m, expected_area_m2):
        assert f.setback_road_mm == pytest.approx(sb * MM, abs=0.01 * MM), f"{f.floor}階"
        assert f.gross_area_mm2 == pytest.approx(area * M2, rel=1e-4), f"{f.floor}階"

    assert r.total_far_area_mm2 == pytest.approx(3555.658 * M2, rel=1e-5)
    assert r.floors[-1].governing == "道路斜線(適用距離で頭打ち) + 隣地斜線 + 建蔽率"


def test_hand_calc_setbacks_take_the_larger_not_the_sum():
    """外壁後退0.5mと斜線後退は足さず、大きいほうだけが効く。

    8階は隣地斜線が 1.16m を要求する。間口は 20 - 2×(1.16 + t) であって、
    20 - 2×(0.5 + 1.16 + t) ではない。
    """
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    t = r.bcr_inset_mm / MM
    f8 = r.floors[7]
    assert f8.setback_neighbor_mm == pytest.approx(1.16 * MM, abs=0.01 * MM)
    assert f8.width_mm / MM == pytest.approx(20 - 2 * (1.16 + t), abs=0.01)
    assert f8.width_mm / MM != pytest.approx(20 - 2 * (0.5 + 1.16 + t), abs=0.01)

    # 斜線が外壁後退より小さい階は、外壁後退がそのまま効く
    f1 = r.floors[0]
    assert f1.setback_neighbor_mm == 0.0
    assert f1.width_mm / MM == pytest.approx(20 - 2 * (0.5 + t), abs=0.01)


def test_hand_calc_road6():
    """道路6m。容積が 6 × 0.6 = 360% に低減され、上限 2160m2。

    道路側後退 = max(0, 天端/1.5 - 6m)。隣地斜線は 31m 未満なのでどの階も 0。
    間口はどの階も 20 - 2×(0.5 + t) = 17.4722m、奥行は 30 - (道路側) - (0.5 + t)。

      階  天端   道路   奥行                          面積      累計
       1   4.5   0.00   30-(0.5+t)-(0.5+t) = 27.4722  480.000   480.000
       2   8.7   0.00                        27.4722  480.000   960.000
       3  12.9   2.60   30-(2.6+t)-(0.5+t) = 25.3722  443.308  1403.308
       4  17.1   5.40                        22.5722  394.386  1797.694
       5  21.3   8.20                        19.7722  345.464  2143.159
       6  25.5  11.00                        16.9722  296.542  → 2439.70 > 2160 で打切

    最高高さ 21.3m
    """
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road6.json"))
    assert r.far_effective == pytest.approx(3.6)
    assert r.max_far_area_mm2 == pytest.approx(2160.0 * M2)
    assert r.floor_count == 5
    assert r.max_height_mm == pytest.approx(21.3 * MM)
    assert r.stop_reason is StopReason.FAR_LIMIT_REACHED

    expected_setback_road_m = [0, 0, 2.6, 5.4, 8.2]
    expected_area_m2 = [480.0, 480.0, 443.308, 394.386, 345.464]
    for f, sb, area in zip(r.floors, expected_setback_road_m, expected_area_m2):
        assert f.setback_road_mm == pytest.approx(sb * MM, abs=0.01 * MM), f"{f.floor}階"
        assert f.gross_area_mm2 == pytest.approx(area * M2, rel=1e-4), f"{f.floor}階"

    assert r.total_far_area_mm2 == pytest.approx(2143.159 * M2, rel=1e-5)


def test_road6_and_road12_differ():
    """STEP 7 の確認内容の先取り：幅員で結果が変わる。"""
    r6 = solve(VolumeInput.from_json_file(SAMPLES / "case_road6.json"))
    r12 = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    assert r12.floor_count > r6.floor_count
    assert r12.max_height_mm > r6.max_height_mm
    assert r12.total_far_area_mm2 > r6.total_far_area_mm2


def _slant_margins(r):
    """各階の (道路斜線の余裕, 隣地斜線の余裕) を返す。単位 mm。"""
    W = r.input.site.road_width_mm
    D = r.input.site.depth_mm
    g = r.road_slant_gradient
    L = r.road_slant_applicable_distance_mm
    for f in r.floors:
        dist_from_opposite = f.y_min_mm + W
        road = float("inf") if dist_from_opposite >= L else dist_from_opposite * g - f.top_mm
        if r.neighbor_slant_start_mm is None:
            nb = float("inf")
        else:
            h0, ng = r.neighbor_slant_start_mm, r.neighbor_slant_gradient
            nb = h0 + (D - f.y_max_mm) * ng - f.top_mm
        yield f, road, nb


@pytest.mark.parametrize("road_width", [4.0, 6.0, 9.0, 12.0, 20.0])
@pytest.mark.parametrize("district", ["商業地域", "第一種住居地域", "工業地域"])
def test_all_floors_fit_within_slant_envelopes(road_width, district):
    """全階が道路斜線・隣地斜線の内側に収まっている。

    断面図で「斜線に建物が収まっている」ことを示すのがこのツールの価値なので、
    その前提を数値で担保する。
    """
    r = solve(make(site={"road_width": road_width},
                   zoning={"use_district": district, "far_designated": 4.0}))
    for f, road_margin, neighbor_margin in _slant_margins(r):
        assert road_margin >= -1e-6, f"{f.floor}階が道路斜線を超えている"
        assert neighbor_margin >= -1e-6, f"{f.floor}階が隣地斜線を超えている"


def test_slant_margin_equals_the_bcr_inset_when_road_slant_governs():
    """道路斜線が支配している階の余裕は、建蔽率の絞り込み × 勾配に一致する。

    外壁後退は斜線と足し合わせず大きいほうを採るので、斜線が支配している階では
    外壁後退は余裕を生まない。余裕を作るのは建蔽率の絞り込みだけ。
    """
    r = solve(make(site={"road_width": 6.0}))
    for f, road_margin, _ in _slant_margins(r):
        if f.setback_road_mm > 0:
            assert road_margin == pytest.approx(
                r.bcr_inset_mm * r.road_slant_gradient, abs=1.0)


def test_the_wall_setback_governs_only_while_the_slant_is_smaller():
    """外壁後退より斜線が小さい階では、外壁後退の分だけ余裕が出る。"""
    r = solve(make(site={"road_width": 6.0}))
    edge = r.input.program.wall_setback_mm + r.bcr_inset_mm
    for f, road_margin, _ in _slant_margins(r):
        if f.setback_road_mm == 0:
            assert road_margin >= edge * r.road_slant_gradient - 1.0


# ---------------------------------------------------------------------------
# どの規定がボリュームを削っているか
# ---------------------------------------------------------------------------


def test_impacts_are_the_marginal_gain_of_relaxing_each_rule():
    """各規定の増分は「その規定だけを外したときの床面積の増分」に一致する。

    道路12m・商業の 8階（天端33.90m）で手計算。
    控除量は辺ごとに max(外壁後退0.5m, その辺の斜線) + t、t = (48-sqrt(2020))/4。

      t = 0.76389747m、道路側 10.6m、隣地側 1.16m

      実際         (20 - 2×(1.16+t)) × (30 - (10.6+t) - (1.16+t))
                   = 16.15221 × 16.71221 = 269.93896m2
      道路斜線なし  16.15221 × (30 - (0.5+t) - (1.16+t)) = 16.15221 × 26.81221
                   = 433.07623m2 → 増分 163.13727m2
      隣地斜線なし  (20 - 2×(0.5+t)) × (30 - (10.6+t) - (0.5+t))
                   = 17.47221 × 17.37221 = 303.53073m2 → 増分 33.59177m2
      建蔽率なし    (20 - 2×1.16) × (30 - 10.6 - 1.16) = 17.68 × 18.24
                   = 322.48320m2 → 増分 52.54424m2
    """
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    f8 = r.floors[7]
    assert f8.floor == 8
    assert f8.gross_area_mm2 == pytest.approx(269.93896 * M2, rel=1e-6)
    gains = {i.constraint: i.area_gain_mm2 for i in f8.impacts}
    assert gains[C_.Constraint.ROAD_SLANT] == pytest.approx(163.13727 * M2, rel=1e-6)
    assert gains[C_.Constraint.NEIGHBOR_SLANT] == pytest.approx(33.59177 * M2, rel=1e-6)
    assert gains[C_.Constraint.BCR] == pytest.approx(52.54424 * M2, rel=1e-6)


def test_impacts_are_sorted_and_non_negative():
    for name in ("case_road6", "case_road12"):
        r = solve(VolumeInput.from_json_file(SAMPLES / f"{name}.json"))
        for f in r.floors:
            gains = [i.area_gain_mm2 for i in f.impacts]
            assert all(g > 0 for g in gains), f"{f.floor}階に増分0以下の要因がある"
            assert gains == sorted(gains, reverse=True), f"{f.floor}階の並びが降順でない"
            assert f.dominant_constraint == f.impacts[0].constraint


def test_impacts_only_list_rules_that_actually_apply():
    """後退量が0の規定は要因に挙げない。"""
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    for f in r.floors:
        listed = set(f.constraints)
        if f.setback_road_mm == 0:
            assert C_.Constraint.ROAD_SLANT not in listed, f"{f.floor}階"
        if f.setback_neighbor_mm == 0:
            assert C_.Constraint.NEIGHBOR_SLANT not in listed, f"{f.floor}階"
        if r.bcr_inset_mm == 0:
            assert C_.Constraint.BCR not in listed, f"{f.floor}階"


def test_no_impacts_when_nothing_constrains_the_floor():
    """斜線も建蔽率もかからない条件では要因が空になる。"""
    r = solve(make(
        site={"road_width": 30.0, "frontage": 60.0, "depth": 60.0},
        zoning={"bcr": 1.0, "far_designated": 20.0},
        program={"max_floors": 1},
    ))
    f1 = r.floors[0]
    assert f1.impacts == ()
    assert f1.dominant_constraint is None
    assert f1.governing == "敷地形状・外壁後退のみ"
    assert f1.area_loss_mm2 == pytest.approx(0.0, abs=1.0)


def test_area_loss_is_the_gap_from_the_unconstrained_footprint():
    """削減量 = 外壁後退のみの面積 - 実面積。"""
    inp = VolumeInput.from_json_file(SAMPLES / "case_road12.json")
    r = solve(inp)
    setback = inp.program.wall_setback_mm
    expected = ((inp.site.frontage_mm - 2 * setback)
                * (inp.site.depth_mm - 2 * setback))
    for f in r.floors:
        assert f.unconstrained_area_mm2 == pytest.approx(expected)
        assert f.area_loss_mm2 == pytest.approx(expected - f.gross_area_mm2)
    assert r.total_area_loss_mm2 == pytest.approx(
        sum(f.area_loss_mm2 for f in r.floors)
    )


def test_constraint_gains_are_aggregated_and_sorted():
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    gains = r.constraint_gains_mm2()
    assert list(gains) == sorted(gains, key=gains.get, reverse=True)
    assert r.dominant_constraint is next(iter(gains))
    for constraint, total in gains.items():
        per_floor = sum(i.area_gain_mm2 for f in r.floors for i in f.impacts
                        if i.constraint is constraint)
        assert total == pytest.approx(per_floor)


def test_the_bcr_outweighs_the_road_slant_on_this_site():
    """道路12m・商業（20×30m・建蔽率80%）では建蔽率が最大の要因になる。

    建蔽率は 551m2 の素地を 480m2 まで絞るので全9階に効く（1階あたり 41〜71m2）。
    道路斜線が効くのは 5階以上の5層だけで、合計では建蔽率がわずかに上回る。
    僅差なので、外壁後退の扱いのような小さな条件変更で順位は入れ替わりうる。
    """
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    gains = r.constraint_gains_mm2()
    assert r.dominant_constraint is C_.Constraint.BCR
    assert gains[C_.Constraint.BCR] / M2 == pytest.approx(570.1, abs=0.5)
    assert gains[C_.Constraint.ROAD_SLANT] / M2 == pytest.approx(558.9, abs=0.5)
    assert gains[C_.Constraint.NEIGHBOR_SLANT] / M2 == pytest.approx(133.6, abs=0.5)

    # 建蔽率は全階に効き、道路斜線は上層だけに効く
    assert all(C_.Constraint.BCR in f.constraints for f in r.floors)
    assert [f.floor for f in r.floors
            if C_.Constraint.ROAD_SLANT in f.constraints] == [5, 6, 7, 8, 9]


def test_wider_road_reduces_the_road_slant_impact():
    """幅員を広げると道路斜線による削減が小さくなる。"""
    narrow = solve(make(site={"road_width": 6.0}))
    wide = solve(make(site={"road_width": 12.0}))
    key = C_.Constraint.ROAD_SLANT
    # 同じ階どうしで比べる（階数が違うため）
    for a, b in zip(narrow.floors, wide.floors):
        ga = next((i.area_gain_mm2 for i in a.impacts if i.constraint is key), 0.0)
        gb = next((i.area_gain_mm2 for i in b.impacts if i.constraint is key), 0.0)
        assert gb <= ga + 1.0, f"{a.floor}階: 幅員を広げたのに道路斜線の影響が増えた"


def test_every_constraint_has_a_statutory_basis():
    for constraint in C_.Constraint:
        assert constraint.basis.startswith("法"), constraint


# ---------------------------------------------------------------------------
# 建蔽率の緩和（法53条3項・6項）
# ---------------------------------------------------------------------------


def test_no_relaxation_by_default():
    r = solve(make())
    assert r.bcr_effective == pytest.approx(0.8)
    assert r.bcr_relaxations == []
    assert any("未適用" in n for n in r.notes)


def test_fireproof_in_fire_zone_with_bcr_80_removes_the_limit():
    """指定建蔽率8/10の地域内の防火地域で耐火建築物等 → 建蔽率の制限なし（法53条6項1号）。"""
    r = solve(make(zoning={"fire_zone": "防火地域"}, program={"fireproof": True}))
    assert r.bcr_effective == pytest.approx(1.0)
    assert r.max_building_area_mm2 == pytest.approx(r.site_area_mm2)
    assert any("法53条6項1号" == basis for _, basis in r.bcr_relaxations)
    # 建蔽率で絞られなくなるので1階は外壁後退のみの大きさになる
    assert r.bcr_inset_mm == pytest.approx(0.0)
    assert r.floors[0].gross_area_mm2 == pytest.approx(19.0 * 29.0 * M2)


def test_fireproof_in_fire_zone_with_lower_bcr_adds_ten_percent():
    """8/10以外の地域では +1/10（法53条3項1号）。"""
    r = solve(make(zoning={"bcr": 0.6, "fire_zone": "防火地域"},
                   program={"fireproof": True}))
    assert r.bcr_effective == pytest.approx(0.7)
    assert any("法53条3項1号" == basis for _, basis in r.bcr_relaxations)


def test_quasi_fire_zone_and_corner_lot_stack_to_twenty_percent():
    """準防火地域の耐火建築物等 + 角地 → +2/10（法53条3項）。"""
    r = solve(make(zoning={"bcr": 0.6, "fire_zone": "準防火地域"},
                   program={"fireproof": True}, site={"corner_lot": True}))
    assert r.bcr_effective == pytest.approx(0.8)
    bases = {basis for _, basis in r.bcr_relaxations}
    assert bases == {"法53条3項1号", "法53条3項2号"}


def test_corner_lot_alone_adds_ten_percent():
    r = solve(make(site={"corner_lot": True}, zoning={"bcr": 0.6}))
    assert r.bcr_effective == pytest.approx(0.7)


def test_fire_zone_without_fireproof_gives_no_relaxation():
    """防火地域でも耐火建築物等としなければ緩和されない。"""
    r = solve(make(zoning={"fire_zone": "防火地域"}, program={"fireproof": False}))
    assert r.bcr_effective == pytest.approx(0.8)
    assert r.bcr_relaxations == []


def test_relaxed_bcr_never_exceeds_one():
    r = solve(make(zoning={"bcr": 0.95, "fire_zone": "準防火地域"},
                   program={"fireproof": True}, site={"corner_lot": True}))
    assert r.bcr_effective == pytest.approx(1.0)


def test_relaxation_increases_the_building_area():
    plain = solve(make(zoning={"bcr": 0.6}))
    relaxed = solve(make(zoning={"bcr": 0.6, "fire_zone": "防火地域"},
                         program={"fireproof": True}))
    assert relaxed.building_area_mm2 > plain.building_area_mm2
    assert relaxed.max_building_area_mm2 == pytest.approx(
        plain.max_building_area_mm2 * 0.7 / 0.6
    )


def test_relaxation_is_recorded_in_applied_rules():
    r = solve(make(zoning={"fire_zone": "防火地域"}, program={"fireproof": True}))
    labels = {rule.label for rule in r.applied_rules}
    assert {"指定建蔽率", "防火地域の指定", "建蔽率の緩和", "緩和後の建蔽率"} <= labels
    assert not any("未適用" in n for n in r.notes)
