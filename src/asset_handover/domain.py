"""基础设施运营责任交接的领域模型。

交接包以单项资产为单位，聚合资产版本、合同义务基线、未决缺陷、保修边界、
维护计划和应急联系人，并按 资料核验 → 现场验收 → 条件接收 → 正式接管
的状态机推进。所有实体均为不可变对象，任何变更都通过生成新版本完成，
历史状态只可追溯、不可改写。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

from .contracts import stable_fingerprint
from .errors import ValidationError


def parse_datetime(value: str) -> datetime:
    """解析 ISO 时间字符串，缺省时区按 UTC 处理。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _require_text(field_name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")


def _coerce_enum(field_name: str, enum_type: type[Enum], value: Any) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError:
        allowed = "、".join(member.value for member in enum_type)
        raise ValueError(f"{field_name} 必须是 {allowed} 之一") from None


class PackageState(str, Enum):
    """交接包状态机：资料核验 → 现场验收 → 条件接收 → 正式接管。"""

    DRAFT = "DRAFT"  # 草拟
    DOC_REVIEW = "DOC_REVIEW"  # 资料核验
    SITE_INSPECTION = "SITE_INSPECTION"  # 现场验收
    CONDITIONAL_ACCEPTANCE = "CONDITIONAL_ACCEPTANCE"  # 条件接收
    FORMAL_TAKEOVER = "FORMAL_TAKEOVER"  # 正式接管
    RETURNED = "RETURNED"  # 退回建设单位


class DefectStatus(str, Enum):
    OPEN = "OPEN"  # 未决
    RESOLVED = "RESOLVED"  # 已整改
    WAIVED = "WAIVED"  # 已豁免


class RemediationStatus(str, Enum):
    OPEN = "OPEN"  # 待补救
    ESCALATED = "ESCALATED"  # 已升级，等待重新约定期限
    COMPLETED = "COMPLETED"  # 已完成
    WAIVED = "WAIVED"  # 已豁免
    RETURNED = "RETURNED"  # 随交接包退回


class OverduePolicy(str, Enum):
    """补救项到期未完成时的约定处置方式。"""

    ESCALATE = "ESCALATE"  # 升级
    DEDUCT = "DEDUCT"  # 扣减
    RETURN = "RETURN"  # 退回


class ObligationType(str, Enum):
    DEFECT_RECTIFICATION = "DEFECT_RECTIFICATION"  # 缺陷整改
    SPARE_PARTS = "SPARE_PARTS"  # 备件承诺
    SERVICE_LEVEL = "SERVICE_LEVEL"  # 服务指标
    WARRANTY_DUTY = "WARRANTY_DUTY"  # 保修义务
    OTHER = "OTHER"  # 其他


class ChainReason(str, Enum):
    """责任链环节的成因。"""

    REGISTRATION = "REGISTRATION"  # 交接包登记，建设单位负责
    TAKEOVER = "TAKEOVER"  # 正式接管，运营企业负责
    OPERATOR_CHANGE = "OPERATOR_CHANGE"  # 运营主体变更


class IssueStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Role(str, Enum):
    """操作主体角色，确认与豁免等关键动作按角色授权。"""

    OWNER_REP = "OWNER_REP"  # 业主代表
    CONSTRUCTOR_REP = "CONSTRUCTOR_REP"  # 建设单位代表
    OPERATOR_REP = "OPERATOR_REP"  # 运营企业代表
    ACCEPTANCE_LEAD = "ACCEPTANCE_LEAD"  # 验收组长


@dataclass(frozen=True)
class Actor:
    """操作主体及其角色集合，权限校验以角色为准。"""

    actor_id: str
    name: str
    roles: tuple[Role, ...]

    def __post_init__(self) -> None:
        _require_text("actor_id", self.actor_id)
        _require_text("name", self.name)
        roles = tuple(_coerce_enum("roles", Role, role) for role in self.roles)
        if not roles:
            raise ValueError("roles 不能为空")
        object.__setattr__(self, "roles", roles)


@dataclass(frozen=True)
class AssetVersion:
    """移交时刻的资产版本快照。"""

    asset_code: str
    version: str
    name: str
    category: str
    location: str
    attributes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ("asset_code", "version", "name", "category", "location"):
            _require_text(key, getattr(self, key))
        object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class Obligation:
    """合同义务条目：缺陷整改、备件承诺、服务指标等，随合同版本生效。"""

    obligation_code: str
    contract_code: str
    obligation_type: ObligationType
    description: str
    owner_party: str
    indicator: str = ""
    target: str = ""
    unit: str = ""

    def __post_init__(self) -> None:
        for key in ("obligation_code", "contract_code", "description", "owner_party"):
            _require_text(key, getattr(self, key))
        object.__setattr__(
            self, "obligation_type", _coerce_enum("obligation_type", ObligationType, self.obligation_type)
        )


@dataclass(frozen=True)
class ContractRevision:
    """合同的一个不可变版本，历史服务指标与事故义务均绑定到具体版本。"""

    contract_code: str
    revision: str
    effective_from: datetime
    obligations: tuple[Obligation, ...] = ()

    def __post_init__(self) -> None:
        _require_text("contract_code", self.contract_code)
        _require_text("revision", self.revision)
        obligations = tuple(self.obligations)
        for obligation in obligations:
            if obligation.contract_code != self.contract_code:
                raise ValueError(f"义务 {obligation.obligation_code} 不属于合同 {self.contract_code}")
        object.__setattr__(self, "obligations", obligations)


@dataclass(frozen=True)
class Contract:
    """合同及其按生效时间排序的版本序列。"""

    contract_code: str
    title: str
    revisions: tuple[ContractRevision, ...] = ()

    def __post_init__(self) -> None:
        _require_text("contract_code", self.contract_code)
        _require_text("title", self.title)
        ordered = tuple(sorted(self.revisions, key=lambda item: item.effective_from))
        object.__setattr__(self, "revisions", ordered)

    def revision_at(self, at: datetime) -> ContractRevision:
        """返回指定时刻生效的合同版本。"""
        eligible = [item for item in self.revisions if item.effective_from <= at]
        if not eligible:
            raise ValidationError(f"合同 {self.contract_code} 在 {at.isoformat()} 尚无有效版本")
        return eligible[-1]


@dataclass(frozen=True)
class Defect:
    """验收中发现的缺陷，关闭只能来自整改确认或业主豁免。"""

    defect_code: str
    description: str
    severity: str
    found_at: datetime
    status: DefectStatus = DefectStatus.OPEN
    resolution_note: str = ""
    resolved_at: Optional[datetime] = None
    waived_reason: str = ""

    def __post_init__(self) -> None:
        for key in ("defect_code", "description", "severity"):
            _require_text(key, getattr(self, key))
        object.__setattr__(self, "status", _coerce_enum("status", DefectStatus, self.status))


@dataclass(frozen=True)
class WarrantyBoundary:
    """保修边界：范围、责任方与有效期限。"""

    warranty_code: str
    scope: str
    responsible_party: str
    valid_from: date
    valid_until: date
    exclusions: str = ""

    def __post_init__(self) -> None:
        for key in ("warranty_code", "scope", "responsible_party"):
            _require_text(key, getattr(self, key))
        if self.valid_from > self.valid_until:
            raise ValueError("valid_from 不能晚于 valid_until")


@dataclass(frozen=True)
class MaintenanceTask:
    task_code: str
    name: str
    interval_days: int
    procedure: str = ""

    def __post_init__(self) -> None:
        _require_text("task_code", self.task_code)
        _require_text("name", self.name)
        if not isinstance(self.interval_days, int) or self.interval_days < 1:
            raise ValueError("interval_days 必须大于零")


@dataclass(frozen=True)
class MaintenancePlan:
    plan_code: str
    responsible_party: str
    tasks: tuple[MaintenanceTask, ...] = ()

    def __post_init__(self) -> None:
        _require_text("plan_code", self.plan_code)
        _require_text("responsible_party", self.responsible_party)
        tasks = tuple(self.tasks)
        if not tasks:
            raise ValueError("维护计划至少包含一项任务")
        object.__setattr__(self, "tasks", tasks)


@dataclass(frozen=True)
class EmergencyContact:
    name: str
    role: str
    phone: str
    priority: int = 1

    def __post_init__(self) -> None:
        for key in ("name", "role", "phone"):
            _require_text(key, getattr(self, key))
        if not isinstance(self.priority, int) or self.priority < 1:
            raise ValueError("priority 必须大于零")


@dataclass(frozen=True)
class Deduction:
    """一次逾期扣减记录，扣减不免除补救义务。"""

    amount: float
    reason: str
    applied_at: datetime
    actor_id: str

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError("amount 必须大于零")
        _require_text("reason", self.reason)
        _require_text("actor_id", self.actor_id)


@dataclass(frozen=True)
class RemediationItem:
    """附条件接收形成的有期限补救项，到期未完成按约定升级、扣减或退回。"""

    item_code: str
    description: str
    deadline: datetime
    policy: OverduePolicy
    policy_param: str = ""
    linked_defects: tuple[str, ...] = ()
    status: RemediationStatus = RemediationStatus.OPEN
    escalation_level: int = 0
    deductions: tuple[Deduction, ...] = ()
    completed_at: Optional[datetime] = None
    completion_note: str = ""
    waived_reason: str = ""

    def __post_init__(self) -> None:
        _require_text("item_code", self.item_code)
        _require_text("description", self.description)
        object.__setattr__(self, "policy", _coerce_enum("policy", OverduePolicy, self.policy))
        object.__setattr__(self, "status", _coerce_enum("status", RemediationStatus, self.status))
        object.__setattr__(self, "linked_defects", tuple(self.linked_defects))
        object.__setattr__(self, "deductions", tuple(self.deductions))


@dataclass(frozen=True)
class HandoverPackage:
    """单项资产的交接包，是责任交接流程的聚合根。"""

    package_code: str
    project_code: str
    asset: AssetVersion
    contract_code: str
    constructor_party: str
    operator_party: str
    baseline_revision: Optional[str] = None
    state: PackageState = PackageState.DRAFT
    warranties: tuple[WarrantyBoundary, ...] = ()
    maintenance_plan: Optional[MaintenancePlan] = None
    contacts: tuple[EmergencyContact, ...] = ()
    defects: tuple[Defect, ...] = ()
    remediations: tuple[RemediationItem, ...] = ()
    created_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        for key in ("package_code", "project_code", "contract_code", "constructor_party", "operator_party"):
            _require_text(key, getattr(self, key))
        object.__setattr__(self, "state", _coerce_enum("state", PackageState, self.state))
        for key in ("warranties", "contacts", "defects", "remediations"):
            object.__setattr__(self, key, tuple(getattr(self, key)))

    def evolve(self, **changes: Any) -> "HandoverPackage":
        """返回新版本，避免就地改写历史对象。"""
        return replace(self, **changes)

    def fingerprint(self) -> str:
        """生成稳定摘要，供幂等和审计使用。"""
        return stable_fingerprint(asdict(self))

    def open_defects(self) -> tuple[Defect, ...]:
        return tuple(defect for defect in self.defects if defect.status is DefectStatus.OPEN)

    def open_remediations(self) -> tuple[RemediationItem, ...]:
        return tuple(
            item
            for item in self.remediations
            if item.status in (RemediationStatus.OPEN, RemediationStatus.ESCALATED)
        )

    def find_remediation(self, item_code: str) -> RemediationItem:
        for item in self.remediations:
            if item.item_code == item_code:
                return item
        raise KeyError(item_code)

    def find_defect(self, defect_code: str) -> Defect:
        for defect in self.defects:
            if defect.defect_code == defect_code:
                return defect
        raise KeyError(defect_code)


@dataclass(frozen=True)
class ResponsibilityLink:
    """资产责任链的一个环节，追加式记录保证任意时刻责任主体唯一。"""

    seq: int
    asset_code: str
    party: str
    effective_from: datetime
    reason: ChainReason
    note: str
    actor_id: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        _require_text("asset_code", self.asset_code)
        _require_text("party", self.party)
        object.__setattr__(self, "reason", _coerce_enum("reason", ChainReason, self.reason))


@dataclass(frozen=True)
class ObligationTransfer:
    """义务转让记录，转让前后同一义务只能有一个承担方。"""

    obligation_code: str
    from_party: str
    to_party: str
    effective_from: datetime
    note: str
    actor_id: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        for key in ("obligation_code", "from_party", "to_party"):
            _require_text(key, getattr(self, key))


@dataclass(frozen=True)
class IncidentReport:
    """一次事故报修，记录事发时刻的责任主体与合同版本快照。"""

    report_code: str
    occurred_at: datetime
    reported_at: datetime
    reporter_id: str
    description: str
    responsible_party: str
    contract_code: str
    contract_revision: str


@dataclass(frozen=True)
class Issue:
    """同一问题归集后的事故单，重复报修追加到同一责任链下。"""

    issue_code: str
    asset_code: str
    signature: str
    description: str
    first_occurred_at: datetime
    status: IssueStatus = IssueStatus.OPEN
    reports: tuple[IncidentReport, ...] = ()

    def __post_init__(self) -> None:
        for key in ("issue_code", "asset_code", "signature", "description"):
            _require_text(key, getattr(self, key))
        object.__setattr__(self, "status", _coerce_enum("status", IssueStatus, self.status))
        object.__setattr__(self, "reports", tuple(self.reports))


@dataclass(frozen=True)
class ServiceMetricRecord:
    """服务指标记录，绑定记录时刻生效的合同版本。"""

    contract_code: str
    revision: str
    indicator: str
    value: float
    unit: str
    recorded_at: datetime
    actor_id: str

    def __post_init__(self) -> None:
        for key in ("contract_code", "revision", "indicator"):
            _require_text(key, getattr(self, key))


@dataclass(frozen=True)
class AuditEvent:
    """审计事件，接管决定等关键结论由完整事件序列支撑。"""

    seq: int
    at: datetime
    actor_id: str
    action: str
    entity_type: str
    entity_code: str
    details: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ("actor_id", "action", "entity_type", "entity_code"):
            _require_text(key, getattr(self, key))
        object.__setattr__(self, "details", {str(k): str(v) for k, v in self.details.items()})
