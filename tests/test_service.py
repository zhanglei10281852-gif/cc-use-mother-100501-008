"""基础设施运营责任交接服务规则测试。"""

import json
import unittest
from datetime import datetime, timedelta, timezone

from asset_handover.domain import (
    AssetVersion,
    DefectStatus,
    DomainError,
    PackageState,
    RemedyStatus,
    RepairStatus,
    Role,
    StateError,
)
from asset_handover.serialization import service_from_dict, service_to_dict
from asset_handover.service import HandoverService

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, moment=T0):
        self.moment = moment

    def __call__(self):
        return self.moment

    def advance(self, **kwargs):
        self.moment += timedelta(**kwargs)


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.service = HandoverService(clock=self.clock)

    def create_package(self, defects=(), **kwargs):
        asset = AssetVersion(asset_code=kwargs.pop("asset_code", "pump-001"), version="v1.0", installed_at=self.clock())
        return self.service.create_package(
            "owner",
            Role.OWNER,
            asset=asset,
            contract_id=kwargs.pop("contract_id", "contract-001"),
            defects=list(defects),
            **kwargs,
        )

    def reach_site_acceptance(self, defects=({"description": "阀门渗漏", "severity": "一般"},)):
        package = self.create_package(defects=defects)
        self.service.confirm_document_review("inspector", Role.INSPECTOR, package.package_id)
        return self.service.get_package(package.package_id)

    def reach_conditional(self, on_expiry="ESCALATE", days=2, **spec_extra):
        package = self.reach_site_acceptance()
        defect = package.defects[0]
        spec = {"defect_id": defect.defect_id, "deadline": self.clock() + timedelta(days=days), "on_expiry": on_expiry}
        if on_expiry == "ESCALATE":
            spec.setdefault("escalation_extension_days", 3)
        if on_expiry == "DEDUCT":
            spec.setdefault("deduction_amount", 5000.0)
        spec.update(spec_extra)
        self.service.accept_with_conditions("owner", Role.OWNER, package.package_id, [spec])
        return self.service.get_package(package.package_id)


class LifecycleTests(ServiceTestCase):
    def test_full_flow_with_conditional_acceptance(self):
        package = self.reach_conditional()
        self.assertEqual(package.state, PackageState.CONDITIONAL_ACCEPTANCE)
        self.assertEqual(len(package.remedies), 1)

        remedy = package.remedies[0]
        self.service.confirm_remedy_completed("inspector", Role.INSPECTOR, package.package_id, remedy.remedy_id, "已复验")
        package = self.service.get_package(package.package_id)
        self.assertEqual(package.remedies[0].status, RemedyStatus.COMPLETED)
        self.assertEqual(package.defects[0].status, DefectStatus.RESOLVED)

        package = self.service.confirm_takeover("owner", Role.OWNER, package.package_id)
        self.assertEqual(package.state, PackageState.FORMAL_TAKEOVER)

        responsibility = self.service.asset_responsibility("pump-001")
        self.assertEqual(responsibility["current"].party, "运营企业")
        self.assertEqual([link.party for link in responsibility["history"]], ["建设单位", "运营企业"])

    def test_direct_takeover_without_defects(self):
        package = self.create_package()
        self.service.confirm_document_review("owner", Role.OWNER, package.package_id)
        package = self.service.confirm_takeover("owner", Role.OWNER, package.package_id)
        self.assertEqual(package.state, PackageState.FORMAL_TAKEOVER)

    def test_cannot_skip_stages(self):
        package = self.create_package()
        with self.assertRaises(StateError):
            self.service.confirm_takeover("owner", Role.OWNER, package.package_id)
        with self.assertRaises(StateError):
            self.service.accept_with_conditions("owner", Role.OWNER, package.package_id, [])

    def test_takeover_rejected_with_open_defect(self):
        # 不能把资产悄悄视为已无缺陷
        package = self.reach_site_acceptance()
        with self.assertRaises(DomainError):
            self.service.confirm_takeover("owner", Role.OWNER, package.package_id)

    def test_takeover_rejected_with_open_remedy(self):
        package = self.reach_conditional()
        with self.assertRaises(DomainError):
            self.service.confirm_takeover("owner", Role.OWNER, package.package_id)

    def test_conditional_acceptance_requires_remedy_per_defect(self):
        package = self.reach_site_acceptance(defects=({"description": "阀门渗漏"}, {"description": "轴承异响"}))
        only_one = [{"defect_id": package.defects[0].defect_id, "deadline": self.clock() + timedelta(days=1), "on_expiry": "DEDUCT"}]
        with self.assertRaises(DomainError):
            self.service.accept_with_conditions("owner", Role.OWNER, package.package_id, only_one)

    def test_conditional_acceptance_rejects_past_deadline(self):
        package = self.reach_site_acceptance()
        spec = [{"defect_id": package.defects[0].defect_id, "deadline": self.clock() - timedelta(days=1), "on_expiry": "DEDUCT"}]
        with self.assertRaises(DomainError):
            self.service.accept_with_conditions("owner", Role.OWNER, package.package_id, spec)

    def test_conditional_acceptance_without_defects_is_rejected(self):
        package = self.create_package()
        self.service.confirm_document_review("owner", Role.OWNER, package.package_id)
        with self.assertRaises(DomainError):
            self.service.accept_with_conditions("owner", Role.OWNER, package.package_id, [])

    def test_second_package_blocked_while_active(self):
        self.create_package()
        with self.assertRaises(DomainError):
            self.create_package()


