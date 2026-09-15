"""每个估计分量的来源说明与不确定性假设（随结果一并显式输出）。"""
from __future__ import annotations

from typing import Dict, List

#: 各分量的标准不确定性假设（中文，直接面向报告）
ASSUMPTIONS: Dict[str, List[str]] = {
    "survivor_growth": [
        "缺测（missing）与位置矛盾待核实（conflict）个体不参与生长量计算，未做插补；"
        "若其生长状况与实测个体系统性不同，将产生方向未知的偏差。",
        "复测改号（renumbered）个体依据位置容差与树种一致性判定为同株，存在误配风险，"
        "已全部显式标记并可追溯。",
        "真实零生长（survivor_zero）指两期均实测且胸径差 ≤ 0.05 cm 的个体，"
        "与缺测严格区分，不得混用。",
    ],
    "ingrowth": [
        "进界按起测胸径判定；复测新进个体胸径超过本期进界可达上限者记为疑似漏测"
        "（possible_missed），核实前不计入进界，进界量可能被低估。",
    ],
    "mortality": [
        "仅统计复测中明确记录为死亡的个体；缺测个体若实际死亡，死亡量将被低估。",
        "死亡量按初测时的个体大小计量。",
    ],
    "net_change": [
        "净变化 = 保留木生长 + 进界 − 死亡；各分量的不确定性假设全部被继承。",
    ],
    "measurement": [
        "胸径测量误差按 ±0.1 cm、树高按 ±0.1 m 假设，未在方差中展开；"
        "报告的不确定性仅含抽样误差。",
        "异速生长方程视为给定，其参数不确定性未计入。",
    ],
    "sampling": [
        "按给定分层抽样设计做 Horvitz–Thompson 加权；层内方差按有放回近似，"
        "未做有限总体修正。",
        "禁止用“全部树木简单平均 × 总面积”代替设计加权估计。",
    ],
}


def component_provenance(
    component: str,
    *,
    n_plots: int,
    n_trees: int,
    equation_set_id: str,
    equation_set_hash: str,
    excluded: List[dict],
    extra_assumptions: List[str] | None = None,
) -> dict:
    """组装单个分量的来源与不确定性说明。"""
    assumptions = list(ASSUMPTIONS.get(component, []))
    assumptions += ASSUMPTIONS["measurement"] + ASSUMPTIONS["sampling"]
    if extra_assumptions:
        assumptions += extra_assumptions
    return {
        "component": component,
        "source": (
            f"{n_plots} 块固定样地两期复测记录，{n_trees} 株个体计入本分量；"
            f"方程集 {equation_set_id}（内容哈希 {equation_set_hash[:12]}…）"
        ),
        "n_plots": n_plots,
        "n_trees": n_trees,
        "equation_set_id": equation_set_id,
        "equation_set_hash": equation_set_hash,
        "excluded_records": excluded,
        "assumptions": assumptions,
    }
