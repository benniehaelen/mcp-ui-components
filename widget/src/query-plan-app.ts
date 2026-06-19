/**
 * Query plan — an interactive MCP App widget.
 *
 * Renders the *query-plan model* produced by the `view_query_plan` tool
 * (see src/lineage_mcp/sql_query.py): a logical execution pipeline where every
 * SQL clause is an operator box, laid out left→right in the order SQL runs, with
 * each CTE / subquery / set-op arm as its own lane that feeds downstream
 * operators via cross-lane dataflow edges.
 *
 * Interactions (all local unless noted):
 *   - hover / click an operator → highlight its tables+columns in the SQL, dim the rest
 *   - click a lane in the legend → focus that lane
 *   - click a lane header       → collapse / expand the lane
 *   - drag an operator, zoom, pan, Fit, Reset view, detail toggle, export, copy SQL
 *   - Edit SQL → re-parse via `parse_query_plan`                  (governed tool call)
 *
 * The zoom/pan/drag/SQL/export/MCP machinery mirrors the join-diagram widget.
 */
import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
  applyHostFonts,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";

type Operator = {
  id: string; type: string; title: string;
  detail: string[]; tables: string[]; columns: string[];
  sourceRef: string | null; badge: string | null;
};
type Block = {
  id: string; kind: string; name: string; label: string;
  accent: string; header: string;
  operators: Operator[]; outputColumns: string[]; sqlTerms: string[];
};
type Edge = { from: string; toBlock: string; toOp: string; label: string; color: string };
type Model = {
  title: string; dialect: string; nl: string | null; sql: string;
  scan: string | null; period: { start: string; end: string } | null;
  dataset: string; blockCount: number; operatorCount: number;
  blocks: Block[]; edges: Edge[];
};

const SVGNS = "http://www.w3.org/2000/svg";

// semantic colour per operator type — the EXPLAIN "feel": you read the pipeline
// by colour as much as by label.
const TYPE_COLOR: Record<string, string> = {
  scan: "#0ea5e9", join: "#22c55e", filter: "#eab308", aggregate: "#8b5cf6",
  having: "#f97316", window: "#06b6d4", project: "#3b82f6", distinct: "#14b8a6",
  sort: "#ec4899", limit: "#64748b", setop: "#f43f5e",
};

// layout geometry (OP_W must match .op-box width in the CSS)
const OP_W = 212;
const OP_GAP = 64;          // horizontal gap between operators in a lane
const LANE_HEADER_W = 168;  // width reserved for the lane header column
const LANE_GAP = 46;        // vertical gap between lanes
const PAD = 40;
const MIN_ZOOM = 0.25;

// ---- element refs ---------------------------------------------------------
const $ = (id: string) => document.getElementById(id)!;
const stage = $("stage");
const wrapper = $("diagram-wrapper");
const svg = $("connector-svg") as unknown as SVGSVGElement;
const legendEl = $("legend");
const sqlEl = $("sql-text");
const statusEl = $("status");
const clearFocusBtn = $("clear-focus");

let model: Model | null = null;
let zoom = 1, panX = 0, panY = 0;
let focusOp: string | null = null;
let busy = false;
let userArranged = false;
const collapsed = new Set<string>();   // collapsed lane (block) ids

// drag-to-move an operator
let opDrag: { id: string; sx: number; sy: number; ox: number; oy: number; moved: boolean } | null = null;
let suppressClick = false;

