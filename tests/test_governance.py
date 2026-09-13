"""用正反固定样例验证治理检查；不以减少检查范围换取通过。"""

# SQLite 固定样本验证备份后账户事实一致而非只比较文件存在。
import sqlite3

# 子进程仅建立隔离 Git 样本，测试不修改真实仓库历史。
import subprocess

# 路径用于隔离临时源码与需求映射，不修改生产文件。
from pathlib import Path

# pytest 提供固定参数组合和临时目录夹具。
import pytest

# 契约生成器在测试中替换为独立固定样例，不使用真实业务模型生成期望。
from quant_core.adapters import storage

# 备份和恢复必须保持源不变且拒绝覆盖旧状态。
from tools.backup_sqlite import backup_database, verify_database

# 生成工具默认不写入，显式更新仅覆盖当前权威生成文件。
from tools.export_contracts import check_examples, maintain_contracts

# 直接验证机械检查的公开接口及已声明的能力边界。
from tools.governance import (
    check_architecture,
    check_documents,
    check_python_comments,
    check_schema_drift,
    sensitive_diff_report,
)


def test_documented_statements_and_function_types_pass(tmp_path: Path) -> None:
    """完整中文说明样例应通过；只在 pytest 临时目录写入固定测试源码。"""
    # 固定源码同时覆盖字段、多行语句、分支、异常、返回和 docstring。
    source = '''"""固定中文模块说明。"""
# 明确依赖的用途。
from pathlib import Path

def resolve(value: int) -> int:
    """接收整数并返回非负值；示例异常转为零，不访问外部状态。"""
    # 类型字段也有说明。
    amount: int = value
    # 对负数选择固定归零规则。
    if amount < 0:
        # 返回独立定义的边界结果。
        return 0
    # 非负输入走异常保护示例。
    else:
        # 保留异常分支覆盖。
        try:
            # 显式返回原输入。
            return amount
        # 只处理本例约定的数值异常。
        except ValueError:
            # 固定回退便于机械覆盖测试。
            return 0
        # 无论返回路径如何都执行本地无效操作。
        finally:
            # 此操作仅演示语句覆盖。
            pass
'''
    # 临时源码由独立文本构造，不调用被测函数生成期望。
    path = tmp_path / "documented.py"
    # 写入只发生在隔离测试目录。
    path.write_text(source, encoding="utf-8")
    # 完整固定样例应无诊断。
    assert check_python_comments(path) == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("value = 1\n", "Assign 缺少紧邻中文说明"),
        ("# English only\nvalue = 1\n", "Assign 缺少紧邻中文说明"),
        ("# 中文说明\n\nvalue = 1\n", "Assign 缺少紧邻中文说明"),
        ('value = "# 这只是字符串"\n', "Assign 缺少紧邻中文说明"),
        ("import json\n", "Import 缺少紧邻中文说明"),
        ("value: int = 1\n", "AnnAssign 缺少紧邻中文说明"),
    ],
)
def test_missing_real_adjacent_chinese_comment_fails(
    tmp_path: Path, body: str, expected: str
) -> None:
    """遗漏、英文、空行和字符串伪注释都失败；返回诊断由固定字面量断言。"""
    # 每个参数独立形成一个模块，避免前一个案例影响下一个。
    path = tmp_path / "missing.py"
    # 模块说明有效，使断言只聚焦当前语句缺失。
    path.write_text('"""中文模块说明。"""\n' + body, encoding="utf-8")
    # 严格要求出现对应语句类型诊断。
    assert any(expected in error for error in check_python_comments(path))


def test_function_documentation_and_type_gaps_are_reported(tmp_path: Path) -> None:
    """函数的中文说明和签名类型不能由普通语句注释替代。"""
    # 缺口分别覆盖 docstring、参数和返回类型。
    path = tmp_path / "untyped.py"
    # 返回语句已说明，避免无关注释缺口干扰观察。
    path.write_text(
        '"""中文模块说明。"""\ndef bad(value):\n    # 返回输入。\n    return value\n',
        encoding="utf-8",
    )
    # 一次检查应同时保留三个独立问题。
    errors = check_python_comments(path)
    # 没有 docstring 的定义必须报告。
    assert any("缺少中文 docstring" in error for error in errors)
    # 每个非 self/cls 参数必须提供类型。
    assert any("参数 value 缺少类型" in error for error in errors)
    # 返回值即使很明显也须显式声明。
    assert any("缺少返回类型" in error for error in errors)


