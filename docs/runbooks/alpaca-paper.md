# 运行一次 Alpaca Paper 策略

本页用于把真实行情接入策略，并在明确授权的 Alpaca 模拟账户执行一次受控操作。先完成[快速开始](quickstart.md)的项目安装；以下命令都在仓库根目录运行。`demo` 仍完全离线，Paper 入口会连接远端，`uv --offline` 只限制依赖下载。

当前支持小候选范围、首次空仓建仓、整股买入限价 DAY 单（当日有效）和同目录恢复。已有持仓或开放订单时停止；不重置账户、不处理范围外订单。实际成交及成交入账仍待真实验收，最新事实见[项目状态](../../PROJECT_STATE.md)。不支持实盘、自动卖出、碎股或持续交易。

## 选择策略与操作

| 路径 | 数据条件与用途 |
|---|---|
| **MA5/MA20** | 使用 `configs/alpaca-ma.example.toml`；最近连续 20 个完整交易日的 SIP 仅拆股调整价格，以及有来源的普通股身份证据。允许行业未知并按最坏集中度限制。以下主线优先说明此路径。 |
| **原周频双因子** | 使用 `configs/alpaca-paper.example.toml`；需要至少 253 个连续交易日的合格总回报价格、普通股身份与行业资料。当前补充资料仍不齐备，见第 3 节分支。 |
| **休市提交与撤单测试** | 独立授权后，从 MA 首个可行目标取一股，提交、查询并立即请求撤销。见[休市测试](#休市提交与撤单测试)，与实际成交验收分开。 |
| **查看已保存结果** | 无需凭据或账户访问，直接跳到[查看结果](#6-查看结果)。 |

程序默认策略仍为 `weekly-two-factor`；只有显式 `strategy="ma-trend"` 才选 MA。两者公式和价格口径分别见[原策略](../strategies/weekly-two-factor.md)与[MA 策略](../strategies/ma-trend.md)。所有路径都使用唯一执行服务，没有 Paper 失败后的合成数据替代。

## 1. 配置与凭据

**首次账户访问前，由账户持有人指定凭据的安全来源和 Paper 账户 ID，并明确授权只读访问。** 不要扫描未知密钥或把秘密发送到聊天。账户 ID 不是 API Key；需先从账户资料确认。模板的空 ID 只是待填写项，仓库 CLI 没有自动发现账户 ID 的命令。

在本地编辑器操作：

1. 将 [凭据模板](../../configs/alpaca-paper.env.example)复制为根 `.env`，已有文件不覆盖。填写 Paper Key、Secret、指定账户 ID，然后运行 `chmod 600 .env`。也可使用另一个明确指定的私有文件路径，并相应替换 `--credentials`。
2. 将 [MA 配置模板](../../configs/alpaca-ma.example.toml)复制为 `artifacts/paper.toml`（先创建 `artifacts/`，已有配置换名）。原策略改用 [双因子模板](../../configs/alpaca-paper.example.toml)。凭据不写进 TOML；`.env` 和 `artifacts/` 均被 Git 忽略。
3. 将 `account_id` 改成同一个指定账户 ID；核对候选、`budget`、`history_start` 和 `history_end`。模板日期仅为示例，必须改为本次所需历史窗口，覆盖至采集时最近完整交易日；不把未完成日线放进指标。预算是分配给策略的 USD 资金，不是远端全部余额或购买力。
4. 先取得可核验的普通股身份资料并保存真实观测时间，格式见[身份资料契约](../contracts.md#ma-普通股身份资料)。若稍后才完成身份绑定或取得资料，应重新只读采集形成新时点，不倒填时间。

交易端点仅接受 `https://paper-api.alpaca.markets` 或其 `/v2` 写法，后者会规范化；行情端点固定 `https://data.alpaca.markets`，没有实盘回退。首次读取比对指定账户身份、USD、ACTIVE 和交易禁止标志。风险参数保留原设计，不能提高仓位或放松限制以凑出订单；字段参考见[Paper 配置](../configuration.md#独立-paper-配置)。

下文使用 `artifacts/paper-read-01` 和 `artifacts/paper-plan-01`，**都必须是新目录**；重复采集或重新计划时换新名称并同步后续命令。运行目录可能包含账户资料，应保存在受控本地位置，不提交 Git。

## 2. 只读核验

在上述只读授权范围内执行：

```bash
uv run --offline --locked quant-core paper-read \
  --config artifacts/paper.toml --credentials .env --output artifacts/paper-read-01
```

Conda 路线将每条业务命令的 `uv run --offline --locked quant-core` 换成 `conda run -n quant-core-dev quant-core`，其余参数不变；例如：

```bash
conda run -n quant-core-dev quant-core paper-read \
  --config artifacts/paper.toml --credentials .env --output artifacts/paper-read-01
```

成功采集后查看 `read-result.json`：`account_verified: true` 表示身份已核验，`history` 说明行情获取情况，`blockers` 列出继续计划所需资料或阻塞。**退出码 0 和 `status: read_only` 仍可能伴随行情阻塞，必须阅读这些字段。** 此步骤不生成策略或订单。

目录保存账户、持仓、开放/历史订单、活动、证券、时钟、日历及 SIP 原始日线；MA 另存仅拆股日线、公司行动和请求参数。SIP 是综合市场行情，IEX 是单一交易所；历史流动性使用最近 20 日 SIP 原始成交股数，不能拿 IEX 量替代。权限失败不自动切换来源。当前资产列表只表示采集时资格，不是历史股票池。

若账户非空、行情权限失败、缺日或资料不全，保留结果并处理原因，不进入下单。具体[失败处理](#失败与恢复入口)见下文。

## 3. 计算策略计划

### MA 主线

把已核验的身份资料保存为 `artifacts/identity-evidence.json`。每项资料需绑定 `assets.json` 中的稳定资产 ID，且观测/可用时间不晚于这次只读完成时间；若资料后来才取得，先换新目录重做第 2 步。字段格式集中在[契约参考](../contracts.md#ma-普通股身份资料)。

```bash
uv run --offline --locked quant-core paper-plan \
  --source artifacts/paper-read-01 --identity-evidence artifacts/identity-evidence.json \
  --output artifacts/paper-plan-01
```

这一步只读取保存资料并计算，不访问账户。打开 `report.md`、`data-qualification.json`、`factors.json`、`signals.json` 和 `target.json`：应能看到 MA5、MA20、强度、评分、排除原因及最多 3 个可行整股目标。`planned` 表示存在目标；`no_target` 表示没有可执行目标，应在此停止，不改公式凑单。

MA 仅在 `MA5 > MA20` 时入选，目标基础额度仍为策略净值 4.5%，不是把全部预算均分给三只股票。整股买不起时留现金。原始价格用于估值和执行，仅拆股价用于指标，缺失总回报不补造。未知行业按最坏情况占用原行业额度。

### 原双因子分支

原策略使用独立的 `artifacts/supplement.json`，包含有来源的普通股类别、行业以及股息再投资总回报价格；完整字段、时间要求与价格口径见[补充资料契约](../contracts.md#原双因子补充资料)。Alpaca `adjustment=all` 本身不足以证明总回报方法合格，缺失时停止。

在原策略配置采集完成后，用下面命令代替 MA 计划命令：

```bash
uv run --offline --locked quant-core paper-plan \
  --source artifacts/paper-read-01 --supplement artifacts/supplement.json \
  --output artifacts/paper-plan-01
```

原策略要求采集时点处于该周最后交易日收盘后、下一周首个常规开盘前；日历与行情至少覆盖 253 个连续交易日及下一执行日。补充资料必须在采集完成前已可用，否则重新采集。不能把新取得的版本回填为上周五已知。原评分和风险参数保持不变。

### 两种计划共同核对

`paper-plan.json` 保存 `plan_id`、输入哈希、代码/依赖信息及 `eligible_at`、`expires_at` 执行窗口。MA 每日形成决策，盘中采集可使用当天剩余常规时段；原策略保持周频。过期计划不自动补执行。

`initial.json` 的策略现金等于批准预算；`paper-plan.json` 的 `reserve` 是初始远端现金减策略预算，固定后不再调整。内部策略现金加固定未分配现金应与远端现金核对，不能用购买力扩大预算或调账消除差异。缺基准行情时市场状态为 UNKNOWN，沿用仅观察设计，不改变预算。

## 4. 确认执行边界，再提交与观察

**示例命令不是交易授权。** 首次发单前，向账户持有人展示具体计划 ID、候选、指标/目标、独立预算、订单数量与金额边界、执行时间，以及未成交保留还是撤销；获得本轮具体方案的最终确认。`PLAN_ID`、`N`、`USD` 分别替换为已审阅计划 ID、获准的最多订单数、单笔含费用预留的美元上限；`keep` 也必须与已确认处理方式一致。

```bash
uv run --offline --locked quant-core paper-execute \
  --run-dir artifacts/paper-plan-01 --credentials .env --approve-plan PLAN_ID \
  --max-orders N --max-order-notional USD --unfilled keep --observe-seconds 300
```

MA 硬边界最多 3 笔、每笔含费用预留不超过 500 USD；用户批准或原风控更严格时优先执行。发单前再次核验账户、资产交易资格、远端时钟和新鲜原始报价。默认执行报价取 IEX 卖价，不保证全市场最优价；超过 60 秒、缺报价或权限失败都阻断。普通模式只在计划的常规交易时段执行，不用旧收盘价替代实时执行报价。

买入限价由原 1% 缓冲及报价精度约束形成，提交前向下取整；先持久化订单身份，再经唯一执行服务提交。取得远端订单 ID 表示受理，成交须由真实逐笔 FILL 活动确认，费用预留不是实际手续费。

默认每 5 秒查询，最多观察 300 秒，全部终态时提前结束。`--observe-seconds` 可设 0—300；0 不运行观察窗口。`--unfilled keep` 保留本轮未完成 DAY 单；`cancel` 在观察结束后仅请求撤销本轮余单，有观察窗口时再每 5 秒核对、最多 60 秒。已开始的网络请求可能晚于截止返回。撤单请求不等于撤单完成；超时或未知状态保留待恢复，不重复发单。

## 5. 恢复核对

重新启动同一计划的恢复入口，沿用已批准边界和未成交处理方式：

```bash
uv run --offline --locked quant-core paper-recover \
  --run-dir artifacts/paper-plan-01 --credentials .env --approve-plan PLAN_ID \
  --max-orders N --max-order-notional USD --unfilled keep
```

普通恢复不执行 300 秒观察窗口，不发新单；它查询既有身份、去重导入逐笔成交并核对现金、持仓、订单与累计成交。`keep` 不请求撤单，`cancel` 会按授权撤本轮未完成单。**休市测试目录的恢复仍可进入最多 60 秒撤单核对循环**，网络请求也受超时约束。停止程序或退出非零均不能推定远端已撤单。

每次运行追加 `observations/0001` 等目录，不覆盖旧观察。修复代码后可恢复并记录新旧源码指纹；新发单仍要求计划源码一致。不得重建初始账户、换目录重发或调整 reserve 消除差异。

MA 正常成交验收要求：至少一笔完全成交，其余本轮订单均为已确认终态，账户核对一致，并在独立重启恢复后保持一致。只有部分成交、拒单、无目标或撤单成功时，应如实保留相应阶段。

## 6. 查看结果

先看计划根目录的 `report.md`，再看最新数字编号的 `observations/` 子目录中的 `report.md`、`result.json` 和 `reconciliation.json`。前者解释决策，后者记录实际订单、成交和差异；成功判定依据保存的阶段字段，不能仅看进程退出码。

### 本地展示窗口

`--run-dir` 指向完整的**只读采集目录或计划根目录**，不要只指向某个 `observations/0002`。例如查看本手册计划：

```bash
# uv
uv run --offline --locked python -m tools.paper_viewer --run-dir artifacts/paper-plan-01 --port 8765

# Conda，无需激活
conda run --no-capture-output -n quant-core-dev python -m tools.paper_viewer --run-dir artifacts/paper-plan-01 --port 8765
```

已激活 Conda 项目环境时，直接运行 `python -m tools.paper_viewer --run-dir artifacts/paper-plan-01 --port 8765`。浏览器打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)；只有只读结果时，将路径换成 `artifacts/paper-read-01`，尚无因子/订单的阶段会显示未完成。

已有保存结果即可查看，无需重新运行策略或等待交易。新检出不含这些生成目录，应先采集，或使用快速开始的离线 demo。刷新只重读本地固定 JSON，选择最新数字观察；不读取凭据、不访问账户、不下单。服务只绑定 127.0.0.1；Ctrl+C 停止窗口不改变订单，端口占用时换新端口。展示不重新验证全部输入哈希，正式校验仍由计划/执行入口负责。

Paper 计划没有适用的独立 `validate/replay/research` CLI；这些命令用于离线 demo。Paper 使用输入哈希和同目录 SQLite 事件恢复。完整产物与命令见[参考页](../commands.md)。

## 休市提交与撤单测试

此项需单独明确授权，使用一个新建且没有订单的 **MA 计划目录**；普通执行目录与测试目录不能互相接管。完成前面的配置、只读、计划与具体边界确认后，把下面的 `PLAN_ID` 换成这个计划的 ID：

```bash
uv run --offline --locked quant-core paper-execute \
  --run-dir artifacts/paper-plan-01 --credentials .env --approve-plan PLAN_ID \
  --max-orders 1 --max-order-notional 500 --unfilled cancel --queue-cancel-test
```

从 MA 目标顺序取首个证券的一股，使用最后完整交易日的原始收盘参考限价并向下按报价精度取整，不称为实时卖价。要求指定 Paper 账户空仓、无其他订单，远端与保存日历均休市、下一开盘一致且至少在 15 分钟后；单笔含费用最多 500 USD，目标、资金、行业、单票、流动性与换手仍经共同风控。

提交一股 BUY LIMIT DAY、不含扩展时段的订单，取得远端 ID 后再次查询并立即请求撤销，不先等 5 分钟；每 5 秒核对最多 60 秒。再用第 5 节同目录恢复命令，保持 `--max-orders 1 --max-order-notional 500 --unfilled cancel`，不再传测试开关。

`queue_cancel_verified` 要求远端已确认 CANCELED、零成交且现金/持仓一致；`queue_restart_verified` 另要求重启核对一致。撤单中、未知、意外成交或差异都不满足这项验收。此结果与正常策略的实际成交、成交入账、卖出能力分开报告。

## 失败与恢复入口

| 问题 | 处理 |
|---|---|
| 凭据、端点或账户不符 | 核对用户指定的私有来源及配置；不搜索其他密钥、不换账户试单。 |
| SIP 权限、缺日、身份或公司行动不明 | 读 `read-result.json` 或 `data-qualification.json`，补资料后新目录采集/计划；不切 IEX 量、编造行业或总回报。 |
| 市场关闭、计划过期、无信号或整股不可行 | 记录停点，不自动预约或提高预算/仓位；正常执行等待另一次合格计划及相应授权。 |
| 提交超时、UNKNOWN、CANCEL_PENDING | 保留目录，用第 5 节同身份恢复。未知订单不得盲目重发，撤单中仍可能成交。 |
| 现金、持仓、费用或累计成交差异 | 停止新增风险，查观察中的远端事实与内部 journal；不重置账本或改 reserve。 |
| 新增非 FILL 活动、碎股、空头或人工订单 | 当前范围无法自动处理，保留原始响应，转[故障恢复](recovery.md#单次-alpaca-paper-的恢复边界)诊断。 |

初态已有活动按完整内容哈希固定，不重复入账；同 ID 内容改变或新增不支持活动会阻断。真实 API 多次读取不是原子快照，临时差异需再恢复确认。特定旧版本 POST 前失败的定向维护只适用于已证明的源码及原因，见[旧版故障维护](recovery.md#已知旧版预提交失败的维护)，普通 404 不构成未发送证明。
