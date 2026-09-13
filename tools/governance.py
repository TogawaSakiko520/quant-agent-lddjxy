"""检查注释覆盖、文档引用和核心依赖；不推断注释语义或人工授权。"""

# AST 提供语句及函数边界，避免用纯文本误判多行 Python。
import ast

# 内存文本流使 tokenize 无须生成临时源码文件。
import io

# JSON 用于读取唯一的机器可读需求映射。
import json

# 正则仅识别中文字符与明确的分支关键字。
import re

# Git 差异是治理工具的输入，业务核心不会调用子进程。
import subprocess

# tokenize 区分真实注释和字符串里的井号。
import tokenize

# 路径参数由调用者显式提供，不猜测工作目录。
from pathlib import Path

# 这些文档分别承担安装、规范、架构、假设、状态及历史职责。
REQUIRED_DOCUMENTS: tuple[str, ...] = (
    "README.md",
    "AGENTS.md",
    "SPEC.md",
    "ARCHITECTURE.md",
    "ASSUMPTIONS.md",
    "PROJECT_STATE.md",
    "CHANGELOG.md",
    "docs/requirements.json",
    "docs/runbooks/ai-maintenance.md",
    "docs/code_walkthrough/business-chain.md",
    "docs/factors/momentum.md",
    "docs/factors/low-volatility.md",
    "docs/configuration.md",
    "prompts/handoff.md",
    "prompts/implement.md",
    "prompts/review.md",
)
# 包含业务代码、测试和治理工具；不存在隐式的生成代码排除名单。
SOURCE_DIRECTORIES: tuple[str, ...] = ("src", "tests", "tools")
# 外部副作用必须移到适配器或应用入口，不能混入核心计算。
FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
    {
        "requests",
        "httpx",
        "urllib",
        "socket",
        "aiohttp",
        "openai",
        "anthropic",
        "subprocess",
        "alpaca",
        "ib_insync",
        "ibapi",
        "yfinance",
    }
)
# 应用装配和供应商实现都不属于纯业务核心的依赖。
BOUNDARY_MODULES: frozenset[str] = frozenset({"adapters", "application", "cli", "tools"})
# 允许的同层协作必须明确登记，不能因为都在核心就任意相互依赖。
CORE_DEPENDENCIES: dict[str, frozenset[str]] = {
    "contracts": frozenset(),
    "data": frozenset({"contracts"}),
    "universe": frozenset({"contracts"}),
    "factors": frozenset({"contracts", "universe"}),
    "signals": frozenset({"contracts", "factors", "universe"}),
    "research": frozenset({"contracts"}),
    "regime": frozenset({"contracts"}),
    "portfolio": frozenset({"contracts"}),
    "risk": frozenset({"contracts", "portfolio"}),
    "execution": frozenset({"contracts", "risk"}),
    "ledger": frozenset({"contracts"}),
    "monitoring": frozenset({"contracts"}),
    "reporting": frozenset({"contracts", "monitoring"}),
}
# 首轮没有第三方或自动生成的 Python 文件需要跳过。
EXCLUDED_PYTHON_FILES: frozenset[str] = frozenset()


def contains_chinese(text: str) -> bool:
    """判断说明是否包含中文；只保证文字存在，不保证业务语义正确。"""
    # 中文字符存在性是可机械验证的最低覆盖要求。
    return re.search(r"[\u3400-\u9fff]", text) is not None


def _comment_lines(source: str) -> dict[int, tuple[str, bool]]:
    """返回注释文字和是否独占该行的映射；输入为源码，不执行或修改它。"""
    # 保留源码行前缀，防止上一条语句的行内注释被下一条冒用。
    lines = source.splitlines()
    # tokenize 只收集 COMMENT token，避免字符串伪装成注释。
    return {
        token.start[0]: (token.string, not lines[token.start[0] - 1][: token.start[1]].strip())
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }


def _has_comment(comments: dict[int, tuple[str, bool]], start: int, end: int) -> bool:
    """检查该语句的首末行注释或前一行独立注释；返回布尔值，不产生副作用。"""
    # 前置说明必须独占上一行，不能复用前一条语句的行内解释。
    previous, standalone = comments.get(start - 1, ("", False))
    # 不跨空白行寻找说明，也不让别条语句的注释覆盖本条。
    return (standalone and contains_chinese(previous)) or any(
        contains_chinese(comments.get(line, ("", False))[0]) for line in (start, end)
    )


