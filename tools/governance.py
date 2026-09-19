"""为维护流程提供 docstring/类型、需求引用、依赖与 Schema 的结构证据。

读取 src、tests、tools 及 Git 差异，返回诊断而不修补业务数据。
检查通过不代表注释语义正确，也不代表交易或发布已获授权。
"""

import ast
import json
import re
import subprocess
from pathlib import Path

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
    return re.search(r"[\u3400-\u9fff]", text) is not None


def check_python_comments(path: Path) -> list[str]:
    """只读检查中文 docstring 是否存在及函数签名类型是否完整。

    仅模块、类和函数的首条字符串表达式被视为 docstring；普通字符串与注释
    不能替代它。解析失败返回诊断。此检查不衡量注释密度，也不能判断业务说明
    是否正确、主流程是否讲清或关键边界是否遗漏，后者必须经过语义复核。
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return [f"{path}: Python 解析失败：{error}"]
    errors: list[str] = []
    for node in ast.walk(tree):
        # 使用 AST 识别真正的文档位置，不把变量里的中文或稍后的字符串当作说明。
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node, clean=False)
            if not docstring or not contains_chinese(docstring):
                errors.append(f"{path}:{getattr(node, 'lineno', 1)}: 缺少中文 docstring")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 全部参数种类都参与检查；self/cls 沿用所属类提供的类型信息。
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            for argument in arguments:
                if argument.arg not in {"self", "cls"} and argument.annotation is None:
                    errors.append(f"{path}:{node.lineno}: 参数 {argument.arg} 缺少类型")
            if node.returns is None:
                errors.append(f"{path}:{node.lineno}: 函数 {node.name} 缺少返回类型")
    return errors


def _project_dependencies(node: ast.Import | ast.ImportFrom) -> set[str]:
    """提取明确的项目模块依赖名称；输入是 AST，不执行导入也不改变源码。"""
    if isinstance(node, ast.Import):
        # 包根直接导入无法表达窄职责，因此显式命名为需审查依赖。
        return {
            name.name.split(".")[1] if "." in name.name else "__package__"
            for name in node.names
            if name.name == "quant_core" or name.name.startswith("quant_core.")
        }
    # from quant_core import module 需要从导入项提取模块名称。
    if node.module == "quant_core":
        return {name.name for name in node.names}
    if node.module and node.module.startswith("quant_core."):
        return {node.module.split(".")[1]}
    if node.level:
        # 空 module 对应 from . import module。
        return {node.module.split(".")[0]} if node.module else {name.name for name in node.names}
    return set()


def check_architecture(path: Path, root: Path) -> list[str]:
    """只读检查核心模块的依赖方向、网络依赖和直接系统时钟调用。

    path 必须位于 root 内；返回静态发现的问题。适配器与装配层按架构承接
    副作用，不套用纯核心矩阵；它们仍参与 docstring、类型及其他治理检查。
    """
    # 用仓库相对路径识别职责，避免工作目录改变检查范围。
    relative = path.relative_to(root).as_posix()
    if not relative.startswith("src/quant_core/"):
        return []
    if "/adapters/" in relative or path.stem in {"application", "cli", "__init__", "__main__"}:
        return []
    # 解析失败由源码检查负责，这里避免重复抛异常。
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    errors: list[str] = []
    if path.stem not in CORE_DEPENDENCIES:
        errors.append(f"{path}: 未登记核心模块的允许依赖")
    permitted = CORE_DEPENDENCIES.get(path.stem, frozenset())
    # 导入别名用于识别通过别名直接获取真实时间的调用。
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        # 核心模块之间也遵守显式依赖矩阵。
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for dependency in sorted(_project_dependencies(node) - permitted):
                errors.append(f"{path}:{node.lineno}: {path.stem} 未获允许依赖 {dependency}")
        if isinstance(node, ast.Import):
            for name in node.names:
                # 保存实际导入名称，防止别名隐藏时钟调用。
                aliases[name.asname or name.name.split(".")[0]] = name.name
                if name.name.split(".")[0] in FORBIDDEN_IMPORTS or BOUNDARY_MODULES.intersection(
                    name.name.split(".")
                ):
                    errors.append(f"{path}:{node.lineno}: 核心禁止导入 {name.name}")
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # 保存 from datetime import datetime 及别名等绑定。
            for name in node.names:
                aliases[name.asname or name.name] = f"{module}.{name.name}".strip(".")
            if (
                module.split(".")[0] in FORBIDDEN_IMPORTS
                or BOUNDARY_MODULES.intersection(module.split("."))
                or (not module and any(name.name in BOUNDARY_MODULES for name in node.names))
            ):
                errors.append(f"{path}:{node.lineno}: 核心越界导入 {module}")
        # 实际时间读取必须通过 Clock 注入，历史解析不受影响。
        if isinstance(node, ast.Call):
            target = ast.unparse(node.func)
            # 别名仅替换首段，保留后续属性访问。
            first, _, rest = target.partition(".")
            resolved = aliases.get(first, first) + (f".{rest}" if rest else "")
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
                errors.append(f"{path}:{node.lineno}: 核心必须注入 Clock，不能调用 {resolved}")
    # 架构检查不推断运行时猴子补丁或动态 import 的实际行为。
    return errors


def check_documents(root: Path) -> list[str]:
    """验证职责文档和需求映射引用，避免只交付空目录或断开的追踪索引。"""
    errors: list[str] = []
    # 每个必需文档必须实际存在且包含内容。
    for relative in REQUIRED_DOCUMENTS:
        path = root / relative
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"{relative}: 必需文档缺失或为空")
    mapping = root / "docs/requirements.json"
    if not mapping.is_file():
        return errors
    try:
        requirements = json.loads(mapping.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [*errors, f"docs/requirements.json: JSON 无效：{error}"]
    # 防止意外把对象或字符串当作需求列表遍历。
    if not isinstance(requirements, list) or not requirements:
        return [*errors, "docs/requirements.json: 必须为非空需求列表"]
    seen: set[str] = set()
    # 每条需求必须连接实现、测试与文档三种证据。
    for item in requirements:
        if not isinstance(item, dict):
            errors.append("docs/requirements.json: 需求条目必须为对象")
            continue
        identifier = item.get("id")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"QC-\d{3}", identifier)
            or identifier in seen
        ):
            errors.append(f"docs/requirements.json: 无效或重复需求 ID {identifier!r}")
        if isinstance(identifier, str):
            seen.add(identifier)
        # 三类路径引用都必须非空且存在。
        for field in ("implementation", "tests", "docs"):
            paths = item.get(field)
            if not isinstance(paths, list) or not paths:
                errors.append(f"{identifier}: {field} 必须是非空路径列表")
                continue
            for relative in paths:
                # 防止绝对路径或父目录引用逃逸到仓库之外。
                if (
                    not isinstance(relative, str)
                    or Path(relative).is_absolute()
                    or ".." in Path(relative).parts
                ):
                    errors.append(f"{identifier}: 非法引用 {relative!r}")
                elif not (root / relative).is_file():
                    errors.append(f"{identifier}: {field} 引用不存在：{relative}")
    return errors


def _sensitive_file_findings(path: str) -> list[str]:
    """按仓库相对路径返回独立复核类别；不判断具体断言强弱或风险方向。"""
    findings: list[str] = []
    # 测试增删改全部需要语义审查，断言数量不是强度证明。
    if path.startswith("tests/"):
        findings.append(f"测试变更需复核：{path}")
    if (
        Path(path).name == "AGENTS.md"
        or path.startswith((".github/", "prompts/", "tools/"))
        or path in {"uv.lock", "pyproject.toml"}
    ):
        findings.append(f"治理或依赖边界变更需独立授权复核：{path}")
    if path.startswith(
        ("configs/", "src/quant_core/risk", "src/quant_core/execution", "src/quant_core/ledger")
    ):
        # 数值变大不一定更安全，不能自动替代业务审查。
        findings.append(f"风险或资金行为变更需复核：{path}")
    return findings


def sensitive_diff_report(root: Path, base: str | None = None) -> list[str]:
    """报告基线至工作树及未跟踪敏感文件；Git 失败显式返回，不写文件或授予批准。"""
    if base is None:
        return ["未提供 --base：未执行敏感差异审计；需独立复核初始规则与风险参数。"]
    # 先把用户引用解析为提交，--end-of-options 防止引用被当成 Git 选项。
    revision = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if revision.returncode != 0:
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
    if changed.returncode != 0 or untracked.returncode != 0:
        return [f"敏感差异审计失败：{changed.stderr.strip()} {untracked.stderr.strip()}".strip()]
    # 集合去重并覆盖已暂存、未暂存、删除及新增文件。
    paths = set(changed.stdout.split("\0")).union(untracked.stdout.split("\0")) - {""}
    return sorted({finding for path in paths for finding in _sensitive_file_findings(path)})


def run_checks(root: Path, base: str | None = None) -> list[str]:
    """运行本地结构治理并返回硬失败；敏感差异通过单独报告交给独立复核者。"""
    root = root.resolve()
    errors = check_documents(root)
    for directory in SOURCE_DIRECTORIES:
        # 缺失源码目录也应暴露，不能将未交付误报为检查通过。
        if not (root / directory).is_dir():
            errors.append(f"{directory}: 必需源码目录缺失")
            continue
        for path in sorted((root / directory).rglob("*.py")):
            if path.relative_to(root).as_posix() in EXCLUDED_PYTHON_FILES:
                continue
            # 定义说明和函数类型是机械检查；代码块语义由独立复核判断。
            errors.extend(check_python_comments(path))
            errors.extend(check_architecture(path, root))
    # 固定正反样例通过真实模型校验，不仅比较 JSON 文本。
    from tools.export_contracts import check_examples

    errors.extend(check_examples(root))
    errors.extend(check_schema_drift(root))
    # 指定基线时验证它确实可读；敏感性本身由单独报告供人工审查。
    if base is not None:
        errors.extend(
            message
            for message in sensitive_diff_report(root, base)
            if message.startswith("敏感差异审计失败")
        )
    return errors


def check_schema_drift(root: Path) -> list[str]:
    """比较运行契约导出的 Schema 与仓库文件；返回差异，不写入或修补文件。"""
    # 延迟加载避免纯注释检查必须初始化整个业务包。
    from quant_core.adapters.storage import schema_documents

    expected = schema_documents()
    actual = {path.name: path for path in (root / "contracts").glob("*.schema.json")}
    errors: list[str] = []
    # 两侧文件名的对称差说明有未同步的契约增删。
    for name in sorted(set(expected).symmetric_difference(actual)):
        errors.append(f"contracts/{name}: Schema 文件集合与运行契约不同")
    # 只对同时存在的文件比较 JSON 语义，忽略无意义的空白差异。
    for name in sorted(set(expected).intersection(actual)):
        try:
            document = json.loads(actual[name].read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            errors.append(f"contracts/{name}: Schema JSON 无效：{error}")
            continue
        if document != expected[name]:
            errors.append(f"contracts/{name}: Schema 漂移，需同步运行契约、版本与测试")
    return errors
