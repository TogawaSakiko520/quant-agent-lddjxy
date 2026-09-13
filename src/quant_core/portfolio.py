"""约束目标及订单差额纯计算；现金不足留现金，不访问券商、不修改持仓。"""

# 延迟注解支持清晰接口。
from __future__ import annotations

# 执行资格时间由外部调度注入。
from datetime import datetime

# 美元预算、费用和限价全用十进制。
from decimal import ROUND_DOWN, Decimal

# 委托方向使用严格枚举而非任意字符串。
from typing import Literal

# 组合边界只使用统一契约。
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    DemoConfig,
    OrderIntent,
    OrderRecord,
    Quote,
    SecurityRecord,
    SignalSet,
    TargetPortfolio,
    TargetPosition,
    canonical_hash,
)

# 非终态订单必须计入持仓差额及预算预留。
ACTIVE_STATUSES = frozenset({"PERSISTED", "OPEN", "PARTIAL", "CANCEL_PENDING", "UNKNOWN"})


def order_fee(quantity: int, config: DemoConfig) -> Decimal:
    """返回整张订单美元费用预留；最低费按订单计一次，无副作用。"""
    # 无委托数量不应产生虚构费用。
    return (
        max(config.minimum_fee, config.fee_per_share * quantity) if quantity > 0 else Decimal("0")
    )


def account_nav(account: AccountSnapshot, quotes: dict[str, Quote]) -> Decimal:
    """以完整原始报价估值美元净值；缺价或空头抛错，不修改账户。"""
    # 字典键与报价消息的稳定身份必须一致，不能混用其他证券价格。
    if any(security_id != quote.security_id for security_id, quote in quotes.items()):
        # 全现金账户同样不能接受错证券报价后继续构建目标。
        raise ContractError("报价证券身份与索引不一致")
    # 现金参与净值，而可用现金只用于买入预算。
    nav = account.cash
    # 每个实际持仓都必须估值，包括不在新股票池的证券。
    for security_id, quantity in account.positions.items():
        # 长仓策略不允许隐藏空头或缺失旧仓价格。
        if quantity < 0 or (quantity and security_id not in quotes):
            # 不以零值伪装无法估值的持仓。
            raise ContractError("账户存在空头或持仓缺少原始报价")
        # 零仓位无须行情即可处理。
        if quantity:
            # 乘以原始价格得到实际口径市值。
            nav += quotes[security_id].price * quantity
    # 非正净值不能作为权重分母。
    if nav <= 0:
        # 拒绝进一步计算目标。
        raise ContractError("账户净值必须为正")
    # 函数只返回估值结果。
    return nav