def check_python_comments(path: Path) -> list[str]:
    """检查单个文件的中文说明和函数类型；返回问题列表且不修改源码。"""
    # 读取实际源码，检查结果不会依赖导入模块带来的副作用。
    source = path.read_text(encoding="utf-8")
    # 语法错误转换为可汇总诊断，而不是中止其他文件的检查。
    try:
        # AST 用于定位语句及识别真正的 docstring。
        tree = ast.parse(source, filename=str(path))
        # 注释 token 与 AST 组合检查解释的相邻关系。
        comments = _comment_lines(source)
    # 词法或语法损坏时给出该文件的明确错误。
    except (SyntaxError, tokenize.TokenError) as error:
        # 无法解析的文件不能宣称通过注释检查。
        return [f"{path}: Python 解析失败：{error}"]
    # 收集所有诊断，保证一次运行能看到完整问题。
    errors: list[str] = []
    # 记录 docstring 语句，避免要求给文字说明再加一条注释。
    docstrings: set[int] = set()
    # 模块、类和函数都必须说明职责或行为。
    for node in ast.walk(tree):
        # 限定具有独立文档职责的定义节点。
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            # clean=False 保留原文，检查不改写源码说明。
            docstring = ast.get_docstring(node, clean=False)
            # 缺失中文说明不能由函数名或类型代替。
            if not docstring or not contains_chinese(docstring):
                # 模块无 lineno 属性，默认在首行报告。
                errors.append(f"{path}:{getattr(node, 'lineno', 1)}: 缺少中文 docstring")
            # AST 的第一个字符串表达式才是定义的 docstring。
            if docstring is not None and node.body:
                # 保存对象身份，避免豁免同一行上的其他表达式。
                docstrings.add(id(node.body[0]))
        # 函数签名是共同契约的一部分，必须提供完整类型信息。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 包括仅限位置参数与仅限关键字参数。
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            # 可变位置参数也有元素类型约束。
            if node.args.vararg is not None:
                # 加入统一参数检查，避免 *args 绕过要求。
                arguments.append(node.args.vararg)
            # 可变关键字参数也需要声明值类型。
            if node.args.kwarg is not None:
                # 加入统一参数检查，避免 **kwargs 绕过要求。
                arguments.append(node.args.kwarg)
            # self 和 cls 的类型由所属类确定，其余参数须显式标注。
            for argument in arguments:
                # 缺失类型只报告位置，不猜测合适的注解。
                if argument.arg not in {"self", "cls"} and argument.annotation is None:
                    # 接口缺口应由实现者根据实际数据契约修复。
                    errors.append(f"{path}:{node.lineno}: 参数 {argument.arg} 缺少类型")
            # 无返回值函数也应标注 None。
            if node.returns is None:
                # 防止调用方凭文档猜测函数返回行为。
                errors.append(f"{path}:{node.lineno}: 函数 {node.name} 缺少返回类型")
    # 逐语句覆盖以 AST 为准，复合表达式不拆成多份教学代码。
    for node in ast.walk(tree):
        # 定义由中文 docstring 满足；真实 docstring 语句显式豁免。
        if (
            isinstance(node, ast.stmt)
            and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and id(node) not in docstrings
        ):
            # 复合语句末尾是其子语句，不能借用子语句的行内注释解释分支头。
            end_line = (
                node.lineno
                if isinstance(
                    node,
                    (
                        ast.If,
                        ast.For,
                        ast.AsyncFor,
                        ast.While,
                        ast.Try,
                        ast.TryStar,
                        ast.With,
                        ast.AsyncWith,
                        ast.Match,
                    ),
                )
                else (node.end_lineno or node.lineno)
            )
            # 相邻说明必须包含中文，字段声明与 import 也遵循同一规则。
            if not _has_comment(comments, node.lineno, end_line):
                # 用 AST 语句类型帮助实现者定位遗漏的分支或副作用。
                errors.append(f"{path}:{node.lineno}: {type(node).__name__} 缺少紧邻中文说明")
    # 一次词法遍历确定真实分支，避免对每个分支重复解析整个文件。
    branch_lines = {
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.NAME and token.string in {"else", "except", "finally"}
    }
    # else、except 和 finally 没有独立通用 ast.stmt，需检查实际关键字行。
    for line_number, line in enumerate(source.splitlines(), start=1):
        # 行首和 token 身份共同排除三元表达式与文档字符串中的伪分支。
        if line_number in branch_lines and re.match(r"^\s*(else\s*:|except\b|finally\s*:)", line):
            # 分支必须独立说明触发条件或异常处理目的。
            if not _has_comment(comments, line_number, line_number):
                # 普通代码行的说明不能替代分支本身的业务含义。
                errors.append(f"{path}:{line_number}: 分支缺少紧邻中文说明")
    # 调用方决定输出或失败策略，本函数不退出进程。
    return errors


