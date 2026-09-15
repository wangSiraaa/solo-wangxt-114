"""ORM ↔ core 计算引擎的桥接层：所有计算都委托给 core，保证与验收测试一致。"""
from __future__ import annotations

from typing import Dict, List, Tuple

from core.estimation import PlotDesign
from core.matching import TreeRecord, match_plot_records
from core.pipeline import run_analysis
from core.units import to_canonical_dbh_cm, to_canonical_height_m

from .models import Plot, Stratum, SurveyVersion, TreeObservation


def _obs_to_record(obs: TreeObservation) -> Tuple[TreeRecord, List[dict]]:
    """ORM 观测 → core 记录；返回 (记录, 本单位换算明细)。"""
    ctx = f"{obs.survey.survey_id}/{obs.plot.plot_id}/{obs.tree_no}"
    conversions: List[dict] = []
    dbh_cm = None
    if obs.dbh_value is not None:
        dbh_cm = to_canonical_dbh_cm(obs.dbh_value, obs.dbh_unit, context=ctx)
        if obs.dbh_unit.strip().lower() != "cm":
            conversions.append({
                "context": ctx, "quantity": "dbh",
                "from_unit": obs.dbh_unit, "to_unit": "cm",
                "value_in": obs.dbh_value, "value_out": dbh_cm,
            })
    height_m = None
    if obs.height_value is not None:
        height_m = to_canonical_height_m(obs.height_value, obs.height_unit, context=ctx)
        if obs.height_unit.strip().lower() != "m":
            conversions.append({
                "context": ctx, "quantity": "height",
                "from_unit": obs.height_unit, "to_unit": "m",
                "value_in": obs.height_value, "value_out": height_m,
            })
    record = TreeRecord(
        plot_id=obs.plot.plot_id,
        tree_no=obs.tree_no,
        species=obs.species.code,
        dbh_cm=dbh_cm,
        height_m=height_m,
        x_m=obs.x_m,
        y_m=obs.y_m,
        status=obs.vital_status,
        survey_id=obs.survey.survey_id,
    )
    return record, conversions


def build_inputs(version: SurveyVersion):
    """从数据库取出该调查版所需的全部输入。"""
    designs: Dict[str, PlotDesign] = {
        p.plot_id: PlotDesign(
            plot_id=p.plot_id,
            stratum=p.stratum.code,
            area_ha=p.area_ha,
            inclusion_probability=p.inclusion_probability,
        )
        for p in Plot.objects.select_related("stratum")
    }
    stratum_areas = {s.code: s.area_ha for s in Stratum.objects.all()}

    records: Dict[str, Dict[str, List[TreeRecord]]] = {"t1": {}, "t2": {}}
    conversions: List[dict] = []
    observations = (
        TreeObservation.objects.filter(
            qc_status="accepted", survey__in=[version.survey_t1, version.survey_t2]
        )
        .select_related("survey", "plot", "species")
        .order_by("plot__plot_id", "tree_no")
    )
    for obs in observations:
        key = "t1" if obs.survey.survey_id == version.survey_t1.survey_id else "t2"
        record, conv = _obs_to_record(obs)
        conversions.extend(conv)
        records[key].setdefault(obs.plot.plot_id, []).append(record)
    return designs, stratum_areas, records, conversions


def _version_info(version: SurveyVersion) -> dict:
    return {
        "id": version.pk,
        "name": version.name,
        "survey_t1": version.survey_t1.survey_id,
        "survey_t2": version.survey_t2.survey_id,
        "interval_years": version.interval_years,
        "dbh_threshold_cm": version.dbh_threshold_cm,
        "position_tolerance_m": version.position_tolerance_m,
        "confirmed": version.confirmed,
        "equation_set_id": version.equation_set.set_id,
        "equation_set_hash": version.equation_set_hash,
    }


def compute_estimates_payload(version: SurveyVersion) -> dict:
    """总体估计接口的完整响应体。"""
    version.verify_equation_set_intact()
    core_set = version.equation_set.build_core_set()
    designs, stratum_areas, records, conversions = build_inputs(version)
    result = run_analysis(
        records_t1=records["t1"],
        records_t2=records["t2"],
        design=designs,
        stratum_areas_ha=stratum_areas,
        equation_set=core_set,
        interval_years=version.interval_years,
        dbh_threshold_cm=version.dbh_threshold_cm,
        position_tolerance_m=version.position_tolerance_m,
    )
    payload = result.to_dict()
    payload["survey_version"] = _version_info(version)
    payload["unit_conversions"] = conversions
    payload["rejected_records"] = [
        {
            "observation": f"{o.survey.survey_id}/{o.plot.plot_id}/{o.tree_no}",
            "reason": o.qc_note,
        }
        for o in TreeObservation.objects.filter(qc_status="rejected")
        .select_related("survey", "plot")
    ]
    payload["category_counts"] = {
        pid: pc.counts for pid, pc in result.components_by_plot.items()
    }
    return payload


def remeasurements_payload(version: SurveyVersion, plot_id: str) -> dict:
    """单块样地的复测匹配明细。"""
    version.verify_equation_set_intact()
    designs, _, records, _ = build_inputs(version)
    if plot_id not in designs:
        from core.estimation import DesignError

        raise DesignError(f"样地 {plot_id} 不在抽样设计中")
    outcomes = match_plot_records(
        records["t1"].get(plot_id, []),
        records["t2"].get(plot_id, []),
        plot_id=plot_id,
        interval_years=version.interval_years,
        position_tolerance_m=version.position_tolerance_m,
        dbh_threshold_cm=version.dbh_threshold_cm,
    )
    return {
        "survey_version": _version_info(version),
        "plot_id": plot_id,
        "outcomes": [o.to_dict() for o in outcomes],
    }


def default_version() -> SurveyVersion:
    """默认调查版：最新的已确认版，否则最新创建的。"""
    return (
        SurveyVersion.objects.filter(confirmed=True).order_by("-confirmed_at").first()
        or SurveyVersion.objects.order_by("-pk").first()
    )