def build_portfolio(
    signals: SignalSet,
    account: AccountSnapshot,
    quotes: dict[str, Quote],
    security_records: list[SecurityRecord],
    config: DemoConfig,
) -> TargetPortfolio:
    """按盘后可知报价构建约束整股目标；保留停牌旧仓，缺价/未来价抛错，无副作用。"""
    # 目标决策不能偷看下一交易时段的执行价格。
    if any(quote.at > signals.decision_time for quote in quotes.values()):
        # 调用方应为决策和执行分别注入报价。
        raise ContractError("目标组合不得使用未来报价")
    # 目标不能借用决策后才确认的账户或证券身份。
    if account.as_of > signals.decision_time or any(
        record.available_at > signals.decision_time for record in security_records
    ):
        # 执行时账户刷新应发生在独立下单风控阶段。
        raise ContractError("目标组合不得使用未来账户或证券主表")
    # 净值包含全部实际旧仓。
    nav = account_nav(account, quotes)
    # 历史主表由已通过时点闸门的调用方注入。
    securities = {security.security_id: security for security in security_records}
    # 输出持仓按确定顺序累积。
    positions: list[TargetPosition] = []
    # 约束无法满足的事实不能被留现金掩盖。
    reasons: list[str] = []
    # 行业已占用额度以美元表示。
    sector_used: dict[str, Decimal] = {}
    # 先保留不可交易的实际持仓。
    reserved = Decimal("0")
    # 已保留证券不再作为新入选名额重复添加。
    locked: set[str] = set()
    # 固定排序避免字典构造顺序改变目标。
    for security_id, quantity in sorted(account.positions.items()):
        # 零持仓无需保留。
        if quantity == 0:
            # 跳过非实际持仓记录。
            continue
        # 查找证券当前主表，缺失时不能猜测可交易。
        security = securities.get(security_id)
        # 证券缺身份、停牌、退市或报价停牌都不能假设卖掉。
        if (
            security is None
            or not security.tradable
            or not security.listed
            or not quotes[security_id].tradable
        ):
            # 缺行业也必须占用独立未知行业额度。
            sector = security.sector if security else "UNKNOWN"
            # 锁定仓位保持实际股数。
            notional = quotes[security_id].price * quantity
            # 该市值占用总仓位预算。
            reserved += notional
            # 同时占用对应行业预算。
            sector_used[sector] = sector_used.get(sector, Decimal("0")) + notional
            # 输出真实保留原因。
            positions.append(
                TargetPosition(
                    security_id=security_id,
                    sector=sector,
                    weight=float(notional / nav),
                    quantity=quantity,
                    reason="untradable_existing_position_retained",
                )
            )
            # 标记证券避免二次分配。
            locked.add(security_id)
            # 无法主动消除的约束违规需要独立披露。
            if notional > nav * Decimal(str(config.max_single)):
                # 不能为了符合目标表而抹掉旧仓。
                reasons.append(f"locked_single_limit:{security_id}")
    # 不可交易持仓超出预算时停止分配新增目标。
    locked_violation = reserved > nav * Decimal(str(config.max_gross)) or any(
        amount > nav * Decimal(str(config.max_sector)) for amount in sector_used.values()
    )
    # 明确整个不可行状态。
    if locked_violation:
        # 保留实际锁定仓位并留出其余现金。
        reasons.append("locked_positions_make_constraints_infeasible")
    # 总目标市值从锁定仓位开始累计。
    allocated = reserved
    # 评分顺序已固定，行业额度不足可跳过并继续尝试后续候选。
    for score in signals.scores:
        # 不能扩展不可行组合或超过总持仓数。
        if locked_violation or len(positions) >= config.max_positions:
            # 记录后退出，不隐式增加持仓上限。
            break
        # 已保留证券不能重复分配。
        if score.security_id in locked:
            # 当前数量已在目标中体现。
            continue
        # 候选报价必须存在且可交易。
        quote = quotes.get(score.security_id)
        # 缺价不能使用研究复权价格代替。
        if quote is None or not quote.tradable:
            # 明确剔除的执行依据。
            reasons.append(f"missing_or_untradable_quote:{score.security_id}")
            # 继续下一个候选。
            continue
        # 单票基础额度不因候选不足扩大。
        base = nav * Decimal(str(min(config.target_weight, config.max_single)))
        # 行业剩余额度独立控制。
        sector_room = nav * Decimal(str(config.max_sector)) - sector_used.get(
            score.sector, Decimal("0")
        )
        # 总仓位剩余额度不能突破90%演示上限。
        gross_room = nav * Decimal(str(config.max_gross)) - allocated
        # 三项上限取最小，不对剩余股票重新归一化。
        budget = max(Decimal("0"), min(base, sector_room, gross_room))
        # 只交易整股且向下取整。
        quantity = int((budget / quote.price).to_integral_value(rounding=ROUND_DOWN))
        # 不足一股留下现金并报告。
        if quantity == 0:
            # 跟踪约束排除而非生成零股订单。
            reasons.append(f"constraint_or_rounding_cash:{score.security_id}")
            # 不消耗持仓名额。
            continue
        # 实际分配按取整后的原始市值计算。
        notional = quote.price * quantity
        # 累计总目标市值。
        allocated += notional
        # 累计行业目标市值。
        sector_used[score.sector] = sector_used.get(score.sector, Decimal("0")) + notional
        # 保存评分与约束后的目标。
        positions.append(
            TargetPosition(
                security_id=score.security_id,
                sector=score.sector,
                weight=float(notional / nav),
                quantity=quantity,
                reason="equal_score_rank_and_constraints",
            )
        )
    # 未配置部分保持现金，即使入选数量不足20只。
    cash_weight = max(0.0, float((nav - allocated) / nav))
    # 把约束目标与后续实际执行结果分开保留。
    return TargetPortfolio(
        decision_id=signals.decision_id,
        as_of=signals.decision_time,
        nav=nav,
        positions=positions,
        cash_weight=cash_weight,
        reasons=reasons,
    )


