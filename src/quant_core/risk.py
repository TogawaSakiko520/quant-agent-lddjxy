"""分类风控纯规则；新增、撤单、减仓分别判断，禁止失败时盲目清仓。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    DemoConfig,
    OrderIntent,
    OrderRecord,
    Quote,
    RiskDecision,
    SecurityRecord,
    TargetPortfolio,
    TradingCalendar,
)
from quant_core.portfolio import ACTIVE_STATUSES, account_nav, order_fee


def assess_operation(
    operation: Literal["NEW", "CANCEL", "REPLACE", "REDUCE"],
    *,
    reconciled: bool,
    known_state: bool,
    authorized: bool = True,
) -> RiskDecision:
    """判断操作的授权与恢复前提；返回权限结论，不代替具体订单的价格和数量检查。"""
    reasons: list[str] = []
    # 减风险路径也不能绕过明确授权。
    if not authorized:
        reasons.append("unauthorized_operation")
    # 未知订单状态必须先恢复，避免撤错单或重复操作。
    if not known_state:
        reasons.append("unknown_order_state")
    # 对账失败阻止新增或改单，但不自动剥夺可审计撤单路径。
    if operation in {"NEW", "REPLACE"} and not reconciled:
        reasons.append("unreconciled_account")
    return RiskDecision(
        allowed=not reasons,
        operation=operation,
        freeze_new_risk=not reconciled or not known_state or not authorized,
        reasons=reasons,
    )


def _hard_checks(
    intent: OrderIntent,
    account: AccountSnapshot,
    open_orders: list[OrderRecord],
    quotes: dict[str, Quote],
    config: DemoConfig,
    now: datetime,
    calendar: TradingCalendar,
) -> list[str]:
    """收集新增和减仓共用的账户、时段、原始报价及可卖数量阻断原因。"""
    reasons: list[str] = []
    # 仅接受明确离线白名单账户。
    if intent.account_id != config.account_id or account.account_id != config.account_id:
        reasons.append("account_not_whitelisted")
    # 不接受未来账户事实或过期账户快照。
    if account.as_of > now or (now - account.as_of).total_seconds() > config.quote_max_age_seconds:
        reasons.append("stale_or_future_account")
    # 只有常规时段且达到订单资格时刻才可执行。
    if now < intent.eligible_at or now < intent.created_at or not calendar.is_open(now):
        reasons.append("outside_execution_session")
    # others 保留除本 client_order_id 外的历史订单（包括终态）；后续按活动状态
    # 扣可卖量。当前意图可能已落盘，因此必须先排除自身，避免一张订单扣两次。
    others = [
        record for record in open_orders if record.intent.client_order_id != intent.client_order_id
    ]
    if any(record.status == "UNKNOWN" for record in others):
        reasons.append("unknown_order_state")
    # 报价字典的键不能覆盖消息内的真实证券身份。
    if any(security_id != value.security_id for security_id, value in quotes.items()):
        reasons.append("quote_identity_mismatch")
    quote = quotes.get(intent.security_id)
    # 缺报价不允许以复权研究价替代。
    if quote is None:
        reasons.append("missing_quote")
    else:
        if quote.at > now or (now - quote.at).total_seconds() > config.quote_max_age_seconds:
            reasons.append("stale_or_future_quote")
        if not quote.tradable:
            reasons.append("untradable_quote")
        # 限价必须覆盖当前报价，离线示例不猜测未来穿价。
        if (intent.side == "BUY" and quote.price > intent.limit_price) or (
            intent.side == "SELL" and quote.price < intent.limit_price
        ):
            reasons.append("quote_outside_limit")
    # 负持仓属于当前长仓契约不支持的账户状态。
    if any(quantity < 0 for quantity in account.positions.values()):
        reasons.append("unexpected_short_position")
    # 累计成交不能大于委托数量。
    if any(record.filled_quantity > record.intent.quantity for record in others):
        reasons.append("invalid_order_quantity")
    # 卖单必须扣除其他尚未完成的卖单。
    if intent.side == "SELL":
        # available_quantity 是此刻尚未被其他卖单占用的整股数，初值只取实际持仓。
        # get 的零表示没有确认持仓；待成交买单不能作为卖出依据。
        available_quantity = account.positions.get(intent.security_id, 0)
        for record in others:
            if (
                record.status in ACTIVE_STATUSES
                and record.intent.security_id == intent.security_id
                and record.intent.side == "SELL"
            ):
                # 撤单中仍预留剩余可卖股数。
                available_quantity -= record.intent.quantity - record.filled_quantity
        # 减仓不能穿过零成为卖空。
        if intent.quantity > available_quantity:
            reasons.append("insufficient_confirmed_shares")
    # 每单费用预留不能低于已配置费用模型。
    if intent.reserved_fee < order_fee(intent.quantity, config):
        reasons.append("insufficient_fee_reserve")
    return reasons


def assess_reduce(
    intent: OrderIntent,
    account: AccountSnapshot,
    open_orders: list[OrderRecord],
    quotes: dict[str, Quote],
    config: DemoConfig,
    now: datetime,
    calendar: TradingCalendar,
    *,
    authorized: bool = True,
) -> RiskDecision:
    """核验单独授权的卖出减仓；不要求正常策略数据可用，但仍检查账户、时段和数量。"""
    # 减风险操作不能以BUY名义扩大多头仓位。
    reasons = [] if intent.side == "SELL" else ["reduce_requires_sell"]
    if not authorized:
        reasons.append("unauthorized_operation")
    # 复用同一份硬约束，防止两套风控漂移。
    reasons.extend(_hard_checks(intent, account, open_orders, quotes, config, now, calendar))
    # 无论减仓是否允许，当前路径都不授权新增风险。
    return RiskDecision(
        allowed=not reasons, operation="REDUCE", freeze_new_risk=True, reasons=reasons
    )


def assess_order(
    intent: OrderIntent,
    account: AccountSnapshot,
    open_orders: list[OrderRecord],
    quotes: dict[str, Quote],
    config: DemoConfig,
    now: datetime,
    calendar: TradingCalendar,
    *,
    reconciled: bool = True,
    data_good: bool = True,
    reference_nav: Decimal | None = None,
    peak_nav: Decimal | None = None,
    target: TargetPortfolio | None = None,
    security_records: list[SecurityRecord] | None = None,
    adv: dict[str, float] | None = None,
    turnover_used: Decimal | None = None,
) -> RiskDecision:
    """核验正常策略订单的目标、预算和交易资格，返回允许结论及拒绝原因。

    now 为注入的 UTC 执行时刻，报价必须是新鲜原始价格。reference_nav 是日内
    损失基线、peak_nav 是回撤高点，金额均为美元且正常提交要求正值。target
    提供批准目标与换手分母；已有成交时 turnover_used 必须给出真实成交总额。
    adv 是此前 20 日平均成交股数，security_records 应由调用方完成历史版本选择。
    估值 ContractError 转为拒绝原因，函数不写账户也不发单。

    本函数仅对当前买入证券的交易资格筛主表时点；下方行业聚合仍直接采用传入
    主表，存在已记录的 F01 未来行业版本缺陷，不能把此结果当作完整时点保证。
    """
    # 普通提交即使方向为卖出也先要求正常链路；紧急减仓另有接口。
    reasons = _hard_checks(intent, account, open_orders, quotes, config, now, calendar)
    if not reconciled:
        reasons.append("unreconciled_account")
    if not data_good:
        reasons.append("data_quality_failed")
    # 正常策略订单必须绑定明确批准目标，不能靠省略上下文跳过检查。
    if target is None:
        reasons.append("missing_approved_target")
    # 目标身份、时点与净值必须符合当前意图。
    elif target.decision_id != intent.decision_id or target.as_of > now or target.nav <= 0:
        reasons.append("invalid_approved_target")
    # 日内基线缺失不能静默禁用亏损检查。
    if reference_nav is None:
        reasons.append("missing_reference_nav")
    elif reference_nav <= 0:
        reasons.append("invalid_reference_nav")
    # 回撤高点同样是正常交易的必需风险上下文。
    if peak_nav is None:
        reasons.append("missing_peak_nav")
    elif peak_nav <= 0:
        reasons.append("invalid_peak_nav")
    # others 用客户端订单身份排除本单，保留历史记录以检查成交额是否需要显式注入；
    # 只有其中的活动单才在下文继续占用资金与持仓额度。
    others = [
        record for record in open_orders if record.intent.client_order_id != intent.client_order_id
    ]
    # 估值失败以拒绝原因返回，而非绕过检查。
    try:
        # nav 是执行时实际账户的美元净值，不是批准目标的净值，也尚未扣预留费用。
        nav = account_nav(account, quotes)
    except ContractError:
        return RiskDecision(
            allowed=False,
            operation="NEW",
            freeze_new_risk=True,
            reasons=reasons + ["account_valuation_failed"],
        )
    # denominator 为换手比例的美元分母，正常路径取固定决策净值 target.nav。
    # 缺目标时虽取后备值完成诊断，前面的 missing_approved_target 已保证本单不能获准。
    denominator = target.nav if target is not None else reference_nav or nav
    # 换手为买卖成交总额（gross turnover），缺真实金额时不能用卖单最低限价替代。
    if turnover_used is None and any(
        record.intent.decision_id == intent.decision_id and record.filled_quantity
        for record in others
    ):
        reasons.append("missing_realized_turnover")
    if turnover_used is not None and turnover_used < 0:
        reasons.append("invalid_realized_turnover")
    # committed_turnover 从注入的本决策成交总额起步，再加活动单与本单的未成交金额。
    # 这是已用/已承诺的美元交易总额，不是账户现金支出；卖出也增加这个累计值。
    committed_turnover = max(Decimal("0"), turnover_used or Decimal("0"))
    # 买入预算来自券商明确确认的可用现金。
    available_cash = account.available_cash
    # projected 是证券 ID→用于风控的整股数：复制实际仓位，加挂买余量，再加本次买单。
    # 它不是账务预测结果；刻意不减挂卖余量，因为卖单尚未成交就不能释放额度。
    projected = dict(account.positions)
    # projected_prices 是对应证券→美元风险单价，从原始现价起步再上调至挂买限价。
    projected_prices = {security_id: quote.price for security_id, quote in quotes.items()}
    # 本单及其他未完成订单尚需支付的费用降低最坏净值。
    unpaid_fees = intent.reserved_fee
    for record in others:
        if record.status in ACTIVE_STATUSES:
            # remaining 是这张活动订单的未成交整股余量，已成交数量已计入 account。
            remaining = record.intent.quantity - record.filled_quantity
            # 挂单占用总换手预算，不按方向抵销。
            committed_turnover += (
                max(
                    record.intent.limit_price,
                    quotes[record.intent.security_id].price
                    if record.intent.security_id in quotes
                    else record.intent.limit_price,
                )
                * remaining
            )
            # 没有完整费用分摊记录时保守预留活动整单费用，不低估支出。
            unpaid_fees += record.intent.reserved_fee
            # 买单预留现金并增加最坏风险暴露。
            if record.intent.side == "BUY":
                available_cash -= record.intent.limit_price * remaining + record.intent.reserved_fee
                projected[record.intent.security_id] = (
                    projected.get(record.intent.security_id, 0) + remaining
                )
                # 活动买单可能在其最高限价成交，不能仅按较低现价估值。
                projected_prices[record.intent.security_id] = max(
                    projected_prices.get(record.intent.security_id, Decimal("0")),
                    record.intent.limit_price,
                )
    # 把本单按现价与限价的较高值加入买卖总额，不能用买卖净额抵销。
    committed_turnover += (
        max(
            intent.limit_price,
            quotes[intent.security_id].price
            if intent.security_id in quotes
            else intent.limit_price,
        )
        * intent.quantity
    )
    # 单轮买卖总额上限不能用净买卖额替代。
    if denominator <= 0 or committed_turnover > denominator * Decimal(str(config.max_turnover)):
        reasons.append("turnover_limit")
    # exposure_nav 是 nav 减本单及活动单费用后的美元分母，用于单票/行业/总仓位上限。
    # 整单费用保守预留，即使部分成交已计过部分费用，也不在缺分摊证据时自行减免。
    exposure_nav = nav - unpaid_fees
    if exposure_nav <= 0:
        reasons.append("nonpositive_after_fee_nav")
    if intent.side == "BUY":
        # 执行时主表资格必须重新核验，目标行业字段不等于交易资格。
        market_day = now.astimezone(ZoneInfo("America/New_York")).date()
        # 仅当时已知且当前有效的主表可作为身份依据。
        current_masters = [
            record
            for record in security_records or []
            if record.security_id == intent.security_id
            and record.available_at <= now
            and record.event_time <= now
            and record.effective_from <= market_day
            and (record.effective_to is None or market_day < record.effective_to)
        ]
        # 缺失、重叠、退市、停牌、隔离和非普通股均不能新增买入。
        if (
            len(current_masters) != 1
            or not current_masters[0].listed
            or not current_masters[0].tradable
            or current_masters[0].quality != "good"
            or current_masters[0].asset_type != "common_stock"
        ):
            reasons.append("security_not_eligible")
        # 禁止以未成交卖单补足购买力。
        if intent.quantity * intent.limit_price + intent.reserved_fee > available_cash:
            reasons.append("insufficient_available_cash")
        projected[intent.security_id] = projected.get(intent.security_id, 0) + intent.quantity
        # 当前意图限价与之前同证券挂买单共同确定最高风险价格。
        projected_prices[intent.security_id] = max(
            projected_prices.get(intent.security_id, Decimal("0")), intent.limit_price
        )
        if reference_nav is not None and nav <= reference_nav * (
            Decimal("1") - Decimal(str(config.daily_loss_limit))
        ):
            # 该阈值不保证实际亏损被封顶。
            reasons.append("daily_loss_limit")
        # 高点回撤与日内损失分别控制。
        if peak_nav is not None and nav <= peak_nav * (
            Decimal("1") - Decimal(str(config.drawdown_limit))
        ):
            reasons.append("drawdown_limit")
        # 全部旧仓与挂买单估值也必须使用当下可知的新鲜报价。
        if any(
            quantity > 0
            and sid in quotes
            and (
                quotes[sid].at > now
                or (now - quotes[sid].at).total_seconds() > config.quote_max_age_seconds
            )
            for sid, quantity in projected.items()
        ):
            reasons.append("stale_projected_position_quote")
        # 持仓数按实际加未完成买单计算。
        if sum(quantity > 0 for quantity in projected.values()) > config.max_positions:
            reasons.append("position_count_limit")
        # 需要全部最坏持仓的原始价格。
        if any(
            quantity and security_id not in quotes for security_id, quantity in projected.items()
        ):
            reasons.append("projected_position_missing_quote")
        else:
            # notionals 是证券 ID→最坏美元市值，本处已包含当前买单数量；与组合规划
            # 中“先求本单剩余空间”的 notionals 不同，这里直接核验加单后的整体额度。
            notionals = {
                security_id: quantity * projected_prices[security_id]
                for security_id, quantity in projected.items()
                if quantity > 0
            }
            # 总仓位不得靠未成交卖单假设下降。
            if sum(notionals.values(), Decimal("0")) > exposure_nav * Decimal(
                str(config.max_gross)
            ):
                reasons.append("gross_exposure_limit")
            # 单票检查包括旧仓和挂单。
            if any(
                amount > exposure_nav * Decimal(str(config.max_single))
                for amount in notionals.values()
            ):
                reasons.append("single_position_limit")
            # 此处直接读取传入主表，未复用上面的时点过滤；未来版本可覆盖行业（F01）。
            sectors = {security.security_id: security.sector for security in security_records or []}
            # 目标携带的行业可补充当前入选证券。
            if target is not None:
                for position in target.positions:
                    # setdefault 只补主表没有的证券行业，不覆盖已存在分类；不修复 F01。
                    sectors.setdefault(position.security_id, position.sector)
            # 缺行业不能跳过风险集中度检查。
            if any(security_id not in sectors for security_id in notionals):
                reasons.append("missing_sector_classification")
            else:
                # sector_totals 是行业→最坏美元市值，从各证券 notionals 按行业归集；
                # get 的零表示该行业尚未累计，不是把缺少行业分类的证券当作零风险。
                sector_totals: dict[str, Decimal] = {}
                for security_id, amount in notionals.items():
                    sector_totals[sectors[security_id]] = (
                        sector_totals.get(sectors[security_id], Decimal("0")) + amount
                    )
                if any(
                    amount > exposure_nav * Decimal(str(config.max_sector))
                    for amount in sector_totals.values()
                ):
                    reasons.append("sector_exposure_limit")
    # ADV 为历史 20 日平均成交股数；缺字典或缺本证券（默认零）都使正数量意图失败。
    if adv is None or intent.quantity > int(
        adv.get(intent.security_id, 0.0) * config.max_adv_fraction
    ):
        reasons.append("liquidity_limit_or_missing_adv")
    if target is not None and intent.side == "BUY":
        # 持仓加挂买单不能超过批准目标。
        target_quantities = {
            position.security_id: position.quantity for position in target.positions
        }
        # 未列入批准目标的证券默认目标零股，因此任何正的风险占用买量都不获准。
        if projected.get(intent.security_id, 0) > target_quantities.get(intent.security_id, 0):
            reasons.append("target_quantity_exceeded")
    # 任一失败都禁止本次正常提交，同时保留独立减风险接口。
    return RiskDecision(
        allowed=not reasons, operation="NEW", freeze_new_risk=bool(reasons), reasons=reasons
    )
