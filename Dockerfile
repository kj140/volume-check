# ボリュームチェック図 Web の実行イメージ。
#
# 日本語フォント（msgothic.ttc）はイメージに含めない。DXF にはフォント「名」を
# 書き込むだけで、ezdxf は実体を必要としないため。文字を描くのは図面を開く
# CAD 側なので、閲覧する端末側にフォントがあればよい。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

# 依存だけ先に入れてレイヤをキャッシュする
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 非rootで動かす
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# web/app.py の main() が環境変数 PORT を読む
CMD ["python", "-m", "web.app"]