def _project_dependencies(node: ast.Import | ast.ImportFrom) -> set[str]:
    """提取明确的项目模块依赖名称；输入是 AST，不执行导入也不改变源码。"""
    # 普通 import 只识别本项目包，外部库另走禁止列表。
    if isinstance(node, ast.Import):
        # 包根直接导入无法表达窄职责，因此显式命名为需审查依赖。
        return {
            name.name.split(".")[1] if "." in name.name else "__package__"
            for name in node.names
            if name.name == "quant_core" or name.name.startswith("quant_core.")
        }
    # from quant_core import module 需要从导入项提取模块名称。
    if node.module == "quant_core":
        # 多项导入分别接受依赖矩阵检查。
        return {name.name for name in node.names}
    # 完整模块路径的第二段就是核心职责模块。
    if node.module and node.module.startswith("quant_core."):
        # 同一模块的多个符号共享一个依赖。
        return {node.module.split(".")[1]}
    # 本包内的相对导入按第一段确定职责。
    if node.level:
        # 空 module 对应 from . import module。
        return {node.module.split(".")[0]} if node.module else {name.name for name in node.names}
    # 标准库和第三方分析库不属于项目模块依赖矩阵。
    return set()


def check_architecture(path: Path, root: Path) -> list[str]:
    """检查业务核心的副作用依赖；适配器、CLI 和应用装配不受纯核规则限制。"""
    # 用仓库相对路径识别职责，避免工作目录改变检查范围。
    relative = path.relative_to(root).as_posix()
    # 只对约定的业务包进行核心依赖检查。
    if not relative.startswith("src/quant_core/"):
        # 测试与开发工具可显式使用子进程和文件系统。
        return []
    # 适配器和装配层负责承接外部副作用。
    if "/adapters/" in relative or path.stem in {"application", "cli", "__init__", "__main__"}:
        # 它们仍受注释、类型和测试约束。
        return []
    # 解析失败由源码检查负责，这里避免重复抛异常。
    try:
        # 不导入实际业务模块即可分析依赖。
        tree = ast.parse(path.read_text(encoding="utf-8"))
    # 不完整源码不能执行架构解析，但仍会在总检查中失败。
    except SyntaxError:
        # 将语法错误统一保留在注释检查输出中。
        return []
    # 逐项累积跨边界问题。
    errors: list[str] = []
    # 新核心模块必须先登记职责依赖，避免无规则的隐式例外。
    if path.stem not in CORE_DEPENDENCIES:
        # 不因新文件尚未进入矩阵就默认为允许任意协作。
        errors.append(f"{path}: 未登记核心模块的允许依赖")
    # 当前模块只接受已明确登记的协作职责。
    permitted = CORE_DEPENDENCIES.get(path.stem, frozenset())
    # 导入别名用于识别通过别名直接获取真实时间的调用。
    aliases: dict[str, str] = {}
    # 静态遍历所有节点，不受运行分支是否执行影响。
    for node in ast.walk(tree):
        # 核心模块之间也遵守显式依赖矩阵。
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # 所有明确导入逐个核对，不让聚合导入绕过职责划分。
            for dependency in sorted(_project_dependencies(node) - permitted):
                # 核心内部不合法依赖与网络越界同样是硬失败。
                errors.append(f"{path}:{node.lineno}: {path.stem} 未获允许依赖 {dependency}")
        # 普通 import 的根包决定外部依赖类型。
        if isinstance(node, ast.Import):
            # 一个 import 语句可能同时引入多个包。
            for name in node.names:
                # 保存实际导入名称，防止别名隐藏时钟调用。
                aliases[name.asname or name.name.split(".")[0]] = name.name
                # 供应商 SDK、网络和进程都不允许进入核心。
                if name.name.split(".")[0] in FORBIDDEN_IMPORTS or BOUNDARY_MODULES.intersection(
                    name.name.split(".")
                ):
                    # 指明违规依赖，便于移动到正确适配器。
                    errors.append(f"{path}:{node.lineno}: 核心禁止导入 {name.name}")
        # from 导入需要同时处理供应商包与项目内部职责。
        if isinstance(node, ast.ImportFrom):
            # 相对导入没有 module 时使用空串，不凭空构造路径。
            module = node.module or ""
            # 保存 from datetime import datetime 及别名等绑定。
            for name in node.names:
                # 完整名称供直接时间调用识别。
                aliases[name.asname or name.name] = f"{module}.{name.name}".strip(".")
            # 核心不得反向依赖装配层或供应商适配器。
            if (
                module.split(".")[0] in FORBIDDEN_IMPORTS
                or BOUNDARY_MODULES.intersection(module.split("."))
                or (not module and any(name.name in BOUNDARY_MODULES for name in node.names))
            ):
                # 依赖方向错误是硬失败，无静默自动排除。
                errors.append(f"{path}:{node.lineno}: 核心越界导入 {module}")
        # 实际时间读取必须通过 Clock 注入，历史解析不受影响。
        if isinstance(node, ast.Call):
            # unparse 只还原调用目标，不执行表达式。
            target = ast.unparse(node.func)
            # 别名仅替换首段，保留后续属性访问。
            first, _, rest = target.partition(".")
            # 将 dt.now 等调用还原为可判定的标准名称。
            resolved = aliases.get(first, first) + (f".{rest}" if rest else "")
            # datetime.now/utcnow/today 和 time 系列真实时钟都受约束。
            if resolved in {
                "datetime.datetime.now",
                "datetime.datetime.utcnow",
                "datetime.datetime.today",
                "datetime.date.today",
                "time.time",
                "time.time_ns",
                "time.monotonic",
                "time.perf_counter",
            }:
                # 提醒使用注入时钟而不是禁止时间类型本身。
                errors.append(f"{path}:{node.lineno}: 核心必须注入 Clock，不能调用 {resolved}")
    # 架构检查不推断运行时猴子补丁或动态 import 的实际行为。
    return errors


