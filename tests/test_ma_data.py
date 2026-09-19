"""独立构造二十日日线与公司行动，保护 MA 价格口径、时点和逐证券排除。"""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from quant_core.adapters.alpaca_data import (
    AlpacaCalendar,
    build_ma_snapshot,
    fetch_corporate_actions,
    fetch_daily_bars,
)
from quant_core.contracts import ContractError


class RecordedTransport:
    """按固定顺序返回脱敏响应并保存请求，不访问真实网络。"""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        """每个测试独占响应列表，避免共享请求状态。"""
        self.pages = deepcopy(pages)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def market_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """保存实际请求口径供断言，再返回手写下一页。"""
        self.calls.append((path, dict(params or {})))
        return self.pages.pop(0)


def ma_inputs() -> dict[str, Any]:
    """给定二十个日历交易日，原始价二十、拆股价十，原始成交量一百万。

    额外的下一交易日只建立日历覆盖，不提供未来价格；采集时间在最后一日收盘后。
    全部身份与来源仅为脱敏测试证据，不冒充当前真实证券。
    """
    days = [date(2024, 1, 2) + timedelta(days=i) for i in range(28)]
    sessions = [day for day in days if day.weekday() < 5][:20]
    observed = datetime.combine(sessions[-1], datetime.min.time(), UTC).replace(hour=22)
    calendar = AlpacaCalendar(
        [{"date": str(day), "open": "09:30", "close": "16:00"} for day in sessions],
        sessions[0],
        sessions[-1] + timedelta(days=7),
    )
    bars = {"AAA": [{"t": f"{day}T05:00:00Z", "o": 19, "c": 20, "v": 1000000} for day in sessions]}
    split_bars = deepcopy(bars)
    for row in split_bars["AAA"]:
        row.update({"c": 10, "v": 2000000})
    return {
        "bars": bars,
        "split_bars": split_bars,
        "assets": [
            {
                "id": "asset-a",
                "symbol": "AAA",
                "class": "us_equity",
                "status": "active",
                "tradable": True,
            }
        ],
        "identity_evidence": {
            "records": [
                {
                    "symbol": "AAA",
                    "alpaca_asset_id": "asset-a",
                    "asset_type_evidence": "common_stock",
                    "source": "https://example.test/stock/aaa",
                    "observed_at": observed.isoformat(),
                }
            ]
        },
        "corporate_actions": {
            "symbols": ["AAA"],
            "corporate_actions": {},
            "start": str(sessions[0]),
            "end": str(sessions[-1]),
            "pagination_complete": True,
            "observed_at": observed.isoformat(),
        },
        "calendar": calendar,
        "observed_at": observed,
        "decision_time": observed,
        "candidates": ["AAA"],
    }


def test_ma_snapshot_distinguishes_raw_split_and_unknown_research() -> None:
    """拆股收盘价十用于 MA；原始价二十和一百万实际成交股数仍保持独立。"""
    values = ma_inputs()
    snapshot, excluded = build_ma_snapshot(**values)
    assert excluded == {}
    assert len(snapshot.records) == 20
    assert {record.raw_close for record in snapshot.records} == {20}
    assert {record.split_adjusted_close for record in snapshot.records} == {10}
    assert {record.volume for record in snapshot.records} == {1000000}
    assert all(record.total_return_close is None for record in snapshot.records)
    assert all(record.first_seen_at == values["observed_at"] for record in snapshot.records)
    assert snapshot.securities[0].sector is None
    assert snapshot.securities[0].effective_from == date(2024, 1, 29)


def test_explicit_split_and_asof_are_passed_without_volume_substitution() -> None:
    """拆股请求与证券映射日期显式传入 API，不误称请求日期是历史可用时间。"""
    transport = RecordedTransport([{"bars": {"AAA": [{"c": 1}]}}])
    fetch_daily_bars(
        transport,
        ["AAA"],
        date(2024, 1, 2),
        date(2024, 1, 29),
        adjustment="split",
        asof=date(2024, 1, 29),
    )
    assert transport.calls[0][1]["adjustment"] == "split"
    assert transport.calls[0][1]["asof"] == "2024-01-29"
    assert transport.calls[0][1]["feed"] == "sip"
    with pytest.raises(ContractError, match="raw 或 split"):
        fetch_daily_bars(transport, ["AAA"], date(2024, 1, 2), date(2024, 1, 29), adjustment="all")


