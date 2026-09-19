/* 本窗口仅读取本地证据接口。所有外部文字通过 textContent 写入，不解释为 HTML。 */
"use strict";

const byId = (id) => document.getElementById(id);
const list = (value) => Array.isArray(value) ? value : [];
const known = (value) => value !== null && value !== undefined && value !== "";
const number = (value) => known(value) && Number.isFinite(Number(value)) ? Number(value) : null;
const digits = (value, places = 2) => number(value) === null ? "—" : new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: places, maximumFractionDigits: places,
}).format(Number(value));
const percent = (value) => number(value) === null ? "—" : `${digits(Number(value) * 100, 2)}%`;
const text = (value) => known(value) ? String(value) : "—";
let lastView = null;

function node(tag, content, className) {
  const element = document.createElement(tag);
  if (content !== undefined) element.textContent = text(content);
  if (className) element.className = className;
  return element;
}

function empty(title, detail) {
  const container = node("div", undefined, "empty-state");
  container.append(node("div", "◇", "empty-icon"), node("strong", title), node("p", detail));
  return container;
}

function table(headers, rows) {
  const element = node("table");
  const head = node("thead");
  const heading = node("tr");
  headers.forEach((label) => heading.append(node("th", label)));
  head.append(heading);
  const body = node("tbody");
  rows.forEach((cells) => {
    const row = node("tr");
    cells.forEach((value) => {
      const cell = node("td");
      if (value instanceof Node) cell.append(value);
      else cell.textContent = text(value);
      row.append(cell);
    });
    body.append(row);
  });
  element.append(head, body);
  return element;
}

function symbolCell(identity, identities, showId = false) {
  const container = node("div", undefined, "symbol-cell");
  container.append(node("span", identities[identity] || identity, "symbol"));
  if (showId && identities[identity]) {
    const id = node("small", identity);
    id.title = text(identity);
    container.append(id);
  }
  return container;
}

function metric(label, value, note, unit = "USD") {
  const card = node("div", undefined, "metric");
  const heading = node("div", undefined, "metric-label");
  heading.append(node("span", label), node("span", unit, "metric-currency"));
  card.append(heading, node("div", value, "metric-value"), node("div", note, "metric-note"));
  return card;
}

