/**
 * Join diagram — an interactive MCP App widget.
 *
 * Renders the *join-diagram model* produced by the `view_join_diagram` tool
 * (see src/lineage_mcp/sql_joins.py): one card per table wired by join-key
 * connectors, plus the natural-language prompt and the source SQL.
 *
 * Interactions layered on the static diagram:
 *   - hover/click a join key   → highlight that key across every card + its
 *                                 connectors, dim the rest                 (local)
 *   - hover a card             → highlight its table/CTE in the SQL          (local)
 *   - click a card / legend    → focus that join, dim everything else        (local)
 *   - column/derived/filter toggles, drag-to-pan, zoom, export, copy SQL    (local)
 *   - paste SQL into the prompt box → re-parse via `parse_join_sql`   (governed tool call)
 *
 * The connector geometry is ported from the original standalone diagram; only
 * the data source (a parsed model instead of hardcoded markup) has changed.
 */
import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
  applyHostFonts,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";

type Card = {
  id: string; name: string; fullName: string; dataset: string;
  alias: string | null; cte: string | null; role: string; joinType: string | null;
  accent: string; header: string;
  joinKeys: string[]; selectedColumns: string[];
  derivedOutputs: { name: string; expr: string }[];
  filters: { column: string; expr: string; clause: string }[];
};
type Join = {
  from: string; to: string; fromName: string; toName: string;
  keys: string[]; type: string; color: string; label: string;
};
type Model = {
  title: string; dataset: string; tableCount: number;
  joinCounts: Record<string, number>; scan: string | null;
  period: { start: string; end: string } | null;
  dialect: string; nl: string | null; sql: string;
  tables: Card[]; joins: Join[];
};

const SVGNS = "http://www.w3.org/2000/svg";
const PORT_SPACING = 18;

// layout geometry (CARD_W must match .table-card width in the CSS)
const CARD_W = 320;
const COL_GAP = 110;   // horizontal space between layers
const ROW_GAP = 30;    // vertical space between stacked cards in a layer
const PAD = 40;        // inset from the wrapper edge
const MIN_ZOOM = 0.25; // allow big diagrams to fully fit on screen

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
let focusCard: string | null = null;
let busy = false;

// drag-to-move state for cards. `userArranged` latches once the user has hand-
// placed a card, so auto-relayout no longer clobbers their arrangement.
let cardDrag: { id: string; sx: number; sy: number; ox: number; oy: number; moved: boolean } | null = null;
let suppressCardClick = false;
let userArranged = false;

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

// ---- render ---------------------------------------------------------------
function render(m: Model) {
  model = m;
  userArranged = false;  // a fresh model re-enables auto-layout

  // header
  $("page-title").textContent = m.title || "Join diagram";
  const joinBits = Object.entries(m.joinCounts)
    .map(([t, n]) => `${n}× ${t} JOIN`).join(", ");
  const parts = [
    m.dataset, `${m.tableCount} tables`, joinBits,
    m.scan ? `Scan: ${m.scan}` : "",
    m.period ? `Period: ${m.period.start} to ${m.period.end}` : "",
  ].filter(Boolean);
  $("page-subtitle").innerHTML = parts.join(" &nbsp;·&nbsp; ");

  // legend
  legendEl.innerHTML = "";
  m.joins.forEach((j, i) => {
    const b = document.createElement("span");
    b.className = "legend-badge";
    b.dataset.join = String(i);
    b.innerHTML = `<span class="legend-dot" style="background:${j.color}"></span>${escapeHtml(j.label)}`;
    b.addEventListener("mouseenter", () => emphasizeJoins((jn) => jn === j));
    b.addEventListener("mouseleave", () => { if (!focusCard) clearEmphasis(); });
    b.addEventListener("click", () => { focusOn(j.from); highlightSql(j.keys, true, true); });
    legendEl.appendChild(b);
  });

  // cards
  wrapper.querySelectorAll(".table-card").forEach((n) => n.remove());
  for (const c of m.tables) wrapper.appendChild(buildCard(c));

  // query source
  const nl = m.nl || "";
  $("nl-text").textContent = nl || "No natural-language prompt provided.";
  (document.getElementById("qs-nl") as HTMLDetailsElement).style.display = nl ? "" : "none";
  $("sql-tag").textContent = (m.dialect || "sql").toUpperCase();
  renderSql();

  attachKeyInteractions();
  requestAnimationFrame(() => { layoutCards(); fit(); });
  setStatus(`${m.tableCount} tables · ${m.joins.length} joins`);
}

