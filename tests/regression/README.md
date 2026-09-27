# tests/regression — 既存結果の固定（M00）

M00 時点（タグ `m00-baseline`）のコードが出す結果をそのまま保存し、以後の変更で
「数値が変わっていないこと」を確かめる。**正しさの保証ではない**。正しさは
`tests/cases/` の手計算ケースで別に確かめる。

## 実行

```
.venv/Scripts/python.exe -m pytest tests/regression -q      # 回帰テストだけ
.venv/Scripts/python.exe -m pytest -q                       # 全件（回帰を含む）
```

- 浮動小数は相対誤差 1e-9 以内で一致とみなす（0 付近のために絶対誤差 1e-9 も許す）。
- `tests/regression/` 配下のテストは **ネットワークに出ない**（`conftest.py` が
  ループバック以外の接続と名前解決を止める。外部 API は `tests/fixtures/` の記録済み応答）。
- 1 件でもずれたら、直す前に報告する（CLAUDE.md「既存の結果を黙って変えない」）。
  意図した変更なら理由を書き、ユーザーの確認を得てから `record.py --force` で保存し直す。

## ファイル

```
tests/regression/
  regress.py            ケースの実行・結果の直列化・DXF の要約・比較
  record.py             結果の保存（初回、または意図した更新のときだけ手で実行）
  conftest.py           ネットワーク遮断
  test_regression.py    各ケースの result.json / dxf_summary.json との比較
  test_external_api_offline.py   外部 API を記録済み応答で置き換えた結果との比較
  <ケース名>/
    input.json          入力。"volume"（solve() の入力）、"sky"（天空率も判定）、
                        "study"（複数案の生成条件。studies.generate() の引数名）
    result.json         算定結果の全体（mm・mm²。丸めない）
    dxf_summary.json    DXF から取り出した内容（レイヤごとの図形数、文字、線の端点を小数3桁）
  zoning_lookup_offline/
    request.json        座標・住所検索の語
    result.json         用途地域取得（関数と API）と住所検索の結果
```

`dxf_summary.json` は表題欄の「作成日時」の値だけを比較から外している（`drawer.py` が
実行時刻を書き込むため。本体コードは変えない、という M00 の判断）。DXF ファイルの
バイト比較はしない。

## ケースと性質の対応

M00 作業3で求められた性質（列）を、どのケース（行）が含むか。

| ケース | 矩形・1面道路 | 2面道路（角地） | 任意形状 | 振り角≠0 | 北側斜線 | 絶対高さで打ち切り | 容積率で打ち切り | 外壁後退＞斜線後退 | 防火地域・耐火の有無 | 天空率で斜線が外れる | 複数案 | 天空率×→後退案 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `rect_road12_commercial`（samples/case_road12） | ○ | | | | | | ○ | | | | | ○ |
| `rect_road6_commercial`（samples/case_road6） | ○ | | | | | | ○ | | | | | ○ |
| `polygon_road8_commercial`（samples/case_polygon） | | | ○ | | | | | | | | | ○ |
| `corner_two_roads_commercial` | | ○ | | | | | | | | | | ○ |
| `polygon_rotated_15deg` | | | ○ | ○ | | | | | | | | |
| `north_slant_midhigh_residential` | ○ | | | | ○ | | | | | | | ○ |
| `low_rise_absolute_height_limit` | ○ | | | | ○ | ○ | | | | ○ | | |
| `fire_zone_fireproof_on` | ○ | | | | | | ○ | | ○（耐火） | | | |
| `fire_zone_fireproof_off` | ○ | | | | | | ○ | | ○（非耐火） | | | |
| `wide_wall_setback_sky_passes` | ○ | | | | | | | ○ | | ○ | | |
| `study_polygon_with_sky` | | | ○ | ○ | | | | | | ○ | ○ | |
| `study_road12_sky_suggestion` | ○ | | | | | | ○ | | ○（両方を振る） | | ○ | ○ |
| `study_fire_zone_no_sky` | ○ | | | | | | | | ○（両方を振る） | | ○ | |

補足：

- 「容積率で打ち切り」は `stop_reason` が「容積率の上限に到達」のケース。`polygon_*`・`corner_*`・`north_*`
  は「床面積が最小成立面積未満」で打ち切られている。
- 「外壁後退＞斜線後退」は `wide_wall_setback_sky_passes`（外壁後退 5m）で、道路斜線の後退が 5m を超える
  のは上層階だけ。下層階では外壁後退が効いている。
- 「天空率×→後退案」は、単体では `skyfactor.evaluate()` の `suggestion`、複数案では
  `studies.generate()` の `sky_suggestion` がそれぞれ提示されるケース。
- `fire_zone_fireproof_on`（指定 80%・防火地域・耐火）は法53条6項1号で建蔽率の制限なしになる。
  板を絞らず階を切り捨てる現在の仕様のため、建築面積は増えるのに延床は減る（既知の挙動）。
- `low_rise_absolute_height_limit` は低層住専なので北側斜線もかかり、隣地斜線はかからない。
  天空率は道路側だけの判定で「通る」となる（北側斜線の天空率は未対応なので不完全）。
- `zoning_lookup_offline` の外部 API 応答は**模擬データ**（`tests/fixtures/README.md` 参照）。

## 更新履歴

意図した更新だけを記録する。数値（算定結果）が変わったかどうかを必ず書く。

| 日付 | 決定 | 変わったもの | 数値 |
|---|---|---|---|
| 2026-09-16 | M00 | 初回の固定 | — |
| 2026-09-27 | 0006 | 注記（`volume.notes`）に「日影規制は未実装」を全 13 ケースで追加、「建蔽率上限に収めるため全階を一律 …m 内側に絞り込み」を絞り込みのある 7 ケースで追加（以前は計算の順番のせいで出なかった）。DXF は足元の注記欄が 1〜2 行伸び、その上の図がすべて 0.88m／1.77m 上へ平行移動、注記の文字が 1〜2 行増えた（それ以外の図形・寸法の変化なし） | **変化なし**（注記を除く全項目が相対誤差 1e-9 以内で一致することを確認してから保存した） |