// ---- helpers --------------------------------------------------------------
function elNS(tag: string, attrs: Record<string, any>): SVGElement {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, String(attrs[k]));
  return e;
}
function escapeHtml(s: string): string {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));
}
function escapeRe(s: string): string { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
function setStatus(text: string, isErr = false) {
  statusEl.textContent = text;
  statusEl.className = isErr ? "err" : "";
}
function block(id: string): Block | undefined { return model?.blocks.find((b) => b.id === id); }

// ---- render ---------------------------------------------------------------
function render(m: Model) {
  model = m;
  userArranged = false;
  collapsed.clear();
  focusOp = null;
  clearFocusBtn.style.display = "none";

  $("page-title").textContent = m.title || "Query plan";
  const parts = [
    m.dataset,
    `${m.blockCount} lane${m.blockCount === 1 ? "" : "s"}`,
    `${m.operatorCount} operator${m.operatorCount === 1 ? "" : "s"}`,
    m.scan ? `Scan: ${m.scan}` : "",
    m.period ? `Period: ${m.period.start} to ${m.period.end}` : "",
  ].filter(Boolean);
  $("page-subtitle").innerHTML = parts.join(" &nbsp;·&nbsp; ");

  // legend — one badge per lane
  legendEl.innerHTML = "";
  m.blocks.forEach((b) => {
    const badge = document.createElement("span");
    badge.className = "legend-badge";
    badge.dataset.block = b.id;
    badge.innerHTML = `<span class="legend-dot" style="background:${b.accent}"></span>${escapeHtml(b.label)}`;
    badge.addEventListener("mouseenter", () => { if (!focusOp) emphasizeLane(b); });
    badge.addEventListener("mouseleave", () => { if (!focusOp) clearHighlight(); });
    badge.addEventListener("click", () => focusLane(b));
    legendEl.appendChild(badge);
  });

  // rebuild stage: lane bands, lane headers, operator boxes
  wrapper.querySelectorAll(".op-box, .lane-band, .lane-header").forEach((n) => n.remove());
  for (const b of m.blocks) {
    wrapper.appendChild(buildLaneBand(b));
    wrapper.appendChild(buildLaneHeader(b));
    for (const op of b.operators) wrapper.appendChild(buildOp(op, b));
  }

  const nl = m.nl || "";
  $("nl-text").textContent = nl || "No natural-language prompt provided.";
  (document.getElementById("qs-nl") as HTMLDetailsElement).style.display = nl ? "" : "none";
  $("sql-tag").textContent = (m.dialect || "sql").toUpperCase();
  renderSql();

  requestAnimationFrame(() => { layoutPlan(); fit(); });
  setStatus(`${m.blockCount} lanes · ${m.operatorCount} operators`);
}

function buildLaneBand(b: Block): HTMLElement {
  const band = document.createElement("div");
  band.className = "lane-band";
  band.dataset.block = b.id;
  return band;
}

function buildLaneHeader(b: Block): HTMLElement {
  const head = document.createElement("div");
  head.className = "lane-header";
  head.dataset.block = b.id;
  head.style.borderLeftColor = b.accent;
  head.style.width = `${LANE_HEADER_W - 18}px`;
  head.innerHTML = `
    <span class="lane-caret">▾</span>
    <span class="lane-kind" style="background:${b.accent}">${escapeHtml(b.kind)}</span>
    <span class="lane-name">${escapeHtml(b.name)}</span>
    <span class="lane-meta">${b.operators.length} op${b.operators.length === 1 ? "" : "s"}</span>`;
  head.addEventListener("mouseenter", () => { if (!focusOp) emphasizeLane(b); });
  head.addEventListener("mouseleave", () => { if (!focusOp) clearHighlight(); });
  head.addEventListener("click", (e) => { e.stopPropagation(); toggleCollapse(b.id); });
  return head;
}

function buildOp(op: Operator, b: Block): HTMLElement {
  const boxEl = document.createElement("div");
  boxEl.className = "op-box";
  boxEl.id = op.id;
  boxEl.dataset.block = b.id;
  const color = TYPE_COLOR[op.type] || "#64748b";

  const lines = op.detail.length
    ? op.detail.map((d) => `<div class="op-line">${escapeHtml(d)}</div>`).join("")
    : `<div class="op-empty">—</div>`;
  const src = op.sourceRef ? `<div class="op-src">◀ from ${escapeHtml(op.sourceRef)}</div>` : "";
  const badge = op.badge ? `<span class="op-badge">${escapeHtml(op.badge)}</span>` : "";

  boxEl.innerHTML = `
    <div class="op-head" style="background:${color}">
      <span class="op-type">${escapeHtml(op.type)}</span>
      <span class="op-title">${escapeHtml(op.title)}</span>
      ${badge}
    </div>
    <div class="op-body">${lines}${src}</div>`;

  boxEl.addEventListener("mouseenter", () => { if (!focusOp) highlightForOp(op); });
  boxEl.addEventListener("mouseleave", () => { if (!focusOp) clearHighlight(); });
  boxEl.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    opDrag = {
      id: op.id, sx: e.clientX, sy: e.clientY,
      ox: parseFloat(boxEl.style.left) || 0, oy: parseFloat(boxEl.style.top) || 0,
      moved: false,
    };
  });
  boxEl.addEventListener("click", (e) => {
    e.stopPropagation();
    if (suppressClick) { suppressClick = false; return; }
    toggleFocusOp(op);
  });
  return boxEl;
}

