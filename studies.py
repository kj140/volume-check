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

天空率（法56条7項1号）による道路斜線の緩和は任意で各案に付けられる。案ごとに
算定が要るので、表に出す上位の案だけを判定する。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field, replace

import constants as C
import skyfactor
from models import VolumeInput, VolumeResult
from solver import solve

M2 = C.M2_TO_MM2
MM = C.M_TO_MM

# 既定の探索範囲。企画段階で実際に動かす幅に合わせている。
DEFAULT_ANGLES_DEG: tuple[float, ...] = (-30, -20, -15, -10, -5, 0, 5, 10, 15, 20, 30)
DEFAULT_FLOOR_HEIGHTS_M: tuple[float, ...] = (3.6, 3.9, 4.2)
DEFAULT_WALL_SETBACKS_M: tuple[float, ...] = (0.5, 1.0)

# 天空率まで見るときの既定。天空率は外壁後退で大きく変わるので後退の候補を広げ、
# 代わりに振り角を粗くして総数を抑える。
SKY_ANGLES_DEG: tuple[float, ...] = (-20, -10, -5, 0, 5, 10, 20)
SKY_WALL_SETBACKS_M: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0)

# 総当たりの上限。これを超える組み合わせは受け付けない（応答時間を守るため）
MAX_CASES = 400

# 天空率を判定する案の数の上限。1案あたり0.1〜0.3秒かかるので、表の上位だけを見る。
MAX_SKY_CHECKS = 20


@dataclass(frozen=True)
class SkyVerdict:
    """その案について、天空率で道路斜線を外せるか（法56条7項1号）。

    checked が False なら判定していない（判定数の上限に掛かった案）。
    worth が False なら道路斜線を外しても延床が増えないので検討する意味がない。
    """

    checked: bool = False
    worth: bool = False
    passes: bool = False
    margin_pct: float | None = None      # 最も不利な算定位置での余裕[ポイント]
    gain_m2: float = 0.0                 # 通った場合に増える延床面積

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "worth": self.worth,
            "passes": self.passes,
            "margin_pct": (round(self.margin_pct, 3)
                           if self.margin_pct is not None else None),
            "gain_m2": round(self.gain_m2, 2),
        }


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

    sky: SkyVerdict | None = None

    # 延床順では上位に入らないが、天空率を見るために表に足した案
    added_for_sky: bool = False

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
            "sky": self.sky.to_dict() if self.sky else None,
            "added_for_sky": self.added_for_sky,
        }


