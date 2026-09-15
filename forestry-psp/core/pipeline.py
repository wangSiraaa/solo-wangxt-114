"""分析流水线：匹配 → 样地分量 → 设计加权总体估计 → 来源与不确定性。

Django 视图与离线测试共用本模块，保证 API 输出与验收测试完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from .components import (
    COMPONENTS,
    COMPONENT_LABELS,
    RESPONSES,
    RESPONSE_LABELS,
    PlotComponents,
    compute_plot_components,
)
from .equations import EquationSet
from .estimation import PlotDesign, horvitz_thompson
from .matching import (
    GROWTH_CATEGORIES,
    DEAD,
    INGROWTH,
    RevisionDirectives,
    TreeRecord,
    match_plot_records,
)
from .provenance import component_provenance

#: 各分量计入的匹配类别（用于统计 n_trees）
_COMPONENT_CATEGORIES = {
    "survivor_growth": GROWTH_CATEGORIES,
    "ingrowth": (INGROWTH,),
    "mortality": (DEAD,),
    "net_change": GROWTH_CATEGORIES + (INGROWTH, DEAD),
}


@dataclass
class AnalysisResult:
    """一次两期对比分析的完整结果。"""

    estimates: Dict[str, Dict[str, dict]]          # component -> response -> 估计
    provenance: Dict[str, dict]                    # component -> 来源与假设
    outcomes_by_plot: Dict[str, list]              # plot_id -> [MatchOutcome]
    components_by_plot: Dict[str, PlotComponents]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "responses": [
                {"id": r, "label": RESPONSE_LABELS[r]} for r in RESPONSES
            ],
            "components": [
                {"id": c, "label": COMPONENT_LABELS[c]} for c in COMPONENTS
            ],
            "estimates": self.estimates,
            "provenance": self.provenance,
            "warnings": self.warnings,
        }


def run_analysis(
    *,
    records_t1: Mapping[str, List[TreeRecord]],
    records_t2: Mapping[str, List[TreeRecord]],
    design: Mapping[str, PlotDesign],
    stratum_areas_ha: Mapping[str, float],
    equation_set: EquationSet,
    interval_years: float,
    dbh_threshold_cm: float = 5.0,
    position_tolerance_m: float = 1.0,
    max_annual_dbh_growth_cm: float = 1.2,
    directives_by_plot: Optional[Mapping[str, RevisionDirectives]] = None,
) -> AnalysisResult:
    """两期调查对比分析主入口。

    records_t1 / records_t2: {plot_id: [TreeRecord]}；design: {plot_id: PlotDesign}。
    directives_by_plot: 核实结论指令（修订批次应用时传入），按样地生效。
    """
    equation_set.assert_unmodified()
    directives_by_plot = directives_by_plot or {}

    outcomes_by_plot: Dict[str, list] = {}
    components_by_plot: Dict[str, PlotComponents] = {}
    for plot_id in design:
        outcomes = match_plot_records(
            records_t1.get(plot_id, []),
            records_t2.get(plot_id, []),
            plot_id=plot_id,
            interval_years=interval_years,
            position_tolerance_m=position_tolerance_m,
            dbh_threshold_cm=dbh_threshold_cm,
            max_annual_dbh_growth_cm=max_annual_dbh_growth_cm,
            directives=directives_by_plot.get(plot_id),
        )
        outcomes_by_plot[plot_id] = outcomes
        components_by_plot[plot_id] = compute_plot_components(
            outcomes,
            plot_id=plot_id,
            interval_years=interval_years,
            equation_set=equation_set,
        )

    estimates: Dict[str, Dict[str, dict]] = {}
    provenance: Dict[str, dict] = {}
    warnings: List[str] = []
    all_excluded = [
        e for pc in components_by_plot.values() for e in pc.excluded
    ]

    for component in COMPONENTS:
        estimates[component] = {}
        cats = _COMPONENT_CATEGORIES[component]
        n_trees = sum(
            pc.counts.get(c, 0) for pc in components_by_plot.values() for c in cats
        )
        for resp in RESPONSES:
            plot_values = {
                pid: pc.totals[component][resp]
                for pid, pc in components_by_plot.items()
            }
            est = horvitz_thompson(
                plot_values, design, stratum_areas_ha=stratum_areas_ha
            )
            warnings.extend(est.warnings)
            estimates[component][resp] = {
                **est.to_dict(),
                "annual_per_ha": est.per_ha / interval_years,
                "annual_total": est.total / interval_years,
            }
        provenance[component] = component_provenance(
            component,
            n_plots=len(design),
            n_trees=n_trees,
            equation_set_id=equation_set.set_id,
            equation_set_hash=equation_set.frozen_hash or "",
            excluded=all_excluded if component == "survivor_growth" else [
                e for e in all_excluded if e["category"] in cats
            ],
        )

    return AnalysisResult(
        estimates=estimates,
        provenance=provenance,
        outcomes_by_plot=outcomes_by_plot,
        components_by_plot=components_by_plot,
        warnings=sorted(set(warnings)),
    )
