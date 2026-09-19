"""监控告警的独立固定样本；验证故障不会被正常日报或自动补单掩盖。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quant_core.contracts import (
    AccountSnapshot,
    OrderIntent,
    OrderRecord,
    ReconciliationResult,
    TargetPortfolio,
    TargetPosition,
)
from quant_core.monitoring import assess_health

AT = datetime(2023, 11, 27, 14, 30, tzinfo=UTC)


def test_health_preserves_all_independent_failure_reasons() -> None:
    """多故障同现时每项都必须输出，不允许只显示最后一条或自动假装目标完成。"""
    # 当前账户没有目标持仓，金额为一千美元。
    account = AccountSnapshot(as_of=AT, cash=Decimal("1000"), available_cash=Decimal("1000"))
    # 目标希望持有 A 十股、对应50美元/净值1000=5%；实际账户仍为0股。
    # 后面的 UNKNOWN 买单也不能代替已成交持仓，所以目标偏离应单独报警。
    target = TargetPortfolio(
        decision_id="decision",
        as_of=AT,
        nav=Decimal("1000"),
        positions=[
            TargetPosition(
                security_id="A", sector="tech", weight=0.05, quantity=10, reason="fixture"
            )
        ],
        cash_weight=0.95,
    )
    # 一个已持久化但无法确认的订单需要独立错误告警。
    intent = OrderIntent(
        client_order_id="order",
        account_id="DEMO",
        decision_id="decision",
        security_id="A",
        side="BUY",
        quantity=10,
        limit_price=Decimal("5"),
        created_at=AT,
        eligible_at=AT,
    )
    orders = [OrderRecord(intent=intent, status="UNKNOWN")]
    reconciliation = ReconciliationResult(as_of=AT, matched=False, differences=["cash"])
    # 心跳61秒/来源偏移31秒分别刚越过60秒/30秒阈值；50%因子覆盖低于80%。
    # completed_steps 是已完成阶段名称集合，空集合表示所有必需阶段都缺失。
    # 注入任务缺步、数据失败、过期心跳、时钟偏移和覆盖下降。
    alerts = assess_health(
        account,
        target,
        reconciliation,
        orders,
        at=AT,
        heartbeat_at=AT - timedelta(seconds=61),
        provider_at=AT - timedelta(seconds=31),
        data_good=False,
        factor_coverage=0.5,
        completed_steps=set(),
    )
    # 期望来自八类独立故障，不由实现输出动态构造。
    assert {item.code for item in alerts} == {
        "INCOMPLETE_RUN",
        "DATA_QUALITY",
        "HEARTBEAT",
        "CLOCK_SKEW",
        "RECONCILIATION",
        "ORDER_ERROR",
        "FACTOR_COVERAGE",
        "TARGET_DEVIATION",
    }
    # 首版没有值守人，不得伪造告警已经确认。
    assert all(item.owner is None and item.acknowledged_at is None for item in alerts)
    # 监控不允许悄悄修改输入持仓或现金。
    assert account.positions == {} and account.cash == Decimal("1000")


def test_healthy_run_has_explicit_non_actionable_observation() -> None:
    """没有故障仍要证明检查运行过，info不能误作关键故障通知。"""
    # 账户与全现金目标完全一致。
    account = AccountSnapshot(as_of=AT, cash=Decimal("1000"), available_cash=Decimal("1000"))
    target = TargetPortfolio(
        decision_id="decision", as_of=AT, nav=Decimal("1000"), positions=[], cash_weight=1.0
    )
    reconciliation = ReconciliationResult(as_of=AT, matched=True, differences=[])
    alerts = assess_health(
        account,
        target,
        reconciliation,
        [],
        at=AT,
        heartbeat_at=AT,
        provider_at=AT,
        data_good=True,
        factor_coverage=1.0,
        completed_steps={"data", "factors", "portfolio", "execution", "reconciliation"},
    )
    assert [(item.code, item.severity) for item in alerts] == [("HEALTHY", "info")]
