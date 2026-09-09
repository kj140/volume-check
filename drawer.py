"""DXF 出力。

図面は実寸（1単位 = 1mm）でモデル空間に描く。1枚に以下を配置する。

    +----------------------+   +-------------------------------+
    |                      |   |          断 面 図             |
    |      配 置 図        |   |                               |
    |                      |   +-------------------------------+
    |                      |   |          面 積 表             |
    +----------------------+---+-------------------------------+
    |     表題欄 / 適用規定・未考慮事項  ―  注記                 |
    +-----------------------------------------------------------+

断面図がこのツールの主眼。「斜線に建物が収まっている」ことが一目で分かるよう、
道路斜線・隣地斜線を実際の勾配で描き、各階の断面を重ねる。

レイアウトは「各ブロックが自分の外形寸法を返し、呼び出し側が原点を決める」
方式にしている（_plan_size / _section_size / _table_size / _footer_size）。
サイズ関数と描画関数で同じ値を使うため、ブロック同士は重ならない。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment as TA

import constants as C
import section as S
from models import RoadSide, VolumeResult

MM = C.M_TO_MM
M2 = C.M2_TO_MM2

# --- 図面上の文字・線のサイズ（mm 実寸） ------------------------------------
H_SHEET_TITLE = 1800.0     # 図面タイトル
H_HEAD = 1100.0            # 「配置図」「断面図」などの図名
H_TEXT = 700.0             # 一般注記・図中文字
H_TABLE = 600.0            # 面積表
H_SMALL = 520.0            # 細かい注記
DIM_TXT = 650.0            # 寸法値

GAP = 9000.0               # ブロック間の間隔
MARGIN = 7000.0            # 各ブロックの余白
HEAD_ZONE = 5200.0         # 図名のためにブロック上部に確保する高さ

# --- レイヤー定義（仕様の表そのまま） ---------------------------------------
JP_FONT = "msgothic.ttc"
JP_STYLE = "JP-GOTHIC"

LT_DASHDOT = "DASHDOT_MM"   # 一点鎖線（斜線用）
LT_DASHED = "DASHED_MM"     # 破線（最上階外形用）
LT_CENTER = "CENTER_MM"     # 一点鎖線（中心線・境界表現用）

LAYERS: tuple[tuple[str, str, int, str, int], ...] = (
    # (名前, 内容, 色, 線種, 線幅[1/100mm]。-3 = 既定)
    ("S-SITE", "敷地境界", 7, "CONTINUOUS", 50),
    ("S-ROAD", "道路", 8, "CONTINUOUS", -3),
    ("A-OUTL", "建物外形", 2, "CONTINUOUS", 35),
    ("A-SLNT", "斜線", 1, LT_DASHDOT, -3),
    ("A-DIMS", "寸法", 3, "CONTINUOUS", -3),
    ("A-TEXT", "文字", 7, "CONTINUOUS", -3),
    ("A-ENVL", "斜線・建蔽率がない場合の範囲", 8, LT_DASHED, -3),
)

DISCLAIMER = (
    "本図は企画検討用のボリューム試算です。矩形敷地のみ対応。\n"
    "天空率・日影規制・複数前面道路の緩和・高度地区・地区計画は\n"
    "未考慮。確認申請には使用できません。建築士による確認が必要です。"
)

ROAD_SIDE_LABEL = {
    RoadSide.NORTH: "北",
    RoadSide.EAST: "東",
    RoadSide.SOUTH: "南",
    RoadSide.WEST: "西",
}


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------


def draw(result: VolumeResult, path: str | Path) -> None:
    """VolumeResult を 1 枚の DXF として書き出す。"""
    doc = _setup_doc()
    msp = doc.modelspace()

    plan_w, plan_h = _plan_size(result)
    sec_w, sec_h = _section_size(result)
    tbl_w, tbl_h = _table_size(result)

    right_w = max(sec_w, tbl_w)
    total_w = plan_w + GAP + right_w
    foot_h = _footer_size(result, total_w)[1]

    body_y = foot_h + GAP
    body_h = max(plan_h, tbl_h + GAP + sec_h)

    # 配置図は左列の中央に置く（右列の方が高いことが多いため）
    _draw_plan(msp, result, 0.0, body_y + (body_h - plan_h) / 2.0)
    _draw_area_table(msp, result, plan_w + GAP, body_y)
    _draw_section(msp, result, plan_w + GAP, body_y + tbl_h + GAP)
    _draw_footer(msp, result, 0.0, 0.0, total_w, foot_h)

    _text(msp, "ボリュームチェック図", (0.0, body_y + body_h + GAP * 0.5),
          H_SHEET_TITLE, "A-TEXT")

    doc.saveas(path)


# ---------------------------------------------------------------------------
# 図面のセットアップ
# ---------------------------------------------------------------------------


def _setup_doc():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4       # mm
    doc.header["$MEASUREMENT"] = 1    # メートル法
    doc.header["$LTSCALE"] = 1.0      # 線種ピッチは mm 実寸で定義済み

    # 線種テーブルへの登録（pattern = [全長, 線, 空白(負), 点, 空白(負)]）
    # ピッチは図面が 1:200〜1:500 で出力されることを前提に mm 実寸で決めている。
    # 例: DASHED の 1000mm の線分は 1:200 で紙面 5mm になり、破線として読める。
    doc.linetypes.add(LT_CENTER, pattern=[2800.0, 2000.0, -400.0, 0.0, -400.0],
                      description="Center ____ . ____")
    doc.linetypes.add(LT_DASHDOT, pattern=[2400.0, 1600.0, -400.0, 0.0, -400.0],
                      description="Dashdot ___ . ___")
    doc.linetypes.add(LT_DASHED, pattern=[1600.0, 1000.0, -600.0],
                      description="Dashed ___ ___")

    # 日本語は TrueType を明示しないと確実に文字化けする
    doc.styles.add(JP_STYLE, font=JP_FONT)

    for name, description, color, linetype, lineweight in LAYERS:
        layer = doc.layers.add(name, color=color, linetype=linetype)
        layer.description = description
        if lineweight > 0:
            layer.dxf.lineweight = lineweight

    ds = doc.dimstyles.add("JP-MM")
    ds.dxf.dimtxsty = JP_STYLE
    ds.dxf.dimtxt = DIM_TXT
    ds.dxf.dimasz = 500.0
    ds.dxf.dimexe = 350.0
    ds.dxf.dimexo = 300.0
    ds.dxf.dimgap = 200.0
    ds.dxf.dimdec = 0
    ds.dxf.dimlunit = 2
    ds.dxf.dimtad = 1
    ds.dxf.dimscale = 1.0
    ds.set_arrows(blk=ezdxf.ARROWS.architectural_tick)
    return doc


def _text(msp, s: str, pos, height: float, layer: str,
          align: TA = TA.LEFT, rotation: float = 0.0):
    t = msp.add_text(
        s, height=height,
        dxfattribs={"layer": layer, "style": JP_STYLE, "rotation": rotation},
    )
    t.set_placement(pos, align=align)
    return t


def _line(msp, p1, p2, layer: str, linetype: str | None = None):
    attribs: dict = {"layer": layer}
    if linetype:
        attribs["linetype"] = linetype
    return msp.add_line(p1, p2, dxfattribs=attribs)


def _rect(msp, x0: float, y0: float, x1: float, y1: float,
          layer: str, linetype: str | None = None):
    attribs: dict = {"layer": layer}
    if linetype:
        attribs["linetype"] = linetype
    return msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs=attribs
    )


def _dim(msp, p1, p2, base, text: str = "<>"):
    """p1-p2 間の直線寸法。base は寸法線を通す点。軸方向は自動判定。"""
    horizontal = abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1])
    d = msp.add_linear_dim(
        base=base, p1=p1, p2=p2, text=text,
        angle=0.0 if horizontal else 90.0,
        dimstyle="JP-MM", dxfattribs={"layer": "A-DIMS"},
    )
    d.render()
    return d


# ---------------------------------------------------------------------------
# 配置図
# ---------------------------------------------------------------------------

# 前面道路の方位ごとの、敷地ローカル座標から図面座標への回転。
#   v = 道路から敷地奥へ向かう方向（ローカル +y）
#   u = 道路に平行な方向（ローカル +x） = v を時計回りに 90 度回した向き
# いずれの方位でも図面は「北が上」になる。
_ROAD_VECTORS: dict[RoadSide, tuple[float, float]] = {
    RoadSide.SOUTH: (0.0, 1.0),    # 道路が南 → 敷地は道路の北側
    RoadSide.NORTH: (0.0, -1.0),
    RoadSide.EAST: (-1.0, 0.0),
    RoadSide.WEST: (1.0, 0.0),
}

_PLAN_SIDE_PAD = 4000.0    # 道路を敷地の左右にはみ出させる長さ・寸法線の余地
_PLAN_DIM_PAD = 5000.0     # 道路側／奥側の寸法線のための余地


class _PlanFrame:
    """敷地ローカル座標 (x, y) → 図面座標への変換。"""

    def __init__(self, road_side: RoadSide, base_x: float, base_y: float,
                 local_bbox: tuple[float, float, float, float]):
        vx, vy = _ROAD_VECTORS[road_side]
        self._u = (vy, -vx)
        self._v = (vx, vy)
        x0, y0, x1, y1 = local_bbox
        corners = [self._rot(x, y) for x in (x0, x1) for y in (y0, y1)]
        self._ox = base_x - min(c[0] for c in corners)
        self._oy = base_y - min(c[1] for c in corners)

    def _rot(self, x: float, y: float) -> tuple[float, float]:
        return (x * self._u[0] + y * self._v[0], x * self._u[1] + y * self._v[1])

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        rx, ry = self._rot(x, y)
        return (rx + self._ox, ry + self._oy)


def _sheet_bbox(frame: _PlanFrame, x0: float, y0: float, x1: float, y1: float):
    """ローカル矩形を図面座標に変換したときの (minx, miny, maxx, maxy)。

    方位によって回転がかかるため、ラベルの位置決めはローカル座標のオフセットでは
    なくこの図面座標のバウンディングボックスを基準にする。
    """
    pts = [frame(x, y) for x in (x0, x1) for y in (y0, y1)]
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _plan_local_bbox(r: VolumeResult) -> tuple[float, float, float, float]:
    """配置図が実際に使うローカル座標の範囲（寸法線の張り出しを含む）。"""
    s = r.input.site
    return (
        -_PLAN_SIDE_PAD,
        -s.road_width_mm - _PLAN_DIM_PAD,
        s.frontage_mm + _PLAN_SIDE_PAD,
        s.depth_mm + _PLAN_DIM_PAD,
    )


def _plan_size(r: VolumeResult) -> tuple[float, float]:
    x0, y0, x1, y1 = _plan_local_bbox(r)
    span_x, span_y = x1 - x0, y1 - y0
    if r.input.site.road_side in (RoadSide.SOUTH, RoadSide.NORTH):
        w, h = span_x, span_y
    else:
        w, h = span_y, span_x
    return w + 2 * MARGIN, h + 2 * MARGIN + HEAD_ZONE


def _draw_plan(msp, r: VolumeResult, ox: float, oy: float) -> None:
    site = r.input.site
    frame = _PlanFrame(site.road_side, ox + MARGIN, oy + MARGIN, _plan_local_bbox(r))
    F, D, W = site.frontage_mm, site.depth_mm, site.road_width_mm
    _, block_h = _plan_size(r)

    _text(msp, "配 置 図（実寸 1:1）", (ox + MARGIN, oy + block_h - MARGIN - H_HEAD),
          H_HEAD, "A-TEXT")

    # --- 道路 ---------------------------------------------------------------
    msp.add_lwpolyline(
        [frame(-_PLAN_SIDE_PAD, -W), frame(F + _PLAN_SIDE_PAD, -W),
         frame(F + _PLAN_SIDE_PAD, 0.0), frame(-_PLAN_SIDE_PAD, 0.0)],
        close=True, dxfattribs={"layer": "S-ROAD"},
    )
    _line(msp, frame(-_PLAN_SIDE_PAD, -W / 2), frame(F + _PLAN_SIDE_PAD, -W / 2),
          "S-ROAD", LT_CENTER)
    # 道路が図面上で縦に走る（東西の前面道路）ときは文字も 90 度回す。
    # 中心線に重ならないよう、敷地と反対側へ少しずらす。
    vx, vy = _ROAD_VECTORS[site.road_side]
    road_label = frame(F * 0.5, -W * 0.5)
    _text(msp, f"前面道路 W={W / MM:.1f}m（{ROAD_SIDE_LABEL[site.road_side]}側）",
          (road_label[0] - vx * 900.0, road_label[1] - vy * 900.0),
          H_TEXT, "A-TEXT", TA.MIDDLE_CENTER,
          rotation=90.0 if abs(vx) > abs(vy) else 0.0)

    # --- 敷地 ---------------------------------------------------------------
    msp.add_lwpolyline(
        [frame(0, 0), frame(F, 0), frame(F, D), frame(0, D)],
        close=True, dxfattribs={"layer": "S-SITE"},
    )
    _text(msp, "敷地境界線", frame(F, D + 900.0), H_TEXT, "A-TEXT", TA.BOTTOM_RIGHT)

    # --- 建物外形（1階=実線 / 最上階=破線） ----------------------------------
    if r.floors:
        f1 = r.floors[0]
        msp.add_lwpolyline(
            [frame(f1.x_min_mm, f1.y_min_mm), frame(f1.x_max_mm, f1.y_min_mm),
             frame(f1.x_max_mm, f1.y_max_mm), frame(f1.x_min_mm, f1.y_max_mm)],
            close=True, dxfattribs={"layer": "A-OUTL"},
        )
        bx0, by0, _, _ = _sheet_bbox(frame, f1.x_min_mm, f1.y_min_mm,
                                     f1.x_max_mm, f1.y_max_mm)
        _text(msp, f"1F 外形 {f1.gross_area_mm2 / M2:,.1f}m2",
              (bx0 + 700, by0 + 700), H_TEXT, "A-TEXT")

        ft = r.floors[-1]
        if ft is not f1:
            msp.add_lwpolyline(
                [frame(ft.x_min_mm, ft.y_min_mm), frame(ft.x_max_mm, ft.y_min_mm),
                 frame(ft.x_max_mm, ft.y_max_mm), frame(ft.x_min_mm, ft.y_max_mm)],
                close=True, dxfattribs={"layer": "A-OUTL", "linetype": LT_DASHED},
            )
            tx0, _, _, ty1 = _sheet_bbox(frame, ft.x_min_mm, ft.y_min_mm,
                                         ft.x_max_mm, ft.y_max_mm)
            _text(msp, f"{ft.floor}F 外形（破線）{ft.gross_area_mm2 / M2:,.1f}m2",
                  (tx0 + 700, ty1 - 700 - H_TEXT), H_TEXT, "A-TEXT")

    # --- 方位記号（北は常に図面上方） -----------------------------------------
    plan_w, _ = _plan_size(r)
    nx = ox + plan_w - MARGIN * 0.8
    ny = oy + block_h - MARGIN - HEAD_ZONE * 0.5
    msp.add_circle((nx, ny), radius=1700.0, dxfattribs={"layer": "A-TEXT"})
    msp.add_lwpolyline(
        [(nx, ny + 2300), (nx - 750, ny - 1500), (nx, ny - 750), (nx + 750, ny - 1500)],
        close=True, dxfattribs={"layer": "A-TEXT"},
    )
    _text(msp, "N", (nx, ny + 2700), H_TEXT, "A-TEXT", TA.BOTTOM_CENTER)

    # --- 寸法 ---------------------------------------------------------------
    _dim(msp, frame(0, 0), frame(F, 0), frame(F / 2, -W - 2600.0))          # 間口
    _dim(msp, frame(F, 0), frame(F, D), frame(F + 2800.0, D / 2))           # 奥行
    if r.floors:
        ft = r.floors[-1]
        if ft.y_min_mm > 1.0:
            _dim(msp, frame(0, 0), frame(0, ft.y_min_mm), frame(-2000.0, ft.y_min_mm / 2),
                 text=f"最上階 道路側後退 {ft.y_min_mm / MM:.2f}m")
        if ft.x_min_mm > 1.0:
            _dim(msp, frame(0, D), frame(ft.x_min_mm, D),
                 frame(ft.x_min_mm / 2, D + 2800.0),
                 text=f"隣地側後退 {ft.x_min_mm / MM:.2f}m")


# ---------------------------------------------------------------------------
# 断面図（最重要）
# ---------------------------------------------------------------------------

_SEC_LEFT_PAD = 7000.0          # 最高高さ寸法のための左余地
_SEC_LEVEL_GAP = 2800.0         # 建物右端からレベル文字までの距離
_SEC_LEVEL_TEXT_W = 14000.0     # レベル文字欄の幅


def _section_z_max(r: VolumeResult) -> float:
    """断面図の描画上端。定義は section.py に集約している。"""
    return S.z_max(r)


def _section_size(r: VolumeResult) -> tuple[float, float]:
    s = r.input.site
    w = (_SEC_LEFT_PAD + s.road_width_mm + s.depth_mm
         + _SEC_LEVEL_GAP + _SEC_LEVEL_TEXT_W)
    return w + 2 * MARGIN, _section_z_max(r) + 2 * MARGIN + HEAD_ZONE


def _draw_section(msp, r: VolumeResult, ox: float, oy: float) -> None:
    site = r.input.site
    W, D = site.road_width_mm, site.depth_mm
    z_max = _section_z_max(r)
    _, block_h = _section_size(r)

    # ローカル (y, z) → 図面座標。y = -W が道路の反対側の境界線、y = 0 が道路境界。
    bx = ox + MARGIN + _SEC_LEFT_PAD + W
    by = oy + MARGIN

    def p(y: float, z: float) -> tuple[float, float]:
        return (bx + y, by + z)

    _text(msp, f"断 面 図（道路と直交する方向・{ROAD_SIDE_LABEL[site.road_side]}側道路）",
          (ox + MARGIN, oy + block_h - MARGIN - H_HEAD), H_HEAD, "A-TEXT")

    # --- GL・道路・敷地境界 ---------------------------------------------------
    _line(msp, p(-W - 2500, 0), p(D + 2500, 0), "S-SITE")
    _text(msp, "GL±0", p(-W - 2500, 500), H_TEXT, "A-TEXT")
    _line(msp, p(-W, 0), p(-W, 3500), "S-ROAD", LT_CENTER)
    _text(msp, "道路反対側境界", p(-W, 3800), H_SMALL, "A-TEXT", TA.BOTTOM_CENTER)
    # 幅員が小さいと「道路境界」ラベルと同じ高さに来るので 1 段下げる
    _text(msp, f"道路 W={W / MM:.1f}m", p(-W / 2, -H_TEXT * 3.6), H_TEXT, "A-TEXT",
          TA.MIDDLE_CENTER)
    for y, label in ((0.0, "道路境界"), (D, "隣地境界")):
        _line(msp, p(y, 0), p(y, z_max), "S-SITE", LT_CENTER)
        _text(msp, label, p(y, -H_TEXT * 1.1), H_SMALL, "A-TEXT", TA.TOP_CENTER)

    geom = S.build(r)

    # --- 道路斜線（適用距離で頭打ち。描画範囲の上端でクリップ） ----------------
    g = geom.road_gradient
    L = geom.applicable_distance_mm
    msp.add_lwpolyline([p(y, z) for y, z in geom.road_slant],
                       dxfattribs={"layer": "A-SLNT"})
    if geom.road_slant_capped:
        cap_z = L * g
        _text(msp, f"道路斜線 1:{g}", p(-W + L * 0.45 - 600, cap_z * 0.45 + 500),
              H_TEXT, "A-TEXT", TA.BOTTOM_RIGHT)
        _text(msp, f"適用距離 {L / MM:.0f}m（以遠は制限なし）",
              p(-W + L - 600, cap_z - H_SMALL * 2.2), H_SMALL, "A-TEXT", TA.BOTTOM_RIGHT)
    else:
        # 最高高さ寸法（左端に縦書き）と同じ高さに来ないよう、やや上に置く
        frac = 0.62
        mid_y = -W + z_max / g * frac
        _text(msp, f"道路斜線 1:{g}", p(mid_y - 600, z_max * frac + 500),
              H_TEXT, "A-TEXT", TA.BOTTOM_RIGHT)
        _text(msp, f"適用距離 {L / MM:.0f}m（頭打ちは図の範囲外）",
              p(mid_y - 600, z_max * frac + 500 - H_SMALL * 1.8),
              H_SMALL, "A-TEXT", TA.BOTTOM_RIGHT)

    # --- 隣地斜線 -------------------------------------------------------------
    if geom.neighbor_slant is not None:
        h0, ng = geom.neighbor_start_mm, geom.neighbor_gradient
        msp.add_lwpolyline([p(y, z) for y, z in geom.neighbor_slant],
                           dxfattribs={"layer": "A-SLNT"})
        top_y = geom.neighbor_slant[-1][0]
        _text(msp, f"隣地斜線 立上り{h0 / MM:.0f}m 1:{ng}",
              p(top_y - 800, z_max - H_TEXT * 1.8), H_TEXT, "A-TEXT", TA.BOTTOM_RIGHT)
    else:
        if geom.neighbor_note.startswith("隣地斜線 立上り"):
            _line(msp, p(D, 0), p(D, z_max), "A-SLNT")
        # 図中に置くと建物や最高高さ線と重なるため、レベル文字欄の下（GL より下）に出す。
        _text(msp, geom.neighbor_note, p(D + _SEC_LEVEL_GAP, -H_TEXT * 2.6),
              H_SMALL, "A-TEXT")

    # --- 絶対高さ制限 ---------------------------------------------------------
    if geom.height_limit_mm is not None:
        limit = geom.height_limit_mm
        _line(msp, p(-W - 1500, limit), p(D + 1500, limit), "A-SLNT")
        _text(msp, f"絶対高さ制限 {limit / MM:.1f}m", p(-W - 1500, limit + 500),
              H_SMALL, "A-TEXT")

    # --- 制限がなければ建てられた範囲（実形との差が削られた分） ----------------
    if geom.unconstrained_y_mm is not None and geom.floors:
        gy0, gy1 = geom.unconstrained_y_mm
        _rect(msp, *p(gy0, 0.0), *p(gy1, geom.max_height_mm), "A-ENVL")
        _text(msp, "斜線・建蔽率がなければ建てられた範囲（同じ高さで）",
              p(gy0 + 400, geom.max_height_mm + 400),
              H_SMALL, "A-TEXT")

    # --- 建物断面・各階レベル -------------------------------------------------
    text_x = D + _SEC_LEVEL_GAP
    for f in geom.floors:
        _rect(msp, *p(f.y_min_mm, f.z_min_mm), *p(f.y_max_mm, f.z_max_mm), "A-OUTL")
        _line(msp, p(f.y_max_mm, f.z_min_mm), p(text_x - 500, f.z_min_mm), "A-DIMS")
        label = f"{f.floor}FL  +{f.z_min_mm / MM:,.2f}m"
        if f.dominant is not None:
            label += f"  （{f.dominant.value}）"
        _text(msp, label, p(text_x, f.z_min_mm + 250), H_TEXT, "A-TEXT")

    if r.floors:
        top = r.max_height_mm
        _line(msp, p(-W - _SEC_LEFT_PAD + 1500, top), p(text_x - 500, top), "A-DIMS")
        _text(msp, f"最高高さ  +{top / MM:,.2f}m", p(text_x, top + 250), H_TEXT, "A-TEXT")
        # 最高高さの寸法は道路の左（余地内）に出す。レベル文字欄と重ねない。
        _dim(msp, p(-W - 3000, 0), p(-W - 3000, top), p(-W - 4800, top / 2))
    else:
        _text(msp, "建築可能な階が成立しません", p(D / 2, z_max * 0.4),
              H_HEAD, "A-TEXT", TA.MIDDLE_CENTER)


# ---------------------------------------------------------------------------
# 面積表
# ---------------------------------------------------------------------------

_TBL_COLS: tuple[tuple[str, float], ...] = (
    ("階", 6000.0),
    ("階高(m)", 8500.0),
    ("床面積(m2)", 12000.0),
    ("容積対象面積(m2)", 15000.0),
    ("累計(m2)", 12000.0),
)
_TBL_ROW_H = 1500.0


def _table_size(r: VolumeResult) -> tuple[float, float]:
    w = sum(c[1] for c in _TBL_COLS)
    rows = len(r.floors) + 2          # 見出し + 各階 + 合計
    return w + 2 * MARGIN, rows * _TBL_ROW_H + 2 * MARGIN + HEAD_ZONE


def _draw_area_table(msp, r: VolumeResult, ox: float, oy: float) -> None:
    widths = [c[1] for c in _TBL_COLS]
    total_w = sum(widths)
    rows = len(r.floors) + 2
    x0 = ox + MARGIN
    y_top = oy + MARGIN + rows * _TBL_ROW_H
    _, block_h = _table_size(r)

    _text(msp, "面 積 表", (x0, oy + block_h - MARGIN - H_HEAD), H_HEAD, "A-TEXT")

    # 罫線（TABLE エンティティは互換性が悪いので LINE で組む）
    for i in range(rows + 1):
        y = y_top - i * _TBL_ROW_H
        _line(msp, (x0, y), (x0 + total_w, y), "A-DIMS")
    x = x0
    for w in widths + [0.0]:
        _line(msp, (x, y_top), (x, y_top - rows * _TBL_ROW_H), "A-DIMS")
        x += w

    def cell(col: int, row: int, s: str, center: bool = False) -> None:
        left = x0 + sum(widths[:col])
        y = y_top - (row + 0.5) * _TBL_ROW_H
        if center:
            _text(msp, s, (left + widths[col] / 2, y), H_TABLE, "A-TEXT", TA.MIDDLE_CENTER)
        else:
            _text(msp, s, (left + widths[col] - 400.0, y), H_TABLE, "A-TEXT", TA.MIDDLE_RIGHT)

    for col, (head, _) in enumerate(_TBL_COLS):
        cell(col, 0, head, center=True)

    cumulative = 0.0
    for i, f in enumerate(r.floors, start=1):
        cumulative += f.far_area_mm2
        cell(0, i, f"{f.floor}F", center=True)
        cell(1, i, f"{f.story_height_mm / MM:.2f}")
        cell(2, i, f"{f.gross_area_mm2 / M2:,.2f}")
        cell(3, i, f"{f.far_area_mm2 / M2:,.2f}")
        cell(4, i, f"{cumulative / M2:,.2f}")

    last = rows - 1
    cell(0, last, "合計", center=True)
    cell(1, last, "-", center=True)
    cell(2, last, f"{r.total_gross_area_mm2 / M2:,.2f}")
    cell(3, last, f"{r.total_far_area_mm2 / M2:,.2f}")
    cell(4, last, f"{r.total_far_area_mm2 / M2:,.2f}")


# ---------------------------------------------------------------------------
# 表題欄・適用規定・注記
# ---------------------------------------------------------------------------


def _title_items(r: VolumeResult) -> list[tuple[str, str]]:
    z = r.input.zoning
    far_by_road = "適用なし" if r.far_by_road is None else f"{r.far_by_road * 100:.0f}%"
    return [
        ("敷地面積", f"{r.site_area_mm2 / M2:,.2f} m2"
                     f"（{r.input.site.frontage_mm / MM:.1f}m × "
                     f"{r.input.site.depth_mm / MM:.1f}m）"),
        ("用途地域", z.use_district.value),
        ("建蔽率", f"指定 {z.bcr * 100:.0f}%  /  建築面積 {r.building_area_mm2 / M2:,.2f} m2"
                   f"（{r.achieved_bcr * 100:.1f}%）"),
        ("容積率", f"指定 {z.far_designated * 100:.0f}%  /  道路幅員による {far_by_road}"
                   f"  /  実効 {r.far_effective * 100:.0f}%"
                   f"  /  達成 {r.achieved_far * 100:.1f}%"),
        ("階数", f"地上 {r.floor_count} 階"),
        ("最高高さ", f"{r.max_height_mm / MM:,.2f} m"),
        ("延床面積", f"{r.total_gross_area_mm2 / M2:,.2f} m2"
                     f"（容積対象 {r.total_far_area_mm2 / M2:,.2f} m2）"),
        ("貸室面積", f"{r.total_rentable_area_mm2 / M2:,.2f} m2"
                     f"（コア比率 {r.input.program.core_ratio * 100:.0f}%）"),
        ("規制による削減", f"{r.total_area_loss_mm2 / M2:,.2f} m2"
                            f"（制限なしなら {sum(f.unconstrained_area_mm2 for f in r.floors) / M2:,.2f} m2）"),
        ("打ち切り理由", f"{r.stop_reason.value if r.stop_reason else '-'}"),
        ("", f"（{r.stop_detail}）"),
        ("作成日時", _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]


def _footer_parts(r: VolumeResult) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    items = _title_items(r)
    rules = [f"・{rule.label}：{rule.value}　［{rule.basis}］" for rule in r.applied_rules]
    notes = [f"・{n}" for n in r.notes]
    return items, rules, notes


def _footer_size(r: VolumeResult, total_w: float) -> tuple[float, float]:
    items, rules, notes = _footer_parts(r)
    disclaimer_h = (DISCLAIMER.count("\n") + 1) * H_TEXT * 1.7
    left_h = H_HEAD * 2.0 + len(items) * H_TEXT * 1.7
    gains = r.constraint_gains_mm2()
    right_h = (H_HEAD * 2.0 + len(rules) * H_SMALL * 1.7
               + H_HEAD * 2.0 + len(notes) * H_SMALL * 1.7
               + (H_HEAD * 2.2 + len(gains) * H_SMALL * 1.7 if gains else 0.0))
    return total_w, MARGIN + disclaimer_h + GAP + max(left_h, right_h) + MARGIN


def _draw_footer(msp, r: VolumeResult, ox: float, oy: float,
                 total_w: float, height: float) -> None:
    items, rules, notes = _footer_parts(r)
    x_left = ox + MARGIN
    x_right = ox + total_w * 0.46
    left_col_w = total_w * 0.42 - MARGIN
    y_top = oy + height - MARGIN

    # --- 注記（必ず図面内に入れる） -------------------------------------------
    disclaimer_h = (DISCLAIMER.count("\n") + 1) * H_TEXT * 1.7
    msp.add_mtext(
        DISCLAIMER,
        dxfattribs={"layer": "A-TEXT", "style": JP_STYLE, "char_height": H_TEXT},
    ).set_location((x_left, oy + MARGIN + disclaimer_h))
    _rect(msp, x_left - 800, oy + MARGIN - 800,
          x_left + left_col_w, oy + MARGIN + disclaimer_h + 800, "A-TEXT")

    # --- 表題欄（左） ---------------------------------------------------------
    yy = y_top
    _text(msp, "表 題 欄", (x_left, yy), H_HEAD, "A-TEXT")
    yy -= H_HEAD * 2.0
    for label, value in items:
        if label:
            _text(msp, label, (x_left, yy), H_TEXT, "A-TEXT")
        _text(msp, f"{':' if label else ' '} {value}", (x_left + 11000, yy),
              H_TEXT, "A-TEXT")
        yy -= H_TEXT * 1.7
    _rect(msp, x_left - 800, yy + H_TEXT * 0.8, x_left + left_col_w,
          y_top + H_HEAD * 1.4, "A-TEXT")

    # --- 適用規定・未考慮事項（右） -------------------------------------------
    yy = y_top
    _text(msp, "適用した規定と根拠値", (x_right, yy), H_HEAD, "A-TEXT")
    yy -= H_HEAD * 2.0
    for line in rules:
        _text(msp, line, (x_right, yy), H_SMALL, "A-TEXT")
        yy -= H_SMALL * 1.7
    yy -= H_HEAD * 0.6
    _text(msp, "未考慮事項（いずれも安全側）", (x_right, yy), H_HEAD, "A-TEXT")
    yy -= H_HEAD * 1.6
    for line in notes:
        _text(msp, line, (x_right, yy), H_SMALL, "A-TEXT")
        yy -= H_SMALL * 1.7

    # --- ボリュームを削っている規定 -------------------------------------------
    gains = r.constraint_gains_mm2()
    if gains:
        yy -= H_HEAD * 0.6
        _text(msp, "ボリュームを削っている規定", (x_right, yy), H_HEAD, "A-TEXT")
        yy -= H_HEAD * 1.6
        for constraint, gain in gains.items():
            _text(msp,
                  f"・{constraint.value}：この制限だけを外すと 延床 +{gain / M2:,.1f} m2"
                  f"　［{constraint.basis}］",
                  (x_right, yy), H_SMALL, "A-TEXT")
            yy -= H_SMALL * 1.7
