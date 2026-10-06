"""领域对象的 JSON 序列化与还原，供命令行持久化与 API 输出使用。"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from .domain import (
    AssetVersion,
    AuditEvent,
    ContractRevision,
    Deduction,
    Defect,
    DefectStatus,
    EmergencyContact,
    ExpiryAction,
    HandoverPackage,
    Incident,
    MaintenancePlan,
    Obligation,
    PackageState,
    RemedyItem,
    RemedyStatus,
    RepairReport,
    RepairStatus,
    ResponsibilityLink,
    ServiceMetric,
    WarrantyBoundary,
    now_utc,
)


def to_jsonable(value: Any) -> Any:
    """把领域对象递归转换为可 JSON 序列化的结构。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def parse_moment(text: str) -> datetime:
    """解析 ISO 时间字符串，缺省时区按 UTC 处理。"""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _optional_moment(text: Optional[str]) -> Optional[datetime]:
    return parse_moment(text) if text else None


def asset_version_from(data: dict) -> AssetVersion:
    return AssetVersion(
        asset_code=data["asset_code"],
        version=data["version"],
        installed_at=parse_moment(data["installed_at"]),
        notes=data.get("notes", ""),
    )


def obligation_from(data: dict) -> Obligation:
    return Obligation(code=data["code"], title=data["title"], service_level=data.get("service_level", ""))


def revision_from(data: dict) -> ContractRevision:
    return ContractRevision(
        contract_id=data["contract_id"],
        revision=int(data["revision"]),
        obligations=tuple(obligation_from(item) for item in data.get("obligations", [])),
        effective_from=parse_moment(data["effective_from"]),
        effective_to=_optional_moment(data.get("effective_to")),
    )


def warranty_from(data: dict) -> WarrantyBoundary:
    return WarrantyBoundary(
        scope=data["scope"],
        start=parse_moment(data["start"]),
        end=parse_moment(data["end"]),
        covers=data.get("covers", ""),
    )


def plan_from(data: dict) -> MaintenancePlan:
    return MaintenancePlan(item=data["item"], frequency=data["frequency"], next_due=parse_moment(data["next_due"]))


def contact_from(data: dict) -> EmergencyContact:
    return EmergencyContact(name=data["name"], role=data["role"], phone=data["phone"], priority=int(data.get("priority", 1)))


def defect_from(data: dict) -> Defect:
    return Defect(
        defect_id=data["defect_id"],
        description=data["description"],
        severity=data.get("severity", "一般"),
        raised_at=parse_moment(data["raised_at"]),
        status=DefectStatus(data["status"]),
        closed_reason=data.get("closed_reason", ""),
    )


def remedy_from(data: dict) -> RemedyItem:
    return RemedyItem(
        remedy_id=data["remedy_id"],
        defect_id=data["defect_id"],
        description=data["description"],
        deadline=parse_moment(data["deadline"]),
        on_expiry=ExpiryAction(data["on_expiry"]),
        escalation_extension_days=int(data.get("escalation_extension_days", 0)),
        deduction_amount=float(data.get("deduction_amount", 0.0)),
        status=RemedyStatus(data["status"]),
        escalation_level=int(data.get("escalation_level", 0)),
        history=tuple(data.get("history", [])),
    )


def package_from(data: dict) -> HandoverPackage:
    return HandoverPackage(
        package_id=data["package_id"],
        asset=asset_version_from(data["asset"]),
        contract_id=data["contract_id"],
        builder_party=data["builder_party"],
        operator_party=data["operator_party"],
        state=PackageState(data["state"]),
        warranty=warranty_from(data["warranty"]) if data.get("warranty") else None,
        maintenance_plans=tuple(plan_from(item) for item in data.get("maintenance_plans", [])),
        emergency_contacts=tuple(contact_from(item) for item in data.get("emergency_contacts", [])),
        defects=tuple(defect_from(item) for item in data.get("defects", [])),
        remedies=tuple(remedy_from(item) for item in data.get("remedies", [])),
        created_at=parse_moment(data["created_at"]),
    )