@dataclass
class StudyResult:
    """複数案の比較結果。best は延床面積が最大の案。"""

    cases: list[StudyCase] = field(default_factory=list)
    baseline: StudyCase | None = None      # 入力そのままの案
    notes: list[str] = field(default_factory=list)

    # どの案も天空率で通らなかったとき、外壁後退を広げれば通る案（最良案について）
    sky_suggestion: skyfactor.Suggestion | None = None

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
            "sky_suggestion": (self.sky_suggestion.to_dict()
                               if self.sky_suggestion else None),
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
    check_sky: bool = False,
) -> StudyResult:
    """入力JSON（辞書）を基準に複数案を作り、延床面積の大きい順に返す。

    base は solve() に渡すのと同じ形の辞書。振らない項目はそのまま使う。
    fireproof_options が空なら base の設定のまま振らない。
    check_sky を立てると、表に出す上位の案に天空率の判定を付ける。
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
    solved: dict[tuple, VolumeResult] = {}
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
                    solved[key] = result
                    study.cases.append(
                        _summarize(case_input, result, angle, fireproof)
                    )

    # 延床面積の大きい順。同じなら最高高さが低いほう（無理のない案）を上に。
    study.cases.sort(key=lambda c: (-c.total_gross_area_m2, c.max_height_m))
    ranked = list(study.cases)
    study.cases = ranked[:limit]
    if check_sky:
        study.cases += _extra_setback_cases(ranked, limit)
        _attach_sky(study, solved, baseline_result)
    _add_notes(study)
    return study


def _extra_setback_cases(ranked: list[StudyCase], limit: int) -> list[StudyCase]:
    """外壁後退ごとの最良案のうち、表から漏れたものを拾う。

    天空率は外壁後退で大きく変わるのに、後退の大きい案は「斜線を守った延床」では
    上位に来ない。そのままだと判定列が全部×になって使えないので、後退の値ごとに
    最良の案を1つずつ足す。足すのは表の末尾で、延床の降順は崩さない
    （もともと limit 位より下の案なので必ず小さい）。
    """
    shown = {c.wall_setback_m for c in ranked[:limit]}
    extras: list[StudyCase] = []
    for case in ranked[limit:]:
        if case.wall_setback_m in shown:
            continue
        shown.add(case.wall_setback_m)
        extras.append(replace(case, added_for_sky=True))
    return extras


def _case_key(case: StudyCase) -> tuple:
    return (case.building_angle_deg, case.floor_height_m,
            case.wall_setback_m, case.fireproof)


def _sky_verdict(result: VolumeResult) -> SkyVerdict:
    """1 案の天空率の判定。通る案の探索まではしない（表に出すのは可否だけ）。"""
    study = skyfactor.evaluate(result, suggest=False)
    margin = study.worst_margin
    return SkyVerdict(
        checked=True,
        worth=study.worth_studying,
        passes=study.passes,
        # 表と注記で同じ数字が出るよう、ここで丸めておく
        margin_pct=round(margin * 100.0, 3) if margin is not None else None,
        gain_m2=study.gain_mm2 / M2,
    )


def _attach_sky(study: StudyResult, solved: dict[tuple, VolumeResult],
                baseline_result: VolumeResult) -> None:
    """表に出す上位の案に天空率の判定を付ける。

    1案あたり0.1〜0.3秒かかるので MAX_SKY_CHECKS 件までとし、
    それ以降の案は「判定していない」ことが分かるようにしておく。
    """
    if study.baseline is not None:
        study.baseline = replace(study.baseline, sky=_sky_verdict(baseline_result))
    checked: list[StudyCase] = []
    for i, case in enumerate(study.cases):
        result = solved.get(_case_key(case))
        if i < MAX_SKY_CHECKS and result is not None:
            checked.append(replace(case, sky=_sky_verdict(result)))
        else:
            checked.append(replace(case, sky=SkyVerdict()))
    study.cases = checked

    # どれも通らないなら、最良案について「どこまで後退させれば通るか」を一度だけ探す
    judged = [c for c in study.cases if c.sky is not None and c.sky.checked]
    if judged and not any(c.sky.passes for c in judged) \
            and any(c.sky.worth for c in judged):
        best = solved.get(_case_key(study.cases[0]))
        if best is not None:
            study.sky_suggestion = skyfactor.search_wall_setback(best)


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
    _add_sky_notes(study)


def _add_sky_notes(study: StudyResult) -> None:
    """天空率まで見たときに何が言えるかを添える。"""
    judged = [c for c in study.cases if c.sky is not None and c.sky.checked]
    if not judged:
        return

    passing = [c for c in judged if c.sky.worth and c.sky.passes]
    if passing:
        top = max(passing, key=lambda c: c.total_gross_area_m2 + c.sky.gain_m2)
        study.notes.append(
            f"天空率まで見ると、振り角 {top.building_angle_deg:+.0f}度・"
            f"階高 {top.floor_height_m}m・外壁後退 {top.wall_setback_m}m の案が"
            f"道路斜線を外せる見込みで、延床は "
            f"{top.total_gross_area_m2 + top.sky.gain_m2:,.1f}m2"
            f"（+{top.sky.gain_m2:,.1f}m2）まで伸ばせます。"
        )
    elif any(c.sky.worth for c in judged):
        best = max((c for c in judged if c.sky.worth),
                   key=lambda c: c.sky.margin_pct or -1e9)
        note = (
            f"天空率で道路斜線を外せる案は上位にありませんでした。"
            f"最も惜しいのは振り角 {best.building_angle_deg:+.0f}度・"
            f"外壁後退 {best.wall_setback_m}m の案で "
            f"{best.sky.margin_pct:+.2f} ポイントです。"
        )
        study.notes.append(note)
        s = study.sky_suggestion
        if s is not None:
            study.notes.append(
                f"最良案の外壁後退を {s.wall_setback_mm / MM:.1f}m まで広げれば"
                f"{s.floor_count}階・{s.max_height_mm / MM:.2f}m・"
                f"延床 {s.total_gross_area_mm2 / M2:,.1f}m2 で通る見込みです。"
            )
        study.notes.append(
            "表の並びは斜線を守ったときの延床順です。天空率で外せる案は"
            "外壁後退が大きく、この並びでは下に来ます。"
        )
    else:
        study.notes.append(
            "上位の案はいずれも道路斜線を外しても延床が増えないため、"
            "天空率を検討する意味はありません。"
        )

    extras = sum(1 for c in study.cases if c.added_for_sky)
    if extras:
        study.notes.append(
            f"末尾の {extras} 案は延床順では表に入りませんが、"
            f"外壁後退ごとの最良案として天空率のために足しています。"
        )

    unchecked = sum(1 for c in study.cases
                    if c.sky is not None and not c.sky.checked)
    if unchecked:
        study.notes.append(
            f"天空率の判定は上位 {MAX_SKY_CHECKS} 案までです"
            f"（残り {unchecked} 案は未判定）。"
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
