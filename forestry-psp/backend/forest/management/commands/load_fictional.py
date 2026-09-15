"""装载虚构两期调查数据（演示/验收用）。

流程：解析 JSON → core.units 单位校验（错误记录拒收并留痕）→ 写入 ORM →
确认方程集与调查版 → 复测匹配 → 为待核实事项生成 VerificationTicket。
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.dataset import parse_dataset_json, validate_observations
from core.matching import PENDING_VERIFICATION, match_plot_records

from forest.models import (
    AllometricEquation,
    EquationSet,
    Plot,
    Species,
    Stratum,
    Survey,
    SurveyVersion,
    TreeObservation,
    VerificationTicket,
)

DEFAULT_DATA = Path(settings.BASE_DIR).parent / "data" / "fictional_survey.json"


class Command(BaseCommand):
    help = "装载虚构固定样地两期调查数据（幂等：先清空再装载）"

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(DEFAULT_DATA), help="数据集 JSON 路径")

    def handle(self, *args, **options):
        path = options["path"]
        dataset = parse_dataset_json(path)
        validate_observations(dataset)

        self.stdout.write("清空既有演示数据…")
        for model in (
            VerificationTicket, TreeObservation, SurveyVersion, Survey,
            EquationSet, AllometricEquation, Plot, Stratum, Species,
        ):
            model.objects.all().delete()

        strata = {
            s["code"]: Stratum.objects.create(
                code=s["code"], name=s["name"], area_ha=s["area_ha"]
            )
            for s in dataset.strata
        }
        species = {
            s["code"]: Species.objects.create(
                code=s["code"], name_cn=s["name_cn"], name_latin=s["name_latin"]
            )
            for s in dataset.species
        }

        equations = {}
        raw_equations = {e["equation_id"]: e for e in
                         __import__("json").loads(Path(path).read_text(encoding="utf-8"))["equations"]}
        for eq in dataset.equations:
            obj = AllometricEquation.objects.create(
                equation_id=eq.equation_id, response=eq.response, form=eq.form,
                params=dict(eq.params), dbh_unit=eq.dbh_unit, height_unit=eq.height_unit,
                source=eq.source, status=eq.status,
            )
            obj.species.set([species[c] for c in eq.species])
            equations[eq.equation_id] = obj
        for eq_id, raw in raw_equations.items():
            if raw.get("supersedes"):
                equations[eq_id].supersedes = equations[raw["supersedes"]]
                equations[eq_id].save(update_fields=["supersedes"])

        equation_sets = {}
        for spec in dataset.equation_sets:
            es = EquationSet.objects.create(
                set_id=spec["set_id"], description=spec.get("description", "")
            )
            es.equations.set([equations[eid] for eid in spec["equation_ids"]])
            if spec.get("confirmed"):
                es.confirm()
            equation_sets[es.set_id] = es

        surveys = {
            s["survey_id"]: Survey.objects.create(survey_id=s["survey_id"], year=s["year"])
            for s in dataset.surveys
        }
        plots = {}
        for p in dataset.plots:
            kwargs = dict(
                plot_id=p["plot_id"], stratum=strata[p["stratum"]],
                area_ha=p["area_ha"], inclusion_probability=p["inclusion_probability"],
            )
            if getattr(settings, "FORESTRY_GIS_BACKEND", "postgis") == "postgis":
                from django.contrib.gis.geos import GEOSGeometry
                import json as _json
                kwargs["boundary"] = GEOSGeometry(_json.dumps(p["boundary"]), srid=32650)
            else:
                kwargs["boundary"] = p["boundary"]
            plots[p["plot_id"]] = Plot.objects.create(**kwargs)

        # 观测记录：单位校验通过的为 accepted，错误记录原样保存并标记 rejected
        rejected_keys = {
            (r["observation"]["survey"], r["observation"]["plot"], r["observation"]["tree_no"]): r["error"]
            for r in dataset.rejected
        }
        n_accepted = 0
        for obs in dataset.observations:
            key = (obs["survey"], obs["plot"], obs["tree_no"])
            rejected = key in rejected_keys
            TreeObservation.objects.create(
                survey=surveys[obs["survey"]], plot=plots[obs["plot"]],
                tree_no=obs["tree_no"], species=species[obs["species"]],
                dbh_value=obs.get("dbh_value"), dbh_unit=obs.get("dbh_unit", ""),
                height_value=obs.get("height_value"), height_unit=obs.get("height_unit", ""),
                x_m=obs["x_m"], y_m=obs["y_m"], vital_status=obs.get("status", "alive"),
                qc_status="rejected" if rejected else "accepted",
                qc_note=rejected_keys.get(key, ""),
            )
            n_accepted += 0 if rejected else 1

        # 调查版：两期 + 方程集 + 匹配参数，确认后冻结
        meta = dataset.meta
        version = SurveyVersion.objects.create(
            name="2020–2025 固定样地复测分析（虚构演示）",
            survey_t1=surveys["S2020"], survey_t2=surveys["S2025"],
            equation_set=equation_sets["EQSET-2020"],
            dbh_threshold_cm=meta.get("dbh_threshold_cm", 5.0),
            position_tolerance_m=meta.get("position_tolerance_m", 1.0),
        )
        version.confirm()

        # 复测匹配 → 待核实事项
        interval = float(meta.get("interval_years", 5.0))
        n_tickets = 0
        for plot_id in plots:
            outcomes = match_plot_records(
                dataset.records.get("S2020", {}).get(plot_id, []),
                dataset.records.get("S2025", {}).get(plot_id, []),
                plot_id=plot_id,
                interval_years=interval,
                position_tolerance_m=version.position_tolerance_m,
                dbh_threshold_cm=version.dbh_threshold_cm,
            )
            for o in outcomes:
                if o.category in PENDING_VERIFICATION:
                    VerificationTicket.objects.create(
                        plot=plots[plot_id], category=o.category,
                        tree_no_t1=o.t1.tree_no if o.t1 else None,
                        tree_no_t2=o.t2.tree_no if o.t2 else None,
                        detail=o.note,
                    )
                    n_tickets += 1

        self.stdout.write(self.style.SUCCESS(
            f"装载完成：样地 {len(plots)} 块，观测 {n_accepted} 条通过、"
            f"{len(dataset.rejected)} 条因单位错误被拒收，"
            f"单位换算 {len(dataset.conversions)} 条，待核实事项 {n_tickets} 项；"
            f"调查版「{version.name}」已确认（方程集哈希 {version.equation_set_hash[:12]}…）"
        ))
        for r in dataset.rejected:
            self.stdout.write(f"  拒收: {r['error']}")
