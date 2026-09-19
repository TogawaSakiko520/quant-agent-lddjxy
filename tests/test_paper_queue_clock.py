"""独立核验远端时钟偏移适配，不用本机时区或采集时刻替换券商事实。"""

from pathlib import Path
from typing import Any

import pytest
from test_paper_queue_application import execute_queue, prepared_queue

from quant_core.adapters.storage import read_json


def test_new_york_clock_normalizes_same_instant_without_restamping(tmp_path: Path) -> None:
    """官方形状17点负四小时应保存为21点UTC，参考价仍为20点完整收盘。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    result = execute_queue(root, plan, transport, clock)
    assert result["queue_cancel_verified"]
    context = read_json(root / "observations/0001/queue-context.json")
    assert context["remote_clock_at"] == "2026-09-16T21:00:00Z"
    assert context["reference_close_at"] == "2026-09-16T20:00:00Z"
    assert context["next_open"] == "2026-09-17T13:30:00Z"


def test_missing_remote_timezone_is_rejected_before_submission(tmp_path: Path) -> None:
    """无时区供应商字符串不能默认为本机时区；应在上下文构造之前显式阻断。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    original = transport.request

    def naive_response(method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """只移除远端时钟时间的时区，保留其它独立脱敏响应和真实观测时钟。"""
        response = original(method, path, data)
        if path == "/clock":
            response["timestamp"] = "2026-09-16T21:00:00"
        return response

    transport.request = naive_response  # type: ignore[method-assign]
    result = execute_queue(root, plan, transport, clock)
    assert result["status"] == "blocked"
    assert not any(call[0] == "POST" for call in transport.calls)
    failure = read_json(root / "observations/0001/failure.json")
    assert failure["type"] == "ContractError"
    assert "时区" in failure["reason"]
    assert not (root / "observations/0001/queue-context.json").exists()


@pytest.mark.parametrize(
    "lead,advances,allowed", [(0.125, True, True), (1.01, True, False), (0.125, False, False)]
)
def test_small_remote_lead_waits_for_real_clock_without_restamping(
    lead: float, advances: bool, allowed: bool
) -> None:
    """独立0.125秒偏差仅推进注入时钟，保留券商原时间；较大偏差与不前进均拒绝。"""
    from datetime import timedelta

    from test_alpaca import FixtureTransport
    from test_paper_application import ScenarioClock
    from test_paper_queue_risk import NOW, OPEN, queue_case

    from quant_core.adapters.alpaca import AlpacaPaperBroker
    from quant_core.contracts import OrderNotSent

    values = queue_case()
    intent = values["intent"]
    clock = ScenarioClock()
    clock.at = NOW
    transport = FixtureTransport()
    transport.account["id"] = "paper-a"
    transport.order.update(
        client_order_id=intent.client_order_id,
        asset_id="A",
        symbol="AAA",
        qty="1",
        limit_price="100",
    )
    original = transport.request
    waits: list[float] = []

    def request(method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """固定远端原时刻，不随等待重新产生未来时间。"""
        if path == "/clock":
            return {
                "timestamp": (NOW + timedelta(seconds=lead)).isoformat(),
                "is_open": False,
                "next_open": OPEN.isoformat(),
            }
        return original(method, path, data)

    def wait(seconds: float) -> None:
        """测试显式模拟真正经过的时间，也覆盖等待后时钟不前进的反例。"""
        waits.append(seconds)
        if advances:
            clock.at += timedelta(seconds=seconds)

    transport.request = request  # type: ignore[method-assign]
    broker = AlpacaPaperBroker(
        transport,
        "paper-a",
        clock,
        {"A": "AAA"},
        lambda: [intent],
        NOW,
        trading_enabled=True,
        clock_wait=wait,
    )
    broker.queue_test_context = values["context"]
    if allowed:
        assert broker.submit(intent).broker_order_id == "remote-1"
        assert waits == [0.125]
        assert broker.queue_clock_samples[0]["age_seconds"] == 0
        assert (
            broker.queue_clock_samples[0]["remote"] == (NOW + timedelta(seconds=0.125)).isoformat()
        )
    else:
        with pytest.raises(OrderNotSent):
            broker.submit(intent)
        assert not any(method == "POST" for method, _, _ in transport.calls)
        assert waits == ([] if lead > 1 else [0.125])
