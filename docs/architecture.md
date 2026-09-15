# アーキテクチャ

2026-09-15 版。既存コードの実態は M00 で `docs/current_state.md` に記録する。この文書と食い違う点は、M00 の報告を受けてこの文書を直すか、移行のマイルストーンで既存を寄せる。どちらにするかはユーザーが決める。

---

## 1. 範囲

| 範囲に入る | 範囲に入らない |
|---|---|
| 用地候補の評価（後で追加） | 売り物件情報の取得 |
| 法規条件の取得・入力、ボリューム算定、複数案 | 確認申請図書 |
| 概算工事費（企画段階・幅で出す） | 見積書 |
| コア計画、柱グリッド、階高 | 構造計算、設備設計 |
| 客先向け簡易図面（PDF）、設計者向け DXF | 実施設計図、総合図そのもの |
| 構造・設備・総合図作成者への基準データ | 天井割・機器配置（第3段の後に検討） |

## 2. 層の構成

```
web/        画面（地図・チャット・図面ビュー）
  ↓ HTTP
api/        案の作成・派生、段の実行、図面の取り出し
  ↓                     ↓
solver/     算定         store/   案と結果の保存（SQLite）
  ↓
model/      データモデル（型・正規化・ハッシュ・差分の検査）
drawing/    図面モデル → DXF / PDF /（将来）IFC
data/       外部データの取得（出典・取得日つき）
tools/      LLM 用ツール定義（MCP と Web チャットで共用）
```

依存の向きは次のとおりにする。

- `solver/` が import してよいのは `model/` だけ。`api/`・`store/`・`data/`・`web/`・`tools/` を import しない。
- `drawing/` は `model/` の結果を読むだけで、算定しない。
- `tools/` は `api/` を呼ぶだけで、solver を直接呼ばない。
- 依存の向きはテストで検査する（M02 で導入）。

## 3. フォルダ構成（目標）

```
CLAUDE.md
docs/
  project_brief.md       背景
  architecture.md        この文書
  current_state.md       既存コードの実態（M00 で作成、以後更新）
  decisions/             決定の記録（NNNN-題名.md）
  milestones/            作業単位
model/
solver/
  law/                   法規定数（constants.py、条文番号つき）
  volume/                ボリューム算定（既存を包む）
  estimate/              概算（M05）
  core/                  コア計画（M06）
  grid/                  柱グリッド・階高（M07）
  site/                  用地の一括評価（後で追加）
drawing/
  model/                 図面モデル
  export/                dxf.py, pdf.py
  layers.py              レイヤ規約
data/
store/
api/
tools/
web/
tests/
  cases/                 手計算ケース（計算過程の Markdown ＋ 期待値 JSON）
  regression/            既存結果の固定（M00）
  fixtures/              外部 API の記録済み応答
```

既存コードの場所は、M00 で「現在の場所 → 目標の場所」の対応表にまとめる。移動は必要になったマイルストーンで行い、まとめて移さない。

## 4. データモデル

### 4.1 共通の約束

- **単位**：長さは mm（float）、面積は mm²（float）、角度は度。
- **座標系**：既存コードの座標系を M00 で記録し、それを正とする。既存に決まりがない場合だけ、次を採用する。
  - 敷地ローカルの直交座標。X＝座標東、Y＝座標北。
  - 原点は敷地ポリゴンの第1頂点。
  - 真北は敷地事実 `true_north_deg` に持つ（座標北から真北への角度、時計回りを正）。
- **高さ**：設計 GL＝0 の平坦な敷地を前提にする。高低差のある敷地は未対応として `not_considered` に出す。
- **建物座標**：建物は敷地座標に対する原点と振り角（`rotation_deg`、道路平行＝0）を持つ。通り芯は建物座標で表す。

### 4.2 値の出所

すべての敷地事実と計画条件は、値そのものと一緒に出所を持つ。

```python
class FactValue:            # 敷地事実（敷地で決まり、人が確かめる）
    value: Any | None
    source: Literal["api", "manual", "drawn", "unavailable"]
    source_name: str | None   # 例：「不動産情報ライブラリ API」
    retrieved_at: datetime | None
    note: str | None

class PlanValue:            # 計画条件（設計者が選ぶ）
    value: Any
    origin: Literal["default", "designer"]
    note: str | None
```

- 取得できない事実は `source="unavailable"`、`value=None` とする。**推測値で埋めない。**
- 計画条件の既定値は既存コードの既定値を使い、`origin="default"` とする。新しい既定値を決めるときはユーザーに確認し、`docs/decisions/` に記録する。

