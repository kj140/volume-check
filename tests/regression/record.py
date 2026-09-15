"""回帰ケースの結果を保存する（初回、または意図した更新のとき**だけ**手で実行する）。

    .venv/Scripts/python.exe tests/regression/record.py            # 未保存のケースだけ
    .venv/Scripts/python.exe tests/regression/record.py --force    # すべて書き直す
    .venv/Scripts/python.exe tests/regression/record.py <ケース名> ... [--force]

result.json / dxf_summary.json が既にあるケースは、--force を付けない限り触らない。
CLAUDE.md「既存の結果を黙って変えない」のとおり、書き直すときは理由を記録し、
期待値の更新をユーザーに確認してから行う。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import regress  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回帰ケースの結果を保存する")
    parser.add_argument("names", nargs="*", help="ケース名（省略で全ケース）")
    parser.add_argument("--force", action="store_true", help="保存済みでも書き直す")
    args = parser.parse_args(argv)

    dirs = regress.case_dirs()
    if args.names:
        dirs = [d for d in dirs if d.name in set(args.names)]
        missing = set(args.names) - {d.name for d in dirs} - {regress.EXTERNAL_CASE_DIR.name}
        if missing:
            print(f"ケースが見つかりません: {sorted(missing)}", file=sys.stderr)
            return 2

    with tempfile.TemporaryDirectory(prefix="volume_check_regress_") as tmp:
        for case in dirs:
            result_path = case / "result.json"
            summary_path = case / "dxf_summary.json"
            if not args.force and result_path.exists() and summary_path.exists():
                print(f"skip   {case.name}（保存済み。書き直すなら --force）")
                continue
            spec = regress.load_json(case / "input.json")
            result, summary = regress.run_case(spec, Path(tmp) / f"{case.name}.dxf")
            regress.dump_json(result_path, result)
            regress.dump_json(summary_path, summary)
            print(f"record {case.name}")

    # 外部 API の記録済み応答から作る結果（tests/regression/zoning_lookup_offline/）
    ext = regress.EXTERNAL_CASE_DIR
    if (ext / "request.json").exists() and (not args.names or ext.name in args.names):
        result_path = ext / "result.json"
        if not args.force and result_path.exists():
            print(f"skip   {ext.name}（保存済み。書き直すなら --force）")
        else:
            regress.dump_json(result_path,
                              regress.run_external_api_case(regress.load_json(ext / "request.json")))
            print(f"record {ext.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
