# quant-core：本地美股策略演示

quant-core 使用程序生成的合成行情，在本地演练选股、持仓规划、模拟订单、现金与持仓记账，以及中文报告生成。它用于理解和检查交易程序各步骤如何衔接，并保存输入与处理记录，便于核对结果。

当前阶段是离线工程验证。**尚未接入 Alpaca，也不需要券商账户或 API 密钥。** FakeBroker 是仓库内的本地模拟券商，不是 Alpaca 的外部模拟账户。

## 当前能力与关键限制

- **选股与模拟交易流程**：按动量（过去一段时间的涨跌表现）和低波动（价格变化的平稳程度）两个选股指标（因子）评分，生成希望达到的持仓，检查风险限制，再模拟订单和成交，记录现金、费用与股数。
- **一次演示**：`demo` 生成配置日期范围内的合成行情，只执行其中最后一次合格周调仓：每周最后一个交易日收盘后形成决策，模拟下一交易时段执行。同一次命令内完成，不等待真实市场开盘，也不是持续运行的服务。
- **核对与重现**：将内部账本与本地模拟券商各自记录的账户、订单比较，这称为“对账”；根据保存的输入和事件重新构建结果，称为“回放”。
- **因子研究**：统计选股指标与后续收益之间的样本关系。当前样本不是真实历史行情；研究统计和一次模拟成交都不能代替完整、扣除交易成本的策略回测，也不证明策略盈利。

当前仍有已知问题：行业分类的历史版本可能影响风险判断；监控可能漏报目标之外的实际持仓；数量仅支持整股，不支持不足一股的碎股交易。具体发现与限制见[上线阻塞记录](docs/audit/launch-blockers.md)，最新实现与验证范围见[项目状态](PROJECT_STATE.md)。

## 环境要求与首次运行

项目需要 **Python 3.12**（`>=3.12,<3.13`），可选择 uv 或 Conda 作为环境入口。uv 路线继续使用 `uv.lock`；Conda 创建独立 Python 环境，环境内的 pip 按同一锁文件导出的 `requirements.txt` 安装项目依赖。首次安装可能联网，演示自身使用本地合成数据，不访问外部行情或券商。

