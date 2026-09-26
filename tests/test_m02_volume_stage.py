"""M02 の受け入れ条件。

ボリュームの算定を「案を受け取り StageResult を返す段」として包み、API から呼べるようにした。
算定の中身は変えていないことを、M00 で固定した回帰結果との一致で確かめる。
"""

from __future__ import annotations

import ast
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "regression"))

import regress  # noqa: E402
from api import routes, stages  # noqa: E402
from api.app import app  # noqa: E402
from model.legacy import from_legacy_input  # noqa: E402
from solver.volume import stage as volume_stage  # noqa: E402
from solver.volume.rules import NOTE_MAPS, RULES_BY_LABEL, note_map  # noqa: E402
from store import SchemeStore  # noqa: E402

CASES = regress.case_dirs()
STUDY_CASES = [c for c in CASES if regress.load_json(c / "input.json").get("study") is not None]


def _id(case: Path) -> str:
    return case.name


# ---------------------------------------------------------------------------
# 段の結果 → 既存の結果の形（比較用）
# ---------------------------------------------------------------------------


def to_legacy_volume(data: dict) -> dict:
    """VolumeStageData（JSON）を、M00 の result.json の "volume" と同じ形に戻す。"""
    floors = []
    for f in data["floors"]:
        g = {k: v for k, v in f.items() if k != "level"}
        g["level_mm"] = f["level"]["fl_mm"]
        g["story_height_mm"] = f["level"]["floor_height_mm"]
        floors.append(g)
    return {
        **data["law"],
        **data["placement"],
        "floors": floors,
        **data["totals"],
        "constraint_gains_mm2": [[r["constraint"], r["area_gain_mm2"]]
                                 for r in data["rule_reductions"]],
        "dominant_constraint": data["dominant_constraint"],
        "stop_reason": data["stop"]["reason"],
        "stop_detail": data["stop"]["detail"],
        "applied_rules": data["legacy_applied_rules"],
        "notes": data["legacy_notes"],
    }


def _jsonable(value):
    return json.loads(json.dumps(value))


@pytest.fixture
def store(tmp_path):
    with SchemeStore(tmp_path / "schemes.sqlite3") as s:
        yield s


def _root_scheme(store: SchemeStore, case: Path):
    spec = regress.load_json(case / "input.json")
    scheme = from_legacy_input(spec["volume"], created_by="ui")
    store.save_scheme(scheme)
    return scheme, spec


# ---------------------------------------------------------------------------
# 受け入れ条件 1：回帰結果との一致
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_stage_reproduces_the_m00_results(case, store):
    scheme, spec = _root_scheme(store, case)
    expected = regress.load_json(case / "result.json")
    result = stages.run_stage(store, scheme.id, "volume")
    assert result.status == "ok"

    diffs = regress.compare(expected["volume"], _jsonable(to_legacy_volume(result.data)))
    assert not diffs, "\n".join(diffs[:20])

    if "sky" in expected:
        diffs = regress.compare(expected["sky"], _jsonable(result.data["sky_factor"]))
        assert not diffs, "\n".join(diffs[:20])


# ---------------------------------------------------------------------------
# 受け入れ条件 2：適用規定・未考慮事項の対応（過不足なし）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_applied_rules_correspond_one_to_one(case, store):
    scheme, _ = _root_scheme(store, case)
    expected = regress.load_json(case / "result.json")["volume"]["applied_rules"]
    result = stages.run_stage(store, scheme.id, "volume")
    assert len(result.applied_rules) == len(expected)
    for legacy, rule in zip(expected, result.applied_rules):
        assert rule.rule_id == RULES_BY_LABEL[legacy["label"]].rule_id
        assert rule.article == legacy["basis"]
        assert rule.summary == f"{legacy['label']}：{legacy['value']}"


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_every_note_lands_in_exactly_one_place(case, store):
    scheme, _ = _root_scheme(store, case)
    notes = regress.load_json(case / "result.json")["volume"]["notes"]
    result = stages.run_stage(store, scheme.id, "volume")
    placed = [n.note for n in result.not_considered] + list(result.warnings)
    assert sorted(placed) == sorted(notes)


