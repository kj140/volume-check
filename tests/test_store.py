"""M01 受け入れ条件：案と結果の保存（store/、SQLite）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "regression"))

import regress  # noqa: E402
from model import (  # noqa: E402
    AppliedRule,
    NotConsidered,
    PlanValue,
    SiteFactChangeRejected,
    StageResult,
)
from model.legacy import from_legacy_input  # noqa: E402
from store import SchemeStore  # noqa: E402

LEGACY = regress.load_json(regress.CASES_DIR / "rect_road12_commercial" / "input.json")["volume"]
POLYGON = regress.load_json(regress.CASES_DIR / "polygon_road8_commercial" / "input.json")["volume"]


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "schemes.sqlite3"


@pytest.fixture
def store(db_path) -> SchemeStore:
    with SchemeStore(db_path) as s:
        yield s


def root(store: SchemeStore, legacy=LEGACY, label="基準案"):
    scheme = from_legacy_input(legacy, created_by="ui", label=label)
    store.save_scheme(scheme)
    return scheme


def test_save_and_get_scheme_round_trip(store):
    scheme = root(store)
    assert store.get_scheme(scheme.id) == scheme
    assert store.get_scheme("no-such-id") is None


def test_saving_the_same_scheme_twice_is_harmless_but_a_changed_one_is_refused(store):
    scheme = root(store)
    store.save_scheme(scheme)                                  # 同じ内容なら何も起きない
    other = scheme.model_copy(update={"label": "別名"})
    with pytest.raises(ValueError, match="書き換え"):
        store.save_scheme(other)


def test_derive_makes_a_child_and_leaves_the_parent_untouched(store):
    parent = root(store)
    child = store.derive(parent.id, [{"path": "plan_conditions.wall_setback_mm", "value": 1000.0}],
                         created_by="chat", label="後退1m")
    assert child.parent_id == parent.id
    assert child.site_id == parent.site_id
    assert child.created_by == "chat" and child.label == "後退1m"
    assert child.plan_conditions.wall_setback_mm == PlanValue(value=1000.0, origin="designer")
    assert child.input_hash != parent.input_hash
    assert store.get_scheme(parent.id) == parent                # 親は変わらない
    assert store.get_scheme(child.id) == child
    assert store.list_children(parent.id) == [child]


def test_derive_with_no_effective_change_still_makes_a_new_scheme_with_the_same_hash(store):
    parent = root(store)
    child = store.derive(parent.id, [{"path": "plan_conditions.wall_setback_mm", "value": 500.0}],
                         created_by="chat")
    assert child.id != parent.id and child.input_hash == parent.input_hash


def test_chat_cannot_derive_a_site_fact_change_and_nothing_is_saved(store):
    parent = root(store)
    with pytest.raises(SiteFactChangeRejected):
        store.derive(parent.id, [{"path": "site_facts.use_district",
                                  "value": {"value": "近隣商業地域", "source": "manual",
                                            "source_name": "会話"}}], created_by="chat")
    assert store.list_children(parent.id) == []
    assert store.list_schemes(parent.site_id) == [parent]


def test_ui_derive_of_a_site_fact_keeps_the_site_id(store):
    parent = root(store)
    child = store.derive(parent.id, [{"path": "site_facts.fire_zone",
                                      "value": {"value": "防火地域", "source": "api",
                                                "source_name": "不動産情報ライブラリ API"}}],
                         created_by="ui")
    assert child.site_id == parent.site_id
    assert child.site_facts.fire_zone.source == "api"


def test_derive_from_an_unknown_parent_fails(store):
    with pytest.raises(KeyError):
        store.derive("no-such-id", [], created_by="ui")


def test_list_schemes_groups_by_site_and_orders_by_creation(store):
    a = root(store)
    b = root(store, POLYGON, label="別の敷地")
    child = store.derive(a.id, [{"path": "plan_conditions.max_floors", "value": 10}],
                         created_by="batch")
    assert [s.id for s in store.list_schemes(a.site_id)] == [a.id, child.id]
    assert store.list_schemes(b.site_id) == [b]
    assert store.list_schemes("nowhere") == []


def test_unavailable_facts_stay_unavailable_after_reopening(db_path):
    with SchemeStore(db_path) as store:
        scheme = root(store)
        assert not scheme.site_facts.fire_zone.available
        assert not scheme.site_facts.height_district.available
    with SchemeStore(db_path) as store:
        loaded = store.get_scheme(scheme.id)
        assert loaded == scheme
        assert not loaded.site_facts.fire_zone.available
        assert loaded.site_facts.fire_zone.value is None
        assert not loaded.site_facts.height_district.available


def test_results_survive_reopening_with_parent_child_links(db_path):
    with SchemeStore(db_path) as store:
        parent = root(store)
        child = store.derive(parent.id, [{"path": "plan_conditions.fireproof", "value": True}],
                             created_by="ui")
        result = StageResult(
            scheme_id=child.id, stage="volume", input_hash="abc", solver_version="0.1+law0",
            status="ok", data={"floor_count": 6, "levels": [
                {"name": "1F", "fl_mm": 0.0, "floor_height_mm": 4500.0,
                 "ceiling_height_mm": None, "beam_bottom_mm": None}]},
            applied_rules=[AppliedRule(rule_id="far.road_width", article="法52条2項",
                                       summary="幅員12m以上で低減なし")],
            not_considered=[NotConsidered(item="日影規制", direction="larger_than_actual",
                                          note="未実装")],
            defaults_used=["plan_conditions.building_angle_deg"],
        )
        store.save_result(result)
    with SchemeStore(db_path) as store:
        assert store.get_result(child.id, "volume", "abc", "0.1+law0") == result
        assert store.get_result(child.id, "volume", "abc", "other-version") is None
        assert store.get_result(child.id, "estimate", "abc", "0.1+law0") is None
        assert store.list_children(parent.id)[0].id == child.id
        assert store.get_scheme(child.id).parent_id == parent.id


def test_saving_a_result_for_an_unknown_scheme_fails(store):
    result = StageResult(scheme_id="ghost", stage="volume", input_hash="h",
                         solver_version="v", status="ok")
    with pytest.raises(KeyError):
        store.save_result(result)


def test_saving_the_same_result_key_again_replaces_it(store):
    scheme = root(store)
    first = StageResult(scheme_id=scheme.id, stage="volume", input_hash="h",
                        solver_version="v", status="error", warnings=["1回目"])
    second = first.model_copy(update={"status": "ok", "warnings": []})
    store.save_result(first)
    store.save_result(second)
    assert store.get_result(scheme.id, "volume", "h", "v") == second
