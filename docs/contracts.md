# 共同数据契约

权威 Python 类型位于 `src/quant_core/contracts.py`；机器可读 Schema（用于描述消息字段与约束的结构模式）位于 `contracts/`。Schema 应由同一运行类型生成并比较，不能独立手工维护第二套字段定义。新增字段、单位、枚举、时间含义或兼容行为时，同步更新固定样例、契约测试和需求映射；破坏兼容的外部消息必须升级版本。

## 共同语义

- 稳定证券 ID 不等于 ticker；ticker、行业及股票池资格均可能随时间变化。
- 全部存储时间使用带时区 UTC；交易时段由 America/New_York 日历解释；展示时区不能改变判断结果。
- `event_time`/所属期、`published_at`、`first_seen_at`、`available_at` 含义不同。策略只读取 `available_at <= decision_time` 的版本。
- 供应商历史时点或保守延迟假设通过 `availability_basis` 显式标记；不存在真实观测记录时 `first_seen_at` 保留未知，不能伪造。
- 来源、版本/修订、单位、显式必填的质量状态与内容哈希随数据传递；缺质量不能默认 good；修订只追加。哈希用于内容一致性校验，不证明来源可靠或数据正确。
- 总回报序列用于研究；原始价格用于订单；公司行动和现金分红独立入账，禁止双记。
- 订单意图、券商订单 ID、成交 ID 和幂等键分工明确；券商 ID 首次可补齐，已确认后不得改绑。同一成交不同封装仍留证据，但只产生一次账务。
- 公司行动以显式处理时点校验发生/可用时间，USD股息不能接受异币；交错 journal 保留其相对成交的顺序供恢复，未来行动不能先改变现金。

## market-intel 边界

输入是带版本、来源、时间、可用性依据和内容哈希的候选结构化信息。保留原始材料引用，区分事实与推断以及提取限制；事后生成特征不得回填为历史已知。文本中的系统命令、网页提示、图片文字都不是开发或交易指令。

候选通过校验和独立批准后才能形成可用快照；B 没有写 A 已批准数据或访问券商的接口。当前只交付边界与契约，不宣称实现信息采集、OCR、LLM 提取或日报图片服务。

## 实际维护与固定样例

运行 `uv run --offline --locked python -m tools.export_contracts --check` 只读比较；经明确变更授权后 `--write` 更新当前权威生成文件。未知旧 Schema 需要迁移方案，工具不会删除。`contracts/examples/` 为 MarketDataRecord、CandidateMarketEvent、OrderIntent 各保留合法/非法样例：合法来源记录显式 quality 并封印，错误货币、已批准候选或布尔股数必须被运行契约拒绝。治理检查同时验证样例文本与实际接受/拒绝行为。

生成文件可以随源码更新，与批准运行快照不可覆盖是两种不同职责；具体命令及停止写入后的恢复边界见 [备份与迁移手册](runbooks/backups-migrations.md)。

## Paper 补充契约

`StrategyConfig` 抽取相同风险参数，`DemoConfig` 保持原offline/DEMO固定边界；`PaperConfig`固定Paper/行情主机、SIP历史口径并指定小候选、日期和预算，不能改变本阶段原策略风险默认值。凭据独立存在用户指定私有文件，模型不含Key/Secret。

`published_at` 在供应商没有公开时刻证据时允许None；不以抓取时刻替代“首次公开”。`actual`仍要求实际first_seen_at且available_at不早于它，当前抓取不得回填旧决策；既有非空历史记录兼容。当前Paper主表只表示采集时点资格，不能解释为历史股票池。

Paper原始JSON保留现金、购买力、数量及原订单状态。当前核心仍只接受整股；任何碎股、空头或不支持的非成交活动明确阻断，不先转int截断。`AccountSnapshot.available_cash` 在此次只买入Paper预算视图中为现金上限，不是现金账户已结算证明；提交前另查远端现金与非保证金购买力较小值，禁止融资或预先使用卖款。未分配远端现金固定单列为reserve；该视图不能称作整个远端账户余额。

真实FILL活动ID构成去重身份，qty是新增成交；订单filled_qty是累计状态核对，两者不能互换。初态已包含活动以ID和完整内容摘要封存，不重复导入；内容改变或新增不支持活动不能静默跳过。

