"""公共契约和文件存储的拒绝边界；固定输入期望不依赖业务实现生成。"""

# JSON损坏使用标准异常核验。
import json

# 所有合法样本使用明确UTC。
from datetime import UTC, datetime, timedelta

# 美元金额不使用二进制浮点。
from decimal import Decimal

# 文件写入限制在pytest临时目录。
from pathlib import Path

# 非法外部消息使用明确边界字典。
from typing import Any

# 测试Parquet外部数据格式及非法单位输入。
import pyarrow as pa

# 文件格式写入不调用生产序列化作为测试预期。
import pyarrow.parquet as pq

# 参数化拒绝测试和临时路径。
import pytest

# 固定合法行情工厂只负责输入装配。
from test_data import market_record

# 真实存储入口用于独占、回读和内容校验。
from quant_core.adapters.storage import (
    hash_file,
    read_json,
    read_market_parquet,
    validate_artifacts,
    write_json,
    write_market_parquet,
)

# 公共模型是唯一权威，非法payload必须通过真实入口验证。
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
    """外部行情缺质量、错版本/单位和缺封印分别失败；不伪造通过状态，无副作用。"""
    # 先构造独立已封印美元记录。
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    # 固定100美元价格。
    original = market_record("A", at.date(), 100.0, at)
    # 模拟跨仓库JSON边界，而非直接构造内部对象。
    payload = original.model_dump(mode="json")
    # 质量状态必须由接入方明确给出，省略不能默认good。
    missing_quality = {key: value for key, value in payload.items() if key != "quality"}
    # 错误在公开输入边界拒绝。
    with pytest.raises(ValueError, match="quality"):
        # 缺质量的历史记录不能进入正常交易数据。
        MarketDataRecord.model_validate(missing_quality)
    # 未知版本、金额单位与非法质量均不能静默兼容。
    for update in (
        {"schema_version": "9.0.0"},
        {"unit": "EUR"},
        {"quality": None},
        {"quality": "approved"},
    ):
        # 各非法字段独立测试。
        with pytest.raises(ValueError):
            # 模型必须拒绝该外部payload。
            MarketDataRecord.model_validate(payload | update)
    # 删除内容封印即使其他字段合法也不能通过数据闸门。
    with pytest.raises(ContractError, match="哈希"):
        # 不自动重签未知来源的输入。
        verify_record(original.model_copy(update={"content_hash": ""}))
    # 修改价格后保留旧哈希必须被发现。
    with pytest.raises(ContractError, match="哈希"):
        # 明确模拟记录传输后被篡改。
        verify_record(original.model_copy(update={"raw_close": 101.0}))


def test_actual_availability_requires_real_ordered_observation() -> None:
    """actual要求真实观测且public<=first_seen<=available；历史vendor允许未知观测，无副作用。"""
    # 原始材料在固定收盘时公开。
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    # 此条合成记录用于构造独立时间关系反例。
    payload = market_record("A", at.date(), 100.0, at).model_dump(mode="python")
    # 真实到达记录不得凭空省略first_seen。
    with pytest.raises(ValueError, match="first_seen_at"):
        # 实际到达依据无法用vendor默认值代替。
        MarketDataRecord.model_validate(
            payload | {"availability_basis": "actual", "first_seen_at": None}
        )
    # 实际观测不得早于公开，也不得晚于可用。
    for observed in (at - timedelta(seconds=1), at + timedelta(seconds=1)):
        # 每个时间反例只改变实际观测字段。
        with pytest.raises(ValueError):
            # 无效先后顺序必须拒绝。
            MarketDataRecord.model_validate(
                payload | {"availability_basis": "actual", "first_seen_at": observed}
            )
    # 正确先后关系可以保留真实观测证据。
    actual = MarketDataRecord.model_validate(
        payload
        | {
            "availability_basis": "actual",
            "first_seen_at": at + timedelta(seconds=1),
            "available_at": at + timedelta(seconds=2),
        }
    )
    # 返回值不篡改用户注入的实际观测时间。
    assert actual.first_seen_at == at + timedelta(seconds=1)
    # 历史供应商时点记录缺到达证据时必须诚实留空。
    vendor = MarketDataRecord.model_validate(
        payload | {"availability_basis": "vendor", "first_seen_at": None}
    )
    # 不以公开时间冒充本系统真实首次观测。
    assert vendor.first_seen_at is None
    # 无时区时间不能按本机时区猜测UTC。
    with pytest.raises(ValueError, match="UTC"):
        # 模型统一拒绝含糊的时点。
        MarketDataRecord.model_validate(payload | {"available_at": at.replace(tzinfo=None)})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01, 1.01])