class RemedyExpiryTests(ServiceTestCase):
    def test_expired_remedy_escalates_with_new_deadline(self):
        package = self.reach_conditional(on_expiry="ESCALATE", escalation_extension_days=3)
        remedy = package.remedies[0]
        self.clock.advance(days=3)
        processed = self.service.process_expired_remedies("owner", Role.OWNER)
        self.assertEqual(processed, [remedy.remedy_id])

        updated = self.service.get_package(package.package_id).remedies[0]
        self.assertEqual(updated.status, RemedyStatus.OPEN)
        self.assertEqual(updated.escalation_level, 1)
        self.assertEqual(updated.deadline, remedy.deadline + timedelta(days=3))
        # 升级后补救项仍未关闭，接管继续被阻断
        with self.assertRaises(DomainError):
            self.service.confirm_takeover("owner", Role.OWNER, package.package_id)

    def test_expired_remedy_deducts_and_settles(self):
        package = self.reach_conditional(on_expiry="DEDUCT", deduction_amount=5000.0)
        remedy = package.remedies[0]
        self.clock.advance(days=3)
        self.service.process_expired_remedies("owner", Role.OWNER)

        package = self.service.get_package(package.package_id)
        self.assertEqual(package.remedies[0].status, RemedyStatus.DEDUCTED)
        self.assertEqual(package.defects[0].status, DefectStatus.SETTLED)
        deductions = self.service.list_deductions(package.package_id)
        self.assertEqual(len(deductions), 1)
        self.assertEqual(deductions[0].amount, 5000.0)
        self.assertEqual(deductions[0].remedy_id, remedy.remedy_id)
        # 扣减结案后可以正式接管
        package = self.service.confirm_takeover("owner", Role.OWNER, package.package_id)
        self.assertEqual(package.state, PackageState.FORMAL_TAKEOVER)

    def test_expired_remedy_returns_asset_to_builder(self):
        package = self.reach_conditional(on_expiry="RETURN")
        self.clock.advance(days=3)
        self.service.process_expired_remedies("owner", Role.OWNER)

        package = self.service.get_package(package.package_id)
        self.assertEqual(package.state, PackageState.RETURNED)
        self.assertEqual(package.remedies[0].status, RemedyStatus.VOID)
        responsibility = self.service.asset_responsibility("pump-001")
        self.assertEqual(responsibility["current"].party, "建设单位")
        self.assertEqual(len(responsibility["history"]), 2)

    def test_unexpired_remedy_is_not_processed(self):
        package = self.reach_conditional(on_expiry="DEDUCT")
        processed = self.service.process_expired_remedies("owner", Role.OWNER)
        self.assertEqual(processed, [])
        self.assertEqual(self.service.get_package(package.package_id).remedies[0].status, RemedyStatus.OPEN)


