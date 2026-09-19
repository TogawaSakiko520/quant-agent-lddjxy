"""历史股票池资格；仅筛选候选，绝不修改实际持仓或暗示已经平仓。"""

from quant_core.contracts import DataSnapshot, SecurityRecord


def qualified_universe(snapshot: DataSnapshot) -> list[SecurityRecord]:
    """从已通过时点闸门的快照选出上市、可交易且质量合格的普通股。"""
    # 保留快照主表的原顺序与记录对象，只新建候选列表；基准不会进入选股评分，
    # 被剔除证券的实际账户持仓仍由组合/执行层处理，不因这次过滤而消失。
    return [
        security
        for security in snapshot.securities
        if security.listed
        and security.tradable
        and security.asset_type == "common_stock"
        and security.quality == "good"
    ]
