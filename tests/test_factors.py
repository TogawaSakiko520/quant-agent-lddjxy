"""因子公式、共同样本与确定性并列评分的可手算固定样本。"""

import math

import pytest
from test_data import history, security_record

from quant_core.contracts import FactorValue
from quant_core.factors import calculate_factors
from quant_core.signals import score_factors


def test_momentum_exact_index_and_low_volatility_sample_std() -> None:
    """独立验证动量的 253 价格索引，以及低波动的 60 收益、n-1 分母。"""
    # history 把价格依次放到证券 A 的连续交易日，返回含行情/主表/决策时点的快照和日历。
    # calculate_factors 返回每只证券的多条 FactorValue；本例只有 A，按 factor_id 建索引。
    # P[t-252]=100且P[t-21]=331，手算动量为2.31。
    snapshot, calendar = history([float(100 + index) for index in range(253)])
    factors = {factor.factor_id: factor for factor in calculate_factors(snapshot, calendar)}
    # 公式不得误用最后收盘或第252个观测为起点。
    assert factors["momentum"].value == pytest.approx(2.31)
    # 30 次 +1% 与 30 次 -1% 的算术均值为零，独立构造价格供标准差验证。
    prices = [100.0]
    for index in range(60):
        prices.append(prices[-1] * (1.01 if index % 2 == 0 else 0.99))
    # 61观测刚好满足低波动窗口。
    short, calendar = history(prices)
    low_vol = next(
        factor
        for factor in calculate_factors(short, calendar)
        if factor.factor_id == "low_volatility"
    )
    # 样本标准差为0.01*sqrt(60/59)，再年化并取负。
    assert low_vol.value == pytest.approx(-0.01 * math.sqrt(252 * 60 / 59))


def test_missing_trading_day_is_not_compressed_and_minimum_prices() -> None:
    """窗口缺日不能压缩为连续观测；60 个价格不足时两个因子均保持空值。"""
    snapshot, calendar = history([100.0] * 253)
    # model_copy 不重新生成快照封印；此例直接测试因子对 records 日历完整性的检查，
    # 并非模拟通过 build_snapshot 的外部快照验签。删除一日后不能把余下价格当作连续日。
    # 删除最近窗口中的一日而非改变价格。
    missing = snapshot.model_copy(
        update={"records": snapshot.records[:-10] + snapshot.records[-9:]}
    )
    # 两个公式都包含这个缺失交易日。
    assert all(factor.value is None for factor in calculate_factors(missing, calendar))
    # 60价格不足以产生60日收益。
    short, calendar = history([100.0] * 60)
    # 缺历史不能填零当作低波动。
    assert all(factor.value is None for factor in calculate_factors(short, calendar))


def test_ties_use_average_rank_and_common_pool() -> None:
    """并列值取平均名次；缺少任一因子的股票退出两个因子的共同排名样本。"""
    # 仅需快照时间与主表，公式结果在测试中独立给定。
    snapshot, _ = history([100.0])
    snapshot = snapshot.model_copy(
        update={"securities": [security_record(identity) for identity in "ABCD"]}
    )
    # 此处绕过因子计算，仅为评分函数提供两个因子的固定结果；嵌套循环生成4证券×2因子。
    # security_id 将结果对应回主表，snapshot_id/decision_time 将其绑定到同一决策快照。
    # 两因子各自原值均为[1,1,3,4]。
    factors = [
        FactorValue(
            security_id=identity,
            factor_id=factor_id,
            decision_time=snapshot.decision_time,
            snapshot_id=snapshot.snapshot_id,
            value=value,
        )
        for identity, value in zip("ABCD", [1.0, 1.0, 3.0, 4.0], strict=True)
        for factor_id in ("momentum", "low_volatility")
    ]
    signals = score_factors(snapshot, factors)
    # 同分A在B前，其他按综合分降序。
    assert [score.security_id for score in signals.scores] == ["D", "C", "A", "B"]
    # 从0起排位时 A/B 并列占第0、1位，平均0.5；除以 n-1=3 得1/6。
    # C/D分别是2/3和1；两个因子本例同序，等权平均后不变。
    # 百分位位于[0,1]，并列端点不保证取到0。
    assert [score.value for score in signals.scores] == pytest.approx([1.0, 2 / 3, 1 / 6, 1 / 6])
    # 循环最后一条是 D 的 low_volatility，factors[:-1] 仅删它，D仍有动量。
    # 缺D的一个因子即完全退出共同样本。
    incomplete = score_factors(snapshot, factors[:-1])
    # 禁止仅将缺失因子重新分配权重。
    assert "D" in incomplete.excluded
    # 剩余样本应当重新以共同三只排名。
    assert len(incomplete.scores) == 3