// ---- layout (deterministic lane grid) -------------------------------------
function layoutPlan() {
  if (!model) return;
  let y = PAD;
  let maxRight = 0;
  for (const b of model.blocks) {
    const isCollapsed = collapsed.has(b.id);
    const header = wrapper.querySelector<HTMLElement>(`.lane-header[data-block="${b.id}"]`)!;
    const band = wrapper.querySelector<HTMLElement>(`.lane-band[data-block="${b.id}"]`)!;
    const opEls = b.operators.map((op) => document.getElementById(op.id)!);

    // lane height = tallest visible element (header or any operator)
    opEls.forEach((el) => { el.style.display = isCollapsed ? "none" : ""; });
    header.querySelector(".lane-caret")!.textContent = isCollapsed ? "▸" : "▾";
    let laneH = header.offsetHeight;
    if (!isCollapsed) for (const el of opEls) laneH = Math.max(laneH, el.offsetHeight);

    header.style.left = `${PAD}px`;
    header.style.top = `${y}px`;

    let right = PAD + LANE_HEADER_W;
    if (!isCollapsed) {
      opEls.forEach((el, k) => {
        const x = PAD + LANE_HEADER_W + k * (OP_W + OP_GAP);
        el.style.left = `${x}px`;
        el.style.top = `${y}px`;
        right = x + OP_W;
      });
    } else {
      right = PAD + LANE_HEADER_W + 40;
    }

    band.style.left = `${PAD - 10}px`;
    band.style.top = `${y - 10}px`;
    band.style.width = `${right - (PAD - 10) + 10}px`;
    band.style.height = `${laneH + 20}px`;

    maxRight = Math.max(maxRight, right);
    y += laneH + LANE_GAP;
  }
  wrapper.style.width = `${maxRight + PAD}px`;
  wrapper.style.height = `${y - LANE_GAP + PAD}px`;
  drawConnectors();
}

function growWrapperToContent() {
  let maxR = 0, maxB = 0;
  wrapper.querySelectorAll<HTMLElement>(".op-box, .lane-header, .lane-band").forEach((el) => {
    if (el.style.display === "none") return;
    const l = parseFloat(el.style.left) || 0, t = parseFloat(el.style.top) || 0;
    maxR = Math.max(maxR, l + el.offsetWidth);
    maxB = Math.max(maxB, t + el.offsetHeight);
  });
  wrapper.style.width = `${maxR + PAD}px`;
  wrapper.style.height = `${maxB + PAD}px`;
}

