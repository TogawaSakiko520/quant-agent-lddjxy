# 按任务查找文档

## 开始使用

- [项目概览](../README.md)：判断项目用途，选择离线或 Paper 入口。
- [快速开始](runbooks/quickstart.md)：选择 uv / Conda，第一次运行 demo、看结果并校验。
- [命令与产物参考](commands.md)：查完整参数、文件位置、研究/报告命令和退出码。

## 查看结果

- [报告与展示窗口](runbooks/quickstart.md#3-查看报告或展示窗口)：已有数据直接查看，新检出先生成数据。
- [Paper 结果](runbooks/alpaca-paper.md#6-查看结果)：区分计划、远端订单、成交与核对。
- [备份与迁移](runbooks/backups-migrations.md)：停止写入后保留和恢复记录。

## 使用 Alpaca Paper

- [Paper 操作手册](runbooks/alpaca-paper.md)：配置与凭据、只读、计划、授权执行、恢复和休市提交撤单测试。
- [Paper 配置](configuration.md#独立-paper-配置)与[输入文件格式](contracts.md#paper-输入文件参考)：核对预算、策略选择、身份和总回报资料。
- [故障与恢复](runbooks/recovery.md)：处理未知订单、撤单竞态、账户差异及定向旧版维护。

## 理解策略与实现

- [股票量化交易数学模型入门](quantitative-trading-mathematical-model.md)：从数学角度理解数据、预测、决策、风险、订单与账户更新。
- [周频双因子策略](strategies/weekly-two-factor.md)、[动量](factors/momentum.md)、[低波动](factors/low-volatility.md)：原公式、评分与组合约束。
- [MA5/MA20 策略](strategies/ma-trend.md)：独立价格趋势、整股目标和未知行业处理。
- [配置参考](configuration.md)、[共同契约](contracts.md)：参数、身份、时间、金额和消息兼容。
- [架构](../ARCHITECTURE.md)、[业务代码导读](code_walkthrough/business-chain.md)：模块职责、对象与调用关系。
- [市场状态观察器](regime.md)、[因子研究](research.md)：观察规则与研究边界。market-intel 目前只有共同契约，尚无信息采集服务。

## 开发和维护

- [工程规则](../AGENTS.md)、[维护流程](runbooks/ai-maintenance.md)：中文注释、独立测试/复核、依赖锁定、环境更新及检查。
- [文档表达原则](AGENTS.md#文档表达原则)：内容分工与写作要求。
- [扩展手册](runbooks/extensions.md)、[维护提示词](../prompts/README.md)：新增因子/适配器、接手、实现、评审与故障处理。
- [行为规范](../SPEC.md)、[需求映射](requirements.json)：需求到实现、测试和文档的对应。
- [离线引擎 ADR](adr/0001-offline-engine.md)、[治理证据 ADR](adr/0002-governance-evidence.md)：设计取舍与检查边界。

## 当前状态与历史记录

**当前事实只看 [PROJECT_STATE](../PROJECT_STATE.md)**；[CHANGELOG](../CHANGELOG.md)按时间概括主要变化。[假设与阻塞](../ASSUMPTIONS.md)说明外部前提。

下面均为特定日期、基线和环境的记录，不延续当时账户或发布授权。`artifacts/` 原始证据不随 Git 分发，历史链接可能只在留存产物的本机可用。

| 主题 | 历史记录 |
|---|---|
| Paper 接入与验证 | [初始只读接入](audit/alpaca-paper.md)、[MA 计划](audit/ma-paper.md)、[真实提交撤单](audit/paper-queue.md) |
| 环境 | [macOS 验证](audit/macos-development.md)、[uv / Conda 验证](audit/conda-compatibility.md) |
| 原始审计及建议 | [准备审计](audit/readiness.md)、[当时阻塞](audit/launch-blockers.md)、[后续 PR 建议](audit/next-prs.md)、[免费 Paper 路线 ADR](adr/free-paper-route.md) |
| 注释规范演进 | [分层解释迁移](audit/comment-semantics-migration.md)、[局部上下文增强](audit/local-context-readability.md)、[全仓核验](audit/readability-compliance.md) |
| 文档与交接 | [本次整理验证](audit/documentation-20260920.md)、[整理前状态及变更全文](audit/project-history-20260920.md) |
