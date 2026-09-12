"""STEP 1: ezdxf の最小検証。

「コードが通っても図面が壊れる」ことを避けるため、本計算に入る前に
以下 5 点が実際に DXF ファイルとして成立することを確認する。

  1. 10000 x 20000 mm の矩形 (LWPOLYLINE)
  2. 水平寸法線 1 本 (add_linear_dim(...).render() を必ず呼ぶ)
  3. 日本語テキスト「敷地境界線」(TrueType フォントを明示指定)
  4. 一点鎖線のレイヤー 1 つ (線種テーブルへの登録込み)
  5. doc = ezdxf.new("R2010", setup=True)

生成物 volume_check/out/smoke.dxf を人が CAD で開いて目視確認する。
"""

from pathlib import Path

import ezdxf
from ezdxf.audit import Auditor

OUT_DIR = Path(__file__).resolve().parent.parent / "out"
OUT_PATH = OUT_DIR / "smoke.dxf"

# 日本語表示用の TrueType フォント。
# ezdxf は文字を描かず「フォント名」を DXF に書くだけなので、
# ここを指定しないと CAD 側が SHX フォント (txt.shx) にフォールバックし
# 日本語が確実に文字化けする。
JP_FONT = "msgothic.ttc"          # MS ゴシック (Windows 標準)
JP_TEXT_STYLE = "JP-GOTHIC"

# 一点鎖線。標準の CENTER は図面単位 1 = 1m 想定のピッチなので、
# mm 単位の図面ではダッシュが潰れて見える。mm 用に自前で登録する。
CENTER_MM = "CENTER_MM"

RECT_W = 10000.0   # mm
RECT_H = 20000.0   # mm


def build_smoke_doc():
    doc = ezdxf.new("R2010", setup=True)

    # --- 図面単位 -------------------------------------------------------
    doc.header["$INSUNITS"] = 4      # 4 = ミリメートル
    doc.header["$MEASUREMENT"] = 1   # 1 = メートル法
    doc.header["$LTSCALE"] = 1.0     # 線種ピッチは mm 実寸で定義済み

    # --- 線種テーブルへの登録 -------------------------------------------
    # pattern = [全長, 線, 空白(負), 点, 空白(負)]  単位 mm
    doc.linetypes.add(
        CENTER_MM,
        pattern=[40.0, 25.0, -5.0, 0.0, -5.0],
        description="Center(mm) ____ . ____ . ____",
    )

    # --- テキストスタイル (TrueType 明示) --------------------------------
    doc.styles.add(JP_TEXT_STYLE, font=JP_FONT)

    # --- 寸法スタイル (mm 図面用に実寸で各サイズを指定) ------------------
    ds = doc.dimstyles.add("JP-MM")
    ds.dxf.dimtxsty = JP_TEXT_STYLE
    ds.dxf.dimtxt = 250.0    # 寸法文字高さ
    ds.dxf.dimexe = 125.0    # 寸法補助線の寸法線からの出
    ds.dxf.dimexo = 100.0    # 寸法補助線の起点からの離れ
    ds.dxf.dimgap = 80.0     # 文字と寸法線のすき間
    ds.dxf.dimdec = 0        # 小数点以下 0 桁
    ds.dxf.dimlunit = 2      # 十進表記
    ds.dxf.dimtad = 1        # 文字は寸法線の上
    ds.dxf.dimscale = 1.0    # 実寸指定なので倍率は 1
    ds.dxf.dimasz = 200.0    # 矢印(斜線)の大きさ
    ds.set_arrows(blk=ezdxf.ARROWS.architectural_tick)

    # --- レイヤー -------------------------------------------------------
    doc.layers.add("S-SITE", color=7).dxf.lineweight = 50   # 0.50mm 太線
    doc.layers.add("S-CNTR", color=4, linetype=CENTER_MM)
    doc.layers.add("A-DIMS", color=3)
    doc.layers.add("A-TEXT", color=7)

    msp = doc.modelspace()

    # --- (1) 矩形 -------------------------------------------------------
    msp.add_lwpolyline(
        [(0, 0), (RECT_W, 0), (RECT_W, RECT_H), (0, RECT_H)],
        close=True,
        dxfattribs={"layer": "S-SITE"},
    )

    # --- (4) 一点鎖線 (敷地の中心線) -------------------------------------
    msp.add_line(
        (RECT_W / 2, -2000), (RECT_W / 2, RECT_H + 2000),
        dxfattribs={"layer": "S-CNTR"},
    )

    # --- (2) 水平寸法線 -------------------------------------------------
    dim = msp.add_linear_dim(
        base=(0, -2000),                 # 寸法線を通す位置
        p1=(0, 0),
        p2=(RECT_W, 0),
        dimstyle="JP-MM",
        dxfattribs={"layer": "A-DIMS"},
    )
    dim.render()                          # ← これを呼ばないと図形が生成されない

    # --- (3) 日本語テキスト ----------------------------------------------
    msp.add_text(
        "敷地境界線",
        height=400.0,
        dxfattribs={"layer": "A-TEXT", "style": JP_TEXT_STYLE},
    ).set_placement((0, RECT_H + 600))

    msp.add_mtext(
        "MTEXT による日本語表示テスト\n寸法単位: mm",
        dxfattribs={"layer": "A-TEXT", "style": JP_TEXT_STYLE, "char_height": 300.0},
    ).set_location((0, -3500))

    return doc