// ---- connectors -----------------------------------------------------------
let wRaw: DOMRect | null = null;
function relCoords(el: Element) {
  const r = el.getBoundingClientRect();
  if (!wRaw) return r as any;
  return {
    left: (r.left - wRaw.left) / zoom, top: (r.top - wRaw.top) / zoom,
    right: (r.right - wRaw.left) / zoom, bottom: (r.bottom - wRaw.top) / zoom,
    width: r.width / zoom, height: r.height / zoom,
  };
}
function arrowHead(x: number, y: number, dir: "right" | "down", color: string, ops: string) {
  const s = 5;
  const pts = dir === "right"
    ? `${x},${y} ${x - s},${y - s} ${x - s},${y + s}`
    : `${x},${y} ${x - s},${y - s} ${x + s},${y - s}`;
  const tri = elNS("polygon", { points: pts, fill: color });
  (tri as any).dataset.ops = ops;
  svg.appendChild(tri);
}
function drawConnectors() {
  if (!model) return;
  wRaw = wrapper.getBoundingClientRect();
  svg.innerHTML = "";
  svg.setAttribute("width", String(wRaw.width / zoom));
  svg.setAttribute("height", String(wRaw.height / zoom));

  // intra-lane flow arrows (operator k → k+1)
  for (const b of model.blocks) {
    if (collapsed.has(b.id)) continue;
    for (let k = 0; k < b.operators.length - 1; k++) {
      const a = document.getElementById(b.operators[k].id);
      const c = document.getElementById(b.operators[k + 1].id);
      if (!a || !c) continue;
      const ra = relCoords(a), rc = relCoords(c);
      const sx = ra.right, sy = ra.top + ra.height / 2;
      const ex = rc.left - 6, ey = rc.top + rc.height / 2;
      const bow = Math.max(20, (ex - sx) / 2);
      const ops = `${b.operators[k].id} ${b.operators[k + 1].id}`;
      const path = elNS("path", {
        d: `M${sx},${sy} C${sx + bow},${sy} ${ex - bow},${ey} ${ex},${ey}`,
        stroke: "var(--text-muted)", "stroke-width": 1.6, fill: "none", "stroke-linecap": "round",
      });
      (path as any).dataset.ops = ops;
      svg.appendChild(path);
      arrowHead(ex + 6, ey, "right", "var(--text-muted)", ops);
    }
  }

  // cross-lane dataflow edges (producing lane → consuming operator)
  for (const e of model.edges) {
    const prod = block(e.from);
    if (!prod) continue;
    const srcEl = collapsed.has(prod.id)
      ? wrapper.querySelector<HTMLElement>(`.lane-header[data-block="${prod.id}"]`)
      : document.getElementById(prod.operators[prod.operators.length - 1].id);
    const tgt = document.getElementById(e.toOp);
    if (!srcEl || !tgt) continue;
    const rs = relCoords(srcEl), rt = relCoords(tgt);
    const sx = rs.left + rs.width / 2, sy = rs.bottom;
    const tx = rt.left + rt.width / 2, ty = rt.top - 6;
    const my = (sy + ty) / 2;
    const ops = `${prod.operators[prod.operators.length - 1]?.id || prod.id} ${e.toOp}`;
    const path = elNS("path", {
      d: `M${sx},${sy} C${sx},${my} ${tx},${my} ${tx},${ty}`,
      stroke: e.color, "stroke-width": 2.5, fill: "none", "stroke-linecap": "round",
    });
    (path as any).dataset.ops = ops;
    svg.appendChild(path);
    arrowHead(tx, ty + 6, "down", e.color, ops);

    // label pill at the midpoint
    const txt = e.label;
    if (txt) {
      const bw = Math.max(70, txt.length * 6.2), bh = 19;
      const pill = elNS("rect", {
        x: tx - bw / 2, y: my - bh / 2, width: bw, height: bh, rx: 9,
        fill: "var(--surface)", stroke: e.color, "stroke-width": 1.4,
      });
      (pill as any).dataset.ops = ops;
      svg.appendChild(pill);
      const label = elNS("text", {
        x: tx, y: my + 3.5, "text-anchor": "middle", "font-family": "var(--sans)",
        "font-size": 9.5, "font-weight": 700, fill: e.color,
      });
      label.textContent = txt;
      (label as any).dataset.ops = ops;
      svg.appendChild(label);
    }
  }
  paintConnectorEmphasis(currentEmphasis);
}

// ---- emphasis / highlight -------------------------------------------------
let currentEmphasis: Set<string> | null = null;

