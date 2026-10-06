"""基础设施运营责任交接命令行测试。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from asset_handover.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmpdir.name) / "state.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def run_cli(self, *argv, expect=0):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--state", self.state, "--actor", "tester", *argv])
        self.assertEqual(code, expect, buffer.getvalue())
        output = buffer.getvalue()
        return json.loads(output) if output.strip() else None

    def test_create_and_query_flow(self):
        package = self.run_cli("create-package", "--asset-code", "pump-001", "--asset-version", "v1.0", "--contract-id", "contract-001")
        self.assertEqual(package["state"], "DOC_REVIEW")
        package_id = package["package_id"]

        self.run_cli("confirm-documents", "--package", package_id)
        responsibility = self.run_cli("responsibility", "--asset", "pump-001")
        self.assertEqual(responsibility["current"]["party"], "建设单位")

        conditions = self.run_cli("unmet-conditions", "--package", package_id)
        self.assertTrue(conditions["satisfied"])

        trail = self.run_cli("decision-trail", "--package", package_id)
        self.assertEqual([event["action"] for event in trail], ["CREATE_PACKAGE", "CONFIRM_DOCUMENT_REVIEW"])

    def test_state_persists_across_invocations(self):
        package = self.run_cli("create-package", "--asset-code", "lift-001", "--asset-version", "v2.0", "--contract-id", "contract-002")
        again = self.run_cli("package", "--package", package["package_id"])
        self.assertEqual(again["package_id"], package["package_id"])
        self.assertTrue(Path(self.state).exists())

    def test_permission_error_is_reported(self):
        package = self.run_cli("create-package", "--asset-code", "pump-002", "--asset-version", "v1.0", "--contract-id", "contract-003")
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = main(["--state", self.state, "--role", "OPERATOR", "confirm-takeover", "--package", package["package_id"]])
        self.assertEqual(code, 1)
        self.assertIn("error", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
