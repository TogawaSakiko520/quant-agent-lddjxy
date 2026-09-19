"""从 contracts 模型生成 Schema 与固定样例，供治理检查确认代码和跨模块格式一致。

Schema 是字段/类型约束的描述文件；默认只读比较，显式写入也只更新这些可再生成文件。
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from quant_core.adapters.storage import schema_documents
from quant_core.contracts import (
    CandidateMarketEvent,
    Contract,
    MarketDataRecord,
    OrderIntent,
    StampedRecord,
    seal_record,
    verify_record,
)

# 每类固定样例都必须有合法与非法版本。
EXAMPLE_MODELS: dict[str, type[Contract]] = {
    "MarketDataRecord": MarketDataRecord,
    "CandidateMarketEvent": CandidateMarketEvent,
    "OrderIntent": OrderIntent,
}


def example_documents() -> dict[str, dict[str, Any]]:
    """返回六个固定 JSON 样例；合法记录先封印，非法样例只改一项边界，无外部副作用。"""
    close = datetime(2023, 1, 3, 21, tzinfo=UTC)
    # 合法行情明确区分收盘与十五分钟后的可用时间。
    market = seal_record(
        MarketDataRecord(
            security_id="EXAMPLE-S1",
            session=date(2023, 1, 3),
            raw_open=100.0,
            raw_close=101.0,
            total_return_close=101.0,
            volume=1000000,
            quality="good",
            event_time=close,
            published_at=close,
            available_at=close + timedelta(minutes=15),
        )
    )
    # 外部信息始终处于候选状态，示例 URL 不会被打开。
    candidate = seal_record(
        CandidateMarketEvent(
            candidate_id="EXAMPLE-EVENT-1",
            security_ids=["EXAMPLE-S1"],
            event_type="synthetic_notice",
            source_url="https://example.invalid/notice/1",
            facts={"description": "仅用于契约校验的合成事实"},
            extractor_version="fixture-1.0.0",
            quality="good",
            event_time=close,
            published_at=close,
            available_at=close + timedelta(minutes=15),
        )
    )
    # 意图创建于收盘后，资格严格等到下一常规交易时段。
    order = OrderIntent(
        client_order_id="EXAMPLE-ORDER-1",
        account_id="DEMO",
        decision_id="EXAMPLE-DECISION-1",
        security_id="EXAMPLE-S1",
        side="BUY",
        quantity=10,
        limit_price=Decimal("102"),
        reserved_fee=Decimal("1"),
        created_at=close + timedelta(minutes=16),
        eligible_at=datetime(2023, 1, 4, 14, 30, tzinfo=UTC),
    )
    # valid[模型名] 是 model_dump(mode="json") 序列化的字段字典；金额/时刻转换为JSON可存形式。
    # 此调用不写文件。下面仅替换反例的顶层字段，不通过模型再验证，否则无法保留非法输入。
    valid = {
        "MarketDataRecord": market.model_dump(mode="json"),
        "CandidateMarketEvent": candidate.model_dump(mode="json"),
        "OrderIntent": order.model_dump(mode="json"),
    }
    # ** 展开产生独立顶层字典；嵌套内容仍共享，但这些反例只替换unit/approval_status/quantity。
    return {
        "MarketDataRecord.valid.json": valid["MarketDataRecord"],
        "MarketDataRecord.invalid.json": {**valid["MarketDataRecord"], "unit": "EUR"},
        "CandidateMarketEvent.valid.json": valid["CandidateMarketEvent"],
        "CandidateMarketEvent.invalid.json": {
            **valid["CandidateMarketEvent"],
            "approval_status": "approved",
        },
        "OrderIntent.valid.json": valid["OrderIntent"],
        "OrderIntent.invalid.json": {**valid["OrderIntent"], "quantity": True},
    }


def check_examples(root: Path) -> list[str]:
    """校验磁盘样例与运行模型及固定文本；返回诊断，拒绝反例意外变合法，不写文件。"""
    # 所有样例都有明确的生成文本，避免手改反例消除边界测试。
    # expected[文件名] 是代码定义的完整样例，actual 是磁盘解析结果；两者先比内容，再检验模型行为。
    expected = example_documents()
    errors: list[str] = []
    for name, document in expected.items():
        path = root / "contracts/examples" / name
        if not path.is_file():
            errors.append(f"contracts/examples/{name}: 样例缺失")
            # 只跳过当前缺失样例，继续收集其余文件问题；缺失不能当空字典送入模型。
            continue
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            errors.append(f"contracts/examples/{name}: 无效 JSON：{error}")
            continue
        # 固定样例文本改变需要同步需求、预期和独立复核。
        if actual != document:
            errors.append(f"contracts/examples/{name}: 固定样例漂移")
        # 文件名首段对应明确模型，不反射加载任意代码。
        model = EXAMPLE_MODELS[name.split(".")[0]]
        # 正例与反例的预期结果由文件名约定固定。
        should_pass = name.endswith(".valid.json")
        # model_validate 真正执行Pydantic字段/跨字段校验；Schema相同也不能替代这一步。
        # 返回 value 是已验证模型；只有合法来源记录还需独立核对内容哈希。
        try:
            value = model.model_validate(actual)
            # 正例带来源记录还必须通过实际内容封印。
            if should_pass and isinstance(value, StampedRecord):
                verify_record(value)
        except ValueError:
            if should_pass:
                errors.append(f"contracts/examples/{name}: 合法样例被运行契约拒绝")
        else:
            if not should_pass:
                errors.append(f"contracts/examples/{name}: 非法样例意外通过")
    return errors


def _write_generated(path: Path, document: dict[str, Any]) -> None:
    """将调用方选定的生成 JSON 写到同目录临时文件，再原子替换目标。

    仅供可再生成的契约文件使用；拒绝目标或直接父目录为符号链接。序列化、写入
    和替换错误向上传播；进入替换阶段后，无论成功与否都会尝试清理临时文件。
    """
    # 序列化先失败，避免因 NaN 或不可编码值破坏已有文件。
    content = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"拒绝生成路径中的符号链接：{path}")
    # 同目录临时文件保证单个 Schema 替换原子。
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".schema-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        # 这里只替换权威模型的生成文件，不可复用到批准快照写入。
        os.replace(temporary, path)
    finally:
        # missing_ok 只处理已经成功替换的临时文件不存在情况。
        temporary.unlink(missing_ok=True)


def maintain_contracts(root: Path, *, write: bool = False) -> list[str]:
    """核对 root 下的当前 Schema 和固定样例，返回缺失、漂移及校验诊断。

    write=False 只读；显式写入先检查未知旧 Schema 和目录符号链接，再逐个原子
    替换已知生成文件，最后重读核对。整个文件集合不是一个事务：中途 I/O 失败
    会向上传播，前面成功替换的文件仍保留，调用方须检查后重试。
    """
    directory = root / "contracts"
    # schemas[文件名] 是从当前模型生成的字段模式，包含模型说明；尚未写磁盘。
    schemas = schema_documents()
    # 未知旧 Schema 可能表示破坏性迁移，不能自动删除。
    unknown = sorted(
        path.name for path in directory.glob("*.schema.json") if path.name not in schemas
    )
    if unknown:
        return [
            f"contracts/{name}: 未知旧 Schema，需要显式迁移方案；未删除或更新生成文件"
            for name in unknown
        ]
    # 拒绝把整个生成目录重定向到其他目录。
    if directory.is_symlink() or (directory / "examples").is_symlink():
        return ["contracts: 生成目录不得为符号链接"]
    # documents 将“Schema文件”和“固定消息样例”合为 路径→JSON对象，供同一写入/比较循环使用。
    documents = {
        **{directory / name: doc for name, doc in schemas.items()},
        **{directory / "examples" / name: doc for name, doc in example_documents().items()},
    }
    # 明确 --write 才能更新生成文件，默认检查没有副作用。
    if write:
        for path, document in documents.items():
            _write_generated(path, document)
    # 检查对磁盘真实内容进行比较，不用写入行为代替验证。
    errors: list[str] = []
    for path, expected in documents.items():
        if not path.is_file():
            errors.append(f"{path.relative_to(root)}: 生成文件缺失")
            continue
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append(f"{path.relative_to(root)}: JSON 无效")
            continue
        if actual != expected:
            errors.append(f"{path.relative_to(root)}: 生成内容漂移")
    # 样例还要通过实际校验/拒绝行为，不能只匹配 JSON 文本。
    errors.extend(check_examples(root))
    return errors


def main(argv: list[str] | None = None) -> int:
    """解析维护参数并输出结果；默认检查，显式写入可能更新生成文件，返回退出码。"""
    parser = argparse.ArgumentParser(description="检查/生成共同契约 Schema 与固定正反样例")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    # 检查和写入互斥，缺省仍是检查。
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只比较，不更新文件（默认）")
    mode.add_argument(
        "--write", action="store_true", help="显式更新当前 Schema/样例；需既有变更授权"
    )
    args = parser.parse_args(argv)
    try:
        errors = maintain_contracts(args.root.resolve(), write=args.write)
    except (OSError, ValueError) as error:
        print(f"契约维护失败：{error}", file=sys.stderr)
        return 1
    for issue in errors:
        print(issue, file=sys.stderr)
    if not errors:
        print(
            "契约生成文件及六个正反样例检查通过" + ("（已显式更新）" if args.write else "（只读）")
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
