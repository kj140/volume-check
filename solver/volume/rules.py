"""既存の出力（適用規定・注記）を StageResult の構造に移すための対応表。M02。

既存の算定（solver.solve）は、適用した規定を (label, value, basis) の並びで、
注記を文字列の並びで返す（DXF と画面にそのまま印字している）。ここでは、
それを architecture.md 4.6 の AppliedRule / NotConsidered / warnings に移す。

約束（M02 の指示）
- 既存にない項目を足さない。対応表にない label・注記が出たら例外にして止める
  （新しい規定や注記が足されたら、ここに対応を書き足すまで通さない）。
- NotConsidered.direction は docs/project_brief.md 5章に対応が書いてあるものだけ設定し、
  書いていないものは "unknown" にする。
  - 5章「未考慮（実際より小さく出る）」：法56条4項・令132条・法52条9項 → smaller_than_actual
  - 5章「未考慮（床面積＝容積対象。実際より厳しい）」：容積率不算入 → smaller_than_actual
  - 5章「安全側にならない」：日影規制 → larger_than_actual
  - 日影規制の未実装を常に出す注記（決定 0006）も larger_than_actual
- 法規の数値は持たない。ここにあるのは既存の出力の文字列と、それを分類するための表だけ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# 適用した規定：既存の label → rule_id
# ---------------------------------------------------------------------------
# effect は、既存の「規定ごとの削減量」（constraint_gains）のどの規定に当たるか。
# 1つの規定が既存で2行に分かれている場合（道路斜線の勾配と適用距離）は、
# 斜線そのものを表す行（勾配）にだけ付ける。

@dataclass(frozen=True)
class RuleMap:
    rule_id: str
    effect: str | None = None       # 既存の Constraint の値（"道路斜線" など）


RULES_BY_LABEL: dict[str, RuleMap] = {
    "用途地域": RuleMap("zoning.use_district"),
    "指定建蔽率": RuleMap("bcr.designated", effect="建蔽率"),
    "防火地域の指定": RuleMap("fire_zone.designation"),
    "建蔽率の緩和": RuleMap("bcr.relaxation"),
    "緩和後の建蔽率": RuleMap("bcr.effective"),
    "指定容積率": RuleMap("far.designated"),
    "前面道路幅員による低減": RuleMap("far.road_width"),
    "前面道路幅員による容積率": RuleMap("far.road_width"),
    "実効容積率": RuleMap("far.effective"),
    "道路斜線 勾配": RuleMap("slant.road.gradient", effect="道路斜線"),
    "道路斜線 適用距離": RuleMap("slant.road.applicable_distance"),
    "隣地斜線": RuleMap("slant.neighbor", effect="隣地斜線"),
    "北側斜線": RuleMap("slant.north", effect="北側斜線"),
    "絶対高さ制限": RuleMap("height.absolute"),
}

# 緩和で建蔽率が変わった案では、建蔽率の削減量は「緩和後の建蔽率」の行に付ける
BCR_EFFECT_WHEN_RELAXED = "bcr.effective"


# ---------------------------------------------------------------------------
# 注記 → 未考慮事項 / 伝達事項（warnings）
# ---------------------------------------------------------------------------

Direction = Literal["larger_than_actual", "smaller_than_actual", "unknown"]


@dataclass(frozen=True)
class NoteMap:
    pattern: str                    # 注記の先頭からの正規表現
    kind: Literal["not_considered", "warning"]
    item: str = ""                  # not_considered のときの項目名
    direction: Direction = "unknown"


NOTE_MAPS: tuple[NoteMap, ...] = (
    NoteMap(r"^容積率対象床面積は床面積と同一として算定", "not_considered",
            "容積率不算入（法52条3項〜6項）", "smaller_than_actual"),
    NoteMap(r"^法56条4項の後退距離による道路斜線の緩和は未考慮", "not_considered",
            "道路斜線の後退距離による緩和（法56条4項）", "smaller_than_actual"),
    NoteMap(r"^法52条9項の特定道路による容積率緩和は未考慮", "not_considered",
            "特定道路による容積率の緩和（法52条9項）", "smaller_than_actual"),
    NoteMap(r"^法53条3項の角地緩和・防火地域内耐火建築物の緩和は未適用", "not_considered",
            "建蔽率の緩和（法53条3項）の未指定", "unknown"),
    NoteMap(r"^【要注意】日影規制", "not_considered",
            "日影規制（法56条の2・別表第四）", "larger_than_actual"),
    NoteMap(r"^日影規制（法56条の2）は未実装", "not_considered",
            "日影規制（法56条の2・別表第四）", "larger_than_actual"),
    NoteMap(r"^用途地域の指定のない区域は法の原則値", "not_considered",
            "特定行政庁が定める値（用途地域の指定のない区域）", "unknown"),
    NoteMap(r"^外壁後退と斜線による後退は、足し合わせず大きいほうを適用", "warning"),
    NoteMap(r"^.+ の絶対高さ制限は都市計画で", "warning"),
    NoteMap(r"^建蔽率上限に収めるため全階を一律", "warning"),
)


class UnmappedLegacyOutput(ValueError):
    """対応表にない既存の出力が出た。rules.py に対応を書き足すまで通さない。"""


def rule_map(label: str) -> RuleMap:
    try:
        return RULES_BY_LABEL[label]
    except KeyError as e:
        raise UnmappedLegacyOutput(f"適用規定の対応表にない項目です: {label!r}") from e


def note_map(note: str) -> NoteMap:
    hits = [m for m in NOTE_MAPS if re.match(m.pattern, note)]
    if len(hits) != 1:
        raise UnmappedLegacyOutput(
            f"注記の対応が{'ありません' if not hits else '複数あります'}: {note!r}")
    return hits[0]
