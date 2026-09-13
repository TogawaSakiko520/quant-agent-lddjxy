"""公共数据与服务契约；统一时间、金额和身份，不访问文件、网络或真实时钟。

金额为美元 Decimal，价格研究使用浮点数；边界对象拒绝额外字段和未知版本。
"""

# 延迟类型求值，允许协议引用后面声明的对象。
from __future__ import annotations

# 标准库只承担确定性的编码、哈希、时间类型和协议声明。
import hashlib

# JSON 编码用于跨模块一致的内容指纹。
import json

# 浮点比较使用全项目固定容差，金额与数量仍精确比较。
import math

# 日期类型表示交易日，时间戳表示 UTC 事件时间。
from datetime import date, datetime, timedelta

# 十进制金额避免现金账务的二进制浮点误差。
from decimal import Decimal

# Literal 约束有限状态，Protocol 定义可注入的服务边界。
from typing import Annotated, Any, ContextManager, Literal, Protocol, Self, TypeVar

# Pydantic 负责边界结构、数值约束与时点关系校验。
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

# 订单状态包含不确定和撤单中，不能把超时当作拒单。
OrderStatus = Literal[
    "PERSISTED", "OPEN", "PARTIAL", "FILLED", "REJECTED", "CANCEL_PENDING", "CANCELED", "UNKNOWN"
]


class ContractError(ValueError):
    """输入违反共同契约；调用方必须明确报告并停止依赖该输入的操作。"""


class RiskBlocked(RuntimeError):
    """操作被风险规则阻止；与输入错误和运行故障分开报告。"""


class Contract(BaseModel):
    """所有公共对象的严格基类；验证失败抛 ValueError，无外部副作用。"""

    # 冻结对象字段并拒绝未定义字段，避免调用者静默拼写错误。
    model_config = ConfigDict(
        frozen=True, extra="forbid", validate_default=True, allow_inf_nan=False
    )
    # 公共版本必须精确匹配；新版本需显式迁移。
    schema_version: Literal["1.0.0"] = "1.0.0"

    @field_validator("*", mode="after")
    @classmethod
    def require_utc(cls, value: Any, info: ValidationInfo) -> Any:
        """校验单字段时间为 UTC；返回原值，非 UTC 抛 ValueError，无副作用。"""
        # 日期不携带时区；只有 datetime 字段需要校验 UTC。
        if isinstance(value, datetime) and value.utcoffset() != timedelta(0):
            # 拒绝无时区时间以及非零 UTC 偏移，避免历史比较歧义。
            raise ValueError("时间戳必须明确使用 UTC")
        # 身份字段不允许空白，否则幂等与证券关联失去意义。
        if isinstance(value, str) and (info.field_name or "").endswith("_id") and not value.strip():
            # 在输入边界拒绝无身份对象。
            raise ValueError("身份字段不能为空")
        # 不改变输入精度及非时间字段类型。
        return value


class StampedRecord(Contract):
    """带来源和可用时间的记录；用于原始输入与追加修订，不自行读取系统时间。"""

    # 事件发生或数据所属期间的时间，不代表策略已经可知。
    event_time: datetime
    # 来源第一次公开该条版本的时间。
    published_at: datetime
    # 本系统实际首次观测时间；历史未知时诚实留空。
    first_seen_at: datetime | None = None
    # 策略可以使用该版本的最早时间。
    available_at: datetime
    # 明确可用时间的证据来源，合成数据不能冒充真实到达记录。
    availability_basis: Literal["synthetic", "actual", "vendor", "conservative_delay"] = "synthetic"
    # 来源标识贯穿快照和解释链。
    source: str = "synthetic-v1"
    # 修订以递增版本追加，不覆盖旧记录。
    revision: int = Field(default=1, ge=1)
    # 字段单位由具体记录进一步限定。
    unit: str = "USD"
    # 质量未通过的输入不能进入正常业务计算。
    quality: Literal["good", "quarantined"]
    # 建立记录后由 seal_record 计算，接入时必须核验。
    content_hash: str = ""

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        """校验公开、观测和可用的顺序；返回自身，矛盾时抛 ValueError，无副作用。"""
        # 策略不可能在来源公开之前使用此版本。
        if self.available_at < self.published_at:
            # 暴露潜在未来信息泄漏而非自动修补时间。
            raise ValueError("available_at 不得早于 published_at")
        # 真实到达依据必须保留实际观测记录。
        if self.availability_basis == "actual" and self.first_seen_at is None:
            # 不能为了历史回放补造实际观测证据。
            raise ValueError("actual 依据必须提供 first_seen_at")
        # 本系统仅处理公开材料，观测时间不能早于来源公开。
        if self.first_seen_at is not None and self.first_seen_at < self.published_at:
            # 未公开观测不属于本轮授权数据契约。
            raise ValueError("first_seen_at 不得早于 published_at")
        # 已知到达时间不能晚于声明的可用时间。
        if self.first_seen_at is not None and self.first_seen_at > self.available_at:
            # 同时验证迟到数据的可用时间。
            raise ValueError("available_at 不得早于 first_seen_at")
        # 所有约束成立后保持原对象。
        return self


