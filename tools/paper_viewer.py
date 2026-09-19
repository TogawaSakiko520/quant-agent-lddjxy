"""提供与交易核心解耦的本地只读证据窗口；不导入策略、券商或凭据读取器。

只读取用户指定运行目录的固定JSON文件和本仓库静态页面。没有写单、修改账本、
任意文件下载或凭据路由；未完成阶段保持待验证，离线数据不冒称真实Paper。
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast

STATIC_ROOT = Path(__file__).resolve().parents[1] / "viewer"


def _read(root: Path, name: str, default: Any) -> Any:
    """读取单个固定证据文件；缺失保留未知，软链或非普通文件明确拒绝。"""
    path = root / name
    if path.is_symlink():
        raise ValueError("展示窗口不跟随证据文件软链")
    if not path.exists():
        return default
    if not path.is_file():
        raise ValueError("证据必须是普通JSON文件")
    return json.loads(path.read_text(encoding="utf-8"))


def build_view(root: Path) -> dict[str, Any]:
    """把只读、计划或观察目录投影为脱敏展示数据，不重算因子或修改交易事实。

    账户现金来自远端原始快照，策略预算来自配置，两者分别展示。计划只含目标，
    没有远端订单与成交证据时不能宣称提交或完成。无文件表示未提供证据。
    """
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("运行目录不存在")
    config = _read(root, "config.json", {})
    manifest = _read(root, "manifest.json", {})
    if not config and manifest:
        config = manifest.get("config", {})
    mode = config.get("mode", "unknown")
    if mode not in {"paper", "offline", "unknown"}:
        raise ValueError("展示只支持Paper或明确的离线证据")
    observation = root
    directory = root / "observations"
    if directory.is_symlink():
        raise ValueError("展示窗口不跟随观察目录软链")
    if directory.is_dir():
        # 只认应用生成的数字目录，不遍历环境/凭据等其他内容。
        candidates = sorted(
            (p for p in directory.iterdir() if p.name.isdigit()), key=lambda path: int(path.name)
        )
        if candidates:
            observation = candidates[-1]
            if observation.is_symlink() or not observation.is_dir():
                raise ValueError("观察必须是本地普通目录")
    raw = _read(observation, "remote.json", None)
    if raw is None:
        raw = _read(root, "account-observation.json", {})
    remote = raw.get("account", {})
    internal = _read(observation, "account.json", {})
    if mode == "offline":
        remote = internal
    initial = _read(root, "initial.json", {})
    meta = _read(root, "read-result.json", {})
    result = _read(observation, "result.json", {})
    plan = _read(root, "paper-plan.json", {})
    assets = _read(root, "assets.json", [])
    snapshot = _read(root, "snapshot.json", {})
    identity = {row["id"]: row["symbol"] for row in assets}
    identity.update({row["security_id"]: row["ticker"] for row in snapshot.get("securities", [])})
    strategy = config.get("strategy", "weekly-two-factor")
    qualification = _read(root, "data-qualification.json", {})
    factors = _read(root, "factors.json", [])
    signals = _read(root, "signals.json", {}).get("scores", [])
    targets = _read(root, "target.json", {}).get("positions", [])
    orders = _read(observation, "orders.json", [])
    reconciliation = _read(observation, "reconciliation.json", {"matched": None, "differences": []})
    bars = _read(root, "bars.json", {})
    properties = {row["symbol"]: row for row in assets}
    sources = []
    for symbol, rows in sorted(bars.items()):
        ordered = sorted(rows, key=lambda row: row["t"])
        prop = properties.get(symbol, {})
        sources.append(
            {
                "symbol": symbol,
                "rows": len(ordered),
                "first": ordered[0]["t"] if ordered else None,
                "last": ordered[-1]["t"] if ordered else None,
                "raw_close": ordered[-1]["c"] if ordered else None,
                "tradable": prop.get("tradable"),
                "fractionable": prop.get("fractionable"),
                "closes": [{"date": row["t"][:10], "price": row["c"]} for row in ordered[-60:]],
            }
        )
    submitted = sum(bool(row.get("broker_order_id")) for row in orders)
    filled = sum(row.get("status") == "FILLED" for row in orders)
    has_fills = any(row.get("filled_quantity", 0) > 0 for row in orders)
    all_filled = bool(orders) and filled == len(orders)
    matched = reconciliation.get("matched") is True
    stage = "observed" if result or orders else "planned" if plan else "read_only"
    valid_inputs = bool(factors)
    if strategy == "ma-trend":
        # 展示不重算公式；只有完整非缺失的三个计算结果才证明存在可用输入。
        by_security: dict[str, set[str]] = {}
        for factor in factors:
            if factor.get("value") is not None and factor.get("reason") is None:
                by_security.setdefault(factor["security_id"], set()).add(factor["factor_id"])
        valid_inputs = any({"ma5", "ma20", "ma_trend"} <= ids for ids in by_security.values())
    ready = bool(valid_inputs and signals and targets)
    complete = ready and all_filled and matched
    if strategy == "ma-trend":
        complete = (
            ready
            and filled > 0
            and all(row.get("status") in {"FILLED", "CANCELED", "REJECTED"} for row in orders)
            and matched
            and result.get("restart_verified") is True
        )
    if result.get("execution_purpose") == "paper_queue_test":
        complete = False
    has_market = bool(bars or snapshot.get("records"))
    pipeline = [
        {
            "name": "账户与数据读取",
            "state": "passed"
            if (raw or internal) and has_market
            else "blocked"
            if raw or internal
            else "pending",
            "detail": "账户和行情快照已保存"
            if (raw or internal) and has_market
            else "账户已读取，行情证据缺失"
            if raw or internal
            else "尚无账户证据",
        },
        {
            "name": "合格策略输入",
            "state": "passed" if valid_inputs else "blocked",
            "detail": "因子输入快照已保存；来源资格以审计为准"
            if valid_inputs
            else "普通股身份、连续20日拆股价格及公司行动需核验"
            if strategy == "ma-trend"
            else "行业、普通股类别及总回报资料待齐备",
        },
        {
            "name": "因子、评分与目标",
            "state": "passed" if ready or (plan and factors) else "pending",
            "detail": f"{len(signals)}个评分，{len(targets)}个目标；空目标不代表成交",
        },
        {
            "name": "Paper订单提交" if mode == "paper" else "离线模拟订单",
            "state": "passed" if submitted else "pending",
            "detail": f"{submitted}笔具有券商订单ID",
        },
        {
            "name": "成交与核对",
            "state": "passed"
            if complete
            else "blocked"
            if reconciliation.get("matched") is False
            else "pending",
            "detail": f"{filled}笔完全成交；对账："
            + (
                "一致"
                if matched
                else "有差异"
                if reconciliation.get("matched") is False
                else "未验证"
            ),
        },
    ]
    limitations = [] if qualification else list(meta.get("blockers", []))
    queue_test = result.get("execution_purpose") == "paper_queue_test"
    queue_verified = (
        queue_test
        and result.get("queue_cancel_verified") is True
        and result.get("queue_restart_verified") is True
        and matched
        and len(orders) == 1
        and orders[0].get("status") == "CANCELED"
        and orders[0].get("broker_order_id") is not None
        and not has_fills
    )
    if queue_test:
        # 撤单验收是独立步骤，不能替换上方仍待验证的成交步骤。
        pipeline.append(
            {
                "name": "休市提交、撤单与重启核对",
                "state": "passed" if queue_verified else "pending",
                "detail": "远端已取消且重启核对一致；未验证成交"
                if queue_verified
                else "等待远端取消终态及独立重启核对",
            }
        )
        limitations.append("本轮为休市订单操作测试；限价使用真实收盘参考，未验证实际成交入账")
    if strategy == "ma-trend":
        limitations.append("MA使用仅拆股价格而非总回报；未知行业按最坏集中度计量")
        if not result.get("restart_verified"):
            limitations.append("本轮成交后重启核对尚未验收")
    if mode == "offline":
        limitations.insert(0, "合成数据/FakeBroker仅验证工程，不能作为真实Paper或实盘完成证据")
    if mode == "unknown":
        limitations.insert(0, "未提供可识别的运行配置，不能确定数据来源")
    if not has_fills:
        limitations.append("没有已保存成交事实；读取账户不等于策略闭环完成")
    limitations.extend(
        ["窗口只展示已保存证据，刷新不会查询账户或发送订单", "实盘未启用；本窗口没有交易权限"]
    )
    return {
        "mode": mode,
        "strategy": strategy,
        "execution_purpose": result.get("execution_purpose", "strategy"),
        "queue_verified": queue_verified,
        "excluded": _read(root, "signals.json", {}).get("excluded", {}),
        "qualification": qualification,
        "events": _read(observation, "events.json", []),
        "stage": stage,
        "title": "策略执行与核对已有证据" if complete else "策略闭环尚未完成",
        "as_of": result.get("as_of")
        or raw.get("observed_at")
        or meta.get("observed_at")
        or internal.get("as_of"),
        "account": {
            "id_suffix": str(remote.get("id", remote.get("account_id", "")))[-8:],
            "cash": remote.get("cash"),
            "buying_power": remote.get("buying_power"),
            "non_marginable_buying_power": remote.get("non_marginable_buying_power"),
            "allocated_budget": config.get("budget", config.get("initial_cash")),
            "reserve": plan.get("reserve"),
            "positions_count": len(raw.get("positions", internal.get("positions", {}))),
            "strategy_cash": internal.get("cash", initial.get("cash")),
        },
        "pipeline": pipeline,
        "sources": sources,
        "factors": factors,
        "signals": signals,
        "targets": targets,
        "orders": orders,
        "reconciliation": reconciliation,
        "risks": _read(observation, "risk.json", []),
        "limitations": limitations,
        "identity": identity,
    }


def create_server(root: Path, host: str = "127.0.0.1", port: int = 0) -> HTTPServer:
    """只在IPv4回环地址提供固定页面与JSON路由；没有任意路径、目录列表或写接口。"""
    if host != "127.0.0.1":
        raise ValueError("展示窗口只允许绑定127.0.0.1")
    build_view(root)

    class ViewerHandler(BaseHTTPRequestHandler):
        """请求处理器仅投影本地证据，拒绝跨来源Host和未列明路由。"""

        def log_message(self, format: str, *args: Any) -> None:
            """不把用户请求路径或参数写入日志，避免意外包含敏感文本。"""
            return

        def do_GET(self) -> None:
            """只响应固定静态资源与脱敏视图；不读取任意查询参数指定的路径。"""
            allowed_hosts = {
                f"127.0.0.1:{cast(HTTPServer, self.server).server_port}",
                f"localhost:{cast(HTTPServer, self.server).server_port}",
            }
            if self.headers.get("Host") not in allowed_hosts:
                self.send_error(403)
                return
            routes = {
                "/": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/style.css": ("style.css", "text/css"),
            }
            try:
                if self.path == "/api/view":
                    body = json.dumps(
                        build_view(root), ensure_ascii=False, allow_nan=False
                    ).encode()
                    content_type = "application/json"
                elif self.path in routes:
                    name, content_type = routes[self.path]
                    body = (STATIC_ROOT / name).read_bytes()
                else:
                    self.send_error(404)
                    return
            except (OSError, ValueError, KeyError, TypeError):
                self.send_error(500, "Evidence unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

    return HTTPServer((host, port), ViewerHandler)


def main(argv: list[str] | None = None) -> int:
    """显式选择证据目录并启动本地窗口服务；退出只关展示，不接触交易状态。"""
    parser = argparse.ArgumentParser(description="本地只读Paper证据窗口，无交易功能")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = create_server(args.run_dir, port=args.port)
    print(f"只读展示：http://127.0.0.1:{server.server_port}；Ctrl+C停止", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
