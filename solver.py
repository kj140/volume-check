"""階別の建築可能形状の算定。

矩形敷地に限定しているため、幾何ライブラリを使わず float の四則演算だけで解く。
座標系は models.py 冒頭の定義に従う（y = 0 が道路境界線）。

斜線の判定高さ:
  各階の「天端高さ」（level + 階高）で判定する。階の中で最も不利な高さであり、
  この高さで斜線に収まっていればその階全体が収まる。

道路斜線の適用距離の扱い（法56条1項1号・別表第三(は)欄）:
  道路斜線制限は「前面道路の反対側の境界線から適用距離 L を超える範囲」には
  かからない。したがって高さ h に対して反対側境界から必要な水平距離は
    min(h / 勾配, L)
  であり、敷地の道路境界からの後退量は
    max(0, min(h / 勾配, L) - 道路幅員)
  となる。h / 勾配 が L を超えた時点で道路斜線は建物形状を支配しなくなる。
"""

from __future__ import annotations

import math

import constants as C
from models import (
    NO_CONSTRAINT_LABEL,
    AppliedRule,
    Constraint,
    ConstraintImpact,
    FloorResult,
    StopReason,
    VolumeInput,
    VolumeResult,
)

# 面積・長さ比較用の許容誤差。mm 単位系での丸め誤差を吸収するだけの値。
_AREA_EPS_MM2 = 1.0        # 1 mm2 = 1e-6 m2
_LENGTH_EPS_MM = 1e-6


