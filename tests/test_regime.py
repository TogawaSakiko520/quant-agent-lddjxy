"""市场状态的因果确认、连续交易日与未知降级测试。"""

from datetime import timedelta

from test_data import history

from quant_core.contracts import seal_record
from quant_core.regime import assess_regime


def test_trend_requires_three_sessions_and_never_changes_budget() -> None:
    """价格向上穿越阈值后连续3日才变UP，所有状态只观察。"""
    # history 原本生成证券 A 的日行情，此处仅把其总回报序列当作单一市场基准样本；
    # 不涉及股票横截面排名。前三组分别只有1、2、3个已满足200日预热的突破日。
    # 首个200日窗口只有当天第一次突破。
    initial, calendar = history([100.0] * 199 + [110.0])
    two, calendar = history([100.0] * 199 + [110.0] * 2)
    three, calendar = history([100.0] * 199 + [110.0] * 3)
    assert assess_regime(initial.records, initial.decision_time, calendar).state.startswith(
        "NEUTRAL"
    )
    assert assess_regime(two.records, two.decision_time, calendar).state.startswith("NEUTRAL")
    result = assess_regime(three.records, three.decision_time, calendar)
    assert result.state.startswith("UP")
    assert result.affects_budget is False


def test_stale_missing_and_future_records_do_not_fake_normal_state() -> None:
    """缺日或末值过期返回UNKNOWN，未来输入不改变旧状态。"""
    snapshot, calendar = history([100.0] * 205)
    # 同时裁去首条记录并删除倒数第5条：首日变化只缩短覆盖起点，内部缺日仍应触发UNKNOWN，
    # 不能把缺口两侧的价格压缩成连续交易日。
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
    assert assess_regime(snapshot.records, later, calendar).state == "UNKNOWN"
    # 新版本仅在未来可知，不能污染本时点。
    future = seal_record(
        snapshot.records[-1].model_copy(
            update={"revision": 2, "total_return_close": 999.0, "available_at": later}
        )
    )
    assert assess_regime(
        snapshot.records + [future], snapshot.decision_time, calendar
    ) == assess_regime(snapshot.records, snapshot.decision_time, calendar)
