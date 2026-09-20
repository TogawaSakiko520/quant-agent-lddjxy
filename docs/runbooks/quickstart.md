# 第一次运行：从安装到看到结果

本页带你完成一次离线演示：合成行情 → 动量/低波动评分 → 目标股数 → 模拟订单与账目 → 中文报告。无需账户或密钥，也不需要等待市场开盘。准备接入真实模拟账户时，另走 [Alpaca Paper 手册](alpaca-paper.md)。

## 1. 选择环境并安装

先取得仓库源码，在包含 `pyproject.toml` 的**仓库根目录**打开终端。要求 Python **3.12**（`>=3.12,<3.13`），主要本地验证平台为 macOS；当前文件锁依赖 POSIX 接口。选择下面一条路线，不混用两个环境。

首次安装可能联网。已有 uv/Python 或 Conda 即可继续；缺工具或终端找不到命令时，先看[工具准备与环境排障](ai-maintenance.md#工具准备与环境排障)。本例的 `artifacts/demo`、`artifacts/replay` 必须尚不存在；若已存在，换新名称并同步替换后续路径，保留旧记录。

### uv 路线

已有 uv 和 Python 3.12 时执行：

```bash
uv --version
uv sync --locked --no-python-downloads
uv run --offline --locked python --version
uv run --offline --locked quant-core --help
```

Python 应显示 `3.12.x`，帮助中应列出 `demo`、`validate`、`replay` 等命令。uv 在仓库 `.venv` 中按 `uv.lock` 安装项目和开发依赖。找不到已有解释器时，可给同步命令追加 `--python /实际路径/python3.12`；不要改系统 Python。后面的 `--offline` 限制 uv 下载依赖，不是操作系统级禁网开关。

### Conda 路线

已有 Conda 时，创建尚不存在的专用环境。`quant-core-dev` 是示例名称；已有同名环境时换一个名称，并替换本页后续命令。

```bash
conda --version
conda create -n quant-core-dev --override-channels -c conda-forge --no-default-packages python=3.12 pip
conda run -n quant-core-dev python -m pip install --require-hashes -r requirements.txt
conda run -n quant-core-dev python -m pip install --no-deps -e .
conda run -n quant-core-dev python -m pip check
conda run -n quant-core-dev python -c "import sys; print(sys.executable); print(sys.version)"
conda run -n quant-core-dev quant-core --help
```

应看到 `No broken requirements found`、专用环境中的 Python 3.12.x 和命令帮助。Conda 提供解释器；环境内 pip 按 **同一 `uv.lock` 导出的 `requirements.txt`** 安装项目依赖，再安装本仓库并注册命令。Conda 使用者运行项目无需安装 uv。

本页用 `conda run`，无需激活或执行 `conda init`。若终端已支持 `conda activate quant-core-dev`，激活后也可直接运行 `quant-core` 和 `python`。不要同时激活仓库 `.venv`，也不要在 Conda 路线上改用 `uv run`。安装失败时保留输出，按[环境排障](ai-maintenance.md#工具准备与环境排障)核对环境，不自由升级或改锁绕过错误。

## 2. 运行 demo

按所选路线执行其中一条：

```bash
# uv
uv run --offline --locked quant-core demo --output artifacts/demo

# Conda
conda run -n quant-core-dev quant-core demo --output artifacts/demo
```

成功时终端输出 `status: completed`、运行目录和报告路径。默认使用 30 只合成股票、100,000 USD 模拟现金，只执行样本内最后一次合格的周调仓；不是整段历史的连续回测。参数见[配置参考](../configuration.md)。

若提示输出目录已存在，换用新目录后再运行；其他失败按本页末尾处理，保留已经生成的记录。

## 3. 查看报告或展示窗口

用编辑器打开 **`artifacts/demo/report.md`**。报告列出因子数值与评分、目标和实际股数、订单状态、现金与核对结果；评分是候选间的相对排名，不是收益预测。`reconciliation.json` 的 `matched` 应为 `true`，表示内部账本与 FakeBroker 的记录一致。

也可在仓库根目录启动展示窗口，按所选环境执行其中一条：

```bash
# uv
uv run --offline --locked python -m tools.paper_viewer --run-dir artifacts/demo --port 8765

# Conda，无需激活；--no-capture-output 让启动信息直接显示
conda run --no-capture-output -n quant-core-dev python -m tools.paper_viewer --run-dir artifacts/demo --port 8765
```

浏览器打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)，应看到明确标注的**离线合成 / FakeBroker** 数据，以及因子、目标、订单和核对结果。离线行情没有 Paper 的 `bars.json`，页面可能没有该类原始行情走势图；完整离线结果以报告和保存文件为准。

窗口只读本地已保存的数据，刷新不会访问账户或执行交易。已有目录可以直接打开，无需重跑策略；全新检出不包含 `artifacts/`，需要先完成第 2 步。Ctrl+C 停止窗口，再继续下方验证；也可保留窗口并在另一个终端运行。端口占用时换 `--port 8766` 并打开对应地址，不终止未知进程。只读采集或 Paper 计划也能展示，见[Paper 查看结果](alpaca-paper.md#6-查看结果)。

## 4. 简单验证与回放

先校验刚才的运行，再在**新的**目录回放保存的事件。选择一组命令：

```bash
# uv
uv run --offline --locked quant-core validate --run-dir artifacts/demo
uv run --offline --locked quant-core replay --run-dir artifacts/demo --output artifacts/replay

# Conda
conda run -n quant-core-dev quant-core validate --run-dir artifacts/demo
conda run -n quant-core-dev quant-core replay --run-dir artifacts/demo --output artifacts/replay
```

分别应输出 `status: validated` 和 `status: replayed`。打开 `artifacts/replay/replay-verification.json`，`account`、`orders`、`factors`、`signals`、`target` 五项应均为 `true`。回放重建本地账户与决策，不重新向券商发订单，也不修改源运行。

保留整个运行目录，只复制报告无法完成这些核验。需要研究、完整产物清单或其他命令时，查[命令与产物参考](../commands.md)；开发改动的完整检查见[维护手册](ai-maintenance.md)。

## 失败时从哪里查

| 现象 | 下一步 |
|---|---|
| 找不到 uv、Conda、Python 或 quant-core | 看[工具与环境排障](ai-maintenance.md#工具准备与环境排障)，确认项目已安装、解释器来自所选环境。 |
| `input_error` 或退出码 2 | 看终端 `reason`；核对参数、输入和新输出路径，保留旧目录。 |
| 风险阻断或退出码 3 | 查看已有 `risk.json`、`reconciliation.json` 和报告，按[故障恢复](recovery.md)处理。 |
| 退出码 1 或其他异常 | 保留终端输出及部分产物，按错误来源核对环境或运行事实；完整[退出码参考](../commands.md#退出码)。 |
| 展示为空或打不开 | 确认目录已有数据、端口正确、服务仍在前台运行；只读采集尚无策略/订单时显示未完成是正常情况。 |

不要靠删除数据库、修改保存的事实或放松风险参数消除失败。已有订单状态未知时先恢复核对，不盲目重发。
