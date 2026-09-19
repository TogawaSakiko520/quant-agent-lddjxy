"""均线策略独立手算、时点扰动及原总回报策略隔离回归；全部使用脱敏样本。"""

from datetime import timedelta
from typing import Any

import pytest
from test_data import history, security_record

from quant_core.adapters.calendar import ExchangeCalendar
from quant_core.contracts import ContractError, DataSnapshot, FactorValue, seal_record
from quant_core.factors import calculate_factors, calculate_ma_factors
from quant_core.regime import assess_regime
from quant_core.research import label_observations
from quant_core.signals import score_factors, score_ma_factors


def ma_history(prices: list[float]) -> tuple[DataSnapshot, ExchangeCalendar]:
    """将给定价格放入仅拆股字段；原始价另乘二，明确测试不能误用执行价格。"""
    snapshot, calendar = history(prices)
    records = [
        seal_record(
            record.model_copy(
                update={
                    "split_adjusted_close": price,
                    "raw_close": price * 2,
                    "total_return_close": None,
                }
            )
        )
        for record, price in zip(snapshot.records, prices, strict=True)
    ]
    return snapshot.model_copy(update={"records": records}), calendar


def fixed_ma_factors(snapshot: DataSnapshot, strengths: dict[str, float]) -> list[FactorValue]:
    """独立给出均线与强度样本，只测试评分；三个字段绑定相同快照。"""
    return [
        FactorValue(
            security_id=identity,
            factor_id=factor_id,
            value=value,
            decision_time=snapshot.decision_time,
            snapshot_id=snapshot.snapshot_id,
        )
        for identity, strength in strengths.items()
        for factor_id, value in (
            ("ma5", 100 * (1 + strength)),
            ("ma20", 100),
            ("ma_trend", strength),
        )
    ]


def test_ma_means_and_strength_use_split_adjusted_twenty_day_window() -> None:
    """二十日1至20的均值10.5，最后五日均值18；更早异常值不进入窗口。"""
    snapshot, calendar = ma_history([9999.0] + [float(value) for value in range(1, 21)])
    factors = {item.factor_id: item for item in calculate_ma_factors(snapshot, calendar)}
    assert factors["ma5"].value == 18
    assert factors["ma20"].value == 10.5
    assert factors["ma_trend"].value == pytest.approx(5 / 7)
    assert all(item.reason is None for item in factors.values())
    signals = score_ma_factors(snapshot, list(factors.values()))
    assert signals.strategy_version == "ma-trend-1.0.0"
    assert signals.scores[0].components == {"ma_trend": 1.0}
    assert signals.scores[0].value == 1.0


@pytest.mark.parametrize("case", ["short", "missing_day", "missing_adjustment", "bad_quality"])
def test_ma_incomplete_or_bad_inputs_do_not_fall_back(case: str) -> None:
    """少一天、窗口缺日、缺调整价或质量失败均保留三项空结果，不回退原始价。"""
    snapshot, calendar = ma_history([100.0] * 21)
    records = list(snapshot.records)
    expected = "insufficient_or_missing_sessions"
    if case == "short":
        records = records[-19:]
    elif case == "missing_day":
        records.pop(-10)
    else:
        update = (
            {"split_adjusted_close": None}
            if case == "missing_adjustment"
            else {"quality": "quarantined"}
        )
        records[-1] = seal_record(records[-1].model_copy(update=update))
        expected = (
            "missing_split_adjusted_price" if case == "missing_adjustment" else "bad_price_quality"
        )
    factors = calculate_ma_factors(snapshot.model_copy(update={"records": records}), calendar)
    assert len(factors) == 3
    assert all(item.value is None and item.reason == expected for item in factors)


def test_future_revision_and_unfinished_bar_do_not_change_ma() -> None:
    """未来修订及当日未收盘报价不能改变上一完整二十日均线。"""
    full, calendar = ma_history([float(value) for value in range(1, 22)])
    decision = calendar.open_at(full.records[-1].session) + timedelta(minutes=1)
    snapshot = full.model_copy(update={"records": full.records[:-1], "decision_time": decision})
    expected = calculate_ma_factors(snapshot, calendar)
    future = seal_record(
        snapshot.records[-1].model_copy(
            update={
                "revision": 2,
                "split_adjusted_close": 9999.0,
                "available_at": decision + timedelta(days=1),
            }
        )
    )
    unfinished = seal_record(
        full.records[-1].model_copy(
            update={
                "event_time": decision,
                "published_at": decision,
                "available_at": decision,
                "split_adjusted_close": 99999.0,
            }
        )
    )
    changed = snapshot.model_copy(
        update={"records": [future, unfinished, *reversed(snapshot.records)]}
    )
    assert calculate_ma_factors(changed, calendar) == expected
    assert score_ma_factors(changed, calculate_ma_factors(changed, calendar)) == score_ma_factors(
        snapshot, expected
    )


def test_ma_ties_positive_gate_and_unknown_sector() -> None:
    """仅四个正趋势参与评分；并列0.1得1/6，零和负趋势明确排除，未知行业保留。"""
    snapshot, _ = ma_history([100.0] * 20)
    securities = [
        security_record(identity).model_copy(update={"sector": None}) for identity in "ABCDEF"
    ]
    snapshot = snapshot.model_copy(update={"securities": securities})
    factors = fixed_ma_factors(
        snapshot, {"A": 0.1, "B": 0.1, "C": 0.3, "D": 0.4, "E": 0, "F": -0.1}
    )
    signals = score_ma_factors(snapshot, factors)
    assert [item.security_id for item in signals.scores] == ["D", "C", "A", "B"]
    assert [item.value for item in signals.scores] == pytest.approx([1, 2 / 3, 1 / 6, 1 / 6])
    assert all(item.sector is None for item in signals.scores)
    assert signals.excluded == {"E": "non_positive_trend", "F": "non_positive_trend"}
    assert all(set(item.components) == {"ma_trend"} for item in signals.scores)


