"""独立脱敏传输贯穿 Paper 读取、原因子规划、受控执行和恢复；无真实账户访问。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from quant_core.adapters.alpaca import AlpacaHTTPError
from quant_core.adapters.paper_session import credential_transport
from quant_core.adapters.storage import read_json, write_json
from quant_core.application import paper_execute, paper_plan, paper_read
from quant_core.contracts import ContractError, PaperConfig, ReconciliationResult
from quant_core.execution import ExecutionService

OBSERVED = datetime(2026, 9, 19, 12, tzinfo=UTC)
EXECUTION = datetime(2026, 9, 21, 14, tzinfo=UTC)


class ScenarioClock:
    """由测试显式推进周末采集与下周开盘，不读真实时间。"""

    def __init__(self) -> None:
        """从周六开始采集，执行前由测试切到周一。"""
        self.at = OBSERVED

    def now(self) -> datetime:
        """返回当前测试业务时间。"""
        return self.at


class ScenarioTransport:
    """手写个人 Trading API 与 Market Data API 响应，独立维护远端现金和成交。"""

    def __init__(self, clock: ScenarioClock) -> None:
        """两证券原始价均100美元；账户现金十万美元而策略仅分配一万美元。"""
        self.clock = clock
        self.remote_cash = Decimal("100000")
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.orders: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.baseline_activities = [
            {
                "id": "initial-funding",
                "activity_type": "JNLC",
                "net_amount": "100000",
                "status": "executed",
                "date": "2026-09-18",
            }
        ]
        self.positions: dict[str, int] = {}
        self.timeout_after_accept = False
        self.open = True
        self.quote_price = 100
        self.submit_fill = 1
        self.quote_time: datetime | None = None
        self.days: list[date] = []
        day = date(2026, 9, 18)
        while len(self.days) < 253:
            if day.weekday() < 5:
                self.days.insert(0, day)
            day -= timedelta(days=1)
        future = [date(2026, 9, 21) + timedelta(days=i) for i in range(12)]
        self.calendar = [
            {"date": str(day), "open": "09:30", "close": "16:00"}
            for day in [*self.days, *(day for day in future if day.weekday() < 5)]
        ]
        self.assets = [
            {
                "id": f"asset-{symbol}",
                "symbol": symbol,
                "class": "us_equity",
                "status": "active",
                "tradable": True,
            }
            for symbol in ("AAA", "BBB")
        ]
        self.bars = {
            symbol: [
                {"t": f"{day}T05:00:00Z", "o": 100, "c": 100, "v": 1000000} for day in self.days
            ]
            for symbol in ("AAA", "BBB")
        }

    def _fill(self, order: dict[str, Any], quantity: int) -> None:
        """以独立固定100美元成交，更新远端事实；不调用项目账务实现。"""
        symbol = order["symbol"]
        self.positions[symbol] = self.positions.get(symbol, 0) + quantity
        self.remote_cash -= Decimal(100) * quantity
        order["filled_qty"] = str(int(order["filled_qty"]) + quantity)
        order["status"] = "filled" if order["filled_qty"] == order["qty"] else "partially_filled"
        self.fills.append(
            {
                "id": f"fill-{len(self.fills) + 1}",
                "activity_type": "FILL",
                "order_id": order["id"],
                "symbol": symbol,
                "side": "buy",
                "qty": str(quantity),
                "price": "100.00",
                "transaction_time": self.clock.now().isoformat(),
            }
        )

    def finish_orders(self) -> None:
        """独立模拟剩余三股后来成交；恢复必须只入账新增量。"""
        for order in self.orders:
            remaining = int(order["qty"]) - int(order["filled_qty"])
            if remaining:
                self._fill(order, remaining)

    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """按路径提供脱敏账户响应；唯一 POST 可在受理后故意超时。"""
        params = dict(data or {})
        self.calls.append((method, path, params))
        if path == "/account":
            return {
                "id": "paper-test",
                "currency": "USD",
                "status": "ACTIVE",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
                "cash": str(self.remote_cash),
                "non_marginable_buying_power": str(self.remote_cash),
                "buying_power": str(self.remote_cash * 4),
            }
        if path == "/positions":
            return [
                {
                    "asset_id": f"asset-{symbol}",
                    "symbol": symbol,
                    "side": "long",
                    "qty": str(quantity),
                    "cost_basis": str(quantity * 100),
                }
                for symbol, quantity in self.positions.items()
            ]
        if path == "/clock":
            return {"is_open": self.open, "timestamp": self.clock.now().isoformat()}
        if path == "/calendar":
            return self.calendar
        if path.startswith("/assets/"):
            return next(row for row in self.assets if row["symbol"] == path.split("/")[-1])
        if path == "/orders:by_client_order_id":
            for row in self.orders:
                if row["client_order_id"] == params["client_order_id"]:
                    return dict(row)
            raise AlpacaHTTPError(404)
        if path == "/orders" and method == "POST":
            row = {
                **params,
                "id": f"order-{len(self.orders) + 1}",
                "asset_id": f"asset-{params['symbol']}",
                "filled_qty": "0",
                "status": "new",
            }
            self.orders.append(row)
            if self.submit_fill:
                self._fill(row, self.submit_fill)
            if self.timeout_after_accept:
                raise TimeoutError("已受理后响应超时")
            return dict(row)
        if method == "DELETE" and path.startswith("/orders/"):
            row = next(row for row in self.orders if row["id"] == path.split("/")[-1])
            row["status"] = "canceled"
            return None
        if path == "/orders":
            if "after_order_id" in params:
                return []
            return [
                dict(row)
                for row in self.orders
                if params.get("status") != "open"
                or row["status"] not in {"filled", "canceled", "rejected", "expired"}
            ]
        if path == "/account/activities":
            return [] if "page_token" in params else [*self.baseline_activities, *self.fills]
        raise AssertionError(f"非预期脱敏请求：{method} {path}")

    def market_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """返回原始 SIP 日线或保留源时间的执行卖价；从不调用外网。"""
        self.calls.append(("GET", path, dict(params or {})))
        if path == "/v2/stocks/bars":
            return {"bars": self.bars, "next_page_token": None}
        if path == "/v2/stocks/quotes/latest":
            return {
                "quotes": {
                    symbol: {"t": (self.quote_time or self.clock.now()).isoformat(), "ap": 100}
                    for symbol in ("AAA", "BBB")
                }
            }
        raise AssertionError("非预期行情请求")


def prepared(
    tmp_path: Path, *, future_history_end: bool = False, daily_volume: int = 1000000
) -> tuple[Path, str, ScenarioTransport, ScenarioClock]:
    """准备253日原始行情及独立总回报资料；AAA动量0、BBB动量20%，最近60收益均零。"""
    clock = ScenarioClock()
    transport = ScenarioTransport(clock)
    for rows in transport.bars.values():
        for row in rows:
            row["v"] = daily_volume
    config = PaperConfig(
        account_id="paper-test",
        candidates=["AAA", "BBB"],
        budget=Decimal("10000"),
        history_start=transport.days[0],
        history_end=date(2026, 9, 25) if future_history_end else transport.days[-1],
    )
    source, target = tmp_path / "read", tmp_path / "plan"
    paper_read(config, source, cast(Any, transport), clock)
    supplement = {
        "schema_version": "1.0.0",
        "quality": "good",
        "price_basis": "dividend_reinvestment_total_return",
        "currency": "USD",
        "source": "https://example.test/independent-fixture",
        "methodology_source": "https://example.test/method",
        "methodology": "独立测试总回报价格，不属于真实数据质量验收",
        "observed_at": OBSERVED.isoformat(),
        "available_at": OBSERVED.isoformat(),
        "securities": [
            {
                "asset_id": f"asset-{symbol}",
                "symbol": symbol,
                "sector": sector,
                "asset_type": "common_stock",
            }
            for symbol, sector in (("AAA", "tech"), ("BBB", "health"))
        ],
        "prices": [
            {
                "asset_id": f"asset-{symbol}",
                "session": str(day),
                "total_return_close": 120 if symbol == "BBB" and i > 0 else 100,
            }
            for symbol in ("AAA", "BBB")
            for i, day in enumerate(transport.days)
        ],
    }
    supplement_path = tmp_path / "supplement.json"
    write_json(supplement_path, supplement)
    result = paper_plan(source, supplement_path, target)
    return target, result["plan_id"], transport, clock


def execute(
    root: Path, plan_id: str, transport: ScenarioTransport, clock: ScenarioClock, **changes: Any
) -> dict[str, Any]:
    """在明确批准的测试边界内调用真实应用装配入口。"""
    options = {
        "approved_plan": plan_id,
        "max_orders": 2,
        "max_order_notional": Decimal("500"),
        "unfilled": "keep",
        **changes,
    }
    return paper_execute(root, cast(Any, transport), clock, **options)


def test_paper_pipeline_original_factors_budget_partial_and_restart(tmp_path: Path) -> None:
    """253日两因子产生四股目标；部分成交与重启恢复核对且不重复发单。"""
    root, plan_id, transport, clock = prepared(tmp_path)
    factors = read_json(root / "factors.json")
    values = {(row["security_id"], row["factor_id"]): row["value"] for row in factors}
    assert values[("asset-AAA", "momentum")] == 0
    assert values[("asset-BBB", "momentum")] == pytest.approx(0.2)
    assert values[("asset-AAA", "low_volatility")] == 0
    assert values[("asset-BBB", "low_volatility")] == 0
    # 动量百分位0/1，低波动并列0.5，等权评分分别0.25/0.75。
    signals = read_json(root / "signals.json")
    assert [(row["security_id"], row["value"]) for row in signals["scores"]] == [
        ("asset-BBB", 0.75),
        ("asset-AAA", 0.25),
    ]
    target = read_json(root / "target.json")
    assert [(row["security_id"], row["quantity"]) for row in target["positions"]] == [
        ("asset-BBB", 4),
        ("asset-AAA", 4),
    ]
    assert Decimal(target["nav"]) == Decimal("10000")
    assert not transport.orders
    clock.at = EXECUTION
    result = execute(root, plan_id, transport, clock)
    assert result["submitted"] == 2
    assert result["filled"] == 0
    assert result["pending"] == 2
    assert result["reconciled"] is True
    account = read_json(Path(result["output"]) / "account.json")
    assert account["positions"] == {"asset-AAA": 1, "asset-BBB": 1}
    assert Decimal(account["cash"]) == Decimal("9800")
    assert transport.remote_cash == Decimal("99800")
    assert result["reserve"] == "90000"
    transport.finish_orders()
    result = execute(root, plan_id, transport, clock, recover_only=True)
    assert result["filled"] == 2 and result["pending"] == 0 and result["reconciled"] is True
    account = read_json(Path(result["output"]) / "account.json")
    assert account["positions"] == {"asset-AAA": 4, "asset-BBB": 4}
    assert Decimal(account["cash"]) == Decimal("9200")
    assert transport.remote_cash == Decimal("99200")
    execute(root, plan_id, transport, clock, recover_only=True)
    assert sum(method == "POST" for method, _, _ in transport.calls) == 2


def test_timeout_after_accept_never_blindly_resubmits(tmp_path: Path) -> None:
    """首单已受理但超时，只恢复原身份及真实一股成交，不因响应丢失发第二次。"""
    root, plan_id, transport, clock = prepared(tmp_path)
    clock.at = EXECUTION
    transport.timeout_after_accept = True
    result = execute(root, plan_id, transport, clock)
    assert result["submitted"] == 1 and result["pending"] == 1
    assert result["reconciled"] is True
    execute(root, plan_id, transport, clock, recover_only=True)
    assert sum(method == "POST" for method, _, _ in transport.calls) == 1


@pytest.mark.parametrize("boundary", ["count", "amount", "closed", "stale"])
def test_user_bounds_and_market_quality_do_not_expand_orders(tmp_path: Path, boundary: str) -> None:
    """显式数量/金额上限、休市或过期报价限制下不放大预算或使用旧报价下单。"""
    root, plan_id, transport, clock = prepared(tmp_path)
    clock.at = EXECUTION
    kwargs: dict[str, Any] = {}
    if boundary == "count":
        kwargs["max_orders"] = 1
    if boundary == "amount":
        kwargs["max_order_notional"] = Decimal("403.99")
    if boundary == "closed":
        transport.open = False
    if boundary == "stale":
        transport.quote_time = EXECUTION - timedelta(hours=1)
    result = execute(root, plan_id, transport, clock, **kwargs)
    assert result["submitted"] == (1 if boundary == "count" else 0)
    assert len(transport.orders) == (1 if boundary == "count" else 0)


def test_initial_holdings_and_tampered_plan_stop_before_post(tmp_path: Path) -> None:
    """不能删除旧仓开始策略，也不能批准后替换预算文件继续执行。"""
    root, plan_id, transport, clock = prepared(tmp_path)
    source = tmp_path / "read"
    observation = read_json(source / "account-observation.json")
    observation["positions"] = [{"asset_id": "outside", "qty": "1"}]
    (source / "account-observation.json").write_text(json.dumps(observation))
    with pytest.raises(ContractError, match="初态"):
        paper_plan(source, tmp_path / "supplement.json", tmp_path / "bad-plan")
    config = read_json(root / "config.json")
    config["budget"] = "100000"
    (root / "config.json").write_text(json.dumps(config))
    clock.at = EXECUTION
    with pytest.raises(ContractError, match="输入已改变"):
        execute(root, plan_id, transport, clock)
    assert transport.orders == []


def test_credentials_require_explicit_private_file_without_echoing_secret(tmp_path: Path) -> None:
    """过宽文件权限及错误账户均拒绝；凭据不写入异常消息。"""
    path = tmp_path / "explicit.env"
    path.write_text(
        "ALPACA_PAPER_API_KEY=sample-secret\nALPACA_PAPER_SECRET_KEY=sample-secret\n"
        "ALPACA_PAPER_ENDPOINT=https://paper-api.alpaca.markets/v2\n"
        "ALPACA_PAPER_ACCOUNT_ID=wrong-account\n"
    )
    path.chmod(0o644)
    with pytest.raises(ContractError, match="权限"):
        credential_transport(path, "paper-test")
    path.chmod(0o600)
    with pytest.raises(ContractError, match="账户") as captured:
        credential_transport(path, "paper-test")
    assert "sample-secret" not in str(captured.value)


@pytest.mark.parametrize("fill_quantity", [0, 1])
def test_canceled_order_with_changed_quote_is_not_resubmitted_same_plan(
    tmp_path: Path, fill_quantity: int
) -> None:
    """用户选择撤销后，同计划改价不能重发；部分成交也不能暗中创建补余量新身份。"""
    root, plan_id, transport, clock = prepared(tmp_path)
    clock.at = EXECUTION
    transport.submit_fill = fill_quantity
    first = execute(root, plan_id, transport, clock, unfilled="cancel", max_orders=4)
    assert first["submitted"] == 2 and first["pending"] == 0
    assert all(row["status"] == "canceled" for row in transport.orders)
    transport.quote_price = 101
    execute(root, plan_id, transport, clock, max_orders=4)
    assert sum(method == "POST" for method, _, _ in transport.calls) == 2


def test_recovery_keeps_unexplained_cash_difference_without_overwriting_ledger(
    tmp_path: Path,
) -> None:
    """远端出现缺少活动依据的一美元现金差异，恢复必须阻断且不改内部账本消差。"""
    root, plan_id, transport, clock = prepared(tmp_path)
    clock.at = EXECUTION
    first = execute(root, plan_id, transport, clock)
    assert first["reconciled"] is True
    transport.remote_cash -= Decimal("1")
    result = execute(root, plan_id, transport, clock, recover_only=True)
    assert result["reconciled"] is False
    account = read_json(Path(result["output"]) / "account.json")
    reconciliation = read_json(Path(result["output"]) / "reconciliation.json")
    assert Decimal(account["cash"]) == Decimal("9800")
    assert "account_mismatch:cash" in reconciliation["differences"]
    assert "account_mismatch:available_cash" in reconciliation["differences"]
    assert transport.remote_cash == Decimal("99799")
    assert sum(method == "POST" for method, _, _ in transport.calls) == 2


def test_adv_uses_twenty_completed_sessions_even_if_requested_end_is_future(tmp_path: Path) -> None:
    """未来请求结束日不稀释ADV；20日各400股均量400，1%流动性允许四股委托。"""
    root, plan_id, transport, clock = prepared(tmp_path, future_history_end=True, daily_volume=400)
    clock.at = EXECUTION
    result = execute(root, plan_id, transport, clock)
    assert result["submitted"] == 2
    assert [(row["symbol"], row["qty"]) for row in transport.orders] == [("BBB", "4"), ("AAA", "4")]


def test_last_reconciliation_failure_changes_execution_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最后报告前才出现差异，也不能以正常执行状态结束；不修改现金来伪造匹配。"""
    root, plan_id, transport, clock = prepared(tmp_path)
    clock.at = EXECUTION
    recover = ExecutionService.recover
    successful_public_checks = 0

    def late_cash_change(service: ExecutionService) -> ReconciliationResult:
        """初始及下单后核对成功后注入外部现金变化，再让真实恢复代码发现差异。"""
        nonlocal successful_public_checks
        # 这是时序故障注入：两次公开核对已经成功，第三次公开核对前才出现新事实。
        # 不替换对账返回值，实际Broker和数据库仍由原恢复代码读取并比较。
        if successful_public_checks == 2:
            transport.remote_cash -= Decimal("1")
        result = recover(service)
        if result.matched:
            successful_public_checks += 1
        return result

    monkeypatch.setattr(ExecutionService, "recover", late_cash_change)
    result = execute(root, plan_id, transport, clock)
    assert result["reconciled"] is False
    assert result["status"] == "blocked"
    account = read_json(Path(result["output"]) / "account.json")
    assert Decimal(account["cash"]) == Decimal("9800")
    assert sum(method == "POST" for method, _, _ in transport.calls) == 2
