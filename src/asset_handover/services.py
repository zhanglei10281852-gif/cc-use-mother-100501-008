"""基础设施运营责任交接的应用服务。

服务层承载全部用例：交接包状态流转、补救项逾期处置、责任链维护、
合同版本管理、事故报修归集、服务指标记录与业主查询。所有变更都
校验角色权限、写入审计事件并持久化；缺陷与补救项只能被显式确认
或豁免，任何逾期处置都不会把资产静默视为无缺陷。
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .domain import (
    Actor,
    AuditEvent,
    ChainReason,
    Contract,
    ContractRevision,
    Deduction,
    Defect,
    DefectStatus,
    HandoverPackage,
    IncidentReport,
    Issue,
    IssueStatus,
    Obligation,
    ObligationTransfer,
    OverduePolicy,
    PackageState,
    RemediationItem,
    RemediationStatus,
    ResponsibilityLink,
    Role,
    ServiceMetricRecord,
)
from .errors import (
    ConflictError,
    NotFoundError,
    PermissionDenied,
    StateTransitionError,
    ValidationError,
)
from .store import Store

TERMINAL_STATES = (PackageState.FORMAL_TAKEOVER, PackageState.RETURNED)
OPEN_REMEDIATION_STATES = (RemediationStatus.OPEN, RemediationStatus.ESCALATED)
DEFECT_RECORD_STATES = (PackageState.SITE_INSPECTION, PackageState.CONDITIONAL_ACCEPTANCE)


class HandoverService:
    """基础设施运营责任交接服务的应用入口。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _now(now: Optional[datetime]) -> datetime:
        return now or datetime.now(timezone.utc)

    @staticmethod
    def _require_role(actor: Actor, *roles: Role) -> None:
        if not any(role in actor.roles for role in roles):
            expected = "、".join(role.value for role in roles)
            raise PermissionDenied(f"操作需要角色 {expected}，主体 {actor.actor_id} 不具备")

    @staticmethod
    def _require_state(package: HandoverPackage, *states: PackageState) -> None:
        if package.state not in states:
            allowed = "、".join(state.value for state in states)
            raise StateTransitionError(
                f"交接包 {package.package_code} 当前状态为 {package.state.value}，无法执行该操作（需要 {allowed}）"
            )

    def _get_package(self, package_code: str) -> HandoverPackage:
        package = self.store.packages.get(package_code)
        if package is None:
            raise NotFoundError(f"交接包 {package_code} 不存在")
        return package

    def _get_contract(self, contract_code: str) -> Contract:
        contract = self.store.contracts.get(contract_code)
        if contract is None:
            raise NotFoundError(f"合同 {contract_code} 不存在")
        return contract

    def _find_obligation(self, obligation_code: str) -> Obligation:
        for contract in self.store.contracts.values():
            for revision in reversed(contract.revisions):
                for obligation in revision.obligations:
                    if obligation.obligation_code == obligation_code:
                        return obligation
        raise NotFoundError(f"合同义务 {obligation_code} 不存在")

    def _audit(
        self,
        actor: Actor,
        action: str,
        entity_type: str,
        entity_code: str,
        at: datetime,
        **details: Any,
    ) -> None:
        self.store.audit.append(
            AuditEvent(
                seq=self.store.next_seq(),
                at=at,
                actor_id=actor.actor_id,
                action=action,
                entity_type=entity_type,
                entity_code=entity_code,
                details={key: str(value) for key, value in details.items()},
            )
        )

    def _append_chain(
        self,
        asset_code: str,
        party: str,
        reason: ChainReason,
        effective_from: datetime,
        actor: Actor,
        note: str,
        now: datetime,
    ) -> ResponsibilityLink:
        links = self.store.chains.setdefault(asset_code, [])
        if links and effective_from <= links[-1].effective_from:
            raise ValidationError(
                f"资产 {asset_code} 责任链新环节的生效时间必须晚于上一环节 {links[-1].effective_from.isoformat()}"
            )
        link = ResponsibilityLink(
            seq=len(links) + 1,
            asset_code=asset_code,
            party=party,
            effective_from=effective_from,
            reason=reason,
            note=note,
            actor_id=actor.actor_id,
            recorded_at=now,
        )
        links.append(link)
        return link

    def _responsible_at(self, asset_code: str, at: datetime) -> ResponsibilityLink:
        links = self.store.chains.get(asset_code)
        if not links:
            raise NotFoundError(f"资产 {asset_code} 没有责任链记录")
        eligible = [link for link in links if link.effective_from <= at]
        if not eligible:
            raise ValidationError(f"资产 {asset_code} 在 {at.isoformat()} 尚无责任记录")
        return eligible[-1]

    def _obligation_holder_at(self, obligation_code: str, at: datetime) -> str:
        holder = self._find_obligation(obligation_code).owner_party
        for transfer in self.store.transfers.get(obligation_code, []):
            if transfer.effective_from <= at:
                holder = transfer.to_party
        return holder

    def _replace_defect(self, package: HandoverPackage, defect_code: str, **changes: Any) -> HandoverPackage:
        try:
            target = package.find_defect(defect_code)
        except KeyError:
            raise NotFoundError(f"交接包 {package.package_code} 中不存在缺陷 {defect_code}") from None
        updated = replace(target, **changes)
        defects = tuple(updated if defect.defect_code == defect_code else defect for defect in package.defects)
        return package.evolve(defects=defects)

    def _get_remediation(self, package: HandoverPackage, item_code: str) -> RemediationItem:
        try:
            return package.find_remediation(item_code)
        except KeyError:
            raise NotFoundError(f"交接包 {package.package_code} 中不存在补救项 {item_code}") from None

    def _replace_remediation(
        self, package: HandoverPackage, item_code: str, **changes: Any
    ) -> tuple[HandoverPackage, RemediationItem]:
        try:
            target = package.find_remediation(item_code)
        except KeyError:
            raise NotFoundError(f"交接包 {package.package_code} 中不存在补救项 {item_code}") from None
        updated = replace(target, **changes)
        items = tuple(updated if item.item_code == item_code else item for item in package.remediations)
        return package.evolve(remediations=items), updated

    # ------------------------------------------------------------------
    # 合同与义务
    # ------------------------------------------------------------------
    def register_contract(
        self, actor: Actor, contract_code: str, title: str, now: Optional[datetime] = None
    ) -> Contract:
        """登记合同，义务条目通过版本逐个生效。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        if contract_code in self.store.contracts:
            raise ConflictError(f"合同 {contract_code} 已存在")
        contract = Contract(contract_code=contract_code, title=title)
        self.store.contracts[contract_code] = contract
        self._audit(actor, "register_contract", "contract", contract_code, now, title=title)
        self.store.save()
        return contract

    def register_revision(
        self,
        actor: Actor,
        contract_code: str,
        revision: str,
        effective_from: datetime,
        obligations: Iterable[Obligation],
        now: Optional[datetime] = None,
    ) -> Contract:
        """登记合同新版本，生效时间必须晚于既有版本，保证任意时刻版本唯一。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        contract = self._get_contract(contract_code)
        if any(item.revision == revision for item in contract.revisions):
            raise ConflictError(f"合同 {contract_code} 已存在版本 {revision}")
        if contract.revisions and effective_from <= contract.revisions[-1].effective_from:
            raise ValidationError(
                f"新版本生效时间必须晚于当前版本 {contract.revisions[-1].effective_from.isoformat()}"
            )
        items = tuple(obligations)
        codes = [item.obligation_code for item in items]
        if len(set(codes)) != len(codes):
            raise ValidationError("同一版本内义务编码必须唯一")
        contract = replace(
            contract,
            revisions=contract.revisions
            + (ContractRevision(contract_code=contract_code, revision=revision, effective_from=effective_from, obligations=items),),
        )
        self.store.contracts[contract_code] = contract
        self._audit(
            actor, "register_revision", "contract", contract_code, now,
            revision=revision, effective_from=effective_from.isoformat(), obligations=len(items),
        )
        self.store.save()
        return contract

    def applicable_obligations(self, contract_code: str, at: datetime) -> dict[str, Any]:
        """查询指定时刻生效的合同版本及其义务，用于确定事故应引用哪版义务。"""
        contract = self._get_contract(contract_code)
        revision = contract.revision_at(at)
        return {
            "contract_code": contract_code,
            "at": at,
            "revision": revision.revision,
            "obligations": list(revision.obligations),
        }

    def transfer_obligation(
        self,
        actor: Actor,
        obligation_code: str,
        to_party: str,
        effective_from: datetime,
        note: str = "",
        now: Optional[datetime] = None,
    ) -> ObligationTransfer:
        """义务转让：转让前后同一义务只能有一个承担方，保持唯一责任链。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        self._find_obligation(obligation_code)
        transfers = self.store.transfers.setdefault(obligation_code, [])
        if transfers and effective_from <= transfers[-1].effective_from:
            raise ValidationError("义务转让的生效时间必须晚于上一次转让")
        holder = self._obligation_holder_at(obligation_code, effective_from)
        if holder == to_party:
            raise ValidationError(f"受让方 {to_party} 已持有义务 {obligation_code}")
        transfer = ObligationTransfer(
            obligation_code=obligation_code,
            from_party=holder,
            to_party=to_party,
            effective_from=effective_from,
            note=note,
            actor_id=actor.actor_id,
            recorded_at=now,
        )
        transfers.append(transfer)
        self._audit(
            actor, "transfer_obligation", "obligation", obligation_code, now,
            from_party=holder, to_party=to_party, effective_from=effective_from.isoformat(),
        )
        self.store.save()
        return transfer

    def obligation_holder(self, obligation_code: str, at: Optional[datetime] = None) -> dict[str, Any]:
        """查询义务在指定时刻的承担方。"""
        at = self._now(at)
        obligation = self._find_obligation(obligation_code)
        return {
            "obligation_code": obligation_code,
            "at": at,
            "holder": self._obligation_holder_at(obligation_code, at),
            "original_owner": obligation.owner_party,
            "transfers": list(self.store.transfers.get(obligation_code, [])),
        }

    # ------------------------------------------------------------------
    # 交接包生命周期
    # ------------------------------------------------------------------
    def create_package(
        self, actor: Actor, package: HandoverPackage, now: Optional[datetime] = None
    ) -> HandoverPackage:
        """登记交接包，资产责任链自登记起由建设单位负责。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        if package.package_code in self.store.packages:
            raise ConflictError(f"交接包 {package.package_code} 已存在")
        if package.state is not PackageState.DRAFT:
            raise ValidationError("新建交接包必须处于 DRAFT 状态")
        asset_code = package.asset.asset_code
        for existing in self.store.packages.values():
            if existing.asset.asset_code == asset_code and existing.state not in TERMINAL_STATES:
                raise ConflictError(
                    f"资产 {asset_code} 已绑定未完结的交接包 {existing.package_code}，责任链必须唯一"
                )
        package = package.evolve(created_at=package.created_at or now)
        self.store.packages[package.package_code] = package
        self._append_chain(asset_code, package.constructor_party, ChainReason.REGISTRATION, now, actor, "交接包登记", now)
        self._audit(
            actor, "create_package", "package", package.package_code, now,
            asset_code=asset_code, constructor=package.constructor_party, operator=package.operator_party,
        )
        self.store.save()
        return package

    def submit_package(self, actor: Actor, package_code: str, now: Optional[datetime] = None) -> HandoverPackage:
        """提交资料核验：交接包必须聚合完整的保修边界、维护计划与应急联系人。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, PackageState.DRAFT)
        if not package.warranties:
            raise ValidationError("交接包缺少保修边界，无法提交核验")
        if package.maintenance_plan is None:
            raise ValidationError("交接包缺少维护计划，无法提交核验")
        if not package.contacts:
            raise ValidationError("交接包缺少应急联系人，无法提交核验")
        revision = self._get_contract(package.contract_code).revision_at(now)
        package = package.evolve(state=PackageState.DOC_REVIEW, baseline_revision=revision.revision)
        self.store.packages[package_code] = package
        self._audit(actor, "submit_package", "package", package_code, now, baseline_revision=revision.revision)
        self.store.save()
        return package

    def confirm_documents(
        self, actor: Actor, package_code: str, note: str = "", now: Optional[datetime] = None
    ) -> HandoverPackage:
        """资料核验通过，进入现场验收。"""
        self._require_role(actor, Role.ACCEPTANCE_LEAD)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, PackageState.DOC_REVIEW)
        package = package.evolve(state=PackageState.SITE_INSPECTION)
        self.store.packages[package_code] = package
        self._audit(actor, "confirm_documents", "package", package_code, now, note=note)
        self.store.save()
        return package

    def record_defect(
        self,
        actor: Actor,
        package_code: str,
        defect_code: str,
        description: str,
        severity: str,
        now: Optional[datetime] = None,
    ) -> HandoverPackage:
        """登记验收中发现的缺陷。"""
        self._require_role(actor, Role.ACCEPTANCE_LEAD)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, *DEFECT_RECORD_STATES)
        if any(defect.defect_code == defect_code for defect in package.defects):
            raise ConflictError(f"缺陷 {defect_code} 已登记")
        defect = Defect(defect_code=defect_code, description=description, severity=severity, found_at=now)
        package = package.evolve(defects=package.defects + (defect,))
        self.store.packages[package_code] = package
        self._audit(actor, "record_defect", "package", package_code, now, defect_code=defect_code, severity=severity)
        self.store.save()
        return package

    def accept_conditionally(
        self,
        actor: Actor,
        package_code: str,
        conditions: Iterable[RemediationItem],
        now: Optional[datetime] = None,
    ) -> HandoverPackage:
        """附条件接收：每项条件都形成有期限的补救项，未决缺陷必须全部被覆盖。"""
        self._require_role(actor, Role.ACCEPTANCE_LEAD)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, PackageState.SITE_INSPECTION)
        items = tuple(conditions)
        if not items:
            raise ValidationError("附条件接收必须至少形成一项补救项")
        codes = [item.item_code for item in items]
        if len(set(codes)) != len(codes):
            raise ValidationError("补救项编码必须唯一")
        for item in items:
            if item.deadline <= now:
                raise ValidationError(f"补救项 {item.item_code} 的期限必须晚于当前时间")
            if item.policy is OverduePolicy.DEDUCT:
                try:
                    if float(item.policy_param) <= 0:
                        raise ValueError
                except ValueError:
                    raise ValidationError(f"补救项 {item.item_code} 的扣减金额必须为正数") from None
            for defect_code in item.linked_defects:
                try:
                    defect = package.find_defect(defect_code)
                except KeyError:
                    raise ValidationError(f"补救项 {item.item_code} 引用了不存在的缺陷 {defect_code}") from None
                if defect.status is not DefectStatus.OPEN:
                    raise ValidationError(f"缺陷 {defect_code} 已关闭，不能重复纳入补救项")
        covered = {code for item in items for code in item.linked_defects}
        uncovered = [defect.defect_code for defect in package.open_defects() if defect.defect_code not in covered]
        if uncovered:
            raise ValidationError(f"附条件接收必须为每项未决缺陷建立补救项，未覆盖：{'、'.join(uncovered)}")
        package = package.evolve(state=PackageState.CONDITIONAL_ACCEPTANCE, remediations=package.remediations + items)
        self.store.packages[package_code] = package
        self._audit(actor, "accept_conditionally", "package", package_code, now, items="、".join(codes))
        self.store.save()
        return package

    def complete_remediation(
        self, actor: Actor, package_code: str, item_code: str, note: str = "", now: Optional[datetime] = None
    ) -> HandoverPackage:
        """确认补救项完成，关联缺陷同步整改关闭。"""
        self._require_role(actor, Role.ACCEPTANCE_LEAD)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, PackageState.CONDITIONAL_ACCEPTANCE)
        item = self._get_remediation(package, item_code)
        if item.status not in OPEN_REMEDIATION_STATES:
            raise StateTransitionError(f"补救项 {item_code} 当前状态为 {item.status.value}，无法确认完成")
        package, _ = self._replace_remediation(
            package, item_code, status=RemediationStatus.COMPLETED, completed_at=now, completion_note=note
        )
        for defect_code in item.linked_defects:
            defect = package.find_defect(defect_code)
            if defect.status is DefectStatus.OPEN:
                package = self._replace_defect(
                    package, defect_code,
                    status=DefectStatus.RESOLVED, resolved_at=now,
                    resolution_note=f"补救项 {item_code} 完成确认",
                )
        self.store.packages[package_code] = package
        self._audit(actor, "complete_remediation", "package", package_code, now, item_code=item_code, note=note)
        self.store.save()
        return package

    def waive_remediation(
        self, actor: Actor, package_code: str, item_code: str, reason: str, now: Optional[datetime] = None
    ) -> HandoverPackage:
        """豁免补救项，仅业主代表可豁免且必须说明理由。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        if not reason.strip():
            raise ValidationError("豁免补救项必须说明理由")
        package = self._get_package(package_code)
        self._require_state(package, PackageState.CONDITIONAL_ACCEPTANCE)
        item = self._get_remediation(package, item_code)
        if item.status not in OPEN_REMEDIATION_STATES:
            raise StateTransitionError(f"补救项 {item_code} 当前状态为 {item.status.value}，无法豁免")
        package, _ = self._replace_remediation(package, item_code, status=RemediationStatus.WAIVED, waived_reason=reason)
        for defect_code in item.linked_defects:
            defect = package.find_defect(defect_code)
            if defect.status is DefectStatus.OPEN:
                package = self._replace_defect(package, defect_code, status=DefectStatus.WAIVED, waived_reason=reason)
        self.store.packages[package_code] = package
        self._audit(actor, "waive_remediation", "package", package_code, now, item_code=item_code, reason=reason)
        self.store.save()
        return package

    def reschedule_remediation(
        self, actor: Actor, package_code: str, item_code: str, new_deadline: datetime, now: Optional[datetime] = None
    ) -> HandoverPackage:
        """为已升级的补救项重新约定期限。"""
        self._require_role(actor, Role.ACCEPTANCE_LEAD)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, PackageState.CONDITIONAL_ACCEPTANCE)
        item = self._get_remediation(package, item_code)
        if item.status is not RemediationStatus.ESCALATED:
            raise StateTransitionError(f"补救项 {item_code} 未处于升级状态，无法重新约定期限")
        if new_deadline <= now:
            raise ValidationError("新的补救期限必须晚于当前时间")
        package, _ = self._replace_remediation(package, item_code, status=RemediationStatus.OPEN, deadline=new_deadline)
        self.store.packages[package_code] = package
        self._audit(
            actor, "reschedule_remediation", "package", package_code, now,
            item_code=item_code, new_deadline=new_deadline.isoformat(),
        )
        self.store.save()
        return package

    def resolve_defect(
        self, actor: Actor, package_code: str, defect_code: str, note: str = "", now: Optional[datetime] = None
    ) -> HandoverPackage:
        """确认缺陷整改完成。"""
        self._require_role(actor, Role.ACCEPTANCE_LEAD)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, *DEFECT_RECORD_STATES)
        if not any(defect.defect_code == defect_code for defect in package.defects):
            raise NotFoundError(f"交接包 {package_code} 中不存在缺陷 {defect_code}")
        defect = package.find_defect(defect_code)
        if defect.status is not DefectStatus.OPEN:
            raise StateTransitionError(f"缺陷 {defect_code} 已关闭")
        package = self._replace_defect(
            package, defect_code, status=DefectStatus.RESOLVED, resolved_at=now, resolution_note=note
        )
        self.store.packages[package_code] = package
        self._audit(actor, "resolve_defect", "package", package_code, now, defect_code=defect_code, note=note)
        self.store.save()
        return package

    def waive_defect(
        self, actor: Actor, package_code: str, defect_code: str, reason: str, now: Optional[datetime] = None
    ) -> HandoverPackage:
        """豁免缺陷，仅业主代表可豁免且必须说明理由。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        if not reason.strip():
            raise ValidationError("豁免缺陷必须说明理由")
        package = self._get_package(package_code)
        self._require_state(package, *DEFECT_RECORD_STATES)
        if not any(defect.defect_code == defect_code for defect in package.defects):
            raise NotFoundError(f"交接包 {package_code} 中不存在缺陷 {defect_code}")
        defect = package.find_defect(defect_code)
        if defect.status is not DefectStatus.OPEN:
            raise StateTransitionError(f"缺陷 {defect_code} 已关闭")
        package = self._replace_defect(package, defect_code, status=DefectStatus.WAIVED, waived_reason=reason)
        self.store.packages[package_code] = package
        self._audit(actor, "waive_defect", "package", package_code, now, defect_code=defect_code, reason=reason)
        self.store.save()
        return package

    def take_over(self, actor: Actor, package_code: str, now: Optional[datetime] = None) -> HandoverPackage:
        """正式接管：不得存在未决缺陷或未完成的补救项，责任链切换至运营企业。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, PackageState.SITE_INSPECTION, PackageState.CONDITIONAL_ACCEPTANCE)
        open_defects = [defect.defect_code for defect in package.open_defects()]
        if open_defects:
            raise StateTransitionError(f"存在未决缺陷 {'、'.join(open_defects)}，不能正式接管")
        open_items = [item.item_code for item in package.open_remediations()]
        if open_items:
            raise StateTransitionError(f"存在未完成的补救项 {'、'.join(open_items)}，不能正式接管")
        package = package.evolve(state=PackageState.FORMAL_TAKEOVER)
        self.store.packages[package_code] = package
        self._append_chain(
            package.asset.asset_code, package.operator_party, ChainReason.TAKEOVER, now, actor, "正式接管", now
        )
        self._audit(
            actor, "take_over", "package", package_code, now,
            asset_code=package.asset.asset_code, operator=package.operator_party,
        )
        self.store.save()
        return package

    def process_overdue(
        self, actor: Actor, package_code: str, now: Optional[datetime] = None
    ) -> list[dict[str, Any]]:
        """处置到期未完成的补救项：按约定升级、扣减或退回，绝不静默关闭。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        package = self._get_package(package_code)
        self._require_state(package, PackageState.CONDITIONAL_ACCEPTANCE)
        actions: list[dict[str, Any]] = []
        for item in package.remediations:
            if item.status not in OPEN_REMEDIATION_STATES or item.deadline >= now:
                continue
            if item.policy is OverduePolicy.ESCALATE:
                package, updated = self._replace_remediation(
                    package, item.item_code,
                    status=RemediationStatus.ESCALATED, escalation_level=item.escalation_level + 1,
                )
                self._audit(
                    actor, "process_overdue_escalate", "package", package_code, now,
                    item_code=item.item_code, escalation_level=updated.escalation_level,
                )
                actions.append({"item_code": item.item_code, "action": "escalated", "level": updated.escalation_level})
            elif item.policy is OverduePolicy.DEDUCT:
                deduction = Deduction(
                    amount=float(item.policy_param),
                    reason=f"补救项 {item.item_code} 逾期未完成",
                    applied_at=now,
                    actor_id=actor.actor_id,
                )
                package, _ = self._replace_remediation(
                    package, item.item_code, deductions=item.deductions + (deduction,)
                )
                self._audit(
                    actor, "process_overdue_deduct", "package", package_code, now,
                    item_code=item.item_code, amount=deduction.amount,
                )
                actions.append({"item_code": item.item_code, "action": "deducted", "amount": deduction.amount})
            else:
                items = tuple(
                    replace(remaining, status=RemediationStatus.RETURNED)
                    if remaining.status in OPEN_REMEDIATION_STATES
                    else remaining
                    for remaining in package.remediations
                )
                package = package.evolve(state=PackageState.RETURNED, remediations=items)
                self._audit(actor, "process_overdue_return", "package", package_code, now, item_code=item.item_code)
                actions.append({"item_code": item.item_code, "action": "returned"})
                break
        self.store.packages[package_code] = package
        self.store.save()
        return actions

    # ------------------------------------------------------------------
    # 责任链
    # ------------------------------------------------------------------
    def change_operator(
        self,
        actor: Actor,
        asset_code: str,
        new_party: str,
        effective_from: datetime,
        note: str = "",
        now: Optional[datetime] = None,
    ) -> ResponsibilityLink:
        """运营主体变更：追加责任链环节，任意时刻责任主体唯一。"""
        self._require_role(actor, Role.OWNER_REP)
        now = self._now(now)
        links = self.store.chains.get(asset_code)
        if not links:
            raise NotFoundError(f"资产 {asset_code} 没有责任链记录")
        if links[-1].party == new_party:
            raise ValidationError(f"新运营主体与当前责任方 {new_party} 相同")
        link = self._append_chain(asset_code, new_party, ChainReason.OPERATOR_CHANGE, effective_from, actor, note, now)
        self._audit(
            actor, "change_operator", "asset", asset_code, now,
            new_party=new_party, effective_from=effective_from.isoformat(),
        )
        self.store.save()
        return link

    def asset_responsibility(self, asset_code: str, at: Optional[datetime] = None) -> dict[str, Any]:
        """查询资产在指定时刻的责任主体及完整责任链。"""
        at = self._now(at)
        link = self._responsible_at(asset_code, at)
        return {
            "asset_code": asset_code,
            "at": at,
            "party": link.party,
            "reason": link.reason,
            "effective_from": link.effective_from,
            "chain": list(self.store.chains[asset_code]),
        }

    # ------------------------------------------------------------------
    # 事故与报修
    # ------------------------------------------------------------------
    def report_issue(
        self,
        actor: Actor,
        asset_code: str,
        signature: str,
        occurred_at: datetime,
        description: str,
        now: Optional[datetime] = None,
    ) -> Issue:
        """事故报修：同一问题的重复报修归集到同一事故单，保持唯一责任链。"""
        if not actor.roles:
            raise PermissionDenied("报修主体必须具备有效角色")
        now = self._now(now)
        candidates = [
            package for package in self.store.packages.values() if package.asset.asset_code == asset_code
        ]
        if not candidates:
            raise NotFoundError(f"资产 {asset_code} 未登记交接包")
        active = [package for package in candidates if package.state not in TERMINAL_STATES]
        package = (active or candidates)[-1]
        responsible = self._responsible_at(asset_code, occurred_at)
        revision = self._get_contract(package.contract_code).revision_at(occurred_at)
        issue_code = f"{asset_code}::{signature}"
        existing = self.store.issues.get(issue_code)
        seq = len(existing.reports) + 1 if existing else 1
        report = IncidentReport(
            report_code=f"{issue_code}#R{seq}",
            occurred_at=occurred_at,
            reported_at=now,
            reporter_id=actor.actor_id,
            description=description,
            responsible_party=responsible.party,
            contract_code=package.contract_code,
            contract_revision=revision.revision,
        )
        if existing is None:
            issue = Issue(
                issue_code=issue_code,
                asset_code=asset_code,
                signature=signature,
                description=description,
                first_occurred_at=occurred_at,
                reports=(report,),
            )
            action = "report_issue"
        else:
            status = existing.status
            if status is IssueStatus.CLOSED:
                status = IssueStatus.OPEN
            issue = replace(existing, status=status, reports=existing.reports + (report,))
            action = "report_issue_again" if existing.status is IssueStatus.OPEN else "reopen_issue"
        self.store.issues[issue_code] = issue
        self._audit(
            actor, action, "issue", issue_code, now,
            report_code=report.report_code, responsible_party=responsible.party, revision=revision.revision,
        )
        self.store.save()
        return issue

    def close_issue(
        self, actor: Actor, issue_code: str, note: str = "", now: Optional[datetime] = None
    ) -> Issue:
        """关闭事故单。"""
        self._require_role(actor, Role.OWNER_REP, Role.OPERATOR_REP)
        now = self._now(now)
        issue = self.store.issues.get(issue_code)
        if issue is None:
            raise NotFoundError(f"事故单 {issue_code} 不存在")
        if issue.status is IssueStatus.CLOSED:
            raise StateTransitionError(f"事故单 {issue_code} 已关闭")
        issue = replace(issue, status=IssueStatus.CLOSED)
        self.store.issues[issue_code] = issue
        self._audit(actor, "close_issue", "issue", issue_code, now, note=note)
        self.store.save()
        return issue

    def incident_context(self, issue_code: str, report_code: Optional[str] = None) -> dict[str, Any]:
        """查询一次事故应引用的责任主体与合同义务版本。"""
        issue = self.store.issues.get(issue_code)
        if issue is None:
            raise NotFoundError(f"事故单 {issue_code} 不存在")
        if report_code is None:
            report = issue.reports[-1]
        else:
            matches = [item for item in issue.reports if item.report_code == report_code]
            if not matches:
                raise NotFoundError(f"事故单 {issue_code} 中不存在报修 {report_code}")
            report = matches[0]
        contract = self._get_contract(report.contract_code)
        revision = next(item for item in contract.revisions if item.revision == report.contract_revision)
        return {
            "issue_code": issue_code,
            "report": report,
            "responsible_party": report.responsible_party,
            "contract_code": report.contract_code,
            "revision": report.contract_revision,
            "obligations": list(revision.obligations),
        }

    # ------------------------------------------------------------------
    # 服务指标
    # ------------------------------------------------------------------
    def record_metric(
        self,
        actor: Actor,
        contract_code: str,
        indicator: str,
        value: float,
        unit: str,
        recorded_at: datetime,
        now: Optional[datetime] = None,
    ) -> ServiceMetricRecord:
        """记录服务指标，历史指标继续绑定记录时刻生效的合同版本。"""
        self._require_role(actor, Role.OWNER_REP, Role.OPERATOR_REP)
        now = self._now(now)
        revision = self._get_contract(contract_code).revision_at(recorded_at)
        record = ServiceMetricRecord(
            contract_code=contract_code,
            revision=revision.revision,
            indicator=indicator,
            value=float(value),
            unit=unit,
            recorded_at=recorded_at,
            actor_id=actor.actor_id,
        )
        self.store.metrics.append(record)
        self._audit(
            actor, "record_metric", "contract", contract_code, now,
            indicator=indicator, value=record.value, revision=revision.revision,
        )
        self.store.save()
        return record

    def metrics_for(self, contract_code: str, revision: Optional[str] = None) -> list[ServiceMetricRecord]:
        """按合同（可选版本）查询服务指标记录。"""
        self._get_contract(contract_code)
        return [
            record
            for record in self.store.metrics
            if record.contract_code == contract_code and (revision is None or record.revision == revision)
        ]

    # ------------------------------------------------------------------
    # 业主查询
    # ------------------------------------------------------------------
    def package_status(self, package_code: str, now: Optional[datetime] = None) -> dict[str, Any]:
        """交接包总览：状态、责任方与缺陷/补救项统计。"""
        package = self._get_package(package_code)

        def count_defects(status: DefectStatus) -> int:
            return sum(1 for defect in package.defects if defect.status is status)

        def count_items(*statuses: RemediationStatus) -> int:
            return sum(1 for item in package.remediations if item.status in statuses)

        return {
            "package_code": package.package_code,
            "project_code": package.project_code,
            "asset_code": package.asset.asset_code,
            "asset_version": package.asset.version,
            "state": package.state,
            "contract_code": package.contract_code,
            "baseline_revision": package.baseline_revision,
            "constructor_party": package.constructor_party,
            "operator_party": package.operator_party,
            "fingerprint": package.fingerprint(),
            "defects": {
                "total": len(package.defects),
                "open": count_defects(DefectStatus.OPEN),
                "resolved": count_defects(DefectStatus.RESOLVED),
                "waived": count_defects(DefectStatus.WAIVED),
            },
            "remediations": {
                "total": len(package.remediations),
                "open": count_items(*OPEN_REMEDIATION_STATES),
                "completed": count_items(RemediationStatus.COMPLETED),
                "waived": count_items(RemediationStatus.WAIVED),
                "returned": count_items(RemediationStatus.RETURNED),
            },
        }

    def pending_conditions(self, package_code: str, now: Optional[datetime] = None) -> dict[str, Any]:
        """查询尚未满足的条件：未完成的补救项（含逾期标记）与未决缺陷。"""
        now = self._now(now)
        package = self._get_package(package_code)
        open_items = []
        for item in package.open_remediations():
            overdue = item.deadline < now
            entry = asdict(item)
            entry["is_overdue"] = overdue
            entry["overdue_days"] = (now - item.deadline).days if overdue else 0
            open_items.append(entry)
        return {
            "package_code": package_code,
            "state": package.state,
            "open_remediations": open_items,
            "open_defects": list(package.open_defects()),
        }

    def takeover_trail(self, package_code: str) -> list[AuditEvent]:
        """接管决定轨迹：交接包从登记到接管的完整审计事件序列。"""
        self._get_package(package_code)
        return [event for event in self.store.audit if event.entity_code == package_code]
