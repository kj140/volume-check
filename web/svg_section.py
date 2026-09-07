"""断面図の SVG。ブラウザでのプレビュー用。

形は section.build() が返す幾何をそのまま描く。DXF（drawer.py）と
同じモジュールを使うので、画面のプレビューと出力される図面はずれない。

SVG は Y 軸が下向きなので、断面のローカル座標 (y, z) を
    X =  (y + 道路幅員) * scale + pad_left
    Y =  (z_max - z)    * scale + pad_top
で写す。
"""

from __future__ import annotations

from xml.sax.saxutils import escape

import constants as C
import section as S
from models import VolumeResult

MM = C.M_TO_MM

VIEW_W = 940.0            # SVG の論理幅[px]
PAD_LEFT = 74.0           # 最高高さ寸法のための左余白
PAD_RIGHT = 150.0         # レベル文字欄
PAD_TOP = 34.0
PAD_BOTTOM = 62.0         # GL より下の道路名・境界名・注記

COLORS = {
    "bg": "#ffffff",
    "ground": "#334155",
    "road": "#94a3b8",
    "boundary": "#94a3b8",
    "slant": "#dc2626",
    "limit": "#ea580c",
    "building": "#fde68a",
    "building_line": "#b45309",
    "level": "#16a34a",
    "text": "#0f172a",
    "muted": "#64748b",
}


def _esc(s: object) -> str:
    return escape(str(s))


class _Canvas:
    def __init__(self, geom: S.SectionGeometry):
        self.geom = geom
        span_mm = geom.road_width_mm + geom.depth_mm
        self.scale = (VIEW_W - PAD_LEFT - PAD_RIGHT) / span_mm
        self.height = geom.z_max_mm * self.scale + PAD_TOP + PAD_BOTTOM
        self._w = geom.road_width_mm
        self._z_max = geom.z_max_mm
        self.parts: list[str] = []

    # --- 座標変換 ---------------------------------------------------------
    def x(self, y_mm: float) -> float:
        return PAD_LEFT + (y_mm + self._w) * self.scale

    def y(self, z_mm: float) -> float:
        return PAD_TOP + (self._z_max - z_mm) * self.scale

    # --- 描画プリミティブ -------------------------------------------------
    def line(self, y1, z1, y2, z2, stroke, width=1.0, dash="", opacity=1.0):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{self.x(y1):.2f}" y1="{self.y(z1):.2f}"'
            f' x2="{self.x(y2):.2f}" y2="{self.y(z2):.2f}"'
            f' stroke="{stroke}" stroke-width="{width}"{d} opacity="{opacity}"/>'
        )

    def polyline(self, points, stroke, width=1.5, dash=""):
        pts = " ".join(f"{self.x(y):.2f},{self.y(z):.2f}" for y, z in points)
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{stroke}"'
            f' stroke-width="{width}"{d}/>'
        )

    def rect(self, y0, z0, y1, z1, fill, stroke, width=1.2):
        x0, x1 = self.x(y0), self.x(y1)
        yy0, yy1 = self.y(z1), self.y(z0)
        self.parts.append(
            f'<rect x="{x0:.2f}" y="{yy0:.2f}" width="{x1 - x0:.2f}"'
            f' height="{yy1 - yy0:.2f}" fill="{fill}" stroke="{stroke}"'
            f' stroke-width="{width}"/>'
        )

    def text(self, y_mm, z_mm, s, *, anchor="start", size=11, fill=None,
             dx=0.0, dy=0.0, weight="normal"):
        self.parts.append(
            f'<text x="{self.x(y_mm) + dx:.2f}" y="{self.y(z_mm) + dy:.2f}"'
            f' text-anchor="{anchor}" font-size="{size}"'
            f' font-weight="{weight}" fill="{fill or COLORS["text"]}">{_esc(s)}</text>'
        )

    def text_px(self, px, py, s, *, anchor="start", size=11, fill=None, rotate=None):
        transform = f' transform="rotate({rotate} {px:.2f} {py:.2f})"' if rotate else ""
        self.parts.append(
            f'<text x="{px:.2f}" y="{py:.2f}" text-anchor="{anchor}"'
            f' font-size="{size}" fill="{fill or COLORS["text"]}"{transform}>'
            f"{_esc(s)}</text>"
        )


