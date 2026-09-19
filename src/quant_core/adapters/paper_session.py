"""Paper 会话的显式凭据读取、真实时钟和独立策略资金分配边界。

只有调用凭据读取才打开用户指定文件，不搜索默认文件；预算映射保留账户外部现金。
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from quant_core.adapters.alpaca import PAPER_URL, AlpacaPaperBroker, AlpacaSDKTransport
from quant_core.contracts import (
    AccountSnapshot,
    ContractError,
    FillEvent,
    OrderEvent,
    OrderIntent,
    OrderRecord,
)


class SystemClock:
    """仅在适配器读取真实 UTC 时间，核心依赖注入此对象而不访问系统时钟。"""

    def now(self) -> datetime:
        """返回本次实际处理时间；不是行情发布时间或交易日标签。"""
        return datetime.now(UTC)


def credential_transport(path: Path, account_id: str) -> AlpacaSDKTransport:
    """读取用户显式指定的私有文件并构造客户端；不执行 shell 或自动读取其他密钥。

    仅允许本项目四个键；文件不得是软链或向其他用户开放。错误不输出文件内容，
    密钥不写入环境、报告或配置对象。账户 ID 必须与非秘密 Paper 配置一致。
    """
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077:
        raise ContractError("凭据文件必须是权限 600 的普通文件且不能是软链")
    values: dict[str, str] = {}
    allowed = {
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_SECRET_KEY",
        "ALPACA_PAPER_ENDPOINT",
        "ALPACA_PAPER_ACCOUNT_ID",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in allowed or key in values:
            raise ContractError("凭据文件键名无效或重复；内容未输出")
        values[key] = value.strip().strip("\"'")
    # 控制台展示版本路径，SDK 自行添加版本；只规范化这两个明确的 Paper 地址。
    if values.get("ALPACA_PAPER_ENDPOINT", "").rstrip("/") not in {PAPER_URL, PAPER_URL + "/v2"}:
        raise ContractError("凭据文件端点不是固定 Paper 端点")
    if values.get("ALPACA_PAPER_ACCOUNT_ID") != account_id:
        raise ContractError("凭据文件账户与指定 Paper 账户不一致")
    return AlpacaSDKTransport(
        values.get("ALPACA_PAPER_API_KEY", ""),
        values.get("ALPACA_PAPER_SECRET_KEY", ""),
        trading_url=PAPER_URL,
    )


class BudgetBroker:
    """向原执行服务提供明确分配的策略现金，同时保留远端账户的未分配现金。

    只用于首次无持仓、无开放订单的账户建仓；不支持卖出或重用卖款。reserve 是初始
    远端现金减去批准预算的固定差额。核对时只减此常数，不用当前差异修补账本。
    所有订单仍由 ExecutionService 发起，包装器追加本轮授权的范围和金额硬边界。
    """

    def __init__(
        self,
        broker: AlpacaPaperBroker,
        reserve: Decimal,
        security_ids: set[str],
        max_orders: int,
        max_order_notional: Decimal,
    ) -> None:
        """保存既定预算映射和本次订单边界；不查账户、不发送订单。"""
        if (
            not reserve.is_finite()
            or reserve < 0
            or max_orders < 1
            or not max_order_notional.is_finite()
            or max_order_notional <= 0
        ):
            raise ContractError("Paper 预算或订单边界无效")
        self.broker = broker
        self.reserve = reserve
        self.security_ids = security_ids
        self.max_orders = max_orders
        self.max_order_notional = max_order_notional

    def account(self) -> AccountSnapshot:
        """从远端事实减固定未分配现金；负预算余额或范围外持仓阻断，不改远端。"""
        actual = self.broker.account()
        if set(actual.positions) - self.security_ids or actual.cash < self.reserve:
            raise ContractError("远端持仓越界或已侵占未分配现金")
        return AccountSnapshot(
            account_id=actual.account_id,
            as_of=actual.as_of,
            cash=actual.cash - self.reserve,
            available_cash=actual.available_cash - self.reserve,
            positions=actual.positions,
            fees=actual.fees,
            cost_basis=actual.cost_basis,
        )

    def orders(self) -> list[OrderRecord]:
        """读取完整本次远端订单；未知订单由底层适配器阻断。"""
        return self.broker.orders()

    def events(self) -> list[OrderEvent | FillEvent]:
        """读取真实新增成交；金额和股数不因预算映射缩放。"""
        return self.broker.events()

    def query(self, client_order_id: str) -> OrderRecord | None:
        """用原稳定身份恢复，查询不到不自动重发。"""
        return self.broker.query(client_order_id)

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """对唯一执行入口的意图再检查本轮范围、只买入和最大订单金额。"""
        if intent.security_id not in self.security_ids or intent.side != "BUY":
            raise ContractError("本阶段仅授权候选范围内首次买入建仓")
        if intent.limit_price * intent.quantity + intent.reserved_fee > self.max_order_notional:
            raise ContractError("订单金额含费用预留超过本轮批准上限")
        if len(self.broker.orders()) >= self.max_orders:
            raise ContractError("本轮订单数量已到批准上限")
        return self.broker.submit(intent)

    def cancel(self, client_order_id: str) -> OrderRecord:
        """仅将执行服务已识别的本次订单交给底层撤单，不调用撤销全部订单。"""
        return self.broker.cancel(client_order_id)
