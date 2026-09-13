"""离线应用独立集成验收：封锁网络、清除凭据、核对事实、破坏证据和验证退出码。"""

# 哈希期望由标准库独立计算，不调用被测文件哈希函数。
import hashlib

# 测试读取原始JSON事实，不从报告反推账务。
import json

# 仅移除凭据环境键，不读取或输出任何密钥值。
import os

# 损坏用例复制只生成一次的默认演示目录。
import shutil

# 主动封锁网络入口验证离线承诺。
import socket

# 独立数据库差异通过只改测试副本注入。
import sqlite3

# 研究日期从原始UTC字段读取，不使用系统当前日期。
from datetime import datetime

# 全部财务恒等式采用十进制原始金额。
from decimal import Decimal

# 文件与目录明确位于pytest临时空间。
from pathlib import Path

# 会话夹具yield及JSON测试边界类型。
from typing import Any, Iterator

# 集成测试框架提供隔离目录、捕获输出和显式补丁作用域。
import pytest

# 持久化状态只在回放验证时以正式查询接口读取。
from quant_core.adapters.sqlite_store import SQLiteEventStore

# 真实被测应用入口不被桩实现替换。
from quant_core.application import (
    REQUIRED_ARTIFACTS,
    replay_run,
    report_run,
    research_run,
    run_demo,
    validate_run,
)

# CLI测试直接核验真实main的退出码与机器输出。
from quant_core.cli import main

# 结构化事实必须符合共同契约。
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    DemoConfig,
    FactorValue,
    FillEvent,
    JournalEntry,
    OrderRecord,
    ReconciliationResult,
    RunManifest,
    SignalSet,
    TargetPortfolio,
)

# 研究观察与索引也使用真实公开类型核验。
from quant_core.research import ResearchFold, ResearchObservation


def read_json(path: Path) -> Any:
    """独立读取测试产物JSON；返回解析数据，错误原样抛出，不修改文件。"""
    # 不复用被测storage读取器，以保留独立观察入口。
    return json.loads(path.read_text(encoding="utf-8"))


