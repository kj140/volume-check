"""データモデル（docs/architecture.md 4章）。

案（Scheme）＝敷地事実（SiteFacts）＋計画条件（PlanConditions）を、書き換えない記録として持つ。
このパッケージは型・正規化・ハッシュ・差分の検査だけを担い、算定も保存もしない
（solver は model だけを import してよく、model は何も import しない）。
"""

from model.hashing import canonical_json, input_hash, sha256_of
from model.patch import (
    ALLOWED_PLAN_PATHS,
    ALLOWED_SITE_PATHS,
    PatchError,
    PatchOp,
    SiteFactChangeRejected,
    apply_patch,
)
from model.types import (
    AppliedRule,
    CreatedBy,
    Edge,
    FactValue,
    GridAxis,
    Level,
    NotConsidered,
    PlanConditions,
    PlanValue,
    Scheme,
    SiteFacts,
    StageResult,
)

__all__ = [
    "AppliedRule", "CreatedBy", "Edge", "FactValue", "GridAxis", "Level", "NotConsidered",
    "PlanConditions", "PlanValue", "Scheme", "SiteFacts", "StageResult",
    "canonical_json", "input_hash", "sha256_of",
    "ALLOWED_PLAN_PATHS", "ALLOWED_SITE_PATHS", "PatchError", "PatchOp",
    "SiteFactChangeRejected", "apply_patch",
]
