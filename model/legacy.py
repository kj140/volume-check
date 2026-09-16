"""既存の入力 JSON（samples/*.json の形、単位 m）と Scheme の相互変換。

M02 で既存の算定（solver.solve）を段として包むときに使う。

対応の規則（docs/decisions/0003）：
- 既存 JSON で**省略された**敷地事実は source="unavailable"（値を推測しない）。
  to_legacy_input は unavailable の項目を省略し、既存コードの既定（指定なし・角地でない・
  日影規制なし・真北は road_side から）がそのまま効く。
- 既存 JSON で**明示された**敷地事実は source="manual"（地図でなぞったか手入力かは
  JSON からは区別できない）。
- 計画条件は、既存 JSON にあれば origin="designer"、なければ既定値（origin="default"）。
- 矩形入力（frontage/depth/road_width）は shape_form="rectangle" として覚え、
  同じ形で戻す。多角形は boundary/edges のまま。
- 単位は m → mm。戻すときは小数 9 桁で丸め、換算の丸め誤差を消す。

このモジュールは既存コードを import しない。既存入力の妥当性（用途地域名など）は
算定側の入口 VolumeInput.from_dict が検査する。
"""

from __future__ import annotations

from typing import Any

from model.types import (
    CreatedBy,
    Edge,
    FactValue,
    PlanConditions,
    PlanValue,
    Scheme,
    SiteFacts,
)

M_TO_MM = 1000.0
_LEGACY_ROAD_SIDE_DEFAULT = "south"
_LEGACY_FIRE_ZONE_DEFAULT = "指定なし"


def _m(value: Any) -> float:
    return float(value) * M_TO_MM


def _to_m(value_mm: float) -> float:
    return round(value_mm / M_TO_MM, 9)


def _manual(value: Any, note: str | None = None) -> FactValue:
    return FactValue(value=value, source="manual", source_name="既存入力 JSON", note=note)


def _designer(value: Any) -> PlanValue:
    return PlanValue(value=value, origin="designer")


# ---------------------------------------------------------------------------
# 既存入力 → Scheme
# ---------------------------------------------------------------------------


def site_facts_from_legacy(site: dict[str, Any], zoning: dict[str, Any]) -> SiteFacts:
    facts: dict[str, Any] = {}

    if site.get("boundary"):
        points = [(_m(x), _m(y)) for x, y in site["boundary"]]
        raw_edges = site.get("edges") or []
        edges = [Edge(kind=e.get("kind", "neighbor"), road_width_mm=_m(e.get("width", 0.0)))
                 for e in raw_edges]
        facts["shape_form"] = "polygon"
        facts["boundary"] = _manual(points)
        facts["edges"] = _manual(edges)
    else:
        frontage, depth, width = _m(site["frontage"]), _m(site["depth"]), _m(site["road_width"])
        facts["shape_form"] = "rectangle"
        facts["boundary"] = _manual(
            [(0.0, 0.0), (frontage, 0.0), (frontage, depth), (0.0, depth)],
            note="frontage/depth から作成（道路境界が y=0）")
        facts["edges"] = _manual([Edge(kind="road", road_width_mm=width),
                                  Edge(kind="neighbor"), Edge(kind="neighbor"), Edge(kind="neighbor")])

    if "road_side" in site:
        facts["road_side"] = _manual(site["road_side"])
    if site.get("north_angle") is not None:
        facts["true_north_deg"] = _manual(float(site["north_angle"]))
    if "corner_lot" in site:
        facts["corner_lot"] = _manual(bool(site["corner_lot"]))

    facts["use_district"] = _manual(zoning["use_district"])
    facts["coverage_ratio"] = _manual(float(zoning["bcr"]))
    facts["far_ratio"] = _manual(float(zoning["far_designated"]))
    if zoning.get("height_limit_absolute") is not None:
        facts["absolute_height_limit_mm"] = _manual(_m(zoning["height_limit_absolute"]))
    if zoning.get("fire_zone"):
        facts["fire_zone"] = _manual(zoning["fire_zone"])
    if "shadow_regulation" in zoning:
        facts["shadow_regulation"] = _manual(bool(zoning["shadow_regulation"]))
    return SiteFacts(**facts)