class SecurityRecord(StampedRecord):
    """历史证券主表版本；有效区间和可用时间共同限制历史查询。"""

    # 稳定身份不随股票代码变化。
    security_id: str
    # 股票代码只用于该有效区间的展示和适配。
    ticker: str
    # 行业分类用于历史组合约束。
    sector: str
    # 证券状态开始生效的交易日。
    effective_from: date
    # 结束日期不包含当天；空值表示尚无已知终点。
    effective_to: date | None = None
    # 第一版只对普通股生成策略信号，基准单独标记。
    asset_type: Literal["common_stock", "benchmark"] = "common_stock"
    # 上市状态与是否可交易分别表达。
    listed: bool = True
    # 停牌证券仍可存在实际持仓。
    tradable: bool = True
    # 主表不使用金额单位。
    unit: Literal["identity"] = "identity"


class MarketDataRecord(StampedRecord):
    """交易日原始行情及独立研究价格；价格单位美元，成交量单位股。"""

    # 关联稳定证券身份。
    security_id: str
    # 纽约市场交易日标签。
    session: date
    # 原始开盘价仅用于合成研究标签，不倒填盘后订单。
    raw_open: float = Field(gt=0, allow_inf_nan=False)
    # 原始收盘价用于真实口径估值。
    raw_close: float = Field(gt=0, allow_inf_nan=False)
    # 独立总回报价格用于因子，不能作为订单报价。
    total_return_close: float = Field(gt=0, allow_inf_nan=False)
    # 原始成交量用于流动性预算。
    volume: int = Field(ge=0)
    # 价格字段统一美元，成交量语义由字段定义固定为股。
    unit: Literal["USD"] = "USD"


class Quote(Contract):
    """执行时注入的原始价格事件；不能由未知的盘内路径推测成交。"""

    # 价格归属证券。
    security_id: str
    # 明确事件发生时刻。
    at: datetime
    # 原始美元价格保持十进制表示。
    price: Decimal = Field(gt=0, allow_inf_nan=False)
    # 停牌事件明确禁止执行。
    tradable: bool = True


class CorporateAction(StampedRecord):
    """离线拆股或现金股息事实；股息数量由权益事件固定，不能按支付日仓位猜测。"""

    # 现金分配只支持美元；拆股比例另为无量纲新旧股数比。
    unit: Literal["USD"] = "USD"
    # 唯一公司行动身份用于幂等。
    action_id: str
    # 关联稳定证券身份。
    security_id: str
    # 首版只接受已定义的两种公司行动。
    kind: Literal["split", "dividend"]
    # 拆股比例为新股数除以旧股数。
    ratio: Decimal = Field(default=Decimal("1"), gt=0)
    # 每股现金股息，单位美元。
    cash_per_share: Decimal = Field(default=Decimal("0"), ge=0)
    # 股息权益数量来自固定的权益记录。
    entitlement_quantity: int = Field(default=0, ge=0)