function drawSparkline(closes) {
  // 图形仅由已有原始收盘价生成，不补齐缺失值、不展示预测或伪造走势。
  const points = list(closes).filter((point) => number(point.price) !== null && known(point.date));
  if (points.length < 2) return node("span", "暂无价格序列", "chart-empty");
  const prices = points.map((point) => Number(point.price));
  const lower = Math.min(...prices);
  const upper = Math.max(...prices);
  const spread = upper - lower;
  const coordinates = prices.map((price, index) => {
    const x = 2 + index * 112 / (prices.length - 1);
    const y = spread === 0 ? 14.5 : 26 - (price - lower) * 23 / spread;
    return `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.classList.add("sparkline");
  svg.setAttribute("viewBox", "0 0 116 29");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `原始收盘价，${points[0].date}至${points.at(-1).date}，${points.length}个交易日`);
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", coordinates.join(" "));
  svg.append(path);
  const container = node("div");
  container.append(svg, node("small", `${points.length} 个交易日 · 非收益曲线`, "trend-label"));
  return container;
}

function renderHeader(view) {
  const mode = byId("mode-badge");
  const paper = view.mode === "paper";
  const offline = view.mode === "offline";
  mode.textContent = paper ? "真实 Alpaca Paper" : offline ? "离线演示 · FakeBroker" : "来源模式未知";
  mode.classList.toggle("offline", !paper);
  byId("subtitle").textContent = text(view.title || "从数据到订单，查看每一步实际留下的证据。");
  const stages = {
    read_only: ["已到只读核验阶段，策略闭环尚未完成", "已读取的行情、账户和证券资料，与尚缺的策略输入分别展示。"],
    planned: ["策略计划已生成，等待执行证据", "因子、评分与目标已保存；目标持仓不是成交结果。"],
    observed: ["已记录订单观察，请查看成交与核对结果", "订单状态、实际累计成交与核对结论分别展示，不把提交当作完成。"],
  };
  const description = stages[view.stage] || ["运行阶段尚未确认", "已有证据不足以确定当前策略进度。"];
  byId("run-title").textContent = offline ? "离线工程演示，不代表真实 Paper 验收" : paper ? description[0] : "运行来源与阶段尚待确认";
  byId("run-detail").textContent = offline ? "此处只展示离线测试产物；不能用演示成交判断真实账户或策略表现。" : description[1];
  const timestamp = new Date(view.as_of || "invalid");
  byId("as-of").textContent = Number.isNaN(timestamp.getTime()) ? "时间未提供" : new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Hong_Kong", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(timestamp) + " HKT";
  byId("as-of").title = text(view.as_of);
  byId("account-id").textContent = known(view.account?.id_suffix) ? `账户尾号 · ${view.account.id_suffix}` : "账户身份未提供";
}

function renderMetrics(view) {
  const account = view.account || {};
  const cards = [
    metric("本次策略预算", digits(account.allocated_budget), "独立分配的模拟资金"),
    metric(view.mode === "offline" ? "离线券商现金" : view.mode === "paper" ? "远端账户现金" : "账户现金（来源未确认）", digits(account.cash), known(account.reserve) ? `未分配现金 ${digits(account.reserve)} USD` : "未分配现金尚未在计划中锁定"),
    metric("非保证金购买力", digits(account.non_marginable_buying_power), "仅作额外资金边界，不扩大策略预算"),
    metric("账户总购买力", digits(account.buying_power), `实际持仓证券数 ${text(account.positions_count)} · 含账户授信口径`),
  ];
  byId("metrics").replaceChildren(...cards);
}

function renderPipeline(view) {
  const items = list(view.pipeline).map((step, index) => {
    const state = ["passed", "blocked", "pending"].includes(step.state) ? step.state : "pending";
    const names = { passed: "已通过", blocked: "待补齐", pending: "未验证" };
    const item = node("li");
    const top = node("div", undefined, "step-top");
    top.append(node("span", state === "passed" ? "✓" : state === "blocked" ? "!" : String(index + 1).padStart(2, "0"), `step-icon ${state}`));
    const title = node("div", step.name, "step-name");
    title.append(node("span", names[state], `step-status ${state}`));
    item.append(top, title, node("div", step.detail || "暂无阶段证据", "step-detail"));
    return item;
  });
  byId("pipeline").replaceChildren(...(items.length ? items : [node("li", "尚无阶段记录", "placeholder")]));
}

function renderSources(view) {
  const sources = list(view.sources);
  byId("source-count").textContent = sources.length;
  byId("sources").replaceChildren(sources.length ? table(
    ["证券", "日线条数", "数据起止日期", "最近原始收盘", "近期原始价格", "当前交易资格", "碎股属性"],
    sources.map((source) => [
      node("span", source.symbol, "symbol"), text(source.rows), `${known(source.first) ? String(source.first).slice(0, 10) : "—"} → ${known(source.last) ? String(source.last).slice(0, 10) : "—"}`,
      digits(source.raw_close), drawSparkline(source.closes),
      node("span", source.tradable === true ? "可交易" : source.tradable === false ? "不可交易" : "未确认", source.tradable === true ? "eligibility" : "eligibility unknown"),
      source.fractionable === true ? "支持 · 本阶段仍为整股" : source.fractionable === false ? "不支持" : "未提供",
    ]),
  ) : empty("尚无合格行情记录", "数据未取得时保持为空；不会用离线样本替代真实 Paper 数据。"));
}

function renderStrategy(view) {
  const ma = view.strategy === "ma-trend";
  byId("strategy-name").textContent = ma ? "MA5 / MA20 TREND" : "WEEKLY TWO-FACTOR";
  byId("strategy-badge").textContent = ma ? "MA 趋势 · 单因子" : "原策略 · 等权";
  byId("strategy-description").textContent = ma ? "仅拆股调整价格：MA5 > MA20 入选，强度 = MA5/MA20 − 1；只有强度参与评分，未知行业按最坏集中度约束。" : "动量与低波动各占50%；评分是相对排名，不是收益预测。";
  const identities = view.identity || {};
  const signals = list(view.signals);
  const factors = list(view.factors);
  const identifiers = [...new Set([...signals.map((row) => row.security_id), ...factors.map((row) => row.security_id)])];
  const factorValue = (id, factor) => factors.find((row) => row.security_id === id && row.factor_id === factor)?.value;
  const rows = identifiers.map((id) => {
    const signal = signals.find((row) => row.security_id === id);
    const score = node("span", digits(signal?.value, 3), "score-value");
    if (number(signal?.value) !== null && Number(signal.value) >= 0 && Number(signal.value) <= 1) {
      const track = node("span", undefined, "score-track");
      const fill = node("span", undefined, "score-fill");
      fill.style.width = `${Number(signal.value) * 100}%`;
      track.append(fill);
      score.prepend(track);
    }
    if (signal?.components) score.title = Object.entries(signal.components).map(([name, value]) => `${name}: ${text(value)}`).join("；");
    return ma ? [symbolCell(id, identities), digits(factorValue(id, "ma5"), 4), digits(factorValue(id, "ma20"), 4), percent(factorValue(id, "ma_trend")), score] : [symbolCell(id, identities), percent(factorValue(id, "momentum")), digits(factorValue(id, "low_volatility"), 4), score];
  });
  byId("scores").replaceChildren(rows.length ? table(ma ? ["证券", "MA5 USD", "MA20 USD", "趋势强度", "评分"] : ["证券", "动量", "低波动因子", "综合评分"], rows) : empty("因子与评分尚未生成", ma ? "MA需要当前普通股身份、连续20日拆股价格和公司行动核验。" : "原策略需要合格的行业、证券类别和股息再投资总回报资料。缺失时不填零、不虚构排名。"));
  byId("exclusions").replaceChildren(...Object.entries(view.excluded || {}).map(([id, reason]) => node("p", `${identities[id] || id} · ${text(reason)}`)));
  const targets = list(view.targets);
  byId("targets").replaceChildren(targets.length ? table(["证券 / 行业", "目标权重", "目标股数", "依据"], targets.map((target) => {
    const symbol = symbolCell(target.security_id, identities);
    symbol.append(node("small", target.sector || "行业未知 · 最坏集中度计量"));
    return [symbol, percent(target.weight), text(target.quantity), node("span", target.reason, "reason-cell")];
  })) : empty("尚无目标持仓", "没有保存目标可能是尚未规划或约束下无合格目标；请结合阶段记录判断。"));
}

function renderExecution(view) {
  const identities = view.identity || {};
  const orders = list(view.orders);
  const labels = { PERSISTED: "意图已保存", OPEN: "待成交", PARTIAL: "部分成交", FILLED: "全部成交", CANCELED: "已撤销", CANCEL_PENDING: "撤单处理中", REJECTED: "已拒绝", UNKNOWN: "状态未知" };
  byId("order-count").textContent = orders.length;
  byId("orders").replaceChildren(orders.length ? table(["证券", "方向", "委托 / 已成交", "限价 USD", "状态", "客户订单身份", "Alpaca 订单 ID"], orders.map((order) => {
    const intent = order.intent || {};
    const id = node("span", known(intent.client_order_id) ? `${String(intent.client_order_id).slice(0, 14)}…` : "—", "id-cell");
    id.title = text(intent.client_order_id);
    const state = Object.hasOwn(labels, order.status) ? order.status : "UNKNOWN";
    const status = node("span", `${labels[state]} · ${text(order.status)}`, `order-status ${state}`);
    return [symbolCell(intent.security_id, identities), intent.side === "BUY" ? "买入" : intent.side === "SELL" ? "卖出" : "未确认",
      `${text(intent.quantity)} / ${text(order.filled_quantity)} 股`, digits(intent.limit_price, Number(intent.limit_price) < 1 ? 4 : 2), status, id, text(order.broker_order_id)];
  })) : empty("尚无订单记录", "这里展示保存的订单事实。未下单不会显示成交，未完成也不会被标记为完成。"));
  const fills = list(view.events).filter((event) => event.kind === "fill");
  byId("fills").replaceChildren(...(fills.length ? [table(["成交 ID", "证券", "新增股数", "成交价 USD", "时间"], fills.map((fill) => [text(fill.event_id), symbolCell(fill.security_id, identities), text(fill.quantity), digits(fill.price, 4), text(fill.at)]))] : []));
  const risks = list(view.risks).map((risk) => {
    const reasons = list(risk.reasons).join("；");
    return node("p", `${risk.allowed === true ? "通过" : risk.allowed === false ? "拒绝" : "未确认"} · ${text(risk.client_order_id)}${reasons ? ` · ${reasons}` : ""}${risk.status ? ` · ${risk.status}` : ""}`);
  });
  byId("risks").replaceChildren(...risks);
  const result = view.reconciliation || {};
  const passed = result.matched === true;
  const blocked = result.matched === false;
  const container = node("div");
  container.append(node("div", passed ? "本次记录核对一致" : blocked ? "存在待解释差异" : "尚未取得核对结论", `reconciliation-status ${passed ? "passed" : blocked ? "blocked" : "pending"}`));
  container.append(node("p", "现金、持仓与订单需依据独立远端事实核对；展示窗口不修改交易账本。", "reconciliation-description"));
  if (list(result.differences).length) {
    const differences = node("ul");
    list(result.differences).forEach((difference) => differences.append(node("li", difference)));
    container.append(differences);
  }
  byId("reconciliation").replaceChildren(container);
  const limitations = list(view.limitations);
  byId("limitations").replaceChildren(...(limitations.length ? limitations : ["运行记录未提供额外限制；这不代表所有能力均已验证。"]).map((entry) => node("li", entry)));
}

function render(view) {
  renderHeader(view);
  renderMetrics(view);
  renderPipeline(view);
  renderSources(view);
  renderStrategy(view);
  renderExecution(view);
}

async function refresh() {
  const button = byId("refresh");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/api/view", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error("Local evidence unavailable");
    const view = await response.json();
    if (!view || typeof view !== "object" || Array.isArray(view)) throw new Error("Invalid evidence");
    render(view);
    lastView = view;
    byId("connection-error").hidden = true;
    document.body.classList.remove("stale");
  } catch {
    byId("connection-error").textContent = lastView ? "刷新失败，仍显示上一次成功读取的本地证据。请以采集时间为准。" : "暂时无法读取本地证据。请确认展示服务运行正常，然后重新刷新。";
    byId("connection-error").hidden = false;
    document.body.classList.add("stale");
    if (!lastView) {
      byId("run-title").textContent = "本地证据暂不可用";
      byId("run-detail").textContent = "没有读取到数据，不推断账户、策略或成交状态。";
    }
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

byId("refresh").addEventListener("click", refresh);
refresh();
