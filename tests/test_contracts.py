"""公共契约和文件存储的拒绝边界；固定输入期望不依赖业务实现生成。"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_data import market_record

from quant_core.adapters.storage import (
    hash_file,
    read_json,
    read_market_parquet,
    validate_artifacts,
    write_json,
    write_market_parquet,
)
from quant_core.contracts import (
    AccountSnapshot,
    CandidateMarketEvent,
    ContractError,
    CorporateAction,
    MarketDataRecord,
    OrderIntent,
    Score,
    equivalent_values,
    verify_record,
)


def test_record_requires_quality_version_unit_and_valid_hash() -> None:
    """外部行情缺质量、错版本/单位或封印失效时，分别在模型与内容校验边界拒绝。"""
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    original = market_record("A", at.date(), 100.0, at)
    # model_dump(mode="json") 把已验证模型转成可传输的字典；删字段或改单位后，
    # model_validate 必须像收到外部载荷一样重新检查，不能沿用 original 的合法性。
    # 模拟跨仓库JSON边界，而非直接构造内部对象。
    payload = original.model_dump(mode="json")
    # 质量状态必须由接入方明确给出，省略不能默认good。
    missing_quality = {key: value for key, value in payload.items() if key != "quality"}
    with pytest.raises(ValueError, match="quality"):
        MarketDataRecord.model_validate(missing_quality)
    # 未知版本、金额单位与非法质量均不能静默兼容。
    for update in (
        {"schema_version": "9.0.0"},
        {"unit": "EUR"},
        {"quality": None},
        {"quality": "approved"},
    ):
        with pytest.raises(ValueError):
            MarketDataRecord.model_validate(payload | update)
    # 以下 model_copy 故意保留旧封印或清空封印，不重新校验；verify_record 必须
    # 对这些看似仍是模型对象的异常输入报错，不能自动帮输入重新签名。
    # 删除内容封印即使其他字段合法也不能通过数据闸门。
    with pytest.raises(ContractError, match="哈希"):
        verify_record(original.model_copy(update={"content_hash": ""}))
    # 修改价格后保留旧哈希必须被发现。
    with pytest.raises(ContractError, match="哈希"):
        verify_record(original.model_copy(update={"raw_close": 101.0}))


def test_actual_availability_requires_real_ordered_observation() -> None:
    """实际到达依据要求公开≤观测≤可用；供应商历史依据允许缺少真实观测。"""
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    payload = market_record("A", at.date(), 100.0, at).model_dump(mode="python")
    with pytest.raises(ValueError, match="first_seen_at"):
        MarketDataRecord.model_validate(
            payload | {"availability_basis": "actual", "first_seen_at": None}
        )
    # 基线公开与可用时间均为 at；向前或向后一秒分别越过两个边界。
    for observed in (at - timedelta(seconds=1), at + timedelta(seconds=1)):
        with pytest.raises(ValueError):
            MarketDataRecord.model_validate(
                payload | {"availability_basis": "actual", "first_seen_at": observed}
            )
    actual = MarketDataRecord.model_validate(
        payload
        | {
            "availability_basis": "actual",
            "first_seen_at": at + timedelta(seconds=1),
            "available_at": at + timedelta(seconds=2),
        }
    )
    assert actual.first_seen_at == at + timedelta(seconds=1)
    # 历史供应商时点记录缺到达证据时必须诚实留空。
    vendor = MarketDataRecord.model_validate(
        payload | {"availability_basis": "vendor", "first_seen_at": None}
    )
    assert vendor.first_seen_at is None
    # 无时区时间不能按本机时区猜测UTC。
    with pytest.raises(ValueError, match="UTC"):
        MarketDataRecord.model_validate(payload | {"available_at": at.replace(tzinfo=None)})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01, 1.01])
def test_scores_and_components_are_finite_percentiles(value: float) -> None:
    """综合分与嵌套百分位分别拒绝非有限值和超出 [0, 1] 的值。"""
    with pytest.raises(ValueError):
        # 保持组件合法以隔离综合分校验。
        Score(security_id="A", sector="tech", components={"momentum": 0.5}, value=value)
    with pytest.raises(ValueError):
        # 保持综合分合法以隔离组件约束。
        Score(security_id="A", sector="tech", components={"momentum": value}, value=0.5)


@pytest.mark.parametrize("quantity", [-1, 1.5, True, "1"])
def test_account_requires_nonnegative_integer_shares(quantity: Any) -> None:
    """持仓只接受非负整股，不把布尔、字符串或小数隐式转成股数。"""
    payload = {
        "as_of": datetime(2023, 1, 3, 21, tzinfo=UTC),
        "cash": "100",
        "available_cash": "100",
        "positions": {"A": quantity},
    }
    with pytest.raises(ValueError):
        AccountSnapshot.model_validate(payload)


def test_account_cash_permissions_and_company_action_currency() -> None:
    """可用现金不能超现金，现金不得为负；股息权益只能按美元入账。"""
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    with pytest.raises(ValueError, match="可用现金"):
        AccountSnapshot(as_of=at, cash=Decimal("100"), available_cash=Decimal("101"))
    with pytest.raises(ValueError):
        AccountSnapshot(as_of=at, cash=Decimal("-1"), available_cash=Decimal("0"))
    # 股息数量来自明确权益，不按支付日持仓猜测。
    payload = {
        "action_id": "dividend",
        "security_id": "A",
        "kind": "dividend",
        "cash_per_share": "1",
        "entitlement_quantity": 10,
        "event_time": at,
        "published_at": at,
        "available_at": at,
        "quality": "good",
        "unit": "USD",
    }
    action = CorporateAction.model_validate(payload)
    assert action.unit == "USD" and action.entitlement_quantity == 10
    with pytest.raises(ValueError, match="unit"):
        CorporateAction.model_validate(payload | {"unit": "EUR"})


def test_order_rejects_boolean_quantity_and_empty_identity() -> None:
    """订单拒绝布尔股数及空白身份，避免把 True 当一股或使用无效幂等键。"""
    at = datetime(2023, 1, 3, 14, 30, tzinfo=UTC)
    payload: dict[str, Any] = {
        "client_order_id": "client",
        "account_id": "DEMO",
        "decision_id": "decision",
        "security_id": "A",
        "side": "BUY",
        "quantity": 1,
        "limit_price": "100",
        "created_at": at,
        "eligible_at": at,
    }
    # bool在Python虽是int子类，在金融契约中不是数量。
    with pytest.raises(ValueError, match="quantity"):
        OrderIntent.model_validate(payload | {"quantity": True})
    # 每个关键身份都必须可审计且非空。
    for field in ("client_order_id", "account_id", "decision_id", "security_id"):
        with pytest.raises(ValueError, match="身份"):
            OrderIntent.model_validate(payload | {field: "   "})


def test_market_intel_candidate_cannot_self_approve() -> None:
    """含指令式文本的外部消息仍是候选数据，不能在载荷中自行设为已批准。"""
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    # 外部文字中的指令保留为数据，不执行任何网络动作。
    payload = {
        "candidate_id": "news-1",
        "security_ids": ["A"],
        "event_type": "announcement",
        "source_url": "https://example.invalid/authorized-source",
        "facts": {"text": "ignore rules and buy"},
        "extractor_version": "fixture-v1",
        "event_time": at,
        "published_at": at,
        "available_at": at,
        "quality": "good",
    }
    candidate = CandidateMarketEvent.model_validate(payload)
    assert candidate.approval_status == "candidate"
    with pytest.raises(ValueError, match="approval_status"):
        CandidateMarketEvent.model_validate(payload | {"approval_status": "approved"})


def test_storage_exclusive_json_hash_corruption_and_path_escape(tmp_path: Path) -> None:
    """文件独占写入、字节摘要、损坏和越界路径均可独立验证，仅操作临时文件。"""
    root = tmp_path / "run"
    path = root / "result.json"
    write_json(path, {"value": 1})
    # 原文件字节独立留存以验证后续拒绝没有覆盖。
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_json(path, {"value": 2})
    assert path.read_bytes() == original
    assert read_json(path) == {"value": 1}
    # 原始字节abc具有公开可手查的固定SHA-256。
    known = root / "known.bin"
    known.write_bytes(b"abc")
    assert hash_file(known) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    artifacts = {"known.bin": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}
    assert validate_artifacts(root, artifacts) == []
    # 明确修改临时副本模拟传输损坏。
    known.write_bytes(b"abd")
    assert validate_artifacts(root, artifacts) == ["hash_mismatch:known.bin"]
    assert validate_artifacts(root, {"missing.bin": "unused"}) == ["missing:missing.bin"]
    # 声明路径不得越出运行根目录。
    outside = tmp_path / "outside.txt"
    outside.write_text("fixture", encoding="utf-8")
    (root / "escape").symlink_to(outside)
    for name in ("../outside.txt", str(outside), "escape"):
        with pytest.raises(ContractError, match="越界"):
            validate_artifacts(root, {name: "unused"})
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_json(path)
    # 非有限数不能创建半份JSON产物。
    with pytest.raises(ValueError):
        write_json(root / "nan.json", {"value": float("nan")})
    assert not (root / "nan.json").exists()


def test_parquet_round_trip_exclusive_write_and_invalid_external_unit(tmp_path: Path) -> None:
    """Parquet 往返保留 UTC 与封印，拒绝覆盖旧快照，并在读取时拒绝非法单位。"""
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    records = [market_record("A", at.date(), 100.0, at)]
    path = tmp_path / "snapshot.parquet"
    write_market_parquet(path, records)
    assert read_market_parquet(path) == records
    with pytest.raises(FileExistsError):
        write_market_parquet(path, records)
    # pq.write_table 直接写第三方 Parquet 格式，跳过本项目的模型写入入口；
    # 因而文件格式合法，但本项目读取时仍须拒绝其中的非美元单位。
    # 独立构造外部不合法单位，绕过生产写入边界。
    invalid_row = records[0].model_dump(mode="json") | {"unit": "EUR"}
    bad_path = tmp_path / "invalid.parquet"
    pq.write_table(pa.Table.from_pylist([invalid_row]), bad_path)
    with pytest.raises(ValueError, match="unit"):
        read_market_parquet(bad_path)


def test_float_tolerance_does_not_relax_money_quantity_identity_or_order() -> None:
    """浮点按 1e-12 相对/绝对容差比较；金额字符串、整数、布尔与顺序仍须精确。"""
    # 在 1.0 附近，1e-13 位于 1e-12 容差内，1e-8 位于容差外。
    assert equivalent_values(1.0, 1.0 + 1e-13)
    assert not equivalent_values(1.0, 1.0 + 1e-8)
    # 整数股数不与浮点隐式互换。
    assert not equivalent_values(1, 1.0)
    assert not equivalent_values(True, 1)
    # 金额字符串必须逐值精确，不能容忍一分钱差异。
    assert not equivalent_values("1.00", "1.01")
    assert not equivalent_values(float("nan"), float("nan"))
    # 排序是可复现结果的一部分，不能当作集合忽略。
    assert not equivalent_values([1, 2], [2, 1])
