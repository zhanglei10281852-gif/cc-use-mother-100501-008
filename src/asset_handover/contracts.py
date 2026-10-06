"""基础设施运营责任交接的基础契约工具。

提供稳定摘要与身份去重能力，供领域对象、幂等校验和审计追踪复用。
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Iterable, TypeVar

T = TypeVar("T")


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"无法序列化的类型: {type(value)!r}")


def stable_fingerprint(payload: Any) -> str:
    """生成稳定摘要，供幂等和审计使用。"""
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return sha256(text.encode("utf-8")).hexdigest()


def _fingerprint_of(item: Any) -> str:
    fingerprint = getattr(item, "fingerprint", None)
    if callable(fingerprint):
        return fingerprint()
    if is_dataclass(item) and not isinstance(item, type):
        return stable_fingerprint(asdict(item))
    return stable_fingerprint(item)


def unique_by_identity(items: Iterable[T], key_attr: str = "package_code") -> list[T]:
    """按业务标识去重，并拒绝同标识不同内容。"""
    found: dict[str, T] = {}
    for item in items:
        key = str(getattr(item, key_attr))
        previous = found.get(key)
        if previous is not None and _fingerprint_of(previous) != _fingerprint_of(item):
            raise ValueError(f"业务标识 {key} 对应的内容发生冲突")
        found[key] = item
    return [found[key] for key in sorted(found)]
