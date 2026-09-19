"""版本化双因子与均线指标；分别使用总回报、仅拆股价格，不产生交易副作用。"""

from __future__ import annotations

import math
from statistics import mean, stdev
from zoneinfo import ZoneInfo

from quant_core.contracts import (
    ContractError,
    DataSnapshot,
    FactorDefinition,
    FactorValue,
    MarketDataRecord,
    TradingCalendar,
    verify_record,
)
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
                if any(price is None for price in values):
                    reason = "missing_total_return_price"
                elif all(
                    price is not None and math.isfinite(price) and price > 0 for price in values
                ):
                    # 已确认没有缺失，收窄类型后继续原公式，不以原始价填补研究价。
                    valid_prices = [price for price in values if price is not None]
                    # 253 个价格覆盖 [t-252, t]；倒数第 22 项是 t-21，跳过最近 21 日。
                    if definition.factor_id == "momentum":
                        value = valid_prices[-22] / valid_prices[0] - 1.0
                    # 61 个价格形成 60 个简单日收益，不替换为对数收益。
                    else:
                        # zip 把原列表和去掉首项的列表配成 (前日价, 当日价)。长度相差一，
                        # strict=False 按较短列表结束，恰好得到 60 个收益，没有最后一天重复配对。
                        returns = [
                            current / previous - 1.0
                            for previous, current in zip(
                                valid_prices, valid_prices[1:], strict=False
                            )
                        ]
                        # stdev 计算样本标准差：60个收益的平方偏差和先除以60-1=59，再开方。
                        # 乘 sqrt(252) 按一年252个交易日的演示约定年化；取负使波动越低值越大。
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


# MA5/MA20 是美元均线的解释项；只有无量纲强度 ma_trend 参与横截面评分。
MA_DEFINITIONS = (
    FactorDefinition(
        factor_id="ma5",
        hypothesis="最近五日价格均值描述短期价格水平，不独立参与排名",
        formula="mean(split_adjusted_close[t-4:t+1])",
        minimum_prices=20,
        unit="USD",
    ),
    FactorDefinition(
        factor_id="ma20",
        hypothesis="最近二十日价格均值描述中期价格水平，不独立参与排名",
        formula="mean(split_adjusted_close[t-19:t+1])",
        minimum_prices=20,
        unit="USD",
    ),
    FactorDefinition(
        factor_id="ma_trend",
        hypothesis="短期均线高于中期均线描述上升趋势，不保证收益",
        formula="MA5/MA20-1",
        minimum_prices=20,
        unit="ratio",
    ),
)


def calculate_ma_factors(snapshot: DataSnapshot, calendar: TradingCalendar) -> list[FactorValue]:
    """以同一连续二十个已收盘交易日计算 MA5、MA20 与均线强度。

    仅拆股调整价不含股息再投资；不会回退到原始价或总回报价。快照应经过数据闸门，
    此处仍过滤未来可用版本并核对行情封印、版本冲突及质量。窗口缺日或缺调整价时，
    三个结果一并为空且说明原因，避免五日均线有效却被误当成完整策略输入。
    """
    # selected 按稳定证券身份和交易日保存当前可知最高修订；同版本不同内容拒绝。
    selected: dict[tuple[str, object], MarketDataRecord] = {}
    versions: dict[tuple[str, object, int], str] = {}
    for record in snapshot.records:
        if record.available_at > snapshot.decision_time:
            continue
        verify_record(record)
        if record.event_time > snapshot.decision_time:
            raise ContractError("均线行情事件晚于决策时间")
        identity = (record.security_id, record.session, record.revision)
        if identity in versions and versions[identity] != record.content_hash:
            raise ContractError("均线行情相同版本内容冲突")
        versions[identity] = record.content_hash
        key = (record.security_id, record.session)
        if key not in selected or record.revision > selected[key].revision:
            selected[key] = record
    # 使用日历最后二十个已收盘交易日，不因个别证券缺日而向更早日期借数据。
    earliest = min((record.session for record in selected.values()), default=None)
    completed = (
        []
        if earliest is None
        else [
            session
            for session in calendar.sessions(
                earliest, snapshot.decision_time.astimezone(ZoneInfo("America/New_York")).date()
            )
            if calendar.close_at(session) <= snapshot.decision_time
        ]
    )
    window = completed[-20:]
    results: list[FactorValue] = []
    for security in qualified_universe(snapshot):
        values: dict[str, float] = {}
        reason: str | None = "insufficient_or_missing_sessions"
        if len(window) == 20 and all(
            (security.security_id, session) in selected for session in window
        ):
            history = [selected[(security.security_id, session)] for session in window]
            prices = [record.split_adjusted_close for record in history]
            if any(record.quality != "good" for record in history):
                reason = "bad_price_quality"
            elif any(price is None for price in prices):
                reason = "missing_split_adjusted_price"
            elif any(
                price is not None and (not math.isfinite(price) or price <= 0) for price in prices
            ):
                reason = "invalid_price"
            else:
                # 最近五日包含 t；二十日包含 t-19 到 t。两者同币种相除得到无量纲强度。
                valid_prices = [price for price in prices if price is not None]
                ma5 = mean(valid_prices[-5:])
                ma20 = mean(valid_prices)
                values = {"ma5": ma5, "ma20": ma20, "ma_trend": ma5 / ma20 - 1.0}
                reason = None
        for definition in MA_DEFINITIONS:
            results.append(
                FactorValue(
                    security_id=security.security_id,
                    factor_id=definition.factor_id,
                    factor_version=definition.version,
                    decision_time=snapshot.decision_time,
                    snapshot_id=snapshot.snapshot_id,
                    value=values.get(definition.factor_id),
                    reason=reason,
                )
            )
    return results
