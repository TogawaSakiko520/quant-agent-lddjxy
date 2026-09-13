"""唯一执行入口的故障、恢复、账户锁与独立券商对账验收。"""

# 直接注入数据库差异模拟独立券商现金事实。
import sqlite3

# 真正启动另一个进程验证账户文件锁。
import subprocess

# 子进程沿用当前已安装依赖的解释器。
import sys

# 固定 UTC 和日期，不依赖系统当前时间。
from datetime import UTC, date, datetime

# 手写十进制资金期望。
from decimal import Decimal

# 临时文件路径由pytest隔离。
from pathlib import Path

# 参数化缺失上下文测试在明确边界组装关键字参数。
from typing import Any

# 测试框架提供参数化与异常断言。
import pytest

# 正式日历与固定时钟用于离线测试装配。
from quant_core.adapters.calendar import ExchangeCalendar, FixedClock

# 券商状态源独立于内部事件库。
from quant_core.adapters.fake_broker import FakeBroker

# 内部唯一账务写入适配器。
from quant_core.adapters.sqlite_store import SQLiteEventStore

# 测试输入全部经过统一契约。
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

# 所有正常订单由唯一服务入口生成副作用。
from quant_core.execution import ExecutionService

# 固定纽约开盘时刻，便于独立确认市场处于交易中。
AT = datetime(2023, 11, 27, 14, 30, tzinfo=UTC)


def initial() -> AccountSnapshot:
    """固定初态为十万美元零持仓；不访问外部状态。"""
    # 可用现金与现金相同，明确模拟即时结算。
    return AccountSnapshot(as_of=AT, cash=Decimal("100000"), available_cash=Decimal("100000"))


def intent(identity: str = "order-A", quantity: int = 10) -> OrderIntent:
    """构造默认十股百美元限价买单；返回固定合法意图，无副作用。"""
    # 幂等身份由调用用例明确指定，不随机生成。
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
    # 执行价必须显式注入。
    return Quote(security_id="A", at=AT, price=Decimal("100"))


def security() -> SecurityRecord:
    """构造已知行业分类以满足新增风险集中度核验；无副作用。"""
    # 证券主表覆盖测试决策日期。
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
    """提供用例明确批准的单证券整股目标及固定净值；只构造测试事实，无副作用。"""
    # 目标数量由具体用例事先声明，执行服务不得自行伪造目标。
    position = TargetPosition(
        security_id="A",
        sector="technology",
        weight=float(Decimal(quantity) * Decimal("100") / nav),
        quantity=quantity,
        reason="固定验收批准目标",
    )
    # 本轮价格仅为独立固定测试基准，不承诺真实未来成交。
    return TargetPortfolio(
        decision_id="weekly-decision",
        as_of=AT,
        nav=nav,
        positions=[position],
        cash_weight=1.0 - position.weight,
    )


def setup(tmp_path: Path) -> tuple[SQLiteEventStore, FakeBroker, ExecutionService]:
    """装配两个独立数据库和统一时钟；返回存储、券商、服务，有本地建库副作用。"""
    # 所有配置保持默认已声明风险预算。
    config = DemoConfig()
    # 测试时钟固定在真实合格时段。
    clock = FixedClock(AT)
    # 内部状态与券商状态必须是两个文件。
    store = SQLiteEventStore(tmp_path / "internal.sqlite", initial())
    # 券商只接收独立初态，不接收内部ledger对象。
    broker = FakeBroker(
        tmp_path / "broker.sqlite",
        clock,
        config,
        initial(),
        calendar=ExchangeCalendar(date(2023, 1, 1), date(2023, 12, 31)),
    )
    # 日历范围固定，不用工作日近似。
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 12, 31))
    # 唯一执行服务依赖契约接口。
    return store, broker, ExecutionService(store, broker, clock, config, calendar)


