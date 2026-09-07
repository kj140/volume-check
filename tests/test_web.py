"""Web層（FastAPI / SVG / 地図まわり）の検証。

Web層は既存の solver / drawer を呼ぶだけの薄い層なので、
「既存の計算結果と一致すること」と「壊れた入力を弾くこと」を中心に見る。
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
import pytest
from ezdxf.audit import Auditor
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import section as S  # noqa: E402
from models import VolumeInput  # noqa: E402
from solver import solve  # noqa: E402
from web import geo, zoning  # noqa: E402
from web.app import app  # noqa: E402
from web.svg_section import render as render_svg  # noqa: E402

SAMPLES = _ROOT / "samples"
client = TestClient(app)


def payload(name: str = "case_road12") -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# /api/solve — 既存の solver と一致すること
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample", ["case_road6", "case_road12"])
def test_solve_matches_the_library(sample):
    body = payload(sample)
    expected = solve(VolumeInput.from_dict(body))

    res = client.post("/api/solve", json=body)
    assert res.status_code == 200
    data = res.json()
    s = data["summary"]

    assert s["floor_count"] == expected.floor_count
    assert s["max_height_m"] == pytest.approx(expected.max_height_mm / 1000, abs=0.005)
    assert s["total_far_area_m2"] == pytest.approx(
        expected.total_far_area_mm2 / 1e6, abs=0.005
    )
    assert s["far_effective"] == pytest.approx(expected.far_effective)
    assert s["stop_reason"] == expected.stop_reason.value
    assert len(data["floors"]) == expected.floor_count
    assert data["applied_rules"] and data["notes"]


def test_solve_area_table_is_cumulative():
    data = client.post("/api/solve", json=payload()).json()
    total = 0.0
    for row in data["floors"]:
        total += row["far_area_m2"]
        assert row["cumulative_far_area_m2"] == pytest.approx(total, abs=0.02)


def test_solve_zero_floors_is_not_an_error():
    body = payload()
    body["site"]["frontage"] = 8.0
    body["site"]["depth"] = 10.0
    res = client.post("/api/solve", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["summary"]["floor_count"] == 0
    assert data["summary"]["stop_reason"]
    assert "成立しません" in data["svg"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["zoning"].__setitem__("bcr", 1.5),          # 建蔽率 > 100%
        lambda b: b["zoning"].__setitem__("use_district", "架空地域"),
        lambda b: b["site"].__setitem__("frontage", -1),
        lambda b: b["site"].__setitem__("road_side", "northwest"),
        lambda b: b["program"].__setitem__("core_ratio", 1.0),
        lambda b: b["program"].__setitem__("max_floors", 0),
    ],
)
def test_solve_rejects_invalid_input(mutate):
    body = payload()
    mutate(body)
    assert client.post("/api/solve", json=body).status_code == 422


# ---------------------------------------------------------------------------
# /api/dxf — 既存の drawer が生成したものがそのまま返ること
# ---------------------------------------------------------------------------


def test_dxf_download(tmp_path):
    res = client.post("/api/dxf", json=payload())
    assert res.status_code == 200
    assert "volume_check.dxf" in res.headers["content-disposition"]

    path = tmp_path / "out.dxf"
    path.write_bytes(res.content)
    doc = ezdxf.readfile(path)
    auditor = Auditor(doc)
    auditor.run()
    assert not auditor.errors

    expected = solve(VolumeInput.from_dict(payload()))
    msp = doc.modelspace()
    # 配置図の1階・最上階 + 断面図の各階
    assert len(msp.query("LWPOLYLINE[layer=='A-OUTL']")) == expected.floor_count + 2
    assert any("確認申請には使用できません" in m.text for m in msp.query("MTEXT"))


def test_dxf_rejects_invalid_input():
    body = payload()
    body["zoning"]["far_designated"] = -3
    assert client.post("/api/dxf", json=body).status_code == 422


# ---------------------------------------------------------------------------
# 断面図SVG — DXFと同じ幾何（section.py）から作られていること
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample", ["case_road6", "case_road12"])
def test_svg_is_wellformed_and_matches_section_geometry(sample):
    result = solve(VolumeInput.from_json_file(SAMPLES / f"{sample}.json"))
    svg = render_svg(result)

    root = ET.fromstring(svg)          # 壊れたSVGならここで例外
    assert root.tag.endswith("svg")

    ns = "{http://www.w3.org/2000/svg}"
    rects = root.findall(f"{ns}rect")
    # 背景1枚 + 各階
    assert len(rects) == result.floor_count + 1

    geom = S.build(result)
    polylines = root.findall(f"{ns}polyline")
    expected_polylines = 1 + (1 if geom.neighbor_slant is not None else 0)
    assert len(polylines) == expected_polylines

    texts = [t.text for t in root.iter(f"{ns}text")]
    assert "GL±0" in texts
    assert f"道路 W={result.input.site.road_width_mm / 1000:.1f}m" in texts
    assert any("最高高さ" in t for t in texts if t)
    for f in result.floors:
        assert any(t and t.startswith(f"{f.floor}FL") for t in texts)


def test_svg_shows_absolute_height_limit_and_no_neighbor_slant():
    body = payload()
    body["zoning"].update(
        use_district="第一種低層住居専用地域", far_designated=2.0, bcr=0.5,
        height_limit_absolute=None,
    )
    body["program"].update(floor_height=3.0, gf_height=3.2)
    result = solve(VolumeInput.from_dict(body))
    svg = render_svg(result)
    ET.fromstring(svg)
    assert "絶対高さ制限 10.0m" in svg
    assert "隣地斜線：適用なし" in svg


def test_svg_escapes_text():
    """ラベルにXMLの特殊文字が混ざっても壊れない。"""
    result = solve(VolumeInput.from_dict(payload()))
    object.__setattr__(result.floors[0], "governing", '道路斜線 & <試験>')
    ET.fromstring(render_svg(result))


# ---------------------------------------------------------------------------
# 地図まわり
# ---------------------------------------------------------------------------


def test_meridian_distance_matches_wgs84():
    """緯度35.5度付近の子午線長は 1度あたり約 110,950m（WGS84）。"""
    assert geo.meridian_distance_m(35.0, 36.0) == pytest.approx(110_950, abs=30)


def test_parallel_distance_matches_wgs84():
    """緯度35.68度の東西方向は 1度あたり約 90,530m。"""
    assert geo.parallel_distance_m(35.68, 139.0, 140.0) == pytest.approx(90_530, abs=60)


def test_parallel_distance_shrinks_toward_the_pole():
    at35 = geo.parallel_distance_m(35.0, 139.0, 139.01)
    at60 = geo.parallel_distance_m(60.0, 139.0, 139.01)
    assert at60 < at35


def test_rect_endpoint_swaps_frontage_by_road_side():
    """道路の方位で間口と奥行が入れ替わる。"""
    rect = {"south": 35.6800, "west": 139.7650, "north": 35.6803, "east": 139.7654}

    south = client.post("/api/rect", json={**rect, "road_side": "south"}).json()
    east = client.post("/api/rect", json={**rect, "road_side": "east"}).json()

    assert south["frontage"] == pytest.approx(east["depth"])
    assert south["depth"] == pytest.approx(east["frontage"])
    assert south["area_m2"] == pytest.approx(east["area_m2"])
    # 緯度0.0003度 ≒ 33.4m
    assert south["depth"] == pytest.approx(33.4, abs=0.5)
    assert south["center"]["lat"] == pytest.approx(35.68015)


def test_rect_endpoint_rejects_out_of_range():
    bad = {"south": 95.0, "west": 139.0, "north": 96.0, "east": 140.0}
    assert client.post("/api/rect", json=bad).status_code == 422


def test_point_in_geometry_handles_holes():
    ring = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    poly = {"type": "Polygon", "coordinates": [ring, hole]}
    assert geo.point_in_geometry(1, 1, poly)
    assert not geo.point_in_geometry(5, 5, poly)      # 穴の中
    assert not geo.point_in_geometry(20, 5, poly)


def test_lonlat_to_tile_matches_known_tile():
    """東京駅（z=15）のタイル座標。"""
    assert geo.lonlat_to_tile(139.767, 35.681, 15) == (29105, 12903)


# ---------------------------------------------------------------------------
# 用途地域の判定
# ---------------------------------------------------------------------------


def test_zoning_parses_reinfolib_properties():
    feature = {
        "geometry": {"type": "Polygon",
                     "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "properties": {"youto_id": 9,
                       "u_building_coverage_ratio_ja": "80%",
                       "u_floor_area_ratio_ja": "600%"},
    }
    guess = zoning._from_features(0.5, 0.5, [feature], "reinfolib")
    assert guess.district == "商業地域"
    assert guess.bcr == pytest.approx(0.8)
    assert guess.far == pytest.approx(6.0)


def test_zoning_parses_kokudo_suuchi_properties():
    feature = {
        "geometry": {"type": "Polygon",
                     "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "properties": {"A29_004": "1", "A29_006": "50", "A29_007": "100"},
    }
    guess = zoning._from_features(0.5, 0.5, [feature], "local")
    assert guess.district == "第一種低層住居専用地域"
    assert guess.bcr == pytest.approx(0.5)
    assert guess.far == pytest.approx(1.0)


def test_zoning_ignores_polygon_that_does_not_contain_the_point():
    feature = {
        "geometry": {"type": "Polygon",
                     "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "properties": {"youto_id": 9},
    }
    assert zoning._from_features(5, 5, [feature], "local") is None


@pytest.mark.parametrize("raw,expected", [("80%", 0.8), ("300%", 3.0), (60, 0.6),
                                          ("", None), (None, None), ("なし", None)])
def test_zoning_ratio_parsing(raw, expected):
    assert zoning._ratio(raw) == (None if expected is None else pytest.approx(expected))


@pytest.mark.anyio
async def test_zoning_lookup_falls_back_without_data(monkeypatch):
    """APIキーもローカルデータもなければ、例外ではなく理由付きの「不明」を返す。"""
    monkeypatch.delenv("REINFOLIB_API_KEY", raising=False)
    monkeypatch.setattr(zoning, "DATA_DIR", Path("does-not-exist"))
    guess = await zoning.lookup(139.767, 35.681)
    assert guess.district is None
    assert guess.source == "none"
    assert guess.detail


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_zoning_endpoint_always_returns_200():
    res = client.get("/api/zoning", params={"lat": 35.681, "lon": 139.767})
    assert res.status_code == 200
    assert "source" in res.json()


# ---------------------------------------------------------------------------
# 画面とメタ情報
# ---------------------------------------------------------------------------


def test_index_and_static_are_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "ボリュームチェック図" in res.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_use_districts_endpoint():
    data = client.get("/api/use-districts").json()
    assert "商業地域" in data["districts"]
    assert "第一種低層住居専用地域" in data["absolute_height_limit_required"]
    assert data["default_absolute_height_limit_m"] == 10.0
    assert "zoning_lookup" in data


# ---------------------------------------------------------------------------
# デプロイまわり（Railway の契約）
# ---------------------------------------------------------------------------


def test_healthz_runs_a_real_calculation():
    """死活監視は計算系まで通す。ライブラリが壊れていれば 500 になる。"""
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["probe_floors"] >= 1


def test_main_uses_the_port_environment_variable(monkeypatch):
    """PaaS は待ち受けポートを環境変数 PORT で渡してくる。"""
    import uvicorn

    from web import app as app_module

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda a, **kw: captured.update(kw))

    monkeypatch.setenv("PORT", "12345")
    monkeypatch.delenv("HOST", raising=False)
    app_module.main()
    assert captured["port"] == 12345
    assert captured["host"] == "0.0.0.0"
    # リバースプロキシ配下で動くので X-Forwarded-* を信頼する
    assert captured["proxy_headers"] is True

    monkeypatch.delenv("PORT", raising=False)
    app_module.main()
    assert captured["port"] == 8000


def test_deployment_files_exist_and_are_consistent():
    """Dockerfile / requirements.txt / railway.toml の食い違いを防ぐ。"""
    import tomllib

    root = _ROOT
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    railway = tomllib.loads((root / "railway.toml").read_text(encoding="utf-8"))
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")

    # コメント行を除いた実際の依存指定だけを見る
    specs = [ln.strip() for ln in requirements.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    names = {re.split(r"[\[=<>~!;]", spec)[0].strip() for spec in specs}

    # 実行時に import するものが requirements.txt にある
    assert {"ezdxf", "fastapi", "uvicorn", "httpx"} <= names, names
    # 開発専用のものは入れない
    assert not ({"pytest", "matplotlib"} & names), names
    # すべてバージョン固定（デプロイの再現性のため）
    for spec in specs:
        assert "==" in spec, f"バージョンが固定されていない: {spec}"

    assert "CMD [\"python\", \"-m\", \"web.app\"]" in dockerfile
    assert "USER appuser" in dockerfile, "非rootで動かすこと"

    assert railway["build"]["builder"] == "DOCKERFILE"
    assert railway["deploy"]["healthcheckPath"] == "/healthz"
    # healthcheckPath が実在すること
    assert client.get(railway["deploy"]["healthcheckPath"]).status_code == 200

    for pattern in (".venv/", "__pycache__/", "out/", "tests/"):
        assert pattern in dockerignore, f"{pattern} が .dockerignore にない"
