"""段の実行とキャッシュ、複数案の自動生成（M02）。

  solver_version()                      パッケージの版 ＋ 法規定数ファイルのハッシュ
  run_stage(store, scheme_id, stage)    キャッシュがあれば返し、なければ算定して保存する
  generate_variants(store, parent_id)   既存の総当たりを子案として保存し、結果も保存する

キャッシュのキーは (scheme_id, stage, input_hash, solver_version)（architecture.md 5章）。
上流や法規定数が変われば input_hash / solver_version が変わるので、無効化の処理は持たない。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import studies
from model.legacy import to_legacy_input
from model.types import Scheme, StageResult
from solver.volume import stage as volume_stage
from store import SchemeStore

ROOT = Path(__file__).resolve().parent.parent

# 法規定数のファイル。移行後は solver/law/ 配下（architecture.md 6章）。
LAW_FILES: tuple[Path, ...] = (ROOT / "constants.py",)

STAGES = {"volume": volume_stage}


def law_hash() -> str:
    h = hashlib.sha256()
    for path in LAW_FILES:
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def solver_version() -> str:
    return f"{volume_stage.SOLVER_PACKAGE_VERSION}+law.{law_hash()}"


def run_stage(store: SchemeStore, scheme_id: str, stage: str = "volume",
              version: str | None = None) -> StageResult:
    """段を実行する。同じ (案, 段, input_hash, solver_version) の結果があればそれを返す。"""
    if stage not in STAGES:
        raise KeyError(f"段 {stage!r} はまだありません（あるのは {sorted(STAGES)}）")
    scheme = store.get_scheme(scheme_id)
    if scheme is None:
        raise KeyError(f"案 {scheme_id} が見つかりません")
    module = STAGES[stage]
    version = version or solver_version()
    cached = store.get_result(scheme.id, stage, module.stage_input_hash(scheme), version)
    if cached is not None:
        return cached
    result = module.run_volume_stage(scheme, solver_version=version)
    store.save_result(result)
    return result


# ---------------------------------------------------------------------------
# 複数案
# ---------------------------------------------------------------------------


def _label(rank: int, case: studies.StudyCase) -> str:
    parts = [f"{rank:02d}",
             f"振り角{case.building_angle_deg:+.0f}°",
             f"階高{case.floor_height_m:g}m",
             f"外壁後退{case.wall_setback_m:g}m"]
    if case.fireproof:
        parts.append("耐火")
    if case.added_for_sky:
        parts.append("［外壁後退ごとの最良案］")
    return " ".join(parts)


def _patch(case: studies.StudyCase) -> list[dict[str, Any]]:
    return [
        {"path": "plan_conditions.building_angle_deg", "value": case.building_angle_deg},
        {"path": "plan_conditions.floor_height_mm", "value": case.floor_height_m * 1000.0},
        {"path": "plan_conditions.wall_setback_mm", "value": case.wall_setback_m * 1000.0},
        {"path": "plan_conditions.fireproof", "value": case.fireproof},
    ]


def generate_variants(store: SchemeStore, parent_id: str, *,
                      angles_deg: tuple[float, ...] = studies.DEFAULT_ANGLES_DEG,
                      floor_heights_m: tuple[float, ...] = studies.DEFAULT_FLOOR_HEIGHTS_M,
                      wall_setbacks_m: tuple[float, ...] = studies.DEFAULT_WALL_SETBACKS_M,
                      fireproof_options: tuple[bool, ...] = (),
                      limit: int = 50,
                      check_sky: bool = False,
                      version: str | None = None
                      ) -> list[tuple[Scheme, StageResult]]:
    """既存の総当たり（studies.generate）の各案を、created_by="batch" の子案として保存する。

    並び順は既存どおり（延床の大きい順、check_sky のときは末尾に外壁後退ごとの最良案）。
    順位は子案の label の先頭2桁と、戻り値の順番で表す。各子案の volume 結果も保存する。
    """
    parent = store.get_scheme(parent_id)
    if parent is None:
        raise KeyError(f"案 {parent_id} が見つかりません")
    study = studies.generate(
        to_legacy_input(parent),
        angles_deg=tuple(angles_deg), floor_heights_m=tuple(floor_heights_m),
        wall_setbacks_m=tuple(wall_setbacks_m), fireproof_options=tuple(fireproof_options),
        limit=limit, check_sky=check_sky,
    )
    version = version or solver_version()
    out: list[tuple[Scheme, StageResult]] = []
    for rank, case in enumerate(study.cases, start=1):
        child = store.derive(parent.id, _patch(case), "batch", label=_label(rank, case))
        out.append((child, run_stage(store, child.id, "volume", version)))
    return out
