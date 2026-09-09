# ボリュームチェック図 自動生成ツール

オフィス・テナントビルの企画段階で、敷地条件と法規条件から建築可能ボリュームを算定し、
**DXF図面1枚**として出力する。CLI と Web アプリの両方から使える。

> **企画検討用の試算です。** 矩形敷地のみ対応。天空率・日影規制・複数前面道路の緩和・
> 高度地区・地区計画は未考慮。**確認申請には使用できません。** 建築士による確認が必要です。

![断面図](docs/section.png)

## できること

- 前面道路幅員による容積率の低減（法52条2項）
- 道路斜線（法56条1項1号・別表第三）— 適用距離による頭打ちを含む
- 隣地斜線（法56条1項2号）
- 絶対高さ制限（法55条）
- 建蔽率による全階一律の絞り込み（法53条）と、防火地域・角地による緩和（法53条3項・6項）
- 階別の可能形状・面積表・打ち切り理由の算定
- 配置図＋断面図＋面積表＋表題欄を1枚にまとめた DXF 出力
- どの規定がどれだけボリュームを削っているかの算定
- 地図から敷地を指定して、断面図・配置図・各階平面図をブラウザでプレビュー（Web版）
- 用途地域・建蔽率・容積率・防火地域などの自動取得（Web版・要APIキー）

適用した規定と根拠条文、および未考慮事項は**すべて図面上に自動で印字される**。

## 使い方

### CLI

```bash
python main.py samples/case_road12.json out/case_road12.dxf
```

入力JSON（単位は m）:

```json
{
  "site":    { "frontage": 20.0, "depth": 30.0, "road_width": 12.0, "road_side": "south" },
  "zoning":  { "use_district": "商業地域", "bcr": 0.8,
               "far_designated": 6.0, "height_limit_absolute": null },
  "program": { "floor_height": 4.2, "gf_height": 4.5, "wall_setback": 0.5,
               "core_ratio": 0.18, "max_floors": 30 }
}
```

`frontage` は道路に接する辺の長さ、`road_side` は前面道路の方位（作図の向きを決める）。

### Web アプリ

```bash
pip install -r requirements.txt
python -m uvicorn web.app:app --reload --port 8000
```

→ http://localhost:8000

地図で敷地の矩形を描くと間口・奥行が入り、断面図と面積表がその場で更新される。
詳細と Railway へのデプロイ手順は [`web/README.md`](web/README.md) を参照。

## 構成

```
constants.py   法規定数（すべて条文番号のコメント付き。ここ以外に法規の値を書かない）
models.py      入出力のdataclass（入力は m、内部は mm。換算は入口で1回だけ）
solver.py      階別の可能形状を算定
section.py     断面の幾何（DXFとSVGプレビューで共有する唯一の定義）
drawer.py      DXF出力
main.py        CLI
web/           FastAPI + Leaflet の Web アプリ
samples/       サンプル入力
tests/         pytest
```

計算ロジックは `solver.py` に閉じており、`drawer.py` も `web/` も法規の判断をしない。

## 開発

```bash
pip install -r requirements.txt
pip install pytest
pytest
```

外部依存は実行時が `ezdxf` / `fastapi` / `uvicorn` / `httpx` の4つだけ。
幾何ライブラリ（shapely, CadQuery 等）は使わず、矩形限定なので float の四則演算で解いている。

テストは性質テスト（どんな入力でも成り立つこと）と、手計算で検算した具体ケースの両方を置いている。

## 法規定数について

特定行政庁の指定や都市計画で値が変わる項目は**法の原則値**を既定とし、
どの値を採用したかを算定結果と図面に必ず記録する。自治体が別の指定をしている場合は
その値で上書きして再計算すること。詳細は `constants.py` のコメントを参照。
