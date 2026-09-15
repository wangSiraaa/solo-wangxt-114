"""按给定抽样设计的总体估计（Horvitz–Thompson 加权 + 分层方差）。

明确不做的事
------------
不把全部树木简单平均后乘总面积。样地面积不等、入样概率不同时，
简单平均 × 面积是有偏的；本模块只接受"逐样地分量值 + 设计权重"，
按 Horvitz–Thompson 估计总量，方差按分层有放回近似。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Mapping, Tuple

import numpy as np


class DesignError(ValueError):
    """抽样设计数据不完整或与观测不一致。"""


@dataclass(frozen=True)
class PlotDesign:
    """单块样地的设计信息（由抽样设计给定，不由样本反推）。"""

    plot_id: str
    stratum: str
    area_ha: float
    inclusion_probability: float   # π_i

    @property
    def weight(self) -> float:
        """Horvitz–Thompson 权重 = 1/π。"""
        return 1.0 / self.inclusion_probability


@dataclass
class EstimateResult:
    total: float
    variance: float
    se: float
    ci95: Tuple[float, float]
    per_ha: float
    se_per_ha: float
    n_plots: int
    forest_area_ha: float
    warnings: Tuple[str, ...] = ()
    plot_contributions: Tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "variance": self.variance,
            "se": self.se,
            "ci95": [self.ci95[0], self.ci95[1]],
            "per_ha": self.per_ha,
            "se_per_ha": self.se_per_ha,
            "n_plots": self.n_plots,
            "forest_area_ha": self.forest_area_ha,
            "warnings": list(self.warnings),
            "plot_contributions": list(self.plot_contributions),
        }


def horvitz_thompson(
    plot_values: Mapping[str, float],
    design: Mapping[str, PlotDesign],
    *,
    stratum_areas_ha: Mapping[str, float],
) -> EstimateResult:
    """对逐样地总量做设计加权总体估计。

    plot_values: {plot_id: 样地总量}；design: {plot_id: PlotDesign}。
    两者必须一一对应，否则抛 DesignError（防止静默丢样地）。
    """
    missing = set(design) - set(plot_values)
    extra = set(plot_values) - set(design)
    if missing or extra:
        raise DesignError(
            f"设计与观测不一致：缺观测 {sorted(missing)}，多观测 {sorted(extra)}。"
            f"不允许静默丢弃样地，请先核实"
        )

    plot_ids = sorted(plot_values)
    weights = np.array([design[p].weight for p in plot_ids], dtype=float)
    values = np.array([plot_values[p] for p in plot_ids], dtype=float)
    strata = np.array([design[p].stratum for p in plot_ids])

    weighted = weights * values
    total = float(np.sum(weighted))

    # 分层方差（层内有放回近似）：Var = Σ_h n_h/(n_h−1) · Σ_{i∈h}(w_i y_i − 均值_h)²
    variance = 0.0
    warnings = []
    for h in sorted(set(strata)):
        vals = weighted[strata == h]
        n = vals.size
        if n < 2:
            warnings.append(
                f"层 {h} 仅 {n} 块样地，无法估计层内方差，"
                f"该层方差贡献按 0 处理（不确定性被低估）"
            )
            continue
        variance += float(n / (n - 1) * np.sum((vals - vals.mean()) ** 2))

    se = math.sqrt(variance)
    forest_area = float(sum(stratum_areas_ha.values()))
    contributions = tuple(
        {
            "plot_id": p,
            "stratum": design[p].stratum,
            "weight": design[p].weight,
            "plot_total": float(v),
            "weighted_total": float(w),
        }
        for p, v, w in zip(plot_ids, values, weighted)
    )
    return EstimateResult(
        total=total,
        variance=variance,
        se=se,
        ci95=(total - 1.96 * se, total + 1.96 * se),
        per_ha=total / forest_area,
        se_per_ha=se / forest_area,
        n_plots=len(plot_ids),
        forest_area_ha=forest_area,
        warnings=tuple(warnings),
        plot_contributions=contributions,
    )
