"""离线应用独立集成验收：封锁网络、清除凭据、核对事实、破坏证据和验证退出码。"""

import hashlib
import json
import os
import shutil
import socket
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import pytest

from quant_core.adapters.sqlite_store import SQLiteEventStore
from quant_core.application import (
    REQUIRED_ARTIFACTS,
    replay_run,
    report_run,
    research_run,
    run_demo,
    validate_run,
)
from quant_core.cli import main
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
from quant_core.research import ResearchFold, ResearchObservation


def read_json(path: Path) -> Any:
    """独立读取测试产物JSON；返回解析数据，错误原样抛出，不修改文件。"""
    # 不复用被测storage读取器，以保留独立观察入口。
    return json.loads(path.read_text(encoding="utf-8"))


def file_hashes(root: Path) -> dict[str, str]:
    """用标准库独立记录运行内全部文件的 SHA-256，比较后续操作是否改写源事实。"""
    # 字节哈希用于验证validate/report/replay没有反写源事实。
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def replace_artifact(root: Path, name: str, payload: Any) -> None:
    """在 copied_run 副本中替换一份 JSON，并同步 manifest.artifacts 的对应字节哈希。

    payload 是字典/列表形状的导出事实，尚未表示与原始交易日志一致。故意给篡改
    内容正确哈希，是为了让 validate_run 继续检查语义矛盾，而不只停在传输损坏。
    """
    path = root / name
    # 完整写出合法JSON，确保失败由语义矛盾而非格式错误触发。
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    manifest = read_json(root / "manifest.json")
    # 新哈希来自真实改后字节，不能用故意错误哈希获得廉价通过。
    manifest["artifacts"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def prohibit_network(*args: object, **kwargs: object) -> None:
    """替换测试中的 socket 连接入口，使调用明确失败，不记录地址或凭据。"""
    # 若演示暗中连接网络，本测试必须失败而非静默mock返回数据。
    raise AssertionError("离线应用不得调用网络连接")


@pytest.fixture(scope="session")
def offline_run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """以默认配置生成一次运行，复用期间阻断 Python socket 连接入口并移除凭据变量。

    patch 只覆盖当前进程所列连接入口，不等同操作系统级网络隔离；退出恢复环境。
    产物含独立 internal/broker 两库、按顺序保存的 journal、JSON 决策事实和 manifest
    文件哈希清单。夹具返回目录路径，不是内存账户；用例须读取对应产物判断结果。
    损坏用例使用 copied_run，不修改本夹具交出的源证据。
    """
    # 会话夹具使用自己的补丁上下文，避免依赖函数级monkeypatch。
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", prohibit_network)
        patch.setattr(socket, "create_connection", prohibit_network)
        # 只检查环境变量名字，不获取或打印对应秘密值。
        for name in list(os.environ):
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
                patch.delenv(name, raising=False)
        output = tmp_path_factory.mktemp("offline-integration") / "default-run"
        # 真实完整默认历史只生成一次，禁止用缩小配置掩盖问题。
        run_demo(DemoConfig(), output)
        # 多个用例复用原始只读目录，损坏用例必须先复制。
        yield output


@pytest.fixture
def copied_run(offline_run: Path, tmp_path: Path) -> Path:
    """复制完整默认运行供单个破坏用例使用；不重新运行或改写session源目录。"""
    output = tmp_path / "run-copy"
    shutil.copytree(offline_run, output)
    return output


@pytest.mark.integration
def test_default_demo_completes_without_network_or_credentials(offline_run: Path) -> None:
    """默认样本从数据到订单和独立对账完整完成；事实文件与哈希均真实存在。"""
    manifest = RunManifest.model_validate(read_json(offline_run / "manifest.json"))
    assert manifest.config == DemoConfig()
    assert manifest.initial_account.cash == Decimal("100000")
    assert REQUIRED_ARTIFACTS <= set(manifest.artifacts)
    # 每个已封印文件的实际字节都独立核验。
    for name, digest in manifest.artifacts.items():
        assert hashlib.sha256((offline_run / name).read_bytes()).hexdigest() == digest
    assert not (offline_run / "internal.sqlite").samefile(offline_run / "broker.sqlite")
    # 短连接实现不能遗漏仍在WAL中的状态。
    assert list(offline_run.glob("*-wal")) == []
    reconciliation = ReconciliationResult.model_validate(
        read_json(offline_run / "reconciliation.json")
    )
    assert reconciliation.matched is True and reconciliation.differences == []
    assert any("FakeBroker" in limitation for limitation in manifest.limitations)


@pytest.mark.integration
def test_cash_positions_fees_and_report_derive_from_raw_fills(offline_run: Path) -> None:
    """从原始唯一成交独立验证资金恒等式、持仓和报告，不用账务实现产生预期。"""
    account = AccountSnapshot.model_validate(read_json(offline_run / "account.json"))
    # events.json 混合订单状态与成交消息；只有 kind=fill 的新增成交进入现金计算，
    # 不能把订单累计数量再次当作成交。account.json 是待核对的最终投影。
    # 默认初态无持仓，第一轮只生成买单。
    fills = [
        FillEvent.model_validate(item)
        for item in read_json(offline_run / "events.json")
        if item["kind"] == "fill"
    ]
    assert fills
    # 去重依据是来源加独立成交身份。
    unique = {(event.source, event.fill_id): event for event in fills}
    assert all(event.side == "BUY" for event in unique.values())
    purchases = sum((event.price * event.quantity for event in unique.values()), Decimal("0"))
    # 实际费用与订单预留费用不同，必须使用成交事件金额。
    fees = sum((event.fee for event in unique.values()), Decimal("0"))
    # 核验现金守恒，不复制被测账务函数。
    assert account.cash + purchases + fees == Decimal("100000")
    assert account.fees == fees
    assert account.available_cash == account.cash
    # 每个证券实际持仓来自原始新增成交量之和。
    for security_id, quantity in account.positions.items():
        assert quantity == sum(
            event.quantity for event in unique.values() if event.security_id == security_id
        )
    # 取得成本包括全部成交金额和买入费用。
    assert sum(account.cost_basis.values(), Decimal("0")) == purchases + fees
    # 报告从事实重建，应与原封印报告完全一致。
    report = report_run(offline_run)
    assert report == (offline_run / "report.md").read_text(encoding="utf-8")
    assert f"现金：{account.cash} USD" in report
    assert f"累计费用：{account.fees} USD" in report
    orders = [OrderRecord.model_validate(item) for item in read_json(offline_run / "orders.json")]
    assert orders and all(order.status == "FILLED" for order in orders)
    # 报告显示委托数量与实际成交量两种事实。
    for order in orders:
        assert (
            f"| {order.intent.client_order_id} | {order.intent.security_id} | {order.intent.side} | {order.intent.quantity} | {order.filled_quantity} |"
            in report
        )
    assert "不是正式回测、收益证据或实盘授权" in report


@pytest.mark.integration
def test_validate_report_and_replay_leave_source_unchanged(
    offline_run: Path, tmp_path: Path
) -> None:
    """校验与报告只读，journal在全新库恢复，回放不得复制旧内部状态作为证据。"""
    before = file_hashes(offline_run)
    source_manifest = validate_run(offline_run)
    report_run(offline_run)
    destination = tmp_path / "replayed"
    replayed = replay_run(offline_run, destination)
    assert replayed.run_id == source_manifest.run_id + "-replay"
    # comparisons 按业务层保存布尔核对结果；另外重读重建库和journal，避免只信展示标记。
    comparisons = read_json(destination / "replay-verification.json")
    # 禁止空字典all为True掩盖未执行验证。
    assert set(comparisons) >= {"account", "orders", "factors", "signals", "target"}
    assert all(result is True for result in comparisons.values())
    assert not (destination / "internal.sqlite").samefile(offline_run / "internal.sqlite")
    expected = AccountSnapshot.model_validate(read_json(offline_run / "account.json"))
    # 用原始初态重开不会重置已经重建的状态。
    store = SQLiteEventStore(destination / "internal.sqlite", source_manifest.initial_account)
    assert store.account(expected.as_of) == expected
    journal = [
        JournalEntry.model_validate(item) for item in read_json(offline_run / "journal.json")
    ]
    # 确认不仅有展示文件，还有可执行的非空事务序列。
    assert journal and [item.sequence for item in journal] == list(range(1, len(journal) + 1))
    assert store.journal() == journal
    assert validate_run(destination).run_id == replayed.run_id
    assert file_hashes(offline_run) == before


def test_demo_and_replay_refuse_to_overwrite_existing_run(offline_run: Path) -> None:
    """旧证据目录不可覆盖，失败后文件指纹保持不变。"""
    before = file_hashes(offline_run)
    with pytest.raises(FileExistsError):
        run_demo(DemoConfig(), offline_run)
    with pytest.raises(FileExistsError):
        replay_run(offline_run, offline_run)
    assert file_hashes(offline_run) == before


def test_corrupt_file_hash_is_rejected_by_validate_and_cli(
    copied_run: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """修改账户文件但不改清单，validate拒绝且CLI输出输入错误码2。"""
    # 追加空白也改变字节证据，不能忽略未签名变化。
    path = copied_run / "account.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ContractError, match="hash_mismatch:account.json"):
        validate_run(copied_run)
    assert main(["validate", "--run-dir", str(copied_run)]) == 2
    output = json.loads(capsys.readouterr().err)
    assert output["status"] == "input_error"


def test_resigned_account_still_must_match_transaction_facts(copied_run: Path) -> None:
    """重签被篡改账户哈希也不能通过，语义对账必须独立从journal重建事实。"""
    account = read_json(copied_run / "account.json")
    # 合法减少现金与可用现金，避免仅靠模型形状错误拒绝。
    account["cash"] = str(Decimal(account["cash"]) - Decimal("10"))
    # 保持available_cash<=cash，确保该假账户仍满足基础契约。
    account["available_cash"] = account["cash"]
    AccountSnapshot.model_validate(account)
    # 真正重签字节摘要，使失败必须来自事件语义核验。
    replace_artifact(copied_run, "account.json", account)
    with pytest.raises(ContractError, match="账户或订单与事务日志不一致"):
        # 原始journal与两个独立数据库都没有这十美元变化。
        validate_run(copied_run)
    with pytest.raises(ContractError):
        report_run(copied_run)


@pytest.mark.parametrize("kind", ["parent", "absolute", "symlink"])
def test_manifest_cannot_escape_run_directory(
    copied_run: Path, tmp_path: Path, kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """相对上级、绝对路径和符号链接越界都拒绝，CLI统一返回输入错误2。"""
    outside = tmp_path / "outside.txt"
    # 创建固定无秘密哨兵，不能读取真实系统文件作为测试。
    outside.write_text("outside-run-evidence", encoding="utf-8")
    # 符号链接通过已有必要文件路径尝试逃逸。
    if kind == "symlink":
        (copied_run / "report.md").unlink()
        (copied_run / "report.md").symlink_to(outside)
    else:
        manifest = read_json(copied_run / "manifest.json")
        name = str(outside.resolve()) if kind == "absolute" else "../outside.txt"
        # 字节哈希正确也不能绕过路径授权边界。
        manifest["artifacts"][name] = hashlib.sha256(outside.read_bytes()).hexdigest()
        (copied_run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractError, match="越界"):
        validate_run(copied_run)
    assert main(["validate", "--run-dir", str(copied_run)]) == 2
    assert "越界" in json.loads(capsys.readouterr().err)["reason"]


def test_missing_required_artifact_reference_is_not_success(copied_run: Path) -> None:
    """从清单删掉风险证据引用不能使必要文件逃离校验范围。"""
    manifest = read_json(copied_run / "manifest.json")
    # 删掉引用但保留文件，模拟规避哈希检查而非单纯缺文件。
    manifest["artifacts"].pop("risk.json")
    (copied_run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractError, match="缺少必要产物"):
        validate_run(copied_run)


def test_resigned_journal_sequence_gap_is_rejected(copied_run: Path) -> None:
    """日志序号缺口即使重签文件哈希也拒绝，不能排序后假装原过程完整。"""
    journal = read_json(copied_run / "journal.json")
    # 第一条改成二意味着缺失首操作。
    journal[0]["sequence"] = 2
    replace_artifact(copied_run, "journal.json", journal)
    with pytest.raises(ContractError, match="序号不连续"):
        validate_run(copied_run)


def test_resigned_broker_database_discrepancy_is_rejected(copied_run: Path) -> None:
    """只修改独立券商数据库并重签hash，内部日志一致也不能宣称独立对账成功。"""
    wrong = read_json(copied_run / "account.json")
    # 从券商现金额外扣十美元作为无对应事件的外部差异。
    wrong["cash"] = str(Decimal(wrong["cash"]) - Decimal("10"))
    # 可用现金同步使结构本身仍合法。
    wrong["available_cash"] = wrong["cash"]
    db = sqlite3.connect(copied_run / "broker.sqlite")
    with db:
        db.execute("UPDATE broker_state SET payload=? WHERE key='account'", (json.dumps(wrong),))
    # 关闭后再计算真实SQLite字节哈希。
    db.close()
    manifest = read_json(copied_run / "manifest.json")
    # 保留正确文件哈希，测试才能验证独立状态比对。
    manifest["artifacts"]["broker.sqlite"] = hashlib.sha256(
        (copied_run / "broker.sqlite").read_bytes()
    ).hexdigest()
    (copied_run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractError):
        validate_run(copied_run)


def test_cli_rejects_live_config_before_creating_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """实盘mode不能通过配置启用，CLI返回2且不创建成功或失败运行目录。"""
    config = tmp_path / "live.toml"
    config.write_text('mode = "live"\n', encoding="utf-8")
    destination = tmp_path / "forbidden-live"
    assert main(["demo", "--config", str(config), "--output", str(destination)]) == 2
    assert not destination.exists()
    assert json.loads(capsys.readouterr().err)["status"] == "input_error"


def test_cli_unreconciled_run_returns_risk_exit_three(
    copied_run: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """明确未解释对账差异属于风险阻断3，不能混同JSON损坏或成功。"""
    reconciliation = read_json(copied_run / "reconciliation.json")
    reconciliation.update(matched=False, differences=["未解释的外部现金差异"])
    # 文件格式和哈希仍合法，以直接验证风险分类。
    replace_artifact(copied_run, "reconciliation.json", reconciliation)
    assert main(["validate", "--run-dir", str(copied_run)]) == 3
    assert json.loads(capsys.readouterr().err)["status"] == "risk_blocked"


def test_cli_validate_and_report_success_match_raw_decision(
    offline_run: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """已验证运行的CLI成功输出指向真实决策，report只输出原始事实形成的文字。"""
    assert main(["validate", "--run-dir", str(offline_run)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["run_id"] == read_json(offline_run / "manifest.json")["run_id"]
    assert result["status"] == "validated"
    assert main(["report", "--run-dir", str(offline_run)]) == 0
    report = capsys.readouterr().out
    signals = SignalSet.model_validate(read_json(offline_run / "signals.json"))
    assert signals.decision_id in report
    target = TargetPortfolio.model_validate(read_json(offline_run / "target.json"))
    assert target.positions
    for position in target.positions:
        assert position.security_id in report and position.reason in report


@pytest.mark.integration
def test_real_research_uses_common_mature_factors_and_preserves_trade_facts(
    offline_run: Path,
) -> None:
    """真实研究只纳入双因子共同成熟样本，保留完整滚动窗口且不修改交易证据。"""
    before = file_hashes(offline_run)
    summary = research_run(offline_run)
    assert summary["status"] == "completed" and summary["observations"] > 0
    # 默认成熟历史应至少支持一折完整12/3/3和末6月保留。
    assert summary["folds"] >= 1
    experiment = offline_run / "research" / "experiment-0001"
    assert Path(summary["experiment"]).resolve() == experiment.resolve()
    request = read_json(experiment / "request.json")
    assert request["trial_budget"] == 1 and request["tuning"] is False
    assert request["code"]["source_hash"] and request["environment"]["lock_hash"]
    # 每个 observation 绑定某证券某次决策的双因子值与未来收益区间；fold 的分组保存
    # 该列表的整数索引，因此下方可从分组索引回查原始 label_end 检查时间泄漏。
    observations = [
        ResearchObservation.model_validate(item)
        for item in read_json(experiment / "observations.json")
    ]
    # 不能只用低波动61日预热样本扩大训练历史。
    assert observations and all(
        set(item.factor_values) == {"momentum", "low_volatility"} for item in observations
    )
    factors = [FactorValue.model_validate(item) for item in read_json(experiment / "factors.json")]
    # 从留存因子结果找首个有效动量时点，核对研究不早于该边界；253价格窗口另有固定样本测试。
    first_momentum = min(
        item.decision_time
        for item in factors
        if item.factor_id == "momentum" and item.value is not None and item.reason is None
    )
    assert min(item.decision_time for item in observations) >= first_momentum
    results = read_json(experiment / "folds.json")
    assert len(results) == summary["folds"]
    for result in results:
        fold = ResearchFold.model_validate(result["fold"])
        assert fold.train and fold.validation and fold.test and fold.holdout
        assert min(observations[index].decision_time for index in fold.train) >= first_momentum
        # 训练标签严格终结在验证开始前，避免跨边界泄漏。
        assert max(observations[index].label_end for index in fold.train) < datetime.fromisoformat(
            fold.boundaries["validation_start"]
        )
        # 最終保留样本不能用于任何开发分组。
        assert set(fold.holdout).isdisjoint(set(fold.train) | set(fold.validation) | set(fold.test))
    artifacts = read_json(experiment / "artifacts.json")
    assert {
        "request.json",
        "factors.json",
        "observations.json",
        "folds.json",
        "summary.json",
    } <= set(artifacts)
    for name, digest in artifacts.items():
        assert hashlib.sha256((experiment / name).read_bytes()).hexdigest() == digest
    # 研究只新增独立实验文件，原始交易文件字节不可改变。
    for name, digest in before.items():
        assert hashlib.sha256((offline_run / name).read_bytes()).hexdigest() == digest
