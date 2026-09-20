# Alpaca Paper 接入记录（2026-09-19）

> 历史记录：以下结论、路径及授权仅适用于文中日期和基线；后续实现与验证见[当前状态](../../PROJECT_STATE.md)。本地 `artifacts/` 证据未随 Git 分发，部分旧产物可能已不可用。

本轮用户明确授权真实接入开发、必要契约衔接和独立回归；覆盖根规则中前轮“仅注释/不得接账户”的任务范围限制，其他风控、中文说明、数据真实性与禁止实盘规则继续有效。起点为 `f48cd4fc8dfc15d55c45655c08a4a957771694c2`，工作区干净；120文件起始快照保存在 `artifacts/alpaca-paper-20260919/baseline/`。未提交、推送、购买数据或部署。

## 本轮范围与状态

原公式、评分、风险默认参数不变。F01行业时点、F02目标外持仓监控和真实查询期间成交误判未来事件已修复。公共风险参数抽为StrategyConfig，DemoConfig仍固定offline/DEMO；PaperConfig限制指定账户/候选/预算与固定端点。未知公开时间允许None，实际首次观测仍须有证据；已用既有工具同步Schema。

新增官方SDK适配、SIP日线/真实日历与外部补充资料接口、四个显式Paper命令。统一执行入口、SQLite幂等/事件去重和账务保留；真实金额不经SDK浮点模型，现金与购买力分开。整数核心遇碎股明确停止，未扩展为碎股策略；本轮只支持空仓首次买入，不自动卖出、重置账户或处理范围外订单。

| 验收层次 | 当前事实 |
|---|---|
| 代码实现 | 最小受控入口及故障适配已实现；总回报重建器、任意账户活动和碎股仍不支持。 |
| 离线验证 | uv与Conda完整check各230 passed/21 warnings，原demo/validate/replay全部成功。 |
| 真实身份/只读 | 经用户分阶段授权已成功读取指定Paper账户与10只候选。 |
| 合格策略输入 | 原始SIP日线已取得，行业/普通股类别/严格总回报补充资料未齐备。 |
| 真实因子/评分/目标 | 未运行，不用原始价替代总回报产生假结果。 |
| Paper下单/实际成交 | 均未执行，未请求首次下单最终批准。 |
| 真实成交后账户核对 | 未执行，账户只读成功不能替代此项。 |

关联QC-001/002/003/004/005/006/008/009/010/011/012/013/014/015。涉及契约、适配器、应用CLI、风险/规划/监控、恢复、测试、Schema、锁与文档；不改公式、默认风控参数、CI及现有治理检查器。原测试输入仅在新增严格主表检查所必需处补齐一致的主表，独立原业务断言保留；复核记录说明依据。

## 官方接口与依赖来源

2026-09-19核对官方个人Trading API与Market Data API，未混用面向券商合作伙伴的Broker API。通过PyPI公开元数据确定并真实解析 `alpaca-py 0.44.0`；`uv lock`新增SDK及8个传递依赖，原已锁定版本未升级；`uv sync --locked`安装、`uv export --locked --format requirements-txt --group dev --no-emit-project`导出共同清单。最初沙箱公开PyPI DNS失败退出6，获网络权限后重试成功；没有伪造锁。

