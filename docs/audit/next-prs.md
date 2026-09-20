# 后续小PR建议与依赖

> 历史记录：以下结论、路径及授权仅适用于文中日期和基线；后续实现与验证见[当前状态](../../PROJECT_STATE.md)。本地 `artifacts/` 证据未随 Git 分发，部分旧产物可能已不可用。

日期：2026-09-13；基线 `f01a1237bc02deae4957ceecd29639e16037759d`。本次只提出8个候选PR，未创建或实施；每项在获后续实现授权后开始。实盘、账户只读探测、Paper发单、远端发布仍按各自授权边界处理。保持美股普通股、日频信号/周调仓、无借款/做空、常规时段的开发默认；少量持仓、阈值与预算须人类确认，不能宣称是最佳收益参数。

依赖主线：**PR1 → PR2 → PR3 → PR4 → PR5 → PR6 → PR7 → PR8**。PR4的纯数量设计可在PR3只读数据完成前独立讨论，但公共契约变更先通知其他作者；不能并行引入相互冲突的Schema。本表是审计后的首批范围，跨主机生产、完整实盘审批系统不塞入这些PR。每个PR内部若超出可独立审阅的范围，应在实施前缩小，不用一次大重写满足清单。

## PR1：修复已复现的行业时点与监控漏报

- **目标/关联**：B01、F01/F02；QC-002/006/009/014/015。为所有被估值证券选择当时唯一有效行业；监控比较实际与目标证券并集，报告目标外旧仓。两处均为小范围纯函数修复。
- **现有文件**：`src/quant_core/risk.py`、`portfolio.py`、`monitoring.py`，对应`tests/test_risk.py`、`test_portfolio.py`、`test_pipeline_invariants.py`、`test_monitoring.py`；按实际复用边界更新`docs/contracts.md`、`ARCHITECTURE.md`及需求映射。核心允许依赖矩阵不可被隐式改写。
- **非目标**：不接SDK、不改交易预算、不增加策略、不把对账一致当目标一致、不自动清仓。
- **验收**：先将审计手算输入写为回归，26,000>24,999.75时追加未来行业仍拒绝；主表插入顺序、未来/过期/重叠均覆盖；空目标A=1报TARGET_DEVIATION。原117项、治理、demo/replay保持通过。
- **回滚**：纯计算变更可回退代码；若缺陷重新出现，禁止将该版本用于Paper，不以旧测试通过恢复写能力。审计反例与失败记录保留。

## PR2：建立可选SDK与只读Paper边界

- **目标/关联**：B02/B06；QC-001/013/015。选择并实际解析一个兼容Python3.12/Pydantic2的官方alpaca-py版本；单独可选适配依赖；建立只读配置、账户ID与交易/流主机校验，live禁用。先完成最小账户/持仓/开放订单只读客户端，写接口默认不可用。
- **现有文件**：`pyproject.toml`、`uv.lock`、`src/quant_core/contracts.py`、`application.py`、`cli.py`、`tests/test_application.py`、`test_contracts.py`、`.github/workflows/quality.yml`；新增明确职责的只读adapter/无网测试夹具，更新`docs/configuration.md`及`docs/runbooks/quickstart.md`。
- **非目标**：不修改DemoConfig去允许所有模式；不访问账户、不探测密钥、不安装插件、不下单、不把SDK拥有submit方法当授权。现有offline流程继续无需SDK和密钥。
- **验收**：固定只读HTTP样例；本地可判断的端点/账户配置错误在任何请求前拒绝。凭据对应的实际账户ID只能在后续获授权后，经白名单端点只读查询并比对白名单；不匹配必须在任何写请求前阻断，不能信任配置自报身份。只读客户端写操作拒绝。pytest收集/导入期禁网与无凭据、外部测试默认排除；锁真实生成/独立复核，既有离线check/demo/replay不变。账户级探测单列“未执行/外部阻塞”，不作为伪造的单测通过；后续Paper门槛包括远端实际净值/现金均为300美元、无旧持仓/开放订单。新建/重置账户后重新核对ID及凭据绑定，本轮不创建或重置账户。
- **回滚**：删除可选适配能力并恢复上个经验证锁/配置版本；未建立交易状态，无订单数据库迁移。敏感依赖/CI变更必须有依据和独立复核。

## PR3：完成显式feed的日线与证券只读快照

