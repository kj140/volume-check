"""CLI: python main.py input.json out.dxf

入力JSON（m単位）を読み、階別の可能形状を算定して DXF を 1 枚書き出す。
--json を付けると算定結果の要約を JSON でも標準出力に出す。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import constants as C
from drawer import draw
from models import VolumeInput
from solver import solve

M2 = C.M2_TO_MM2
MM = C.M_TO_MM


def _print_summary(result) -> None:
    r = result
    z = r.input.zoning
    far_by_road = "適用なし（幅員12m以上）" if r.far_by_road is None else f"{r.far_by_road * 100:.0f}%"
    print(f"敷地面積      : {r.site_area_mm2 / M2:,.2f} m2")
    print(f"用途地域      : {z.use_district.value}")
    print(f"容積率        : 指定 {z.far_designated * 100:.0f}%"
          f" / 道路幅員による {far_by_road}"
          f" / 実効 {r.far_effective * 100:.0f}%")
    print(f"建蔽率        : 指定 {z.bcr * 100:.0f}%"
          f" / 建築面積 {r.building_area_mm2 / M2:,.2f} m2 ({r.achieved_bcr * 100:.1f}%)")
    if r.bcr_inset_mm > 0:
        print(f"              （建蔽率のため全階を一律 {r.bcr_inset_mm / MM:.3f}m 内側に絞り込み）")
    print(f"階数 / 最高高さ: {r.floor_count} 階 / {r.max_height_mm / MM:,.2f} m")
    print(f"延床面積      : {r.total_gross_area_mm2 / M2:,.2f} m2"
          f"（容積対象 {r.total_far_area_mm2 / M2:,.2f} m2"
          f" / 上限 {r.max_far_area_mm2 / M2:,.2f} m2）")
    print(f"貸室面積      : {r.total_rentable_area_mm2 / M2:,.2f} m2")
    print(f"打ち切り理由  : {r.stop_reason.value if r.stop_reason else '-'}")
    print(f"              {r.stop_detail}")
    if r.floors:
        print()
        print("  階   天端(m)   道路SB(m)  隣地SB(m)   間口(m)   奥行(m)   床面積(m2)  支配規定")
        for f in r.floors:
            print(f"  {f.floor:>2}F  {f.top_mm / MM:>7.2f}  {f.setback_road_mm / MM:>9.2f}"
                  f"  {f.setback_neighbor_mm / MM:>8.2f}  {f.width_mm / MM:>8.3f}"
                  f"  {f.depth_mm / MM:>8.3f}  {f.gross_area_mm2 / M2:>10.2f}"
                  f"  {f.governing}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="矩形敷地のボリュームチェック図（DXF）を生成する"
    )
    parser.add_argument("input", type=Path, help="入力JSON（単位はm）")
    parser.add_argument("output", type=Path, help="出力DXF")
    parser.add_argument("--json", action="store_true", help="算定結果の要約をJSONでも出力")
    args = parser.parse_args(argv)

    try:
        inp = VolumeInput.from_json_file(args.input)
    except (OSError, KeyError, ValueError) as e:
        print(f"入力の読み込みに失敗しました: {e}", file=sys.stderr)
        return 2

    try:
        result = solve(inp)
    except ValueError as e:
        print(f"算定できません: {e}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    draw(result, args.output)

    _print_summary(result)
    print()
    print(f"DXF を書き出しました: {args.output}")
    if result.floor_count == 0:
        print("※ 建築可能な階が成立しないため、図面には建物が描かれていません。")

    if args.json:
        print()
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