class DataSnapshot(Contract):
    """已冻结的历史可知输入；版本指纹包含证券和行情，输出不得原地覆盖。"""

    # 快照稳定身份由内容指纹产生。
    snapshot_id: str
    # 所有选中记录必须不晚于此决策时间可用。
    decision_time: datetime
    # 交易日历及证券主表的版本参与复现。
    calendar_version: str
    # 按时点选中的证券主表记录。
    securities: list[SecurityRecord]
    # 按时点选中的行情版本。
    records: list[MarketDataRecord]
    # 内容哈希不包含此字段本身。
    content_hash: str


class FactorDefinition(Contract):
    """稳定因子说明；公式、窗口和单位由测试与文档共同约束。"""

    # 因子身份与参数版本分离。
    factor_id: str
    # 因子定义变更必须升级版本。
    version: str = "1.0.0"
    # 可检验的经济假设，不承诺超额收益。
    hypothesis: str
    # 人可读公式对应唯一计算实现。
    formula: str
    # 最少完整价格观测数。
    minimum_prices: int
    # 输出计量单位。
    unit: str
    # 首版全部方向对齐为越大越优。
    direction: Literal["higher_is_better"] = "higher_is_better"


class FactorValue(Contract):
    """某个证券的因子原值及输入溯源；缺失时用原因表达，不补零。"""

    # 对应证券身份。
    security_id: str
    # 对应稳定因子身份。
    factor_id: str
    # 对应公式版本。
    factor_version: str = "1.0.0"
    # 计算时点。
    decision_time: datetime
    # 绑定冻结输入。
    snapshot_id: str
    # 有效数值必须有限，缺失为 None。
    value: float | None = Field(default=None, allow_inf_nan=False)
    # 排除理由用于解释覆盖率。
    reason: str | None = None


class Score(Contract):
    """共同合格股票池的方向一致评分，不解释为预期收益。"""

    # 稳定身份也是最终并列排序键。
    security_id: str
    # 历史行业分类用于组合约束。
    sector: str
    # 每因子百分位分数。
    components: dict[str, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]]
    # 固定等权综合分。
    value: float = Field(ge=0, le=1, allow_inf_nan=False)


class SignalSet(Contract):
    """策略输出评分列表与排除原因；策略不能直接生成券商副作用。"""

    # 固定输入与策略版本确定决策身份。
    decision_id: str
    # 策略已知信息的截止时间。
    decision_time: datetime
    # 输入快照身份。
    snapshot_id: str
    # 策略版本用于审计与订单幂等。
    strategy_version: str = "weekly-two-factor-1.0.0"
    # 按综合分降序、稳定身份升序排列。
    scores: list[Score]
    # 记录无资格、缺历史和异常等排除原因。
    excluded: dict[str, str] = Field(default_factory=dict)


class RegimeAssessment(Contract):
    """透明市场状态观察；首版不改变组合风险预算。"""

    # 输出对应决策时点。
    as_of: datetime
    # 趋势与波动组合成可解释标签，历史不足为 UNKNOWN。
    state: str
    # 规则版本固定阈值及确认规则。
    version: str = "observer-1.0.0"
    # 保留数值与状态切换理由。
    evidence: dict[str, float | str]
    # 明确首版只观察，不让未知状态暗中改变仓位。
    affects_budget: Literal[False] = False


