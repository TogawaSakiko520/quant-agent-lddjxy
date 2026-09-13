"""账务独立数值期望、双重去重与公司行动测试；不调用被测公式生成期望。"""

# 显式 UTC 常量使测试与机器时间无关。
from datetime import UTC, datetime

# 美元期望直接写十进制常量。
from decimal import Decimal

# 临时数据库路径由 pytest 提供。
from pathlib import Path

# 异常断言与临时目录由测试框架提供。
import pytest

# 测试仅从公开存储和契约入口驱动。
from quant_core.adapters.sqlite_store import SQLiteEventStore

# 公共输入与异常类型保持一致。
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    CorporateAction,
    FillEvent,
    OrderEvent,
    OrderIntent,
    seal_record,
)

# 纯函数测试直接验证唯一账务投影。
from quant_core.ledger import apply_action, apply_fill

# 固定工作日 UTC 成交时刻，不调用系统时钟。
AT = datetime(2023, 11, 27, 14, 30, tzinfo=UTC)


def initial() -> AccountSnapshot:
    """构造十万美元无持仓初态；返回固定契约，无外部副作用。"""
    # 现金期望值由测试固定。
    return AccountSnapshot(as_of=AT, cash=Decimal("100000"), available_cash=Decimal("100000"))


def intent() -> OrderIntent:
    """构造十股百美元买单；返回固定意图，无外部副作用。"""
    # 每个字段都有明确来源，便于故障用例单独变更。
    return OrderIntent(
        client_order_id="buy-A",
        account_id="DEMO",
        decision_id="decision",
        security_id="A",
        side="BUY",
        quantity=10,
        limit_price=Decimal("100"),
        created_at=AT,
        eligible_at=AT,
        reserved_fee=Decimal("1"),
    )


def fill() -> FillEvent:
    """构造十股百美元加一美元费用的成交；期望现金为98999美元。"""
    # 新增成交量明确不是订单累计快照。
    return FillEvent(
        event_id="event-1",
        fill_id="fill-1",
        account_id="DEMO",
        client_order_id="buy-A",
        broker_order_id="broker-1",
        security_id="A",
        side="BUY",
        quantity=10,
        price=Decimal("100"),
        fee=Decimal("1"),
        at=AT,
    )


def action(kind: str) -> CorporateAction:
    """构造封印拆股或固定权益现金分红；返回契约，未知类型抛 ValueError。"""
    # 使用模型输入校验字符串分派，避免测试绕过合法类型。
    record = CorporateAction.model_validate(
        {
            "action_id": kind,
            "quality": "good",
            "security_id": "A",
            "kind": kind,
            "ratio": "2",
            "cash_per_share": "0.5",
            "entitlement_quantity": 10,
            "event_time": AT,
            "published_at": AT,
            "available_at": AT,
        }
    )
    # 先封印才能进入正式账务写入边界。
    return seal_record(record)


def test_fill_cash_fees_and_cost_basis_have_independent_expectations() -> None:
    """买卖费用与移动平均成本直接比固定常量；不依赖被测代码计算期望。"""
    # 十股每股100加费用1，使现金减少1001。
    bought = apply_fill(initial(), fill())
    # 买入后现金必须精确一致。
    assert bought.cash == Decimal("98999")
    # 可用现金同即时模拟结算现金。
    assert bought.available_cash == Decimal("98999")
    # 持仓只来自真实成交新增数量。
    assert bought.positions == {"A": 10}
    # 买入费用进入总成本。
    assert bought.cost_basis == {"A": Decimal("1001")}
    # 累计费用另有独立对账字段。
    assert bought.fees == Decimal("1")
    # 构造卖出四股，每股110，新增费用1。
    sale = fill().model_copy(
        update={
            "event_id": "sale-event",
            "fill_id": "sale-fill",
            "side": "SELL",
            "quantity": 4,
            "price": Decimal("110"),
        }
    )
    # 部分平仓不改变原输入对象。
    sold = apply_fill(bought, sale)
    # 98999加440减1，直接写期望。
    assert sold.cash == Decimal("99438")
    # 剩余六股的取得成本为600.6。
    assert sold.cost_basis == {"A": Decimal("600.6")}
    # 两次交易费用均必须保留。
    assert sold.fees == Decimal("2")
    # 旧对象不能被就地修改。
    assert bought.positions == {"A": 10}


