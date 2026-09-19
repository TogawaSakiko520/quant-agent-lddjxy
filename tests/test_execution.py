"""唯一执行入口的故障、恢复、账户锁与独立券商对账验收。"""

import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from quant_core.adapters.calendar import ExchangeCalendar, FixedClock
from quant_core.adapters.fake_broker import FakeBroker
from quant_core.adapters.sqlite_store import SQLiteEventStore
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    CorporateAction,
    DemoConfig,
    FillEvent,
    OrderIntent,
    Quote,
    RiskBlocked,
    SecurityRecord,
    TargetPortfolio,
    TargetPosition,
    seal_record,
)
from quant_core.execution import ExecutionService

# 固定纽约开盘时刻，便于独立确认市场处于交易中。
AT = datetime(2023, 11, 27, 14, 30, tzinfo=UTC)


def initial() -> AccountSnapshot:
    """固定初态为十万美元零持仓；不访问外部状态。"""
    # 可用现金与现金相同，明确模拟即时结算。
    return AccountSnapshot(as_of=AT, cash=Decimal("100000"), available_cash=Decimal("100000"))


def intent(identity: str = "order-A", quantity: int = 10) -> OrderIntent:
    """构造默认十股、每股限价一百美元的买单，允许用例指定稳定身份与数量。"""
    return OrderIntent(
        client_order_id=identity,
        account_id="DEMO",
        decision_id="weekly-decision",
        security_id="A",
        side="BUY",
        quantity=quantity,
        limit_price=Decimal("100"),
        created_at=AT,
        eligible_at=AT,
        reserved_fee=Decimal("1"),
    )


def quote() -> Quote:
    """构造固定时点原始报价事件；不从总回报价格推导成交。"""
    return Quote(security_id="A", at=AT, price=Decimal("100"))


def security() -> SecurityRecord:
    """为测试证券提供决策当时已知的行业分类，用于集中度检查。"""
    return SecurityRecord(
        security_id="A",
        ticker="AAA",
        sector="technology",
        effective_from=date(2020, 1, 1),
        quality="good",
        event_time=AT,
        published_at=AT,
        available_at=AT,
    )


def approved_target(quantity: int = 10, nav: Decimal = Decimal("100000")) -> TargetPortfolio:
    """装配本例的批准目标：希望最终持有 A 的 quantity 股，而非再买 quantity 股。

    nav 是美元净值，权重按固定每股100美元手算。此对象只是风控输入，既不是当前
    账户持仓，也不表示已发订单或成交；没有通过被测规划函数推导授权数量。
    """
    # 目标数量由具体用例事先声明，执行服务不得自行伪造目标。
    position = TargetPosition(
        security_id="A",
        sector="technology",
        weight=float(Decimal(quantity) * Decimal("100") / nav),
        quantity=quantity,
        reason="固定验收批准目标",
    )
    return TargetPortfolio(
        decision_id="weekly-decision",
        as_of=AT,
        nav=nav,
        positions=[position],
        cash_weight=1.0 - position.weight,
    )


def setup(tmp_path: Path) -> tuple[SQLiteEventStore, FakeBroker, ExecutionService]:
    """在 tmp_path 下装配内部库、独立 FakeBroker 与唯一执行服务，按此顺序返回。

    两库都从十万美元空仓开始，但各自持久化账户、订单和事件；service.recover()
    消费券商事件并核对两侧事实。再次使用同一目录只重开已有状态，绝不重置余额。
    时钟固定在 AT；setup 会创建 SQLite 文件，不连接真实券商。
    """
    config = DemoConfig()
    clock = FixedClock(AT)
    store = SQLiteEventStore(tmp_path / "internal.sqlite", initial())
    broker = FakeBroker(
        tmp_path / "broker.sqlite",
        clock,
        config,
        initial(),
        calendar=ExchangeCalendar(date(2023, 1, 1), date(2023, 12, 31)),
    )
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 12, 31))
    return store, broker, ExecutionService(store, broker, clock, config, calendar)


def submit(service: ExecutionService, order: OrderIntent | None = None) -> None:
    """使用完整行业与历史成交量上下文提交合法测试订单；失败由用例观察。"""
    # 默认十股×100美元低于单票上限；用例可通过 order 另行指定订单。
    service.submit(
        order or intent(),
        {"A": quote()},
        security_records=[security()],
        adv={"A": 1000000.0},
        target=approved_target(),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )


def test_accepted_then_timeout_recovers_without_resend(tmp_path: Path) -> None:
    """受理后响应超时必须保存UNKNOWN，重复调用只查回原订单。"""
    store, broker, service = setup(tmp_path)
    # 注入受理后响应丢失，不回滚券商事实。
    broker.timeout_after_accept = True
    result = service.submit(
        intent(),
        {"A": quote()},
        security_records=[security()],
        adv={"A": 1000000.0},
        target=approved_target(),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert result.status == "UNKNOWN"
    assert len(broker.orders()) == 1
    assert len(broker.events()) == 1
    submit(service)
    assert len(broker.events()) == 1
    assert store.orders()[0].status == "OPEN"
    assert service.recover().matched is True


def test_partial_fill_restart_keeps_fee_and_cash_exact(tmp_path: Path) -> None:
    """部分成交后重启，恢复剩余订单并继续成交，最低费用整单仅收一次。"""
    store, broker, service = setup(tmp_path)
    submit(service)
    first = broker.fill("order-A", quote(), 4)
    assert first.fee == Decimal("1")
    # 故意不立即消费事件，模拟内部进程崩溃。
    reopened_store, reopened_broker, reopened_service = setup(tmp_path)
    assert reopened_service.recover().matched is True
    # 首次现金 = 100000 - 4 × 100 - 最低费 1 = 99599 美元。
    assert reopened_store.account(AT).cash == Decimal("99599")
    # 四股加一美元取得费构成总成本401。
    assert reopened_store.account(AT).cost_basis == {"A": Decimal("401")}
    second = reopened_broker.fill("order-A", quote(), 6)
    # 整单每股费低于最低费，不再次收最低一美元。
    assert second.fee == Decimal("0")
    assert reopened_service.recover().matched is True
    # 原实例短连接能读取重启后同一持久化事实。
    # 十股合计花费 1000 美元，整单只收最低费 1 美元，剩余 98999 美元。
    assert store.account(AT).cash == Decimal("98999")
    assert store.orders()[0].status == "FILLED"
    assert store.account(AT).fees == Decimal("1")


def test_cancel_fill_race_keeps_full_fill_authoritative(tmp_path: Path) -> None:
    """撤单请求到确认间完全成交，迟到撤单确认不能撤销真实成交。"""
    store, broker, service = setup(tmp_path)
    submit(service)
    assert service.cancel("order-A").status == "CANCEL_PENDING"
    # 请求在途仍可发生真实成交。
    broker.fill("order-A", quote())
    assert broker.complete_cancel("order-A").status == "FILLED"
    assert service.recover().matched is True
    assert store.account(AT).positions == {"A": 10}
    assert store.orders()[0].status == "FILLED"


def test_replace_waits_for_confirmation_and_accounts_partial_fill(tmp_path: Path) -> None:
    """改单先撤后等确认，部分成交后的新单通过完整风险而非覆盖旧意图。"""
    store, broker, service = setup(tmp_path)
    submit(service)
    broker.fill("order-A", quote(), 4)
    # 批准目标仍是最终10股；原单已成交4股，替换的是未成交的6股，不能再买10股。
    # 替代意图只包含预期剩余六股。
    replacement = intent("replacement-A", 6)
    # 第一次改单只能发撤单请求并阻断新单。
    with pytest.raises(RiskBlocked, match="confirmed_cancel"):
        service.replace(
            "order-A",
            replacement,
            {"A": quote()},
            security_records=[security()],
            adv={"A": 1000000.0},
            target=approved_target(),
            reference_nav=Decimal("100000"),
            peak_nav=Decimal("100000"),
        )
    assert len(broker.orders()) == 1
    assert len(store.orders()) == 1
    broker.complete_cancel("order-A")
    changed = service.replace(
        "order-A",
        replacement,
        {"A": quote()},
        security_records=[security()],
        adv={"A": 1000000.0},
        target=approved_target(),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    assert changed.status == "OPEN"
    assert store.account(AT).positions == {"A": 4}
    assert len(store.orders()) == 2


def test_unknown_orphan_intent_never_resubmits(tmp_path: Path) -> None:
    """进程可能在发送前后崩溃，券商查不到的旧PERSISTED也不盲目补发。"""
    store, broker, service = setup(tmp_path)
    # 模拟持久化成功后崩溃，不能证明请求未曾发出。
    store.save_intent(intent())
    submit(service)
    assert broker.orders() == []
    assert store.orders()[0].status == "UNKNOWN"
    assert service.recover().matched is False


def test_independent_broker_cash_discrepancy_blocks_new_risk(tmp_path: Path) -> None:
    """只修改券商现金事实，内部不能反向抄数制造一致，新单必须拒绝。"""
    store, broker, service = setup(tmp_path)
    changed = broker.account().model_copy(
        update={"cash": Decimal("99990"), "available_cash": Decimal("99990")}
    )
    # 直接改券商数据库模拟接口返回额外费用但缺少对应事件。
    connection = sqlite3.connect(broker.path)
    with connection:
        connection.execute(
            "UPDATE broker_state SET payload=? WHERE key='account'", (changed.model_dump_json(),)
        )
    connection.close()
    result = service.recover()
    assert result.matched is False
    assert "account_mismatch:cash" in result.differences
    assert store.account(AT).cash == Decimal("100000")
    # 对账不清即使金额很小也不能新增风险。
    with pytest.raises(RiskBlocked, match="unreconciled_account"):
        submit(service)
    assert broker.orders() == []


def test_manual_fill_is_booked_and_freezes_only_new_risk(tmp_path: Path) -> None:
    """人工成交作为事实入账并冻结新增，撤单与有授权减仓仍走各自硬约束。"""
    store, broker, service = setup(tmp_path)
    submit(service)
    # 外部人工买入两股，同账号但没有内部策略意图。
    manual = FillEvent(
        event_id="manual-event",
        fill_id="manual-fill",
        account_id="DEMO",
        client_order_id="MANUAL",
        broker_order_id="MANUAL-1",
        security_id="A",
        side="BUY",
        quantity=2,
        price=Decimal("100"),
        fee=Decimal("1"),
        at=AT,
    )
    broker.manual_fill(manual)
    assert service.recover().matched is False
    assert store.account(AT).positions == {"A": 2}
    assert service.cancel("order-A").status == "CANCEL_PENDING"
    broker.complete_cancel("order-A")
    # model_copy 仅组装卖出一股的变体；减仓数量来自已确认的2股，不借用仍未成交的买单。
    reduce_intent = intent("reduce-A", 1).model_copy(update={"side": "SELL"})
    with pytest.raises(RiskBlocked, match="unauthorized_operation"):
        # 另一个身份避免本地拒绝重试改变语义。
        service.reduce(
            reduce_intent.model_copy(update={"client_order_id": "unauthorized"}),
            {"A": quote()},
            authorized=False,
        )
    assert service.reduce(reduce_intent, {"A": quote()}, authorized=True).status == "OPEN"
    # 初始人工现金流仍是两百零一美元。
    assert store.account(AT).cash == Decimal("99799")
    assert "MANUAL_TRADE:MANUAL" in store.frozen()


def test_company_actions_require_both_independent_facts(tmp_path: Path) -> None:
    """外部拆股先到产生真实差异，导入同一封印行动后一致，分红按固定权益数量。"""
    store, broker, service = setup(tmp_path)
    submit(service)
    broker.fill("order-A", quote())
    assert service.recover().matched is True
    split = seal_record(
        CorporateAction(
            quality="good",
            action_id="split-A",
            security_id="A",
            kind="split",
            ratio=Decimal("2"),
            event_time=AT,
            published_at=AT,
            available_at=AT,
        )
    )
    broker.apply_action(split)
    assert service.recover().matched is False
    store.apply_action(split, AT)
    assert service.recover().matched is True
    assert store.account(AT).cost_basis == {"A": Decimal("1001")}
    # 支付日二十股，但权益记录固定为十股。
    dividend = seal_record(
        CorporateAction(
            quality="good",
            action_id="div-A",
            security_id="A",
            kind="dividend",
            cash_per_share=Decimal("0.5"),
            entitlement_quantity=10,
            event_time=AT,
            published_at=AT,
            available_at=AT,
        )
    )
    broker.apply_action(dividend)
    store.apply_action(dividend, AT)
    # 现金 = 100000 - 10 × 100 - 1 + 权益 10 股 × 0.5 = 99004 美元。
    assert broker.account().cash == Decimal("99004")
    assert service.recover().matched is True


def test_cross_process_account_lock_ignores_database_directory(tmp_path: Path) -> None:
    """不同工作目录/数据库中的同一账号在另一进程也不能同时成为执行者。"""
    store = SQLiteEventStore(tmp_path / "run-one" / "internal.sqlite", initial())
    # tmp_path 中的两份库只含测试账户；子进程确实创建第二库并竞争同机文件锁。
    # 下面字符串是原样执行的 Python 程序，不能作为普通注释块整理或改写。
    # 子进程用第二个目录重新创建同账号适配器。
    code = """
# 子进程读取明确参数而非环境密钥。
import sys
# 使用相同公开适配器验证操作系统排他锁。
from pathlib import Path
# 初始账户经公共契约反序列化。
from quant_core.contracts import AccountSnapshot, RiskBlocked
# 第二运行目录不应产生第二个账号锁。
from quant_core.adapters.sqlite_store import SQLiteEventStore
# 从父进程只接收测试路径与合成账户。
store = SQLiteEventStore(Path(sys.argv[1]), AccountSnapshot.model_validate_json(sys.argv[2]))
# 应观察到另一个进程持锁。
try:
    # 尝试同账号账户级写入锁。
    with store.account_lock():
        # 意外获得锁代表隔离失败。
        raise RuntimeError('second executor unexpectedly acquired lock')
# 明确锁冲突即为此子进程预期。
except RiskBlocked:
    # 固定输出供父进程断言。
    print('blocked')
"""
    # 父进程先持有统一账号锁。
    with store.account_lock():
        # 使用同解释器启动真实新进程，cwd也切到不同位置。
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(tmp_path / "run-two" / "internal.sqlite"),
                initial().model_dump_json(),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"
    # 上下文退出后同账号锁必须被正常释放。
    with store.account_lock():
        assert store.account(AT).cash == Decimal("100000")


def test_disconnect_and_rejected_orders_have_distinct_states(tmp_path: Path) -> None:
    """断线阻断账户对账，明确券商拒单保留终态；两种失败不得混淆。"""
    store, broker, service = setup(tmp_path)
    broker.disconnect()
    with pytest.raises(RiskBlocked, match="unreconciled_account"):
        submit(service, intent("offline"))
    # 本地确定没有发送，因此REJECTED且没有券商身份。
    assert store.orders()[0].status == "REJECTED"
    # 重连只恢复查询，不补发先前被拒订单。
    broker.reconnect()
    broker.reject_next = True
    submit(service, intent("rejected"))
    assert broker.orders()[0].status == "REJECTED"
    assert service.recover().matched is True


def test_fake_broker_rejects_outside_limit_and_duplicate_intent(tmp_path: Path) -> None:
    """模拟成交只使用可执行的注入限价报价，相同意图身份不可改价。"""
    store, broker, service = setup(tmp_path)
    submit(service)
    # 101报价不能穿越买入最高限价。
    with pytest.raises(ContractError, match="限价"):
        broker.fill("order-A", quote().model_copy(update={"price": Decimal("101")}))
    # 相同客户端ID修改价格必须报冲突并冻结。
    with pytest.raises(ContractError, match="意图 ID 内容冲突"):
        submit(service, intent().model_copy(update={"limit_price": Decimal("99")}))
    assert store.account(AT).cash == Decimal("100000")
    assert store.frozen()


def test_fake_broker_rejects_after_close_even_with_fresh_quote(tmp_path: Path) -> None:
    """盘后报价即使零年龄且已满足eligible_at也不能产生常规时段成交。"""
    store, broker, service = setup(tmp_path)
    submit(service)
    # 明确当天收盘后一分的UTC时刻。
    after_close = datetime(2023, 11, 27, 21, 1, tzinfo=UTC)
    # 用相同独立券商库和未来注入时钟重开，保留旧单。
    later = FakeBroker(
        broker.path,
        FixedClock(after_close),
        DemoConfig(),
        initial(),
        calendar=ExchangeCalendar(date(2023, 1, 1), date(2023, 12, 31)),
    )
    with pytest.raises(ContractError, match="交易时段"):
        later.fill("order-A", quote().model_copy(update={"at": after_close}))
    assert later.account().positions == {}
    assert store.account(AT).cash == Decimal("100000")


@pytest.mark.parametrize("future_field", ["event_time", "available_at"])
def test_fake_broker_rejects_future_action_or_unavailable_fact(
    tmp_path: Path, future_field: str
) -> None:
    """未来发生或未来才可用的公司行动都不能在现在改变独立券商现金。"""
    store, broker, service = setup(tmp_path)
    dividend = CorporateAction(
        quality="good",
        action_id="future-dividend",
        security_id="A",
        kind="dividend",
        cash_per_share=Decimal("1"),
        entitlement_quantity=10,
        event_time=AT,
        published_at=AT,
        available_at=AT,
    )
    # 单独把经济发生时间或信息可用时间移到七天以后。
    future = seal_record(
        dividend.model_copy(update={future_field: datetime(2023, 12, 4, 14, 30, tzinfo=UTC)})
    )
    # 合法哈希不能替代处理时间检查。
    with pytest.raises(ContractError, match="尚未发生|尚不可用"):
        broker.apply_action(future)
    assert broker.account().cash == Decimal("100000")
    assert service.recover().matched is True
    assert store.account(AT).cash == Decimal("100000")


def test_actual_sell_turnover_cannot_be_understated_by_limit_or_caller_zero(tmp_path: Path) -> None:
    """卖单限价是下界，实际成交额与外部显式0冲突时必须采用真实较大换手。"""
    # 初始五万美元现金加一千股百美元估值，总净值十五万美元。
    account = AccountSnapshot(
        as_of=AT,
        cash=Decimal("50000"),
        available_cash=Decimal("50000"),
        positions={"A": 1000},
        cost_basis={"A": Decimal("100000")},
    )
    # 正式默认换手上限仍为净值一倍，测试不放松风险。
    config = DemoConfig()
    clock = FixedClock(AT)
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 12, 31))
    store = SQLiteEventStore(tmp_path / "internal.sqlite", account)
    broker = FakeBroker(tmp_path / "broker.sqlite", clock, config, account, calendar=calendar)
    service = ExecutionService(store, broker, clock, config, calendar)
    # 千股卖单限价100，费用预算必须覆盖每股0.005共五美元。
    sale = intent("large-sale", 1000).model_copy(
        update={"side": "SELL", "reserved_fee": Decimal("5")}
    )
    # 单轮换手分母固定十五万美元。
    service.submit(
        sale,
        {"A": quote()},
        security_records=[security()],
        adv={"A": 1000000.0},
        target=approved_target(1, Decimal("150000")),
        reference_nav=Decimal("150000"),
        peak_nav=Decimal("150000"),
    )
    # 实际以150美元卖出，现金成交额十五万美元而非限价估计十万美元。
    broker.fill("large-sale", quote().model_copy(update={"price": Decimal("150")}))
    # 此时只更新了券商事实；下面 submit 会先恢复卖出事件到内账，再检查额外买单。
    # 因此买单即使被拒绝，已恢复的卖出现金和实际换手仍保留，不随拒单回滚。
    # 故意给外部累计换手传0，也不能覆盖内部真实十五万美元。
    with pytest.raises(RiskBlocked, match="turnover_limit"):
        # 额外一股100美元买单会超过已消耗完整换手预算。
        service.submit(
            intent("extra-buy", 1),
            {"A": quote()},
            security_records=[security()],
            adv={"A": 1000000.0},
            target=approved_target(1, Decimal("150000")),
            reference_nav=Decimal("150000"),
            peak_nav=Decimal("150000"),
            turnover_used=Decimal("0"),
        )
    assert len(broker.orders()) == 1
    # 实际卖出现金150000减费5加初始50000，直接写独立期望。
    assert store.account(AT).cash == Decimal("199995")