def test_scores_and_components_are_finite_percentiles(value: float) -> None:
    """综合分与嵌套百分位均必须有限且位于0至1；返回通过/异常由独立样本决定。"""
    # 综合分字段不能接纳NaN、无穷或越界百分位。
    with pytest.raises(ValueError):
        # 保持组件合法以隔离综合分校验。
        Score(security_id="A", sector="tech", components={"momentum": 0.5}, value=value)
    # 嵌套组件也不能躲过基类有限值检查。
    with pytest.raises(ValueError):
        # 保持综合分合法以隔离组件约束。
        Score(security_id="A", sector="tech", components={"momentum": value}, value=0.5)


@pytest.mark.parametrize("quantity", [-1, 1.5, True, "1"])
def test_account_requires_nonnegative_integer_shares(quantity: Any) -> None:
    """持仓数量只接受非负整股，不把bool/字符串/小数隐式转成股数，无副作用。"""
    # 固定合法账户的其他字段。
    payload = {
        "as_of": datetime(2023, 1, 3, 21, tzinfo=UTC),
        "cash": "100",
        "available_cash": "100",
        "positions": {"A": quantity},
    }
    # 数量类型或符号错误在账户边界被阻断。
    with pytest.raises(ValueError):
        # 不借下游风控修补不合法持仓。
        AccountSnapshot.model_validate(payload)


def test_account_cash_permissions_and_company_action_currency() -> None:
    """可用现金不得超现金或为负，现金公司行动明确只接受美元，无副作用。"""
    # 固定账户时点。
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    # 可用购买力不能扩为额外授信。
    with pytest.raises(ValueError, match="可用现金"):
        # 总现金100却声称可用101属于契约冲突。
        AccountSnapshot(as_of=at, cash=Decimal("100"), available_cash=Decimal("101"))
    # 无杠杆账户不能以负现金当作正常状态。
    with pytest.raises(ValueError):
        # 精确十进制仍需要非负约束。
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
    # 合法美元权益事件必须接受。
    action = CorporateAction.model_validate(payload)
    # 单位没有被变为比例或其他币种。
    assert action.unit == "USD" and action.entitlement_quantity == 10
    # 其他币种需要明确新契约，不能直接进入美元账本。
    with pytest.raises(ValueError, match="unit"):
        # 单位错误不得由现金数字看起来合理掩盖。
        CorporateAction.model_validate(payload | {"unit": "EUR"})


def test_order_rejects_boolean_quantity_and_empty_identity() -> None:
    """订单股数不接受bool，幂等和证券身份不允许空白，无外部副作用。"""
    # 固定合法执行时刻。
    at = datetime(2023, 1, 3, 14, 30, tzinfo=UTC)
    # 正常整股订单作为外部payload基线。
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
        # 不允许True被解释成一股。
        OrderIntent.model_validate(payload | {"quantity": True})
    # 每个关键身份都必须可审计且非空。
    for field in ("client_order_id", "account_id", "decision_id", "security_id"):
        # 空白比空字符串同样不构成合法身份。
        with pytest.raises(ValueError, match="身份"):
            # 禁止空键绕过幂等和账户范围。
            OrderIntent.model_validate(payload | {field: "   "})


def test_market_intel_candidate_cannot_self_approve() -> None:
    """外部候选只有candidate状态，不能通过payload自我批准或触发交易，无副作用。"""
    # 材料时间只作为不可信候选元数据。
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
    # 合法候选应仅保持待审批状态。
    candidate = CandidateMarketEvent.model_validate(payload)
    # 模型没有将外部文字解释成开发或交易指令。
    assert candidate.approval_status == "candidate"
    # 外部生产者没有自行授予批准状态的字段权限。
    with pytest.raises(ValueError, match="approval_status"):
        # 未批准数据不能直接进入已批准因子表。
        CandidateMarketEvent.model_validate(payload | {"approval_status": "approved"})


