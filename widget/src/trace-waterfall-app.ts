/**
 * Trace + cost waterfall - an interactive MCP App widget.
 *
 * Renders the span waterfall produced by the `view_trace_waterfall` tool: one
 * row per OpenTelemetry span, a duration bar positioned on a shared time axis,
 * and a cost chip whose value follows the selected dimension (latency / tokens /
 * dollars). Every drill-down is proxied back through the host as a governed tool
 * call:
 *   - click a span row        -> describe_span            (details panel)
 *   - click a span cost chip   -> get_span_cost_breakdown  (cost decomposition)
 *   - + on a lazy span         -> expand_span_children     (reveal children)
 *   - open the trace picker    -> list_recent_traces       (dropdown)
 *   - pick a trace             -> view_trace_waterfall      (recenter)
 * The dimension toggle, branch collapse, and reset are local re-renders, no call.
 */
import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
  applyHostFonts,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";

type SpanCost = { token_cost_usd: number; bytes_billed: number; total_cost_usd: number };
type Span = {
  span_id: string; parent_span_id: string | null; name: string; kind: string;
  start_ms: number; duration_ms: number; status: string; has_lazy_children: boolean;
  tokens: number; cost: SpanCost;
};
type Totals = {
  token_cost_usd: number; tokens_in: number; tokens_out: number; cached_tokens_in: number;
  bytes_scanned: number; bytes_billed: number; bigquery_cost_usd: number; total_cost_usd: number;
};
type Trace = {
  trace_id: string; question: string; started_at: string; wall_ms: number;
  status: string; totals: Totals; spans: Span[];
};
type Dimension = "latency" | "tokens" | "dollars";

const KINDS = [
  "root", "embedding", "resolver", "neo4j", "guardrail", "bigquery", "claude_api", "cache",
];

// ---- element refs ---------------------------------------------------------
const $ = (id: string) => document.getElementById(id)!;
const rowsEl = $("rows");
const axisEl = $("axis-track");
const detailsEl = $("details");
const statusEl = $("status");
const menuEl = $("trace-menu");

// ---- state ----------------------------------------------------------------
let meta: Omit<Trace, "spans"> | null = null;     // header + totals of the current trace
let spans = new Map<string, Span>();              // every span currently known
let baseSpanIds = new Set<string>();              // the spans from the last view_* call
const collapsed = new Set<string>();              // locally collapsed branch roots
let dimension: Dimension = "latency";
let selected: string | null = null;
let busy = false;

// ---- helpers --------------------------------------------------------------
function escapeHtml(s: string): string {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));
}
function setStatus(text: string, isErr = false) {
  statusEl.textContent = text;
  statusEl.className = isErr ? "err" : "";
}
function kindColor(kind: string): string {
  return KINDS.includes(kind) ? `var(--k-${kind})` : "var(--k-root)";
}
function fmtUsd(v: number): string {
  if (v === 0) return "$0";
  if (v < 0.01) return "$" + v.toFixed(4);
  return "$" + v.toFixed(2);
}
function fmtTokens(n: number): string {
  return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n);
}
function fmtBytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 && i > 0 ? 2 : 0) + " " + u[i];
}

// ---- tree ordering --------------------------------------------------------
function childrenOf(id: string): Span[] {
  const out: Span[] = [];
  for (const s of spans.values()) if (s.parent_span_id === id) out.push(s);
  out.sort((a, b) => a.start_ms - b.start_ms);
  return out;
}
function hasLoadedChildren(id: string): boolean {
  for (const s of spans.values()) if (s.parent_span_id === id) return true;
  return false;
}
// Pre-order traversal, skipping under collapsed nodes.
function visibleOrder(): { span: Span; depth: number }[] {
  const out: { span: Span; depth: number }[] = [];
  const roots = [...spans.values()]
    .filter((s) => !s.parent_span_id || !spans.has(s.parent_span_id))
    .sort((a, b) => a.start_ms - b.start_ms);
  const walk = (s: Span, depth: number) => {
    out.push({ span: s, depth });
    if (collapsed.has(s.span_id)) return;
    for (const c of childrenOf(s.span_id)) walk(c, depth + 1);
  };
  roots.forEach((r) => walk(r, 0));
  return out;
}

// ---- dimension value for a span ------------------------------------------
function dimValue(s: Span): number {
  if (dimension === "tokens") return s.tokens;
  if (dimension === "dollars") return s.cost.total_cost_usd;
  return s.duration_ms;
}
function dimLabel(s: Span): string {
  if (dimension === "tokens") return s.tokens ? fmtTokens(s.tokens) + " tok" : "0 tok";
  if (dimension === "dollars") return fmtUsd(s.cost.total_cost_usd);
  return s.duration_ms + " ms";
}

