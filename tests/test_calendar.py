"""纽约交易所会话、节假日、半日市及夏令时的独立固定日期验收。"""

from datetime import UTC, date, datetime, timedelta

import pytest

from quant_core.adapters.calendar import ExchangeCalendar, FixedClock


def test_declared_holiday_and_weekend_edges_are_valid_empty_sessions() -> None:
    """声明范围可以始于元旦、终于周末，边缘自然日不是越界错误。"""
    # 2021年元旦休市，2023年最后一天为星期日。
    calendar = ExchangeCalendar(date(2021, 1, 1), date(2023, 12, 31))
    assert calendar.sessions(date(2021, 1, 1), date(2021, 1, 1)) == []
    assert calendar.sessions(date(2021, 1, 1), date(2021, 1, 4)) == [date(2021, 1, 4)]
    assert calendar.sessions(date(2023, 12, 29), date(2023, 12, 31)) == [date(2023, 12, 29)]
    assert calendar.sessions(date(2023, 12, 30), date(2023, 12, 31)) == []


def test_good_friday_half_day_and_dst_have_exact_utc_times() -> None:
    """固定2023年日期验证耶稣受难日、感恩节后半日市和春季夏令时。"""
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 12, 31))
    # 2023年4月7日受难日美股休市，4月6日之后是4月10日。
    assert calendar.sessions(date(2023, 4, 7), date(2023, 4, 7)) == []
    assert calendar.next_session(date(2023, 4, 6)) == date(2023, 4, 10)
    # 感恩节后周五当地13点收市，对应UTC18点。
    assert calendar.close_at(date(2023, 11, 24)) == datetime(2023, 11, 24, 18, tzinfo=UTC)
    assert calendar.open_at(date(2023, 11, 24)) == datetime(2023, 11, 24, 14, 30, tzinfo=UTC)
    # 夏令时开始前周五开盘UTC14:30。
    assert calendar.open_at(date(2023, 3, 10)) == datetime(2023, 3, 10, 14, 30, tzinfo=UTC)
    # 夏令时开始后周一开盘提前为UTC13:30。
    assert calendar.open_at(date(2023, 3, 13)) == datetime(2023, 3, 13, 13, 30, tzinfo=UTC)
    # 收盘也从21点变成20点，不是只改开盘显示。
    assert calendar.close_at(date(2023, 3, 10)) == datetime(2023, 3, 10, 21, tzinfo=UTC)
    assert calendar.close_at(date(2023, 3, 13)) == datetime(2023, 3, 13, 20, tzinfo=UTC)


def test_session_contains_open_excludes_close_and_rejects_out_of_range() -> None:
    """常规时段左闭右开；范围外查询明确失败，不能自动扩展未来日历。"""
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    # 手工给出1月3日正常开盘时刻。
    opened = datetime(2023, 1, 3, 14, 30, tzinfo=UTC)
    closed = datetime(2023, 1, 3, 21, tzinfo=UTC)
    assert calendar.is_open(opened)
    assert not calendar.is_open(opened - timedelta(microseconds=1))
    assert calendar.is_open(closed - timedelta(microseconds=1))
    assert not calendar.is_open(closed)
    # 无覆盖的未来时刻不能声称可交易。
    assert not calendar.is_open(datetime(2023, 2, 1, 15, tzinfo=UTC))
    # 主动查询超出范围必须明确失败。
    with pytest.raises(ValueError, match="超出日历范围"):
        calendar.sessions(date(2022, 12, 31), date(2023, 1, 3))
    with pytest.raises(ValueError):
        calendar.next_session(date(2023, 1, 31))
    # 无时区输入不是休市，而是调用错误。
    with pytest.raises(ValueError, match="UTC"):
        calendar.is_open(opened.replace(tzinfo=None))


def test_fixed_clock_never_reads_wall_time_and_cannot_reverse() -> None:
    """固定时钟只按显式事件前移；逆行或无时区输入拒绝，仅改变本地对象。"""
    initial = datetime(2023, 1, 3, 14, 30, tzinfo=UTC)
    clock = FixedClock(initial)
    assert clock.now() == initial
    # 同时刻重放不改变时间顺序。
    clock.advance(initial)
    clock.advance(initial + timedelta(minutes=1))
    assert clock.now() == initial + timedelta(minutes=1)
    # 不能通过倒退时钟重做历史执行。
    with pytest.raises(ValueError, match="时钟"):
        clock.advance(initial)
    with pytest.raises(ValueError, match="UTC"):
        FixedClock(initial.replace(tzinfo=None))
