"""纯账务投影：只消费成交或公司行动事实，金额美元、数量整股，无存储副作用。"""

# 延迟注解求值以保持契约模块独立。
from __future__ import annotations

# 公司行动处理时刻必须由调用者显式传入。
from datetime import datetime, timedelta

# 十进制计算避免资金出现二进制浮点误差。
from decimal import Decimal

# 所有账务输入输出共享同一权威契约。
from quant_core.contracts import AccountSnapshot, ContractError, CorporateAction, FillEvent


def apply_fill(account: AccountSnapshot, event: FillEvent) -> AccountSnapshot:
    """将一笔已去重成交投影到账号；返回新快照，跨账户、透支或卖空抛 ContractError。

    参数 account 为当前美元账户、event 为新增整股成交；本函数不负责去重或持久化。
    买入费用计入持仓总成本，卖出按移动平均成本释放；现金逐笔扣减实际费用。
    """
    # 账户边界必须在任何资金运算之前检查。
    if event.account_id != account.account_id:
        # 不能把外部账号成交混入当前账本。
        raise ContractError("成交账户不匹配")
    # 复制持仓，保持传入冻结快照的嵌套内容不被修改。
    positions = dict(account.positions)
    # 成本同样使用副本，单位为整个持仓的美元成本。
    costs = dict(account.cost_basis)
    # 无旧仓时实际数量为零。
    previous = positions.get(event.security_id, 0)
    # 本次成交金额不包括手续费。
    notional = event.price * event.quantity
    # 买卖分别处理现金方向和成本释放。
    if event.side == "BUY":
        # 买入必须同时支付成交额与真实费用。
        cash = account.cash - notional - event.fee
        # 即时模拟结算也不能允许无资金买入。
        if cash < 0:
            # 不通过补造现金修正错误成交。
            raise ContractError("成交将造成现金透支")
        # 增加实际持仓而非订单累计报告数量。
        positions[event.security_id] = previous + event.quantity
        # 买入手续费属于取得成本，保留完整十进制金额。
        costs[event.security_id] = costs.get(event.security_id, Decimal("0")) + notional + event.fee
    # 卖出释放已有持仓，禁止空头。
    else:
        # 数量不足时拒绝投影，留给恢复流程解释。
        if event.quantity > previous:
            # 不允许把缺失买入成交掩盖为空头仓位。
            raise ContractError("卖出成交超过实际持仓")
        # 交易费用从卖出现金收入中扣除。
        cash = account.cash + notional - event.fee
        # 计算成交后剩余实际数量。
        remaining = previous - event.quantity
        # 仍有剩余时采用移动平均法按比例保留总成本。
        if remaining:
            # 数量按整股记录。
            positions[event.security_id] = remaining
            # 先乘再除降低十进制中间舍入的影响。
            costs[event.security_id] = (
                costs.get(event.security_id, Decimal("0")) * remaining / previous
            )
        # 清空仓位时同时清理零成本键，保证独立对账规范一致。
        else:
            # 已验证持仓存在，删除实际持仓。
            positions.pop(event.security_id, None)
            # 成本键可能不存在于外部初始账户，安全清理。
            costs.pop(event.security_id, None)
    # 通过模型重新校验而非直接修改原对象。
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

    参数现金单位美元，at为明确UTC处理时刻；股息只使用固定权益股数。
    未来发生、未来可用或质量失败的行动抛ContractError，不产生文件副作用。
    """
    # 处理时刻需要明确UTC，且行动必须已经发生并为当时可知。
    if (
        at.utcoffset() != timedelta(0)
        or action.event_time > at
        or action.available_at > at
        or action.quality != "good"
    ):
        # 不允许为了当前对账提前支付未来股息或应用未来拆股。
        raise ContractError("公司行动尚未发生、尚不可用或质量不合格")
    # 复制持仓，不能修改输入账户。
    positions = dict(account.positions)
    # 默认公司行动不改变现金。
    cash = account.cash
    # 拆股只改变数量，总成本和费用保持不变。
    if action.kind == "split":
        # 拆分按事件比例计算新股数。
        quantity = Decimal(positions.get(action.security_id, 0)) * action.ratio
        # 首版无碎股与现金替代规则，因此拒绝不完整的投影。
        if quantity != quantity.to_integral_value():
            # 禁止通过向下取整悄悄丢失账户权益。
            raise ContractError("拆股产生碎股，必须提供独立现金替代事实")
        # 无持仓行动不创建零股键。
        if quantity:
            # 已确认整股，可安全转换为整数。
            positions[action.security_id] = int(quantity)
    # 股息权益独立于支付日是否仍持仓。
    else:
        # 仅将明确权益数量乘每股现金加入账户。
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
