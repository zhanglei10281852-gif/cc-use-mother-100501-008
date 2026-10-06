"""基础设施运营责任交接命令行。

用法示例：
    python -m asset_handover.cli --state store.json --actor 张三 --role OWNER create-package \
        --asset-code pump-001 --asset-version v1.0 --contract-id contract-001

状态保存在 JSON 文件中（--state 或环境变量 HANDOVER_STATE），
查询类命令只读，操作类命令执行后自动保存。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .domain import AssetVersion, Role, now_utc
from .serialization import parse_moment, service_from_dict, service_to_dict, to_jsonable
from .service import HandoverService

_MUTATING = {
    "create-package",
    "add-defect",
    "confirm-documents",
    "accept-conditional",
    "confirm-takeover",
    "return-package",
    "confirm-remedy",
    "waive-remedy",
    "waive-defect",
    "process-expired",
    "transfer",
    "report-repair",
    "close-repair",
    "add-revision",
    "add-metric",
    "record-incident",
}


def _load(path: str) -> HandoverService:
    file = Path(path)
    if file.exists():
        return service_from_dict(json.loads(file.read_text(encoding="utf-8")))
    return HandoverService()


def _save(path: str, service: HandoverService) -> None:
    Path(path).write_text(json.dumps(service_to_dict(service), ensure_ascii=False, indent=2), encoding="utf-8")


def _emit(payload) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True))


def _remedy_specs(text: str) -> list[dict]:
    specs = json.loads(text)
    if not isinstance(specs, list):
        raise ValueError("--remedies 必须是 JSON 数组")
    for spec in specs:
        if isinstance(spec.get("deadline"), str):
            spec["deadline"] = parse_moment(spec["deadline"])
    return specs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asset-handover", description="基础设施运营责任交接命令行")
    parser.add_argument("--state", default=os.environ.get("HANDOVER_STATE", "handover_state.json"), help="状态文件路径")
    parser.add_argument("--actor", default="cli-user", help="操作人")
    parser.add_argument("--role", default=Role.OWNER.value, choices=[item.value for item in Role], help="操作角色")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-package", help="建立交接包")
    create.add_argument("--asset-code", required=True)
    create.add_argument("--asset-version", required=True)
    create.add_argument("--contract-id", required=True)
    create.add_argument("--package-id")
    create.add_argument("--builder-party", default="建设单位")
    create.add_argument("--operator-party", default="运营企业")
    create.add_argument("--installed-at", help="ISO 时间，默认当前时间")
    create.add_argument("--defects", help="JSON 数组，如 [{\"description\": \"阀门渗漏\"}]")

    defect = sub.add_parser("add-defect", help="登记未决缺陷")
    defect.add_argument("--package", required=True)
    defect.add_argument("--description", required=True)
    defect.add_argument("--severity", default="一般")

    confirm_docs = sub.add_parser("confirm-documents", help="资料核验确认")
    confirm_docs.add_argument("--package", required=True)

    conditional = sub.add_parser("accept-conditional", help="附条件接收，形成有期限补救项")
    conditional.add_argument("--package", required=True)
    conditional.add_argument("--remedies", required=True, help="JSON 数组，每项含 defect_id/deadline/on_expiry 等")

    takeover = sub.add_parser("confirm-takeover", help="正式接管确认")
    takeover.add_argument("--package", required=True)

    send_back = sub.add_parser("return-package", help="退回建设单位")
    send_back.add_argument("--package", required=True)
    send_back.add_argument("--reason", required=True)

    remedy_done = sub.add_parser("confirm-remedy", help="确认补救项整改完成")
    remedy_done.add_argument("--package", required=True)
    remedy_done.add_argument("--remedy", required=True)
    remedy_done.add_argument("--note", default="")

    remedy_waive = sub.add_parser("waive-remedy", help="业主豁免补救项")
    remedy_waive.add_argument("--package", required=True)
    remedy_waive.add_argument("--remedy", required=True)
    remedy_waive.add_argument("--reason", required=True)

    defect_waive = sub.add_parser("waive-defect", help="业主豁免缺陷")
    defect_waive.add_argument("--package", required=True)
    defect_waive.add_argument("--defect", required=True)
    defect_waive.add_argument("--reason", required=True)

    expired = sub.add_parser("process-expired", help="处置到期补救项：升级、扣减或退回")
    expired.add_argument("--package")

    transfer = sub.add_parser("transfer", help="运营主体变更或义务转让")
    transfer.add_argument("--asset", required=True)
    transfer.add_argument("--new-party", required=True)
    transfer.add_argument("--reason", required=True)
    transfer.add_argument("--obligation", help="义务编号（义务转让时填写）")

    repair = sub.add_parser("report-repair", help="报修（同一问题自动归并）")
    repair.add_argument("--asset", required=True)
    repair.add_argument("--problem-key", required=True)
    repair.add_argument("--description", required=True)

    close_repair = sub.add_parser("close-repair", help="关闭报修单")
    close_repair.add_argument("--report", required=True)

    revision = sub.add_parser("add-revision", help="登记合同新版本")
    revision.add_argument("--contract", required=True)
    revision.add_argument("--effective-from", required=True, help="ISO 时间")
    revision.add_argument("--obligations", required=True, help='JSON 数组，如 [{"code": "SLA-1", "title": "故障响应", "service_level": "<=4h"}]')
    revision.add_argument("--revision", type=int)

    metric = sub.add_parser("add-metric", help="记录服务指标（绑定当时合同版本）")
    metric.add_argument("--asset", required=True)
    metric.add_argument("--metric", required=True)
    metric.add_argument("--value", required=True, type=float)
    metric.add_argument("--recorded-at", help="ISO 时间，默认当前时间")

    incident = sub.add_parser("record-incident", help="登记事故")
    incident.add_argument("--asset", required=True)
    incident.add_argument("--occurred-at", required=True, help="ISO 时间")
    incident.add_argument("--description", required=True)

    responsibility = sub.add_parser("responsibility", help="查询资产当前责任主体与责任链")
    responsibility.add_argument("--asset", required=True)

    unmet = sub.add_parser("unmet-conditions", help="查询尚未满足的交接条件")
    unmet.add_argument("--package", required=True)

    trail = sub.add_parser("decision-trail", help="查询接管决定的形成过程")
    trail.add_argument("--package", required=True)

    obligations = sub.add_parser("incident-obligations", help="查询事故应引用哪版合同义务")
    obligations.add_argument("--incident", required=True)

    show = sub.add_parser("package", help="查看交接包")
    show.add_argument("--package", required=True)

    sub.add_parser("packages", help="列出全部交接包")

    deductions = sub.add_parser("deductions", help="查询扣减记录")
    deductions.add_argument("--package")

    serve = sub.add_parser("serve", help="启动 HTTP API 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    return parser


def _run(service: HandoverService, args: argparse.Namespace):
    actor, role = args.actor, Role(args.role)
    command = args.command
    if command == "create-package":
        asset = AssetVersion(
            asset_code=args.asset_code,
            version=args.asset_version,
            installed_at=parse_moment(args.installed_at) if args.installed_at else now_utc(),
        )
        return service.create_package(
            actor,
            role,
            asset=asset,
            contract_id=args.contract_id,
            package_id=args.package_id,
            builder_party=args.builder_party,
            operator_party=args.operator_party,
            defects=json.loads(args.defects) if args.defects else [],
        )
    if command == "add-defect":
        return service.add_defect(actor, role, args.package, args.description, args.severity)
    if command == "confirm-documents":
        return service.confirm_document_review(actor, role, args.package)
    if command == "accept-conditional":
        return service.accept_with_conditions(actor, role, args.package, _remedy_specs(args.remedies))
    if command == "confirm-takeover":
        return service.confirm_takeover(actor, role, args.package)
    if command == "return-package":
        return service.return_package(actor, role, args.package, args.reason)
    if command == "confirm-remedy":
        return service.confirm_remedy_completed(actor, role, args.package, args.remedy, args.note)
    if command == "waive-remedy":
        return service.waive_remedy(actor, role, args.package, args.remedy, args.reason)
    if command == "waive-defect":
        return service.waive_defect(actor, role, args.package, args.defect, args.reason)
    if command == "process-expired":
        return {"processed": service.process_expired_remedies(actor, role, args.package)}
    if command == "transfer":
        return service.transfer_responsibility(actor, role, args.asset, args.new_party, args.reason, args.obligation)
    if command == "report-repair":
        return service.report_repair(actor, role, args.asset, args.problem_key, args.description)
    if command == "close-repair":
        return service.close_repair(actor, role, args.report)
    if command == "add-revision":
        return service.register_contract_revision(
            actor, role, args.contract, json.loads(args.obligations), parse_moment(args.effective_from), args.revision
        )
    if command == "add-metric":
        return service.record_metric(actor, role, args.asset, args.metric, args.value, parse_moment(args.recorded_at) if args.recorded_at else None)
    if command == "record-incident":
        return service.record_incident(actor, role, args.asset, parse_moment(args.occurred_at), args.description)
    if command == "responsibility":
        return service.asset_responsibility(args.asset)
    if command == "unmet-conditions":
        return service.unmet_conditions(args.package)
    if command == "decision-trail":
        return service.takeover_decision_trail(args.package)
    if command == "incident-obligations":
        return service.obligations_for_incident(args.incident)
    if command == "package":
        return service.get_package(args.package)
    if command == "packages":
        return service.list_packages()
    if command == "deductions":
        return service.list_deductions(args.package)
    raise ValueError(f"未知命令 {command}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    service = _load(args.state)
    if args.command == "serve":
        from .api import make_server

        server = make_server(service, args.host, args.port)
        print(f"API 服务已启动: http://{args.host}:{args.port}（状态文件 {args.state}）")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            _save(args.state, service)
        return 0
    try:
        result = _run(service, args)
    except (PermissionError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if args.command in _MUTATING:
        _save(args.state, service)
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
