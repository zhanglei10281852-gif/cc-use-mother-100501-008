"""应用服务测试：状态机、补救项逾期处置、责任链、版本绑定与权限。"""

import unittest

from asset_handover import (
    Actor,
    AssetVersion,
    ConflictError,
    DefectStatus,
    EmergencyContact,
    HandoverPackage,
    HandoverService,
    MaintenancePlan,
    MaintenanceTask,
    NotFoundError,
    Obligation,
    ObligationType,
    OverduePolicy,
    PackageState,
    PermissionDenied,
    RemediationItem,
    RemediationStatus,
    Role,
    StateTransitionError,
    Store,
    ValidationError,
    WarrantyBoundary,
    parse_datetime,
)

OWNER = Actor("owner-01", "业主代表", (Role.OWNER_REP,))
LEAD = Actor("lead-01", "验收组长", (Role.ACCEPTANCE_LEAD,))
OPERATOR = Actor("op-01", "运营企业代表", (Role.OPERATOR_REP,))
CONSTRUCTOR = Actor("cons-01", "建设单位代表", (Role.CONSTRUCTOR_REP,))


def t(text: str):
    return parse_datetime(f"{text}+00:00" if "+" not in text else text)


def _package(package_code: str = "PKG-001", asset_code: str = "PS-001") -> HandoverPackage:
    return HandoverPackage(
        package_code=package_code,
        project_code="PRJ-001",
        asset=AssetVersion(asset_code, "1.0", "雨水泵站", "排水设施", "城东路 18 号"),
        contract_code="HT-001",
        constructor_party="城建集团",
        operator_party="城投水务",
        warranties=(WarrantyBoundary("WR-1", "主体结构", "城建集团", t("2026-01-01").date(), t("2028-01-01").date()),),
        maintenance_plan=MaintenancePlan("MP-1", "城投水务", (MaintenanceTask("MT-1", "巡检", 30),)),
        contacts=(EmergencyContact("张工", "值班", "13800000000", 1),),
    )


def _obligations() -> list[Obligation]:
    return [
        Obligation("OB-01", "HT-001", ObligationType.DEFECT_RECTIFICATION, "缺陷整改", "城建集团"),
        Obligation("OB-02", "HT-001", ObligationType.SPARE_PARTS, "备件两套", "城建集团"),
        Obligation("OB-03", "HT-001", ObligationType.SERVICE_LEVEL, "可用率不低于 99.9%", "城投水务", indicator="可用率", target="99.9", unit="%"),
    ]


class ServiceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.service = HandoverService(Store())
        self.service.register_contract(OWNER, "HT-001", "泵站运营合同", now=t("2026-01-01T09:00"))
        self.service.register_revision(OWNER, "HT-001", "R1", t("2026-01-01"), _obligations(), now=t("2026-01-01T09:05"))

    def create_and_submit(self) -> HandoverPackage:
        self.service.create_package(OWNER, _package(), now=t("2026-01-10T09:00"))
        return self.service.submit_package(OWNER, "PKG-001", now=t("2026-01-10T10:00"))

    def reach_inspection(self) -> HandoverPackage:
        self.create_and_submit()
        return self.service.confirm_documents(LEAD, "PKG-001", now=t("2026-01-15T09:00"))

    def reach_conditional(self) -> HandoverPackage:
        self.reach_inspection()
        self.service.record_defect(LEAD, "PKG-001", "DF-01", "闸门渗漏", "严重", now=t("2026-01-20T09:00"))
        return self.service.accept_conditionally(
            LEAD, "PKG-001",
            [RemediationItem("RM-01", "修复闸门渗漏", t("2026-03-01"), OverduePolicy.DEDUCT, "20000", ("DF-01",))],
            now=t("2026-01-25T09:00"),
        )