function paintConnectorEmphasis(ids: Set<string> | null) {
  svg.querySelectorAll<SVGElement>("[data-ops]").forEach((el) => {
    const list = (el.dataset.ops || "").split(" ");
    const on = !ids || list.some((o) => ids.has(o));
    el.style.opacity = on ? "1" : "0.12";
  });
}
function setEmphasis(ids: Set<string> | null) {
  currentEmphasis = ids;
  wrapper.querySelectorAll<HTMLElement>(".op-box").forEach((el) =>
    el.classList.toggle("dim", !!ids && !ids.has(el.id)));
  wrapper.querySelectorAll<HTMLElement>(".lane-header").forEach((el) => {
    const b = block(el.dataset.block!);
    const on = !ids || !!b?.operators.some((o) => ids.has(o.id));
    el.classList.toggle("dim", !!ids && !on);
  });
  legendEl.querySelectorAll<HTMLElement>(".legend-badge").forEach((el) => {
    const b = block(el.dataset.block!);
    const on = !!ids && !!b?.operators.some((o) => ids.has(o.id));
    el.classList.toggle("dim", !!ids && !on);
    el.classList.toggle("active", on);
  });
  paintConnectorEmphasis(ids);
}
function highlightForOp(op: Operator) {
  setEmphasis(new Set([op.id]));
  highlightSql([...op.tables, ...op.columns], false, false);
}
function emphasizeLane(b: Block) {
  setEmphasis(new Set(b.operators.map((o) => o.id)));
  highlightSql(b.sqlTerms, false, false);
}
function clearHighlight() {
  if (focusOp) return;
  setEmphasis(null);
  renderSql();
}
function toggleFocusOp(op: Operator) {
  if (focusOp === op.id) { focusOp = null; clearFocusBtn.style.display = "none"; setEmphasis(null); renderSql(); return; }
  focusOp = op.id;
  clearFocusBtn.style.display = "";
  setEmphasis(new Set([op.id]));
  highlightSql([...op.tables, ...op.columns], true, true);
}
function focusLane(b: Block) {
  focusOp = `lane:${b.id}`;
  clearFocusBtn.style.display = "";
  setEmphasis(new Set(b.operators.map((o) => o.id)));
  highlightSql(b.sqlTerms, true, true);
}
function clearFocus() {
  focusOp = null;
  clearFocusBtn.style.display = "none";
  setEmphasis(null);
  renderSql();
}

// ---- collapse / detail ----------------------------------------------------
function toggleCollapse(id: string) {
  if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
  if (userArranged) { growWrapperToContent(); drawConnectors(); } else layoutPlan();
}

