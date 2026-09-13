"""独立 SQLite 离线券商：注入限价报价与故障事件，只验证工程而非市场成交质量。"""

# 延迟注解以保持接口类型清晰。
from __future__ import annotations

# 模拟券商自有状态与内部账本完全分开。
import sqlite3

# 短连接由上下文管理器统一释放。
from contextlib import contextmanager

# 真实现金采用十进制金额。
from decimal import Decimal

# 独立数据库路径由应用显式传入。
from pathlib import Path

# 连接上下文与事件联合类型需要迭代器标注。
from typing import Iterator

# 仅依赖公共契约，不导入 ledger 或内部事件存储。
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
        # 存储路径必须由装配层选择独立文件。
        self.path = Path(path).resolve()
        # 时间只来自注入源。
        self.clock = clock
        # 交易时段通过注入日历核验，模拟成交也不能穿越收盘边界。
        self.calendar = calendar
        # 费用与账户来自显式离线配置。
        self.config = config
        # 故障开关是测试驱动，不作为生产接口。
        self.connected = True
        # 只对下一次实际接受的新单注入响应超时。
        self.timeout_after_accept = False
        # 下一次新单可注入确定拒单。
        self.reject_next = False
        # 创建应用指定的券商数据目录。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 未传初态时使用独立演示账户初始现金。
        initial = initial_account or AccountSnapshot(
            account_id=config.account_id,
            as_of=clock.now(),
            cash=config.initial_cash,
            available_cash=config.initial_cash,
        )
        # 模拟配置不能接受其他账户。
        if initial.account_id != config.account_id:
            # 防止调用者认为离线配置授予了其他账号权限。
            raise ContractError("券商初态账号与配置不一致")
        # 独立连接建立仅属于券商的表。
        with self._db() as db:
            # 表名前缀保证误复用内部库时可识别异常，而非共享内部投影。
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
                # 不能把已有券商账户迁移为新账户。
                raise ContractError("券商数据库账号不一致")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """打开券商短事务；退出提交/回滚并关闭，数据库失败原样抛出。"""
        # 每次访问独立券商文件。
        connection = sqlite3.connect(self.path, timeout=5.0)
        # 异常也必须释放数据库文件。
        try:
            # 上下文自动提交或回滚整个模拟动作。
            with connection:
                # 只把自有连接交给券商内部方法。
                yield connection
        # 提交完成后不留下长期连接。
        finally:
            # 及时关闭便于应用对独立库做文件哈希。
            connection.close()

    def _check(self) -> None:
        """验证注入连接状态；断线抛 ConnectionError，不修改交易事实。"""
        # 模拟网络不可用的明确边界。
        if not self.connected:
            # 外部调用失败不代表委托不存在。
            raise ConnectionError("离线券商已注入断线")

    def disconnect(self) -> None:
        """注入断线故障；后续券商访问失败，不改变持久化订单。"""
        # 仅测试连接状态改变。
        self.connected = False

    def reconnect(self) -> None:
        """恢复测试连接；不自动补发订单或修改账户。"""
        # 恢复读取后由执行服务显式对账。
        self.connected = True

    def _account(self, db: sqlite3.Connection) -> AccountSnapshot:
        """读取券商自有账户；状态缺失抛 ContractError，不接触内部账本。"""
        # 独立账户事实只来源于 broker_state。
        row = db.execute("SELECT payload FROM broker_state WHERE key='account'").fetchone()
        # 缺失状态不得从内部 ledger 补造。
        if row is None:
            # 明确阻断不可解释的账户查询。
            raise ContractError("券商账户状态缺失")
        # 输出统一账户契约。
        return AccountSnapshot.model_validate_json(row[0])

    def _order(self, db: sqlite3.Connection, client_id: str) -> OrderRecord | None:
        """查询事务内券商订单；未找到返回 None，无副作用。"""
        # 客户端幂等键可用于受理超时后的查询。
        row = db.execute(
            "SELECT payload FROM broker_orders WHERE client_id=?", (client_id,)
        ).fetchone()
        # 反序列化时重新验证公共字段。
        return None if row is None else OrderRecord.model_validate_json(row[0])

    def _status(self, db: sqlite3.Connection, order: OrderRecord) -> None:
        """原子记录券商订单及递增状态事件；不改变账户现金或持仓。"""
        # 单订单序号与状态同一事务更新。
        row = db.execute(
            "SELECT sequence FROM broker_orders WHERE client_id=?", (order.intent.client_order_id,)
        ).fetchone()
        # 首个受理事件从一开始，其后严格增加。
        sequence = 1 if row is None else int(row[0]) + 1
        # 券商身份在受理时必须已经存在。
        if order.broker_order_id is None:
            # 状态通道不接受无券商 ID 的伪记录。
            raise ContractError("券商订单缺少 broker_order_id")
        # 统一订单记录持久化。
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
        # 断线模拟在产生副作用前失败。
        self._check()
        # 限制离线账户边界。
        if intent.account_id != self.config.account_id:
            # 不接受测试配置未包含的账户。
            raise ContractError("券商意图账号不匹配")
        # 订单存在与受理都在事务内完成。
        with self._db() as db:
            # 查询同一个客户端身份。
            previous = self._order(db, intent.client_order_id)
            # 重复提交不创建新订单或新状态事件。
            if previous is not None:
                # 稳定身份不能更换价格或数量。
                if previous.intent != intent:
                    # 明确暴露幂等键误用。
                    raise ContractError("券商同一订单 ID 内容冲突")
                # 返回已有状态，可包含部分成交或终态。
                return previous
            # 拒单故障有明确终态，区别于响应超时。
            status: OrderStatus = "REJECTED" if self.reject_next else "OPEN"
            # 故障开关消费一次。
            self.reject_next = False
            # 券商 ID 与客户端身份有独立命名空间。
            order = OrderRecord(
                intent=intent, status=status, broker_order_id=f"FAKE:{intent.client_order_id}"
            )
            # 在返回之前提交券商受理及状态事件。
            self._status(db, order)
        # 响应丢失发生在事务提交之后，模拟真实不确定性。
        if self.timeout_after_accept:
            # 超时只注入一次，便于恢复查询。
            self.timeout_after_accept = False
            # 内部执行入口必须标 UNKNOWN，而非换键重发。
            raise TimeoutError("券商已受理，但提交响应超时")
        # 正常路径返回受理记录。
        return order

    def query(self, client_order_id: str) -> OrderRecord | None:
        """按客户端 ID 返回券商当前订单；断线抛 ConnectionError，无写入副作用。"""
        # 查询是否可用由注入故障决定。
        self._check()
        # 查询仅打开短连接。
        with self._db() as db:
            # 不存在也不推导为可以重发。
            return self._order(db, client_order_id)

    def cancel(self, client_order_id: str) -> OrderRecord:
        """请求撤销并记录 CANCEL_PENDING；不自动确认撤单，允许随后发生竞态成交。"""
        # 断线时无法确认请求到达。
        self._check()
        # 读取与状态变更共享事务。
        with self._db() as db:
            # 撤单必须指向券商已知委托。
            order = self._order(db, client_order_id)
            # 未知订单不能伪造撤单成功。
            if order is None:
                # 调用者应继续恢复核对。
                raise ContractError("券商找不到待撤订单")
            # 终态和已经请求中的撤单保持幂等。
            if order.status in {"FILLED", "CANCELED", "REJECTED", "CANCEL_PENDING"}:
                # 不生成重复状态事件。
                return order
            # 撤单请求只是中间状态。
            pending = order.model_copy(update={"status": "CANCEL_PENDING"})
            # 记录请求到达状态，后续 fill 仍然合法。
            self._status(db, pending)
            # 返回中间态，执行者不能据此立即替换。
            return pending

    def complete_cancel(self, client_order_id: str) -> OrderRecord:
        """测试驱动确认撤单；完全成交优先，未请求撤单时抛 ContractError。"""
        # 只有连接恢复后才有确认事件。
        self._check()
        # 撤单确认与订单终态共享事务。
        with self._db() as db:
            # 读取当前可能已被成交改变的状态。
            order = self._order(db, client_order_id)
            # 不允许测试直接跳过撤单请求阶段。
            if order is None or order.status not in {"CANCEL_PENDING", "FILLED", "CANCELED"}:
                # 强制测试真实覆盖请求与确认的分离。
                raise ContractError("订单没有待确认撤单")
            # 成交完成或已经撤销的确认是幂等操作。
            if order.status in {"FILLED", "CANCELED"}:
                # 完全成交不能被迟到撤单确认改为 CANCELED。
                return order
            # 确认仅撤销尚未成交的剩余数量。
            canceled = order.model_copy(update={"status": "CANCELED"})
            # 状态通道记录最终确认。
            self._status(db, canceled)
            # 返回确认后的订单。
            return canceled

    def _book(self, db: sqlite3.Connection, event: FillEvent) -> None:
        """独立更新券商现金/持仓/费用；超卖或资金不足抛 ContractError，事务回滚。"""
        # 直接读取券商事实，不调用内部 ledger。
        before = self._account(db)
        # 外部人工交易也不能跨账户。
        if event.account_id != before.account_id:
            # 不能为了制造对账一致混入其他账户。
            raise ContractError("券商成交账户不匹配")
        # 新字典避免修改当前契约对象。
        holdings = dict(before.positions)
        # 券商成本独立维护，买入费用计入取得成本。
        bases = dict(before.cost_basis)
        # 单独计算当前证券数量。
        held = holdings.get(event.security_id, 0)
        # 成交现金价值为整股乘实际原始价。
        value = Decimal(event.quantity) * event.price
        # 买入增加数量并扣除金额与费用。
        if event.side == "BUY":
            # 现金交易不允许透支。
            money = before.cash - value - event.fee
            # 资金不足时事务不能接受成交事实。
            if money < 0:
                # 不提供隐含融资。
                raise ContractError("券商模拟现金不足")
            # 独立维护成交后的整股仓位。
            holdings[event.security_id] = held + event.quantity
            # 整个持仓成本包含取得费用。
            bases[event.security_id] = (
                bases.get(event.security_id, Decimal("0")) + value + event.fee
            )
        # 卖出仅限已有仓位。
        else:
            # 禁止券商模拟空头。
            if event.quantity > held:
                # 不用内部目标持仓替代已成交持仓。
                raise ContractError("券商模拟持仓不足")
            # 费用减少卖出净收入。
            money = before.cash + value - event.fee
            # 全部卖出时清理规范化数量与成本。
            if event.quantity == held:
                # 清除已平仓的数量键。
                holdings.pop(event.security_id, None)
                # 平仓后不再保留旧总成本。
                bases.pop(event.security_id, None)
            # 部分卖出保留移动平均法的剩余成本。
            else:
                # 用剩余整股数量表示真实仓位。
                holdings[event.security_id] = held - event.quantity
                # 按相同比例释放取得成本，不把卖出费用增加到剩余成本。
                bases[event.security_id] = (
                    bases.get(event.security_id, Decimal("0")) * (held - event.quantity) / held
                )
        # 券商自有事实投影写回独立表。
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
        # 券商侧事件与成交身份也具有唯一约束。
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

        quantity 是本次成交股数，None 表示全部剩余；最低费用每订单只累计收取一次。
        """
        # 成交驱动仍服从已连接边界。
        self._check()
        # 全部交易事实原子保存。
        with self._db() as db:
            # 找到被测试的既有订单。
            order = self._order(db, client_order_id)
            # 不替未知订单创建真实成交。
            if order is None or order.broker_order_id is None:
                # 人工成交必须走明确的独立测试入口。
                raise ContractError("券商找不到成交订单")
            # 指定成交身份重投递时返回既有同事实事件。
            if fill_id is not None:
                # 先查独立成交键，允许完全成交后的回执重试。
                duplicate = db.execute(
                    "SELECT payload FROM broker_events WHERE fill_id=?", (fill_id,)
                ).fetchone()
                # 已存在成交不能再次增加持仓。
                if duplicate is not None:
                    # 恢复原始完整事件。
                    previous = FillEvent.model_validate_json(duplicate[0])
                    # 同成交键的核心交易事实必须一致。
                    if (
                        previous.client_order_id != client_order_id
                        or previous.price != quote.price
                        or previous.security_id != quote.security_id
                        or (quantity is not None and previous.quantity != quantity)
                        or (event_id is not None and previous.event_id != event_id)
                    ):
                        # 不能把重复身份误用为另一笔成交。
                        raise ContractError("券商成交 ID 内容冲突")
                    # 返回旧事件而非生成新账务。
                    return previous
            # 仅活动订单和撤单竞态可以成交。
            if order.status not in {"OPEN", "PARTIAL", "CANCEL_PENDING"}:
                # 已撤剩余量不能再主动生成新成交。
                raise ContractError("订单当前状态不可成交")
            # 计算注入报价相对业务时钟的年龄。
            age = (self.clock.now() - quote.at).total_seconds()
            # 注入报价和处理时刻都必须处于明确市场常规时段。
            if not self.calendar.is_open(quote.at) or not self.calendar.is_open(self.clock.now()):
                # 不把盘后价格当成常规限价成交。
                raise ContractError("报价或处理时间不在交易时段")
            # 不使用未来、过期、停牌或错误证券报价。
            if (
                quote.security_id != order.intent.security_id
                or not quote.tradable
                or age < 0
                or age > self.config.quote_max_age_seconds
                or quote.at < order.intent.eligible_at
            ):
                # 明确拒绝缺乏执行依据的价格事件。
                raise ContractError("报价不满足成交时间、身份或可交易要求")
            # 限价订单只接受价格界限内的报价。
            if (order.intent.side == "BUY" and quote.price > order.intent.limit_price) or (
                order.intent.side == "SELL" and quote.price < order.intent.limit_price
            ):
                # 不根据收盘价或不可见盘中路径猜测成交。
                raise ContractError("报价超过订单限价")
            # 默认成交所有剩余数量。
            amount = order.intent.quantity - order.filled_quantity if quantity is None else quantity
            # 严格拒绝零股、负股和超过剩余的成交。
            if amount <= 0 or amount > order.intent.quantity - order.filled_quantity:
                # 不自动截断错误成交数量。
                raise ContractError("成交数量超过剩余量或非正")
            # 读取该订单已经发生的成交事件以计算已收费用。
            rows = db.execute(
                "SELECT payload FROM broker_events WHERE fill_id IS NOT NULL"
            ).fetchall()
            # 只累计这个订单之前真实费用，不按分笔重复收最低费。
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
            # 原始事件保留实际注入报价时刻。
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
            # 完成数量以真实新增成交累计。
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
            # 返回事件供应用选择即时交付或故障延迟交付。
            return event

    def manual_fill(self, event: FillEvent) -> None:
        """注入真实样式的外部人工成交事实；无内部意图，重复幂等，身份冲突抛错。"""
        # 测试驱动也服从连接状态。
        self._check()
        # 外部成交事实也不能来自尚未发生的未来。
        if event.at > self.clock.now():
            # 人工交易测试入口不能提前制造未来购买力或持仓。
            raise ContractError("人工成交发生时刻晚于当前处理时刻")
        # 人工成交只修改券商自有状态，等待执行服务独立发现。
        with self._db() as db:
            # 两种身份都查重，避免制造两次财务影响。
            existing = db.execute(
                "SELECT payload FROM broker_events WHERE event_id=? OR fill_id=?",
                (event.event_id, event.fill_id),
            ).fetchone()
            # 已有相同身份时验证完整内容。
            if existing is not None:
                # 不把不同人工交易覆盖旧事实。
                if FillEvent.model_validate_json(existing[0]) != event:
                    # 测试数据也要遵守稳定身份。
                    raise ContractError("券商人工成交身份冲突")
                # 同一成交重复投递不记账。
                return
            # 独立生成账户事实和持久化事件。
            self._book(db, event)

    def apply_action(self, action: CorporateAction) -> bool:
        """独立应用封印公司行动；拆股保留总成本，股息按固定权益数量，异常回滚。"""
        # 不接收断线期间假装查询到的公司行动。
        self._check()
        # 经济事件与信息到达都不能晚于当前注入处理时刻。
        if (
            action.event_time > self.clock.now()
            or action.available_at > self.clock.now()
            or action.quality != "good"
        ):
            # 未来现金分红不能提前充当购买力。
            raise ContractError("券商公司行动尚未发生、尚不可用或质量不合格")
        # 公司行动输入需要可靠内容指纹。
        verify_record(action)
        # 为相同来源行动建立不可变内容身份。
        digest = canonical_hash(action.model_dump(mode="json"))
        # 券商权益变化独立于内部 ledger。
        with self._db() as db:
            # 核实是否已处理同源行动。
            duplicate = db.execute(
                "SELECT digest FROM broker_actions WHERE source=? AND action_id=?",
                (action.source, action.action_id),
            ).fetchone()
            # 相同动作重复交付保持幂等。
            if duplicate is not None:
                # 相同身份内容变化必须明确报错。
                if duplicate[0] != digest:
                    # 不根据内部账务推断哪一份内容正确。
                    raise ContractError("券商公司行动身份冲突")
                # 原事实已经产生权益变化。
                return False
            # 行动只读取券商自己的持仓现金。
            account = self._account(db)
            # 拆股修改独立数量副本。
            holdings = dict(account.positions)
            # 默认现金不改变。
            cash = account.cash
            # 拆股对实际持仓按比例处理。
            if action.kind == "split":
                # 当前整股数量乘明确比例。
                new_shares = action.ratio * holdings.get(action.security_id, 0)
                # 没有碎股现金事实时不能自动向下取整。
                if new_shares != new_shares.to_integral_value():
                    # 首版不模拟券商碎股处理政策。
                    raise ContractError("券商拆股产生未支持碎股")
                # 无持仓不生成零股记录。
                if new_shares:
                    # 已确认整股后更新数量。
                    holdings[action.security_id] = int(new_shares)
            # 股息不按支付日当前仓位猜测权益。
            else:
                # 增加明确权益数量对应的现金。
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
            # 券商账户变化持久化。
            db.execute(
                "UPDATE broker_state SET payload=? WHERE key='account'",
                (updated.model_dump_json(),),
            )
            # 行动身份与权益变化同一事务提交。
            db.execute(
                "INSERT INTO broker_actions VALUES (?,?,?)",
                (action.source, action.action_id, digest),
            )
            # 首次处理成功。
            return True

    def orders(self) -> list[OrderRecord]:
        """返回独立券商订单稳定快照；包含终态，无写入副作用。"""
        # 断线查询需要调用者明确处理。
        self._check()
        # 不访问内部状态库。
        with self._db() as db:
            # 按稳定订单 ID 排序便于可重复报告。
            return [
                OrderRecord.model_validate_json(row[0])
                for row in db.execute("SELECT payload FROM broker_orders ORDER BY client_id")
            ]

    def events(self) -> list[OrderEvent | FillEvent]:
        """按发生顺序返回可重复消费事件；不清空事实，断线抛 ConnectionError。"""
        # 连接故障与空事件流必须区分。
        self._check()
        # 只读自有事件表。
        with self._db() as db:
            # 明确字段 fill_id 决定公共事件类型。
            return [
                OrderEvent.model_validate_json(row[1])
                if row[0] is None
                else FillEvent.model_validate_json(row[1])
                for row in db.execute("SELECT fill_id,payload FROM broker_events ORDER BY id")
            ]

    def account(self) -> AccountSnapshot:
        """按注入时钟返回独立账户；不会由内部 ledger 反向生成一致结果。"""
        # 断线不能伪造账户查询成功。
        self._check()
        # 查询券商自有持久化事实。
        with self._db() as db:
            # 保留真实现金、费用、数量和成本。
            value = self._account(db)
            # 查询时刻来自统一 Clock，重新校验 UTC。
            return AccountSnapshot.model_validate({**value.model_dump(), "as_of": self.clock.now()})
