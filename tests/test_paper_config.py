"""Paper 配置与显式凭据读取的独立边界测试；不读取用户文件或访问账户。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from quant_core.adapters.paper_session import credential_transport
from quant_core.contracts import (
    ContractError,
    DemoConfig,
    PaperConfig,
    SecurityRecord,
    StrategyConfig,
)


def paper_fields() -> dict[str, Any]:
    """给出无秘密固定配置，预算与候选仅用于类型校验。"""
    return {
        "account_id": "paper-account",
        "candidates": ["AAA", "BBB"],
        "budget": "10000",
        "history_start": "2024-01-01",
        "history_end": "2025-02-07",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "live"},
        {"trading_endpoint": "https://api.alpaca.markets"},
        {"history_feed": "iex"},
        {"account_id": "DEMO"},
        {"candidates": ["AAA", "AAA"]},
        {"budget": "0"},
        {"history_start": "2026-01-01"},
    ],
)
def test_paper_rejects_wrong_boundaries(change: dict[str, Any]) -> None:
    """固定实盘主机、错误成交量口径及无效预算等输入都必须拒绝。"""
    with pytest.raises(ValueError):
        PaperConfig.model_validate({**paper_fields(), **change})


def test_strategy_defaults_and_demo_isolation() -> None:
    """抽取公共参数不能改变原风险数字或使离线DEMO接受真实账户。"""
    strategy = StrategyConfig(account_id="paper-account")
    paper = PaperConfig.model_validate(paper_fields())
    for config in (DemoConfig(), strategy, paper):
        assert (config.target_weight, config.max_single, config.max_sector, config.max_gross) == (
            0.045,
            0.05,
            0.25,
            0.90,
        )
        assert (config.max_positions, config.max_adv_fraction, config.max_turnover) == (
            20,
            0.01,
            1.0,
        )
        assert (config.daily_loss_limit, config.drawdown_limit, config.quote_max_age_seconds) == (
            0.03,
            0.10,
            60,
        )
    with pytest.raises(ValueError):
        DemoConfig.model_validate({"mode": "paper"})
    with pytest.raises(ValueError):
        DemoConfig.model_validate({"account_id": "paper-account"})


def test_unknown_publication_does_not_fabricate_history() -> None:
    """未知公开时间保持None，实际采集依据仍需first_seen且不得晚于可用时间。"""
    at = datetime(2025, 2, 8, tzinfo=UTC)
    fields = {
        "security_id": "asset",
        "ticker": "AAA",
        "sector": "TECH",
        "quality": "good",
        "effective_from": at.date(),
        "event_time": at,
        "published_at": None,
        "available_at": at,
        "availability_basis": "actual",
    }
    with pytest.raises(ValueError, match="first_seen_at"):
        SecurityRecord.model_validate(fields)
    valid = SecurityRecord.model_validate({**fields, "first_seen_at": at})
    assert valid.published_at is None
    with pytest.raises(ValueError):
        SecurityRecord.model_validate({**fields, "first_seen_at": "2025-02-09T00:00:00Z"})


def test_explicit_private_credentials_normalize_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v2仅规范化固定Paper地址；凭据不执行shell、不查询账户、不读取其他文件。"""
    calls: list[tuple[str, str, str]] = []

    def construct(key: str, secret: str, *, trading_url: str) -> Any:
        """捕获假凭据构造参数，独立断言调用不包含网络动作。"""
        calls.append((key, secret, trading_url))
        return object()

    monkeypatch.setattr("quant_core.adapters.paper_session.AlpacaSDKTransport", construct)
    path = tmp_path / ".env"
    path.write_text(
        "ALPACA_PAPER_API_KEY=fixture-key\nALPACA_PAPER_SECRET_KEY=fixture-secret\n"
        "ALPACA_PAPER_ENDPOINT=https://paper-api.alpaca.markets/v2\n"
        "ALPACA_PAPER_ACCOUNT_ID=paper-account\n"
    )
    path.chmod(0o600)
    credential_transport(path, "paper-account")
    assert calls == [("fixture-key", "fixture-secret", "https://paper-api.alpaca.markets")]
    with pytest.raises(ContractError, match="账户"):
        credential_transport(path, "another-account")
    path.chmod(0o644)
    with pytest.raises(ContractError, match="600"):
        credential_transport(path, "paper-account")
    with pytest.raises(ContractError):
        credential_transport(tmp_path, "paper-account")
