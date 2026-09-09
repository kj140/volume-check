"""配置図・各階平面図の SVG。

形は plan.build() が返す幾何をそのまま描く。DXF（drawer.py）と同じモジュールを
使うので、画面と出力図面はずれない。方位による回転も plan.Frame に集約している。

SVG は Y 軸が下向きなので、Frame（Y 上向き）の結果を最後に上下反転させる。
各階の色は断面図と同じ「その階を最も削っている規定」で塗り分ける。
"""

from __future__ import annotations

from xml.sax.saxutils import escape

import constants as C
import plan as P
from models import RoadSide, VolumeResult

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

ROAD_SIDE_LABEL = {
    RoadSide.NORTH: "北", RoadSide.EAST: "東",
    RoadSide.SOUTH: "南", RoadSide.WEST: "西",
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

    def rect_local(self, x0, y0, x1, y1, fill, stroke, width=1.2, dash="", cls=""):
        pts = [self.pt(x, y) for x in (x0, x1) for y in (y0, y1)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        k = f' class="{cls}"' if cls else ""
        self.parts.append(
            f'<rect{k} x="{min(xs):.2f}" y="{min(ys):.2f}"'
            f' width="{max(xs) - min(xs):.2f}" height="{max(ys) - min(ys):.2f}"'
            f' fill="{fill}" stroke="{stroke}" stroke-width="{width}"{d}/>'
        )

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


def _make_canvas(geom: P.PlanGeometry, view_w: float, pad: float) -> _Canvas:
    """道路まで含めた範囲が収まるキャンバスを作る。"""
    bbox = (0.0, -geom.road_width_mm, geom.frontage_mm, geom.depth_mm)
    frame = P.Frame(geom.road_side, 0.0, 0.0, bbox)
    x0, y0, x1, y1 = frame.bbox(*bbox)
    span_x, span_y = x1 - x0, y1 - y0
    scale = (view_w - 2 * pad) / span_x
    height = span_y * scale + 2 * pad
    c = _Canvas(frame, view_w, height, scale)
    # 余白の分だけ内側に寄せる
    c.frame = P.Frame(geom.road_side, pad / scale, pad / scale, bbox)
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
    f_len, d_len, w = geom.frontage_mm, geom.depth_mm, geom.road_width_mm

    # --- 道路 ---------------------------------------------------------------
    c.rect_local(0, -w, f_len, 0, COLORS["road"], COLORS["road_line"], 1.0, cls="road")
    c.line_local(0, -w / 2, f_len, -w / 2, COLORS["road_line"], 1.0, dash="10 3 2 3")
    rx, ry = c.pt(f_len / 2, -w / 2)
    vertical_road = geom.road_side in (RoadSide.EAST, RoadSide.WEST)
    c.text_px(rx, ry + 4, f"前面道路 W={w / MM:.1f}m（{ROAD_SIDE_LABEL[geom.road_side]}側）",
              anchor="middle", size=10, fill=COLORS["muted"],
              rotate=-90 if vertical_road else None)

    # --- 斜線・建蔽率がない場合の範囲 -----------------------------------------
    box = geom.unconstrained
    if box is not None and geom.floors:
        c.rect_local(*box, "none", COLORS["ghost"], 1.2, dash="4 4", cls="ghost")

    # --- 敷地 ---------------------------------------------------------------
    c.rect_local(0, 0, f_len, d_len, "none", COLORS["site"], 2.0, cls="site")

    # --- 建物外形（1階は塗り、最上階は破線） ----------------------------------
    if geom.floors:
        f1 = geom.floors[0]
        fill, stroke = CONSTRAINT_COLORS[f1.dominant]
        c.rect_local(f1.x_min_mm, f1.y_min_mm, f1.x_max_mm, f1.y_max_mm,
                     fill, stroke, 1.5, cls="floor")
        bx0, _, _, by1 = _sheet_rect(c, f1)
        c.text_px(bx0 + 6, by1 - 7, f"1F {f1.area_mm2 / M2:,.1f}m2", size=10)

        top = geom.floors[-1]
        if top is not f1:
            _, stroke_t = CONSTRAINT_COLORS[top.dominant]
            c.rect_local(top.x_min_mm, top.y_min_mm, top.x_max_mm, top.y_max_mm,
                         "none", stroke_t, 1.5, dash="6 4", cls="floor-top")
            tx0, ty0 = _sheet_rect(c, top)[:2]
            c.text_px(tx0 + 6, ty0 + 13, f"{top.floor}F {top.area_mm2 / M2:,.1f}m2",
                      size=10, fill=stroke_t)

    # --- 寸法 ---------------------------------------------------------------
    _dim_between(c, (0, 0), (f_len, 0), f"{f_len / MM:,.2f}m")
    _dim_between(c, (f_len, 0), (f_len, d_len), f"{d_len / MM:,.2f}m")

    _north_arrow(c, c.width - 24, 24)
    return c.svg()


def _sheet_rect(c: _Canvas, f: P.FloorPlan) -> tuple[float, float, float, float]:
    """階の矩形を SVG 座標の (minx, miny, maxx, maxy) で返す。"""
    pts = [c.pt(x, y) for x in (f.x_min_mm, f.x_max_mm) for y in (f.y_min_mm, f.y_max_mm)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


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
    bbox = (0.0, 0.0, geom.frontage_mm, geom.depth_mm)
    probe = P.Frame(geom.road_side, 0.0, 0.0, bbox)
    x0, y0, x1, y1 = probe.bbox(*bbox)
    span_x, span_y = x1 - x0, y1 - y0
    scale = (TILE_W - 2 * TILE_PAD) / span_x
    tile_h = span_y * scale + 2 * TILE_PAD + TILE_LABEL_H

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
        tile = _Canvas(
            P.Frame(geom.road_side, TILE_PAD / scale, TILE_PAD / scale, bbox),
            TILE_W, tile_h - TILE_LABEL_H, scale,
        )
        tile.rect_local(0, 0, geom.frontage_mm, geom.depth_mm,
                        "none", COLORS["site"], 1.2, cls="site")
        box = geom.unconstrained
        if box is not None:
            tile.rect_local(*box, "none", COLORS["ghost"], 1.0, dash="3 3", cls="ghost")
        fill, stroke = CONSTRAINT_COLORS[f.dominant]
        tile.rect_local(f.x_min_mm, f.y_min_mm, f.x_max_mm, f.y_max_mm,
                        fill, stroke, 1.2, cls="floor")

        label = f.dominant.value if f.dominant else NO_CONSTRAINT_LEGEND
        tile.text_px(TILE_PAD, tile.height + 12, f"{f.floor}F", size=11)
        tile.text_px(TILE_W - TILE_PAD, tile.height + 12, f"{f.area_mm2 / M2:,.1f}m2",
                     anchor="end", size=10)
        tile.text_px(TILE_PAD, tile.height + 24, label, size=9, fill=stroke)
        tile.text_px(TILE_W - TILE_PAD, tile.height + 24,
                     f"{f.width_mm / MM:.1f}×{f.depth_mm / MM:.1f}m",
                     anchor="end", size=9, fill=COLORS["muted"])

        parts.append(f'<g transform="translate({ox:.2f} {oy:.2f})">'
                     f'{"".join(tile.parts)}</g>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}"'
        f' width="100%" style="max-width:{width:.0f}px"'
        f' font-family="system-ui, sans-serif">{"".join(parts)}</svg>'
    )
