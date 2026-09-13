# 300 美元、零付费 API 上线准备审计

审计日期：2026-09-13。对象：本地 `quant-agent-lddjxy`。本报告按用户明确授权的附件执行；源码、测试与本次命令输出是实现证据，官方页面仅证明产品规则。没有访问账户、发送 Alpaca Paper/实盘订单、购买服务、部署或提交/推送本仓库。

## 最重要的三个阻塞

1. **当前风控存在已复现的未来行业记录泄漏**：`risk.assess_order` 接收版本化主表时，对旧仓行业没有按时点筛选；一条未来记录即可让当前超行业上限买单从拒绝变为允许。详见 F01。不能把已有时点测试通过等同于整个风控边界成立。
2. **Alpaca 账户与行情适配未实现，账户权限未核实**：运行只装配 FakeBroker；连只读身份、feed/entitlement、完整分页与真实购买力都还不能由现有 CLI 验证。详见 R02/R07、B02。
3. **300 美元碎股要求未满足**：默认是 100,000 美元整股工程；持仓、订单、成交和公司行动数量均受整股契约限制。固定 300 美元、股价 1,000 美元的组合探针实际生成 0 股。详见 F03。

另外，目标外持仓漏报已复现；持续调度、外部心跳、实际券商恢复、运行审批与真实数据策略有效性仍有缺口。具体风险、修复目标与验收见 [launch-blockers](launch-blockers.md)，实施建议见 [next-prs](next-prs.md)。

## 范围、版本与影响清单

| 项目 | 本次观察 |
|---|---|
| 分支 / commit | `main` / `f01a1237bc02deae4957ceecd29639e16037759d`，提交标题 `init: 仓库框架初始化`；实际提交中已有完整工程，不能根据标题猜测内容。 |
| 工作树 | 开工 `git status --short` 无输出；109 个跟踪文件。生成审计文档后出现文档差异，未提交/推送。演示/回放清单诚实记录 `dirty=true`，不能称该次运行工作树干净。 |
| 运行环境 | Linux `7.0.0-31-generic` x86_64，Python `3.12.3`，uv `0.12.5`；本地已安装虚拟环境。 |
| 依赖 | 已读 `pyproject.toml`、`uv.lock`；NumPy 2.5.3、pandas 2.3.3、Pydantic 2.13.5、PyArrow 23.0.1、exchange-calendars 4.13.2。没有 alpaca-py、LEAN 或 Backtrader 依赖。本次无安装或升级。 |
| 源码指纹 | demo/replay 均为 `9a3ad1a2e82c7f634e37680561ed67723e25351a1850c25241069fd5ffe7a29e`；指纹生成范围见 `application.code_evidence:155–178`，不等同完整发布归档。 |
| 文件范围 | 显式读取根 AGENTS、PROJECT_STATE、SPEC、ARCHITECTURE、README、ASSUMPTIONS；相关契约/配置/研究/策略/因子/运维/ADR 文档、需求映射、源码、全部测试及维护工具；CI 为 `.github/workflows/quality.yml`。 |
| 搜索缺失项 | 在 `src/`、`tests/`、`tools/`、`configs/`、依赖及 `.github/` 中没有真实券商适配、常驻调度或真实通知实现；仓库文件清单中无 Dockerfile、compose 或部署文件。未查询远端 CI/分支保护，相关状态为未验证。 |
| 凭据边界 | 未打开 `.env`、密钥或凭据文件，未读取/打印凭据值，未执行账户级探测；必要变量名称及存在性也尚未进行账户配置验收。报告不推断用户居住地或账户资格。 |

旧 `PROJECT_STATE` 的“代码仍未提交”已过时；本次补充状态而保留旧轮次真实检查记录。影响需求为 R01–R16，对应 QC-001–QC-017。本轮只新增四份审计文档、同步 PROJECT_STATE/CHANGELOG，并在忽略的 `artifacts/` 下留审计工具与产物；业务逻辑、公式、接口、配置、锁文件、CI 和测试断言不变，因此不改 Schema、配置说明或既有需求映射。R/QC 对照见下面矩阵。

