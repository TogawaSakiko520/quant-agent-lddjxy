"""组合约束、现金预留与挂单净额的独立固定样本测试。"""

# 显式UTC交易时点使测试不依赖今天。
from datetime import UTC, datetime, timedelta

# 资金期望以十进制精确断言。
from decimal import Decimal

# 异常边界验证不能被静默降级。
import pytest

# 固定历史身份夹具不实现被测组合算法。
from test_data import security_record

# 共享类型用于明确建立目标和账户。
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

# 被测目标及差额入口。
from quant_core.portfolio import build_portfolio, plan_orders


def portfolio_case(
    count: int = 20,
) -> tuple[SignalSet, AccountSnapshot, dict[str, Quote], list[SecurityRecord], DemoConfig]:
    """创建100000美元、每股100美元和固定排序候选；仅装配输入，无外部副作用。"""
    # 周五收盘后决策。
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
    # 固定决策身份确保幂等可核验。
    signals = SignalSet(
        decision_id="decision", decision_time=at, snapshot_id="snapshot", scores=scores
    )
    # 全现金初始账户无隐含融资。
    account = AccountSnapshot(as_of=at, cash=Decimal("100000"), available_cash=Decimal("100000"))
    # 决策报价不能包含下一开盘的信息。
    quotes = {
        record.security_id: Quote(security_id=record.security_id, at=at, price=Decimal("100"))
        for record in masters
    }
    # 所有演示参数使用同一严格契约。
    return signals, account, quotes, masters, DemoConfig()


def test_equal_targets_cash_and_halted_old_position() -> None:
    """20只各45股形成90%目标，候选不足留现金，停牌旧仓不消失，无外部副作用。"""
    # 固定100000美元与100美元每股可以直接手算。
    signals, account, quotes, masters, config = portfolio_case()
    # 构建初始组合目标。
    target = build_portfolio(signals, account, quotes, masters, config)
    # 各目标4500美元，恰好45股。
    assert [position.quantity for position in target.positions] == [45] * 20
    # 余下10000美元保持现金。
    assert target.cash_weight == pytest.approx(0.1)
    # 两只候选不应重新归一化成满仓。
    sparse = build_portfolio(
        signals.model_copy(update={"scores": signals.scores[:2]}), account, quotes, masters, config
    )
    # 两只各4.5%，应有91%现金。
    assert sparse.cash_weight == pytest.approx(0.91)
    # 新股票池中不可交易旧仓仍属于账户事实。
    halted = security_record("OLD", "legacy", tradable=False)
    # 增加一只100美元的停牌报价。
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
    # 不能假设停牌旧仓已经卖出。
    assert (
        next(position.quantity for position in result.positions if position.security_id == "OLD")
        == 10
    )
    # 名额不能偷偷扩为21。
    assert len(result.positions) == 20


def test_pending_orders_netting_cash_and_execution_reserve() -> None:
    """执行涨价预留缩减数量，已有挂单不重复买入，费用现金预算成立，无外部副作用。"""
    # 先建立盘后目标。
    signals, account, quotes, masters, config = portfolio_case()
    # 决策阶段不能看到未来开盘价格。
    target = build_portfolio(signals, account, quotes, masters, config)
    # 下一交易时段报价比决策价高0.1%。
    execution_at = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    # 独立原始报价事件用于执行规划。
    execution_quotes = {
        sid: Quote(security_id=sid, at=execution_at, price=Decimal("100.1")) for sid in quotes
    }
    # 初始全现金账户事实在执行时确认。
    account = account.model_copy(update={"as_of": execution_at})
    # 规划须容纳1%价格空间，而不是放宽90%上限。
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
    # 全部限价市值加费用必须在最保守总仓位预算之内。
    total = sum((intent.quantity * intent.limit_price for intent in intents), Decimal("0"))
    # 明确不允许满目标数量在执行报价变化后突破预算。
    assert total < Decimal("90000")
    # 计划必须保留至少一个未完全实现目标。
    assert sum(intent.quantity for intent in intents) < 900
    # 相同挂单存在时不能生成第二个净重复买单。
    pending = [OrderRecord(intent=intents[0], status="OPEN", broker_order_id="broker-1")]
    # 再规划将挂单计入数量和现金预留。
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
    # 初始第一只股票只能有原来的订单。
    assert all(intent.security_id != intents[0].security_id for intent in repeated)
    # 未知状态必须先恢复。
    with pytest.raises(ContractError, match="未知"):
        # 不根据超时猜测订单失败再重发。
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
    """未来报价阻止目标生成，低ADV只能缩量不能放宽上限，无外部副作用。"""
    # 单一目标仍保留其他资金现金。
    signals, account, quotes, masters, config = portfolio_case(2)
    # 显式构造未来报价。
    future = {
        sid: quote.model_copy(update={"at": quote.at + timedelta(days=1)})
        for sid, quote in quotes.items()
    }
    # 下一时段价格不得反向影响过去决策。
    with pytest.raises(ContractError, match="未来报价"):
        # 数据时点不符时明确停止。
        build_portfolio(signals, account, future, masters, config)
    # 账户事实也不能从下一执行时点回填到盘后目标。
    with pytest.raises(ContractError, match="未来账户"):
        # 禁止只检查报价而遗漏账户时点。
        build_portfolio(
            signals,
            account.model_copy(update={"as_of": signals.decision_time + timedelta(days=1)}),
            quotes,
            masters,
            config,
        )
    # 当前合法目标仍能构建。
    target = build_portfolio(signals, account, quotes, masters, config)
    # ADV为100股，1%限制每单仅1股。
    intents = plan_orders(
        target, account, [], quotes, dict.fromkeys(quotes, 100.0), config, signals.decision_time
    )
    # 小额交易不应被静默扩大以追赶目标。
    assert [intent.quantity for intent in intents] == [1, 1]


