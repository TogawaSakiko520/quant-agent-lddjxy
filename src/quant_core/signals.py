"""横截面百分位与固定等权评分；排名不是收益预测，不访问订单或券商。"""

import math

from quant_core.contracts import (
    ContractError,
    DataSnapshot,
    FactorValue,
    Score,
    SignalSet,
    canonical_hash,
)
from quant_core.factors import DEFINITIONS, MA_DEFINITIONS
from quant_core.universe import qualified_universe


def score_factors(snapshot: DataSnapshot, factors: list[FactorValue]) -> SignalSet:
    """把同一快照的双因子原值转换为固定等权评分。

    任一因子缺失的证券退出两个因子的共同样本，少于两只时返回空评分及原因。
    百分位采用精确并列的平均名次；结果按综合分降序、稳定证券 ID 升序排列。
    快照/时点不符、未知因子版本或重复证券因子键抛 ContractError。
    """
    # universe 用稳定证券 ID 索引合格主表，既决定评分资格，也向输出 Score 提供行业。
    universe = {security.security_id: security for security in qualified_universe(snapshot)}
    # 原双因子仍要求真实行业；行业未知只由独立均线策略的保守风险口径处理。
    missing_sector = {identity for identity, security in universe.items() if not security.sector}
    universe = {
        identity: security
        for identity, security in universe.items()
        if identity not in missing_sector
    }
    # 因子 ID 顺序固定，不允许外来因子改变权重。
    required = [definition.factor_id for definition in DEFINITIONS]
    # values 的形状是 {证券 ID: {因子 ID: 原始因子值}}；数值仍是动量/低波动原值，
    # 尚未成为 [0,1] 百分位。输入空值或带排除原因的结果不会写入这个表。
    values: dict[str, dict[str, float]] = {}
    # 不合格证券也要在解释中可见。
    excluded = {
        security.security_id: "not_eligible"
        for security in snapshot.securities
        if security.asset_type == "common_stock" and security.security_id not in universe
    }
    excluded.update({identity: "missing_sector" for identity in missing_sector})
    # 重复输入即使同值也不能意外提高权重。
    seen: set[tuple[str, str]] = set()
    # 校验时间与版本后才接受因子。
    for factor in factors:
        # 因子不能来自其他决策或快照。
        if (
            factor.snapshot_id != snapshot.snapshot_id
            or factor.decision_time != snapshot.decision_time
        ):
            raise ContractError("因子快照或时点不一致")
        if factor.factor_id not in required or factor.factor_version != "1.0.0":
            raise ContractError("未知因子或版本")
        key = (factor.security_id, factor.factor_id)
        if key in seen:
            raise ContractError("重复因子结果")
        seen.add(key)
        if factor.security_id in universe and factor.value is not None and factor.reason is None:
            # 首个有效因子为该证券建立内层字典，后续因子加入同一字典；不是覆盖整只证券。
            values.setdefault(factor.security_id, {})[factor.factor_id] = factor.value
        elif factor.security_id in universe:
            # 不补零或沿用上一期因子。
            excluded[factor.security_id] = factor.reason or "missing_factor"
    # eligible 是两个因子都有效的证券 ID 列表，两次排名都使用它以保持样本可比。
    # get 的空字典表示该证券没有有效因子；先按 ID 排序让后续遍历顺序可复现。
    eligible = sorted(
        security_id for security_id in universe if len(values.get(security_id, {})) == len(required)
    )
    # 无输入因子的证券也记入缺失。
    for security_id in universe:
        if security_id not in eligible:
            # 只给尚无原因的证券补通用缺失原因，不覆盖上面保留的具体失败原因。
            excluded.setdefault(security_id, "missing_factor")
    # 一个样本无法提供横截面对比。
    if len(eligible) < 2:
        excluded.update({security_id: "insufficient_cross_section" for security_id in eligible})
        eligible = []
    # components 与 values 键结构相同，但内层存的是每个因子的 [0,1] 百分位；
    # 只为 eligible 建表，之后直接成为 Score.components，并用于计算等权综合分。
    components: dict[str, dict[str, float]] = {security_id: {} for security_id in eligible}
    for factor_id in required:
        # 值升序使越大对应越高百分位，稳定 ID 仅固定排序。
        ordered = sorted(
            eligible, key=lambda security_id: (values[security_id][factor_id], security_id)
        )
        index = 0
        # 不对近似浮点值擅自定义新的并列容差。
        while index < len(ordered):
            # index/end 是 ordered 中同值证券组的左闭右开下标，[index:end] 正好取整组。
            end = index + 1
            while (
                end < len(ordered)
                and values[ordered[end]][factor_id] == values[ordered[index]][factor_id]
            ):
                end += 1
            # 零基平均名次除以 N-1，范围为[0,1]；并列端点未必取0/1，全并列时均为0.5。
            percentile = (index + end - 1) / 2 / (len(ordered) - 1)
            for security_id in ordered[index:end]:
                components[security_id][factor_id] = percentile
            # 从组的右边界继续，既不重复计分，也不跳过下一组首个证券。
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
    # 负综合分使高分排在前；同分按稳定 ID 排序，为组合挑选目标提供确定优先级。
    scores.sort(key=lambda score: (-score.value, score.security_id))
    # 决策身份依赖固定输入和固定策略版本，不依赖运行UUID。
    decision_id = canonical_hash(
        {"snapshot_id": snapshot.snapshot_id, "strategy": "weekly-two-factor-1.0.0"}
    )
    return SignalSet(
        decision_id=decision_id,
        decision_time=snapshot.decision_time,
        snapshot_id=snapshot.snapshot_id,
        scores=scores,
        excluded=excluded,
    )


