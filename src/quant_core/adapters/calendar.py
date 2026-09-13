"""日历与注入时钟适配器；使用明确范围的 XNYS 常规时段，不查询网络。

范围外日期明确失败。真实系统时间仅可由将来独立适配器读取，当前演示固定时钟。
"""

# 日期和 UTC 时刻分别表达交易日与执行时间。
from datetime import date, datetime, timedelta

# 读取实际日历版本用于运行指纹。
from importlib.metadata import version

# Any 仅用于无类型第三方日历边界。
from typing import Any

# 成熟交易所日历提供假日、半日市和夏令时规则。
import exchange_calendars as xcals


class FixedClock:
    """确定性可前移的测试时钟；参数必须 UTC，修改只影响本实例。"""

    def __init__(self, at: datetime) -> None:
        """保存注入 UTC 时刻；非 UTC 抛 ValueError，无外部副作用。"""
        # 不允许没有时区或纽约当地时间混入 UTC 状态。
        if at.utcoffset() != timedelta(0):
            # 显式失败比悄悄转换未知时区可靠。
            raise ValueError("FixedClock 需要 UTC 时间")
        # 时钟状态由测试或应用编排控制。
        self._at = at

    def now(self) -> datetime:
        """返回当前注入 UTC 时刻，无副作用。"""
        # 不读取操作系统时钟。
        return self._at

    def advance(self, at: datetime) -> None:
        """将时钟前移至 UTC 时刻；逆行或非 UTC 抛 ValueError，只改变实例状态。"""
        # 真实事件回放不能悄悄倒退应用时钟。
        if at.utcoffset() != timedelta(0) or at < self._at:
            # 历史研究应创建新时钟，而非倒退已有执行状态。
            raise ValueError("时钟只接受不早于当前时刻的 UTC 时间")
        # 调用者明确推进业务时间。
        self._at = at


class ExchangeCalendar:
    """固定日期范围的纽约证券交易所日历，输出 UTC 开收盘时间。"""

    def __init__(self, start: date, end: date) -> None:
        """构建含边界交易日日历；无效范围抛 ValueError，不联网。"""
        # 日期范围写入实例，避免第三方默认使用今天推导范围。
        self.start = start
        # 明确日历覆盖终点。
        self.end = end
        # 第三方不带完整类型信息，限制 Any 在适配器内部。
        self._calendar: Any = xcals.get_calendar("XNYS", start=str(start), end=str(end))
        # 版本和范围共同标识日历输入。
        self.version = f"XNYS:exchange-calendars-{version('exchange-calendars')}:{start}:{end}"

    def _check(self, value: date) -> None:
        """验证日期在显式范围内；越界抛 ValueError，无副作用。"""
        # 禁止把缺失的远期日历当作普通工作日。
        if value < self.start or value > self.end:
            # 报告可覆盖范围，便于操作手册排障。
            raise ValueError(f"日期 {value} 超出日历范围 {self.start}..{self.end}")

    def sessions(self, start: date, end: date) -> list[date]:
        """返回闭区间合格交易日；越界抛 ValueError，无副作用。"""
        # 分别检查查询的起点和终点。
        self._check(start)
        # 结束日期也必须位于已固定范围。
        self._check(end)
        # 底层库范围从首个交易日开始，声明范围边缘的假日必须合法裁剪。
        first = max(start, self._calendar.first_session.date())
        # 周末结束日期也不能被底层库误报越界。
        last = min(end, self._calendar.last_session.date())
        # 合法自然日范围可能不包含任何交易日。
        if first > last:
            # 空会话是明确的休市结果。
            return []
        # 把第三方 Timestamp 限制在此边界内。
        return [stamp.date() for stamp in self._calendar.sessions_in_range(str(first), str(last))]

    def open_at(self, session: date) -> datetime:
        """返回当日 UTC 开盘时间；范围外或非交易日抛 ValueError。"""
        # 首先防止第三方隐式扩展范围。
        self._check(session)
        # to_pydatetime 保留已知 UTC 时区。
        return self._calendar.session_open(str(session)).to_pydatetime()  # type: ignore[no-any-return]

    def close_at(self, session: date) -> datetime:
        """返回当日 UTC 收盘时间，半日市使用实际早收盘，无副作用。"""
        # 校验日期覆盖范围。
        self._check(session)
        # 禁止用固定 16 点假设替代交易日历。
        return self._calendar.session_close(str(session)).to_pydatetime()  # type: ignore[no-any-return]

    def next_session(self, session: date) -> date:
        """返回严格晚于参数的下一交易日；无后续日期抛 ValueError，无副作用。"""
        # 日期必须来自已声明范围。
        self._check(session)
        # 支持输入自然日而不只接受当前交易日。
        candidates = self.sessions(session + timedelta(days=1), self.end)
        # 没有后续时段不能凭空安排执行。
        if not candidates:
            # 调用者应扩展经验证的日历范围。
            raise ValueError("日历没有后续交易日")
        # 交易日列表已经按时间升序排列。
        return candidates[0]

    def is_open(self, at: datetime) -> bool:
        """判断 UTC 时刻是否在常规时段；范围外或非交易日返回 False，无副作用。"""
        # 时区错误是输入错误，而非市场休市。
        if at.utcoffset() != timedelta(0):
            # 与公共契约一致地拒绝不明确时间。
            raise ValueError("执行时间必须 UTC")
        # 美股日间常规交易的 UTC 日期与当地日期一致。
        day = at.date()
        # 超过固定范围不能判为可交易。
        if day < self.start or day > self.end or not self.sessions(day, day):
            # 明确休市或无覆盖。
            return False
        # 开盘包含、收盘不包含，避免盘后执行。
        return self.open_at(day) <= at < self.close_at(day)
