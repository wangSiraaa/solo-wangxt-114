"""核实结论指令（RevisionDirectives）的核心引擎验收测试。"""
import math

import pytest

from core.matching import (
    CONFIRMED_MISSED,
    DEAD,
    EXCLUDED_BY_VERIFICATION,
    RENUMBERED,
    SURVIVOR,
    DataError,
    RevisionDirectives,
    TreeRecord,
    match_plot_records,
)

P = "PX"


def rec(no, d, x=0.0, y=0.0, sp="QUMO", status="alive"):
    return TreeRecord(
        plot_id=P, tree_no=no, species=sp, dbh_cm=d,
        height_m=15.0 if d else None, x_m=x, y_m=y, status=status,
    )


def match(t1, t2, directives=None):
    return match_plot_records(
        t1, t2, plot_id=P, interval_years=5.0, directives=directives,
    )


class TestForcedPairs:
    def test_conflict_resolved_as_same_tree(self):
        """位置矛盾经核实为同株 → 强制配对，忽略位置，带核实标记。"""
        t1 = [rec("T004", 30.0, 30, 14)]
        t2 = [rec("T004", 32.0, 36, 4)]
        directives = RevisionDirectives(
            forced_pairs=(("T004", "T004", "现场核实：同株，坐标误记"),)
        )
        (o,) = match(t1, t2, directives)
        assert o.category == SURVIVOR
        assert "verification_confirmed" in o.flags
        assert o.t2.dbh_cm == 32.0

    def test_forced_pair_different_numbers(self):
        directives = RevisionDirectives(
            forced_pairs=(("A", "B", "核实为同株改号"),)
        )
        (o,) = match([rec("A", 20.0, 1, 1)], [rec("B", 22.0, 9, 9)], directives)
        assert o.category == RENUMBERED
        assert "verification_confirmed" in o.flags

    def test_forced_pair_missing_record_fails(self):
        """指令引用不存在的记录 → DataError（批次应用失败）。"""
        directives = RevisionDirectives(forced_pairs=(("A", "ZZZ", "x"),))
        with pytest.raises(DataError, match="不存在"):
            match([rec("A", 20.0, 1, 1)], [rec("B", 22.0, 2, 2)], directives)

    def test_directive_conflict_double_use_fails(self):
        """同一记录被两条指令引用 → DataError。"""
        directives = RevisionDirectives(
            forced_pairs=(("A", "B", "x"),),
            forced_exclusions_t2=(("B", "y"),),
        )
        with pytest.raises(DataError, match="重复引用"):
            match([rec("A", 20.0, 1, 1)], [rec("B", 22.0, 2, 2)], directives)


class TestForcedDeadAndExclusions:
    def test_forced_dead_counts_as_mortality(self):
        directives = RevisionDirectives(forced_dead=(("A", "核实确认死亡"),))
        outcomes = match([rec("A", 25.0, 1, 1)], [rec("A", 26.0, 9, 9)], directives)
        dead = [o for o in outcomes if o.category == DEAD]
        assert len(dead) == 1 and dead[0].t1.tree_no == "A"

    def test_keep_excluded(self):
        directives = RevisionDirectives(
            forced_exclusions_t1=(("A", "保留排除"),),
            forced_exclusions_t2=(("B", "保留排除"),),
        )
        outcomes = match([rec("A", 20.0, 1, 1)], [rec("B", 22.0, 2, 2)], directives)
        cats = {o.category for o in outcomes}
        assert cats == {EXCLUDED_BY_VERIFICATION}

    def test_confirmed_missed_stays_excluded(self):
        """疑似漏测经核实确认 → 保持排除并更名，不进入进界。"""
        directives = RevisionDirectives(
            confirmed_missed_t2=(("N1", "核实：初测确属漏测"),)
        )
        (o,) = match([], [rec("N1", 14.8, 5, 5)], directives)
        assert o.category == CONFIRMED_MISSED
        assert "verification_confirmed" in o.flags


class TestFictionalRevision:
    """在虚构数据集上：P05 位置矛盾确认同株改号后的手算对照。"""

    def test_p05_confirm_renumbered_effect(self, fictional):
        from core.pipeline import run_analysis

        equation_set = fictional.equation_set("EQSET-2020")
        base = run_analysis(
            records_t1=fictional.records["S2020"],
            records_t2=fictional.records["S2025"],
            design=fictional.design(),
            stratum_areas_ha=fictional.stratum_areas(),
            equation_set=equation_set,
            interval_years=5.0,
        )
        directives = {
            "P05": RevisionDirectives(
                forced_pairs=(("T004", "T004", "现场核实：同株，复测坐标误记"),)
            )
        }
        revised = run_analysis(
            records_t1=fictional.records["S2020"],
            records_t2=fictional.records["S2025"],
            design=fictional.design(),
            stratum_areas_ha=fictional.stratum_areas(),
            equation_set=equation_set,
            interval_years=5.0,
            directives_by_plot=directives,
        )
        # P05 T004（30.0→32.0 cm）由"排除"变为保留木：生长量增加 π(32²−30²)/40000 × 权重40
        expected_delta = 40.0 * math.pi * (32.0**2 - 30.0**2) / 40000.0
        base_growth = base.estimates["survivor_growth"]["basal_area_m2"]["total"]
        revised_growth = revised.estimates["survivor_growth"]["basal_area_m2"]["total"]
        assert revised_growth - base_growth == pytest.approx(expected_delta, rel=1e-9)
        # 基线结果本身不受指令影响（指令只传给修订计算）
        assert base_growth == pytest.approx(4.2016445, rel=1e-6)
