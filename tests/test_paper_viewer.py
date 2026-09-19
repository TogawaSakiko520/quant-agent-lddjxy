"""本地展示读取器的独立场景与保密边界；不建立真实网络连接、不读取用户凭据。"""

import json
from http.server import BaseHTTPRequestHandler
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.paper_viewer import build_view, create_server


def write_fixture(root: Path, name: str, value: Any) -> None:
    """把明确给出的脱敏事实保存为展示输入；不调用业务计算构造预期结果。"""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def readonly_case(root: Path) -> None:
    """构造远端十万美元、策略预算一万美元的只读成功，因子与订单尚不存在。"""
    root.mkdir(parents=True, exist_ok=True)
    write_fixture(
        root,
        "config.json",
        {
            "mode": "paper",
            "account_id": "fixture-paper",
            "budget": "10000",
            "candidates": ["AAA", "BBB"],
            "history_feed": "sip",
            "quote_feed": "iex",
        },
    )
    write_fixture(
        root,
        "read-result.json",
        {
            "status": "read_only",
            "account_verified": True,
            "history": "received_sip_raw",
            "observed_at": "2026-09-19T15:00:00+00:00",
            "blockers": ["缺少行业和合格总回报资料"],
        },
    )
    write_fixture(
        root,
        "account-observation.json",
        {
            "account": {
                "id": "fixture-paper",
                "cash": "100000",
                "buying_power": "400000",
                "non_marginable_buying_power": "100000",
                "status": "ACTIVE",
            },
            "positions": [],
            "orders": [],
            "open_orders": [],
            "historical_orders": [],
            "activities": [],
        },
    )
    write_fixture(
        root,
        "assets.json",
        [
            {"id": "asset-a", "symbol": "AAA", "status": "active", "tradable": True},
            {"id": "asset-b", "symbol": "BBB", "status": "active", "tradable": True},
        ],
    )
    write_fixture(
        root,
        "bars.json",
        {
            "AAA": [{"t": "2026-09-18T04:00:00Z", "o": 100, "c": 100, "v": 1000000}],
            "BBB": [{"t": "2026-09-18T04:00:00Z", "o": 200, "c": 200, "v": 2000000}],
        },
    )
    write_fixture(
        root,
        "clock.json",
        {
            "is_open": False,
            "next_open": "2026-09-21T09:30:00-04:00",
        },
    )


def plan_case(root: Path) -> None:
    """独立声明两个评分与一个三股目标；计划存在不代表订单已获券商受理。"""
    readonly_case(root)
    write_fixture(root, "paper-plan.json", {"plan_id": "fixture-plan", "reserve": "90000"})
    write_fixture(root, "initial.json", {"cash": "10000"})
    write_fixture(
        root,
        "factors.json",
        [
            {"security_id": "asset-a", "factor_id": "momentum", "value": 0.2},
            {"security_id": "asset-b", "factor_id": "momentum", "value": 0.1},
        ],
    )
    write_fixture(
        root,
        "signals.json",
        {
            "scores": [
                {"security_id": "asset-a", "value": 0.75},
                {"security_id": "asset-b", "value": 0.25},
            ]
        },
    )
    write_fixture(
        root,
        "target.json",
        {
            "positions": [
                {"security_id": "asset-a", "quantity": 3, "sector": "TECH", "weight": 0.03},
            ]
        },
    )


def observation_case(root: Path, number: str, *, status: str, filled: int, matched: bool) -> None:
    """写入独立订单观察；三股委托每股一百美元，成交股数与状态分别明示。"""
    directory = root / "observations" / number
    # 这里仅装配输入，不用被测展示器推导断言；价差和费用取零用于清楚区分两个现金口径。
    cash_by_filled = {0: "100000", 1: "99900", 3: "99700"}
    strategy_by_filled = {0: "10000", 1: "9900", 3: "9700"}
    write_fixture(directory, "result.json", {"status": "orders_observed"})
    write_fixture(
        directory,
        "remote.json",
        {
            "account": {"id": "fixture-paper", "cash": cash_by_filled[filled]},
            "positions": [{"asset_id": "asset-a", "qty": str(filled)}] if filled else [],
            "observed_at": "2026-09-21T13:32:00+00:00",
        },
    )
    write_fixture(
        directory,
        "account.json",
        {
            "cash": strategy_by_filled[filled],
            "positions": {"asset-a": filled} if filled else {},
            "as_of": "2026-09-21T13:32:00+00:00",
        },
    )
    write_fixture(
        directory,
        "orders.json",
        [
            {
                "intent": {
                    "client_order_id": "fixed-order",
                    "security_id": "asset-a",
                    "quantity": 3,
                },
                "broker_order_id": "remote-order",
                "status": status,
                "filled_quantity": filled,
            }
        ],
    )
    write_fixture(
        directory,
        "events.json",
        [
            {
                "event_id": "fill-event",
                "fill_id": "fill-id",
                "quantity": filled,
                "security_id": "asset-a",
                "price": "100",
            }
        ]
        if filled
        else [],
    )
    write_fixture(
        directory,
        "reconciliation.json",
        {
            "matched": matched,
            "differences": [] if matched else ["account_mismatch:cash"],
        },
    )


