# ADR：300 美元、零付费 API 的模拟盘候选路线

> 历史记录：以下结论、路径及授权仅适用于文中日期和基线；后续实现与验证见[当前状态](../../PROJECT_STATE.md)。本地 `artifacts/` 证据未随 Git 分发，部分旧产物可能已不可用。

日期与官方资料访问日期：2026-09-13。范围：R02、R06、R07、R11、R14、R16；关联 QC-001、QC-002、QC-006、QC-008～QC-015。

状态：**部分实现**。现有离线核心可以复用；本 ADR 推荐保留现有架构并增加 Alpaca Paper 适配，建议尚待实现和验收。Alpaca 接口、账户权限及持续运行均未验收，本次未安装 SDK、未访问账户、未发送模拟或真实订单。本文补充 [ADR-0001](0001-offline-engine.md)，不将其“正式引擎待验收”改写成已完成。实际本次运行证据集中在 [readiness](../audit/readiness.md)。

## 决策及仓库依据

当前默认路线建议为：保留 quant-core 的数据、因子、信号、组合、风控、执行、账务和回放边界，以官方 alpaca-py 封装个人 Trading API 的 Alpaca Paper 适配器；行情适配器单独封装 Market Data API。先实现隔离的只读能力，再验收有人监督的 Paper 闭环，最后评估持续运行。FakeBroker 保留作确定性故障测试，不能作为真实数据回测或券商成交证据。

现有 `pyproject.toml:6–20` 与 `uv.lock` 未列入 alpaca-py、LEAN 或 Backtrader；`src/quant_core/application.py:233–305` 的 `run_demo` 装配合成数据和 FakeBroker，`src/quant_core/contracts.py:680–714` 的 Broker 契约已经分开提交、查询、撤单、订单、事件和账户。核心与副作用分层的依据是 `ARCHITECTURE.md:7–50`。因此没有“当前依赖必须付费才能运行”的实现证据，也没有为免费运行必须迁移框架的证据。

主要欠缺是外围能力与契约演进：`src/quant_core/contracts.py:291–339` 固定 offline / DEMO；同文件 `360–391,442,463,533` 的持仓、目标、订单和成交仍用整数股，`src/quant_core/portfolio.py:168–180` 明确向下取整。300 美元碎股不能仅靠改 `initial_cash` 或增加 SDK 实现。新适配器、证券属性、精度、账户购买力、恢复与活动对账必须共同验收；本次不修改这些实现。

## 路线比较

下表“代价”是基于本仓库结构与官方运行前提的工程判断，不是已完成的集成测试。

