"""确定性监控规则；输出独立告警事实，不发送网络通知、不改写交易状态。

检查运行完整性、数据质量、心跳、时钟、订单、全部持仓偏差与因子覆盖率。
"""

from datetime import datetime
from typing import Literal

from quant_core.contracts import (
    AccountSnapshot,
    Contract,
    OrderRecord,
    ReconciliationResult,
    TargetPortfolio,
)


class Alert(Contract):
    """独立于日报的告警记录；后续处置证据不可被解释文字替代。"""

    # 稳定代码用于检索和去重。
    code: str
    # 严重级别区分关键告警与观察；告警记录本身不会自动拦单或改变账户。
    severity: Literal["critical", "warning", "info"]
    message: str
    # 告警时点为注入的业务时间。
    at: datetime
    # 处置负责人未指定时保持空值，禁止伪造确认。
    owner: str | None = None
    # 确认时间由将来的独立操作入口留存。
    acknowledged_at: datetime | None = None


def assess_health(
    account: AccountSnapshot,
    target: TargetPortfolio,
    reconciliation: ReconciliationResult,
    orders: list[OrderRecord],
    *,
    at: datetime,
    heartbeat_at: datetime,
    provider_at: datetime,
    data_good: bool,
    factor_coverage: float,
    completed_steps: set[str],
) -> list[Alert]:
    """将本次运行和对账事实转换为告警列表，供独立文件及报告展示。

    at、heartbeat_at、provider_at 均为调用方注入的 UTC 时刻，时差以秒计算；
    factor_coverage 由调用方计算；标准演示取双因子共同有效的评分证券数除以原始
    候选股票数，不是成交比例或目标完成率。[0, 1] 是输入前提，本函数不重算或校验
    该比例范围。无规则触发时返回 HEALTHY 记录，不发送通知。
    持仓偏离比较目标与实际证券并集，未列目标的旧仓按零目标比较；只告警不补单。
    HEALTHY 仅表示注入事实未触发本函数规则，不证明外部通知或持续运行正常。
    """
    alerts: list[Alert] = []
    # 任务完成标记防止某阶段静默跳过。
    required = {"data", "factors", "portfolio", "execution", "reconciliation"}
    # 集合差得到尚无完成标记的阶段；sorted 只固定告警文字顺序，不推断实际任务依赖。
    if missing := required - completed_steps:
        alerts.append(
            Alert(
                code="INCOMPLETE_RUN", severity="critical", message=",".join(sorted(missing)), at=at
            )
        )
    # 数据质量未通过时必须独立告警。
    if not data_good:
        alerts.append(
            Alert(
                code="DATA_QUALITY",
                severity="critical",
                message="数据质量或交易日新鲜度未通过",
                at=at,
            )
        )
    # 演示心跳阈值固定为一分钟，未来运营参数需另外验收。
    if (at - heartbeat_at).total_seconds() > 60 or heartbeat_at > at:
        alerts.append(
            Alert(code="HEARTBEAT", severity="critical", message="心跳缺失、过期或来自未来", at=at)
        )
    # 比较显式注入的本地与来源时刻，避免核心读取系统时钟。
    if abs((at - provider_at).total_seconds()) > 30:
        alerts.append(
            Alert(
                code="CLOCK_SKEW", severity="critical", message="来源与业务时钟偏差超过30秒", at=at
            )
        )
    # 对账差异形成独立告警记录；实际通知渠道不在本模块实现。
    if not reconciliation.matched:
        alerts.append(
            Alert(
                code="RECONCILIATION",
                severity="critical",
                message="；".join(reconciliation.differences),
                at=at,
            )
        )
    # 未知订单需要恢复，拒单需要查明原因。
    if any(order.status in {"UNKNOWN", "REJECTED"} for order in orders):
        alerts.append(
            Alert(
                code="ORDER_ERROR",
                severity="critical",
                message="存在未知或拒绝订单，请先恢复核对",
                at=at,
            )
        )
    # 因子覆盖率下降作为漂移观测，不自动更改策略。
    if factor_coverage < 0.8:
        alerts.append(
            Alert(
                code="FACTOR_COVERAGE",
                severity="warning",
                message=f"双因子覆盖率 {factor_coverage:.1%} 低于演示观察线80%",
                at=at,
            )
        )
    # deviations 只保存未达目标的证券 ID，不保存应补多少股，也不生成订单。
    # 目标缺键表示不再希望持有；账户缺键表示实有零股。并集包含待退出的目标外旧仓，
    # sorted 固定告警顺序，零股残留键与零目标一致，不产生虚假偏离。
    desired = {position.security_id: position.quantity for position in target.positions}
    deviations = [
        security_id
        for security_id in sorted(set(desired) | set(account.positions))
        if account.positions.get(security_id, 0) != desired.get(security_id, 0)
    ]
    # 偏离本身不触发强制补单。
    if deviations:
        alerts.append(
            Alert(
                code="TARGET_DEVIATION",
                severity="warning",
                message="目标尚未达到：" + ",".join(deviations),
                at=at,
            )
        )
    # 没有告警也保留健康事实，方便验证任务确实执行。
    if not alerts:
        alerts.append(
            Alert(code="HEALTHY", severity="info", message="本次离线链路及独立对账通过", at=at)
        )
    return alerts
