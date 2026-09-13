"""文件与契约导出适配器；所有写入显式给定路径，已批准产物默认拒绝覆盖。

JSON 保存完整契约，Parquet 保存版本化历史记录；本模块没有交易或网络能力。
"""

# 哈希对实际文件字节计算，用于检测产物损坏。
import hashlib

# 标准 JSON 是跨仓库契约格式。
import json

# SQLite只读连接用于验证两份独立财务事实，不触发写入。
import sqlite3

# TOML 用于可维护的演示配置。
import tomllib

# 文件边界始终使用显式路径。
from pathlib import Path

# Any 只存在于序列化边界，进入业务前必须经模型校验。
from typing import Any, Literal

# Parquet 使用固定依赖的 Arrow 实现。
import pyarrow as pa

# 独立导入文件读写接口。
import pyarrow.parquet as pq

# 模型模块是 JSON Schema 唯一来源。
from quant_core import contracts as models

# 常用对象显式列出，避免业务中到处传任意字典。
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    DemoConfig,
    MarketDataRecord,
    OrderRecord,
)


def write_json(path: Path, value: Any) -> None:
    """独占创建 UTF-8 JSON；存在时抛 FileExistsError，非法数值抛 ValueError，有文件副作用。"""
    # 先序列化，避免无效数据留下半份文件。
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    # 输出目录由用户选定，可递归建立。
    path.parent.mkdir(parents=True, exist_ok=True)
    # x 模式保证不可覆盖语义由文件系统执行。
    with path.open("x", encoding="utf-8") as stream:
        # 保存实际文本，供人和 AI 审计。
        stream.write(content)


def read_json(path: Path) -> Any:
    """读取 JSON 边界值；缺失或非法内容抛原异常，返回值须由调用方模型验证。"""
    # 明确 UTF-8 编码，避免机器区域设置影响结果。
    return json.loads(path.read_text(encoding="utf-8"))


def hash_file(path: Path) -> str:
    """返回文件字节 SHA-256；读取失败抛 OSError，无写入副作用。"""
    # 流式读取避免为大型历史快照复制全部文件。
    digest = hashlib.sha256()
    # 文件句柄在退出上下文时关闭。
    with path.open("rb") as stream:
        # 分块读取直到文件结束。
        while block := stream.read(1024 * 1024):
            # 只追加实际读到的字节。
            digest.update(block)
    # 返回可直接保存于运行清单的摘要。
    return digest.hexdigest()


def load_config(path: Path | None = None) -> DemoConfig:
    """读取并校验演示 TOML；空路径采用同一类型默认值，非法配置抛 ValueError。"""
    # CLI 不给配置时无需读取不确定的工作目录文件。
    if path is None:
        # 所有默认值都来自公共配置模型。
        return DemoConfig()
    # TOML 的解析结果必须通过严格契约验证。
    return DemoConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


def write_market_parquet(path: Path, records: list[MarketDataRecord]) -> None:
    """独占创建历史 Parquet；字段按共同契约序列化，存在抛 FileExistsError，有文件副作用。"""
    # 先把已验证对象转换为固定的列结构。
    table = pa.Table.from_pylist([item.model_dump(mode="json") for item in records])
    # 只在明确输出路径建立父目录。
    path.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建避免覆盖已有批准数据。
    with path.open("xb") as stream:
        # 压缩不改变内容语义，实际字节哈希仍留存。
        pq.write_table(table, stream, compression="zstd")


def read_market_parquet(path: Path) -> list[MarketDataRecord]:
    """读取 Parquet 并逐条验证契约；返回类型化记录，错误明确抛出，无写入副作用。"""
    # 文件格式只存在于数据接入边界。
    rows = pq.read_table(path).to_pylist()
    # 每条数据重新检查时区、版本、价格、单位及字段结构。
    return [MarketDataRecord.model_validate(row) for row in rows]


