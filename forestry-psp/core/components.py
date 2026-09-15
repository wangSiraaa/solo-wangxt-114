"""由复测匹配结果计算样地级生长 / 死亡 / 进界分量。

分量定义（调查期间，样地总量）
------------------------------
- survivor_growth 保留木生长：两期均实测个体（含真实零生长、复测改号）的响应量之差合计。
- ingrowth        进界：本期新进且达起测径个体的期末响应量合计。
- mortality       死亡：有明确死亡记录个体的**期初**响应量合计。
- net_change      净变化 = 保留木生长 + 进界 − 死亡。

缺测（missing）、位置矛盾（conflict）、疑似漏测（possible_missed）等
不进入任何分量，全部列入 excluded 并随结果输出。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .equations import (
    EquationError,
    EquationSet,
    RESPONSE_BIOMASS_KG,
    RESPONSE_VOLUME_M3,
)
from .matching import DEAD, GROWTH_CATEGORIES, INGROWTH, MatchOutcome

RESPONSE_BASAL_AREA = "basal_area_m2"
RESPONSES = (RESPONSE_BASAL_AREA, RESPONSE_BIOMASS_KG, RESPONSE_VOLUME_M3)
RESPONSE_LABELS = {
    RESPONSE_BASAL_AREA: "断面积 (m²)",
    RESPONSE_BIOMASS_KG: "地上生物量 (kg)",
    RESPONSE_VOLUME_M3: "蓄积 (m³)",
}

COMPONENT_SURVIVOR_GROWTH = "survivor_growth"
COMPONENT_INGROWTH = "ingrowth"
COMPONENT_MORTALITY = "mortality"
COMPONENT_NET_CHANGE = "net_change"
COMPONENTS = (
    COMPONENT_SURVIVOR_GROWTH,
    COMPONENT_INGROWTH,
    COMPONENT_MORTALITY,
    COMPONENT_NET_CHANGE,
)
COMPONENT_LABELS = {
    COMPONENT_SURVIVOR_GROWTH: "保留木生长",
    COMPONENT_INGROWTH: "进界",
    COMPONENT_MORTALITY: "死亡",
    COMPONENT_NET_CHANGE: "净变化",
}


def basal_area_m2(dbh_cm: float) -> float:
    """胸径(cm) → 单株断面积(m²)。"""
    return math.pi * dbh_cm * dbh_cm / 40000.0


def _response_value(resp: str, record, equation_set: EquationSet):
    """单株响应量；无法计算（如缺树高）时返回 None 并记录原因。"""
    if resp == RESPONSE_BASAL_AREA:
        return basal_area_m2(record.dbh_cm), None
    try:
        eq = equation_set.equation_for(resp, record.species)
        value = eq.evaluate(
            species=record.species, dbh_cm=record.dbh_cm, height_m=record.height_m
        )
        return value, None
    except EquationError as exc:
        return None, str(exc)


@dataclass
class PlotComponents:
    """单块样地的分量结果（调查期间的样地总量）。"""

    plot_id: str
    interval_years: float
    totals: Dict[str, Dict[str, float]]          # component -> response -> 样地总量
    counts: Dict[str, int]                       # 匹配类别 -> 株数
    excluded: List[dict]                         # 被剔除记录及原因
    response_exclusions: List[dict] = field(default_factory=list)  # 分响应量的剔除

    def plot_total(self, component: str, response: str) -> float:
        return self.totals[component][response]

    def per_hectare(self, component: str, response: str, area_ha: float) -> float:
        """按本样地面积换算每公顷值（样地面积可不等，逐块换算）。"""
        return self.totals[component][response] / area_ha

    def annual_per_hectare(self, component: str, response: str, area_ha: float) -> float:
        return self.per_hectare(component, response, area_ha) / self.interval_years


def compute_plot_components(
    outcomes,
    *,
    plot_id: str,
    interval_years: float,
    equation_set: EquationSet,
) -> PlotComponents:
    totals = {
        c: {r: 0.0 for r in RESPONSES}
        for c in (COMPONENT_SURVIVOR_GROWTH, COMPONENT_INGROWTH, COMPONENT_MORTALITY)
    }
    counts: Dict[str, int] = {}
    excluded: List[dict] = []
    response_exclusions: List[dict] = []

    def _accumulate(component: str, record, sign_response: str):
        value, err = _response_value(sign_response, record, equation_set)
        if value is None:
            response_exclusions.append({
                "plot_id": plot_id,
                "tree_no": record.tree_no,
                "response": sign_response,
                "reason": err,
            })
            return 0.0
        return value

    for o in outcomes:
        assert isinstance(o, MatchOutcome)
        counts[o.category] = counts.get(o.category, 0) + 1
        if o.category in GROWTH_CATEGORIES:
            for resp in RESPONSES:
                v1 = _accumulate(COMPONENT_SURVIVOR_GROWTH, o.t1, resp)
                v2 = _accumulate(COMPONENT_SURVIVOR_GROWTH, o.t2, resp)
                totals[COMPONENT_SURVIVOR_GROWTH][resp] += v2 - v1
        elif o.category == DEAD:
            for resp in RESPONSES:
                totals[COMPONENT_MORTALITY][resp] += _accumulate(
                    COMPONENT_MORTALITY, o.t1, resp
                )
        elif o.category == INGROWTH:
            for resp in RESPONSES:
                totals[COMPONENT_INGROWTH][resp] += _accumulate(
                    COMPONENT_INGROWTH, o.t2, resp
                )
        else:
            excluded.append({
                "plot_id": plot_id,
                "category": o.category,
                "tree_no_t1": o.t1.tree_no if o.t1 else None,
                "tree_no_t2": o.t2.tree_no if o.t2 else None,
                "reason": o.note,
            })

    # 净变化（NumPy 向量化合计，避免逐分量手算出错）
    for resp in RESPONSES:
        totals[COMPONENT_NET_CHANGE] = totals.get(COMPONENT_NET_CHANGE, {r: 0.0 for r in RESPONSES})
        totals[COMPONENT_NET_CHANGE][resp] = float(np.sum([
            totals[COMPONENT_SURVIVOR_GROWTH][resp],
            totals[COMPONENT_INGROWTH][resp],
            -totals[COMPONENT_MORTALITY][resp],
        ]))

    return PlotComponents(
        plot_id=plot_id,
        interval_years=interval_years,
        totals=totals,
        counts=counts,
        excluded=excluded,
        response_exclusions=response_exclusions,
    )
