"""核实结论修订批次的 API 验收测试。

覆盖验收场景：
1. P05 位置矛盾确认同株改号 → 新草稿版，旧已确认版结果不变；
2. 同一批次重复提交 → 幂等返回同一批次，不重复生成版本/结论；
3. 两人对同一工单提交相反结论 → 后者看到明确冲突，先前结果完整；
4. 单位不合法 → 应用失败 → 修正 → 重试完成同一批次，不污染旧版；
5. 重启/刷新后基线、修订版、未处理工单、审计链路可恢复（持久化）；
6. 版本比较接口给出三类响应量差异。
"""
import math
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from forest.models import (
    RevisionBatch,
    RevisionConclusion,
    SurveyVersion,
    VerificationTicket,
)

DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "fictional_survey.json"

BASELINE_SURVIVOR_BA = 4.2016445  # 基线保留木生长断面积总量（手算对照，见 tests/）
P05_T004_DELTA_BA = 40.0 * math.pi * (32.0**2 - 30.0**2) / 40000.0  # ≈0.3895571


class RevisionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_fictional", path=str(DATA_PATH), verbosity=0)

    def setUp(self):
        self.base = SurveyVersion.objects.get(confirmed=True)
        self.p05_conflict = VerificationTicket.objects.get(
            plot__plot_id="P05", category="conflict"
        )

    def _estimates(self, version_id):
        resp = self.client.get(f"/api/estimates/?version_id={version_id}")
        assert resp.status_code == 200, resp.json()
        return resp.json()

    def _survivor_ba(self, version_id):
        return self._estimates(version_id)["estimates"]["survivor_growth"]["basal_area_m2"]["total"]

    def _p05_batch_payload(self, request_id="req-p05-001", conclusion_type="confirm_renumbered",
                           after=None):
        return {
            "request_id": request_id,
            "base_version_id": self.base.pk,
            "created_by": "核实员甲",
            "conclusions": [
                {
                    "ticket_id": self.p05_conflict.pk,
                    "conclusion_type": conclusion_type,
                    "operator": "核实员甲",
                    "request_id": f"{request_id}-c1",
                    "after_value": after or {
                        "tree_no_t1": "T004", "tree_no_t2": "T004",
                        "note": "现场核实：同株，复测坐标误记",
                    },
                }
            ],
        }


class TestConfirmRenumbered(RevisionTestCase):
    def test_p05_conflict_creates_draft_and_baseline_unchanged(self):
        before = self._survivor_ba(self.base.pk)
        assert before == self._survivor_ba(self.base.pk)  # 可重复

        resp = self.client.post(
            "/api/revision-batches/", self._p05_batch_payload(),
            content_type="application/json",
        )
        assert resp.status_code == 201, resp.json()
        batch_id = resp.json()["id"]
        assert resp.json()["status"] == "draft"

        resp = self.client.post(f"/api/revision-batches/{batch_id}/apply/")
        assert resp.status_code == 200, resp.json()
        batch = resp.json()
        assert batch["status"] == "applied"
        revision_id = batch["result_version"]
        assert revision_id is not None

        # 新草稿版：未确认、指向基线
        revision = SurveyVersion.objects.get(pk=revision_id)
        assert revision.confirmed is False
        assert revision.base_version_id == self.base.pk

        # 旧已确认版结果不变
        assert self._survivor_ba(self.base.pk) == before
        self.base.refresh_from_db()
        assert self.base.confirmed is True

        # 修订版生长量增加（P05 T004 30.0→32.0 计入保留木）
        revised = self._survivor_ba(revision_id)
        assert abs((revised - before) - P05_T004_DELTA_BA) < 1e-9

        # 工单已核实并关联批次
        self.p05_conflict.refresh_from_db()
        assert self.p05_conflict.status == "resolved"
        assert self.p05_conflict.resolved_by_batch_id == batch_id

        # 修订版复测明细中该对记录带核实标记
        detail = self.client.get(
            f"/api/plots/P05/remeasurements/?version_id={revision_id}"
        ).json()
        forced = [o for o in detail["outcomes"] if "verification_confirmed" in o["flags"]]
        assert len(forced) == 1
        assert forced[0]["t1"]["tree_no"] == "T004"


