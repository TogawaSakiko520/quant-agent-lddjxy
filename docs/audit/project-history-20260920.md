# 项目状态与变更原始记录归档（截至 2026-09-20）

本页保存本次整理前 PROJECT_STATE 与 CHANGELOG 的完整正文。下文的“当前”“本轮”和权限只适用于各段原始日期及基线，不是当前操作授权；当前事实以 [PROJECT_STATE](../../PROJECT_STATE.md) 为准。只调整相对 Markdown 链接的目录层级，日期、数字、失败和结论均保留。历史 `artifacts/` 链接是未随 Git 分发的本地证据定位，部分旧产物本机也已不可用，不要求新检出存在。

## 原 PROJECT_STATE 全文

# 项目当前状态

本文件是交接事实源；聊天中的承诺和计划不等同于已实现能力。更新日期：2026-09-20。

## 当前阶段：真实Paper提交、查询、撤单与重启核对通过（2026-09-20）

本轮用户将验收范围明确调整为休市订单操作，并授权通过后commit/push；实际成交标准仍独立保留。包含前轮成果的148文件基线在`artifacts/paper-queue-20260920/baseline`，所有真实尝试及失败追加保存，没有替换旧证据。

- **真实数据与策略**：10只候选的SIP原始/仅拆股日线，8月21日至9月18日的20日MA窗口；5个正趋势评分，目标AAPL1、GOOGL1、XOM2。原4.5%目标、5%单票等约束保留；本轮从首个可行MA目标取AAPL一股作受限订单测试。
- **真实提交与查询通过**：唯一执行服务提交BUY LIMIT DAY一股，限价336.13 USD、费用预留1 USD；远端订单ID `981caa56-2789-4944-9c4b-4f0c17149e2c`。客户ID与计划绑定，获得回执后再次查询，不把受理记成成交。
- **真实撤单与恢复通过**：仅撤本单，远端与内部均CANCELED、累计成交0；新进程恢复后队列验收两标记为true。远端现金100000、空仓无开放订单；策略现金10000+固定未分配90000精确一致，恢复未增加订单日志或成交事件。实际成交、成交入账、卖出与正常MA成交闭环仍未验收。
- **已确认缺陷已修复**：纽约偏移转UTC保留同一时刻；小于等于1秒的远端时钟领先采用真实等待后严格校验；明确POST前拒绝与发送后未知分开。旧未发送UNKNOWN经固定源码证据和独立远端核查追加本地拒绝，不改现金、不删历史、不盲重发。真实订单短页结束读取，满页仍要求游标推进，重复身份拒绝；大于等于500条真实分页未验收。
- **离线演示保留**：两环境最终原离线demo、validate、replay均成功，回放五项true、10份业务JSON精确一致；新目录均带release后缀。沙箱PyArrow CPU探测诊断保留，不影响退出码或验证结果。
- **双环境完整检查通过**：最终uv与Conda各439项测试通过、36项警告；治理、Ruff、mypy无错误。检查日志使用release名称，之前源码变动期间的失败保留，不计最终通过。
- **展示**：本机窗口读取`artifacts/paper-queue-20260920/plan-04`，显示完整Alpaca ID、CANCELED/0、账户一致及单独的休市测试通过。uv/Conda实际启动，HTTP数据相等；浏览器刷新验证仅重读本地，实际成交仍显示待验证。README含两种启动方法。

本轮计划ID为`c4c6f8f7a821443d91d9a1e6029e2bfff84658ec6222ac7f48047e96a73ed9b6`。执行源码指纹和恢复修复后的指纹分别保存在授权边界；最后事实为`plan-04/observations/0002`。非作者复核覆盖风险、异常边界、分页、真实账务与重启证据，见[本轮审计](../../docs/audit/paper-queue.md)。凭据、原始账户资料和运行目录均不入Git，发布的是源码、独立脱敏测试和中文文档。没有自动预约、持续交易、实盘、卖出或购买数据。以下旧记录保留当时阶段含义。

## 当前阶段：MA真实输入、策略计划与展示已验证，Paper成交待验收（2026-09-20）

用户批准新增独立MA5/MA20价格趋势策略，保留原双因子与离线入口；根规则中的前轮仅注释限制不适用于本轮明确授权的业务改动。起点含前轮未提交成果，139文件基线保存在 `artifacts/ma-paper-20260920/baseline`。新增代码与契约、配置、Schema、需求映射及中文说明已同步，无新增依赖或相对本轮基线的锁文件改动。

- **代码与离线验证通过**：MA连续20日窗口、正趋势评分、最多3个可行整股目标、未知行业最坏集中度，复用唯一执行服务及持久化恢复；支持5秒轮询、最多5分钟观察及仅撤本轮余单后的60秒核对。原4.5%基础目标、5%单票及其他风险阈值保留，原策略仍拒绝缺失总回报/行业。
- **真实只读及策略计算通过**：再次读取指定Paper账户，现金100000 USD、空仓无订单；10只候选各56条SIP原始和56条仅拆股日线，保存请求、公司行动及身份绑定证据。2026-08-21至09-18的20个完整交易日进入MA；5只正趋势评分，META因一股超过450 USD基础额度跳过，目标为AAPL 1股、GOOGL 1股、XOM 2股。按最近原始收盘估值1012.75 USD，独立预算10000 USD；不是已发订单或成交。
- **双环境检查通过**：uv及Conda完整check各369 passed/26 warnings，分别63.96秒、66.91秒；两套原demo/validate/replay均退出0、回放五项true、10份业务JSON一致。首次完整检查的3个失败来自新增旧版契约测试误读新版生成示例，改为固定旧版独立样本后通过，未降低断言。详情见[MA接入记录](../../docs/audit/ma-paper.md)。
- **展示已验证**：uv及Conda均实际启动并在浏览器展示真实MA值、评分、目标与未完成阶段；刷新只读本地保存事实。README已列出两种入口，已有数据无需重跑交易即可查看。本机当前窗口读取 `artifacts/ma-paper-20260920/plan-01`。
- **提交、成交、成交后核对及重启恢复未验收**：真实订单仍为0。市场关闭，下一常规开盘为2026-09-21美东09:30（香港21:30）；用户已明确批准该具体计划，确认保存在本轮user-approval.json。没有自动预约、持续交易、实盘或用FakeBroker代替真实验证。计划ID `ffb51588d7d7d10c0ab8f624eec833962af3b2e1f422aec0184a95587be4afdb`，仅限该常规时段，执行前重新核验资格、账户和新鲜报价。

非作者复核覆盖契约/存储/数据、策略/风险、执行/展示与中文说明；真实计划另以Decimal从原始响应独立重算。原双因子仍缺严格总回报与行业，不能用MA验收替代。既有非成交活动账务、任意旧仓迁移、Paper独立回放CLI及跨主机限制保留。

用户最新要求在**全部验收通过后**执行Git commit并push至GitHub，提交格式为英文标签加中文说明（如 `feat: 接入MA趋势Paper策略与只读展示`）；该条件尚未满足，当前未提交或推送。保留全部工作区与证据，停止在休市边界。以下历史记录保留其原时点含义。

## 当前阶段：Alpaca Paper 接入与真实只读已完成，策略闭环未验收（2026-09-19）

本轮用户明确授权必要真实接入开发，按两次确认指定Paper账户及凭据来源、10只候选和10000 USD策略预算；尚未批准第一笔订单。起点HEAD=`f48cd4fc8dfc15d55c45655c08a4a957771694c2`，工作区干净，120文件快照保存于新的 `artifacts/alpaca-paper-20260919/`，旧证据不覆盖。

