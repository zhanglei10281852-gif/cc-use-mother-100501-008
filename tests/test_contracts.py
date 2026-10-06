"""基础设施运营责任交接基础契约工具测试。"""

import unittest
from dataclasses import dataclass

from asset_handover import stable_fingerprint, unique_by_identity


@dataclass(frozen=True)
class _Sample:
    package_code: str
    content: str


class StableFingerprintTests(unittest.TestCase):
    def test_fingerprint_ignores_key_order(self) -> None:
        left = stable_fingerprint({"a": 1, "b": "文本"})
        right = stable_fingerprint({"b": "文本", "a": 1})
        self.assertEqual(left, right)

    def test_fingerprint_changes_with_content(self) -> None:
        self.assertNotEqual(stable_fingerprint({"a": 1}), stable_fingerprint({"a": 2}))

    def test_fingerprint_supports_dataclass_and_enum(self) -> None:
        from asset_handover import PackageState

        payload = {"item": _Sample("PKG-1", "内容"), "state": PackageState.DRAFT}
        self.assertEqual(stable_fingerprint(payload), stable_fingerprint(payload))


class UniqueByIdentityTests(unittest.TestCase):
    def test_identical_items_are_deduplicated(self) -> None:
        item = _Sample("PKG-1", "内容")
        result = unique_by_identity([item, _Sample("PKG-1", "内容")])
        self.assertEqual(len(result), 1)

    def test_conflicting_identity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            unique_by_identity([_Sample("PKG-1", "内容"), _Sample("PKG-1", "冲突")])

    def test_result_is_sorted_by_identity(self) -> None:
        result = unique_by_identity([_Sample("PKG-2", "b"), _Sample("PKG-1", "a")])
        self.assertEqual([item.package_code for item in result], ["PKG-1", "PKG-2"])


if __name__ == "__main__":
    unittest.main()
