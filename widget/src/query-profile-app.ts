/**
 * Query profile — a small analytic MCP App widget.
 *
 * Renders the *query-profile model* produced by the `view_query_profile` tool
 * (see src/mcp_ui_components/components/query_profile/sql.py): a compact card of
 * parse-derived analytics — table / join / operator counts, the join-type and
 * operator mix as mini bar charts, estimated scan, and a heuristic complexity
 * score. No diagram canvas; just an at-a-glance summary.
 *
 * Interactions:
 *   - theme toggle, follows the host theme                                (local)
 *   - copy SQL                                                            (local)
 *   - Query-source drawer (NL + SQL), collapsed by default                (local)
 *   - Edit SQL → Apply → re-parse via `parse_query_profile`      (governed tool call)
 */
import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
  applyHostFonts,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";

type Model = {
  title: string; dataset: string; dialect: string; nl: string | null; sql: string;
  scan: string | null; period: { start: string; end: string } | null;
  tableCount: number; joinCounts: Record<string, number>; joinTotal: number;
  blockCount: number; operatorCount: number; operatorCounts: Record<string, number>;
  complexity: {
    score: number; label: string;
    factors: { key: string; label: string; points: number; detail: string }[];
  };
};

// Per-join-type colours (mirror the diagram palette).
const JOIN_COLORS: Record<string, string> = {
  INNER: "#3b82f6", LEFT: "#22c55e", RIGHT: "#eab308",
  FULL: "#ec4899", CROSS: "#8b5cf6",
};

const $ = (id: string) => document.getElementById(id)!;
const sqlEl = $("sql-text");
const statusEl = $("status");

let model: Model | null = null;
let busy = false;
let editing = false;

function escapeHtml(s: string): string {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));
}
function setStatus(text: string, isErr = false) {
  statusEl.textContent = text;
  statusEl.className = isErr ? "err" : "";
}

function tile(num: number, label: string, hint: string): string {
  return `<div class="tile" title="${escapeHtml(hint)}"><div class="num">${num}</div><div class="lbl">${label}</div></div>`;
}

// One labelled bar; width is value / max so the longest bar in the group fills
// the track. The count is printed on the right; `tip` spells the bar out on hover.
function barRow(name: string, value: number, max: number, color: string, tip: string): string {
  const pct = max > 0 ? Math.max(4, Math.round((value / max) * 100)) : 0;
  return `<div class="bar-row" title="${escapeHtml(tip)}">
    <span class="bar-name">${escapeHtml(name)}</span>
    <span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:${color}"></span></span>
    <span class="bar-val">${value}</span>
  </div>`;
}

function renderBars(
  el: HTMLElement,
  entries: [string, number][],
  colorFor: (k: string) => string,
  tipFor: (k: string, n: number) => string,
  denom?: number,
) {
  if (!entries.length) { el.innerHTML = `<span class="empty-note">none</span>`; return; }
  // Normalize to `denom` (e.g. the total score) so the bars are a true share of
  // it; otherwise to the largest value so the biggest bar fills the track.
  const max = denom || Math.max(...entries.map(([, n]) => n));
  el.innerHTML = entries.map(([k, n]) => barRow(k, n, max, colorFor(k), tipFor(k, n))).join("");
}

// Compact "name N" pills — composition detail that doesn't roll up into the score.
function renderChips(
  el: HTMLElement,
  entries: [string, number][],
  dotFor?: (k: string) => string,
) {
  if (!entries.length) { el.innerHTML = `<span class="empty-note">none</span>`; return; }
  el.innerHTML = entries.map(([k, n]) => {
    const dot = dotFor ? `<span class="dot" style="background:${dotFor(k)}"></span>` : "";
    return `<span class="kv">${dot}${escapeHtml(k)} <b>${n}</b></span>`;
  }).join("");
}

