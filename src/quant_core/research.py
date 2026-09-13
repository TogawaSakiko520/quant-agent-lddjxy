"""隔离的因子研究统计；未来标签只留在研究层，不向评分或订单提供输入。"""

# 注解延迟解析保持边界类型明确。
from __future__ import annotations

# 有限值核验防止异常标签污染统计。
import math

# 时间和日期用于真实事件区间，而非按行隔离。
from datetime import date, datetime

# 自校验返回同一模型类型。
from typing import Any, Self

# 按日期偏移和横截面秩相关使用固定分析依赖。
import pandas as pd

# 研究本地契约同样执行边界验证。
from pydantic import Field, model_validator

# 研究对象不进入交易核心输入契约。
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
    # 标签截止至第5个交易日收盘，包含事件时间边界。
    label_end: datetime
    # 已冻结因子原值，不能由未来标签反向拟合。
    factor_values: dict[str, float]
    # 无量纲研究收益，非FakeBroker策略业绩。
    forward_return: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_label(self) -> Self:
        """校验标签严格晚于特征且数值有限；返回自身，矛盾抛错，无副作用。"""
        # 标签区间必须排在决策之后。
        if self.label_start <= self.decision_time or self.label_end < self.label_start:
            # 不允许当日已经结束的价格作为未来入场标签。
            raise ValueError("研究标签区间必须严格晚于决策")
        # 嵌套因子值也需有限数检查。
        if not self.factor_values or any(
            not math.isfinite(value) for value in self.factor_values.values()
        ):
            # 空特征或NaN不能用于横截面统计。
            raise ValueError("研究因子必须非空且有限")
        # 合法对象保持不变。
        return self


class ResearchFold(Contract):
    """按真实日期滚动划分的样本索引；同日同组，跨标签区间已清除。"""

    # 每个滚动窗口有稳定身份。
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
    # 记录每个日期边界供报告和审核。
    boundaries: dict[str, str]


