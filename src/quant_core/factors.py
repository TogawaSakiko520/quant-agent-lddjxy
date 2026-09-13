"""版本化动量和低波动公式；只读取时点快照的总回报序列，不产生交易副作用。"""

# 延迟类型注解统一现代类型写法。
from __future__ import annotations

# 年化和有限值检查使用确定的数学函数。
import math

# 样本标准差明确使用 n-1 分母。
from statistics import stdev

# 输入输出遵循唯一共享契约。
from quant_core.contracts import DataSnapshot, FactorDefinition, FactorValue, TradingCalendar

# 股票池模块集中管理资格。
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
    """计算合格股票的两个因子；缺少连续窗口返回带原因空值，不补值，无副作用。"""
    # 没有行情时仍逐证券给出历史不足解释。
    earliest = min((record.session for record in snapshot.records), default=None)
    # 决策日期只作日历查询上界，实际截止还检查收盘。
    sessions = (
        [] if earliest is None else calendar.sessions(earliest, snapshot.decision_time.date())
    )
    # 当日尚未收盘时不能把未完成交易日纳入窗口。
    completed = [
        session for session in sessions if calendar.close_at(session) <= snapshot.decision_time
    ]
    # 以证券和交易日直接索引，避免缺日被压缩成连续窗口。
    prices = {
        (record.security_id, record.session): record.total_return_close
        for record in snapshot.records
        if record.available_at <= snapshot.decision_time
    }
    # 输出顺序固定为证券再因子定义顺序。
    results: list[FactorValue] = []
    # 所有因子都使用同一合格股票池。
    for security in qualified_universe(snapshot):
        # 不同公式分别验证各自最短连续历史。
        for definition in DEFINITIONS:
            # 从同一个已完成日 t 向前取完整交易日窗口。
            window = completed[-definition.minimum_prices :]
            # 缺日和不足历史不能用较旧观测代替。
            complete = len(window) == definition.minimum_prices and all(
                (security.security_id, session) in prices for session in window
            )
            # 缺失原因与数值同时保存。
            value: float | None = None
            # 默认异常原因随后只在成功时清除。
            reason: str | None = "insufficient_or_missing_sessions"
            # 仅完整窗口进入数值计算。
            if complete:
                # 提取已按交易日排序的总回报价格。
                values = [prices[(security.security_id, session)] for session in window]
                # 模型边界之外再次检查数学前提。
                if all(math.isfinite(price) and price > 0 for price in values):
                    # 动量索引 t-21 对应 253 窗口倒数第 22 项。
                    if definition.factor_id == "momentum":
                        # 第一个价格恰好是 t-252。
                        value = values[-22] / values[0] - 1.0
                    # 另一公式从 61 价格形成 60 日收益。
                    else:
                        # 简单收益保持与文档一致，不替换为对数收益。
                        returns = [
                            current / previous - 1.0
                            for previous, current in zip(values, values[1:], strict=False)
                        ]
                        # stdev 使用 ddof=1；负号统一为越大越好。
                        value = -stdev(returns) * math.sqrt(252)
                    # 成功值不携带排除原因。
                    reason = None
                # 非有限或非正输入不能当作低波动信号。
                else:
                    # 保留机器可读的异常价格原因。
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
    # 无外部状态更新，结果由调用者持久化。
    return results
