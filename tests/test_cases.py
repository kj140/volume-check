"""tests/cases/ の手計算ケースを現在の実装と突き合わせる。

各ケースの計算過程と許容差は calc.md、期待値は expected.json（表示単位 m・m²）。
期待値を実装の出力に合わせて書き換えてはいけない（CLAUDE.md 3）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constants as C  # noqa: E402
from models import VolumeInput  # noqa: E402
from solver import solve  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "cases"
CASES = sorted(p.parent for p in CASES_DIR.glob("*/expected.json"))
MM = C.M_TO_MM
M2 = C.M2_TO_MM2


def _load(case: Path):
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    result = solve(VolumeInput.from_json_file(case / "input.json"))
    return expected, result


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_hand_calc_case(case: Path):
    assert (case / "calc.md").exists(), f"{case.name}: 計算過程 calc.md がない"
    e, r = _load(case)
    tol = e["tolerance"]
    exact = tol["exact_rel"]

    assert r.far_effective == pytest.approx(e["far_effective"], rel=exact)
    if e["far_by_road"] is None:
        assert r.far_by_road is None
    else:
        assert r.far_by_road == pytest.approx(e["far_by_road"], rel=exact)
    assert r.max_far_area_mm2 / M2 == pytest.approx(e["max_far_area_m2"], rel=exact)
    assert r.max_building_area_mm2 / M2 == pytest.approx(e["max_building_area_m2"], rel=exact)
    assert r.bcr_inset_mm / MM == pytest.approx(e["bcr_inset_m"], abs=tol["length_abs_m"])
    assert r.floor_count == e["floor_count"]
    assert r.max_height_mm / MM == pytest.approx(e["max_height_m"], rel=exact)
    assert r.stop_reason is not None and r.stop_reason.value == e["stop_reason"]
    assert r.total_far_area_mm2 / M2 == pytest.approx(e["total_far_area_m2"],
                                                       rel=tol["total_area_rel"])
    if e.get("top_floor_governing") is not None:
        assert r.floors[-1].governing == e["top_floor_governing"]

    assert len(r.floors) == len(e["floors"])
    for f, ef in zip(r.floors, e["floors"]):
        tag = f"{case.name} {f.floor}階"
        assert f.floor == ef["floor"], tag
        assert f.top_mm / MM == pytest.approx(ef["top_m"], rel=exact), tag
        assert f.setback_road_mm / MM == pytest.approx(ef["setback_road_m"],
                                                       abs=tol["length_abs_m"]), tag
        assert f.setback_neighbor_mm / MM == pytest.approx(ef["setback_neighbor_m"],
                                                           abs=tol["length_abs_m"]), tag
        assert f.width_mm / MM == pytest.approx(ef["width_m"], abs=tol["length_abs_m"]), tag
        assert f.depth_mm / MM == pytest.approx(ef["depth_m"], abs=tol["length_abs_m"]), tag
        assert f.gross_area_mm2 / M2 == pytest.approx(ef["area_m2"],
                                                       rel=tol["floor_area_rel"]), tag
