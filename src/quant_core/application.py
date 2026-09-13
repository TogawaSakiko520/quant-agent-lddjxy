"""离线应用编排；连接纯业务和显式适配器，保存全部证据，不持有真实交易权限。

演示、校验、回放和研究分别写入受控产物；任何失败都不能冒充成功交易。
"""

# 文件清单和命令输出使用标准 JSON。
# 环境版本是复现证据的一部分。
import platform

# 回放复制经哈希验证的原始证据到新目录。
import shutil

# 仅应用边界读取 Git 元数据，不调用交易或网络命令。
import subprocess

# 业务时刻从固定日历构造，不读取今天。
from datetime import date, datetime, timedelta

# 金额与费用使用十进制。
from decimal import Decimal

# 依赖实际版本随运行清单保存。
from importlib.metadata import version

# 所有产物路径由 CLI 明确注入。
from pathlib import Path

# 临时状态库只用于独立语义校验，退出时清理，不改源运行。
from tempfile import TemporaryDirectory

# Any 仅用于 JSON 边界输出。
from typing import Any

# 日历和时钟由适配器显式组装。
from quant_core.adapters.calendar import ExchangeCalendar, FixedClock

# 固定数据不访问网络。
from quant_core.adapters.datasets import fixture_quotes, generate_fixture

# 内外部状态分别存储，禁止共享账本。
from quant_core.adapters.fake_broker import FakeBroker

# 唯一内部状态写入适配器。
from quant_core.adapters.sqlite_store import SQLiteEventStore

# 文件读写和哈希集中在外部边界。
# 数据库只读校验不通过构造券商适配器修改源文件。
from quant_core.adapters.storage import (
    hash_file,
    read_json,
    read_market_parquet,
    read_sqlite_state,
    validate_artifacts,
    write_json,
    write_market_parquet,
)

# 公共对象是所有子系统交换的唯一结构定义。
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

# 时间查询先于所有因子计算。
from quant_core.data import build_snapshot

# 交易副作用必须经过唯一服务。
from quant_core.execution import ExecutionService

# 因子计算不读取事后研究标签。
from quant_core.factors import calculate_factors

# 独立告警与日报分开生成。
from quant_core.monitoring import Alert, assess_health

# 组合目标与订单计划分开计算。
from quant_core.portfolio import build_portfolio, plan_orders

# 市场状态只观察，不更改风险预算。
from quant_core.regime import assess_regime

# 报告从已保存事实生成。
from quant_core.reporting import render_report

# 研究是独立应用，不将标签送回执行。
from quant_core.research import evaluate_research, label_observations, rolling_splits

# 预提交风险证据与执行服务使用同一规则。
from quant_core.risk import assess_order

# 固定评分生成可追溯决策。
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
    """构造带前后缓冲的固定日历；返回适配器，异常由日期配置决定，不联网。"""
    # 后续执行和五交易日标签都需要显式日历覆盖。
    return ExchangeCalendar(config.start - timedelta(days=14), config.end + timedelta(days=31))


def weekly_sessions(calendar: ExchangeCalendar, start: date, end: date) -> list[date]:
    """找出范围内每周最后交易日；假日周以真实下一交易日判断，无副作用。"""
    # 实际交易日列表是调仓调度唯一依据。
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
    # 默认记录无法确认的版本，不能用空提交假装干净环境。
    commit = "unavailable"
    # 未知工作树默认标为脏，禁止误判可重现发布版本。
    dirty = True
    # Git 只读命令不需要网络或凭证。
    if (root / ".git").exists():
        # 读取当前提交，不创建提交或修改索引。
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        # 未跟踪代码也必须在工作树状态中出现。
        dirty = bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
        )
    # 指纹涵盖未提交的实际 Python 和配置，避免只依赖初始提交号。
    candidates = sorted(
        [*root.glob("src/**/*.py"), *root.glob("tools/**/*.py"), *root.glob("configs/*.toml")]
    )
    # 只有实际存在的文件进入指纹。
    hashes = {str(path.relative_to(root)): hash_file(path) for path in candidates if path.is_file()}
    # 工作树脏状态不妨碍离线演示，但必须留证据。
    return {"commit": commit, "dirty": dirty, "source_hash": canonical_hash(hashes)}