### 4.3 敷地事実 `SiteFacts`

| 項目 | 内容 |
|---|---|
| `boundary` | 敷地ポリゴン（頂点列 mm） |
| `edges` | 辺ごとの種別（道路／隣地）、道路なら幅員・道路種別 |
| `true_north_deg` | 真北 |
| `use_zone` | 用途地域 |
| `coverage_ratio` / `far_ratio` | 指定建蔽率・指定容積率（倍率） |
| `fire_zone` | 防火地域・準防火地域 |
| `height_district` | 高度地区（手入力、なければ unavailable） |
| `shadow_regulation` | 日影規制の指定（手入力、なければ unavailable） |
| `absolute_height_limit` | 絶対高さ制限 |
| `district_plan` | 地区計画（手入力、なければ unavailable） |

既存の入力にある項目は必ず含める。上の表にない既存項目は、M01 で追加してこの表を更新する。

### 4.4 計画条件 `PlanConditions`

振り角、階高、外壁後退、耐火の別、コア比率（コア計画ができるまでの仮定）、構造種別（M05 以降）、コア方式（M06 以降）、単価表の版（M05 以降）など。項目は既存の入力から M01 で確定する。

### 4.5 案 `Scheme`

```python
class Scheme:
    id: str                    # UUID
    parent_id: str | None
    site_id: str               # 同じ敷地の案をまとめる
    created_at: datetime
    created_by: Literal["ui", "chat", "batch"]
    label: str | None
    site_facts: SiteFacts
    plan_conditions: PlanConditions
    input_hash: str            # site_facts ＋ plan_conditions の正規化 JSON の SHA-256
```

- 案は保存後に書き換えない。
- 変更は「親案 ＋ 差分（patch）」から新しい案を作る（`derive`）。
- `created_by="chat"` の差分が `site_facts` に触れたら、`SiteFactChangeRejected` で拒否する。許可する項目の一覧は `model/patch.py` に1か所で持つ。
- 複数案の自動生成は、親案から `created_by="batch"` の子案を並べて作る操作として扱う。

### 4.6 段の結果 `StageResult`

```python
class StageResult:
    scheme_id: str
    stage: Literal["volume", "estimate", "core", "grid", "drawing", ...]
    input_hash: str            # 下記「キャッシュ」参照
    solver_version: str
    status: Literal["ok", "infeasible", "error"]
    data: dict                 # 段ごとの型を別に定義する
    applied_rules: list[AppliedRule]
    not_considered: list[NotConsidered]
    defaults_used: list[str]   # origin="default" の計画条件のパス
    warnings: list[str]

class AppliedRule:
    rule_id: str               # solver/law で定義した ID
    article: str               # 例：「法56条1項1号」
    summary: str
    effect_mm2: float | None   # この規定を外した場合の増分（出せる段だけ）

class NotConsidered:
    item: str
    direction: Literal["larger_than_actual", "smaller_than_actual", "unknown"]
    note: str
```

- `direction="larger_than_actual"`（実際より大きく出る＝危険側）の項目は、画面・図面で目立つ位置に出す。例は日影規制の未実装。
- `applied_rules`、`not_considered`、`defaults_used` は空でも省略しない。

### 4.7 骨格データ（型だけ先に定義し、中身は後の段で入れる）

```python
class Level:
    name: str                  # "1F", "RF" など
    fl_mm: float               # GL からの床高さ
    floor_height_mm: float
    ceiling_height_mm: PlanValue | None   # M07 以降
    beam_bottom_mm: PlanValue | None      # M07 以降

class GridAxis:
    name: str                  # "X1", "Y1" など
    direction: Literal["X", "Y"]
    offset_mm: float           # 建物座標での位置
```

通り芯とレベルは、線や文字として描くだけでなく、必ずデータとして取り出せるようにする。総合図作成者への受け渡しの土台になるため。

## 5. 段と依存

| 段 | 入力 | 主な出力 | マイルストーン |
|---|---|---|---|
| volume | 敷地事実、計画条件 | 各階形状・面積・打ち切り理由、規定別の削減量、天空率の判定、Level | M02 |
| estimate | volume（core・grid があれば使う） | 部位別数量、概算の幅 | M05 |
| core | volume | コア配置、EV 台数、有効率 | M06 |
| grid | core | GridAxis、階高 | M07 |
| drawing | そろっている結果すべて | 図面モデル | M03, M08 |
| site | 複数の敷地事実 | 候補ごとの volume 要約 | 後で追加 |

