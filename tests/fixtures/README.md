# tests/fixtures — 外部 API の記録済み応答

テストはネットワークに出ない。外部 API を呼ぶ箇所（`web/zoning.py`・`web/app.py` の
`/api/geocode`）は、ここにある応答を `httpx.AsyncClient.get` の差し替えで返す
（`tests/regression/test_external_api_offline.py`）。

| ファイル | 相手 | 内容 |
|---|---|---|
| `reinfolib/XKT002.json` | 不動産情報ライブラリ 用途地域 | 東京駅付近 (139.767, 35.681) を含む「商業地域 80% / 800%」のポリゴン1つと、含まないポリゴン1つ |
| `reinfolib/XKT014.json` | 同 防火・準防火地域 | 「防火地域」 |
| `reinfolib/XKT001.json` | 同 区域区分 | 「市街化区域」 |
| `reinfolib/XKT023.json` | 同 地区計画 | 該当なし（features が空） |
| `reinfolib/XKT024.json` | 同 高度利用地区 | 該当なし |
| `gsi_address_search.json` | 国土地理院 住所検索 API | 「丸の内」2件 |

**注意（M00 時点）**：これらは**実応答ではなく模擬データ**。M00 の作業時に
`REINFOLIB_API_KEY` がなく実応答を記録できなかったため、`web/zoning.py` が読む
キー名（`youto_id`, `u_youto_ja`, `u_building_coverage_ratio_ja`,
`u_floor_area_ratio_ja`, `fire_prevention_ja`, `area_classification_ja`）と
GeoJSON の形だけを合わせてある。キーが使えるようになったら同じ座標の実応答で
差し替え、`tests/regression/zoning_lookup_offline/result.json` を取り直す
（差し替えは M00 の記録を変えることになるので、ユーザーの確認を得てから）。
