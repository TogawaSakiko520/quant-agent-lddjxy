"""从结构化事实生成中文解释报告；不调用 LLM、不编造买卖原因、不改写交易事实。"""

# 固定JSON键顺序保证字典经磁盘读回后报告文本仍完全一致。
import json

# 报表只读取权威业务对象。
from quant_core.contracts import (
    AccountSnapshot,
    DataSnapshot,
    FactorValue,
    OrderRecord,
    ReconciliationResult,
    RegimeAssessment,
    SignalSet,
    TargetPortfolio,
)

# 告警来自独立监控检查。
from quant_core.monitoring import Alert


def render_report(
    snapshot: DataSnapshot,
    factors: list[FactorValue],
    signals: SignalSet,
    regime: RegimeAssessment,
    target: TargetPortfolio,
    orders: list[OrderRecord],
    account: AccountSnapshot,
    reconciliation: ReconciliationResult,
    alerts: list[Alert],
) -> str:
    """返回 Markdown 解释文本；输入金额美元、数量股，缺失因子明确显示，无副作用。"""
    # 字典展示必须显式排序，不能依赖内存构造或JSON读回的插入顺序。
    regime_evidence = json.dumps(regime.evidence, ensure_ascii=False, sort_keys=True)
    # 排除原因同样按稳定身份顺序展示。
    exclusions = json.dumps(signals.excluded, ensure_ascii=False, sort_keys=True)
    # 固定报告头部说明工程证据的适用边界。
    lines = [
        "# quant-core 离线工程解释报告",
        "",
        "> 合成数据与 FakeBroker 只证明工程链路，不是正式回测、收益证据或实盘授权。",
        "",
        f"决策：`{signals.decision_id}`；截止时间：{signals.decision_time.isoformat()}。",
        f"快照：`{snapshot.snapshot_id}`；内容哈希：`{snapshot.content_hash}`。",
        f"市场状态：{regime.state}（仅观察）；证据：{regime_evidence}。",
        "",
        "## 因子与评分",
        "",
        "动量 = P[t-21]/P[t-252]-1（253个价格）；低波动 = -std(r, ddof=1)×√252（61个价格）。",
        "百分位按平均并列名次计算，两个因子固定等权；排名不是收益预测。",
        "",
        "| 证券 | 动量原值 | 低波动原值 | 动量百分位 | 低波动百分位 | 综合分 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    # 建立只读因子索引，避免从评分反推原始数值。
    values = {(item.security_id, item.factor_id): item.value for item in factors}
    # 依照真实信号顺序展示全部合格证券。
    for score in signals.scores:
        # 从契约内的因子身份读取实际计算结果。
        momentum = values.get((score.security_id, "momentum"))
        # 低波动缺失不得替换成零。
        low_vol = values.get((score.security_id, "low_volatility"))
        # 百分位来自实际标准化结果。
        lines.append(
            f"| {score.security_id} | {momentum} | {low_vol} | {score.components.get('momentum')} | {score.components.get('low_volatility')} | {score.value:.6f} |"
        )
    # 排除理由也属于完整解释链。
    lines.extend(
        [
            "",
            "排除记录：" + (exclusions if signals.excluded else "无"),
            "",
            "## 目标与实际订单",
            "",
            "| 证券 | 行业 | 目标权重 | 目标股数 | 实际股数 | 理由 |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    # 每个目标绑定真实约束原因，未成交目标不冒充已实现仓位。
    for position in target.positions:
        # 实际持仓来自独立对账后的内部账户事实。
        lines.append(
            f"| {position.security_id} | {position.sector} | {position.weight:.2%} | {position.quantity} | {account.positions.get(position.security_id, 0)} | {position.reason} |"
        )
    # 展示保留现金与未能完成目标的原因。
    lines.extend(
        [
            "",
            f"目标现金权重：{target.cash_weight:.2%}；组合约束记录：{target.reasons}。",
            "",
            "| 客户订单ID | 证券 | 方向 | 委托股数 | 已成交 | 限价 | 费用预留 | 状态 |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    # 订单与成交投影明确区分。
    for order in orders:
        # 订单意图包含从目标到执行的稳定身份。
        intent = order.intent
        # 限价与费用都保留十进制金额。
        lines.append(
            f"| {intent.client_order_id} | {intent.security_id} | {intent.side} | {intent.quantity} | {order.filled_quantity} | {intent.limit_price} | {intent.reserved_fee} | {order.status} |"
        )
    # 现金及费用是账本事实，不来自模拟收益曲线。
    lines.extend(
        [
            "",
            "## 账务、对账与监控",
            "",
            f"现金：{account.cash} USD；可用现金：{account.available_cash} USD；累计费用：{account.fees} USD。",
            f"独立对账通过：{reconciliation.matched}；差异：{reconciliation.differences}。",
            "",
        ]
    )
    # 关键告警同时写入独立 JSONL，本报告只是展示副本。
    for alert in alerts:
        # 未确认告警不得暗示已有处置人。
        lines.append(
            f"- [{alert.severity}] {alert.code}：{alert.message}；负责人：{alert.owner or '未指定'}。"
        )
    # 报告末尾给出原始证据文件入口。
    lines.extend(
        [
            "",
            "原始数据见 inputs/market.parquet；完整因子、评分、目标、订单、成交事件分别见同目录 JSON 文件。",
            "复现含义是固定输入重算与已有事件回放，不代表能重复取得真实市场成交价。",
            "",
        ]
    )
    # 固定换行保证同事实产生同报告。
    return "\n".join(lines)
