"""Alpaca 日行情、当前证券资料与外部总回报证据的边界转换。

本模块不读取凭据或系统时间；只有显式调用采集函数才经注入传输访问行情。
SIP 原始价格和成交量不充当总回报序列，当前小候选集合不冒充历史股票池。
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from quant_core.contracts import (
    ContractError,
    DataSnapshot,
    MarketDataRecord,
    SecurityRecord,
    canonical_hash,
    seal_record,
)
from quant_core.data import build_snapshot

NEW_YORK = ZoneInfo("America/New_York")


class MarketTransport(Protocol):
    """仅提供 Market Data API 读取；账户授权和网络副作用由外部实现负责。"""

    def market_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """读取指定行情路径；权限、超时等失败必须向调用方传播。"""
        ...


def _utc(value: Any) -> datetime:
    """解析明确 UTC 的采集证据时间；未知或非 UTC 不能代替实际观测。"""
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if result.utcoffset() != timedelta(0):
        raise ContractError("采集证据需要明确 UTC 时间")
    return result


def _source(value: Any) -> str:
    """限制溯源地址为无认证信息、查询参数或片段的 HTTPS 资料地址。"""
    if not isinstance(value, str) or not value.strip():
        raise ContractError("缺少数据来源")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in value)
    ):
        raise ContractError("来源必须为不含凭据和查询参数的 HTTPS 地址")
    return value


class AlpacaCalendar:
    """保存实际下载的纽约常规交易时段；由采集范围和内容指纹确定版本。

    rows 的 date/open/close 来自 Trading API，开收盘钟点按纽约时区解释，
    因而夏令时与半日市无需硬编码 UTC 小时。没有下载的日期范围不能推断。
    """

    def __init__(self, rows: list[dict[str, Any]], start: date, end: date) -> None:
        """冻结给定含首尾日期的日历响应；重复日期、空响应及倒置时段失败。"""
        if start > end or not rows:
            raise ContractError("日历范围无效或响应为空")
        self.start = start
        self.end = end
        self._sessions: dict[date, tuple[datetime, datetime]] = {}
        for row in rows:
            day = date.fromisoformat(str(row["date"]))
            opening = datetime.combine(day, time.fromisoformat(str(row["open"])), NEW_YORK)
            closing = datetime.combine(day, time.fromisoformat(str(row["close"])), NEW_YORK)
            if day in self._sessions or not start <= day <= end or opening >= closing:
                raise ContractError("日历日期重复、越界或开收盘倒置")
            self._sessions[day] = (opening.astimezone(UTC), closing.astimezone(UTC))
        self.version = f"alpaca-calendar:{start}:{end}:{canonical_hash(rows)}"

    def _check(self, day: date) -> None:
        """拒绝采集区间之外的日期，避免把未知日历解释成休市。"""
        if not self.start <= day <= self.end:
            raise ContractError("日期超出已采集日历范围")

    def sessions(self, start: date, end: date) -> list[date]:
        """返回包含两个端点的交易日升序列表。"""
        self._check(start)
        self._check(end)
        return sorted(day for day in self._sessions if start <= day <= end)

    def open_at(self, session: date) -> datetime:
        """返回该交易日实际常规开盘 UTC 时间，非交易日明确失败。"""
        self._check(session)
        if session not in self._sessions:
            raise ContractError("查询日期不是已知交易日")
        return self._sessions[session][0]

    def close_at(self, session: date) -> datetime:
        """返回该交易日实际常规收盘 UTC 时间，包含官方半日市安排。"""
        self._check(session)
        if session not in self._sessions:
            raise ContractError("查询日期不是已知交易日")
        return self._sessions[session][1]

    def next_session(self, session: date) -> date:
        """返回严格晚于给定日期的首个已下载交易日；覆盖不足失败。"""
        self._check(session)
        following = sorted(day for day in self._sessions if day > session)
        if not following:
            raise ContractError("日历未覆盖下一交易日")
        return following[0]

    def is_open(self, at: datetime) -> bool:
        """按纽约日期和左闭右开的常规时段判断能否执行；范围外失败。"""
        at = _utc(at)
        day = at.astimezone(NEW_YORK).date()
        self._check(day)
        return day in self._sessions and self.open_at(day) <= at < self.close_at(day)


def fetch_daily_bars(
    transport: MarketTransport,
    symbols: list[str],
    start: date,
    end: date,
    feed: str = "sip",
    adjustment: str = "raw",
    asof: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """完整读取小候选集合的原始或仅拆股调整日行情，不回退 IEX 或合成数据。

    start/end 为纽约交易日期，均包含。Alpaca 多证券结果按证券优先排序，
    第一页可能只有第一只股票；必须读完 next_page_token 才验证证券覆盖。
    asof 固定证券代码映射日期，不表示价格在该时点已可用；调整成交量不得用于 ADV。
    """
    if adjustment not in {"raw", "split"}:
        raise ContractError("日行情只接受 raw 或 split 调整口径")
    if feed != "sip":
        raise ContractError("策略 ADV 需要 SIP 综合成交量，不能使用 IEX 替代")
    if not symbols or len(set(symbols)) != len(symbols) or start > end:
        raise ContractError("候选代码为空、重复或行情日期范围倒置")
    start_at = datetime.combine(start, time.min, NEW_YORK).astimezone(UTC)
    end_at = datetime.combine(end + timedelta(days=1), time.min, NEW_YORK).astimezone(UTC)
    # 结束日下一午夜是右开边界；减一微秒也兼容 API 包含 end 的规则。
    params: dict[str, Any] = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "start": start_at.isoformat(),
        "end": (end_at - timedelta(microseconds=1)).isoformat(),
        "adjustment": adjustment,
        "feed": feed,
        "sort": "asc",
        "limit": 10000,
    }
    if asof is not None:
        params["asof"] = asof.isoformat()
    result: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    tokens: set[str] = set()
    while True:
        response = transport.market_request("/v2/stocks/bars", params)
        if not isinstance(response, dict) or not isinstance(response.get("bars"), dict):
            raise ContractError("日行情响应没有 bars 映射")
        for symbol, rows in response["bars"].items():
            if symbol not in result or not isinstance(rows, list):
                raise ContractError("日行情返回范围外证券或无效序列")
            if not all(isinstance(row, dict) for row in rows):
                raise ContractError("日行情记录不是对象")
            result[symbol].extend(rows)
        token = response.get("next_page_token")
        if token is None:
            break
        if not isinstance(token, str) or not token or token in tokens:
            raise ContractError("行情分页 token 无效或循环")
        tokens.add(token)
        params = {**params, "page_token": token}
    if any(not rows for rows in result.values()):
        raise ContractError("完整分页后仍缺少候选证券行情")
    return result


def _supplement(
    value: dict[str, Any] | None, decision_time: datetime
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, date], float], datetime, str]:
    """验证外部分类与总回报证据，返回资产索引、研究价索引、可用时刻和指纹来源。

    这些字段证明资料有明确声明与可复现出处，不证明供应商算法真实正确；
    首次真实运行前仍须核实供应商方法及资料授权，不能只凭填了字符串即验收。
    """
    if value is None:
        raise ContractError("缺少补充资料：普通股类别、行业及真实总回报价格与方法证据")
    if value.get("schema_version") != "1.0.0" or value.get("quality") != "good":
        raise ContractError("补充资料版本或质量不合格")
    if value.get("price_basis") != "dividend_reinvestment_total_return":
        raise ContractError("补充研究价格必须明确现金股息再投资总回报口径")
    if value.get("currency") != "USD":
        raise ContractError("补充研究价格必须以 USD 表示")
    source = _source(value.get("source"))
    _source(value.get("methodology_source"))
    if not isinstance(value.get("methodology"), str) or not value["methodology"].strip():
        raise ContractError("缺少总回报计算方法说明")
    observed = _utc(value.get("observed_at"))
    available = _utc(value.get("available_at"))
    if observed > available or available > decision_time:
        raise ContractError("补充资料实际观测、可用或决策时间顺序无效")
    masters: dict[str, dict[str, Any]] = {}
    for row in value.get("securities", []):
        identity = row.get("asset_id")
        if not isinstance(identity, str) or not identity or identity in masters:
            raise ContractError("补充资料资产身份缺失或重复")
        if row.get("asset_type") != "common_stock" or not str(row.get("sector", "")).strip():
            raise ContractError("补充资料缺少普通股类别或行业")
        masters[identity] = row
    prices: dict[tuple[str, date], float] = {}
    for row in value.get("prices", []):
        key = (str(row["asset_id"]), date.fromisoformat(str(row["session"])))
        if key[1] > decision_time.astimezone(NEW_YORK).date():
            raise ContractError("补充研究价格包含未来交易日")
        price = float(row["total_return_close"])
        if key in prices or not math.isfinite(price) or price <= 0:
            raise ContractError("总回报价格重复、非有限或非正")
        prices[key] = price
    # 完整补充文件摘要把方法、来源和每个值绑定到记录；文件由应用追加保存。
    return masters, prices, available, f"{source}#sha256={canonical_hash(value)}"


def build_paper_snapshot(
    *,
    bars: dict[str, list[dict[str, Any]]],
    assets: list[dict[str, Any]],
    supplement: dict[str, Any] | None,
    calendar: AlpacaCalendar,
    observed_at: datetime,
    decision_time: datetime,
    candidates: list[str],
    feed: str = "sip",
) -> DataSnapshot:
    """拼合当前小候选范围的实际采集事实，保持原因子所需连续 253 日窗口。

    observed_at 是本次行情、证券与日历全部读取完成的实际 UTC 时间，decision_time
    不得早于它。本入口只支持本次纽约采集日的当前候选决策，不声称历史选股能力。
    缺行业、研究口径、日行情或身份时整体阻断，不偷偷缩减候选或压缩因子窗口。
    """
    observed_at, decision_time = _utc(observed_at), _utc(decision_time)
    market_day = decision_time.astimezone(NEW_YORK).date()
    if observed_at > decision_time or observed_at.astimezone(NEW_YORK).date() != market_day:
        raise ContractError("当前证券资料仅适用于采集当日且不能回填旧决策")
    if feed != "sip":
        raise ContractError("策略 ADV 需要 SIP 综合成交量")
    if not candidates or len(set(candidates)) != len(candidates) or set(bars) != set(candidates):
        raise ContractError("候选范围与完整行情证券集合不一致")
    masters, research, supplement_available, evidence = _supplement(supplement, decision_time)
    by_symbol: dict[str, dict[str, Any]] = {}
    identities: set[str] = set()
    for fetched_asset in assets:
        symbol, identity = str(fetched_asset["symbol"]), str(fetched_asset["id"])
        if symbol in by_symbol or identity in identities:
            raise ContractError("Alpaca 证券身份重复")
        by_symbol[symbol] = fetched_asset
        identities.add(identity)
    completed = [
        day
        for day in calendar.sessions(calendar.start, market_day)
        if calendar.close_at(day) <= decision_time
    ]
    window = completed[-253:]
    if len(window) != 253:
        raise ContractError("日历不足 253 个已收盘交易日")
    securities: list[SecurityRecord] = []
    records: list[MarketDataRecord] = []
    available = max(observed_at, supplement_available)
    for symbol in candidates:
        asset = by_symbol.get(symbol)
        if asset is None or asset.get("class", asset.get("asset_class")) != "us_equity":
            raise ContractError("候选缺少 Alpaca 美股证券身份")
        identity = str(asset["id"])
        master = masters.get(identity)
        if master is None or master.get("symbol") != symbol:
            raise ContractError("补充分类缺失或证券稳定身份与代码不一致")
        if not isinstance(asset.get("tradable"), bool) or asset.get("status") not in {
            "active",
            "inactive",
        }:
            raise ContractError("Alpaca 证券交易资格字段缺失或未知")
        # effective_from 只建立当日的候选资格；不把现在的行业与上市状态写回历史。
        securities.append(
            seal_record(
                SecurityRecord(
                    security_id=identity,
                    ticker=symbol,
                    sector=master["sector"],
                    effective_from=market_day,
                    listed=asset["status"] == "active",
                    tradable=asset["tradable"],
                    quality="good",
                    event_time=observed_at,
                    published_at=None,
                    first_seen_at=observed_at,
                    available_at=available,
                    availability_basis="actual",
                    source=f"alpaca-current-assets+{evidence}",
                )
            )
        )
        daily: dict[date, dict[str, Any]] = {}
        for row in bars[symbol]:
            stamp = _utc(row["t"])
            day = stamp.astimezone(NEW_YORK).date()
            if day in daily:
                raise ContractError("原始行情同证券交易日重复")
            if day not in completed or calendar.close_at(day) > observed_at:
                raise ContractError("原始日行情不是采集时已完成的交易日")
            daily[day] = row
        if any(day not in daily or (identity, day) not in research for day in window):
            raise ContractError("缺少连续 253 交易日原始或总回报价格，不能压缩窗口")
        for day in window:
            row = daily[day]
            # 原始成交量必须是整数股；不能截断非整数或将缺失解释成零成交。
            volume = row.get("v")
            if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
                raise ContractError("原始成交量必须为非负整数股")
            records.append(
                seal_record(
                    MarketDataRecord(
                        security_id=identity,
                        session=day,
                        raw_open=row["o"],
                        raw_close=row["c"],
                        total_return_close=research[(identity, day)],
                        volume=volume,
                        quality="good",
                        event_time=calendar.close_at(day),
                        published_at=None,
                        first_seen_at=observed_at,
                        available_at=available,
                        availability_basis="actual",
                        source=f"alpaca-sip-raw+{evidence}",
                    )
                )
            )
    return build_snapshot(records, securities, decision_time, calendar.version)


def fetch_corporate_actions(
    transport: MarketTransport, symbols: list[str], start: date, end: date
) -> dict[str, Any]:
    """完整分页读取窗口内公司行动，保留类别和原始字段供身份连续性闸门解释。

    请求 data_quality=all 以保留尚不完整的行动，避免默认过滤把身份疑点隐藏。
    返回采集范围与分页完成声明；真实 observed_at 必须由调用方在采集完成后补入。
    没有行动是有效空集合，网络失败或分页不全不能用空集合替代。
    """
    if not symbols or len(set(symbols)) != len(symbols) or start > end:
        raise ContractError("公司行动候选或日期范围无效")
    params: dict[str, Any] = {
        "symbols": ",".join(symbols),
        "start": str(start),
        "end": str(end),
        "limit": 1000,
        "data_quality": "all",
    }
    actions: dict[str, list[dict[str, Any]]] = {}
    tokens: set[str] = set()
    while True:
        response = transport.market_request("/v1/corporate-actions", params)
        if not isinstance(response, dict) or not isinstance(
            response.get("corporate_actions"), dict
        ):
            raise ContractError("公司行动响应没有类别映射")
        for kind, rows in response["corporate_actions"].items():
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ContractError("公司行动记录不是对象序列")
            actions.setdefault(kind, []).extend(rows)
        token = response.get("next_page_token")
        if token is None:
            break
        if not isinstance(token, str) or not token or token in tokens:
            raise ContractError("公司行动分页 token 无效或循环")
        tokens.add(token)
        params = {**params, "page_token": token}
    return {
        "symbols": list(symbols),
        "corporate_actions": actions,
        "start": str(start),
        "end": str(end),
        "pagination_complete": True,
    }


def _ma_actions(
    evidence: dict[str, Any], window: list[date], market_day: date, decision_time: datetime
) -> dict[str, str]:
    """找出窗口内不能安全接续身份的证券；现金股息不调整价格型 MA。

    合并的收购方也保守排除，不推测换股事件是否影响其价格连续性。行动日期未知时
    不能证明落在窗口之外；未知类别、无可识别证券的行动使整批资料失败。
    """
    if evidence.get("pagination_complete") is not True:
        raise ContractError("公司行动分页未完成")
    if _utc(evidence.get("observed_at")) > decision_time:
        raise ContractError("公司行动在决策之后才取得")
    if (
        date.fromisoformat(str(evidence.get("start"))) > window[0]
        or date.fromisoformat(str(evidence.get("end"))) < market_day
    ):
        raise ContractError("公司行动范围没有覆盖指标窗口和当前日期")
    groups = evidence.get("corporate_actions")
    if not isinstance(groups, dict):
        raise ContractError("公司行动缺少类别映射")
    exclusions: dict[str, str] = {}
    for kind, rows in groups.items():
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ContractError("公司行动记录不是对象序列")
        for row in rows:
            symbols = {
                value
                for key, value in row.items()
                if (key == "symbol" or key.endswith("_symbol")) and isinstance(value, str) and value
            }
            if not symbols:
                raise ContractError("公司行动缺少可识别证券代码")
            effective = row.get("ex_date", row.get("effective_date", row.get("process_date")))
            if effective is None:
                for symbol in symbols:
                    exclusions[symbol] = "公司行动缺少生效日期，无法证明窗口身份连续"
                continue
            action_day = date.fromisoformat(str(effective))
            if not window[0] <= action_day <= market_day:
                continue
            if kind == "cash_dividends":
                continue
            if kind in {"forward_splits", "reverse_splits"}:
                # 仅拆股调整价格由 Alpaca 提供；比例必须存在且为正，不能猜测缺失比例。
                rates = [row.get("new_rate"), row.get("old_rate")]
                if all(
                    not isinstance(rate, bool)
                    and isinstance(rate, (int, float))
                    and math.isfinite(rate)
                    and rate > 0
                    for rate in rates
                ):
                    continue
            for symbol in symbols:
                exclusions[symbol] = f"窗口内存在未支持的身份或价格连续性行动：{kind}"
    return exclusions


def _ma_daily(
    rows: list[dict[str, Any]], window: list[date], label: str
) -> dict[date, dict[str, Any]]:
    """只选择二十个完整交易日，未来或尚未收盘的日线不进入当前快照及哈希。"""
    daily: dict[date, dict[str, Any]] = {}
    for row in rows:
        day = _utc(row["t"]).astimezone(NEW_YORK).date()
        if day not in window:
            continue
        if day in daily:
            raise ContractError(f"{label}同证券交易日重复")
        daily[day] = row
    if set(daily) != set(window):
        raise ContractError(f"{label}缺少连续 20 个完整交易日，不能向前补日")
    return daily


def build_ma_snapshot(
    *,
    bars: dict[str, list[dict[str, Any]]],
    split_bars: dict[str, list[dict[str, Any]]],
    assets: list[dict[str, Any]],
    identity_evidence: dict[str, Any],
    corporate_actions: dict[str, Any],
    calendar: AlpacaCalendar,
    observed_at: datetime,
    decision_time: datetime,
    candidates: list[str],
    feed: str = "sip",
) -> tuple[DataSnapshot, dict[str, str]]:
    """建立价格型 MA 快照及逐证券排除理由，不填造行业或总回报。

    证券证据 records 必须含 symbol、alpaca_asset_id、asset_type_evidence=common_stock、
    HTTPS source 和实际 observed_at；资产 ID 的绑定由上游核实后显式保存。原始日线供
    估值和流动性，split 日线只提供 MA 收盘价。全局时间/范围矛盾抛异常；候选个体
    缺价、身份或复杂行动会退出并留理由，即使全部退出也返回可解释的空快照。
    """
    observed_at, decision_time = _utc(observed_at), _utc(decision_time)
    market_day = decision_time.astimezone(NEW_YORK).date()
    if observed_at > decision_time or observed_at.astimezone(NEW_YORK).date() != market_day:
        raise ContractError("当前证券资料仅适用于采集当日且不能回填旧决策")
    if feed != "sip":
        raise ContractError("MA 流动性必须使用 SIP 原始成交量")
    if not candidates or len(candidates) != len(set(candidates)):
        raise ContractError("MA 候选代码为空或重复")
    if set(bars) - set(candidates) or set(split_bars) - set(candidates):
        raise ContractError("MA 行情包含候选范围外证券")
    completed = [
        day
        for day in calendar.sessions(calendar.start, market_day)
        if calendar.close_at(day) <= observed_at
    ]
    window = completed[-20:]
    if len(window) != 20:
        raise ContractError("日历不足 20 个已完成交易日")
    action_symbols = corporate_actions.get("symbols")
    if (
        not isinstance(action_symbols, list)
        or not all(isinstance(symbol, str) for symbol in action_symbols)
        or not set(candidates).issubset(action_symbols)
    ):
        raise ContractError("公司行动没有覆盖本轮候选证券范围")
    exclusions = _ma_actions(corporate_actions, window, market_day, decision_time)
    # 只输出本轮候选的排除理由；收购目标等旁系证券不成为隐含候选。
    exclusions = {symbol: reason for symbol, reason in exclusions.items() if symbol in candidates}
    by_symbol: dict[str, dict[str, Any]] = {}
    identities: set[str] = set()
    for asset in assets:
        symbol, identity = asset.get("symbol"), asset.get("id")
        if not isinstance(symbol, str) or not isinstance(identity, str) or not identity:
            raise ContractError("Alpaca 证券身份缺失")
        if symbol in by_symbol or identity in identities:
            raise ContractError("Alpaca 证券身份重复")
        by_symbol[symbol], identities = asset, identities | {identity}
    evidence_rows = identity_evidence.get("records")
    if not isinstance(evidence_rows, list):
        raise ContractError("普通股证据缺少 records 序列")
    by_evidence: dict[str, dict[str, Any]] = {}
    for evidence in evidence_rows:
        if not isinstance(evidence, dict) or not isinstance(evidence.get("symbol"), str):
            raise ContractError("普通股证据记录无效")
        if evidence["symbol"] in by_evidence:
            raise ContractError("普通股证据代码重复")
        by_evidence[evidence["symbol"]] = evidence
    securities: list[SecurityRecord] = []
    records: list[MarketDataRecord] = []
    for symbol in candidates:
        if symbol in exclusions:
            continue
        try:
            asset, evidence = by_symbol[symbol], by_evidence[symbol]
            identity = asset["id"]
            if (
                asset.get("class", asset.get("asset_class")) != "us_equity"
                or asset.get("status") != "active"
                or asset.get("tradable") is not True
            ):
                raise ContractError("当前证券不是可交易的活跃美股")
            if (
                evidence.get("alpaca_asset_id") != identity
                or evidence.get("asset_type_evidence") != "common_stock"
            ):
                raise ContractError("普通股证据未绑定当前 Alpaca 稳定身份")
            source = _source(evidence.get("source"))
            evidence_seen = _utc(evidence.get("observed_at"))
            evidence_available = _utc(evidence.get("available_at", evidence_seen))
            if evidence_available < evidence_seen or evidence_available > observed_at:
                raise ContractError("普通股资料晚于本轮采集时点")
            raw, split = (
                _ma_daily(bars[symbol], window, "原始日线"),
                _ma_daily(split_bars[symbol], window, "拆股日线"),
            )
            symbol_records: list[MarketDataRecord] = []
            for day in window:
                original, adjusted = raw[day], split[day]
                # 复权响应中的成交量故意不读取，避免把拆股前调整股数充当实际成交股数。
                volume = original.get("v")
                if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
                    raise ContractError("原始成交量必须为非负整数股")
                for value in (original.get("o"), original.get("c"), adjusted.get("c")):
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value <= 0
                    ):
                        raise ContractError("原始或拆股价格必须为有限正数")
                symbol_records.append(
                    seal_record(
                        MarketDataRecord(
                            security_id=identity,
                            session=day,
                            raw_open=original["o"],
                            raw_close=original["c"],
                            split_adjusted_close=adjusted["c"],
                            total_return_close=None,
                            volume=volume,
                            quality="good",
                            event_time=calendar.close_at(day),
                            published_at=None,
                            first_seen_at=observed_at,
                            available_at=observed_at,
                            availability_basis="actual",
                            source="alpaca-sip-raw+alpaca-sip-split",
                        )
                    )
                )
            # 当前身份从本轮采集日开始；历史行情不被误写成历史候选成员资格。
            security = seal_record(
                SecurityRecord(
                    security_id=identity,
                    ticker=symbol,
                    sector=None,
                    effective_from=market_day,
                    listed=True,
                    tradable=True,
                    quality="good",
                    event_time=observed_at,
                    published_at=None,
                    first_seen_at=observed_at,
                    available_at=observed_at,
                    availability_basis="actual",
                    source=f"alpaca-current-assets+{source}#sha256={canonical_hash(evidence)}",
                )
            )
        except (KeyError, ValueError, TypeError) as error:
            exclusions[symbol] = (
                str(error)
                if isinstance(error, ContractError)
                else "候选行情或身份字段缺失或格式无效"
            )
            continue
        records.extend(symbol_records)
        securities.append(security)
    return build_snapshot(records, securities, decision_time, calendar.version), exclusions