- 原因子、评分、组合约束及风险默认值保留；修复F01全风险占用行业时点、F02目标外持仓监控、网络查询期间合法成交误判未来事件，并补独立反例。Paper部分成交撤单后同计划再运行不补发，未知状态只恢复；提交回执不记账，逐笔FILL去重。
- 新增官方alpaca-py0.44.0、8个传递依赖及真实锁定/导出；原已锁定外部包不升级。显式Paper配置与四个CLI入口复用唯一执行服务；默认demo仍无网无密钥。StrategyConfig与PaperConfig、公开时间未知None及派生Schema同步。整股之外事实保留原始响应并阻断，未实现碎股交易。
- 真实只读已读取指定ACTIVE/USD Paper账户：现金100000、购买力400000、非保证金购买力100000 USD，空仓无开放/历史订单；10只候选各429条SIP原始日线。策略预算仍10000，不占全部购买力。IEX最新报价可访问，SIP最新报价403，实时SIP权限不具备。市场关闭，下一常规开盘2026-09-21美东09:30。
- 原始账户包含100000 USD JNLC初始化活动，日期2026-09-21、无transaction_time；不伪造发生秒数、不重记或算收益。后续恢复固定初态活动ID和全文哈希，内容变化/新增非FILL仍阻断。
- **合格行业及严格股息再投资总回报资料尚缺，普通股类别公开证据尚待稳定ID绑定**；Alpaca all调整不被擅自当成原策略总回报。真实因子/评分/目标尚未运行，未发送订单、未发生本程序Paper成交，成交后账户核对未验收；不得把账户查询或离线脱敏闭环当成真实策略完成。
- 独立作者分别复核风险/监控、数据、SDK、应用/配置与锁文件；发现问题已有回归关闭。uv与新克隆Conda专用环境完整check均退出0，各230 passed/21 warnings，分别54.69秒、64.84秒。两套原demo/validate/replay均退出0，回放五项全部true，跨环境10份业务JSON精确一致；该230项记录对应展示工具补充前的核心接入版本。Conda旧环境保留，新环境按同一哈希清单安装并更新可编辑元数据。

新增独立本地展示窗口 `viewer/` 与 `tools/paper_viewer.py`：只读固定运行文件，不导入策略或SDK、不读取凭据、不发单。已在浏览器显示真实Paper现金/预算、10只行情、阶段缺口和空订单；导航、刷新与窄屏采集时间显示通过。独立24项展示测试及复核完成。公司行动只读另取得96条现金分红及相关行动，普通股类别在10个Nasdaq官方摘要标题得到确认；行业仍为空，XOM身份变化需核实，不据此构造未经验证的总回报。新增展示后uv/Conda完整check各254 passed/21 warnings（55.09秒、57.12秒），两套新demo/validate/replay均退出0、回放五项true、跨环境10份业务JSON一致；当前指纹与静态页面哈希另记viewer-validation.json。

运行、凭据和恢复说明见[Paper手册](../../docs/runbooks/alpaca-paper.md)，官方来源、数据补充选项与实际证据见[接入记录](../../docs/audit/alpaca-paper.md)。只读复核readonly-02再次成功，代码指纹与展示补充前的核心离线验证一致。首要停止点是合格策略输入，不请求盲目下单批准。持续服务、跨主机、任意历史账户迁移、非成交活动账务、Paper独立回放CLI、实盘及远端CI未验收。未提交/推送、购买、实盘或部署。以下历史阶段原文保留。

## 当前阶段：uv 与 Conda 双入口兼容完成（2026-09-19）

起始工作区干净，HEAD=`78e5ed7ec5167f0712a5f796a5ef1fa1830990d3`；119文件快照、摘要、Git状态和环境清单保存在新的 `artifacts/conda-compatibility-20260919/`。本轮不重复macOS迁移，以QC-013/015关联环境入口和维护说明；详细命令、版本与限制见 [双入口兼容记录](../../docs/audit/conda-compatibility.md)。

- 保留uv0.12.5及原 `.venv`。新增Conda路线：Conda创建Python3.12环境，目标环境的pip按共同requirements安装库，再以 `--no-deps -e .` 安装项目；不要求Conda用户日常安装uv，不用Conda覆盖同组项目库。
- 本机没有Conda，固定下载并校验官方Miniforge26.7.2-0，在忽略目录内安装工具并创建 `envs/quant-core-dev`。实际Conda26.7.2、项目Python3.12.14、pip26.2.1；与uv的28项适用外部依赖及项目版本一致，CLI、源码导入和检查子进程均指向正确环境。SQLite为3.53.4，对比uv的3.53.1，未声称底层环境完全相同。
- README末尾明确各一条日常更新命令：`uv sync --locked`；`conda run -n quant-core-dev python -m pip install --require-hashes -r requirements.txt`。首次项目安装和开发检查分开；普通依赖更新不必重建，删除依赖或改变Python/基础包时按标准新环境验证。
- Conda首次安装、可编辑安装、pip check及CLI入口检查均退出0。在同一环境实际重复执行单命令更新，退出0、约0.8秒，已满足包保留、环境非字节码文件不变。预装packaging符合锁要求，pip没有覆盖原Conda基础文件；构建隔离工具版本单独记录，不冒称由uv.lock完整锁定。
- 原uv路线实际同步、完整check、demo、validate、replay均退出0；**133 passed、20 warnings、55.04秒**。新Conda路线完整check及同样三条业务命令均退出0；**133 passed、20 warnings、58.58秒**。两边回放五项true，跨环境10份业务JSON完整结果精确相同；环境差异单列。
- requirements两次真实导出均与起点字节一致，无需产生内容变更；项目声明、锁、源码、源码注释、测试、策略配置、Schema、检查器和Linux CI保持原样。根规则只调整环境入口限定，需求映射只追加QC-013/015文档；新清单记录真实源码指纹与dirty=true。
- 非作者独立复核完成，无阻塞或待修正项，记录在证据目录的 `independent-review.md`。最终范围/保护文件、83项链接与锚点、11条业务命令示例、静态治理和 `git diff --check` 均通过；原uv环境及本轮Conda基础文件未被覆盖，历史正文保留。

初次获取发布信息DNS失败（退出6）、初次安装因 `~/.conda` 权限失败（退出1），取得必要权限后按原版本完成，失败日志保留。安装器新建用户Conda登记文件（核对为空）；shell和全局 `.condarc` 未改，没有向既有base或其他项目装包。第三方弃用警告与沙箱CPU信息诊断保留，没有为通过而升级依赖或弱化检查。

未验证其他Mac架构/版本、其他Conda发行版、Linux本轮复测、远端CI、research/report跨环境独立CLI、真实依赖升级/删除/迁移及持续交易。业务缺陷F01/F02与碎股缺口未在本轮修复。完成后停止，不提交、推送、接入Alpaca或自动继续下一阶段。以下旧阶段正文保留，按当时基线解释。

## 当前阶段：macOS 开发环境核验完成（2026-09-19）

开工工作区干净，HEAD=`fadd4e0f8c1a078cc2d9232b08b76a682afc5cf7`；117文件快照、摘要及Git状态保存在新的 `artifacts/macos-validation-20260919/`。本轮关联QC-008/010/011/012/013/015的环境与验证证据，不改变业务行为或验收规则。详细环境、命令、警告与限制集中在 [macOS 开发环境核验](../../docs/audit/macos-development.md)。

