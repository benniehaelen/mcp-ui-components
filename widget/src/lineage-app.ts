/**
 * Lineage viewer — an interactive MCP App widget.
 *
 * Built with the official MCP Apps SDK (@modelcontextprotocol/ext-apps) so it
 * renders as a real interactive control in MCP Apps hosts (VS Code Copilot,
 * Claude Desktop, …).
 *
 * Interactions — every one that needs data is proxied through the host as a
 * governed MCP tool call:
 *   - initial render        ← `view_lineage` result via `app.ontoolresult`
 *   - click a node          → `describe_node`         (details panel)
 *   - ＋ on a node          → `expand_lineage_node`    (reveal neighbours)
 *   - − on a node           → collapse (local; hides the branch)
 *   - double-click a node   → `view_lineage`           (recenter on that node)
 */
import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
  applyHostFonts,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";

type Node = {
  id: string; label: string; kind: string; description?: string;
  layer: number; isFocus?: boolean; upstreamCount: number; downstreamCount: number;
};
type Edge = { source: string; target: string };
type Graph = { focus: string; nodes: Node[]; edges: Edge[] };

const KIND_COLOR: Record<string, string> = {
  source: "#5b7fa6", staging: "#c08a3e", fact: "#4e8a5f",
  metric: "#7d5ba6", view: "#3e8f96",
};
const NODE_W = 132, NODE_H = 46, COL_GAP = 92, ROW_GAP = 22, PAD = 30;
const SVGNS = "http://www.w3.org/2000/svg";

const svg = document.getElementById("svg") as unknown as SVGSVGElement;
const statusEl = document.getElementById("status") as HTMLElement;
const detailsEl = document.getElementById("details") as HTMLElement;
const detailsBody = document.getElementById("details-body") as HTMLElement;

let graph: Graph = { focus: "", nodes: [], edges: [] };
let initialGraph: Graph | null = null;
let newIds: Record<string, boolean> = {};
let selected: string | null = null;
let busy = false;

// ---- small graph helpers (operate on the *visible* graph) --------------
const nodeById = (id: string) => graph.nodes.find((n) => n.id === id);
const visParents = (id: string) => graph.edges.filter((e) => e.target === id).map((e) => e.source);
const visChildren = (id: string) => graph.edges.filter((e) => e.source === id).map((e) => e.target);
const visibleIds = () => graph.nodes.map((n) => n.id);

function hiddenIn(id: string, dir: "upstream" | "downstream"): boolean {
  const n = nodeById(id);
  if (!n) return false;
  const total = dir === "upstream" ? n.upstreamCount : n.downstreamCount;
  const shown = (dir === "upstream" ? visParents(id) : visChildren(id)).length;
  return shown < total;
}
function shownIn(id: string, dir: "upstream" | "downstream"): boolean {
  return (dir === "upstream" ? visParents(id) : visChildren(id)).length > 0;
}

// ---- rendering ----------------------------------------------------------
function el(tag: string, attrs: Record<string, any>): SVGElement {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, String(attrs[k]));
  return e;
}

function layout(nodes: Node[]) {
  const byLayer: Record<number, Node[]> = {};
  nodes.forEach((n) => (byLayer[n.layer] = byLayer[n.layer] || []).push(n));
  const layers = Object.keys(byLayer).map(Number).sort((a, b) => a - b);
  let maxRows = 1;
  layers.forEach((L) => (maxRows = Math.max(maxRows, byLayer[L].length)));
  const contentH = maxRows * NODE_H + (maxRows - 1) * ROW_GAP;
  const pos: Record<string, { x: number; y: number }> = {};
  layers.forEach((L, ci) => {
    const col = byLayer[L].slice().sort((a, b) => (a.label < b.label ? -1 : 1));
    const colH = col.length * NODE_H + (col.length - 1) * ROW_GAP;
    const y0 = PAD + (contentH - colH) / 2;
    col.forEach((n, ri) => {
      pos[n.id] = { x: PAD + ci * (NODE_W + COL_GAP), y: y0 + ri * (NODE_H + ROW_GAP) };
    });
  });
  const width = PAD * 2 + layers.length * NODE_W + (layers.length - 1) * COL_GAP;
  const height = PAD * 2 + contentH;
  return { pos, width, height: Math.max(height, 170) };
}

function edgePath(a: { x: number; y: number }, b: { x: number; y: number }) {
  const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
}

function makeToggle(
  cx: number, cy: number, plus: boolean,
  node: string, dir: "upstream" | "downstream", onClick: () => void,
) {
  const g = el("g", {
    class: "toggle", transform: `translate(${cx},${cy})`,
    "data-node": node, "data-dir": dir, "data-mode": plus ? "expand" : "collapse",
  });
  g.appendChild(el("circle", { r: 9 }));
  const t = el("text", { x: 0, y: 0 });
  t.textContent = plus ? "+" : "−"; // − minus sign
  g.appendChild(t);
  g.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(); });
  return g;
}