// ---- render ---------------------------------------------------------------
function renderHeader() {
  if (!meta) return;
  $("page-title").textContent = "Trace waterfall";
  $("question").innerHTML = `<b>${escapeHtml(meta.question)}</b>`;
  const pill = $("trace-status");
  const st = meta.status;
  pill.textContent = st === "guardrail_blocked" ? "Guardrail blocked" : st.toUpperCase();
  pill.className = "status-pill" + (st === "ok" ? "" : st === "guardrail_blocked" ? " blocked" : " error");
  const t = meta.totals;
  $("totals").innerHTML = [
    `<span>wall <b>${meta.wall_ms} ms</b></span>`,
    `<span>tokens <b>${fmtTokens(t.tokens_in + t.tokens_out)}</b></span>`,
    `<span>scanned <b>${fmtBytes(t.bytes_scanned)}</b></span>`,
    `<span>cost <b>${fmtUsd(t.total_cost_usd)}</b></span>`,
  ].join("");
}

function renderAxis() {
  if (!meta) return;
  axisEl.innerHTML = "";
  const wall = Math.max(1, meta.wall_ms);
  const target = 5;
  const raw = wall / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => raw <= s) || 10 * mag;
  for (let t = 0; t <= wall + 0.5; t += step) {
    const tick = document.createElement("div");
    tick.className = "tick";
    tick.style.left = (t / wall) * 100 + "%";
    tick.innerHTML = `<span>${Math.round(t)} ms</span>`;
    axisEl.appendChild(tick);
  }
}

function renderRows() {
  if (!meta) return;
  const order = visibleOrder();
  const wall = Math.max(1, meta.wall_ms);
  const maxDim = Math.max(...order.map(({ span }) => dimValue(span)), 1e-9);
  rowsEl.innerHTML = "";

  for (const { span, depth } of order) {
    const row = document.createElement("div");
    row.className = "row" + (span.span_id === selected ? " sel" : "");
    row.dataset.span = span.span_id;

    // name column (twirl + kind dot + name + blocked badge)
    const name = document.createElement("div");
    name.className = "name";
    name.style.paddingLeft = 6 + depth * 14 + "px";

    const loaded = hasLoadedChildren(span.span_id);
    const lazyUnloaded = span.has_lazy_children && !loaded;
    const twirl = document.createElement("button");
    twirl.className = "twirl" + (loaded || lazyUnloaded ? "" : " placeholder");
    twirl.textContent = lazyUnloaded ? "+" : collapsed.has(span.span_id) ? "+" : "−";
    twirl.title = lazyUnloaded ? "Load children" : collapsed.has(span.span_id) ? "Expand" : "Collapse";
    twirl.addEventListener("click", (e) => { e.stopPropagation(); onTwirl(span); });
    name.appendChild(twirl);

    const dot = document.createElement("span");
    dot.className = "kind-dot";
    dot.style.background = kindColor(span.kind);
    name.appendChild(dot);

    const label = document.createElement("span");
    label.className = "name-text";
    label.textContent = span.name;
    label.title = `${span.name} (${span.kind})`;
    name.appendChild(label);

    const blocking = meta.status === "guardrail_blocked" && span.status === "error" && span.kind === "guardrail";
    if (blocking) {
      const b = document.createElement("span");
      b.className = "blocked-badge";
      b.textContent = "Blocked";
      name.appendChild(b);
    }
    row.appendChild(name);

    // track (the time bar)
    const track = document.createElement("div");
    track.className = "track";
    const bar = document.createElement("div");
    bar.className = "bar" + (span.status === "error" ? " err" : "");
    bar.style.left = (span.start_ms / wall) * 100 + "%";
    bar.style.width = Math.max(0.4, (span.duration_ms / wall) * 100) + "%";
    bar.style.background = kindColor(span.kind);
    track.appendChild(bar);
    row.appendChild(track);

    // chip cell (encodes the active dimension; fill scales by share)
    const cell = document.createElement("div");
    cell.className = "chip-cell";
    const chip = document.createElement("button");
    chip.className = "cost-chip";
    chip.textContent = dimLabel(span);
    const share = Math.min(100, (dimValue(span) / maxDim) * 100);
    chip.style.background = `linear-gradient(90deg, var(--accent-weak) ${share}%, var(--surface-alt) ${share}%)`;
    chip.title = "Cost breakdown for this span";
    chip.addEventListener("click", (e) => { e.stopPropagation(); onCostChip(span); });
    cell.appendChild(chip);
    row.appendChild(cell);

    row.addEventListener("click", () => onRowClick(span));
    rowsEl.appendChild(row);
  }
}