def test_store_dual_unique_ids_and_restart(tmp_path: Path) -> None:
    """事件ID与成交ID各自防重，重开数据库不能重置资产；只有一次经济影响。"""
    # 内部状态库从独立固定初态创建。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 所有策略成交都有预先保存的意图。
    store.save_intent(intent())
    # 第一笔唯一成交成功入账。
    assert store.apply(fill()) is True
    # 同事件原样重投递不能重复记账。
    assert store.apply(fill()) is False
    # 相同成交可以由另一事件封装重复送达。
    assert store.apply(fill().model_copy(update={"event_id": "redelivered"})) is False
    # 精确现金期望不能因双通道重复改变。
    assert store.account(AT).cash == Decimal("98999")
    # 重开相同文件，初始化参数不能覆盖累计账户。
    reopened = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 重启后仍保留真实持仓。
    assert reopened.account(AT).positions == {"A": 10}
    # 独立成交身份依然有效。
    assert reopened.apply(fill()) is False
    # 费用没有因为重启重复计算。
    assert reopened.account(AT).fees == Decimal("1")


@pytest.mark.parametrize(
    "change", [{"price": Decimal("101")}, {"event_id": "other", "fee": Decimal("2")}]
)
def test_same_identity_different_content_freezes_atomically(
    tmp_path: Path, change: dict[str, str | Decimal]
) -> None:
    """同事件或同成交异内容必须报错并跨重启冻结，不能覆盖事实。"""
    # 保存初态与正常意图。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 当前测试使用已授权策略意图。
    store.save_intent(intent())
    # 先确认原始成交一次。
    store.apply(fill())
    # 身份冲突必须被明确拒绝。
    with pytest.raises(ContractError, match="内容冲突"):
        # 修改价格或费用会改变真实经济含义。
        store.apply(fill().model_copy(update=change))
    # 冲突事务不得改变现金。
    assert store.account(AT).cash == Decimal("98999")
    # 故障冻结不能随着事务失败回滚。
    assert store.frozen()
    # 进程重开仍然必须看到冻结。
    assert SQLiteEventStore(tmp_path / "events.sqlite", initial()).frozen()


def test_order_event_out_of_order_does_not_revert_state(tmp_path: Path) -> None:
    """先到已成交状态、后到开放状态，状态不倒退且账务只随Fill改变。"""
    # 建立内部账本。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 已授权意图先保存。
    store.save_intent(intent())
    # 较新事件报告整笔成交。
    newest = OrderEvent(
        event_id="status-2",
        client_order_id="buy-A",
        broker_order_id="broker-1",
        status="FILLED",
        filled_quantity=10,
        at=AT,
        sequence=2,
    )
    # 较新事件先到。
    store.apply(newest)
    # 只有状态报告不得伪造账户持仓。
    assert store.account(AT).positions == {}
    # 迟到OPEN事件使用更低单订单序号。
    store.apply(
        newest.model_copy(
            update={"event_id": "status-1", "status": "OPEN", "filled_quantity": 0, "sequence": 1}
        )
    )
    # 不能复活已全部成交订单。
    assert store.orders()[0].status == "FILLED"
    # 真实成交随后到达才增加财务数量。
    store.apply(fill())
    # 现金仅按这笔实际成交扣减一次。
    assert store.account(AT).cash == Decimal("98999")


