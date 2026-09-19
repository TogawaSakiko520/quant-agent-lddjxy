"""无网络合成数据适配器；输出配置日期范围内的固定样本和下一时段独立报价。

样本只验证工程，价格轨迹不是历史市场，也不证明因子存在超额收益。
"""

import math
import random
from datetime import timedelta
from decimal import Decimal

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
    """按配置种子和实际交易日历生成普通股、基准行情及相应历史主表。

    行情价格保留六位小数；不含公司行动，因此总回报收盘等于原始收盘。
    收盘十五分钟后标为可用是合成延迟，不能当作真实首次观测记录。
    返回 (行情列表, 主表列表)：前者每证券每交易日一条，后者每证券一条初始身份。
    应用用完整样本按决策时点 build_snapshot，并保存原始输入；这里不选股或生成订单。
    返回记录已带内容哈希；日期范围内无交易日时抛 ValueError。
    """
    sessions = calendar.sessions(config.start, config.end)
    if not sessions:
        raise ValueError("样本范围没有交易日")
    # 局部种子保证不依赖其他模块的随机调用。
    rng = random.Random(config.seed)
    records: list[MarketDataRecord] = []
    securities: list[SecurityRecord] = []
    # 稳定 ID 在行情与主表之间建立关联；BENCH 是观察市场状态的基准，不参与普通股选股。
    identities = [f"S{number:03d}" for number in range(1, config.securities + 1)] + ["BENCH"]
    # 主表在首个交易日开盘前已经可用。
    master_at = calendar.open_at(sessions[0]) - timedelta(days=1)
    for index, security_id in enumerate(identities):
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
        securities.append(seal_record(security))
        # 初始价格按身份变化，避免全部证券具有相同取整结果。
        previous_close = 50.0 + index * 3.0
        for offset, session in enumerate(sessions):
            # 小幅隔夜变化提供独立开盘标签价格。
            overnight = rng.uniform(-0.001, 0.001)
            raw_open = round(previous_close * (1 + overnight), 6)
            # 固定周期市场分量展示趋势切换，不代表市场预测。
            market_component = 0.00025 + 0.0006 * math.sin(offset / 85)
            # 不同证券的波动和漂移为因子提供可区分样本。
            noise = rng.uniform(-1, 1) * (0.004 if is_benchmark else 0.004 + index * 0.0003)
            daily_return = market_component + noise + (0 if is_benchmark else index * 0.000012)
            # 六位小数固定输入序列，业务金额另使用 Decimal。
            raw_close = round(raw_open * (1 + daily_return), 6)
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
            # seal_record 返回带内容哈希的模型副本，供接入层核验内容一致性；哈希不使
            # 合成轨迹变成真实行情，也不补造 first_seen_at 的实际观测证据。
            records.append(seal_record(record))
            previous_close = raw_close
    # 固定排序是快照和研究复现的前提。
    records.sort(key=lambda item: (item.session, item.security_id, item.revision))
    return records, securities


def fixture_quotes(
    records: list[MarketDataRecord], calendar: TradingCalendar, *, execution: bool
) -> dict[str, Quote]:
    """由最后样本日的原始收盘生成按证券 ID 索引的决策或执行报价。

    execution=False 使用当日收盘；True 使用下一交易日开盘时刻与 0.1% 合成跳空，
    该未来报价只能用于执行，不能回填决策。价格取六位小数，空记录抛 ValueError。
    records 通常来自已过滤版本的 snapshot.records；本函数不另做历史版本选择。
    返回字典的键为稳定证券 ID，Quote.price 为每股美元，供组合估值或执行风控读取。
    """
    if not records:
        raise ValueError("生成报价需要行情")
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
