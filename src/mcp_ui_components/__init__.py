"""mcp_ui_components — MCP Apps reference implementation (SEP-1865).

One MCP server serves three interactive UI controls — a **lineage viewer**, a **SQL
join diagram**, and a **query plan**. Each is a UI-enabled tool whose result carries
``_meta.ui.resourceUri``; the host renders the named ``ui://`` resource inside a
sandboxed iframe, and every interaction the widget performs is proxied back through
the host as a governed MCP tool call.

This package is the *server* half: each control lives under :mod:`.components`,
shared SQL/export/example code under :mod:`.shared`, and :mod:`.registry` aggregates
them into the server's tool/resource surface. See ``demo/`` for a local host that
renders the widgets and visualizes the control plane.
"""

__version__ = "0.1.0"

# Widget resource URIs (server-side identifiers; not embedded in the widget bundles).
WIDGET_URI = "ui://mcp-ui-components/viewer.html"
JOIN_WIDGET_URI = "ui://mcp-ui-components/join-diagram.html"
QUERY_PLAN_URI = "ui://mcp-ui-components/query-plan.html"
QUERY_PROFILE_URI = "ui://mcp-ui-components/query-profile.html"

# The MCP Apps (SEP-1865) MIME type. A host uses this to recognise that a resource
# is an interactive App UI rather than plain HTML; hosts reject a UI resource served
# as anything else.
RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"
