# 既存コードの実態（current_state）

M00（2026-09-16、タグ `m00-baseline`）時点の記録。M01（2026-09-17）で 9. と 10.・11. を更新した。
本体コードは M00 では変えていない。気づいた点は「11. 食い違い・懸念」に**記録だけ**している。

---

## 1. 言語・ライブラリ・起動・テスト

| 項目 | 内容 |
|---|---|
| 言語 | Python 3.14.4（`.venv/Scripts/python.exe`）。Docker イメージは `python:3.13-slim` |
| 実行時依存（`requirements.txt`、版固定） | ezdxf 1.4.4 / shapely 2.1.2 / numpy 2.5.2 / fastapi 0.141.1 / uvicorn[standard] 0.52.4 / httpx 0.28.1 |
| 開発時のみ | pytest 9.1.1（venv に入っているが `requirements.txt` には含めない）。starlette 1.6.0 / pydantic 2.13.5 / anyio 4.15.1 は fastapi 経由 |
| 画面側 | 素の HTML / CSS / JS ＋ Leaflet 1.9.4（CDN）。ビルド工程なし |
| 起動（CLI） | `.venv/Scripts/python.exe main.py samples/case_road12.json out/case_road12.dxf [--json]` |
| 起動（Web・開発） | `.venv/Scripts/python.exe -m uvicorn web.app:app --port 8790`（`.claude/launch.json` の `volume-check`。`--reload` は効かない環境なので変更後は再起動） |
| 起動（本番） | `python -m web.app`（環境変数 `PORT`、既定 8000）。Railway が `main` への push で自動デプロイ、`/healthz` を死活監視 |
| テスト | `.venv/Scripts/python.exe -m pytest -q` → M00 着手前 318 件すべて通過（23 秒）。M00 で追加した分は「9. M00 で追加したもの」 |
| 設定ファイル | pytest.ini / pyproject.toml はない。各テストが `sys.path.insert` でリポジトリ直下を通している |

パッケージ構成はなく、`*.py` がリポジトリ直下に平置き（`web/` だけがパッケージ）。

## 2. Web 画面の構成と、画面から算定までの流れ

```
web/static/index.html + app.js（Leaflet で敷地をなぞる／フォーム）
   │  fetch
   ▼
web/app.py（FastAPI。法規ロジックなし。solve()/draw() を呼ぶだけ）
   ├ POST /api/polygon   緯度経度 → ローカル座標[m]（web/geo.py）
   ├ POST /api/rect      地図の矩形 → 間口・奥行[m]
   ├ GET  /api/zoning    緯度経度 → 用途地域等（web/zoning.py → 不動産情報ライブラリ API／ローカル GeoJSON／不明）
   ├ GET  /api/geocode   住所 → 座標（国土地理院 住所検索 API）
   ├ POST /api/solve     VolumeIn → solver.solve() → 要約 JSON ＋ SVG（断面・配置・各階平面）
   ├ POST /api/studies   → studies.generate()（複数案）＋ sweep_angles()
   ├ POST /api/skyfactor → skyfactor.evaluate()（天空率）＋ SVG
   ├ POST /api/dxf       → drawer.draw() → 一時ファイルを FileResponse で返す
   ├ GET  /api/use-districts, /api/study-defaults（選択肢・既定値の配布）
   └ GET  /healthz（solve() を 1 回通す）
```

算定モジュールの依存（矢印は import）：

```
web/app.py ─→ solver, studies, skyfactor, drawer, models, constants, web/svg_*, web/zoning, web/geo
studies.py ─→ solver, skyfactor, models, constants
skyfactor.py ─→ solver(road_setback/neighbor_setback), geometry, models, constants, numpy
solver.py ─→ geometry, models, constants
models.py ─→ geometry, constants
geometry.py ─→ shapely, numpy（shapely 依存はここだけ）
drawer.py ─→ section, plan, models, constants, ezdxf
section.py / plan.py ─→ models, geometry（DXF と SVG が共有する幾何）
```

`solver.py`・`geometry.py`・`skyfactor.py`・`studies.py` は入出力（ファイル・ネットワーク・DB・時刻・乱数）を
持たない純粋な関数群。`models.py` の `from_json_file` だけがファイルを読む。

## 3. 入力の形

`VolumeInput.from_dict()`（`models.py`）が唯一の入口。JSON の単位は m、ここで mm に換算する。

