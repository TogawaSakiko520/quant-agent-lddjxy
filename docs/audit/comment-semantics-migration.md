# QC-014：按业务语义分层解释的迁移记录

日期：2026-09-19。基线：`01205ca`；开工工作区干净。用户明确授权迁移注释规范、配套检查与规则测试，其他业务、风控、权限、依赖及工程约束不变。本记录不是新的注释标准；唯一长期规范在 [根 AGENTS.md](../../AGENTS.md#按业务语义分层解释qc-014唯一主规范)。目标读者懂基本 Python，但不熟悉本项目和交易业务。

## 需求与影响清单

主要需求为 QC-014；QC-015 涉及测试迁移依据及独立复核，QC-001 涉及 docstring 对 Schema 的潜在影响，QC-012/013 涉及源码哈希与新运行证据。其余需求关联的实现和业务测试只整理注释/docstring，公式、签名、配置、异常行为、输入和断言保持不变。

旧逐句要求的有效位置及迁移如下：

| 位置 | 原要求或检查 | 当前处理 |
|---|---|---|
| AGENTS.md、tools/AGENTS.md、prompts/AGENTS.md | 自有 Python 逐语句中文注释 | 根规则集中定义分层解释，子规则引用；docs/AGENTS.md 明确历史记录处理 |
| SPEC.md QC-014、docs/requirements.json | 逐句说明及实现/测试/文档映射 | 改为业务语义、存在性检查和独立语义复核，扩充关联路径 |
| implement.md、factor.md | 每条语句紧邻说明、公式逐句解释 | 引用根规范，强调阶段、前提、公式/索引和独立期望 |
| review.md 与其余维护模板 | 中文覆盖或未引用统一规范 | 引用同一规范，强调任务所需的语义复核，不复制标准 |
| ai-maintenance.md、ADR-0002、business-chain.md | 相邻说明覆盖每条语句 | 区分存在性和语义质量；正文保留关键解释，完整流程放文档 |
| docs/audit/next-prs.md | 历史审计中仍约束未来 PR 逐句说明 | 保留原段，新增日期注记说明 QC-014 已取代该项要求 |
| tools/governance.py | AST 语句邻接与 tokenize 分支注释检查 | 只保留真正中文 docstring 与函数类型；其他治理逻辑不变 |
| tests/test_governance.py | 专门验证旧邻接和分支规则 | 逐项迁移，补真正 docstring 与参数种类反例，保留无关测试 |

历史 CHANGELOG、旧审计结果及 PROJECT_STATE 的旧验收段落不改写为新规范验收。CI 入口、权限、依赖、锁文件、配置、业务版本与 Schema 约束均不因注释整理而变更。`src`、`tests`、`tools` 中的自有 Python 按文件逐段阅读；已适当简洁的包初始化说明保留。

## 先行样板与代表差异

先完成 `factors.py`、`execution.py`、`tests/test_factors.py`，再推广。以下片段只是差异摘录，不是另一套可运行教学实现。

因子索引原来分散说明：

```python
# 动量索引 t-21 对应 253 窗口倒数第 22 项。
if definition.factor_id == "momentum":
    # 第一个价格恰好是 t-252。
    value = values[-22] / values[0] - 1.0
```

现在合并为公式旁的窗口说明，计算完全不变：

```python
# 253 个价格覆盖 [t-252, t]；倒数第 22 项是 t-21，跳过最近 21 日。
if definition.factor_id == "momentum":
    value = values[-22] / values[0] - 1.0
```

执行入口原来的 `_submit` docstring 仅简述持锁提交流程；现在明确调用方必须已持账户锁，解释报价/风险上下文、重复请求只恢复、超时保留 UNKNOWN，以及写意图/订单状态和调用 Broker 的副作用。`_recover` 仍在正文解释 `just_persisted` 仅豁免本次新建且尚未发送的意图，不能跨重启使用；正文按查询、导入事件、订单/账户/成交核对分段，未将所有信息搬到函数顶部。

因子测试删除「只提取被测低波动结果」等调用复述，保留 30 次 +1% 和 30 次 -1% 的构造，以及 `-0.01 × sqrt(252 × 60/59)` 独立手算。账务测试将误写的「三分之二拆分」纠正为实际 `ratio=0.5` 产生半股，并补足现金与剩余成本的手算；原数值及断言不变。

| 位置 | 修改前 | 修改后 |
|---|---|---|
| signals 百分位 | 「最低0最高1」 | 说明范围为[0,1]，并列端点未必取0/1，全并列时均为0.5 |
| ledger 成交入口 | 笼统说透支均抛 ContractError | 区分买入现金不足与卖出费用导致返回模型校验失败，说明异常向调用方传播 |
| test_ledger 剩余成本 | 「剩余六股的取得成本为600.6」 | 「原成本1001按剩余6/10保留，取得成本为600.6」，补足独立推导 |
| test_application 研究边界 | 「独立找到动量首次具备253个价格的有效时间」 | 明确依据留存因子结果，253价格窗口另由固定因子样本验证，不夸大这条断言 |

## 治理测试逐项迁移

| 原测试 | 新测试/保护 |
|---|---|
| test_documented_statements_and_function_types_pass | test_documented_definitions_without_statement_comments_pass：普通导入、字段、赋值、返回和分支无需逐句说明 |
| test_missing_real_adjacent_chinese_comment_fails（6 场景） | test_ordinary_statements_do_not_require_adjacent_comments：原普通语句场景均允许通过 |
| test_else_branch_requires_its_own_explanation | test_branches_do_not_require_separate_comments：else/except/finally 无独立注释也通过 |
| test_previous_inline_comment_cannot_explain_next_statement | test_inline_comment_does_not_require_comment_on_next_statement：保留行内注释，后一赋值不承担密度门槛 |
| test_compound_header_cannot_borrow_child_inline_comment | test_compound_header_does_not_require_separate_comment：简单 if 无独立说明也通过 |
| 函数中文说明、参数/返回类型缺口原测试 | 保持原逻辑，仍同时报告三项缺口 |
| 新增真正 docstring 反例 | 缺失/英文模块、类、同步/异步函数说明仍失败；变量内及非首条普通字符串不能冒充 |
| 新增参数种类反例 | 位置专用、普通、关键字专用、可变位置/关键字参数缺类型仍失败 |

其他架构、文档引用、Schema/样例漂移、敏感差异和备份测试保留输入与断言。检查器仅删除邻接辅助函数、相关 io/tokenize 导入和对应逻辑，公开检查接口与文件范围不变，排除集合仍为空。不存在新的密度、固定行数、强制标签或关键词计数。

## 验证、复核与限制

执行证据位于新的 `artifacts/comment-semantics-20260919/`，该目录被 Git 忽略；新检出需重新运行。以下 `uv` 为 `/private/tmp/quant-qc014-tools/bin/uv`（0.12.5），每条运行命令带 `UV_CACHE_DIR=/private/tmp/quant-qc014-uv-cache`。解释器为 `/Users/sakiko/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`（3.12.14），依赖按原 uv.lock 安装在 `.venv`。

| 实际命令/证据 | 结果与界限 |
|---|---|
| 初始 `command -v uv`；bundled Python `-m pip install --disable-pip-version-check --no-input --target /private/tmp/quant-qc014-tools uv==0.12.5` | PATH 无 uv；默认沙箱 DNS 无法解析 PyPI，安装退出1。随后通过受控联网以 `--no-cache-dir` 安装同版本至临时目录，退出0；未修改系统 Python。 |
| `uv sync --locked --offline --python <上述解释器> --cache-dir /private/tmp/quant-qc014-uv-cache` | 退出1：缓存缺少锁定包，无法离线下载。 |
| `uv sync --locked --python <上述解释器> --cache-dir /private/tmp/quant-qc014-uv-cache` | 受控联网安装29个锁定包，退出0；不更新锁。安装联网与离线业务运行分开。 |
| `rules-red.json` / `rules-green.json` | 先写新规则固定样例，旧检查器18通过/10失败；新检查器28通过/0失败。bundled Python3.12原样执行，无解释器兼容补丁；这不是完整 pytest。 |
| `uv run --offline --locked pytest tests/test_governance.py -q` | 独立复核者实际运行，40 passed，退出0。 |
| `uv run --offline --locked pytest tests/test_governance.py tests/test_contracts.py -q` | 56 passed、1条既有警告、33.92秒，退出0。 |
| 初次 `uv run --offline --locked ruff check src tests tools` | 退出1：移除 import 语法注释后留下空行，触发20个 I001 格式诊断。使用既有 Ruff 只整理 import 空行及格式，完整 AST 前后相同（含 docstring 和导入顺序），见 `format.json`。 |
| `uv run --offline --locked quant-core check --base 01205ca` | `check.log` 退出0：133 passed、20 warnings、55.11秒；独立语义复核后 `check-final.log` 再次退出0：133 passed、20 warnings、57.04秒，治理/Ruff/格式/mypy通过。 |
| `uv run --offline --locked python -m tools.export_contracts --check` | `contracts-check.log`、`contracts-final.log` 两次只读退出0，生成 Schema 与六个正反样例一致，未重写生成文件。 |
| `.venv/bin/python artifacts/comment-semantics-20260919/verify_ast.py` | 40个跟踪 Python 中38个归一化 AST 与基线一致；仅治理规则及对应测试逻辑变化。治理其余函数/常量、无关治理测试一致，unexpected_changes=[]，见 `ast-verification.json`。 |
| `uv run --offline --locked quant-core demo --config configs/demo.toml --output artifacts/comment-semantics-20260919/demo` | `demo.log` 退出0，运行ID `e1eecad399e04c73a62b69e5`。 |
| `uv run --offline --locked quant-core validate --run-dir artifacts/comment-semantics-20260919/demo` | `validate.log` 退出0。 |
| `uv run --offline --locked quant-core replay --run-dir artifacts/comment-semantics-20260919/demo --output artifacts/comment-semantics-20260919/replay` | `replay.log` 退出0，account/orders/factors/signals/target 全为 true。 |

验证了 docstring 的非 AST 影响：检索业务无显式 `__doc__`/`inspect.getdoc` 读取，治理使用 `ast.get_docstring`，`storage.schema_documents` 使用模型 `model_json_schema()`。导出契约模型的类 docstring 未变；ResearchFold 类说明澄清生成方职责，但不在该导出集合中。协议方法和普通函数说明不改变现有 Schema。生成文件的精确比较同时保护描述、字段、类型、默认值和约束，不以只比较字段个数替代。

最终完整检查后仅改两条业务测试注释：研究有效时点来自留存因子结果，不冒称独立计数253价格；提交夹具的1000美元仅指默认十股，允许传入其他订单。再次进行 AST、静态治理、格式和 `git diff --check` 核验，没有因纯注释变动重复运行整套业务测试。

最终静态结果保存在 `final-static.json`：governance_errors=[]，四项子命令均退出0，受保护的配置/依赖/CI/Schema路径无改动，当前源码与新运行指纹相同。AST验证脚本解析时保留类型注释，只有真正模块/类/函数首条 docstring 被移除，普通字符串（包括测试内嵌子进程脚本）参与完整比较。

新 demo 清单与当前 `code_evidence()` 的源码指纹均为 `bfe76e9a5ba554fd23577b917f1824aa81e9519512cb6b691aad42a9660549b2`，commit=`01205cae9f3c396938bdcdbe6c6cd4af216f89cb`、dirty=true；新运行没有覆盖任何旧快照或沿用旧源码指纹。全量检查的20条第三方弃用警告未屏蔽；validate/replay 在 macOS 沙箱下出现 PyArrow sysctl CPU 信息权限警告，仍退出0，原始日志保留。

独立 AI 复核按不同作者进行：执行作者复核治理迁移及核心模块；治理作者复核执行/适配器/应用/CLI与两份对应测试；核心作者复核其余九份业务测试和长期规则；主代理复核契约与因子测试及整合差异。复核关注主要阶段、调用前提、易错边界、重复语法说明与过强承诺。异常类型、百分位并列端点、事件处理返回值、锁职责、事务范围和测试独立性说明问题已逐项修正并由发现者核对关闭。作者自检 AST 与上述独立语义复核分别记录；AI交叉复核不冒称外部人工批准。

本轮未单独运行 research CLI、运行目录的完整灾恢或远端 CI；既有研究/备份/恢复测试仍在完整检查中执行。未接触真实账户、发送外部订单、发布、部署或改变远端权限。macOS 本地验证不等于生产平台或持续交易验收。

已知 F01 行业版本风控、F02 目标外持仓监控和 F03 碎股缺口仍未修复，见 [原阻塞清单](launch-blockers.md)。相关源码仅如实说明当前范围，没有把缺陷包装成已满足保证。另纠正了备份期间源变化、生成文件集合原子性、测试进程禁网范围和演示执行时刻等超过实际实现的旧说明，均未修改逻辑。

阅读中另外留存两项边界观察供后续业务审查，不在本次定级或修复：`SQLiteEventStore.apply` 的事件序列化在 try 之外，序列化失败直接传播而不持久冻结；`regime` 在本次 as_of 选定历史修订后重放确认，并非逐个历史日重新选择当时版本。当前说明已写清实际范围，不能由注释迁移推导这些边界已获得业务验收。
