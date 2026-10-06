"""基础设施运营责任交接命令行入口。

所有变更类与查询类操作都通过子命令暴露，输出 JSON；
``--store`` 指定 JSON 存储文件以便跨命令共享状态。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from .domain import (
    Actor,
    AssetVersion,
    EmergencyContact,
    HandoverPackage,
    MaintenancePlan,
    MaintenanceTask,
    Obligation,
    ObligationType,
    OverduePolicy,
    RemediationItem,
    Role,
    WarrantyBoundary,
    parse_datetime,
)
from .errors import DomainError
from .serde import from_jsonable, to_jsonable
from .services import HandoverService
from .store import JsonFileStore


def _emit(payload: Any) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True))


def _load_json_arg(raw: str) -> Any:
    if raw == "-":
        return json.load(sys.stdin)
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
    return json.loads(raw)


def _actor(args: argparse.Namespace) -> Actor:
    roles = tuple(Role(item) for item in (args.role or [Role.OWNER_REP.value]))
    return Actor(actor_id=args.actor_id, name=args.actor_name, roles=roles)


def _now(args: argparse.Namespace) -> Any:
    return parse_datetime(args.now) if args.now else None


def _service(args: argparse.Namespace) -> HandoverService:
    return HandoverService(JsonFileStore(args.store))


# ---------------------------------------------------------------------------
# 变更类命令
# ---------------------------------------------------------------------------
def _cmd_register_contract(args: argparse.Namespace) -> Any:
    return _service(args).register_contract(_actor(args), args.contract_code, args.title, now=_now(args))


def _cmd_register_revision(args: argparse.Namespace) -> Any:
    obligations = from_jsonable(list[Obligation], _load_json_arg(args.obligations))
    return _service(args).register_revision(
        _actor(args), args.contract_code, args.revision, parse_datetime(args.effective_from), obligations, now=_now(args)
    )


def _cmd_create_package(args: argparse.Namespace) -> Any:
    package = from_jsonable(HandoverPackage, _load_json_arg(args.data))
    return _service(args).create_package(_actor(args), package, now=_now(args))


def _cmd_submit(args: argparse.Namespace) -> Any:
    return _service(args).submit_package(_actor(args), args.package, now=_now(args))


def _cmd_confirm_documents(args: argparse.Namespace) -> Any:
    return _service(args).confirm_documents(_actor(args), args.package, note=args.note, now=_now(args))


def _cmd_add_defect(args: argparse.Namespace) -> Any:
    return _service(args).record_defect(
        _actor(args), args.package, args.defect_code, args.description, args.severity, now=_now(args)
    )


def _cmd_accept_conditional(args: argparse.Namespace) -> Any:
    conditions = from_jsonable(list[RemediationItem], _load_json_arg(args.conditions))
    return _service(args).accept_conditionally(_actor(args), args.package, conditions, now=_now(args))


def _cmd_complete_remediation(args: argparse.Namespace) -> Any:
    return _service(args).complete_remediation(
        _actor(args), args.package, args.item, note=args.note, now=_now(args)
    )


def _cmd_waive_remediation(args: argparse.Namespace) -> Any:
    return _service(args).waive_remediation(_actor(args), args.package, args.item, args.reason, now=_now(args))


def _cmd_reschedule_remediation(args: argparse.Namespace) -> Any:
    return _service(args).reschedule_remediation(
        _actor(args), args.package, args.item, parse_datetime(args.deadline), now=_now(args)
    )


def _cmd_resolve_defect(args: argparse.Namespace) -> Any:
    return _service(args).resolve_defect(_actor(args), args.package, args.defect_code, note=args.note, now=_now(args))


def _cmd_waive_defect(args: argparse.Namespace) -> Any:
    return _service(args).waive_defect(_actor(args), args.package, args.defect_code, args.reason, now=_now(args))


def _cmd_take_over(args: argparse.Namespace) -> Any:
    return _service(args).take_over(_actor(args), args.package, now=_now(args))


def _cmd_process_overdue(args: argparse.Namespace) -> Any:
    return _service(args).process_overdue(_actor(args), args.package, now=_now(args))


def _cmd_change_operator(args: argparse.Namespace) -> Any:
    return _service(args).change_operator(
        _actor(args), args.asset, args.party, parse_datetime(args.effective_from), note=args.note, now=_now(args)
    )


def _cmd_transfer_obligation(args: argparse.Namespace) -> Any:
    return _service(args).transfer_obligation(
        _actor(args), args.obligation, args.to_party, parse_datetime(args.effective_from), note=args.note, now=_now(args)
    )


def _cmd_report_issue(args: argparse.Namespace) -> Any:
    return _service(args).report_issue(
        _actor(args), args.asset, args.signature, parse_datetime(args.occurred_at), args.description, now=_now(args)
    )


def _cmd_close_issue(args: argparse.Namespace) -> Any:
    return _service(args).close_issue(_actor(args), args.issue, note=args.note, now=_now(args))


def _cmd_record_metric(args: argparse.Namespace) -> Any:
    return _service(args).record_metric(
        _actor(args), args.contract, args.indicator, args.value, args.unit,
        parse_datetime(args.recorded_at), now=_now(args),
    )


# ---------------------------------------------------------------------------
# 查询类命令
# ---------------------------------------------------------------------------
def _cmd_package_status(args: argparse.Namespace) -> Any:
    return _service(args).package_status(args.package, now=_now(args))


def _cmd_pending_conditions(args: argparse.Namespace) -> Any:
    return _service(args).pending_conditions(args.package, now=_now(args))


def _cmd_asset_responsibility(args: argparse.Namespace) -> Any:
    at = parse_datetime(args.at) if args.at else None
    return _service(args).asset_responsibility(args.asset, at=at)


def _cmd_obligation_holder(args: argparse.Namespace) -> Any:
    at = parse_datetime(args.at) if args.at else None
    return _service(args).obligation_holder(args.obligation, at=at)


def _cmd_incident_context(args: argparse.Namespace) -> Any:
    return _service(args).incident_context(args.issue, report_code=args.report)


def _cmd_applicable_obligations(args: argparse.Namespace) -> Any:
    return _service(args).applicable_obligations(args.contract, parse_datetime(args.at))


def _cmd_metrics(args: argparse.Namespace) -> Any:
    return _service(args).metrics_for(args.contract, revision=args.revision)


def _cmd_takeover_trail(args: argparse.Namespace) -> Any:
    return _service(args).takeover_trail(args.package)


# ---------------------------------------------------------------------------
# 端到端演示
# ---------------------------------------------------------------------------
def _t(text: str):
    return parse_datetime(text)


def _cmd_demo(args: argparse.Namespace) -> Any:
    service = _service(args)
    if service.store.packages or service.store.contracts:
        raise DomainError("演示需要空存储，请为 --store 指定一个新文件")
    owner = Actor("owner-01", "业主代表", (Role.OWNER_REP,))
    lead = Actor("lead-01", "验收组长", (Role.ACCEPTANCE_LEAD,))
    operator = Actor("op-01", "运营企业代表", (Role.OPERATOR_REP,))
    steps: list[dict[str, Any]] = []

    def record(step: str, result: Any) -> None:
        steps.append({"step": step, "result": result})

    service.register_contract(owner, "HT-2026-001", "城东雨水泵站运营合同", now=_t("2026-01-01T09:00:00+00:00"))
    contract = service.register_revision(
        owner, "HT-2026-001", "R1", _t("2026-01-01T00:00:00+00:00"),
        [
            Obligation("OB-01", "HT-2026-001", ObligationType.DEFECT_RECTIFICATION, "保修期内缺陷由施工单位修复", "城建集团"),
            Obligation("OB-02", "HT-2026-001", ObligationType.SPARE_PARTS, "移交水泵备件两套", "城建集团"),
            Obligation("OB-03", "HT-2026-001", ObligationType.SERVICE_LEVEL, "泵站可用率不低于 99.9%", "城投水务", indicator="可用率", target="99.9", unit="%"),
        ],
        now=_t("2026-01-01T09:05:00+00:00"),
    )
    record("登记合同与义务基线", {"contract_code": contract.contract_code, "revisions": [r.revision for r in contract.revisions]})

    package = HandoverPackage(
        package_code="PKG-001",
        project_code="PRJ-CD-001",
        asset=AssetVersion("PS-001", "1.0", "城东雨水泵站", "排水设施", "城东路 18 号"),
        contract_code="HT-2026-001",
        constructor_party="城建集团",
        operator_party="城投水务",
        warranties=(WarrantyBoundary("WR-01", "主体结构及水泵机组", "城建集团", _t("2026-01-01").date(), _t("2028-01-01").date()),),
        maintenance_plan=MaintenancePlan("MP-01", "城投水务", (MaintenanceTask("MT-01", "水泵巡检", 30, "按巡检表执行"),)),
        contacts=(EmergencyContact("张工", "应急值班", "13800000000", 1),),
    )
    service.create_package(owner, package, now=_t("2026-01-10T09:00:00+00:00"))
    package = service.submit_package(owner, "PKG-001", now=_t("2026-01-10T10:00:00+00:00"))
    record("提交资料核验", {"state": package.state.value, "baseline_revision": package.baseline_revision})
    package = service.confirm_documents(lead, "PKG-001", note="资料齐全", now=_t("2026-01-15T09:00:00+00:00"))
    package = service.record_defect(lead, "PKG-001", "DF-01", "闸门渗漏", "严重", now=_t("2026-01-20T09:00:00+00:00"))
    package = service.record_defect(lead, "PKG-001", "DF-02", "备件未随船交付", "一般", now=_t("2026-01-20T09:30:00+00:00"))
    package = service.accept_conditionally(
        lead, "PKG-001",
        [
            RemediationItem("RM-01", "修复闸门渗漏并通过灌水试验", _t("2026-03-01T00:00:00+00:00"), OverduePolicy.DEDUCT, "20000", ("DF-01",)),
            RemediationItem("RM-02", "补齐两套水泵备件", _t("2026-02-10T00:00:00+00:00"), OverduePolicy.ESCALATE, "", ("DF-02",)),
        ],
        now=_t("2026-01-25T09:00:00+00:00"),
    )
    record("附条件接收并形成补救项", {"state": package.state.value, "items": [i.item_code for i in package.remediations]})

    issue = service.report_issue(operator, "PS-001", "gate-leak", _t("2026-02-01T08:00:00+00:00"), "闸门渗漏导致泵坑积水", now=_t("2026-02-01T08:30:00+00:00"))
    issue = service.report_issue(operator, "PS-001", "gate-leak", _t("2026-02-03T08:00:00+00:00"), "同一渗漏点再次积水", now=_t("2026-02-03T08:30:00+00:00"))
    record("重复报修归集", {"issue_code": issue.issue_code, "reports": len(issue.reports)})

    metric = service.record_metric(operator, "HT-2026-001", "可用率", 99.95, "%", _t("2026-02-15T00:00:00+00:00"), now=_t("2026-02-16T09:00:00+00:00"))
    record("服务指标绑定合同版本", {"indicator": metric.indicator, "revision": metric.revision})

    actions = service.process_overdue(owner, "PKG-001", now=_t("2026-03-05T09:00:00+00:00"))
    record("逾期处置（扣减 + 升级）", actions)
    package = service.reschedule_remediation(lead, "PKG-001", "RM-02", _t("2026-03-20T00:00:00+00:00"), now=_t("2026-03-06T09:00:00+00:00"))
    package = service.complete_remediation(lead, "PKG-001", "RM-01", note="灌水试验合格", now=_t("2026-03-10T09:00:00+00:00"))
    package = service.complete_remediation(lead, "PKG-001", "RM-02", note="备件到库", now=_t("2026-03-15T09:00:00+00:00"))
    package = service.take_over(owner, "PKG-001", now=_t("2026-03-16T09:00:00+00:00"))
    record("正式接管", {"state": package.state.value})

    service.change_operator(owner, "PS-001", "第二运营公司", _t("2026-06-01T00:00:00+00:00"), note="年度招标轮换", now=_t("2026-05-20T09:00:00+00:00"))
    service.transfer_obligation(owner, "OB-02", "城投水务", _t("2026-04-01T00:00:00+00:00"), note="备件管理随运营移交", now=_t("2026-03-25T09:00:00+00:00"))

    record("当前责任主体", service.asset_responsibility("PS-001", at=_t("2026-06-15T00:00:00+00:00"))["party"])
    record("事故应引用的义务版本", {
        "issue_code": issue.issue_code,
        "responsible_party": service.incident_context(issue.issue_code)["responsible_party"],
        "revision": service.incident_context(issue.issue_code)["revision"],
    })
    record("接管决定轨迹", [f"{event.action}@{event.at.date().isoformat()} by {event.actor_id}" for event in service.takeover_trail("PKG-001")])
    return steps


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asset-handover", description="基础设施运营责任交接服务命令行")
    parser.add_argument("--store", default="handover_store.json", help="JSON 存储文件路径")
    parser.add_argument("--actor-id", default="cli-user", help="操作主体标识")
    parser.add_argument("--actor-name", default="命令行用户", help="操作主体名称")
    parser.add_argument("--role", action="append", choices=[role.value for role in Role],
                        help="操作主体角色，可重复；缺省为 OWNER_REP")
    parser.add_argument("--now", default=None, help="覆盖当前时间（ISO 格式），便于演示与对账")
    sub = parser.add_subparsers(dest="command")

    def add(name: str, handler: Any, help_text: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text)
        child.set_defaults(handler=handler)
        return child

    add("demo", _cmd_demo, "运行端到端演示场景")
    contract = add("register-contract", _cmd_register_contract, "登记合同")
    contract.add_argument("--contract-code", required=True)
    contract.add_argument("--title", required=True)

    revision = add("register-revision", _cmd_register_revision, "登记合同新版本")
    revision.add_argument("--contract-code", required=True)
    revision.add_argument("--revision", required=True)
    revision.add_argument("--effective-from", required=True)
    revision.add_argument("--obligations", required=True, help="义务条目 JSON（数组、@文件 或 - 读标准输入）")

    add("create-package", _cmd_create_package, "登记交接包").add_argument(
        "--data", required=True, help="交接包 JSON（@文件 或 - 读标准输入）")
    add("submit", _cmd_submit, "提交资料核验").add_argument("--package", required=True)
    confirm = add("confirm-documents", _cmd_confirm_documents, "资料核验通过，进入现场验收")
    confirm.add_argument("--package", required=True)
    confirm.add_argument("--note", default="")

    defect = add("add-defect", _cmd_add_defect, "登记缺陷")
    defect.add_argument("--package", required=True)
    defect.add_argument("--defect-code", required=True)
    defect.add_argument("--description", required=True)
    defect.add_argument("--severity", required=True)

    accept = add("accept-conditional", _cmd_accept_conditional, "附条件接收并形成补救项")
    accept.add_argument("--package", required=True)
    accept.add_argument("--conditions", required=True, help="补救项 JSON（数组、@文件 或 - 读标准输入）")

    complete = add("complete-remediation", _cmd_complete_remediation, "确认补救项完成")
    complete.add_argument("--package", required=True)
    complete.add_argument("--item", required=True)
    complete.add_argument("--note", default="")

    waive_item = add("waive-remediation", _cmd_waive_remediation, "豁免补救项（仅业主代表）")
    waive_item.add_argument("--package", required=True)
    waive_item.add_argument("--item", required=True)
    waive_item.add_argument("--reason", required=True)

    reschedule = add("reschedule-remediation", _cmd_reschedule_remediation, "为已升级补救项重新约定期限")
    reschedule.add_argument("--package", required=True)
    reschedule.add_argument("--item", required=True)
    reschedule.add_argument("--deadline", required=True)

    resolve = add("resolve-defect", _cmd_resolve_defect, "确认缺陷整改完成")
    resolve.add_argument("--package", required=True)
    resolve.add_argument("--defect-code", required=True)
    resolve.add_argument("--note", default="")

    waive_defect = add("waive-defect", _cmd_waive_defect, "豁免缺陷（仅业主代表）")
    waive_defect.add_argument("--package", required=True)
    waive_defect.add_argument("--defect-code", required=True)
    waive_defect.add_argument("--reason", required=True)

    add("take-over", _cmd_take_over, "正式接管").add_argument("--package", required=True)
    add("process-overdue", _cmd_process_overdue, "处置到期未完成的补救项").add_argument("--package", required=True)

    change = add("change-operator", _cmd_change_operator, "运营主体变更")
    change.add_argument("--asset", required=True)
    change.add_argument("--party", required=True)
    change.add_argument("--effective-from", required=True)
    change.add_argument("--note", default="")

    transfer = add("transfer-obligation", _cmd_transfer_obligation, "义务转让")
    transfer.add_argument("--obligation", required=True)
    transfer.add_argument("--to-party", required=True)
    transfer.add_argument("--effective-from", required=True)
    transfer.add_argument("--note", default="")

    report = add("report-issue", _cmd_report_issue, "事故报修（同一问题自动归集）")
    report.add_argument("--asset", required=True)
    report.add_argument("--signature", required=True, help="问题特征标识，用于重复报修归集")
    report.add_argument("--occurred-at", required=True)
    report.add_argument("--description", required=True)

    close = add("close-issue", _cmd_close_issue, "关闭事故单")
    close.add_argument("--issue", required=True)
    close.add_argument("--note", default="")

    metric = add("record-metric", _cmd_record_metric, "记录服务指标")
    metric.add_argument("--contract", required=True)
    metric.add_argument("--indicator", required=True)
    metric.add_argument("--value", required=True, type=float)
    metric.add_argument("--unit", default="")
    metric.add_argument("--recorded-at", required=True)

    add("package-status", _cmd_package_status, "交接包总览").add_argument("--package", required=True)
    add("pending-conditions", _cmd_pending_conditions, "查看尚未满足的条件").add_argument("--package", required=True)

    responsibility = add("asset-responsibility", _cmd_asset_responsibility, "查看资产责任主体")
    responsibility.add_argument("--asset", required=True)
    responsibility.add_argument("--at", default=None)

    holder = add("obligation-holder", _cmd_obligation_holder, "查看义务承担方")
    holder.add_argument("--obligation", required=True)
    holder.add_argument("--at", default=None)

    incident = add("incident-context", _cmd_incident_context, "查看事故应引用的义务版本")
    incident.add_argument("--issue", required=True)
    incident.add_argument("--report", default=None)

    applicable = add("applicable-obligations", _cmd_applicable_obligations, "查看指定时刻生效的义务")
    applicable.add_argument("--contract", required=True)
    applicable.add_argument("--at", required=True)

    metrics = add("metrics", _cmd_metrics, "查看服务指标记录")
    metrics.add_argument("--contract", required=True)
    metrics.add_argument("--revision", default=None)

    add("takeover-trail", _cmd_takeover_trail, "查看接管决定轨迹").add_argument("--package", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    try:
        result = handler(args)
    except DomainError as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "type": "ValidationError"}, ensure_ascii=False), file=sys.stderr)
        return 2
    if result is not None:
        _emit(result)
    return 0
