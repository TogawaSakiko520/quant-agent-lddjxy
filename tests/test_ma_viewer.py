"""MA展示及报告独立事实样本；缺值、终态和重启验收不能被界面补成成功。"""

from pathlib import Path
from typing import Any

import pytest
from test_ma_application import read_ma
from test_paper_viewer import readonly_case, stages, write_fixture

from quant_core.application import paper_plan
from tools.paper_viewer import build_view


def ma_plan_case(root: Path) -> None:
    """独立写入100/110美元均线、单候选满分、四股目标；不调用因子实现推导。"""
    readonly_case(root)
    write_fixture(root, "config.json", {"mode": "paper", "strategy": "ma-trend", "budget": "10000"})
    write_fixture(root, "paper-plan.json", {"plan_id": "ma-fixture", "reserve": "90000"})
    write_fixture(
        root,
        "data-qualification.json",
        {
            "strategy": "ma-trend",
            "price_basis": "split_adjusted",
            "qualified": ["asset-a"],
            "excluded": {"asset-b": "普通股身份未绑定"},
        },
    )
    write_fixture(
        root,
        "factors.json",
        [
            {"security_id": "asset-a", "factor_id": identity, "value": value, "reason": None}
            for identity, value in (("ma5", 110), ("ma20", 100), ("ma_trend", 0.1))
        ],
    )
    write_fixture(
        root,
        "signals.json",
        {
            "strategy_version": "ma-trend-1.0.0",
            "scores": [
                {
                    "security_id": "asset-a",
                    "sector": None,
                    "value": 1,
                    "components": {"ma_trend": 1},
                }
            ],
            "excluded": {"asset-b": "普通股身份未绑定"},
        },
    )
    write_fixture(
        root,
        "target.json",
        {"positions": [{"security_id": "asset-a", "sector": None, "quantity": 4, "weight": 0.04}]},
    )


def ma_observation(
    root: Path, number: str, *, restart: bool | None, matched: bool, statuses: tuple[str, str]
) -> None:
    """保存一笔全成与另一笔终态/活动态的独立观察；重启标记不从成交推导。"""
    directory = root / "observations" / number
    result: dict[str, Any] = {"status": "recovery_only", "as_of": "2026-09-21T14:05:00+00:00"}
    if restart is not None:
        result["restart_verified"] = restart
    write_fixture(directory, "result.json", result)
    write_fixture(
        directory,
        "orders.json",
        [
            {
                "intent": {
                    "security_id": identity,
                    "quantity": 4,
                    "client_order_id": f"client-{identity}",
                },
                "broker_order_id": f"remote-{identity}",
                "status": status,
                "filled_quantity": 4 if status == "FILLED" else 0,
            }
            for identity, status in zip(("asset-a", "asset-b"), statuses, strict=True)
        ],
    )
    write_fixture(
        directory,
        "events.json",
        [
            {
                "kind": "fill",
                "event_id": "alpaca-fill-001",
                "security_id": "asset-a",
                "quantity": 4,
                "price": "100",
                "at": "2026-09-21T14:00:05+00:00",
            }
        ]
        if "FILLED" in statuses
        else [],
    )
    write_fixture(
        directory,
        "reconciliation.json",
        {"matched": matched, "differences": [] if matched else ["account_mismatch:cash"]},
    )


def test_ma_view_keeps_values_unknown_sector_and_exclusion(tmp_path: Path) -> None:
    """界面投影保留均线原值、仅强度评分、未知行业及排除原因，不带旧缺总回报提示。"""
    ma_plan_case(tmp_path)
    view = build_view(tmp_path)
    assert view["strategy"] == "ma-trend"
    assert [(row["factor_id"], row["value"]) for row in view["factors"]] == [
        ("ma5", 110),
        ("ma20", 100),
        ("ma_trend", 0.1),
    ]
    assert view["signals"][0]["components"] == {"ma_trend": 1}
    assert view["targets"][0]["sector"] is None
    assert view["excluded"] == {"asset-b": "普通股身份未绑定"}
    assert "缺少行业和合格总回报资料" not in view["limitations"]
    assert stages(view)["Paper订单提交"] == "pending"
    assert view["title"] == "策略闭环尚未完成"


