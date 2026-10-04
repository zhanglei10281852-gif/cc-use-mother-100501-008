"""基础设施运营责任交接命令行冒烟入口。"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from asset_handover import HandoverPackage


def main() -> None:
    item = HandoverPackage(package_code='package-code-001', asset_code='asset-code-001', contract_revision='contract-revision-001', state='state-001')
    print(json.dumps({"item": asdict(item), "fingerprint": item.fingerprint()}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
