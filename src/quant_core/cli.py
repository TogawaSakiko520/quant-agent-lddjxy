"""统一离线命令行；区分输入错误、风险阻断与运行失败，禁止通过开关启用实盘。"""

# argparse提供标准可发现命令帮助，不增加CLI运行依赖。
import argparse

# 机器可读结果使用统一JSON。
import json

# 检查工具在显式开发仓库子进程中运行。
import subprocess

# CLI入口与子进程使用当前锁定解释器。
import sys

# 路径参数与开发仓库定位。
from pathlib import Path

# 演示配置只从严格TOML契约读取。
from quant_core.adapters.storage import load_config

# 应用层是命令行唯一业务入口。
from quant_core.application import replay_run, report_run, research_run, run_demo, validate_run

# 风控阻断与输入错误分开分类。
from quant_core.contracts import ContractError, RiskBlocked


def check_project(base: str | None = None) -> int:
    """在开发仓库运行治理/测试/格式/类型检查；返回0或1，不自动修复或升级依赖。"""
    # 开发检查以安装源码对应仓库为准。
    root = Path(__file__).resolve().parents[2]
    # wheel用户不能把未知cwd当作可信开发仓库。
    if not (root / "tools/governance.py").is_file() or not (root / "pyproject.toml").is_file():
        # 明确该命令的开发依赖前提。
        raise ContractError("check仅用于包含tools和开发依赖的源码仓库")
    # 只加入已确定的项目根，使治理工具能在开发命令中导入。
    sys.path.insert(0, str(root))
    # 治理工具不参与运行时交易链路。
    from tools.governance import run_checks, sensitive_diff_report

    # 汇总机械可验证的工程问题。
    errors = run_checks(root, base)
    # 差异报告不假装本地JSON等于独立人工审批。
    review = sensitive_diff_report(root, base)
    # 输出清楚区分硬检查错误和待复核证据。
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
    # 分项实际执行并保留stdout/stderr，不能虚构通过。
    for command in commands:
        # 输出命令便于PROJECT_STATE准确记录。
        print("执行检查：" + " ".join(command), flush=True)
        # 禁止shell拼接，避免用户base等参数成为命令代码。
        result = subprocess.run(command, cwd=root, check=False)
        # 所有非零退出码均影响最终结论。
        failed = failed or result.returncode != 0
    # 待独立复核清单不是技术检查失败，也不是自动批准。
    return 1 if failed else 0


def make_parser() -> argparse.ArgumentParser:
    """定义真实已实现命令与参数；返回解析器，无交易和文件副作用。"""
    # 帮助明确系统目前仅为离线工程。
    parser = argparse.ArgumentParser(
        prog="quant-core", description="无密钥离线量化工程；FakeBroker不代表正式回测或实盘授权"
    )
    # 必须明确操作，不默认执行交易演示。
    commands = parser.add_subparsers(dest="command", required=True)
    # 演示生成新目录，不覆盖旧证据。
    demo = commands.add_parser("demo", help="固定样本到独立对账与报告")
    # 可选配置通过DemoConfig严格验证。
    demo.add_argument("--config", type=Path, help="演示TOML配置，省略时使用相同默认值")
    # 输出目录由用户显式选择。
    demo.add_argument("--output", type=Path, required=True, help="尚不存在的新运行目录")
    # 校验、回放、报告与研究统一使用源运行目录。
    for name, help_text in (
        ("validate", "校验运行哈希与数据契约"),
        ("replay", "在新状态库回放日志并重算决策"),
        ("report", "只读重生中文解释报告"),
        ("research", "追加隔离因子研究实验"),
    ):
        # 每个命令都在帮助中明确职责。
        command = commands.add_parser(name, help=help_text)
        # 源目录不是可以任意执行的外部指令。
        command.add_argument("--run-dir", type=Path, required=True)
        # 回放独立要求新输出，防止覆盖原交易事实。
        if name == "replay":
            # 参数与演示输出语义一致。
            command.add_argument("--output", type=Path, required=True)
    # 开发质量检查与业务交易命令分开。
    check = commands.add_parser("check", help="运行治理、pytest、Ruff和严格mypy")
    # 可选基线用于同步变更及敏感测试/风险差异检查。
    check.add_argument("--base", help="独立审查使用的Git比较基线")
    # 解析器不提供live命令或实盘开关。
    return parser


def main(argv: list[str] | None = None) -> int:
    """解析并执行CLI；成功0、运行失败1、输入错误2、风险阻断3，输出机器原因。"""
    # argparse错误会按标准约定返回SystemExit(2)。
    args = make_parser().parse_args(argv)
    # 用异常类别保持失败原因与退出码一致。
    try:
        # 开发检查不进入业务运行。
        if args.command == "check":
            # 该命令自身汇总各检查退出状态。
            return check_project(args.base)
        # 演示从已校验配置开始完整链路。
        if args.command == "demo":
            # 输出路径在应用内按独占规则创建。
            manifest = run_demo(load_config(args.config), args.output)
            # 只输出关键索引，全部详细证据已经写入产物。
            result = {
                "status": "completed",
                "run_id": manifest.run_id,
                "output": str(args.output.resolve()),
                "report": str((args.output / "report.md").resolve()),
            }
        # 校验命令不修改原运行。
        elif args.command == "validate":
            # 校验类型、哈希和历史快照语义。
            manifest = validate_run(args.run_dir)
            # 明确机器可读校验结论。
            result = {"status": "validated", "run_id": manifest.run_id}
        # 回放命令完全使用留存事实，不发送订单。
        elif args.command == "replay":
            # 原运行与回放目录必须不同且输出未存在。
            manifest = replay_run(args.run_dir, args.output)
            # 保存回放结果索引供后续验证。
            result = {
                "status": "replayed",
                "run_id": manifest.run_id,
                "output": str(args.output.resolve()),
            }
        # 报告只从结构化事实重生文字。
        elif args.command == "report":
            # 输出到stdout，由用户决定是否另存，不覆盖源文件。
            print(report_run(args.run_dir), end="")
            # 可读报告成功完成。
            return 0
        # 唯一剩余已定义命令是隔离研究。
        else:
            # 研究产物以新实验号追加，并保留失败试验。
            summary = research_run(args.run_dir)
            # 简短展示规模，完整统计已写入独立目录。
            result = {
                "status": summary["status"],
                "observations": summary["observations"],
                "folds": summary["folds"],
                "experiment": summary["experiment"],
            }
        # 默认输出JSON，便于其他人或AI编排命令。
        print(json.dumps(result, ensure_ascii=False))
        # 全链路完成才返回成功。
        return 0
    # 风险原因必须与普通故障分开。
    except RiskBlocked as exc:
        # 不建议降低阈值或反复提交来解决风险阻断。
        print(
            json.dumps({"status": "risk_blocked", "reason": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        # 上层调度可明确暂停新增风险。
        return 3
    # 文件覆盖、缺输入及模型验证均属于明确输入问题。
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        # 保留错误类别与消息，禁止静默套用默认配置。
        print(
            json.dumps(
                {"status": "input_error", "error_type": type(exc).__name__, "reason": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        # 不完整输入不应返回正常成功。
        return 2
    # 其他运行错误如数据库或权限问题仍需显式失败。
    except Exception as exc:
        # 不输出环境变量或任何凭证。
        print(
            json.dumps(
                {"status": "runtime_failure", "error_type": type(exc).__name__, "reason": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        # 保持可机器识别的通用失败码。
        return 1