def score_ma_factors(snapshot: DataSnapshot, factors: list[FactorValue]) -> SignalSet:
    """将完整均线证据中的正趋势强度转换为单因子百分位信号。

    MA5、MA20 仅保留在因子文件供解释；只有 ma_trend 参与评分且权重为 100%。
    先排除强度不为正的股票，再按合格样本平均并列名次评分，单只得 1。
    未知行业保持 None，由组合与风控按最坏行业占用处理。外来身份、因子版本、
    重复因子或快照时点不符抛 ContractError，不把非法数据静默当成有效信号。
    """
    universe = {security.security_id: security for security in qualified_universe(snapshot)}
    identities = {security.security_id for security in snapshot.securities}
    if len(identities) != len(snapshot.securities):
        raise ContractError("均线评分证券身份重复")
    required = {definition.factor_id: definition.version for definition in MA_DEFINITIONS}
    values: dict[str, dict[str, float]] = {}
    excluded = {
        security.security_id: "not_eligible"
        for security in snapshot.securities
        if security.asset_type == "common_stock" and security.security_id not in universe
    }
    seen: set[tuple[str, str]] = set()
    for factor in factors:
        if (
            factor.snapshot_id != snapshot.snapshot_id
            or factor.decision_time != snapshot.decision_time
        ):
            raise ContractError("因子快照或时点不一致")
        if factor.security_id not in identities:
            raise ContractError("均线因子证券身份未知")
        if required.get(factor.factor_id) != factor.factor_version:
            raise ContractError("未知均线因子或版本")
        key = (factor.security_id, factor.factor_id)
        if key in seen:
            raise ContractError("重复因子结果")
        seen.add(key)
        if factor.security_id not in universe:
            continue
        if factor.value is None or factor.reason is not None:
            excluded[factor.security_id] = factor.reason or "missing_factor"
        else:
            # 每证券收集三个解释项；任何一项缺失均不能仅凭趋势值放行。
            values.setdefault(factor.security_id, {})[factor.factor_id] = factor.value
    eligible: list[str] = []
    for identity in universe:
        indicators = values.get(identity, {})
        if len(indicators) != len(required):
            excluded.setdefault(identity, "missing_factor")
        elif indicators["ma5"] <= 0 or indicators["ma20"] <= 0:
            excluded[identity] = "invalid_price"
        elif not math.isclose(
            indicators["ma_trend"],
            indicators["ma5"] / indicators["ma20"] - 1,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            # 仅容忍同一算式序列化后的浮点误差，不覆盖原值；排名并列仍使用精确值。
            raise ContractError("均线与趋势强度不一致")
        elif indicators["ma5"] <= indicators["ma20"] or indicators["ma_trend"] <= 0:
            excluded[identity] = "non_positive_trend"
        else:
            eligible.append(identity)
    # 升序零基名次用于百分位；并列只依据精确强度，不引入额外浮点容差。
    ordered = sorted(eligible, key=lambda identity: (values[identity]["ma_trend"], identity))
    percentiles: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while (
            end < len(ordered)
            and values[ordered[end]]["ma_trend"] == values[ordered[index]]["ma_trend"]
        ):
            end += 1
        percentile = 1.0 if len(ordered) == 1 else (index + end - 1) / 2 / (len(ordered) - 1)
        for identity in ordered[index:end]:
            percentiles[identity] = percentile
        index = end
    # 降序按实际强度排列；同强度按稳定 ID，输出百分位与此排序保持一致。
    scores = [
        Score(
            security_id=identity,
            sector=universe[identity].sector,
            components={"ma_trend": percentiles[identity]},
            value=percentiles[identity],
        )
        for identity in sorted(
            eligible, key=lambda identity: (-values[identity]["ma_trend"], identity)
        )
    ]
    version = "ma-trend-1.0.0"
    return SignalSet(
        decision_id=canonical_hash({"snapshot_id": snapshot.snapshot_id, "strategy": version}),
        decision_time=snapshot.decision_time,
        snapshot_id=snapshot.snapshot_id,
        strategy_version=version,
        scores=scores,
        excluded=excluded,
    )
