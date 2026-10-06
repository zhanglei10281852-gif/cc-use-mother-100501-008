# 基础设施运营责任交接

本项目提供一套完整的基础设施运营责任交接服务，用于政府投资项目从建设单位移交长期运营企业的场景。服务把资产版本、合同义务、未决缺陷、保修边界、维护计划和应急联系人归入交接包，按 **资料核验 → 现场验收 → 条件接收 → 正式接管** 推进，并保证：

- 任何附条件接收都形成**有期限的补救项**；到期未完成时按约定**升级、扣减或退回**，资产不会被静默视为无缺陷；
- 运营主体变更、义务转让与同一问题的重复报修都挂在**唯一责任链**上，任意时刻每项资产、每项义务只有一个责任主体；
- 历史服务指标与事故义务始终绑定**当时生效的合同版本**；
- 确认与豁免等关键动作只能由具备相应角色的主体执行，全程留痕，接管决定可回放。

仓库不依赖浏览器、外部数据库或其他运行服务：领域核心为纯 Python，存储提供内存与 JSON 文件两种实现，对外提供 Python API（`HandoverService`）与命令行两种使用方式。

## 运行环境

- Python 3.11 或更高版本
- Linux、macOS 或 Windows

## 运行测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 编译检查

```bash
python3 -m compileall -q src tests run_cli.py
```

## 命令行冒烟

```bash
python3 run_cli.py --store /tmp/demo.json demo
```

演示命令在空存储上跑通完整场景：登记合同与义务基线 → 组包提交 → 资料核验 → 现场验收发现缺陷 → 附条件接收形成补救项 → 重复报修归集 → 指标记录 → 逾期扣减与升级 → 补救完成 → 正式接管 → 运营主体变更与义务转让 → 责任主体 / 事故义务版本 / 接管轨迹查询。

## 模块结构

| 模块 | 职责 |
| --- | --- |
| `contracts.py` | 稳定摘要（`stable_fingerprint`）与身份去重（`unique_by_identity`）基础工具 |
| `domain.py` | 不可变领域模型：交接包、缺陷、补救项、合同版本、责任链、事故单、指标、审计事件与角色枚举 |
| `errors.py` | 领域错误类型（权限、状态流转、冲突、校验等） |
| `serde.py` | 领域对象与 JSON 结构的双向转换 |
| `store.py` | 存储层：内存 `Store` 与 JSON 文件 `JsonFileStore` |
| `services.py` | 应用服务 `HandoverService`：全部用例、状态机、权限校验与审计 |
| `cli.py` | 命令行入口，所有变更与查询以子命令暴露，输出 JSON |

## 交接包状态机

```
DRAFT → DOC_REVIEW → SITE_INSPECTION → CONDITIONAL_ACCEPTANCE → FORMAL_TAKEOVER
                          │                    │
                          └──（无未决缺陷）──────┴──（补救项全部完成/豁免）──┘
                          CONDITIONAL_ACCEPTANCE ──（退回政策触发）──→ RETURNED
```

关键规则：

- 提交资料核验前，交接包必须聚合保修边界、维护计划与应急联系人，并快照当时生效的合同版本作为义务基线；
- 附条件接收时，每项条件生成带期限与逾期政策（升级 / 扣减 / 退回）的补救项，且**所有未决缺陷必须被补救项覆盖**；
- 补救项只能被验收组长确认完成、或被业主代表说明理由后豁免；逾期处置只会升级、扣减或退回，**不会**自动关闭；
- 正式接管要求未决缺陷与未完成补救项清零，接管瞬间责任链从建设单位切换到运营企业。

## 角色与权限

| 动作 | 角色 |
| --- | --- |
| 登记合同 / 版本、组包、提交、接管、豁免、逾期处置、主体变更、义务转让 | 业主代表 `OWNER_REP` |
| 资料核验确认、缺陷登记、附条件接收、补救完成确认、重新约定期限、缺陷整改确认 | 验收组长 `ACCEPTANCE_LEAD` |
| 服务指标记录 | 运营企业代表 / 业主代表 |
| 事故报修 | 任一具备角色的主体 |

