"""基础设施运营责任交接 HTTP API 测试。"""

import http.client
import json
import threading
import unittest

from asset_handover.api import make_server
from asset_handover.service import HandoverService


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = HandoverService()
        cls.server = make_server(cls.service, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, method, path, body=None, role="OWNER", actor="api-tester"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-Actor": actor, "X-Role": role}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, data

    def test_health(self):
        status, data = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

    def test_unknown_path_returns_404(self):
        status, data = self.request("GET", "/no-such-path")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    def test_package_lifecycle_over_http(self):
        status, package = self.request(
            "POST",
            "/packages",
            {
                "asset": {"asset_code": "lift-001", "version": "v2.3", "installed_at": "2026-01-01T00:00:00+00:00"},
                "contract_id": "contract-901",
                "defects": [{"description": "轿厢异响", "severity": "一般"}],
            },
        )
        self.assertEqual(status, 201)
        package_id = package["package_id"]
        defect_id = package["defects"][0]["defect_id"]
        self.assertEqual(package["state"], "DOC_REVIEW")

        # 角色不足被拒绝
        status, data = self.request("POST", f"/packages/{package_id}/confirm-documents", role="OPERATOR")
        self.assertEqual(status, 403)
        self.assertIn("error", data)

        status, package = self.request("POST", f"/packages/{package_id}/confirm-documents", role="INSPECTOR")
        self.assertEqual(status, 200)
        self.assertEqual(package["state"], "SITE_ACCEPTANCE")

        # 有未决缺陷时不能静默接管
        status, data = self.request("POST", f"/packages/{package_id}/confirm-takeover")
        self.assertEqual(status, 400)

        status, package = self.request(
            "POST",
            f"/packages/{package_id}/accept-with-conditions",
            {"remedies": [{"defect_id": defect_id, "deadline": "2027-01-01T00:00:00+00:00", "on_expiry": "DEDUCT", "deduction_amount": 1200}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(package["state"], "CONDITIONAL_ACCEPTANCE")
        remedy_id = package["remedies"][0]["remedy_id"]

        status, conditions = self.request("GET", f"/packages/{package_id}/unmet-conditions")
        self.assertEqual(status, 200)
        self.assertFalse(conditions["satisfied"])
        self.assertEqual(len(conditions["open_remedies"]), 1)

        status, package = self.request("POST", f"/packages/{package_id}/remedies/{remedy_id}/confirm-completed", {"note": "复验通过"}, role="INSPECTOR")
        self.assertEqual(status, 200)

        status, package = self.request("POST", f"/packages/{package_id}/confirm-takeover")
        self.assertEqual(status, 200)
        self.assertEqual(package["state"], "FORMAL_TAKEOVER")

        # 业主查询：资产责任主体
        status, responsibility = self.request("GET", "/assets/lift-001/responsibility")
        self.assertEqual(status, 200)
        self.assertEqual(responsibility["current"]["party"], "运营企业")
        self.assertEqual(len(responsibility["history"]), 2)

        # 业主查询：接管决定形成过程
        status, trail = self.request("GET", f"/packages/{package_id}/decision-trail")
        self.assertEqual(status, 200)
        actions = [event["action"] for event in trail]
        self.assertEqual(
            actions,
            ["CREATE_PACKAGE", "CONFIRM_DOCUMENT_REVIEW", "ACCEPT_WITH_CONDITIONS", "CONFIRM_REMEDY_COMPLETED", "CONFIRM_TAKEOVER"],
        )

    def test_incident_obligations_over_http(self):
        self.request(
            "POST",
            "/packages",
            {"asset": {"asset_code": "pump-901", "version": "v1.0", "installed_at": "2026-01-01T00:00:00+00:00"}, "contract_id": "contract-902"},
        )
        status, revision = self.request(
            "POST",
            "/contracts/contract-902/revisions",
            {"effective_from": "2026-01-01T00:00:00+00:00", "obligations": [{"code": "SLA-1", "title": "故障响应", "service_level": "<=4h"}]},
        )
        self.assertEqual(status, 201)

        status, incident = self.request(
            "POST", "/incidents", {"asset_code": "pump-901", "occurred_at": "2026-02-01T00:00:00+00:00", "description": "停泵事故"}
        )
        self.assertEqual(status, 201)

        status, result = self.request("GET", f"/incidents/{incident['incident_id']}/obligations")
        self.assertEqual(status, 200)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["obligations"][0]["code"], "SLA-1")

    def test_unknown_package_returns_400(self):
        status, data = self.request("GET", "/packages/pkg-404")
        self.assertEqual(status, 400)
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
