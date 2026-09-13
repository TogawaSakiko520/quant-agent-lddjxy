"""提交前硬约束和独立减风险权限测试；失败不修改阈值或账户。"""

# 固定正常开盘时刻。
from datetime import UTC, date, datetime, timedelta

# 精确金额用于风险边界。
from decimal import Decimal

# 风险上下文测试显式模拟可选外部字典。
from typing import Any

# 显式行业夹具保证有效请求具备完整风险上下文。
from test_data import security_record

# 日历提供真实会话规则。
from quant_core.adapters.calendar import ExchangeCalendar

# 所有请求使用公开契约。
from quant_core.contracts import (
    AccountSnapshot,
    DemoConfig,
    OrderIntent,
    OrderRecord,
    Quote,
    TargetPortfolio,
    TargetPosition,
)

# 被测新增、撤单与减仓路径。
from quant_core.risk import assess_operation, assess_order, assess_reduce


def approved_target(intent: OrderIntent) -> TargetPortfolio:
    """建立明确数量授权且不计算风控期望；返回100000美元目标，无外部副作用。"""
    # 目标数量由固定订单样本独立声明，风险上限仍由配置另行检验。
    position = TargetPosition(
        security_id=intent.security_id,
        sector="tech",
        weight=0.05,
        quantity=intent.quantity,
        reason="fixed_approved_test_quantity",
    )
    # 同决策目标提供可审计的正常交易授权边界。
    return TargetPortfolio(
        decision_id=intent.decision_id,
        as_of=intent.created_at,
        nav=Decimal("100000"),
        positions=[position],
        cash_weight=0.95,
    )


def test_order_hard_checks_losses_and_unreconciled_state() -> None:
    """有效订单通过；过期报价、对账失败、缺资金和亏损分别拒绝，无外部副作用。"""
    # 固定纽约普通交易日开盘。
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    # 日历覆盖当前月份。
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    # 全现金账户有明确可用购买力。
    account = AccountSnapshot(as_of=now, cash=Decimal("100000"), available_cash=Decimal("100000"))
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
    # 原始报价100美元，不是研究复权价。
    quotes = {"A": Quote(security_id="A", at=now, price=Decimal("100"))}
    # 默认演示预算保持不变。
    config = DemoConfig()
    # 完整行业和流动性依据使合法订单可评估。
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
    # 合法固定样本必须通过所有现有规则。
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
    # 必须提供明确原因，不自动清仓。
    assert not failed.allowed and "unreconciled_account" in failed.reasons
    # 超过60秒的旧报价不得放行。
    stale = {"A": quotes["A"].model_copy(update={"at": now - timedelta(seconds=61)})}
    # 所有其他条件仍合法，隔离验证新鲜度。
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
    # 报价过期原因必须出现。
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
    # 不允许通过改阈值使新增通过。
    assert "daily_loss_limit" in loss.reasons
    # 可用现金只有100美元，即使总现金足够也不得占用未结算部分。
    limited = account.model_copy(update={"available_cash": Decimal("100")})
    # 使用明确购买力事实。
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
    # 不能拿总现金取代available_cash。
    assert "insufficient_available_cash" in cash_risk.reasons


def test_reduce_and_cancel_have_independent_controlled_paths() -> None:
    """冻结新增不等于盲目清仓；减仓需授权、已确认股数、正常执行条件，无副作用。"""
    # 同一固定普通交易时段。
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    # 日期范围明确有限。
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    # 实际确认10股，不能以未完成买单扩大可卖数。
    account = AccountSnapshot(
        as_of=now, cash=Decimal("99000"), available_cash=Decimal("99000"), positions={"A": 10}
    )
    # 10股卖单正好减至零。
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
    # 当前报价符合卖出最低价。
    quotes = {"A": Quote(security_id="A", at=now, price=Decimal("100"))}
    # 正常硬条件下独立减风险可以通过。
    result = assess_reduce(intent, account, [], quotes, DemoConfig(), now, calendar)
    # 允许本次减仓仍不授权新增风险。
    assert result.allowed and result.freeze_new_risk
    # 多卖一股将变空头，必须拒绝。
    excessive = assess_reduce(
        intent.model_copy(update={"quantity": 11}), account, [], quotes, DemoConfig(), now, calendar
    )
    # 独立路径不能突破确认持仓数量。
    assert "insufficient_confirmed_shares" in excessive.reasons
    # 对账失败时已知订单仍可受控撤单。
    assert assess_operation("CANCEL", reconciled=False, known_state=True).allowed
    # 同一状态不允许新增风险。
    assert not assess_operation("NEW", reconciled=False, known_state=True).allowed
    # 未授权撤单也不能通过紧急名义绕过。
    assert not assess_operation(
        "CANCEL", reconciled=False, known_state=True, authorized=False
    ).allowed


