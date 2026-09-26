"""ボリュームの段（M02）。既存の算定を包み、案を受け取って StageResult を返す。

  run_volume_stage(scheme) -> StageResult

- 内部で model.legacy.to_legacy_input を使い、既存の算定（solver.solve、skyfactor.evaluate）
  を呼ぶ。**既存の算定関数の中身は変えない。**
- 既存の結果の項目は捨てずに VolumeStageData に移す（model/stages/volume.py）。
- 適用規定・注記の構造化は solver/volume/rules.py の対応表で行う。
- 入出力（ファイル・ネットワーク・DB・時刻・乱数）を持たない。キャッシュ・保存は api/ の役目。
"""

from __future__ import annotations

from typing import Any

import skyfactor
from model.hashing import sha256_of
from model.legacy import to_legacy_input
from model.stages.volume import VolumeStageData
from model.types import AppliedRule, Level, NotConsidered, Scheme, StageResult
from models import VolumeInput, VolumeResult
from solver import solve
from solver.volume.rules import BCR_EFFECT_WHEN_RELAXED, note_map, rule_map

STAGE = "volume"

# パッケージの版。算定の中身を変えたら上げる。api 側が法規定数ファイルのハッシュと
# 組み合わせて solver_version にする（architecture.md 5章）。
SOLVER_PACKAGE_VERSION = "0.1.0"

# 既存コードが、敷地事実が不明（unavailable）のときに黙って使う既定
# （docs/decisions/0003。既存コードの挙動を記録したもので、新しく決めた値ではない）。
LEGACY_SITE_DEFAULTS: dict[str, str] = {
    "road_side": "南（south）",
    "true_north_deg": "road_side から導く（南道路→90度）",
    "corner_lot": "角地でない",
    "fire_zone": "指定なし",
    "shadow_regulation": "日影規制の対象区域でない",
}


def stage_input_hash(scheme: Scheme) -> str:
    """段の input_hash：段の名前、この段が使う入力、上流の input_hash（volume には上流がない）。"""
    return sha256_of({
        "stage": STAGE,
        "inputs": {"site_facts": scheme.site_facts.values(),
                   "plan_conditions": scheme.plan_conditions.values()},
        "upstream": {},
    })


# ---------------------------------------------------------------------------
# 既存の結果 → VolumeStageData
# ---------------------------------------------------------------------------


def _pts(outline) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in outline]


def _floor_name(floor: int) -> str:
    return f"{floor}F"


def _volume_data(r: VolumeResult, sky: skyfactor.SkyFactorStudy | None) -> VolumeStageData:
    ratio = r.input.program.core_ratio
    gains = r.constraint_gains_mm2()
    return VolumeStageData(
        law={
            "far_designated": r.far_designated,
            "far_by_road": r.far_by_road,
            "far_effective": r.far_effective,
            "far_road_coefficient": r.far_road_coefficient,
            "site_area_mm2": r.site_area_mm2,
            "max_far_area_mm2": r.max_far_area_mm2,
            "max_building_area_mm2": r.max_building_area_mm2,
            "road_slant_gradient": r.road_slant_gradient,
            "road_slant_applicable_distance_mm": r.road_slant_applicable_distance_mm,
            "neighbor_slant_start_mm": r.neighbor_slant_start_mm,
            "neighbor_slant_gradient": r.neighbor_slant_gradient,
            "north_slant_start_mm": r.north_slant_start_mm,
            "north_slant_gradient": r.north_slant_gradient,
            "height_limit_applied_mm": r.height_limit_applied_mm,
            "bcr_effective": r.bcr_effective,
            "bcr_relaxations": [(d, b) for d, b in r.bcr_relaxations],
        },
        placement={
            "building_angle_rad": r.building_angle_rad,
            "building_angle_deg": r.building_angle_deg,
            "footprint": _pts(r.footprint),
            "bcr_inset_mm": r.bcr_inset_mm,
        },
        floors=[
            {
                "floor": f.floor,
                "level": Level(name=_floor_name(f.floor), fl_mm=f.level_mm,
                               floor_height_mm=f.story_height_mm),
                "top_mm": f.top_mm,
                "x_min_mm": f.x_min_mm,
                "x_max_mm": f.x_max_mm,
                "y_min_mm": f.y_min_mm,
                "y_max_mm": f.y_max_mm,
                "width_mm": f.width_mm,
                "depth_mm": f.depth_mm,
                "setback_road_mm": f.setback_road_mm,
                "setback_neighbor_mm": f.setback_neighbor_mm,
                "setback_north_mm": f.setback_north_mm,
                "governing": f.governing,
                "impacts": [{"constraint": i.constraint.value,
                             "area_gain_mm2": i.area_gain_mm2,
                             "setback_mm": i.setback_mm} for i in f.impacts],
                "dominant_constraint": (f.dominant_constraint.value
                                        if f.dominant_constraint else None),
                "road_slant_capped": f.road_slant_capped,
                "unconstrained_area_mm2": f.unconstrained_area_mm2,
                "outline": _pts(f.outline),
                "area_mm2": f.area_mm2,
                "gross_area_mm2": f.gross_area_mm2,
                "far_area_mm2": f.far_area_mm2,
                "rentable_area_mm2": f.rentable_area_mm2(ratio),
                "area_loss_mm2": f.area_loss_mm2,
            }
            for f in r.floors
        ],
        totals={
            "floor_count": r.floor_count,
            "total_gross_area_mm2": r.total_gross_area_mm2,
            "total_far_area_mm2": r.total_far_area_mm2,
            "total_rentable_area_mm2": r.total_rentable_area_mm2,
            "building_area_mm2": r.building_area_mm2,
            "max_height_mm": r.max_height_mm,
            "achieved_far": r.achieved_far,
            "achieved_bcr": r.achieved_bcr,
            "total_area_loss_mm2": r.total_area_loss_mm2,
        },
        rule_reductions=[{"constraint": c.value, "area_gain_mm2": g} for c, g in gains.items()],
        dominant_constraint=r.dominant_constraint.value if r.dominant_constraint else None,
        stop={"reason": r.stop_reason.value if r.stop_reason else None,
              "detail": r.stop_detail},
        sky_factor=None if sky is None else _sky_data(sky),
        legacy_applied_rules=[{"label": a.label, "value": a.value, "basis": a.basis}
                              for a in r.applied_rules],
        legacy_notes=list(r.notes),
    )


