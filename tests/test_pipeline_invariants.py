"""未来输入扰动的整条决策不变性：同时验证因子、评分、目标，避免只检查数据表。"""

from datetime import timedelta
from decimal import Decimal

from test_data import history, market_record, security_record

from quant_core.adapters.datasets import fixture_quotes
from quant_core.contracts import AccountSnapshot, DemoConfig, seal_record
from quant_core.data import build_snapshot
from quant_core.factors import calculate_factors
from quant_core.portfolio import build_portfolio
from quant_core.signals import score_factors


def test_future_prices_revisions_and_master_do_not_change_any_past_decision() -> None:
    """添加未来价格、历史修订及未来行业变更后，过去因子/评分/目标必须完全相同。"""
    # 本例把同一决策输入在“增加未来数据前/后”分别送完整链路，比较不变性；
    # 原始因子数值不是手算期望，公式正确性另由 test_factors 的固定样本验证。
    # 两只股票各有253个完整价格，保证比较的不是空策略结果。
    base, calendar = history([float(100 + index) for index in range(253)])
    second = [
        market_record("B", row.session, 200.0 + index * 0.5, row.event_time)
        for index, row in enumerate(base.records)
    ]
    securities = [security_record("A"), security_record("B")]
    original = build_snapshot(
        base.records + second, securities, base.decision_time, calendar.version
    )
    # 未来修订改变过去某天的研究价和原始价，但当时尚未知晓。
    revised = seal_record(
        base.records[10].model_copy(
            update={
                "revision": 2,
                "raw_close": 9999.0,
                "total_return_close": 9999.0,
                "available_at": base.decision_time + timedelta(days=1),
            }
        )
    )
    # 下一交易日价格不得被向后填充到当前决策。
    next_day = calendar.next_session(base.records[-1].session)
    future = market_record("A", next_day, 10000.0, calendar.close_at(next_day))
    # 这里测试 build_snapshot 先过滤主表的标准链路，不覆盖直接把未过滤主表
    # 传给风控/订单规划入口的已知 F01 缺陷。
    # 未来行业和股票代码修订同样不准影响过去组合约束。
    future_master = seal_record(
        securities[0].model_copy(
            update={
                "revision": 2,
                "ticker": "CHANGED",
                "sector": "future-sector",
                "available_at": base.decision_time + timedelta(days=1),
            }
        )
    )
    # 同时反转旧输入顺序，验证排序不成为隐藏状态。
    perturbed = build_snapshot(
        [future, revised, *reversed(base.records + second)],
        [future_master, *reversed(securities)],
        base.decision_time,
        calendar.version,
    )
    assert perturbed == original
    before_factors = calculate_factors(original, calendar)
    after_factors = calculate_factors(perturbed, calendar)
    # 两只证券的两个因子均实际有效。
    assert len(before_factors) == 4 and all(item.value is not None for item in before_factors)
    assert after_factors == before_factors
    before_signals = score_factors(original, before_factors)
    after_signals = score_factors(perturbed, after_factors)
    # 不是只比较空列表；共同股票池确实含两只。
    assert len(before_signals.scores) == 2 and before_signals == after_signals
    # 初态没有后验持仓或现金变化。
    account = AccountSnapshot(
        as_of=base.decision_time, cash=Decimal("100000"), available_cash=Decimal("100000")
    )
    before_target = build_portfolio(
        before_signals,
        account,
        fixture_quotes(original.records, calendar, execution=False),
        original.securities,
        DemoConfig(),
    )
    # 扰动组合重新执行完整预算逻辑。
    after_target = build_portfolio(
        after_signals,
        account,
        fixture_quotes(perturbed.records, calendar, execution=False),
        perturbed.securities,
        DemoConfig(),
    )
    # 目标确实包含持仓，且身份、股数、权重与原因全部保持不变。
    assert len(before_target.positions) == 2 and after_target == before_target
