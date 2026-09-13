"""SQLite 内部事件库；事务原子投影、双重成交幂等和机器内统一账户锁。"""

# 延迟类型注解避免循环依赖。
from __future__ import annotations

# 文件锁由操作系统跨进程维护。
import fcntl

# 哈希把账号映射为固定且不泄漏名称的文件名。
import hashlib

# JSON解析仅用于恢复统一联合契约的结构化内容。
import json

# 系统接口仅用于安全打开和释放统一账户锁。
import os

# SQLite 负责本地原子提交和唯一约束。
import sqlite3

# 上下文管理器确保锁与数据库连接异常时仍释放。
from contextlib import contextmanager

# 时间只来自接口参数，适配器不生成交易时间。
from datetime import datetime

# 路径适配隔离文件系统细节。
from pathlib import Path

# Iterator 描述上下文管理器的 yield 类型。
from typing import Iterator, Literal

# 存储只接收统一契约并产生显式错误。
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    CorporateAction,
    FillEvent,
    JournalEntry,
    OrderEvent,
    OrderIntent,
    OrderRecord,
    RiskBlocked,
    canonical_hash,
    verify_record,
)

# 内部账务唯一投影实现；券商不会调用此模块。
from quant_core.ledger import apply_action, apply_fill


class SQLiteEventStore:
    """单账户本地事件库；参数为路径和初始账户，重开已有库绝不重置余额。

    同源事件 ID 与同源成交 ID 分别唯一；冲突先持久化冻结再向调用者抛错。
    """

    def __init__(self, path: Path, initial_account: AccountSnapshot) -> None:
        """初始化表并仅首次保存初态；账户不一致抛 ContractError，有建库副作用。"""
        # 路径归一化只用于存储，账户锁路径不依赖运行目录。
        self.path = Path(path).resolve()
        # 记录边界账户，所有写入必须保持一致。
        self.account_id = initial_account.account_id
        # 新库所在目录可由应用自由选择。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 使用短连接避免 WAL 未刷盘导致产物哈希遗漏。
        with self._db() as db:
            # 数据库状态只包含一个当前账户投影和一个不可变初态。
            db.executescript(
                "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS orders (client_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sequence INTEGER NOT NULL DEFAULT -1);"
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, event_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(source,event_id));"
                "CREATE TABLE IF NOT EXISTS fills (source TEXT NOT NULL, fill_id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(source,fill_id));"
                "CREATE TABLE IF NOT EXISTS actions (source TEXT NOT NULL, action_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(source,action_id));"
                "CREATE TABLE IF NOT EXISTS freezes (reason TEXT PRIMARY KEY);"
                "CREATE TABLE IF NOT EXISTS journal (sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL);"
            )
            # 首次初始化采用唯一键，重启不覆盖真实累计账户。
            db.execute(
                "INSERT OR IGNORE INTO state VALUES ('account', ?)",
                (initial_account.model_dump_json(),),
            )
            # 初始账户用于独立事件回放。
            db.execute(
                "INSERT OR IGNORE INTO state VALUES ('initial', ?)",
                (initial_account.model_dump_json(),),
            )
            # 读取已有账户核实是否错误复用别的账号数据库。
            existing = self._read_account(db)
            # 不能跨账户沿用状态库。
            if existing.account_id != self.account_id:
                # 保留旧库并明确拒绝构造。
                raise ContractError("状态库账户与初始账户不一致")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """提供短生命周期事务连接；提交或回滚后关闭，数据库错误原样抛出。"""
        # 默认 rollback journal 不留下待合并 WAL 文件。
        db = sqlite3.connect(self.path, timeout=5.0)
        # 无论业务成功失败都关闭文件描述符。
        try:
            # 连接上下文执行提交或回滚。
            with db:
                # 调用方在同一事务读写事件与账户。
                yield db
        # 异常也必须及时释放连接。
        finally:
            # 关闭后的主库可直接纳入产物哈希。
            db.close()

    @contextmanager
    def account_lock(self) -> Iterator[None]:
        """取得机器内统一账户排他锁；竞争抛RiskBlocked，权限失败停止，退出释放。"""
        # 固定目录不随 cwd、运行目录或数据库文件变化。
        directory = Path("/tmp/quant-core-account-locks")
        # 不按用户或运行目录另开命名空间；权限不足必须停止，禁止降级另建锁。
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # 稳定账号身份构成跨进程锁键。
        digest = hashlib.sha256(self.account_id.encode("utf-8")).hexdigest()
        # 禁止跟随锁文件软链，避免误锁其他路径。
        descriptor = os.open(
            directory / f"{digest}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        # 文件描述符关闭会释放内核锁。
        try:
            # 非阻塞失败明确告诉第二个执行者停止。
            try:
                # 同账号不同数据库也竞争同一锁。
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 锁冲突不是交易拒单，应由调度层处理。
            except BlockingIOError as exc:
                # 避免第二执行者读取半完成的外部状态。
                raise RiskBlocked("该账户已有有效执行者") from exc
            # 只在持有排他锁的范围内允许服务操作。
            yield
        # 任意异常退出都必须释放账号所有权。
        finally:
            # close 自动释放 flock，不删除锁文件以避免 inode 竞态。
            os.close(descriptor)

    def _journal(
        self,
        db: sqlite3.Connection,
        operation: Literal["intent", "order", "event", "action"],
        payload: OrderIntent | OrderRecord | OrderEvent | FillEvent | CorporateAction,
        at: datetime,
    ) -> None:
        """在当前事务追加有序重放操作；时间来自明确输入，序号由数据库分配。"""
        # 操作交错顺序必须与实际事务提交内容同时保存。
        db.execute(
            "INSERT INTO journal(operation,payload,at) VALUES (?,?,?)",
            (operation, payload.model_dump_json(), at.isoformat()),
        )

    def journal(self) -> list[JournalEntry]:
        """返回实际写入顺序的完整操作日志；包括公司行动，重放不按事件时间重排。"""
        # 使用只读短连接，不触发任何投影或补造状态。
        with self._db() as db:
            # 公共联合契约验证每种操作及其输入结构。
            return [
                JournalEntry.model_validate(
                    {
                        "sequence": row[0],
                        "operation": row[1],
                        "payload": json.loads(row[2]),
                        "at": row[3],
                    }
                )
                for row in db.execute(
                    "SELECT sequence,operation,payload,at FROM journal ORDER BY sequence"
                )
            ]

    def freeze(self, reason: str) -> None:
        """追加持久化新增风险冻结原因；相同原因幂等，无自动解冻接口。"""
        # 故障标记与失败投影事务分开，避免回滚时丢失冻结。
        with self._db() as db:
            # 同一故障重复恢复不会增加重复标记。
            db.execute("INSERT OR IGNORE INTO freezes VALUES (?)", (reason,))

    def frozen(self) -> list[str]:
        """返回排序后的永久故障标记；无修改副作用。"""
        # 每次从持久化状态读取，重启也能看见冻结。
        with self._db() as db:
            # 排序使报告与重放结果稳定。
            return [str(row[0]) for row in db.execute("SELECT reason FROM freezes ORDER BY reason")]

    def _read_account(self, db: sqlite3.Connection) -> AccountSnapshot:
        """读取事务内账户快照；损坏或缺失抛 ContractError，不写入数据。"""
        # 账户投影必须和成交写入位于同一事务。
        row = db.execute("SELECT payload FROM state WHERE key='account'").fetchone()
        # 缺少初始状态不能自行猜测资金。
        if row is None:
            # 让恢复流程明确暴露状态库损坏。
            raise ContractError("状态库缺少账户")
        # 读取时再次执行契约校验。
        return AccountSnapshot.model_validate_json(row[0])

    def _read_order(self, db: sqlite3.Connection, client_id: str) -> OrderRecord | None:
        """读取事务内订单；不存在返回 None，不产生副作用。"""
        # 稳定客户端键是意图的唯一身份。
        row = db.execute("SELECT payload FROM orders WHERE client_id=?", (client_id,)).fetchone()
        # 数据存在时重新验证，不信任任意 JSON 字段。
        return None if row is None else OrderRecord.model_validate_json(row[0])

    def save_intent(self, intent: OrderIntent) -> None:
        """在发送前原子保存意图；同键同内容幂等，冲突冻结并抛 ContractError。"""
        # 冲突冻结必须在失败事务之外落盘。
        try:
            # 重新验证边界，防止model_copy绕过不可变契约字段约束。
            intent = OrderIntent.model_validate(intent.model_dump())
            # 先保存意图，再由执行服务调用 Broker。
            with self._db() as db:
                # 拒绝把别的账号意图写入当前账户库。
                if intent.account_id != self.account_id:
                    # 账户身份不能由运行目录推断。
                    raise ContractError("意图账户不匹配")
                # 查找同一幂等键的旧意图。
                previous = self._read_order(db, intent.client_order_id)
                # 稳定身份不允许绑定不同业务内容。
                if previous is not None and previous.intent != intent:
                    # 不用覆盖方式修复身份冲突。
                    raise ContractError("相同订单意图 ID 内容冲突")
                # 只有新意图写入初始状态。
                if previous is None:
                    # 订单初态保留 PERSISTED，尚不能声称券商接受。
                    record = OrderRecord(intent=intent)
                    # 唯一主键提供并发重复提交的最后一道保护。
                    db.execute(
                        "INSERT INTO orders(client_id,payload) VALUES (?,?)",
                        (intent.client_order_id, record.model_dump_json()),
                    )
                    # 原始意图位置必须在任何发送结果之前进入有序日志。
                    self._journal(db, "intent", intent, intent.created_at)
        # 合约冲突需要跨重启阻止新增风险。
        except ValueError as exc:
            # 冻结理由记录可定位的错误信息。
            self.freeze(str(exc))
            # 原错误继续传播给执行入口，结构错误统一为存储契约错误。
            raise ContractError(str(exc)) from exc

    def record_order(self, record: OrderRecord) -> None:
        """记录券商投影且保留累计成交单调性；未知意图或内容冲突冻结并抛错。"""
        # 与 save_intent 一样，故障标记不随失败事务回滚。
        try:
            # 重新验证状态与数量，不能把非法FILLED零成交投影当作真实终态。
            record = OrderRecord.model_validate(record.model_dump())
            # 订单投影不能修改账户余额。
            with self._db() as db:
                # 意图必须预先落盘。
                current = self._read_order(db, record.intent.client_order_id)
                # 外部订单只能作为人工交易差异处理，不能伪造内部授权。
                if current is None or current.intent != record.intent:
                    # 不自动创建未经执行入口保存的意图。
                    raise ContractError("券商订单没有匹配的持久化意图")
                # 已补齐的券商订单身份不能在后续响应中更换。
                if (
                    current.broker_order_id is not None
                    and record.broker_order_id is not None
                    and current.broker_order_id != record.broker_order_id
                ):
                    # 同一客户意图不能悄悄指向第二张外部委托。
                    raise ContractError("券商订单 ID 内容冲突")
                # 订单报告量不得超出原委托量。
                if record.filled_quantity > record.intent.quantity:
                    # 超额成交需要人工解释，不能修改意图总数。
                    raise ContractError("订单累计成交超过委托量")
                # 旧响应不得把已确认成交数量变小。
                filled = max(current.filled_quantity, record.filled_quantity)
                # 优先保留完全成交这一确定终态。
                status = "FILLED" if filled == record.intent.quantity else record.status
                # 迟到开放回执不能让已确认的部分成交变成OPEN/非零数量。
                if 0 < filled < record.intent.quantity and status in {"OPEN", "PERSISTED"}:
                    # 保留已确认实际数量对应的部分成交语义。
                    status = "PARTIAL"
                # 迟到的开放状态不能复活已结束的订单。
                if current.status in {"CANCELED", "REJECTED"} and status in {
                    "OPEN",
                    "PARTIAL",
                    "UNKNOWN",
                    "PERSISTED",
                    "CANCEL_PENDING",
                }:
                    # 撤单后的迟到部分成交仍保留剩余已撤销语义。
                    status = current.status
                # 通过模型重建确认有效状态。
                merged = OrderRecord(
                    intent=current.intent,
                    status=status,
                    broker_order_id=record.broker_order_id or current.broker_order_id,
                    filled_quantity=filled,
                )
                # 没有投影变化时不重复堆积相同恢复操作。
                if merged == current:
                    # 原始日志已足以重建相同状态。
                    return
                # sequence 保留给事件通道管理。
                db.execute(
                    "UPDATE orders SET payload=? WHERE client_id=?",
                    (merged.model_dump_json(), record.intent.client_order_id),
                )
                # 订单快照没有独立响应时刻，日志at使用原意图创建时刻，顺序以sequence为准。
                self._journal(db, "order", record, record.intent.created_at)
        # 投影冲突留下持久故障证据。
        except ValueError as exc:
            # 新增风险冻结不妨碍后续读取和诊断。
            self.freeze(str(exc))
            # 调用者必须处理真实失败，模型错误也带统一契约异常。
            raise ContractError(str(exc)) from exc

    def apply(self, event: OrderEvent | FillEvent) -> bool:
        """原子应用事件与账务；重复返回 False，同 ID 异内容冻结并抛 ContractError。"""
        # 事件封装所有字段参与身份内容检查。
        digest = canonical_hash(event.model_dump(mode="json"))
        # 冻结错误不得与事务一同回滚。
        try:
            # 模型副本可能绕过构造校验，存储边界必须再次核验完整输入。
            event = (
                FillEvent.model_validate(event.model_dump())
                if isinstance(event, FillEvent)
                else OrderEvent.model_validate(event.model_dump())
            )
            # 所有去重键、投影及账户写入共享事务。
            with self._db() as db:
                # 同来源范围内查询事件身份。
                previous = db.execute(
                    "SELECT digest FROM events WHERE source=? AND event_id=?",
                    (event.source, event.event_id),
                ).fetchone()
                # 相同事件身份已经存在时不得重复记账。
                if previous is not None:
                    # 内容冲突不是幂等重投递。
                    if previous[0] != digest:
                        # 保留原事实而非覆盖修订。
                        raise ContractError("相同事件 ID 内容冲突")
                    # 相同内容重复只读返回。
                    return False
                # 成交身份可以在不同投递事件 ID 下重复。
                if isinstance(event, FillEvent):
                    # 第二重身份忽略传输事件 ID，保留成交全部业务内容。
                    fill_digest = canonical_hash(
                        event.model_dump(mode="json", exclude={"event_id"})
                    )
                    # 券商成交 ID 在来源内唯一。
                    duplicate = db.execute(
                        "SELECT digest FROM fills WHERE source=? AND fill_id=?",
                        (event.source, event.fill_id),
                    ).fetchone()
                    # 同成交不同内容必须阻断，不能认为是新成交。
                    if duplicate is not None and duplicate[0] != fill_digest:
                        # 不修改已经确认的真实账户。
                        raise ContractError("相同成交 ID 内容冲突")
                    # 保存不同投递封装以检测后续事件身份冲突。
                    db.execute(
                        "INSERT INTO events(source,event_id,digest,payload) VALUES (?,?,?,?)",
                        (event.source, event.event_id, digest, event.model_dump_json()),
                    )
                    # 成交事实已经投影时不再次改变现金。
                    if duplicate is not None:
                        # 传输别名也进入操作日志，重放时保留事件ID冲突证据但不再次记账。
                        self._journal(db, "event", event, event.at)
                        # 该事件已被留痕，但没有新经济事实。
                        return False
                    # 当前订单可以为空，代表外部人工成交。
                    order = self._read_order(db, event.client_order_id)
                    # 已知订单必须与成交方向、证券和券商身份一致。
                    if order is not None and (
                        order.intent.security_id != event.security_id
                        or order.intent.side != event.side
                        or (
                            order.broker_order_id is not None
                            and order.broker_order_id != event.broker_order_id
                        )
                    ):
                        # 真实成交不得借用其他订单身份。
                        raise ContractError("成交与订单身份不匹配")
                    # 去重之后才调用唯一内部账务函数。
                    account = apply_fill(self._read_account(db), event)
                    # 账户和成交去重键一并提交。
                    db.execute(
                        "UPDATE state SET payload=? WHERE key='account'",
                        (account.model_dump_json(),),
                    )
                    # 保存不可覆盖的独立成交身份。
                    db.execute(
                        "INSERT INTO fills VALUES (?,?,?)",
                        (event.source, event.fill_id, fill_digest),
                    )
                    # 外部人工交易入账但必须冻结新增等待解释。
                    if order is None:
                        # 这是交易事实导入，不是补造策略意图。
                        db.execute(
                            "INSERT OR IGNORE INTO freezes VALUES (?)",
                            (f"MANUAL_TRADE:{event.client_order_id}",),
                        )
                # 订单状态事件不改变账户。
                else:
                    # 订单事件也保留完整原始内容。
                    db.execute(
                        "INSERT INTO events(source,event_id,digest,payload) VALUES (?,?,?,?)",
                        (event.source, event.event_id, digest, event.model_dump_json()),
                    )
                    # 未知订单事件保持差异，不创建内部意图。
                    order = self._read_order(db, event.client_order_id)
                    # 外部订单需要独立识别。
                    if order is None:
                        # 冻结标记和未知事件一并持久化。
                        db.execute(
                            "INSERT OR IGNORE INTO freezes VALUES (?)",
                            (f"UNKNOWN_ORDER:{event.client_order_id}",),
                        )
                        # 未知外部事件仍保留在可重放日志中。
                        self._journal(db, "event", event, event.at)
                        # 已记录事实，消费成功。
                        return True
                    # 首次响应可以补齐外部身份，后续事件不得改变绑定。
                    if (
                        order.broker_order_id is not None
                        and order.broker_order_id != event.broker_order_id
                    ):
                        # 不接受借用客户意图绑定另一张券商订单。
                        raise ContractError("券商订单 ID 内容冲突")
                    # 读取最近已应用的订单事件序号。
                    row = db.execute(
                        "SELECT sequence FROM orders WHERE client_id=?", (event.client_order_id,)
                    ).fetchone()
                    # 同序号比较原始事件，而不是可能已被独立订单快照推进的当前投影。
                    same_sequence = db.execute(
                        "SELECT payload FROM events WHERE source=? AND event_id<>? AND json_extract(payload,'$.kind')='order' AND json_extract(payload,'$.client_order_id')=? AND json_extract(payload,'$.sequence')=?",
                        (event.source, event.event_id, event.client_order_id, event.sequence),
                    ).fetchone()
                    # 不同传输身份不能让同一券商序号拥有矛盾业务内容。
                    if same_sequence is not None:
                        # 原始状态事实保留自己的发生时间、数量和外部身份。
                        historical = OrderEvent.model_validate_json(same_sequence[0])
                        # 只忽略传输事件ID，其他内容必须完全一致。
                        if historical.model_dump(exclude={"event_id"}) != event.model_dump(
                            exclude={"event_id"}
                        ):
                            # 这不是迟到或乱序，而是来源自身矛盾。
                            raise ContractError("同一订单事件序号内容冲突")
                    # 旧状态事件可以入库，但不得倒退当前状态。
                    if row is not None and event.sequence <= row[0]:
                        # 迟到事件仍保留真实接收顺序，重放不假装顺序到达。
                        self._journal(db, "event", event, event.at)
                        # 序号较旧不影响成交独立记账通道。
                        return True
                    # 拒绝超额的状态报告。
                    if event.filled_quantity > order.intent.quantity:
                        # 不能为了状态一致扩大委托数量。
                        raise ContractError("订单事件累计成交超过委托量")
                    # FILLED必须确实达到原意图的全部数量。
                    if event.status == "FILLED" and event.filled_quantity != order.intent.quantity:
                        # 不让FILLED/零成交的伪终态通过账户零变动对账。
                        raise ContractError("FILLED状态必须达到全部委托数量")
                    # PARTIAL明确代表至少一股且仍有剩余。
                    if (
                        event.status == "PARTIAL"
                        and not 0 < event.filled_quantity < order.intent.quantity
                    ):
                        # 完全成交或零成交不能标记为部分成交。
                        raise ContractError("PARTIAL状态与累计数量不一致")
                    # 累计确认量不得随事件降低。
                    filled = max(order.filled_quantity, event.filled_quantity)
                    # 完全成交优先于任何撤单中间态。
                    status = "FILLED" if filled == order.intent.quantity else event.status
                    # 独立查询可能已确认部分成交，较旧开放事件不能抹掉数量。
                    if 0 < filled < order.intent.quantity and status in {"OPEN", "PERSISTED"}:
                        # 在状态事件与查询回执乱序时保留部分成交语义。
                        status = "PARTIAL"
                    # 已结束订单不能被迟到开放事件重新打开。
                    if order.status in {"CANCELED", "REJECTED"} and status in {
                        "OPEN",
                        "PARTIAL",
                        "CANCEL_PENDING",
                        "UNKNOWN",
                        "PERSISTED",
                    }:
                        # 已撤销剩余委托的事实保持有效。
                        status = order.status
                    # 创建经过契约校验的订单投影。
                    updated = OrderRecord(
                        intent=order.intent,
                        status=status,
                        broker_order_id=event.broker_order_id,
                        filled_quantity=filled,
                    )
                    # 状态和序号原子更新。
                    db.execute(
                        "UPDATE orders SET payload=?, sequence=? WHERE client_id=?",
                        (updated.model_dump_json(), event.sequence, event.client_order_id),
                    )
                # 原始事件接收顺序必须与财务变化及状态投影同一事务记录。
                self._journal(db, "event", event, event.at)
                # 首次事件处理成功，是否成交由具体类型决定。
                return True
        # 内容或账务冲突必须可靠地冻结账户。
        except ValueError as exc:
            # 单独事务确保冻结不会随前面错误回滚。
            self.freeze(str(exc))
            # 错误必须到达执行服务与测试调用者，不能静默更正无效状态。
            raise ContractError(str(exc)) from exc

    def apply_action(self, action: CorporateAction, at: datetime) -> bool:
        """在显式UTC时刻at原子应用已发生且可用的封印行动；冲突冻结并抛ContractError。"""
        # 公司行动也必须验证来源内容指纹。
        try:
            # 不能用未验证动作悄悄调整差异。
            verify_record(action)
            # 身份与全部内容绑定。
            digest = canonical_hash(action.model_dump(mode="json"))
            # 行动去重和账务共享事务。
            with self._db() as db:
                # 同源行动身份唯一。
                previous = db.execute(
                    "SELECT digest FROM actions WHERE source=? AND action_id=?",
                    (action.source, action.action_id),
                ).fetchone()
                # 同动作重复不再次改变权益。
                if previous is not None:
                    # 内容变动必须建新事实并审查，禁止覆盖旧动作。
                    if previous[0] != digest:
                        # 冲突会在外部持久冻结。
                        raise ContractError("相同公司行动 ID 内容冲突")
                    # 完全相同的投递幂等返回。
                    return False
                # 账务函数保持拆股总成本和固定分红权益语义。
                account = apply_action(self._read_account(db), action, at)
                # 保存行动原文以支持重放。
                db.execute(
                    "INSERT INTO actions VALUES (?,?,?,?)",
                    (action.source, action.action_id, digest, action.model_dump_json()),
                )
                # 公司行动和新账户投影一并提交。
                db.execute(
                    "UPDATE state SET payload=? WHERE key='account'", (account.model_dump_json(),)
                )
                # 公司行动与未来新意图的交错必须出现在统一日志中。
                self._journal(db, "action", action, at)
                # 首次成功处理返回 True。
                return True
        # 验证或账务失败均必须停止正常新增风险。
        except ValueError as exc:
            # 保存准确的失败理由。
            self.freeze(str(exc))
            # 不在存储层静默纠正输入。
            raise ContractError(str(exc)) from exc

    def orders(self) -> list[OrderRecord]:
        """按客户端身份返回内部订单；只读且结果稳定。"""
        # 短连接不长期持有数据库锁。
        with self._db() as db:
            # 每行重新执行契约验证。
            return [
                OrderRecord.model_validate_json(row[0])
                for row in db.execute("SELECT payload FROM orders ORDER BY client_id")
            ]

    def events(self) -> list[OrderEvent | FillEvent]:
        """返回按接收顺序保存的原始事件；重复传输封装也保留，无写入副作用。"""
        # 只读事件不会触发账户重放。
        with self._db() as db:
            # 先保留 SQL 插入顺序，避免客户端排序掩盖乱序场景。
            rows = db.execute("SELECT payload FROM events ORDER BY id").fetchall()
            # 逐条根据明确的 kind 字段分派模型。
            return [
                FillEvent.model_validate_json(row[0])
                if '"kind":"fill"' in row[0]
                else OrderEvent.model_validate_json(row[0])
                for row in rows
            ]

    def account(self, at: datetime) -> AccountSnapshot:
        """返回指定查询时刻的当前账户投影；at 是 UTC 查询时间而非历史查询条件。"""
        # 时间由调用者的 Clock 注入。
        with self._db() as db:
            # 先读取当前实际账户再替换查询时刻。
            account = self._read_account(db)
            # 完整重建确保时间仍经过 UTC 契约验证。
            return AccountSnapshot.model_validate({**account.model_dump(), "as_of": at})
