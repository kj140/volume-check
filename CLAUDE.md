# CLAUDE.md — このリポジトリで作業するときのルール

## このリポジトリは何か

オフィス・テナントビルの企画段階で、敷地から法規条件を取り、建てられるボリュームと複数案を出し、概算・簡易図面を経て、構造・設備・総合図の作成者へ基準データを渡すまでを行うシステム。

- **企画検討用の試算であり、確認申請には使わない。** この前提を弱める変更をしない。
- 総合図そのものは作らない。渡すのは通り芯・階レベル・コア・PS/EPS などの基準データまで。
- 背景は `docs/project_brief.md`、構成は `docs/architecture.md`、作業単位は `docs/milestones/`。
- 既存コード（ボリュームチェック単体）の経緯と検証済み機能の一覧は `docs/HANDOFF.md`。

## 絶対に守ること

以下に反しそうになったら、**作業を止めてユーザーに報告する**。

1. **法規の数値・適用条件をあなた（Claude Code）が決めない。**
   - 法規の数値は法規定数ファイルだけに置き、1つずつ条文番号をコメントで添える。現在は `constants.py`、移行先は `solver/law/`（`docs/architecture.md`）。他のファイルに法規の数値を直書きしない。
   - 条文を自分で解釈して新しい数値や適用条件を足さない。必要なら `TODO(法規確認)` を残し、質問して止まる。
   - 読み切れない点の既定は厳しい側。理由は `docs/decisions/` に記録する。
2. **数値の算定は solver だけが行う。**
   - solver は入出力（ファイル・ネットワーク・DB・時刻・乱数）を持たない純粋な関数にする。
   - LLM を使う部分（`tools/`、Web のチャット）は、入力の翻訳と結果の説明だけを担う。
3. **期待値を先に書く。** 新しい算定には、実装の前に `tests/cases/` へ計算過程つきの手計算ケースを置く。期待値はユーザーの確認を得てから実装に進む。実装の出力を期待値に写さない。
4. **既存の結果を黙って変えない。** `tests/regression/` が1件でもずれたら修正を止めて報告する。性質テストが成り立たなくなった場合も同じ。意図した変更なら、理由を書き、期待値の更新をユーザーに確認する。土台を入れ替えるときは、従来結果との一致を数値で示す。
5. **全段の結果に「適用した規定と条文」「未考慮事項」「既定値で進めた計画条件」を必ず持たせ、出力に印字する。** 図面・PDF では同じ紙面に載せる。現在は `VolumeResult.notes` が画面と DXF の両方に印字される。
6. **敷地事実（用途地域・道路幅員・防火地域など）は、LLM 経由の変更をコードで拒否する。** プロンプトでの注意に頼らない。
7. **案は書き換えない。** 変更は親案 ID を持つ新しい案として作る。

## 作業の進め方

1. 指示されたマイルストーン文書を読み、`CLAUDE.md` と `docs/architecture.md` と矛盾がないか確かめる。
2. 不明点・矛盾があれば、**着手前に**まとめて質問する。
3. テスト（手計算ケース・受け入れ条件）を先に書く。
4. 実装する。マイルストーンの「やらないこと」を守る。ついでのリファクタリング、依存ライブラリの追加・更新、フォーマッタの一括適用はしない。必要ならユーザーに確認する。
5. テストを全件実行する（回帰テストを含む）。テストが通らない状態で次の工程に進まない。
6. `docs/current_state.md` を更新し、決めたことがあれば `docs/decisions/` に追記する。
7. 下の書式で完了報告をする。

## 完了報告の書式

ユーザーはプログラミング経験がない。専門用語はできるだけ避け、日本語で書く。

```
## 完了報告：Mxx
- やったこと（3行以内）
- 確かめ方と結果（どのテストを何件実行し、何件通ったか）
- 変わった数値（なければ「なし」。あれば件数と理由）
- やらなかったこと・残った課題
- ユーザーに決めてほしいこと（選択肢・推奨・理由）
```

## 単位と表記

- 内部の長さは mm、面積は mm²。表示は m・m²。入力 JSON は m で、換算は `models.py` の入口で1回だけ。
- 建蔽率・容積率は倍率で表す（0.8＝80%、6.0＝600%）。
- 座標系は `docs/architecture.md` の「共通の約束」に従う。

## 現在のコードの約束（移行前の構成。M00 で `docs/current_state.md` に記録し、移行のマイルストーンで目標構成に寄せる）

- 斜線の式は `solver.py` の `road_setback` / `neighbor_setback` / `north_setback` が唯一の定義。`skyfactor.py` もこれを使う。
- shapely への依存は `geometry.py` だけ。`solver.py` は `geometry` 経由で幾何を扱う。
- 断面・平面の幾何は `section.py` / `plan.py` が唯一の定義で、DXF と SVG が共有する。
- `web/app.py` に法規ロジックを置かない。solve() と draw() を呼ぶだけ。
- 外壁後退と斜線後退は足さず `max` を取り、建蔽率の絞り込みはその上に足す（`geometry.buildable_region`）。

## 開発環境

- venv は `volume_check/.venv`。実行は `.venv/Scripts/python.exe`。
- 開発サーバーは `.claude/launch.json` の `volume-check`（port 8790）。**`--reload` が効かないので、変更後はサーバーを再起動する。**
- Bash の heredoc に日本語やバックスラッシュを含む Python を渡すと壊れる。**パッチはスクリプトファイルに書いて実行する。**
- DXF の改行は CRLF。
- `main` への push で Railway が自動デプロイする。本番: https://volume-check-production-f7b5.up.railway.app （PORT=8080）

## コミット

- 何を変えたかより「なぜ」を書く。数値が変わるときは変更前後を本文に残す。
- 末尾に `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## コマンド

- 起動（Web・開発）：`.venv/Scripts/python.exe -m uvicorn web.app:app --port 8790`（`.claude/launch.json` の `volume-check`）
- 起動（CLI）：`.venv/Scripts/python.exe main.py samples/case_road12.json out/case_road12.dxf`
- テスト全件：`.venv/Scripts/python.exe -m pytest -q`
- 回帰テストだけ：`.venv/Scripts/python.exe -m pytest tests/regression -q`
- 手計算ケースだけ：`.venv/Scripts/python.exe -m pytest tests/test_cases.py -q`
- 回帰結果の保存（意図した更新のときだけ。ユーザー確認後）：`.venv/Scripts/python.exe tests/regression/record.py --force`