| 路线 | 官方与仓库事实 | 必要性、代价和本次判断 |
|---|---|---|
| 保留现有核心 + alpaca-py / Alpaca Paper | 官方 SDK 提供账户、证券、订单与持仓接口；[TradingStream](https://alpaca.markets/sdks/python/api_reference/trading/stream.html) 提供交易更新流。现有 Broker 通过适配层注入。 | **推荐当前默认候选**。复用现有计算和故障测试；仍需实现数据、碎股、真实状态映射、持续恢复及运维。SDK 不是正式撮合引擎，不能把它的存在当作完整订单管理。见 [SDK Trading](https://alpaca.markets/sdks/python/trading.html)。 |
| LEAN 官方 CLI | 官方入门要求付费组织成员资格；本地引擎命令依赖 Docker。 | 在零付费平台约束下**不适用**为必需默认依赖；不安装、不假设“本地 CLI 免费”。见 [官方 CLI 入门](https://www.quantconnect.com/docs/v2/lean-cli/key-concepts/getting-started)。 |
| 开源 LEAN Engine 独立构建 | 官方核心为 Apache-2.0；仓库说明独立 Linux 构建及 Launcher 运行，当前说明要求 .NET 10。它与官方 CLI 是不同路线。 | 可保留为后续备选；软件许可不等于数据、账户或托管免费。增加 .NET/Python 运行时、引擎输入映射、日历/复权/费用差异与恢复验收；本次没有这些新增成本能消除当前阻塞的证据，不迁移。固定提交后重新核对构建要求。见 [LEAN 官方源码](https://github.com/QuantConnect/Lean)。 |
| Backtrader | 官方定位为 Python 回测/交易框架；官方碎股示例通过 `CommissionInfo.getsize` 扩展 sizing。 | 可评估的备选，当前不引入；换框架仍需 Alpaca 权限、适配器与账务恢复，并增加第二种策略生命周期。该示例不证明本仓库或 Alpaca 端已支持碎股。见 [Backtrader](https://www.backtrader.com/) 和 [碎股示例](https://www.backtrader.com/blog/posts/2019-08-29-fractional-sizes/fractional-sizes/)。 |

只有以后可复现的对比实验表明现有实现无法满足关键订单状态、性能或研究准确性，并且迁移能以更小风险解决，才重新决策。若采用成熟引擎，要迁移订单生命周期职责并保留单一写单入口，不能让引擎与 quant-core 同时为同一账户独立下单。

## 免费数据的明确边界

个人 Trading API 与面向券商合作方的 Broker API 不可混用；不得用后者的套餐限额、开户或资金 API 作为个人账户依据。Basic 股票数据表列出免费 IEX 实时、30 个 WebSocket 订阅符号、2016 年起历史、历史调用 200 次/分钟及最近 15 分钟限制。30 个符号针对实时订阅，**不是历史股票池上限**；实际权限和限流响应仍待账户核验。[Market Data API 套餐](https://docs.alpaca.markets/us/docs/about-market-data-api)

IEX 是单一交易所来源，不代表全市场量或完整 NBBO；不能用其成交量直接冒充全市场 ADV。FAQ 对历史 SIP 的说明是：不购买实时 SIP 订阅时，显式 `feed=sip` 且 `end` 至少早于请求时刻 15 分钟；最新/Snapshot SIP 端点仍要求订阅。默认 feed 会随权限选取，因此未来实现必须显式指定并写入运行清单。[Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)

同时，Paper Only 页面明示其持有人仅有 IEX 权限。这是与上述通用历史 SIP 说明需要分别验证的账户边界，不能由文档推断当前账户可取 SIP。以后经用户授权的只读探测应保存脱敏后的账户类型、端点、feed、请求/返回时刻、end、entitlement 或拒绝码；SIP 不获准时暂停该数据集，另行明确批准 IEX 数据集及研究偏差，不自动购买或静默替换。实际 Paper 账户和行情权限当前为**外部阻塞**。[Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading)

历史 bars 先按符号再按时间排序；每页 limit 是总记录数，少于 limit 也不代表取完。必须追随 `next_page_token` 至结束，检查重复/缺口、全部请求符号与交易日覆盖、429 重试及页间 feed 一致，全部完成后才封印快照。服务端 429 的限流响应头需保留为非秘密证据，重试不能把半份数据放行。[Historical bars](https://docs.alpaca.markets/us/reference/stockbars)

候选时序为完成交易日数据在收盘后或次日盘前入库，周末决策使用已完成且 `available_at <= decision_time` 的快照，下一合格常规交易时段重新检查原始报价和账户后才执行。若使用延迟 SIP，请求的 end 要留足超过 15 分钟的保守余量；实际余量、预定决策时刻与迟到期限属于待批准工程配置。迟到数据追加新版本，不回填旧决策。历史供应商修订/复权信息不自动等于历史当时已知信息；不得伪造 `first_seen_at`。完整历史股票池、公司行动和幸存者偏差还需独立验收，免费 bars 本身不能解决。

## 300 美元、碎股与订单边界

碎股官方页面给出最小 1 美元股票购买说明、`qty` 与 `notional` 二选一且同时传入会拒绝、两者最多 9 位小数、逐证券检查 `fractionable`。页面 Supported Order Types 声明 market/limit/stop/stop-limit 配合 DAY，但同页尾部仍保留仅常规时段 market 的描述；该文档存在内部口径冲突。[Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading)

SDK 的 `OrderRequest.qty/notional` 说明仍称股票仅 market，且字段类型为 float；不能推断所有碎股订单组合均已一致支持，也不能将 Decimal 到 SDK 的转换视为无损。[alpaca-py 请求模型](https://alpaca.markets/sdks/python/api_reference/trading/requests.html) 现有 `src/quant_core/contracts.py:426–444` 明确限价意图，`src/quant_core/risk.py:120–121,354` 与 `src/quant_core/adapters/fake_broker.py:420–421` 按限价检查，默认路线必须保留这项风险边界。首期优先验证常规交易时段的碎股 limit + DAY、`extended_hours=false`：固定 SDK 后检查数量与价格序列化精度、TIF、拒绝及未成交行为，再经另行明确授权的 Paper 验收确认。官方文档冲突尚未解决时，该组合保持**外部阻塞**，不自动退化成市价单；没有证据证明免费 API 路线必须使用市价。market + DAY 仅作为需要另行明确人工风险批准、预算重审和独立验收的备选，不能由本 ADR 批准。

未来内部金额/股数采用明确 Decimal 单位和量化规则，先检查 active、tradable、fractionable、普通股类型及账户权限；不可碎股资产不能通过整数截断静默消失。目标差额使用实际持仓与未决剩余量；碎股最小交易金额与小额残余平仓规则分别核验。现金预留、最小调仓金额、少量持仓参数属于待批准工程值，不能沿用 100,000 美元样本默认即宣称适合小账户。`price_buffer` 是预算与限价构造参数，不能冒充市价单的成交价格上限；市价备选不得沿用限价单的最坏价格保证。

券商未成交买单会占用购买力，卖单提交不会提前释放买入资金；流式交易更新与按稳定 client_order_id 查询可用于接续状态，但恢复还需拉取事实和对账。首期禁止借款与卖空，本地现金约束和券商实际购买力取保守可用值，不能从券商 margin buying power 直接放大 300 美元预算。[Orders / Buying Power](https://docs.alpaca.markets/us/docs/orders-at-alpaca)

Paper 默认初态为 100,000 美元，界面可新建账户并配置初始额，新账户需要新密钥。未来用户操作后必须只读核对净值/现金确为 300 美元、无初始持仓与开放订单、账户 ID 匹配白名单；本任务不创建、删除或重置账户。Paper 按 NBBO 模拟不等于免费行情订阅就是 NBBO；模拟不覆盖股息、真实流动性数量、时延滑点、队列位置和监管费用等，因此必须在独立研究/合成账本中检验缺失项，不能伪造券商到账活动。[Paper 规则与限制](https://docs.alpaca.markets/us/docs/paper-trading)

## 依赖、隔离和账户外部条件

后续实现如获授权，只增加一个可选 Alpaca 适配依赖，评估与当前 Python 3.12 / Pydantic 2 的兼容性，用 uv 实际解析后固定 `uv.lock`，独立复核锁文件并保留离线安装后无网路径。本次没有选定或安装 alpaca-py 版本，没有执行兼容性测试；SDK 集成为**未实现**，不自动升级现有锁定包。

建议配置层显式区分 offline/paper/live；paper 只允许 `https://paper-api.alpaca.markets` 及指定账户 ID，live 默认禁用。密钥分别保管，不把 `paper=True` 当成唯一防线；SDK 也要求传入与 paper 相配的密钥。[SDK Trading](https://alpaca.markets/sdks/python/trading.html) 行情主机、交易主机和交易更新流分别白名单校验；启动时先只读核对身份再开放写能力，错误组合必须在 submit 前阻断。报告最多记录环境变量名与存在性，不读取或展示值；本轮未检测账户凭证。

小额实盘准入当前为**外部阻塞**，需人类确认居住地/KYC、资金来源、主体及账户政策。Alpaca 国家支持页要求联系支持确认，不能由本机时区或全球 Paper 可用推导实盘可开户。[国家支持说明](https://alpaca.markets/support/countries-alpaca-is-available)

FINRA 官方说明新日内保证金要求于 2026-06-04 生效，券商可过渡至 2027-10-20；过渡期间可能仍用旧规则。必须核对具体券商和账户的迁移及附加限制，不能硬编码“所有账户仍须 25,000 美元”或“PDT 已统一取消”。300 美元方案继续禁止借款杠杆，不以监管变化扩大预算。[FINRA 新规与过渡](https://www.finra.org/investors/insights/intraday-margin-requirements)

零付费 API 不是零交易或出入金费用。官方费用表本次正文修订日期为 2026-09-01：例如国际汇出电汇 35 美元、美国国内汇出 15 美元、本币转换 1.5% 且每笔最高 40 美元；适用通道和第三方银行费用仍需账户级确认。仅作预算敏感性例子，35/300 约为 11.67%，并非断言该用户必付此费。费用表还说明实际碎股数量参与计费、按日/账户/费用类型汇总后向上取到美分，不能直接套演示每单最低 1 美元。数据许可、交易费用、入金到账、退出资金路径和费用未确认，均不得解除实盘阻塞。[Alpaca 费用表](https://files.alpaca.markets/disclosures/BrokFeeSched.pdf)；监管费用页将当前费率指向正式费用表，[Regulatory Fees](https://docs.alpaca.markets/us/docs/regulatory-fees)。

## 分阶段验收与回滚

以下均为后续验收目标，尚未执行账户集成；当前实际离线结果见 [readiness](../audit/readiness.md)，修复顺序见 [next-prs](../audit/next-prs.md)。

1. **只读阶段**：先以无凭证固定 HTTP/流事件样例测试字段映射、分页、feed 变化、限流、拒绝和错误端点；获用户只读授权后才核对 Paper 身份、账户状态、300 美元初态、持仓、开放订单、成交/账户活动、证券属性、日历/时钟与数据授权。只读代码路径不持有下单能力；权限未满足时保留外部阻塞。
2. **订单计划阶段**：保留全部原有整股边界，再增加独立手算的 300 美元高价股碎股目标、精度量化、不可碎股拒绝、最小金额/残余、挂单预留和未成交卖款用例。qty/notional 互斥、SDK 数量/限价精度、碎股 limit + DAY 的 TIF/拒绝/未成交、paper/live 误配分别检查；文档冲突未确认保持阻塞，不生成任何远端订单。
3. **生命周期阶段**：离线注入超时已接受、部分成交崩溃、撤单中继续成交、事件乱序/重复、连接恢复、手工交易、入出金及分红，验证唯一意图、持久化、账户单执行者和无法解释差异时停止新增风险。全部关键 A–N 故障门槛要有独立预期证据。
4. **有人监督 Paper 阶段**：需另行交易授权；先关闭 readiness 中 F01 未来行业风控泄漏、F02 目标外持仓漏报，以及碎股限价组合权限与行为的阻塞，完成完整备份与恢复演练，随后取得实际订单/成交/现金/持仓一致性证据。Paper 不模拟的活动留在明确标记的独立研究账本。完成单次闭环不自动批准持续运行。
5. **持续 Paper 阶段**：错过调度告警、交易日历、外部心跳、重启恢复、固定版本及回滚验收后，建议观察至少 20 个交易日且至少 4 次正常调仓，并完成关键故障测试。这是本任务建议的工程门槛，不是法规或盈利证明。实盘还需资金阻塞清零、人类批准固定版本/预算及券商原生界面的独立暂停/持仓处置能力。

回滚先关闭新增风险和调度，确认唯一执行者停止，再保存运行清单、已发订单、成交、活动及幂等记录；旧版本只在状态 Schema 兼容并完成券商事实同步与对账后恢复。不得用恢复旧数据库或重置 Paper 账户删除新订单事实。若适配器或免费数据路线失败，保持停机/只读诊断；离线演示仍可单独运行，但不把 FakeBroker 静默接到 Paper 任务冒充恢复。此 ADR 本身仅为审计建议，撤回可删除本文件且不会改变现有运行行为。

本次未改变逻辑、公式、公共接口、风险配置、依赖锁或测试断言，因而未更新这些文件及其 Schema/测试；需求映射由上述 R/QC 关联和审计报告承担。上线依据、实测状态和敏感变更复核不得由本 ADR 自行批准。