def solve(inp: VolumeInput) -> VolumeResult:
    """敷地条件・法規条件から階別の可能形状を算定する。

    階数が 0 になる入力でも例外は投げず、stop_reason に理由を入れて返す。
    特定行政庁の指定や都市計画によって値が変わる項目は法の原則値を用い、
    どの値を採ったかを applied_rules / notes に記録する。
    """
    site, zoning, program = inp.site, inp.zoning, inp.program
    district = zoning.use_district

    # 低層住専・田園住居は法55条の絶対高さ制限が必ずかかる。入力がなければ
    # 指定の多数である 10m を既定として適用し、その旨を結果に記録する。
    height_limit_mm = zoning.height_limit_absolute_mm
    if height_limit_mm is None and district in C.DISTRICTS_REQUIRING_ABSOLUTE_HEIGHT_LIMIT:
        height_limit_mm = C.DEFAULT_ABSOLUTE_HEIGHT_LIMIT_M * C.M_TO_MM

    # --- 1) 実効容積率（法52条2項） -----------------------------------------
    road_width_m = site.road_width_mm / C.M_TO_MM
    coefficient = C.far_road_width_coefficient(district)
    if road_width_m < C.FAR_ROAD_WIDTH_COEFFICIENT_THRESHOLD_M:
        far_by_road: float | None = road_width_m * coefficient
        far_effective = min(zoning.far_designated, far_by_road)
    else:
        # 幅員 12m 以上は前面道路幅員による低減を受けない
        far_by_road = None
        far_effective = zoning.far_designated

    # --- 2) 3) 上限面積 ------------------------------------------------------
    site_area_mm2 = site.area_mm2
    max_far_area_mm2 = site_area_mm2 * far_effective
    # 建蔽率は防火地域・角地による緩和を反映する（法53条3項・6項）
    bcr_effective, bcr_relaxations = C.relaxed_bcr(
        zoning.bcr, zoning.fire_zone, program.fireproof, site.corner_lot
    )
    max_building_area_mm2 = site_area_mm2 * bcr_effective

    # --- 斜線の根拠値 --------------------------------------------------------
    road_gradient = C.road_slant_gradient(district)
    # 適用距離の判定は指定容積率で行う（別表第三(ろ)欄）。実効容積率ではない。
    applicable_distance_mm = (
        C.road_slant_applicable_distance_m(district, zoning.far_designated) * C.M_TO_MM
    )
    neighbor = C.neighbor_slant(district)
    neighbor_start_mm = None if neighbor is None else neighbor[0] * C.M_TO_MM
    neighbor_gradient = None if neighbor is None else neighbor[1]

    result = VolumeResult(
        input=inp,
        far_designated=zoning.far_designated,
        far_by_road=far_by_road,
        far_effective=far_effective,
        far_road_coefficient=coefficient,
        site_area_mm2=site_area_mm2,
        max_far_area_mm2=max_far_area_mm2,
        max_building_area_mm2=max_building_area_mm2,
        road_slant_gradient=road_gradient,
        road_slant_applicable_distance_mm=applicable_distance_mm,
        neighbor_slant_start_mm=neighbor_start_mm,
        neighbor_slant_gradient=neighbor_gradient,
        height_limit_applied_mm=height_limit_mm,
        bcr_effective=bcr_effective,
        bcr_relaxations=bcr_relaxations,
    )
    _record_applied_rules(result)

    # --- 6) 建蔽率による全階一律の絞り込み量を先に求める ----------------------
    # 仕様の手順では最後に絞るが、絞ると容積対象床面積が減って階数条件が変わるため、
    # 1階の形状だけを先に試算して絞り込み量 t を確定させ、それを全階に適用する。
    # 「全階を等しく内側に絞る」という結果は同じで、容積の打ち切り判定が正しくなる。
    first_top_mm = program.height_of_floor_mm(1)
    sb_road_1, sb_neighbor_1, _ = _setbacks(
        first_top_mm, site.road_width_mm, road_gradient, applicable_distance_mm,
        neighbor_start_mm, neighbor_gradient,
    )
    w1, d1 = _footprint_size(site, program, sb_road_1, sb_neighbor_1, inset_mm=0.0)
    bcr_inset_mm = _bcr_inset(w1, d1, max_building_area_mm2)
    result.bcr_inset_mm = bcr_inset_mm

    # --- 4) 5) 1階から順に積み上げる -----------------------------------------
    level_mm = 0.0
    cumulative_far_mm2 = 0.0
    min_area_mm2 = C.MIN_VIABLE_FLOOR_AREA_M2 * C.M2_TO_MM2

    for floor in range(1, program.max_floors + 1):
        story_mm = program.height_of_floor_mm(floor)
        top_mm = level_mm + story_mm

        # 絶対高さ制限（法55条 / 高度地区等による指定値）
        if height_limit_mm is not None and top_mm > height_limit_mm + _LENGTH_EPS_MM:
            result.stop_reason = StopReason.ABSOLUTE_HEIGHT_LIMIT
            result.stop_detail = (
                f"{floor}階の天端 {top_mm / C.M_TO_MM:.2f}m が"
                f"絶対高さ制限 {height_limit_mm / C.M_TO_MM:.2f}m を超える"
            )
            break

        sb_road, sb_neighbor, road_capped = _setbacks(
            top_mm, site.road_width_mm, road_gradient, applicable_distance_mm,
            neighbor_start_mm, neighbor_gradient,
        )
        width_mm, depth_mm = _footprint_size(site, program, sb_road, sb_neighbor, bcr_inset_mm)

        if width_mm <= _LENGTH_EPS_MM or depth_mm <= _LENGTH_EPS_MM:
            result.stop_reason = StopReason.NO_EFFECTIVE_FOOTPRINT
            result.stop_detail = (
                f"{floor}階で有効間口 {width_mm / C.M_TO_MM:.2f}m / "
                f"有効奥行 {depth_mm / C.M_TO_MM:.2f}m となり成立しない"
            )
            break

        area_mm2 = width_mm * depth_mm
        if area_mm2 < min_area_mm2 - _AREA_EPS_MM2:
            result.stop_reason = StopReason.BELOW_MIN_FLOOR_AREA
            result.stop_detail = (
                f"{floor}階の床面積 {area_mm2 / C.M2_TO_MM2:.2f}m2 が"
                f"最小成立面積 {C.MIN_VIABLE_FLOOR_AREA_M2:.0f}m2 未満"
            )
            break

        # 容積の上限。端数調整はせず、収まらない階は切り捨てる。
        if cumulative_far_mm2 + area_mm2 > max_far_area_mm2 + _AREA_EPS_MM2:
            result.stop_reason = StopReason.FAR_LIMIT_REACHED
            result.stop_detail = (
                f"{floor}階を加えると累計容積対象床面積が上限 "
                f"{max_far_area_mm2 / C.M2_TO_MM2:.2f}m2 を超える"
                f"（{floor - 1}階までで {cumulative_far_mm2 / C.M2_TO_MM2:.2f}m2）"
            )
            break

        result.floors.append(
            _make_floor(
                floor=floor,
                level_mm=level_mm,
                story_mm=story_mm,
                site=site,
                program=program,
                sb_road=sb_road,
                sb_neighbor=sb_neighbor,
                inset_mm=bcr_inset_mm,
                road_capped=road_capped,
            )
        )
        cumulative_far_mm2 += area_mm2
        level_mm = top_mm

        if floor == program.max_floors:
            result.stop_reason = StopReason.MAX_FLOORS_REACHED
            result.stop_detail = f"max_floors = {program.max_floors} に到達"

    if result.stop_reason is None:
        # ループが break も max_floors 到達もせずに終わることはない
        raise AssertionError("打ち切り理由が記録されていない")

    return result


