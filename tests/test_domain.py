"""领域模型测试。"""

import unittest
from datetime import timezone

from asset_handover import (
    Contract,
    ContractRevision,
    Defect,
    DefectStatus,
    HandoverPackage,
    AssetVersion,
    MaintenancePlan,
    MaintenanceTask,
    Obligation,
    ObligationType,
    PackageState,
    RemediationItem,
    OverduePolicy,
    WarrantyBoundary,
    parse_datetime,
)


def _asset() -> AssetVersion:
    return AssetVersion("PS-001", "1.0", "雨水泵站", "排水设施", "城东路 18 号")


def _package() -> HandoverPackage:
    return HandoverPackage(
        package_code="PKG-001",
        project_code="PRJ-001",
        asset=_asset(),
        contract_code="HT-001",
        constructor_party="城建集团",
        operator_party="城投水务",
    )


class EntityValidationTests(unittest.TestCase):
    def test_blank_code_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AssetVersion(" ", "1.0", "泵站", "排水", "城东")

    def test_warranty_period_must_be_ordered(self) -> None:
        with self.assertRaises(ValueError):
            WarrantyBoundary(
                "WR-1", "主体", "城建集团",
                parse_datetime("2028-01-01").date(), parse_datetime("2026-01-01").date(),
            )

    def test_maintenance_plan_requires_task(self) -> None:
        with self.assertRaises(ValueError):
            MaintenancePlan("MP-1", "城投水务", ())

    def test_enum_fields_accept_strings(self) -> None:
        item = RemediationItem(
            "RM-1", "修复", parse_datetime("2026-03-01"), "DEDUCT", "1000"
        )
        self.assertIs(item.policy, OverduePolicy.DEDUCT)


class PackageBehaviorTests(unittest.TestCase):
    def test_fingerprint_is_stable(self) -> None:
        self.assertEqual(_package().fingerprint(), _package().fingerprint())

    def test_evolve_keeps_original(self) -> None:
        original = _package()
        changed = original.evolve(state=PackageState.DOC_REVIEW)
        self.assertIs(original.state, PackageState.DRAFT)
        self.assertIs(changed.state, PackageState.DOC_REVIEW)
        self.assertNotEqual(original.fingerprint(), changed.fingerprint())

    def test_open_defects_and_remediations(self) -> None:
        defect = Defect("DF-1", "渗漏", "严重", parse_datetime("2026-01-20"), status=DefectStatus.OPEN)
        item = RemediationItem("RM-1", "修复", parse_datetime("2026-03-01"), OverduePolicy.ESCALATE)
        package = _package().evolve(defects=(defect,), remediations=(item,))
        self.assertEqual([d.defect_code for d in package.open_defects()], ["DF-1"])
        self.assertEqual([i.item_code for i in package.open_remediations()], ["RM-1"])


class ContractRevisionTests(unittest.TestCase):
    def test_revision_at_selects_effective_version(self) -> None:
        contract = Contract(
            "HT-001", "运营合同",
            (
                ContractRevision("HT-001", "R2", parse_datetime("2026-06-01"), ()),
                ContractRevision("HT-001", "R1", parse_datetime("2026-01-01"), ()),
            ),
        )
        self.assertEqual(contract.revision_at(parse_datetime("2026-03-01")).revision, "R1")
        self.assertEqual(contract.revision_at(parse_datetime("2026-07-01")).revision, "R2")

    def test_revision_at_rejects_time_before_first_version(self) -> None:
        contract = Contract(
            "HT-001", "运营合同",
            (ContractRevision("HT-001", "R1", parse_datetime("2026-01-01"), ()),),
        )
        with self.assertRaises(Exception):
            contract.revision_at(parse_datetime("2025-01-01"))


class ParseDatetimeTests(unittest.TestCase):
    def test_naive_value_is_treated_as_utc(self) -> None:
        self.assertEqual(parse_datetime("2026-01-01T08:00:00").tzinfo, timezone.utc)

    def test_aware_value_keeps_timezone(self) -> None:
        parsed = parse_datetime("2026-01-01T08:00:00+08:00")
        self.assertEqual(parsed.utcoffset().total_seconds(), 8 * 3600)


if __name__ == "__main__":
    unittest.main()
