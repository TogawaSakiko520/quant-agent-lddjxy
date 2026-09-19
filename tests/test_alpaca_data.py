"""独立构造日历、原始 SIP 行情和外部研究价，验证接入闸门且不联网。"""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from quant_core.adapters.alpaca_data import (
    AlpacaCalendar,
    build_paper_snapshot,
    fetch_daily_bars,
)
from quant_core.contracts import ContractError
from quant_core.factors import calculate_factors


class PageTransport:
    """顺序交付手写分页响应，保存请求以核对数据口径，不包含账户或密钥。"""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        """保存当前测试独占的响应队列和请求记录。"""
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def market_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """返回下一固定响应；测试可以断言无额外请求及所有分页的 feed。"""
        assert path == "/v2/stocks/bars"
        self.calls.append(dict(params or {}))
        return self.pages.pop(0)


def inputs() -> dict[str, Any]:
    """构造 253 个明示交易日及下一日历日的采集，不声称是真实交易所数据。

    原始价恒为 10；总回报首项为 50，其余 252 项为 100。独立期望是动量 1、
    最近 60 个收益全零；这能识别意外使用原始价格替代研究价格。
    """
    days: list[date] = []
    day = date(2024, 1, 2)
    while len(days) < 253:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    observed = datetime.combine(days[-1] + timedelta(days=1), datetime.min.time(), UTC)
    # 22点 UTC 保证纽约仍是同一天且已收盘，避免日界线使主表提前生效。
    observed = observed.replace(hour=22)
    calendar = AlpacaCalendar(
        [{"date": str(day), "open": "09:30", "close": "16:00"} for day in days],
        days[0],
        observed.date() + timedelta(days=7),
    )
    bars = {"AAA": [{"t": f"{day}T05:00:00Z", "o": 10.0, "c": 10.0, "v": 1000000} for day in days]}
    supplement = {
        "schema_version": "1.0.0",
        "quality": "good",
        "price_basis": "dividend_reinvestment_total_return",
        "currency": "USD",
        "source": "https://example.test/research-dataset",
        "methodology_source": "https://example.test/methodology",
        "methodology": "固定测试样本，股息再投资；不属于真实资料验收",
        "observed_at": observed.isoformat(),
        "available_at": observed.isoformat(),
        "securities": [
            {"asset_id": "asset-a", "symbol": "AAA", "sector": "tech", "asset_type": "common_stock"}
        ],
        "prices": [
            {
                "asset_id": "asset-a",
                "session": str(day),
                "total_return_close": 50 if i == 0 else 100,
            }
            for i, day in enumerate(days)
        ],
    }
    return {
        "bars": bars,
        "assets": [
            {
                "id": "asset-a",
                "symbol": "AAA",
                "class": "us_equity",
                "status": "active",
                "tradable": True,
            }
        ],
        "supplement": supplement,
        "calendar": calendar,
        "observed_at": observed,
        "decision_time": observed + timedelta(seconds=1),
        "candidates": ["AAA"],
    }


def test_real_format_enters_original_factors_without_backdating() -> None:
    """来源时间、身份和两种价格分别保留，原因子输出符合手算值。"""
    values = inputs()
    snapshot = build_paper_snapshot(**values)
    assert snapshot.records[0].raw_close == 10
    assert snapshot.records[0].total_return_close == 50
    assert all(record.published_at is None for record in snapshot.records)
    assert all(record.first_seen_at == values["observed_at"] for record in snapshot.records)
    assert all(record.available_at == values["observed_at"] for record in snapshot.records)
    assert snapshot.securities[0].effective_from == values["observed_at"].date()
    assert snapshot.securities[0].security_id == "asset-a"
    factors = calculate_factors(snapshot, values["calendar"])
    assert [(factor.factor_id, factor.value) for factor in factors] == [
        ("momentum", 1.0),
        ("low_volatility", 0.0),
    ]


def test_pages_cover_later_symbols_with_explicit_raw_sip() -> None:
    """第一页仅第一证券时仍继续翻页；显式原始 SIP 口径避免默认权限降级。"""
    transport = PageTransport(
        [
            {"bars": {"AAA": [{"c": 10}]}, "next_page_token": "next"},
            {"bars": {"BBB": [{"c": 20}]}, "next_page_token": None},
        ]
    )
    result = fetch_daily_bars(transport, ["AAA", "BBB"], date(2024, 3, 8), date(2024, 3, 11))
    assert result == {"AAA": [{"c": 10}], "BBB": [{"c": 20}]}
    assert all(call["adjustment"] == "raw" and call["feed"] == "sip" for call in transport.calls)
    assert transport.calls[1]["page_token"] == "next"
    assert transport.calls[0]["start"] == "2024-03-08T05:00:00+00:00"
    assert transport.calls[0]["end"] == "2024-03-12T03:59:59.999999+00:00"


@pytest.mark.parametrize(
    "pages, message",
    [
        (
            [{"bars": {"AAA": [{}]}, "next_page_token": "x"}, {"bars": {}, "next_page_token": "x"}],
            "循环",
        ),
        ([{"bars": {"AAA": [{}]}}], "缺少候选"),
        ([{"bars": {"OTHER": [{}]}}], "范围外"),
    ],
)
def test_incomplete_or_looping_pages_fail(pages: list[dict[str, Any]], message: str) -> None:
    """重复分页令牌、完整响应丢证券和越界证券均阻断，不能使用局部成功数据。"""
    with pytest.raises(ContractError, match=message):
        fetch_daily_bars(PageTransport(pages), ["AAA", "BBB"], date(2024, 1, 2), date(2024, 1, 3))


