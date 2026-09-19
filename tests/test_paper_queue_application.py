"""独立脱敏响应验证休市提交、远端查询、撤单及重启，不连接真实账户。"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from test_ma_application import MATransport
from test_paper_application import ScenarioClock

from quant_core.adapters.storage import read_json, write_json
from quant_core.application import paper_execute, paper_plan, paper_read
from quant_core.contracts import ContractError, PaperConfig
from tools.paper_viewer import build_view

QUEUE_AT = datetime(2026, 9, 16, 21, tzinfo=UTC)


class QueueTransport(MATransport):
    """独立返回休市与下一开盘事实；订单受理不产生任何成交。"""

    def __init__(self, clock: ScenarioClock) -> None:
        """继承手写价格样本，关闭模拟成交并指定周三收盘后的观测时间。"""
        super().__init__(clock)
        self.submit_fill = 0
        self.open = False
        self.cash_change_after_submit = False
        self.naive_clock = False

    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """只补远端时钟必须返回的下一开盘字段；其他远端行为由独立响应维护。"""
        response = super().request(method, path, data)
        if self.cash_change_after_submit and method == "POST":
            # 模拟与本单成交无关的外部现金差异；不补造FILL来解释它。
            self.remote_cash -= Decimal("1")
        if path == "/clock":
            # 官方时钟使用纽约偏移，适配必须保留实际时刻并规范为UTC契约。
            response["timestamp"] = (
                self.clock.now().astimezone(timezone(timedelta(hours=-4))).isoformat()
            )
            response["next_open"] = "2026-09-17T13:30:00+00:00"
            if self.naive_clock:
                response["timestamp"] = self.clock.now().replace(tzinfo=None).isoformat()
        if path == "/calendar":
            # 真实日历接口只返回请求的含首尾范围，不能沿用较晚采集样本的越界尾日。
            params = data or {}
            response = [row for row in response if params["start"] <= row["date"] <= params["end"]]
        return response


def prepared_queue(tmp_path: Path) -> tuple[Path, str, QueueTransport, ScenarioClock]:
    """四个真实来源形状的候选生成三个目标，测试只取排名首位DDD的一股。"""
    clock = ScenarioClock()
    clock.at = QUEUE_AT
    transport = QueueTransport(clock)
    config = PaperConfig(
        account_id="paper-test",
        strategy="ma-trend",
        candidates=["AAA", "BBB", "CCC", "DDD"],
        budget=Decimal("10000"),
        history_start=transport.days[0],
        history_end=transport.days[-1],
    )
    source = tmp_path / "read"
    paper_read(config, source, cast(Any, transport), clock)
    identity = tmp_path / "identity.json"
    write_json(
        identity,
        {
            "records": [
                {
                    "symbol": row["symbol"],
                    "alpaca_asset_id": row["id"],
                    "asset_type_evidence": "common_stock",
                    "source": "https://example.test/security",
                    "observed_at": QUEUE_AT.isoformat(),
                }
                for row in transport.assets
            ]
        },
    )
    root = tmp_path / "plan"
    plan = paper_plan(source, None, root, identity_path=identity)
    return root, plan["plan_id"], transport, clock


def execute_queue(
    root: Path, plan: str, transport: QueueTransport, clock: ScenarioClock, **options: Any
) -> dict[str, Any]:
    """按一笔500美元边界执行排队撤单，不使用真实等待以外的假成交。"""
    return paper_execute(
        root,
        cast(Any, transport),
        clock,
        approved_plan=plan,
        max_orders=1,
        max_order_notional=Decimal("500"),
        unfilled="cancel",
        queue_cancel_test=True,
        **options,
    )


def test_real_shaped_queue_cancel_restart_and_view(tmp_path: Path) -> None:
    """受理一股100美元、查询远端ID并取消；独立重启核对不发第二单或改现金。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    result = execute_queue(root, plan, transport, clock)
    assert result["submitted"] == 1 and result["filled"] == 0
    assert result["queue_cancel_verified"] and not result["queue_restart_verified"]
    sent = [data for method, path, data in transport.calls if method == "POST"]
    assert len(sent) == 1
    assert sent[0]["symbol"] == "DDD" and sent[0]["qty"] == "1"
    assert sent[0]["limit_price"] == "100.00" and sent[0]["extended_hours"] is False
    assert sent[0]["type"] == "limit" and sent[0]["time_in_force"] == "day"
    observed = root / "observations/0001"
    assert read_json(observed / "queue-query.json")["broker_order_id"] == "order-1"
    assert read_json(observed / "queue-before-cancel.json")["orders"][0]["status"] == "new"
    assert read_json(observed / "orders.json")[0]["status"] == "CANCELED"
    assert read_json(observed / "account.json")["cash"] == "10000"
    # journal含意图及状态持久化；无成交表示没有账务事件，不能误要求删除订单审计。
    journal = read_json(observed / "journal.json")
    assert [row["operation"] for row in journal] == ["intent", "order", "order"]
    assert [row["payload"]["status"] for row in journal[1:]] == ["OPEN", "CANCELED"]
    assert read_json(observed / "events.json") == []
    assert transport.remote_cash == Decimal("100000") and transport.positions == {}
    # 每次调用重新打开同一事件库，恢复只能查询原身份，且不重复撤终态订单。
    recovered = execute_queue(root, plan, transport, clock, recover_only=True)
    assert recovered["queue_restart_verified"] and not recovered["filled_and_reconciled"]
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1
    assert len([call for call in transport.calls if call[0] == "DELETE"]) == 1
    view = build_view(root)
    assert view["queue_verified"] is True
    assert view["pipeline"][-1]["state"] == "passed"
    assert view["pipeline"][-2]["state"] != "passed"
    assert view["title"] == "策略闭环尚未完成"