def plan_conditions_from_legacy(program: dict[str, Any]) -> PlanConditions:
    plan: dict[str, Any] = {}
    if "floor_height" in program:
        plan["floor_height_mm"] = _designer(_m(program["floor_height"]))
    if "gf_height" in program:
        plan["gf_height_mm"] = _designer(_m(program["gf_height"]))
    if "wall_setback" in program:
        plan["wall_setback_mm"] = _designer(_m(program["wall_setback"]))
    if "core_ratio" in program:
        plan["core_ratio"] = _designer(float(program["core_ratio"]))
    if "max_floors" in program:
        plan["max_floors"] = _designer(int(program["max_floors"]))
    if "fireproof" in program:
        plan["fireproof"] = _designer(bool(program["fireproof"]))
    if program.get("building_angle") is not None:
        plan["building_angle_deg"] = _designer(float(program["building_angle"]))
    return PlanConditions(**plan)


def from_legacy_input(legacy: dict[str, Any], *, created_by: CreatedBy,
                      label: str | None = None, **scheme_kwargs: Any) -> Scheme:
    """既存の入力 JSON（辞書）から根の案を作る。"""
    return Scheme.new(
        site_facts_from_legacy(legacy["site"], legacy["zoning"]),
        plan_conditions_from_legacy(legacy.get("program", {})),
        created_by=created_by, label=label, **scheme_kwargs,
    )


# ---------------------------------------------------------------------------
# Scheme → 既存入力
# ---------------------------------------------------------------------------


def _is_legacy_rectangle(facts: SiteFacts) -> bool:
    b = facts.boundary.value or []
    e = facts.edges.value or []
    if len(b) != 4 or len(e) != 4:
        return False
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = b
    return (x0 == 0.0 and y0 == 0.0 and y1 == 0.0 and x1 == x2 and y2 == y3 and x3 == 0.0
            and e[0].kind == "road" and all(k.kind == "neighbor" for k in e[1:]))


def to_legacy_input(scheme: Scheme) -> dict[str, Any]:
    """案を既存の入力 JSON（辞書、単位 m）に戻す。unavailable の項目は省略する。"""
    facts, plan = scheme.site_facts, scheme.plan_conditions
    if not (facts.boundary.available and facts.edges.available):
        raise ValueError("敷地の形（boundary / edges）が不明なので既存の入力に変換できません")

    site: dict[str, Any] = {}
    if facts.shape_form == "rectangle" and _is_legacy_rectangle(facts):
        (_, _), (frontage, _), (_, depth), _ = facts.boundary.value
        site["frontage"] = _to_m(frontage)
        site["depth"] = _to_m(depth)
        site["road_width"] = _to_m(facts.edges.value[0].road_width_mm)
    else:
        site["boundary"] = [[_to_m(x), _to_m(y)] for x, y in facts.boundary.value]
        site["edges"] = [
            ({"kind": "road", "width": _to_m(e.road_width_mm)} if e.kind == "road"
             else {"kind": "neighbor"})
            for e in facts.edges.value
        ]
    if facts.road_side.available:
        site["road_side"] = facts.road_side.value
    if facts.true_north_deg.available:
        site["north_angle"] = facts.true_north_deg.value
    if facts.corner_lot.available:
        site["corner_lot"] = facts.corner_lot.value

    for name in ("use_district", "coverage_ratio", "far_ratio"):
        if not getattr(facts, name).available:
            raise ValueError(f"{name} が不明なので既存の入力に変換できません")
    zoning: dict[str, Any] = {
        "use_district": facts.use_district.value,
        "bcr": facts.coverage_ratio.value,
        "far_designated": facts.far_ratio.value,
        "height_limit_absolute": (_to_m(facts.absolute_height_limit_mm.value)
                                  if facts.absolute_height_limit_mm.available else None),
    }
    if facts.fire_zone.available:
        zoning["fire_zone"] = facts.fire_zone.value
    if facts.shadow_regulation.available:
        zoning["shadow_regulation"] = facts.shadow_regulation.value

    program: dict[str, Any] = {
        "floor_height": _to_m(plan.floor_height_mm.value),
        "gf_height": _to_m(plan.gf_height_mm.value),
        "wall_setback": _to_m(plan.wall_setback_mm.value),
        "core_ratio": plan.core_ratio.value,
        "max_floors": plan.max_floors.value,
    }
    if plan.fireproof.origin == "designer":
        program["fireproof"] = plan.fireproof.value
    if plan.building_angle_deg.value is not None:
        program["building_angle"] = plan.building_angle_deg.value

    return {"site": site, "zoning": zoning, "program": program}
