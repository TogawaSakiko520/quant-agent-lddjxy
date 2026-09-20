# 命令与运行产物参考

在仓库根目录执行。首次使用按[快速开始](runbooks/quickstart.md)，真实模拟账户按[Paper 手册](runbooks/alpaca-paper.md)；本页用于查参数、产物和退出码，不代替账户或发单授权。`DIR`、`NEW_DIR`、`FILE`、`REV` 和尖括号内容均须替换为实际路径或值；新输出不得覆盖旧记录。

## 离线命令


下表中的参数接在 `uv run --offline --locked quant-core` 或 `conda run -n quant-core-dev quant-core` 后。相对路径仍以仓库根目录为起点。

| 命令 | 必需参数 | 可选参数 |
|---|---|---|
| `demo` | `--output <新目录>` | `--config <TOML文件>` |
| `validate` | `--run-dir <已有运行目录>` | 无 |
| `replay` | `--run-dir <已有运行目录>`、`--output <新目录>` | 无 |
| `research` | `--run-dir <已有运行目录>` | 无 |
| `report` | `--run-dir <已有运行目录>` | 无 |
| `check` | 无 | `--base <Git提交或引用>` |


`validate`、`replay`、`research`、`report` 只适用于离线 demo 清单，不能用于 Paper 计划。`check` 需要源码仓库与开发依赖；完整检查流程见[维护手册](runbooks/ai-maintenance.md#自动检查与边界)。

## Paper 命令

以下参数接在相同环境前缀后；只有 `paper-plan` 不访问远端。

| 命令 | 必需参数 | 可选参数 |
|---|---|---|
| `paper-read` | `--config FILE --credentials FILE --output NEW_DIR` | 无 |
| `paper-plan` | `--source DIR --output NEW_DIR` | 原策略必需 `--supplement FILE`；MA 必需 `--identity-evidence FILE`。 |
| `paper-execute` | `--run-dir DIR --credentials FILE --approve-plan PLAN_ID --max-orders N --max-order-notional USD --unfilled keep或cancel` | `--observe-seconds 0至300`，默认 300；单独授权的 `--queue-cancel-test`。 |
| `paper-recover` | 同 `paper-execute` 的必需参数 | 接受 `--observe-seconds`，但不运行普通观察窗口；固定旧版维护可用 `--confirm-unsent-source DIR`。 |

`keep或cancel` 和 `0至300` 是取值说明，输入时分别选 `keep` / `cancel` 或一个整数。MA 订单最多 3 笔、每笔含费用预留最多 500 USD；休市测试只能 1 笔且必须 `cancel`。正常恢复与队列恢复等待边界、授权和失败处理以[手册](runbooks/alpaca-paper.md)为准。

## 展示工具

```bash
# uv
uv run --offline --locked python -m tools.paper_viewer --run-dir artifacts/demo --port 8765
# Conda
conda run --no-capture-output -n quant-core-dev python -m tools.paper_viewer --run-dir artifacts/demo --port 8765
```

`--run-dir` 必填且为已有目录；`--port` 默认 8765。用浏览器打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)，刷新只重读本地保存文件。Paper 应传只读目录或完整计划根目录，工具会选最新观察，不访问账户。备份与契约导出工具见[备份手册](runbooks/backups-migrations.md)。

## 研究与报告


分析因子与后续收益的样本关系：

```bash
uv run --offline --locked quant-core research --run-dir artifacts/demo
```

Conda路线将前缀替换为 `conda run -n quant-core-dev quant-core`，后面的research参数不变。

结果追加到 `artifacts/demo/research/experiment-0001` 等递增编号目录，不覆盖原交易文件。研究包含因子覆盖率、排名与后续收益的相关性等统计；它不是扣除成本后的完整策略业绩，也不提供完整交易净值曲线。研究口径和时间隔离方式见[因子研究说明](research.md)。

从已保存记录重新生成文字报告：

```bash
uv run --offline --locked quant-core report --run-dir artifacts/demo
```

Conda路线同样替换前缀，后面的report参数不变。

`report` 先校验运行，再将 Markdown 输出到终端的标准输出，不覆盖原 `report.md`。校验阶段同样会使用临时数据库，不修改源运行文件。


