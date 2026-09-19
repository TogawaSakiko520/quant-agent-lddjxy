"""把订单意图、券商事件和公司行动写成可恢复的内部交易事实。

订单状态与账户账务分开投影；交易写入同时保存有序 journal（事务操作日志）。
成交既按投递事件身份去重，也按实际成交身份去重；账户锁仅协调同机合作进程。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

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
from quant_core.ledger import apply_action, apply_fill


class SQLiteEventStore:
    """单账户本地事件库；参数为路径和初始账户，重开已有库绝不重置余额。

    同源事件 ID 与同源成交 ID 分别唯一；写入校验冲突会持久化冻结后抛错。
    意图、事件和账户的单次写入在对应数据库事务中提交，不自动取得账户锁；
    跨方法的交易序列由 ExecutionService 持锁编排。构造时的 executescript 建表
    不与随后初态写入组成整体原子事务，构造失败不能推断新库及已建表全部撤销。
    序列化或数据库本身的错误仍可能直接向上传播。
    orders 保存每张意图的当前 OrderRecord；events 保存收到的状态/成交消息，fills
    只保存成交去重身份；state.account 是这些成交及公司行动推导的当前账户。
    因此 OrderRecord 的累计股数只是订单状态，不能替代逐笔 FillEvent 的现金事实。
    """

    def __init__(self, path: Path, initial_account: AccountSnapshot) -> None:
        """初始化表并仅首次保存初态；账户不一致抛 ContractError，有建库副作用。"""
        self.path = Path(path).resolve()
        self.account_id = initial_account.account_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            if existing.account_id != self.account_id:
                raise ContractError("状态库账户与初始账户不一致")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """提供短生命周期事务连接；提交或回滚后关闭，数据库错误原样抛出。"""
        # @contextmanager 让本生成器用于 with self._db() as db：进入时打开连接，
        # yield 将连接交给该次存储操作；调用方正常退出（含 return）后提交，异常则回滚
        # 当前事务，最后关闭连接。此前其他方法已提交的事实不在本次回滚范围内。
        # 新建库默认 rollback journal；这里不主动切换已有数据库的日志模式。
        db = sqlite3.connect(self.path, timeout=5.0)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def account_lock(self) -> Iterator[None]:
        """在当前上下文取得同机、同账户的非阻塞排他文件锁。

        不同数据库和运行目录仍共用锁；它不能协调其他机器或阻止外部人工交易。
        账户哈希仅用于固定锁文件名，不是账户加密。竞争抛 RiskBlocked，权限错误
        原样传播；退出关闭文件描述符释放锁，保留文件以避免删除造成 inode 竞态。
        """
        # 固定目录不随 cwd、运行目录或数据库文件变化。
        directory = Path("/tmp/quant-core-account-locks")
        # 不按用户或运行目录另开命名空间；权限不足必须停止，禁止降级另建锁。
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest = hashlib.sha256(self.account_id.encode("utf-8")).hexdigest()
        # 禁止跟随锁文件软链，避免误锁其他路径。
        descriptor = os.open(
            directory / f"{digest}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            # 非阻塞失败明确告诉第二个执行者停止。
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RiskBlocked("该账户已有有效执行者") from exc
            # 只有成功取得 flock 才进入调用方的 with 正文；锁持续覆盖其内部多个数据库事务。
            yield
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
        """将本次操作和输入模型追加到调用方 db 事务，供 journal 回放重建状态。

        payload.model_dump_json() 将模型序列化为 JSON 文本供数据库保存，不负责重新
        验证业务内容；operation 决定回放时交给哪一种写入方法。sequence 是写入次序，
        at 是输入事件或操作的业务时刻，两者不同，不能按 at 重排日志。
        """
        db.execute(
            "INSERT INTO journal(operation,payload,at) VALUES (?,?,?)",
            (operation, payload.model_dump_json(), at.isoformat()),
        )

    def journal(self) -> list[JournalEntry]:
        """返回实际写入顺序的完整操作日志；包括公司行动，重放不按事件时间重排。"""
        with self._db() as db:
            # SQL 列分别为次序、操作类型、JSON 载荷和业务时刻；先解析载荷文本，再由
            # JournalEntry 校验操作与载荷类型对应。应用据此逐条调用原存储入口重建新库。
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
            db.execute("INSERT OR IGNORE INTO freezes VALUES (?)", (reason,))

    def frozen(self) -> list[str]:
        """读取全部永久冻结原因并排序，供恢复与诊断使用。"""
        with self._db() as db:
            return [str(row[0]) for row in db.execute("SELECT reason FROM freezes ORDER BY reason")]

    def _read_account(self, db: sqlite3.Connection) -> AccountSnapshot:
        """读取事务内账户快照；缺失抛 ContractError，损坏内容由模型抛 ValidationError。"""
        row = db.execute("SELECT payload FROM state WHERE key='account'").fetchone()
        if row is None:
            raise ContractError("状态库缺少账户")
        return AccountSnapshot.model_validate_json(row[0])

    def _read_order(self, db: sqlite3.Connection, client_id: str) -> OrderRecord | None:
        """在当前事务中按客户端订单 ID 查询内部记录；不存在返回 None。"""
        row = db.execute("SELECT payload FROM orders WHERE client_id=?", (client_id,)).fetchone()
        return None if row is None else OrderRecord.model_validate_json(row[0])

    def save_intent(self, intent: OrderIntent) -> None:
        """在发送前保存意图及 PERSISTED 订单初态，供执行服务重试时识别旧请求。

        同键同内容不再写日志，同键异内容冻结并抛 ContractError。方法返回表示本地
        事务已完成，不表示券商已受理；调用 Broker.submit 是执行服务的后续步骤。
        """
        try:
            # model_copy(update=...) 不执行 Pydantic 字段校验；先 model_dump 转成 Python
            # 字段值，再 model_validate 重建并校验，避免把非法副本写成可发送意图。
            intent = OrderIntent.model_validate(intent.model_dump())
            # 先保存意图，再由执行服务调用 Broker。
            with self._db() as db:
                if intent.account_id != self.account_id:
                    raise ContractError("意图账户不匹配")
                previous = self._read_order(db, intent.client_order_id)
                if previous is not None and previous.intent != intent:
                    raise ContractError("相同订单意图 ID 内容冲突")
                if previous is None:
                    # 订单初态保留 PERSISTED，尚不能声称券商接受。
                    record = OrderRecord(intent=intent)
                    db.execute(
                        "INSERT INTO orders(client_id,payload) VALUES (?,?)",
                        (intent.client_order_id, record.model_dump_json()),
                    )
                    # 原始意图位置必须在任何发送结果之前进入有序日志。
                    self._journal(db, "intent", intent, intent.created_at)
        except ValueError as exc:
            self.freeze(str(exc))
            raise ContractError(str(exc)) from exc

    def record_order(self, record: OrderRecord) -> None:
        """记录券商投影且保留累计成交单调性；未知意图或内容冲突冻结并抛错。"""
        try:
            # record 可以来自券商快照，也可以是执行服务构造的本地 REJECTED/UNKNOWN。
            # 先重建模型检查状态与累计股数，再与库中 current 合并；这里不按累计量补造现金。
            record = OrderRecord.model_validate(record.model_dump())
            # 订单投影不能修改账户余额。
            with self._db() as db:
                current = self._read_order(db, record.intent.client_order_id)
                # 外部订单只能作为人工交易差异处理，不能伪造内部授权。
                if current is None or current.intent != record.intent:
                    raise ContractError("券商订单没有匹配的持久化意图")
                # 已补齐的券商订单身份不能在后续响应中更换。
                if (
                    current.broker_order_id is not None
                    and record.broker_order_id is not None
                    and current.broker_order_id != record.broker_order_id
                ):
                    raise ContractError("券商订单 ID 内容冲突")
                if record.filled_quantity > record.intent.quantity:
                    raise ContractError("订单累计成交超过委托量")
                # 旧响应不得把已确认成交数量变小。
                filled = max(current.filled_quantity, record.filled_quantity)
                status = "FILLED" if filled == record.intent.quantity else record.status
                # 迟到开放回执不能让已确认的部分成交变成OPEN/非零数量。
                if 0 < filled < record.intent.quantity and status in {"OPEN", "PERSISTED"}:
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
                # merged 保留原意图、已确认券商身份和不回退的累计股数；构造模型会再次
                # 校验合并后的状态/数量是否自洽。它仍不是一笔新增成交。
                merged = OrderRecord(
                    intent=current.intent,
                    status=status,
                    broker_order_id=record.broker_order_id or current.broker_order_id,
                    filled_quantity=filled,
                )
                # 没有投影变化时不重复堆积相同恢复操作。
                if merged == current:
                    return
                db.execute(
                    "UPDATE orders SET payload=? WHERE client_id=?",
                    (merged.model_dump_json(), record.intent.client_order_id),
                )
                # 订单快照没有独立响应时刻，日志at使用原意图创建时刻，顺序以sequence为准。
                self._journal(db, "order", record, record.intent.created_at)
        except ValueError as exc:
            self.freeze(str(exc))
            raise ContractError(str(exc)) from exc

    def apply(self, event: OrderEvent | FillEvent) -> bool:
        """导入一条券商事件，并在同一事务保存原文、去重身份、订单或账户投影。

        相同事件重投递返回 False；同一成交换 event_id 仍留原文，但返回 False 且
        不重复记账。新的状态事件返回 True，包括只留痕而不推进投影的旧序号事件。
        外部人工成交会入账并冻结新增；未知订单事件仅留痕，不补造内部授权意图。
        事务内的内容、身份或账务校验失败会回滚本次投影，再持久化冻结并抛
        ContractError。最初的内容摘要序列化在保护范围外；序列化异常直接传播，
        不保证写入冻结标记，调用方不能据此认定输入已被处理。
        """
        # mode="json" 把 Decimal、时间等转成 JSON 可编码表示；摘要绑定整条投递内容。
        # 这一步只形成去重指纹，不证明外部事实可靠，也尚未开始数据库事务。
        digest = canonical_hash(event.model_dump(mode="json"))
        try:
            # 模型副本可能绕过构造校验，存储边界必须再次核验完整输入。
            event = (
                FillEvent.model_validate(event.model_dump())
                if isinstance(event, FillEvent)
                else OrderEvent.model_validate(event.model_dump())
            )
            # 所有去重键、投影及账户写入共享事务。
            with self._db() as db:
                previous = db.execute(
                    "SELECT digest FROM events WHERE source=? AND event_id=?",
                    (event.source, event.event_id),
                ).fetchone()
                # 相同事件身份已经存在时不得重复记账。
                if previous is not None:
                    if previous[0] != digest:
                        raise ContractError("相同事件 ID 内容冲突")
                    return False
                # 成交身份可以在不同投递事件 ID 下重复。
                if isinstance(event, FillEvent):
                    # 第二重身份忽略传输事件 ID，保留成交全部业务内容。
                    fill_digest = canonical_hash(
                        event.model_dump(mode="json", exclude={"event_id"})
                    )
                    duplicate = db.execute(
                        "SELECT digest FROM fills WHERE source=? AND fill_id=?",
                        (event.source, event.fill_id),
                    ).fetchone()
                    if duplicate is not None and duplicate[0] != fill_digest:
                        raise ContractError("相同成交 ID 内容冲突")
                    # 保存不同投递封装以检测后续事件身份冲突。
                    db.execute(
                        "INSERT INTO events(source,event_id,digest,payload) VALUES (?,?,?,?)",
                        (event.source, event.event_id, digest, event.model_dump_json()),
                    )
                    if duplicate is not None:
                        # 传输别名也进入操作日志，重放时保留事件ID冲突证据但不再次记账。
                        self._journal(db, "event", event, event.at)
                        return False
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
                        raise ContractError("成交与订单身份不匹配")
                    # apply_fill 用库内当前账户和这笔新增成交产生新 AccountSnapshot；
                    # 它不自行存储或去重。这里已确认新 fill_id，才将返回的现金、持仓、
                    # 费用及成本写回 state，并同时保存成交身份，避免只扣款却丢失去重键。
                    account = apply_fill(self._read_account(db), event)
                    # 账户和成交去重键一并提交。
                    db.execute(
                        "UPDATE state SET payload=? WHERE key='account'",
                        (account.model_dump_json(),),
                    )
                    db.execute(
                        "INSERT INTO fills VALUES (?,?,?)",
                        (event.source, event.fill_id, fill_digest),
                    )
                    # 外部人工交易入账但必须冻结新增等待解释。
                    if order is None:
                        db.execute(
                            "INSERT OR IGNORE INTO freezes VALUES (?)",
                            (f"MANUAL_TRADE:{event.client_order_id}",),
                        )
                # 订单状态事件不改变账户。
                else:
                    db.execute(
                        "INSERT INTO events(source,event_id,digest,payload) VALUES (?,?,?,?)",
                        (event.source, event.event_id, digest, event.model_dump_json()),
                    )
                    # 未知订单事件保持差异，不创建内部意图。
                    order = self._read_order(db, event.client_order_id)
                    if order is None:
                        db.execute(
                            "INSERT OR IGNORE INTO freezes VALUES (?)",
                            (f"UNKNOWN_ORDER:{event.client_order_id}",),
                        )
                        self._journal(db, "event", event, event.at)
                        # 返回 True 表示消息与冻结原因已留痕，未知意图仍未建立，账户也未变。
                        return True
                    # 首次响应可以补齐外部身份，后续事件不得改变绑定。
                    if (
                        order.broker_order_id is not None
                        and order.broker_order_id != event.broker_order_id
                    ):
                        raise ContractError("券商订单 ID 内容冲突")
                    # orders.sequence 是该客户端订单已消费的最新状态序号；它不排序 FillEvent，
                    # 成交有自己的唯一身份和账务通道，不能因状态序号较旧而丢弃成交。
                    row = db.execute(
                        "SELECT sequence FROM orders WHERE client_id=?", (event.client_order_id,)
                    ).fetchone()
                    # 同序号比较原始事件，而不是可能已被独立订单快照推进的当前投影。
                    same_sequence = db.execute(
                        "SELECT payload FROM events WHERE source=? AND event_id<>? AND json_extract(payload,'$.kind')='order' AND json_extract(payload,'$.client_order_id')=? AND json_extract(payload,'$.sequence')=?",
                        (event.source, event.event_id, event.client_order_id, event.sequence),
                    ).fetchone()
                    if same_sequence is not None:
                        historical = OrderEvent.model_validate_json(same_sequence[0])
                        # 只忽略传输事件ID，其他内容必须完全一致。
                        if historical.model_dump(exclude={"event_id"}) != event.model_dump(
                            exclude={"event_id"}
                        ):
                            raise ContractError("同一订单事件序号内容冲突")
                    # 旧/同序号状态只追加接收日志后返回 True：原事件已保存，但不推进
                    # 当前 OrderRecord。之后的独立成交仍可正常入账。
                    if row is not None and event.sequence <= row[0]:
                        self._journal(db, "event", event, event.at)
                        return True
                    if event.filled_quantity > order.intent.quantity:
                        raise ContractError("订单事件累计成交超过委托量")
                    # FILLED必须确实达到原意图的全部数量。
                    if event.status == "FILLED" and event.filled_quantity != order.intent.quantity:
                        raise ContractError("FILLED状态必须达到全部委托数量")
                    # PARTIAL明确代表至少一股且仍有剩余。
                    if (
                        event.status == "PARTIAL"
                        and not 0 < event.filled_quantity < order.intent.quantity
                    ):
                        raise ContractError("PARTIAL状态与累计数量不一致")
                    filled = max(order.filled_quantity, event.filled_quantity)
                    # 完全成交优先于任何撤单中间态。
                    status = "FILLED" if filled == order.intent.quantity else event.status
                    # 独立查询可能已确认部分成交，较旧开放事件不能抹掉数量。
                    if 0 < filled < order.intent.quantity and status in {"OPEN", "PERSISTED"}:
                        status = "PARTIAL"
                    # 已结束订单不能被迟到开放事件重新打开。
                    if order.status in {"CANCELED", "REJECTED"} and status in {
                        "OPEN",
                        "PARTIAL",
                        "CANCEL_PENDING",
                        "UNKNOWN",
                        "PERSISTED",
                    }:
                        status = order.status
                    # updated 以累计成交股数生成当前订单状态，随后与最新 sequence 一起保存；
                    # 这一分支始终不写 state.account，账户只能由新增成交/公司行动改变。
                    updated = OrderRecord(
                        intent=order.intent,
                        status=status,
                        broker_order_id=event.broker_order_id,
                        filled_quantity=filled,
                    )
                    db.execute(
                        "UPDATE orders SET payload=?, sequence=? WHERE client_id=?",
                        (updated.model_dump_json(), event.sequence, event.client_order_id),
                    )
                # 原始事件接收顺序必须与财务变化及状态投影同一事务记录。
                self._journal(db, "event", event, event.at)
                return True
        except ValueError as exc:
            # 单独事务确保冻结不会随前面错误回滚。
            self.freeze(str(exc))
            raise ContractError(str(exc)) from exc

    def apply_action(self, action: CorporateAction, at: datetime) -> bool:
        """以 UTC 处理时刻 at 导入已发生且可用的拆股或股息，同时保存账户和日志。

        原样重投递返回 False；首次应用返回 True。内容哈希、时点、质量或账务校验
        失败时回滚行动事务，另行保存冻结原因并抛 ContractError。
        """
        try:
            verify_record(action)
            digest = canonical_hash(action.model_dump(mode="json"))
            # 行动去重和账务共享事务。
            with self._db() as db:
                previous = db.execute(
                    "SELECT digest FROM actions WHERE source=? AND action_id=?",
                    (action.source, action.action_id),
                ).fetchone()
                if previous is not None:
                    # 内容变动必须建新事实并审查，禁止覆盖旧动作。
                    if previous[0] != digest:
                        raise ContractError("相同公司行动 ID 内容冲突")
                    return False
                # apply_action 读取当前账户，并按 at 校验发生/可用时间后返回新账户；
                # 拆股只改股数不改总成本，股息按行动携带的权益股数增加美元现金。
                # 返回账户、行动原文和 journal 将在同一事务提交，供恢复保持原先交错顺序。
                account = apply_action(self._read_account(db), action, at)
                db.execute(
                    "INSERT INTO actions VALUES (?,?,?,?)",
                    (action.source, action.action_id, digest, action.model_dump_json()),
                )
                db.execute(
                    "UPDATE state SET payload=? WHERE key='account'", (account.model_dump_json(),)
                )
                # 公司行动与未来新意图的交错必须出现在统一日志中。
                self._journal(db, "action", action, at)
                return True
        except ValueError as exc:
            self.freeze(str(exc))
            raise ContractError(str(exc)) from exc

    def orders(self) -> list[OrderRecord]:
        """按客户端身份返回内部订单；只读且结果稳定。"""
        with self._db() as db:
            return [
                OrderRecord.model_validate_json(row[0])
                for row in db.execute("SELECT payload FROM orders ORDER BY client_id")
            ]

    def events(self) -> list[OrderEvent | FillEvent]:
        """按接收顺序读取原始事件，包含同一成交的不同投递封装。"""
        with self._db() as db:
            # 库中保存的是 JSON 原文；kind 决定重建 FillEvent 或 OrderEvent，模型验证
            # 将文本还原为类型化金额/时间。返回顺序是接收顺序，不按事件发生时刻重排。
            rows = db.execute("SELECT payload FROM events ORDER BY id").fetchall()
            return [
                FillEvent.model_validate_json(row[0])
                if '"kind":"fill"' in row[0]
                else OrderEvent.model_validate_json(row[0])
                for row in rows
            ]

    def account(self, at: datetime) -> AccountSnapshot:
        """返回指定查询时刻的当前账户投影；at 是 UTC 查询时间而非历史查询条件。"""
        with self._db() as db:
            account = self._read_account(db)
            # model_dump 展开当前投影后只替换查询时刻，model_validate 重新核验 UTC 等约束；
            # 返回新对象而不写数据库，也不会重放到历史 at 对应的账户状态。
            return AccountSnapshot.model_validate({**account.model_dump(), "as_of": at})
