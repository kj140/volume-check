"""外部 API をテストから切り離す（M00 作業5）。

web/zoning.py（不動産情報ライブラリ）と web/app.py の /api/geocode（国土地理院）は
httpx で外に出る。ここでは httpx.AsyncClient.get を tests/fixtures/ の記録済み応答に
差し替え、ネットワークなしで同じ結果になることを固定する
（tests/regression/zoning_lookup_offline/request.json → result.json）。

conftest.py のネットワーク遮断が効いていること（差し替えなしなら失敗すること）も
確かめる。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import regress  # noqa: E402
from web import zoning  # noqa: E402

CASE = regress.EXTERNAL_CASE_DIR


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_external_api_results_match_recorded():
    spec = regress.load_json(CASE / "request.json")
    expected = regress.load_json(CASE / "result.json")
    actual = regress.run_external_api_case(spec)
    diffs = regress.compare(expected, actual)
    assert not diffs, "\n".join(diffs)


@pytest.mark.anyio
async def test_all_five_layers_are_served_from_fixtures(monkeypatch):
    spec = regress.load_json(CASE / "request.json")
    monkeypatch.setenv("REINFOLIB_API_KEY", spec["api_key"])
    calls: list[str] = []
    with regress.recorded_http(calls):
        conditions = await zoning.lookup(spec["lon"], spec["lat"])
    assert len(calls) == 5
    assert conditions.zoning.source == "reinfolib"
    assert conditions.zoning.district == "商業地域"


@pytest.mark.anyio
async def test_the_network_guard_really_blocks(monkeypatch):
    """差し替えなしで外に出ようとすると、conftest の遮断で止まる。"""
    spec = regress.load_json(CASE / "request.json")
    monkeypatch.setenv("REINFOLIB_API_KEY", spec["api_key"])
    with pytest.raises(RuntimeError, match="ネットワーク|名前解決"):
        await zoning.lookup(spec["lon"], spec["lat"])


@pytest.mark.anyio
async def test_lookup_without_key_and_data_does_not_touch_the_network(monkeypatch):
    """キーもローカルデータもなければ、外に出ずに「不明」を返す。"""
    spec = regress.load_json(CASE / "request.json")
    monkeypatch.setattr(zoning, "DATA_DIR", Path("does-not-exist"))
    conditions = await zoning.lookup(spec["lon"], spec["lat"])
    assert conditions.zoning.source == "none"