def stages(view: dict[str, Any]) -> dict[str, str]:
    """按中文阶段名称索引展示结论，测试只比较固定业务预期，不重算状态规则。"""
    return {row["name"]: row["state"] for row in view["pipeline"]}


def test_readonly_keeps_budget_separate_and_strategy_pending(tmp_path: Path) -> None:
    """账户查询成功但没有合格策略结果和订单，不能显示闭环完成。"""
    readonly_case(tmp_path)
    view = build_view(tmp_path)
    assert view["mode"] == "paper" and view["stage"] == "read_only"
    assert view["title"] == "策略闭环尚未完成"
    assert view["account"]["cash"] == "100000"
    assert view["account"]["allocated_budget"] == "10000"
    assert view["account"]["buying_power"] == "400000"
    assert view["account"]["strategy_cash"] is None
    assert stages(view)["因子、评分与目标"] == "pending"
    assert stages(view)["Paper订单提交"] == "pending"
    assert stages(view)["成交与核对"] == "pending"
    assert view["factors"] == [] and view["orders"] == []
    assert view["reconciliation"]["matched"] is None
    assert view["sources"][0]["closes"] == [{"date": "2026-09-18", "price": 100}]


def test_plan_is_not_submission_or_fill(tmp_path: Path) -> None:
    """评分和目标已保存只证明计划形成，三股目标不能冒充实际持仓。"""
    plan_case(tmp_path)
    view = build_view(tmp_path)
    assert view["stage"] == "planned"
    assert stages(view)["因子、评分与目标"] == "passed"
    assert stages(view)["Paper订单提交"] == "pending"
    assert stages(view)["成交与核对"] == "pending"
    assert view["targets"][0]["quantity"] == 3
    assert view["account"]["positions_count"] == 0
    assert view["account"]["reserve"] == "90000"
    assert view["account"]["strategy_cash"] == "10000"


@pytest.mark.parametrize(
    "status,filled,matched,state",
    [
        ("OPEN", 0, True, "pending"),
        ("PARTIAL", 1, True, "pending"),
        ("FILLED", 3, False, "blocked"),
        ("FILLED", 3, True, "passed"),
    ],
)
def test_observed_orders_do_not_conflate_submission_fill_and_reconciliation(
    tmp_path: Path, status: str, filled: int, matched: bool, state: str
) -> None:
    """受理、部分成交、全成交及核对失败分别呈现，不能只凭broker ID判闭环成功。"""
    plan_case(tmp_path)
    observation_case(tmp_path, "0001", status=status, filled=filled, matched=matched)
    view = build_view(tmp_path)
    assert view["stage"] == "observed"
    assert stages(view)["Paper订单提交"] == "passed"
    assert stages(view)["成交与核对"] == state
    assert view["orders"][0]["filled_quantity"] == filled
    assert view["reconciliation"]["matched"] is matched
    assert view["as_of"] == "2026-09-21T13:32:00+00:00"
    if filled == 3:
        assert view["account"]["cash"] == "99700"
        assert view["account"]["strategy_cash"] == "9700"
        assert view["account"]["allocated_budget"] == "10000"


def test_latest_observation_uses_numeric_directory_order(tmp_path: Path) -> None:
    """第十次观察应覆盖第二次，不因目录名称字符串排序而回退旧OPEN状态。"""
    plan_case(tmp_path)
    observation_case(tmp_path, "2", status="OPEN", filled=0, matched=True)
    observation_case(tmp_path, "10", status="FILLED", filled=3, matched=True)
    view = build_view(tmp_path)
    assert view["orders"][0]["status"] == "FILLED"
    assert view["account"]["cash"] == "99700"


def test_failed_market_read_is_not_successful_data_stage(tmp_path: Path) -> None:
    """只查到账户而行情失败时，阶段不得写成账户和行情都已保存。"""
    readonly_case(tmp_path)
    (tmp_path / "bars.json").unlink()
    write_fixture(
        tmp_path,
        "read-result.json",
        {
            "status": "read_only",
            "account_verified": True,
            "history": "blocked",
            "blockers": ["SIP权限失败"],
        },
    )
    view = build_view(tmp_path)
    assert stages(view)["账户与数据读取"] == "blocked"
    assert view["sources"] == []
    assert "SIP权限失败" in view["limitations"]