def test_pending_buy_limit_prices_and_unpaid_fees_bound_exposure() -> None:
    """独立复核样本：所有挂买单限价及未支付费用均参与最坏风险，无外部副作用。"""
    # 使用固定合格会话。
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    # 真实有限日历隔离时间条件。
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    # 全现金100000美元账户。
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
    # 已接受挂单尚未成交。
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
    # A当前报价极低，故意暴露仅用现价估算挂单的漏洞。
    quotes = {
        "A": Quote(security_id="A", at=now, price=Decimal("1")),
        "B": Quote(security_id="B", at=now, price=Decimal("100")),
    }
    # 独立配置使这次失败只针对总仓位预算。
    config = DemoConfig(max_gross=0.1, max_single=0.1, max_sector=1.0)
    # 所有其他输入合法且新鲜。
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
    # 最坏11000美元不允许通过10000美元预算。
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
    # 手续费不能被忽略以获得边界通过。
    assert not fee_result.allowed and "single_position_limit" in fee_result.reasons


def test_current_security_eligibility_and_quote_identity_are_enforced() -> None:
    """主表停牌、退市、隔离、未来或失效均禁止新买；报价证券身份必须匹配，无副作用。"""
    # 固定执行时刻。
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    # 时段合法，隔离其他输入的影响。
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    # 全现金账户。
    account = AccountSnapshot(as_of=now, cash=Decimal("100000"), available_cash=Decimal("100000"))
    # 合法小额买单不会触碰普通额度。
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
    # 不允许任一主表异常被quote.tradable=True掩盖。
    for master in invalid_masters:
        # 每次只改变一个主表条件。
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
        # 资格失败必须阻断具体新买单。
        assert not result.allowed and "security_not_eligible" in result.reasons
    # 字典键与消息身份冲突属于错证券价格。
    mismatch = {"A": Quote(security_id="DIFFERENT", at=now, price=Decimal("100"))}
    # 报价存在并不等于对应本次证券。
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
    # 禁止把其他证券的价格用于本证券下单。
    assert not result.allowed and "quote_identity_mismatch" in result.reasons


def test_normal_order_requires_approved_target_and_positive_loss_baselines() -> None:
    """省略目标或损失基线不能静默关掉风控；独立减风险接口保持分离，无副作用。"""
    # 固定有效执行时刻。
    now = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)
    # 日历覆盖正常交易日。
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31))
    # 全现金固定账户。
    account = AccountSnapshot(as_of=now, cash=Decimal("100000"), available_cash=Decimal("100000"))
    # 数量和价格本身都在合法演示预算内。
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
    # 明确新鲜原始报价。
    quotes = {"A": Quote(security_id="A", at=now, price=Decimal("100"))}
    # 合法上下文完整提供所有必需授权与风险输入。
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
        # 复制固定夹具，不改被测生产风险参数。
        missing = {key: value for key, value in context.items() if key != field}
        # 仅移除一个关键上下文隔离该检查。
        result = assess_order(intent, account, [], quotes, DemoConfig(), now, calendar, **missing)
        # 缺事实必须冻结新增，不能当作无需检查。
        assert not result.allowed and reason in result.reasons
    # 零和负数基线不能使损失比例检查无意义。
    for field, reason in (
        ("reference_nav", "invalid_reference_nav"),
        ("peak_nav", "invalid_peak_nav"),
    ):
        # 注入明确无效的零净值。
        invalid = context | {field: Decimal("0")}
        # 正常策略入口必须失败闭合。
        result = assess_order(intent, account, [], quotes, DemoConfig(), now, calendar, **invalid)
        # 保留具体机器可读原因。
        assert not result.allowed and reason in result.reasons
    # 借用另一个决策的目标不是有效授权。
    invalid_target = approved_target(intent).model_copy(update={"decision_id": "unrelated"})
    # 目标存在也必须绑定当前订单身份。
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
    # 别的决策不能授予当前订单执行权。
    assert not result.allowed and "invalid_approved_target" in result.reasons
