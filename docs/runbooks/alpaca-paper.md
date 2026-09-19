# 运行一次 Alpaca Paper 策略

这些入口把真实Paper账户和行情接到指定策略与唯一执行服务。默认 `demo` 仍完全离线；Paper默认原双因子，显式 `strategy="ma-trend"` 使用独立[MA价格趋势](../strategies/ma-trend.md)。原策略仍缺总回报/行业资料，新策略及真实交易验证状态见[MA接入记录](../audit/ma-paper.md)。

本阶段仅支持指定的小候选范围、首次空仓建仓、整股限价 DAY 单，以及同目录恢复。已有持仓或开放订单时停止，不重置账户、不撤范围外订单。不支持持续运行、自动卖出、碎股交易或实盘。碎股/空头等原始响应保留；核心无法表达时明确拒绝转换。

## 安装与凭据

沿用[快速开始](quickstart.md)的 Python3.12 环境。uv 执行 `uv sync --locked`；Conda 专用环境执行 `python -m pip install --require-hashes -r requirements.txt` 和首次项目安装 `python -m pip install --no-deps -e .`。SDK固定为官方 `alpaca-py==0.44.0`，普通模块导入和测试不访问账户。

首次使用时复制 `configs/alpaca-paper.env.example` 到根 `.env`，执行 `chmod 600 .env`，在本地编辑器填入 Paper Key、Secret、账户ID。已有文件勿覆盖。`.env` 已被Git忽略；不要把密钥粘贴到聊天、命令参数、配置TOML或报告。凭据只从显式 `--credentials` 路径读取，不自动搜索、加载shell或试用其他密钥。

端点只接受 `https://paper-api.alpaca.markets` 或其 `/v2` 写法；后者在交给SDK前规范化，SDK自行加版本。拒绝其他主机和实盘地址。凭据文件的账户ID须与 Paper配置一致；接口首次读取也比对账户ID、USD、ACTIVE和交易禁止标志。配置中的数据主机固定 `https://data.alpaca.markets`。

复制 `configs/alpaca-paper.example.toml` 到本地忽略目录，替换 `account_id` 和历史日期。候选与预算必须事先确认。本阶段风险数字固定为原设计，不能在Paper配置中提高仓位、行业、换手、报价年龄或亏损阈值以完成订单。

## 1. 获授权的只读核验

```bash
uv run --offline --locked quant-core paper-read \
  --config artifacts/paper.toml --credentials .env --output artifacts/paper-read-01
```

`--offline` 只限制uv下载依赖；此显式Paper命令会连接固定远端。Conda前缀换成 `conda run -n quant-core-dev quant-core`，其他参数相同。

新目录保存账户、完整持仓、开放及历史订单、活动、资产、时钟、日历和SIP原始日线。金额保留原始字符串；购买力不是现金，也不是策略预算。只读命令成功不代表数据合格，必须阅读 `read-result.json` 的阻塞原因；行情权限失败不切到IEX或合成数据。分页中断/游标重复不签发完整数据结果。

`assets` 只表示本次查询时证券状态，不能据此构造历史股票池。`us_equity` 也不足以证明普通股类别。原策略仍需253个连续交易日总回报收盘价、原始价格和最近20日SIP股数成交量；IEX为单一交易所，不能充当综合ADV。

## 2. 准备补充资料并计算原策略

Alpaca `adjustment=all` 未经方法核对不能填入 `total_return_close`。补充JSON必须保存供应商来源、实际观测及可用UTC时间、普通股类别、行业分类和股息再投资总回报序列。格式如下，省略号表示需要实际完整数据，不能直接当作输入：

```text
schema_version: "1.0.0"
quality: "good"
price_basis: "dividend_reinvestment_total_return"
currency: "USD"
source: 不带查询参数和认证信息的 HTTPS 原资料地址
methodology_source: 总回报方法的 HTTPS 原资料地址
methodology: 可审计的方法说明及分类体系说明
observed_at / available_at: 真实 UTC 证据时间
securities: [{asset_id, symbol, sector, asset_type: "common_stock"}, ...]
prices: [{asset_id, session: "YYYY-MM-DD", total_return_close}, ...]
```

