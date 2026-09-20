# macOS 开发环境核验（2026-09-19）

> 历史记录：以下结论、路径及授权仅适用于文中日期和基线；后续实现与验证见[当前状态](../../PROJECT_STATE.md)。本地 `artifacts/` 证据未随 Git 分发，部分旧产物可能已不可用。

本记录核对当前锁定依赖在 macOS 上的环境重建与离线开发流程，关联 QC-008、QC-010、QC-011、QC-012、QC-013 和 QC-015。业务行为、需求条文及原实现映射不变；不将平台验证解释为已修复既有业务缺陷、接入 Alpaca 或完成生产验收。

## 基线与环境

起始工作区干净，HEAD 为 `fadd4e0f8c1a078cc2d9232b08b76a682afc5cf7`。修改前保存117个文件的快照、SHA-256摘要、Git状态及已有虚拟环境和shell配置摘要。证据保存在新的 `artifacts/macos-validation-20260919/`，该目录被Git忽略，新检出不会自动获得这些产物。

| 项目 | 本次实际环境 |
|---|---|
| 系统与架构 | macOS 26.5.1（25F80），Apple Silicon / arm64。 |
| Python | CPython 3.12.14，来自现有Codex运行时；本轮没有安装或下载Python。 |
| uv | 官方独立安装器安装0.12.5至 `~/.local/bin`，与现有Linux CI工具版本一致。 |
| 依赖 | 使用原 `uv.lock`；独立环境实际安装29个包，包含本项目；与原环境安装版本一致。 |
| SQLite | 当前解释器使用SQLite 3.53.1。 |
| 验证环境 | `artifacts/macos-validation-20260919/venv`，与原 `.venv` 分开。 |

Python基路径为 `/Users/sakiko/.cache/codex-runtimes/codex-primary-runtime/dependencies/python`。独立环境复用这份解释器和已有uv缓存；验收证明从空虚拟环境按锁文件重建成功，不是从空包缓存下载、独立安装Python或全新电脑初始化的验收。若基础解释器被移除，依赖它的虚拟环境需要重建。

## uv 安装与环境重建

初始PATH和常见用户/Homebrew安装位置没有持久uv，只有旧任务的临时副本。确认 `~/.local/bin/uv`、`uvx` 不存在且没有同名符号链接后，从官方 `https://astral.sh/uv/0.12.5/install.sh` 下载并读取安装器，执行：

```bash
env UV_NO_MODIFY_PATH=1 UV_INSTALL_DIR=/Users/sakiko/.local/bin \
  sh artifacts/macos-validation-20260919/uv-install.sh
```