def test_fake_manual_fill_cannot_arrive_from_future(tmp_path: Path) -> None:
    """人工成交入口不应成为绕过业务时钟的未来持仓生成器。"""
    store, broker, service = setup(tmp_path)
    # 构造七天以后才发生的外部人工成交。
    future = FillEvent(
        event_id="future",
        fill_id="future",
        account_id="DEMO",
        client_order_id="manual",
        broker_order_id="manual",
        security_id="A",
        side="BUY",
        quantity=1,
        price=Decimal("100"),
        fee=Decimal("1"),
        at=datetime(2023, 12, 4, 14, 30, tzinfo=UTC),
    )
    with pytest.raises(ContractError, match="晚于当前"):
        broker.manual_fill(future)
    assert broker.account().positions == {}
    assert service.recover().matched is True
    assert store.account(AT).cash == Decimal("100000")


@pytest.mark.parametrize("missing", ["target", "reference_nav", "peak_nav"])
def test_missing_approved_context_cannot_reach_broker(tmp_path: Path, missing: str) -> None:
    """省略批准目标或任一损失基线都必须拒绝，服务不得自动构造上下文补齐授权。"""
    # context 是传给 service.submit 的风险关键字参数：目标数量、美元损失基线、
    # 主表资格及 ADV（日均成交股数）；删除一项并不修改实际账户或行情。
    # 正常初始账户和市场信息充分，唯一故障是明确删掉必需上下文。
    store, broker, service = setup(tmp_path)
    context: dict[str, Any] = {
        "target": approved_target(),
        "reference_nav": Decimal("100000"),
        "peak_nav": Decimal("100000"),
        "security_records": [security()],
        "adv": {"A": 1000000.0},
    }
    # 每个参数化用例只缺一项，确保诊断指向真实缺口。
    context.pop(missing)
    with pytest.raises(RiskBlocked, match="missing_"):
        service.submit(intent(), {"A": quote()}, **context)
    assert broker.orders() == []
    assert store.orders()[0].status == "REJECTED"
