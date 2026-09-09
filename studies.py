"""複数案の自動生成と比較。

同じ敷地・法規条件のまま、設計者が動かせるパラメータだけを振って solve() を
繰り返し、結果を並べて比べる。1ケースの算定は数ミリ秒〜数十ミリ秒なので、
企画段階の当たりを付けるには総当たりで十分足りる。

振るのは次の3つ。いずれも「法規で決まっている値」ではなく計画側の選択。

  建物の向き    前面道路に対する振り角。非矩形の敷地では効きが大きい。
  階高          基準階の階高。低くすれば階数が増え、容積を使い切りやすい。
  外壁後退      隣地との離れ。大きくすると床は減るが、斜線の当たりが緩む。

法規条件（用途地域・建蔽率・容積率・防火地域など）は敷地の条件なので振らない。
建蔽率の緩和に関わる「耐火建築物等とするか」だけは計画の選択なので任意で振れる。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import constants as C
from models import VolumeInput, VolumeResult
from solver import solve

M2 = C.M2_TO_MM2
MM = C.M_TO_MM

# 既定の探索範囲。企画段階で実際に動かす幅に合わせている。
DEFAULT_ANGLES_DEG: tuple[float, ...] = (-30, -20, -15, -10, -5, 0, 5, 10, 15, 20, 30)
DEFAULT_FLOOR_HEIGHTS_M: tuple[float, ...] = (3.6, 3.9, 4.2)
DEFAULT_WALL_SETBACKS_M: tuple[float, ...] = (0.5, 1.0)

# 総当たりの上限。これを超える組み合わせは受け付けない（応答時間を守るため）
MAX_CASES = 400


@dataclass(frozen=True)
class StudyCase:
    """1 案。振ったパラメータと、その結果の要点。"""

    building_angle_deg: float
    floor_height_m: float
    wall_setback_m: float
    fireproof: bool

    floor_count: int
    max_height_m: float
    building_area_m2: float
    total_gross_area_m2: float
    total_far_area_m2: float
    total_rentable_area_m2: float
    achieved_far: float
    stop_reason: str
    dominant_constraint: str | None

    def to_dict(self) -> dict:
        return {
            "building_angle_deg": self.building_angle_deg,
            "floor_height_m": self.floor_height_m,
            "wall_setback_m": self.wall_setback_m,
            "fireproof": self.fireproof,
            "floor_count": self.floor_count,
            "max_height_m": round(self.max_height_m, 2),
            "building_area_m2": round(self.building_area_m2, 2),
            "total_gross_area_m2": round(self.total_gross_area_m2, 2),
            "total_far_area_m2": round(self.total_far_area_m2, 2),
            "total_rentable_area_m2": round(self.total_rentable_area_m2, 2),
            "achieved_far": round(self.achieved_far, 4),
            "stop_reason": self.stop_reason,
            "dominant_constraint": self.dominant_constraint,
        }


@dataclass
class StudyResult:
    """複数案の比較結果。best は延床面積が最大の案。"""

    cases: list[StudyCase] = field(default_factory=list)
    baseline: StudyCase | None = None      # 入力そのままの案
    notes: list[str] = field(default_factory=list)

    @property
    def best(self) -> StudyCase | None:
        return self.cases[0] if self.cases else None

    def to_dict(self) -> dict:
        return {
            "cases": [c.to_dict() for c in self.cases],
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "best": self.best.to_dict() if self.best else None,
            "gain_over_baseline_m2": (
                round(self.best.total_gross_area_m2 - self.baseline.total_gross_area_m2, 2)
                if self.best and self.baseline else None
            ),
            "notes": list(self.notes),
        }


def _summarize(case_input: VolumeInput, result: VolumeResult,
               angle_deg: float, fireproof: bool) -> StudyCase:
    program = case_input.program
    dominant = result.dominant_constraint
    return StudyCase(
        building_angle_deg=angle_deg,
        floor_height_m=program.floor_height_mm / MM,
        wall_setback_m=program.wall_setback_mm / MM,
        fireproof=fireproof,
        floor_count=result.floor_count,
        max_height_m=result.max_height_mm / MM,
        building_area_m2=result.building_area_mm2 / M2,
        total_gross_area_m2=result.total_gross_area_mm2 / M2,
        total_far_area_m2=result.total_far_area_mm2 / M2,
        total_rentable_area_m2=result.total_rentable_area_mm2 / M2,
        achieved_far=result.achieved_far,
        stop_reason=result.stop_reason.value if result.stop_reason else "",
        dominant_constraint=dominant.value if dominant else None,
    )


def generate(
    base: dict,
    angles_deg: tuple[float, ...] = DEFAULT_ANGLES_DEG,
    floor_heights_m: tuple[float, ...] = DEFAULT_FLOOR_HEIGHTS_M,
    wall_setbacks_m: tuple[float, ...] = DEFAULT_WALL_SETBACKS_M,
    fireproof_options: tuple[bool, ...] = (),
    limit: int = 50,
) -> StudyResult:
    """入力JSON（辞書）を基準に複数案を作り、延床面積の大きい順に返す。

    base は solve() に渡すのと同じ形の辞書。振らない項目はそのまま使う。
    fireproof_options が空なら base の設定のまま振らない。
    """
    base_fireproof = bool(base.get("program", {}).get("fireproof", False))
    fireproof_options = fireproof_options or (base_fireproof,)

    total = (len(angles_deg) * len(floor_heights_m)
             * len(wall_setbacks_m) * len(fireproof_options))
    if total > MAX_CASES:
        raise ValueError(
            f"組み合わせが多すぎます（{total} 通り）。"
            f"{MAX_CASES} 通り以下になるよう探索範囲を絞ってください。"
        )

    study = StudyResult()
    baseline_input = VolumeInput.from_dict(base)
    baseline_result = solve(baseline_input)
    study.baseline = _summarize(
        baseline_input, baseline_result,
        angle_deg=baseline_result.building_angle_deg,
        fireproof=base_fireproof,
    )

    seen: set[tuple] = set()
    for angle in angles_deg:
        for height in floor_heights_m:
            for setback in wall_setbacks_m:
                for fireproof in fireproof_options:
                    key = (angle, height, setback, fireproof)
                    if key in seen:
                        continue
                    seen.add(key)

                    d = copy.deepcopy(base)
                    d["program"]["building_angle"] = angle
                    d["program"]["floor_height"] = height
                    d["program"]["wall_setback"] = setback
                    d["program"]["fireproof"] = fireproof
                    try:
                        case_input = VolumeInput.from_dict(d)
                        result = solve(case_input)
                    except ValueError:
                        continue      # 成立しない組み合わせは飛ばす
                    study.cases.append(
                        _summarize(case_input, result, angle, fireproof)
                    )

    # 延床面積の大きい順。同じなら最高高さが低いほう（無理のない案）を上に。
    study.cases.sort(key=lambda c: (-c.total_gross_area_m2, c.max_height_m))
    study.cases = study.cases[:limit]
    _add_notes(study)
    return study


def _add_notes(study: StudyResult) -> None:
    """比較して分かることを一言で添える。"""
    best, baseline = study.best, study.baseline
    if best is None or baseline is None:
        study.notes.append("成立する案がありませんでした。")
        return

    gain = best.total_gross_area_m2 - baseline.total_gross_area_m2
    if gain > 1.0:
        study.notes.append(
            f"最良案は入力のままの案より延床 {gain:,.1f}m2 大きくなります"
            f"（振り角 {best.building_angle_deg:+.0f}度・基準階高 {best.floor_height_m}m"
            f"・外壁後退 {best.wall_setback_m}m）。"
        )
    else:
        study.notes.append("入力のままの案がほぼ最良でした。")

    angles = {c.building_angle_deg for c in study.cases[:5]}
    if len(angles) == 1:
        study.notes.append(
            f"上位の案はいずれも振り角 {next(iter(angles)):+.0f}度でした。"
        )

    if best.dominant_constraint:
        study.notes.append(
            f"最良案でも {best.dominant_constraint} が最大の削減要因です。"
        )
    if best.stop_reason:
        study.notes.append(f"最良案の打ち切り理由: {best.stop_reason}")

    study.notes.append(
        "振っているのは計画側の選択（建物の向き・階高・外壁後退）だけで、"
        "法規条件は変えていません。"
    )


def sweep_angles(base: dict, angles_deg: tuple[float, ...] = DEFAULT_ANGLES_DEG
                 ) -> list[tuple[float, float]]:
    """振り角と延床面積の関係だけを取り出す（グラフ用）。"""
    out = []
    for angle in angles_deg:
        d = copy.deepcopy(base)
        d["program"]["building_angle"] = angle
        try:
            result = solve(VolumeInput.from_dict(d))
        except ValueError:
            continue
        out.append((angle, result.total_gross_area_mm2 / M2))
    return out


def best_angle_deg(base: dict, angles_deg: tuple[float, ...] = DEFAULT_ANGLES_DEG
                   ) -> float | None:
    """延床面積が最大になる振り角。"""
    sweep = sweep_angles(base, angles_deg)
    return max(sweep, key=lambda t: t[1])[0] if sweep else None


def suggested_angles(site_shape, extra_deg: tuple[float, ...] = (-15, -10, -5, 5, 10, 15)
                     ) -> tuple[float, ...]:
    """敷地の辺の向きを候補に加えた振り角の一覧。

    非矩形の敷地では「どれかの辺に平行」が良い解になりやすいので、
    等間隔の候補に加えて各辺の向きも試す。
    """
    road = site_shape.widest_road
    base = road.angle_rad if road is not None else 0.0
    angles = {0.0, *(float(a) for a in extra_deg)}
    for edge_angle in site_shape.edge_angles_rad():
        delta = math.degrees(edge_angle - base)
        angles.add(round(((delta + 90) % 180) - 90, 1))
    return tuple(sorted(angles))