首次下载在沙箱内DNS解析失败，curl退出6；申请必要网络权限后原URL下载成功，安装退出0，uv/uvx均报告0.12.5。没有改用其他版本、包管理器或安装路线。安装器写入用户目录和自身安装凭据，不使用sudo；保存的shell配置摘要全部不变。官方行为说明见[指定版本安装](https://docs.astral.sh/uv/getting-started/installation/)与[不修改PATH的选项](https://docs.astral.sh/uv/reference/installer/)。

以下验证以仓库根目录为工作目录，`uv` 实际为 `/Users/sakiko/.local/bin/uv`。除检查原环境与导出清单外，命令环境均包含：

```bash
export UV_PROJECT_ENVIRONMENT="$PWD/artifacts/macos-validation-20260919/venv"
export UV_CACHE_DIR=/private/tmp/quant-qc014-uv-cache
export UV_PYTHON_DOWNLOADS=never
export PYTHONDONTWRITEBYTECODE=1
```

这些是本轮独立验证进程的设置，不写入shell启动文件，也不要求日常开发一直设置 `UV_PROJECT_ENVIRONMENT`。日常按快速开始使用项目 `.venv`。

| 操作 | 命令与结果 |
|---|---|
| 原环境只读检查 | 未设置 `UV_PROJECT_ENVIRONMENT` 时执行 `uv sync --locked --offline --check`，退出0，无需改变原环境。 |
| 空环境重建 | `uv sync --locked --offline --python /Users/sakiko/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`，退出0；缓存足够，安装29包，没有联网获取项目依赖。 |
| 重建后检查 | `uv sync --offline --locked --check` 与 `uv pip check --python artifacts/macos-validation-20260919/venv/bin/python` 均退出0；关键分析库导入通过，版本与原环境一致。 |

## 离线命令与结果

下列业务命令均带 `uv run --offline --locked quant-core` 前缀；输出分别保存为证据目录中的 `<名称>.stdout`、`<名称>.stderr` 和记录命令、退出码、耗时、环境的 `<名称>.json`。

| 名称 | 命令后缀 | 结果 |
|---|---|---|
| check | `check --base fadd4e0f8c1a078cc2d9232b08b76a682afc5cf7` | 退出0；治理零诊断，Ruff/格式/mypy通过，133项测试通过、20条警告，pytest耗时54.54秒。 |
| demo | `demo --output artifacts/macos-validation-20260919/demo` | 退出0，生成新运行目录。 |
| validate | `validate --run-dir artifacts/macos-validation-20260919/demo` | 退出0。 |
| replay | `replay --run-dir artifacts/macos-validation-20260919/demo --output artifacts/macos-validation-20260919/replay` | 退出0，账户、订单、因子、评分、目标五项均为true。 |
| research | `research --run-dir artifacts/macos-validation-20260919/demo` | 退出0；独立实验0001，3,600个观察样本、2个滚动分组。 |
| report | `report --run-dir artifacts/macos-validation-20260919/demo` | 退出0，标准输出与原 `report.md` 字节一致。 |
| demo-repeat | `demo --output artifacts/macos-validation-20260919/demo-repeat` | 退出0；账户、订单、事件、journal、因子、评分、目标、风险、市场状态、快照、对账和报告12项与首次演示字节一致。 |

业务命令不访问真实行情或券商；`--offline` 限制uv取依赖，不是操作系统级禁网。本次未新增禁网拦截器。完整开发检查保留20条exchange_calendars/NumPy timedelta弃用警告，不通过升级依赖或屏蔽警告处理。

validate、replay、research、report的stderr保留PyArrow读取CPU缓存/NEON信息时的 `sysctlbyname ... Operation not permitted` 诊断，命令仍退出0；没有屏蔽诊断、改系统权限或更改依赖来消除输出。

## 关键边界的现有测试

独立复核对照代码确认以下覆盖，不新增或修改测试：

| 边界 | 已有证据及限制 |
|---|---|
| 文件锁 | `test_cross_process_account_lock_ignores_database_directory` 用真实子进程、不同数据库和工作目录争用同账户锁，验证被阻断及正常释放后可再获取。没有证明多机、多用户或网络盘协调。 |
| SQLite状态与恢复 | `test_store_dual_unique_ids_and_restart`、`test_same_identity_different_content_freezes_atomically`、`test_invalid_filled_zero_state_is_frozen_at_storage_boundary`、`test_partial_fill_restart_keeps_fee_and_cash_exact` 覆盖重开、重复身份、冲突冻结与部分成交恢复；没有进程强杀、断电或磁盘故障验收。 |
| 备份恢复 | 原治理测试检查静止数据库的备份、恢复及独立SQL结果；没有活跃双库原子备份或并发写压测。 |
| Parquet文件 | `test_parquet_round_trip_exclusive_write_and_invalid_external_unit` 覆盖UTC/封印往返、不覆盖和外部非法单位。 |
| 日历与时间 | `tests/test_calendar.py` 覆盖2023春季夏令时切换、Good Friday休市、半日市、开闭端点、越界、无时区输入与固定时钟；没有直接覆盖秋季切换、所有历史年份或改变主机时区的矩阵测试。 |
| 端到端重算 | `test_validate_report_and_replay_leave_source_unchanged` 覆盖源运行保持不变、从新数据库重建及五项比较。 |

## 依赖导出与范围核验

`requirements.txt` 使用维护手册中的固定命令从原锁文件导出，第二次相同命令导出字节一致。包含29个外部依赖条目，包括仅Windows适用的colorama及开发工具，不包含quant-core自身；这与macOS实际安装的“29包含项目、不含colorama”口径不同。平台条件保留不代表仓库支持Windows。

清单版本与锁文件一致；它不是conda环境文件，本轮没有使用pip安装此清单，也没有创建或验证conda环境。依赖范围、锁文件、源码、注释、测试、配置、Schema、检查器及CI均保持基线字节。需求行为没有变化，相关规格与原需求映射无需改写，平台证据关联需求已列于页首。

运行清单如实记录起始提交、dirty=true，以及源码指纹 `1353a5c67072c4e1568288924520e4364fddb50c163f1fb6650348fc3b02e3f1`；未覆盖旧运行或伪造旧指纹。首阶段范围核验另确认原 `.venv` 非字节码文件和已记录shell配置不变；随后用户另行授权的PATH变更见下一节。

## 补充：用户授权永久 PATH

在上述安装与环境核验完成后，用户明确要求新终端可直接找到uv，因此新增用户级 `~/.zshrc`，将 `~/.local/bin` 加入PATH，并用条件判断避免重复添加。原文件不存在，采用独占创建，没有覆盖已有配置；已有 `.zprofile` 的Homebrew配置保持原样。不修改系统配置、Python、conda或其他项目环境。

新启动 `/bin/zsh -lic` 实测 `command -v uv` 为 `/Users/sakiko/.local/bin/uv`，`uv --version` 为0.12.5；重复两次加载配置后，该目录在PATH中仍只出现一次。此前“shell配置不变”的证据对应补充操作前，不能解释为最终完全未修改用户shell配置。安装器仍未修改shell配置，永久PATH是后续明确授权的独立操作。

补充核验同时覆盖非登录交互式zsh；`path-verification.json` 记录只有 `.zshrc` 这一项授权变化，其他已记录配置不变。独立复核者实际重跑路径查找与重复加载检查，无待修正项，记录为 `path-independent-review.md`；最终范围与文档核验另存 `final-after-path-verification.json`，保留此前阶段证据。

## 结论边界与复核

不同作者已完成独立复核，记录为证据目录中的 `independent-review.md`。复核者独立重算117文件快照摘要，检查修改范围、保护文件、依赖清单与锁、结果比较、历史记录保留及平台声明，未发现阻塞或待修正项。最终相对链接/标题锚点、shell片段语法、8条CLI示例解析、静态治理与 `git diff --check` 均通过；作者自检与独立复核分别保存。

本次组合可以作为当前仓库的日常离线开发环境，macOS据此作为主要本地开发平台。依据是空虚拟环境重建、完整检查、六命令及重复结果核验；“稳定”限于这些可复核的开发行为，不代表长期运行故障率保证。

其他macOS版本、Intel Mac、独立Python安装、空缓存下载、Linux本轮复测、远端CI、conda、持续交易和生产环境均未验证。Linux CI仍使用原ubuntu-latest定义，保留其检查入口，不将本机结果当作远端成功记录。F01/F02与碎股限制等既有业务问题没有在本轮修复。