# 注記の分類の対応表。direction は docs/project_brief.md 5章に書いてあるものだけ。
NOTE_TABLE = [
    ("容積率対象床面積は床面積と同一として算定（法52条3項〜6項の不算入は未考慮）",
     "not_considered", "smaller_than_actual"),
    ("法56条4項の後退距離による道路斜線の緩和は未考慮（安全側）",
     "not_considered", "smaller_than_actual"),
    ("法52条9項の特定道路による容積率緩和は未考慮（安全側）",
     "not_considered", "smaller_than_actual"),
    ("法53条3項の角地緩和・防火地域内耐火建築物の緩和は未適用（該当する場合は入力で指定すると反映されます）",
     "not_considered", "unknown"),
    ("【要注意】日影規制（法56条の2・別表第四）の指定ありとして北側斜線を外しているが、"
     "日影規制そのものは本ツールでは未検証。この結果は日影規制で削られる前の形であり、安全側ではない",
     "not_considered", "larger_than_actual"),
    ("用途地域の指定のない区域は法の原則値（道路斜線1.5・容積率係数6/10）で試算。"
     "特定行政庁が別の値を指定している場合は再計算が必要", "not_considered", "unknown"),
    ("外壁後退と斜線による後退は、足し合わせず大きいほうを適用（どちらも境界線からの離れを定めるものであるため）",
     "warning", "unknown"),
    ("第一種低層住居専用地域 の絶対高さ制限は都市計画で10.0m または 12.0mのいずれかに定められる。"
     "入力がないため既定の10m で試算", "warning", "unknown"),
    ("建蔽率上限に収めるため全階を一律 0.764m 内側に絞り込み", "warning", "unknown"),
    ("北側斜線は適用なしとして算定（日影規制の指定は入力で切り替えられます）", "warning", "unknown"),
]


@pytest.mark.parametrize("note,kind,direction", NOTE_TABLE)
def test_note_classification_table(note, kind, direction):
    m = note_map(note)
    assert m.kind == kind
    assert m.direction == direction


def test_the_table_covers_every_mapping():
    assert len(NOTE_TABLE) == len(NOTE_MAPS)


def test_an_unknown_note_stops_the_stage():
    with pytest.raises(ValueError, match="注記の対応"):
        note_map("まったく新しい注記")


# ---------------------------------------------------------------------------
# 受け入れ条件 3〜5：キャッシュ
# ---------------------------------------------------------------------------


def _count_solve(monkeypatch) -> list[int]:
    calls = [0]
    real = volume_stage.solve

    def counting(inp):
        calls[0] += 1
        return real(inp)

    monkeypatch.setattr(volume_stage, "solve", counting)
    return calls


def test_second_run_uses_the_cache(store, monkeypatch):
    scheme, _ = _root_scheme(store, CASES[0])
    calls = _count_solve(monkeypatch)
    first = stages.run_stage(store, scheme.id, "volume")
    second = stages.run_stage(store, scheme.id, "volume")
    assert calls[0] == 1
    assert first == second


def test_changing_a_plan_condition_recalculates(store, monkeypatch):
    scheme, _ = _root_scheme(store, CASES[0])
    calls = _count_solve(monkeypatch)
    stages.run_stage(store, scheme.id, "volume")
    child = store.derive(scheme.id, [{"path": "plan_conditions.wall_setback_mm", "value": 1500.0}], "ui")
    result = stages.run_stage(store, child.id, "volume")
    assert calls[0] == 2
    assert result.input_hash != volume_stage.stage_input_hash(scheme)


