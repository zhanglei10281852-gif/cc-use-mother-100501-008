"""基础设施运营责任交接的领域错误类型。"""

from __future__ import annotations


class DomainError(Exception):
    """领域规则错误基类，命令行与 API 统一捕获。"""


class ValidationError(DomainError):
    """输入或内容不满足领域约束。"""


class NotFoundError(DomainError):
    """引用的业务对象不存在。"""


class ConflictError(DomainError):
    """业务标识冲突或违反唯一性约束。"""


class StateTransitionError(DomainError):
    """当前状态不允许执行该操作。"""


class PermissionDenied(DomainError):
    """操作主体不具备所需角色。"""
