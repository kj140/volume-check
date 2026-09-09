"""緯度経度から敷地の法規条件を推定する。

データ源は 2 つ。上から順に試し、当たらなければ「不明」を返して
UI 側の手入力にフォールバックする（推定できないまま計算を止めない）。

1. 国土交通省「不動産情報ライブラリ」API（要APIキー・無料）
   都市計画決定GISデータをXYZタイル単位のGeoJSONで返す。
     GET https://www.reinfolib.mlit.go.jp/ex-api/external/{API_ID}
         ?response_format=geojson&z={z}&x={x}&y={y}
     ヘッダ Ocp-Apim-Subscription-Key: <APIキー>
   ズームレベルは 11〜15。環境変数 REINFOLIB_API_KEY があるときだけ使う。

     XKT002  用途地域（建蔽率・容積率つき）
     XKT014  防火・準防火地域
     XKT001  都市計画区域／区域区分
     XKT023  地区計画
     XKT024  高度利用地区

2. ローカルに置いた GeoJSON（web/data/*.geojson）。
   国土数値情報「用途地域データ(A29)」を GeoJSON に変換したものを想定。
   APIキーなしで動かしたい場合はこちら（用途地域のみ）。

いずれも「その座標を含むポリゴンを探して属性を読む」だけなので、
幾何ライブラリは使わず geo.point_in_geometry で判定する。

なお **高度地区**（法58条の高さの最高限度）と **道路幅員・道路種別** は
公開APIに存在しない。自治体の都市計画情報で確認して手入力する必要がある。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from . import geo

REINFOLIB_BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"
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
    """用途地域の推定結果。値が取れなかった項目は None。"""

    district: str | None
    bcr: float | None            # 倍率表記（0.8 = 80%）
    far: float | None            # 倍率表記（6.0 = 600%）
    source: str                  # "reinfolib" / "local" / "none"
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SiteConditions:
    """敷地の法規条件一式。用途地域以外は参考情報として返す。"""

    zoning: ZoningGuess
    fire_zone: str | None = None            # 防火地域 / 準防火地域 など
    area_classification: str | None = None  # 市街化区域 / 市街化調整区域 など
    district_plan: str | None = None        # 地区計画の名称
    high_use_district: str | None = None    # 高度利用地区の名称
    warnings: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["zoning"] = self.zoning.to_dict()
        return d


NOT_FOUND = ZoningGuess(None, None, None, "none", "用途地域を判定できませんでした")

# 公開APIに存在せず、自治体の都市計画情報を見るしかない項目
UNAVAILABLE_ITEMS = (
    "高度地区（法58条の高さの最高限度）",
    "前面道路の幅員・道路種別（法42条の何項か）",
    "日影規制の指定",
)


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
    for props in _properties_at(lon, lat, features):
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


def _properties_at(lon: float, lat: float, features: list) -> list[dict]:
    """その座標を含むポリゴンの属性をすべて返す。"""
    return [
        feature.get("properties") or {}
        for feature in features
        if geo.point_in_geometry(lon, lat, feature.get("geometry") or {})
    ]


# ---------------------------------------------------------------------------
# 不動産情報ライブラリ API
# ---------------------------------------------------------------------------


async def _fetch_layer(client: httpx.AsyncClient, api_id: str, api_key: str,
                       x: int, y: int) -> list:
    """1レイヤ分のタイルを取得して features を返す。失敗時は空。"""
    try:
        res = await client.get(
            f"{REINFOLIB_BASE}/{api_id}",
            params={"response_format": "geojson", "z": REINFOLIB_ZOOM, "x": x, "y": y},
            headers={"Ocp-Apim-Subscription-Key": api_key},
        )
        res.raise_for_status()
        return (res.json() or {}).get("features") or []
    except (httpx.HTTPError, ValueError, KeyError):
        return []


async def _from_reinfolib(lon: float, lat: float, api_key: str) -> SiteConditions | None:
    """用途地域を含む複数レイヤをまとめて引く。用途地域が取れなければ None。"""
    x, y = geo.lonlat_to_tile(lon, lat, REINFOLIB_ZOOM)
    layers = ("XKT002", "XKT014", "XKT001", "XKT023", "XKT024")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        results = await asyncio.gather(
            *(_fetch_layer(client, api_id, api_key, x, y) for api_id in layers)
        )
    youto, fire, area, plan, high_use = results

    zoning = _from_features(lon, lat, youto, "reinfolib")
    if zoning is None:
        return None

    conditions = SiteConditions(zoning=zoning, unavailable=list(UNAVAILABLE_ITEMS))

    for props in _properties_at(lon, lat, fire):
        name = _first(props, ("fire_prevention_ja", "bouka_ja"))
        if name:
            conditions.fire_zone = str(name).strip()
            break

    for props in _properties_at(lon, lat, area):
        name = _first(props, ("area_classification_ja",))
        if name:
            conditions.area_classification = str(name).strip()
            break

    for props in _properties_at(lon, lat, plan):
        name = _first(props, ("district_plan_name_ja", "plan_name_ja", "name_ja", "名称"))
        conditions.district_plan = str(name).strip() if name else "指定あり（名称不明）"
        break

    for props in _properties_at(lon, lat, high_use):
        name = _first(props, ("kodo_riyou_ja", "name_ja", "名称"))
        conditions.high_use_district = str(name).strip() if name else "指定あり（名称不明）"
        break

    _add_warnings(conditions)
    return conditions


def _add_warnings(c: SiteConditions) -> None:
    """算定に影響するが本ツールが扱わない指定を警告として積む。"""
    if c.area_classification and "調整" in c.area_classification:
        c.warnings.append(
            f"{c.area_classification}です。原則として建築できません（都市計画法43条）。"
            "開発許可等の可否を確認してください。"
        )
    if c.district_plan:
        c.warnings.append(
            f"地区計画（{c.district_plan}）の区域内です。高さ・壁面位置・用途の制限が"
            "別途定められている可能性があります。本ツールは未考慮です。"
        )
    if c.high_use_district:
        c.warnings.append(
            f"高度利用地区（{c.high_use_district}）の区域内です。容積率の最高・最低限度、"
            "建築面積の最低限度などが定められています。本ツールは未考慮です。"
        )
    if c.fire_zone and "防火" in c.fire_zone:
        c.warnings.append(
            f"{c.fire_zone}です。耐火建築物等とすれば建蔽率が10%緩和されます"
            "（法53条3項1号イ）。適用する場合は「建蔽率の緩和」を有効にしてください。"
        )


# ---------------------------------------------------------------------------
# ローカル GeoJSON
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------


async def lookup(lon: float, lat: float) -> SiteConditions:
    """敷地の法規条件を推定する。失敗しても例外は投げない。"""
    api_key = os.environ.get("REINFOLIB_API_KEY", "").strip()

    if api_key:
        found = await _from_reinfolib(lon, lat, api_key)
        if found is not None:
            return found

    local = _from_local(lon, lat)
    if local is not None:
        return SiteConditions(zoning=local, unavailable=list(UNAVAILABLE_ITEMS))

    if not api_key and not local_dataset_names():
        return SiteConditions(zoning=ZoningGuess(
            None, None, None, "none",
            "自動判定のデータがありません。環境変数 REINFOLIB_API_KEY を設定するか、"
            "web/data/ に用途地域のGeoJSONを置いてください。手入力でも計算できます。",
        ))
    return SiteConditions(zoning=NOT_FOUND)