def test_storage_exclusive_json_hash_corruption_and_path_escape(tmp_path: Path) -> None:
    """文件独占写入、字节摘要、损坏和越界路径均可独立验证，仅操作临时文件。"""
    # 本次运行根目录由测试显式创建。
    root = tmp_path / "run"
    # 合法JSON产物首次创建。
    path = root / "result.json"
    # 写入明确小样本，副作用只在临时目录。
    write_json(path, {"value": 1})
    # 原文件字节独立留存以验证后续拒绝没有覆盖。
    original = path.read_bytes()
    # 第二次同路径创建必须失败。
    with pytest.raises(FileExistsError):
        # 不以新内容覆盖批准运行产物。
        write_json(path, {"value": 2})
    # 失败没有暗中修改原始产物。
    assert path.read_bytes() == original
    # 标准JSON回读必须得到初始内容。
    assert read_json(path) == {"value": 1}
    # 原始字节abc具有公开可手查的固定SHA-256。
    known = root / "known.bin"
    # 固定输入用于独立验证生产哈希函数。
    known.write_bytes(b"abc")
    # 不调用被测hash_file生成自己的预期摘要。
    assert hash_file(known) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    # 合法声明记录的是当前原始产物摘要。
    artifacts = {"known.bin": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}
    # 完整未损坏文件通过校验。
    assert validate_artifacts(root, artifacts) == []
    # 明确修改临时副本模拟传输损坏。
    known.write_bytes(b"abd")
    # 校验只报告差异，不尝试修正原文件。
    assert validate_artifacts(root, artifacts) == ["hash_mismatch:known.bin"]
    # 缺失文件也不能被静默创建。
    assert validate_artifacts(root, {"missing.bin": "unused"}) == ["missing:missing.bin"]
    # 声明路径不得越出运行根目录。
    outside = tmp_path / "outside.txt"
    # 测试用外部文件没有真实敏感内容。
    outside.write_text("fixture", encoding="utf-8")
    # 符号链接也不能成为逃逸手段。
    (root / "escape").symlink_to(outside)
    # 三类越界都应在读取越界文件之前失败。
    for name in ("../outside.txt", str(outside), "escape"):
        # 统一验证路径约束。
        with pytest.raises(ContractError, match="越界"):
            # 清单不能授予任意文件读取能力。
            validate_artifacts(root, {name: "unused"})
    # 非法JSON应暴露标准解析错误。
    path.write_text("{broken", encoding="utf-8")
    # 损坏输入不允许当作空字典继续。
    with pytest.raises(json.JSONDecodeError):
        # 不静默回退默认配置或空账户。
        read_json(path)
    # 非有限数不能创建半份JSON产物。
    with pytest.raises(ValueError):
        # 序列化必须发生在独占文件创建之前。
        write_json(root / "nan.json", {"value": float("nan")})
    # 无效产物没有残留假成功文件。
    assert not (root / "nan.json").exists()


def test_parquet_round_trip_exclusive_write_and_invalid_external_unit(tmp_path: Path) -> None:
    """Parquet回读重新验证模型，保留UTC与封印，不覆盖旧快照，仅操作临时文件。"""
    # 已封印原始记录有明确价格与来源时点。
    at = datetime(2023, 1, 3, 21, tzinfo=UTC)
    # 一行足以独立验证格式往返，不需要大规模假数据。
    records = [market_record("A", at.date(), 100.0, at)]
    # 输出位置显式位于临时目录。
    path = tmp_path / "snapshot.parquet"
    # 首次写入完整历史记录。
    write_market_parquet(path, records)
    # 读取必须恢复真实类型与原记录值。
    assert read_market_parquet(path) == records
    # 已批准快照禁止覆盖。
    with pytest.raises(FileExistsError):
        # 重复写入即使内容相同仍需新运行目录。
        write_market_parquet(path, records)
    # 独立构造外部不合法单位，绕过生产写入边界。
    invalid_row = records[0].model_dump(mode="json") | {"unit": "EUR"}
    # 只修改新的坏样本文件，不破坏原快照。
    bad_path = tmp_path / "invalid.parquet"
    # 外部格式本身合法，但数据契约不合法。
    pq.write_table(pa.Table.from_pylist([invalid_row]), bad_path)
    # 回读边界必须拒绝其他币种而非把它解释为美元。
    with pytest.raises(ValueError, match="unit"):
        # 不依赖交易代码才发现单位错误。
        read_market_parquet(bad_path)


def test_float_tolerance_does_not_relax_money_quantity_identity_or_order() -> None:
    """仅float采用1e-12容差，金额字符串、整数、布尔和顺序保持精确，无副作用。"""
    # 数学重算中的极小浮点舍入被明确定义为等价。
    assert equivalent_values(1.0, 1.0 + 1e-13)
    # 超出计划容差的真实数值变化不能被抹掉。
    assert not equivalent_values(1.0, 1.0 + 1e-8)
    # 整数股数不与浮点隐式互换。
    assert not equivalent_values(1, 1.0)
    # bool状态与整数1不是同一事实。
    assert not equivalent_values(True, 1)
    # 金额字符串必须逐值精确，不能容忍一分钱差异。
    assert not equivalent_values("1.00", "1.01")
    # 两个NaN也不是合法相同结果。
    assert not equivalent_values(float("nan"), float("nan"))
    # 排序是可复现结果的一部分，不能当作集合忽略。
    assert not equivalent_values([1, 2], [2, 1])