def test_sector_cap_and_realized_turnover_requirements() -> None:
    """行业不足留现金；90%换仓不能突破100%gross预算，已有成交须用真实金额，无副作用。"""
    # 基础固定样本有100000美元和20候选。
    signals, account, quotes, masters, config = portfolio_case()
    # 所有评分证券强制归属同一行业，独立验证25%上限。
    concentrated = signals.model_copy(
        update={"scores": [score.model_copy(update={"sector": "one"}) for score in signals.scores]}
    )
    # 行业总额不得被目标再次归一化。
    capped = build_portfolio(concentrated, account, quotes, masters, config)
    # 5只4.5%加第6只2.5%，合计恰好25%。
    assert sum(position.quantity for position in capped.positions) == 250
    # 无法投资部分明确留现金。
    assert capped.cash_weight == pytest.approx(0.75)
    # 正常20只目标用于换仓测试。
    target = build_portfolio(signals, account, quotes, masters, config)
    # 旧仓900股共90000美元，卖出后必须使用真实90000成交换手。
    execution_at = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    # 执行报价与决策价相同使手算清晰。
    execution_quotes = {
        sid: quote.model_copy(update={"at": execution_at}) for sid, quote in quotes.items()
    }
    # 独立旧仓报价，不把它混入新目标。
    execution_quotes["OLD"] = Quote(security_id="OLD", at=execution_at, price=Decimal("100"))
    # 真实旧账户90%仓位、10%现金。
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
    # 本轮只产生已确认旧仓的卖单。
    assert len(sells) == 1 and sells[0].side == "SELL" and sells[0].quantity == 900
    # 模拟券商已经确认该订单全成。
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
        # 已有成交必须明确提供唯一成交事件汇总。
        plan_orders(
            target,
            after,
            [completed],
            execution_quotes,
            dict.fromkeys(execution_quotes, 1000000.0),
            config,
            execution_at,
        )
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
    # 新买单不得超过10000美元剩余限价总额。
    assert sum((intent.quantity * intent.limit_price for intent in buys), Decimal("0")) <= Decimal(
        "10000"
    )
    # 大部分90%目标必须留待未来决策，不能放宽换手预算。
    assert sum(intent.quantity for intent in buys) < 100


def test_wrong_security_quote_cannot_value_or_plan_orders() -> None:
    """字典键与报价消息证券错配不能估值、建目标或规划订单，无外部副作用。"""
    # 固定两只候选和合法盘后报价。
    signals, account, quotes, masters, config = portfolio_case(2)
    # 先取得合法目标供规划阶段独立检查。
    target = build_portfolio(signals, account, quotes, masters, config)
    # 篡改消息证券身份，字典键仍保持原证券。
    wrong_quotes = quotes | {"S00": quotes["S00"].model_copy(update={"security_id": "OTHER"})}
    # 目标不能借其他证券价格构造。
    with pytest.raises(ContractError, match="报价证券身份"):
        # 全现金账户也必须核验候选报价身份。
        build_portfolio(signals, account, wrong_quotes, masters, config)
    # 已有合法目标也不能让执行价格错配。
    with pytest.raises(ContractError, match="报价证券身份"):
        # 下单规划重新执行身份核验。
        plan_orders(
            target,
            account,
            [],
            wrong_quotes,
            dict.fromkeys(quotes, 1000000.0),
            config,
            signals.decision_time,
        )