@pytest.mark.parametrize("dataset", ["bars", "split_bars"])
def test_missing_or_duplicate_day_excludes_without_shortening_window(dataset: str) -> None:
    """任一口径缺日或重复日均退出候选，不拿旧日补齐。"""
    values = ma_inputs()
    values[dataset]["AAA"].pop(3)
    snapshot, exclusions = build_ma_snapshot(**values)
    assert snapshot.records == []
    assert "连续 20" in exclusions["AAA"]
    values = ma_inputs()
    values[dataset]["AAA"].append(deepcopy(values[dataset]["AAA"][0]))
    assert "重复" in build_ma_snapshot(**values)[1]["AAA"]


@pytest.mark.parametrize("value", [True, -1, 1.5, "100"])
def test_bad_raw_volume_is_never_coerced(value: Any) -> None:
    """非整数、负量和布尔量不得转成实际成交股数。"""
    values = ma_inputs()
    values["bars"]["AAA"][-1]["v"] = value
    snapshot, exclusions = build_ma_snapshot(**values)
    assert not snapshot.records
    assert "整数股" in exclusions["AAA"]


@pytest.mark.parametrize("value", [False, 0, -1, float("nan"), float("inf"), "10"])
def test_bad_adjusted_price_is_rejected(value: Any) -> None:
    """拆股价格缺乏有限正数语义时不计算 MA，也不回退原始价格。"""
    values = ma_inputs()
    values["split_bars"]["AAA"][-1]["c"] = value
    assert "有限正数" in build_ma_snapshot(**values)[1]["AAA"]


def test_future_and_unfinished_bar_values_do_not_change_snapshot() -> None:
    """未来日线即使出现也不进入当前内容指纹，盘中当日数据不能成为完整日线。"""
    values = ma_inputs()
    expected = build_ma_snapshot(**values)[0].content_hash
    for dataset in ("bars", "split_bars"):
        values[dataset]["AAA"].append({"t": "2024-01-30T05:00:00Z", "o": 999, "c": 999, "v": 1})
    assert build_ma_snapshot(**values)[0].content_hash == expected
    values = ma_inputs()
    values["observed_at"] = values["decision_time"] = values["observed_at"].replace(hour=18)
    with pytest.raises(ContractError, match="不足 20"):
        build_ma_snapshot(**values)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("alpaca_asset_id", None, "稳定身份"),
        ("alpaca_asset_id", "wrong", "稳定身份"),
        ("asset_type_evidence", "etf", "稳定身份"),
        ("observed_at", "2024-01-30T22:00:00Z", "晚于"),
        ("available_at", "2024-01-30T22:00:00Z", "晚于"),
        ("source", "https://example.test/stock?secret=x", "凭据"),
    ],
)
def test_unbound_or_unavailable_identity_excludes(field: str, value: Any, message: str) -> None:
    """代码匹配不代替稳定身份绑定，晚到资料和带秘密参数来源也不能入选。"""
    values = ma_inputs()
    values["identity_evidence"]["records"][0][field] = value
    assert message in build_ma_snapshot(**values)[1]["AAA"]


def test_cash_dividends_and_valid_splits_allow_price_strategy() -> None:
    """现金股息不使价格型 MA 冒充总回报；合法拆股仍使用供应商拆股价。"""
    values = ma_inputs()
    values["corporate_actions"]["corporate_actions"] = {
        "cash_dividends": [{"symbol": "AAA", "ex_date": "2024-01-10", "rate": 2}],
        "forward_splits": [
            {"symbol": "AAA", "ex_date": "2024-01-15", "new_rate": 2, "old_rate": 1}
        ],
    }
    snapshot, exclusions = build_ma_snapshot(**values)
    assert exclusions == {}
    assert all(record.total_return_close is None for record in snapshot.records)


