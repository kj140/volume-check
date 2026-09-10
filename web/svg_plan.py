"""配置図・各階平面図の SVG。

形は plan.build() が返す幾何をそのまま描く。DXF（drawer.py）と同じモジュールを
使うので、画面と出力図面はずれない。方位による回転も plan.Frame に集約している。

SVG は Y 軸が下向きなので、Frame（Y 上向き）の結果を最後に上下反転させる。
各階の色は断面図と同じ「その階を最も削っている規定」で塗り分ける。
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

import constants as C
import plan as P
from models import VolumeResult

from .svg_section import CONSTRAINT_COLORS, NO_CONSTRAINT_LEGEND

MM = C.M_TO_MM
M2 = C.M2_TO_MM2

SITE_VIEW_W = 460.0        # 配置図の論理幅[px]
SITE_PAD = 46.0            # 寸法線・方位記号のための余白
TILE_W = 148.0             # 各階平面図 1 枚の幅[px]
TILE_PAD = 12.0
TILE_LABEL_H = 30.0

COLORS = {
    "bg": "#ffffff",
    "site": "#0f172a",
    "road": "#e2e8f0",
    "road_line": "#94a3b8",
    "ghost": "#cbd5e1",
    "dim": "#16a34a",
    "text": "#0f172a",
    "muted": "#64748b",
}

def _esc(s: object) -> str:
    return escape(str(s))


class _Canvas:
    """Frame（Y上向き）の結果を SVG（Y下向き）へ写す。"""

    def __init__(self, frame: P.Frame, width: float, height: float, scale: float):
        self.frame = frame
        self.width = width
        self.height = height
        self.scale = scale
        self.parts: list[str] = []

    def pt(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        fx, fy = self.frame(x_mm, y_mm)
        return (fx * self.scale, self.height - fy * self.scale)

    def polygon_local(self, outline, fill, stroke, width=1.2, dash="", cls=""):
        """敷地ローカル座標の頂点列を多角形として描く。"""
        if not outline:
            return
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in (self.pt(*q) for q in outline))
        d = f' stroke-dasharray="{dash}"' if dash else ""
        k = f' class="{cls}"' if cls else ""
        self.parts.append(
            f'<polygon{k} points="{pts}" fill="{fill}" stroke="{stroke}"'
            f' stroke-width="{width}"{d}/>'
        )

    def bbox_local(self, outline) -> tuple[float, float, float, float]:
        pts = [self.pt(*q) for q in outline]
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def line_local(self, x0, y0, x1, y1, stroke, width=1.0, dash=""):
        p0, p1 = self.pt(x0, y0), self.pt(x1, y1)
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{p0[0]:.2f}" y1="{p0[1]:.2f}" x2="{p1[0]:.2f}" y2="{p1[1]:.2f}"'
            f' stroke="{stroke}" stroke-width="{width}"{d}/>'
        )

    def text_px(self, px, py, s, *, anchor="start", size=10, fill=None, rotate=None):
        t = f' transform="rotate({rotate} {px:.2f} {py:.2f})"' if rotate else ""
        self.parts.append(
            f'<text x="{px:.2f}" y="{py:.2f}" text-anchor="{anchor}" font-size="{size}"'
            f' fill="{fill or COLORS["text"]}"{t}>{_esc(s)}</text>'
        )

    def svg(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width:.0f}'
            f' {self.height:.0f}" width="100%" style="max-width:{self.width:.0f}px"'
            f' font-family="system-ui, sans-serif">'
            f'<rect class="bg" x="0" y="0" width="{self.width:.0f}"'
            f' height="{self.height:.0f}" fill="{COLORS["bg"]}"/>'
            f'{"".join(self.parts)}</svg>'
        )


def _corners(bbox: tuple[float, float, float, float]):
    x0, y0, x1, y1 = bbox
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _make_canvas(geom: P.PlanGeometry, view_w: float, pad: float,
                 bbox: tuple[float, float, float, float] | None = None) -> _Canvas:
    """指定範囲が収まるキャンバスを作る。既定は敷地と道路を含む範囲。"""
    bbox = bbox if bbox is not None else geom.local_bbox()
    frame = P.Frame(geom.north_angle_rad, 0.0, 0.0, bbox)
    x0, y0, x1, y1 = frame.bbox(_corners(bbox))
    span_x, span_y = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    scale = (view_w - 2 * pad) / span_x
    height = span_y * scale + 2 * pad
    c = _Canvas(frame, view_w, height, scale)
    # 余白の分だけ内側に寄せる
    c.frame = P.Frame(geom.north_angle_rad, pad / scale, pad / scale, bbox)
    return c


def _north_arrow(c: _Canvas, px: float, py: float) -> None:
    """方位記号。図は常に北が上を向く。"""
    c.parts.append(
        f'<circle cx="{px:.2f}" cy="{py:.2f}" r="13" fill="none"'
        f' stroke="{COLORS["muted"]}" stroke-width="1"/>'
        f'<polygon points="{px:.2f},{py - 17:.2f} {px - 5:.2f},{py + 10:.2f}'
        f' {px:.2f},{py + 4:.2f} {px + 5:.2f},{py + 10:.2f}"'
        f' fill="none" stroke="{COLORS["text"]}" stroke-width="1.2"/>'
    )
    c.text_px(px, py - 21, "N", anchor="middle", size=11, fill=COLORS["muted"])


def render_site_plan(result: VolumeResult) -> str:
    """配置図。敷地・道路・1階外形・最上階外形・寸法・方位。"""
    geom = P.build(result)
    c = _make_canvas(geom, SITE_VIEW_W, SITE_PAD)

    # --- 道路 ---------------------------------------------------------------
    for road in geom.roads:
        c.polygon_local(road.outline, COLORS["road"], COLORS["road_line"], 1.0, cls="road")
        a, b = (c.pt(*q) for q in road.center_line)
        c.parts.append(
            f'<line x1="{a[0]:.2f}" y1="{a[1]:.2f}" x2="{b[0]:.2f}" y2="{b[1]:.2f}"'
            f' stroke="{COLORS["road_line"]}" stroke-width="1"'
            f' stroke-dasharray="10 3 2 3"/>'
        )
        angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        if angle > 90 or angle <= -90:
            angle += 180
        c.text_px((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 4,
                  f"前面道路 W={road.width_mm / MM:.1f}m",
                  anchor="middle", size=10, fill=COLORS["muted"], rotate=angle)

    # --- 斜線・建蔽率がない場合の範囲 -----------------------------------------
    if geom.unconstrained and geom.floors:
        c.polygon_local(geom.unconstrained, "none", COLORS["ghost"], 1.2,
                        dash="4 4", cls="ghost")

    # --- 敷地 ---------------------------------------------------------------
    c.polygon_local(geom.site_outline, "none", COLORS["site"], 2.0, cls="site")

    # --- 建物外形（1階は塗り、最上階は破線） ----------------------------------
    if geom.floors:
        f1 = geom.floors[0]
        fill, stroke = CONSTRAINT_COLORS[f1.dominant]
        c.polygon_local(f1.outline, fill, stroke, 1.5, cls="floor")
        bx0, _, _, by1 = c.bbox_local(f1.outline)
        c.text_px(bx0 + 6, by1 - 7, f"1F {f1.area_mm2 / M2:,.1f}m2", size=10)

        top = geom.floors[-1]
        if top is not f1:
            _, stroke_t = CONSTRAINT_COLORS[top.dominant]
            c.polygon_local(top.outline, "none", stroke_t, 1.5, dash="6 4",
                            cls="floor-top")
            tx0, ty0, _, _ = c.bbox_local(top.outline)
            c.text_px(tx0 + 6, ty0 + 13, f"{top.floor}F {top.area_mm2 / M2:,.1f}m2",
                      size=10, fill=stroke_t)

    # --- 寸法（矩形敷地のときだけ間口・奥行を入れる） --------------------------
    if geom.is_rectangle:
        f_len, d_len = geom.frontage_mm, geom.depth_mm
        _dim_between(c, (0, 0), (f_len, 0), f"{f_len / MM:,.2f}m")
        _dim_between(c, (f_len, 0), (f_len, d_len), f"{d_len / MM:,.2f}m")
    else:
        c.text_px(SITE_PAD, c.height - 8,
                  f"間口 {geom.frontage_mm / MM:,.2f}m ／ "
                  f"奥行（最大）{geom.depth_mm / MM:,.2f}m",
                  size=10, fill=COLORS["muted"])

    _north_arrow(c, c.width - 24, 24)
    return c.svg()


def _dim_between(c: _Canvas, a: tuple[float, float], b: tuple[float, float],
                 label: str) -> None:
    """2点間の寸法。図の外側へ 16px ずらして引く。"""
    p0, p1 = c.pt(*a), c.pt(*b)
    horizontal = abs(p1[0] - p0[0]) >= abs(p1[1] - p0[1])
    off = 16.0
    # 図の中心と反対側へ寄せる
    cx, cy = c.width / 2, c.height / 2
    if horizontal:
        oy = -off if (p0[1] + p1[1]) / 2 < cy else off
        q0, q1 = (p0[0], p0[1] + oy), (p1[0], p1[1] + oy)
        tx, ty, anchor, rot = (q0[0] + q1[0]) / 2, q0[1] - 4, "middle", None
    else:
        ox = -off if (p0[0] + p1[0]) / 2 < cx else off
        q0, q1 = (p0[0] + ox, p0[1]), (p1[0] + ox, p1[1])
        tx, ty, anchor, rot = q0[0] - 4, (q0[1] + q1[1]) / 2, "middle", -90
    c.parts.append(
        f'<line x1="{q0[0]:.2f}" y1="{q0[1]:.2f}" x2="{q1[0]:.2f}" y2="{q1[1]:.2f}"'
        f' stroke="{COLORS["dim"]}" stroke-width="1"/>'
    )
    c.text_px(tx, ty, label, anchor=anchor, size=10, fill=COLORS["dim"], rotate=rot)


def render_floor_plans(result: VolumeResult) -> str:
    """各階平面図。最上階から順に並べる（図面と同じく上が上階）。"""
    geom = P.build(result)
    if not geom.floors:
        return ""

    # 全階を同じ縮尺で描く。敷地が収まる大きさを1枚分の基準にする。
    xs = [q[0] for q in geom.site_outline]
    ys = [q[1] for q in geom.site_outline]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    probe = _make_canvas(geom, TILE_W, TILE_PAD, bbox)
    tile_body_h = probe.height
    tile_h = tile_body_h + TILE_LABEL_H

    per_row = 4
    rows = (len(geom.floors) + per_row - 1) // per_row
    width = TILE_W * min(per_row, len(geom.floors))
    height = tile_h * rows

    parts: list[str] = [
        f'<rect class="bg" x="0" y="0" width="{width:.0f}" height="{height:.0f}"'
        f' fill="{COLORS["bg"]}"/>'
    ]

    for i, f in enumerate(reversed(geom.floors)):
        col, row = i % per_row, i // per_row
        ox, oy = col * TILE_W, row * tile_h
        tile = _make_canvas(geom, TILE_W, TILE_PAD, bbox)
        tile.polygon_local(geom.site_outline, "none", COLORS["site"], 1.2, cls="site")
        if geom.unconstrained:
            tile.polygon_local(geom.unconstrained, "none", COLORS["ghost"], 1.0,
                               dash="3 3", cls="ghost")
        fill, stroke = CONSTRAINT_COLORS[f.dominant]
        tile.polygon_local(f.outline, fill, stroke, 1.2, cls="floor")

        label = f.dominant.value if f.dominant else NO_CONSTRAINT_LEGEND
        tile.text_px(TILE_PAD, tile_body_h + 12, f"{f.floor}F", size=11)
        tile.text_px(TILE_W - TILE_PAD, tile_body_h + 12,
                     f"{f.area_mm2 / M2:,.1f}m2", anchor="end", size=10)
        tile.text_px(TILE_PAD, tile_body_h + 24, label, size=9, fill=stroke)

        parts.append(f'<g transform="translate({ox:.2f} {oy:.2f})">'
                     f'{"".join(tile.parts)}</g>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}"'
        f' width="100%" style="max-width:{width:.0f}px"'
        f' font-family="system-ui, sans-serif">{"".join(parts)}</svg>'
    )


# ---------------------------------------------------------------------------
# 天空率の検討図
# ---------------------------------------------------------------------------

SKY_COLORS = {
    "pass": "#16a34a",
    "fail": "#dc2626",
    "planned": "#2563eb",
}


def render_sky_plan(result: VolumeResult, study) -> str:
    """天空率の検討図。計画建築物の外形と、道路の反対側に並ぶ算定位置を描く。

    算定位置の点は、その位置で計画建築物の天空率が適合建築物以上なら緑、
    足りなければ赤。最も不利な位置には不足分を添える。
    """
    geom = P.build(result)
    c = _make_canvas(geom, SITE_VIEW_W, SITE_PAD)

    for road in geom.roads:
        c.polygon_local(road.outline, COLORS["road"], COLORS["road_line"], 1.0, cls="road")
    c.polygon_local(geom.site_outline, "none", COLORS["site"], 2.0, cls="site")

    # 斜線を守った現状の案（1階外形）を薄い破線で残しておく
    if geom.floors:
        c.polygon_local(geom.floors[0].outline, "none", COLORS["ghost"], 1.2,
                        dash="4 4", cls="base")

    # 計画建築物（斜線を外し、全階同一平面とした案）
    if study.planned_outline:
        c.parts.append(
            '<g fill-opacity="0.18">'
        )
        c.polygon_local(study.planned_outline, SKY_COLORS["planned"],
                        SKY_COLORS["planned"], 1.6, cls="planned")
        c.parts.append("</g>")

    # 算定位置（令135条の9第1項）
    for road_check in study.roads:
        worst = road_check.worst
        for point in road_check.points:
            px, py = c.pt(*point.position)
            color = SKY_COLORS["pass"] if point.passes else SKY_COLORS["fail"]
            c.parts.append(
                f'<circle class="sky-point" cx="{px:.2f}" cy="{py:.2f}" r="4"'
                f' fill="{color}" fill-opacity="0.85" stroke="#ffffff"'
                f' stroke-width="1"/>'
            )
            if worst is not None and point is worst:
                c.text_px(px, py + 16, f"{point.margin * 100:+.2f}pt",
                          anchor="middle", size=10, fill=color)

    # 通るようになる案（外壁後退を広げたもの）
    legend = "青＝計画建築物・灰破線＝斜線を守った案"
    if study.suggestion is not None and study.suggestion.outline:
        c.polygon_local(study.suggestion.outline, "none", SKY_COLORS["pass"], 1.6,
                        dash="7 4", cls="suggestion")
        legend += (f"・緑破線＝外壁後退 "
                   f"{study.suggestion.wall_setback_mm / MM:.1f}m で通る案")

    passed = sum(1 for r in study.roads for p in r.points if p.passes)
    total = sum(len(r.points) for r in study.roads)
    c.text_px(SITE_PAD, c.height - 20,
              f"算定位置 {passed}/{total} か所で適合建築物以上",
              size=10, fill=COLORS["muted"])
    c.text_px(SITE_PAD, c.height - 8, legend, size=9, fill=COLORS["muted"])
    _north_arrow(c, c.width - 24, 24)
    return c.svg()