def runtime_evidence() -> dict[str, str]:
    """返回实际 Python/依赖/锁文件证据；缺锁明确标记，无交易副作用。"""
    # 复现记录不依赖远端最新包版本。
    packages = ("numpy", "pandas", "pydantic", "pyarrow", "exchange-calendars")
    # 每个实际版本从已安装元数据读取。
    result = {package: version(package) for package in packages}
    # Python补丁版本与平台共同记录。
    result.update(python=platform.python_version(), platform=platform.platform())
    # 锁文件位于本地开发仓库根。
    lock = Path(__file__).resolve().parents[2] / "uv.lock"
    # 打包分发若未携带锁文件需诚实显示缺失。
    result["lock_hash"] = hash_file(lock) if lock.is_file() else "unavailable"
    # 固定数值比较约定，回放以类型和业务结果为准。
    result["float_tolerance"] = "relative=1e-12, absolute=1e-12; money=Decimal exact"
    # 不收集任何凭据或完整环境变量。
    return result


def _seal_run(output: Path, manifest: RunManifest) -> RunManifest:
    """对当前运行所有产物封印并独占写入清单；已有清单抛错，有文件副作用。"""
    # 清单不能把自身哈希纳入自身而产生循环定义。
    artifacts = {
        str(path.relative_to(output)): hash_file(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    # 重建模型保留严格结构与字段验证。
    sealed = RunManifest.model_validate({**manifest.model_dump(), "artifacts": artifacts})
    # 清单是完整运行最后写入的成功证据。
    write_json(output / "manifest.json", sealed.model_dump(mode="json"))
    # 返回最终清单供CLI输出摘要。
    return sealed


def _turnover(store: SQLiteEventStore, decision_id: str) -> Decimal:
    """汇总本决策唯一成交的真实金额；返回美元总额，不使用限价替代成交价。"""
    # 把意图决策身份映射到实际成交。
    identities = {
        record.intent.client_order_id
        for record in store.orders()
        if record.intent.decision_id == decision_id
    }
    # 同一成交可能有多个传输event_id，按来源成交身份去重。
    unique = {
        (event.source, event.fill_id): event
        for event in store.events()
        if isinstance(event, FillEvent) and event.client_order_id in identities
    }
    # 买卖总額相加而非抵销。
    return sum((event.price * event.quantity for event in unique.values()), Decimal("0"))


def run_demo(config: DemoConfig, output: Path) -> RunManifest:
    """建立完整离线运行；返回封印清单，任何失败抛错且不创建成功清单，有文件副作用。"""
    # 在运行开始冻结源码证据，避免并行编辑后把新文件误标为已执行代码。
    captured_code = code_evidence()
    # 拒绝覆盖旧运行，原始证据必须保持不可变。
    output.mkdir(parents=True, exist_ok=False)
    # 显式日历覆盖样本、后续执行和研究标签。
    calendar = make_calendar(config)
    # 无网络固定数据与证券主表。
    records, securities = generate_fixture(config, calendar)
    # 每周末调度依实际市场日历计算。
    eligible_decisions = weekly_sessions(calendar, config.start, config.end)
    # 太短的日期范围不能伪造周调仓时点。
    if not eligible_decisions:
        # 已创建目录中不会出现成功运行清单。
        raise ContractError("样本没有已完成的周调仓交易日")
    # 首次演示只执行最后一个合格周调仓决策。
    decision_day = eligible_decisions[-1]
    # 留十五分钟固定接入延迟后再留十五分钟验证窗口。
    decision_time = calendar.close_at(decision_day) + timedelta(minutes=30)
    # 历史快照只取当时已可用版本。
    snapshot = build_snapshot(records, securities, decision_time, calendar.version)
    # 保存完整原始样本与主表，研究可用事后部分但策略不可用。
    write_market_parquet(output / "inputs/market.parquet", records)
    # 主表原文属于不可覆盖运行输入。
    write_json(
        output / "inputs/securities.json", [item.model_dump(mode="json") for item in securities]
    )
    # 决策报价只来自历史快照的原始收盘。
    decision_quotes = fixture_quotes(snapshot.records, calendar, execution=False)
    # 下一时段执行报价单独生成并留证据，不能反向影响决策。
    execution_quotes = fixture_quotes(snapshot.records, calendar, execution=True)
    # 输出排序固定，避免字典次序影响证据文件。
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
    # 两因子分别校验完整交易日窗口。
    factors = calculate_factors(snapshot, calendar)
    # 标准化评分是唯一策略选择依据。
    signals = score_factors(snapshot, factors)
    # 独立基准只影响观察报告。
    regime = assess_regime(
        [item for item in snapshot.records if item.security_id == "BENCH"], decision_time, calendar
    )
    # 组合以当时账户和报价决定整股目标。
    target = build_portfolio(signals, initial, decision_quotes, snapshot.securities, config)
    # 决策证据必须在发送任何意图之前写入。
    for name, model in (
        ("snapshot", snapshot),
        ("signals", signals),
        ("regime", regime),
        ("target", target),
    ):
        # 每个职责有自己的证据文件。
        write_json(output / f"{name}.json", model.model_dump(mode="json"))
    # 原始因子与标准化信号分开保存。
    write_json(output / "factors.json", [item.model_dump(mode="json") for item in factors])
    # 下一时段开盘是明确注入的执行时刻。
    execution_at = calendar.open_at(calendar.next_session(decision_day))
    # 固定时钟不访问系统时间。
    clock = FixedClock(execution_at)
    # 内部和券商存储使用两个独立文件与实现。
    store = SQLiteEventStore(output / "internal.sqlite", initial)
    # 模拟券商也接收日历，盘后fill必须被拒绝。
    broker = FakeBroker(output / "broker.sqlite", clock, config, initial, calendar=calendar)
    # 唯一执行服务组装全部依赖。
    service = ExecutionService(store, broker, clock, config, calendar)
    # 流动性依据只来自策略已知的最近20个交易日。
    latest_sessions = calendar.sessions(config.start, decision_day)[-20:]
    # 每个证券必须具备完整20日成交量，否则不给ADV预算。
    adv: dict[str, float] = {}
    # 汇总历史股票池与基准，基准不会生成订单。
    for security in snapshot.securities:
        # 同一证券只读取已批准快照中的原始成交量。
        volumes = [
            record.volume
            for record in snapshot.records
            if record.security_id == security.security_id and record.session in latest_sessions
        ]
        # 不把不足20日的平均值冒充ADV20。
        if len(volumes) == 20:
            # 转成股数均值供组合和提交风控共同使用。
            adv[security.security_id] = sum(volumes) / 20
    # 保存每次实际提交前风险判断，包含拒绝原因。
    risks: list[dict[str, Any]] = []
    # 最多两次规划：第二次只使用第一轮已确认成交释放的现金与额度。
    for _ in range(2):
        # 任何新风险之前先恢复并完成独立对账。
        reconciliation = service.recover()
        # 不一致账户不能继续正常交易。
        if not reconciliation.matched:
            # 拒绝盲目修改账本来对平现金。
            raise RiskBlocked("；".join(reconciliation.differences))
        # 实际成交美元额固定换手证据，不采用订单限价。
        turnover = _turnover(store, signals.decision_id)
        # 当前账户与挂单净额生成可执行候选。
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
        # 没有剩余合格意图就结束，不反复修改参数补齐仓位。
        if not intents:
            # 未达到目标的事实由监控报告解释。
            break
        # 候选已经按卖出优先与评分顺序确定。
        for intent in intents:
            # 提交前重新读取账户和实际累计换手。
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
            # 对具体订单保存风险判断依据。
            risks.append(
                {
                    "client_order_id": intent.client_order_id,
                    "at": execution_at.isoformat(),
                    "reference_nav": str(target.nav),
                    "peak_nav": str(target.nav),
                    **risk.model_dump(mode="json"),
                }
            )
            # 不让直接模拟fill绕过交易服务再次核验。
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
                # 未知、拒单不假装成功成交。
                raise RiskBlocked(f"订单未明确受理：{order.status}")
            # FakeBroker使用下一时段原始报价，并独立维护账户。
            broker.fill(intent.client_order_id, execution_quotes[intent.security_id])
            # 每次成交后消费事件与独立对账，再考虑下一单。
            checked = service.recover()
            # 成交差异不容许被后续交易掩盖。
            if not checked.matched:
                # 保留已提交数据库事实，等待恢复。
                raise RiskBlocked("；".join(checked.differences))
    # 盘后工程验收明确比较独立状态。
    reconciliation = service.recover()
    # 最终账户来自内部唯一事件投影。
    account = store.account(execution_at)
    # 订单状态与独立成交事件分开输出。
    orders = store.orders()
    # 双因子共同覆盖率用于工程观察。
    coverage = len(signals.scores) / config.securities
    # 独立告警留存真实任务完成情况。
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
    # 对账结果提供机器可判断结论。
    write_json(output / "reconciliation.json", reconciliation.model_dump(mode="json"))
    # 已持久化意图及最终投影为人提供快速索引。
    write_json(output / "orders.json", [item.model_dump(mode="json") for item in orders])
    # 全部原始传输事件保留重复语义。
    write_json(output / "events.json", [item.model_dump(mode="json") for item in store.events()])
    # 有序事务证据包含公司行动与意图的真实交错。
    write_json(output / "journal.json", [item.model_dump(mode="json") for item in store.journal()])
    # 风险判断不是凭空声称通过。
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
        # 保留已产生证据并要求使用稳定源码在新目录重跑。
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
    # 成功清单代表所有规定产物已经写入。
    return _seal_run(output, manifest)


def replay_journal(store: SQLiteEventStore, journal: list[JournalEntry]) -> None:
    """按连续事务序号重建新状态库；错误抛 ContractError，不触发券商提交。"""
    # 每条记录依原顺序进入同一存储接口。
    for number, entry in enumerate(journal, start=1):
        # 缺失或重复序号不能静默重排。
        if entry.sequence != number:
            # 回放拒绝不完整证据链。
            raise ContractError("事务日志序号不连续")
        # 先保存意图的操作保留发送前持久化语义。
        if entry.operation == "intent" and isinstance(entry.payload, OrderIntent):
            # 这只写本地新库，不访问券商。
            store.save_intent(entry.payload)
        # 券商确认的投影操作与现金事实分开。
        elif entry.operation == "order" and isinstance(entry.payload, OrderRecord):
            # 状态必须继续满足订单契约。
            store.record_order(entry.payload)
        # 真实输入事件由幂等处理器重放。
        elif entry.operation == "event" and isinstance(entry.payload, OrderEvent | FillEvent):
            # 重复成交仍只能入账一次。
            store.apply(entry.payload)
        # 公司行动按原处理时点应用，不能提前计息或拆股。
        elif entry.operation == "action" and isinstance(entry.payload, CorporateAction):
            # 显式时间禁止调用真实系统时钟。
            store.apply_action(entry.payload, entry.at)
        # 枚举与载荷错配属于损坏，而非可跳过记录。
        else:
            # 双层检查防止后续扩展错误绕过回放分派。
            raise ContractError("事务操作与载荷不匹配")


def validate_run(root: Path) -> RunManifest:
    """核验运行文件与核心对象；返回清单，缺失/篡改/契约错误抛异常，不写原目录。"""
    # 清单也必须通过统一契约版本和结构校验。
    manifest = RunManifest.model_validate(read_json(root / "manifest.json"))
    # 防止空清单或删掉故障文件的引用后获得通过。
    if missing := REQUIRED_ARTIFACTS - set(manifest.artifacts):
        # 成功运行必须保留完整链路证据。
        raise ContractError("运行缺少必要产物：" + ",".join(sorted(missing)))
    # 首先逐文件核验哈希和路径边界。
    if errors := validate_artifacts(root, manifest.artifacts):
        # 不对原始证据自动重签或修补。
        raise ContractError("；".join(errors))
    # 重新读取原始数据而非只相信报告。
    records = read_market_parquet(root / "inputs/market.parquet")
    # 证券主表也属于时点输入。
    securities = [
        SecurityRecord.model_validate(item) for item in read_json(root / "inputs/securities.json")
    ]
    # 从原始输入重新计算时点快照，验证版本选择未被改写。
    reconstructed = build_snapshot(
        records, securities, manifest.decision_time, manifest.inputs["calendar_version"]
    )
    # 已保存快照必须同样通过类型验证。
    stored = DataSnapshot.model_validate(read_json(root / "snapshot.json"))
    # 文件字节一致之外再校验语义链路。
    if reconstructed != stored or reconstructed.content_hash != manifest.inputs["snapshot_hash"]:
        # 不允许报告与输入选择分叉。
        raise ContractError("快照与原始时点数据不一致")
    # 核验最终账户与对账类型，防止格式合法但缺核心字段。
    expected_account = AccountSnapshot.model_validate(read_json(root / "account.json"))
    # 对账失败不能被标为成功运行。
    reconciliation = ReconciliationResult.model_validate(read_json(root / "reconciliation.json"))
    # 正常完成的演示必须独立对账通过。
    if not reconciliation.matched:
        # 保留风险阻断与输入损坏的区别。
        raise RiskBlocked("运行存在未解释的对账差异")
    # 即使有人重签导出JSON哈希，语义也必须与原始日志和独立状态相符。
    journal = [JournalEntry.model_validate(item) for item in read_json(root / "journal.json")]
    # 订单导出不能省略拒单或未完成单来伪装成功。
    expected_orders = [OrderRecord.model_validate(item) for item in read_json(root / "orders.json")]
    # 临时库从原始账户开始，不复制生产投影作为期望。
    with TemporaryDirectory(prefix="quant-validate-") as temporary:
        # 临时写入严格隔离于源运行。
        store = SQLiteEventStore(Path(temporary) / "reconstructed.sqlite", manifest.initial_account)
        # 有序回放包括公司行动、重复传输和部分成交。
        replay_journal(store, journal)
        # 现金、成本、费用和持仓必须完全符合日志。
        if (
            store.account(expected_account.as_of) != expected_account
            or store.orders() != expected_orders
        ):
            # 不能通过修改清单使错误账户获得正常报告。
            raise ContractError("账户或订单与事务日志不一致")
        # 原始事件导出也必须对应事务中接受的事件。
        if [item.model_dump(mode="json") for item in store.events()] != read_json(
            root / "events.json"
        ):
            # 不允许删除不利事件后继续正常展示。
            raise ContractError("事件导出与事务日志不一致")
    # 两份实际状态库都以只读连接核验，不信任导出文件代替独立对账。
    for kind in ("internal", "broker"):
        # 固定枚举值在适配器中选择固定表名。
        database_account, database_orders = read_sqlite_state(root / f"{kind}.sqlite", kind)
        # 查询时点归一化只为比较当前事实，不声称重新取得市场状态。
        normalized = database_account.model_copy(update={"as_of": expected_account.as_of})
        # 本地未发送的风险拒绝不会在券商产生订单。
        comparable_orders = [
            order
            for order in expected_orders
            if kind == "internal" or order.broker_order_id is not None
        ]
        # 全部财务字段和订单都必须独立一致。
        if normalized != expected_account or database_orders != comparable_orders:
            # 审计只能发现差异，不能反向修复事实。
            raise ContractError(f"{kind} 状态库与已导出事实不一致")
    # 校验派生说明始终对应同一份业务实现。
    calendar = make_calendar(manifest.config)
    # 因子不能只靠文件存在就被视为正确。
    actual_factors = calculate_factors(stored, calendar)
    # 评分重新使用固定的因子结果。
    actual_signals = score_factors(stored, actual_factors)
    # 目标只使用决策时初态和原始收盘。
    actual_target = build_portfolio(
        actual_signals,
        manifest.initial_account,
        fixture_quotes(stored.records, calendar, execution=False),
        stored.securities,
        manifest.config,
    )
    # 市场观察依据也需要按当时数据重算。
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
    # 每项不一致都应阻止继续生成正常报告。
    for name, value in derived.items():
        # 固定输入结果发生改变必须视为回归或版本差异。
        if not equivalent_values(value, read_json(root / f"{name}.json")):
            # 不覆盖旧行为的证据文件。
            raise ContractError(f"{name} 与固定输入重算结果不一致")
    # 清单可以被后续只读报告或回放复用。
    return manifest


def replay_run(source: Path, output: Path) -> RunManifest:
    """校验源运行并在新库按事务顺序回放；同时重算决策，无Broker提交副作用。"""
    # 未通过源数据校验时不能建立可信回放。
    manifest = validate_run(source)
    # 输出目录必须全新，原运行不可覆盖。
    output.mkdir(parents=True, exist_ok=False)
    # 保留源运行全部证据，内部状态库必须重新建立。
    for name in sorted(manifest.artifacts):
        # 本次证明不能复制旧的内部投影当作重算结果。
        if name == "internal.sqlite":
            # 其他已验证输入可以直接复制。
            continue
        # 由源清单已经验证的相对路径建立目标目录。
        destination = output / name
        # 包含输入子目录的层级需要先创建。
        destination.parent.mkdir(parents=True, exist_ok=True)
        # 复制证据不会发起券商或网络操作。
        shutil.copyfile(source / name, destination)
    # 新事件库仅以原始初态开始。
    store = SQLiteEventStore(output / "internal.sqlite", manifest.initial_account)
    # 事务记录携带公司行动和普通事件的精确顺序。
    journal = [JournalEntry.model_validate(item) for item in read_json(source / "journal.json")]
    # 调用和语义校验共用的顺序分派，仍由存储执行幂等约束。
    replay_journal(store, journal)
    # 最终比较按源账户同一查询时点进行。
    expected = AccountSnapshot.model_validate(read_json(source / "account.json"))
    # 回放账户完全来自初态与日志。
    actual = store.account(expected.as_of)
    # 订单列表也必须匹配，不能只核现金总额。
    expected_orders = [
        OrderRecord.model_validate(item) for item in read_json(source / "orders.json")
    ]
    # 重算时使用同一显式日历范围。
    calendar = make_calendar(manifest.config)
    # 回放时读取冻结快照，已经经原始输入重建验证。
    snapshot = DataSnapshot.model_validate(read_json(source / "snapshot.json"))
    # 因子重新调用唯一业务实现。
    factors = calculate_factors(snapshot, calendar)
    # 评分也重新计算而非复制原输出作为证明。
    signals = score_factors(snapshot, factors)
    # 目标只使用原决策账户与收盘报价。
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
    # 回放报告保存实际比较结果，不写入源目录。
    write_json(output / "replay-verification.json", comparisons)
    # 任一差异意味着源版本不能被当前实现等价重算。
    if not all(comparisons.values()):
        # 不更新预期输出让回放强行通过。
        raise ContractError("回放结果不一致：" + str(comparisons))
    # 重放清单记录源运行，避免假称新市场成交。
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
    # 新库和证明文件按本次字节重新封印。
    return _seal_run(output, replayed)


def report_run(root: Path) -> str:
    """校验运行后从真实结构重生报告并返回文本；不覆盖原文件，无交易副作用。"""
    # 文件损坏时禁止输出看似正常的解释。
    validate_run(root)
    # 告警是独立结构化证据，不从旧日报反推。
    alerts = [
        Alert.model_validate_json(line)
        for line in (root / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # 每个模块的实际输出重新通过契约验证。
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
    """从留存原始样本重建历次周末特征并评估；追加实验目录，不更改交易快照。"""
    # 研究开始前确认原始运行证据完好。
    manifest = validate_run(root)
    # 研究也记录本次实际代码与环境，不能只借用交易演示的旧版本。
    captured_code = code_evidence()
    # 实验只在运行的独立research目录追加。
    research_root = root / "research"
    # 目录创建是明确的本地研究副作用。
    research_root.mkdir(exist_ok=True)
    # 递增实验号保留失败与全部尝试，不覆盖最佳一次。
    number = 1
    # 独占创建保证同一实验不会覆盖旧结果。
    while (directory := research_root / f"experiment-{number:04d}").exists():
        # 已存在试验不删除、不重命名。
        number += 1
    # 后续失败也会保留本次实验目录。
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
    # 无论计算成功失败都留存对应状态。
    try:
        # 使用与交易相同的固定日历。
        calendar = make_calendar(manifest.config)
        # 完整事后行情只在研究局部用于标签。
        records = read_market_parquet(root / "inputs/market.parquet")
        # 主表版本仍按每个历史决策重新选择。
        securities = [
            SecurityRecord.model_validate(item)
            for item in read_json(root / "inputs/securities.json")
        ]
        # 累积每个周末的冻结特征，不使用标签优化。
        all_factors: list[FactorValue] = []
        # 调仓时间表与真实策略一致。
        days = weekly_sessions(calendar, manifest.config.start, manifest.config.end)
        # 每个历史决策独立按available_at查询。
        for day in days:
            # 可用延迟及验证窗口与演示完全一致。
            at = calendar.close_at(day) + timedelta(minutes=30)
            # 构造当时快照，未来修订不会影响旧特征。
            snapshot = build_snapshot(records, securities, at, calendar.version)
            # 复用唯一因子实现，不建立教学或研究副本。
            all_factors.extend(calculate_factors(snapshot, calendar))
        # 双因子策略的时间窗口必须从共同有效特征开始，不能借用只有低波动的早期样本。
        available_factors: dict[tuple[str, datetime], set[str]] = {}
        # 原始因子仍完整留存，缺失只影响共同研究样本资格。
        for factor in all_factors:
            # 只有真正有效且无排除原因的数值提供研究资格。
            if factor.value is not None and factor.reason is None:
                # 同证券同决策收集有效因子身份。
                available_factors.setdefault((factor.security_id, factor.decision_time), set()).add(
                    factor.factor_id
                )
        # 本次研究严格复用固定策略要求的两个因子，不自动降低要求。
        required_factors = {"momentum", "low_volatility"}
        # 只把共同资格的数据用于滚动切分，原候选分母保持不变。
        common_factors = [
            factor
            for factor in all_factors
            if required_factors
            <= available_factors.get((factor.security_id, factor.decision_time), set())
        ]
        # 事后收益只在此步骤与已冻结共同特征配对。
        observations = label_observations(common_factors, records, calendar)
        # 时间窗口不足时保留空结果，绝不缩短12/3/3规则。
        folds = rolling_splits(observations)
        # 整体描述统计明确包含预热与未成熟标签造成的覆盖缺口。
        summary = evaluate_research(
            observations, expected_observations=len(days) * manifest.config.securities
        )
        # 逐窗口统计分别保存，保留集另列且不参与调参。
        fold_results: list[dict[str, Any]] = []
        # 所有滚动折必须展示，不只报告最好的一折。
        for fold in folds:
            # 训练、验证、测试和最终保留按原索引各自评估。
            groups = {
                name: evaluate_research([observations[index] for index in getattr(fold, name)])
                for name in ("train", "validation", "test", "holdout")
            }
            # 记录精确边界和清除索引，便于独立复核。
            fold_results.append({"fold": fold.model_dump(mode="json"), "groups": groups})
        # 长时间研究期间任何源码变动都破坏本次版本证据。
        if code_evidence()["source_hash"] != captured_code["source_hash"]:
            # 失败实验照样保留，不把变更后的版本写为已执行。
            raise ContractError("研究期间源码发生变化，保留失败实验")
        # 返回实际统计规模而非虚构正式回测收益。
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
        # 每个时间窗口的全部结果都留下证据。
        write_json(directory / "folds.json", fold_results)
        # 汇总只在完成计算后写为成功。
        write_json(directory / "summary.json", summary)
        # 试验请求和输出也以哈希保留。
        write_json(
            directory / "artifacts.json",
            {path.name: hash_file(path) for path in sorted(directory.iterdir()) if path.is_file()},
        )
        # CLI只展示精简统计，详细内容在产物内。
        return summary
    # 失败试验也属于试验预算，不能删除隐藏。
    except Exception as exc:
        # 错误只保存类别与消息，不读取环境凭据。
        write_json(
            directory / "failure.json",
            {"status": "failed", "error_type": type(exc).__name__, "message": str(exc)},
        )
        # 保留原异常让CLI返回正确非零退出码。
        raise