function buildCard(c: Card): HTMLElement {
  const card = document.createElement("div");
  card.className = "table-card";
  card.id = c.id;
  card.style.borderColor = c.accent;

  const badges: string[] = [];
  if (c.alias) badges.push(`<span class="card-badge">alias: ${escapeHtml(c.alias)}</span>`);
  if (c.cte) badges.push(`<span class="card-badge">CTE: ${escapeHtml(c.cte)}</span>`);
  if (c.role === "spine") badges.push(`<span class="card-badge spine">⬤ SPINE</span>`);
  else if (c.joinType) badges.push(`<span class="card-badge">${escapeHtml(c.joinType)} JOIN</span>`);

  const keyRows = c.joinKeys.map((k) =>
    `<div class="col-row" data-card="${c.id}" data-key="${escapeHtml(k)}">
       <span class="col-icon key">KEY</span>
       <span class="col-name is-key">${escapeHtml(k)}</span>
     </div>`).join("");

  const colRows = c.selectedColumns.length
    ? c.selectedColumns.map((n) =>
        `<div class="col-row"><span class="col-icon col">C</span><span class="col-name">${escapeHtml(n)}</span></div>`).join("")
    : `<p class="empty-note">No standalone columns selected from this table.</p>`;

  const derivedRows = c.derivedOutputs.map((d) =>
    `<div class="col-row"><span class="col-icon col">ƒ</span>
       <span class="col-name">${escapeHtml(d.name)}</span>
       <span class="col-sub">${escapeHtml(d.expr)}</span></div>`).join("");

  const filterRows = c.filters.length
    ? c.filters.map((f) =>
        `<div class="col-row"><span class="col-icon filter">F</span>
           <span class="col-name">${escapeHtml(f.column || "filter")}</span>
           <span class="col-sub">${escapeHtml(f.expr)} (${escapeHtml(f.clause)})</span></div>`).join("")
    : `<p class="empty-note">No standalone WHERE filter on this table.</p>`;

  card.innerHTML = `
    <div class="card-header" style="background:${c.header}">
      <div class="card-table-name">${escapeHtml(c.name)}</div>
      <div class="card-dataset">${escapeHtml(c.fullName)}</div>
      <div class="badge-row">${badges.join("")}</div>
    </div>
    <div class="card-body">
      <div class="section-label">Join Keys</div>
      ${keyRows || `<p class="empty-note">No join keys.</p>`}
      <div class="section-label sec-selected">Selected Columns</div><div class="sec-selected">${colRows}</div>
      ${c.derivedOutputs.length ? `<div class="section-label sec-derived">Derived Output</div><div class="sec-derived">${derivedRows}</div>` : ""}
      <div class="section-label sec-filters">Filters</div><div class="sec-filters">${filterRows}</div>
    </div>`;

  // hover a card → show it in the SQL; click → focus its joins
  card.addEventListener("mouseenter", () => {
    if (!focusCard) highlightSql([c.name, c.cte].filter(Boolean) as string[], false, false);
  });
  card.addEventListener("mouseleave", () => { if (!focusCard) renderSql(); });
  // press-and-drag to reposition; a plain press (no movement) still counts as a click
  card.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();  // don't let the stage start a pan
    cardDrag = {
      id: c.id, sx: e.clientX, sy: e.clientY,
      ox: parseFloat(card.style.left) || 0, oy: parseFloat(card.style.top) || 0,
      moved: false,
    };
  });
  card.addEventListener("click", (e) => {
    e.stopPropagation();
    if (suppressCardClick) { suppressCardClick = false; return; }  // ended a drag, not a click
    focusOn(focusCard === c.id ? null : c.id);
    if (c.id) highlightSql([c.name, c.cte].filter(Boolean) as string[], false, true);
  });
  return card;
}

// ---- join-key highlighting ------------------------------------------------
function attachKeyInteractions() {
  wrapper.querySelectorAll<HTMLElement>(".col-row[data-key]").forEach((row) => {
    const key = row.dataset.key!;
    row.addEventListener("mouseenter", () => highlightKey(key));
    row.addEventListener("mouseleave", () => { if (!focusCard) highlightKey(null); });
    row.addEventListener("click", (e) => { e.stopPropagation(); highlightSql([key], true, true); });
  });
}

