"""确定性监控规则；输出独立告警事实，不发送网络通知、不改写交易状态。

覆盖运行完整性、数据质量、心跳、时钟、订单、持仓偏差与因子覆盖率。
"""

# 时钟差值只来自注入的观测时间。
from datetime import datetime

# 有限严重级别便于通知适配器分流。
from typing import Literal

# 监控只读取共同契约，不访问交易写入服务。
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
    # 严重级别区分阻断与观察。
    severity: Literal["critical", "warning", "info"]
    # 告警依据必须来自真实检查结果。
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
    """检查注入的运行事实；返回告警列表，时间单位秒，无网络或交易副作用。"""
    # 告警与日报分别输出，关键故障不能淹没在长文里。
    alerts: list[Alert] = []
    # 任务完成标记防止某阶段静默跳过。
    required = {"data", "factors", "portfolio", "execution", "reconciliation"}
    # 缺少任何核心阶段都不能标为完整成功。
    if missing := required - completed_steps:
        # 保存具体缺失阶段便于恢复操作。
        alerts.append(
            Alert(
                code="INCOMPLETE_RUN", severity="critical", message=",".join(sorted(missing)), at=at
            )
        )
    # 数据质量未通过时必须独立告警。
    if not data_good:
        # 数据失效不意味着默认清仓。
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
        # 未来心跳同样可能是时钟异常。
        alerts.append(
            Alert(code="HEARTBEAT", severity="critical", message="心跳缺失、过期或来自未来", at=at)
        )
    # 比较显式注入的本地与来源时刻，避免核心读取系统时钟。
    if abs((at - provider_at).total_seconds()) > 30:
        # 时钟差异会影响订单时段与数据可用性。
        alerts.append(
            Alert(
                code="CLOCK_SKEW", severity="critical", message="来源与业务时钟偏差超过30秒", at=at
            )
        )
    # 对账差异必须在独立告警渠道暴露。
    if not reconciliation.matched:
        # 不从监控模块修正账务。
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
        # 该告警不等同于允许盲目重发。
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
        # 显示真实覆盖率，不编造收益解释。
        alerts.append(
            Alert(
                code="FACTOR_COVERAGE",
                severity="warning",
                message=f"双因子覆盖率 {factor_coverage:.1%} 低于演示观察线80%",
                at=at,
            )
        )
    # 实际与目标不一致可能来自约束、未成交或取整，需留痕。
    deviations = [
        position.security_id
        for position in target.positions
        if account.positions.get(position.security_id, 0) != position.quantity
    ]
    # 偏离本身不触发强制补单。
    if deviations:
        # 只表达事实，具体原因由订单及组合报告解释。
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
        # info 不触发未来运营中的关键告警通知。
        alerts.append(
            Alert(code="HEALTHY", severity="info", message="本次离线链路及独立对账通过", at=at)
        )
    # 输出不修改任何输入对象。
    return alerts
