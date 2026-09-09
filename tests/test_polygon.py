"""非矩形の敷地と建物の回転の検証。

矩形限定をやめたので、
  ・矩形は従来と完全に同じ結果になること（後退の計算が変わっていないこと）
  ・任意の多角形でも斜線が境界線からの距離として正しく効くこと
  ・建物を振れること
を確かめる。
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geometry as G  # noqa: E402
from models import Constraint, VolumeInput  # noqa: E402
from solver import solve  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
M2 = 1e6
MM = 1000.0


def payload(name: str = "case_road12") -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 矩形との等価性 — 土台を入れ替えても従来の結果が変わっていないこと
# ---------------------------------------------------------------------------


def test_polygon_input_reproduces_the_rectangle_input_exactly():
    """同じ矩形を boundary で与えても、従来の frontage/depth 指定と一致する。"""
    body = payload()
    rect = solve(VolumeInput.from_dict(body))

    poly_body = copy.deepcopy(body)
    poly_body["site"] = {
        "boundary": [[0, 0], [20, 0], [20, 30], [0, 30]],
        "edges": [
            {"kind": "road", "width": 12.0},
            {"kind": "neighbor"}, {"kind": "neighbor"}, {"kind": "neighbor"},
        ],
        "road_side": "south",
    }
    poly = solve(VolumeInput.from_dict(poly_body))

    assert poly.site_area_mm2 == pytest.approx(rect.site_area_mm2)
    assert poly.floor_count == rect.floor_count
    assert poly.max_height_mm == pytest.approx(rect.max_height_mm)
    assert poly.total_gross_area_mm2 == pytest.approx(rect.total_gross_area_mm2, rel=1e-9)
    assert poly.bcr_inset_mm == pytest.approx(rect.bcr_inset_mm, abs=1e-3)
    for a, b in zip(rect.floors, poly.floors):
        assert b.gross_area_mm2 == pytest.approx(a.gross_area_mm2, rel=1e-9)
        assert b.setback_road_mm == pytest.approx(a.setback_road_mm)
        assert b.setback_neighbor_mm == pytest.approx(a.setback_neighbor_mm)


def test_rectangle_buildable_region_matches_the_simple_formula():
    """矩形では「間口 - 隣地SB×2 - 外壁後退×2」という単純計算と厳密に一致する。"""
    site = G.rectangle_site(20000, 30000, 12000)
    sb_road, sb_nb, wall = 10600.0, 1160.0, 500.0
    setbacks = [sb_road if e.kind is G.EdgeKind.ROAD else sb_nb for e in site.edges]
    region = G.buildable_region(site, setbacks, wall)

    expected_w = 20000 - 2 * sb_nb - 2 * wall
    expected_d = 30000 - sb_road - sb_nb - 2 * wall
    minx, miny, maxx, maxy = region.bounds
    assert maxx - minx == pytest.approx(expected_w, abs=1e-6)
    assert maxy - miny == pytest.approx(expected_d, abs=1e-6)
    assert region.area == pytest.approx(expected_w * expected_d, abs=1e-6)


# ---------------------------------------------------------------------------
# 任意形状
# ---------------------------------------------------------------------------


def test_polygon_site_solves_and_stays_inside_the_boundary():
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_polygon.json"))
    assert r.floor_count >= 1
    site_poly = r.input.site.shape.polygon
    for f in r.floors:
        shape = G.Polygon(f.outline)
        assert site_poly.covers(shape.buffer(1e-6)), f"{f.floor}階が敷地からはみ出している"
        assert f.gross_area_mm2 == pytest.approx(shape.area, rel=1e-9)


def test_polygon_site_respects_the_wall_setback_everywhere():
    """どの階も敷地境界から外壁後退以上離れている。"""
    inp = VolumeInput.from_json_file(SAMPLES / "case_polygon.json")
    r = solve(inp)
    boundary = inp.site.shape.polygon.exterior
    for f in r.floors:
        distance = boundary.distance(G.Polygon(f.outline))
        assert distance >= inp.program.wall_setback_mm - 1.0, f"{f.floor}階"


def test_slant_setback_is_measured_from_each_boundary():
    """各階が、道路境界・隣地境界それぞれから所定の距離以上離れている。"""
    inp = VolumeInput.from_json_file(SAMPLES / "case_polygon.json")
    r = solve(inp)
    for f in r.floors:
        shape = G.Polygon(f.outline)
        for edge, in zip(inp.site.shape.edges):
            if edge.kind is G.EdgeKind.ROAD:
                required = max(0.0, min(f.top_mm / r.road_slant_gradient,
                                        r.road_slant_applicable_distance_mm)
                               - edge.road_width_mm)
            elif r.neighbor_slant_start_mm is None:
                required = 0.0
            else:
                required = max(0.0, (f.top_mm - r.neighbor_slant_start_mm)
                               / r.neighbor_slant_gradient)
            assert edge.line().distance(shape) >= required - 1.0, (
                f"{f.floor}階が{edge.kind.value}境界から{required / MM:.2f}m離れていない"
            )


def test_concave_site_is_handled():
    """L字型（凹）の敷地でも成立し、建物は敷地内に収まる。"""
    body = payload()
    body["site"] = {
        "boundary": [[0, 0], [24, 0], [24, 12], [12, 12], [12, 26], [0, 26]],
        "edges": [{"kind": "road", "width": 10.0}] + [{"kind": "neighbor"}] * 5,
        "road_side": "south",
    }
    r = solve(VolumeInput.from_dict(body))
    assert r.site_area_mm2 == pytest.approx((24 * 12 + 12 * 14) * M2)
    assert r.floor_count >= 1
    site_poly = r.input.site.shape.polygon
    for f in r.floors:
        assert site_poly.covers(G.Polygon(f.outline).buffer(1e-6)), f"{f.floor}階"


def test_two_roads_use_their_own_widths():
    """2つの道路に接する場合、各辺がそれぞれの幅員で判定される。"""
    body = payload()
    body["site"] = {
        "boundary": [[0, 0], [24, 0], [24, 24], [0, 24]],
        "edges": [
            {"kind": "road", "width": 12.0},   # 南 12m
            {"kind": "road", "width": 4.0},    # 東 4m
            {"kind": "neighbor"}, {"kind": "neighbor"},
        ],
        "road_side": "south", "corner_lot": True,
    }
    r = solve(VolumeInput.from_dict(body))
    # 容積率の低減は最大幅員（12m）で判定する
    assert r.far_by_road is None
    edges = r.input.site.shape.edges
    assert {e.road_width_mm for e in edges if e.kind is G.EdgeKind.ROAD} == {12000.0, 4000.0}
    # 幅員4mの側のほうが早く後退が始まる
    narrow = next(e for e in edges if e.road_width_mm == 4000.0)
    wide = next(e for e in edges if e.road_width_mm == 12000.0)
    top = r.floors[-1].top_mm
    g, L = r.road_slant_gradient, r.road_slant_applicable_distance_mm
    assert (max(0, min(top / g, L) - narrow.road_width_mm)
            > max(0, min(top / g, L) - wide.road_width_mm))


def test_site_without_any_road_is_rejected():
    body = payload()
    body["site"] = {
        "boundary": [[0, 0], [20, 0], [20, 20], [0, 20]],
        "edges": [{"kind": "neighbor"}] * 4,
        "road_side": "south",
    }
    with pytest.raises(ValueError, match="道路"):
        VolumeInput.from_dict(body)


def test_self_intersecting_boundary_is_rejected():
    body = payload()
    body["site"] = {
        "boundary": [[0, 0], [20, 20], [20, 0], [0, 20]],   # 蝶ネクタイ形
        "edges": [{"kind": "road", "width": 6.0}] + [{"kind": "neighbor"}] * 3,
        "road_side": "south",
    }
    with pytest.raises(ValueError):
        VolumeInput.from_dict(body)


# ---------------------------------------------------------------------------
# 建物の回転
# ---------------------------------------------------------------------------


def test_default_building_angle_is_parallel_to_the_road():
    r = solve(VolumeInput.from_dict(payload()))
    assert r.building_angle_deg == pytest.approx(0.0, abs=1e-9)


def test_building_angle_is_applied():
    body = payload()
    body["program"]["building_angle"] = 30.0
    r = solve(VolumeInput.from_dict(body))
    assert r.building_angle_deg == pytest.approx(30.0)


def test_rotating_the_building_changes_the_result_on_a_polygon_site():
    base = json.loads((SAMPLES / "case_polygon.json").read_text(encoding="utf-8"))
    areas = {}
    for angle in (-20, 0, 20):
        d = copy.deepcopy(base)
        d["program"]["building_angle"] = angle
        areas[angle] = solve(VolumeInput.from_dict(d)).total_gross_area_mm2
    assert len(set(areas.values())) == 3, "振り角を変えても結果が変わっていない"
    # この敷地では道路平行より振ったほうが取れる
    assert areas[-20] > areas[0]


def test_rotating_on_a_rectangle_site_never_beats_the_parallel_placement():
    """矩形敷地では道路に平行が最大。振ると必ず小さくなる。"""
    body = payload()
    straight = solve(VolumeInput.from_dict(body)).floors[0].gross_area_mm2
    for angle in (10, 25, 40):
        d = copy.deepcopy(body)
        d["program"]["building_angle"] = angle
        assert solve(VolumeInput.from_dict(d)).floors[0].gross_area_mm2 <= straight + 1.0


def test_rotated_building_is_actually_rotated():
    """振り角を付けた外形の辺が、敷地の辺と平行でなくなる。"""
    body = payload()
    body["program"]["building_angle"] = 30.0
    r = solve(VolumeInput.from_dict(body))
    outline = r.floors[0].outline
    edge_angle = math.degrees(math.atan2(outline[1][1] - outline[0][1],
                                         outline[1][0] - outline[0][0])) % 90
    assert edge_angle == pytest.approx(30.0, abs=1.0)


def test_impacts_still_work_on_a_polygon_site():
    r = solve(VolumeInput.from_json_file(SAMPLES / "case_polygon.json"))
    gains = r.constraint_gains_mm2()
    assert Constraint.ROAD_SLANT in gains
    assert all(g > 0 for g in gains.values())
    for f in r.floors:
        assert f.area_loss_mm2 >= -1.0