// ---- SQL highlighting -----------------------------------------------------
function renderSql() { if (model) sqlEl.innerHTML = escapeHtml(model.sql); }
function highlightSql(terms: string[], strong: boolean, open: boolean) {
  if (!model) return;
  const uniq = [...new Set(terms.filter(Boolean))];
  if (!uniq.length) { renderSql(); return; }
  const re = new RegExp("\\b(" + uniq.map(escapeRe).join("|") + ")\\b", "g");
  sqlEl.innerHTML = escapeHtml(model.sql).replace(
    re, (m) => `<mark${strong ? ' class="strong"' : ""}>${m}</mark>`);
  if (open) {
    (document.getElementById("qs-sql") as HTMLDetailsElement).open = true;
    const first = sqlEl.querySelector("mark");
    if (first) first.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

// ---- pan + zoom -----------------------------------------------------------
function applyTransform() {
  wrapper.style.transform = `translate(${panX}px,${panY}px) scale(${zoom})`;
  drawConnectors();
}
function setZoom(z: number, cx?: number, cy?: number) {
  const nz = Math.min(2, Math.max(MIN_ZOOM, z));
  if (cx != null && cy != null) {
    panX = cx - (cx - panX) * (nz / zoom);
    panY = cy - (cy - panY) * (nz / zoom);
  }
  zoom = nz;
  $("zoom-label").textContent = Math.round(zoom * 100) + "%";
  ($("zoom-out") as HTMLButtonElement).disabled = zoom <= MIN_ZOOM;
  ($("zoom-in") as HTMLButtonElement).disabled = zoom >= 2;
  applyTransform();
}
function fit() {
  panX = 0; panY = 0; zoom = 1;
  wrapper.style.transform = "none";
  const sw = stage.clientWidth, sh = stage.clientHeight;
  const ww = wrapper.scrollWidth, wh = wrapper.scrollHeight;
  const z = Math.min(1, (sw - 24) / ww, (sh - 24) / wh);
  zoom = Math.max(MIN_ZOOM, z || 1);
  panX = Math.max(0, (sw - ww * zoom) / 2);
  panY = Math.max(0, (sh - wh * zoom) / 2);
  $("zoom-label").textContent = Math.round(zoom * 100) + "%";
  applyTransform();
}
function resetView() {
  if (!model) { fit(); return; }
  clearFocus();
  userArranged = false;
  collapsed.clear();
  layoutPlan();
  fit();
}

let drag: { x: number; y: number; px: number; py: number } | null = null;
stage.addEventListener("mousedown", (e) => {
  if ((e.target as HTMLElement).closest(".op-box, .lane-header")) return;
  drag = { x: e.clientX, y: e.clientY, px: panX, py: panY };
  stage.classList.add("panning");
});
window.addEventListener("mousemove", (e) => {
  if (!drag) return;
  panX = drag.px + (e.clientX - drag.x);
  panY = drag.py + (e.clientY - drag.y);
  applyTransform();
});
window.addEventListener("mouseup", () => { drag = null; stage.classList.remove("panning"); });
stage.addEventListener("click", (e) => {
  if ((e.target as HTMLElement) === stage || (e.target as HTMLElement).id === "diagram-wrapper") clearFocus();
});
stage.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = stage.getBoundingClientRect();
  setZoom(zoom * (e.deltaY < 0 ? 1.1 : 0.9), e.clientX - rect.left, e.clientY - rect.top);
}, { passive: false });

// ---- drag an operator -----------------------------------------------------
window.addEventListener("mousemove", (e) => {
  if (!opDrag) return;
  if (!opDrag.moved && Math.hypot(e.clientX - opDrag.sx, e.clientY - opDrag.sy) < 4) return;
  const el = document.getElementById(opDrag.id);
  if (!el) return;
  if (!opDrag.moved) { opDrag.moved = true; userArranged = true; el.classList.add("dragging"); }
  el.style.left = Math.max(0, opDrag.ox + (e.clientX - opDrag.sx) / zoom) + "px";
  el.style.top = Math.max(0, opDrag.oy + (e.clientY - opDrag.sy) / zoom) + "px";
  growWrapperToContent();
  drawConnectors();
});
window.addEventListener("mouseup", () => {
  if (!opDrag) return;
  document.getElementById(opDrag.id)?.classList.remove("dragging");
  if (opDrag.moved) suppressClick = true;
  opDrag = null;
});

$("zoom-in").addEventListener("click", () => setZoom(zoom + 0.2));
$("zoom-out").addEventListener("click", () => setZoom(zoom - 0.2));
$("zoom-reset").addEventListener("click", fit);
$("reset-view").addEventListener("click", resetView);
clearFocusBtn.addEventListener("click", clearFocus);

// ---- toolbar toggles ------------------------------------------------------
$("t-detail").addEventListener("click", () => {
  const btn = $("t-detail");
  const hidden = document.body.hasAttribute("data-hide-detail");
  if (hidden) document.body.removeAttribute("data-hide-detail");
  else document.body.setAttribute("data-hide-detail", "");
  btn.classList.toggle("on", hidden);
  btn.classList.toggle("off", !hidden);
  if (userArranged) { growWrapperToContent(); drawConnectors(); } else layoutPlan();
});
$("collapse-all").addEventListener("click", () => {
  if (!model) return;
  const allCollapsed = model.blocks.every((b) => collapsed.has(b.id));
  collapsed.clear();
  if (!allCollapsed) model.blocks.forEach((b) => collapsed.add(b.id));
  userArranged = false;
  layoutPlan();
});

