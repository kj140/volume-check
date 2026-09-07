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
    assert {"用途地域", "建蔽率", "指定容積率", "実効容積率", "道路斜線 勾配",
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
# 建蔽率による絞り込み:
#   1階は斜線がかからず  間口 20 - 0.5×2 = 19.0m、奥行 30 - 0.5×2 = 29.0m → 551.0m2
#   建蔽率上限 600 × 0.8 = 480m2 を超えるので (19-2t)(29-2t) = 480 を解く
#   4t^2 - 96t + 71 = 0 → t = (48 - sqrt(2304 - 284)) / 4 = (48 - 44.9444) / 4 = 0.76390m
#   → 全階 間口 17.4722m / 奥行から 1.5278m を控除


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
      1-4階: 天端 4.5 / 8.7 / 12.9 / 17.1m → 天端/1.5 = 3.0 / 5.8 / 8.6 / 11.4m → 後退 0
      5階  : 21.3 / 1.5 = 14.2m        → 2.2m   奥行 30-2.2-1.5278 = 26.2722 ... 面積 441.56
      6階  : 25.5 / 1.5 = 17.0m        → 5.0m               面積 392.64
      7階  : 29.7 / 1.5 = 19.8m        → 7.8m               面積 343.72
      8階  : 33.9 / 1.5 = 22.6m        → 10.6m  隣地 (33.9-31)/2.5 = 1.16m   面積 238.08
      9階  : 38.1 / 1.5 = 25.4m > 25m  → 適用距離で頭打ち 25-12 = 13.0m
                                          隣地 (38.1-31)/2.5 = 2.84m         面積 137.17
      10階 : 面積 83.92m2 < 100m2 → 打ち切り
    累計 3473.16m2 ≦ 3600m2、最高高さ 38.1m
    """
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    assert r.far_effective == pytest.approx(6.0)
    assert r.max_far_area_mm2 == pytest.approx(3600.0 * M2)
    assert r.floor_count == 9
    assert r.max_height_mm == pytest.approx(38.1 * MM)
    assert r.stop_reason is StopReason.BELOW_MIN_FLOOR_AREA

    expected_setback_road_m = [0, 0, 0, 0, 2.2, 5.0, 7.8, 10.6, 13.0]
    expected_area_m2 = [480.0, 480.0, 480.0, 480.0, 441.561,
                        392.639, 343.717, 238.075, 137.169]
    for f, sb, area in zip(r.floors, expected_setback_road_m, expected_area_m2):
        assert f.setback_road_mm == pytest.approx(sb * MM, abs=0.01 * MM), f"{f.floor}階"
        assert f.gross_area_mm2 == pytest.approx(area * M2, rel=1e-4), f"{f.floor}階"

    assert r.total_far_area_mm2 == pytest.approx(3473.161 * M2, rel=1e-5)
    assert r.floors[-1].governing == "道路斜線(適用距離で頭打ち) + 隣地斜線 + 建蔽率"


def test_hand_calc_road6():
    """道路6m。容積が 6 × 0.6 = 360% に低減され、上限 2160m2。

    道路側後退 = max(0, 天端/1.5 - 6m)
      1階 4.5 → 3.0m  → 0       面積 480.00
      2階 8.7 → 5.8m  → 0       面積 480.00
      3階 12.9 → 8.6m → 2.6m    面積 434.57
      4階 17.1 → 11.4m→ 5.4m    面積 385.65
      5階 21.3 → 14.2m→ 8.2m    面積 336.73  累計 2116.95 ≦ 2160
      6階 25.5 → 17.0m→ 11.0m   面積 287.81  累計 2404.76 > 2160 → 打ち切り
    最高高さ 21.3m
    """
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road6.json"))
    assert r.far_effective == pytest.approx(3.6)
    assert r.max_far_area_mm2 == pytest.approx(2160.0 * M2)
    assert r.floor_count == 5
    assert r.max_height_mm == pytest.approx(21.3 * MM)
    assert r.stop_reason is StopReason.FAR_LIMIT_REACHED

    expected_setback_road_m = [0, 0, 2.6, 5.4, 8.2]
    expected_area_m2 = [480.0, 480.0, 434.572, 385.650, 336.728]
    for f, sb, area in zip(r.floors, expected_setback_road_m, expected_area_m2):
        assert f.setback_road_mm == pytest.approx(sb * MM, abs=0.01 * MM), f"{f.floor}階"
        assert f.gross_area_mm2 == pytest.approx(area * M2, rel=1e-4), f"{f.floor}階"

    assert r.total_far_area_mm2 == pytest.approx(2116.950 * M2, rel=1e-5)


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


def test_slant_margin_equals_wall_setback_effect_when_road_slant_governs():
    """道路斜線が支配している階の余裕は、外壁後退＋建蔽率絞り込み × 勾配に一致する。"""
    r = solve(make(site={"road_width": 6.0}))
    edge = r.input.program.wall_setback_mm + r.bcr_inset_mm
    for f, road_margin, _ in _slant_margins(r):
        if f.setback_road_mm > 0:
            assert road_margin == pytest.approx(edge * r.road_slant_gradient, abs=1.0)
