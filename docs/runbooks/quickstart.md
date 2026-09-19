# 在本地运行演示

本手册说明如何生成一次模拟交易结果、查看报告，再检查和回放保存的记录。程序使用自己生成的行情和本地模拟券商 FakeBroker，不需要券商账户或密钥；当前没有接入 Alpaca，包括 Alpaca Paper 模拟账户。

## 运行前提

准备好仓库源码和 Python 3.12；uv 的检查与安装见下一节。下面项目命令都在**仓库根目录**执行，也就是包含 `pyproject.toml` 和 `uv.lock` 的目录。

macOS 是主要本地开发环境，具体系统、架构、解释器和验证边界见 [macOS 验证记录](../audit/macos-development.md)。Linux CI 定义继续保留；本轮没有重新执行 Linux 或远端 CI。当前账户锁使用 `fcntl.flock` 文件锁，协调同一台机器上、同一账户且遵守该锁机制的程序，避免同时执行；它不加密账户，也不能协调另一台机器或控制券商端人工操作。

本例使用 `artifacts/demo` 和 `artifacts/replay` 作为输出目录，二者必须尚不存在。如果已经有同名目录，分别换成新的名称，例如 `artifacts/demo-02`、`artifacts/replay-02`，并在后面的命令和报告路径中保持一致。保留旧目录，不覆盖原运行记录。

## 1. 确认 uv 并安装锁定的依赖

先运行 `command -v uv` 和 `uv --version`。若命令不可用，先检查 `~/.local/bin/uv` 等已有安装位置；只是没有加入 PATH 时，不需要重复安装。PATH 是终端查找命令的目录列表。

确实没有安装时，可按官方指定版本方式安装 **uv 0.12.5**，与仓库 CI 使用的版本一致。以下步骤会联网，只适用于 `~/.local/bin/uv` 和 `~/.local/bin/uvx` 均不存在（也没有同名符号链接）的情况；已有其他版本不自动覆盖或升级。

```bash
uv_installer=$(mktemp)
curl -LsSf https://astral.sh/uv/0.12.5/install.sh -o "$uv_installer" &&
  env UV_NO_MODIFY_PATH=1 UV_INSTALL_DIR="$HOME/.local/bin" sh "$uv_installer"
```