@pytest.mark.parametrize(
    "kind,row",
    [
        ("name_changes", {"old_symbol": "AAA", "new_symbol": "AAA", "process_date": "2024-01-10"}),
        (
            "stock_mergers",
            {"acquiree_symbol": "BBB", "acquirer_symbol": "AAA", "effective_date": "2024-01-10"},
        ),
        ("spinoffs", {"symbol": "AAA", "ex_date": "2024-01-10"}),
        ("unknown", {"symbol": "AAA", "process_date": "2024-01-10"}),
        (
            "forward_splits",
            {"symbol": "AAA", "ex_date": "2024-01-15", "new_rate": 0, "old_rate": 1},
        ),
        ("cash_dividends", {"symbol": "AAA"}),
    ],
)
def test_ambiguous_actions_exclude_affected_candidates(kind: str, row: dict[str, Any]) -> None:
    """复杂行动、未知类别、非法拆股比例及未知日期均明确排除，不猜身份衔接。"""
    values = ma_inputs()
    values["corporate_actions"]["corporate_actions"] = {kind: [row]}
    snapshot, exclusions = build_ma_snapshot(**values)
    assert not snapshot.records
    assert "AAA" in exclusions


def test_old_identity_change_outside_short_window_does_not_poison_current_history() -> None:
    """窗口之前已经完成的同代码身份变更不冒充窗口内变化。"""
    values = ma_inputs()
    values["corporate_actions"]["corporate_actions"] = {
        "name_changes": [{"old_symbol": "AAA", "new_symbol": "AAA", "process_date": "2023-12-01"}]
    }
    assert build_ma_snapshot(**values)[1] == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("pagination_complete", False),
        ("start", "2024-01-03"),
        ("end", "2024-01-28"),
        ("observed_at", "2024-01-30T22:00:00Z"),
    ],
)
def test_incomplete_action_envelope_blocks_entire_snapshot(field: str, value: Any) -> None:
    """无法证明完整覆盖的行动响应不能解释成没有公司行动。"""
    values = ma_inputs()
    values["corporate_actions"][field] = value
    with pytest.raises(ContractError):
        build_ma_snapshot(**values)


def test_actions_collect_all_pages_and_reject_token_loop() -> None:
    """完整收集不同类别分页；游标循环不得生成完成声明。"""
    transport = RecordedTransport(
        [
            {"corporate_actions": {"cash_dividends": [{"symbol": "AAA"}]}, "next_page_token": "x"},
            {"corporate_actions": {"name_changes": [{"old_symbol": "AAA"}]}},
        ]
    )
    result = fetch_corporate_actions(transport, ["AAA"], date(2024, 1, 2), date(2024, 1, 29))
    assert result["pagination_complete"] is True
    assert set(result["corporate_actions"]) == {"cash_dividends", "name_changes"}
    assert all(path == "/v1/corporate-actions" for path, _ in transport.calls)
    assert transport.calls[1][1]["page_token"] == "x"
    assert transport.calls[0][1]["data_quality"] == "all"
    transport = RecordedTransport(
        [
            {"corporate_actions": {}, "next_page_token": "x"},
            {"corporate_actions": {}, "next_page_token": "x"},
        ]
    )
    with pytest.raises(ContractError, match="循环"):
        fetch_corporate_actions(transport, ["AAA"], date(2024, 1, 2), date(2024, 1, 29))


def test_other_candidate_action_evidence_is_not_reused_as_empty_coverage() -> None:
    """另一组证券的空行动结果不能证明当前候选没有身份变化。"""
    values = ma_inputs()
    values["corporate_actions"]["symbols"] = ["OTHER"]
    with pytest.raises(ContractError, match="候选证券范围"):
        build_ma_snapshot(**values)