此格式校验来源与时间声明，**不能证明资料真实或算法完整**。必须先核实方法、数据授权和每个候选的覆盖。总回报缺失时停止，不填零、回退普通收盘价或跳过因子。可选补充来源及现有缺口见[本轮记录](../audit/alpaca-paper.md#数据缺口与最小补充选择)。

```bash
uv run --offline --locked quant-core paper-plan \
  --source artifacts/paper-read-01 --supplement artifacts/supplement.json \
  --output artifacts/paper-plan-01
```

这一步只计算，不访问账户、不写订单。决策时点是资料采集完成后，不能把周末才取得的版本写成周五已知。必须处于该周最后交易日收盘后、下一周首个常规开盘前；所有补充资料需在该次采集完成前可用，否则重新只读采集。日历必须覆盖253个历史交易日和下一执行日。

生成 `snapshot/factors/signals/target/initial/regime.json`、`report.md` 及含输入哈希的 `paper-plan.json`。原评分和4.5%基础权重保持；预算10000美元每票基础额度450美元，整股向下取整，买不起时留现金。未提供市场基准时状态为UNKNOWN，依原“仅观察”设计不调整预算。不能称空目标为已成交闭环。

初始策略现金仅为批准预算；`reserve` 固定保存远端现金减预算。内部现金加这笔固定未分配现金才与整个账户比较；差异不能通过修改reserve、重置账本或补单消除。

## 2A. MA价格趋势计划

使用 `configs/alpaca-ma.example.toml`，替换账户ID和历史日期；需要覆盖至少20个完整交易日。沿用前述paper-read命令，另外取得 `split-bars.json`、完整分页的 `corporate-actions.json`、带asof及raw/split口径的 `data-requests.json`。公司行动日期筛选按process_date，供应商可能延迟处理；分页完成不证明现实行动绝对齐备，不把未采到事件理解为绝对没有事件。

`--identity-evidence` 是有来源的普通股身份JSON：顶层records，每项含symbol、alpaca_asset_id、asset_type_evidence="common_stock"、source（HTTPS原文地址）、observed_at（实际UTC观测时间），available_at若有也须不晚于本次采集。它必须绑定当前资产，不能仅凭us_equity推断普通股。既有公开摘要可作为当期证据，不能伪造历史成员资格；来源内容与绑定还需独立核验。

```bash
uv run --offline --locked quant-core paper-plan \
  --source artifacts/ma-read-01 --identity-evidence artifacts/identity-evidence.json \
  --output artifacts/ma-plan-01
```

MA不需要--supplement，不会填入假总回报。相同行情日期对齐、原始成交量、复杂公司行动及身份核验后，保存最多20日的快照、三项指标、单因子评分、最多3个可行目标和 `data-qualification.json`；不合格候选记录原因。全部不合格或MA信号为空时保留空计划，不下单。预算与未分配现金口径同原策略。

## 3. 确认计划后单次执行

首次发送前明确确认计划中的候选/预算、订单总数量上限、单笔含费用预留金额上限，以及未成交单保留或撤销。把 `PLAN_ID` 换成已审阅 `paper-plan.json` 的计划ID；`N` 与 `USD` 必须由授权者明确给定，示例不提供隐含交易批准。

```bash
uv run --offline --locked quant-core paper-execute \
  --run-dir artifacts/paper-plan-01 --credentials .env --approve-plan PLAN_ID \
  --max-orders N --max-order-notional USD --unfilled keep
```

执行时只允许计划规定的常规交易时段（MA盘中计划可用当日剩余时段），核对远端时钟、最新资产资格与新鲜原始报价。默认最新执行报价使用明确IEX卖价，历史流动性继续使用SIP；IEX不是全市场最优报价保证。报价超过60秒、无报价或权限失败阻断，不用旧收盘价代替。

规划输出整股意图；买限价在持久化前按Alpaca允许精度向下取整（1美元及以上两位、以下四位），不扩大1%价格缓冲。稳定订单身份不随价格或重启变化。先由SQLite保存意图，经原风控、唯一 `ExecutionService.submit` 后才调用远端。费用预留仍使用原保守模型，实际账务使用Paper活动事实，不把预留费用当作真实费用。

CLI默认 `--observe-seconds 300`，每5秒恢复查询，达到终态提前结束；参数范围0—300秒。观察截止以单调时钟计量，已开始的网络请求受10/30秒连接/读取超时约束，可能晚于截止返回。MA固定最多3笔且每笔含费用预留不超过500 USD，参数不能扩大该边界。

`keep` 保留本轮未完成DAY订单；`cancel` 在该次观察结束时经原执行服务请求撤销本轮开放订单。有观察请求时，撤单后再每5秒核对、最多60秒，不重复撤销CANCEL_PENDING；网络请求同样可能在截止后返回。不会无限等待，撤单请求不等于撤单成功；未知或核对失败时不盲目改写。市场关闭或没有可用目标时如实停止，不能改公式或提高预算凑单。

## 4. 观察、恢复及停止

```bash
uv run --offline --locked quant-core paper-recover \
  --run-dir artifacts/paper-plan-01 --credentials .env --approve-plan PLAN_ID \
  --max-orders N --max-order-notional USD --unfilled keep
```

恢复不等待观察窗口，只查询同一批身份、导入逐笔FILL、核对和按既定方式处理未成交订单，不发新单。修复源码后允许恢复并记录新旧源码指纹，新发单仍要求计划源码不变；不能重建初始账户消除已发生事实。使用 `keep` 是纯恢复，`cancel` 仍有撤单副作用，须在已确认范围内。

`observations/0001` 等追加目录保存远端原始事实、本地账户/订单/事件/journal、风险原因、对账、摘要和中文报告。提交成功、部分成交、全部成交、对账一致分别计数。MA最小验收须至少一笔完全成交、其余本轮订单均已确认终态，并在独立重启恢复后保持对账一致；仅部分成交不算完整验收。`submitted=0` 或只有拒单不是闭环完成；金额/成本精度不一致时保留差异，不使用容差或修改账本消差。

初始账户中已经包含的活动以完整内容哈希固定，恢复不重复入账；相同ID内容改变或新增非FILL活动阻断，需要独立解释资金/公司行动。当前不支持任意非成交活动账务、实时流或跨主机执行。已有同机账户锁不控制券商网页上的人工操作。

输出目录不可覆盖。Paper计划不适用原离线 `validate/replay/research` 清单；原命令保留用于离线demo。Paper目前有输入哈希和SQLite事件恢复，无独立Paper重放CLI；不得冒称该部分已验收。普通测试在模块收集前封锁socket连接，不能代替系统级网络防火墙。完成一次有据可查的人工观察后停止，不自动部署或预约后续交易。

## 休市提交与撤单测试

只有明确授权此项测试时，才给 `paper-execute` 加 `--queue-cancel-test`。使用单独新建、没有订单的MA计划目录，`--max-orders 1 --max-order-notional 500 --unfilled cancel`；其余凭据和批准计划参数同普通执行。Conda仍只替换环境命令前缀。普通模式的开市、新鲜报价及风险参数不变。

该模式从MA目标顺序取首个证券的一股，限价使用最后完整交易日的原始收盘价并向下按报价精度取整，不高于该收盘价。它保存真实参考价格及收盘/采集时间，不称为实时卖价；缺最新完整日或20日原始成交量时阻断。远端和保存日历均须休市，下一开盘须一致且至少在15分钟以后；仅空仓、无其他订单、指定Paper账户、含费用最多500 USD。目标、资金、行业、单票、ADV和换手等仍经过共同风控。

订单为一股BUY LIMIT DAY、`extended_hours=false`，唯一执行服务先持久化，再向Alpaca提交。取得客户与远端订单身份、查询并保存撤单前事实后立即请求撤销，不先等待5分钟；每5秒核对最多60秒。网络请求可能在计时截止后返回。已知本轮订单在现金不一致时也允许撤销，差异仍保留；未知身份不盲撤，不重复发单。普通与测试目录不能互相接管订单。

`queue-context.json`、`queue-submission.json`、`queue-query.json`、`queue-before-cancel.json`分别保存来源边界、提交回执、再次查询及远端原始事实；末态仍保存到普通观察文件。重启运行同目录 `paper-recover`，边界仍为1笔、500美元、cancel。`queue_cancel_verified`要求远端确认为CANCELED、零成交且现金/持仓核对一致；`queue_restart_verified`另要求重启恢复一致。pending_cancel、超时或差异均未完成；取消成功也不证明成交入账、策略收益或卖出能力。

## 本地展示窗口

展示窗口单独放在 `viewer/`，由标准库工具提供页面；不导入交易核心或SDK，不读取.env，没有下单按钮。它只展示用户指定目录中已经保存的证据；“刷新证据”不会查询远端账户或让策略继续运行。

```bash
uv run --offline --locked python -m tools.paper_viewer \
  --run-dir artifacts/alpaca-paper-20260919/readonly-02 --port 8765
```

Conda使用 `conda run -n quant-core-dev python -m tools.paper_viewer`，后续参数相同。终端会给出本地地址 `http://127.0.0.1:8765`；用浏览器打开，Ctrl+C只停止窗口服务，不改变订单或持仓。端口被占用时改成另一个本地端口，不关闭未知进程。

`--run-dir`也可指向Paper计划目录，窗口选择其中最大数字编号的观察，显示因子原值/百分位、目标、原始行情走势图、真实订单状态与核对差异。只读阶段没有因子或订单时保留“未完成”，不画模拟成交曲线；加载离线demo则明确标注合成/FakeBroker来源，不能用它证明Paper闭环。

服务只绑定127.0.0.1，只有三个静态页面资源和一个经过字段筛选的JSON接口，没有任意文件下载/目录列表。只读取固定JSON路径，拒绝证据软链；账户ID只显示末8位。窗口不构成实时行情终端、授权系统或持续交易服务，也不证明输入文件没有被人工修改；严格输入校验仍由正式计划/执行入口负责。

## 已知旧版预提交失败的维护

正常新版本在POST之前的账户/时间/请求校验失败抛出明确的未发送异常，追加本地REJECTED；调用POST后的超时或坏回执仍保留待恢复。排队测试若券商时钟领先本机不超过1秒，会实际等待本机追上后再作原有严格校验，保存queue-clock-checks.json，不重写时钟或旧报价。

2026-09-20记录的一次旧版排队尝试在POST前失败，但旧服务保守记为UNKNOWN。仅此固定源码指纹和原因可使用 `paper-recover --confirm-unsent-source <旧源码目录>`，其余恢复参数保持1笔/500美元/cancel。应用先核实完整旧代码树、原计划和原始失败证据，保存unsent-proof.json；执行服务持锁确认唯一意图、无券商ID/成交、仅该单未确认差异及再次查询无远端订单后，追加本地REJECTED并再次核对。它不改现金、删除历史、解冻或重发。目录缺失、证据不符、其他差异或远端已出现订单时停止；普通404绝不授权使用此维护。详细原始版本及审查依据见[本轮审计](../audit/paper-queue.md)。

订单列表以请求上限500读取，短页结束、满页按唯一ID继续；满页游标不前进或页内重复均拒绝不完整结果。活动仍按活动ID读取到空页。本轮真实账户只验证小规模订单，不能据此推定大账户历史读取已完成真实压力验收。