class LifecycleTests(ServiceTestBase):
    def test_full_path_to_formal_takeover(self) -> None:
        self.reach_conditional()
        self.service.complete_remediation(LEAD, "PKG-001", "RM-01", note="灌水试验合格", now=t("2026-02-20T09:00"))
        package = self.service.take_over(OWNER, "PKG-001", now=t("2026-02-21T09:00"))
        self.assertIs(package.state, PackageState.FORMAL_TAKEOVER)
        responsibility = self.service.asset_responsibility("PS-001", at=t("2026-02-22"))
        self.assertEqual(responsibility["party"], "城投水务")

    def test_submit_requires_complete_package(self) -> None:
        incomplete = _package().evolve(warranties=())
        self.service.create_package(OWNER, incomplete, now=t("2026-01-10T09:00"))
        with self.assertRaises(ValidationError):
            self.service.submit_package(OWNER, "PKG-001", now=t("2026-01-10T10:00"))

    def test_submit_snapshots_baseline_revision(self) -> None:
        package = self.create_and_submit()
        self.assertEqual(package.baseline_revision, "R1")

    def test_state_machine_rejects_out_of_order_steps(self) -> None:
        self.service.create_package(OWNER, _package(), now=t("2026-01-10T09:00"))
        with self.assertRaises(StateTransitionError):
            self.service.confirm_documents(LEAD, "PKG-001", now=t("2026-01-11T09:00"))
        with self.assertRaises(StateTransitionError):
            self.service.take_over(OWNER, "PKG-001", now=t("2026-01-11T09:00"))

    def test_asset_cannot_have_two_active_packages(self) -> None:
        self.service.create_package(OWNER, _package(), now=t("2026-01-10T09:00"))
        with self.assertRaises(ConflictError):
            self.service.create_package(OWNER, _package("PKG-002"), now=t("2026-01-11T09:00"))

    def test_direct_takeover_blocked_by_open_defect(self) -> None:
        self.reach_inspection()
        self.service.record_defect(LEAD, "PKG-001", "DF-01", "渗漏", "严重", now=t("2026-01-20T09:00"))
        with self.assertRaises(StateTransitionError):
            self.service.take_over(OWNER, "PKG-001", now=t("2026-01-21T09:00"))

    def test_direct_takeover_allowed_when_clean(self) -> None:
        self.reach_inspection()
        package = self.service.take_over(OWNER, "PKG-001", now=t("2026-01-21T09:00"))
        self.assertIs(package.state, PackageState.FORMAL_TAKEOVER)


class ConditionalAcceptanceTests(ServiceTestBase):
    def test_conditions_must_cover_every_open_defect(self) -> None:
        self.reach_inspection()
        self.service.record_defect(LEAD, "PKG-001", "DF-01", "渗漏", "严重", now=t("2026-01-20T09:00"))
        self.service.record_defect(LEAD, "PKG-001", "DF-02", "异响", "一般", now=t("2026-01-20T10:00"))
        with self.assertRaises(ValidationError):
            self.service.accept_conditionally(
                LEAD, "PKG-001",
                [RemediationItem("RM-01", "修复渗漏", t("2026-03-01"), OverduePolicy.ESCALATE, "", ("DF-01",))],
                now=t("2026-01-25T09:00"),
            )

    def test_conditions_require_future_deadline(self) -> None:
        self.reach_inspection()
        with self.assertRaises(ValidationError):
            self.service.accept_conditionally(
                LEAD, "PKG-001",
                [RemediationItem("RM-01", "补资料", t("2026-01-01"), OverduePolicy.ESCALATE)],
                now=t("2026-01-25T09:00"),
            )

    def test_conditions_require_at_least_one_item(self) -> None:
        self.reach_inspection()
        with self.assertRaises(ValidationError):
            self.service.accept_conditionally(LEAD, "PKG-001", [], now=t("2026-01-25T09:00"))

    def test_completion_closes_linked_defects(self) -> None:
        package = self.reach_conditional()
        package = self.service.complete_remediation(LEAD, "PKG-001", "RM-01", now=t("2026-02-20T09:00"))
        self.assertIs(package.find_defect("DF-01").status, DefectStatus.RESOLVED)
        self.assertIs(package.find_remediation("RM-01").status, RemediationStatus.COMPLETED)

    def test_waiver_requires_owner_role_and_reason(self) -> None:
        self.reach_conditional()
        with self.assertRaises(PermissionDenied):
            self.service.waive_remediation(LEAD, "PKG-001", "RM-01", "理由", now=t("2026-02-01T09:00"))
        with self.assertRaises(ValidationError):
            self.service.waive_remediation(OWNER, "PKG-001", "RM-01", "  ", now=t("2026-02-01T09:00"))
        package = self.service.waive_remediation(OWNER, "PKG-001", "RM-01", "业主同意改由大修解决", now=t("2026-02-01T09:00"))
        self.assertIs(package.find_remediation("RM-01").status, RemediationStatus.WAIVED)
        self.assertIs(package.find_defect("DF-01").status, DefectStatus.WAIVED)

    def test_takeover_blocked_until_all_items_closed(self) -> None:
        self.reach_conditional()
        with self.assertRaises(StateTransitionError):
            self.service.take_over(OWNER, "PKG-001", now=t("2026-02-01T09:00"))