def check_documents(root: Path) -> list[str]:
    """验证职责文档和需求映射引用，避免只交付空目录或断开的追踪索引。"""
    # 完整性诊断和引用诊断共同返回，便于一次修复。
    errors: list[str] = []
    # 每个必需文档必须实际存在且包含内容。
    for relative in REQUIRED_DOCUMENTS:
        # 只接受仓库里的文件，不允许目录替代文档。
        path = root / relative
        # 空文件不构成已交付的职责文档。
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            # 缺失文件可以在一次运行中集中显示。
            errors.append(f"{relative}: 必需文档缺失或为空")
    # 映射不存在时已记录诊断，无需产生额外异常。
    mapping = root / "docs/requirements.json"
    # 只有真实存在的映射才继续解析。
    if not mapping.is_file():
        # 返回已收集的文档缺口。
        return errors
    # 非法 JSON 必须被显式报告。
    try:
        # 映射仅作为数据读取，不执行其中任何内容。
        requirements = json.loads(mapping.read_text(encoding="utf-8"))
    # 解析异常不能被吞掉后宣称追踪完整。
    except json.JSONDecodeError as error:
        # 保留 JSON 的位置诊断以缩短修复路径。
        return [*errors, f"docs/requirements.json: JSON 无效：{error}"]
    # 防止意外把对象或字符串当作需求列表遍历。
    if not isinstance(requirements, list) or not requirements:
        # 空映射不满足需求追踪能力。
        return [*errors, "docs/requirements.json: 必须为非空需求列表"]
    # 稳定 ID 不允许重复，避免多个行为共用一个审计入口。
    seen: set[str] = set()
    # 每条需求必须连接实现、测试与文档三种证据。
    for item in requirements:
        # 非对象条目无法表达完整追踪关系。
        if not isinstance(item, dict):
            # 记录后继续检查其余有效条目。
            errors.append("docs/requirements.json: 需求条目必须为对象")
            # 无对象字段可检查，跳过本条后续逻辑。
            continue
        # 非字符串 ID 也不应被自动转换为看似合法的编号。
        identifier = item.get("id")
        # 唯一且规范的 ID 可以在代码、文档和评审中稳定引用。
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"QC-\d{3}", identifier)
            or identifier in seen
        ):
            # 让重复 ID 和无效 ID 都成为明确失败。
            errors.append(f"docs/requirements.json: 无效或重复需求 ID {identifier!r}")
        # 合法字符串仍登记，确保重复检测准确。
        if isinstance(identifier, str):
            # 登记不会赋予需求人工批准状态。
            seen.add(identifier)
        # 三类路径引用都必须非空且存在。
        for field in ("implementation", "tests", "docs"):
            # 不使用默认空列表掩盖缺失字段。
            paths = item.get(field)
            # 列表缺失意味着需求尚未形成可追踪交付。
            if not isinstance(paths, list) or not paths:
                # 精确报告缺少哪类关联证据。
                errors.append(f"{identifier}: {field} 必须是非空路径列表")
                # 无合法路径列表时不执行逐路径检查。
                continue
            # 引用只允许仓库内的真实文件。
            for relative in paths:
                # 防止绝对路径或父目录引用逃逸到仓库之外。
                if (
                    not isinstance(relative, str)
                    or Path(relative).is_absolute()
                    or ".." in Path(relative).parts
                ):
                    # 路径安全不依赖文件当前是否存在。
                    errors.append(f"{identifier}: 非法引用 {relative!r}")
                # 合法相对路径还须对应一个实际文件。
                elif not (root / relative).is_file():
                    # 不接受空目录作为实现或测试证据。
                    errors.append(f"{identifier}: {field} 引用不存在：{relative}")
    # 引用存在不代表关联内容语义正确，需独立复核。
    return errors


