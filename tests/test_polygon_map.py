"""地図上でなぞった敷地（緯度経度の多角形）をローカル座標に写す部分の検証。"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.app import app  # noqa: E402
from web.geo import (  # noqa: E402
    local_xy_m,
    meridian_distance_m,
    parallel_distance_m,
    polygon_area_m2,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
client = TestClient(app)

LAT0, LON0 = 35.95, 139.70          # さいたま市岩槻あたり


def latlon_of(x_m: float, y_m: float, angle_deg: float = 0.0) -> dict:
    """ローカル座標[m]（東 x・北 y）を angle_deg だけ回してから緯度経度にする。"""
    a = math.radians(angle_deg)
    xr, yr = x_m * math.cos(a) - y_m * math.sin(a), x_m * math.sin(a) + y_m * math.cos(a)
    dlat = yr / meridian_distance_m(LAT0, LAT0 + 1.0)
    dlon = xr / parallel_distance_m(LAT0, LON0, LON0 + 1.0)
    return {"lat": LAT0 + dlat, "lon": LON0 + dlon}


def rectangle(w: float, d: float, angle_deg: float = 0.0) -> list[dict]:
    return [latlon_of(0, 0, angle_deg), latlon_of(w, 0, angle_deg),
            latlon_of(w, d, angle_deg), latlon_of(0, d, angle_deg)]


# ---------------------------------------------------------------------------
# 座標変換
# ---------------------------------------------------------------------------


def test_local_coordinates_are_east_x_north_y_in_metres():
    pts = [(LAT0, LON0), (LAT0, LON0 + 0.001), (LAT0 + 0.001, LON0)]
    xy = local_xy_m(pts)
    # 東へ動いた点は x が増え、北へ動いた点は y が増える
    assert xy[1][0] > xy[0][0] and abs(xy[1][1] - xy[0][1]) < 1e-6
    assert xy[2][1] > xy[0][1] and abs(xy[2][0] - xy[0][0]) < 1e-6
    # 0.001度は北緯36度付近で 東西およそ 90m、南北およそ 111m。
    # 東西の縮尺は重心の緯度で固定しているので、基準緯度との差は 1mm 未満に収まる
    assert xy[1][0] - xy[0][0] == pytest.approx(
        parallel_distance_m(LAT0, LON0, LON0 + 0.001), abs=1e-3)
    assert xy[2][1] - xy[0][1] == pytest.approx(
        meridian_distance_m(LAT0, LAT0 + 0.001), abs=1e-3)


def test_the_origin_is_the_centroid():
    xy = local_xy_m([(q["lat"], q["lon"]) for q in rectangle(20, 30)])
    assert sum(x for x, _ in xy) == pytest.approx(0.0, abs=1e-6)
    assert sum(y for _, y in xy) == pytest.approx(0.0, abs=1e-6)


def test_a_rectangle_round_trips_its_dimensions():
    xy = local_xy_m([(q["lat"], q["lon"]) for q in rectangle(20, 30)])
    lengths = [math.dist(xy[i], xy[(i + 1) % 4]) for i in range(4)]
    assert lengths == pytest.approx([20.0, 30.0, 20.0, 30.0], abs=0.01)
    assert polygon_area_m2(xy) == pytest.approx(600.0, abs=0.5)


def test_a_rotated_rectangle_keeps_its_dimensions():
    """地図の南北に揃っていない敷地でも、辺の長さと面積は変わらない。"""
    xy = local_xy_m([(q["lat"], q["lon"]) for q in rectangle(20, 30, angle_deg=37.0)])
    lengths = [math.dist(xy[i], xy[(i + 1) % 4]) for i in range(4)]
    assert lengths == pytest.approx([20.0, 30.0, 20.0, 30.0], abs=0.01)
    assert polygon_area_m2(xy) == pytest.approx(600.0, abs=0.5)
    # 辺1 の向きは 37 度
    angle = math.degrees(math.atan2(xy[1][1] - xy[0][1], xy[1][0] - xy[0][0]))
    assert angle == pytest.approx(37.0, abs=0.05)


def test_area_does_not_depend_on_winding():
    xy = local_xy_m([(q["lat"], q["lon"]) for q in rectangle(20, 30)])
    assert polygon_area_m2(list(reversed(xy))) == pytest.approx(polygon_area_m2(xy))


def test_degenerate_inputs():
    assert local_xy_m([]) == []
    assert polygon_area_m2([(0, 0), (1, 1)]) == 0.0


# ---------------------------------------------------------------------------
# /api/polygon
# ---------------------------------------------------------------------------


def test_polygon_endpoint_returns_a_site_solve_can_take():
    res = client.post("/api/polygon", json={
        "points": rectangle(20, 30, angle_deg=30.0), "road_edges": [0], "road_width": 6.0,
    })
    assert res.status_code == 200
    d = res.json()
    assert d["area_m2"] == pytest.approx(600.0, abs=0.5)
    assert d["edge_lengths_m"] == pytest.approx([20.0, 30.0, 20.0, 30.0], abs=0.02)
    assert d["road_frontage_m"] == pytest.approx(20.0, abs=0.02)
    assert d["site"]["north_angle"] == 90.0
    assert [e["kind"] for e in d["site"]["edges"]] == ["road", "neighbor", "neighbor", "neighbor"]
    assert d["site"]["edges"][0]["width"] == 6.0

    body = json.loads((SAMPLES / "case_road6.json").read_text(encoding="utf-8"))
    body["site"] = d["site"]
    solved = client.post("/api/solve", json=body)
    assert solved.status_code == 200
    summary = solved.json()["summary"]
    assert summary["site_area_m2"] == pytest.approx(600.0, abs=0.5)
    assert summary["site_is_rectangle"] is False
    # 建物は道路辺に平行に置かれる（振り角 0）
    assert summary["building_angle_deg"] == pytest.approx(0.0, abs=1e-6)


def test_polygon_endpoint_accepts_several_road_edges():
    d = client.post("/api/polygon", json={
        "points": rectangle(20, 30), "road_edges": [0, 2], "road_width": 8.0,
    }).json()
    assert [e["kind"] for e in d["site"]["edges"]] == ["road", "neighbor", "road", "neighbor"]
    assert d["road_frontage_m"] == pytest.approx(40.0, abs=0.02)


def test_polygon_endpoint_rejects_a_self_intersecting_outline():
    pts = rectangle(20, 30)
    bow = [pts[0], pts[2], pts[1], pts[3]]
    res = client.post("/api/polygon", json={"points": bow})
    assert res.status_code == 422
    assert "形状が不正" in res.json()["detail"]


def test_polygon_endpoint_rejects_bad_road_edge_indices():
    res = client.post("/api/polygon", json={"points": rectangle(20, 30), "road_edges": [7]})
    assert res.status_code == 422
    assert "範囲外" in res.json()["detail"]
    res = client.post("/api/polygon", json={"points": rectangle(20, 30), "road_edges": []})
    assert res.status_code == 422


def test_polygon_endpoint_needs_three_points():
    res = client.post("/api/polygon", json={"points": rectangle(20, 30)[:2]})
    assert res.status_code == 422


def test_the_page_offers_the_polygon_tool():
    html = client.get("/").text
    assert 'id="btn-poly"' in html
    assert 'id="btn-poly-done"' in html