class OverduePolicyTests(ServiceTestBase):
    def test_deduct_records_penalty_but_never_closes_item(self) -> None:
        self.reach_conditional()
        actions = self.service.process_overdue(OWNER, "PKG-001", now=t("2026-03-05T09:00"))
        self.assertEqual(actions, [{"item_code": "RM-01", "action": "deducted", "amount": 20000.0}])
        package = self.service.store.packages["PKG-001"]
        item = package.find_remediation("RM-01")
        self.assertIs(item.status, RemediationStatus.OPEN)
        self.assertEqual(len(item.deductions), 1)
        self.assertIs(package.find_defect("DF-01").status, DefectStatus.OPEN)
        with self.assertRaises(StateTransitionError):
            self.service.take_over(OWNER, "PKG-001", now=t("2026-03-06T09:00"))

    def test_escalate_then_reschedule_then_complete(self) -> None:
        self.reach_inspection()
        self.service.accept_conditionally(
            LEAD, "PKG-001",
            [RemediationItem("RM-01", "补齐备件", t("2026-02-10"), OverduePolicy.ESCALATE)],
            now=t("2026-01-25T09:00"),
        )
        actions = self.service.process_overdue(OWNER, "PKG-001", now=t("2026-02-15T09:00"))
        self.assertEqual(actions, [{"item_code": "RM-01", "action": "escalated", "level": 1}])
        package = self.service.store.packages["PKG-001"]
        self.assertIs(package.find_remediation("RM-01").status, RemediationStatus.ESCALATED)
        package = self.service.reschedule_remediation(LEAD, "PKG-001", "RM-01", t("2026-03-01"), now=t("2026-02-16T09:00"))
        self.assertIs(package.find_remediation("RM-01").status, RemediationStatus.OPEN)
        package = self.service.complete_remediation(LEAD, "PKG-001", "RM-01", now=t("2026-02-25T09:00"))
        self.assertIs(package.find_remediation("RM-01").status, RemediationStatus.COMPLETED)

    def test_return_policy_sends_package_back(self) -> None:
        self.reach_inspection()
        self.service.accept_conditionally(
            LEAD, "PKG-001",
            [RemediationItem("RM-01", "重做防水", t("2026-02-10"), OverduePolicy.RETURN)],
            now=t("2026-01-25T09:00"),
        )
        actions = self.service.process_overdue(OWNER, "PKG-001", now=t("2026-02-15T09:00"))
        self.assertEqual(actions, [{"item_code": "RM-01", "action": "returned"}])
        package = self.service.store.packages["PKG-001"]
        self.assertIs(package.state, PackageState.RETURNED)
        self.assertIs(package.find_remediation("RM-01").status, RemediationStatus.RETURNED)
        responsibility = self.service.asset_responsibility("PS-001", at=t("2026-02-16"))
        self.assertEqual(responsibility["party"], "城建集团")

    def test_overdue_visible_in_pending_conditions(self) -> None:
        self.reach_conditional()
        pending = self.service.pending_conditions("PKG-001", now=t("2026-03-05T09:00"))
        self.assertEqual(len(pending["open_remediations"]), 1)
        self.assertTrue(pending["open_remediations"][0]["is_overdue"])
        self.assertEqual(len(pending["open_defects"]), 1)