// ---- theme ----------------------------------------------------------------
function setTheme(theme: "light" | "dark") {
  document.documentElement.dataset.theme = theme;
  $("theme-toggle").textContent = theme === "dark" ? "Light" : "Dark";
  drawConnectors();
}
$("theme-toggle").addEventListener("click", () =>
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

// ---- copy + export --------------------------------------------------------
$("copy-sql").addEventListener("click", async (e) => {
  e.preventDefault(); e.stopPropagation();
  if (!model) return;
  try { await navigator.clipboard.writeText(model.sql); setStatus("SQL copied to clipboard"); }
  catch { setStatus("Copy blocked by the host", true); }
});

let editing = false;
$("edit-sql").addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openEditor(); });
function openEditor() {
  if (editing || !model) return;
  editing = true;
  (document.getElementById("qs-sql") as HTMLDetailsElement).open = true;
  const body = sqlEl.parentElement!;
  const ta = document.createElement("textarea");
  ta.className = "sql-edit"; ta.value = model.sql; ta.spellcheck = false;
  const bar = document.createElement("div"); bar.className = "edit-bar";
  const apply = document.createElement("button"); apply.className = "chip on"; apply.textContent = "Apply";
  const cancel = document.createElement("button"); cancel.className = "chip"; cancel.textContent = "Cancel";
  bar.append(apply, cancel);
  sqlEl.style.display = "none"; body.append(ta, bar);
  const close = () => { ta.remove(); bar.remove(); sqlEl.style.display = ""; editing = false; };
  cancel.addEventListener("click", close);
  apply.addEventListener("click", async () => {
    if (busy) return;
    busy = true; apply.textContent = "Parsing…";
    try {
      const res: any = await app.callServerTool({
        name: "parse_query_plan", arguments: { sql: ta.value, title: model?.title },
      });
      const sc = res?.structuredContent as Model | undefined;
      close();
      if (sc && sc.blocks) { render(sc); setStatus(`Re-parsed via parse_query_plan · ${sc.blockCount} lanes`); }
      else setStatus("parse_query_plan returned no blocks", true);
    } catch (err: any) {
      apply.textContent = "Apply";
      setStatus("Parse failed: " + (err?.message || err), true);
    } finally { busy = false; }
  });
}