def test_offline_evidence_stays_explicitly_synthetic(tmp_path: Path) -> None:
    """FakeBroker完成记录可展示离线结果，但任何阶段都不能标为真实Paper订单。"""
    plan_case(tmp_path)
    write_fixture(tmp_path, "config.json", {"mode": "offline", "initial_cash": "10000"})
    observation_case(tmp_path, "0001", status="FILLED", filled=3, matched=True)
    view = build_view(tmp_path)
    assert view["mode"] == "offline"
    assert "Paper订单提交" not in stages(view)
    assert stages(view)["离线模拟订单"] == "passed"
    assert "合成数据/FakeBroker仅验证工程，不能作为真实Paper或实盘完成证据" in view["limitations"]


def test_empty_directory_does_not_invent_completed_stages(tmp_path: Path) -> None:
    """没有任何证据时来源未知、资金未知、订单与成交均待验证。"""
    view = build_view(tmp_path)
    assert view["mode"] == "unknown"
    assert view["account"]["cash"] is None
    assert view["account"]["allocated_budget"] is None
    assert all(row["state"] != "passed" for row in view["pipeline"])
    assert view["title"] == "策略闭环尚未完成"


@pytest.mark.parametrize("name", ["config.json", "observations"])
def test_viewer_rejects_known_file_or_observation_symlink(tmp_path: Path, name: str) -> None:
    """固定文件名也不能经软链读取指定运行目录之外的文件或观察目录。"""
    root = tmp_path / "run"
    readonly_case(root)
    destination = tmp_path / "private"
    destination.mkdir()
    if name == "config.json":
        destination = destination / "credential.json"
        destination.write_text('{"secret":"do-not-read"}')
        (root / name).unlink()
    (root / name).symlink_to(destination)
    with pytest.raises(ValueError, match="软链"):
        build_view(root)


def test_viewer_never_reads_credentials_or_changes_saved_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目录内密钥诱饵不得被读取或输出；生成视图后所有输入字节保持不变。"""
    readonly_case(tmp_path)
    secret = "FIXTURE-SECRET-MUST-NOT-APPEAR"
    (tmp_path / ".env").write_text(f"ALPACA_PAPER_SECRET_KEY={secret}")
    (tmp_path / "unrelated.json").write_text(json.dumps({"secret": secret}))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    original = Path.read_text

    def checked_read(path: Path, *args: Any, **kwargs: Any) -> str:
        """只允许展示读取约定事实文件；一旦扫描凭据或无关JSON则立即失败。"""
        assert path.name not in {".env", "unrelated.json"}
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", checked_read)
    result = build_view(tmp_path)
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == before


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.2", "example.com", "::"])
def test_viewer_rejects_non_loopback_listeners(tmp_path: Path, host: str) -> None:
    """展示不能因host参数暴露到局域网或公网；拒绝发生在建立服务之前。"""
    with pytest.raises(ValueError):
        create_server(tmp_path, host=host, port=0)


@pytest.mark.parametrize(
    "host,path,expected",
    [
        ("127.0.0.1:8765", "/api/view", 200),
        ("localhost:8765", "/api/view", 200),
        ("evil.example:8765", "/api/view", 403),
        ("127.0.0.1:8765", "/.env", 404),
        ("127.0.0.1:8765", "/config.json", 404),
        ("127.0.0.1:8765", "/../.env", 404),
        ("127.0.0.1:8765", "/api/view?path=.env", 404),
    ],
)
def test_http_routes_are_fixed_readonly_and_host_restricted_without_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, path: str, expected: int
) -> None:
    """直接调用HTTP处理器验证固定路由和Host；用假服务器避免绕过测试禁网规则。"""
    readonly_case(tmp_path)

    def fake_server(address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]) -> Any:
        """仅保存处理器，不绑定端口；独立检查真实构造参数仍限制IPv4回环。"""
        assert address == ("127.0.0.1", 0)
        return SimpleNamespace(server_port=8765, handler_class=handler_class)

    monkeypatch.setattr("tools.paper_viewer.HTTPServer", fake_server)
    server: Any = create_server(tmp_path)
    handler: Any = object.__new__(server.handler_class)
    handler.server = server
    handler.headers = {"Host": host}
    handler.path = path
    handler.wfile = BytesIO()
    responses: list[int] = []
    headers: dict[str, str] = {}

    def respond(code: int, message: str | None = None) -> None:
        """保存HTTP状态，不创建网络响应。"""
        responses.append(code)

    def record_header(name: str, value: str) -> None:
        """保存安全响应头供固定期望检查。"""
        headers[name] = value

    def end_headers() -> None:
        """内存响应无需写HTTP头终止符。"""
        return

    handler.send_error = respond
    handler.send_response = respond
    handler.send_header = record_header
    handler.end_headers = end_headers
    handler.do_GET()
    assert responses == [expected]
    assert not hasattr(server.handler_class, "do_POST")
    assert not hasattr(server.handler_class, "do_DELETE")
    if expected == 200:
        body = json.loads(handler.wfile.getvalue())
        assert body["account"]["cash"] == "100000"
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    else:
        assert handler.wfile.getvalue() == b""
