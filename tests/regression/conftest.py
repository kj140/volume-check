"""回帰テスト共通の設定。

ここ（tests/regression/ 配下）のテストはすべてネットワークなしで動かす。
外向きの接続を試みた時点で失敗させ、「記録済みの応答だけで通る」ことを担保する。
ループバック（127.0.0.1 / ::1）は asyncio が内部で使うので通す。
"""

from __future__ import annotations

import socket

import pytest

_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


def _host_of(address) -> str:
    return str(address[0]) if isinstance(address, tuple) else str(address)


def _blocked_connect(self, address, *args, **kwargs):
    if _host_of(address) in _LOOPBACK:
        return _real_connect(self, address, *args, **kwargs)
    raise RuntimeError(f"回帰テストではネットワークに出ない設定です: {address!r}")


def _blocked_getaddrinfo(host, *args, **kwargs):
    if host is None or str(host) in _LOOPBACK:
        return _real_getaddrinfo(host, *args, **kwargs)
    raise RuntimeError(f"回帰テストでは名前解決もしない設定です: {host!r}")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)
    monkeypatch.delenv("REINFOLIB_API_KEY", raising=False)
    yield