def test_split_and_dividend_are_separate_idempotent_facts(tmp_path: Path) -> None:
    """拆股保留总成本，分红按固定权益数量，双重交付不重复改变权益。"""
    # 先建立十股真实持仓。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 策略意图为成交提供来源。
    store.save_intent(intent())
    # 买入持仓费用一并记账。
    store.apply(fill())
    # 二比一拆股第一次生效。
    assert store.apply_action(action("split"), AT) is True
    # 重复拆股不能再乘二。
    assert store.apply_action(action("split"), AT) is False
    # 十股拆为二十股。
    assert store.account(AT).positions == {"A": 20}
    # 总取得成本不因拆股翻倍或减半。
    assert store.account(AT).cost_basis == {"A": Decimal("1001")}
    # 分红采用十股固定权益，不按目前二十股猜测。
    assert store.apply_action(action("dividend"), AT) is True
    # 固定股息收入仅五美元。
    assert store.account(AT).cash == Decimal("99004")
    # 股息重复不能再入账。
    assert store.apply_action(action("dividend"), AT) is False
    # 费用不会被股息冲销。
    assert store.account(AT).fees == Decimal("1")


def test_fractional_split_and_oversell_fail_without_mutating_input() -> None:
    """不支持的碎股和超卖不能向下取整或产生隐含空头。"""
    # 固定一股初态与总成本。
    account = AccountSnapshot(
        as_of=AT,
        cash=Decimal("100"),
        available_cash=Decimal("100"),
        positions={"A": 1},
        cost_basis={"A": Decimal("10")},
    )
    # 三分之二拆分将产生碎股，必须失败。
    with pytest.raises(ContractError, match="碎股"):
        # 此处纯函数验证数量边界，不进入存储封印要求。
        apply_action(account, action("split").model_copy(update={"ratio": Decimal("0.5")}), AT)
    # 一股持仓不能卖出十股。
    with pytest.raises(ContractError, match="超过实际持仓"):
        # 修改方向构造确定的超卖事实。
        apply_fill(account, fill().model_copy(update={"side": "SELL"}))
    # 失败不能改变原账户。
    assert account.positions == {"A": 1}


def test_order_broker_identity_cannot_be_rebound(tmp_path: Path) -> None:
    """两种订单写入口都只能首次补齐外部ID，之后改绑必须冻结且保留旧值。"""
    # 内部意图与原始券商身份先建立绑定。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 意图先于任何券商响应落盘。
    store.save_intent(intent())
    # 原始受理事件明确券商ID。
    accepted = OrderEvent(
        event_id="accepted",
        client_order_id="buy-A",
        broker_order_id="KNOWN",
        status="OPEN",
        at=AT,
        sequence=1,
    )
    # 首次绑定允许补齐。
    store.apply(accepted)
    # 事件通道试图改绑必须失败。
    with pytest.raises(ContractError, match="券商订单 ID 内容冲突"):
        # 较高序号也不赋予更换委托身份的权利。
        store.apply(
            accepted.model_copy(
                update={"event_id": "bad-event", "broker_order_id": "DIFFERENT", "sequence": 2}
            )
        )
    # 快照通道同样不能改绑。
    with pytest.raises(ContractError, match="券商订单 ID 内容冲突"):
        # 从已知投影修改外部身份重现独立审查反例。
        store.record_order(store.orders()[0].model_copy(update={"broker_order_id": "DIFFERENT"}))
    # 两次失败均没有覆盖旧绑定。
    assert store.orders()[0].broker_order_id == "KNOWN"
    # 账户新增风险被持久化冻结。
    assert store.frozen()


