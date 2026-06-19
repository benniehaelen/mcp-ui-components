# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A runnable reference implementation of **MCP Apps** (SEP-1865, spec 2026-01-26): one MCP
server (`mcp-ui-components`) serves three interactive UI controls. A UI-enabled tool returns a
result carrying `_meta.ui.resourceUri`; the host fetches the named `ui://` resource and renders it
in a sandboxed iframe; every widget interaction is proxied back through the host as a *governed
MCP tool call* (same auth/guardrails/traces as a prompt). The three controls are the **lineage
viewer**, the **SQL join diagram**, and the **query plan**.

## Commands

```bash
# Install the Python server (mcp + sqlglot). `pip install mcp` alone omits sqlglot,
# which the two SQL controls require.
pip install -e .

# Run the real MCP server
python -m mcp_ui_components.server                 # stdio (what VS Code / Claude Desktop launch)
python -m mcp_ui_components.server --http --port 3001   # streamable HTTP at /mcp

# Offline browser preview — MCP server + vendored reference host, stdlib only, no Node, no mcp SDK
python demo/host.py   # host UI :8080, MCP server :8770, sandbox proxy :8081

# Tests
pip install pytest
python -m pytest tests/ -q
python -m pytest tests/test_query_plan.py -q                          # one file
python -m pytest tests/test_query_plan.py::test_name -q               # one test

# Rebuild a widget (only when changing widget UI; needs Node). INPUT env var picks the entry.
cd widget && npm install
npm run build       && cp dist/index.html        ../src/mcp_ui_components/widgets/viewer.html
npm run build:join  && cp dist/join-diagram.html ../src/mcp_ui_components/widgets/join-diagram.html
npm run build:plan  && cp dist/query-plan.html   ../src/mcp_ui_components/widgets/query-plan.html
```

## Architecture

**Two languages, one contract.** Python serves tools + `ui://` HTML resources; the widgets are
TypeScript apps. They communicate only over the MCP Apps SDK (`@modelcontextprotocol/ext-apps`),
which runs MCP JSON-RPC over `window.postMessage` — the sandboxed widget has no network of its
own. A widget receives its originating tool result via `app.ontoolresult` and proxies further
actions via `app.callServerTool({ name, arguments })`.

**One component per control.** Each control is a self-contained subpackage under
`src/mcp_ui_components/components/<x>/`. A component's `__init__.py` exposes four module-level names:
`TOOLS` (schemas), `RESOURCES`, `HANDLERS` (tool name → callable), and `RESOURCE_HTML` (`ui://`
uri → HTML loader); alongside it sit `provider.py` (the data logic) and, for the SQL controls,
`sql.py` (the parser). To add or change a tool, edit the owning component's `__init__.py` — nothing
else needs to know.

**`registry.py` is the aggregator**, deliberately transport-agnostic. It merges every component's
four names into flat `TOOLS`/`RESOURCES` lists plus `call_tool()` / `read_resource()` dispatch (by
name/uri lookup). Both `server.py` (real stdio + HTTP MCP server) and `demo/host.py` (offline HTTP
host) call the *same* `registry.call_tool()` — that's what makes the "button click is governed
exactly like a prompt" guarantee real: demo and production run identical code.

**What makes a tool UI-enabled:** `_meta.ui.resourceUri` (plus the legacy flat `ui/resourceUri`
for older hosts) on its schema, *and* the same `_meta` on the returned result. The resource's
content must use MIME type `text/html;profile=mcp-app` (`RESOURCE_MIME_TYPE`, defined in the package
`__init__.py`) — hosts reject a UI resource served as anything else.

**Per-control wiring** — view tool → widget resource → proxied action tool:
- Lineage (`components/lineage/`): `view_lineage` → `viewer.html` → `expand_lineage_node`,
  `describe_node`. `LineageProvider` over the in-memory graph in `data.py`.
- Join diagram (`components/join_diagram/`): `view_join_diagram` → `join-diagram.html` →
  `parse_join_sql` (re-parses edited SQL). `sql.py` parser via `JoinDiagramProvider`.
- Query plan (`components/query_plan/`): `view_query_plan` → `query-plan.html` → `parse_query_plan`.
  `sql.py` parser via `QueryPlanProvider`.
- Export (`export_join_diagram` / `export_query_plan`): `shared/export.py:write_standalone_html`
  writes a self-contained interactive HTML — the widget bundle with the parsed model injected as
  `window.__MCP_MODEL__`, so it renders in a plain browser with no host. Export lives *outside* the
  widget because the sandboxed iframe blocks downloads. Output dir: `MCP_UI_COMPONENTS_EXPORT_DIR`,
  else cwd (the workspace folder), else OS temp.

**Shared code** lives in `src/mcp_ui_components/shared/`: `sql.py` (the sqlglot AST helpers +
colour palette both parsers use — `query_plan/sql.py` and `join_diagram/sql.py` both import from
here, so neither depends on the other), `examples.py` (the seed SQL both SQL controls fall back to),
and `export.py`.

**Build pipeline.** Each `widgets/*.html` is the **committed build output** of a `widget/src/*-app.ts`
app, bundled into a single self-contained file by Vite + `vite-plugin-singlefile`. The `INPUT` env
var selects the entry (`index.html` / `join-diagram.html` / `query-plan.html`). Edit the `.ts`
source, never the built HTML directly. (The `widget/` TypeScript tree is intentionally flat — it is
*not* organized into per-component folders the way the Python package is.)

## Caching gotcha (matters when editing widgets)

The join/query widget loaders in their component `__init__.py` re-read from disk on **every** call —
after a `build:join`/`build:plan` + copy, just re-invoke the tool, no server restart. But the
lineage viewer loader (`components/lineage/__init__.py:_viewer_html`) is `@lru_cache`'d, so a viewer
rebuild **needs a server restart** to show up.

## Layout

```
src/mcp_ui_components/
  __init__.py     version + WIDGET_URI / JOIN_WIDGET_URI / QUERY_PLAN_URI + RESOURCE_MIME_TYPE
  registry.py     aggregates the components → flat TOOLS/RESOURCES + call_tool/read_resource
  server.py       real MCP server (stdio + streamable HTTP)
  shared/         sql.py (AST helpers + palette) · examples.py (seed SQL) · export.py
  components/
    lineage/      __init__.py (tools+dispatch) · provider.py · data.py
    join_diagram/ __init__.py · provider.py · sql.py (SQL -> join model)
    query_plan/   __init__.py · provider.py · sql.py (SQL -> query-plan model)
  widgets/*.html  committed build outputs
widget/           TypeScript widget source (Vite single-file builds; flat)
demo/host.py      offline launcher; demo/_vendor_host/ = MIT prebuilt reference host
tests/            test_provider.py, test_join_model.py, test_query_plan.py
```