function renderLegend() {
  const el = $("legend");
  el.innerHTML = KINDS
    .map((k) => `<span><i style="background:var(--k-${k})"></i>${k}</span>`)
    .join("");
}

function render(model: Trace) {
  meta = { trace_id: model.trace_id, question: model.question, started_at: model.started_at,
    wall_ms: model.wall_ms, status: model.status, totals: model.totals };
  spans = new Map(model.spans.map((s) => [s.span_id, s]));
  baseSpanIds = new Set(spans.keys());
  collapsed.clear();
  selected = null;
  closeDetails();
  renderHeader();
  renderAxis();
  renderRows();
  renderLegend();
  setStatus(`${model.spans.length} spans · ${model.wall_ms} ms wall · ${fmtUsd(model.totals.total_cost_usd)}`);
}

// ---- interactions ---------------------------------------------------------
async function onTwirl(span: Span) {
  const loaded = hasLoadedChildren(span.span_id);
  if (span.has_lazy_children && !loaded) {
    await expandChildren(span);
    return;
  }
  if (collapsed.has(span.span_id)) collapsed.delete(span.span_id);
  else collapsed.add(span.span_id);
  renderRows();
}

async function expandChildren(span: Span) {
  if (busy) return;
  busy = true;
  setStatus(`Expanding ${span.name} via expand_span_children…`);
  try {
    const res: any = await app.callServerTool({
      name: "expand_span_children",
      arguments: { span_id: span.span_id, visible_span_ids: [...spans.keys()] },
    });
    const fresh = (res?.structuredContent?.spans as Span[]) || [];
    for (const s of fresh) spans.set(s.span_id, s);
    collapsed.delete(span.span_id);
    renderRows();
    setStatus(`Revealed ${fresh.length} spans under ${span.name}`);
  } catch (err: any) {
    setStatus("Expand failed: " + (err?.message || err), true);
  } finally { busy = false; }
}

async function onRowClick(span: Span) {
  selected = span.span_id;
  renderRows();
  if (busy) return;
  busy = true;
  setStatus(`Describing ${span.name} via describe_span…`);
  try {
    const res: any = await app.callServerTool({
      name: "describe_span", arguments: { span_id: span.span_id },
    });
    renderDetails(res?.structuredContent, span);
    setStatus(`describe_span · ${span.name}`);
  } catch (err: any) {
    setStatus("describe_span failed: " + (err?.message || err), true);
  } finally { busy = false; }
}

async function onCostChip(span: Span) {
  selected = span.span_id;
  renderRows();
  if (busy) return;
  busy = true;
  // A span with children is decomposed over its subtree, a leaf over itself.
  const scope = hasLoadedChildren(span.span_id) || span.has_lazy_children ? "subtree" : "span";
  setStatus(`Cost breakdown for ${span.name} via get_span_cost_breakdown…`);
  try {
    const res: any = await app.callServerTool({
      name: "get_span_cost_breakdown", arguments: { span_id: span.span_id, scope },
    });
    renderCostBreakdown(res?.structuredContent, span);
    setStatus(`get_span_cost_breakdown · ${span.name} (${scope})`);
  } catch (err: any) {
    setStatus("get_span_cost_breakdown failed: " + (err?.message || err), true);
  } finally { busy = false; }
}

// ---- details panel --------------------------------------------------------
function openDetails(title: string, kind: string) {
  $("d-title").textContent = title;
  const k = $("d-kind");
  k.textContent = kind;
  k.style.background = kindColor(kind);
  detailsEl.classList.add("open");
}
function closeDetails() {
  detailsEl.classList.remove("open");
}