def test_same_order_sequence_cannot_have_conflicting_status(tmp_path: Path) -> None:
    """同序号不同事件ID并不代表新状态，冲突应冻结而不是作为乱序忽略。"""
    # 已落盘意图准备接收状态。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 保存确定性意图。
    store.save_intent(intent())
    # 券商首次事件声称开放。
    accepted = OrderEvent(
        event_id="accepted",
        client_order_id="buy-A",
        broker_order_id="KNOWN",
        status="OPEN",
        at=AT,
        sequence=1,
    )
    # 原始事实成功保存。
    store.apply(accepted)
    # 同序号改称已取消违反来源单调事件约定。
    with pytest.raises(ContractError, match="序号内容冲突"):
        # 传输事件ID变化不能掩盖券商事实矛盾。
        store.apply(accepted.model_copy(update={"event_id": "contradiction", "status": "CANCELED"}))
    # 原始OPEN投影不被覆盖。
    assert store.orders()[0].status == "OPEN"


def test_invalid_filled_zero_state_is_frozen_at_storage_boundary(tmp_path: Path) -> None:
    """绕过模型构造的FILLED/0仍在存储边界失败，不能伪装成零成交成功对账。"""
    # 首先创建合法意图投影。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 正常初始状态为PERSISTED零成交。
    store.save_intent(intent())
    # model_copy模拟一个不重新验证内容的外部调用者。
    corrupt = store.orders()[0].model_copy(update={"status": "FILLED"})
    # 存储边界必须重新验证状态数量。
    with pytest.raises(ContractError):
        # 非法快照不能被写成合法终态。
        store.record_order(corrupt)
    # 原始订单仍未送达券商。
    assert store.orders()[0].status == "PERSISTED"
    # 失败必须留有持久冻结理由。
    assert store.frozen()


def test_future_corporate_action_cannot_change_present_cash(tmp_path: Path) -> None:
    """未来行动即使带合法哈希也不能提前付款，处理时点是必填边界。"""
    # 内部初态无权益变化。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 使用明确晚于测试当前时间的未来发生时刻。
    future = datetime(2023, 12, 4, 14, 30, tzinfo=UTC)
    # 未来事件可提前公布，但经济权益仍不可提前投影。
    dividend = seal_record(action("dividend").model_copy(update={"event_time": future}))
    # 已封印内容不等于已经生效。
    with pytest.raises(ContractError, match="尚未发生"):
        # 处理时点不允许读取真实系统时间补齐。
        store.apply_action(dividend, AT)
    # 现金未增加且故障已冻结。
    assert store.account(AT).cash == Decimal("100000")
    # 明确风险闸门存在。
    assert store.frozen()