# ---------------------------------------------------------------------------
# 内部関数
# ---------------------------------------------------------------------------


def _setbacks(
    top_mm: float,
    road_width_mm: float,
    road_gradient: float,
    applicable_distance_mm: float,
    neighbor_start_mm: float | None,
    neighbor_gradient: float | None,
) -> tuple[float, float, bool]:
    """天端高さ top_mm における道路側・隣地側の後退量を返す。

    戻り値 = (道路側後退量, 隣地側後退量, 道路斜線が適用距離で頭打ちになったか)
    いずれも外壁後退を含まない、斜線制限だけによる後退量。
    """
    # 道路斜線（法56条1項1号）
    required_from_opposite_mm = top_mm / road_gradient
    road_capped = required_from_opposite_mm > applicable_distance_mm
    if road_capped:
        # 適用距離を超える範囲には道路斜線制限がかからない
        required_from_opposite_mm = applicable_distance_mm
    sb_road = max(0.0, required_from_opposite_mm - road_width_mm)

    # 隣地斜線（法56条1項2号）。適用のない用途地域は 0。
    if neighbor_start_mm is None or neighbor_gradient is None:
        sb_neighbor = 0.0
    else:
        sb_neighbor = max(0.0, (top_mm - neighbor_start_mm) / neighbor_gradient)

    return sb_road, sb_neighbor, road_capped


def _footprint_size(
    site, program, sb_road: float, sb_neighbor: float, inset_mm: float
) -> tuple[float, float]:
    """有効間口・有効奥行を返す。

    間口方向は左右とも隣地。奥行方向は道路側が道路斜線、反対側が隣地斜線。
    """
    width = site.frontage_mm - 2 * sb_neighbor - 2 * program.wall_setback_mm - 2 * inset_mm
    depth = site.depth_mm - sb_road - sb_neighbor - 2 * program.wall_setback_mm - 2 * inset_mm
    return width, depth


def _bcr_inset(width_mm: float, depth_mm: float, max_building_area_mm2: float) -> float:
    """1階が建蔽率上限を超える場合に全階へ一律適用する内側への絞り込み量[mm]。

    (w - 2t)(d - 2t) = A を満たす最小の t を解く。超えていなければ 0。
    """
    if width_mm <= 0 or depth_mm <= 0:
        return 0.0
    area = width_mm * depth_mm
    if area <= max_building_area_mm2 + _AREA_EPS_MM2:
        return 0.0
    s = width_mm + depth_mm
    disc = s * s - 4.0 * (area - max_building_area_mm2)
    if disc < 0:
        # 目標面積が小さすぎて矩形を保てない。片側が潰れる直前まで絞る。
        return min(width_mm, depth_mm) / 2.0
    return (s - math.sqrt(disc)) / 4.0


