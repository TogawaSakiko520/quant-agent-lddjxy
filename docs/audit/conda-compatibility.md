# uv 与 Conda 双入口验证（2026-09-19）

本轮以QC-013、QC-015为主要关联需求，验证Conda创建Python环境、环境内pip按共同清单安装项目库的路线，并复核原uv流程。起点HEAD为 `78e5ed7ec5167f0712a5f796a5ef1fa1830990d3`，工作区干净；编辑前保存119文件快照、摘要、Git状态及原环境清单到新的 `artifacts/conda-compatibility-20260919/`。该目录被Git忽略，不覆盖前轮证据；新检出没有这些运行文件。

## 环境分工与实际来源

| 项目 | uv路线 | Conda路线 |
|---|---|---|
| 平台 | macOS26.5.1、Apple Silicon / arm64 | 同一台机器 |
| 项目Python | 原 `.venv`，CPython3.12.14，来自既有Codex运行时 | conda-forge的CPython3.12.14，新建项目专用环境 |
| 工具 | uv0.12.5，`~/.local/bin/uv` | Miniforge26.7.2-0提供Conda26.7.2；目标环境pip26.2.1 |
| SQLite | 3.53.1 | 3.53.4 |
| 项目依赖 | 原 `uv.lock` | 同一锁文件导出的 `requirements.txt` |
| 清单外Python分发 | 沿用原环境 | Conda提供pip26.2.1、setuptools84.0.0、wheel0.48.0 |

当前清单有29个外部包条目，macOS适用28项，colorama仅适用Windows；两条路线的28项版本及quant-core0.1.0一致。Conda预装的packaging26.3已满足锁要求，pip保留其文件，安装者标记仍为conda；不能说所有项目所需分发都由pip重新下载。Conda基础包原有非字节码文件在项目依赖安装前后全部一致。Conda的系统时区数据和Python的tzdata分发位置不同，未发现文件覆盖。

两条路线的可编辑安装指向同一仓库源码。Conda中的Python、pip、CLI、Ruff/mypy/pytest路径以及检查子进程均来自本轮专用环境，没有落回 `.venv`。Python版本和项目包版本相同不意味着底层环境完全一致；SQLite和安装工具差异保留在 `uv-probe.stdout`、`conda-probe.stdout`、`environment-comparison.json` 中。

## 安装过程、失败与副作用

没有发现已有Conda，因此从官方发行下载固定版本 `Miniforge3-26.7.2-0-MacOSX-arm64.sh` 及SHA-256文件，安装器摘要为 `d70bfa2e97afcda96927c9b9ca0e2316cb7750e4ce651c94388267cbe9588711`，同时匹配官方发布元数据和校验文件。

- 首次读取发布元数据因沙箱DNS失败，curl退出6；获必要网络权限后按原URL成功下载。
- 首次安装退出1：官方安装器需要创建 `~/.conda`，被沙箱阻止。读取安装器后确认其登记行为，再获权限对本轮未完成的工具目录执行 `-b -u -p`，退出0。没有换版本、改安装器或删除失败记录。
- 工具安装在本轮证据目录的 `miniforge/`，项目环境在 `envs/quant-core-dev/`，包缓存各自放在同一证据目录。未执行 `conda init`，没有修改shell配置或全局 `.condarc`，没有往其他项目或既有base装包。
- 安装器创建了用户 `~/.conda/environments.txt` 登记文件，本轮核对内容为空；这与修改shell/全局配置不同，不能声称所有用户目录完全未写。项目环境创建与后续命令关闭自动环境登记。
- Miniforge自身的管理环境使用Python3.14.7，仅用于运行Conda工具；项目环境仍为3.12.14。创建项目环境后未升级工具管理环境的依赖。

可编辑安装按原build-system约束使用隔离构建，`--no-deps` 不禁止构建工具下载。实际构建工具为hatchling1.32.3、packaging26.3、pathspec1.1.1、pluggy1.6.0、tomlkit0.15.1、trove-classifiers2026.6.1.19，另有editables0.6；版本保存在 `conda-editable.stderr`，未把这些临时构建工具当作已被uv.lock完整锁定的项目依赖。

