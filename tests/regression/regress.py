"""回帰ケースの実行・保存・比較（M00）。

1 ケース = tests/regression/<ケース名>/ の 3 ファイル。

  input.json        入力。"volume" が solve() に渡す入力（samples/*.json と同じ形）。
                    "sky": true なら skyfactor.evaluate() も実行する。
                    "study": {...} があれば studies.generate() も実行する
                    （キーは generate() の引数名。角度などのリストはタプルにして渡す）。
  result.json       算定結果の全体。数値は丸めずに保存する（models.to_dict() は
                    表示用に 3 桁へ丸めるので使わない）。
  dxf_summary.json  DXF から取り出した内容。レイヤごとの図形数、文字（並べ替え）、
                    線の端点（小数 3 桁）など。バイト比較はしない。

比較は compare() で行う。浮動小数は相対誤差 1e-9 以内を一致とみなす
（0 付近の値のために絶対誤差 1e-9 も許す。mm 系なので実質ゼロ）。

保存（初回・意図した更新）は record.py から行う。テストは保存しない。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest import mock

import ezdxf
import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import skyfactor  # noqa: E402
import studies  # noqa: E402
from drawer import draw  # noqa: E402
from models import VolumeInput, VolumeResult  # noqa: E402
from solver import solve  # noqa: E402
from web import zoning  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "tests" / "fixtures"
EXTERNAL_CASE_DIR = CASES_DIR / "zoning_lookup_offline"
REL_TOL = 1e-9
ABS_TOL = 1e-9

# 表題欄の「作成日時」の値。drawer.py が実行時刻を書き込むため比較から外す
# （本体コードは変えない。M00 の判断を参照）。
_DATETIME_TEXT = re.compile(r"^: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ---------------------------------------------------------------------------
# ケースの列挙
# ---------------------------------------------------------------------------


def case_dirs() -> list[Path]:
    """input.json を持つケースのディレクトリ（名前順）。"""
    return sorted(p.parent for p in CASES_DIR.glob("*/input.json"))


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# 結果の直列化（丸めない）
# ---------------------------------------------------------------------------


def _pts(outline) -> list[list[float]]:
    return [[float(x), float(y)] for x, y in outline]


def volume_to_dict(r: VolumeResult) -> dict:
    """VolumeResult の全項目。長さは mm、面積は mm² のまま。"""
    ratio = r.input.program.core_ratio
    return {
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
        "bcr_relaxations": [[d, b] for d, b in r.bcr_relaxations],
        "building_angle_rad": r.building_angle_rad,
        "building_angle_deg": r.building_angle_deg,
        "footprint": _pts(r.footprint),
        "bcr_inset_mm": r.bcr_inset_mm,
        "floors": [
            {
                "floor": f.floor,
                "level_mm": f.level_mm,
                "story_height_mm": f.story_height_mm,
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
                "impacts": [
                    {"constraint": i.constraint.value,
                     "area_gain_mm2": i.area_gain_mm2,
                     "setback_mm": i.setback_mm}
                    for i in f.impacts
                ],
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
        "floor_count": r.floor_count,
        "total_gross_area_mm2": r.total_gross_area_mm2,
        "total_far_area_mm2": r.total_far_area_mm2,
        "total_rentable_area_mm2": r.total_rentable_area_mm2,
        "building_area_mm2": r.building_area_mm2,
        "max_height_mm": r.max_height_mm,
        "achieved_far": r.achieved_far,
        "achieved_bcr": r.achieved_bcr,
        "total_area_loss_mm2": r.total_area_loss_mm2,
        # 規定ごとの削減量（増分の大きい順。順序も固定する）
        "constraint_gains_mm2": [[c.value, g] for c, g in r.constraint_gains_mm2().items()],
        "dominant_constraint": r.dominant_constraint.value if r.dominant_constraint else None,
        "stop_reason": r.stop_reason.value if r.stop_reason else None,
        "stop_detail": r.stop_detail,
        "applied_rules": [
            {"label": a.label, "value": a.value, "basis": a.basis} for a in r.applied_rules
        ],
        "notes": list(r.notes),
    }


def sky_to_dict(s: skyfactor.SkyFactorStudy) -> dict:
    """SkyFactorStudy の全項目（算定位置ごとの天空率を含む）。"""
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
                "points": [
                    {"position": [float(p.position[0]), float(p.position[1])],
                     "planned": p.planned,
                     "compliant": p.compliant,
                     "margin": p.margin,
                     "passes": p.passes}
                    for p in e.points
                ],
            }
            for e in s.edges
        ],
        "notes": list(s.notes),
    }


def study_to_dict(st: studies.StudyResult, sweep: list[tuple[float, float]]) -> dict:
    """StudyResult の全項目。案の並び順もそのまま固定する。"""
    def case(c):
        d = asdict(c)                      # sky（SkyVerdict）も入れ子で展開される
        return d

    return {
        "cases": [case(c) for c in st.cases],
        "baseline": None if st.baseline is None else case(st.baseline),
        "best": None if st.best is None else case(st.best),
        "sky_suggestion": None if st.sky_suggestion is None else {
            **{k: v for k, v in asdict(st.sky_suggestion).items() if k != "outline"},
            "outline": _pts(st.sky_suggestion.outline),
        },
        "notes": list(st.notes),
        "angle_sweep": [[a, area] for a, area in sweep],
    }


# ---------------------------------------------------------------------------
# DXF の要約
# ---------------------------------------------------------------------------


def _r(v: float) -> float:
    return round(float(v), 3)


def dxf_summary(path: Path) -> dict:
    """DXF から比較に使う内容だけを取り出す。座標は小数 3 桁に丸める。"""
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()

    counts = Counter((e.dxf.layer, e.dxftype()) for e in msp)
    texts, mtexts, lines, polylines, circles, dims = [], [], [], [], [], []
    for e in msp:
        kind = e.dxftype()
        if kind == "TEXT":
            if not _DATETIME_TEXT.match(e.dxf.text):
                texts.append([e.dxf.layer, e.dxf.text])
        elif kind == "MTEXT":
            mtexts.append([e.dxf.layer, e.text])
        elif kind == "LINE":
            s, t = e.dxf.start, e.dxf.end
            lines.append([e.dxf.layer, e.dxf.get("linetype", "BYLAYER"),
                          _r(s.x), _r(s.y), _r(t.x), _r(t.y)])
        elif kind == "LWPOLYLINE":
            polylines.append([e.dxf.layer, e.dxf.get("linetype", "BYLAYER"), bool(e.closed),
                              [[_r(x), _r(y)] for x, y, *_ in e.get_points()]])
        elif kind == "CIRCLE":
            c = e.dxf.center
            circles.append([e.dxf.layer, _r(c.x), _r(c.y), _r(e.dxf.radius)])
        elif kind == "DIMENSION":
            dims.append([e.dxf.layer, e.dxf.text,
                         [_r(e.dxf.defpoint.x), _r(e.dxf.defpoint.y)],
                         [_r(e.dxf.defpoint2.x), _r(e.dxf.defpoint2.y)],
                         [_r(e.dxf.defpoint3.x), _r(e.dxf.defpoint3.y)]])

    return {
        "header": {
            "$INSUNITS": doc.header["$INSUNITS"],
            "$MEASUREMENT": doc.header["$MEASUREMENT"],
            "$LTSCALE": doc.header["$LTSCALE"],
        },
        "layers": {
            layer.dxf.name: {
                "color": layer.color,
                "linetype": layer.dxf.linetype,
                "lineweight": layer.dxf.lineweight,
                "description": layer.description,
            }
            for layer in sorted(doc.layers, key=lambda l: l.dxf.name)
            if layer.dxf.name != "0"
        },
        "linetypes": sorted(lt.dxf.name for lt in doc.linetypes),
        "styles": sorted(s.dxf.name for s in doc.styles),
        "extents": {
            "extmin": [_r(msp.dxf.extmin.x), _r(msp.dxf.extmin.y)],
            "extmax": [_r(msp.dxf.extmax.x), _r(msp.dxf.extmax.y)],
        },
        "counts": {f"{layer}/{kind}": n for (layer, kind), n in sorted(counts.items())},
        "texts": sorted(texts),
        "mtexts": sorted(mtexts),
        "lines": sorted(lines),
        "polylines": sorted(polylines, key=json.dumps),
        "circles": sorted(circles),
        "dimensions": sorted(dims, key=json.dumps),
    }


# ---------------------------------------------------------------------------
# ケースの実行
# ---------------------------------------------------------------------------


def _study_kwargs(spec: dict) -> dict:
    kwargs = dict(spec)
    for key in ("angles_deg", "floor_heights_m", "wall_setbacks_m", "fireproof_options"):
        if key in kwargs:
            kwargs[key] = tuple(kwargs[key])
    return kwargs


def run_case(spec: dict, dxf_path: Path) -> tuple[dict, dict]:
    """1 ケースを実行し、(result, dxf_summary) を返す。"""
    volume_input = spec["volume"]
    r = solve(VolumeInput.from_dict(volume_input))
    result: dict = {"volume": volume_to_dict(r)}

    if spec.get("sky"):
        result["sky"] = sky_to_dict(skyfactor.evaluate(r))

    if spec.get("study") is not None:
        kwargs = _study_kwargs(spec["study"])
        st = studies.generate(volume_input, **kwargs)
        angles = kwargs.get("angles_deg", studies.DEFAULT_ANGLES_DEG)
        result["study"] = study_to_dict(st, studies.sweep_angles(volume_input, angles))

    draw(r, dxf_path)
    return result, dxf_summary(dxf_path)


# ---------------------------------------------------------------------------
# 比較
# ---------------------------------------------------------------------------


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compare(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """expected と actual の差分を人が読める行で返す。空なら一致。"""
    diffs: list[str] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: 期待は辞書、実際は {type(actual).__name__}"]
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                diffs.append(f"{path}.{key}: 実際の結果にない")
            elif key not in expected:
                diffs.append(f"{path}.{key}: 保存した結果にない（新しい項目）")
            else:
                diffs.extend(compare(expected[key], actual[key], f"{path}.{key}"))
        return diffs
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: 期待はリスト、実際は {type(actual).__name__}"]
        if len(expected) != len(actual):
            return [f"{path}: 要素数 期待 {len(expected)} / 実際 {len(actual)}"]
        for i, (e, a) in enumerate(zip(expected, actual)):
            diffs.extend(compare(e, a, f"{path}[{i}]"))
        return diffs
    if _is_number(expected) and _is_number(actual):
        if not math.isclose(expected, actual, rel_tol=REL_TOL, abs_tol=ABS_TOL):
            diffs.append(f"{path}: 期待 {expected!r} / 実際 {actual!r}")
        return diffs
    if expected != actual or type(expected) is not type(actual):
        diffs.append(f"{path}: 期待 {expected!r} / 実際 {actual!r}")
    return diffs


# ---------------------------------------------------------------------------
# 外部 API（記録済み応答で置き換える）
# ---------------------------------------------------------------------------


def fixture_response(url: str) -> httpx.Response:
    """URL に対応する記録済み応答（tests/fixtures/）。知らない URL は 404。"""
    from web.app import GSI_ADDRESS_SEARCH

    request = httpx.Request("GET", url)
    if url.startswith(zoning.REINFOLIB_BASE + "/"):
        path = FIXTURES_DIR / "reinfolib" / f"{url.rsplit('/', 1)[-1]}.json"
    elif url == GSI_ADDRESS_SEARCH:
        path = FIXTURES_DIR / "gsi_address_search.json"
    else:
        return httpx.Response(404, request=request)
    if not path.exists():
        return httpx.Response(404, request=request)
    return httpx.Response(200, json=load_json(path), request=request)


def recorded_http(calls: list[str] | None = None):
    """httpx.AsyncClient.get を記録済み応答に差し替えるコンテキスト。"""
    async def fake_get(self, url, *args, **kwargs):
        if calls is not None:
            calls.append(str(url))
        return fixture_response(str(url))

    return mock.patch.object(httpx.AsyncClient, "get", fake_get)


def run_external_api_case(spec: dict) -> dict:
    """用途地域の取得と住所検索を、記録済み応答で実行した結果。"""
    from fastapi.testclient import TestClient
    from web.app import app

    lon, lat = spec["lon"], spec["lat"]
    with recorded_http(), mock.patch.dict(os.environ, {"REINFOLIB_API_KEY": spec["api_key"]}):
        conditions = asyncio.run(zoning.lookup(lon, lat))
        client = TestClient(app)
        geocode = client.get("/api/geocode", params={"q": spec["geocode_query"]}).json()
        endpoint = client.get("/api/zoning", params={"lat": lat, "lon": lon}).json()
    return {"zoning_lookup": conditions.to_dict(), "zoning_endpoint": endpoint,
            "geocode": geocode}
