"""Tool + resource definitions, independent of any transport.

Both halves of the diagram are wired from here:

  * ``server.py``  exposes these over a real stdio MCP server (Claude Desktop, etc.)
  * ``demo/host.py`` exposes the same functions over HTTP for the browser demo.

Keeping one source of truth means the "button click is governed exactly like a
prompt" guarantee is real: the demo host and a production host run identical code.

The UI-enabled tool ``view_lineage`` advertises its widget through
``_meta.ui.resourceUri`` (the SEP-1865 / MCP Apps convention). When the host
sees a tool result carrying that meta, it fetches the named ``ui://`` resource
and renders it sandboxed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from functools import lru_cache
from importlib import resources

from . import JOIN_WIDGET_URI, QUERY_PLAN_URI, WIDGET_URI
from .data import FOCUS_ID
from .provider import JoinDiagramProvider, LineageProvider, QueryPlanProvider

_provider = LineageProvider()
_joins = JoinDiagramProvider()
_plans = QueryPlanProvider()


def _export_dir() -> str:
    """Where exported HTML is written.

    Prefers ``LINEAGE_MCP_EXPORT_DIR`` (set it in ``mcp.json`` to pin a folder),
    else the current working directory — which is the workspace folder when a
    host like VS Code launches the stdio server — falling back to the OS temp
    dir if the cwd isn't writable.
    """
    override = os.environ.get("LINEAGE_MCP_EXPORT_DIR")
    if override:
        return override
    cwd = os.getcwd()
    return cwd if os.access(cwd, os.W_OK) else tempfile.gettempdir()


def _write_standalone_html(widget_html: str, model: dict, title: str) -> dict:
    """Write a self-contained, *interactive* HTML copy of a widget to disk.

    The vendored single-file widget bundle is reused verbatim, with the parsed
    model injected as ``window.__MCP_MODEL__``. Opened in a browser (outside the
    host sandbox) the bundle renders the model directly — no MCP host needed —
    and its Export PNG/SVG buttons work there. Returns the file path; the host
    agent surfaces it (no megabyte payload echoed into the chat).

    Writes to the workspace (cwd) by default; see :func:`_export_dir`.
    """
    payload = json.dumps(model).replace("</", "<\\/")  # keep </script> out of the inline JSON
    inject = f"<script>window.__MCP_MODEL__ = {payload};</script>\n</head>"
    html = widget_html.replace("</head>", inject, 1)
    name = (re.sub(r"[^A-Za-z0-9._-]+", "-", title or "diagram").strip("-") or "diagram")
    path = os.path.join(_export_dir(), f"{name}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return {
        "structuredContent": {
            "path": path,
            "filename": f"{name}.html",
            "bytes": len(html),
            "note": "Self-contained interactive HTML written to disk (workspace "
                    "folder by default; set LINEAGE_MCP_EXPORT_DIR to change it). "
                    "Open it in a browser to view; use its Export PNG / Export "
                    "SVG buttons or Print there.",
        },
    }


# ---------------------------------------------------------------------------
# Widget resources
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def widget_html() -> str:
    """The HTML served at ``ui://lineage/viewer.html``."""
    return (resources.files("lineage_mcp")
            .joinpath("widgets/viewer.html")
            .read_text(encoding="utf-8"))


def join_widget_html() -> str:
    """The HTML served at ``ui://lineage/join-diagram.html``.

    Deliberately *not* cached: the widget is re-read from disk on every call so a
    fresh ``build:join`` shows up after re-running the tool, without restarting
    the (long-lived stdio) server.
    """
    return (resources.files("lineage_mcp")
            .joinpath("widgets/join-diagram.html")
            .read_text(encoding="utf-8"))


def query_plan_html() -> str:
    """The HTML served at ``ui://lineage/query-plan.html``.

    Like :func:`join_widget_html`, deliberately *not* cached so a fresh
    ``build:plan`` shows up on the next tool call without a server restart.
    """
    return (resources.files("lineage_mcp")
            .joinpath("widgets/query-plan.html")
            .read_text(encoding="utf-8"))


# The MCP Apps (SEP-1865) MIME type. A host uses this to recognise that the
# resource is an interactive App UI rather than plain HTML.
RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"

