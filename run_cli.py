"""基础设施运营责任交接命令行入口。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from asset_handover.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