def test_queue_timeout_after_accept_recovers_and_cancels(tmp_path: Path) -> None:
    """远端受理后本地超时，仍按原client ID查回并取消，不能重发。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    transport.timeout_after_accept = True
    result = execute_queue(root, plan, transport, clock)
    assert result["queue_cancel_verified"]
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1
    assert read_json(root / "observations/0001/queue-submission.json")["status"] == "UNKNOWN"


def test_queue_pending_cancel_stays_unverified(tmp_path: Path) -> None:
    """撤单一直pending时有界核对，保留开放事实而非伪造CANCELED。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    transport.defer_cancel = True
    elapsed = [0.0]

    def wait(seconds: float) -> None:
        """仅推进测试单调时钟与真实形状的观测时间，不制造券商状态变化。"""
        elapsed[0] += seconds
        clock.at += timedelta(seconds=seconds)

    result = execute_queue(root, plan, transport, clock, wait=wait, monotonic=lambda: elapsed[0])
    assert elapsed[0] == 60
    assert result["pending"] == 1 and not result["queue_cancel_verified"]
    assert len([call for call in transport.calls if call[0] == "DELETE"]) == 1


def test_queue_open_market_does_not_submit(tmp_path: Path) -> None:
    """远端报告开市即阻断独立排队测试，不能假称休市提交。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    transport.open = True
    result = execute_queue(root, plan, transport, clock)
    assert result["status"] == "blocked"
    assert not any(call[0] == "POST" for call in transport.calls)


def test_default_execute_still_rejects_closed_market(tmp_path: Path) -> None:
    """未带显式测试模式时，旧常规执行时段限制保持有效。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    result = paper_execute(
        root,
        cast(Any, transport),
        clock,
        approved_plan=plan,
        max_orders=1,
        max_order_notional=Decimal("500"),
        unfilled="cancel",
    )
    assert result["status"] == "blocked"
    assert not any(call[0] == "POST" for call in transport.calls)


def test_queue_directory_cannot_be_reused_for_normal_submit(tmp_path: Path) -> None:
    """已保存排队用途的目录不能意外转成正常发单，保留独立测试身份。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    execute_queue(root, plan, transport, clock)
    with pytest.raises(ContractError, match="不能转为普通"):
        paper_execute(
            root,
            cast(Any, transport),
            clock,
            approved_plan=plan,
            max_orders=1,
            max_order_notional=Decimal("500"),
            unfilled="cancel",
        )


def test_queue_requires_cancel_and_one_order(tmp_path: Path) -> None:
    """独立测试不能被参数扩大为三笔或把未成交单保留到未来开盘。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    for maximum, policy in [(3, "cancel"), (1, "keep")]:
        with pytest.raises(ContractError, match="最多一笔"):
            paper_execute(
                root,
                cast(Any, transport),
                clock,
                approved_plan=plan,
                max_orders=maximum,
                max_order_notional=Decimal("500"),
                unfilled=policy,
                queue_cancel_test=True,
            )
    assert not any(call[0] == "POST" for call in transport.calls)