def link_from(data: dict) -> ResponsibilityLink:
    return ResponsibilityLink(
        sequence=int(data["sequence"]),
        asset_code=data["asset_code"],
        party=data["party"],
        reason=data["reason"],
        effective_at=parse_moment(data["effective_at"]),
        ended_at=_optional_moment(data.get("ended_at")),
    )


def report_from(data: dict) -> RepairReport:
    return RepairReport(
        report_id=data["report_id"],
        asset_code=data["asset_code"],
        problem_key=data["problem_key"],
        description=data["description"],
        reported_at=parse_moment(data["reported_at"]),
        reported_by=data["reported_by"],
        chain_sequence=int(data["chain_sequence"]),
        contract_id=data.get("contract_id"),
        revision=data.get("revision"),
        status=RepairStatus(data["status"]),
        repeat_count=int(data.get("repeat_count", 0)),
    )


def metric_from(data: dict) -> ServiceMetric:
    return ServiceMetric(
        asset_code=data["asset_code"],
        metric_code=data["metric_code"],
        value=float(data["value"]),
        recorded_at=parse_moment(data["recorded_at"]),
        contract_id=data["contract_id"],
        revision=int(data["revision"]),
    )


def incident_from(data: dict) -> Incident:
    return Incident(
        incident_id=data["incident_id"],
        asset_code=data["asset_code"],
        occurred_at=parse_moment(data["occurred_at"]),
        description=data["description"],
    )


def deduction_from(data: dict) -> Deduction:
    return Deduction(
        remedy_id=data["remedy_id"],
        package_id=data["package_id"],
        amount=float(data["amount"]),
        reason=data["reason"],
        at=parse_moment(data["at"]),
    )


def audit_from(data: dict) -> AuditEvent:
    return AuditEvent(
        seq=int(data["seq"]),
        at=parse_moment(data["at"]),
        actor=data["actor"],
        role=data["role"],
        action=data["action"],
        target=data["target"],
        detail=dict(data.get("detail", {})),
    )


def service_to_dict(service: Any) -> dict:
    """导出服务全量状态，用于命令行持久化。"""
    return {
        "seq": service._seq,
        "packages": [to_jsonable(item) for item in service._packages.values()],
        "chains": {key: [to_jsonable(item) for item in links] for key, links in service._chains.items()},
        "revisions": {key: [to_jsonable(item) for item in revisions] for key, revisions in service._revisions.items()},
        "reports": [to_jsonable(item) for item in service._reports.values()],
        "metrics": [to_jsonable(item) for item in service._metrics],
        "incidents": [to_jsonable(item) for item in service._incidents.values()],
        "deductions": [to_jsonable(item) for item in service._deductions],
        "audit": [to_jsonable(item) for item in service._audit_events],
    }


def service_from_dict(data: dict, clock: Callable[[], datetime] = now_utc) -> Any:
    """从快照还原服务状态。"""
    from .service import HandoverService

    service = HandoverService(clock=clock)
    service._seq = int(data.get("seq", 0))
    for item in data.get("packages", []):
        package = package_from(item)
        service._packages[package.package_id] = package
    for asset_code, links in data.get("chains", {}).items():
        service._chains[asset_code] = [link_from(item) for item in links]
    for contract_id, revisions in data.get("revisions", {}).items():
        service._revisions[contract_id] = [revision_from(item) for item in revisions]
    for item in data.get("reports", []):
        report = report_from(item)
        service._reports[report.report_id] = report
    service._metrics = [metric_from(item) for item in data.get("metrics", [])]
    for item in data.get("incidents", []):
        incident = incident_from(item)
        service._incidents[incident.incident_id] = incident
    service._deductions = [deduction_from(item) for item in data.get("deductions", [])]
    service._audit_events = [audit_from(item) for item in data.get("audit", [])]
    return service
