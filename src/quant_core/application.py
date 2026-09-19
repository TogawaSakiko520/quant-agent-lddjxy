"""离线应用编排；连接纯业务和显式适配器，保存全部证据，不持有真实交易权限。

CLI 将配置和目录交给本模块；本模块把行情→因子→评分→目标→订单→账户串起来。
演示/回放生成新运行，校验只读原产物并借临时库重算，研究另存实验，不把报告当交易事实。
"""

import json
import platform
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from quant_core.adapters.calendar import ExchangeCalendar, FixedClock
from quant_core.adapters.datasets import fixture_quotes, generate_fixture
from quant_core.adapters.fake_broker import FakeBroker
from quant_core.adapters.sqlite_store import SQLiteEventStore
from quant_core.adapters.storage import (
    hash_file,
    read_json,
    read_market_parquet,
    read_sqlite_state,
    validate_artifacts,
    write_json,
    write_market_parquet,
)
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    CorporateAction,
    DataSnapshot,
    DemoConfig,
    FactorValue,
    FillEvent,
    JournalEntry,
    OrderEvent,
    OrderIntent,
    OrderRecord,
    ReconciliationResult,
    RegimeAssessment,
    RiskBlocked,
    RunManifest,
    SecurityRecord,
    SignalSet,
    TargetPortfolio,
    canonical_hash,
    equivalent_values,
)
from quant_core.data import build_snapshot
from quant_core.execution import ExecutionService
from quant_core.factors import calculate_factors
from quant_core.monitoring import Alert, assess_health
from quant_core.portfolio import build_portfolio, plan_orders
from quant_core.regime import assess_regime
from quant_core.reporting import render_report
from quant_core.research import evaluate_research, label_observations, rolling_splits
from quant_core.risk import assess_order
from quant_core.signals import score_factors

if TYPE_CHECKING:
    from quant_core.adapters.alpaca import AlpacaSDKTransport
    from quant_core.adapters.alpaca_data import AlpacaCalendar
    from quant_core.contracts import (
        Clock,
        PaperConfig,
        PaperQueueTestContext,
        QueuePreflightRejectionProof,
        Quote,
    )

# 完整运行必须具有这些事实文件，空哈希清单不能被当成成功校验。
REQUIRED_ARTIFACTS = frozenset(
    {
        "inputs/market.parquet",
        "inputs/securities.json",
        "inputs/execution_quotes.json",
        "snapshot.json",
        "factors.json",
        "signals.json",
        "regime.json",
        "target.json",
        "risk.json",
        "orders.json",
        "events.json",
        "journal.json",
        "account.json",
        "reconciliation.json",
        "alerts.jsonl",
        "report.md",
        "internal.sqlite",
        "broker.sqlite",
    }
)


def make_calendar(config: DemoConfig) -> ExchangeCalendar:
    """为样本、下一交易时段与五日收益标签构建覆盖前后缓冲的固定日历。"""
    # 后续执行和五交易日标签都需要显式日历覆盖。
    return ExchangeCalendar(config.start - timedelta(days=14), config.end + timedelta(days=31))


def weekly_sessions(calendar: ExchangeCalendar, start: date, end: date) -> list[date]:
    """列出日期范围内每周最后一个交易日；假日周也以真实下一交易日跨周判断。"""
    sessions = calendar.sessions(start, end)
    # ISO 年周组合同时处理跨年周，不能仅比较星期五。
    return [
        day
        for day in sessions
        if calendar.next_session(day).isocalendar()[:2] != day.isocalendar()[:2]
    ]


def code_evidence() -> dict[str, str | bool]:
    """读取仓库提交与自有源码指纹；安装包外运行标记未知，不猜测 Git 信息。"""
    # 源码安装的仓库根目录由模块位置确定，不读取用户随意 cwd。
    root = Path(__file__).resolve().parents[2]
    commit = "unavailable"
    # 未知工作树默认标为脏，禁止误判可重现发布版本。
    dirty = True
    if (root / ".git").exists():
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
        )
    # 指纹涵盖未提交的实际 Python 和配置，避免只依赖初始提交号。
    candidates = sorted(
        [*root.glob("src/**/*.py"), *root.glob("tools/**/*.py"), *root.glob("configs/*.toml")]
    )
    hashes = {str(path.relative_to(root)): hash_file(path) for path in candidates if path.is_file()}
    return {"commit": commit, "dirty": dirty, "source_hash": canonical_hash(hashes)}


def runtime_evidence() -> dict[str, str]:
    """收集已安装 Python、依赖版本和锁文件哈希；缺锁明确标为 unavailable。"""
    packages = ("numpy", "pandas", "pydantic", "pyarrow", "exchange-calendars", "alpaca-py")
    result = {package: version(package) for package in packages}
    result.update(python=platform.python_version(), platform=platform.platform())
    lock = Path(__file__).resolve().parents[2] / "uv.lock"
    # 打包分发若未携带锁文件需诚实显示缺失。
    result["lock_hash"] = hash_file(lock) if lock.is_file() else "unavailable"
    # 固定数值比较约定，回放以类型和业务结果为准。
    result["float_tolerance"] = "relative=1e-12, absolute=1e-12; money=Decimal exact"
    return result