def test_known_queue_order_cancels_even_when_cash_differs(tmp_path: Path) -> None:
    """提交后现金出现未解释差异，已确认本轮订单仍撤销，账本差异必须保留。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    transport.cash_change_after_submit = True
    result = execute_queue(root, plan, transport, clock)
    assert result["status"] == "blocked" and not result["reconciled"]
    assert not result["queue_cancel_verified"]
    assert len([call for call in transport.calls if call[0] == "DELETE"]) == 1
    assert transport.orders[0]["status"] == "canceled"
    assert transport.remote_cash == Decimal("99999")
    assert read_json(root / "observations/0001/account.json")["cash"] == "10000"
    assert (
        "account_mismatch:cash"
        in read_json(root / "observations/0001/reconciliation.json")["differences"]
    )


def test_normal_order_directory_cannot_become_queue_test(tmp_path: Path) -> None:
    """普通策略已发的开放订单不能被新测试开关接管撤销或冒充排队验收。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    clock.at = datetime(2026, 9, 17, 14, tzinfo=UTC)
    transport.open = True
    normal = paper_execute(
        root,
        cast(Any, transport),
        clock,
        approved_plan=plan,
        max_orders=1,
        max_order_notional=Decimal("500"),
        unfilled="keep",
    )
    assert normal["submitted"] == 1 and normal["pending"] == 1
    with pytest.raises(ContractError, match="普通订单的目录不能转"):
        execute_queue(root, plan, transport, clock)
    assert not any(call[0] == "DELETE" for call in transport.calls)
    assert transport.orders[0]["status"] == "new"


def test_queue_unexpected_fill_does_not_claim_strategy_completion(tmp_path: Path) -> None:
    """即使排队测试响应意外成交，也如实入账并保持正式策略验收未完成。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    transport.submit_fill = 1
    result = execute_queue(root, plan, transport, clock)
    assert result["filled"] == 1 and result["reconciled"]
    assert not result["queue_cancel_verified"] and not result["filled_and_reconciled"]
    recovered = execute_queue(root, plan, transport, clock, recover_only=True)
    assert not recovered["restart_verified"] and not recovered["queue_restart_verified"]
    assert read_json(root / "observations/0002/account.json")["cash"] == "9900.00"
    view = build_view(root)
    assert view["title"] == "策略闭环尚未完成"
    assert view["queue_verified"] is False


def test_queue_missing_remote_timezone_is_rejected(tmp_path: Path) -> None:
    """无时区的券商时钟不能被自动当作本机时间，必须在任何提交前拒绝。"""
    root, plan, transport, clock = prepared_queue(tmp_path)
    transport.naive_clock = True
    result = execute_queue(root, plan, transport, clock)
    assert result["status"] == "blocked" and result["submitted"] == 0
    assert not any(call[0] == "POST" for call in transport.calls)
    assert "必须明确时区" in read_json(root / "observations/0001/failure.json")["reason"]


def test_legacy_unsent_requires_exact_archived_source(tmp_path: Path) -> None:
    """普通计划不能借维护开关把待确认改成拒绝，空源码及其他用途都被拒绝。"""
    from quant_core.application import _queue_unsent_proof

    root, plan, transport, clock = prepared_queue(tmp_path)
    execute_queue(root, plan, transport, clock)
    with pytest.raises(ContractError, match="原始观察"):
        # 构造旧失败外形不足以证明指定旧源码；完整代码树指纹必须精确匹配。
        write_json(
            root / "observations/0001/failure.json", {"type": "ContractError", "reason": "old"}
        )
        _queue_unsent_proof(root, tmp_path / "absent-source")
    with pytest.raises(ContractError, match="只允许显式恢复"):
        execute_queue(root, plan, transport, clock, confirm_unsent_source=tmp_path)
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved_plan", "other"),
        ("queue_cancel_test", False),
        ("max_orders", 2),
        ("unfilled", "keep"),
    ],
)
def test_unsent_proof_binds_original_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    """仅隔离源码哈希前提，逐项扰动旧授权；不能用当前授权替代当时订单用途。"""
    import quant_core.application as application

    source_hash = "07be0b052412b809fb4acf317202d0cb0a41e0861744f629c77a30c205eef072"
    original = tmp_path / "observations/0001"
    original.mkdir(parents=True)
    write_json(
        tmp_path / "paper-plan.json", {"plan_id": "plan", "code": {"source_hash": source_hash}}
    )
    write_json(tmp_path / "queue-test.json", {"plan_id": "plan", "client_order_id": "queue"})
    write_json(
        original / "failure.json",
        {"type": "ContractError", "reason": "Paper 排队测试的单股、金额、身份或休市边界不符"},
    )
    authorization = {
        "approved_plan": "plan",
        "queue_cancel_test": True,
        "max_orders": 1,
        "unfilled": "cancel",
        "current_code": {"source_hash": source_hash},
    }
    authorization[field] = value
    write_json(original / "authorization-boundaries.json", authorization)
    monkeypatch.setattr(application, "canonical_hash", lambda data: source_hash)
    with pytest.raises(ContractError, match="原始观察"):
        application._queue_unsent_proof(tmp_path, tmp_path / "source")
