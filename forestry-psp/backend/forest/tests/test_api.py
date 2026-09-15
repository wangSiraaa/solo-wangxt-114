"""API 层验收测试：虚构数据装载、复测标记、单位错误、版本不可变。"""
import math
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from forest.models import (
    AllometricEquation,
    EquationSet,
    SurveyVersion,
    TreeObservation,
    VerificationTicket,
)

DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "fictional_survey.json"


class FictionalDataTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_fictional", path=str(DATA_PATH), verbosity=0)


class TestEstimatesEndpoint(FictionalDataTestCase):
    def test_components_and_provenance_present(self):
        resp = self.client.get("/api/estimates/")
        assert resp.status_code == 200
        payload = resp.json()
        for component in ("survivor_growth", "ingrowth", "mortality", "net_change"):
            assert component in payload["estimates"]
            prov = payload["provenance"][component]
            assert prov["source"] and prov["assumptions"]
        # 断面积保留木生长总体量与手算一致（权重 50/40）
        expected = (
            50.0 * math.pi * (182.5 + 148.32 + 254.40) / 40000.0
            + 40.0 * math.pi * (189.44 + 416.46) / 40000.0
        )
        got = payload["estimates"]["survivor_growth"]["basal_area_m2"]["total"]
        assert abs(got - expected) < 1e-6

    def test_version_banner_and_hash(self):
        payload = self.client.get("/api/estimates/").json()
        version = payload["survey_version"]
        assert version["confirmed"] is True
        assert version["equation_set_id"] == "EQSET-2020"
        assert len(version["equation_set_hash"]) == 64

    def test_unit_conversions_and_rejections_reported(self):
        payload = self.client.get("/api/estimates/").json()
        conv = [c for c in payload["unit_conversions"] if "P02/T001" in c["context"]]
        assert conv and conv[0]["from_unit"] == "mm"
        rejected = payload["rejected_records"]
        assert any("P02/T006" in r["observation"] for r in rejected)
        assert any("疑似单位标错" in r["reason"] for r in rejected)


class TestRemeasurementsEndpoint(FictionalDataTestCase):
    def test_special_cases_flagged(self):
        payload = self.client.get("/api/plots/P01/remeasurements/").json()
        cats = [o["category"] for o in payload["outcomes"]]
        for expected in (
            "survivor", "survivor_zero", "dead", "missing",
            "renumbered", "conflict", "ingrowth", "possible_missed",
        ):
            assert expected in cats, f"缺少类别 {expected}"
        conflict = next(o for o in payload["outcomes"] if o["category"] == "conflict")
        assert conflict["needs_verification"] is True
        assert "不认定同株" in conflict["note"]


class TestTickets(FictionalDataTestCase):
    def test_tickets_created_for_pending_items(self):
        assert VerificationTicket.objects.filter(category="conflict").count() == 2
        assert VerificationTicket.objects.filter(category="possible_missed").count() == 2
        resp = self.client.get("/api/verification-tickets/")
        assert resp.status_code == 200
        assert len(resp.json()) == 4


class TestUnitErrorHandling(FictionalDataTestCase):
    def test_unit_error_record_rejected_but_kept(self):
        obs = TreeObservation.objects.get(
            survey__survey_id="S2020", plot__plot_id="P02", tree_no="T006"
        )
        assert obs.qc_status == "rejected"
        assert "疑似单位标错" in obs.qc_note
        assert obs.dbh_value == 412.0  # 原始录入值原样保留，未静默改写


class TestImmutability(FictionalDataTestCase):
    def test_confirmed_equation_set_immutable(self):
        es = EquationSet.objects.get(set_id="EQSET-2020")
        es.description = "试图篡改"
        with self.assertRaises(ValidationError):
            es.save()

    def test_confirmed_equation_set_cannot_unconfirm(self):
        es = EquationSet.objects.get(set_id="EQSET-2020")
        es.confirmed = False
        with self.assertRaises(ValidationError):
            es.save()

    def test_equation_in_confirmed_set_immutable(self):
        eq = AllometricEquation.objects.get(equation_id="BIO-QUMO-1")
        eq.params = {"a": 0.149, "b": 2.33}
        with self.assertRaises(ValidationError):
            eq.save()

    def test_confirmed_survey_version_immutable(self):
        version = SurveyVersion.objects.get()
        version.dbh_threshold_cm = 10.0
        with self.assertRaises(ValidationError):
            version.save()

    def test_new_draft_equation_does_not_change_confirmed_version(self):
        """新建草稿方程（修订版）不改变已确认调查版的估计结果。"""
        before = self.client.get("/api/estimates/").json()
        qumo = AllometricEquation.objects.get(equation_id="BIO-QUMO-1").species.first()
        draft = AllometricEquation.objects.create(
            equation_id="BIO-QUMO-9", response="biomass_kg", form="power_dbh",
            params={"a": 0.10, "b": 2.50}, source="虚构-新草稿", status="draft",
        )
        draft.species.set([qumo])
        after = self.client.get("/api/estimates/").json()
        assert before["estimates"] == after["estimates"]
        assert before["survey_version"]["equation_set_hash"] == \
            after["survey_version"]["equation_set_hash"]

    def test_tampered_equation_set_detected_at_compute_time(self):
        """绕过模型层直接改库（模拟恶意/误操作）→ 出数时被哈希校验拦截。"""
        AllometricEquation.objects.filter(equation_id="BIO-QUMO-1").update(
            params={"a": 0.999, "b": 9.99}
        )
        resp = self.client.get("/api/estimates/")
        assert resp.status_code == 409
        assert "不可被新方程静默改变" in str(resp.json()["detail"]) or "不一致" in str(resp.json())