@pytest.mark.parametrize(
    "restart,matched,statuses,expected",
    [
        (None, True, ("FILLED", "CANCELED"), "pending"),
        (False, True, ("FILLED", "CANCELED"), "pending"),
        (True, True, ("FILLED", "CANCELED"), "passed"),
        (True, True, ("FILLED", "REJECTED"), "passed"),
        (True, False, ("FILLED", "CANCELED"), "blocked"),
        (True, True, ("CANCELED", "CANCELED"), "pending"),
        (True, True, ("FILLED", "OPEN"), "pending"),
        (True, True, ("FILLED", "UNKNOWN"), "pending"),
    ],
)
def test_ma_complete_needs_fill_all_terminal_match_and_restart(
    tmp_path: Path, restart: bool | None, matched: bool, statuses: tuple[str, str], expected: str
) -> None:
    """一笔全成与其他撤单可完成，但必须同时已核对和重启验证，不凭提交或旧标记推断。"""
    ma_plan_case(tmp_path)
    ma_observation(tmp_path, "0001", restart=restart, matched=matched, statuses=statuses)
    view = build_view(tmp_path)
    assert stages(view)["成交与核对"] == expected
    assert view["title"] == (
        "策略执行与核对已有证据" if expected == "passed" else "策略闭环尚未完成"
    )
    if "FILLED" in statuses:
        assert view["events"][0]["kind"] == "fill"
        assert view["events"][0]["at"] == "2026-09-21T14:00:05+00:00"


def test_ma_latest_observation_does_not_inherit_previous_restart_success(tmp_path: Path) -> None:
    """较旧观察已验收不覆盖最新现金差异，最新结果缺重启标记也不能继承旧成功。"""
    ma_plan_case(tmp_path)
    ma_observation(tmp_path, "0001", restart=True, matched=True, statuses=("FILLED", "CANCELED"))
    ma_observation(tmp_path, "0002", restart=None, matched=False, statuses=("FILLED", "CANCELED"))
    view = build_view(tmp_path)
    assert stages(view)["成交与核对"] == "blocked"
    assert "本轮成交后重启核对尚未验收" in view["limitations"]


def test_ma_all_excluded_stays_empty_with_reasons(tmp_path: Path) -> None:
    """全部证券被排除仍显示排除事实，不生成零评分、零股目标或成交。"""
    ma_plan_case(tmp_path)
    write_fixture(tmp_path, "factors.json", [])
    write_fixture(
        tmp_path,
        "signals.json",
        {
            "strategy_version": "ma-trend-1.0.0",
            "scores": [],
            "excluded": {"asset-a": "缺少连续20日", "asset-b": "普通股身份未绑定"},
        },
    )
    write_fixture(tmp_path, "target.json", {"positions": []})
    view = build_view(tmp_path)
    assert view["factors"] == view["signals"] == view["targets"] == view["orders"] == []
    assert view["excluded"] == {"asset-a": "缺少连续20日", "asset-b": "普通股身份未绑定"}
    assert stages(view)["合格策略输入"] == "blocked"
    assert view["title"] == "策略闭环尚未完成"


def test_ma_null_factor_values_do_not_pass_input_qualification(tmp_path: Path) -> None:
    """三个因子空值虽有文件也不是合格输入，不能由列表非空标成通过。"""
    ma_plan_case(tmp_path)
    write_fixture(
        tmp_path,
        "factors.json",
        [
            {
                "security_id": "asset-a",
                "factor_id": identity,
                "value": None,
                "reason": "missing_split_adjusted_price",
            }
            for identity in ("ma5", "ma20", "ma_trend")
        ],
    )
    write_fixture(
        tmp_path,
        "signals.json",
        {"scores": [], "excluded": {"asset-a": "missing_split_adjusted_price"}},
    )
    write_fixture(tmp_path, "target.json", {"positions": []})
    view = build_view(tmp_path)
    assert stages(view)["合格策略输入"] == "blocked"
    assert all(row["value"] is None for row in view["factors"])


def test_legacy_read_config_is_not_relabelled_ma(tmp_path: Path) -> None:
    """缺显式策略字段的历史只读记录仍按原双因子解释，不被新默认入口误标MA。"""
    readonly_case(tmp_path)
    view = build_view(tmp_path)
    assert view["strategy"] == "weekly-two-factor"
    assert "缺少行业和合格总回报资料" in view["limitations"]
    assert not any("MA使用" in text for text in view["limitations"])


def test_ma_report_names_price_basis_values_and_unchanged_order_stage(tmp_path: Path) -> None:
    """正式报告使用均线策略名称和原值，计划阶段不冒称已成交或沿用双因子权重。"""
    source, identity, _, _ = read_ma(tmp_path)
    root = tmp_path / "plan"
    paper_plan(source, None, root, identity_path=identity)
    report = (root / "report.md").read_text(encoding="utf-8")
    assert "策略ma-trend-1.0.0" in report
    assert "仅拆股调整收盘均价" in report
    assert "仅强度贡献评分" in report
    assert "未知（最坏集中度计量）" in report
    assert "| asset-DDD | 140.0 | 110.0 |" in report
    assert "两个因子固定等权" not in report
    assert "独立对账通过：False" in report
    assert "execution_not_started" in report