def test_dxf_smoke():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_smoke_doc().saveas(OUT_PATH)
    assert OUT_PATH.exists(), "DXF が書き出されていない"

    # 書いたものを読み直して検証する（保存時に壊れるパターンを潰すため）
    doc = ezdxf.readfile(OUT_PATH)
    auditor = Auditor(doc)
    auditor.run()
    assert not auditor.errors, f"DXF audit errors: {auditor.errors}"

    msp = doc.modelspace()

    # (1) 矩形
    plines = msp.query("LWPOLYLINE[layer=='S-SITE']")
    assert len(plines) == 1
    pts = [(p[0], p[1]) for p in plines[0].get_points("xy")]
    assert plines[0].closed is True
    assert set(pts) == {(0, 0), (RECT_W, 0), (RECT_W, RECT_H), (0, RECT_H)}

    # (2) 寸法：DIMENSION 本体と、render() で生成された図形ブロックの両方
    dims = msp.query("DIMENSION")
    assert len(dims) == 1
    geom_block = doc.blocks.get(dims[0].dxf.geometry)
    assert len(geom_block) > 0, "render() が寸法図形を生成していない"
    assert dims[0].dxf.dimstyle == "JP-MM"

    # (3) 日本語テキストと TrueType フォント指定
    texts = msp.query("TEXT[layer=='A-TEXT']")
    assert len(texts) == 1
    assert texts[0].dxf.text == "敷地境界線"
    assert texts[0].dxf.style == JP_TEXT_STYLE
    assert doc.styles.get(JP_TEXT_STYLE).dxf.font == JP_FONT

    mtexts = msp.query("MTEXT[layer=='A-TEXT']")
    assert len(mtexts) == 1
    assert "日本語" in mtexts[0].text

    # (4) 一点鎖線レイヤーと線種テーブル登録
    assert CENTER_MM in doc.linetypes
    assert doc.layers.get("S-CNTR").dxf.linetype == CENTER_MM
    assert len(msp.query(f"LINE[layer=='S-CNTR']")) == 1

    # (5) バージョン
    assert doc.dxfversion == "AC1024", "R2010 で作成されていない"


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_smoke_doc().saveas(OUT_PATH)
    print(f"wrote {OUT_PATH}")


