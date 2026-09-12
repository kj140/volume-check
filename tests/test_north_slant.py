"""北側斜線（法56条1項3号）の検証。

道路斜線・隣地斜線が「その境界線に垂直な距離」で決まるのに対し、北側斜線は
真北方向の水平距離で決まる。敷地の向きと無関係に真北を向くこと、その向きに
沿って正しく削られることを確かめる。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constants as C  # noqa: E402
import geometry as G  # noqa: E402
from models import Constraint, VolumeInput  # noqa: E402
from solver import north_setback, solve  # noqa: E402
from web.app import app  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
MM = C.M_TO_MM
M2 = C.M2_TO_MM2
client = TestClient(app)


def make(district: str = "第一種中高層住居専用地域", **zoning) -> dict:
    body = json.loads((SAMPLES / "case_road6.json").read_text(encoding="utf-8"))
    defaults = {"use_district": district, "far_designated": 3.0, "bcr": 0.6,
                "height_limit_absolute": None}
    body["zoning"].update({**defaults, **zoning})
    body["program"]["wall_setback"] = 1.0
    return body


# ---------------------------------------------------------------------------
# 適用のある用途地域（法56条1項3号）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("district,expected", [
    ("第一種低層住居専用地域", (5.0, 1.25)),
    ("第二種低層住居専用地域", (5.0, 1.25)),
    ("田園住居地域", (5.0, 1.25)),
    ("第一種中高層住居専用地域", (10.0, 1.25)),
    ("第二種中高層住居専用地域", (10.0, 1.25)),
    ("第一種住居地域", None),
    ("近隣商業地域", None),
    ("商業地域", None),
    ("準工業地域", None),
    ("指定なし", None),
])
def test_north_slant_applies_only_to_the_listed_districts(district, expected):
    assert C.north_slant(C.UseDistrict(district)) == expected


def test_the_shadow_rule_replaces_the_north_slant_in_mid_rise_districts():
    """中高層住専は日影規制の対象区域だと北側斜線が外れる（法56条1項3号かっこ書き）。"""
    for name in ("第一種中高層住居専用地域", "第二種中高層住居専用地域"):
        district = C.UseDistrict(name)
        assert C.north_slant(district) is not None
        assert C.north_slant(district, shadow_rule_applies=True) is None


def test_low_rise_districts_keep_the_north_slant_even_with_the_shadow_rule():
    """低層住専・田園住居は日影規制の有無にかかわらず北側斜線がかかる。"""
    for name in ("第一種低層住居専用地域", "第二種低層住居専用地域", "田園住居地域"):
        district = C.UseDistrict(name)
        assert C.north_slant(district, shadow_rule_applies=True) == (5.0, 1.25)


def test_the_solver_picks_up_the_shadow_rule_switch():
    a = solve(VolumeInput.from_dict(make()))
    b = solve(VolumeInput.from_dict(make(shadow_regulation=True)))
    assert a.north_slant_start_mm == pytest.approx(10.0 * MM)
    assert a.north_slant_gradient == pytest.approx(1.25)
    assert b.north_slant_start_mm is None
    assert any("日影規制" in n and "未検証" in n for n in b.notes), \
        "日影規制で北側斜線を外したのに、日影規制が未検証である旨の注記がない"


# ---------------------------------------------------------------------------
# 必要な距離
# ---------------------------------------------------------------------------


def test_required_distance_is_measured_from_the_rise():
    for top_m, expected in ((5.0, 0.0), (10.0, 0.0), (12.5, 2.0), (20.0, 8.0)):
        assert north_setback(top_m * MM, 10.0 * MM, 1.25) == pytest.approx(
            expected * MM)


def test_no_distance_is_required_where_the_slant_does_not_apply():
    assert north_setback(30.0 * MM, None, None) == 0.0


def test_each_floor_records_the_distance_it_needed():
    r = solve(VolumeInput.from_dict(make()))
    for f in r.floors:
        expected = max(0.0, (f.top_mm - r.north_slant_start_mm)
                       / r.north_slant_gradient)
        assert f.setback_north_mm == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 幾何（真北方向に削る）
# ---------------------------------------------------------------------------


def test_the_region_is_cut_towards_true_north():
    """北へ d 必要なら、敷地の北端から d だけ南に下がった範囲だけが残る。"""
    site = G.rectangle_site(20000, 30000, 6000).polygon      # 0..20 x 0..30
    north = math.pi / 2                                      # +y が北
    for d in (0.0, 3000.0, 10000.0):
        region = G.north_slant_region(site, site, north, d)
        assert region.bounds[3] == pytest.approx(30000.0 - d, abs=1e-6)
        assert region.bounds[1] == pytest.approx(0.0, abs=1e-6)
        assert region.area == pytest.approx(20000.0 * (30000.0 - d), abs=1e-3)


@pytest.mark.parametrize("north_deg,axis", [(90, "north"), (-90, "south"),
                                            (0, "east"), (180, "west")])
def test_the_cut_follows_the_north_direction_not_the_site_edges(north_deg, axis):
    """同じ敷地でも、北の向きが変われば削られる側が変わる。"""
    site = G.rectangle_site(20000, 30000, 6000).polygon
    region = G.north_slant_region(site, site, math.radians(north_deg), 4000.0)
    minx, miny, maxx, maxy = region.bounds
    if axis == "north":
        assert (miny, maxy) == pytest.approx((0.0, 26000.0), abs=1e-6)
    elif axis == "south":
        assert (miny, maxy) == pytest.approx((4000.0, 30000.0), abs=1e-6)
    elif axis == "east":
        assert (minx, maxx) == pytest.approx((0.0, 16000.0), abs=1e-6)
    else:
        assert (minx, maxx) == pytest.approx((4000.0, 20000.0), abs=1e-6)


def test_a_concave_site_is_eroded_along_north_not_just_shifted():
    """凹んだ敷地では、北へ進む途中で敷地の外に出る点も落ちる。"""
    # 北（+y）側が二股になった凹形状
    boundary = [(0.0, 0.0), (20000.0, 0.0), (20000.0, 20000.0),
                (12000.0, 20000.0), (12000.0, 8000.0), (8000.0, 8000.0),
                (8000.0, 20000.0), (0.0, 20000.0)]
    site = G.Polygon(boundary)
    region = G.north_slant_region(site, site, math.pi / 2, 6000.0, step_mm=250.0)
    # 谷の底（x=10m, y=7m）は北へ 6m 進むと切り欠きに入るので残らない
    assert not region.contains(G.Point(10000.0, 7000.0))
    # 左の腕（x=4m, y=7m）は北へ 6m 進んでも敷地内なので残る
    assert region.contains(G.Point(4000.0, 7000.0))


def test_no_cut_when_nothing_is_required():
    site = G.rectangle_site(20000, 30000, 6000).polygon
    assert G.north_slant_region(site, site, math.pi / 2, 0.0).equals(site)


# ---------------------------------------------------------------------------
# 算定結果
# ---------------------------------------------------------------------------


def test_upper_floors_are_pushed_south():
    """階が上がるほど北端が南へ下がる。"""
    r = solve(VolumeInput.from_dict(make()))
    site = r.input.site.shape.polygon
    north = r.input.site.north_angle_rad
    previous = None
    for f in r.floors:
        gap = G.north_projection_mm(G.Polygon(f.outline), site, north)
        assert gap >= f.setback_north_mm - 1.0, f"{f.floor}階が北側斜線を超えている"
        if previous is not None:
            assert gap >= previous - 1.0, f"{f.floor}階で北端が北に戻っている"
        previous = gap


def test_the_north_slant_shows_up_as_a_constraint():
    r = solve(VolumeInput.from_dict(make()))
    affected = [f for f in r.floors if f.setback_north_mm > 0]
    assert affected, "北側斜線が効く階がない条件になっている"
    assert any(Constraint.NORTH_SLANT in f.constraints for f in affected)
    assert any("北側斜線" in f.governing for f in r.floors)
    assert Constraint.NORTH_SLANT in r.constraint_gains_mm2()


def test_turning_off_the_north_slant_never_shrinks_a_floor():
    """北側斜線を外せば各階は同じか広くなる。"""
    with_slant = solve(VolumeInput.from_dict(make()))
    without = solve(VolumeInput.from_dict(make(shadow_regulation=True)))
    for a, b in zip(with_slant.floors, without.floors):
        assert b.gross_area_mm2 >= a.gross_area_mm2 - 1.0, f"{a.floor}階"


def test_commercial_districts_are_untouched():
    """北側斜線の適用がない用途地域では結果が変わらない。"""
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_road12.json"))
    assert r.north_slant_start_mm is None
    assert all(f.setback_north_mm == 0.0 for f in r.floors)
    assert r.total_gross_area_mm2 == pytest.approx(3555.658 * M2, rel=1e-5)


def test_the_applied_rules_state_which_way_it_went():
    for body, expected in ((make(), "立ち上がり 10m"),
                           (make(shadow_regulation=True), "日影規制の対象区域"),
                           (make("商業地域", far_designated=6.0, bcr=0.8), "適用なし")):
        r = solve(VolumeInput.from_dict(body))
        rule = next(x for x in r.applied_rules if x.label == "北側斜線")
        assert rule.basis == "法56条1項3号"
        assert expected in rule.value


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_the_endpoint_reports_the_north_slant():
    data = client.post("/api/solve", json=make()).json()
    assert data["summary"]["north_slant_start_m"] == 10.0
    assert data["summary"]["north_slant_gradient"] == 1.25
    assert data["summary"]["shadow_regulation"] is False
    assert any(f["setback_north_m"] > 0 for f in data["floors"])


def test_the_endpoint_honours_the_shadow_rule_switch():
    data = client.post("/api/solve", json=make(shadow_regulation=True)).json()
    assert data["summary"]["north_slant_start_m"] is None
    assert data["summary"]["shadow_regulation"] is True
    assert all(f["setback_north_m"] == 0 for f in data["floors"])


def test_the_sky_factor_study_warns_that_the_north_slant_is_not_covered():
    data = client.post("/api/skyfactor", json=make()).json()
    assert data["north_slant_unchecked"] is True
    assert any("北側斜線" in n and "未対応" in n for n in data["notes"])


def test_the_dxf_still_renders_with_a_north_slant():
    res = client.post("/api/dxf", json=make())
    assert res.status_code == 200
    assert res.content.startswith(b"  0\r\nSECTION")   # DXF の改行は CRLF
