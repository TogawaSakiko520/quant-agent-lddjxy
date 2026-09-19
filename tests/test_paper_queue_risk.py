"""休市单股排队测试的独立边界；普通策略时段规则、金额约束和恢复语义不变。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_data import security_record

from quant_core.adapters.calendar import ExchangeCalendar, FixedClock
from quant_core.adapters.sqlite_store import SQLiteEventStore
from quant_core.contracts import (
    AccountSnapshot,
    DemoConfig,
    FillEvent,
    OrderEvent,
    OrderIntent,
    OrderRecord,
    PaperConfig,
    PaperQueueTestContext,
    Quote,
    RiskBlocked,
    TargetPortfolio,
    TargetPosition,
)
from quant_core.execution import ExecutionService
from quant_core.risk import assess_order, assess_paper_queue_test

NOW = datetime(2023, 1, 8, 18, tzinfo=UTC)
CLOSE = datetime(2023, 1, 6, 21, tzinfo=UTC)
OPEN = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)


def queue_case() -> dict[str, Any]:
    """构造周日真实时刻、周五100美元原始收盘及周一待开盘的一股计划。

    账户净值10000、空仓，计划最多4股但测试仅1股；现金支出含1美元费用为101，
    低于5%单票500美元边界。历史参考报价保留周五收盘时间，不改成周日。
    """
    intent = OrderIntent(
        client_order_id="queue-test-client",
        account_id="paper-a",
        decision_id="ma-d",
        strategy_version="ma-trend-1.0.0",
        security_id="A",
        side="BUY",
        quantity=1,
        limit_price=Decimal("100"),
        reserved_fee=Decimal("1"),
        created_at=NOW,
        eligible_at=OPEN,
    )
    target = TargetPortfolio(
        decision_id="ma-d",
        as_of=NOW - timedelta(minutes=1),
        nav=Decimal("10000"),
        cash_weight=0.96,
        positions=[
            TargetPosition(
                security_id="A", sector=None, weight=0.04, quantity=4, reason="ma_target"
            )
        ],
    )
    return {
        "intent": intent,
        "account": AccountSnapshot(
            account_id="paper-a", as_of=NOW, cash=Decimal("10000"), available_cash=Decimal("10000")
        ),
        "open_orders": [],
        "quotes": {"A": Quote(security_id="A", at=CLOSE, price=Decimal("100"))},
        "config": PaperConfig(
            account_id="paper-a",
            strategy="ma-trend",
            candidates=["AAA", "BBB"],
            budget=Decimal("10000"),
            history_start=date(2022, 1, 1),
            history_end=date(2023, 1, 6),
        ),
        "now": NOW,
        "calendar": ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31)),
        "context": PaperQueueTestContext(
            plan_id="approved-plan",
            decision_id="ma-d",
            account_id="paper-a",
            security_id="A",
            client_order_id="queue-test-client",
            reference_session=date(2023, 1, 6),
            reference_close=Decimal("100"),
            reference_close_at=CLOSE,
            reference_observed_at=NOW - timedelta(minutes=1),
            remote_clock_at=NOW,
            remote_is_open=False,
            next_open=OPEN,
        ),
        "target": target,
        "security_records": [security_record("A").model_copy(update={"sector": None})],
        "adv": {"A": 1000000.0},
        "reference_nav": Decimal("10000"),
        "peak_nav": Decimal("10000"),
    }


def test_only_explicit_queue_entry_accepts_closed_market_reference() -> None:
    """同一旧收盘价仅经独立队列入口可用，正常策略仍拒休市和旧报价。"""
    values = queue_case()
    assert assess_paper_queue_test(**values).allowed
    normal = {key: value for key, value in values.items() if key != "context"}
    result = assess_order(**normal)
    assert not result.allowed
    assert "outside_execution_session" in result.reasons
    assert "stale_or_future_quote" in result.reasons
    assert values["quotes"]["A"].at == CLOSE
    assert values["intent"].eligible_at == OPEN


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("quantity", 2, "queue_test_single_share_or_amount_limit"),
        ("side", "SELL", "queue_test_single_share_or_amount_limit"),
        ("limit_price", Decimal("101"), "queue_test_reference_price_mismatch"),
        ("limit_price", Decimal("500"), "queue_test_single_share_or_amount_limit"),
        ("created_at", NOW + timedelta(seconds=1), "queue_test_opening_buffer_or_eligibility"),
        ("eligible_at", NOW, "queue_test_opening_buffer_or_eligibility"),
        ("strategy_version", "weekly-two-factor-1.0.0", "queue_test_identity_mismatch"),
    ],
)
def test_queue_intent_bounds_are_not_normal_order_exemptions(
    field: str, value: Any, reason: str
) -> None:
    """数量、方向、价格、创建时间和策略身份分别越界时不能借排队测试扩权。"""
    values = queue_case()
    values["intent"] = values["intent"].model_copy(update={field: value})
    result = assess_paper_queue_test(**values)
    assert not result.allowed and reason in result.reasons


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("account_id", "other", "queue_test_identity_mismatch"),
        ("client_order_id", "other", "queue_test_identity_mismatch"),
        ("decision_id", "other", "queue_test_identity_mismatch"),
        ("security_id", "other", "queue_test_identity_mismatch"),
        ("remote_clock_at", NOW - timedelta(seconds=61), "queue_test_stale_or_future_remote_clock"),
        ("remote_clock_at", NOW + timedelta(seconds=1), "queue_test_stale_or_future_remote_clock"),
        ("remote_is_open", True, "queue_test_requires_closed_market"),
        (
            "reference_observed_at",
            CLOSE - timedelta(seconds=1),
            "queue_test_reference_observation_time",
        ),
        (
            "reference_observed_at",
            NOW + timedelta(seconds=1),
            "queue_test_reference_observation_time",
        ),
        ("reference_session", date(2023, 1, 5), "queue_test_not_latest_completed_session"),
        (
            "reference_close_at",
            CLOSE - timedelta(seconds=1),
            "queue_test_not_latest_completed_session",
        ),
        ("next_open", OPEN + timedelta(days=1), "queue_test_not_latest_completed_session"),
        ("reference_close", Decimal("99"), "queue_test_reference_price_mismatch"),
    ],
)
def test_queue_reference_and_authorization_must_match_actual_context(
    field: str, value: Any, reason: str
) -> None:
    """错误绑定、陈旧时钟、非最后完整日线及伪造收盘时点分别拒绝。"""
    values = queue_case()
    values["context"] = values["context"].model_copy(update={field: value})
    result = assess_paper_queue_test(**values)
    assert not result.allowed and reason in result.reasons


def test_premarket_buffer_open_market_and_fresh_account_are_required() -> None:
    """开盘前只剩十分钟、已开市、旧账户事实均独立阻止队列测试。"""
    values = queue_case()
    values["account"] = values["account"].model_copy(update={"as_of": NOW - timedelta(seconds=61)})
    assert "stale_or_future_account" in assess_paper_queue_test(**values).reasons
    for now, reason in [
        (OPEN - timedelta(minutes=10), "queue_test_opening_buffer_or_eligibility"),
        (OPEN + timedelta(minutes=1), "queue_test_requires_closed_market"),
    ]:
        values = queue_case()
        values["now"] = now
        values["account"] = values["account"].model_copy(update={"as_of": now})
        values["context"] = values["context"].model_copy(update={"remote_clock_at": now})
        assert reason in assess_paper_queue_test(**values).reasons


def test_queue_scope_requires_paper_ma_and_single_flat_account() -> None:
    """离线、原双因子、有实际持仓或其他订单均不符合独立休市测试范围。"""
    values = queue_case()
    values["config"] = DemoConfig()
    assert "queue_test_requires_paper_ma" in assess_paper_queue_test(**values).reasons
    values = queue_case()
    values["config"] = values["config"].model_copy(update={"strategy": "weekly-two-factor"})
    assert "queue_test_requires_paper_ma" in assess_paper_queue_test(**values).reasons
    values = queue_case()
    values["account"] = values["account"].model_copy(update={"positions": {"A": 1}})
    assert "queue_test_requires_flat_single_order" in assess_paper_queue_test(**values).reasons
    values = queue_case()
    values["open_orders"] = [
        OrderRecord(
            intent=values["intent"].model_copy(update={"client_order_id": "other"}),
            status="CANCELED",
        )
    ]
    assert "queue_test_requires_flat_single_order" in assess_paper_queue_test(**values).reasons


def test_real_reference_cannot_be_restamped_or_replaced_with_extra_quotes() -> None:
    """参考收盘报价改成现在、不同原始价或额外证券都不能获得旧价豁免。"""
    for patch in ({"at": NOW}, {"price": Decimal("99")}):
        values = queue_case()
        values["quotes"]["A"] = values["quotes"]["A"].model_copy(update=patch)
        assert "queue_test_reference_price_mismatch" in assess_paper_queue_test(**values).reasons
    values = queue_case()
    values["quotes"]["B"] = Quote(security_id="B", at=CLOSE, price=Decimal("100"))
    assert "queue_test_reference_price_mismatch" in assess_paper_queue_test(**values).reasons


def test_queue_keeps_target_cash_fees_liquidity_reconciliation_and_exposure_rules() -> None:
    """仅时段与参考价语义改变；全部正常经济约束仍能独立拒绝测试单。"""
    changes: list[tuple[dict[str, Any], str]] = [
        ({"target": None}, "missing_approved_target"),
        ({"reference_nav": None}, "missing_reference_nav"),
        ({"peak_nav": None}, "missing_peak_nav"),
        ({"adv": {"A": 0}}, "liquidity_limit_or_missing_adv"),
        ({"reconciled": False}, "unreconciled_account"),
        ({"data_good": False}, "data_quality_failed"),
        ({"security_records": []}, "security_not_eligible"),
    ]
    for patch, reason in changes:
        values = queue_case() | patch
        result = assess_paper_queue_test(**values)
        assert not result.allowed and reason in result.reasons
    values = queue_case()
    values["account"] = values["account"].model_copy(update={"available_cash": Decimal("50")})
    assert "insufficient_available_cash" in assess_paper_queue_test(**values).reasons
    values = queue_case()
    values["intent"] = values["intent"].model_copy(update={"reserved_fee": Decimal("0")})
    assert "insufficient_fee_reserve" in assess_paper_queue_test(**values).reasons
    values = queue_case()
    values["account"] = values["account"].model_copy(
        update={"cash": Decimal("1000"), "available_cash": Decimal("1000")}
    )
    values["reference_nav"] = values["peak_nav"] = Decimal("1000")
    # 一股100除以扣1美元费用后的999净值，大于默认5%，小额测试也不能放宽。
    assert "single_position_limit" in assess_paper_queue_test(**values).reasons
    values = queue_case()
    values["target"] = values["target"].model_copy(update={"positions": []})
    assert "target_quantity_exceeded" in assess_paper_queue_test(**values).reasons


class QueueBroker:
    """独立离线订单事实源，只返回手工受理和撤单结果，不产生虚拟成交。"""

    def __init__(self, account: AccountSnapshot) -> None:
        """保存独立账户初态及空远端订单；计数用于核验重复调用没有第二次提交。"""
        self.snapshot = account
        self.records: dict[str, OrderRecord] = {}
        self.submit_calls = 0
        self.cancel_calls = 0

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """返回独立远端ID与OPEN状态，保持现金和实际持仓不变。"""
        self.submit_calls += 1
        record = OrderRecord(intent=intent, status="OPEN", broker_order_id="remote-queue-id")
        self.records[intent.client_order_id] = record
        return record

    def query(self, client_order_id: str) -> OrderRecord | None:
        """按原客户ID查询已保存的远端事实。"""
        return self.records.get(client_order_id)

    def cancel(self, client_order_id: str) -> OrderRecord:
        """将指定测试订单确认为CANCELED，不伪造成交或现金变化。"""
        self.cancel_calls += 1
        record = self.records[client_order_id].model_copy(update={"status": "CANCELED"})
        self.records[client_order_id] = record
        return record

    def orders(self) -> list[OrderRecord]:
        """返回当前独立订单列表，供执行入口恢复。"""
        return list(self.records.values())

    def events(self) -> list[OrderEvent | FillEvent]:
        """无实际成交，事件列表诚实为空。"""
        return []

    def account(self) -> AccountSnapshot:
        """返回独立不变账户；队列提交本身不能扣成已发生买入。"""
        return self.snapshot


def service_case(tmp_path: Path) -> tuple[ExecutionService, QueueBroker, dict[str, Any]]:
    """把固定周日场景装配为持久化执行服务，沿用同一真实时点而不改日历。"""
    values = queue_case()
    store = SQLiteEventStore(tmp_path / "internal.sqlite", values["account"])
    broker = QueueBroker(values["account"])
    service = ExecutionService(store, broker, FixedClock(NOW), values["config"], values["calendar"])
    inputs = {
        key: values[key]
        for key in (
            "intent",
            "quotes",
            "context",
            "target",
            "security_records",
            "adv",
            "reference_nav",
            "peak_nav",
        )
    }
    return service, broker, inputs


def test_queue_service_restarts_queries_and_cancels_without_resubmitting(tmp_path: Path) -> None:
    """新入口只发一次，重启重复调用先恢复，撤单和最终核对沿用原执行服务。"""
    service, broker, inputs = service_case(tmp_path)
    record = service.submit_paper_queue_test(**inputs)
    assert record.status == "OPEN" and record.broker_order_id == "remote-queue-id"
    assert broker.submit_calls == 1
    restarted = ExecutionService(
        SQLiteEventStore(tmp_path / "internal.sqlite", broker.snapshot),
        broker,
        FixedClock(NOW),
        service.config,
        service.calendar,
    )
    repeated = restarted.submit_paper_queue_test(**inputs)
    assert repeated == record and broker.submit_calls == 1
    canceled = restarted.cancel(record.intent.client_order_id)
    assert canceled.status == "CANCELED" and broker.cancel_calls == 1
    assert restarted.recover().matched
    assert restarted.store.account(NOW).positions == {}
    assert restarted.store.account(NOW).cash == Decimal("10000")
    assert restarted.store.events() == []


def test_normal_execution_service_cannot_accept_queue_context(tmp_path: Path) -> None:
    """普通submit没有测试上下文参数，省掉参数也仍以休市旧报价拒绝且不调用Broker。"""
    service, broker, inputs = service_case(tmp_path)
    with pytest.raises(TypeError):
        service.submit(**inputs)
    normal = {key: value for key, value in inputs.items() if key != "context"}
    with pytest.raises(RiskBlocked, match="outside_execution_session"):
        service.submit(**normal)
    assert broker.submit_calls == 0


class TimeoutQueueBroker(QueueBroker):
    """远端已经受理但首次响应丢失，供独立队列入口复核未知状态路径。"""

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """保留真实远端OPEN事实后模拟超时，不能将超时当作确定拒绝。"""
        super().submit(intent)
        raise TimeoutError("脱敏响应丢失")


def test_queue_accepted_timeout_recovers_original_remote_id_without_retry(tmp_path: Path) -> None:
    """队列受理超时只保留UNKNOWN，重启按原客户ID恢复并撤单，不发送第二次。"""
    values = queue_case()
    broker = TimeoutQueueBroker(values["account"])
    store = SQLiteEventStore(tmp_path / "internal.sqlite", values["account"])
    service = ExecutionService(store, broker, FixedClock(NOW), values["config"], values["calendar"])
    inputs = {
        key: values[key]
        for key in (
            "intent",
            "quotes",
            "context",
            "target",
            "security_records",
            "adv",
            "reference_nav",
            "peak_nav",
        )
    }
    unknown = service.submit_paper_queue_test(**inputs)
    assert unknown.status == "UNKNOWN" and broker.submit_calls == 1
    restarted = ExecutionService(
        SQLiteEventStore(tmp_path / "internal.sqlite", values["account"]),
        broker,
        FixedClock(NOW),
        values["config"],
        values["calendar"],
    )
    recovered = restarted.submit_paper_queue_test(**inputs)
    assert recovered.status == "OPEN" and recovered.broker_order_id == "remote-queue-id"
    assert broker.submit_calls == 1
    assert restarted.cancel(recovered.intent.client_order_id).status == "CANCELED"
    assert restarted.recover().matched


def test_closed_remote_market_is_required_fact_not_a_default() -> None:
    """缺少远端休市事实必须校验失败，不能靠上下文默认值宣称市场已关闭。"""
    payload = queue_case()["context"].model_dump()
    payload.pop("remote_is_open")
    with pytest.raises(ValueError, match="remote_is_open"):
        PaperQueueTestContext.model_validate(payload)