def submit(service: ExecutionService, order: OrderIntent | None = None) -> None:
    """使用完整行业与历史成交量上下文提交合法测试订单；失败由用例观察。"""
    # 每单一千美元低于默认单票五千美元上限。
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
    # 装配独立状态源。
    store, broker, service = setup(tmp_path)
    # 注入受理后响应丢失，不回滚券商事实。
    broker.timeout_after_accept = True
    # 第一次执行返回未知，不是拒单。
    result = service.submit(
        intent(),
        {"A": quote()},
        security_records=[security()],
        adv={"A": 1000000.0},
        target=approved_target(),
        reference_nav=Decimal("100000"),
        peak_nav=Decimal("100000"),
    )
    # 内部必须保留不确定生命周期。
    assert result.status == "UNKNOWN"
    # 券商独立事实已经有一张受理单。
    assert len(broker.orders()) == 1
    # 记录目前事件数量验证后续没有再次受理。
    assert len(broker.events()) == 1
    # 使用相同意图再次调用入口只能恢复。
    submit(service)
    # 原始券商订单与事件数量保持一份。
    assert len(broker.events()) == 1
    # 内部恢复真实券商身份与OPEN状态。
    assert store.orders()[0].status == "OPEN"
    # 完整独立对账通过。
    assert service.recover().matched is True


def test_partial_fill_restart_keeps_fee_and_cash_exact(tmp_path: Path) -> None:
    """部分成交后重启，恢复剩余订单并继续成交，最低费用整单仅收一次。"""
    # 独立账户初态无持仓。
    store, broker, service = setup(tmp_path)
    # 正常入口创建一张十股单。
    submit(service)
    # 第一笔只成交四股。
    first = broker.fill("order-A", quote(), 4)
    # 最低一美元费用首次成交确认。
    assert first.fee == Decimal("1")
    # 故意不立即消费事件，模拟内部进程崩溃。
    reopened_store, reopened_broker, reopened_service = setup(tmp_path)
    # 新服务从独立券商事件恢复部分成交。
    assert reopened_service.recover().matched is True
    # 持仓现金期望直接由四股成交手算。
    assert reopened_store.account(AT).cash == Decimal("99599")
    # 四股加一美元取得费构成总成本401。
    assert reopened_store.account(AT).cost_basis == {"A": Decimal("401")}
    # 剩余六股独立成交。
    second = reopened_broker.fill("order-A", quote(), 6)
    # 整单每股费低于最低费，不再次收最低一美元。
    assert second.fee == Decimal("0")
    # 再次恢复导入唯一新成交。
    assert reopened_service.recover().matched is True
    # 原实例短连接能读取重启后同一持久化事实。
    assert store.account(AT).cash == Decimal("98999")
    # 订单完全成交。
    assert store.orders()[0].status == "FILLED"
    # 累计费用保持一美元。
    assert store.account(AT).fees == Decimal("1")


def test_cancel_fill_race_keeps_full_fill_authoritative(tmp_path: Path) -> None:
    """撤单请求到确认间完全成交，迟到撤单确认不能撤销真实成交。"""
    # 装配订单链路。
    store, broker, service = setup(tmp_path)
    # 先创建受理委托。
    submit(service)
    # 撤单只返回请求中。
    assert service.cancel("order-A").status == "CANCEL_PENDING"
    # 请求在途仍可发生真实成交。
    broker.fill("order-A", quote())
    # 迟到撤单确认应返回完全成交。
    assert broker.complete_cancel("order-A").status == "FILLED"
    # 服务恢复两条状态通道和成交事实。
    assert service.recover().matched is True
    # 最终账务不能因撤单请求消失。
    assert store.account(AT).positions == {"A": 10}
    # 最终订单保持完全成交。
    assert store.orders()[0].status == "FILLED"


def test_replace_waits_for_confirmation_and_accounts_partial_fill(tmp_path: Path) -> None:
    """改单先撤后等确认，部分成交后的新单通过完整风险而非覆盖旧意图。"""
    # 建立原始订单。
    store, broker, service = setup(tmp_path)
    # 原单委托十股。
    submit(service)
    # 撤单前已有四股成交。
    broker.fill("order-A", quote(), 4)
    # 替代意图只包含预期剩余六股。
    replacement = intent("replacement-A", 6)
    # 第一次改单只能发撤单请求并阻断新单。
    with pytest.raises(RiskBlocked, match="confirmed_cancel"):
        # 新单的完整流动性与行业参数不能绕过撤单确认。
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
    # 外部仍只有原单。
    assert len(broker.orders()) == 1
    # 新意图尚未保存，不能误认为已发送。
    assert len(store.orders()) == 1
    # 券商明确撤销剩余数量。
    broker.complete_cancel("order-A")
    # 第二次才能提交具有新身份的委托。
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
    # 新单正常受理。
    assert changed.status == "OPEN"
    # 原有四股真实持仓仍保留。
    assert store.account(AT).positions == {"A": 4}
    # 意图从不原地更换内容。
    assert len(store.orders()) == 2


