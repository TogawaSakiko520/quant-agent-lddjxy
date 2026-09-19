"""约束目标及订单差额纯计算；现金不足留现金，不访问券商、不修改持仓。"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Literal

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
    """计算整张订单的美元费用预留；最低费按订单计一次，零股不收费。"""
    return (
        max(config.minimum_fee, config.fee_per_share * quantity) if quantity > 0 else Decimal("0")
    )


def account_nav(account: AccountSnapshot, quotes: dict[str, Quote]) -> Decimal:
    """以完整原始报价估值美元净值；缺价或空头抛错，不修改账户。"""
    # 字典键与报价消息的稳定身份必须一致，不能混用其他证券价格。
    if any(security_id != quote.security_id for security_id, quote in quotes.items()):
        raise ContractError("报价证券身份与索引不一致")
    # nav 是净资产值（美元）：现金加实际持仓的原始价市值，不包含未成交订单。
    # 它供权重和风险比例作分母；available_cash 则只表示此刻可用于买入的现金。
    nav = account.cash
    # 每个实际持仓都必须估值，包括不在新股票池的证券。
    for security_id, quantity in account.positions.items():
        # 长仓策略不允许隐藏空头或缺失旧仓价格。
        if quantity < 0 or (quantity and security_id not in quotes):
            raise ContractError("账户存在空头或持仓缺少原始报价")
        # 零仓位无须行情即可处理。
        if quantity:
            nav += quotes[security_id].price * quantity
    # 非正净值不能作为权重分母。
    if nav <= 0:
        raise ContractError("账户净值必须为正")
    return nav


def build_portfolio(
    signals: SignalSet,
    account: AccountSnapshot,
    quotes: dict[str, Quote],
    security_records: list[SecurityRecord],
    config: DemoConfig,
) -> TargetPortfolio:
    """把评分次序转换为受仓位上限约束的整股目标。

    quotes 使用决策时刻可知的原始价格；security_records 应已通过历史时点闸门。
    先保留不可交易旧仓，再按单票、行业、总仓位剩余额度分配；向下取整后余款
    留作现金，不重新归一化。返回目标及约束原因，不代表已经成交。
    未来输入、旧仓无法估值或非正净值抛 ContractError。
    """
    # 目标决策不能偷看下一交易时段的执行价格。
    if any(quote.at > signals.decision_time for quote in quotes.values()):
        raise ContractError("目标组合不得使用未来报价")
    # 目标不能借用决策后才确认的账户或证券身份。
    if account.as_of > signals.decision_time or any(
        record.available_at > signals.decision_time for record in security_records
    ):
        raise ContractError("目标组合不得使用未来账户或证券主表")
    # 本函数的 nav 固定在决策时点；后续分配只改变目标，不改变账户净值事实。
    nav = account_nav(account, quotes)
    # 历史主表由已通过时点闸门的调用方注入。
    securities = {security.security_id: security for security in security_records}
    # positions 是待返回的目标列表；sector_used 是行业→已分配目标市值（美元），
    # 先计入不能卖掉的旧仓，再计入新目标，不是从账户里直接扣款。
    positions: list[TargetPosition] = []
    reasons: list[str] = []
    sector_used: dict[str, Decimal] = {}
    # reserved 累加必须保留的旧仓市值；locked 保存其证券 ID，防止再次分配目标。
    reserved = Decimal("0")
    locked: set[str] = set()
    for security_id, quantity in sorted(account.positions.items()):
        if quantity == 0:
            continue
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
            notional = quotes[security_id].price * quantity
            reserved += notional
            # 首次出现的行业尚未占目标额度，get 的零只代表本次累计起点，不是缺行情填零。
            sector_used[sector] = sector_used.get(sector, Decimal("0")) + notional
            positions.append(
                TargetPosition(
                    security_id=security_id,
                    sector=sector,
                    weight=float(notional / nav),
                    quantity=quantity,
                    reason="untradable_existing_position_retained",
                )
            )
            locked.add(security_id)
            # 无法主动消除的约束违规需要独立披露。
            if notional > nav * Decimal(str(config.max_single)):
                reasons.append(f"locked_single_limit:{security_id}")
    # 不可交易持仓超出预算时停止分配新增目标。
    locked_violation = reserved > nav * Decimal(str(config.max_gross)) or any(
        amount > nav * Decimal(str(config.max_sector)) for amount in sector_used.values()
    )
    if locked_violation:
        reasons.append("locked_positions_make_constraints_infeasible")
    # allocated 是所有已列入目标的美元市值：从保留旧仓起步，随后按整股目标增加。
    # 它用来扣总仓位余额与计算现金权重；不会因“计划卖出”而改变账户现金。
    allocated = reserved
    # 评分顺序已固定，行业额度不足可跳过并继续尝试后续候选。
    for score in signals.scores:
        # 不能扩展不可行组合或超过总持仓数。
        if locked_violation or len(positions) >= config.max_positions:
            break
        if score.security_id in locked:
            continue
        quote = quotes.get(score.security_id)
        # 缺价不能使用研究复权价格代替。
        if quote is None or not quote.tradable:
            reasons.append(f"missing_or_untradable_quote:{score.security_id}")
            continue
        # 单票基础额度不因候选不足扩大。比例先经 str 再转 Decimal，避免把浮点
        # 二进制尾差带入美元预算；下方所有额度取最小值后才转换成整股数量。
        base = nav * Decimal(str(min(config.target_weight, config.max_single)))
        # 行业剩余额度独立控制。
        sector_room = nav * Decimal(str(config.max_sector)) - sector_used.get(
            score.sector, Decimal("0")
        )
        gross_room = nav * Decimal(str(config.max_gross)) - allocated
        # 三项上限取最小，不对剩余股票重新归一化。
        budget = max(Decimal("0"), min(base, sector_room, gross_room))
        # 只交易整股且向下取整。
        quantity = int((budget / quote.price).to_integral_value(rounding=ROUND_DOWN))
        # 不足一股留下现金并报告。
        if quantity == 0:
            reasons.append(f"constraint_or_rounding_cash:{score.security_id}")
            continue
        # 实际分配按取整后的原始市值计算。
        notional = quote.price * quantity
        allocated += notional
        sector_used[score.sector] = sector_used.get(score.sector, Decimal("0")) + notional
        positions.append(
            TargetPosition(
                security_id=score.security_id,
                sector=score.sector,
                weight=float(notional / nav),
                quantity=quantity,
                reason="equal_score_rank_and_constraints",
            )
        )
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
    """把目标与实际持仓、未完成订单的差额转换为可提交的整股意图。

    quotes 是 eligible_at 时刻可知的原始报价；adv 为此前 20 日平均成交股数。
    security_records 须由调用方筛选为当时可知的有效主表，本函数不会过滤版本。
    turnover_used 为本决策实际成交总额（美元）；已有成交时不能省略。
    open_orders 虽然名称含 open，调用方传入的是可含终态的历史订单列表；终态用于
    成交上下文及同键防重，只有 ACTIVE_STATUSES 中的记录继续占用数量和预算。
    未完成买单预留现金、费用和风险额度，未成交卖单不释放额度。预算不足的目标
    暂不产生意图，返回空列表也不表示目标已完成。未知订单、非法成交/换手数量、
    无法估值或非正限价抛 ContractError；返回列表不会发单或改变账户。
    """
    active = [record for record in open_orders if record.status in ACTIVE_STATUSES]
    # 状态不清时先恢复，不能继续生成新风险。
    if any(record.status == "UNKNOWN" for record in active):
        raise ContractError("未知订单状态必须先恢复")
    # effective 是证券 ID→“实际股数 + 挂买余量 - 挂卖余量”，只用于计算目标缺口。
    # dict 建立独立数量表，后续净额调整不会改写 account.positions 的成交事实。
    effective = dict(account.positions)
    # cash 是本次规划尚能分给新买单的美元数，起点为已确认可用现金，随后扣挂单预留。
    cash = account.available_cash
    # 已有本轮成交时必须注入真实成交总额，卖单限价不能冒充成交金额。
    if turnover_used is None and any(
        record.intent.decision_id == target.decision_id and record.filled_quantity
        for record in open_orders
    ):
        raise ContractError("已有成交必须提供真实turnover_used")
    # 换手已使用金额不能为负，禁止通过负预算扩展权限。
    if turnover_used is not None and turnover_used < 0:
        raise ContractError("turnover_used不得为负")
    # turnover_used 由调用方按本决策唯一成交的 price×quantity 汇总，买卖金额均取正。
    # used_turnover 在该事实基础上再加挂单与本次新意图预留，不是净买入金额。
    used_turnover = turnover_used or Decimal("0")
    # 历史订单分别影响数量、现金和本轮换手。
    for record in open_orders:
        # 完成数量不能超过原委托数量。
        if record.filled_quantity > record.intent.quantity:
            raise ContractError("订单累计成交超过委托")
        if record.status in ACTIVE_STATUSES:
            # remaining 是该订单尚未成交的整股数；已成交部分已在账户里，不能再加一次。
            remaining = record.intent.quantity - record.filled_quantity
            sign = 1 if record.intent.side == "BUY" else -1
            # get(..., 0) 表示账户原本未持有该证券；买单加、卖单减，预扣目标缺口。
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
    # risk_nav 是执行报价下的账户净值减活动订单整单费用（美元），并非 target.nav。
    # target.nav 固定换手分母，risk_nav 则供本次执行集中度估值，随后还扣新买单费用。
    risk_nav = account_nav(account, quotes) - sum(
        (record.intent.reserved_fee for record in active), Decimal("0")
    )
    # risk_quantities 是证券 ID→风险占用股数：实际仓位 + 挂买余量 + 本轮计划买量。
    # 与 effective 不同，这里不减挂卖余量：卖单可能不成交，不能先释放风险额度。
    risk_quantities = dict(account.positions)
    # risk_prices 保存各证券用于风险估值的美元单价，起初是现价，再取挂买限价较高值。
    risk_prices = {sid: quote.price for sid, quote in quotes.items()}
    # 行业先取传入主表再用目标补缺；这里未筛选 available_at/有效区间（已知 F01 同类路径）。
    risk_sectors = {record.security_id: record.sector for record in security_records or []}
    for position in target.positions:
        # setdefault 只补主表缺失的证券；已有分类不被目标覆盖，但不替代主表时点校验。
        risk_sectors.setdefault(position.security_id, position.sector)
    # 活动买单占用其剩余数量和限价额度。
    for record in active:
        if record.intent.side == "BUY":
            remaining = record.intent.quantity - record.filled_quantity
            risk_quantities[record.intent.security_id] = (
                risk_quantities.get(record.intent.security_id, 0) + remaining
            )
            # 限价高于现价时仍须预留至限价。
            risk_prices[record.intent.security_id] = max(
                risk_prices.get(record.intent.security_id, Decimal("0")), record.intent.limit_price
            )
    # desired 是批准目标的证券→最终股数，只有计划含义；未列入目标的旧仓目标为零。
    desired = {position.security_id: position.quantity for position in target.positions}
    # 目标解释中的排序决定买单优先级。
    priority = {position.security_id: index for index, position in enumerate(target.positions)}
    # 并集确保既检查新目标，也检查目标外旧仓；优先级默认 -1 使后者先被处理。
    candidates = sorted(
        set(desired) | set(effective),
        key=lambda security_id: (priority.get(security_id, -1), security_id),
    )
    # 布尔排序的 False 在前，所以负缺口（卖出）先规划；稳定排序保留组内原优先级。
    # 这里只改变意图顺序，未成交卖单仍不释放现金或风险额度。
    candidates.sort(
        key=lambda security_id: desired.get(security_id, 0) >= effective.get(security_id, 0)
    )
    intents: list[OrderIntent] = []
    # 同一证券旧单仍活动时只扣净额，不另建可能冲突的反向委托。
    active_ids = {record.intent.security_id for record in active}
    # 逐个意图占用剩余预算。
    for security_id in candidates:
        # delta 是目标减有效数量的整股缺口，正数需买、负数需卖；缺键的零表示没有
        # 目标或现存数量，不表示证券的价格/行业可以缺失。
        delta = desired.get(security_id, 0) - effective.get(security_id, 0)
        # 无缺口或已有活动订单的证券先等待完成。
        if delta == 0 or security_id in active_ids:
            continue
        quote = quotes.get(security_id)
        # 不可交易或缺报价时保持实际仓位。
        if quote is None or not quote.tradable or quote.at > eligible_at:
            continue
        side: Literal["BUY", "SELL"] = "BUY" if delta > 0 else "SELL"
        # 限价为买入价格预留上限或卖出下限。
        limit_price = quote.price * (
            Decimal("1") + config.price_buffer
            if side == "BUY"
            else Decimal("1") - config.price_buffer
        )
        if limit_price <= 0:
            raise ContractError("价格缓冲导致非正限价")
        # ADV 是此前 20 日平均成交股数；缺少该证券时给零流动性额度，最终不生成订单。
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
                continue
            # 本单风险价格不低于其限价。
            risk_price = max(risk_prices.get(security_id, quote.price), limit_price)
            # notionals 是当前风险占用量的证券→美元市值，尚未加入本次候选买量；
            # 先确认旧风险未超限，再用剩余美元空间约束本单，不能靠新仓掩盖旧仓超限。
            notionals = {
                sid: amount * (risk_price if sid == security_id else risk_prices[sid])
                for sid, amount in risk_quantities.items()
                if amount > 0
            }
            # 已有单票超限期间不增加正常风险。
            if any(
                amount > risk_nav * Decimal(str(config.max_single)) for amount in notionals.values()
            ):
                continue
            # 检查所有已有行业，而非仅检查新买单所在行业。
            existing_sectors: dict[str, Decimal] = {}
            for sid, amount in notionals.items():
                existing_sectors[risk_sectors[sid]] = (
                    existing_sectors.get(risk_sectors[sid], Decimal("0")) + amount
                )
            # 任一已有行业超限时只保留独立卖出路径。
            if any(
                amount > risk_nav * Decimal(str(config.max_sector))
                for amount in existing_sectors.values()
            ):
                continue
            # 持仓名额由实际仓位和待成交买单共同占用。
            if (
                risk_quantities.get(security_id, 0) == 0
                and sum(amount > 0 for amount in risk_quantities.values()) >= config.max_positions
            ):
                # 卖单实际成交前不释放名额。
                continue
            # 仅汇总本单所在行业已经占用的美元市值；无同业持仓时 Decimal("0")
            # 使空集合仍以金额类型参与后面的行业余额运算。
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
            quantity = min(quantity, int(exposure_room / risk_price))
            # 用 (现金-最低费)/(限价+每股费) 给出保守整股上界，再用实际整单费用核验。
            quantity = min(
                quantity,
                max(0, int((cash - config.minimum_fee) / (limit_price + config.fee_per_share))),
            )
            # 边界处核验真实最低费用，不借用卖单收入。
            while quantity > 0 and quantity * limit_price + order_fee(quantity, config) > cash:
                quantity -= 1
        if quantity <= 0:
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
        fee = order_fee(quantity, config)
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
        # 该对象仍是候选意图，由 execution 持久化、恢复核对并重新风控后才发送。
        intents.append(intent)
        # 本轮后续意图不能重复使用同一换手预算。
        used_turnover += budget_price * quantity
        # 买单立即占用计划现金，卖单不释放现金。
        if side == "BUY":
            cash -= limit_price * quantity + fee
            # 后续买单不能重复占用本单的风险空间。
            risk_quantities[security_id] = risk_quantities.get(security_id, 0) + quantity
            risk_prices[security_id] = max(quote.price, limit_price)
            # 计划费用也降低后续预算净值。
            risk_nav -= fee
    return intents