class TestIdempotency(RevisionTestCase):
    def test_same_request_twice_returns_same_batch(self):
        payload = self._p05_batch_payload()
        r1 = self.client.post("/api/revision-batches/", payload, content_type="application/json")
        r2 = self.client.post("/api/revision-batches/", payload, content_type="application/json")
        assert r1.status_code == 201
        assert r2.status_code == 200  # 幂等返回
        assert r1.json()["id"] == r2.json()["id"]
        assert RevisionBatch.objects.count() == 1
        assert RevisionConclusion.objects.count() == 1

    def test_same_request_id_different_content_rejected(self):
        self.client.post(
            "/api/revision-batches/", self._p05_batch_payload(),
            content_type="application/json",
        )
        other = self._p05_batch_payload(conclusion_type="confirm_dead")
        resp = self.client.post("/api/revision-batches/", other, content_type="application/json")
        assert resp.status_code == 409
        assert RevisionBatch.objects.count() == 1

    def test_apply_twice_is_idempotent(self):
        resp = self.client.post(
            "/api/revision-batches/", self._p05_batch_payload(),
            content_type="application/json",
        )
        batch_id = resp.json()["id"]
        self.client.post(f"/api/revision-batches/{batch_id}/apply/")
        resp = self.client.post(f"/api/revision-batches/{batch_id}/apply/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"
        # 不重复生成版本、不重复写入结论
        assert SurveyVersion.objects.count() == 2
        assert RevisionConclusion.objects.count() == 1
        batch = RevisionBatch.objects.get(pk=batch_id)
        assert batch.result_version_id is not None


class TestConflictingConclusions(RevisionTestCase):
    def test_opposite_conclusions_on_same_ticket(self):
        # 核实员甲：同株改号
        r1 = self.client.post(
            "/api/revision-batches/", self._p05_batch_payload("req-a"),
            content_type="application/json",
        )
        assert r1.status_code == 201
        # 核实员乙：确认死亡（同一工单，相反结论）
        payload_b = self._p05_batch_payload("req-b", conclusion_type="confirm_dead",
                                            after={"note": "现场核实：已死亡"})
        payload_b["conclusions"][0]["operator"] = "核实员乙"
        r2 = self.client.post("/api/revision-batches/", payload_b, content_type="application/json")
        assert r2.status_code == 409
        assert "冲突" in r2.json()["detail"]
        # 先前结果保持完整
        assert RevisionBatch.objects.count() == 1
        batch_a = RevisionBatch.objects.get()
        assert batch_a.status == "draft"
        assert batch_a.conclusions.count() == 1
        assert batch_a.conclusions.first().conclusion_type == "confirm_renumbered"
        # 工单仍未被消费
        self.p05_conflict.refresh_from_db()
        assert self.p05_conflict.status == "open"


class TestFailureAndRetry(RevisionTestCase):
    def _unit_correction_payload(self, dbh_value, request_id="req-unit-001"):
        ticket = VerificationTicket.objects.get(
            plot__plot_id="P02", category="possible_missed"
        )
        return {
            "request_id": request_id,
            "base_version_id": self.base.pk,
            "created_by": "核实员丙",
            "conclusions": [
                {
                    "ticket_id": ticket.pk,
                    "conclusion_type": "correct_observation",
                    "operator": "核实员丙",
                    "request_id": f"{request_id}-c1",
                    "after_value": {
                        "survey": "S2020", "plot": "P02", "tree_no": "T006",
                        "corrections": {"dbh_value": dbh_value, "dbh_unit": "cm"},
                        "note": "现场核实：初测胸径单位误标",
                    },
                }
            ],
        }

    def test_illegal_unit_fails_then_fix_and_retry(self):
        # 批次中含单位不合法记录（412 cm 仍然超界）→ 应用失败
        resp = self.client.post(
            "/api/revision-batches/", self._unit_correction_payload(412.0),
            content_type="application/json",
        )
        assert resp.status_code == 201
        batch_id = resp.json()["id"]
        resp = self.client.post(f"/api/revision-batches/{batch_id}/apply/")
        assert resp.status_code == 422
        assert "疑似单位标错" in resp.json()["detail"]

        batch = RevisionBatch.objects.get(pk=batch_id)
        assert batch.status == "failed"
        assert batch.result_version is None  # 未留下半套版本
        assert SurveyVersion.objects.count() == 1  # 只有基线版
        # 工单未被消费（事务回滚）
        ticket = VerificationTicket.objects.get(plot__plot_id="P02", category="possible_missed")
        assert ticket.status == "open"
        # 基线结果未被污染
        assert abs(self._survivor_ba(self.base.pk) - BASELINE_SURVIVOR_BA) < 1e-4

        # 修正该条结论（412 cm → 41.2 cm）并重试
        conclusion = batch.conclusions.get()
        after = conclusion.after_value
        after["corrections"]["dbh_value"] = 41.2
        resp = self.client.patch(
            f"/api/revision-conclusions/{conclusion.pk}/",
            {"after_value": after}, content_type="application/json",
        )
        assert resp.status_code == 200

        resp = self.client.post(f"/api/revision-batches/{batch_id}/retry/")
        assert resp.status_code == 200, resp.json()
        batch = RevisionBatch.objects.get(pk=batch_id)
        assert batch.status == "applied"
        assert batch.error == ""
        # 同一批次完成，未新建批次
        assert RevisionBatch.objects.count() == 1
        assert batch.result_version_id is not None

        # 修订版中 P02/T006（41.2→23.5）成为保留木
        baseline_actual = self._survivor_ba(self.base.pk)
        revised = self._survivor_ba(batch.result_version_id)
        expected_delta = 50.0 * math.pi * (23.5**2 - 41.2**2) / 40000.0
        assert abs((revised - baseline_actual) - expected_delta) < 1e-9
        # 旧版依然不变
        assert abs(self._survivor_ba(self.base.pk) - BASELINE_SURVIVOR_BA) < 1e-4

    def test_atomicity_no_partial_state_on_failure(self):
        """批次含一条合法结论 + 一条非法更正 → 失败时合法结论也不留痕。"""
        ticket = VerificationTicket.objects.get(plot__plot_id="P02", category="possible_missed")
        payload = {
            "request_id": "req-atomic-001",
            "base_version_id": self.base.pk,
            "created_by": "核实员丁",
            "conclusions": [
                {
                    "ticket_id": self.p05_conflict.pk,
                    "conclusion_type": "confirm_renumbered",
                    "operator": "核实员丁",
                    "request_id": "req-atomic-001-c1",
                    "after_value": {"tree_no_t1": "T004", "tree_no_t2": "T004"},
                },
                {
                    "ticket_id": ticket.pk,
                    "conclusion_type": "correct_observation",
                    "operator": "核实员丁",
                    "request_id": "req-atomic-001-c2",
                    "after_value": {
                        "survey": "S2020", "plot": "P02", "tree_no": "T006",
                        "corrections": {"dbh_value": 412.0, "dbh_unit": "cm"},
                    },
                },
            ],
        }
        resp = self.client.post("/api/revision-batches/", payload, content_type="application/json")
        batch_id = resp.json()["id"]
        resp = self.client.post(f"/api/revision-batches/{batch_id}/apply/")
        assert resp.status_code == 422
        # 合法结论也未被应用：工单仍 open、无版本、无 applied_at
        self.p05_conflict.refresh_from_db()
        assert self.p05_conflict.status == "open"
        assert SurveyVersion.objects.count() == 1
        assert RevisionConclusion.objects.filter(applied_at__isnull=False).count() == 0


class TestPersistenceAndAudit(RevisionTestCase):
    def test_state_recovers_after_requery(self):
        """模拟重启后恢复：全部状态从数据库重新读取仍完整。"""
        resp = self.client.post(
            "/api/revision-batches/", self._p05_batch_payload(),
            content_type="application/json",
        )
        batch_id = resp.json()["id"]
        self.client.post(f"/api/revision-batches/{batch_id}/apply/")

        # 全新查询（相当于服务重启后的首次读取）
        baseline = SurveyVersion.objects.get(confirmed=True, base_version__isnull=True)
        revision = SurveyVersion.objects.get(base_version=baseline)
        assert revision.confirmed is False
        batch = RevisionBatch.objects.get(result_version=revision)
        assert batch.status == "applied"
        assert batch.result_estimates["survey_version"]["id"] == revision.pk

        # 审计链路：每条结论的操作者、时间、前后值、来源版本、请求标识
        c = batch.conclusions.get()
        assert c.operator == "核实员甲"
        assert c.request_id == "req-p05-001-c1"
        assert c.source_version_id == baseline.pk
        assert c.applied_at is not None
        assert c.before_value["ticket"]["id"] == self.p05_conflict.pk
        assert c.after_value["tree_no_t1"] == "T004"

        # 未处理工单仍待核实
        open_tickets = VerificationTicket.objects.filter(status="open")
        assert open_tickets.count() == 3  # P01 矛盾、P01 疑似漏测、P02 疑似漏测
        # 基线估计仍可复算且不变
        assert abs(self._survivor_ba(baseline.pk) - BASELINE_SURVIVOR_BA) < 1e-4

    def test_cannot_patch_conclusion_after_applied(self):
        resp = self.client.post(
            "/api/revision-batches/", self._p05_batch_payload(),
            content_type="application/json",
        )
        batch_id = resp.json()["id"]
        self.client.post(f"/api/revision-batches/{batch_id}/apply/")
        conclusion = RevisionConclusion.objects.get()
        resp = self.client.patch(
            f"/api/revision-conclusions/{conclusion.pk}/",
            {"after_value": {"tree_no_t1": "X", "tree_no_t2": "Y"}},
            content_type="application/json",
        )
        assert resp.status_code == 409


class TestCompare(RevisionTestCase):
    def test_compare_endpoint_three_responses(self):
        resp = self.client.post(
            "/api/revision-batches/", self._p05_batch_payload(),
            content_type="application/json",
        )
        batch_id = resp.json()["id"]
        revision_id = self.client.post(
            f"/api/revision-batches/{batch_id}/apply/"
        ).json()["result_version"]

        resp = self.client.get(
            f"/api/survey-versions/compare/?base={self.base.pk}&revision={revision_id}"
        )
        assert resp.status_code == 200
        diff = resp.json()["diff"]
        for component in ("survivor_growth", "ingrowth", "mortality", "net_change"):
            for response in ("basal_area_m2", "biomass_kg", "volume_m3"):
                assert response in diff[component]
        d = diff["survivor_growth"]["basal_area_m2"]
        assert abs(d["abs"] - P05_T004_DELTA_BA) < 1e-9
        assert abs(d["rel"] - P05_T004_DELTA_BA / d["base"]) < 1e-9
        # 批次审计信息随比较返回
        assert resp.json()["batch"]["conclusions"][0]["operator"] == "核实员甲"


class TestBaseVersionRules(RevisionTestCase):
    def test_base_must_be_confirmed(self):
        # 先制造一个草稿修订版
        resp = self.client.post(
            "/api/revision-batches/", self._p05_batch_payload(),
            content_type="application/json",
        )
        batch_id = resp.json()["id"]
        draft_id = self.client.post(
            f"/api/revision-batches/{batch_id}/apply/"
        ).json()["result_version"]
        # 以草稿版为基线 → 拒绝
        payload = self._p05_batch_payload("req-ondraft")
        payload["base_version_id"] = draft_id
        payload["conclusions"][0]["ticket_id"] = VerificationTicket.objects.get(
            plot__plot_id="P01", category="conflict"
        ).pk
        resp = self.client.post("/api/revision-batches/", payload, content_type="application/json")
        assert resp.status_code == 400
        assert "已确认" in str(resp.json()["detail"])