def test_unknown_orphan_intent_never_resubmits(tmp_path: Path) -> None:
    """进程可能在发送前后崩溃，券商查不到的旧PERSISTED也不盲目补发。"""
    # 状态和外部账户各自初始化。
    store, broker, service = setup(tmp_path)
    # 模拟持久化成功后崩溃，不能证明请求未曾发出。
    store.save_intent(intent())
    # 同意图恢复调用不能创建新的券商订单。
    submit(service)
    # 不以查不到作为重发许可证。
    assert broker.orders() == []
    # 不确定状态跨重启保存。
    assert store.orders()[0].status == "UNKNOWN"
    # 对账明确失败，禁止后续新增。
    assert service.recover().matched is False


def test_independent_broker_cash_discrepancy_blocks_new_risk(tmp_path: Path) -> None:
    """只修改券商现金事实，内部不能反向抄数制造一致，新单必须拒绝。"""
    # 两个数据库各自保存十万美元。
    store, broker, service = setup(tmp_path)
    # 独立读取券商账户用于制造外部未解释现金修订。
    changed = broker.account().model_copy(
        update={"cash": Decimal("99990"), "available_cash": Decimal("99990")}
    )
    # 直接改券商数据库模拟接口返回额外费用但缺少对应事件。
    connection = sqlite3.connect(broker.path)
    # 更新只发生在外部独立状态源。
    with connection:
        # 不向内部事件库写入任何修正。
        connection.execute(
            "UPDATE broker_state SET payload=? WHERE key='account'", (changed.model_dump_json(),)
        )
    # 关闭独立数据库连接。
    connection.close()
    # 恢复必须报告现金差异。
    result = service.recover()
    # 差异不是自动成功。
    assert result.matched is False
    # 精确定位现金字段。
    assert "account_mismatch:cash" in result.differences
    # 内部保留原事实，不抄外部余额。
    assert store.account(AT).cash == Decimal("100000")
    # 对账不清即使金额很小也不能新增风险。
    with pytest.raises(RiskBlocked, match="unreconciled_account"):
        # 普通合法订单仍须经过对账闸门。
        submit(service)
    # 没有任何新券商委托。
    assert broker.orders() == []


def test_manual_fill_is_booked_and_freezes_only_new_risk(tmp_path: Path) -> None:
    """人工成交作为事实入账并冻结新增，撤单与有授权减仓仍走各自硬约束。"""
    # 创建正常可追溯订单。
    store, broker, service = setup(tmp_path)
    # 正常订单保持开放以验证独立撤单路径。
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
    # 人工成交只先改变券商事实。
    broker.manual_fill(manual)
    # 恢复导入真实事件，同时保持待解释冻结。
    assert service.recover().matched is False
    # 人工持仓必须如实入账，不忽略外部交易。
    assert store.account(AT).positions == {"A": 2}
    # 原已知委托仍能请求撤单，不强制扩大风险。
    assert service.cancel("order-A").status == "CANCEL_PENDING"
    # 显式确认撤单后再测试减仓。
    broker.complete_cancel("order-A")
    # 构造只卖出一股的明确减仓意图。
    reduce_intent = intent("reduce-A", 1).model_copy(update={"side": "SELL"})
    # 没有授权的减仓必须拒绝。
    with pytest.raises(RiskBlocked, match="unauthorized_operation"):
        # 另一个身份避免本地拒绝重试改变语义。
        service.reduce(
            reduce_intent.model_copy(update={"client_order_id": "unauthorized"}),
            {"A": quote()},
            authorized=False,
        )
    # 有授权且可卖数量明确时减仓可独立通过。
    assert service.reduce(reduce_intent, {"A": quote()}, authorized=True).status == "OPEN"
    # 初始人工现金流仍是两百零一美元。
    assert store.account(AT).cash == Decimal("99799")
    # 永久标记没有被自动清除。
    assert "MANUAL_TRADE:MANUAL" in store.frozen()