def test_else_branch_requires_its_own_explanation(tmp_path: Path) -> None:
    """else 无独立 AST 语句，仍必须验证其中文触发说明。"""
    # 正文赋值都已说明，只留下 else 分支说明缺口。
    source = '''"""中文模块说明。"""
# 固定条件用于分支覆盖。
if True:
    # 固定值定义第一条路径。
    value = 1
else:
    # 固定值定义另一条路径。
    value = 2
'''
    # 本例只写临时 Python 文件。
    path = tmp_path / "branch.py"
    # 保留源码行号，便于断言精确分支位置。
    path.write_text(source, encoding="utf-8")
    # 必须指出 else 所在第六行缺少说明。
    assert any(":6: 分支缺少紧邻中文说明" in error for error in check_python_comments(path))


def test_docstring_branch_text_is_not_code(tmp_path: Path) -> None:
    """文档中的 else 示例不是执行语句，不能产生虚假分支诊断。"""
    # 多行文档包含看似分支的文字，用 token 类型区分。
    source = '"""中文说明。\nelse:\n    这行是文字。\n"""\n'
    # 临时文件没有真实业务语句。
    path = tmp_path / "prose.py"
    # 写入固定文档样例。
    path.write_text(source, encoding="utf-8")
    # 合法纯说明模块应完全通过。
    assert check_python_comments(path) == []


def test_core_rejects_network_and_real_clock_alias(tmp_path: Path) -> None:
    """核心即使用别名也不能联网或读取真实时钟；不实际导入这些依赖。"""
    # 使用约定目录模拟真正核心边界。
    path = tmp_path / "src/quant_core/factors.py"
    # 临时父目录仅用于隔离静态检查。
    path.parent.mkdir(parents=True)
    # 样例包含网络依赖、适配器反向依赖和带别名的真实时钟。
    path.write_text(
        "import requests\nfrom quant_core.adapters import storage\nfrom datetime import datetime as dt\ndt.now()\n",
        encoding="utf-8",
    )
    # 检查不执行 dt.now 或任何网络访问。
    errors = check_architecture(path, tmp_path)
    # 网络 SDK 必须留在适配器。
    assert any("禁止导入 requests" in error for error in errors)
    # 核心不能反向导入外部实现。
    assert any("越界导入 quant_core.adapters" in error for error in errors)
    # 别名不能绕过时间注入约束。
    assert any("datetime.datetime.now" in error for error in errors)


def test_adapter_boundary_allows_external_imports(tmp_path: Path) -> None:
    """适配器可以声明外部依赖；本检查不代表真实网络或账户访问获准。"""
    # 以真实适配器目录结构验证边界豁免。
    path = tmp_path / "src/quant_core/adapters/source.py"
    # 创建临时目录不触及生产适配器。
    path.parent.mkdir(parents=True)
    # 只分析 import 文本，不实际运行第三方包。
    path.write_text("import requests\n", encoding="utf-8")
    # 外部依赖存在于正确职责层时不构成架构错误。
    assert check_architecture(path, tmp_path) == []


def test_missing_document_and_invalid_requirement_reference_fail(tmp_path: Path) -> None:
    """空目录和断开的需求引用不能构成文档交付。"""
    # 最小目录用于验证缺失其他必需文档的诊断。
    (tmp_path / "docs").mkdir()
    # 固定坏引用分别覆盖仓库逃逸和不存在文件。
    (tmp_path / "docs/requirements.json").write_text(
        '[{"id":"QC-001","implementation":["../outside.py"],"tests":["missing.py"],"docs":[]}]',
        encoding="utf-8",
    )
    # 实际返回应保留所有独立失败，不能遇首错后隐去其他项。
    errors = check_documents(tmp_path)
    # README 是职责入口，不能缺失。
    assert any("README.md: 必需文档缺失" in error for error in errors)
    # 不允许用仓库外路径充当本项目实现。
    assert any("非法引用 '../outside.py'" in error for error in errors)
    # 测试路径必须实际存在。
    assert any("tests 引用不存在：missing.py" in error for error in errors)
    # 文档映射也不能是空列表。
    assert any("docs 必须是非空路径列表" in error for error in errors)


