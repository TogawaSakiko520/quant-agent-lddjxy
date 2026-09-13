"""透明趋势及波动观察器；全历史因果重放滞回与确认，不改变组合预算。"""

# 注解延迟解析，类型边界保持明确。
from __future__ import annotations

# 平方根用于固定252日年化。
import math

# 接受显式注入的UTC观察时间。
from datetime import datetime

# 固定窗口均值和样本标准差。
from statistics import mean, stdev

# 输出观察证据，不写账户或配置。
from quant_core.contracts import (
    ContractError,
    MarketDataRecord,
    RegimeAssessment,
    TradingCalendar,
    verify_record,
)


def assess_regime(
    records: list[MarketDataRecord],
    as_of: datetime,
    calendar: TradingCalendar | None = None,
) -> RegimeAssessment:
    """对单一基准因果重放200日趋势和60日波动；返回观察状态，混合基准抛错，无副作用。"""
    # 未来版本完全不参与本次观察。
    visible = [
        record for record in records if record.available_at <= as_of and record.event_time <= as_of
    ]
    # 基准序列不能混合不同证券。
    if len({record.security_id for record in visible}) > 1:
        # 调用方必须显式选择一个基准。
        raise ContractError("市场状态要求单一基准")
    # 每日只取当时可用最高修订。
    selected: dict[object, MarketDataRecord] = {}
    # 同一版本冲突不能被最新值覆盖。
    versions: dict[tuple[object, int], str] = {}
    # 输入顺序不影响状态。
    for record in visible:
        # 基准也执行来源封印核验。
        verify_record(record)
        # 身份由交易日与修订组成。
        key = (record.session, record.revision)
        # 检查同键内容冲突。
        if key in versions and versions[key] != record.content_hash:
            # 无法可信决定基准时停止。
            raise ContractError("基准版本冲突")
        # 记住本修订的内容。
        versions[key] = record.content_hash
        # 高修订替换低修订。
        if record.session not in selected or record.revision > selected[record.session].revision:
            # 仅修改本地计算索引。
            selected[record.session] = record
    # 日顺序决定状态确认的先后。
    history = sorted(selected.values(), key=lambda record: record.session)
    # 没有市场日历无法证明连续交易日与末日新鲜度。
    if calendar is None or not history:
        # 明确降级，绝不把观测条数冒充完整交易日。
        return RegimeAssessment(
            as_of=as_of, state="UNKNOWN", evidence={"reason": "missing_calendar_or_history"}
        )
    # 全历史因果重放需要从首日至本时点最后收盘日完整覆盖。
    expected = [
        session
        for session in calendar.sessions(history[0].session, as_of.date())
        if calendar.close_at(session) <= as_of
    ]
    # 缺日、过期末值或盘内未完成记录都不能压缩成连续历史。
    if [record.session for record in history] != expected:
        # 数据恢复之前维持明确未知状态。
        return RegimeAssessment(
            as_of=as_of, state="UNKNOWN", evidence={"reason": "missing_or_stale_sessions"}
        )
    # 缺历史或被隔离的数据不能宣称为正常市场。
    if len(history) < 200 or any(record.quality != "good" for record in history):
        # 首版UNKNOWN同样只观察，不减少或增加仓位。
        return RegimeAssessment(
            as_of=as_of, state="UNKNOWN", evidence={"reason": "insufficient_or_bad_history"}
        )
    # 趋势在未穿越阈值时保持中性起点。
    trend = "NEUTRAL"
    # 波动状态初始为普通，进入高波动仍须三日证据。
    high_vol = False
    # 保存待确认趋势及连续次数。
    pending_trend = "NEUTRAL"
    # 未发生待确认方向时计数为零。
    trend_count = 0
    # 波动切换亦独立确认。
    volatility_count = 0
    # 末日指标用于解释输出。
    ratio = 1.0
    # 末日波动默认只在完整窗口后输出。
    volatility = 0.0
    # 至少200个观测后逐日计算，不使用全样本拟合。
    for index in range(199, len(history)):
        # 当前窗口只含当前及更早记录。
        prices = [record.total_return_close for record in history[index - 199 : index + 1]]
        # 基准相对SMA200比值无量纲。
        ratio = prices[-1] / mean(prices)
        # 超过正负1%才提出新趋势，其余保留当前状态。
        candidate = "UP" if ratio > 1.01 else "DOWN" if ratio < 0.99 else trend
        # 当前状态无需重复确认。
        if candidate == trend:
            # 滞回区间内中断待确认切换。
            trend_count = 0
        # 相同候选延续则累积。
        elif candidate == pending_trend:
            # 需要连续三个观测日。
            trend_count += 1
        # 新候选重新开始计数。
        else:
            # 记录新方向。
            pending_trend = candidate
            # 首日证据计为一。
            trend_count = 1
        # 达到明确确认窗口才改变趋势。
        if trend_count >= 3:
            # 本日开始输出已确认方向。
            trend = candidate
            # 完成一次状态转换后清零。
            trend_count = 0
        # 最近61价格形成60简单收益。
        returns = [
            current / previous - 1
            for previous, current in zip(prices[-61:-1], prices[-60:], strict=True)
        ]
        # 波动同因子采用样本标准差及252日年化。
        volatility = stdev(returns) * math.sqrt(252)
        # 高波动进入25%、退出20%，中间维持原状态。
        candidate_high = volatility > 0.25 if not high_vol else volatility >= 0.20
        # 与当前状态不同才需要连续确认。
        volatility_count = volatility_count + 1 if candidate_high != high_vol else 0
        # 三个连续观测满足切换条件。
        if volatility_count >= 3:
            # 切换波动标志，不联动组合。
            high_vol = candidate_high
            # 切换完成后重置计数。
            volatility_count = 0
    # 输出两个正交观察维度的组合标签。
    state = f"{trend}_{'HIGH_VOL' if high_vol else 'NORMAL_VOL'}"
    # 记录阈值、确认以及只观察语义所需证据。
    return RegimeAssessment(
        as_of=as_of,
        state=state,
        evidence={
            "price_to_sma200": ratio,
            "annualized_volatility": volatility,
            "confirmation_sessions": 3.0,
            "trend_pending": float(trend_count),
            "volatility_pending": float(volatility_count),
            "mode": "observation_only",
        },
    )
