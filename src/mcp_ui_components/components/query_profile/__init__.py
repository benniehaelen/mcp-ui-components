"""SQL query-profile component.

A UI-enabled ``view_query_profile`` tool renders a compact, parse-derived analytic
card for a SQL query: table/join/operator counts, the join-type and operator mix,
estimated scan, and a heuristic complexity score. The widget proxies
``parse_query_profile`` (re-parse edited SQL) back through the host.

Exposes the four names the registry merges: ``TOOLS``, ``RESOURCES``, ``HANDLERS``,
``RESOURCE_HTML``.
"""

from __future__ import annotations

from importlib import resources

from ... import QUERY_PROFILE_URI, RESOURCE_MIME_TYPE
from .provider import QueryProfileProvider

_provider = QueryProfileProvider()


def _query_profile_html() -> str:
    """The HTML served at ``ui://mcp-ui-components/query-profile.html``.

    Like the other SQL widgets, deliberately *not* cached so a fresh
    ``build:profile`` shows up on the next tool call without a server restart.
    """
    return (resources.files("mcp_ui_components")
            .joinpath("widgets/query-profile.html")
            .read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "view_query_profile",
        "description": "Show a compact analytic profile of a SQL query as an "
                       "interactive card: table / join / operator counts, the "
                       "join-type and operator mix, estimated scan, and a "
                       "heuristic complexity score. All parse-derived (no "
                       "execution). With no SQL it profiles a bundled example.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to profile. Omit to profile the "
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
                "resourceUri": QUERY_PROFILE_URI,
                "preferredSize": {"width": 560, "height": 460},
            },
            "ui/resourceUri": QUERY_PROFILE_URI,
        },
    },
    {
        "name": "parse_query_profile",
        "description": "Parse a SQL query into the query-profile model without "
                       "(re)opening the widget. Called by the query-profile widget "
                       "when the user pastes or edits SQL, so the card re-renders "
                       "via the same governed round-trip.",
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
]

RESOURCES = [
    {
        "uri": QUERY_PROFILE_URI,
        "name": "Query profile widget",
        "description": "Compact analytic card for a SQL query: table / join / "
                       "operator counts, the join-type and operator mix, estimated "
                       "scan, and a heuristic complexity score. Rendered by the "
                       "host inside a sandboxed iframe.",
        "mimeType": RESOURCE_MIME_TYPE,
    },
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _view_query_profile(args: dict) -> dict:
    model = _provider.query_profile(
        sql=args.get("sql"), nl=args.get("nl"), title=args.get("title"),
    )
    return {
        "structuredContent": model,
        "_meta": {"ui": {"resourceUri": QUERY_PROFILE_URI}},
    }


def _parse_query_profile(args: dict) -> dict:
    sql = args.get("sql")
    if not sql:
        raise ValueError("parse_query_profile requires 'sql'")
    model = _provider.query_profile(
        sql=sql, nl=args.get("nl"), title=args.get("title"),
    )
    return {"structuredContent": model}


HANDLERS = {
    "view_query_profile": _view_query_profile,
    "parse_query_profile": _parse_query_profile,
}

RESOURCE_HTML = {QUERY_PROFILE_URI: _query_profile_html}