## 命令与验证证据

所有命令在仓库根目录运行。证据执行器逐条保存 `<名称>.json`（参数、环境、退出码和耗时）、`.stdout` 和 `.stderr`。验证进程统一设置以下环境变量，不写入shell启动文件：

```bash
export UV_CACHE_DIR=/private/tmp/quant-qc014-uv-cache
export UV_PYTHON_DOWNLOADS=never
export PYTHONDONTWRITEBYTECODE=1
export CONDA_ENVS_PATH="$PWD/artifacts/conda-compatibility-20260919/envs"
export CONDA_PKGS_DIRS="$PWD/artifacts/conda-compatibility-20260919/conda-pkgs"
export CONDA_REGISTER_ENVS=false
export CONDA_AUTO_UPDATE_CONDA=false
export PIP_CACHE_DIR="$PWD/artifacts/conda-compatibility-20260919/pip-cache"
export PIP_DISABLE_PIP_VERSION_CHECK=1
```

执行器移除继承的 `VIRTUAL_ENV`、`CONDA_PREFIX`、`CONDA_DEFAULT_ENV`、`UV_PROJECT_ENVIRONMENT` 和 `UV_NO_SYNC`，避免误选环境。实际uv路径为 `/Users/sakiko/.local/bin/uv`，Conda路径为本轮 `miniforge/bin/conda` 的绝对路径。日常开发者无需复制这些验证专用缓存设置；命名环境由自己的Conda管理。

| 证据名称 | 实际操作 | 已观察结果 |
|---|---|---|
| uv-sync-check / uv-sync | `uv sync --locked --offline --check`；`uv sync --locked --offline` | 均退出0，保留原项目环境。 |
| export | `uv export --locked --offline --format requirements-txt --group dev --no-emit-project --output-file requirements.txt` | 退出0，生成内容与起点文件一致；锁不变。 |
| conda-create | `conda create -y -n quant-core-dev --override-channels -c conda-forge --no-default-packages python=3.12 pip` | 退出0，新环境位于明确的CONDA_ENVS_PATH下。 |
| conda-dependencies | `conda run -n quant-core-dev python -m pip install --require-hashes -r requirements.txt` | 退出0，按清单安装缺少的项目库；packaging已满足而保留。 |
| conda-editable | `conda run -n quant-core-dev python -m pip install --no-deps -e . --verbose` | 退出0，项目可编辑安装与CLI注册成功；verbose仅用于记录构建版本。 |
| conda-repeat-update | 同一Conda环境再次执行上述requirements安装命令 | 退出0，约0.8秒；全部适用条目显示already satisfied，没有重建或重新安装，环境非字节码文件不变。 |
| conda-pip-check / conda-cli-help | 目标环境执行 `python -m pip check` 与 `quant-core --help` | 均退出0。 |

重复安装只验证当前清单在现有环境中的重复执行，不代表本轮实际做过依赖升级、降级、删除清理或Python版本迁移。没有为了演示升级而修改共同依赖。

两条业务路线分别使用 `uv run --offline --locked quant-core` 与 `conda run -n quant-core-dev quant-core` 前缀。完整检查均指定 `--base 78e5ed7ec5167f0712a5f796a5ef1fa1830990d3`；业务输出分开存放在 `uv-demo` / `uv-replay` 和 `conda-demo` / `conda-replay`，全部位于本轮证据目录。对应命令后缀为 `demo --output <新演示目录>`、`validate --run-dir <演示目录>`、`replay --run-dir <演示目录> --output <新回放目录>`；证据JSON保留未经省略的实际参数。

## 实际验收结果

