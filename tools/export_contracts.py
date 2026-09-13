"""维护可再生成的契约 Schema 与固定样例；默认只读检查，不触碰批准运行快照。"""

# 命令行必须显式选择写入，避免检查隐式修补差异。
import argparse

# JSON 比较忽略缩进但保持真实字段语义。
import json

# 原子替换仅用于当前权威类型的生成文件。
import os

# 错误输出与退出码供 CI 明确判断。
import sys

# 临时文件与目标同目录，保证单文件替换不会跨文件系统。
import tempfile

# 固定 UTC 时刻构造可独立核对的示例。
from datetime import UTC, date, datetime, timedelta

# 十进制金额按运行契约构造，不使用隐式字符串类型转换。
from decimal import Decimal

# 工具始终使用明确仓库路径。
from pathlib import Path

# 任意 JSON 值仅存在于序列化边界。
from typing import Any

# 运行类型是 Schema 的唯一权威来源。
from quant_core.adapters.storage import schema_documents

# 例子覆盖行情、外部候选信息与受控订单三条重要边界。
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
    # 固定普通交易日收盘不依赖系统时钟。
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
    # 序列化后非法样例可以表达运行类型明确拒绝的字段值。
    valid = {
        "MarketDataRecord": market.model_dump(mode="json"),
        "CandidateMarketEvent": candidate.model_dump(mode="json"),
        "OrderIntent": order.model_dump(mode="json"),
    }
    # 正例与反例独立字典，避免反例污染合法封印。
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
    expected = example_documents()
    # 逐个记录缺失、漂移及错误的校验结果。
    errors: list[str] = []
    # 稳定顺序方便审查和 CI 输出。
    for name, document in expected.items():
        # 示例与 Schema 分目录，避免误认为批准数据快照。
        path = root / "contracts/examples" / name
        # 缺失样例即契约交付不完整。
        if not path.is_file():
            # 不自动生成以掩盖遗漏提交。
            errors.append(f"contracts/examples/{name}: 样例缺失")
            # 文件不存在时无法进行语义校验。
            continue
        # JSON 损坏单独报告，不影响其他样例诊断。
        try:
            # 从磁盘读取真实待审核内容。
            actual = json.loads(path.read_text(encoding="utf-8"))
        # 无效 JSON 不能被默认为合法空对象。
        except json.JSONDecodeError as error:
            # 保留具体文件与格式错误。
            errors.append(f"contracts/examples/{name}: 无效 JSON：{error}")
            # 没有可用 JSON 时跳过模型校验。
            continue
        # 固定样例文本改变需要同步需求、预期和独立复核。
        if actual != document:
            # 字段变化不能被只检查类型所忽略。
            errors.append(f"contracts/examples/{name}: 固定样例漂移")
        # 文件名首段对应明确模型，不反射加载任意代码。
        model = EXAMPLE_MODELS[name.split(".")[0]]
        # 正例与反例的预期结果由文件名约定固定。
        should_pass = name.endswith(".valid.json")
        # 使用运行验证，而非只验证 Schema 的部分结构约束。
        try:
            # 外部消息首先执行字段、UTC、版本和状态约束。
            value = model.model_validate(actual)
            # 正例带来源记录还必须通过实际内容封印。
            if should_pass and isinstance(value, StampedRecord):
                # 即使结构合法，哈希不一致也不能作为合法样例。
                verify_record(value)
        # 模型或封印拒绝是反例的正确结果。
        except ValueError:
            # 正例失败则契约或样例同步有误。
            if should_pass:
                # 不更新测试预期让坏正例通过。
                errors.append(f"contracts/examples/{name}: 合法样例被运行契约拒绝")
        # 没有异常时必须确认本来就预期合法。
        else:
            # 反例意外通过意味着某项边界可能被放松。
            if not should_pass:
                # 禁止默默接受 approved 候选或布尔股数等输入。
                errors.append(f"contracts/examples/{name}: 非法样例意外通过")
    # 返回完整机械证据，不授予契约发布许可。
    return errors


def _write_generated(path: Path, document: dict[str, Any]) -> None:
    """原子更新已明确列出的生成 JSON；不处理运行快照，I/O 错误原样抛出。"""
    # 序列化先失败，避免因 NaN 或不可编码值破坏已有文件。
    content = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    # 只为明确生成路径创建父目录。
    path.parent.mkdir(parents=True, exist_ok=True)
    # 拒绝通过符号链接改写其他文件或目录。
    if path.is_symlink() or path.parent.is_symlink():
        # 生成接口不应扩展成任意路径覆盖入口。
        raise ValueError(f"拒绝生成路径中的符号链接：{path}")
    # 同目录临时文件保证单个 Schema 替换原子。
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".schema-", delete=False
    ) as stream:
        # 记录需要在异常情况下清理的自有临时文件。
        temporary = Path(stream.name)
        # 将完整内容写入尚未公开的临时文件。
        stream.write(content)
    # 文件关闭后才替换正式生成文件。
    try:
        # 这里只替换权威模型的生成文件，不可复用到批准快照写入。
        os.replace(temporary, path)
    # 替换失败时清理本工具刚创建的临时文件。
    finally:
        # missing_ok 只处理已经成功替换的临时文件不存在情况。
        temporary.unlink(missing_ok=True)