def label_observations(
    factors: list[FactorValue],
    records: list[MarketDataRecord],
    calendar: TradingCalendar,
    horizon: int = 5,
) -> list[ResearchObservation]:
    """由冻结特征和事后完整行情建立下一开盘至第5日收盘标签；缺终点跳过，无副作用。"""
    # 标签持有期必须为正交易日数。
    if horizon < 1:
        # 无效窗口不能悄悄改为默认值。
        raise ContractError("标签持有期必须为正")
    # 事后标签可取最终修订，但绝不把此索引交回特征计算。
    prices: dict[tuple[str, date], MarketDataRecord] = {}
    # 同版本冲突必须在研究中同样拒绝。
    versions: dict[tuple[str, date, int], str] = {}
    # 按修订号选择标签评估口径。
    for record in records:
        # 研究标签的外部记录也必须封印可验证。
        verify_record(record)
        # 版本身份不受输入顺序影响。
        identity = (record.security_id, record.session, record.revision)
        # 不允许同一版本的标签出现多个值。
        if identity in versions and versions[identity] != record.content_hash:
            # 标签污染同样属于契约错误。
            raise ContractError("研究标签行情版本冲突")
        # 保存已见版本的内容。
        versions[identity] = record.content_hash
        # 同一证券交易日只留最高修订。
        key = (record.security_id, record.session)
        # 标签最终版允许事后修订，但其用途限于评估。
        if key not in prices or record.revision > prices[key].revision:
            # 索引只存在于研究函数局部。
            prices[key] = record
    # 最新修订质量不合格时不能退回好看的旧标签。
    prices = {key: record for key, record in prices.items() if record.quality == "good"}
    # 将同一证券同一决策的两个因子合并。
    features: dict[tuple[str, datetime], dict[str, float]] = {}
    # 保存每个冻结因子的身份，重复不能静默覆盖。
    seen_factors: set[tuple[str, datetime, str]] = set()
    # 特征输入必须已经绑定历史时点。
    for factor in factors:
        # 同证券同决策同因子只有一个批准结果。
        identity_factor = (factor.security_id, factor.decision_time, factor.factor_id)
        # 两批结果冲突不能由拼接顺序决定。
        if identity_factor in seen_factors:
            # 要求调用者消除重复批次。
            raise ContractError("重复研究因子")
        # 记录当前因子已经出现。
        seen_factors.add(identity_factor)
        # 只有真实有效的原值进入研究。
        if factor.value is not None and factor.reason is None:
            # 保持因子ID，不在此重算或优化评分。
            features.setdefault((factor.security_id, factor.decision_time), {})[
                factor.factor_id
            ] = factor.value
    # 空行情自然得到无可用标签。
    if not prices:
        # 不伪造收益序列。
        return []
    # 标签起点由决策时刻和日历确定，不能让缺失的首行情偷偷推迟入口。
    first = min(
        [record.session for record in prices.values()]
        + [decision_time.date() for _, decision_time in features]
    )
    # 研究终点不超过实际标签数据。
    last = max(record.session for record in prices.values())
    # 所有交易日用同一市场日历。
    sessions = calendar.sessions(first, last)
    # 研究样本返回顺序固定。
    observations: list[ResearchObservation] = []
    # 同日各证券拥有同一标签交易区间。
    for (security_id, decision_time), factor_values in sorted(
        features.items(), key=lambda item: (item[0][1], item[0][0])
    ):
        # 只选择严格晚于决策的首次开盘。
        future = [session for session in sessions if calendar.open_at(session) > decision_time]
        # 未到期的标签不能按较短窗口填入。
        if len(future) < horizon:
            # 末尾样本留待未来数据完成。
            continue
        # 首次可交易开盘作为真实标签起点。
        entry = prices.get((security_id, future[0]))
        # 第horizon个交易日收盘作为终点。
        exit_record = prices.get((security_id, future[horizon - 1]))
        # 缺任何中间行情也不能宣称完整窗口。
        if (
            entry is None
            or exit_record is None
            or any((security_id, session) not in prices for session in future[:horizon])
        ):
            # 保留覆盖缺口而非缩短窗口。
            continue
        # 用同日原始开收比将总回报收盘口径转换成研究开盘基值。
        entry_research_price = entry.total_return_close * entry.raw_open / entry.raw_close
        # 这是持有期研究标签，不是撮合器收益或净策略收益。
        forward_return = exit_record.total_return_close / entry_research_price - 1.0
        # 保存时间区间用于后续purge与解释。
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
    # 返回隔离研究对象，没有向交易链回写的接口。
    return observations


