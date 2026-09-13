"""历史时点数据闸门；只选择当时可知的版本，不访问外部数据或修改记录。"""

# 延迟注解支持统一契约类型。
from __future__ import annotations

# 日期转换只依据注入的决策时间。
from datetime import datetime

# 纽约日期决定证券有效区间。
from zoneinfo import ZoneInfo

# 所有边界对象和哈希规则均复用唯一契约。
from quant_core.contracts import (
    ContractError,
    DataSnapshot,
    MarketDataRecord,
    SecurityRecord,
    canonical_hash,
    verify_record,
)


def build_snapshot(
    records: list[MarketDataRecord],
    securities: list[SecurityRecord],
    decision_time: datetime,
    calendar_version: str,
) -> DataSnapshot:
    """按 UTC 决策时间冻结行情和主表；返回快照，冲突或质量失败抛错，无副作用。"""
    # 借助空快照模型先验证决策时间，拒绝无时区或非 UTC 时间。
    DataSnapshot(
        snapshot_id="validation",
        decision_time=decision_time,
        calendar_version=calendar_version,
        securities=[],
        records=[],
        content_hash="",
    )
    # 不可知的未来输入不能影响过去快照的校验结果或哈希。
    visible = [record for record in records if record.available_at <= decision_time]
    # 每个证券交易日保存唯一修订事实。
    versions: dict[tuple[str, object, int], MarketDataRecord] = {}
    # 当前版本以证券和交易日为键选择。
    selected: dict[tuple[str, object], MarketDataRecord] = {}
    # 检查所有可知版本，而非静默接受同版本冲突。
    for record in visible:
        # 接入记录必须有可验证的封印。
        verify_record(record)
        # 事件在未来却自称已经可知属于输入错误。
        if record.event_time > decision_time:
            # 拒绝不可能的日行情事件时间。
            raise ContractError("行情事件晚于决策时间")
        # 相同证券交易日修订号不能表示不同内容或不同来源。
        identity = (record.security_id, record.session, record.revision)
        # 允许完全一致的重复投递。
        if identity in versions and versions[identity].content_hash != record.content_hash:
            # 数据源冲突须先人工明确优先级，而非静默覆盖。
            raise ContractError("行情相同版本内容冲突")
        # 留存已经验证的版本身份。
        versions[identity] = record
        # 高版本只在本时点可见时取代低版本。
        key = (record.security_id, record.session)
        # 同一时点确定地选择最高修订。
        if key not in selected or record.revision > selected[key].revision:
            # 仅修改本函数局部索引。
            selected[key] = record
    # 使用纽约日界线核验主表有效区间。
    market_date = decision_time.astimezone(ZoneInfo("America/New_York")).date()
    # 主表先按有效区间起点消解修订。
    security_versions: dict[tuple[str, object], SecurityRecord] = {}
    # 同版本内容冲突不能由排序消除。
    identities: dict[tuple[str, object, int], str] = {}
    # 主表独立执行时点过滤。
    for security in securities:
        # 未来主表修订不影响历史证券资格。
        if security.available_at > decision_time:
            # 该事实留待未来快照使用。
            continue
        # 历史主表也必须通过内容封印。
        verify_record(security)
        # 有效区间必须是正长度。
        if security.effective_to is not None and security.effective_to <= security.effective_from:
            # 错误区间不能悄悄当作证券已退市。
            raise ContractError("证券有效区间为空或倒置")
        # 识别同一区间的追加修订。
        identity_security = (security.security_id, security.effective_from, security.revision)
        # 拒绝同修订号不同内容。
        if (
            identity_security in identities
            and identities[identity_security] != security.content_hash
        ):
            # 无法证明哪一个版本可信时停止。
            raise ContractError("证券相同版本内容冲突")
        # 记录版本证据。
        identities[identity_security] = security.content_hash
        # 按区间起点索引最新可用版本。
        security_key = (security.security_id, security.effective_from)
        # 高版本优先且不依赖输入列表顺序。
        if (
            security_key not in security_versions
            or security.revision > security_versions[security_key].revision
        ):
            # 保留当前最高修订。
            security_versions[security_key] = security
    # 最终股票池只含决策日有效主表。
    current: dict[str, SecurityRecord] = {}
    # 对有效区间检查重叠，不能凭列表顺序挑一个行业。
    for security in security_versions.values():
        # 区间结束日不含当天。
        if security.effective_from <= market_date and (
            security.effective_to is None or market_date < security.effective_to
        ):
            # 同证券同时有效的两个区间是接入冲突。
            if security.security_id in current:
                # 要求数据维护者修复历史有效区间。
                raise ContractError("证券有效区间重叠")
            # 主表质量失败阻止正常业务继续。
            if security.quality != "good":
                # 不把异常主表当成正常可交易身份。
                raise ContractError("证券主表质量未通过")
            # 建立当前有效身份索引。
            current[security.security_id] = security
    # 只允许已知稳定身份的历史行情进入快照。
    chosen = sorted(
        (record for record in selected.values() if record.security_id in current),
        key=lambda record: (record.security_id, record.session),
    )
    # 最新可知修订质量失败时不能回退旧值掩盖异常。
    if any(record.quality != "good" for record in chosen):
        # 明确阻断整个正常决策。
        raise ContractError("行情质量未通过")
    # 稳定排序使输入重排不改变结果。
    masters = sorted(current.values(), key=lambda security: security.security_id)
    # 对最终内容计算快照身份，未来未选中记录完全不参与。
    payload = {
        "decision_time": decision_time.isoformat(),
        "calendar_version": calendar_version,
        "securities": [item.model_dump(mode="json") for item in masters],
        "records": [item.model_dump(mode="json") for item in chosen],
    }
    # 版本和时点均属于可复现输入。
    digest = canonical_hash(payload)
    # 输出冻结契约，适配器负责不可覆盖的持久化。
    return DataSnapshot(
        snapshot_id=digest,
        decision_time=decision_time,
        calendar_version=calendar_version,
        securities=masters,
        records=chosen,
        content_hash=digest,
    )