def test_changing_the_solver_version_recalculates(store, monkeypatch):
    scheme, _ = _root_scheme(store, CASES[0])
    calls = _count_solve(monkeypatch)
    stages.run_stage(store, scheme.id, "volume", version="0.1.0+law.aaaa")
    stages.run_stage(store, scheme.id, "volume", version="0.1.0+law.aaaa")
    stages.run_stage(store, scheme.id, "volume", version="0.1.0+law.bbbb")
    assert calls[0] == 2


def test_solver_version_follows_the_law_constants(monkeypatch, tmp_path):
    fake = tmp_path / "constants.py"
    fake.write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(stages, "LAW_FILES", (fake,))
    v1 = stages.solver_version()
    fake.write_text("X = 2\n", encoding="utf-8")
    assert stages.solver_version() != v1


# ---------------------------------------------------------------------------
# 受け入れ条件 6：複数案
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", STUDY_CASES, ids=_id)
def test_variants_match_the_m00_study(case, store):
    scheme, spec = _root_scheme(store, case)
    kwargs = regress._study_kwargs(spec["study"])
    expected = regress.load_json(case / "result.json")["study"]["cases"]

    pairs = stages.generate_variants(store, scheme.id, **kwargs)
    assert len(pairs) == len(expected)
    for rank, ((child, result), want) in enumerate(zip(pairs, expected), start=1):
        assert child.created_by == "batch"
        assert child.parent_id == scheme.id
        assert child.label.startswith(f"{rank:02d} ")
        assert ("最良案" in child.label) == want["added_for_sky"]
        pc = child.plan_conditions
        assert pc.building_angle_deg.value == pytest.approx(want["building_angle_deg"])
        assert pc.floor_height_mm.value / 1000 == pytest.approx(want["floor_height_m"])
        assert pc.wall_setback_mm.value / 1000 == pytest.approx(want["wall_setback_m"])
        assert pc.fireproof.value == want["fireproof"]
        got = result.data["totals"]["total_gross_area_mm2"] / 1e6
        assert math.isclose(got, want["total_gross_area_m2"], rel_tol=1e-9, abs_tol=1e-9), rank
    assert [c.id for c, _ in pairs] == [c.id for c in store.list_children(scheme.id)]


# ---------------------------------------------------------------------------
# 受け入れ条件 7・8：API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "api.sqlite3"

    def _store():
        with SchemeStore(db) as s:
            yield s

    app.dependency_overrides[routes.get_store] = _store
    yield TestClient(app)
    app.dependency_overrides.clear()


def _scheme_body(case: Path) -> dict:
    spec = regress.load_json(case / "input.json")
    s = from_legacy_input(spec["volume"], created_by="ui")
    return {"site_facts": s.site_facts.model_dump(mode="json"),
            "plan_conditions": s.plan_conditions.model_dump(mode="json"),
            "label": case.name}


def test_api_create_run_derive_run(client):
    case = next(c for c in CASES if c.name == "rect_road12_commercial")
    created = client.post("/api/schemes", json=_scheme_body(case))
    assert created.status_code == 200
    root = created.json()
    assert root["created_by"] == "ui"

    run = client.post(f"/api/schemes/{root['id']}/stages/volume")
    assert run.status_code == 200
    want = regress.load_json(case / "result.json")["volume"]["total_gross_area_mm2"]
    assert run.json()["data"]["totals"]["total_gross_area_mm2"] == pytest.approx(want, rel=1e-9)

    child = client.post(f"/api/schemes/{root['id']}/derive", json={
        "patch": [{"path": "plan_conditions.wall_setback_mm", "value": 2000.0}],
        "created_by": "chat", "label": "外壁後退2m"})
    assert child.status_code == 200
    child_id = child.json()["id"]
    assert child.json()["parent_id"] == root["id"]
    assert child.json()["site_id"] == root["site_id"]

    run2 = client.post(f"/api/schemes/{child_id}/stages/volume")
    assert run2.status_code == 200
    assert run2.json()["input_hash"] != run.json()["input_hash"]

    listed = client.get(f"/api/sites/{root['site_id']}/schemes").json()
    assert [s["id"] for s in listed] == [root["id"], child_id]
    assert client.get(f"/api/schemes/{child_id}").json()["label"] == "外壁後退2m"