def rolling_splits(observations: list[ResearchObservation]) -> list[ResearchFold]:
    """按12/3/3个月滚动、3个月步进，末6个月保留；按标签时间清除整日，无副作用。"""
    # 无样本不能制造空窗口通过研究验收。
    if not observations:
        # 调用方应明确报告研究历史不足。
        return []
    # 月边界以UTC时间统一表示。
    earliest = min(item.decision_time for item in observations)
    # 最晚月份的下月1日作为样本区间右开端。
    latest = max(item.decision_time for item in observations)
    # 首月起点不把同月股票拆成不同集合。
    start = pd.Timestamp(earliest).normalize().replace(day=1)
    # 末月完整日历范围纳入保留期。
    end = pd.Timestamp(latest).normalize().replace(day=1) + pd.DateOffset(months=1)
    # 最后6个日历月从训练、验证、普通测试中永久剥离。
    holdout_start = end - pd.DateOffset(months=6)
    # 保留样本索引供独立最终评估。
    holdout = [
        index
        for index, item in enumerate(observations)
        if pd.Timestamp(item.decision_time) >= holdout_start
    ]
    # 同一天的标签最大结束时间决定整天是否需要清除。
    day_end: dict[date, datetime] = {}
    # 按日期维护最保守的事件终点。
    for item in observations:
        # 多证券不同到期也不能让同日被拆组。
        day = item.decision_time.date()
        # 同日取最晚标签终点。
        day_end[day] = max(day_end.get(day, item.label_end), item.label_end)
    # 所有窗口都完整保存，不只挑最好一组。
    folds: list[ResearchFold] = []
    # 18个月开发窗口不能侵入6个月保留集。
    while start + pd.DateOffset(months=18) <= holdout_start:
        # 训练覆盖12个日历月。
        validation_start = start + pd.DateOffset(months=12)
        # 验证覆盖接下来3个月。
        test_start = validation_start + pd.DateOffset(months=3)
        # 测试再覆盖3个月。
        test_end = test_start + pd.DateOffset(months=3)
        # 保存按真实时间清除的样本证据。
        purged: list[int] = []
        # 三组分别按日期收集索引。
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
            # 留下明确的删除依据，测试不依赖被测函数生成期望。
            purged.extend(index for index in candidates if index not in clean)
            # 本组只接纳没有跨界的日期。
            groups.append(clean)
        # 不足样本的月份不能当成有效训练/验证/测试窗口。
        if all(groups):
            # 保存完整窗口时间与四组索引。
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
    """汇总全样本覆盖、逐日Rank IC、五分组和因子相关性；不生成正式策略业绩，无副作用。"""
    # 覆盖率分母由调用者的原始候选计数提供。
    expected = len(observations) if expected_observations is None else expected_observations
    # 不能用较小分母隐藏缺失。
    if expected < len(observations):
        # 分母配置错误属于研究契约错误。
        raise ContractError("预期样本数不能小于有效研究样本数")
    # 同一证券同一决策不允许重复计入统计。
    keys = [(item.security_id, item.decision_time) for item in observations]
    # 重复样本会扭曲横截面相关与覆盖。
    if len(keys) != len(set(keys)):
        # 要求修复来源而非平均重复行。
        raise ContractError("研究样本重复")
    # 从有效样本确定因子集合，不修改交易策略。
    factor_ids = sorted({factor_id for item in observations for factor_id in item.factor_values})
    # 按决策日期分组，确保同日股票同时评估。
    by_day: dict[date, list[ResearchObservation]] = {}
    # 行序不能决定训练/验证或IC。
    for item in observations:
        # 因子日频评估使用UTC决策日期，交易调度统一盘后UTC。
        by_day.setdefault(item.decision_time.date(), []).append(item)
    # 为每个因子记录全部日IC与五分组收益。
    statistics: dict[str, Any] = {}
    # 独立计算每个原始方向一致因子的描述统计。
    for factor_id in factor_ids:
        # 日IC空值需保留，不能伪装成0相关。
        daily_ic: dict[str, float | None] = {}
        # 分组收益记录每一天而非只显示最好时期。
        grouped: dict[str, list[float | None]] = {}
        # 固定时间排序便于复现。
        for day, items in sorted(by_day.items()):
            # 仅该因子有效的样本参与当日覆盖。
            valid = [item for item in items if factor_id in item.factor_values]
            # 值与标签必须按同一证券顺序配对。
            values = pd.Series([item.factor_values[factor_id] for item in valid], dtype=float)
            # 未来收益只用于评价，绝不进入features。
            labels = pd.Series([item.forward_return for item in valid], dtype=float)
            # 样本不足或常数横截面时秩相关没有定义。
            correlation = (
                None
                if len(valid) < 2 or values.nunique() < 2 or labels.nunique() < 2
                else float(values.rank(method="average").corr(labels.rank(method="average")))
            )
            # 按日期留存原始日IC。
            daily_ic[day.isoformat()] = correlation
            # 因子值升序，稳定证券ID固定并列分组。
            ordered = sorted(
                valid, key=lambda item: (item.factor_values[factor_id], item.security_id)
            )
            # 五组按离散序号划分，少于5只时相应组留空。
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
        # 所有观察都保留，不能以统计最优为筛选条件。
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
    # 每对因子只计算一次，避免重复权重。
    for index, left in enumerate(factor_ids):
        # 与右侧后续因子组合。
        for right in factor_ids[index + 1 :]:
            # 保存每个日期的独立相关。
            daily: list[float] = []
            # 不把不同日期的水平差异当成横截面关系。
            for items in by_day.values():
                # 每对因子用共同有效股票样本。
                paired = [
                    item
                    for item in items
                    if left in item.factor_values and right in item.factor_values
                ]
                # 数值顺序共享同一批证券。
                a = pd.Series([item.factor_values[left] for item in paired], dtype=float)
                # 第二个因子使用相同排序。
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
