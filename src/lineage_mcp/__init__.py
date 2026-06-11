"""lineage_mcp — MCP Apps reference implementation.

A UI-enabled MCP tool returns an interactive data-lineage widget. The widget is
rendered by the host inside a sandboxed iframe, and every interaction it performs
(e.g. "Expand upstream") is proxied back through the host as an MCP tool call.

This package is the *server* half of the diagram: tools, the widget resource, and
the in-memory lineage provider. See ``demo/`` for a local host that renders the
widget and visualizes the control plane.
"""

__version__ = "0.1.0"

WIDGET_URI = "ui://lineage/viewer.html"
