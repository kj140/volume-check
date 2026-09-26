"""案と段の API のアプリ（M02）。localhost だけで待ち受ける。

    .venv/Scripts/python.exe -m api            # http://127.0.0.1:8791

公開中の Web アプリ（web/app.py、Railway）には載せない。認証がなく、案を保存する
API を外に出さないため（docs/decisions/0005）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI  # noqa: E402

from api.routes import router  # noqa: E402

app = FastAPI(title="ボリュームチェック 案と段の API", version="0.1.0",
              description="企画検討用の試算。確認申請には使用できません。localhost 専用。")
app.include_router(router)

HOST = "127.0.0.1"
PORT = 8791


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
