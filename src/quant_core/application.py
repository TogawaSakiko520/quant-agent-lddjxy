"""离线应用编排；连接纯业务和显式适配器，保存全部证据，不持有真实交易权限。

CLI 将配置和目录交给本模块；本模块把行情→因子→评分→目标→订单→账户串起来。
演示/回放生成新运行，校验只读原产物并借临时库重算，研究另存实验，不把报告当交易事实。
"""

import platform
import shutil
import subprocess
from datetime import date, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

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
    packages = ("numpy", "pandas", "pydantic", "pyarrow", "exchange-calendars")
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