function highlightKey(key: string | null) {
  wrapper.querySelectorAll<HTMLElement>(".col-row[data-key]").forEach((r) => {
    r.classList.toggle("key-hl", !!key && r.dataset.key === key);
  });
  if (!model) return;
  if (key) emphasizeJoins((j) => j.keys.includes(key));
  else if (!focusCard) clearEmphasis();
}

// ---- focus mode -----------------------------------------------------------
function focusOn(cardId: string | null) {
  focusCard = cardId;
  clearFocusBtn.style.display = cardId ? "" : "none";
  if (!model) return;
  if (!cardId) { clearEmphasis(); highlightKey(null); renderSql(); return; }
  const related = new Set<string>([cardId]);
  model.joins.forEach((j) => {
    if (j.from === cardId || j.to === cardId) { related.add(j.from); related.add(j.to); }
  });
  wrapper.querySelectorAll<HTMLElement>(".table-card").forEach((c) =>
    c.classList.toggle("dim", !related.has(c.id)));
  emphasizeJoins((j) => j.from === cardId || j.to === cardId, false);
}

// ---- connector emphasis ---------------------------------------------------
// Remembered so a redraw (zoom/pan/theme) can reapply the current highlight.
let emphasisPred: ((j: Join) => boolean) | null = null;

function paintConnectorOpacity() {
  if (!model) return;
  const active = new Set<string>();
  if (emphasisPred) {
    model.joins.forEach((j, i) => { if (emphasisPred!(j)) active.add(String(i)); });
  }
  svg.querySelectorAll<SVGElement>("[data-join]").forEach((p) => {
    p.style.opacity = !emphasisPred || active.has(p.dataset.join!) ? "1" : "0.12";
  });
}

