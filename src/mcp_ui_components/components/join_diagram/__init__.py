"""SQL join-diagram component.

A UI-enabled ``view_join_diagram`` tool renders the joins in a SQL query as
draggable table cards wired by join-key connectors. The widget proxies
``parse_join_sql`` (re-parse edited SQL) back through the host; ``export_join_diagram``
writes a self-contained interactive HTML copy to disk.

Exposes the four names the registry merges: ``TOOLS``, ``RESOURCES``, ``HANDLERS``,
``RESOURCE_HTML``.
"""

from __future__ import annotations

from importlib import resources

from ... import JOIN_WIDGET_URI, RESOURCE_MIME_TYPE
from ...shared.export import write_standalone_html
from .provider import JoinDiagramProvider

_provider = JoinDiagramProvider()


def _join_html() -> str:
    """The HTML served at ``ui://mcp-ui-components/join-diagram.html``.

    Deliberately *not* cached: the widget is re-read from disk on every call so a
    fresh ``build:join`` shows up after re-running the tool, without restarting
    the (long-lived stdio) server.
    """
    return (resources.files("mcp_ui_components")
            .joinpath("widgets/join-diagram.html")
            .read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
TOOLS = [
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
]

RESOURCES = [
    {
        "uri": JOIN_WIDGET_URI,
        "name": "Join diagram widget",
        "description": "Interactive SQL join diagram: table cards, join-key "
                       "connectors, and the source query. Rendered by the host "
                       "inside a sandboxed iframe.",
        "mimeType": RESOURCE_MIME_TYPE,
    },
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _view_join_diagram(args: dict) -> dict:
    model = _provider.join_diagram(
        sql=args.get("sql"), nl=args.get("nl"), title=args.get("title"),
    )
    return {
        "structuredContent": model,
        "_meta": {"ui": {"resourceUri": JOIN_WIDGET_URI}},
    }


def _parse_join_sql(args: dict) -> dict:
    sql = args.get("sql")
    if not sql:
        raise ValueError("parse_join_sql requires 'sql'")
    model = _provider.join_diagram(
        sql=sql, nl=args.get("nl"), title=args.get("title"),
    )
    return {"structuredContent": model}


def _export_join_diagram(args: dict) -> dict:
    model = _provider.join_diagram(
        sql=args.get("sql"), nl=args.get("nl"), title=args.get("title"),
    )
    return write_standalone_html(
        _join_html(), model,
        args.get("title") or model.get("title") or "join-diagram",
    )


HANDLERS = {
    "view_join_diagram": _view_join_diagram,
    "parse_join_sql": _parse_join_sql,
    "export_join_diagram": _export_join_diagram,
}

RESOURCE_HTML = {JOIN_WIDGET_URI: _join_html}
