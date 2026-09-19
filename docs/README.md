# 文档导航

第一次了解项目，先读[仓库概览](../README.md)，再按[快速开始](runbooks/quickstart.md)运行本地演示。下面按阅读目的组织文档。

## 运行

| 想做什么 | 阅读入口 |
|---|---|
| 安装、跑一次演示、看报告、校验和回放 | [快速开始](runbooks/quickstart.md)：完整命令、生成文件和退出码。 |
| 看懂默认本金、持仓比例、费用等参数 | [演示配置](configuration.md)。 |
| 处理失败、未知订单或账户差异 | [故障与恢复](runbooks/recovery.md)。 |
| 保留数据库和运行记录、恢复到新目录 | [备份与迁移](runbooks/backups-migrations.md)。 |

## 理解项目

| 想了解什么 | 阅读入口 |
|---|---|
| 模块分别负责什么，为什么这样拆分 | [架构说明](../ARCHITECTURE.md)。 |
| 数据如何变成评分、目标、订单和账户记录 | [业务代码导读](code_walkthrough/business-chain.md)。 |
| 股票如何排名，希望持有哪些股票 | [周频双因子策略](strategies/weekly-two-factor.md)。 |
| 两个因子如何计算 | [动量因子](factors/momentum.md)、[低波动因子](factors/low-volatility.md)：公式、价格窗口和手算例子。 |
| “市场状态”是什么，是否改变交易 | [市场状态观察器](regime.md)。 |
| 因子与后续收益如何研究 | [研究说明](research.md)：样本划分、避免未来信息泄漏及统计结果的限制。 |
| 模块交换哪些数据，时间、身份和金额如何约定 | [共同数据契约](contracts.md)。其中的 market-intel 在本仓库只有候选信息的数据格式约定，尚未实现信息采集，也不是运行演示需要安装的组件。 |
| 为什么当前使用本地模拟券商 | [离线引擎决策](adr/0001-offline-engine.md)：FakeBroker 的用途与正式引擎尚未验收的边界。 |

## 开发与维护

| 想做什么 | 阅读入口 |
|---|---|
| 确认功能要求和验收行为 | [行为规范](../SPEC.md)、[需求与实现映射](requirements.json)。 |
| 修改源码、测试或说明 | [根工程规则](../AGENTS.md)、[维护流程](runbooks/ai-maintenance.md)。 |
| 新增因子、数据来源或适配器 | [扩展手册](runbooks/extensions.md)。 |
| 使用接手、实现、复核或故障处理提示词 | [维护提示词导航](../prompts/README.md)。 |
| 了解自动检查能证明什么 | [治理检查的能力边界](adr/0002-governance-evidence.md)。 |
| 编写或整理文档 | [文档表达原则](AGENTS.md#文档表达原则)；代码注释规范仍以根工程规则为准。 |

## 状态与历史

| 想确认什么 | 阅读入口 |
|---|---|
| 当前完成了什么、哪些命令实际验证过 | [项目状态](../PROJECT_STATE.md)。历史记录按当时的代码和环境解释。 |
| macOS 能否用于本地开发，具体测过哪些环境与命令 | [macOS 开发环境核验](audit/macos-development.md)。 |
| uv和Conda是否使用同一套项目依赖，哪些差异实际验证过 | [双入口兼容记录](audit/conda-compatibility.md)；日常命令见[README末尾](../README.md#拉取代码后如何更新环境)。 |
| 仓库有哪些变更 | [变更日志](../CHANGELOG.md)。 |
| 有哪些未解决假设或外部条件 | [假设与阻塞](../ASSUMPTIONS.md)。 |
| 接入外部模拟交易前还缺什么 | [上线阻塞清单](audit/launch-blockers.md)、[准备审计](audit/readiness.md)。 |
| 既有模拟交易路线讨论的依据 | [免费 Paper 路线记录](adr/free-paper-route.md)：保留当时的调查与决策背景，不表示已接入 Alpaca。 |
| 注释规范如何演进及如何核验 | [分层解释迁移](audit/comment-semantics-migration.md)、[局部上下文增强](audit/local-context-readability.md)、[全仓可读性核验](audit/readability-compliance.md)。 |

运行报告和数据库由程序写入本地 `artifacts/`，不随 Git 保存。生成与读取这些文件的方法集中在快速开始，仓库文档不依赖某次运行目录已经存在。