class PermissionTests(ServiceTestCase):
    def test_waive_remedy_requires_owner(self):
        package = self.reach_conditional()
        remedy = package.remedies[0]
        with self.assertRaises(PermissionError):
            self.service.waive_remedy("inspector", Role.INSPECTOR, package.package_id, remedy.remedy_id, "影响可接受")
        self.service.waive_remedy("owner", Role.OWNER, package.package_id, remedy.remedy_id, "影响可接受")
        package = self.service.get_package(package.package_id)
        self.assertEqual(package.remedies[0].status, RemedyStatus.WAIVED)
        self.assertEqual(package.defects[0].status, DefectStatus.WAIVED)
        package = self.service.confirm_takeover("owner", Role.OWNER, package.package_id)
        self.assertEqual(package.state, PackageState.FORMAL_TAKEOVER)

    def test_waive_defect_is_explicit_and_audited(self):
        package = self.reach_site_acceptance()
        defect = package.defects[0]
        with self.assertRaises(PermissionError):
            self.service.waive_defect("operator", Role.OPERATOR, package.package_id, defect.defect_id, "不影响运行")
        self.service.waive_defect("owner", Role.OWNER, package.package_id, defect.defect_id, "不影响运行")
        package = self.service.confirm_takeover("owner", Role.OWNER, package.package_id)
        self.assertEqual(package.state, PackageState.FORMAL_TAKEOVER)
        actions = [event.action for event in self.service.takeover_decision_trail(package.package_id)]
        self.assertIn("WAIVE_DEFECT", actions)

    def test_confirm_roles_are_enforced(self):
        package = self.create_package()
        with self.assertRaises(PermissionError):
            self.service.confirm_document_review("operator", Role.OPERATOR, package.package_id)
        with self.assertRaises(PermissionError):
            self.service.transfer_responsibility("inspector", Role.INSPECTOR, "pump-001", "第三方", "运营主体变更")
        with self.assertRaises(PermissionError):
            self.service.process_expired_remedies("operator", Role.OPERATOR)


class ResponsibilityChainTests(ServiceTestCase):
    def test_transfer_keeps_single_current_link(self):
        self.create_package()
        self.service.transfer_responsibility("owner", Role.OWNER, "pump-001", "第三方运维公司", "运营主体变更")
        responsibility = self.service.asset_responsibility("pump-001")
        self.assertEqual(responsibility["current"].party, "第三方运维公司")
        history = responsibility["history"]
        self.assertEqual(len(history), 2)
        self.assertIsNotNone(history[0].ended_at)
        self.assertIsNone(history[1].ended_at)
        self.assertEqual(sum(1 for link in history if link.is_current), 1)

    def test_obligation_transfer_is_recorded_in_chain(self):
        self.create_package()
        self.service.transfer_responsibility("owner", Role.OWNER, "pump-001", "专业维保公司", "义务转让", obligation_code="SLA-1")
        responsibility = self.service.asset_responsibility("pump-001")
        self.assertIn("SLA-1", responsibility["current"].reason)

    def test_transfer_to_same_party_is_rejected(self):
        self.create_package()
        with self.assertRaises(DomainError):
            self.service.transfer_responsibility("owner", Role.OWNER, "pump-001", "建设单位", "运营主体变更")

    def test_unknown_asset_has_no_chain(self):
        with self.assertRaises(DomainError):
            self.service.asset_responsibility("ghost-asset")


class RepairReportTests(ServiceTestCase):
    def test_repeated_reports_merge_into_single_chain(self):
        self.create_package()
        first = self.service.report_repair("operator", Role.OPERATOR, "pump-001", "leak-valve", "阀门渗漏")
        second = self.service.report_repair("operator", Role.OPERATOR, "pump-001", "leak-valve", "阀门又漏了")
        self.assertEqual(first.report_id, second.report_id)
        self.assertEqual(second.repeat_count, 1)
        self.assertEqual(len(self.service._reports), 1)
        # 报修单挂在当前责任链节上
        current = self.service.current_responsibility("pump-001")
        self.assertEqual(first.chain_sequence, current.sequence)

        other = self.service.report_repair("operator", Role.OPERATOR, "pump-001", "noise-bearing", "轴承异响")
        self.assertNotEqual(other.report_id, first.report_id)

    def test_closed_report_allows_new_report(self):
        self.create_package()
        first = self.service.report_repair("operator", Role.OPERATOR, "pump-001", "leak-valve", "阀门渗漏")
        self.service.close_repair("inspector", Role.INSPECTOR, first.report_id)
        second = self.service.report_repair("operator", Role.OPERATOR, "pump-001", "leak-valve", "阀门再次渗漏")
        self.assertNotEqual(first.report_id, second.report_id)
        self.assertEqual(self.service.repair_report(first.report_id).status, RepairStatus.CLOSED)


class ContractRevisionTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.create_package()
        self.service.register_contract_revision(
            "owner", Role.OWNER, "contract-001", [{"code": "SLA-1", "title": "故障响应", "service_level": "<=4h"}], T0
        )
        self.clock.advance(days=30)
        self.service.register_contract_revision(
            "owner", Role.OWNER, "contract-001", [{"code": "SLA-1", "title": "故障响应", "service_level": "<=2h"}], self.clock()
        )

    def test_metrics_bind_contemporary_revision(self):
        old = self.service.record_metric("owner", Role.OWNER, "pump-001", "response-hours", 3.5, recorded_at=T0 + timedelta(days=10))
        self.assertEqual(old.revision, 1)
        new = self.service.record_metric("owner", Role.OWNER, "pump-001", "response-hours", 1.5)
        self.assertEqual(new.revision, 2)

    def test_metric_without_active_revision_is_rejected(self):
        with self.assertRaises(DomainError):
            self.service.record_metric("owner", Role.OWNER, "pump-001", "response-hours", 3.5, recorded_at=T0 - timedelta(days=1))

    def test_incident_references_contemporary_obligations(self):
        incident = self.service.record_incident("owner", Role.OWNER, "pump-001", T0 + timedelta(days=10), "泵站停机")
        result = self.service.obligations_for_incident(incident.incident_id)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["obligations"][0].service_level, "<=4h")

        later = self.service.record_incident("owner", Role.OWNER, "pump-001", self.clock(), "泵站再次停机")
        result = self.service.obligations_for_incident(later.incident_id)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["obligations"][0].service_level, "<=2h")

    def test_revision_numbers_must_increase(self):
        with self.assertRaises(DomainError):
            self.service.register_contract_revision("owner", Role.OWNER, "contract-001", [], self.clock() + timedelta(days=1), revision=1)


class QueryTests(ServiceTestCase):
    def test_unmet_conditions(self):
        package = self.reach_conditional()
        result = self.service.unmet_conditions(package.package_id)
        self.assertFalse(result["satisfied"])
        self.assertEqual(len(result["open_defects"]), 1)
        self.assertEqual(len(result["open_remedies"]), 1)
        self.assertEqual(result["overdue_remedy_ids"], [])

        self.clock.advance(days=3)
        result = self.service.unmet_conditions(package.package_id)
        self.assertEqual(result["overdue_remedy_ids"], [package.remedies[0].remedy_id])

    def test_decision_trail_records_takeover_formation(self):
        package = self.reach_conditional()
        remedy = package.remedies[0]
        self.service.confirm_remedy_completed("inspector", Role.INSPECTOR, package.package_id, remedy.remedy_id)
        self.service.confirm_takeover("owner", Role.OWNER, package.package_id)

        actions = [event.action for event in self.service.takeover_decision_trail(package.package_id)]
        self.assertEqual(
            actions,
            ["CREATE_PACKAGE", "CONFIRM_DOCUMENT_REVIEW", "ACCEPT_WITH_CONDITIONS", "CONFIRM_REMEDY_COMPLETED", "CONFIRM_TAKEOVER"],
        )
        for event in self.service.takeover_decision_trail(package.package_id):
            self.assertTrue(event.actor)
            self.assertTrue(event.role)

    def test_state_roundtrip_through_snapshot(self):
        package = self.reach_conditional(on_expiry="DEDUCT", deduction_amount=800.0)
        self.clock.advance(days=3)
        self.service.process_expired_remedies("owner", Role.OWNER)
        self.service.report_repair("operator", Role.OPERATOR, "pump-001", "leak-valve", "阀门渗漏")
        self.service.register_contract_revision(
            "owner", Role.OWNER, "contract-001", [{"code": "SLA-1", "title": "响应", "service_level": "<=4h"}], T0
        )

        snapshot = service_to_dict(self.service)
        restored = service_from_dict(snapshot, clock=self.clock)
        self.assertEqual(
            json.dumps(service_to_dict(restored), ensure_ascii=False, sort_keys=True),
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
        )
        self.assertEqual(restored.get_package(package.package_id).state, package.state)


if __name__ == "__main__":
    unittest.main()
