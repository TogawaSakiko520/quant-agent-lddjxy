"""提交前硬约束和独立减风险权限测试；失败不修改阈值或账户。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from test_data import security_record

from quant_core.adapters.calendar import ExchangeCalendar
from quant_core.contracts import (
    AccountSnapshot,
    DemoConfig,
    OrderIntent,
    OrderRecord,
    Quote,
    TargetPortfolio,
    TargetPosition,
)
from quant_core.risk import assess_operation, assess_order, assess_reduce


def approved_target(intent: OrderIntent) -> TargetPortfolio:
    """为 intent 所属决策装配固定批准目标，声明该证券最多希望持有的股数。

    nav 固定100000美元，quantity 取用例明确指定的订单数量；weight 固定0.05，
    并不替代风控按限价、费用和现有账户重新计算暴露。此工厂只给授权输入，
    不计算 allowed 或拒绝原因；没有模拟一个完整的目标优化过程。
    """
    # 目标数量由固定订单样本独立声明，风险上限仍由配置另行检验。
    position = TargetPosition(
        security_id=intent.security_id,
        sector="tech",
        weight=0.05,
        quantity=intent.quantity,
        reason="fixed_approved_test_quantity",
    )
    return TargetPortfolio(
        decision_id=intent.decision_id,
        as_of=intent.created_at,
        nav=Decimal("100000"),
        positions=[position],
        cash_weight=0.95,
    )


def test_order_hard_checks_losses_and_unreconciled_state() -> None:
    """有效订单通过；过期报价、对账失败、缺资金和亏损分别拒绝。"""
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    account = AccountSnapshot(as_of=now, cash=Decimal("100000"), available_cash=Decimal("100000"))
    # account 是已确认的空仓资产，quotes 是按证券ID索引的原始执行价格。
    # intent 是本次拟下订单，target 则是批准的最终股数；传 [] 表示没有未完成旧单。
    # 40股乘101美元限价为4040美元，低于5%单票上限。
    intent = OrderIntent(
        client_order_id="one",
        account_id="DEMO",
        decision_id="d",
        security_id="A",
        side="BUY",
        quantity=40,
        limit_price=Decimal("101"),
        reserved_fee=Decimal("1"),
        created_at=now,
        eligible_at=now,
    )
    quotes = {"A": Quote(security_id="A", at=now, price=Decimal("100"))}
    config = DemoConfig()
    valid = assess_order(
        intent,
        account,
        [],
        quotes,
        config,
        now,
        calendar,
        security_records=[security_record()],
        adv={"A": 100000.0},
        target=approved_target(intent),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert valid.allowed
    # 数据与账户未对齐时即使额度很小仍阻止正常提交。
    failed = assess_order(
        intent,
        account,
        [],
        quotes,
        config,
        now,
        calendar,
        reconciled=False,
        security_records=[security_record()],
        adv={"A": 100000.0},
        target=approved_target(intent),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert not failed.allowed and "unreconciled_account" in failed.reasons
    # 超过60秒的旧报价不得放行。
    stale = {"A": quotes["A"].model_copy(update={"at": now - timedelta(seconds=61)})}
    aged = assess_order(
        intent,
        account,
        [],
        stale,
        config,
        now,
        calendar,
        security_records=[security_record()],
        adv={"A": 100000.0},
        target=approved_target(intent),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert "stale_or_future_quote" in aged.reasons
    # 期初110000到当前100000亏损已超过3%。
    loss = assess_order(
        intent,
        account,
        [],
        quotes,
        config,
        now,
        calendar,
        reference_nav=Decimal("110000"),
        security_records=[security_record()],
        adv={"A": 100000.0},
        target=approved_target(intent),
        peak_nav=Decimal("110000"),
    )
    assert "daily_loss_limit" in loss.reasons
    # 可用现金只有100美元，即使总现金足够也不得占用未结算部分。
    limited = account.model_copy(update={"available_cash": Decimal("100")})
    cash_risk = assess_order(
        intent,
        limited,
        [],
        quotes,
        config,
        now,
        calendar,
        security_records=[security_record()],
        adv={"A": 100000.0},
        target=approved_target(intent),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert "insufficient_available_cash" in cash_risk.reasons


def test_reduce_and_cancel_have_independent_controlled_paths() -> None:
    """冻结新增不等于盲目清仓；减仓需授权、已确认股数、正常执行条件。"""
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    # 实际确认10股，不能以未完成买单扩大可卖数。
    account = AccountSnapshot(
        as_of=now, cash=Decimal("99000"), available_cash=Decimal("99000"), positions={"A": 10}
    )
    intent = OrderIntent(
        client_order_id="reduce",
        account_id="DEMO",
        decision_id="d",
        security_id="A",
        side="SELL",
        quantity=10,
        limit_price=Decimal("99"),
        reserved_fee=Decimal("1"),
        created_at=now,
        eligible_at=now,
    )
    quotes = {"A": Quote(security_id="A", at=now, price=Decimal("100"))}
    result = assess_reduce(intent, account, [], quotes, DemoConfig(), now, calendar)
    assert result.allowed and result.freeze_new_risk
    # 多卖一股将变空头，必须拒绝。
    excessive = assess_reduce(
        intent.model_copy(update={"quantity": 11}), account, [], quotes, DemoConfig(), now, calendar
    )
    assert "insufficient_confirmed_shares" in excessive.reasons
    # 对账失败时已知订单仍可受控撤单。
    assert assess_operation("CANCEL", reconciled=False, known_state=True).allowed
    assert not assess_operation("NEW", reconciled=False, known_state=True).allowed
    # 未授权撤单也不能通过紧急名义绕过。
    assert not assess_operation(
        "CANCEL", reconciled=False, known_state=True, authorized=False
    ).allowed


def test_pending_buy_limit_prices_and_unpaid_fees_bound_exposure() -> None:
    """独立复核样本：所有挂买单限价及未支付费用均参与最坏风险。"""
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    account = AccountSnapshot(as_of=now, cash=Decimal("100000"), available_cash=Decimal("100000"))
    # 旧挂买单最高成交9000美元，不能因目前报价1元只算90美元。
    pending_intent = OrderIntent(
        client_order_id="pending",
        account_id="DEMO",
        decision_id="d",
        security_id="A",
        side="BUY",
        quantity=90,
        limit_price=Decimal("100"),
        reserved_fee=Decimal("1"),
        created_at=now,
        eligible_at=now,
    )
    pending = OrderRecord(intent=pending_intent, status="OPEN", broker_order_id="pending-broker")
    # 新买单再占2000美元，最坏11000超过10%总仓位。
    current = OrderIntent(
        client_order_id="new",
        account_id="DEMO",
        decision_id="d",
        security_id="B",
        side="BUY",
        quantity=20,
        limit_price=Decimal("100"),
        reserved_fee=Decimal("1"),
        created_at=now,
        eligible_at=now,
    )
    # pending 的累计成交默认为0，所以全部90股都还占用风险；这不是实际持仓。
    # A当前报价极低，故意暴露仅用现价估算挂单的漏洞。
    quotes = {
        "A": Quote(security_id="A", at=now, price=Decimal("1")),
        "B": Quote(security_id="B", at=now, price=Decimal("100")),
    }
    # 本例构造10%总仓位/单票限制并把行业设为100%，只隔离总暴露反例；不修改默认配置。
    config = DemoConfig(max_gross=0.1, max_single=0.1, max_sector=1.0)
    result = assess_order(
        current,
        account,
        [pending],
        quotes,
        config,
        now,
        calendar,
        security_records=[security_record("A"), security_record("B")],
        adv={"B": 100000.0},
        target=approved_target(current),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert not result.allowed and "gross_exposure_limit" in result.reasons
    # 默认5%单票边界上买5000美元，再付1美元手续费，净值降为99999。
    boundary = current.model_copy(update={"quantity": 50})
    # 费用后5000/99999严格大于5%，必须拒绝而非舍入为通过。
    fee_result = assess_order(
        boundary,
        account,
        [],
        quotes,
        DemoConfig(),
        now,
        calendar,
        security_records=[security_record("B")],
        adv={"B": 100000.0},
        target=approved_target(boundary),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert not fee_result.allowed and "single_position_limit" in fee_result.reasons


def test_current_security_eligibility_and_quote_identity_are_enforced() -> None:
    """主表停牌、退市、隔离、未来或失效均禁止新买；报价证券身份必须匹配。"""
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    account = AccountSnapshot(as_of=now, cash=Decimal("100000"), available_cash=Decimal("100000"))
    intent = OrderIntent(
        client_order_id="identity",
        account_id="DEMO",
        decision_id="d",
        security_id="A",
        side="BUY",
        quantity=40,
        limit_price=Decimal("101"),
        reserved_fee=Decimal("1"),
        created_at=now,
        eligible_at=now,
    )
    # 行情声称可交易不能覆盖主表中的停牌事实。
    quotes = {"A": Quote(security_id="A", at=now, price=Decimal("100"))}
    # model_copy 刻意不重验模型/封印，把候选资格变体直接交给风控入口检查；
    # 此例保护的是资格判断，不把它当作数据接入层的内容哈希验收。
    # effective_to=今日利用右端不包含规则，因此今天已失效。
    # 每一种主表异常都是独立的拒绝条件。
    invalid_masters = [
        security_record().model_copy(update=update)
        for update in (
            {"tradable": False},
            {"listed": False},
            {"quality": "quarantined"},
            {"available_at": now + timedelta(seconds=1)},
            {"effective_to": now.date()},
            {"effective_from": now.date() + timedelta(days=1)},
            {"asset_type": "benchmark"},
        )
    ]
    for master in invalid_masters:
        result = assess_order(
            intent,
            account,
            [],
            quotes,
            DemoConfig(),
            now,
            calendar,
            security_records=[master],
            adv={"A": 100000.0},
            target=approved_target(intent),
            reference_nav=Decimal("100000"),
            peak_nav=Decimal("100000"),
        )
        assert not result.allowed and "security_not_eligible" in result.reasons
    # 字典键与消息身份冲突属于错证券价格。
    mismatch = {"A": Quote(security_id="DIFFERENT", at=now, price=Decimal("100"))}
    result = assess_order(
        intent,
        account,
        [],
        mismatch,
        DemoConfig(),
        now,
        calendar,
        security_records=[security_record()],
        adv={"A": 100000.0},
        target=approved_target(intent),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert not result.allowed and "quote_identity_mismatch" in result.reasons


def test_normal_order_requires_approved_target_and_positive_loss_baselines() -> None:
    """省略目标或损失基线不能静默关掉风控；独立减风险接口保持分离。"""
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    account = AccountSnapshot(as_of=now, cash=Decimal("100000"), available_cash=Decimal("100000"))
    intent = OrderIntent(
        client_order_id="context",
        account_id="DEMO",
        decision_id="d",
        security_id="A",
        side="BUY",
        quantity=40,
        limit_price=Decimal("101"),
        reserved_fee=Decimal("1"),
        created_at=now,
        eligible_at=now,
    )
    quotes = {"A": Quote(security_id="A", at=now, price=Decimal("100"))}
    context: dict[str, Any] = {
        "target": approved_target(intent),
        "reference_nav": Decimal("100000"),
        "peak_nav": Decimal("100000"),
        "security_records": [security_record()],
        "adv": {"A": 100000.0},
    }
    # 完整配置的独立样本必须通过，防止测试只证明永远拒绝。
    assert assess_order(intent, account, [], quotes, DemoConfig(), now, calendar, **context).allowed
    # 每个必需字段单独省略都应得到明确原因。
    for field, reason in (
        ("target", "missing_approved_target"),
        ("reference_nav", "missing_reference_nav"),
        ("peak_nav", "missing_peak_nav"),
    ):
        missing = {key: value for key, value in context.items() if key != field}
        result = assess_order(intent, account, [], quotes, DemoConfig(), now, calendar, **missing)
        assert not result.allowed and reason in result.reasons
    # 分别把两个净值基线设为零，验证无效分母不能静默跳过损失检查。
    for field, reason in (
        ("reference_nav", "invalid_reference_nav"),
        ("peak_nav", "invalid_peak_nav"),
    ):
        invalid = context | {field: Decimal("0")}
        result = assess_order(intent, account, [], quotes, DemoConfig(), now, calendar, **invalid)
        assert not result.allowed and reason in result.reasons
    # 借用另一个决策的目标不是有效授权。
    invalid_target = approved_target(intent).model_copy(update={"decision_id": "unrelated"})
    result = assess_order(
        intent,
        account,
        [],
        quotes,
        DemoConfig(),
        now,
        calendar,
        **(context | {"target": invalid_target}),
    )
    assert not result.allowed and "invalid_approved_target" in result.reasons