def test_journal_replays_interleaved_actions_and_future_intents(tmp_path: Path) -> None:
    """买入→拆股→新卖意图→成交→分红保持写入交错，账户和订单重放完全一致。"""
    # 原始账本从固定无持仓账户开始。
    original = SQLiteEventStore(tmp_path / "original.sqlite", initial())
    # 原始买单先持久化。
    original.save_intent(intent())
    # 成交事实产生十股持仓与1001取得成本。
    original.apply(fill())
    # 订单通道明确整笔买入完成。
    original.apply(
        OrderEvent(
            event_id="buy-filled",
            client_order_id="buy-A",
            broker_order_id="broker-1",
            status="FILLED",
            filled_quantity=10,
            at=AT,
            sequence=1,
        )
    )
    # 拆股发生在未来卖单意图之前。
    original.apply_action(action("split"), AT)
    # 新卖单五股，不能在拆股前错误计算剩余仓位。
    sale = intent().model_copy(
        update={
            "client_order_id": "sell-A",
            "side": "SELL",
            "quantity": 5,
            "limit_price": Decimal("120"),
        }
    )
    # 新意图位置必须保留在统一日志中。
    original.save_intent(sale)
    # 新增卖出现金为600减1，移动平均剩余成本750.75。
    sold = fill().model_copy(
        update={
            "event_id": "sold-event",
            "fill_id": "sold-fill",
            "client_order_id": "sell-A",
            "broker_order_id": "broker-2",
            "side": "SELL",
            "quantity": 5,
            "price": Decimal("120"),
        }
    )
    # 唯一成交事件改变现金与持仓。
    original.apply(sold)
    # 卖单状态明确完成五股。
    original.apply(
        OrderEvent(
            event_id="sell-filled",
            client_order_id="sell-A",
            broker_order_id="broker-2",
            status="FILLED",
            filled_quantity=5,
            at=AT,
            sequence=1,
        )
    )
    # 分红权益仍固定为十股，而当前实际持仓十五股。
    original.apply_action(action("dividend"), AT)
    # 独立手算现金，不用原账本输出作为唯一期望。
    assert original.account(AT).cash == Decimal("99603")
    # 原账户最后十五股与两美元总费用。
    assert original.account(AT).positions == {"A": 15}
    # 原账户移动平均成本精确为750.75。
    assert original.account(AT).cost_basis == {"A": Decimal("750.75")}
    # 新数据库仅接收原始初态，不拷贝最后账户。
    replay = SQLiteEventStore(tmp_path / "replayed.sqlite", initial())
    # 按实际写入序号重放，不按业务时间重新排序。
    for entry in original.journal():
        # 意图输入必须保持精确具体类型。
        if entry.operation == "intent":
            # 重建被保存的原始稳定意图。
            assert isinstance(entry.payload, OrderIntent)
            # 仍使用正式保存边界进行校验。
            replay.save_intent(entry.payload)
        # 此用例的订单状态由事件携带。
        elif entry.operation == "event":
            # 确认公共联合类型只包含已定义交易事件。
            assert isinstance(entry.payload, (FillEvent, OrderEvent))
            # 账务与状态按原先去重规则投影。
            replay.apply(entry.payload)
        # 公司行动保留实际处理时刻。
        elif entry.operation == "action":
            # 明确这是动作事实而非普通状态事件。
            assert isinstance(entry.payload, CorporateAction)
            # 不能把所有行动挪到最后批量处理。
            replay.apply_action(entry.payload, entry.at)
        # 如果新增其他操作，本用例必须显式更新预期。
        else:
            # 不默默跳过未知日志操作。
            raise AssertionError("当前固定样例不应生成其他日志操作")
    # 新账户独立重建后每个事实字段必须相同。
    assert replay.account(AT) == original.account(AT)
    # 新意图和两笔终态也完全一致。
    assert replay.orders() == original.orders()
    # 更强断言确认原始交错顺序被原样保存。
    assert [entry.operation for entry in replay.journal()] == [
        "intent",
        "event",
        "event",
        "action",
        "intent",
        "event",
        "event",
        "action",
    ]


def test_old_open_event_after_partial_query_does_not_regress_quantity(tmp_path: Path) -> None:
    """查询已确认部分成交后才收到旧OPEN事件，保持PARTIAL而非构造非法OPEN/非零。"""
    # 原始事件库只先保存意图。
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    # 未发送初态可接收后续券商确认。
    store.save_intent(intent())
    # 模拟独立查询先拿到四股部分成交快照。
    partial = store.orders()[0].model_copy(
        update={"status": "PARTIAL", "filled_quantity": 4, "broker_order_id": "KNOWN"}
    )
    # 快照通道先更新状态，不伪造成交现金。
    store.record_order(partial)
    # 原始OPEN事件晚于独立查询到达。
    old = OrderEvent(
        event_id="old-open",
        client_order_id="buy-A",
        broker_order_id="KNOWN",
        status="OPEN",
        at=AT,
        sequence=1,
    )
    # 迟到状态仍可留痕。
    store.apply(old)
    # 已确认部分成交数量不能被重置。
    assert store.orders()[0].filled_quantity == 4
    # 状态数量保持有效组合。
    assert store.orders()[0].status == "PARTIAL"
    # 相同旧事件以不同传输ID重复，比较原始序号事实而非当前投影。
    store.apply(old.model_copy(update={"event_id": "old-open-alias"}))
    # 语义相同的同序号重复不应误冻结。
    assert store.frozen() == []