def _area(site, program, sb_road: float, sb_neighbor: float, inset_mm: float) -> float:
    """後退量の組み合わせに対する床面積。潰れる場合は 0。"""
    w, d = _footprint_size(site, program, sb_road, sb_neighbor, inset_mm)
    return w * d if w > 0 and d > 0 else 0.0


def _impacts(site, program, sb_road: float, sb_neighbor: float,
             inset_mm: float) -> tuple[ConstraintImpact, ...]:
    """その階を削っている規定ごとの限界寄与を求める。

    「その規定だけを外したら床面積がどれだけ増えるか」を規定ごとに計算する。
    規定どうしは掛け算で効くので合計は実際の減少量と一致しないが、
    「この制限が外れたら何m2増えるか」という設計上の判断には直接使える。
    """
    actual = _area(site, program, sb_road, sb_neighbor, inset_mm)
    candidates = (
        (Constraint.ROAD_SLANT, sb_road,
         _area(site, program, 0.0, sb_neighbor, inset_mm)),
        (Constraint.NEIGHBOR_SLANT, sb_neighbor,
         _area(site, program, sb_road, 0.0, inset_mm)),
        (Constraint.BCR, inset_mm,
         _area(site, program, sb_road, sb_neighbor, 0.0)),
    )
    impacts = [
        ConstraintImpact(constraint=c, area_gain_mm2=relaxed - actual, setback_mm=amount)
        for c, amount, relaxed in candidates
        if amount > _LENGTH_EPS_MM and relaxed - actual > _AREA_EPS_MM2
    ]
    impacts.sort(key=lambda i: i.area_gain_mm2, reverse=True)
    return tuple(impacts)


def _make_floor(
    *,
    floor: int,
    level_mm: float,
    story_mm: float,
    site,
    program,
    sb_road: float,
    sb_neighbor: float,
    inset_mm: float,
    road_capped: bool,
) -> FloorResult:
    edge = program.wall_setback_mm + inset_mm
    impacts = _impacts(site, program, sb_road, sb_neighbor, inset_mm)
    return FloorResult(
        floor=floor,
        level_mm=level_mm,
        story_height_mm=story_mm,
        x_min_mm=edge + sb_neighbor,
        x_max_mm=site.frontage_mm - edge - sb_neighbor,
        y_min_mm=edge + sb_road,
        y_max_mm=site.depth_mm - edge - sb_neighbor,
        setback_road_mm=sb_road,
        setback_neighbor_mm=sb_neighbor,
        governing=_governing(sb_road, sb_neighbor, inset_mm, road_capped),
        impacts=impacts,
        road_slant_capped=road_capped,
        # 斜線も建蔽率もかからず、外壁後退だけを引いた場合の床面積
        unconstrained_area_mm2=_area(site, program, 0.0, 0.0, 0.0),
    )


def _governing(sb_road: float, sb_neighbor: float, inset_mm: float, road_capped: bool) -> str:
    parts: list[str] = []
    if sb_road > _LENGTH_EPS_MM:
        parts.append("道路斜線(適用距離で頭打ち)" if road_capped else "道路斜線")
    if sb_neighbor > _LENGTH_EPS_MM:
        parts.append("隣地斜線")
    if inset_mm > _LENGTH_EPS_MM:
        parts.append("建蔽率")
    if not parts:
        return NO_CONSTRAINT_LABEL
    return " + ".join(parts)


