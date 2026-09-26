"""案と段の API（M02）。

  POST /api/schemes                          敷地事実と計画条件から案を作る（created_by="ui"）
  GET  /api/schemes/{id}
  POST /api/schemes/{id}/derive              {patch, created_by, label} から子案
  POST /api/schemes/{id}/stages/volume       段の実行（キャッシュがあれば返す）
  POST /api/schemes/{id}/variants            複数案（子案と結果を順番どおりに）
  GET  /api/sites/{site_id}/schemes

公開中の Web アプリ（web/app.py）には載せない。認証がないため、この API は
api/app.py の別アプリとして localhost だけで待ち受ける（docs/decisions/0005）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import studies
from api import stages
from model.patch import PatchError, SiteFactChangeRejected
from model.types import CreatedBy, PlanConditions, Scheme, SiteFacts, StageResult
from store import SchemeStore

DEFAULT_DB = Path(__file__).resolve().parent.parent / ".data" / "schemes.sqlite3"

router = APIRouter(prefix="/api")


def get_store() -> Iterator[SchemeStore]:
    """要求ごとに開いて閉じる（SQLite の接続はスレッドをまたげないため）。"""
    path = Path(os.environ.get("VOLUME_CHECK_DB", DEFAULT_DB))
    path.parent.mkdir(parents=True, exist_ok=True)
    store = SchemeStore(path)
    try:
        yield store
    finally:
        store.close()


class SchemeIn(BaseModel):
    site_facts: SiteFacts
    plan_conditions: PlanConditions = Field(default_factory=PlanConditions)
    label: str | None = None


class DeriveIn(BaseModel):
    patch: list[dict[str, Any]]
    created_by: CreatedBy
    label: str | None = None


class VariantsIn(BaseModel):
    angles_deg: list[float] = Field(default_factory=lambda: list(studies.DEFAULT_ANGLES_DEG))
    floor_heights_m: list[float] = Field(default_factory=lambda: list(studies.DEFAULT_FLOOR_HEIGHTS_M))
    wall_setbacks_m: list[float] = Field(default_factory=lambda: list(studies.DEFAULT_WALL_SETBACKS_M))
    fireproof_options: list[bool] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=100)
    check_sky: bool = False


class SchemeWithResult(BaseModel):
    scheme: Scheme
    result: StageResult


def _scheme_or_404(store: SchemeStore, scheme_id: str) -> Scheme:
    scheme = store.get_scheme(scheme_id)
    if scheme is None:
        raise HTTPException(status_code=404, detail=f"案 {scheme_id} が見つかりません")
    return scheme


@router.post("/schemes", response_model=Scheme)
def create_scheme(body: SchemeIn, store: SchemeStore = Depends(get_store)) -> Scheme:
    scheme = Scheme.new(body.site_facts, body.plan_conditions, created_by="ui", label=body.label)
    store.save_scheme(scheme)
    return scheme


@router.get("/schemes/{scheme_id}", response_model=Scheme)
def get_scheme(scheme_id: str, store: SchemeStore = Depends(get_store)) -> Scheme:
    return _scheme_or_404(store, scheme_id)


@router.post("/schemes/{scheme_id}/derive", response_model=Scheme)
def derive(scheme_id: str, body: DeriveIn, store: SchemeStore = Depends(get_store)) -> Scheme:
    _scheme_or_404(store, scheme_id)
    try:
        return store.derive(scheme_id, body.patch, body.created_by, label=body.label)
    except SiteFactChangeRejected as e:
        raise HTTPException(status_code=422, detail={
            "message": str(e), "rejected_paths": e.paths, "created_by": e.created_by}) from e
    except PatchError as e:
        raise HTTPException(status_code=422, detail={"message": str(e)}) from e


@router.post("/schemes/{scheme_id}/stages/{stage}", response_model=StageResult)
def run_stage(scheme_id: str, stage: str, store: SchemeStore = Depends(get_store)) -> StageResult:
    _scheme_or_404(store, scheme_id)
    if stage not in stages.STAGES:
        raise HTTPException(status_code=404, detail=f"段 {stage!r} はまだありません")
    return stages.run_stage(store, scheme_id, stage)


@router.post("/schemes/{scheme_id}/variants", response_model=list[SchemeWithResult])
def variants(scheme_id: str, body: VariantsIn | None = None,
             store: SchemeStore = Depends(get_store)) -> list[SchemeWithResult]:
    _scheme_or_404(store, scheme_id)
    opts = body or VariantsIn()
    total = (len(opts.angles_deg) * len(opts.floor_heights_m) * len(opts.wall_setbacks_m)
             * max(1, len(opts.fireproof_options)))
    if total > studies.MAX_CASES:
        raise HTTPException(status_code=422,
                            detail=f"組み合わせが多すぎます（{total} 通り、上限 {studies.MAX_CASES}）")
    pairs = stages.generate_variants(
        store, scheme_id,
        angles_deg=tuple(opts.angles_deg), floor_heights_m=tuple(opts.floor_heights_m),
        wall_setbacks_m=tuple(opts.wall_setbacks_m),
        fireproof_options=tuple(opts.fireproof_options),
        limit=opts.limit, check_sky=opts.check_sky,
    )
    return [SchemeWithResult(scheme=s, result=r) for s, r in pairs]


@router.get("/sites/{site_id}/schemes", response_model=list[Scheme])
def list_site_schemes(site_id: str, store: SchemeStore = Depends(get_store)) -> list[Scheme]:
    return store.list_schemes(site_id)