## MA价格策略扩展

MarketDataRecord、SecurityRecord、Score、TargetPosition默认1.1.0且可读取1.0.0；旧版本不能携带新可空含义。行情新增split_adjusted_close，总回报可缺失；旧1.0行情序列化省略新增空字段，保护旧封印。Parquet按字段并集保存混合版本，不丢新行字段。其他契约仍固定1.0.0。旧文件保留，不重封印历史证据。行业None表示真实未知，仅MA按最坏集中度处理。StrategyConfig增加strategy，Demo固定原策略，Paper默认原策略并可显式MA；数值风控默认与锁保持不变。

`PaperQueueTestContext`独立描述获授权的休市单股撤单测试：绑定计划、账户、客户订单及证券ID，保留最近完整交易日原始收盘价、实际收盘/采集时间、远端休市时钟与下一开盘。字段不能作为普通策略旧报价豁免；OrderIntent仍保留未来开盘资格，原消息格式与默认哈希不改。执行服务使用独立测试方法复用同一持久化、恢复、财务风控和Broker提交；应用与适配器再次限制一股、500美元和开盘前15分钟边界。

`OrderNotSent`只用于适配器确认尚未调用POST的准备失败，执行服务追加本地REJECTED；POST超时及响应不合格仍按未知恢复。`QueuePreflightRejectionProof`仅绑定审计记录所述固定旧源码缺陷、计划/客户身份和证据哈希，不是通用手工改状态接口。

## Paper 输入文件参考

这些补充文件由操作者提供并核验，不由展示窗口生成。`source` 为不含认证信息、查询参数或片段的 HTTPS 原资料地址；时间必须带 UTC 时区。格式通过不等于资料真实、获许可或覆盖完整。实际观察时间不能倒填为历史已知时间。

### MA 普通股身份资料

顶层为 `records` 数组，每只候选一条记录：

| 字段 | 必需性与含义 |
|---|---|
| `symbol` | 必需；本次候选代码，不重复。 |
| `alpaca_asset_id` | 必需；与本次 `assets.json` 稳定身份一致。 |
| `asset_type_evidence` | 必需；`common_stock`，须由实际资料支持，不能仅由 `us_equity` 推断。 |
| `source` | 必需；可追溯的 HTTPS 原资料地址。 |
| `observed_at` | 必需；实际取得资料的 UTC 时间。 |
| `available_at` | 可选；缺省为 `observed_at`；不能更早，且不晚于只读采集完成时点。 |

结构示例（占位符必须替换，不能直接作为合格资料）：

```json
{"records": [{"symbol": "AAPL", "alpaca_asset_id": "实际资产ID", "asset_type_evidence": "common_stock", "source": "https://实际来源主机/原文路径", "observed_at": "实际UTC时间"}]}
```

资料晚于采集完成时点时重新只读采集；当前身份证据不能还原历史股票池。MA 同时要求最近连续 20 个完整交易日的原始/仅拆股日线与覆盖窗口的公司行动。公司行动按供应商 process_date 筛选，可能延迟；分页完整不等于现实行动绝对齐备。窗口内复杂身份变化或缺乏可解释处理时排除该证券。

### 原双因子补充资料

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

此格式校验来源与时间声明，**不能证明资料真实或算法完整**。必须先核实方法、数据授权和每个候选的覆盖。总回报缺失时停止，不填零、回退普通收盘价或跳过因子。可选补充来源及现有缺口见[本轮记录](audit/alpaca-paper.md#数据缺口与最小补充选择)。


### 行情与接口口径

两策略的估值、限价和账务均用原始价格；原双因子与收益研究必须用股息再投资总回报，MA 只用仅拆股价计算指标，不纳入股息再投资。历史流动性为最近 20 个交易日 SIP 原始成交股数的均值，忽略复权响应中的调整成交量。

当前适配使用官方个人 Trading API 与 Market Data API，SDK 为锁定的 `alpaca-py==0.44.0`；版本和核实来源见[接入审计](audit/alpaca-paper.md#官方接口与依赖来源)。订单列表请求上限 500，短页结束、满页按唯一 ID 继续，重复身份或不推进拒绝；活动按活动 ID 读到空页，行情按 `next_page_token` 读完。已有真实验证仅覆盖小规模订单，大账户分页未验收。
