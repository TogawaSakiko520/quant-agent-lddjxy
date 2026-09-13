"""无网络合成数据适配器；输出三年固定样本和下一时段独立报价。

样本只验证工程，价格轨迹不是历史市场，也不证明因子存在超额收益。
"""

# 正弦分量生成可重现的市场状态变化。
import math

# 每个实例使用局部随机源，禁止改变全局随机状态。
import random

# 日期偏移用于构建公开与可用时间，不读取真实时钟。
from datetime import timedelta

# 金额在执行边界转换为十进制。
from decimal import Decimal

# 契约集中定义单位、时间与内容哈希。
from quant_core.contracts import (
    DemoConfig,
    MarketDataRecord,
    Quote,
    SecurityRecord,
    TradingCalendar,
    seal_record,
)


def generate_fixture(
    config: DemoConfig, calendar: TradingCalendar
) -> tuple[list[MarketDataRecord], list[SecurityRecord]]:
    """生成固定合成行情和主表；返回记录列表，空日历抛 ValueError，无文件或网络副作用。"""
    # 只采用实际市场交易日，不用工作日生成器替代。
    sessions = calendar.sessions(config.start, config.end)
    # 空范围无法提供可验证的历史。
    if not sessions:
        # 报告环境或日期配置错误。
        raise ValueError("样本范围没有交易日")
    # 局部种子保证不依赖其他模块的随机调用。
    rng = random.Random(config.seed)
    # 汇总逐条带来源的行情。
    records: list[MarketDataRecord] = []
    # 汇总历史证券身份，基准是单独类型。
    securities: list[SecurityRecord] = []
    # 普通股和基准使用固定稳定身份。
    identities = [f"S{number:03d}" for number in range(1, config.securities + 1)] + ["BENCH"]
    # 主表在首个交易日开盘前已经可用。
    master_at = calendar.open_at(sessions[0]) - timedelta(days=1)
    # 每个证券生成独立且确定的轨迹。
    for index, security_id in enumerate(identities):
        # 基准不进入普通股选股池。
        is_benchmark = security_id == "BENCH"
        # 行业轮转确保样本可以验证行业约束。
        security = SecurityRecord(
            quality="good",
            security_id=security_id,
            ticker="DEMO-" + security_id,
            sector="market" if is_benchmark else f"sector-{index % 6}",
            effective_from=sessions[0],
            asset_type="benchmark" if is_benchmark else "common_stock",
            event_time=master_at,
            published_at=master_at,
            available_at=master_at,
        )
        # 输入内容封印后才能进入时点快照。
        securities.append(seal_record(security))
        # 初始价格按身份变化，避免全部证券具有相同取整结果。
        previous_close = 50.0 + index * 3.0
        # 每个交易日有明确开收盘与可用时间。
        for offset, session in enumerate(sessions):
            # 小幅隔夜变化提供独立开盘标签价格。
            overnight = rng.uniform(-0.001, 0.001)
            # 原始开盘价格不包含之后的盘内信息。
            raw_open = round(previous_close * (1 + overnight), 6)
            # 固定周期市场分量展示趋势切换，不代表市场预测。
            market_component = 0.00025 + 0.0006 * math.sin(offset / 85)
            # 不同证券的波动和漂移为因子提供可区分样本。
            noise = rng.uniform(-1, 1) * (0.004 if is_benchmark else 0.004 + index * 0.0003)
            # 演示收益保持有界且价格为正。
            daily_return = market_component + noise + (0 if is_benchmark else index * 0.000012)
            # 六位小数固定输入序列，业务金额另使用 Decimal。
            raw_close = round(raw_open * (1 + daily_return), 6)
            # 事件时间为该交易日真实收盘时刻。
            close_at = calendar.close_at(session)
            # 合成可用延迟固定十五分钟，绝不标为实际到达记录。
            available_at = close_at + timedelta(minutes=15)
            # 样本不含公司行动，总回报与原始收盘一致；公司行动另有独立测试。
            record = MarketDataRecord(
                quality="good",
                security_id=security_id,
                session=session,
                raw_open=raw_open,
                raw_close=raw_close,
                total_return_close=raw_close,
                volume=1_000_000 + index * 10_000 + offset * 17,
                event_time=close_at,
                published_at=close_at,
                available_at=available_at,
            )
            # 每条行情保存独立哈希，便于定位破损输入。
            records.append(seal_record(record))
            # 下一日开盘只以已生成的前一收盘为基准。
            previous_close = raw_close
    # 固定排序是快照和研究复现的前提。
    records.sort(key=lambda item: (item.session, item.security_id, item.revision))
    # 交付数据与主表，调用者决定写入位置。
    return records, securities


def fixture_quotes(
    records: list[MarketDataRecord], calendar: TradingCalendar, *, execution: bool
) -> dict[str, Quote]:
    """产生最后收盘或下一开盘的合成原始报价；返回身份映射，空记录抛 ValueError。"""
    # 没有输入时不能凭空生成价格。
    if not records:
        # 显式区分无样本与零价格。
        raise ValueError("生成报价需要行情")
    # 最新交易日从样本决定，不使用今天。
    last_session = max(item.session for item in records)
    # 决策报价用历史收盘，执行报价为下一时段独立事件。
    at = (
        calendar.open_at(calendar.next_session(last_session))
        if execution
        else calendar.close_at(last_session)
    )
    # 仅取最后交易日原始收盘，不使用总回报价格下单。
    latest = [item for item in records if item.session == last_session]
    # 独立报价小幅跳空，显式模拟，禁止反向参与过去因子计算。
    multiplier = Decimal("1.001") if execution else Decimal("1")
    # Decimal 从十进制字符串构造避免二进制误差。
    return {
        item.security_id: Quote(
            security_id=item.security_id,
            at=at,
            price=(Decimal(str(item.raw_close)) * multiplier).quantize(Decimal("0.000001")),
        )
        for item in latest
    }
