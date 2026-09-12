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
import geometry as G
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
    north = C.north_slant(district, zoning.shadow_regulation)
    north_start_mm = None if north is None else north[0] * C.M_TO_MM
    north_gradient = None if north is None else north[1]

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
        north_slant_start_mm=north_start_mm,
        north_slant_gradient=north_gradient,
        height_limit_applied_mm=height_limit_mm,
        bcr_effective=bcr_effective,
        bcr_relaxations=bcr_relaxations,
    )
    _record_applied_rules(result)

    # --- 建物の向きと外形 -----------------------------------------------------
    # 既定は前面道路に平行。program.building_angle で明示的に回すこともできる。
    road_angle = site.road_edge.angle_rad
    angle = (road_angle if program.building_angle_rad is None
             else road_angle + program.building_angle_rad)
    result.building_angle_rad = angle

    def _north(top_mm: float) -> float:
        """その天端で必要な真北方向の距離（法56条1項3号）。適用がなければ 0。"""
        return north_setback(top_mm, north_start_mm, north_gradient)

    def _region(top_mm: float, inset_mm: float,
                setbacks: list[float] | None = None):
        """天端 top_mm における建築可能領域。北側斜線までここで適用する。"""
        region = G.buildable_region(
            site.shape,
            setbacks if setbacks is not None else _setbacks(
                site, top_mm, road_gradient, applicable_distance_mm,
                neighbor_start_mm, neighbor_gradient),
            program.wall_setback_mm,
            inset_mm,
        )
        return G.north_slant_region(region, site.shape.polygon,
                                    site.north_angle_rad, _north(top_mm))

    first_top_mm = program.height_of_floor_mm(1)
    region_1f = _region(first_top_mm, 0.0)
    footprint = G.largest_inscribed_rectangle(region_1f, angle) or region_1f
    result.footprint = G.outline(footprint)

    # --- 建蔽率による全階一律の絞り込み ----------------------------------------
    # 仕様の手順では最後に絞るが、絞ると容積対象床面積が減って階数条件が変わるため、
    # 1階の形状だけを先に試算して絞り込み量を確定させ、それを全階に適用する。
    # 絞り込みは全周を等しく内側に寄せるもので、外壁後退・斜線とは別に最後に足す
    # （外壁後退と斜線は互いに「大きいほう」だけが効く。geometry.py を参照）。
    def _area_at(inset_mm: float, top_mm: float = first_top_mm,
                 setbacks: list[float] | None = None) -> float:
        return G.area_mm2(_region(top_mm, inset_mm, setbacks).intersection(footprint))

    def _area_no_north(inset_mm: float, top_mm: float,
                       setbacks: list[float]) -> float:
        """北側斜線だけを外したときの床面積。要因の切り分けに使う。"""
        region = G.buildable_region(
            site.shape, setbacks, program.wall_setback_mm, inset_mm)
        return G.area_mm2(region.intersection(footprint))

    bcr_inset_mm = G.inset_for_area(
        _area_at, max_building_area_mm2, math.sqrt(site.area_mm2) / 2.0 + 1.0
    )
    result.bcr_inset_mm = bcr_inset_mm

    # --- 1階から順に積み上げる -------------------------------------------------
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

        setbacks = _setbacks(site, top_mm, road_gradient, applicable_distance_mm,
                             neighbor_start_mm, neighbor_gradient)
        shape = _region(top_mm, bcr_inset_mm, setbacks).intersection(footprint)
        area_mm2 = G.area_mm2(shape)

        if area_mm2 <= _AREA_EPS_MM2:
            result.stop_reason = StopReason.NO_EFFECTIVE_FOOTPRINT
            result.stop_detail = f"{floor}階で建築可能な範囲がなくなる"
            break

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
                floor=floor, level_mm=level_mm, story_mm=story_mm,
                site=site, shape=shape, setbacks=setbacks,
                impacts=_impacts(site, program, footprint, setbacks, bcr_inset_mm,
                                 area_mm2, _area_at, top_mm,
                                 north_mm=_north(top_mm),
                                 without_north=_area_no_north(bcr_inset_mm, top_mm,
                                                              setbacks)),
                north_mm=_north(top_mm),
                road_capped=_road_capped(site, top_mm, road_gradient,
                                         applicable_distance_mm),
                unconstrained_area_mm2=_area_at(
                    0.0, setbacks=[0.0] * len(site.shape.edges)),
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
# 斜線による後退量。skyfactor.py も使う（斜線の式はここが唯一の定義）
# ---------------------------------------------------------------------------