def test_missing_git_base_is_explicitly_unverified(tmp_path: Path) -> None:
    """未指定基线时不能把没有差异报告冒称已完成敏感审计。"""
    # 独立固定文本要求明确指出没有执行审计。
    assert "未执行敏感差异审计" in sensitive_diff_report(tmp_path)[0]


def test_invalid_git_base_reports_audit_failure(tmp_path: Path) -> None:
    """无效 Git 上下文产生显式失败，不会报告成无敏感修改。"""
    # 临时目录不是 Git 仓库，所以不存在有效比较基线。
    report = sensitive_diff_report(tmp_path, "definitely-not-a-valid-base")
    # 必须留下失败诊断供聚合检查使用。
    assert report[0].startswith("敏感差异审计失败")


def test_previous_inline_comment_cannot_explain_next_statement(tmp_path: Path) -> None:
    """前一条语句的行内说明不能让无注释的新语句通过。"""
    # 两条赋值仅第一条含行内注释，第二条必须独立失败。
    source = '"""中文模块说明。"""\nfirst = 1  # 解释第一条。\nsecond = 2\n'
    # 固定反例保存在隔离目录。
    path = tmp_path / "borrowed_comment.py"
    # 不依赖生产源码生成期望。
    path.write_text(source, encoding="utf-8")
    # 只有第二条赋值的第三行缺少说明。
    assert any(":3: Assign 缺少紧邻中文说明" in error for error in check_python_comments(path))


def test_compound_header_cannot_borrow_child_inline_comment(tmp_path: Path) -> None:
    """分支头和分支内语句分别解释，子语句说明不能覆盖无说明的 if。"""
    # if 本身没有解释，但最后一条子语句有行内说明。
    source = '"""中文模块说明。"""\nif True:\n    value = 1  # 只解释赋值。\n'
    # 固定反例直接写入临时文件。
    path = tmp_path / "borrowed_branch.py"
    # 文件写入仅限测试夹具目录。
    path.write_text(source, encoding="utf-8")
    # 必须指出第二行的 If，而非将子节点注释冒用为覆盖。
    assert any(":2: If 缺少紧邻中文说明" in error for error in check_python_comments(path))


def test_schema_comparison_detects_content_and_file_set_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """固定 Schema 正例通过，字段或文件集合漂移失败；只改临时文件和测试绑定。"""

    def fixed_documents() -> dict[str, dict[str, str]]:
        """返回独立字面量契约，不读取生产 Schema 或修改任何外部状态。"""
        # 期望结构故意保持最小，便于独立验证漂移检查。
        return {"fixture.schema.json": {"type": "string"}}

    # 用固定提供者隔离业务类型，测试目标仅是漂移比较行为。
    monkeypatch.setattr(storage, "schema_documents", fixed_documents)
    # 临时契约目录不触及仓库内已生成 Schema。
    directory = tmp_path / "contracts"
    # 建立待比较文件目录。
    directory.mkdir()
    # 独立字面量正例与固定提供者结构一致。
    (directory / "fixture.schema.json").write_text('{"type":"string"}', encoding="utf-8")
    # 相同结构必须没有漂移诊断。
    assert check_schema_drift(tmp_path) == []
    # 修改真实文件而不改期望，模拟开发者遗漏 Schema 同步。
    (directory / "fixture.schema.json").write_text('{"type":"integer"}', encoding="utf-8")
    # 类型变化必须被精确归类为内容漂移。
    assert any(
        "fixture.schema.json: Schema 漂移" in error for error in check_schema_drift(tmp_path)
    )
    # 额外文件模拟未清理的旧版本契约。
    (directory / "extra.schema.json").write_text("{}", encoding="utf-8")
    # 多余文件也不得被静默忽略。
    assert any(
        "extra.schema.json: Schema 文件集合" in error for error in check_schema_drift(tmp_path)
    )
    # 缺失权威文件是另一种契约不完整状态。
    (directory / "fixture.schema.json").unlink()
    # 检查应同时报告缺失而非只检查存在的文件。
    assert any(
        "fixture.schema.json: Schema 文件集合" in error for error in check_schema_drift(tmp_path)
    )