function renderDetails(d: any, span: Span) {
  if (!d) return;
  openDetails(d.name || span.name, d.kind || span.kind);
  const parts: string[] = [];

  parts.push(`<div class="d-section"><h4>Span</h4><dl class="kv-row">
    <dt>status</dt><dd>${escapeHtml(d.status)}</dd>
    <dt>start</dt><dd>${d.start_ms} ms</dd>
    <dt>duration</dt><dd>${d.duration_ms} ms</dd>
  </dl></div>`);

  const attrs = d.attributes || {};
  const attrKeys = Object.keys(attrs);
  if (attrKeys.length) {
    const rows = attrKeys
      .map((key) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(String(attrs[key]))}</dd>`)
      .join("");
    parts.push(`<div class="d-section"><h4>Attributes</h4><dl class="kv-row">${rows}</dl></div>`);
  }

  const events = (d.events || []) as any[];
  if (events.length) {
    const evRows = events.map((e) => {
      const verdict = e["guardrail.verdict"];
      const cls = verdict === "fail" ? "v-fail" : "v-pass";
      return `<div class="ev"><span>${escapeHtml(e["guardrail.rule_id"] || e.name)}</span>
        <span class="${cls}">${escapeHtml(verdict || "")}</span>
        <span class="ev-cite" title="${escapeHtml(e["guardrail.citation"] || "")}">${escapeHtml(e["guardrail.citation"] || "")}</span></div>`;
    }).join("");
    parts.push(`<div class="d-section"><h4>Events</h4>${evRows}</div>`);
  }

  const c = d.cost || span.cost;
  parts.push(`<div class="d-section"><h4>Cost (this span)</h4><div class="cost-grid">
    <span class="lbl">token cost</span><span class="val">${fmtUsd(c.token_cost_usd)}</span>
    <span class="lbl">bytes billed</span><span class="val">${fmtBytes(c.bytes_billed)}</span>
    <div class="total"><span>total</span><span>${fmtUsd(c.total_cost_usd)}</span></div>
  </div></div>`);

  const links = (d.links || []) as any[];
  if (links.length) {
    parts.push(`<div class="d-section"><h4>Links</h4><div id="d-links"></div></div>`);
  }

  $("d-body").innerHTML = parts.join("");

  // wire link buttons (governed cross-widget tool calls)
  if (links.length) {
    const host = $("d-links");
    links.forEach((link, i) => {
      const btn = document.createElement("button");
      btn.className = "link-btn";
      btn.textContent = link.label || `Open ${link.tool}`;
      btn.dataset.i = String(i);
      btn.addEventListener("click", () => onLink(link));
      host.appendChild(btn);
    });
  }
}

function renderCostBreakdown(cb: any, span: Span) {
  if (!cb) return;
  openDetails(span.name + " - cost", span.kind);
  const ca = cb.claude_api, bq = cb.bigquery;
  $("d-body").innerHTML = `
    <div class="d-section"><h4>Scope: ${escapeHtml(cb.scope)}</h4></div>
    <div class="d-section"><div class="cost-grid">
      <div class="grp">Claude (tokens)</div>
      <span class="lbl">input (uncached)</span><span class="val">${fmtTokens(ca.uncached_tokens_in)} @ $${ca.rate_card.in_per_mtok_usd}/Mtok</span>
      <span class="lbl">input (cached)</span><span class="val">${fmtTokens(ca.cached_tokens_in)} @ $${ca.rate_card.cached_in_per_mtok_usd}/Mtok</span>
      <span class="lbl">output</span><span class="val">${fmtTokens(ca.tokens_out)} @ $${ca.rate_card.out_per_mtok_usd}/Mtok</span>
      <span class="lbl">Claude cost</span><span class="val">${fmtUsd(ca.cost_usd)}</span>
      <div class="grp">BigQuery (bytes)</div>
      <span class="lbl">bytes scanned</span><span class="val">${fmtBytes(bq.bytes_scanned)}</span>
      <span class="lbl">bytes billed</span><span class="val">${fmtBytes(bq.bytes_billed)}</span>
      <span class="lbl">TiB billed</span><span class="val">${bq.tib_billed} @ $${bq.rate_per_tib_usd}/TiB</span>
      <span class="lbl">BigQuery cost</span><span class="val">${fmtUsd(bq.cost_usd)}</span>
      <div class="total"><span>total</span><span>${fmtUsd(cb.total_cost_usd)}</span></div>
    </div></div>`;
}

async function onLink(link: any) {
  if (busy) return;
  busy = true;
  setStatus(`Requesting ${link.tool} via the host…`);
  try {
    await app.callServerTool({ name: link.tool, arguments: link.args || {} });
    setStatus(`Requested ${link.tool} (${JSON.stringify(link.args || {})}) through the host`);
  } catch (err: any) {
    setStatus(`${link.tool} call failed: ` + (err?.message || err), true);
  } finally { busy = false; }
}

// ---- trace picker ---------------------------------------------------------
async function openPicker() {
  if (menuEl.classList.contains("open")) { menuEl.classList.remove("open"); return; }
  menuEl.classList.add("open");
  menuEl.innerHTML = `<div class="menu-item"><div class="mi-q">Loading…</div></div>`;
  try {
    const res: any = await app.callServerTool({ name: "list_recent_traces", arguments: {} });
    const traces = (res?.structuredContent?.traces as any[]) || [];
    menuEl.innerHTML = "";
    if (!traces.length) { menuEl.innerHTML = `<div class="menu-item"><div class="mi-q">No traces</div></div>`; return; }
    for (const t of traces) {
      const item = document.createElement("button");
      item.className = "menu-item";
      const dotKind = t.status === "ok" ? "bigquery" : "guardrail";
      item.innerHTML = `<div class="mi-q">${escapeHtml(t.question)}</div>
        <div class="mi-meta">
          <span><span class="mi-dot" style="background:var(--k-${dotKind})"></span>${escapeHtml(t.status)}</span>
          <span>${t.wall_ms} ms</span><span>${fmtUsd(t.total_cost_usd)}</span>
          <span>${escapeHtml(t.trace_id)}</span>
        </div>`;
      item.addEventListener("click", () => { menuEl.classList.remove("open"); pickTrace(t.trace_id); });
      menuEl.appendChild(item);
    }
  } catch (err: any) {
    menuEl.innerHTML = `<div class="menu-item"><div class="mi-q">list_recent_traces failed</div></div>`;
    setStatus("list_recent_traces failed: " + (err?.message || err), true);
  }
}

async function pickTrace(traceId: string) {
  if (busy) return;
  busy = true;
  setStatus(`Recentering on ${traceId} via view_trace_waterfall…`);
  try {
    const res: any = await app.callServerTool({
      name: "view_trace_waterfall", arguments: { trace_id: traceId },
    });
    const sc = res?.structuredContent as Trace | undefined;
    if (sc && sc.spans) render(sc);
    else setStatus("view_trace_waterfall returned no spans", true);
  } catch (err: any) {
    setStatus("view_trace_waterfall failed: " + (err?.message || err), true);
  } finally { busy = false; }
}

// ---- dimension toggle + chrome -------------------------------------------
$("dim-seg").querySelectorAll<HTMLButtonElement>("button").forEach((btn) => {
  btn.addEventListener("click", () => {
    dimension = btn.dataset.dim as Dimension;
    $("dim-seg").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === btn));
    renderRows();  // local re-render, no tool call
  });
});
$("picker-btn").addEventListener("click", (e) => { e.stopPropagation(); openPicker(); });
document.addEventListener("click", (e) => {
  if (!(e.target as HTMLElement).closest(".picker")) menuEl.classList.remove("open");
});
$("d-close").addEventListener("click", () => { closeDetails(); selected = null; renderRows(); });
$("reset").addEventListener("click", () => {
  if (!meta) return;
  // drop expanded extras + collapses, restore the spans from the last view call
  for (const id of [...spans.keys()]) if (!baseSpanIds.has(id)) spans.delete(id);
  collapsed.clear();
  selected = null;
  closeDetails();
  renderRows();
  setStatus("View reset");
});

// ---- theme ----------------------------------------------------------------
function setTheme(theme: "light" | "dark") {
  document.documentElement.dataset.theme = theme;
  $("theme-toggle").textContent = theme === "dark" ? "Light" : "Dark";
}
$("theme-toggle").addEventListener("click", () =>
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

// ---- host context + MCP wiring -------------------------------------------
function applyHostContext(ctx: McpUiHostContext) {
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx?.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
  $("theme-toggle").textContent = document.documentElement.dataset.theme === "dark" ? "Light" : "Dark";
}

const app = new App({ name: "Trace Waterfall", version: "0.1.0" });
app.ontoolresult = (result: any) => {
  const sc = result?.structuredContent as Trace | undefined;
  if (sc && sc.spans) render(sc);
};
app.onhostcontextchanged = applyHostContext;
app.onerror = (e: any) => console.error(e);

// Standalone mode: a future exporter could inject the trace as window.__MCP_MODEL__.
const injected = (window as any).__MCP_MODEL__ as Trace | undefined;
if (injected && injected.spans) {
  document.body.dataset.standalone = "1";
  render(injected);
  setStatus("Standalone - drill-downs need the live server");
} else {
  app.connect().then(() => {
    const ctx = app.getHostContext();
    if (ctx) applyHostContext(ctx);
    if (!meta) setStatus("Connected - awaiting trace…");
  });
}
