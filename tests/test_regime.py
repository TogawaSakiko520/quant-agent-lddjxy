"""市场状态的因果确认、连续交易日与未知降级测试。"""

# 未来扰动通过显式时间偏移构造。
from datetime import timedelta

# 夹具只装配交易日价格。
from test_data import history

# 修改记录后显式重签，用于合法追加输入测试。
from quant_core.contracts import seal_record

# 市场状态观察入口。
from quant_core.regime import assess_regime


def test_trend_requires_three_sessions_and_never_changes_budget() -> None:
    """价格向上穿越阈值后连续3日才变UP，所有状态只观察，无外部副作用。"""
    # 首个200日窗口只有当天第一次突破。
    initial, calendar = history([100.0] * 199 + [110.0])
    # 两个确认日时仍不能宣称趋势已切换。
    two, calendar = history([100.0] * 199 + [110.0] * 2)
    # 第三个确认日才能转UP。
    three, calendar = history([100.0] * 199 + [110.0] * 3)
    # 第一天状态保留中性。
    assert assess_regime(initial.records, initial.decision_time, calendar).state.startswith(
        "NEUTRAL"
    )
    # 第二天不提早通过确认。
    assert assess_regime(two.records, two.decision_time, calendar).state.startswith("NEUTRAL")
    # 第三天达到规则定义的确认窗口。
    result = assess_regime(three.records, three.decision_time, calendar)
    # 趋势已转向且仍只是观察。
    assert result.state.startswith("UP")
    # 任何市场状态都不能暗中改变预算。
    assert result.affects_budget is False


def test_stale_missing_and_future_records_do_not_fake_normal_state() -> None:
    """缺日或末值过期返回UNKNOWN，未来输入不改变旧状态，无外部副作用。"""
    # 足够历史的常数序列构成正常观察基线。
    snapshot, calendar = history([100.0] * 205)
    # 删除一个交易日后不允许压缩历史。
    assert (
        assess_regime(
            snapshot.records[1:-5] + snapshot.records[-4:], snapshot.decision_time, calendar
        ).state
        == "UNKNOWN"
    )
    # 决策推进到下一交易日收盘后而没有行情即数据过期。
    later = calendar.close_at(calendar.next_session(snapshot.records[-1].session)) + timedelta(
        minutes=1
    )
    # 200条旧记录不能替代最新收盘。
    assert assess_regime(snapshot.records, later, calendar).state == "UNKNOWN"
    # 新版本仅在未来可知，不能污染本时点。
    future = seal_record(
        snapshot.records[-1].model_copy(
            update={"revision": 2, "total_return_close": 999.0, "available_at": later}
        )
    )
    # 未来追加记录对旧观察结果完全无影响。
    assert assess_regime(
        snapshot.records + [future], snapshot.decision_time, calendar
    ) == assess_regime(snapshot.records, snapshot.decision_time, calendar)