def schema_documents() -> dict[str, dict[str, Any]]:
    """在内存生成全部公共模型 Schema；返回文件名映射，不修改仓库。"""
    # 新公共类型自动进入统一导出，避免遗漏同步维护。
    result: dict[str, dict[str, Any]] = {}
    # 只扫描本项目权威契约模块。
    for name, model in sorted(vars(models).items()):
        # 协议、导入类和基础抽象不成为跨仓库发布模型。
        if (
            isinstance(model, type)
            and issubclass(model, models.Contract)
            and model not in (models.Contract, models.StampedRecord)
        ):
            # 默认校验 Schema 与入口验证语义一致。
            result[f"{name}.schema.json"] = model.model_json_schema()
    # 调用者可比较已提交的 Schema，检测漂移。
    return result


def export_schemas(destination: Path) -> None:
    """显式导出生成 Schema；已有同名文件抛错，更新需经维护流程安排，有文件副作用。"""
    # 每个生成文件都可独立审查和固定版本。
    for name, schema in schema_documents().items():
        # 使用统一不可覆盖写入规则。
        write_json(destination / name, schema)


def validate_artifacts(root: Path, artifacts: dict[str, str]) -> list[str]:
    """检查相对路径与文件哈希；返回差异列表，危险路径抛 ContractError，不修复原文件。"""
    # 先标准化可信运行根目录。
    resolved_root = root.resolve()
    # 集中报告所有缺失和损坏，便于恢复手册定位。
    differences: list[str] = []
    # 清单中的每个路径都必须位于本次运行内部。
    for name, expected in sorted(artifacts.items()):
        # 拒绝绝对路径及通过 .. 或符号链接逃逸根目录。
        candidate = (root / name).resolve()
        # 运行清单不能授予读取其他目录的能力。
        if Path(name).is_absolute() or not candidate.is_relative_to(resolved_root):
            # 不访问任何越界文件。
            raise ContractError("运行清单包含越界产物路径")
        # 缺失产物与损坏产物分别表达。
        if not candidate.is_file():
            # 不静默创建缺失文件。
            differences.append(f"missing:{name}")
        # 仅对存在的普通文件计算摘要。
        elif hash_file(candidate) != expected:
            # 原始产物必须由正确输入重建到新目录。
            differences.append(f"hash_mismatch:{name}")
    # 空列表表示所有声明产物字节一致。
    return differences


def read_sqlite_state(
    path: Path, kind: Literal["internal", "broker"]
) -> tuple[AccountSnapshot, list[OrderRecord]]:
    """只读核验状态库账户和订单；返回类型化事实，缺失抛 ContractError，不创建或写数据库。"""
    # 表名只由固定枚举选择，不能把外部字符串拼为SQL。
    state_table = "broker_state" if kind == "broker" else "state"
    # 内外部实现有各自独立订单表。
    order_table = "broker_orders" if kind == "broker" else "orders"
    # URI只读模式避免打开不存在的库时悄悄创建空库。
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    # 无论解析是否成功都要关闭只读句柄。
    try:
        # 只运行固定表名的只读查询，不执行数据库存储内容。
        row = connection.execute(
            f"SELECT payload FROM {state_table} WHERE key='account'"
        ).fetchone()
        # 缺少账户不是合法空账户。
        if row is None:
            # 让运行校验明确失败。
            raise ContractError("状态库缺少账户事实")
        # 数据库JSON和导出JSON遵守同一模型。
        account = AccountSnapshot.model_validate_json(row[0])
        # 订单按稳定身份排序比较，不依赖SQLite物理行顺序。
        orders = [
            OrderRecord.model_validate_json(item[0])
            for item in connection.execute(f"SELECT payload FROM {order_table} ORDER BY client_id")
        ]
        # 返回独立读到的账户及订单。
        return account, orders
    # 必须释放数据库文件，不更改其journal模式。
    finally:
        # 关闭只读连接没有交易副作用。
        connection.close()
