"""回帰テスト（M00）。tests/regression/<ケース名>/ に保存した結果と現在の出力を比べる。

正しさの保証ではなく「変わっていないこと」の保証。1 件でもずれたら、
直す前に報告する（CLAUDE.md「既存の結果を黙って変えない」）。
ケースの一覧と、どのケースがどの性質を含むかは README.md を参照。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import regress  # noqa: E402

CASES = regress.case_dirs()


def _ids(dirs: list[Path]) -> list[str]:
    return [d.name for d in dirs]


@pytest.fixture(scope="module")
def outputs(tmp_path_factory) -> dict[str, tuple[dict, dict]]:
    """全ケースを 1 回ずつ実行して (result, dxf_summary) を返す。"""
    out_dir = tmp_path_factory.mktemp("dxf")
    return {
        case.name: regress.run_case(regress.load_json(case / "input.json"),
                                    out_dir / f"{case.name}.dxf")
        for case in CASES
    }


def test_every_case_has_recorded_results():
    missing = [c.name for c in CASES
               if not (c / "result.json").exists() or not (c / "dxf_summary.json").exists()]
    assert not missing, f"結果が未保存のケース: {missing}（record.py で保存する）"


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_result_matches_recorded(case: Path, outputs):
    expected = regress.load_json(case / "result.json")
    actual, _ = outputs[case.name]
    diffs = regress.compare(expected, actual)
    assert not diffs, f"{case.name}: 算定結果が保存値とずれています\n" + "\n".join(diffs[:40])


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_dxf_summary_matches_recorded(case: Path, outputs):
    expected = regress.load_json(case / "dxf_summary.json")
    _, actual = outputs[case.name]
    diffs = regress.compare(expected, actual)
    assert not diffs, f"{case.name}: DXF の内容が保存値とずれています\n" + "\n".join(diffs[:40])


def test_case_inputs_are_well_formed():
    for case in CASES:
        spec = regress.load_json(case / "input.json")
        assert "volume" in spec, f"{case.name}: input.json に volume がない"
        for key in ("site", "zoning", "program"):
            assert key in spec["volume"], f"{case.name}: volume.{key} がない"