def test_sensitive_diff_includes_deleted_tests_and_untracked_risk(tmp_path: Path) -> None:
    """临时 Git 固定样本覆盖已删除测试和未跟踪风控文件；不触及真实仓库。"""

    def git(*arguments: str) -> None:
        """在 pytest 临时目录执行明确的 Git 参数，失败抛异常；只写该目录。"""
        # 账号配置仅限本次命令，不更改用户全局 Git 配置。
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Governance test",
                "-c",
                "user.email=test@example.invalid",
                *arguments,
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )

    # 创建独立仓库，为基线差异提供真实 Git 行为。
    git("init", "--quiet")
    # 测试文件作为已提交的固定基线。
    (tmp_path / "tests").mkdir()
    # 测试源码在本例是待审查数据，不执行。
    tracked = tmp_path / "tests/test_original.py"
    # 独立字面量表示原有断言。
    tracked.write_text("assert 1 == 1\n", encoding="utf-8")
    # 暂存仅限临时仓库的明确文件。
    git("add", "tests/test_original.py")
    # 创建确定的测试基线，不影响共享仓库历史。
    git("commit", "--quiet", "-m", "fixed governance fixture")
    # 删除现有测试模拟必须被报告的敏感变更。
    tracked.unlink()
    # 新增尚未暂存的风险模块也必须进入差异审查。
    (tmp_path / "src/quant_core").mkdir(parents=True)
    # 文件内容不决定敏感性，职责路径本身就需要审查。
    (tmp_path / "src/quant_core/risk.py").write_text("limit = 1\n", encoding="utf-8")
    # 以明确提交基线读取真实工作树差异。
    report = sensitive_diff_report(tmp_path, "HEAD")
    # 删除测试不能因新路径不存在而遗漏。
    assert "测试变更需复核：tests/test_original.py" in report
    # 未跟踪文件不能因为 git diff 看不到而漏报。
    assert "风险或资金行为变更需复核：src/quant_core/risk.py" in report


def test_core_dependencies_follow_explicit_responsibility_matrix(tmp_path: Path) -> None:
    """同属核心也不能逆向依赖任意模块；因子允许股票池但不得读取交易账务。"""
    # 使用真实职责文件名，使检查执行 factors 允许依赖集合。
    path = tmp_path / "src/quant_core/factors.py"
    # 模拟目录只存在于测试工作区。
    path.parent.mkdir(parents=True)
    # 两种导入语法验证职责矩阵而非仅检查网络黑名单。
    path.write_text(
        "from .universe import qualified_universe\nfrom quant_core import ledger\n",
        encoding="utf-8",
    )
    # 不执行任一导入，静态读取确定的依赖关系。
    errors = check_architecture(path, tmp_path)
    # 合法 universe 依赖不能误报。
    assert not any("未获允许依赖 universe" in error for error in errors)
    # 因子读取账务将混淆研究输入和执行事实，必须报告。
    assert any("factors 未获允许依赖 ledger" in error for error in errors)


def test_contract_maintenance_is_read_only_by_default_and_exports_examples(tmp_path: Path) -> None:
    """缺生成文件默认报错但不建目录，显式写入后所有模型与六个正反例通过。"""
    # 缺失生成文件是预期失败，不应被检查自动修补。
    assert maintain_contracts(tmp_path)
    # 默认只读操作不能在空目录创建输出。
    assert not (tmp_path / "contracts").exists()
    # 本测试显式授权临时目录的生成文件创建。
    assert maintain_contracts(tmp_path, write=True) == []
    # 三类消息各一正一反，共六份固定样例。
    assert len(list((tmp_path / "contracts/examples").glob("*.json"))) == 6
    # 实际模型必须接受正例并拒绝错误单位、批准候选及布尔股数。
    assert check_examples(tmp_path) == []
    # 再次只读检查确认磁盘生成内容与权威类型一致。
    assert maintain_contracts(tmp_path) == []


def test_unknown_old_schema_blocks_write_without_deleting_or_replacing(tmp_path: Path) -> None:
    """未知旧 Schema 触发显式迁移要求，写入请求也不能删除或顺带更新其他文件。"""
    # 临时目录首先生成一份完整当前契约。
    assert maintain_contracts(tmp_path, write=True) == []
    # 旧文件模拟未批准的契约迁移残留。
    old = tmp_path / "contracts/RetiredMessage.schema.json"
    # 固定旧内容必须在失败后保留。
    old.write_text('{"legacy":true}', encoding="utf-8")
    # 将当前生成文件标为待修复，验证预检会在任何写入之前失败。
    current = tmp_path / "contracts/OrderIntent.schema.json"
    # 哨兵内容用于证明不会发生部分覆盖。
    current.write_text('{"sentinel":"must-remain"}', encoding="utf-8")
    # 显式写入也必须尊重未知旧契约的迁移边界。
    errors = maintain_contracts(tmp_path, write=True)
    # 诊断明确需要迁移，不能鼓励删除测试或删旧文件强过检查。
    assert any("RetiredMessage.schema.json: 未知旧 Schema" in error for error in errors)
    # 未知文件保留原内容，没有被清理。
    assert old.read_text(encoding="utf-8") == '{"legacy":true}'
    # 当前文件也未更新，避免半次未经审批的导出。
    assert current.read_text(encoding="utf-8") == '{"sentinel":"must-remain"}'


