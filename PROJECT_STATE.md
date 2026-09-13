# 项目当前状态

本文件是交接事实源；聊天中的承诺和计划不等同于已实现能力。更新日期：2026-09-13。

## 当前阶段

首轮离线工程交付完成：共同契约、完整业务链路、治理工具、演示/校验、确定性回放、双因子共同样本研究及两库备份/恢复均已验证。**完整本地质量检查退出码 0，pytest 117 passed、20 条第三方弃用警告，131.64 秒。** 本轮离线实现没有剩余阻塞；当前验收对应的源码、工具与配置指纹已留存。代码仍是本地未提交工作树；运行清单明确记录 dirty 状态及自有源码哈希，未把初始 README 提交当成完整实现版本。

## 已实现的能力

- Python 3.12、uv 锁定依赖；Linux/POSIX 文件锁；安装后的业务命令使用离线模式。
- 固定合成样本→历史时点/质量/证券身份→动量与低波动→共同横截面评分→市场状态观察→约束目标→风控→持久化意图→独立 FakeBroker→事件/公司行动账务→对账→解释报告。
- 六个 CLI：`demo`、`validate`、`replay`、`research`、`report`、`check`。回放按 intent/order/event/action 的真实交错 journal 重建，报告重生只读。
- 来源记录显式质量必填；UTC、稳定身份、单位、版本和内容封印；当前模型 Schema 与行情/候选/订单六个正反样例。
- 逐语句中文说明、函数类型/docstring、核心依赖矩阵、需求路径、Schema/样例漂移与敏感差异检查；CI 定义和五类 AI 维护提示词。
- 显式契约导出与 SQLite 一致性备份/校验/恢复到新路径工具；不覆盖批准快照，不删除未知旧 Schema，不进行生产迁移。

## 实际验证记录

| 阶段与实际命令 | 已观察结果 | 限制 |
|---|---|---|
| `uv sync --group dev` | Python 3.12.3、uv 0.12.5；实际解析 30 个包、安装 29 个包并生成 uv.lock。 | 这一步需要可用包源；之后的固定样本运行不联网。 |
| `uv run --locked pytest tests/test_governance.py -q` | 维护工具完成后，24 项治理/契约样例/备份测试通过。 | 局部验证，不代表全部项目通过。 |
| `uv run --locked ruff check --fix tools tests/test_governance.py`；`uv run --locked mypy tools tests/test_governance.py` | 当时工具与治理测试的 Ruff 和严格类型检查通过。 | 随后以全量 check 为最终依据。 |
| `uv run --locked python -m tools.export_contracts --write`；`uv run --offline --locked python -m tools.export_contracts --check` | 最新权威 Schema 及六个正反样例生成、只读比较和真实模型接受/拒绝检查通过。 | 更新的是可再生成源码契约文件，不是批准运行快照。 |
| `uv run --locked pytest tests/test_execution.py tests/test_ledger.py -q` | 独立复跑 29 项通过，4 条第三方弃用警告。 | 仅执行/账务集合；警告未屏蔽，依赖未自动升级。 |
| 初次离线集成演示与回放 | 根代理报告实际成功；账户、订单与重算链路进入后续全量验证。 | 早期临时产物不是最终正式交付目录。 |
| 早期研究实验 | 实际产生 4,800 observations、5 folds。 | 旧口径包含较早仅低波动样本；已保留原实验，**不作为最终双因子共同样本验收**。应用现先筛两因子均有效的 253 价格预热后样本，再按 12/3/3 月和末 6 月保留期评估；覆盖率分母未缩小。 |
| 第一次统一 `quant-core check`（[日志](artifacts/checks/check-01.log)） | Ruff 有 1 处 I001 导入排序问题；格式检查、mypy 通过；pytest **1 failed、116 passed、20 条第三方弃用警告**，耗时 129.85 秒。 | 唯一失败为报告重生的全文相等断言；本轮综合检查未通过。 |
| 第二次统一 `quant-core check`（[日志](artifacts/checks/check-02.log)） | **退出码 0**：governance_errors=[]；Ruff 通过；41 files already formatted；mypy 检查 40 个源文件无问题；pytest **117 passed、20 条第三方弃用警告，131.64 秒**。 | 本地源码质量验收通过；不等同远端 CI、正式引擎或实盘验收。 |
| 最终补充只读检查 | `git diff --check` 与 `uv run --offline --locked python -m tools.export_contracts --check` 均通过；正式 replay 再次 validate 成功，CLI report 重生后 cmp 字节一致，正式清单 source_hash 与当前源码仍一致。 | 不包含远端 CI 或发布。 |
| 带 HEAD 基线的独立只读治理检查 | `run_checks` 返回 0 个错误；`sensitive_diff_report` 报告 36 条初始规则/风险/依赖复核项。 | 用户已授权初始工程建设，各负责人执行独立交叉复核；差异报告不是远端人工审批制度。 |