### site（敷地）

| 項目 | 型 | 既定値 | 既定の定義場所 | 内容 |
|---|---|---|---|---|
| `frontage` / `depth` / `road_width` | m | 20 / 30 / 6（API）、20 / 30 / 12（画面） | `web/app.py SiteIn`、`web/static/index.html` | 矩形敷地。`boundary` があればそちらが優先 |
| `boundary` | [[x, y], …] m | なし | — | 任意多角形の頂点列（反時計回りに揃えられる） |
| `edges` | [{kind: road/neighbor, width}] | なし | — | 辺 i（頂点 i → i+1）の種別と幅員。`boundary` と同数。道路が 1 辺以上必要 |
| `road_side` | north/east/south/west | south | `models.py SiteInput.from_dict` | 主要な前面道路の方位。矩形のときの北の向きと、断面図の見出しに使う |
| `north_angle` | 度 | `road_side` から決まる（south→90） | `models.py _NORTH_ANGLE_BY_ROAD_SIDE` | ローカル座標での北の向き（+x 軸から反時計回り） |
| `corner_lot` | bool | false | `models.py` | 角地指定（法53条3項2号） |

### zoning（法規条件）

| 項目 | 型 | 既定値 | 既定の定義場所 |
|---|---|---|---|
| `use_district` | 14 種の日本語名 | 必須 | `constants.UseDistrict` |
| `bcr` | 倍率（0.8＝80%） | 必須（画面は 80） | `index.html` |
| `far_designated` | 倍率（6.0＝600%） | 必須（画面は 600） | `index.html` |
| `height_limit_absolute` | m または null | null。低層住専・田園住居で null なら **10m を既定として適用**し notes に記録 | `constants.DEFAULT_ABSOLUTE_HEIGHT_LIMIT_M`（法55条） |
| `fire_zone` | 防火地域／準防火地域／指定なし | 指定なし | `models.py`、`constants.FireZone` |
| `shadow_regulation` | bool | false | `models.py` |

### program（計画条件）

| 項目 | 型 | 既定値 | 既定の定義場所 |
|---|---|---|---|
| `floor_height` / `gf_height` | m | 必須（画面 4.2 / 4.5） | `index.html` |
| `wall_setback` | m | 必須（画面 0.5） | `index.html` |
| `core_ratio` | 0〜1 | 必須（画面 0.18） | `index.html`。貸室面積＝床面積×(1−コア比率) |
| `max_floors` | 整数 | 必須（画面 30、API 上限 200） | `index.html`、`web/app.py` |
| `fireproof` | bool | false | `models.py` |
| `building_angle` | 度 | null（道路に平行） | `models.py` |

複数案（`/api/studies`）の探索範囲の既定は `studies.py`（`DEFAULT_ANGLES_DEG` 等、天空率ありは `SKY_*`）。
上限は `MAX_CASES`＝400、天空率判定は上位 `MAX_SKY_CHECKS`＝20 案まで。

## 4. 出力の形

### 算定結果 `VolumeResult`（`models.py`）

- 容積率：`far_designated` / `far_by_road`（12m 以上なら None） / `far_effective` / `far_road_coefficient`
- 上限：`site_area_mm2` / `max_far_area_mm2` / `max_building_area_mm2`
- 斜線の根拠値：道路（勾配・適用距離）、隣地（立ち上がり・勾配、None＝適用なし）、北側（同）、`height_limit_applied_mm`
- 建蔽率：`bcr_effective`、`bcr_relaxations`（説明, 条文）、`bcr_inset_mm`（全階一律の絞り込み量）
- 建物：`building_angle_rad`（敷地座標での絶対角）、`footprint`（絞る前の 1 階外形）
- `floors[]`：階・床レベル・階高・天端、x/y 範囲（道路基準の向き）、道路／隣地／北側の後退量、
  `governing`（表示用文字列）、`impacts[]`（規定ごとの「それだけ外したときの増分」）、`road_slant_capped`、
  `unconstrained_area_mm2`、`outline`（頂点列）、`area_mm2`
- `stop_reason`（5 種）／`stop_detail`、`applied_rules[]`（label, value, basis）、`notes[]`（未考慮事項・既定値の適用など、自由文）
- 集計は property（延床・容積対象・貸室・建築面積・最高高さ・達成率・削減量・`constraint_gains_mm2()`）

