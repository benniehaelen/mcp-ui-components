/**
 * Lineage viewer — an interactive MCP App widget.
 *
 * Built with the official MCP Apps SDK (@modelcontextprotocol/ext-apps) so it
 * renders as a real interactive control in MCP Apps hosts (VS Code Copilot,
 * Claude Desktop, …).
 *
 * Every interaction that needs data is proxied through the host as a governed
 * MCP tool call:
 *   - initial render      ← `view_lineage` result via `app.ontoolresult`
 *   - click a node        → `describe_node`         (details panel)
 *   - ＋ on a node        → `expand_lineage_node`    (reveal neighbours)
 *   - − on a node         → collapse (local)
 *   - double-click a node → `view_lineage`           (recenter)
 *
 * Hovering a node traces its full lineage (upstream + downstream) and dims the
 * rest — locally, no tool call.
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
const NODE_W = 152, NODE_H = 50, COL_GAP = 96, ROW_GAP = 24, PAD = 34;
const SVGNS = "http://www.w3.org/2000/svg";

const svg = document.getElementById("svg") as unknown as SVGSVGElement;
const statusEl = document.getElementById("status") as HTMLElement;
const focusChip = document.getElementById("focus-chip") as HTMLElement;
const detailsEl = document.getElementById("details") as HTMLElement;
const detailsHead = document.getElementById("details-head") as HTMLElement;
const detailsBody = document.getElementById("details-body") as HTMLElement;

let graph: Graph = { focus: "", nodes: [], edges: [] };
let initialGraph: Graph | null = null;
let selected: string | null = null;
let busy = false;

// live element refs so hover-trace doesn't rebuild the whole SVG
let content: SVGGElement | null = null;
const nodeEls = new Map<string, SVGGElement>();
let edgeEls: { el: SVGPathElement; source: string; target: string }[] = [];

// ---- visible-graph helpers ----------------------------------------------
const nodeById = (id: string) => graph.nodes.find((n) => n.id === id);
const visParents = (id: string) => graph.edges.filter((e) => e.target === id).map((e) => e.source);
const visChildren = (id: string) => graph.edges.filter((e) => e.source === id).map((e) => e.target);
const visibleIds = () => graph.nodes.map((n) => n.id);

function hiddenCount(id: string, dir: "upstream" | "downstream"): number {
  const n = nodeById(id);
  if (!n) return 0;
  const total = dir === "upstream" ? n.upstreamCount : n.downstreamCount;
  const shown = (dir === "upstream" ? visParents(id) : visChildren(id)).length;
  return Math.max(0, total - shown);
}
function shownIn(id: string, dir: "upstream" | "downstream"): boolean {
  return (dir === "upstream" ? visParents(id) : visChildren(id)).length > 0;
}

/** The node's full lineage within the visible graph: it + ancestors + descendants. */
function lineageSet(id: string): Set<string> {
  const set = new Set<string>([id]);
  let q = [id];
  while (q.length) { const x = q.pop()!; for (const p of visParents(x)) if (!set.has(p)) { set.add(p); q.push(p); } }
  q = [id];
  while (q.length) { const x = q.pop()!; for (const c of visChildren(x)) if (!set.has(c)) { set.add(c); q.push(c); } }
  return set;
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
  return { pos, width, height: Math.max(height, 180) };
}

function edgePath(a: { x: number; y: number }, b: { x: number; y: number }) {
  const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x - 5, y2 = b.y + NODE_H / 2;
  const dx = Math.max(28, (x2 - x1) * 0.5);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

function makeToggle(
  cx: number, cy: number, label: string,
  node: string, dir: "upstream" | "downstream", mode: "expand" | "collapse",
  onClick: () => void,
) {
  const w = label.length <= 1 ? 19 : 13 + label.length * 7;
  const h = 18;
  const g = el("g", {
    class: "toggle", transform: `translate(${cx},${cy})`,
    "data-node": node, "data-dir": dir, "data-mode": mode,
  });
  g.appendChild(el("rect", { class: "pill", x: -w / 2, y: -h / 2, width: w, height: h, rx: 9 }));
  const t = el("text", { x: 0, y: 0 });
  t.textContent = label;
  g.appendChild(t);
  g.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(); });
  return g;
}