def road_setback(top_mm: float, road_width_mm: float, gradient: float,
                 applicable_distance_mm: float) -> tuple[float, bool]:
    """道路斜線による後退量と、適用距離で頭打ちになったか（法56条1項1号）。

    天空率の適合建築物（skyfactor.py）も同じ式で包絡線を作るので公開している。
    """
    required = top_mm / gradient
    capped = required > applicable_distance_mm
    if capped:
        # 適用距離を超える範囲には道路斜線制限がかからない
        required = applicable_distance_mm
    return max(0.0, required - road_width_mm), capped


def neighbor_setback(top_mm: float, start_mm: float | None,
                     gradient: float | None) -> float:
    """隣地斜線による後退量（法56条1項2号）。適用のない用途地域は 0。"""
    if start_mm is None or gradient is None:
        return 0.0
    return max(0.0, (top_mm - start_mm) / gradient)


def north_setback(top_mm: float, start_mm: float | None,
                  gradient: float | None) -> float:
    """北側斜線で必要な真北方向の距離（法56条1項3号）。適用がなければ 0。

    他の2つと違い、敷地の辺ではなく真北方向に測る距離である点に注意。
    """
    if start_mm is None or gradient is None:
        return 0.0
    return max(0.0, (top_mm - start_mm) / gradient)


def _setbacks(site, top_mm: float, road_gradient: float,
              applicable_distance_mm: float, neighbor_start_mm: float | None,
              neighbor_gradient: float | None) -> list[float]:
    """天端高さ top_mm における辺ごとの後退量。

    道路に接する辺はその辺の幅員で、それ以外の辺は隣地斜線で決まる。
    複数の道路に接していれば、それぞれの幅員で個別に判定される。
    """
    out: list[float] = []
    for edge in site.shape.edges:
        if edge.kind is G.EdgeKind.ROAD:
            setback, _ = road_setback(top_mm, edge.road_width_mm, road_gradient,
                                       applicable_distance_mm)
            out.append(setback)
        else:
            out.append(neighbor_setback(top_mm, neighbor_start_mm, neighbor_gradient))
    return out


def _road_capped(site, top_mm: float, gradient: float,
                 applicable_distance_mm: float) -> bool:
    """最大幅員の前面道路で、道路斜線が適用距離で頭打ちになっているか。"""
    return road_setback(top_mm, site.road_width_mm, gradient,
                         applicable_distance_mm)[1]


def _impacts(site, program, footprint, setbacks: list[float], inset_mm: float,
             actual_area_mm2: float, area_at, top_mm: float,
             north_mm: float = 0.0, without_north: float | None = None
             ) -> tuple[ConstraintImpact, ...]:
    """その階を削っている規定ごとの限界寄与を求める。

    「その規定だけを外したら床面積がどれだけ増えるか」を規定ごとに計算する。
    規定どうしは掛け算で効くので合計は実際の減少量と一致しないが、
    「この制限が外れたら何m2増えるか」という設計上の判断には直接使える。
    """
    kinds = [e.kind for e in site.shape.edges]
    road_sb = max((sb for sb, k in zip(setbacks, kinds)
                   if k is G.EdgeKind.ROAD), default=0.0)
    nb_sb = max((sb for sb, k in zip(setbacks, kinds)
                 if k is G.EdgeKind.NEIGHBOR), default=0.0)

    without_road = [0.0 if k is G.EdgeKind.ROAD else sb
                    for sb, k in zip(setbacks, kinds)]
    without_nb = [sb if k is G.EdgeKind.ROAD else 0.0
                  for sb, k in zip(setbacks, kinds)]

    candidates = (
        (Constraint.ROAD_SLANT, road_sb,
         area_at(inset_mm, top_mm, without_road)),
        (Constraint.NEIGHBOR_SLANT, nb_sb,
         area_at(inset_mm, top_mm, without_nb)),
        (Constraint.BCR, inset_mm, area_at(0.0, top_mm, setbacks)),
        (Constraint.NORTH_SLANT, north_mm,
         actual_area_mm2 if without_north is None else without_north),
    )
    impacts = [
        ConstraintImpact(constraint=c, area_gain_mm2=relaxed - actual_area_mm2,
                         setback_mm=amount)
        for c, amount, relaxed in candidates
        if amount > _LENGTH_EPS_MM and relaxed - actual_area_mm2 > _AREA_EPS_MM2
    ]
    impacts.sort(key=lambda i: i.area_gain_mm2, reverse=True)
    return tuple(impacts)