def _seal_run(output: Path, manifest: RunManifest) -> RunManifest:
    """计算当前运行产物哈希并独占写入清单；已有清单抛 FileExistsError。"""
    # artifacts 的键是运行目录内的相对文件名，值是对应文件字节的摘要，供 validate_run 核对。
    # 清单不能把自身哈希纳入自身而产生循环定义。
    artifacts = {
        str(path.relative_to(output)): hash_file(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    # model_dump 先取出原清单字段，再用 model_validate 重验加入摘要后的完整清单。
    # 写JSON时 mode="json" 将日期、Decimal等转为可序列化形式，不会自动写文件或重新计算业务值。
    sealed = RunManifest.model_validate({**manifest.model_dump(), "artifacts": artifacts})
    # 清单是完整运行最后写入的成功证据。
    write_json(output / "manifest.json", sealed.model_dump(mode="json"))
    return sealed


def _turnover(store: SQLiteEventStore, decision_id: str) -> Decimal:
    """汇总本决策唯一成交的真实金额；返回美元总额，不使用限价替代成交价。"""
    # 先从内部订单筛出本决策的客户端ID集合；事件本身没有决策ID，需由此判断归属。
    identities = {
        record.intent.client_order_id
        for record in store.orders()
        if record.intent.decision_id == decision_id
    }
    # unique 的复合键是(券商来源, 成交ID)，值是一次经济成交；不同传输event_id不多算一次。
    unique = {
        (event.source, event.fill_id): event
        for event in store.events()
        if isinstance(event, FillEvent) and event.client_order_id in identities
    }
    # 买卖总額相加而非抵销。
    return sum((event.price * event.quantity for event in unique.values()), Decimal("0"))


def run_demo(config: DemoConfig, output: Path) -> RunManifest:
    """用合成历史完成最后一个合格周调仓决策，并在下一交易时段模拟执行。

    config 只允许离线演示；output 必须不存在。先保存决策输入与目标，再向独立
    FakeBroker 提交、注入成交、恢复对账，最后输出告警、报告和带哈希的运行清单。
    不是逐周完整回测，也不访问真实账户。失败保留已写文件与数据库用于诊断，
    不生成成功清单；风险阻断抛 RiskBlocked，数据契约或源码中途变化抛 ContractError，
    文件系统错误原样传播。成功返回最终封印清单。
    """
    # 在运行开始冻结源码证据，避免并行编辑后把新文件误标为已执行代码。
    captured_code = code_evidence()
    # 拒绝覆盖旧运行，原始证据必须保持不可变。
    output.mkdir(parents=True, exist_ok=False)
    # calendar 提供交易时段；records 是合成每日行情，securities 是带历史资格的证券主表。
    # 它们是输入资料，尚未形成账户持仓或任何订单。
    calendar = make_calendar(config)
    records, securities = generate_fixture(config, calendar)
    eligible_decisions = weekly_sessions(calendar, config.start, config.end)
    if not eligible_decisions:
        raise ContractError("样本没有已完成的周调仓交易日")
    # 首次演示只执行最后一个合格周调仓决策。
    decision_day = eligible_decisions[-1]
    # 留十五分钟固定接入延迟后再留十五分钟验证窗口。
    decision_time = calendar.close_at(decision_day) + timedelta(minutes=30)
    # build_snapshot 从完整输入筛出 decision_time 已可用的版本并校验质量。
    # snapshot 是此决策的统一研究输入，后续因子/评分共用它，不直接使用完整 records。
    snapshot = build_snapshot(records, securities, decision_time, calendar.version)
    # 保存完整原始样本与主表，研究可用事后部分但策略不可用。
    write_market_parquet(output / "inputs/market.parquet", records)
    write_json(
        output / "inputs/securities.json", [item.model_dump(mode="json") for item in securities]
    )
    # 两组报价都以稳定证券ID为键、Quote为值：决策报价用原始收盘来规划目标，
    # 下一时段报价才交执行层。因子使用的总回报研究价格不能拿来成交。
    decision_quotes = fixture_quotes(snapshot.records, calendar, execution=False)
    # 下一时段执行报价单独生成并留证据，不能反向影响决策。
    execution_quotes = fixture_quotes(snapshot.records, calendar, execution=True)
    write_json(
        output / "inputs/execution_quotes.json",
        {key: quote.model_dump(mode="json") for key, quote in sorted(execution_quotes.items())},
    )
    # 初始账户时点属于决策时刻，不借用后来的执行状态。
    initial = AccountSnapshot(
        account_id=config.account_id,
        as_of=decision_time,
        cash=config.initial_cash,
        available_cash=config.initial_cash,
    )
    # factors 是每只股票各因子的原始数值/缺失原因列表，尚不能直接跨因子比较。
    # calculate_factors 只计算、不写账户；结果交 score_factors 做共同样本评分。
    factors = calculate_factors(snapshot, calendar)
    # signals 保存双因子都有效的股票、百分位综合分、排序和排除原因；这仍不是订单。
    signals = score_factors(snapshot, factors)
    # 独立基准只影响观察报告。
    regime = assess_regime(
        [item for item in snapshot.records if item.security_id == "BENCH"], decision_time, calendar
    )
    # target 是按初始账户净值与约束希望达到的整股持仓，可能留现金；不修改 initial。
    # 后续 plan_orders 会用执行时实际账户计算离此目标还差多少，不能直接按目标重复买入。
    target = build_portfolio(signals, initial, decision_quotes, snapshot.securities, config)
    # 决策证据必须在发送任何意图之前写入。
    for name, model in (
        ("snapshot", snapshot),
        ("signals", signals),
        ("regime", regime),
        ("target", target),
    ):
        write_json(output / f"{name}.json", model.model_dump(mode="json"))
    write_json(output / "factors.json", [item.model_dump(mode="json") for item in factors])
    # 下一时段开盘是明确注入的执行时刻。
    execution_at = calendar.open_at(calendar.next_session(decision_day))
    clock = FixedClock(execution_at)
    # store 维护我们的意图/事件/账户，broker 独立维护模拟券商账户，分别写两个SQLite文件。
    # clock 提供本次业务时刻，calendar 判断可交易时段；service 协调锁、风控、发送和对账。
    store = SQLiteEventStore(output / "internal.sqlite", initial)
    broker = FakeBroker(output / "broker.sqlite", clock, config, initial, calendar=calendar)
    service = ExecutionService(store, broker, clock, config, calendar)
    # 流动性依据只来自策略已知的最近20个交易日。
    latest_sessions = calendar.sessions(config.start, decision_day)[-20:]
    # ADV（平均每日成交量）在此取最近20个交易日、单位股/日。
    # adv[证券ID] 供规划和风控计算单单流动性上限；缺完整20日就不放入该证券，不能当零成交量补齐。
    adv: dict[str, float] = {}
    for security in snapshot.securities:
        volumes = [
            record.volume
            for record in snapshot.records
            if record.security_id == security.security_id and record.session in latest_sessions
        ]
        if len(volumes) == 20:
            adv[security.security_id] = sum(volumes) / 20
    # risks 逐单累积风险判定，稍后写 risk.json 供审计；render_report 不读取此列表，它不授予执行权限。
    risks: list[dict[str, Any]] = []
    # 最多两次规划：第二次只使用第一轮已确认成交释放的现金与额度。
    for _ in range(2):
        # recover 会导入券商新事件并更新内部账务，再返回两边是否一致；不是只读查询。
        # 未解释差异立即结束演示，已经写入的意图/事件留在数据库等待诊断。
        reconciliation = service.recover()
        if not reconciliation.matched:
            raise RiskBlocked("；".join(reconciliation.differences))
        # turnover 是本决策已真实成交的买卖美元总额，供规划扣减剩余换手预算，不用限价估算。
        turnover = _turnover(store, signals.decision_id)
        # intents 是候选订单意图列表：目标减去实际持仓及未完成订单，再受现金/风险预算约束。
        # plan_orders 只做内存计算，不写库、不发单；这里每轮都重读账户，避免沿用成交前的余额。
        intents = plan_orders(
            target,
            store.account(execution_at),
            store.orders(),
            execution_quotes,
            adv,
            config,
            execution_at,
            security_records=snapshot.securities,
            turnover_used=turnover,
        )
        # 空列表只表示当前条件下没有可规划候选，不保证目标已全部达到；留给后面的监控说明偏差。
        if not intents:
            break
        for intent in intents:
            # 这次 assess_order 读取最新事实生成审计用判定。reference_nav/peak_nav 在演示中
            # 都取决策时目标净值（NAV，美元）；实际提交仍由 service 在锁内重新核验。
            risk = assess_order(
                intent,
                store.account(execution_at),
                store.orders(),
                execution_quotes,
                config,
                execution_at,
                calendar,
                reconciled=True,
                target=target,
                security_records=snapshot.securities,
                adv=adv,
                reference_nav=target.nav,
                peak_nav=target.nav,
                turnover_used=_turnover(store, signals.decision_id),
            )
            risks.append(
                {
                    "client_order_id": intent.client_order_id,
                    "at": execution_at.isoformat(),
                    "reference_nav": str(target.nav),
                    "peak_nav": str(target.nav),
                    **risk.model_dump(mode="json"),
                }
            )
            # submit 在账户锁内保存意图、风控后调用 Broker，返回订单状态，不表示已经成交。
            # 即使上面的审计判定已生成，执行服务仍可根据最新事实拒绝；不能拿审计记录当批准结果。
            order = service.submit(
                intent,
                execution_quotes,
                target=target,
                security_records=snapshot.securities,
                adv=adv,
                reference_nav=target.nav,
                peak_nav=target.nav,
            )
            # 演示只对明确受理的订单注入成交事件。
            if order.status not in {"OPEN", "PARTIAL"}:
                raise RiskBlocked(f"订单未明确受理：{order.status}")
            # fill 只写模拟券商自己的成交/账户；内部账本要等下一次 recover 消费事件才变化。
            broker.fill(intent.client_order_id, execution_quotes[intent.security_id])
            # 每次成交后消费事件与独立对账，再考虑下一单。
            checked = service.recover()
            if not checked.matched:
                raise RiskBlocked("；".join(checked.differences))
    # 模拟执行结束后再次核对独立状态；这里没有推进到实际收盘时刻。
    reconciliation = service.recover()
    # account/orders 是执行后的内部事实；target 仍保留原目标，二者不能用同一对象代替。
    account = store.account(execution_at)
    orders = store.orders()
    # 覆盖率分母为原始合成股票数，分子为双因子共同有效的入选评分数，不是实际成交股票数。
    coverage = len(signals.scores) / config.securities
    # 以本次运行的时点和完成步骤生成结构化告警，不代表外部常驻心跳监控。
    alerts = assess_health(
        account,
        target,
        reconciliation,
        orders,
        at=execution_at,
        heartbeat_at=execution_at,
        provider_at=execution_at,
        data_good=True,
        factor_coverage=coverage,
        completed_steps={"data", "factors", "portfolio", "execution", "reconciliation"},
    )
    # 财务事实写出后才封印成功清单。
    write_json(output / "account.json", account.model_dump(mode="json"))
    write_json(output / "reconciliation.json", reconciliation.model_dump(mode="json"))
    write_json(output / "orders.json", [item.model_dump(mode="json") for item in orders])
    write_json(output / "events.json", [item.model_dump(mode="json") for item in store.events()])
    # 有序事务证据包含公司行动与意图的真实交错。
    write_json(output / "journal.json", [item.model_dump(mode="json") for item in store.journal()])
    write_json(output / "risk.json", risks)
    # 关键告警独立于日报文本，未来通知适配器可直接消费。
    (output / "alerts.jsonl").write_text(
        "".join(alert.model_dump_json() + "\n" for alert in alerts), encoding="utf-8"
    )
    # 可读报告完全由实际结构化记录派生。
    (output / "report.md").write_text(
        render_report(
            snapshot, factors, signals, regime, target, orders, account, reconciliation, alerts
        ),
        encoding="utf-8",
    )
    # 运行身份由决策与配置确定，不使用系统时间掩盖重复。
    run_id = canonical_hash(
        {"decision_id": signals.decision_id, "config": config.model_dump(mode="json")}
    )[:24]
    # 运行期间若业务或维护源码改变，则不能签发可复现成功清单。
    if code_evidence()["source_hash"] != captured_code["source_hash"]:
        raise ContractError("运行期间源码发生变化，未生成成功清单")
    # 先生成不含文件指纹的结构化清单，再由最后一步封印。
    manifest = RunManifest(
        run_id=run_id,
        decision_id=signals.decision_id,
        decision_time=decision_time,
        code=captured_code,
        environment=runtime_evidence(),
        config=config,
        initial_account=initial,
        inputs={
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": snapshot.content_hash,
            "calendar_version": calendar.version,
            "security_master_hash": canonical_hash(
                [item.model_dump(mode="json") for item in snapshot.securities]
            ),
        },
        artifacts={},
        limitations=[
            "合成数据仅验证工程，无真实收益证据",
            "FakeBroker仅为契约与故障测试驱动",
            "合成立即结算不适用于未经核验的真实账户",
            "单机账户锁，不是分布式执行权限",
            "策略/因子/风险/研究阈值均为演示参数",
        ],
    )
    return _seal_run(output, manifest)


def replay_journal(store: SQLiteEventStore, journal: list[JournalEntry]) -> None:
    """按连续事务序号重建传入的新状态库，不触发券商提交。

    序号不连续或操作与载荷类型不匹配时抛 ContractError；存储方法产生的模型校验、
    数据库及其他错误原样传播，已提交的较早操作不会随着后项失败一起回滚。
    """
    # journal 是按入库先后排列的操作记录；payload 随 operation 分别是意图、订单状态、事件或行动。
    # 这些调用只重建传入 store；若后项失败，前面已经提交的事务仍保留，不是全日志一次回滚。
    for number, entry in enumerate(journal, start=1):
        # 缺失或重复序号不能静默重排。
        if entry.sequence != number:
            raise ContractError("事务日志序号不连续")
        if entry.operation == "intent" and isinstance(entry.payload, OrderIntent):
            store.save_intent(entry.payload)
        elif entry.operation == "order" and isinstance(entry.payload, OrderRecord):
            store.record_order(entry.payload)
        elif entry.operation == "event" and isinstance(entry.payload, OrderEvent | FillEvent):
            store.apply(entry.payload)
        # 公司行动按原处理时点应用，不能提前计息或拆股。
        elif entry.operation == "action" and isinstance(entry.payload, CorporateAction):
            store.apply_action(entry.payload, entry.at)
        else:
            raise ContractError("事务操作与载荷不匹配")


def validate_run(root: Path) -> RunManifest:
    """核对清单哈希、历史输入、交易日志及派生决策是否组成同一次一致运行。

    root 是已有运行目录。先检查文件边界与哈希，再重建时点快照，在临时库重放
    账户和订单，最后只读比较两份数据库并重算因子、目标等结果。
    不写源目录；会创建并清理临时状态库。文件或模型错误原样抛出，业务证据不一致
    抛 ContractError，已记录的对账不通过抛 RiskBlocked；成功返回原清单。
    """
    # read_json 只解析文本；model_validate 才把字典变为带类型/单位约束的模型，非法结构会抛错。
    # 模型校验不证明文件内容可信，因此仍要继续比对哈希、原始输入与数据库。
    manifest = RunManifest.model_validate(read_json(root / "manifest.json"))
    # 防止空清单或删掉故障文件的引用后获得通过。
    if missing := REQUIRED_ARTIFACTS - set(manifest.artifacts):
        raise ContractError("运行缺少必要产物：" + ",".join(sorted(missing)))
    # 首先逐文件核验哈希和路径边界。
    if errors := validate_artifacts(root, manifest.artifacts):
        raise ContractError("；".join(errors))
    records = read_market_parquet(root / "inputs/market.parquet")
    securities = [
        SecurityRecord.model_validate(item) for item in read_json(root / "inputs/securities.json")
    ]
    # 从原始输入重新计算时点快照，验证版本选择未被改写。
    reconstructed = build_snapshot(
        records, securities, manifest.decision_time, manifest.inputs["calendar_version"]
    )
    stored = DataSnapshot.model_validate(read_json(root / "snapshot.json"))
    if reconstructed != stored or reconstructed.content_hash != manifest.inputs["snapshot_hash"]:
        raise ContractError("快照与原始时点数据不一致")
    # expected_account/orders 是该运行先前导出的待核对终态，不是从它们生成“独立正确答案”。
    # 下方临时库将从 initial_account 与 journal 重建账户，再与这些导出值比较。
    expected_account = AccountSnapshot.model_validate(read_json(root / "account.json"))
    # 对账失败不能被标为成功运行。
    reconciliation = ReconciliationResult.model_validate(read_json(root / "reconciliation.json"))
    if not reconciliation.matched:
        raise RiskBlocked("运行存在未解释的对账差异")
    # 即使有人重签导出JSON哈希，语义也必须与原始日志和独立状态相符。
    journal = [JournalEntry.model_validate(item) for item in read_json(root / "journal.json")]
    expected_orders = [OrderRecord.model_validate(item) for item in read_json(root / "orders.json")]
    # TemporaryDirectory 的 with 结束时清理临时库（异常也清理），原运行文件始终不写。
    with TemporaryDirectory(prefix="quant-validate-") as temporary:
        store = SQLiteEventStore(Path(temporary) / "reconstructed.sqlite", manifest.initial_account)
        replay_journal(store, journal)
        if (
            store.account(expected_account.as_of) != expected_account
            or store.orders() != expected_orders
        ):
            raise ContractError("账户或订单与事务日志不一致")
        # 原始事件导出也必须对应事务中接受的事件。
        if [item.model_dump(mode="json") for item in store.events()] != read_json(
            root / "events.json"
        ):
            raise ContractError("事件导出与事务日志不一致")
    # 两份实际状态库都以只读连接核验，不信任导出文件代替独立对账。
    for kind in ("internal", "broker"):
        database_account, database_orders = read_sqlite_state(root / f"{kind}.sqlite", kind)
        # model_copy(update=...) 创建用于比较的新对象，不重验字段，也不改库中的账户。
        # 这里只统一查询时间 as_of；资金和股数不改，不声称重新取得当年的市场状态。
        normalized = database_account.model_copy(update={"as_of": expected_account.as_of})
        # 本地未发送的风险拒绝不会在券商产生订单。
        comparable_orders = [
            order
            for order in expected_orders
            if kind == "internal" or order.broker_order_id is not None
        ]
        if normalized != expected_account or database_orders != comparable_orders:
            raise ContractError(f"{kind} 状态库与已导出事实不一致")
    # actual_factors/signals/target 在此意为“本次重算值”，不是实际成交事实。
    # 从已核对的历史快照重复纯计算，与保存的决策产物比较，避免只有交易账目一致而解释漂移。
    calendar = make_calendar(manifest.config)
    actual_factors = calculate_factors(stored, calendar)
    actual_signals = score_factors(stored, actual_factors)
    # 目标只使用决策时初态和原始收盘。
    actual_target = build_portfolio(
        actual_signals,
        manifest.initial_account,
        fixture_quotes(stored.records, calendar, execution=False),
        stored.securities,
        manifest.config,
    )
    actual_regime = assess_regime(
        [item for item in stored.records if item.security_id == "BENCH"],
        stored.decision_time,
        calendar,
    )
    # 分别比较每层已保存输出，避免解释仍描述旧行为。
    derived = {
        "factors": [item.model_dump(mode="json") for item in actual_factors],
        "signals": actual_signals.model_dump(mode="json"),
        "target": actual_target.model_dump(mode="json"),
        "regime": actual_regime.model_dump(mode="json"),
    }
    for name, value in derived.items():
        if not equivalent_values(value, read_json(root / f"{name}.json")):
            raise ContractError(f"{name} 与固定输入重算结果不一致")
    return manifest


def replay_run(source: Path, output: Path) -> RunManifest:
    """验证源运行后，在新目录从初态与原始事务日志重建内部账户和订单。

    output 必须不存在；其余已验证证据可以复制，但内部状态库必须重新生成。
    重算因子、评分和目标后逐项比较，任何差异抛 ContractError 并保留失败产物。
    成功返回记录本次源码、环境与源清单哈希的新清单；不调用 Broker 提交订单。
    """
    manifest = validate_run(source)
    output.mkdir(parents=True, exist_ok=False)
    # 保留源运行全部证据，内部状态库必须重新建立。
    for name in sorted(manifest.artifacts):
        if name == "internal.sqlite":
            continue
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    # 新事件库仅以原始初态开始。
    store = SQLiteEventStore(output / "internal.sqlite", manifest.initial_account)
    journal = [JournalEntry.model_validate(item) for item in read_json(source / "journal.json")]
    replay_journal(store, journal)
    # expected 是源运行导出的账户终态，actual 是新库按日志重建的终态；二者以同一时刻比较。
    expected = AccountSnapshot.model_validate(read_json(source / "account.json"))
    actual = store.account(expected.as_of)
    expected_orders = [
        OrderRecord.model_validate(item) for item in read_json(source / "orders.json")
    ]
    # 重算时仍用决策初态与原始收盘形成目标，不用 actual 成交后的现金倒推原决策。
    calendar = make_calendar(manifest.config)
    snapshot = DataSnapshot.model_validate(read_json(source / "snapshot.json"))
    factors = calculate_factors(snapshot, calendar)
    signals = score_factors(snapshot, factors)
    target = build_portfolio(
        signals,
        manifest.initial_account,
        fixture_quotes(snapshot.records, calendar, execution=False),
        snapshot.securities,
        manifest.config,
    )
    # 组合每层分别比较，以定位逻辑变化。
    comparisons = {
        "account": actual == expected,
        "orders": store.orders() == expected_orders,
        "factors": equivalent_values(
            [item.model_dump(mode="json") for item in factors], read_json(source / "factors.json")
        ),
        "signals": equivalent_values(
            signals.model_dump(mode="json"), read_json(source / "signals.json")
        ),
        "target": equivalent_values(
            target.model_dump(mode="json"), read_json(source / "target.json")
        ),
    }
    write_json(output / "replay-verification.json", comparisons)
    if not all(comparisons.values()):
        raise ContractError("回放结果不一致：" + str(comparisons))
    # model_copy 只替换这次回放的来源/版本信息，不重验；_seal_run 会再验证完整清单并写盘。
    # 复制的成交事实仍属于源运行，不假称取得新的市场成交。
    replayed = manifest.model_copy(
        update={
            "run_id": manifest.run_id + "-replay",
            "code": code_evidence(),
            "environment": runtime_evidence(),
            "inputs": {
                **manifest.inputs,
                "source_manifest_hash": hash_file(source / "manifest.json"),
            },
        }
    )
    return _seal_run(output, replayed)


def report_run(root: Path) -> str:
    """通过运行校验后，从留存结构化事实重生报告文本，不覆盖源报告。"""
    validate_run(root)
    # 告警是独立结构化证据，不从旧日报反推。
    alerts = [
        Alert.model_validate_json(line)
        for line in (root / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return render_report(
        DataSnapshot.model_validate(read_json(root / "snapshot.json")),
        [FactorValue.model_validate(item) for item in read_json(root / "factors.json")],
        SignalSet.model_validate(read_json(root / "signals.json")),
        RegimeAssessment.model_validate(read_json(root / "regime.json")),
        TargetPortfolio.model_validate(read_json(root / "target.json")),
        [OrderRecord.model_validate(item) for item in read_json(root / "orders.json")],
        AccountSnapshot.model_validate(read_json(root / "account.json")),
        ReconciliationResult.model_validate(read_json(root / "reconciliation.json")),
        alerts,
    )


def research_run(root: Path) -> dict[str, Any]:
    """按每个历史周末的可用数据重建特征，再配对事后收益进行隔离研究。

    root 必须是可通过 validate_run 的运行目录。实验写入新的递增编号目录，
    包含请求、冻结因子、收益标签、滚动切分和汇总；原交易快照不变。
    只有动量与低波动共同有效的样本进入研究，但覆盖率分母仍包括全部原候选。
    成功返回统计摘要；计算阶段失败记录 failure.json 后原样抛出，不删除失败实验。
    """
    manifest = validate_run(root)
    # 研究也记录本次实际代码与环境，不能只借用交易演示的旧版本。
    captured_code = code_evidence()
    research_root = root / "research"
    research_root.mkdir(exist_ok=True)
    # 递增实验号保留失败与全部尝试，不覆盖最佳一次。
    number = 1
    while (directory := research_root / f"experiment-{number:04d}").exists():
        number += 1
    directory.mkdir()
    # 试验开始记录输入与固定预算，尚未宣称成功。
    write_json(
        directory / "request.json",
        {
            "source_run": manifest.run_id,
            "snapshot_hash": manifest.inputs["snapshot_hash"],
            "config": manifest.config.model_dump(mode="json"),
            "trial_budget": 1,
            "code": captured_code,
            "environment": runtime_evidence(),
            "tuning": False,
        },
    )
    try:
        calendar = make_calendar(manifest.config)
        # 完整事后行情只在研究局部用于标签。
        records = read_market_parquet(root / "inputs/market.parquet")
        securities = [
            SecurityRecord.model_validate(item)
            for item in read_json(root / "inputs/securities.json")
        ]
        # all_factors 累积每周×每证券×每因子的结果，包含预热不足的None，留作研究输入证据。
        all_factors: list[FactorValue] = []
        days = weekly_sessions(calendar, manifest.config.start, manifest.config.end)
        # 每个历史决策独立按available_at查询。
        for day in days:
            # 可用延迟及验证窗口与演示完全一致。
            at = calendar.close_at(day) + timedelta(minutes=30)
            snapshot = build_snapshot(records, securities, at, calendar.version)
            all_factors.extend(calculate_factors(snapshot, calendar))
        # 双因子策略的时间窗口必须从共同有效特征开始，不能借用只有低波动的早期样本。
        # available_factors[(证券ID, 决策时刻)] = 此股票此周有效的因子ID集合。
        # 它记录样本资格，不存储分数，也不能把不同周的两个有效因子拼成一对。
        available_factors: dict[tuple[str, datetime], set[str]] = {}
        for factor in all_factors:
            if factor.value is not None and factor.reason is None:
                # setdefault 首次建立该股票/时刻的集合，后续沿用它累积因子ID，避免覆盖先前因子。
                available_factors.setdefault((factor.security_id, factor.decision_time), set()).add(
                    factor.factor_id
                )
        required_factors = {"momentum", "low_volatility"}
        # get 的空集合表示该股票/时刻没有有效因子；<= 是集合包含，要求两种因子同时齐全。
        # common_factors 保留合格股票/时刻的两条原始结果，供标签函数按同一时点组装样本。
        common_factors = [
            factor
            for factor in all_factors
            if required_factors
            <= available_factors.get((factor.security_id, factor.decision_time), set())
        ]
        # observations 每项配对一只股票某次决策的两因子与后续5交易日收益；未来收益只用于研究。
        # 无完整标签区间会跳过该样本，因此样本数还可能小于共同有效因子所覆盖的股票/周数。
        observations = label_observations(common_factors, records, calendar)
        # folds 是训练/验证/测试/保留期的样本索引集合；空列表表示没有完整且开发分组非空的窗口，
        # 可能是时间跨度或有效样本不足，不等于研究盈利。
        folds = rolling_splits(observations)
        # 整体描述统计明确包含预热与未成熟标签造成的覆盖缺口。
        summary = evaluate_research(
            observations, expected_observations=len(days) * manifest.config.securities
        )
        # fold_results 逐窗口累积四组统计；getattr 取每组在 observations 中的整数索引，
        # 再取对应完整研究样本交 evaluate_research，不能把索引当收益或直接混合四组。
        fold_results: list[dict[str, Any]] = []
        for fold in folds:
            groups = {
                name: evaluate_research([observations[index] for index in getattr(fold, name)])
                for name in ("train", "validation", "test", "holdout")
            }
            fold_results.append({"fold": fold.model_dump(mode="json"), "groups": groups})
        # 长时间研究期间任何源码变动都破坏本次版本证据。
        if code_evidence()["source_hash"] != captured_code["source_hash"]:
            raise ContractError("研究期间源码发生变化，保留失败实验")
        summary.update(
            folds=len(folds),
            experiment=str(directory),
            status="completed",
            label="next_tradeable_open_to_fifth_session_close",
            costs="研究标签未扣交易成本；演示实际费用另见account.json，不是正式业绩",
        )
        # 保留全部原始冻结因子用于重算研究统计。
        write_json(
            directory / "factors.json", [item.model_dump(mode="json") for item in all_factors]
        )
        # 研究标签与交易输入物理分离。
        write_json(
            directory / "observations.json", [item.model_dump(mode="json") for item in observations]
        )
        write_json(directory / "folds.json", fold_results)
        write_json(directory / "summary.json", summary)
        # 试验请求和输出也以哈希保留。
        write_json(
            directory / "artifacts.json",
            {path.name: hash_file(path) for path in sorted(directory.iterdir()) if path.is_file()},
        )
        return summary
    # 失败试验也属于试验预算，不能删除隐藏。
    except Exception as exc:
        write_json(
            directory / "failure.json",
            {"status": "failed", "error_type": type(exc).__name__, "message": str(exc)},
        )
        raise


def paper_read(
    config: "PaperConfig", output: Path, transport: "AlpacaSDKTransport", clock: "Clock"
) -> dict[str, Any]:
    """采集真实 Paper 只读证据；不启用写能力，数据不足也保存明确阻塞原因。

    凭据和账户必须由用户先指定。输出目录全新；每类响应完整取得后追加保存，
    行情失败只影响数据资格，不以 FakeBroker 或合成资料替代。此处尚未运行策略。
    """
    from quant_core.adapters.alpaca import AlpacaPaperBroker
    from quant_core.adapters.alpaca_data import NEW_YORK, fetch_corporate_actions, fetch_daily_bars

    output.mkdir(parents=True, exist_ok=False)
    at = clock.now()
    broker = AlpacaPaperBroker(
        transport,
        config.account_id,
        clock,
        {symbol: symbol for symbol in config.candidates},
        lambda: [],
        at,
    )
    raw = broker.read_snapshot()
    write_json(output / "account-observation.json", raw)
    write_json(output / "config.json", config.model_dump(mode="json"))
    assets = broker.assets()
    write_json(output / "assets.json", assets)
    calendar_end = max(config.history_end, at.date()) + timedelta(days=14)
    calendar = broker.calendar(config.history_start, calendar_end)
    write_json(output / "calendar.json", calendar)
    write_json(output / "clock.json", transport.request("GET", "/clock"))
    blockers = (
        ["需要绑定当前普通股身份，MA使用仅拆股价格；未知行业按最坏情况计量"]
        if config.strategy == "ma-trend"
        else ["需要可追溯普通股类别、行业与股息再投资总回报资料"]
    )
    if raw["positions"] or any(
        row.get("status") not in {"filled", "canceled", "expired", "rejected", "replaced"}
        for row in raw["orders"]
    ):
        blockers.append("本次最小建仓入口要求初态无持仓和开放订单；现有事实原样保留")
    try:
        bars = fetch_daily_bars(
            transport,
            config.candidates,
            config.history_start,
            config.history_end,
            config.history_feed,
            asof=at.astimezone(NEW_YORK).date(),
        )
        write_json(output / "bars.json", bars)
        if config.strategy == "ma-trend":
            # 价格序列与原始成交量分开保存，调整价绝不冒充总回报或执行报价。
            split_bars = fetch_daily_bars(
                transport,
                config.candidates,
                config.history_start,
                config.history_end,
                config.history_feed,
                adjustment="split",
                asof=at.astimezone(NEW_YORK).date(),
            )
            write_json(output / "split-bars.json", split_bars)
            actions = fetch_corporate_actions(
                transport, config.candidates, config.history_start, at.astimezone(NEW_YORK).date()
            )
            actions["observed_at"] = clock.now().isoformat()
            write_json(output / "corporate-actions.json", actions)
        write_json(
            output / "data-requests.json",
            {
                "symbols": config.candidates,
                "start": str(config.history_start),
                "end": str(config.history_end),
                "asof": str(at.astimezone(NEW_YORK).date()),
                "feed": config.history_feed,
                "timeframe": "1Day",
                "adjustments": ["raw", "split"] if config.strategy == "ma-trend" else ["raw"],
            },
        )
        history = "received_sip_raw"
    except (ConnectionError, TimeoutError, ContractError) as exc:
        history = "blocked"
        blockers.append(f"历史行情未合格：{type(exc).__name__}:{exc}")
    result = {
        "status": "read_only",
        "account_verified": True,
        "history": history,
        "observed_at": clock.now().isoformat(),
        "calendar_end": str(calendar_end),
        "blockers": blockers,
        "code": code_evidence(),
        "environment": runtime_evidence(),
    }
    write_json(output / "read-result.json", result)
    return result


def paper_plan(
    source: Path, supplement_path: Path | None, output: Path, *, identity_path: Path | None = None
) -> dict[str, Any]:
    """从留存的真实读取和补充证据计算原因子、评分及目标，完全离线且不下单。

    只支持首次空仓账户；固定预算建立独立策略资金视图，未分配远端现金单列。
    补充数据方法须经人工核实；模型校验不等于供应商质量认证。
    """
    from quant_core.adapters.alpaca_data import (
        NEW_YORK,
        AlpacaCalendar,
        build_ma_snapshot,
        build_paper_snapshot,
    )
    from quant_core.contracts import PaperConfig, Quote
    from quant_core.factors import calculate_ma_factors
    from quant_core.signals import score_ma_factors

    config = PaperConfig.model_validate(read_json(source / "config.json"))
    raw = read_json(source / "account-observation.json")
    meta = read_json(source / "read-result.json")
    if raw["positions"] or any(
        row.get("status") not in {"filled", "canceled", "expired", "rejected", "replaced"}
        for row in raw["orders"]
    ):
        raise ContractError("初态有持仓或未完成订单；不重置、不撤销，请使用独立空仓 Paper 账户")
    if raw["account"]["id"] != config.account_id:
        raise ContractError("只读证据账户与配置不一致")
    cash = Decimal(raw["account"]["cash"])
    buying_power = Decimal(raw["account"]["non_marginable_buying_power"])
    if config.budget > min(cash, buying_power):
        raise ContractError("策略预算超过远端现金或非保证金购买力")
    at = datetime.fromisoformat(meta["observed_at"])
    supplement = read_json(supplement_path) if supplement_path is not None else None
    # 传统双因子仍要求真实总回报和分类；MA从独立价格口径构建，不能静默改公式。
    if config.strategy == "weekly-two-factor":
        if supplement is None:
            raise ContractError("原双因子需要--supplement总回报和行业资料")
        if datetime.fromisoformat(supplement["available_at"]) > at:
            raise ContractError("补充资料晚于读取时点，请重新执行只读采集形成新决策")
    calendar = AlpacaCalendar(
        read_json(source / "calendar.json"),
        config.history_start,
        date.fromisoformat(meta["calendar_end"]),
    )
    latest = [
        day
        for day in calendar.sessions(config.history_start, at.astimezone(NEW_YORK).date())
        if calendar.close_at(day) <= at
    ][-1]
    following = calendar.next_session(latest)
    data_excluded: dict[str, str] = {}
    identity: dict[str, Any] | None = None
    if config.strategy == "weekly-two-factor":
        if latest.isocalendar()[:2] == following.isocalendar()[:2] or at >= calendar.open_at(
            following
        ):
            raise ContractError("本次采集不在原周频策略的周末决策窗口")
        snapshot = build_paper_snapshot(
            bars=read_json(source / "bars.json"),
            assets=read_json(source / "assets.json"),
            supplement=supplement,
            calendar=calendar,
            observed_at=at,
            decision_time=at,
            candidates=config.candidates,
        )
    else:
        if identity_path is None:
            raise ContractError("MA计划需要--identity-evidence当前普通股身份来源")
        identity = read_json(identity_path)
        snapshot, data_excluded = build_ma_snapshot(
            bars=read_json(source / "bars.json"),
            split_bars=read_json(source / "split-bars.json"),
            assets=read_json(source / "assets.json"),
            identity_evidence=identity,
            corporate_actions=read_json(source / "corporate-actions.json"),
            calendar=calendar,
            observed_at=at,
            decision_time=at,
            candidates=config.candidates,
        )
    initial = AccountSnapshot(
        account_id=config.account_id,
        as_of=at,
        cash=config.budget,
        available_cash=config.budget,
    )
    quotes = {
        row.security_id: Quote(
            security_id=row.security_id,
            at=row.event_time,
            price=Decimal(str(row.raw_close)),
        )
        for row in snapshot.records
        if row.session == latest
    }
    if config.strategy == "ma-trend":
        factors = calculate_ma_factors(snapshot, calendar)
        signals = score_ma_factors(snapshot, factors)
        signals = SignalSet.model_validate(
            {**signals.model_dump(), "excluded": {**data_excluded, **signals.excluded}}
        )
    else:
        factors = calculate_factors(snapshot, calendar)
        signals = score_factors(snapshot, factors)
    target = build_portfolio(signals, initial, quotes, snapshot.securities, config)
    regime = assess_regime([], at, calendar)
    output.mkdir(parents=True, exist_ok=False)
    for name in (
        "config.json",
        "calendar.json",
        "assets.json",
        "bars.json",
        "read-result.json",
        "account-observation.json",
        "clock.json",
    ):
        write_json(output / name, read_json(source / name))
    if supplement is not None:
        write_json(output / "supplement.json", supplement)
    if identity is not None:
        write_json(output / "identity-evidence.json", identity)
    for extra in ("split-bars.json", "corporate-actions.json", "data-requests.json"):
        if (source / extra).is_file():
            write_json(output / extra, read_json(source / extra))
    write_json(
        output / "data-qualification.json",
        {
            "strategy": config.strategy,
            "signal_session": str(latest),
            "qualified": [row.security_id for row in snapshot.securities],
            "excluded": data_excluded,
            "price_basis": "split_adjusted" if config.strategy == "ma-trend" else "total_return",
            "unknown_sector_policy": "worst_case" if config.strategy == "ma-trend" else "reject",
        },
    )
    for name, model in (
        ("snapshot", snapshot),
        ("signals", signals),
        ("target", target),
        ("initial", initial),
        ("regime", regime),
    ):
        write_json(output / f"{name}.json", model.model_dump(mode="json"))
    write_json(output / "factors.json", [row.model_dump(mode="json") for row in factors])
    (output / "report.md").write_text(
        render_report(
            snapshot,
            factors,
            signals,
            regime,
            target,
            [],
            initial,
            ReconciliationResult(as_of=at, matched=False, differences=["execution_not_started"]),
            [],
            mode="paper",
        ),
        encoding="utf-8",
    )
    files = {path.name: hash_file(path) for path in output.iterdir() if path.is_file()}
    evidence = {
        "files": files,
        "reserve": str(cash - config.budget),
        "eligible_at": max(at, calendar.open_at(following)).isoformat(),
        "expires_at": calendar.close_at(following).isoformat(),
        "code": code_evidence(),
        "environment": runtime_evidence(),
    }
    plan_id = canonical_hash(evidence)
    write_json(output / "paper-plan.json", {"plan_id": plan_id, **evidence})
    return {
        "status": "planned" if target.positions else "no_target",
        "plan_id": plan_id,
        "scores": len(signals.scores),
        "positions": len(target.positions),
        "budget": str(config.budget),
        "eligible_at": evidence["eligible_at"],
        "orders_submitted": False,
    }


def _paper_quotes(
    transport: "AlpacaSDKTransport", securities: list[SecurityRecord], feed: str
) -> dict[str, "Quote"]:
    """读取原始最新卖价供首次买入执行；保留报价时间，不把抓取时间当作新鲜度。"""
    from quant_core.contracts import Quote

    response = transport.market_request(
        "/v2/stocks/quotes/latest",
        {"symbols": ",".join(row.ticker for row in securities), "feed": feed},
    )
    result = {}
    for security in securities:
        raw = response["quotes"][security.ticker]
        result[security.security_id] = Quote(
            security_id=security.security_id,
            at=datetime.fromisoformat(raw["t"].replace("Z", "+00:00")),
            price=Decimal(str(raw["ap"])),
            tradable=security.tradable,
        )
    return result


def _paper_queue_order(
    snapshot: DataSnapshot,
    target: TargetPortfolio,
    plan: dict[str, Any],
    transport: "AlpacaSDKTransport",
    clock: "Clock",
    calendar: "AlpacaCalendar",
    config: "PaperConfig",
) -> tuple["PaperQueueTestContext", OrderIntent, dict[str, "Quote"], dict[str, float]]:
    """为休市撤单验收准备首个策略目标的一股；不另选股票或伪造实时行情。

    参考价来自冻结快照的最后完整原始日线，时点保留实际收盘和采集时间；
    远端时钟及资产必须重新读取。这里只形成意图，发单仍经唯一执行服务。
    """
    from decimal import ROUND_DOWN

    from quant_core.contracts import PaperQueueTestContext, Quote
    from quant_core.portfolio import order_fee

    if not target.positions:
        raise RiskBlocked("策略没有目标，不能任意指定测试股票")
    position = target.positions[0]
    security = next(row for row in snapshot.securities if row.security_id == position.security_id)
    asset = transport.request("GET", f"/assets/{security.ticker}")
    if (
        asset.get("id") != security.security_id
        or asset.get("symbol") != security.ticker
        or asset.get("status") != "active"
        or asset.get("tradable") is not True
    ):
        raise RiskBlocked("测试证券当前身份或交易资格已改变")
    remote_clock = transport.request("GET", "/clock")
    if remote_clock.get("is_open") is not False:
        raise RiskBlocked("休市撤单测试不允许在开市时发单")
    remote_at = datetime.fromisoformat(remote_clock["timestamp"])
    if remote_at.utcoffset() is None:
        raise ContractError("远端时钟必须明确时区，不能按本机时区猜测")
    now = clock.now()
    completed = [
        day
        for day in calendar.sessions(config.history_start, now.date())
        if calendar.close_at(day) <= now
    ]
    if not completed:
        raise RiskBlocked("没有完整交易日可作为排队测试参考")
    session = completed[-1]
    rows = [
        row
        for row in snapshot.records
        if row.security_id == security.security_id and row.session == session
    ]
    if len(rows) != 1 or rows[0].quality != "good":
        raise RiskBlocked("最新完整交易日原始参考价缺失或不唯一")
    reference = rows[0]
    next_open = calendar.open_at(calendar.next_session(session))
    if datetime.fromisoformat(remote_clock["next_open"]) != next_open:
        raise RiskBlocked("远端下一开盘与保存日历不一致")
    price = Decimal(str(reference.raw_close))
    tick = Decimal("0.01") if price >= 1 else Decimal("0.0001")
    client_id = (
        "queue-" + canonical_hash({"plan": plan["plan_id"], "security": security.security_id})[:32]
    )
    context = PaperQueueTestContext(
        plan_id=plan["plan_id"],
        decision_id=target.decision_id,
        account_id=config.account_id,
        security_id=security.security_id,
        client_order_id=client_id,
        reference_session=session,
        reference_close=price,
        reference_close_at=calendar.close_at(session),
        reference_observed_at=reference.available_at,
        # Alpaca时钟带纽约偏移；仅转换同一真实时刻为契约UTC，不改为本地抓取时间。
        remote_clock_at=remote_at.astimezone(UTC),
        remote_is_open=remote_clock["is_open"],
        next_open=next_open,
    )
    intent = OrderIntent(
        client_order_id=client_id,
        account_id=config.account_id,
        decision_id=target.decision_id,
        security_id=security.security_id,
        side="BUY",
        quantity=1,
        limit_price=price.quantize(tick, rounding=ROUND_DOWN),
        reserved_fee=order_fee(1, config),
        created_at=now,
        eligible_at=next_open,
        strategy_version="ma-trend-1.0.0",
    )
    quotes = {
        security.security_id: Quote(
            security_id=security.security_id, at=context.reference_close_at, price=price
        )
    }
    volumes = [
        row.volume
        for row in snapshot.records
        if row.security_id == security.security_id and row.session in completed[-20:]
    ]
    if len(volumes) != 20:
        raise RiskBlocked("排队测试仍需完整20日原始成交量")
    return context, intent, quotes, {security.security_id: sum(volumes) / 20}


def _queue_unsent_proof(root: Path, legacy_source: Path) -> "QueuePreflightRejectionProof":
    """核验已知旧版预提交失败的完整源码和观察材料，不以远端404代替未发送证明。"""
    from quant_core.contracts import QueuePreflightRejectionProof

    # 旧代码树必须精确匹配失败时的封印；缺文件、增文件或代码差异都不能用此维护。
    paths = sorted(
        [
            *legacy_source.glob("src/**/*.py"),
            *legacy_source.glob("tools/**/*.py"),
            *legacy_source.glob("configs/*.toml"),
        ]
    )
    hashes = {str(path.relative_to(legacy_source)): hash_file(path) for path in paths}
    source_hash = canonical_hash(hashes)
    original = root / "observations/0001"
    plan = read_json(root / "paper-plan.json")
    authorization = read_json(original / "authorization-boundaries.json")
    failure = read_json(original / "failure.json")
    marker = read_json(root / "queue-test.json")
    if (
        plan["code"]["source_hash"] != source_hash
        or authorization["current_code"]["source_hash"] != source_hash
        or authorization.get("approved_plan") != plan["plan_id"]
        or authorization.get("queue_cancel_test") is not True
        or authorization.get("max_orders") != 1
        or authorization.get("unfilled") != "cancel"
        or failure.get("type") != "ContractError"
        or marker.get("plan_id") != plan["plan_id"]
        or (original / "queue-submission.json").exists()
    ):
        raise ContractError("原始观察不能证明此旧版意图在POST前被拒绝")
    evidence = {
        "source_files": hashes,
        "plan": plan,
        "authorization": authorization,
        "failure": failure,
        "marker": marker,
    }
    return QueuePreflightRejectionProof.model_validate(
        {
            "plan_id": plan["plan_id"],
            "client_order_id": marker["client_order_id"],
            "failed_source_hash": source_hash,
            "failure_reason": failure["reason"],
            "evidence_hash": canonical_hash(evidence),
        }
    )


def paper_execute(
    root: Path,
    transport: "AlpacaSDKTransport",
    clock: "Clock",
    *,
    approved_plan: str,
    max_orders: int,
    max_order_notional: Decimal,
    unfilled: str,
    recover_only: bool = False,
    queue_cancel_test: bool = False,
    confirm_unsent_source: Path | None = None,
    observe_seconds: int = 0,
    cancel_observe_seconds: int = 60,
    wait: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """在已获用户批准的计划边界内单次执行或恢复；唯一写入口仍是 ExecutionService。

    调用方必须已确认候选、资金预算、订单边界及未成交处理。重启使用同一目录，
    不重新分配现金、不重写计划、不为 UNKNOWN 换身份。每次观察写新子目录。
    非终态不等待伪造完成；调用方之后可显式恢复，超出首个执行日禁止普通新单。
    queue_cancel_test 是独立获准的休市订单操作测试：只取一个策略目标的一股，
    用真实收盘参考价排队后立即撤销；它不证明常规策略成交或收益，默认关闭。
    """
    # 等待仅属于应用边界；测试注入计时器，核心不读取真实时间或等待网络。
    from time import monotonic as system_monotonic
    from time import sleep

    from quant_core.adapters.alpaca import AlpacaPaperBroker
    from quant_core.adapters.alpaca_data import AlpacaCalendar
    from quant_core.adapters.paper_session import BudgetBroker
    from quant_core.contracts import PaperConfig

    wait = sleep if wait is None else wait
    monotonic = system_monotonic if monotonic is None else monotonic
    if not 0 <= observe_seconds <= 300 or not 0 <= cancel_observe_seconds <= 60:
        raise ContractError("观察仅允许0至300秒，撤单核对仅允许0至60秒")
    plan = read_json(root / "paper-plan.json")
    payload = {key: value for key, value in plan.items() if key != "plan_id"}
    if approved_plan != plan["plan_id"] or canonical_hash(payload) != approved_plan:
        raise ContractError("批准的计划身份与输入不一致")
    if validate_artifacts(root, plan["files"]):
        raise ContractError("Paper 计划输入已改变")
    if not recover_only and plan["code"]["source_hash"] != code_evidence()["source_hash"]:
        raise ContractError("计划后源码已改变，需重新检查并批准新计划")
    if (
        unfilled not in {"keep", "cancel"}
        or max_orders < 1
        or not max_order_notional.is_finite()
        or max_order_notional <= 0
    ):
        raise ContractError("必须明确订单数量、金额边界和未成交处理方式")
    config = PaperConfig.model_validate(read_json(root / "config.json"))
    if config.strategy == "ma-trend" and (max_orders > 3 or max_order_notional > Decimal("500")):
        raise ContractError("MA本轮授权边界为最多3笔且每笔含费用不超过500 USD")
    queue_marker = root / "queue-test.json"
    queue_mode = queue_cancel_test or queue_marker.exists()
    if queue_mode:
        if config.strategy != "ma-trend" or max_orders != 1 or unfilled != "cancel":
            raise ContractError("休市排队测试仅允许MA、最多一笔且必须撤销余单")
        if not recover_only and not queue_cancel_test:
            raise ContractError("排队测试目录不能转为普通策略发单；只能显式恢复或同模式核对")
        if queue_marker.exists() and read_json(queue_marker)["plan_id"] != approved_plan:
            raise ContractError("排队测试与批准计划不一致")
    initial = AccountSnapshot.model_validate(read_json(root / "initial.json"))
    snapshot = DataSnapshot.model_validate(read_json(root / "snapshot.json"))
    target = TargetPortfolio.model_validate(read_json(root / "target.json"))
    meta = read_json(root / "read-result.json")
    calendar = AlpacaCalendar(
        read_json(root / "calendar.json"),
        config.history_start,
        date.fromisoformat(meta["calendar_end"]),
    )
    store = SQLiteEventStore(root / "internal.sqlite", initial)
    if queue_mode:
        if store.orders() and not queue_marker.exists():
            raise ContractError("已有普通订单的目录不能转为排队测试")
        if queue_marker.exists() and any(
            row.intent.client_order_id != read_json(queue_marker).get("client_order_id")
            for row in store.orders()
        ):
            raise ContractError("排队测试目录存在未绑定的订单，禁止操作")
    broker = AlpacaPaperBroker(
        transport,
        config.account_id,
        clock,
        {row.security_id: row.ticker for row in snapshot.securities},
        lambda: [row.intent for row in store.orders()],
        initial.as_of,
        trading_enabled=True,
        baseline_activities=read_json(root / "account-observation.json")["activities"],
    )
    scoped = BudgetBroker(
        broker,
        Decimal(plan["reserve"]),
        set(broker.symbols),
        max_orders,
        max_order_notional,
    )
    service = ExecutionService(store, scoped, clock, config, calendar)
    attempt = root / "observations" / f"{len(list((root / 'observations').glob('*'))) + 1:04d}"
    attempt.mkdir(parents=True, exist_ok=False)
    write_json(
        attempt / "authorization-boundaries.json",
        {
            "approved_plan": approved_plan,
            "max_orders": max_orders,
            "max_order_notional": str(max_order_notional),
            "unfilled": unfilled,
            "recover_only": recover_only,
            "queue_cancel_test": queue_mode,
            "observe_seconds": observe_seconds,
            "cancel_observe_seconds": cancel_observe_seconds,
            "current_code": code_evidence(),
        },
    )
    if confirm_unsent_source is not None:
        if not recover_only or not queue_mode:
            raise ContractError("旧版未发送维护只允许显式恢复排队测试")
        proof = _queue_unsent_proof(root, confirm_unsent_source)
        write_json(attempt / "unsent-proof.json", proof.model_dump(mode="json"))
        # 服务在账户锁内再核验本地日志和远端事实，只追加本地拒绝，不修改账务。
        service.confirm_queue_not_sent(proof.client_order_id, proof=proof)
    checked = service.recover()
    risks: list[dict[str, Any]] = []
    status = "recovery_only"
    try:
        if not checked.matched:
            raise RiskBlocked("Paper 初次恢复不一致，禁止新增")
        now = clock.now()
        if not recover_only and queue_mode:
            # 已有意图包括未知、拒绝和撤销终态，均只恢复，不能再次生成排队测试订单。
            if not store.orders():
                context, intent, quotes, adv = _paper_queue_order(
                    snapshot, target, plan, transport, clock, calendar, config
                )
                write_json(attempt / "queue-context.json", context.model_dump(mode="json"))
                if not queue_marker.exists():
                    write_json(
                        queue_marker,
                        {
                            "plan_id": approved_plan,
                            "purpose": context.purpose,
                            "client_order_id": context.client_order_id,
                        },
                    )
                # 适配器也只接受该单上下文；不会解除其他订单的开盘资格检查。
                broker.queue_test_context = context
                record = service.submit_paper_queue_test(
                    intent,
                    quotes,
                    context=context,
                    target=target,
                    security_records=snapshot.securities,
                    adv=adv,
                    reference_nav=target.nav,
                    peak_nav=target.nav,
                )
                risks.append(
                    {
                        "client_order_id": intent.client_order_id,
                        "allowed": True,
                        "status": record.status,
                        "purpose": context.purpose,
                    }
                )
                # 提交回执与再次查询分别保存。查询不确定时也保留原身份，后面只恢复/撤单。
                write_json(attempt / "queue-submission.json", record.model_dump(mode="json"))
                confirmed = broker.query(intent.client_order_id)
                write_json(
                    attempt / "queue-query.json",
                    confirmed.model_dump(mode="json") if confirmed else None,
                )
                write_json(attempt / "queue-before-cancel.json", broker.read_snapshot())
                status = "queue_order_observed"
        elif not recover_only:
            if (
                not datetime.fromisoformat(plan["eligible_at"])
                <= now
                < datetime.fromisoformat(plan["expires_at"])
            ):
                raise RiskBlocked("市场未到首个执行时段或计划已过期")
            remote_clock = transport.request("GET", "/clock")
            if remote_clock.get("is_open") is not True or not calendar.is_open(now):
                raise RiskBlocked("远端时钟或保存日历显示常规市场未开盘")
            # 每个执行批次重新检查当前资产可交易属性；行业仍用当时已知补充证据。
            current_assets = {row["id"]: row for row in broker.assets()}
            if any(
                current_assets[row.security_id].get("tradable") is not True
                or current_assets[row.security_id].get("status") != "active"
                for row in snapshot.securities
            ):
                raise RiskBlocked("当前证券资格已改变")
            quotes = _paper_quotes(transport, snapshot.securities, config.quote_feed)
            completed = [
                day
                for day in calendar.sessions(config.history_start, snapshot.decision_time.date())
                if calendar.close_at(day) <= snapshot.decision_time
            ][-20:]
            adv = {
                row.security_id: sum(
                    record.volume
                    for record in snapshot.records
                    if record.security_id == row.security_id and record.session in completed
                )
                / 20
                for row in snapshot.securities
            }
            intents = plan_orders(
                target,
                store.account(now),
                store.orders(),
                quotes,
                adv,
                config,
                now,
                security_records=snapshot.securities,
                turnover_used=_turnover(store, target.decision_id),
            )
            for intent in intents:
                # 持久化前适配 Alpaca 最小价格步长：买入只向下舍入，绝不扩大价格风险。
                # 客户订单身份仅压缩既有稳定键，不因报价变化而换键；已有意图只恢复。
                tick = Decimal("0.01") if intent.limit_price >= 1 else Decimal("0.0001")
                from decimal import ROUND_DOWN

                price = intent.limit_price.quantize(tick, rounding=ROUND_DOWN)
                intent = OrderIntent.model_validate(
                    {
                        **intent.model_dump(),
                        "limit_price": price,
                        "client_order_id": intent.client_order_id[:40],
                    }
                )
                if any(
                    row.intent.client_order_id == intent.client_order_id
                    or (
                        row.intent.decision_id == intent.decision_id
                        and row.intent.security_id == intent.security_id
                        and row.intent.side == intent.side
                    )
                    for row in store.orders()
                ):
                    # 一次执行每证券只允许一张意图；部分成交后取消也不能隐式重开余量。
                    continue
                if len([row for row in store.orders() if row.broker_order_id]) >= max_orders:
                    status = "order_count_boundary"
                    break
                if intent.quantity * intent.limit_price + intent.reserved_fee > max_order_notional:
                    risks.append(
                        {
                            "client_order_id": intent.client_order_id,
                            "allowed": False,
                            "reasons": ["approval_notional_boundary"],
                        }
                    )
                    continue
                # 报价在每单前更新，时效仍由原风控检查。失败不改预算或放宽原参数。
                quotes = _paper_quotes(transport, snapshot.securities, config.quote_feed)
                try:
                    record = service.submit(
                        intent,
                        quotes,
                        target=target,
                        security_records=snapshot.securities,
                        adv=adv,
                        reference_nav=target.nav,
                        peak_nav=target.nav,
                    )
                    risks.append(
                        {
                            "client_order_id": intent.client_order_id,
                            "allowed": True,
                            "status": record.status,
                        }
                    )
                    status = "orders_observed"
                    if record.status == "UNKNOWN":
                        break
                except RiskBlocked as exc:
                    risks.append(
                        {
                            "client_order_id": intent.client_order_id,
                            "allowed": False,
                            "reasons": [str(exc)],
                        }
                    )
            if not intents:
                status = "no_orders"
        checked = service.recover()
        # 一次授权批次只发送前面的目标；观察期间只读取，不追单、改单或补发余量。
        if not recover_only and observe_seconds and not queue_mode:
            deadline = monotonic() + observe_seconds
            while checked.matched and any(
                row.status not in {"FILLED", "REJECTED", "CANCELED"} for row in store.orders()
            ):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                wait(min(5.0, remaining))
                checked = service.recover()
        if unfilled == "cancel" and checked.matched and not queue_mode:
            for order in store.orders():
                if order.status in {"OPEN", "PARTIAL"}:
                    service.cancel(order.intent.client_order_id)
            checked = service.recover()
            # 撤单回执不代表终态；有观察请求才等待，兼容原程序化单次查询入口。
            if not recover_only and observe_seconds:
                deadline = monotonic() + cancel_observe_seconds
                while checked.matched and any(
                    row.status not in {"FILLED", "REJECTED", "CANCELED"} for row in store.orders()
                ):
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        break
                    wait(min(5.0, remaining))
                    checked = service.recover()
    except (RiskBlocked, ContractError, ConnectionError, TimeoutError) as exc:
        status = "blocked"
        write_json(attempt / "failure.json", {"type": type(exc).__name__, "reason": str(exc)})
    finally:
        if queue_mode:
            write_json(attempt / "queue-clock-checks.json", broker.queue_clock_samples)
        # 报告读取内存与持久化事实，不消除差异；取不到远端时仍保存本地待恢复证据。
        checked = service.recover()
        if queue_mode:
            # 排队测试即使提交后取证失败，也在恢复确认身份后尝试撤销本轮单。
            # 已知本轮单即使现金不一致也可撤销；执行服务核验身份与授权。
            # 不重复请求CANCEL_PENDING；未知订单保持待恢复，不能盲目重发或假称撤销。
            try:
                for order in store.orders():
                    if order.status in {"OPEN", "PARTIAL"}:
                        service.cancel(order.intent.client_order_id)
                checked = service.recover()
                deadline = monotonic() + cancel_observe_seconds
                while checked.matched and any(
                    row.status not in {"FILLED", "REJECTED", "CANCELED"} for row in store.orders()
                ):
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        break
                    wait(min(5.0, remaining))
                    checked = service.recover()
            except (RiskBlocked, ContractError, ConnectionError, TimeoutError) as exc:
                status = "blocked"
                write_json(
                    attempt / "cancel-failure.json",
                    {"type": type(exc).__name__, "reason": str(exc)},
                )
                checked = service.recover()
        if not checked.matched:
            status = "blocked"
        account = store.account(clock.now())
        orders = store.orders()
        for name, value in (
            ("account", account.model_dump(mode="json")),
            ("orders", [row.model_dump(mode="json") for row in orders]),
            ("events", [row.model_dump(mode="json") for row in store.events()]),
            ("journal", [row.model_dump(mode="json") for row in store.journal()]),
            ("reconciliation", checked.model_dump(mode="json")),
            ("risk", risks),
        ):
            write_json(attempt / f"{name}.json", value)
        try:
            write_json(attempt / "remote.json", broker.read_snapshot())
        except (ContractError, ConnectionError, TimeoutError):
            status = "remote_observation_failed"
        summary = {
            "status": status,
            "reconciled": checked.matched,
            "submitted": sum(row.broker_order_id is not None for row in orders),
            "filled": sum(row.status == "FILLED" for row in orders),
            "pending": sum(
                row.status in {"OPEN", "PARTIAL", "UNKNOWN", "CANCEL_PENDING"} for row in orders
            ),
            "reserve": plan["reserve"],
            "as_of": clock.now().isoformat(),
            "execution_purpose": "paper_queue_test" if queue_mode else "strategy",
            "queue_cancel_verified": queue_mode
            and checked.matched
            and len(orders) == 1
            and orders[0].broker_order_id is not None
            and orders[0].status == "CANCELED"
            and orders[0].filled_quantity == 0
            and account.cash == initial.cash
            and account.positions == initial.positions,
            "queue_restart_verified": queue_mode
            and recover_only
            and checked.matched
            and len(orders) == 1
            and orders[0].broker_order_id is not None
            and orders[0].status == "CANCELED"
            and orders[0].filled_quantity == 0
            and account.cash == initial.cash
            and account.positions == initial.positions,
            "filled_and_reconciled": not queue_mode
            and checked.matched
            and any(row.status == "FILLED" for row in orders)
            and all(row.status in {"FILLED", "REJECTED", "CANCELED"} for row in orders),
            "restart_verified": not queue_mode
            and recover_only
            and checked.matched
            and any(row.status == "FILLED" for row in orders)
            and all(row.status in {"FILLED", "REJECTED", "CANCELED"} for row in orders),
            "output": str(attempt),
        }
        write_json(attempt / "result.json", summary)
        factors = [FactorValue.model_validate(row) for row in read_json(root / "factors.json")]
        signals = SignalSet.model_validate(read_json(root / "signals.json"))
        regime = RegimeAssessment.model_validate(read_json(root / "regime.json"))
        (attempt / "report.md").write_text(
            render_report(
                snapshot,
                factors,
                signals,
                regime,
                target,
                orders,
                account,
                checked,
                [],
                mode="paper",
            )
            + "\n\n内部现金为分配的策略现金，"
            + f"加未分配现金 {plan['reserve']} USD 后才与远端全部现金比较。\n"
            + "逐单风险原因见 risk.json；市场状态缺基准保持 UNKNOWN，不改变预算。\n"
            + (
                "本次为休市提交/查询/撤单测试，收盘参考价不是实时报价；取消成功不等于成交验收。\n"
                if queue_mode
                else ""
            )
            + json.dumps(summary, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return summary
