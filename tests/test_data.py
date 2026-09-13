"""时点、内容冲突和历史身份独立固定样本；不访问网络或真实账户。"""

# 所有样本时间使用显式UTC。
from datetime import UTC, date, datetime, timedelta

# 断言异常属于验收，不降低数据要求。
import pytest

# 真实交易日边界由已固定适配器提供。
from quant_core.adapters.calendar import ExchangeCalendar

# 测试只复用数据类型和封印，不复用被测时点算法产生期望。
from quant_core.contracts import (
    ContractError,
    DataSnapshot,
    MarketDataRecord,
    SecurityRecord,
    seal_record,
)

# 本文件的被测历史快照入口。
from quant_core.data import build_snapshot


def security_record(
    security_id: str = "A", sector: str = "tech", tradable: bool = True
) -> SecurityRecord:
    """建立固定2020年可知主表；返回已封印普通股对象，无外部副作用。"""
    # 固定公开时刻不依赖真实时钟。
    at = datetime(2020, 1, 1, tzinfo=UTC)
    # 稳定身份和历史行业由参数显式指定。
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
    """建立单条美元价格固定样本；返回已封印记录，无外部副作用。"""
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
    """把显式价格列表映射到真实交易日；返回快照与日历，不计算任何因子期望。"""
    # 覆盖足够长但有限的固定日历。
    calendar = ExchangeCalendar(date(2021, 1, 1), date(2023, 12, 31))
    # 每个给定价格恰好占一个合格交易日。
    sessions = calendar.sessions(date(2021, 1, 1), date(2023, 12, 31))[: len(prices)]
    # 每条记录在收盘时已知，便于独立计算时点。
    records = [
        market_record("A", session, price, calendar.close_at(session))
        for session, price in zip(sessions, prices, strict=True)
    ]
    # 决策固定在最后收盘一分钟之后。
    decision = calendar.close_at(sessions[-1]) + timedelta(minutes=1)
    # 快照生成是夹具输入装配，不生成公式测试期望。
    return build_snapshot(records, [security_record()], decision, calendar.version), calendar


def test_future_revision_and_ordering_do_not_change_past() -> None:
    """未来高修订与输入重排不改变旧快照；独立断言选中原值及哈希，无外部副作用。"""
    # 原始版本当日已经可知。
    at = datetime(2021, 1, 4, 21, tzinfo=UTC)
    # 固定原始价格为100。
    original = market_record("A", at.date(), 100.0, at)
    # 决策之后才到达的修订价格为999。
    revised = seal_record(
        original.model_copy(
            update={"raw_close": 999.0, "revision": 2, "available_at": at + timedelta(days=1)}
        )
    )
    # 原始历史快照作为稳定输入摘要。
    old = build_snapshot([original], [security_record()], at, "fixture-v1")
    # 将未来修订排在前面不能偷看。
    perturbed = build_snapshot([revised, original], [security_record()], at, "fixture-v1")
    # 未来输入完全不参与旧快照身份。
    assert perturbed.content_hash == old.content_hash
    # 历史值必须仍为独立指定的100。
    assert perturbed.records[0].raw_close == 100.0
    # 到修订可用时才选择新值。
    later = build_snapshot(
        [original, revised], [security_record()], at + timedelta(days=1), "fixture-v1"
    )
    # 新时点的最高可用修订确实生效。
    assert later.records[0].raw_close == 999.0


def test_hash_conflicts_quality_and_late_data_fail_closed() -> None:
    """验证同版本冲突、封印破损和最新质量失败均拒绝；无外部副作用。"""
    # 固定可知事件时刻。
    at = datetime(2021, 1, 4, 21, tzinfo=UTC)
    # 原始行情通过数据封印。
    original = market_record("A", at.date(), 100.0, at)
    # 同修订另一内容即使重新封印仍是版本冲突。
    conflict = seal_record(original.model_copy(update={"raw_close": 101.0}))
    # 两个同版本不能靠输入顺序择一。
    with pytest.raises(ContractError, match="版本内容冲突"):
        # 被测函数必须明确拒绝。
        build_snapshot([original, conflict], [security_record()], at, "fixture")
    # 未重新封印的内容改变属于损坏。
    with pytest.raises(ContractError, match="哈希"):
        # 即使只有一条记录也必须核验内容。
        build_snapshot(
            [original.model_copy(update={"raw_close": 200.0})], [security_record()], at, "fixture"
        )
    # 新修订质量失败不允许回退旧好值。
    bad = seal_record(original.model_copy(update={"revision": 2, "quality": "quarantined"}))
    # 正常决策必须被质量闸门阻断。
    with pytest.raises(ContractError, match="质量"):
        # 不允许静默忽略坏修订继续交易。
        build_snapshot([original, bad], [security_record()], at, "fixture")


def test_security_overlap_and_historical_ticker() -> None:
    """历史ticker修订仅在可知时生效，重叠有效区间失败，无外部副作用。"""
    # 固定历史主表。
    original = security_record()
    # 代码变更版本在下一年才可知。
    at = datetime(2021, 1, 4, 21, tzinfo=UTC)
    # 未来修订不能改写当时显示的ticker。
    future = seal_record(
        original.model_copy(
            update={"ticker": "NEW", "revision": 2, "available_at": at + timedelta(days=1)}
        )
    )
    # 截止当日只读旧代码。
    snapshot = build_snapshot([], [future, original], at, "fixture")
    # 稳定证券身份不随未来代码变化。
    assert snapshot.securities[0].ticker == "A"
    # 未关闭旧区间就新增区间属于主表冲突。
    overlap = seal_record(original.model_copy(update={"effective_from": date(2021, 1, 1)}))
    # 不允许任意挑一个同时有效的行业或ticker。
    with pytest.raises(ContractError, match="区间重叠"):
        # 错误在数据边界暴露。
        build_snapshot([], [original, overlap], at, "fixture")
