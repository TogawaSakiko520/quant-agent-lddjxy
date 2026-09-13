"""唯一订单执行入口：先持久化、后风控与发送；超时恢复对账，不盲目换键重发。"""

# 延迟注解求值便于依赖注入。
from __future__ import annotations

# 金额上下文使用美元 Decimal。
from decimal import Decimal

# 参数转交的类型保留在唯一风险函数签名中。
from typing import Any

# 执行核心只依赖公共接口，不导入任何具体适配器。
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

# 独立分类风控提供唯一业务规则，执行层不复制阈值。
from quant_core.risk import assess_operation, assess_order, assess_reduce


class ExecutionService:
    """注入 EventStore/Broker/Clock/配置/日历的唯一执行服务；不会直接联网或查系统时间。

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
        """保存依赖并验证两个适配器未复用同一路径；不提交交易，有读取副作用。"""
        # 内部事件事实的唯一写入接口。
        self.store = store
        # 独立券商事实查询和交易接口。
        self.broker = broker
        # 所有业务时刻由显式时钟提供。
        self.clock = clock
        # 风控参数和离线账户白名单来自不可变配置。
        self.config = config
        # 真实交易时段语义由日历协议提供。
        self.calendar = calendar
        # 若适配器公开路径，则主动阻止把内外部状态放进同一文件。
        if getattr(store, "path", None) is not None and getattr(store, "path", None) == getattr(
            broker, "path", None
        ):
            # 独立对账不能共享同一个账户状态源。
            raise ContractError("内部事件库与券商数据库必须使用不同文件")

    def _find(self, client_order_id: str) -> OrderRecord | None:
        """查询内部稳定订单身份；未找到返回 None，无修改副作用。"""
        # 避免让具体数据库查询类型泄漏到核心。
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

        just_persisted 仅表示当前调用已确定尚未发送的新意图；重启不得使用此豁免。
        """
        # 每次对账使用同一明确业务时刻。
        now = self.clock.now()
        # 差异保存可定位的机器事实，不靠报告修正账务。
        differences: list[str] = []
        # 断线或内容冲突都必须返回不可新增风险。
        try:
            # 券商订单快照与事件流分别查询，不能用一个替代另一个。
            remote_orders = {
                record.intent.client_order_id: record for record in self.broker.orders()
            }
            # UNKNOWN 与旧 PERSISTED 必须按稳定客户身份单独查询。
            for local in self.store.orders():
                # 当前调用尚未发送的意图不是一次历史不确定请求。
                if (
                    local.status in {"UNKNOWN", "PERSISTED"}
                    and local.intent.client_order_id != just_persisted
                ):
                    # None 不等于确定未受理，不能因此自动重发。
                    confirmed = self.broker.query(local.intent.client_order_id)
                    # 单独查询发现订单后补充快照。
                    if confirmed is not None:
                        # 保留明确的券商返回事实。
                        remote_orders[local.intent.client_order_id] = confirmed
            # 先导入可重复投递的真实状态和成交事件。
            for event in self.broker.events():
                # 查询得到的事实也必须已经发生，不能提前记入未来成交。
                if event.at > now:
                    # 留存明确故障身份并停止新增风险，绝不提前产生购买力。
                    self.store.freeze(f"FUTURE_BROKER_EVENT:{event.source}:{event.event_id}")
                    # 可疑未来事件不进入当前财务投影。
                    continue
                # 存储事务承担双重去重与账务，重复不会产生第二次持仓。
                self.store.apply(event)
            # 已落盘意图是内部授权边界。
            local_orders = {record.intent.client_order_id: record for record in self.store.orders()}
            # 券商订单用于恢复响应丢失后的外部身份与最新状态。
            for client_id, remote in remote_orders.items():
                # 外部人工订单不能自动建立策略意图。
                if client_id not in local_orders:
                    # 永久标记需要人工解释的外部活动。
                    self.store.freeze(f"MANUAL_ORDER:{client_id}")
                    # 差异报告带具体客户端身份。
                    differences.append(f"external_order:{client_id}")
                # 已存在稳定意图时更新独立订单状态投影。
                else:
                    # 同键异内容会由存储冻结并抛错。
                    self.store.record_order(remote)
            # 更新后重新读取，避免比较旧缓存状态。
            for local in self.store.orders():
                # 当前调用新落盘而尚未发送的意图可以暂时没有券商记录。
                if local.intent.client_order_id == just_persisted:
                    # 此豁免不会跨调用或重启保存。
                    continue
                # 风控本地拒绝未产生券商请求，是明确终态。
                if local.status == "REJECTED" and local.broker_order_id is None:
                    # 本地风控拒绝无需伪造券商订单。
                    continue
                # 独立查找与内部订单相对应的券商事实。
                counterpart = remote_orders.get(local.intent.client_order_id)
                # 没有外部事实意味着存在发送前后崩溃歧义。
                if counterpart is None:
                    # 持久化 UNKNOWN 确保下一次不能盲发。
                    self.store.record_order(local.model_copy(update={"status": "UNKNOWN"}))
                    # 显式要求核对未找到的订单。
                    differences.append(f"unconfirmed_order:{local.intent.client_order_id}")
                # 相同身份仍需逐字段比较当前订单状态。
                elif local != counterpart:
                    # 不以字符串状态排序掩盖生命周期冲突。
                    differences.append(f"order_mismatch:{local.intent.client_order_id}")
            # 现金、持仓、费用、成本从独立账户接口获取。
            actual = self.broker.account()
            # 内部投影时间只标记本次查询。
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
                # 同值才算该类事实对齐。
                if getattr(actual, field) != getattr(expected, field):
                    # 差异保存字段名供恢复文档解释。
                    differences.append(f"account_mismatch:{field}")
            # 从唯一成交事实独立累计订单实际入账数量。
            seen_fills: set[tuple[str, str]] = set()
            # 每个客户端订单的实际成交数量。
            totals: dict[str, int] = {}
            # 原始事件可以包括不同投递封装的同一成交。
            for event in self.store.events():
                # 只有唯一成交事实参与财务数量对账。
                if isinstance(event, FillEvent) and (event.source, event.fill_id) not in seen_fills:
                    # 记录已累计的独立成交身份。
                    seen_fills.add((event.source, event.fill_id))
                    # 订单状态数量不能代替实际成交事件数量。
                    totals[event.client_order_id] = (
                        totals.get(event.client_order_id, 0) + event.quantity
                    )
            # 每笔已知券商订单都要核实成交事件是否齐全。
            for client_id, remote in remote_orders.items():
                # 任何累计量差异都可能意味着成交事件缺口。
                if totals.get(client_id, 0) != remote.filled_quantity:
                    # 防止状态已经 FILLED 但现金事件未到时继续交易。
                    differences.append(f"fill_quantity_mismatch:{client_id}")
        # 断线、超时和事实冲突都不能变成成功对账。
        except (ConnectionError, TimeoutError, ContractError) as exc:
            # 保存错误类别和内容，保留已有持久化事实用于下一轮恢复。
            differences.append(f"recovery_failed:{type(exc).__name__}:{exc}")
        # 人工成交和身份冲突永久冻结，自动一致不自动解冻。
        differences.extend(f"frozen:{reason}" for reason in self.store.frozen())
        # 只有无任何未解释差异才能开放正常新增路径。
        return ReconciliationResult(
            as_of=now, matched=not differences, differences=sorted(set(differences))
        )

    def recover(self) -> ReconciliationResult:
        """取得账户锁后恢复并对账；返回明确匹配结果，锁竞争抛 RiskBlocked。"""
        # 恢复也会写入事件，因此需要同一唯一执行者锁。
        with self.store.account_lock():
            # 不豁免任何历史 PERSISTED 意图。
            return self._recover()

    def _turnover(self, decision_id: str) -> Decimal:
        """按唯一实际成交计算本决策已用总换手美元额；买卖均取正，不用卖单限价估算。"""
        # 意图决定成交归属的稳定调仓决策。
        decisions = {
            record.intent.client_order_id: record.intent.decision_id
            for record in self.store.orders()
        }
        # 来源与成交ID共同确定独立经济事实。
        seen: set[tuple[str, str]] = set()
        # 空历史明确为已确认零成交额。
        amount = Decimal("0")
        # 原始事件可能有重复传输封装。
        for event in self.store.events():
            # 只累计属于当前决策且尚未见过的实际成交。
            if (
                isinstance(event, FillEvent)
                and decisions.get(event.client_order_id) == decision_id
                and (event.source, event.fill_id) not in seen
            ):
                # 去重不能依赖传输事件ID。
                seen.add((event.source, event.fill_id))
                # 实际成交价乘新增股数，卖出和买入不互相抵销。
                amount += event.price * event.quantity
        # 返回可从日志独立重算的明确累计金额。
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
        """持锁提交内部流程；先落盘后风险验证，重复只恢复，拒绝抛 RiskBlocked。"""
        # 先确定该幂等键是否在本次调用前已经存在。
        previous = self._find(intent.client_order_id)
        # 同键异内容在任何外部调用前冻结并失败。
        self.store.save_intent(intent)
        # 旧意图跨调用重试只能恢复，不能再次执行 submit。
        if previous is not None:
            # 若上次已受理但响应丢失，这里会补齐券商事实。
            self._recover()
            # 恢复后读取当前可知状态。
            recovered = self._find(intent.client_order_id)
            # 已有意图不应消失，否则状态库损坏。
            if recovered is None:
                # 不重新构造意图掩盖消失事实。
                raise ContractError("恢复后订单意图缺失")
            # 返回已知或 UNKNOWN 状态，绝不盲目再发。
            return recovered
        # 明确当前意图尚未调用 Broker.submit，豁免当前这一个未发送身份。
        reconciliation = self._recover(just_persisted=intent.client_order_id)
        # 普通新单和独立减仓使用不同授权规则。
        if reduce_only:
            # 减仓仍必须获得当前券商确认账户，断线不得沿用旧持仓。
            try:
                # 独立账户事实决定可卖的实际股数。
                account = self.broker.account()
            # 查询失败使当前尚未发送的意图被本地明确拒绝。
            except (ConnectionError, TimeoutError) as exc:
                # 保存本地拒绝，避免下次把确定未发送误当不确定请求。
                self.store.record_order(OrderRecord(intent=intent, status="REJECTED"))
                # 不能把紧急状态解释为无账户事实交易授权。
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
        # 正常交易使用完整策略与风险上下文。
        else:
            # 已成交总换手必须使用真实FillEvent，不按卖单限价下界估算。
            recorded_turnover = self._turnover(intent.decision_id)
            # 调用方可提供额外保守预算，但不能覆盖本地已确认的较高实际成交额。
            provided_turnover = risk_context.get("turnover_used")
            # 没有外部金额时直接使用明确的事件累计值。
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
        # 任何规则拒绝都不能继续访问提交接口。
        if not decision.allowed:
            # 本地明确拒绝不声称券商拒绝。
            self.store.record_order(OrderRecord(intent=intent, status="REJECTED"))
            # 机器原因进入应用错误报告。
            raise RiskBlocked(",".join(decision.reasons))
        # 风险通过后才允许唯一外部提交。
        try:
            # 稳定客户端 ID 不因 run_id 或重试改变。
            accepted = self.broker.submit(intent)
        # 网络异常无法证明委托是否被受理。
        except (TimeoutError, ConnectionError):
            # 保留 UNKNOWN，而非误判 REJECTED 或重新生成订单身份。
            unknown = OrderRecord(intent=intent, status="UNKNOWN")
            # 不确定状态需要跨进程重启保存。
            self.store.record_order(unknown)
            # 调用者收到明确未知结果后可显式恢复。
            return unknown
        # 成功回执只更新订单状态，不伪造成交。
        self.store.record_order(accepted)
        # 返回独立券商确认的订单状态。
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

        全部风险上下文转交统一风控；方法可能写意图、账户事件并提交离线券商订单。
        """
        # 跨不同运行目录仍只允许同账号一个有效发送者。
        with self.store.account_lock():
            # 不省略行业、流动性、目标或累计换手约束。
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
        """独立授权撤单入口；返回 CANCEL_PENDING 或终态，禁止把请求当确认。"""
        # 撤单也属于唯一账户写入者的职责。
        with self.store.account_lock():
            # 先恢复可能已经成交的实际订单。
            reconciliation = self._recover()
            # 只允许取消已有可追溯意图。
            current = self._find(client_order_id)
            # 当前状态必须可识别，未知不能盲目撤错订单。
            permission = assess_operation(
                "CANCEL",
                reconciled=reconciliation.matched,
                known_state=current is not None and current.status not in {"UNKNOWN", "PERSISTED"},
                authorized=authorized,
            )
            # 缺授权或状态未知不会触发外部动作。
            if not permission.allowed or current is None:
                # 冻结新增并不自动剥夺已知订单撤销权限。
                raise RiskBlocked(",".join(permission.reasons))
            # 本地拒绝或已终结订单无需再请求券商。
            if current.status in {"FILLED", "REJECTED", "CANCELED"}:
                # 幂等返回当前明确终态。
                return current
            # 撤单响应同样可能丢失。
            try:
                # 请求不会在 FakeBroker 内直接变成确认。
                record = self.broker.cancel(client_order_id)
            # 不确定撤单状态不能被自动当作成功。
            except (TimeoutError, ConnectionError):
                # 保留原意图、已知券商 ID 和成交数，显式变为 UNKNOWN。
                record = current.model_copy(update={"status": "UNKNOWN"})
            # 保存当前可知订单状态，账务只由成交事件改变。
            self.store.record_order(record)
            # 调用者依据状态决定等待或恢复。
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
        """撤单确认后以新意图改价/数量；未确认时仅请求撤单并抛 RiskBlocked。

        新客户端 ID 必须不同且账户/证券/方向一致；新意图仍走完整风险与先落盘流程。
        """
        # 改单从恢复到新提交必须保持同一账户排他所有权。
        with self.store.account_lock():
            # 先导入撤单期间可能发生的真实成交。
            reconciliation = self._recover()
            # 查找原始委托，不能凭券商 ID 猜测授权。
            current = self._find(client_order_id)
            # 改单属于新增风险路径，要求当前账户对齐。
            permission = assess_operation(
                "REPLACE",
                reconciled=reconciliation.matched,
                known_state=current is not None and current.status not in {"UNKNOWN", "PERSISTED"},
                authorized=authorized,
            )
            # 缺授权或对账差异明确阻止替换。
            if not permission.allowed or current is None:
                # 不自动放宽正常新单规则。
                raise RiskBlocked(",".join(permission.reasons))
            # 必须建立独立新意图，不能修改旧键内容。
            if new_intent.client_order_id == client_order_id or (
                new_intent.account_id,
                new_intent.security_id,
                new_intent.side,
            ) != (current.intent.account_id, current.intent.security_id, current.intent.side):
                # 无关交易应走独立正常 submit，而非假称改单。
                raise ContractError("替换必须使用新 ID 并保持账户、证券和方向")
            # 尚未确认撤单时不能发送替代委托。
            if current.status != "CANCELED":
                # 只有开放或部分成交状态需要发起撤单请求。
                if current.status in {"OPEN", "PARTIAL"}:
                    # 请求与响应可能存在不确定性。
                    try:
                        # 本轮只撤单，不同时发送替代订单。
                        pending = self.broker.cancel(client_order_id)
                    # 撤单超时仍不能假装确认成功。
                    except (ConnectionError, TimeoutError):
                        # 保存明确未知状态供恢复。
                        pending = current.model_copy(update={"status": "UNKNOWN"})
                    # 记录撤单中间状态。
                    self.store.record_order(pending)
                # FILLED、REJECTED、CANCEL_PENDING 都不构成已撤剩余量授权。
                raise RiskBlocked("replace_requires_confirmed_cancel")
            # 旧剩余量已确认撤销后才保存并核查新意图。
            return self._submit(new_intent, quotes, **risk_context)

    def reduce(
        self, intent: OrderIntent, quotes: dict[str, Quote], *, authorized: bool = True
    ) -> OrderRecord:
        """独立授权卖出减仓；冻结新增时仍检查当前事实、已占用股数、价格和时间。"""
        # 减仓同样属于唯一执行入口，不允许绕过账号锁。
        with self.store.account_lock():
            # 减仓路径不会自动生成清仓目标。
            return self._submit(intent, quotes, reduce_only=True, authorized=authorized)
