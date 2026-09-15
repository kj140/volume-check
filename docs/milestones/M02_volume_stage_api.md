# M02 ボリューム算定を段として包み、API 化する

## 目的

既存のボリューム算定を、算定の中身は変えずに「案を受け取り `StageResult` を返す段」として包み、HTTP の API から呼べるようにする。以後の段（概算・コア・グリッド・図面）と、MCP・チャットが同じ入口を使えるようにするため。

## 前提

- M01 が完了している（データモデル、差分の検査、保存、既存入力との変換）
- 読む文書：`CLAUDE.md`、`docs/architecture.md` の2章・4.6章・5章・8章、`docs/current_state.md`

## 作業

1. **ボリュームの結果の型を定義する（`model/stages/volume.py`）。**
   - `floors`：階ごとの `level`（`Level`）、形状、面積、打ち切り理由
   - `totals`：延床面積、建築面積、階数、最高高さ
   - `rule_reductions`：規定ごとの削減量
   - `sky_factor`：天空率の判定（道路・隣地ごと、判定と余裕）
   - 既存の結果にあって上に当てはまらない項目は、名前を付けて追加する。捨てない。
2. **段の関数を作る（`solver/volume/stage.py`）。** `run_volume_stage(scheme: Scheme) -> StageResult`
   - 内部で `to_legacy_input` を使い、既存の算定関数を呼ぶ。**既存の算定関数の中身は変えない。**
   - `applied_rules` と `not_considered` は、既存の出力（DXF に印字している内容など）から構造化して移す。**既存にない項目を足さない。** 足すべきだと思ったら、報告で提案する。
   - `not_considered` の `direction` は、`docs/project_brief.md` 5章の記述（「実際より小さく出る」「安全側にならない」など）に対応が書いてあるものだけ設定し、書いていないものは `unknown` にする。
   - `defaults_used` には、`origin="default"` の計画条件のパスを入れる。
   - 階ごとの `Level`（`name`、`fl_mm`、`floor_height_mm`）を入れる。
   - 成立する階がない場合は `status="infeasible"` とし、理由を `warnings` に入れる。
   - 外部 API を呼ばない。既存の算定が内部で外部 API を呼んでいる場合は、止めて報告する（切り離し方をユーザーと決める）。
3. **段の実行とキャッシュを作る（`api/stages.py` など）。**
   - 段の `input_hash` を `docs/architecture.md` 5章のとおり計算する。
   - `solver_version` は、パッケージの版と `solver/law/` の定数ファイルのハッシュを組み合わせる。
   - `get_result` で見つかれば返し、なければ計算して `save_result` する。
4. **複数案の自動生成を移す。** `generate_variants(parent_id) -> list[Scheme]`
   - 既存の総当たりで出る各案を、`created_by="batch"` の子案として保存し、それぞれの volume 結果も保存する。
   - 並び順（既存の順位づけ、末尾に足す外壁後退ごとの最良案を含む）は既存どおりにする。並び順は子案の `label` と、応答の順番で表す。
5. **API を作る。** 既存にサーバーがあればそれを使い、なければ FastAPI にする。どちらにしたかを `docs/decisions/` に記録する。
   - `POST /api/schemes`：敷地事実と計画条件から案を作る（`created_by="ui"`）
   - `GET /api/schemes/{id}`
   - `POST /api/schemes/{id}/derive`：本文は `{patch, created_by, label}`。拒否されたら 422 で、拒否された項目を返す
   - `POST /api/schemes/{id}/stages/volume`：結果を返す（キャッシュがあればそれを返す）
   - `POST /api/schemes/{id}/variants`：子案と結果を順番どおりに返す
   - `GET /api/sites/{site_id}/schemes`
   - 待ち受けは localhost のみ、認証なし
6. **依存の向きを検査するテストを作る。** `solver/` 配下のファイルが `api`・`store`・`data`・`web`・`tools` を import していないことを確かめる。
7. **`docs/current_state.md` を更新する。**

## 受け入れ条件

- [ ] 全回帰ケースを `run_volume_stage` 経由で実行し、M00 で固定した `result.json` と数値が一致する（相対誤差 1e-9 以内）
- [ ] 全回帰ケースで、`applied_rules` と `not_considered` の内容が既存の出力と過不足なく対応する（対応表をテストに含める）
- [ ] 同じ案で段を2回実行すると、2回目は算定関数が呼ばれない（呼び出し回数で確かめる）
- [ ] 計画条件を1つ変えた子案では、再計算される
- [ ] `solver_version` が変わると再計算される
- [ ] 複数案の自動生成で、案の数・並び順・各案の延床が M00 の結果と一致する
- [ ] API 経由（テストクライアント）で、案の作成 → 段の実行 → 差分から子案 → 段の実行 が通る
- [ ] API 経由で `created_by="chat"` の敷地事実の変更が 422 で拒否される
- [ ] 依存の向きのテストが通る
- [ ] 回帰テスト（M00）が全件通る
- [ ] 既存の算定関数の中身に差分がない

## やらないこと

- 算定内容の変更、バグ修正（見つけたら記録するだけ）
- Web 画面の変更（画面を API 経由に切り替えるのは、M03 の後に別のマイルストーンとして判断する）
- DXF 出力の移行（M03）
- 認証、公開用の設定
- 新しい未考慮事項・適用規定の追加

## 着手前に確認すること

- 既存の算定が外部 API・ファイル・時刻に依存していないか（`current_state.md` を見て、依存があれば切り離し方を相談する）
- 既存の結果の項目で、1章の型のどこに入れるか迷うもの
