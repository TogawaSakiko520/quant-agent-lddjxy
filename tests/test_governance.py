"""用正反固定样例验证治理检查；不以减少检查范围换取通过。"""

import sqlite3
import subprocess
from pathlib import Path

import pytest

from quant_core.adapters import storage
from tools.backup_sqlite import backup_database, verify_database
from tools.export_contracts import check_examples, maintain_contracts
from tools.governance import (
    check_architecture,
    check_documents,
    check_python_comments,
    check_schema_drift,
    sensitive_diff_report,
)


def test_documented_definitions_without_statement_comments_pass(tmp_path: Path) -> None:
    """中文职责说明和完整签名足够通过结构检查；普通语句不必逐条解释。"""
    source = '''"""固定中文模块说明。"""
from pathlib import Path

class Amount:
    """保存样例金额；本例只检查声明，不执行交易。"""
    value: int = 1

def resolve(value: int) -> int:
    """负数归零，其余返回原值；固定样例不读取外部状态。"""
    amount: int = value
    if amount < 0:
        return 0
    else:
        try:
            return amount
        except ValueError:
            return 0
        finally:
            pass
'''
    path = tmp_path / "documented.py"
    path.write_text(source, encoding="utf-8")
    assert check_python_comments(path) == []


@pytest.mark.parametrize(
    "body",
    [
        "value = 1\n",
        "# English only\nvalue = 1\n",
        "# 中文说明\n\nvalue = 1\n",
        'value = "# 这只是字符串"\n',
        "import json\n",
        "value: int = 1\n",
    ],
)
def test_ordinary_statements_do_not_require_adjacent_comments(tmp_path: Path, body: str) -> None:
    """旧邻接反例按新规范成为正例；注释距离、语言和字符串内容不限制普通语句。"""
    path = tmp_path / "ordinary.py"
    path.write_text('"""中文模块说明。"""\n' + body, encoding="utf-8")
    assert check_python_comments(path) == []


@pytest.mark.parametrize(
    "source",
    [
        "value = 1\n",
        '"""English module documentation."""\n',
        '# 中文注释不是模块文档。\nvalue = "中文说明"\n',
        'value = 1\n"""不在首条位置的中文字符串。"""\n',
        '"""中文模块说明。"""\nclass Missing:\n    pass\n',
        '"""中文模块说明。"""\nclass English:\n    """English class."""\n',
        '"""中文模块说明。"""\nclass Fake:\n    description = "中文字符串"\n',
        '"""中文模块说明。"""\ndef missing() -> None:\n    pass\n',
        '"""中文模块说明。"""\ndef english() -> None:\n    """English function."""\n',
        '"""中文模块说明。"""\ndef fake() -> None:\n    value = 1\n    """稍后的字符串不是文档。"""\n',
        '"""中文模块说明。"""\nasync def fake() -> None:\n    value = "中文说明"\n',
    ],
)
def test_missing_chinese_real_docstrings_fail(tmp_path: Path, source: str) -> None:
    """模块、类和同步/异步函数均需真正的中文 docstring；普通字符串不能冒充。"""
    path = tmp_path / "missing_docstring.py"
    path.write_text(source, encoding="utf-8")
    errors = check_python_comments(path)
    assert any("缺少中文 docstring" in error for error in errors)


@pytest.mark.parametrize("signature", ["value, /", "value", "*, value", "*value", "**value"])
def test_each_parameter_kind_requires_annotation(tmp_path: Path, signature: str) -> None:
    """位置专用、普通、关键字专用及可变参数都不能省略类型。"""
    path = tmp_path / "missing_type.py"
    path.write_text(
        '"""中文模块说明。"""\n'
        + f'def sample({signature}) -> None:\n    """只声明待检查的参数。"""\n',
        encoding="utf-8",
    )
    assert any("参数 value 缺少类型" in error for error in check_python_comments(path))


def test_function_documentation_and_type_gaps_are_reported(tmp_path: Path) -> None:
    """函数的中文说明和签名类型不能由普通语句注释替代。"""
    # 缺口分别覆盖 docstring、参数和返回类型。
    path = tmp_path / "untyped.py"
    path.write_text(
        '"""中文模块说明。"""\ndef bad(value):\n    # 返回输入。\n    return value\n',
        encoding="utf-8",
    )
    errors = check_python_comments(path)
    assert any("缺少中文 docstring" in error for error in errors)
    assert any("参数 value 缺少类型" in error for error in errors)
    assert any("缺少返回类型" in error for error in errors)


def test_branches_do_not_require_separate_comments(tmp_path: Path) -> None:
    """else、except 和 finally 与普通分支一样，不再各自承担邻接注释门槛。"""
    source = '''"""中文模块说明。"""
if True:
    value = 1
else:
    value = 2
try:
    value += 1
except ValueError:
    value = 0
finally:
    value = 3
'''
    path = tmp_path / "branch.py"
    path.write_text(source, encoding="utf-8")
    assert check_python_comments(path) == []


