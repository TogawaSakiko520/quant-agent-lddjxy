"""使用独立脱敏 JSON 验证 Paper 映射、资金边界与恢复；普通测试绝不访问账户。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from quant_core.adapters.alpaca import (
    AlpacaHTTPError,
    AlpacaPaperBroker,
    AlpacaSDKTransport,
)
from quant_core.contracts import ContractError, FillEvent, OrderIntent

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)


class FixedClock:
    """固定只读采集时间，不以运行测试的真实时间改变预期。"""

    def now(self) -> datetime:
        """返回固定常规交易时段。"""
        return NOW


class FixtureTransport:
    """保存独立响应和请求记录；不包含 SDK 或网络能力。"""

    def __init__(self) -> None:
        """构造账户、订单和分页事实，订单均为 3 股限价 10 美元。"""
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.order: dict[str, Any] = {
            "id": "remote-1",
            "client_order_id": "client-1",
            "asset_id": "asset-1",
            "symbol": "ABC",
            "side": "buy",
            "qty": "3",
            "filled_qty": "0",
            "limit_price": "10.00",
            "type": "limit",
            "time_in_force": "day",
            "extended_hours": False,
            "status": "new",
        }
        self.account: dict[str, Any] = {
            "id": "paper-1",
            "currency": "USD",
            "status": "ACTIVE",
            "trading_blocked": False,
            "account_blocked": False,
            "trade_suspended_by_user": False,
            "cash": "1000.00",
            "buying_power": "4000.00",
            "non_marginable_buying_power": "1000.00",
        }
        self.positions: list[dict[str, Any]] = []
        self.activities: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.repeat_page = False

    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """按相对路径返回原始样例，记录每次提交以验证无盲目重试。"""
        self.calls.append((method, path, dict(data) if data is not None else None))
        if path == "/account":
            return dict(self.account)
        if path == "/positions":
            return self.positions
        if self.error is not None:
            raise self.error
        if path == "/orders:by_client_order_id" or method == "POST":
            return dict(self.order)
        if path == "/orders":
            return (
                []
                if data and "after_order_id" in data and not self.repeat_page
                else [dict(self.order)]
            )
        if path == "/account/activities":
            return [] if data and "page_token" in data else self.activities
        if method == "DELETE":
            # 撤单请求期间剩余股数成交，查询必须保留 FILLED，不能自造 CANCELED。
            self.order.update(status="filled", filled_qty="3")
            return None
        raise AssertionError("测试收到非预期请求")


def intent() -> OrderIntent:
    """建立已持久化的三股买入意图；与供应商样例分别构造经济字段。"""
    return OrderIntent(
        client_order_id="client-1",
        account_id="paper-1",
        decision_id="decision-1",
        security_id="asset-1",
        side="BUY",
        quantity=3,
        limit_price=Decimal("10.00"),
        created_at=NOW,
        eligible_at=NOW,
    )


def broker(transport: FixtureTransport, *, enabled: bool = False) -> AlpacaPaperBroker:
    """组装无网络代理，默认保持写能力关闭。"""
    return AlpacaPaperBroker(
        transport,
        "paper-1",
        FixedClock(),
        {"asset-1": "ABC"},
        lambda: [intent()],
        datetime(2026, 9, 18, 14, tzinfo=UTC),
        trading_enabled=enabled,
    )


def test_sdk_construction_is_offline_and_paper_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """指定环境变量构造不联网，SDK 固定端点且停用可能重发 POST 的默认重试。"""
    monkeypatch.setenv("TEST_PAPER_KEY", "not-a-real-key")
    monkeypatch.setenv("TEST_PAPER_SECRET", "not-a-real-secret")
    transport = AlpacaSDKTransport.from_environment("TEST_PAPER_KEY", "TEST_PAPER_SECRET")
    assert transport._client._retry == 0
    assert transport._client._base_url == "https://paper-api.alpaca.markets"
    assert transport._market._base_url == "https://data.alpaca.markets"
    assert transport._client._session.trust_env is False
    with pytest.raises(ContractError):
        AlpacaSDKTransport("test", "test", trading_url="https://api.alpaca.markets")
    with pytest.raises(ContractError):
        transport.request("GET", "//untrusted/account")
    with pytest.raises(ContractError):
        transport.market_request("https://untrusted/v2/stocks/bars")


def test_submit_preserves_decimal_limit_and_write_gate() -> None:
    """开闸前无 POST，开闸后精确传递限价和整股，不把受理当成成交。"""
    transport = FixtureTransport()
    with pytest.raises(ContractError, match="写能力"):
        broker(transport).submit(intent())
    assert transport.calls == []
    record = broker(transport, enabled=True).submit(intent())
    assert record.status == "OPEN"
    assert record.filled_quantity == 0
    assert transport.calls[-1] == (
        "POST",
        "/orders",
        {
            "symbol": "ABC",
            "qty": "3",
            "side": "buy",
            "type": "limit",
            "time_in_force": "day",
            "limit_price": "10.00",
            "extended_hours": False,
            "client_order_id": "client-1",
        },
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "other-account"),
        ("currency", "EUR"),
        ("status", "CLOSED"),
        ("trading_blocked", True),
        ("account_blocked", True),
        ("trade_suspended_by_user", True),
        ("non_marginable_buying_power", "29.99"),
    ],
)
def test_account_mismatch_or_cash_boundary_blocks_post(field: str, value: Any) -> None:
    """账户身份、资格或非保证金购买力失败时，充足杠杆购买力也不能放行。"""
    transport = FixtureTransport()
    transport.account[field] = value
    with pytest.raises(ContractError):
        broker(transport, enabled=True).submit(intent())
    assert not any(method == "POST" for method, _, _ in transport.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("qty", "3.5"),
        ("filled_qty", "0.1"),
        ("asset_id", "wrong-id"),
        ("side", "sell"),
        ("limit_price", "10.01"),
        ("time_in_force", "gtc"),
        ("type", "market"),
        ("extended_hours", True),
        ("client_order_id", "manual"),
    ],
)
def test_external_order_conflict_is_not_coerced(field: str, value: Any) -> None:
    """外部订单条件不符或含碎股，必须阻断；不能截断或创造策略意图。"""
    transport = FixtureTransport()
    transport.order[field] = value
    with pytest.raises(ContractError):
        broker(transport).orders()


def test_order_pagination_requires_progress() -> None:
    """一条记录正好填满limit=1时必须继续游标；忽略游标导致重复时拒绝半份数据。"""
    transport = FixtureTransport()
    params = {"status": "all", "limit": 1, "direction": "asc", "after": NOW.isoformat()}
    # limit=1使独立的一条订单样本确实是满页，不能用短页测试强求空页行为。
    assert len(broker(transport)._paginate("/orders", params, "after_order_id")) == 1
    assert transport.calls[1][2] == {
        "status": "all",
        "limit": 1,
        "direction": "asc",
        "after_order_id": "remote-1",
    }
    transport.repeat_page = True
    with pytest.raises(ContractError, match="分页"):
        broker(transport)._paginate("/orders", params, "after_order_id")


def test_order_short_page_ends_without_demanding_an_empty_cursor_page() -> None:
    """真实单订单短页即完整响应；供应商忽略多余游标也不应迫使已完整查询失败。"""
    transport = FixtureTransport()
    transport.repeat_page = True
    result = broker(transport)._paginate(
        "/orders", {"status": "all", "limit": 500, "direction": "asc"}, "after_order_id"
    )
    assert [row["id"] for row in result] == ["remote-1"]
    assert transport.calls == [
        ("GET", "/orders", {"status": "all", "limit": 500, "direction": "asc"})
    ]


def test_order_short_page_still_checks_duplicate_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """短页终止在身份校验之后执行，两条同ID不能因为少于500而被当成完整证据。"""
    transport = FixtureTransport()

    def duplicate_page(method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """返回独立构造的页内重复订单，检验短页不会掩盖身份冲突。"""
        assert method == "GET" and path == "/orders"
        return [dict(transport.order), dict(transport.order)]

    monkeypatch.setattr(transport, "request", duplicate_page)
    with pytest.raises(ContractError, match="分页身份缺失或重复"):
        broker(transport)._paginate("/orders", {"limit": 500}, "after_order_id")


def test_activity_short_page_keeps_existing_cursor_completion_rule() -> None:
    """订单短页规则不改变账户活动读取，活动仍继续page_token直到明确空页。"""
    transport = FixtureTransport()
    transport.activities = [{"id": "activity-1"}]
    assert broker(transport).raw_activities() == [{"id": "activity-1"}]
    assert len(transport.calls) == 2
    assert transport.calls[1][2] == {
        "after": "2026-09-18T14:00:00+00:00",
        "direction": "asc",
        "page_size": 100,
        "page_token": "activity-1",
    }


def test_new_fill_quantity_comes_from_activity_not_cumulative_order() -> None:
    """两笔一股与两股活动独立入账；订单累计三股和均价不能再生成第三笔成交。"""
    transport = FixtureTransport()
    transport.order.update(status="filled", filled_qty="3", filled_avg_price="9.90")
    transport.activities = [
        {
            "id": "fill-1",
            "activity_type": "FILL",
            "order_id": "remote-1",
            "symbol": "ABC",
            "side": "buy",
            "qty": "1",
            "price": "9.80",
            "transaction_time": "2026-09-18T14:59:01Z",
        },
        {
            "id": "fill-2",
            "activity_type": "FILL",
            "order_id": "remote-1",
            "symbol": "ABC",
            "side": "buy",
            "qty": "2",
            "price": "9.95",
            "transaction_time": "2026-09-18T14:59:02Z",
        },
    ]
    events = broker(transport).events()
    assert len(events) == 2
    assert all(isinstance(event, FillEvent) for event in events)
    fills = [event for event in events if isinstance(event, FillEvent)]
    assert [(event.fill_id, event.quantity, event.price) for event in fills] == [
        ("fill-1", 1, Decimal("9.80")),
        ("fill-2", 2, Decimal("9.95")),
    ]
    assert sum(event.price * event.quantity for event in fills) == Decimal("29.70")
    assert broker(transport).events() == events
    transport.activities.append({"id": "div-1", "activity_type": "DIV"})
    with pytest.raises(ContractError, match="非成交"):
        broker(transport).events()


def test_cancel_race_returns_fill_and_404_is_not_rejection() -> None:
    """撤单期间成交必须保持 FILLED；查单 404 返回未确认而非 REJECTED。"""
    transport = FixtureTransport()
    result = broker(transport, enabled=True).cancel("client-1")
    assert result.status == "FILLED"
    assert result.filled_quantity == 3
    transport.error = AlpacaHTTPError(404)
    assert broker(transport).query("client-1") is None


def test_timeout_has_one_post_and_no_retry() -> None:
    """提交超时仅产生一次 POST；由执行服务保存 UNKNOWN 后恢复查询。"""
    transport = FixtureTransport()
    transport.error = TimeoutError("脱敏超时")
    with pytest.raises(TimeoutError):
        broker(transport, enabled=True).submit(intent())
    assert sum(method == "POST" for method, _, _ in transport.calls) == 1


def test_cash_is_not_leveraged_buying_power_and_fractional_facts_stop() -> None:
    """一千美元现金不能变成四千购买力；0.5 股不能被截断为零。"""
    transport = FixtureTransport()
    account = broker(transport).account()
    assert account.cash == Decimal("1000.00")
    assert account.available_cash == Decimal("1000.00")
    transport.positions = [
        {
            "asset_id": "asset-1",
            "symbol": "ABC",
            "side": "long",
            "qty": "0.5",
            "cost_basis": "5.00",
        }
    ]
    with pytest.raises(ContractError, match="碎股"):
        broker(transport).account()


def test_sdk_http_options_and_market_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """在 Session 边界拦截请求，证明实际 SDK 禁止重定向、带超时且两个主机分开。"""
    import importlib

    calls: list[tuple[str, str, dict[str, Any]]] = []

    class Response:
        """提供 SDK 所需的最小脱敏 HTTP 成功响应。"""

        text = "{}"

        def raise_for_status(self) -> None:
            """本用例固定 HTTP 200，不构造错误状态。"""

        def json(self) -> dict[str, Any]:
            """返回空 JSON，不依赖任何真实服务。"""
            return {}

    def intercept(session: Any, method: str, url: str, **kwargs: Any) -> Response:
        """记录 SDK 实际请求参数，代替所有外部通信。"""
        calls.append((method, url, kwargs))
        return Response()

    monkeypatch.setattr(importlib.import_module("requests").Session, "request", intercept)
    transport = AlpacaSDKTransport("fixture-key", "fixture-secret")
    assert calls == []
    transport.request("POST", "/orders", {"limit_price": "10.0001", "qty": "3"})
    transport.market_request("/v2/stocks/bars", {"feed": "sip"})
    assert [url for _, url, _ in calls] == [
        "https://paper-api.alpaca.markets/v2/orders",
        "https://data.alpaca.markets/v2/stocks/bars",
    ]
    assert all(options["allow_redirects"] is False for _, _, options in calls)
    assert all(options["timeout"] == (10, 30) for _, _, options in calls)
    assert calls[0][2]["json"] == {"limit_price": "10.0001", "qty": "3"}
    assert transport._market._api_version == "v2"


def test_sdk_exception_discards_secret_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """供应商异常正文不进入外层异常消息或可显示的异常链。"""
    transport = AlpacaSDKTransport("fixture-key", "fixture-secret")

    def fail(path: str, data: Any = None) -> Any:
        """模拟可能含敏感请求正文的 SDK 异常。"""
        raise RuntimeError("fixture-secret in request headers")

    monkeypatch.setattr(transport._client, "post", fail)
    with pytest.raises(ConnectionError) as captured:
        transport.request("POST", "/orders", {})
    assert "fixture-secret" not in str(captured.value)
    assert captured.value.__suppress_context__ is True


def test_old_open_order_remains_visible_outside_activity_boundary() -> None:
    """旧关闭订单不需伪造本地意图，但初态以前的开放订单始终读取并阻断。"""

    class OldOpenTransport(FixtureTransport):
        """时间范围内无新单，只有账户上更早的人工开放订单。"""

        def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
            """根据 open 查询返回旧人工单；所有时间过滤的历史查询为空。"""
            if path == "/orders" and data and data.get("status") == "all":
                return []
            return super().request(method, path, data)

    transport = OldOpenTransport()
    transport.order["client_order_id"] = "manual-before-baseline"
    with pytest.raises(ContractError, match="外部订单"):
        broker(transport).orders()


def test_invalid_price_tick_is_rejected_without_changing_intent() -> None:
    """100.12美元乘1.01得到101.1212；不默默改价，也不发已知会拒绝的委托。"""
    transport = FixtureTransport()
    invalid = intent().model_copy(update={"limit_price": Decimal("101.1212")})
    adapter = AlpacaPaperBroker(
        transport,
        "paper-1",
        FixedClock(),
        {"asset-1": "ABC"},
        lambda: [invalid],
        NOW,
        trading_enabled=True,
    )
    with pytest.raises(ContractError, match="价格步长"):
        adapter.submit(invalid)
    assert invalid.limit_price == Decimal("101.1212")
    assert transport.calls == []


def test_baseline_deposit_is_not_reapplied_and_changed_or_new_deposit_blocks() -> None:
    """初态现金已含初始化入金，只豁免相同ID与内容；新入金或旧ID修订必须阻断。"""
    transport = FixtureTransport()
    deposit = {
        "id": "deposit-1",
        "activity_type": "JNLC",
        "net_amount": "1000.00",
        "date": "2026-09-18",
        "status": "executed",
    }
    transport.activities = [dict(deposit)]
    adapter = AlpacaPaperBroker(
        transport,
        "paper-1",
        FixedClock(),
        {"asset-1": "ABC"},
        lambda: [intent()],
        NOW,
        baseline_activities=[deposit],
    )
    assert adapter.events() == []
    assert adapter.account().cash == Decimal("1000.00")
    transport.activities[0]["net_amount"] = "1001.00"
    with pytest.raises(ContractError, match="初态"):
        adapter.events()
    transport.activities = [dict(deposit), {**deposit, "id": "deposit-2"}]
    with pytest.raises(ContractError, match="非成交"):
        adapter.events()


def test_read_snapshot_rejects_cash_change_during_collection() -> None:
    """采集活动时发生现金变化，不把先取现金和后取活动拼成稳定初态。"""

    class ChangingAccount(FixtureTransport):
        """活动请求后模拟独立入金改变远端现金。"""

        def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
            """保留普通响应，在活动读取阶段独立改变账户现金。"""
            result = super().request(method, path, data)
            if path == "/account/activities":
                self.account["cash"] = "1001.00"
            return result

    with pytest.raises(ContractError, match="采集期间"):
        broker(ChangingAccount()).read_snapshot()