RESOURCES = [
    {
        "uri": WIDGET_URI,
        "name": "Lineage viewer widget",
        "description": "Interactive data-lineage graph. Rendered by the host "
                       "inside a sandboxed iframe.",
        "mimeType": RESOURCE_MIME_TYPE,
    },
    {
        "uri": QUERY_PLAN_URI,
        "name": "Query plan widget",
        "description": "Interactive EXPLAIN-style logical execution pipeline: one "
                       "operator box per SQL clause (scan, join, filter, group-by, "
                       "having, window, project, distinct, sort, limit) in "
                       "execution order, with CTEs/subqueries as lanes that feed "
                       "downstream operators. Rendered by the host inside a "
                       "sandboxed iframe.",
        "mimeType": RESOURCE_MIME_TYPE,
    },
    {
        "uri": JOIN_WIDGET_URI,
        "name": "Join diagram widget",
        "description": "Interactive SQL join diagram: table cards, join-key "
                       "connectors, and the source query. Rendered by the host "
                       "inside a sandboxed iframe.",
        "mimeType": RESOURCE_MIME_TYPE,
    },
]


def read_resource(uri: str) -> str:
    if uri == WIDGET_URI:
        return widget_html()
    if uri == JOIN_WIDGET_URI:
        return join_widget_html()
    if uri == QUERY_PLAN_URI:
        return query_plan_html()
    raise KeyError(f"unknown resource: {uri!r}")


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "view_lineage",
        "description": "Show the data lineage for a table or model as an "
                       "interactive widget.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Id of the table/model to focus on.",
                    "default": FOCUS_ID,
                }
            },
        },
        # SEP-1865 / MCP Apps: this is what marks the tool "UI-enabled". The
        # host reads `_meta.ui.resourceUri` to know which resource to render.
        # `ui/resourceUri` is the legacy flat key the SDK also populates, kept
        # here for compatibility with older hosts.
        "_meta": {
            "ui": {
                "resourceUri": WIDGET_URI,
                "preferredSize": {"width": 720, "height": 460},
            },
            "ui/resourceUri": WIDGET_URI,
        },
    },
    {
        "name": "expand_lineage_node",
        "description": "Reveal a node's direct neighbours in one direction. "
                       "Called by the widget when the user expands a node; "
                       "returns the new nodes and edges to merge in.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Id of the node being expanded.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["upstream", "downstream"],
                    "default": "upstream",
                },
                "focus": {
                    "type": "string",
                    "description": "Focus node the widget is centred on.",
                    "default": FOCUS_ID,
                },
                "visible_node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ids already shown, so they are not re-added.",
                },
            },
            "required": ["node"],
        },
    },
    {
        "name": "describe_node",
        "description": "Return rich detail for a single lineage node "
                       "(owner, grain, row count, columns, neighbours). Called "
                       "by the widget when the user clicks a node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Id of the node."},
            },
            "required": ["node"],
        },
    },
    {
        "name": "view_join_diagram",
        "description": "Visualise the joins in a SQL query as an interactive "
                       "diagram: one card per table (join keys, columns, "
                       "filters) wired by join-key connectors, alongside the "
                       "natural-language prompt and the source SQL. With no SQL "
                       "it shows a bundled example.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to diagram. Omit to render the "
                                   "bundled monthly-encounter example.",
                },
                "nl": {
                    "type": "string",
                    "description": "Optional natural-language prompt the SQL was "
                                   "generated from.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional title shown in the header.",
                },
            },
        },
        # SEP-1865 / MCP Apps: marks the tool UI-enabled and names its widget.
        "_meta": {
            "ui": {
                "resourceUri": JOIN_WIDGET_URI,
                "preferredSize": {"width": 900, "height": 620},
            },
            "ui/resourceUri": JOIN_WIDGET_URI,
        },
    },
    {
        "name": "parse_join_sql",
        "description": "Parse a SQL query into the join-diagram model without "
                       "(re)opening the widget. Called by the join-diagram "
                       "widget when the user pastes or edits SQL, so the diagram "
                       "re-renders via the same governed round-trip.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL query to parse."},
                "nl": {"type": "string", "description": "Optional NL prompt."},
                "title": {"type": "string", "description": "Optional title."},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "view_query_plan",
        "description": "Visualise an entire SQL statement as a logical execution "
                       "pipeline (EXPLAIN-style): one operator box per clause "
                       "(scan, join, filter, group-by, having, window, project, "
                       "distinct, sort, limit) in the order SQL logically runs, "
                       "with CTEs/subqueries as lanes that feed downstream "
                       "operators. With no SQL it shows a bundled example.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to diagram. Omit to render the "
                                   "bundled monthly-encounter example.",
                },
                "nl": {
                    "type": "string",
                    "description": "Optional natural-language prompt the SQL was "
                                   "generated from.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional title shown in the header.",
                },
            },
        },
        # SEP-1865 / MCP Apps: marks the tool UI-enabled and names its widget.
        "_meta": {
            "ui": {
                "resourceUri": QUERY_PLAN_URI,
                "preferredSize": {"width": 1000, "height": 680},
            },
            "ui/resourceUri": QUERY_PLAN_URI,
        },
    },
    {
        "name": "parse_query_plan",
        "description": "Parse a SQL query into the query-plan model without "
                       "(re)opening the widget. Called by the query-plan widget "
                       "when the user pastes or edits SQL, so the pipeline "
                       "re-renders via the same governed round-trip.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL query to parse."},
                "nl": {"type": "string", "description": "Optional NL prompt."},
                "title": {"type": "string", "description": "Optional title."},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "export_join_diagram",
        "description": "Export the join diagram as a self-contained, INTERACTIVE "
                       "HTML file written to disk. Open it in any browser (outside "
                       "the host sandbox) to view, drag, zoom, and use its Export "
                       "PNG/SVG/Print options. Returns the file path. With no SQL "
                       "it exports the bundled example.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL query to diagram."},
                "nl": {"type": "string", "description": "Optional NL prompt."},
                "title": {"type": "string", "description": "Optional title / filename."},
            },
        },
    },
    {
        "name": "export_query_plan",
        "description": "Export the query plan (logical execution pipeline) as a "
                       "self-contained, INTERACTIVE HTML file written to disk. Open "
                       "it in any browser (outside the host sandbox) to view and "
                       "use its Export PNG/SVG/Print options. Returns the file "
                       "path. With no SQL it exports the bundled example.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL query to diagram."},
                "nl": {"type": "string", "description": "Optional NL prompt."},
                "title": {"type": "string", "description": "Optional title / filename."},
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def call_tool(name: str, arguments: dict | None) -> dict:
    """Execute a tool and return a JSON-serialisable result payload.

    The returned dict is the tool's *structured content*. For ``view_lineage``
    it also carries the ``_meta.ui.resourceUri`` so a host knows to render the
    widget; the same graph payload is what the widget renders.
    """
    args = arguments or {}

    if name == "view_lineage":
        focus = args.get("node") or FOCUS_ID
        graph = _provider.view(focus)
        return {
            "structuredContent": graph,
            "_meta": {"ui": {"resourceUri": WIDGET_URI}},
        }

    if name == "expand_lineage_node":
        node = args.get("node")
        if not node:
            raise ValueError("expand_lineage_node requires 'node'")
        focus = args.get("focus") or FOCUS_ID
        direction = args.get("direction") or "upstream"
        visible = args.get("visible_node_ids") or []
        result = _provider.expand(focus, node, direction, visible)
        return {"structuredContent": result}

    if name == "describe_node":
        node = args.get("node")
        if not node:
            raise ValueError("describe_node requires 'node'")
        return {"structuredContent": _provider.describe(node)}

    if name == "view_join_diagram":
        model = _joins.join_diagram(
            sql=args.get("sql"),
            nl=args.get("nl"),
            title=args.get("title"),
        )
        return {
            "structuredContent": model,
            "_meta": {"ui": {"resourceUri": JOIN_WIDGET_URI}},
        }

    if name == "parse_join_sql":
        sql = args.get("sql")
        if not sql:
            raise ValueError("parse_join_sql requires 'sql'")
        model = _joins.join_diagram(
            sql=sql, nl=args.get("nl"), title=args.get("title"),
        )
        return {"structuredContent": model}

    if name == "view_query_plan":
        model = _plans.query_plan(
            sql=args.get("sql"),
            nl=args.get("nl"),
            title=args.get("title"),
        )
        return {
            "structuredContent": model,
            "_meta": {"ui": {"resourceUri": QUERY_PLAN_URI}},
        }

    if name == "parse_query_plan":
        sql = args.get("sql")
        if not sql:
            raise ValueError("parse_query_plan requires 'sql'")
        model = _plans.query_plan(
            sql=sql, nl=args.get("nl"), title=args.get("title"),
        )
        return {"structuredContent": model}

    if name == "export_join_diagram":
        model = _joins.join_diagram(
            sql=args.get("sql"), nl=args.get("nl"), title=args.get("title"),
        )
        return _write_standalone_html(
            join_widget_html(), model,
            args.get("title") or model.get("title") or "join-diagram",
        )

    if name == "export_query_plan":
        model = _plans.query_plan(
            sql=args.get("sql"), nl=args.get("nl"), title=args.get("title"),
        )
        return _write_standalone_html(
            query_plan_html(), model,
            args.get("title") or model.get("title") or "query-plan",
        )

    raise KeyError(f"unknown tool: {name!r}")