function render() {
  // keep <defs>; render into a dedicated content group
  if (!content) { content = el("g", { id: "content" }) as SVGGElement; svg.appendChild(content); }
  while (content.firstChild) content.removeChild(content.firstChild);
  nodeEls.clear();
  edgeEls = [];
  if (!graph.nodes.length) return;

  const L = layout(graph.nodes);
  svg.setAttribute("viewBox", `0 0 ${L.width} ${L.height}`);

  graph.edges.forEach((e) => {
    const a = L.pos[e.source], b = L.pos[e.target];
    if (!a || !b) return;
    const path = el("path", { d: edgePath(a, b), class: "edge" }) as SVGPathElement;
    content!.appendChild(path);
    edgeEls.push({ el: path, source: e.source, target: e.target });
  });

  graph.nodes.forEach((n) => {
    const p = L.pos[n.id];
    if (!p) return;
    const g = el("g", {
      class: "node-box" + (n.isFocus ? " focus" : "") + (selected === n.id ? " selected" : ""),
      transform: `translate(${p.x},${p.y})`,
    }) as SVGGElement;
    g.appendChild(el("rect", { class: "body", width: NODE_W, height: NODE_H, rx: 9 }));
    g.appendChild(el("rect", { x: 0, y: 0, width: 4, height: NODE_H, rx: 2, fill: KIND_COLOR[n.kind] || "#9aa1ad" }));
    const label = el("text", { class: "label", x: 17, y: 23 });
    label.textContent = n.label;
    g.appendChild(label);
    const kind = el("text", { class: "kind", x: 17, y: 38 });
    kind.textContent = n.kind;
    g.appendChild(kind);
    if (n.isFocus) {
      const star = el("text", { class: "star", x: NODE_W - 16, y: 23 });
      star.textContent = "★";
      g.appendChild(star);
    }
    g.addEventListener("click", () => selectNode(n.id));
    g.addEventListener("dblclick", () => refocus(n.id));
    g.addEventListener("mouseenter", () => { if (!selected) applyTrace(n.id); });
    g.addEventListener("mouseleave", () => { if (!selected) applyTrace(null); });
    content!.appendChild(g);
    nodeEls.set(n.id, g);

    // toggles
    if (n.upstreamCount > 0) {
      const hid = hiddenCount(n.id, "upstream");
      if (hid > 0) {
        content!.appendChild(makeToggle(p.x - 13, p.y + NODE_H / 2, "+" + hid, n.id, "upstream", "expand",
          () => expand(n.id, "upstream")));
      } else if (shownIn(n.id, "upstream")) {
        content!.appendChild(makeToggle(p.x - 13, p.y + NODE_H / 2, "−", n.id, "upstream", "collapse",
          () => collapse(n.id, "upstream")));
      }
    }
    if (n.downstreamCount > 0) {
      const hid = hiddenCount(n.id, "downstream");
      if (hid > 0) {
        content!.appendChild(makeToggle(p.x + NODE_W + 13, p.y + NODE_H / 2, "+" + hid, n.id, "downstream", "expand",
          () => expand(n.id, "downstream")));
      } else if (shownIn(n.id, "downstream")) {
        content!.appendChild(makeToggle(p.x + NODE_W + 13, p.y + NODE_H / 2, "−", n.id, "downstream", "collapse",
          () => collapse(n.id, "downstream")));
      }
    }
  });

  if (selected) applyTrace(selected);
}

/** Highlight a node's lineage and dim the rest (or clear when id is null). */
function applyTrace(id: string | null) {
  if (!id) {
    nodeEls.forEach((g) => g.classList.remove("dim"));
    edgeEls.forEach(({ el }) => el.classList.remove("trace", "dim"));
    return;
  }
  const set = lineageSet(id);
  nodeEls.forEach((g, nid) => g.classList.toggle("dim", !set.has(nid)));
  edgeEls.forEach(({ el, source, target }) => {
    const on = set.has(source) && set.has(target);
    el.classList.toggle("trace", on);
    el.classList.toggle("dim", !on);
  });
}