def test_docstring_branch_text_is_not_code(tmp_path: Path) -> None:
    """文档中的 else 示例不是执行语句，不能产生虚假分支诊断。"""
    source = '"""中文说明。\nelse:\n    这行是文字。\n"""\n'
    path = tmp_path / "prose.py"
    path.write_text(source, encoding="utf-8")
    assert check_python_comments(path) == []


def test_core_rejects_network_and_real_clock_alias(tmp_path: Path) -> None:
    """核心即使用别名也不能联网或读取真实时钟；不实际导入这些依赖。"""
    path = tmp_path / "src/quant_core/factors.py"
    path.parent.mkdir(parents=True)
    # 样例包含网络依赖、适配器反向依赖和带别名的真实时钟。
    path.write_text(
        "import requests\nfrom quant_core.adapters import storage\nfrom datetime import datetime as dt\ndt.now()\n",
        encoding="utf-8",
    )
    errors = check_architecture(path, tmp_path)
    assert any("禁止导入 requests" in error for error in errors)
    assert any("越界导入 quant_core.adapters" in error for error in errors)
    assert any("datetime.datetime.now" in error for error in errors)


def test_adapter_boundary_allows_external_imports(tmp_path: Path) -> None:
    """适配器可以声明外部依赖；本检查不代表真实网络或账户访问获准。"""
    path = tmp_path / "src/quant_core/adapters/source.py"
    path.parent.mkdir(parents=True)
    path.write_text("import requests\n", encoding="utf-8")
    assert check_architecture(path, tmp_path) == []


def test_missing_document_and_invalid_requirement_reference_fail(tmp_path: Path) -> None:
    """空目录和断开的需求引用不能构成文档交付。"""
    (tmp_path / "docs").mkdir()
    # 固定坏引用分别覆盖仓库逃逸和不存在文件。
    (tmp_path / "docs/requirements.json").write_text(
        '[{"id":"QC-001","implementation":["../outside.py"],"tests":["missing.py"],"docs":[]}]',
        encoding="utf-8",
    )
    errors = check_documents(tmp_path)
    assert any("README.md: 必需文档缺失" in error for error in errors)
    assert any("非法引用 '../outside.py'" in error for error in errors)
    assert any("tests 引用不存在：missing.py" in error for error in errors)
    assert any("docs 必须是非空路径列表" in error for error in errors)


def test_missing_git_base_is_explicitly_unverified(tmp_path: Path) -> None:
    """未指定基线时不能把没有差异报告冒称已完成敏感审计。"""
    assert "未执行敏感差异审计" in sensitive_diff_report(tmp_path)[0]


def test_invalid_git_base_reports_audit_failure(tmp_path: Path) -> None:
    """无效 Git 上下文产生显式失败，不会报告成无敏感修改。"""
    report = sensitive_diff_report(tmp_path, "definitely-not-a-valid-base")
    assert report[0].startswith("敏感差异审计失败")


def test_inline_comment_does_not_require_comment_on_next_statement(tmp_path: Path) -> None:
    """行内说明可以保留，后续普通赋值不必为满足覆盖率另加注释。"""
    source = '"""中文模块说明。"""\nfirst = 1  # 解释第一条。\nsecond = 2\n'
    path = tmp_path / "borrowed_comment.py"
    path.write_text(source, encoding="utf-8")
    assert check_python_comments(path) == []


def test_compound_header_does_not_require_separate_comment(tmp_path: Path) -> None:
    """可保留有用的行内解释；简单 if 无单独注释不会触发结构诊断。"""
    source = '"""中文模块说明。"""\nif True:\n    value = 1  # 只解释赋值。\n'
    path = tmp_path / "borrowed_branch.py"
    path.write_text(source, encoding="utf-8")
    assert check_python_comments(path) == []


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
    directory = tmp_path / "contracts"
    directory.mkdir()
    (directory / "fixture.schema.json").write_text('{"type":"string"}', encoding="utf-8")
    assert check_schema_drift(tmp_path) == []
    # 修改真实文件而不改期望，模拟开发者遗漏 Schema 同步。
    (directory / "fixture.schema.json").write_text('{"type":"integer"}', encoding="utf-8")
    assert any(
        "fixture.schema.json: Schema 漂移" in error for error in check_schema_drift(tmp_path)
    )
    # 额外文件模拟未清理的旧版本契约。
    (directory / "extra.schema.json").write_text("{}", encoding="utf-8")
    assert any(
        "extra.schema.json: Schema 文件集合" in error for error in check_schema_drift(tmp_path)
    )
    # 缺失权威文件是另一种契约不完整状态。
    (directory / "fixture.schema.json").unlink()
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
    (tmp_path / "tests").mkdir()
    tracked = tmp_path / "tests/test_original.py"
    tracked.write_text("assert 1 == 1\n", encoding="utf-8")
    git("add", "tests/test_original.py")
    git("commit", "--quiet", "-m", "fixed governance fixture")
    # 删除现有测试模拟必须被报告的敏感变更。
    tracked.unlink()
    # 新增尚未暂存的风险模块也必须进入差异审查。
    (tmp_path / "src/quant_core").mkdir(parents=True)
    (tmp_path / "src/quant_core/risk.py").write_text("limit = 1\n", encoding="utf-8")
    report = sensitive_diff_report(tmp_path, "HEAD")
    assert "测试变更需复核：tests/test_original.py" in report
    assert "风险或资金行为变更需复核：src/quant_core/risk.py" in report


