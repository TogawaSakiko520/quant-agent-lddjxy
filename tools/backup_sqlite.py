"""在运行停止写入后，用 SQLite 备份 API 复制状态库并输出可核对摘要。

备份与恢复都只写新数据库；输出供维护者核对，不将恢复库自动接入执行服务。
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from quant_core.adapters.storage import hash_file


def _logical_hash(database: sqlite3.Connection) -> str:
    """返回当前只读数据库的确定性逻辑摘要；不打印账户内容或修改数据库。"""
    digest = hashlib.sha256()
    # 用户版本不在普通 INSERT 语句中，单独纳入指纹。
    version = database.execute("PRAGMA user_version").fetchone()
    digest.update(f"user_version={version[0] if version else 0}\n".encode("utf-8"))
    # iterdump 输出重建表结构与行内容的SQL文字；摘要描述逻辑内容，不依赖数据库文件页布局。
    for statement in database.iterdump():
        # 换行分隔避免不同语句拼接产生歧义。
        digest.update((statement + "\n").encode("utf-8"))
    return digest.hexdigest()


def verify_database(path: Path) -> dict[str, str]:
    """只读校验数据库，返回物理文件摘要、逻辑摘要及用户版本。

    path 必须为已有数据库；缺失抛 FileNotFoundError，结构损坏抛 ValueError
    或 sqlite3.Error。完整性与逻辑摘要使用同一事务视图；文件摘要在事务结束后
    读取，因此需要先停写才能将两类摘要解释为同一状态的证据。
    """
    source = path.resolve()
    # 先检查文件，禁止 sqlite.connect 为拼错的源名创建空库。
    if not source.is_file():
        raise FileNotFoundError(source)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as database:
        # closing 退出时只负责关连接；这里不是存储适配器“正常退出提交”的写事务上下文。
        # mode=ro 禁止写库，BEGIN 固定随后多次读取视图，连接关闭时释放它。
        # 同一次只读事务固定完整性与逻辑摘要对应的数据库视图。
        database.execute("BEGIN")
        integrity = database.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise ValueError(f"SQLite 完整性检查失败：{source}")
        logical = _logical_hash(database)
        version = database.execute("PRAGMA user_version").fetchone()
    # sha256 是文件字节指纹，logical_sha256 是表结构/内容指纹；复制可改变页布局，
    # 所以下游 backup_database 比逻辑摘要，不能要求源/目标字节相同。
    return {
        "path": str(source),
        "sha256": hash_file(source),
        "logical_sha256": logical,
        "user_version": str(version[0] if version else 0),
        "integrity": "ok",
    }


def backup_database(source: Path, destination: Path) -> dict[str, str]:
    """将 source 一致性复制到新的 destination，核对逻辑内容并返回两侧摘要。

    调用方应先停止源库写入；预检与复制使用不同连接，摘要不一致时拒绝成功。
    目标已存在抛 FileExistsError；校验或 I/O 失败向上传播，只清理本次独占创建的
    目标。会创建目标父目录；不会恢复或迁移到已有数据库。
    """
    # 先检查真实源内容，错误源不应留下看似有效备份。
    # before/copied结果都是校验证据字典，不是账户快照；只用摘要判断本次复制是否保存同样内容。
    before = verify_database(source)
    origin = source.resolve()
    target = destination.absolute()
    # 已存在的文件或符号链接都不得覆盖。
    if target.exists() or target.is_symlink() or target.resolve() == origin:
        raise FileExistsError(f"备份或恢复必须使用不存在的新文件：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建让同名目标在文件系统层得到保护。
    with target.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(origin.as_uri() + "?mode=ro", uri=True)) as read_only:
            with closing(sqlite3.connect(target)) as writable:
                # 单库备份使用 SQLite 一致性机制，禁止裸复制正在写入的主文件。
                read_only.backup(writable)
        copied = verify_database(target)
        # 复制结果与预检的逻辑摘要必须一致；这不证明其间源库从未发生变化。
        if copied["logical_sha256"] != before["logical_sha256"]:
            raise ValueError("源在备份期间变化或目标逻辑不一致；请停止写入后重试")
    except (OSError, ValueError, sqlite3.Error):
        # 仅删除本次独占创建且未完成验证的新目标。
        target.unlink(missing_ok=True)
        raise
    # 此结果交维护CLI打印；不返回数据库连接，也不会令交易服务改用新库。
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
    parser = argparse.ArgumentParser(description="SQLite 备份、完整性校验与恢复到新目录")
    actions = parser.add_subparsers(dest="action", required=True)
    verification = actions.add_parser("verify")
    verification.add_argument("--database", type=Path, required=True)
    # backup 与 restore 使用同一安全复制机制，均不覆盖目标。
    for action in ("backup", "restore"):
        operation = actions.add_parser(action)
        operation.add_argument("--source", type=Path, required=True)
        operation.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (
            verify_database(args.database)
            if args.action == "verify"
            else backup_database(args.source, args.output)
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"SQLite 操作失败：{error}", file=sys.stderr)
        return 1
    # 证据只包含路径、哈希、版本和完整性结论。
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
