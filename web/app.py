"""FastAPI アプリ。

計算は既存の solver.solve()、図面は既存の drawer.draw() をそのまま呼ぶ。
このモジュールには法規のロジックを一切置かない。

起動（開発）:
    volume_check/ で
    .venv/Scripts/python -m uvicorn web.app:app --reload --port 8000

起動（本番 / Railway 等の PaaS）:
    python -m web.app
    待ち受けポートは環境変数 PORT から取る（未設定なら 8000）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Literal

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    # 既存モジュール（solver / drawer / models ...）はフラット配置なのでパスを通す
    sys.path.insert(0, str(_ROOT))

import httpx                                                # noqa: E402
from fastapi import FastAPI, HTTPException, Query          # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse   # noqa: E402
from fastapi.staticfiles import StaticFiles                # noqa: E402
from pydantic import BaseModel, Field                      # noqa: E402
from starlette.background import BackgroundTask            # noqa: E402

import constants as C                                      # noqa: E402
from drawer import draw                                    # noqa: E402
from models import VolumeInput, VolumeResult               # noqa: E402
import skyfactor                                           # noqa: E402
import studies                                             # noqa: E402
from solver import solve                                   # noqa: E402

from . import zoning                                       # noqa: E402
from .geo import frontage_depth_m                          # noqa: E402
from .svg_plan import (                                     # noqa: E402
    render_floor_plans,
    render_site_plan,
    render_sky_plan,
)
from .svg_section import render as render_svg              # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
M2 = C.M2_TO_MM2
MM = C.M_TO_MM

app = FastAPI(
    title="ボリュームチェック図 Web",
    description="矩形敷地の企画用ボリューム試算。確認申請には使用できません。",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# リクエスト / レスポンス
# ---------------------------------------------------------------------------


class SiteIn(BaseModel):
    """単位は m。既存の入力JSONと同じ形。"""

    frontage: float = Field(default=20.0, gt=0, description="道路に接する辺の長さ[m]")
    depth: float = Field(default=30.0, gt=0, description="道路と直交する辺の長さ[m]")
    road_width: float = Field(default=6.0, gt=0, description="前面道路の幅員[m]")
    road_side: Literal["north", "east", "south", "west"]
    corner_lot: bool = Field(
        default=False, description="角地等の指定を受けているか（法53条3項2号）"
    )
    boundary: list[tuple[float, float]] | None = Field(
        default=None, description="非矩形敷地の頂点列[m]。指定すると矩形の指定より優先")
    edges: list[dict] | None = Field(
        default=None, description="辺ごとの {kind: road|neighbor, width: 幅員[m]}")
    north_angle: float | None = Field(
        default=None, description="ローカル座標における北の向き[度]")


class ZoningIn(BaseModel):
    use_district: str
    bcr: float = Field(gt=0, le=1.0, description="建蔽率（倍率表記 0.8 = 80%）")
    far_designated: float = Field(gt=0, description="指定容積率（倍率表記 6.0 = 600%）")
    height_limit_absolute: float | None = Field(default=None, gt=0)
    fire_zone: Literal["防火地域", "準防火地域", "指定なし"] = Field(
        default="指定なし", description="防火地域の指定（法61条）"
    )


class ProgramIn(BaseModel):
    floor_height: float = Field(gt=0)
    gf_height: float = Field(gt=0)
    wall_setback: float = Field(ge=0)
    core_ratio: float = Field(ge=0, lt=1.0)
    max_floors: int = Field(ge=1, le=200)
    building_angle: float | None = Field(
        default=None, ge=-90, le=90,
        description="前面道路に対する建物の振り角[度]。未指定なら道路に平行")
    fireproof: bool = Field(
        default=False, description="耐火建築物等とするか（法53条3項1号・6項1号）"
    )


class VolumeIn(BaseModel):
    site: SiteIn
    zoning: ZoningIn
    program: ProgramIn


class StudyIn(BaseModel):
    """複数案の自動生成。振るのは計画側の選択だけ。"""

    base: VolumeIn
    angles_deg: list[float] | None = Field(
        default=None, description="前面道路に対する振り角[度]の候補")
    floor_heights_m: list[float] | None = Field(
        default=None, description="基準階の階高[m]の候補")
    wall_setbacks_m: list[float] | None = Field(
        default=None, description="外壁後退[m]の候補")
    try_fireproof: bool = Field(
        default=False, description="耐火建築物等とするかも振るか")
    limit: int = Field(default=20, ge=1, le=100)


class RectIn(BaseModel):
    """地図上で描いた矩形（Leaflet の bounds）。"""

    south: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    road_side: Literal["north", "east", "south", "west"] = "south"


def _to_input(payload: VolumeIn) -> VolumeInput:
    try:
        return VolumeInput.from_dict(payload.model_dump())
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"入力が不正です: {e}") from e


def _solve(payload: VolumeIn) -> VolumeResult:
    try:
        return solve(_to_input(payload))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def _summary(r: VolumeResult) -> dict:
    """画面表示用の要約。面積は m2、長さは m。"""
    return {
        "site_area_m2": round(r.site_area_mm2 / M2, 2),
        "far_designated": r.far_designated,
        "far_by_road": r.far_by_road,
        "far_effective": r.far_effective,
        "far_road_coefficient": r.far_road_coefficient,
        "far_achieved": round(r.achieved_far, 4),
        "max_far_area_m2": round(r.max_far_area_mm2 / M2, 2),
        "bcr": r.input.zoning.bcr,
        "bcr_effective": r.bcr_effective,
        "bcr_relaxations": [
            {"description": d, "basis": b} for d, b in r.bcr_relaxations
        ],
        "bcr_achieved": round(r.achieved_bcr, 4),
        "building_area_m2": round(r.building_area_mm2 / M2, 2),
        "max_building_area_m2": round(r.max_building_area_mm2 / M2, 2),
        "bcr_inset_m": round(r.bcr_inset_mm / MM, 3),
        "floor_count": r.floor_count,
        "max_height_m": round(r.max_height_mm / MM, 2),
        "total_gross_area_m2": round(r.total_gross_area_mm2 / M2, 2),
        "total_far_area_m2": round(r.total_far_area_mm2 / M2, 2),
        "total_rentable_area_m2": round(r.total_rentable_area_mm2 / M2, 2),
        "road_slant_gradient": r.road_slant_gradient,
        "road_slant_applicable_distance_m": round(
            r.road_slant_applicable_distance_mm / MM, 1
        ),
        "neighbor_slant_start_m": (
            None if r.neighbor_slant_start_mm is None
            else round(r.neighbor_slant_start_mm / MM, 1)
        ),
        "neighbor_slant_gradient": r.neighbor_slant_gradient,
        "height_limit_applied_m": (
            None if r.height_limit_applied_mm is None
            else round(r.height_limit_applied_mm / MM, 2)
        ),
        "stop_reason": r.stop_reason.value if r.stop_reason else None,
        "stop_detail": r.stop_detail,
        "building_angle_deg": round(r.building_angle_deg, 2),
        "site_is_rectangle": r.input.site.is_rectangle,
        # 規制がかからなかった場合との差
        "unconstrained_area_m2": round(
            sum(f.unconstrained_area_mm2 for f in r.floors) / M2, 2
        ),
        "total_area_loss_m2": round(r.total_area_loss_mm2 / M2, 2),
        "dominant_constraint": (
            r.dominant_constraint.value if r.dominant_constraint else None
        ),
    }


def _constraint_gains(r: VolumeResult) -> list[dict]:
    """どの規定がどれだけボリュームを削っているか（増分の大きい順）。"""
    return [
        {
            "constraint": c.value,
            "basis": c.basis,
            "area_gain_m2": round(gain / M2, 2),
        }
        for c, gain in r.constraint_gains_mm2().items()
    ]


def _floors(r: VolumeResult) -> list[dict]:
    rows = []
    cumulative = 0.0
    for f in r.floors:
        cumulative += f.far_area_mm2
        rows.append({
            "floor": f.floor,
            "level_m": round(f.level_mm / MM, 2),
            "story_height_m": round(f.story_height_mm / MM, 2),
            "top_m": round(f.top_mm / MM, 2),
            "width_m": round(f.width_mm / MM, 3),
            "depth_m": round(f.depth_mm / MM, 3),
            "area_m2": round(f.gross_area_mm2 / M2, 2),
            "far_area_m2": round(f.far_area_mm2 / M2, 2),
            "cumulative_far_area_m2": round(cumulative / M2, 2),
            "setback_road_m": round(f.setback_road_mm / MM, 2),
            "setback_neighbor_m": round(f.setback_neighbor_mm / MM, 2),
            "governing": f.governing,
            "dominant_constraint": (
                f.dominant_constraint.value if f.dominant_constraint else None
            ),
            "area_loss_m2": round(f.area_loss_mm2 / M2, 2),
            "impacts": [
                {"constraint": i.constraint.value,
                 "area_gain_m2": round(i.area_gain_mm2 / M2, 2)}
                for i in f.impacts
            ],
        })
    return rows


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict:
    """死活監視用。Railway の healthcheckPath がこれを叩く。

    ここで実際に solve() を1回通し、計算系まで動いていることを確かめる。
    """
    probe = VolumeIn(
        site=SiteIn(frontage=20.0, depth=30.0, road_width=12.0, road_side="south"),
        zoning=ZoningIn(use_district="商業地域", bcr=0.8, far_designated=6.0),
        program=ProgramIn(floor_height=4.2, gf_height=4.5, wall_setback=0.5,
                          core_ratio=0.18, max_floors=30),
    )
    result = solve(_to_input(probe))
    return {"status": "ok", "version": app.version, "probe_floors": result.floor_count}


@app.get("/api/use-districts")
def use_districts() -> dict:
    """用途地域の選択肢と、絶対高さ制限が必須の地域。"""
    return {
        "districts": [d.value for d in C.UseDistrict],
        "fire_zones": [z.value for z in C.FireZone],
        "absolute_height_limit_required": [
            d.value for d in C.DISTRICTS_REQUIRING_ABSOLUTE_HEIGHT_LIMIT
        ],
        "default_absolute_height_limit_m": C.DEFAULT_ABSOLUTE_HEIGHT_LIMIT_M,
        "zoning_lookup": {
            "reinfolib_enabled": bool(os.environ.get("REINFOLIB_API_KEY", "").strip()),
            "local_datasets": zoning.local_dataset_names(),
        },
    }


@app.post("/api/rect")
def rect_to_size(rect: RectIn) -> dict:
    """地図上の矩形から間口・奥行[m]を求める。"""
    frontage, depth = frontage_depth_m(
        rect.south, rect.west, rect.north, rect.east, rect.road_side
    )
    return {
        "frontage": round(frontage, 2),
        "depth": round(depth, 2),
        "area_m2": round(frontage * depth, 2),
        "center": {"lat": (rect.south + rect.north) / 2,
                   "lon": (rect.west + rect.east) / 2},
    }


@app.get("/api/zoning")
async def zoning_lookup(
    lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)
) -> dict:
    """緯度経度から敷地の法規条件を推定する。失敗しても200で返す。

    用途地域・建蔽率・容積率のほか、防火地域・区域区分・地区計画・高度利用地区も
    引く。公開APIに存在しない項目（高度地区・道路幅員など）は unavailable で返す。
    """
    return (await zoning.lookup(lon, lat)).to_dict()


GSI_ADDRESS_SEARCH = "https://msearch.gsi.go.jp/address-search/AddressSearch"


@app.get("/api/geocode")
async def geocode(q: str = Query(min_length=1, max_length=200)) -> dict:
    """住所・地名から座標を引く（国土地理院 住所検索API・APIキー不要）。

    ブラウザから直接叩くとCORSに左右されるので、サーバ経由にしている。
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(GSI_ADDRESS_SEARCH, params={"q": q})
        res.raise_for_status()
        hits = res.json() or []
    except (httpx.HTTPError, ValueError) as e:
        return {"results": [], "detail": f"住所検索に失敗しました: {e}"}

    results = []
    for hit in hits[:10]:
        coords = (hit.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        results.append({
            "title": (hit.get("properties") or {}).get("title", ""),
            "lon": coords[0],
            "lat": coords[1],
        })
    return {"results": results}


@app.post("/api/solve")
def api_solve(payload: VolumeIn) -> dict:
    """階別の可能形状を算定し、断面図SVGを添えて返す。"""
    r = _solve(payload)
    return {
        "summary": _summary(r),
        "floors": _floors(r),
        "constraint_gains": _constraint_gains(r),
        "applied_rules": [
            {"label": x.label, "value": x.value, "basis": x.basis} for x in r.applied_rules
        ],
        "notes": list(r.notes),
        "svg": render_svg(r),
        "svg_site_plan": render_site_plan(r),
        "svg_floor_plans": render_floor_plans(r),
    }


@app.post("/api/studies")
def api_studies(payload: StudyIn) -> dict:
    """条件を振って複数案を作り、延床面積の大きい順に返す。

    法規条件は敷地で決まるので振らない。動かすのは建物の向き・階高・
    外壁後退・耐火建築物等とするか、という計画側の選択だけ。
    """
    base = payload.base.model_dump()
    try:
        study = studies.generate(
            base,
            angles_deg=tuple(payload.angles_deg) if payload.angles_deg
            else studies.DEFAULT_ANGLES_DEG,
            floor_heights_m=tuple(payload.floor_heights_m) if payload.floor_heights_m
            else studies.DEFAULT_FLOOR_HEIGHTS_M,
            wall_setbacks_m=tuple(payload.wall_setbacks_m) if payload.wall_setbacks_m
            else studies.DEFAULT_WALL_SETBACKS_M,
            fireproof_options=(False, True) if payload.try_fireproof else (),
            limit=payload.limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    result = study.to_dict()
    result["angle_sweep"] = [
        {"angle_deg": a, "total_gross_area_m2": round(area, 2)}
        for a, area in studies.sweep_angles(
            base,
            tuple(payload.angles_deg) if payload.angles_deg
            else studies.DEFAULT_ANGLES_DEG,
        )
    ]
    return result


@app.post("/api/skyfactor")
def api_skyfactor(payload: VolumeIn) -> dict:
    """天空率で道路斜線を緩和できる見込みがあるかを判定する（法56条7項1号）。

    確認申請の判定ではなく、企画段階で「天空率を検討する価値があるか」を
    見るための試算。隣地斜線・北側斜線の天空率は未対応。
    """
    r = _solve(payload)
    study = skyfactor.evaluate(r)
    result = study.to_dict()
    result["svg_sky_plan"] = render_sky_plan(r, study) if study.roads else ""
    return result


@app.post("/api/dxf")
def api_dxf(payload: VolumeIn) -> FileResponse:
    """既存の drawer.py で DXF を生成して返す。"""
    r = _solve(payload)
    tmp_dir = Path(tempfile.mkdtemp(prefix="volume_check_"))
    path = tmp_dir / "volume_check.dxf"
    draw(r, path)
    return FileResponse(
        path,
        media_type="application/dxf",
        filename="volume_check.dxf",
        headers={"Cache-Control": "no-store"},
        # 送信後に一時ファイルを消す。消せなくても配信自体は済んでいるので無視する。
        background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
    )


# ---------------------------------------------------------------------------
# 画面
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    """`python -m web.app` で起動する。

    Railway をはじめ PaaS は待ち受けポートを環境変数 PORT で渡してくるので、
    ポートをコードに固定しない。リバースプロキシの背後に置かれるため、
    X-Forwarded-* を信頼する設定にしておく。
    """
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
