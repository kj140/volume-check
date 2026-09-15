# volume_check — 作業ルール

建築基準法に基づくボリュームチェックのツール。全体像・経緯・今後の道筋は `docs/HANDOFF.md`。

## 絶対に守ること

- **法規定数は `constants.py` に集約し、各値に条文番号のコメントを付ける。** 他のファイルに法規の数値を直書きしない。
- **法規の値で迷ったら推測しない。** 調べて最も一般的な値を採る。条文から読み切れない点は「厳しい側」を既定にし、理由をコードのコメントと README に書く。
- **テストが通らない状態で次の工程に進まない。** `.venv/Scripts/python.exe -m pytest -q`
- **手計算の期待値を先に書き、実装をそれに合わせる。** 実装の出力を期待値に写さない。
- **性質テストが成り立たなくなったら黙って書き換えない。** 何が成り立たなくなったかを報告し、合意してから直す。
- **土台を入れ替えるときは、従来結果との一致を数値で示す。**
- **未考慮事項は `VolumeResult.notes` に入れる。** 画面と DXF の両方に印字される。
- 確認申請に使えないことを前提にした企画用ツール。この前提を弱める表現をしない。

## 構成の約束

- 斜線の式は `solver.py` の `road_setback` / `neighbor_setback` / `north_setback` が唯一の定義。`skyfactor.py` もこれを使う。
- shapely への依存は `geometry.py` だけ。`solver.py` は `geometry` 経由で幾何を扱う。
- 断面・平面の幾何は `section.py` / `plan.py` が唯一の定義で、DXF と SVG が共有する。
- `web/app.py` に法規ロジックを置かない。solve() と draw() を呼ぶだけ。
- 単位: 入力 JSON は m、内部は mm。換算は `models.py` の入口で1回だけ。
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
