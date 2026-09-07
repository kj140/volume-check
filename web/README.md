# ボリュームチェック図 Web

既存の `solver.py` / `drawer.py` をそのままバックエンドに使う Web アプリ。
法規のロジックは `web/` の中には一切ない。

## 起動

```
cd volume_check
.venv/Scripts/python -m uvicorn web.app:app --reload --port 8000
```

→ http://localhost:8000 （API の一覧は http://localhost:8000/docs ）

## 使い方

1. 住所で地図を移動し、「敷地の矩形を描く」でドラッグして敷地を指定する
   矩形の辺は南北・東西を向く。**道路の方位**でどちらの辺が間口になるかが決まる
2. 前面道路の幅員、用途地域、建蔽率・容積率、階高などを入力する
   （地図から取得した間口・奥行はそのまま編集できる）
3. 右側に断面図と面積表が自動で更新される
4. 下部の「DXF をダウンロード」で図面を取得する

## 構成

| ファイル | 役割 |
|---|---|
| `app.py` | FastAPI。`solve()` と `draw()` を呼ぶだけの薄い層 |
| `geo.py` | 地図の矩形 → 間口・奥行（WGS84 楕円体の弧長）、タイル座標、点の内外判定 |
| `zoning.py` | 緯度経度 → 用途地域・建蔽率・容積率の推定 |
| `svg_section.py` | 断面図の SVG。DXF と同じ `section.py` の幾何を描く |
| `static/` | 画面（素の HTML/CSS/JS + Leaflet） |

断面の形は `volume_check/section.py` が唯一の定義で、DXF と画面プレビューが
同じものを参照している。片方だけずれることはない。

## 地図タイル

APIキー不要のものだけを使っている。

- 地理院タイル（淡色 / 写真）— 国土地理院
- OpenStreetMap
- 住所検索：国土地理院 住所検索API（`/api/geocode` でサーバ経由）

## 用途地域の自動判定

`/api/zoning` は次の順に試し、どれも当たらなければ「手入力」を返す
（判定できなくても計算は止まらない）。

### 1. 不動産情報ライブラリ API（推奨・要APIキー）

国土交通省の「都市計画決定GISデータ（用途地域）」API（XKT002）。
用途地域・建蔽率・容積率がまとめて取れる。**無料だが事前のAPIキー取得が必要**。

<https://www.reinfolib.mlit.go.jp/> で取得したキーを環境変数に入れて起動する。

```
set REINFOLIB_API_KEY=<取得したキー>      # PowerShell なら $env:REINFOLIB_API_KEY="..."
.venv/Scripts/python -m uvicorn web.app:app --port 8000
```

### 2. ローカルの GeoJSON（APIキー不要）

`web/data/*.geojson` に用途地域のポリゴンを置くと、そこから判定する。
国土数値情報の「用途地域データ(A29)」を GeoJSON に変換したものを想定していて、
属性は次のどちらの命名でも読める。

| 項目 | 不動産情報ライブラリ | 国土数値情報 A29 |
|---|---|---|
| 用途地域コード | `youto_id` | `A29_004` |
| 建蔽率 | `u_building_coverage_ratio_ja` | `A29_006` |
| 容積率 | `u_floor_area_ratio_ja` | `A29_007` |

用途地域コードは 1〜12・21（1=第一種低層住居専用地域 … 9=商業地域 … 21=田園住居地域）。
建蔽率・容積率は `"80%"` でも `80` でも解釈する。

データ: <https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A29.html>

### 3. 手入力

上のどちらも無い場合はフォームで選ぶ。自動判定した値も**必ず実際の都市計画情報で
確認すること**。この画面の値は企画検討用の目安にすぎない。

## 注意

矩形敷地のみ対応。天空率・日影規制・複数前面道路の緩和・高度地区・地区計画は未考慮。
確認申請には使用できない。

---

## Railway へのデプロイ

デプロイ用のファイルは `volume_check/` の直下にある。

| ファイル | 役割 |
|---|---|
| `Dockerfile` | 実行イメージ（python:3.13-slim・非rootで起動） |
| `requirements.txt` | 実行時依存のみ（開発用の pytest / matplotlib は含まない） |
| `.dockerignore` | `.venv/` `__pycache__/` `out/` `tests/` などを除外 |
| `railway.toml` | ビルダ・ヘルスチェック・再起動ポリシー |

### 手順

1. **リポジトリを用意する**

   ```
   git init
   git add .
   git commit -m "Add volume check web app"
   git remote add origin <GitHubのURL>
   git push -u origin main
   ```

2. **Railway でサービスを作る**

   New Project → Deploy from GitHub repo → 対象リポジトリを選択。

3. **Root Directory を設定する（重要）**

   親リポジトリの中に `volume_check/` を置いている場合は、
   Settings → Build → **Root Directory** に `volume_check` を指定する。
   これで `Dockerfile` と `railway.toml` がビルドコンテキストの直下に来る。
   `volume_check/` 自体がリポジトリのルートなら設定不要。

4. **デプロイ**

   `railway.toml` の `builder = "DOCKERFILE"` により Dockerfile でビルドされる。
   待ち受けポートは Railway が渡す環境変数 `PORT` を `web/app.py` の `main()` が読む。
   コードにポートを固定していないので追加設定は不要。

5. **ヘルスチェック**

   `/healthz` を叩くと実際に `solve()` を1回通し、計算系まで動いていることを確認する。
   単に「プロセスが起きている」だけでなく、法規計算が壊れていれば落ちる。

   ```json
   {"status": "ok", "version": "0.1.0", "probe_floors": 9}
   ```

6. **用途地域の自動判定を使う場合（任意）**

   Variables に `REINFOLIB_API_KEY` を追加する。未設定でも手入力で動く。

### ローカルでイメージを検証する

```
cd volume_check
docker build -t volume-check-web .
docker run --rm -e PORT=9123 -p 9123:9123 volume-check-web
curl http://127.0.0.1:9123/healthz
```

### 日本語フォントについて

イメージに日本語フォント（msgothic.ttc）は**入れていない**。DXF にはフォント「名」を
書き込むだけで、ezdxf は実体を必要としないため。文字を描画するのは図面を開く CAD 側なので、
Linux コンテナ上で生成した DXF でも Windows の CAD で開けば正しく日本語が表示される。

実際に、コンテナで生成した DXF とローカルで生成した DXF は
図形数・レイヤー・線種・寸法ブロックまで完全に一致することを確認済み
（バイト数の差は改行コードが CRLF か LF かの違いのみ）。

### 注意

- `tests/` はイメージに含めていないので、コンテナ内で `pytest` は実行できない。
  CI で回す場合は別途 `pip install pytest` してリポジトリから実行する。
- `web/data/` に大きな GeoJSON を置くとイメージサイズがそのまま増える。
  必要な範囲だけに切り出しておくこと。
