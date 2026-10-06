"""基础设施运营责任交接的领域模型。

聚合与值对象均为不可变结构，任何变更都通过生成新版本完成，
配合审计事件保证责任链与接管决定的形成过程可追溯。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def now_utc() -> datetime:
    """返回带时区的当前时间，便于测试注入时钟。"""
    return datetime.now(timezone.utc)


class DomainError(Exception):
    """领域规则被违反。"""


class StateError(DomainError):
    """当前状态不允许执行该操作。"""


class Role(str, Enum):
    """可执行确认、豁免等敏感操作的角色。"""

    OWNER = "OWNER"  # 业主代表
    INSPECTOR = "INSPECTOR"  # 验收工程师
    OPERATOR = "OPERATOR"  # 运营企业代表
    BUILDER = "BUILDER"  # 建设单位代表


class PackageState(str, Enum):
    """交接包状态机：资料核验 → 现场验收 → 条件接收 → 正式接管。"""

    DOC_REVIEW = "DOC_REVIEW"  # 资料核验
    SITE_ACCEPTANCE = "SITE_ACCEPTANCE"  # 现场验收
    CONDITIONAL_ACCEPTANCE = "CONDITIONAL_ACCEPTANCE"  # 条件接收
    FORMAL_TAKEOVER = "FORMAL_TAKEOVER"  # 正式接管
    RETURNED = "RETURNED"  # 退回建设单位


class DefectStatus(str, Enum):
    """缺陷只能经整改、豁免或扣减关闭，不允许静默消失。"""

    OPEN = "OPEN"  # 未决
    RESOLVED = "RESOLVED"  # 补救项整改完成
    WAIVED = "WAIVED"  # 业主书面豁免
    SETTLED = "SETTLED"  # 到期扣减结案


class RemedyStatus(str, Enum):
    OPEN = "OPEN"  # 待补救
    COMPLETED = "COMPLETED"  # 按期完成并确认
    WAIVED = "WAIVED"  # 业主豁免
    DEDUCTED = "DEDUCTED"  # 到期未完成，按约扣减结案
    VOID = "VOID"  # 随资产退回而终止


class ExpiryAction(str, Enum):
    """补救项到期未完成时的约定处置。"""

    ESCALATE = "ESCALATE"  # 升级：提高升级层级并顺延期限
    DEDUCT = "DEDUCT"  # 扣减：按约定金额扣减并结案
    RETURN = "RETURN"  # 退回：资产退回建设单位


class RepairStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")


@dataclass(frozen=True, slots=True)
class AssetVersion:
    """资产版本：交接时锁定的资产基线。"""

    asset_code: str
    version: str
    installed_at: datetime
    notes: str = ""

    def __post_init__(self) -> None:
        _require_text(self.asset_code, "asset_code")
        _require_text(self.version, "version")


@dataclass(frozen=True, slots=True)
class Obligation:
    """合同义务条目，含服务指标。"""

    code: str
    title: str
    service_level: str

    def __post_init__(self) -> None:
        _require_text(self.code, "code")
        _require_text(self.title, "title")


@dataclass(frozen=True, slots=True)
class ContractRevision:
    """合同版本：同一合同同一时刻只允许一个生效版本。"""

    contract_id: str
    revision: int
    obligations: tuple[Obligation, ...]
    effective_from: datetime
    effective_to: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_text(self.contract_id, "contract_id")
        if self.revision < 1:
            raise ValueError("revision 必须大于零")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to 必须晚于 effective_from")

    def covers(self, moment: datetime) -> bool:
        """判断该版本在给定时刻是否生效。"""
        return self.effective_from <= moment and (self.effective_to is None or moment < self.effective_to)


@dataclass(frozen=True, slots=True)
class WarrantyBoundary:
    """保修边界：范围与起止时间。"""

    scope: str
    start: datetime
    end: datetime
    covers: str = ""

    def __post_init__(self) -> None:
        _require_text(self.scope, "scope")
        if self.end <= self.start:
            raise ValueError("保修结束时间必须晚于开始时间")


@dataclass(frozen=True, slots=True)
class MaintenancePlan:
    """维护计划条目。"""

    item: str
    frequency: str
    next_due: datetime

    def __post_init__(self) -> None:
        _require_text(self.item, "item")
        _require_text(self.frequency, "frequency")


@dataclass(frozen=True, slots=True)
class EmergencyContact:
    """应急联系人。"""

    name: str
    role: str
    phone: str
    priority: int = 1

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.phone, "phone")


@dataclass(frozen=True, slots=True)
class Defect:
    """未决缺陷：关闭必须给出明确途径与原因。"""

    defect_id: str
    description: str
    severity: str
    raised_at: datetime
    status: DefectStatus = DefectStatus.OPEN
    closed_reason: str = ""

    def __post_init__(self) -> None:
        _require_text(self.defect_id, "defect_id")
        _require_text(self.description, "description")

    @property
    def is_open(self) -> bool:
        return self.status is DefectStatus.OPEN

    def close(self, status: DefectStatus, reason: str) -> "Defect":
        """以整改、豁免或扣减方式关闭缺陷，必须记录原因。"""
        if status is DefectStatus.OPEN:
            raise ValueError("关闭缺陷时必须给出关闭状态")
        _require_text(reason, "closed_reason")
        return replace(self, status=status, closed_reason=reason)


@dataclass(frozen=True, slots=True)
class RemedyItem:
    """附条件接收形成的有期限补救项。"""

    remedy_id: str
    defect_id: str
    description: str
    deadline: datetime
    on_expiry: ExpiryAction
    escalation_extension_days: int = 0
    deduction_amount: float = 0.0
    status: RemedyStatus = RemedyStatus.OPEN
    escalation_level: int = 0
    history: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.remedy_id, "remedy_id")
        _require_text(self.defect_id, "defect_id")
        _require_text(self.description, "description")
        if self.on_expiry is ExpiryAction.ESCALATE and self.escalation_extension_days < 1:
            raise ValueError("约定升级的补救项必须给出顺延天数")
        if self.deduction_amount < 0:
            raise ValueError("deduction_amount 不能为负")

    @property
    def is_open(self) -> bool:
        return self.status is RemedyStatus.OPEN

    def is_overdue(self, moment: datetime) -> bool:
        return self.is_open and moment > self.deadline


@dataclass(frozen=True, slots=True)
class HandoverPackage:
    """交接包聚合：资产版本、合同义务、未决缺陷、保修边界、维护计划与应急联系人。"""

    package_id: str
    asset: AssetVersion
    contract_id: str
    builder_party: str
    operator_party: str
    state: PackageState = PackageState.DOC_REVIEW
    warranty: Optional[WarrantyBoundary] = None
    maintenance_plans: tuple[MaintenancePlan, ...] = ()
    emergency_contacts: tuple[EmergencyContact, ...] = ()
    defects: tuple[Defect, ...] = ()
    remedies: tuple[RemedyItem, ...] = ()
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        _require_text(self.package_id, "package_id")
        _require_text(self.contract_id, "contract_id")
        _require_text(self.builder_party, "builder_party")
        _require_text(self.operator_party, "operator_party")

    def evolve(self, **changes: object) -> "HandoverPackage":
        """返回新版本，避免就地改写历史对象。"""
        return replace(self, **changes)

    @property
    def open_defects(self) -> tuple[Defect, ...]:
        return tuple(d for d in self.defects if d.is_open)

    @property
    def open_remedies(self) -> tuple[RemedyItem, ...]:
        return tuple(r for r in self.remedies if r.is_open)

    def defect(self, defect_id: str) -> Defect:
        for item in self.defects:
            if item.defect_id == defect_id:
                return item
        raise DomainError(f"缺陷 {defect_id} 不存在于交接包 {self.package_id}")

    def remedy(self, remedy_id: str) -> RemedyItem:
        for item in self.remedies:
            if item.remedy_id == remedy_id:
                return item
        raise DomainError(f"补救项 {remedy_id} 不存在于交接包 {self.package_id}")


@dataclass(frozen=True, slots=True)
class ResponsibilityLink:
    """责任链节：同一资产任意时刻只有一个未终结链节。"""

    sequence: int
    asset_code: str
    party: str
    reason: str
    effective_at: datetime
    ended_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_text(self.asset_code, "asset_code")
        _require_text(self.party, "party")
        _require_text(self.reason, "reason")

    @property
    def is_current(self) -> bool:
        return self.ended_at is None


@dataclass(frozen=True, slots=True)
class RepairReport:
    """报修单：同一资产同一问题的重复报修归并到同一条责任链节。"""

    report_id: str
    asset_code: str
    problem_key: str
    description: str
    reported_at: datetime
    reported_by: str
    chain_sequence: int
    contract_id: Optional[str]
    revision: Optional[int]
    status: RepairStatus = RepairStatus.OPEN
    repeat_count: int = 0

    def __post_init__(self) -> None:
        _require_text(self.report_id, "report_id")
        _require_text(self.asset_code, "asset_code")
        _require_text(self.problem_key, "problem_key")


@dataclass(frozen=True, slots=True)
class ServiceMetric:
    """历史服务指标：记录时绑定当时生效的合同版本。"""

    asset_code: str
    metric_code: str
    value: float
    recorded_at: datetime
    contract_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class Incident:
    """事故：用于追溯应引用哪一版合同义务。"""

    incident_id: str
    asset_code: str
    occurred_at: datetime
    description: str


@dataclass(frozen=True, slots=True)
class Deduction:
    """扣减记录：补救项到期未完成时按约定产生。"""

    remedy_id: str
    package_id: str
    amount: float
    reason: str
    at: datetime


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """审计事件：接管决定与责任变更的形成过程。"""

    seq: int
    at: datetime
    actor: str
    role: str
    action: str
    target: str
    detail: dict = field(default_factory=dict)
