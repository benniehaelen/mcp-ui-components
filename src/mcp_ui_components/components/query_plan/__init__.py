"""SQL query-plan component.

A UI-enabled ``view_query_plan`` tool renders an entire SQL statement as an
EXPLAIN-style logical execution pipeline (one operator box per clause, CTEs and
subqueries as lanes). The widget proxies ``parse_query_plan`` (re-parse edited SQL)
back through the host; ``export_query_plan`` writes a self-contained interactive
HTML copy to disk.

Exposes the four names the registry merges: ``TOOLS``, ``RESOURCES``, ``HANDLERS``,
``RESOURCE_HTML``.
"""

from __future__ import annotations

from importlib import resources

from ... import QUERY_PLAN_URI, RESOURCE_MIME_TYPE
from ...shared.export import write_standalone_html
from .provider import QueryPlanProvider

_provider = QueryPlanProvider()


def _query_plan_html() -> str:
    """The HTML served at ``ui://mcp-ui-components/query-plan.html``.

    Like the join-diagram widget, deliberately *not* cached so a fresh
    ``build:plan`` shows up on the next tool call without a server restart.
    """
    return (resources.files("mcp_ui_components")
            .joinpath("widgets/query-plan.html")
            .read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
TOOLS = [
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
                "preferredSize": {"width": 1100, "height": 760},
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

RESOURCES = [
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
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _view_query_plan(args: dict) -> dict:
    model = _provider.query_plan(
        sql=args.get("sql"), nl=args.get("nl"), title=args.get("title"),
    )
    return {
        "structuredContent": model,
        "_meta": {"ui": {"resourceUri": QUERY_PLAN_URI}},
    }


def _parse_query_plan(args: dict) -> dict:
    sql = args.get("sql")
    if not sql:
        raise ValueError("parse_query_plan requires 'sql'")
    model = _provider.query_plan(
        sql=sql, nl=args.get("nl"), title=args.get("title"),
    )
    return {"structuredContent": model}


def _export_query_plan(args: dict) -> dict:
    model = _provider.query_plan(
        sql=args.get("sql"), nl=args.get("nl"), title=args.get("title"),
    )
    return write_standalone_html(_query_plan_html(), model, "query-plan")


HANDLERS = {
    "view_query_plan": _view_query_plan,
    "parse_query_plan": _parse_query_plan,
    "export_query_plan": _export_query_plan,
}

RESOURCE_HTML = {QUERY_PLAN_URI: _query_plan_html}