安装器写入用户目录，不需要 sudo，也不安装系统 Python。`UV_NO_MODIFY_PATH=1` 禁止它修改 shell 启动配置，详见 [uv 官方安装选项](https://docs.astral.sh/uv/reference/installer/)。安装失败时保留输出，不继续假定 uv 已可用。

使用上述用户目录安装时，在**当前终端**启用并核对：

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v uv
uv --version
```

这不会写入 `.zshrc` 等配置文件；新开终端后需要重新设置，或直接使用 `~/.local/bin/uv` 代替下文的 `uv`。不要将 `source ~/.local/bin/env` 作为必需步骤：本安装方式没有要求生成该脚本。

经常在macOS的zsh终端开发时，也可以把下面这一段加入自己的 `~/.zshrc`，让之后的新交互式终端自动找到uv。先检查已有内容，只添加一次，不覆盖其他配置；判断条件避免重复添加目录。这是用户主动配置PATH的步骤，与安装器自动修改shell配置分开。

```bash
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
  export PATH="$HOME/.local/bin:$PATH"
fi
```

保存后新开终端，或在已有终端执行 `source ~/.zshrc`，再运行 `command -v uv` 与 `uv --version` 确认。无需sudo；不会修改系统Python或项目依赖，但该目录中的命令会进入终端的查找范围。

确认已有 Python 3.12 后安装项目依赖，并禁止 uv 在缺少解释器时自动下载另一套 Python：

```bash
uv sync --locked --no-python-downloads
uv run --offline --locked python --version
uv sync --offline --locked --check
```

首次安装可能需要从包源下载依赖。若已有 Python 3.12 未被找到，可在同步命令追加 `--python /实际路径/python3.12`，不要修改系统 Python。核对版本应为 3.12.x，最后一条命令只检查环境是否与项目同步；未通过时先处理原因。安装完成后，下文命令使用 `--offline --locked`，按现有锁文件在本地环境运行；它不会在运行时下载缺失的依赖。

日常仍由 uv 管理本仓库的 `.venv`。根 [requirements.txt](../../requirements.txt) 是锁文件导出的运行与开发依赖清单，不包含本项目自身安装，也不是 conda 环境文件。导出和更新方法集中在[维护流程](ai-maintenance.md#依赖声明锁文件与导出清单)；conda 兼容尚未验证。

## 2. 运行一次演示

```bash
uv run --offline --locked quant-core demo --output artifacts/demo
```

这条命令生成合成历史行情，按两个选股指标（因子）评分：动量反映过去一段时间的涨跌表现，低波动描述价格变化的平稳程度。程序据此计算希望持有的股数，检查交易限制，再通过 FakeBroker 模拟下单、成交、记账和核对。

`demo` 只执行样本中最后一个合格的周调仓决策，没有把整段历史的每周交易全部跑一遍。它在同一次命令中使用模拟时钟进入下一交易时段，不需要等真实市场开盘。成功后，终端输出 `status: completed`、运行目录和报告路径。

不传 `--config` 时使用代码中的演示默认值；仓库的 `configs/demo.toml` 提供对应配置示例。参数含义见[演示配置说明](../configuration.md)。

## 3. 先看报告

用编辑器打开本地文件 `artifacts/demo/report.md`，可使用编辑器的 Markdown 预览。首次阅读重点看：

- **因子与评分**：每只股票按哪些数值排序。得分表示这批股票中的相对位置，不是收益预测。
- **目标与实际订单**：希望持有多少股、实际持有多少股，以及订单是否全部成交。
- **账务、对账与监控**：现金、费用，以及内部账本与 FakeBroker 的账户记录是否一致；比较两边记录是否一致就是“对账”。

报告来自本地模拟数据，不能用它判断策略是否能在真实市场赚钱。`artifacts/` 不随 Git 保存；新检出仓库后，需要先运行演示才会有这些文件。

## 4. 校验保存的运行

```bash
uv run --offline --locked quant-core validate --run-dir artifacts/demo
```

`validate` 核对文件摘要、原始输入、订单与账户记录，并重新计算因子和目标。文件摘要也称哈希，是由内容计算的指纹，用于发现内容变化，不是加密或数据来源可靠性的证明。成功时输出 `status: validated`。它会创建并清理临时数据库用于核对，但不修改 `artifacts/demo` 中的原文件。

## 5. 在新目录回放

```bash
uv run --offline --locked quant-core replay --run-dir artifacts/demo --output artifacts/replay
```

`replay` 先校验源运行，再从保存的初始账户和操作日志重建内部账本，在新目录保存回放结果。它不把原订单重新发送给券商。成功时输出 `status: replayed`。

打开 `artifacts/replay/replay-verification.json`，其中账户（account）、订单（orders）、因子（factors）、评分与排序（signals）、目标（target）五项应均为 `true`。这表示保存的输入和事件能够重建相同结果，不代表能再次取得真实市场的成交价。

## 可选：研究因子或重新输出报告

分析因子与后续收益的样本关系：

```bash
uv run --offline --locked quant-core research --run-dir artifacts/demo
```

结果追加到 `artifacts/demo/research/experiment-0001` 等递增编号目录，不覆盖原交易文件。研究包含因子覆盖率、排名与后续收益的相关性等统计；它不是扣除成本后的完整策略业绩，也不提供完整交易净值曲线。研究口径和时间隔离方式见[因子研究说明](../research.md)。

从已保存记录重新生成文字报告：

```bash
uv run --offline --locked quant-core report --run-dir artifacts/demo
```

`report` 先校验运行，再将 Markdown 输出到终端的标准输出，不覆盖原 `report.md`。校验阶段同样会使用临时数据库，不修改源运行文件。

## 运行目录里有什么

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

## 开发检查

修改代码或相关文档后，在仓库根目录运行：

```bash
uv run --offline --locked quant-core check
```

`check` 执行仓库治理检查、Ruff 规则与格式检查、mypy 类型检查和 pytest 测试。它不自动修复源码或升级依赖，但检查工具可能写缓存，测试会创建临时文件和数据库；它不是“完全不写文件”的命令。此入口需要源码仓库及开发依赖，不能只用安装后的业务包替代。

需要查看相对某次提交的敏感变更提示时，使用 `check --base <已有的Git提交或引用>`。该提示供人工复核，不等于自动批准修改。维护规则见[维护流程](ai-maintenance.md)。

## 六个命令与参数

下表中的参数接在 `uv run --offline --locked quant-core` 后。相对路径仍以仓库根目录为起点。

| 命令 | 必需参数 | 可选参数 |
|---|---|---|
| `demo` | `--output <新目录>` | `--config <TOML文件>` |
| `validate` | `--run-dir <已有运行目录>` | 无 |
| `replay` | `--run-dir <已有运行目录>`、`--output <新目录>` | 无 |
| `research` | `--run-dir <已有运行目录>` | 无 |
| `report` | `--run-dir <已有运行目录>` | 无 |
| `check` | 无 | `--base <Git提交或引用>` |

## 失败时如何处理

以下是 `quant-core` 的退出码；如果命令尚未启动、错误来自 `uv`，先处理环境或依赖问题。

| 退出码 | 含义 | 处理方式 |
|---|---|---|
| `0` | 命令成功 | 根据当前命令查看报告、校验结果或检查输出。 |
| `1` | 开发检查失败，或未分类的运行故障 | 保留完整输出，定位具体检查项或异常。 |
| `2` | 参数或输入不符合数据格式/约束，或输出路径已存在 | 核对参数和路径；已有运行目录换新名称，不删除旧记录。 |
| `3` | 风险检查或对账阻断 | 查看原因与已有订单、账户记录，按[故障与恢复手册](recovery.md)核对。 |

依赖未装齐时，先在可访问包源的环境完成 `uv sync --locked`；不要通过升级依赖或改锁文件绕过错误。运行失败可能已经写下部分记录，应保留输出目录和错误信息。未知订单或对账差异不能靠删数据库、改风险参数或修改原文件消除。

需要复制或恢复记录时，使用[备份与恢复手册](backups-migrations.md)。平台验证和已知限制以[项目状态](../../PROJECT_STATE.md)为准；更多说明见[文档导航](../README.md)。
