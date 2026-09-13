"""分类风控纯规则；新增、撤单、减仓分别判断，禁止失败时盲目清仓。"""

# 延迟注解用于明确可选风险上下文。
from __future__ import annotations

# 时间完全来自调用者注入。
from datetime import datetime

# 现金及限价预算使用十进制。
from decimal import Decimal

# 操作类型限于公开契约中的明确分类。
from typing import Literal

# 主表有效区间按纽约交易日核验。
from zoneinfo import ZoneInfo

# 风控不依赖broker或外部状态读取。
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

# 费用、估值和活动订单范围与组合规划保持一致。
from quant_core.portfolio import ACTIVE_STATUSES, account_nav, order_fee


def assess_operation(
    operation: Literal["NEW", "CANCEL", "REPLACE", "REDUCE"],
    *,
    reconciled: bool,
    known_state: bool,
    authorized: bool = True,
) -> RiskDecision:
    """判断操作授权和状态前提；返回独立权限结论，不代替价格/数量检查，无副作用。"""
    # 每个失败前提保存机器可读原因。
    reasons: list[str] = []
    # 减风险路径也不能绕过明确授权。
    if not authorized:
        # 权限缺失对全部操作生效。
        reasons.append("unauthorized_operation")
    # 未知订单状态必须先恢复，避免撤错单或重复操作。
    if not known_state:
        # 同时冻结新增风险。
        reasons.append("unknown_order_state")
    # 对账失败阻止新增或改单，但不自动剥夺可审计撤单路径。
    if operation in {"NEW", "REPLACE"} and not reconciled:
        # 账户差异不能靠扩大交易覆盖。
        reasons.append("unreconciled_account")
    # 此判断不产生任何清仓或撤单副作用。
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
    """返回账户、时段、行情和数量的硬约束失败原因；不执行交易，无副作用。"""
    # 硬约束对新增和减风险路径同样成立。
    reasons: list[str] = []
    # 仅接受明确离线白名单账户。
    if intent.account_id != config.account_id or account.account_id != config.account_id:
        # 真实或意外账户不能因参数格式正确获得授权。
        reasons.append("account_not_whitelisted")
    # 不接受未来账户事实或过期账户快照。
    if account.as_of > now or (now - account.as_of).total_seconds() > config.quote_max_age_seconds:
        # 需要适配器重新核实账户。
        reasons.append("stale_or_future_account")
    # 只有常规时段且达到订单资格时刻才可执行。
    if now < intent.eligible_at or now < intent.created_at or not calendar.is_open(now):
        # 盘后决策不能倒填当日收盘价。
        reasons.append("outside_execution_session")
    # 排除自身已持久化意图，避免重复扣同一个订单。
    others = [
        record for record in open_orders if record.intent.client_order_id != intent.client_order_id
    ]
    # 未知其他订单先恢复。
    if any(record.status == "UNKNOWN" for record in others):
        # 不能靠新单绕过无法确定的旧单。
        reasons.append("unknown_order_state")
    # 报价字典的键不能覆盖消息内的真实证券身份。
    if any(security_id != value.security_id for security_id, value in quotes.items()):
        # 任意估值输入错配都可能绕过集中度或订单价格检查。
        reasons.append("quote_identity_mismatch")
    # 查询当前证券的注入原始价格。
    quote = quotes.get(intent.security_id)
    # 缺报价不允许以复权研究价替代。
    if quote is None:
        # 价格信息缺失属于执行阻断。
        reasons.append("missing_quote")
    # 有报价时验证时点和可交易性。
    else:
        # 不接纳未来报价或超龄报价。
        if quote.at > now or (now - quote.at).total_seconds() > config.quote_max_age_seconds:
            # 防止重连后使用断线前的过期价格。
            reasons.append("stale_or_future_quote")
        # 停牌不能以清仓名义绕过。
        if not quote.tradable:
            # 保留真实旧仓并报告。
            reasons.append("untradable_quote")
        # 限价必须覆盖当前报价，离线示例不猜测未来穿价。
        if (intent.side == "BUY" and quote.price > intent.limit_price) or (
            intent.side == "SELL" and quote.price < intent.limit_price
        ):
            # 不更改限价来强行获得成交。
            reasons.append("quote_outside_limit")
    # 负持仓属于当前长仓契约不支持的账户状态。
    if any(quantity < 0 for quantity in account.positions.values()):
        # 未解释的空头先处理账户事实。
        reasons.append("unexpected_short_position")
    # 累计成交不能大于委托数量。
    if any(record.filled_quantity > record.intent.quantity for record in others):
        # 不让负剩余量释放预算。
        reasons.append("invalid_order_quantity")
    # 卖单必须扣除其他尚未完成的卖单。
    if intent.side == "SELL":
        # 只采用实际确认持仓，不包括待成交买单。
        available_quantity = account.positions.get(intent.security_id, 0)
        # 每一张未完成卖单都占用可卖数量。
        for record in others:
            # 状态在活动集合内且为同证券卖单才需扣除。
            if (
                record.status in ACTIVE_STATUSES
                and record.intent.security_id == intent.security_id
                and record.intent.side == "SELL"
            ):
                # 撤单中仍预留剩余可卖股数。
                available_quantity -= record.intent.quantity - record.filled_quantity
        # 减仓不能穿过零成为卖空。
        if intent.quantity > available_quantity:
            # 不允许借未来买单证明可卖数量。
            reasons.append("insufficient_confirmed_shares")
    # 每单费用预留不能低于已配置费用模型。
    if intent.reserved_fee < order_fee(intent.quantity, config):
        # 费用不能被漏算以绕过现金限制。
        reasons.append("insufficient_fee_reserve")
    # 所有检查只收集原因，不执行修复。
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
    """核验独立卖出减仓路径；无需正常新增数据状态，但必须授权且可执行，无副作用。"""
    # 减风险操作不能以BUY名义扩大多头仓位。
    reasons = [] if intent.side == "SELL" else ["reduce_requires_sell"]
    # 减仓仍需要明确授权。
    if not authorized:
        # 不把紧急状态视为无限授权。
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
    """提交前检查完整新单风险；上下文来自注入事实，返回原因和权限，不写状态。"""
    # 普通提交即使方向为卖出也先要求正常链路；紧急减仓另有接口。
    reasons = _hard_checks(intent, account, open_orders, quotes, config, now, calendar)
    # 账户未对齐不得新增正常交易。
    if not reconciled:
        # 明确要求执行层恢复。
        reasons.append("unreconciled_account")
    # 数据质量失败不能继续正常策略。
    if not data_good:
        # 减风险替代路径不能隐式自动执行。
        reasons.append("data_quality_failed")
    # 正常策略订单必须绑定明确批准目标，不能靠省略上下文跳过检查。
    if target is None:
        # 缺目标时没有证券和数量授权依据。
        reasons.append("missing_approved_target")
    # 目标身份、时点与净值必须符合当前意图。
    elif target.decision_id != intent.decision_id or target.as_of > now or target.nav <= 0:
        # 不允许借另一决策或未来目标扩大权限。
        reasons.append("invalid_approved_target")
    # 日内基线缺失不能静默禁用亏损检查。
    if reference_nav is None:
        # 调用方必须明确注入已确认基线。
        reasons.append("missing_reference_nav")
    # 基线必须是有意义的正净值。
    elif reference_nav <= 0:
        # 零或负值无法定义合法日内损失比例。
        reasons.append("invalid_reference_nav")
    # 回撤高点同样是正常交易的必需风险上下文。
    if peak_nav is None:
        # 不以未提供数据当作没有发生回撤。
        reasons.append("missing_peak_nav")
    # 高点净值必须为正。
    elif peak_nav <= 0:
        # 防止无效分母使回撤检查失效。
        reasons.append("invalid_peak_nav")
    # 自身已落盘意图不重复参与预算。
    others = [
        record for record in open_orders if record.intent.client_order_id != intent.client_order_id
    ]
    # 估值失败以拒绝原因返回，而非绕过检查。
    try:
        # 必须对全部旧仓使用当前原始报价估值。
        nav = account_nav(account, quotes)
    # 缺价格、空头和非正净值都阻止正常交易。
    except ContractError:
        # 直接返回不可继续的失败，不假造净值分母。
        return RiskDecision(
            allowed=False,
            operation="NEW",
            freeze_new_risk=True,
            reasons=reasons + ["account_valuation_failed"],
        )
    # 换手分母以决策净值固定，不随当日交易反复重置。
    denominator = target.nav if target is not None else reference_nav or nav
    # 已有成交但缺真实金额时不能拿卖单最低限价低估gross turnover。
    if turnover_used is None and any(
        record.intent.decision_id == intent.decision_id and record.filled_quantity
        for record in others
    ):
        # 执行服务必须依据唯一成交事件汇总。
        reasons.append("missing_realized_turnover")
    # 负数不能用来释放虚构额度。
    if turnover_used is not None and turnover_used < 0:
        # 明确拒绝错误的风险上下文。
        reasons.append("invalid_realized_turnover")
    # 已注入真实成交额先累计，活动订单随后另行预留。
    committed_turnover = max(Decimal("0"), turnover_used or Decimal("0"))
    # 买入预算来自券商明确确认的可用现金。
    available_cash = account.available_cash
    # 最坏情况持仓只增加挂买单，不提前减去未成交卖单。
    projected = dict(account.positions)
    # 各证券采用当前报价和所有活动买单限价中的最高值。
    projected_prices = {security_id: quote.price for security_id, quote in quotes.items()}
    # 本单及其他未完成订单尚需支付的费用降低最坏净值。
    unpaid_fees = intent.reserved_fee
    # 把所有未完成买单和本次操作合并检查。
    for record in others:
        # 活动单余量继续保留额度。
        if record.status in ACTIVE_STATUSES:
            # 剩余量不能包含已经进入账户的成交量。
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
                # 未成交卖出收入不增加现金。
                available_cash -= record.intent.limit_price * remaining + record.intent.reserved_fee
                # 最坏情况假设买单全部成交、卖单尚未成交。
                projected[record.intent.security_id] = (
                    projected.get(record.intent.security_id, 0) + remaining
                )
                # 活动买单可能在其最高限价成交，不能仅按较低现价估值。
                projected_prices[record.intent.security_id] = max(
                    projected_prices.get(record.intent.security_id, Decimal("0")),
                    record.intent.limit_price,
                )
    # 本次限价金额也占用gross turnover额度。
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
        # 超过预算的目标必须留待未来批准决策。
        reasons.append("turnover_limit")
    # 尚需支付的费用不能被忽略而让恰好触顶订单通过。
    exposure_nav = nav - unpaid_fees
    # 费用后净值不为正时没有可授权的新增风险预算。
    if exposure_nav <= 0:
        # 不把负分母转换成可交易权重。
        reasons.append("nonpositive_after_fee_nav")
    # 买单需要全额限价金额及费用已确认可用。
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
            # 行情声称可交易不能掩盖主表异常。
            reasons.append("security_not_eligible")
        # 禁止以未成交卖单补足购买力。
        if intent.quantity * intent.limit_price + intent.reserved_fee > available_cash:
            # 费用及限价变动都不能透支。
            reasons.append("insufficient_available_cash")
        # 新买单纳入最坏情况数量。
        projected[intent.security_id] = projected.get(intent.security_id, 0) + intent.quantity
        # 当前意图限价与之前同证券挂买单共同确定最高风险价格。
        projected_prices[intent.security_id] = max(
            projected_prices.get(intent.security_id, Decimal("0")), intent.limit_price
        )
        # 新风险需要重新检查当日亏损。
        if reference_nav is not None and nav <= reference_nav * (
            Decimal("1") - Decimal(str(config.daily_loss_limit))
        ):
            # 该阈值不保证实际亏损被封顶。
            reasons.append("daily_loss_limit")
        # 高点回撤与日内损失分别控制。
        if peak_nav is not None and nav <= peak_nav * (
            Decimal("1") - Decimal(str(config.drawdown_limit))
        ):
            # 不因降低阈值测试失败而放宽规则。
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
            # 不能只验证当前下单证券却用陈旧旧仓低估风险。
            reasons.append("stale_projected_position_quote")
        # 持仓数按实际加未完成买单计算。
        if sum(quantity > 0 for quantity in projected.values()) > config.max_positions:
            # 已有不在目标的旧仓也占名额。
            reasons.append("position_count_limit")
        # 需要全部最坏持仓的原始价格。
        if any(
            quantity and security_id not in quotes for security_id, quantity in projected.items()
        ):
            # 缺挂单证券报价同样不能绕过估值。
            reasons.append("projected_position_missing_quote")
        # 价格齐备后检查总仓位与单票。
        else:
            # 全部现有仓位和挂买单均采用该证券最高可成交风险价。
            notionals = {
                security_id: quantity * projected_prices[security_id]
                for security_id, quantity in projected.items()
                if quantity > 0
            }
            # 总仓位不得靠未成交卖单假设下降。
            if sum(notionals.values(), Decimal("0")) > exposure_nav * Decimal(
                str(config.max_gross)
            ):
                # 留现金或先等待卖出实际成交。
                reasons.append("gross_exposure_limit")
            # 单票检查包括旧仓和挂单。
            if any(
                amount > exposure_nav * Decimal(str(config.max_single))
                for amount in notionals.values()
            ):
                # 旧仓异常也不能通过新增掩盖。
                reasons.append("single_position_limit")
            # 行业事实可来自明确主表或已审核目标。
            sectors = {security.security_id: security.sector for security in security_records or []}
            # 目标携带的行业可补充当前入选证券。
            if target is not None:
                # 已知主表优先，不让目标覆盖当前事实。
                for position in target.positions:
                    # 仅补充缺失字段。
                    sectors.setdefault(position.security_id, position.sector)
            # 缺行业不能跳过风险集中度检查。
            if any(security_id not in sectors for security_id in notionals):
                # 调用方需补齐证券主表。
                reasons.append("missing_sector_classification")
            # 资料完整后按行业累加。
            else:
                # 行业市值使用美元。
                sector_totals: dict[str, Decimal] = {}
                # 每个持仓只属于当前明确行业。
                for security_id, amount in notionals.items():
                    # 聚合同一行业的最坏市值。
                    sector_totals[sectors[security_id]] = (
                        sector_totals.get(sectors[security_id], Decimal("0")) + amount
                    )
                # 行业上限不是目标归一化建议。
                if any(
                    amount > exposure_nav * Decimal(str(config.max_sector))
                    for amount in sector_totals.values()
                ):
                    # 拒绝超过已配置演示预算的新增订单。
                    reasons.append("sector_exposure_limit")
    # 注入的ADV来自历史20日原始成交量，不能用未来成交量。
    if adv is None or intent.quantity > int(
        adv.get(intent.security_id, 0.0) * config.max_adv_fraction
    ):
        # 缺流动性依据与超限均不允许新增正常订单。
        reasons.append("liquidity_limit_or_missing_adv")
    # 目标存在时防止当前单超过目标数量。
    if target is not None and intent.side == "BUY":
        # 持仓加挂买单不能超过批准目标。
        target_quantities = {
            position.security_id: position.quantity for position in target.positions
        }
        # 检查证券是否批准且数量未越界。
        if projected.get(intent.security_id, 0) > target_quantities.get(intent.security_id, 0):
            # 拒绝从合法策略目标扩大风险。
            reasons.append("target_quantity_exceeded")
    # 任一失败都禁止本次正常提交，同时保留独立减风险接口。
    return RiskDecision(
        allowed=not reasons, operation="NEW", freeze_new_risk=bool(reasons), reasons=reasons
    )