def test_core_dependencies_follow_explicit_responsibility_matrix(tmp_path: Path) -> None:
    """同属核心也不能逆向依赖任意模块；因子允许股票池但不得读取交易账务。"""
    path = tmp_path / "src/quant_core/factors.py"
    path.parent.mkdir(parents=True)
    # 两种导入语法验证职责矩阵而非仅检查网络黑名单。
    path.write_text(
        "from .universe import qualified_universe\nfrom quant_core import ledger\n",
        encoding="utf-8",
    )
    errors = check_architecture(path, tmp_path)
    assert not any("未获允许依赖 universe" in error for error in errors)
    assert any("factors 未获允许依赖 ledger" in error for error in errors)


def test_contract_maintenance_is_read_only_by_default_and_exports_examples(tmp_path: Path) -> None:
    """缺生成文件默认报错但不建目录，显式写入后所有模型与六个正反例通过。"""
    assert maintain_contracts(tmp_path)
    assert not (tmp_path / "contracts").exists()
    assert maintain_contracts(tmp_path, write=True) == []
    # 三类消息各一正一反，共六份固定样例。
    assert len(list((tmp_path / "contracts/examples").glob("*.json"))) == 6
    # 实际模型必须接受正例并拒绝错误单位、批准候选及布尔股数。
    assert check_examples(tmp_path) == []
    assert maintain_contracts(tmp_path) == []


def test_unknown_old_schema_blocks_write_without_deleting_or_replacing(tmp_path: Path) -> None:
    """未知旧 Schema 触发显式迁移要求，写入请求也不能删除或顺带更新其他文件。"""
    assert maintain_contracts(tmp_path, write=True) == []
    # 旧文件模拟未批准的契约迁移残留。
    old = tmp_path / "contracts/RetiredMessage.schema.json"
    old.write_text('{"legacy":true}', encoding="utf-8")
    # 将当前生成文件标为待修复，验证预检会在任何写入之前失败。
    current = tmp_path / "contracts/OrderIntent.schema.json"
    current.write_text('{"sentinel":"must-remain"}', encoding="utf-8")
    errors = maintain_contracts(tmp_path, write=True)
    assert any("RetiredMessage.schema.json: 未知旧 Schema" in error for error in errors)
    assert old.read_text(encoding="utf-8") == '{"legacy":true}'
    assert current.read_text(encoding="utf-8") == '{"sentinel":"must-remain"}'


def test_sqlite_backup_and_restore_preserve_independent_fixed_facts(tmp_path: Path) -> None:
    """真实 SQLite 固定两笔事件经备份和恢复保持完整；只写 pytest 临时目录。"""
    source = tmp_path / "source.sqlite"
    # 测试使用标准 SQLite，不调用被测备份函数构造期望。
    database = sqlite3.connect(source)
    try:
        database.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, quantity INTEGER NOT NULL)")
        # 具体数量10与5不由生产投影计算。
        database.executemany("INSERT INTO events VALUES (?, ?)", [(1, 10), (2, 5)])
        # 用户状态库版本也必须由备份保留。
        database.execute("PRAGMA user_version=7")
        # 提交后模拟已停止写入的运行输出。
        database.commit()
    finally:
        database.close()
    original = verify_database(source)
    backup = tmp_path / "backup/source.sqlite"
    result = backup_database(source, backup)
    assert result["logical_sha256"] == original["logical_sha256"]
    assert result["user_version"] == "7"
    assert verify_database(source)["sha256"] == original["sha256"]
    restored = tmp_path / "restore/source.sqlite"
    backup_database(backup, restored)
    # 用独立 SQL 核对最终行和数量。
    check = sqlite3.connect(restored)
    try:
        assert check.execute("SELECT id, quantity FROM events ORDER BY id").fetchall() == [
            (1, 10),
            (2, 5),
        ]
    finally:
        check.close()
    # 第二次同名恢复必须失败，保护已经验证的输出。
    with pytest.raises(FileExistsError):
        backup_database(backup, restored)


def test_sqlite_backup_rejects_missing_or_corrupt_source(tmp_path: Path) -> None:
    """缺失或非数据库源不能生成一个假成功空库；目标目录保持未创建。"""
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "missing.sqlite", tmp_path / "missing-copy.sqlite")
    assert not (tmp_path / "missing.sqlite").exists()
    # 明确不是 SQLite 的字节模拟损坏源。
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"not-a-sqlite-database")
    with pytest.raises(sqlite3.DatabaseError):
        backup_database(broken, tmp_path / "broken-copy.sqlite")
    assert not (tmp_path / "broken-copy.sqlite").exists()