// ---- render ---------------------------------------------------------------
function render(m: Model) {
  model = m;

  $("page-title").textContent = m.title || "Query profile";
  const parts = [
    m.dataset, (m.dialect || "sql").toUpperCase(),
    m.period ? `${m.period.start} → ${m.period.end}` : "",
  ].filter(Boolean);
  $("page-subtitle").textContent = parts.join("  ·  ");

  const cx = m.complexity || { score: 0, label: "—", factors: [] };
  const badge = $("complexity");
  badge.textContent = `Complexity: ${cx.label}` + (cx.score ? ` (${cx.score})` : "");
  badge.className = "complexity " + (cx.label || "").toLowerCase();

  $("scan-line").textContent = m.scan ? `Scan ${m.scan}` : "";

  $("tiles").innerHTML =
    tile(m.tableCount, "Tables", "Distinct physical tables referenced (CTE names don't count)") +
    tile(m.joinTotal, "Joins", "Total join operations in the query") +
    tile(m.blockCount, m.blockCount === 1 ? "Lane" : "Lanes",
      "Execution lanes: each CTE / subquery, plus the main query") +
    tile(m.operatorCount, "Operators", "Total logical operators across all lanes");

  // Complexity breakdown: each bar is one factor's POINTS, normalized to the
  // total score so the bars literally add up to the badge number.
  const factors = cx.factors || [];
  renderBars(
    $("cx-bars"),
    factors.map((f) => [f.label, f.points] as [string, number]),
    () => "var(--accent)",
    (label) => {
      const f = factors.find((x) => x.label === label);
      return f ? `${f.detail} = ${f.points} pts` : "";
    },
    cx.score || undefined,
  );
  $("cx-total").textContent = factors.length
    ? `Score ${cx.score} = ${factors.map((f) => f.points).join(" + ")}  ·  ${cx.label}`
    : "";

  // Composition detail (not part of the score) — compact pills.
  renderChips($("op-chips"), Object.entries(m.operatorCounts || {}));
  renderChips(
    $("join-chips"),
    Object.entries(m.joinCounts || {}),
    (k) => JOIN_COLORS[k.toUpperCase()] || "#06b6d4",
  );

  const nl = m.nl || "";
  $("nl-text").textContent = nl || "No natural-language prompt provided.";
  ($("qs-nl") as HTMLElement).style.display = nl ? "" : "none";
  ($("nl-tag") as HTMLElement).style.display = nl ? "" : "none";
  $("sql-tag").textContent = (m.dialect || "sql").toUpperCase();
  renderSql();

  setStatus(`${m.tableCount} tables · ${m.joinTotal} joins · ${m.operatorCount} operators · ${cx.label} complexity`);
}

function renderSql() { if (model) sqlEl.innerHTML = escapeHtml(model.sql); }

// ---- copy + edit-SQL round-trip -------------------------------------------
$("copy-sql").addEventListener("click", async (e) => {
  e.preventDefault(); e.stopPropagation();
  if (!model) return;
  try { await navigator.clipboard.writeText(model.sql); setStatus("SQL copied to clipboard"); }
  catch { setStatus("Copy blocked by the host", true); }
});

$("edit-sql").addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openEditor(); });
function openEditor() {
  if (editing || !model) return;
  editing = true;
  ($("qs-drawer") as HTMLDetailsElement).open = true;
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
        name: "parse_query_profile", arguments: { sql: ta.value, title: model?.title },
      });
      const sc = res?.structuredContent as Model | undefined;
      close();
      if (sc && typeof sc.tableCount === "number") {
        render(sc); setStatus(`Re-parsed via parse_query_profile · ${sc.operatorCount} operators`);
      } else setStatus("parse_query_profile returned no model", true);
    } catch (err: any) {
      apply.textContent = "Apply";
      setStatus("Parse failed: " + (err?.message || err), true);
    } finally { busy = false; }
  });
}

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

const app = new App({ name: "Query Profile", version: "0.1.0" });
app.ontoolresult = (result: any) => {
  const sc = result?.structuredContent as Model | undefined;
  if (sc && typeof sc.tableCount === "number") render(sc);
};
app.onhostcontextchanged = applyHostContext;
app.onerror = (e: any) => console.error(e);

// Standalone mode (a future export tool could inject the model here); otherwise
// connect to the MCP host as usual.
const injected = (window as any).__MCP_MODEL__ as Model | undefined;
if (injected && typeof injected.tableCount === "number") {
  document.body.dataset.standalone = "1";
  render(injected);
  setStatus("Standalone — Edit SQL needs the live server");
} else {
  app.connect().then(() => {
    const ctx = app.getHostContext();
    if (ctx) applyHostContext(ctx);
    if (!model) setStatus("Connected — awaiting query…");
  });
}
