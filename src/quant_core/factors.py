"""版本化动量和低波动公式；只读取时点快照的总回报序列，不产生交易副作用。"""

from __future__ import annotations

import math
from statistics import stdev

from quant_core.contracts import DataSnapshot, FactorDefinition, FactorValue, TradingCalendar
from quant_core.universe import qualified_universe

# 稳定因子定义被说明、报告和计算共同引用。
DEFINITIONS = (
    FactorDefinition(
        factor_id="momentum",
        hypothesis="过去相对强弱可能持续，也可能失效",
        formula="P[t-21]/P[t-252]-1",
        minimum_prices=253,
        unit="ratio",
    ),
    FactorDefinition(
        factor_id="low_volatility",
        hypothesis="低历史波动描述更平稳路径，不保证收益",
        formula="-sample_std(last_60_daily_returns)*sqrt(252)",
        minimum_prices=61,
        unit="annualized_ratio",
    ),
)


def calculate_factors(snapshot: DataSnapshot, calendar: TradingCalendar) -> list[FactorValue]:
    """将合格股票的总回报历史转换为动量和低波动因子。

    snapshot 应来自时点和质量闸门；日历决定决策时刻已收盘的交易日 t。
    每只证券按定义顺序输出两个结果；历史不足或窗口缺日时 value 为 None，
    reason 说明原因，不用更早价格补齐。数值方向统一为越大越好。
    """
    # 先确定共同的已收盘交易日轴；没有行情时仍输出各证券的历史不足原因。
    earliest = min((record.session for record in snapshot.records), default=None)
    sessions = (
        [] if earliest is None else calendar.sessions(earliest, snapshot.decision_time.date())
    )
    # 当日尚未收盘时不能把未完成交易日纳入窗口。
    completed = [
        session for session in sessions if calendar.close_at(session) <= snapshot.decision_time
    ]
    # prices 是 (稳定证券 ID, 交易日)→总回报研究收盘价；与执行原始报价分开。
    # 用日历日期查找每个价格，避免某证券缺日后把更早价格挤进最近窗口。
    prices = {
        (record.security_id, record.session): record.total_return_close
        for record in snapshot.records
        if record.available_at <= snapshot.decision_time
    }
    results: list[FactorValue] = []
    # 每个公式从同一交易日 t 向前取自己的连续窗口，缺日不能压缩成连续观测。
    for security in qualified_universe(snapshot):
        for definition in DEFINITIONS:
            # completed 由日历按时间升序提供，尾部切片取最近 253 或 61 个已收盘日。
            # 切片本身不会补齐不足的历史，下面同时检查天数与每一天是否有价格。
            window = completed[-definition.minimum_prices :]
            # 缺日和不足历史不能用较旧观测代替。
            complete = len(window) == definition.minimum_prices and all(
                (security.security_id, session) in prices for session in window
            )
            value: float | None = None
            reason: str | None = "insufficient_or_missing_sessions"
            if complete:
                # 此函数的 values 是单只证券、单个窗口的价格列表，顺序与 window 一致；
                # 不同于 signals 中同名的“证券→因子”字典，下一步直接用于价格比值。
                values = [prices[(security.security_id, session)] for session in window]
                # 模型边界之外再次检查数学前提。
                if all(math.isfinite(price) and price > 0 for price in values):
                    # 253 个价格覆盖 [t-252, t]；倒数第 22 项是 t-21，跳过最近 21 日。
                    if definition.factor_id == "momentum":
                        value = values[-22] / values[0] - 1.0
                    # 61 个价格形成 60 个简单日收益，不替换为对数收益。
                    else:
                        # zip 把原列表和去掉首项的列表配成 (前日价, 当日价)。长度相差一，
                        # strict=False 按较短列表结束，恰好得到 60 个收益，没有最后一天重复配对。
                        returns = [
                            current / previous - 1.0
                            for previous, current in zip(values, values[1:], strict=False)
                        ]
                        # stdev 使用 ddof=1；负号统一为越大越好。
                        value = -stdev(returns) * math.sqrt(252)
                    reason = None
                # 非有限或非正输入不能当作低波动信号。
                else:
                    reason = "invalid_price"
            # 绑定因子版本、输入快照与时点。
            results.append(
                FactorValue(
                    security_id=security.security_id,
                    factor_id=definition.factor_id,
                    factor_version=definition.version,
                    decision_time=snapshot.decision_time,
                    snapshot_id=snapshot.snapshot_id,
                    value=value,
                    reason=reason,
                )
            )
    return results
