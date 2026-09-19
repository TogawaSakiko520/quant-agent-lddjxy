"""将 Alpaca Paper 的原始订单、成交和账户事实接入现有执行契约。

模块导入和对象构造不访问账户。真实传输使用官方个人 TradingClient，现金与购买力
分别保留；本阶段只发送整股限价 DAY 单，无法表达的事实明确阻断，绝不截断碎股。
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from time import sleep
from typing import Any, Protocol, cast
from urllib.parse import quote

from quant_core.contracts import (
    AccountSnapshot,
    Clock,
    ContractError,
    FillEvent,
    OrderEvent,
    OrderIntent,
    OrderNotSent,
    OrderRecord,
    OrderStatus,
    PaperQueueTestContext,
    canonical_hash,
)

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


class AlpacaHTTPError(ConnectionError):
    """只保留 HTTP 状态码；上层不得把供应商错误正文或请求头写入报告。"""

    def __init__(self, status: int) -> None:
        """保存安全状态码，使查单的 404 与网络未知状态能够区分。"""
        self.status = status
        super().__init__(f"Alpaca HTTP {status}")


class AlpacaTransport(Protocol):
    """注入原始 JSON 传输；离线测试实现不持有凭据或网络客户端。"""

    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """执行固定主机上的相对 API 路径，返回未转换金额字符串的响应。"""
        ...


class AlpacaSDKTransport:
    """封装官方 SDK 的个人 Paper API，禁止重试写请求、重定向和无期限等待。

    SDK 模型中的 float 会损失十进制事实，因此调用其原始 get/post/delete 传输。
    SDK 版本由项目锁固定；_retry 和 _session 是需随 SDK 升级重新验收的封装边界。
    """

    def __init__(self, key: str, secret: str, *, trading_url: str = PAPER_URL) -> None:
        """只构造 Paper 客户端；参数必须来自用户明确指定的安全凭据来源。"""
        if trading_url != PAPER_URL or not key or not secret:
            raise ContractError("需要指定 Paper 端点与非空凭据")
        client_type = importlib.import_module("alpaca.trading.client").TradingClient
        self._client: Any = client_type(key, secret, paper=True, raw_data=True)
        # SDK 默认会重试 429，且未设置 HTTP 超时。提交只允许单次请求；即便超时，
        # 调用方仍按 client_order_id 恢复，不能把超时视为确定未受理。
        self._client._retry = 0
        self._client._session.trust_env = False
        self._client._session.request = partial(self._client._session.request, timeout=(10, 30))
        market_type = importlib.import_module(
            "alpaca.data.historical.stock"
        ).StockHistoricalDataClient
        self._market: Any = market_type(key, secret, raw_data=True)
        self._market._retry = 0
        self._market._session.trust_env = False
        self._market._session.request = partial(self._market._session.request, timeout=(10, 30))
        if self._market._base_url != DATA_URL:
            raise ContractError("SDK 行情端点不在白名单")
        if self._client._base_url != PAPER_URL:
            raise ContractError("SDK 实际端点不是 Paper")

    @classmethod
    def from_environment(
        cls, key_env: str, secret_env: str, *, trading_url: str = PAPER_URL
    ) -> AlpacaSDKTransport:
        """只读取指定的两个环境变量；不搜索文件、其他变量或尝试未知密钥。"""
        if not key_env or not secret_env or key_env == secret_env:
            raise ContractError("凭据环境变量名必须明确且不同")
        key, secret = os.environ.get(key_env), os.environ.get(secret_env)
        if not key or not secret:
            raise ContractError("指定凭据环境变量尚未提供")
        return cls(key, secret, trading_url=trading_url)

    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        """单次请求固定 Paper 主机；任何异常只暴露安全类别和 HTTP 状态。"""
        if method not in {"GET", "POST", "DELETE"} or not path.startswith("/"):
            raise ContractError("不支持的 Paper 请求")
        if "://" in path or "?" in path or ".." in path or path.startswith("//"):
            raise ContractError("Paper 路径必须为固定主机上的相对路径")
        if self._client._base_url != PAPER_URL or self._client._retry != 0:
            raise ContractError("Paper 传输端点或重试配置已改变")
        try:
            return getattr(self._client, method.lower())(path, data=data)
        except Exception as exc:
            # 原 SDK APIError 可能带供应商全文；异常链也禁止泄露给 CLI traceback。
            status = getattr(exc, "status_code", None)
            if isinstance(status, int):
                raise AlpacaHTTPError(status) from None
            if "timeout" in type(exc).__name__.lower():
                raise TimeoutError("Alpaca 请求超时，订单状态可能未知") from None
            raise ConnectionError("Alpaca 请求失败，需恢复核对") from None

    def market_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """单次读取固定行情主机；v2行情与唯一v1公司行动路径明确分离。"""
        if (not path.startswith("/v2/") and path != "/v1/corporate-actions") or any(
            value in path for value in ("..", "?", "://")
        ):
            raise ContractError("行情路径必须是固定 v2 相对路径")
        if self._market._base_url != DATA_URL or self._market._retry != 0:
            raise ContractError("行情端点或重试配置已改变")
        try:
            if path == "/v1/corporate-actions":
                return self._market.get(path[3:], data=params, api_version="v1")
            return self._market.get(path[3:], data=params)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if isinstance(status, int):
                raise AlpacaHTTPError(status) from None
            if "timeout" in type(exc).__name__.lower():
                raise TimeoutError("Alpaca 行情请求超时") from None
            raise ConnectionError("Alpaca 行情请求失败") from None


def _object(value: Any) -> dict[str, Any]:
    """要求供应商返回 JSON 对象，拒绝把错误页面或空响应当成事实。"""
    if not isinstance(value, dict):
        raise ContractError("Alpaca 响应不是对象")
    return cast(dict[str, Any], value)


def _rows(value: Any) -> list[dict[str, Any]]:
    """要求列表中的每一项都是对象，不丢弃无效记录。"""
    if not isinstance(value, list):
        raise ContractError("Alpaca 响应不是列表")
    return [_object(row) for row in value]


def _number(value: Any) -> Decimal:
    """从供应商十进制字符串解析数值；拒绝浮点精度损失和非有限数。"""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ContractError("Alpaca 数值必须是原始十进制字符串")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ContractError("Alpaca 数值无效") from None
    if not number.is_finite():
        raise ContractError("Alpaca 数值必须有限")
    return number


def _shares(value: Any) -> int:
    """仅把已确认非负整股事实交给当前核心，碎股和空头不能向零截断。"""
    number = _number(value)
    if number < 0 or number != number.to_integral_value():
        raise ContractError("当前 Paper 核心不支持碎股或空头事实")
    return int(number)


def _time(value: Any) -> datetime:
    """解析供应商带时区时间，拒绝用本机时刻填补缺失的成交时间。"""
    if not isinstance(value, str):
        raise ContractError("Alpaca 缺少事实时间")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("Alpaca 事实时间无效") from None
    if result.tzinfo is None:
        raise ContractError("Alpaca 事实时间缺少时区")
    return result


class AlpacaPaperBroker:
    """把只读事实和受控写单连接到 ExecutionService；不维护第二份账本。

    intents 每次读取持久化意图，重启后仍能核对客户端身份。symbols 以稳定证券 ID
    索引当前展示代码。activity_after 是已留存初始账户快照的边界，不能每次重启移动。
    Paper 现金按模拟即时结算处理；额外购买力仅保留在原始事实，不扩大现金预算。
    """

    def __init__(
        self,
        transport: AlpacaTransport,
        account_id: str,
        clock: Clock,
        symbols: dict[str, str],
        intents: Callable[[], list[OrderIntent]],
        activity_after: datetime,
        *,
        trading_enabled: bool = False,
        baseline_activities: list[dict[str, Any]] | None = None,
        clock_wait: Callable[[float], None] = sleep,
    ) -> None:
        """保存依赖而不联网；写能力默认关闭，应用须在用户授权后显式启用。"""
        if not account_id or activity_after.tzinfo is None:
            raise ContractError("Paper 需要指定账户与带时区的初始快照边界")
        if len(set(symbols.values())) != len(symbols):
            raise ContractError("同一 Paper 代码不能映射多个证券身份")
        self.transport = transport
        self.account_id = account_id
        self.clock = clock
        self.symbols = dict(symbols)
        self.intents = intents
        self.activity_after = activity_after
        self.trading_enabled = trading_enabled
        # 应用只在独立排队测试通过输入闸门后显式绑定此上下文；普通装配没有豁免。
        self.queue_test_context: PaperQueueTestContext | None = None
        self.clock_wait = clock_wait
        self.queue_clock_samples: list[dict[str, Any]] = []
        # 初态已含的活动用ID和完整内容摘要固定；只跳过完全相同的历史事实。
        # 日期过滤不足以表达初始化入金等活动边界，不能仅凭类型或日期忽略资金变化。
        self._baseline_activities: dict[str, str] = {}
        for row in baseline_activities or []:
            identity = row.get("id")
            if (
                not isinstance(identity, str)
                or not identity
                or identity in self._baseline_activities
            ):
                raise ContractError("初态账户活动身份缺失或重复")
            self._baseline_activities[identity] = canonical_hash(row)

    def verify_account(self) -> dict[str, Any]:
        """读取账户并比对指定身份、美元币种和交易资格；失败不开放写能力。"""
        account = _object(self.transport.request("GET", "/account"))
        if account.get("id") != self.account_id:
            raise ContractError("Paper 账户身份与指定账户不符")
        if account.get("currency") != "USD" or account.get("status") != "ACTIVE":
            raise ContractError("Paper 账户不是有效美元账户")
        if any(
            account.get(key) is not False
            for key in ("trading_blocked", "account_blocked", "trade_suspended_by_user")
        ):
            raise ContractError("Paper 账户禁止交易或资格字段缺失")
        return account

    def _paginate(
        self, path: str, params: dict[str, Any], cursor_name: str
    ) -> list[dict[str, Any]]:
        """订单短页或活动空页结束读取；满页继续按ID，重复游标拒绝部分结果。"""
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        query = dict(params)
        for _ in range(10000):
            page = _rows(self.transport.request("GET", path, query))
            if not page:
                return result
            for row in page:
                identity = row.get("id")
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise ContractError("Alpaca 分页身份缺失或重复，不能确认完整性")
                seen.add(identity)
            result.extend(page)
            # 订单列表按limit返回至多该数量，短页已穷尽本次查询；实际Paper服务在
            # 短页后仍可能忽略ID游标重复旧单，不能强求额外空页。满页仍须继续，
            # 身份重复或不前进则明确阻断，绝不跳过同一提交时刻的订单。
            if path == "/orders" and len(page) < int(params["limit"]):
                return result
            # 新版订单 API 的 ID 游标不能与时间过滤共用；第一页已限定起点，
            # 后续从最后订单身份继续，避免相同时刻的订单被时间游标跳过。
            if cursor_name == "after_order_id":
                query.pop("after", None)
            query[cursor_name] = page[-1]["id"]
        raise ContractError("Alpaca 分页超出安全上限，不能放行截断数据")

    def raw_orders(self) -> list[dict[str, Any]]:
        """读取初态边界后的订单及全账户开放订单；旧开放订单不能因时间过滤消失。"""
        recent = self._paginate(
            "/orders",
            {
                "status": "all",
                "limit": 500,
                "direction": "asc",
                "after": self.activity_after.isoformat(),
            },
            "after_order_id",
        )
        # 两次读取之间订单可能更新。开放查询的较新事实覆盖同 ID 的旧响应；
        # 不同 client_order_id 的身份冲突仍在 _record/执行存储中拒绝。
        combined = {row["id"]: row for row in recent}
        for row in self.raw_open_orders():
            combined[row["id"]] = row
        return list(combined.values())

    def raw_open_orders(self) -> list[dict[str, Any]]:
        """不设时间和证券过滤读取全账户开放订单，初态检查和每次恢复共同使用。"""
        return self._paginate(
            "/orders", {"status": "open", "limit": 500, "direction": "asc"}, "after_order_id"
        )

    def raw_activities(self) -> list[dict[str, Any]]:
        """读取初始快照之后的全部活动；包含非成交活动以发现未知资金变动。"""
        return self._paginate(
            "/account/activities",
            {"after": self.activity_after.isoformat(), "direction": "asc", "page_size": 100},
            "page_token",
        )

    def read_snapshot(self) -> dict[str, Any]:
        """采集完整事实并在前后比较现金与持仓，变化时拒绝把混合时点当成初态。

        HTTP读取不具备跨端点原子性；前后稳定检查只提供本次证据，不是账户锁。
        应用仍要求独占首次空仓账户，运行期间的外部活动继续由恢复核对阻断。
        """
        account = self.verify_account()
        positions = _rows(self.transport.request("GET", "/positions"))
        result = {
            "account": account,
            "positions": positions,
            "orders": self.raw_orders(),
            "open_orders": self.raw_open_orders(),
            "historical_orders": self._paginate(
                "/orders", {"status": "all", "limit": 500, "direction": "asc"}, "after_order_id"
            ),
            "activities": self.raw_activities(),
        }
        following = self.verify_account()
        following_positions = _rows(self.transport.request("GET", "/positions"))
        before_holdings = sorted(positions, key=lambda row: str(row.get("asset_id")))
        after_holdings = sorted(following_positions, key=lambda row: str(row.get("asset_id")))
        # 持仓市值随行情变化，不是数量/成本账务变化；只比较需要入账的经济字段。
        before_facts = [
            {key: row.get(key) for key in ("asset_id", "symbol", "side", "qty", "cost_basis")}
            for row in before_holdings
        ]
        after_facts = [
            {key: row.get(key) for key in ("asset_id", "symbol", "side", "qty", "cost_basis")}
            for row in after_holdings
        ]
        if account.get("cash") != following.get("cash") or before_facts != after_facts:
            raise ContractError("账户采集期间现金或持仓改变，需重新取得稳定初态")
        result["observed_at"] = self.clock.now().isoformat()
        return result

    def assets(self) -> list[dict[str, Any]]:
        """逐一读取显式候选的当前证券属性，不把当前列表冒充历史股票池。"""
        return [
            _object(self.transport.request("GET", f"/assets/{quote(symbol, safe='')}"))
            for symbol in self.symbols.values()
        ]

    def calendar(self, start: date, end: date) -> list[dict[str, Any]]:
        """读取指定日期范围的 Alpaca 交易日历，供应用与注入日历交叉核对。"""
        return _rows(
            self.transport.request("GET", "/calendar", {"start": str(start), "end": str(end)})
        )

    def _intent(self, client_id: str) -> OrderIntent:
        """从持久化意图查找授权身份；未知远端订单不可自动创造策略决策。"""
        matches = [item for item in self.intents() if item.client_order_id == client_id]
        if len(matches) != 1:
            raise ContractError("发现未知或冲突的外部订单身份")
        return matches[0]

    def _record(self, row: dict[str, Any]) -> OrderRecord:
        """核对订单经济条件后映射状态；累计成交只更新订单，不生成成交事件。"""
        intent = self._intent(str(row.get("client_order_id", "")))
        if (
            intent.account_id != self.account_id
            or row.get("asset_id") != intent.security_id
            or row.get("symbol") != self.symbols.get(intent.security_id)
            or str(row.get("side", "")).upper() != intent.side
            or _shares(row.get("qty")) != intent.quantity
            or row.get("type") != "limit"
            or row.get("time_in_force") != "day"
            or row.get("extended_hours") is not False
            or _number(row.get("limit_price")) != intent.limit_price
        ):
            raise ContractError("Alpaca 订单与已保存意图不一致")
        mapping: dict[str, OrderStatus] = {
            "new": "OPEN",
            "accepted": "OPEN",
            "pending_new": "OPEN",
            "partially_filled": "PARTIAL",
            "filled": "FILLED",
            "canceled": "CANCELED",
            "expired": "CANCELED",
            "rejected": "REJECTED",
            "pending_cancel": "CANCEL_PENDING",
        }
        # suspended/replaced/done_for_day 等没有足够的终态保证，统一保留 UNKNOWN。
        status = mapping.get(str(row.get("status")), "UNKNOWN")
        broker_id = row.get("id")
        if not isinstance(broker_id, str) or not broker_id:
            raise ContractError("Alpaca 订单缺少券商身份")
        try:
            return OrderRecord(
                intent=intent,
                status=status,
                broker_order_id=broker_id,
                filled_quantity=_shares(row.get("filled_qty")),
            )
        except ValueError:
            raise ContractError("Alpaca 订单状态与累计成交数量不一致") from None

    def orders(self) -> list[OrderRecord]:
        """读取并核对全部订单；任何范围外订单阻断恢复而不被自动撤销。"""
        return [self._record(row) for row in self.raw_orders()]

    def query(self, client_order_id: str) -> OrderRecord | None:
        """按稳定客户身份查单；404 只表示本次未查到，不能授权重发。"""
        try:
            row = self.transport.request(
                "GET", "/orders:by_client_order_id", {"client_order_id": client_order_id}
            )
        except AlpacaHTTPError as exc:
            if exc.status == 404:
                return None
            raise
        return self._record(_object(row))

    def _submission_payload(self, intent: OrderIntent) -> dict[str, Any]:
        """只做发送前核验与请求构造；本方法绝不POST，失败可确认为未发送。"""
        if not self.trading_enabled or self._intent(intent.client_order_id) != intent:
            raise ContractError("Paper 写能力关闭或意图尚未持久化")
        # 限价步长属于券商接入契约；适配器只验证，不能发送与落盘意图不同的取整价。
        tick = Decimal("0.01") if intent.limit_price >= 1 else Decimal("0.0001")
        if intent.limit_price % tick:
            raise ContractError("Paper 限价不符合最小价格步长，须在规划时确定合法限价")
        account = self.verify_account()
        if intent.account_id != self.account_id or intent.security_id not in self.symbols:
            raise ContractError("Paper 委托超出账户或证券范围")
        context = self.queue_test_context
        if context is not None:
            # 临近开盘或账户/身份/价格改变时拒绝；再读远端时钟，防止风控后跨入开市。
            remote_clock = _object(self.transport.request("GET", "/clock"))
            before = self.clock.now()
            sampled_at = _time(remote_clock.get("timestamp"))
            ahead = (sampled_at - before).total_seconds()
            # 券商时钟可能稍领先本机；最多实际等待一秒让本机追上，绝不改写事实时刻。
            # 等待后仍严格拒绝未来/过期时钟，较大偏差直接拒绝，普通策略没有此路径。
            waiting = ahead if 0 < ahead <= 1 else 0.0
            if waiting:
                self.clock_wait(waiting)
            now = self.clock.now()
            self.queue_clock_samples.append(
                {
                    "remote": sampled_at.isoformat(),
                    "local_before": before.isoformat(),
                    "local_after": now.isoformat(),
                    "requested_wait_seconds": waiting,
                    "age_seconds": (now - sampled_at).total_seconds(),
                    "is_open": remote_clock.get("is_open"),
                }
            )
            if (
                context.account_id != intent.account_id
                or context.client_order_id != intent.client_order_id
                or context.security_id != intent.security_id
                or context.decision_id != intent.decision_id
                or intent.quantity != 1
                or intent.side != "BUY"
                or intent.limit_price > context.reference_close
                or intent.limit_price + intent.reserved_fee > Decimal("500")
                or intent.eligible_at != context.next_open
                or context.next_open - now < timedelta(minutes=15)
                or remote_clock.get("is_open") is not False
                or _time(remote_clock.get("next_open")) != context.next_open
                or not 0 <= (now - _time(remote_clock.get("timestamp"))).total_seconds() <= 60
            ):
                raise ContractError("Paper 排队测试的单股、金额、身份或休市边界不符")
        elif self.clock.now() < intent.eligible_at:
            raise ContractError("Paper 委托尚未到允许执行时间")
        # 只建仓的首轮以现金及非保证金购买力的较小值再设一道边界；购买力不可
        # 冒充模拟资金预算，后者已经由应用和统一风控单独核验。
        if intent.side != "BUY":
            raise ContractError("本阶段 Paper 只允许已授权的首次建仓")
        available = min(
            _number(account.get("cash")), _number(account.get("non_marginable_buying_power"))
        )
        if intent.limit_price * intent.quantity + intent.reserved_fee > available:
            raise ContractError("Paper 现金或非保证金购买力不足")
        return {
            "symbol": self.symbols[intent.security_id],
            "qty": str(intent.quantity),
            "side": intent.side.lower(),
            "type": "limit",
            "time_in_force": "day",
            "limit_price": str(intent.limit_price),
            "extended_hours": False,
            "client_order_id": intent.client_order_id,
        }

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """经唯一执行服务单次提交；明确区分POST前拒绝与发送后未知事实。"""
        try:
            payload = self._submission_payload(intent)
        except (ContractError, ConnectionError, TimeoutError) as exc:
            # 准备阶段没有写请求；专属异常允许服务记录本地拒绝，避免制造伪未知单。
            raise OrderNotSent(str(exc)) from exc
        # POST及其响应解析都在专属异常转换之外，超时或不合法回执仍需按原身份恢复。
        return self._record(_object(self.transport.request("POST", "/orders", payload)))

    def cancel(self, client_order_id: str) -> OrderRecord:
        """只撤销已知策略订单；请求成功后重新查单，撤单与成交竞态保留远端事实。"""
        if not self.trading_enabled:
            raise ContractError("Paper 写能力关闭")
        self.verify_account()
        current = self.query(client_order_id)
        if current is None or current.broker_order_id is None:
            raise ContractError("无法识别待撤销的 Paper 订单")
        self.transport.request("DELETE", f"/orders/{quote(current.broker_order_id, safe='')}")
        result = self.query(client_order_id)
        if result is None:
            raise ConnectionError("撤单后状态未确认，需恢复核对")
        return result

    def events(self) -> list[OrderEvent | FillEvent]:
        """只从逐笔 FILL 活动建立新增成交；不以累计股数或平均价格反推成交。"""
        orders = {row.broker_order_id: row for row in self.orders()}
        events: list[OrderEvent | FillEvent] = []
        for row in self.raw_activities():
            baseline = self._baseline_activities.get(str(row["id"]))
            if baseline is not None:
                if canonical_hash(row) != baseline:
                    raise ContractError("已纳入初态的账户活动内容发生变化")
                continue
            if row.get("activity_type") != "FILL":
                raise ContractError("存在未支持的非成交账户活动，停止并核对现金或公司行动")
            order = orders.get(row.get("order_id"))
            if order is None:
                raise ContractError("成交活动缺少已知策略订单")
            if row.get("symbol") != self.symbols[order.intent.security_id]:
                raise ContractError("成交活动证券身份与订单不符")
            if str(row.get("side", "")).upper() != order.intent.side:
                raise ContractError("成交活动方向与订单不符")
            # Paper 官方不模拟监管费用；若实际活动带费用，必须保留该值；独立非成交
            # 费用活动走上方阻断，不能忽略或用演示费用替代远端事实。
            fee = _number(row.get("commission", "0"))
            quantity, price = _shares(row.get("qty")), _number(row.get("price"))
            if quantity <= 0 or price <= 0 or fee < 0:
                raise ContractError("Alpaca 成交数量、价格或费用无效")
            events.append(
                FillEvent(
                    event_id=row["id"],
                    fill_id=row["id"],
                    source="alpaca-paper",
                    account_id=self.account_id,
                    client_order_id=order.intent.client_order_id,
                    broker_order_id=cast(str, order.broker_order_id),
                    security_id=order.intent.security_id,
                    side=order.intent.side,
                    quantity=quantity,
                    price=price,
                    fee=fee,
                    at=_time(row.get("transaction_time")),
                )
            )
        return events

    def account(self) -> AccountSnapshot:
        """映射美元现金与整股持仓；购买力保留为外部字段，不冒充账本可用现金。"""
        raw = self.verify_account()
        positions: dict[str, int] = {}
        costs: dict[str, Decimal] = {}
        for row in _rows(self.transport.request("GET", "/positions")):
            identity = row.get("asset_id")
            if identity not in self.symbols or row.get("symbol") != self.symbols[identity]:
                raise ContractError("账户持有当前范围之外的证券，需要显式纳入核对")
            if row.get("side") != "long" or identity in positions:
                raise ContractError("账户含空头或重复证券事实")
            positions[identity] = _shares(row.get("qty"))
            costs[identity] = _number(row.get("cost_basis"))
        cash = _number(raw.get("cash"))
        if cash < 0 or any(cost < 0 for cost in costs.values()):
            raise ContractError("当前 Paper 核心不支持借款或负持仓成本")
        return AccountSnapshot(
            account_id=self.account_id,
            as_of=self.clock.now(),
            cash=cash,
            available_cash=cash,
            positions=positions,
            cost_basis=costs,
            fees=sum(
                (event.fee for event in self.events() if isinstance(event, FillEvent)), Decimal(0)
            ),
        )