# ---------------------------------------------------------------------------
# drawer.draw() の出力検証（STEP 6）
# ---------------------------------------------------------------------------

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from drawer import DISCLAIMER, JP_FONT, JP_STYLE, LAYERS, draw  # noqa: E402
from models import VolumeInput  # noqa: E402
from solver import solve  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.mark.parametrize("sample", ["case_road6", "case_road12"])
def test_draw_produces_valid_dxf(sample, tmp_path):
    result = solve(VolumeInput.from_json_file(SAMPLES / f"{sample}.json"))
    path = tmp_path / f"{sample}.dxf"
    draw(result, path)

    doc = ezdxf.readfile(path)
    auditor = Auditor(doc)
    auditor.run()
    assert not auditor.errors, f"DXF audit errors: {auditor.errors}"

    # 仕様のレイヤーがすべて定義され、指定の色・線種になっている
    for name, _, color, linetype, _ in LAYERS:
        assert name in doc.layers, f"レイヤー {name} がない"
        layer = doc.layers.get(name)
        assert layer.color == color
        assert layer.dxf.linetype == linetype
        assert layer.dxf.linetype in doc.linetypes, f"{linetype} が線種テーブルにない"

    msp = doc.modelspace()
    # 各図が描かれている
    assert len(msp.query("LWPOLYLINE[layer=='S-SITE']")) >= 1, "敷地境界がない"
    assert len(msp.query("LWPOLYLINE[layer=='S-ROAD']")) >= 1, "道路がない"
    assert len(msp.query("LWPOLYLINE[layer=='A-SLNT']")) >= 1, "斜線がない"
    # 建物外形: 配置図の1階・最上階 + 断面図の各階
    assert len(msp.query("LWPOLYLINE[layer=='A-OUTL']")) == result.floor_count + 2
    assert len(msp.query("DIMENSION")) >= 3, "寸法が足りない"

    # 日本語テキストは TrueType スタイル
    assert doc.styles.get(JP_STYLE).dxf.font == JP_FONT
    for t in msp.query("TEXT MTEXT"):
        assert t.dxf.style == JP_STYLE, f"{t.dxftype()} が日本語スタイルでない"

    # 注記が図面内に必ず入っている
    mtexts = [m.text for m in msp.query("MTEXT")]
    assert any("確認申請には使用できません" in m for m in mtexts), "注記がない"
    assert any(DISCLAIMER.splitlines()[0] in m for m in mtexts)

    # 表題欄の主要項目
    texts = [t.dxf.text for t in msp.query("TEXT")]
    for label in ("敷地面積", "用途地域", "建蔽率", "容積率", "階数", "最高高さ",
                  "延床面積", "作成日時", "面 積 表", "配 置 図"):
        assert any(label in t for t in texts), f"表題欄/図名に「{label}」がない"


def test_draw_handles_zero_floors(tmp_path):
    """階数0でも例外なく図面が書ける。"""
    import json

    data = json.loads((SAMPLES / "case_road6.json").read_text(encoding="utf-8"))
    data["site"]["frontage"] = 8.0
    data["site"]["depth"] = 10.0
    result = solve(VolumeInput.from_dict(data))
    assert result.floor_count == 0

    path = tmp_path / "zero.dxf"
    draw(result, path)
    doc = ezdxf.readfile(path)
    assert not Auditor(doc).run()
    texts = [t.dxf.text for t in doc.modelspace().query("TEXT")]
    assert any("成立しません" in t for t in texts)


@pytest.mark.parametrize("road_side", ["north", "east", "south", "west"])
def test_draw_handles_all_road_sides(road_side, tmp_path):
    """4方位すべてで作図でき、建物ラベルが建物外形の内側に入る。

    配置図は方位によって回転するため、ラベル位置を図面座標で決めているかの回帰テスト。
    """
    import json

    import plan as P
    from drawer import _plan_local_bbox

    data = json.loads((SAMPLES / "case_road12.json").read_text(encoding="utf-8"))
    data["site"]["road_side"] = road_side
    result = solve(VolumeInput.from_dict(data))

    path = tmp_path / f"{road_side}.dxf"
    draw(result, path)
    doc = ezdxf.readfile(path)
    assert not Auditor(doc).run()

    # 1階外形ラベルが 1 階の外形内にあること
    frame = P.Frame(result.input.site.north_angle_rad, 0.0, 0.0,
                    _plan_local_bbox(result))
    f1 = result.floors[0]
    bx0, by0, bx1, by1 = frame.bbox(f1.outline)
    label = next(t for t in doc.modelspace().query("TEXT") if t.dxf.text.startswith("1F 外形"))
    # draw() は配置図をシート内へ平行移動するので、相対位置で比較する
    site_pl = min(
        doc.modelspace().query("LWPOLYLINE[layer=='S-SITE']"),
        key=lambda e: min(p[1] for p in e.get_points("xy")),
    )
    sx0 = min(p[0] for p in site_pl.get_points("xy"))
    sy0 = min(p[1] for p in site_pl.get_points("xy"))
    ref_x0, ref_y0, _, _ = frame.bbox(
        tuple(result.input.site.shape.polygon.exterior.coords)[:-1])
    dx, dy = sx0 - ref_x0, sy0 - ref_y0
    lx, ly = label.dxf.insert.x - dx, label.dxf.insert.y - dy
    assert bx0 <= lx <= bx1, f"{road_side}: 1F ラベルが外形の外(x)"
    assert by0 <= ly <= by1, f"{road_side}: 1F ラベルが外形の外(y)"


