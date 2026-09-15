"""复测匹配规则的验收测试：改号、位置矛盾、零生长/缺测/死亡的区分。"""
import pytest

from core.matching import (
    CONFLICT,
    DEAD,
    DEAD_WITHOUT_T1,
    INGROWTH,
    MISSING,
    POSSIBLE_MISSED,
    RENUMBERED,
    SUBTHRESHOLD,
    SURVIVOR,
    SURVIVOR_ZERO,
    DataError,
    TreeRecord,
    match_plot_records,
)

P = "PX"


def rec(no, d, x=0.0, y=0.0, sp="QUMO", status="alive", survey="t"):
    return TreeRecord(
        plot_id=P, tree_no=no, species=sp, dbh_cm=d,
        height_m=15.0 if d else None, x_m=x, y_m=y, status=status, survey_id=survey,
    )


def match(t1, t2, **kw):
    kw.setdefault("plot_id", P)
    kw.setdefault("interval_years", 5.0)
    return match_plot_records(t1, t2, **kw)


class TestSameNumber:
    def test_survivor(self):
        (o,) = match([rec("A", 20.0, 3, 4)], [rec("A", 22.5, 3.1, 4.0)])
        assert o.category == SURVIVOR

    def test_true_zero_growth_is_distinct(self):
        """两期均实测且 ΔD≈0 → 真实零生长，与缺测严格区分。"""
        (o,) = match([rec("A", 15.0, 3, 4)], [rec("A", 15.0, 3.0, 4.1)])
        assert o.category == SURVIVOR_ZERO
        assert o.t1.dbh_cm == o.t2.dbh_cm

    def test_dead(self):
        (o,) = match([rec("A", 18.0, 3, 4)], [rec("A", None, 3, 4, status="dead")])
        assert o.category == DEAD

    def test_same_id_conflicting_position_not_matched(self):
        """编号相同但位置矛盾 → 不认定同株，挂起待核实。"""
        (o,) = match([rec("A", 30.0, 10, 16)], [rec("A", 33.0, 21, 18)])
        assert o.category == CONFLICT
        assert o.needs_verification
        assert "不认定同株" in o.note

    def test_position_at_tolerance_boundary(self):
        (o,) = match([rec("A", 20.0, 0, 0)], [rec("A", 21.0, 0.6, 0.8)])  # 距离恰好 1.0 m
        assert o.category == SURVIVOR


class TestRenumbering:
    def test_renumbered_matched_by_position(self):
        """复测改号：编号不同但位置吻合、树种一致 → 判定同株并标记。"""
        outcomes = match([rec("T005", 14.0, 5, 15)], [rec("T105", 16.5, 5.1, 14.9)])
        (o,) = outcomes
        assert o.category == RENUMBERED
        assert "id_changed" in o.flags
        assert o.t1.tree_no == "T005" and o.t2.tree_no == "T105"

    def test_renumbered_requires_same_species(self):
        """位置吻合但树种不同 → 不匹配（初测缺测 + 复测进界分开处理）。"""
        outcomes = match([rec("A", 14.0, 5, 15, sp="QUMO")], [rec("B", 6.5, 5.1, 14.9, sp="BEPL")])
        cats = {o.category for o in outcomes}
        assert cats == {MISSING, INGROWTH}


class TestMissingDeadIngrowth:
    def test_missing_is_not_mortality(self):
        """复测无记录 → 缺测；缺测不是死亡。"""
        (o,) = match([rec("A", 25.0, 15, 12)], [])
        assert o.category == MISSING
        assert "缺测不是死亡" in o.note

    def test_ingrowth(self):
        (o,) = match([], [rec("N1", 5.4, 18, 6)])
        assert o.category == INGROWTH

    def test_implausible_ingrowth_flagged(self):
        """新进个体过大（> 起测径 + 最大年生长×间隔）→ 疑似漏测，不计进界。"""
        (o,) = match([], [rec("N1", 14.8, 22, 10)])
        assert o.category == POSSIBLE_MISSED
        assert o.needs_verification

    def test_subthreshold_ignored(self):
        (o,) = match([], [rec("N1", 3.2, 1, 1)])
        assert o.category == SUBTHRESHOLD

    def test_orphan_dead_flagged(self):
        (o,) = match([], [rec("N1", None, 1, 1, status="dead")])
        assert o.category == DEAD_WITHOUT_T1
        assert o.needs_verification


class TestDataValidation:
    def test_duplicate_number_rejected(self):
        with pytest.raises(DataError, match="重复"):
            match([rec("A", 10, 1, 1), rec("A", 12, 2, 2)], [])

    def test_alive_without_dbh_rejected(self):
        with pytest.raises(DataError, match="胸径缺测"):
            match([rec("A", None, 1, 1)], [])