天空率 `SkyFactorStudy`（`skyfactor.py`）：境界ごと `EdgeCheck` → 算定位置ごと `PointCheck`（計画・適合・余裕）、
計画建築物（階数・高さ・延床・外形）、`suggestion`（通る外壁後退）、`verdict`、`notes`。
複数案 `StudyResult`（`studies.py`）：`cases[]`（延床順）、`baseline`、`sky_suggestion`、`notes`。

API の JSON はこれらを m・m² に換算し 2〜4 桁に丸めた要約（`web/app.py _summary/_floors`、各 `to_dict()`）。
回帰テストは丸める前の値を保存している（`tests/regression/regress.py`）。

### DXF（`drawer.py`）

1 枚のモデル空間に mm 実寸で描く。`ezdxf.new("R2010")`、`$INSUNITS`＝4（mm）。

```
+------------+   +-------------------+
|  配置図    |   |  断面図           |
|            |   +-------------------+
|            |   |  面積表           |
+------------+---+-------------------+
| 表題欄 ／ 適用した規定と根拠値 ／ 未考慮事項 ／ ボリュームを削っている規定 ／ 免責注記 |
+----------------------------------------+
```

レイヤ（`drawer.LAYERS`）：

| 名前 | 内容 | 色 | 線種 |
|---|---|---|---|
| S-SITE | 敷地境界 | 7 | CONTINUOUS |
| S-ROAD | 道路 | 8 | CONTINUOUS（中心線は CENTER_MM） |
| A-OUTL | 建物外形 | 2 | CONTINUOUS（最上階は DASHED_MM） |
| A-SLNT | 斜線 | 1 | DASHDOT_MM |
| A-DIMS | 寸法・面積表の罫線・レベル引出線 | 3 | CONTINUOUS |
| A-TEXT | 文字・方位記号・表題欄の枠 | 7 | CONTINUOUS |
| A-ENVL | 斜線・建蔽率がない場合の範囲 | 8 | DASHED_MM |

寸法を `render()` すると `Defpoints` レイヤも生成される。文字スタイル `JP-GOTHIC`（msgothic.ttc）、
寸法スタイル `JP-MM`。表題欄に「作成日時」（実行時刻）を印字する。
`DISCLAIMER`（免責注記）の文言は「矩形敷地のみ対応」「天空率…未考慮」のまま（→ 11.）。

SVG（`web/svg_section.py`・`web/svg_plan.py`）は `section.py`・`plan.py` の幾何を DXF と共有する。

## 5. 法規定数の場所と、条文番号のない定数

法規の数値は `constants.py` に集約されており、値ごとに条文コメントがある：
用途地域（法48条）、別表第三の行区分、道路斜線の勾配・適用距離（法56条1項1号・別表第三）、
容積率の道路幅員係数（法52条2項）、隣地斜線（法56条1項2号）、北側斜線（法56条1項3号）、
防火地域（法61条）、建蔽率の緩和（法53条3項・6項、`relaxed_bcr()`）、絶対高さ制限の既定（法55条）、
天空率の算定位置（令135条の9・10）。

**条文番号が付いていない定数（法規定数ではないもの）**：

| 場所 | 定数 | 値 | 性格 |
|---|---|---|---|
| constants.py | `MIN_VIABLE_FLOOR_AREA_M2` | 100 | 運用上の打ち切りしきい値 |
| constants.py | `M_TO_MM` / `M2_TO_MM2` | 1000 / 1e6 | 単位換算 |
| solver.py | `_AREA_EPS_MM2` / `_LENGTH_EPS_MM` | 1 mm² / 1e-6 mm | 丸め誤差の許容 |
| geometry.py | `_QUAD_SEGS` | 32 | 凹頂点の円の分割数 |
| geometry.py | `largest_inscribed_rectangle(grid_mm=250)`、格子上限 400,000 | 250 mm | 最大内接矩形の格子刻み（近似） |
| geometry.py | `north_slant_region(step_mm=500)` | 500 mm | 凹敷地の北側収縮の刻み（近似） |
| skyfactor.py | `_AZIMUTH_DIVISIONS` / `_SEARCH_DIVISIONS` | 2880 / 720 | 方位角の分割（数値積分） |
| skyfactor.py | `_ENVELOPE_LAYER_MM` | 1000 | 適合建築物の層厚 |
| skyfactor.py | `_SKY_FACTOR_EPS` | 1e-5 | 同等とみなす幅 |
| skyfactor.py | `NEIGHBOR_ENVELOPE_CAPPED` | True | 隣地の適合建築物を頭打ちにする（条文から一義に読めず厳しい側。`docs/decisions/0001`） |
| skyfactor.py | `_SEARCH_STEP_MM` / `_SEARCH_STEPS` | 500 / 10 | 通る外壁後退の探索範囲 |
| studies.py | `DEFAULT_*` / `SKY_*` / `MAX_CASES` / `MAX_SKY_CHECKS` | — | 探索範囲と上限 |
| drawer.py | 文字高・余白・レイヤ・線種ピッチ | — | 作図寸法 |
| web/app.py | `SiteIn` の既定値、`max_floors ≦ 200`、`limit` 既定 20、候補数の上限 | — | 画面・API の既定 |
| web/zoning.py | `REINFOLIB_ZOOM`＝15、`REQUEST_TIMEOUT_S`＝10、`YOUTO_ID_TO_DISTRICT` | — | 外部データの読み方 |

