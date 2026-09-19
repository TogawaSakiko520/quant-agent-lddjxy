# QC-014 第二轮：局部上下文与调用衔接

日期：2026-09-19。本轮是第一轮分层解释规范之上的增强，不再迁移检查器，也不以减少注释为目标。唯一长期规范仍在 [根 AGENTS.md](../../AGENTS.md#按业务语义分层解释qc-014唯一主规范)，[第一轮迁移记录](comment-semantics-migration.md) 保持原字节。

## 起点、范围与本轮差异

编辑前保存当前工作区114个文件到 `artifacts/readability-context-20260919/baseline/`，`baseline-manifest.json` 记录每个文件的SHA-256、提交及当时源码指纹，`start-status.txt`、`start-diff.patch` 保留原未提交状态。起始源码指纹为 `bfe76e9a5ba554fd23577b917f1824aa81e9519512cb6b691aad42a9660549b2`。起始HEAD虽仍为 `01205ca`，但本轮只与上述工作区字节快照比较，不把上一轮改动计为新增。

主要需求 QC-014；QC-001 仅核验文档字符串对生成Schema的影响，QC-012/013 涉及新源码指纹和新运行证据，QC-015 涉及独立语义复核。业务行为、签名、数据结构、变量名、运算、配置、异常、测试输入及断言均不在修改范围；`tools/governance.py`、`tests/test_governance.py`、CI、依赖锁和配置保持起点字节。无新治理样例、密度/逐句/关键词门槛。

本轮在根规范增加变量来源/形状/单位、事实与目标/预算区别、调用输入/输出/去向、库用法的当前业务效果和出口后果；允许完整定义集中维护、关键位置简短重述。语法简单但引入新业务含义时仍需说明，宽泛阶段标题不能代替内部处理解释。同步 QC-014、提示词、工具子规则、维护手册、业务导读和需求路径；历史迁移/验收不改写。

## 代表阅读障碍与前后示例

先完成 application、portfolio、signals、execution/sqlite_store 及对应测试样板，再检查其他源码/测试/工具；已有足够说明的日历测试与包初始化文件保持不变。以下是实际说明摘录，不是另一份教学实现。

| 区域 | 本轮前的阅读缺口 | 本轮补充 |
|---|---|---|
| application.run_demo | 一句「计算因子与评分，再形成整股目标」覆盖三个不同对象 | 分别说明 factors 是原值/缺失原因、signals 是可比较评分/排序、target 是希望持仓；plan_orders 只返回候选，submit才持久化/提交，fill与recover分别更新券商/内账 |
| portfolio.plan_orders | `effective = dict(account.positions)` 缺少附近的业务口径 | 说明证券ID→实际股数+挂买余量−挂卖余量，只算目标缺口；本地副本不改账户。risk_quantities 则不减挂卖，保守计风险 |
| portfolio 金额 | nav、cash、reserved、allocated容易都被当作可花的钱 | 分别注明净值分母、可用现金剩余、必须保留旧仓市值、累积目标市值；换手是买卖金额均取正、不抵销 |
| signals 索引 | values/components 都是嵌套字典，含义不易辨认 | 两层键是证券ID/因子ID；前者原值，后者百分位。setdefault 合并同一证券的不同因子，eligible决定共同排名样本 |
| execution._recover | actual/expected 来源不明，容易误认为目标对实际 | 就地说明券商账户与内部事件账本账户；positions值是股数、cost_basis值是美元总成本，两者都不是目标 |
| sqlite_store._db | 「提交/回滚后关闭」未说明生成器上下文 | 说明 @contextmanager进入、yield交连接、正常return也提交、异常只回滚当前事务、最后关连接；账户锁可跨多个事务 |
| application.research_run | 复合索引/集合包含不易与共同样本对应 | `(证券ID,决策时刻)→有效因子ID集合`；空集合代表无有效因子，`<=`要求两因子齐全；fold索引指向observations，空窗口不等于成功策略 |
| 测试工厂与反例 | 读者不知道helper返回对象的角色及model_copy为何出现 | 补入独立给定的目标/事实、八条因子如何配对、4股已成与6股剩余、无重验复制如何把反例送到真正被测边界 |
| 工具 | before/copied、文件摘要与逻辑摘要容易混淆 | 说明它们是核验证据而非账户；SHA-256比字节，logical_sha256比表结构/内容，备份页布局可不同 |

具体变量样板在 `plan_orders` 中新增：

```python
# effective 是证券 ID→“实际股数 + 挂买余量 - 挂卖余量”，只用于计算目标缺口。
# dict 建立独立数量表，后续净额调整不会改写 account.positions 的成交事实。
effective = dict(account.positions)
```

与其并列的风险数量说明强调未成交卖单不能先释放额度，而不是再次翻译「复制字典」。在评分中，`values.setdefault(factor.security_id, {})[factor.factor_id] = factor.value` 的说明解释首次建立内层表、后续加入同一证券不同因子；数据结构和表达式未改。

调用样板把原来的宽泛阶段说明拆为当前用途：`plan_orders` 无文件/券商副作用，候选列表交 `service.submit`；后者返回订单状态而非成交；`broker.fill` 只改变FakeBroker自有账本，随后 `recover` 才消费事件更新内部账本。空候选列表表示当前不能再规划，不承诺目标完成。审计判定保存 `risk.json`，不误称当前文字报告读取该文件。

## 库行为与阅读复核

已对照锁定环境中的 Pydantic `BaseModel.model_copy`、`model_dump` 实现及 SQLite 连接上下文说明：model_copy默认浅复制且不重验更新字段；model_dump是序列化而非验证/写盘；model_validate从字典做模型校验；SQLite连接上下文正常提交、异常回滚，contextlib.closing只关闭连接。Decimal与排序/切片/集合的说明对照实际表达式和现有固定样本，未增加第二套测试实现或修改断言。

独立AI交叉复核按不同作者进行，复核记录含具名函数和读者问题，不用标签或关键词打分：

- `core-independent-review.json`：其他作者检查 application、CLI、备份/契约工具、application/contracts测试及长期规则；抽取run_demo对象链、重建账户、研究索引、CLI退出、SQLite closing等样本。
- `core-independent-review-by-execution.json`：其他作者检查核心模块及portfolio/risk/factors测试；抽取目标缺口与风险占用、净值/现金/换手、评分表、研究原索引与分组等样本。
- `execution-reader-independent-review.md`：其他作者检查execution/全部adapters；抽取恢复账户来源、UNKNOWN出口、改单余量、事务/锁、模型复制/重验、事件去重、日历与文件副作用等样本。
- 主代理另核对data/regime/research/ledger/monitoring/pipeline测试新增说明与实现，区分自然日标签夹具与交易日标签、事件状态与真实新增成交、未来扰动不变性与独立公式验算。

独立复核纠正了：空folds也可能因清除后开发分组为空；45股目标应由固定4.5%推导，不能说按候选数均分90%；合成输入的保存顺序；record_order也接收本地REJECTED/UNKNOWN；风险判定保存到审计文件而非当前文字报告。另收窄CLI解释器锁定责任、封印生成与验证的区别，以及告警严重级别不自动拦单。均只改说明，发现者回看问题关闭；作者自检另列，不称独立审查。

## 本轮验证与限制

实际验证位于新目录 `artifacts/readability-context-20260919/`；不覆盖第一轮快照、日志或运行结果。该目录被Git忽略，新检出不自动带入。本轮沿用 `.venv` 的 Python3.12.14 与已锁定依赖，不安装或升级包；下表 `uv` 的完整路径为 `/private/tmp/quant-qc014-tools/bin/uv`，执行时均带 `UV_CACHE_DIR=/private/tmp/quant-qc014-uv-cache`。

| 实际命令/证据 | 结果 |
|---|---|
| `.venv/bin/python artifacts/readability-context-20260919/verify_round.py` | 退出0；114文件基线摘要无损坏、无起始文件丢失；40个Python AST全一致，受保护路径字节全一致。`round-verification.json`记录逐文件结果，`round.diff`只包含本轮相对工作区快照的变化。 |
| `uv run --offline --locked quant-core check --base 01205ca` | 退出0，governance_errors=[]，Ruff/格式/mypy通过，**133 passed、20 warnings、52.58秒**，见 `check.log`。 |
| `uv run --offline --locked python -m tools.export_contracts --check` | 退出0，Schema与六个固定样例一致，见 `contracts-check.log`；未执行写入生成。 |
| `uv run --offline --locked quant-core demo --config configs/demo.toml --output artifacts/readability-context-20260919/demo` | 退出0，见 `demo.log`，生成新清单与报告。 |
| `uv run --offline --locked quant-core validate --run-dir artifacts/readability-context-20260919/demo` | 退出0，见 `validate.log`。 |
| `uv run --offline --locked quant-core replay --run-dir artifacts/readability-context-20260919/demo --output artifacts/readability-context-20260919/replay` | 退出0，见 `replay.log`；account/orders/factors/signals/target五项全部true。 |
| `git diff --check` | 退出0；本轮另有起始工作区差异，不把累计Git补丁冒称第二轮全部改动。 |

全量check的 `--base 01205ca` 只供既有敏感差异机制使用，其列表包含第一轮未提交成果；不以该列表证明第二轮逻辑不变。第二轮AST没有任何治理豁免，所有40个文件均纳入；比较保留普通字符串、签名、变量、控制流、数据、断言与类型注释，仅剥真正docstring及位置。作者最初一次比较把TypeIgnore.lineno当语义字段而报告日历差异，后在比较器归一化该位置、仍保留tag后通过，没有改动业务文件来消除误报。

显式检索确认业务没有 `__doc__`/`inspect.getdoc` 读取，治理以AST检查说明存在；Schema由 `storage.schema_documents` 读取模型类说明生成。导出契约模型类docstring与基线相同，生成文件字节及运行生成结果均不变，字段、类型、默认值与校验约束无需迁移。`library-inspection.json`记录已核对的锁定库版本、源码位置/摘要及SQLite内置上下文说明，这不是新增治理或业务测试。

`source-evidence.json` 核对新demo清单与当前 `code_evidence()` 完全一致：提交仍为 `01205cae9f3c396938bdcdbe6c6cd4af216f89cb`，dirty=true，新源码指纹 `fdf13598bd0c799221b85059c66f36e889f226101d0a57c9ff7d5b606018f518`，不同于本轮起始指纹。新目录使用相同固定决策输入，因此run_id可与上轮相同；这不代表沿用旧源码清单或产生新的真实市场成交。

本轮正式检查与离线命令没有失败，未重复跑上轮red/green迁移。20条第三方弃用警告未屏蔽，validate/replay出现macOS沙箱下PyArrow获取CPU缓存/NEON信息的sysctl权限诊断，仍退出0，日志保留。未单独执行research CLI或完整运行目录灾恢，原研究/恢复测试仍包含在133项中；远端CI、真实接口、持续交易和生产平台验收不在本轮验证范围。

已知F01/F02/F03及第一轮记录的序列化保护范围、市场状态修订选择边界继续保留，未修复或包装成正确保证。本轮阅读未据此新增业务验收结论；任何后续业务修复须单独任务。未连接真实账户、发送外部订单、发布、部署或修改远端权限。