### キャッシュと再計算

- 段の `input_hash` は、次の正規化 JSON の SHA-256 とする：段の名前、その段が使う入力、上流の段の `input_hash`。
- 結果は `(scheme_id, stage, input_hash, solver_version)` で保存し、同じ組があれば再計算しない。
- 上流が変われば `input_hash` が変わるので、下流は次に呼ばれたとき自然に再計算される。無効化の処理は作らない。
- `solver_version` は、パッケージの版と `solver/law/` の定数ファイルのハッシュを組み合わせたもの。法規定数が変われば全段が再計算される。
- ハッシュ用の正規化では、キーを並べ替え、float を小数3桁に丸める。保存する値そのものは丸めない。

## 6. 法規定数

- `solver/law/constants.py`（大きくなれば分割してよい）に集約し、各定数に条文番号をコメントで添える。
- 各規定には `rule_id` を付け、`AppliedRule` から参照する。
- 条文から一義に読めず厳しい側を既定にしたものは、`docs/decisions/` に「読み方の候補、採った既定、理由、結果への影響の向き」を記録する。

## 7. 図面

- `drawing/model/` に共通の図面モデル（シート、ビュー、mm 単位の図形、レイヤ名、文字）を持つ。DXF と PDF は、どちらもこのモデルから出力する。一方から他方へは変換しない。
- **出力器は、注記ブロック（企画検討用・確認申請に使えない旨、未考慮事項、既定値で進めた計画条件、適用規定と条文）がない図面モデルを受け付けない。**
- レイヤ規約は `drawing/layers.py` に1か所で持つ。接頭辞は `A-`（建築）、`S-`（構造）、`M-`（機械設備）、`E-`（電気設備）とし、`S-`・`M-`・`E-` は名前の枠だけ予約する。既存のレイヤ名は M00 で記録し、M03 で対応を決める。
- IFC は将来の出力器として同じモデルから出す。今は作らない。

## 8. API

- 既存にサーバーがあればそれを使う。なければ FastAPI（M02 で決定し記録する）。
- M02 の段階では認証なし、localhost のみで待ち受ける。
- 主なエンドポイント（詳細は M02）：
  - `POST /api/schemes` 案の作成
  - `GET /api/schemes/{id}` 案の取得
  - `POST /api/schemes/{id}/derive` 差分から子案を作成
  - `POST /api/schemes/{id}/stages/{stage}` 段の実行（キャッシュがあれば返す）
  - `POST /api/schemes/{id}/variants` 複数案の自動生成
  - `GET /api/sites/{site_id}/schemes` 敷地ごとの案一覧

## 9. LLM とのつなぎ方

- `tools/` に、会話で一言で頼める単位のツールを定義する（例：案を作る、条件を変える、案を並べる、結果を説明する）。MCP サーバー（M04）と Web チャット（M09）は同じ定義を使う。
- ツールが LLM に返すのは `StageResult` の JSON。**画面に出す数値は solver の結果から直接表示する。** LLM の説明文に含まれる数値は結果と照合し、ずれていれば差し替える（M09）。

## 10. 保存

- `store/` は SQLite で始める（テーブル：`schemes`、`stage_results`）。
- `store/` の外からは、保存・取得のインターフェースだけを使う。DB を移すときに `store/` だけ差し替えられるようにするため。

## 11. 検証

- **回帰テスト**（`tests/regression/`）：M00 時点の出力を固定する。正しさの保証ではなく「変わっていないこと」の保証。浮動小数は相対誤差 1e-9 以内で一致とする。
- **手計算ケース**（`tests/cases/<名前>/`）：`calc.md` に計算過程を書き、`expected.json` に期待値、`input.json` に入力を置く。許容差はケースごとに `calc.md` に書く。
- **外部 API** はテストで呼ばない。`tests/fixtures/` の記録済み応答を使う。
- 手計算で検算できない機能（日影、コア計画の一部）は、着手前にマイルストーン文書で検証方法を決める。

## 12. 将来の拡張（今は枠だけ）

- 用地候補の一括評価（`solver/site/`、エリアスクリーニング）
- 日影規制（第1段と並行。検証方法を先に決める）
- 事業収支（第2段）
- IFC 出力
- 仮の天井・設備検討図（第3段の後に検討）
- 公開（招待制）に向けた認証・利用ログ・費用上限