- **macOS作为主要本地开发环境**：实测macOS26.5.1、Apple Silicon、CPython3.12.14。原 `.venv` 保留，在独立目录从空环境按原锁文件安装29包，完整版本与原环境一致；同步检查、依赖一致性与关键库导入通过。复用既有Codex解释器和包缓存，没有重新安装Python或验证空缓存下载。
- 缺少持久uv，使用官方安装器将uv/uvx0.12.5安装至 `~/.local/bin`，设置 `UV_NO_MODIFY_PATH=1`；不改全局shell配置。初始沙箱DNS失败（curl退出6），获必要网络权限后按原URL成功下载/安装。安装与运行日志均保留，没有升级依赖。
- 独立环境实际执行 `quant-core check --base fadd4e0f8c1a078cc2d9232b08b76a682afc5cf7` 退出0：治理、Ruff、格式、mypy通过；**133 passed、20 warnings、54.54秒**。各CLI通过 `uv run --offline --locked` 执行，环境路径和完整命令见证据JSON与审计页。
- 新目录demo/validate/replay/research/report全部退出0；回放五项true，报告重生字节一致；研究3,600观察样本、2个滚动分组。第二新目录demo退出0，12类业务产物字节一致。20条既有弃用警告及PyArrow读取CPU信息的沙箱权限诊断未屏蔽。
- 新增根 `requirements.txt`，从原锁文件导出运行与开发依赖，保留版本、平台条件及哈希；重复导出字节一致。其29条外部依赖含Windows条件项，不含本项目；不是conda环境文件，本轮未验证pip按此清单安装或conda兼容。
- README、快速开始、维护手册和导航同步平台声明、uv安装及依赖清单分工。源代码、源码注释、测试、业务配置、元数据、锁、Schema、检查器和CI不变；Linux CI继续保留，远端未运行。原环境非字节码文件与已记录shell配置摘要不变；新清单记录dirty=true及真实源码指纹，不覆盖旧证据。
- 非作者独立复核完成，未发现阻塞或待修正项，记录为证据目录的 `independent-review.md`；范围/链接/锚点、命令示例、静态治理及 `git diff --check` 通过。
- **后续明确授权补充**：用户要求新终端直接找到uv，故新增原本不存在的用户 `~/.zshrc`，有条件地加入 `~/.local/bin`。新zsh登录交互会话找到uv0.12.5，重复加载不增加重复PATH项；原 `.zprofile` 与其他配置未改。上文shell摘要不变是补充操作前的核验事实，最终用户配置已有这一项获授权变更；系统、conda和其他项目环境仍未修改。

未验证范围包括其他macOS版本、Intel Mac、独立Python安装、空缓存下载、Linux本轮复测、远端CI、conda、持续交易和生产环境。本轮不修既有F01/F02或碎股缺口，不提交、推送、部署或连接真实账户。以下此前记录保持原貌，各“当前阶段”标题按记录时点理解。

## 当前阶段：第一阶段文档整理完成（2026-09-19）

本阶段只整理项目介绍、基本使用说明和文档维护入口。开工工作区干净，HEAD=`841966ab1bd823fd8c90f82298b42f3dbf6cad45`；修改前保存116文件快照、摘要和Git状态到 `artifacts/docs-phase1-20260919/baseline/`。源代码、源码注释、测试、配置、依赖锁、CI、检查器、Schema、项目元数据及旧审计文档保持起点字节，未开展其他开发任务。

