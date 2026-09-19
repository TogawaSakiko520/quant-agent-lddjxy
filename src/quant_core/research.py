"""隔离的因子研究统计；未来标签只留在研究层，不向评分或订单提供输入。"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Self

import pandas as pd
from pydantic import Field, model_validator

from quant_core.contracts import (
    Contract,
    ContractError,
    FactorValue,
    MarketDataRecord,
    TradingCalendar,
    verify_record,
)


class ResearchObservation(Contract):
    """一个证券决策及其事后收益标签；仅供隔离研究，不含交易授权或副作用。"""

    # 稳定证券身份保证同日横截面可分组。
    security_id: str
    # 特征已经可知的UTC决策时刻。
    decision_time: datetime
    # 标签从严格晚于决策的可交易开盘开始。
    label_start: datetime
    # 标签截止至持有期最后交易日收盘；默认第 5 日，包含该收盘时刻。
    label_end: datetime
    # 已冻结因子原值，不能由未来标签反向拟合。
    factor_values: dict[str, float]
    # 无量纲研究收益，非FakeBroker策略业绩。
    forward_return: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_label(self) -> Self:
        """拒绝早于决策的标签区间、空因子和非有限因子值；合法时返回自身。"""
        # 标签区间必须排在决策之后。
        if self.label_start <= self.decision_time or self.label_end < self.label_start:
            raise ValueError("研究标签区间必须严格晚于决策")
        if not self.factor_values or any(
            not math.isfinite(value) for value in self.factor_values.values()
        ):
            raise ValueError("研究因子必须非空且有限")
        return self


class ResearchFold(Contract):
    """保存滚动窗口的样本索引；同日分组与跨标签清理由 rolling_splits 完成。

    本对象承载划分结果，不独立校验索引是否重叠或是否仍含跨阶段标签。
    """

    fold_id: str
    # 12个日历月训练索引，不是按证券行数切分。
    train: list[int]
    # 随后3个月验证索引。
    validation: list[int]
    # 再后3个月测试索引。
    test: list[int]
    # 最后6个月永久保留，不能用于预处理或调参。
    holdout: list[int]
    # 清除的跨边界日期索引留证据。
    purged: list[int]
    boundaries: dict[str, str]


def label_observations(
    factors: list[FactorValue],
    records: list[MarketDataRecord],
    calendar: TradingCalendar,
    horizon: int = 5,
) -> list[ResearchObservation]:
    """为冻结因子附加下一次开盘起、持有 horizon 个交易日的总回报标签。

    horizon 默认为 5；首个开盘必须严格晚于决策时间，终点包含第 horizon 日收盘。
    标签行情可使用事后最终修订，不能把该价格索引传回特征或交易链。缺日、缺端点
    或尚未到期的样本跳过；封印/版本冲突、重复因子或非正持有期抛 ContractError。
    """
    if horizon < 1:
        raise ContractError("标签持有期必须为正")
    # prices 是 (证券 ID, 交易日)→完整行情记录，保留开盘/收盘/研究价供标签计算；
    # 与 factors 的同名价格索引不同，这里允许取事后最终修订，但不能交回特征计算。
    prices: dict[tuple[str, date], MarketDataRecord] = {}
    # 同版本冲突必须在研究中同样拒绝。
    versions: dict[tuple[str, date, int], str] = {}
    for record in records:
        # 研究标签的外部记录也必须封印可验证。
        verify_record(record)
        identity = (record.security_id, record.session, record.revision)
        if identity in versions and versions[identity] != record.content_hash:
            raise ContractError("研究标签行情版本冲突")
        versions[identity] = record.content_hash
        key = (record.security_id, record.session)
        # 标签最终版允许事后修订，但其用途限于评估。
        if key not in prices or record.revision > prices[key].revision:
            prices[key] = record
    # 最新修订质量不合格时不能退回好看的旧标签。
    prices = {key: record for key, record in prices.items() if record.quality == "good"}
    # features 是 {(证券 ID, 决策时刻): {因子 ID: 冻结原值}}；按同次决策合并有效因子。
    # 本函数不要求双因子齐全，共同样本由调用方筛选，不能据此认定每行都含两因子。
    features: dict[tuple[str, datetime], dict[str, float]] = {}
    # 保存每个冻结因子的身份，重复不能静默覆盖。
    seen_factors: set[tuple[str, datetime, str]] = set()
    for factor in factors:
        identity_factor = (factor.security_id, factor.decision_time, factor.factor_id)
        if identity_factor in seen_factors:
            raise ContractError("重复研究因子")
        seen_factors.add(identity_factor)
        # 只有真实有效的原值进入研究。
        if factor.value is not None and factor.reason is None:
            # setdefault 保留该证券该决策已收集的其他因子，只在首次出现时创建内层字典。
            features.setdefault((factor.security_id, factor.decision_time), {})[
                factor.factor_id
            ] = factor.value
    if not prices:
        return []
    # 标签起点由决策时刻和日历确定，不能让缺失的首行情偷偷推迟入口。
    first = min(
        [record.session for record in prices.values()]
        + [decision_time.date() for _, decision_time in features]
    )
    last = max(record.session for record in prices.values())
    sessions = calendar.sessions(first, last)
    observations: list[ResearchObservation] = []
    # 先按决策时刻、再按稳定证券 ID 排序输出，让 observations 的列表索引可复现；
    # rolling_splits 返回的是这些索引，不能在切分后随意重排原列表。
    for (security_id, decision_time), factor_values in sorted(
        features.items(), key=lambda item: (item[0][1], item[0][0])
    ):
        # future 是决策之后按日历排列的可交易日；先定真正下一开盘，再按日期取行情。
        future = [session for session in sessions if calendar.open_at(session) > decision_time]
        # 未到期的标签不能按较短窗口填入。
        if len(future) < horizon:
            continue
        # 首次可交易开盘作为真实标签起点。
        entry = prices.get((security_id, future[0]))
        # 索引从零开始，所以第 horizon 日为 future[horizon-1]；[:horizon] 包含这一天。
        exit_record = prices.get((security_id, future[horizon - 1]))
        # 缺任何中间行情也不能宣称完整窗口。
        if (
            entry is None
            or exit_record is None
            or any((security_id, session) not in prices for session in future[:horizon])
        ):
            continue
        # 用同日原始开收比将总回报收盘口径转换成研究开盘基值。
        entry_research_price = entry.total_return_close * entry.raw_open / entry.raw_close
        # 这是持有期研究标签，不是撮合器收益或净策略收益。
        forward_return = exit_record.total_return_close / entry_research_price - 1.0
        observations.append(
            ResearchObservation(
                security_id=security_id,
                decision_time=decision_time,
                label_start=calendar.open_at(future[0]),
                label_end=calendar.close_at(future[horizon - 1]),
                factor_values=factor_values,
                forward_return=forward_return,
            )
        )
    return observations


def rolling_splits(observations: list[ResearchObservation]) -> list[ResearchFold]:
    """按 12/3/3 日历月建立训练、验证、测试索引，每轮前移 3 个月。

    最后 6 个月作为最终保留集。开发窗口内，标签结束时间触及下一阶段边界时，
    清除该决策日的全部证券（purge），避免同日样本或未来标签跨组泄漏。
    返回原 observations 列表的索引；历史不足或任一开发分组为空时不产生该窗口。
    """
    if not observations:
        return []
    # 月边界以UTC时间统一表示。
    earliest = min(item.decision_time for item in observations)
    # 最晚月份的下月1日作为样本区间右开端。
    latest = max(item.decision_time for item in observations)
    # normalize 去掉当日时分秒，再设为月首：边界按整个月解释，而不是首条样本的日/时刻。
    start = pd.Timestamp(earliest).normalize().replace(day=1)
    end = pd.Timestamp(latest).normalize().replace(day=1) + pd.DateOffset(months=1)
    # 最后6个日历月从训练、验证、普通测试中永久剥离。
    holdout_start = end - pd.DateOffset(months=6)
    holdout = [
        index
        for index, item in enumerate(observations)
        if pd.Timestamp(item.decision_time) >= holdout_start
    ]
    # day_end 是 UTC 决策日期→该日全部证券最晚的标签终点，用它决定是否整日清除。
    day_end: dict[date, datetime] = {}
    for item in observations:
        day = item.decision_time.date()
        # 当天首条样本以自身终点起步；之后只向更晚终点扩展，不让短标签掩盖长标签。
        day_end[day] = max(day_end.get(day, item.label_end), item.label_end)
    folds: list[ResearchFold] = []
    # 18个月开发窗口不能侵入6个月保留集。
    while start + pd.DateOffset(months=18) <= holdout_start:
        # 训练覆盖12个日历月。
        validation_start = start + pd.DateOffset(months=12)
        # 验证覆盖接下来3个月。
        test_start = validation_start + pd.DateOffset(months=3)
        # 测试再覆盖3个月。
        test_end = test_start + pd.DateOffset(months=3)
        # groups 按训练/验证/测试顺序保存原 observations 的索引；purged 另存跨界索引，
        # 因而“删出训练窗口”不等于销毁原始研究样本，也能在报告中解释剔除原因。
        purged: list[int] = []
        groups: list[list[int]] = []
        # 每段右边界都是其标签不能触及的下一阶段信息起点。
        for left, right in (
            (start, validation_start),
            (validation_start, test_start),
            (test_start, test_end),
        ):
            # 先分决策日期，而非对证券行数取gap。
            candidates = [
                index
                for index, item in enumerate(observations)
                if left <= pd.Timestamp(item.decision_time) < right
            ]
            # 标签触及后续阶段边界时清除同一天全部证券。
            clean = [
                index
                for index in candidates
                if pd.Timestamp(day_end[observations[index].decision_time.date()]) < right
            ]
            purged.extend(index for index in candidates if index not in clean)
            groups.append(clean)
        # 不足样本的月份不能当成有效训练/验证/测试窗口。
        if all(groups):
            folds.append(
                ResearchFold(
                    fold_id=f"rolling-{len(folds) + 1:03d}",
                    train=groups[0],
                    validation=groups[1],
                    test=groups[2],
                    holdout=holdout,
                    purged=sorted(purged),
                    boundaries={
                        "train_start": start.isoformat(),
                        "validation_start": validation_start.isoformat(),
                        "test_start": test_start.isoformat(),
                        "test_end": test_end.isoformat(),
                        "holdout_start": holdout_start.isoformat(),
                    },
                )
            )
        # 固定3个月前移，禁止按结果挑时间窗口。
        start += pd.DateOffset(months=3)
    # 无完整窗口时返回空并由报告标记不足，不放宽切分规则。
    return folds


def evaluate_research(
    observations: list[ResearchObservation],
    *,
    expected_observations: int | None = None,
) -> dict[str, Any]:
    """用事后标签描述因子覆盖、逐日秩相关和五分组收益，不计算策略净业绩。

    Rank IC 是同日因子排名与后续收益排名的相关系数；不足两只或常数横截面
    返回 None，均值只取有定义日期。expected_observations 应为筛选前候选数，
    省略时使用有效样本数；分母小于有效样本或证券决策重复时抛 ContractError。
    五分组收益为无量纲简单平均，空组保持 None，不把缺失补成零收益。
    """
    # 覆盖率分母由调用者的原始候选计数提供。
    expected = len(observations) if expected_observations is None else expected_observations
    if expected < len(observations):
        raise ContractError("预期样本数不能小于有效研究样本数")
    # 同一证券同一决策不允许重复计入统计。
    keys = [(item.security_id, item.decision_time) for item in observations]
    if len(keys) != len(set(keys)):
        raise ContractError("研究样本重复")
    factor_ids = sorted({factor_id for item in observations for factor_id in item.factor_values})
    # by_day 是 UTC 决策日期→该日各证券样本列表，横截面指同一日不同证券的比较。
    by_day: dict[date, list[ResearchObservation]] = {}
    for item in observations:
        # 因子日频评估使用UTC决策日期，交易调度统一盘后UTC。
        # 首次遇到日期才建列表，随后追加同日其他证券，不把每日横截面互相覆盖。
        by_day.setdefault(item.decision_time.date(), []).append(item)
    # statistics 是因子 ID→该因子的日秩相关、日分组收益和全样本覆盖率，供研究摘要使用。
    statistics: dict[str, Any] = {}
    for factor_id in factor_ids:
        # 日IC空值需保留，不能伪装成0相关。
        daily_ic: dict[str, float | None] = {}
        # grouped 是日期字符串→五个分组的平均收益，列表位置从低因子组排到高因子组。
        grouped: dict[str, list[float | None]] = {}
        for day, items in sorted(by_day.items()):
            valid = [item for item in items if factor_id in item.factor_values]
            # 此处 values 是当日这个因子的原值 Series，labels 是同一 valid 顺序的未来
            # 简单收益 Series；默认下标一一对应，排名相关才不会把不同证券错配。
            values = pd.Series([item.factor_values[factor_id] for item in valid], dtype=float)
            labels = pd.Series([item.forward_return for item in valid], dtype=float)
            # 样本不足或常数横截面时秩相关没有定义。
            correlation = (
                None
                if len(valid) < 2 or values.nunique() < 2 or labels.nunique() < 2
                else float(values.rank(method="average").corr(labels.rank(method="average")))
            )
            daily_ic[day.isoformat()] = correlation
            # 因子值升序，稳定证券ID固定并列分组。
            ordered = sorted(
                valid, key=lambda item: (item.factor_values[factor_id], item.security_id)
            )
            # buckets 是五个“样本收益列表”；index*5//N 将从零开始的排名映射到组号0..4。
            # 按证券个数分组而非因子数值区间；少于5只时部分组为空，同值也按 ID 分组。
            buckets = (
                [
                    [
                        item.forward_return
                        for index, item in enumerate(ordered)
                        if index * 5 // len(ordered) == group
                    ]
                    for group in range(5)
                ]
                if ordered
                else [[] for _ in range(5)]
            )
            # 每组为空时不补零收益。
            grouped[day.isoformat()] = [
                sum(bucket) / len(bucket) if bucket else None for bucket in buckets
            ]
        # 仅有定义的IC参与均值。
        valid_ic = [value for value in daily_ic.values() if value is not None]
        statistics[factor_id] = {
            "rank_ic_by_date": daily_ic,
            "mean_rank_ic": sum(valid_ic) / len(valid_ic) if valid_ic else None,
            "quintile_returns_by_date": grouped,
            "coverage": sum(factor_id in item.factor_values for item in observations) / expected
            if expected
            else 0.0,
        }
    # 因子相关同样采用逐日横截面秩相关。
    correlations: dict[str, float | None] = {}
    for index, left in enumerate(factor_ids):
        # 只取当前因子右侧的后续 ID，避免自己与自己相关、以及 A:B/B:A 重复计算。
        for right in factor_ids[index + 1 :]:
            daily: list[float] = []
            # 不把不同日期的水平差异当成横截面关系。
            for items in by_day.values():
                # 每对因子用共同有效股票样本。
                paired = [
                    item
                    for item in items
                    if left in item.factor_values and right in item.factor_values
                ]
                # a/b 是同一 paired 顺序下的两因子原值；先转为平均并列名次，再算相关。
                a = pd.Series([item.factor_values[left] for item in paired], dtype=float)
                b = pd.Series([item.factor_values[right] for item in paired], dtype=float)
                # 常数列或不足样本不构造虚假相关。
                if len(paired) >= 2 and a.nunique() >= 2 and b.nunique() >= 2:
                    # 使用平均并列秩进行Spearman定义的计算。
                    daily.append(float(a.rank(method="average").corr(b.rank(method="average"))))
            # 无有效日保留缺失，不报告零。
            correlations[f"{left}:{right}"] = sum(daily) / len(daily) if daily else None
    # 返回可序列化描述统计和不可省略的限制说明。
    return {
        "observations": len(observations),
        "expected_observations": expected,
        "coverage": len(observations) / expected if expected else 0.0,
        "factors": statistics,
        "factor_rank_correlations": correlations,
        "limitations": [
            "合成标签仅验证研究流程，不证明超额收益",
            "五日研究收益不等于正式成本后策略业绩",
            "未训练模型或优化权重，全部实验窗口均须留存",
        ],
    }