def test_company_actions_require_both_independent_facts(tmp_path: Path) -> None:
    """外部拆股先到产生真实差异，导入同一封印行动后一致，分红按固定权益数量。"""
    # 买入十股形成可验证公司行动持仓。
    store, broker, service = setup(tmp_path)
    # 创建并全部成交正常订单。
    submit(service)
    # 成交事实先进入券商独立账户。
    broker.fill("order-A", quote())
    # 恢复正常初始持仓。
    assert service.recover().matched is True
    # 固定二比一拆股行动。
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
    # 外部权益发生变化，内部不会因为报告复制数量。
    broker.apply_action(split)
    # 缺少内部公司行动导入时必须不一致。
    assert service.recover().matched is False
    # 从明确封印事实导入内部账务。
    store.apply_action(split, AT)
    # 同一行动在两边独立计算后对齐。
    assert service.recover().matched is True
    # 总成本保持原先一千零一美元。
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
    # 两侧分别消费同一真实事实。
    broker.apply_action(dividend)
    # 内部唯一账务写入者应用明确行动。
    store.apply_action(dividend, AT)
    # 独立余额统一增加五美元。
    assert broker.account().cash == Decimal("99004")
    # 完整对账核实成本、费用、现金和数量。
    assert service.recover().matched is True


def test_cross_process_account_lock_ignores_database_directory(tmp_path: Path) -> None:
    """不同工作目录/数据库中的同一账号在另一进程也不能同时成为执行者。"""
    # 当前进程在第一个运行目录创建账本。
    store = SQLiteEventStore(tmp_path / "run-one" / "internal.sqlite", initial())
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
    # 子进程不能靠换目录绕过排他。
    assert result.returncode == 0, result.stderr
    # 明确收到被阻止结果。
    assert result.stdout.strip() == "blocked"
    # 上下文退出后同账号锁必须被正常释放。
    with store.account_lock():
        # 无异常即说明可恢复后续正常执行。
        assert store.account(AT).cash == Decimal("100000")


def test_disconnect_and_rejected_orders_have_distinct_states(tmp_path: Path) -> None:
    """断线阻断账户对账，明确券商拒单保留终态；两种失败不得混淆。"""
    # 先创建独立账户状态。
    store, broker, service = setup(tmp_path)
    # 注入恢复前的网络不可用。
    broker.disconnect()
    # 无法对账不能发送新委托。
    with pytest.raises(RiskBlocked, match="unreconciled_account"):
        # 订单在风险失败前仍有本地意图审计。
        submit(service, intent("offline"))
    # 本地确定没有发送，因此REJECTED且没有券商身份。
    assert store.orders()[0].status == "REJECTED"
    # 重连只恢复查询，不补发先前被拒订单。
    broker.reconnect()
    # 下一笔注入明确券商拒绝。
    broker.reject_next = True
    # 新身份通过完整风控后获得明确拒单回执。
    submit(service, intent("rejected"))
    # 券商只有这张明确拒绝的单。
    assert broker.orders()[0].status == "REJECTED"
    # 对账能够如实匹配拒单事实。
    assert service.recover().matched is True


def test_fake_broker_rejects_outside_limit_and_duplicate_intent(tmp_path: Path) -> None:
    """模拟成交只使用可执行的注入限价报价，相同意图身份不可改价。"""
    # 受控入口正常创建限价100的买单。
    store, broker, service = setup(tmp_path)
    # 资金和风控正常。
    submit(service)
    # 101报价不能穿越买入最高限价。
    with pytest.raises(ContractError, match="限价"):
        # 故障驱动不自动使用100补造成交。
        broker.fill("order-A", quote().model_copy(update={"price": Decimal("101")}))
    # 相同客户端ID修改价格必须报冲突并冻结。
    with pytest.raises(ContractError, match="意图 ID 内容冲突"):
        # 执行入口检查幂等内容在任何外部发送之前。
        submit(service, intent().model_copy(update={"limit_price": Decimal("99")}))
    # 未发生任何成交，现金仍保持初始值。
    assert store.account(AT).cash == Decimal("100000")
    # 冲突留下不可自动解除的新增冻结。
    assert store.frozen()


