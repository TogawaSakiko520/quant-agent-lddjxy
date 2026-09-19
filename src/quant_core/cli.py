"""把命令行参数交给 application 的离线流程，再把结果或异常转换为输出与退出码。

不在这里计算评分或直接调用券商；成功返回的是本地运行状态，不能由开关启用实盘。
"""

import argparse
import json
import subprocess
import sys
import tomllib
from decimal import Decimal
from pathlib import Path

from quant_core.adapters.storage import load_config
from quant_core.application import replay_run, report_run, research_run, run_demo, validate_run
from quant_core.contracts import ContractError, RiskBlocked


def check_project(base: str | None = None) -> int:
    """在源码仓库运行治理、格式、类型和测试检查，并输出诊断与敏感差异报告。

    base 为可选 Git 比较基线。依赖当前解释器中已安装的开发工具；未找到仓库
    抛 ContractError。逐项运行子进程，任一硬检查失败返回 1，全通过返回 0。
    敏感差异报告是独立复核输入，不等于批准；命令不会自动修复或升级依赖。
    """
    root = Path(__file__).resolve().parents[2]
    # wheel用户不能把未知cwd当作可信开发仓库。
    if not (root / "tools/governance.py").is_file() or not (root / "pyproject.toml").is_file():
        raise ContractError("check仅用于包含tools和开发依赖的源码仓库")
    sys.path.insert(0, str(root))
    # 治理工具不参与运行时交易链路。
    from tools.governance import run_checks, sensitive_diff_report

    # errors 是硬性治理诊断列表；review 是给复核者的敏感文件提示，非同一类失败。
    errors = run_checks(root, base)
    # 差异报告不假装本地JSON等于独立人工审批。
    review = sensitive_diff_report(root, base)
    print(
        json.dumps({"governance_errors": errors, "independent_review": review}, ensure_ascii=False)
    )
    # 任意治理错误最终使检查失败，仍继续收集其他诊断。
    failed = bool(errors)
    # 所有自动检查保持只读检查模式，不运行format --fix。
    commands = [
        [sys.executable, "-m", "ruff", "check", "src", "tests", "tools"],
        [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "tools"],
        [sys.executable, "-m", "mypy"],
        [sys.executable, "-m", "pytest", "-q"],
    ]
    # 每个子进程复用当前解释器；依赖锁定靠调用方按手册用 uv --locked 启动，并非此处另行锁定。
    # check=False 让非零结果留给聚合器处理，
    # 不会自动抛出后跳过后面的检查。failed 一旦为True就保持失败，仍收集剩余诊断。
    for command in commands:
        print("执行检查：" + " ".join(command), flush=True)
        result = subprocess.run(command, cwd=root, check=False)
        failed = failed or result.returncode != 0
    # 待独立复核清单不是技术检查失败，也不是自动批准。
    return 1 if failed else 0


