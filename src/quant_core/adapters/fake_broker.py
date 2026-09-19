"""独立 SQLite 离线券商：注入限价报价与故障事件，只验证工程而非市场成交质量。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from quant_core.contracts import (
    AccountSnapshot,
    Clock,
    ContractError,
    CorporateAction,
    DemoConfig,
    FillEvent,
    OrderEvent,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    Quote,
    TradingCalendar,
    canonical_hash,
    verify_record,
)


class FakeBroker:
    """注入时钟和配置的离线券商；构造参数为独立路径、Clock、DemoConfig、可选初态。

    交易事实直接维护于券商自有表；不会读取内部 ledger，也不模拟生产市场队列。
    clock 提供处理/事件时间，calendar 限定常规交易时段，config 提供账户和费用规则。
    submit 只创建订单，fill 由演示或测试显式注入成交；执行服务再消费 events 记入内账。
    connected 等故障开关只存在于实例内存，重开实例会重置；订单、事件和账户留在数据库。
    """

    def __init__(
        self,
        path: Path,
        clock: Clock,
        config: DemoConfig,
        initial_account: AccountSnapshot | None = None,
        *,
        calendar: TradingCalendar,
    ) -> None:
        """首次创建券商状态，重开保留账户和订单；配置账号冲突抛 ContractError。"""
        self.path = Path(path).resolve()
        self.clock = clock
        self.calendar = calendar
        self.config = config
        # 故障开关是测试驱动，不作为生产接口。
        self.connected = True
        # 只对下一次实际接受的新单注入响应超时。
        self.timeout_after_accept = False
        # 下一次新单可注入确定拒单。
        self.reject_next = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 未传初态时使用独立演示账户初始现金。
        initial = initial_account or AccountSnapshot(
            account_id=config.account_id,
            as_of=clock.now(),
            cash=config.initial_cash,
            available_cash=config.initial_cash,
        )
        if initial.account_id != config.account_id:
            raise ContractError("券商初态账号与配置不一致")
        with self._db() as db:
            # 券商表与内部投影使用不同名称；同库路径冲突由 ExecutionService 装配检查。
            db.executescript(
                "CREATE TABLE IF NOT EXISTS broker_state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS broker_orders (client_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sequence INTEGER NOT NULL);"
                "CREATE TABLE IF NOT EXISTS broker_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, fill_id TEXT UNIQUE, payload TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS broker_actions (source TEXT NOT NULL, action_id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(source,action_id));"
            )
            # 新库才写初态，重启绝不重置现金。
            db.execute(
                "INSERT OR IGNORE INTO broker_state VALUES ('account',?)",
                (initial.model_dump_json(),),
            )
            # 核查持久化账号身份。
            if self._account(db).account_id != config.account_id:
                raise ContractError("券商数据库账号不一致")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """为一次券商操作提供连接和事务，操作结束后关闭连接。

        @contextmanager 在 with 进入时执行到 yield，将连接交给 submit/fill 等正文；
        正常退出（含 return）提交当前事务，异常退出回滚，再由 finally 关闭连接。
        内部事件库是另一个数据库，不属于此事务，也不会被一起提交或回滚。
        """
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _check(self) -> None:
        """验证注入连接状态；断线抛 ConnectionError，不修改交易事实。"""
        if not self.connected:
            raise ConnectionError("离线券商已注入断线")

    def disconnect(self) -> None:
        """注入断线故障；后续券商访问失败，不改变持久化订单。"""
        self.connected = False

    def reconnect(self) -> None:
        """恢复测试连接；不自动补发订单或修改账户。"""
        self.connected = True

    def _account(self, db: sqlite3.Connection) -> AccountSnapshot:
        """读取券商自有账户；状态缺失抛 ContractError，不接触内部账本。"""
        row = db.execute("SELECT payload FROM broker_state WHERE key='account'").fetchone()
        if row is None:
            raise ContractError("券商账户状态缺失")
        return AccountSnapshot.model_validate_json(row[0])

    def _order(self, db: sqlite3.Connection, client_id: str) -> OrderRecord | None:
        """在当前券商事务内按客户端订单 ID 查询记录；不存在返回 None。"""
        row = db.execute(
            "SELECT payload FROM broker_orders WHERE client_id=?", (client_id,)
        ).fetchone()
        return None if row is None else OrderRecord.model_validate_json(row[0])

    def _status(self, db: sqlite3.Connection, order: OrderRecord) -> None:
        """在调用方 db 事务内保存当前 OrderRecord，并追加对应 OrderEvent。

        order 是订单状态快照，OrderEvent 是供执行服务反复读取的状态消息；都携带
        累计成交股数，但都不代表一笔新增成交。此方法不改账户，也不自行提交事务。
        """
        # 单订单序号与状态同一事务更新。
        row = db.execute(
            "SELECT sequence FROM broker_orders WHERE client_id=?", (order.intent.client_order_id,)
        ).fetchone()
        # 首个受理事件从一开始，其后严格增加。
        sequence = 1 if row is None else int(row[0]) + 1
        if order.broker_order_id is None:
            raise ContractError("券商订单缺少 broker_order_id")
        db.execute(
            "INSERT INTO broker_orders VALUES (?,?,?) ON CONFLICT(client_id) DO UPDATE SET payload=excluded.payload, sequence=excluded.sequence",
            (order.intent.client_order_id, order.model_dump_json(), sequence),
        )
        # 事件 ID 由订单身份与单调序号确定，重放稳定。
        event = OrderEvent(
            event_id=f"{order.broker_order_id}:state:{sequence}",
            client_order_id=order.intent.client_order_id,
            broker_order_id=order.broker_order_id,
            status=order.status,
            filled_quantity=order.filled_quantity,
            at=self.clock.now(),
            sequence=sequence,
        )
        # 状态事件没有 fill_id，不触发财务去重。
        db.execute(
            "INSERT INTO broker_events(event_id,payload) VALUES (?,?)",
            (event.event_id, event.model_dump_json()),
        )

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """幂等接受离线限价单；可注入受理后 TimeoutError，超时不回滚券商受理。"""
        self._check()
        if intent.account_id != self.config.account_id:
            raise ContractError("券商意图账号不匹配")
        # 订单存在与受理都在事务内完成。
        with self._db() as db:
            previous = self._order(db, intent.client_order_id)
            # 重复提交不创建新订单或新状态事件。
            if previous is not None:
                if previous.intent != intent:
                    raise ContractError("券商同一订单 ID 内容冲突")
                return previous
            # 拒单故障有明确终态，区别于响应超时。
            status: OrderStatus = "REJECTED" if self.reject_next else "OPEN"
            self.reject_next = False
            order = OrderRecord(
                intent=intent, status=status, broker_order_id=f"FAKE:{intent.client_order_id}"
            )
            self._status(db, order)
            # 到这里仅有订单和状态事件，现金/持仓未改变；离开 with 后这些事实已落库。
        # 响应丢失发生在事务提交之后，模拟真实不确定性。
        if self.timeout_after_accept:
            self.timeout_after_accept = False
            raise TimeoutError("券商已受理，但提交响应超时")
        return order

    def query(self, client_order_id: str) -> OrderRecord | None:
        """查询客户端 ID 对应的券商当前订单；不存在返回 None，断线抛 ConnectionError。"""
        self._check()
        with self._db() as db:
            return self._order(db, client_order_id)

    def cancel(self, client_order_id: str) -> OrderRecord:
        """请求撤销并记录 CANCEL_PENDING；不自动确认撤单，允许随后发生竞态成交。"""
        self._check()
        with self._db() as db:
            order = self._order(db, client_order_id)
            if order is None:
                raise ContractError("券商找不到待撤订单")
            # 终态和已经请求中的撤单保持幂等。
            if order.status in {"FILLED", "CANCELED", "REJECTED", "CANCEL_PENDING"}:
                return order
            # model_copy 不重新验证字段；这里只基于已读出的合法订单改状态，保留身份和
            # 累计成交股数。持久化后仍须 complete_cancel 确认，期间 fill 可以继续成交。
            pending = order.model_copy(update={"status": "CANCEL_PENDING"})
            self._status(db, pending)
            return pending

    def complete_cancel(self, client_order_id: str) -> OrderRecord:
        """测试驱动确认撤单；完全成交优先，未请求撤单时抛 ContractError。"""
        self._check()
        with self._db() as db:
            order = self._order(db, client_order_id)
            if order is None or order.status not in {"CANCEL_PENDING", "FILLED", "CANCELED"}:
                raise ContractError("订单没有待确认撤单")
            # 成交完成或已经撤销的确认是幂等操作。
            if order.status in {"FILLED", "CANCELED"}:
                # 完全成交不能被迟到撤单确认改为 CANCELED。
                return order
            # 确认仅撤销尚未成交的剩余数量。
            canceled = order.model_copy(update={"status": "CANCELED"})
            self._status(db, canceled)
            return canceled

    def _book(self, db: sqlite3.Connection, event: FillEvent) -> None:
        """在调用方提供的券商事务中登记成交，独立更新美元现金、持仓和总成本。

        买入费用计入取得成本；部分卖出按原持仓比例释放成本，卖出费用只扣现金。
        可用现金同步本次现金差额，仅模拟即时结算。账户不符、超卖或买入现金不足抛
        ContractError；返回快照构造时的校验错误仍传播，例如卖出费用使现金为负。
        事务回滚由调用方的 _db 上下文负责。
        """
        # 直接读取券商事实，不调用内部 ledger。
        before = self._account(db)
        if event.account_id != before.account_id:
            raise ContractError("券商成交账户不匹配")
        # holdings 与 bases 都按稳定证券 ID 索引：前者是已成交股数，后者是整笔持仓
        # 的美元取得成本。复制后只改本次成交证券，避免改动读取到的 before 快照。
        holdings = dict(before.positions)
        # 券商成本独立维护，买入费用计入取得成本。
        bases = dict(before.cost_basis)
        held = holdings.get(event.security_id, 0)
        value = Decimal(event.quantity) * event.price
        # 买入增加数量并扣除金额与费用。
        if event.side == "BUY":
            money = before.cash - value - event.fee
            if money < 0:
                raise ContractError("券商模拟现金不足")
            holdings[event.security_id] = held + event.quantity
            bases[event.security_id] = (
                bases.get(event.security_id, Decimal("0")) + value + event.fee
            )
        # 卖出仅限已有仓位。
        else:
            if event.quantity > held:
                raise ContractError("券商模拟持仓不足")
            money = before.cash + value - event.fee
            # 全部卖出时清理规范化数量与成本。
            if event.quantity == held:
                holdings.pop(event.security_id, None)
                bases.pop(event.security_id, None)
            else:
                holdings[event.security_id] = held - event.quantity
                # 按相同比例释放取得成本，不把卖出费用增加到剩余成本。
                bases[event.security_id] = (
                    bases.get(event.security_id, Decimal("0")) * (held - event.quantity) / held
                )
        # after 重新构造并校验账户；现金变化同步到可用现金，保留原有两者差额。
        # _book 随后把账户和本次 FillEvent 一起写入券商事务，不直接写内部事件库。
        after = AccountSnapshot(
            account_id=before.account_id,
            as_of=event.at,
            cash=money,
            available_cash=before.available_cash + money - before.cash,
            positions=holdings,
            fees=before.fees + event.fee,
            cost_basis=bases,
        )
        # 成交事件与余额同一事务生效。
        db.execute(
            "UPDATE broker_state SET payload=? WHERE key='account'", (after.model_dump_json(),)
        )
        db.execute(
            "INSERT INTO broker_events(event_id,fill_id,payload) VALUES (?,?,?)",
            (event.event_id, event.fill_id, event.model_dump_json()),
        )

    def fill(
        self,
        client_order_id: str,
        quote: Quote,
        quantity: int | None = None,
        *,
        fill_id: str | None = None,
        event_id: str | None = None,
    ) -> FillEvent:
        """注入可执行原始报价成交；返回新增 FillEvent，越限/过期/超量抛 ContractError。

        quantity 是本次成交股数，None 表示全部剩余；报价必须是该证券的原始执行价，
        处于订单可执行时段、当前交易时段且未过期。最低费用每订单累计收取一次。
        成交、账户和订单状态同事务落库；CANCEL_PENDING 仍可成交，以覆盖撤单竞态。
        相同 fill_id 的重投递先核对既有事实，可在订单已完全成交后返回原事件。
        返回事件同时留在 broker.events()；调用方须让执行服务 recover 才会更新内部
        账户。本方法不替执行服务发单，也不自动把成交送入内部 SQLiteEventStore。
        """
        self._check()
        with self._db() as db:
            order = self._order(db, client_order_id)
            if order is None or order.broker_order_id is None:
                raise ContractError("券商找不到成交订单")
            # 指定成交身份重投递时返回既有同事实事件。
            if fill_id is not None:
                # 先查独立成交键，允许完全成交后的回执重试。
                duplicate = db.execute(
                    "SELECT payload FROM broker_events WHERE fill_id=?", (fill_id,)
                ).fetchone()
                if duplicate is not None:
                    previous = FillEvent.model_validate_json(duplicate[0])
                    if (
                        previous.client_order_id != client_order_id
                        or previous.price != quote.price
                        or previous.security_id != quote.security_id
                        or (quantity is not None and previous.quantity != quantity)
                        or (event_id is not None and previous.event_id != event_id)
                    ):
                        raise ContractError("券商成交 ID 内容冲突")
                    return previous
            # 仅活动订单和撤单竞态可以成交。
            if order.status not in {"OPEN", "PARTIAL", "CANCEL_PENDING"}:
                raise ContractError("订单当前状态不可成交")
            age = (self.clock.now() - quote.at).total_seconds()
            # 注入报价和处理时刻都必须处于明确市场常规时段。
            if not self.calendar.is_open(quote.at) or not self.calendar.is_open(self.clock.now()):
                raise ContractError("报价或处理时间不在交易时段")
            # 不使用未来、过期、停牌或错误证券报价。
            if (
                quote.security_id != order.intent.security_id
                or not quote.tradable
                or age < 0
                or age > self.config.quote_max_age_seconds
                or quote.at < order.intent.eligible_at
            ):
                raise ContractError("报价不满足成交时间、身份或可交易要求")
            # 限价订单只接受价格界限内的报价。
            if (order.intent.side == "BUY" and quote.price > order.intent.limit_price) or (
                order.intent.side == "SELL" and quote.price < order.intent.limit_price
            ):
                raise ContractError("报价超过订单限价")
            # amount 是本次新增成交股数；委托总股数减已成交累计股数才是可成交剩余量。
            # quantity=None 选全部剩余，不是重新成交整个原单；零股也明确拒绝。
            amount = order.intent.quantity - order.filled_quantity if quantity is None else quantity
            if amount <= 0 or amount > order.intent.quantity - order.filled_quantity:
                raise ContractError("成交数量超过剩余量或非正")
            rows = db.execute(
                "SELECT payload FROM broker_events WHERE fill_id IS NOT NULL"
            ).fetchall()
            # rows 来自券商已落库的全部成交 JSON，按 client_order_id 筛出本单；paid 是
            # 已收美元费用。与下方累计应收费用作差，避免部分成交每笔重收最低费。
            paid = sum(
                (
                    item.fee
                    for row in rows
                    if (item := FillEvent.model_validate_json(row[0])).client_order_id
                    == client_order_id
                ),
                Decimal("0"),
            )
            # 累计费用取整笔最低费与累计每股费的较大值。
            cumulative_fee = max(
                self.config.minimum_fee,
                self.config.fee_per_share * (order.filled_quantity + amount),
            )
            # 新成交费用仅为新增累计差额。
            fee = max(Decimal("0"), cumulative_fee - paid)
            # 默认成交身份由已成交数量边界确定，便于固定回放。
            identity = fill_id or f"{order.broker_order_id}:fill:{order.filled_quantity + amount}"
            event = FillEvent(
                event_id=event_id or f"event:{identity}",
                fill_id=identity,
                account_id=order.intent.account_id,
                client_order_id=client_order_id,
                broker_order_id=order.broker_order_id,
                security_id=quote.security_id,
                side=order.intent.side,
                quantity=amount,
                price=quote.price,
                fee=fee,
                at=quote.at,
            )
            # 券商账户与成交事件由独立账务实现原子更新。
            self._book(db, event)
            # filled 是本订单累计成交股数，区别于 event.quantity 的本次新增股数；
            # 用累计值决定终态，不能把 PARTIAL 的一次新增量写成订单全部进度。
            filled = order.filled_quantity + amount
            # 完全成交优先，部分成交保留撤单中的权限限制。
            status: OrderStatus = (
                "FILLED"
                if filled == order.intent.quantity
                else ("CANCEL_PENDING" if order.status == "CANCEL_PENDING" else "PARTIAL")
            )
            # 状态回执独立于 FillEvent，但在同一模拟动作内生成。
            self._status(
                db,
                OrderRecord(
                    intent=order.intent,
                    status=status,
                    broker_order_id=order.broker_order_id,
                    filled_quantity=filled,
                ),
            )
            return event

    def manual_fill(self, event: FillEvent) -> None:
        """注入真实样式的外部人工成交事实；无内部意图，重复幂等，身份冲突抛错。"""
        self._check()
        # 外部成交事实也不能来自尚未发生的未来。
        if event.at > self.clock.now():
            raise ContractError("人工成交发生时刻晚于当前处理时刻")
        # 人工成交只修改券商自有状态，等待执行服务独立发现。
        with self._db() as db:
            # 两种身份都查重，避免制造两次财务影响。
            existing = db.execute(
                "SELECT payload FROM broker_events WHERE event_id=? OR fill_id=?",
                (event.event_id, event.fill_id),
            ).fetchone()
            if existing is not None:
                if FillEvent.model_validate_json(existing[0]) != event:
                    raise ContractError("券商人工成交身份冲突")
                return
            self._book(db, event)

    def apply_action(self, action: CorporateAction) -> bool:
        """按注入时钟核验公司行动后，在券商自有账户应用拆股或股息。

        拆股保留总成本，不支持产生碎股；股息按 action 中固定权益股数计算，
        不拿支付日持仓猜测权益。重复返回 False，首次提交账户与行动身份后返回 True；
        时点、质量、身份或碎股校验失败抛 ContractError，事务内失败不留下半次变动。
        """
        self._check()
        # 经济事件与信息到达都不能晚于当前注入处理时刻。
        if (
            action.event_time > self.clock.now()
            or action.available_at > self.clock.now()
            or action.quality != "good"
        ):
            raise ContractError("券商公司行动尚未发生、尚不可用或质量不合格")
        verify_record(action)
        digest = canonical_hash(action.model_dump(mode="json"))
        # 券商权益变化独立于内部 ledger。
        with self._db() as db:
            duplicate = db.execute(
                "SELECT digest FROM broker_actions WHERE source=? AND action_id=?",
                (action.source, action.action_id),
            ).fetchone()
            if duplicate is not None:
                if duplicate[0] != digest:
                    raise ContractError("券商公司行动身份冲突")
                return False
            # 公司行动从券商自己的持仓/现金计算新账户；holdings 的键为证券 ID，值为
            # 当前整股数量。拆股按新旧股比替换该数量，分红改现金且不依赖当天持仓。
            account = self._account(db)
            holdings = dict(account.positions)
            cash = account.cash
            if action.kind == "split":
                new_shares = action.ratio * holdings.get(action.security_id, 0)
                # 没有碎股现金事实时不能自动向下取整。
                if new_shares != new_shares.to_integral_value():
                    raise ContractError("券商拆股产生未支持碎股")
                if new_shares:
                    holdings[action.security_id] = int(new_shares)
            # 股息不按支付日当前仓位猜测权益。
            else:
                cash += action.cash_per_share * action.entitlement_quantity
            # 费用和总成本在两种行动中保持不变。
            updated = AccountSnapshot(
                account_id=account.account_id,
                as_of=action.event_time,
                cash=cash,
                available_cash=account.available_cash + cash - account.cash,
                positions=holdings,
                fees=account.fees,
                cost_basis=dict(account.cost_basis),
            )
            db.execute(
                "UPDATE broker_state SET payload=? WHERE key='account'",
                (updated.model_dump_json(),),
            )
            # 行动身份与权益变化同一事务提交。
            db.execute(
                "INSERT INTO broker_actions VALUES (?,?,?)",
                (action.source, action.action_id, digest),
            )
            return True

    def orders(self) -> list[OrderRecord]:
        """读取券商全部订单并按客户端 ID 排序，包含已结束的订单。"""
        self._check()
        with self._db() as db:
            return [
                OrderRecord.model_validate_json(row[0])
                for row in db.execute("SELECT payload FROM broker_orders ORDER BY client_id")
            ]

    def events(self) -> list[OrderEvent | FillEvent]:
        """按券商写入顺序读取可重复消费的事件；不清空事实，断线抛 ConnectionError。"""
        self._check()
        with self._db() as db:
            return [
                OrderEvent.model_validate_json(row[1])
                if row[0] is None
                else FillEvent.model_validate_json(row[1])
                for row in db.execute("SELECT fill_id,payload FROM broker_events ORDER BY id")
            ]

    def account(self) -> AccountSnapshot:
        """按注入时钟返回独立账户；不会由内部 ledger 反向生成一致结果。"""
        self._check()
        with self._db() as db:
            value = self._account(db)
            # 展开当前账户字段、替换查询时刻后重新校验；没有写回库，也没有按该时间查历史。
            return AccountSnapshot.model_validate({**value.model_dump(), "as_of": self.clock.now()})
