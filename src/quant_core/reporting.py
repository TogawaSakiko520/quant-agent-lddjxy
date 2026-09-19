"""从结构化事实生成中文解释报告；不调用 LLM、不编造买卖原因、不改写交易事实。"""

import json

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
    """把同次运行的输入、评分、目标、订单和账户事实排成 Markdown；金额美元、数量股。"""
    # 字典展示必须显式排序，不能依赖内存构造或JSON读回的插入顺序。
    regime_evidence = json.dumps(regime.evidence, ensure_ascii=False, sort_keys=True)
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
    # 本函数的 values 是 (证券 ID, 因子 ID)→原始因子值，仅用于展示，不重新算因子。
    # get 缺键保持 None，与缺失数值一样明确展示；不能从百分位反推原值或把缺失填零。
    values = {(item.security_id, item.factor_id): item.value for item in factors}
    for score in signals.scores:
        momentum = values.get((score.security_id, "momentum"))
        # 低波动缺失不得替换成零。
        low_vol = values.get((score.security_id, "low_volatility"))
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
    # 左侧数量来自目标，右侧通过 account.positions 查实际股数（缺键为零），并排
    # 展示计划与事实。这里只遍历 target.positions，不能据此认定目标外没有实际持仓。
    for position in target.positions:
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
    # intent 是发送前保存的原始委托要求；order.filled_quantity/status 是当前订单
    # 投影，不能用“已提交总数量”冒充实际成交数量，现金及费用仍以下方账户事实为准。
    for order in orders:
        intent = order.intent
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
    # 这里只展示传入告警；独立 JSONL 由应用装配层写入。
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
    return "\n".join(lines)
