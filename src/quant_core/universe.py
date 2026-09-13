"""历史股票池资格；仅筛选候选，绝不修改实际持仓或暗示已经平仓。"""

# 公共数据契约是本模块唯一依赖。
from quant_core.contracts import DataSnapshot, SecurityRecord


def qualified_universe(snapshot: DataSnapshot) -> list[SecurityRecord]:
    """返回已冻结主表中合格普通股；不读取外部状态、不改变账户，无副作用。"""
    # 上市、可交易与普通股资格必须同时满足。
    return [
        security
        for security in snapshot.securities
        if security.listed
        and security.tradable
        and security.asset_type == "common_stock"
        and security.quality == "good"
    ]
