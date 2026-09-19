"""时点、内容冲突和历史身份独立固定样本；不访问网络或真实账户。"""

from datetime import UTC, date, datetime, timedelta

import pytest

from quant_core.adapters.calendar import ExchangeCalendar
from quant_core.contracts import (
    ContractError,
    DataSnapshot,
    MarketDataRecord,
    SecurityRecord,
    seal_record,
)
from quant_core.data import build_snapshot


def security_record(
    security_id: str = "A", sector: str = "tech", tradable: bool = True
) -> SecurityRecord:
    """装配自2020年起已知且生效的普通股主表，默认证券 A、tech 行业、可交易。

    security_id 关联行情与仓位，ticker 在本样本故意同名；effective_to 未设置表示
    没有结束日。seal_record 为当前内容生成哈希，供快照入口检验，非真实来源认证。
    """
    at = datetime(2020, 1, 1, tzinfo=UTC)
    return seal_record(
        SecurityRecord(
            security_id=security_id,
            ticker=security_id,
            sector=sector,
            tradable=tradable,
            quality="good",
            effective_from=at.date(),
            event_time=at,
            published_at=at,
            available_at=at,
        )
    )


def market_record(security_id: str, session: date, price: float, at: datetime) -> MarketDataRecord:
    """将 price 作为指定证券在 session 的美元开盘、收盘和总回报收盘样本。

    at 同时作为事件、公开与可用时刻，成交量固定100万股；不伪造 first_seen_at。
    返回带内容哈希的 MarketDataRecord，后续测试按需要另行改变版本或时点。
    """
    # 原始价和研究价在无公司行动样本中相同。
    return seal_record(
        MarketDataRecord(
            security_id=security_id,
            session=session,
            raw_open=price,
            raw_close=price,
            total_return_close=price,
            volume=1000000,
            quality="good",
            event_time=at,
            published_at=at,
            available_at=at,
        )
    )


def history(prices: list[float]) -> tuple[DataSnapshot, ExchangeCalendar]:
    """把非空 prices 依次映射到2021年起的实际交易日，生成证券 A 的决策快照。

    返回的 DataSnapshot 含 records 行情列表、securities 主表、decision_time 和封印；
    日历用于因子检查窗口是否连续。输入长度必须落在2021至2023日历范围内，
    最后收盘一分钟后作决策。这里只构造输入，不调用因子实现计算测试期望。
    """
    calendar = ExchangeCalendar(date(2021, 1, 1), date(2023, 12, 31))
    sessions = calendar.sessions(date(2021, 1, 1), date(2023, 12, 31))[: len(prices)]
    # 每条记录在收盘时已知，便于独立计算时点。
    records = [
        market_record("A", session, price, calendar.close_at(session))
        for session, price in zip(sessions, prices, strict=True)
    ]
    # 决策固定在最后收盘一分钟之后。
    decision = calendar.close_at(sessions[-1]) + timedelta(minutes=1)
    return build_snapshot(records, [security_record()], decision, calendar.version), calendar


def test_future_revision_and_ordering_do_not_change_past() -> None:
    """未来高修订与输入重排不改变旧快照；独立断言选中原值及哈希。"""
    at = datetime(2021, 1, 4, 21, tzinfo=UTC)
    original = market_record("A", at.date(), 100.0, at)
    # model_copy 组装改动而不自动验签；再 seal_record 明确声明这是另一条自洽版本，
    # 使本例检查“当时是否可用”，而非因为哈希破损提前失败。
    # 决策之后才到达的修订价格为999。
    revised = seal_record(
        original.model_copy(
            update={"raw_close": 999.0, "revision": 2, "available_at": at + timedelta(days=1)}
        )
    )
    old = build_snapshot([original], [security_record()], at, "fixture-v1")
    # 将未来修订排在前面不能偷看。
    perturbed = build_snapshot([revised, original], [security_record()], at, "fixture-v1")
    assert perturbed.content_hash == old.content_hash
    assert perturbed.records[0].raw_close == 100.0
    # 到修订可用时才选择新值。
    later = build_snapshot(
        [original, revised], [security_record()], at + timedelta(days=1), "fixture-v1"
    )
    assert later.records[0].raw_close == 999.0


def test_hash_conflicts_quality_and_late_data_fail_closed() -> None:
    """验证同版本冲突、封印破损和最新质量失败均拒绝。"""
    at = datetime(2021, 1, 4, 21, tzinfo=UTC)
    original = market_record("A", at.date(), 100.0, at)
    # 同修订另一内容即使重新封印仍是版本冲突。
    conflict = seal_record(original.model_copy(update={"raw_close": 101.0}))
    with pytest.raises(ContractError, match="版本内容冲突"):
        build_snapshot([original, conflict], [security_record()], at, "fixture")
    # 未重新封印的内容改变属于损坏。
    with pytest.raises(ContractError, match="哈希"):
        build_snapshot(
            [original.model_copy(update={"raw_close": 200.0})], [security_record()], at, "fixture"
        )
    # 新修订质量失败不允许回退旧好值。
    bad = seal_record(original.model_copy(update={"revision": 2, "quality": "quarantined"}))
    with pytest.raises(ContractError, match="质量"):
        build_snapshot([original, bad], [security_record()], at, "fixture")


def test_security_overlap_and_historical_ticker() -> None:
    """历史ticker修订仅在可知时生效，重叠有效区间失败。"""
    original = security_record()
    at = datetime(2021, 1, 4, 21, tzinfo=UTC)
    # 未来修订不能改写当时显示的ticker。
    future = seal_record(
        original.model_copy(
            update={"ticker": "NEW", "revision": 2, "available_at": at + timedelta(days=1)}
        )
    )
    snapshot = build_snapshot([], [future, original], at, "fixture")
    assert snapshot.securities[0].ticker == "A"
    # 未关闭旧区间就新增区间属于主表冲突。
    overlap = seal_record(original.model_copy(update={"effective_from": date(2021, 1, 1)}))
    with pytest.raises(ContractError, match="区间重叠"):
        build_snapshot([], [original, overlap], at, "fixture")
