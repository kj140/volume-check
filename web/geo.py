"""地図まわりの計算。

地図上で描いた矩形から間口・奥行を求める。矩形は Leaflet の
「南西・北東」で表される緯度経度の軸平行矩形なので、辺は必ず
東西方向と南北方向を向く。前面道路の方位でどちらが間口かが決まる。
"""

from __future__ import annotations

import math

# WGS84 楕円体
_A = 6378137.0                       # 長半径[m]
_F = 1.0 / 298.257223563             # 扁平率
_E2 = _F * (2.0 - _F)                # 第一離心率の2乗


def _meridian_radius_m(lat_deg: float) -> float:
    """子午線曲率半径 M(φ)。"""
    s2 = math.sin(math.radians(lat_deg)) ** 2
    return _A * (1.0 - _E2) / (1.0 - _E2 * s2) ** 1.5


def _prime_vertical_radius_m(lat_deg: float) -> float:
    """卯酉線曲率半径 N(φ)。"""
    s2 = math.sin(math.radians(lat_deg)) ** 2
    return _A / math.sqrt(1.0 - _E2 * s2)


def meridian_distance_m(lat1: float, lat2: float) -> float:
    """同一経度上の南北距離[m]。

    敷地は数百m以内なので、中央緯度の曲率半径を使う近似で十分（誤差はmm未満）。
    球近似だと緯度35度で 0.3% ほど過大になるため、楕円体で計算する。
    """
    mid = (lat1 + lat2) / 2.0
    return _meridian_radius_m(mid) * math.radians(abs(lat2 - lat1))


def parallel_distance_m(lat: float, lon1: float, lon2: float) -> float:
    """同一緯度上の東西距離[m]。"""
    return (
        _prime_vertical_radius_m(lat)
        * math.cos(math.radians(lat))
        * math.radians(abs(lon2 - lon1))
    )


def rect_size_m(south: float, west: float, north: float, east: float) -> tuple[float, float]:
    """軸平行矩形の (東西方向の長さ, 南北方向の長さ)[m]。

    東西方向は緯度によって長さが変わるので、矩形の中央緯度で測る。
    """
    mid_lat = (south + north) / 2.0
    return parallel_distance_m(mid_lat, west, east), meridian_distance_m(south, north)


def frontage_depth_m(
    south: float, west: float, north: float, east: float, road_side: str
) -> tuple[float, float]:
    """矩形と前面道路の方位から (間口, 奥行)[m] を返す。

    間口 = 道路に接する辺なので、南北道路なら東西方向の辺、
    東西道路なら南北方向の辺が間口になる。
    """
    ew, ns = rect_size_m(south, west, north, east)
    if road_side in ("south", "north"):
        return ew, ns
    return ns, ew


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """経緯度を XYZ タイル座標に変換する（Web メルカトル）。"""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    """点が閉じたリング（[[lon, lat], ...]）の内側にあるか。Ray casting。"""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x_at = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_at:
                inside = not inside
    return inside


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    """GeoJSON の Polygon / MultiPolygon に点が含まれるか（穴も考慮）。"""
    gtype = geometry.get("type")
    if gtype == "Polygon":
        polygons = [geometry.get("coordinates") or []]
    elif gtype == "MultiPolygon":
        polygons = geometry.get("coordinates") or []
    else:
        return False
    for rings in polygons:
        if not rings:
            continue
        if point_in_ring(lon, lat, rings[0]):
            if not any(point_in_ring(lon, lat, hole) for hole in rings[1:]):
                return True
    return False
