"""趋势及波动观察器；在本次可知的历史版本上重放状态确认，不改变组合预算。"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import mean, stdev

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
    """从单一基准历史计算趋势和波动观察状态，供报告解释市场环境。

    as_of 是 UTC 观察时刻；先取此时已知最高修订，再按事件日重放 200 日趋势
    与 60 日波动的三日确认。重放并非对每个历史日重建其当时可知的数据版本。
    日历缺失、历史不足、缺日或质量失败返回 UNKNOWN；混合证券、封印/版本冲突
    抛 ContractError。所有输出仅观察，不调整组合风险预算。
    """
    # 未来版本完全不参与本次观察。
    visible = [
        record for record in records if record.available_at <= as_of and record.event_time <= as_of
    ]
    # 基准序列不能混合不同证券。
    if len({record.security_id for record in visible}) > 1:
        raise ContractError("市场状态要求单一基准")
    # selected 是交易日→as_of 时已知最高修订；versions 是 (交易日, 修订号)→内容哈希，
    # 两个索引分别负责“选版本”和“查同版本冲突”，不能只用后来的记录覆盖先来的记录。
    selected: dict[object, MarketDataRecord] = {}
    # 同一版本冲突不能被最新值覆盖。
    versions: dict[tuple[object, int], str] = {}
    for record in visible:
        # 基准也执行来源封印核验。
        verify_record(record)
        key = (record.session, record.revision)
        if key in versions and versions[key] != record.content_hash:
            raise ContractError("基准版本冲突")
        versions[key] = record.content_hash
        if record.session not in selected or record.revision > selected[record.session].revision:
            selected[record.session] = record
    history = sorted(selected.values(), key=lambda record: record.session)
    # 没有市场日历无法证明连续交易日与末日新鲜度。
    if calendar is None or not history:
        return RegimeAssessment(
            as_of=as_of, state="UNKNOWN", evidence={"reason": "missing_calendar_or_history"}
        )
    # 状态确认要求从首日至本次观察的最后收盘日连续覆盖，不能用观测条数代替交易日。
    expected = [
        session
        for session in calendar.sessions(history[0].session, as_of.date())
        if calendar.close_at(session) <= as_of
    ]
    # 缺日、过期末值或盘内未完成记录都不能压缩成连续历史。
    if [record.session for record in history] != expected:
        return RegimeAssessment(
            as_of=as_of, state="UNKNOWN", evidence={"reason": "missing_or_stale_sessions"}
        )
    # 缺历史或被隔离的数据不能宣称为正常市场。
    if len(history) < 200 or any(record.quality != "good" for record in history):
        return RegimeAssessment(
            as_of=as_of, state="UNKNOWN", evidence={"reason": "insufficient_or_bad_history"}
        )
    # 趋势在未穿越阈值时保持中性起点。
    trend = "NEUTRAL"
    # 波动状态初始为普通，进入高波动仍须三日证据。
    high_vol = False
    # trend 是已经确认的状态，pending_trend 是尚待连续三日确认的新方向。
    # 两个 count 均是连续观测日数，循环中遇到不满足条件会重置，而非累计历史总次数。
    pending_trend = "NEUTRAL"
    trend_count = 0
    volatility_count = 0
    # ratio 和 volatility 在每个窗口被更新，退出循环后只将最后一天指标放入 evidence。
    ratio = 1.0
    volatility = 0.0
    # 至少200个观测后逐日计算，不使用全样本拟合。
    for index in range(199, len(history)):
        # index 从199开始，切片 [index-199:index+1] 包含当前日及前199日，共200个研究价；
        # 窗口事件日不超过当前日，版本选择已在 as_of 时刻统一完成。
        prices = [record.total_return_close for record in history[index - 199 : index + 1]]
        # 末日价除以200日简单均价（SMA200）得到无量纲比值，1.01 表示高于均价1%。
        ratio = prices[-1] / mean(prices)
        # 超过正负1%才提出新趋势，其余保留当前状态。
        candidate = "UP" if ratio > 1.01 else "DOWN" if ratio < 0.99 else trend
        if candidate == trend:
            # 滞回区间内中断待确认切换。
            trend_count = 0
        # 相同候选延续则累积。
        elif candidate == pending_trend:
            trend_count += 1
        # 新候选重新开始计数。
        else:
            pending_trend = candidate
            trend_count = 1
        # 达到明确确认窗口才改变趋势。
        if trend_count >= 3:
            trend = candidate
            trend_count = 0
        # 两个长度均为60的切片错开一天，zip 配成 (前日价, 当日价)；strict=True
        # 要求长度相同，[-61:-1] 不含末日，[-60:] 包含末日，正好形成最近60个收益。
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
            high_vol = candidate_high
            volatility_count = 0
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