// ---- state changes ------------------------------------------------------
function setGraph(data: Graph, keepInitial = false) {
  graph = { focus: data.focus, nodes: (data.nodes || []).slice(), edges: (data.edges || []).slice() };
  if (!keepInitial) initialGraph = JSON.parse(JSON.stringify(graph));
  selected = null;
  focusChip.textContent = graph.focus;
  closeDetails();
  render();
  setStatus(summary());
}

function mergeAdded(res: { addedNodes?: Node[]; addedEdges?: Edge[] }) {
  const have: Record<string, boolean> = {};
  graph.nodes.forEach((n) => (have[n.id] = true));
  (res.addedNodes || []).forEach((n) => { if (!have[n.id]) { graph.nodes.push(n); have[n.id] = true; } });
  const ek: Record<string, boolean> = {};
  graph.edges.forEach((e) => (ek[e.source + ">" + e.target] = true));
  (res.addedEdges || []).forEach((e) => {
    const k = e.source + ">" + e.target;
    if (!ek[k]) { graph.edges.push(e); ek[k] = true; }
  });
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
    const far = dir === "upstream" ? visChildren(x) : visParents(x);
    const alive = far.filter((c) => c !== node && !removed.has(c));
    if (alive.length === 0) {
      removed.add(x);
      (dir === "upstream" ? visParents(x) : visChildren(x)).forEach((p) => queue.push(p));
    }
  }
  if (!removed.size) return;
  graph.nodes = graph.nodes.filter((n) => !removed.has(n.id));
  graph.edges = graph.edges.filter((e) => !removed.has(e.source) && !removed.has(e.target));
  if (selected && removed.has(selected)) closeDetails();
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
  } finally { busy = false; }
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
  } finally { busy = false; }
}

async function selectNode(id: string) {
  selected = id;
  nodeEls.forEach((g, nid) => g.classList.toggle("selected", nid === id));
  applyTrace(id);
  const n = nodeById(id);
  const color = KIND_COLOR[n?.kind || ""] || "#9aa1ad";
  detailsEl.classList.add("open");
  detailsHead.innerHTML = `<div class="pill"><span class="dot" style="background:${color}"></span>${n?.kind ?? ""}</div><h2>${n?.label ?? id}</h2>`;
  detailsBody.innerHTML = `<p class="desc">Loading details…</p>`;
  try {
    const res: any = await app.callServerTool({ name: "describe_node", arguments: { node: id } });
    renderDetails(res?.structuredContent || {});
    setStatus(`${n?.label ?? id} — details via describe_node`);
  } catch (e: any) {
    detailsBody.innerHTML = `<p class="desc">Couldn't load details: ${e?.message || e}</p>`;
  }
}

function renderDetails(d: any) {
  const color = KIND_COLOR[d.kind] || "#9aa1ad";
  const fmt = (v: any) => (typeof v === "number" ? v.toLocaleString() : (v ?? "—"));
  const chips = (arr: string[]) =>
    (arr && arr.length) ? arr.map((c) => `<span>${c}</span>`).join("") : "<span>—</span>";
  detailsHead.innerHTML =
    `<div class="pill"><span class="dot" style="background:${color}"></span>${d.kind ?? ""}</div><h2>${d.label ?? ""}</h2>`;
  detailsBody.innerHTML = `
    <p class="desc">${d.description ?? ""}</p>
    <dl>
      <dt>owner</dt><dd>${d.owner ?? "—"}</dd>
      <dt>grain</dt><dd>${d.grain ?? "—"}</dd>
      <dt>rows</dt><dd>${fmt(d.rows)}</dd>
      <dt>updated</dt><dd>${d.updated ?? "—"}</dd>
    </dl>
    <div class="sec">columns</div><div class="chips">${chips(d.columns)}</div>
    <div class="sec">upstream</div><div class="chips">${chips(d.upstream)}</div>
    <div class="sec">downstream</div><div class="chips">${chips(d.downstream)}</div>`;
}

function closeDetails() {
  detailsEl.classList.remove("open");
  if (selected) {
    nodeEls.get(selected)?.classList.remove("selected");
    selected = null;
    applyTrace(null);
  }
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
const app = new App({ name: "Lineage Viewer", version: "0.3.0" });

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
