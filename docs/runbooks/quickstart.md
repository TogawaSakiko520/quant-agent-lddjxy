# 无密钥快速开始

使用 Python 3.12、uv 和仓库锁文件：

```bash
uv sync --locked
uv run --offline --locked quant-core check
uv run --offline --locked quant-core demo --output artifacts/demo
uv run --offline --locked quant-core validate --run-dir artifacts/demo
uv run --offline --locked quant-core replay --run-dir artifacts/demo --output artifacts/replay
```

首次依赖安装需要可用包源。演示使用本地合成固定样本、注入时钟和 FakeBroker；不加载实盘凭证、不访问网络或券商。`check` 是开发仓库命令，聚合工程治理、Ruff 规则/格式只读检查、严格 mypy 和 pytest；安装的业务 wheel 不能代替源码仓库的治理文件。

## 产物与读取顺序

| 产物 | 用途 |
|---|---|
| `manifest.json` | 运行身份、版本、输入与环境清单，是重放入口。 |
| `inputs/market.parquet`、`inputs/securities.json`、`inputs/execution_quotes.json` | 保留研究原始输入、证券主表与独立执行事件，不用盘后信号倒填收盘成交。 |
| `snapshot.json`、`factors.json`、`signals.json` | 时点快照、原始因子值和标准化评分证据。 |
| `regime.json`、`target.json`、`risk.json` | 状态依据、约束后目标和风险判断。 |
| `orders.json`、`events.json`、`journal.json`、`account.json`、`reconciliation.json` | 订单、成交/状态事件、真实写入交错顺序、账户终态与对账证据。 |
| `alerts.jsonl`、`report.md` | 结构化告警与面向人的解释报告。 |
| `internal.sqlite`、`broker.sqlite` | 内部与 FakeBroker 持久化事实；不得随意删库来消除未知状态。 |

保留整个输出目录，不只复制 Markdown。回放读取已有输入、配置及事件/清单证据，在新目录生成结果；可复现不意味着能再次取得真实市场成交价。研究命令在独立子目录追加实验记录，不修改交易快照：

```bash
uv run --offline --locked quant-core research --run-dir artifacts/demo
uv run --offline --locked quant-core report --run-dir artifacts/demo
```

研究先过滤完成 253 价格预热且两因子均有效的共同样本，再建立时间窗口；全部原候选仍计入覆盖率分母。研究输出位于 `research/experiment-0001` 等递增目录，保存 request、factors、observations、folds、summary、artifacts；失败保存 failure。每次试验固定 `trial_budget=1`、`tuning=false`，不优化权重。报告命令只向标准输出重生解释，不覆盖原 report.md。

这些命令与产物已经由应用实现；具体成功运行记录以 PROJECT_STATE 为准。已有输出目录不可再次使用，请为新运行选择新名称。发生失败先保留完整错误与清单，不修改批准快照来“重跑成功”；换新输出目录并按 recovery.md 核对。状态未知或对账差异不能靠删除数据库解决。

CLI 退出码：成功为 0，运行故障为 1，输入/契约或已存在输出路径错误为 2，风险阻断为 3。非零结果应保留原因并按恢复手册处理，不能通过改变风险参数掩盖。