## 执行前的安全核查与隔离

`cli.check_project:26–67` 只运行治理、Ruff、mypy、pytest；`application:233–307` 装配本地固定样本、FixedClock、两个 SQLite 和 FakeBroker。没有真实券商导入或 HTTP 调用。tests 中没有 conftest/启动钩子；已安装 `.pth` 只有源码路径和标准 `_virtualenv` 修补，没有第三方 pytest11 插件。`test_application:103–142` 已有 socket 阻断 fixture，但它在模块导入之后才生效，不足以替代全程隔离。

默认工具沙箱此前报 `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`，因此本次执行使用显式外部启动器，并在任何项目导入前加载内核 seccomp：拒绝 `socket/connect/sendto/sendmsg/sendmmsg/io_uring_setup`，自检必须得到 `EPERM` 才执行。限制由全部子进程继承；关闭非标准继承句柄，环境从空构造，无账户凭据/代理，禁用自动 pytest 插件及用户/系统 Git 配置、Git hooks/GPG。启动器以 `/usr/bin/python3 -I -S` 运行。不是把 `uv --offline` 当成交易网络防火墙。

`test_governance:278–315` 会在 pytest 临时目录创建并提交独立固定 Git 样例；该既有离线测试未对本仓库 commit，且钩子已禁用。FakeBroker 的本地合成订单属于离线测试事实，未创建任何远端 Paper 或真实订单。

启动器与独立探针分别为 [offline_guard.py](../../artifacts/readiness-audit-20260913/offline_guard.py)、[probes.py](../../artifacts/readiness-audit-20260913/probes.py)。下面每条运行命令均有共同前缀：

```bash
/usr/bin/python3 -I -S artifacts/readiness-audit-20260913/offline_guard.py
```

| 完整子命令（接上述前缀） | 退出码与实际结果 | 本地证据 |
|---|---|---|
| `uv run --offline --locked quant-core check --base f01a1237bc02deae4957ceecd29639e16037759d` | **0**；governance_errors=[]，independent_review=[]；Ruff 通过、41 files already formatted；mypy 40 文件通过；**117 passed，20 warnings，130.27 秒**。 | [check.log](../../artifacts/readiness-audit-20260913/check.log) |
| `uv run --offline --locked python artifacts/readiness-audit-20260913/probes.py` | **1**；四个观测中基线拒绝符合期望，未来行业版本、监控额外持仓、300 美元碎股三项不满足固定期望；未修改断言以通过。 | [probes.log](../../artifacts/readiness-audit-20260913/probes.log) |
| `uv run --offline --locked quant-core demo --config configs/demo.toml --output artifacts/readiness-audit-20260913/demo` | **0**；run_id=`e1eecad399e04c73a62b69e5`，固定样本完整链路生成。 | [demo.log](../../artifacts/readiness-audit-20260913/demo.log)、[报告](../../artifacts/readiness-audit-20260913/demo/report.md) |
| `uv run --offline --locked quant-core validate --run-dir artifacts/readiness-audit-20260913/demo` | **0**；哈希、契约及语义校验返回 validated。 | [validate.log](../../artifacts/readiness-audit-20260913/validate.log) |
| `uv run --offline --locked quant-core replay --run-dir artifacts/readiness-audit-20260913/demo --output artifacts/readiness-audit-20260913/replay` | **0**；从初态和 journal 重建新内部库，重算比较 account/orders/factors/signals/target，五项均 true。 | [replay.log](../../artifacts/readiness-audit-20260913/replay.log)、[比较结果](../../artifacts/readiness-audit-20260913/replay/replay-verification.json) |
| `uv run --offline --locked python artifacts/readiness-audit-20260913/final_checks.py` | **0**；文档本地链接/行尾、治理、Git差异检查无错误；仅两份维护记录及四份审计文档发生变更，源码/测试/配置/锁/CI不变。 | [final-checks.log](../../artifacts/readiness-audit-20260913/final-checks.log) |