def render(result: VolumeResult) -> str:
    """VolumeResult から断面図の SVG 文字列を作る。"""
    geom = S.build(result)
    c = _Canvas(geom)
    w, d = geom.road_width_mm, geom.depth_mm
    z_max = geom.z_max_mm

    c.parts.append(
        f'<rect x="0" y="0" width="{VIEW_W}" height="{c.height:.2f}" fill="{COLORS["bg"]}"/>'
    )

    # --- GL・道路・敷地境界 ------------------------------------------------
    c.line(-w, 0, d, 0, COLORS["ground"], 2.0)
    c.line(-w, 0, 0, 0, COLORS["road"], 5.0)
    c.text(-w, 0, "GL±0", anchor="start", size=11, fill=COLORS["muted"], dy=16)
    c.text_px(c.x(-w / 2), c.y(0) + 34, f"道路 W={w / MM:.1f}m",
              anchor="middle", size=11, fill=COLORS["muted"])
    for y_mm, label in ((0.0, "道路境界"), (d, "隣地境界")):
        c.line(y_mm, 0, y_mm, z_max, COLORS["boundary"], 1.0, dash="6 4")
        c.text_px(c.x(y_mm), c.y(0) + 16, label, anchor="middle", size=10,
                  fill=COLORS["muted"])
    c.line(-w, 0, -w, z_max * 0.12, COLORS["boundary"], 1.0, dash="6 4")

    # --- 斜線 --------------------------------------------------------------
    c.polyline(geom.road_slant, COLORS["slant"], 1.8, dash="9 5")
    if geom.road_slant_capped:
        cap_y, cap_z = geom.road_slant[1]
        c.text(cap_y, cap_z, f"適用距離 {geom.applicable_distance_mm / MM:.0f}m",
               anchor="end", size=10, fill=COLORS["slant"], dx=-6, dy=-6)
    label_z = z_max * 0.5
    c.text(-w + label_z / geom.road_gradient, label_z,
           f"道路斜線 1:{geom.road_gradient}", anchor="end", size=11,
           fill=COLORS["slant"], dx=-8, dy=-4)

    if geom.neighbor_slant is not None:
        c.polyline(geom.neighbor_slant, COLORS["slant"], 1.8, dash="9 5")
        top_y = geom.neighbor_slant[-1][0]
        c.text(top_y, z_max, f"隣地斜線 立上り{geom.neighbor_start_mm / MM:.0f}m"
                             f" 1:{geom.neighbor_gradient}",
               anchor="end", size=11, fill=COLORS["slant"], dx=-6, dy=14)

    if geom.height_limit_mm is not None:
        limit = geom.height_limit_mm
        c.line(-w, limit, d, limit, COLORS["limit"], 1.5, dash="12 5")
        c.text(-w, limit, f"絶対高さ制限 {limit / MM:.1f}m", size=10,
               fill=COLORS["limit"], dy=-5)

    # --- 建物断面・各階レベル ----------------------------------------------
    text_x = c.x(d) + 16
    for f in geom.floors:
        c.rect(f.y_min_mm, f.z_min_mm, f.y_max_mm, f.z_max_mm,
               COLORS["building"], COLORS["building_line"])
        c.line(f.y_max_mm, f.z_min_mm, d, f.z_min_mm, COLORS["level"], 1.0, opacity=0.7)
        c.text_px(text_x, c.y(f.z_min_mm) - 3, f"{f.floor}FL  +{f.z_min_mm / MM:,.2f}m",
                  size=10, fill=COLORS["text"])

    if geom.floors:
        top = geom.max_height_mm
        c.line(-w, top, d, top, COLORS["level"], 1.2)
        c.text_px(text_x, c.y(top) - 3, f"最高高さ +{top / MM:,.2f}m",
                  size=11, fill=COLORS["level"])
        # 最高高さ寸法（左端に縦書き）
        x0 = PAD_LEFT - 26
        c.parts.append(
            f'<line x1="{x0:.2f}" y1="{c.y(0):.2f}" x2="{x0:.2f}"'
            f' y2="{c.y(top):.2f}" stroke="{COLORS["level"]}" stroke-width="1"/>'
        )
        c.text_px(x0 - 6, (c.y(0) + c.y(top)) / 2, f"{top / MM:,.2f}m",
                  anchor="middle", size=10, fill=COLORS["level"],
                  rotate=-90)
    else:
        c.text_px(VIEW_W / 2, c.height / 2, "建築可能な階が成立しません",
                  anchor="middle", size=15, fill=COLORS["muted"])

    if geom.neighbor_note:
        c.text_px(text_x, c.y(0) + 34, geom.neighbor_note, size=10, fill=COLORS["muted"])

    body = "".join(c.parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW_W:.0f}'
        f' {c.height:.0f}" width="100%" style="max-width:{VIEW_W:.0f}px"'
        f' font-family="system-ui, sans-serif">{body}</svg>'
    )
