"""账务独立数值期望、双重去重与公司行动测试；不调用被测公式生成期望。"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quant_core.adapters.sqlite_store import SQLiteEventStore
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    CorporateAction,
    FillEvent,
    OrderEvent,
    OrderIntent,
    seal_record,
)
from quant_core.ledger import apply_action, apply_fill

AT = datetime(2023, 11, 27, 14, 30, tzinfo=UTC)


def initial() -> AccountSnapshot:
    """构造美元现金/可用现金均为十万且 positions 为空的账户事实，供每例独立起步。"""
    return AccountSnapshot(as_of=AT, cash=Decimal("100000"), available_cash=Decimal("100000"))


def intent() -> OrderIntent:
    """构造拟买 A 十股、限价100美元的授权意图；保存它本身不会产生持仓或成交。"""
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
    """构造 A 新增成交十股、每股100美元、费用1美元的券商事实。

    client_order_id 对应 intent()，event_id 是投递身份，fill_id 是经济成交身份；
    两种身份分别测试重复投递和重复记账。作用于 initial() 后现金应为98999美元。
    """
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
    """按 kind 构造已封印的公司行动；未知类型由契约抛 ValueError。

    split 使用2倍股数比例，dividend 使用每股0.5美元和固定10股权益；未适用于
    该 kind 的字段仍在输入字典中，账务按行动种类选择。seal_record 生成内容摘要，
    不证明来源真实或行动已经发生；处理时点仍由账务入口核对。
    """
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
    return seal_record(record)


def test_fill_cash_fees_and_cost_basis_have_independent_expectations() -> None:
    """买卖费用与移动平均成本直接比固定常量；不依赖被测代码计算期望。"""
    # 十股每股100加费用1，使现金减少1001。
    bought = apply_fill(initial(), fill())
    assert bought.cash == Decimal("98999")
    # 可用现金同即时模拟结算现金。
    assert bought.available_cash == Decimal("98999")
    assert bought.positions == {"A": 10}
    # 买入费用进入总成本。
    assert bought.cost_basis == {"A": Decimal("1001")}
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
    sold = apply_fill(bought, sale)
    # 98999加440减1，直接写期望。
    assert sold.cash == Decimal("99438")
    # 原成本1001按剩余6/10保留，取得成本为600.6。
    assert sold.cost_basis == {"A": Decimal("600.6")}
    assert sold.fees == Decimal("2")
    assert bought.positions == {"A": 10}


def test_store_dual_unique_ids_and_restart(tmp_path: Path) -> None:
    """事件ID与成交ID各自防重，重开数据库不能重置资产；只有一次经济影响。"""
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    store.save_intent(intent())
    assert store.apply(fill()) is True
    assert store.apply(fill()) is False
    # 相同成交可以由另一事件封装重复送达。
    assert store.apply(fill().model_copy(update={"event_id": "redelivered"})) is False
    assert store.account(AT).cash == Decimal("98999")
    # 重开相同文件，初始化参数不能覆盖累计账户。
    reopened = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    assert reopened.account(AT).positions == {"A": 10}
    assert reopened.apply(fill()) is False
    assert reopened.account(AT).fees == Decimal("1")


@pytest.mark.parametrize(
    "change", [{"price": Decimal("101")}, {"event_id": "other", "fee": Decimal("2")}]
)
def test_same_identity_different_content_freezes_atomically(
    tmp_path: Path, change: dict[str, str | Decimal]
) -> None:
    """同事件或同成交异内容必须报错并跨重启冻结，不能覆盖事实。"""
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    store.save_intent(intent())
    store.apply(fill())
    # model_copy 不重做模型验证，故意让同身份异内容到达存储入口；原经济事实已入账。
    with pytest.raises(ContractError, match="内容冲突"):
        store.apply(fill().model_copy(update=change))
    assert store.account(AT).cash == Decimal("98999")
    # 故障冻结不能随着事务失败回滚。
    assert store.frozen()
    assert SQLiteEventStore(tmp_path / "events.sqlite", initial()).frozen()


def test_order_event_out_of_order_does_not_revert_state(tmp_path: Path) -> None:
    """先到已成交状态、后到开放状态，状态不倒退且账务只随Fill改变。"""
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    store.save_intent(intent())
    # OrderEvent 只报告累计数量/状态；FillEvent 才携带本次成交价、费用并改变账户。
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
    store.apply(newest)
    # 只有状态报告不得伪造账户持仓。
    assert store.account(AT).positions == {}
    # 迟到OPEN事件使用更低单订单序号。
    store.apply(
        newest.model_copy(
            update={"event_id": "status-1", "status": "OPEN", "filled_quantity": 0, "sequence": 1}
        )
    )
    assert store.orders()[0].status == "FILLED"
    # 真实成交随后到达才增加财务数量。
    store.apply(fill())
    assert store.account(AT).cash == Decimal("98999")


def test_split_and_dividend_are_separate_idempotent_facts(tmp_path: Path) -> None:
    """拆股保留总成本，分红按固定权益数量，双重交付不重复改变权益。"""
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    store.save_intent(intent())
    store.apply(fill())
    # 二比一拆股第一次生效。
    assert store.apply_action(action("split"), AT) is True
    assert store.apply_action(action("split"), AT) is False
    assert store.account(AT).positions == {"A": 20}
    # 总取得成本不因拆股翻倍或减半。
    assert store.account(AT).cost_basis == {"A": Decimal("1001")}
    # 分红采用十股固定权益，不按目前二十股猜测。
    assert store.apply_action(action("dividend"), AT) is True
    # 固定股息收入仅五美元。
    assert store.account(AT).cash == Decimal("99004")
    assert store.apply_action(action("dividend"), AT) is False
    assert store.account(AT).fees == Decimal("1")


def test_fractional_split_and_oversell_fail_without_mutating_input() -> None:
    """不支持的碎股和超卖不能向下取整或产生隐含空头。"""
    account = AccountSnapshot(
        as_of=AT,
        cash=Decimal("100"),
        available_cash=Decimal("100"),
        positions={"A": 1},
        cost_basis={"A": Decimal("10")},
    )
    # ratio=0.5 将一股变成半股，当前整股契约必须拒绝，不能向下取整。
    with pytest.raises(ContractError, match="碎股"):
        apply_action(account, action("split").model_copy(update={"ratio": Decimal("0.5")}), AT)
    # 一股持仓不能卖出十股。
    with pytest.raises(ContractError, match="超过实际持仓"):
        apply_fill(account, fill().model_copy(update={"side": "SELL"}))
    assert account.positions == {"A": 1}


def test_order_broker_identity_cannot_be_rebound(tmp_path: Path) -> None:
    """两种订单写入口都只能首次补齐外部ID，之后改绑必须冻结且保留旧值。"""
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    store.save_intent(intent())
    accepted = OrderEvent(
        event_id="accepted",
        client_order_id="buy-A",
        broker_order_id="KNOWN",
        status="OPEN",
        at=AT,
        sequence=1,
    )
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
        store.record_order(store.orders()[0].model_copy(update={"broker_order_id": "DIFFERENT"}))
    assert store.orders()[0].broker_order_id == "KNOWN"
    assert store.frozen()


def test_same_order_sequence_cannot_have_conflicting_status(tmp_path: Path) -> None:
    """同序号不同事件ID并不代表新状态，冲突应冻结而不是作为乱序忽略。"""
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    store.save_intent(intent())
    accepted = OrderEvent(
        event_id="accepted",
        client_order_id="buy-A",
        broker_order_id="KNOWN",
        status="OPEN",
        at=AT,
        sequence=1,
    )
    store.apply(accepted)
    # 同序号改称已取消违反来源单调事件约定。
    with pytest.raises(ContractError, match="序号内容冲突"):
        store.apply(accepted.model_copy(update={"event_id": "contradiction", "status": "CANCELED"}))
    assert store.orders()[0].status == "OPEN"


def test_invalid_filled_zero_state_is_frozen_at_storage_boundary(tmp_path: Path) -> None:
    """绕过模型构造的FILLED/0仍在存储边界失败，不能伪装成零成交成功对账。"""
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    store.save_intent(intent())
    # save_intent 生成的记录是 PERSISTED/累计0股；model_copy只改成FILLED，
    # 刻意不重跑模型验证，让 record_order 自己识别“全部成交但数量为0”的冲突。
    # model_copy模拟一个不重新验证内容的外部调用者。
    corrupt = store.orders()[0].model_copy(update={"status": "FILLED"})
    with pytest.raises(ContractError):
        store.record_order(corrupt)
    assert store.orders()[0].status == "PERSISTED"
    assert store.frozen()


def test_future_corporate_action_cannot_change_present_cash(tmp_path: Path) -> None:
    """未来行动即使带合法哈希也不能提前付款，处理时点是必填边界。"""
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
    future = datetime(2023, 12, 4, 14, 30, tzinfo=UTC)
    # 未来事件可提前公布，但经济权益仍不可提前投影。
    dividend = seal_record(action("dividend").model_copy(update={"event_time": future}))
    with pytest.raises(ContractError, match="尚未发生"):
        store.apply_action(dividend, AT)
    assert store.account(AT).cash == Decimal("100000")
    assert store.frozen()


def test_journal_replays_interleaved_actions_and_future_intents(tmp_path: Path) -> None:
    """买入→拆股→新卖意图→成交→分红保持写入交错，账户和订单重放完全一致。"""
    original = SQLiteEventStore(tmp_path / "original.sqlite", initial())
    original.save_intent(intent())
    original.apply(fill())
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
    original.save_intent(sale)
    # 拆股后20股共有成本1001；卖5股后剩15股，成本=1001×15/20=750.75。
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
    original.apply(sold)
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
    # 手算现金：100000 - 1001 + (5×120 - 1) + (10×0.5) = 99603。
    assert original.account(AT).cash == Decimal("99603")
    assert original.account(AT).positions == {"A": 15}
    assert original.account(AT).cost_basis == {"A": Decimal("750.75")}
    # 新数据库仅接收原始初态，不拷贝最后账户。
    replay = SQLiteEventStore(tmp_path / "replayed.sqlite", initial())
    # 按实际写入序号重放，不按业务时间重新排序。
    for entry in original.journal():
        if entry.operation == "intent":
            assert isinstance(entry.payload, OrderIntent)
            replay.save_intent(entry.payload)
        # 此用例的订单状态由事件携带。
        elif entry.operation == "event":
            assert isinstance(entry.payload, (FillEvent, OrderEvent))
            replay.apply(entry.payload)
        # 公司行动保留实际处理时刻。
        elif entry.operation == "action":
            assert isinstance(entry.payload, CorporateAction)
            replay.apply_action(entry.payload, entry.at)
        # 如果新增其他操作，本用例必须显式更新预期。
        else:
            raise AssertionError("当前固定样例不应生成其他日志操作")
    assert replay.account(AT) == original.account(AT)
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
    store = SQLiteEventStore(tmp_path / "events.sqlite", initial())
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
    store.apply(old)
    assert store.orders()[0].filled_quantity == 4
    assert store.orders()[0].status == "PARTIAL"
    # 相同旧事件以不同传输ID重复，比较原始序号事实而非当前投影。
    store.apply(old.model_copy(update={"event_id": "old-open-alias"}))
    assert store.frozen() == []