def test_fake_broker_rejects_after_close_even_with_fresh_quote(tmp_path: Path) -> None:
    """盘后报价即使零年龄且已满足eligible_at也不能产生常规时段成交。"""
    # 先在常规开盘时段创建已受理订单。
    store, broker, service = setup(tmp_path)
    # 完整风控通过的订单不意味着可以任意时间成交。
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
    # 新鲜原始报价也不能绕过注入日历。
    with pytest.raises(ContractError, match="交易时段"):
        # 不根据未来假设自动推迟或补生成交。
        later.fill("order-A", quote().model_copy(update={"at": after_close}))
    # 券商持仓保持未成交。
    assert later.account().positions == {}
    # 内部财务事实同样未被改变。
    assert store.account(AT).cash == Decimal("100000")


@pytest.mark.parametrize("future_field", ["event_time", "available_at"])
def test_fake_broker_rejects_future_action_or_unavailable_fact(
    tmp_path: Path, future_field: str
) -> None:
    """未来发生或未来才可用的公司行动都不能在现在改变独立券商现金。"""
    # 两个账户初态相同但状态源独立。
    store, broker, service = setup(tmp_path)
    # 构造当前可用的合法固定权益分红。
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
        # 券商必须使用注入时钟而非直接兑现未来股息。
        broker.apply_action(future)
    # 外部现金没有因为失败变化。
    assert broker.account().cash == Decimal("100000")
    # 内外部状态依然一致。
    assert service.recover().matched is True
    # 原始内部现金也保持独立。
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
    # 所有交易在同一明确UTC常规时刻。
    clock = FixedClock(AT)
    # 同一固定交易日历供执行与模拟券商使用。
    calendar = ExchangeCalendar(date(2023, 1, 1), date(2023, 12, 31))
    # 内部和券商数据库分别保存相同明确初态。
    store = SQLiteEventStore(tmp_path / "internal.sqlite", account)
    # 独立券商账户不来源于内部账本。
    broker = FakeBroker(tmp_path / "broker.sqlite", clock, config, account, calendar=calendar)
    # 注入完整业务边界。
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
    # 券商仍只有原始卖单，没有新买单。
    assert len(broker.orders()) == 1
    # 实际卖出现金150000减费5加初始50000，直接写独立期望。
    assert store.account(AT).cash == Decimal("199995")


def test_fake_manual_fill_cannot_arrive_from_future(tmp_path: Path) -> None:
    """人工成交入口不应成为绕过业务时钟的未来持仓生成器。"""
    # 独立状态源处于固定当前时刻。
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
    # 时间边界必须在独立券商财务写入前检查。
    with pytest.raises(ContractError, match="晚于当前"):
        # 未来事件不能提前消费现金。
        broker.manual_fill(future)
    # 独立账户仍为空仓。
    assert broker.account().positions == {}
    # 内部恢复不会凭空发现未来成交。
    assert service.recover().matched is True
    # 内部现金没有变化。
    assert store.account(AT).cash == Decimal("100000")


@pytest.mark.parametrize("missing", ["target", "reference_nav", "peak_nav"])
def test_missing_approved_context_cannot_reach_broker(tmp_path: Path, missing: str) -> None:
    """省略批准目标或任一损失基线都必须拒绝，服务不得自动构造上下文补齐授权。"""
    # 正常初始账户和市场信息充分，唯一故障是明确删掉必需上下文。
    store, broker, service = setup(tmp_path)
    # 合法上下文来自测试事先定义的目标与净值。
    context: dict[str, Any] = {
        "target": approved_target(),
        "reference_nav": Decimal("100000"),
        "peak_nav": Decimal("100000"),
        "security_records": [security()],
        "adv": {"A": 1000000.0},
    }
    # 每个参数化用例只缺一项，确保诊断指向真实缺口。
    context.pop(missing)
    # 省略上下文不能被默认值解释为通过批准。
    with pytest.raises(RiskBlocked, match="missing_"):
        # 不在测试或ExecutionService里补造缺失目标/基线。
        service.submit(intent(), {"A": quote()}, **context)
    # 任何正常风险缺失都不能产生券商订单。
    assert broker.orders() == []
    # 先保存的意图仍保留明确本地拒绝作为审计事实。
    assert store.orders()[0].status == "REJECTED"
