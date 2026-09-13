"""横截面百分位与固定等权评分；排名不是收益预测，不访问订单或券商。"""

# 排名只用确定的排序与共享契约。
from quant_core.contracts import (
    ContractError,
    DataSnapshot,
    FactorValue,
    Score,
    SignalSet,
    canonical_hash,
)

# 因子稳定定义决定共同评分要求。
from quant_core.factors import DEFINITIONS

# 资格规则只有一个实现。
from quant_core.universe import qualified_universe


def score_factors(snapshot: DataSnapshot, factors: list[FactorValue]) -> SignalSet:
    """在共同有效样本排名，平均并列名次；返回排序信号，输入冲突抛错，无副作用。"""
    # 合格主表用于行业映射与确定候选。
    universe = {security.security_id: security for security in qualified_universe(snapshot)}
    # 因子 ID 顺序固定，不允许外来因子改变权重。
    required = [definition.factor_id for definition in DEFINITIONS]
    # 记录每只证券的完整有效因子。
    values: dict[str, dict[str, float]] = {}
    # 不合格证券也要在解释中可见。
    excluded = {
        security.security_id: "not_eligible"
        for security in snapshot.securities
        if security.asset_type == "common_stock" and security.security_id not in universe
    }
    # 重复输入即使同值也不能意外提高权重。
    seen: set[tuple[str, str]] = set()
    # 校验时间与版本后才接受因子。
    for factor in factors:
        # 因子不能来自其他决策或快照。
        if (
            factor.snapshot_id != snapshot.snapshot_id
            or factor.decision_time != snapshot.decision_time
        ):
            # 拒绝交叉批次混入而非静默重贴标签。
            raise ContractError("因子快照或时点不一致")
        # 本策略只认识固定的两个版本化因子。
        if factor.factor_id not in required or factor.factor_version != "1.0.0":
            # 未批准因子不能改变评分定义。
            raise ContractError("未知因子或版本")
        # 唯一键由稳定证券和因子组成。
        key = (factor.security_id, factor.factor_id)
        # 同键重复属于边界错误。
        if key in seen:
            # 避免列表覆盖掩盖来源冲突。
            raise ContractError("重复因子结果")
        # 标记该键已消费。
        seen.add(key)
        # 仅接受资格内有效值。
        if factor.security_id in universe and factor.value is not None and factor.reason is None:
            # 收集后统一做共同样本排名。
            values.setdefault(factor.security_id, {})[factor.factor_id] = factor.value
        # 无效因子解释仍需留存。
        elif factor.security_id in universe:
            # 不补零或沿用上一期因子。
            excluded[factor.security_id] = factor.reason or "missing_factor"
    # 共同样本确保不同股票得分可比较。
    eligible = sorted(
        security_id for security_id in universe if len(values.get(security_id, {})) == len(required)
    )
    # 无输入因子的证券也记入缺失。
    for security_id in universe:
        # 缺任何因子就退出两个因子的排名样本。
        if security_id not in eligible:
            # 保留更详细的现有原因。
            excluded.setdefault(security_id, "missing_factor")
    # 一个样本无法提供横截面对比。
    if len(eligible) < 2:
        # 记录样本不足而非给予虚假的满分。
        excluded.update({security_id: "insufficient_cross_section" for security_id in eligible})
        # 返回空信号供组合保留现金。
        eligible = []
    # 每个证券保存两个百分位。
    components: dict[str, dict[str, float]] = {security_id: {} for security_id in eligible}
    # 按固定公式分别排序。
    for factor_id in required:
        # 值升序使越大对应越高百分位，稳定 ID 仅固定排序。
        ordered = sorted(
            eligible, key=lambda security_id: (values[security_id][factor_id], security_id)
        )
        # 逐组处理完全相同的值。
        index = 0
        # 不对近似浮点值擅自定义新的并列容差。
        while index < len(ordered):
            # 找到当前并列组的右开边界。
            end = index + 1
            # 精确并列采用平均名次。
            while (
                end < len(ordered)
                and values[ordered[end]][factor_id] == values[ordered[index]][factor_id]
            ):
                # 继续扩展当前并列组。
                end += 1
            # 零基平均名次除以 N-1，最低0最高1。
            percentile = (index + end - 1) / 2 / (len(ordered) - 1)
            # 给每个并列证券相同分数。
            for security_id in ordered[index:end]:
                # 最终并列才由证券 ID 决定入选顺序。
                components[security_id][factor_id] = percentile
            # 继续下一组。
            index = end
    # 两因子严格等权，不估计优化权重。
    scores = [
        Score(
            security_id=security_id,
            sector=universe[security_id].sector,
            components=components[security_id],
            value=sum(components[security_id].values()) / 2,
        )
        for security_id in eligible
    ]
    # 排序在所有运行中确定。
    scores.sort(key=lambda score: (-score.value, score.security_id))
    # 决策身份依赖固定输入和固定策略版本，不依赖运行UUID。
    decision_id = canonical_hash(
        {"snapshot_id": snapshot.snapshot_id, "strategy": "weekly-two-factor-1.0.0"}
    )
    # 策略只返回信号，不生成外部操作。
    return SignalSet(
        decision_id=decision_id,
        decision_time=snapshot.decision_time,
        snapshot_id=snapshot.snapshot_id,
        scores=scores,
        excluded=excluded,
    )