def test_iex_is_not_consolidated_volume() -> None:
    """IEX 限定单交易所，不能通过同一字段名获得全市场 ADV 资格。"""
    with pytest.raises(ContractError, match="SIP"):
        fetch_daily_bars(PageTransport([]), ["AAA"], date(2024, 1, 2), date(2024, 1, 3), "iex")
    values = inputs()
    with pytest.raises(ContractError, match="SIP"):
        build_paper_snapshot(**values, feed="iex")


@pytest.mark.parametrize(
    "field, replacement, message",
    [
        ("quality", "quarantined", "质量"),
        ("price_basis", "alpaca_adjustment_all", "再投资"),
        ("currency", "EUR", "USD"),
        ("methodology", "", "方法"),
        ("source", "https://secret@example.test/data", "凭据"),
        ("source", "https://example.test/data?api_key=redacted", "凭据"),
        ("methodology_source", "https://example.test/data#secret", "凭据"),
    ],
)
def test_supplement_requires_specific_evidence(field: str, replacement: str, message: str) -> None:
    """未知研究口径、隔离质量和可能泄密的溯源地址不得进入快照。"""
    values = inputs()
    values["supplement"][field] = replacement
    with pytest.raises(ContractError, match=message):
        build_paper_snapshot(**values)


def test_missing_supplement_and_future_availability_block() -> None:
    """缺少真实总回报/分类证据与决策之后才可用的资料分别形成数据阻塞。"""
    values = inputs()
    original = deepcopy(values["supplement"])
    values["supplement"] = None
    with pytest.raises(ContractError, match="缺少补充资料"):
        build_paper_snapshot(**values)
    values["supplement"] = original
    values["supplement"]["available_at"] = (
        values["decision_time"] + timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ContractError, match="顺序"):
        build_paper_snapshot(**values)


@pytest.mark.parametrize("volume", [1.5, "100", True, -1])
def test_fractional_or_unknown_raw_volume_is_not_truncated(volume: Any) -> None:
    """原始成交量非整数、布尔或负值不能强转成可用的整股流动性。"""
    values = inputs()
    values["bars"]["AAA"][-1]["v"] = volume
    with pytest.raises(ContractError, match="整数股"):
        build_paper_snapshot(**values)


def test_missing_session_and_identity_do_not_silently_shrink_candidates() -> None:
    """缺日不会拿更旧价格补齐；分类的资产 ID 不匹配也不会猜代码绑定。"""
    values = inputs()
    values["bars"]["AAA"].pop(10)
    with pytest.raises(ContractError, match="253"):
        build_paper_snapshot(**values)
    values = inputs()
    values["supplement"]["securities"][0]["asset_id"] = "wrong-asset"
    with pytest.raises(ContractError, match="稳定身份"):
        build_paper_snapshot(**values)


def test_calendar_dst_half_day_and_bounds() -> None:
    """明确固定日期验证纽约夏令时、半日收盘及收盘边界，没有依赖被测计算作期望。"""
    calendar = AlpacaCalendar(
        [
            {"date": "2024-03-08", "open": "09:30", "close": "16:00"},
            {"date": "2024-03-11", "open": "09:30", "close": "16:00"},
            {"date": "2024-11-29", "open": "09:30", "close": "13:00"},
        ],
        date(2024, 3, 8),
        date(2024, 12, 1),
    )
    assert calendar.open_at(date(2024, 3, 8)) == datetime(2024, 3, 8, 14, 30, tzinfo=UTC)
    assert calendar.open_at(date(2024, 3, 11)) == datetime(2024, 3, 11, 13, 30, tzinfo=UTC)
    assert calendar.close_at(date(2024, 11, 29)) == datetime(2024, 11, 29, 18, tzinfo=UTC)
    assert calendar.next_session(date(2024, 3, 8)) == date(2024, 3, 11)
    assert calendar.is_open(datetime(2024, 11, 29, 17, 59, tzinfo=UTC))
    assert not calendar.is_open(datetime(2024, 11, 29, 18, tzinfo=UTC))
    with pytest.raises(ContractError, match="范围"):
        calendar.is_open(datetime(2025, 1, 1, tzinfo=UTC))


def test_current_asset_does_not_become_historical_membership() -> None:
    """现在抓到的证券分类不能用于昨天决策，也不跨日沿用当前资格。"""
    values = inputs()
    values["decision_time"] -= timedelta(days=1)
    with pytest.raises(ContractError, match="回填"):
        build_paper_snapshot(**values)
    values = inputs()
    values["decision_time"] += timedelta(days=1)
    with pytest.raises(ContractError, match="采集当日"):
        build_paper_snapshot(**values)


def test_supplement_future_sessions_cannot_hide_in_source_digest() -> None:
    """窗口外未来价格也不能影响当前快照来源摘要，资料矛盾须明确阻断。"""
    values = inputs()
    values["supplement"]["prices"].append(
        {
            "asset_id": "asset-a",
            "session": str(values["decision_time"].date() + timedelta(days=2)),
            "total_return_close": 999,
        }
    )
    with pytest.raises(ContractError, match="未来交易日"):
        build_paper_snapshot(**values)
