"""Lineage viewer component.

A UI-enabled ``view_lineage`` tool renders an interactive data-lineage graph; the
widget proxies ``expand_lineage_node`` (reveal a node's neighbours) and
``describe_node`` (node detail) back through the host as governed tool calls.

Exposes the four names the registry merges: ``TOOLS``, ``RESOURCES``, ``HANDLERS``,
``RESOURCE_HTML``.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

from ... import RESOURCE_MIME_TYPE, WIDGET_URI
from .data import FOCUS_ID
from .provider import LineageProvider

_provider = LineageProvider()


@lru_cache(maxsize=1)
def _viewer_html() -> str:
    """The HTML served at ``ui://mcp-ui-components/viewer.html``.

    Cached: a viewer rebuild needs a server restart to show up (unlike the SQL
    widgets, which re-read from disk each call).
    """
    return (resources.files("mcp_ui_components")
            .joinpath("widgets/viewer.html")
            .read_text(encoding="utf-8"))


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
]

RESOURCES = [
    {
        "uri": WIDGET_URI,
        "name": "Lineage viewer widget",
        "description": "Interactive data-lineage graph. Rendered by the host "
                       "inside a sandboxed iframe.",
        "mimeType": RESOURCE_MIME_TYPE,
    },
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _view_lineage(args: dict) -> dict:
    focus = args.get("node") or FOCUS_ID
    graph = _provider.view(focus)
    return {
        "structuredContent": graph,
        "_meta": {"ui": {"resourceUri": WIDGET_URI}},
    }


def _expand_lineage_node(args: dict) -> dict:
    node = args.get("node")
    if not node:
        raise ValueError("expand_lineage_node requires 'node'")
    focus = args.get("focus") or FOCUS_ID
    direction = args.get("direction") or "upstream"
    visible = args.get("visible_node_ids") or []
    return {"structuredContent": _provider.expand(focus, node, direction, visible)}


def _describe_node(args: dict) -> dict:
    node = args.get("node")
    if not node:
        raise ValueError("describe_node requires 'node'")
    return {"structuredContent": _provider.describe(node)}


HANDLERS = {
    "view_lineage": _view_lineage,
    "expand_lineage_node": _expand_lineage_node,
    "describe_node": _describe_node,
}

RESOURCE_HTML = {WIDGET_URI: _viewer_html}
