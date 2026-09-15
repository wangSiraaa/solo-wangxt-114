"""ORM ↔ core 计算引擎的桥接层，以及核实结论修订批次的应用状态机。

设计要点
--------
- 原始观测记录永不改写：修订版 = 基线数据 + 批次结论构成的覆盖层，
  结论持久化在 RevisionConclusion 中，任何时刻都可确定性重建（重启安全）。
- 批次应用全程单事务：任一结论不合法即整体回滚，
  不会留下半套观测或半套估计；失败原因落库，可修正后重试。
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Dict, List, Optional

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.components import COMPONENTS, RESPONSES
from core.equations import EquationError
from core.estimation import DesignError, PlotDesign
from core.matching import (
    PENDING_VERIFICATION,
    DataError,
    RevisionDirectives,
    TreeRecord,
    match_plot_records,
)
from core.pipeline import run_analysis
from core.units import UnitError, to_canonical_dbh_cm, to_canonical_height_m

from .models import (
    Plot,
    RevisionBatch,
    RevisionConclusion,
    Stratum,
    SurveyVersion,
    TreeObservation,
    VerificationTicket,
)

#: 更正结论允许修改的观测字段（其余字段一律拒绝）
CORRECTABLE_FIELDS = {
    "dbh_value", "dbh_unit", "height_value", "height_unit", "x_m", "y_m", "vital_status",
}

_DOMAIN_ERRORS = (UnitError, DesignError, DataError, EquationError, DjangoValidationError)


class BatchApplyError(Exception):
    """批次应用失败（批次状态已置为 failed，可修正结论后重试）。"""


class ConclusionConflict(Exception):
    """结论之间或结论与工单状态冲突（对应 HTTP 409）。"""


# ---------------------------------------------------------------------------
# 输入构建（含修订覆盖层）
# ---------------------------------------------------------------------------

def _values_to_record(obs, vals):
    """由（可能被结论更正过的）原始值构造 core 记录。

    单位不合法时抛 core.units.UnitError —— 批次应用因此失败并可重试。
    """
    ctx = f"{vals['survey_id']}/{obs.plot.plot_id}/{obs.tree_no}"
    conversions: List[dict] = []
    dbh_cm = None
    if vals["dbh_value"] is not None:
        dbh_cm = to_canonical_dbh_cm(vals["dbh_value"], vals["dbh_unit"], context=ctx)
        if str(vals["dbh_unit"]).strip().lower() != "cm":
            conversions.append({
                "context": ctx, "quantity": "dbh",
                "from_unit": vals["dbh_unit"], "to_unit": "cm",
                "value_in": vals["dbh_value"], "value_out": dbh_cm,
            })
    height_m = None
    if vals["height_value"] is not None:
        height_m = to_canonical_height_m(
            vals["height_value"], vals["height_unit"], context=ctx
        )
        if str(vals["height_unit"]).strip().lower() != "m":
            conversions.append({
                "context": ctx, "quantity": "height",
                "from_unit": vals["height_unit"], "to_unit": "m",
                "value_in": vals["height_value"], "value_out": height_m,
            })
    record = TreeRecord(
        plot_id=obs.plot.plot_id,
        tree_no=obs.tree_no,
        species=obs.species.code,
        dbh_cm=dbh_cm,
        height_m=height_m,
        x_m=float(vals["x_m"]),
        y_m=float(vals["y_m"]),
        status=vals["vital_status"],
        survey_id=vals["survey_id"],
    )
    return record, conversions


def _correction_index(conclusions) -> dict:
    """更正结论 → {(survey, plot, tree_no): corrections}。"""
    index = {}
    for c in conclusions:
        if c.conclusion_type != "correct_observation":
            continue
        av = c.after_value
        index[(av["survey"], av["plot"], av["tree_no"])] = av["corrections"]
    return index


def _directives_from_conclusions(conclusions) -> Dict[str, RevisionDirectives]:
    """工单类结论 → 按样地的匹配指令。"""
    by_plot = defaultdict(lambda: {
        "forced_pairs": [], "forced_dead": [],
        "forced_exclusions_t1": [], "forced_exclusions_t2": [],
        "confirmed_missed_t2": [],
    })
    for c in conclusions:
        if c.conclusion_type == "correct_observation" or c.ticket is None:
            continue
        ticket = c.ticket
        plot_id = ticket.plot.plot_id
        av = c.after_value or {}
        note = av.get("note") or (
            f"核实结论：{c.get_conclusion_type_display()}"
            f"（操作者 {c.operator}，批次#{c.batch_id}）"
        )
        d = by_plot[plot_id]
        if c.conclusion_type == "confirm_renumbered":
            d["forced_pairs"].append((av["tree_no_t1"], av["tree_no_t2"], note))
        elif c.conclusion_type == "confirm_dead":
            d["forced_dead"].append((ticket.tree_no_t1, note))
            t2_no = ticket.tree_no_t2
            if t2_no and av.get("t2_disposition", "exclude") == "exclude":
                d["forced_exclusions_t2"].append((t2_no, note + "；复测记录按结论排除"))
        elif c.conclusion_type == "confirm_missing":
            if ticket.tree_no_t2:
                d["confirmed_missed_t2"].append((ticket.tree_no_t2, note))
        elif c.conclusion_type == "keep_excluded":
            if ticket.tree_no_t1:
                d["forced_exclusions_t1"].append((ticket.tree_no_t1, note))
            if ticket.tree_no_t2:
                d["forced_exclusions_t2"].append((ticket.tree_no_t2, note))
    return {pid: RevisionDirectives(**v) for pid, v in by_plot.items()}


def build_inputs(version: SurveyVersion, batch: Optional[RevisionBatch] = None):
    """构建指定调查版的全部计算输入（修订版自动叠加其批次结论）。"""
    batch = batch or getattr(version, "produced_by_batch", None)
    conclusions = (
        list(batch.conclusions.select_related("ticket__plot", "ticket").all())
        if batch else []
    )
    corrections = _correction_index(conclusions)
    directives = _directives_from_conclusions(conclusions)

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
    rejected: List[dict] = []
    observations = (
        TreeObservation.objects.filter(
            survey__in=[version.survey_t1, version.survey_t2]
        )
        .select_related("survey", "plot", "species")
        .order_by("plot__plot_id", "tree_no")
    )
    for obs in observations:
        key = (obs.survey.survey_id, obs.plot.plot_id, obs.tree_no)
        corr = corrections.get(key)
        if obs.qc_status == "rejected":
            rejected.append({
                "observation": f"{key[0]}/{key[1]}/{key[2]}",
                "reason": obs.qc_note,
                "rehabilitated": corr is not None,
            })
            if corr is None:
                continue  # 无更正结论：仍然排除
        vals = {
            "survey_id": obs.survey.survey_id,
            "dbh_value": obs.dbh_value, "dbh_unit": obs.dbh_unit,
            "height_value": obs.height_value, "height_unit": obs.height_unit,
            "x_m": obs.x_m, "y_m": obs.y_m, "vital_status": obs.vital_status,
        }
        if corr:
            unknown = set(corr) - CORRECTABLE_FIELDS
            if unknown:
                raise DataError(f"更正结论含未登记字段 {sorted(unknown)}")
            vals.update(corr)
        if vals["vital_status"] not in ("alive", "dead"):
            raise DataError(
                f"{key[0]}/{key[1]}/{key[2]}: 死亡状态取值非法 {vals['vital_status']!r}"
            )
        record, conv = _values_to_record(obs, vals)
        conversions.extend(conv)
        k = "t1" if obs.survey.survey_id == version.survey_t1.survey_id else "t2"
        records[k].setdefault(obs.plot.plot_id, []).append(record)

    return {
        "designs": designs,
        "stratum_areas": stratum_areas,
        "records": records,
        "conversions": conversions,
        "directives": directives,
        "rejected": rejected,
    }


# ---------------------------------------------------------------------------
# 估计与复测明细
# ---------------------------------------------------------------------------

def _version_info(version: SurveyVersion) -> dict:
    info = {
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
        "base_version_id": version.base_version_id,
    }
    batch = getattr(version, "produced_by_batch", None)
    if batch is not None:
        info["revision"] = {
            "batch_id": batch.pk,
            "batch_request_id": batch.request_id,
            "batch_status": batch.status,
            "base_version_id": batch.base_version_id,
        }
    return info


def compute_analysis(version: SurveyVersion, batch: Optional[RevisionBatch] = None):
    """运行完整分析流水线，返回 (AnalysisResult, 输入构建信息)。"""
    version.verify_equation_set_intact()
    core_set = version.equation_set.build_core_set()
    inputs = build_inputs(version, batch)
    result = run_analysis(
        records_t1=inputs["records"]["t1"],
        records_t2=inputs["records"]["t2"],
        design=inputs["designs"],
        stratum_areas_ha=inputs["stratum_areas"],
        equation_set=core_set,
        interval_years=version.interval_years,
        dbh_threshold_cm=version.dbh_threshold_cm,
        position_tolerance_m=version.position_tolerance_m,
        directives_by_plot=inputs["directives"],
    )
    return result, inputs


def _assemble_payload(version, result, inputs) -> dict:
    payload = result.to_dict()
    payload["survey_version"] = _version_info(version)
    payload["unit_conversions"] = inputs["conversions"]
    payload["rejected_records"] = inputs["rejected"]
    payload["category_counts"] = {
        pid: pc.counts for pid, pc in result.components_by_plot.items()
    }
    return payload


def compute_estimates_payload(version: SurveyVersion) -> dict:
    """总体估计接口的完整响应体。"""
    result, inputs = compute_analysis(version)
    return _assemble_payload(version, result, inputs)


def remeasurements_payload(version: SurveyVersion, plot_id: str) -> dict:
    """单块样地的复测匹配明细（修订版自动应用其批次结论）。"""
    version.verify_equation_set_intact()
    inputs = build_inputs(version)
    if plot_id not in inputs["designs"]:
        raise DesignError(f"样地 {plot_id} 不在抽样设计中")
    outcomes = match_plot_records(
        inputs["records"]["t1"].get(plot_id, []),
        inputs["records"]["t2"].get(plot_id, []),
        plot_id=plot_id,
        interval_years=version.interval_years,
        position_tolerance_m=version.position_tolerance_m,
        dbh_threshold_cm=version.dbh_threshold_cm,
        directives=inputs["directives"].get(plot_id),
    )
    return {
        "survey_version": _version_info(version),
        "plot_id": plot_id,
        "outcomes": [o.to_dict() for o in outcomes],
    }


def default_version() -> Optional[SurveyVersion]:
    """默认调查版：最新的已确认版，否则最新创建的。"""
    return (
        SurveyVersion.objects.filter(confirmed=True).order_by("-confirmed_at").first()
        or SurveyVersion.objects.order_by("-pk").first()
    )


# ---------------------------------------------------------------------------
# 版本比较
# ---------------------------------------------------------------------------

def compare_versions(base: SurveyVersion, revision: SurveyVersion) -> dict:
    """两版估计结果对比（三类响应量 × 四个分量）。"""
    base_payload = compute_estimates_payload(base)
    rev_payload = compute_estimates_payload(revision)
    diff = {}
    for comp in COMPONENTS:
        diff[comp] = {}
        for resp in RESPONSES:
            b = base_payload["estimates"][comp][resp]["total"]
            r = rev_payload["estimates"][comp][resp]["total"]
            diff[comp][resp] = {
                "base": b,
                "revision": r,
                "abs": r - b,
                "rel": (r - b) / b if b else None,
            }
    batch_info = None
    batch = getattr(revision, "produced_by_batch", None)
    if batch is not None:
        batch_info = {
            "id": batch.pk,
            "request_id": batch.request_id,
            "status": batch.status,
            "created_by": batch.created_by,
            "applied_at": batch.applied_at.isoformat() if batch.applied_at else None,
            "conclusions": [
                {
                    "id": c.pk,
                    "ticket_id": c.ticket_id,
                    "conclusion_type": c.conclusion_type,
                    "operator": c.operator,
                    "request_id": c.request_id,
                    "applied_at": c.applied_at.isoformat() if c.applied_at else None,
                }
                for c in batch.conclusions.all()
            ],
        }
    return {
        "base": _version_info(base),
        "revision": _version_info(revision),
        "diff": diff,
        "batch": batch_info,
    }


# ---------------------------------------------------------------------------
# 修订批次：创建（幂等）与应用（状态机）
# ---------------------------------------------------------------------------

def _fingerprint(payload: dict) -> str:
    conclusions = payload.get("conclusions") or []
    canon = {
        "base_version_id": payload.get("base_version_id"),
        "conclusions": sorted(
            json.dumps(c, sort_keys=True, ensure_ascii=False) for c in conclusions
        ),
    }
    return hashlib.sha256(
        json.dumps(canon, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def validate_after_value(conclusion_type: str, after) -> None:
    """结论内容结构校验（单位合法性留到应用时校验，对应失败-重试流程）。"""
    if not isinstance(after, dict):
        raise DjangoValidationError("after_value 必须为 JSON 对象")
    if conclusion_type == "correct_observation":
        for key in ("survey", "plot", "tree_no", "corrections"):
            if key not in after:
                raise DjangoValidationError(f"更正结论缺少字段 {key}")
        corrections = after["corrections"]
        if not isinstance(corrections, dict) or not corrections:
            raise DjangoValidationError("corrections 必须为非空对象")
        unknown = set(corrections) - CORRECTABLE_FIELDS
        if unknown:
            raise DjangoValidationError(f"更正字段未登记: {sorted(unknown)}")
    elif conclusion_type == "confirm_renumbered":
        if not (after.get("tree_no_t1") and after.get("tree_no_t2")):
            raise DjangoValidationError("同株改号结论需给出 tree_no_t1 与 tree_no_t2")
    elif conclusion_type == "confirm_dead":
        disposition = after.get("t2_disposition", "exclude")
        if disposition not in ("exclude", "leave"):
            raise DjangoValidationError("t2_disposition 仅允许 exclude / leave")


def _observation_snapshot(obs: TreeObservation) -> dict:
    return {
        "survey": obs.survey.survey_id,
        "plot": obs.plot.plot_id,
        "tree_no": obs.tree_no,
        "species": obs.species.code,
        "dbh_value": obs.dbh_value, "dbh_unit": obs.dbh_unit,
        "height_value": obs.height_value, "height_unit": obs.height_unit,
        "x_m": obs.x_m, "y_m": obs.y_m,
        "vital_status": obs.vital_status,
        "qc_status": obs.qc_status, "qc_note": obs.qc_note,
    }


def _ticket_snapshot(ticket: VerificationTicket, base: SurveyVersion) -> dict:
    observations = []
    for survey_id, tree_no in (
        (base.survey_t1.survey_id, ticket.tree_no_t1),
        (base.survey_t2.survey_id, ticket.tree_no_t2),
    ):
        if not tree_no:
            continue
        obs = TreeObservation.objects.filter(
            survey__survey_id=survey_id, plot=ticket.plot, tree_no=tree_no
        ).first()
        if obs is not None:
            observations.append(_observation_snapshot(obs))
    return {
        "ticket": {
            "id": ticket.pk,
            "plot": ticket.plot.plot_id,
            "category": ticket.category,
            "status": ticket.status,
            "detail": ticket.detail,
        },
        "observations": observations,
    }


def _check_ticket(ticket_id) -> VerificationTicket:
    ticket = VerificationTicket.objects.filter(pk=ticket_id).first()
    if ticket is None:
        raise DjangoValidationError(f"工单 #{ticket_id} 不存在")
    if ticket.status == "resolved":
        raise ConclusionConflict(f"工单 #{ticket_id} 已核实，不能再提交新结论")
    return ticket


def _create_conclusion(batch: RevisionBatch, base: SurveyVersion, item: dict):
    ctype = item.get("conclusion_type")
    if ctype not in dict(RevisionConclusion.TYPES):
        raise DjangoValidationError(f"未知结论类型 {ctype!r}")
    after = dict(item.get("after_value") or {})
    validate_after_value(ctype, after)

    ticket = None
    before: dict = {}
    if ctype == "correct_observation":
        obs = TreeObservation.objects.filter(
            survey__survey_id=after["survey"],
            plot__plot_id=after["plot"],
            tree_no=after["tree_no"],
        ).first()
        if obs is None:
            raise DjangoValidationError(
                f"更正目标观测 {after['survey']}/{after['plot']}/{after['tree_no']} 不存在"
            )
        before = {"observation": _observation_snapshot(obs)}
        if item.get("ticket_id"):
            ticket = _check_ticket(item["ticket_id"])
    else:
        if not item.get("ticket_id"):
            raise DjangoValidationError(f"结论类型 {ctype} 必须关联工单")
        ticket = _check_ticket(item["ticket_id"])
        before = _ticket_snapshot(ticket, base)
        if ctype == "confirm_renumbered":
            after.setdefault("tree_no_t1", ticket.tree_no_t1)
            after.setdefault("tree_no_t2", ticket.tree_no_t2)
            validate_after_value(ctype, after)

    if ticket is not None:
        clash = (
            RevisionConclusion.objects.filter(
                ticket=ticket, batch__status__in=("draft", "applying", "applied")
            )
            .select_related("batch")
            .first()
        )
        if clash is not None:
            if clash.batch_id == batch.pk:
                raise ConclusionConflict(
                    f"工单 #{ticket.pk} 在本批次中已有结论，同一工单不得重复"
                )
            raise ConclusionConflict(
                f"工单 #{ticket.pk} 已被批次 #{clash.batch_id} 的结论"
                f"（{clash.get_conclusion_type_display()}，操作者 {clash.operator}）占用，"
                f"结论相互冲突；先前批次与结果保持完整，请先处理该批次"
            )

    return RevisionConclusion.objects.create(
        batch=batch,
        ticket=ticket,
        conclusion_type=ctype,
        operator=(item.get("operator") or "").strip() or batch.created_by,
        request_id=(item.get("request_id") or "").strip()
        or f"{batch.request_id}#{RevisionConclusion.objects.filter(batch=batch).count() + 1}",
        before_value=before,
        after_value=after,
        source_version=base,
    )


def create_batch(payload: dict):
    """创建修订批次（幂等）。

    返回 (batch, created)。同一 request_id 重复提交且内容一致时返回原批次；
    内容不一致时抛 ConclusionConflict。
    """
    request_id = (payload.get("request_id") or "").strip()
    if not request_id:
        raise DjangoValidationError("缺少请求标识 request_id")
    fingerprint = _fingerprint(payload)
    existing = RevisionBatch.objects.filter(request_id=request_id).first()
    if existing is not None:
        if existing.payload_fingerprint != fingerprint:
            raise ConclusionConflict(
                "同一请求标识提交了不同内容，拒绝覆盖既有批次"
            )
        return existing, False

    base = SurveyVersion.objects.filter(pk=payload.get("base_version_id")).first()
    if base is None:
        raise DjangoValidationError("基线调查版不存在")
    if not base.confirmed:
        raise DjangoValidationError("基线必须是已确认调查版（草稿版请先确认）")
    conclusions_payload = payload.get("conclusions") or []
    if not conclusions_payload:
        raise DjangoValidationError("批次至少包含一条结论")
    created_by = (payload.get("created_by") or "").strip() or "未署名"

    try:
        with transaction.atomic():
            batch = RevisionBatch.objects.create(
                request_id=request_id,
                payload_fingerprint=fingerprint,
                base_version=base,
                created_by=created_by,
            )
            for item in conclusions_payload:
                _create_conclusion(batch, base, item)
    except IntegrityError:
        # request_id 唯一约束兜底（并发创建同一请求）
        existing = RevisionBatch.objects.get(request_id=request_id)
        if existing.payload_fingerprint != fingerprint:
            raise ConclusionConflict("同一请求标识提交了不同内容，拒绝覆盖既有批次")
        return existing, False
    return batch, True


def _create_pending_tickets(result) -> int:
    """修订版重新匹配后，为新出现的待核实事项补建工单（已存在的去重）。"""
    created = 0
    plots = {p.plot_id: p for p in Plot.objects.all()}
    for plot_id, outcomes in result.outcomes_by_plot.items():
        for o in outcomes:
            if o.category not in PENDING_VERIFICATION:
                continue
            t1 = o.t1.tree_no if o.t1 else None
            t2 = o.t2.tree_no if o.t2 else None
            exists = VerificationTicket.objects.filter(
                plot=plots[plot_id], category=o.category,
                tree_no_t1=t1, tree_no_t2=t2, status="open",
            ).exists()
            if not exists:
                VerificationTicket.objects.create(
                    plot=plots[plot_id], category=o.category,
                    tree_no_t1=t1, tree_no_t2=t2, detail=o.note,
                )
                created += 1
    return created


def _resolve_tickets(batch: RevisionBatch) -> None:
    now = timezone.now()
    for c in batch.conclusions.select_related("ticket").all():
        if c.ticket_id is None:
            continue
        ticket = c.ticket
        if ticket.status == "resolved" and ticket.resolved_by_batch_id != batch.pk:
            raise ConclusionConflict(
                f"工单 #{ticket.pk} 已被批次 #{ticket.resolved_by_batch_id} 核实，"
                f"本批次结论与之冲突，应用中止；先前结果保持完整"
            )
        ticket.status = "resolved"
        ticket.resolved_by_batch = batch
        ticket.resolution_note = f"{c.get_conclusion_type_display()}（{c.operator}）"
        ticket.save(update_fields=["status", "resolved_by_batch", "resolution_note"])
        c.applied_at = now
        c.save(update_fields=["applied_at"])


def _build_revision(batch: RevisionBatch):
    """基于基线版 + 批次结论构建新草稿版并计算（不保存批次状态）。"""
    base = batch.base_version
    if not base.confirmed:
        raise DjangoValidationError("基线必须是已确认调查版")
    base.verify_equation_set_intact()
    version = batch.result_version
    if version is None:
        version = SurveyVersion.objects.create(
            name=f"{base.name} · 修订批次#{batch.pk}",
            survey_t1=base.survey_t1,
            survey_t2=base.survey_t2,
            equation_set=base.equation_set,
            dbh_threshold_cm=base.dbh_threshold_cm,
            position_tolerance_m=base.position_tolerance_m,
            base_version=base,
        )
        batch.result_version = version
        batch.save(update_fields=["result_version"])  # 同事务内，失败随回滚消失
    result, inputs = compute_analysis(version, batch=batch)
    payload = _assemble_payload(version, result, inputs)
    _create_pending_tickets(result)
    return version, payload


def apply_batch(batch_id: int) -> RevisionBatch:
    """应用批次：draft/failed → applying → applied（或 failed 可重试）。

    - 已应用批次：幂等直接返回；
    - 全程单事务，任何结论不合法整体回滚，不留半套观测或半套估计；
    - 失败原因写入 batch.error，修正结论后可重试（同一批次）。
    """
    RevisionBatch.objects.filter(
        pk=batch_id, status__in=("draft", "failed")
    ).update(status="applying", error="")
    try:
        with transaction.atomic():
            batch = RevisionBatch.objects.select_for_update().get(pk=batch_id)
            if batch.status == "applied":
                return batch  # 幂等：重复应用直接返回原批次
            if batch.status != "applying":
                raise BatchApplyError(f"批次状态 {batch.status} 不可应用")
            result_version, payload = _build_revision(batch)
            _resolve_tickets(batch)
            batch.result_version = result_version
            batch.result_estimates = payload
            batch.status = "applied"
            batch.applied_at = timezone.now()
            batch.save(update_fields=[
                "result_version", "result_estimates", "status", "applied_at",
            ])
    except _DOMAIN_ERRORS + (ConclusionConflict,) as exc:
        RevisionBatch.objects.filter(pk=batch_id).update(
            status="failed", error=str(exc)[:2000]
        )
        raise BatchApplyError(str(exc)) from exc
    return RevisionBatch.objects.get(pk=batch_id)
