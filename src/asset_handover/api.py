"""基础设施运营责任交接的 JSON API，仅依赖标准库。

业主可以随时查询：每项资产由谁负责、哪些条件尚未满足、
一次事故应引用哪版义务、接管决定如何形成。
操作人与角色通过请求头 X-Actor / X-Role 传入。
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .domain import AssetVersion, DomainError, Role
from .serialization import parse_moment, to_jsonable
from .service import HandoverService


def _need(body: dict, key: str):
    if key not in body or body[key] in (None, ""):
        raise DomainError(f"请求缺少字段 {key}")
    return body[key]


# ----------------------------------------------------------------------
# 查询
# ----------------------------------------------------------------------
def _health(service, actor, role, body):
    return 200, {"status": "ok"}


def _list_packages(service, actor, role, body):
    return 200, service.list_packages()


def _get_package(service, actor, role, body, package_id):
    return 200, service.get_package(package_id)


def _unmet_conditions(service, actor, role, body, package_id):
    return 200, service.unmet_conditions(package_id)


def _decision_trail(service, actor, role, body, package_id):
    return 200, service.takeover_decision_trail(package_id)


def _responsibility(service, actor, role, body, asset_code):
    return 200, service.asset_responsibility(asset_code)


def _incident_obligations(service, actor, role, body, incident_id):
    return 200, service.obligations_for_incident(incident_id)


def _deductions(service, actor, role, body, package_id):
    return 200, service.list_deductions(package_id)


# ----------------------------------------------------------------------
# 操作
# ----------------------------------------------------------------------
def _create_package(service, actor, role, body):
    asset_data = _need(body, "asset")
    asset = AssetVersion(
        asset_code=_need(asset_data, "asset_code"),
        version=_need(asset_data, "version"),
        installed_at=parse_moment(asset_data["installed_at"]) if asset_data.get("installed_at") else parse_moment("1970-01-01T00:00:00+00:00"),
        notes=asset_data.get("notes", ""),
    )
    package = service.create_package(
        actor,
        role,
        asset=asset,
        contract_id=_need(body, "contract_id"),
        package_id=body.get("package_id"),
        builder_party=body.get("builder_party", "建设单位"),
        operator_party=body.get("operator_party", "运营企业"),
        defects=body.get("defects", []),
    )
    return 201, package


def _add_defect(service, actor, role, body, package_id):
    defect = service.add_defect(actor, role, package_id, _need(body, "description"), body.get("severity", "一般"))
    return 201, defect


def _confirm_documents(service, actor, role, body, package_id):
    return 200, service.confirm_document_review(actor, role, package_id)


def _accept_with_conditions(service, actor, role, body, package_id):
    specs = _need(body, "remedies")
    for spec in specs:
        if isinstance(spec.get("deadline"), str):
            spec["deadline"] = parse_moment(spec["deadline"])
    return 200, service.accept_with_conditions(actor, role, package_id, specs)


def _confirm_takeover(service, actor, role, body, package_id):
    return 200, service.confirm_takeover(actor, role, package_id)


def _return_package(service, actor, role, body, package_id):
    return 200, service.return_package(actor, role, package_id, _need(body, "reason"))


def _process_expired(service, actor, role, body, package_id):
    return 200, {"processed": service.process_expired_remedies(actor, role, package_id)}


def _confirm_remedy(service, actor, role, body, package_id, remedy_id):
    return 200, service.confirm_remedy_completed(actor, role, package_id, remedy_id, body.get("note", ""))


def _waive_remedy(service, actor, role, body, package_id, remedy_id):
    return 200, service.waive_remedy(actor, role, package_id, remedy_id, _need(body, "reason"))


def _waive_defect(service, actor, role, body, package_id, defect_id):
    return 200, service.waive_defect(actor, role, package_id, defect_id, _need(body, "reason"))


def _transfer(service, actor, role, body, asset_code):
    link = service.transfer_responsibility(
        actor, role, asset_code, _need(body, "new_party"), _need(body, "reason"), body.get("obligation_code")
    )
    return 201, link


def _report_repair(service, actor, role, body, asset_code):
    report = service.report_repair(actor, role, asset_code, _need(body, "problem_key"), _need(body, "description"))
    return 201, report


def _add_revision(service, actor, role, body, contract_id):
    revision = service.register_contract_revision(
        actor,
        role,
        contract_id,
        _need(body, "obligations"),
        parse_moment(_need(body, "effective_from")),
        body.get("revision"),
    )
    return 201, revision


def _add_metric(service, actor, role, body, asset_code):
    metric = service.record_metric(
        actor,
        role,
        asset_code,
        _need(body, "metric_code"),
        _need(body, "value"),
        parse_moment(body["recorded_at"]) if body.get("recorded_at") else None,
    )
    return 201, metric


def _record_incident(service, actor, role, body):
    incident = service.record_incident(actor, role, _need(body, "asset_code"), parse_moment(_need(body, "occurred_at")), _need(body, "description"))
    return 201, incident


_ROUTES = [
    ("GET", re.compile(r"^/health$"), _health),
    ("GET", re.compile(r"^/packages$"), _list_packages),
    ("GET", re.compile(r"^/packages/(?P<package_id>[^/]+)$"), _get_package),
    ("GET", re.compile(r"^/packages/(?P<package_id>[^/]+)/unmet-conditions$"), _unmet_conditions),
    ("GET", re.compile(r"^/packages/(?P<package_id>[^/]+)/decision-trail$"), _decision_trail),
    ("GET", re.compile(r"^/packages/(?P<package_id>[^/]+)/deductions$"), _deductions),
    ("GET", re.compile(r"^/assets/(?P<asset_code>[^/]+)/responsibility$"), _responsibility),
    ("GET", re.compile(r"^/incidents/(?P<incident_id>[^/]+)/obligations$"), _incident_obligations),
    ("POST", re.compile(r"^/packages$"), _create_package),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/defects$"), _add_defect),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/confirm-documents$"), _confirm_documents),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/accept-with-conditions$"), _accept_with_conditions),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/confirm-takeover$"), _confirm_takeover),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/return$"), _return_package),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/process-expired$"), _process_expired),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/remedies/(?P<remedy_id>[^/]+)/confirm-completed$"), _confirm_remedy),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/remedies/(?P<remedy_id>[^/]+)/waive$"), _waive_remedy),
    ("POST", re.compile(r"^/packages/(?P<package_id>[^/]+)/defects/(?P<defect_id>[^/]+)/waive$"), _waive_defect),
    ("POST", re.compile(r"^/assets/(?P<asset_code>[^/]+)/transfer$"), _transfer),
    ("POST", re.compile(r"^/assets/(?P<asset_code>[^/]+)/repairs$"), _report_repair),
    ("POST", re.compile(r"^/assets/(?P<asset_code>[^/]+)/metrics$"), _add_metric),
    ("POST", re.compile(r"^/contracts/(?P<contract_id>[^/]+)/revisions$"), _add_revision),
    ("POST", re.compile(r"^/incidents$"), _record_incident),
]


def make_server(service: HandoverService, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    """创建挂接给定服务的 HTTP 服务。"""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload) -> None:
            body = json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            path = urlparse(self.path).path
            for route_method, pattern, handler in _ROUTES:
                if route_method != method:
                    continue
                match = pattern.match(path)
                if match is None:
                    continue
                try:
                    body = {}
                    if method == "POST":
                        length = int(self.headers.get("Content-Length") or 0)
                        if length:
                            body = json.loads(self.rfile.read(length).decode("utf-8"))
                    actor = self.headers.get("X-Actor", "api-user")
                    role = Role(self.headers.get("X-Role", Role.OWNER.value))
                    status, payload = handler(service, actor, role, body, **match.groupdict())
                    self._send(status, payload)
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
                except (DomainError, ValueError, KeyError) as exc:
                    self._send(400, {"error": str(exc)})
                return
            self._send(404, {"error": f"路径不存在: {method} {path}"})

        def log_message(self, *args) -> None:  # 静默访问日志
            return

    return ThreadingHTTPServer((host, port), Handler)