`constants.py` 以外で条文を**文字列として**持つ場所：`models.Constraint.basis`（規定名→条文の表示用対応）、
`solver._record_applied_rules`（applied_rules の basis 文字列）、`web/zoning._add_warnings`（警告文中の条文）。
数値の定義ではないが、条文表記の一元管理としては分散している。

## 6. 座標系・単位・真北

- **単位**：入力 JSON は m、内部は mm（面積 mm²）。換算は `VolumeInput.from_dict()` で 1 回。
  角度は入力が度、内部はラジアン（`north_angle_rad`、`building_angle_rad`）。
- **敷地ローカル座標**（`geometry.py`）：(x, y) mm、多角形は反時計回りに揃える。
  - 矩形入力：`rectangle_site()` が (0,0)-(frontage,0) を道路境界（y＝0）とし、y が敷地の奥へ向かう。
  - 多角形入力：頂点をそのまま使う（原点は与えられたまま）。地図からなぞった場合は `/api/polygon` が
    **+x＝東、+y＝北**のローカル座標[m]（原点は頂点の重心付近）を返し、`north_angle`＝90 を付ける。
- **真北**：`SiteInput.north_angle_rad`＝ローカル座標での北の向き（+x 軸から反時計回り）。
  矩形入力では `road_side` から決まる（南道路→+y が北）。北側斜線はこの向きに敷地を収縮させる
  （`geometry.north_slant_region`）。作図では `plan.Frame` がこの角度で回転し「北が上」にする。
- **建物の向き**：`VolumeResult.building_angle_rad` は敷地座標での絶対角。入力の `building_angle` は
  前面道路（最大幅員の辺）の向きを 0 とした相対角で、`road_edge.angle_rad + building_angle_rad`。
- **断面の座標**（`section.py`）：(y, z)。y＝−道路幅員 が道路の反対側境界、y＝0 が道路境界、z＝GL からの高さ。
  各階の `x_min/x_max/y_min/y_max` は前面道路の向きへ射影した範囲（道路境界を y＝0 とする）。
- **高さ**：GL＝0、平坦な敷地前提。天空率の算定位置の高さも GL。

## 7. 外部 API を呼んでいる箇所

| 箇所 | 相手 | 用途 | solver から呼ぶか |
|---|---|---|---|
| `web/zoning.py _from_reinfolib` | 不動産情報ライブラリ API（XKT002/014/001/023/024、要 `REINFOLIB_API_KEY`） | 用途地域・建蔽率・容積率・防火地域・区域区分・地区計画・高度利用地区 | **呼ばない**（画面の入力補助のみ） |
| `web/zoning.py _from_local` | `web/data/*.geojson`（ローカル） | 用途地域（API キーなしの代替） | 呼ばない |
| `web/app.py geocode` | 国土地理院 住所検索 API | 住所→座標 | 呼ばない |
| `web/static/index.html` | Leaflet CDN・地図タイル | 画面 | 呼ばない |

算定（`solve`・`evaluate`・`generate`）は入力 JSON だけで完結する。取得できない項目（高度地区・道路幅員・
道路種別・日影規制の指定）は `zoning.UNAVAILABLE_ITEMS` として画面に出し、手入力にしている。

## 8. 決定的でない箇所

