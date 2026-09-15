"""虚构调查数据集的解析与单位校验（core 与 Django 装载命令共用）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from .equations import AllometricEquation, EquationSet
from .estimation import PlotDesign
from .matching import TreeRecord
from .units import UnitError, to_canonical_dbh_cm, to_canonical_height_m


@dataclass
class ParsedDataset:
    """解析后的数据集（原始 JSON 的结构化表示）。"""

    meta: dict
    strata: List[dict]
    plots: List[dict]
    species: List[dict]
    equations: List[AllometricEquation]
    equation_sets: List[dict]
    surveys: List[dict]
    observations: List[dict]

    # 单位校验后填充
    records: Dict[str, Dict[str, List[TreeRecord]]] = field(default_factory=dict)
    conversions: List[dict] = field(default_factory=list)
    rejected: List[dict] = field(default_factory=list)

    def design(self) -> Dict[str, PlotDesign]:
        return {
            p["plot_id"]: PlotDesign(
                plot_id=p["plot_id"],
                stratum=p["stratum"],
                area_ha=float(p["area_ha"]),
                inclusion_probability=float(p["inclusion_probability"]),
            )
            for p in self.plots
        }

    def stratum_areas(self) -> Dict[str, float]:
        return {s["code"]: float(s["area_ha"]) for s in self.strata}

    def equation_set(self, set_id: str) -> EquationSet:
        spec = next(s for s in self.equation_sets if s["set_id"] == set_id)
        by_id = {e.equation_id: e for e in self.equations}
        es = EquationSet(
            set_id,
            [by_id[eid] for eid in spec["equation_ids"]],
            description=spec.get("description", ""),
        )
        es.freeze()
        return es


def parse_dataset_json(path) -> ParsedDataset:
    """读取数据集 JSON 并构造方程对象（不做单位换算）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    equations = [
        AllometricEquation(
            equation_id=e["equation_id"],
            response=e["response"],
            form=e["form"],
            params=e["params"],
            species=tuple(e["species"]),
            dbh_unit=e.get("dbh_unit", "cm"),
            height_unit=e.get("height_unit", "m"),
            source=e.get("source", ""),
            status=e.get("status", "draft"),
        )
        for e in raw["equations"]
    ]
    return ParsedDataset(
        meta=raw.get("meta", {}),
        strata=raw["strata"],
        plots=raw["plots"],
        species=raw["species"],
        equations=equations,
        equation_sets=raw["equation_sets"],
        surveys=raw["surveys"],
        observations=raw["observations"],
    )


def validate_observations(dataset: ParsedDataset) -> ParsedDataset:
    """对全部观测记录做显式单位换算与校验。

    结果写入 dataset.records / conversions / rejected：
    - 合法记录换算到基准单位（cm / m），换算行为逐条记录；
    - 单位错误记录进入 rejected（含错误原因），绝不静默修正。
    """
    records: Dict[str, Dict[str, List[TreeRecord]]] = {}
    conversions: List[dict] = []
    rejected: List[dict] = []

    for obs in dataset.observations:
        ctx = f"{obs['survey']}/{obs['plot']}/{obs['tree_no']}"
        try:
            dbh_cm = None
            if obs.get("dbh_value") is not None:
                dbh_cm = to_canonical_dbh_cm(
                    obs["dbh_value"], obs.get("dbh_unit"), context=ctx
                )
                if obs["dbh_unit"].strip().lower() != "cm":
                    conversions.append({
                        "context": ctx, "quantity": "dbh",
                        "from_unit": obs["dbh_unit"], "to_unit": "cm",
                        "value_in": obs["dbh_value"], "value_out": dbh_cm,
                    })
            height_m = None
            if obs.get("height_value") is not None:
                height_m = to_canonical_height_m(
                    obs["height_value"], obs.get("height_unit"), context=ctx
                )
                if obs["height_unit"].strip().lower() != "m":
                    conversions.append({
                        "context": ctx, "quantity": "height",
                        "from_unit": obs["height_unit"], "to_unit": "m",
                        "value_in": obs["height_value"], "value_out": height_m,
                    })
            if obs.get("status", "alive") == "alive" and dbh_cm is None:
                raise UnitError(f"{ctx}: 存活个体胸径缺测，应显式标记")
        except UnitError as exc:
            rejected.append({"observation": obs, "error": str(exc)})
            continue

        rec = TreeRecord(
            plot_id=obs["plot"],
            tree_no=obs["tree_no"],
            species=obs["species"],
            dbh_cm=dbh_cm,
            height_m=height_m,
            x_m=float(obs["x_m"]),
            y_m=float(obs["y_m"]),
            status=obs.get("status", "alive"),
            survey_id=obs["survey"],
        )
        records.setdefault(obs["survey"], {}).setdefault(obs["plot"], []).append(rec)

    dataset.records = records
    dataset.conversions = conversions
    dataset.rejected = rejected
    return dataset
