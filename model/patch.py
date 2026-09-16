"""差分の適用と検査（docs/architecture.md 4.5）。

差分は {"path": "plan_conditions.wall_setback_mm", "value": ...} の並び。

- 計画条件（plan_conditions.*）：誰でも変えられる。値を渡すと origin="designer" になる。
- 敷地事実（site_facts.*）：created_by="ui" だけ。値は FactValue の形
  {"value": ..., "source": ..., "source_name": ...} で、source と source_name が必須
  （{"source": "unavailable"} で「不明」に戻せる）。
- created_by="chat" / "batch" が site_facts に触れたら SiteFactChangeRejected。
  プロンプトでの注意に頼らず、ここで拒否する（CLAUDE.md 6）。
- 存在しない項目、型の合わない値は PatchError。

書き換えを許す項目の一覧はこのファイルの ALLOWED_* だけで持つ。
"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import ValidationError

from model.types import CreatedBy, FactValue, PlanConditions, PlanValue, Scheme, SiteFacts


class PatchOp(TypedDict, total=False):
    path: str
    value: Any


# 書き換えを許す項目（1か所で持つ）
ALLOWED_PLAN_PATHS: tuple[str, ...] = tuple(
    f"plan_conditions.{name}" for name in PlanConditions.model_fields)
ALLOWED_SITE_PATHS: tuple[str, ...] = tuple(
    f"site_facts.{name}" for name in SiteFacts.model_fields if name != "shape_form")

# 敷地事実を変えてよい作成者
SITE_FACT_EDITORS: frozenset[str] = frozenset({"ui"})

SITE_FACT_GUIDANCE = "敷地の事実は画面から出典つきで入力してください"


class PatchError(ValueError):
    """差分が不正（存在しない項目・型の不一致・出典なし など）。"""


class SiteFactChangeRejected(PatchError):
    """会話（chat）や自動生成（batch）からの敷地事実の変更。"""

    def __init__(self, paths: list[str], created_by: str):
        self.paths = paths
        self.created_by = created_by
        super().__init__(
            f"created_by='{created_by}' からは敷地事実を変えられません: "
            f"{', '.join(paths)}。{SITE_FACT_GUIDANCE}。"
        )


def _fact_from_patch(path: str, raw: Any) -> FactValue:
    if not isinstance(raw, dict):
        raise PatchError(
            f"{path}: 敷地事実は {{'value': ..., 'source': ..., 'source_name': ...}} の形で"
            f"渡してください（受け取った値: {raw!r}）")
    if raw.get("source") is None:
        raise PatchError(f"{path}: 敷地事実の変更には source（出所）が必要です")
    if raw["source"] != "unavailable" and not raw.get("source_name"):
        raise PatchError(f"{path}: 敷地事実の変更には source_name（出所の名前）が必要です")
    return raw


def apply_patch(scheme: Scheme, patch: list[PatchOp], created_by: CreatedBy
                ) -> tuple[SiteFacts, PlanConditions]:
    """差分を検査して適用し、新しい (SiteFacts, PlanConditions) を返す。元の案は変えない。

    子案を作って保存するのは store.derive() の役目。
    """
    ops = list(patch)
    for op in ops:
        if not isinstance(op, dict) or "path" not in op:
            raise PatchError(f"差分には path が必要です: {op!r}")
        if "value" not in op:
            raise PatchError(f"{op['path']}: 差分には value が必要です")

    unknown = [op["path"] for op in ops
               if op["path"] not in ALLOWED_PLAN_PATHS and op["path"] not in ALLOWED_SITE_PATHS]
    if unknown:
        raise PatchError(f"存在しない、または変更できない項目です: {', '.join(unknown)}")

    site_paths = [op["path"] for op in ops if op["path"] in ALLOWED_SITE_PATHS]
    if site_paths and created_by not in SITE_FACT_EDITORS:
        raise SiteFactChangeRejected(site_paths, created_by)

    facts = scheme.site_facts.model_dump(mode="json")
    plan = scheme.plan_conditions.model_dump(mode="json")
    for op in ops:
        section, field = op["path"].split(".", 1)
        if section == "plan_conditions":
            plan[field] = {"value": op["value"], "origin": "designer", "note": None}
        else:
            facts[field] = _fact_from_patch(op["path"], op["value"])

    try:
        new_facts = SiteFacts.model_validate(facts)
        new_plan = PlanConditions.model_validate(plan)
    except ValidationError as e:
        raise PatchError(f"型の合わない値があります: {_describe(e)}") from e
    return new_facts, new_plan


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in error.errors()
    )
