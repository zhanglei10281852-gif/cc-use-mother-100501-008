"""命令行集成测试。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def run_cli(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "run_cli.py"), *args],
        capture_output=True, text=True, input=input_text, env=ENV, cwd=ROOT,
    )


class CliTests(unittest.TestCase):
    def test_demo_runs_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--store", str(Path(tmp) / "demo.json"), "demo")
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = json.loads(result.stdout)
            self.assertGreaterEqual(len(steps), 8)
            states = [step for step in steps if step["step"] == "正式接管"]
            self.assertEqual(states[0]["result"]["state"], "FORMAL_TAKEOVER")

    def test_persistence_across_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = str(Path(tmp) / "store.json")
            package = {
                "package_code": "PKG-100",
                "project_code": "PRJ-100",
                "asset": {"asset_code": "BR-001", "version": "2.0", "name": "城西桥", "category": "桥梁", "location": "城西路"},
                "contract_code": "HT-100",
                "constructor_party": "城建集团",
                "operator_party": "城投水务",
                "warranties": [{"warranty_code": "WR-1", "scope": "桥面", "responsible_party": "城建集团",
                                "valid_from": "2026-01-01", "valid_until": "2028-01-01"}],
                "maintenance_plan": {"plan_code": "MP-1", "responsible_party": "城投水务",
                                     "tasks": [{"task_code": "MT-1", "name": "巡检", "interval_days": 30}]},
                "contacts": [{"name": "张工", "role": "值班", "phone": "13800000000", "priority": 1}],
            }
            common = ["--store", store, "--now", "2026-01-10T09:00:00+00:00"]
            self.assertEqual(run_cli(*common, "register-contract", "--contract-code", "HT-100", "--title", "桥梁运营合同").returncode, 0)
            obligations = json.dumps([
                {"obligation_code": "OB-1", "contract_code": "HT-100", "obligation_type": "WARRANTY_DUTY",
                 "description": "保修义务", "owner_party": "城建集团"}
            ], ensure_ascii=False)
            created = run_cli(*common, "register-revision", "--contract-code", "HT-100", "--revision", "R1",
                              "--effective-from", "2026-01-01T00:00:00+00:00", "--obligations", obligations)
            self.assertEqual(created.returncode, 0, created.stderr)
            created = run_cli(*common, "create-package", "--data", json.dumps(package, ensure_ascii=False))
            self.assertEqual(created.returncode, 0, created.stderr)
            submitted = run_cli(*common, "submit", "--package", "PKG-100")
            self.assertEqual(submitted.returncode, 0, submitted.stderr)
            status = run_cli("--store", store, "package-status", "--package", "PKG-100")
            self.assertEqual(status.returncode, 0, status.stderr)
            payload = json.loads(status.stdout)
            self.assertEqual(payload["state"], "DOC_REVIEW")
            self.assertEqual(payload["baseline_revision"], "R1")
            responsibility = run_cli("--store", store, "asset-responsibility", "--asset", "BR-001",
                                     "--at", "2026-01-11T00:00:00+00:00")
            self.assertEqual(json.loads(responsibility.stdout)["party"], "城建集团")

    def test_permission_error_exits_with_code_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = str(Path(tmp) / "store.json")
            result = run_cli("--store", store, "--now", "2026-01-10T09:00:00+00:00",
                             "--role", "OPERATOR_REP", "register-contract", "--contract-code", "HT-1", "--title", "合同")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["type"], "PermissionDenied")


if __name__ == "__main__":
    unittest.main()