class DemoConfig(Contract):
    """仅适用于离线工程的显式参数；拒绝 live 模式，无真实账户授权含义。"""

    # 不接受实盘或联网模拟模式。
    mode: Literal["offline"] = "offline"
    # 明确参数用途，禁止去掉演示标签后当作批准的风险预算。
    demo_only: Literal[True] = True
    # 虚拟账户身份。
    account_id: Literal["DEMO"] = "DEMO"
    # 固定种子只用于合成样本。
    seed: int = 1729
    # 合成历史开始日。
    start: date = date(2020, 9, 1)
    # 合成历史结束日。
    end: date = date(2023, 12, 29)
    # 合成普通股数量，市场基准另计。
    securities: int = Field(default=30, ge=2, le=100)
    # 初始模拟现金，单位美元。
    initial_cash: Decimal = Field(default=Decimal("100000"), gt=0)
    # 最多目标持仓数。
    max_positions: int = Field(default=20, ge=1)
    # 每个入选证券的基础目标权重。
    target_weight: float = Field(default=0.045, gt=0, le=1)
    # 总仓位、单票和行业约束全部为净值比例。
    max_gross: float = Field(default=0.90, gt=0, le=1)
    # 单票上限不得通过目标归一化突破。
    max_single: float = Field(default=0.05, gt=0, le=1)
    # 行业额度不足时留现金。
    max_sector: float = Field(default=0.25, gt=0, le=1)
    # 单次调仓买卖总额相对期初净值上限。
    max_turnover: float = Field(default=1.0, gt=0, le=2)
    # 单订单最多占历史 20 日平均成交量的比例。
    max_adv_fraction: float = Field(default=0.01, gt=0, le=1)
    # 模拟费用每股美元数。
    fee_per_share: Decimal = Field(default=Decimal("0.005"), ge=0)
    # 每订单最低费用美元数。
    minimum_fee: Decimal = Field(default=Decimal("1"), ge=0)
    # 买入资金预留的价格变动比例。
    price_buffer: Decimal = Field(default=Decimal("0.01"), ge=0)
    # 演示日内亏损触发禁止新增风险。
    daily_loss_limit: float = Field(default=0.03, gt=0, le=1)
    # 演示高点回撤触发禁止新增风险。
    drawdown_limit: float = Field(default=0.10, gt=0, le=1)
    # 原始执行报价最大允许年龄，单位秒。
    quote_max_age_seconds: int = Field(default=60, ge=0)
    # 排名缓冲首版明确为零。
    ranking_buffer: Literal[0] = 0
    # 合成成交即结算不能迁移为真实账户制度。
    settlement: Literal["simulated_immediate_settlement"] = "simulated_immediate_settlement"

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        """检查参数内在关系；返回自身，矛盾时抛 ValueError，无副作用。"""
        # 基础目标不能主动违反单票上限。
        if self.target_weight > self.max_single or self.start >= self.end:
            # 拒绝不合理日期或风险配置，不静默截断设置。
            raise ValueError("目标权重不得超过单票上限，历史开始日必须早于结束日")
        # 合法配置作为不可变对象返回。
        return self