function render() {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!graph.nodes.length) return;
  const L = layout(graph.nodes);
  svg.setAttribute("viewBox", `0 0 ${L.width} ${L.height}`);

  graph.edges.forEach((e) => {
    const a = L.pos[e.source], b = L.pos[e.target];
    if (!a || !b) return;
    let cls = "edge";
    if (e.source === graph.focus || e.target === graph.focus) cls += " focus";
    if (newIds[e.source] || newIds[e.target]) cls += " new";
    svg.appendChild(el("path", { d: edgePath(a, b), class: cls }));
  });

  graph.nodes.forEach((n) => {
    const p = L.pos[n.id];
    if (!p) return;
    const g = el("g", {
      class: "node-box" + (n.isFocus ? " focus" : "") + (selected === n.id ? " selected" : ""),
      transform: `translate(${p.x},${p.y})`,
    });
    g.appendChild(el("rect", { class: "body", width: NODE_W, height: NODE_H, rx: 7 }));
    g.appendChild(el("rect", { x: 0, y: 0, width: 5, height: NODE_H, rx: 2, fill: KIND_COLOR[n.kind] || "#9aa1ad" }));
    const label = el("text", { class: "label", x: 16, y: 21 });
    label.textContent = n.label;
    g.appendChild(label);
    const kind = el("text", { class: "kind", x: 16, y: 35 });
    kind.textContent = n.kind;
    g.appendChild(kind);
    if (n.isFocus) {
      const star = el("text", { x: NODE_W - 15, y: 21, "font-size": 12, fill: "#4e8a5f" });
      star.textContent = "★";
      g.appendChild(star);
    }
    g.addEventListener("click", () => selectNode(n.id));
    g.addEventListener("dblclick", () => refocus(n.id));
    svg.appendChild(g);

    // expand/collapse toggles, drawn outside the node body
    if (n.upstreamCount > 0) {
      const canExpand = hiddenIn(n.id, "upstream");
      const canCollapse = !canExpand && shownIn(n.id, "upstream");
      if (canExpand || canCollapse) {
        svg.appendChild(makeToggle(p.x - 2, p.y + NODE_H / 2, canExpand, n.id, "upstream",
          () => canExpand ? expand(n.id, "upstream") : collapse(n.id, "upstream")));
      }
    }
    if (n.downstreamCount > 0) {
      const canExpand = hiddenIn(n.id, "downstream");
      const canCollapse = !canExpand && shownIn(n.id, "downstream");
      if (canExpand || canCollapse) {
        svg.appendChild(makeToggle(p.x + NODE_W + 2, p.y + NODE_H / 2, canExpand, n.id, "downstream",
          () => canExpand ? expand(n.id, "downstream") : collapse(n.id, "downstream")));
      }
    }
  });
}

// ---- state changes ------------------------------------------------------
function setGraph(data: Graph, keepInitial = false) {
  graph = { focus: data.focus, nodes: (data.nodes || []).slice(), edges: (data.edges || []).slice() };
  if (!keepInitial) initialGraph = JSON.parse(JSON.stringify(graph));
  newIds = {};
  selected = null;
  closeDetails();
  render();
  setStatus(summary());
}

function mergeAdded(res: { addedNodes?: Node[]; addedEdges?: Edge[] }) {
  const have: Record<string, boolean> = {};
  graph.nodes.forEach((n) => (have[n.id] = true));
  const added: Record<string, boolean> = {};
  (res.addedNodes || []).forEach((n) => {
    if (!have[n.id]) { graph.nodes.push(n); have[n.id] = true; added[n.id] = true; }
  });
  const ek: Record<string, boolean> = {};
  graph.edges.forEach((e) => (ek[e.source + ">" + e.target] = true));
  (res.addedEdges || []).forEach((e) => {
    const k = e.source + ">" + e.target;
    if (!ek[k]) { graph.edges.push(e); ek[k] = true; }
  });
  newIds = added;
  render();
}

/** Hide the branch reachable from `node` in `dir` that exists only to feed it. */
function collapse(node: string, dir: "upstream" | "downstream") {
  const removed = new Set<string>();
  const seed = dir === "upstream" ? visParents(node) : visChildren(node);
  const queue = [...seed];
  while (queue.length) {
    const x = queue.pop()!;
    if (removed.has(x) || x === graph.focus) continue;
    // links that keep x alive (its dependants on the far side)
    const far = dir === "upstream" ? visChildren(x) : visParents(x);
    const alive = far.filter((c) => c !== node && !removed.has(c));
    if (alive.length === 0) {
      removed.add(x);
      const next = dir === "upstream" ? visParents(x) : visChildren(x);
      next.forEach((p) => queue.push(p));
    }
  }
  if (!removed.size) return;
  graph.nodes = graph.nodes.filter((n) => !removed.has(n.id));
  graph.edges = graph.edges.filter((e) => !removed.has(e.source) && !removed.has(e.target));
  if (selected && removed.has(selected)) closeDetails();
  newIds = {};
  render();
  setStatus(`Collapsed ${removed.size} node${removed.size > 1 ? "s" : ""} · ${summary()}`);
}