class ResponsibilityChainTests(ServiceTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.reach_conditional()
        self.service.complete_remediation(LEAD, "PKG-001", "RM-01", now=t("2026-02-20T09:00"))
        self.service.take_over(OWNER, "PKG-001", now=t("2026-02-21T09:00"))

    def test_chain_tracks_constructor_then_operator(self) -> None:
        before = self.service.asset_responsibility("PS-001", at=t("2026-02-01"))
        after = self.service.asset_responsibility("PS-001", at=t("2026-03-01"))
        self.assertEqual(before["party"], "城建集团")
        self.assertEqual(after["party"], "城投水务")
        self.assertEqual(len(after["chain"]), 2)

    def test_operator_change_keeps_single_chain(self) -> None:
        self.service.change_operator(OWNER, "PS-001", "第二运营公司", t("2026-06-01"), now=t("2026-05-20T09:00"))
        self.assertEqual(self.service.asset_responsibility("PS-001", at=t("2026-05-31"))["party"], "城投水务")
        self.assertEqual(self.service.asset_responsibility("PS-001", at=t("2026-06-02"))["party"], "第二运营公司")

    def test_operator_change_requires_forward_clock(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.change_operator(OWNER, "PS-001", "第二运营公司", t("2026-01-01"), now=t("2026-05-20T09:00"))

    def test_operator_change_to_same_party_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.change_operator(OWNER, "PS-001", "城投水务", t("2026-06-01"), now=t("2026-05-20T09:00"))

    def test_time_before_first_link_has_no_responsibility(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.asset_responsibility("PS-001", at=t("2025-12-31"))


class ObligationTransferTests(ServiceTestBase):
    def test_transfer_moves_holder_uniquely(self) -> None:
        self.service.transfer_obligation(OWNER, "OB-02", "城投水务", t("2026-04-01"), now=t("2026-03-25T09:00"))
        before = self.service.obligation_holder("OB-02", at=t("2026-03-31"))
        after = self.service.obligation_holder("OB-02", at=t("2026-04-02"))
        self.assertEqual(before["holder"], "城建集团")
        self.assertEqual(after["holder"], "城投水务")
        self.assertEqual(after["transfers"][0].from_party, "城建集团")

    def test_transfer_from_wrong_holder_rejected(self) -> None:
        self.service.transfer_obligation(OWNER, "OB-02", "城投水务", t("2026-04-01"), now=t("2026-03-25T09:00"))
        with self.assertRaises(ValidationError):
            self.service.transfer_obligation(OWNER, "OB-02", "城建集团", t("2026-04-01"), now=t("2026-03-26T09:00"))

    def test_transfer_unknown_obligation_rejected(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.transfer_obligation(OWNER, "OB-99", "城投水务", t("2026-04-01"), now=t("2026-03-25T09:00"))


class IssueAndMetricTests(ServiceTestBase):
    def test_duplicate_reports_share_one_issue_and_chain(self) -> None:
        self.create_and_submit()
        first = self.service.report_issue(OPERATOR, "PS-001", "gate-leak", t("2026-01-12T08:00"), "渗漏", now=t("2026-01-12T08:30"))
        second = self.service.report_issue(CONSTRUCTOR, "PS-001", "gate-leak", t("2026-01-13T08:00"), "再次渗漏", now=t("2026-01-13T08:30"))
        self.assertEqual(first.issue_code, second.issue_code)
        self.assertEqual(len(second.reports), 2)
        self.assertEqual(len(self.service.store.issues), 1)

    def test_incident_binds_revision_and_responsible_at_occurrence(self) -> None:
        self.reach_conditional()
        self.service.complete_remediation(LEAD, "PKG-001", "RM-01", now=t("2026-02-20T09:00"))
        self.service.take_over(OWNER, "PKG-001", now=t("2026-02-21T09:00"))
        self.service.register_revision(
            OWNER, "HT-001", "R2", t("2026-03-01"),
            [Obligation("OB-03", "HT-001", ObligationType.SERVICE_LEVEL, "可用率不低于 99.5%", "城投水务", indicator="可用率", target="99.5", unit="%")],
            now=t("2026-02-25T09:00"),
        )
        early = self.service.report_issue(OPERATOR, "PS-001", "pump-fault", t("2026-02-10T08:00"), "水泵故障", now=t("2026-02-10T08:30"))
        late = self.service.report_issue(OPERATOR, "PS-001", "scada-fault", t("2026-03-10T08:00"), "监控中断", now=t("2026-03-10T08:30"))
        early_ctx = self.service.incident_context(early.issue_code)
        late_ctx = self.service.incident_context(late.issue_code)
        self.assertEqual(early_ctx["responsible_party"], "城建集团")
        self.assertEqual(early_ctx["revision"], "R1")
        self.assertEqual(late_ctx["responsible_party"], "城投水务")
        self.assertEqual(late_ctx["revision"], "R2")

    def test_metrics_bind_to_revision_at_recorded_time(self) -> None:
        self.service.register_revision(
            OWNER, "HT-001", "R2", t("2026-03-01"),
            [Obligation("OB-03", "HT-001", ObligationType.SERVICE_LEVEL, "可用率不低于 99.5%", "城投水务")],
            now=t("2026-02-25T09:00"),
        )
        self.service.record_metric(OPERATOR, "HT-001", "可用率", 99.9, "%", t("2026-02-15"), now=t("2026-04-01T09:00"))
        self.service.record_metric(OPERATOR, "HT-001", "可用率", 99.6, "%", t("2026-03-15"), now=t("2026-04-01T09:00"))
        r1 = self.service.metrics_for("HT-001", revision="R1")
        r2 = self.service.metrics_for("HT-001", revision="R2")
        self.assertEqual([record.value for record in r1], [99.9])
        self.assertEqual([record.value for record in r2], [99.6])

    def test_metric_before_any_revision_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.record_metric(OPERATOR, "HT-001", "可用率", 99.9, "%", t("2025-12-31"), now=t("2026-01-02T09:00"))


class PermissionTests(ServiceTestBase):
    def test_confirm_documents_requires_acceptance_lead(self) -> None:
        self.create_and_submit()
        with self.assertRaises(PermissionDenied):
            self.service.confirm_documents(OWNER, "PKG-001", now=t("2026-01-15T09:00"))

    def test_take_over_requires_owner(self) -> None:
        self.reach_inspection()
        with self.assertRaises(PermissionDenied):
            self.service.take_over(LEAD, "PKG-001", now=t("2026-01-21T09:00"))

    def test_waive_defect_requires_owner(self) -> None:
        self.reach_inspection()
        self.service.record_defect(LEAD, "PKG-001", "DF-01", "渗漏", "严重", now=t("2026-01-20T09:00"))
        with self.assertRaises(PermissionDenied):
            self.service.waive_defect(LEAD, "PKG-001", "DF-01", "影响可忽略", now=t("2026-01-21T09:00"))
        package = self.service.waive_defect(OWNER, "PKG-001", "DF-01", "影响可忽略", now=t("2026-01-21T09:00"))
        self.assertIs(package.find_defect("DF-01").status, DefectStatus.WAIVED)

    def test_record_metric_requires_operator_or_owner(self) -> None:
        with self.assertRaises(PermissionDenied):
            self.service.record_metric(LEAD, "HT-001", "可用率", 99.9, "%", t("2026-02-15"), now=t("2026-02-16T09:00"))


class AuditTrailTests(ServiceTestBase):
    def test_takeover_trail_records_decision_formation(self) -> None:
        self.reach_conditional()
        self.service.complete_remediation(LEAD, "PKG-001", "RM-01", now=t("2026-02-20T09:00"))
        self.service.take_over(OWNER, "PKG-001", now=t("2026-02-21T09:00"))
        trail = self.service.takeover_trail("PKG-001")
        actions = [event.action for event in trail]
        self.assertEqual(
            actions,
            ["create_package", "submit_package", "confirm_documents", "record_defect",
             "accept_conditionally", "complete_remediation", "take_over"],
        )
        self.assertTrue(all(event.actor_id for event in trail))
        self.assertEqual(trail[-1].details["operator"], "城投水务")


if __name__ == "__main__":
    unittest.main()
