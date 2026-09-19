"""文件与契约导出适配器；所有写入显式给定路径，已批准产物默认拒绝覆盖。

JSON 保存完整契约，Parquet 保存版本化历史记录；本模块没有交易或网络能力。
"""

import hashlib
import json
import sqlite3
import tomllib
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq

from quant_core import contracts as models
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    DemoConfig,
    MarketDataRecord,
    OrderRecord,
)


def write_json(path: Path, value: Any) -> None:
    """把已准备好的 JSON 可编码内容写入新文件，供清单校验或回放读取。

    模型通常先由调用方 model_dump(mode="json") 转成字典；本函数只序列化，不验证
    契约或内容哈希。已有路径抛 FileExistsError，非法数值抛 ValueError；写入中途
    I/O 失败可能留下不完整新文件，调用方不能仅凭文件存在判断运行成功。
    """
    # 先序列化，避免无效数据留下半份文件。
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # x 模式保证不可覆盖语义由文件系统执行。
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)


def read_json(path: Path) -> Any:
    """读取 JSON 边界值；缺失或非法内容抛原异常，返回值须由调用方模型验证。"""
    return json.loads(path.read_text(encoding="utf-8"))


def hash_file(path: Path) -> str:
    """计算实际文件字节的 SHA-256 摘要；读取失败抛 OSError。"""
    # 流式读取避免为大型历史快照复制全部文件。
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: Path | None = None) -> DemoConfig:
    """读取并校验演示 TOML；空路径采用同一类型默认值，非法配置抛 ValueError。"""
    if path is None:
        return DemoConfig()
    return DemoConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


def write_market_parquet(path: Path, records: list[MarketDataRecord]) -> None:
    """按共同契约字段独占创建历史 Parquet 文件；已有路径抛 FileExistsError。"""
    # model_dump(mode="json") 将模型转成可编码字段，Arrow 将每条记录组织为表中一行；
    # 写文件不替代输入质量/哈希校验。读取后仍需重建契约并经过历史时点闸门。
    table = pa.Table.from_pylist([item.model_dump(mode="json") for item in records])
    path.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建避免覆盖已有批准数据。
    with path.open("xb") as stream:
        pq.write_table(table, stream, compression="zstd")


def read_market_parquet(path: Path) -> list[MarketDataRecord]:
    """读取 Parquet 并逐条校验行情契约；文件或字段错误向调用方传播。"""
    # Arrow 行字典里的时间、金额等由 model_validate 恢复并校验；这一步只验证模型
    # 字段约束，来源封印与历史版本选择仍由 build_snapshot 等业务入口处理。
    rows = pq.read_table(path).to_pylist()
    return [MarketDataRecord.model_validate(row) for row in rows]


def schema_documents() -> dict[str, dict[str, Any]]:
    """从权威契约模型生成 JSON Schema，返回文件名到模式内容的映射。

    模型 docstring 会成为描述字段；维护契约说明时也需比较生成文件。
    此函数只在内存生成内容，不写入或覆盖快照。
    """
    # result 的键是生成文件名，值是模型的字段/类型/约束/描述。只枚举 contracts 模块
    # 命名空间；其他模块中继承 Contract 的内部类型不自动出现在这组导出文件中。
    result: dict[str, dict[str, Any]] = {}
    for name, model in sorted(vars(models).items()):
        # 协议、导入类和基础抽象不成为跨仓库发布模型。
        if (
            isinstance(model, type)
            and issubclass(model, models.Contract)
            and model not in (models.Contract, models.StampedRecord)
        ):
            result[f"{name}.schema.json"] = model.model_json_schema()
    return result


def export_schemas(destination: Path) -> None:
    """在目标目录独占导出生成的 Schema；已有同名文件抛 FileExistsError。"""
    for name, schema in schema_documents().items():
        write_json(destination / name, schema)


def validate_artifacts(root: Path, artifacts: dict[str, str]) -> list[str]:
    """核对运行清单声明的文件内容，返回缺失或字节哈希不符的诊断列表。

    artifacts 把相对 root 的文件路径映射到预期 SHA-256；空结果只表示这些声明文件
    的字节一致，不证明清单完整或业务事实一致，后者由 application.validate_run 检查。
    越界路径抛 ContractError；原文件不会被修复或重签。
    """
    resolved_root = root.resolve()
    differences: list[str] = []
    for name, expected in sorted(artifacts.items()):
        # 拒绝绝对路径及通过 .. 或符号链接逃逸根目录。
        candidate = (root / name).resolve()
        if Path(name).is_absolute() or not candidate.is_relative_to(resolved_root):
            raise ContractError("运行清单包含越界产物路径")
        if not candidate.is_file():
            differences.append(f"missing:{name}")
        elif hash_file(candidate) != expected:
            differences.append(f"hash_mismatch:{name}")
    return differences


def read_sqlite_state(
    path: Path, kind: Literal["internal", "broker"]
) -> tuple[AccountSnapshot, list[OrderRecord]]:
    """只读核验状态库账户和订单，返回契约模型，不创建或改写数据库。

    缺数据库或表由 SQLite 异常报告，缺账户行抛 ContractError；内容损坏由模型
    验证异常报告。调用方应分别处理文件/数据库不可读和事实本身不合法的情况。
    """
    # 表名只由固定枚举选择，不能把外部字符串拼为SQL。
    state_table = "broker_state" if kind == "broker" else "state"
    order_table = "broker_orders" if kind == "broker" else "orders"
    # URI只读模式避免打开不存在的库时悄悄创建空库。
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = connection.execute(
            f"SELECT payload FROM {state_table} WHERE key='account'"
        ).fetchone()
        if row is None:
            raise ContractError("状态库缺少账户事实")
        # JSON 文本重新经过模型验证成为账户事实，再与同库订单一起交给 validate_run；
        # 此处不通过构造 SQLiteEventStore/FakeBroker 读取，避免初始化逻辑修改源数据库。
        account = AccountSnapshot.model_validate_json(row[0])
        # 订单按稳定身份排序比较，不依赖SQLite物理行顺序。
        orders = [
            OrderRecord.model_validate_json(item[0])
            for item in connection.execute(f"SELECT payload FROM {order_table} ORDER BY client_id")
        ]
        return account, orders
    finally:
        connection.close()