## 离线运行产物


以下路径都相对于本次演示输出目录。**保留整个目录**，只复制 `report.md` 无法校验或回放交易过程。

| 文件 | 保存的内容 |
|---|---|
| `report.md`、`alerts.jsonl` | 可读报告，以及逐条保存的结构化告警。告警文件本身不发送外部通知。 |
| `manifest.json` | 运行身份、代码和依赖信息、配置、初始账户、输入版本及文件摘要；用于校验和回放。 |
| `inputs/market.parquet`、`inputs/securities.json` | 完整合成行情，以及证券身份、行业和交易资格等主表信息。 |
| `inputs/execution_quotes.json` | 独立生成的模拟执行报价，供执行阶段使用。 |
| `snapshot.json` | 决策时刻已知的行情和证券信息。 |
| `factors.json`、`signals.json` | 原始因子值、评分、排序和被排除的原因。 |
| `regime.json`、`target.json`、`risk.json` | 市场状态观察、希望达到的持仓、逐单风控判断。市场状态当前只观察，不调整预算。 |
| `orders.json`、`events.json` | 订单状态，以及实际收到的状态与新增成交事件。 |
| `journal.json` | 按写入先后保存的操作日志，回放据此还原处理顺序。 |
| `account.json`、`reconciliation.json` | 最终账户，以及内部账本和 FakeBroker 记录的核对结果。 |
| `internal.sqlite`、`broker.sqlite` | 内部系统和 FakeBroker 各自维护的数据库。 |

回放目录另有 `replay-verification.json`。可选研究的每个实验目录保存 `request.json`、`factors.json`、`observations.json`、`folds.json`、`summary.json` 和 `artifacts.json`；计算阶段失败时会尝试保存 `failure.json`。这些是本地生成路径，不保证在代码托管网站上存在。


## Paper 运行产物

| 阶段/目录 | 主要文件及用途 |
|---|---|
| 只读采集目录 | `config.json`、`read-result.json`、`account-observation.json`、`assets.json`、`calendar.json`、`clock.json`、`bars.json`；MA 另有 `split-bars.json`、`corporate-actions.json`，成功行情采集保存 `data-requests.json`。 |
| 计划根目录 | 复制使用的只读证据及补充资料，保存 `data-qualification.json`、`snapshot.json`、`factors.json`、`signals.json`、`target.json`、`initial.json`、`regime.json`、`paper-plan.json`、`report.md`。 |
| 执行后的计划根目录 | `internal.sqlite` 持久化订单与账务；每次确认边界保存在对应观察目录，不能自行证明授权者身份。 |
| `observations/0001` 等 | 每次执行/恢复追加的 `authorization-boundaries.json`、`remote.json`、`account.json`、`orders.json`、`events.json`、`journal.json`、`risk.json`、`reconciliation.json`、`result.json`、`report.md`。失败可能只留下部分文件。 |
| 休市测试的观察目录 | `queue-context.json`、`queue-submission.json`、`queue-query.json`、`queue-before-cancel.json` 和 `queue-clock-checks.json` 保存测试边界与远端事实；计划根另有 `queue-test.json` 绑定身份。 |

只读成功并不自动证明资料合格；计划成功不表示订单已发送；远端订单 ID 不等于成交；撤单以已确认终态为准。`result.json` 将提交、成交、待处理、对账及两种恢复验收分别保存。完整保留计划目录和每次观察，不编辑事实来消除差异。

## 退出码

| 退出码 | 含义 | 下一步 |
|---|---|---|
| `0` | 对应命令处理成功 | 仍读阶段结果；Paper 只读可能有 blockers，空目标也不表示成交。 |
| `1` | 开发检查失败或未分类运行异常 | 保留完整输出和部分产物，定位具体失败项。 |
| `2` | 参数、输入约束或路径错误 | 核对终端原因，已有输出换新名称。 |
| `3` | 风控、对账或远端观察阻断 | 停止新增风险，按[故障恢复](runbooks/recovery.md)核对；不盲目重发。 |

若项目命令尚未启动，错误来自 uv、Conda 或 pip，按[环境排障](runbooks/ai-maintenance.md#工具准备与环境排障)处理。
