"""MA 契约版本的独立复核；旧封印和混合存储不得被新价格字段破坏。"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from quant_core.adapters.storage import read_market_parquet, write_market_parquet
from quant_core.contracts import (
    Contract,
    DemoConfig,
    MarketDataRecord,
    PaperConfig,
    Quote,
    Score,
    SecurityRecord,
    TargetPosition,
    verify_record,
)


def old_price_payload() -> dict[str, Any]:
    """返回本轮起点公开1.0合成消息的固定副本，不跟随当前样例生成器升级。

    内容逐字段冻结自起始基线的 MarketDataRecord.valid.json；固定封印保持原值，
    不调用生产封印算法生成它。每次返回新字典，测试变体不会改写其它场景。
    """
    return {
        "availability_basis": "synthetic",
        "available_at": "2023-01-03T21:15:00Z",
        "content_hash": "a1428ad0f1d561e93a1705d548c9102fd7a60d06f7e5311df0a765848b8882c5",
        "event_time": "2023-01-03T21:00:00Z",
        "first_seen_at": None,
        "published_at": "2023-01-03T21:00:00Z",
        "quality": "good",
        "raw_close": 101.0,
        "raw_open": 100.0,
        "revision": 1,
        "schema_version": "1.0.0",
        "security_id": "EXAMPLE-S1",
        "session": "2023-01-03",
        "source": "synthetic-v1",
        "total_return_close": 101.0,
        "unit": "USD",
        "volume": 1000000,
    }


def new_price_payload() -> dict[str, Any]:
    """独立组装 MA 新行情并用标准 JSON/SHA-256 计算输入封印。

    原始价101、拆股价50.5、总回报未知；这里固定输入语义，期望仍直接写数字，
    不借用生产 seal_record 或价格算法生成测试期望。
    """
    value = old_price_payload() | {
        "schema_version": "1.1.0",
        "security_id": "MA-S1",
        "total_return_close": None,
        "split_adjusted_close": 50.5,
    }
    encoded = json.dumps(
        {key: item for key, item in value.items() if key != "content_hash"},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    value["content_hash"] = hashlib.sha256(encoded).hexdigest()
    return value


def test_published_legacy_price_keeps_exact_serialized_fields_and_seal() -> None:
    """1.0 原样例往返不插入拆股空字段，已有固定 SHA-256 仍通过验证。"""
    payload = old_price_payload()
    record = MarketDataRecord.model_validate(payload)
    assert record.content_hash == "a1428ad0f1d561e93a1705d548c9102fd7a60d06f7e5311df0a765848b8882c5"
    assert record.split_adjusted_close is None
    assert record.model_dump(mode="json") == payload
    assert json.loads(record.model_dump_json()) == payload
    verify_record(record)


@pytest.mark.parametrize("patch", [{"total_return_close": None}, {"split_adjusted_close": 50.5}])
def test_legacy_price_rejects_new_missing_research_or_split_semantics(
    patch: dict[str, Any],
) -> None:
    """旧价格消息必须保持总回报必填且不支持拆股字段，不能仅更换内容而留旧版本。"""
    with pytest.raises(ValueError):
        MarketDataRecord.model_validate(old_price_payload() | patch)


def test_new_price_preserves_missing_total_return_without_substitution() -> None:
    """1.1 明确保留未知总回报，原始价与拆股价格分别保留并参与内容封印。"""
    payload = new_price_payload()
    record = MarketDataRecord.model_validate(payload)
    assert record.raw_close == 101.0
    assert record.split_adjusted_close == 50.5
    assert record.total_return_close is None
    assert record.model_dump(mode="json") == payload
    verify_record(record)


@pytest.mark.parametrize("old_first", [True, False])
def test_mixed_price_versions_parquet_round_trip_preserves_both_seals(
    tmp_path: Path, old_first: bool
) -> None:
    """Arrow 首行推断不得丢弃后续1.1拆股字段，两种版本顺序都保留原值和旧封印。"""
    old, new = (
        MarketDataRecord.model_validate(old_price_payload()),
        MarketDataRecord.model_validate(new_price_payload()),
    )
    records = [old, new] if old_first else [new, old]
    path = tmp_path / "mixed.parquet"
    write_market_parquet(path, records)
    restored = read_market_parquet(path)
    by_id = {record.security_id: record for record in restored}
    assert by_id["MA-S1"].split_adjusted_close == 50.5
    assert by_id["MA-S1"].total_return_close is None
    assert by_id["EXAMPLE-S1"].model_dump(mode="json") == old_price_payload()
    assert by_id["MA-S1"].model_dump(mode="json") == new_price_payload()
    for record in restored:
        verify_record(record)


@pytest.mark.parametrize("kind", ["security", "score", "target"])
def test_nullable_sector_is_only_expressed_in_new_contract_version(kind: str) -> None:
    """三种行业载体均须用1.1表达未知，1.0不能悄然扩展为可空。"""
    classes: dict[str, type[SecurityRecord] | type[Score] | type[TargetPosition]] = {
        "security": SecurityRecord,
        "score": Score,
        "target": TargetPosition,
    }
    values: dict[str, dict[str, Any]] = {
        "security": {
            "security_id": "A",
            "ticker": "AAA",
            "sector": None,
            "effective_from": "2023-01-03",
            "event_time": "2023-01-03T21:00:00Z",
            "published_at": "2023-01-03T21:00:00Z",
            "available_at": "2023-01-03T21:00:00Z",
            "quality": "good",
        },
        "score": {
            "security_id": "A",
            "sector": None,
            "components": {"ma_trend": 1.0},
            "value": 1.0,
        },
        "target": {
            "security_id": "A",
            "sector": None,
            "quantity": 1,
            "weight": 0.01,
            "reason": "fixed",
        },
    }
    model, payload = classes[kind], values[kind]
    assert model.model_validate(payload | {"schema_version": "1.1.0"}).sector is None
    with pytest.raises(ValueError):
        model.model_validate(payload | {"schema_version": "1.0.0"})
    # 旧版本明确行业仍兼容，不要求升级旧数据封印或旧目标版本。
    old = model.model_validate(payload | {"schema_version": "1.0.0", "sector": "tech"})
    assert old.sector == "tech" and old.schema_version == "1.0.0"


def test_ma_is_explicit_paper_choice_and_never_changes_offline_default() -> None:
    """MA 必须显式选择，离线演示仍只允许原双因子，Paper 风险参数不能变宽。"""
    payload = {
        "account_id": "paper-a",
        "candidates": ["AAA", "BBB"],
        "budget": "10000",
        "history_start": "2023-01-01",
        "history_end": "2023-02-01",
    }
    assert PaperConfig.model_validate(payload).strategy == "weekly-two-factor"
    assert PaperConfig.model_validate(payload | {"strategy": "ma-trend"}).strategy == "ma-trend"
    assert DemoConfig().strategy == "weekly-two-factor"
    with pytest.raises(ValueError):
        DemoConfig.model_validate({"strategy": "ma-trend"})
    with pytest.raises(ValueError):
        PaperConfig.model_validate(payload | {"strategy": "ma-trend", "max_single": 0.1})


def version_schema_accepts(schema: dict[str, Any], value: str) -> bool:
    """按版本字段的 JSON Schema 关键字独立判断字符串，不调用 Pydantic 验证器。

    此处只验证公开版本字段的type/const/enum/pattern/长度语义，不实现通用Schema。
    Python搜索与JSON Schema的非全串搜索一致，可暴露末尾换行与运行时引擎差异。
    """
    return (
        schema.get("type") == "string"
        and ("const" not in schema or schema["const"] == value)
        and ("enum" not in schema or value in schema["enum"])
        and ("pattern" not in schema or re.search(schema["pattern"], value) is not None)
        and ("minLength" not in schema or len(value) >= schema["minLength"])
        and ("maxLength" not in schema or len(value) <= schema["maxLength"])
    )


@pytest.mark.parametrize("kind", ["base", "quote", "demo"])
def test_unextended_contract_versions_are_exact_in_runtime_and_schema(kind: str) -> None:
    """基类改成受约束字符串后，未扩展消息仍仅接受精确1.0.0，包括拒绝末尾换行。"""
    classes: dict[str, type[Contract]] = {"base": Contract, "quote": Quote, "demo": DemoConfig}
    payloads: dict[str, dict[str, Any]] = {
        "base": {},
        "quote": {"security_id": "A", "at": "2023-01-03T21:00:00Z", "price": "100"},
        "demo": {},
    }
    model, payload = classes[kind], payloads[kind]
    schema = model.model_json_schema()["properties"]["schema_version"]
    assert model.model_validate(payload | {"schema_version": "1.0.0"}).schema_version == "1.0.0"
    assert version_schema_accepts(schema, "1.0.0")
    for value in ("1.1.0", "9.0.0", "1x0x0", "1.0.0\n", " 1.0.0", "1.0.00"):
        with pytest.raises(ValueError):
            model.model_validate(payload | {"schema_version": value})
        assert not version_schema_accepts(schema, value)


@pytest.mark.parametrize("model", [SecurityRecord, MarketDataRecord, Score, TargetPosition])
def test_extended_version_schema_does_not_inherit_old_base_restriction(
    model: type[Contract],
) -> None:
    """四个显式扩展消息的版本Schema只接受1.0和1.1，不继承基类仅1.0的约束。"""
    schema = model.model_json_schema()["properties"]["schema_version"]
    assert schema["default"] == "1.1.0"
    assert version_schema_accepts(schema, "1.0.0")
    assert version_schema_accepts(schema, "1.1.0")
    assert not version_schema_accepts(schema, "1.2.0")
    assert not version_schema_accepts(schema, "1.1.0\n")