def file_hashes(root: Path) -> dict[str, str]:
    """计算临时运行全部文件的独立SHA256快照；返回相对路径映射，无写入副作用。"""
    # 字节哈希用于验证validate/report/replay没有反写源事实。
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def replace_artifact(root: Path, name: str, payload: Any) -> None:
    """只在测试副本篡改指定JSON并重签字节哈希；用于验证语义检查不能止于哈希。"""
    # 测试副本允许故意破坏，源session演示目录保持不可变。
    path = root / name
    # 完整写出合法JSON，确保失败由语义矛盾而非格式错误触发。
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    # 读取原清单并只替换被攻击文件摘要。
    manifest = read_json(root / "manifest.json")
    # 新哈希来自真实改后字节，不能用故意错误哈希获得廉价通过。
    manifest["artifacts"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    # 重签清单本身未被纳入自引用哈希。
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def prohibit_network(*args: object, **kwargs: object) -> None:
    """任何socket联网调用直接失败；参数被忽略且不记录地址或凭据，无网络副作用。"""
    # 若演示暗中连接网络，本测试必须失败而非静默mock返回数据。
    raise AssertionError("离线应用不得调用网络连接")


@pytest.fixture(scope="session")
def offline_run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """仅生成一次完整默认离线运行；整个复用期间禁止网络并移除凭据，退出恢复环境。"""
    # 会话夹具使用自己的补丁上下文，避免依赖函数级monkeypatch。
    with pytest.MonkeyPatch.context() as patch:
        # socket底层连接禁止访问外部服务。
        patch.setattr(socket.socket, "connect", prohibit_network)
        # 高层便捷连接也必须被明确封锁。
        patch.setattr(socket, "create_connection", prohibit_network)
        # 只检查环境变量名字，不获取或打印对应秘密值。
        for name in list(os.environ):
            # 覆盖常见供应商和通用令牌/密码环境变量。
            if any(
                marker in name.upper()
                for marker in (
                    "API_KEY",
                    "TOKEN",
                    "SECRET",
                    "PASSWORD",
                    "CREDENTIAL",
                    "QUANTCONNECT",
                    "ALPACA",
                    "IBKR",
                )
            ):
                # 缺少凭据不能阻止完全离线工程演示。
                patch.delenv(name, raising=False)
        # pytest负责隔离根目录，应用必须自行独占创建最终运行目录。
        output = tmp_path_factory.mktemp("offline-integration") / "default-run"
        # 真实完整默认历史只生成一次，禁止用缩小配置掩盖问题。
        run_demo(DemoConfig(), output)
        # 多个用例复用原始只读目录，损坏用例必须先复制。
        yield output


@pytest.fixture
def copied_run(offline_run: Path, tmp_path: Path) -> Path:
    """复制完整默认运行供单个破坏用例使用；不重新运行或改写session源目录。"""
    # 每个损坏场景获得独立副本，失败不能污染下一用例。
    output = tmp_path / "run-copy"
    # 所有证据包含两个独立SQLite状态源都原样复制。
    shutil.copytree(offline_run, output)
    # 返回副本供显式篡改。
    return output


@pytest.mark.integration
def test_default_demo_completes_without_network_or_credentials(offline_run: Path) -> None:
    """默认样本从数据到订单和独立对账完整完成；事实文件与哈希均真实存在。"""
    # 源清单必须通过严格公共契约。
    manifest = RunManifest.model_validate(read_json(offline_run / "manifest.json"))
    # 不采用缩小或放宽参数的特殊配置。
    assert manifest.config == DemoConfig()
    # 默认初始十万美元是独立固定验收前提。
    assert manifest.initial_account.cash == Decimal("100000")
    # 每个规定职责都有独立证据文件。
    assert REQUIRED_ARTIFACTS <= set(manifest.artifacts)
    # 每个已封印文件的实际字节都独立核验。
    for name, digest in manifest.artifacts.items():
        # 测试不调用被测hash_file计算预期。
        assert hashlib.sha256((offline_run / name).read_bytes()).hexdigest() == digest
    # 独立数据库必须确实是两个文件。
    assert not (offline_run / "internal.sqlite").samefile(offline_run / "broker.sqlite")
    # 短连接实现不能遗漏仍在WAL中的状态。
    assert list(offline_run.glob("*-wal")) == []
    # 完成结论必须来自已保存独立对账。
    reconciliation = ReconciliationResult.model_validate(
        read_json(offline_run / "reconciliation.json")
    )
    # 不存在被忽略的未解释差异。
    assert reconciliation.matched is True and reconciliation.differences == []
    # 演示边界必须随清单明确披露。
    assert any("FakeBroker" in limitation for limitation in manifest.limitations)


@pytest.mark.integration
def test_cash_positions_fees_and_report_derive_from_raw_fills(offline_run: Path) -> None:
    """从原始唯一成交独立验证资金恒等式、持仓和报告，不用账务实现产生预期。"""
    # 账户最终事实是被核验对象。
    account = AccountSnapshot.model_validate(read_json(offline_run / "account.json"))
    # 默认初态无持仓，第一轮只生成买单。
    fills = [
        FillEvent.model_validate(item)
        for item in read_json(offline_run / "events.json")
        if item["kind"] == "fill"
    ]
    # 演示不能用空成交列表冒充执行链路完成。
    assert fills
    # 去重依据是来源加独立成交身份。
    unique = {(event.source, event.fill_id): event for event in fills}
    # 默认工程场景从现金建仓，卖出会改变本用例独立预期前提。
    assert all(event.side == "BUY" for event in unique.values())
    # 原始成交金额独立求和。
    purchases = sum((event.price * event.quantity for event in unique.values()), Decimal("0"))
    # 实际费用与订单预留费用不同，必须使用成交事件金额。
    fees = sum((event.fee for event in unique.values()), Decimal("0"))
    # 核验现金守恒，不复制被测账务函数。
    assert account.cash + purchases + fees == Decimal("100000")
    # 累计费用字段必须等于真实成交费用。
    assert account.fees == fees
    # 模拟即时结算不能凭空增加额外可用现金。
    assert account.available_cash == account.cash
    # 每个证券实际持仓来自原始新增成交量之和。
    for security_id, quantity in account.positions.items():
        # 默认没有拆股，直接比较实际整股数量。
        assert quantity == sum(
            event.quantity for event in unique.values() if event.security_id == security_id
        )
    # 取得成本包括全部成交金额和买入费用。
    assert sum(account.cost_basis.values(), Decimal("0")) == purchases + fees
    # 报告从事实重建，应与原封印报告完全一致。
    report = report_run(offline_run)
    # 不接受重生时静默编造的新解释。
    assert report == (offline_run / "report.md").read_text(encoding="utf-8")
    # 现金、费用必须原样来自原始账户字段。
    assert f"现金：{account.cash} USD" in report
    # 费用与持仓成本不能混淆。
    assert f"累计费用：{account.fees} USD" in report
    # 每张订单的稳定身份都出现在报告。
    orders = [OrderRecord.model_validate(item) for item in read_json(offline_run / "orders.json")]
    # 默认注入成交后每单都应达到确定终态。
    assert orders and all(order.status == "FILLED" for order in orders)
    # 报告显示委托数量与实际成交量两种事实。
    for order in orders:
        # 固定表格行来自真实意图及成交投影。
        assert (
            f"| {order.intent.client_order_id} | {order.intent.security_id} | {order.intent.side} | {order.intent.quantity} | {order.filled_quantity} |"
            in report
        )
    # 明确披露非真实回测边界。
    assert "不是正式回测、收益证据或实盘授权" in report


@pytest.mark.integration
def test_validate_report_and_replay_leave_source_unchanged(
    offline_run: Path, tmp_path: Path
) -> None:
    """校验与报告只读，journal在全新库恢复，回放不得复制旧内部状态作为证据。"""
    # 保存包括数据库在内的全部源文件字节指纹。
    before = file_hashes(offline_run)
    # 完整语义校验成功才允许继续。
    source_manifest = validate_run(offline_run)
    # 报告重生不写回原文件。
    report_run(offline_run)
    # 回放输出是全新目录。
    destination = tmp_path / "replayed"
    # 真实应用负责按journal操作顺序重建内账。
    replayed = replay_run(offline_run, destination)
    # 回放身份保留来源关系。
    assert replayed.run_id == source_manifest.run_id + "-replay"
    # 证明文件必须包含全部实际比较结果。
    comparisons = read_json(destination / "replay-verification.json")
    # 禁止空字典all为True掩盖未执行验证。
    assert set(comparisons) >= {"account", "orders", "factors", "signals", "target"}
    # 每一层都实际通过。
    assert all(result is True for result in comparisons.values())
    # 新内账不能只是指向旧数据库的硬链接。
    assert not (destination / "internal.sqlite").samefile(offline_run / "internal.sqlite")
    # 查询重建库当前账户，与原始账户按同一时刻精确比较。
    expected = AccountSnapshot.model_validate(read_json(offline_run / "account.json"))
    # 用原始初态重开不会重置已经重建的状态。
    store = SQLiteEventStore(destination / "internal.sqlite", source_manifest.initial_account)
    # 新库确实保留了真实回放累计事实。
    assert store.account(expected.as_of) == expected
    # 原始journal保持严格连续顺序。
    journal = [
        JournalEntry.model_validate(item) for item in read_json(offline_run / "journal.json")
    ]
    # 确认不仅有展示文件，还有可执行的非空事务序列。
    assert journal and [item.sequence for item in journal] == list(range(1, len(journal) + 1))
    # 新库自行产生的重放日志与来源逐条一致。
    assert store.journal() == journal
    # 校验新回放清单仍通过自身语义和哈希检查。
    assert validate_run(destination).run_id == replayed.run_id
    # 原始运行全部字节必须没有被这些操作改变。
    assert file_hashes(offline_run) == before


def test_demo_and_replay_refuse_to_overwrite_existing_run(offline_run: Path) -> None:
    """旧证据目录不可覆盖，失败后文件指纹保持不变。"""
    # 保存全部原始字节摘要。
    before = file_hashes(offline_run)
    # 默认演示不能写入已有目录。
    with pytest.raises(FileExistsError):
        # 不删除目录后重试来让测试通过。
        run_demo(DemoConfig(), offline_run)
    # 回放也不能覆盖原运行。
    with pytest.raises(FileExistsError):
        # 相同源与目标必须明确失败。
        replay_run(offline_run, offline_run)
    # 两次失败均未修改成功运行的原始事实。
    assert file_hashes(offline_run) == before


def test_corrupt_file_hash_is_rejected_by_validate_and_cli(
    copied_run: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """修改账户文件但不改清单，validate拒绝且CLI输出输入错误码2。"""
    # 追加空白也改变字节证据，不能忽略未签名变化。
    path = copied_run / "account.json"
    # 修改只发生在当前测试副本。
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    # 原清单哈希应立即检出变化。
    with pytest.raises(ContractError, match="hash_mismatch:account.json"):
        # 校验不能自动重签修改后的内容。
        validate_run(copied_run)
    # CLI必须保留输入损坏与风控阻断的区别。
    assert main(["validate", "--run-dir", str(copied_run)]) == 2
    # 标准错误输出机器可读原因。
    output = json.loads(capsys.readouterr().err)
    # 正常完成状态不能混入失败结果。
    assert output["status"] == "input_error"


def test_resigned_account_still_must_match_transaction_facts(copied_run: Path) -> None:
    """重签被篡改账户哈希也不能通过，语义对账必须独立从journal重建事实。"""
    # 读取当前合法账户作为攻击目标。
    account = read_json(copied_run / "account.json")
    # 合法减少现金与可用现金，避免仅靠模型形状错误拒绝。
    account["cash"] = str(Decimal(account["cash"]) - Decimal("10"))
    # 保持available_cash<=cash，确保该假账户仍满足基础契约。
    account["available_cash"] = account["cash"]
    # 显式证明这是结构合法的假事实。
    AccountSnapshot.model_validate(account)
    # 真正重签字节摘要，使失败必须来自事件语义核验。
    replace_artifact(copied_run, "account.json", account)
    # 不能把人工修改的账户当成真实成交结果。
    with pytest.raises(ContractError, match="账户或订单与事务日志不一致"):
        # 原始journal与两个独立数据库都没有这十美元变化。
        validate_run(copied_run)
    # 解释报告也必须拒绝显示伪造的正常财务事实。
    with pytest.raises(ContractError):
        # 报告不能成为绕过完整校验的读数入口。
        report_run(copied_run)


@pytest.mark.parametrize("kind", ["parent", "absolute", "symlink"])
def test_manifest_cannot_escape_run_directory(
    copied_run: Path, tmp_path: Path, kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """相对上级、绝对路径和符号链接越界都拒绝，CLI统一返回输入错误2。"""
    # 越界目标也只使用pytest无敏感临时文件。
    outside = tmp_path / "outside.txt"
    # 创建固定无秘密哨兵，不能读取真实系统文件作为测试。
    outside.write_text("outside-run-evidence", encoding="utf-8")
    # 符号链接通过已有必要文件路径尝试逃逸。
    if kind == "symlink":
        # 删除只属于测试副本的报告文件。
        (copied_run / "report.md").unlink()
        # 越界软链应在读取文件前被拒绝。
        (copied_run / "report.md").symlink_to(outside)
    # 其他两种场景直接修改清单引用。
    else:
        # 添加合法哈希不能赋予访问越界文件的权限。
        manifest = read_json(copied_run / "manifest.json")
        # 分别构造绝对路径和上级目录引用。
        name = str(outside.resolve()) if kind == "absolute" else "../outside.txt"
        # 字节哈希正确也不能绕过路径授权边界。
        manifest["artifacts"][name] = hashlib.sha256(outside.read_bytes()).hexdigest()
        # 清单修改仅限测试副本。
        (copied_run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # API必须明确报告路径越界。
    with pytest.raises(ContractError, match="越界"):
        # 不进行网络或外部路径自动修复。
        validate_run(copied_run)
    # CLI按输入错误分类。
    assert main(["validate", "--run-dir", str(copied_run)]) == 2
    # 错误输出同样包含明确越界依据。
    assert "越界" in json.loads(capsys.readouterr().err)["reason"]


def test_missing_required_artifact_reference_is_not_success(copied_run: Path) -> None:
    """从清单删掉风险证据引用不能使必要文件逃离校验范围。"""
    # 攻击完整性清单的覆盖范围。
    manifest = read_json(copied_run / "manifest.json")
    # 删掉引用但保留文件，模拟规避哈希检查而非单纯缺文件。
    manifest["artifacts"].pop("risk.json")
    # 仅测试副本清单被修改。
    (copied_run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # 必要产物集合是独立于清单内容的强制要求。
    with pytest.raises(ContractError, match="缺少必要产物"):
        # 不接受缩减证据范围后的成功结论。
        validate_run(copied_run)


def test_resigned_journal_sequence_gap_is_rejected(copied_run: Path) -> None:
    """日志序号缺口即使重签文件哈希也拒绝，不能排序后假装原过程完整。"""
    # 默认运行必须已有可重放操作。
    journal = read_json(copied_run / "journal.json")
    # 第一条改成二意味着缺失首操作。
    journal[0]["sequence"] = 2
    # 使用正确新字节哈希制造真实语义反例。
    replace_artifact(copied_run, "journal.json", journal)
    # 校验必须核实事务序列完整性。
    with pytest.raises(ContractError, match="序号不连续"):
        # 不允许自动重排或补造缺失意图。
        validate_run(copied_run)


def test_resigned_broker_database_discrepancy_is_rejected(copied_run: Path) -> None:
    """只修改独立券商数据库并重签hash，内部日志一致也不能宣称独立对账成功。"""
    # 账户JSON仍保留内部原始实际投影。
    wrong = read_json(copied_run / "account.json")
    # 从券商现金额外扣十美元作为无对应事件的外部差异。
    wrong["cash"] = str(Decimal(wrong["cash"]) - Decimal("10"))
    # 可用现金同步使结构本身仍合法。
    wrong["available_cash"] = wrong["cash"]
    # 直接访问当前测试副本券商状态源，不碰源运行。
    db = sqlite3.connect(copied_run / "broker.sqlite")
    # 券商状态修改在事务中提交。
    with db:
        # 原始内部账本和events都不改变。
        db.execute("UPDATE broker_state SET payload=? WHERE key='account'", (json.dumps(wrong),))
    # 关闭后再计算真实SQLite字节哈希。
    db.close()
    # 读取清单并明确重签修改后的外部数据库。
    manifest = read_json(copied_run / "manifest.json")
    # 保留正确文件哈希，测试才能验证独立状态比对。
    manifest["artifacts"]["broker.sqlite"] = hashlib.sha256(
        (copied_run / "broker.sqlite").read_bytes()
    ).hexdigest()
    # 清单写回仅限副本。
    (copied_run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # 独立券商差异不能由内部日志一致性掩盖。
    with pytest.raises(ContractError):
        # 需要明确拒绝成功对账声明。
        validate_run(copied_run)


def test_cli_rejects_live_config_before_creating_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """实盘mode不能通过配置启用，CLI返回2且不创建成功或失败运行目录。"""
    # 模拟用户输入的明确未支持模式。
    config = tmp_path / "live.toml"
    # 仅写测试本地配置，不包含真实账户凭据。
    config.write_text('mode = "live"\n', encoding="utf-8")
    # 输出目录必须仍未存在。
    destination = tmp_path / "forbidden-live"
    # 加载严格DemoConfig时应立即拒绝。
    assert main(["demo", "--config", str(config), "--output", str(destination)]) == 2
    # 不进入应用run_demo的目录创建步骤。
    assert not destination.exists()
    # 错误必须被分类为输入错误。
    assert json.loads(capsys.readouterr().err)["status"] == "input_error"


def test_cli_unreconciled_run_returns_risk_exit_three(
    copied_run: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """明确未解释对账差异属于风险阻断3，不能混同JSON损坏或成功。"""
    # 将合法对账对象标记为存在未解释差异。
    reconciliation = read_json(copied_run / "reconciliation.json")
    # 两个字段共同表达真实风险阻断语义。
    reconciliation.update(matched=False, differences=["未解释的外部现金差异"])
    # 文件格式和哈希仍合法，以直接验证风险分类。
    replace_artifact(copied_run, "reconciliation.json", reconciliation)
    # 真实CLI应返回专用风险退出码。
    assert main(["validate", "--run-dir", str(copied_run)]) == 3
    # 机器状态与退出码一致。
    assert json.loads(capsys.readouterr().err)["status"] == "risk_blocked"


def test_cli_validate_and_report_success_match_raw_decision(
    offline_run: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """已验证运行的CLI成功输出指向真实决策，report只输出原始事实形成的文字。"""
    # 命令行校验走完整真实应用链路。
    assert main(["validate", "--run-dir", str(offline_run)]) == 0
    # 读取成功JSON，不能只断言返回码。
    result = json.loads(capsys.readouterr().out)
    # 输出身份必须来自原始清单。
    assert result["run_id"] == read_json(offline_run / "manifest.json")["run_id"]
    # 正常状态明确为validated。
    assert result["status"] == "validated"
    # 真实报告命令不创建第二个报告文件。
    assert main(["report", "--run-dir", str(offline_run)]) == 0
    # 报告stdout包含完整中文解释。
    report = capsys.readouterr().out
    # 策略决策来自真实原始SignalSet。
    signals = SignalSet.model_validate(read_json(offline_run / "signals.json"))
    # 每次解释必须保留稳定决策身份。
    assert signals.decision_id in report
    # 目标数量与理由来自实际组合事实。
    target = TargetPortfolio.model_validate(read_json(offline_run / "target.json"))
    # 默认目标非空，避免循环空执行获得通过。
    assert target.positions
    # 至少核实每个目标证券及其约束理由原样出现。
    for position in target.positions:
        # 解释不可用无关模板替代实际组合。
        assert position.security_id in report and position.reason in report


@pytest.mark.integration
def test_real_research_uses_common_mature_factors_and_preserves_trade_facts(
    offline_run: Path,
) -> None:
    """真实研究只纳入双因子共同成熟样本，保留完整滚动窗口且不修改交易证据。"""
    # 保存研究开始前所有交易事实字节，包括原始清单与两侧数据库。
    before = file_hashes(offline_run)
    # 真实研究复用默认演示数据，网络和凭据仍被session夹具封锁。
    summary = research_run(offline_run)
    # 不能把失败或零样本静默当作研究完成。
    assert summary["status"] == "completed" and summary["observations"] > 0
    # 默认成熟历史应至少支持一折完整12/3/3和末6月保留。
    assert summary["folds"] >= 1
    # 当前会话首次研究必须创建第一个独立实验目录。
    experiment = offline_run / "research" / "experiment-0001"
    # 返回的路径必须指向实际写出的实验。
    assert Path(summary["experiment"]).resolve() == experiment.resolve()
    # 原始研究请求记录代码环境和固定试验预算。
    request = read_json(experiment / "request.json")
    # 固定预算不允许隐藏多次尝试挑最好一次。
    assert request["trial_budget"] == 1 and request["tuning"] is False
    # 源码与实际依赖环境都需要明确证据。
    assert request["code"]["source_hash"] and request["environment"]["lock_hash"]
    # 每条观察从冻结特征与事后标签构成。
    observations = [
        ResearchObservation.model_validate(item)
        for item in read_json(experiment / "observations.json")
    ]
    # 不能只用低波动61日预热样本扩大训练历史。
    assert observations and all(
        set(item.factor_values) == {"momentum", "low_volatility"} for item in observations
    )
    # 所有历史冻结因子原始输出保留完整成熟/缺失信息。
    factors = [FactorValue.model_validate(item) for item in read_json(experiment / "factors.json")]
    # 独立找到动量首次具备253个价格的有效时间。
    first_momentum = min(
        item.decision_time
        for item in factors
        if item.factor_id == "momentum" and item.value is not None and item.reason is None
    )
    # 共同研究样本不能早于任何有效动量出现。
    assert min(item.decision_time for item in observations) >= first_momentum
    # 每个折的具体索引与日期边界都保存，而非仅给折数。
    results = read_json(experiment / "folds.json")
    # 实际完整折数必须与汇总一致。
    assert len(results) == summary["folds"]
    # 遍历全部折，不只验证最好的一折。
    for result in results:
        # 公共折契约验证索引与明确边界。
        fold = ResearchFold.model_validate(result["fold"])
        # 训练、验证、测试与保留组均必须非空。
        assert fold.train and fold.validation and fold.test and fold.holdout
        # 每个训练观察必须来自成熟共同样本。
        assert min(observations[index].decision_time for index in fold.train) >= first_momentum
        # 训练标签严格终结在验证开始前，避免跨边界泄漏。
        assert max(observations[index].label_end for index in fold.train) < datetime.fromisoformat(
            fold.boundaries["validation_start"]
        )
        # 最終保留样本不能用于任何开发分组。
        assert set(fold.holdout).isdisjoint(set(fold.train) | set(fold.validation) | set(fold.test))
    # 实验输出各文件都保存独立字节摘要。
    artifacts = read_json(experiment / "artifacts.json")
    # 核实关键证据文件都包含在实验清单。
    assert {
        "request.json",
        "factors.json",
        "observations.json",
        "folds.json",
        "summary.json",
    } <= set(artifacts)
    # 真实文件哈希独立重新计算。
    for name, digest in artifacts.items():
        # 不能用自报成功代替完整文件证据。
        assert hashlib.sha256((experiment / name).read_bytes()).hexdigest() == digest
    # 研究只新增独立实验文件，原始交易文件字节不可改变。
    for name, digest in before.items():
        # 原manifest也在该集合中，不能因研究完成而重新签名。
        assert hashlib.sha256((offline_run / name).read_bytes()).hexdigest() == digest
