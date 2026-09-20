# MA Paper 最小策略接入记录（2026-09-20）

> 历史记录：以下结论、路径及授权仅适用于文中日期和基线；后续实现与验证见[当前状态](../../PROJECT_STATE.md)。本地 `artifacts/` 证据未随 Git 分发，部分旧产物可能已不可用。

用户明确批准独立MA5/MA20策略、最多3笔每笔含费用500 USD、10000 USD预算和5分钟观察后撤余单方案；首次具体计划批准已记录于下文，尚未发单。本轮范围覆盖根规则前轮仅注释任务限制；其他风险、凭据与禁止实盘约束不变。最新补充授权为全部验收后提交并推送，当前真实成交未验收，尚未执行。

## 基线与实施

起始HEAD f48cd4fc8dfc15d55c45655c08a4a957771694c2，工作区包含前轮接入成果；139文件完整快照及哈希存于 `artifacts/ma-paper-20260920/baseline`，不用HEAD差异冒充本轮范围。首次快照脚本系统Python没有datetime.UTC，文件复制成功后用项目Python校验全部字节并完成清单，未覆盖前轮证据。

已新增独立MA数据/指标/评分分派、保守未知行业风险及最多3个可行目标；原策略保留总回报及行业门槛。真实读取分别保存raw/split日线、asof与公司行动分页；当前普通股来源显式绑定资产ID。版本化可空价格/行业、旧封印兼容及混合Parquet同步；新消息1.1，其他契约仍精确1.0。无新依赖或锁文件变更（相对本轮基线）。

## 独立复核

非作者分别审查契约/存储/数据、策略/风险、应用/展示，并写独立预期测试。发现并关闭旧版本错误接受None、混合Parquet丢列、Schema换行验证差异、公司行动范围未绑定候选、MA三元组数学矛盾、展示空因子误报合格。MA订单保留稳定身份并标记自身策略版本；恢复文案明确cancel仍有撤单副作用。记录位于上述证据根目录review-contract-data.md、review-strategy-risk.md、review-application.md。

## 来源与限制

官方[日线接口](https://docs.alpaca.markets/us/reference/stockbars)明确raw、split和asof；仅拆股价格不是总回报。官方[公司行动](https://docs.alpaca.markets/us/reference/corporateactions-1)按process_date筛选，存在处理延迟；显式data_quality=all与完整分页不保证现实世界行动绝对齐全。缺时间、未知类型或窗口内复杂身份变动导致排除。普通股公开来源说明不替代历史成员资格。

## 验证状态

证据均追加于 `artifacts/ma-paper-20260920`，运行目录被Git忽略，新检出需重新生成。

| 实际操作 | 结果与证据 |
|---|---|
| uv `quant-core check --base HEAD` | 最终退出0，369 passed/26 warnings/63.96秒，uv-check-02.log。 |
| 专用Conda环境同一check | 退出0，369 passed/26 warnings/66.91秒，conda-check-01.log。 |
| 两环境 `demo --config configs/demo.toml`、`validate`、`replay` | 全部退出0；uv-demo、conda-demo及对应replay目录，回放五项均true；demo-comparison.json记录10份业务JSON完全一致。 |
| uv与Conda `python -m tools.paper_viewer` | 分别在8765/8766实际启动，浏览器显示MA指标、5个评分、3个目标、0订单及核对未验证；刷新不改事实。8766测试服务已停止，8765保留本机展示。 |
| `paper-read --config artifacts/ma-paper-20260920/paper.toml --credentials .env --output artifacts/ma-paper-20260920/readonly-01` | 获授权真实只读退出0，使用固定Paper账户；未发单。 |
| `paper-plan --source artifacts/ma-paper-20260920/readonly-01 --identity-evidence artifacts/ma-paper-20260920/identity-evidence.json --output artifacts/ma-paper-20260920/plan-01` | 退出0，仅从已保存真实输入计算；报告与输入/结果哈希保留。 |

uv命令使用 `uv run --offline --locked`；Conda使用已有 `artifacts/alpaca-paper-20260919/conda-env` 专用环境，与同一requirements及源码一致。首轮完整检查366通过、3失败：新增兼容测试误把已导出的1.1示例当旧1.0固定样本；改用基线真实1.0字面样本保留原哈希与断言，第二轮通过。现有第三方弃用警告和沙箱CPU信息诊断未屏蔽。首次本机HTTP绑定因沙箱PermissionError失败，获准仅回环监听后成功；非自动审批拒绝。Conda服务通过Ctrl+C停止导致包装器KeyboardInterrupt退出，非页面运行失败。

## 真实输入与具体计划

本次资料采集于2026-09-19 17:10:54 UTC完成。指定账户ACTIVE/USD、现金100000、购买力400000、非保证金购买力100000，空仓无订单；策略10000与固定未分配90000分开。10只各56条SIP原始/仅拆股日线，200条记录进入8月21日至9月18日的20日快照。公司行动7条现金分红、1条XOM 7月2日身份变更；变更早于指标窗口，窗口内没有复杂身份变化。普通股公开资料与当前稳定ID绑定，不声称已恢复历史股票池。

MA正趋势按META、AAPL、GOOGL、XOM、PG排序，对应评分1、0.75、0.5、0.25、0。META一股665.75超过10000×4.5%=450美元基础额度，跳过后选3个可行目标；PG因已有3个可行目标未选。其余5只非正趋势排除。

| 目标 | MA5 / MA20 USD | 股数 | 最近原始收盘估值 USD |
|---|---|---:|---:|
| AAPL | 333.992 / 322.64 | 1 | 336.13 |
| GOOGL | 346.822 / 341.7865 | 1 | 349.54 |
| XOM | 164.906 / 162.6555 | 2 | 327.08 |

合计1012.75，目标现金8987.25（未扣实际执行差价/费用）；不是已发订单或成交金额。未知行业整体10.1275%，各单票低于5%；提交仍需新鲜报价与完整风控，不以收盘估值预先宣称发单风控通过。真实计划独立复核文件review-real-plan.md以Decimal直接从split-bars重算，不调用被测策略形成期望。

计划ID `ffb51588d7d7d10c0ab8f624eec833962af3b2e1f422aec0184a95587be4afdb`，执行窗口2026-09-21 13:30—20:00 UTC（香港21:30至次日04:00）。源码指纹 `cd54d5f418ff374e60c96a9fc9166e9ec5a8760e4bdbdb67adedd3597e25c9bf`、HEAD f48cd4fc8dfc15d55c45655c08a4a957771694c2、dirty=true；环境及锁摘要在read-result.json。

## 实际停止点

代码实现、离线检查、真实只读、MA数据资格、指标/评分/目标与展示已验证；**真实订单提交、成交、成交后账户核对及重启恢复均未验证**。当前休市，用户已明确批准上述具体计划（user-approval.json）；不提前排队下单或自动预约。下一次在合格时段恢复时重新只读检查账户、资产资格及报价，再按确认范围执行；账户或计划变化不得套用过期授权。原双因子的行业/严格总回报缺口及Paper独立回放CLI等限制仍保留。