| 箇所 | 内容 | 結果への影響 |
|---|---|---|
| `drawer._title_items` | 表題欄「作成日時」に `datetime.now()` を印字 | DXF の文字 1 つが毎回変わる。回帰テストでは比較から外した（本体は変えない） |
| ezdxf | `$FINGERPRINTGUID`／`$VERSIONGUID`（乱数）と `ezdxf` の書き出し時刻をヘッダに書く | DXF のバイト列は毎回異なる。図形の内容は同じ（M00 で 2 回書き出して差分がこの 4 行だけであることを確認） |
| `web/app.py api_dxf` | `tempfile.mkdtemp` の一時ディレクトリ名 | 応答内容には影響しない |
| `studies.generate` | `set` は重複判定のみ、並べ替えは安定ソート（延床降順→高さ昇順）。同点の案は探索順（角度→階高→後退→耐火）で決まる | 決定的 |
| `geometry.largest_inscribed_rectangle` | 格子探索。同面積の候補があれば先に見つかったもの | 決定的だが、格子刻み（250mm）に依存する近似 |
| 算定全般 | 乱数・時刻・並列なし。shapely / numpy は決定的 | 回帰テストを 2 回続けて実行し一致することを確認 |

## 9. M00 で追加したもの

本体コードは変えていない。`git diff --stat m00-baseline~1 m00-baseline` は `docs/`・`tests/`・`CLAUDE.md` のみ。

- `tests/regression/`：14 ケース（算定 13 ＋ 外部 API 1）。`README.md` に性質との対応表。
  実行 `pytest tests/regression`（約 5 秒）。ネットワーク遮断（`conftest.py`）。
- `tests/fixtures/`：外部 API の**模擬**応答（実キーがなかったため。`tests/fixtures/README.md`）。
- `tests/cases/`：`tests/test_solver.py` の手計算ケース 2 件を calc.md / expected.json / input.json に写した。
  `tests/test_cases.py` が照合する。元のテストは残している。
- 依存ライブラリの追加・更新はなし。

### M01 で追加したもの（2026-09-17）

solver・既存の入出力は無変更。回帰テスト全件通過。

- `model/`：`types.py`（FactValue / PlanValue / SiteFacts / PlanConditions / Scheme / StageResult /
  AppliedRule / NotConsidered / Level / GridAxis。pydantic v2）、`hashing.py`（正規化 JSON と SHA-256）、
  `patch.py`（差分の検査。`chat`・`batch` からの敷地事実の変更を拒否）、`legacy.py`（既存入力との相互変換）。
- `store/`：`sqlite.py` の `SchemeStore`（schemes / stage_results。save・get・derive・list）。
- `tests/test_model.py`・`tests/test_store.py`（69 件）。
- `requirements.txt` に `pydantic==2.13.5` を明示（fastapi 経由で同じ版が入っていた）。
- 決めたことは `docs/decisions/0003-m01-data-model.md`。`docs/architecture.md` §4.1・4.3・4.4・4.5 を更新。

## 10. `docs/architecture.md` との対応表（現在の場所 → 目標の場所）

| 現在 | 目標（architecture.md §3） | 備考 |
|---|---|---|
| `constants.py` | `solver/law/constants.py` | そのまま移せる。`relaxed_bcr()` は関数だが法規定数の一部として扱う |
| `models.py`（入力 dataclass） | `model/`（SiteFacts / PlanConditions） | M01 で `model/types.py` を追加。既存 dataclass との橋渡しは `model/legacy.py`。M02 で算定を包む |
| `models.py`（VolumeResult / FloorResult） | `solver/volume/`（段の結果の型）＋ `model/`（StageResult への包み） | applied_rules / notes の形が違う（下記） |
| `geometry.py` | `solver/volume/geometry.py` | |
| `solver.py` | `solver/volume/` | |
| `skyfactor.py` | `solver/volume/` | |
| `studies.py` | `api/`（複数案＝親案から子案を並べて作る操作）＋ `solver/volume/` | 現在は solver 層に総当たりと天空率の付与が同居 |
| `section.py` / `plan.py` | `drawing/model/` | DXF と SVG が共有する幾何 |
| `drawer.py` | `drawing/export/dxf.py`、`LAYERS` → `drawing/layers.py` | |
| `web/svg_*.py` | `drawing/export/svg.py`（または `web/`） | |
| `web/app.py`（API 部分） | `api/` | 現在は画面配信と API が同じモジュール |
| `web/static/` | `web/` | |
| `web/zoning.py` / `web/geo.py` | `data/` | 出典・取得日は現在 `source` 文字列のみ |
| `main.py` | （CLI。目標構成に該当なし） | |
| `samples/` | `tests/cases/` または docs の例 | 回帰ケースの入力としても使用 |
| `docs/HANDOFF.md` / `docs/PROJECT_INSTRUCTIONS.md` | （目標構成に該当なし） | 経緯の記録として残す |
| `tests/*.py` | `tests/` | |
| `model/`, `store/`（M01 で作成） | `model/`, `store/` | |
| （なし） | `tools/`, `api/`, `data/`, `drawing/model/`, `solver/{estimate,core,grid,site}` | 未作成 |

