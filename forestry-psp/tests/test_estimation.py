"""抽样设计加权总体估计的验收测试。"""
import math

import pytest

from core.estimation import DesignError, PlotDesign, horvitz_thompson


def _design():
    return {
        "A1": PlotDesign("A1", "SA", 0.05, 0.02),   # 权重 50
        "A2": PlotDesign("A2", "SA", 0.04, 0.02),
        "B1": PlotDesign("B1", "SB", 0.05, 0.025),  # 权重 40
        "B2": PlotDesign("B2", "SB", 0.08, 0.025),
    }


class TestHorvitzThompson:
    def test_weighted_total_and_variance_hand_computed(self):
        """手算对照：总量 = Σ w·y；方差 = Σ_h n/(n−1)·Σ(w·y − 均值_h)²。"""
        values = {"A1": 2.0, "A2": 4.0, "B1": 3.0, "B2": 5.0}
        est = horvitz_thompson(values, _design(), stratum_areas_ha={"SA": 120.0, "SB": 80.0})
        # 总量 = 50·2 + 50·4 + 40·3 + 40·5 = 620
        assert est.total == pytest.approx(620.0, rel=1e-12)
        # SA: 100,200 均值150 → 2·(50²+50²)=10000；SB: 120,200 均值160 → 2·(40²+40²)=6400
        assert est.variance == pytest.approx(16400.0, rel=1e-12)
        assert est.se == pytest.approx(math.sqrt(16400.0), rel=1e-12)
        assert est.per_ha == pytest.approx(620.0 / 200.0, rel=1e-12)

    def test_not_naive_mean_times_area(self):
        """设计加权结果 ≠ 全部样地简单平均 × 总面积（样地面积/权重不等时）。"""
        values = {"A1": 2.0, "A2": 4.0, "B1": 3.0, "B2": 5.0}
        design = _design()
        est = horvitz_thompson(values, design, stratum_areas_ha={"SA": 120.0, "SB": 80.0})
        naive = sum(values[p] / design[p].area_ha for p in values) / len(values) * 200.0
        assert not math.isclose(est.total, naive, rel_tol=1e-3), (
            "加权估计与简单平均×面积不应相等（本设计下二者应可区分）"
        )

    def test_design_observation_mismatch_rejected(self):
        """设计与观测不一致 → 显式报错，不静默丢样地。"""
        with pytest.raises(DesignError, match="不一致"):
            horvitz_thompson({"A1": 1.0}, _design(), stratum_areas_ha={"SA": 1.0, "SB": 1.0})

    def test_single_plot_stratum_warns(self):
        design = {
            "A1": PlotDesign("A1", "SA", 0.05, 0.02),
            "A2": PlotDesign("A2", "SA", 0.05, 0.02),
            "B1": PlotDesign("B1", "SB", 0.05, 0.025),
        }
        values = {"A1": 1.0, "A2": 2.0, "B1": 3.0}
        est = horvitz_thompson(values, design, stratum_areas_ha={"SA": 100.0, "SB": 50.0})
        assert any("仅 1 块样地" in w for w in est.warnings)


class TestFictionalPopulationEstimates:
    """虚构数据集上的总体估计：手算加权总量对照。"""

    def test_survivor_growth_basal_area_total(self, analysis):
        """保留木生长（断面积）总体量手算：

        各样地 ΔD² 合计（cm²）：P01=182.5，P02=148.32，P03=254.40，P04=189.44，P05=416.46
        样地总量 = π·ΔD²/40000；权重：S1 层 50，S2 层 40。
        """
        pi = math.pi
        expected = (
            50.0 * pi * (182.5 + 148.32 + 254.40) / 40000.0
            + 40.0 * pi * (189.44 + 416.46) / 40000.0
        )
        got = analysis.estimates["survivor_growth"]["basal_area_m2"]["total"]
        assert got == pytest.approx(expected, rel=1e-9)

    def test_mortality_basal_area_total(self, analysis):
        """死亡（断面积）：P01 18.0、P02 19.5、P03 17.5（权重 50），P04 19.0（权重 40）。"""
        pi = math.pi
        expected = (
            50.0 * pi * (18.0**2 + 19.5**2 + 17.5**2) / 40000.0
            + 40.0 * pi * 19.0**2 / 40000.0
        )
        got = analysis.estimates["mortality"]["basal_area_m2"]["total"]
        assert got == pytest.approx(expected, rel=1e-9)

    def test_ingrowth_basal_area_total(self, analysis):
        """进界（断面积）：P01 5.4、P02 5.1、P03 6.8（权重 50），P04 5.6、P05 7.2（权重 40）。"""
        pi = math.pi
        expected = (
            50.0 * pi * (5.4**2 + 5.1**2 + 6.8**2) / 40000.0
            + 40.0 * pi * (5.6**2 + 7.2**2) / 40000.0
        )
        got = analysis.estimates["ingrowth"]["basal_area_m2"]["total"]
        assert got == pytest.approx(expected, rel=1e-9)

    def test_net_change_identity_at_population_level(self, analysis):
        for resp in ("basal_area_m2", "biomass_kg", "volume_m3"):
            e = analysis.estimates
            net = e["net_change"][resp]["total"]
            assert net == pytest.approx(
                e["survivor_growth"][resp]["total"]
                + e["ingrowth"][resp]["total"]
                - e["mortality"][resp]["total"],
                rel=1e-9,
            )

    def test_differs_from_naive_tree_average(self, analysis, fictional):
        """总体估计不得等于'全部树木简单平均 × 总面积'式的朴素结果。"""
        got = analysis.estimates["survivor_growth"]["basal_area_m2"]["total"]
        design = fictional.design()
        pcs = analysis.components_by_plot
        naive = (
            sum(pc.totals["survivor_growth"]["basal_area_m2"] / design[pid].area_ha
                for pid, pc in pcs.items())
            / len(pcs) * 200.0
        )
        assert not math.isclose(got, naive, rel_tol=1e-3)

    def test_provenance_complete(self, analysis):
        """每个分量都有来源说明与不确定性假设。"""
        for component in ("survivor_growth", "ingrowth", "mortality", "net_change"):
            prov = analysis.provenance[component]
            assert prov["source"]
            assert prov["n_plots"] == 5
            assert len(prov["assumptions"]) >= 3
            assert prov["equation_set_id"] == "EQSET-2020"
            assert prov["equation_set_hash"]

    def test_excluded_records_listed_in_provenance(self, analysis):
        excluded = analysis.provenance["survivor_growth"]["excluded_records"]
        cats = {e["category"] for e in excluded}
        assert {"missing", "conflict", "possible_missed"} <= cats