def _sky_data(s: skyfactor.SkyFactorStudy) -> dict[str, Any]:
    return {
        "verdict": s.verdict,
        "passes": s.passes,
        "worth_studying": s.worth_studying,
        "worst_margin": s.worst_margin,
        "gain_mm2": s.gain_mm2,
        "north_slant_unchecked": s.north_slant_unchecked,
        "planned_floor_count": s.planned_floor_count,
        "planned_max_height_mm": s.planned_max_height_mm,
        "planned_total_gross_area_mm2": s.planned_total_gross_area_mm2,
        "planned_outline": _pts(s.planned_outline),
        "base_total_gross_area_mm2": s.base_total_gross_area_mm2,
        "base_max_height_mm": s.base_max_height_mm,
        "wall_setback_mm": s.wall_setback_mm,
        "suggestion_searched": s.suggestion_searched,
        "suggestion": None if s.suggestion is None else {
            "wall_setback_mm": s.suggestion.wall_setback_mm,
            "floor_count": s.suggestion.floor_count,
            "max_height_mm": s.suggestion.max_height_mm,
            "total_gross_area_mm2": s.suggestion.total_gross_area_mm2,
            "worst_margin": s.suggestion.worst_margin,
            "outline": _pts(s.suggestion.outline),
        },
        "edges": [
            {
                "edge_index": e.edge_index,
                "kind": e.kind.value,
                "label": e.label,
                "offset_mm": e.offset_mm,
                "passes": e.passes,
                "points": [{"position": (float(p.position[0]), float(p.position[1])),
                            "planned": p.planned, "compliant": p.compliant,
                            "margin": p.margin, "passes": p.passes} for p in e.points],
            }
            for e in s.edges
        ],
        "notes": list(s.notes),
    }


# ---------------------------------------------------------------------------
# 既存の出力 → applied_rules / not_considered / warnings / defaults_used
# ---------------------------------------------------------------------------


def _applied_rules(r: VolumeResult) -> list[AppliedRule]:
    gains = {c.value: g for c, g in r.constraint_gains_mm2().items()}
    relaxed = bool(r.bcr_relaxations)
    out: list[AppliedRule] = []
    for a in r.applied_rules:
        m = rule_map(a.label)
        effect = m.effect
        # 建蔽率が緩和された案では、削減量は「緩和後の建蔽率」の行に付ける
        if relaxed and m.rule_id == "bcr.designated":
            effect = None
        if relaxed and m.rule_id == BCR_EFFECT_WHEN_RELAXED:
            effect = "建蔽率"
        out.append(AppliedRule(
            rule_id=m.rule_id,
            article=a.basis,
            summary=f"{a.label}：{a.value}",
            effect_mm2=gains.get(effect) if effect else None,
        ))
    return out


def _notes(r: VolumeResult) -> tuple[list[NotConsidered], list[str]]:
    not_considered: list[NotConsidered] = []
    warnings: list[str] = []
    for note in r.notes:
        m = note_map(note)
        if m.kind == "not_considered":
            not_considered.append(NotConsidered(item=m.item, direction=m.direction, note=note))
        else:
            warnings.append(note)
    return not_considered, warnings


def _defaults_used(scheme: Scheme) -> list[str]:
    """既定値で進めた項目のパス。計画条件の既定と、既存コードが黙って使う敷地事実の既定。"""
    used = list(scheme.plan_conditions.defaults_used())
    facts = scheme.site_facts
    for name in LEGACY_SITE_DEFAULTS:
        if not getattr(facts, name).available:
            used.append(f"site_facts.{name}")
    return used


# ---------------------------------------------------------------------------
# 段
# ---------------------------------------------------------------------------


def run_volume_stage(scheme: Scheme, solver_version: str | None = None) -> StageResult:
    """案のボリュームを算定して StageResult を返す。

    solver_version は api が法規定数ファイルのハッシュと組み合わせて渡す。
    省略したときはパッケージの版だけを使う。
    """
    version = solver_version or SOLVER_PACKAGE_VERSION
    base = {"scheme_id": scheme.id, "stage": STAGE,
            "input_hash": stage_input_hash(scheme), "solver_version": version,
            "defaults_used": _defaults_used(scheme)}
    try:
        legacy = to_legacy_input(scheme)
        result = solve(VolumeInput.from_dict(legacy))
    except ValueError as e:
        return StageResult(status="error", warnings=[f"算定できませんでした: {e}"], **base)

    sky = skyfactor.evaluate(result) if result.floor_count > 0 else None
    data = _volume_data(result, sky)
    not_considered, warnings = _notes(result)

    status = "ok"
    if result.floor_count == 0:
        status = "infeasible"
        warnings.insert(0, f"成立する階がありません: {result.stop_reason.value}"
                           f"（{result.stop_detail}）")
    return StageResult(
        status=status,
        data=data.model_dump(mode="json"),
        applied_rules=_applied_rules(result),
        not_considered=not_considered,
        warnings=warnings,
        **base,
    )
