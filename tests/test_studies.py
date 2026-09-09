"""複数案の自動生成と比較の検証。"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import studies  # noqa: E402
from models import VolumeInput  # noqa: E402
from solver import solve  # noqa: E402
from web.app import app  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
client = TestClient(app)

SMALL_ANGLES = (-10.0, 0.0, 10.0)
SMALL_HEIGHTS = (3.6, 4.2)
SMALL_SETBACKS = (0.5,)


def payload(name: str = "case_road12") -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def small_study(name: str = "case_road12", **kwargs) -> studies.StudyResult:
    return studies.generate(
        payload(name), angles_deg=SMALL_ANGLES, floor_heights_m=SMALL_HEIGHTS,
        wall_setbacks_m=SMALL_SETBACKS, **kwargs
    )


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def test_generates_every_combination():
    study = small_study()
    assert len(study.cases) == len(SMALL_ANGLES) * len(SMALL_HEIGHTS) * len(SMALL_SETBACKS)
    combos = {(c.building_angle_deg, c.floor_height_m, c.wall_setback_m)
              for c in study.cases}
    assert len(combos) == len(study.cases), "同じ組み合わせが重複している"


def test_cases_are_sorted_by_total_floor_area():
    study = small_study()
    areas = [c.total_gross_area_m2 for c in study.cases]
    assert areas == sorted(areas, reverse=True)
    assert study.best is study.cases[0]


def test_baseline_matches_solving_the_input_as_is():
    body = payload()
    expected = solve(VolumeInput.from_dict(body))
    study = small_study()
    assert study.baseline.total_gross_area_m2 == pytest.approx(
        expected.total_gross_area_mm2 / 1e6, abs=0.01
    )
    assert study.baseline.floor_count == expected.floor_count
    assert study.baseline.building_angle_deg == pytest.approx(0.0, abs=1e-9)


def test_every_case_is_reproducible_by_solving_it_directly():
    """表に出した案は、その条件で solve し直すと同じ結果になる。"""
    study = small_study()
    for case in study.cases[:4]:
        d = copy.deepcopy(payload())
        d["program"].update(
            building_angle=case.building_angle_deg,
            floor_height=case.floor_height_m,
            wall_setback=case.wall_setback_m,
            fireproof=case.fireproof,
        )
        again = solve(VolumeInput.from_dict(d))
        assert again.floor_count == case.floor_count
        assert again.total_gross_area_mm2 / 1e6 == pytest.approx(
            case.total_gross_area_m2, abs=0.01
        )


def test_best_is_at_least_as_good_as_the_baseline():
    study = small_study()
    assert study.best.total_gross_area_m2 >= study.baseline.total_gross_area_m2 - 0.01


def test_fireproof_option_adds_cases_and_can_win():
    """耐火建築物等も振ると、建蔽率が緩和されて有利な案が出る。"""
    plain = small_study()
    with_fire = studies.generate(
        payload(), angles_deg=SMALL_ANGLES, floor_heights_m=SMALL_HEIGHTS,
        wall_setbacks_m=SMALL_SETBACKS, fireproof_options=(False, True),
    )
    assert len(with_fire.cases) == 2 * len(plain.cases)
    # 商業地域・建蔽率80%・防火地域なしなので、耐火にしても緩和はされない
    assert with_fire.best.total_gross_area_m2 >= plain.best.total_gross_area_m2 - 0.01

    body = payload()
    body["zoning"]["fire_zone"] = "防火地域"
    fire_zone = studies.generate(
        body, angles_deg=(0.0,), floor_heights_m=(4.2,), wall_setbacks_m=(0.5,),
        fireproof_options=(False, True),
    )
    assert fire_zone.best.fireproof is True, "防火地域なら耐火にしたほうが有利になるはず"


def test_limit_caps_the_number_of_cases():
    study = small_study(limit=2)
    assert len(study.cases) == 2


def test_too_many_combinations_is_rejected():
    with pytest.raises(ValueError, match="多すぎ"):
        studies.generate(
            payload(),
            angles_deg=tuple(float(a) for a in range(-45, 46)),
            floor_heights_m=(3.6, 3.9, 4.2, 4.5),
            wall_setbacks_m=(0.5, 1.0, 1.5),
        )


def test_notes_explain_the_outcome():
    study = small_study()
    assert study.notes
    assert any("法規条件は変えていません" in n for n in study.notes)


# ---------------------------------------------------------------------------
# 振り角のスイープ
# ---------------------------------------------------------------------------


def test_angle_sweep_covers_every_angle():
    sweep = studies.sweep_angles(payload(), SMALL_ANGLES)
    assert [a for a, _ in sweep] == list(SMALL_ANGLES)
    assert all(area > 0 for _, area in sweep)


def test_parallel_is_best_on_a_rectangle_site():
    """矩形敷地では道路に平行（0度）が最良になる。"""
    assert studies.best_angle_deg(payload(), (-20.0, -10.0, 0.0, 10.0, 20.0)) == 0.0


def test_rotation_can_win_on_a_polygon_site():
    """非矩形の敷地では、振ったほうが取れることがある。"""
    sweep = dict(studies.sweep_angles(payload("case_polygon"),
                                      (-20.0, -10.0, 0.0, 10.0, 20.0)))
    assert sweep[-20.0] > sweep[0.0]


def test_suggested_angles_include_the_site_edges():
    inp = VolumeInput.from_json_file(SAMPLES / "case_polygon.json")
    angles = studies.suggested_angles(inp.site.shape)
    assert 0.0 in angles, "道路に平行が候補に入っていない"
    assert len(angles) > len(studies.suggested_angles.__defaults__[0]), \
        "敷地の辺の向きが候補に足されていない"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_studies_endpoint():
    res = client.post("/api/studies", json={
        "base": payload(), "angles_deg": list(SMALL_ANGLES),
        "floor_heights_m": list(SMALL_HEIGHTS), "wall_setbacks_m": list(SMALL_SETBACKS),
        "limit": 3,
    })
    assert res.status_code == 200
    data = res.json()
    assert len(data["cases"]) == 3
    assert data["baseline"] and data["best"]
    assert data["gain_over_baseline_m2"] >= 0
    assert len(data["angle_sweep"]) == len(SMALL_ANGLES)
    assert data["notes"]


def test_studies_endpoint_rejects_too_many_combinations():
    res = client.post("/api/studies", json={
        "base": payload(),
        "angles_deg": [float(a) for a in range(-45, 46)],
        "floor_heights_m": [3.6, 3.9, 4.2, 4.5],
        "wall_setbacks_m": [0.5, 1.0, 1.5],
    })
    assert res.status_code == 422


def test_studies_endpoint_rejects_invalid_base():
    body = payload()
    body["zoning"]["bcr"] = 1.5
    assert client.post("/api/studies", json={"base": body}).status_code == 422


def test_solve_accepts_a_polygon_site():
    res = client.post("/api/solve", json=payload("case_polygon"))
    assert res.status_code == 200
    data = res.json()
    assert data["summary"]["site_is_rectangle"] is False
    assert data["summary"]["floor_count"] >= 1
    assert data["svg_site_plan"] and data["svg_floor_plans"]


def test_solve_accepts_a_building_angle():
    body = payload()
    body["program"]["building_angle"] = 20.0
    res = client.post("/api/solve", json=body)
    assert res.status_code == 200
    assert res.json()["summary"]["building_angle_deg"] == pytest.approx(20.0)