def test_ma_missing_explanation_and_all_equal_trend_produce_no_order_signal() -> None:
    """缺MA5不能只凭强度评分；MA5等于MA20时不产生正趋势信号。"""
    snapshot, calendar = ma_history([100.0] * 20)
    equal = score_ma_factors(snapshot, calculate_ma_factors(snapshot, calendar))
    assert equal.scores == []
    assert equal.excluded == {"A": "non_positive_trend"}
    incomplete = score_ma_factors(snapshot, fixed_ma_factors(snapshot, {"A": 0.1})[1:])
    assert incomplete.scores == []
    assert incomplete.excluded == {"A": "missing_factor"}


@pytest.mark.parametrize(
    "change", ["identity", "snapshot", "time", "version", "factor", "duplicate"]
)
def test_ma_scoring_rejects_mixed_identity_or_version(change: str) -> None:
    """快照、时点、稳定证券身份和因子版本必须一致，重复结果不能增加权重。"""
    snapshot, _ = ma_history([100.0] * 20)
    factors = fixed_ma_factors(snapshot, {"A": 0.1})
    if change == "duplicate":
        factors.append(factors[0])
    else:
        updates: dict[str, dict[str, Any]] = {
            "identity": {"security_id": "UNKNOWN"},
            "snapshot": {"snapshot_id": "other"},
            "time": {"decision_time": snapshot.decision_time + timedelta(seconds=1)},
            "version": {"factor_version": "2.0.0"},
            "factor": {"factor_id": "momentum"},
        }
        factors[0] = factors[0].model_copy(update=updates[change])
    with pytest.raises(ContractError):
        score_ma_factors(snapshot, factors)


def test_original_factors_and_regime_reject_missing_total_return() -> None:
    """即使有完整拆股行情，原双因子及观察器也不能冒充具有总回报研究输入。"""
    snapshot, calendar = ma_history([100.0] * 253)
    factors = calculate_factors(snapshot, calendar)
    assert all(
        item.value is None and item.reason == "missing_total_return_price" for item in factors
    )
    regime = assess_regime(snapshot.records, snapshot.decision_time, calendar)
    assert regime.state == "UNKNOWN"
    assert regime.evidence == {"reason": "missing_total_return_price"}


def test_original_scoring_keeps_missing_industry_excluded() -> None:
    """行业未知只允许独立均线策略处理；原双因子不扩大证券资格。"""
    snapshot, _ = history([100.0])
    snapshot = snapshot.model_copy(
        update={
            "securities": [
                security_record(identity).model_copy(update={"sector": None}) for identity in "AB"
            ]
        }
    )
    factors = [
        FactorValue(
            security_id=identity,
            factor_id=factor_id,
            value=1,
            snapshot_id=snapshot.snapshot_id,
            decision_time=snapshot.decision_time,
        )
        for identity in "AB"
        for factor_id in ("momentum", "low_volatility")
    ]
    signals = score_factors(snapshot, factors)
    assert signals.scores == []
    assert signals.excluded == {"A": "missing_sector", "B": "missing_sector"}


def test_research_missing_intermediate_total_return_has_no_label() -> None:
    """五日研究标签即使端点存在，中间日无总回报价也不得生成收益标签。"""
    snapshot, calendar = history([100.0] * 6)
    factor = FactorValue(
        security_id="A",
        factor_id="momentum",
        value=1,
        snapshot_id=snapshot.snapshot_id,
        decision_time=calendar.close_at(snapshot.records[0].session),
    )
    records = list(snapshot.records)
    records[3] = seal_record(
        records[3].model_copy(update={"total_return_close": None, "split_adjusted_close": 100.0})
    )
    assert label_observations([factor], records, calendar) == []


def test_ma_conflicting_revision_rejected_and_bad_latest_not_replaced() -> None:
    """同版本不同价格拒绝；最新可知修订质量失败时不回退旧好价。"""
    snapshot, calendar = ma_history([100.0] * 20)
    last = snapshot.records[-1]
    conflict = seal_record(last.model_copy(update={"split_adjusted_close": 200.0}))
    with pytest.raises(ContractError, match="版本内容冲突"):
        calculate_ma_factors(
            snapshot.model_copy(update={"records": [*snapshot.records, conflict]}), calendar
        )
    bad = seal_record(last.model_copy(update={"revision": 2, "quality": "quarantined"}))
    factors = calculate_ma_factors(
        snapshot.model_copy(update={"records": [bad, *snapshot.records]}), calendar
    )
    assert all(item.value is None and item.reason == "bad_price_quality" for item in factors)


@pytest.mark.parametrize("ma5,ma20,strength", [(90, 100, 0.1), (110, 100, 0.2), (100, 100, 0.01)])
def test_ma_score_rejects_conflicting_means_and_strength(
    ma5: float, ma20: float, strength: float
) -> None:
    """均线与强度矛盾时拒绝，而不是重算覆盖输入或凭伪正强度入选。"""
    snapshot, _ = ma_history([100.0] * 20)
    factors = [
        FactorValue(
            security_id="A",
            factor_id=identity,
            value=value,
            snapshot_id=snapshot.snapshot_id,
            decision_time=snapshot.decision_time,
        )
        for identity, value in (("ma5", ma5), ("ma20", ma20), ("ma_trend", strength))
    ]
    with pytest.raises(ContractError, match="均线与趋势强度不一致"):
        score_ma_factors(snapshot, factors)