def test_api_rejects_site_fact_changes_from_chat(client):
    case = CASES[0]
    root = client.post("/api/schemes", json=_scheme_body(case)).json()
    res = client.post(f"/api/schemes/{root['id']}/derive", json={
        "patch": [{"path": "site_facts.use_district",
                   "value": {"value": "商業地域", "source": "manual", "source_name": "会話"}}],
        "created_by": "chat"})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["rejected_paths"] == ["site_facts.use_district"]
    assert "出典つき" in detail["message"]


def test_api_variants_keep_the_order(client):
    case = next(c for c in STUDY_CASES if c.name == "study_polygon_with_sky")
    spec = regress.load_json(case / "input.json")["study"]
    root = client.post("/api/schemes", json=_scheme_body(case)).json()
    res = client.post(f"/api/schemes/{root['id']}/variants", json=spec)
    assert res.status_code == 200
    expected = regress.load_json(case / "result.json")["study"]["cases"]
    got = [p["result"]["data"]["totals"]["total_gross_area_mm2"] / 1e6 for p in res.json()]
    assert got == pytest.approx([c["total_gross_area_m2"] for c in expected], rel=1e-9)


def test_api_unknown_scheme_is_404(client):
    assert client.get("/api/schemes/nope").status_code == 404
    assert client.post("/api/schemes/nope/stages/volume").status_code == 404


def test_the_public_web_app_does_not_expose_the_scheme_api():
    from web.app import app as web_app

    paths = {getattr(r, "path", "") for r in web_app.routes}
    assert not any(p.startswith("/api/schemes") for p in paths)


def test_the_scheme_api_listens_on_localhost_only():
    from api import app as api_app

    assert api_app.HOST == "127.0.0.1"


# ---------------------------------------------------------------------------
# 受け入れ条件 9：依存の向き
# ---------------------------------------------------------------------------

FORBIDDEN_FROM_SOLVER = {"api", "store", "data", "web", "tools"}


def _imported_top_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_solver_does_not_import_outer_layers():
    files = sorted((ROOT / "solver").rglob("*.py"))
    assert files
    for path in files:
        bad = _imported_top_modules(path) & FORBIDDEN_FROM_SOLVER
        assert not bad, f"{path.relative_to(ROOT)} が {sorted(bad)} を import している"


def test_model_does_not_import_the_solver_or_outer_layers():
    for path in sorted((ROOT / "model").rglob("*.py")):
        bad = _imported_top_modules(path) & (FORBIDDEN_FROM_SOLVER | {"solver"})
        assert not bad, f"{path.relative_to(ROOT)} が {sorted(bad)} を import している"


# ---------------------------------------------------------------------------
# 受け入れ条件 11：既存の算定関数の中身に差分がない
# ---------------------------------------------------------------------------

# (今の場所, M00 時点の場所)。solver.py は solver/__init__.py へ移した（中身は同じ）。
UNCHANGED = [
    ("solver/__init__.py", "solver.py"),
    ("geometry.py", "geometry.py"),
    ("skyfactor.py", "skyfactor.py"),
    ("studies.py", "studies.py"),
    ("models.py", "models.py"),
    ("constants.py", "constants.py"),
]


@pytest.mark.skipif(shutil.which("git") is None, reason="git がない環境")
@pytest.mark.parametrize("now,then", UNCHANGED)
def test_existing_calculation_code_is_unchanged(now, then):
    try:
        old = subprocess.run(["git", "show", f"m00-baseline:{then}"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        pytest.skip("m00-baseline タグが見つからない")
    assert (ROOT / now).read_bytes().replace(b"\r\n", b"\n") == old.replace(b"\r\n", b"\n")
