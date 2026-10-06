"""序列化与存储往返测试。"""

import tempfile
import unittest
from pathlib import Path

from asset_handover import (
    AssetVersion,
    Contract,
    ContractRevision,
    Defect,
    EmergencyContact,
    HandoverPackage,
    JsonFileStore,
    MaintenancePlan,
    MaintenanceTask,
    Obligation,
    ObligationType,
    OverduePolicy,
    PackageState,
    RemediationItem,
    Store,
    WarrantyBoundary,
    parse_datetime,
)
from asset_handover.serde import from_jsonable, to_jsonable


def _rich_package() -> HandoverPackage:
    return HandoverPackage(
        package_code="PKG-001",
        project_code="PRJ-001",
        asset=AssetVersion("PS-001", "1.0", "雨水泵站", "排水设施", "城东路 18 号", {"功率": "75kW"}),
        contract_code="HT-001",
        constructor_party="城建集团",
        operator_party="城投水务",
        baseline_revision="R1",
        state=PackageState.CONDITIONAL_ACCEPTANCE,
        warranties=(WarrantyBoundary("WR-1", "主体结构", "城建集团", parse_datetime("2026-01-01").date(), parse_datetime("2028-01-01").date()),),
        maintenance_plan=MaintenancePlan("MP-1", "城投水务", (MaintenanceTask("MT-1", "巡检", 30),)),
        contacts=(EmergencyContact("张工", "值班", "13800000000", 1),),
        defects=(Defect("DF-1", "渗漏", "严重", parse_datetime("2026-01-20")),),
        remediations=(RemediationItem("RM-1", "修复", parse_datetime("2026-03-01"), OverduePolicy.DEDUCT, "20000", ("DF-1",)),),
        created_at=parse_datetime("2026-01-10T09:00:00+00:00"),
    )


class SerdeTests(unittest.TestCase):
    def test_package_roundtrip_preserves_everything(self) -> None:
        package = _rich_package()
        restored = from_jsonable(HandoverPackage, to_jsonable(package))
        self.assertEqual(restored, package)
        self.assertEqual(restored.fingerprint(), package.fingerprint())

    def test_missing_fields_fall_back_to_defaults(self) -> None:
        data = {
            "package_code": "PKG-2",
            "project_code": "PRJ-2",
            "asset": {"asset_code": "A-1", "version": "1", "name": "桥", "category": "桥梁", "location": "城西"},
            "contract_code": "HT-1",
            "constructor_party": "甲",
            "operator_party": "乙",
        }
        package = from_jsonable(HandoverPackage, data)
        self.assertIs(package.state, PackageState.DRAFT)
        self.assertEqual(package.defects, ())

    def test_missing_required_field_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            from_jsonable(HandoverPackage, {"package_code": "PKG-3"})

    def test_enum_datetime_and_date_roundtrip(self) -> None:
        contract = Contract(
            "HT-1", "合同",
            (ContractRevision("HT-1", "R1", parse_datetime("2026-01-01T00:00:00+00:00"),
                              (Obligation("OB-1", "HT-1", ObligationType.SPARE_PARTS, "备件", "甲"),)),),
        )
        restored = from_jsonable(Contract, to_jsonable(contract))
        self.assertEqual(restored, contract)


class JsonFileStoreTests(unittest.TestCase):
    def test_save_and_reload_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.json"
            store = JsonFileStore(path)
            package = _rich_package()
            store.packages[package.package_code] = package
            store.contracts["HT-001"] = Contract("HT-001", "合同")
            store.next_seq()
            store.save()

            reloaded = JsonFileStore(path)
            self.assertEqual(reloaded.packages["PKG-001"], package)
            self.assertEqual(reloaded.packages["PKG-001"].fingerprint(), package.fingerprint())
            self.assertEqual(reloaded.next_seq(), 2)

    def test_missing_file_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonFileStore(Path(tmp) / "new.json")
            self.assertEqual(store.packages, {})
            self.assertIsInstance(store, Store)


if __name__ == "__main__":
    unittest.main()
