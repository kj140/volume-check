"""天空率の検証。

天空率は「半球の正射影面積の比」という定義そのものなので、まず算定式が
定義どおりであることを、式を使わない独立な方法（正射影円上を走査して各方向の
レイが建物に当たるかを幾何的に判定する）で確かめる。そのうえで算定位置・
適合建築物・判定の順に確かめる。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
import shapely
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constants as C  # noqa: E402
import geometry as G  # noqa: E402
import skyfactor as S  # noqa: E402
from models import VolumeInput  # noqa: E402
from solver import solve  # noqa: E402
from web.app import app  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
client = TestClient(app)


def payload(name: str = "case_road12") -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def result(name: str = "case_road12"):
    return solve(VolumeInput.from_json_file(SAMPLES / f"{name}.json"))


def box(x0: float, y0: float, x1: float, y1: float) -> G.Polygon:
    return G.Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


# ---------------------------------------------------------------------------
# 算定式そのものの検証
# ---------------------------------------------------------------------------


def sky_factor_by_ray_casting(slabs, position, n: int = 500) -> float:
    """天空率の独立な算定。skyfactor.py の式を一切使わない。

    全天空の正射影は半径1の円。円を格子で覆い、各点が表す方向へ実際に
    レイを飛ばして建築物に当たるかどうかを shapely の交差判定だけで決め、
    当たらなかった点の割合を返す。正射影面積の比の定義そのもの。
    """
    axis = (np.arange(n) + 0.5) / n * 2.0 - 1.0
    gx, gy = np.meshgrid(axis, axis)
    radius = np.hypot(gx, gy).ravel()
    inside = radius <= 1.0
    radius = radius[inside]
    theta = np.arctan2(gy.ravel()[inside], gx.ravel()[inside])

    # 正射影半径 r の点は仰角 φ = acos(r) の方向。水平距離 = 高さ * r / sin(φ)
    sin_phi = np.sqrt(np.maximum(1.0 - radius * radius, 1e-18))
    ux, uy = np.cos(theta), np.sin(theta)
    px, py = position

    blocked = np.zeros(len(radius), dtype=bool)
    for slab in slabs:
        reach = np.minimum(slab.top_mm * radius / sin_phi, 1e12)
        coords = np.stack([
            np.stack([np.full(len(radius), px), np.full(len(radius), py)], axis=1),
            np.stack([px + ux * reach, py + uy * reach], axis=1),
        ], axis=1)
        blocked |= shapely.intersects(shapely.linestrings(coords), slab.polygon)
    return 1.0 - blocked.mean()


CASES = {
    "箱": ([S.Slab(box(0, 0, 20000, 30000), 30000.0)], (10000.0, -8000.0)),
    "箱の正面": ([S.Slab(box(0, 0, 20000, 30000), 30000.0)], (0.0, -6000.0)),
    "2段": ([S.Slab(box(0, 0, 20000, 30000), 12000.0),
             S.Slab(box(5000, 5000, 15000, 15000), 40000.0)], (10000.0, -6000.0)),
    "五角形": ([S.Slab(G.Polygon([(0, 0), (26000, 0), (30000, 16000),
                                 (12000, 24000), (0, 14000)]), 25000.0)],
               (13000.0, -8000.0)),
}


@pytest.mark.parametrize("name", list(CASES))
def test_sky_factor_matches_independent_ray_casting(name):
    slabs, position = CASES[name]
    assert S.sky_factor(slabs, position) == pytest.approx(
        sky_factor_by_ray_casting(slabs, position), abs=1e-3
    )


def test_nothing_built_means_full_sky():
    assert S.sky_factor([], (0.0, 0.0)) == 1.0


def test_a_taller_building_blocks_more_sky():
    p = (10000.0, -8000.0)
    values = [S.sky_factor([S.Slab(box(0, 0, 20000, 30000), h)], p)
              for h in (10000.0, 20000.0, 40000.0)]
    assert values[0] > values[1] > values[2]


def test_moving_away_recovers_sky():
    slab = [S.Slab(box(0, 0, 20000, 30000), 30000.0)]
    values = [S.sky_factor(slab, (10000.0, -d)) for d in (4000.0, 10000.0, 40000.0)]
    assert values[0] < values[1] < values[2]
    assert values[2] > 0.95


def test_symmetric_building_gives_symmetric_values():
    slab = [S.Slab(box(0, 0, 20000, 30000), 30000.0)]
    left = S.sky_factor(slab, (2000.0, -6000.0))
    right = S.sky_factor(slab, (18000.0, -6000.0))
    assert left == pytest.approx(right, abs=1e-6)


def test_result_is_stable_against_the_azimuth_division():
    slabs, position = CASES["五角形"]
    coarse = S.sky_factor(slabs, position, divisions=720)
    fine = S.sky_factor(slabs, position, divisions=11520)
    assert coarse == pytest.approx(fine, abs=1e-3)


# ---------------------------------------------------------------------------
# 算定位置（令135条の9第1項）
# ---------------------------------------------------------------------------


def test_measurement_positions_sit_on_the_opposite_road_boundary():
    r = result()
    edge = r.input.site.shape.edges[0]
    line = edge.line()
    for position in S.measurement_positions(r, 0):
        assert line.distance(G.Point(*position)) == pytest.approx(
            edge.road_width_mm, abs=1e-6
        )


def test_measurement_positions_are_spaced_within_half_the_road_width():
    r = result()
    edge = r.input.site.shape.edges[0]
    positions = S.measurement_positions(r, 0)
    limit = edge.road_width_mm * C.SKY_FACTOR_ROAD_POINT_INTERVAL_RATIO
    assert len(positions) >= 2
    for a, b in zip(positions, positions[1:]):
        assert math.dist(a, b) <= limit + 1e-6


def test_measurement_positions_span_the_whole_frontage():
    """両端は、敷地が道路に接する部分の端から下ろした垂線の足になる。"""
    r = result()
    edge = r.input.site.shape.edges[0]
    positions = S.measurement_positions(r, 0)
    span = math.dist(positions[0], positions[-1])
    assert span == pytest.approx(edge.length_mm, abs=1e-6)
    # 端点は敷地の端から幅員だけ離れている
    assert math.dist(positions[0], edge.start) == pytest.approx(
        edge.road_width_mm, abs=1e-6)


def test_a_narrower_road_needs_more_measurement_positions():
    assert len(S.measurement_positions(result("case_road6"), 0)) > \
        len(S.measurement_positions(result("case_road12"), 0))


# ---------------------------------------------------------------------------
# 適合建築物（令135条の6）
# ---------------------------------------------------------------------------


def test_compliant_envelope_follows_the_road_slant():
    """各層が、道路境界から max(0, min(h/勾配, 適用距離) - 幅員) だけ下がる。"""
    r = result()
    edge = r.input.site.shape.edges[0]
    for slab in S.compliant_slabs(r, 0, 30000.0):
        required = min(slab.top_mm / r.road_slant_gradient,
                       r.road_slant_applicable_distance_mm)
        expected = max(0.0, required - edge.road_width_mm)
        assert edge.line().distance(slab.polygon) == pytest.approx(expected, abs=1e-3)


def test_compliant_envelope_fills_the_site_sideways():
    """適合建築物は当該道路の斜線だけに従う。隣地側は敷地いっぱい。"""
    r = result()
    site = r.input.site.shape.polygon
    lowest = S.compliant_slabs(r, 0, 30000.0)[0]
    for edge in r.input.site.shape.edges:
        if edge.kind is G.EdgeKind.NEIGHBOR:
            assert edge.line().distance(lowest.polygon) == pytest.approx(0.0, abs=1e-6)
    assert site.covers(lowest.polygon)
    # 幅員 12m のこの敷地では、最下層で道路斜線がまだ効かず敷地と一致する
    assert lowest.polygon.area == pytest.approx(site.area, rel=1e-9)


def test_compliant_envelope_stops_at_the_planned_height():
    r = result()
    slabs = S.compliant_slabs(r, 0, 25000.0)
    assert max(s.top_mm for s in slabs) == pytest.approx(25000.0)


def test_compliant_envelope_is_stable_against_the_layer_thickness():
    r = result()
    position = S.measurement_positions(r, 0)[0]
    coarse = S.sky_factor(S.compliant_slabs(r, 0, 30000.0, 2000.0), position)
    fine = S.sky_factor(S.compliant_slabs(r, 0, 30000.0, 125.0), position)
    assert coarse == pytest.approx(fine, abs=1e-3)


def test_the_compliant_envelope_ties_with_itself():
    """適合建築物そのものを計画建築物として比べれば、差は出ない。"""
    r = result()
    slabs = S.compliant_slabs(r, 0, 30000.0)
    for position in S.measurement_positions(r, 0):
        assert S.sky_factor(slabs, position) == S.sky_factor(slabs, position)


# ---------------------------------------------------------------------------
# 計画建築物
# ---------------------------------------------------------------------------


def test_planned_building_never_exceeds_the_far_limit():
    for name in ("case_road6", "case_road12", "case_polygon"):
        r = result(name)
        _, count, height_mm, total_mm2 = S.planned_slabs(r)
        assert count >= 1
        assert total_mm2 <= r.max_far_area_mm2 + 1.0
        assert height_mm == pytest.approx(
            sum(r.input.program.height_of_floor_mm(f) for f in range(1, count + 1))
        )


def test_planned_building_never_exceeds_the_bcr_limit():
    for name in ("case_road6", "case_road12", "case_polygon"):
        r = result(name)
        slabs, count, _, total_mm2 = S.planned_slabs(r)
        assert total_mm2 / count <= r.max_building_area_mm2 + 1.0
        assert r.input.site.shape.polygon.covers(slabs[0].polygon.buffer(1e-6))


def test_planned_building_keeps_the_wall_setback():
    r = result("case_polygon")
    slabs, _, _, _ = S.planned_slabs(r)
    boundary = r.input.site.shape.polygon.exterior
    assert boundary.distance(slabs[0].polygon) >= r.input.program.wall_setback_mm - 1.0


def test_planned_building_uses_the_fewest_floors_that_use_up_the_far():
    """1つ少ない階数では容積率を使い切れないところまで階数を減らしている。"""
    r = result()
    slabs, count, _, _ = S.planned_slabs(r)
    plate_mm2 = G.area_mm2(slabs[0].polygon)
    assert (count - 1) * plate_mm2 < r.max_far_area_mm2 - 1.0


def test_a_wider_wall_setback_shrinks_the_plate():
    r = result()
    small, _, _, _ = S.planned_slabs(r, wall_setback_mm=500.0)
    large, _, _, _ = S.planned_slabs(r, wall_setback_mm=3000.0)
    assert G.area_mm2(large[0].polygon) < G.area_mm2(small[0].polygon)


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


def test_evaluate_reports_every_measurement_position():
    r = result()
    study = S.evaluate(r, divisions=720, layer_mm=1000.0)
    assert len(study.roads) == 1
    assert len(study.roads[0].points) == len(S.measurement_positions(r, 0))
    for point in study.roads[0].points:
        assert 0.0 < point.compliant < 1.0
        assert 0.0 < point.planned < 1.0
        assert point.margin == pytest.approx(point.planned - point.compliant)


def test_a_slim_building_far_from_the_boundaries_passes():
    """外壁後退を大きく取れば、境界から離れる分だけ天空率は適合建築物を上回る。"""
    body = payload()
    body["program"]["wall_setback"] = 5.0
    study = S.evaluate(solve(VolumeInput.from_dict(body)), divisions=720)
    assert study.passes
    assert (study.worst_margin or 0) > 0


def test_a_box_hard_against_the_road_does_not_pass():
    study = S.evaluate(result(), divisions=720)
    assert not study.passes
    assert study.worth_studying


def test_the_suggested_setback_really_passes():
    """提案された外壁後退で組み直すと、実際にすべての算定位置で上回る。"""
    study = S.evaluate(result(), divisions=720)
    assert study.suggestion is not None
    again = S.evaluate(result(), divisions=720,
                       wall_setback_mm=study.suggestion.wall_setback_mm,
                       suggest=False)
    assert again.passes
    assert again.planned_total_gross_area_mm2 == pytest.approx(
        study.suggestion.total_gross_area_mm2)


def test_no_suggestion_is_offered_when_the_case_already_passes():
    body = payload()
    body["program"]["wall_setback"] = 5.0
    study = S.evaluate(solve(VolumeInput.from_dict(body)), divisions=720)
    assert study.suggestion is None


def test_nothing_to_study_when_the_slant_is_not_costing_floor_area():
    """絶対高さ制限で頭打ちなら、道路斜線を外しても増えないので検討不要。"""
    body = payload()
    body["zoning"]["height_limit_absolute"] = 10.0
    study = S.evaluate(solve(VolumeInput.from_dict(body)), divisions=720)
    assert not study.worth_studying
    assert not study.roads
    assert "必要はありません" in study.verdict


def test_two_roads_are_judged_separately():
    body = payload()
    body["site"] = {
        "boundary": [[0, 0], [24, 0], [24, 24], [0, 24]],
        "edges": [
            {"kind": "road", "width": 12.0},
            {"kind": "road", "width": 4.0},
            {"kind": "neighbor"}, {"kind": "neighbor"},
        ],
        "road_side": "south",
    }
    study = S.evaluate(solve(VolumeInput.from_dict(body)), divisions=720,
                       layer_mm=1000.0)
    assert {r.road_width_mm for r in study.roads} == {12000.0, 4000.0}


def test_notes_state_what_is_not_covered():
    study = S.evaluate(result(), divisions=720)
    joined = " ".join(study.notes)
    assert "令135条の5" in joined
    assert "北側斜線" in joined
    assert "確認申請" in joined


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_skyfactor_endpoint():
    res = client.post("/api/skyfactor", json=payload())
    assert res.status_code == 200
    data = res.json()
    assert data["verdict"]
    assert data["worth_studying"] is True
    assert data["roads"] and data["roads"][0]["points"]
    assert data["svg_sky_plan"].startswith("<svg")
    assert data["notes"]


def test_skyfactor_endpoint_on_a_polygon_site():
    data = client.post("/api/skyfactor", json=payload("case_polygon")).json()
    assert data["roads"]
    assert data["planned"]["floor_count"] >= 1


def test_skyfactor_endpoint_rejects_invalid_input():
    body = payload()
    body["zoning"]["bcr"] = 1.5
    assert client.post("/api/skyfactor", json=body).status_code == 422


def test_skyfactor_svg_marks_every_measurement_position():
    data = client.post("/api/skyfactor", json=payload()).json()
    points = sum(len(r["points"]) for r in data["roads"])
    assert data["svg_sky_plan"].count('class="sky-point"') == points


def test_deployment_requirements_include_every_runtime_dependency():
    """requirements.txt に実行時の依存が漏れていないこと。"""
    text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(
        encoding="utf-8")
    listed = {line.split("==")[0].split("[")[0].strip().lower()
              for line in text.splitlines()
              if line.strip() and not line.lstrip().startswith("#")}
    assert {"ezdxf", "shapely", "numpy", "fastapi", "uvicorn", "httpx"} <= listed


def test_the_verdict_does_not_depend_on_the_layer_thickness():
    """適合建築物の刻み方を変えても余裕は変わらない。

    道路斜線の斜面は反対側の境界線からの距離に比例するので、その境界線上に
    ある算定位置から見ると斜面上のどの点も仰角が等しい。層に刻んでも仰角の
    最大値は変わらない。
    """
    for name in ("case_road12", "case_polygon"):
        r = result(name)
        margins = [S.evaluate(r, layer_mm=layer, suggest=False).worst_margin
                   for layer in (250.0, 1000.0, 3000.0)]
        assert margins[0] == pytest.approx(margins[1], abs=1e-6), name
        assert margins[0] == pytest.approx(margins[2], abs=1e-6), name