20 条警告来自 exchange-calendars/NumPy timedelta 弃用；未屏蔽，未改锁。本次单独 `research` CLI、两个数据库的完整运行目录灾恢演练、真实接口/外部集成、远端 CI、持续 Paper 均未执行。已有 pytest 确实覆盖研究入口、通用 SQLite 备份恢复和回放，不能把它们扩大为真实策略回测或生产灾恢验收。所有 `artifacts/` 产物被 Git 忽略，新检出不会自动带入；报告保留关键数字和反例输入，重跑应使用新输出目录，不能覆盖现有快照。

## 实际业务调用链

`pyproject.toml:17–18` → `cli.main:107–197` → `application.run_demo:233` → `generate_fixture` / `build_snapshot` → `qualified_universe` / `calculate_factors` → `score_factors` → `assess_regime` → `build_portfolio` → `plan_orders` / `assess_order` → `ExecutionService.submit` → `SQLiteEventStore.save_intent` → `FakeBroker.submit/fill` → `ExecutionService.recover` → `SQLiteEventStore.apply_event` / `ledger.apply_fill` → 独立对账 → `assess_health` / `render_report`。

关键装配证据为 `application:254–286,303–307,326–398,404–423`；策略不调用 Broker。`run_demo:249–250` 只执行最后一个合格周调仓日，不能把 3.3 年输入误称为连续交易回测。下一时段报价由 `datasets.fixture_quotes:110` 明确合成，收盘价乘 1.001，不是实际历史开盘成交。FakeBroker 只在显式注入 `fill` 时撮合，未发现随机成交冒充真实成交的路径。

对 `pass/TODO/FIXME/NotImplementedError/skip/xfail` 的源码和测试搜索未发现待实现业务占位或跳过测试；`tools/backup_sqlite:93` 的 pass 是独占创建空目标的上下文体，`test_governance:62` 是被扫描的坏代码字符串。contracts 的 Protocol 省略体是接口声明，不等于真实适配器已实现。`cli:187–201` 捕获运行异常后返回非零；仅在缺少 `.git` 时，`application.code_evidence` 保留 commit=unavailable、dirty=true；存在 `.git` 时若 Git 查询失败会抛错，由 CLI 返回非零，未吞掉后继续交易。固定 HEALTHY 元数据局限见 F02/R13。

## R01–R16 能力与证据矩阵

状态只采用“已验证、已实现待验证、部分实现、未实现、外部阻塞、不适用”。“已验证”限定于本次测试/运行覆盖；整体 Paper 能力不会由离线子模块通过自动升级。

