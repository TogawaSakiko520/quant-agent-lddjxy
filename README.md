# quant-core：可解释的离线美股多因子工程

这是一个模块化单体：自有 Python 核心负责数据、因子、组合、风控和解释，适配器负责日历、快照、事件持久化及 FakeBroker（仅按注入事件运行的离线模拟券商）。首轮已完成演示、校验、回放、共同样本研究及双库备份/恢复验收；完整测试和运行证据见 [PROJECT_STATE.md](PROJECT_STATE.md)。合成样本和 FakeBroker 不证明收益，也不是已验收的生产回测引擎。

## 安装和无密钥运行

当前验证环境为 Linux、Python 3.12 与 uv；账户文件锁依赖 POSIX 接口，其他平台未验证。首次安装依赖需要可用的包源；安装后固定样本演示不访问网络、不使用密钥。

```bash
uv sync --locked
uv run --offline --locked quant-core check
uv run --offline --locked quant-core demo --output artifacts/demo
```

当前工作区可直接查看 [正式解释报告](artifacts/demo/report.md) 和 [业务代码导读](docs/code_walkthrough/business-chain.md)；`artifacts/` 被 Git 忽略，新检出需先运行演示生成。

`check` 的最终检查范围、`demo` 的产物清单和回放命令由 [运行手册](docs/runbooks/quickstart.md) 说明；实际运行记录只写入 [PROJECT_STATE.md](PROJECT_STATE.md)。命令失败时先保留完整错误，再按 [故障与恢复手册](docs/runbooks/recovery.md) 处理，不得删测试或放松约束获得通过。

## 业务链路

固定原始样本 → 时点和质量检查 → 历史股票池 → 动量/低波动 → 同日百分位与等权评分 → 市场状态解释 → 目标组合 → 风控 → 持久化订单意图 → FakeBroker 事件 → 成交与现金账务 → 对账 → Markdown 解释报告。

每次决策按 `available_at <= decision_time` 取历史版本。信号在周末合格交易日收盘后形成，执行必须等待下一合格交易时段。解释沿真实结构化记录生成，不调用 LLM。

## 从哪里了解项目

| 问题 | 权威入口 |
|---|---|
| 需求和验收是什么 | [SPEC.md](SPEC.md)、[需求映射](docs/requirements.json) |
| 模块为何这样分工 | [ARCHITECTURE.md](ARCHITECTURE.md)、[架构决策](docs/adr/0001-offline-engine.md) |
| 哪些是假设或阻塞 | [ASSUMPTIONS.md](ASSUMPTIONS.md) |
| 实际做完并验证了什么 | [PROJECT_STATE.md](PROJECT_STATE.md)、[CHANGELOG.md](CHANGELOG.md) |
| 策略怎样形成信号与目标 | [周频双因子策略](docs/strategies/weekly-two-factor.md) |
| 因子公式如何手算 | [动量](docs/factors/momentum.md)、[低波动](docs/factors/low-volatility.md) |
| 如何读源码和共同契约 | [业务代码导读](docs/code_walkthrough/business-chain.md)、[数据契约](docs/contracts.md) |
| 人和 AI 如何维护 | [AGENTS.md](AGENTS.md)、[维护流程](docs/runbooks/ai-maintenance.md)、[提示词](prompts/README.md) |
| 研究怎样隔离未来信息 | [研究与验证](docs/research.md) |
| 市场状态是什么意思 | [观察器规则](docs/regime.md) |
| 参数是什么意思 | [演示配置](docs/configuration.md) |
| 如何运行、扩展与恢复 | [快速开始](docs/runbooks/quickstart.md)、[扩展](docs/runbooks/extensions.md)、[恢复](docs/runbooks/recovery.md)、[备份/迁移](docs/runbooks/backups-migrations.md) |

`market-intel` 是后续独立项目；本仓库只定义经版本化契约传入候选信息的边界。它不能调用本项目交易入口、写入已批准因子或覆盖历史快照。
