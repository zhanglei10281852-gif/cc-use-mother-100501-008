"""基础设施运营责任交接命令行冒烟入口。

先打印基础契约对象的稳定摘要，再演示一条完整的交接主线：
建包（含缺陷）→ 资料核验 → 附条件接收 → 补救完成 → 正式接管，
最后输出业主视角的三个关键查询结果。
"""

import json
import sys
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from asset_handover.contracts import HandoverPackage as ContractHandoverPackage
from asset_handover.domain import AssetVersion, Role, now_utc
from asset_handover.serialization import to_jsonable
from asset_handover.service import HandoverService


def main() -> None:
    item = ContractHandoverPackage(
        package_code="package-code-001",
        asset_code="asset-code-001",
        contract_revision="contract-revision-001",
        state="state-001",
    )
    print(json.dumps({"item": asdict(item), "fingerprint": item.fingerprint()}, ensure_ascii=False, sort_keys=True))

    service = HandoverService()
    package = service.create_package(
        "demo-owner",
        Role.OWNER,
        asset=AssetVersion(asset_code="pump-001", version="v1.0", installed_at=now_utc()),
        contract_id="contract-001",
        defects=[{"description": "阀门渗漏", "severity": "一般"}],
    )
    service.confirm_document_review("demo-inspector", Role.INSPECTOR, package.package_id)
    defect = package.defects[0]
    package = service.accept_with_conditions(
        "demo-owner",
        Role.OWNER,
        package.package_id,
        [
            {
                "defect_id": defect.defect_id,
                "deadline": now_utc() + timedelta(days=7),
                "on_expiry": "ESCALATE",
                "escalation_extension_days": 3,
            }
        ],
    )
    remedy = package.remedies[0]
    service.confirm_remedy_completed("demo-inspector", Role.INSPECTOR, package.package_id, remedy.remedy_id, "复验通过")
    service.confirm_takeover("demo-owner", Role.OWNER, package.package_id)

    summary = {
        "responsibility": service.asset_responsibility("pump-001"),
        "unmet_conditions": service.unmet_conditions(package.package_id),
        "decision_trail": [event.action for event in service.takeover_decision_trail(package.package_id)],
    }
    print(json.dumps(to_jsonable(summary), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