def maintain_contracts(root: Path, *, write: bool = False) -> list[str]:
    """检查或显式更新当前 Schema/样例；未知旧 Schema 阻止全部写入且保留原文件。"""
    # 明确仓库根目录，生成路径固定为其 contracts 子目录。
    directory = root / "contracts"
    # 模型生成的文件名是唯一允许更新的 Schema 集合。
    schemas = schema_documents()
    # 未知旧 Schema 可能表示破坏性迁移，不能自动删除。
    unknown = sorted(
        path.name for path in directory.glob("*.schema.json") if path.name not in schemas
    )
    # 先检查未知文件，确保不会更新一半后才发现迁移未批准。
    if unknown:
        # 给出明确人工迁移入口，禁止删旧文件让检查直接通过。
        return [
            f"contracts/{name}: 未知旧 Schema，需要显式迁移方案；未删除或更新生成文件"
            for name in unknown
        ]
    # 拒绝把整个生成目录重定向到其他目录。
    if directory.is_symlink() or (directory / "examples").is_symlink():
        # 路径错误发生在任何写入之前。
        return ["contracts: 生成目录不得为符号链接"]
    # 当前模型与固定样例都作为可再生成源文件维护。
    documents = {
        **{directory / name: doc for name, doc in schemas.items()},
        **{directory / "examples" / name: doc for name, doc in example_documents().items()},
    }
    # 明确 --write 才能更新生成文件，默认检查没有副作用。
    if write:
        # 逐个文件使用原子替换，不覆盖任何批准运行产物。
        for path, document in documents.items():
            # 写入范围由上面的固定映射决定，不能从 JSON 内容扩展路径。
            _write_generated(path, document)
    # 检查对磁盘真实内容进行比较，不用写入行为代替验证。
    errors: list[str] = []
    # Schema 与样例都必须存在且与源定义一致。
    for path, expected in documents.items():
        # 不把空目录或漏提交当作成功。
        if not path.is_file():
            # 默认检查不会生成缺少的文件。
            errors.append(f"{path.relative_to(root)}: 生成文件缺失")
            # 缺失文件无需继续解析。
            continue
        # 无效 JSON 单独报告，保留其他差异。
        try:
            # 比较对象语义，格式约定由写入器固定。
            actual = json.loads(path.read_text(encoding="utf-8"))
        # 人工误改文件不能触发自动修补。
        except json.JSONDecodeError:
            # 明确提示需要审查生成来源。
            errors.append(f"{path.relative_to(root)}: JSON 无效")
            # 无内容可进行后续相等比较。
            continue
        # 类型或固定样例变化需显式同步。
        if actual != expected:
            # 不将差异自动当成已获授权的契约升级。
            errors.append(f"{path.relative_to(root)}: 生成内容漂移")
    # 样例还要通过实际校验/拒绝行为，不能只匹配 JSON 文本。
    errors.extend(check_examples(root))
    # 空列表表示当前生成文件与运行定义及固定例子一致。
    return errors


def main(argv: list[str] | None = None) -> int:
    """解析维护参数并输出结果；默认检查，显式写入可能更新生成文件，返回退出码。"""
    # 参数只影响本地生成文件维护，不访问资金或网络。
    parser = argparse.ArgumentParser(description="检查/生成共同契约 Schema 与固定正反样例")
    # 默认仓库由工具所在路径决定，不依赖任意工作目录。
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    # 检查和写入互斥，缺省仍是检查。
    mode = parser.add_mutually_exclusive_group()
    # --check 是显式表达默认只读行为。
    mode.add_argument("--check", action="store_true", help="只比较，不更新文件（默认）")
    # --write 只更新当前权威生成文件，不删除未知旧 Schema。
    mode.add_argument(
        "--write", action="store_true", help="显式更新当前 Schema/样例；需既有变更授权"
    )
    # 只解析明确的选项，拒绝未知维护动作。
    args = parser.parse_args(argv)
    # 文件系统错误应给出短诊断和非零退出码。
    try:
        # 执行默认只读或显式写入模式。
        errors = maintain_contracts(args.root.resolve(), write=args.write)
    # I/O 与生成路径错误不能打印成功。
    except (OSError, ValueError) as error:
        # 错误送标准错误，供 CI 保存。
        print(f"契约维护失败：{error}", file=sys.stderr)
        # 非零返回值明确阻止通过。
        return 1
    # 所有差异逐条展示，无敏感数据或账户凭证。
    for issue in errors:
        # 诊断是文件/字段边界，不输出外部原文。
        print(issue, file=sys.stderr)
    # 只有没有差异时才打印对应模式成功。
    if not errors:
        # 说明是生成文件一致，不意味着已批准发布或交易。
        print(
            "契约生成文件及六个正反样例检查通过" + ("（已显式更新）" if args.write else "（只读）")
        )
    # 默认检查发现差异时保持非零退出。
    return 1 if errors else 0


# 直接模块执行使用退出码；导入工具不会触发维护。
if __name__ == "__main__":
    # 交给系统保留严格的命令成功或失败状态。
    raise SystemExit(main())