## 责任链与版本绑定

- 资产责任链自交接包登记起由建设单位负责，正式接管切换至运营企业，运营主体变更以追加环节方式记录，新环节生效时间必须晚于上一环节；
- 义务转让校验转让时点的当前持有方，转让前后同一义务只有一个承担方；
- 同一问题的重复报修按 `资产 + 问题特征` 归集到同一事故单，每次报修快照事发时刻的责任主体与合同版本；
- 服务指标按记录时刻绑定合同版本，事故应引用哪版义务由 `incident-context` 查询给出。

## 命令行用法

全局参数：`--store`（JSON 存储文件，默认 `handover_store.json`）、`--actor-id` / `--actor-name` / `--role`（可重复，缺省 `OWNER_REP`）、`--now`（ISO 时间，便于对账与演示）。

```bash
# 登记合同与义务版本
python3 run_cli.py register-contract --contract-code HT-001 --title 泵站运营合同
python3 run_cli.py register-revision --contract-code HT-001 --revision R1 \
  --effective-from 2026-01-01T00:00:00+00:00 --obligations @obligations.json

# 组包并推进流程
python3 run_cli.py create-package --data @package.json
python3 run_cli.py submit --package PKG-001
python3 run_cli.py --role ACCEPTANCE_LEAD confirm-documents --package PKG-001
python3 run_cli.py --role ACCEPTANCE_LEAD add-defect --package PKG-001 \
  --defect-code DF-01 --description 闸门渗漏 --severity 严重
python3 run_cli.py --role ACCEPTANCE_LEAD accept-conditional --package PKG-001 \
  --conditions @conditions.json
python3 run_cli.py process-overdue --package PKG-001 --now 2026-03-05T09:00:00+00:00
python3 run_cli.py take-over --package PKG-001

# 业主查询
python3 run_cli.py asset-responsibility --asset PS-001            # 每项资产由谁负责
python3 run_cli.py pending-conditions --package PKG-001           # 哪些条件尚未满足
python3 run_cli.py incident-context --issue PS-001::gate-leak     # 事故应引用哪版义务
python3 run_cli.py takeover-trail --package PKG-001               # 接管决定如何形成
```

复杂 JSON 入参支持三种形式：内联字符串、`@文件路径`、`-`（标准输入）。

## Python API 用法

```python
from asset_handover import (
    Actor, HandoverPackage, HandoverService, JsonFileStore, Role, RemediationItem,
    OverduePolicy, parse_datetime,
)

service = HandoverService(JsonFileStore("handover_store.json"))
owner = Actor("owner-01", "业主代表", (Role.OWNER_REP,))
lead = Actor("lead-01", "验收组长", (Role.ACCEPTANCE_LEAD,))

service.create_package(owner, package, now=parse_datetime("2026-01-10T09:00:00+00:00"))
service.submit_package(owner, "PKG-001")
service.confirm_documents(lead, "PKG-001")
service.accept_conditionally(lead, "PKG-001", [
    RemediationItem("RM-01", "修复闸门渗漏", parse_datetime("2026-03-01T00:00:00+00:00"),
                    OverduePolicy.DEDUCT, "20000", ("DF-01",)),
])
service.process_overdue(owner, "PKG-001")          # 到期未完成 → 按约定扣减
service.complete_remediation(lead, "PKG-001", "RM-01")
service.take_over(owner, "PKG-001")                # 责任链切换至运营企业

service.asset_responsibility("PS-001")             # 当前责任主体与完整责任链
service.pending_conditions("PKG-001")              # 未满足的补救项与未决缺陷
service.incident_context("PS-001::gate-leak")      # 事故应引用的义务版本
service.takeover_trail("PKG-001")                  # 接管决定的审计轨迹
```
