"""日历与注入时钟适配器；使用明确范围的 XNYS 常规时段，不查询网络。

范围外日期明确失败。真实系统时间仅可由将来独立适配器读取，当前演示固定时钟。
"""

from datetime import date, datetime, timedelta
from importlib.metadata import version
from typing import Any

import exchange_calendars as xcals


class FixedClock:
    """保存由应用或测试推进的业务时刻，供执行服务和 FakeBroker 共同读取。

    参数必须 UTC，advance 只改变本实例内的 _at，不自动推进市场或产生事件；
    多个组件共享同一实例时会读到相同业务时刻，不依赖运行程序的墙上时间。
    """

    def __init__(self, at: datetime) -> None:
        """以注入的 UTC 时刻初始化时钟；非 UTC 抛 ValueError。"""
        if at.utcoffset() != timedelta(0):
            raise ValueError("FixedClock 需要 UTC 时间")
        self._at = at

    def now(self) -> datetime:
        """读取当前注入的 UTC 时刻，不访问系统时钟。"""
        return self._at

    def advance(self, at: datetime) -> None:
        """将时钟前移至 UTC 时刻；逆行或非 UTC 抛 ValueError，只改变实例状态。"""
        if at.utcoffset() != timedelta(0) or at < self._at:
            raise ValueError("时钟只接受不早于当前时刻的 UTC 时间")
        self._at = at


class ExchangeCalendar:
    """将纽约证券交易所交易日历转换为本项目的日期与 UTC 开收盘接口。

    _calendar 是 exchange_calendars 提供的 XNYS 日历，负责假日、半日市和夏令时；
    version 把库版本与日期范围写进运行证据，供快照与回放确认同一日历输入。
    """

    def __init__(self, start: date, end: date) -> None:
        """构建含边界交易日日历；无效范围抛 ValueError，不联网。"""
        # 日期范围写入实例，避免第三方默认使用今天推导范围。
        self.start = start
        self.end = end
        self._calendar: Any = xcals.get_calendar("XNYS", start=str(start), end=str(end))
        self.version = f"XNYS:exchange-calendars-{version('exchange-calendars')}:{start}:{end}"

    def _check(self, value: date) -> None:
        """验证日期处于已声明的日历范围，越界抛 ValueError。"""
        if value < self.start or value > self.end:
            raise ValueError(f"日期 {value} 超出日历范围 {self.start}..{self.end}")

    def sessions(self, start: date, end: date) -> list[date]:
        """列出 start、end 均包含在内的交易日；休市区间可为空，范围越界抛 ValueError。"""
        self._check(start)
        self._check(end)
        # 底层库范围从首个交易日开始，声明范围边缘的假日必须合法裁剪。
        first = max(start, self._calendar.first_session.date())
        last = min(end, self._calendar.last_session.date())
        if first > last:
            return []
        # 库返回带日期信息的 Timestamp 索引；这里只交付 date 交易日标签，供因子窗口
        # 和周调仓调度计数。具体开收盘时刻仍需 open_at/close_at 查询，不能从标签猜时间。
        return [stamp.date() for stamp in self._calendar.sessions_in_range(str(first), str(last))]

    def open_at(self, session: date) -> datetime:
        """返回当日 UTC 开盘时间；范围外或非交易日抛 ValueError。"""
        self._check(session)
        return self._calendar.session_open(str(session)).to_pydatetime()  # type: ignore[no-any-return]

    def close_at(self, session: date) -> datetime:
        """查询当日 UTC 收盘时间，半日市采用早收盘；越界或非交易日抛 ValueError。"""
        self._check(session)
        return self._calendar.session_close(str(session)).to_pydatetime()  # type: ignore[no-any-return]

    def next_session(self, session: date) -> date:
        """查找严格晚于参数日期的下一交易日；覆盖范围内无后续日期则抛 ValueError。"""
        self._check(session)
        candidates = self.sessions(session + timedelta(days=1), self.end)
        if not candidates:
            raise ValueError("日历没有后续交易日")
        return candidates[0]

    def is_open(self, at: datetime) -> bool:
        """判断 UTC 时刻是否处于常规时段；非 UTC 抛 ValueError，范围外或休市返回 False。"""
        if at.utcoffset() != timedelta(0):
            raise ValueError("执行时间必须 UTC")
        # 美股日间常规交易的 UTC 日期与当地日期一致。
        day = at.date()
        if day < self.start or day > self.end or not self.sessions(day, day):
            return False
        # 开盘包含、收盘不包含，避免盘后执行。
        return self.open_at(day) <= at < self.close_at(day)