class AccountSnapshot(Contract):
    """经适配器确认的账户事实；首版持仓整股、现金美元、禁止空头。"""

    # 账户身份必须和执行授权一致。
    account_id: str = "DEMO"
    # 事实查询时刻由注入时钟提供。
    as_of: datetime
    # 现金事实不含未成交卖单预期收入。
    cash: Decimal = Field(ge=0)
    # 适配器明确可用的现金；未结算现金不可擅自视为可用。
    available_cash: Decimal = Field(ge=0)
    # 稳定证券 ID 到实际整股数量。
    positions: dict[str, Annotated[int, Field(strict=True, ge=0)]] = Field(default_factory=dict)
    # 累计成交费用便于独立对账。
    fees: Decimal = Field(default=Decimal("0"), ge=0)
    # 持仓总成本，拆股后总成本保持不变。
    cost_basis: dict[str, Annotated[Decimal, Field(ge=0)]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_cash(self) -> Self:
        """校验无杠杆可用现金与身份；返回自身，非法值抛 ValueError，无副作用。"""
        # 可用现金不能包含尚未成交卖出收入或额外授信。
        if self.available_cash > self.cash or any(not key.strip() for key in self.positions):
            # 这类账户事实需要独立恢复，不能视为合法模拟输入。
            raise ValueError("可用现金不得超过现金，持仓证券身份不能为空")
        # 合法状态继续进入业务风险判断。
        return self


class TargetPosition(Contract):
    """一个证券的约束后目标；不可交易旧仓必须保留并说明。"""

    # 目标归属证券身份。
    security_id: str
    # 时点行业用于解释预算。
    sector: str
    # 约束后的目标权重。
    weight: float = Field(ge=0)
    # 按原始价格整股取整后的目标数量。
    quantity: int = Field(ge=0)
    # 目标产生或旧仓保留的理由。
    reason: str


class TargetPortfolio(Contract):
    """组合层输出目标，不触碰券商；留现金和未完成目标均明确记录。"""

    # 绑定策略决策。
    decision_id: str
    # 目标时点。
    as_of: datetime
    # 决策时净值，作为换手统一分母。
    nav: Decimal
    # 包含目标证券及必须保留的不可交易旧仓。
    positions: list[TargetPosition]
    # 无法分配部分保留为现金权重。
    cash_weight: float
    # 约束不可行等原因不能隐藏。
    reasons: list[str] = Field(default_factory=list)


class RiskDecision(Contract):
    """分开表达新增、撤单和减仓权限，避免万能清仓开关。"""

    # 是否允许该具体操作。
    allowed: bool
    # 具体操作类别。
    operation: Literal["NEW", "CANCEL", "REPLACE", "REDUCE"]
    # 是否冻结新增风险。
    freeze_new_risk: bool = False
    # 可机器判断、可报告的原因代码。
    reasons: list[str] = Field(default_factory=list)


class OrderIntent(Contract):
    """提交前持久化的稳定订单意图；只允许整股限价，避免无界价格预算。"""

    # 客户端幂等键不随重试和 run_id 改变。
    client_order_id: str
    # 订单所属账户。
    account_id: str
    # 对应调仓决策。
    decision_id: str
    # 固定策略版本。
    strategy_version: str = "weekly-two-factor-1.0.0"
    # 对应证券身份。
    security_id: str
    # 买入或卖出，禁止用负数数量编码方向。
    side: Literal["BUY", "SELL"]
    # 总委托股数。
    quantity: int = Field(gt=0, strict=True)
    # 买入最大价格或卖出最低价格，单位美元。
    limit_price: Decimal = Field(gt=0)
    # 意图创建时刻必须由注入时钟提供。
    created_at: datetime
    # 执行不得早于此时刻。
    eligible_at: datetime
    # 该订单总费用预留。
    reserved_fee: Decimal = Field(default=Decimal("0"), ge=0)


class OrderRecord(Contract):
    """券商订单或内部意图投影；成交金额事实由 FillEvent 决定。"""

    # 持久化的原始意图。
    intent: OrderIntent
    # 当前订单状态包含未知和撤单中。
    status: OrderStatus = "PERSISTED"
    # 券商分配的身份可以在超时恢复时补齐。
    broker_order_id: str | None = None
    # 累计成交数量仅作为订单状态参考。
    filled_quantity: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_status_quantity(self) -> Self:
        """校验状态与累计成交量一致；返回自身，矛盾抛 ValueError，无副作用。"""
        # 成交量不得超过原始委托数量。
        if self.filled_quantity > self.intent.quantity:
            # 拒绝超额成交投影。
            raise ValueError("累计成交超过委托数量")
        # 已全部成交必须具有完整成交数量。
        if self.status == "FILLED" and self.filled_quantity != self.intent.quantity:
            # 终态不能掩盖缺失的成交事实。
            raise ValueError("FILLED 数量必须等于委托数量")
        # 部分成交必须严格位于零与原数量之间。
        if self.status == "PARTIAL" and not 0 < self.filled_quantity < self.intent.quantity:
            # 排除零成交或全成交伪装的部分状态。
            raise ValueError("PARTIAL 数量必须大于零且小于委托数量")
        # 未成交类状态不允许携带已有成交。
        if self.status in {"OPEN", "REJECTED", "PERSISTED"} and self.filled_quantity != 0:
            # 拒绝通过回退订单状态隐藏成交。
            raise ValueError("未成交状态不能携带成交数量")
        # 合法投影继续交给独立对账核查事件完整性。
        return self


class OrderEvent(Contract):
    """订单状态事件；本身不得再次改变现金或持仓。"""

    # 来源事件身份用于去重和冲突检测。
    event_id: str
    # 固定事件类型支持序列化分派。
    kind: Literal["order"] = "order"
    # 事件来源必须留存。
    source: str = "fake-broker"
    # 对应客户端订单。
    client_order_id: str
    # 券商订单身份。
    broker_order_id: str
    # 订单状态不能按字符串或枚举大小简单排序。
    status: OrderStatus
    # 券商报告的累计成交量。
    filled_quantity: int = Field(default=0, ge=0)
    # 事件实际发生时刻。
    at: datetime
    # 券商单订单序号用于处理乱序状态事件。
    sequence: int = Field(ge=0)


class FillEvent(Contract):
    """独立成交事实；价格原始美元、数量整股、费用美元，仅一次入账。"""

    # 来源事件身份。
    event_id: str
    # 序列化分派使用明确类型。
    kind: Literal["fill"] = "fill"
    # 事件来源构成幂等范围。
    source: str = "fake-broker"
    # 券商独立成交身份用于第二层唯一约束。
    fill_id: str
    # 成交所属账户。
    account_id: str
    # 可追溯至持久化意图，人工交易允许外部身份。
    client_order_id: str
    # 券商订单身份。
    broker_order_id: str
    # 成交证券。
    security_id: str
    # 成交方向。
    side: Literal["BUY", "SELL"]
    # 本次新增成交股数而非累计数量。
    quantity: int = Field(gt=0, strict=True)
    # 原始成交价格。
    price: Decimal = Field(gt=0)
    # 本次新增费用。
    fee: Decimal = Field(ge=0)
    # 实际成交时刻。
    at: datetime


class ReconciliationResult(Contract):
    """内部账本与独立券商状态比较结果；报表不得修正差异事实。"""

    # 对账时点。
    as_of: datetime
    # 全部关键事实一致才允许恢复新增风险。
    matched: bool
    # 现金、费用、数量及订单差异的明确描述。
    differences: list[str]


class CandidateMarketEvent(StampedRecord):
    """market-intel 的隔离候选契约；内容是待验证数据，没有交易授权语义。"""

    # 候选事件稳定身份。
    candidate_id: str
    # 关联稳定证券身份，没有映射时保持空列表。
    security_ids: list[str]
    # 事实类别由来源证据支持。
    event_type: str
    # 原始来源位置仅留存，不自动打开或执行。
    source_url: str
    # 结构化事实与推断分开保存。
    facts: dict[str, str | float]
    # 分析推断不进入已批准因子表。
    inference: str = ""
    # 提取器和提示词版本便于追踪 LLM 后验风险。
    extractor_version: str
    # 本轮固定为候选，禁止调用者自行升为已批准。
    approval_status: Literal["candidate"] = "candidate"
    # 事后提取历史材料必须披露后验限制。
    historical_limitations: str = "未以前向影子数据验证，不能回填为历史已知特征"


class RunManifest(Contract):
    """复现清单；承诺固定输入重算和事件回放，不承诺真实市场再次同价成交。"""

    # 当前运行目录身份。
    run_id: str
    # 对应稳定调仓决策。
    decision_id: str
    # 固定决策时间。
    decision_time: datetime
    # 代码提交、工作树摘要与未提交状态。
    code: dict[str, str | bool]
    # 依赖锁与实际运行环境。
    environment: dict[str, str]
    # 完整演示参数。
    config: DemoConfig
    # 初始账户事实用于离线恢复。
    initial_account: AccountSnapshot
    # 快照、证券主表与日历版本。
    inputs: dict[str, str]
    # 每个不可变输入/输出文件的内容哈希。
    artifacts: dict[str, str]
    # 限制说明随结果一同交付。
    limitations: list[str]


# 泛型保留具体记录类型，使封印函数不丢失调用者类型信息。
RecordT = TypeVar("RecordT", bound=StampedRecord)


def canonical_hash(value: Any) -> str:
    """计算可 JSON 编码值的 SHA-256；返回十六进制摘要，非法值抛错，无副作用。"""
    # 严格禁用 NaN 并固定键顺序与分隔符，保证同输入同指纹。
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    # UTF-8 是所有共享 JSON 的统一编码。
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def seal_record(record: RecordT) -> RecordT:
    """为已验证记录计算内容哈希；返回同类型新对象，不修改输入或访问存储。"""
    # 内容指纹排除自身字段，避免循环定义。
    digest = canonical_hash(record.model_dump(mode="json", exclude={"content_hash"}))
    # 字段只有指纹改变，其余已经通过模型验证。
    return record.model_copy(update={"content_hash": digest})


def verify_record(record: StampedRecord) -> None:
    """核验记录封印；成功返回 None，缺失或冲突抛 ContractError，无副作用。"""
    # 重新计算内容指纹，不能仅检查哈希字段非空。
    expected = canonical_hash(record.model_dump(mode="json", exclude={"content_hash"}))
    # 任何内容修改或未封印记录都不能进入批准快照。
    if record.content_hash != expected:
        # 不自动重签接收到的可疑数据。
        raise ContractError("内容哈希缺失或不匹配")


class Clock(Protocol):
    """注入时钟；业务核心不直接查询真实系统时间。"""

    def now(self) -> datetime:
        """返回 UTC 当前业务时刻；实现不得在纯业务中隐藏系统时钟读取。"""
        # 协议方法由具体适配器实现。
        ...


class TradingCalendar(Protocol):
    """市场日历协议；日期范围由适配器显式固定。"""

    def sessions(self, start: date, end: date) -> list[date]:
        """返回闭区间交易日列表；越界抛 ValueError，无业务状态副作用。"""
        # 具体节假日逻辑属于日历适配器。
        ...

    def open_at(self, session: date) -> datetime:
        """返回交易日 UTC 常规开盘时刻；非交易日抛 ValueError。"""
        # 协议不指定第三方日历类型。
        ...

    def close_at(self, session: date) -> datetime:
        """返回交易日 UTC 常规收盘时刻；包括半日市。"""
        # 调仓调度必须使用实际日历收盘时刻。
        ...

    def next_session(self, session: date) -> date:
        """返回严格晚于参数的下一交易日；越界抛 ValueError。"""
        # 周末和节假日由适配器处理。
        ...

    def is_open(self, at: datetime) -> bool:
        """判断 UTC 时刻是否处于常规时段，开盘包含、收盘不包含。"""
        # 业务不能用工作日近似真实交易时段。
        ...


class DataPortal(Protocol):
    """历史数据访问边界；任何查询必须带明确决策时点。"""

    def snapshot(self, decision_time: datetime) -> DataSnapshot:
        """返回当时可知且通过质量校验的快照；失败抛 ContractError。"""
        # 存储来源不向业务核心泄漏。
        ...


class Broker(Protocol):
    """唯一执行服务调用的券商接口；首版只有独立离线模拟实现。"""

    def submit(self, intent: OrderIntent) -> OrderRecord:
        """提交稳定意图；可产生券商副作用，超时不代表未接受。"""
        # 具体订单生命周期属于适配器。
        ...

    def query(self, client_order_id: str) -> OrderRecord | None:
        """按客户身份查询；返回 None 也不得据此盲目换键重发。"""
        # 查询未能找到订单与确定拒绝是不同事实。
        ...

    def cancel(self, client_order_id: str) -> OrderRecord:
        """请求撤单并返回当时状态；请求成功不代表已撤单。"""
        # 撤单竞态仍可能产生真实成交。
        ...

    def orders(self) -> list[OrderRecord]:
        """返回券商已知订单快照；用于独立恢复与对账。"""
        # 包含终态记录便于恢复成交缺口。
        ...

    def events(self) -> list[OrderEvent | FillEvent]:
        """返回可重复投递的事件；消费者必须自行去重。"""
        # 读取事件不应清空券商事实。
        ...

    def account(self) -> AccountSnapshot:
        """返回券商独立账户状态；不得从内部账本反向生成。"""
        # 持仓和现金事实来自适配器自身。
        ...


class EventStore(Protocol):
    """唯一内部状态写入边界；具体事务与唯一约束由适配器提供。"""

    def save_intent(self, intent: OrderIntent) -> None:
        """提交前持久化意图；同键异内容抛 ContractError，有存储副作用。"""
        # 真正发送动作只能在此事务成功之后发生。
        ...

    def record_order(self, record: OrderRecord) -> None:
        """持久化订单投影；不直接改变成交、现金或持仓。"""
        # 状态更新与成交记账分离。
        ...

    def apply(self, event: OrderEvent | FillEvent) -> bool:
        """原子去重并处理事件；首次处理返回 True，同事实重复返回 False。"""
        # 事件身份冲突必须失败而非静默覆盖。
        ...

    def orders(self) -> list[OrderRecord]:
        """返回内部订单投影，用于恢复和差额计算。"""
        # 查询本身不得修改交易事实。
        ...

    def account(self, at: datetime) -> AccountSnapshot:
        """按注入时刻返回内部账户投影，不访问真实系统时钟。"""
        # 账户时间由调用者显式提供。
        ...

    def freeze(self, reason: str) -> None:
        """持久化不可自动解除的完整性故障；参数为原因代码，有存储副作用。"""
        # 冻结不能仅停留在进程内存中。
        ...

    def frozen(self) -> list[str]:
        """返回持久化冻结原因；空列表代表没有此类故障，不代表所有风控通过。"""
        # 查询不隐式解除冻结。
        ...

    def account_lock(self) -> ContextManager[None]:
        """取得本机账户唯一执行权上下文；竞争失败抛 RiskBlocked，退出时释放。"""
        # 账户执行锁独立于每个运行目录。
        ...

    def apply_action(self, action: CorporateAction, at: datetime) -> bool:
        """幂等处理已核验的公司行动；首次返回 True，重复返回 False，有存储副作用。"""
        # 公司行动与成交共用唯一账务写入边界。
        ...

    def events(self) -> list[OrderEvent | FillEvent]:
        """返回留存的输入事件，供回放使用；查询不得改写事件。"""
        # 原始事件是投影恢复的依据。
        ...

    def journal(self) -> list[JournalEntry]:
        """导出按接收顺序的意图、投影、成交和公司行动操作，供新状态库回放。"""
        # 导出不改变原有事实。
        ...


class JournalEntry(Contract):
    """内部事务操作的有序证据；回放只写新状态库，不向券商重新提交。"""

    # 序号来自同一事务日志，保留公司行动与订单的真实交错顺序。
    sequence: int = Field(ge=1, strict=True)
    # 操作类别明确决定应用哪个持久化接口。
    operation: Literal["intent", "order", "event", "action"]
    # 载荷必须属于已定义公共对象，不能包含可执行指令。
    payload: OrderIntent | OrderRecord | OrderEvent | FillEvent | CorporateAction
    # 对应业务事件或显式公司行动应用时刻。
    at: datetime

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        """校验操作与载荷类型对应；返回自身，错配抛 ValueError，无副作用。"""
        # 用明确的类型集合阻止将候选载荷误作交易事实。
        expected = {
            "intent": (OrderIntent,),
            "order": (OrderRecord,),
            "event": (OrderEvent, FillEvent),
            "action": (CorporateAction,),
        }
        # 不根据任意字段猜测操作。
        if not isinstance(self.payload, expected[self.operation]):
            # 回放在写入前拒绝不一致日志。
            raise ValueError("journal 操作和载荷类型不一致")
        # 合法记录保持原样。
        return self


def equivalent_values(left: Any, right: Any) -> bool:
    """比较JSON形状业务结果；浮点使用1e-12相对/绝对容差，金额字符串及整股精确比较。"""
    # 仅两个浮点值使用容差，不能把数量或布尔值近似相等。
    if isinstance(left, float) and isinstance(right, float):
        # NaN永远不能伪装成可重复结果。
        return (
            math.isfinite(left)
            and math.isfinite(right)
            and math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
        )
    # 字典必须具有同一字段集合，未知或缺失字段不是兼容结果。
    if isinstance(left, dict) and isinstance(right, dict):
        # 逐字段继续比较，不依赖插入顺序。
        return left.keys() == right.keys() and all(
            equivalent_values(value, right[key]) for key, value in left.items()
        )
    # 顺序列表保留业务固定排序，不擅自忽略次序。
    if isinstance(left, list) and isinstance(right, list):
        # 长度不同说明输入或输出内容发生变化。
        return len(left) == len(right) and all(
            equivalent_values(a, b) for a, b in zip(left, right, strict=True)
        )
    # 字符串金额、整数数量和布尔状态必须类型和值都一致。
    return type(left) is type(right) and bool(left == right)
