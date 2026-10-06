"""领域对象与 JSON 可序列化结构之间的通用转换。"""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

from .domain import parse_datetime
from .errors import ValidationError

_UNION_ORIGINS = (Union, types.UnionType)


def to_jsonable(value: Any) -> Any:
    """把领域对象转换为 JSON 可序列化的结构。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def from_jsonable(target_type: Any, data: Any) -> Any:
    """按目标类型还原领域对象，缺失字段回退到默认值。"""
    if target_type is None or target_type is Any:
        return data
    if data is None:
        return None

    origin = get_origin(target_type)
    if origin in _UNION_ORIGINS:
        candidates = [item for item in get_args(target_type) if item is not type(None)]
        if len(candidates) == 1:
            return from_jsonable(candidates[0], data)
        return data
    if origin in (list, tuple, set, frozenset):
        args = get_args(target_type)
        item_type = args[0] if args and args[0] is not Ellipsis else Any
        items = [from_jsonable(item_type, item) for item in data]
        if origin is tuple:
            return tuple(items)
        if origin is list:
            return items
        return origin(items)
    if origin is dict:
        args = get_args(target_type)
        key_type, value_type = (args + (Any, Any))[:2] if args else (str, Any)
        return {from_jsonable(key_type, key): from_jsonable(value_type, item) for key, item in data.items()}

    if isinstance(target_type, type):
        if issubclass(target_type, Enum):
            return data if isinstance(data, target_type) else target_type(data)
        if target_type is datetime:
            return data if isinstance(data, datetime) else parse_datetime(data)
        if target_type is date:
            return data if isinstance(data, date) else date.fromisoformat(data)
        if is_dataclass(target_type):
            return _dataclass_from_dict(target_type, data)
        if target_type in (str, int, float, bool):
            return data
    return data


def _dataclass_from_dict(target_type: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise ValidationError(f"无法从 {type(data).__name__} 还原 {target_type.__name__}")
    hints = get_type_hints(target_type)
    kwargs: dict[str, Any] = {}
    for item in fields(target_type):
        if item.name in data:
            kwargs[item.name] = from_jsonable(hints[item.name], data[item.name])
        elif item.default is MISSING and item.default_factory is MISSING:
            raise ValidationError(f"{target_type.__name__} 缺少字段 {item.name}")
    return target_type(**kwargs)