def _record_applied_rules(r: VolumeResult) -> None:
    """適用した規定と根拠値、および未考慮事項を結果に記録する。"""
    z = r.input.zoning
    district = z.use_district

    r.applied_rules.append(AppliedRule("用途地域", district.value, "都市計画法8条1項1号"))
    r.applied_rules.append(AppliedRule("指定建蔽率", f"{z.bcr * 100:.0f}%", "法53条1項"))
    if z.fire_zone is not C.FireZone.NONE:
        r.applied_rules.append(
            AppliedRule("防火地域の指定", z.fire_zone.value, "法61条")
        )
    for description, basis in r.bcr_relaxations:
        r.applied_rules.append(AppliedRule("建蔽率の緩和", description, basis))
    if r.bcr_relaxations:
        r.applied_rules.append(
            AppliedRule("緩和後の建蔽率", f"{r.bcr_effective * 100:.0f}%", "法53条3項・6項")
        )
    r.applied_rules.append(
        AppliedRule("指定容積率", f"{z.far_designated * 100:.0f}%", "法52条1項")
    )
    if r.far_by_road is None:
        r.applied_rules.append(
            AppliedRule(
                "前面道路幅員による低減",
                f"適用なし（幅員 {r.input.site.road_width_mm / C.M_TO_MM:.1f}m ≧ "
                f"{C.FAR_ROAD_WIDTH_COEFFICIENT_THRESHOLD_M:.0f}m）",
                "法52条2項",
            )
        )
    else:
        r.applied_rules.append(
            AppliedRule(
                "前面道路幅員による容積率",
                f"{r.input.site.road_width_mm / C.M_TO_MM:.1f}m × {r.far_road_coefficient:.1f}"
                f" = {r.far_by_road * 100:.0f}%",
                "法52条2項",
            )
        )
    r.applied_rules.append(
        AppliedRule("実効容積率", f"{r.far_effective * 100:.0f}%", "法52条1項・2項")
    )
    r.applied_rules.append(
        AppliedRule("道路斜線 勾配", f"{r.road_slant_gradient}", "法56条1項1号・別表第三(に)欄")
    )
    r.applied_rules.append(
        AppliedRule(
            "道路斜線 適用距離",
            f"{r.road_slant_applicable_distance_mm / C.M_TO_MM:.0f}m"
            f"（指定容積率 {z.far_designated * 100:.0f}% による）",
            "法56条1項1号・別表第三(は)欄",
        )
    )
    if r.neighbor_slant_start_mm is None:
        r.applied_rules.append(
            AppliedRule("隣地斜線", "適用なし（絶対高さ制限による地域）", "法56条1項2号・法55条")
        )
    else:
        r.applied_rules.append(
            AppliedRule(
                "隣地斜線",
                f"立ち上がり {r.neighbor_slant_start_mm / C.M_TO_MM:.0f}m"
                f" / 勾配 {r.neighbor_slant_gradient}",
                "法56条1項2号",
            )
        )
    if r.height_limit_applied_mm is not None:
        defaulted = z.height_limit_absolute_mm is None
        r.applied_rules.append(
            AppliedRule(
                "絶対高さ制限",
                f"{r.height_limit_applied_mm / C.M_TO_MM:.1f}m"
                + ("（都市計画の指定がないため既定値を適用）" if defaulted else ""),
                "法55条 / 高度地区等",
            )
        )
        if defaulted:
            r.notes.append(
                f"{district.value} の絶対高さ制限は都市計画で"
                f"{'m または '.join(str(v) for v in C.ABSOLUTE_HEIGHT_LIMIT_CHOICES_M)}m"
                f"のいずれかに定められる。入力がないため既定の"
                f"{C.DEFAULT_ABSOLUTE_HEIGHT_LIMIT_M:.0f}m で試算"
            )

    # 未考慮事項（推測で緩和を適用していないことを明示する）
    r.notes.append("容積率対象床面積は床面積と同一として算定（法52条3項〜6項の不算入は未考慮）")
    r.notes.append("法56条4項の後退距離による道路斜線の緩和は未考慮（安全側）")
    if not r.bcr_relaxations:
        r.notes.append(
            "法53条3項の角地緩和・防火地域内耐火建築物の緩和は未適用"
            "（該当する場合は入力で指定すると反映されます）"
        )
    r.notes.append("法52条9項の特定道路による容積率緩和は未考慮（安全側）")
    if district == C.UseDistrict.UNDESIGNATED:
        r.notes.append(
            "用途地域の指定のない区域は法の原則値（道路斜線1.5・容積率係数6/10）で試算。"
            "特定行政庁が別の値を指定している場合は再計算が必要"
        )
    if r.bcr_inset_mm > _LENGTH_EPS_MM:
        r.notes.append(
            f"建蔽率上限に収めるため全階を一律 {r.bcr_inset_mm / C.M_TO_MM:.3f}m 内側に絞り込み"
        )