- 文档表达原则集中追加到 [docs/AGENTS.md](../../docs/AGENTS.md#文档表达原则)，没有新增writing-guide.md。根规则只增加读取引用，原源码注释与业务权限规则不变；提示词目录规则、索引及五份模板引用同一入口。
- [README](../../README.md)按用途/阶段、能力限制、首次运行、报告、六命令、目录结构、开发检查、导航组织；[快速开始](../../docs/runbooks/quickstart.md)集中完整步骤、产物及退出码；新增[文档导航](../../docs/README.md)承接专题、维护和历史入口，不复制正文。
- 代表性处理：保留已有锁定依赖命令和六个CLI；把抽象架构开头改为合成行情至报告的具体用途；详细公式、维护和审计入口移至分组导航；纠正未接Alpaca、一次周调仓演示、Linux/macOS本地验证、文件锁非加密及artifacts运行后才生成等表述。历史验证数字和过程不搬入首页。
- 根规则、统一文档原则和首页由不同作者复核；快速开始与导航另经非作者以目标读者走读。发现的产物链接锚点和目录树缩进已修正，首次术语解释已补充，均已回看关闭。记录位于本阶段证据目录的 `principles-readme-review.md` 和 `reader-review.md`。

### 本阶段实际验证

沿用已有Python3.12.14环境及uv0.12.5，没有安装、升级或同步依赖。以下 `uv` 实际使用 `/private/tmp/quant-qc014-tools/bin/uv`，各运行命令带 `UV_NO_SYNC=1 UV_CACHE_DIR=/private/tmp/quant-qc014-uv-cache`，防止自动同步环境。README中的 `uv sync --locked` 是使用说明，本阶段未执行。

| 实际命令/核验 | 结果 |
|---|---|
| `.venv/bin/python artifacts/docs-phase1-20260919/verify_docs.py` | 起始快照摘要及授权范围检查通过；根规则精确保持为仅追加文档原则引用，原docs规则保留；修改页相对链接、标题锚点及入站链接通过，六命令示例均由实际CLI解析器接受。旧状态记录中的生成产物链接单独列示，不冒充仓库内文件。证据见 `docs-verification.json`。 |
| `uv run --offline --locked quant-core check --base 841966a` | 退出0，治理、Ruff、格式、mypy通过；**133 passed、20 warnings、52.80秒**。见 `check.log`。 |
| `uv run --offline --locked quant-core demo --output artifacts/docs-phase1-20260919/demo` | 退出0，生成本阶段新报告和运行证据。见 `demo.log`。 |
| `uv run --offline --locked quant-core validate --run-dir artifacts/docs-phase1-20260919/demo` | 退出0，不改原运行。见 `validate.log`。 |
| `uv run --offline --locked quant-core replay --run-dir artifacts/docs-phase1-20260919/demo --output artifacts/docs-phase1-20260919/replay` | 退出0，账户、订单、因子、评分与目标五项比较均为true。见 `replay.log`。 |
| `uv run --offline --locked quant-core report --run-dir artifacts/docs-phase1-20260919/demo` | 退出0；标准输出另存 `report.stdout.md`，用 `cmp` 与原演示报告比较，字节一致，未覆盖原报告。 |
| `git diff --check` | 退出0。 |

所有正式验证命令成功，20条既有第三方弃用警告及macOS沙箱中PyArrow读取CPU信息的权限诊断均保留，未通过改依赖或配置消除。未重新验证依赖安装、其他平台、远端CI、真实接口或持续运行；research独立CLI本阶段未执行，参数已核对，现有研究测试包含在完整检查中。安装命令未执行与命令失败分开记录。

完整产物保存在新的 `artifacts/docs-phase1-20260919/`，不提交Git，也不覆盖旧证据。文档变更不改变业务源码指纹；新清单如实记录当前提交与dirty=true，环境前后依赖版本另存核对。本阶段结束，不自动继续环境迁移、接口接入、组件清理等任务，未提交、推送、发布或修改远端设置。

以下保留此前阶段记录；其中“当前阶段”与验证结论均按各记录写入时的状态理解。

## 当前阶段：QC-014 全仓核验与局部补齐完成（2026-09-19）

用户批准实施逐文件核验后的补齐计划，并确认可仅修正治理测试的一句错误说明。开工工作区干净，HEAD=`9f0835be08812b4233f548788a72d18a6fb1a5d9`，前两轮成果已经提交。编辑前保存115文件字节快照、摘要和Git状态到 `artifacts/readability-compliance-20260919/baseline/`，本轮比较基于该快照。

- 审查覆盖src/tests/tools全部40个自有Python文件。16文件局部补齐执行资格时点、公司行动同步责任、响应超时范围、事务/读视图边界、公式统计口径、覆盖率及报告调用前提、契约字段和测试样本说明，其余24文件保留。逐文件判断及修订依据见 [全仓审查记录](../../docs/audit/readability-compliance.md)，不以注释数量验收。
- 不同作者已完成具名语义复核，所有本次发现的注释问题均关闭。治理测试仅授权的一句docstring变化，检查器、测试输入与断言不变；无新规范、密度规则或业务修复。
- 全部40个Python对本轮快照AST一致，普通字符串、类型注释、签名、变量、控制流、公式及断言保留；范围外文件字节全部一致。契约类docstring与生成Schema/六个样例均未改变，现有生成检查退出0，无需写入生成文件。
- 实际 `quant-core check --base 9f0835b` 退出0：**133 passed、20 warnings、54.98秒**；治理、Ruff、格式、mypy通过。新目录demo/validate/replay均退出0，五项回放一致。原始日志、范围验证及独立复核保存在 `artifacts/readability-compliance-20260919/`。
- 新运行清单与当前源码指纹为 `1353a5c67072c4e1568288924520e4364fddb50c163f1fb6650348fc3b02e3f1`，dirty=true；旧快照和旧指纹未覆盖。根/子规则、维护模板、SPEC、架构、CI、依赖锁、配置及前两轮验收文档保持原样；仅需求映射追加本轮报告。

本轮沿用锁定环境，没有安装或升级依赖。正式验收命令均成功，第三方弃用和macOS PyArrow sysctl权限诊断保留；一项作者额外整文件字节比较因并行注释更新而不等，随后按契约类描述/AST/Schema实际保护目标正确核验通过。未单独执行research CLI或完整灾恢，远端CI、持续Paper和真实接口仍未验证；F01/F02/F03等既有问题未修。未连接真实账户、发送外部交易、发布、部署、提交、推送或修改远端权限。

## 第二轮记录：QC-014 局部上下文增强完成（2026-09-19，历史）

本轮以第一轮尚未提交的工作区为起点，编辑前保存114个文件及SHA-256清单到 `artifacts/readability-context-20260919/baseline/`；保留原Git状态和补丁，没有重置或把上轮改动计为本轮新增。根唯一规范补入关键变量来源/形状/单位、事实/目标/预算区别、调用去向、库用法效果和重要出口；允许在关键位置简短重述完整定义，不恢复逐句门槛。同步SPEC、维护模板、工具子规则、维护手册、代码导读及需求路径。第一轮迁移记录保持原字节，以下第一轮验收正文保留，仅将阶段标题标为历史。

- 先补 application、portfolio、signals、execution/sqlite_store 及对应测试样板，再检查其他自有文件。改善 factors/signals/target/intents衔接、effective与risk_quantities、nav/cash/reserved/allocated/remaining、嵌套/复合索引、model_copy/validate/dump、事务/锁与UNKNOWN/空结果等局部阅读障碍。完整前后示例、具名新读者抽样和实际命令见 [第二轮记录](../../docs/audit/local-context-readability.md)。
- 所有40个自有Python对**本轮起始工作区快照**的AST一致，仅忽略真正docstring和位置信息，保留普通字符串、类型注释、签名、变量、运算、分支、测试数据与断言。快照所有文件摘要核验通过；治理检查器/治理测试、CI、配置、依赖锁及全部Schema/样例保持起点字节，没有本轮逻辑豁免。
- 当前锁定环境实际 `quant-core check --base 01205ca` 退出0：**133 passed、20 warnings、52.58秒**；治理、Ruff、格式、mypy通过。该Git基线的敏感提示包含上轮未提交成果，未冒称它是第二轮差异；本轮比较证据为 `round-verification.json` 与 `round.diff`。
- 现有契约生成检查 `tools.export_contracts --check` 退出0；业务未显式读取 `__doc__`/`inspect.getdoc`，模型Schema描述来自类docstring。导出的契约类说明未变，生成文件无漂移，未执行 `--write`。已读取锁定Pydantic源码与SQLite上下文说明核对复制/验证/序列化/事务语义，证据 `library-inspection.json`。
- 新目录 demo、validate、replay 均退出0，回放五项全部true。起始源码指纹为 `bfe76e9a5ba554fd23577b917f1824aa81e9519512cb6b691aad42a9660549b2`，本轮清单与当前源码均为 `fdf13598bd0c799221b85059c66f36e889f226101d0a57c9ff7d5b606018f518`，dirty=true；未覆盖第一轮证据或旧运行。
- 不同作者已完成独立AI语义复核，按实际变量定义/表达式/调用/状态出口抽样检查对象、来源、结构单位、变化、去向和失败后果。发现并更正的说明问题包括空folds原因、固定4.5%目标而非候选均分、risk.json不是当前报告输入、record_order本地状态来源等；全部只改说明。作者自检与独立复核分别留证，不以注释数量验收。

本轮未升级或重装依赖，现有正式检查与离线命令均成功；第三方弃用警告及macOS沙箱PyArrow sysctl权限诊断保留。一个作者AST比较器最初将TypeIgnore.lineno位置误算为差异，修正比较器后保留类型忽略标签并通过，未因此改业务代码。远端CI、真实账户、持续Paper及独立research CLI/完整灾恢未验证；已有研究/恢复测试在完整检查中执行。F01/F02/F03及既有边界观察仍未修复。未发送外部交易、发布、部署、提交、推送或修改远端权限。

## 第一轮记录：QC-014 注释规范迁移完成（2026-09-19，历史）

按用户明确授权，将「逐语句中文注释」改为根 AGENTS.md 集中定义的「按业务语义分层解释」。读者为懂基本 Python、不了解项目及交易业务的开发者。规范、子规则、QC-014/需求映射、维护提示词、维护手册、治理 ADR 和代码导读同步；先完成 factors/execution/test_factors 样板，再逐段整理 src/tests/tools。旧审计、旧验收及历史 CHANGELOG 保留当时口径，未来 PR 条款新增替代注记。完整范围、前后示例、测试迁移和命令见 [迁移记录](../../docs/audit/comment-semantics-migration.md)。

- 开工工作区干净，基线 `01205cae9f3c396938bdcdbe6c6cd4af216f89cb`。除 `tools/governance.py` 的注释检查规则及 `tests/test_governance.py` 对应迁移/新增测试外，全部跟踪 Python（38 个文件）去除真正 docstring/位置后的 AST 与基线一致；普通字符串、签名、控制流、运算、测试输入和业务断言不变。检查器其余函数/常量及无关治理测试亦一致。
- 取消普通语句邻接中文与 else/except/finally 单独注释门槛；保留真正中文模块/类/函数 docstring、完整函数类型、依赖、需求、Schema/样例、敏感差异及全部其他检查，排除集合仍为空。新旧 28 个固定规则样例先 red（旧规则 10 失败）再 green（新规则全部通过）。
- 临时安装历史工具版本 uv 0.12.5，并以 bundled Python 3.12.14 在本地 `.venv` 执行 `uv sync --locked`；依赖版本与 uv.lock 未变。最初缺 uv、沙箱 DNS 不可用和 offline sync 缺缓存均已如实记录，之后受控联网仅用于安装工具/锁定包。未连接真实账户。
- 完整 `quant-core check --base 01205ca` 两次退出 0：首次 **133 passed、20 warnings、55.11 秒**；独立复核修正说明后最终 **133 passed、20 warnings、57.04 秒**。Ruff、格式、mypy、治理均通过；敏感差异提示保留并经跨作者复核，没有伪装成自动批准。最终完整检查后只再修正两条业务测试注释，并以 AST、静态治理及格式检查确认。
- `demo`、`validate`、`replay` 在新目录实际退出 0；回放 account/orders/factors/signals/target 全为 true。`artifacts/comment-semantics-20260919/` 保存日志、AST 核验脚本/结果、新 demo/replay；清单 `dirty=true`，源码指纹 `bfe76e9a5ba554fd23577b917f1824aa81e9519512cb6b691aad42a9660549b2` 与当前源码一致，未覆盖旧快照。
- docstring 使用已检索：业务未显式读取 `__doc__`/`inspect.getdoc`，治理通过 AST 读取，模型生成 Schema 会读取类说明。导出契约模型类 docstring 保持不变；ResearchFold 的类说明虽澄清职责，但不属于既有跨仓库 Schema 导出集合。现有 `tools.export_contracts --check` 两次只读通过，Schema/样例字节、字段、类型、默认值和约束未改，无需重新写生成文件。
- 已完成独立 AI 交叉复核：治理规则/测试、执行与适配器、核心业务模块、所有业务测试及规范文档由不同作者检查。复核纠正了异常类型、状态事件返回含义、百分位并列端点、备份/文件集合原子性、同机锁与测试独立性等说明，未混入业务修复。作者自检与独立复核分开记录。

20 条既有第三方弃用警告未屏蔽；macOS 沙箱下 validate/replay 输出 PyArrow CPU 信息 sysctl 权限警告但退出 0，日志保留。CI 权限、依赖锁、配置、业务公式与交易边界未改；远端 CI、真实账户、持续 Paper 和生产环境仍未验证。本次本地通过不消除 F01/F02 既有缺陷及 F03 碎股缺口。未发布、部署、提交或推送；本轮注释迁移完成，下一业务工程步骤仍按原上线阻塞清单单独安排。

## 上线准备审计记录（2026-09-13，历史）

用户授权按300美元、零付费API任务书审计；本次未修改业务源码、测试、配置、依赖锁、CI或根规则，未访问账户、发送Alpaca Paper/实盘订单、部署或提交/推送。开工工作树干净，分支main、HEAD=`f01a1237bc02deae4957ceecd29639e16037759d`，已有109个跟踪文件；首轮“源码未提交”表述已过时。审计文档写入后，demo/replay清单诚实记录dirty=true，源代码指纹仍一致。

- 内核seccomp禁网、无继承凭据/插件/Git钩子的环境下，实际`uv run --offline --locked quant-core check --base f01a1237bc02deae4957ceecd29639e16037759d`退出0：治理与差异诊断为空，Ruff/格式/mypy通过，**117 passed、20 warnings、130.27秒**；警告未屏蔽、锁未更新。
- 新目录demo、validate、replay均实际退出0；回放account/orders/factors/signals/target五项全部true。证据目录为`artifacts/readiness-audit-20260913`，运行时禁网启动器、日志及独立探针均保留；完整前缀/命令见审计报告。
- 独立探针退出**1**，如实保留三个需求不满足项：F01未来行业主表使当前超限风险判定从拒绝变允许；F02空目标而实际有持仓时监控仅报HEALTHY；F03保持原风险参数的300美元/1000美元股价目标由应需碎股0.0135变0。前两项为已复现业务边界缺陷，第三项为新的碎股要求缺口，非原整股SPEC回归失败。本轮仅审计，尚未修复。
- F01限`risk.assess_order`公共入口实测；`plan_orders`同类路径只静态确认，标准demo使用已过滤主表，没有本次标准demo触发泄漏的证据。两名独立复核者核对了反例输入、手算与日志。
- 新增[准备审计](../../docs/audit/readiness.md)、[上线阻塞](../../docs/audit/launch-blockers.md)、[8个后续PR建议](../../docs/audit/next-prs.md)、[免费Paper路线ADR](../../docs/adr/free-paper-route.md)。官方权限/规则已重新核实，账户级权限、资金/费用、远端CI、持续Paper与完整灾恢仍未验证；本次未单独运行research CLI，研究/通用备份已有测试包含在117项中。

结论：固定合成离线链路可运行；当前Alpaca只读适配未实现，单次/持续Paper尚不具备条件，有人监督300美元实盘与无人值守实盘均不可上线。最小下一工程步是先修F01/F02，然后按依赖验收隔离适配、数据和碎股；不重复开发已有意图/去重/恢复基础，不因免费路线自动放弃限价保护。首轮“无剩余阻塞”仅是当时离线交付记录，不能覆盖本次新发现。

## 首轮离线交付记录（历史）

首轮离线工程交付完成：共同契约、完整业务链路、治理工具、演示/校验、确定性回放、双因子共同样本研究及两库备份/恢复均已验证。**完整本地质量检查退出码 0，pytest 117 passed、20 条第三方弃用警告，131.64 秒。** 本轮离线实现没有剩余阻塞；当前验收对应的源码、工具与配置指纹已留存。首轮验收时曾为本地未提交工作树，运行清单记录了当时 dirty 状态及自有源码哈希；当前提交状态以上述本次审计为准。

## 已实现的能力

- Python 3.12、uv 锁定依赖；Linux/POSIX 文件锁；安装后的业务命令使用离线模式。
- 固定合成样本→历史时点/质量/证券身份→动量与低波动→共同横截面评分→市场状态观察→约束目标→风控→持久化意图→独立 FakeBroker→事件/公司行动账务→对账→解释报告。
- 六个 CLI：`demo`、`validate`、`replay`、`research`、`report`、`check`。回放按 intent/order/event/action 的真实交错 journal 重建，报告重生只读。
- 来源记录显式质量必填；UTC、稳定身份、单位、版本和内容封印；当前模型 Schema 与行情/候选/订单六个正反样例。
- 逐语句中文说明、函数类型/docstring、核心依赖矩阵、需求路径、Schema/样例漂移与敏感差异检查；CI 定义和五类 AI 维护提示词。
- 显式契约导出与 SQLite 一致性备份/校验/恢复到新路径工具；不覆盖批准快照，不删除未知旧 Schema，不进行生产迁移。

## 实际验证记录

| 阶段与实际命令 | 已观察结果 | 限制 |
|---|---|---|
| `uv sync --group dev` | Python 3.12.3、uv 0.12.5；实际解析 30 个包、安装 29 个包并生成 uv.lock。 | 这一步需要可用包源；之后的固定样本运行不联网。 |
| `uv run --locked pytest tests/test_governance.py -q` | 维护工具完成后，24 项治理/契约样例/备份测试通过。 | 局部验证，不代表全部项目通过。 |
| `uv run --locked ruff check --fix tools tests/test_governance.py`；`uv run --locked mypy tools tests/test_governance.py` | 当时工具与治理测试的 Ruff 和严格类型检查通过。 | 随后以全量 check 为最终依据。 |
| `uv run --locked python -m tools.export_contracts --write`；`uv run --offline --locked python -m tools.export_contracts --check` | 最新权威 Schema 及六个正反样例生成、只读比较和真实模型接受/拒绝检查通过。 | 更新的是可再生成源码契约文件，不是批准运行快照。 |
| `uv run --locked pytest tests/test_execution.py tests/test_ledger.py -q` | 独立复跑 29 项通过，4 条第三方弃用警告。 | 仅执行/账务集合；警告未屏蔽，依赖未自动升级。 |
| 初次离线集成演示与回放 | 根代理报告实际成功；账户、订单与重算链路进入后续全量验证。 | 早期临时产物不是最终正式交付目录。 |
| 早期研究实验 | 实际产生 4,800 observations、5 folds。 | 旧口径包含较早仅低波动样本；已保留原实验，**不作为最终双因子共同样本验收**。应用现先筛两因子均有效的 253 价格预热后样本，再按 12/3/3 月和末 6 月保留期评估；覆盖率分母未缩小。 |
| 第一次统一 `quant-core check`（[日志](../../artifacts/checks/check-01.log)） | Ruff 有 1 处 I001 导入排序问题；格式检查、mypy 通过；pytest **1 failed、116 passed、20 条第三方弃用警告**，耗时 129.85 秒。 | 唯一失败为报告重生的全文相等断言；本轮综合检查未通过。 |
| 第二次统一 `quant-core check`（[日志](../../artifacts/checks/check-02.log)） | **退出码 0**：governance_errors=[]；Ruff 通过；41 files already formatted；mypy 检查 40 个源文件无问题；pytest **117 passed、20 条第三方弃用警告，131.64 秒**。 | 本地源码质量验收通过；不等同远端 CI、正式引擎或实盘验收。 |
| 最终补充只读检查 | `git diff --check` 与 `uv run --offline --locked python -m tools.export_contracts --check` 均通过；正式 replay 再次 validate 成功，CLI report 重生后 cmp 字节一致，正式清单 source_hash 与当前源码仍一致。 | 不包含远端 CI 或发布。 |
| 带 HEAD 基线的独立只读治理检查 | `run_checks` 返回 0 个错误；`sensitive_diff_report` 报告 36 条初始规则/风险/依赖复核项。 | 用户已授权初始工程建设，各负责人执行独立交叉复核；差异报告不是远端人工审批制度。 |

## 已发现问题与修正依据

- 早期治理测试因 `tools` 未在导入路径而收集失败；通过明确仓库测试路径修复。适配器尚未落盘时的局部 mypy 导入错误，在真实模块及类型标记完成后解决。
- 独立固定反例发现的错误单位、账户/数值/订单契约缺口、盘后成交、未来公司行动提前入账、券商 ID 改绑、状态不变量、挂买单限价低估、手续费净值边界、主表资格/报价身份和研究首日错位均已交文件负责人修复并加入回归。
- 第一次统一检查的报告失败来自 `regime.evidence` 字典插入顺序在 JSON 保存时被排序，重生时字符串展示顺序不同。报告现在对 evidence/exclusions 使用 `json.dumps(sort_keys=True)` 固定键顺序；**保留原来的报告全文相等断言**，未降低预期、删除测试或放松风险。
- `test_application` 的 I001 通过整理导入修复。现金、持仓、费用、订单以及当前双因子共同样本研究测试在首次综合 pytest 的其余通过项目中；第二次完整检查现已通过，原全文相等断言得到保留。

## 正式产物与实际命令

以下均已实际成功，采用 `configs/demo.toml`：

```bash
uv run --offline --locked quant-core demo --config configs/demo.toml --output artifacts/demo
uv run --offline --locked quant-core validate --run-dir artifacts/demo
uv run --offline --locked quant-core replay --run-dir artifacts/demo --output artifacts/replay
uv run --offline --locked quant-core research --run-dir artifacts/demo
uv run --offline --locked python -m tools.backup_sqlite backup --source artifacts/demo/internal.sqlite --output artifacts/backups/demo-v1/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite backup --source artifacts/demo/broker.sqlite --output artifacts/backups/demo-v1/broker.sqlite
uv run --offline --locked python -m tools.backup_sqlite restore --source artifacts/backups/demo-v1/internal.sqlite --output artifacts/restored/demo-v1/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite restore --source artifacts/backups/demo-v1/broker.sqlite --output artifacts/restored/demo-v1/broker.sqlite
```

- [正式解释报告](../../artifacts/demo/report.md)：20 个实际持仓，20 笔订单全部 FILLED，现金 **11,380.072035 USD**，累计费用 **20 USD**，独立对账一致。报告内各目标股数与实际股数一致；本次结构化监控为 HEALTHY。
- [回放核对](../../artifacts/replay/replay-verification.json)：account、orders、factors、signals、target 比较全部为 true；回放内部账户由初态和交错 journal 重建。
- 两个数据库分别备份到 `artifacts/backups/demo-v1`，恢复到 `artifacts/restored/demo-v1`；完整性均为 ok，源/备份/恢复的逻辑哈希分别一致。SQLite backup API 可以改变物理文件布局，不以字节哈希必须相等代替逻辑一致性验证。
- [正式共同样本研究](../../artifacts/demo/research/experiment-0001/summary.json)退出码 0：**3,600 observations、2 folds**；目录为 `artifacts/demo/research/experiment-0001`，完整日志为 [research-final.log](../../artifacts/checks/research-final.log)。使用两因子均有效的 253 价格预热后样本，保留 12/3/3 月滚动与末 6 月 holdout，覆盖率分母仍为全部原始候选。早期 4,800/5 只作为保留的修正前实验，不是当前验收结果。

`artifacts/` 被 Git 忽略，当前工作区保留这些产物，新的检出需要按 README 重新生成。已有产物不可覆盖，重新验证应选择新运行/备份名称。

## 已知限制与阻塞

合成数据和 FakeBroker 只证明工程链路，不证明收益或真实成交表现。正式引擎、授权真实数据回测、完整成本后策略业绩、持续模拟/影子交易及实盘尚未验收；market-intel 仅有候选契约。真实告警通知、值守人、独立发布审批与 GitHub 远端分支保护未配置，远端 CI 尚未实际运行；本地规则和 CI 文件不代表不可绕过的授权机制。

当前第三方弃用警告与 exchange_calendars/NumPy timedelta 兼容性有关，未据此改锁文件或屏蔽警告。生产迁移、跨平台运行和多进程事务存储尚未验收。资金、账户、授权与发布阻塞项见 ASSUMPTIONS.md。

## 最小下一步

先按 [README](../../README.md) 阅读并在新的输出目录运行示例；新增因子按 [扩展手册](../../docs/runbooks/extensions.md) 补齐公式、版本、手算样本、时点与回归测试。下一工程阶段是取得正式数据授权并执行 [引擎验收 ADR](../../docs/adr/0001-offline-engine.md)，不属于修复本轮遗留问题。真实账户、实盘及远端发布仍需各自明确授权和验收。

## 原 CHANGELOG 全文

# 变更记录

## Alpaca Paper真实提交撤单验收（2026-09-20）

新增显式受限的休市订单测试，复用MA目标、共同风险和唯一执行服务；完成真实AAPL一股336.13美元限价的提交、远端ID查询、仅本单撤销及新进程核对。最终CANCELED/零成交，现金持仓一致且无重复日志；实际成交仍待验收，不用撤单代替成交标准。

修复时区规范、微小远端时钟领先、POST前明确拒绝及订单短页读取，补独立边界/故障回归。严格定向处理已证实未发送的旧意图，保留原失败与历史账本。展示增加远端完整ID及独立队列验收状态，README保留uv/Conda启动说明，契约、Schema、需求映射和操作手册同步。最终uv/Conda完整check各439项通过，36项既有兼容警告未屏蔽。具体版本、真实订单与独立复核见[审计记录](../../docs/audit/paper-queue.md)。

## MA5/MA20真实策略计划与双环境展示（2026-09-20）

新增独立MA趋势策略、SIP仅拆股价格与公司行动输入、普通股身份绑定、未知行业保守风险和最多3个可行整股目标；原双因子公式及风险参数保留。扩展可空总回报/行业契约并兼容旧版本哈希，复用唯一执行服务、观察、撤单与恢复；展示按策略显示指标、评分、排除原因、目标及执行事实。同步Schema、配置、需求映射与中文说明，README补充uv/Conda打开方式。

uv/Conda完整check各369 passed/26 warnings，两套原demo/validate/replay通过，五项回放及10份跨环境业务JSON一致；独立复核与两套浏览器入口验证完成。真实只读后生成AAPL 1股、GOOGL 1股、XOM 2股目标，参考收盘估值1012.75 USD；当前休市、尚未提交订单，成交、成交后核对及真实重启仍待验收。详见[MA接入记录](../../docs/audit/ma-paper.md)。用户已授权全部验收通过后按 `feat: 中文说明` 提交并推送，当前条件未满足，未执行Git提交或推送。

## 本地只读展示窗口（2026-09-20）

新增解耦的静态页面与标准库本地服务器，展示保存的真实Paper账户、独立策略预算、10只候选行情、因子/目标、订单和账户核对。只绑定回环地址，无SDK、凭据或交易接口；明确区分离线样本、只读核验及尚未完成的阶段。独立24项测试覆盖状态真实性、文件只读和HTTP边界；浏览器导航/刷新与窄屏时间显示已验证。补查公司行动及普通股公开证据，行业与严格总回报仍阻塞真实策略执行。新增后uv/Conda完整check各254 passed/21 warnings，两套新demo/validate/replay均通过。完整新增验证见[接入记录](../../docs/audit/alpaca-paper.md)。

## Alpaca Paper 接入与真实只读（2026-09-19）

新增官方alpaca-py0.44.0适配器、SIP原始历史/日历和严格补充资料入口，四个Paper命令沿用原因子、评分、组合、风险与唯一执行服务。凭据显式本地读取、Paper端点/账户核验，独立策略预算、追加证据和真实FILL恢复；默认离线不变。SDK及8项传递依赖真实锁定，共同requirements重新导出，原包版本保留。

修复行业时点F01、目标外持仓F02及真实网络查询期间成交的时钟判定；补部分成交/撤单/超时/变价不重发/资金差异等独立回归。公开时间未知允许None，公共风险配置分离且Paper禁止改变原风险默认值，Schema和需求映射同步。中文说明、README、操作手册与审计记录更新。

经用户分阶段授权完成真实只读：现金100000 USD、空仓无订单、10只各429条SIP原始日线；预算10000 USD与购买力分开。行业及严格总回报资料未齐备，真实策略未执行，未提交Paper订单或产生成交；完整闭环明确未验收。uv及新Conda专用环境check各230 passed/21 warnings；两边原demo/validate/replay均通过，五项回放true、10份业务JSON一致。详细验证及未实现边界见[接入记录](../../docs/audit/alpaca-paper.md)。未提交、推送、部署或购买数据。

## 第三阶段：uv 与 Conda 共用依赖（2026-09-19）

保留原uv路线，验证Conda创建Python3.12环境、环境内pip按uv.lock导出的requirements安装项目库，并可编辑安装同一源码。README末尾明确各一条日常更新命令，把首次安装、依赖更新和开发检查分开；普通新增/版本更新不必重建，删除依赖或调整Python/基础包时采用新环境验证。快速开始、维护手册、根规则入口限定、导航和QC-013/015映射同步；没有新增第二套手写依赖或Conda CI。

原uv检查133 passed/20 warnings/55.04秒，新Conda检查133 passed/20 warnings/58.58秒；两条路线demo/validate/replay均成功，回放五项一致，跨环境10份业务JSON精确一致。同一Conda环境重复单命令更新约0.8秒且不重装，requirements两次导出内容与起点相同。未改业务源码、测试、配置、依赖声明/锁、Schema、检查器或CI。

工具安装与解释器来源、SQLite差异、隔离构建依赖和实际失败见[兼容验收记录](../../docs/audit/conda-compatibility.md)。官方Miniforge固定版本安装在忽略目录，用户Conda登记文件创建如实记录；shell/全局配置和其他项目环境未改。未宣称其他平台、实际依赖升级删除或生产运行已验收，未提交或推送。

## macOS 开发环境与依赖清单（2026-09-19）

确认macOS26.5.1/Apple Silicon/Python3.12.14组合可用于当前仓库的日常离线开发，README声明macOS为主要本地开发环境，保留Linux CI及明确验证边界。缺少持久uv时已使用官方安装器安装0.12.5至用户目录，不修改shell配置。保留现有 `.venv`，使用原锁文件与已有解释器/缓存重建独立环境，不升级项目依赖。

新增由uv.lock导出的 `requirements.txt`，包含运行、开发及传递依赖的精确版本、条件和哈希，重复导出一致；uv仍为主工作流，conda及pip清单安装未验证。快速开始补充uv安装和当前终端PATH用法，维护手册集中导出方法，新增[macOS验证记录](../../docs/audit/macos-development.md)并同步导航与项目状态。

独立环境check退出0：**133 passed、20 warnings、54.54秒**；demo/validate/replay/research/report及重复demo均退出0，回放五项一致、报告重生及12类重复业务结果一致，研究3,600样本/2分组。初始安装器下载DNS失败与重试成功分别留存，第三方弃用和沙箱CPU信息诊断未屏蔽。源码、测试、配置、锁、元数据、Schema、检查器和CI未改；其他平台、完整新机初始化、持续交易和生产未验收。原始证据在新的 `artifacts/macos-validation-20260919/`，旧记录不改写。

随后按用户新增授权创建用户 `~/.zshrc`，将 `~/.local/bin` 持久加入交互式终端PATH；新zsh会话可直接调用uv0.12.5，重复加载不重复添加。原有Homebrew配置保留，系统与其他项目未修改；快速开始补充用户主动配置PATH的可选步骤，与安装器不改shell的行为区分。

## 第一阶段文档整理（2026-09-19）

复用 `docs/AGENTS.md` 集中文档表达原则，根规则和维护提示词只增加必要引用，不新增writing-guide.md或文字风格检查器。重整README与快速开始，新增四组文档导航；先解释用途、当前行为与运行结果，再引导阅读实现。明确合成行情、本地FakeBroker未接Alpaca、一次周调仓演示、已记录平台验证范围和生成产物边界，首次运行与开发验收分开。旧审计、验证和变更正文保留。

以本阶段起始工作区快照核对范围，源码、注释、测试、配置、锁文件、CI、Schema及元数据未改。独立读者走读发现的锚点与缩进问题已关闭；相对链接、标题锚点、CLI参数及 `git diff --check` 通过。沿用现有环境并禁用uv自动同步，未执行安装/升级；完整check退出0（**133 passed、20 warnings、52.80秒**），新demo/validate/replay/report均成功，五项回放一致、报告重生字节一致。警告、未验证项与完整命令见PROJECT_STATE，证据位于 `artifacts/docs-phase1-20260919/`。

本阶段只交付文档整理，未提交、推送、发布，也未启动后续开发或环境调整。

## QC-014 — 全仓核验与局部补齐（2026-09-19）

按用户确认计划核验40个自有Python文件，仅在16文件补齐已确认的局部阅读缺口，其余保留。修正执行时资格主表口径、FakeBroker行动去向及新单处理后超时、事务与只读查询边界、回放异常范围；补中文统计口径、覆盖率分母、报告输入前提和契约字段说明；更正测试场景与术语。治理测试仅修改单独获授权的一句docstring，不改规则、输入或断言。

以开工工作区115文件快照验证40个Python AST全部一致；导出类docstring、Schema/样例、规范、CI、配置和依赖锁保持不变。跨作者独立语义复核问题全部关闭；新增 [全仓审查记录](../../docs/audit/readability-compliance.md) 与QC-014映射，前两轮验收正文不改写。

完整check退出0：**133 passed、20 warnings、54.98秒**；契约只读检查及新demo/validate/replay退出0，五项回放一致。证据位于 `artifacts/readability-compliance-20260919/`，新清单记录实际源码指纹及dirty=true。未升级依赖，警告和自检过程限制如实记录；既有业务缺陷未修，真实账户、远端CI与持续交易未验收，未发布或部署。

## QC-014 — 第二轮局部上下文与调用衔接（2026-09-19）

以第一轮未提交工作区为起点保存114文件快照及摘要，未重做治理迁移。增强根唯一规范中的关键变量来源/结构/单位、事实与目标/预算口径、重要调用的输入输出和去向、库用法的业务效果及状态出口；允许关键位置简短重述，语法简单不等于业务含义无需说明。同步提示词、SPEC、维护手册与导读，新增 [第二轮记录](../../docs/audit/local-context-readability.md)，历史迁移记录原字节保留。

先补application/portfolio/signals/execution/sqlite_store与对应测试样板，再检查其余源码、测试与工具；已有充分解释保留。改善目标缺口与风险占用、净值/现金/规划预算、原值/评分、已成交/剩余量、嵌套索引、模型复制/验证和事务/锁等阅读障碍。跨作者独立AI复核更正不精确说明，未混入业务修复。

40个自有Python相对本轮工作区快照AST全部一致，无治理例外；治理检查器、治理测试、CI、配置、依赖锁、Schema/样例字节未变。完整check退出0：**133 passed、20 warnings、52.58秒**；契约只读检查及新demo/validate/replay均退出0，五项回放一致。证据 `artifacts/readability-context-20260919/` 区分本轮diff与Git累计diff，清单诚实记录新源码指纹及dirty=true。没有升级依赖，警告保留；远端与持续交易仍未验证，未连接真实账户或执行外部交易/发布/部署。

## QC-014 — 按业务语义分层解释（2026-09-19）

按用户明确授权，把逐语句注释规则改为根 AGENTS.md 唯一主规范：模块/类职责、函数调用约定、正文业务阶段和关键公式/时点/资金/订单边界分层说明。同步子规则、SPEC/需求映射、所有维护模板、维护手册、治理 ADR 与业务导读；先完成 factors/execution/test_factors 样板，再整理自有 Python。历史逐句验收不改写，新增 [迁移记录](../../docs/audit/comment-semantics-migration.md) 保存前后示例、逐项规则迁移及真实验证。

治理仅取消语句邻接与分支单独中文说明门槛，保留真正中文 docstring、函数类型及全部无关治理保护。原五类专门逐句测试逐项迁移，新增模块/类/函数 docstring 与五类参数缺类型反例；业务测试输入、断言、容差保持不变。独立 AI 交叉复核完成，纠正超过实际实现的说明，未修 F01/F02 或新增碎股功能。

业务及其他非迁移 Python 共38文件 AST 与基线 `01205ca` 一致；检查器/治理测试之外无执行结构变化。契约生成文件/六个样例只读检查通过且无漂移，配置、CI、权限、依赖及锁文件未变。完整 check 两次退出0，最终 **133 passed、20 warnings、57.04秒**；新目录 demo/validate/replay 退出0，五项回放一致。最后两条测试注释修正后补做 AST、静态治理与格式核验。证据目录 `artifacts/comment-semantics-20260919/`，清单诚实记录新源码哈希及 dirty=true。

初始缺 uv、受限 DNS、离线缓存缺失和 import 空行检查失败均记录；使用临时 uv 0.12.5 与锁定依赖恢复验证，未升级依赖。第三方弃用及 macOS PyArrow sysctl 警告保留；远端 CI、真实账户和持续交易未验证，未执行外部交易、发布、部署或推送。

## 上线准备审计（2026-09-13，仅文档与证据）

按用户授权审计300美元/零付费API路线，新增readiness、launch-blockers、next-prs及free-paper-route四份文档，更新PROJECT_STATE的当前Git与验收事实。R01–R16对应QC-001–QC-017；未改变业务逻辑、公式、公共接口、配置、依赖锁、CI、根规则或测试断言，因此Schema/配置说明/原需求映射保持不变，关联范围记于审计矩阵。

实际禁网统一check退出0：117 passed、20 warnings、130.27秒；新demo/validate/replay退出0且五项回放一致。独立手算探针退出1，复现未来行业风控泄漏与目标外持仓监控漏报，并确认300美元碎股需求未满足；未修改代码掩盖问题，后续修复尚未执行。输入、公式与日志完成独立交叉复核。证据位于artifacts/readiness-audit-20260913，完整命令及范围见docs/audit/readiness.md。

当前只能确认合成离线链路；Alpaca真实适配/账户权限、持续Paper、小额实盘均未验收。官方路线建议复用现有核心、先保留限价边界，不引入多个正式引擎；本轮未联网账户、发单、购买、部署或提交/推送。

## 0.1.0 — 初始离线工程（2026-09-13，交付完成）

建立 quant-core 模块化单体、版本化共同契约、固定样本全链路和六个 CLI；加入分层文档、因子/策略说明、逐语句中文注释治理、需求追踪、AI 维护提示词及离线 CI 定义。FakeBroker、合成研究和本地检查不构成收益证明或实盘批准。

- QC-001/002/010/016：显式必填质量、UTC/身份/单位/版本与封印，严格订单/账户/评分，候选信息保持隔离；Schema 与六个正反样例由唯一运行类型维护。
- QC-004/005/006/009/011/017：固定公式与共同横截面，当前主表和报价身份核对，真实成交换手、全部挂买单限价与费用后净值，固定下一可交易标签和双因子共同预热后的完整研究窗口。通过扩大合成历史保留 253 价格预热及 12/3/3 月、末 6 月边界，未缩短窗口换通过。
- QC-008/010/012：持久化意图、账户唯一执行锁、双键事件幂等、交错 journal、公司行动时点、独立券商/内部账务和恢复；提供只读源、新目标的 SQLite 备份与恢复工具。
- QC-014/015：AST/tokenize 注释覆盖、函数类型与 docstring、核心依赖矩阵、需求/Schema/样例检查、敏感 Git 差异；明确机械检查无法替代语义复核和独立授权。

### 验证进展与保留失败

维护工具阶段 24 项测试与对应 Ruff/mypy 通过；独立执行/账务复验 29 项通过并保留 4 条第三方警告。早期研究真实得到 4,800 observations、5 folds，但包含低波动提前样本；保留该实验，最终以修正后的双因子共同样本结果另行验收。

第一次统一检查：1 处 I001；格式/mypy 通过；pytest 1 failed、116 passed、20 条第三方弃用警告，129.85 秒。报告重生全文断言揭示结构化字典经 JSON 保存后键顺序不同，修复为 evidence/exclusions 确定排序；保留完整断言，并修正测试导入排序。第二次完整检查已通过（退出码 0）：governance_errors=[]，Ruff 通过，41 文件格式正确，mypy 检查 40 个源文件无问题，pytest **117 passed、20 条第三方弃用警告、131.64 秒**。两轮原始日志保留在 `artifacts/checks/`，没有屏蔽警告或修改生产依赖。

HEAD 基线只读治理检查无错误，36 条初始敏感变更已作为独立复核证据列出；初建由用户明确授权，但远端 CI/分支保护、人工发布审批、授权真实数据、LEAN、券商与实盘仍未验收或启用。正式演示及校验已成功：20 个持仓、20 笔 FILLED 订单、现金 11,380.072035 USD、费用 20 USD，独立对账一致。`artifacts/replay/replay-verification.json` 的账户/订单/因子/信号/目标五项全部相同。内外部两库已备份并恢复到新目录，完整性与各自逻辑哈希一致。正式共同样本研究已成功：**3,600 observations、2 folds**，输出位于 `artifacts/demo/research/experiment-0001`，日志为 `artifacts/checks/research-final.log`。早期 4,800/5 保留为修正前集成实验，不作最终验收。最终 `git diff --check` 和离线契约生成文件/样例检查均通过。

本轮离线实现无剩余阻塞。使用入口为 README，新增因子遵循扩展手册；下一阶段是正式数据授权和引擎验收。远端 CI/分支保护、真实数据/LEAN、券商模拟与实盘仍按既定范围保持未验收或未启用。

完整状态、实际命令及未验收事项以 PROJECT_STATE.md 为准。后续行为变更须记录需求 ID、前后行为、关联测试/文档和回滚影响；禁止删除测试、降低断言或放松风控获得通过。

## 长期规则中移出的历史任务范围

以下是整理前根规则的任务状态原文，只保存当时授权和阶段，不作为新任务授权。长期规范仍见[根规则](../../AGENTS.md)。

### 原已授权的本轮工作

用户已经授权建立完整离线工程、公共契约、业务链路、测试、中文注释、分层文档、提示词、本地检查与 CI 配置。初次创建这些规则及必要依赖属于本轮授权，无需重复询问。可以在仓库内实现、测试与修复；不得接入真实账户、购买数据、发布内容、部署或修改远端权限。FakeBroker 仅用于离线工程验证。


### 原标准维护流程中的阶段限制

第一轮 QC-014 规范与配套机械规则迁移已完成。本轮获授权的是局部上下文、变量含义和调用衔接的第二轮增强，仅修改注释、docstring 及相关说明；不改变业务逻辑、签名、数据结构、变量名、公式、配置、异常行为、测试输入或业务断言，不重构代码。保留当前治理检查器及治理测试，不新增机械注释门槛。其余业务、风控、权限、依赖和工程约束继续有效；阅读发现的业务缺陷单独记录和处理。