const EXPORT_VARS = [
  "--bg", "--surface", "--surface-alt", "--surface-subtle", "--border", "--border-strong",
  "--text", "--text-strong", "--text-muted", "--shadow", "--badge-bg", "--badge-border",
  "--badge-text", "--lane-band", "--lane-line", "--mono", "--sans",
];
function diagramSvg(): { svg: string; w: number; h: number } {
  const w = Math.ceil(wrapper.scrollWidth), h = Math.ceil(wrapper.scrollHeight);
  const cs = getComputedStyle(document.documentElement);
  const vars = EXPORT_VARS.map((v) => `${v}:${cs.getPropertyValue(v)}`).join(";");
  const styleText = document.querySelector("style")!.textContent || "";
  const clone = wrapper.cloneNode(true) as HTMLElement;
  clone.style.transform = "none";
  const body = new XMLSerializer().serializeToString(clone);
  const svgStr =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<foreignObject x="0" y="0" width="${w}" height="${h}">` +
    `<div xmlns="http://www.w3.org/1999/xhtml" style="${vars};background:var(--bg);width:${w}px;height:${h}px">` +
    `<style>${styleText}</style>${body}</div></foreignObject></svg>`;
  return { svg: svgStr, w, h };
}
function download(href: string, name: string) {
  const a = document.createElement("a");
  a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove();
}

// A self-contained HTML snapshot: the pipeline markup + the full stylesheet +
// the baked theme variables + the source SQL. Static (no MCP interactivity), so
// it opens in any browser for viewing / sharing / embedding.
function diagramHtml(): string {
  const w = Math.ceil(wrapper.scrollWidth), h = Math.ceil(wrapper.scrollHeight);
  const cs = getComputedStyle(document.documentElement);
  const vars = EXPORT_VARS.map((v) => `${v}:${cs.getPropertyValue(v)}`).join(";");
  const styleText = document.querySelector("style")!.textContent || "";
  const theme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const clone = wrapper.cloneNode(true) as HTMLElement;
  clone.removeAttribute("id");
  clone.style.position = "relative";
  clone.style.transform = "none";
  clone.style.width = `${w}px`;
  clone.style.height = `${h}px`;
  const title = escapeHtml(model?.title || "Query plan");
  const nl = model?.nl ? `<p class="x-nl">${escapeHtml(model.nl)}</p>` : "";
  const sql = escapeHtml(model?.sql || "");
  return `<!DOCTYPE html>
<html lang="en" data-theme="${theme}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<style>${styleText}
  body { margin:0; padding:24px; ${vars}; background:var(--bg); color:var(--text); font-family:var(--sans); }
  .x-title { font-size:18px; font-weight:700; margin:0 0 6px; color:var(--text-strong); }
  .x-nl { font-style:italic; max-width:900px; color:var(--text-muted); margin:0 0 16px; }
  .x-stage { position:relative; overflow:auto; }
  .x-sql { margin-top:24px; }
  .x-sql pre { margin:0; background:var(--sql-bg); color:var(--sql-text); padding:15px; border-radius:8px;
    overflow-x:auto; font-family:var(--mono); font-size:11.5px; line-height:1.6; white-space:pre; }
</style>
</head>
<body>
  <h1 class="x-title">${title}</h1>
  ${nl}
  <div class="x-stage">${clone.outerHTML}</div>
  <div class="x-sql"><pre>${sql}</pre></div>
</body>
</html>`;
}
function showAsHtml() {
  if (!model) return;
  let html: string;
  try { html = diagramHtml(); } catch { setStatus("Show as HTML failed", true); return; }
  try {
    download("data:text/html;charset=utf-8," + encodeURIComponent(html),
      `${model.title || "query-plan"}.html`);
    setStatus("Exported standalone HTML");
  } catch {
    navigator.clipboard.writeText(html)
      .then(() => setStatus("Download blocked — HTML copied to clipboard instead"))
      .catch(() => setStatus("Show as HTML blocked by the host", true));
  }
}
$("show-html").addEventListener("click", showAsHtml);
$("export-svg").addEventListener("click", () => {
  try {
    const { svg: s } = diagramSvg();
    download("data:image/svg+xml;charset=utf-8," + encodeURIComponent(s), `${model?.title || "query-plan"}.svg`);
    setStatus("Exported SVG");
  } catch { setStatus("Export blocked by the host", true); }
});
$("export-png").addEventListener("click", () => {
  try {
    const { svg: s, w, h } = diagramSvg();
    const img = new Image();
    const scale = Math.min(2, window.devicePixelRatio || 1);
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = w * scale; canvas.height = h * scale;
      const ctx = canvas.getContext("2d")!;
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0);
      try { download(canvas.toDataURL("image/png"), `${model?.title || "query-plan"}.png`); setStatus("Exported PNG"); }
      catch { setStatus("PNG export blocked by the host", true); }
    };
    img.onerror = () => setStatus("PNG export failed", true);
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(s);
  } catch { setStatus("Export blocked by the host", true); }
});

// ---- host context + MCP wiring -------------------------------------------
function applyHostContext(ctx: McpUiHostContext) {
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx?.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
  $("theme-toggle").textContent = document.documentElement.dataset.theme === "dark" ? "Light" : "Dark";
  if (model) drawConnectors();
}

const app = new App({ name: "Query Plan", version: "0.1.0" });
app.ontoolresult = (result: any) => {
  const sc = result?.structuredContent as Model | undefined;
  if (sc && sc.blocks) render(sc);
};
app.onhostcontextchanged = applyHostContext;
app.onerror = (e: any) => console.error(e);

window.addEventListener("resize", () => { if (model) drawConnectors(); });

app.connect().then(() => {
  const ctx = app.getHostContext();
  if (ctx) applyHostContext(ctx);
  if (!model) setStatus("Connected — awaiting query…");
});
