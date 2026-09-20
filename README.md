# quant-core：可追溯的美股策略工程

quant-core 将行情转换为选股指标、评分和目标持仓，再经过风控、订单执行、账务核对，生成可解释的中文报告。它适合学习交易程序的完整流程、验证策略与执行接口，以及在明确授权下进行小范围 Alpaca Paper 模拟交易。

默认 `demo` 使用合成行情与本地 FakeBroker，无需密钥或账户。独立 Paper 入口使用真实行情和 Alpaca 模拟账户；**已验证真实只读、MA 策略计划、受限订单提交、查询、撤单及重启核对，实际成交和成交入账仍待验收**。原动量/低波动策略保留，其真实运行仍缺合格行业与总回报资料。当前没有实盘入口，也不运行持续交易服务；完整范围见[项目状态](PROJECT_STATE.md)。

## 选择入口

| 你想做什么 | 从这里开始 |
|---|---|
| 第一次运行，无需账户 | [快速开始](docs/runbooks/quickstart.md)：安装 → demo → 报告/展示 → 校验。 |
| 使用真实行情和模拟账户 | [Alpaca Paper 手册](docs/runbooks/alpaca-paper.md)：配置 → 只读 → MA 或原双因子计划 → 授权执行与恢复。 |
| 查看已有运行结果 | 使用下方展示命令；已有数据无需重新运行策略或等待交易完成。 |

## 安装与离线体验

在**仓库根目录**选择一条路线。要求 Python **3.12**；主要本地验证平台为 macOS，文件锁依赖 POSIX 接口。首次安装可能联网，以下 demo 本身只使用本地数据。工具准备及失败处理见[快速开始](docs/runbooks/quickstart.md)。

**uv**：已有 uv 和 Python 3.12 时：

```bash
uv sync --locked --no-python-downloads
uv run --offline --locked quant-core demo --output artifacts/demo
```

**Conda**：已有 Conda 时，在新的项目环境安装共同依赖和本项目，不需要 uv：

```bash
conda create -n quant-core-dev --override-channels -c conda-forge --no-default-packages python=3.12 pip
conda run -n quant-core-dev python -m pip install --require-hashes -r requirements.txt
conda run -n quant-core-dev python -m pip install --no-deps -e .
conda run -n quant-core-dev quant-core demo --output artifacts/demo
```

两条路线共用 `uv.lock` 锁定的项目依赖，`requirements.txt` 由该锁导出。环境名 `quant-core-dev` 和输出目录 `artifacts/demo` 都是示例；同名环境或输出已存在时换新名称，后续路径保持一致。不要覆盖旧运行记录。

成功时终端输出 `status: completed`。打开 **`artifacts/demo/report.md`**，查看因子、评分、目标、订单和账目；同目录的 `reconciliation.json` 保存账户核对结果。保留整个目录以便校验和回放。`artifacts/` 不随 Git 保存，新检出需要先运行 demo 或按 Paper 手册采集数据。

## 打开展示窗口

以下例子查看刚生成的离线 demo；查看 Paper 时，将 `artifacts/demo` 换为已有的只读采集目录或计划根目录。

```bash
# uv
uv run --offline --locked python -m tools.paper_viewer --run-dir artifacts/demo --port 8765

# Conda：激活已安装项目依赖的项目环境后
python -m tools.paper_viewer --run-dir artifacts/demo --port 8765
```

浏览器打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。目录须已存在；已有保存数据即可查看，刷新仅重读本地文件，不访问账户、不执行交易。窗口明确区分合成演示与 Paper 证据；Ctrl+C 停止展示。无需激活的 Conda 命令及更多说明见[快速开始](docs/runbooks/quickstart.md#3-查看报告或展示窗口)。

## 后续使用

- [快速开始](docs/runbooks/quickstart.md)：完成首次运行和简单验证。
- [文档导航](docs/README.md)：按目的查找策略、配置、契约、恢复和维护说明。
- [维护手册](docs/runbooks/ai-maintenance.md#两条路线的更新与重建)：拉取代码后的环境更新及开发检查。
- [项目状态](PROJECT_STATE.md)与[变更记录](CHANGELOG.md)：当前验证边界和历史变化。
