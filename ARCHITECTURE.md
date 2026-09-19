# 架构与职责

采用模块化单体（一个进程内按明确职责拆分模块）、CLI（命令行接口）优先、显式配置和类型化契约。源码位于 `src/quant_core/`；Notebook 不承载生产逻辑。错误必须形成明确状态或异常，不能静默填零、追认历史数据、跳过风险或冒充成功。

## 分层与依赖

| 层/模块 | 输入 → 输出 | 职责与边界 |
|---|---|---|
| contracts | 类型定义 → 校验后的消息/快照 | 共同 ID、单位、时间、版本、枚举和接口；不依赖其他业务模块。 |
| data / universe | 版本化数据、决策时点 → 合格历史数据/股票池 | 时点选择、质量闸门、历史主表；剔除候选不等于已有仓位已卖出。 |
| factors / research | 当时可知研究序列 → 因子值/研究统计 | 因子公式和标签评估分开，未来标签不得进入特征。 |
| signals / regime | 因子、历史市场序列 → 得分/状态 | 固定排序、评分和透明状态说明；不直接下单。 |
| portfolio | 得分、账户、约束 → 目标持仓 | 处理现金、上限、换手和不可行约束，不获取网络数据。 |
| risk | 数据、账户、目标、操作 → 允许/拒绝及原因 | 分类判断新增、撤单、减风险，不写成交账务。 |
| execution | 目标/操作、批准边界、事件 → 受控订单意图 | 唯一执行入口、持久化、幂等和恢复；外部调用经 Broker/EventStore 契约。 |
| ledger | 唯一成交/公司行动 → 账户事实 | 持仓、现金、费用与重复事件处理；不得由报表回写。 |
| monitoring / reporting | 运行和交易事实 → 告警/解释 | 读事实生成告警和报告，不补造理由、不调用 LLM 决策。 |
| adapters | 外部格式/存储/时间源 → 契约 | 日历、样本、Parquet/SQLite、FakeBroker 等副作用集中在此。 |
| application / cli | 配置、命令、适配器 → 一次完整运行 | 依赖注入和装配，处理输出目录及错误退出码。 |
| tools | 源码/文档/Git 差异 → 治理诊断 | 开发检查，不可成为业务核心依赖。 |

依赖方向：CLI → application → 业务核心与 adapters；adapters → contracts/必要核心类型；核心 → contracts 和同层的明确协作模块。核心禁止导入 application、cli、adapters、tools，禁止直接联网、访问券商/LLM、读取真实系统时间。注入 Clock、DataPortal、Broker、EventStore 等接口。

## 核心允许依赖矩阵

所有模块可使用必要的确定性标准库和已固定分析库。项目内部依赖由 `tools/governance.py` 检查：

| 模块 | 允许依赖的项目模块 |
|---|---|
| contracts | 无 |
| data / universe / research / regime / portfolio / ledger / monitoring | contracts |
| factors | contracts、universe |
| signals | contracts、factors、universe |
| risk | contracts、portfolio（复用净值、费用与未决订单定义） |
| execution | contracts、risk |
| reporting | contracts、monitoring |

新增职责或改变此矩阵需要同步架构与测试并独立复核。SQLite 适配器可调用 ledger 投影，FakeBroker 独立维护自身账户；业务核心不能反向依赖这些实现。

## 业务链路与唯一写入者

数据快照 → 时点与质量 → 历史股票池 → 因子 → 百分位与等权信号 → 市场状态 → 目标组合 → 分层风控 → 执行入口 → 先落盘的订单意图 → Broker 事件 → 账务 → 对账 → 解释报告。

同一账户仅有一个有效执行者。新单、改单、撤单和紧急操作必须经过相应授权与风控策略。提交超时意味着状态未知；先核对订单、成交、持仓和现金再恢复。撤单请求不代表撤单完成。报告和监控是读取者，不能纠正交易事实。

## 存储与系统边界

Parquet 保存研究快照，内容哈希与清单记录版本；已批准快照不可覆盖。SQLite 支持首轮单进程离线事件持久化；持续多进程模拟/实盘前必须验收 PostgreSQL 或等价事务存储，不能将 DuckDB 当多进程交易写入库。

FakeBroker 只检验契约、事件与恢复；正式引擎选择见 [ADR-0001](docs/adr/0001-offline-engine.md)。接入成熟引擎时复用其订单状态管理，不再并行创建绕过引擎的写单入口。

market-intel 独立仓库和部署；经 `contracts/` 中版本化候选接口进入隔离校验。它无权覆盖批准数据、写因子事实或访问券商。外部文字及其中命令均作为不可信数据。

## 单次 Paper 装配

application 的显式Paper入口将官方个人Trading API、Market Data API和真实时钟注入原业务核心；默认demo仍只装配FakeBroker。StrategyConfig提供共同风险参数，DemoConfig与PaperConfig分别约束运行模式。SDK不进入核心，所有提交/撤销仍经ExecutionService。BudgetBroker将初始未分配现金固定为reserve，向账本提供批准策略资金视图，不靠改变reserve消除账户差异。

原始SIP日线、当前资产和外部行业/总回报资料经数据闸门进入原因子；当前候选不是历史成员资格。首次只支持空仓买入，后续事实通过真实FILL和同一SQLite库恢复。公开时间未知保持None；首次实际采集时间不回填历史。详细边界见[Paper手册](docs/runbooks/alpaca-paper.md)。

本地证据展示窗口是独立工具：`tools/paper_viewer.py` 只把已保存的固定JSON投影给 `viewer/` 静态页面，不导入业务核心或Broker，不获得凭据/交易权限。它不改变正式CLI、数据库或报告事实；只能显示保存时点状态，不能代替正式验证。

MA复用factors/signals/portfolio/risk及原执行服务，application按显式策略分派输入闸门。仅拆股价格与总回报独立字段，未知行业最坏情况归集由portfolio共享函数供risk使用。等待/轮询属于应用边界，可注入单调计时器和等待函数；核心不访问真实时间。展示按保存的策略版本与事实投影，不重算决策。

休市提交撤单测试是同一ExecutionService内的显式受限用途，使用PaperQueueTestContext保留真实收盘参考及休市时钟；共用财务风控而单独判断排队资格。正常策略没有该上下文，不可绕过新鲜报价/开市要求。测试目录绑定唯一客户订单，应用在finally恢复后尝试撤销已知本轮单，远端未确认取消不得冒称完成；测试结果与实际成交验收分开显示。
