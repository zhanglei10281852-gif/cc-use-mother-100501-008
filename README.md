# 基础设施运营责任交接

本项目提供一套完整的基础设施运营责任交接服务：政府投资项目从建设单位移交给长期运营企业时，把资产版本、合同义务、未决缺陷、保修边界、维护计划和应急联系人归入交接包，按**资料核验 → 现场验收 → 条件接收 → 正式接管**推进，全程留痕、责任可追。仓库只依赖 Python 标准库，不依赖浏览器、外部数据库或其他运行服务。

## 核心规则

- **交接包状态机**：`DOC_REVIEW`（资料核验）→ `SITE_ACCEPTANCE`（现场验收）→ `CONDITIONAL_ACCEPTANCE`（条件接收）→ `FORMAL_TAKEOVER`（正式接管）；处置不当时进入 `RETURNED`（退回建设单位）。不允许跳级。
- **附条件接收**：每个未决缺陷都必须形成有期限的补救项（`RemedyItem`），并约定到期未完成的处置方式——`ESCALATE`（升级并顺延）、`DEDUCT`（按约扣减结案）或 `RETURN`（退回建设单位）。到期由 `process-expired` 按约定自动处置。
- **不允许静默视为无缺陷**：存在未决缺陷或未关闭补救项时，正式接管一律被拒绝；缺陷只能经整改完成、业主书面豁免或扣减结案关闭，全程留审计事件。
- **唯一责任链**：每个资产任意时刻只有一个责任主体。建包时挂在建设单位，正式接管后转到运营企业，退回则回到建设单位；运营主体变更与义务转让都通过关闭当前链节、追加新链节完成，历史完整保留。
- **重复报修归并**：同一资产同一问题（`problem_key`）的重复报修归并到同一报修单与同一责任链节，只累计次数，不产生第二条责任线。
- **合同版本绑定**：同一合同同一时刻只有一个生效版本；历史服务指标记录时绑定当时生效的版本，事故可追溯发生时刻应引用哪一版义务。
- **角色权限**：`OWNER`（业主代表）才能确认接管、条件接收、豁免缺陷/补救项、变更责任主体；`INSPECTOR`（验收工程师）可确认资料核验与补救完成；任何角色都可报修、登记事故。

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
python3 run_cli.py
```

输出基础契约对象的稳定摘要，以及一条完整交接主线（建包 → 核验 → 条件接收 → 补救完成 → 正式接管）的业主视角查询结果。

## 命令行

状态保存在 JSON 文件中（`--state` 或环境变量 `HANDOVER_STATE`），操作类命令执行后自动保存：

```bash
export PYTHONPATH=src

# 建立交接包（含未决缺陷），责任初始挂在建设单位
python3 -m asset_handover.cli --actor 王业主 --role OWNER create-package \
    --asset-code pump-001 --asset-version v1.0 --contract-id contract-001 \
    --defects '[{"description": "阀门渗漏"}]'

# 资料核验 → 附条件接收（为每个缺陷形成有期限补救项）
python3 -m asset_handover.cli --actor 李工 --role INSPECTOR confirm-documents --package pkg-0001
python3 -m asset_handover.cli --actor 王业主 --role OWNER accept-conditional --package pkg-0001 \
    --remedies '[{"defect_id": "def-0002", "deadline": "2026-10-20T00:00:00+08:00", "on_expiry": "DEDUCT", "deduction_amount": 8000}]'

# 业主随时查询
python3 -m asset_handover.cli responsibility --asset pump-001        # 每项资产由谁负责
python3 -m asset_handover.cli unmet-conditions --package pkg-0001    # 哪些条件尚未满足
python3 -m asset_handover.cli decision-trail --package pkg-0001      # 接管决定如何形成
python3 -m asset_handover.cli incident-obligations --incident inc-0007  # 事故应引用哪版义务
```

其他命令：`add-defect`、`confirm-remedy`、`waive-remedy`、`waive-defect`、`process-expired`、`return-package`、`transfer`（运营主体变更/义务转让）、`report-repair`、`close-repair`、`add-revision`、`add-metric`、`record-incident`、`packages`、`deductions`、`serve`。安装后也可使用 `asset-handover` 入口。

## HTTP API

```bash
python3 -m asset_handover.cli serve --port 8080
```

操作人与角色通过请求头 `X-Actor` / `X-Role` 传入。主要端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/assets/{code}/responsibility` | 资产当前责任主体与完整责任链 |
| GET | `/packages/{id}/unmet-conditions` | 尚未满足的交接条件（含逾期补救项） |
| GET | `/packages/{id}/decision-trail` | 接管决定的形成过程 |
| GET | `/incidents/{id}/obligations` | 事故应引用的合同版本与义务 |
| GET | `/packages/{id}` `/packages` `/packages/{id}/deductions` | 交接包与扣减记录 |
| POST | `/packages` `/packages/{id}/defects` | 建包、登记缺陷 |
| POST | `/packages/{id}/confirm-documents` `/accept-with-conditions` `/confirm-takeover` `/return` `/process-expired` | 状态推进与到期处置 |
| POST | `/packages/{id}/remedies/{rid}/confirm-completed` `/waive`、`/packages/{id}/defects/{did}/waive` | 补救确认与豁免 |
| POST | `/assets/{code}/transfer` `/repairs` `/metrics`、`/contracts/{id}/revisions`、`/incidents` | 责任变更、报修、指标、合同版本、事故 |

## 代码结构

- `src/asset_handover/contracts.py` — 基础契约（稳定标识、不可变版本、内容摘要、冲突检测）
- `src/asset_handover/domain.py` — 领域模型：交接包聚合、补救项、责任链节、报修单、合同版本、审计事件
- `src/asset_handover/service.py` — 应用服务：状态机、角色权限、到期处置、唯一责任链、查询
- `src/asset_handover/serialization.py` — JSON 快照与还原
- `src/asset_handover/api.py` — 标准库 HTTP JSON API
- `src/asset_handover/cli.py` — 命令行入口
