"""SQLite による案と段の結果の保存。

テーブル
  schemes        案。本文は JSON（pydantic の model_dump）。id・親・敷地・作成日時は列にも持つ。
  stage_results  段の結果。(scheme_id, stage, input_hash, solver_version) で一意。

約束
  - 案は保存後に書き換えない。同じ id で違う内容を save_scheme すると拒否する。
  - 変更は derive() で子案を作る。差分の検査は model.patch.apply_patch が行う。
  - 段の結果は同じキーなら置き換える（再計算の結果で上書きしてよい）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from model.patch import PatchOp, apply_patch
from model.types import CreatedBy, Scheme, StageResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schemes (
    id          TEXT PRIMARY KEY,
    parent_id   TEXT REFERENCES schemes(id),
    site_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    label       TEXT,
    input_hash  TEXT NOT NULL,
    body        TEXT NOT NULL,
    seq         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS schemes_site ON schemes(site_id, seq);
CREATE INDEX IF NOT EXISTS schemes_parent ON schemes(parent_id, seq);

CREATE TABLE IF NOT EXISTS stage_results (
    scheme_id      TEXT NOT NULL REFERENCES schemes(id),
    stage          TEXT NOT NULL,
    input_hash     TEXT NOT NULL,
    solver_version TEXT NOT NULL,
    status         TEXT NOT NULL,
    body           TEXT NOT NULL,
    PRIMARY KEY (scheme_id, stage, input_hash, solver_version)
);
"""


def _dump(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


class SchemeStore:
    """案と段の結果を SQLite に保存する。with 文で使える。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SchemeStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- 案 ---------------------------------------------------------------

    def save_scheme(self, scheme: Scheme) -> None:
        """案を保存する。同じ id・同じ内容なら何もしない。内容が違えば拒否する。"""
        body = _dump(scheme)
        row = self._conn.execute("SELECT body FROM schemes WHERE id = ?", (scheme.id,)).fetchone()
        if row is not None:
            if row[0] == body:
                return
            raise ValueError(f"案 {scheme.id} は保存済みです。案は書き換えず、derive で子案を作ってください")
        if scheme.parent_id is not None and self.get_scheme(scheme.parent_id) is None:
            raise KeyError(f"親案 {scheme.parent_id} が見つかりません")
        seq = self._conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM schemes").fetchone()[0]
        self._conn.execute(
            "INSERT INTO schemes (id, parent_id, site_id, created_at, created_by, label,"
            " input_hash, body, seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scheme.id, scheme.parent_id, scheme.site_id, scheme.created_at.isoformat(),
             scheme.created_by, scheme.label, scheme.input_hash, body, seq),
        )
        self._conn.commit()

    def get_scheme(self, scheme_id: str) -> Scheme | None:
        row = self._conn.execute("SELECT body FROM schemes WHERE id = ?", (scheme_id,)).fetchone()
        return None if row is None else Scheme.model_validate_json(row[0])

    def derive(self, parent_id: str, patch: list[PatchOp], created_by: CreatedBy,
               label: str | None = None) -> Scheme:
        """親案に差分を当てた子案を作って保存する。親は変えない。

        差分が拒否されれば例外を送出し、何も保存しない。
        """
        parent = self.get_scheme(parent_id)
        if parent is None:
            raise KeyError(f"親案 {parent_id} が見つかりません")
        facts, plan = apply_patch(parent, patch, created_by)
        child = Scheme.new(facts, plan, created_by=created_by, label=label,
                           parent_id=parent.id, site_id=parent.site_id)
        self.save_scheme(child)
        return child

    def list_schemes(self, site_id: str) -> list[Scheme]:
        """同じ敷地の案を、作った順に返す。"""
        rows = self._conn.execute(
            "SELECT body FROM schemes WHERE site_id = ? ORDER BY seq", (site_id,)).fetchall()
        return [Scheme.model_validate_json(r[0]) for r in rows]

    def list_children(self, parent_id: str) -> list[Scheme]:
        rows = self._conn.execute(
            "SELECT body FROM schemes WHERE parent_id = ? ORDER BY seq", (parent_id,)).fetchall()
        return [Scheme.model_validate_json(r[0]) for r in rows]

    # --- 段の結果 -------------------------------------------------------------

    def save_result(self, result: StageResult) -> None:
        """段の結果を保存する。同じ (案, 段, input_hash, solver_version) なら置き換える。"""
        if self.get_scheme(result.scheme_id) is None:
            raise KeyError(f"案 {result.scheme_id} が見つかりません")
        self._conn.execute(
            "INSERT OR REPLACE INTO stage_results"
            " (scheme_id, stage, input_hash, solver_version, status, body)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (result.scheme_id, result.stage, result.input_hash, result.solver_version,
             result.status, _dump(result)),
        )
        self._conn.commit()

    def get_result(self, scheme_id: str, stage: str, input_hash: str,
                   solver_version: str) -> StageResult | None:
        row = self._conn.execute(
            "SELECT body FROM stage_results WHERE scheme_id = ? AND stage = ?"
            " AND input_hash = ? AND solver_version = ?",
            (scheme_id, stage, input_hash, solver_version),
        ).fetchone()
        return None if row is None else StageResult.model_validate_json(row[0])
