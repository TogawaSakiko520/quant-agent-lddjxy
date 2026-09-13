"""使用 SQLite 备份 API 创建或恢复新数据库；不覆盖现有状态，不执行生产迁移。"""

# 命令行显式区分校验、备份与恢复到新路径。
import argparse

# 逻辑指纹比较包含表结构、数据和 schema user_version。
import hashlib

# 输出机器可读的备份证据。
import json

# SQLite 备份 API 提供单数据库的一致性复制。
import sqlite3

# 失败退出码和诊断用于操作记录。
import sys

# 明确关闭只读与目标连接，避免遗留锁。
from contextlib import closing

# 所有源和目标路径由操作者显式指定。
from pathlib import Path

# 文件字节哈希用于保留实际备份证据，不代替逻辑校验。
from quant_core.adapters.storage import hash_file


def _logical_hash(database: sqlite3.Connection) -> str:
    """返回当前只读数据库的确定性逻辑摘要；不打印账户内容或修改数据库。"""
    # SHA-256 逐句处理逻辑转储，不把账户全文留在工具输出。
    digest = hashlib.sha256()
    # 用户版本不在普通 INSERT 语句中，单独纳入指纹。
    version = database.execute("PRAGMA user_version").fetchone()
    # 同一逻辑内容但不同状态库版本不应被视为完全相同。
    digest.update(f"user_version={version[0] if version else 0}\n".encode("utf-8"))
    # 转储语句只用于计算摘要，不执行为迁移脚本。
    for statement in database.iterdump():
        # 换行分隔避免不同语句拼接产生歧义。
        digest.update((statement + "\n").encode("utf-8"))
    # 返回短指纹而非敏感的完整交易事实。
    return digest.hexdigest()


def verify_database(path: Path) -> dict[str, str]:
    """只读检查完整性并返回文件/逻辑指纹；缺失、损坏时抛错，不创建文件。"""
    # 使用绝对路径建立明确的只读 SQLite URI。
    source = path.resolve()
    # 先检查文件，禁止 sqlite.connect 为拼错的源名创建空库。
    if not source.is_file():
        # 缺失源不能通过空库完整性检查冒称恢复成功。
        raise FileNotFoundError(source)
    # mode=ro 禁止任何写入，包括意外创建不存在的库。
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as database:
        # 同一次只读事务固定完整性与逻辑摘要对应的数据库视图。
        database.execute("BEGIN")
        # SQLite 自身检查页面和索引完整性。
        integrity = database.execute("PRAGMA integrity_check").fetchall()
        # 只有唯一 ok 才表示数据库结构完整。
        if integrity != [("ok",)]:
            # 不修复损坏源，也不忽略检查结果。
            raise ValueError(f"SQLite 完整性检查失败：{source}")
        # 逻辑摘要用于验证复制前后账户/事件内容一致。
        logical = _logical_hash(database)
        # 保存数据库引擎的用户版本声明，当前工程并未假定存在自动迁移。
        version = database.execute("PRAGMA user_version").fetchone()
    # 字节哈希和逻辑摘要承担不同证据职责。
    return {
        "path": str(source),
        "sha256": hash_file(source),
        "logical_sha256": logical,
        "user_version": str(version[0] if version else 0),
        "integrity": "ok",
    }