| ID / QC 关联 | 状态 | 实现、当次验证与缺口 |
|---|---|---|
| R01 / QC-012/013 | **已验证** | 限固定合成离线演示/回放：`application.code_evidence/runtime_evidence/replay_run:155,181,627` 保存版本、输入、初态、锁及 journal；本次 check/demo/replay 通过，五项重算一致。不是生产回测/固定版本发布验收。 |
| R02 / QC-001/002/013 | **未实现** | `pyproject.toml:6–20`、`adapters/` 无 Market Data/Alpaca 适配，feed、entitlement、延迟/分页/限流未进入真实数据链。免费规则已核官方资料，实际账户权限仍外部阻塞；见 [路线 ADR](../adr/free-paper-route.md)。 |
| R03 / QC-001/002/003/011 | **部分实现** | `data.build_snapshot:23` 按 available_at 过滤，StampedRecord actual 必须 first_seen_at；因子用总回报，执行用原始价。`test_data:83`、`test_pipeline_invariants:31` 当前覆盖通过。但 F01 证明风控旧仓行业有未来泄漏；供应商日线完成/缺页/迟到实际接入未实现。 |
| R04 / QC-003/010 | **部分实现** | `SecurityRecord:117–137` 有稳定ID、ticker、历史区间和上市/可交易状态；`test_data:137` 改名，`test_portfolio:64` 停牌旧仓，`test_ledger:217` 拆股/股息与幂等均在本次通过。合成主表固定；真实历史退市覆盖、现金替代、碎股拆股与公司行动活动导入未实现。 |
| R05 / QC-004/005/007/011 | **已验证** | 限当前固定双因子：`factors.calculate_factors:37` 为 P[t−21]/P[t−252]−1、60日简单收益样本标准差年化后取负；`signals.score_factors:20` 双因子共同样本、平均并列名次、固定等权与稳定排序。`test_factors:22` 手算 2.31、−0.01√(252×60/59)、排名[1,2/3,1/6,1/6]通过。`research_run:780` 复用相同因子与周日历；不是收益有效性验证。 |
| R06 / QC-006/009/010 | **部分实现** | 金额 Decimal、现金/费用缓冲已有；`contracts:364,391,442,463,533` 数量整股，`portfolio:178` 向下取整；无 fractionable/notional/数量步长/最小调仓金额。F03 的300美元高价股目标为0；默认费用每单1美元是合成参数，不能冒称 Alpaca费率。 |
| R07 / QC-008/010/013 | **未实现** | `contracts.Broker:680–714` 仅公共接口；唯一实现 `adapters.fake_broker.FakeBroker:40` 使用本地SQLite。没有真实账户、证券属性、时钟/日历、分页订单/活动、stream 或SDK契约验收。 |
| R08 / QC-008/012 | **部分实现** | 离线已有成熟基础：`execution:244–260,315–325` 意图先落盘，超时UNKNOWN；`sqlite_store:206–240` 同键同内容幂等；`portfolio:445–458` 稳定ID；跨进程账户锁测试通过。真实API幂等和同账户持续服务租约待验证，勿重写已有核心。 |
| R09 / QC-008/010 | **部分实现** | `portfolio:257–279,323–332` 计算余量并等待同证券活动单；`execution:437–454` 等撤单确认；`sqlite_store:339–363,443–480` 去重与状态单调。B/C/D/E离线案例通过，未覆盖真实stream丢包、分页、跨买卖成交/公司行动任意乱序补取。 |
| R10 / QC-008/009/010/012 | **部分实现** | `execution._recover:89–198` 从独立Broker订单/事件/账户核对现金、可用现金、数量、费用/成本，差异阻新增，不覆盖内部账本。手工事件冻结已有。缺独立入出金/结算/费用等账户活动模型、真实持续对账历史与恢复点之后外部事实演练。 |
| R11 / QC-006/009 | **部分实现** | `risk:271–299,324–356` 挂买余量及费占现金、不预支未成卖款，费用降低风险分母，空头/超卖拒绝，本次测试通过。真实账户权限/buying_power/settled cash未建模；`ledger:75,124` 即时释放可用现金仅合成；F01还可漏掉行业超限。 |
| R12 / QC-008/009 | **部分实现** | 质量、陈旧报价、UNKNOWN、差异/亏损等阻新增；CANCEL/REDUCE独立且有硬约束。`authorized: bool=True`（risk:41,168；execution:363,405,457）不是人的审批凭证；没有完整持久暂停与身份审计。当前 offline/DEMO 边界有效，不代表实际账户授权已实现。 |
| R13 / QC-008/011/012 | **部分实现** | `calendar:46`及`test_calendar:27`验证假日、DST、半日市；有同机锁、SQLite、备份工具、监控纯规则。`application:249,412–423` 单次运行且心跳/来源时间直接等于execution_at；无常驻调度、漏跑处理、独立心跳/通知/确认人。F02漏报目标外持仓；无UI，第二UI执行器当前不适用。 |
| R14 / QC-013/014/015/016 | **部分实现** | `DemoConfig:291–299`锁offline/DEMO；`test_application:438`拒live配置；本次治理/注释/类型检查通过。CI contents:read、无secrets引用；未查询远端保护或CI运行。live marker只注册未默认排除（pyproject:27–31，cli:52–56），新增真实适配前须前移隔离；paper/live endpoint、密钥/账户分离及发布回滚尚未实现。 |
| R15 / QC-017 | **部分实现** | `research:84,195`下一合格开盘标签、12/3/3月、末6月holdout和标签区间purge；`application:816–826`确有train/validation/test/holdout分别描述统计，实验/失败留痕。本次研究测试通过。`application:832`明确未扣成本；无真实授权历史、完整成本后净值/回撤/换手/基准及参数稳定性验收。 |
| R16 / QC-013/015 | **外部阻塞** | 居住地/KYC、开户支持、资金来源、实际账户制度、到账与出入金费用、人工风险批准均未核实。`ASSUMPTIONS.md`列出准入边界；FINRA变化和Alpaca公开页面不能代替账户级确认。见 ADR官方来源。 |

