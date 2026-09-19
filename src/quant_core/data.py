"""历史时点数据闸门；只选择当时可知的版本，不访问外部数据或修改记录。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

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
    """冻结决策时刻可知的行情和历史证券主表，供股票池与因子计算使用。

    decision_time 必须为 UTC。先过滤 available_at，再选择最高修订，最后验证质量；
    不能先选最终修订再回退旧值。证券有效区间按纽约日期左闭右开解释。
    时间/内容冲突、重叠主表或选中版本质量失败抛 ContractError（时间模型校验
    也可能抛 ValueError）；返回带内容指纹的快照，持久化由调用方完成。
    """
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
    # versions 以 (证券 ID, 交易日, 修订号) 保存已见完整记录，用来查同版本内容冲突；
    # selected 去掉修订号作键，只保留每证券每日的最高可知版本，后续成为快照行情。
    versions: dict[tuple[str, object, int], MarketDataRecord] = {}
    selected: dict[tuple[str, object], MarketDataRecord] = {}
    # 检查所有可知版本，而非静默接受同版本冲突。
    for record in visible:
        # 接入记录必须有可验证的封印。
        verify_record(record)
        # 事件在未来却自称已经可知属于输入错误。
        if record.event_time > decision_time:
            raise ContractError("行情事件晚于决策时间")
        # 相同证券交易日修订号不能表示不同内容或不同来源。
        identity = (record.security_id, record.session, record.revision)
        # 允许完全一致的重复投递。
        if identity in versions and versions[identity].content_hash != record.content_hash:
            raise ContractError("行情相同版本内容冲突")
        versions[identity] = record
        # 高版本只在本时点可见时取代低版本。
        key = (record.security_id, record.session)
        if key not in selected or record.revision > selected[key].revision:
            selected[key] = record
    # 使用纽约日界线核验主表有效区间。
    market_date = decision_time.astimezone(ZoneInfo("America/New_York")).date()
    # security_versions 是 (证券 ID, 有效起日)→最高可知主表版本；同一证券可有多个
    # 不同历史区间。identities 另按三元键保存内容哈希，防止同一修订号被重复解释。
    security_versions: dict[tuple[str, object], SecurityRecord] = {}
    identities: dict[tuple[str, object, int], str] = {}
    for security in securities:
        # 未来主表修订不影响历史证券资格。
        if security.available_at > decision_time:
            continue
        verify_record(security)
        # 有效区间必须是正长度。
        if security.effective_to is not None and security.effective_to <= security.effective_from:
            raise ContractError("证券有效区间为空或倒置")
        # 识别同一区间的追加修订。
        identity_security = (security.security_id, security.effective_from, security.revision)
        if (
            identity_security in identities
            and identities[identity_security] != security.content_hash
        ):
            raise ContractError("证券相同版本内容冲突")
        identities[identity_security] = security.content_hash
        security_key = (security.security_id, security.effective_from)
        # 高版本优先且不依赖输入列表顺序。
        if (
            security_key not in security_versions
            or security.revision > security_versions[security_key].revision
        ):
            security_versions[security_key] = security
    # current 才是本决策日的证券 ID→唯一有效主表；先完成各区间修订选择，再查重叠。
    current: dict[str, SecurityRecord] = {}
    # 对有效区间检查重叠，不能凭列表顺序挑一个行业。
    for security in security_versions.values():
        # 区间结束日不含当天。
        if security.effective_from <= market_date and (
            security.effective_to is None or market_date < security.effective_to
        ):
            if security.security_id in current:
                raise ContractError("证券有效区间重叠")
            # 主表质量失败阻止正常业务继续。
            if security.quality != "good":
                raise ContractError("证券主表质量未通过")
            current[security.security_id] = security
    # chosen 是将要冻结的行情列表，只保留 current 中的稳定身份；按证券/交易日排序
    # 同时供后续读者查看与哈希使用，不让输入投递顺序改变快照身份。
    chosen = sorted(
        (record for record in selected.values() if record.security_id in current),
        key=lambda record: (record.security_id, record.session),
    )
    # 最新可知修订质量失败时不能回退旧值掩盖异常。
    if any(record.quality != "good" for record in chosen):
        raise ContractError("行情质量未通过")
    # 稳定排序使输入重排不改变结果。
    masters = sorted(current.values(), key=lambda security: security.security_id)
    # payload 是指纹的完整输入：选中记录、UTC 决策时间和日历版本。model_dump 的
    # JSON 模式把日期等类型转为可编码值；digest 同时用作 snapshot_id 与 content_hash。
    # 这里只证明所选内容一致，哈希不能证明行情正确或来源可信。
    payload = {
        "decision_time": decision_time.isoformat(),
        "calendar_version": calendar_version,
        "securities": [item.model_dump(mode="json") for item in masters],
        "records": [item.model_dump(mode="json") for item in chosen],
    }
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
