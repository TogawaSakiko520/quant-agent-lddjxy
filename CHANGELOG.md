# 变更记录

本页按时间记录主要变化；当前能力以 [PROJECT_STATE](PROJECT_STATE.md) 为准。整理前各阶段的完整文字、命令、失败及验证数字见[原始记录归档](docs/audit/project-history-20260920.md#原-changelog-全文)。历史账户、发布授权和“未提交”等描述仅适用于当时任务。

## 文档按使用任务整理（2026-09-20）

README 聚焦入口，快速开始串联 uv / Conda 首次运行、报告与展示、校验和回放；Paper 手册明确两策略、授权、休市测试和恢复路径。完整参数/产物移至参考页，工具排障与依赖维护集中到维护手册，历史状态完整归档；长期规则移除过期任务范围，保持注释、测试、风险和权限约束。修正契约版本、账户 ID 和身份资料时序等过期或不清说明。

本次仅修改文档、提示词与需求文档路径；uv / Conda 各 439 项测试、demo / validate / replay、展示刷新、链接与历史完整性核验通过，独立复核通过。详情及未验证项见[整理验证记录](docs/audit/documentation-20260920.md)。不访问账户，不 commit / push。

## Alpaca Paper 真实提交撤单验收（2026-09-20）

新增受限休市订单操作测试，复用 MA 目标、共同风控和唯一执行服务；真实提交 AAPL 一股、查询远端身份、仅撤本单并独立重启核对，终态 CANCELED / 零成交。修复时区、微小时钟领先、明确 POST 前拒绝与订单短页读取；实际成交仍待验收。uv / Conda 各 439 项测试通过，原离线链路通过。见[审计](docs/audit/paper-queue.md)。相关代码已提交为 `85b9057`；这不是实盘部署。

## MA5/MA20 策略与展示（2026-09-20）

新增独立 MA、仅拆股价格输入、普通股身份绑定、未知行业保守计量及最多 3 个可行整股目标；保留原策略与风险参数。扩展契约兼容、执行观察和只读展示。该阶段两环境各 369 项通过，真实行情生成目标但未发单，见[MA 记录](docs/audit/ma-paper.md)。

## 本地只读展示窗口（2026-09-20）

新增独立静态页面和本地服务器，只读保存证据，无凭据或交易接口；分别显示只读、策略、订单、成交与核对状态。当时两环境各 254 项通过，见[接入记录](docs/audit/alpaca-paper.md)。

## Alpaca Paper 接入与真实只读（2026-09-19）

接入官方 alpaca-py 0.44.0，真实锁定并导出依赖；四个 Paper 命令沿用唯一执行服务，增加端点/账户校验、独立预算和真实 FILL 恢复。修复行业时点 F01、目标外持仓监控 F02 及网络期间成交时钟判断。该阶段两环境各 230 项通过，真实只读成功、原策略资料不足未发单。见[接入审计](docs/audit/alpaca-paper.md)。

## uv 与 Conda 共用依赖（2026-09-19）

增加 Conda 专用 Python 环境与共同 requirements 安装路线；保留 uv.lock 为唯一项目依赖锁。两环境各 133 项通过，demo / validate / replay 及业务结果比较通过；具体工具安装、失败与环境边界见[兼容记录](docs/audit/conda-compatibility.md)。

## macOS 开发环境与依赖清单（2026-09-19）

验证 macOS / Apple Silicon 锁定环境重建和离线开发，导出共同 requirements 清单，并按单独授权配置用户 PATH。133 项通过，研究、报告及重复结果也经核验；见[环境记录](docs/audit/macos-development.md)。

## 第一阶段文档整理（2026-09-19）

集中维护文档表达原则，整理首次运行和导航；源码与依赖未改，133 项及离线演示/回放通过。完整步骤和失败保留在[历史状态归档](docs/audit/project-history-20260920.md#当前阶段第一阶段文档整理完成2026-09-19)。

## QC-014 注释规范与可读性（2026-09-19）

先将逐句注释改为按业务语义分层解释，迁移对应机械检查与固定规则样例；再增强变量、调用和状态分支的局部说明，最后进行全仓核验。后两轮保持业务 AST 与治理规则不变；各阶段完整检查均为 133 项通过，失败和独立复核分别保留于[规范迁移](docs/audit/comment-semantics-migration.md)、[局部增强](docs/audit/local-context-readability.md)、[全仓核验](docs/audit/readability-compliance.md)。

## 上线准备审计（2026-09-13）

当时离线 117 项通过，独立探针发现 F01 / F02 缺陷并确认碎股需求缺口；没有改源码掩盖失败。形成[准备审计](docs/audit/readiness.md)、[历史阻塞](docs/audit/launch-blockers.md)、[后续 PR 建议](docs/audit/next-prs.md)和[Paper 路线 ADR](docs/adr/free-paper-route.md)。这些结论不覆盖后续接入及修复事实。

## 0.1.0 初始离线工程（2026-09-13）

建立共同契约、双因子业务链、FakeBroker、治理与六个离线命令、研究和 SQLite 备份恢复。首次完整检查曾因报告顺序失败，修复后保留原断言，最终 117 项通过；正式 demo / 回放、两库备份和研究完成。完整初始结论、失败与数字见[归档](docs/audit/project-history-20260920.md#原-changelog-全文)。