def _sensitive_file_findings(path: str) -> list[str]:
    """按仓库相对路径返回独立复核类别；不判断具体断言强弱或风险方向。"""
    # 同一文件可能同时涉及测试规则和授权边界。
    findings: list[str] = []
    # 测试增删改全部需要语义审查，断言数量不是强度证明。
    if path.startswith("tests/"):
        # 独立固定样本与需求依据由复核者核对。
        findings.append(f"测试变更需复核：{path}")
    # 规则、CI、提示词和依赖控制开发及运行边界。
    if (
        Path(path).name == "AGENTS.md"
        or path.startswith((".github/", "prompts/", "tools/"))
        or path in {"uv.lock", "pyproject.toml"}
    ):
        # 报告本身不是批准，开发 AI 不能自签解除约束。
        findings.append(f"治理或依赖边界变更需独立授权复核：{path}")
    # 执行、风险、账务和配置可能影响资金约束。
    if path.startswith(
        ("configs/", "src/quant_core/risk", "src/quant_core/execution", "src/quant_core/ledger")
    ):
        # 数值变大不一定更安全，不能自动替代业务审查。
        findings.append(f"风险或资金行为变更需复核：{path}")
    # 返回所有适用分类，不进行任何外部状态变更。
    return findings


def sensitive_diff_report(root: Path, base: str | None = None) -> list[str]:
    """报告基线至工作树及未跟踪敏感文件；Git 失败显式返回，不写文件或授予批准。"""
    # 未提供比较基线时只报告检查边界，不猜测 Git 历史。
    if base is None:
        # 首次脚手架建设也必须诚实标注未执行差异审计。
        return ["未提供 --base：未执行敏感差异审计；需独立复核初始规则与风险参数。"]
    # 先把用户引用解析为提交，--end-of-options 防止引用被当成 Git 选项。
    revision = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    # 无效基线不能退化成一次假成功的空差异。
    if revision.returncode != 0:
        # 保留可诊断的 Git 错误，不执行任何修复操作。
        return [f"敏感差异审计失败：{revision.stderr.strip()}"]
    # NUL 分隔直接处理空格和特殊文件名，不解析带引号的补丁路径。
    changed = subprocess.run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--name-only",
            "--no-renames",
            "-z",
            revision.stdout.strip(),
            "--",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    # 未跟踪的新测试或规则不在 git diff 中，必须额外纳入。
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    # 任一 Git 查询失败都使差异证据不完整。
    if changed.returncode != 0 or untracked.returncode != 0:
        # 不静默省略尚未跟踪的敏感文件。
        return [f"敏感差异审计失败：{changed.stderr.strip()} {untracked.stderr.strip()}".strip()]
    # 集合去重并覆盖已暂存、未暂存、删除及新增文件。
    paths = set(changed.stdout.split("\0")).union(untracked.stdout.split("\0")) - {""}
    # 固定排序让同一工作树和基线产生可复现的报告。
    return sorted({finding for path in paths for finding in _sensitive_file_findings(path)})