## 11. `docs/architecture.md` と食い違う点・気づいた懸念（修正はしない）

### 食い違い

1. **レイヤ接頭辞**：既存は敷地・道路に `S-SITE`・`S-ROAD` を使う。architecture §7 は `S-` を構造用に予約。
   M03 で対応を決める必要がある。
2. **座標系の約束**：~~architecture §4.1 と既存が異なっていた~~ → M01 で既存を正とし §4.1 を書き直した（解消）。
3. **角度の単位**：architecture は「角度は度」。既存は入力 度・内部 ラジアン。
4. **AppliedRule の形**：既存は (label, value, basis) の表示用文字列。architecture §4.6 は
   rule_id・article・summary・effect_mm2。`rule_id` に相当するものはない（`Constraint` 列挙が近い）。
5. **未考慮事項の形**：既存は `notes: list[str]`（自由文）。architecture は `NotConsidered(item, direction, note)`。
   「実際より大きく出る（危険側）」の区別は文中の【要注意】表記のみ。
6. **値の出所**：既存の入力に FactValue / PlanValue はない → M01 で `model/` に導入。既存入力との対応は
   `model/legacy.py`。既存側の既定値は引き続き `web/app.py`（pydantic）、`index.html`、`models.py`、
   `constants.py`（絶対高さ 10m）に分散し、画面と API で一部異なる（`road_width` 既定：画面 12、API 6）。
   M02 で既存の入口を `model/` 経由に寄せるまでは二重に存在する。
7. **敷地事実の項目**：→ M01 で §4.3 の表を既存項目込みで確定（`road_side`・`corner_lot`・`shadow_regulation` を追加、
   `height_district`・`district_plan`・道路種別は unavailable 既定）。`web/zoning.py` の地区計画・高度利用地区の
   警告を `district_plan` に流し込むのは M02 以降。
8. **計画条件**：→ M01 で §4.4 の表を確定（`core_ratio`・`max_floors`・`gf_height` を追加）。
9. **段の結果の保存**：→ M01 で `store/`（SQLite）を作成。`solver_version` の決め方は M02。
10. **依存の向き**：`studies.py`（solver 層）が `skyfactor` を呼ぶのは可。`web/app.py` が solver を直接呼ぶ
    （`api/` 層がない）。`tools/` はない。

### 懸念・不一致（記録のみ）

- `drawer.DISCLAIMER` と `web/app.py` の `description`、`main.py` の argparse 説明が「矩形敷地のみ」
  「天空率…未考慮」のまま。任意形状・天空率に対応した現状と合っていない（図面に印字される）。
- 防火地域で耐火にすると建蔽率が緩和され建築面積は増えるが、板を絞らず階を切り捨てる仕様のため延床が減る
  （`fire_zone_fireproof_on`：6 階 3188m² ＜ 非耐火 9 階 3556m²）。既知（`docs/decisions/0001`）。
- `studies.generate` は成立しない組み合わせの `ValueError` を黙って飛ばす。入力の誤りも同じ経路で消える。
- 天空率の `suggestion` は方位分割 720 で探索し、その値で報告する（本判定は 2880）。
- `Dockerfile` は Python 3.13、開発 venv は 3.14。
- `.claude/launch.json` は `--reload` 付きだが効かない（CLAUDE.md に記載あり）。
- `web/data/` の GeoJSON は追跡外。ローカル用途地域データを置いた環境でだけ動く経路がある。
- 隣地斜線の適合建築物の頭打ち（`NEIGHBOR_ENVELOPE_CAPPED`）は条文から一義に読めず厳しい側を既定にしている。
  決定の記録は `docs/decisions/0001-initial-decisions.md`。
