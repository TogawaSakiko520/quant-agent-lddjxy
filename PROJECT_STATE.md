# 项目当前状态

本文件是交接事实源；聊天中的承诺和计划不等同于已实现能力。更新日期：2026-09-19。

## 当前阶段：QC-014 第二轮局部上下文增强完成（2026-09-19）

本轮以第一轮尚未提交的工作区为起点，编辑前保存114个文件及SHA-256清单到 `artifacts/readability-context-20260919/baseline/`；保留原Git状态和补丁，没有重置或把上轮改动计为本轮新增。根唯一规范补入关键变量来源/形状/单位、事实/目标/预算区别、调用去向、库用法效果和重要出口；允许在关键位置简短重述完整定义，不恢复逐句门槛。同步SPEC、维护模板、工具子规则、维护手册、代码导读及需求路径。第一轮迁移记录保持原字节，以下第一轮验收正文保留，仅将阶段标题标为历史。

- 先补 application、portfolio、signals、execution/sqlite_store 及对应测试样板，再检查其他自有文件。改善 factors/signals/target/intents衔接、effective与risk_quantities、nav/cash/reserved/allocated/remaining、嵌套/复合索引、model_copy/validate/dump、事务/锁与UNKNOWN/空结果等局部阅读障碍。完整前后示例、具名新读者抽样和实际命令见 [第二轮记录](docs/audit/local-context-readability.md)。
- 所有40个自有Python对**本轮起始工作区快照**的AST一致，仅忽略真正docstring和位置信息，保留普通字符串、类型注释、签名、变量、运算、分支、测试数据与断言。快照所有文件摘要核验通过；治理检查器/治理测试、CI、配置、依赖锁及全部Schema/样例保持起点字节，没有本轮逻辑豁免。
- 当前锁定环境实际 `quant-core check --base 01205ca` 退出0：**133 passed、20 warnings、52.58秒**；治理、Ruff、格式、mypy通过。该Git基线的敏感提示包含上轮未提交成果，未冒称它是第二轮差异；本轮比较证据为 `round-verification.json` 与 `round.diff`。
- 现有契约生成检查 `tools.export_contracts --check` 退出0；业务未显式读取 `__doc__`/`inspect.getdoc`，模型Schema描述来自类docstring。导出的契约类说明未变，生成文件无漂移，未执行 `--write`。已读取锁定Pydantic源码与SQLite上下文说明核对复制/验证/序列化/事务语义，证据 `library-inspection.json`。
- 新目录 demo、validate、replay 均退出0，回放五项全部true。起始源码指纹为 `bfe76e9a5ba554fd23577b917f1824aa81e9519512cb6b691aad42a9660549b2`，本轮清单与当前源码均为 `fdf13598bd0c799221b85059c66f36e889f226101d0a57c9ff7d5b606018f518`，dirty=true；未覆盖第一轮证据或旧运行。
- 不同作者已完成独立AI语义复核，按实际变量定义/表达式/调用/状态出口抽样检查对象、来源、结构单位、变化、去向和失败后果。发现并更正的说明问题包括空folds原因、固定4.5%目标而非候选均分、risk.json不是当前报告输入、record_order本地状态来源等；全部只改说明。作者自检与独立复核分别留证，不以注释数量验收。

本轮未升级或重装依赖，现有正式检查与离线命令均成功；第三方弃用警告及macOS沙箱PyArrow sysctl权限诊断保留。一个作者AST比较器最初将TypeIgnore.lineno位置误算为差异，修正比较器后保留类型忽略标签并通过，未因此改业务代码。远端CI、真实账户、持续Paper及独立research CLI/完整灾恢未验证；已有研究/恢复测试在完整检查中执行。F01/F02/F03及既有边界观察仍未修复。未发送外部交易、发布、部署、提交、推送或修改远端权限。

## 第一轮记录：QC-014 注释规范迁移完成（2026-09-19，历史）

按用户明确授权，将「逐语句中文注释」改为根 AGENTS.md 集中定义的「按业务语义分层解释」。读者为懂基本 Python、不了解项目及交易业务的开发者。规范、子规则、QC-014/需求映射、维护提示词、维护手册、治理 ADR 和代码导读同步；先完成 factors/execution/test_factors 样板，再逐段整理 src/tests/tools。旧审计、旧验收及历史 CHANGELOG 保留当时口径，未来 PR 条款新增替代注记。完整范围、前后示例、测试迁移和命令见 [迁移记录](docs/audit/comment-semantics-migration.md)。

