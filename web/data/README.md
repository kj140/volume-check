# 用途地域データの置き場

APIキーなしで用途地域を自動判定したい場合、ここに GeoJSON を置く。
`*.geojson` を上から順に見て、敷地中心を含むポリゴンの属性を読む。

- 国土数値情報 用途地域データ(A29): https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A29.html
- 読める属性名は `web/README.md` の表を参照

都道府県単位のデータは数十MB〜になることがある。Railway にデプロイする場合は
イメージサイズが増えるので、必要な市区町村だけに切り出しておくとよい。