def test_sqlite_backup_and_restore_preserve_independent_fixed_facts(tmp_path: Path) -> None:
    """真实 SQLite 固定两笔事件经备份和恢复保持完整；只写 pytest 临时目录。"""
    # 源数据库采用明确独立路径。
    source = tmp_path / "source.sqlite"
    # 测试使用标准 SQLite，不调用被测备份函数构造期望。
    database = sqlite3.connect(source)
    # 所有连接在成功或失败后都明确关闭。
    try:
        # 两条固定事件是可独立手算的原始事实。
        database.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, quantity INTEGER NOT NULL)")
        # 具体数量10与5不由生产投影计算。
        database.executemany("INSERT INTO events VALUES (?, ?)", [(1, 10), (2, 5)])
        # 用户状态库版本也必须由备份保留。
        database.execute("PRAGMA user_version=7")
        # 提交后模拟已停止写入的运行输出。
        database.commit()
    # 不把持续写入连接带入备份测试。
    finally:
        # 源文件保持可独立读取状态。
        database.close()
    # 记录源文件字节指纹，确认备份没有修改源。
    original = verify_database(source)
    # 第一次复制到新的备份目录。
    backup = tmp_path / "backup/source.sqlite"
    # 工具应返回真实的完整性与摘要证据。
    result = backup_database(source, backup)
    # 同一次复制必须保持结构和数据的逻辑摘要。
    assert result["logical_sha256"] == original["logical_sha256"]
    # schema user_version不能因新建文件丢失。
    assert result["user_version"] == "7"
    # 验证源文件字节未变化，不能把备份变成迁移。
    assert verify_database(source)["sha256"] == original["sha256"]
    # 恢复也必须选择不存在的新数据库。
    restored = tmp_path / "restore/source.sqlite"
    # 从备份恢复，不直接覆盖原运行库。
    backup_database(backup, restored)
    # 用独立 SQL 核对最终行和数量。
    check = sqlite3.connect(restored)
    # 数据库句柄无论断言是否成功都关闭。
    try:
        # 精确验证两条事件，而非只验证“有输出文件”。
        assert check.execute("SELECT id, quantity FROM events ORDER BY id").fetchall() == [
            (1, 10),
            (2, 5),
        ]
    # 测试连接不遗留数据库锁。
    finally:
        # 显式释放恢复库连接。
        check.close()
    # 第二次同名恢复必须失败，保护已经验证的输出。
    with pytest.raises(FileExistsError):
        # 不能自动清理旧输出再复制来获得通过。
        backup_database(backup, restored)


def test_sqlite_backup_rejects_missing_or_corrupt_source(tmp_path: Path) -> None:
    """缺失或非数据库源不能生成一个假成功空库；目标目录保持未创建。"""
    # 拼错源路径必须由只读模式拒绝。
    with pytest.raises(FileNotFoundError):
        # 输出还不存在，失败后也不能出现。
        backup_database(tmp_path / "missing.sqlite", tmp_path / "missing-copy.sqlite")
    # 确保没有被 sqlite.connect 自动建成空源。
    assert not (tmp_path / "missing.sqlite").exists()
    # 明确不是 SQLite 的字节模拟损坏源。
    broken = tmp_path / "broken.sqlite"
    # 固定文本不包含任何数据库数据。
    broken.write_bytes(b"not-a-sqlite-database")
    # SQLite 自身完整性检查必须拒绝该源。
    with pytest.raises(sqlite3.DatabaseError):
        # 不修复源，也不制造空目标通过测试。
        backup_database(broken, tmp_path / "broken-copy.sqlite")
    # 失败不能留下被误认作已验证备份的文件。
    assert not (tmp_path / "broken-copy.sqlite").exists()