## 独立反例：既有测试通过之外的新证据

以下探针输入全部为自造固定数据，只调用纯函数，无Broker对象、持久化意图或订单发送。两名独立复核者已检查输入、手算及日志。探针没有写入/修改项目测试；退出1如实保留，不冒称统一check失败，也不掩盖需求未满足。

### F01：未来主表改变当前行业风控（已验证，P1）

位置：`src/quant_core/risk.py:334–352`只筛当前买入证券资格，`:419–425`却用全部传入版本按最后一条覆盖行业；同类静态路径在`portfolio.py:289`。

固定时点2023-01-09 14:30 UTC；现金76,000美元，A0–A5各40股×100美元，当前行业均TECH；拟买B 20股×100美元，手续费1美元，B也属TECH，净值/期初/高点均100,000。独立手算：TECH应为26,000，大于(100,000−1)×25%=24,999.75，必须拒绝。

实测仅当前主表时`allowed=false`、`sector_exposure_limit`；仅末尾追加A0在2024-01-01才公开且生效的OTHER行业版本后，`allowed=true`、reasons为空。两个期望均独立固定为拒绝，未用第一个实现结果当第二个期望。

范围：**risk.assess_order公共入口缺陷已复现**；plan_orders同类路径只静态审查，尚未单独运行该反例。标准demo通过`build_snapshot`传入已过滤的`snapshot.securities`（application:254,345,365,386），本次没有证据表明标准demo触发了泄漏。后续真实适配若传完整版本列表会暴露此缺口，执行前风控应自己守住时点边界。

### F02：目标外持仓被监控报为健康（已验证，P2）

位置：`src/quant_core/monitoring.py:123–144`。账户A=1股、现金1,000美元、A总成本10美元，目标为空、cash_weight=1，独立账本对账一致，其余运行元数据完整且新鲜。独立期望是`TARGET_DEVIATION`（实际1股≠目标0股）；实际只有`HEALTHY`。原因是只遍历目标证券，没有检查账户与目标集合的并集。对账一致说明两个账本相同，不能证明目标已达成。

### F03：300美元高价股被整股取整为零（已验证的新需求缺口）

位置：`portfolio.py:168–184`及整股契约。现金300美元、HIGH股价1,000美元、原目标权重4.5%，保持全部现有阈值，独立预算13.50美元对应0.0135股；实际目标股数0、cash_weight=1、理由`constraint_or_rounding_cash:HIGH`。SecurityRecord没有fractionable字段。这是新R06要求未满足，不是原SPEC明确整股行为的回归失败；未验证真实可碎股资产或券商精度。

## A–N 故障覆盖

