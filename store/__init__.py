"""案と結果の保存（docs/architecture.md 10章）。SQLite から始める。

外からは SchemeStore のインターフェースだけを使う。DB を移すときはこのパッケージだけを
差し替える。
"""

from store.sqlite import SchemeStore

__all__ = ["SchemeStore"]
