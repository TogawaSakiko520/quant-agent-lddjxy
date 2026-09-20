# QC-014 全仓可读性核验与局部补齐

> 历史记录：以下结论、路径及授权仅适用于文中日期和基线；后续实现与验证见[当前状态](../../PROJECT_STATE.md)。本地 `artifacts/` 证据未随 Git 分发，部分旧产物可能已不可用。

日期：2026-09-19。审查及实施起点为提交 `9f0835be08812b4233f548788a72d18a6fb1a5d9` 的干净工作区。唯一规范继续使用根 [AGENTS.md](../../AGENTS.md#按业务语义分层解释qc-014唯一主规范)，不修改规范、不重做前两轮迁移、不增加注释密度门槛。

## 范围、依据与起始证据

只读审查覆盖 `src`、`tests`、`tools` 中全部40个自有Python文件，逐文件读取模块、类、函数及相应调用/测试；代码中322个类/函数定义仅表示审查对象规模，不是可读性指标。整体已有较充分的局部上下文，针对发现的具体缺口补齐，充分说明原样保留。

主要需求QC-014；QC-001核对docstring与Schema关系，QC-012/013涉及新源码指纹和离线证据，QC-015涉及独立复核。注释之外的逻辑、签名、变量名、数据结构、公式、配置、异常行为、测试输入与断言均不变，不修业务缺陷。用户单独确认允许更正治理测试的一句错误注释；这不授权改变检查器或测试规则。

编辑前将工作区115个文件复制到 `artifacts/readability-compliance-20260919/baseline/`，以 `baseline-manifest.json` 保存逐文件SHA-256、HEAD和代码身份；`start-status.txt`及`start-diff.patch`记录起始干净状态。所有本轮比较使用这份快照，不仅依赖HEAD。证据目录被Git忽略，新检出需要重新生成；前两轮记录和产物不覆盖。

## 逐文件审查清单

“保留”表示本次逐函数审阅未发现需要补齐的局部说明；“补齐”对应已确认的阅读缺口，不意味着重写整个文件。

| 文件 | 本轮处理与阅读依据 |
|---|---|
| src/quant_core/__init__.py | 保留：导入无交易副作用与包职责清楚。 |
| src/quant_core/adapters/__init__.py | 保留：外部边界职责清楚。 |
| src/quant_core/adapters/calendar.py | 保留：日期范围、UTC、半日市、时段端点及固定时钟清楚。 |
| src/quant_core/adapters/datasets.py | 补齐：交易日序号、前收盘至开盘与开盘至收盘两个收益口径。 |
| src/quant_core/adapters/fake_broker.py | 补齐：新单处理结果落库后超时、_book写入而非返回快照、公司行动与订单/成交流分离。 |
| src/quant_core/adapters/sqlite_store.py | 补齐：写入事务与初始化建表的原子性范围区别。 |
| src/quant_core/adapters/storage.py | 补齐：两项数据库事实读取用于停写产物，不承诺并发一致快照。 |
| src/quant_core/application.py | 补齐：replay_journal自身契约错误与传播的模型/存储错误。其余阶段、变量和产物去向保留。 |
| src/quant_core/cli.py | 保留：分派、应用返回值、退出码与失败持久事实清楚。 |
| src/quant_core/contracts.py | 补齐：freeze_new_risk非持久化动作、输入索引与文件摘要映射。导出模型类docstring保持原样。 |
| src/quant_core/data.py | 保留：时点筛选、复合索引、修订/质量顺序、有效区间和失败出口清楚。 |
| src/quant_core/execution.py | 补齐：执行时资格主表与决策时批准目标区别，保留F01限制。恢复、幂等及账户锁说明保留。 |
| src/quant_core/factors.py | 补齐：样本标准差59分母、252交易日年化约定与负号方向。 |
| src/quant_core/ledger.py | 保留：新增成交、总成本、剩余仓位、副本、结算和异常范围清楚。 |
| src/quant_core/monitoring.py | 补齐：覆盖率由调用方提供、标准演示分母、范围校验并非本函数职责。 |
| src/quant_core/portfolio.py | 保留：目标/实仓/挂单、effective与risk_quantities、净值/现金/预算和约束出口清楚。 |
| src/quant_core/regime.py | 保留：状态计数、阈值、确认、当前修订回放及UNKNOWN局限清楚。 |
| src/quant_core/reporting.py | 补齐：同运行输入为调用前提，只返回文本，不负责跨对象核验或文件写入。 |
| src/quant_core/research.py | 保留：事后标签与特征隔离、样本索引、时间窗口、purge和空值口径清楚。 |
| src/quant_core/risk.py | 保留：正常/减风险路径、累计占用、损失基线、费用后净值及F01边界清楚。 |
| src/quant_core/signals.py | 保留：原值/百分位、共同样本、嵌套表、并列下标和排序清楚。 |
| src/quant_core/universe.py | 保留：已过滤快照前提、资格筛选与实际持仓无副作用清楚。 |
| tests/test_application.py | 保留：产物、重签反例、现金恒等式、隔离限制和研究索引清楚。 |
| tests/test_calendar.py | 保留：固定日期与UTC期望、会话端点及范围错误清楚。 |
| tests/test_contracts.py | 保留：模型重验、内容摘要、固定摘要期望、Parquet输入及精确类型边界清楚。 |
| tests/test_data.py | 保留：样本工厂、可用时间、历史修订、冲突与原值期望清楚。 |
| tests/test_execution.py | 补齐：后续submit先恢复卖出成交，再拒绝新单；已更新内账不会回滚。 |
| tests/test_factors.py | 保留：价格窗口、独立标准差、平均并列名次及共同样本期望清楚。 |
| tests/test_governance.py | 仅更正一条注释：缺源时不创建目标数据库文件，不是父目录未创建；其余文字、源码样例及测试全部保留。 |
| tests/test_ledger.py | 保留：两种身份、状态/成交区别、手算成本、事务冻结与交错日志清楚。 |
| tests/test_monitoring.py | 保留：八类反例、阈值、目标/事实与未确认告警清楚。 |
| tests/test_pipeline_invariants.py | 保留：未来扰动不变性与公式独立验算分离，明确标准链路不覆盖F01直接入口。 |
| tests/test_portfolio.py | 补齐：首次位置参数中的ADV字典含义、20日窗口与股数口径。 |
| tests/test_regime.py | 更正：实际切片同时裁去首条并制造内部缺日，不改切片或预期。 |
| tests/test_research.py | 补齐：IC为因子与后续收益排名相关，五只五组每组一只的期望关系。 |
| tests/test_risk.py | 补齐：首次ADV、日内损失基线和回撤高点的单位与作用区别。 |
| tools/__init__.py | 保留：开发工具边界清楚。 |
| tools/backup_sqlite.py | 保留：停写前提、只读视图、逻辑/字节摘要、新目标与失败清理清楚。 |
| tools/export_contracts.py | 保留：固定正反样例、序列化/校验、单文件原子替换和部分失败清楚。 |
| tools/governance.py | 保留全部字节：说明存在性与语义判断、依赖矩阵、需求/Schema和敏感差异范围清楚；不改规则。 |

## 修订前后与判断依据

- `ExecutionService.submit` 原说明将主表统一写成决策时点；现在区分固定批准目标与执行时已知/有效的主表资格。依据 `assess_order` 的 `now` 校验和资格边界测试，未宣称F01已修。
- `FakeBroker.apply_action` 补明只修改券商账户及行动身份，不把公司行动加入 `events()`；内部需要独立应用同一事实。若行动造成未同步的账户变化，recover只报告差异，不自动补记；没有经济变化的行动不必然产生账户差异。依据现有双库公司行动测试，不新增自动同步实现。
- 故障开关原称“实际接受的新单”；改为新单处理结果落库后的响应丢失，包含拒单开关同时设置的情况；重复同键直接返回、不消耗开关。异常消息等普通字符串不变。
- `_book` 的“返回快照”改为构造待写入快照；其返回仍为None。数据库初始化说明区分建表与后续事务，数据库读取明确停写前提，避免把只读等同于并发一致性。
- 低波动公式把未解释的 `ddof=1` 就地展开为60个收益的平方偏差和除59后开方，再按252交易日约定年化；与已有公式和手算一致。
- `factor_coverage` 的[0,1]是调用前提而非函数内验证；标准演示分子为双因子共同有效评分证券数，分母为原始候选股票数。不是成交率或目标完成率。
- 风控结果对象不会自行持久冻结；运行清单inputs保存输入身份/版本/摘要索引，artifacts保存相对文件名→字节摘要，不包括清单自身。只补字段旁解释，生成模型描述不变。
- `render_report` 只排文本；同次运行且已核验是调用方责任。`replay_journal` 只将自身序号/载荷错误明确为ContractError，存储和模型错误仍向上传播。
- 测试中保留全部独立输入和断言，只纠正样本与对象描述、补调用副作用及首次术语。治理测试的一句文字更正已获用户明确确认，不等于调整治理测试规则。

## 验证与独立复核

审查后仅修改16个Python文件中的已确认说明，其余24个保留原字节；此数量只表示处理范围，不是注释质量指标。本次逐文件审阅及跨作者复核发现的注释问题已全部关闭，未发现尚待补齐的规范缺口。所有日志、快照、AST比较和新运行保存到 `artifacts/readability-compliance-20260919/`，不沿用旧运行清单。

独立AI复核按不同作者分工，结果分别保存在 `review-core-tests.md`、`review-execution-adapters.md`、`review-formulas-docs.md`。它们核对具体函数的对象、来源、结构/单位、操作效果、调用责任和失败后果，不把作者自检或自动通过当成语义复核。复核期间还收窄了公司行动不一定产生账户差异的条件，补全执行入口的20日ADV窗口，并纠正研究测试“五只因子”为“五只证券”；提出者均已回看关闭。40文件清单与实际文件集合逐项一致，没有遗漏或重复。

本轮沿用已安装的Python3.12.14和uv0.12.5，未安装或升级依赖。下表 `uv` 完整路径为 `/private/tmp/quant-qc014-tools/bin/uv`，运行均带 `UV_CACHE_DIR=/private/tmp/quant-qc014-uv-cache`。

| 实际命令 | 结果及证据 |
|---|---|
| `.venv/bin/python artifacts/readability-compliance-20260919/verify_scope.py` | 退出0；115文件起始快照摘要完整，40个Python AST全部一致，范围外所有文件字节不变；治理测试全文只替换授权的一句docstring，契约类docstring不变。见 `scope-verification.json`、`round.diff`。 |
| `uv run --offline --locked quant-core check --base 9f0835b` | 退出0，governance_errors=[]，Ruff、格式和mypy通过；**133 passed、20 warnings、54.98秒**。见 `check.log`。敏感差异提示仍保留，并经独立复核，未当成自动批准。 |
| `uv run --offline --locked python -m tools.export_contracts --check` | 退出0，生成Schema及六个固定正反样例一致；未执行写入生成。见 `contracts-check.log`。 |
| `uv run --offline --locked quant-core demo --config configs/demo.toml --output artifacts/readability-compliance-20260919/demo` | 退出0，见 `demo.log`。运行ID仍由相同固定输入确定，不表示沿用旧清单。 |
| `uv run --offline --locked quant-core validate --run-dir artifacts/readability-compliance-20260919/demo` | 退出0，见 `validate.log`。 |
| `uv run --offline --locked quant-core replay --run-dir artifacts/readability-compliance-20260919/demo --output artifacts/readability-compliance-20260919/replay` | 退出0，account/orders/factors/signals/target五项全为true，见 `replay.log` 与回放核对文件。 |
| `git diff --check` | 退出0，源码注释与文档无补丁空白错误。 |

AST核验只忽略真正模块/类/函数docstring和位置信息（包含TypeIgnore.lineno），保留类型注释标签、普通字符串、变量、签名、运算、分支、测试数据和断言；没有治理逻辑豁免。另以精确单句替换验证治理测试的授权范围。根/子规范、维护提示词、SPEC、架构、治理检查器、CI、依赖锁、配置、生成契约及前两轮验收文档保持原字节。

已检索docstring运行时使用：业务无显式 `__doc__`/`inspect.getdoc` 读取；治理通过AST读取，Schema生成通过 `model_json_schema()` 使用类说明。导出契约类docstring逐项不变，字段旁注释不会进入Schema；生成结果与磁盘文件的只读比较也通过。描述、字段、类型、默认值和约束均不需迁移。

新清单记录提交 `9f0835be08812b4233f548788a72d18a6fb1a5d9`、dirty=true，源码指纹为 `1353a5c67072c4e1568288924520e4364fddb50c163f1fb6650348fc3b02e3f1`；不同于起始指纹 `fdf13598bd0c799221b85059c66f36e889f226101d0a57c9ff7d5b606018f518`。源码与新清单一致，不向旧运行写回新指纹。

正式检查及离线命令均成功。作者首次额外比较整个contracts文件字节时，因另一作者正补字段注释而不相同；其后按实际保护目标核对类docstring、统一AST与Schema，均一致，没有改业务实现来绕过检查。20条既有第三方弃用警告未屏蔽；validate/replay保留macOS沙箱下PyArrow读取CPU信息的sysctl权限诊断，但退出0。未单独运行research CLI、完整运行目录灾恢、远端CI或真实接口；现有研究/备份恢复测试包含在133项中。

本次未发现需混入本轮修复的新业务问题；F01/F02/F03和已记录的运行边界继续单独保留。注释合规不代表策略收益、持续交易、真实接口或生产环境已验收；不连接真实账户、发送外部订单、发布或部署。
