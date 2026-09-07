"""緯度経度から用途地域・建蔽率・容積率を推定する。

データ源は 2 つ。上から順に試し、当たらなければ「不明」を返して
UI 側の手入力にフォールバックする（推定できないまま計算を止めない）。

1. 国土交通省「不動産情報ライブラリ」API の
   都市計画決定GISデータ（用途地域）XKT002
     GET https://www.reinfolib.mlit.go.jp/ex-api/external/XKT002
         ?response_format=geojson&z={z}&x={x}&y={y}
     ヘッダ Ocp-Apim-Subscription-Key: <APIキー>
   ズームレベルは 11〜15。無料だが事前のAPIキー取得が必要なため、
   環境変数 REINFOLIB_API_KEY が設定されているときだけ使う。

2. ローカルに置いた GeoJSON（web/data/*.geojson）。
   国土数値情報「用途地域データ(A29)」を GeoJSON に変換したものを想定。
   APIキーなしで動かしたい場合はこちらを使う。

いずれも「その座標を含むポリゴンを探して属性を読む」だけなので、
幾何ライブラリは使わず geo.point_in_geometry で判定する。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from . import geo

REINFOLIB_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external/XKT002"
REINFOLIB_ZOOM = 15          # 11(市)〜15(詳細)。企画検討では最詳細でよい
REQUEST_TIMEOUT_S = 10.0

DATA_DIR = Path(__file__).resolve().parent / "data"

# 国土数値情報 用途地域コード（A29_004 / reinfolib の youto_id 共通）
YOUTO_ID_TO_DISTRICT: dict[int, str] = {
    1: "第一種低層住居専用地域",
    2: "第二種低層住居専用地域",
    3: "第一種中高層住居専用地域",
    4: "第二種中高層住居専用地域",
    5: "第一種住居地域",
    6: "第二種住居地域",
    7: "準住居地域",
    8: "近隣商業地域",
    9: "商業地域",
    10: "準工業地域",
    11: "工業地域",
    12: "工業専用地域",
    21: "田園住居地域",
}

# 属性名のゆらぎを吸収する（reinfolib / 国土数値情報 A29 / 変換ツールの差）
_DISTRICT_KEYS = ("youto_id", "A29_004", "a29_004", "用途地域コード")
_DISTRICT_NAME_KEYS = ("u_youto_ja", "A29_005", "a29_005", "用途地域", "youto")
_BCR_KEYS = ("u_building_coverage_ratio_ja", "A29_006", "a29_006", "建蔽率")
_FAR_KEYS = ("u_floor_area_ratio_ja", "A29_007", "a29_007", "容積率")


@dataclass(frozen=True)
class ZoningGuess:
    """推定結果。値が取れなかった項目は None。"""

    district: str | None
    bcr: float | None            # 倍率表記（0.8 = 80%）
    far: float | None            # 倍率表記（6.0 = 600%）
    source: str                  # "reinfolib" / "local" / "none"
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


NOT_FOUND = ZoningGuess(None, None, None, "none", "用途地域を判定できませんでした")


def _first(props: dict, keys) -> object | None:
    for k in keys:
        if k in props and props[k] not in (None, ""):
            return props[k]
    return None


def _ratio(value: object) -> float | None:
    """"80%" / "80" / 80 を 0.8 に、"300%" を 3.0 にする。"""
    if value is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(value))
    if not m:
        return None
    return float(m.group()) / 100.0


def _district(props: dict) -> str | None:
    code = _first(props, _DISTRICT_KEYS)
    if code is not None:
        try:
            return YOUTO_ID_TO_DISTRICT.get(int(code))
        except (TypeError, ValueError):
            pass
    name = _first(props, _DISTRICT_NAME_KEYS)
    if name is None:
        return None
    name = str(name).strip()
    return name if name in YOUTO_ID_TO_DISTRICT.values() else None


def _from_features(lon: float, lat: float, features: list, source: str) -> ZoningGuess | None:
    for feature in features:
        geometry = feature.get("geometry") or {}
        if not geo.point_in_geometry(lon, lat, geometry):
            continue
        props = feature.get("properties") or {}
        district = _district(props)
        if district is None:
            continue
        return ZoningGuess(
            district=district,
            bcr=_ratio(_first(props, _BCR_KEYS)),
            far=_ratio(_first(props, _FAR_KEYS)),
            source=source,
        )
    return None


async def _from_reinfolib(lon: float, lat: float, api_key: str) -> ZoningGuess | None:
    x, y = geo.lonlat_to_tile(lon, lat, REINFOLIB_ZOOM)
    params = {"response_format": "geojson", "z": REINFOLIB_ZOOM, "x": x, "y": y}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        res = await client.get(
            REINFOLIB_URL, params=params, headers={"Ocp-Apim-Subscription-Key": api_key}
        )
    res.raise_for_status()
    return _from_features(lon, lat, (res.json() or {}).get("features") or [], "reinfolib")


def _from_local(lon: float, lat: float) -> ZoningGuess | None:
    if not DATA_DIR.is_dir():
        return None
    for path in sorted(DATA_DIR.glob("*.geojson")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        found = _from_features(lon, lat, data.get("features") or [], "local")
        if found is not None:
            return found
    return None


def local_dataset_names() -> list[str]:
    """配置済みのローカル用途地域データのファイル名。"""
    if not DATA_DIR.is_dir():
        return []
    return [p.name for p in sorted(DATA_DIR.glob("*.geojson"))]


async def lookup(lon: float, lat: float) -> ZoningGuess:
    """用途地域を推定する。失敗しても例外は投げず NOT_FOUND 相当を返す。"""
    api_key = os.environ.get("REINFOLIB_API_KEY", "").strip()
    if api_key:
        try:
            found = await _from_reinfolib(lon, lat, api_key)
            if found is not None:
                return found
        except (httpx.HTTPError, ValueError, KeyError) as e:
            # APIが落ちていてもローカル・手入力に落とせるので止めない
            local = _from_local(lon, lat)
            if local is not None:
                return local
            return ZoningGuess(None, None, None, "none", f"APIの参照に失敗: {e}")

    local = _from_local(lon, lat)
    if local is not None:
        return local

    if not api_key and not local_dataset_names():
        return ZoningGuess(
            None, None, None, "none",
            "自動判定のデータがありません。環境変数 REINFOLIB_API_KEY を設定するか、"
            "web/data/ に用途地域のGeoJSONを置いてください。手入力でも計算できます。",
        )
    return NOT_FOUND
