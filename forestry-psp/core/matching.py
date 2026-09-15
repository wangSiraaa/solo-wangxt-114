"""固定样地两期调查的个体复测匹配。

匹配规则（与数据字典一致，顺序执行）
------------------------------------
1. 编号相同且复测记录为死亡        → dead（死亡，有明确记录）。
2. 编号相同且位置距离 ≤ 容差       → survivor / survivor_zero（真实零生长单独成类）。
3. 编号相同但位置矛盾（距离 > 容差）→ conflict：**不认定同株**，挂起待人工核实。
4. 初测编号在复测中不存在，但附近（≤ 容差）有树种一致的新编号
                                  → renumbered（复测改号），匹配但显式标记。
5. 初测个体在复测中无任何对应      → missing（缺测）。**缺测不是死亡**，
   不计入任何分量，只在来源说明中列出。
6. 仅复测有的存活个体：胸径 ≥ 起测径且不超过本期进界可达上限 → ingrowth；
   超过上限 → possible_missed（疑似初测漏测），挂起待核实。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

# ---- 匹配类别常量 -----------------------------------------------------------
SURVIVOR = "survivor"                # 两期均实测，有生长
SURVIVOR_ZERO = "survivor_zero"      # 两期均实测，真实零生长
DEAD = "dead"                        # 死亡（复测明确记录）
MISSING = "missing"                  # 缺测（复测无记录）
INGROWTH = "ingrowth"                # 进界
RENUMBERED = "renumbered"            # 复测改号（位置吻合，判定同株）
CONFLICT = "conflict"                # 编号相同但位置矛盾 → 待核实
POSSIBLE_MISSED = "possible_missed"  # 疑似初测漏测 → 待核实
SUBTHRESHOLD = "subthreshold"        # 未达起测径，不参与分量
DEAD_WITHOUT_T1 = "dead_without_t1"  # 异常：死亡记录无初测对应 → 待核实

#: 参与"保留木生长"的类别
GROWTH_CATEGORIES = (SURVIVOR, SURVIVOR_ZERO, RENUMBERED)
#: 需要人工核实、核实前不参与任何分量的类别
PENDING_VERIFICATION = (CONFLICT, POSSIBLE_MISSED, DEAD_WITHOUT_T1)

#: 胸径测量分辨率（cm）：两期差值绝对值不超过该值视为真实零生长
ZERO_GROWTH_TOLERANCE_CM = 0.05

CATEGORY_LABELS = {
    SURVIVOR: "存活生长",
    SURVIVOR_ZERO: "真实零生长",
    DEAD: "死亡",
    MISSING: "缺测",
    INGROWTH: "进界",
    RENUMBERED: "复测改号",
    CONFLICT: "位置矛盾待核实",
    POSSIBLE_MISSED: "疑似漏测待核实",
    SUBTHRESHOLD: "未达起测径",
    DEAD_WITHOUT_T1: "死亡记录异常",
}


class DataError(ValueError):
    """输入数据本身不合法（如同期同样地编号重复、存活个体缺胸径）。"""


@dataclass(frozen=True)
class TreeRecord:
    """单株单次调查记录（胸径/树高已换算到基准单位）。"""

    plot_id: str
    tree_no: str
    species: str
    dbh_cm: Optional[float]
    height_m: Optional[float]
    x_m: float
    y_m: float
    status: str = "alive"   # alive | dead
    survey_id: str = ""


@dataclass
class MatchOutcome:
    plot_id: str
    category: str
    t1: Optional[TreeRecord]
    t2: Optional[TreeRecord]
    note: str = ""
    flags: Tuple[str, ...] = ()

    @property
    def needs_verification(self) -> bool:
        return self.category in PENDING_VERIFICATION

    def to_dict(self) -> dict:
        def _r(r):
            if r is None:
                return None
            return {
                "tree_no": r.tree_no, "species": r.species,
                "dbh_cm": r.dbh_cm, "height_m": r.height_m,
                "x_m": r.x_m, "y_m": r.y_m, "status": r.status,
            }
        return {
            "plot_id": self.plot_id,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "t1": _r(self.t1),
            "t2": _r(self.t2),
            "note": self.note,
            "flags": list(self.flags),
            "needs_verification": self.needs_verification,
        }


def _distance(a: TreeRecord, b: TreeRecord) -> float:
    return math.hypot(a.x_m - b.x_m, a.y_m - b.y_m)


def _assert_unique(records, survey_label: str, plot_id: str) -> None:
    seen = set()
    for r in records:
        if r.tree_no in seen:
            raise DataError(
                f"{survey_label}样地 {plot_id} 内编号 {r.tree_no} 重复，"
                f"请先核实编号再匹配"
            )
        seen.add(r.tree_no)


def _require_dbh(record: TreeRecord, survey_label: str) -> None:
    if record.status == "alive" and record.dbh_cm is None:
        raise DataError(
            f"{survey_label}个体 {record.plot_id}/{record.tree_no} 为存活但胸径缺测，"
            f"应显式标记为缺测记录而不是存活"
        )


def match_plot_records(
    t1_records,
    t2_records,
    *,
    plot_id: str,
    interval_years: float,
    position_tolerance_m: float = 1.0,
    dbh_threshold_cm: float = 5.0,
    max_annual_dbh_growth_cm: float = 1.2,
):
    """对单块样地的两期记录做匹配，返回 MatchOutcome 列表。"""
    _assert_unique(t1_records, "初测", plot_id)
    _assert_unique(t2_records, "复测", plot_id)
    for r in t1_records:
        _require_dbh(r, "初测")
    for r in t2_records:
        _require_dbh(r, "复测")

    outcomes = []
    t2_by_no = {r.tree_no: r for r in t2_records}
    consumed_t2 = set()

    # 第一遍：编号相同的记录
    t1_unmatched = []
    for r1 in t1_records:
        r2 = t2_by_no.get(r1.tree_no)
        if r2 is None:
            t1_unmatched.append(r1)
            continue
        consumed_t2.add(r2.tree_no)
        if r2.status == "dead":
            outcomes.append(MatchOutcome(
                plot_id, DEAD, r1, r2, note="复测明确记录为死亡",
            ))
            continue
        d = _distance(r1, r2)
        if d <= position_tolerance_m:
            delta = r2.dbh_cm - r1.dbh_cm
            cat = SURVIVOR_ZERO if abs(delta) <= ZERO_GROWTH_TOLERANCE_CM else SURVIVOR
            outcomes.append(MatchOutcome(plot_id, cat, r1, r2))
        else:
            outcomes.append(MatchOutcome(
                plot_id, CONFLICT, r1, r2,
                note=(
                    f"编号 {r1.tree_no} 相同但位置矛盾：两期距离 {d:.2f} m > 容差 "
                    f"{position_tolerance_m} m。不认定同株，挂起待人工核实；"
                    f"核实前该个体不参与生长、死亡、进界任何分量"
                ),
                flags=("position_contradiction",),
            ))

    # 第二遍：初测编号在复测中不存在 → 尝试按位置识别"复测改号"
    for r1 in t1_unmatched:
        best, best_d = None, None
        for r2 in t2_records:
            if r2.tree_no in consumed_t2 or r2.status != "alive":
                continue
            if r2.species != r1.species:
                continue
            d = _distance(r1, r2)
            if d <= position_tolerance_m and (best is None or d < best_d):
                best, best_d = r2, d
        if best is not None:
            consumed_t2.add(best.tree_no)
            outcomes.append(MatchOutcome(
                plot_id, RENUMBERED, r1, best,
                note=(
                    f"复测改号：初测 {r1.tree_no} → 复测 {best.tree_no}，"
                    f"位置距离 {best_d:.2f} m ≤ 容差且树种一致，判定同株并显式标记"
                ),
                flags=("id_changed",),
            ))
        else:
            outcomes.append(MatchOutcome(
                plot_id, MISSING, r1, None,
                note=(
                    "复测无该编号记录且附近无位置吻合个体：按缺测处理。"
                    "缺测不是死亡，不计入任何分量"
                ),
                flags=("not_remeasured",),
            ))

    # 第三遍：复测中剩余的记录（初测无对应）
    max_plausible_ingrowth_dbh = dbh_threshold_cm + max_annual_dbh_growth_cm * interval_years
    for r2 in t2_records:
        if r2.tree_no in consumed_t2:
            continue
        consumed_t2.add(r2.tree_no)
        if r2.status == "dead":
            outcomes.append(MatchOutcome(
                plot_id, DEAD_WITHOUT_T1, None, r2,
                note="复测死亡记录无对应初测个体，数据异常，待人工核实",
                flags=("orphan_dead",),
            ))
            continue
        if r2.dbh_cm < dbh_threshold_cm:
            outcomes.append(MatchOutcome(
                plot_id, SUBTHRESHOLD, None, r2,
                note=f"胸径 {r2.dbh_cm:.1f} cm 未达起测径 {dbh_threshold_cm} cm，不参与分量",
            ))
        elif r2.dbh_cm <= max_plausible_ingrowth_dbh:
            outcomes.append(MatchOutcome(
                plot_id, INGROWTH, None, r2,
                note=f"新进个体，胸径 ≥ 起测径 {dbh_threshold_cm} cm，判定进界",
            ))
        else:
            outcomes.append(MatchOutcome(
                plot_id, POSSIBLE_MISSED, None, r2,
                note=(
                    f"新进个体胸径 {r2.dbh_cm:.1f} cm 超过本期进界可达上限 "
                    f"{max_plausible_ingrowth_dbh:.1f} cm（起测径 + 最大年生长 × 间隔），"
                    f"疑似初测漏测，挂起待人工核实；核实前不计入进界"
                ),
                flags=("implausible_ingrowth",),
            ))
    return outcomes
