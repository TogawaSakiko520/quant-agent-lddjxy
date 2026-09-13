"""因子公式、共同样本与确定性并列评分的可手算固定样本。"""

# 年化手算期望只用明确数学公式。
import math

# 浮点容差只用于数学舍入，不放宽金融风险。
import pytest

# 夹具只装配原始价格，不计算被测因子。
from test_data import history, security_record

# 共享对象用于明确构造边界样本。
from quant_core.contracts import FactorValue

# 被测因子入口。
from quant_core.factors import calculate_factors

# 被测固定评分入口。
from quant_core.signals import score_factors


def test_momentum_exact_index_and_low_volatility_sample_std() -> None:
    """动量独立验证253价格索引；波动独立验证60收益n-1分母，无外部副作用。"""
    # P[t-252]=100且P[t-21]=331，手算动量为2.31。
    snapshot, calendar = history([float(100 + index) for index in range(253)])
    # 原始输出转因子索引仅便于断言。
    factors = {factor.factor_id: factor for factor in calculate_factors(snapshot, calendar)}
    # 公式不得误用最后收盘或第252个观测为起点。
    assert factors["momentum"].value == pytest.approx(2.31)
    # 建立30次正1%和30次负1%的交替收益。
    prices = [100.0]
    # 独立价格构造不是重复被测标准差实现。
    for index in range(60):
        # 每对收益的算术平均恰好为零。
        prices.append(prices[-1] * (1.01 if index % 2 == 0 else 0.99))
    # 61观测刚好满足低波动窗口。
    short, calendar = history(prices)
    # 只提取被测低波动结果。
    low_vol = next(
        factor
        for factor in calculate_factors(short, calendar)
        if factor.factor_id == "low_volatility"
    )
    # 样本标准差为0.01*sqrt(60/59)，再年化并取负。
    assert low_vol.value == pytest.approx(-0.01 * math.sqrt(252 * 60 / 59))


def test_missing_trading_day_is_not_compressed_and_minimum_prices() -> None:
    """窗口缺日不能压缩为连续观测，历史不足保持显式空值，无外部副作用。"""
    # 固定253个真实交易日输入。
    snapshot, calendar = history([100.0] * 253)
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
    """四只股票并列百分位可手算，缺任何因子从共同样本剔除，无外部副作用。"""
    # 仅需快照时间与主表，公式结果在测试中独立给定。
    snapshot, _ = history([100.0])
    # 四只股票都有共同资格。
    snapshot = snapshot.model_copy(
        update={"securities": [security_record(identity) for identity in "ABCD"]}
    )
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
    # 被测评分不使用收益标签。
    signals = score_factors(snapshot, factors)
    # 同分A在B前，其他按综合分降序。
    assert [score.security_id for score in signals.scores] == ["D", "C", "A", "B"]
    # 百分位从0到1，平均并列名次独立手算。
    assert [score.value for score in signals.scores] == pytest.approx([1.0, 2 / 3, 1 / 6, 1 / 6])
    # 缺D的一个因子即完全退出共同样本。
    incomplete = score_factors(snapshot, factors[:-1])
    # 禁止仅将缺失因子重新分配权重。
    assert "D" in incomplete.excluded
    # 剩余样本应当重新以共同三只排名。
    assert len(incomplete.scores) == 3
