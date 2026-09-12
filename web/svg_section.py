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
from models import Constraint, VolumeResult

MM = C.M_TO_MM

VIEW_W = 940.0            # SVG の論理幅[px]
PAD_LEFT = 74.0           # 最高高さ寸法のための左余白
PAD_RIGHT = 150.0         # レベル文字欄
PAD_TOP = 34.0
PAD_BOTTOM = 92.0         # GL より下の道路名・境界名・注記・凡例

COLORS = {
    "bg": "#ffffff",
    "ground": "#334155",
    "road": "#94a3b8",
    "boundary": "#94a3b8",
    "limit": "#ea580c",
    "level": "#16a34a",
    "text": "#0f172a",
    "muted": "#64748b",
    "ghost": "#cbd5e1",
}

# 各階を「最も大きく削っている規定」で塗り分ける。斜線の線色と塗りの色を
# 揃えてあるので、どの線がその階を削ったのかが対応で読める。
CONSTRAINT_COLORS: dict[Constraint | None, tuple[str, str]] = {
    # 規定: (塗り, 線)
    Constraint.ROAD_SLANT: ("#fecaca", "#dc2626"),
    Constraint.NEIGHBOR_SLANT: ("#bfdbfe", "#2563eb"),
    Constraint.NORTH_SLANT: ("#ddd6fe", "#7c3aed"),
    Constraint.BCR: ("#fde68a", "#b45309"),
    None: ("#e2e8f0", "#64748b"),      # 斜線も建蔽率もかかっていない階
}
NO_CONSTRAINT_LEGEND = "制限なし"


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

    def rect(self, y0, z0, y1, z1, fill, stroke, width=1.2, cls=""):
        x0, x1 = self.x(y0), self.x(y1)
        yy0, yy1 = self.y(z1), self.y(z0)
        klass = f' class="{cls}"' if cls else ""
        self.parts.append(
            f'<rect{klass} x="{x0:.2f}" y="{yy0:.2f}" width="{x1 - x0:.2f}"'
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

    # --- 制限がかからなかった場合の外形（ゴースト） --------------------------
    # 実際の形との差が、規制で削られた分になる。
    if geom.unconstrained_y_mm is not None and geom.floors:
        gy0, gy1 = geom.unconstrained_y_mm
        c.parts.append(
            f'<rect class="ghost" x="{c.x(gy0):.2f}" y="{c.y(geom.max_height_mm):.2f}"'
            f' width="{c.x(gy1) - c.x(gy0):.2f}"'
            f' height="{c.y(0) - c.y(geom.max_height_mm):.2f}"'
            f' fill="none" stroke="{COLORS["ghost"]}" stroke-width="1.2"'
            f' stroke-dasharray="4 4"/>'
        )

    # --- 斜線 --------------------------------------------------------------
    road_color = CONSTRAINT_COLORS[Constraint.ROAD_SLANT][1]
    neighbor_color = CONSTRAINT_COLORS[Constraint.NEIGHBOR_SLANT][1]

    c.polyline(geom.road_slant, road_color, 1.8, dash="9 5")
    if geom.road_slant_capped:
        cap_y, cap_z = geom.road_slant[1]
        c.text(cap_y, cap_z, f"適用距離 {geom.applicable_distance_mm / MM:.0f}m",
               anchor="end", size=10, fill=road_color, dx=-6, dy=-6)
    label_z = z_max * 0.5
    c.text(-w + label_z / geom.road_gradient, label_z,
           f"道路斜線 1:{geom.road_gradient}", anchor="end", size=11,
           fill=road_color, dx=-8, dy=-4)

    if geom.neighbor_slant is not None:
        c.polyline(geom.neighbor_slant, neighbor_color, 1.8, dash="9 5")
        top_y = geom.neighbor_slant[-1][0]
        c.text(top_y, z_max, f"隣地斜線 立上り{geom.neighbor_start_mm / MM:.0f}m"
                             f" 1:{geom.neighbor_gradient}",
               anchor="end", size=11, fill=neighbor_color, dx=-6, dy=14)

    if geom.height_limit_mm is not None:
        limit = geom.height_limit_mm
        c.line(-w, limit, d, limit, COLORS["limit"], 1.5, dash="12 5")
        c.text(-w, limit, f"絶対高さ制限 {limit / MM:.1f}m", size=10,
               fill=COLORS["limit"], dy=-5)

    # --- 建物断面・各階レベル ----------------------------------------------
    text_x = c.x(d) + 16
    for f in geom.floors:
        fill, stroke = CONSTRAINT_COLORS[f.dominant]
        c.rect(f.y_min_mm, f.z_min_mm, f.y_max_mm, f.z_max_mm, fill, stroke, cls="floor")
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

    _draw_legend(c, geom)

    body = "".join(c.parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW_W:.0f}'
        f' {c.height:.0f}" width="100%" style="max-width:{VIEW_W:.0f}px"'
        f' font-family="system-ui, sans-serif">{body}</svg>'
    )


def _draw_legend(c: _Canvas, geom: S.SectionGeometry) -> None:
    """各階の塗り分けの凡例。実際に現れた規定だけを出す。"""
    used: list[Constraint | None] = []
    for f in geom.floors:
        if f.dominant not in used:
            used.append(f.dominant)
    if not used:
        return

    y = c.height - 22.0
    x = PAD_LEFT
    c.text_px(x, y, "各階の色 = その階を最も削っている規定：", size=10,
              fill=COLORS["muted"])
    x += 190.0
    for constraint in used:
        fill, stroke = CONSTRAINT_COLORS[constraint]
        label = constraint.value if constraint else NO_CONSTRAINT_LEGEND
        c.parts.append(
            f'<rect class="legend" x="{x:.2f}" y="{y - 9:.2f}" width="12" height="12"'
            f' fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
        )
        c.text_px(x + 17, y, label, size=10)
        x += 22.0 + len(label) * 11.0

    c.parts.append(
        f'<line x1="{x + 4:.2f}" y1="{y - 3:.2f}" x2="{x + 26:.2f}" y2="{y - 3:.2f}"'
        f' stroke="{COLORS["ghost"]}" stroke-width="1.2" stroke-dasharray="4 4"/>'
    )
    c.text_px(x + 31, y, "斜線・建蔽率がない場合の範囲（同じ高さで）", size=10,
              fill=COLORS["muted"])