## 已发现问题与修正依据

- 早期治理测试因 `tools` 未在导入路径而收集失败；通过明确仓库测试路径修复。适配器尚未落盘时的局部 mypy 导入错误，在真实模块及类型标记完成后解决。
- 独立固定反例发现的错误单位、账户/数值/订单契约缺口、盘后成交、未来公司行动提前入账、券商 ID 改绑、状态不变量、挂买单限价低估、手续费净值边界、主表资格/报价身份和研究首日错位均已交文件负责人修复并加入回归。
- 第一次统一检查的报告失败来自 `regime.evidence` 字典插入顺序在 JSON 保存时被排序，重生时字符串展示顺序不同。报告现在对 evidence/exclusions 使用 `json.dumps(sort_keys=True)` 固定键顺序；**保留原来的报告全文相等断言**，未降低预期、删除测试或放松风险。
- `test_application` 的 I001 通过整理导入修复。现金、持仓、费用、订单以及当前双因子共同样本研究测试在首次综合 pytest 的其余通过项目中；第二次完整检查现已通过，原全文相等断言得到保留。

## 正式产物与实际命令

以下均已实际成功，采用 `configs/demo.toml`：

```bash
uv run --offline --locked quant-core demo --config configs/demo.toml --output artifacts/demo
uv run --offline --locked quant-core validate --run-dir artifacts/demo
uv run --offline --locked quant-core replay --run-dir artifacts/demo --output artifacts/replay
uv run --offline --locked quant-core research --run-dir artifacts/demo
uv run --offline --locked python -m tools.backup_sqlite backup --source artifacts/demo/internal.sqlite --output artifacts/backups/demo-v1/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite backup --source artifacts/demo/broker.sqlite --output artifacts/backups/demo-v1/broker.sqlite
uv run --offline --locked python -m tools.backup_sqlite restore --source artifacts/backups/demo-v1/internal.sqlite --output artifacts/restored/demo-v1/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite restore --source artifacts/backups/demo-v1/broker.sqlite --output artifacts/restored/demo-v1/broker.sqlite
```

- [正式解释报告](artifacts/demo/report.md)：20 个实际持仓，20 笔订单全部 FILLED，现金 **11,380.072035 USD**，累计费用 **20 USD**，独立对账一致。报告内各目标股数与实际股数一致；本次结构化监控为 HEALTHY。
- [回放核对](artifacts/replay/replay-verification.json)：account、orders、factors、signals、target 比较全部为 true；回放内部账户由初态和交错 journal 重建。
- 两个数据库分别备份到 `artifacts/backups/demo-v1`，恢复到 `artifacts/restored/demo-v1`；完整性均为 ok，源/备份/恢复的逻辑哈希分别一致。SQLite backup API 可以改变物理文件布局，不以字节哈希必须相等代替逻辑一致性验证。
- [正式共同样本研究](artifacts/demo/research/experiment-0001/summary.json)退出码 0：**3,600 observations、2 folds**；目录为 `artifacts/demo/research/experiment-0001`，完整日志为 [research-final.log](artifacts/checks/research-final.log)。使用两因子均有效的 253 价格预热后样本，保留 12/3/3 月滚动与末 6 月 holdout，覆盖率分母仍为全部原始候选。早期 4,800/5 只作为保留的修正前实验，不是当前验收结果。

`artifacts/` 被 Git 忽略，当前工作区保留这些产物，新的检出需要按 README 重新生成。已有产物不可覆盖，重新验证应选择新运行/备份名称。

## 已知限制与阻塞

合成数据和 FakeBroker 只证明工程链路，不证明收益或真实成交表现。正式引擎、授权真实数据回测、完整成本后策略业绩、持续模拟/影子交易及实盘尚未验收；market-intel 仅有候选契约。真实告警通知、值守人、独立发布审批与 GitHub 远端分支保护未配置，远端 CI 尚未实际运行；本地规则和 CI 文件不代表不可绕过的授权机制。

当前第三方弃用警告与 exchange_calendars/NumPy timedelta 兼容性有关，未据此改锁文件或屏蔽警告。生产迁移、跨平台运行和多进程事务存储尚未验收。资金、账户、授权与发布阻塞项见 ASSUMPTIONS.md。

## 最小下一步

先按 [README](README.md) 阅读并在新的输出目录运行示例；新增因子按 [扩展手册](docs/runbooks/extensions.md) 补齐公式、版本、手算样本、时点与回归测试。下一工程阶段是取得正式数据授权并执行 [引擎验收 ADR](docs/adr/0001-offline-engine.md)，不属于修复本轮遗留问题。真实账户、实盘及远端发布仍需各自明确授权和验收。