- **目标/关联**：B02、R02–R05；QC-001/002/003/011。实现历史bars全部分页、固定feed、限流/缺页失败、完成日线时点、来源/权限证据；读取active/tradable/fractionable属性并保留历史版本，不能用当前股票列表伪装历史股票池。
- **现有文件**：`src/quant_core/adapters/datasets.py`（保留fixture）、`adapters/storage.py`、`contracts.py`、`data.py`、`universe.py`、`application.py`；`tests/test_data.py`、`test_pipeline_invariants.py`，新增Market Data只读adapter测试；`docs/contracts.md`、`docs/research.md`、`docs/configuration.md`。
- **非目标**：不换信号公式、不静默SIP→IEX、不购买授权、不把IEX量当全市场ADV、不宣称免费bars解决历史退市/公司行动授权。
- **验收**：多符号跨页直至next_page_token结束，429/截断/重复/缺日/feed变更阻断；未来修订不改旧决策；完成日线与市场收盘/session对应；缺first_seen_at不伪造。真实entitlement只在后续明确授权的只读探测中关闭。
- **回滚**：停用该数据集/适配器，保留全部原始分页、失败及快照；不覆盖批准快照，不把Fake数据接到Paper任务冒充供应商恢复。

## PR4：数量契约和账本支持精确碎股

- **目标/关联**：B03/B04；QC-001/006/010/012。明确share/USD单位、Decimal精度与舍入边界，演进账户、目标、订单、成交、累计成交、分红权益及拆股，保留原整数事实可读性。采用显式版本化的新数量契约与兼容适配，旧整股路径继续独立运行；PR5再切换新planner，不以等待PR5为理由让本PR类型检查失败，不扩交易策略。
- **现有文件**：`src/quant_core/contracts.py`、`ledger.py`、`adapters/sqlite_store.py`、`adapters/fake_broker.py`、`tools/export_contracts.py`及生成的`contracts/`；必要类型传播/版本分派同时检查`execution.py`的成交汇总及`portfolio.py`/`risk.py`的数量接口，功能规划仍留PR5；`tests/test_contracts.py`、`test_ledger.py`、`test_governance.py`；`docs/contracts.md`、`docs/runbooks/backups-migrations.md`、需求映射。
- **非目标**：不手工改生成Schema、不覆盖批准运行、不进行生产库迁移、不扩大本金/单票风险。碎股拆股不能静默丢掉现金替代或分数权益。
- **验收**：独立固定0.0135股与金额恒等式、0.004+0.0095累计、重复事件不双记、数量越界拒绝、拆股/股息现金手算；老整股journal和报告可回放，新版本不支持时明确拒绝。旧整股契约明确拒绝碎股的用例在兼容模式保留，新版使用独立样例；不得静默删除或降低原断言。测试期望不得由实现计算。
- **回滚**：只在备份复制库验新Schema；旧版不兼容新数量时禁止把旧代码连上新库。保留所有新事件，回滚代码不回滚事实；写出明确版本兼容表。

## PR5：纯300美元订单计划及限价序列化

- **目标/关联**：B03/B06、F/G/L；QC-006/009。复用评分和约束，支持小账户预算/最小调仓阈值、不可碎股拒绝、未决余量与现金预留；把新意图精确映射成固定SDK的碎股limit+DAY候选请求。
- **现有文件**：`src/quant_core/portfolio.py`、`risk.py`、`contracts.py`、`tests/test_portfolio.py`、`test_risk.py`；新增独立小账户配置和SDK序列化测试，更新`docs/configuration.md`、`docs/strategies/weekly-two-factor.md`。默认演示参数及费用不冒充Alpaca规则。
- **非目标**：不发远端订单、不因候选不足重归一化或放松上限、不增加notional和qty双字段、不自动改市价。官方文档冲突未核实则保持该能力不可用；市价是另需人类风险批准的变更。
- **验收**：300×4.5%/1000=0.0135股；不可碎股理由固定；现金300、旧买占101、未成卖款200不得增加可用199；费用/步长/最小金额/残余分别固定边界；SDK浮点序列化、限价精度、DAY、拒绝/未成交及paper/live错误组合验证。交易兼容性仍标待授权Paper验收。
- **回滚**：停用新配置和计划输出，保留旧离线模式；若尚未发送则无账户事实迁移，已有批准目标/订单不得因回滚重新生成新身份。

## PR6：真实Paper生命周期接入既有恢复入口

- **目标/关联**：B04、A–E/G/J/N；QC-008/009/010。将SDK提交/查询/撤单/流事件接入现有ExecutionService；真实订单/成交/账户活动补取、断流后恢复、持续差异证据与保守可用现金。复用已有意图/去重/锁与journal，优先映射适配层而非重写状态机。
- **现有文件**：`src/quant_core/execution.py`、`contracts.py`、`ledger.py`、`adapters/sqlite_store.py`、`application.py`；新增可写Paper Broker adapter及活动映射；`tests/test_execution.py`、`test_ledger.py`、`test_risk.py`，`docs/runbooks/recovery.md`。
- **非目标**：不接live、不把完整分页假设变成首屏即完成、不直接用券商账户覆盖本地事实、不由LLM补账/判断超时、不在本PR验收前远端发单。无法正确映射的活动显式冻结。
- **验收**：固定离线API/流故障覆盖A–E及活动差异；受理后超时查询旧键，4/10崩溃恢复只记4并保留6，取消期间成交不回退；重触发不加仓；孤儿/未知活动冻结；费用/结算与净现金逐项解释。Paper实际调用留到明确获交易授权的阶段。
- **回滚**：冻结新风险/停执行者，保留最新订单、事件/活动/幂等与差异，旧版本只有在Schema兼容且外部事实已重新对账后才能恢复。不能重置Paper账户清除错误。

