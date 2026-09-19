"""均线 Paper 装配的独立响应与手算验收；不读取凭据、不访问真实账户。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from test_paper_application import ScenarioClock, ScenarioTransport

from quant_core.adapters.storage import read_json, write_json
from quant_core.application import paper_execute, paper_plan, paper_read
from quant_core.cli import main
from quant_core.contracts import ContractError, PaperConfig

READ_AT = datetime(2026, 9, 17, 14, tzinfo=UTC)


class MATransport(ScenarioTransport):
    """四证券二十日样本，原始价100而拆股均线递增，独立维持远端成交事实。"""

    def __init__(self, clock: ScenarioClock) -> None:
        """构造周三收盘行情，在周四盘中采集，以验证均线不受周频门槛限制。"""
        super().__init__(clock)
        self.days = []
        day = date(2026, 9, 16)
        while len(self.days) < 20:
            if day.weekday() < 5:
                self.days.insert(0, day)
            day -= timedelta(days=1)
        following = [date(2026, 9, 17) + timedelta(days=index) for index in range(15)]
        self.calendar = [
            {"date": str(day), "open": "09:30", "close": "16:00"}
            for day in [*self.days, *(day for day in following if day.weekday() < 5)]
        ]
        self.assets = [
            {
                "id": f"asset-{symbol}",
                "symbol": symbol,
                "class": "us_equity",
                "status": "active",
                "tradable": True,
            }
            for symbol in ("AAA", "BBB", "CCC", "DDD")
        ]
        self.bars = {
            symbol: [
                {"t": f"{day}T04:00:00Z", "o": 100, "c": 100, "v": 1000000} for day in self.days
            ]
            for symbol in ("AAA", "BBB", "CCC", "DDD")
        }
        self.split_bars = {
            symbol: [
                {"t": f"{day}T04:00:00Z", "o": 100, "c": final if index >= 15 else 100, "v": 777777}
                for index, day in enumerate(self.days)
            ]
            for symbol, final in (("AAA", 110), ("BBB", 120), ("CCC", 130), ("DDD", 140))
        }
        self.submit_fill = 4
        self.defer_cancel = False

    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """可将撤单故意停在等待态，独立模拟撤单回执与最终取消并非同一事实。"""
        if self.defer_cancel and method == "DELETE" and path.startswith("/orders/"):
            self.calls.append((method, path, dict(data or {})))
            row = next(row for row in self.orders if row["id"] == path.split("/")[-1])
            row["status"] = "pending_cancel"
            return None
        return super().request(method, path, data)

    def market_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """按请求口径返回两套日线和空公司行动；执行报价独立固定为原始100美元。"""
        query = dict(params or {})
        self.calls.append(("GET", path, query))
        if path == "/v2/stocks/bars":
            return {
                "bars": self.split_bars if query.get("adjustment") == "split" else self.bars,
                "next_page_token": None,
            }
        if path == "/v1/corporate-actions":
            return {"corporate_actions": {}, "next_page_token": None}
        if path == "/v2/stocks/quotes/latest":
            return {
                "quotes": {
                    row["symbol"]: {"t": self.clock.now().isoformat(), "ap": 100}
                    for row in self.assets
                }
            }
        raise AssertionError("非预期均线行情请求")


def read_ma(tmp_path: Path) -> tuple[Path, Path, MATransport, ScenarioClock]:
    """生成只读证据和独立普通股身份文件；未知行业及总回报均不补造。"""
    clock = ScenarioClock()
    clock.at = READ_AT
    transport = MATransport(clock)
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
                    "source": f"https://example.test/securities/{row['symbol']}",
                    "observed_at": READ_AT.isoformat(),
                }
                for row in transport.assets
            ]
        },
    )
    return source, identity, transport, clock


def execute_ma(
    root: Path, plan: str, transport: MATransport, clock: ScenarioClock, **options: Any
) -> dict[str, Any]:
    """在三笔、每笔500美元范围内调用唯一执行装配入口，等待参数仅由测试显式注入。"""
    return paper_execute(
        root,
        cast(Any, transport),
        clock,
        approved_plan=plan,
        max_orders=3,
        max_order_notional=Decimal("500"),
        unfilled="cancel",
        **options,
    )


def test_ma_read_keeps_raw_and_split_requests_separate(tmp_path: Path) -> None:
    """独立检查SIP两次请求及留存，原始成交量不能被调整序列成交量替代。"""
    source, _, transport, _ = read_ma(tmp_path)
    calls = [query for _, path, query in transport.calls if path == "/v2/stocks/bars"]
    assert [query["adjustment"] for query in calls] == ["raw", "split"]
    assert all(query["feed"] == "sip" and query["asof"] == "2026-09-17" for query in calls)
    assert read_json(source / "bars.json")["DDD"][-1]["v"] == 1000000
    assert read_json(source / "split-bars.json")["DDD"][-1]["c"] == 140
    assert read_json(source / "corporate-actions.json")["pagination_complete"] is True
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_ma_daily_plan_handcomputed_scores_and_three_targets(tmp_path: Path) -> None:
    """周四盘中形成计划，四个均线信号只取三只，每只450美元基础目标向下取四股。"""
    source, identity, transport, _ = read_ma(tmp_path)
    target = tmp_path / "plan"
    result = paper_plan(source, None, target, identity_path=identity)
    assert result["scores"] == 4 and result["positions"] == 3
    assert result["eligible_at"] == READ_AT.isoformat()
    plan = read_json(target / "paper-plan.json")
    assert plan["expires_at"] == "2026-09-17T20:00:00+00:00"
    factors = {
        (row["security_id"], row["factor_id"]): row["value"]
        for row in read_json(target / "factors.json")
    }
    # DDD: (15×100+5×140)/20=110；140/110−1=3/11。
    assert factors[("asset-DDD", "ma5")] == 140
    assert factors[("asset-DDD", "ma20")] == 110
    assert factors[("asset-DDD", "ma_trend")] == pytest.approx(3 / 11)
    scores = read_json(target / "signals.json")["scores"]
    assert [row["security_id"] for row in scores] == [
        "asset-DDD",
        "asset-CCC",
        "asset-BBB",
        "asset-AAA",
    ]
    assert [row["value"] for row in scores] == pytest.approx([1, 2 / 3, 1 / 3, 0])
    assert all(set(row["components"]) == {"ma_trend"} and row["sector"] is None for row in scores)
    positions = read_json(target / "target.json")["positions"]
    assert [(row["security_id"], row["quantity"]) for row in positions] == [
        ("asset-DDD", 4),
        ("asset-CCC", 4),
        ("asset-BBB", 4),
    ]
    snapshot = read_json(target / "snapshot.json")
    assert len(snapshot["records"]) == 80
    assert all(
        row["total_return_close"] is None and row["volume"] == 1000000
        for row in snapshot["records"]
    )
    assert not transport.orders


def test_ma_cli_no_supplement_but_identity_required(tmp_path: Path) -> None:
    """MA的CLI可省略旧总回报补充文件，但不能省略普通股身份依据。"""
    source, identity, _, _ = read_ma(tmp_path)
    assert (
        main(
            [
                "paper-plan",
                "--source",
                str(source),
                "--identity-evidence",
                str(identity),
                "--output",
                str(tmp_path / "cli-plan"),
            ]
        )
        == 0
    )
    with pytest.raises(ContractError, match="identity-evidence"):
        paper_plan(source, None, tmp_path / "no-identity")


def test_original_plan_still_requires_supplement(tmp_path: Path) -> None:
    """把配置明确选回原双因子时，不能因为已有拆股数据而跳过总回报要求。"""
    source, identity, _, _ = read_ma(tmp_path)
    config = read_json(source / "config.json")
    config["strategy"] = "weekly-two-factor"
    (source / "config.json").unlink()
    write_json(source / "config.json", config)
    with pytest.raises(ContractError, match="总回报"):
        paper_plan(source, None, tmp_path / "original", identity_path=identity)


def test_ma_fills_cash_and_restart_reconcile_without_duplicate_orders(tmp_path: Path) -> None:
    """三只各四股真实响应型成交，共1200美元；预算现金8800而远端98800，恢复不重复。"""
    source, identity, transport, clock = read_ma(tmp_path)
    root = tmp_path / "plan"
    plan = paper_plan(source, None, root, identity_path=identity)
    result = execute_ma(root, plan["plan_id"], transport, clock)
    assert result["submitted"] == 3 and result["filled"] == 3 and result["pending"] == 0
    assert result["reconciled"] is True
    account = read_json(Path(result["output"]) / "account.json")
    assert account["positions"] == {"asset-DDD": 4, "asset-CCC": 4, "asset-BBB": 4}
    assert Decimal(account["cash"]) == Decimal("8800")
    assert transport.remote_cash == Decimal("98800")
    assert result["reserve"] == "90000"
    recovered = execute_ma(root, plan["plan_id"], transport, clock, recover_only=True)
    assert (
        recovered["filled"] == 3 and recovered["pending"] == 0 and recovered["reconciled"] is True
    )
    assert sum(method == "POST" for method, _, _ in transport.calls) == 3
    # 每单一个意图、一份订单记录和一个独立FILL事件；恢复没有再次写入任何操作。
    journal = read_json(Path(recovered["output"]) / "journal.json")
    assert len(journal) == 9
    assert sum(row["operation"] == "event" for row in journal) == 3
    assert journal == read_json(Path(result["output"]) / "journal.json")
    assert result["filled_and_reconciled"] is True and result["restart_verified"] is False
    assert recovered["restart_verified"] is True


class ObservationTimer:
    """只推进脱敏业务时钟及单调计时器，测试观察循环而不真实睡眠。"""

    def __init__(
        self, clock: ScenarioClock, transport: MATransport, finish_at: float | None = None
    ) -> None:
        """可在指定已耗秒数完成剩余成交，用于观察提前结束和撤单竞态。"""
        self.clock = clock
        self.transport = transport
        self.finish_at = finish_at
        self.elapsed = 0.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        """返回独立经过秒数，不使用墙上时钟决定观察截止。"""
        return self.elapsed

    def wait(self, seconds: float) -> None:
        """记录每次读取间隔，并在指定时刻独立产生剩余真实响应型成交。"""
        self.elapsed += seconds
        self.waits.append(seconds)
        self.clock.at += timedelta(seconds=seconds)
        if self.finish_at is not None and self.elapsed >= self.finish_at:
            self.transport.finish_orders()
            self.finish_at = None


def test_ma_observation_finishes_early_without_cancel_or_repeat(tmp_path: Path) -> None:
    """两次五秒轮询后全部成交，提前结束且不撤已成单；恢复不再等待或下单。"""
    source, identity, transport, clock = read_ma(tmp_path)
    root = tmp_path / "plan"
    plan = paper_plan(source, None, root, identity_path=identity)
    transport.submit_fill = 1
    timer = ObservationTimer(clock, transport, finish_at=10)
    result = execute_ma(
        root,
        plan["plan_id"],
        transport,
        clock,
        observe_seconds=300,
        wait=timer.wait,
        monotonic=timer.monotonic,
    )
    assert timer.waits == [5, 5]
    assert result["filled_and_reconciled"] is True and result["pending"] == 0
    assert sum(method == "DELETE" for method, _, _ in transport.calls) == 0
    recovered = execute_ma(
        root,
        plan["plan_id"],
        transport,
        clock,
        recover_only=True,
        observe_seconds=300,
        wait=timer.wait,
        monotonic=timer.monotonic,
    )
    assert timer.waits == [5, 5]
    assert recovered["restart_verified"] is True
    assert sum(method == "POST" for method, _, _ in transport.calls) == 3
    assert transport.remote_cash == Decimal("98800")


def test_ma_partial_fills_cancel_after_five_minutes_without_refill(tmp_path: Path) -> None:
    """观察五分钟后只撤三张本轮余量，已有一股成交仍入账，不能宣称全成。"""
    source, identity, transport, clock = read_ma(tmp_path)
    root = tmp_path / "plan"
    plan = paper_plan(source, None, root, identity_path=identity)
    transport.submit_fill = 1
    timer = ObservationTimer(clock, transport)
    result = execute_ma(
        root,
        plan["plan_id"],
        transport,
        clock,
        observe_seconds=300,
        wait=timer.wait,
        monotonic=timer.monotonic,
    )
    assert timer.waits == [5] * 60
    assert result["filled"] == 0 and result["pending"] == 0 and result["reconciled"] is True
    assert result["filled_and_reconciled"] is False
    assert sum(method == "DELETE" for method, _, _ in transport.calls) == 3
    assert all(row["status"] == "canceled" and row["filled_qty"] == "1" for row in transport.orders)
    account = read_json(Path(result["output"]) / "account.json")
    assert account["positions"] == {"asset-DDD": 1, "asset-CCC": 1, "asset-BBB": 1}
    assert Decimal(account["cash"]) == Decimal("9700")
    assert transport.remote_cash == Decimal("99700")
    recovered = execute_ma(
        root,
        plan["plan_id"],
        transport,
        clock,
        recover_only=True,
        observe_seconds=300,
        wait=timer.wait,
        monotonic=timer.monotonic,
    )
    assert recovered["restart_verified"] is False
    assert timer.waits == [5] * 60
    assert sum(method == "POST" for method, _, _ in transport.calls) == 3


@pytest.mark.parametrize("finish_at", [None, 305.0])
def test_ma_cancel_pending_stops_at_deadline_or_records_racing_fills(
    tmp_path: Path, finish_at: float | None
) -> None:
    """撤单不确认最多再等六十秒；其间成交如实入账，重启不重复撤单或发单。"""
    source, identity, transport, clock = read_ma(tmp_path)
    root = tmp_path / "plan"
    plan = paper_plan(source, None, root, identity_path=identity)
    transport.submit_fill = 1
    transport.defer_cancel = True
    timer = ObservationTimer(clock, transport, finish_at=finish_at)
    result = execute_ma(
        root,
        plan["plan_id"],
        transport,
        clock,
        observe_seconds=300,
        wait=timer.wait,
        monotonic=timer.monotonic,
    )
    assert timer.elapsed == (360 if finish_at is None else 305)
    assert result["pending"] == (3 if finish_at is None else 0)
    assert result["filled"] == (0 if finish_at is None else 3)
    assert result["reconciled"] is True
    assert result["filled_and_reconciled"] is (finish_at is not None)
    assert sum(method == "DELETE" for method, _, _ in transport.calls) == 3
    execute_ma(
        root,
        plan["plan_id"],
        transport,
        clock,
        recover_only=True,
        observe_seconds=300,
        wait=timer.wait,
        monotonic=timer.monotonic,
    )
    assert timer.elapsed == (360 if finish_at is None else 305)
    assert sum(method == "POST" for method, _, _ in transport.calls) == 3
    assert sum(method == "DELETE" for method, _, _ in transport.calls) == 3


@pytest.mark.parametrize(
    "override",
    [
        {"max_orders": 4},
        {"max_order_notional": Decimal("500.01")},
        {"observe_seconds": 301},
        {"cancel_observe_seconds": 61},
    ],
)
def test_ma_execution_rejects_expanded_order_or_wait_bounds(
    tmp_path: Path, override: dict[str, Any]
) -> None:
    """用户本轮三笔、单笔500及观察300加60秒是硬边界，不可通过参数扩大。"""
    source, identity, transport, clock = read_ma(tmp_path)
    root = tmp_path / "plan"
    plan = paper_plan(source, None, root, identity_path=identity)
    options = {
        "approved_plan": plan["plan_id"],
        "max_orders": 3,
        "max_order_notional": Decimal("500"),
        "unfilled": "cancel",
        **override,
    }
    with pytest.raises(ContractError):
        paper_execute(root, cast(Any, transport), clock, **options)
    assert not transport.orders
    assert all(method == "GET" for method, _, _ in transport.calls)
