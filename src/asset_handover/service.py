"""基础设施运营责任交接的应用服务。

编排交接包状态机、唯一责任链、合同版本绑定与审计事件，
所有敏感操作都校验角色，所有状态变更都留下审计痕迹。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from .domain import (
    AssetVersion,
    AuditEvent,
    ContractRevision,
    Deduction,
    Defect,
    DefectStatus,
    DomainError,
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
    Role,
    ServiceMetric,
    StateError,
    WarrantyBoundary,
    now_utc,
)

_OWNER_ONLY = frozenset({Role.OWNER})
_CONFIRMERS = frozenset({Role.OWNER, Role.INSPECTOR})
_DEFECT_REPORTERS = frozenset({Role.OWNER, Role.INSPECTOR, Role.OPERATOR})
_METRIC_RECORDERS = frozenset({Role.OWNER, Role.OPERATOR})
_ANY_ROLE = frozenset(Role)

_TERMINAL_STATES = (PackageState.FORMAL_TAKEOVER, PackageState.RETURNED)


class HandoverService:
    """基础设施运营责任交接的核心服务。"""

    def __init__(self, clock: Callable[[], datetime] = now_utc) -> None:
        self._clock = clock
        self._packages: dict[str, HandoverPackage] = {}
        self._chains: dict[str, list[ResponsibilityLink]] = {}
        self._revisions: dict[str, list[ContractRevision]] = {}
        self._reports: dict[str, RepairReport] = {}
        self._metrics: list[ServiceMetric] = []
        self._incidents: dict[str, Incident] = {}
        self._deductions: list[Deduction] = []
        self._audit_events: list[AuditEvent] = []
        self._seq = 0

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    @staticmethod
    def _require_role(role: Role, allowed: frozenset, action: str) -> None:
        if role not in allowed:
            names = "/".join(sorted(item.value for item in allowed))
            raise PermissionError(f"{action} 需要角色 {names}，当前角色 {role.value}")

    def _audit(self, actor: str, role: Role, action: str, target: str, detail: Optional[dict] = None) -> None:
        self._audit_events.append(
            AuditEvent(
                seq=len(self._audit_events) + 1,
                at=self._clock(),
                actor=actor,
                role=role.value,
                action=action,
                target=target,
                detail=detail or {},
            )
        )

    def _append_chain(self, asset_code: str, party: str, reason: str) -> ResponsibilityLink:
        """关闭当前链节并追加新链节，保证任意时刻责任主体唯一。"""
        links = self._chains.setdefault(asset_code, [])
        moment = self._clock()
        if links and links[-1].is_current:
            links[-1] = replace(links[-1], ended_at=moment)
        link = ResponsibilityLink(
            sequence=len(links) + 1,
            asset_code=asset_code,
            party=party,
            reason=reason,
            effective_at=moment,
        )
        links.append(link)
        return link

    def _package_for_asset(self, asset_code: str) -> Optional[HandoverPackage]:
        matches = [pkg for pkg in self._packages.values() if pkg.asset.asset_code == asset_code]
        return matches[-1] if matches else None

    def _revision_at(self, contract_id: str, moment: datetime) -> Optional[ContractRevision]:
        for revision in self._revisions.get(contract_id, []):
            if revision.covers(moment):
                return revision
        return None

    @staticmethod
    def _replace_remedy(package: HandoverPackage, remedy: RemedyItem) -> HandoverPackage:
        remedies = tuple(remedy if item.remedy_id == remedy.remedy_id else item for item in package.remedies)
        return package.evolve(remedies=remedies)

    @staticmethod
    def _close_defect(package: HandoverPackage, defect_id: str, status: DefectStatus, reason: str) -> HandoverPackage:
        defects = tuple(item.close(status, reason) if item.defect_id == defect_id else item for item in package.defects)
        return package.evolve(defects=defects)

    # ------------------------------------------------------------------
    # 交接包生命周期
    # ------------------------------------------------------------------
    def create_package(
        self,
        actor: str,
        role: Role,
        *,
        asset: AssetVersion,
        contract_id: str,
        package_id: Optional[str] = None,
        builder_party: str = "建设单位",
        operator_party: str = "运营企业",
        warranty: Optional[WarrantyBoundary] = None,
        maintenance_plans: Iterable[MaintenancePlan] = (),
        emergency_contacts: Iterable[EmergencyContact] = (),
        defects: Iterable[dict] = (),
    ) -> HandoverPackage:
        """建立交接包，资产责任初始挂在建设单位名下。"""
        self._require_role(role, _CONFIRMERS, "创建交接包")
        package_id = package_id or self._next_id("pkg")
        if package_id in self._packages:
            raise DomainError(f"交接包 {package_id} 已存在")
        existing = self._package_for_asset(asset.asset_code)
        if existing is not None and existing.state not in _TERMINAL_STATES:
            raise DomainError(f"资产 {asset.asset_code} 已有进行中的交接包 {existing.package_id}")
        moment = self._clock()
        defect_items = tuple(
            Defect(
                defect_id=self._next_id("def"),
                description=item["description"],
                severity=item.get("severity", "一般"),
                raised_at=moment,
            )
            for item in defects
        )
        package = HandoverPackage(
            package_id=package_id,
            asset=asset,
            contract_id=contract_id,
            builder_party=builder_party,
            operator_party=operator_party,
            warranty=warranty,
            maintenance_plans=tuple(maintenance_plans),
            emergency_contacts=tuple(emergency_contacts),
            defects=defect_items,
            created_at=moment,
        )
        self._packages[package_id] = package
        self._append_chain(asset.asset_code, builder_party, "交接启动，建设单位承担运营责任")
        self._audit(actor, role, "CREATE_PACKAGE", package_id, {"asset_code": asset.asset_code, "contract_id": contract_id})
        return package

    def get_package(self, package_id: str) -> HandoverPackage:
        try:
            return self._packages[package_id]
        except KeyError:
            raise DomainError(f"交接包 {package_id} 不存在") from None

    def list_packages(self) -> list[HandoverPackage]:
        return list(self._packages.values())

    def add_defect(self, actor: str, role: Role, package_id: str, description: str, severity: str = "一般") -> Defect:
        """在核验或验收过程中登记未决缺陷。"""
        self._require_role(role, _DEFECT_REPORTERS, "登记缺陷")
        package = self.get_package(package_id)
        if package.state in _TERMINAL_STATES:
            raise StateError("已接管或已退回的交接包不能再登记缺陷")
        defect = Defect(defect_id=self._next_id("def"), description=description, severity=severity, raised_at=self._clock())
        self._packages[package_id] = package.evolve(defects=package.defects + (defect,))
        self._audit(actor, role, "ADD_DEFECT", package_id, {"defect_id": defect.defect_id, "severity": severity})
        return defect

    def confirm_document_review(self, actor: str, role: Role, package_id: str) -> HandoverPackage:
        """资料核验通过，进入现场验收。"""
        self._require_role(role, _CONFIRMERS, "资料核验确认")
        package = self.get_package(package_id)
        if package.state is not PackageState.DOC_REVIEW:
            raise StateError(f"当前状态 {package.state.value} 不允许资料核验确认")
        package = package.evolve(state=PackageState.SITE_ACCEPTANCE)
        self._packages[package_id] = package
        self._audit(actor, role, "CONFIRM_DOCUMENT_REVIEW", package_id)
        return package

    def accept_with_conditions(self, actor: str, role: Role, package_id: str, remedy_specs: Iterable[dict]) -> HandoverPackage:
        """附条件接收：每个未决缺陷都必须形成有期限的补救项。

        remedy_specs 每项包含：defect_id、deadline(datetime)、on_expiry、
        可选 description、escalation_extension_days、deduction_amount。
        """
        self._require_role(role, _OWNER_ONLY, "条件接收")
        package = self.get_package(package_id)
        if package.state is not PackageState.SITE_ACCEPTANCE:
            raise StateError(f"当前状态 {package.state.value} 不允许条件接收")
        open_defects = {item.defect_id: item for item in package.open_defects}
        if not open_defects:
            raise DomainError("没有未决缺陷，无需附条件接收，可直接正式接管")
        spec_by_defect: dict[str, dict] = {}
        for spec in remedy_specs:
            defect_id = spec.get("defect_id")
            if defect_id in spec_by_defect:
                raise DomainError(f"缺陷 {defect_id} 的补救项重复")
            spec_by_defect[defect_id] = spec
        missing = sorted(set(open_defects) - set(spec_by_defect))
        extra = sorted(set(spec_by_defect) - set(open_defects))
        if missing:
            raise DomainError(f"附条件接收必须为每个未决缺陷形成补救项，缺少: {missing}")
        if extra:
            raise DomainError(f"补救项指向不存在或已关闭的缺陷: {extra}")
        moment = self._clock()
        remedies = []
        for defect in package.open_defects:
            spec = spec_by_defect[defect.defect_id]
            deadline = spec["deadline"]
            if not isinstance(deadline, datetime):
                raise DomainError("补救项期限必须是 datetime")
            if deadline <= moment:
                raise DomainError(f"补救项期限必须晚于当前时间: {defect.defect_id}")
            remedies.append(
                RemedyItem(
                    remedy_id=self._next_id("rem"),
                    defect_id=defect.defect_id,
                    description=spec.get("description") or defect.description,
                    deadline=deadline,
                    on_expiry=ExpiryAction(spec["on_expiry"]),
                    escalation_extension_days=int(spec.get("escalation_extension_days", 0)),
                    deduction_amount=float(spec.get("deduction_amount", 0.0)),
                )
            )
        package = package.evolve(state=PackageState.CONDITIONAL_ACCEPTANCE, remedies=package.remedies + tuple(remedies))
        self._packages[package_id] = package
        self._audit(actor, role, "ACCEPT_WITH_CONDITIONS", package_id, {"remedy_ids": [item.remedy_id for item in remedies]})
        return package

    def confirm_takeover(self, actor: str, role: Role, package_id: str) -> HandoverPackage:
        """正式接管：存在未决缺陷或未关闭补救项时一律拒绝。"""
        self._require_role(role, _OWNER_ONLY, "正式接管确认")
        package = self.get_package(package_id)
        if package.state not in (PackageState.SITE_ACCEPTANCE, PackageState.CONDITIONAL_ACCEPTANCE):
            raise StateError(f"当前状态 {package.state.value} 不允许正式接管")
        if package.open_defects or package.open_remedies:
            raise DomainError("存在未决缺陷或未关闭补救项，不能把资产静默视为无缺陷而接管")
        package = package.evolve(state=PackageState.FORMAL_TAKEOVER)
        self._packages[package_id] = package
        self._append_chain(package.asset.asset_code, package.operator_party, "正式接管，运营企业承担运营责任")
        self._audit(actor, role, "CONFIRM_TAKEOVER", package_id, {"operator_party": package.operator_party})
        return package

    def return_package(self, actor: str, role: Role, package_id: str, reason: str) -> HandoverPackage:
        """业主决定将资产退回建设单位。"""
        self._require_role(role, _OWNER_ONLY, "退回资产")
        package = self.get_package(package_id)
        if package.state not in (PackageState.SITE_ACCEPTANCE, PackageState.CONDITIONAL_ACCEPTANCE):
            raise StateError(f"当前状态 {package.state.value} 不允许退回")
        package = self._return_package(package, actor, role, reason)
        self._packages[package_id] = package
        return package

    def _return_package(self, package: HandoverPackage, actor: str, role: Role, reason: str) -> HandoverPackage:
        moment = self._clock()
        remedies = tuple(
            replace(item, status=RemedyStatus.VOID, history=item.history + (f"{moment.isoformat()} 资产退回，补救项终止",))
            if item.is_open
            else item
            for item in package.remedies
        )
        package = package.evolve(state=PackageState.RETURNED, remedies=remedies)
        self._append_chain(package.asset.asset_code, package.builder_party, f"退回建设单位：{reason}")
        self._audit(actor, role, "RETURN_PACKAGE", package.package_id, {"reason": reason})
        return package

    # ------------------------------------------------------------------
    # 补救项
    # ------------------------------------------------------------------
    def confirm_remedy_completed(self, actor: str, role: Role, package_id: str, remedy_id: str, note: str = "") -> HandoverPackage:
        """确认补救项按期整改完成，关联缺陷随之关闭。"""
        self._require_role(role, _CONFIRMERS, "补救完成确认")
        package = self.get_package(package_id)
        remedy = package.remedy(remedy_id)
        if not remedy.is_open:
            raise StateError(f"补救项 {remedy_id} 已关闭")
        moment = self._clock()
        remedy = replace(
            remedy,
            status=RemedyStatus.COMPLETED,
            history=remedy.history + (f"{moment.isoformat()} 由 {actor} 确认完成 {note}".strip(),),
        )
        package = self._replace_remedy(package, remedy)
        package = self._close_defect(package, remedy.defect_id, DefectStatus.RESOLVED, f"补救项 {remedy_id} 整改完成")
        self._packages[package_id] = package
        self._audit(actor, role, "CONFIRM_REMEDY_COMPLETED", package_id, {"remedy_id": remedy_id})
        return package

    def waive_remedy(self, actor: str, role: Role, package_id: str, remedy_id: str, reason: str) -> HandoverPackage:
        """业主书面豁免补救项，关联缺陷同步豁免。"""
        self._require_role(role, _OWNER_ONLY, "豁免补救项")
        package = self.get_package(package_id)
        remedy = package.remedy(remedy_id)
        if not remedy.is_open:
            raise StateError(f"补救项 {remedy_id} 已关闭")
        moment = self._clock()
        remedy = replace(
            remedy,
            status=RemedyStatus.WAIVED,
            history=remedy.history + (f"{moment.isoformat()} 由 {actor} 豁免：{reason}",),
        )
        package = self._replace_remedy(package, remedy)
        package = self._close_defect(package, remedy.defect_id, DefectStatus.WAIVED, f"补救项 {remedy_id} 被豁免：{reason}")
        self._packages[package_id] = package
        self._audit(actor, role, "WAIVE_REMEDY", package_id, {"remedy_id": remedy_id, "reason": reason})
        return package

    def waive_defect(self, actor: str, role: Role, package_id: str, defect_id: str, reason: str) -> HandoverPackage:
        """业主书面豁免缺陷：显式留痕，而不是静默视为无缺陷。"""
        self._require_role(role, _OWNER_ONLY, "豁免缺陷")
        package = self.get_package(package_id)
        defect = package.defect(defect_id)
        if not defect.is_open:
            raise StateError(f"缺陷 {defect_id} 已关闭")
        package = self._close_defect(package, defect_id, DefectStatus.WAIVED, reason)
        self._packages[package_id] = package
        self._audit(actor, role, "WAIVE_DEFECT", package_id, {"defect_id": defect_id, "reason": reason})
        return package

    def process_expired_remedies(self, actor: str, role: Role, package_id: Optional[str] = None) -> list[str]:
        """处置到期未完成的补救项：按约定升级、扣减或退回。"""
        self._require_role(role, _CONFIRMERS, "到期补救项处置")
        moment = self._clock()
        processed: list[str] = []
        for package_id_key in sorted(self._packages):
            if package_id is not None and package_id_key != package_id:
                continue
            package = self._packages[package_id_key]
            for remedy_id in [item.remedy_id for item in package.open_remedies]:
                remedy = package.remedy(remedy_id)
                if not remedy.is_open or not remedy.is_overdue(moment):
                    continue
                if remedy.on_expiry is ExpiryAction.ESCALATE:
                    new_deadline = remedy.deadline + timedelta(days=remedy.escalation_extension_days)
                    remedy = replace(
                        remedy,
                        escalation_level=remedy.escalation_level + 1,
                        deadline=new_deadline,
                        history=remedy.history
                        + (f"{moment.isoformat()} 到期未完成，升级为 {remedy.escalation_level + 1} 级，顺延至 {new_deadline.isoformat()}",),
                    )
                    package = self._replace_remedy(package, remedy)
                    self._audit(actor, role, "ESCALATE_REMEDY", package.package_id, {"remedy_id": remedy_id, "escalation_level": remedy.escalation_level})
                elif remedy.on_expiry is ExpiryAction.DEDUCT:
                    remedy = replace(
                        remedy,
                        status=RemedyStatus.DEDUCTED,
                        history=remedy.history + (f"{moment.isoformat()} 到期未完成，按约扣减 {remedy.deduction_amount}",),
                    )
                    package = self._replace_remedy(package, remedy)
                    package = self._close_defect(package, remedy.defect_id, DefectStatus.SETTLED, f"补救项 {remedy_id} 到期扣减结案")
                    self._deductions.append(
                        Deduction(remedy_id=remedy_id, package_id=package.package_id, amount=remedy.deduction_amount, reason="到期未完成按约扣减", at=moment)
                    )
                    self._audit(actor, role, "DEDUCT_REMEDY", package.package_id, {"remedy_id": remedy_id, "amount": remedy.deduction_amount})
                else:
                    package = self._return_package(package, actor, role, f"补救项 {remedy_id} 到期未完成")
                processed.append(remedy_id)
            self._packages[package.package_id] = package
        return processed

    # ------------------------------------------------------------------
    # 责任链
    # ------------------------------------------------------------------
    def current_responsibility(self, asset_code: str) -> ResponsibilityLink:
        links = self._chains.get(asset_code)
        if not links:
            raise DomainError(f"资产 {asset_code} 没有责任链")
        return links[-1]

    def asset_responsibility(self, asset_code: str) -> dict:
        """业主视角：每项资产当前由谁负责，以及完整责任链历史。"""
        links = self._chains.get(asset_code)
        if not links:
            raise DomainError(f"资产 {asset_code} 没有责任链")
        return {"asset_code": asset_code, "current": links[-1], "history": list(links)}

    def transfer_responsibility(
        self,
        actor: str,
        role: Role,
        asset_code: str,
        new_party: str,
        reason: str,
        obligation_code: Optional[str] = None,
    ) -> ResponsibilityLink:
        """运营主体变更或义务转让：关闭当前链节并追加新链节，保持唯一责任链。"""
        self._require_role(role, _OWNER_ONLY, "运营主体变更/义务转让")
        current = self.current_responsibility(asset_code)
        if current.party == new_party and obligation_code is None:
            raise DomainError("新旧责任主体相同，无需变更")
        full_reason = reason if obligation_code is None else f"{reason}（义务 {obligation_code} 转让）"
        link = self._append_chain(asset_code, new_party, full_reason)
        self._audit(
            actor,
            role,
            "TRANSFER_RESPONSIBILITY",
            asset_code,
            {"new_party": new_party, "obligation_code": obligation_code, "sequence": link.sequence},
        )
        return link

    # ------------------------------------------------------------------
    # 报修
    # ------------------------------------------------------------------
    def report_repair(self, actor: str, role: Role, asset_code: str, problem_key: str, description: str) -> RepairReport:
        """报修：同一资产同一问题的重复报修归并到同一报修单与责任链节。"""
        self._require_role(role, _ANY_ROLE, "报修")
        for report in self._reports.values():
            if report.asset_code == asset_code and report.problem_key == problem_key and report.status is RepairStatus.OPEN:
                updated = replace(report, repeat_count=report.repeat_count + 1)
                self._reports[report.report_id] = updated
                self._audit(
                    actor,
                    role,
                    "REPEAT_REPAIR",
                    report.report_id,
                    {"asset_code": asset_code, "problem_key": problem_key, "repeat_count": updated.repeat_count},
                )
                return updated
        current = self.current_responsibility(asset_code)
        package = self._package_for_asset(asset_code)
        contract_id = package.contract_id if package else None
        revision = self._revision_at(contract_id, self._clock()) if contract_id else None
        report = RepairReport(
            report_id=self._next_id("rep"),
            asset_code=asset_code,
            problem_key=problem_key,
            description=description,
            reported_at=self._clock(),
            reported_by=actor,
            chain_sequence=current.sequence,
            contract_id=contract_id,
            revision=revision.revision if revision else None,
        )
        self._reports[report.report_id] = report
        self._audit(actor, role, "REPORT_REPAIR", report.report_id, {"asset_code": asset_code, "problem_key": problem_key})
        return report

    def close_repair(self, actor: str, role: Role, report_id: str) -> RepairReport:
        self._require_role(role, _CONFIRMERS, "关闭报修")
        report = self._reports.get(report_id)
        if report is None:
            raise DomainError(f"报修单 {report_id} 不存在")
        if report.status is RepairStatus.CLOSED:
            raise StateError(f"报修单 {report_id} 已关闭")
        updated = replace(report, status=RepairStatus.CLOSED)
        self._reports[report_id] = updated
        self._audit(actor, role, "CLOSE_REPAIR", report_id)
        return updated

    def repair_report(self, report_id: str) -> RepairReport:
        try:
            return self._reports[report_id]
        except KeyError:
            raise DomainError(f"报修单 {report_id} 不存在") from None

    # ------------------------------------------------------------------
    # 合同版本、服务指标与事故
    # ------------------------------------------------------------------
    def register_contract_revision(
        self,
        actor: str,
        role: Role,
        contract_id: str,
        obligations: Iterable,
        effective_from: datetime,
        revision: Optional[int] = None,
    ) -> ContractRevision:
        """登记合同新版本：自动终结上一版本，保证同一时刻只有一个生效版本。"""
        self._require_role(role, _OWNER_ONLY, "登记合同版本")
        revisions = self._revisions.setdefault(contract_id, [])
        revision = revision if revision is not None else len(revisions) + 1
        if revisions:
            last = revisions[-1]
            if revision <= last.revision:
                raise DomainError("合同版本号必须递增")
            if effective_from <= last.effective_from:
                raise DomainError("新版本生效时间必须晚于上一版本")
            revisions[-1] = replace(last, effective_to=effective_from)
        items = tuple(Obligation(**item) if isinstance(item, dict) else item for item in obligations)
        contract_revision = ContractRevision(contract_id=contract_id, revision=revision, obligations=items, effective_from=effective_from)
        revisions.append(contract_revision)
        self._audit(actor, role, "REGISTER_CONTRACT_REVISION", contract_id, {"revision": revision})
        return contract_revision

    def record_metric(
        self,
        actor: str,
        role: Role,
        asset_code: str,
        metric_code: str,
        value: float,
        recorded_at: Optional[datetime] = None,
    ) -> ServiceMetric:
        """记录服务指标：必须绑定记录时刻生效的合同版本。"""
        self._require_role(role, _METRIC_RECORDERS, "记录服务指标")
        recorded_at = recorded_at or self._clock()
        package = self._package_for_asset(asset_code)
        if package is None:
            raise DomainError(f"资产 {asset_code} 没有交接包，无法确定所属合同")
        revision = self._revision_at(package.contract_id, recorded_at)
        if revision is None:
            raise DomainError("记录时间不在任何合同版本的有效期内，历史服务指标必须绑定当时合同")
        metric = ServiceMetric(
            asset_code=asset_code,
            metric_code=metric_code,
            value=float(value),
            recorded_at=recorded_at,
            contract_id=revision.contract_id,
            revision=revision.revision,
        )
        self._metrics.append(metric)
        self._audit(actor, role, "RECORD_METRIC", asset_code, {"metric_code": metric_code, "revision": revision.revision})
        return metric

    def list_metrics(self, asset_code: str) -> list[ServiceMetric]:
        return [metric for metric in self._metrics if metric.asset_code == asset_code]

    def record_incident(self, actor: str, role: Role, asset_code: str, occurred_at: datetime, description: str) -> Incident:
        self._require_role(role, _ANY_ROLE, "登记事故")
        incident = Incident(incident_id=self._next_id("inc"), asset_code=asset_code, occurred_at=occurred_at, description=description)
        self._incidents[incident.incident_id] = incident
        self._audit(actor, role, "RECORD_INCIDENT", incident.incident_id, {"asset_code": asset_code})
        return incident

    def obligations_for_incident(self, incident_id: str) -> dict:
        """回答“一次事故应引用哪版义务”：按事故发生时刻定位合同版本。"""
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise DomainError(f"事故 {incident_id} 不存在")
        package = self._package_for_asset(incident.asset_code)
        if package is None:
            raise DomainError(f"资产 {incident.asset_code} 没有交接包，无法确定所属合同")
        revision = self._revision_at(package.contract_id, incident.occurred_at)
        if revision is None:
            raise DomainError(f"事故发生时合同 {package.contract_id} 没有生效版本")
        return {
            "incident_id": incident.incident_id,
            "asset_code": incident.asset_code,
            "occurred_at": incident.occurred_at,
            "contract_id": revision.contract_id,
            "revision": revision.revision,
            "obligations": list(revision.obligations),
        }

    # ------------------------------------------------------------------
    # 业主查询
    # ------------------------------------------------------------------
    def unmet_conditions(self, package_id: str) -> dict:
        """哪些条件尚未满足：未决缺陷与未关闭补救项（含是否已逾期）。"""
        package = self.get_package(package_id)
        moment = self._clock()
        return {
            "package_id": package.package_id,
            "asset_code": package.asset.asset_code,
            "state": package.state,
            "open_defects": list(package.open_defects),
            "open_remedies": list(package.open_remedies),
            "overdue_remedy_ids": [item.remedy_id for item in package.open_remedies if item.is_overdue(moment)],
            "satisfied": not package.open_defects and not package.open_remedies,
        }

    def takeover_decision_trail(self, package_id: str) -> list[AuditEvent]:
        """接管决定如何形成：该交接包的全部审计事件，按发生顺序排列。"""
        self.get_package(package_id)
        return [event for event in self._audit_events if event.target == package_id]

    def audit_trail(self, target: Optional[str] = None) -> list[AuditEvent]:
        if target is None:
            return list(self._audit_events)
        return [event for event in self._audit_events if event.target == target]

    def list_deductions(self, package_id: Optional[str] = None) -> list[Deduction]:
        if package_id is None:
            return list(self._deductions)
        return [item for item in self._deductions if item.package_id == package_id]