## PR7：最小单主机运行监督与完整恢复演练

- **目标/关联**：B05/B07、H/K/N；QC-008/011/012。只做单主机单账户前台运行器，持久记录周调仓到期/完成/过期、启动先恢复、暂停/撤单/减仓分开；把实际心跳/阶段事实送现有监控，留通知接口。整目录停写备份和新路径恢复有可执行验收。
- **现有文件**：`src/quant_core/application.py`、`cli.py`、`monitoring.py`、`adapters/calendar.py`、`adapters/sqlite_store.py`、`tools/backup_sqlite.py`；`tests/test_calendar.py`、`test_monitoring.py`、`test_execution.py`、`test_application.py`；`docs/runbooks/recovery.md`、`backups-migrations.md`。
- **非目标**：不部署生产、不建多节点/微服务/UI、不让通知服务获得交易写权限、不伪造外部心跳送达。跨主机事务存储按现有ARCHITECTURE另行验收，不塞进本PR。
- **验收**：固定注入时钟跨DST/节假日/半日市、漏跑旧信号不自动补发；第二实例无法执行；停止主进程由独立观察者发现；备份后成交4/10、恢复旧内部库，事实只补记一次且不重发10；实际通知渠道在后续授权下测送达。回滚版本不得丢恢复点后订单。
- **回滚**：停止调度与执行者，保留任务状态/清单/两库/外部事件；维持只读恢复诊断，不能回到旧库后直接继续执行。

## PR8：真实数据的最小成本后研究与分阶段验收材料

- **目标/关联**：R15/L03，持续Paper及实盘准入证据；QC-012/013/017。固定现有双因子，复用时间分割/holdout，增加可审阅的成本后账户轨迹、基准、换手、回撤及有限参数扰动报告；建立单次监督Paper与20交易日/4次调仓的观察记录模板。
- **现有文件**：`src/quant_core/research.py`、`application.py`、`reporting.py`、`tests/test_research.py`、`test_application.py`，`docs/research.md`、`docs/adr/0001-offline-engine.md`、`ASSUMPTIONS.md`、运行手册；不引入第二正式引擎。
- **非目标**：不扩因子、不做LLM新闻交易、不把合成即时成交冒充成本后收益、不优化到holdout、不宣称低成本账户能获利。没有授权且足够的数据就记录阻塞，不购买。
- **验收**：独立小轨迹的费用、NAV、换手和回撤手算；same-day分组/purge/holdout保留；固定试验预算与全部失败记录。历史成分/公司行动/数据覆盖偏差明确。实际监督Paper与账户探测仅在另行授权后留真实日志，观察门槛不是法规/盈利保证。
- **回滚**：研究输出追加，不回写交易事实；停用新评估器，保留旧/新全部实验，禁止删除不利试验。研究报告无权改变交易批准状态。

## 各PR共同交付要求

每个PR须先写需求ID和独立预期，再最小实现；自有Python逐语句中文说明、中文docstring与类型，复杂公式说明索引/单位/边界。同步契约/Schema样例、配置说明、相关文档、需求映射、PROJECT_STATE/CHANGELOG；不受影响者记录理由。实际运行`uv run --offline --locked quant-core check`和新目录demo/replay，记录失败/警告；风险、CI、锁和测试断言变更独立复核。不得删除测试、降低断言、放松风控或自动批准发布。

2026-09-19 更新：上段保留原审计建议口径，其中对未来工作的「逐语句中文说明」要求已由 [根 AGENTS.md 的 QC-014 分层解释规范](../../AGENTS.md#按业务语义分层解释qc-014唯一主规范)取代；其余测试、风险、依赖、复核和发布约束继续有效。迁移依据与验证见 [注释规范迁移记录](comment-semantics-migration.md)。

本次未改变业务行为，现有需求映射及测试不改；此处文件名为后续拟修改清单，不是已完成变更。免费路线与限价支持冲突详见[ADR](../adr/free-paper-route.md)，各级放行仍以[readiness](readiness.md)和[阻塞清单](launch-blockers.md)为准。
