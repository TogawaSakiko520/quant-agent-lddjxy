"""MA 策略名额与未知行业最坏暴露的独立手算回归；不访问外部账户。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from test_data import security_record

from quant_core.adapters.calendar import ExchangeCalendar
from quant_core.contracts import (
    AccountSnapshot,
    OrderIntent,
    OrderRecord,
    Quote,
    Score,
    SecurityRecord,
    SignalSet,
    StrategyConfig,
    TargetPortfolio,
    TargetPosition,
)
from quant_core.portfolio import (
    build_portfolio,
    plan_orders,
    resolve_security_records,
    sector_committed,
    sector_exposure_peak,
)
from quant_core.risk import assess_order

AT = datetime(2023, 1, 9, 14, 30, tzinfo=UTC)


def master(sid: str, sector: str | None) -> SecurityRecord:
    """提供已知普通股身份与明确的行业缺失，不生成占位行业。"""
    return SecurityRecord.model_validate(security_record(sid).model_dump() | {"sector": sector})


def test_unknown_sector_selection_is_explicit_and_still_checks_versions() -> None:
    """只有 MA 显式允许 None；空字符串、未来主表与冲突仍不能进入风险估值。"""
    unknown = master("A", None)
    assert resolve_security_records([unknown], AT) == {}
    assert resolve_security_records([unknown], AT, allow_unknown_sector=True) == {"A": unknown}
    for invalid in (
        unknown.model_copy(update={"sector": " "}),
        unknown.model_copy(update={"available_at": AT + timedelta(seconds=1)}),
        unknown.model_copy(update={"quality": "quarantined"}),
    ):
        assert resolve_security_records([invalid], AT, allow_unknown_sector=True) == {}
    assert (
        resolve_security_records(
            [unknown, unknown.model_copy(update={"sector": "tech"})], AT, allow_unknown_sector=True
        )
        == {}
    )


def test_unknown_sector_is_added_to_every_known_sector_not_a_separate_bucket() -> None:
    """科技400、金融300、未知200的最大行业占用为600；新增未知须使用该600。"""
    totals = {"tech": Decimal("400"), "finance": Decimal("300"), None: Decimal("200")}
    assert sector_exposure_peak(totals) == Decimal("600")
    assert sector_committed(totals, None) == Decimal("600")
    assert sector_committed(totals, "finance") == Decimal("500")
    assert sector_committed(totals, "new-sector") == Decimal("200")
    assert sector_exposure_peak({None: Decimal("200")}) == Decimal("200")
    assert sector_exposure_peak({}) == Decimal("0")


def test_ma_selects_three_feasible_targets_without_redistributing_cash() -> None:
    """首位600美元买不起一股后继续排名；其后三只各4股，现金仍为8800美元。"""
    masters = [master(sid, None) for sid in ("A", "B", "C", "D", "E")]
    signals = SignalSet(
        decision_id="ma-d",
        decision_time=AT,
        snapshot_id="ma-snapshot",
        scores=[
            Score(security_id=record.security_id, sector=None, components={"ma_trend": 1}, value=1)
            for record in masters
        ],
    )
    account = AccountSnapshot(as_of=AT, cash=Decimal("10000"), available_cash=Decimal("10000"))
    quotes = {
        record.security_id: Quote(
            security_id=record.security_id,
            at=AT,
            price=Decimal("600") if record.security_id == "A" else Decimal("100"),
        )
        for record in masters
    }
    config = StrategyConfig(account_id="DEMO", strategy="ma-trend")
    target = build_portfolio(signals, account, quotes, masters, config)
    # 基础额度10000×4.5%=450，100美元整股向下取整为4股，而非按3只均分预算。
    assert [(row.security_id, row.quantity, row.sector) for row in target.positions] == [
        ("B", 4, None),
        ("C", 4, None),
        ("D", 4, None),
    ]
    assert target.cash_weight == pytest.approx(0.88)
    assert "constraint_or_rounding_cash:A" in target.reasons
    # 相同缺行业输入进入旧策略仍无目标。
    legacy = build_portfolio(signals, account, quotes, masters, StrategyConfig(account_id="DEMO"))
    assert legacy.positions == []


@pytest.mark.parametrize("candidate_sector", [None, "tech"])
def test_retained_unknown_position_uses_candidate_sector_room(candidate_sector: str | None) -> None:
    """未知旧仓400加新目标最多100，达到更严格5%行业上限；不按两个行业分摊。"""
    old = master("OLD", None).model_copy(update={"tradable": False})
    candidate = master("NEW", candidate_sector)
    account = AccountSnapshot(
        as_of=AT, cash=Decimal("9600"), available_cash=Decimal("9600"), positions={"OLD": 4}
    )
    quotes = {sid: Quote(security_id=sid, at=AT, price=Decimal("100")) for sid in ("OLD", "NEW")}
    signal = SignalSet(
        decision_id="d",
        decision_time=AT,
        snapshot_id="s",
        scores=[
            Score(security_id="NEW", sector=candidate_sector, components={"ma_trend": 1}, value=1)
        ],
    )
    # 特意采用比默认25%更严格的5%边界，隔离验证未知行业；其他参数保持默认。
    config = StrategyConfig(account_id="DEMO", strategy="ma-trend", max_sector=0.05)
    target = build_portfolio(signal, account, quotes, [old, candidate], config)
    assert [(row.security_id, row.quantity) for row in target.positions] == [("OLD", 4), ("NEW", 1)]
    assert target.positions[0].sector is None


def risk_case(
    candidate_sector: str | None = None,
) -> tuple[
    TargetPortfolio,
    AccountSnapshot,
    list[OrderRecord],
    dict[str, Quote],
    list[SecurityRecord],
    StrategyConfig,
]:
    """构造300美元科技旧仓、200美元未知挂买及最多400美元新目标。

    账户净值10000美元，单票都小于默认5%。采用更严格8%行业上限，方便在三只
    名额内单独触及未知行业约束；挂单与新单各预留1美元费用。
    """
    account = AccountSnapshot(
        as_of=AT, cash=Decimal("9700"), available_cash=Decimal("9700"), positions={"A": 3}
    )
    quotes = {sid: Quote(security_id=sid, at=AT, price=Decimal("100")) for sid in ("A", "B", "C")}
    masters = [master("A", "tech"), master("B", None), master("C", candidate_sector)]
    pending = OrderRecord(
        intent=OrderIntent(
            client_order_id="pending",
            account_id="DEMO",
            decision_id="d",
            security_id="B",
            side="BUY",
            quantity=2,
            limit_price=Decimal("100"),
            reserved_fee=Decimal("1"),
            created_at=AT,
            eligible_at=AT,
        ),
        status="OPEN",
        broker_order_id="broker-pending",
    )
    target = TargetPortfolio(
        decision_id="d",
        as_of=AT,
        nav=Decimal("10000"),
        cash_weight=0.91,
        positions=[
            TargetPosition(
                security_id=sid,
                sector=sector,
                quantity=quantity,
                weight=weight,
                reason="fixed_input",
            )
            for sid, sector, quantity, weight in [
                ("A", "tech", 3, 0.03),
                ("B", None, 2, 0.02),
                ("C", candidate_sector, 4, 0.04),
            ]
        ],
    )
    config = StrategyConfig(account_id="DEMO", strategy="ma-trend", max_sector=0.08)
    return target, account, [pending], quotes, masters, config


@pytest.mark.parametrize("sector,expected", [(None, 2), ("tech", 2), ("finance", 4)])
def test_planning_reserves_unknown_holdings_and_pending_buys(
    sector: str | None, expected: int
) -> None:
    """未知或科技候选只剩约299美元行业空间，金融候选另有空间但仍计未知200。"""
    target, account, pending, quotes, masters, config = risk_case(sector)
    intents = plan_orders(
        target,
        account,
        pending,
        quotes,
        dict.fromkeys(quotes, 1000000.0),
        config,
        AT,
        security_records=masters,
    )
    # 101美元限价；未知/科技空间(9999×8%−500−1)=298.92，可买2股；金融可完成4股目标。
    assert [(intent.security_id, intent.quantity) for intent in intents] == [("C", expected)]


@pytest.mark.parametrize("quantity,allowed", [(2, True), (3, False)])
def test_submission_uses_same_worst_case_industry_and_fee_denominator(
    quantity: int, allowed: bool
) -> None:
    """两股后700低于799.84可通过；三股后800超过扣两笔费用后的8%上限。"""
    target, account, pending, quotes, masters, config = risk_case()
    intent = pending[0].intent.model_copy(
        update={"client_order_id": "new", "security_id": "C", "quantity": quantity}
    )
    result = assess_order(
        intent,
        account,
        pending,
        quotes,
        config,
        AT,
        ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31)),
        target=target,
        security_records=masters,
        adv={"C": 1000000.0},
        reference_nav=Decimal("10000"),
        peak_nav=Decimal("10000"),
    )
    assert result.allowed is allowed
    assert ("sector_exposure_limit" in result.reasons) is (not allowed)


def test_missing_master_is_not_equivalent_to_explicit_unknown_sector() -> None:
    """MA 允许已确认证券的未知行业，不允许缺失挂单证券身份而继续规划或提交。"""
    target, account, pending, quotes, masters, config = risk_case()
    masters = [row for row in masters if row.security_id != "B"]
    assert (
        plan_orders(
            target,
            account,
            pending,
            quotes,
            dict.fromkeys(quotes, 1000000.0),
            config,
            AT,
            security_records=masters,
        )
        == []
    )
    intent = pending[0].intent.model_copy(
        update={"client_order_id": "new", "security_id": "C", "quantity": 1}
    )
    result = assess_order(
        intent,
        account,
        pending,
        quotes,
        config,
        AT,
        ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31)),
        target=target,
        security_records=masters,
        adv={"C": 1000000.0},
        reference_nav=Decimal("10000"),
        peak_nav=Decimal("10000"),
    )
    assert not result.allowed
    assert "missing_sector_classification" in result.reasons


def test_ma_three_position_limit_counts_pending_buys() -> None:
    """两只旧仓加一只挂买已占三名额，第四只即使有现金与目标仍拒绝。"""
    target, account, pending, quotes, masters, config = risk_case("finance")
    account = account.model_copy(
        update={
            "cash": Decimal("9600"),
            "available_cash": Decimal("9600"),
            "positions": {"A": 3, "D": 1},
        }
    )
    quotes["D"] = Quote(security_id="D", at=AT, price=Decimal("100"))
    masters.append(master("D", "energy"))
    # 保留D的目标避免触发卖出规划，使反例只观察新增C的名额限制。
    target = target.model_copy(
        update={
            "positions": target.positions
            + [
                TargetPosition(
                    security_id="D", sector="energy", quantity=1, weight=0.01, reason="retained"
                )
            ]
        }
    )
    assert (
        plan_orders(
            target,
            account,
            pending,
            quotes,
            dict.fromkeys(quotes, 1000000.0),
            config,
            AT,
            security_records=masters,
        )
        == []
    )
    intent = pending[0].intent.model_copy(
        update={"client_order_id": "new", "security_id": "C", "quantity": 1}
    )
    result = assess_order(
        intent,
        account,
        pending,
        quotes,
        config,
        AT,
        ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31)),
        target=target,
        security_records=masters,
        adv={"C": 1000000.0},
        reference_nav=Decimal("10000"),
        peak_nav=Decimal("10000"),
    )
    assert not result.allowed
    assert "position_count_limit" in result.reasons


def test_partial_pending_buy_counts_only_remaining_shares_once() -> None:
    """未知挂买已成交一股进入账户，另余一股；实际加未成交仍只占200美元。"""
    target, account, pending, quotes, masters, config = risk_case()
    account = account.model_copy(
        update={
            "cash": Decimal("9600"),
            "available_cash": Decimal("9600"),
            "positions": {"A": 3, "B": 1},
        }
    )
    pending = [pending[0].model_copy(update={"filled_quantity": 1, "status": "PARTIAL"})]
    intents = plan_orders(
        target,
        account,
        pending,
        quotes,
        dict.fromkeys(quotes, 1000000.0),
        config,
        AT,
        security_records=masters,
        turnover_used=Decimal("100"),
    )
    assert [(intent.security_id, intent.quantity) for intent in intents] == [("C", 2)]
    intent = pending[0].intent.model_copy(update={"client_order_id": "new", "security_id": "C"})
    result = assess_order(
        intent,
        account,
        pending,
        quotes,
        config,
        AT,
        ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31)),
        target=target,
        security_records=masters,
        adv={"C": 1000000.0},
        turnover_used=Decimal("100"),
        reference_nav=Decimal("10000"),
        peak_nav=Decimal("10000"),
    )
    # 300科技 + (100已成交B + 100未成交B + 200新C) = 700，不能重复算累计成交。
    assert result.allowed


def test_legacy_planning_and_submit_reject_explicit_unknown_sector() -> None:
    """同一资料在双因子下仍缺合格行业，不因公共字段放宽为可空而放行。"""
    target, account, pending, quotes, masters, _ = risk_case()
    config = StrategyConfig(account_id="DEMO")
    assert (
        plan_orders(
            target,
            account,
            pending,
            quotes,
            dict.fromkeys(quotes, 1000000.0),
            config,
            AT,
            security_records=masters,
        )
        == []
    )
    intent = pending[0].intent.model_copy(
        update={"client_order_id": "new", "security_id": "C", "quantity": 1}
    )
    result = assess_order(
        intent,
        account,
        pending,
        quotes,
        config,
        AT,
        ExchangeCalendar(date(2023, 1, 1), date(2023, 1, 31)),
        target=target,
        security_records=masters,
        adv={"C": 1000000.0},
        reference_nav=Decimal("10000"),
        peak_nav=Decimal("10000"),
    )
    assert not result.allowed
    assert "security_not_eligible" in result.reasons
    assert "missing_sector_classification" in result.reasons


def test_ma_order_label_matches_strategy_without_changing_stable_identity() -> None:
    """MA 意图保存自身版本，稳定ID仍取决策、账户、证券、方向和既有/目标股数。"""
    target, account, pending, quotes, masters, config = risk_case()
    intents = plan_orders(
        target,
        account,
        pending,
        quotes,
        dict.fromkeys(quotes, 1000000.0),
        config,
        AT,
        security_records=masters,
    )
    assert len(intents) == 1
    assert intents[0].strategy_version == "ma-trend-1.0.0"
    # 该固定哈希独立来自d/DEMO/C/BUY/0/4，报价、实际缩量和展示标签不改变既有身份规则。
    assert (
        intents[0].client_order_id
        == "42f4c6feee99fd993e6d451ed5313fbe32f62639b956b3fa73a76284b90e37fc"
    )
    # 同一意图已经提交时，重复规划只扣挂买余量，不产生新的C订单。
    repeated = plan_orders(
        target,
        account,
        pending + [OrderRecord(intent=intents[0], status="OPEN", broker_order_id="c")],
        quotes,
        dict.fromkeys(quotes, 1000000.0),
        config,
        AT,
        security_records=masters,
    )
    assert repeated == []
