"""样地级分量计算的验收测试（含手算对照值）。"""
import math

import pytest

from core.components import (
    COMPONENT_INGROWTH,
    COMPONENT_MORTALITY,
    COMPONENT_NET_CHANGE,
    COMPONENT_SURVIVOR_GROWTH,
    RESPONSE_BASAL_AREA,
)
from core.equations import RESPONSE_BIOMASS_KG
from core.matching import (
    CONFLICT,
    INGROWTH,
    MISSING,
    POSSIBLE_MISSED,
    RENUMBERED,
    SURVIVOR,
    SURVIVOR_ZERO,
    DEAD,
)

BA = RESPONSE_BASAL_AREA


def _outcomes(analysis, plot_id):
    return {o.category: o for o in analysis.outcomes_by_plot[plot_id]}


class TestPlotP01Categories:
    """P01 覆盖全部特殊情形：改号、位置矛盾、零生长、缺测、死亡、进界、疑似漏测。"""

    def test_categories(self, analysis):
        cats = [o.category for o in analysis.outcomes_by_plot["P01"]]
        assert cats.count(SURVIVOR) == 1          # T001
        assert cats.count(SURVIVOR_ZERO) == 1     # T002 真实零生长
        assert cats.count(DEAD) == 1              # T003
        assert cats.count(MISSING) == 1           # T004 缺测
        assert cats.count(RENUMBERED) == 1        # T005 → T105 复测改号
        assert cats.count(CONFLICT) == 1          # T006 位置矛盾
        assert cats.count(INGROWTH) == 1          # T107
        assert cats.count(POSSIBLE_MISSED) == 1   # T108 疑似漏测

    def test_survivor_growth_basal_area_hand_computed(self, analysis):
        """保留木生长（断面积，样地总量）手算对照：

        T001: π(22.5²−20²)/40000；T002: 0（真实零生长）；T005→T105: π(16.5²−14²)/40000
        """
        expected = math.pi * (22.5**2 - 20.0**2) / 40000.0 \
            + 0.0 \
            + math.pi * (16.5**2 - 14.0**2) / 40000.0
        pc = analysis.components_by_plot["P01"]
        assert pc.totals[COMPONENT_SURVIVOR_GROWTH][BA] == pytest.approx(expected, rel=1e-9)

    def test_mortality_uses_t1_size(self, analysis):
        """死亡量按初测大小计：T003 初测 18.0 cm。"""
        expected = math.pi * 18.0**2 / 40000.0
        pc = analysis.components_by_plot["P01"]
        assert pc.totals[COMPONENT_MORTALITY][BA] == pytest.approx(expected, rel=1e-9)

    def test_ingrowth_basal_area(self, analysis):
        expected = math.pi * 5.4**2 / 40000.0
        pc = analysis.components_by_plot["P01"]
        assert pc.totals[COMPONENT_INGROWTH][BA] == pytest.approx(expected, rel=1e-9)

    def test_net_change_identity(self, analysis):
        pc = analysis.components_by_plot["P01"]
        net = pc.totals[COMPONENT_NET_CHANGE][BA]
        assert net == pytest.approx(
            pc.totals[COMPONENT_SURVIVOR_GROWTH][BA]
            + pc.totals[COMPONENT_INGROWTH][BA]
            - pc.totals[COMPONENT_MORTALITY][BA],
            rel=1e-12,
        )

    def test_pending_records_excluded_with_reasons(self, analysis):
        """缺测/矛盾/疑似漏测不进入任何分量，但在 excluded 中逐条留痕。"""
        pc = analysis.components_by_plot["P01"]
        reasons = {(e["category"], e["tree_no_t1"] or e["tree_no_t2"]) for e in pc.excluded}
        assert (MISSING, "T004") in reasons
        assert (CONFLICT, "T006") in reasons
        assert (POSSIBLE_MISSED, "T108") in reasons

    def test_biomass_uses_species_equation(self, analysis):
        """生物量按树种各自的方程计算：T001 蒙古栎 W=0.152·D^2.31。"""
        w1 = 0.152 * 20.0**2.31
        w2 = 0.152 * 22.5**2.31
        w5_1 = 0.152 * 14.0**2.31
        w5_2 = 0.152 * 16.5**2.31
        expected = (w2 - w1) + 0.0 + (w5_2 - w5_1)
        pc = analysis.components_by_plot["P01"]
        assert pc.totals[COMPONENT_SURVIVOR_GROWTH][RESPONSE_BIOMASS_KG] == pytest.approx(
            expected, rel=1e-9
        )


class TestUnequalPlotAreas:
    def test_per_hectare_uses_own_area(self, analysis, fictional):
        """样地面积不等：每公顷换算逐块使用各自面积。"""
        for plot in fictional.plots:
            pc = analysis.components_by_plot[plot["plot_id"]]
            per_ha = pc.per_hectare(COMPONENT_SURVIVOR_GROWTH, BA, plot["area_ha"])
            assert per_ha == pytest.approx(
                pc.totals[COMPONENT_SURVIVOR_GROWTH][BA] / plot["area_ha"], rel=1e-12
            )

    def test_areas_actually_differ(self, fictional):
        areas = {p["area_ha"] for p in fictional.plots}
        assert len(areas) > 1, "验收数据必须包含不等面积样地"


class TestUnitErrorCascade:
    def test_unit_error_breaks_pair_and_flags(self, analysis):
        """P02/T006 初测因单位错误被拒 → 复测记录成为'新进过大个体'，标记疑似漏测。"""
        cats = [o.category for o in analysis.outcomes_by_plot["P02"]]
        assert cats.count(POSSIBLE_MISSED) == 1
        # 该个体不进入进界分量
        pc = analysis.components_by_plot["P02"]
        t2_only_ingrowth = math.pi * 5.1**2 / 40000.0  # 仅 T105
        assert pc.totals[COMPONENT_INGROWTH][BA] == pytest.approx(t2_only_ingrowth, rel=1e-9)
