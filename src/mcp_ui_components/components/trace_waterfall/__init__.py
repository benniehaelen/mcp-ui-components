"""Trace + cost waterfall component.

A UI-enabled ``view_trace_waterfall`` tool renders the OpenTelemetry span
waterfall for a single NL-to-SQL request, annotated with latency, token cost, and
BigQuery bytes. The widget proxies four governed tool calls back through the host:
``describe_span`` (click a span), ``get_span_cost_breakdown`` (click a cost chip),
``expand_span_children`` (the + affordance on a lazy branch), and
``list_recent_traces`` (the trace picker). Picking a different trace re-invokes
``view_trace_waterfall`` to recenter.

Exposes the four names the registry merges: ``TOOLS``, ``RESOURCES``, ``HANDLERS``,
``RESOURCE_HTML``.
"""

from __future__ import annotations

from importlib import resources

from ... import RESOURCE_MIME_TYPE, TRACE_WATERFALL_URI
from .provider import TraceProvider

_provider = TraceProvider()


def _waterfall_html() -> str:
    """The HTML served at ``ui://mcp-ui-components/trace-waterfall.html``.

    Like the SQL widgets, deliberately *not* cached so a fresh ``build:trace``
    shows up on the next tool call without a server restart.
    """
    return (resources.files("mcp_ui_components")
            .joinpath("widgets/trace-waterfall.html")
            .read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "view_trace_waterfall",
        "description": "Render the OpenTelemetry span waterfall for a single "
                       "NL-to-SQL request, annotated with latency, token cost, "
                       "and BigQuery bytes scanned and billed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {
                    "type": "string",
                    "description": "Trace to render. If omitted, the most recent "
                                   "trace is used.",
                },
            },
        },
        # SEP-1865 / MCP Apps: marks the tool UI-enabled and names its widget.
        # `ui/resourceUri` is the legacy flat key kept for older hosts.
        "_meta": {
            "ui": {
                "resourceUri": TRACE_WATERFALL_URI,
                "preferredSize": {"width": 960, "height": 600},
            },
            "ui/resourceUri": TRACE_WATERFALL_URI,
        },
    },
    {
        "name": "describe_span",
        "description": "Return full OTel detail for a single span: attributes, "
                       "events, status, and kind-specific extras, plus any "
                       "cross-widget links. Called by the widget when the user "
                       "clicks a span bar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "span_id": {"type": "string", "description": "Id of the span."},
            },
            "required": ["span_id"],
        },
    },
    {
        "name": "get_span_cost_breakdown",
        "description": "Decompose a span's cost into its Claude (token) and "
                       "BigQuery (bytes) parts, with the rate cards used. Called "
                       "by the widget when the user clicks a span's cost chip.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "span_id": {"type": "string", "description": "Id of the span."},
                "scope": {
                    "type": "string",
                    "enum": ["span", "subtree"],
                    "default": "span",
                    "description": "Cost for the span alone, or for the span plus "
                                   "all of its descendants.",
                },
            },
            "required": ["span_id"],
        },
    },
    {
        "name": "expand_span_children",
        "description": "Reveal a span's not-yet-shown direct children. Called by "
                       "the widget when the user expands a span flagged with lazy "
                       "children; returns the new spans to merge in.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "span_id": {
                    "type": "string",
                    "description": "Id of the span being expanded.",
                },
                "visible_span_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Span ids already shown, so they are not "
                                   "re-added.",
                },
            },
            "required": ["span_id", "visible_span_ids"],
        },
    },
    {
        "name": "list_recent_traces",
        "description": "List recent NL-to-SQL traces for the picker, newest "
                       "first. Called by the widget when the user opens the "
                       "trace picker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "maximum": 50,
                    "description": "Maximum number of traces to return.",
                },
                "status": {
                    "type": "string",
                    "enum": ["any", "ok", "error", "guardrail_blocked"],
                    "default": "any",
                    "description": "Filter by trace status.",
                },
            },
        },
    },
]

RESOURCES = [
    {
        "uri": TRACE_WATERFALL_URI,
        "name": "Trace waterfall widget",
        "description": "Interactive OpenTelemetry span waterfall for one "
                       "NL-to-SQL request, annotated with latency, token cost, "
                       "and BigQuery bytes. Rendered by the host inside a "
                       "sandboxed iframe.",
        "mimeType": RESOURCE_MIME_TYPE,
    },
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _view_trace_waterfall(args: dict) -> dict:
    trace = _provider.get_trace(args.get("trace_id"))
    return {
        "structuredContent": trace,
        "_meta": {"ui": {"resourceUri": TRACE_WATERFALL_URI}},
    }


def _describe_span(args: dict) -> dict:
    span_id = args.get("span_id")
    if not span_id:
        raise ValueError("describe_span requires 'span_id'")
    return {"structuredContent": _provider.describe_span(span_id)}


def _get_span_cost_breakdown(args: dict) -> dict:
    span_id = args.get("span_id")
    if not span_id:
        raise ValueError("get_span_cost_breakdown requires 'span_id'")
    scope = args.get("scope") or "span"
    return {"structuredContent": _provider.cost_breakdown(span_id, scope)}


def _expand_span_children(args: dict) -> dict:
    span_id = args.get("span_id")
    if not span_id:
        raise ValueError("expand_span_children requires 'span_id'")
    visible = args.get("visible_span_ids") or []
    return {"structuredContent": _provider.expand(span_id, visible)}


def _list_recent_traces(args: dict) -> dict:
    traces = _provider.list_recent(
        limit=args.get("limit", 10), status=args.get("status", "any"))
    return {"structuredContent": {"traces": traces}}


HANDLERS = {
    "view_trace_waterfall": _view_trace_waterfall,
    "describe_span": _describe_span,
    "get_span_cost_breakdown": _get_span_cost_breakdown,
    "expand_span_children": _expand_span_children,
    "list_recent_traces": _list_recent_traces,
}

RESOURCE_HTML = {TRACE_WATERFALL_URI: _waterfall_html}