def run_checks(root: Path, base: str | None = None) -> list[str]:
    """运行本地结构治理并返回硬失败；敏感差异通过单独报告交给独立复核者。"""
    # 将输入路径标准化，统一相对路径诊断和依赖识别。
    root = root.resolve()
    # 文档完整性是其余可追踪检查的基础。
    errors = check_documents(root)
    # Python 文件按稳定路径排序，避免平台文件遍历顺序影响输出。
    for directory in SOURCE_DIRECTORIES:
        # 缺失源码目录也应暴露，不能将未交付误报为检查通过。
        if not (root / directory).is_dir():
            # 这些目录全部属于首轮真实交付范围。
            errors.append(f"{directory}: 必需源码目录缺失")
            # 没有目录时继续检查其余目录。
            continue
        # 只读取 Python，不触发导入或任何业务副作用。
        for path in sorted((root / directory).rglob("*.py")):
            # 排除名单是受治理的明确集合，首轮为空。
            if path.relative_to(root).as_posix() in EXCLUDED_PYTHON_FILES:
                # 被排除文件不计入自有手写代码覆盖。
                continue
            # 每条自有语句和函数接口都执行说明检查。
            errors.extend(check_python_comments(path))
            # 核心代码进一步检查依赖和真实时间读取。
            errors.extend(check_architecture(path, root))
    # 固定正反样例通过真实模型校验，不仅比较 JSON 文本。
    from tools.export_contracts import check_examples

    # 六个固定样例必须被提交且保持预期接受/拒绝行为。
    errors.extend(check_examples(root))
    # 公共 Schema 必须与运行类型一致，检查只读而不自动覆盖文件。
    errors.extend(check_schema_drift(root))
    # 指定基线时验证它确实可读；敏感性本身由单独报告供人工审查。
    if base is not None:
        # 检查错误应导致硬失败，不能输出不可信的空差异。
        errors.extend(
            message
            for message in sensitive_diff_report(root, base)
            if message.startswith("敏感差异审计失败")
        )
    # 零错误表示机械检查通过，不表示已获实盘或发布授权。
    return errors


def check_schema_drift(root: Path) -> list[str]:
    """比较运行契约导出的 Schema 与仓库文件；返回差异，不写入或修补文件。"""
    # 延迟加载避免纯注释检查必须初始化整个业务包。
    from quant_core.adapters.storage import schema_documents

    # 权威 Schema 在内存生成，不覆盖待审查的契约文件。
    expected = schema_documents()
    # 契约目录也可能包含说明和样例，只比较明确 Schema 后缀。
    actual = {path.name: path for path in (root / "contracts").glob("*.schema.json")}
    # 累积缺失、多余与内容漂移，便于一次修复。
    errors: list[str] = []
    # 两侧文件名的对称差说明有未同步的契约增删。
    for name in sorted(set(expected).symmetric_difference(actual)):
        # 增删也必须随契约版本和样例同步审查。
        errors.append(f"contracts/{name}: Schema 文件集合与运行契约不同")
    # 只对同时存在的文件比较 JSON 语义，忽略无意义的空白差异。
    for name in sorted(set(expected).intersection(actual)):
        # 无效 JSON 必须作为检查失败，不能自动重写。
        try:
            # 文件仅作为契约数据读取，不执行其中内容。
            document = json.loads(actual[name].read_text(encoding="utf-8"))
        # 解析失败保留文件名和具体错误。
        except json.JSONDecodeError as error:
            # 记录后继续验证其他契约。
            errors.append(f"contracts/{name}: Schema JSON 无效：{error}")
            # 无合法 JSON 时不再进行相等比较。
            continue
        # 运行类型和持久化接口必须来自同一个定义。
        if document != expected[name]:
            # 提醒显式更新契约与测试，而非静默生成修复。
            errors.append(f"contracts/{name}: Schema 漂移，需同步运行契约、版本与测试")
    # 零差异只说明结构一致，兼容语义仍需独立复核。
    return errors