- [官方SDK TradingClient](https://alpaca.markets/sdks/python/api_reference/trading/client.html)：使用paper=True，原始JSON传输避免金额经过float。安装版本源码核对了API版本、禁止重定向、默认重试；封装关闭所有重试并添加连接/读取超时。
- [历史日线](https://docs.alpaca.markets/us/reference/stockbars)：完整分页，显式raw及SIP，保留原始成交量；调整选项本身不足以证明严格股息再投资总回报。
- [个人行情权限](https://docs.alpaca.markets/us/docs/about-market-data-api)：套餐说明与实际账户调用分开，SIP历史查询成功不证明实时SIP权限。
- [订单](https://docs.alpaca.markets/us/reference/getallorders-1)及[限价规则](https://docs.alpaca.markets/us/docs/orders-at-alpaca)：订单ID分页、明确价格步长；发送后查状态和FILL，提交回执不是成交。

SDK构造不联网；测试响应独立构造，不从真实账户文件复制测试夹具。任何网络异常只留下安全类别/HTTP状态，不输出请求头、密钥或供应商异常正文。

## 真实只读证据

用户在本地权限600、Git忽略的 `.env` 填入密钥，先授权仅识别账户，随后确认指定身份并授权完整只读。控制台提供的Paper `/v2` 端点规范化后交给SDK，无密钥进入Git/报告。用户确认候选AAPL、MSFT、AMZN、GOOGL、META、NVDA、JPM、XOM、JNJ、PG，策略预算10000 USD；这不是订单发送授权。

身份查询账户ACTIVE/USD。第一次完整只读结果位于 `artifacts/alpaca-paper-20260919/readonly-01/`；2026-09-19 15:43 UTC完成，现金100000 USD、购买力400000 USD、非保证金购买力100000 USD，无持仓、开放或历史订单。10只候选均为active/tradable的us_equity，不能仅据此认定普通股类别；每只取得2025-01-02至2026-09-18的429根SIP原始日线，共4290条。

市场关闭，Alpaca时钟显示下一常规开盘为2026-09-21 09:30美东（13:30 UTC、香港21:30）。随后同一授权范围查询最新报价：IEX成功返回10只，SIP最新报价返回HTTP403；这不影响已成功取得的SIP历史日线，但不得声称拥有实时SIP权限。市场关闭时的最新报价也不能作为下一交易日新鲜报价。尚未验证开盘时执行或订单成交。读取成功不代表每条数据的策略资格已经通过。

初始活动中有一条JNLC、100000 USD、executed、日期2026-09-21且无transaction_time。原始事实完整保留；它已包含在100000现金初态，不自行杜撰发生秒数、重复记账或把它当收益。新适配器将初态已有活动按ID/内容哈希固定，后续变化或新增非FILL仍阻断。

## 数据缺口与最小补充选择

没有现成补充资料。原策略要求股息再投资总回报价格，不能把任意复权价、供应商请求成功或补充文件自述直接当成符合要求。

1. **尽量复用Alpaca与公开原始公告。** [官方数据产品](https://alpaca.markets/data)列出公司行动；[公司行动文档](https://docs.alpaca.markets/us/docs/corporate-actions)说明供应商处理与时间限制。需要实际确认账户权限、分页、处理日期与除权日期、现金分红/拆股完整性，再与发行人或SEC公告核对，按明确再投资方法重建总回报。分拆、合并、资本返还等复杂行动必须有正确处理或阻断，不能遗漏。该重建器本轮尚未实现，完整性未验收；不能由下载一次行动列表证明历史齐全。
2. **行业及普通股身份。** [Nasdaq市场资料](https://www.nasdaq.com/market-activity)可进一步核实其供应商行业体系和[筛选器](https://www.nasdaq.com/market-activity/stocks/screener)输出；本次未取得可用机器数据。可记录当前已发布分类及实际采集时间，不映射成不存在的历史GICS。[SEC公开API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)可辅助核实发行人/普通股身份，SIC不能由AI自行变成GICS。
3. **额外供应商。** [Alpha Vantage文档](https://www.alphavantage.co/documentation/)将日线调整接口标为Premium，且未在本次证据中证明其调整价等同所需再投资方法。因此未申请账户、购买或用它宣称免费完成。

实际最小补充选择仍需有据可查的资料。当前停在真实原始数据取得后、合格策略输入之前；不应下单验证一只股票来替代策略验收。市场关闭之外，数据资格是独立阻塞。

## 自动与独立复核

原离线check/demo/validate/replay均已完成，结果见下表。独立作者已复核F01/F02六文件、数据/契约、SDK及网络期间事件恢复；发现的初态历史订单、恢复换手、变价订单身份、金额有限值和修复后只读恢复问题均已反馈修正。作者自检不计独立复核，完整记录位于本轮证据目录。

局部证据包括risk-tests-final.log（33项）、recovery-time-regression-before/after.log（修复前失败、修复后21项）、独立数据/适配器/配置检查。旧审计页保持历史正文，本页仅关闭具体已验证缺陷，不宣称B02/B04/B05或持续Paper全部关闭。

### 最终实际结果

证据根目录为 `artifacts/alpaca-paper-20260919/`，全部产物追加、不覆盖前轮。uv前缀为 `UV_CACHE_DIR=/private/tmp/quant-qc014-uv-cache uv run --offline --locked quant-core`；Conda通过既有Miniforge的 `conda run --prefix <本轮根目录>/conda-env quant-core` 执行。后者从前轮专用环境克隆到新目录，旧环境保留；再按requirements哈希清单安装、可编辑安装更新项目元数据，pip check通过，不安装到base。

| 命令/检查 | uv | Conda |
|---|---|---|
| `check --base f48cd4fc8dfc15d55c45655c08a4a957771694c2` | 退出0；230 passed、21 warnings、54.69秒 | 退出0；230 passed、21 warnings、64.84秒 |
| `demo --config configs/demo.toml --output <新目录>` | 退出0，uv-demo | 退出0，conda-demo |
| `validate --run-dir <演示目录>` | 退出0 | 退出0 |
| `replay --run-dir <演示目录> --output <新目录>` | 退出0，uv-replay | 退出0，conda-replay |
| account/orders/factors/signals/target回放比较 | 五项true | 五项true |

10份业务JSON跨环境精确一致，两套关键依赖版本一致；Python构建编译器等环境差异保留在final-validation.json，不冒称整个环境字节相同。21条第三方警告包含原exchange_calendars/NumPy弃用及SDK所用websockets.legacy弃用；PyArrow沙箱CPU探测权限诊断保留，未屏蔽、未为通过升级旧依赖。

最终 `uv sync --offline --locked --check` 无变更、Schema/六正反样例只读检查与 `git diff --check` 通过；治理零错误、Ruff/格式/mypy均通过。首次完整检查后源码和测试未再修改，源码指纹为 `acd686bd845d8496827dc62d7a82d75316cefb8f408c610532438fd19e59c335`，dirty=true；不包含秘密的代码与运行证据均未提交。最终readonly-02于15:54 UTC再次验证初态稳定性，同一源码指纹、无订单，仍明确缺补充资料。

独立复核记录为 independent-risk-review.md、independent-paper-review-risk-agent.md、independent-paper-application-review.md。后两者分别由非作者检查相关实现，发现的部分成交取消后同计划重发、ADV未来窗口及最后核对失败退出状态已修复并独立复跑。SDK/应用/配置集合独立52项通过，初态活动仅按原哈希排除，不用现金差异重写基线。

本轮秘密扫描在跟踪/新增仓库文件及新运行日志、真实只读JSON中未发现用户Key/Secret。118个相对文档链接中6个旧PROJECT_STATE链接指向早期未随仓库保留的artifacts，均已存在于本轮基线；本轮新增链接可定位，不改写旧历史来冒称产物存在。原始429个交易日与实际下载日历逐项匹配，但这仍不证明总回报资格。无实盘、无持续部署、无购买或提交/推送。

### 后续只读补充及展示（香港时间2026-09-20）

用户补充要求可视化窗口，继续保留数据→原策略→获授权小额Paper→成交/核对的顺序。`viewer/`与只读标准库工具独立于业务层，没有账户SDK、凭据读取或交易控制；界面按保存证据显示真实Paper/离线模式、远端现金与策略预算、原始行情、评分/目标/订单及阶段缺口。无成交证据不会绘制假收益或已完成状态。

进一步获授权只读核实公司行动Market Data v1：完整分页取得96条cash_dividends、3条cash_mergers、1条forward_splits、1条name_changes、1条stock_mergers（查询处理日期2024-01-01至2026-09-19）；含收购方关联记录，不能全部当作候选股自身权益变动。XOM同代码CUSIP从30231G102变为30233Q108，处理日2026-07-02，需要核实稳定身份与价格衔接；不擅自将这种事件忽略。原始响应见corporate-actions-probe，权限通过仍不等于行动完整性或总回报方法验收。

实际读取10个Nasdaq官方摘要页，标题均明确Common Stock（GOOGL/META为Class A），证据保存于public-security-evidence.json；尚需与Alpaca稳定ID绑定。行业字段均没有可采纳结果：页面Key Data为空，公开summary请求先DNS失败、HTTP/2传输错误，再HTTP/1.1超时；没有据此声称必须收费或登录，也没有凭记忆填写行业。严格总回报与行业仍为独立阻塞。

上节230项双环境及源码指纹对应展示补充前的核心接入验收；展示工具加入tools源码指纹范围，后续新增检查结果另记，不能把旧记录改成已验证新工具。

### 展示补充后的最终验证

uv与新Conda环境再次实际执行 `check --base HEAD`，均退出0，各 **254 passed、21 warnings**，分别55.09秒、57.12秒；包含24项独立展示测试。Ruff、格式、mypy、治理与Schema检查通过。两套新的 `demo-viewer` / `validate` / `replay-viewer` 均退出0，五项回放全true，10份业务JSON跨环境一致。完整日志以 `-viewer.log` 结尾，汇总为viewer-validation.json。原第三方与沙箱CPU诊断保留。

当前Python/配置源码指纹为 `26ee8584a0c4aad8436faa0f3405d871605682ee1c4dec193966a8ab57fd7ea2`，dirty=true；静态页面三个文件另记SHA256。readonly-02仍保留真实采集时的原指纹，不重写旧证据来追认新工具。再次扫描仓库文件及本轮日志/JSON/报告，没有发现用户Key/Secret。

展示独立复核见independent-viewer-review.md，覆盖阶段、数字观察排序、采集时间、资金口径、固定文件与软链、Host及无写接口。浏览器已验证真实readonly-02页面、概览导航、刷新和窄屏时间显示；显示策略预算10000、远端现金100000、4290条行情、零订单及闭环未完成。最初沙箱不允许绑定本地端口，获权限后只绑定127.0.0.1:8765成功；仅保留本地只读预览，没有部署交易服务或安排后续任务。

本次停止点仍为合格行业、严格总回报和证券身份衔接；没有执行真实因子/目标、Paper下单、成交或成交后核对。后续先补齐数据资格并运行原策略，再取得具体订单边界及未成交处理确认；不能用独立脱敏测试或展示窗口替代这一验收。
