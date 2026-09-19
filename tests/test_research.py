"""隔离研究标签、可手算秩相关和真实标签时间清除测试。"""

from datetime import UTC, date, datetime, timedelta

import pytest
from test_data import history, market_record

from quant_core.adapters.calendar import ExchangeCalendar
from quant_core.contracts import FactorValue, seal_record
from quant_core.research import (
    ResearchObservation,
    evaluate_research,
    label_observations,
    rolling_splits,
)


def observation(at: datetime, security_id: str = "A", value: float = 1.0) -> ResearchObservation:
    """装配某证券某时点的研究观察，不调用真实标签生成器。

    两因子分别为 value 和 -value，事后收益固定 value/100；这里的标签区间用
    自然日 +1/+5 简化构造，仅服务统计与切分反例。真实五交易日标签另有测试。
    """
    # 标签始终严格晚于决策，不使用当日已发生的开盘。
    return ResearchObservation(
        security_id=security_id,
        decision_time=at,
        label_start=at + timedelta(days=1),
        label_end=at + timedelta(days=5),
        factor_values={"momentum": value, "low_volatility": -value},
        forward_return=value / 100,
    )


def test_rank_ic_coverage_and_factor_correlation_hand_sample() -> None:
    """单日五只因子与收益同序，IC=1、反向因子相关=-1，覆盖可手算。"""
    at = datetime(2021, 1, 15, 21, tzinfo=UTC)
    # 标签1%、2%、3%、4%、5%与动量单调相同。
    samples = [observation(at, str(index), float(index)) for index in range(1, 6)]
    # 原始候选10只，合法标签只有5只。
    result = evaluate_research(samples, expected_observations=10)
    assert result["coverage"] == 0.5
    assert result["factors"]["momentum"]["mean_rank_ic"] == pytest.approx(1.0)
    # 两因子方向在本人工样本中恰好相反。
    assert result["factor_rank_correlations"]["low_volatility:momentum"] == pytest.approx(-1.0)
    assert result["factors"]["momentum"]["quintile_returns_by_date"]["2021-01-15"] == pytest.approx(
        [0.01, 0.02, 0.03, 0.04, 0.05]
    )


def test_rolling_calendar_months_holdout_and_whole_day_purge() -> None:
    """30个月固定样本验证12/3/3切分、末6月保留与跨边界整日清除。"""
    # 每月15日一条样本避免月末意外跨界。
    samples = [
        observation(datetime(2021 + index // 12, index % 12 + 1, 15, 21, tzinfo=UTC))
        for index in range(30)
    ]
    # 2021年末的标签跨入2022训练/验证边界。
    boundary = datetime(2021, 12, 31, 21, tzinfo=UTC)
    # purge（清除跨边界标签）必须按整个决策日处理；12月31日样本的 +5日
    # 标签跨进次年1月验证期，不能留在训练集中偷看验证期收益。
    # 同一天两只股票必须一起purge，不能跨分组。
    samples.extend([observation(boundary, "A"), observation(boundary, "B")])
    # fold 返回原 samples 列表的索引而非复制的数据；0..29对应30个月，30/31是
    # 额外追加的同日两证券，所以可独立写出每个分组应包含的整数位置。
    folds = rolling_splits(samples)
    assert len(folds) == 3
    # 首个训练集为2021年的12个月15日。
    assert folds[0].train == list(range(12))
    # 验证为2022年1至3月。
    assert folds[0].validation == [12, 13, 14]
    # 首个测试为2022年4至6月。
    assert folds[0].test == [15, 16, 17]
    # 2023年1至6月为永久最终保留集。
    assert folds[0].holdout == [24, 25, 26, 27, 28, 29]
    # 年末两只证券均被按整日标签区间清除。
    assert folds[0].purged == [30, 31]
    # 只有一年历史不能放宽为一个假窗口。
    assert rolling_splits(samples[:12]) == []


def test_next_open_five_sessions_and_label_never_enters_factor() -> None:
    """未来标签从下一开盘开始，第5交易日收盘结束；不修改历史因子对象。"""
    # 第1日100元，随后5日110元。
    snapshot, calendar = history([100.0, 110.0, 110.0, 110.0, 110.0, 110.0])
    # 特征在第一天收盘后才可用。
    decision = snapshot.records[0].event_time + timedelta(minutes=1)
    feature = FactorValue(
        security_id="A",
        factor_id="momentum",
        decision_time=decision,
        snapshot_id="first-day",
        value=0.2,
    )
    # feature 是事先给定的历史因子输入；snapshot.records 在本研究局部还包含
    # 决策之后的价格，只可供收益标签使用。返回 labels 不得反写 feature。
    labels = label_observations([feature], snapshot.records, calendar)
    # 第一笔未来开盘价格已经是110，不能用第一天100计算10%收益。
    assert labels[0].forward_return == pytest.approx(0.0)
    assert labels[0].label_start == calendar.open_at(snapshot.records[1].session)
    # 第5个未来交易日正好是第6条记录收盘。
    assert labels[0].label_end == snapshot.records[5].event_time
    assert feature.value == 0.2
    assert "forward_return" not in feature.model_dump()


def test_missing_first_future_session_never_shifts_label_window() -> None:
    """真正下一交易日缺数据或被隔离时不能顺延至下一条好行情。"""
    decision = datetime(2023, 1, 3, 21, 1, tzinfo=UTC)
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    feature = FactorValue(
        security_id="A",
        factor_id="momentum",
        decision_time=decision,
        snapshot_id="before-gap",
        value=0.2,
    )
    # 1月4日应为真正入口，但故意只提供1月5日起五条好行情。
    days = [date(2023, 1, day) for day in (5, 6, 9, 10, 11)]
    records = [market_record("A", day, 100.0, calendar.close_at(day)) for day in days]
    assert label_observations([feature], records, calendar) == []
    # 补入1月4日旧好版本以及最新隔离版本。
    original = market_record("A", date(2023, 1, 4), 100.0, calendar.close_at(date(2023, 1, 4)))
    # 坏最新修订不能回退旧值，也不能把标签开始顺延一天。
    bad = seal_record(original.model_copy(update={"revision": 2, "quality": "quarantined"}))
    assert label_observations([feature], records + [original, bad], calendar) == []