- 开工工作区干净，基线 `01205cae9f3c396938bdcdbe6c6cd4af216f89cb`。除 `tools/governance.py` 的注释检查规则及 `tests/test_governance.py` 对应迁移/新增测试外，全部跟踪 Python（38 个文件）去除真正 docstring/位置后的 AST 与基线一致；普通字符串、签名、控制流、运算、测试输入和业务断言不变。检查器其余函数/常量及无关治理测试亦一致。
- 取消普通语句邻接中文与 else/except/finally 单独注释门槛；保留真正中文模块/类/函数 docstring、完整函数类型、依赖、需求、Schema/样例、敏感差异及全部其他检查，排除集合仍为空。新旧 28 个固定规则样例先 red（旧规则 10 失败）再 green（新规则全部通过）。
- 临时安装历史工具版本 uv 0.12.5，并以 bundled Python 3.12.14 在本地 `.venv` 执行 `uv sync --locked`；依赖版本与 uv.lock 未变。最初缺 uv、沙箱 DNS 不可用和 offline sync 缺缓存均已如实记录，之后受控联网仅用于安装工具/锁定包。未连接真实账户。
- 完整 `quant-core check --base 01205ca` 两次退出 0：首次 **133 passed、20 warnings、55.11 秒**；独立复核修正说明后最终 **133 passed、20 warnings、57.04 秒**。Ruff、格式、mypy、治理均通过；敏感差异提示保留并经跨作者复核，没有伪装成自动批准。最终完整检查后只再修正两条业务测试注释，并以 AST、静态治理及格式检查确认。
- `demo`、`validate`、`replay` 在新目录实际退出 0；回放 account/orders/factors/signals/target 全为 true。`artifacts/comment-semantics-20260919/` 保存日志、AST 核验脚本/结果、新 demo/replay；清单 `dirty=true`，源码指纹 `bfe76e9a5ba554fd23577b917f1824aa81e9519512cb6b691aad42a9660549b2` 与当前源码一致，未覆盖旧快照。
- docstring 使用已检索：业务未显式读取 `__doc__`/`inspect.getdoc`，治理通过 AST 读取，模型生成 Schema 会读取类说明。导出契约模型类 docstring 保持不变；ResearchFold 的类说明虽澄清职责，但不属于既有跨仓库 Schema 导出集合。现有 `tools.export_contracts --check` 两次只读通过，Schema/样例字节、字段、类型、默认值和约束未改，无需重新写生成文件。
- 已完成独立 AI 交叉复核：治理规则/测试、执行与适配器、核心业务模块、所有业务测试及规范文档由不同作者检查。复核纠正了异常类型、状态事件返回含义、百分位并列端点、备份/文件集合原子性、同机锁与测试独立性等说明，未混入业务修复。作者自检与独立复核分开记录。

20 条既有第三方弃用警告未屏蔽；macOS 沙箱下 validate/replay 输出 PyArrow CPU 信息 sysctl 权限警告但退出 0，日志保留。CI 权限、依赖锁、配置、业务公式与交易边界未改；远端 CI、真实账户、持续 Paper 和生产环境仍未验证。本次本地通过不消除 F01/F02 既有缺陷及 F03 碎股缺口。未发布、部署、提交或推送；本轮注释迁移完成，下一业务工程步骤仍按原上线阻塞清单单独安排。

## 上线准备审计记录（2026-09-13，历史）

用户授权按300美元、零付费API任务书审计；本次未修改业务源码、测试、配置、依赖锁、CI或根规则，未访问账户、发送Alpaca Paper/实盘订单、部署或提交/推送。开工工作树干净，分支main、HEAD=`f01a1237bc02deae4957ceecd29639e16037759d`，已有109个跟踪文件；首轮“源码未提交”表述已过时。审计文档写入后，demo/replay清单诚实记录dirty=true，源代码指纹仍一致。

- 内核seccomp禁网、无继承凭据/插件/Git钩子的环境下，实际`uv run --offline --locked quant-core check --base f01a1237bc02deae4957ceecd29639e16037759d`退出0：治理与差异诊断为空，Ruff/格式/mypy通过，**117 passed、20 warnings、130.27秒**；警告未屏蔽、锁未更新。
- 新目录demo、validate、replay均实际退出0；回放account/orders/factors/signals/target五项全部true。证据目录为`artifacts/readiness-audit-20260913`，运行时禁网启动器、日志及独立探针均保留；完整前缀/命令见审计报告。
- 独立探针退出**1**，如实保留三个需求不满足项：F01未来行业主表使当前超限风险判定从拒绝变允许；F02空目标而实际有持仓时监控仅报HEALTHY；F03保持原风险参数的300美元/1000美元股价目标由应需碎股0.0135变0。前两项为已复现业务边界缺陷，第三项为新的碎股要求缺口，非原整股SPEC回归失败。本轮仅审计，尚未修复。
- F01限`risk.assess_order`公共入口实测；`plan_orders`同类路径只静态确认，标准demo使用已过滤主表，没有本次标准demo触发泄漏的证据。两名独立复核者核对了反例输入、手算与日志。
- 新增[准备审计](docs/audit/readiness.md)、[上线阻塞](docs/audit/launch-blockers.md)、[8个后续PR建议](docs/audit/next-prs.md)、[免费Paper路线ADR](docs/adr/free-paper-route.md)。官方权限/规则已重新核实，账户级权限、资金/费用、远端CI、持续Paper与完整灾恢仍未验证；本次未单独运行research CLI，研究/通用备份已有测试包含在117项中。

结论：固定合成离线链路可运行；当前Alpaca只读适配未实现，单次/持续Paper尚不具备条件，有人监督300美元实盘与无人值守实盘均不可上线。最小下一工程步是先修F01/F02，然后按依赖验收隔离适配、数据和碎股；不重复开发已有意图/去重/恢复基础，不因免费路线自动放弃限价保护。首轮“无剩余阻塞”仅是当时离线交付记录，不能覆盖本次新发现。

## 首轮离线交付记录（历史）

首轮离线工程交付完成：共同契约、完整业务链路、治理工具、演示/校验、确定性回放、双因子共同样本研究及两库备份/恢复均已验证。**完整本地质量检查退出码 0，pytest 117 passed、20 条第三方弃用警告，131.64 秒。** 本轮离线实现没有剩余阻塞；当前验收对应的源码、工具与配置指纹已留存。首轮验收时曾为本地未提交工作树，运行清单记录了当时 dirty 状态及自有源码哈希；当前提交状态以上述本次审计为准。

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