def backup_database(source: Path, destination: Path) -> dict[str, str]:
    """一致性复制到不存在的新路径并比对逻辑指纹；异常清理自建目标，绝不覆盖旧库。"""
    # 先检查真实源内容，错误源不应留下看似有效备份。
    before = verify_database(source)
    # 归一化路径以防源与目标实际指向同一个文件。
    origin = source.resolve()
    # 目标仅可由操作者明确指定为新路径。
    target = destination.absolute()
    # 已存在的文件或符号链接都不得覆盖。
    if target.exists() or target.is_symlink() or target.resolve() == origin:
        # 不把恢复操作变成原地替换生产事实。
        raise FileExistsError(f"备份或恢复必须使用不存在的新文件：{target}")
    # 仅创建操作者明确选择的目标父目录。
    target.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建让同名目标在文件系统层得到保护。
    with target.open("xb"):
        # SQLite 随后初始化这个仅由本操作创建的空目标。
        pass
    # 失败时只清理本工具刚创建的目标，不触碰源库。
    try:
        # 源连接明确只读，防止备份过程修改交易事实。
        with closing(sqlite3.connect(origin.as_uri() + "?mode=ro", uri=True)) as read_only:
            # 新目标数据库由备份 API 写入完整结构与数据。
            with closing(sqlite3.connect(target)) as writable:
                # 单库备份使用 SQLite 一致性机制，禁止裸复制正在写入的主文件。
                read_only.backup(writable)
        # 目标必须通过相同完整性和逻辑摘要检查。
        copied = verify_database(target)
        # 复制期间源若变化，拒绝给出与预检不同的成功证据。
        if copied["logical_sha256"] != before["logical_sha256"]:
            # 操作者应先停止写入，再重试到新的路径。
            raise ValueError("源在备份期间变化或目标逻辑不一致；请停止写入后重试")
    # 数据库、文件系统或校验失败都不能留下伪成功的输出文件。
    except (OSError, ValueError, sqlite3.Error):
        # 仅删除本次独占创建且未完成验证的新目标。
        target.unlink(missing_ok=True)
        # 保留原始失败类型给 CLI 与测试。
        raise
    # 返回两侧指纹，便于交接和独立恢复核对。
    return {
        "source": str(origin),
        "destination": str(target),
        "source_sha256": before["sha256"],
        "destination_sha256": copied["sha256"],
        "logical_sha256": copied["logical_sha256"],
        "user_version": copied["user_version"],
        "integrity": "ok",
    }


def main(argv: list[str] | None = None) -> int:
    """执行只读校验或备份/恢复到新路径；打印 JSON 证据，失败返回非零退出码。"""
    # 该工具不包含迁移执行器或自动生产切换。
    parser = argparse.ArgumentParser(description="SQLite 备份、完整性校验与恢复到新目录")
    # 动作必须显式给出，避免缺参数产生写入。
    actions = parser.add_subparsers(dest="action", required=True)
    # 只读验证不需要目标路径。
    verification = actions.add_parser("verify")
    # 源文件必须存在且通过 SQLite 检查。
    verification.add_argument("--database", type=Path, required=True)
    # backup 与 restore 使用同一安全复制机制，均不覆盖目标。
    for action in ("backup", "restore"):
        # 动作名称保留在用户的实际命令记录中。
        operation = actions.add_parser(action)
        # 复制来源必须为已存在的数据库。
        operation.add_argument("--source", type=Path, required=True)
        # 备份或恢复后的数据库必须位于新文件。
        operation.add_argument("--output", type=Path, required=True)
    # 拒绝未知参数和不完整操作。
    args = parser.parse_args(argv)
    # 预期操作失败转换为明确退出码。
    try:
        # 只有 verify 是完全只读动作。
        result = (
            verify_database(args.database)
            if args.action == "verify"
            else backup_database(args.source, args.output)
        )
    # 不尝试修复或删除操作者已有文件。
    except (OSError, ValueError, sqlite3.Error) as error:
        # 输出诊断但不泄漏账户内容。
        print(f"SQLite 操作失败：{error}", file=sys.stderr)
        # 非零状态阻止后续自动迁移或发布步骤。
        return 1
    # 证据只包含路径、哈希、版本和完整性结论。
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    # 成功仅意味着当前单库复制/验证通过，不代表生产切换获得批准。
    return 0


# 直接执行模块时才开展操作者明确指定的动作。
if __name__ == "__main__":
    # 保留真实退出码供 shell 和 CI 使用。
    raise SystemExit(main())