def make_parser() -> argparse.ArgumentParser:
    """构造六个已实现离线命令的参数解析器；创建解析器不会启动运行。"""
    parser = argparse.ArgumentParser(
        prog="quant-core", description="无密钥离线量化工程；FakeBroker不代表正式回测或实盘授权"
    )
    # 必须明确操作，不默认执行交易演示。
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="固定样本到独立对账与报告")
    demo.add_argument("--config", type=Path, help="演示TOML配置，省略时使用相同默认值")
    demo.add_argument("--output", type=Path, required=True, help="尚不存在的新运行目录")
    for name, help_text in (
        ("validate", "校验运行哈希与数据契约"),
        ("replay", "在新状态库回放日志并重算决策"),
        ("report", "只读重生中文解释报告"),
        ("research", "追加隔离因子研究实验"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--run-dir", type=Path, required=True)
        # 回放独立要求新输出，防止覆盖原交易事实。
        if name == "replay":
            command.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check", help="运行治理、pytest、Ruff和严格mypy")
    check.add_argument("--base", help="独立审查使用的Git比较基线")
    paper_read = commands.add_parser("paper-read", help="已授权账户的真实 Paper 只读核验")
    paper_read.add_argument("--config", type=Path, required=True)
    paper_read.add_argument("--credentials", type=Path, required=True)
    paper_read.add_argument("--output", type=Path, required=True)
    paper_plan = commands.add_parser("paper-plan", help="按配置使用总回报或MA拆股价格形成策略目标")
    paper_plan.add_argument("--source", type=Path, required=True)
    paper_plan.add_argument("--supplement", type=Path, help="原双因子必需的总回报/行业证据")
    paper_plan.add_argument("--identity-evidence", type=Path, help="MA必需的当前普通股身份来源")
    paper_plan.add_argument("--output", type=Path, required=True)
    for name in ("paper-execute", "paper-recover"):
        paper = commands.add_parser(name, help="按明确批准边界执行或恢复同一 Paper 计划")
        paper.add_argument("--run-dir", type=Path, required=True)
        paper.add_argument("--credentials", type=Path, required=True)
        paper.add_argument("--approve-plan", required=True)
        paper.add_argument("--max-orders", type=int, required=True)
        paper.add_argument("--max-order-notional", type=Decimal, required=True)
        paper.add_argument("--unfilled", choices=("keep", "cancel"), required=True)
        if name == "paper-execute":
            paper.add_argument(
                "--queue-cancel-test",
                action="store_true",
                help="显式休市Paper提交撤单测试：首个MA目标一股，必须max-orders=1及unfilled=cancel",
            )
        if name == "paper-recover":
            paper.add_argument(
                "--confirm-unsent-source",
                type=Path,
                help="仅核验固定旧版预提交缺陷的完整源码证据；不能用404解除普通未知订单",
            )
        paper.add_argument(
            "--observe-seconds",
            type=int,
            default=300,
            help="提交后观察上限0至300秒；恢复不等待，仍按已授权未成交策略处理",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    """解析并执行CLI；成功0、运行失败1、输入错误2、风险阻断3，输出机器原因。"""
    # argparse错误会按标准约定返回SystemExit(2)。
    args = make_parser().parse_args(argv)
    # argparse 将路径转为Path、命令名留在 args.command；以下分派只调用该命令的应用入口。
    # 应用成功后返回清单/研究摘要，再裁成终端索引；完整资金/订单事实仍留在运行目录。
    try:
        if args.command == "check":
            return check_project(args.base)
        if args.command.startswith("paper-"):
            from quant_core.adapters.paper_session import SystemClock, credential_transport
            from quant_core.adapters.storage import read_json
            from quant_core.application import paper_execute, paper_plan, paper_read
            from quant_core.contracts import PaperConfig

            if args.command == "paper-plan":
                result = paper_plan(
                    args.source, args.supplement, args.output, identity_path=args.identity_evidence
                )
            else:
                config = PaperConfig.model_validate(
                    tomllib.loads(args.config.read_text())
                    if args.command == "paper-read"
                    else read_json(args.run_dir / "config.json")
                )
                transport = credential_transport(args.credentials, config.account_id)
                if args.command == "paper-read":
                    result = paper_read(config, args.output, transport, SystemClock())
                else:
                    result = paper_execute(
                        args.run_dir,
                        transport,
                        SystemClock(),
                        approved_plan=args.approve_plan,
                        max_orders=args.max_orders,
                        max_order_notional=args.max_order_notional,
                        unfilled=args.unfilled,
                        recover_only=args.command == "paper-recover",
                        queue_cancel_test=getattr(args, "queue_cancel_test", False),
                        observe_seconds=args.observe_seconds,
                        confirm_unsent_source=getattr(args, "confirm_unsent_source", None),
                    )
            print(json.dumps(result, ensure_ascii=False))
            return 3 if result.get("status") in {"blocked", "remote_observation_failed"} else 0
        if args.command == "demo":
            # load_config 做配置校验，run_demo 才创建新目录、两份本地账户库和模拟交易记录。
            manifest = run_demo(load_config(args.config), args.output)
            result = {
                "status": "completed",
                "run_id": manifest.run_id,
                "output": str(args.output.resolve()),
                "report": str((args.output / "report.md").resolve()),
            }
        elif args.command == "validate":
            # validate_run 重算证据并返回原清单，不在源目录补写“修正后”的账户。
            manifest = validate_run(args.run_dir)
            result = {"status": "validated", "run_id": manifest.run_id}
        elif args.command == "replay":
            # 回放写新目录并从初态重建内部账本，不把原订单再次发给 Broker。
            manifest = replay_run(args.run_dir, args.output)
            result = {
                "status": "replayed",
                "run_id": manifest.run_id,
                "output": str(args.output.resolve()),
            }
        elif args.command == "report":
            # 输出到stdout，由用户决定是否另存，不覆盖源文件。
            print(report_run(args.run_dir), end="")
            return 0
        else:
            # 解析器已经限制命令集合，这里仅剩 research；它追加实验目录，不调参或更改交易事实。
            summary = research_run(args.run_dir)
            result = {
                "status": summary["status"],
                "observations": summary["observations"],
                "folds": summary["folds"],
                "experiment": summary["experiment"],
            }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except RiskBlocked as exc:
        # 异常在CLI边界变成退出码3；这里不回滚之前已落盘的意图/事件，也不重发订单。
        print(
            json.dumps({"status": "risk_blocked", "reason": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 3
    # 文件覆盖、缺输入及模型验证均属于明确输入问题。
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        print(
            json.dumps(
                {"status": "input_error", "error_type": type(exc).__name__, "reason": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        # 未分类的运行故障返回1，调用者须保留输出目录诊断；失败不表示已经撤单或清仓。
        print(
            json.dumps(
                {"status": "runtime_failure", "error_type": type(exc).__name__, "reason": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
