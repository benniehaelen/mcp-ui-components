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

from functools import lru_cache
from importlib import resources

from . import WIDGET_URI
from .data import FOCUS_ID
from .provider import LineageProvider

_provider = LineageProvider()


# ---------------------------------------------------------------------------
# Widget resource
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def widget_html() -> str:
    """The HTML served at ``ui://lineage/viewer.html``."""
    return (resources.files("lineage_mcp")
            .joinpath("widgets/viewer.html")
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
    }
]


def read_resource(uri: str) -> str:
    if uri == WIDGET_URI:
        return widget_html()
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

    raise KeyError(f"unknown tool: {name!r}")