def plan_orders(
    target: TargetPortfolio,
    account: AccountSnapshot,
    open_orders: list[OrderRecord],
    quotes: dict[str, Quote],
    adv: dict[str, float],
    config: DemoConfig,
    eligible_at: datetime,
    *,
    security_records: list[SecurityRecord] | None = None,
    turnover_used: Decimal | None = None,
) -> list[OrderIntent]:
    """计算含挂单的差额及现金/ADV/换手约束；只返回意图，未知状态抛错，无副作用。"""
    # 已知未完成订单参与差额，终态不再预留。
    active = [record for record in open_orders if record.status in ACTIVE_STATUSES]
    # 状态不清时先恢复，不能继续生成新风险。
    if any(record.status == "UNKNOWN" for record in active):
        # 调用方必须进行独立对账恢复。
        raise ContractError("未知订单状态必须先恢复")
    # 全部实际持仓作为有效数量起点。
    effective = dict(account.positions)
    # 现金预算仅来自已确认可用现金。
    cash = account.available_cash
    # 已有本轮成交时必须注入真实成交总额，卖单限价不能冒充成交金额。
    if turnover_used is None and any(
        record.intent.decision_id == target.decision_id and record.filled_quantity
        for record in open_orders
    ):
        # 调用方应从唯一成交事件账本汇总，而不是猜测成交价。
        raise ContractError("已有成交必须提供真实turnover_used")
    # 换手已使用金额不能为负，禁止通过负预算扩展权限。
    if turnover_used is not None and turnover_used < 0:
        # 金额输入不合法时立即拒绝。
        raise ContractError("turnover_used不得为负")
    # 换手包含本决策真实成交及随后累加的未完成预留。
    used_turnover = turnover_used or Decimal("0")
    # 历史订单分别影响数量、现金和本轮换手。
    for record in open_orders:
        # 完成数量不能超过原委托数量。
        if record.filled_quantity > record.intent.quantity:
            # 不用负剩余数量削减风险预算。
            raise ContractError("订单累计成交超过委托")
        # 只有非终态继续预留剩余数量。
        if record.status in ACTIVE_STATUSES:
            # 部分成交剩余量不能重复计入已持有部分。
            remaining = record.intent.quantity - record.filled_quantity
            # 卖单从有效数量扣除，买单增加。
            sign = 1 if record.intent.side == "BUY" else -1
            # 目标缺口先扣现存未完成订单。
            effective[record.intent.security_id] = (
                effective.get(record.intent.security_id, 0) + sign * remaining
            )
            # 挂单剩余部分占用本轮换手额度。
            used_turnover += (
                max(
                    record.intent.limit_price,
                    quotes[record.intent.security_id].price
                    if record.intent.security_id in quotes
                    else record.intent.limit_price,
                )
                * remaining
            )
            # 未完成卖单收入不能当作现有现金。
            if record.intent.side == "BUY":
                # 费用按整单预留，部分成交后保守保留最低费。
                cash -= record.intent.limit_price * remaining + record.intent.reserved_fee
    # 执行风险按扣除活动订单整单费用后的保守净值核算。
    risk_nav = account_nav(account, quotes) - sum(
        (record.intent.reserved_fee for record in active), Decimal("0")
    )
    # 最坏情形包括实际持仓与所有未完成买单，卖单不能预先释放额度。
    risk_quantities = dict(account.positions)
    # 原始报价作为风险估值起点。
    risk_prices = {sid: quote.price for sid, quote in quotes.items()}
    # 行业由当前主表与目标共同提供，缺失不猜测。
    risk_sectors = {record.security_id: record.sector for record in security_records or []}
    # 目标提供未在主表中的候选行业。
    for position in target.positions:
        # 已有主表事实优先。
        risk_sectors.setdefault(position.security_id, position.sector)
    # 活动买单占用其剩余数量和限价额度。
    for record in active:
        # 未完成卖单不释放风险预算。
        if record.intent.side == "BUY":
            # 部分成交数量已经进入实际持仓。
            remaining = record.intent.quantity - record.filled_quantity
            # 保守假设剩余买单全部成交。
            risk_quantities[record.intent.security_id] = (
                risk_quantities.get(record.intent.security_id, 0) + remaining
            )
            # 限价高于现价时仍须预留至限价。
            risk_prices[record.intent.security_id] = max(
                risk_prices.get(record.intent.security_id, Decimal("0")), record.intent.limit_price
            )
    # 约束后目标数量统一索引。
    desired = {position.security_id: position.quantity for position in target.positions}
    # 目标解释中的排序决定买单优先级。
    priority = {position.security_id: index for index, position in enumerate(target.positions)}
    # 未列入目标的可交易旧仓以零为目标。
    candidates = sorted(
        set(desired) | set(effective),
        key=lambda security_id: (priority.get(security_id, -1), security_id),
    )
    # 卖单先计划，但不提前挪用其预期收入。
    candidates.sort(
        key=lambda security_id: desired.get(security_id, 0) >= effective.get(security_id, 0)
    )
    # 返回列表不产生broker副作用。
    intents: list[OrderIntent] = []
    # 同一证券旧单仍活动时只扣净额，不另建可能冲突的反向委托。
    active_ids = {record.intent.security_id for record in active}
    # 逐个意图占用剩余预算。
    for security_id in candidates:
        # 计算目标相对实际加挂单的缺口。
        delta = desired.get(security_id, 0) - effective.get(security_id, 0)
        # 无缺口或已有活动订单的证券先等待完成。
        if delta == 0 or security_id in active_ids:
            # 后续恢复或下一轮规划再处理剩余目标。
            continue
        # 查找执行时原始报价。
        quote = quotes.get(security_id)
        # 不可交易或缺报价时保持实际仓位。
        if quote is None or not quote.tradable or quote.at > eligible_at:
            # 不伪造强制平仓结果。
            continue
        # 方向用显式枚举，不用负数量编码。
        side: Literal["BUY", "SELL"] = "BUY" if delta > 0 else "SELL"
        # 限价为买入价格预留上限或卖出下限。
        limit_price = quote.price * (
            Decimal("1") + config.price_buffer
            if side == "BUY"
            else Decimal("1") - config.price_buffer
        )
        # 对过大价格缓冲产生的非正卖价明确拒绝。
        if limit_price <= 0:
            # 配置错误不能变成无界订单。
            raise ContractError("价格缓冲导致非正限价")
        # 交易量按此前20日平均原始股数预算。
        liquidity = int(adv.get(security_id, 0.0) * config.max_adv_fraction)
        # 换手不以卖出收入回补预算。
        turnover_room = max(
            Decimal("0"), target.nav * Decimal(str(config.max_turnover)) - used_turnover
        )
        # 用原始报价与限价较高值保守估计交易额。
        budget_price = max(quote.price, limit_price)
        # 数量同时满足缺口、流动性和剩余换手。
        quantity = min(abs(delta), liquidity, int(turnover_room / budget_price))
        # 买入先按执行时最坏价格计算集中度空间，再验证可用现金。
        if side == "BUY":
            # 缺行业或旧仓报价时先完成卖出/对账，不猜测风险贡献。
            if security_id not in risk_sectors or any(
                amount > 0 and (sid not in risk_prices or sid not in risk_sectors)
                for sid, amount in risk_quantities.items()
            ):
                # 此目标留待资料完备后处理。
                continue
            # 本单风险价格不低于其限价。
            risk_price = max(risk_prices.get(security_id, quote.price), limit_price)
            # 全部已有和计划持仓均参与预算。
            notionals = {
                sid: amount * (risk_price if sid == security_id else risk_prices[sid])
                for sid, amount in risk_quantities.items()
                if amount > 0
            }
            # 已有单票超限期间不增加正常风险。
            if any(
                amount > risk_nav * Decimal(str(config.max_single)) for amount in notionals.values()
            ):
                # 不用新买单掩盖旧仓违规。
                continue
            # 检查所有已有行业，而非仅检查新买单所在行业。
            existing_sectors: dict[str, Decimal] = {}
            # 持仓和挂单按已知行业归集。
            for sid, amount in notionals.items():
                # 同行业金额相加，不遗漏目标外旧仓。
                existing_sectors[risk_sectors[sid]] = (
                    existing_sectors.get(risk_sectors[sid], Decimal("0")) + amount
                )
            # 任一已有行业超限时只保留独立卖出路径。
            if any(
                amount > risk_nav * Decimal(str(config.max_sector))
                for amount in existing_sectors.values()
            ):
                # 不向其他行业买入来掩盖尚未解除的约束。
                continue
            # 持仓名额由实际仓位和待成交买单共同占用。
            if (
                risk_quantities.get(security_id, 0) == 0
                and sum(amount > 0 for amount in risk_quantities.values()) >= config.max_positions
            ):
                # 卖单实际成交前不释放名额。
                continue
            # 相同行业的最坏市值需共同累加。
            sector_used_now = sum(
                (
                    amount
                    for sid, amount in notionals.items()
                    if risk_sectors[sid] == risk_sectors[security_id]
                ),
                Decimal("0"),
            )
            # 总仓位、行业与单票剩余额度取最小值。
            exposure_room = max(
                Decimal("0"),
                min(
                    risk_nav * Decimal(str(config.max_gross))
                    - sum(notionals.values(), Decimal("0")),
                    risk_nav * Decimal(str(config.max_sector)) - sector_used_now,
                    risk_nav * Decimal(str(config.max_single))
                    - notionals.get(security_id, Decimal("0")),
                ),
            )
            # 本单手续费会降低净值，提前从风险空间扣除。
            exposure_room = max(Decimal("0"), exposure_room - order_fee(quantity, config))
            # 确定性缩减整股数量，不改动任何风险参数。
            quantity = min(quantity, int(exposure_room / risk_price))
            # 买入还要独立扣费用与已有挂单预留。
            # 先用最低费用估计可负担整数上界。
            quantity = min(
                quantity,
                max(0, int((cash - config.minimum_fee) / (limit_price + config.fee_per_share))),
            )
            # 边界处核验真实最低费用，不借用卖单收入。
            while quantity > 0 and quantity * limit_price + order_fee(quantity, config) > cash:
                # 向下缩减整股，绝不放宽现金预算。
                quantity -= 1
        # 数量不足时保留现金或推迟目标。
        if quantity <= 0:
            # 不能生成零数量意图。
            continue
        # 稳定身份依赖同一决策、方向、证券和本轮已成交基线。
        identity = canonical_hash(
            {
                "decision_id": target.decision_id,
                "account_id": account.account_id,
                "security_id": security_id,
                "side": side,
                "existing_quantity": account.positions.get(security_id, 0),
                "target_quantity": desired.get(security_id, 0),
            }
        )
        # 同键已存在的终态订单也不隐式重新发送。
        if any(record.intent.client_order_id == identity for record in open_orders):
            # 拒单或撤单需要明确新决策，不盲目重试。
            continue
        # 预留费用按本次完整订单计算。
        fee = order_fee(quantity, config)
        # 创建意图时点就是注入的当前执行资格时点。
        intent = OrderIntent(
            client_order_id=identity,
            account_id=account.account_id,
            decision_id=target.decision_id,
            security_id=security_id,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            created_at=eligible_at,
            eligible_at=eligible_at,
            reserved_fee=fee,
        )
        # 只将通过预算计算的意图返回执行层。
        intents.append(intent)
        # 本轮后续意图不能重复使用同一换手预算。
        used_turnover += budget_price * quantity
        # 买单立即占用计划现金，卖单不释放现金。
        if side == "BUY":
            # 预留限价金额和整单费用。
            cash -= limit_price * quantity + fee
            # 后续买单不能重复占用本单的风险空间。
            risk_quantities[security_id] = risk_quantities.get(security_id, 0) + quantity
            # 全部已计划买单按最高可成交价估值。
            risk_prices[security_id] = max(quote.price, limit_price)
            # 计划费用也降低后续预算净值。
            risk_nav -= fee
    # 剩余未实现目标由实际与目标报告明确展示。
    return intents