function emphasizeJoins(pred: (j: Join) => boolean, dimCards = true) {
  if (!model) return;
  emphasisPred = pred;
  const active = new Set<string>();
  model.joins.forEach((j) => { if (pred(j)) { active.add(j.from); active.add(j.to); } });
  paintConnectorOpacity();
  legendEl.querySelectorAll<HTMLElement>(".legend-badge").forEach((b, i) => {
    const on = model!.joins.some((j, k) => k === i && pred(j));
    b.classList.toggle("dim", !on);
    b.classList.toggle("active", on);
  });
  if (dimCards && !focusCard) {
    wrapper.querySelectorAll<HTMLElement>(".table-card").forEach((c) =>
      c.classList.toggle("dim", !active.has(c.id)));
  }
}
function clearEmphasis() {
  emphasisPred = null;
  paintConnectorOpacity();
  legendEl.querySelectorAll(".legend-badge").forEach((b) => b.classList.remove("dim", "active"));
  if (!focusCard) wrapper.querySelectorAll(".table-card").forEach((c) => c.classList.remove("dim"));
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

// ---- layered graph layout -------------------------------------------------
// Arranges cards in columns by their join-graph distance from the spine, so the
// diagram grows mostly *sideways* (one column per join hop) instead of stacking
// into one tall scroll. Within a column, cards are ordered by the average row of
// their already-placed neighbours to keep connectors from crossing, then the
// shorter columns are vertically centred against the tallest one.
function layoutCards() {
  if (!model || !model.tables.length) return;
  const cards = model.tables;

  // undirected adjacency over the join graph
  const adj = new Map<string, Set<string>>();
  cards.forEach((c) => adj.set(c.id, new Set()));
  model.joins.forEach((j) => {
    adj.get(j.from)?.add(j.to);
    adj.get(j.to)?.add(j.from);
  });

  // root = spine, else the most-connected table, else the first
  let root = cards.find((c) => c.role === "spine")?.id;
  if (!root) root = [...adj.entries()].sort((a, b) => b[1].size - a[1].size)[0]?.[0];
  root = root ?? cards[0].id;

  // BFS: each card's column is its graph distance from the root
  const colOf = new Map<string, number>([[root, 0]]);
  const queue = [root];
  while (queue.length) {
    const id = queue.shift()!;
    for (const nb of adj.get(id)!) {
      if (!colOf.has(nb)) { colOf.set(nb, colOf.get(id)! + 1); queue.push(nb); }
    }
  }
  // tables not reachable from the root (no joins) get their own trailing columns
  let maxCol = 0;
  colOf.forEach((v) => { if (v > maxCol) maxCol = v; });
  cards.forEach((c) => { if (!colOf.has(c.id)) colOf.set(c.id, ++maxCol); });

  // bucket cards into columns, keeping model order as the initial within-column order
  const columns: string[][] = [];
  cards.forEach((c) => { (columns[colOf.get(c.id)!] ??= []).push(c.id); });

  // crossing reduction: sort each column by the mean row of its placed left-neighbours
  const rowOf = new Map<string, number>();
  const bary = (id: string) => {
    const prev = [...adj.get(id)!].filter((n) => rowOf.has(n) && colOf.get(n)! < colOf.get(id)!);
    return prev.length ? prev.reduce((s, n) => s + rowOf.get(n)!, 0) / prev.length : Number.MAX_SAFE_INTEGER;
  };
  columns.forEach((ids, k) => {
    if (k > 0) ids.sort((a, b) => bary(a) - bary(b));
    ids.forEach((id, r) => rowOf.set(id, r));
  });

  // measure each column's stacked height (cards vary in height)
  const colHeights: number[] = [];
  const colTops: number[][] = [];
  let maxColH = 0;
  columns.forEach((ids, k) => {
    let h = 0; const tops: number[] = [];
    ids.forEach((id) => { tops.push(h); h += document.getElementById(id)!.offsetHeight + ROW_GAP; });
    h = Math.max(0, h - ROW_GAP);
    colHeights[k] = h; colTops[k] = tops;
    if (h > maxColH) maxColH = h;
  });

  // place cards; centre shorter columns vertically against the tallest one
  let maxRight = 0, maxBottom = 0;
  columns.forEach((ids, k) => {
    const x = PAD + k * (CARD_W + COL_GAP);
    const yOff = PAD + (maxColH - colHeights[k]) / 2;
    ids.forEach((id, r) => {
      const el = document.getElementById(id)!;
      const top = yOff + colTops[k][r];
      el.style.left = x + "px";
      el.style.top = top + "px";
      maxRight = Math.max(maxRight, x + CARD_W);
      maxBottom = Math.max(maxBottom, top + el.offsetHeight);
    });
  });

  // size the wrapper to its content so fit()/export measure correctly
  wrapper.style.width = maxRight + PAD + "px";
  wrapper.style.height = maxBottom + PAD + "px";
}

// Resize the wrapper so it always contains every (possibly hand-moved) card —
// keeps the connector SVG and fit()/export bounds in sync after a drag.
function growWrapperToContent() {
  let maxR = 0, maxB = 0;
  wrapper.querySelectorAll<HTMLElement>(".table-card").forEach((el) => {
    const l = parseFloat(el.style.left) || 0, t = parseFloat(el.style.top) || 0;
    if (l + el.offsetWidth > maxR) maxR = l + el.offsetWidth;
    if (t + el.offsetHeight > maxB) maxB = t + el.offsetHeight;
  });
  wrapper.style.width = maxR + PAD + "px";
  wrapper.style.height = maxB + PAD + "px";
}

// ---- connectors (geometry ported from the standalone diagram) -------------
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
function classify(f: any, t: any) {
  const toRight = t.left > f.right - 6, toBelow = t.top > f.bottom - 6;
  if (toRight && !toBelow) return "horizontal";
  if (toBelow && !toRight) return "vertical";
  return "diagonal";
}
function keySpanMidY(card: Element, cardId: string, keys: string[]) {
  const rows = keys
    .map((k) => card.querySelector(`[data-key="${k}"][data-card="${cardId}"]`))
    .filter(Boolean) as Element[];
  if (!rows.length) { const rc = relCoords(card); return rc.top + rc.height / 2; }
  const ys = rows.map((row) => { const rc = relCoords(row); return rc.top + rc.height / 2; });
  return ys.reduce((s, v) => s + v, 0) / ys.length;
}
function drawConnectors() {
  if (!model) return;
  wRaw = wrapper.getBoundingClientRect();
  svg.innerHTML = "";
  svg.setAttribute("width", String(wRaw.width / zoom));
  svg.setAttribute("height", String(wRaw.height / zoom));

  const exitSlots: any = {}, entrySlots: any = {};
  const joins = model.joins;
  joins.forEach((j, i) => {
    const fc = document.getElementById(j.from), tc = document.getElementById(j.to);
    if (!fc || !tc) return;
    const type = classify(relCoords(fc), relCoords(tc));
    const exit = type === "vertical" ? "bottom" : "right";
    const entry = type === "vertical" ? "top" : "left";
    ((exitSlots[j.from] ??= {})[exit] ??= []).push(i);
    ((entrySlots[j.to] ??= {})[entry] ??= []).push(i);
  });

  joins.forEach((j, i) => {
    const fc = document.getElementById(j.from), tc = document.getElementById(j.to);
    if (!fc || !tc) return;
    const f = relCoords(fc), t = relCoords(tc);
    const type = classify(f, t);
    const exit = type === "vertical" ? "bottom" : "right";
    const entry = type === "vertical" ? "top" : "left";
    const srcY = keySpanMidY(fc, j.from, j.keys), dstY = keySpanMidY(tc, j.to, j.keys);
    const ep = exitSlots[j.from][exit], np = entrySlots[j.to][entry];
    const eo = (ep.indexOf(i) - (ep.length - 1) / 2) * PORT_SPACING;
    const no = (np.indexOf(i) - (np.length - 1) / 2) * PORT_SPACING;

    let sx, sy, ex, ey, d, mx, my;
    if (type === "vertical") {
      sx = f.left + f.width / 2 + eo; sy = f.bottom;
      ex = t.left + t.width / 2 + no; ey = t.top;
      const lane = sx + (ex - sx) / 2;
      d = `M${sx},${sy} L${lane},${sy} L${lane},${ey} L${ex},${ey}`;
      mx = lane; my = (sy + ey) / 2;
    } else {
      const bow = type === "diagonal" ? 80 : 50;
      sx = f.right; sy = srcY + eo; ex = t.left; ey = dstY + no;
      d = `M${sx},${sy} C${sx + bow},${sy} ${ex - bow},${ey} ${ex},${ey}`;
      mx = (sx + ex) / 2; my = (sy + ey) / 2;
    }

    const path = elNS("path", { d, stroke: j.color, "stroke-width": 2.5, "stroke-linecap": "round", fill: "none" });
    path.dataset.join = String(i);
    svg.appendChild(path);

    const txt = `${j.type} · ${j.keys.join(" + ")}`;
    const bw = Math.max(90, txt.length * 6.0), bh = 21;
    const pill = elNS("rect", { x: mx - bw / 2, y: my - bh / 2, width: bw, height: bh, rx: 10,
      fill: "var(--surface)", stroke: j.color, "stroke-width": 1.6 });
    pill.dataset.join = String(i);
    svg.appendChild(pill);
    const label = elNS("text", { x: mx, y: my + 4, "text-anchor": "middle",
      "font-family": "var(--sans)", "font-size": 9.5, "font-weight": 700, fill: j.color });
    label.textContent = txt;
    label.dataset.join = String(i);
    svg.appendChild(label);

    [[sx, sy], [ex, ey]].forEach(([cx, cy]) => {
      const c = elNS("circle", { cx, cy, r: 4.5, fill: j.color });
      c.dataset.join = String(i);
      svg.appendChild(c);
    });
  });

  paintConnectorOpacity();  // keep any active highlight after a redraw
}

// ---- pan + zoom -----------------------------------------------------------
function applyTransform() {
  wrapper.style.transform = `translate(${panX}px,${panY}px) scale(${zoom})`;
  drawConnectors();
}
function setZoom(z: number, cx?: number, cy?: number) {
  const nz = Math.min(2, Math.max(MIN_ZOOM, z));
  if (cx != null && cy != null) {
    // keep the point under the cursor stationary
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
  // reset pan, scale so the whole diagram fits the stage
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

// Restore the diagram to its just-rendered state: clear focus/highlights, undo
// any hand-dragged card positions (re-run auto-layout), and re-fit zoom + pan.
function resetView() {
  if (!model) { fit(); return; }
  focusOn(null);          // clears focus, connector emphasis, key + SQL highlight
  userArranged = false;   // re-enable auto-layout
  layoutCards();          // restore the layered arrangement
  fit();                  // reset zoom + pan to frame it
}

let drag: { x: number; y: number; px: number; py: number } | null = null;
stage.addEventListener("mousedown", (e) => {
  if ((e.target as HTMLElement).closest(".table-card")) return; // let card clicks through
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

// ---- drag a card to reposition it -----------------------------------------
window.addEventListener("mousemove", (e) => {
  if (!cardDrag) return;
  if (!cardDrag.moved && Math.hypot(e.clientX - cardDrag.sx, e.clientY - cardDrag.sy) < 4) return;
  const el = document.getElementById(cardDrag.id);
  if (!el) return;
  if (!cardDrag.moved) {
    cardDrag.moved = true;
    userArranged = true;          // stop auto-relayout from clobbering hand placement
    el.classList.add("dragging");
  }
  // divide by zoom so the card tracks the cursor under the wrapper's scale
  const nx = Math.max(0, cardDrag.ox + (e.clientX - cardDrag.sx) / zoom);
  const ny = Math.max(0, cardDrag.oy + (e.clientY - cardDrag.sy) / zoom);
  el.style.left = nx + "px";
  el.style.top = ny + "px";
  growWrapperToContent();
  drawConnectors();
});
window.addEventListener("mouseup", () => {
  if (!cardDrag) return;
  document.getElementById(cardDrag.id)?.classList.remove("dragging");
  if (cardDrag.moved) suppressCardClick = true;  // swallow the click that trails a drag
  cardDrag = null;
});
stage.addEventListener("click", (e) => {
  if ((e.target as HTMLElement) === stage || (e.target as HTMLElement).id === "diagram-wrapper") {
    focusOn(null);
  }
});
stage.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = stage.getBoundingClientRect();
  setZoom(zoom * (e.deltaY < 0 ? 1.1 : 0.9), e.clientX - rect.left, e.clientY - rect.top);
}, { passive: false });

$("zoom-in").addEventListener("click", () => setZoom(zoom + 0.2));
$("zoom-out").addEventListener("click", () => setZoom(zoom - 0.2));
$("zoom-reset").addEventListener("click", fit);
$("reset-view").addEventListener("click", resetView);
clearFocusBtn.addEventListener("click", () => focusOn(null));

// ---- section toggles ------------------------------------------------------
for (const id of ["t-selected", "t-derived", "t-filters"]) {
  const btn = $(id);
  btn.addEventListener("click", () => {
    const sec = (btn as HTMLElement).dataset.sec!;
    const attr = `hide-${sec}`;
    const hidden = document.body.hasAttribute(`data-${attr}`);
    if (hidden) document.body.removeAttribute(`data-${attr}`);
    else document.body.setAttribute(`data-${attr}`, "");
    btn.classList.toggle("on", hidden);
    btn.classList.toggle("off", !hidden);
    // card heights changed → re-flow columns, unless the user hand-arranged them
    if (userArranged) growWrapperToContent(); else layoutCards();
    drawConnectors();
  });
}

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

// ---- edit SQL → re-parse (governed round-trip via parse_join_sql) ---------
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
        name: "parse_join_sql", arguments: { sql: ta.value, title: model?.title },
      });
      const sc = res?.structuredContent as Model | undefined;
      close();
      if (sc && sc.tables) { render(sc); setStatus(`Re-parsed via parse_join_sql · ${sc.tableCount} tables`); }
      else setStatus("parse_join_sql returned no tables", true);
    } catch (err: any) {
      apply.textContent = "Apply";
      setStatus("Parse failed: " + (err?.message || err), true);
    } finally { busy = false; }
  });
}

