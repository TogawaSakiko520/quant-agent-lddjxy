"""纯账务投影：只消费成交或公司行动事实，金额美元、数量整股，无存储副作用。"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from quant_core.contracts import AccountSnapshot, ContractError, CorporateAction, FillEvent


def apply_fill(account: AccountSnapshot, event: FillEvent) -> AccountSnapshot:
    """将一笔已去重成交投影为新的账户快照，原账户保持不变。

    参数 account 为当前美元账户、event 为新增整股成交；本函数不负责去重或持久化。
    买入费用计入持仓总成本，卖出按移动平均成本释放；现金逐笔扣减实际费用。
    跨账户、买入现金不足或超卖抛 ContractError；返回快照的模型校验错误向外传播，
    例如卖出费用超过现金与成交收入时抛 ValidationError。
    """
    # 账户边界必须在任何资金运算之前检查。
    if event.account_id != account.account_id:
        raise ContractError("成交账户不匹配")
    # 复制持仓，保持传入冻结快照的嵌套内容不被修改。
    positions = dict(account.positions)
    # 成本同样使用副本，单位为整个持仓的美元成本。
    costs = dict(account.cost_basis)
    # previous 是成交前实际整股数；缺键为零表示没有旧仓。notional 是本笔新增
    # 成交额（美元），不含 event.fee，也不是订单累计成交额。
    previous = positions.get(event.security_id, 0)
    notional = event.price * event.quantity
    if event.side == "BUY":
        # 买入必须同时支付成交额与真实费用。
        cash = account.cash - notional - event.fee
        if cash < 0:
            raise ContractError("成交将造成现金透支")
        positions[event.security_id] = previous + event.quantity
        # 买入手续费属于取得成本；costs 保存每证券整笔仓位的总美元成本，不是每股价。
        # 缺成本键时从零累计，函数不会反查历史成交来重建遗漏成本。
        costs[event.security_id] = costs.get(event.security_id, Decimal("0")) + notional + event.fee
    else:
        # 数量不足时拒绝投影，留给恢复流程解释。
        if event.quantity > previous:
            raise ContractError("卖出成交超过实际持仓")
        cash = account.cash + notional - event.fee
        # 此处 remaining 是卖出后实际剩余持仓，区别于订单规划中“尚未成交的订单余量”。
        remaining = previous - event.quantity
        # 仍有剩余时采用移动平均法按比例保留总成本。
        if remaining:
            positions[event.security_id] = remaining
            # 先乘再除降低十进制中间舍入的影响。
            costs[event.security_id] = (
                costs.get(event.security_id, Decimal("0")) * remaining / previous
            )
        # 清空仓位时同时清理零成本键，保证独立对账规范一致。
        else:
            positions.pop(event.security_id, None)
            # 成本键可能不存在于外部初始账户，安全清理。
            costs.pop(event.security_id, None)
    # 以新模型返回并重新校验：现金变动同步到可用现金，保留原有不可用现金差额。
    return AccountSnapshot(
        account_id=account.account_id,
        as_of=event.at,
        cash=cash,
        available_cash=account.available_cash + cash - account.cash,
        positions=positions,
        fees=account.fees + event.fee,
        cost_basis=costs,
    )


def apply_action(
    account: AccountSnapshot, action: CorporateAction, at: datetime
) -> AccountSnapshot:
    """投影已验证且已去重的公司行动；返回新账户，非整股拆分抛 ContractError。

    参数现金单位美元，at 为明确 UTC 处理时刻；股息只使用固定权益股数。
    调用方负责去重；返回账户 as_of 使用行动发生时间，不是这里的处理时间。
    未来发生、未来可用或质量失败的行动抛ContractError，不产生文件副作用。
    """
    # 处理时刻需要明确UTC，且行动必须已经发生并为当时可知。
    if (
        at.utcoffset() != timedelta(0)
        or action.event_time > at
        or action.available_at > at
        or action.quality != "good"
    ):
        raise ContractError("公司行动尚未发生、尚不可用或质量不合格")
    # 单独复制数量表，拆股只更新这个新表；成本表在返回处另复制，均不修改输入快照。
    positions = dict(account.positions)
    cash = account.cash
    # 拆股只改变数量，总成本和费用保持不变。
    if action.kind == "split":
        # 将旧整股数转 Decimal 与拆股比例相乘，先检验是否仍为整股，再转换为 int。
        # 缺键为零表示未持有该证券，不能凭公司行动新造持仓。
        quantity = Decimal(positions.get(action.security_id, 0)) * action.ratio
        # 首版无碎股与现金替代规则，因此拒绝不完整的投影。
        if quantity != quantity.to_integral_value():
            raise ContractError("拆股产生碎股，必须提供独立现金替代事实")
        # 无持仓行动不创建零股键。
        if quantity:
            positions[action.security_id] = int(quantity)
    # 股息权益独立于支付日是否仍持仓。
    else:
        cash += action.cash_per_share * action.entitlement_quantity
    # 保持持仓成本与累计费用，现金随模拟即时结算同步变化。
    return AccountSnapshot(
        account_id=account.account_id,
        as_of=action.event_time,
        cash=cash,
        available_cash=account.available_cash + cash - account.cash,
        positions=positions,
        fees=account.fees,
        cost_basis=dict(account.cost_basis),
    )
