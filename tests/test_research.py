"""隔离研究标签、可手算秩相关和真实标签时间清除测试。"""

# 明确日期构造让切分期望独立于被测实现。
from datetime import UTC, date, datetime, timedelta

# 浮点相关只采用舍入容差。
import pytest

# 固定交易日价格夹具不计算研究标签期望。
from test_data import history, market_record

# 因子输入类型不能包含未来收益字段。
# 有限交易日历独立于标签数据是否缺失。
from quant_core.adapters.calendar import ExchangeCalendar

# 共享因子类型与封印用于独立构造输入。
from quant_core.contracts import FactorValue, seal_record

# 被测研究能力。
from quant_core.research import (
    ResearchObservation,
    evaluate_research,
    label_observations,
    rolling_splits,
)


def observation(at: datetime, security_id: str = "A", value: float = 1.0) -> ResearchObservation:
    """建立显式因子和事后标签的独立样本；返回研究对象，无外部副作用。"""
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
    """单日五只因子与收益同序，IC=1、反向因子相关=-1，覆盖可手算，无副作用。"""
    # 五只股票的固定相对顺序完全明确。
    at = datetime(2021, 1, 15, 21, tzinfo=UTC)
    # 标签1%、2%、3%、4%、5%与动量单调相同。
    samples = [observation(at, str(index), float(index)) for index in range(1, 6)]
    # 原始候选10只，合法标签只有5只。
    result = evaluate_research(samples, expected_observations=10)
    # 覆盖率不得通过缩小分母掩盖缺失。
    assert result["coverage"] == 0.5
    # 动量秩与收益秩完全一致。
    assert result["factors"]["momentum"]["mean_rank_ic"] == pytest.approx(1.0)
    # 两因子方向在本人工样本中恰好相反。
    assert result["factor_rank_correlations"]["low_volatility:momentum"] == pytest.approx(-1.0)
    # 五组各一只，组收益可直接核验。
    assert result["factors"]["momentum"]["quintile_returns_by_date"]["2021-01-15"] == pytest.approx(
        [0.01, 0.02, 0.03, 0.04, 0.05]
    )


def test_rolling_calendar_months_holdout_and_whole_day_purge() -> None:
    """30个月固定样本验证12/3/3切分、末6月保留与跨边界整日清除，无副作用。"""
    # 每月15日一条样本避免月末意外跨界。
    samples = [
        observation(datetime(2021 + index // 12, index % 12 + 1, 15, 21, tzinfo=UTC))
        for index in range(30)
    ]
    # 2021年末的标签跨入2022训练/验证边界。
    boundary = datetime(2021, 12, 31, 21, tzinfo=UTC)
    # 同一天两只股票必须一起purge，不能跨分组。
    samples.extend([observation(boundary, "A"), observation(boundary, "B")])
    # 按真实标签事件时间切分。
    folds = rolling_splits(samples)
    # 30个月可形成3个季度步进完整窗口。
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
    """未来标签从下一开盘开始，第5交易日收盘结束；不修改历史因子对象，无副作用。"""
    # 第1日100元，随后5日110元。
    snapshot, calendar = history([100.0, 110.0, 110.0, 110.0, 110.0, 110.0])
    # 特征在第一天收盘后才可用。
    decision = snapshot.records[0].event_time + timedelta(minutes=1)
    # 冻结人工因子值，不从未来收益反算。
    feature = FactorValue(
        security_id="A",
        factor_id="momentum",
        decision_time=decision,
        snapshot_id="first-day",
        value=0.2,
    )
    # 标签函数接收独立未来行情输入。
    labels = label_observations([feature], snapshot.records, calendar)
    # 第一笔未来开盘价格已经是110，不能用第一天100计算10%收益。
    assert labels[0].forward_return == pytest.approx(0.0)
    # 起点严格等于第二个交易日开盘。
    assert labels[0].label_start == calendar.open_at(snapshot.records[1].session)
    # 第5个未来交易日正好是第6条记录收盘。
    assert labels[0].label_end == snapshot.records[5].event_time
    # 历史因子值没有被研究标签修改。
    assert feature.value == 0.2
    # 交易因子契约没有未来收益字段。
    assert "forward_return" not in feature.model_dump()


def test_missing_first_future_session_never_shifts_label_window() -> None:
    """真正下一交易日缺数据或被隔离时不能顺延至下一条好行情，无外部副作用。"""
    # 固定已收盘的1月3日决策。
    decision = datetime(2023, 1, 3, 21, 1, tzinfo=UTC)
    # 交易日历独立于当前可用标签数据。
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    # 冻结历史特征。
    feature = FactorValue(
        security_id="A",
        factor_id="momentum",
        decision_time=decision,
        snapshot_id="before-gap",
        value=0.2,
    )
    # 1月4日应为真正入口，但故意只提供1月5日起五条好行情。
    days = [date(2023, 1, day) for day in (5, 6, 9, 10, 11)]
    # 数据数量足够五条也不能决定标签起点。
    records = [market_record("A", day, 100.0, calendar.close_at(day)) for day in days]
    # 应跳过缺入口样本，不能错误地产生1月5日开盘标签。
    assert label_observations([feature], records, calendar) == []
    # 补入1月4日旧好版本以及最新隔离版本。
    original = market_record("A", date(2023, 1, 4), 100.0, calendar.close_at(date(2023, 1, 4)))
    # 坏最新修订不能回退旧值，也不能把标签开始顺延一天。
    bad = seal_record(original.model_copy(update={"revision": 2, "quality": "quarantined"}))
    # 两种缺入口来源遵循同一个固定交易区间。
    assert label_observations([feature], records + [original, bad], calendar) == []
