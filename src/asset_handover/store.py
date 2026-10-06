"""交接服务的存储层：内存实现与 JSON 文件实现。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .domain import (
    AuditEvent,
    Contract,
    HandoverPackage,
    Issue,
    ObligationTransfer,
    ResponsibilityLink,
    ServiceMetricRecord,
)
from .serde import from_jsonable, to_jsonable

SCHEMA_VERSION = 1


class Store:
    """内存存储，同时定义持久化数据结构与序列化格式。"""

    def __init__(self) -> None:
        self.packages: dict[str, HandoverPackage] = {}
        self.contracts: dict[str, Contract] = {}
        self.chains: dict[str, list[ResponsibilityLink]] = {}
        self.transfers: dict[str, list[ObligationTransfer]] = {}
        self.issues: dict[str, Issue] = {}
        self.metrics: list[ServiceMetricRecord] = []
        self.audit: list[AuditEvent] = []
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def save(self) -> None:
        """内存实现无需落盘。"""

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "seq": self._seq,
            "packages": [to_jsonable(item) for item in self.packages.values()],
            "contracts": [to_jsonable(item) for item in self.contracts.values()],
            "chains": {key: to_jsonable(value) for key, value in self.chains.items()},
            "transfers": {key: to_jsonable(value) for key, value in self.transfers.items()},
            "issues": [to_jsonable(item) for item in self.issues.values()],
            "metrics": to_jsonable(self.metrics),
            "audit": to_jsonable(self.audit),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        if state.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"不支持的存储格式版本: {state.get('schema_version')!r}")
        self._seq = int(state.get("seq", 0))
        self.packages = {
            item.package_code: item
            for item in (from_jsonable(HandoverPackage, raw) for raw in state.get("packages", []))
        }
        self.contracts = {
            item.contract_code: item
            for item in (from_jsonable(Contract, raw) for raw in state.get("contracts", []))
        }
        self.chains = {
            key: [from_jsonable(ResponsibilityLink, raw) for raw in value]
            for key, value in state.get("chains", {}).items()
        }
        self.transfers = {
            key: [from_jsonable(ObligationTransfer, raw) for raw in value]
            for key, value in state.get("transfers", {}).items()
        }
        self.issues = {
            item.issue_code: item
            for item in (from_jsonable(Issue, raw) for raw in state.get("issues", []))
        }
        self.metrics = [from_jsonable(ServiceMetricRecord, raw) for raw in state.get("metrics", [])]
        self.audit = [from_jsonable(AuditEvent, raw) for raw in state.get("audit", [])]


class JsonFileStore(Store):
    """以 JSON 文件持久化的存储，供命令行跨调用共享状态。"""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self.load_state(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(self.to_state(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)
