"""唯一订单执行入口：先持久化、后风控与发送；超时恢复对账，不盲目换键重发。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from quant_core.contracts import (
    Broker,
    Clock,
    ContractError,
    DemoConfig,
    EventStore,
    FillEvent,
    OrderIntent,
    OrderRecord,
    Quote,
    ReconciliationResult,
    RiskBlocked,
    SecurityRecord,
    TargetPortfolio,
    TradingCalendar,
)
from quant_core.risk import assess_operation, assess_order, assess_reduce


class ExecutionService:
    """注入 EventStore/Broker/Clock/配置/日历的唯一执行服务；不会直接联网或查系统时间。

    store 保存本系统的意图、事件和由成交推导的账户；broker 提供独立的券商订单、
    事件及账户，并接收提交/撤单请求。clock 提供 UTC 处理时刻，calendar 判断该时刻
    是否可交易，config 提供账户范围、费用与风险限制；这些依赖由应用装配。
    每个公开交易操作取得统一账户锁；故障恢复只导入真实事件，不反向修改券商事实。
    """

    def __init__(
        self,
        store: EventStore,
        broker: Broker,
        clock: Clock,
        config: DemoConfig,
        calendar: TradingCalendar,
    ) -> None:
        """保存执行依赖；若内部库与券商公开同一路径，抛 ContractError 拒绝装配。"""
        self.store = store
        self.broker = broker
        self.clock = clock
        self.config = config
        self.calendar = calendar
        # 若适配器公开路径，则主动阻止把内外部状态放进同一文件。
        if getattr(store, "path", None) is not None and getattr(store, "path", None) == getattr(
            broker, "path", None
        ):
            raise ContractError("内部事件库与券商数据库必须使用不同文件")

    def _find(self, client_order_id: str) -> OrderRecord | None:
        """按稳定客户端订单 ID 查询内部记录；未找到返回 None。"""
        return next(
            (
                record
                for record in self.store.orders()
                if record.intent.client_order_id == client_order_id
            ),
            None,
        )

    def _recover(self, just_persisted: str | None = None) -> ReconciliationResult:
        """在已持账户锁下恢复事件并比较内外部事实；差异返回失败，不修改外部事实。

        just_persisted 仅表示本次调用新建且确定尚未发送的新意图；重启不得使用此豁免。
        导入的事件会修改内部账务和状态；连接、超时及契约冲突写入差异结果，
        已成功落库的事实保留用于后续恢复，其他存储错误仍向调用方传播。
        """
        now = self.clock.now()
        differences: list[str] = []
        try:
            # remote_orders 以客户端订单 ID 为键，值是券商当前 OrderRecord（意图、状态及累计股数）。
            # 它用于核对订单是否受理，不能替代随后逐笔导入的成交事件。
            remote_orders = {
                record.intent.client_order_id: record for record in self.broker.orders()
            }
            # UNKNOWN 与旧 PERSISTED 必须按稳定客户身份单独查询。
            for local in self.store.orders():
                if (
                    local.status in {"UNKNOWN", "PERSISTED"}
                    and local.intent.client_order_id != just_persisted
                ):
                    # None 不等于确定未受理，不能因此自动重发。
                    confirmed = self.broker.query(local.intent.client_order_id)
                    if confirmed is not None:
                        remote_orders[local.intent.client_order_id] = confirmed
            # 先导入可重复投递的真实状态和成交事件。
            for event in self.broker.events():
                # 查询得到的事实也必须已经发生，不能提前记入未来成交。
                if event.at > now:
                    self.store.freeze(f"FUTURE_BROKER_EVENT:{event.source}:{event.event_id}")
                    # 冻结原因已落库；只跳过此未来事件，继续核对其他事实，最终结果仍含冻结差异。
                    continue
                # OrderEvent 只推进订单状态；FillEvent 按新增成交股数改变账户，存储负责去重。
                self.store.apply(event)
            # local_orders 使用同一客户端键索引本地记录；只允许更新已落盘意图的状态。
            local_orders = {record.intent.client_order_id: record for record in self.store.orders()}
            for client_id, remote in remote_orders.items():
                # 外部人工订单不能自动建立策略意图。
                if client_id not in local_orders:
                    self.store.freeze(f"MANUAL_ORDER:{client_id}")
                    differences.append(f"external_order:{client_id}")
                else:
                    self.store.record_order(remote)
            for local in self.store.orders():
                # 当前调用新落盘而尚未发送的意图可以暂时没有券商记录。
                if local.intent.client_order_id == just_persisted:
                    continue
                # 风控本地拒绝未产生券商请求，是明确终态。
                if local.status == "REJECTED" and local.broker_order_id is None:
                    continue
                counterpart = remote_orders.get(local.intent.client_order_id)
                # 没有外部事实意味着存在发送前后崩溃歧义。
                if counterpart is None:
                    # 复制原记录只改状态，保留客户/券商身份和累计股数；model_copy 本身不校验，
                    # record_order 会重新验证并持久化，使下次调用仍知道这是一张未确认旧单。
                    self.store.record_order(local.model_copy(update={"status": "UNKNOWN"}))
                    differences.append(f"unconfirmed_order:{local.intent.client_order_id}")
                elif local != counterpart:
                    differences.append(f"order_mismatch:{local.intent.client_order_id}")
            # actual 来自独立券商账户；expected 来自刚导入事件后的内部账务投影。
            # 两者的 positions 以证券 ID 对应实际股数，cost_basis 对应持仓总成本美元；
            # 不比较各自查询时间，而是逐项核对现金、可用现金、费用、数量和成本。
            actual = self.broker.account()
            expected = self.store.account(now)
            # 必须比较费用和成本，不能只比较净资产总额。
            for field in (
                "account_id",
                "cash",
                "available_cash",
                "positions",
                "fees",
                "cost_basis",
            ):
                if getattr(actual, field) != getattr(expected, field):
                    differences.append(f"account_mismatch:{field}")
            # totals 按客户端订单 ID 累计已入账股数；seen_fills 按（来源, 成交 ID）识别一笔成交，
            # 同一成交换 event_id 再投递也不能多算。缺少成交的订单默认累计为 0 股。
            seen_fills: set[tuple[str, str]] = set()
            totals: dict[str, int] = {}
            for event in self.store.events():
                if isinstance(event, FillEvent) and (event.source, event.fill_id) not in seen_fills:
                    seen_fills.add((event.source, event.fill_id))
                    totals[event.client_order_id] = (
                        totals.get(event.client_order_id, 0) + event.quantity
                    )
            # 每笔已知券商订单都要核实成交事件是否齐全。
            for client_id, remote in remote_orders.items():
                if totals.get(client_id, 0) != remote.filled_quantity:
                    differences.append(f"fill_quantity_mismatch:{client_id}")
        except (ConnectionError, TimeoutError, ContractError) as exc:
            # 保存错误类别和内容，保留已有持久化事实用于下一轮恢复。
            differences.append(f"recovery_failed:{type(exc).__name__}:{exc}")
        # 人工成交和身份冲突永久冻结，自动一致不自动解冻。
        differences.extend(f"frozen:{reason}" for reason in self.store.frozen())
        # 结果交给新增订单风控或应用决定是否继续；空 differences 表示本轮这些核对全部一致。
        return ReconciliationResult(
            as_of=now, matched=not differences, differences=sorted(set(differences))
        )

    def recover(self) -> ReconciliationResult:
        """取得账户锁后恢复并对账；返回明确匹配结果，锁竞争抛 RiskBlocked。"""
        with self.store.account_lock():
            return self._recover()

    def _turnover(self, decision_id: str) -> Decimal:
        """按唯一实际成交计算本决策已用总换手美元额；买卖均取正，不用卖单限价估算。"""
        # decisions 把客户端订单 ID 映射到调仓决策 ID，用于从全部成交中筛出本轮已花换手。
        decisions = {
            record.intent.client_order_id: record.intent.decision_id
            for record in self.store.orders()
        }
        # 来源与成交ID共同确定独立经济事实。
        seen: set[tuple[str, str]] = set()
        amount = Decimal("0")
        for event in self.store.events():
            if (
                isinstance(event, FillEvent)
                and decisions.get(event.client_order_id) == decision_id
                and (event.source, event.fill_id) not in seen
            ):
                # 去重不能依赖传输事件ID。
                seen.add((event.source, event.fill_id))
                # 实际成交价乘新增股数，卖出和买入不互相抵销。
                amount += event.price * event.quantity
        return amount

    def _submit(
        self,
        intent: OrderIntent,
        quotes: dict[str, Quote],
        *,
        reduce_only: bool = False,
        authorized: bool = True,
        **risk_context: Any,
    ) -> OrderRecord:
        """在调用方已持有账户锁时，保存意图、恢复事实并检查本次提交权限。

        quotes 必须是原始执行报价；risk_context 使用 assess_order 的单位和约定。
        reduce_only 选择独立的卖出减仓检查，不授予无事实依据的紧急交易权限。
        新意图可能写入本地拒绝状态；风控拒绝抛 RiskBlocked，身份冲突抛 ContractError。
        已有意图只恢复并返回当前状态；发送超时返回 UNKNOWN，调用方应恢复而非重发。
        """
        # 先确定该幂等键是否在本次调用前已经存在。
        previous = self._find(intent.client_order_id)
        # save_intent 将新意图写成 PERSISTED 并留日志；这时尚未发给券商。
        # 同键同内容保留旧事实，同键异内容在任何外部调用前冻结并失败。
        self.store.save_intent(intent)
        # 旧意图跨调用重试只能恢复，不能再次执行 submit。
        if previous is not None:
            self._recover()
            recovered = self._find(intent.client_order_id)
            if recovered is None:
                raise ContractError("恢复后订单意图缺失")
            # 即使恢复仍不一致也只返回现有记录；调用方必须看状态，不能把返回值当作新受理。
            return recovered
        # 明确当前意图尚未调用 Broker.submit，豁免当前这一个未发送身份。
        reconciliation = self._recover(just_persisted=intent.client_order_id)
        if reduce_only:
            # 减仓仍必须获得当前券商确认账户，断线不得沿用旧持仓。
            try:
                account = self.broker.account()
            except (ConnectionError, TimeoutError) as exc:
                # 保存本地拒绝，避免下次把确定未发送误当不确定请求。
                self.store.record_order(OrderRecord(intent=intent, status="REJECTED"))
                raise RiskBlocked("减仓需要可查询的独立账户事实") from exc
            # 减仓也检查已确认数量、未完成卖单、报价和时间等硬约束。
            decision = assess_reduce(
                intent,
                account,
                self.store.orders(),
                quotes,
                self.config,
                self.clock.now(),
                self.calendar,
                authorized=authorized,
            )
        else:
            recorded_turnover = self._turnover(intent.decision_id)
            # 调用方可提供额外保守预算，但不能覆盖本地已确认的较高实际成交额。
            provided_turnover = risk_context.get("turnover_used")
            risk_context["turnover_used"] = (
                recorded_turnover
                if provided_turnover is None
                else max(recorded_turnover, provided_turnover)
            )
            # 风控明确接收本次恢复结论与全部未完成订单。
            decision = assess_order(
                intent,
                self.store.account(self.clock.now()),
                self.store.orders(),
                quotes,
                self.config,
                self.clock.now(),
                self.calendar,
                reconciled=reconciliation.matched,
                **risk_context,
            )
        # decision 是本次风控的允许结论与原因，不是券商回执。拒绝时只把本地意图置为
        # REJECTED 并抛异常；先前恢复成功写入的成交和账户不会因本次拒绝回滚。
        if not decision.allowed:
            self.store.record_order(OrderRecord(intent=intent, status="REJECTED"))
            raise RiskBlocked(",".join(decision.reasons))
        # 风险通过后才允许唯一外部提交。
        try:
            # 返回 OrderRecord 表示券商当时的订单状态；实际成交仍须经事件流入账。
            accepted = self.broker.submit(intent)
        # 网络异常无法证明委托是否被受理。
        except (TimeoutError, ConnectionError):
            # UNKNOWN 表示可能已受理但无法确认；保存原客户端身份供恢复查询，不能换键再发。
            unknown = OrderRecord(intent=intent, status="UNKNOWN")
            self.store.record_order(unknown)
            return unknown
        # 成功回执只更新订单状态，不伪造成交。
        self.store.record_order(accepted)
        return accepted

    def submit(
        self,
        intent: OrderIntent,
        quotes: dict[str, Quote],
        *,
        security_records: list[SecurityRecord] | None = None,
        adv: dict[str, float] | None = None,
        target: TargetPortfolio | None = None,
        reference_nav: Decimal | None = None,
        peak_nav: Decimal | None = None,
        turnover_used: Decimal | None = None,
        data_good: bool = True,
    ) -> OrderRecord:
        """正常策略提交唯一入口；输入美元整股意图与原始报价，拒绝抛 RiskBlocked。

        security_records 应按决策时点提供主表，adv 是历史日均成交股数；target 是批准目标。
        reference_nav、peak_nav 和 turnover_used 均为美元，后者只可提高已用换手预算。
        None 不构成检查豁免：必要事实缺失仍由统一风控拒绝。
        方法持锁写入意图、恢复事件并调用注入 Broker；超时返回 UNKNOWN，不能视为拒单。
        锁的实际作用范围由 EventStore 实现决定，当前 SQLite 适配器仅协调同机执行者。
        """
        # 锁覆盖先落意图、恢复、检查和发送整个序列；退出或异常时释放锁，已提交事实仍保留。
        with self.store.account_lock():
            return self._submit(
                intent,
                quotes,
                security_records=security_records,
                adv=adv,
                target=target,
                reference_nav=reference_nav,
                peak_nav=peak_nav,
                turnover_used=turnover_used,
                data_good=data_good,
            )

    def cancel(self, client_order_id: str, *, authorized: bool = True) -> OrderRecord:
        """持锁恢复既有订单后请求撤销剩余委托，撤单不撤回已发生的成交。

        缺授权或状态不明抛 RiskBlocked；请求超时保存并返回 UNKNOWN。
        CANCEL_PENDING 仅说明正在撤单，仍可能成交；最终结果须继续恢复核对。
        """
        with self.store.account_lock():
            # 先恢复可能已经成交的实际订单。
            reconciliation = self._recover()
            current = self._find(client_order_id)
            # 当前状态必须可识别，未知不能盲目撤错订单。
            permission = assess_operation(
                "CANCEL",
                reconciled=reconciliation.matched,
                known_state=current is not None and current.status not in {"UNKNOWN", "PERSISTED"},
                authorized=authorized,
            )
            if not permission.allowed or current is None:
                raise RiskBlocked(",".join(permission.reasons))
            # 本地拒绝或已终结订单无需再请求券商。
            if current.status in {"FILLED", "REJECTED", "CANCELED"}:
                return current
            try:
                record = self.broker.cancel(client_order_id)
            # 不确定撤单状态不能被自动当作成功。
            except (TimeoutError, ConnectionError):
                # model_copy 生成保留意图、券商 ID 和累计成交股数的副本，仅改状态，不执行校验；
                # 随后的 record_order 在存储边界重新校验，原 current 对象未被改写。
                record = current.model_copy(update={"status": "UNKNOWN"})
            # 保存当前可知订单状态，账务只由成交事件改变。
            self.store.record_order(record)
            return record

    def replace(
        self,
        client_order_id: str,
        new_intent: OrderIntent,
        quotes: dict[str, Quote],
        *,
        authorized: bool = True,
        **risk_context: Any,
    ) -> OrderRecord:
        """先确认原单剩余量已撤销，再以新意图重新检查价格、数量和风险。

        新客户端 ID 必须不同且账户/证券/方向一致，否则抛 ContractError。
        原单仍开放或部分成交时，本次只请求撤单并抛 RiskBlocked；其他未确认
        撤销的状态同样阻断替换。撤单竞态中的成交先恢复入账，新单再走 _submit。
        new_intent.quantity 是调用方指定的新委托股数，本方法不会替调用方自动扣除原单已成交量。
        """
        # 改单从恢复到新提交必须保持同一账户排他所有权。
        with self.store.account_lock():
            reconciliation = self._recover()
            current = self._find(client_order_id)
            # 改单属于新增风险路径，要求当前账户对齐。
            permission = assess_operation(
                "REPLACE",
                reconciled=reconciliation.matched,
                known_state=current is not None and current.status not in {"UNKNOWN", "PERSISTED"},
                authorized=authorized,
            )
            if not permission.allowed or current is None:
                raise RiskBlocked(",".join(permission.reasons))
            # 必须建立独立新意图，不能修改旧键内容。
            if new_intent.client_order_id == client_order_id or (
                new_intent.account_id,
                new_intent.security_id,
                new_intent.side,
            ) != (current.intent.account_id, current.intent.security_id, current.intent.side):
                raise ContractError("替换必须使用新 ID 并保持账户、证券和方向")
            # 尚未确认撤单时不能发送替代委托。
            if current.status != "CANCELED":
                if current.status in {"OPEN", "PARTIAL"}:
                    try:
                        # 本轮只撤单，不同时发送替代订单。
                        pending = self.broker.cancel(client_order_id)
                    except (ConnectionError, TimeoutError):
                        # 保留旧单身份与已成交股数，只将撤单结果标为未知；不会生成替代单。
                        pending = current.model_copy(update={"status": "UNKNOWN"})
                    self.store.record_order(pending)
                # FILLED、REJECTED、CANCEL_PENDING 都不构成已撤剩余量授权。
                raise RiskBlocked("replace_requires_confirmed_cancel")
            # 旧剩余量已确认撤销后才保存并核查新意图。
            return self._submit(new_intent, quotes, **risk_context)

    def reduce(
        self, intent: OrderIntent, quotes: dict[str, Quote], *, authorized: bool = True
    ) -> OrderRecord:
        """持锁提交经独立授权的卖出减仓，冻结新增时仍核验可卖数量和原始报价。

        会保存意图、恢复事件并调用 Broker；缺事实或风控拒绝抛 RiskBlocked，
        发送超时返回 UNKNOWN。方法不生成清仓目标，调用方须提供明确的减仓意图。
        """
        with self.store.account_lock():
            return self._submit(intent, quotes, reduce_only=True, authorized=authorized)