def _make_floor(
    *,
    floor: int,
    level_mm: float,
    story_mm: float,
    site,
    shape,
    setbacks: list[float],
    impacts: tuple[ConstraintImpact, ...],
    road_capped: bool,
    unconstrained_area_mm2: float,
    north_mm: float = 0.0,
) -> FloorResult:
    """1 階分の結果を組み立てる。

    x/y の範囲は前面道路を基準にした向きへ射影したもの。
    x = 道路に平行な方向、y = 道路から敷地奥へ向かう方向（断面図の切り口）。
    """
    road_angle = site.road_edge.angle_rad
    x_min, x_max = G.projection_range(shape, road_angle + math.pi / 2)
    y_min, y_max = G.projection_range(shape, road_angle)
    road_base, _ = G.projection_range(site.road_edge.line(), road_angle)
    x_base, _ = G.projection_range(site.shape.polygon, road_angle + math.pi / 2)

    kinds = [e.kind for e in site.shape.edges]
    road_sb = max((sb for sb, k in zip(setbacks, kinds)
                   if k is G.EdgeKind.ROAD), default=0.0)
    nb_sb = max((sb for sb, k in zip(setbacks, kinds)
                 if k is G.EdgeKind.NEIGHBOR), default=0.0)

    return FloorResult(
        floor=floor,
        level_mm=level_mm,
        story_height_mm=story_mm,
        x_min_mm=x_min - x_base,
        x_max_mm=x_max - x_base,
        y_min_mm=y_min - road_base,
        y_max_mm=y_max - road_base,
        setback_road_mm=road_sb,
        setback_neighbor_mm=nb_sb,
        governing=_governing(road_sb, nb_sb, impacts, road_capped),
        setback_north_mm=north_mm,
        impacts=impacts,
        road_slant_capped=road_capped,
        unconstrained_area_mm2=unconstrained_area_mm2,
        outline=G.outline(shape),
        area_mm2=G.area_mm2(shape),
    )


def _governing(sb_road: float, sb_neighbor: float,
               impacts: tuple[ConstraintImpact, ...], road_capped: bool) -> str:
    """その階の形状を決めた規定を、表示用の文字列にまとめる。"""
    listed = {i.constraint for i in impacts}
    parts: list[str] = []
    if Constraint.ROAD_SLANT in listed:
        parts.append("道路斜線(適用距離で頭打ち)" if road_capped else "道路斜線")
    if Constraint.NEIGHBOR_SLANT in listed:
        parts.append("隣地斜線")
    if Constraint.NORTH_SLANT in listed:
        parts.append("北側斜線")
    if Constraint.BCR in listed:
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
    if r.north_slant_start_mm is None:
        replaced = (district in C.DISTRICTS_WHERE_SHADOW_RULE_REPLACES_NORTH_SLANT
                    and z.shadow_regulation)
        r.applied_rules.append(
            AppliedRule(
                "北側斜線",
                "適用なし（日影規制の対象区域のため）" if replaced else "適用なし",
                "法56条1項3号",
            )
        )
    else:
        r.applied_rules.append(
            AppliedRule(
                "北側斜線",
                f"立ち上がり {r.north_slant_start_mm / C.M_TO_MM:.0f}m"
                f" / 勾配 {r.north_slant_gradient}（真北方向に測る）",
                "法56条1項3号",
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
    r.notes.append(
        "外壁後退と斜線による後退は、足し合わせず大きいほうを適用"
        "（どちらも境界線からの離れを定めるものであるため）"
    )
    if not r.bcr_relaxations:
        r.notes.append(
            "法53条3項の角地緩和・防火地域内耐火建築物の緩和は未適用"
            "（該当する場合は入力で指定すると反映されます）"
        )
    r.notes.append("法52条9項の特定道路による容積率緩和は未考慮（安全側）")
    if z.shadow_regulation:
        r.notes.append(
            "【要注意】日影規制（法56条の2・別表第四）の指定ありとして北側斜線を"
            "外しているが、日影規制そのものは本ツールでは未検証。"
            "この結果は日影規制で削られる前の形であり、安全側ではない"
        )
    elif r.north_slant_start_mm is None and district in C.NORTH_SLANT and             C.NORTH_SLANT[district] is not None:
        r.notes.append("北側斜線は適用なしとして算定（日影規制の指定は入力で切り替えられます）")
    if district == C.UseDistrict.UNDESIGNATED:
        r.notes.append(
            "用途地域の指定のない区域は法の原則値（道路斜線1.5・容積率係数6/10）で試算。"
            "特定行政庁が別の値を指定している場合は再計算が必要"
        )
    if r.bcr_inset_mm > _LENGTH_EPS_MM:
        r.notes.append(
            f"建蔽率上限に収めるため全階を一律 {r.bcr_inset_mm / C.M_TO_MM:.3f}m 内側に絞り込み"
        )