async function expand(node: string, dir: "upstream" | "downstream") {
  if (busy) return;
  busy = true;
  setStatus(`Proxying expand_lineage_node (${dir}) through the host…`);
  try {
    const res: any = await app.callServerTool({
      name: "expand_lineage_node",
      arguments: { node, direction: dir, focus: graph.focus, visible_node_ids: visibleIds() },
    });
    const sc = res?.structuredContent || {};
    const n = (sc.addedNodes || []).length;
    mergeAdded(sc);
    setStatus(n ? `Expanded ${dir} · +${n} node${n > 1 ? "s" : ""} · ${summary()}`
                 : `Nothing more ${dir} · ${summary()}`);
  } catch (e: any) {
    setStatus("Expand failed: " + (e?.message || e), true);
  } finally {
    busy = false;
  }
}

async function refocus(node: string) {
  if (busy || node === graph.focus) return;
  busy = true;
  setStatus(`Recentering on ${node} via view_lineage…`);
  try {
    const res: any = await app.callServerTool({ name: "view_lineage", arguments: { node } });
    if (res?.structuredContent?.nodes) setGraph(res.structuredContent);
    setStatus(`Focus: ${node} · ${summary()}`);
  } catch (e: any) {
    setStatus("Recenter failed: " + (e?.message || e), true);
  } finally {
    busy = false;
  }
}

async function selectNode(id: string) {
  selected = id;
  render();
  const n = nodeById(id);
  detailsEl.classList.add("open");
  detailsBody.innerHTML = `<div class="kind">Loading…</div><h2>${n?.label ?? id}</h2>`;
  try {
    const res: any = await app.callServerTool({ name: "describe_node", arguments: { node: id } });
    renderDetails(res?.structuredContent || {});
    setStatus(`${n?.label ?? id} — details fetched via describe_node`);
  } catch (e: any) {
    detailsBody.innerHTML = `<h2>${n?.label ?? id}</h2><p class="desc">Could not load details: ${e?.message || e}</p>`;
  }
}

function renderDetails(d: any) {
  const color = KIND_COLOR[d.kind] || "#9aa1ad";
  const fmt = (v: any) => (typeof v === "number" ? v.toLocaleString() : v ?? "—");
  const chips = (arr: string[]) =>
    (arr && arr.length) ? arr.map((c) => `<span>${c}</span>`).join("") : "<span>—</span>";
  detailsBody.innerHTML = `
    <div class="dh"><span class="accent" style="background:${color}"></span><h2>${d.label ?? ""}</h2></div>
    <p class="kind">${d.kind ?? ""}</p>
    <p class="desc">${d.description ?? ""}</p>
    <dl>
      <dt>owner</dt><dd>${d.owner ?? "—"}</dd>
      <dt>grain</dt><dd>${d.grain ?? "—"}</dd>
      <dt>rows</dt><dd>${fmt(d.rows)}</dd>
      <dt>updated</dt><dd>${d.updated ?? "—"}</dd>
    </dl>
    <div class="sec">columns</div><div class="cols">${chips(d.columns)}</div>
    <div class="sec">upstream</div><div class="nbr">${chips(d.upstream)}</div>
    <div class="sec">downstream</div><div class="nbr">${chips(d.downstream)}</div>`;
}

function closeDetails() {
  detailsEl.classList.remove("open");
  if (selected) { selected = null; render(); }
}

// ---- ui glue ------------------------------------------------------------
function summary() { return `${graph.nodes.length} nodes · ${graph.edges.length} edges`; }
function setStatus(text: string, isErr = false) {
  statusEl.textContent = text;
  statusEl.className = isErr ? "err" : "";
}
document.getElementById("details-close")!.addEventListener("click", closeDetails);
document.getElementById("reset")!.addEventListener("click", () => {
  if (initialGraph) { setGraph(JSON.parse(JSON.stringify(initialGraph)), true); setStatus("View reset · " + summary()); }
});

function applyHostContext(ctx: McpUiHostContext) {
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx?.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
}

// ---- MCP App wiring -----------------------------------------------------
const app = new App({ name: "Lineage Viewer", version: "0.2.0" });

app.ontoolresult = (result: any) => {
  const sc = result?.structuredContent as Graph | undefined;
  if (sc && sc.nodes) setGraph(sc);
};
app.onhostcontextchanged = applyHostContext;
app.onerror = (e: any) => console.error(e);

app.connect().then(() => {
  const ctx = app.getHostContext();
  if (ctx) applyHostContext(ctx);
  if (!graph.nodes.length) setStatus("Connected — awaiting lineage…");
});
