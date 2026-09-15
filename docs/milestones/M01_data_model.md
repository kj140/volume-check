# M01 データモデルと案の保存

## 目的

「案」を、敷地事実と計画条件を持つ書き換えない記録として定義し、保存できるようにする。会話や画面で条件を変えるたびに履歴が残り、どの案がどの条件だったかを追えるようにするため。算定には触れない。

## 前提

- M00 が完了している（`m00-baseline` タグ、回帰テストが通る）
- 読む文書：`CLAUDE.md`、`docs/architecture.md` の4章・5章・10章、`docs/current_state.md`

## 作業

1. **型を定義する（`model/`）。** 対象は `FactValue`、`PlanValue`、`SiteFacts`、`PlanConditions`、`Scheme`、`StageResult`、`AppliedRule`、`NotConsidered`、`Level`、`GridAxis`。
   - 型の定義には pydantic v2 を使う。既存に同等の仕組みがあればそちらに合わせ、どちらにしたかを `docs/decisions/` に記録する。
   - `SiteFacts` と `PlanConditions` の項目は、`current_state.md` に記録した既存の入力項目をすべて含める。既存にない敷地事実（高度地区、日影規制の指定、地区計画など）は `source="unavailable"` を既定にする。
   - 計画条件の既定値は既存コードの既定値を使い、`origin="default"` とする。
2. **正規化とハッシュを作る（`model/hashing.py`）。** キーを並べ替えた JSON をもとに、float を小数3桁に丸めて SHA-256 を計算する。保存する値そのものは丸めない。
3. **差分の適用と検査を作る（`model/patch.py`）。**
   - 差分は `{"path": "plan_conditions.setback_mm", "value": ...}` の並び。
   - 書き換えを許す項目の一覧を、このファイルに1か所で持つ。
   - `created_by="chat"` の差分が `site_facts` 配下に触れたら、`SiteFactChangeRejected` を送出する。エラーには、どの項目が拒否されたかと「敷地の事実は画面から出典つきで入力してください」という案内を含める。
   - `created_by="ui"` の敷地事実の変更は受け付ける。ただし `source` と `source_name` の指定を必須にする。
   - 存在しない項目や型の合わない値は拒否する。
4. **既存入力との変換を作る（`model/legacy.py`）。** `from_legacy_input(既存の入力) -> Scheme` と `to_legacy_input(Scheme) -> 既存の入力` を実装する。M02 で既存の算定を包むときに使う。
5. **保存を作る（`store/`）。** SQLite で、テーブルは `schemes` と `stage_results`。インターフェースは次のとおり。
   - `save_scheme(scheme)`、`get_scheme(id)`
   - `derive(parent_id, patch, created_by, label=None) -> Scheme`（新しい子案を作って保存する。親は変えない）
   - `list_schemes(site_id)`、`list_children(parent_id)`
   - `save_result(result)`、`get_result(scheme_id, stage, input_hash, solver_version)`
6. **型の説明を書く。** `docs/architecture.md` 4.3・4.4 の項目表を、実際に確定した項目で更新する。

## 受け入れ条件

- [ ] 全回帰ケースの入力について、`to_legacy_input(from_legacy_input(x))` が元の入力と一致する
- [ ] 同じ内容でキーの順番だけ違う入力が、同じ `input_hash` になる
- [ ] 敷地事実・計画条件のどの1項目を変えても、`input_hash` が変わる（全項目を回すテスト）
- [ ] `derive` で子案が作られ、親案の内容と `input_hash` が変わらない
- [ ] `created_by="chat"` で `site_facts` 配下を変える差分が拒否され、案が作られない
- [ ] `created_by="ui"` で `source` なしの敷地事実の変更が拒否される
- [ ] `source="unavailable"` の事実が、保存・読み出しの後も `unavailable` のまま残る
- [ ] SQLite を閉じて開き直しても、案・親子関係・結果が読み出せる
- [ ] `Level` と `GridAxis` を JSON で保存・読み出しできる（中身は空でよい）
- [ ] 回帰テスト（M00）が全件通る
- [ ] solver のコードに差分がない

## やらないこと

- 算定（solver）の変更
- API、画面、MCP
- クラウド DB
- 通り芯・階レベルの算定（型の定義だけ）

## 着手前に確認すること

- 既存の入力項目のうち、敷地事実か計画条件か判断に迷うものは一覧にしてユーザーに聞く（例：耐火にするかどうかは計画条件、防火地域の指定は敷地事実）
- 既存コードに既定値のない計画条件が見つかったら、値を決めずに質問する
