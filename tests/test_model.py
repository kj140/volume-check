"""M01 受け入れ条件：型・ハッシュ・差分・既存入力との変換（model/）。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "regression"))

import regress  # noqa: E402
from model import (  # noqa: E402
    ALLOWED_PLAN_PATHS,
    ALLOWED_SITE_PATHS,
    FactValue,
    GridAxis,
    Level,
    PatchError,
    PlanConditions,
    PlanValue,
    Scheme,
    SiteFactChangeRejected,
    SiteFacts,
    StageResult,
    apply_patch,
    canonical_json,
    input_hash,
)
from model.legacy import from_legacy_input, to_legacy_input  # noqa: E402
from models import VolumeInput  # noqa: E402
from solver import solve  # noqa: E402

REGRESSION_INPUTS = {
    case.name: regress.load_json(case / "input.json")["volume"]
    for case in regress.case_dirs()
}


def scheme_from(name: str = "rect_road12_commercial", **kw) -> Scheme:
    return from_legacy_input(REGRESSION_INPUTS[name], created_by=kw.pop("created_by", "ui"), **kw)


# ---------------------------------------------------------------------------
# 既存入力との変換
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(REGRESSION_INPUTS))
def test_legacy_round_trip_keeps_every_regression_input(name):
    """to_legacy_input(from_legacy_input(x)) が元の入力と一致する。

    一致の基準：算定側の入口（VolumeInput.from_dict）を通した結果が等しく、
    solve() の結果も等しい。省略されたキーと既定値と同じ明示値は区別しない。
    """
    original = REGRESSION_INPUTS[name]
    scheme = from_legacy_input(original, created_by="ui")
    back = to_legacy_input(scheme)

    assert VolumeInput.from_dict(back) == VolumeInput.from_dict(original)
    assert regress.compare(regress.volume_to_dict(solve(VolumeInput.from_dict(original))),
                           regress.volume_to_dict(solve(VolumeInput.from_dict(back)))) == []
    # もう一度往復しても変わらない（キーの有無まで安定する）
    assert to_legacy_input(from_legacy_input(back, created_by="ui")) == back


def test_legacy_rectangle_input_round_trips_as_rectangle():
    back = to_legacy_input(scheme_from("rect_road12_commercial"))
    assert back["site"] == {"frontage": 20.0, "depth": 30.0, "road_width": 12.0,
                            "road_side": "south"}


def test_legacy_polygon_input_round_trips_as_polygon():
    back = to_legacy_input(scheme_from("polygon_road8_commercial"))
    assert back["site"]["boundary"] == [[0, 0], [26, 0], [30, 16], [12, 24], [0, 14]]
    assert back["site"]["edges"][0] == {"kind": "road", "width": 8.0}
    assert back["site"]["edges"][1] == {"kind": "neighbor"}


def test_legacy_omitted_facts_become_unavailable_and_explicit_ones_manual():
    s = scheme_from("rect_road12_commercial")
    f = s.site_facts
    assert f.use_district.value == "商業地域" and f.use_district.source == "manual"
    assert f.coverage_ratio.value == 0.8 and f.far_ratio.value == 6.0
    # samples は height_limit_absolute: null、fire_zone は省略
    assert not f.absolute_height_limit_mm.available
    assert not f.fire_zone.available
    assert not f.corner_lot.available
    assert not f.true_north_deg.available and f.road_side.value == "south"
    assert not f.height_district.available and not f.district_plan.available
    assert f.boundary.value == [(0.0, 0.0), (20000.0, 0.0), (20000.0, 30000.0), (0.0, 30000.0)]
    assert f.edges.value[0].kind == "road" and f.edges.value[0].road_width_mm == 12000.0

    s2 = scheme_from("fire_zone_fireproof_on")
    assert s2.site_facts.fire_zone.value == "防火地域"
    assert s2.site_facts.fire_zone.source == "manual"
    assert s2.plan_conditions.fireproof.value is True
    assert s2.plan_conditions.fireproof.origin == "designer"


def test_legacy_plan_conditions_are_designer_values_in_mm():
    p = scheme_from().plan_conditions
    assert p.floor_height_mm == PlanValue(value=4200.0, origin="designer")
    assert p.gf_height_mm.value == 4500.0 and p.wall_setback_mm.value == 500.0
    assert p.core_ratio.value == 0.18 and p.max_floors.value == 30
    assert p.building_angle_deg.value is None and p.building_angle_deg.origin == "default"


def test_default_plan_conditions_match_the_screen_defaults():
    """既定値は画面（index.html）と /healthz が使っている値（docs/decisions/0003）。"""
    p = PlanConditions()
    assert (p.floor_height_mm.value, p.gf_height_mm.value, p.wall_setback_mm.value,
            p.core_ratio.value, p.max_floors.value, p.fireproof.value,
            p.building_angle_deg.value) == (4200.0, 4500.0, 500.0, 0.18, 30, False, None)
    assert all(getattr(p, n).origin == "default" for n in PlanConditions.model_fields)
    assert p.defaults_used() == [f"plan_conditions.{n}" for n in PlanConditions.model_fields]


# ---------------------------------------------------------------------------
# ハッシュ
# ---------------------------------------------------------------------------


def test_same_content_with_different_key_order_hashes_the_same():
    original = REGRESSION_INPUTS["polygon_road8_commercial"]
    shuffled = json.loads(json.dumps(original))
    shuffled = {k: shuffled[k] for k in reversed(list(shuffled))}
    shuffled["site"] = {k: shuffled["site"][k] for k in reversed(list(shuffled["site"]))}
    shuffled["zoning"] = {k: shuffled["zoning"][k] for k in reversed(list(shuffled["zoning"]))}
    a = from_legacy_input(original, created_by="ui")
    b = from_legacy_input(shuffled, created_by="ui")
    assert a.input_hash == b.input_hash


def test_canonical_json_sorts_keys_and_rounds_floats_to_3_places():
    assert canonical_json({"b": 1.23456, "a": [1, {"z": -0.0, "y": 2.0}]}) == \
        '{"a":[1,{"y":2.0,"z":0.0}],"b":1.235}'


def test_hash_ignores_provenance_but_not_values():
    base = scheme_from()
    facts = base.site_facts.model_copy(update={
        "use_district": FactValue(value="商業地域", source="api",
                                  source_name="不動産情報ライブラリ API",
                                  retrieved_at=datetime(2026, 9, 17, tzinfo=timezone.utc)),
    })
    assert input_hash(facts, base.plan_conditions) == base.input_hash
    plan = base.plan_conditions.model_copy(update={
        "wall_setback_mm": PlanValue(value=500.0, origin="default")})
    assert input_hash(base.site_facts, plan) == base.input_hash


_SITE_CHANGES = {
    "boundary": [(0.0, 0.0), (21000.0, 0.0), (21000.0, 30000.0), (0.0, 30000.0)],
    "edges": None,   # 後で作る
    "road_side": "north",
    "true_north_deg": 45.0,
    "corner_lot": True,
    "use_district": "近隣商業地域",
    "coverage_ratio": 0.6,
    "far_ratio": 4.0,
    "fire_zone": "準防火地域",
    "absolute_height_limit_mm": 31000.0,
    "shadow_regulation": True,
    "height_district": "第二種高度地区",
    "district_plan": "○○地区計画",
}
_PLAN_CHANGES = {
    "floor_height_mm": 3900.0, "gf_height_mm": 5000.0, "wall_setback_mm": 1000.0,
    "core_ratio": 0.2, "max_floors": 12, "fireproof": True, "building_angle_deg": 10.0,
}


def test_every_site_fact_field_is_covered_by_the_hash_test():
    assert set(_SITE_CHANGES) == set(SiteFacts.model_fields) - {"shape_form"}
    assert set(_PLAN_CHANGES) == set(PlanConditions.model_fields)


@pytest.mark.parametrize("field", sorted(_SITE_CHANGES))
def test_changing_any_site_fact_changes_the_hash(field):
    base = scheme_from()
    value = _SITE_CHANGES[field]
    if field == "edges":
        edges = [e.model_copy() for e in base.site_facts.edges.value]
        edges[0] = edges[0].model_copy(update={"road_width_mm": 13000.0})
        value = edges
    facts = base.site_facts.model_copy(update={
        field: FactValue(value=value, source="manual", source_name="テスト")})
    assert input_hash(facts, base.plan_conditions) != base.input_hash


def test_changing_the_shape_form_changes_the_hash():
    base = scheme_from()
    facts = base.site_facts.model_copy(update={"shape_form": "polygon"})
    assert input_hash(facts, base.plan_conditions) != base.input_hash


@pytest.mark.parametrize("field", sorted(_PLAN_CHANGES))
def test_changing_any_plan_condition_changes_the_hash(field):
    base = scheme_from()
    plan = base.plan_conditions.model_copy(update={
        field: PlanValue(value=_PLAN_CHANGES[field], origin="designer")})
    assert input_hash(base.site_facts, plan) != base.input_hash


def test_making_a_fact_unavailable_changes_the_hash():
    base = scheme_from()
    facts = base.site_facts.model_copy(update={"use_district": FactValue()})
    assert input_hash(facts, base.plan_conditions) != base.input_hash


# ---------------------------------------------------------------------------
# 型の約束
# ---------------------------------------------------------------------------


def test_unavailable_fact_cannot_carry_a_value():
    with pytest.raises(ValidationError):
        FactValue(value=1.0, source="unavailable")
    with pytest.raises(ValidationError):
        FactValue(value=None, source="manual")


def test_scheme_rejects_a_hash_that_does_not_match_its_content():
    s = scheme_from()
    with pytest.raises(ValidationError, match="input_hash"):
        Scheme.model_validate({**s.model_dump(mode="json"), "input_hash": "0" * 64})


def test_a_root_scheme_uses_its_own_id_as_site_id():
    s = scheme_from()
    assert s.site_id == s.id and s.parent_id is None


def test_level_and_grid_axis_survive_json():
    level = Level(name="1F", fl_mm=0.0, floor_height_mm=4500.0,
                  ceiling_height_mm=PlanValue(value=2800.0, origin="designer"))
    axis = GridAxis(name="X1", direction="X", offset_mm=0.0)
    assert Level.model_validate_json(level.model_dump_json()) == level
    assert GridAxis.model_validate_json(axis.model_dump_json()) == axis
    empty = Level(name="RF", fl_mm=38100.0, floor_height_mm=0.0)
    assert Level.model_validate_json(empty.model_dump_json()) == empty


def test_stage_result_keeps_empty_lists_instead_of_omitting_them():
    r = StageResult(scheme_id="s", stage="volume", input_hash="h", solver_version="v",
                    status="ok")
    d = r.model_dump(mode="json")
    assert d["applied_rules"] == [] and d["not_considered"] == [] and d["defaults_used"] == []


# ---------------------------------------------------------------------------
# 差分
# ---------------------------------------------------------------------------


def test_chat_can_change_plan_conditions():
    base = scheme_from()
    facts, plan = apply_patch(base, [{"path": "plan_conditions.wall_setback_mm", "value": 1000.0}],
                              created_by="chat")
    assert plan.wall_setback_mm == PlanValue(value=1000.0, origin="designer")
    assert facts == base.site_facts
    assert base.plan_conditions.wall_setback_mm.value == 500.0   # 親は変わらない


def test_chat_cannot_touch_site_facts():
    base = scheme_from()
    patch = [{"path": "plan_conditions.wall_setback_mm", "value": 1000.0},
             {"path": "site_facts.use_district",
              "value": {"value": "近隣商業地域", "source": "manual", "source_name": "会話"}}]
    with pytest.raises(SiteFactChangeRejected) as info:
        apply_patch(base, patch, created_by="chat")
    assert info.value.paths == ["site_facts.use_district"]
    assert "敷地の事実は画面から出典つきで入力してください" in str(info.value)


def test_batch_cannot_touch_site_facts_either():
    with pytest.raises(SiteFactChangeRejected):
        apply_patch(scheme_from(), [{"path": "site_facts.fire_zone",
                                     "value": {"value": "防火地域", "source": "manual",
                                               "source_name": "x"}}], created_by="batch")


def test_ui_can_change_site_facts_with_a_source():
    base = scheme_from()
    facts, _ = apply_patch(base, [{"path": "site_facts.fire_zone",
                                   "value": {"value": "防火地域", "source": "api",
                                             "source_name": "不動産情報ライブラリ API"}}],
                           created_by="ui")
    assert facts.fire_zone.value == "防火地域" and facts.fire_zone.source == "api"
    assert facts.fire_zone.source_name == "不動産情報ライブラリ API"


def test_ui_site_fact_change_without_a_source_is_rejected():
    base = scheme_from()
    with pytest.raises(PatchError, match="source"):
        apply_patch(base, [{"path": "site_facts.fire_zone", "value": {"value": "防火地域"}}],
                    created_by="ui")
    with pytest.raises(PatchError, match="source_name"):
        apply_patch(base, [{"path": "site_facts.fire_zone",
                            "value": {"value": "防火地域", "source": "manual"}}],
                    created_by="ui")
    with pytest.raises(PatchError):
        apply_patch(base, [{"path": "site_facts.fire_zone", "value": "防火地域"}],
                    created_by="ui")


def test_ui_can_mark_a_fact_unavailable():
    facts, _ = apply_patch(scheme_from("fire_zone_fireproof_on"),
                           [{"path": "site_facts.fire_zone", "value": {"source": "unavailable"}}],
                           created_by="ui")
    assert not facts.fire_zone.available


def test_unknown_paths_and_wrong_types_are_rejected():
    base = scheme_from()
    with pytest.raises(PatchError, match="存在しない"):
        apply_patch(base, [{"path": "plan_conditions.nonexistent", "value": 1}], created_by="ui")
    with pytest.raises(PatchError, match="存在しない"):
        apply_patch(base, [{"path": "id", "value": "x"}], created_by="ui")
    with pytest.raises(PatchError, match="型"):
        apply_patch(base, [{"path": "plan_conditions.max_floors", "value": "十"}], created_by="ui")
    with pytest.raises(PatchError, match="型"):
        apply_patch(base, [{"path": "site_facts.coverage_ratio",
                            "value": {"value": "80%", "source": "manual", "source_name": "x"}}],
                    created_by="ui")
    with pytest.raises(PatchError):
        apply_patch(base, [{"path": "plan_conditions.max_floors"}], created_by="ui")


def test_allowed_paths_cover_every_field_once():
    assert ALLOWED_PLAN_PATHS == tuple(f"plan_conditions.{n}" for n in PlanConditions.model_fields)
    assert ALLOWED_SITE_PATHS == tuple(f"site_facts.{n}" for n in SiteFacts.model_fields
                                       if n != "shape_form")
