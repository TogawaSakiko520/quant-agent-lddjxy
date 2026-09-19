"""提交前确定拒绝与旧版定向维护的独立测试；远端缺单绝不自动构成未发送证明。"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_paper_queue_risk import NOW, QueueBroker, service_case

from quant_core.adapters.sqlite_store import SQLiteEventStore
from quant_core.contracts import (
    ContractError,
    FillEvent,
    OrderIntent,
    OrderNotSent,
    OrderRecord,
    QueuePreflightRejectionProof,
)
from quant_core.execution import ExecutionService

CLIENT = "queue-06ea96562f420f84391d5d33f094135e"
OLD_HASH = "07be0b052412b809fb4acf317202d0cb0a41e0861744f629c77a30c205eef072"
REASON = "Paper 排队测试的单股、金额、身份或休市边界不符"


class PreflightBroker(QueueBroker):
    """请求发送前明确拒绝的脱敏适配器；调用次数不是券商受理次数。"""

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """只累计提交尝试并抛专用异常，远端订单集合保持为空。"""
        self.submit_calls += 1
        raise OrderNotSent("独立提交前拒绝样本")


class AmbiguousBroker(QueueBroker):
    """普通契约失败不能证明发送边界，保留未知而非凭异常文本确定拒绝。"""

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """模拟无响应的普通契约异常，其消息恰好等于旧版提交前消息。"""
        self.submit_calls += 1
        raise ContractError(REASON)


class AcceptedBadResponseBroker(QueueBroker):
    """券商已受理但回执解析出错，之后独立查询应恢复原远端身份。"""

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """先保存远端OPEN事实再抛解析异常，禁止归类为未发送。"""
        super().submit(intent)
        raise ContractError("脱敏回执解析错误")


def proof_case() -> QueuePreflightRejectionProof:
    """构造独立审计样本；固定客户键由规范JSON摘要预先计算，证据hash为脱敏占位。"""
    return QueuePreflightRejectionProof.model_validate(
        {
            "plan_id": "approved-plan",
            "client_order_id": CLIENT,
            "failed_source_hash": OLD_HASH,
            "failure_reason": REASON,
            "evidence_hash": "a" * 64,
        }
    )


def legacy_case(tmp_path: Path) -> tuple[ExecutionService, QueueBroker, dict[str, Any]]:
    """落盘唯一旧意图及UNKNOWN，保留空远端账户；没有调用任何提交方法。"""
    service, broker, inputs = service_case(tmp_path)
    inputs["intent"] = inputs["intent"].model_copy(update={"client_order_id": CLIENT})
    inputs["context"] = inputs["context"].model_copy(update={"client_order_id": CLIENT})
    service.store.save_intent(inputs["intent"])
    service.store.record_order(OrderRecord(intent=inputs["intent"], status="UNKNOWN"))
    return service, broker, inputs


def test_only_dedicated_not_sent_error_records_local_rejection(tmp_path: Path) -> None:
    """专用异常追加本地拒绝；重启再次调用只返回旧拒绝，不再发送或改现金。"""
    service, original, inputs = service_case(tmp_path)
    broker = PreflightBroker(original.snapshot)
    service.broker = broker
    with pytest.raises(OrderNotSent):
        service.submit_paper_queue_test(**inputs)
    assert service.store.orders()[0].status == "REJECTED"
    assert service.store.orders()[0].broker_order_id is None
    assert broker.records == {} and broker.submit_calls == 1
    assert service.recover().matched
    restarted = ExecutionService(
        SQLiteEventStore(tmp_path / "internal.sqlite", broker.snapshot),
        broker,
        service.clock,
        service.config,
        service.calendar,
    )
    assert restarted.submit_paper_queue_test(**inputs).status == "REJECTED"
    assert broker.submit_calls == 1
    assert restarted.store.account(NOW).cash == Decimal("10000")
    assert restarted.store.events() == []


@pytest.mark.parametrize("broker_type", [AmbiguousBroker, AcceptedBadResponseBroker])
def test_ordinary_contract_error_never_implies_not_sent(
    tmp_path: Path, broker_type: type[QueueBroker]
) -> None:
    """普通异常文字不是证明；无订单时保持未知，有远端事实时恢复OPEN。"""
    service, original, inputs = service_case(tmp_path)
    broker = broker_type(original.snapshot)
    service.broker = broker
    with pytest.raises(ContractError):
        service.submit_paper_queue_test(**inputs)
    assert service.store.orders()[0].status == "PERSISTED"
    recovered = service.recover()
    expected = "UNKNOWN" if broker_type is AmbiguousBroker else "OPEN"
    assert service.store.orders()[0].status == expected
    assert recovered.matched is (broker_type is AcceptedBadResponseBroker)
    assert service.submit_paper_queue_test(**inputs).status == expected
    assert broker.submit_calls == 1


def test_exact_legacy_proof_appends_rejection_preserving_history_and_no_resubmit(
    tmp_path: Path,
) -> None:
    """维护只追加拒绝；原UNKNOWN日志、现金、事件及冻结均原样保留，重启不能再发送。"""
    service, broker, inputs = legacy_case(tmp_path)
    prior = service.store.journal()
    result = service.confirm_queue_not_sent(CLIENT, proof=proof_case())
    assert result.matched and result.differences == []
    journal = service.store.journal()
    assert journal[:2] == prior and len(journal) == 3
    assert journal[-1].operation == "order"
    assert isinstance(journal[-1].payload, OrderRecord)
    assert journal[-1].payload.status == "REJECTED"
    assert service.store.account(NOW).cash == Decimal("10000")
    assert service.store.account(NOW).positions == {}
    assert service.store.events() == [] and service.store.frozen() == []
    with pytest.raises(ContractError):
        service.confirm_queue_not_sent(CLIENT, proof=proof_case())
    restarted = ExecutionService(
        SQLiteEventStore(tmp_path / "internal.sqlite", broker.snapshot),
        broker,
        service.clock,
        service.config,
        service.calendar,
    )
    assert restarted.submit_paper_queue_test(**inputs).status == "REJECTED"
    assert broker.submit_calls == 0 and restarted.store.journal() == journal


@pytest.mark.parametrize(
    "patch",
    [
        {"failed_source_hash": "b" * 64},
        {"failure_reason": "other preflight failure"},
        {"evidence_hash": "not-a-hash"},
        {"client_order_id": "other"},
        {"plan_id": "other-plan"},
    ],
)
def test_missing_or_wrong_proof_cannot_convert_absence_into_rejection(
    tmp_path: Path, patch: dict[str, str]
) -> None:
    """无证明或错误来源、原因、摘要和身份均拒绝；model_copy也不能绕过验证。"""
    service, broker, _ = legacy_case(tmp_path)
    prior = service.store.journal()
    with pytest.raises(TypeError):
        service.confirm_queue_not_sent(CLIENT)  # type: ignore[call-arg]
    with pytest.raises(ContractError):
        service.confirm_queue_not_sent(CLIENT, proof=proof_case().model_copy(update=patch))
    assert service.store.journal() == prior and broker.submit_calls == 0


@pytest.mark.parametrize("condition", ["broker_id", "filled", "cash", "frozen", "remote"])
def test_other_facts_and_discrepancies_block_legacy_maintenance(
    tmp_path: Path, condition: str
) -> None:
    """券商身份、累计成交、现金差异、冻结或远端订单各自阻止维护，不能借维护消除差异。"""
    service, broker, inputs = legacy_case(tmp_path)
    if condition in {"broker_id", "filled"}:
        update = (
            {"broker_order_id": "real-order"}
            if condition == "broker_id"
            else {"filled_quantity": 1}
        )
        service.store.record_order(service.store.orders()[0].model_copy(update=update))
    elif condition == "cash":
        broker.snapshot = broker.snapshot.model_copy(update={"cash": Decimal("9999")})
    elif condition == "frozen":
        service.store.freeze("independent-hold")
    else:
        broker.records[CLIENT] = OrderRecord(
            intent=inputs["intent"], status="OPEN", broker_order_id="real-order"
        )
    with pytest.raises(ContractError):
        service.confirm_queue_not_sent(CLIENT, proof=proof_case())
    assert service.store.orders()[0].status != "REJECTED"
    assert broker.submit_calls == 0
    assert service.store.account(NOW).cash == Decimal("10000")
    if condition == "frozen":
        assert service.store.frozen() == ["independent-hold"]


def test_recorded_fill_and_missing_intent_journal_block_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """即使伪投影声称零成交，也要核对逐笔事实和原始意图日志，不能只看订单摘要。"""
    service, broker, _ = legacy_case(tmp_path)
    fill = FillEvent(
        event_id="event",
        fill_id="fill",
        account_id="paper-a",
        client_order_id=CLIENT,
        broker_order_id="real-order",
        security_id="A",
        side="BUY",
        quantity=1,
        price=Decimal("100"),
        fee=Decimal("1"),
        at=NOW,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(service.store, "events", lambda: [fill])
        with pytest.raises(ContractError, match="没有成交事实"):
            service.confirm_queue_not_sent(CLIENT, proof=proof_case())
    with monkeypatch.context() as scoped:
        scoped.setattr(service.store, "journal", lambda: [])
        with pytest.raises(ContractError, match="唯一原意图"):
            service.confirm_queue_not_sent(CLIENT, proof=proof_case())
    assert service.store.orders()[0].status == "UNKNOWN" and broker.submit_calls == 0


def test_last_query_discovering_order_blocks_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次恢复尚未见单、紧接查询出现远端ID时必须停止维护，不能写确定拒绝。"""
    service, broker, inputs = legacy_case(tmp_path)
    calls = 0

    def later_order(client_order_id: str) -> OrderRecord | None:
        """第二次查同一客户身份时才看到远端受理，独立模拟查询竞态。"""
        nonlocal calls
        assert client_order_id == CLIENT
        calls += 1
        if calls == 1:
            return None
        return OrderRecord(intent=inputs["intent"], status="OPEN", broker_order_id="late-id")

    monkeypatch.setattr(broker, "query", later_order)
    with pytest.raises(ContractError, match="查询到远端订单"):
        service.confirm_queue_not_sent(CLIENT, proof=proof_case())
    assert calls == 2 and broker.submit_calls == 0
    assert service.store.orders()[0].status == "UNKNOWN"


def test_legacy_maintenance_never_becomes_original_strategy_recovery(tmp_path: Path) -> None:
    """原策略即使复用同一证据和空远端，也不能进入限定MA缺陷维护。"""
    service, broker, _ = legacy_case(tmp_path)
    service.config = service.config.model_copy(update={"strategy": "weekly-two-factor"})
    with pytest.raises(ContractError, match="指定Paper MA"):
        service.confirm_queue_not_sent(CLIENT, proof=proof_case())
    assert service.store.orders()[0].status == "UNKNOWN" and broker.submit_calls == 0