| 核验 | uv路线 | Conda路线 |
|---|---|---|
| 完整check | 退出0，133 passed、20 warnings，pytest55.04秒 | 退出0，133 passed、20 warnings，pytest58.58秒 |
| 治理、Ruff、格式、mypy | 均通过 | 均通过；子进程使用Conda项目解释器 |
| demo / validate / replay | 三条命令均退出0 | 三条命令均退出0 |
| 回放五项 | account/orders/factors/signals/target全部true | 同左 |
| requirements重复导出 | 两次实际导出均与起点字节一致，锁未改 | 安装直接使用同一文件 |

跨环境的account、orders、factors、signals、target、events、journal、risk、regime、reconciliation共10份业务JSON按完整解析结果精确一致，本次无需浮点容差。两份演示报告也字节一致，但没有把环境清单全文或SQLite物理文件字节相等作为要求。具体比较见 `business-comparison.json`。两份新运行清单均如实记录本轮提交、dirty=true及源码指纹 `1353a5c67072c4e1568288924520e4364fddb50c163f1fb6650348fc3b02e3f1`。

完整检查保留exchange_calendars/NumPy timedelta的20条既有弃用警告；沙箱内两条路线的部分校验/回放出现PyArrow读取CPU信息时的sysctl权限诊断。业务命令退出0，没有改系统权限或屏蔽警告。安装联网与本地业务运行分开；uv的 `--offline` 只约束其依赖获取，本轮未新增操作系统禁网拦截器。

当前证据支持本次macOS26.5.1/arm64组合下两种开发入口兼容。未验证Intel Mac、其他macOS版本、其他Conda发行版、Linux本轮复测、远端CI、跨环境research/report独立CLI、持续运行或生产交易；既有研究测试包含在两次完整check中。没有实际升级、降级或删除项目依赖，也未验证整个系统环境完全可复现。

## 文档与规则边界

README末尾各提供一条日常更新命令，首次创建环境、项目可编辑安装和检查单列；删除依赖或调整Python/基础包时才按本阶段标准新建环境。pip普通安装不自动清理旧包，`pip check` 不等于锁定清单或残留检查，哈希校验不复验所有已安装文件。

根规则只替换协作验证段中“仅用uv”的入口限制，保留锁文件、独立复核和真实验收要求。源码CLI中原有uv调用注释按禁止修改源码的范围保留；真实实现用 `sys.executable` 启动检查子进程，已核对其能够沿用Conda解释器。规格、业务源码、测试、配置、Schema、检查器和Linux CI不变；需求映射只追加QC-013/015文档入口。

官方行为依据与本机工具帮助交叉核对：[Conda环境与pip分工](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html#using-pip-in-an-environment)、[Conda 26.7创建参数](https://docs.conda.io/projects/conda/en/26.7.x/commands/create.html)、[pip可编辑安装](https://pip.pypa.io/en/stable/topics/local-project-installs/)、[pip哈希校验边界](https://pip.pypa.io/en/stable/topics/secure-installs/)、[uv pip目标环境](https://docs.astral.sh/uv/pip/environments/)。本轮未新增永久检查器或Conda CI。

## 最终复核

非作者独立复核已完成，记录在本轮证据目录的 `independent-review.md`。复核者重算起点摘要与保护范围，检查两条路线实际日志和解释器、根规则最小变更、基础包所有权、构建依赖与哈希边界，独立比较10份业务JSON及两套回放结果，无阻塞或待修正项。此前快速开始的失败来源提示已补齐uv、Conda、pip并回看关闭。

最终范围核验为8个已有规则/文档/映射文件修改和1份新增审计页；requirements实际重导出但字节未变。原 `.venv`、业务源码、测试、配置、依赖声明/锁、Schema、检查器、CI及历史记录保持起点内容，shell/全局配置不变；本轮Miniforge管理环境包记录与项目环境原有基础文件均未被后续安装覆盖。相对链接/标题锚点83项、11条业务CLI示例解析、shell片段语法、静态治理及 `git diff --check` 通过。证据分别见 `final-verification.json`、`environment-preservation.json`、`cli-examples.json`，作者自检未冒称独立复核。