@pytest.mark.parametrize(
    "district,far,bcr",
    [
        ("第一種低層住居専用地域", 1.0, 0.5),
        ("第一種中高層住居専用地域", 2.0, 0.6),
        ("第一種住居地域", 3.0, 0.6),
        ("近隣商業地域", 4.0, 0.8),
        ("商業地域", 8.0, 0.8),
        ("工業地域", 2.0, 0.6),
        ("指定なし", 2.0, 0.7),
    ],
)
def test_draw_handles_every_district(district, far, bcr, tmp_path):
    """用途地域を一通り変えても作図できる（隣地斜線なし・図の範囲外の分岐を含む）。"""
    import json

    data = json.loads((SAMPLES / "case_road6.json").read_text(encoding="utf-8"))
    data["zoning"].update(use_district=district, far_designated=far, bcr=bcr)
    result = solve(VolumeInput.from_dict(data))

    path = tmp_path / "d.dxf"
    draw(result, path)
    doc = ezdxf.readfile(path)
    assert not Auditor(doc).run()
    assert len(doc.modelspace().query("LWPOLYLINE[layer=='A-SLNT']")) >= 1


# ---------------------------------------------------------------------------
# 開いたときに図面全体が見えること
# ---------------------------------------------------------------------------


def test_the_drawing_extents_and_initial_view_cover_every_entity(tmp_path):
    """$EXTMIN/$EXTMAX・$LIMMIN/$LIMMAX・*Active ビューポートが図形全体を含む。

    図形は mm 実寸で幅 10 万 mm を超えるのに、既定のままだと $LIMMAX が A3 の紙寸
    (420, 297) で、CAD ビューアで開くと左下の空白だけが表示され「中身が無い」ように
    見えていた。
    """
    from ezdxf import bbox
    from ezdxf.math import Vec3

    for name in ("case_road12", "case_polygon"):
        result = solve(VolumeInput.from_json_file(SAMPLES / f"{name}.json"))
        path = tmp_path / f"{name}.dxf"
        draw(result, path)
        doc = ezdxf.readfile(path)
        msp = doc.modelspace()
        ext = bbox.extents(msp)
        assert ext.has_data
        extmin, extmax = Vec3(doc.header["$EXTMIN"]), Vec3(doc.header["$EXTMAX"])
        assert extmin.isclose(ext.extmin, abs_tol=1.0), name
        assert extmax.isclose(ext.extmax, abs_tol=1.0), name

        limmin, limmax = doc.header["$LIMMIN"], doc.header["$LIMMAX"]
        assert limmin[0] <= ext.extmin.x and limmin[1] <= ext.extmin.y, name
        assert limmax[0] >= ext.extmax.x and limmax[1] >= ext.extmax.y, name
        # 紙寸の既定値 (420, 297) のままではない
        assert limmax[0] > 10_000 and limmax[1] > 10_000, name

        vport = doc.viewports.get("*Active")[0]
        cx, cy = vport.dxf.center.x, vport.dxf.center.y
        assert abs(cx - (ext.extmin.x + ext.extmax.x) / 2) < 1.0, name
        assert abs(cy - (ext.extmin.y + ext.extmax.y) / 2) < 1.0, name
        assert vport.dxf.height >= ext.extmax.y - ext.extmin.y, name