const EXPORT_VARS = [
  "--bg", "--surface", "--surface-alt", "--surface-subtle", "--border", "--border-strong",
  "--text", "--text-strong", "--text-muted", "--shadow", "--key-bg", "--key-text", "--key-border",
  "--col-bg", "--col-text", "--col-border", "--filter-bg", "--filter-text", "--filter-border",
  "--badge-bg", "--badge-border", "--badge-text", "--mono", "--sans",
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
$("export-svg").addEventListener("click", () => {
  try {
    const { svg: s } = diagramSvg();
    download("data:image/svg+xml;charset=utf-8," + encodeURIComponent(s),
      `${model?.title || "join-diagram"}.svg`);
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
      try {
        download(canvas.toDataURL("image/png"), `${model?.title || "join-diagram"}.png`);
        setStatus("Exported PNG");
      } catch { setStatus("PNG export blocked by the host", true); }
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

const app = new App({ name: "Join Diagram", version: "0.1.0" });
app.ontoolresult = (result: any) => {
  const sc = result?.structuredContent as Model | undefined;
  if (sc && sc.tables) render(sc);
};
app.onhostcontextchanged = applyHostContext;
app.onerror = (e: any) => console.error(e);

window.addEventListener("resize", () => { if (model) drawConnectors(); });

app.connect().then(() => {
  const ctx = app.getHostContext();
  if (ctx) applyHostContext(ctx);
  if (!model) setStatus("Connected — awaiting query…");
});
