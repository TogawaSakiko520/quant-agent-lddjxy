"""组合约束、现金预留与挂单净额的独立固定样本测试。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_data import security_record

from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    DemoConfig,
    OrderRecord,
    Quote,
    Score,
    SecurityRecord,
    SignalSet,
)
from quant_core.portfolio import build_portfolio, plan_orders


def portfolio_case(
    count: int = 20,
) -> tuple[SignalSet, AccountSnapshot, dict[str, Quote], list[SecurityRecord], DemoConfig]:
    """装配固定候选场景，返回信号、实际账户、报价、证券主表和约束配置。

    count 只改变候选数量；每只报价均为 100 美元，账户为 100000 美元且空仓。
    signals.scores 是按稳定证券 ID 排好的候选评分列表，quotes 按同一 ID 索引；
    masters 提供行业与可交易资格。这些都是被测组合函数的输入，不是目标仓位。
    """
    at = datetime(2023, 1, 6, 21, 1, tzinfo=UTC)
    # 行业轮转防止基础样本意外触发行业集中。
    masters = [security_record(f"S{index:02d}", f"sector-{index % 6}") for index in range(count)]
    # 排序分数只作为已批准输入，不调用因子算法造期望。
    scores = [
        Score(
            security_id=record.security_id,
            sector=record.sector,
            components={"momentum": 1.0, "low_volatility": 1.0},
            value=1.0,
        )
        for record in masters
    ]
    signals = SignalSet(
        decision_id="decision", decision_time=at, snapshot_id="snapshot", scores=scores
    )
    account = AccountSnapshot(as_of=at, cash=Decimal("100000"), available_cash=Decimal("100000"))
    quotes = {
        record.security_id: Quote(security_id=record.security_id, at=at, price=Decimal("100"))
        for record in masters
    }
    return signals, account, quotes, masters, DemoConfig()


def test_equal_targets_cash_and_halted_old_position() -> None:
    """20只各45股形成90%目标，候选不足留现金，停牌旧仓不消失。"""
    signals, account, quotes, masters, config = portfolio_case()
    # 目标是希望持有的整股数量，生成目标本身不会修改 account 或产生真实成交。
    target = build_portfolio(signals, account, quotes, masters, config)
    # 每只基础目标固定4.5%：100000 × 0.045 / 100 = 45股，20只合计90%，不是按候选数重新均分。
    # 各目标4500美元，恰好45股。
    assert [position.quantity for position in target.positions] == [45] * 20
    assert target.cash_weight == pytest.approx(0.1)
    # 两只候选不应重新归一化成满仓。
    sparse = build_portfolio(
        signals.model_copy(update={"scores": signals.scores[:2]}), account, quotes, masters, config
    )
    # 两只各4.5%，应有91%现金。
    assert sparse.cash_weight == pytest.approx(0.91)
    # 新股票池中不可交易旧仓仍属于账户事实。
    halted = security_record("OLD", "legacy", tradable=False)
    quotes["OLD"] = Quote(
        security_id="OLD", at=signals.decision_time, price=Decimal("100"), tradable=False
    )
    # 10股旧仓市值1000美元，现金99000仍合计100000。
    held = account.model_copy(
        update={
            "cash": Decimal("99000"),
            "available_cash": Decimal("99000"),
            "positions": {"OLD": 10},
        }
    )
    # 停牌仓位必须占用一个名额和风险预算。
    result = build_portfolio(signals, held, quotes, masters + [halted], config)
    assert (
        next(position.quantity for position in result.positions if position.security_id == "OLD")
        == 10
    )
    assert len(result.positions) == 20


def test_pending_orders_netting_cash_and_execution_reserve() -> None:
    """执行涨价预留缩减数量，已有挂单不重复买入，费用现金预算成立。"""
    signals, account, quotes, masters, config = portfolio_case()
    target = build_portfolio(signals, account, quotes, masters, config)
    # 下一交易时段报价比决策价高0.1%。
    execution_at = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    execution_quotes = {
        sid: Quote(security_id=sid, at=execution_at, price=Decimal("100.1")) for sid in quotes
    }
    account = account.model_copy(update={"as_of": execution_at})
    # 规划须容纳1%价格空间，而不是放宽90%上限。
    # dict.fromkeys 用报价中的证券 ID 建立 ADV 输入：每只此前20日平均成交100万股，
    # 这是流动性额度的股数口径，不是账户持仓或可用现金。
    intents = plan_orders(
        target,
        account,
        [],
        execution_quotes,
        dict.fromkeys(quotes, 1000000.0),
        config,
        execution_at,
        security_records=masters,
    )
    # 汇总限价市值；执行涨价与费用预留应使计划规模低于原90%目标。
    total = sum((intent.quantity * intent.limit_price for intent in intents), Decimal("0"))
    assert total < Decimal("90000")
    assert sum(intent.quantity for intent in intents) < 900
    # intents 是本轮建议下的订单意图；已有目标并不代表这些股数已经买到。
    # OPEN 的 filled_quantity 默认为0，整张意图数量仍未成交；必须占用现金和目标差额。
    # 相同挂单存在时不能生成第二个净重复买单。
    pending = [OrderRecord(intent=intents[0], status="OPEN", broker_order_id="broker-1")]
    repeated = plan_orders(
        target,
        account,
        pending,
        execution_quotes,
        dict.fromkeys(quotes, 1000000.0),
        config,
        execution_at,
        security_records=masters,
    )
    assert all(intent.security_id != intents[0].security_id for intent in repeated)
    # model_copy 在这里刻意替换状态而不重新构造模型，让规划入口面对未知挂单。
    # 未知状态必须先恢复。
    with pytest.raises(ContractError, match="未知"):
        plan_orders(
            target,
            account,
            [pending[0].model_copy(update={"status": "UNKNOWN"})],
            execution_quotes,
            dict.fromkeys(quotes, 1000000.0),
            config,
            execution_at,
        )


def test_future_quote_rejected_and_liquidity_is_not_relaxed() -> None:
    """未来报价阻止目标生成，低ADV只能缩量不能放宽上限。"""
    signals, account, quotes, masters, config = portfolio_case(2)
    future = {
        sid: quote.model_copy(update={"at": quote.at + timedelta(days=1)})
        for sid, quote in quotes.items()
    }
    with pytest.raises(ContractError, match="未来报价"):
        build_portfolio(signals, account, future, masters, config)
    # 账户事实也不能从下一执行时点回填到盘后目标。
    with pytest.raises(ContractError, match="未来账户"):
        build_portfolio(
            signals,
            account.model_copy(update={"as_of": signals.decision_time + timedelta(days=1)}),
            quotes,
            masters,
            config,
        )
    target = build_portfolio(signals, account, quotes, masters, config)
    # ADV为100股，1%限制每单仅1股。
    intents = plan_orders(
        target, account, [], quotes, dict.fromkeys(quotes, 100.0), config, signals.decision_time
    )
    assert [intent.quantity for intent in intents] == [1, 1]


def test_sector_cap_and_realized_turnover_requirements() -> None:
    """行业不足留现金；90%换仓不能突破100%gross预算，已有成交须用真实金额。"""
    signals, account, quotes, masters, config = portfolio_case()
    # 所有评分证券强制归属同一行业，独立验证25%上限。
    concentrated = signals.model_copy(
        update={"scores": [score.model_copy(update={"sector": "one"}) for score in signals.scores]}
    )
    capped = build_portfolio(concentrated, account, quotes, masters, config)
    # 5只4.5%加第6只2.5%，合计恰好25%。
    assert sum(position.quantity for position in capped.positions) == 250
    assert capped.cash_weight == pytest.approx(0.75)
    target = build_portfolio(signals, account, quotes, masters, config)
    # 旧仓900股共90000美元，卖出后必须使用真实90000成交换手。
    execution_at = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    execution_quotes = {
        sid: quote.model_copy(update={"at": execution_at}) for sid, quote in quotes.items()
    }
    execution_quotes["OLD"] = Quote(security_id="OLD", at=execution_at, price=Decimal("100"))
    before = account.model_copy(
        update={
            "as_of": execution_at,
            "cash": Decimal("10000"),
            "available_cash": Decimal("10000"),
            "positions": {"OLD": 900},
        }
    )
    # 先规划卖出，未完成卖出不释放风险与现金。
    sells = plan_orders(
        target,
        before,
        [],
        execution_quotes,
        dict.fromkeys(execution_quotes, 1000000.0),
        config,
        execution_at,
        security_records=masters + [security_record("OLD", "legacy")],
    )
    assert len(sells) == 1 and sells[0].side == "SELL" and sells[0].quantity == 900
    # 订单的 filled_quantity 是累计成交，账户 before/after 则分别代表卖出前后的实际资产。
    # 仅把订单标为 FILLED 不会自动改写账户，所以后面另给手算后的 after。
    completed = OrderRecord(
        intent=sells[0], status="FILLED", filled_quantity=900, broker_order_id="sold"
    )
    # 卖出收入已到账但需扣4.5美元费用。
    after = account.model_copy(
        update={
            "as_of": execution_at,
            "cash": Decimal("99995.5"),
            "available_cash": Decimal("99995.5"),
        }
    )
    # 不允许用99美元限价假装100美元真实成交换手。
    with pytest.raises(ContractError, match="真实turnover_used"):
        plan_orders(
            target,
            after,
            [completed],
            execution_quotes,
            dict.fromkeys(execution_quotes, 1000000.0),
            config,
            execution_at,
        )
    # gross 换手按买卖金额绝对值相加，不用卖出抵销买入。
    # 真实成交90000美元只剩10000美元gross预算。
    buys = plan_orders(
        target,
        after,
        [completed],
        execution_quotes,
        dict.fromkeys(execution_quotes, 1000000.0),
        config,
        execution_at,
        security_records=masters,
        turnover_used=Decimal("90000"),
    )
    assert sum((intent.quantity * intent.limit_price for intent in buys), Decimal("0")) <= Decimal(
        "10000"
    )
    assert sum(intent.quantity for intent in buys) < 100


def test_wrong_security_quote_cannot_value_or_plan_orders() -> None:
    """字典键与报价消息证券错配不能估值、建目标或规划订单。"""
    signals, account, quotes, masters, config = portfolio_case(2)
    target = build_portfolio(signals, account, quotes, masters, config)
    # 篡改消息证券身份，字典键仍保持原证券。
    wrong_quotes = quotes | {"S00": quotes["S00"].model_copy(update={"security_id": "OTHER"})}
    with pytest.raises(ContractError, match="报价证券身份"):
        build_portfolio(signals, account, wrong_quotes, masters, config)
    with pytest.raises(ContractError, match="报价证券身份"):
        plan_orders(
            target,
            account,
            [],
            wrong_quotes,
            dict.fromkeys(quotes, 1000000.0),
            config,
            signals.decision_time,
        )