| 场景 | 状态 | 本次已有证据与仍需验收的部分 |
|---|---|---|
| A 重复调仓 | 部分实现 | `test_portfolio:105–150`活动单不再规划、`test_execution:159–188`同意图恢复通过；缺部分/全部成交后完整重复调度闭环。 |
| B 受理后超时 | 已验证 | 限Fake：`test_execution:159–188`UNKNOWN→查询既有OPEN，订单及事件各1；真实API未验证。 |
| C 部分成交崩溃 | 部分实现 | `test_execution:191–220`10股先成4后重开两库，现金99,599/成本401，再成6后98,999/费1；缺重新plan+submit完整恢复及真实stream。 |
| D 撤单中成交 | 已验证 | 限Fake：`test_execution:223–288`CANCEL_PENDING后全成保持FILLED；部分4后撤单确认才补6；真实撤单超时未验证。 |
| E 重复/乱序 | 部分实现 | `test_ledger:134–155,183–214,300–323,471–500`双ID去重、旧状态不回退；跨买卖/公司行动事件重排和流重连补页尚缺。 |
| F 小账户碎股 | 部分实现 | F03固定新需求失败；现有整股测试保持通过，真实资产fractionable与不可碎股拒绝未实现。 |
| G 买单预留/未成卖款 | 部分实现 | `test_portfolio:105–162,198–285`、`test_risk:215–293`离线边界通过；真实300美元购买力/结算活动待验收。 |
| H 日历/漏跑 | 部分实现 | `test_calendar:27`DST/假日/半日市与执行时段通过；持久调度及过期信号不补发规则未实现。 |
| I 坏数据 | 部分实现 | 快照质量/迟到、主表停牌与过期报价测试通过；真实分页中断、feed变化、日线未完成闸门缺失。 |
| J 公司行动/外部活动 | 部分实现 | 拆股/股息/重复事件与手工交易冻结通过；真实入出金、现金替代、结算/外部权益口径未实现。 |
| K 两个执行进程 | 已验证 | 限同主机同/tmp：`test_execution:449–496`真实子进程换cwd/库路径仍不能抢同账户flock，释放后可获取；不代表跨主机或常驻调度租约。 |
| L Paper误配Live | 未实现 | `test_application:438–456`只证明offline拒live；尚无paper/endpoint/凭据/真实account模型，不能替代此测试。 |
| M 未来扰动 | 部分实现 | 现有行情→非空因子/信号/目标不变性测试通过；F01新增未来行业反例失败。 |
| N 备份后恢复 | 部分实现 | `test_governance:382–434`通用SQLite固定两事件与user_version=7备份还原通过，journal回放通过；未执行完整运行目录+恢复点之后券商新订单的灾恢。 |

独立后续期望：G可固定现金300、旧买单预留101、未成卖单预期200，则新单可用最多199（再扣自身费用），不能变399；N可备份后让独立Fake的10股单成交4，再恢复旧内部库，必须仅补记4并识别剩余6，不重发10；恢复点后的未知意图必须冻结，不能补造授权。这些新增测试为建议，**本次未执行**。

## 分层上线判断

| 层级 | 状态 | 结论与门槛 |
|---|---|---|
| 1 离线演示/回测 | 部分实现 | **固定合成演示、研究框架与回放可运行且本次已验证**；真实授权数据、完整成本后策略回测未验收，F01/F02待修。 |
| 2 只读Alpaca | 未实现 | 当前CLI不能完成；需适配器、端点隔离及另行授权账户只读探测，账户权限外部阻塞。 |
| 3 有人监督单次Paper | 未实现 | 当前不具备条件；B01–B04/B06及对应故障验收关闭后仍需明确交易授权。本任务未发送。 |
| 4 持续Paper | 未实现 | 还需B05运维、调度/恢复/告警与N灾恢，建议至少20个交易日、4次正常调仓且关键故障测试完成；工程门槛，不是法规或收益证据。 |
| 5 有人监督300美元实盘 | 外部阻塞 | **当前不可上线**；全部Paper关键阻塞、真实成本后研究、账户/费用/资金准入与人工固定版本/风险预算批准须关闭，且可用券商原生界面独立处置。 |
| 6 无人值守实盘 | 外部阻塞 | **当前不可上线**；第5级之外还需独立值守/升级、持续故障与恢复演练、受保护发布、可核实审批；本地规则不是不可绕过的生产授权。 |

路线建议是复用现有核心、以官方alpaca-py增加隔离的Alpaca Paper适配，不同时引入多个正式引擎。LEAN官方CLI付费组织要求与开源Engine独立部署分别评估；免费IEX/SIP权限、碎股文档冲突、FINRA过渡与账户费用的本次官方链接、适用边界均在[free-paper-route ADR](../adr/free-paper-route.md)。免费API不代表免费出入金，也不代表真实账户已具备资格。