下面是 uv 首次运行摘要，当前使用 uv 0.12.5。缺少工具时见[uv 安装步骤](docs/runbooks/quickstart.md#1-确认-uv-并安装锁定的依赖)；使用 Conda 的开发者直接从[Conda 首次安装](docs/runbooks/quickstart.md#conda-首次安装与环境选择)开始，不需要先安装 uv。两条路线的实际环境与限制见[兼容验证记录](docs/audit/conda-compatibility.md)。

**macOS 是本仓库的主要本地开发环境。** 已在 macOS 26.5.1、Apple Silicon、Python 3.12.14 上完成独立虚拟环境重建与离线开发验收；具体证据和解释器来源见 [macOS 验证记录](docs/audit/macos-development.md)。Linux 的已有记录和 CI 定义继续保留，本轮未重新验证 Linux 或远端 CI；其他 macOS 版本、Intel Mac 和持续交易环境不在本次结论内。

当前依赖 POSIX 类系统提供的文件锁接口，实际使用 `fcntl.flock` 协调**同机、同账户**的执行操作。这不是账户加密，也不能控制其他机器或券商端人工操作。

以下命令均在**仓库根目录**运行：

```bash
uv sync --locked --no-python-downloads
uv run --offline --locked quant-core demo --output artifacts/demo
```

同步前须已有可用的 Python 3.12；`--no-python-downloads` 避免自动下载解释器，未找到时按快速开始指定已有解释器路径。`artifacts/demo` **必须尚不存在**。若已有同名目录，请换一个新名称，并让后续命令的 `--run-dir` 使用该名称；不要删除旧结果来重复运行。`--offline` 限制 uv 获取依赖时联网，不是操作系统级网络隔离；运行前仍需完成依赖安装。

默认演示使用30只合成股票、10万美元模拟现金，参数与 [configs/demo.toml](configs/demo.toml) 一致，仅用于演示。可用 `--config configs/demo.toml` 显式指定配置；完整步骤见[快速开始](docs/runbooks/quickstart.md)。

## 运行结果

成功后，先用编辑器打开 **`artifacts/demo/report.md`**。这份演示报告按选股指标、评分、目标股数、实际股数、订单和账目组织结果。

需要核对细节时，再看同目录的 `account.json`（现金、持仓和费用）、`orders.json`（委托及成交进度）和 `reconciliation.json`（两边记录是否一致及差异）。校验和回放命令检查保存的证据，完整文件清单见[快速开始的产物说明](docs/runbooks/quickstart.md#运行目录里有什么)。

这些文件运行后才会生成，`artifacts/` 被 Git 忽略，新检出或 GitHub 页面中不保证存在。保留整个运行目录，不能只保存报告。

## 六个常用命令

下表参数接在 `uv run --offline --locked quant-core` 后使用；Conda 路线改用 `conda run -n quant-core-dev quant-core`，子命令和参数相同。`DIR`、`NEW_DIR` 和 `FILE` 表示需要替换的实际路径，`REV` 表示 Git 比较基线。

| 子命令与参数 | 用途、主要输入输出 | 写入范围 |
|---|---|---|
| `demo --output NEW_DIR`；可选 `--config FILE` | 从配置生成合成数据，完成一次演示，输出报告和运行证据。 | 创建新运行目录及本地数据库。 |
| `validate --run-dir DIR` | 校验已有运行的文件内容、记录与重算结果，输出校验状态。 | 不改原产物；创建并清理临时数据库。 |
| `replay --run-dir DIR --output NEW_DIR` | 校验源运行后重建账户、订单及决策结果，输出一致性比较。 | 写新回放目录；不改源运行，不重新向券商提交订单。 |
| `research --run-dir DIR` | 从已有运行追加因子研究，输出样本与分组统计。 | 在该运行目录的 `research/` 下新增实验；不改旧交易文件。 |
| `report --run-dir DIR` | 校验后从已有记录重新生成报告文本，输出到终端。 | 不覆盖原报告；校验使用临时数据库。 |
| `check`；可选 `--base REV` | 检查源码规范、类型、测试及文档引用，输出诊断。 | 可写缓存和测试临时文件，不自动修复源码。 |

校验通过不等于收益或外部交易能力已验证；回放一致也不表示真实市场能再次以同价成交。

## 仓库结构

```text
src/quant_core/          选股、组合、风险、订单、账务和应用入口
  contracts.py          代码实际使用的数据模型与接口
  adapters/             日历、文件/数据库及本地模拟券商
configs/                演示参数
contracts/              从代码模型导出的 Schema 和消息示例
tests/                  业务、恢复、契约与治理测试
tools/                  开发检查、契约导出与数据库备份工具
docs/                   使用、设计、专题与审计文档
prompts/                接手、实现、评审等维护模板
.github/                CI 检查定义，不代表远端已经运行通过
artifacts/              运行生成的结果；不提交 Git
pyproject.toml          Python要求、依赖声明及命令入口
uv.lock                 锁定的依赖版本
requirements.txt        从锁文件导出的运行与开发依赖，不手工维护
```

“数据契约”指模块间约定的数据格式与接口；`src/quant_core/contracts.py` 是实际定义，根 `contracts/` 保存生成的 Schema（字段与约束描述）及示例，两者不应各自维护一套字段。详细说明见[共同数据契约](docs/contracts.md)。

## 开发检查

修改代码后，在源码仓库及已安装开发依赖的环境中运行统一检查：

```bash
uv run --offline --locked quant-core check
```

Conda 用户运行 `conda run -n quant-core-dev quant-core check`，仍执行同一套检查，不切换到仓库 `.venv`。

它汇总治理检查、Ruff格式与规则检查、mypy类型检查和pytest测试。进行差异复核时，可使用 `check --base REV` 指定实际基线；不指定时不会完成敏感差异审计。

首次体验与开发验收是不同步骤。开发完成后仍需按现有要求执行检查、在新目录演示与回放，并记录真实结果。参见[维护流程](docs/runbooks/ai-maintenance.md)和[根规则](AGENTS.md)，文档修改遵循[统一表达原则](docs/AGENTS.md#文档表达原则)。

`requirements.txt` 是共同锁文件导出的运行与开发依赖清单，不是另一套手写版本，也不是完整 Conda 环境文件。它不包含本项目自身安装；生成方法见[依赖维护说明](docs/runbooks/ai-maintenance.md#依赖声明锁文件与导出清单)。

## 文档导航

| 阅读目的 | 首选入口 |
|---|---|
| 运行项目 | [快速开始](docs/runbooks/quickstart.md)、[配置说明](docs/configuration.md) |
| 理解代码 | [业务代码导读](docs/code_walkthrough/business-chain.md)、[架构与职责](ARCHITECTURE.md) |
| 参与开发 | [维护流程](docs/runbooks/ai-maintenance.md)、[维护提示词](prompts/README.md) |
| 查看状态 | [项目状态](PROJECT_STATE.md)、[变更记录](CHANGELOG.md) |

完整的专题、需求、恢复和历史审计入口见[文档导航](docs/README.md)。

## 拉取代码后如何更新环境

以下命令在仓库根目录执行，用于把本地环境更新到仓库规定的依赖版本，不是主动升级到最新版本。仅改普通业务源码且依赖未变时，无需重新安装依赖。

**uv：一条命令同步项目环境。**

```bash
uv sync --locked
```

它安装所需包、调整版本并清理项目环境中不再需要的额外包；不会修改共同锁文件。Python 准备和禁止自动下载解释器的方式见快速开始。

**Conda：一条命令更新现有项目环境中的依赖。**

```bash
conda run -n quant-core-dev python -m pip install --require-hashes -r requirements.txt
```

`quant-core-dev` 是首次安装时创建的项目环境名；改过名称时替换它。已正确激活该环境后，可简写为 `python -m pip install --require-hashes -r requirements.txt`。命令会安装新增包或调整版本，满足要求的包通常保留，不必每次新建环境。哈希用于核对本次下载的包，不是重新检查全部已安装文件。

普通 pip 安装不会自动卸载清单外的旧包。清单删除依赖、Python 更换或 Conda 基础包调整时，按[维护流程](docs/runbooks/ai-maintenance.md#两条路线的更新与重建)创建新环境验证，旧环境保留。不要用 `conda update --all`、`conda install pandas` 或自由升级的 pip 命令替代共同清单。

**项目安装和开发检查是另外的步骤。** 首次安装或项目安装信息/命令入口变化时，Conda 用户还需运行 `conda run -n quant-core-dev python -m pip install --no-deps -e .`；这不是每次更新依赖都必须执行的命令。

更新后按开发流程验证，安装成功不等于测试通过：

```bash
# uv 路线
uv run --offline --locked quant-core check

# Conda 路线
conda run -n quant-core-dev python -m pip check
conda run -n quant-core-dev quant-core check
```

`pip check` 检查依赖冲突，不证明没有残留包。新增、删除或升级共同依赖必须更新声明、锁和导出清单，并经过两条路线验证；不能由两位开发者各自维护不同版本。
